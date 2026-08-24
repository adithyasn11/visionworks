-- VisionWorks — let an EXISTING user accept an invitation
--
-- ═══════════════════════════════════════════════════════════════════════════
--  THE BUG
-- ═══════════════════════════════════════════════════════════════════════════
--
-- An admin invited a colleague who ALREADY HAD AN ACCOUNT. The membership row
-- was created correctly (status INVITED, invitedEmail matching exactly), and
-- the invitee never saw it. Nothing appeared anywhere in their interface.
--
-- The cause: acceptance lived ENTIRELY in `on_auth_user_created`
-- (002_auth_triggers.sql), a trigger on `auth.users` INSERT. It flips a
-- matching INVITED row to ACTIVE at the moment an account is created. That
-- works perfectly for the case it was written for — invite someone who has
-- never used the product — and does nothing at all for someone who signed up
-- last week. The trigger never fires again, so the invitation sits INVITED
-- forever with no path out of that state.
--
-- Measured on the live database before writing this:
--
--   invitedEmail  adithyakumar3698@gmail.com   status INVITED   profileId NULL
--   auth.users    adithyakumar3698@gmail.com   exists, created BEFORE the invite
--
-- The emails matched exactly. There was simply no code that could act on it.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  THE FIX
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Two functions, both SECURITY DEFINER and both scoped to the CALLER:
--
--   list_my_invitations()   what am I invited to, right now
--   accept_invitation(id)   join that organisation
--
-- They cannot be ordinary RLS-filtered queries. `membership_select` returns a
-- row when `profileId = auth.uid()` OR the org is already in `user_org_ids()`
-- — and a pending invitation has a NULL profileId and an org the invitee is
-- not yet a member of. It therefore matches NEITHER branch and is invisible to
-- the very person it is addressed to. That is correct for a policy about
-- membership, and it is exactly why acceptance needs a definer function that
-- matches on the VERIFIED EMAIL instead.
--
-- The email comes from `auth.users` via `auth.uid()`, never from an argument.
-- A caller cannot ask "what is invited to somebody else's address".
--
-- Apply AFTER 015_plan_limits.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  WHAT AM I INVITED TO?
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.list_my_invitations()
RETURNS TABLE (
  membership_id UUID,
  org_id        UUID,
  org_name      TEXT,
  role          "Role",
  invited_at    TIMESTAMPTZ,
  expires_at    TIMESTAMPTZ,
  invited_by    TEXT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    m.id,
    m."orgId",
    o.name::TEXT,
    m.role,
    m."createdAt",
    m."inviteExpiresAt",
    inviter.email::TEXT
  FROM public.memberships m
  JOIN public.organisations o ON o.id = m."orgId"
  LEFT JOIN public.profiles inviter ON inviter.id = m."invitedById"
  WHERE m.status = 'INVITED'
    -- Matched on the caller's OWN verified address, read from auth.users.
    -- Lowercased both sides: the signup trigger stores `lower(NEW.email)` in
    -- profiles, but `invitedEmail` is whatever the admin typed into the form,
    -- and "Adithya@..." must still match "adithya@...".
    AND lower(trim(m."invitedEmail")) = (
      SELECT lower(trim(u.email)) FROM auth.users u WHERE u.id = (SELECT auth.uid())
    )
    AND (m."inviteExpiresAt" IS NULL OR m."inviteExpiresAt" > now())
    -- An invitation into a deleted organisation is not actionable.
    AND o."deletedAt" IS NULL
  ORDER BY m."createdAt" ASC;
$$;

REVOKE ALL ON FUNCTION public.list_my_invitations() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.list_my_invitations() TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  ACCEPT ONE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Takes a membership id rather than "accept everything": a user may hold
-- invitations to several organisations, and joining all of them because they
-- clicked once is not what anyone meant.
CREATE OR REPLACE FUNCTION public.accept_invitation(p_membership_id UUID)
RETURNS TABLE (ok BOOLEAN, message TEXT, org_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid    UUID := (SELECT auth.uid());
  v_email  TEXT;
  v_inv    RECORD;
  v_org    RECORD;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT, NULL::UUID;
    RETURN;
  END IF;

  SELECT lower(trim(u.email)) INTO v_email FROM auth.users u WHERE u.id = v_uid;
  IF v_email IS NULL THEN
    RETURN QUERY SELECT FALSE, 'Your account could not be verified.'::TEXT, NULL::UUID;
    RETURN;
  END IF;

  -- THE AUTHORISATION CHECK. The row must be INVITED, unexpired, and addressed
  -- to THIS caller's verified email. A forged membership id belonging to
  -- someone else's invitation fails here, because the email will not match.
  SELECT * INTO v_inv
    FROM public.memberships
   WHERE id = p_membership_id
     AND status = 'INVITED'
     AND lower(trim("invitedEmail")) = v_email
     AND ("inviteExpiresAt" IS NULL OR "inviteExpiresAt" > now());

  IF v_inv.id IS NULL THEN
    -- Deliberately one message for "not found", "not yours" and "expired".
    -- Distinguishing them would let someone probe which membership ids exist.
    RETURN QUERY SELECT FALSE,
      'That invitation is no longer available. Ask an administrator to send a new one.'::TEXT,
      NULL::UUID;
    RETURN;
  END IF;

  SELECT * INTO v_org FROM public.organisations
   WHERE id = v_inv."orgId" AND "deletedAt" IS NULL;

  IF v_org.id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That organisation is no longer available.'::TEXT, NULL::UUID;
    RETURN;
  END IF;

  -- Already a member by another route? Retire the invitation rather than
  -- creating a second membership row for the same person in the same org.
  --
  -- SUSPENDED, not deleted and not "REVOKED" — the enum is
  -- INVITED | ACTIVE | SUSPENDED (verified against the live database; an
  -- earlier draft of this file assumed a REVOKED value that does not exist and
  -- would have failed on the cast). SUSPENDED is the right terminal state:
  -- `user_org_ids()` filters on status = 'ACTIVE', so it grants nothing, and
  -- the row survives for the roster and the audit trail.
  IF EXISTS (
    SELECT 1 FROM public.memberships
     WHERE "orgId" = v_inv."orgId" AND "profileId" = v_uid AND status = 'ACTIVE'
  ) THEN
    UPDATE public.memberships
       SET status = 'SUSPENDED', "updatedAt" = now()
     WHERE id = v_inv.id;
    RETURN QUERY SELECT TRUE,
      ('You are already a member of ' || v_org.name || '.')::TEXT, v_org.id;
    RETURN;
  END IF;

  -- The acceptance itself. Mirrors exactly what the signup trigger does in
  -- 002_auth_triggers.sql, so an invite accepted this way is indistinguishable
  -- from one accepted at signup.
  --
  -- NOTE ON THE SEAT LIMIT: `seat_plan_limit` (015) is an INSERT trigger, not
  -- UPDATE, precisely so an acceptance cannot be refused — the seat was
  -- already consumed when the invitation was created. Verified, not assumed.
  UPDATE public.memberships
     SET "profileId"       = v_uid,
         status            = 'ACTIVE',
         "acceptedAt"      = now(),
         "inviteTokenHash" = NULL,   -- single use; burn it
         "updatedAt"       = now()
   WHERE id = v_inv.id;

  -- Point them at the organisation they just joined. COALESCE on onboardedAt:
  -- an invited member never runs the wizard, and leaving it NULL would send
  -- them to /onboarding forever.
  UPDATE public.profiles
     SET "currentOrgId" = v_inv."orgId",
         "onboardedAt"  = COALESCE("onboardedAt", now()),
         "updatedAt"    = now()
   WHERE id = v_uid;

  INSERT INTO public.audit_logs
    (id, "orgId", "actorId", "actorEmail", action, "targetType", "targetId", metadata, "createdAt")
  VALUES
    (gen_random_uuid(), v_inv."orgId", v_uid, v_email,
     'membership.accepted', 'Membership', v_inv.id::text,
     jsonb_build_object('role', v_inv.role, 'via', 'existing_account'), now());

  RETURN QUERY SELECT TRUE, ('You have joined ' || v_org.name || '.')::TEXT, v_org.id;
END;
$$;

REVOKE ALL ON FUNCTION public.accept_invitation(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_invitation(UUID) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  DECLINE ONE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Without this, the only way to clear an unwanted invitation is to accept it
-- or ask the admin to withdraw it. SUSPENDED rather than deleted so the roster
-- keeps its history and the audit trail is not orphaned — and SUSPENDED rather
-- than a "REVOKED" value, which does not exist in the MembershipStatus enum.
CREATE OR REPLACE FUNCTION public.decline_invitation(p_membership_id UUID)
RETURNS TABLE (ok BOOLEAN, message TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_uid   UUID := (SELECT auth.uid());
  v_email TEXT;
  v_inv   RECORD;
BEGIN
  IF v_uid IS NULL THEN
    RETURN QUERY SELECT FALSE, 'You must be signed in.'::TEXT;
    RETURN;
  END IF;

  SELECT lower(trim(u.email)) INTO v_email FROM auth.users u WHERE u.id = v_uid;

  SELECT * INTO v_inv
    FROM public.memberships
   WHERE id = p_membership_id
     AND status = 'INVITED'
     AND lower(trim("invitedEmail")) = v_email;

  IF v_inv.id IS NULL THEN
    RETURN QUERY SELECT FALSE, 'That invitation is no longer available.'::TEXT;
    RETURN;
  END IF;

  UPDATE public.memberships
     SET status = 'SUSPENDED', "inviteTokenHash" = NULL, "updatedAt" = now()
   WHERE id = v_inv.id;

  RETURN QUERY SELECT TRUE, 'Invitation declined.'::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.decline_invitation(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.decline_invitation(UUID) TO authenticated;
