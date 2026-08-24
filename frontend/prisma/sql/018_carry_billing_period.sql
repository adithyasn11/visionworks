-- VisionWorks — carry the chosen billing period through signup
--
-- ═══════════════════════════════════════════════════════════════════════════
--  THE BUG
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Reported: "even though I chose yearly, the plan page says billed monthly".
--
-- Reproduced against the live database — the organisation was created with
-- billingPeriod = MONTHLY and a 31-day term, and there was no plan_changed
-- audit row, so nothing had overwritten it. It was wrong from the moment the
-- organisation existed.
--
-- The chain had a missing link at every step:
--
--   1. /home's Monthly|Yearly toggle was CLIENT-ONLY state. Choosing yearly
--      changed the displayed price and was never sent anywhere.
--   2. select_plan() stored only the TIER. There was nowhere to put a period.
--   3. create_organisation() hardcoded 'MONTHLY' (017, lines 345-346).
--
-- So a yearly customer got a monthly term and a monthly renewal date, and the
-- only way to correct it was an explicit change_plan() after the fact. The
-- price shown at checkout was the yearly one; what was recorded was not.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  THE FIX
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Give the pending choice a period alongside its tier, and have
-- create_organisation() consume BOTH. The period now travels the same route
-- the tier already did: /home -> checkout -> select_plan() -> the profile ->
-- create_organisation() -> the organisation.
--
-- `selectedBillingPeriod` is nullable and defaults to MONTHLY at consumption
-- rather than at the column, so a profile written before this migration is
-- indistinguishable from one that genuinely chose monthly — which it is.
--
-- Apply AFTER 017_plan_term.sql.

ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS "selectedBillingPeriod" "BillingPeriod";

COMMENT ON COLUMN public.profiles."selectedBillingPeriod" IS
  'DEMO BILLING. Term chosen on /home before any organisation exists. Consumed and cleared by create_organisation(), like selectedPlan. NULL means monthly was chosen, or the choice predates this column.';

-- ═══════════════════════════════════════════════════════════════════════════
--  select_plan() — now records the term too
-- ═══════════════════════════════════════════════════════════════════════════
--
-- The period argument DEFAULTS, so the existing single-argument call sites keep
-- working while the frontend catches up. PostgREST dispatches on the argument
-- names it is given, so both forms resolve.
CREATE OR REPLACE FUNCTION public.select_plan(
  p_plan   "PlanTier",
  p_period "BillingPeriod" DEFAULT 'MONTHLY'
)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid UUID := (SELECT auth.uid());
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  UPDATE public.profiles
     SET "selectedPlan"          = p_plan,
         "selectedBillingPeriod" = p_period,   -- NEW (018)
         "selectedPlanAt"        = now(),
         "updatedAt"             = now()
   WHERE id = v_uid;

  IF NOT FOUND THEN
    -- The profile row is created by the on_auth_user_created trigger. Missing
    -- means it has not committed yet — a real race in the seconds after a
    -- first OAuth sign-in — so this is retryable rather than fatal.
    RETURN QUERY SELECT FALSE, 'Your profile is still being set up. Try again in a moment.'::TEXT;
    RETURN;
  END IF;

  RETURN QUERY SELECT TRUE, 'Plan recorded.'::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.select_plan("PlanTier", "BillingPeriod") FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.select_plan("PlanTier", "BillingPeriod") TO authenticated;

-- The single-argument overload from 014 still exists and would silently keep
-- writing a monthly-only choice. Dropping it forces every caller onto the form
-- that carries a period, rather than leaving a working call that quietly loses
-- half the information.
DROP FUNCTION IF EXISTS public.select_plan("PlanTier");

-- ═══════════════════════════════════════════════════════════════════════════
--  create_organisation() — consume the period instead of hardcoding it
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Redefined rather than edited in place in 017, so the record of what was
-- applied stays accurate. The ONLY change is the three lines marked NEW (018).
CREATE OR REPLACE FUNCTION public.create_organisation(
  p_name     TEXT,
  p_timezone TEXT DEFAULT 'UTC',
  p_site_name TEXT DEFAULT 'Main site'
)
RETURNS TABLE (org_id UUID, site_id UUID, membership_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid    UUID := auth.uid();
  v_email  TEXT;
  v_org    UUID := gen_random_uuid();
  v_site   UUID := gen_random_uuid();
  v_member UUID := gen_random_uuid();
  v_slug   TEXT;
  v_suffix INT := 0;
  v_plan   "PlanTier";
  v_period "BillingPeriod";   -- NEW (018)
  v_start  TIMESTAMPTZ := now();
BEGIN
  IF v_uid IS NULL THEN
    -- The ERRCODEs are load-bearing: describeDbError() in
    -- app/onboarding/actions.js matches on these messages.
    RAISE EXCEPTION 'create_organisation must be called by an authenticated user'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;
  IF v_email IS NULL THEN
    RAISE EXCEPTION 'No profile for user %', v_uid USING ERRCODE = 'no_data_found';
  END IF;

  v_slug := trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g'));
  IF v_slug = '' THEN
    v_slug := 'org';
  END IF;
  WHILE EXISTS (SELECT 1 FROM public.organisations WHERE slug = v_slug) LOOP
    v_suffix := v_suffix + 1;
    v_slug := trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g'))
              || '-' || v_suffix::text;
  END LOOP;

  -- NEW (018): read BOTH halves of the pending choice. The period defaults to
  -- MONTHLY here rather than at the column, so a profile written before this
  -- migration reads identically to one that genuinely chose monthly.
  SELECT COALESCE("selectedPlan", 'FREE'),
         COALESCE("selectedBillingPeriod", 'MONTHLY')
    INTO v_plan, v_period
    FROM public.profiles WHERE id = v_uid;

  INSERT INTO public.organisations
    (id, name, slug, timezone, plan, "planSelectedAt",
     "billingPeriod", "planStartedAt", "planRenewsAt",
     "createdAt", "updatedAt")
  VALUES
    (v_org, p_name, v_slug, p_timezone, v_plan,
     CASE WHEN v_plan = 'FREE' THEN NULL ELSE v_start END,
     v_period, v_start,                                    -- NEW (018)
     public.plan_term_end(v_plan, v_period, v_start),      -- NEW (018)
     now(), now());

  INSERT INTO public.sites (id, "orgId", name, timezone, "createdAt", "updatedAt")
  VALUES (v_site, v_org, p_site_name, p_timezone, now(), now());

  INSERT INTO public.memberships
    (id, "orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "createdAt", "updatedAt")
  VALUES
    (v_member, v_org, v_uid, 'ADMIN', 'ACTIVE', v_email, now(), now(), now());

  -- Both halves of the choice are spent together. Leaving the period behind
  -- would silently apply it to a SECOND organisation created later.
  UPDATE public.profiles
     SET "currentOrgId"          = v_org,
         "onboardedAt"           = COALESCE("onboardedAt", now()),
         "selectedPlan"          = NULL,
         "selectedBillingPeriod" = NULL,   -- NEW (018)
         "updatedAt"             = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org, v_uid, v_email,
     'organisation.created', 'Organisation', v_org::text,
     jsonb_build_object('name', p_name, 'slug', v_slug, 'timezone', p_timezone,
                        'plan', v_plan, 'period', v_period), now());

  RETURN QUERY SELECT v_org, v_site, v_member;
END;
$$;

REVOKE ALL ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) TO authenticated;
