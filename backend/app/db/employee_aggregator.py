# backend/app/db/employee_aggregator.py
"""
Per-employee daily rollup: desk time, chair exits, breaks, focus blocks.

Step 8 of IDENTITY_TRACKING_PLAN.md, and the part the whole plan is really for.
The plan is explicit: "If you run short on time, this is the part that must
work", and `awayFromDeskCount` is "the single most important verification in the
whole plan — it is the number your sir asked for".

WHAT THIS IS THE PER-PERSON TWIN OF

`minute_aggregator.py` folds `activity_logs` into `zone_minute_stats`: one row
per zone per minute, counts only, no person reference, deliberately impossible
to de-anonymise. This module folds `identity_events` into `employee_day_stats`:
one row per PERSON per day.

The two run side by side and never touch each other's tables. That separation
is the deal the schema makes — a deployment can run the anonymous path alone,
and the anonymous path stays anonymous whatever this module does.

═══════════════════════════════════════════════════════════════════════════
 THE FOUR MEASURES, AND WHY EACH THRESHOLD IS WHAT IT IS
═══════════════════════════════════════════════════════════════════════════

CHAIR EXIT — left the assigned zone for > 90 s.

  The debounce is the whole measure. Without it every measure is nonsense:
  a person bending to a drawer, leaning back out of the polygon, or being
  briefly occluded by a colleague would each register as "left the desk".
  The plan calls 90 s a guess to be tuned on real footage, and it is right to:
  it trades sensitivity (a genuine 60-second trip to the printer is missed)
  for precision (a 5-second lean-back is not counted). Precision is the right
  side to err on, because an inflated exit count is a claim about someone's
  behaviour that they would dispute and you could not defend.

BREAK — in a BREAK-type zone, or absent from every camera, for > 5 min.

  Two different situations with the same meaning. Note the asymmetry with a
  chair exit: a break is also an absence from the desk, so the same span can
  be both. They measure different things — "how fragmented was the day" versus
  "how much of it was time off" — and double-counting them is correct.

FOCUS BLOCK — continuously SEATED in the assigned zone for > 20 min.

  "Low motion" in the plan is already captured: `posture` is the smoothed
  output of the pose classifier, and someone fidgeting reads as SITTING while
  someone who gets up reads as STANDING or WALKING. Requiring the posture to
  stay SITTING is the same test, using the signal the pipeline already
  computes rather than a second motion threshold to tune.

FRAGMENTATION INDEX — focus blocks per desk hour.

  Low is good: one long block in a 4-hour stretch is 0.25. Reported as a rate
  rather than a count so it can be compared between a 2-hour day and an 8-hour
  one.

═══════════════════════════════════════════════════════════════════════════
 THE TRAP THIS AVOIDS, AND MINUTE_AGGREGATOR DOCUMENTS
═══════════════════════════════════════════════════════════════════════════

`identity_events` is SAMPLED — roughly every 5 seconds per person, plus one on
every posture change. It is NOT a per-frame record. So:

  - Time is measured as the SPAN between samples, never as a count of samples
    multiplied by anything. Counting samples would make the numbers depend on
    the sampling rate rather than on what happened.

  - A gap between two samples larger than `MAX_SAMPLE_GAP_SECONDS` is treated
    as ABSENCE, not as continuous presence. Without that rule, a person who
    left at 10am and returned at 2pm would be credited with four hours at their
    desk, because their two samples are simply consecutive rows.

Every measure below is built on spans, and every span is bounded by that gap
rule.
"""

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta

from sqlalchemy import text

from app.db.database import engine, SessionLocal

logger = logging.getLogger(__name__)

# ── The measures ────────────────────────────────────────────────────────────

# A departure shorter than this is not a chair exit. See the module docstring:
# this is the debounce that stops bending down being counted as leaving.
CHAIR_EXIT_MIN_SECONDS = 90.0

# A break. Long enough that walking to the printer is not one.
BREAK_MIN_SECONDS = 300.0

# Continuous seated work that counts as a focus block.
FOCUS_BLOCK_MIN_SECONDS = 1200.0

# ── The sampling rule ───────────────────────────────────────────────────────

# The writer samples every ~5 s. A gap beyond this means the person was not
# observed, not that they stood perfectly still: presence is not carried across
# it. 30 s allows for a few missed samples (a brief occlusion, a dropped frame)
# without inventing hours of attendance out of two distant rows.
MAX_SAMPLE_GAP_SECONDS = 30.0

# Postures that count as being at work rather than away.
PRESENT_POSTURES = ("SITTING", "STANDING", "WALKING")


def _parse(raw):
    """ISO string (or datetime) -> aware UTC datetime, or None."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_stats_table() -> None:
    """
    Creates the local `employee_day_stats` mirror.

    Column-for-column the same as the Postgres table in migration 020, in
    snake_case rather than camelCase — the same split `zone_minute_stats`
    already has. The sync below maps between them.

    UNIQUE(employee_id, stat_date) mirrors `@@unique([employeeId, statDate])`
    and is what makes this aggregator idempotent: re-running over the same day
    updates the row rather than adding a second one.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employee_day_stats (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id               TEXT,
                employee_id          TEXT NOT NULL,
                stat_date            TEXT NOT NULL,
                first_seen_at        TEXT,
                last_seen_at         TEXT,
                present_minutes      INTEGER NOT NULL DEFAULT 0,
                desk_minutes         INTEGER NOT NULL DEFAULT 0,
                seated_minutes       INTEGER NOT NULL DEFAULT 0,
                away_from_desk_count INTEGER NOT NULL DEFAULT 0,
                break_minutes        INTEGER NOT NULL DEFAULT 0,
                longest_focus_block  INTEGER NOT NULL DEFAULT 0,
                fragmentation_idx    REAL    NOT NULL DEFAULT 0,
                binding_confidence   REAL    NOT NULL DEFAULT 0,
                unknown_minutes      INTEGER NOT NULL DEFAULT 0,
                synced_at            TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE (employee_id, stat_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_eds_org_date "
            "ON employee_day_stats (org_id, stat_date)"
        ))
        # The Postgres sync scans for unsynced rows.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_eds_unsynced "
            "ON employee_day_stats (synced_at)"
        ))


def analyse_timeline(samples: list, desk_zones: set, break_zones: set) -> dict:
    """
    Turn one employee's ordered samples for one day into the day's measures.

    `samples` is a list of dicts with `observed_at`, `zone_id`, `posture` and
    `confidence`, sorted by time. `desk_zones` is the zone(s) that count as
    this person's desk; `break_zones` are the BREAK-type zones.

    Pure and side-effect free, so the thresholds can be tested directly against
    a hand-built timeline — which is exactly what the Step 8 verification does.
    """
    out = {
        "first_seen_at": None, "last_seen_at": None,
        "present_seconds": 0.0, "desk_seconds": 0.0, "seated_seconds": 0.0,
        "away_from_desk_count": 0, "break_seconds": 0.0,
        "longest_focus_block": 0.0, "focus_blocks": 0,
        "fragmentation_idx": 0.0, "binding_confidence": 0.0,
        "unknown_seconds": 0.0, "samples": len(samples),
        "chair_exits": [], "breaks": [],
    }
    if not samples:
        return out

    ordered = sorted(samples, key=lambda s: s["observed_at"])
    out["first_seen_at"] = ordered[0]["observed_at"]
    out["last_seen_at"] = ordered[-1]["observed_at"]

    confidences = [s.get("confidence") or 0.0 for s in ordered]
    out["binding_confidence"] = sum(confidences) / len(confidences)

    # ── Spans between consecutive samples ───────────────────────────────────
    #
    # Each span is credited with the STATE AT ITS START. A sample says "at this
    # instant they were SITTING at desk_1"; the seconds until the next sample
    # are attributed to that state. Using the end state instead would attribute
    # the walk to wherever they arrived.
    at_desk_since = None
    away_since = None
    focus_since = None
    break_since = None

    def close_focus(end):
        nonlocal focus_since
        if focus_since is None:
            return
        length = (end - focus_since).total_seconds()
        if length >= FOCUS_BLOCK_MIN_SECONDS:
            out["focus_blocks"] += 1
            out["longest_focus_block"] = max(out["longest_focus_block"], length)
        focus_since = None

    def close_away(end):
        nonlocal away_since
        if away_since is None:
            return
        length = (end - away_since).total_seconds()
        # THE CHAIR EXIT. Only a departure longer than the debounce counts.
        if length >= CHAIR_EXIT_MIN_SECONDS:
            out["away_from_desk_count"] += 1
            out["chair_exits"].append({
                "from": away_since.isoformat(),
                "to": end.isoformat(),
                "seconds": round(length, 1),
            })
        away_since = None

    def close_break(end):
        nonlocal break_since
        if break_since is None:
            return
        length = (end - break_since).total_seconds()
        if length >= BREAK_MIN_SECONDS:
            out["break_seconds"] += length
            out["breaks"].append({
                "from": break_since.isoformat(),
                "to": end.isoformat(),
                "seconds": round(length, 1),
            })
        break_since = None

    for i, s in enumerate(ordered):
        t = s["observed_at"]
        zone = s.get("zone_id")
        posture = (s.get("posture") or "").upper()
        at_desk = zone in desk_zones if desk_zones else False
        on_break_zone = zone in break_zones if break_zones else False

        # How long this state lasted: until the next sample, unless the gap is
        # too large to call it continuous.
        if i + 1 < len(ordered):
            gap = (ordered[i + 1]["observed_at"] - t).total_seconds()
        else:
            gap = 0.0
        observed_span = gap if gap <= MAX_SAMPLE_GAP_SECONDS else 0.0
        absent = gap > MAX_SAMPLE_GAP_SECONDS

        if posture in PRESENT_POSTURES:
            out["present_seconds"] += observed_span
        if at_desk:
            out["desk_seconds"] += observed_span
            if posture == "SITTING":
                out["seated_seconds"] += observed_span

        # ── Chair exits ─────────────────────────────────────────────────────
        if at_desk:
            close_away(t)
            at_desk_since = at_desk_since or t
        else:
            if at_desk_since is not None and away_since is None:
                away_since = t
            at_desk_since = None

        # ── Focus blocks: continuous SEATED at the desk ─────────────────────
        if at_desk and posture == "SITTING":
            focus_since = focus_since or t
        else:
            close_focus(t)

        # ── Breaks: in a break zone, or absent entirely ─────────────────────
        if on_break_zone:
            break_since = break_since or t
        else:
            close_break(t)

        # A gap longer than the sample rule means they were not observed at
        # all. That is itself a candidate break, and it also ends every open
        # run — presence cannot be carried across an absence.
        if absent:
            gap_end = ordered[i + 1]["observed_at"]
            close_focus(t)
            close_break(t)
            if gap >= BREAK_MIN_SECONDS:
                out["break_seconds"] += gap
                out["breaks"].append({
                    "from": t.isoformat(), "to": gap_end.isoformat(),
                    "seconds": round(gap, 1), "reason": "absent from all cameras",
                })
            # An absence that starts at the desk is also a departure from it.
            if at_desk and away_since is None:
                away_since = t
            at_desk_since = None

    # Close whatever is still open at the end of the day.
    end = ordered[-1]["observed_at"]
    close_focus(end)
    close_away(end)
    close_break(end)

    desk_hours = out["desk_seconds"] / 3600.0
    out["fragmentation_idx"] = (out["focus_blocks"] / desk_hours) if desk_hours > 0 else 0.0
    return out


def aggregate_day_sync(day: date = None, org_id: str = None) -> dict:
    """
    Roll one day's `identity_events` into `employee_day_stats`.

    Only rows with an `employee_id` produce a stats row — an UNKNOWN
    observation belongs to nobody, and inventing a row for it would be exactly
    the guess the plan forbids. UNKNOWN time is instead accumulated into
    `unknown_minutes` on the employees observed at the same camera, so the
    unattributed residue is visible rather than silently dropped.

    Idempotent: upserts on (employee_id, stat_date), so re-running replaces.
    """
    ensure_stats_table()
    day = day or datetime.now(timezone.utc).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    session = SessionLocal()
    try:
        params = {"start": start.isoformat(), "end": end.isoformat()}
        where_org = ""
        if org_id is not None:
            where_org = " AND org_id = :org_id"
            params["org_id"] = org_id

        rows = [dict(r._mapping) for r in session.execute(text(f"""
            SELECT org_id, employee_id, camera_id, zone_id, posture,
                   confidence, method, observed_at
              FROM identity_events
             WHERE observed_at >= :start AND observed_at < :end{where_org}
             ORDER BY observed_at ASC
        """), params)]

        if not rows:
            return {"employees": 0, "rows_read": 0, "day": day.isoformat()}

        # Which zone is whose desk, and which zones are breaks.
        desk_by_employee, break_zones = _load_zone_config(session, org_id)

        by_employee = defaultdict(list)
        unknown_seconds_by_org = defaultdict(float)
        prev_unknown_t = {}

        for r in rows:
            t = _parse(r["observed_at"])
            if t is None:
                continue
            if r["employee_id"]:
                by_employee[(r["org_id"], r["employee_id"])].append({
                    "observed_at": t,
                    "zone_id": r["zone_id"],
                    "posture": r["posture"],
                    "confidence": r["confidence"],
                })
            else:
                # Unattributed observation. Measured as a span like everything
                # else, so "we could not say who this was for 12 minutes" is a
                # number rather than a row count.
                key = (r["org_id"], r["camera_id"])
                prev = prev_unknown_t.get(key)
                if prev is not None:
                    gap = (t - prev).total_seconds()
                    if 0 < gap <= MAX_SAMPLE_GAP_SECONDS:
                        unknown_seconds_by_org[r["org_id"]] += gap
                prev_unknown_t[key] = t

        written = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        results = []

        for (org, employee_id), samples in by_employee.items():
            desks = desk_by_employee.get(employee_id, set())
            stats = analyse_timeline(samples, desks, break_zones)

            # The unattributed residue is shared across the employees observed
            # that day rather than assigned to any one of them: nobody knows
            # whose it was, which is the entire point of it being unknown.
            unknown_share = (unknown_seconds_by_org.get(org, 0.0)
                             / max(1, len(by_employee)))

            payload = {
                "org_id": org,
                "employee_id": employee_id,
                "stat_date": day.isoformat(),
                "first_seen_at": stats["first_seen_at"].isoformat() if stats["first_seen_at"] else None,
                "last_seen_at": stats["last_seen_at"].isoformat() if stats["last_seen_at"] else None,
                # Clamped to a day: the CHECK constraints in migration 020
                # reject anything above 1440, and a value that large means a
                # bug upstream rather than a very long shift.
                "present_minutes": _minutes(stats["present_seconds"]),
                "desk_minutes": _minutes(stats["desk_seconds"]),
                "seated_minutes": _minutes(stats["seated_seconds"]),
                "away_from_desk_count": stats["away_from_desk_count"],
                "break_minutes": _minutes(stats["break_seconds"]),
                "longest_focus_block": _minutes(stats["longest_focus_block"]),
                "fragmentation_idx": round(stats["fragmentation_idx"], 4),
                "binding_confidence": round(min(1.0, max(0.0, stats["binding_confidence"])), 4),
                "unknown_minutes": _minutes(unknown_share),
                "now": now_iso,
            }

            session.execute(text("""
                INSERT INTO employee_day_stats
                    (org_id, employee_id, stat_date, first_seen_at, last_seen_at,
                     present_minutes, desk_minutes, seated_minutes,
                     away_from_desk_count, break_minutes, longest_focus_block,
                     fragmentation_idx, binding_confidence, unknown_minutes,
                     created_at, updated_at)
                VALUES
                    (:org_id, :employee_id, :stat_date, :first_seen_at, :last_seen_at,
                     :present_minutes, :desk_minutes, :seated_minutes,
                     :away_from_desk_count, :break_minutes, :longest_focus_block,
                     :fragmentation_idx, :binding_confidence, :unknown_minutes,
                     :now, :now)
                ON CONFLICT (employee_id, stat_date) DO UPDATE SET
                    org_id               = excluded.org_id,
                    first_seen_at        = excluded.first_seen_at,
                    last_seen_at         = excluded.last_seen_at,
                    present_minutes      = excluded.present_minutes,
                    desk_minutes         = excluded.desk_minutes,
                    seated_minutes       = excluded.seated_minutes,
                    away_from_desk_count = excluded.away_from_desk_count,
                    break_minutes        = excluded.break_minutes,
                    longest_focus_block  = excluded.longest_focus_block,
                    fragmentation_idx    = excluded.fragmentation_idx,
                    binding_confidence   = excluded.binding_confidence,
                    unknown_minutes      = excluded.unknown_minutes,
                    updated_at           = excluded.updated_at,
                    -- Re-running must re-sync: the numbers changed.
                    synced_at            = NULL
            """), payload)
            written += 1
            results.append({**payload, "chair_exits": stats["chair_exits"],
                            "breaks": stats["breaks"],
                            "focus_blocks": stats["focus_blocks"]})

        session.commit()
        return {
            "day": day.isoformat(),
            "employees": written,
            "rows_read": len(rows),
            "results": results,
        }

    except Exception as e:
        session.rollback()
        logger.error(f"employee day aggregation failed: {e}")
        return {"employees": 0, "rows_read": 0, "error": str(e)}
    finally:
        session.close()


def _minutes(seconds: float) -> int:
    """Seconds -> whole minutes, clamped to a day (migration 020's CHECK)."""
    return int(max(0, min(1440, round(seconds / 60.0))))


def _load_zone_config(session, org_id):
    """
    (employee_id -> {desk zone ids}, {break zone ids}) from the local tables.

    The desk map comes from `employees.assigned_zone_id` when that table has
    been mirrored locally; otherwise it is inferred from the identity events
    themselves — the zone an employee was seen in most. The inference is a
    fallback for a standalone pipeline with no Supabase, and it is honest about
    what it is: without a configured desk there is no seat prior, only an
    observation.
    """
    desks = defaultdict(set)
    breaks = set()

    try:
        for r in session.execute(text(
            "SELECT zone_id, zone_type FROM zones"
        )):
            if (r.zone_type or "").upper() == "BREAK":
                breaks.add(r.zone_id)
    except Exception:
        pass

    try:
        rows = session.execute(text(
            "SELECT employee_id, assigned_zone_id FROM employees "
            "WHERE assigned_zone_id IS NOT NULL"
        ))
        for r in rows:
            desks[r.employee_id].add(r.assigned_zone_id)
    except Exception:
        # No local employees mirror. Infer each employee's desk from where
        # their own events place them, which is what the seat binding used to
        # name them in the first place.
        params = {}
        where = ""
        if org_id is not None:
            where = " AND org_id = :org_id"
            params["org_id"] = org_id
        try:
            for r in session.execute(text(f"""
                SELECT employee_id, zone_id, COUNT(*) AS n
                  FROM identity_events
                 WHERE employee_id IS NOT NULL AND zone_id IS NOT NULL{where}
                 GROUP BY employee_id, zone_id
            """), params):
                if r.zone_id != "TRANSIT_ZONE":
                    desks[r.employee_id].add(r.zone_id)
        except Exception as e:
            logger.debug(f"could not infer desks: {e}")

    return desks, breaks


async def aggregate_day(**kwargs) -> dict:
    """aggregate_day_sync() off the event loop."""
    return await asyncio.to_thread(aggregate_day_sync, **kwargs)


# ── Postgres sync ───────────────────────────────────────────────────────────

def service_role_configured() -> bool:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return bool((os.getenv("SUPABASE_URL") or "").strip()
                and key and not key.startswith("your-"))


def sync_to_postgres_sync(limit: int = 500) -> dict:
    """
    Push unsynced local day stats into Postgres `employee_day_stats`.

    Follows `minute_aggregator.sync_to_postgres_sync()` exactly: upsert on the
    same unique key the local table uses, so a retry replaces rather than
    duplicates, and rows that cannot be resolved stay unsynced and are counted
    rather than guessed at.

    The one difference is what gets resolved. Minute buckets resolve a camera
    and a zone BY NAME; these rows already carry the Postgres `employees.id`
    UUID, because that is what the seat binding bound to. So there is nothing
    to look up — but the id is still validated against Postgres before the
    write, because a stale local id would otherwise fail the foreign key and
    take the whole batch down with it.
    """
    if not service_role_configured():
        return {"synced": 0, "unmapped": 0, "reason": "no service-role key configured"}

    import json
    import urllib.request

    ensure_stats_table()
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    session = SessionLocal()
    try:
        rows = [dict(r._mapping) for r in session.execute(text("""
            SELECT * FROM employee_day_stats
             WHERE synced_at IS NULL AND org_id IS NOT NULL
             ORDER BY stat_date ASC
             LIMIT :limit
        """), {"limit": limit})]
        if not rows:
            return {"synced": 0, "unmapped": 0}

        payload, synced_ids, unmapped = [], [], 0
        for row in rows:
            if not _employee_exists(base, key, row["employee_id"]):
                # The employee was deleted, or this id predates the current
                # Supabase project. Left unsynced and counted, never invented.
                unmapped += 1
                continue
            payload.append({
                "orgId": row["org_id"],
                "employeeId": row["employee_id"],
                "statDate": row["stat_date"],
                "firstSeenAt": row["first_seen_at"],
                "lastSeenAt": row["last_seen_at"],
                "presentMinutes": row["present_minutes"],
                "deskMinutes": row["desk_minutes"],
                "seatedMinutes": row["seated_minutes"],
                "awayFromDeskCount": row["away_from_desk_count"],
                "breakMinutes": row["break_minutes"],
                "longestFocusBlock": row["longest_focus_block"],
                "fragmentationIdx": row["fragmentation_idx"],
                "bindingConfidence": row["binding_confidence"],
                "unknownMinutes": row["unknown_minutes"],
                # Prisma's @updatedAt is client-side and the column has no DB
                # default — the trap documented in 008 and minute_aggregator.
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            synced_ids.append(row["id"])

        if not payload:
            return {"synced": 0, "unmapped": unmapped}

        request = urllib.request.Request(
            f"{base}/rest/v1/employee_day_stats?on_conflict=employeeId,statDate",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201, 204):
                raise RuntimeError(f"PostgREST returned {response.status}")

        stamp = datetime.now(timezone.utc).isoformat()
        for row_id in synced_ids:
            session.execute(
                text("UPDATE employee_day_stats SET synced_at = :now WHERE id = :id"),
                {"now": stamp, "id": row_id},
            )
        session.commit()
        return {"synced": len(synced_ids), "unmapped": unmapped}

    except Exception as e:
        session.rollback()
        logger.error(f"employee day stats sync failed: {e}")
        return {"synced": 0, "unmapped": 0, "error": str(e)}
    finally:
        session.close()


_employee_cache: dict = {}


def _employee_exists(base: str, key: str, employee_id: str) -> bool:
    """Does this employee id exist in Postgres? Cached per process."""
    if employee_id in _employee_cache:
        return _employee_cache[employee_id]
    import json
    import urllib.parse
    import urllib.request
    url = (f"{base}/rest/v1/employees?"
           + urllib.parse.urlencode({"select": "id", "id": f"eq.{employee_id}", "limit": "1"}))
    request = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            found = bool(json.loads(response.read().decode("utf-8") or "[]"))
    except Exception as e:
        logger.warning(f"could not verify employee {employee_id}: {e}")
        found = False
    _employee_cache[employee_id] = found
    return found


async def sync_to_postgres(**kwargs) -> dict:
    return await asyncio.to_thread(sync_to_postgres_sync, **kwargs)


async def aggregate_after_session(org_id: str = None) -> dict:
    """
    Roll up today and push it upstream. Called when a session ends, so the
    numbers appear without waiting for a timer.

    Swallows its own failures: an aggregation problem must not surface as a
    broken video session.
    """
    try:
        result = await aggregate_day(org_id=org_id)
        if result.get("employees"):
            await sync_to_postgres()
        return result
    except Exception as e:
        logger.warning(f"employee aggregation after session failed: {e}")
        return {"employees": 0, "error": str(e)}
