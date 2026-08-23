-- VisionWorks — covering index for the dashboard aggregations
--
-- THE PROBLEM, MEASURED
--
-- `dashboard_overview()` over a 90-day window scans ~413,000 buckets. Warm,
-- that is ~1.4 s. Cold — immediately after a seed, before any of those pages
-- are in shared buffers — it read 827,510 buffers and exceeded Supabase's
-- statement timeout, so the FIRST person to open a 90-day range got an error
-- and everyone after them got a working page. An intermittent failure that
-- only hits the first user is the worst kind to debug.
--
-- WHY A COVERING INDEX RATHER THAN A BIGGER TIMEOUT
--
-- Raising the timeout would hide the symptom and keep the heap reads. The
-- existing `(orgId, bucketStart)` index locates the rows but every aggregate
-- column then has to be fetched from the heap — which is where the 827k buffer
-- reads came from.
--
-- INCLUDE-ing the aggregated columns makes the scan INDEX ONLY: the planner
-- reads the index and never touches the table. Verified after creating it:
--
--   ->  Index Only Scan using ix_zms_org_bucket_covering
--         Index Cond: ("bucketStart" >= (now() - '90 days'::interval))
--
-- The columns are in INCLUDE rather than the key because they are never
-- filtered or sorted on — only summed. Keeping them out of the key leaves the
-- index navigable by (orgId, bucketStart) while still carrying the payload.
--
-- COST: this index is large, because it duplicates ten columns across ~826,000
-- rows. That is the trade — disk for a dashboard that never times out on first
-- load. If storage ever matters more than first-load latency, drop it: every
-- query still works, just slower when cold.
--
-- Apply AFTER 011_retention_schedule.sql. `ANALYZE` afterwards so the planner
-- has statistics for it immediately rather than after autovacuum notices.

CREATE INDEX IF NOT EXISTS ix_zms_org_bucket_covering
  ON public.zone_minute_stats ("orgId", "bucketStart")
  INCLUDE (
    "zoneId",
    "uniqueTrackCount",
    "sittingFrames",
    "standingFrames",
    "walkingFrames",
    "sampleFrames",
    "avgActivityScore",
    "totalDwellSeconds",
    "occupancyAvg",
    "occupancyMax"
  );

ANALYZE public.zone_minute_stats;
