'use client';

// frontend/app/dashboard/page.jsx
//
// The safety officer's workspace.
//
// Four sections behind one sidebar, matching the founder console's shell so the
// customer and operator halves of the product read as one application:
//
//   Overview   what needs a person first, and how the site did — the home screen
//   Live feed  the camera, with detection and tracking overlays
//   Zones      define the areas that rules and measurements attach to
//   Reports    the safety report for a period, as a PDF or CSV
//
// Sections are view state rather than routes on purpose: they share one polling
// data source and a live video WebSocket, and routing between them would tear
// the socket down and re-fetch everything on every click.

import React, { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { RefreshCw, Loader2, Eye } from 'lucide-react';

import DashboardShell, { VIEWS } from './DashboardShell';
import SafetyAlertsPanel from './SafetyAlertsPanel';
import ReviewQueue from './safety/ReviewQueue';
import EventsScreen from './safety/EventsScreen';
import RulesScreen from './safety/RulesScreen';
import ActionsScreen from './safety/ActionsScreen';
import SafetySummary from './safety/SafetySummary';
import ReportsScreen from './safety/ReportsScreen';
import EvaluationScreen from './evaluation/EvaluationScreen';
import PlanSection from './PlanSection';
import CamerasScreen from './CamerasScreen';
import { Skeleton } from './safety/common';
import { listCameras } from '../lib/platform/cameras';
import { VideoCanvasPlayer } from '../components/VideoCanvasPlayer';
import { FloorplanHeatmap } from '../components/FloorplanHeatmap';
import { ZoneEditor } from '../components/ZoneEditor';
import { supabase } from '../lib/supabase/browser';
import { backendFetch } from '../lib/backend';
import { getDashboardStart } from '../lib/session';
import { getBadgeCounts } from '../lib/safety/queries';
import RangePicker from './RangePicker';
import { can, denialMessage } from '../lib/permissions';

// Must match a camera actually registered for this org — both in the local
// pipeline registry (backend/app/api/routers/cameras.py) and by NAME in
// Postgres `cameras` (see minute_aggregator._resolve_ids), or telemetry is
// captured but never syncs to the dashboard's data source.
const CAMERA_ID = 'floor5';
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

  const [refreshing, setRefreshing] = useState(false);
  const [summaryTick, setSummaryTick] = useState(0);        // bumped to make the safety summary read again
  // Open alerts: potential violations still waiting for review (Postgres, through the caller's RLS).
  const [alerts, setAlerts] = useState({ status: 'loading', count: 0, alerts: [], error: null });
  // The window the overview summarises is the viewer's choice.
  const [rangeDays, setRangeDays] = useState(7);

  // WHICH CAMERA EVERYTHING ON THIS PAGE IS ABOUT.
  //
  // This was the hardcoded constant CAMERA_ID. One camera name, baked in, so
  // a zone drawn for the door camera and a zone drawn for the desk camera
  // landed in the same bucket and the pipeline attributed both to whichever
  // it happened to be running. Zones ARE per-camera — a polygon is a region
  // of one camera's frame and means nothing in another's — so the camera has
  // to be a choice, and a visible one.
  const [cameraId, setCameraId] = useState(CAMERA_ID);
  const [cameras, setCameras] = useState([]);

  // Zones for the SELECTED camera, loaded here rather than by the editor.
  //
  // They used to be handed up by ZoneEditor's onZonesChanged, which meant the
  // live overlay drew nothing until you had visited the Zones page in that
  // session. The overlay is the whole point of drawing them; it should not
  // depend on which tab you opened first.
  const [activeZones, setActiveZones] = useState([]);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    // Only the name and e-mail for the sidebar, so the browser's own session is enough: getSession() reads it
    // locally, where getUser() was a round trip to the auth server on every dashboard load. Nothing is decided
    // by this value (the layout verified the session on the server; every read and action re-checks).
    supabase.auth.getSession().then(({ data }) => {
      const u = data?.session?.user;
      if (active && u) {
        setUser({
          email: u.email,
          fullName: u.user_metadata?.full_name ?? null,
        });
      }
    });
    return () => { active = false; };
  }, []);

  const alertsRequest = useRef(0);
  const [overdue, setOverdue] = useState(0);
  const applyBadges = useCallback((r) => {
    setOverdue(r?.overdue || 0);
    if (r?.error) {
      // Keep the last good count and list: one failed poll must not blank the badge.
      setAlerts((s) => ({ ...s, status: 'error', error: String(r.error) }));
      return;
    }
    setAlerts({ status: 'ready', count: r?.count ?? 0, alerts: r?.alerts ?? [], error: null });
  }, []);
  const loadAlerts = useCallback(async () => {
    try {
      const mine = ++alertsRequest.current;
      // One server call for both badges (Next.js runs a page's server actions one at a time).
      const r = await getBadgeCounts(new Date().toLocaleDateString("en-CA"));
      if (mine !== alertsRequest.current) return;            // an older reply that arrived late
      applyBadges(r);
    } catch (err) {
      setAlerts((s) => ({ ...s, status: 'error', error: String(err?.message || err) }));
    }
  }, [applyBadges]);

  // The badge and the list follow the same slow poll as the overview: a flag is synced to
  // Postgres within a minute of being raised (and at the end of a video). The FIRST read comes
  // with the opening call below, so the poll starts one interval later.
  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(() => { if (!cancelled && !document.hidden) loadAlerts(); }, REFRESH_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [loadAlerts]);

  // The cameras this org has registered in Postgres. That registry is the one
  // that matters: decisions, zone events and zones reach the dashboard only when
  // the camera NAME a run uses matches one of these rows (safety_sync,
  // zones._mirror_zone_to_postgres). It used to read the backend's local SQLite
  // list, which nothing in the product ever filled, so the page always ran as
  // the unregistered default and nothing synced.
  const [camerasStatus, setCamerasStatus] = useState({ status: 'loading', error: null });
  const applyCameras = useCallback((r, prefer) => {
    if (r?.error) { setCamerasStatus({ status: 'error', error: String(r.error) }); return; }
    const list = Array.isArray(r?.cameras) ? r.cameras : [];
    setCameras(list);
    setCamerasStatus({ status: 'ready', error: null });
    const names = list.map((c) => c.name);
    // Keep the camera being worked on; otherwise the one just added, or the first.
    setCameraId((current) => (prefer && names.includes(prefer) ? prefer
      : names.includes(current) ? current : (names[0] ?? CAMERA_ID)));
  }, []);
  const loadCameras = useCallback(async (prefer) => {
    try {
      applyCameras(await listCameras(), prefer);
    } catch (err) {
      setCamerasStatus({ status: 'error', error: String(err?.message || err) });
    }
  }, [applyCameras]);

  // OPENING: role and organisation, cameras and both badges in ONE server call (getDashboardStart). They
  // used to be three separate server actions, which Next.js runs one after another, so the Overview waited
  // for all three in a row.
  useEffect(() => {
    let active = true;
    getDashboardStart(new Date().toLocaleDateString("en-CA")).then((r) => {
      if (!active || !r) return;
      // Resolved on the server so the role the UI draws from is the same one the
      // Server Actions re-check against.
      setRole(r.viewer?.role ?? null);
      setOrg({
        name: r.viewer?.orgName ?? null,
        plan: r.viewer?.plan ?? null,
        planSelectedAt: r.viewer?.planSelectedAt ?? null,
      });
      applyCameras(r.cameras);
      applyBadges(r.badges);
    }).catch((err) => {
      if (!active) return;
      setCamerasStatus({ status: 'error', error: String(err?.message || err) });
      setAlerts((s) => ({ ...s, status: 'error', error: String(err?.message || err) }));
    });
    return () => { active = false; };
  }, [applyCameras, applyBadges]);

  // Zones for the selected camera. Loaded HERE, not in the editor, so the live
  // overlay has them whether or not the Zones page has been opened — and so
  // changing camera reloads them rather than showing the previous camera's
  // shapes over a different view.
  const loadZones = useCallback(async () => {
    if (!cameraId) return;
    try {
      const res = await backendFetch(`/api/v1/zones/${encodeURIComponent(cameraId)}`);
      if (!res.ok) return;
      const data = await res.json();
      setActiveZones(Array.isArray(data) ? data : []);
    } catch {
      // Non-fatal: the overlay simply has nothing to draw.
    }
  }, [cameraId]);

  useEffect(() => { loadZones(); }, [loadZones]);

  /**
   * Which camera the live feed and the zone editor are both about.
   *
   * Rendered on BOTH views deliberately. They are two halves of configuring
   * one camera — draw the zones, then watch them fill — and a selector that
   * appeared on only one of them would let the two drift apart without
   * anybody noticing which camera they were looking at.
   */
  const cameraPicker = cameras.length > 0 ? (
    <label className="flex items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
        camera
      </span>
      <select
        value={cameraId}
        onChange={(e) => setCameraId(e.target.value)}
        className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12.5px] font-bold text-ink focus:outline-none focus:border-[color:var(--accent)] transition-colors"
      >
        {cameras.map((c) => (
          <option key={c.id} value={c.name}>{c.name}</option>
        ))}
      </select>
    </label>
  ) : null;

  // Shown on Live feed and Zones while the organisation has no camera: runs
  // still work, but nothing they decide can reach the dashboard.
  const noCameraNotice = camerasStatus.status === 'ready' && cameras.length === 0 ? (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3">
      <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed max-w-2xl">
        <strong className="text-ink">No camera is registered.</strong> Detection still runs, but its decisions stay on
        this computer and never reach Review, Events, Reports or the Overview.
      </p>
      <button
        type="button"
        onClick={() => setView('cameras')}
        className="rounded-lg bg-accent px-3.5 py-2 text-[12.5px] font-bold text-white hover:brightness-110 transition"
      >
        {can(role, 'cameras.edit') ? 'Add a camera' : 'See cameras'}
      </button>
    </div>
  ) : null;

  // Refresh: read the waiting flags and the safety summary again.
  const refreshOverview = async () => {
    setRefreshing(true);
    setSummaryTick((t) => t + 1);
    try { await loadAlerts(); } finally { setRefreshing(false); }
  };

  const refreshButton = (
    <button
      type="button"
      onClick={refreshOverview}
      disabled={refreshing}
      aria-label="Refresh"
      className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-[12.5px] font-bold text-ink-muted hover:text-ink hover:border-field transition-colors disabled:opacity-50"
    >
      {refreshing
        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
        : <RefreshCw className="w-3.5 h-3.5" />}
      {/* Icon only on a phone, so the range picker and this fit one row without spilling past the edge. */}
      <span className="hidden sm:inline">Refresh</span>
    </button>
  );

  const rangeLabel = RANGE_LABELS[rangeDays] ?? `last ${rangeDays} days`;

  // Range picker and refresh travel together — both change what the panel
  // below is showing, so separating them would leave the reader hunting.
  const overviewActions = (
    <div className="flex items-center gap-2 flex-wrap">
      <RangePicker value={rangeDays} onChange={setRangeDays} busy={refreshing} />
      {refreshButton}
    </div>
  );

  return (
    <DashboardShell view={view} onViewChange={setView} user={user} role={role} alertCount={alerts.count} overdueCount={overdue}>
      {view === 'overview' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Site overview"
            title={`Site safety, ${rangeLabel}`}
            subtitle="What needs a person first, then how the site did."
            action={overviewActions}
          />

          {/* What needs a person first; the numbers follow. */}
          <SafetyAlertsPanel status={alerts.status} count={alerts.count} alerts={alerts.alerts} error={alerts.error} onReview={() => setView('review')} />

          <SafetySummary days={rangeDays} refreshKey={`${alerts.count}-${summaryTick}`} />
        </div>
      )}

      {/* KEPT MOUNTED, NOT CONDITIONALLY RENDERED.
          
          `{view === 'live' && <VideoCanvasPlayer/>}` unmounts the player the
          moment you click another section, and the unmount cleanup stops the
          webcam tracks and closes the WebSocket — by design, because a camera
          left running after you navigate away is worse. But that meant a
          five-minute upload was destroyed by a glance at the Zones page, and
          the camera light went out with it.
          
          Hiding it with CSS keeps the component alive: the stream, the
          socket and the processed frames all survive a trip to Zones and
          back. The cleanup still fires when the DASHBOARD unmounts, so
          leaving for another page or signing out still releases the
          camera properly. */}
      <div className={view === 'live' ? 'block animate-view-in' : 'hidden'} aria-hidden={view !== 'live'}>
        <div className="space-y-6">
          <PageHeader
            eyebrow="Live feed"
            title="Camera & detection"
            subtitle={
              can(role, 'analysis.run')
                ? 'Upload a recording or start the camera. This camera’s zones are outlined on the frame.'
                : 'Live detection for your organisation. Your role can watch but not start a run.'
            }
            action={cameraPicker}
          />

          {noCameraNotice}
          {!can(role, 'analysis.run') && role && <ReadOnlyNotice capability="analysis.run" />}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-5 items-start">
            <VideoCanvasPlayer
              activeZones={activeZones}
              readOnly={!can(role, 'analysis.run')}
              cameraId={cameraId}
            />
            <FloorplanHeatmap />
          </div>
        </div>
      </div>

      {view === 'review' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader eyebrow="Review" title="Flags waiting for a person" subtitle="Look at the evidence, then confirm or dismiss each flag." />
          <ReviewQueue role={role} onCountChange={loadAlerts} />
        </div>
      )}

      {view === 'events' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader eyebrow="Events" title="What the safety layer decided and saw" subtitle="Every decision and zone visit, with filters." />
          <EventsScreen role={role} />
        </div>
      )}

      {view === 'rules' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader eyebrow="Rules" title="What gets checked" subtitle="What is checked, where, and how serious it is." />
          <RulesScreen role={role} />
        </div>
      )}

      {view === 'actions' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader eyebrow="Actions" title="What was done about confirmed violations" subtitle="Who is doing what, by when, and whether it is overdue." />
          <ActionsScreen role={role} onCountChange={loadAlerts} />
        </div>
      )}

      {view === 'cameras' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Cameras"
            title="Your cameras"
            subtitle="Each camera has its own zones and history. Only registered cameras reach the dashboard."
          />
          <CamerasScreen
            role={role}
            cameras={cameras}
            status={camerasStatus.status}
            error={camerasStatus.error}
            selected={cameras.length ? cameraId : null}
            onSelect={setCameraId}
            onChanged={loadCameras}
          />
        </div>
      )}

      {view === 'zones' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Zones"
            title="Define your zones"
            subtitle={
              can(role, 'zones.edit')
                ? 'Draw the areas to monitor on this camera’s view.'
                : 'The areas being monitored on this camera.'
            }
            action={cameraPicker}
          />

          {noCameraNotice}
          {!can(role, 'zones.edit') && role && <ReadOnlyNotice capability="zones.edit" />}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] gap-5 items-start">
            <ZoneEditor
              cameraId={cameraId}
              onZonesChanged={setActiveZones}
              readOnly={!can(role, 'zones.edit')}
            />
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
        <div className="space-y-6 animate-view-in">
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
            <div className="glass-panel p-6 min-h-[220px]">
              <Skeleton rows={5} label="Loading your plan" />
            </div>
          )}
        </div>
      )}

      {/* The other half of the gate: a non-admin who reached ?view=plan gets an
          explanation rather than an empty column. Only rendered once `role` is
          known, so it does not flash for an admin mid-load. */}
      {view === 'plan' && role && !can(role, 'org.settings') && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Plan"
            title="Your subscription"
            subtitle="Your current subscription, what it includes, and how the tiers compare."
          />
          <ReadOnlyNotice capability="org.settings" />
        </div>
      )}

      {view === 'evaluation' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Evaluation"
            title="How well the system works, measured"
            subtitle="The measured results: accuracy, scenarios, speed and your reviews."
          />
          <EvaluationScreen />
        </div>
      )}

      {view === 'reports' && (
        <div className="space-y-6 animate-view-in">
          <PageHeader
            eyebrow="Reports"
            title="Safety reports"
            subtitle="How the site did over a period, as a PDF or CSV."
          />
          <ReportsScreen />
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
