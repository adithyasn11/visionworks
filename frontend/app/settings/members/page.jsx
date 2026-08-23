// frontend/app/settings/members/page.jsx
//
// Server half of the members screen.
//
// Two guards, in the order that gives the most useful outcome:
//
//   no organisation  →  /onboarding   (they cannot have members without one)
//   not an ADMIN     →  render read-only, do NOT redirect
//
// The second is a deliberate choice. A MANAGER or VIEWER has a legitimate
// reason to see who is in their organisation — it is their colleague list — but
// no business changing it. Redirecting them away would hide information they
// are entitled to; hiding only the controls matches what the database actually
// enforces. `membership_select` returns the roster to every member of the org,
// while `membership_insert/update/delete` require admin_org_ids(). The UI mirrors
// that boundary exactly rather than inventing a stricter or looser one.
//
// This satisfies "MANAGER cannot see the invite form" without pretending the
// page does not exist.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import MembersScreen from './MembersScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Members · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function MembersPage() {
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
  if (error || !data?.user) redirect('/login?next=/settings/members');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email')
    .eq('id', data.user.id)
    .maybeSingle();

  // Same guard as the dashboard layout: no org means onboarding is unfinished,
  // and a member list for an organisation that does not exist is meaningless.
  if (!profile?.currentOrgId) redirect('/onboarding');

  const { data: org } = await supabase
    .from('organisations')
    .select('name')
    .eq('id', profile.currentOrgId)
    .maybeSingle();

  return (
    <MembersScreen
      orgName={org?.name ?? 'your organisation'}
      viewer={{
        id: data.user.id,
        email: profile.email ?? data.user.email ?? null,
        fullName: profile.fullName ?? null,
      }}
    />
  );
}
