'use client';

// frontend/app/platform/PlatformShell.jsx
//
// The founder console chrome: fixed sidebar on desktop, slide-over on mobile.
//
// Uses the same design tokens as the marketing and auth pages (--ground,
// --surface, --ink, --accent) so light and dark both work without a second
// palette. The console should read as the same product, not a bolted-on admin
// tool — which is also why the theme toggle lives in the sidebar footer rather
// than being dropped.

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  LayoutDashboard, Building2, Activity, ShieldCheck, ScrollText,
  Menu, X, LogOut, Loader2, ExternalLink,
} from 'lucide-react';
import ThemeToggle from '../components/ThemeToggle';
import { supabase } from '../lib/supabase/browser';

const NAV = [
  { href: '/platform',               label: 'Overview',      icon: LayoutDashboard, exact: true },
  { href: '/platform/organisations', label: 'Organisations', icon: Building2 },
  { href: '/platform/health',        label: 'Health',        icon: Activity },
  { href: '/platform/operators',     label: 'Operators',     icon: ShieldCheck },
  { href: '/platform/audit',         label: 'Audit log',     icon: ScrollText },
];

function NavLink({ item, active, onNavigate }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={`group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-semibold transition-colors duration-150 ${
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
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

export default function PlatformShell({ children, operator }) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const isActive = (item) =>
    item.exact ? pathname === item.href : pathname.startsWith(item.href);

  const signOut = async () => {
    setSigningOut(true);
    try {
      // Clear the client session first, then hand off to the server route: only
      // it can delete the httpOnly cookies middleware reads, and a client-only
      // signOut leaves them intact so /login redirects straight back here.
      if (supabase) await supabase.auth.signOut({ scope: 'global' }).catch(() => {});
    } finally {
      // A full navigation rather than router.replace(), so the Set-Cookie
      // deletions are applied and no cached Server Component payload holding
      // customer data survives in the client router cache.
      window.location.href = '/auth/signout';
    }
  };

  const initials = (operator?.fullName || operator?.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');

  const sidebar = (
    <div className="flex h-full flex-col bg-surface border-r border-line themed">
      {/* Brand */}
      <div className="px-5 pt-5 pb-4">
        <Link href="/platform" className="flex items-center gap-2.5 w-max group">
          <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg group-hover:rotate-12 transition-transform duration-300">
            <div className="w-2 h-2 bg-white rounded-sm" />
          </div>
          <div className="leading-none">
            <div className="font-extrabold text-[15px] tracking-tight text-ink">VisionWorks</div>
            <div className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-accent mt-1">
              Platform
            </div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 min-h-0 overflow-y-auto px-3 space-y-1" aria-label="Console sections">
        {NAV.map((item) => (
          <NavLink
            key={item.href}
            item={item}
            active={isActive(item)}
            onNavigate={() => setOpen(false)}
          />
        ))}
      </nav>

      {/* The boundary, stated in the chrome rather than buried in a doc. An
          operator is reminded of it on every screen, and so is anyone being
          shown the console. */}
      <div className="mx-3 mb-3 rounded-lg border border-line bg-surface-alt px-3 py-2.5">
        <p className="text-[11px] leading-relaxed text-ink-faint">
          <span className="font-bold text-ink-muted">Metadata only.</span>{' '}
          Occupancy measurements are not accessible to platform operators.
        </p>
      </div>

      {/* Operator identity + controls */}
      <div className="border-t border-line px-3 py-3 space-y-2">
        <div className="flex items-center gap-2.5 px-1">
          <div className="w-8 h-8 rounded-lg bg-accent-soft text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-bold text-ink truncate">
              {operator?.fullName || 'Operator'}
            </div>
            <div className="text-[11px] text-ink-faint truncate">{operator?.email}</div>
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
      {/* Desktop sidebar */}
      <aside className="hidden lg:block fixed inset-y-0 left-0 w-[248px] z-30">
        {sidebar}
      </aside>

      {/* Mobile slide-over */}
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

      {/* Content column */}
      <div className="lg:pl-[248px]">
        {/* Mobile bar */}
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
            Platform
          </span>
        </div>

        <main className="px-5 sm:px-7 lg:px-9 py-6 lg:py-8 max-w-[1400px]">
          {children}
        </main>
      </div>
    </div>
  );
}
