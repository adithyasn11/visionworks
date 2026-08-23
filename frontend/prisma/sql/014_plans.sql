-- VisionWorks — subscription plans (DEMO BILLING)
--
-- ═══════════════════════════════════════════════════════════════════════════
--  READ THIS FIRST: NO MONEY MOVES ANYWHERE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- This is a demonstration billing flow. There is no payment processor, no card
-- is charged, and NO CARD DETAILS ARE EVER STORED OR TRANSMITTED. The checkout
-- screen collects nothing: it shows the chosen plan, waits, and records the
-- selection. `selectedPlan` is a preference column, exactly like
-- `themePreference` — it is not a receipt and must never be read as proof of
-- payment.
--
-- If real billing is added later, the authority for "has this org paid" MUST
-- come from the payment provider's webhook writing a separate, service-role-only
-- table. Do NOT promote these columns to that role: `profile_update_self` lets
-- a user write their own profile row, so `selectedPlan` is self-asserted by
-- construction. That is fine for a preference and catastrophic for an
-- entitlement. See the note on `plan_is_self_asserted` below.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY THE PLAN IS ON THE PROFILE AND *ALSO* ON THE ORGANISATION
-- ═══════════════════════════════════════════════════════════════════════════
--
-- The user picks a plan BEFORE any organisation exists — that is the whole
-- point of the flow (/home -> checkout -> /onboarding -> /dashboard). So the
-- choice has nowhere to live except the profile at the moment it is made.
--
-- But the plan describes an ORGANISATION's entitlements (camera count, seats,
-- retention), not a person's. So `create_organisation()` copies the profile's
-- pending choice onto the new org, and from then on the ORG column is the one
-- the product reads. The profile column becomes a spent token.
--
-- Both are kept rather than one moved, because they answer different questions:
--   profiles.selectedPlan     "what did this person choose at signup"
--   organisations.plan        "what is this organisation entitled to now"
-- An invited member joining an existing org has the second and never the first.
--
-- Apply AFTER 013_org_soft_delete_fix.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  THE ENUM
-- ═══════════════════════════════════════════════════════════════════════════

-- Created idempotently: this file may be re-run, and CREATE TYPE has no
-- IF NOT EXISTS.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'PlanTier') THEN
    CREATE TYPE "PlanTier" AS ENUM ('FREE', 'GROWTH', 'ENTERPRISE');
  END IF;
END
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  COLUMNS
-- ═══════════════════════════════════════════════════════════════════════════

-- The org's live entitlement. FREE is the default so an organisation created
-- by any path that does not know about plans (the invite trigger, a fixture, a
-- future migration) is still valid and still usable — degrading to the free
-- tier is the only safe default direction.
ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS plan "PlanTier" NOT NULL DEFAULT 'FREE';

-- When the demo checkout was completed for this org. Null on FREE.
ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "planSelectedAt" TIMESTAMPTZ;

-- The person's pending choice, made on /home before any org exists.
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS "selectedPlan" "PlanTier";

ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS "selectedPlanAt" TIMESTAMPTZ;

COMMENT ON COLUMN public.organisations.plan IS
  'DEMO BILLING. Entitlement tier for this organisation. Not proof of payment — no payment processor exists. Copied from profiles."selectedPlan" by create_organisation().';

COMMENT ON COLUMN public.profiles."selectedPlan" IS
  'DEMO BILLING. The plan this person chose on /home before creating an organisation. Self-asserted (profile_update_self permits writing it) — never treat as an entitlement. Consumed by create_organisation().';

-- ═══════════════════════════════════════════════════════════════════════════
--  RECORDING A CHOICE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- SECURITY DEFINER not because this is privileged — `profile_update_self`
-- already allows the write — but because it gives the action ONE well-named
-- door with a validated argument, instead of the Server Action issuing a raw
-- column update on `profiles`. That matters when billing becomes real: this
-- function is the single place to add "and verify the payment intent", and a
-- grep for it finds every caller.
--
-- It deliberately touches ONLY the two plan columns. A definer function that
-- accepted a row would be an escalation path onto `currentOrgId`.
CREATE OR REPLACE FUNCTION public.select_plan(p_plan "PlanTier")
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
     SET "selectedPlan"   = p_plan,
         "selectedPlanAt" = now(),
         "updatedAt"      = now()
   WHERE id = v_uid;

  IF NOT FOUND THEN
    -- The profile row is created by the on_auth_user_created trigger. Missing
    -- means the trigger has not committed yet (a real race in the seconds
    -- after a first OAuth sign-in), so this is retryable rather than fatal.
    RETURN QUERY SELECT FALSE, 'Your profile is still being set up. Try again in a moment.'::TEXT;
    RETURN;
  END IF;

  RETURN QUERY SELECT TRUE, 'Plan recorded.'::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.select_plan("PlanTier") FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.select_plan("PlanTier") TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  CARRYING THE CHOICE INTO THE NEW ORGANISATION
-- ═══════════════════════════════════════════════════════════════════════════
--
-- create_organisation() is redefined here rather than edited in place in
-- 002_auth_triggers.sql, because that file is the historical record of what was
-- applied and rewriting it would make the two disagree.
--
-- THE ONLY BEHAVIOURAL CHANGE is the three lines marked "-- NEW (014)". The
-- rest is reproduced verbatim so the function keeps its atomicity guarantee:
-- organisation + site + membership + profile pointer + audit row all commit
-- together or not at all. That property is the reason this logic lives in
-- Postgres at all (see the header of app/onboarding/actions.js), and splitting
-- the plan write into a separate statement afterwards would break it — an org
-- could exist on the wrong tier if the second statement failed.
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
  v_plan   "PlanTier";   -- NEW (014)
BEGIN
  IF v_uid IS NULL THEN
    -- The ERRCODEs are load-bearing, not decoration: describeDbError() in
    -- app/onboarding/actions.js matches on these messages to turn a policy
    -- violation into a sentence the user can act on.
    RAISE EXCEPTION 'create_organisation must be called by an authenticated user'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;
  IF v_email IS NULL THEN
    RAISE EXCEPTION 'No profile for user %', v_uid USING ERRCODE = 'no_data_found';
  END IF;

  -- Slugify, then de-duplicate. Two companies called "Acme" is normal and
  -- must not fail; the unique index on slug would otherwise reject the second.
  v_slug := trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g'));
  IF v_slug = '' THEN
    v_slug := 'org';
  END IF;
  WHILE EXISTS (SELECT 1 FROM public.organisations WHERE slug = v_slug) LOOP
    v_suffix := v_suffix + 1;
    v_slug := trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g'))
              || '-' || v_suffix::text;
  END LOOP;

  -- NEW (014). Consume the plan chosen on /home. COALESCE to FREE: a user who
  -- reached onboarding without passing through the plans page (an old session,
  -- a direct URL, a future entry point) gets a working free organisation rather
  -- than a NULL tier the product would have to special-case everywhere.
  SELECT COALESCE("selectedPlan", 'FREE') INTO v_plan
    FROM public.profiles WHERE id = v_uid;

  INSERT INTO public.organisations
    (id, name, slug, timezone, plan, "planSelectedAt", "createdAt", "updatedAt")
  VALUES
    (v_org, p_name, v_slug, p_timezone, v_plan,
     CASE WHEN v_plan = 'FREE' THEN NULL ELSE now() END,   -- NEW (014)
     now(), now());

  INSERT INTO public.sites (id, "orgId", name, timezone, "createdAt", "updatedAt")
  VALUES (v_site, v_org, p_site_name, p_timezone, now(), now());

  -- The creator is the first admin. Inserted directly as ACTIVE, which is the
  -- one legitimate case of a membership that was never invited.
  INSERT INTO public.memberships
    (id, "orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "createdAt", "updatedAt")
  VALUES
    (v_member, v_org, v_uid, 'ADMIN', 'ACTIVE', v_email, now(), now(), now());

  -- The pointer that ends onboarding. `selectedPlan` is cleared in the same
  -- statement: it has been spent, and leaving it set would silently apply the
  -- same tier to a SECOND organisation created later by the same person.
  UPDATE public.profiles
     SET "currentOrgId" = v_org,
         "onboardedAt"  = COALESCE("onboardedAt", now()),
         "selectedPlan" = NULL,          -- NEW (014)
         "updatedAt"    = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org, v_uid, v_email,
     'organisation.created', 'Organisation', v_org::text,
     jsonb_build_object('name', p_name, 'slug', v_slug, 'timezone', p_timezone,
                        'plan', v_plan), now());   -- 'plan' added (014)

  RETURN QUERY SELECT v_org, v_site, v_member;
END;
$$;

REVOKE ALL ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  WHY THERE IS NO POLICY STOPPING A USER SETTING plan = 'ENTERPRISE'
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `org_update` already restricts organisation writes to ADMINs of that org
-- (013), so a VIEWER cannot change the tier. An ADMIN can — by calling
-- PostgREST directly, without going through the checkout screen.
--
-- That is ACCEPTED, and stated rather than hidden, because this is demo
-- billing: there is nothing to defraud. The tier gates cosmetic limits, not
-- access to another tenant's data — every cross-tenant boundary is still RLS,
-- and none of it consults `plan`.
--
-- WHAT WOULD HAVE TO CHANGE FOR REAL BILLING:
--   1. Revoke UPDATE on organisations.plan from `authenticated` at the column
--      level:  REVOKE UPDATE (plan, "planSelectedAt") ON public.organisations
--              FROM authenticated;
--   2. Write the tier ONLY from a service-role webhook handler that has
--      verified the provider's signature.
--   3. Drop profiles."selectedPlan" from the trust path entirely — keep it as
--      the "what did they click" analytics field it really is.
-- Steps 1 and 2 are deliberately NOT applied here; applying them would break
-- the demo checkout, which has no webhook to write the column instead.
