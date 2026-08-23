-- VisionWorks — dashboard analytics functions
--
-- WHY THESE EXIST (a bug, found by measurement)
--
-- The first Step 6 implementation fetched buckets through PostgREST and folded
-- them in JavaScript. That is wrong at this table's scale, and not subtly:
--
--   .limit(50000) on a 30-day window -> 1,000 rows returned
--   rows that actually match          -> 146,359
--
-- PostgREST caps responses (`max-rows`, 1000 by default on Supabase). The cap
-- is applied AFTER the filter and silently — no error, no warning. So the
-- dashboard would have computed "last 30 days" from the oldest 1,000 minutes
-- of it and displayed the result as fact. A wrong number presented confidently
-- is worse than an error.
--
-- Aggregation therefore happens in Postgres, where the whole window is scanned
-- and only a handful of rows cross the wire.
--
-- SECURITY: these are NOT `SECURITY DEFINER`
--
-- They run as the CALLER, so `zms_select` (`orgId IN (SELECT user_org_ids())`)
-- applies inside the function exactly as it would to a direct query. There is
-- no `WHERE orgId = ...` in any of them and none is needed — writing one would
-- create a second place for tenancy to be got wrong.
--
-- A definer function here would have been a real hole: it would run with the
-- owner's rights and return every tenant's occupancy to anyone who called it.
--
-- `STABLE` lets the planner cache `user_org_ids()` for the statement instead of
-- re-evaluating it per row — the 68x difference documented in 002.
--
-- Apply AFTER 008_uuid_defaults.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  OVERVIEW — the six headline tiles
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.dashboard_overview(
  p_since TIMESTAMPTZ,
  p_until TIMESTAMPTZ
)
RETURNS TABLE (
  has_data              BOOLEAN,
  people                BIGINT,
  zones_active          BIGINT,
  avg_activity          DOUBLE PRECISION,
  sitting_pct           DOUBLE PRECISION,
  standing_pct          DOUBLE PRECISION,
  walking_pct           DOUBLE PRECISION,
  peak_zone_name        TEXT,
  peak_zone_people      BIGINT,
  longest_dwell_minutes DOUBLE PRECISION,
  last_seen             TIMESTAMPTZ,
  sample_frames         BIGINT
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  -- ONE PASS over the window, not two.
  --
  -- The first version had a `totals` CTE and a separate `peak` CTE, each
  -- scanning the same 522,000 rows. Measured: totals 366 ms, peak 1.6 s, but
  -- the combined function 7.5 s — past Supabase's statement timeout, so a
  -- 90-day range failed outright.
  --
  -- Aggregating per zone once and rolling that up is a scan and a small
  -- group-by instead of two full scans.
  WITH per_zone AS (
    SELECT
      "zoneId",
      sum("uniqueTrackCount")                                  AS zone_people,
      sum("sittingFrames")                                     AS sitting,
      sum("standingFrames")                                    AS standing,
      sum("walkingFrames")                                     AS walking,
      sum("sampleFrames")                                      AS samples,
      sum("avgActivityScore" * GREATEST("sampleFrames", 1))    AS score_weighted,
      sum(GREATEST("sampleFrames", 1))                         AS score_weight,
      max("totalDwellSeconds")                                 AS longest_dwell,
      max("bucketStart")                                       AS last_seen,
      count(*)                                                 AS bucket_count
    FROM public.zone_minute_stats
    WHERE "bucketStart" >= p_since AND "bucketStart" <= p_until
    GROUP BY "zoneId"
  ),
  totals AS (
    SELECT
      COALESCE(sum(zone_people), 0)      AS people,
      count(*)                           AS zones_active,
      COALESCE(sum(sitting), 0)          AS sitting,
      COALESCE(sum(standing), 0)         AS standing,
      COALESCE(sum(walking), 0)          AS walking,
      COALESCE(sum(samples), 0)          AS samples,
      COALESCE(sum(score_weighted) / NULLIF(sum(score_weight), 0), 0) AS avg_score,
      COALESCE(max(longest_dwell), 0)    AS longest_dwell,
      max(last_seen)                     AS last_seen,
      COALESCE(sum(bucket_count), 0)     AS bucket_count
    FROM per_zone
  ),
  peak AS (
    -- Reads the already-aggregated per-zone rows (one per zone, not 522k), so
    -- the join to `zones` is trivial.
    SELECT z.name AS zone_name, pz.zone_people
    FROM per_zone pz
    LEFT JOIN public.zones z ON z.id = pz."zoneId"
    ORDER BY pz.zone_people DESC NULLS LAST
    LIMIT 1
  )
  SELECT
    t.bucket_count > 0,
    t.people,
    t.zones_active,
    round(t.avg_score::numeric, 1)::DOUBLE PRECISION,
    -- Ratios divide by the POSTURE total, not sampleFrames: AWAY samples are
    -- counted in sampleFrames but are not one of the three, so dividing by it
    -- would make the three percentages sum to less than 100 for no reason a
    -- reader could see.
    round((100.0 * t.sitting  / NULLIF(t.sitting + t.standing + t.walking, 0))::numeric, 1)::DOUBLE PRECISION,
    round((100.0 * t.standing / NULLIF(t.sitting + t.standing + t.walking, 0))::numeric, 1)::DOUBLE PRECISION,
    round((100.0 * t.walking  / NULLIF(t.sitting + t.standing + t.walking, 0))::numeric, 1)::DOUBLE PRECISION,
    p.zone_name,
    p.zone_people,
    round((t.longest_dwell / 60.0)::numeric, 1)::DOUBLE PRECISION,
    t.last_seen,
    t.samples
  FROM totals t
  LEFT JOIN peak p ON TRUE;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  TREND — the time series behind the charts
-- ═══════════════════════════════════════════════════════════════════════════

-- Granularity is a parameter rather than a guess: a 90-day window at minute
-- resolution is ~130,000 points, which is both unreadable as a chart and slow
-- to ship. The caller passes 'hour' or 'day' based on the span.
CREATE OR REPLACE FUNCTION public.dashboard_trend(
  p_since       TIMESTAMPTZ,
  p_until       TIMESTAMPTZ,
  p_granularity TEXT DEFAULT 'hour'
)
RETURNS TABLE (
  bucket             TIMESTAMPTZ,
  avg_activity_score DOUBLE PRECISION,
  sitting_count      BIGINT,
  standing_count     BIGINT,
  walking_count      BIGINT,
  people             BIGINT,
  avg_occupancy      DOUBLE PRECISION
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT
    date_trunc(
      -- Whitelisted, not interpolated: date_trunc takes a text unit, and
      -- passing a caller's string straight through would be an injection point
      -- in a function that is otherwise safe.
      CASE WHEN p_granularity = 'day' THEN 'day' ELSE 'hour' END,
      "bucketStart"
    ) AS bucket,
    round((
      sum("avgActivityScore" * GREATEST("sampleFrames", 1))
        / NULLIF(sum(GREATEST("sampleFrames", 1)), 0)
    )::numeric, 2)::DOUBLE PRECISION,
    COALESCE(sum("sittingFrames"), 0),
    COALESCE(sum("standingFrames"), 0),
    COALESCE(sum("walkingFrames"), 0),
    COALESCE(sum("uniqueTrackCount"), 0),
    round(avg("occupancyAvg")::numeric, 2)::DOUBLE PRECISION
  FROM public.zone_minute_stats
  WHERE "bucketStart" >= p_since AND "bucketStart" <= p_until
  GROUP BY 1
  ORDER BY 1;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  ZONES — dwell and utilisation per zone
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.dashboard_zones(
  p_since TIMESTAMPTZ,
  p_until TIMESTAMPTZ
)
RETURNS TABLE (
  zone_id        UUID,
  zone_name      TEXT,
  seconds        BIGINT,
  minutes        DOUBLE PRECISION,
  visitors       BIGINT,
  peak_occupancy INT,
  avg_occupancy  DOUBLE PRECISION,
  active_minutes BIGINT
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT
    s."zoneId",
    COALESCE(z.name, 'Unnamed zone'),
    -- Summing totalDwellSeconds across buckets is correct here: Step 5 stores
    -- it as presence WITHIN each minute, not as a cumulative per-track counter.
    -- Summing activity_logs.dwell_duration_seconds would double-count.
    COALESCE(sum(s."totalDwellSeconds"), 0),
    round((COALESCE(sum(s."totalDwellSeconds"), 0) / 60.0)::numeric, 1)::DOUBLE PRECISION,
    COALESCE(sum(s."uniqueTrackCount"), 0),
    COALESCE(max(s."occupancyMax"), 0)::INT,
    round(avg(s."occupancyAvg")::numeric, 2)::DOUBLE PRECISION,
    count(*)
  FROM public.zone_minute_stats s
  LEFT JOIN public.zones z ON z.id = s."zoneId"
  WHERE s."bucketStart" >= p_since AND s."bucketStart" <= p_until
  GROUP BY s."zoneId", z.name
  ORDER BY sum(s."totalDwellSeconds") DESC NULLS LAST;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  COVERAGE — how much history this org actually has
-- ═══════════════════════════════════════════════════════════════════════════

-- Drives the range picker. Offering "last 90 days" to an org whose first bucket
-- is yesterday produces an empty chart and a support question.
CREATE OR REPLACE FUNCTION public.dashboard_coverage()
RETURNS TABLE (first_bucket TIMESTAMPTZ, last_bucket TIMESTAMPTZ, bucket_count BIGINT)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT min("bucketStart"), max("bucketStart"), count(*)
  FROM public.zone_minute_stats;
$$;

-- Callable by signed-in users only. RLS inside still decides what they see, so
-- this grant exposes nothing on its own — it is the outer door, not the lock.
REVOKE ALL ON FUNCTION public.dashboard_overview(TIMESTAMPTZ, TIMESTAMPTZ)          FROM PUBLIC;
REVOKE ALL ON FUNCTION public.dashboard_trend(TIMESTAMPTZ, TIMESTAMPTZ, TEXT)       FROM PUBLIC;
REVOKE ALL ON FUNCTION public.dashboard_zones(TIMESTAMPTZ, TIMESTAMPTZ)             FROM PUBLIC;
REVOKE ALL ON FUNCTION public.dashboard_coverage()                                  FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.dashboard_overview(TIMESTAMPTZ, TIMESTAMPTZ)        TO authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_trend(TIMESTAMPTZ, TIMESTAMPTZ, TEXT)     TO authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_zones(TIMESTAMPTZ, TIMESTAMPTZ)           TO authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_coverage()                                TO authenticated;
