'use client';

// frontend/app/components/LandingFooter.jsx
//
// Shared footer: the public landing page, /home, and /home/checkout.
//
// `card-dark`, not `bg-inverse` — the footer is permanently dark in BOTH
// themes. bg-inverse flips with the theme, which turned the footer white in
// dark mode. The 8px accent top border is the page's full-width sign-off.
//
// WHY THE LINK LIST IS SHORT
//
// It used to carry eight `href="#"` placeholders across "Company" and "Legal".
// A dead link is worse than a missing one: it looks finished, invites a click,
// and does nothing — and in a demo that is the click someone will make. Every
// entry here now goes somewhere real. When About/Careers/Privacy exist, they
// get added back; until then their absence is honest.
//
// Because this renders on signed-in pages too, no link may assume the reader
// has an organisation — /dashboard would bounce anyone without one straight to
// /onboarding. /home is correct for every reader: signed out it passes through
// the login gate, signed in it is their actual home.

import React from 'react';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';

const PRODUCT = [
  { href: '/features', label: 'Features' },
  { href: '/security', label: 'Security' },
  { href: '/home#pricing', label: 'Plans' },
  { href: '/home', label: 'Your workspace' },
];

export default function LandingFooter() {
  return (
    <footer className="card-dark pt-16 pb-10 border-t-8 border-accent mt-auto w-full transition-colors duration-700">
      <div className="max-w-6xl mx-auto px-6 sm:px-8">

        <div className="grid grid-cols-1 lg:grid-cols-[1.6fr_1fr_1.4fr] gap-10 lg:gap-12 mb-12">

          {/* Brand */}
          <div>
            <Link href="/" className="flex items-center gap-3 mb-6 cursor-pointer group/logo w-max">
              <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg shadow-sm shadow-red-600/30 group-hover/logo:rotate-12 group-hover/logo:shadow-red-600/50 transition-all duration-300">
                <div className="w-2 h-2 bg-white rounded-sm" />
              </div>
              <span className="font-extrabold text-xl tracking-tight group-hover/logo:text-red-500 transition-colors">
                VisionWorks
              </span>
            </Link>
            <p className="opacity-70 font-medium text-[14px] leading-relaxed max-w-sm mb-6">
              Workplace activity analytics that measures how a space is used — occupancy,
              posture and dwell time per zone — without keeping the footage or identifying
              anyone in it.
            </p>
            <div className="flex items-center gap-3">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)] animate-pulse" aria-hidden="true" />
              <span className="opacity-70 font-bold text-[10px] tracking-widest uppercase">
                All systems operational
              </span>
            </div>
          </div>

          {/* Product links */}
          <nav aria-label="Product">
            <h4 className="font-black mb-5 uppercase tracking-[0.14em] text-[10px] text-red-500">
              Product
            </h4>
            <ul className="space-y-3.5">
              {PRODUCT.map(({ href, label }) => (
                <li key={href}>
                  <Link
                    href={href}
                    className="opacity-70 text-[13.5px] font-bold hover:opacity-100 hover:text-red-500 hover:translate-x-1 inline-block transition-all duration-300"
                  >
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          {/* Closing CTA — the footer's own reason to exist on a marketing
              page, rather than four columns of links nobody reads. */}
          <div className="rounded-3xl border border-white/10 bg-white/5 p-7 flex flex-col justify-between gap-5 group/cta hover:border-red-500/40 hover:bg-white/[0.07] transition-all duration-500">
            <div>
              <h4 className="text-xl font-black tracking-tight mb-2 group-hover/cta:text-red-500 transition-colors">
                Measure your space
              </h4>
              <p className="opacity-65 font-medium text-[13.5px] leading-relaxed">
                Point a camera at one room and see whether the numbers match what you
                already believe.
              </p>
            </div>
            <Link
              href="/home"
              className="bg-accent text-white px-5 py-3 rounded-2xl font-bold text-[13.5px] hover:brightness-110 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-red-600/40 transition-all duration-300 flex items-center justify-center gap-2.5 w-max group/btn"
            >
              Get started
              <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" aria-hidden="true" />
            </Link>
          </div>
        </div>

        <div className="pt-6 border-t border-white/10 flex flex-col sm:flex-row justify-between items-center gap-4">
          <p className="opacity-55 font-bold text-[12px]">
            &copy; {new Date().getFullYear()} VisionWorks Analytics.
          </p>
          <p className="opacity-45 font-bold text-[11px] tracking-wide text-center sm:text-right">
            Workplace analytics · privacy by design
          </p>
        </div>
      </div>
    </footer>
  );
}
