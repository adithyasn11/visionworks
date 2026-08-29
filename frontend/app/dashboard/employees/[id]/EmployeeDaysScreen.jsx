'use client';

// frontend/app/dashboard/employees/[id]/EmployeeDaysScreen.jsx
//
// Step 15 — the per-employee dashboard.
//
// Daily timeline, desk time, chair exits, breaks, focus blocks, and the 7/30-day
// trend. The plan's requirement, in its own words: "Show the confidence on
// every figure — a number without its confidence is a claim you cannot defend
// in the viva."
//
// THE RULE THIS SCREEN OBEYS
//
// No figure appears without the confidence that qualifies it. Not in a tooltip,
// not in a footnote — beside it, at the same visual weight, because "6h 12m at
// desk" and "6h 12m at desk, 0.42 confidence" are different claims and only one
// of them is defensible.
//
// Unattributed time is shown for the same reason. A day where the system could
// not tell who somebody was reads as a SHORT day unless the missing minutes are
// visible, and a short day is a claim about that person's behaviour that they
// would rightly dispute.
//
// WHY THE TIMELINE IS BARS AND NOT A LINE
//
// A line implies continuity between its points — that something was measured
// on the way from 09:00 to 10:00, and that the value moved smoothly. Neither is
// true: each hour is an independent bucket, and an hour with no observation is
// not a dip toward zero, it is an absence. Bars say "these are buckets", and a
// missing bar says "nothing here" without drawing a line through it.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip,
  CartesianGrid, Cell, LineChart, Line,
} from 'recharts';
import {
  ArrowLeft, Loader2, ScanFace, CalendarDays, HelpCircle, Clock,
  TrendingUp, Armchair, DoorOpen, Coffee, Target, ChevronRight,
} from 'lucide-react';

import DashboardShell from '../../DashboardShell';
import { Banner } from '../../../components/AuthFormBits';
import ConfidenceBanner, {
  ConfidencePill, LOW_CONFIDENCE,
} from '../../../components/ConfidenceBanner';
import { getEmployeeDays, getEmployeeHours, getEmployeeTrend } from '../dayActions';

/* ── formatting ───────────────────────────────────────────────────────────── */

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

function shortDay(value) {
  if (!value) return '';
  try {
    return new Date(`${value}T00:00:00Z`).toLocaleDateString(undefined, {
      day: 'numeric', month: 'short',
    });
  } catch {
    return value;
  }
}

/**
 * Chart colours come from CSS custom properties, re-read when the theme class
 * on <html> changes.
 *
 * Recharts takes colours as props, not classes, so it cannot inherit a themed
 * value the way the surrounding markup does — the same reason AnalyticsCharts
 * watches the class attribute rather than resolving once at mount.
 */
function useChartPalette() {
  const read = () => {
    if (typeof window === 'undefined') {
      return { desk: '#C7362F', seated: '#3B62B0', unknown: '#B7791F',
               grid: '#ECEAE7', axis: '#6B6772', tooltipBg: '#FFFFFF',
               line: '#E7E5E9', ink: '#0B0A0C', faint: '#ECEAE7' };
    }
    const v = (n, f) => getComputedStyle(document.documentElement)
      .getPropertyValue(n).trim() || f;
    return {
      desk: v('--chart-1', '#C7362F'),
      seated: v('--chart-2', '#3B62B0'),
      unknown: v('--chart-3', '#B7791F'),
      grid: v('--chart-grid', '#ECEAE7'),
      axis: v('--chart-axis', '#6B6772'),
      tooltipBg: v('--chart-tooltip-bg', '#FFFFFF'),
      line: v('--line', '#E7E5E9'),
      ink: v('--ink', '#0B0A0C'),
      faint: v('--surface-alt', '#F4F4F5'),
    };
  };

  const [palette, setPalette] = useState(read);
  useEffect(() => {
    const update = () => setPalette(read());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true, attributeFilter: ['class'],
    });
    return () => observer.disconnect();
  }, []);
  return palette;
}

/* ── small pieces ─────────────────────────────────────────────────────────── */

/** A figure, its unit, and optionally the confidence that qualifies it. */
function Stat({ icon: Icon, label, value, hint, confidence }) {
  return (
    <div className="rounded-lg border border-line bg-ground px-3 py-2.5 min-w-0">
      <div className="flex items-center justify-between gap-2 mb-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint flex items-center gap-1.5 min-w-0">
          {Icon && <Icon className="w-3 h-3 shrink-0" />}
          <span className="truncate">{label}</span>
        </p>
        {confidence !== undefined && (
          <ConfidencePill value={confidence} className="shrink-0" />
        )}
      </div>
      <p className="text-[18px] font-black text-ink leading-none">{value}</p>
      {hint && (
        <p className="text-[11px] text-ink-faint font-medium mt-1 leading-snug">{hint}</p>
      )}
    </div>
  );
}

function Panel({ title, icon: Icon, right, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-4 sm:p-5 ${className}`}>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
          {Icon && <Icon className="w-4 h-4 text-accent" />}
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

/**
 * A window of the trend, as a comparison rather than a bare number.
 *
 * The delta against the other window is the point: "5h 40m" is not
 * interpretable, "5h 40m, 22m below the 30-day average" is.
 */
function TrendCard({ title, summary, compare }) {
  if (!summary) {
    return (
      <div className="rounded-lg border border-line bg-ground px-3 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint mb-1">
          {title}
        </p>
        <p className="text-[12.5px] text-ink-muted font-medium">Nothing measured yet.</p>
      </div>
    );
  }

  const delta = compare ? summary.avgDeskMinutes - compare.avgDeskMinutes : null;
  const deltaLabel = delta === null || Math.abs(delta) < 1
    ? null
    : `${delta > 0 ? '+' : '−'}${hhmm(Math.abs(delta))} vs 30-day`;

  return (
    <div className="rounded-lg border border-line bg-ground px-3 py-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
          {title}
        </p>
        <ConfidencePill value={summary.bindingConfidence} />
      </div>
      <p className="text-[20px] font-black text-ink leading-none">
        {hhmm(summary.avgDeskMinutes)}
        <span className="text-[11px] font-bold text-ink-faint ml-1.5">avg at desk</span>
      </p>
      {deltaLabel && (
        <p className={`text-[11.5px] font-bold mt-1 ${
          delta > 0 ? 'text-emerald-700 dark:text-emerald-400'
                    : 'text-amber-700 dark:text-amber-400'}`}>
          {deltaLabel}
        </p>
      )}
      <div className="mt-2.5 pt-2.5 border-t border-line grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <p className="text-ink-faint font-medium">exits</p>
          <p className="font-bold text-ink">{summary.avgExits}</p>
        </div>
        <div>
          <p className="text-ink-faint font-medium">breaks</p>
          <p className="font-bold text-ink">{hhmm(summary.avgBreakMinutes)}</p>
        </div>
        <div>
          <p className="text-ink-faint font-medium">focus</p>
          <p className="font-bold text-ink">{hhmm(summary.avgFocusBlock)}</p>
        </div>
      </div>
      <p className="text-[10.5px] text-ink-faint font-medium mt-2">
        over {summary.days} observed day{summary.days === 1 ? '' : 's'}
      </p>
    </div>
  );
}

/* ── the timeline ─────────────────────────────────────────────────────────── */

/**
 * One day, hour by hour.
 *
 * Each bar is stacked: desk time, then unattributed time above it. Stacking
 * rather than placing them side by side is deliberate — they are parts of the
 * same hour, and putting them adjacent would let a reader see a tall pair and
 * conclude the person was there for two hours.
 *
 * An hour whose confidence falls below the threshold is drawn muted, so the
 * chart itself carries the caveat rather than relying on a note beneath it.
 */
function HourlyTimeline({ hours, palette, loading, date }) {
  if (loading) {
    return (
      <div className="h-52 flex items-center justify-center">
        <Loader2 className="w-4 h-4 animate-spin text-ink-faint" />
      </div>
    );
  }
  if (!hours || hours.every((h) => !h.observed)) {
    return (
      <div className="h-52 flex flex-col items-center justify-center text-center px-4 gap-1.5">
        <Clock className="w-5 h-5 text-ink-faint" strokeWidth={1.8} />
        <p className="text-[12.5px] text-ink-muted font-medium max-w-xs leading-relaxed">
          No hourly detail for {dayLabel(date)}. The timeline appears once the
          pipeline has aggregated this day.
        </p>
      </div>
    );
  }

  const tooltipStyle = {
    backgroundColor: palette.tooltipBg,
    borderColor: palette.line,
    borderRadius: '10px',
    color: palette.ink,
    fontSize: '12px',
  };

  return (
    <>
      <div className="h-52 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={hours} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={palette.grid} vertical={false} />
            <XAxis
              dataKey="label"
              stroke={palette.axis}
              tick={{ fontSize: 10 }}
              interval="preserveStartEnd"
              minTickGap={12}
            />
            <YAxis
              stroke={palette.axis}
              tick={{ fontSize: 10 }}
              domain={[0, 60]}
              ticks={[0, 15, 30, 45, 60]}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              cursor={{ fill: palette.faint, opacity: 0.5 }}
              formatter={(value, name) => [`${value} min`, name]}
              labelFormatter={(label) => {
                const h = hours.find((x) => x.label === label);
                if (!h?.observed) return `${label} — not observed`;
                return `${label} · confidence ${h.bindingConfidence.toFixed(2)}`;
              }}
            />
            <Bar dataKey="deskMinutes" name="At desk" stackId="a" radius={[0, 0, 0, 0]}>
              {hours.map((h) => (
                <Cell
                  key={h.hour}
                  fill={palette.desk}
                  // An hour the system was unsure about is drawn faded. The
                  // caveat belongs ON the bar, not in a note under the chart.
                  fillOpacity={h.bindingConfidence >= LOW_CONFIDENCE ? 1 : 0.4}
                />
              ))}
            </Bar>
            <Bar
              dataKey="unknownMinutes"
              name="Unattributed"
              stackId="a"
              fill={palette.unknown}
              fillOpacity={0.55}
              radius={[3, 3, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 text-[11px] font-semibold text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: palette.desk }} />
          At desk
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm"
                style={{ backgroundColor: palette.desk, opacity: 0.4 }} />
          Low confidence
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm"
                style={{ backgroundColor: palette.unknown, opacity: 0.55 }} />
          Unattributed
        </span>
      </div>
    </>
  );
}

/* ── screen ───────────────────────────────────────────────────────────────── */

export default function EmployeeDaysScreen({ orgName, initialRole, employee, viewer }) {
  const [state, setState] = useState({ days: [], totals: null, loading: true });
  const [trend, setTrend] = useState({ last7: null, last30: null, series: [] });
  const [hours, setHours] = useState({ rows: null, loading: false });
  const [selectedDate, setSelectedDate] = useState(null);
  const [banner, setBanner] = useState(null);
  const palette = useChartPalette();

  const load = useCallback(async () => {
    try {
      const [res, tr] = await Promise.all([
        getEmployeeDays(employee.id, 30),
        getEmployeeTrend(employee.id),
      ]);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setState((s) => ({ ...s, loading: false }));
        return;
      }
      setState({ days: res.days, totals: res.totals, loading: false });
      if (tr.ok) setTrend({ last7: tr.last7, last30: tr.last30, series: tr.series });
      // Open on the most recent measured day rather than today: today may not
      // have been aggregated yet, and an empty chart on arrival reads as a
      // broken page.
      //
      // Set through the updater form rather than reading `selectedDate` from
      // scope. Depending on it here would put this callback's identity in the
      // hands of a value it also sets, so every date change would re-run the
      // whole 30-day fetch — and the guard would then be reading a stale value
      // anyway.
      if (res.days.length) {
        setSelectedDate((current) => current ?? res.days[0].statDate);
      }
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not reach the server. Check your connection and try again.',
      });
      setState((s) => ({ ...s, loading: false }));
    }
  }, [employee.id]);

  useEffect(() => { load(); }, [load]);

  // The timeline reloads whenever the selected day changes.
  useEffect(() => {
    if (!selectedDate) return;
    let cancelled = false;
    setHours((h) => ({ ...h, loading: true }));
    getEmployeeHours(employee.id, selectedDate)
      .then((res) => {
        if (cancelled) return;
        setHours({ rows: res.ok ? res.hours : null, loading: false });
        if (!res.ok) setBanner({ kind: 'error', text: res.message });
      })
      .catch(() => !cancelled && setHours({ rows: null, loading: false }));
    return () => { cancelled = true; };
  }, [employee.id, selectedDate]);

  const t = state.totals;

  // The daily trend line, oldest first. Recharts draws left to right and the
  // day list arrives newest first.
  const trendSeries = useMemo(
    () => (trend.series ?? []).map((r) => ({
      date: shortDay(r.statDate),
      desk: Math.round(Number(r.deskMinutes ?? 0) / 6) / 10,   // hours, 1dp
      confidence: Number(r.bindingConfidence ?? 0),
    })),
    [trend.series],
  );

  const selectedDay = useMemo(
    () => state.days.find((d) => d.statDate === selectedDate) ?? null,
    [state.days, selectedDate],
  );

  const tooltipStyle = {
    backgroundColor: palette.tooltipBg,
    borderColor: palette.line,
    borderRadius: '10px',
    color: palette.ink,
    fontSize: '12px',
  };

  return (
    <DashboardShell user={viewer} role={initialRole}>
      <div className="mx-auto max-w-5xl">

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
            The last 30 days, as measured. Every figure carries the confidence
            behind it — a number on its own is not something you could defend.
          </p>
        </header>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

        {state.loading ? (
          <div className="rounded-xl border border-line bg-surface px-4 py-16 flex items-center justify-center">
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
          <div className="flex flex-col gap-5">

            {/* STEP 14's banner. Renders only when it has something to say. */}
            <ConfidenceBanner
              confidence={t.bindingConfidence}
              unknownMinutes={t.unknownMinutes}
              presentMinutes={t.presentMinutes}
              subject={`${employee.displayName}'s figures`}
            />

            {/* ── TOTALS ───────────────────────────────────────────────── */}
            <Panel
              title={`Across ${t.days} observed day${t.days === 1 ? '' : 's'}`}
              icon={CalendarDays}
              right={
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                    confidence
                  </span>
                  <ConfidencePill value={t.bindingConfidence} />
                </div>
              }
            >
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat icon={Clock} label="At desk" value={hhmm(t.deskMinutes)} />
                <Stat icon={Armchair} label="Seated" value={hhmm(t.seatedMinutes)} />
                <Stat icon={DoorOpen} label="Chair exits" value={t.awayFromDeskCount}
                      hint="over 90s away" />
                <Stat icon={Coffee} label="Breaks" value={hhmm(t.breakMinutes)} />
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
            </Panel>

            {/* ── DAILY TIMELINE ───────────────────────────────────────── */}
            <Panel
              title="Daily timeline"
              icon={Clock}
              right={
                <div className="flex items-center gap-2 flex-wrap justify-end">
                  <select
                    value={selectedDate ?? ''}
                    onChange={(e) => setSelectedDate(e.target.value)}
                    aria-label="Choose a day to see hour by hour"
                    className="rounded-lg border border-field bg-ground text-ink text-[12px] font-bold px-2.5 py-1.5 focus:outline-none focus:border-field-hover"
                  >
                    {state.days.map((d) => (
                      <option key={d.statDate} value={d.statDate}>
                        {dayLabel(d.statDate)}
                      </option>
                    ))}
                  </select>
                  {selectedDay && (
                    <ConfidencePill value={selectedDay.bindingConfidence} />
                  )}
                </div>
              }
            >
              <HourlyTimeline
                hours={hours.rows}
                palette={palette}
                loading={hours.loading}
                date={selectedDate}
              />
              {selectedDay && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-4 pt-4 border-t border-line">
                  <Stat label="Desk" value={hhmm(selectedDay.deskMinutes)} />
                  <Stat label="Exits" value={selectedDay.awayFromDeskCount} />
                  <Stat label="Breaks" value={hhmm(selectedDay.breakMinutes)} />
                  <Stat label="Longest focus" value={hhmm(selectedDay.longestFocusBlock)}
                        hint="unbroken, seated" />
                </div>
              )}
            </Panel>

            {/* ── TREND ────────────────────────────────────────────────── */}
            <Panel title="Trend" icon={TrendingUp}>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
                <TrendCard title="Last 7 days" summary={trend.last7} compare={trend.last30} />
                <TrendCard title="Last 30 days" summary={trend.last30} />
              </div>

              {trendSeries.length > 1 ? (
                <div className="h-44 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={trendSeries} margin={{ top: 4, right: 6, left: -24, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={palette.grid} vertical={false} />
                      <XAxis dataKey="date" stroke={palette.axis} tick={{ fontSize: 10 }}
                             interval="preserveStartEnd" minTickGap={16} />
                      <YAxis stroke={palette.axis} tick={{ fontSize: 10 }}
                             label={{ value: 'h', position: 'insideTopLeft',
                                      fontSize: 10, fill: palette.axis, dy: -2 }} />
                      <Tooltip
                        contentStyle={tooltipStyle}
                        formatter={(value, name) =>
                          name === 'desk'
                            ? [`${value} h at desk`, 'Desk time']
                            : [Number(value).toFixed(2), 'Confidence']}
                      />
                      <Line type="monotone" dataKey="desk" stroke={palette.desk}
                            strokeWidth={2.5} dot={{ r: 3, fill: palette.desk }}
                            isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="text-[12.5px] text-ink-muted font-medium py-4 text-center">
                  A trend needs at least two measured days.
                </p>
              )}
            </Panel>

            {/* ── DAY BY DAY ───────────────────────────────────────────── */}
            <Panel title="Day by day" icon={CalendarDays}>
              <ul className="flex flex-col gap-2">
                {state.days.map((d) => {
                  const low = Number(d.bindingConfidence ?? 0) < LOW_CONFIDENCE;
                  const active = d.statDate === selectedDate;
                  return (
                    <li key={d.statDate}>
                      <button
                        type="button"
                        onClick={() => setSelectedDate(d.statDate)}
                        aria-pressed={active}
                        className={`w-full text-left rounded-xl border bg-ground p-3.5 transition-colors hover:border-field ${
                          active ? 'border-accent' : low ? 'border-amber-500/40' : 'border-line'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-3 flex-wrap mb-1.5">
                          <p className="text-[14px] font-black text-ink flex items-center gap-1.5">
                            {dayLabel(d.statDate)}
                            {active && (
                              <ChevronRight className="w-3.5 h-3.5 text-accent" />
                            )}
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
                          <p className="mt-1.5 text-[11.5px] text-amber-700 dark:text-amber-400 font-medium">
                            Low confidence — read this day as an estimate.
                          </p>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </Panel>
          </div>
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
