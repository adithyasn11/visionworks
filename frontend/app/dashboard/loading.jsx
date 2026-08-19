// Loading state for the customer dashboard.
//
// The dashboard is a dark control room in both themes (see .dashboard-shell in
// globals.css), so its skeleton has to be dark too — the themed surface tokens
// used elsewhere would flash light before the real page paints over them.
import React from 'react';

const Block = ({ className }) => (
  <div className={`animate-pulse rounded bg-white/[0.06] ${className}`} aria-hidden="true" />
);

export default function Loading() {
  return (
    <div
      className="dashboard-shell px-5 sm:px-7 py-6"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">Loading dashboard…</span>
      <div className="max-w-[1400px] space-y-5">
        <div className="flex items-end justify-between gap-4">
          <div>
            <Block className="h-3 w-24 mb-3" />
            <Block className="h-8 w-64" />
          </div>
          <Block className="h-10 w-36 rounded-lg" />
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-2xl border border-[#2A272E] bg-[#141316] p-5">
              <Block className="h-2.5 w-16 mb-3" />
              <Block className="h-7 w-14" />
            </div>
          ))}
        </div>

        <div className="grid lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4">
          <div className="rounded-2xl border border-[#2A272E] bg-[#141316] p-5">
            <Block className="h-[320px] w-full rounded-xl" />
          </div>
          <div className="rounded-2xl border border-[#2A272E] bg-[#141316] p-5 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Block key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
