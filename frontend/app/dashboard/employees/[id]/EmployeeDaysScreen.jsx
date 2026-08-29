'use client';

// frontend/app/dashboard/employees/[id]/EmployeeDaysScreen.jsx
//
// Per-employee daily figures, each one carrying its confidence.
//
// Step 14: "UI shows a warning banner when bindingConfidence < 0.6".
//
// THE RULE THIS SCREEN OBEYS
//
// No figure appears without the confidence that qualifies it. Not in a
// tooltip, not in a footnote — beside it, at the same visual weight, because
// "6h 12m at desk" and "6h 12m at desk, 0.42 confidence" are different claims
// and only one of them is defensible.
//
// Unattributed time is shown for the same reason. A day where the system could
// not tell who somebody was reads as a SHORT day unless the missing minutes
// are visible, and a short day is a claim about that person's behaviour that
// they would rightly dispute.

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft, Loader2, ScanFace, CalendarDays, HelpCircle,
} from 'lucide-react';

import DashboardShell from '../../DashboardShell';
import { Banner } from '../../../components/AuthFormBits';
import ConfidenceBanner, {
  ConfidencePill, LOW_CONFIDENCE,
} from '../../../components/ConfidenceBanner';
import { getEmployeeDays } from '../dayActions';

function hhmm(minutes) {
  const m = Math.max(0, Math.round(Number(minutes ?? 0)));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function dayLabel(value) {
  if (!value) return '';
  try {
    return new Date(`${value}T00:00:00Z`).toLocaleDateString(undefined, {
      weekday: 'short', day: 'numeric', month: 'short',
    });
  } catch {
    return value;
  }
}

/** A figure and the confidence that qualifies it, never one without the other. */
function Stat({ label, value, hint }) {
  return (
    <div className="rounded-lg border border-line bg-ground px-3 py-2.5">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
        {label}
      </p>
      <p className="text-[18px] font-black text-ink mt-0.5">{value}</p>
      {hint && <p className="text-[11px] text-ink-faint font-medium mt-0.5">{hint}</p>}
    </div>
  );
}

export default function EmployeeDaysScreen({ orgName, initialRole, employee, viewer }) {
  const [state, setState] = useState({ days: [], totals: null, loading: true });
  const [banner, setBanner] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await getEmployeeDays(employee.id, 14);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setState((s) => ({ ...s, loading: false }));
        return;
      }
      setState({ days: res.days, totals: res.totals, loading: false });
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
      setState((s) => ({ ...s, loading: false }));
    }
  }, [employee.id]);

  useEffect(() => { load(); }, [load]);

  const t = state.totals;

  return (
    <DashboardShell user={viewer} role={initialRole}>
      <div className="mx-auto max-w-3xl">

        <Link
          href="/dashboard/employees"
          className="inline-flex items-center gap-1.5 text-[13px] font-bold text-ink-muted hover:text-ink mb-4 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Employees
        </Link>

        <header className="flex flex-col gap-2 mb-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName} · {employee.employeeCode}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            {employee.displayName}
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            The last 14 days, as measured. Every figure carries the confidence behind
            it — a number on its own is not something you could defend.
          </p>
        </header>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

        {state.loading ? (
          <div className="rounded-xl border border-line bg-surface px-4 py-12 flex items-center justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
          </div>
        ) : !t || t.days === 0 ? (
          <div className="rounded-xl border border-line bg-surface px-4 py-10 text-center">
            <p className="text-[14px] font-bold text-ink mb-1">Nothing measured yet</p>
            <p className="text-[13px] text-ink-muted font-medium leading-relaxed max-w-md mx-auto">
              Figures appear here once {employee.displayName} has been recognised on a
              camera. That needs a desk assigned, or a face enrolled at a door camera.
            </p>
            <Link
              href={`/dashboard/employees/${employee.id}/enroll`}
              className="inline-flex items-center gap-2 mt-4 px-4 py-2.5 rounded-lg border-2 border-field text-ink font-bold text-[13px] hover:border-field-hover transition-colors"
            >
              <ScanFace className="w-4 h-4" />
              Enrol a face
            </Link>
          </div>
        ) : (
          <>
            {/* STEP 14: the banner. Renders only when it has something to say. */}
            <div className="mb-5">
              <ConfidenceBanner
                confidence={t.bindingConfidence}
                unknownMinutes={t.unknownMinutes}
                presentMinutes={t.presentMinutes}
                subject={`${employee.displayName}'s figures`}
              />
            </div>

            <section className="mb-6 rounded-xl border border-line bg-surface p-4 sm:p-5">
              <div className="flex items-center justify-between gap-3 mb-4">
                <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
                  <CalendarDays className="w-4 h-4 text-accent" />
                  Across {t.days} day{t.days === 1 ? '' : 's'}
                </h2>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                    confidence
                  </span>
                  <ConfidencePill value={t.bindingConfidence} />
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat label="At desk" value={hhmm(t.deskMinutes)} />
                <Stat label="Seated" value={hhmm(t.seatedMinutes)} />
                <Stat label="Chair exits" value={t.awayFromDeskCount} hint="over 90s away" />
                <Stat label="Breaks" value={hhmm(t.breakMinutes)} />
              </div>

              {t.unknownMinutes > 0 && (
                <div className="mt-3 rounded-lg border border-line bg-ground px-3 py-2.5 flex items-start gap-2">
                  <HelpCircle className="w-3.5 h-3.5 text-ink-faint shrink-0 mt-0.5" />
                  <p className="text-[12px] text-ink-muted font-medium leading-relaxed">
                    <span className="font-bold text-ink">
                      {hhmm(t.unknownMinutes)} unattributed.
                    </span>{' '}
                    Somebody was observed, but the system was not confident enough to say
                    who — so it did not guess. That time is excluded from the figures
                    above rather than added to them.
                  </p>
                </div>
              )}
            </section>

            <section>
              <h2 className="text-[15px] font-black tracking-tight text-ink mb-3">
                Day by day
              </h2>
              <ul className="flex flex-col gap-2">
                {state.days.map((d) => {
                  const low = Number(d.bindingConfidence ?? 0) < LOW_CONFIDENCE;
                  return (
                    <li
                      key={d.statDate}
                      className={`rounded-xl border bg-surface p-4 ${
                        low ? 'border-amber-500/40' : 'border-line'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
                        <p className="text-[14px] font-black text-ink">
                          {dayLabel(d.statDate)}
                        </p>
                        <ConfidencePill value={d.bindingConfidence} />
                      </div>
                      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] font-medium">
                        <span className="text-ink-muted">
                          desk <span className="font-bold text-ink">{hhmm(d.deskMinutes)}</span>
                        </span>
                        <span className="text-ink-muted">
                          exits <span className="font-bold text-ink">{d.awayFromDeskCount}</span>
                        </span>
                        <span className="text-ink-muted">
                          breaks <span className="font-bold text-ink">{hhmm(d.breakMinutes)}</span>
                        </span>
                        <span className="text-ink-muted">
                          focus <span className="font-bold text-ink">{hhmm(d.longestFocusBlock)}</span>
                        </span>
                        {Number(d.unknownMinutes ?? 0) > 0 && (
                          <span className="text-amber-700 dark:text-amber-400 font-bold">
                            {hhmm(d.unknownMinutes)} unattributed
                          </span>
                        )}
                      </div>
                      {low && (
                        <p className="mt-2 text-[11.5px] text-amber-700 dark:text-amber-400 font-medium">
                          Low confidence — read this day as an estimate.
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}

        <p className="mt-6 text-[12px] text-ink-faint font-medium leading-relaxed">
          These figures cannot see work done away from a camera — a meeting in another
          room, a call taken outside. A low desk time is not the same as a short day, and
          nothing here should be read as one.
        </p>
      </div>
    </DashboardShell>
  );
}
