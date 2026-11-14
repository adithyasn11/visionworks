-- 031_org_slug_length.sql
--
-- A long organisation name could not be created. The name may be 160 characters, but the slug made from it
-- (lower case, runs of other characters turned into '-') goes into a VARCHAR(80) column and was not shortened,
-- so any name over about 80 characters failed with "Could not create your organisation". The slug is now cut
-- to 60 characters before a numeric suffix is added for uniqueness (so it always fits).
--
-- The function body is otherwise unchanged (taken from the live database). Idempotent.

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

  v_slug := trim(both '-' from left(trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g')), 60));
  IF v_slug = '' THEN
    v_slug := 'org';
  END IF;
  WHILE EXISTS (SELECT 1 FROM public.organisations WHERE slug = v_slug) LOOP
    v_suffix := v_suffix + 1;
    v_slug := trim(both '-' from left(trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g')), 60))
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
$function$;
