// frontend/middleware.js
//
// Edge middleware. Two jobs, in this order:
//
//   1. REFRESH THE SESSION on every request. Supabase access tokens are
//      short-lived; without a refresh here the cookie goes stale and Server
//      Components start seeing a logged-out user mid-session. This is the
//      reason middleware must run even on routes it does not guard.
//
//   2. PRE-FILTER protected routes so an unauthenticated visitor is redirected
//      before any page code runs.
//
// WHAT THIS IS *NOT*
//
// Middleware is not the security boundary. It can be bypassed — a direct fetch
// to a Route Handler does not always pass through it, and it never sees
// server-to-server calls. It exists to make redirects fast and to keep the
// session fresh. The real enforcement is two layers deeper:
//
//   layer 2  the /platform server layout re-checks is_platform_admin()
//   layer 3  Postgres RLS returns nothing to a non-operator regardless
//
// Three independent layers, so a mistake in one does not expose customer data.

import { NextResponse } from 'next/server';
import { createServerClient } from '@supabase/ssr';

/** Routes that require a signed-in user. */
const PROTECTED_PREFIXES = ['/platform', '/dashboard', '/onboarding', '/settings', '/home'];

/** Routes a signed-in user should be bounced away from. */
const AUTH_PAGES = ['/login', '/signup'];

export async function middleware(request) {
  const { pathname } = request.nextUrl;

  // A single response object threaded through the Supabase client so any
  // rotated auth cookie is attached to the response the browser actually gets.
  let response = NextResponse.next({ request });

  // The auth routes must run untouched. /auth/signout is deleting the session
  // cookies, and the token refresh below would race it — writing a freshly
  // rotated cookie onto the very response that is trying to clear them, which
  // leaves the user still signed in. /auth/callback is mid-PKCE-exchange and
  // has no session to refresh yet.
  if (pathname.startsWith('/auth/')) {
    return response;
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  // Unconfigured environment: let everything through rather than locking the
  // developer out of their own app with confusing redirects. The pages
  // themselves show a "not connected" message.
  if (!url || !anonKey || url.includes('your-supabase')) {
    return response;
  }

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  // getUser() rather than getSession(): it validates the JWT with the auth
  // server instead of trusting the cookie's contents. This call is also what
  // triggers the token refresh, which is why it runs on every request.
  const { data: { user } } = await supabase.auth.getUser();

  const isProtected = PROTECTED_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );

  // ── Not signed in, asking for a protected route ──
  if (isProtected && !user) {
    const redirect = request.nextUrl.clone();
    redirect.pathname = '/login';
    // Drop the original query string before adding `next`. Without this, a URL
    // like /platform/organisations?filter=attention carries filter= and sort=
    // onto the login page, where they mean nothing. `next` keeps the full path
    // plus its query so the reader still lands exactly where they intended.
    redirect.search = '';
    const target = request.nextUrl.search ? `${pathname}${request.nextUrl.search}` : pathname;
    redirect.searchParams.set('next', target);
    return NextResponse.redirect(redirect);
  }

  // ── Signed in, sitting on /login or /signup ──
  // Route by role so an operator lands in the console rather than a customer
  // dashboard they have no organisation for.
  //
  // Customers go to /dashboard — the product itself. Middleware deliberately
  // does NOT check whether they have an organisation or a plan: that needs a
  // database read on every request, and the answer is already enforced one
  // layer deeper. The chain resolves in one server hop each:
  //
  //   /dashboard   no org           -> /onboarding
  //   /onboarding  no plan, no org  -> /home
  //   /home        has org          -> /dashboard
  //
  // Every link is a `redirect()` in a Server Component, so nothing renders
  // before it moves and the user sees only the screen they belong on.
  //
  // Operators still go to /platform: they run VisionWorks and never create a
  // customer organisation, so plans and onboarding mean nothing to them.
  if (user && AUTH_PAGES.includes(pathname)) {
    const { data: isOperator } = await supabase.rpc('is_platform_admin');
    const target = request.nextUrl.clone();
    target.pathname = isOperator === true ? '/platform' : '/dashboard';
    target.search = '';
    return NextResponse.redirect(target);
  }

  // ── Signed in, asking for /platform ──
  // Redirect non-operators to /dashboard rather than showing a 403: there is no
  // reason to confirm to a customer that a founder console exists.
  if (user && (pathname === '/platform' || pathname.startsWith('/platform/'))) {
    const { data: isOperator } = await supabase.rpc('is_platform_admin');
    if (isOperator !== true) {
      const target = request.nextUrl.clone();
      target.pathname = '/dashboard';
      target.search = '';
      return NextResponse.redirect(target);
    }
  }

  return response;
}

export const config = {
  // Everything except static assets and image optimisation. The session refresh
  // has to run broadly, but there is no point paying for it on a PNG.
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff2?)$).*)',
  ],
};
