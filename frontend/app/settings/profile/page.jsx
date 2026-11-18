// frontend/app/settings/profile/page.jsx
//
// Server half of the profile screen: signed in, and with an organisation (the sidebar needs a role). Everything
// on the page is the person's own, so every member reaches it.

import { redirect } from 'next/navigation';
import { createClient, getAuthUser } from '../../lib/supabase/server';
import ProfileScreen from './ProfileScreen';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Profile · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function ProfilePage() {
  const supabase = createClient();
  if (!supabase) redirect('/login');

  const verified = await getAuthUser(supabase);
  if (!verified) redirect('/login?next=/settings/profile');

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, fullName, email, avatarUrl')
    .eq('id', verified.id)
    .maybeSingle();
  if (!profile?.currentOrgId) redirect('/onboarding');

  const { data: membership } = await supabase
    .from('memberships').select('role')
    .eq('orgId', profile.currentOrgId).eq('profileId', verified.id)
    .eq('status', 'ACTIVE').maybeSingle();
  if (!membership) redirect('/home');

  return (
    <ProfileScreen
      role={membership.role}
      viewer={{ id: verified.id, email: profile.email ?? verified.email ?? null, fullName: profile.fullName ?? null, avatarUrl: profile.avatarUrl ?? null }}
    />
  );
}
