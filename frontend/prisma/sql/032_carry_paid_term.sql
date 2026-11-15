-- 032_carry_paid_term.sql
--
-- THE PROBLEM
--   Deleting an organisation on a paid plan threw the rest of the paid term away. The administrator landed on
--   Home with no organisation, and creating a new one meant choosing (and paying for) a plan again, although the
--   old term had days or months left on it.
--
-- THE FIX
--   soft_delete_organisation()  when a paid term still has time left, it is kept on the deleting administrator's
--                               profile (the "carried" columns): the tier, the billing period, and the dates.
--   create_organisation()       the next organisation created by that person continues the SAME term: same tier,
--                               same period, same end date. It does not restart, and it does not extend.
--   select_plan()               choosing a DIFFERENT tier or period at checkout gives the carried term up (the
--                               checkout screen says so before they confirm); choosing the same one keeps it.
--   restore                     if an operator restores the deleted organisation, the time goes back to it: an
--                               unused carried term is cleared, and a term already moved to a new organisation
--                               ends on the restored one, so the same paid time is never counted twice.
--   guard                       the carried columns cannot be written directly through the API, only by these
--                               functions (same rule as the organisation's own billing columns, 029).
--
-- Idempotent: safe to run again. Apply AFTER 031_org_slug_length.sql.

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS "carriedPlan" "PlanTier";
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS "carriedBillingPeriod" "BillingPeriod";
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS "carriedPlanStartedAt" TIMESTAMPTZ;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS "carriedPlanRenewsAt" TIMESTAMPTZ;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS "carriedFromOrgId" UUID;

ALTER TABLE public.organisations ADD COLUMN IF NOT EXISTS "termCarriedFromOrgId" UUID;

COMMENT ON COLUMN public.profiles."carriedPlanRenewsAt" IS
  'DEMO BILLING. End of a paid term kept when this person deleted a paid organisation; the next organisation they create continues it. Ignored once it has passed.';
COMMENT ON COLUMN public.organisations."termCarriedFromOrgId" IS
  'The deleted organisation whose paid term this one continued, if any.';

-- ═══ GUARD: the carried term changes only through the functions ═════════════

CREATE OR REPLACE FUNCTION public.guard_carried_term()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF current_user IN ('authenticated', 'anon') AND (
       NEW."carriedPlan" IS DISTINCT FROM OLD."carriedPlan"
    OR NEW."carriedBillingPeriod" IS DISTINCT FROM OLD."carriedBillingPeriod"
    OR NEW."carriedPlanStartedAt" IS DISTINCT FROM OLD."carriedPlanStartedAt"
    OR NEW."carriedPlanRenewsAt" IS DISTINCT FROM OLD."carriedPlanRenewsAt"
    OR NEW."carriedFromOrgId" IS DISTINCT FROM OLD."carriedFromOrgId") THEN
    RAISE EXCEPTION 'billing_columns: the plan and its term can only be changed from the Plan screen.'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS profile_guard_carried_term ON public.profiles;
CREATE TRIGGER profile_guard_carried_term
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.guard_carried_term();

-- The organisation's new column is billing too.
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
    OR NEW."reportScheduleNextAt" IS DISTINCT FROM OLD."reportScheduleNextAt"
    OR NEW."termCarriedFromOrgId" IS DISTINCT FROM OLD."termCarriedFromOrgId") THEN
    RAISE EXCEPTION 'billing_columns: the plan and its term can only be changed from the Plan screen.'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END;
$$;

-- ═══ DELETE: keep what is left of a paid term ═══════════════════════════════

CREATE OR REPLACE FUNCTION public.soft_delete_organisation(p_org_id uuid)
 RETURNS TABLE(ok boolean, message text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  v_uid   UUID := (SELECT auth.uid());
  v_name  TEXT;
  v_email TEXT;
  v_org   RECORD;
  v_kept  BOOLEAN := FALSE;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.memberships
    WHERE "orgId" = p_org_id AND "profileId" = v_uid AND status = 'ACTIVE' AND role = 'ADMIN'
  ) THEN
    RETURN QUERY SELECT FALSE, 'Only an administrator of this organisation can delete it.'::TEXT;
    RETURN;
  END IF;

  SELECT name, plan, "billingPeriod", "planStartedAt", "planRenewsAt" INTO v_org
    FROM public.organisations WHERE id = p_org_id AND "deletedAt" IS NULL;
  v_name := v_org.name;

  IF v_name IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That organisation is not available to delete.'::TEXT;
    RETURN;
  END IF;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;

  -- Keep the rest of a paid term for the person deleting (the administrator, who chose and paid for it).
  -- A term they already carry from an earlier deletion is replaced only by one that lasts longer.
  IF v_org.plan <> 'FREE' AND v_org."planRenewsAt" IS NOT NULL AND v_org."planRenewsAt" > now() THEN
    UPDATE public.profiles
       SET "carriedPlan"          = v_org.plan,
           "carriedBillingPeriod" = v_org."billingPeriod",
           "carriedPlanStartedAt" = v_org."planStartedAt",
           "carriedPlanRenewsAt"  = v_org."planRenewsAt",
           "carriedFromOrgId"     = p_org_id,
           "updatedAt"            = now()
     WHERE id = v_uid
       AND ("carriedPlanRenewsAt" IS NULL OR "carriedPlanRenewsAt" <= now() OR "carriedPlanRenewsAt" < v_org."planRenewsAt");
    v_kept := FOUND;
  END IF;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), p_org_id, v_uid, v_email,
     'organisation.deleted', 'Organisation', p_org_id::text,
     jsonb_build_object('name', v_name, 'soft', true, 'term_kept', v_kept,
                        'plan', v_org.plan, 'renews_at', v_org."planRenewsAt"), now());

  UPDATE public.organisations
     SET "deletedAt" = now(),
         slug        = left('deleted-' || replace(p_org_id::text, '-', ''), 80),
         "updatedAt" = now()
   WHERE id = p_org_id;

  UPDATE public.memberships
     SET status = 'SUSPENDED', "updatedAt" = now()
   WHERE "orgId" = p_org_id AND status IN ('ACTIVE', 'INVITED');

  UPDATE public.profiles
     SET "currentOrgId" = NULL, "onboardedAt" = NULL, "updatedAt" = now()
   WHERE "currentOrgId" = p_org_id;

  RETURN QUERY SELECT TRUE, (v_name || ' has been deleted.')::TEXT;
END;
$function$;

-- ═══ CREATE: continue a carried term ════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.create_organisation(p_name text, p_timezone text DEFAULT 'UTC'::text, p_site_name text DEFAULT 'Main site'::text)
 RETURNS TABLE(org_id uuid, site_id uuid, membership_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  v_uid    UUID := auth.uid();
  v_email  TEXT;
  v_org    UUID := gen_random_uuid();
  v_site   UUID := gen_random_uuid();
  v_member UUID := gen_random_uuid();
  v_slug   TEXT;
  v_suffix INT := 0;
  v_plan   "PlanTier";
  v_period "BillingPeriod";
  v_start  TIMESTAMPTZ := now();
  v_end    TIMESTAMPTZ;
  v_from   UUID;
  v_p      RECORD;
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'create_organisation must be called by an authenticated user'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT email, "selectedPlan", "selectedBillingPeriod", "carriedPlan", "carriedBillingPeriod",
         "carriedPlanStartedAt", "carriedPlanRenewsAt", "carriedFromOrgId"
    INTO v_p FROM public.profiles WHERE id = v_uid;
  v_email := v_p.email;
  IF v_email IS NULL THEN
    RAISE EXCEPTION 'No profile for user %', v_uid USING ERRCODE = 'no_data_found';
  END IF;

  v_slug := trim(both '-' from left(trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g')), 60));
  IF v_slug = '' THEN
    v_slug := 'org';
  END IF;
  WHILE EXISTS (SELECT 1 FROM public.organisations WHERE slug = v_slug) LOOP
    v_suffix := v_suffix + 1;
    v_slug := trim(both '-' from left(trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g')), 60))
              || '-' || v_suffix::text;
  END LOOP;

  IF v_p."carriedPlan" IS NOT NULL AND v_p."carriedPlanRenewsAt" > now() THEN
    -- The same paid term goes on: its tier, period, start and end date.
    v_plan   := v_p."carriedPlan";
    v_period := COALESCE(v_p."carriedBillingPeriod", 'MONTHLY');
    v_start  := COALESCE(v_p."carriedPlanStartedAt", now());
    v_end    := v_p."carriedPlanRenewsAt";
    v_from   := v_p."carriedFromOrgId";
  ELSE
    v_plan   := COALESCE(v_p."selectedPlan", 'FREE');
    v_period := COALESCE(v_p."selectedBillingPeriod", 'MONTHLY');
    v_end    := public.plan_term_end(v_plan, v_period, v_start);
  END IF;

  INSERT INTO public.organisations
    (id, name, slug, timezone, plan, "planSelectedAt",
     "billingPeriod", "planStartedAt", "planRenewsAt", "termCarriedFromOrgId",
     "createdAt", "updatedAt")
  VALUES
    (v_org, p_name, v_slug, p_timezone, v_plan,
     CASE WHEN v_plan = 'FREE' THEN NULL ELSE v_start END,
     v_period, v_start, v_end, v_from,
     now(), now());

  INSERT INTO public.sites (id, "orgId", name, timezone, "createdAt", "updatedAt")
  VALUES (v_site, v_org, p_site_name, p_timezone, now(), now());

  INSERT INTO public.memberships
    (id, "orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "createdAt", "updatedAt")
  VALUES
    (v_member, v_org, v_uid, 'ADMIN', 'ACTIVE', v_email, now(), now(), now());

  -- The pending choice and the carried term are both spent here.
  UPDATE public.profiles
     SET "currentOrgId"          = v_org,
         "onboardedAt"           = COALESCE("onboardedAt", now()),
         "selectedPlan"          = NULL,
         "selectedBillingPeriod" = NULL,
         "carriedPlan"           = NULL,
         "carriedBillingPeriod"  = NULL,
         "carriedPlanStartedAt"  = NULL,
         "carriedPlanRenewsAt"   = NULL,
         "carriedFromOrgId"      = NULL,
         "updatedAt"             = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org, v_uid, v_email,
     'organisation.created', 'Organisation', v_org::text,
     jsonb_build_object('name', p_name, 'slug', v_slug, 'timezone', p_timezone,
                        'plan', v_plan, 'period', v_period, 'term_carried_from', v_from), now());

  RETURN QUERY SELECT v_org, v_site, v_member;
END;
$function$;

-- ═══ CHECKOUT: a different choice gives the carried term up ═════════════════

CREATE OR REPLACE FUNCTION public.select_plan(p_plan "PlanTier", p_period "BillingPeriod" DEFAULT 'MONTHLY'::"BillingPeriod")
 RETURNS TABLE(ok boolean, message text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  v_uid UUID := (SELECT auth.uid());
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  UPDATE public.profiles
     SET "selectedPlan"          = p_plan,
         "selectedBillingPeriod" = p_period,
         "selectedPlanAt"        = now(),
         -- Same tier and period: the carried term stays and is used. Anything else replaces it.
         "carriedPlan"          = CASE WHEN "carriedPlan" = p_plan AND "carriedBillingPeriod" = p_period THEN "carriedPlan" END,
         "carriedBillingPeriod" = CASE WHEN "carriedPlan" = p_plan AND "carriedBillingPeriod" = p_period THEN "carriedBillingPeriod" END,
         "carriedPlanStartedAt" = CASE WHEN "carriedPlan" = p_plan AND "carriedBillingPeriod" = p_period THEN "carriedPlanStartedAt" END,
         "carriedPlanRenewsAt"  = CASE WHEN "carriedPlan" = p_plan AND "carriedBillingPeriod" = p_period THEN "carriedPlanRenewsAt" END,
         "carriedFromOrgId"     = CASE WHEN "carriedPlan" = p_plan AND "carriedBillingPeriod" = p_period THEN "carriedFromOrgId" END,
         "updatedAt"             = now()
   WHERE id = v_uid;

  IF NOT FOUND THEN
    RETURN QUERY SELECT FALSE, 'Your profile is still being set up. Try again in a moment.'::TEXT;
    RETURN;
  END IF;

  RETURN QUERY SELECT TRUE, 'Plan recorded.'::TEXT;
END;
$function$;

-- ═══ RESTORE: the paid time is never counted twice ══════════════════════════

CREATE OR REPLACE FUNCTION public.return_carried_term_on_restore()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF OLD."deletedAt" IS NOT NULL AND NEW."deletedAt" IS NULL THEN
    -- Not used yet: it simply goes back to the restored organisation.
    UPDATE public.profiles
       SET "carriedPlan" = NULL, "carriedBillingPeriod" = NULL, "carriedPlanStartedAt" = NULL,
           "carriedPlanRenewsAt" = NULL, "carriedFromOrgId" = NULL, "updatedAt" = now()
     WHERE "carriedFromOrgId" = NEW.id;
    -- Already continued by a new organisation: the restored one's term ends now.
    IF EXISTS (SELECT 1 FROM public.organisations WHERE "termCarriedFromOrgId" = NEW.id AND "deletedAt" IS NULL)
       AND NEW."planRenewsAt" > now() THEN
      NEW."planRenewsAt" := now();
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS org_return_carried_term ON public.organisations;
CREATE TRIGGER org_return_carried_term
  BEFORE UPDATE OF "deletedAt" ON public.organisations
  FOR EACH ROW EXECUTE FUNCTION public.return_carried_term_on_restore();
