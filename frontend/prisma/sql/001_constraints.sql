-- VisionWorks — CHECK constraints and data-integrity guards
--
-- Prisma's schema language cannot express these, so they are applied as a
-- follow-up migration. Every one of them guards an invariant that, if broken,
-- produces silently wrong analytics rather than a visible error — which is the
-- worst kind of bug in a measurement product.
--
-- Apply AFTER the initial `prisma migrate` has created the tables.

-- ═══════════════════════════════════════════════════════════════════════════
--  ZONES
-- ═══════════════════════════════════════════════════════════════════════════

-- A polygon needs at least three vertices. A two-point "polygon" has zero
-- area, so every point-in-polygon test against it returns false and the zone
-- reports permanently empty — no error, just a dead zone in the dashboard.
ALTER TABLE public.zones
  ADD CONSTRAINT zones_polygon_is_array
  CHECK (jsonb_typeof(polygon) = 'array');

ALTER TABLE public.zones
  ADD CONSTRAINT zones_polygon_min_vertices
  CHECK (jsonb_array_length(polygon) >= 3);

-- Capacity of zero would make utilisation percentages divide by zero.
-- NULL is the correct way to say "capacity does not apply" (a corridor).
ALTER TABLE public.zones
  ADD CONSTRAINT zones_capacity_positive
  CHECK (capacity IS NULL OR capacity > 0);

ALTER TABLE public.zones
  ADD CONSTRAINT zones_utilisation_floor_range
  CHECK ("utilisationFloorPct" IS NULL
         OR ("utilisationFloorPct" >= 0 AND "utilisationFloorPct" <= 100));

-- ═══════════════════════════════════════════════════════════════════════════
--  ZONE_MINUTE_STATS  —  the bucket invariants
-- ═══════════════════════════════════════════════════════════════════════════

-- bucketStart MUST be truncated to the minute. The @@unique([zoneId,
-- bucketStart]) constraint is what makes the aggregation writer idempotent,
-- and it only works if every writer rounds identically. A single write at
-- 10:23:07 instead of 10:23:00 creates a duplicate bucket that double-counts
-- the minute in every downstream chart.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_bucket_truncated_to_minute
  CHECK ("bucketStart" = date_trunc('minute', "bucketStart"));

-- Occupancy is a count of people: never negative, and min <= avg <= max.
-- A violation here means the aggregator's window logic is broken.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_occupancy_non_negative
  CHECK ("occupancyMin" >= 0 AND "occupancyMax" >= 0 AND "occupancyAvg" >= 0);

ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_occupancy_ordered
  CHECK ("occupancyMin" <= "occupancyMax"
         AND "occupancyAvg" >= "occupancyMin"
         AND "occupancyAvg" <= "occupancyMax");

-- Posture frames are a partition of the sampled frames. They may sum to less
-- than sampleFrames (frames where nobody was detected), but never more.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_posture_frames_non_negative
  CHECK ("sittingFrames" >= 0 AND "standingFrames" >= 0 AND "walkingFrames" >= 0);

ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_posture_frames_within_sample
  CHECK ("sittingFrames" + "standingFrames" + "walkingFrames" <= "sampleFrames");

-- The activity index is defined on 0-100.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_activity_score_range
  CHECK ("avgActivityScore" >= 0 AND "avgActivityScore" <= 100);

-- One minute of wall clock caps total dwell at 60s per concurrently present
-- person. With a hard ceiling of 60 * occupancyMax, a value above this means
-- dwell is being accumulated across bucket boundaries — the bug that makes
-- person-hours grow without limit.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_dwell_within_minute
  CHECK ("totalDwellSeconds" >= 0
         AND "totalDwellSeconds" <= 60 * GREATEST("occupancyMax", 1));

-- Cannot see more distinct tracks than the peak concurrent count... except
-- you can: six people passing through one at a time gives occupancyMax 1 and
-- uniqueTrackCount 6. So only the lower bound is checkable.
ALTER TABLE public.zone_minute_stats
  ADD CONSTRAINT zms_unique_tracks_non_negative
  CHECK ("uniqueTrackCount" >= 0);

-- ═══════════════════════════════════════════════════════════════════════════
--  ZONE_DAY_STATS
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.zone_day_stats
  ADD CONSTRAINT zds_utilisation_range
  CHECK ("utilisationPct" >= 0 AND "utilisationPct" <= 100);

ALTER TABLE public.zone_day_stats
  ADD CONSTRAINT zds_peak_hour_range
  CHECK ("peakHour" IS NULL OR ("peakHour" >= 0 AND "peakHour" <= 23));

-- Posture ratios are shares of one. Allowing a small epsilon because they are
-- computed as floating-point division.
ALTER TABLE public.zone_day_stats
  ADD CONSTRAINT zds_posture_ratios_sum_to_one
  CHECK ("sittingRatio" + "standingRatio" + "walkingRatio" <= 1.001);

ALTER TABLE public.zone_day_stats
  ADD CONSTRAINT zds_occupied_minutes_bounded
  CHECK ("occupiedMinutes" >= 0 AND "occupiedMinutes" <= 1440);

-- ═══════════════════════════════════════════════════════════════════════════
--  SITES  —  working-hours windows
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.sites
  ADD CONSTRAINT sites_workday_window_valid
  CHECK ("workdayStartMinute" >= 0
         AND "workdayEndMinute" <= 1440
         AND "workdayStartMinute" < "workdayEndMinute");

ALTER TABLE public.sites
  ADD CONSTRAINT sites_capacity_positive
  CHECK ("totalCapacity" IS NULL OR "totalCapacity" > 0);

-- ═══════════════════════════════════════════════════════════════════════════
--  ORGANISATIONS
-- ═══════════════════════════════════════════════════════════════════════════

-- Retention must be at least a day (below that the job would delete data
-- before it could be read) and is capped at ~2 years to keep the privacy
-- promise meaningful.
ALTER TABLE public.organisations
  ADD CONSTRAINT organisations_retention_range
  CHECK ("dataRetentionDays" >= 1 AND "dataRetentionDays" <= 730);

-- Slug is URL-safe: lowercase alphanumeric and single hyphens.
ALTER TABLE public.organisations
  ADD CONSTRAINT organisations_slug_format
  CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$');

-- ═══════════════════════════════════════════════════════════════════════════
--  MEMBERSHIPS
-- ═══════════════════════════════════════════════════════════════════════════

-- Emails are stored lower-cased so invite matching needs no functional index
-- and cannot be defeated by casing.
ALTER TABLE public.memberships
  ADD CONSTRAINT memberships_email_lowercase
  CHECK ("invitedEmail" = lower("invitedEmail"));

-- An ACTIVE membership must point at a real profile; an INVITED one must not
-- have been accepted. These two together make the invite lifecycle
-- unambiguous — the state and the data always agree.
ALTER TABLE public.memberships
  ADD CONSTRAINT memberships_active_has_profile
  CHECK (status <> 'ACTIVE' OR ("profileId" IS NOT NULL AND "acceptedAt" IS NOT NULL));

ALTER TABLE public.memberships
  ADD CONSTRAINT memberships_invited_not_accepted
  CHECK (status <> 'INVITED' OR "acceptedAt" IS NULL);

-- Every organisation needs at least one admin. A CHECK cannot span rows, so
-- this is a trigger — it is the guard that stops an admin removing their own
-- access and locking the whole company out of its account.
CREATE OR REPLACE FUNCTION public.assert_org_keeps_an_admin()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  remaining INT;
  target_org UUID;
BEGIN
  target_org := COALESCE(OLD."orgId", NEW."orgId");

  SELECT count(*) INTO remaining
  FROM public.memberships
  WHERE "orgId" = target_org
    AND role = 'ADMIN'
    AND status = 'ACTIVE'
    AND id <> COALESCE(OLD.id, NEW.id);

  -- If the row surviving this statement is itself an active admin, we are fine.
  IF TG_OP <> 'DELETE'
     AND NEW.role = 'ADMIN'
     AND NEW.status = 'ACTIVE' THEN
    RETURN NEW;
  END IF;

  IF remaining = 0 THEN
    RAISE EXCEPTION
      'Organisation % must retain at least one active admin', target_org
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER memberships_keep_an_admin
  BEFORE UPDATE OR DELETE ON public.memberships
  FOR EACH ROW
  EXECUTE FUNCTION public.assert_org_keeps_an_admin();

-- ═══════════════════════════════════════════════════════════════════════════
--  ALERT RULES
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.alert_rules
  ADD CONSTRAINT alert_rules_sustained_positive
  CHECK ("sustainedMinutes" >= 1);

ALTER TABLE public.alert_rules
  ADD CONSTRAINT alert_rules_cooldown_non_negative
  CHECK ("cooldownMinutes" >= 0);

-- Threshold meaning depends on type, so validate per type. A percentage rule
-- with threshold 500 would never fire, and would look configured.
ALTER TABLE public.alert_rules
  ADD CONSTRAINT alert_rules_threshold_valid_for_type
  CHECK (
    CASE type
      WHEN 'UNDERUTILISATION' THEN "thresholdValue" >= 0 AND "thresholdValue" <= 100
      WHEN 'SEDENTARY'        THEN "thresholdValue" >= 1
      WHEN 'OVERCROWDING'     THEN "thresholdValue" >= 1
      WHEN 'ZONE_EMPTY'       THEN "thresholdValue" >= 1
      WHEN 'CAMERA_OFFLINE'   THEN "thresholdValue" >= 1
      ELSE TRUE
    END
  );

-- ═══════════════════════════════════════════════════════════════════════════
--  ALERTS
-- ═══════════════════════════════════════════════════════════════════════════

-- Acknowledgement and resolution must record who, not just when. An audit
-- trail with a timestamp and no actor is not accountability.
ALTER TABLE public.alerts
  ADD CONSTRAINT alerts_ack_has_actor
  CHECK (("acknowledgedAt" IS NULL) = ("acknowledgedById" IS NULL));

ALTER TABLE public.alerts
  ADD CONSTRAINT alerts_resolve_has_actor
  CHECK (("resolvedAt" IS NULL) = ("resolvedById" IS NULL));

ALTER TABLE public.alerts
  ADD CONSTRAINT alerts_state_matches_timestamps
  CHECK (
    CASE state
      WHEN 'ACKNOWLEDGED' THEN "acknowledgedAt" IS NOT NULL
      WHEN 'RESOLVED'     THEN "resolvedAt" IS NOT NULL
      ELSE TRUE
    END
  );

-- ═══════════════════════════════════════════════════════════════════════════
--  REPORTS & SESSIONS
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.reports
  ADD CONSTRAINT reports_range_ordered
  CHECK ("rangeStart" < "rangeEnd");

ALTER TABLE public.analysis_sessions
  ADD CONSTRAINT sessions_progress_non_negative
  CHECK ("processedFrames" >= 0);

ALTER TABLE public.analysis_sessions
  ADD CONSTRAINT sessions_progress_within_total
  CHECK ("totalFrames" IS NULL OR "processedFrames" <= "totalFrames");

ALTER TABLE public.analysis_sessions
  ADD CONSTRAINT sessions_coverage_ordered
  CHECK ("coverageStart" IS NULL OR "coverageEnd" IS NULL
         OR "coverageStart" <= "coverageEnd");

-- A terminal session must say when it terminated.
ALTER TABLE public.analysis_sessions
  ADD CONSTRAINT sessions_terminal_has_finish
  CHECK (status NOT IN ('DONE', 'ERROR', 'CANCELLED') OR "finishedAt" IS NOT NULL);

-- An errored session must say why. "Failed" with no message is the single
-- most common unhelpful state in a processing pipeline.
ALTER TABLE public.analysis_sessions
  ADD CONSTRAINT sessions_error_has_message
  CHECK (status <> 'ERROR' OR "errorMessage" IS NOT NULL);
