// frontend/app/loading.jsx
//
// Root-level loading state. Covers any route without a closer loading.jsx —
// today that is the marketing pages and /dashboard.
//
// Deliberately minimal: these routes are mostly static and appear instantly, so
// this exists to prevent a blank frame on a slow connection rather than to
// mimic a specific layout. It paints the themed background so the flash is the
// right colour in both light and dark.
import React from 'react';

export default function Loading() {
  return (
    <div
      className="min-h-screen bg-ground flex items-center justify-center themed"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">Loading…</span>
      <div className="flex flex-col items-center gap-4">
        {/* The brand mark, pulsing. Recognisable at a glance and needs no
            additional asset. */}
        <div className="w-9 h-9 bg-accent rounded-xl flex items-center justify-center animate-pulse">
          <div className="w-2.5 h-2.5 bg-white rounded-sm" />
        </div>
        <div className="h-1 w-28 rounded-full bg-surface-alt overflow-hidden">
          <div className="h-full w-1/3 rounded-full bg-[color:var(--accent)] animate-loading-sweep" />
        </div>
      </div>
    </div>
  );
}
