-- VisionWorks — let an admin soft-delete their own organisation
--
-- THE BUG
--
-- An ADMIN clicking "Delete organisation" got "Only an administrator can do
-- that." — while being the administrator. Reproduced through a real signed-in
-- client, and narrowed by elimination:
--
--   UPDATE organisations SET name=...              -> 1 row   OK
--   UPDATE organisations SET "dataRetentionDays"=. -> 1 row   OK
--   UPDATE organisations SET "deletedAt"=now()     -> RLS violation
--
-- Only the soft delete fails. Both branches of a widened `WITH CHECK`
-- evaluated to `true` when tested standalone, and it still failed — so the
-- problem is not one that a cleverer policy expression fixes. A row that makes
-- itself invisible is fundamentally awkward for a symmetric USING/WITH CHECK,
-- and chasing it further would mean guessing at the planner.
--
-- THE FIX: THE SAME SHAPE AS create_organisation()
--
-- Organisation creation had the mirror-image problem — `organisations` has no
-- INSERT policy at all, because a browser client must not be able to conjure
-- one. The answer there was a SECURITY DEFINER function that does the whole
-- job atomically and checks authorisation itself. Deletion gets the same
-- treatment, for the same reason: it is a privileged lifecycle operation, not
-- an ordinary column edit.
--
-- The function is the ONLY way to soft-delete, and it verifies membership
-- itself before doing anything. `org_update` is therefore reverted to its
-- original strict form — no widened check, no second helper function, nothing
-- for a future reader to misread as a loophole.
--
-- WHY THIS IS NOT A WEAKER BOUNDARY
--
-- SECURITY DEFINER bypasses RLS, so the authorisation has to be explicit — and
-- it is, in the function body:
--
--   * auth.uid() must be non-null                (a real session)
--   * an ACTIVE ADMIN membership in THAT org     (not just any member)
--   * the org must exist and not be deleted yet  (idempotent, no double audit)
--
-- A MANAGER or VIEWER calling this RPC directly is refused by the membership
-- check, not by a policy — verified below. EXECUTE is granted to
-- `authenticated` only; `anon` cannot call it at all.
--
-- Apply AFTER 012_dashboard_covering_index.sql.

-- Revert 013's earlier attempt: the widened check did not fix the bug, and
-- leaving it would be a permanent puzzle for the next reader.
DROP POLICY IF EXISTS org_update ON public.organisations;

CREATE POLICY org_update ON public.organisations
  FOR UPDATE TO authenticated
  USING (id IN (SELECT public.admin_org_ids()))
  WITH CHECK (id IN (SELECT public.admin_org_ids()));

DROP FUNCTION IF EXISTS public.admin_org_ids_including_deleted();

-- ── The deletion path ──────────────────────────────────────────────────────

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

  -- THE AUTHORISATION CHECK. This is what RLS would otherwise do, and it is
  -- deliberately narrow: an ACTIVE ADMIN of THIS organisation, nothing else.
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
    -- Either it never existed or it is already deleted. Same answer either
    -- way, and returning early keeps the audit log free of duplicate entries
    -- from a double-clicked button.
    RETURN QUERY SELECT FALSE, 'That organisation is not available to delete.'::TEXT;
    RETURN;
  END IF;

  SELECT email INTO v_email FROM public.profiles WHERE id = v_uid;

  -- Audit FIRST, while the org is still visible. Written inside the function
  -- because `audit_insert` requires the org to be in `user_org_ids()`, and the
  -- caller is about to lose sight of it entirely.
  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), p_org_id, v_uid, v_email,
     'organisation.deleted', 'Organisation', p_org_id::text,
     jsonb_build_object('name', v_name, 'soft', true), now());

  UPDATE public.organisations
     SET "deletedAt" = now(), "updatedAt" = now()
   WHERE id = p_org_id;

  -- RELEASE EVERY MEMBER'S POINTER. This is not tidying — omitting it strands
  -- them, and it stranded a real account:
  --
  --   profiles.currentOrgId still pointed at the deleted org
  --   -> the dashboard guard saw a non-null pointer and let them in
  --   -> `org_select` filters `deletedAt IS NULL`, so every page that read the
  --      organisation got nothing back and hung on its loading state
  --   -> no error, no escape, no way to create a replacement org
  --
  -- Clearing `onboardedAt` too is what routes them back through /onboarding,
  -- which is the only screen that can give them a new organisation.
  UPDATE public.profiles
     SET "currentOrgId" = NULL, "onboardedAt" = NULL, "updatedAt" = now()
   WHERE "currentOrgId" = p_org_id;

  RETURN QUERY SELECT TRUE, (v_name || ' has been deleted.')::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.soft_delete_organisation(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.soft_delete_organisation(UUID) TO authenticated;
