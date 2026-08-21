'use client';

// frontend/app/components/AnalyticsCharts.jsx
//
// The dashboard's three analytics charts, read from the live telemetry API.
//
// Every value here comes from `activity_logs`, which the CV pipeline writes as
// it processes a video or a live camera (see backend/app/db/activity_writer.py).
// Nothing on this page is generated or placeholder data: when the table is
// empty the charts say so and explain what would fill them, because "no one has
// been observed yet" and "we could not reach the backend" are different
// situations and must not look alike.

import React, { useCallback, useEffect, useState } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  CartesianGrid
} from 'recharts';
import {
  TrendingUp, PieChart as PieIcon, BarChart3,
  Loader2, AlertCircle, Inbox, RefreshCw
} from 'lucide-react';

const BACKEND_HTTP = 'http://localhost:8001';

/** How far back the charts look, and how often they re-poll. */
const WINDOW_HOURS = 24;
const REFRESH_INTERVAL_MS = 15000;

/**
 * Chart colours are read from CSS custom properties rather than hardcoded, so
 * a theme toggle repaints the charts along with everything else. Reading them
 * at render time (not module load) is what makes that work — module scope would
 * capture whichever theme happened to be active on first import.
 */
function cssVar(name, fallback) {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Posture states separated by weight along one red ramp, not by unrelated hues. */
const POSTURE_VARS = {
  Sitting:  '--chart-1',
  Standing: '--chart-2',
  Walking:  '--chart-3',
};

/* ── shared chart chrome ──────────────────────────────────────────────────── */

function Panel({ title, icon: Icon, iconColor, wide = false, children }) {
  return (
    <div className={`glass-panel p-5 flex flex-col gap-4 ${wide ? 'md:col-span-2' : ''}`}>
      <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
        <Icon className={`w-4 h-4 ${iconColor}`} />
        <span>{title}</span>
      </div>
      {children}
    </div>
  );
}

/**
 * One state block for a chart body — loading, error, or empty.
 *
 * Height is passed in so the placeholder occupies exactly the space the chart
 * will take, and the panel does not resize when data arrives.
 */
function ChartState({ kind, height, message }) {
  const config = {
    loading: { Icon: Loader2, cls: 'text-accent animate-spin', tone: 'text-ink-muted' },
    error:   { Icon: AlertCircle, cls: 'text-accent', tone: 'text-accent' },
    empty:   { Icon: Inbox, cls: 'text-ink-faint', tone: 'text-ink-muted' },
  }[kind];

  const { Icon, cls, tone } = config;

  return (
    <div
      className={`${height} w-full flex flex-col items-center justify-center gap-2.5 text-center px-4`}
      role="status"
      aria-live="polite"
    >
      <Icon className={`w-6 h-6 ${cls}`} strokeWidth={1.8} />
      <p className={`text-xs leading-relaxed max-w-xs ${tone}`}>{message}</p>
    </div>
  );
}

const EMPTY_HINT =
  'No telemetry yet. Upload a video or start the live camera above — occupancy and posture data appear here as people are detected.';

/* ── data hook ────────────────────────────────────────────────────────────── */

/**
 * Fetches all three endpoints together and re-polls on an interval.
 *
 * The three charts share one loading/error state deliberately: they are three
 * views of the same telemetry, so showing two charts while a third spins reads
 * as a broken panel rather than as partial progress.
 */
function useTelemetry() {
  const [state, setState] = useState({
    status: 'loading',   // 'loading' | 'ready' | 'error'
    summary: null,
    historical: [],
    zones: [],
    error: null,
  });

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState((s) => ({ ...s, status: 'loading' }));

    try {
      const [summaryRes, historicalRes, zonesRes] = await Promise.all([
        fetch(`${BACKEND_HTTP}/api/v1/analytics/summary`, { cache: 'no-store' }),
        fetch(`${BACKEND_HTTP}/api/v1/analytics/historical?hours=${WINDOW_HOURS}`, { cache: 'no-store' }),
        fetch(`${BACKEND_HTTP}/api/v1/analytics/zones?hours=${WINDOW_HOURS}`, { cache: 'no-store' }),
      ]);

      if (!summaryRes.ok || !historicalRes.ok || !zonesRes.ok) {
        throw new Error(`Analytics API returned ${summaryRes.status}/${historicalRes.status}/${zonesRes.status}`);
      }

      const [summary, historical, zones] = await Promise.all([
        summaryRes.json(),
        historicalRes.json(),
        zonesRes.json(),
      ]);

      setState({
        status: 'ready',
        summary,
        historical: Array.isArray(historical) ? historical : [],
        zones: Array.isArray(zones) ? zones : [],
        error: null,
      });
    } catch (err) {
      setState((s) => ({
        ...s,
        status: 'error',
        error:
          /failed to fetch|networkerror|load failed/i.test(String(err?.message))
            ? 'Cannot reach the analytics backend. Make sure the FastAPI server is running on port 8001.'
            : String(err?.message || err),
      }));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    // The initial load shows the spinner; every poll after it refreshes in
    // place, so the charts never flash back to a loading state while the user
    // is reading them.
    load();
    const timer = setInterval(() => {
      if (!cancelled) load({ silent: true });
    }, REFRESH_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  return { ...state, reload: load };
}

/* ── transforms ───────────────────────────────────────────────────────────── */

/** "2026-08-21 16:00" → "16:00", the only part the axis needs. */
function toClockLabel(value) {
  if (typeof value !== 'string') return '';
  const parts = value.split(' ');
  return parts.length > 1 ? parts[1] : value;
}

function buildTimeline(historical) {
  return historical.map((row) => ({
    time: toClockLabel(row.time),
    score: Number(row.avg_activity_score) || 0,
  }));
}

/**
 * Posture percentages → pie slices, dropping any zero-value slice.
 *
 * Recharts renders a zero slice as an invisible wedge that still claims a
 * legend entry and a tooltip target, so filtering keeps both honest.
 */
function buildPosture(summary, palette) {
  const dist = summary?.posture_distribution;
  if (!dist) return [];

  return [
    { name: 'Sitting',  value: Number(dist.sitting_percentage) || 0 },
    { name: 'Standing', value: Number(dist.standing_percentage) || 0 },
    { name: 'Walking',  value: Number(dist.walking_percentage) || 0 },
  ]
    .filter((slice) => slice.value > 0)
    .map((slice) => ({ ...slice, color: palette[slice.name] }));
}

/* ── component ────────────────────────────────────────────────────────────── */

/**
 * Resolves the chart palette from CSS variables, and re-resolves it whenever the
 * theme class on <html> changes.
 *
 * Recharts takes colours as props, not classes, so it cannot inherit a themed
 * value the way the surrounding markup does. Watching the class attribute is
 * what lets the charts repaint on a theme toggle instead of keeping whichever
 * palette was active when they first mounted.
 */
function useChartPalette() {
  const read = () => ({
    series: cssVar('--chart-1', '#DC2626'),
    grid: cssVar('--chart-grid', '#E7E5E9'),
    axis: cssVar('--chart-axis', '#6B6772'),
    tooltipBg: cssVar('--chart-tooltip-bg', '#FFFFFF'),
    line: cssVar('--line', '#E7E5E9'),
    ink: cssVar('--ink', '#0B0A0C'),
    posture: {
      Sitting:  cssVar(POSTURE_VARS.Sitting,  '#DC2626'),
      Standing: cssVar(POSTURE_VARS.Standing, '#F0736F'),
      Walking:  cssVar(POSTURE_VARS.Walking,  '#F7B4B0'),
    },
  });

  const [palette, setPalette] = useState(read);

  useEffect(() => {
    const update = () => setPalette(read());
    update(); // first client render, once real computed styles exist

    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
    return () => observer.disconnect();
  }, []);

  return palette;
}

export const AnalyticsCharts = () => {
  const { status, summary, historical, zones, error, reload } = useTelemetry();
  const { series, grid, axis, tooltipBg, line, ink, posture: posturePalette } = useChartPalette();

  const tooltipStyle = {
    backgroundColor: tooltipBg,
    borderColor: line,
    borderRadius: '10px',
    color: ink,
    fontSize: '12px',
  };

  const timeline = buildTimeline(historical);
  const posture = buildPosture(summary, posturePalette);
  const hasAnyTelemetry = (summary?.total_logs ?? 0) > 0;

  /**
   * Resolves what a given chart should render: its own data, or a shared
   * loading/error state, or an empty state when the pipeline has run but this
   * particular view has nothing in it.
   */
  const stateFor = (rows) => {
    if (status === 'loading') return { kind: 'loading', message: 'Loading telemetry…' };
    if (status === 'error') return { kind: 'error', message: error };
    if (!hasAnyTelemetry || rows.length === 0) return { kind: 'empty', message: EMPTY_HINT };
    return null;
  };

  const timelineState = stateFor(timeline);
  const postureState = stateFor(posture);
  const zonesState = stateFor(zones);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      {/* 1. Activity Score Timeline */}
      <Panel
        wide
        title={`Activity Index Timeline — last ${WINDOW_HOURS}h (0 – 100 Score)`}
        icon={TrendingUp}
        iconColor="text-accent"
      >
        {timelineState ? (
          <ChartState kind={timelineState.kind} height="h-64" message={timelineState.message} />
        ) : (
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={timeline}>
                <CartesianGrid strokeDasharray="3 3" stroke={grid} />
                <XAxis dataKey="time" stroke={axis} />
                <YAxis domain={[0, 100]} stroke={axis} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  itemStyle={{ color: series }}
                  formatter={(value) => [value, 'Activity index']}
                />
                <Line
                  type="monotone"
                  dataKey="score"
                  stroke={series}
                  strokeWidth={3}
                  dot={{ fill: series, r: 4 }}
                  activeDot={{ r: 8 }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {/* 2. Posture Distribution Pie */}
      <Panel title="Posture Balance Ratio" icon={PieIcon} iconColor="text-accent">
        {postureState ? (
          <ChartState kind={postureState.kind} height="h-56" message={postureState.message} />
        ) : (
          <>
            <div className="h-56 w-full flex items-center justify-center">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={posture}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={5}
                    dataKey="value"
                    isAnimationActive={false}
                  >
                    {posture.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(value, name) => [`${value}%`, name]}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex justify-around text-[11.5px] font-semibold text-ink-muted">
              {posture.map((item) => (
                <div key={item.name} className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                  <span>{item.name} ({item.value}%)</span>
                </div>
              ))}
            </div>
          </>
        )}
      </Panel>

      {/* 3. Zone Dwell Time Bar Chart */}
      <Panel title="Workstation Dwell Time (Minutes)" icon={BarChart3} iconColor="text-accent">
        {zonesState ? (
          <ChartState kind={zonesState.kind} height="h-56" message={zonesState.message} />
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={zones}>
                <CartesianGrid strokeDasharray="3 3" stroke={grid} />
                <XAxis dataKey="zone" stroke={axis} />
                <YAxis stroke={axis} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(value, _name, entry) => [
                    `${value} min · ${entry?.payload?.visitors ?? 0} tracked`,
                    'Dwell',
                  ]}
                />
                <Bar dataKey="minutes" fill={series} radius={[6, 6, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {/* Retry lives outside the panels: one failure is a backend-wide failure,
          so a single control is clearer than three identical buttons. */}
      {status === 'error' && (
        <div className="md:col-span-2 flex justify-center">
          <button
            type="button"
            onClick={() => reload()}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink text-[12px] font-bold transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Retry
          </button>
        </div>
      )}
    </div>
  );
};
