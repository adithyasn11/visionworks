'use client';

// frontend/app/dashboard/safety/common.jsx
//
// The small pieces every safety screen shares, so a decision looks the same in the review queue, the
// events list and the detail panel: chips for the result, the review state and the severity, a time
// formatter, and the notice box. Wording stays non-accusing ("potential violation", "not seen").

import React from 'react';
import { AlertCircle, CheckCircle2, Info } from 'lucide-react';
import { decisionStatus } from '../../lib/decisions';
import { severityMeta } from '../../lib/alerts';

const CHIP = 'inline-flex items-center px-2 py-0.5 rounded-md border text-[10px] font-bold uppercase tracking-wide whitespace-nowrap';
const TONE = {
  ok: 'border-line text-ink bg-surface-alt',
  alert: 'border-[color:var(--accent)] text-white bg-accent',
  unknown: 'border-line text-ink-muted bg-transparent',
  outline: 'border-[color:var(--accent)] text-accent bg-transparent',
  quiet: 'border-line text-ink-faint bg-transparent',
};

export function StatusChip({ status }) {
  const meta = decisionStatus(status);
  return <span className={`${CHIP} ${TONE[meta.tone]}`}>{meta.label}</span>;
}

const REVIEW = {
  NOT_REQUIRED: { label: 'Not reviewed', tone: 'quiet' },
  PENDING: { label: 'Waiting for review', tone: 'outline' },
  CONFIRMED: { label: 'Confirmed', tone: 'alert' },
  DISMISSED: { label: 'Dismissed', tone: 'ok' },
};

export function ReviewChip({ state }) {
  const meta = REVIEW[state] || REVIEW.NOT_REQUIRED;
  return <span className={`${CHIP} ${TONE[meta.tone]}`}>{meta.label}</span>;
}

export function SeverityChip({ severity }) {
  const meta = severityMeta(severity);
  const tone = meta.tone === 'critical' ? 'alert' : meta.tone === 'warning' ? 'outline' : 'quiet';
  return <span className={`${CHIP} ${TONE[tone]}`}>{meta.label}</span>;
}

/** The reasons a reviewer can give, with the words shown (the codes are the ones the database accepts). */
export const REVIEW_REASONS = [
  { value: 'CORRECT', label: 'The flag was right' },
  { value: 'PPE_PRESENT', label: 'The equipment was worn (the system missed it)' },
  { value: 'WRONG_PERSON', label: 'It was about the wrong person' },
  { value: 'NOT_IN_ZONE', label: 'The person was not in the zone' },
  { value: 'BAD_PICTURE', label: 'The picture was too poor to judge' },
  { value: 'OTHER', label: 'Another reason (say which in the note)' },
];

export const reasonLabel = (code) => (REVIEW_REASONS.find((r) => r.value === code) || {}).label || code || '';

/** "9 Nov, 10:06" in the viewer's own time zone. */
export function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** "9 Nov 2026" for a date-only value (a due date), without the time-zone shift a Date would add. */
export function fmtDate(value) {
  if (!value) return '';
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return '';
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

export function confidenceText(decision) {
  if (!decision || decision.status === 'UNVERIFIABLE') return '';
  // A time or a route is measured, not estimated: a percentage would claim more than the tracker can promise.
  if (decision.ruleType === 'MAX_DWELL' || decision.ruleType === 'REQUIRED_EXIT') return '';
  const c = Number(decision.confidence);
  return Number.isFinite(c) && c > 0 ? `${Math.round(c * 100)}% sure` : '';
}

/** A short message box: tone 'error' | 'ok' | 'info'. `role="alert"` only for errors, so a success is not shouted. */
export function Notice({ tone = 'info', children, className = '' }) {
  const Icon = tone === 'error' ? AlertCircle : tone === 'ok' ? CheckCircle2 : Info;
  const box = tone === 'error' ? 'border-[color:var(--accent)] bg-accent-soft' : 'border-line bg-surface-alt';
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed text-ink-muted ${box} ${className}`}>
      <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${tone === 'error' ? 'text-accent' : 'text-ink-faint'}`} aria-hidden="true" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export const BTN = 'inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]';
export const BTN_PRIMARY = `${BTN} bg-accent text-white border-[color:var(--accent)] hover:brightness-110`;
export const BTN_QUIET = `${BTN} bg-surface-alt text-ink-muted border-line hover:text-ink hover:border-field`;
export const INPUT = 'rounded-lg bg-ground border border-line px-3 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)] transition-colors';

/** A page-section card with a heading. */
export function Panel({ title, icon: Icon, aside, children, className = '' }) {
  return (
    <section className={`glass-panel px-4 sm:px-5 py-4 themed ${className}`}>
      {(title || aside) && (
        <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 mb-3">
          <div className="flex items-center gap-2">
            {Icon && <Icon className="w-4 h-4 text-accent" strokeWidth={2.2} aria-hidden="true" />}
            <h3 className="text-[13.5px] font-bold tracking-tight text-ink">{title}</h3>
          </div>
          {aside && <div className="text-[10px] font-mono uppercase tracking-[0.14em] text-ink-faint">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
