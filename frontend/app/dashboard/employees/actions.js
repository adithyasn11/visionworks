'use server';

// frontend/app/dashboard/employees/actions.js
//
// Employee roster CRUD.
//
// WHY THESE ACTIONS ARE THIN
//
// Nearly every rule they appear to enforce is already enforced in Postgres by
// migration 020, and each was verified against a real Postgres 17 container
// rather than assumed:
//
//   employee_select    "orgId" IN user_org_ids()  AND "deletedAt" IS NULL
//                      → every member reads their own org's roster, nobody
//                        reads another org's. Measured: Org B sees 0 of Org A's
//                        3 employees.
//   employee_insert    WITH CHECK ("orgId" IN manage_org_ids())
//                      → a VIEWER inserting is rejected by the database, and so
//                        is an ADMIN of a different organisation.
//   employee_update    USING + WITH CHECK ("orgId" IN manage_org_ids())
//   employees_org_code_key        partial UNIQUE ("orgId","employeeCode")
//                                 WHERE "deletedAt" IS NULL
//                      → codes are unique per org and REUSABLE after someone
//                        leaves.
//   employees_one_active_per_zone partial UNIQUE ("assignedZoneId")
//                                 WHERE active AND "deletedAt" IS NULL
//                      → two active employees cannot share a desk, because
//                        Step 7's binding rule needs "exactly one employee has
//                        assignedZoneId == Z" to be true or it silently never
//                        fires.
//
// So the checks here exist to produce a readable sentence and to fail fast, not
// to be the boundary. A Server Action is a POST endpoint the browser can call
// directly and does not inherit the page's render decisions — but even called
// directly, RLS holds.
//
// SOFT DELETE GOES THROUGH AN RPC, AND THAT IS NOT A STYLE CHOICE
//
// `update({ deletedAt })` is REJECTED by RLS. Reproduced and narrowed by
// elimination on a real database: updating `displayName` succeeds, updating
// `active` succeeds, updating `deletedAt` alone fails with "new row violates
// row-level security policy". `employee_select` filters `deletedAt IS NULL`,
// and an UPDATE must leave the row visible to the SELECT policy, so a row
// cannot make itself invisible. `organisations` has exactly this property and
// solved it exactly this way in 013. `soft_delete_employee()` is the matching
// door, and it re-checks ADMIN-or-MANAGER inside the definer context because
// SECURITY DEFINER bypasses the RLS that would otherwise have done it.

import { revalidatePath } from 'next/cache';
import { createClient } from '../../lib/supabase/server';
import { can, denialMessage } from '../../lib/permissions';

const fail = (message) => ({ ok: false, message });

const MAX_CODE = 64;
const MAX_NAME = 160;

/**
 * The caller, their org, and their role — cross-checked against an ACTIVE
 * membership rather than trusting `currentOrgId` alone. A suspended member
 * keeps a stale pointer, and honouring it would let someone act inside an
 * organisation that removed them.
 */
async function requireCapability(capability) {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured. Add your project URL and anon key to frontend/.env.local, then restart the dev server.' };
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  const user = data.user;

  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return { error: 'Create your organisation first.' };

  const { data: membership } = await supabase
    .from('memberships')
    .select('role')
    .eq('orgId', orgId)
    .eq('profileId', user.id)
    .eq('status', 'ACTIVE')
    .maybeSingle();

  if (!membership) return { error: 'You are not an active member of this organisation.' };
  if (!can(membership.role, capability)) return { error: denialMessage(capability) };

  return { supabase, user, orgId, role: membership.role };
}

/** Best-effort audit row. Never blocks the change it records. */
async function writeAudit(supabase, { orgId, user, action, targetId, metadata }) {
  try {
    await supabase.from('audit_logs').insert({
      orgId,
      actorId: user.id,
      actorEmail: user.email ?? null,
      action,
      targetType: 'Employee',
      targetId: targetId ?? null,
      metadata: metadata ?? null,
    });
  } catch {
    // Deliberately silent — an audit failure must not undo a saved change.
  }
}

/**
 * Turn a Postgres error into a sentence a person can act on.
 *
 * Each branch names a constraint that migration 020 actually creates, so a
 * message here cannot drift away from what the database really rejected.
 *
 * ANYTHING UNRECOGNISED BECOMES THE CALLER'S FALLBACK, NEVER THE RAW MESSAGE.
 *
 * This used to end `return msg || fallback`, which passed the database's own
 * words straight to the screen. A user with migration 020 unapplied was shown
 * "Could not find the table 'public.employees' in the schema cache" — which
 * means nothing to them, and leaks the schema's internals to anyone who can
 * provoke an error. Postgres messages are for the server log; the screen gets
 * a sentence written for a person.
 *
 * The raw text is still logged, so a real fault stays diagnosable.
 */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');

  if (/employees_org_code_key/i.test(msg)) {
    return 'That employee code is already used by someone else in this organisation.';
  }
  if (/employees_one_active_per_zone/i.test(msg)) {
    return 'That desk is already assigned to another active employee. Free it first, or pick a different zone.';
  }
  if (/employees_code_not_blank/i.test(msg))  return 'An employee code is required.';
  if (/employees_name_not_blank/i.test(msg))  return 'A name is required.';
  if (/row-level security/i.test(msg)) {
    return 'Only an administrator or manager can change the employee roster.';
  }
  if (/insufficient_privilege|Only an administrator or manager/i.test(msg)) {
    return 'Only an administrator or manager can remove an employee.';
  }
  if (/no_data_found|Employee not found/i.test(msg)) return 'That employee no longer exists.';

  // The identity tables are created by prisma/sql/020_identity.sql. Until that
  // has been applied to this deployment, PostgREST reports the table as missing
  // from its schema cache. That is a setup step nobody has run, not a fault the
  // user caused, so it gets its own message pointing at the fix.
  if (/schema cache|does not exist|relation .* does not exist/i.test(msg)) {
    return 'Employee tracking is not set up on this deployment yet. Apply the database migration prisma/sql/020_identity.sql, then reload.';
  }

  // Unrecognised. Log the real thing for whoever has to fix it; show the user
  // a sentence rather than Postgres's internals.
  if (msg) console.error('[employees] unhandled database error:', msg);
  return fallback;
}

/** Trim, collapse whitespace, and cap. Empty becomes null so CHECKs speak. */
function clean(value, max) {
  const s = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (!s) return null;
  return s.slice(0, max);
}

/* ── Read ────────────────────────────────────────────────────────────────── */

/**
 * The roster, plus the zones available to assign and the caller's role.
 *
 * Readable by every member: `employee_select` returns the rows to anyone in the
 * org, so hiding the list from a VIEWER would hide what the database willingly
 * returns. Only writing is gated.
 *
 * `deletedAt IS NULL` is applied by the policy itself, so no filter is needed
 * here — but the query is still scoped by the policy's `orgId` predicate, which
 * is what makes this safe without an explicit `.eq('orgId', …)`.
 */
export async function listEmployees() {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', auth.user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return fail('Create your organisation first.');

  const { data: membership } = await supabase
    .from('memberships').select('role')
    .eq('orgId', orgId).eq('profileId', auth.user.id).eq('status', 'ACTIVE')
    .maybeSingle();
  if (!membership) return fail('You are not an active member of this organisation.');

  const [{ data: employees, error }, { data: zones }, { data: members }] =
    await Promise.all([
      supabase
        .from('employees')
        .select('id, employeeCode, displayName, assignedZoneId, profileId, active, createdAt')
        .order('active', { ascending: false })
        .order('displayName', { ascending: true }),
      // Only WORKSTATION zones can be a desk. A corridor or a break area is not
      // somebody's seat, and offering them would invite a seat prior that can
      // never bind.
      supabase
        .from('zones')
        .select('id, name, zoneType, cameraId')
        .eq('zoneType', 'WORKSTATION')
        .order('name', { ascending: true }),
      // Accounts an employee can be linked to (migration 022). Linking is what
      // lets somebody see their OWN figures — without it a VIEWER's policy
      // matches no employee row and they see nothing at all.
      supabase
        .from('memberships')
        // The embed names its constraint. `memberships` has TWO foreign keys
        // to `profiles` — `profileId` (the member) and `invitedById` (whoever
        // invited them) — so a bare `profiles(...)` is ambiguous and PostgREST
        // answers 300 PGRST201 rather than guessing. Measured: that 300 came
        // back as an empty member list and the picker rendered "no active
        // members to link to" for an org that plainly had one.
        .select('profileId, role, profiles!memberships_profileId_fkey(id, fullName, email)')
        .eq('orgId', orgId)
        .eq('status', 'ACTIVE'),
    ]);

  if (error) return fail(describeDbError(error, 'Could not load the employee roster.'));

  return {
    ok: true,
    employees: employees ?? [],
    zones: zones ?? [],
    members: (members ?? [])
      .filter((m) => m.profiles)
      .map((m) => ({
        id: m.profileId,
        role: m.role,
        name: m.profiles.fullName || m.profiles.email || 'Unnamed member',
        email: m.profiles.email ?? null,
      })),
    viewerRole: membership.role,
  };
}

/* ── Create ──────────────────────────────────────────────────────────────── */

export async function createEmployee(form) {
  const { supabase, user, orgId, error } = await requireCapability('employees.edit');
  if (error) return fail(error);

  const employeeCode = clean(form?.employeeCode, MAX_CODE);
  const displayName = clean(form?.displayName, MAX_NAME);
  const assignedZoneId = form?.assignedZoneId || null;

  if (!employeeCode) return fail('An employee code is required.');
  if (!displayName) return fail('A name is required.');

  const { data, error: dbError } = await supabase
    .from('employees')
    .insert({ orgId, employeeCode, displayName, assignedZoneId })
    .select('id, employeeCode, displayName, assignedZoneId, active, createdAt')
    .maybeSingle();

  if (dbError) return fail(describeDbError(dbError, 'Could not add this employee.'));

  await writeAudit(supabase, {
    orgId, user,
    action: 'employee.created',
    targetId: data?.id,
    metadata: { employeeCode, displayName, assignedZoneId },
  });

  revalidatePath('/dashboard/employees');
  return { ok: true, employee: data, message: `${displayName} was added.` };
}

/* ── Update ──────────────────────────────────────────────────────────────── */

export async function updateEmployee(id, form) {
  const { supabase, user, orgId, error } = await requireCapability('employees.edit');
  if (error) return fail(error);
  if (!id) return fail('No employee was specified.');

  const patch = {};
  if (form?.employeeCode !== undefined) {
    const v = clean(form.employeeCode, MAX_CODE);
    if (!v) return fail('An employee code is required.');
    patch.employeeCode = v;
  }
  if (form?.displayName !== undefined) {
    const v = clean(form.displayName, MAX_NAME);
    if (!v) return fail('A name is required.');
    patch.displayName = v;
  }
  // Distinguish "not sent" from "explicitly cleared": `null` unassigns the
  // desk, `undefined` leaves it alone.
  if (form?.assignedZoneId !== undefined) {
    patch.assignedZoneId = form.assignedZoneId || null;
  }

  // LINKING AN EMPLOYEE TO A LOGIN (migration 022)
  //
  // This is the one field here that changes who can SEE something rather than
  // what is recorded. Pointing an employee row at a profile grants that login
  // permanent sight of that person's measured figures, so the account must be
  // an active member of THIS organisation — otherwise an admin could hand a
  // stranger's login a window into their staff.
  //
  // The membership check is not the security boundary (a unique index and the
  // RLS policy are), but it is the one that produces a sentence instead of a
  // constraint violation.
  if (form?.profileId !== undefined) {
    const wanted = form.profileId || null;
    if (wanted) {
      const { data: member } = await supabase
        .from('memberships')
        .select('profileId')
        .eq('orgId', orgId)
        .eq('profileId', wanted)
        .eq('status', 'ACTIVE')
        .maybeSingle();
      if (!member) {
        return fail('That account is not an active member of this organisation.');
      }
      // ux_employees_profile would reject this anyway; catching it here says
      // WHO already holds the link instead of surfacing an index name.
      const { data: taken } = await supabase
        .from('employees')
        .select('id, displayName')
        .eq('profileId', wanted)
        .is('deletedAt', null)
        .maybeSingle();
      if (taken && taken.id !== id) {
        return fail(`That account is already linked to ${taken.displayName}.`);
      }
    }
    patch.profileId = wanted;
  }

  if (Object.keys(patch).length === 0) return fail('Nothing to change.');

  const { data, error: dbError } = await supabase
    .from('employees')
    .update(patch)
    .eq('id', id)
    .select('id, employeeCode, displayName, assignedZoneId, profileId, active, createdAt')
    .maybeSingle();

  if (dbError) return fail(describeDbError(dbError, 'Could not save this employee.'));
  // Zero rows means RLS filtered the row out — the caller is not permitted to
  // touch it, or it belongs to another organisation. Postgres reports no error
  // for that, so silence here would look like success.
  if (!data) return fail('That employee could not be updated. It may belong to another organisation.');

  await writeAudit(supabase, {
    orgId, user, action: 'employee.updated', targetId: id, metadata: patch,
  });

  revalidatePath('/dashboard/employees');
  return { ok: true, employee: data, message: `${data.displayName} was updated.` };
}

/* ── Activate / deactivate ───────────────────────────────────────────────── */

/**
 * Deactivating is the normal way to take someone off the floor.
 *
 * It also FREES THEIR DESK: `employees_one_active_per_zone` is partial on
 * `active`, so an inactive employee no longer blocks the zone. Verified against
 * a real database — reassigning the desk immediately afterwards succeeds.
 */
export async function setEmployeeActive(id, active) {
  const { supabase, user, orgId, error } = await requireCapability('employees.edit');
  if (error) return fail(error);
  if (!id) return fail('No employee was specified.');

  const next = Boolean(active);

  const { data, error: dbError } = await supabase
    .from('employees')
    .update({ active: next })
    .eq('id', id)
    .select('id, employeeCode, displayName, assignedZoneId, active, createdAt')
    .maybeSingle();

  if (dbError) return fail(describeDbError(dbError, 'Could not change this employee.'));
  if (!data) return fail('That employee could not be updated. It may belong to another organisation.');

  await writeAudit(supabase, {
    orgId, user,
    action: next ? 'employee.reactivated' : 'employee.deactivated',
    targetId: id,
  });

  revalidatePath('/dashboard/employees');
  return {
    ok: true,
    employee: data,
    message: `${data.displayName} was ${next ? 'reactivated' : 'deactivated'}.`,
  };
}

/* ── Remove ──────────────────────────────────────────────────────────────── */

/**
 * Soft-delete: sets `deletedAt`, clears the desk, and deactivates.
 *
 * Through the RPC, for the reason documented at the top of this file — a direct
 * `update({ deletedAt })` is refused by RLS because the row would make itself
 * invisible to `employee_select`.
 *
 * The row survives so that `employee_day_stats` from previous months keeps a
 * name to point at. Hard DELETE exists in the policies but the UI never uses
 * it: it would orphan exactly the history reports are built from.
 */
export async function removeEmployee(id) {
  const { supabase, user, orgId, error } = await requireCapability('employees.edit');
  if (error) return fail(error);
  if (!id) return fail('No employee was specified.');

  const { data, error: dbError } = await supabase
    .rpc('soft_delete_employee', { p_employee_id: id });

  if (dbError) return fail(describeDbError(dbError, 'Could not remove this employee.'));

  const row = Array.isArray(data) ? data[0] : data;
  if (!row) return fail('That employee no longer exists.');

  await writeAudit(supabase, {
    orgId, user, action: 'employee.removed', targetId: id,
    metadata: { displayName: row.displayName },
  });

  revalidatePath('/dashboard/employees');
  return { ok: true, message: `${row.displayName} was removed.` };
}
