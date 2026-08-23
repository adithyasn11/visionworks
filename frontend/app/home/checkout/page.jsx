// frontend/app/home/checkout/page.jsx
//
// The demo checkout. Server half: validate the requested tier, resolve who is
// asking, and hand a fully-resolved plan to the client screen.
//
// ─────────────────────────────────────────────────────────────────────────────
//  NO PAYMENT IS TAKEN AND NO CARD DETAILS ARE COLLECTED.
// ─────────────────────────────────────────────────────────────────────────────
//
// See app/lib/plans.js for the full statement. The short version: there is no
// processor, the form has no card fields, and the only write is
// `select_plan()`, which records a preference.
//
// WHY THE PLAN COMES FROM THE QUERY STRING AND THAT IS FINE
//
// `?plan=` is attacker-controlled, so it is validated against the catalogue
// here and AGAIN in `confirmPlan` before it reaches Postgres. Forging it grants
// nothing worth having: the tier gates advisory limits, and no RLS policy in
// the schema consults it — cross-tenant isolation is membership, not billing.
// An unrecognised value redirects back to the plans rather than rendering an
// empty checkout for a plan that does not exist.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import { isPlanId, getPlan } from '../../lib/plans';
import CheckoutScreen from './CheckoutScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Confirm your plan · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function CheckoutPage({ searchParams }) {
  const supabase = createClient();

  if (!supabase) {
    return (
      <div className="themed min-h-screen bg-ground text-ink flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="text-xl font-black tracking-tight mb-2">Not connected</h1>
          <p className="text-[14px] text-ink-muted leading-relaxed">
            Add your Supabase URL and anon key to{' '}
            <code className="font-mono text-[13px]">frontend/.env.local</code>, then restart the
            dev server.
          </p>
        </div>
      </div>
    );
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) redirect('/login?next=/home');

  const requested = String(searchParams?.plan ?? '');
  // Fails closed to the plans section rather than defaulting to a tier. Picking
  // one on the user's behalf would mean recording a choice they never made.
  if (!isPlanId(requested)) redirect('/home#pricing');

  const plan = getPlan(requested);

  const { data: profile } = await supabase
    .from('profiles')
    .select('fullName, email, currentOrgId')
    .eq('id', data.user.id)
    .maybeSingle();

  // Members are ejected, exactly as /home ejects them — checkout is part of the
  // pre-membership gate, and a user who already has a working organisation has
  // no business re-running the flow that creates one.
  //
  // The check is REACHABILITY, not a non-null pointer: `org_select` hides
  // soft-deleted rows, so a member of a deleted organisation keeps a
  // `currentOrgId` resolving to nothing. Treating unreachable as "no org" lets
  // them choose a plan and build a replacement, which is the recovery path.
  if (profile?.currentOrgId) {
    const { data: orgRow } = await supabase
      .from('organisations')
      .select('id')
      .eq('id', profile.currentOrgId)
      .maybeSingle();

    if (orgRow) redirect('/dashboard');
  }

  return (
    <CheckoutScreen
      plan={plan}
      email={profile?.email ?? data.user.email ?? null}
      fullName={profile?.fullName ?? null}
    />
  );
}
