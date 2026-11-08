-- VisionWorks — retire the tables that belonged to the workplace-productivity
-- product.
--
-- The project is now workplace SAFETY COMPLIANCE monitoring. These eight tables
-- existed only for the old idea and have no place in the new one:
--
--   employees, face_templates            named people and their face vectors.
--                                        The safety product keeps workers
--                                        anonymous, so there is nobody to name.
--   identity_events                      who-was-where per person.
--   employee_day_stats,
--   employee_hour_stats                  per-person desk time and break time:
--                                        the productivity measurement.
--   camera_links                         the hand-off map for cross-camera
--                                        tracking, which was dropped from the
--                                        core design.
--   alert_rules, alerts                  occupancy alerts (sedentary, overcrowded,
--                                        under-used). Safety alerts are decisions
--                                        awaiting review (see 025_safety.sql).
--
-- KEPT, deliberately: zone_minute_stats and zone_day_stats (the site overview),
-- analysis_sessions, reports, audit_logs, platform_audit_logs and everything
-- that holds organisations, users, sites, cameras and zones.
--
-- SAFETY
--
-- Dropped WITHOUT CASCADE, in child-to-parent order, so an unexpected dependency
-- makes this fail loudly instead of silently taking a view or a policy with it.
-- The guard below also refuses to run if any of the eight tables holds a row:
-- dropping a table is irreversible for the data in it.
--
-- To undo: re-apply 014 (alert enums) / 001-003 / 020 / 022 for the table
-- definitions. The data cannot be recovered from this file.
--
-- Idempotent: every statement uses IF EXISTS, so re-running is a no-op.

-- ═══ 0. GUARD: REFUSE TO DESTROY DATA ═══════════════════════════════════════
DO $$
DECLARE
  t   TEXT;
  n   BIGINT;
  bad TEXT := '';
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'employee_hour_stats', 'employee_day_stats', 'identity_events',
    'face_templates', 'alerts', 'alert_rules', 'camera_links', 'employees'
  ] LOOP
    IF to_regclass('public.' || quote_ident(t)) IS NOT NULL THEN
      EXECUTE format('SELECT count(*) FROM public.%I', t) INTO n;
      IF n > 0 THEN bad := bad || format(' %s(%s rows)', t, n); END IF;
    END IF;
  END LOOP;
  IF bad <> '' THEN
    RAISE EXCEPTION 'refusing to drop tables that still hold data:%', bad;
  END IF;
END $$;

-- ═══ 1. THE ORGANISATION PURGE MUST NOT TOUCH TABLES THAT NO LONGER EXIST ═══
-- Same function as 019 minus the two alert deletes; privileges unchanged.
CREATE OR REPLACE FUNCTION public.purge_deleted_organisations(p_grace_days INT DEFAULT 30)
RETURNS TABLE (purged INT, names TEXT[])
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_ids   UUID[];
  v_names TEXT[];
BEGIN
  IF p_grace_days < 0 THEN
    RAISE EXCEPTION 'grace period cannot be negative';
  END IF;

  SELECT array_agg(id), array_agg(name)
    INTO v_ids, v_names
    FROM public.organisations
   WHERE "deletedAt" IS NOT NULL
     AND "deletedAt" < now() - make_interval(days => p_grace_days);

  IF v_ids IS NULL THEN
    RETURN QUERY SELECT 0, ARRAY[]::TEXT[];
    RETURN;
  END IF;

  -- Child-to-parent order. Explicit rather than relying on cascades, because
  -- the FKs are a mix of CASCADE and SET NULL and the order matters for the
  -- ones that are not.
  DELETE FROM public.zone_minute_stats  WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.zone_day_stats     WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.reports            WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.analysis_sessions  WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.zones              WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.cameras            WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.sites              WHERE "orgId" = ANY(v_ids);

  UPDATE public.profiles SET "currentOrgId" = NULL WHERE "currentOrgId" = ANY(v_ids);

  -- `assert_org_keeps_an_admin` fires on membership deletes and would block
  -- removing the last admin of an organisation being destroyed outright.
  ALTER TABLE public.memberships DISABLE TRIGGER USER;
  DELETE FROM public.memberships WHERE "orgId" = ANY(v_ids);
  ALTER TABLE public.memberships ENABLE TRIGGER USER;

  DELETE FROM public.audit_logs     WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.organisations  WHERE id = ANY(v_ids);

  RETURN QUERY SELECT array_length(v_ids, 1), v_names;
END;
$$;

REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM anon;
REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.purge_deleted_organisations(INT) TO service_role;

-- ═══ 2. THE RETENTION JOB FOR A TABLE THAT IS GOING ═════════════════════════
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron')
     AND EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'purge-identity-events') THEN
    PERFORM cron.unschedule('purge-identity-events');
  END IF;
END $$;

-- ═══ 3. THE TABLES, CHILD BEFORE PARENT, NO CASCADE ═════════════════════════
-- Tables first, functions after: row-level-security policies on these tables
-- call my_employee_ids(), so the function cannot be dropped while they exist.
DROP TABLE IF EXISTS public.employee_hour_stats;
DROP TABLE IF EXISTS public.employee_day_stats;
DROP TABLE IF EXISTS public.identity_events;
DROP TABLE IF EXISTS public.face_templates;
DROP TABLE IF EXISTS public.alerts;
DROP TABLE IF EXISTS public.alert_rules;
DROP TABLE IF EXISTS public.camera_links;
DROP TABLE IF EXISTS public.employees;

-- ═══ 4. FUNCTIONS THAT ONLY SERVED THOSE TABLES ═════════════════════════════
DROP FUNCTION IF EXISTS public.purge_identity_events(INTEGER);
DROP FUNCTION IF EXISTS public.soft_delete_employee(UUID);
DROP FUNCTION IF EXISTS public.my_employee_ids();

-- ═══ 5. ENUMS THAT ONLY THE ALERT TABLES USED ═══════════════════════════════
DROP TYPE IF EXISTS public."AlertState";
DROP TYPE IF EXISTS public."AlertSeverity";
DROP TYPE IF EXISTS public."AlertType";
