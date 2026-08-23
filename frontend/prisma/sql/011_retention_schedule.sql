-- VisionWorks — nightly retention schedule
--
-- WHY THIS FILE IS SMALL
--
-- The retention LOGIC already exists. `purge_expired_minute_stats()` in
-- 004_secrets_and_retention.sql walks every non-deleted organisation, deletes
-- `zone_minute_stats` older than that org's own `dataRetentionDays`, and writes
-- a `retention.purged` audit row per org that lost rows. It is per-org, not
-- global, and it is `SECURITY DEFINER` with EXECUTE revoked from both PUBLIC
-- and `authenticated` — deliberately, because it ignores RLS.
--
-- What was missing was only the schedule. This file adds it.
--
-- ⚠️ THIS FILE NEEDS pg_cron, WHICH IS NOT INSTALLED YET
--
-- `pg_cron` is AVAILABLE on this Supabase project but not installed, and
-- `CREATE EXTENSION` needs privileges the pooled application role does not
-- have. Enable it once, either in the dashboard
-- (Database → Extensions → search "pg_cron" → enable) or by running the
-- CREATE EXTENSION below as a superuser in the SQL editor, then run the rest.
--
-- Until it is enabled, retention does not run automatically. The function can
-- still be invoked by hand from the SQL editor, and everything it does has been
-- verified that way — see §11 Step 9.
--
-- WHY NOT SCHEDULE IT FROM THE PYTHON BACKEND INSTEAD
--
-- Because a privacy guarantee that only holds while a process happens to be
-- running is a weak one. "We delete your measurements after 90 days" must not
-- depend on whether the CV server was up that night. pg_cron runs inside the
-- database that owns the data.
--
-- Apply AFTER 010_alert_update_role.sql.

-- ── 1. Enable the extension (superuser / dashboard) ────────────────────────
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- ── 2. Schedule ────────────────────────────────────────────────────────────
--
-- 03:15 UTC: past midnight in every timezone the product is likely deployed
-- in, and offset from the top of the hour so it does not contend with every
-- other cron job on the instance.
--
-- Unscheduled first so re-running this file replaces the job rather than
-- stacking a second copy that deletes the same rows twice.
SELECT cron.unschedule('visionworks-retention')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'visionworks-retention');

SELECT cron.schedule(
  'visionworks-retention',
  '15 3 * * *',
  $$SELECT public.purge_expired_minute_stats();$$
);

-- Day rollups must be built BEFORE the minute rows they summarise are deleted,
-- or a 90-day retention policy silently loses the year-over-year trend it was
-- supposed to preserve. 02:45 leaves half an hour of headroom before the purge.
SELECT cron.unschedule('visionworks-day-rollup')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'visionworks-day-rollup');

SELECT cron.schedule(
  'visionworks-day-rollup',
  '45 2 * * *',
  $$SELECT public.rollup_zone_day_stats((now() - interval '1 day')::date);$$
);

-- Expired report files. Cheap, and keeps generated exports from accumulating.
SELECT cron.unschedule('visionworks-expire-reports')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'visionworks-expire-reports');

SELECT cron.schedule(
  'visionworks-expire-reports',
  '30 3 * * *',
  $$SELECT public.expire_stale_reports();$$
);

-- ── 3. Verify ──────────────────────────────────────────────────────────────
--
--   SELECT jobname, schedule, active FROM cron.job
--    WHERE jobname LIKE 'visionworks-%';
--
--   SELECT j.jobname, r.status, r.return_message, r.start_time
--     FROM cron.job_run_details r
--     JOIN cron.job j ON j.jobid = r.jobid
--    WHERE j.jobname LIKE 'visionworks-%'
--    ORDER BY r.start_time DESC
--    LIMIT 10;
--
-- And to confirm it actually deleted something, without waiting for 03:15:
--
--   SELECT o.slug, a.metadata, a."createdAt"
--     FROM audit_logs a JOIN organisations o ON o.id = a."orgId"
--    WHERE a.action = 'retention.purged'
--    ORDER BY a."createdAt" DESC;
