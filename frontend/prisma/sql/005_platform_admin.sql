-- VisionWorks — platform operator access
--
-- WHO THIS IS FOR
--
-- The people running VisionWorks itself: you. Not a customer, not an org admin.
-- A platform admin answers support tickets, checks whether a customer's
-- pipeline is stuck, and suspends accounts that stop paying.
--
-- THE BOUNDARY, AND WHY IT IS NARROW
--
-- A platform admin can see:
--     that an organisation exists, its name, when it signed up, how many
--     members and cameras it has, its storage footprint, whether its last
--     processing run failed and with what error.
--
-- A platform admin CANNOT see:
--     zone_minute_stats, zone_day_stats, alerts, reports — any occupancy
--     figure whatsoever. Not by policy, by absence of grant.
--
-- This matters because /security tells customers their data is isolated per
-- organisation. If the vendor could read everyone's occupancy numbers that
-- claim would be false, and the honest version — "even we cannot see how busy
-- your office is" — is a much stronger thing to be able to say. The console
-- can answer "is their processing broken?" without ever seeing their numbers.
--
-- ORGANISATIONS ARE STILL SELF-SERVE. Customers sign up and
-- create_organisation() makes them their own ADMIN. This file does not add an
-- org-creation path for the vendor: putting a manual vendor step in front of
-- every signup is a bottleneck, not a feature.
--
-- Apply AFTER 004_secrets_and_retention.sql, and after `prisma migrate` has
-- created platform_admins and platform_audit_logs.

-- ═══════════════════════════════════════════════════════════════════════════
--  1. THE PREDICATE
-- ═══════════════════════════════════════════════════════════════════════════

-- Is the caller an active platform operator?
--
-- STABLE + used as `(SELECT public.is_platform_admin())` at every call site so
-- it is evaluated once per query, not once per row — the same lesson as the
-- tenant policies in 003, where the per-row form cost 8.6s on 824k rows.
CREATE OR REPLACE FUNCTION public.is_platform_admin()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.platform_admins
    WHERE "profileId" = (SELECT auth.uid())
      AND "revokedAt" IS NULL
  );
$$;

GRANT EXECUTE ON FUNCTION public.is_platform_admin() TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  2. RLS ON THE PLATFORM TABLES THEMSELVES
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.platform_admins    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_audit_logs ENABLE ROW LEVEL SECURITY;

-- Only platform admins can see who the platform admins are. A customer has no
-- business enumerating the vendor's staff.
CREATE POLICY platform_admins_select ON public.platform_admins
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- Granting platform access is deliberately NOT possible through the API.
-- There is no INSERT, UPDATE or DELETE policy: privilege escalation to
-- platform level should require a deliberate act with database credentials,
-- not a bug in a route handler. Use grant_platform_admin() below, which runs
-- as the definer and is revoked from `authenticated`.

CREATE POLICY platform_audit_select ON public.platform_audit_logs
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

CREATE POLICY platform_audit_insert ON public.platform_audit_logs
  FOR INSERT TO authenticated
  WITH CHECK (
    (SELECT public.is_platform_admin())
    AND ("actorId" IS NULL OR "actorId" = (SELECT auth.uid()))
  );

-- Append-only, like audit_logs. No UPDATE, no DELETE policy — a log the actor
-- can edit is not a log. This matters more here than anywhere else in the
-- schema, because a platform admin is the one actor RLS does not constrain;
-- the record of what they did is the accountability that replaces the missing
-- enforcement.

REVOKE ALL     ON public.platform_admins     FROM anon, authenticated;
GRANT  SELECT  ON public.platform_admins     TO authenticated;
REVOKE ALL     ON public.platform_audit_logs FROM anon, authenticated;
GRANT  SELECT, INSERT ON public.platform_audit_logs TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  3. WIDEN THE EXISTING POLICIES — METADATA ONLY
-- ═══════════════════════════════════════════════════════════════════════════

-- Additional permissive policies. Postgres ORs multiple SELECT policies
-- together, so these grant platform read access on top of the tenant rules in
-- 003 without touching them. Adding rather than editing keeps the tenant
-- boundary reviewable on its own.
--
-- Note precisely which tables appear here — and which do not.

-- Org list: the core of the support console.
CREATE POLICY org_select_platform ON public.organisations
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- Suspension / retention correction on a support ticket.
CREATE POLICY org_update_platform ON public.organisations
  FOR UPDATE TO authenticated
  USING ((SELECT public.is_platform_admin()))
  WITH CHECK ((SELECT public.is_platform_admin()));

-- Member counts and "who is the admin of this org" for support.
CREATE POLICY membership_select_platform ON public.memberships
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

CREATE POLICY profile_select_platform ON public.profiles
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- Infrastructure health: is the camera online, is the site configured.
CREATE POLICY site_select_platform ON public.sites
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

CREATE POLICY camera_select_platform ON public.cameras
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- Zone *configuration* is visible (how many zones, what type, are they set up
-- at all) — the occupancy measured inside them is not.
CREATE POLICY zone_select_platform ON public.zones
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- "Customer says processing is broken." Status and error message are exactly
-- what a support ticket needs.
CREATE POLICY session_select_platform ON public.analysis_sessions
  FOR SELECT TO authenticated
  USING ((SELECT public.is_platform_admin()));

-- ── DELIBERATELY ABSENT ──────────────────────────────────────────────────
--
--   zone_minute_stats   occupancy measurements
--   zone_day_stats      occupancy rollups
--   alerts              reveal occupancy through their triggered values
--   alert_rules         reveal how a customer configures thresholds
--   reports             contain occupancy figures
--   audit_logs          the customer's own internal activity
--
-- No platform policy is created on any of these. `authenticated` also holds no
-- write grant on the analytics tables (see 003), so a platform admin is
-- blocked twice: no policy grants the read, and no grant permits the write.
--
-- If a support case genuinely needs a customer's numbers, the customer exports
-- them and sends them. That is a consent-bearing act, and it leaves a Report
-- row behind.

-- ═══════════════════════════════════════════════════════════════════════════
--  4. THE CONSOLE VIEW
-- ═══════════════════════════════════════════════════════════════════════════

-- One row per organisation with everything the support console shows.
--
-- `security_invoker = true` so the view runs with the CALLER's permissions and
-- honours the policies above. Without it the view would run as its owner and
-- become a hole straight through RLS — the same trap as cameras_safe in 004.
CREATE OR REPLACE VIEW public.platform_org_overview
WITH (security_invoker = true)
AS
SELECT
  o.id,
  o.name,
  o.slug,
  o.timezone,
  o."dataRetentionDays",
  o."createdAt",
  o."deletedAt",
  (o."deletedAt" IS NOT NULL) AS "isSuspended",

  (SELECT count(*) FROM public.memberships m
    WHERE m."orgId" = o.id AND m.status = 'ACTIVE')          AS "activeMembers",
  (SELECT count(*) FROM public.memberships m
    WHERE m."orgId" = o.id AND m.status = 'INVITED')         AS "pendingInvites",
  (SELECT count(*) FROM public.sites s
    WHERE s."orgId" = o.id AND s."deletedAt" IS NULL)        AS "siteCount",
  (SELECT count(*) FROM public.cameras c
    WHERE c."orgId" = o.id AND c."deletedAt" IS NULL)        AS "cameraCount",
  (SELECT count(*) FROM public.cameras c
    WHERE c."orgId" = o.id AND c.status = 'ERROR')           AS "camerasInError",
  (SELECT count(*) FROM public.zones z
    WHERE z."orgId" = o.id AND z."deletedAt" IS NULL)        AS "zoneCount",

  -- Health signals. Counts and statuses only — never a measurement.
  (SELECT count(*) FROM public.analysis_sessions a
    WHERE a."orgId" = o.id AND a.status = 'ERROR')           AS "failedSessions",
  (SELECT count(*) FROM public.analysis_sessions a
    WHERE a."orgId" = o.id AND a.status = 'PROCESSING')      AS "runningSessions",
  (SELECT max(a."finishedAt") FROM public.analysis_sessions a
    WHERE a."orgId" = o.id AND a.status = 'DONE')            AS "lastSuccessfulRun",

  -- An org with cameras but no zones has not finished onboarding — the single
  -- most useful support signal, since nothing downstream works without zones.
  (
    (SELECT count(*) FROM public.cameras c WHERE c."orgId" = o.id) > 0
    AND (SELECT count(*) FROM public.zones z WHERE z."orgId" = o.id) = 0
  ) AS "needsOnboardingHelp"

FROM public.organisations o;

REVOKE ALL ON public.platform_org_overview FROM anon;
GRANT SELECT ON public.platform_org_overview TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  5. BOOTSTRAP AND GRANT
-- ═══════════════════════════════════════════════════════════════════════════

-- Promote a user to platform admin, by email.
--
-- SECURITY DEFINER and revoked from `authenticated`, so it is reachable only
-- with database credentials — the Supabase SQL editor, or a server-side
-- service-role connection. There is intentionally no API path to platform
-- privilege.
CREATE OR REPLACE FUNCTION public.grant_platform_admin(
  p_email TEXT,
  p_note  TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_profile UUID;
  v_actor   UUID := (SELECT auth.uid());  -- NULL when run from the SQL editor
BEGIN
  SELECT id INTO v_profile FROM public.profiles WHERE email = lower(p_email);

  IF v_profile IS NULL THEN
    RAISE EXCEPTION
      'No profile for %. They must sign up first — the trigger in 002 creates the profile.',
      p_email
      USING ERRCODE = 'no_data_found';
  END IF;

  INSERT INTO public.platform_admins ("profileId", note, "grantedById", "grantedAt")
  VALUES (v_profile, p_note, v_actor, now())
  ON CONFLICT ("profileId") DO UPDATE
    SET "revokedAt" = NULL,           -- re-granting un-revokes
        note        = COALESCE(EXCLUDED.note, public.platform_admins.note);

  INSERT INTO public.platform_audit_logs (id, "actorId", action, metadata, "createdAt")
  VALUES (gen_random_uuid(), v_actor, 'platform.admin_granted',
          jsonb_build_object('email', lower(p_email), 'note', p_note), now());

  RETURN v_profile;
END;
$$;

CREATE OR REPLACE FUNCTION public.revoke_platform_admin(p_email TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_profile UUID;
  v_actor   UUID := (SELECT auth.uid());
BEGIN
  SELECT id INTO v_profile FROM public.profiles WHERE email = lower(p_email);
  IF v_profile IS NULL THEN
    RETURN FALSE;
  END IF;

  UPDATE public.platform_admins
     SET "revokedAt" = now()
   WHERE "profileId" = v_profile AND "revokedAt" IS NULL;

  INSERT INTO public.platform_audit_logs (id, "actorId", action, metadata, "createdAt")
  VALUES (gen_random_uuid(), v_actor, 'platform.admin_revoked',
          jsonb_build_object('email', lower(p_email)), now());

  RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.grant_platform_admin(TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.revoke_platform_admin(TEXT)      FROM PUBLIC, anon, authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  HOW TO MAKE YOURSELF A PLATFORM ADMIN
-- ═══════════════════════════════════════════════════════════════════════════
--
--   1. Sign up normally through /signup. The trigger in 002 creates your
--      profile.
--   2. In the Supabase SQL editor:
--
--        select public.grant_platform_admin('you@example.com', 'founder');
--
--   3. Confirm:
--
--        select * from public.platform_org_overview;
--
--      You will see every organisation with its member and camera counts, and
--      no occupancy figures anywhere. Try it and see:
--
--        select count(*) from public.zone_minute_stats;   -- returns 0 rows
--
--      Zero, not an error — RLS filters rather than refusing. That is the
--      boundary working: as a platform admin you can see that customers exist
--      and whether their pipelines are healthy, and you cannot see how busy
--      their offices are.
