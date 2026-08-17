-- VisionWorks — credential protection and the retention job
--
-- Two things live here because both are properties of the database rather than
-- the application:
--
--   1. RTSP URLs contain camera passwords. They must not be readable as
--      plaintext, and must never be sent to a browser.
--   2. The retention promise on /security is only true if something actually
--      deletes old data. That something is here.
--
-- Apply AFTER 003_rls_policies.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  1. RTSP CREDENTIAL HANDLING
-- ═══════════════════════════════════════════════════════════════════════════

-- A camera URL looks like:
--     rtsp://admin:Str0ngPass@192.168.1.40:554/stream1
--
-- That password is a real credential — it grants live video of the customer's
-- office. Two protections:
--
--   (a) The plaintext column is never selected by the app. A view exposes a
--       redacted form instead, and the API reads the view.
--   (b) Encryption at rest via pgsodium/Vault where available.
--
-- On Supabase, prefer Vault: store the URL as a secret and keep only its uuid
-- in `cameras.rtspUrl`. The redaction view below works either way, because it
-- only ever strips the userinfo segment.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Strip `user:password@` from a URL, keeping the shape recognisable so an
-- operator can still confirm they typed the right host.
--   rtsp://admin:secret@10.0.0.5:554/s1  ->  rtsp://****@10.0.0.5:554/s1
CREATE OR REPLACE FUNCTION public.redact_rtsp_url(p_url TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN p_url IS NULL THEN NULL
    WHEN p_url !~ '://' THEN p_url
    ELSE regexp_replace(p_url, '://[^/@]*@', '://****@')
  END;
$$;

-- The view the application should read for camera lists. `security_invoker`
-- makes it honour the caller's RLS rather than the view owner's — without it,
-- this view would be a hole straight through every policy in 003.
CREATE OR REPLACE VIEW public.cameras_safe
WITH (security_invoker = true)
AS
SELECT
  c.id,
  c."orgId",
  c."siteId",
  c.name,
  c.description,
  c."sourceType",
  public.redact_rtsp_url(c."rtspUrl") AS "rtspUrlRedacted",
  (c."rtspUrl" IS NOT NULL)           AS "hasRtspUrl",
  c."deviceIndex",
  c."fpsTarget",
  c."frameWidth",
  c."frameHeight",
  c."homographyMatrix" IS NOT NULL    AS "isCalibrated",
  c.status,
  c."lastSeenAt",
  c."lastErrorMessage",
  c."createdAt",
  c."updatedAt"
FROM public.cameras c
WHERE c."deletedAt" IS NULL;

-- Supabase grants ALL on new objects in `public` to anon and authenticated by
-- default. 003 revokes anon's access, but it runs BEFORE this view exists, so
-- the default grant lands afterwards and is never stripped — the view would be
-- readable without a session. Revoke here, where the object is created.
REVOKE ALL ON public.cameras_safe FROM anon;
GRANT SELECT ON public.cameras_safe TO authenticated;

-- Column-level privilege so even a mistaken `select *` from a browser client
-- cannot return the credential. The CV backend uses the service role, which
-- is unaffected.
--
-- CAREFUL: a table-level `GRANT SELECT ON cameras` silently supersedes a
-- column-level REVOKE — Postgres treats the table grant as covering every
-- column, so the revoke below would appear to succeed and change nothing.
-- (003 issues exactly such a table-wide grant, which is why this must come
-- after it AND must strip the table grant first.) The only way to keep a
-- column unreadable is to hold no table-level SELECT at all and instead grant
-- each permitted column explicitly.
REVOKE SELECT ON public.cameras FROM authenticated;

GRANT SELECT (
  id, "orgId", "siteId", name, description,
  "sourceType", "deviceIndex", "fpsTarget",
  "frameWidth", "frameHeight",
  "homographyMatrix", "homographyPoints",
  status, "lastSeenAt", "lastErrorMessage",
  "createdAt", "updatedAt", "deletedAt"
  -- "rtspUrl" deliberately absent.
) ON public.cameras TO authenticated;

-- Writes still need the column: a manager saving a camera must be able to set
-- its URL. Write-without-read is exactly the asymmetry we want — you can
-- replace the credential, never retrieve it.
GRANT INSERT, UPDATE ON public.cameras TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  2. DAY ROLLUP
-- ═══════════════════════════════════════════════════════════════════════════

-- Collapses minute buckets into ZoneDayStat. Must run before retention
-- deletes the minutes, which is why both are called from the same job — a day
-- rollup that runs after the purge silently produces zeros.
--
-- Utilisation is measured against working minutes only. A cleaner walking
-- through at 03:00 should not appear as "the room was used at night", and
-- including 24h in the denominator would make every zone look underused.
CREATE OR REPLACE FUNCTION public.rollup_zone_day_stats(p_date DATE DEFAULT (now() - interval '1 day')::date)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_rows INT;
BEGIN
  INSERT INTO public.zone_day_stats AS zds (
    id, "orgId", "siteId", "cameraId", "zoneId", "statDate",
    "peakOccupancy", "avgOccupancy", "peakHour", "occupiedMinutes",
    "utilisationPct", "sittingRatio", "standingRatio", "walkingRatio",
    "totalDwellSeconds", "avgActivityScore", "createdAt", "updatedAt"
  )
  SELECT
    gen_random_uuid(),
    s."orgId", s."siteId", s."cameraId", s."zoneId", p_date,
    max(s."occupancyMax"),
    avg(s."occupancyAvg"),
    -- Hour containing the peak, in the site's local zone.
    (array_agg(
       EXTRACT(HOUR FROM s."bucketStart" AT TIME ZONE COALESCE(si.timezone, o.timezone, 'UTC'))
       ORDER BY s."occupancyMax" DESC, s."bucketStart" ASC
     ))[1]::int,
    count(*) FILTER (WHERE s."occupancyMax" >= 1),
    -- Occupied working minutes over available working minutes, capped at 100
    -- so a clock skew cannot produce 103%.
    LEAST(100.0, round(
      100.0 * count(*) FILTER (WHERE s."occupancyMax" >= 1)
            / NULLIF(COALESCE(si."workdayEndMinute", 1080)
                     - COALESCE(si."workdayStartMinute", 540), 0)
    , 2)),
    -- Posture shares of sampled frames. NULLIF guards a bucket set with no
    -- samples at all, which would otherwise divide by zero.
    round(sum(s."sittingFrames")::numeric  / NULLIF(sum(s."sampleFrames"), 0), 4),
    round(sum(s."standingFrames")::numeric / NULLIF(sum(s."sampleFrames"), 0), 4),
    round(sum(s."walkingFrames")::numeric  / NULLIF(sum(s."sampleFrames"), 0), 4),
    sum(s."totalDwellSeconds"),
    avg(s."avgActivityScore"),
    now(), now()
  FROM public.zone_minute_stats s
  JOIN public.zones      z  ON z.id = s."zoneId"
  JOIN public.organisations o ON o.id = s."orgId"
  LEFT JOIN public.sites si ON si.id = s."siteId"
  WHERE (s."bucketStart" AT TIME ZONE COALESCE(si.timezone, o.timezone, 'UTC'))::date = p_date
    AND z."excludeFromUtilisation" = false
    -- Working hours only.
    AND EXTRACT(HOUR FROM s."bucketStart" AT TIME ZONE COALESCE(si.timezone, o.timezone, 'UTC')) * 60
      + EXTRACT(MINUTE FROM s."bucketStart" AT TIME ZONE COALESCE(si.timezone, o.timezone, 'UTC'))
        BETWEEN COALESCE(si."workdayStartMinute", 540)
            AND COALESCE(si."workdayEndMinute", 1080)
  GROUP BY s."orgId", s."siteId", s."cameraId", s."zoneId",
           si.timezone, o.timezone, si."workdayStartMinute", si."workdayEndMinute"
  -- Idempotent: safe to re-run for a day already rolled up.
  ON CONFLICT ("zoneId", "statDate") DO UPDATE SET
    "peakOccupancy"     = EXCLUDED."peakOccupancy",
    "avgOccupancy"      = EXCLUDED."avgOccupancy",
    "peakHour"          = EXCLUDED."peakHour",
    "occupiedMinutes"   = EXCLUDED."occupiedMinutes",
    "utilisationPct"    = EXCLUDED."utilisationPct",
    "sittingRatio"      = EXCLUDED."sittingRatio",
    "standingRatio"     = EXCLUDED."standingRatio",
    "walkingRatio"      = EXCLUDED."walkingRatio",
    "totalDwellSeconds" = EXCLUDED."totalDwellSeconds",
    "avgActivityScore"  = EXCLUDED."avgActivityScore",
    "updatedAt"         = now();

  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  3. RETENTION
-- ═══════════════════════════════════════════════════════════════════════════

-- Deletes minute buckets past each organisation's own dataRetentionDays.
-- Per-org rather than one global cutoff, because retention is a customer
-- setting on /settings/organisation — and because a shorter retention is a
-- promise the customer made to their staff.
--
-- Day rollups are deliberately NOT deleted here. They contain no
-- person-level detail at any resolution, so keeping them lets a 90-day
-- retention policy still support a year-over-year trend. That is the payoff
-- of aggregating twice.
CREATE OR REPLACE FUNCTION public.purge_expired_minute_stats()
RETURNS TABLE (org_id UUID, deleted_rows BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  r      RECORD;
  v_del  BIGINT;
BEGIN
  FOR r IN
    SELECT id, "dataRetentionDays" FROM public.organisations WHERE "deletedAt" IS NULL
  LOOP
    DELETE FROM public.zone_minute_stats
     WHERE "orgId" = r.id
       AND "bucketStart" < now() - make_interval(days => r."dataRetentionDays");

    GET DIAGNOSTICS v_del = ROW_COUNT;

    IF v_del > 0 THEN
      INSERT INTO public.audit_logs
        (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
      VALUES
        (gen_random_uuid(), r.id, NULL, 'system@visionworks',
         'retention.purged', 'ZoneMinuteStat', NULL,
         jsonb_build_object('deletedRows', v_del, 'retentionDays', r."dataRetentionDays"),
         now());
    END IF;

    RETURN QUERY SELECT r.id, v_del;
  END LOOP;
END;
$$;

-- Expire downloadable report files. The row survives with status EXPIRED so
-- /reports still shows that an export happened and who took it — the record of
-- a data export is itself a privacy control and outlives the file.
CREATE OR REPLACE FUNCTION public.expire_stale_reports()
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_rows INT;
BEGIN
  UPDATE public.reports
     SET status = 'EXPIRED',
         "filePath" = NULL,
         "downloadTokenHash" = NULL
   WHERE status = 'READY'
     AND "expiresAt" IS NOT NULL
     AND "expiresAt" < now();
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  4. SCHEDULING
-- ═══════════════════════════════════════════════════════════════════════════

-- Order matters: roll up first, then purge. Reversed, the rollup would find
-- the minutes already deleted and write zeros over good history.
--
-- Enable pg_cron in the Supabase dashboard (Database -> Extensions), then:
--
--   select cron.schedule('vw-nightly', '15 2 * * *', $$
--     select public.rollup_zone_day_stats((now() - interval '1 day')::date);
--     select public.purge_expired_minute_stats();
--     select public.expire_stale_reports();
--   $$);
--
-- These functions are SECURITY DEFINER and must not be callable by end users —
-- purge_expired_minute_stats() ignores RLS by design.
REVOKE ALL ON FUNCTION public.rollup_zone_day_stats(DATE)      FROM PUBLIC;
REVOKE ALL ON FUNCTION public.purge_expired_minute_stats()     FROM PUBLIC;
REVOKE ALL ON FUNCTION public.expire_stale_reports()           FROM PUBLIC;
REVOKE ALL ON FUNCTION public.rollup_zone_day_stats(DATE)      FROM authenticated;
REVOKE ALL ON FUNCTION public.purge_expired_minute_stats()     FROM authenticated;
REVOKE ALL ON FUNCTION public.expire_stale_reports()           FROM authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  5. FINAL SWEEP — anon must reach nothing in `public`
-- ═══════════════════════════════════════════════════════════════════════════
--
-- This runs last, on purpose. 003 revokes anon's grants, but any object created
-- after it (like cameras_safe above) picks up Supabase's default grants and
-- keeps them. Rather than rely on remembering to revoke at every future
-- creation site, sweep the whole schema here — tables, views and sequences.
--
-- An unauthenticated request should not be able to read anything. RLS would
-- mostly stop it anyway, but a missing GRANT is a second, simpler barrier that
-- does not depend on a policy being correct.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL ROUTINES  IN SCHEMA public FROM anon;

-- And stop the problem recurring: change the default privileges so objects
-- created from now on never grant anything to anon in the first place.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON ROUTINES  FROM anon;
