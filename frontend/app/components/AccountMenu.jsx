'use client';

// frontend/app/components/AccountMenu.jsx
//
// The signed-in identity control in the header: avatar, name, and a dropdown.
//
// WHAT IT RENDERS ABOUT THE VIEWER, AND WHY THAT IS SAFE HERE
//
// This shows an email address, which the public landing chrome deliberately
// never does (see the note in LandingNavbar.jsx). The difference is that every
// page mounting this component is already behind the auth guard — /home,
// /dashboard and /settings all redirect a signed-out visitor before rendering.
// The email shown is always the viewer's own, read server-side and passed in as
// a prop; this component never fetches, so it cannot be made to display someone
// else's by a client-side mistake.
//
// NO ORG-SCOPED LINKS. This menu renders only in AppHeader, which renders only
// on the pre-membership screens (/home, /home/checkout) — a member is
// redirected to /dashboard before either can appear. Every reader therefore has
// no organisation, so Dashboard / Members / Organisation settings would all be
// dead ends that bounce to /onboarding. Once inside the workspace the sidebar
// in DashboardShell carries those links instead.
//
// SIGN OUT IS A FORM POST, NOT A LINK OR A FETCH
//
// The session lives in httpOnly cookies that only the server can delete, so the
// destructive action goes to /auth/signout as a real POST. GET would make sign
// out reachable by prefetch, by a crawler, or by an <img> tag on another site —
// a one-click logout CSRF. It is a nuisance rather than a breach, but a POST
// costs nothing and closes it.

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ChevronDown, LogOut, CreditCard } from 'lucide-react';

/** Initials for the avatar. Falls back through name -> email -> a neutral dot. */
function initials(name, email) {
  const source = (name ?? '').trim() || (email ?? '').split('@')[0] || '';
  if (!source) return '·';
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return '·';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function AccountMenu({ email, fullName }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);

  // Close on an outside click or Escape. `mousedown` rather than `click`, so
  // the menu is gone before the click lands on whatever is underneath —
  // otherwise the first click outside is spent only on dismissing.
  useEffect(() => {
    if (!open) return undefined;

    const onPointer = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setOpen(false);
        // Return focus to the trigger, or the user is left with focus on
        // nothing and has to tab from the top of the document.
        buttonRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const label = (fullName ?? '').trim() || (email ?? '').split('@')[0] || 'Account';

  return (
    <div className="relative" ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((s) => !s)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-xl border-2 border-line hover:border-[color:var(--accent)] bg-ground pl-1.5 pr-2 py-1.5 transition-all duration-300 group"
      >
        <span
          aria-hidden="true"
          className="w-7 h-7 rounded-lg bg-accent text-white flex items-center justify-center text-[11px] font-black shrink-0 shadow-sm shadow-red-600/30 group-hover:shadow-red-600/50 group-hover:scale-110 transition-all duration-300"
        >
          {initials(fullName, email)}
        </span>
        <span className="hidden sm:block max-w-[10rem] truncate text-[13px] font-bold text-ink text-left">
          {label}
        </span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-ink-faint shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 mt-2.5 w-64 rounded-2xl border border-line bg-surface shadow-xl shadow-black/10 overflow-hidden z-50 animate-menu-in"
        >
          {/* Identity block. `break-all` because an email has no spaces to wrap
              at and a long one would otherwise widen the whole menu. */}
          <div className="px-4 py-3.5 border-b border-line bg-surface-alt">
            <p className="text-[13px] font-black text-ink truncate">{label}</p>
            <p className="text-[12px] text-ink-faint font-medium break-all">{email ?? '—'}</p>
          </div>

          <div className="py-1.5">
            <MenuLink href="/home#pricing" icon={CreditCard} onNavigate={() => setOpen(false)}>
              Plans
            </MenuLink>
          </div>

          <form action="/auth/signout" method="post" className="border-t border-line">
            <button
              type="submit"
              role="menuitem"
              className="w-full flex items-center gap-2.5 px-4 py-3 text-[13px] font-bold text-ink-muted hover:text-accent hover:bg-surface-alt hover:pl-5 transition-all duration-300 text-left"
            >
              <LogOut className="w-4 h-4 shrink-0" aria-hidden="true" />
              Sign out
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

function MenuLink({ href, icon: Icon, children, onNavigate }) {
  return (
    <Link
      href={href}
      role="menuitem"
      onClick={onNavigate}
      className="flex items-center gap-2.5 px-4 py-2.5 text-[13px] font-bold text-ink-muted hover:text-accent hover:bg-surface-alt hover:pl-5 transition-all duration-300"
    >
      <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
      {children}
    </Link>
  );
}
