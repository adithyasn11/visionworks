// frontend/app/onboarding/page.jsx
//
// Server half of onboarding: the inverse of the dashboard guard.
//
//   dashboard/layout.jsx   no org  -> /onboarding
//   this file              has org -> /dashboard
//
// Both are needed. Without this one, an onboarded user who bookmarked
// /onboarding could run the wizard again and create a second organisation they
// did not want, silently switching `currentOrgId` away from the one holding all
// their data.
//
// The exception is a user who has an org but never finished the optional steps.
// They are not sent away — `onboardedAt` is set by create_organisation(), so
// "has an org" and "finished onboarding" are the same moment. Anyone with an
// org has, by definition, completed the only mandatory step.

import { redirect } from 'next/navigation';
import { createClient } from '../lib/supabase/server';
import OnboardingWizard from './OnboardingWizard';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Set up your organisation · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function OnboardingPage({ searchParams }) {
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
  if (error || !data?.user) redirect('/login?next=/onboarding');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email, selectedPlan')
    .eq('id', data.user.id)
    .maybeSingle();

  // Already in an organisation — including the invited path, where the signup
  // trigger set currentOrgId automatically and the user should never see a
  // wizard at all.
  //
  // EXCEPT while the wizard is mid-flow. Step 1 creates the organisation, so
  // from step 2 onward `currentOrgId` IS set — and any re-render of this page
  // would eject the user to /dashboard before they reach the site and camera
  // steps.
  //
  // The primary fix is in the actions: they no longer call
  // `revalidatePath('/dashboard', 'layout')` mid-wizard, because that
  // re-rendered THIS page as part of the action's response and redirected the
  // user away before the client could advance the step. Both routes are
  // `force-dynamic` with `revalidate = 0`, so nothing was being cached and
  // nothing needed invalidating.
  //
  // This marker remains as a second line of defence: any future re-render of
  // /onboarding while the wizard is open — a refresh, a back-navigation, an
  // action added later — would otherwise eject the user the same way.
  //
  // `?step=` is set by the wizard once the org exists. It is a NAVIGATION hint
  // only, never an authorisation one: a user who forges it still has to get
  // past this same guard on any other route, and the steps it unlocks are two
  // optional forms writing to their own organisation.
  // REACHABILITY, NOT THE POINTER. This distinction is a fixed infinite loop,
  // not a refinement.
  //
  // `dashboard/layout.jsx` ejects to /onboarding when the organisation is
  // unreachable — `org_select` hides soft-deleted rows, so a member of a
  // deleted org keeps a `currentOrgId` that resolves to nothing. If THIS guard
  // trusted the bare pointer, the two would disagree and bounce forever:
  //
  //   /dashboard  -> org unreachable  -> /onboarding
  //   /onboarding -> pointer non-null -> /dashboard      (loop)
  //
  // That is the same stranded-account failure that followed deleting an
  // organisation, in a new shape: last time the dashboard let the user in and
  // hung; this time the two guards would ping-pong. Asking the same question
  // both places — "is there an organisation I can actually read" — is what
  // makes the graph terminate.
  //
  // The SELECT is RLS-filtered, so it answers exactly that question.
  let orgReachable = false;
  if (profile?.currentOrgId) {
    const { data: orgRow } = await supabase
      .from('organisations')
      .select('id')
      .eq('id', profile.currentOrgId)
      .maybeSingle();
    orgReachable = Boolean(orgRow);
  }

  const inWizard = searchParams?.step === '2' || searchParams?.step === '3';
  if (orgReachable && !inWizard) redirect('/dashboard');

  // THE PLAN GATE. Nobody creates an organisation without choosing a tier.
  //
  // This is the second half of the rule /home enforces from the other side:
  // /home ejects members to /dashboard, and this ejects non-members without a
  // plan back to /home. Between them, the only way into the wizard is through
  // the plans page, and the only way out of it is with an organisation.
  //
  // WHY THIS IS SAFE TO BLOCK ON — the cases that would otherwise strand:
  //
  //   * an INVITED member never picks a plan, but the signup trigger sets
  //     `currentOrgId`, so they are caught by the redirect ABOVE and go to
  //     /dashboard. They never reach this line.
  //   * a user recovering from a deleted organisation had `selectedPlan`
  //     consumed by the org they no longer have — so they land on /home and
  //     choose again, which is the correct recovery, not a dead end.
  //   * a checkout that succeeded and then failed to navigate has the plan
  //     RECORDED, so this guard passes on the next request.
  //
  // Every path leads somewhere usable. The redirect target is /home#pricing
  // rather than /home so the reader arrives at the cards, not the hero.
  //
  // ── THE `inWizard` CONDITION IS LOAD-BEARING, NOT BELT-AND-BRACES ──
  //
  // `create_organisation()` CLEARS `selectedPlan` — it is a pending choice that
  // gets spent when the org is created (014_plans.sql). So from step 2 onward
  // the plan is legitimately NULL, and a bare `if (!selectedPlan)` here would
  // eject the user to /home in the middle of the wizard, one step after they
  // paid for it.
  //
  // That is the exact failure this page has already suffered three times, from
  // three different causes: a guard that is correct on first render and wrong
  // on re-render. A REACHABLE organisation is the proof that the plan WAS
  // spent, so the two conditions together mean "no plan, and no organisation to
  // show for it" — the only state that genuinely belongs on /home.
  //
  // `orgReachable` rather than the pointer, for the same reason as above: a
  // user whose organisation was deleted has a stale pointer AND a spent plan,
  // and trusting the pointer here would keep them in a wizard they cannot
  // finish instead of sending them to /home to choose again.
  if (!profile?.selectedPlan && !orgReachable) redirect('/home#pricing');

  // Null from step 2 onward, by design — see above. The wizard renders the
  // badge only when it has one, so a spent plan simply stops being advertised
  // rather than showing "null plan".
  const pendingPlan = profile?.selectedPlan ?? null;

  // A sensible default beats an empty field, and the browser is the only thing
  // that knows where the user is. Resolved on the client, because the server
  // renders in UTC.
  return (
    <OnboardingWizard
      firstName={(profile?.fullName ?? '').trim().split(/\s+/)[0] || null}
      email={profile?.email ?? data.user.email ?? null}
      pendingPlan={pendingPlan}
    />
  );
}
