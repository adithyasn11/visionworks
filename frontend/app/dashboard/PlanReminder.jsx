'use client';

// frontend/app/dashboard/PlanReminder.jsx
//
// The payment reminder. Shown above every dashboard screen when a paid plan is in its last 7 days, and (in red,
// not dismissable) once its term has ended, when the organisation is held to Starter limits until it renews
// (029_plan_term_enforcement.sql). Admins get the button that renews; everyone else is told who can.
//
// The upcoming reminder can be hidden for the rest of the browser session; the expired one cannot.

import React, { useEffect, useState } from 'react';
import { AlertTriangle, CalendarClock, X } from 'lucide-react';
import { planName, getPlan } from '../lib/plans';

export const REMIND_DAYS = 7;

const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
};

/** Whole days until `iso` (rounded up, so "ends in 1 day" holds until the moment it ends). Null when there is no date. */
export function daysLeft(iso) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  return Math.ceil(ms / 86400000);
}

/** 'expired' | 'due' | null: what the reminder should say for this usage. */
export function reminderState(usage) {
  if (!usage || usage.plan === 'FREE') return null;
  if (usage.expired) return 'expired';
  const left = daysLeft(usage.renewsAt);
  return left !== null && left <= REMIND_DAYS ? 'due' : null;
}

function limitsSentence(planId) {
  const l = getPlan(planId)?.limits;
  if (!l) return '';
  const n = (v, one, many) => (v == null ? `unlimited ${many}` : `${v} ${v === 1 ? one : many}`);
  return `${n(l.cameras, 'camera', 'cameras')}, ${n(l.sites, 'site', 'sites')}, ${n(l.seats, 'member', 'members')} and ${l.retentionDays} days of history`;
}

export default function PlanReminder({ usage, canRenew, onRenew, compact = false }) {
  const state = reminderState(usage);
  const key = usage?.renewsAt ? `vw-plan-reminder-hidden-${usage.renewsAt}` : null;
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    try { setHidden(Boolean(key && sessionStorage.getItem(key))); } catch { setHidden(false); }
  }, [key]);

  if (!state || (state === 'due' && hidden)) return null;

  const name = planName(usage.plan);
  const left = daysLeft(usage.renewsAt);
  const expired = state === 'expired';

  const hide = () => {
    try { if (key) sessionStorage.setItem(key, '1'); } catch { /* the banner simply comes back next time */ }
    setHidden(true);
  };

  return (
    <div
      role={expired ? 'alert' : 'status'}
      className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${
        expired
          ? 'border-[color:var(--accent)] bg-accent-soft'
          : 'border-amber-500/60 bg-amber-500/10'
      } ${compact ? '' : 'animate-view-in'}`}
    >
      <div className="flex items-start gap-3 min-w-0 max-w-3xl">
        {expired
          ? <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-accent" aria-hidden="true" />
          : <CalendarClock className="w-4 h-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />}
        <p className="text-[0.78125rem] text-ink-muted font-medium leading-relaxed">
          {expired ? (
            <>
              <strong className="text-ink">Your {name} plan ended on {fmtDate(usage.renewsAt)}.</strong>{' '}
              Until it is renewed you are on Starter limits ({limitsSentence('FREE')}), and scheduled reports are
              paused. Nothing over the limit has been removed, but nothing new can be added.
            </>
          ) : (
            <>
              <strong className="text-ink">
                Your {name} plan {left <= 0 ? 'ends today' : `ends in ${left} day${left === 1 ? '' : 's'}`}, on {fmtDate(usage.renewsAt)}.
              </strong>{' '}
              Renew to keep {limitsSentence(usage.plan)}.
            </>
          )}
          {!canRenew && ' Ask an administrator to renew it.'}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {canRenew && onRenew && (
          <button
            type="button"
            onClick={onRenew}
            className="rounded-lg bg-accent px-3.5 py-2 text-[0.78125rem] font-bold text-white hover:brightness-110 transition"
          >
            {expired ? 'Renew plan' : 'Renew now'}
          </button>
        )}
        {!expired && (
          <button
            type="button"
            onClick={hide}
            aria-label="Hide this reminder"
            className="rounded-lg p-2 text-ink-faint hover:text-ink transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
