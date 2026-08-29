# backend/app/db/employee_hour_aggregator.py
"""
Per-employee HOURLY rollup — the daily aggregator at one finer resolution.

Step 15 of IDENTITY_TRACKING_PLAN.md asks the per-employee dashboard for a
"daily timeline". `employee_day_stats` cannot answer that: one row per person
per day tells you how long somebody was at their desk, never when.

WHY NOT JUST SYNC THE RAW EVENTS

`identity_events` already holds the shape — one row every five seconds with an
`observedAt`. It would seem simpler to push those to Postgres and let the
browser bucket them. Two reasons not to:

  VOLUME. A sample every 5 s is 720/hour, ~11.5k for a 16-hour day, per
  person. A ten-person office is 115k rows a day crossing the network so the
  client can average them straight back down into 24 buckets. The aggregation
  belongs where the data already is; what crosses the wire is 24 rows.

  PRIVACY. The raw stream is a second-by-second record of one person's
  movements. The hourly rollup answers "when was Prajwal at his desk" without
  shipping "where exactly was Prajwal at 14:32:05" to every browser that asks.
  Migration 022 narrows identity_events for the same reason.

WHAT AN HOUR ROW MEANS

Each row covers one clock hour [hour:00, hour:59] in UTC — the same timezone
the daily rollup buckets by, so the two never disagree about which day a
14:00 observation belongs to.

`bindingConfidence` is computed PER HOUR and deliberately not inherited from
the day. An hour at 0.95 and an hour at 0.55 averaged into a daily 0.75 hides
that half the day is unreliable, which is the exact failure Step 14 exists to
prevent: a confidence hidden inside an average stops being a warning.

WHY THE MINUTES ARE MEASURED AS SPANS

Not `count(samples) * 5s`. A dropped frame, a paused upload, or a camera
restart leaves a gap, and multiplying a sample count would silently credit
that gap as presence. Every measure here is the sum of gaps between
consecutive samples, capped at MAX_SAMPLE_GAP_SECONDS — the same rule
`analyse_timeline()` uses, for the same reason it documents.

An hour is capped at 60 minutes on write, and the Postgres CHECK enforces it
independently. Two people can be observed in the same hour and each gets their
own row, so the cap is per person, never a sum.
"""

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from .database import SessionLocal, engine
from .employee_aggregator import (
    IDENTITY_MIN_CONFIDENCE,
    MAX_SAMPLE_GAP_SECONDS,
    _load_zone_config,
    _parse,
    service_role_configured,
)

logger = logging.getLogger(__name__)

# One hour. Used to cap every per-hour measure — a rollup that reported 61
# minutes inside an hour would mean the span arithmetic double-counted.
SECONDS_PER_HOUR = 3600.0


def ensure_hour_stats_table() -> None:
    """
    Creates the local `employee_hour_stats` mirror.

    Column-for-column the Postgres table in migration 022, snake_case rather
    than camelCase — the same split every other synced table has.

    UNIQUE(employee_id, stat_date, hour) mirrors the Postgres constraint and is
    what makes this aggregator idempotent: re-running over the same hour
    replaces the row rather than appending a second one.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employee_hour_stats (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id               TEXT,
                employee_id          TEXT NOT NULL,
                stat_date            TEXT NOT NULL,
                hour                 INTEGER NOT NULL,
                present_minutes      INTEGER NOT NULL DEFAULT 0,
                desk_minutes         INTEGER NOT NULL DEFAULT 0,
                seated_minutes       INTEGER NOT NULL DEFAULT 0,
                unknown_minutes      INTEGER NOT NULL DEFAULT 0,
                away_from_desk_count INTEGER NOT NULL DEFAULT 0,
                binding_confidence   REAL    NOT NULL DEFAULT 0,
                synced_at            TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE (employee_id, stat_date, hour)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ehs_org_date "
            "ON employee_hour_stats (org_id, stat_date)"
        ))
        # The Postgres sync scans for unsynced rows; without this it is a full
        # table scan on every pass. Same reasoning as ix_eds_unsynced.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ehs_unsynced "
            "ON employee_hour_stats (synced_at)"
        ))


def _cap_minutes(seconds: float) -> int:
    """
    Seconds to whole minutes, capped at an hour.

    The cap is not cosmetic. If two cameras both observe the same person in the
    same hour, their spans are summed and can exceed 3600 s — real overlap, not
    a bug, but "73 minutes in an hour" is not a figure anyone can read. Capping
    keeps the row honest and satisfies the Postgres CHECK; the daily rollup,
    which does not cap, remains the place to see total observed time.
    """
    return int(min(max(seconds, 0.0), SECONDS_PER_HOUR) // 60)


def analyse_hours(samples: list, desk_zones: set) -> dict:
    """
    Fold one employee's day of samples into per-hour buckets.

    `samples` is the same shape `analyse_timeline()` takes — dicts with
    observed_at, zone_id, posture, confidence — already filtered to one person
    and sorted ascending.

    Returns {hour: {present, desk, seated, exits, confidence}}.

    A SPAN IS CREDITED TO THE HOUR IT STARTS IN

    A five-second gap straddling 09:59:58 → 10:00:03 could be split across two
    hours. It is not: it is credited whole to hour 9. Splitting would be more
    precise by two seconds and would cost a reader nothing they could perceive,
    while making every figure here harder to reason about. At a 5-second sample
    interval the worst-case error is 5 seconds per hour boundary.
    """
    buckets = defaultdict(lambda: {
        "present": 0.0, "desk": 0.0, "seated": 0.0,
        "conf_sum": 0.0, "conf_n": 0, "exits": 0,
    })

    prev_t = None
    prev_at_desk = None

    for s in samples:
        t = s["observed_at"]
        hour = t.hour
        b = buckets[hour]

        at_desk = bool(desk_zones) and s.get("zone_id") in desk_zones
        seated = str(s.get("posture") or "").upper() == "SITTING"

        # Confidence is per-sample and averaged per hour, so an hour where the
        # system was unsure reads as unsure even if the rest of the day was
        # confident.
        b["conf_sum"] += float(s.get("confidence") or 0.0)
        b["conf_n"] += 1

        if prev_t is not None:
            gap = (t - prev_t).total_seconds()
            # A gap larger than the cap means the pipeline stopped — a paused
            # upload, a camera drop. Crediting it as presence would invent
            # time nobody was observed.
            if 0 < gap <= MAX_SAMPLE_GAP_SECONDS:
                b["present"] += gap
                if at_desk:
                    b["desk"] += gap
                    if seated:
                        b["seated"] += gap

            # A chair exit is counted in the hour the person LEFT, which is the
            # hour a reader looking for "when did they get up" expects to find
            # it. The 90-second debounce lives in the daily aggregator; at hour
            # resolution what matters is the transition, and duplicating the
            # debounce here would let the two disagree.
            if prev_at_desk is True and at_desk is False:
                buckets[prev_t.hour]["exits"] += 1

        prev_t = t
        prev_at_desk = at_desk

    out = {}
    for hour, b in buckets.items():
        out[hour] = {
            "present_minutes": _cap_minutes(b["present"]),
            "desk_minutes": _cap_minutes(b["desk"]),
            "seated_minutes": _cap_minutes(b["seated"]),
            "away_from_desk_count": b["exits"],
            "binding_confidence": round(b["conf_sum"] / b["conf_n"], 4) if b["conf_n"] else 0.0,
        }
    return out


def aggregate_hours_sync(day: date = None, org_id: str = None) -> dict:
    """
    Roll one day's `identity_events` into `employee_hour_stats`.

    Mirrors `aggregate_day_sync()` decision for decision, including Step 14's
    confidence floor: a row that names somebody below IDENTITY_MIN_CONFIDENCE
    is treated as UNATTRIBUTED, not as weak evidence about that person. Those
    minutes land in `unknown_minutes` for the hour they occurred in, so the
    timeline shows WHEN the system was unsure rather than only how much.

    Idempotent: upserts on (employee_id, stat_date, hour).
    """
    ensure_hour_stats_table()
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
            return {"employees": 0, "rows_read": 0, "hours": 0,
                    "day": day.isoformat()}

        desk_by_employee, _break_zones = _load_zone_config(session, org_id)

        by_employee = defaultdict(list)
        # Unattributed time, per org per hour. Keyed by camera for the span
        # arithmetic so two cameras' unknown people do not merge into one
        # implausibly continuous presence.
        unknown_by_org_hour = defaultdict(float)
        prev_unknown_t = {}
        low_confidence_rows = 0

        for r in rows:
            t = _parse(r["observed_at"])
            if t is None:
                continue

            confident = (r["employee_id"]
                         and float(r["confidence"] or 0.0) >= IDENTITY_MIN_CONFIDENCE)
            if r["employee_id"] and not confident:
                low_confidence_rows += 1

            if confident:
                by_employee[(r["org_id"], r["employee_id"])].append({
                    "observed_at": t,
                    "zone_id": r["zone_id"],
                    "posture": r["posture"],
                    "confidence": r["confidence"],
                })
            else:
                key = (r["org_id"], r["camera_id"])
                prev = prev_unknown_t.get(key)
                if prev is not None:
                    gap = (t - prev).total_seconds()
                    if 0 < gap <= MAX_SAMPLE_GAP_SECONDS:
                        unknown_by_org_hour[(r["org_id"], prev.hour)] += gap
                prev_unknown_t[key] = t

        written = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        employees_seen = set()

        for (org, employee_id), samples in by_employee.items():
            desks = desk_by_employee.get(employee_id, set())
            hours = analyse_hours(samples, desks)
            employees_seen.add(employee_id)

            for hour, stats in sorted(hours.items()):
                # The unattributed residue for this hour is attached to every
                # employee observed in it, the same way the daily rollup
                # attaches it. It is a property of the hour, not of the person:
                # "somebody was here and we could not say who".
                unknown = _cap_minutes(unknown_by_org_hour.get((org, hour), 0.0))

                session.execute(text("""
                    INSERT INTO employee_hour_stats (
                        org_id, employee_id, stat_date, hour,
                        present_minutes, desk_minutes, seated_minutes,
                        unknown_minutes, away_from_desk_count,
                        binding_confidence, created_at, updated_at
                    ) VALUES (
                        :org_id, :employee_id, :stat_date, :hour,
                        :present, :desk, :seated,
                        :unknown, :exits,
                        :confidence, :now, :now
                    )
                    ON CONFLICT (employee_id, stat_date, hour) DO UPDATE SET
                        present_minutes      = excluded.present_minutes,
                        desk_minutes         = excluded.desk_minutes,
                        seated_minutes       = excluded.seated_minutes,
                        unknown_minutes      = excluded.unknown_minutes,
                        away_from_desk_count = excluded.away_from_desk_count,
                        binding_confidence   = excluded.binding_confidence,
                        updated_at           = excluded.updated_at,
                        -- Re-queue for sync: the figures changed, so the copy
                        -- already in Postgres is now stale.
                        synced_at            = NULL
                """), {
                    "org_id": org,
                    "employee_id": employee_id,
                    "stat_date": day.isoformat(),
                    "hour": hour,
                    "present": stats["present_minutes"],
                    "desk": stats["desk_minutes"],
                    "seated": stats["seated_minutes"],
                    "unknown": unknown,
                    "exits": stats["away_from_desk_count"],
                    "confidence": stats["binding_confidence"],
                    "now": now_iso,
                })
                written += 1

        session.commit()

        if low_confidence_rows:
            logger.info(
                f"hourly: {low_confidence_rows} observation(s) below the "
                f"{IDENTITY_MIN_CONFIDENCE} floor, counted as unattributed")

        return {
            "employees": len(employees_seen),
            "rows_read": len(rows),
            "hours": written,
            "low_confidence_rows": low_confidence_rows,
            "confidence_floor": IDENTITY_MIN_CONFIDENCE,
            "day": day.isoformat(),
        }

    except Exception as e:
        session.rollback()
        logger.error(f"hourly aggregation failed: {e}")
        return {"employees": 0, "rows_read": 0, "hours": 0, "error": str(e)}
    finally:
        session.close()


def sync_hours_to_postgres_sync(limit: int = 2000) -> dict:
    """
    Push unsynced local hour stats into Postgres `employee_hour_stats`.

    Follows `employee_aggregator.sync_to_postgres_sync()` exactly, including
    validating each employee id against Postgres before the write — a stale
    local id would otherwise fail the foreign key and take the whole batch
    down with it.

    The default limit is higher than the daily sync's because there are up to
    24 rows per person per day rather than one.
    """
    if not service_role_configured():
        return {"synced": 0, "unmapped": 0, "reason": "no service-role key configured"}

    import json
    import urllib.request

    from .employee_aggregator import _employee_exists

    ensure_hour_stats_table()
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    session = SessionLocal()
    try:
        rows = [dict(r._mapping) for r in session.execute(text("""
            SELECT * FROM employee_hour_stats
             WHERE synced_at IS NULL AND org_id IS NOT NULL
             ORDER BY stat_date ASC, hour ASC
             LIMIT :limit
        """), {"limit": limit})]
        if not rows:
            return {"synced": 0, "unmapped": 0}

        payload, synced_ids, unmapped = [], [], 0
        for row in rows:
            if not _employee_exists(base, key, row["employee_id"]):
                unmapped += 1
                continue
            payload.append({
                "orgId": row["org_id"],
                "employeeId": row["employee_id"],
                "statDate": row["stat_date"],
                "hour": row["hour"],
                "presentMinutes": row["present_minutes"],
                "deskMinutes": row["desk_minutes"],
                "seatedMinutes": row["seated_minutes"],
                "unknownMinutes": row["unknown_minutes"],
                "awayFromDeskCount": row["away_from_desk_count"],
                "bindingConfidence": row["binding_confidence"],
                # Prisma's @updatedAt is client-side and the column has no DB
                # default — the trap documented in 008.
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            synced_ids.append(row["id"])

        if not payload:
            return {"synced": 0, "unmapped": unmapped}

        request = urllib.request.Request(
            f"{base}/rest/v1/employee_hour_stats"
            "?on_conflict=employeeId,statDate,hour",
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
                text("UPDATE employee_hour_stats SET synced_at = :now WHERE id = :id"),
                {"now": stamp, "id": row_id},
            )
        session.commit()
        return {"synced": len(synced_ids), "unmapped": unmapped}

    except Exception as e:
        session.rollback()
        logger.error(f"employee hour stats sync failed: {e}")
        return {"synced": 0, "unmapped": 0, "error": str(e)}
    finally:
        session.close()


async def aggregate_hours(**kwargs) -> dict:
    """Async wrapper, matching the daily aggregator's entry point."""
    import asyncio
    return await asyncio.to_thread(aggregate_hours_sync, **kwargs)


async def sync_hours_to_postgres(**kwargs) -> dict:
    import asyncio
    return await asyncio.to_thread(sync_hours_to_postgres_sync, **kwargs)
