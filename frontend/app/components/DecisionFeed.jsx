'use client';

// frontend/app/components/DecisionFeed.jsx
//
// What the safety rules concluded, newest first: each entry into a restricted zone
// is judged COMPLIANT, a POTENTIAL VIOLATION (for a person to review) or CANNOT
// VERIFY, with the sentence that explains why. Appears only when the backend is
// running the safety layer.

import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { decisionStatus, confidenceLabel, tally } from '../lib/decisions';
import { workerLabel } from '../lib/zoneEvents';
import { zoneTypeMeta } from '../lib/zoneTypes';
import EvidencePicture from './EvidencePicture';

function clock(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return '00:00';
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
}

const BADGE = {
  ok: 'border-line text-ink bg-surface-alt',
  alert: 'border-[color:var(--accent)] text-white bg-accent',
  unknown: 'border-line text-ink-muted bg-transparent',
};

export default function DecisionFeed({ decisions, zones = [] }) {
  const byId = new Map((zones || []).map((z) => [String(z.zone_id), z]));
  const counts = tally(decisions);

  return (
    <section className="glass-card p-3 flex flex-col gap-2.5" aria-label="Safety decisions">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex items-center gap-2 text-ink text-sm font-semibold">
          <ShieldCheck className="w-4 h-4 text-accent" />
          <h3 className="font-semibold">Safety decisions</h3>
        </div>
        <span className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint">
          {counts.POTENTIAL_VIOLATION} to review · {counts.COMPLIANT} compliant · {counts.UNVERIFIABLE} cannot verify
        </span>
      </header>

      {decisions.length === 0 ? (
        <p className="text-xs text-ink-muted leading-relaxed">
          Nothing yet. Each time someone enters a Restricted zone, the safety rule for that zone is judged here.
          A flag is a potential violation for a person to review, never a final verdict.
        </p>
      ) : (
        <ul
          role="log"
          aria-label="Decisions, newest first"
          tabIndex={0}
          className="flex flex-col divide-y divide-line-soft max-h-[34rem] overflow-y-auto pr-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)] rounded"
        >
          {decisions.map((d) => {
            const meta = decisionStatus(d.status);
            const zone = byId.get(String(d.zone_id));
            const type = zoneTypeMeta(zone && zone.zone_type);
            const sure = confidenceLabel(d);
            return (
              <li key={d.id} className="flex flex-col gap-1 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="w-11 shrink-0 font-mono text-ink-faint tabular-nums">{clock(d.video_time_s)}</span>
                  <span className={`px-2 py-0.5 rounded-md border text-[0.625rem] font-bold uppercase tracking-wide ${BADGE[meta.tone]}`}>
                    {meta.label}
                  </span>
                  <span className="text-ink font-medium">{workerLabel(d.worker_key)}</span>
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-line px-1.5 py-0.5 text-[0.625rem] font-mono text-ink-muted max-w-[40%]">
                    <span aria-hidden="true" className="w-2 h-2 rounded-full shrink-0 border border-line" style={{ background: type.color }} />
                    <span className="truncate">{(zone && (zone.zone_name || zone.zone_id)) || d.zone_id || 'Zone'}</span>
                  </span>
                  {sure && <span className="text-ink-faint">{sure}</span>}
                </div>
                <p className={`pl-14 leading-relaxed ${d.status === 'UNVERIFIABLE' ? 'text-ink-faint' : 'text-ink-muted'}`}>{d.reason}</p>
                {/* snapshot is only present on decisions that wanted a picture: true = kept, false = not kept */}
                {d.snapshot === true && (
                  <div className="pl-14 flex flex-col">
                    <EvidencePicture decisionId={d.id} />
                  </div>
                )}
                {d.snapshot === false && (
                  <p className="pl-14 text-[0.6875rem] text-ink-faint">No evidence picture was kept for this decision.</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
