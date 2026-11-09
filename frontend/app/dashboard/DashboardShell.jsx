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

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, Video, Shapes, FileDown,
  Menu, X, LogOut, Loader2, ExternalLink, Users, Settings, CreditCard,
} from 'lucide-react';
import ThemeToggle from '../components/ThemeToggle';
import { supabase } from '../lib/supabase/browser';
import { can } from '../lib/permissions';
import { getOpenAlerts } from '../lib/safety/queries';

// `plan` sits with the other views rather than down with the settings links,
// because it is view state on this page like the rest of them — no navigation,
// no torn-down WebSocket. It is last because it is reference material.
//
// PLAN IS ADMIN-ONLY, via `capability: 'org.settings'`. Billing is a
// governance matter, the same as organisation settings, member management and
// deletion — a MANAGER runs the space but does not decide what the account
// pays for. `change_plan()` enforces this itself; the gate here just stops
// offering a screen whose every control would be refused.
//
// A view with no `capability` is open to every ACTIVE member.
export const VIEWS = [
  { id: 'overview', label: 'Overview',  icon: LayoutDashboard },
  { id: 'live',     label: 'Live feed', icon: Video },
  { id: 'zones',    label: 'Zones',     icon: Shapes },
  { id: 'reports',  label: 'Reports',   icon: FileDown },
  { id: 'plan',     label: 'Plan',      icon: CreditCard, capability: 'org.settings' },
];

/**
 * What every sidebar row looks like.
 *
 * This string was written out seven times — once in NavItem and once per
 * routed link below. Seven hand-maintained copies of the same class list is
 * how a sidebar ends up with two slightly different row heights, and it made
 * tuning the vertical rhythm a seven-place edit.
 *
 * `py-[7px]` rather than `py-2`: at eleven rows, the 2px saved on each is 22px
 * back in the list, which is the difference between the whole nav fitting on a
 * 700px laptop screen and not. It is still a 34px target, comfortably above
 * the 24px minimum for a pointer and unchanged for touch, where the mobile
 * drawer has room to spare.
 */
const NAV_ROW = 'relative w-full flex items-center gap-3 rounded-lg px-3 py-[7px] '
  + 'text-[13.5px] font-semibold transition-colors duration-150';

const NAV_ROW_ACTIVE = 'bg-accent-soft text-accent';
const NAV_ROW_IDLE = 'text-ink-muted hover:text-ink hover:bg-surface-alt';

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
function NavItem({ view, active, onSelect, asLink = false, badge = 0 }) {
  const Icon = view.icon;
  // A count of things waiting (open alerts). Shown as a number, never as colour alone.
  const pill = badge > 0 ? (
    <span className="ml-auto shrink-0 min-w-[20px] px-1.5 py-0.5 rounded-md bg-accent text-white text-[11px] font-bold tabular-nums text-center">
      {badge > 99 ? '99+' : badge}
      <span className="sr-only"> open alerts</span>
    </span>
  ) : null;
  const className = `group ${NAV_ROW} text-left ${
    active ? NAV_ROW_ACTIVE : `${NAV_ROW_IDLE} hover:translate-x-0.5`
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
        {pill}
      </Link>
    );
  }

  // Reuses `className` and `marker` rather than repeating them. The button and
  // the link MUST look identical — the difference between them is behavioural
  // — and two hand-maintained copies of the same class string is exactly how
  // they drift apart.
  return (
    <button
      type="button"
      onClick={() => onSelect(view.id)}
      aria-current={active ? 'page' : undefined}
      className={className}
    >
      {marker}
      <Icon className="w-[17px] h-[17px] shrink-0" strokeWidth={2.1} />
      <span className="truncate">{view.label}</span>
      {pill}
    </button>
  );
}

/**
 * `onViewChange` omitted -> the shell is being used by a routed page
 * (/settings/*), so the section nav becomes links back to the dashboard and
 * nothing is marked active. Passing it -> the dashboard itself, where the nav
 * switches view state in place.
 */
export default function DashboardShell({ view, onViewChange, user, role, alertCount, children }) {
  const [open, setOpen] = useState(false);
  const routed = typeof onViewChange !== 'function';
  // The dashboard page passes the count it already polls. A routed page (settings) does not, so the
  // shell asks for it itself and the badge is the same on every screen.
  const [ownCount, setOwnCount] = useState(0);
  useEffect(() => {
    if (typeof alertCount === 'number') return undefined;
    let live = true;
    const load = () => getOpenAlerts().then((r) => { if (live && r && !r.error) setOwnCount(r.count || 0); }).catch(() => {});
    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
    return () => { live = false; clearInterval(timer); };
  }, [alertCount]);
  const shownAlerts = typeof alertCount === 'number' ? alertCount : ownCount;
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
      {/* Brand. `h-16` matches AppHeader and the landing navbar exactly, so the
          sidebar's first rule lines up with the mobile top bar and the eye
          reads one horizontal band across the whole product. */}
      <div className="h-16 px-5 flex items-center border-b border-line shrink-0">
        <Link href="/dashboard" className="flex items-center gap-2.5 w-max group">
          <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover:shadow-red-600/50 group-hover:rotate-12 transition-all duration-300">
            <div className="w-2 h-2 bg-white rounded-sm" />
          </div>
          <span className="font-extrabold text-[16px] tracking-tight text-ink group-hover:text-accent transition-colors duration-300">
            VisionWorks
          </span>
        </Link>
      </div>

      {/* ONE SCROLL REGION FOR BOTH NAV GROUPS.

          `Workspace` and `Account` sit in one scroller so the whole list
          scrolls as a single thing at the sidebar's own edge, and only when
          it is genuinely taller than the space. `overscroll-contain` stops a
          scroll that reaches the end of the list from continuing into the page
          behind it. */}
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain nav-scroll pb-1">
      <nav className="px-3 pt-3.5 space-y-0.5" aria-label="Dashboard sections">
        {/* Group label. Five unlabelled items and two more below the rule read
            as one long undifferentiated list; naming the groups is what makes
            "where am I" answerable at a glance. */}
        <p className="px-3 pb-1 font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
          Monitor
        </p>
        {VIEWS.filter((v) => !v.capability || can(role, v.capability)).map((v) => (
          <NavItem
            key={v.id}
            view={v}
            asLink={routed}
            active={!routed && view === v.id}
            badge={v.id === 'overview' ? shownAlerts : 0}
            onSelect={routed ? () => setOpen(false) : (id) => { onViewChange(id); setOpen(false); }}
          />
        ))}
      </nav>

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
      <div className="mt-3 pt-3 border-t border-line px-3 space-y-0.5">
        <p className="px-3 pb-1 font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
          Account
        </p>
        <Link
          href="/settings/members"
          onClick={() => setOpen(false)}
          className={`${NAV_ROW} ${
            pathname === '/settings/members' ? NAV_ROW_ACTIVE : NAV_ROW_IDLE
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
            className={`${NAV_ROW} ${
              pathname === '/settings/organisation' ? NAV_ROW_ACTIVE : NAV_ROW_IDLE
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

      {/* The privacy boundary, stated in the chrome rather than buried in a doc,
          so it is on screen whenever the product is being demonstrated. Quiet
          text under a rule, inside the scroll region: it is a footnote, not a
          thing to click. The full sentence lives in `title` so the visible text
          can stay short enough to survive the clip. */}
      <p
        title="No continuous video is stored. Frames are discarded after analysis; workers are identified only by an anonymous track, never by name."
        className="mx-3 mt-3 mb-1 pt-2.5 border-t border-line text-[10.5px] leading-snug text-ink-faint"
      >
        <span className="font-semibold text-ink-muted">No video stored.</span>{' '}
        Workers stay anonymous.
      </p>
      </div>{/* end of the single scroll region */}

      <div className="shrink-0 border-t border-line px-3 py-3 space-y-2">
        <div className="flex items-center gap-2.5 px-1">
          <div className="w-8 h-8 rounded-lg bg-accent-soft text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-bold text-ink truncate">
              {user?.fullName || (user?.email ? user.email.split('@')[0] : 'Account')}
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
      {/* First thing a keyboard user reaches: jump past the navigation to the page. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
      >
        Skip to the page content
      </a>
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
            Safety
          </span>
        </div>

        {/* mx-auto is load-bearing: `max-w` alone pins the column to the left
            edge and leaves a growing band of dead space on a wide monitor. */}
        <main id="main-content" tabIndex={-1} className="px-5 sm:px-7 lg:px-9 py-6 lg:py-8 max-w-[1400px] mx-auto focus:outline-none">
          {children}
        </main>
      </div>
    </div>
  );
}
