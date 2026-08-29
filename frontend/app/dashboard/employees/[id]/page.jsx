// frontend/app/dashboard/employees/[id]/page.jsx
//
// Server half of the per-employee figures.
//
// Step 14 needs somewhere to show its confidence banner, and the banner is
// meaningless without the numbers it qualifies. This is the smallest honest
// version of that: the daily rollup, each figure carrying its confidence, with
// unattributed time shown rather than hidden.
//
// The full per-employee dashboard — timelines, 7/30-day trends, charts — is
// Step 15. This page deliberately stops short of that.

import { redirect, notFound } from 'next/navigation';
import { createClient } from '../../../lib/supabase/server';
import EmployeeDaysScreen from './EmployeeDaysScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Employee figures · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function EmployeeDetailPage({ params }) {
  const { id } = await params;
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
  if (error || !data?.user) redirect(`/login?next=/dashboard/employees/${id}`);

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email')
    .eq('id', data.user.id)
    .maybeSingle();

  if (!profile?.currentOrgId) redirect('/onboarding');

  const [{ data: org }, { data: membership }, { data: employee }] = await Promise.all([
    supabase.from('organisations').select('name').eq('id', profile.currentOrgId).maybeSingle(),
    supabase.from('memberships').select('role')
      .eq('orgId', profile.currentOrgId).eq('profileId', data.user.id)
      .eq('status', 'ACTIVE').maybeSingle(),
    supabase.from('employees')
      .select('id, displayName, employeeCode, active')
      .eq('id', id).maybeSingle(),
  ]);

  if (!employee) notFound();

  return (
    <EmployeeDaysScreen
      orgName={org?.name ?? 'your organisation'}
      initialRole={membership?.role ?? null}
      employee={employee}
      viewer={{
        id: data.user.id,
        email: profile.email ?? data.user.email ?? null,
        fullName: profile.fullName ?? null,
      }}
    />
  );
}
