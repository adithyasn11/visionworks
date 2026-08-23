'use client';

// frontend/app/dashboard/DashboardShell.jsx
//
// Chrome for the manager's workspace: fixed sidebar on desktop, slide-over on
// mobile — the same structure as the founder console's PlatformShell, so the two
// halves of the product feel like one application.
//
// Navigation is view state rather than routing. The dashboard's sections share
// one polling data source and a live video session; making them separate routes
// would tear down the WebSocket on every tab change and re-fetch everything.

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, Video, Shapes, FileDown,
  Menu, X, LogOut, Loader2, ExternalLink, Users, Settings, CreditCard,
} from 'lucide-react';
import ThemeToggle from '../components/ThemeToggle';
import { supabase } from '../lib/supabase/browser';
import { can } from '../lib/permissions';

// `plan` sits with the other views rather than down with the settings links,
// because it is view state on this page like the rest of them — no navigation,
// no torn-down WebSocket. It is last because it is reference material: you read
// it once and go back to the space you are measuring.
//
// Shown to EVERY member, not just admins. `org_select` returns the tier to
// anyone in the organisation, and what a member pays for is not a secret from
// them. Nothing on the panel is writable, so there is no capability to gate.
export const VIEWS = [
  { id: 'overview', label: 'Overview',  icon: LayoutDashboard },
  { id: 'live',     label: 'Live feed', icon: Video },
  { id: 'zones',    label: 'Zones',     icon: Shapes },
  { id: 'reports',  label: 'Reports',   icon: FileDown },
  { id: 'plan',     label: 'Plan',      icon: CreditCard },
];

/**
 * One sidebar entry.
 *
 * Renders as a BUTTON on /dashboard, where the four sections are view state
 * sharing a polling source and a live WebSocket that routing would tear down —
 * and as a LINK everywhere else, where clicking must actually navigate back to
 * the dashboard and then select that view.
 *
 * Both look identical. The distinction is behavioural, not visual, so the
 * sidebar reads the same on every page.
 */
function NavItem({ view, active, onSelect, asLink = false }) {
  const Icon = view.icon;
  const className = `group relative w-full flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 text-left ${
    active
      ? 'bg-accent-soft text-accent'
      : 'text-ink-muted hover:text-ink hover:bg-surface-alt'
  }`;
  const marker = (
    <span
      aria-hidden="true"
      className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-r-full bg-[color:var(--accent)] transition-all duration-200 ${
        active ? 'h-5 opacity-100' : 'h-0 opacity-0'
      }`}
    />
  );

  if (asLink) {
    return (
      <Link href={`/dashboard?view=${view.id}`} onClick={onSelect} className={className}>
        {marker}
        <Icon className="w-[17px] h-[17px] shrink-0" strokeWidth={2.1} />
        <span className="truncate">{view.label}</span>
      </Link>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(view.id)}
      aria-current={active ? 'page' : undefined}
      className={`group relative w-full flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 text-left ${
        active
          ? 'bg-accent-soft text-accent'
          : 'text-ink-muted hover:text-ink hover:bg-surface-alt'
      }`}
    >
      {/* Active marker as a bar rather than a full fill — keeps the sidebar
          quiet while still being unmistakable at a glance. */}
      <span
        aria-hidden="true"
        className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-r-full bg-[color:var(--accent)] transition-all duration-200 ${
          active ? 'h-5 opacity-100' : 'h-0 opacity-0'
        }`}
      />
      <Icon className="w-[17px] h-[17px] shrink-0" strokeWidth={2.1} />
      <span className="truncate">{view.label}</span>
    </button>
  );
}

/**
 * `onViewChange` omitted -> the shell is being used by a routed page
 * (/settings/*), so the section nav becomes links back to the dashboard and
 * nothing is marked active. Passing it -> the dashboard itself, where the nav
 * switches view state in place.
 */
export default function DashboardShell({ view, onViewChange, user, role, children }) {
  const [open, setOpen] = useState(false);
  const routed = typeof onViewChange !== 'function';
  // Highlights whichever settings page is open, so the sidebar always shows
  // where you are — the same job the view marker does on the dashboard.
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      if (supabase) await supabase.auth.signOut({ scope: 'global' }).catch(() => {});
    } finally {
      // Full navigation: only the server route can clear the httpOnly cookies.
      window.location.href = '/auth/signout';
    }
  };

  const initials = (user?.fullName || user?.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');

  const sidebar = (
    <div className="flex h-full flex-col bg-surface border-r border-line themed">
      <div className="px-5 pt-5 pb-4">
        <Link href="/dashboard" className="flex items-center gap-2.5 w-max group">
          <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg group-hover:rotate-12 transition-transform duration-300">
            <div className="w-2 h-2 bg-white rounded-sm" />
          </div>
          <div className="leading-none">
            <div className="font-extrabold text-[15px] tracking-tight text-ink">VisionWorks</div>
            <div className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-accent mt-1">
              Workspace
            </div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 min-h-0 overflow-y-auto px-3 space-y-1" aria-label="Dashboard sections">
        {VIEWS.map((v) => (
          <NavItem
            key={v.id}
            view={v}
            asLink={routed}
            active={!routed && view === v.id}
            onSelect={routed ? () => setOpen(false) : (id) => { onViewChange(id); setOpen(false); }}
          />
        ))}
      </nav>

      {/* The privacy boundary, stated in the chrome rather than buried in a doc,
          so it is on screen whenever the product is being demonstrated. */}
      <div className="mx-3 mb-3 rounded-lg border border-line bg-surface-alt px-3 py-2.5">
        <p className="text-[11px] leading-relaxed text-ink-faint">
          <span className="font-bold text-ink-muted">No footage stored.</span>{' '}
          Frames are discarded after inference; only counts and postures are kept.
        </p>
      </div>

      {/* Members lives on its own route rather than in the nav above, because
          that nav switches view state inside this page — it shares a polling
          source and a WebSocket. A real navigation belongs down here with the
          other links that leave the workspace.

          Shown to every member, not only admins: `membership_select` returns
          the roster to anyone in the org, so this is their colleague list. What
          a non-admin does NOT get is the invite form or the row actions — see
          MembersScreen. Hiding the link would hide information the database
          willingly returns. */}
      {can(role, 'members.view') && (
      <div className="border-t border-line px-3 pt-3">
        <Link
          href="/settings/members"
          onClick={() => setOpen(false)}
          className={`relative w-full flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 ${
            pathname === '/settings/members'
              ? 'bg-accent-soft text-accent'
              : 'text-ink-muted hover:text-ink hover:bg-surface-alt'
          }`}
        >
          {pathname === '/settings/members' && (
            <span aria-hidden="true" className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-[color:var(--accent)]" />
          )}
          <Users className="w-[17px] h-[17px] shrink-0" strokeWidth={2.1} />
          <span className="truncate">Members</span>
        </Link>

        {/* Organisation settings. Shown to admins only — unlike Members, where
            the roster is every member's colleague list, these are the controls
            that decide when data is destroyed. `org_update` is admin-only, so
            advertising the page to a manager would offer a screen they can only
            read. They can still reach it directly if they need to. */}
        {can(role, 'org.settings') && (
          <Link
            href="/settings/organisation"
            onClick={() => setOpen(false)}
            className={`relative w-full flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 ${
              pathname === '/settings/organisation'
                ? 'bg-accent-soft text-accent'
                : 'text-ink-muted hover:text-ink hover:bg-surface-alt'
            }`}
          >
            {pathname === '/settings/organisation' && (
              <span aria-hidden="true" className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-[color:var(--accent)]" />
            )}
            <Settings className="w-[17px] h-[17px] shrink-0" strokeWidth={2.1} />
            <span className="truncate">Organisation</span>
          </Link>
        )}
      </div>
      )}

      <div className="border-t border-line px-3 py-3 space-y-2">
        <div className="flex items-center gap-2.5 px-1">
          <div className="w-8 h-8 rounded-lg bg-accent-soft text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-bold text-ink truncate">
              {user?.fullName || 'Manager'}
            </div>
            <div className="text-[11px] text-ink-faint truncate">{user?.email}</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link
            href="/"
            className="flex-1 flex items-center justify-center gap-1.5 h-9 rounded-xl border border-line bg-surface text-[12px] font-semibold text-ink-muted hover:text-ink hover:border-field transition-colors"
            title="Open the public site"
          >
            Site <ExternalLink className="w-3 h-3" />
          </Link>
          <button
            type="button"
            onClick={signOut}
            disabled={signingOut}
            className="w-9 h-9 rounded-xl border border-line bg-surface text-ink-muted flex items-center justify-center hover:text-accent hover:border-[color:var(--accent)] transition-colors disabled:opacity-50"
            aria-label="Sign out"
            title="Sign out"
          >
            {signingOut
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <LogOut className="w-4 h-4" />}
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-ground text-ink themed">
      <aside className="hidden lg:block fixed inset-y-0 left-0 w-[248px] z-30">
        {sidebar}
      </aside>

      <div
        className={`lg:hidden fixed inset-0 z-50 ${open ? '' : 'pointer-events-none'}`}
        aria-hidden={!open}
      >
        <div
          onClick={() => setOpen(false)}
          className={`absolute inset-0 bg-black/50 transition-opacity duration-200 ${
            open ? 'opacity-100' : 'opacity-0'
          }`}
        />
        <div
          className={`absolute inset-y-0 left-0 w-[264px] max-w-[82vw] transition-transform duration-250 ease-out ${
            open ? 'translate-x-0' : '-translate-x-full'
          }`}
        >
          {sidebar}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="absolute top-4 right-3 w-8 h-8 rounded-lg text-ink-muted hover:text-ink flex items-center justify-center"
            aria-label="Close menu"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="lg:pl-[248px]">
        <div className="lg:hidden sticky top-0 z-20 flex items-center gap-3 h-14 px-4 border-b border-line bg-ground/90 backdrop-blur-sm themed">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="w-9 h-9 -ml-1 rounded-lg text-ink-muted hover:text-ink flex items-center justify-center"
            aria-label="Open menu"
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="font-extrabold text-[15px] tracking-tight">VisionWorks</span>
          <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-accent">
            Workspace
          </span>
        </div>

        <main className="px-5 sm:px-7 lg:px-9 py-6 lg:py-8 max-w-[1400px]">
          {children}
        </main>
      </div>
    </div>
  );
}
