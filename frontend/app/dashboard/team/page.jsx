// frontend/app/dashboard/team/page.jsx
//
// Server half of Step 16's team comparison.
//
// Mirrors the employees page: resolve the session, the org, and the caller's
// role, then hand a client screen the data it needs to render its own chrome.
// The figures themselves are fetched client-side through `getTeamComparison`,
// so the window selector re-queries without a full navigation.
//
// The role is passed for the SHELL, not as a permission check. What a caller
// may see is decided by `employee_day_stat_select` in migration 022 — ADMIN and
// MANAGER get the team, a VIEWER gets their own row. Gating this page in the
// route as well would be a second, weaker copy of that rule, and the two would
// eventually disagree.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import TeamScreen from './TeamScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Team · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function TeamPage() {
  const supabase = createClient();

  if (!supabase) {
    return (
      <div className="themed min-h-screen bg-ground text-ink flex items-center justify-center px-6">
        <div className="max-w-md text-center">
          <h1 className="text-xl font-black tracking-tight mb-2">Not connected</h1>
          <p className="text-[14px] text-ink-muted leading-relaxed">
            Add your Supabase URL and anon key to{' '}
            <code className="font-mono text-[13px]">frontend/.env.local</code>, then
            restart the dev server.
          </p>
        </div>
      </div>
    );
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) redirect('/login?next=/dashboard/team');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email')
    .eq('id', data.user.id)
    .maybeSingle();

  if (!profile?.currentOrgId) redirect('/onboarding');

  const [{ data: org }, { data: membership }] = await Promise.all([
    supabase.from('organisations').select('name')
      .eq('id', profile.currentOrgId).maybeSingle(),
    supabase.from('memberships').select('role')
      .eq('orgId', profile.currentOrgId).eq('profileId', data.user.id)
      .eq('status', 'ACTIVE').maybeSingle(),
  ]);

  return (
    <TeamScreen
      orgName={org?.name ?? 'your organisation'}
      initialRole={membership?.role ?? null}
      viewer={{
        id: data.user.id,
        email: profile.email ?? data.user.email ?? null,
        fullName: profile.fullName ?? null,
      }}
    />
  );
}
