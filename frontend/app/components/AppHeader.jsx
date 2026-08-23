'use client';

// frontend/app/components/AppHeader.jsx
//
// The signed-in counterpart to LandingNavbar.
//
// It is deliberately the SAME header, not a similar one: identical geometry
// (max-w-6xl, h-16, px-6 sm:px-8), the same sticky/blur/border treatment, the
// same brand mark with its rotate-on-hover square, and the same nav link hover
// (`hover:text-accent hover:-translate-y-0.5`). One difference: the right side
// carries an AccountMenu instead of a "Log in" button.
//
// THERE IS NO DASHBOARD LINK, ANYWHERE IN THIS CHROME.
//
// This header only ever renders on /home and /home/checkout, and both of those
// are pre-membership screens — a member is redirected to /dashboard before
// either can render. So every reader of this header has no organisation, and a
// Dashboard link would be a guaranteed dead end: the dashboard guard would
// bounce them to /onboarding. The workspace is reached by finishing the flow,
// not by a link that skips it.
//
// THE 4rem HEIGHT IS LOAD-BEARING. `.hero-screen` in globals.css sizes the
// first screen as `calc(100svh - 4rem)`. A header of any other height leaves the
// next section peeking above the fold or pushes it off. Changing one means
// changing the other.

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ThemeToggle } from './ThemeToggle';
import AccountMenu from './AccountMenu';

const LINKS = [
  { href: '/home', label: 'Home' },
  { href: '/home#pricing', label: 'Plans' },
  { href: '/security', label: 'Security' },
];

export default function AppHeader({ email, fullName }) {
  const pathname = usePathname();
  const links = LINKS;

  return (
    <header className="themed sticky top-0 z-50 bg-ground/90 backdrop-blur-md border-b border-line">
      <div className="max-w-6xl mx-auto px-6 sm:px-8 h-16 flex items-center justify-between gap-4">

        <Link href="/home" className="flex items-center gap-2.5 group cursor-pointer shrink-0">
          <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover:shadow-red-600/50 group-hover:scale-110 transition-all duration-300">
            <div className="w-2 h-2 bg-white rounded-sm" />
          </div>
          <span className="font-extrabold text-lg tracking-tight text-ink group-hover:text-accent transition-colors duration-300">
            VisionWorks
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-8 font-bold text-[13px] text-ink-muted">
          {links.map(({ href, label }) => {
            // `#pricing` lives on /home, so compare paths only — otherwise the
            // Plans link could never match and Home would stay lit while the
            // reader is looking at the pricing section.
            const path = href.split('#')[0];
            const active = pathname === path && !href.includes('#');
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                className={`hover:text-accent hover:-translate-y-0.5 transition-all duration-300 ${
                  active ? 'text-accent' : ''
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2.5 shrink-0">
          <ThemeToggle />
          <AccountMenu email={email} fullName={fullName} />
        </div>
      </div>
    </header>
  );
}
