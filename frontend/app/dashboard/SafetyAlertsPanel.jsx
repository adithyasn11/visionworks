'use client';

// frontend/app/dashboard/SafetyAlertsPanel.jsx
//
// Open alerts on the overview: potential violations still waiting for a person. Each row is
// a decision (the single source of truth); the panel never decides anything itself. An alert
// leaves this list when it is reviewed. The wording stays non-accusing: "potential", "not seen".

import React from 'react';
import { Skeleton } from './safety/common';
import { ShieldAlert, ShieldCheck, Loader2, AlertCircle } from 'lucide-react';
import { severityMeta } from '../lib/alerts';
import { workerLabel } from '../lib/zoneEvents';
import EvidencePicture from '../components/EvidencePicture';

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

export default function SafetyAlertsPanel({ status, count, alerts, error, onReview }) {
  const open = count > 0;
  return (
    <section
      className={`glass-panel px-4 sm:px-5 py-4 themed ${open ? 'border-[color:var(--accent)]' : ''}`}
      aria-label="Open safety alerts"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 mb-3">
        <div className="flex items-center gap-2">
          {open
            ? <ShieldAlert className="w-4 h-4 text-accent" strokeWidth={2.2} />
            : <ShieldCheck className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />}
          <h3 className="text-[0.84375rem] font-bold tracking-tight text-ink">Open alerts</h3>
          {(status === 'ready' || (status === 'error' && count > 0)) && (
            <span
              className={`px-2 py-0.5 rounded-md text-[0.6875rem] font-bold tabular-nums ${open ? 'bg-accent text-white' : 'bg-surface-alt text-ink-muted'}`}
              aria-label={`${count} open`}
            >
              {count}
            </span>
          )}
        </div>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-ink-faint">
          potential violations waiting for review
        </span>
      </header>

      {status === 'loading' && (
        <Skeleton rows={3} label="Loading alerts" />
      )}

      {status === 'error' && (
        <p role="alert" className="flex items-start gap-2 text-xs text-ink-muted">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 text-accent shrink-0" /> Could not load alerts. {error}
          {count > 0 ? ` The count above is the last one received (${count}).` : ''}
        </p>
      )}

      {status === 'ready' && !open && (
        <p className="text-xs text-ink-muted leading-relaxed">
          No open alerts. New flags appear here within a minute.
        </p>
      )}

      {status === 'ready' && open && (
        <ul className="flex flex-col divide-y divide-line-soft max-h-[30rem] overflow-y-auto pr-1" aria-label="Open alerts, newest first">
          {alerts.map((a) => {
            const meta = severityMeta(a.severity);
            return (
              <li key={a.id} className="flex flex-col gap-1 py-2.5 text-xs">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className={`px-2 py-0.5 rounded-md border text-[0.625rem] font-bold uppercase tracking-wide ${BADGE[meta.tone]}`}>
                    {meta.label}
                  </span>
                  <span className="text-ink font-medium">{workerLabel(a.workerKey)}</span>
                  <span className="text-ink-muted">
                    {[a.camera, a.zone].filter(Boolean).join(' · ') || 'Unknown location'}
                  </span>
                  <span className="text-ink-faint font-mono tabular-nums">{when(a.observedAt)}</span>
                  {Number(a.confidence) > 0 && <span className="text-ink-faint">{Math.round(a.confidence * 100)}% sure</span>}
                </div>
                <p className="text-ink-muted leading-relaxed">{a.reason}</p>
                {a.hasPicture && <div className="flex flex-col"><EvidencePicture decisionId={a.id} /></div>}
              </li>
            );
          })}
        </ul>
      )}
      {status === 'ready' && open && (
        <p className="mt-2 text-[0.6875rem] text-ink-faint">
          They stay listed until someone reviews them.{onReview ? ' ' : ''}
          {onReview && <button type="button" onClick={onReview} className="underline text-ink hover:text-accent focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)] rounded">Open the Review queue</button>}
        </p>
      )}
      {status === 'ready' && count > alerts.length && (
        <p className="mt-2 text-[0.6875rem] text-ink-faint">Showing the newest {alerts.length} of {count}.</p>
      )}
    </section>
  );
}
