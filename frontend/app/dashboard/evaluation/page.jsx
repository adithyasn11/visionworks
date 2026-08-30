// frontend/app/dashboard/evaluation/page.jsx
//
// Server half of the Step 17 results page.
//
// No data fetching here: the evaluation is a static artefact generated offline
// by `python -m backend.eval.report`, and the client half reads it from
// /public. That is deliberate — this is the page somebody opens to EXPLAIN the
// system, often with the backend stopped, and it should not depend on a
// running pipeline to render.

import { redirect } from 'next/navigation';
import { createClient } from '../../lib/supabase/server';
import EvaluationScreen from './EvaluationScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Evaluation · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function EvaluationPage() {
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
  if (error || !data?.user) redirect('/login?next=/dashboard/evaluation');

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
    <EvaluationScreen
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
