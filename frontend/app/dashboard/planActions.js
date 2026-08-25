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
import { isPlanId } from '../lib/plans';

/**
 * Resolve the caller. getUser(), never getSession(): a Server Action is a POST
 * endpoint the browser can call directly, so the cookie's own claims are not
 * evidence. Same pattern as home/actions.js and onboarding/actions.js.
 */
async function requireUser() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured.' };
  }
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  return { supabase, user: data.user };
}

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
      billingPeriod: row.billing_period,
      startedAt: row.started_at,
      renewsAt: row.renews_at,
      cameras: { used: row.cameras_used, max: row.cameras_max },
      sites: { used: row.sites_used, max: row.sites_max },
      seats: { used: row.seats_used, max: row.seats_max },
      retention: { used: row.retention_days, max: row.retention_max },
    },
  };
}

/**
 * Switch tier. ADMIN only — enforced in `change_plan()` itself, not here.
 *
 * The function refuses a DOWNGRADE that the organisation's current usage would
 * already exceed, and says so with the real numbers ("that plan allows 1 camera
 * and this organisation has 8"). The limit triggers in 015 only fire on INSERT,
 * so nothing else would stop an org landing on a tier it is already over —
 * every existing row would keep working while every new one was refused.
 */
export async function changePlan(planId, period = 'MONTHLY') {
  const { supabase, error: authError } = await requireUser();
  if (authError) return { ok: false, message: authError };

  if (!isPlanId(planId)) {
    return { ok: false, message: 'That plan is not available.' };
  }
  if (period !== 'MONTHLY' && period !== 'YEARLY') {
    return { ok: false, message: 'Choose a monthly or yearly term.' };
  }

  const { data, error } = await supabase.rpc('change_plan', {
    p_plan: planId,
    p_period: period,
  });

  if (error) {
    const msg = String(error?.message ?? '');
    if (/row-level security/i.test(msg)) {
      return { ok: false, message: 'You do not have permission to change the plan.' };
    }
    if (/invalid input value for enum/i.test(msg)) {
      // Only reachable if plans.js and the Postgres enum have drifted apart.
      return { ok: false, message: 'That plan is not available.' };
    }
    return { ok: false, message: 'Could not update the plan. Please try again.' };
  }

  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) return { ok: false, message: row?.message ?? 'Could not update the plan.' };

  return { ok: true, message: row.message };
}
