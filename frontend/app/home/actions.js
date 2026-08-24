'use server';

// frontend/app/home/actions.js
//
// The single write behind the demo checkout.
//
// ─────────────────────────────────────────────────────────────────────────────
//  NO PAYMENT IS PROCESSED HERE, AND NO CARD DATA IS ACCEPTED.
// ─────────────────────────────────────────────────────────────────────────────
//
// `confirmPlan` records which tier the user picked. That is the entire
// transaction. There is no processor, no charge, no card number reaching this
// file — the checkout form has no card fields at all, deliberately, so there is
// no path by which a real card could be typed into a demo that cannot protect
// it. If this ever becomes real billing, the card must go directly from the
// browser to the provider's hosted element and never touch this server.
//
// WHY A SERVER ACTION AND NOT A CLIENT-SIDE SUPABASE CALL
//
// The browser holds an anon-key client that could call `select_plan` itself, so
// this adds no authorisation the database does not already enforce. It exists
// to keep the plan id VALIDATED AGAINST THE CATALOGUE in one place, and to give
// billing a single server-side seam — the one function a future webhook handler
// would sit next to. A client-side RPC would scatter that across components.

import { createClient } from '../lib/supabase/server';
import { isPlanId, getPlan } from '../lib/plans';

const fail = (message) => ({ ok: false, message });

/**
 * Resolve the caller. getUser(), never getSession(): a Server Action is a POST
 * endpoint the browser can call directly, so the cookie's own claims are not
 * evidence. Same pattern as onboarding/actions.js.
 */
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
 * Record the chosen tier and report where the user should go next.
 *
 * Returns `{ ok, next }` rather than redirecting. A `redirect()` thrown inside
 * a Server Action travels back with the action's response and navigates before
 * the caller's own code runs — which is exactly the bug that made the
 * onboarding wizard skip steps 2 and 3 (see the long note in
 * onboarding/actions.js). The client decides when to navigate; this only says
 * where.
 */
export async function confirmPlan(formData) {
  const { supabase, user, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const planId = String(formData.get('plan') ?? '').trim();

  // The database enum is MONTHLY | YEARLY; the UI speaks lowercase. Mapped
  // here rather than trusting the form value, so an unrecognised string cannot
  // reach a Postgres cast and fail with a message nobody can act on.
  const rawPeriod = String(formData.get('period') ?? '').toLowerCase();
  const period = rawPeriod === 'yearly' ? 'YEARLY' : 'MONTHLY';

  // Validated against the catalogue, not merely non-empty. The value reaches a
  // Postgres enum: an unrecognised string would be rejected by the database
  // with a cast error nobody could act on, so it is refused here with a
  // sentence instead.
  if (!isPlanId(planId)) {
    return fail('That plan is not available. Choose one of the plans listed.');
  }

  const { data, error } = await supabase.rpc('select_plan', {
    p_plan: planId,
    p_period: period,
  });

  if (error) {
    // The only expected failures are a lost session or a profile row that has
    // not committed yet, both of which the function itself reports as a row.
    // An error here means something structural — surface it rather than
    // pretending the plan was saved.
    const msg = String(error?.message ?? '');
    if (/row-level security/i.test(msg)) {
      return fail('You do not have permission to do that.');
    }
    if (/invalid input value for enum/i.test(msg)) {
      // Reachable only if plans.js and the Postgres enum have drifted apart.
      return fail('That plan is not available. Choose one of the plans listed.');
    }
    return fail('Could not record your plan. Please try again.');
  }

  // A TABLE-returning function arrives from PostgREST as an array of rows.
  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) {
    return fail(row?.message ?? 'Could not record your plan. Please try again.');
  }

  // Where to go next depends on whether this person already has an
  // organisation, and it is decided HERE rather than in the browser because
  // the browser would have to be told, and telling it means trusting it.
  //
  // An existing org means they came back to change tier — they belong on the
  // dashboard, not through onboarding again, which would create a SECOND
  // organisation and silently move `currentOrgId` away from their data.
  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', user.id)
    .maybeSingle();

  return {
    ok: true,
    next: profile?.currentOrgId ? '/dashboard' : '/onboarding',
    planName: getPlan(planId)?.name ?? planId,
  };

  // Nothing is revalidated. /home, /onboarding and /dashboard are all
  // force-dynamic, so there is no cached render holding a stale plan — and
  // revalidating a shared layout from inside an action re-renders the CURRENT
  // route before the client can act on the result, which is the precise
  // mechanism that broke the onboarding wizard three times.
}
