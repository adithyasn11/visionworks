# backend/app/db/safety_sync.py
"""
Copy the safety layer's local rows (SQLite) into Postgres, exactly once.

The pipeline writes `safety_events` and `safety_decisions` locally, because a
video loop must never wait for a network round trip. The dashboard reads
Postgres. This module is the bridge, and it is deliberately boring:

  * One direction. SQLite -> Postgres. A human review lives only in Postgres.
  * Idempotent. Every row carries a uuid made at the source, so sending it twice
    changes nothing.
        events     POST ...?on_conflict=eventUid   merge-duplicates
        decisions  POST ...?on_conflict=id         ignore-duplicates
    Decisions use IGNORE, never merge: a decision is a measurement, and a re-sent
    copy must not be able to overwrite the review fields a person set since.
  * Nothing is invented. A row whose camera or zone has no Postgres row stays
    unsynced and is counted. It is never attached to something else.
  * Never raises into the video loop. A failure is logged and the rows are tried
    again on the next tick.

MAPPING (design doc 24.8 / 24.10)

    organisation   identical uuid
    camera         local camera_id (a name)   -> cameras.id    by (orgId, name)
    zone           local zone_id (a slug)     -> zones.id      by (cameraId, slug)
                   no zone (a camera-wide row) is sent with zoneId = null
    rule           the rule's uuid, or null for the built-in default; a rule id
                   Postgres does not know is sent as null (the column is a
                   nullable foreign key) and counted

Soft-deleted cameras and zones still match: history recorded against a camera that
was later removed from the dashboard should still land under it. (New RULES cannot
attach to a deleted zone; that is the database's job.)

This does NOT reuse minute_aggregator._resolve_ids: that returns nothing when a
zone is missing, which would drop camera-wide rows, and it cannot tell "no such
row" from "the lookup failed". Here a failed lookup stops the pass (nothing is
retired, nothing counted as unmapped), because a network blip must never look
like a permanently unmappable row.

WORKING THROUGH A BACKLOG

Each pass takes the next `limit` unsynced rows AFTER a per-table cursor (created_at,
id), then moves the cursor on, and wraps to the start when it reaches the end. A pile
of rows that cannot be sent right now (no registered camera, a rejected row) is
therefore walked past, never sat behind: the rows after it are reached on the next
pass, and the pile is looked at again once per lap.

WHAT "RETIRED" MEANS

A row that can never reach Postgres would otherwise be retried for ever and, with a
service key configured, never purged. Such rows are stamped `synced_at` so the
ordinary housekeeping (`purge_local`) removes them a week later:

  * an organisation that is not a uuid: after `local_synced_retention_days`;
  * a camera or zone with no Postgres row, or a row Postgres refused for a reason
    about the row itself: after `UNMAPPED_RETIRE_DAYS` (30). Long, because a zone
    whose mirror failed or a camera registered late is still worth waiting for, and
    only for an organisation whose existence this pass CONFIRMED in Postgres, so a
    wrong or non-service key (which sees no rows at all) can never cause a deletion.

A row that merely could not be SENT (Postgres down, bad key, rate limit, a missing
table or column from a half-applied migration, a timeout) is never retired.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from app.db.database import engine as _default_engine

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500
SYNC_INTERVAL_S = 60
FIRST_PASS_DELAY_S = 5             # after start-up: do not make a backlog wait a whole interval
HTTP_TIMEOUT_S = 30
PASS_BUDGET_S = 45                 # stop examining rows after this long; the cursor resumes there
REJECT_COOLDOWN_S = 3600           # a row Postgres refused is not re-sent for this long
NEGATIVE_LOOKUP_TTL_S = 60         # "no such camera/zone" is remembered this long across passes
UNMAPPED_RETIRE_DAYS = 30

# Column widths in 025_safety.sql. Longer text is cut, never refused: a decision
# that cannot be stored at all is worse than a reason that ends early.
_WIDTHS = {"reason": 500, "workerKey": 80, "sessionKey": 40, "snapshotRef": 300, "eventUid": 36}

_run_lock = threading.Lock()
_rejected: dict = {}               # (table, row id) -> monotonic time until which the row is left alone
_cursor: dict = {}                 # table -> (created_at, id) of the last row examined
_negative: dict = {}               # lookup key -> monotonic time until which "not found" is believed
_background: set = set()           # keeps fire-and-forget tasks alive until they finish

_PG_ROW_ERROR = re.compile(r"^(22|23)\d{3}$")     # data exception / integrity violation: about the row


class _LookupFailed(Exception):
    """A Postgres lookup failed (as opposed to finding nothing). The pass stops."""


class _SendRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def about_the_row(self) -> bool:
        """
        Did Postgres refuse THIS DATA (a constraint, a bad value), as opposed to the
        request, the key, the schema or the server? Only the first may be set aside or
        retired. A 404 (table not in the schema cache), a missing column, a bad key or
        an outage must all be retried untouched: they are deployment problems, and
        retiring on them would delete good data.
        """
        if self.status not in (400, 409, 422):
            return False
        try:
            code = str(json.loads(self.detail).get("code", ""))
        except Exception:
            return False
        return bool(_PG_ROW_ERROR.match(code))


# ── configuration ────────────────────────────────────────────────────────────

def service_role_configured() -> bool:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return bool(key) and "your-" not in key and bool(os.getenv("SUPABASE_URL"))


def _creds() -> tuple:
    return (os.getenv("SUPABASE_URL") or "").rstrip("/"), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _is_uuid(value) -> bool:
    try:
        return str(_uuid.UUID(str(value))) == str(value).lower()
    except (ValueError, AttributeError, TypeError):
        return False


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _call(method: str, path: str, params: Optional[dict] = None, body=None, prefer: Optional[str] = None):
    base, key = _creds()
    url = f"{base}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8") or ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise _SendRejected(e.code, detail)


def _lookup(table: str, params: dict, cache: dict) -> Optional[str]:
    """
    The id of the first matching row, None if there is none. Raises _LookupFailed on any error.
    A positive answer is cached for the pass; "not found" for a minute across passes, so a
    run of uploads to one unregistered camera costs one request, not one per row.
    """
    key = (table, tuple(sorted(params.items())))
    if key in cache:
        return cache[key]
    if _negative.get(key, 0) > time.monotonic():
        return None
    try:
        _, raw = _call("GET", table, {**params, "select": "id", "limit": "1"})
        rows = json.loads(raw or "[]")
    except Exception as e:
        raise _LookupFailed(f"{table}: {type(e).__name__}: {e}")
    found = rows[0]["id"] if rows else None
    if found:
        cache[key] = found
        _negative.pop(key, None)
    else:
        _negative[key] = time.monotonic() + NEGATIVE_LOOKUP_TTL_S
        if len(_negative) > 5000:                           # bounded: drop what has expired
            now_m = time.monotonic()
            for k in [k for k, v in _negative.items() if v <= now_m]:
                del _negative[k]
    return found


def _org_exists(org_id: str, cache: dict) -> bool:
    """
    Does this organisation exist as far as the key can see? Rows are only ever retired as
    "unmappable" for an organisation this confirms: a key that is not the service role sees
    nothing, and "I see no camera" must not be read as "there is no camera".
    """
    key = ("org-exists", org_id)
    if key not in cache:
        try:
            _, raw = _call("GET", "organisations", {"select": "id", "id": f"eq.{org_id}", "limit": "1"})
            cache[key] = bool(json.loads(raw or "[]"))
        except Exception as e:
            raise _LookupFailed(f"organisations: {type(e).__name__}: {e}")
    return cache[key]


# ── row to payload ───────────────────────────────────────────────────────────

def _cut(value, field: str):
    if value is None:
        return None
    text_value = str(value)
    width = _WIDTHS.get(field)
    return text_value[:width] if width else text_value


def _json_or_none(raw):
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def event_payload(row: dict, camera_id: str, zone_id: Optional[str]) -> dict:
    return {
        "eventUid": _cut(row["event_uid"], "eventUid"),
        "orgId": row["org_id"],
        "cameraId": camera_id,
        "zoneId": zone_id,
        "workerKey": _cut(row["worker_key"], "workerKey"),
        "sessionKey": _cut(row["session_key"], "sessionKey"),
        "eventType": row["event_type"],
        "videoTimeS": row["video_time_s"],
        "observedAt": row["observed_at"],
        "details": _json_or_none(row["details"]),
    }


def decision_payload(row: dict, camera_id: str, zone_id: Optional[str], rule_id: Optional[str]) -> dict:
    return {
        "id": row["id"],
        "orgId": row["org_id"],
        "cameraId": camera_id,
        "zoneId": zone_id,
        "ruleId": rule_id,
        "ruleType": row["rule_type"],
        "severity": row["severity"],
        "workerKey": _cut(row["worker_key"], "workerKey"),
        "sessionKey": _cut(row["session_key"], "sessionKey"),
        "status": row["status"],
        "reason": _cut(row["reason"], "reason"),
        "confidence": row["confidence"],
        "evidence": _json_or_none(row["evidence"]) or {},
        "thresholds": _json_or_none(row["thresholds"]),
        "snapshotRef": _cut(row["snapshot_ref"], "snapshotRef"),
        "videoTimeS": row["video_time_s"],
        "observedAt": row["observed_at"],
        # A flag waits for a person; everything else needs no review. Sent with
        # the row, and harmless on a re-send because duplicates are ignored.
        "reviewState": "PENDING" if row["status"] == "POTENTIAL_VIOLATION" else "NOT_REQUIRED",
    }


# ── the sync ─────────────────────────────────────────────────────────────────

_TABLES = {
    # table: (Postgres path, conflict target, Prefer header)
    "safety_events": ("safety_events", "eventUid", "resolution=merge-duplicates,return=minimal"),
    "safety_decisions": ("safety_decisions", "id", "resolution=ignore-duplicates,return=minimal"),
}


def _iso(days_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="milliseconds")


def _in_clause(prefix: str, values: list) -> tuple:
    return ",".join(f":{prefix}{n}" for n in range(len(values))), {f"{prefix}{n}": v for n, v in enumerate(values)}


def _stamp(engine, table: str, ids: list, when: str) -> None:
    with engine.begin() as conn:
        for i in range(0, len(ids), 400):
            marks, params = _in_clause("i", ids[i:i + 400])
            conn.execute(text(f"UPDATE {table} SET synced_at = :now WHERE synced_at IS NULL AND id IN ({marks})"),
                         {"now": when, **params})


def _next_rows(engine, table: str, limit: int) -> list:
    """The next `limit` unsynced rows after this table's cursor (see the module docstring)."""
    where, params = ["synced_at IS NULL"], {"limit": int(limit)}
    after = _cursor.get(table)
    if after:
        where.append("(created_at > :c OR (created_at = :c AND id > :i))")
        params.update({"c": after[0], "i": after[1]})
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY created_at ASC, id ASC LIMIT :limit"), params)]


def _retire_ids(engine, table: str, ids: list, days: float) -> int:
    """Stamp the given rows synced if they were created more than `days` ago, so housekeeping can delete them."""
    if not ids:
        return 0
    cutoff, now, total = _iso(days), _iso(), 0
    with engine.begin() as conn:
        for i in range(0, len(ids), 400):
            marks, params = _in_clause("i", ids[i:i + 400])
            total += conn.execute(
                text(f"UPDATE {table} SET synced_at = :now WHERE synced_at IS NULL AND created_at < :cut AND id IN ({marks})"),
                {"now": now, "cut": cutoff, **params}).rowcount
    return total


def _known_rules(rule_ids: set) -> set:
    """Which of these rule ids exist in Postgres (soft-deleted rules still do)."""
    known, wanted = set(), sorted(r for r in rule_ids if _is_uuid(r))
    for i in range(0, len(wanted), 100):
        chunk = wanted[i:i + 100]
        try:
            _, raw = _call("GET", "safety_rules", {"select": "id", "id": "in.(" + ",".join(chunk) + ")"})
            known.update(r["id"] for r in json.loads(raw or "[]"))
        except Exception as e:
            raise _LookupFailed(f"safety_rules: {type(e).__name__}: {e}")
    return known


def _send(table: str, payload: list) -> None:
    path, conflict, prefer = _TABLES[table]
    status, _ = _call("POST", path, {"on_conflict": conflict}, payload, prefer)
    if status not in (200, 201, 204):
        raise _SendRejected(status, "unexpected status")


def _sync_table(engine, table: str, limit: int, retire_days: int, summary: dict, cache: dict, deadline: float) -> None:
    rows = _next_rows(engine, table, limit)
    if not rows:
        _cursor.pop(table, None)                            # reached the end: next pass starts from the top
        return
    wrapped = len(rows) < limit
    now_m = time.monotonic()
    known_rules = _known_rules({r["rule_id"] for r in rows if table == "safety_decisions" and r.get("rule_id")}) \
        if table == "safety_decisions" else set()

    built, last_examined = [], None
    invalid, unmapped, unmapped_in_known_org = [], [], []
    for row in rows:
        if time.monotonic() > deadline:                     # out of time: resume from here next pass
            wrapped = False
            break
        last_examined = row
        if not _is_uuid(row["org_id"]):
            invalid.append(row["id"])                       # can never sync (e.g. "test-org", or no organisation)
            continue
        if _rejected.get((table, row["id"]), 0) > now_m:
            continue                                        # refused a short while ago
        camera = _lookup("cameras", {"orgId": f"eq.{row['org_id']}", "name": f"eq.{row['camera_id']}"}, cache)
        zone = None
        if camera and row["zone_id"]:
            zone = _lookup("zones", {"cameraId": f"eq.{camera}", "slug": f"eq.{row['zone_id']}"}, cache)
        if not camera or (row["zone_id"] and not zone):
            unmapped.append(row["id"])
            pair = f"{row['camera_id']}/{row['zone_id'] or '-'}"
            summary["unmapped_pairs"][pair] = summary["unmapped_pairs"].get(pair, 0) + 1
            if _org_exists(row["org_id"], cache):
                unmapped_in_known_org.append(row["id"])
            continue
        if table == "safety_events":
            built.append((row["id"], event_payload(row, camera, zone)))
        else:
            rule = row["rule_id"] if row["rule_id"] in known_rules else None
            if row["rule_id"] and rule is None:
                summary["rule_missing"] += 1
            built.append((row["id"], decision_payload(row, camera, zone, rule)))

    if last_examined is not None:
        _cursor[table] = (last_examined["created_at"], last_examined["id"])
    if wrapped:
        _cursor.pop(table, None)
    summary["unmapped"] += len(unmapped)
    summary["invalid"] += len(invalid)
    summary["retired"] += _retire_ids(engine, table, invalid, retire_days)
    summary["retired"] += _retire_ids(engine, table, unmapped_in_known_org, UNMAPPED_RETIRE_DAYS)

    if not built:
        return
    ids = [i for i, _ in built]
    payload = [p for _, p in built]
    when = _iso()
    kind = table.split("_")[1]                              # "events" / "decisions"
    try:
        _send(table, payload)
        _stamp(engine, table, ids, when)
        summary["synced_" + kind] += len(ids)
        return
    except _SendRejected as e:
        if not e.about_the_row:
            raise                                           # the request, key, schema or server: try everything again next pass
        first_error = e
    # Postgres refused the batch for a reason about the data. Find out which row, so one
    # bad row cannot hold back its neighbours.
    good, bad = [], []
    for row_id, one in built:
        try:
            _send(table, [one])
            good.append(row_id)
        except _SendRejected as e:
            if not e.about_the_row:
                raise
            bad.append((row_id, e))
    if good:
        _stamp(engine, table, good, when)
        summary["synced_" + kind] += len(good)
    for row_id, _e in bad:
        _rejected[(table, row_id)] = time.monotonic() + REJECT_COOLDOWN_S
    if bad:
        summary["rejected"] += len(bad)
        logger.error("Postgres rejected %d %s row(s); first: %s", len(bad), table, bad[0][1])
        summary["retired"] += _retire_ids(engine, table, [r for r, _ in bad], UNMAPPED_RETIRE_DAYS)
    elif not good:
        logger.error("Postgres rejected a %s batch: %s", table, first_error)


def sync_safety_sync(limit: int = BATCH_LIMIT, *, engine=None, retire_days: Optional[int] = None) -> dict:
    """One pass over both tables. Never raises."""
    summary = {"synced_events": 0, "synced_decisions": 0, "unmapped": 0, "unmapped_pairs": {},
               "invalid": 0, "rejected": 0, "rule_missing": 0, "retired": 0}
    if not service_role_configured():
        summary["reason"] = "no service-role key configured"
        return summary
    if not _run_lock.acquire(blocking=False):
        summary["reason"] = "already running"
        return summary
    try:
        engine = engine or _default_engine
        if retire_days is None:
            try:
                from app.safety.config import load_config
                retire_days = int(load_config().local_synced_retention_days)
            except Exception:
                retire_days = 7
        now_m = time.monotonic()
        for k in [k for k, until in _rejected.items() if until <= now_m]:
            del _rejected[k]
        from app.db.safety_models import ensure_safety_tables
        ensure_safety_tables(engine)
        lookups: dict = {}                                  # shared: each camera and zone is asked for once per pass
        deadline = time.monotonic() + PASS_BUDGET_S
        for table in ("safety_events", "safety_decisions"):
            _sync_table(engine, table, limit, retire_days, summary, lookups, deadline)
    except _LookupFailed as e:
        summary["error"] = str(e)
        logger.warning("Safety sync paused (Postgres lookup failed): %s", e)
    except _SendRejected as e:
        summary["error"] = str(e)
        logger.warning("Safety sync will retry (%s)", e)
    except Exception as e:                                  # never into the video loop
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.error("Safety sync failed: %s", summary["error"])
    finally:
        _run_lock.release()
    return summary


async def sync_safety(**kwargs) -> dict:
    """sync_safety_sync() off the event loop."""
    import asyncio
    return await asyncio.to_thread(sync_safety_sync, **kwargs)


def sync_safety_in_background() -> None:
    """
    Start a pass now without waiting for it (the end of a video session). The task is
    kept in a set until it finishes: an asyncio task nothing refers to can be collected
    mid-run. A no-op without a service-role key. Never raises.
    """
    try:
        import asyncio
        if not service_role_configured():
            return
        task = asyncio.get_running_loop().create_task(sync_safety())
        _background.add(task)
        task.add_done_callback(_background.discard)
    except Exception as e:
        logger.warning("Could not start the safety sync: %s", e)


def unsynced_counts(engine=None) -> dict:
    """How many local rows have not reached Postgres (for a health page)."""
    engine = engine or _default_engine
    out = {}
    with engine.connect() as conn:
        for table in _TABLES:
            try:
                out[table] = int(conn.execute(text(f"SELECT count(*) FROM {table} WHERE synced_at IS NULL")).scalar() or 0)
            except Exception:
                out[table] = 0
    return out


UNMAPPED_ALERT_THRESHOLD = 5


async def run_safety_sync_loop(stop_event=None) -> None:
    """Sync every SYNC_INTERVAL_S seconds (the first pass soon after start). One failed pass never ends the loop."""
    import asyncio
    logger.info("Safety sync started (%ss interval, Postgres %s)", SYNC_INTERVAL_S,
                "on" if service_role_configured() else "off: no service-role key")
    delay = FIRST_PASS_DELAY_S
    while True:
        try:
            if stop_event is not None and stop_event.is_set():
                return
            await asyncio.sleep(delay)
            delay = SYNC_INTERVAL_S
            if not service_role_configured():
                continue
            pushed = await sync_safety()
            if pushed.get("synced_events") or pushed.get("synced_decisions"):
                logger.info("Synced %s safety event(s) and %s decision(s) to Postgres",
                            pushed["synced_events"], pushed["synced_decisions"])
            if pushed.get("unmapped", 0) >= UNMAPPED_ALERT_THRESHOLD:
                named = ", ".join(f"{k} x{v}" for k, v in sorted(
                    pushed["unmapped_pairs"].items(), key=lambda kv: -kv[1])[:6])
                logger.error("%d safety rows could not be mapped to a registered camera/zone: %s. They stay in "
                             "SQLite until a camera and zone with those names exist in Postgres.",
                             pushed["unmapped"], named)
        except asyncio.CancelledError:
            logger.info("Safety sync stopped")
            raise
        except Exception as e:
            logger.error("Safety sync tick failed: %s", e)
