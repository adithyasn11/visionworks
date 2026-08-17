// frontend/app/platform/layout.jsx
//
// LAYER 2 of the three-layer guard.
//
//   layer 1  middleware.js       fast redirect, keeps the session fresh
//   layer 2  this file           authoritative is_platform_admin() check
//   layer 3  Postgres RLS        returns nothing to a non-operator
//
// Middleware alone is not sufficient: it can be bypassed (a direct fetch to a
// nested Route Handler does not always run it) and it is a single point of
// failure. This layout runs on the server for every /platform route, so no
// console markup is ever generated for a non-operator — not hidden by CSS, not
// filtered on the client. Not rendered at all.
//
// `redirect()` from a Server Component throws, so it cannot be accidentally
// fallen through.

import { redirect } from 'next/navigation';
import { getPlatformContext } from '../lib/supabase/server';
import PlatformShell from './PlatformShell';

// Never statically rendered or cached: the output depends on who is asking, and
// a cached console page would be the worst possible thing to serve to the wrong
// person.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata = {
  title: 'Platform · VisionWorks',
  // Keep the console out of search results if it is ever reachable publicly.
  robots: { index: false, follow: false },
};

export default async function PlatformLayout({ children }) {
  const { configured, user, isOperator, profile } = await getPlatformContext();

  if (!configured) {
    return (
      <div className="min-h-screen bg-ground text-ink flex items-center justify-center px-6 themed">
        <div className="max-w-md text-center">
          <h1 className="text-xl font-black tracking-tight mb-2">Not connected</h1>
          <p className="text-[14px] text-ink-muted leading-relaxed">
            Add <code className="font-mono text-[13px] text-accent">NEXT_PUBLIC_SUPABASE_URL</code>{' '}
            and <code className="font-mono text-[13px] text-accent">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{' '}
            to <code className="font-mono text-[13px]">frontend/.env.local</code>, then restart the
            dev server.
          </p>
        </div>
      </div>
    );
  }

  if (!user) redirect('/login?next=/platform');

  // Not an operator: send to the customer dashboard rather than showing a 403.
  // A 403 confirms the console exists; a redirect says nothing.
  if (!isOperator) redirect('/dashboard');

  return (
    <PlatformShell
      operator={{
        email: profile?.email ?? user.email,
        fullName: profile?.fullName ?? null,
      }}
    >
      {children}
    </PlatformShell>
  );
}
