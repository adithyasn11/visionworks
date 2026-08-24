-- VisionWorks — real billing terms, and in-app plan changes
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHAT THIS ADDS
-- ═══════════════════════════════════════════════════════════════════════════
--
--   organisations."billingPeriod"   MONTHLY | YEARLY
--   organisations."planStartedAt"   when the current term began
--   organisations."planRenewsAt"    when it ends — a real date, computed here
--
-- The Plan screen previously showed only "Selected 24 August 2026", because
-- that was the only date the schema held. A term with no end is not a
-- subscription, so the end date is STORED rather than derived in the browser:
-- a client computing "+1 month" would drift from whatever a real processor
-- eventually says, and two places computing a renewal date is how they come to
-- disagree.
--
--   change_plan()   an ADMIN switches tier, starting a fresh term
--
-- ═══════════════════════════════════════════════════════════════════════════
--  A NOTE FOR WHOEVER READS THIS NEXT
-- ═══════════════════════════════════════════════════════════════════════════
--
-- No payment processor is connected to this build. `change_plan()` records a
-- choice and moves a date; it does not charge anything, and `planRenewsAt` is
-- a term boundary rather than a promise that money will move on that day.
--
-- The interface no longer says so, by request. That is fine for a controlled
-- demo and NOT fine the moment real users arrive: someone completing a
-- checkout that looks real will believe they paid. Before this is exposed to
-- anyone outside the team, either wire a processor or put the disclosure back.
--
-- WHEN A PROCESSOR IS ADDED, the authority for `plan` moves to its webhook:
--   1. REVOKE UPDATE (plan, "billingPeriod", "planStartedAt", "planRenewsAt")
--      ON public.organisations FROM authenticated;
--   2. write those columns only from a service-role handler that has verified
--      the provider's signature;
--   3. keep change_plan() as the *request*, and let the webhook confirm it.
-- Steps 1 and 2 are deliberately not applied here — they would break the
-- in-app upgrade, which has no webhook to replace it.
--
-- Apply AFTER 016_accept_invite_existing_user.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  BILLING PERIOD
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'BillingPeriod') THEN
    CREATE TYPE "BillingPeriod" AS ENUM ('MONTHLY', 'YEARLY');
  END IF;
END
$$;

ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "billingPeriod" "BillingPeriod" NOT NULL DEFAULT 'MONTHLY';

ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "planStartedAt" TIMESTAMPTZ;

ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "planRenewsAt" TIMESTAMPTZ;

COMMENT ON COLUMN public.organisations."planRenewsAt" IS
  'End of the current billing term. NULL on the free tier, which does not expire. Computed by change_plan()/create_organisation(), never in the browser.';

-- Backfill existing organisations so no row shows a blank term. Anything
-- already on a paid tier gets a term dated from when the plan was chosen;
-- free organisations get a start date and no renewal, because they never end.
UPDATE public.organisations
   SET "planStartedAt" = COALESCE("planStartedAt", "planSelectedAt", "createdAt"),
       "planRenewsAt"  = CASE
         WHEN plan = 'FREE' THEN NULL
         ELSE COALESCE("planRenewsAt",
                       COALESCE("planSelectedAt", "createdAt") + INTERVAL '1 month')
       END
 WHERE "planStartedAt" IS NULL OR ("planRenewsAt" IS NULL AND plan <> 'FREE');

-- ═══════════════════════════════════════════════════════════════════════════
--  TERM ARITHMETIC — ONE PLACE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Every caller that needs "when does this term end" calls this, so the answer
-- cannot differ between org creation, an upgrade, and a future renewal job.
--
-- INTERVAL arithmetic, not "+30 days": Postgres already handles the awkward
-- cases correctly. A term starting 31 January renews 28 February, and 29
-- February renews 28 February the following year. Adding a fixed number of
-- days would slowly drift the renewal date away from the day of the month the
-- customer actually signed up on.
CREATE OR REPLACE FUNCTION public.plan_term_end(
  p_plan   "PlanTier",
  p_period "BillingPeriod",
  p_start  TIMESTAMPTZ
)
RETURNS TIMESTAMPTZ
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    -- The free tier has no term. NULL means "does not expire", and the UI
    -- renders that as a dash rather than inventing a date.
    WHEN p_plan = 'FREE' THEN NULL
    WHEN p_period = 'YEARLY' THEN p_start + INTERVAL '1 year'
    ELSE p_start + INTERVAL '1 month'
  END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  CHANGING PLAN FROM INSIDE THE APP
-- ═══════════════════════════════════════════════════════════════════════════
--
-- SECURITY DEFINER with an explicit ADMIN check, matching every other
-- governance action (soft_delete_organisation, membership management).
-- `org_update` would already refuse a MANAGER, but doing the check here means
-- the caller gets a sentence instead of an opaque policy violation.
CREATE OR REPLACE FUNCTION public.change_plan(
  p_plan   "PlanTier",
  p_period "BillingPeriod" DEFAULT 'MONTHLY'
)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid    UUID := (SELECT auth.uid());
  v_email  TEXT;
  v_org_id UUID;
  v_org    RECORD;
  v_start  TIMESTAMPTZ := now();
  v_end    TIMESTAMPTZ;
  v_lim    RECORD;
  v_cams   INT;
  v_sites  INT;
  v_seats  INT;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  SELECT "currentOrgId" INTO v_org_id FROM public.profiles WHERE id = v_uid;
  IF v_org_id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'No organisation selected.'::TEXT;
    RETURN;
  END IF;

  -- THE AUTHORISATION CHECK: an ACTIVE ADMIN of THIS organisation.
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships
     WHERE "orgId" = v_org_id AND "profileId" = v_uid
       AND status = 'ACTIVE' AND role = 'ADMIN'
  ) THEN
    RETURN QUERY SELECT FALSE, 'Only an administrator can change the plan.'::TEXT;
    RETURN;
  END IF;

  SELECT * INTO v_org FROM public.organisations
   WHERE id = v_org_id AND "deletedAt" IS NULL;
  IF v_org.id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That organisation is no longer available.'::TEXT;
    RETURN;
  END IF;

  -- ── DOWNGRADE GUARD ──
  --
  -- The limit triggers in 015 only fire on INSERT, so nothing would stop an
  -- org dropping to a tier its CURRENT usage already exceeds. That would leave
  -- 8 cameras on a 1-camera plan: every existing row keeps working, every new
  -- one is refused, and the Plan screen shows 8/1 with a bar past its end.
  --
  -- Refusing with the actual numbers is the only honest answer — silently
  -- deleting cameras to fit a cheaper tier would be far worse.
  SELECT * INTO v_lim FROM public.plan_limits WHERE plan = p_plan;

  SELECT count(*) INTO v_cams  FROM public.cameras WHERE "orgId" = v_org_id;
  SELECT count(*) INTO v_sites FROM public.sites   WHERE "orgId" = v_org_id;
  SELECT count(*) INTO v_seats FROM public.memberships
   WHERE "orgId" = v_org_id AND status IN ('ACTIVE', 'INVITED');

  IF v_lim.max_cameras IS NOT NULL AND v_cams > v_lim.max_cameras THEN
    RETURN QUERY SELECT FALSE, format(
      'That plan allows %s camera(s) and this organisation has %s. Remove %s before switching.',
      v_lim.max_cameras, v_cams, v_cams - v_lim.max_cameras)::TEXT;
    RETURN;
  END IF;

  IF v_lim.max_sites IS NOT NULL AND v_sites > v_lim.max_sites THEN
    RETURN QUERY SELECT FALSE, format(
      'That plan allows %s site(s) and this organisation has %s. Remove %s before switching.',
      v_lim.max_sites, v_sites, v_sites - v_lim.max_sites)::TEXT;
    RETURN;
  END IF;

  IF v_lim.max_seats IS NOT NULL AND v_seats > v_lim.max_seats THEN
    RETURN QUERY SELECT FALSE, format(
      'That plan allows %s member(s) and this organisation has %s, including pending invitations. Remove %s before switching.',
      v_lim.max_seats, v_seats, v_seats - v_lim.max_seats)::TEXT;
    RETURN;
  END IF;

  v_end := public.plan_term_end(p_plan, p_period, v_start);

  -- A plan change starts a FRESH term. Carrying the old renewal date forward
  -- would mean upgrading mid-month bought a shorter term than the one before.
  --
  -- `dataRetentionDays` is deliberately NOT touched: the retention cap trigger
  -- (015) clamps it on the way up and leaves an over-cap value alone on a
  -- downgrade, because shortening retention DELETES measurements and a pricing
  -- change must never destroy data as a side effect.
  UPDATE public.organisations
     SET plan             = p_plan,
         "billingPeriod"  = p_period,
         "planSelectedAt" = CASE WHEN p_plan = 'FREE' THEN NULL ELSE v_start END,
         "planStartedAt"  = v_start,
         "planRenewsAt"   = v_end,
         "updatedAt"      = now()
   WHERE id = v_org_id;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org_id, v_uid, v_email,
     'organisation.plan_changed', 'Organisation', v_org_id::text,
     jsonb_build_object('from', v_org.plan, 'to', p_plan, 'period', p_period), now());

  RETURN QUERY SELECT TRUE, 'Your plan has been updated.'::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.change_plan("PlanTier", "BillingPeriod") FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.change_plan("PlanTier", "BillingPeriod") TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  plan_usage() — now carries the term
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Replaces the 015 version. The Plan screen needs the dates and the usage in
-- one round trip, and splitting them across two calls would let the panel
-- render a tier from one moment and a renewal date from another.
DROP FUNCTION IF EXISTS public.plan_usage(UUID);

CREATE OR REPLACE FUNCTION public.plan_usage(p_org_id UUID)
RETURNS TABLE (
  plan "PlanTier",
  billing_period "BillingPeriod",
  started_at TIMESTAMPTZ,
  renews_at TIMESTAMPTZ,
  cameras_used INT, cameras_max INT,
  sites_used INT, sites_max INT,
  seats_used INT, seats_max INT,
  retention_days INT, retention_max INT
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT
    o.plan,
    o."billingPeriod",
    o."planStartedAt",
    o."planRenewsAt",
    (SELECT count(*)::int FROM public.cameras  c WHERE c."orgId" = o.id),
    l.max_cameras,
    (SELECT count(*)::int FROM public.sites    s WHERE s."orgId" = o.id),
    l.max_sites,
    (SELECT count(*)::int FROM public.memberships m
      WHERE m."orgId" = o.id AND m.status IN ('ACTIVE','INVITED')),
    l.max_seats,
    o."dataRetentionDays",
    l.max_retention_days
  FROM public.organisations o
  JOIN public.plan_limits l ON l.plan = o.plan
  WHERE o.id = p_org_id
    AND o."deletedAt" IS NULL;
$$;

REVOKE ALL ON FUNCTION public.plan_usage(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.plan_usage(UUID) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  create_organisation() — stamp the term at creation
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Redefined rather than edited in place in 014, so the historical record of
-- what was applied stays accurate. The ONLY change from the 014 version is the
-- three new columns on the organisations INSERT, marked below.
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
  v_start  TIMESTAMPTZ := now();   -- NEW (017)
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

  SELECT COALESCE("selectedPlan", 'FREE') INTO v_plan
    FROM public.profiles WHERE id = v_uid;

  INSERT INTO public.organisations
    (id, name, slug, timezone, plan, "planSelectedAt",
     "billingPeriod", "planStartedAt", "planRenewsAt",          -- NEW (017)
     "createdAt", "updatedAt")
  VALUES
    (v_org, p_name, v_slug, p_timezone, v_plan,
     CASE WHEN v_plan = 'FREE' THEN NULL ELSE v_start END,
     'MONTHLY', v_start,                                        -- NEW (017)
     public.plan_term_end(v_plan, 'MONTHLY', v_start),          -- NEW (017)
     now(), now());

  INSERT INTO public.sites (id, "orgId", name, timezone, "createdAt", "updatedAt")
  VALUES (v_site, v_org, p_site_name, p_timezone, now(), now());

  INSERT INTO public.memberships
    (id, "orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "createdAt", "updatedAt")
  VALUES
    (v_member, v_org, v_uid, 'ADMIN', 'ACTIVE', v_email, now(), now(), now());

  UPDATE public.profiles
     SET "currentOrgId" = v_org,
         "onboardedAt"  = COALESCE("onboardedAt", now()),
         "selectedPlan" = NULL,
         "updatedAt"    = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org, v_uid, v_email,
     'organisation.created', 'Organisation', v_org::text,
     jsonb_build_object('name', p_name, 'slug', v_slug, 'timezone', p_timezone,
                        'plan', v_plan), now());

  RETURN QUERY SELECT v_org, v_site, v_member;
END;
$$;

REVOKE ALL ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) TO authenticated;
