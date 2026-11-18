'use server';

// frontend/app/settings/profile/actions.js
//
// The signed-in person's own profile: read it, change the editable parts, set or clear the picture.
// Everything goes through the CALLER's session, so `profile_update_self` (only your own row) is what allows the
// write; the checks here turn bad input into a sentence before the database sees it. The email is never written
// here: it belongs to the sign-in account.

import { revalidatePath } from 'next/cache';
import { createClient } from '../../lib/supabase/server';

const THEMES = ['SYSTEM', 'LIGHT', 'DARK'];
const fail = (message) => ({ ok: false, message });

async function me() {
  const supabase = createClient();
  if (!supabase) return { error: 'The database is not configured.' };
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  return { supabase, user: data.user };
}

/** Your profile, your organisation and your role in it, and how you sign in. */
export async function getProfile() {
  const { supabase, user, error } = await me();
  if (error) return fail(error);
  const { data: p, error: e } = await supabase
    .from('profiles')
    .select('email, fullName, avatarUrl, jobTitle, timezone, themePreference, currentOrgId, createdAt')
    .eq('id', user.id)
    .maybeSingle();
  if (e || !p) return fail('Could not load your profile.');
  let org = null;
  let membership = null;
  if (p.currentOrgId) {
    const [o, m] = await Promise.all([
      supabase.from('organisations').select('name, timezone').eq('id', p.currentOrgId).is('deletedAt', null).maybeSingle(),
      supabase.from('memberships').select('role, acceptedAt, createdAt').eq('orgId', p.currentOrgId).eq('profileId', user.id).eq('status', 'ACTIVE').maybeSingle(),
    ]);
    org = o.data || null;
    membership = m.data || null;
  }
  const providers = user.app_metadata?.providers || (user.app_metadata?.provider ? [user.app_metadata.provider] : []);
  return {
    ok: true,
    profile: {
      id: user.id,
      email: p.email || user.email,
      fullName: p.fullName || '',
      avatarUrl: p.avatarUrl || null,
      jobTitle: p.jobTitle || '',
      timezone: p.timezone || '',
      themePreference: THEMES.includes(p.themePreference) ? p.themePreference : 'SYSTEM',
      createdAt: p.createdAt,
      lastSignInAt: user.last_sign_in_at || null,
      providers,
      hasPassword: providers.includes('email'),
    },
    org: org ? { name: org.name, timezone: org.timezone } : null,
    role: membership?.role || null,
    joinedAt: membership?.acceptedAt || membership?.createdAt || null,
  };
}

function validTimezone(tz) {
  if (!tz) return true;
  try { new Intl.DateTimeFormat('en-US', { timeZone: tz }); return true; } catch { return false; }
}

/** Name, job title, your own time zone (blank: the organisation's) and theme. */
export async function updateProfile(input) {
  const { supabase, user, error } = await me();
  if (error) return fail(error);
  const fullName = String(input?.fullName ?? '').trim().replace(/\s+/g, ' ');
  const jobTitle = String(input?.jobTitle ?? '').trim().replace(/\s+/g, ' ');
  const timezone = String(input?.timezone ?? '').trim();
  const themePreference = String(input?.themePreference ?? 'SYSTEM');
  if (!fullName) return fail('Enter your name.');
  if (fullName.length > 160) return fail('Your name can be at most 160 characters.');
  if (jobTitle.length > 120) return fail('The job title can be at most 120 characters.');
  if (timezone.length > 64 || !validTimezone(timezone)) return fail('Choose a time zone from the list.');
  if (!THEMES.includes(themePreference)) return fail('Choose a theme.');

  const { error: e } = await supabase
    .from('profiles')
    .update({ fullName, jobTitle: jobTitle || null, timezone: timezone || null, themePreference, updatedAt: new Date().toISOString() })
    .eq('id', user.id);
  if (e) return fail('Your profile could not be saved. Try again.');
  // Keep the sign-in account's name in step, so a fresh session shows the same one.
  await supabase.auth.updateUser({ data: { full_name: fullName } }).catch(() => {});
  revalidatePath('/settings/profile');
  return { ok: true, message: 'Saved.' };
}

/** Points the profile at a picture you uploaded to your own avatars folder, or clears it (null). */
export async function setAvatar(url) {
  const { supabase, user, error } = await me();
  if (error) return fail(error);
  if (url !== null) {
    const base = `${process.env.NEXT_PUBLIC_SUPABASE_URL}/storage/v1/object/public/avatars/${user.id}/`;
    if (typeof url !== 'string' || !url.startsWith(base) || url.length > 1024) return fail('That picture could not be used.');
  }
  const { error: e } = await supabase.from('profiles').update({ avatarUrl: url, updatedAt: new Date().toISOString() }).eq('id', user.id);
  if (e) return fail('Your picture could not be saved. Try again.');
  revalidatePath('/settings/profile');
  return { ok: true, message: url ? 'Picture updated.' : 'Picture removed.' };
}
