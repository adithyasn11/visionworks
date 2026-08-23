'use client';

// frontend/app/dashboard/RangePicker.jsx
//
// The date-range control for the historical panels.
//
// WHY RANGES ARE DISABLED RATHER THAN HIDDEN
//
// An organisation whose first bucket is yesterday cannot fill a 90-day chart.
// Offering the option anyway produces an empty graph and a support question
// ("the dashboard is broken"), so a range the data cannot cover is shown but
// disabled, with the reason in its tooltip. Hiding it instead would be worse:
// the reader would not know the option exists, or why their history is short.
//
// The visual language is the dashboard's own — the same pill treatment as the
// sidebar's active marker and the members screen's role chips, so this reads as
// part of the same application rather than a control bolted on.

import React from 'react';
import { CalendarRange } from 'lucide-react';

export const RANGES = [
  { days: 1,   label: '24h',  full: 'Last 24 hours' },
  { days: 7,   label: '7d',   full: 'Last 7 days' },
  { days: 30,  label: '30d',  full: 'Last 30 days' },
  { days: 90,  label: '90d',  full: 'Last 90 days' },
  { days: 365, label: '1y',   full: 'Last year' },
];

/** Whole days between the first bucket and now, or null when there is none. */
export function coverageDays(coverage) {
  if (!coverage?.first) return null;
  const first = new Date(coverage.first).getTime();
  if (Number.isNaN(first)) return null;
  return Math.max(1, Math.ceil((Date.now() - first) / 86400_000));
}

export default function RangePicker({ value, onChange, coverage, busy = false }) {
  const available = coverageDays(coverage);

  return (
    <div className="flex items-center gap-2">
      <CalendarRange className="w-3.5 h-3.5 text-ink-faint shrink-0" aria-hidden="true" />
      <div
        role="group"
        aria-label="Date range"
        className="flex items-center gap-1 rounded-lg border border-line bg-surface p-1"
      >
        {RANGES.map((range) => {
          const active = value === range.days;
          // A range longer than the data can fill is offered but disabled —
          // except the shortest, which stays clickable so there is always a
          // usable option even before the first bucket lands.
          const unsupported =
            available !== null && range.days > available && range.days !== RANGES[0].days;

          return (
            <button
              key={range.days}
              type="button"
              onClick={() => !unsupported && onChange(range.days)}
              disabled={busy || unsupported}
              aria-pressed={active}
              title={
                unsupported
                  ? `Only ${available} day${available === 1 ? '' : 's'} of data recorded so far`
                  : range.full
              }
              className={`px-2.5 py-1 rounded-md text-[12px] font-bold transition-colors duration-150 ${
                active
                  ? 'bg-accent text-white'
                  : unsupported
                    ? 'text-ink-faint cursor-not-allowed'
                    : 'text-ink-muted hover:text-ink hover:bg-surface-alt'
              } ${busy && !active ? 'opacity-60' : ''}`}
            >
              {range.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
