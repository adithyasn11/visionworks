// frontend/app/auth/signout/route.js
//
// Server-side sign-out.
//
// WHY A ROUTE HANDLER AND NOT JUST supabase.auth.signOut() IN THE BROWSER
//
// The session lives in httpOnly cookies written server-side by /auth/callback.
// A browser-side signOut() clears what the JS client can see and revokes the
// refresh token upstream, but it cannot reliably delete httpOnly cookies —
// only the server that set them can. If any auth cookie survives, middleware
// still resolves a user on the next request and redirects straight back to
// /platform or /dashboard, which looks exactly like "clicking login logs me
// in automatically".
//
// A Route Handler *may* write cookies (a Server Component may not), so this is
// the one place the session can actually be torn down.

import { NextResponse } from 'next/server';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';

async function signOut(request) {
  const { origin } = new URL(request.url);
  const cookieStore = cookies();

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (url && anonKey) {
    const supabase = createServerClient(url, anonKey, {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) => {
            cookieStore.set(name, value, options);
          });
        },
      },
    });

    // scope: 'global' revokes the refresh token everywhere, not just this
    // browser. A session that stays valid upstream can be silently resumed.
    try {
      await supabase.auth.signOut({ scope: 'global' });
    } catch {
      // Already-expired or unreachable session: fall through and still clear
      // the cookies below. Failing to sign out must never leave the user stuck
      // on a page they are trying to leave.
    }
  }

  // Belt and braces: delete anything Supabase left behind. The SDK normally
  // expires its own cookies, but a partial write (or a cookie from an older
  // SDK version with a different name) would otherwise keep middleware
  // resolving a user forever.
  for (const cookie of cookieStore.getAll()) {
    if (cookie.name.startsWith('sb-') || cookie.name.includes('supabase')) {
      cookieStore.set(cookie.name, '', { path: '/', maxAge: 0 });
    }
  }

  // 303 so the browser issues a GET for /login regardless of the method used
  // to reach this route.
  return NextResponse.redirect(new URL('/login', origin), { status: 303 });
}

export async function POST(request) {
  return signOut(request);
}

export async function GET(request) {
  return signOut(request);
}
