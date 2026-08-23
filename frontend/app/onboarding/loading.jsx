// Loading state for onboarding.
//
// The page is force-dynamic and does two database reads (session, then
// profile), so this is shown on a real navigation rather than a hypothetical
// one. It mirrors the wizard's layout — step rail, heading, three fields — so
// the skeleton settles into the form instead of being replaced by it.
//
// Themed surfaces, not the dashboard's dark blocks: onboarding follows the
// auth screens' light/dark theming, not the control-room shell.
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
      <span className="sr-only">Loading setup…</span>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] min-h-screen">
        {/* Left panel is permanently dark in both themes, like AuthAside. */}
        <div className="hidden lg:block card-dark" />

        <main className="flex flex-col px-[clamp(1.25rem,4vw,2.5rem)] py-[clamp(1rem,2.2vh,2rem)]">
          <div className="flex items-center justify-between gap-4">
            <Block className="h-8 w-64 rounded-full" />
            <Block className="h-8 w-8 rounded-lg" />
          </div>

          <div className="flex-1 flex items-center justify-center py-8">
            <div className="w-full max-w-sm space-y-6">
              <div className="space-y-3">
                <Block className="h-8 w-52" />
                <Block className="h-4 w-full" />
                <Block className="h-4 w-3/4" />
              </div>

              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="space-y-2">
                  <Block className="h-3.5 w-32" />
                  <Block className="h-11 w-full rounded-lg" />
                </div>
              ))}

              <Block className="h-11 w-full rounded-lg" />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
