# backend/app/db/identity_writer.py
"""
Persistence layer for per-identity observations.

WHY THIS EXISTS

This is the per-person analogue of `activity_writer.py`. That module writes
`activity_logs`, which is deliberately anonymous — it carries a ByteTrack id
that means nothing across sessions and is discarded entirely by
`minute_aggregator.py`. This module writes `identity_events`, which carries a
STITCHED identity and, once Steps 5-14 land, an `employee_id`.

Step 4 of IDENTITY_TRACKING_PLAN.md. It writes the rows with
`employee_id = NULL` and `method = "unknown"`, because nothing identifies anyone
yet — that is the correct output for this step, not a placeholder. The point of
building the writer before the identifier is that every write path is proven
before anything clever depends on it.

THE SAMPLING PATTERN IS COPIED FROM activity_writer.py, DELIBERATELY

Same 5-second interval, same "always write a posture change", same
`asyncio.to_thread` offloading, same swallow-and-log failure mode. Two reasons
that is the right call rather than lazy:

1. The two writers are driven from the SAME frame loop and see the SAME
   entities. If their sampling rules diverged, `identity_events` and
   `activity_logs` would disagree about when a person was observed, and Step 17
   evaluates identity accuracy by comparing exactly those two records.

2. The volume problem is identical. At ~60 FPS, one row per person per frame is
   thousands of rows a minute. `identity_events` is worse than `activity_logs`
   in one respect: it names people, so it carries a 7-day retention (see
   `purge_identity_events()` in migration 020). Sampling is what keeps that
   window affordable.

WHAT IS DIFFERENT FROM activity_writer.py

- `identity_id` is namespaced per session. ByteTrack restarts its ids at 0 for
  every video (plan §8.5), so a bare track id is ambiguous the moment a second
  session runs. `SESSION-<8 hex>::<track_id>` keeps "track 3 today" distinct
  from "track 3 tomorrow" without needing coordination between sessions.

- `confidence` and `method` travel with every row. The plan's central rule is
  that the system must say UNKNOWN rather than guess, and a stored attribution
  without its confidence is a claim that cannot be checked later. Migration 020
  enforces the honesty in the database: `(employee_id IS NULL) = (method =
  'unknown')` is a CHECK constraint, so the two columns cannot disagree.

- The local table is created here rather than by `Base.metadata.create_all()`.
  It mirrors the Postgres table from migration 020 column-for-column, the same
  way `minute_aggregator.ensure_bucket_table()` mirrors `zone_minute_stats`.
"""

import asyncio
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.db.database import engine, SessionLocal

logger = logging.getLogger(__name__)

# Identical to activity_writer.MIN_WRITE_INTERVAL_SECONDS, and deliberately so:
# the two writers observe the same entities from the same loop, and a different
# cadence would make their records disagree about when someone was seen.
MIN_WRITE_INTERVAL_SECONDS = 5.0

# The attribution methods migration 020 will accept. Anything outside this set
# is rejected by the `identity_events_method_known` CHECK constraint, so it is
# validated here too — a bad value should be a caught bug, not a dropped row at
# 3am when the Postgres sync runs.
VALID_METHODS = frozenset({"face", "fusion", "seat", "handoff", "unknown"})

# What Step 4 writes. Nothing identifies anyone yet, so every row abstains.
UNKNOWN_METHOD = "unknown"


def ensure_identity_table() -> None:
    """
    Creates the local `identity_events` mirror.

    Column-for-column the same as the Postgres table in migration 020, in
    snake_case rather than camelCase — the same split `zone_minute_stats`
    already has, where SQLite holds `bucket_start` and Postgres holds
    `bucketStart`. The sync layer maps the two, as
    `minute_aggregator.sync_to_postgres_sync()` does today.

    `org_id`/`camera_id`/`zone_id` are TEXT here because SQLite telemetry
    carries text ids ('live_webcam', 'workstation_01'); a Postgres writer
    resolves them to UUIDs by name, exactly as
    `minute_aggregator._resolve_ids()` already does.

    `synced_at` and `created_at` exist only locally: they track what has been
    pushed upstream, which is not something the Postgres row needs to know.

    The CHECK constraints from 020 are repeated here rather than left to
    Postgres. SQLite is where the pipeline actually writes, so an unenforced
    invariant here means bad rows are only caught after they have already been
    recorded — and `identity_events_unknown_consistent` in particular is what
    keeps the UNKNOWN rule honest.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS identity_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id        TEXT,
                employee_id   TEXT,
                camera_id     TEXT    NOT NULL,
                zone_id       TEXT,
                track_id      INTEGER NOT NULL,
                identity_id   TEXT    NOT NULL,
                posture       TEXT    NOT NULL,
                confidence    REAL    NOT NULL,
                method        TEXT    NOT NULL,
                observed_at   TEXT    NOT NULL,
                synced_at     TEXT,
                created_at    TEXT    NOT NULL,
                CHECK (confidence >= 0 AND confidence <= 1),
                CHECK (method IN ('face', 'fusion', 'seat', 'handoff', 'unknown')),
                -- UNKNOWN must be honest in BOTH columns: an event with no
                -- employee cannot claim a method that names one, and a named
                -- attribution cannot claim to be unknown.
                CHECK ((employee_id IS NULL) = (method = 'unknown'))
            )
        """))
        # The three read paths, matching the Postgres indexes in 020: "what
        # happened in this org recently", "what did this person do", and
        # "reassemble this stitched track".
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ie_org_observed "
            "ON identity_events (org_id, observed_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ie_employee_observed "
            "ON identity_events (employee_id, observed_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ie_identity "
            "ON identity_events (identity_id)"
        ))
        # The future Postgres sync scans for unsynced rows; without this it is
        # a full table scan on every pass. Same reasoning as ix_zms_unsynced.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ie_unsynced "
            "ON identity_events (synced_at)"
        ))


class IdentityEventWriter:
    """
    Sampled writer for one processing session.

    One instance per WebSocket session, so both the "when did I last write this
    track" state and the session's identity namespace are scoped to that
    session and cannot leak between concurrent videos. Not thread-safe by
    design: it is only ever driven from a single session's frame loop.
    """

    def __init__(
        self,
        camera_id: str,
        org_id: Optional[str] = None,
        session_id: Optional[str] = None,
        min_interval_seconds: float = MIN_WRITE_INTERVAL_SECONDS,
    ):
        self.camera_id = camera_id
        # See ActivityLogWriter.org_id: None means the pipeline is running
        # unauthenticated, and those rows are invisible to every org-scoped
        # query — the correct outcome for telemetry nobody can be shown.
        self.org_id = org_id
        # The identity namespace for this session. ByteTrack restarts track ids
        # at 0 for every video, so without this, "track 3" from an upload an
        # hour ago and "track 3" from the live camera now would collide in the
        # identity_id column and Step 6's stitching would silently join two
        # different people.
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.min_interval = min_interval_seconds
        # track_id -> {"last_write": float, "last_posture": str}
        self._track_state: dict = {}
        self.rows_written = 0

    def identity_for(self, track_id: int) -> str:
        """
        The session-namespaced identity for a raw ByteTrack id.

        Step 6 replaces this with real stitching, where one identity survives
        several track ids across an occlusion. Until then the mapping is 1:1,
        which is honest: nothing has stitched anything yet, and pretending
        otherwise would make the Step 6 stitch-rate metric meaningless because
        its baseline would already claim to be perfect.
        """
        return f"{self.session_id}::{track_id}"

    def _should_write(self, track_id: int, posture: str, now: float) -> bool:
        """
        Sampling rule: write if this track is new, if its posture just changed,
        or if min_interval has elapsed since its last write.

        Identical to ActivityLogWriter._should_write — see the module docstring
        for why the two must not diverge.
        """
        state = self._track_state.get(track_id)
        if state is None:
            return True
        if state["last_posture"] != posture:
            return True
        return (now - state["last_write"]) >= self.min_interval

    def collect(self, tracked_entities: list) -> list:
        """
        Filters one frame's tracked entities down to the rows that are due, and
        records that they were written.

        Returns plain dicts (not ORM objects) so the caller can hand them to a
        worker thread without carrying a Session across threads.

        Every row is written with `employee_id = NULL`, `method = "unknown"`
        and `confidence = 0.0`. That is Step 4's correct output, not a stub:
        no identification exists yet, and the plan's central rule is that the
        system abstains rather than guesses.
        """
        now = time.time()
        # One timestamp for the whole frame, so entities observed in the same
        # frame share an observed_at and Step 17 can group by it.
        observed_at = datetime.now(timezone.utc)
        due = []

        for entity in tracked_entities:
            track_id = entity.get("track_id")
            posture = entity.get("posture")
            if track_id is None or posture is None:
                continue

            if not self._should_write(track_id, posture, now):
                continue

            employee_id = entity.get("employee_id")
            method = entity.get("identity_method") or UNKNOWN_METHOD
            confidence = float(entity.get("identity_confidence") or 0.0)

            # Keep the two columns consistent even if a future caller sets one
            # without the other. The database enforces this too (in both
            # SQLite and Postgres), but failing a whole frame's write over a
            # caller's inconsistency would lose good rows alongside the bad.
            if employee_id is None:
                method, confidence = UNKNOWN_METHOD, 0.0
            elif method == UNKNOWN_METHOD:
                employee_id = None
                confidence = 0.0

            if method not in VALID_METHODS:
                logger.warning(
                    f"identity_events: unknown method {method!r}; recording as UNKNOWN"
                )
                employee_id, method, confidence = None, UNKNOWN_METHOD, 0.0

            due.append({
                "org_id": self.org_id,
                "employee_id": employee_id,
                "camera_id": self.camera_id,
                # NULL rather than the "TRANSIT_ZONE" sentinel activity_logs
                # uses: `identity_events.zoneId` is a real nullable FK in
                # Postgres, and inventing a zone name that does not exist in
                # `zones` would fail resolution at sync time.
                "zone_id": entity.get("zone_id") or None,
                "track_id": int(track_id),
                "identity_id": self.identity_for(track_id),
                "posture": str(posture).upper(),
                "confidence": max(0.0, min(1.0, confidence)),
                "method": method,
                "observed_at": observed_at.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            self._track_state[track_id] = {"last_write": now, "last_posture": posture}

        return due

    def flush_sync(self, rows: list) -> int:
        """
        Writes rows in a single transaction. Runs in a worker thread.

        Returns the number of rows written; 0 on failure. Never raises — a
        telemetry write must not be able to terminate a live video session.
        """
        if not rows:
            return 0

        session = SessionLocal()
        try:
            session.execute(
                text("""
                    INSERT INTO identity_events
                        (org_id, employee_id, camera_id, zone_id, track_id,
                         identity_id, posture, confidence, method,
                         observed_at, created_at)
                    VALUES
                        (:org_id, :employee_id, :camera_id, :zone_id, :track_id,
                         :identity_id, :posture, :confidence, :method,
                         :observed_at, :created_at)
                """),
                rows,
            )
            session.commit()
            return len(rows)
        except Exception as e:
            session.rollback()
            logger.warning(f"identity_events write failed ({len(rows)} rows dropped): {e}")
            return 0
        finally:
            session.close()


async def persist_identity_frame(
    writer: Optional[IdentityEventWriter],
    tracked_entities: list,
) -> None:
    """
    Async entry point called from the frame loop.

    Selects the rows that are due and writes them off the event loop. Returns
    immediately when nothing is due, which is the common case — most frames add
    no rows at all, so the per-frame overhead is a dict lookup per track.
    """
    if writer is None or not tracked_entities:
        return

    rows = writer.collect(tracked_entities)
    if not rows:
        return

    written = await asyncio.to_thread(writer.flush_sync, rows)
    writer.rows_written += written
