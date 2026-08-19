-- VisionWorks — operator self-service revoke
--
-- WHY THIS FILE EXISTS
--
-- 005 deliberately gave `platform_admins` a SELECT policy and nothing else, and
-- revoked EXECUTE on revoke_platform_admin() from `authenticated`. Escalating to
-- platform level should require database credentials, not a bug in a route
-- handler — that reasoning still holds for GRANTING.
--
-- REVOKING is the opposite risk. Removing access is the safe direction, and
-- needing to open the SQL editor to cut off a departing colleague is exactly the
-- friction that leaves stale access in place for weeks. So revoke gets a
-- controlled path; grant does not.
--
-- THE LOCKOUT GUARD IS THE POINT
--
-- The single worst outcome here is an operator revoking the last active
-- operator: nobody can reach the console, and nobody can grant access back
-- through the app either, because granting has no API path at all. Recovery
-- would mean opening the Supabase SQL editor with the database password.
--
-- The function therefore refuses to remove the final active operator. That check
-- lives in the database, not the UI, so it holds against a direct RPC call.
--
-- Apply AFTER 005_platform_admin.sql.

CREATE OR REPLACE FUNCTION public.revoke_operator(p_profile_id UUID)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_actor      UUID := (SELECT auth.uid());
  v_target     RECORD;
  v_remaining  INT;
  v_actor_email TEXT;
BEGIN
  -- 1. Only an active operator may revoke.
  IF NOT (SELECT public.is_platform_admin()) THEN
    RETURN QUERY SELECT FALSE, 'Not permitted.'::TEXT;
    RETURN;
  END IF;

  -- 2. Target must exist and still be active.
  SELECT pa."profileId", pa.note, pr.email
    INTO v_target
  FROM public.platform_admins pa
  LEFT JOIN public.profiles pr ON pr.id = pa."profileId"
  WHERE pa."profileId" = p_profile_id
    AND pa."revokedAt" IS NULL;

  IF v_target."profileId" IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That operator is not active.'::TEXT;
    RETURN;
  END IF;

  -- 3. THE LOCKOUT GUARD. Count operators who would remain.
  SELECT count(*) INTO v_remaining
  FROM public.platform_admins
  WHERE "revokedAt" IS NULL
    AND "profileId" <> p_profile_id;

  IF v_remaining = 0 THEN
    RETURN QUERY SELECT FALSE,
      'Cannot revoke the last platform operator — nobody would be able to reach the console, and access can only be granted back from the SQL editor.'::TEXT;
    RETURN;
  END IF;

  -- 4. Soft revoke. The row is kept so audit entries attributed to this operator
  --    still resolve to a real person.
  UPDATE public.platform_admins
     SET "revokedAt" = now()
   WHERE "profileId" = p_profile_id;

  SELECT email INTO v_actor_email FROM public.profiles WHERE id = v_actor;

  INSERT INTO public.platform_audit_logs
    (id, "actorId", "actorEmail", action, metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_actor, v_actor_email, 'platform.admin_revoked',
     jsonb_build_object(
       'targetProfileId', p_profile_id,
       'targetEmail', v_target.email,
       'note', v_target.note,
       'selfRevoke', (v_actor = p_profile_id)
     ),
     now());

  RETURN QUERY SELECT TRUE, 'Access revoked.'::TEXT;
END;
$$;

-- Callable by signed-in users, but the function's own first check means a
-- non-operator gets 'Not permitted.' rather than any effect.
REVOKE ALL     ON FUNCTION public.revoke_operator(UUID) FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION public.revoke_operator(UUID) TO authenticated;

COMMENT ON FUNCTION public.revoke_operator(UUID) IS
  'Revokes a platform operator. Refuses to remove the last active one, because '
  'granting access has no API path and recovery would require the SQL editor. '
  'Granting deliberately remains SQL-editor-only; only revoking is exposed.';
