-- 029_plan_term_enforcement.sql
--
-- A paid term now ENDS. Until this file, `planRenewsAt` was only displayed: a monthly or yearly Growth or
-- Enterprise plan kept its limits forever after its end date. From here:
--
--   EXPIRY      A paid plan whose term has ended counts as Starter (FREE) until it is renewed. Nothing is deleted
--               or switched off at that moment: cameras, sites and members over the Starter limit keep working,
--               but no new ones can be added, the history window falls to Starter's, and scheduled reports pause.
--               `plan` itself is left as it was, so renewing restores the plan the organisation paid for.
--   RENEWAL     renew_plan(): an administrator starts the next term (demo billing: no money moves). Allowed in the
--               last 7 days of a term (extends it from its end date) or after it has ended (starts from now).
--   GUARD       the billing columns can only be changed through these functions, not by a direct table write, so
--               an administrator cannot extend their own term or tier through the API.
--   LIMITS      the camera, site and member triggers use the plan in force; the member limit now also applies
--               when a suspended member is restored (it only fired on INSERT before); soft-deleted rows do not
--               count.
--   HISTORY     the retention purge keeps the smaller of the organisation's setting and its plan's history limit.
--   SCHEDULED   scheduled safety reports (Growth and Enterprise): daily, weekly or monthly, made by pg_cron and
--   REPORTS     listed with the reports made before; each is rebuilt from the data when downloaded, like the rest.
--
-- Idempotent: safe to run again.

-- ═══ THE PLAN IN FORCE ══════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.effective_plan(p_plan "PlanTier", p_renews TIMESTAMPTZ)
RETURNS "PlanTier"
LANGUAGE sql
STABLE
AS $$
  SELECT CASE
    WHEN p_plan <> 'FREE' AND p_renews IS NOT NULL AND p_renews <= now() THEN 'FREE'::"PlanTier"
    ELSE p_plan
  END;
$$;

CREATE OR REPLACE FUNCTION public.org_effective_plan(p_org UUID)
RETURNS "PlanTier"
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT public.effective_plan(plan, "planRenewsAt") FROM public.organisations WHERE id = p_org;
$$;
REVOKE ALL ON FUNCTION public.org_effective_plan(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.org_effective_plan(UUID) TO authenticated;

-- ═══ LIMIT TRIGGERS: the plan in force, live rows only ══════════════════════

CREATE OR REPLACE FUNCTION public.enforce_camera_limit()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_plan "PlanTier"; v_max INT; v_count INT;
BEGIN
  v_plan := public.org_effective_plan(NEW."orgId");
  IF v_plan IS NULL THEN RETURN NEW; END IF;
  SELECT max_cameras INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN RETURN NEW; END IF;
  SELECT count(*) INTO v_count FROM public.cameras WHERE "orgId" = NEW."orgId" AND "deletedAt" IS NULL;
  IF v_count >= v_max THEN
    RAISE EXCEPTION 'plan_limit_cameras: the % plan allows % camera(s); this organisation already has %.',
      v_plan, v_max, v_count USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_site_limit()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_plan "PlanTier"; v_max INT; v_count INT;
BEGIN
  v_plan := public.org_effective_plan(NEW."orgId");
  IF v_plan IS NULL THEN RETURN NEW; END IF;
  SELECT max_sites INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN RETURN NEW; END IF;
  SELECT count(*) INTO v_count FROM public.sites WHERE "orgId" = NEW."orgId" AND "deletedAt" IS NULL;
  IF v_count >= v_max THEN
    RAISE EXCEPTION 'plan_limit_sites: the % plan allows % site(s); this organisation already has %.',
      v_plan, v_max, v_count USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

-- Fires on INSERT, and on an UPDATE that brings a seat back (a suspended or removed member restored).
CREATE OR REPLACE FUNCTION public.enforce_seat_limit()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_plan "PlanTier"; v_max INT; v_count INT;
BEGIN
  IF NEW.status NOT IN ('ACTIVE', 'INVITED') THEN RETURN NEW; END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('ACTIVE', 'INVITED') THEN RETURN NEW; END IF;
  v_plan := public.org_effective_plan(NEW."orgId");
  IF v_plan IS NULL THEN RETURN NEW; END IF;
  SELECT max_seats INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN RETURN NEW; END IF;
  SELECT count(*) INTO v_count FROM public.memberships
   WHERE "orgId" = NEW."orgId" AND status IN ('ACTIVE', 'INVITED') AND id <> NEW.id;
  IF v_count >= v_max THEN
    RAISE EXCEPTION 'plan_limit_seats: the % plan allows % member(s); this organisation already has % (including pending invitations).',
      v_plan, v_max, v_count USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS seat_plan_limit ON public.memberships;
CREATE TRIGGER seat_plan_limit
  BEFORE INSERT OR UPDATE OF status ON public.memberships
  FOR EACH ROW EXECUTE FUNCTION public.enforce_seat_limit();

-- Retention may not be RAISED above the plan in force (an existing higher value is left alone, as before).
CREATE OR REPLACE FUNCTION public.clamp_retention_to_plan()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_max INT;
BEGIN
  SELECT max_retention_days INTO v_max FROM public.plan_limits
   WHERE plan = public.effective_plan(NEW.plan, NEW."planRenewsAt");
  IF v_max IS NULL THEN RETURN NEW; END IF;
  IF NEW."dataRetentionDays" > v_max THEN
    IF TG_OP = 'UPDATE' AND OLD."dataRetentionDays" > v_max AND NEW."dataRetentionDays" <= OLD."dataRetentionDays" THEN
      RETURN NEW;
    END IF;
    NEW."dataRetentionDays" := v_max;
  END IF;
  RETURN NEW;
END;
$$;

-- ═══ HISTORY: the smaller of the setting and the plan's limit ═══════════════

CREATE OR REPLACE FUNCTION public.org_history_days(p_retention INT, p_plan "PlanTier", p_renews TIMESTAMPTZ)
RETURNS INT
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT LEAST(p_retention, COALESCE(
    (SELECT max_retention_days FROM public.plan_limits WHERE plan = public.effective_plan(p_plan, p_renews)),
    p_retention));
$$;

CREATE OR REPLACE FUNCTION public.purge_safety_data()
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  -- raw events: past the organisation's history window
  WITH gone AS (
    DELETE FROM public.safety_events e
     USING public.organisations o
     WHERE e."orgId" = o.id
       AND e."observedAt" < NOW() - make_interval(days => public.org_history_days(o."dataRetentionDays", o.plan, o."planRenewsAt"))
    RETURNING e."orgId")
  INSERT INTO public.audit_logs ("orgId", action, "targetType", metadata)
  SELECT "orgId", 'safety.events_purged', 'safety_event', jsonb_build_object('count', count(*))
    FROM gone GROUP BY "orgId";

  -- decisions are the compliance record: kept twice as long as raw events, and never while a violation still
  -- awaits review or a corrective action is still open (deleting the decision would cascade away the action)
  WITH gone AS (
    DELETE FROM public.safety_decisions d
     USING public.organisations o
     WHERE d."orgId" = o.id
       AND d."observedAt" < NOW() - make_interval(days => 2 * public.org_history_days(o."dataRetentionDays", o.plan, o."planRenewsAt"))
       AND NOT (d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING')
       AND NOT EXISTS (SELECT 1 FROM public.corrective_actions a
                        WHERE a."decisionId" = d.id AND a.status IN ('OPEN','IN_PROGRESS'))
    RETURNING d."orgId")
  INSERT INTO public.audit_logs ("orgId", action, "targetType", metadata)
  SELECT "orgId", 'safety.decisions_purged', 'safety_decision', jsonb_build_object('count', count(*))
    FROM gone GROUP BY "orgId";
END;
$$;

-- ═══ SCHEDULED REPORTS: columns ═════════════════════════════════════════════

ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "reportSchedule" TEXT NOT NULL DEFAULT 'OFF';
ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "reportScheduleNextAt" TIMESTAMPTZ;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'organisations_report_schedule_check') THEN
    ALTER TABLE public.organisations ADD CONSTRAINT organisations_report_schedule_check
      CHECK ("reportSchedule" IN ('OFF', 'DAILY', 'WEEKLY', 'MONTHLY'));
  END IF;
END $$;

-- ═══ GUARD: billing columns change only through the functions ═══════════════
-- A direct write by a signed-in user (role `authenticated`) to any of these is refused. The SECURITY DEFINER
-- functions below run as their owner, and the service role (operators, scripts) is not affected.

CREATE OR REPLACE FUNCTION public.guard_billing_columns()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF current_user IN ('authenticated', 'anon') AND (
       NEW.plan IS DISTINCT FROM OLD.plan
    OR NEW."billingPeriod" IS DISTINCT FROM OLD."billingPeriod"
    OR NEW."planStartedAt" IS DISTINCT FROM OLD."planStartedAt"
    OR NEW."planRenewsAt" IS DISTINCT FROM OLD."planRenewsAt"
    OR NEW."planSelectedAt" IS DISTINCT FROM OLD."planSelectedAt"
    OR NEW."reportSchedule" IS DISTINCT FROM OLD."reportSchedule"
    OR NEW."reportScheduleNextAt" IS DISTINCT FROM OLD."reportScheduleNextAt") THEN
    RAISE EXCEPTION 'billing_columns: the plan and its term can only be changed from the Plan screen.'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS org_guard_billing ON public.organisations;
CREATE TRIGGER org_guard_billing
  BEFORE UPDATE ON public.organisations
  FOR EACH ROW EXECUTE FUNCTION public.guard_billing_columns();

-- ═══ RENEWAL ════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.renew_plan(p_period "BillingPeriod" DEFAULT NULL)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid    UUID := (SELECT auth.uid());
  v_org_id UUID;
  v_org    RECORD;
  v_period "BillingPeriod";
  v_start  TIMESTAMPTZ;
  v_end    TIMESTAMPTZ;
  v_email  TEXT;
  v_expired BOOLEAN;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT; RETURN;
  END IF;
  SELECT "currentOrgId" INTO v_org_id FROM public.profiles WHERE id = v_uid;
  IF v_org_id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'No organisation selected.'::TEXT; RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.memberships
                  WHERE "orgId" = v_org_id AND "profileId" = v_uid AND status = 'ACTIVE' AND role = 'ADMIN') THEN
    RETURN QUERY SELECT FALSE, 'Only an administrator can renew the plan.'::TEXT; RETURN;
  END IF;
  SELECT * INTO v_org FROM public.organisations WHERE id = v_org_id AND "deletedAt" IS NULL FOR UPDATE;
  IF v_org.id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That organisation is no longer available.'::TEXT; RETURN;
  END IF;
  IF v_org.plan = 'FREE' THEN
    RETURN QUERY SELECT FALSE, 'The Starter plan is free and does not need renewing.'::TEXT; RETURN;
  END IF;

  v_period  := COALESCE(p_period, v_org."billingPeriod");
  v_expired := v_org."planRenewsAt" IS NULL OR v_org."planRenewsAt" <= now();

  IF v_expired THEN
    -- A new term from today.
    v_start := now();
    v_end   := public.plan_term_end(v_org.plan, v_period, v_start);
  ELSIF v_org."planRenewsAt" - now() <= INTERVAL '7 days' THEN
    -- Renewing early: the next term follows on from the end of this one, so no paid day is lost.
    v_start := v_org."planStartedAt";
    v_end   := public.plan_term_end(v_org.plan, v_period, v_org."planRenewsAt");
  ELSE
    RETURN QUERY SELECT FALSE, format('Your plan runs until %s. It can be renewed in its last 7 days, from %s.',
      to_char(v_org."planRenewsAt", 'FMDD Mon YYYY'),
      to_char(v_org."planRenewsAt" - INTERVAL '7 days', 'FMDD Mon YYYY'))::TEXT;
    RETURN;
  END IF;

  UPDATE public.organisations
     SET "billingPeriod" = v_period,
         "planStartedAt" = v_start,
         "planRenewsAt"  = v_end,
         "updatedAt"     = now()
   WHERE id = v_org_id;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;
  INSERT INTO public.audit_logs (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES (gen_random_uuid(), v_org_id, v_uid, v_email, 'organisation.plan_renewed', 'Organisation', v_org_id::text,
          jsonb_build_object('plan', v_org.plan, 'period', v_period, 'was_expired', v_expired,
                             'renews_at', v_end), now());

  RETURN QUERY SELECT TRUE, format('Renewed. Your plan now runs until %s.', to_char(v_end, 'FMDD Mon YYYY'))::TEXT;
END;
$$;
REVOKE ALL ON FUNCTION public.renew_plan("BillingPeriod") FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.renew_plan("BillingPeriod") TO authenticated;

-- ═══ SCHEDULED REPORTS: setting and running ═════════════════════════════════

CREATE OR REPLACE FUNCTION public.report_schedule_step(p_schedule TEXT)
RETURNS INTERVAL LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_schedule WHEN 'DAILY' THEN INTERVAL '1 day' WHEN 'WEEKLY' THEN INTERVAL '7 days'
                         WHEN 'MONTHLY' THEN INTERVAL '1 month' END;
$$;

CREATE OR REPLACE FUNCTION public.set_report_schedule(p_schedule TEXT)
RETURNS TABLE (ok BOOLEAN, message TEXT, next_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid    UUID := (SELECT auth.uid());
  v_org_id UUID;
  v_org    RECORD;
  v_next   TIMESTAMPTZ;
  v_email  TEXT;
BEGIN
  IF p_schedule NOT IN ('OFF', 'DAILY', 'WEEKLY', 'MONTHLY') THEN
    RETURN QUERY SELECT FALSE, 'Choose off, daily, weekly or monthly.'::TEXT, NULL::TIMESTAMPTZ; RETURN;
  END IF;
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT, NULL::TIMESTAMPTZ; RETURN;
  END IF;
  SELECT "currentOrgId" INTO v_org_id FROM public.profiles WHERE id = v_uid;
  IF NOT EXISTS (SELECT 1 FROM public.memberships
                  WHERE "orgId" = v_org_id AND "profileId" = v_uid AND status = 'ACTIVE' AND role IN ('ADMIN', 'MANAGER')) THEN
    RETURN QUERY SELECT FALSE, 'Only an administrator or manager can schedule reports.'::TEXT, NULL::TIMESTAMPTZ; RETURN;
  END IF;
  SELECT * INTO v_org FROM public.organisations WHERE id = v_org_id AND "deletedAt" IS NULL;
  IF v_org.id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That organisation is no longer available.'::TEXT, NULL::TIMESTAMPTZ; RETURN;
  END IF;
  IF p_schedule <> 'OFF' AND public.effective_plan(v_org.plan, v_org."planRenewsAt") = 'FREE' THEN
    RETURN QUERY SELECT FALSE, CASE WHEN v_org.plan <> 'FREE'
      THEN 'Your plan has ended. Renew it to schedule reports again.'
      ELSE 'Scheduled PDF reports are part of the Growth and Enterprise plans.' END::TEXT, NULL::TIMESTAMPTZ;
    RETURN;
  END IF;

  -- The first one is made at the next midnight UTC (and covers the period before it).
  v_next := CASE WHEN p_schedule = 'OFF' THEN NULL ELSE date_trunc('day', now()) + INTERVAL '1 day' END;
  UPDATE public.organisations SET "reportSchedule" = p_schedule, "reportScheduleNextAt" = v_next, "updatedAt" = now()
   WHERE id = v_org_id;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;
  INSERT INTO public.audit_logs (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES (gen_random_uuid(), v_org_id, v_uid, v_email, 'organisation.report_schedule_changed', 'Organisation', v_org_id::text,
          jsonb_build_object('from', v_org."reportSchedule", 'to', p_schedule), now());

  RETURN QUERY SELECT TRUE, CASE WHEN p_schedule = 'OFF' THEN 'Scheduled reports are off.'
    ELSE format('Scheduled. The first report is made on %s.', to_char(v_next, 'FMDD Mon YYYY')) END::TEXT, v_next;
END;
$$;
REVOKE ALL ON FUNCTION public.set_report_schedule(TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.set_report_schedule(TEXT) TO authenticated;

-- Makes every report that is due (run by pg_cron every 15 minutes). An organisation whose plan has ended is
-- skipped and its next time moved past now, so renewing does not release a backlog of reports.
CREATE OR REPLACE FUNCTION public.run_scheduled_reports()
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_org   RECORD;
  v_step  INTERVAL;
  v_end   TIMESTAMPTZ;
  v_made  INT := 0;
  v_label TEXT;
BEGIN
  FOR v_org IN
    SELECT * FROM public.organisations
     WHERE "deletedAt" IS NULL AND "reportSchedule" <> 'OFF'
       AND "reportScheduleNextAt" IS NOT NULL AND "reportScheduleNextAt" <= now()
     FOR UPDATE SKIP LOCKED
  LOOP
    v_step := public.report_schedule_step(v_org."reportSchedule");
    v_end  := v_org."reportScheduleNextAt";
    IF public.effective_plan(v_org.plan, v_org."planRenewsAt") <> 'FREE' THEN
      v_label := lower(v_org."reportSchedule");
      INSERT INTO public.reports (id, "orgId", kind, format, status, title, "rangeStart", "rangeEnd",
                                  "zoneIds", "siteIds", "requestedById", "createdAt", "completedAt")
      VALUES (gen_random_uuid(), v_org.id, 'SAFETY_SUMMARY', 'PDF', 'READY',
              format('Scheduled %s safety report, %s to %s', v_label,
                     to_char(v_end - v_step, 'FMDD Mon'), to_char(v_end, 'FMDD Mon YYYY')),
              v_end - v_step, v_end, '{}', '{}', NULL, now(), now());
      INSERT INTO public.audit_logs ("orgId", action, "targetType", metadata)
      VALUES (v_org.id, 'safety.report_scheduled_made', 'safety_report',
              jsonb_build_object('schedule', v_org."reportSchedule", 'since', v_end - v_step, 'until', v_end));
      v_made := v_made + 1;
    END IF;
    -- Next time: one step on, and never in the past (a missed run is not made up several times over).
    v_end := v_end + v_step;
    WHILE v_end <= now() LOOP v_end := v_end + v_step; END LOOP;
    UPDATE public.organisations SET "reportScheduleNextAt" = v_end WHERE id = v_org.id;
  END LOOP;
  RETURN v_made;
END;
$$;
REVOKE ALL ON FUNCTION public.run_scheduled_reports() FROM PUBLIC, anon, authenticated;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('visionworks-scheduled-reports')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'visionworks-scheduled-reports');
    PERFORM cron.schedule('visionworks-scheduled-reports', '*/15 * * * *', 'SELECT public.run_scheduled_reports()');
  END IF;
END $$;

-- ═══ USAGE: what the Plan screen and the reminders read ═════════════════════

DROP FUNCTION IF EXISTS public.plan_usage(UUID);
CREATE OR REPLACE FUNCTION public.plan_usage(p_org_id UUID)
RETURNS TABLE (
  plan "PlanTier",
  effective_plan "PlanTier",
  expired BOOLEAN,
  billing_period "BillingPeriod",
  started_at TIMESTAMPTZ,
  renews_at TIMESTAMPTZ,
  cameras_used INT, cameras_max INT,
  sites_used INT, sites_max INT,
  seats_used INT, seats_max INT,
  retention_days INT, retention_max INT,
  report_schedule TEXT,
  report_next_at TIMESTAMPTZ
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT
    o.plan,
    public.effective_plan(o.plan, o."planRenewsAt"),
    (o.plan <> 'FREE' AND o."planRenewsAt" IS NOT NULL AND o."planRenewsAt" <= now()),
    o."billingPeriod",
    o."planStartedAt",
    o."planRenewsAt",
    (SELECT count(*)::int FROM public.cameras c WHERE c."orgId" = o.id AND c."deletedAt" IS NULL),
    l.max_cameras,
    (SELECT count(*)::int FROM public.sites s WHERE s."orgId" = o.id AND s."deletedAt" IS NULL),
    l.max_sites,
    (SELECT count(*)::int FROM public.memberships m WHERE m."orgId" = o.id AND m.status IN ('ACTIVE','INVITED')),
    l.max_seats,
    public.org_history_days(o."dataRetentionDays", o.plan, o."planRenewsAt"),
    l.max_retention_days,
    o."reportSchedule",
    o."reportScheduleNextAt"
  FROM public.organisations o
  JOIN public.plan_limits l ON l.plan = public.effective_plan(o.plan, o."planRenewsAt")
  WHERE o.id = p_org_id
    AND o."deletedAt" IS NULL;
$$;
GRANT EXECUTE ON FUNCTION public.plan_usage(UUID) TO authenticated;
