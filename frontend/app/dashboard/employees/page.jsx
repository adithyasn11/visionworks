// frontend/app/dashboard/employees/page.jsx
//
// Server half of the employees screen.
//
// Two guards, in the order that gives the most useful outcome:
//
//   no organisation  →  /onboarding   (there is nobody to roster without one)
//   not a manager    →  render read-only, do NOT redirect
//
// The second mirrors what the database actually enforces rather than inventing
// something stricter. `employee_select` returns the roster to every ACTIVE
// member of the org, while `employee_insert/update` require `manage_org_ids()`.
// A VIEWER has a legitimate reason to see who works here; they have no business
// changing it. Redirecting them would hide information they are entitled to.
// This is exactly the shape /settings/members already uses.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import EmployeesScreen from './EmployeesScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Employees · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function EmployeesPage() {
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
  if (error || !data?.user) redirect('/login?next=/dashboard/employees');

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
    <EmployeesScreen
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
