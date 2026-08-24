'use server';

// frontend/app/home/inviteActions.js
//
// Pending invitations for the signed-in user.
//
// WHY THIS LIVES ON /home
//
// /home is the screen a user without an organisation sees. That is exactly the
// audience for an invitation: someone who has an account but no workspace yet.
// A member already inside an org is redirected to /dashboard before /home can
// render, so this never competes with the product for their attention.
//
// WHY THESE ARE RPCs AND NOT ORDINARY QUERIES
//
// A pending invitation is INVISIBLE to its own recipient through normal RLS.
// `membership_select` returns a row when `profileId = auth.uid()` OR the org is
// already in `user_org_ids()` — and an INVITED row has a NULL profileId and an
// org the invitee has not joined. It matches neither branch, by design.
//
// So acceptance goes through SECURITY DEFINER functions that match on the
// caller's VERIFIED EMAIL from auth.users (016_accept_invite_existing_user.sql).
// The email is never an argument, so nobody can ask what was sent to another
// address.
//
// THE BUG THIS FIXES
//
// Acceptance used to live entirely in `on_auth_user_created`, a trigger on
// signup. Inviting somebody who ALREADY had an account did nothing at all: the
// trigger never fires again, and the invitation sat INVITED forever with no
// path out of that state and no indication anywhere in the interface.

import { createClient } from '../lib/supabase/server';

const fail = (message) => ({ ok: false, message });

async function requireUser() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured. Add your project URL and anon key to frontend/.env.local, then restart the dev server.' };
  }
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  return { supabase, user: data.user };
}

/**
 * Invitations addressed to the caller, newest last.
 *
 * Returns an empty list rather than an error when there are none — "no pending
 * invitations" is the normal state for almost everyone, and the caller renders
 * nothing at all in that case.
 */
export async function listMyInvitations() {
  const { supabase, error: authError } = await requireUser();
  if (authError) return { ok: false, message: authError, invitations: [] };

  const { data, error } = await supabase.rpc('list_my_invitations');
  if (error) {
    return { ok: false, message: 'Could not check for invitations.', invitations: [] };
  }

  const rows = Array.isArray(data) ? data : [];
  return {
    ok: true,
    invitations: rows.map((r) => ({
      id: r.membership_id,
      orgId: r.org_id,
      orgName: r.org_name,
      role: r.role,
      invitedAt: r.invited_at,
      expiresAt: r.expires_at,
      invitedBy: r.invited_by,
    })),
  };
}

/**
 * Join the organisation an invitation points at.
 *
 * Returns `{ ok, next }` rather than redirecting. A `redirect()` thrown inside
 * a Server Action travels back with the action's response and navigates before
 * the caller's own code runs — the precise mechanism that made the onboarding
 * wizard skip its own steps. The client decides when to move; this says where.
 */
export async function acceptInvitation(membershipId) {
  const { supabase, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const id = String(membershipId ?? '').trim();
  if (!id) return fail('That invitation could not be identified.');

  const { data, error } = await supabase.rpc('accept_invitation', { p_membership_id: id });

  if (error) {
    // A malformed uuid is the one client-side mistake worth naming; everything
    // else the function itself reports as a row.
    if (/invalid input syntax for type uuid/i.test(String(error.message ?? ''))) {
      return fail('That invitation could not be identified.');
    }
    return fail('Could not accept that invitation. Please try again.');
  }

  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) {
    return fail(row?.message ?? 'Could not accept that invitation.');
  }

  // Straight into the workspace they just joined. The function has already set
  // `currentOrgId` AND `onboardedAt`, so the dashboard guard lets them through
  // rather than bouncing them into a wizard they must not run — an invited
  // member joins an existing organisation and never creates one.
  return { ok: true, message: row.message, next: '/dashboard' };
}

/** Turn one down. SUSPENDED rather than deleted, so the roster keeps history. */
export async function declineInvitation(membershipId) {
  const { supabase, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const id = String(membershipId ?? '').trim();
  if (!id) return fail('That invitation could not be identified.');

  const { data, error } = await supabase.rpc('decline_invitation', { p_membership_id: id });
  if (error) return fail('Could not decline that invitation. Please try again.');

  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) return fail(row?.message ?? 'Could not decline that invitation.');

  return { ok: true, message: row.message };
}
