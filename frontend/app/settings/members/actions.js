'use server';

// frontend/app/settings/members/actions.js
//
// Member and invitation management.
//
// WHY THESE ACTIONS ARE THIN
//
// Almost every rule this file appears to enforce is already enforced in
// Postgres, and that is deliberate — verified by direct testing rather than
// assumed:
//
//   membership_insert   WITH CHECK (orgId IN admin_org_ids() AND status='INVITED')
//                       → only an ADMIN can invite, and only into their own org,
//                         and they cannot fabricate an already-ACTIVE member.
//                         Measured: inserting status='ACTIVE' is rejected.
//   membership_update   USING + WITH CHECK (orgId IN admin_org_ids())
//                       → only an ADMIN can change a role or suspend anyone.
//   membership_delete   admin_org_ids() OR profileId = auth.uid()
//                       → admins remove anyone; anyone may remove themselves.
//   memberships_keep_an_admin (trigger, 001_constraints.sql)
//                       → refuses to demote, suspend or delete the final ACTIVE
//                         ADMIN. Measured: self-demote raises
//                         "Organisation … must retain at least one active admin".
//   audit_insert        WITH CHECK (orgId IN user_org_ids() AND actorId = auth.uid())
//                       → a member may write audit rows only for their own org,
//                         attributed only to themselves.
//
// So the checks here exist to produce a readable message and to fail fast, not
// to be the boundary. A Server Action is a POST endpoint the browser can call
// directly and it does not inherit the page's guard — but even called directly,
// RLS holds. That is the layering the rest of this codebase uses, and Step 1's
// two bugs both came from *forgetting* that RLS answers isolation, not
// integrity. Where a rule spans two columns or two rows, it is checked here.
//
// THE INVITE TOKEN
//
// A random 32-byte token is generated, and only its SHA-256 hash is stored.
// `inviteTokenHash` is a bearer credential: if the table leaked, raw tokens
// would grant access, hashes would not. The raw token is returned to the
// inviting admin exactly once, in the response, and never persisted anywhere.
//
// Note what the token is actually for. Acceptance does NOT require it — the
// `handle_new_auth_user()` trigger matches on EMAIL, so an invitee simply signs
// up with the invited address and is activated automatically (verified: INVITED
// → ACTIVE, profile pointed, onboardedAt set, `member.invite_accepted` written).
// The token exists so the invite link is unguessable and so a future
// token-based acceptance route has something to verify against. Email matching
// is what makes OAuth invitees work, since they never touch a signup form.

import crypto from 'crypto';
import { revalidatePath } from 'next/cache';
import { createClient } from '../../lib/supabase/server';
import { can, denialMessage } from '../../lib/permissions';

const INVITE_TTL_DAYS = 7;
const ROLES = new Set(['ADMIN', 'MANAGER', 'VIEWER']);

const fail = (message) => ({ ok: false, message });

/**
 * The caller, their organisation, and their role in it.
 *
 * Every action starts here. `currentOrgId` is cross-checked against an ACTIVE
 * membership rather than trusted alone — the same rule Step 2's tenant resolver
 * uses. A suspended member keeps a stale pointer, and honouring it would let
 * someone act inside an organisation that removed them.
 */
async function requireMember() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured. Add your project URL and anon key to frontend/.env.local, then restart the dev server.' };
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  const user = data.user;

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', user.id)
    .maybeSingle();

  const orgId = profile?.currentOrgId;
  if (!orgId) return { error: 'Create your organisation before managing members.' };

  const { data: membership } = await supabase
    .from('memberships')
    .select('role, status')
    .eq('orgId', orgId)
    .eq('profileId', user.id)
    .eq('status', 'ACTIVE')
    .maybeSingle();

  if (!membership) return { error: 'You are not an active member of this organisation.' };

  return { supabase, user, orgId, role: membership.role };
}

/**
 * Same, but refuses any role lacking the named capability.
 *
 * LAYER 2. The capability is resolved through lib/permissions.js rather than
 * compared against a hardcoded 'ADMIN' string, so this check and the button
 * the UI decided to render read from one table and cannot drift apart.
 *
 * This is not the boundary — `membership_insert/update/delete` require
 * admin_org_ids() and hold against a direct POST to this action. It exists so
 * the refusal is a sentence instead of a policy violation.
 */
async function requireCapability(capability) {
  const ctx = await requireMember();
  if (ctx.error) return ctx;
  if (!can(ctx.role, capability)) {
    return { error: denialMessage(capability) };
  }
  return ctx;
}

/**
 * Postgres errors, rendered as sentences.
 *
 * The constraint names are ours (001_constraints.sql), so the ones a user can
 * actually trigger are worth translating. Anything else falls through to the
 * raw message rather than being swallowed into a generic string.
 */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');

  if (/must retain at least one active admin/i.test(msg)) {
    return 'This is the only administrator. Promote someone else to administrator first.';
  }
  if (/memberships_orgId_invitedEmail_key/i.test(msg) || /duplicate key/i.test(msg)) {
    return 'That address already has an invitation or membership in this organisation.';
  }
  // Plan limits (015_plan_limits.sql). The trigger raises a message that is
  // already written for a person and already carries the tier and the number,
  // so it is passed through with a suffix rather than replaced by a vaguer
  // sentence that would have to hardcode the limit a second time.
  //
  // The `plan_limit_` prefix is the contract between the trigger and this
  // mapper; matching on it rather than on the prose means the wording can
  // change in SQL without silently falling through to the raw error here.
  const planLimit = msg.match(/plan_limit_(cameras|sites|seats):\s*(.+?)(?:\s*CONTEXT:|$)/is);
  if (planLimit) {
    return `${planLimit[2].trim()} Upgrade the plan to add more.`;
  }

  if (/row-level security/i.test(msg)) {
    return 'You do not have permission to do that.';
  }
  if (/memberships_email_lowercase/i.test(msg)) {
    return 'That email address could not be stored. Check it and try again.';
  }
  return msg || fallback;
}

/**
 * Best-effort audit entry.
 *
 * Never throws and never blocks the action that triggered it: an audit write
 * failing must not undo a membership change the user already saw succeed. The
 * `audit_insert` policy requires actorId = auth.uid(), so the actor is always
 * the caller — an audit row cannot be attributed to someone else.
 */
async function writeAudit(supabase, { orgId, user, action, targetType, targetId, metadata }) {
  try {
    await supabase.from('audit_logs').insert({
      orgId,
      actorId: user.id,
      actorEmail: user.email ?? null,
      action,
      targetType: targetType ?? null,
      targetId: targetId ?? null,
      metadata: metadata ?? null,
    });
  } catch {
    // Deliberately silent — see above.
  }
}

/** Email shape check. Storage is lower-cased to satisfy memberships_email_lowercase. */
function normaliseEmail(raw) {
  const email = String(raw ?? '').trim().toLowerCase();
  if (!email) return { error: 'Enter an email address.' };
  if (email.length > 320) return { error: 'That email address is too long.' };
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { error: 'That email address doesn’t look right.' };
  }
  return { email };
}

/* ── Read ────────────────────────────────────────────────────────────────── */

/**
 * The roster: active members and outstanding invitations, plus the caller's own
 * role so the UI knows what to render.
 *
 * `membership_select` already limits this to the caller's own organisations, so
 * the orgId filter here is for clarity and for the multi-org case, not for
 * safety.
 */
export async function listMembers() {
  const { supabase, user, orgId, role, error } = await requireCapability('members.view');
  if (error) return fail(error);

  const { data, error: dbError } = await supabase
    .from('memberships')
    .select('id, role, status, invitedEmail, invitedById, acceptedAt, createdAt, inviteExpiresAt, profileId, profiles:profileId (fullName, email, avatarUrl)')
    .eq('orgId', orgId)
    .order('createdAt', { ascending: true });

  if (dbError) return fail(describeDbError(dbError, 'Could not load the member list.'));

  const now = Date.now();
  const members = (data ?? []).map((row) => ({
    id: row.id,
    role: row.role,
    status: row.status,
    email: row.profiles?.email ?? row.invitedEmail,
    fullName: row.profiles?.fullName ?? null,
    joinedAt: row.acceptedAt,
    invitedAt: row.createdAt,
    // An invite past its expiry is still an INVITED row — nothing sweeps it —
    // so the UI needs to distinguish "waiting" from "too late to accept".
    expired:
      row.status === 'INVITED' &&
      Boolean(row.inviteExpiresAt) &&
      new Date(row.inviteExpiresAt).getTime() < now,
    expiresAt: row.inviteExpiresAt,
    isSelf: row.profileId === user.id,
  }));

  return {
    ok: true,
    members,
    viewerRole: role,
    viewerId: user.id,
    // Drives the last-admin warnings in the UI. The database enforces the rule
    // regardless; this only decides whether a button is shown as disabled.
    activeAdminCount: members.filter((m) => m.role === 'ADMIN' && m.status === 'ACTIVE').length,
  };
}

/* ── Invite ──────────────────────────────────────────────────────────────── */

/**
 * Invite an address at a role.
 *
 * Returns the signup link once. There is no email provider configured in this
 * project, so delivery is the admin's — the link is theirs to send. The invite
 * itself is fully functional without it: signing up with the invited address is
 * what accepts it.
 */
export async function inviteMember(formData) {
  const { supabase, user, orgId, error } = await requireCapability('members.invite');
  if (error) return fail(error);

  const { email, error: emailError } = normaliseEmail(formData.get('email'));
  if (emailError) return fail(emailError);

  const role = String(formData.get('role') ?? '').trim();
  if (!ROLES.has(role)) return fail('Choose a role for this person.');

  // Inviting yourself is always a mistake — you are already here — and the
  // unique index would reject it with a far less helpful message.
  if (email === String(user.email ?? '').toLowerCase()) {
    return fail('You are already a member of this organisation.');
  }

  // Check for an existing row first. `@@unique([orgId, invitedEmail])` would
  // catch it anyway, but "already invited" and "already a member" are different
  // situations and deserve different sentences.
  const { data: existing } = await supabase
    .from('memberships')
    .select('id, status')
    .eq('orgId', orgId)
    .eq('invitedEmail', email)
    .maybeSingle();

  if (existing) {
    return fail(
      existing.status === 'ACTIVE'
        ? 'That person is already a member of this organisation.'
        : existing.status === 'SUSPENDED'
          ? 'That person’s membership is suspended. Restore it instead of re-inviting.'
          : 'That address already has an outstanding invitation. Resend it instead.',
    );
  }

  // 32 random bytes, stored only as a SHA-256 hash. The column is VarChar(64),
  // which is exactly the width of a hex-encoded SHA-256 digest.
  const token = crypto.randomBytes(32).toString('base64url');
  const tokenHash = crypto.createHash('sha256').update(token).digest('hex');
  const expiresAt = new Date(Date.now() + INVITE_TTL_DAYS * 86400_000).toISOString();

  const { data: inserted, error: dbError } = await supabase
    .from('memberships')
    .insert({
      orgId,
      role,
      status: 'INVITED',
      invitedEmail: email,
      inviteTokenHash: tokenHash,
      inviteExpiresAt: expiresAt,
      invitedById: user.id,
      // Prisma @updatedAt is maintained client-side and the column has no DB
      // default — the same trap Step 1 hit on sites and cameras.
      updatedAt: new Date().toISOString(),
    })
    .select('id')
    .single();

  if (dbError) return fail(describeDbError(dbError, 'Could not create the invitation.'));

  await writeAudit(supabase, {
    orgId, user,
    action: 'member.invited',
    targetType: 'Membership',
    targetId: inserted.id,
    // The address is already in the row this points at; the role is the part
    // worth having in the log. No token, hashed or otherwise, goes in an audit.
    metadata: { role, email },
  });

  revalidatePath('/settings/members');

  return {
    ok: true,
    message: `Invitation created for ${email}.`,
    // Shown once, then gone. Not stored, not logged, not re-retrievable.
    inviteToken: token,
    email,
  };
}

/** Issue a fresh token and extend the expiry on an outstanding invitation. */
export async function resendInvite(membershipId) {
  const { supabase, user, orgId, error } = await requireCapability('members.invite');
  if (error) return fail(error);
  if (typeof membershipId !== 'string' || !membershipId) return fail('Invalid invitation.');

  const { data: row } = await supabase
    .from('memberships')
    .select('id, status, invitedEmail')
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .maybeSingle();

  if (!row) return fail('That invitation no longer exists.');
  if (row.status !== 'INVITED') return fail('That person has already accepted.');

  // A new token, not the old one: the point of resending is that the previous
  // link may have gone astray, so it should stop working.
  const token = crypto.randomBytes(32).toString('base64url');
  const tokenHash = crypto.createHash('sha256').update(token).digest('hex');

  const { data: updated, error: dbError } = await supabase
    .from('memberships')
    .update({
      inviteTokenHash: tokenHash,
      inviteExpiresAt: new Date(Date.now() + INVITE_TTL_DAYS * 86400_000).toISOString(),
      updatedAt: new Date().toISOString(),
    })
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not refresh the invitation.'));
  // An UPDATE filtered away by RLS returns no error and no rows — the exact
  // silent-success bug found in Step 1. An empty result is a failure.
  if (!updated || updated.length === 0) return fail('That invitation could not be updated.');

  await writeAudit(supabase, {
    orgId, user,
    action: 'member.invite_resent',
    targetType: 'Membership',
    targetId: membershipId,
    metadata: { email: row.invitedEmail },
  });

  revalidatePath('/settings/members');
  return { ok: true, message: `New link generated for ${row.invitedEmail}.`, inviteToken: token, email: row.invitedEmail };
}

/** Withdraw an invitation that has not been accepted. */
export async function revokeInvite(membershipId) {
  const { supabase, user, orgId, error } = await requireCapability('members.manage');
  if (error) return fail(error);
  if (typeof membershipId !== 'string' || !membershipId) return fail('Invalid invitation.');

  const { data: row } = await supabase
    .from('memberships')
    .select('id, status, invitedEmail')
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .maybeSingle();

  if (!row) return fail('That invitation no longer exists.');
  // Deleting an ACTIVE membership is "remove member", a different action with a
  // different audit verb and a last-admin risk. Refuse rather than silently
  // doing something more destructive than the button said.
  if (row.status !== 'INVITED') return fail('That person has already accepted. Remove them instead.');

  const { data: deleted, error: dbError } = await supabase
    .from('memberships')
    .delete()
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not withdraw the invitation.'));
  if (!deleted || deleted.length === 0) return fail('That invitation could not be withdrawn.');

  await writeAudit(supabase, {
    orgId, user,
    action: 'member.invite_revoked',
    targetType: 'Membership',
    targetId: membershipId,
    metadata: { email: row.invitedEmail },
  });

  revalidatePath('/settings/members');
  return { ok: true, message: `Invitation for ${row.invitedEmail} withdrawn.` };
}

/* ── Members ─────────────────────────────────────────────────────────────── */

/**
 * Change someone's role.
 *
 * The last-admin case is checked here for the message, and enforced by the
 * `memberships_keep_an_admin` trigger regardless — including against a direct
 * POST to this action that skipped the UI entirely.
 */
export async function changeRole(membershipId, nextRole) {
  const { supabase, user, orgId, error } = await requireCapability('members.manage');
  if (error) return fail(error);
  if (typeof membershipId !== 'string' || !membershipId) return fail('Invalid member.');
  if (!ROLES.has(nextRole)) return fail('Choose a valid role.');

  const { data: row } = await supabase
    .from('memberships')
    .select('id, role, status, invitedEmail, profileId')
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .maybeSingle();

  if (!row) return fail('That member no longer exists.');
  if (row.role === nextRole) return { ok: true, message: 'No change — they already hold that role.' };

  const { data: updated, error: dbError } = await supabase
    .from('memberships')
    .update({ role: nextRole, updatedAt: new Date().toISOString() })
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not change that role.'));
  if (!updated || updated.length === 0) return fail('That member could not be updated.');

  await writeAudit(supabase, {
    orgId, user,
    action: 'member.role_changed',
    targetType: 'Membership',
    targetId: membershipId,
    // Before AND after: a log that records only the new value cannot answer
    // "what was it before" without replaying every prior entry.
    metadata: { from: row.role, to: nextRole, email: row.invitedEmail },
  });

  revalidatePath('/settings/members');
  return { ok: true, message: `Role changed to ${nextRole.toLowerCase()}.` };
}

/**
 * Suspend or restore a member.
 *
 * SUSPENDED rather than deleted: `user_org_ids()` counts only ACTIVE
 * memberships, so suspension revokes access immediately while keeping the row —
 * which means audit entries attributed to this person still resolve, and
 * restoring them is one click rather than a re-invitation.
 */
export async function setMemberStatus(membershipId, nextStatus) {
  const { supabase, user, orgId, error } = await requireCapability('members.manage');
  if (error) return fail(error);
  if (typeof membershipId !== 'string' || !membershipId) return fail('Invalid member.');
  if (!['ACTIVE', 'SUSPENDED'].includes(nextStatus)) return fail('Invalid status.');

  const { data: row } = await supabase
    .from('memberships')
    .select('id, role, status, invitedEmail, profileId, acceptedAt')
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .maybeSingle();

  if (!row) return fail('That member no longer exists.');
  if (row.status === 'INVITED') return fail('That invitation has not been accepted yet.');
  if (row.status === nextStatus) return { ok: true, message: 'No change.' };

  // `memberships_active_has_profile` requires an ACTIVE row to carry both a
  // profileId and an acceptedAt. Restoring someone suspended before they ever
  // accepted would violate it, so refuse with a real explanation instead of
  // surfacing a constraint name.
  if (nextStatus === 'ACTIVE' && (!row.profileId || !row.acceptedAt)) {
    return fail('That membership was never accepted, so it cannot be restored.');
  }

  const { data: updated, error: dbError } = await supabase
    .from('memberships')
    .update({ status: nextStatus, updatedAt: new Date().toISOString() })
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not update that member.'));
  if (!updated || updated.length === 0) return fail('That member could not be updated.');

  await writeAudit(supabase, {
    orgId, user,
    action: nextStatus === 'SUSPENDED' ? 'member.suspended' : 'member.restored',
    targetType: 'Membership',
    targetId: membershipId,
    metadata: { email: row.invitedEmail, role: row.role },
  });

  revalidatePath('/settings/members');
  return {
    ok: true,
    message: nextStatus === 'SUSPENDED' ? 'Member suspended.' : 'Member restored.',
  };
}

/** Remove a member entirely. The last-admin trigger still applies. */
export async function removeMember(membershipId) {
  const { supabase, user, orgId, error } = await requireCapability('members.manage');
  if (error) return fail(error);
  if (typeof membershipId !== 'string' || !membershipId) return fail('Invalid member.');

  const { data: row } = await supabase
    .from('memberships')
    .select('id, role, status, invitedEmail')
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .maybeSingle();

  if (!row) return fail('That member no longer exists.');

  const { data: deleted, error: dbError } = await supabase
    .from('memberships')
    .delete()
    .eq('id', membershipId)
    .eq('orgId', orgId)
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not remove that member.'));
  if (!deleted || deleted.length === 0) return fail('That member could not be removed.');

  await writeAudit(supabase, {
    orgId, user,
    action: 'member.removed',
    targetType: 'Membership',
    targetId: membershipId,
    metadata: { email: row.invitedEmail, role: row.role },
  });

  revalidatePath('/settings/members');
  return { ok: true, message: `${row.invitedEmail} removed.` };
}
