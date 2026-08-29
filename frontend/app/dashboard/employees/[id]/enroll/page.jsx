// frontend/app/dashboard/employees/[id]/enroll/page.jsx
//
// Server half of the face enrolment screen.
//
// Same two guards as the employees list, and the same reasoning: no
// organisation means onboarding is unfinished, and a non-manager gets the page
// read-only rather than a redirect. But there is one difference that matters
// here — enrolling a face is the act that makes a named person recognisable by
// camera, so this page does NOT show a VIEWER the capture controls at all.
// `employee_insert`/`face_template_insert` require manage_org_ids(), and the
// backend endpoint re-checks `employees.edit` regardless of what this renders.

import { redirect, notFound } from 'next/navigation';
import { createClient } from '../../../../lib/supabase/server';
import EnrollScreen from './EnrollScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Enrol a face · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function EnrollPage({ params }) {
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
  if (error || !data?.user) redirect(`/login?next=/dashboard/employees/${id}/enroll`);

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
    // RLS scopes this to the caller's org, so an id from another organisation
    // simply returns nothing and becomes a 404 — the same answer as an id that
    // does not exist, which is what stops this page confirming whether another
    // tenant's employee is real.
    supabase.from('employees')
      .select('id, displayName, employeeCode, active')
      .eq('id', id).maybeSingle(),
  ]);

  if (!employee) notFound();

  return (
    <EnrollScreen
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
