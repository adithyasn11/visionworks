// frontend/app/lib/permissions.js
//
// THE SINGLE SOURCE OF TRUTH FOR WHAT EACH ROLE MAY DO.
//
// Three roles exist in the schema and are meaningless until something enforces
// them. This file is the one place that answers "may this role do X", so the
// answer cannot drift between a hidden button and the action behind it.
//
// WHERE THIS IS APPLIED — ALL THREE LAYERS, NOT JUST THE FIRST
//
//   1. UI            hide controls a role cannot use          (this file)
//   2. Server        re-check inside every action/endpoint    (this file)
//   3. Postgres RLS  the actual boundary                      (003_rls_policies)
//
// Layer 1 is courtesy: it stops a VIEWER staring at a button that will fail.
// Layer 2 is the application's own guard, because a Server Action is a POST
// endpoint the browser can call directly and it does not inherit the page's
// render decisions — hiding a button removes it from the screen, not from the
// network.
//
// Layer 3 is the one that actually holds. Verified by direct measurement, not
// assumed: a VIEWER inserting a zone gets
// "violates row-level security policy for table zones", while MANAGER and ADMIN
// succeed. If layers 1 and 2 were both deleted tomorrow, the data would still
// be safe; if only layer 3 were deleted, nothing else would save it.
//
// So this file must MIRROR the policies, never invent its own rules. Each
// capability below names the policy that really enforces it. When they
// disagree, the policy is right and this file is a bug.

/** Roles, most privileged first. Matches the `Role` enum in schema.prisma. */
export const ROLES = ['ADMIN', 'MANAGER', 'VIEWER'];

export const ROLE_LABELS = {
  ADMIN: 'Administrator',
  MANAGER: 'Manager',
  VIEWER: 'Viewer',
};

/**
 * capability -> roles that hold it.
 *
 * Read this table against 003_rls_policies.sql. The `enforcedBy` note on each
 * group is the policy or function that makes the rule real; this map only
 * decides what the interface offers.
 */
const CAPABILITIES = {
  // ── Reading. Every ACTIVE member of an org may read its measurements. ──
  // enforcedBy: user_org_ids() — membership-based, so it covers all three
  // roles and excludes platform operators entirely.
  'dashboard.view':   ['ADMIN', 'MANAGER', 'VIEWER'],
  'analytics.view':   ['ADMIN', 'MANAGER', 'VIEWER'],
  'reports.export':   ['ADMIN', 'MANAGER', 'VIEWER'],
  'zones.view':       ['ADMIN', 'MANAGER', 'VIEWER'],
  'members.view':     ['ADMIN', 'MANAGER', 'VIEWER'],
  // enforcedBy: employee_select — "orgId IN user_org_ids()", the same
  // membership-based read every other roster gets. The employee list is a
  // colleague list; what a VIEWER does not get is the editing controls.
  'employees.view':   ['ADMIN', 'MANAGER', 'VIEWER'],
  // Step 16's team comparison — everyone's measured figures in one table.
  //
  // enforcedBy: employee_day_stat_select (migration 022), which returns other
  // people's rows only to `manage_org_ids()` — ADMIN and MANAGER. A VIEWER
  // sees exactly one employee's figures: their own.
  //
  // So this capability is not the security boundary, the policy is; it exists
  // so a VIEWER is not shown a link to a table that would render with one row
  // and look broken. Hiding the link is a courtesy, not a control.
  'team.compare':     ['ADMIN', 'MANAGER'],

  // ── Configuring the space. ──
  // enforcedBy: manage_org_ids() — zone_insert/update/delete,
  // camera_insert/update/delete, site_insert/update.
  // A VIEWER is read-only by definition; a MANAGER runs the space.
  'zones.edit':       ['ADMIN', 'MANAGER'],
  'cameras.edit':     ['ADMIN', 'MANAGER'],
  'sites.edit':       ['ADMIN', 'MANAGER'],
  // enforcedBy: employee_insert/update (manage_org_ids()) and
  // soft_delete_employee(), which re-checks ADMIN-or-MANAGER inside the
  // definer context. Naming who works here is configuring the space, exactly
  // like naming its zones.
  'employees.edit':   ['ADMIN', 'MANAGER'],
  // Running analysis writes telemetry, so it is a configuration act, not a
  // read. A VIEWER watching a live feed would be creating data.
  'analysis.run':     ['ADMIN', 'MANAGER'],

  // ── Governing the organisation. ──
  // enforcedBy: admin_org_ids() — membership_insert/update/delete, org_update.
  // Plus memberships_keep_an_admin, which stops the last admin removing
  // themselves regardless of what any of this says.
  'members.invite':   ['ADMIN'],
  'members.manage':   ['ADMIN'],
  'org.settings':     ['ADMIN'],
};

/** Every capability name, so a typo can be caught rather than silently denied. */
export const ALL_CAPABILITIES = Object.freeze(Object.keys(CAPABILITIES));

/**
 * May this role perform this capability?
 *
 * Fails CLOSED in every ambiguous case:
 *   - unknown role (null, undefined, a suspended member's absent role) -> false
 *   - unknown capability (a typo) -> false
 *
 * The second one matters more than it looks. If `can()` returned true for an
 * unrecognised name, a misspelled capability would silently grant everyone
 * everything, and the mistake would look like working code. Denying instead
 * makes a typo visible as a missing button rather than invisible as a hole.
 */
export function can(role, capability) {
  const allowed = CAPABILITIES[capability];
  if (!allowed) return false;
  if (typeof role !== 'string') return false;
  return allowed.includes(role);
}

/**
 * Every capability a role holds. For passing one object to a client component
 * instead of calling can() a dozen times during render.
 */
export function capabilitiesFor(role) {
  const out = {};
  for (const capability of ALL_CAPABILITIES) {
    out[capability] = can(role, capability);
  }
  return out;
}

/**
 * The message shown when something is refused.
 *
 * Names the role that WOULD be able to do it, because "you do not have
 * permission" leaves the reader with nowhere to go, while "an administrator can
 * do this" tells them exactly who to ask.
 */
export function denialMessage(capability) {
  const allowed = CAPABILITIES[capability];
  if (!allowed) return 'That action is not available.';

  if (allowed.length === 1 && allowed[0] === 'ADMIN') {
    return 'Only an administrator can do that.';
  }
  if (allowed.includes('MANAGER') && !allowed.includes('VIEWER')) {
    return 'Only an administrator or manager can do that.';
  }
  return 'You do not have permission to do that.';
}
