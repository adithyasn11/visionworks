// frontend/app/platform/components/StatTile.jsx
//
// A single metric. Server component — no interactivity, so no client bundle.
//
// Deliberately restrained: one number, one label, optional secondary line. The
// temptation in admin dashboards is to add sparklines and percentage deltas to
// every tile; with two organisations that would be noise dressed as insight.

import React from 'react';

/**
 * @param tone 'default' | 'warn' | 'danger'  — warn/danger only when the value
 *   is actually non-zero, so a healthy platform shows no colour at all and
 *   colour therefore means something.
 */
export default function StatTile({
  label, value, hint, icon: Icon, tone = 'default', mono = false,
}) {
  const active = tone !== 'default' && Number(value) > 0;

  return (
    <div
      className={`rounded-xl border bg-surface px-4 py-3.5 transition-colors duration-200 themed ${
        active ? 'border-[color:var(--accent)]' : 'border-line'
      }`}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint leading-tight">
          {label}
        </span>
        {Icon && (
          <Icon
            className={`w-[15px] h-[15px] shrink-0 ${active ? 'text-accent' : 'text-ink-faint'}`}
            strokeWidth={2.1}
          />
        )}
      </div>

      <div
        className={`text-[26px] leading-none font-black tracking-tight tabular-nums ${
          active ? 'text-accent' : 'text-ink'
        } ${mono ? 'font-mono' : ''}`}
      >
        {value}
      </div>

      {hint && (
        <div className="mt-1.5 text-[11.5px] text-ink-faint leading-snug">{hint}</div>
      )}
    </div>
  );
}
