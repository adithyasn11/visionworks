// frontend/app/platform/components/Skeleton.jsx
//
// Loading placeholders for the console.
//
// WHY SKELETONS AND NOT A SPINNER
//
// Every /platform route is force-dynamic: each navigation runs real queries
// against Supabase, so there is a real wait. Without a loading.jsx, Next keeps
// the *previous* page on screen for that whole time — the sidebar highlight
// moves but the content does not, which reads as a broken click.
//
// A skeleton fixes that and does something a spinner cannot: it holds the
// layout. The header, tiles and table rows appear at the size they will
// actually be, so nothing jumps when the data lands.
//
// The shapes below deliberately mirror each page's real structure. A generic
// grey box everywhere would be less work but would flash a layout that is
// immediately replaced by a different one.

import React from 'react';

/**
 * One shimmering block.
 *
 * `animate-pulse` rather than a translating gradient: it is a single opacity
 * animation the compositor can run off the main thread, so it stays smooth
 * while React is hydrating — which is exactly when this component is on screen.
 */
export function Shimmer({ className = '' }) {
  return (
    <div
      className={`animate-pulse rounded bg-surface-alt ${className}`}
      aria-hidden="true"
    />
  );
}

/**
 * Wrapper that announces loading to assistive technology.
 *
 * The visual skeleton is hidden from the accessibility tree (each Shimmer is
 * aria-hidden) and replaced by one polite live-region message, so a screen
 * reader hears "Loading…" once instead of reading out forty empty boxes.
 */
export function SkeletonPage({ children }) {
  return (
    <div className="space-y-5" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading…</span>
      {children}
    </div>
  );
}

/** Page title block — matches the real header's rhythm. */
export function SkeletonHeader({ withAction = false }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="w-full max-w-xl">
        <Shimmer className="h-3 w-24 mb-3" />
        <Shimmer className="h-8 w-72 max-w-full mb-2.5" />
        <Shimmer className="h-3.5 w-full max-w-lg" />
      </div>
      {withAction && <Shimmer className="h-10 w-40 rounded-lg" />}
    </div>
  );
}

/** A row of stat tiles. */
export function SkeletonTiles({ count = 6, cols = 'grid-cols-2 md:grid-cols-3 xl:grid-cols-6' }) {
  return (
    <div className={`grid ${cols} gap-3`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl border border-line bg-surface px-4 py-3.5">
          <Shimmer className="h-2.5 w-16 mb-3" />
          <Shimmer className="h-7 w-12 mb-2" />
          <Shimmer className="h-2.5 w-20" />
        </div>
      ))}
    </div>
  );
}

/** A bordered card with a header strip and `rows` list items. */
export function SkeletonCard({ rows = 4, headerWidth = 'w-40', tall = false }) {
  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden">
      <div className="flex items-center justify-between px-4 sm:px-5 py-3.5 border-b border-line">
        <Shimmer className={`h-3.5 ${headerWidth}`} />
        <Shimmer className="h-3 w-12" />
      </div>
      {tall ? (
        <div className="p-4 sm:p-5">
          <Shimmer className="h-[180px] w-full rounded-lg" />
        </div>
      ) : (
        <div className="divide-y divide-[color:var(--line)]">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="px-4 sm:px-5 py-3.5 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                {/* Widths vary per row so the block reads as content rather
                    than as a repeating pattern. */}
                <Shimmer className={`h-3.5 mb-2 ${['w-48', 'w-40', 'w-56', 'w-44', 'w-52'][i % 5]}`} />
                <Shimmer className={`h-2.5 ${['w-32', 'w-28', 'w-36', 'w-24', 'w-20'][i % 5]}`} />
              </div>
              <Shimmer className="h-5 w-16 rounded" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** A table with a header row and `rows` body rows. */
export function SkeletonTable({ rows = 6, cols = 5 }) {
  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden">
      <div className="flex items-center gap-6 px-4 py-2.5 border-b border-line">
        {Array.from({ length: cols }).map((_, i) => (
          <Shimmer key={i} className={`h-2.5 ${i === 0 ? 'flex-1' : 'w-16'}`} />
        ))}
      </div>
      <div className="divide-y divide-[color:var(--line)]">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="flex items-center gap-6 px-4 py-3.5">
            <div className="flex-1 min-w-0">
              <Shimmer className={`h-3.5 mb-2 ${['w-44', 'w-52', 'w-36', 'w-48'][r % 4]}`} />
              <Shimmer className="h-2.5 w-28" />
            </div>
            {Array.from({ length: cols - 1 }).map((_, c) => (
              <Shimmer key={c} className="h-3.5 w-16" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The filter bar above a list. */
export function SkeletonFilters() {
  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row gap-3">
        <Shimmer className="h-10 flex-1 rounded-lg" />
        <Shimmer className="h-10 w-44 rounded-lg" />
      </div>
      <div className="flex flex-wrap gap-2">
        {/* Fixed Tailwind widths rather than an inline style: Shimmer takes only
            a className, and mixing the two silently drops the style prop. */}
        {['w-20', 'w-24', 'w-36', 'w-28'].map((w, i) => (
          <Shimmer key={i} className={`h-8 rounded-lg ${w}`} />
        ))}
      </div>
    </div>
  );
}
