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

import { createClient } from './supabase/server';

/**
 * `{ role, orgId, orgName }` for the signed-in user, or nulls.
 *
 * Cross-checks `currentOrgId` against an ACTIVE membership rather than trusting
 * the pointer alone — the same rule the tenant resolver and the member actions
 * use. A suspended member keeps a stale `currentOrgId`, and treating them as
 * still holding their old role would render controls they can no longer use.
 */
export async function getViewerRole() {
  const supabase = createClient();
  if (!supabase) return { role: null, orgId: null, orgName: null };

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { role: null, orgId: null, orgName: null };

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', data.user.id)
    .maybeSingle();

  const orgId = profile?.currentOrgId ?? null;
  if (!orgId) return { role: null, orgId: null, orgName: null };

  const { data: membership } = await supabase
    .from('memberships')
    .select('role, organisations:orgId (name)')
    .eq('orgId', orgId)
    .eq('profileId', data.user.id)
    .eq('status', 'ACTIVE')
    .maybeSingle();

  if (!membership) return { role: null, orgId, orgName: null };

  return {
    role: membership.role,
    orgId,
    orgName: membership.organisations?.name ?? null,
  };
}
