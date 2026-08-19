'use client';

// frontend/app/components/RouteProgress.jsx
//
// A global top-of-screen progress bar for App Router navigations.
//
// WHY THIS EXISTS
//
// Next's per-route loading.jsx only appears once the *target* route starts
// rendering. Anything upstream of that — a force-dynamic layout re-running its
// own auth checks (see /platform/layout.jsx), or just network latency before
// the RSC response starts streaming — has no visual feedback at all. The tab
// looks frozen: the click registered, nothing moved, and there is no way to
// tell a slow page from a broken one.
//
// This bar starts the instant a same-origin link is clicked (capture-phase
// listener, so it fires before Next's own handler) and clears when the URL
// actually changes. That covers the gap loading.jsx cannot reach, on every
// route in the app, with no per-page wiring required.
//
// Deliberately not a real progress percentage — there is no way to know how
// far a navigation is from done, so it creeps and holds rather than claiming
// false precision, matching the sweep animation already used in loading.jsx.

import React, { useEffect, useRef, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

function isInternalNavClick(e) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
    return null;
  }
  const anchor = e.target instanceof Element ? e.target.closest('a') : null;
  if (!anchor || !anchor.href) return null;
  if (anchor.target && anchor.target !== '_self') return null;
  if (anchor.hasAttribute('download')) return null;

  let url;
  try {
    url = new URL(anchor.href, window.location.href);
  } catch {
    return null;
  }
  if (url.origin !== window.location.origin) return null;
  // Same-page hash link or identical URL: nothing will actually navigate.
  if (url.pathname === window.location.pathname && url.search === window.location.search) {
    return null;
  }
  return url;
}

export default function RouteProgress() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [active, setActive] = useState(false);
  const timeoutRef = useRef(null);
  const key = `${pathname}?${searchParams.toString()}`;
  const prevKeyRef = useRef(key);

  useEffect(() => {
    const start = () => {
      window.clearTimeout(timeoutRef.current);
      setActive(true);
    };
    const onClick = (e) => {
      if (isInternalNavClick(e)) start();
    };

    document.addEventListener('click', onClick, true);
    window.addEventListener('popstate', start);

    return () => {
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('popstate', start);
    };
  }, []);

  // The URL actually changed, or we're back to idle after a click that didn't
  // navigate anywhere (e.g. the target route errored before committing) — a
  // short trailing timeout clears the bar either way so it never gets stuck.
  useEffect(() => {
    if (prevKeyRef.current !== key) {
      prevKeyRef.current = key;
      setActive(false);
      window.clearTimeout(timeoutRef.current);
      return;
    }
    if (active) {
      timeoutRef.current = window.setTimeout(() => setActive(false), 8000);
      return () => window.clearTimeout(timeoutRef.current);
    }
  }, [key, active]);

  if (!active) return null;

  return (
    <div
      className="fixed inset-x-0 top-0 z-[100] h-[3px] overflow-hidden bg-transparent"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">Loading…</span>
      <div className="h-full w-1/3 rounded-r-full bg-[color:var(--accent)] animate-loading-sweep" />
    </div>
  );
}
