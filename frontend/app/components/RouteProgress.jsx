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
    // A link click: shown until the URL changes, with a long safety net so it can never get stuck.
    const start = () => {
      window.clearTimeout(timeoutRef.current);
      setActive(true);
      timeoutRef.current = window.setTimeout(() => setActive(false), 8000);
    };
    const onClick = (e) => {
      if (isInternalNavClick(e)) start();
    };

    // The dashboard switches screens without changing the URL (view state, so the camera socket survives),
    // so it announces the switch with this event: the same bar sweeps briefly, then clears on its own.
    const onViewChange = () => {
      window.clearTimeout(timeoutRef.current);
      setActive(true);
      timeoutRef.current = window.setTimeout(() => setActive(false), 550);
    };

    document.addEventListener('click', onClick, true);
    window.addEventListener('popstate', start);
    window.addEventListener('vw-view-change', onViewChange);

    return () => {
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('popstate', start);
      window.removeEventListener('vw-view-change', onViewChange);
    };
  }, []);

  // The URL actually changed: the navigation is done. (Each start sets its own timer above, so the bar
  // also clears after a click that never navigated, e.g. the target errored before committing.)
  useEffect(() => {
    if (prevKeyRef.current !== key) {
      prevKeyRef.current = key;
      setActive(false);
      window.clearTimeout(timeoutRef.current);
    }
  }, [key]);
  useEffect(() => () => window.clearTimeout(timeoutRef.current), []);

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
