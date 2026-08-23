// frontend/app/settings/organisation/page.jsx
//
// Server half of organisation settings.
//
// Same guard shape as /settings/members: no org means onboarding is
// unfinished, so there is nothing to configure. A non-admin is NOT redirected —
// they see the settings read-only, because `org_select` returns the row to
// every member and hiding an organisation's retention policy from its own
// manager would hide something they have a legitimate interest in. Only writing
// is admin-gated, which is exactly what `org_update` enforces.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import OrganisationSettingsScreen from './OrganisationSettingsScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Organisation · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function OrganisationSettingsPage() {
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
  if (error || !data?.user) redirect('/login?next=/settings/organisation');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', data.user.id)
    .maybeSingle();

  if (!profile?.currentOrgId) redirect('/onboarding');

  const { data: membership } = await supabase
    .from('memberships').select('role')
    .eq('orgId', profile.currentOrgId).eq('profileId', data.user.id)
    .eq('status', 'ACTIVE').maybeSingle();

  return (
    <OrganisationSettingsScreen
      role={membership?.role ?? null}
      viewer={{
        id: data.user.id,
        email: data.user.email ?? null,
        fullName: null,
      }}
    />
  );
}
