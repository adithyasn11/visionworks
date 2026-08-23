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

export default async function OnboardingPage() {
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
    .select('currentOrgId, fullName, email')
    .eq('id', data.user.id)
    .maybeSingle();

  // Already in an organisation — including the invited path, where the signup
  // trigger set currentOrgId automatically and the user should never see a
  // wizard at all.
  if (profile?.currentOrgId) redirect('/dashboard');

  // A sensible default beats an empty field, and the browser is the only thing
  // that knows where the user is. Resolved on the client, because the server
  // renders in UTC.
  return (
    <OnboardingWizard
      firstName={(profile?.fullName ?? '').trim().split(/\s+/)[0] || null}
      email={profile?.email ?? data.user.email ?? null}
    />
  );
}
