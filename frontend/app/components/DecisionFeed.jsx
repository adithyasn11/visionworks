'use client';

// frontend/app/components/DecisionFeed.jsx
//
// What the safety rules concluded, newest first: each entry into a restricted zone
// is judged COMPLIANT, a POTENTIAL VIOLATION (for a person to review) or CANNOT
// VERIFY, with the sentence that explains why. Appears only when the backend is
// running the safety layer.

import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { tally } from '../lib/decisions';
import { workerLabel } from '../lib/zoneEvents';
import { ruleTypeMeta } from '../lib/safety/ruleTypes';

function clock(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return '00:00';
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
}

const BADGE = {
  COMPLIANT: { label: 'OK', cls: 'border-line text-ink bg-surface-alt' },
  POTENTIAL_VIOLATION: { label: 'Flagged', cls: 'border-[color:var(--accent)] text-white bg-accent' },
  UNVERIFIABLE: { label: 'Unsure', cls: 'border-line text-ink-muted bg-transparent' },
};

export default function DecisionFeed({ decisions, zones = [] }) {
  const byId = new Map((zones || []).map((z) => [String(z.zone_id), z]));
  const counts = tally(decisions);

  return (
    <section className="glass-card p-3 flex flex-col gap-2" aria-label="Safety decisions">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-ink text-sm font-semibold">
          <ShieldCheck className="w-4 h-4 text-accent" />
          <h3 className="font-semibold">Safety decisions</h3>
        </div>
        <span className="text-[0.75rem] text-ink-faint tabular-nums">{counts.POTENTIAL_VIOLATION} flagged</span>
      </header>

      {decisions.length === 0 ? (
        <p className="text-[0.8125rem] text-ink-muted">Nothing yet.</p>
      ) : (
        <ul
          role="log"
          aria-label="Decisions, newest first"
          tabIndex={0}
          className="flex flex-col divide-y divide-line-soft max-h-[34rem] overflow-y-auto pr-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)] rounded"
        >
          {decisions.map((d) => {
            const badge = BADGE[d.status] || BADGE.UNVERIFIABLE;
            const zone = byId.get(String(d.zone_id));
            const where = (zone && (zone.zone_name || zone.zone_id)) || (d.zone_id && d.zone_id !== 'TRANSIT_ZONE' ? d.zone_id : null);
            const what = (ruleTypeMeta(d.rule_type) || {}).label || 'Check';
            return (
              <li key={d.id} title={d.reason} className="flex flex-col gap-0.5 py-2 text-[0.8125rem]">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[0.75rem] text-ink-faint tabular-nums">{clock(d.video_time_s)}</span>
                  <span className={`px-1.5 py-0.5 rounded-md border text-[0.6875rem] font-bold ${badge.cls}`}>{badge.label}</span>
                  <span className="text-ink font-medium truncate">{what}</span>
                </div>
                <p className="text-[0.75rem] text-ink-faint truncate">{[workerLabel(d.worker_key), where].filter(Boolean).join(' · ')}</p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
