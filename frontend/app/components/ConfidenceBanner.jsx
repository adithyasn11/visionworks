'use client';

// frontend/app/components/ConfidenceBanner.jsx
//
// Step 14 of IDENTITY_TRACKING_PLAN.md: "UI shows a warning banner when
// bindingConfidence < 0.6".
//
// WHY A NUMBER NEEDS A BANNER
//
// A row saying "Prajwal — 6h 12m at desk" reads as a fact. It is not: it is a
// measurement with a confidence attached, and at 0.45 that measurement is
// mostly guesswork the system was not willing to throw away. Presenting both
// figures with the same weight would be the single most misleading thing this
// interface could do — somebody would act on it.
//
// So confidence is rendered next to the number it qualifies, and below 0.6 the
// row is visibly marked rather than quietly annotated. Migration 020 defines
// that threshold in the schema comment; this is the interface honouring it.
//
// The plan's §15 puts it plainly: "a number without its confidence is a claim
// you cannot defend in the viva."

import React from 'react';
import { AlertTriangle, CheckCircle2, HelpCircle } from 'lucide-react';

// Below this a figure is shown as low-confidence rather than as fact.
// Migration 020's `bindingConfidence` comment is the source of the number.
export const LOW_CONFIDENCE = 0.6;

// Below this the identity pipeline refuses to attribute at all, and the time
// lands in unknownMinutes instead. Mirrors IDENTITY_MIN_CONFIDENCE in
// cv/identity_tracker.py and db/employee_aggregator.py.
export const ABSTAIN_FLOOR = 0.5;

/**
 * A confidence, as a pill.
 *
 * Three states rather than a gradient, because a reader needs to know which
 * side of a decision a number falls on, not to interpolate. Green means the
 * figure can be relied on; amber means it should be read as an estimate; and
 * anything at or below the floor should never have reached the UI attributed
 * at all, so it is shown as an error rather than as a weak value.
 */
export function ConfidencePill({ value, className = '' }) {
  const v = Number(value ?? 0);
  const state = v >= LOW_CONFIDENCE ? 'good' : v > ABSTAIN_FLOOR ? 'weak' : 'bad';
  const tone = {
    good: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
    weak: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
    bad:  'border-[color:var(--accent)] bg-accent-soft text-accent',
  }[state];
  const Icon = { good: CheckCircle2, weak: AlertTriangle, bad: HelpCircle }[state];
  const title = {
    good: 'Confident enough to rely on',
    weak: `Below ${LOW_CONFIDENCE} — read this as an estimate, not a fact`,
    bad:  `At or below the ${ABSTAIN_FLOOR} floor — this should be unattributed`,
  }[state];

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold ${tone} ${className}`}
    >
      <Icon className="w-3 h-3" />
      {v.toFixed(2)}
    </span>
  );
}

/**
 * The warning banner.
 *
 * Renders nothing when the figures can be relied on — a banner that is always
 * present is a banner nobody reads.
 *
 * `unknownMinutes` is shown alongside, because the two facts belong together:
 * low confidence and unattributed time are the same phenomenon seen from two
 * directions, and a reader who sees only one of them will draw the wrong
 * conclusion about which is missing.
 */
export default function ConfidenceBanner({
  confidence,
  unknownMinutes = 0,
  presentMinutes = 0,
  subject = 'These figures',
}) {
  const v = Number(confidence ?? 0);
  const unknown = Number(unknownMinutes ?? 0);
  const present = Number(presentMinutes ?? 0);

  const low = v < LOW_CONFIDENCE;
  const material = unknown > 0 && (present <= 0 || unknown / (present + unknown) > 0.1);
  if (!low && !material) return null;

  const share = present + unknown > 0
    ? Math.round((unknown / (present + unknown)) * 100)
    : null;

  return (
    <div
      role="status"
      className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start gap-2.5"
    >
      <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
      <div className="min-w-0">
        {low && (
          <p className="text-[13px] font-bold text-ink leading-relaxed">
            {subject} are low-confidence ({v.toFixed(2)}).
          </p>
        )}
        <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed mt-0.5">
          {low && (
            <>
              The system was not consistently sure who it was watching, so read
              these as an estimate rather than a record.{' '}
            </>
          )}
          {unknown > 0 && (
            <>
              <span className="font-bold text-ink">
                {unknown} minute{unknown === 1 ? '' : 's'}
                {share !== null ? ` (${share}%)` : ''}
              </span>{' '}
              could not be attributed to anyone and are excluded from the totals
              below — deliberately, rather than being assigned to the nearest
              guess.
            </>
          )}
        </p>
      </div>
    </div>
  );
}
