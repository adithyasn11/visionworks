-- VisionWorks — bridging Supabase Auth to the application schema
--
-- THE PROBLEM THIS SOLVES
--
-- Supabase owns `auth.users`: passwords, sessions, OAuth identities. That
-- table is not Prisma-managed and cannot be extended with a role or an
-- organisation. So today, signup produces a user the application knows nothing
-- about — no name, no org, no role.
--
-- These triggers close that gap. When Supabase creates an auth user, Postgres
-- immediately creates the matching `profiles` row, and if the person was
-- invited, activates their membership and drops them straight into the right
-- organisation with the right role.
--
-- Doing it in the database rather than in the signup handler matters: OAuth
-- sign-ups never touch your signup form at all. A user arriving through the
-- Google button would otherwise get no profile. The trigger catches every
-- path into `auth.users`, including ones your code never sees.
--
-- Apply AFTER 001_constraints.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  1. PROFILE CREATION
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
-- SECURITY DEFINER because the inserting role is Supabase's auth service,
-- which has no rights on `public`. Locked search_path prevents a schema
-- shadowing attack against a definer function.
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_full_name   TEXT;
  v_avatar      TEXT;
  v_email       TEXT;
  v_invite      RECORD;
BEGIN
  v_email := lower(NEW.email);

  -- Name and avatar arrive in different metadata keys depending on the
  -- provider: our signup form sends `full_name`, Google sends `name` and
  -- `picture`. Coalescing here means one code path serves both.
  v_full_name := COALESCE(
    NEW.raw_user_meta_data->>'full_name',
    NEW.raw_user_meta_data->>'name',
    split_part(v_email, '@', 1)
  );
  v_avatar := COALESCE(
    NEW.raw_user_meta_data->>'avatar_url',
    NEW.raw_user_meta_data->>'picture'
  );

  INSERT INTO public.profiles (id, email, "fullName", "avatarUrl", "createdAt", "updatedAt")
  VALUES (NEW.id, v_email, v_full_name, v_avatar, now(), now())
  -- Idempotent: Supabase can replay this on identity linking, and a failed
  -- trigger here would block the signup entirely.
  ON CONFLICT (id) DO UPDATE
    SET email       = EXCLUDED.email,
        "fullName"  = COALESCE(public.profiles."fullName", EXCLUDED."fullName"),
        "avatarUrl" = COALESCE(EXCLUDED."avatarUrl", public.profiles."avatarUrl"),
        "updatedAt" = now();

  -- ── Pending invitation? Activate it. ──
  -- This is what makes "invite a manager by email" work end to end: the
  -- membership row already exists in INVITED status, and accepting it is
  -- simply signing up with that address.
  SELECT * INTO v_invite
  FROM public.memberships
  WHERE "invitedEmail" = v_email
    AND status = 'INVITED'
    AND ("inviteExpiresAt" IS NULL OR "inviteExpiresAt" > now())
  ORDER BY "createdAt" ASC
  LIMIT 1;

  IF v_invite.id IS NOT NULL THEN
    UPDATE public.memberships
       SET "profileId"       = NEW.id,
           status            = 'ACTIVE',
           "acceptedAt"      = now(),
           "inviteTokenHash" = NULL,   -- single use; burn it
           "updatedAt"       = now()
     WHERE id = v_invite.id;

    -- Land them in the org they were invited to, so they skip onboarding.
    UPDATE public.profiles
       SET "currentOrgId" = v_invite."orgId",
           "onboardedAt"  = now(),
           "updatedAt"    = now()
     WHERE id = NEW.id;

    INSERT INTO public.audit_logs
      (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
    VALUES
      (gen_random_uuid(), v_invite."orgId", NEW.id, v_email,
       'member.invite_accepted', 'Membership', v_invite.id::text,
       jsonb_build_object('role', v_invite.role), now());
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_auth_user();

-- ═══════════════════════════════════════════════════════════════════════════
--  2. EMAIL CHANGES
-- ═══════════════════════════════════════════════════════════════════════════

-- Keep the denormalised email in sync. Without this, a user who changes their
-- address in Supabase would still show the old one throughout the app.
CREATE OR REPLACE FUNCTION public.handle_auth_user_email_change()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.email IS DISTINCT FROM OLD.email THEN
    UPDATE public.profiles
       SET email = lower(NEW.email), "updatedAt" = now()
     WHERE id = NEW.id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_email_changed ON auth.users;
CREATE TRIGGER on_auth_user_email_changed
  AFTER UPDATE OF email ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_auth_user_email_change();

-- ═══════════════════════════════════════════════════════════════════════════
--  3. ORGANISATION CREATION  (the /onboarding transaction)
-- ═══════════════════════════════════════════════════════════════════════════

-- Creating an org, its first site, the admin membership and the audit entry
-- must be atomic. Half of it succeeding leaves a user who can neither use the
-- app nor retry onboarding — an unrecoverable account.
--
-- Called from the app as:
--   select * from public.create_organisation('Acme Ltd', 'Asia/Kolkata', 'HQ');
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
BEGIN
  IF v_uid IS NULL THEN
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

  INSERT INTO public.organisations (id, name, slug, timezone, "createdAt", "updatedAt")
  VALUES (v_org, p_name, v_slug, p_timezone, now(), now());

  INSERT INTO public.sites (id, "orgId", name, timezone, "createdAt", "updatedAt")
  VALUES (v_site, v_org, p_site_name, p_timezone, now(), now());

  -- The creator is the first admin. Inserted directly as ACTIVE, which is the
  -- one legitimate case of a membership that was never invited.
  INSERT INTO public.memberships
    (id, "orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "createdAt", "updatedAt")
  VALUES
    (v_member, v_org, v_uid, 'ADMIN', 'ACTIVE', v_email, now(), now(), now());

  UPDATE public.profiles
     SET "currentOrgId" = v_org,
         "onboardedAt"  = COALESCE("onboardedAt", now()),
         "updatedAt"    = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_org, v_uid, v_email,
     'organisation.created', 'Organisation', v_org::text,
     jsonb_build_object('name', p_name, 'slug', v_slug, 'timezone', p_timezone), now());

  RETURN QUERY SELECT v_org, v_site, v_member;
END;
$$;

REVOKE ALL ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_organisation(TEXT, TEXT, TEXT) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  4. AUTHORISATION HELPERS  (used by every RLS policy in 003)
-- ═══════════════════════════════════════════════════════════════════════════

-- Every organisation the caller actively belongs to.
--
-- SET-RETURNING, deliberately. This is the single most important performance
-- decision in the whole schema, and it was measured rather than assumed:
--
--   USING (can_read_org("orgId"))              8,623 ms   <- boolean per row
--   USING ("orgId" IN (SELECT user_org_ids()))   126 ms   <- hashed set, once
--
-- on 824,000 minute buckets. A 68x difference, identical results.
--
-- `STABLE` alone does NOT save you. Postgres still evaluates a boolean
-- function once per candidate row — `EXPLAIN ANALYZE` shows `loops=824312`.
-- Only a set the planner can materialise and hash-join collapses that to one
-- evaluation. So every policy is written as `orgId IN (SELECT ...)`, never as
-- a boolean helper call.
CREATE OR REPLACE FUNCTION public.user_org_ids()
RETURNS SETOF UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT "orgId"
  FROM public.memberships
  WHERE "profileId" = (SELECT auth.uid())
    AND status = 'ACTIVE';
$$;

-- Role-scoped variants of the same idea. These are what the policies actually
-- use, so the permission matrix in DOCUMENTATION.md §9 is expressed as
-- "which orgs may I do this in" rather than "may I do this here".

-- Orgs where the caller is an ADMIN — governs billing, members, org settings.
CREATE OR REPLACE FUNCTION public.admin_org_ids()
RETURNS SETOF UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT "orgId"
  FROM public.memberships
  WHERE "profileId" = (SELECT auth.uid())
    AND status = 'ACTIVE'
    AND role = 'ADMIN';
$$;

-- Orgs where the caller may configure the space — zones, cameras, alert
-- rules, starting an analysis. ADMIN or MANAGER.
CREATE OR REPLACE FUNCTION public.manage_org_ids()
RETURNS SETOF UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT "orgId"
  FROM public.memberships
  WHERE "profileId" = (SELECT auth.uid())
    AND status = 'ACTIVE'
    AND role IN ('ADMIN', 'MANAGER');
$$;

-- ── Boolean helpers: for APPLICATION CODE, not for RLS policies ───────────
--
-- These answer "may I do X in org Y" for a single known org — exactly what a
-- route handler needs before a mutation, and what returns the 403.
--
-- DO NOT put them in a policy's USING clause. A boolean function in a policy
-- is evaluated once per candidate row (measured: 8.6s vs 126ms on 824k rows).
-- Policies use the set-returning functions above instead.

-- Does the caller hold at least one of these roles in this organisation?
CREATE OR REPLACE FUNCTION public.user_has_role(p_org UUID, p_roles TEXT[])
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.memberships
    WHERE "profileId" = (SELECT auth.uid())
      AND "orgId" = p_org
      AND status = 'ACTIVE'
      AND role::text = ANY (p_roles)
  );
$$;

-- Convenience wrappers matching the permission matrix in DOCUMENTATION.md §9.
CREATE OR REPLACE FUNCTION public.is_org_admin(p_org UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT public.user_has_role(p_org, ARRAY['ADMIN']);
$$;

-- "Can configure" — draw zones, manage cameras, set alert rules, run analysis.
CREATE OR REPLACE FUNCTION public.can_manage_org(p_org UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT public.user_has_role(p_org, ARRAY['ADMIN', 'MANAGER']);
$$;

-- "Can read" — all three roles.
CREATE OR REPLACE FUNCTION public.can_read_org(p_org UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT public.user_has_role(p_org, ARRAY['ADMIN', 'MANAGER', 'VIEWER']);
$$;

GRANT EXECUTE ON FUNCTION public.user_org_ids()                 TO authenticated;
GRANT EXECUTE ON FUNCTION public.admin_org_ids()                TO authenticated;
GRANT EXECUTE ON FUNCTION public.manage_org_ids()               TO authenticated;
GRANT EXECUTE ON FUNCTION public.user_has_role(UUID, TEXT[])    TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_org_admin(UUID)             TO authenticated;
GRANT EXECUTE ON FUNCTION public.can_manage_org(UUID)           TO authenticated;
GRANT EXECUTE ON FUNCTION public.can_read_org(UUID)             TO authenticated;
