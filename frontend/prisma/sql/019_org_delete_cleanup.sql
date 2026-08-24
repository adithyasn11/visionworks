-- VisionWorks — make organisation deletion actually clean up
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHAT WAS WRONG
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `soft_delete_organisation()` (013) set `deletedAt` and released every
-- member's `currentOrgId`. That made the organisation INVISIBLE, and left
-- everything it owned in place. Measured on the live database after two
-- deletions:
--
--   3 memberships, 2 sites, 2 cameras, 6 audit rows  -- all still there
--
-- Two consequences that are actually visible to a user:
--
--   1. THE SLUG IS STILL TAKEN. `organisations_slug_key` is a plain unique
--      index with no `WHERE deletedAt IS NULL`, so a deleted "weddingOS" holds
--      "weddingos" forever. Recreating it produced "weddingos-1" — the reported
--      symptom of "deleting does not really delete".
--
--   2. SEATS AND CAMERAS ARE STILL CONSUMED. The plan-limit triggers (015)
--      count rows by `orgId` without joining `organisations`, so memberships
--      and cameras belonging to a DELETED org still counted against the tier.
--      A user could delete an org and find their new one already at its seat
--      limit for no visible reason.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY NOT JUST HARD-DELETE EVERYTHING
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Because the point of a soft delete is that it is reversible for a grace
-- period, and because `audit_logs` is the record of who did what — destroying
-- it on request is exactly what an audit trail must not permit.
--
-- So this splits the difference along the line that actually matters:
--
--   RELEASED IMMEDIATELY (the things that block the user from moving on)
--     - the slug, freed by renaming it to a `deleted-<id>` form
--     - memberships, moved to SUSPENDED so they stop consuming seats
--
--   KEPT (reversible, and nobody is blocked by them)
--     - sites, cameras, zones, telemetry
--     - audit_logs, in full
--
--   PURGED LATER by `purge_deleted_organisations()`, which the retention job
--   calls. That is where the grace period lives.
--
-- Apply AFTER 018_carry_billing_period.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  0. THE KEEP-AN-ADMIN TRIGGER MUST NOT GUARD A DELETED ORGANISATION
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `assert_org_keeps_an_admin` (001) refuses any statement that would leave an
-- organisation with no ACTIVE admin. That is exactly right while the
-- organisation exists — it is what stops an admin locking their whole company
-- out of its own account.
--
-- It does not consider `deletedAt`, and the new "release the seats" step below
-- suspends every membership including the last admin. So deleting an
-- organisation failed outright:
--
--   Organisation <id> must retain at least one active admin
--
-- Caught by the test rather than in review. The trigger was right and the
-- delete function was wrong: a deleted organisation has no account to be
-- locked out of, so there is no admin to retain.
--
-- Fixed HERE rather than by disabling the trigger around the update, because a
-- DISABLE TRIGGER inside a user-facing function needs table ownership and would
-- suppress the guard for anything else running concurrently.
CREATE OR REPLACE FUNCTION public.assert_org_keeps_an_admin()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  remaining INT;
  target_org UUID;
BEGIN
  target_org := COALESCE(OLD."orgId", NEW."orgId");

  -- NEW (019): a soft-deleted organisation has nobody to lock out.
  IF EXISTS (
    SELECT 1 FROM public.organisations
     WHERE id = target_org AND "deletedAt" IS NOT NULL
  ) THEN
    RETURN COALESCE(NEW, OLD);
  END IF;

  SELECT count(*) INTO remaining
  FROM public.memberships
  WHERE "orgId" = target_org
    AND role = 'ADMIN'
    AND status = 'ACTIVE'
    AND id <> COALESCE(OLD.id, NEW.id);

  -- If the row surviving this statement is itself an active admin, we are fine.
  IF TG_OP <> 'DELETE'
     AND NEW.role = 'ADMIN'
     AND NEW.status = 'ACTIVE' THEN
    RETURN NEW;
  END IF;

  IF remaining = 0 THEN
    RAISE EXCEPTION
      'Organisation % must retain at least one active admin', target_org
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  1. STOP DELETED ORGS CONSUMING PLAN ALLOWANCES
-- ═══════════════════════════════════════════════════════════════════════════
--
-- The three limit triggers counted rows for an org without asking whether that
-- org still exists. Redefined here to join `organisations` and ignore deleted
-- ones — otherwise a member could be blocked by seats held by an organisation
-- they can no longer see.

CREATE OR REPLACE FUNCTION public.enforce_seat_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  -- Deleted organisations have no live tier to enforce.
  SELECT plan INTO v_plan FROM public.organisations
   WHERE id = NEW."orgId" AND "deletedAt" IS NULL;
  IF v_plan IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT max_seats INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  -- INVITED still holds a seat: an outstanding invitation is a seat kept open,
  -- and not counting it would let an admin invite twenty people onto a
  -- three-seat plan and only discover the problem as they accepted.
  SELECT count(*) INTO v_count
    FROM public.memberships
   WHERE "orgId" = NEW."orgId"
     AND status IN ('ACTIVE', 'INVITED');

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_seats: the % plan allows % member(s); this organisation already has % (including pending invitations).',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_camera_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  SELECT plan INTO v_plan FROM public.organisations
   WHERE id = NEW."orgId" AND "deletedAt" IS NULL;
  IF v_plan IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT max_cameras INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT count(*) INTO v_count FROM public.cameras WHERE "orgId" = NEW."orgId";

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_cameras: the % plan allows % camera(s); this organisation already has %.',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.enforce_site_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  SELECT plan INTO v_plan FROM public.organisations
   WHERE id = NEW."orgId" AND "deletedAt" IS NULL;
  IF v_plan IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT max_sites INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT count(*) INTO v_count FROM public.sites WHERE "orgId" = NEW."orgId";

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_sites: the % plan allows % site(s); this organisation already has %.',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
--  2. FREE THE SLUG AND THE SEATS ON DELETE
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.soft_delete_organisation(p_org_id UUID)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid   UUID := (SELECT auth.uid());
  v_name  TEXT;
  v_email TEXT;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  -- An ACTIVE ADMIN of THIS organisation, and nothing else.
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships
    WHERE "orgId" = p_org_id
      AND "profileId" = v_uid
      AND status = 'ACTIVE'
      AND role = 'ADMIN'
  ) THEN
    RETURN QUERY SELECT FALSE, 'Only an administrator of this organisation can delete it.'::TEXT;
    RETURN;
  END IF;

  SELECT name INTO v_name FROM public.organisations
   WHERE id = p_org_id AND "deletedAt" IS NULL;

  IF v_name IS NULL THEN
    -- Either it never existed or it is already deleted. Returning early keeps
    -- the audit log free of duplicates from a double-clicked button.
    RETURN QUERY SELECT FALSE, 'That organisation is not available to delete.'::TEXT;
    RETURN;
  END IF;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;

  -- Audit FIRST, while the org is still visible: `audit_insert` requires the
  -- org to be in `user_org_ids()`, and the caller is about to lose sight of it.
  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), p_org_id, v_uid, v_email,
     'organisation.deleted', 'Organisation', p_org_id::text,
     jsonb_build_object('name', v_name, 'soft', true), now());

  -- RELEASE THE SLUG. `organisations_slug_key` is a plain unique index with no
  -- partial predicate, so a deleted organisation holds its handle forever and
  -- recreating it yields "acme-1", "acme-2"... This is the reported "deleting
  -- does not really delete".
  --
  -- Renamed rather than nulled: the column is NOT NULL and
  -- `organisations_slug_format` requires alphanumerics. `left(...)` keeps it
  -- inside the varchar(80) bound.
  UPDATE public.organisations
     SET "deletedAt" = now(),
         slug        = left('deleted-' || replace(p_org_id::text, '-', ''), 80),
         "updatedAt" = now()
   WHERE id = p_org_id;

  -- RELEASE THE SEATS. Memberships of a deleted org counted against the plan
  -- limit, so a user could delete an organisation and find the next one already
  -- at its seat cap for no visible reason.
  --
  -- This MUST come after the UPDATE above that sets `deletedAt`:
  -- `assert_org_keeps_an_admin` checks whether the organisation is deleted, and
  -- suspending the last admin of a still-live org is correctly refused.
  --
  -- SUSPENDED, not deleted: `user_org_ids()` filters on ACTIVE, so this grants
  -- nothing, and the rows survive for audit attribution.
  UPDATE public.memberships
     SET status = 'SUSPENDED', "updatedAt" = now()
   WHERE "orgId" = p_org_id
     AND status IN ('ACTIVE', 'INVITED');

  -- RELEASE EVERY MEMBER'S POINTER. Omitting this stranded a real account:
  -- `currentOrgId` still pointed at the deleted org, the dashboard guard saw a
  -- non-null pointer and let them in, and every read returned nothing.
  -- Clearing `onboardedAt` is what routes them back through /onboarding.
  UPDATE public.profiles
     SET "currentOrgId" = NULL, "onboardedAt" = NULL, "updatedAt" = now()
   WHERE "currentOrgId" = p_org_id;

  RETURN QUERY SELECT TRUE, (v_name || ' has been deleted.')::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.soft_delete_organisation(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.soft_delete_organisation(UUID) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  3. THE GRACE PERIOD, THEN REAL DELETION
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Everything the soft delete kept is destroyed here, once the grace period has
-- passed. Service-role only: this is irreversible, and no browser client should
-- be able to reach it.
--
-- `audit_logs` are deliberately the LAST thing removed and are removed with the
-- organisation, because a row referencing a vanished org is worse than no row.
-- If your retention policy requires keeping them longer, stop calling this and
-- archive them elsewhere first.
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
  DELETE FROM public.alerts             WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.alert_rules        WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.reports            WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.analysis_sessions  WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.zones              WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.cameras            WHERE "orgId" = ANY(v_ids);
  DELETE FROM public.sites              WHERE "orgId" = ANY(v_ids);

  -- Pointers before parents: `profiles.currentOrgId` is ON DELETE SET NULL, but
  -- being explicit keeps the intent visible.
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

-- REVOKE FROM PUBLIC alone is NOT enough. Supabase's default privileges grant
-- EXECUTE on new functions to `authenticated` and `anon` directly, and a
-- revoke from PUBLIC does not touch a grant made to a named role. Verified: the
-- first version of this file left `authenticated` able to call it, which would
-- have let any signed-in user irreversibly destroy every organisation past its
-- grace period.
REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM anon;
REVOKE ALL ON FUNCTION public.purge_deleted_organisations(INT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.purge_deleted_organisations(INT) TO service_role;
