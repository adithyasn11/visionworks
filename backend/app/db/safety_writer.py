# backend/app/db/safety_writer.py
"""
Writes safety events and decisions to the local database without ever slowing
or stopping the video.

THE RULE THAT SHAPES EVERYTHING HERE

Telemetry and safety decisions are not the same kind of data. Losing a minute of
telemetry is a gap in a chart. Losing a decision is a worker flagged for a
violation the supervisor never sees, or a violation nobody was ever told about.
So this writer follows the telemetry writers' habits (buffer, write off the
event loop, never raise into the frame loop) and adds the guarantee they do
not give: a row that could not be written because the database was busy is KEPT
and tried again, not dropped.

WHAT CAN GO WRONG, AND WHAT HAPPENS

  database locked / busy / briefly unavailable
      Transient. The rows stay in the buffer and the next flush tries again
      (no more often than `retry_interval_s`, so a stuck database is not
      hammered every frame).
  a row the database REFUSES (a CHECK or NOT NULL constraint)
      Permanent: retrying can never help and one bad row must not hold up the
      good ones behind it. The batch is retried row by row; the bad row is
      dropped, counted in `rejected`, and logged with its id and the reason.
      (`add_*` already validates, so this is a second line of defence.)
  the buffer is full because the database has been unavailable for a long time
      The OLDEST row of that kind is dropped, counted, and logged as an error.
      Events and decisions have separate limits, and decisions get the larger
      protection: events are facts that can be re-derived, decisions cannot.
  the tables do not exist
      Created on first use. The code that adds rows cannot forget to.

IDEMPOTENT BY CONSTRUCTION

Every event and decision has a UUID made when it is created. A row is inserted
with ON CONFLICT DO NOTHING on that id, so writing the same row twice (a retry
after a timeout that actually succeeded) leaves one row. This is deliberately
`ON CONFLICT ... DO NOTHING` and not `INSERT OR IGNORE`: the latter also hides
CHECK-constraint violations, which would turn a bug into silently missing data.

THREADS

`add_*` is called from the event loop; `flush_sync` runs in a worker thread
(asyncio.to_thread). The buffers are guarded by a lock, a flush takes a
snapshot and puts back whatever it could not write, and only one flush runs at
a time.
"""

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app.db.database import engine as _default_engine
from app.db.safety_models import (
    DECISION_STATUSES, EVENT_TYPES, RULE_TYPES, SEVERITIES, ensure_safety_tables,
)

logger = logging.getLogger(__name__)

MAX_PENDING_EVENTS = 20_000
MAX_PENDING_DECISIONS = 5_000
RETRY_INTERVAL_S = 1.0
WARN_EVERY_S = 30.0

_INSERT_EVENT = text("""
    INSERT INTO safety_events
        (event_uid, org_id, camera_id, zone_id, worker_key, session_key,
         event_type, video_time_s, observed_at, details, created_at)
    VALUES
        (:event_uid, :org_id, :camera_id, :zone_id, :worker_key, :session_key,
         :event_type, :video_time_s, :observed_at, :details, :created_at)
    ON CONFLICT (event_uid) DO NOTHING
""")

_INSERT_DECISION = text("""
    INSERT INTO safety_decisions
        (id, org_id, camera_id, zone_id, rule_id, rule_type, severity, worker_key,
         session_key, status, reason, confidence, evidence, thresholds,
         snapshot_ref, video_time_s, observed_at, created_at)
    VALUES
        (:id, :org_id, :camera_id, :zone_id, :rule_id, :rule_type, :severity, :worker_key,
         :session_key, :status, :reason, :confidence, :evidence, :thresholds,
         :snapshot_ref, :video_time_s, :observed_at, :created_at)
    ON CONFLICT (id) DO NOTHING
""")


# ── small helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _clean(value):
    """
    Make a value JSON-safe: numpy scalars and arrays, datetimes, sets, and the
    floats JSON cannot hold (NaN, ±inf become null, because Postgres JSONB
    rejects the bare word NaN and one such value would poison a whole sync).
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_clean(v) for v in value), key=str)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "tolist"):                      # numpy array or scalar
        return _clean(value.tolist())
    if hasattr(value, "item"):
        return _clean(value.item())
    return str(value)


def _json(value) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_clean(value), separators=(",", ":"), allow_nan=False)


def _uuid(value, what: str) -> str:
    """A caller-supplied id must be a UUID: Postgres types the column that way."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{what} must be a UUID, got {value!r}") from None


def _time(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{what} must be a finite number of seconds >= 0, got {value!r}")
    return float(value)


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    return value


def _iso(value, what: str) -> str:
    _text(value, what)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00") if value[-1] in "Zz" else value)
    except ValueError:
        raise ValueError(f"{what} must be an ISO-8601 timestamp, got {value!r}") from None
    return value


# ── the writer ───────────────────────────────────────────────────────────────

class SafetyWriter:
    """
    One per processing session. See the module docstring for the contract.

        w = SafetyWriter(camera_id="gate_1", org_id=org, session_id=sid)
        w.add_event("ENTERED_ZONE", "trk-7", 12.4, observed_at, zone_id="zone_a")
        w.add_decision("PPE_ENTRY", "WARNING", "trk-7", "POTENTIAL_VIOLATION",
                       "no helmet in 27/29 clear frames", 0.93, {...}, 12.4, observed_at)
        await persist_safety(w)           # from the frame loop
        await persist_safety(w, force=True)   # when the session ends
    """

    def __init__(
        self,
        camera_id: str,
        org_id: Optional[str] = None,
        session_id: Optional[str] = None,
        *,
        engine=None,
        max_pending_events: int = MAX_PENDING_EVENTS,
        max_pending_decisions: int = MAX_PENDING_DECISIONS,
        retry_interval_s: float = RETRY_INTERVAL_S,
        monotonic=time.monotonic,
    ):
        self.camera_id = _text(camera_id, "camera_id")
        self.org_id = org_id
        self.session_id = session_id
        self._engine = engine or _default_engine
        self._max_events = int(max_pending_events)
        self._max_decisions = int(max_pending_decisions)
        self._retry_interval = float(retry_interval_s)
        self._mono = monotonic

        self._events: deque = deque()
        self._decisions: deque = deque()
        self._lock = threading.Lock()          # guards the two deques
        self._flush_lock = threading.Lock()    # one flush at a time
        self._tables_ready = False
        self._backoff_until = 0.0
        self._last_warn = {}

        self.events_written = 0
        self.decisions_written = 0
        self.dropped_events = 0                # buffer full while the database was away
        self.dropped_decisions = 0
        self.rejected = 0                      # refused by a database constraint

    # ── adding ───────────────────────────────────────────────────────────────

    def add_event(
        self,
        event_type: str,
        worker_key: str,
        video_time_s: float,
        observed_at: str,
        *,
        zone_id: Optional[str] = None,
        details: Optional[dict] = None,
        event_uid: Optional[str] = None,
    ) -> str:
        """Queue one event. Returns its uuid. Raises ValueError on a malformed event."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {EVENT_TYPES}, got {event_type!r}")
        row = {
            "event_uid": _uuid(event_uid, "event_uid") if event_uid else str(uuid.uuid4()),
            "org_id": self.org_id,
            "camera_id": self.camera_id,
            "zone_id": zone_id,
            "worker_key": _text(worker_key, "worker_key"),
            "session_key": self.session_id,
            "event_type": event_type,
            "video_time_s": _time(video_time_s, "video_time_s"),
            "observed_at": _iso(observed_at, "observed_at"),
            "details": _json(details),
            "created_at": _now_iso(),
        }
        with self._lock:
            self._push(self._events, row, self._max_events, "events")
        return row["event_uid"]

    def add_decision(
        self,
        rule_type: str,
        severity: str,
        worker_key: str,
        status: str,
        reason: str,
        confidence: float,
        evidence: dict,
        video_time_s: float,
        observed_at: str,
        *,
        zone_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        thresholds: Optional[dict] = None,
        snapshot_ref: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> str:
        """Queue one decision. Returns its uuid. Raises ValueError on a malformed decision."""
        if rule_type not in RULE_TYPES:
            raise ValueError(f"rule_type must be one of {RULE_TYPES}, got {rule_type!r}")
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
        if status not in DECISION_STATUSES:
            raise ValueError(f"status must be one of {DECISION_STATUSES}, got {status!r}")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be a number from 0 to 1, got {confidence!r}")
        if status == "UNVERIFIABLE" and confidence != 0:
            # No claim, so no confidence — never quietly coerced, because a
            # caller that supplies one has misunderstood the verdict.
            raise ValueError("an UNVERIFIABLE decision makes no claim and must have confidence 0")
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be a dict (it is stored as JSON)")
        row = {
            "id": _uuid(decision_id, "decision_id") if decision_id else str(uuid.uuid4()),
            "org_id": self.org_id,
            "camera_id": self.camera_id,
            "zone_id": zone_id,
            "rule_id": _uuid(rule_id, "rule_id") if rule_id else None,
            "rule_type": rule_type,
            "severity": severity,
            "worker_key": _text(worker_key, "worker_key"),
            "session_key": self.session_id,
            "status": status,
            "reason": _text(reason, "reason"),
            "confidence": float(confidence),
            "evidence": _json(evidence),
            "thresholds": _json(thresholds),
            "snapshot_ref": snapshot_ref,
            "video_time_s": _time(video_time_s, "video_time_s"),
            "observed_at": _iso(observed_at, "observed_at"),
            "created_at": _now_iso(),
        }
        with self._lock:
            self._push(self._decisions, row, self._max_decisions, "decisions")
        return row["id"]

    def _push(self, buffer: deque, row: dict, limit: int, kind: str) -> None:
        """Append, dropping the OLDEST row of this kind if the buffer is full. Caller holds the lock."""
        buffer.append(row)
        while len(buffer) > limit:
            buffer.popleft()
            if kind == "events":
                self.dropped_events += 1
            else:
                self.dropped_decisions += 1
            self._warn(f"overflow-{kind}",
                       f"safety {kind} buffer full ({limit}); the database has been "
                       f"unavailable too long. Dropping the oldest {kind[:-1]}.", error=True)

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def pending_events(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def pending_decisions(self) -> int:
        with self._lock:
            return len(self._decisions)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._events or self._decisions)

    def in_backoff(self) -> bool:
        """True while a recent failure says "do not try again just yet"."""
        return self._mono() < self._backoff_until

    # ── flushing ─────────────────────────────────────────────────────────────

    def flush_sync(self) -> dict:
        """
        Write everything buffered. Runs in a worker thread. NEVER raises.

        Returns {"events": n, "decisions": m}, the rows newly stored by this
        call. Rows that could not be stored because the database was busy are
        put back and will be tried again; rows the database refuses are dropped
        and counted in `rejected`.
        """
        written = {"events": 0, "decisions": 0}
        if not self._flush_lock.acquire(blocking=False):
            return written                    # another flush is already doing this
        try:
            with self._lock:
                events, self._events = list(self._events), deque()
                decisions, self._decisions = list(self._decisions), deque()
            if not events and not decisions:
                return written
            try:
                self._write(events, decisions, written)
            except Exception as e:             # belt and braces: nothing escapes
                self._requeue(events, decisions)
                self._fail(f"unexpected error ({type(e).__name__}: {e})")
        finally:
            self._flush_lock.release()
        return written

    def _write(self, events: list, decisions: list, written: dict) -> None:
        if not self._tables_ready:
            ensure_safety_tables(self._engine)
            self._tables_ready = True

        try:
            n_events, n_decisions = self._write_batch(events, decisions)
            written["events"], written["decisions"] = n_events, n_decisions
            self.events_written += n_events
            self.decisions_written += n_decisions
            return
        except IntegrityError:
            # One refused row must not hold up the good ones: isolate it.
            self._write_individually(events, decisions, written)
            return
        except OperationalError as e:
            self._requeue(events, decisions)
            self._fail(f"database busy or unavailable ({e.orig})")
            return
        except DBAPIError as e:
            # Unclassified database error: assume it may pass. Rows are safe in
            # the buffer, which is bounded.
            self._requeue(events, decisions)
            self._fail(f"database error ({type(e.orig).__name__}: {e.orig})")
            return

    def _write_batch(self, events: list, decisions: list):
        """All rows in ONE transaction. Returns (events_stored, decisions_stored)."""
        with self._engine.begin() as conn:
            before = conn.exec_driver_sql("SELECT total_changes()").scalar()
            if events:
                conn.execute(_INSERT_EVENT, events)
            mid = conn.exec_driver_sql("SELECT total_changes()").scalar()
            if decisions:
                conn.execute(_INSERT_DECISION, decisions)
            after = conn.exec_driver_sql("SELECT total_changes()").scalar()
        return mid - before, after - mid

    def _write_individually(self, events: list, decisions: list, written: dict) -> None:
        """Fallback after a constraint failure: one row, one transaction."""
        retry_events, retry_decisions = [], []
        for sql, rows, key, bucket in (
            (_INSERT_EVENT, events, "event_uid", retry_events),
            (_INSERT_DECISION, decisions, "id", retry_decisions),
        ):
            for row in rows:
                try:
                    with self._engine.begin() as conn:
                        before = conn.exec_driver_sql("SELECT total_changes()").scalar()
                        conn.execute(sql, row)
                        stored = conn.exec_driver_sql("SELECT total_changes()").scalar() - before
                    if sql is _INSERT_EVENT:
                        written["events"] += stored
                        self.events_written += stored
                    else:
                        written["decisions"] += stored
                        self.decisions_written += stored
                except IntegrityError as e:
                    self.rejected += 1
                    logger.error(f"safety row {row.get(key)} rejected by the database and dropped: {e.orig}")
                except DBAPIError:
                    bucket.append(row)          # transient: keep it
        if retry_events or retry_decisions:
            self._requeue(retry_events, retry_decisions)
            self._fail("database busy while isolating a rejected row")

    def _requeue(self, events: list, decisions: list) -> None:
        """Put unwritten rows BACK at the front, ahead of anything added meanwhile."""
        with self._lock:
            self._events.extendleft(reversed(events))
            self._decisions.extendleft(reversed(decisions))
            for buffer, limit, kind in ((self._events, self._max_events, "events"),
                                        (self._decisions, self._max_decisions, "decisions")):
                while len(buffer) > limit:
                    buffer.popleft()
                    if kind == "events":
                        self.dropped_events += 1
                    else:
                        self.dropped_decisions += 1
                    self._warn(f"overflow-{kind}",
                               f"safety {kind} buffer full ({limit}); dropping the oldest "
                               f"{kind[:-1]}.", error=True)

    def _fail(self, why: str) -> None:
        self._backoff_until = self._mono() + self._retry_interval
        self._warn("write", f"safety write deferred: {why}. "
                            f"{self.pending_events} event(s) and {self.pending_decisions} "
                            f"decision(s) are kept and will be retried.")

    def _warn(self, key: str, message: str, error: bool = False) -> None:
        """Log at most once every WARN_EVERY_S per kind, so a dead database cannot flood the log."""
        now = self._mono()
        if now - self._last_warn.get(key, -WARN_EVERY_S) >= WARN_EVERY_S:
            self._last_warn[key] = now
            (logger.error if error else logger.warning)(message)


async def persist_safety(writer: Optional[SafetyWriter], *, force: bool = False) -> None:
    """
    Async entry point for the frame loop.

    Cheap when there is nothing to do (the common case: most frames produce no
    event), and it never blocks the event loop: the write runs in a thread. After
    a failed write it waits `retry_interval_s` before touching the database
    again, unless `force` (use it when the session ends: one last honest try).
    """
    if writer is None or not writer.has_pending():
        return
    if not force and writer.in_backoff():
        return
    await asyncio.to_thread(writer.flush_sync)
