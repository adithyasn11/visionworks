// frontend/app/home/page.jsx
//
// The signed-in home page: welcome, what to do next, and the plans.
//
// WHERE THIS SITS IN THE FLOW
//
//   /login  ->  /home  ->  (pick a plan)  ->  /home/checkout  ->  /onboarding
//                                                              ->  /dashboard
//
// Before this page existed, sign-in dropped the user straight into the
// dashboard guard, which bounced them to /onboarding with no explanation of
// what they were being asked to create or why. This page is that explanation.
//
// THIS PAGE IS A PRE-MEMBERSHIP GATE. IT IS NOT REACHABLE AFTER ONBOARDING.
//
// Anyone who already belongs to an organisation is redirected to /dashboard,
// the same way /onboarding ejects them. The reasoning is the reasoning for the
// whole flow: /home exists to sell a plan to someone who has not got one. Once
// they have, the dashboard is the product and there is nothing here they need
// — plan and billing live in the workspace itself (see PlanSection in
// app/dashboard/), so nothing is lost by closing this door.
//
// It also removes a whole class of confusion: with /home reachable there were
// two "home" screens, two places showing the tier, and a Dashboard link on a
// marketing page that a member with no org could not use.
//
// The redirect checks REACHABILITY, not just a non-null pointer. `org_select`
// hides soft-deleted rows, so a member of a deleted org keeps a `currentOrgId`
// that resolves to nothing — sending them to /dashboard would bounce them
// straight back to /onboarding. Treating unreachable as "no org" leaves them
// here, where they can create a replacement.
//
// WHAT IS READ, AND WHY EACH FIELD
//
//   fullName / email     the greeting and the account menu
//   currentOrgId         decides whether this page renders at all
//   selectedPlan         a tier chosen but not yet spent on an organisation —
//                        lets the page resume an abandoned checkout

import { redirect } from 'next/navigation';
import { createClient } from '../lib/supabase/server';
import HomeScreen from './HomeScreen';

// The whole page is about who is asking. Never cached across visitors.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Home · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function HomePage() {
  const supabase = createClient();

  if (!supabase) {
    return (
      <div className="themed min-h-screen bg-ground text-ink flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="text-xl font-black tracking-tight mb-2">Not connected</h1>
          <p className="text-[14px] text-ink-muted leading-relaxed">
            Add <code className="font-mono text-[13px] text-accent">NEXT_PUBLIC_SUPABASE_URL</code>{' '}
            and <code className="font-mono text-[13px] text-accent">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{' '}
            to <code className="font-mono text-[13px]">frontend/.env.local</code>, then restart the
            dev server.
          </p>
        </div>
      </div>
    );
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) redirect('/login?next=/home');

  const { data: profile } = await supabase
    .from('profiles')
    .select('fullName, email, currentOrgId, selectedPlan')
    .eq('id', data.user.id)
    .maybeSingle();

  // The organisation is read separately rather than joined, because the join
  // would be filtered by `org_select` anyway and a soft-deleted org must come
  // back as "no org" rather than as a row with a name.
  //
  // This SELECT is the reachability check described above: it is RLS-filtered,
  // so a deleted or revoked organisation returns nothing and the user stays on
  // this page instead of being bounced into a dashboard that would hang.
  if (profile?.currentOrgId) {
    const { data: orgRow } = await supabase
      .from('organisations')
      .select('id')
      .eq('id', profile.currentOrgId)
      .maybeSingle();

    // Already a member of a working organisation — the gate is closed.
    if (orgRow) redirect('/dashboard');
  }

  return (
    <HomeScreen
      email={profile?.email ?? data.user.email ?? null}
      fullName={profile?.fullName ?? null}
      // A tier chosen but not yet spent on an organisation, so returning here
      // resumes the abandoned checkout rather than starting over.
      pendingPlan={profile?.selectedPlan ?? null}
    />
  );
}
