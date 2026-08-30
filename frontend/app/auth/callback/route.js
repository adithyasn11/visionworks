// frontend/app/auth/callback/route.js
//
// OAuth / email-confirmation callback.
//
// WHY THIS ROUTE IS REQUIRED NOW
//
// With localStorage sessions, the Supabase JS client picked the token out of the
// URL fragment in the browser (`detectSessionInUrl`) and there was nothing for
// the server to do — which is why the old code could point `redirectTo` straight
// at /dashboard.
//
// With cookie sessions the flow is PKCE: Supabase sends the browser back with a
// `?code=` query parameter, and that code must be exchanged for a session on the
// SERVER so the auth cookies are set with httpOnly. A client-side exchange
// cannot write httpOnly cookies, so middleware would never see the session and
// every protected route would bounce to /login — the confusing failure this
// route exists to prevent.
//
// After exchanging, route by role: operators to the console, everyone else to
// the dashboard.

import { NextResponse } from 'next/server';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';

export async function GET(request) {
  const requestUrl = new URL(request.url);
  const { searchParams } = requestUrl;
  const code = searchParams.get('code');
  const next = searchParams.get('next');

  // Behind `next dev`, request.url is the address this process bound, which is
  // not necessarily the address the browser typed. The forwarded host is what
  // the user actually sees, so cookies and redirects must follow it.
  const forwardedHost = request.headers.get('x-forwarded-host');
  const forwardedProto = request.headers.get('x-forwarded-proto');
  const host = forwardedHost || request.headers.get('host') || requestUrl.host;
  const proto = forwardedProto || requestUrl.protocol.replace(':', '');
  const origin = `${proto}://${host}`;

  // WHERE THE FLOW STARTED, VS WHERE IT LANDED
  //
  // The login page stamps `from` with the host it was opened at. If we are
  // running on a different host, Supabase discarded our redirectTo and
  // substituted the project's Site URL — which happens whenever the address
  // is missing from the Auth redirect allow-list.
  //
  // This matters far more than it looks. Landing on localhost from a phone is
  // a dead end, and the session cookies would be written for the WRONG host,
  // so even reopening the right address would still show you signed out. Fail
  // loudly with the fix instead of exchanging the code into the void.
  const from = searchParams.get('from');
  if (from && from !== host) {
    const back = new URL('/login', `${proto}://${from}`);
    back.searchParams.set('originMismatch', host);
    return NextResponse.redirect(back);
  }

  // Supabase reports provider-side failures (user cancelled, provider not
  // enabled) as query params rather than an HTTP error.
  const authError = searchParams.get('error') ?? searchParams.get('error_code');
  const authErrorDesc = searchParams.get('error_description');

  if (authError) {
    const back = new URL('/login', origin);
    back.searchParams.set('authError', authErrorDesc || authError);
    return NextResponse.redirect(back);
  }

  if (!code) {
    // Nothing to exchange — most likely someone opened this URL directly.
    return NextResponse.redirect(new URL('/login', origin));
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    const back = new URL('/login', origin);
    back.searchParams.set('authError', 'Supabase is not configured on the server.');
    return NextResponse.redirect(back);
  }

  const cookieStore = cookies();
  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        // A Route Handler *may* set cookies, unlike a Server Component — this is
        // the one place the session cookies are actually written on OAuth.
        cookiesToSet.forEach(({ name, value, options }) => {
          cookieStore.set(name, value, options);
        });
      },
    },
  });

  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    const back = new URL('/login', origin);
    back.searchParams.set('authError', error.message);
    return NextResponse.redirect(back);
  }

  // Same open-redirect guard as the login page: only same-origin paths.
  if (next && next.startsWith('/') && !next.startsWith('//')) {
    return NextResponse.redirect(new URL(next, origin));
  }

  // Customers land on /dashboard — see the note on resolveLandingPath() in
  // app/login/page.jsx. This is the OAuth arm of the same decision, and the two
  // must agree or a Google sign-in and a password sign-in would end up on
  // different pages.
  const { data: isOperator } = await supabase.rpc('is_platform_admin');
  return NextResponse.redirect(
    new URL(isOperator === true ? '/platform' : '/dashboard', origin),
  );
}
