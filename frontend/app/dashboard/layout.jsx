// frontend/app/dashboard/layout.jsx
//
// THE ORG GUARD.
//
// A signed-in user with no organisation cannot use this dashboard: every RLS
// policy on customer data resolves through `user_org_ids()`, which reads
// ACTIVE memberships. With no membership the set is empty, and every query
// correctly returns nothing. Rendering the dashboard for such a user shows a
// working page full of zeroes — the worst failure mode, because it looks like
// the product measured an empty office rather than that the account is not
// finished. So they go to /onboarding instead.
//
// WHY A LAYOUT AND NOT MIDDLEWARE
//
// Middleware already checks "is there a session". It does not check "is there
// an organisation", and it should not: that needs a database read on every
// request including static assets, and middleware runs at the edge on the whole
// matcher. This layout runs once per dashboard navigation, on the server, and
// `redirect()` throws — so it cannot be accidentally fallen through.
//
// This is the same LAYER 2 role that platform/layout.jsx plays for the founder
// console. Layer 3 (RLS) still holds if this file were removed; what would be
// lost is the redirect, not the isolation.

import { redirect } from 'next/navigation';
import { createClient } from '../lib/supabase/server';

// Never cached: the answer depends on who is asking, and a cached "you have an
// org" would let a brand-new account straight past the guard.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Dashboard · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function DashboardLayout({ children }) {
  const supabase = createClient();

  // Unconfigured environment: render the dashboard, which shows its own
  // "not connected" state. Redirecting to /onboarding here would be worse —
  // onboarding cannot work without Supabase either, so the user would bounce
  // between two broken pages with no explanation.
  if (!supabase) return children;

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) redirect('/login?next=/dashboard');

  // `profiles` is readable by its owner (profile_select_self), so this needs no
  // elevated client. `maybeSingle()` rather than `single()`: the profile row is
  // created by a trigger on auth.users, and in the seconds around a first OAuth
  // sign-in it is possible — if rare — to arrive before the trigger has
  // committed. `single()` would throw a 500 on that race; this way the user
  // simply gets sent to onboarding, which is the correct destination anyway.
  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', data.user.id)
    .maybeSingle();

  if (!profile?.currentOrgId) redirect('/onboarding');

  return children;
}
