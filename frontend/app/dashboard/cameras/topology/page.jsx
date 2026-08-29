// frontend/app/dashboard/cameras/topology/page.jsx
//
// Server half of the camera topology editor.
//
// The same two guards as every other dashboard page, and the same reasoning: no
// organisation means onboarding is unfinished, and a VIEWER gets the page
// read-only rather than a redirect. The layout of a building is not a secret
// from the people working in it — `camera_link_select` returns it to every
// member — but only `manage_org_ids()` may change it.

import { redirect } from 'next/navigation';
import { createClient } from '../../../lib/supabase/server';
import TopologyScreen from './TopologyScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Camera layout · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function TopologyPage() {
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
  if (error || !data?.user) redirect('/login?next=/dashboard/cameras/topology');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email')
    .eq('id', data.user.id)
    .maybeSingle();

  if (!profile?.currentOrgId) redirect('/onboarding');

  const [{ data: org }, { data: membership }] = await Promise.all([
    supabase.from('organisations').select('name').eq('id', profile.currentOrgId).maybeSingle(),
    supabase.from('memberships').select('role')
      .eq('orgId', profile.currentOrgId).eq('profileId', data.user.id)
      .eq('status', 'ACTIVE').maybeSingle(),
  ]);

  return (
    <TopologyScreen
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
