'use client';

// frontend/app/dashboard/OverviewSection.jsx
//
// The manager's home screen: what happened in the space over the last 24 hours,
// answered in the order a manager actually asks it — how many people, how busy,
// where, and is anyone sitting too long.
//
// Restraint is the design here. The temptation on an analytics home screen is to
// show every number the schema can produce; this shows six, because a tile that
// nobody acts on is noise competing with the tiles that matter.
//
// Every value is measured. Nothing is estimated, projected or placeholder — when
// the pipeline has not run, the page says so plainly rather than showing zeros
// that look like a quiet day.

import React from 'react';
import {
  Users, Activity, MapPin, Armchair, Clock, Radio,
  AlertCircle, Inbox, Loader2,
} from 'lucide-react';

/**
 * One metric. Mirrors the founder console's StatTile so both surfaces read as
 * the same product: mono eyebrow, one big tabular number, one quiet hint.
 *
 * `tone="warn"` only colours the tile when the value actually crosses the
 * threshold, so colour on this page always means something.
 */
function StatTile({ label, value, hint, icon: Icon, tone = 'default', suffix }) {
  const active = tone === 'warn';

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
        }`}
      >
        {value}
        {suffix && (
          <span className="text-[14px] font-bold text-ink-faint ml-1">{suffix}</span>
        )}
      </div>

      {hint && <div className="mt-1.5 text-[11.5px] text-ink-faint leading-snug">{hint}</div>}
    </div>
  );
}

/**
 * Posture split as a single stacked bar with direct labels.
 *
 * A bar rather than a donut: this is one whole broken into three parts read as
 * proportions, and a bar compares segment lengths far more accurately than
 * angles. Each segment carries its own label because the dark palette's
 * red/amber pair sits in the CVD floor band — identity must never rest on
 * colour alone. Segments are separated by a 2px surface gap.
 */
function PostureBar({ sitting, standing, walking }) {
  const rows = [
    { name: 'Sitting',  pct: sitting,  varName: '--chart-1' },
    { name: 'Standing', pct: standing, varName: '--chart-2' },
    { name: 'Walking',  pct: walking,  varName: '--chart-3' },
  ].filter((r) => r.pct > 0);

  if (rows.length === 0) return null;

  return (
    <div className="glass-panel px-4 sm:px-5 py-4 themed">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Armchair className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
          <h3 className="text-[13.5px] font-bold tracking-tight text-ink">Posture mix</h3>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
          share of observations
        </span>
      </div>

      <div className="flex w-full h-3 rounded-full overflow-hidden gap-[2px]" role="img"
        aria-label={rows.map((r) => `${r.name} ${r.pct}%`).join(', ')}>
        {rows.map((r) => (
          <div
            key={r.name}
            style={{ width: `${r.pct}%`, backgroundColor: `var(${r.varName})` }}
            className="first:rounded-l-full last:rounded-r-full"
          />
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ backgroundColor: `var(${r.varName})` }}
            />
            <span className="text-[12.5px] font-semibold text-ink">{r.name}</span>
            <span className="text-[12.5px] tabular-nums text-ink-muted">{r.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="glass-panel px-6 py-14 text-center themed">
      <Inbox className="w-8 h-8 mx-auto text-ink-faint mb-3" strokeWidth={1.7} />
      <p className="text-[14px] font-bold text-ink">No activity recorded yet</p>
      <p className="text-[12.5px] text-ink-faint mt-1.5 max-w-md mx-auto leading-relaxed">
        Open <span className="font-semibold text-ink-muted">Live feed</span> and process a
        video or start the camera. Occupancy, posture and dwell time appear here as
        people are detected.
      </p>
    </div>
  );
}

export default function OverviewSection({ data, status, error, hours }) {
  if (status === 'loading') {
    return (
      <div className="glass-panel px-6 py-14 text-center themed">
        <Loader2 className="w-6 h-6 mx-auto text-accent animate-spin" />
        <p className="text-[12.5px] text-ink-muted mt-3">Loading activity…</p>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div
        role="alert"
        className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3.5"
      >
        <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
        <p className="text-[13px] leading-relaxed">
          <span className="font-bold text-accent">Could not load activity.</span>{' '}
          <span className="text-ink-muted">{error}</span>
        </p>
      </div>
    );
  }

  if (!data?.has_data) return <EmptyState />;

  // Long unbroken sitting is the one number on this page that is actionable
  // rather than descriptive, so it is the only tile allowed to turn red.
  const SEDENTARY_MINUTES = 45;
  const sedentary = data.longest_dwell_minutes >= SEDENTARY_MINUTES;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatTile
          label="People seen"
          value={data.people}
          hint={`over the last ${hours}h`}
          icon={Users}
        />
        <StatTile
          label="Activity index"
          value={data.avg_activity}
          suffix="/100"
          hint="movement & posture change"
          icon={Activity}
        />
        <StatTile
          label="Zones active"
          value={data.zones_active}
          hint={data.zones_active ? 'with recorded presence' : 'none yet'}
          icon={MapPin}
        />
        <StatTile
          label="Busiest zone"
          value={data.peak_zone?.people ?? '—'}
          hint={data.peak_zone ? data.peak_zone.zone : 'no zone activity'}
          icon={Users}
        />
        <StatTile
          label="Longest sitting"
          value={data.longest_dwell_minutes}
          suffix="min"
          hint={sedentary ? 'break recommended' : 'within healthy range'}
          icon={Clock}
          tone={sedentary ? 'warn' : 'default'}
        />
        <StatTile
          label="Last detection"
          value={data.last_seen ? data.last_seen.split(' ')[1]?.slice(0, 5) ?? '—' : '—'}
          hint={data.last_seen ? data.last_seen.split(' ')[0] : 'never'}
          icon={Radio}
        />
      </div>

      <PostureBar
        sitting={data.sitting_pct}
        standing={data.standing_pct}
        walking={data.walking_pct}
      />
    </div>
  );
}
