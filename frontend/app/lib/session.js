'use server';

// frontend/app/lib/session.js
//
// The caller's role in their current organisation — resolved on the server.
//
// WHY THIS IS A SERVER ACTION AND NOT A CLIENT QUERY
//
// The dashboard is a client component, so it could read `memberships` through
// the browser Supabase client directly. It should not, for one reason that
// matters and one that is merely tidy:
//
//   - The role decides which controls render. Resolving it here keeps one
//     definition of "what is my role" shared with every Server Action that
//     re-checks it, so the button and the action behind it can never disagree
//     about who the caller is.
//   - It is one round trip on the server rather than two from the browser.
//
// This is LAYER 1 input only. Nothing here is a security boundary — the value
// it returns decides what is *drawn*. A client that lies to itself about its
// own role gains nothing: every action re-checks (layer 2) and every policy
// re-checks (layer 3).

import { createClient, getAuthUser } from './supabase/server';
import { listCameras } from './platform/cameras';
import { getBadgeCounts } from './safety/queries';

/**
 * Everything the dashboard needs on opening, in ONE server call: the caller's role and organisation, the
 * cameras, and the two badge counts. Next.js runs a page's server actions one at a time, so these used to
 * be three calls queued in a row before the Overview could show anything; here the reads run together.
 */
export async function getDashboardStart(day) {
  const [viewer, cameras, badges] = await Promise.all([
    getViewerRole(),
    listCameras(),
    getBadgeCounts(day),
  ]);
  return { viewer, cameras, badges };
}

/** The shape returned when there is no usable session or membership. */
const EMPTY = Object.freeze({
  role: null, orgId: null, orgName: null, plan: null, planSelectedAt: null,
});

/**
 * `{ role, orgId, orgName, plan, planSelectedAt }` for the signed-in user.
 *
 * Cross-checks `currentOrgId` against an ACTIVE membership rather than trusting
 * the pointer alone — the same rule the tenant resolver and the member actions
 * use. A suspended member keeps a stale `currentOrgId`, and treating them as
 * still holding their old role would render controls they can no longer use.
 *
 * THE PLAN IS READ THROUGH THE MEMBERSHIP JOIN, NOT SEPARATELY.
 *
 * It could be a second query against `organisations`, and that would be one
 * more round trip returning the same row. More importantly, joining it here
 * means the tier is subject to the SAME membership check as the role: a
 * suspended member gets `plan: null` rather than the tier of an organisation
 * they no longer belong to. The join is RLS-filtered by `org_select`, so a
 * soft-deleted organisation also correctly returns nothing.
 */
export async function getViewerRole() {
  const supabase = createClient();
  if (!supabase) return EMPTY;

  const user = await getAuthUser(supabase);
  if (!user) return EMPTY;

  // The profile pointer and the caller's ACTIVE memberships are read AT THE SAME TIME (they do not depend on
  // each other); the membership of the current org is then picked from the list. One round trip of waiting
  // instead of two, and the same cross-check as before.
  const [{ data: profile }, { data: memberships }] = await Promise.all([
    supabase.from('profiles').select('currentOrgId').eq('id', user.id).maybeSingle(),
    supabase
      .from('memberships')
      .select('orgId, role, organisations:orgId (name, plan, planSelectedAt)')
      .eq('profileId', user.id)
      .eq('status', 'ACTIVE'),
  ]);

  const orgId = profile?.currentOrgId ?? null;
  if (!orgId) return EMPTY;

  const membership = (memberships || []).find((m) => m.orgId === orgId) ?? null;
  if (!membership) return { ...EMPTY, orgId };

  const org = membership.organisations ?? null;

  return {
    role: membership.role,
    orgId,
    orgName: org?.name ?? null,
    // Defaults to FREE rather than null when the organisation is readable but
    // the column somehow is not — the free tier is the safe direction to
    // degrade, and a null here would render "Unknown plan" to a paying member.
    plan: org ? (org.plan ?? 'FREE') : null,
    planSelectedAt: org?.planSelectedAt ?? null,
  };
}
