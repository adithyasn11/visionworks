'use client';

// frontend/app/dashboard/team/TeamScreen.jsx
//
// Step 16 — the team comparison.
//
// A sortable table across employees, "with an explicit note on what the data
// cannot tell you (off-camera work, meetings elsewhere)".
//
// WHY THE CAVEAT IS AT THE TOP AND NOT THE BOTTOM
//
// This is the one screen in the system that invites ranking people against
// each other. Sorting by desk time descending produces something that looks
// exactly like a productivity leaderboard, and somebody will read it as one.
//
// A footnote under the table does not reach that reader — they have already
// drawn their conclusion from the first three rows. So the limits are stated
// above the table, before the numbers, where they are part of reading it
// rather than a disclaimer appended to it.
//
// The figures are also deliberately shown as PER-DAY AVERAGES, not totals.
// A total ranks whoever the cameras happened to observe most often, which is a
// fact about camera coverage presented as a fact about a person.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  Users, Loader2, ArrowUpDown, ArrowUp, ArrowDown, Info, HelpCircle,
  MapPinOff, EyeOff, ChevronRight,
} from 'lucide-react';

import DashboardShell from '../DashboardShell';
import { Banner } from '../../components/AuthFormBits';
import { ConfidencePill, LOW_CONFIDENCE } from '../../components/ConfidenceBanner';
import { getTeamComparison } from './actions';

function hhmm(minutes) {
  const m = Math.max(0, Math.round(Number(minutes ?? 0)));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/**
 * The columns, and how each one sorts.
 *
 * `numeric` drives the default direction: names sort A-Z on first click,
 * numbers sort high-to-low, because that is what somebody clicking a number
 * column is asking for.
 */
const COLUMNS = [
  { key: 'displayName',       label: 'Employee',   numeric: false, align: 'left' },
  { key: 'avgDeskMinutes',    label: 'Avg / day',  numeric: true,
    hint: 'Mean desk time per observed day. For somebody with no assigned '
        + 'desk this shows time observed anywhere instead.' },
  { key: 'deskMinutes',       label: 'Total desk', numeric: true,
    hint: 'Across the whole window' },
  { key: 'awayFromDeskCount', label: 'Exits',      numeric: true,
    hint: 'Times away from the desk for over 90s' },
  { key: 'breakMinutes',      label: 'Breaks',     numeric: true },
  { key: 'longestFocusBlock', label: 'Best focus', numeric: true,
    hint: 'Longest unbroken seated block' },
  { key: 'bindingConfidence', label: 'Confidence', numeric: true,
    hint: 'How sure the system was that this is the right person' },
];

/**
 * Columns that measure time AT AN ASSIGNED DESK.
 *
 * They are undefined rather than zero for somebody who has no desk: the
 * measurement was never possible, not attempted and found to be nothing. The
 * table renders these as "—" for those people, and the sort keeps them out of
 * the ranking rather than pinning them to the bottom on a false zero.
 */
const DESK_COLUMNS = new Set([
  'avgDeskMinutes', 'deskMinutes', 'awayFromDeskCount', 'longestFocusBlock',
]);

const WINDOWS = [7, 30, 90];

export default function TeamScreen({ orgName, initialRole, viewer }) {
  const [state, setState] = useState({ team: [], totals: null, scope: 'team', loading: true });
  const [banner, setBanner] = useState(null);
  const [window_, setWindow] = useState(30);
  const [sort, setSort] = useState({ key: 'avgDeskMinutes', dir: 'desc' });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await getTeamComparison(window_);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setState((s) => ({ ...s, loading: false }));
        return;
      }
      setBanner(null);
      setState({ team: res.team, totals: res.totals, scope: res.scope, loading: false });
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not reach the server. Check your connection and try again.',
      });
      setState((s) => ({ ...s, loading: false }));
    }
  }, [window_]);

  useEffect(() => { load(); }, [load]);

  const sorted = useMemo(() => {
    const rows = [...state.team];
    const col = COLUMNS.find((c) => c.key === sort.key);
    rows.sort((a, b) => {
      // People with no measured days always sink to the bottom, whichever
      // column is sorted. They are not "zero hours at their desk" — they were
      // never observed, and ranking them among people who were would be the
      // single most misleading thing this table could do.
      if (a.measured !== b.measured) return a.measured ? -1 : 1;

      // Desk-derived columns do not apply to somebody with no assigned desk.
      // Sorting them by a 0 they could never have avoided ranks them last on a
      // measure that was never taken — so they sink below the people the
      // column actually describes, the same way unmeasured people do.
      if (DESK_COLUMNS.has(sort.key) && a.hasDesk !== b.hasDesk) {
        return a.hasDesk ? -1 : 1;
      }

      let av = a[sort.key];
      let bv = b[sort.key];
      if (!col?.numeric) {
        av = String(av ?? '').toLowerCase();
        bv = String(bv ?? '').toLowerCase();
        return sort.dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      av = Number(av ?? 0);
      bv = Number(bv ?? 0);
      return sort.dir === 'asc' ? av - bv : bv - av;
    });
    return rows;
  }, [state.team, sort]);

  const toggleSort = (key) => {
    const col = COLUMNS.find((c) => c.key === key);
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: col?.numeric ? 'desc' : 'asc' });
  };

  const t = state.totals;
  const selfOnly = state.scope === 'self';

  return (
    <DashboardShell user={viewer} role={initialRole}>
      <div className="mx-auto max-w-6xl">

        <header className="flex flex-col gap-2 mb-5">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Team
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-2xl">
            Everyone the system is permitted to name, compared over the same window.
            Figures are per-day averages so that someone observed on three days and
            someone observed on twenty can be read side by side.
          </p>
        </header>

        {/* ── WHAT THIS CANNOT TELL YOU ──────────────────────────────────
            Above the table, deliberately. See the note at the top of this
            file: a caveat below a leaderboard is a caveat nobody reads. */}
        <section className="rounded-xl border border-line bg-surface p-4 sm:p-5 mb-5">
          <h2 className="text-[13.5px] font-black tracking-tight text-ink flex items-center gap-2 mb-2.5">
            <Info className="w-4 h-4 text-accent" />
            What these numbers cannot tell you
          </h2>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-[12.5px] text-ink-muted font-medium leading-relaxed">
            <li className="flex gap-2">
              <span className="text-ink-faint mt-0.5">—</span>
              <span>
                <span className="font-bold text-ink">Work done off camera is invisible.</span>{' '}
                A meeting in another room, a call taken outside, a day working from
                home. Low desk time is not a short day.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-ink-faint mt-0.5">—</span>
              <span>
                <span className="font-bold text-ink">Time at a desk is not output.</span>{' '}
                Someone thinking hard and someone staring at a screen are the same
                observation. Nothing here measures what was produced.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-ink-faint mt-0.5">—</span>
              <span>
                <span className="font-bold text-ink">A dash is not a zero.</span>{' '}
                Desk time, exits and focus need an assigned desk. Where somebody
                has none these read &quot;—&quot;, because the measurement was never
                possible — not attempted and found to be nothing.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-ink-faint mt-0.5">—</span>
              <span>
                <span className="font-bold text-ink">Coverage differs per person.</span>{' '}
                Somebody without an assigned desk, or seated outside a camera&apos;s
                view, is measured less — a fact about the cameras, not about them.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-ink-faint mt-0.5">—</span>
              <span>
                <span className="font-bold text-ink">Every figure has a confidence.</span>{' '}
                Below {LOW_CONFIDENCE.toFixed(2)} the system was not consistently sure
                who it was watching. Those rows are estimates.
              </span>
            </li>
          </ul>
        </section>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

        {selfOnly && !state.loading && (
          <div className="mb-5 rounded-xl border border-line bg-surface px-4 py-3 flex items-start gap-2.5">
            <EyeOff className="w-4 h-4 text-ink-faint shrink-0 mt-0.5" />
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              <span className="font-bold text-ink">You are seeing only your own figures.</span>{' '}
              Measured data about other people is visible to administrators and managers.
              This is not an empty team — it is the part of it you have access to.
            </p>
          </div>
        )}

        {/* ── CONTROLS ──────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
          <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => setWindow(w)}
                aria-pressed={window_ === w}
                className={`px-3 py-1.5 rounded-[6px] text-[12px] font-bold transition-colors ${
                  window_ === w
                    ? 'bg-accent-soft text-accent'
                    : 'text-ink-muted hover:text-ink'
                }`}
              >
                {w} days
              </button>
            ))}
          </div>

          {t && !state.loading && (
            <div className="flex items-center gap-3 text-[11.5px] font-semibold text-ink-muted flex-wrap">
              <span>
                <span className="font-black text-ink">{t.measuredPeople}</span> measured
                {t.unmeasuredPeople > 0 && (
                  <span className="text-ink-faint"> · {t.unmeasuredPeople} not yet</span>
                )}
              </span>
              <span className="flex items-center gap-1.5">
                team confidence
                <ConfidencePill value={t.bindingConfidence} />
              </span>
            </div>
          )}
        </div>

        {/* ── TABLE ─────────────────────────────────────────────────────── */}
        <div className="rounded-xl border border-line bg-surface overflow-hidden">
          {state.loading ? (
            <div className="px-4 py-16 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
            </div>
          ) : sorted.length === 0 ? (
            <div className="px-4 py-12 text-center">
              <Users className="w-6 h-6 text-ink-faint mx-auto mb-2" strokeWidth={1.8} />
              <p className="text-[14px] font-bold text-ink mb-1">No employees yet</p>
              <p className="text-[13px] text-ink-muted font-medium max-w-md mx-auto leading-relaxed">
                Add people on the{' '}
                <Link href="/dashboard/employees" className="text-accent font-bold hover:underline">
                  Employees
                </Link>{' '}
                page, and give them a desk so the system can attribute their time.
              </p>
            </div>
          ) : (
            /* The wrapper scrolls, not the page: a wide table must never make
               the whole document scroll sideways. */
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse min-w-[760px]">
                <thead>
                  <tr className="border-b border-line">
                    {COLUMNS.map((c) => {
                      const active = sort.key === c.key;
                      const Icon = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown;
                      return (
                        <th
                          key={c.key}
                          scope="col"
                          aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                          className={`px-3 py-2.5 ${c.align === 'left' ? 'text-left' : 'text-right'}`}
                        >
                          <button
                            type="button"
                            onClick={() => toggleSort(c.key)}
                            title={c.hint}
                            className={`inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] transition-colors ${
                              active ? 'text-accent' : 'text-ink-faint hover:text-ink'
                            } ${c.align === 'left' ? '' : 'flex-row-reverse'}`}
                          >
                            {c.label}
                            <Icon className="w-3 h-3 shrink-0" />
                          </button>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r) => {
                    const low = r.measured && r.bindingConfidence < LOW_CONFIDENCE;
                    return (
                      <tr
                        key={r.id}
                        className={`border-b border-line last:border-0 transition-colors hover:bg-surface-alt ${
                          !r.measured ? 'opacity-60' : ''
                        }`}
                      >
                        <td className="px-3 py-2.5">
                          <Link
                            href={`/dashboard/employees/${r.id}`}
                            className="group inline-flex flex-col gap-0.5"
                          >
                            <span className="text-[13.5px] font-bold text-ink group-hover:text-accent transition-colors inline-flex items-center gap-1">
                              {r.displayName}
                              <ChevronRight className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
                            </span>
                            <span className="font-mono text-[10.5px] text-ink-faint flex items-center gap-1.5">
                              {r.employeeCode}
                              {!r.hasDesk && (
                                <span className="inline-flex items-center gap-1 text-ink-faint"
                                      title="No desk assigned — cannot be identified by seat">
                                  <MapPinOff className="w-3 h-3" />
                                  no desk
                                </span>
                              )}
                            </span>
                          </Link>
                        </td>

                        {!r.measured ? (
                          <td colSpan={COLUMNS.length - 1}
                              className="px-3 py-2.5 text-right text-[12px] text-ink-faint font-medium italic">
                            Not yet measured
                          </td>
                        ) : (
                          <>
                            <td className="px-3 py-2.5 text-right">
                              {/* Somebody with no assigned desk has 0 desk
                                  minutes however long they were on camera.
                                  Printing "0m" next to a colleague's "5h" says
                                  they did nothing, when what actually happened
                                  is that the system had nowhere to attribute
                                  their time TO. Show what WAS measured. */}
                              {r.hasDesk ? (
                                <>
                                  <span className="text-[13.5px] font-black text-ink">
                                    {hhmm(r.avgDeskMinutes)}
                                  </span>
                                  <span className="block text-[10.5px] text-ink-faint font-medium">
                                    over {r.days}d
                                  </span>
                                </>
                              ) : (
                                <>
                                  <span className="text-[13.5px] font-black text-ink-muted">
                                    {hhmm(r.avgPresentMinutes)}
                                  </span>
                                  <span className="block text-[10.5px] text-ink-faint font-medium">
                                    seen, not at a desk
                                  </span>
                                </>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                              {r.hasDesk ? hhmm(r.deskMinutes)
                                         : <span className="text-ink-faint">—</span>}
                              {r.unknownMinutes > 0 && (
                                <span
                                  className="block text-[10.5px] font-bold text-amber-700 dark:text-amber-400"
                                  title="Observed but not confidently identified — excluded from the figures"
                                >
                                  +{hhmm(r.unknownMinutes)} unattributed
                                </span>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                              {r.hasDesk ? r.awayFromDeskCount
                                         : <span className="text-ink-faint">—</span>}
                            </td>
                            <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                              {hhmm(r.breakMinutes)}
                            </td>
                            <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                              {/* A focus block is "seated at YOUR desk". With
                                  no desk the measure does not exist, and 0m
                                  would read as "never concentrated". */}
                              {r.hasDesk ? hhmm(r.longestFocusBlock)
                                         : <span className="text-ink-faint">—</span>}
                            </td>
                            <td className="px-3 py-2.5 text-right">
                              <ConfidencePill value={r.bindingConfidence} />
                              {low && (
                                <span className="block text-[10.5px] font-bold text-amber-700 dark:text-amber-400 mt-0.5">
                                  estimate
                                </span>
                              )}
                            </td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {t && t.totalUnknownMinutes > 0 && (
          <div className="mt-3 rounded-xl border border-line bg-surface px-4 py-3 flex items-start gap-2.5">
            <HelpCircle className="w-4 h-4 text-ink-faint shrink-0 mt-0.5" />
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              <span className="font-bold text-ink">
                {hhmm(t.totalUnknownMinutes)} unattributed across the team.
              </span>{' '}
              Somebody was observed during that time, but the system was not confident
              enough to say who — so it did not guess. That time is excluded from every
              figure above rather than distributed among the people who might have been
              there.
            </p>
          </div>
        )}

        {t && t.withoutDesk > 0 && (
          <p className="mt-3 text-[12px] text-ink-faint font-medium leading-relaxed">
            {t.withoutDesk} {t.withoutDesk === 1 ? 'person has' : 'people have'} no desk
            assigned, so they can only be identified by face at a door camera. Assigning
            a desk on the{' '}
            <Link href="/dashboard/employees" className="text-accent font-bold hover:underline">
              Employees
            </Link>{' '}
            page is the strongest identity signal available and needs no biometrics.
          </p>
        )}
      </div>
    </DashboardShell>
  );
}
