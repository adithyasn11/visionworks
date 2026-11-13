// frontend/app/components/ShellSkeleton.jsx
//
// The loading screen for every page that lives inside the dashboard shell (the dashboard itself and the
// settings pages): the same sidebar, page header and panels as the real page, drawn with the shimmering
// .skeleton bars in the theme's own colours. Because it has the real page's shape, the page FILLS IN
// when it arrives instead of jumping (the old settings skeleton had no sidebar, so everything shifted).
import React from 'react';

const Bar = ({ className }) => <div className={`skeleton ${className}`} aria-hidden="true" />;

/**
 * `narrow`: the settings pages' centred column (max-w-3xl) instead of the dashboard's full width.
 * `panels`: how many placeholder cards to draw.
 */
export default function ShellSkeleton({ label = 'Loading', narrow = false, panels = 2 }) {
  return (
    <div className="min-h-screen bg-ground themed" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}…</span>

      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-[15.5rem] flex-col bg-surface border-r border-line">
        <div className="h-16 px-5 flex items-center gap-2.5 border-b border-line">
          <div className="w-8 h-8 rounded-lg bg-accent" aria-hidden="true" />
          <Bar className="h-3.5 w-24" />
        </div>
        <div className="px-6 pt-5 space-y-4">
          {Array.from({ length: 10 }).map((_, i) => <Bar key={i} className="h-3 w-32" />)}
        </div>
      </aside>

      <div className="lg:hidden h-14 border-b border-line bg-ground" aria-hidden="true" />

      <div className="lg:pl-[15.5rem]">
        <div className="px-5 sm:px-7 lg:px-9 py-6 lg:py-8 max-w-[1400px] mx-auto">
          <div className={`${narrow ? 'mx-auto max-w-3xl' : ''} space-y-6`}>
            <div className="pb-5 border-b border-line space-y-3">
              <Bar className="h-2.5 w-20" />
              <Bar className="h-8 w-64 max-w-full" />
              <Bar className="h-3 w-96 max-w-full" />
            </div>
            {Array.from({ length: panels }).map((_, i) => (
              <div key={i} className="glass-panel px-5 py-5 space-y-3">
                <Bar className="h-3.5 w-40" />
                <Bar className="h-3 w-11/12" />
                <Bar className="h-3 w-3/4" />
                <Bar className="h-3 w-2/3" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
