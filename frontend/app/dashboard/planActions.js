'use server';

// frontend/app/dashboard/planActions.js
//
// How much of the plan's allowance this organisation is actually using.
//
// WHY THE COUNTS COME FROM POSTGRES AND NOT FROM THREE CLIENT QUERIES
//
// The Plan panel could count cameras, sites and members itself with three
// supabase-js calls. It should not, for the reason that matters everywhere else
// in this codebase: the numbers it displays would then be computed differently
// from the numbers the TRIGGERS enforce, and the two would eventually disagree.
//
// `plan_usage()` (015_plan_limits.sql) is the same query shape the triggers use
// — notably counting INVITED memberships as occupied seats, which a naive
// "count the members" would miss and then show 2/3 on an org that cannot invite
// anyone else.
//
// The function is deliberately NOT `SECURITY DEFINER`: it runs as the caller,
// so `org_select` and `user_org_ids()` filter it and a member can only ever
// read their own organisation's usage. This is a display, not an enforcement
// decision — the enforcement already happened in the trigger.

import { createClient } from '../lib/supabase/server';

/**
 * `{ ok, usage }` for the caller's current organisation.
 *
 * Returns `ok: false` rather than throwing on every failure path, because the
 * caller renders this inside a panel — an exception here would take out the
 * whole dashboard view for what is, at worst, a missing progress bar.
 */
export async function getPlanUsage() {
  const supabase = createClient();
  if (!supabase) return { ok: false, message: 'Supabase is not configured.' };

  const { data: userData, error: authError } = await supabase.auth.getUser();
  if (authError || !userData?.user) {
    return { ok: false, message: 'Your session has expired.' };
  }

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', userData.user.id)
    .maybeSingle();

  const orgId = profile?.currentOrgId;
  if (!orgId) return { ok: false, message: 'No organisation selected.' };

  const { data, error } = await supabase.rpc('plan_usage', { p_org_id: orgId });
  if (error) return { ok: false, message: 'Could not read your plan usage.' };

  // A TABLE-returning function arrives from PostgREST as an array of rows. An
  // empty array is the soft-deleted case — `plan_usage` filters
  // `deletedAt IS NULL`, so an organisation on its way out returns nothing
  // rather than a row of zeroes that would read as "you have used none of your
  // allowance".
  const row = Array.isArray(data) ? data[0] : data;
  if (!row) return { ok: false, message: 'That organisation is no longer available.' };

  return {
    ok: true,
    usage: {
      plan: row.plan,
      cameras: { used: row.cameras_used, max: row.cameras_max },
      sites: { used: row.sites_used, max: row.sites_max },
      seats: { used: row.seats_used, max: row.seats_max },
      retention: { used: row.retention_days, max: row.retention_max },
    },
  };
}
