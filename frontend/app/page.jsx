// frontend/app/page.jsx
//
// The public landing page, and the role-based entry gate for the whole app.
//
// A signed-in visitor never sees this page: they are redirected to the home
// their role owns — operators to /platform, everyone else to /dashboard —
// before any marketing HTML is generated. Only signed-out visitors get the
// landing page itself.
//
// WHY THE REDIRECT LIVES ON THE SERVER
//
// Deciding this in the browser would mean shipping the marketing page to a
// signed-in user, letting it paint, and then bouncing them — a visible flash,
// and a moment where a client-side check could simply be skipped. Resolving it
// here means the redirect is in the HTTP response itself.
//
// NOTHING ABOUT THE VIEWER IS RENDERED. The landing page is public and
// cacheable-looking by nature; putting an email address or role hint in its
// markup would leak who is signed in to anything that sees the response —
// browser history, a screenshot, a shared screen, an intermediary. The gate
// decides and redirects; it never describes.

import React from 'react';
import { redirect } from 'next/navigation';
import { getViewerLanding } from './lib/supabase/server';
import LandingPageClient from './LandingPageClient';

// Depends on the caller's cookies, so it must never be cached across visitors.
export const dynamic = 'force-dynamic';

export default async function LandingPage() {
  const viewer = await getViewerLanding();

  // redirect() throws, so there is no path where a signed-in user falls through
  // and renders the marketing page anyway.
  if (viewer) redirect(viewer.href);

  return <LandingPageClient />;
}
