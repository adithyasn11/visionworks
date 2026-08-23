// Loading state for the members screen.
//
// The page is force-dynamic and does three reads (session, profile, org name),
// so this is shown on a real navigation. It mirrors the screen's layout — back
// link, header, invite card, roster rows — so the skeleton settles into the
// page rather than being replaced by something differently shaped.
import React from 'react';

const Block = ({ className }) => (
  <div className={`animate-pulse rounded bg-[color:var(--surface-alt)] ${className}`} aria-hidden="true" />
);

export default function Loading() {
  return (
    <div
      className="themed min-h-screen bg-ground"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="sr-only">Loading members…</span>
      <div className="mx-auto max-w-3xl px-[clamp(1.25rem,4vw,2rem)] py-[clamp(1.25rem,3vh,2.5rem)]">
        <div className="flex items-center justify-between gap-4 mb-8">
          <Block className="h-4 w-36" />
          <Block className="h-8 w-8 rounded-lg" />
        </div>

        <div className="space-y-3 mb-7">
          <Block className="h-3 w-28" />
          <Block className="h-8 w-40" />
          <Block className="h-4 w-full max-w-xl" />
        </div>

        <div className="rounded-xl border border-line bg-surface p-5 mb-7 space-y-4">
          <Block className="h-4 w-32" />
          <Block className="h-11 w-full rounded-lg" />
          {Array.from({ length: 3 }).map((_, i) => (
            <Block key={i} className="h-14 w-full rounded-lg" />
          ))}
        </div>

        <div className="rounded-xl border border-line bg-surface overflow-hidden">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3.5 border-b border-line last:border-b-0">
              <Block className="w-9 h-9 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Block className="h-3.5 w-40" />
                <Block className="h-3 w-56" />
              </div>
              <Block className="h-6 w-16 rounded-full" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
