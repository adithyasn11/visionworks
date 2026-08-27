# backend/app/db/minute_aggregator.py
"""
The bridge from raw telemetry to the analytics schema.

WHAT THIS DOES

`activity_logs` holds one sampled row per tracked person every ~5 seconds (plus
one on every posture change). That is the right shape for capture and the wrong
shape for analytics: it is per-person, it carries coordinates, and it grows
without bound. `zone_minute_stats` is the shape analytics wants — one row per
zone per minute, counts only.

This module converts the first into the second.

═══════════════════════════════════════════════════════════════════════════
 ANONYMITY IS STRUCTURAL HERE, NOT A POLICY PROMISE
═══════════════════════════════════════════════════════════════════════════

A minute bucket contains NO track id, NO coordinates, and NO reference to a
person. `uniqueTrackCount` is a COUNT — the ids are used to compute it and then
discarded inside this function; they never reach a column.

Once a minute closes, individual movement is mathematically unrecoverable. The
database physically cannot answer "what did this person do today", because no
row refers to a person. That is a property of the schema, not of a promise, and
it is the reason this system is deployable in a workplace at all.

**Do not add a person reference to the output.** Not a track id, not a hashed
track id, not a coordinate, not a first-seen timestamp precise enough to
re-identify. If a future feature seems to need one, it needs a different table
with a different retention policy and a different conversation.

═══════════════════════════════════════════════════════════════════════════
 THE THREE THINGS THAT ARE EASY TO GET WRONG
═══════════════════════════════════════════════════════════════════════════

1. DWELL IS CUMULATIVE PER TRACK, NOT PER ROW.
   `activity_logs.dwell_duration_seconds` is the running total for that track
   (0, 5, 10, 15 ...). Summing the column would count the same seconds many
   times over — the same trap `analytics.py` documents for its zone chart.
   `totalDwellSeconds` here is PRESENCE WITHIN THE MINUTE: for each track, the
   span from its first to its last sample inside the bucket, clamped to 60.

2. FRAME COUNTS ARE SAMPLES, NOT FRAMES.
   The writer samples every ~5s, so a bucket holds ~12 samples per person, not
   480. `sampleFrames` is therefore the number of SAMPLES in the bucket. The
   ratios (sitting/sample) are still correct, which is what the column is for;
   the absolute number is simply not comparable with the seeded demo rows,
   which were generated at 8 fps. The schema comment already says raw counts
   are not comparable across cameras with different fpsTarget — this is that
   same caveat, and it is why every consumer divides by `sampleFrames`.

3. AWAY IS COUNTED BUT NOT CATEGORISED.
   `zms_posture_frames_within_sample` requires
   sitting + standing + walking <= sampleFrames. AWAY samples are real
   observations, so they belong in `sampleFrames`, but they are not one of the
   three posture columns. That is precisely why the constraint is `<=` and not
   `=`, and why the verification reads "≈" rather than "==".

═══════════════════════════════════════════════════════════════════════════
 WHERE BUCKETS ARE WRITTEN
═══════════════════════════════════════════════════════════════════════════

SQLite `zone_minute_stats` always. Postgres additionally, when a service-role
key is configured — and only then, because `zone_minute_stats` has no INSERT
policy at all: measured data is read-only to every browser client by design, so
the only key that can write it is the one that bypasses RLS. That key must
never reach the browser, which is why it lives in backend/.env and is read
here rather than passed in from anywhere near a request.
"""

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db.database import SessionLocal, engine

logger = logging.getLogger(__name__)

# A bucket is only written once it can no longer receive new samples. The
# pipeline writes a row up to ~5s after the moment it describes, so closing a
# minute the instant it ends would drop the tail. Two minutes of slack is
# comfortably beyond that and still keeps the dashboard near-live.
BUCKET_SETTLE_SECONDS = 120

# smallint columns. A single zone holding more than this is not a real reading;
# clamping keeps a runaway tracker from failing the whole batch on an overflow
# that has nothing to do with the other buckets.
_SMALLINT_MAX = 32767

_VALID_POSTURES = ("SITTING", "STANDING", "WALKING")


def _floor_to_minute(value: datetime) -> datetime:
    """Truncate to the minute. `zms_bucket_truncated_to_minute` requires it."""
    return value.replace(second=0, microsecond=0)


def _parse_timestamp(raw) -> datetime | None:
    """
    SQLite hands back either a datetime or a string, depending on the driver
    and on how the row was written. Normalise, and treat naive values as UTC —
    the pipeline writes `datetime.utcnow()`, so they always are.
    """
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_bucket_table() -> None:
    """
    Creates the local `zone_minute_stats` mirror.

    Same column names as the Postgres table so the two can be compared directly
    and so the Postgres writer can hand rows straight across without renaming.
    `org_id`/`camera_id`/`zone_id` are TEXT here because SQLite telemetry
    carries text ids; the Postgres writer resolves them to UUIDs.

    UNIQUE(zone_id, bucket_start) mirrors `@@unique([zoneId, bucketStart])` and
    is what makes the aggregator idempotent — re-running over the same window
    updates rather than duplicates.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS zone_minute_stats (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id              TEXT,
                camera_id           TEXT NOT NULL,
                zone_id             TEXT NOT NULL,
                bucket_start        TEXT NOT NULL,
                occupancy_max       INTEGER NOT NULL,
                occupancy_avg       REAL    NOT NULL,
                occupancy_min       INTEGER NOT NULL,
                sitting_frames      INTEGER NOT NULL DEFAULT 0,
                standing_frames     INTEGER NOT NULL DEFAULT 0,
                walking_frames      INTEGER NOT NULL DEFAULT 0,
                sample_frames       INTEGER NOT NULL DEFAULT 0,
                avg_activity_score  REAL    NOT NULL DEFAULT 0,
                total_dwell_seconds INTEGER NOT NULL DEFAULT 0,
                unique_track_count  INTEGER NOT NULL DEFAULT 0,
                synced_at           TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE (zone_id, bucket_start)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_zms_org_bucket "
            "ON zone_minute_stats (org_id, bucket_start)"
        ))
        # The Postgres sync scans for unsynced rows; without this it is a full
        # table scan every 60 seconds.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_zms_unsynced "
            "ON zone_minute_stats (synced_at)"
        ))


def _aggregate_rows(rows) -> list[dict]:
    """
    Fold raw samples into per-(zone, minute) buckets.

    Track ids are used HERE — to count distinct people and to measure each
    person's presence span — and are dropped before returning. Nothing
    identifying crosses this function's boundary.
    """
    # (org_id, camera_id, zone_id, bucket_start) -> accumulator
    buckets: dict[tuple, dict] = {}

    for row in rows:
        moment = _parse_timestamp(row["timestamp"])
        if moment is None:
            continue

        key = (row["org_id"], row["camera_id"], row["zone_id"], _floor_to_minute(moment))
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {
                "sitting": 0, "standing": 0, "walking": 0, "samples": 0,
                "score_total": 0.0, "score_count": 0,
                # track_id -> [first_seen, last_seen] within this minute only.
                # Local to this loop; never written anywhere.
                "spans": defaultdict(lambda: [moment, moment]),
                # Per-second occupancy, for max/avg/min. A dict of
                # second -> set of track ids present; the sets are discarded.
                "per_second": defaultdict(set),
            }

        bucket["samples"] += 1

        posture = str(row["posture_state"] or "").upper()
        if posture == "SITTING":
            bucket["sitting"] += 1
        elif posture == "STANDING":
            bucket["standing"] += 1
        elif posture == "WALKING":
            bucket["walking"] += 1
        # AWAY (or anything unrecognised) counts toward `samples` only — see
        # the module docstring on zms_posture_frames_within_sample.

        score = row["activity_score"]
        if score is not None:
            bucket["score_total"] += float(score)
            bucket["score_count"] += 1

        track_id = row["track_id"]
        if track_id is not None:
            span = bucket["spans"][track_id]
            if moment < span[0]:
                span[0] = moment
            if moment > span[1]:
                span[1] = moment
            bucket["per_second"][moment.second].add(track_id)

    results = []
    for (org_id, camera_id, zone_id, bucket_start), acc in buckets.items():
        # ── Occupancy ──
        # Measured only across seconds that actually carry a sample. A minute
        # sampled at 5s intervals has ~12 populated seconds; treating the other
        # 48 as "zero people" would report an occupancyMin of 0 for a room
        # somebody sat in continuously, which is false.
        counts = [len(ids) for ids in acc["per_second"].values()]
        occupancy_max = max(counts) if counts else 0
        occupancy_min = min(counts) if counts else 0
        occupancy_avg = (sum(counts) / len(counts)) if counts else 0.0

        # ── Presence, not cumulative dwell ──
        # Each track's span inside this minute. A track seen only once has a
        # zero-length span; it was present for at least one sampling interval,
        # but claiming a duration the data does not show would be inventing it.
        dwell_seconds = 0
        for first_seen, last_seen in acc["spans"].values():
            dwell_seconds += int(min(60, max(0, (last_seen - first_seen).total_seconds())))

        # `zms_dwell_within_minute`: totalDwellSeconds <= 60 * max(occupancyMax, 1).
        # Clamp rather than risk rejecting the batch — the cap is the physical
        # limit, so a value above it was wrong anyway.
        dwell_seconds = min(dwell_seconds, 60 * max(occupancy_max, 1))

        unique_tracks = len(acc["spans"])
        avg_score = (acc["score_total"] / acc["score_count"]) if acc["score_count"] else 0.0

        results.append({
            "org_id": org_id,
            "camera_id": camera_id,
            "zone_id": zone_id,
            "bucket_start": bucket_start,
            "occupancy_max": min(occupancy_max, _SMALLINT_MAX),
            "occupancy_avg": round(occupancy_avg, 4),
            "occupancy_min": min(occupancy_min, _SMALLINT_MAX),
            "sitting_frames": acc["sitting"],
            "standing_frames": acc["standing"],
            "walking_frames": acc["walking"],
            "sample_frames": acc["samples"],
            # `zms_activity_score_range` is 0..100; the pipeline already clips,
            # but a stored value slightly outside would fail the whole batch.
            "avg_activity_score": round(max(0.0, min(100.0, avg_score)), 2),
            "total_dwell_seconds": dwell_seconds,
            "unique_track_count": min(unique_tracks, _SMALLINT_MAX),
        })

    return results


def aggregate_window_sync(
    since: datetime | None = None,
    until: datetime | None = None,
    settle_seconds: int = BUCKET_SETTLE_SECONDS,
) -> dict:
    """
    Build buckets for closed minutes and upsert them. Runs in a worker thread.

    Only reads rows carrying an `org_id`: telemetry with no tenant belongs to
    nobody and must not become an org-scoped analytics row. That is the same
    rule Step 2 applied to every read path.

    Idempotent by UNIQUE(zone_id, bucket_start) — re-running over a window
    recomputes and replaces, so a retry after a crash cannot double-count.
    """
    ensure_bucket_table()

    now = datetime.now(timezone.utc)

    # Callers may pass naive datetimes (a test, or a caller that built one from
    # a date string). The pipeline writes UTC throughout, so treat naive as UTC
    # rather than raising — comparing naive to aware is a TypeError, and this
    # function must not blow up on an unremarkable argument.
    def _as_utc(value):
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)

    since, until = _as_utc(since), _as_utc(until)

    # Never aggregate a minute that can still receive samples.
    horizon = _floor_to_minute(now - timedelta(seconds=settle_seconds))
    if until is None or until > horizon:
        until = horizon
    if since is None:
        since = until - timedelta(hours=24)

    if since >= until:
        return {"buckets": 0, "samples": 0, "skipped_no_org": 0, "window": None}

    session = SessionLocal()
    try:
        result = session.execute(
            text("""
                SELECT timestamp, camera_id, zone_id, track_id, posture_state,
                       activity_score, org_id
                FROM activity_logs
                WHERE org_id IS NOT NULL
                  AND timestamp >= :since
                  AND timestamp <  :until
            """),
            {"since": since.replace(tzinfo=None).isoformat(sep=" "),
             "until": until.replace(tzinfo=None).isoformat(sep=" ")},
        )
        rows = [dict(r._mapping) for r in result]

        skipped = session.execute(
            text("""
                SELECT count(*) FROM activity_logs
                WHERE org_id IS NULL
                  AND timestamp >= :since AND timestamp < :until
            """),
            {"since": since.replace(tzinfo=None).isoformat(sep=" "),
             "until": until.replace(tzinfo=None).isoformat(sep=" ")},
        ).scalar() or 0

        if not rows:
            return {"buckets": 0, "samples": 0, "skipped_no_org": int(skipped),
                    "window": (since.isoformat(), until.isoformat())}

        buckets = _aggregate_rows(rows)
        stamp = now.isoformat()

        for bucket in buckets:
            session.execute(
                text("""
                    INSERT INTO zone_minute_stats (
                        org_id, camera_id, zone_id, bucket_start,
                        occupancy_max, occupancy_avg, occupancy_min,
                        sitting_frames, standing_frames, walking_frames,
                        sample_frames, avg_activity_score,
                        total_dwell_seconds, unique_track_count,
                        synced_at, created_at, updated_at
                    ) VALUES (
                        :org_id, :camera_id, :zone_id, :bucket_start,
                        :occupancy_max, :occupancy_avg, :occupancy_min,
                        :sitting_frames, :standing_frames, :walking_frames,
                        :sample_frames, :avg_activity_score,
                        :total_dwell_seconds, :unique_track_count,
                        NULL, :now, :now
                    )
                    ON CONFLICT (zone_id, bucket_start) DO UPDATE SET
                        occupancy_max       = excluded.occupancy_max,
                        occupancy_avg       = excluded.occupancy_avg,
                        occupancy_min       = excluded.occupancy_min,
                        sitting_frames      = excluded.sitting_frames,
                        standing_frames     = excluded.standing_frames,
                        walking_frames      = excluded.walking_frames,
                        sample_frames       = excluded.sample_frames,
                        avg_activity_score  = excluded.avg_activity_score,
                        total_dwell_seconds = excluded.total_dwell_seconds,
                        unique_track_count  = excluded.unique_track_count,
                        -- A recomputed bucket must be re-synced, so clear the
                        -- marker rather than leaving Postgres holding the old
                        -- numbers.
                        synced_at           = NULL,
                        updated_at          = excluded.updated_at
                """),
                {**bucket, "bucket_start": bucket["bucket_start"].isoformat(), "now": stamp},
            )

        session.commit()
        return {
            "buckets": len(buckets),
            "samples": len(rows),
            "skipped_no_org": int(skipped),
            "window": (since.isoformat(), until.isoformat()),
        }

    except Exception as e:
        session.rollback()
        logger.error(f"Minute aggregation failed: {e}")
        return {"buckets": 0, "samples": 0, "skipped_no_org": 0, "error": str(e)}
    finally:
        session.close()


async def aggregate_window(**kwargs) -> dict:
    """
    aggregate_window_sync() off the event loop.

    SQLAlchemy is synchronous; running this inline in an async handler would
    stall frame delivery for every other session — the same reason
    activity_writer.py pushes its writes through a thread.
    """
    return await asyncio.to_thread(aggregate_window_sync, **kwargs)


async def aggregate_after_session(camera_id: str, org_id: str | None) -> dict:
    """
    Called when a processing session ends.

    Waits out the settle window first, so the last samples the session wrote
    are inside a closed minute rather than being missed and only picked up by
    the next timer tick.

    Never raises: aggregation is a side effect of a session that has already
    finished successfully, and failing it must not surface as a session error.
    """
    if org_id is None:
        # Unattributed telemetry produces no buckets, by design.
        return {"buckets": 0, "samples": 0, "reason": "no organisation"}

    try:
        await asyncio.sleep(BUCKET_SETTLE_SECONDS)
        summary = await aggregate_window()
        logger.info(
            f"Aggregated after session on {camera_id}: "
            f"{summary.get('buckets', 0)} buckets from {summary.get('samples', 0)} samples"
        )
        return summary
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Post-session aggregation failed for {camera_id}: {e}")
        return {"buckets": 0, "samples": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
#  POSTGRES SYNC
# ═══════════════════════════════════════════════════════════════════════════

def service_role_configured() -> bool:
    """
    Is a service-role key present?

    `zone_minute_stats` has NO INSERT policy — measured data is read-only to
    every browser client by design, so the only credential that can write it is
    the one that bypasses RLS. Without it the aggregator still produces correct
    buckets locally; they simply do not reach Postgres.
    """
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return bool(key) and "your-" not in key


def _resolve_ids(org_id: str, camera_name: str, zone_name: str, cache: dict):
    """
    Map SQLite text ids to Postgres UUIDs, BY NAME, within the row's org.

    SQLite telemetry carries `camera_id = 'live_webcam'` and
    `zone_id = 'workstation_01'`; Postgres wants real UUIDs with valid foreign
    keys. Nothing maps the two, so the match is on `name`.

    Returns None when either side does not resolve. Unmapped rows are COUNTED
    and reported, never invented: creating a camera or zone here would
    fabricate configuration the user never drew — including zone polygons that
    do not exist — and a bucket carrying the wrong zoneId is worse than no
    bucket at all.
    """
    import json
    import urllib.parse
    import urllib.request

    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    def lookup(table: str, name: str, extra: dict):
        cache_key = (table, org_id, name)
        if cache_key in cache:
            return cache[cache_key]
        params = {"select": "id", "orgId": f"eq.{org_id}", "name": f"eq.{name}", "limit": "1"}
        params.update(extra)
        url = f"{base}/rest/v1/{table}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8") or "[]")
            resolved = data[0]["id"] if data else None
        except Exception as e:
            logger.warning(f"Lookup failed for {table}.{name}: {type(e).__name__}")
            resolved = None
        cache[cache_key] = resolved
        return resolved

    camera_uuid = lookup("cameras", camera_name, {})
    if not camera_uuid:
        return None
    zone_uuid = lookup("zones", zone_name, {"cameraId": f"eq.{camera_uuid}"})
    if not zone_uuid:
        return None
    return camera_uuid, zone_uuid


def sync_to_postgres_sync(limit: int = 500) -> dict:
    """
    Push unsynced local buckets into Postgres `zone_minute_stats`.

    Upserts on `(zoneId, bucketStart)` — the same unique key the local table
    uses — so a retry replaces rather than duplicates.

    Rows whose camera/zone cannot be resolved are left unsynced and counted as
    `unmapped`. They stay in SQLite, so drawing the matching zone later and
    re-running the sync picks them up; nothing is lost and nothing is guessed.
    """
    if not service_role_configured():
        return {"synced": 0, "unmapped": 0, "reason": "no service-role key configured"}

    import json
    import urllib.request

    ensure_bucket_table()
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    session = SessionLocal()
    try:
        rows = [dict(r._mapping) for r in session.execute(
            text("""
                SELECT * FROM zone_minute_stats
                WHERE synced_at IS NULL AND org_id IS NOT NULL
                ORDER BY bucket_start ASC
                LIMIT :limit
            """),
            {"limit": limit},
        )]
        if not rows:
            return {"synced": 0, "unmapped": 0}

        cache: dict = {}
        payload, synced_ids, unmapped, skipped_ids = [], [], 0, []

        for row in rows:
            # TRANSIT_ZONE is the synthetic bucket for "inside the frame but not
            # inside any drawn zone" (see SpatialEngine.check_zone_containment).
            # It is not a place the user configured and no Postgres `zones` row
            # will ever carry that name, so retrying it every 60s would warn
            # forever about something that can never resolve. Stamp it synced to
            # retire it: the occupancy it represents is still counted locally,
            # it simply has no zone to be attributed to upstream.
            if row["zone_id"] == "TRANSIT_ZONE":
                skipped_ids.append(row["id"])
                continue

            resolved = _resolve_ids(row["org_id"], row["camera_id"], row["zone_id"], cache)
            if resolved is None:
                unmapped += 1
                continue
            camera_uuid, zone_uuid = resolved
            payload.append({
                "orgId": row["org_id"],
                "cameraId": camera_uuid,
                "zoneId": zone_uuid,
                "bucketStart": row["bucket_start"],
                "occupancyMax": row["occupancy_max"],
                "occupancyAvg": row["occupancy_avg"],
                "occupancyMin": row["occupancy_min"],
                "sittingFrames": row["sitting_frames"],
                "standingFrames": row["standing_frames"],
                "walkingFrames": row["walking_frames"],
                "sampleFrames": row["sample_frames"],
                "avgActivityScore": row["avg_activity_score"],
                "totalDwellSeconds": row["total_dwell_seconds"],
                "uniqueTrackCount": row["unique_track_count"],
                # Prisma's @updatedAt is client-side, and the column has no DB
                # default — the trap that broke three write paths in Step 3.
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            synced_ids.append(row["id"])

        def retire_skipped(stamp: str) -> None:
            """Stamp TRANSIT_ZONE buckets synced so they stop being re-read."""
            for row_id in skipped_ids:
                session.execute(
                    text("UPDATE zone_minute_stats SET synced_at = :now WHERE id = :id"),
                    {"now": stamp, "id": row_id},
                )

        if not payload:
            # Still retire the transit rows — otherwise a batch containing only
            # those would loop on them forever.
            if skipped_ids:
                retire_skipped(datetime.now(timezone.utc).isoformat())
                session.commit()
            return {"synced": 0, "unmapped": unmapped, "skipped_transit": len(skipped_ids)}

        request = urllib.request.Request(
            f"{base}/rest/v1/zone_minute_stats?on_conflict=zoneId,bucketStart",
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
                text("UPDATE zone_minute_stats SET synced_at = :now WHERE id = :id"),
                {"now": stamp, "id": row_id},
            )
        retire_skipped(stamp)
        session.commit()
        return {
            "synced": len(synced_ids),
            "unmapped": unmapped,
            "skipped_transit": len(skipped_ids),
        }

    except Exception as e:
        session.rollback()
        logger.error(f"Postgres bucket sync failed: {e}")
        return {"synced": 0, "unmapped": 0, "error": str(e)}
    finally:
        session.close()


async def sync_to_postgres(**kwargs) -> dict:
    """sync_to_postgres_sync() off the event loop."""
    return await asyncio.to_thread(sync_to_postgres_sync, **kwargs)


def unsynced_bucket_count_sync(org_id: str | None = None) -> int:
    """
    How many local buckets have never reached Postgres.

    This is the number that was silently zero-looking on the dashboard while
    actually being nonzero in SQLite: `synced_at IS NULL` forever, with no
    surface anywhere that showed it. Exposed so a caller (an admin endpoint,
    a health page) can show it rather than the sync failing invisibly.
    """
    ensure_bucket_table()
    session = SessionLocal()
    try:
        query = "SELECT count(*) FROM zone_minute_stats WHERE synced_at IS NULL"
        params = {}
        if org_id is not None:
            query += " AND org_id = :org_id"
            params["org_id"] = org_id
        return int(session.execute(text(query), params).scalar() or 0)
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  BACKGROUND TIMER
# ═══════════════════════════════════════════════════════════════════════════

AGGREGATE_INTERVAL_SECONDS = 60

# Below this, an unmapped bucket most likely means "the zone was drawn a
# minute after this bucket closed" and will resolve on its own next tick.
# Above it, something is structurally wrong (no camera of that name was ever
# registered) and staying quiet about it is how this bug went unnoticed the
# first time.
UNMAPPED_ALERT_THRESHOLD = 5


async def run_aggregator_loop(stop_event: asyncio.Event | None = None) -> None:
    """
    Aggregate every 60 seconds, then push to Postgres if a key is configured.

    Started from the FastAPI lifespan. Every iteration is wrapped: a failure
    logs and waits for the next tick rather than killing the loop, because a
    dead aggregator is silent and would look like "no data" rather than
    "aggregation is broken".
    """
    logger.info(
        "Minute aggregator started (%ss interval, %ss settle, Postgres sync %s)",
        AGGREGATE_INTERVAL_SECONDS,
        BUCKET_SETTLE_SECONDS,
        "on" if service_role_configured() else "off — no service-role key",
    )
    while True:
        try:
            if stop_event is not None and stop_event.is_set():
                return
            await asyncio.sleep(AGGREGATE_INTERVAL_SECONDS)

            summary = await aggregate_window()
            if summary.get("buckets"):
                logger.info(
                    f"Aggregated {summary['buckets']} buckets from {summary['samples']} samples"
                )

            # SYNC RUNS EVERY TICK, NOT ONLY WHEN THIS ONE AGGREGATED SOMETHING.
            #
            # THE BUG THIS FIXES. Sync used to sit inside the `if buckets:`
            # branch above. The final minute of a session aggregates on one
            # tick; if that tick's push failed, or the bucket closed after it,
            # the NEXT tick produced no new buckets — so sync never ran and the
            # rows sat unsynced forever. In practice that meant analysis
            # stopped appearing on the dashboard the moment you stopped
            # uploading, which is exactly when a user goes to look at it.
            #
            # Unsynced rows are durable state, not an event, so the trigger has
            # to be "is there anything pending" rather than "did something just
            # happen".
            if service_role_configured():
                pushed = await sync_to_postgres()
                if pushed.get("synced") or pushed.get("unmapped"):
                    logger.info(
                        f"Synced {pushed.get('synced', 0)} buckets to Postgres"
                        f" ({pushed.get('unmapped', 0)} unmapped)"
                    )
                # Buckets left unmapped are invisible to every dashboard —
                # they never reach Postgres, so nothing downstream can even
                # know they exist. A one-off unmapped bucket is normal (a
                # zone drawn after the fact catches up next tick); a bucket
                # left unmapped for a long time means a camera/zone name
                # will never resolve on its own, which is worth a louder
                # signal than the per-row warning inside _resolve_ids.
                if pushed.get("unmapped", 0) >= UNMAPPED_ALERT_THRESHOLD:
                    logger.error(
                        f"{pushed['unmapped']} minute buckets could not be mapped "
                        f"to a registered camera/zone and will stay unsynced until "
                        f"one is registered with a matching name — see "
                        f"minute_aggregator._resolve_ids."
                    )

            # Rules are evaluated on every tick, not only when buckets were
            # written. CAMERA_OFFLINE fires precisely BECAUSE nothing arrived,
            # so gating this on new data would make that rule unable to fire.
            if service_role_configured():
                from app.db.alerts_engine import evaluate_rules
                verdict = await evaluate_rules()
                if verdict.get("fired"):
                    logger.info(
                        f"Alerts: {verdict['fired']} fired, "
                        f"{verdict.get('suppressed', 0)} suppressed by cooldown"
                    )
        except asyncio.CancelledError:
            logger.info("Minute aggregator stopped")
            raise
        except Exception as e:
            logger.error(f"Aggregator tick failed: {e}")
