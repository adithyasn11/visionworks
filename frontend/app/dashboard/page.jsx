'use client';

// frontend/app/dashboard/page.jsx
//
// The manager's workspace.
//
// Four sections behind one sidebar, matching the founder console's shell so the
// customer and operator halves of the product read as one application:
//
//   Overview   what happened in the space — the home screen
//   Live feed  the camera, with detection and posture overlays
//   Zones      define the areas occupancy is attributed to
//   Reports    export what was recorded
//
// Sections are view state rather than routes on purpose: they share one polling
// data source and a live video WebSocket, and routing between them would tear
// the socket down and re-fetch everything on every click.

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { RefreshCw, Loader2, Eye, ShieldCheck, Check } from 'lucide-react';

import DashboardShell, { VIEWS } from './DashboardShell';
import OverviewSection from './OverviewSection';
import PlanSection from './PlanSection';
import { VideoCanvasPlayer } from '../components/VideoCanvasPlayer';
import { AnalyticsCharts } from '../components/AnalyticsCharts';
import { FloorplanHeatmap } from '../components/FloorplanHeatmap';
import { ZoneEditor } from '../components/ZoneEditor';
import { ReportExport } from '../components/ReportExport';
import { supabase } from '../lib/supabase/browser';
import { backendFetch } from '../lib/backend';
import { getViewerRole } from '../lib/session';
import { getOverview, getDataCoverage } from '../lib/analytics/queries';
import RangePicker from './RangePicker';
import AlertsPanel from './AlertsPanel';
import { can, denialMessage } from '../lib/permissions';

// Must match a camera actually registered for this org — both in the local
// pipeline registry (backend/app/api/routers/cameras.py) and by NAME in
// Postgres `cameras` (see minute_aggregator._resolve_ids), or telemetry is
// captured but never syncs to the dashboard's data source.
const CAMERA_ID = 'floor5';
const WINDOW_HOURS = 24;
const REFRESH_MS = 15000;

// Reads better than "last 1 days".
const RANGE_LABELS = {
  1: 'last 24 hours',
  7: 'last 7 days',
  30: 'last 30 days',
  90: 'last 90 days',
  365: 'last year',
};

/**
 * Explains why a control is missing.
 *
 * A VIEWER finding no "Save zone" button should be told they are read-only,
 * not left wondering whether the page is broken. Only rendered once the role is
 * known, so it never flashes during load.
 */
function ReadOnlyNotice({ capability }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-line bg-surface-alt px-4 py-3">
      <Eye className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" />
      <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
        You have <strong className="text-ink">view-only</strong> access here. {denialMessage(capability)}
      </p>
    </div>
  );
}

/**
 * Page header: title, one line of context, and the section's own action.
 *
 * The bottom rule matters more than it looks. Without it the header floats
 * directly above the first card and reads as part of it, which is why the
 * sections felt like a stack of boxes rather than a page with a title.
 *
 * `items-start` rather than `items-end`: when the action is a two-row control
 * group (the range picker plus refresh, wrapped on a narrow window) bottom
 * alignment drags the whole heading block down with it.
 */
function PageHeader({ eyebrow, title, subtitle, action }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 pb-5 border-b border-line">
      <div className="min-w-0">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent mb-2">
          {eyebrow}
        </p>
        <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-2 text-[13.5px] text-ink-muted max-w-2xl leading-relaxed">
            {subtitle}
          </p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </header>
  );
}

function DashboardInner() {
  const searchParams = useSearchParams();

  // `?view=` is how the settings pages navigate BACK into a dashboard section:
  // DashboardShell renders its nav as links there (`/dashboard?view=zones`)
  // because those pages are separate routes. Without reading it here, every one
  // of those links silently landed on Overview.
  //
  // Validated against VIEWS rather than trusted — the value is in the URL, so
  // an unknown one falls back to 'overview' instead of rendering no section at
  // all and leaving a blank page.
  const requested = searchParams.get('view');
  const initialView = VIEWS.some((v) => v.id === requested) ? requested : 'overview';

  // Read once, as the INITIAL value only. Making this a synced effect would
  // fight the sidebar: clicking a section sets state without touching the URL,
  // and an effect watching `searchParams` would immediately reset it back.
  const [view, setView] = useState(initialView);
  const [user, setUser] = useState(null);
  // LAYER 1 input. Null until resolved; every capability check below reads
  // false while it is, so a control never flashes visible before we know the
  // role and then disappear. Hiding is courtesy only — the action (layer 2)
  // and the RLS policy (layer 3) both re-check.
  const [role, setRole] = useState(null);
  // The organisation's tier and name, resolved in the SAME round trip as the
  // role (see getViewerRole) so the two can never describe different orgs.
  const [org, setOrg] = useState({ name: null, plan: null, planSelectedAt: null });

  const [overview, setOverview] = useState({ status: 'loading', data: null, error: null });
  const [refreshing, setRefreshing] = useState(false);
  // The historical panels read Postgres `zone_minute_stats`, which holds the
  // org's whole history — so the window is a user choice rather than a fixed
  // 24 hours. Live feed and heatmap still read the running session.
  const [rangeDays, setRangeDays] = useState(7);
  const [coverage, setCoverage] = useState(null);

  // Zones are loaded by the editor and handed up, so the live overlay draws the
  // same shapes the CV pipeline is attributing occupancy to.
  const [activeZones, setActiveZones] = useState([]);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    supabase.auth.getUser().then(({ data }) => {
      if (active && data?.user) {
        setUser({
          email: data.user.email,
          fullName: data.user.user_metadata?.full_name ?? null,
        });
      }
    });
    // Resolved on the server so the role the UI draws from is the same one the
    // Server Actions re-check against.
    getViewerRole().then((r) => {
      if (!active) return;
      setRole(r?.role ?? null);
      setOrg({
        name: r?.orgName ?? null,
        plan: r?.plan ?? null,
        planSelectedAt: r?.planSelectedAt ?? null,
      });
    });
    return () => { active = false; };
  }, []);

  const loadOverview = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setOverview((s) => ({ ...s, status: 'loading' }));
    else setRefreshing(true);

    try {
      // Reads Postgres `zone_minute_stats` through the caller's own session, so
      // RLS scopes it — there is no orgId filter in the query, and none is
      // needed. See app/lib/analytics/queries.js.
      const data = await getOverview(rangeDays);
      if (data?.error) throw new Error(data.error);
      setOverview({ status: 'ready', data, error: null });
    } catch (err) {
      setOverview((s) => ({
        ...s,
        status: 'error',
        error: String(err?.message || err),
      }));
    } finally {
      setRefreshing(false);
    }
  }, [rangeDays]);

  // The org's real data extent, so the range picker can disable windows the
  // data cannot fill rather than rendering an empty chart.
  useEffect(() => {
    let active = true;
    getDataCoverage().then((c) => { if (active) setCoverage(c); }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadOverview();
    const timer = setInterval(() => {
      if (!cancelled) loadOverview({ silent: true });
    }, REFRESH_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [loadOverview]);

  const refreshButton = (
    <button
      type="button"
      onClick={() => loadOverview({ silent: true })}
      disabled={refreshing}
      className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-[12.5px] font-bold text-ink-muted hover:text-ink hover:border-field transition-colors disabled:opacity-50"
    >
      {refreshing
        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
        : <RefreshCw className="w-3.5 h-3.5" />}
      Refresh
    </button>
  );

  const rangeLabel = RANGE_LABELS[rangeDays] ?? `last ${rangeDays} days`;

  // Range picker and refresh travel together — both change what the panel
  // below is showing, so separating them would leave the reader hunting.
  const overviewActions = (
    <div className="flex items-center gap-2 flex-wrap">
      <RangePicker
        value={rangeDays}
        onChange={setRangeDays}
        coverage={coverage}
        busy={refreshing}
      />
      {refreshButton}
    </div>
  );

  return (
    <DashboardShell view={view} onViewChange={setView} user={user} role={role}>
      {view === 'overview' && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Workspace overview"
            title={`Your space, ${rangeLabel}`}
            subtitle="Occupancy, posture and dwell time measured from the camera feed. No footage or identity is stored."
            action={overviewActions}
          />

          <OverviewSection
            data={overview.data}
            status={overview.status}
            error={overview.error}
            hours={rangeDays * 24}
          />

          {/* Alerts sit directly under the tiles: the tiles say what the space
              did, alerts say what needs doing about it. Anything further down
              would be below the fold on a laptop. */}
          <AlertsPanel role={role} />

          {/* Trends sit below the headline numbers: the tiles answer "what is
              true now", the charts answer "how did it get there". */}
          <AnalyticsCharts days={rangeDays} />
        </div>
      )}

      {view === 'live' && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Live feed"
            title="Camera & detection"
            subtitle={
              can(role, 'analysis.run')
                ? 'Upload a recording or start the camera. Detection, tracking and posture run on your own hardware.'
                : 'Live detection and posture, as recorded by your team. Starting an analysis writes new measurements, which your role does not permit.'
            }
          />

          {!can(role, 'analysis.run') && role && <ReadOnlyNotice capability="analysis.run" />}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-5 items-start">
            <VideoCanvasPlayer activeZones={activeZones} readOnly={!can(role, 'analysis.run')} cameraId={CAMERA_ID} />
            <FloorplanHeatmap />
          </div>
        </div>
      )}

      {view === 'zones' && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Zones"
            title="Define your spaces"
            subtitle={
              can(role, 'zones.edit')
                ? 'Draw the areas you want measured. Occupancy and dwell time are attributed to whichever zone a person is standing in.'
                : 'The areas being measured. Occupancy and dwell time are attributed to whichever zone a person is standing in.'
            }
          />

          {!can(role, 'zones.edit') && role && <ReadOnlyNotice capability="zones.edit" />}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] gap-5 items-start">
            <ZoneEditor cameraId={CAMERA_ID} onZonesChanged={setActiveZones} readOnly={!can(role, 'zones.edit')} />
            <FloorplanHeatmap />
          </div>
        </div>
      )}

      {/* GATED, not merely unlinked. Hiding the sidebar entry stops a MANAGER
          being OFFERED the plan screen; it does nothing about `?view=plan`
          typed directly, and an unguarded view would render the upgrade cards
          to someone every one of whose clicks `change_plan()` would refuse.
          `role` is null until it resolves, so this reads false during load and
          the panel never flashes in before disappearing. */}
      {view === 'plan' && can(role, 'org.settings') && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Plan"
            title="Your subscription"
            subtitle="Your current subscription, what it includes, and how the tiers compare."
          />

          {/* `plan` is null until getViewerRole resolves. Rendering the panel
              with a null tier would flash "Unknown plan" before settling, so
              the section waits — the same reason every capability check reads
              false while `role` is null. */}
          {org.plan ? (
            <PlanSection
              plan={org.plan}
              orgName={org.name}
              canManage={can(role, 'org.settings')}
            />
          ) : (
            /* `min-h` roughly matches the resolved panel, so the page does not
               collapse to a thin strip and then jump when the plan arrives. */
            <div className="glass-panel p-6 flex items-center justify-center gap-3 min-h-[220px]">
              <Loader2 className="w-4 h-4 animate-spin text-ink-faint" aria-hidden="true" />
              <span className="text-[13px] font-medium text-ink-muted">
                Loading your plan…
              </span>
            </div>
          )}
        </div>
      )}

      {/* The other half of the gate: a non-admin who reached ?view=plan gets an
          explanation rather than an empty column. Only rendered once `role` is
          known, so it does not flash for an admin mid-load. */}
      {view === 'plan' && role && !can(role, 'org.settings') && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Plan"
            title="Your subscription"
            subtitle="Your current subscription, what it includes, and how the tiers compare."
          />
          <ReadOnlyNotice capability="org.settings" />
        </div>
      )}

      {view === 'reports' && (
        <div className="space-y-6">
          <PageHeader
            eyebrow="Reports"
            title="Export activity"
            subtitle="Download recorded telemetry as raw data or an executive summary. Exports contain counts and postures only."
            action={
              <RangePicker
                value={rangeDays}
                onChange={setRangeDays}
                coverage={coverage}
                busy={refreshing}
              />
            }
          />

          {/* Two columns rather than a lone `max-w-xl` card stranded in a wide
              page. The export control is the action; the panel beside it says
              what an export actually contains — which is the question a
              facilities manager asks before sending one to their director, and
              it was previously answerable only by downloading one. */}
          {/* `items-stretch`, not `items-start`. The two cards hold different
              amounts of text, and letting each size to its own content left the
              left one stopping well short of the right — a visibly ragged
              bottom edge. Stretching makes them one row. */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-stretch">
            <ReportExport hours={rangeDays * 24} rangeLabel={rangeLabel} />

            <div className="glass-panel p-5 flex flex-col gap-4 h-full">
              <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
                <ShieldCheck className="w-4 h-4 text-accent" aria-hidden="true" />
                What an export contains
              </div>

              <dl className="flex flex-col gap-3">
                {[
                  ['Per-zone occupancy', 'Headcount per zone per minute, across the selected window.'],
                  ['Posture breakdown', 'Sitting, standing and walking totals — as counts, never per person.'],
                  ['Dwell time', 'How long the zone was occupied, aggregated per minute bucket.'],
                ].map(([term, detail]) => (
                  <div key={term} className="flex gap-3">
                    <Check className="w-4 h-4 text-accent shrink-0 mt-0.5" strokeWidth={3} aria-hidden="true" />
                    <div className="min-w-0">
                      <dt className="text-[12.5px] font-bold text-ink">{term}</dt>
                      <dd className="text-[12px] text-ink-muted leading-relaxed mt-0.5">{detail}</dd>
                    </div>
                  </div>
                ))}
              </dl>

              {/* The anonymity guarantee is structural, not a policy promise —
                  `zone_minute_stats` holds no person reference at all, so
                  there is nothing identifying that an export COULD leak. */}
              <div className="rounded-lg border border-line bg-surface-alt px-3.5 py-3 mt-auto">
                <p className="text-[11.5px] text-ink-faint leading-relaxed">
                  <strong className="text-ink-muted font-bold">Nothing identifying is exported.</strong>{' '}
                  The underlying table holds counts per zone per minute and carries no track id,
                  coordinate or person reference — so an export physically cannot contain one.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </DashboardShell>
  );
}

/**
 * `useSearchParams()` opts its subtree into a client-side rendering bailout,
 * and Next requires that to sit inside a Suspense boundary — without one the
 * build fails outright. Same pattern as app/login/page.jsx.
 *
 * The fallback is the page ground rather than a spinner: this resolves in the
 * same tick, and a spinner that flashes for one frame reads as jank.
 */
export default function Dashboard() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-ground" />}>
      <DashboardInner />
    </Suspense>
  );
}
