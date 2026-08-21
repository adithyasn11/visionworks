// frontend/app/lib/supabase/server.js
//
// Server-side Supabase clients, plus the authoritative operator check.
//
// Everything here runs on the server and reads the session from httpOnly
// cookies. Two clients, because Server Components and Route Handlers have
// different cookie-writing rules in Next 14.

import { cookies } from 'next/headers';
import { createServerClient } from '@supabase/ssr';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const isSupabaseConfigured = Boolean(
  url && anonKey && !url.includes('your-supabase') && !anonKey.includes('your-supabase'),
);

/**
 * Client for Server Components and Route Handlers.
 *
 * Server Components are not permitted to set cookies — Next throws if you try.
 * The token-refresh path inside the SDK naturally wants to write the rotated
 * token, so the setter swallows that specific failure. This is safe because
 * middleware refreshes the session on every request, so the cookie is already
 * current by the time a Server Component reads it.
 */
export function createClient() {
  if (!isSupabaseConfigured) return null;
  const cookieStore = cookies();

  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        } catch {
          // Called from a Server Component — middleware already handled the
          // refresh, so there is nothing to recover here.
        }
      },
    },
  });
}

/**
 * The signed-in user, or null.
 *
 * Uses getUser(), not getSession(). getSession() reads the cookie and trusts
 * its contents; getUser() verifies the JWT against Supabase's auth server. On
 * a page that lists every customer on the platform, a forged cookie must not be
 * enough, so the extra round trip is the right trade.
 */
export async function getServerUser() {
  const supabase = createClient();
  if (!supabase) return null;
  const { data, error } = await supabase.auth.getUser();
  if (error) return null;
  return data.user ?? null;
}

/**
 * Where a signed-in visitor should be sent, or null when signed out.
 *
 * Used by the landing page to redirect a returning user straight to the home
 * their role owns, so a live session never lands on the marketing page.
 *
 * Returns ONLY a path. No email, no role flag, no user object: the caller is a
 * public page, and anything richer would end up either rendered into public
 * markup or serialised across the server/client boundary. A destination is all
 * a redirect needs.
 *
 * Returns null rather than throwing when auth is unreachable — a public page
 * must still render for everyone if the auth service is down, and failing
 * closed here would take the marketing site offline with it.
 */
export async function getViewerLanding() {
  const supabase = createClient();
  if (!supabase) return null;

  try {
    const { data, error } = await supabase.auth.getUser();
    if (error || !data?.user) return null;

    const { data: isOperator } = await supabase.rpc('is_platform_admin');
    return { href: isOperator === true ? '/platform' : '/dashboard' };
  } catch {
    return null;
  }
}

/**
 * Is the caller an active platform operator?
 *
 * Asks the database via is_platform_admin() rather than checking a hardcoded
 * email list or a JWT claim. The database is the single source of truth, so
 * revoking access in platform_admins takes effect immediately — no token to
 * expire, no list to redeploy.
 *
 * This is the AUTHORITATIVE check. Middleware does a cheap cookie-based
 * pre-filter for fast redirects, but middleware can be bypassed (a direct fetch
 * to a Route Handler never runs it), so every server-side entry point into
 * /platform calls this.
 */
export async function isPlatformAdmin() {
  const supabase = createClient();
  if (!supabase) return false;

  const { data: userData } = await supabase.auth.getUser();
  if (!userData?.user) return false;

  const { data, error } = await supabase.rpc('is_platform_admin');
  if (error) return false;
  return data === true;
}

/**
 * Session + operator status in one round trip's worth of work.
 *
 * Returned as a small object so a layout can decide between "send to login",
 * "send to dashboard" and "render" without three separate awaits.
 */
export async function getPlatformContext() {
  const supabase = createClient();
  if (!supabase) {
    return { configured: false, user: null, isOperator: false, profile: null };
  }

  // is_platform_admin() reads auth.uid() from the request's own session, so it
  // does not need the user object first — run it alongside getUser() instead of
  // after it. The profiles query does need user.id, so it can't join this batch.
  const [{ data: userData }, { data: isOp }] = await Promise.all([
    supabase.auth.getUser(),
    supabase.rpc('is_platform_admin'),
  ]);
  const user = userData?.user ?? null;
  if (!user) return { configured: true, user: null, isOperator: false, profile: null };

  const { data: profile } = await supabase
    .from('profiles')
    .select('id, email, fullName, avatarUrl')
    .eq('id', user.id)
    .maybeSingle();

  return {
    configured: true,
    user,
    isOperator: isOp === true,
    profile: profile ?? null,
  };
}
