'use client';

// frontend/app/dashboard/SafetyAlertsPanel.jsx
//
// Open alerts on the overview: potential violations still waiting for a person. Each row is
// a decision (the single source of truth); the panel never decides anything itself. An alert
// leaves this list when it is reviewed. The wording stays non-accusing: "potential", "not seen".

import React from 'react';
import { Skeleton } from './safety/common';
import { ShieldAlert, ShieldCheck, AlertCircle, ArrowRight, Image as ImageIcon } from 'lucide-react';
import { severityMeta } from '../lib/alerts';
import { workerLabel } from '../lib/zoneEvents';
import DecisionModal from './safety/DecisionModal';

const BADGE = {
  critical: 'bg-accent text-white border-[color:var(--accent)]',
  warning: 'border-[color:var(--accent)] text-accent bg-transparent',
  info: 'border-line text-ink-muted bg-transparent',
};

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function SafetyAlertsPanel({ status, count, alerts, error, onReview, role, onChanged }) {
  const open = count > 0;
  const [shown, setShown] = React.useState(null);
  // The rows in the shape the review window reads (the same flags the Review queue lists).
  const items = alerts.map((a) => ({ id: a.id, severity: a.severity, status: 'POTENTIAL_VIOLATION', reason: a.reason, confidence: a.confidence,
    observedAt: a.observedAt, workerKey: a.workerKey, camera: a.camera ? { name: a.camera } : null, zone: a.zone ? { name: a.zone } : null }));
  return (
    <section
      className={`glass-panel px-5 sm:px-6 py-5 themed ${open ? 'border-[color:var(--accent)]' : ''}`}
      aria-label="Open safety alerts"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 mb-4">
        <div className="flex items-center gap-2">
          {open
            ? <ShieldAlert className="w-4 h-4 text-accent" strokeWidth={2.2} />
            : <ShieldCheck className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />}
          <h3 className="text-[0.9375rem] font-bold tracking-tight text-ink">Open alerts</h3>
          {(status === 'ready' || (status === 'error' && count > 0)) && (
            <span
              className={`px-2 py-0.5 rounded-md text-xs font-bold tabular-nums ${open ? 'bg-accent text-white' : 'bg-surface-alt text-ink-muted'}`}
              aria-label={`${count} open`}
            >
              {count}
            </span>
          )}
        </div>
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-ink-faint">
          potential violations waiting for review
        </span>
      </header>

      {status === 'loading' && (
        <Skeleton rows={3} label="Loading alerts" />
      )}

      {status === 'error' && (
        <p role="alert" className="flex items-start gap-2 text-[0.8125rem] text-ink-muted">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 text-accent shrink-0" /> Could not load alerts. {error}
          {count > 0 ? ` The count above is the last one received (${count}).` : ''}
        </p>
      )}

      {status === 'ready' && !open && (
        <p className="text-[0.8125rem] text-ink-muted leading-relaxed">
          No open alerts. New flags appear here within a minute.
        </p>
      )}

      {status === 'ready' && open && (
        <ul className="flex flex-col divide-y divide-line-soft max-h-[30rem] overflow-y-auto pr-1" aria-label="Open alerts, newest first">
          {alerts.map((a) => {
            const meta = severityMeta(a.severity);
            return (
              <li key={a.id} className="first:pt-0">
                <button type="button" onClick={() => setShown(alerts.indexOf(a))}
                  className="w-full text-left flex flex-col gap-1.5 py-3 text-[0.8125rem] rounded-lg hover:bg-surface-alt/60 px-2 -mx-2 transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className={`px-2 py-0.5 rounded-md border text-[0.6875rem] font-bold uppercase tracking-wide ${BADGE[meta.tone]}`}>
                    {meta.label}
                  </span>
                  <span className="text-ink font-medium">{workerLabel(a.workerKey)}</span>
                  <span className="text-ink-muted">
                    {[a.camera, a.zone].filter(Boolean).join(' · ') || 'Unknown location'}
                  </span>
                  <span className="text-ink-faint font-mono tabular-nums">{when(a.observedAt)}</span>
                  {Number(a.confidence) > 0 && <span className="text-ink-faint">{Math.round(a.confidence * 100)}% sure</span>}
                </div>
                <p className="text-ink leading-relaxed line-clamp-2">{a.reason}</p>
                <span className="flex items-center justify-between text-ink-faint">
                  <span className="inline-flex items-center gap-1.5">{a.hasPicture && <><ImageIcon className="w-3.5 h-3.5" aria-hidden="true" /> Picture</>}</span>
                  <span className="inline-flex items-center gap-1 font-bold text-accent">Open <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" /></span>
                </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {status === 'ready' && open && (
        <p className="mt-3 pt-3 border-t border-line-soft text-xs text-ink-faint">
          They stay listed until someone reviews them.{onReview ? ' ' : ''}
          {onReview && <button type="button" onClick={onReview} className="underline text-ink hover:text-accent focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)] rounded">Open the Review queue</button>}
        </p>
      )}
      {shown !== null && items[shown] && (
        <DecisionModal items={items} index={shown} onIndex={setShown} onClose={() => setShown(null)} role={role}
          onChanged={(id, updated) => { if (updated && updated.reviewState && updated.reviewState !== 'PENDING' && onChanged) { setShown(null); onChanged(); } }} />
      )}
      {status === 'ready' && count > alerts.length && (
        <p className="mt-2 text-xs text-ink-faint">Showing the newest {alerts.length} of {count}.</p>
      )}
    </section>
  );
}
