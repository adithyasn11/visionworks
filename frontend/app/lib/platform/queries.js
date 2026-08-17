// frontend/app/lib/platform/queries.js
//
// Server-side data access for the founder console.
//
// Every query goes through the caller's own Supabase session, so RLS applies.
// That is deliberate: if the operator check above these functions ever broke,
// the queries would return nothing rather than leaking customer data. The
// console is not trusted to filter — the database does it.
//
// The privacy boundary lives in the schema, not here. There is no query in this
// file that touches zone_minute_stats, zone_day_stats, alerts or reports,
// because no RLS policy grants a platform operator access to them. Adding one
// would return zero rows anyway.

import { createClient } from '../supabase/server';

/**
 * Coerce a Postgres count to a JS number.
 *
 * The view's count(*) columns arrive as BigInt over this driver, which
 * JSON.stringify refuses to serialise — passing a raw row into a Client
 * Component throws "Do not know how to serialize a BigInt" at the boundary.
 * Every numeric field is funnelled through here so nothing BigInt-shaped can
 * reach the client.
 */
const n = (v) => (v == null ? 0 : Number(v));

/**
 * Single mapping from a platform_org_overview row to the shape the UI uses.
 *
 * Shared by the overview and the organisations list on purpose: two hand-written
 * mappings of the same view drift, and the bug surfaces as one page showing a
 * different camera count from the other.
 */
function mapOrgRow(o) {
  return {
    id: o.id,
    name: o.name,
    slug: o.slug,
    timezone: o.timezone,
    retentionDays: n(o.dataRetentionDays),
    createdAt: o.createdAt,
    isSuspended: Boolean(o.isSuspended),
    activeMembers: n(o.activeMembers),
    pendingInvites: n(o.pendingInvites),
    siteCount: n(o.siteCount),
    cameraCount: n(o.cameraCount),
    camerasInError: n(o.camerasInError),
    zoneCount: n(o.zoneCount),
    failedSessions: n(o.failedSessions),
    runningSessions: n(o.runningSessions),
    // Dates are serialised to ISO strings so they cross the server/client
    // boundary as primitives rather than Date objects.
    lastSuccessfulRun: o.lastSuccessfulRun ?? null,
    needsOnboardingHelp: Boolean(o.needsOnboardingHelp),
  };
}

/**
 * Everything the overview page needs, in one place.
 *
 * Returns a plain object rather than throwing on failure: a support console that
 * 500s because one count failed is worse than one that renders with a visible
 * error strip. `error` is surfaced in the UI.
 */
export async function getPlatformOverview() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured.', orgs: [], stats: null, signups: [] };
  }

  // platform_org_overview is a security_invoker view, so this is RLS-scoped to
  // the caller. For an operator it returns every org; for anyone else, theirs.
  const { data: rows, error } = await supabase
    .from('platform_org_overview')
    .select('*')
    .order('createdAt', { ascending: false });

  if (error) {
    return { error: error.message, orgs: [], stats: null, signups: [] };
  }

  const orgs = (rows ?? []).map(mapOrgRow);

  const active = orgs.filter((o) => !o.isSuspended);

  const stats = {
    totalOrgs: orgs.length,
    activeOrgs: active.length,
    suspendedOrgs: orgs.length - active.length,
    totalMembers: orgs.reduce((a, o) => a + o.activeMembers, 0),
    pendingInvites: orgs.reduce((a, o) => a + o.pendingInvites, 0),
    totalSites: orgs.reduce((a, o) => a + o.siteCount, 0),
    totalCameras: orgs.reduce((a, o) => a + o.cameraCount, 0),
    totalZones: orgs.reduce((a, o) => a + o.zoneCount, 0),
    camerasInError: orgs.reduce((a, o) => a + o.camerasInError, 0),
    failedSessions: orgs.reduce((a, o) => a + o.failedSessions, 0),
    runningSessions: orgs.reduce((a, o) => a + o.runningSessions, 0),
  };

  return { error: null, orgs, stats, signups: buildSignupSeries(orgs, 30) };
}

/**
 * Daily signup counts for the last `days` days, oldest first.
 *
 * Built in JS from the org list rather than with a SQL date_trunc + generate_
 * series, because the org count is small (tens, not millions) and this keeps
 * every gap-day present at zero — a chart with missing days lies about its
 * shape.
 */
function buildSignupSeries(orgs, days) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const buckets = new Map();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    buckets.set(d.toISOString().slice(0, 10), 0);
  }

  for (const o of orgs) {
    if (!o.createdAt) continue;
    const key = new Date(o.createdAt).toISOString().slice(0, 10);
    if (buckets.has(key)) buckets.set(key, buckets.get(key) + 1);
  }

  let cumulative = 0;
  // Orgs that existed before the window still count toward the running total,
  // otherwise the cumulative line starts at zero and understates the platform.
  const windowStart = [...buckets.keys()][0];
  for (const o of orgs) {
    if (o.createdAt && new Date(o.createdAt).toISOString().slice(0, 10) < windowStart) {
      cumulative += 1;
    }
  }

  return [...buckets.entries()].map(([date, count]) => {
    cumulative += count;
    return { date, count, total: cumulative };
  });
}

/**
 * Orgs that need a human to look at them, most urgent first.
 *
 * The ranking is the whole value of this list — a support queue sorted by
 * signup date is useless. Weights reflect what actually blocks a customer:
 * a broken camera stops data arriving at all, whereas a pending invite is
 * merely untidy.
 */
export function buildAttentionList(orgs) {
  const items = [];

  for (const o of orgs) {
    const reasons = [];
    let severity = 0;

    if (o.camerasInError > 0) {
      reasons.push({
        kind: 'camera_error',
        label: `${o.camerasInError} camera${o.camerasInError > 1 ? 's' : ''} in error`,
        weight: 100,
      });
      severity = Math.max(severity, 3);
    }

    if (o.failedSessions > 0) {
      reasons.push({
        kind: 'failed_session',
        label: `${o.failedSessions} failed run${o.failedSessions > 1 ? 's' : ''}`,
        weight: 80,
      });
      severity = Math.max(severity, 2);
    }

    // Cameras but no zones: the pipeline runs and produces nothing, because
    // every detection falls outside every zone. Silent and total.
    if (o.needsOnboardingHelp) {
      reasons.push({
        kind: 'no_zones',
        label: 'cameras configured but no zones drawn',
        weight: 70,
      });
      severity = Math.max(severity, 2);
    }

    // Signed up, never configured anything.
    if (o.cameraCount === 0) {
      reasons.push({ kind: 'no_cameras', label: 'no cameras added yet', weight: 40 });
      severity = Math.max(severity, 1);
    }

    if (o.activeMembers === 0) {
      reasons.push({ kind: 'no_members', label: 'no active members', weight: 30 });
      severity = Math.max(severity, 1);
    }

    if (reasons.length > 0) {
      items.push({
        org: o,
        reasons,
        severity,
        score: reasons.reduce((a, r) => a + r.weight, 0),
      });
    }
  }

  return items.sort((a, b) => b.score - a.score);
}

/* ── Organisations list ─────────────────────────────────────────────────── */

/**
 * Sort keys the list supports, mapped to a comparator.
 *
 * Sorting happens in JS rather than SQL because the view's derived columns
 * (needsOnboardingHelp, the health counts) are computed per row and PostgREST
 * cannot order by a client-supplied expression safely. The org count is in the
 * tens — if it ever reaches thousands this moves into the view with a proper
 * index, but paginating twenty rows would be premature.
 */
const SORTERS = {
  newest:    (a, b) => new Date(b.createdAt) - new Date(a.createdAt),
  oldest:    (a, b) => new Date(a.createdAt) - new Date(b.createdAt),
  name:      (a, b) => a.name.localeCompare(b.name),
  members:   (a, b) => b.activeMembers - a.activeMembers,
  cameras:   (a, b) => b.cameraCount - a.cameraCount,
  zones:     (a, b) => b.zoneCount - a.zoneCount,
  // Most broken first — the default when filtering by "needs attention".
  health:    (a, b) => healthScore(b) - healthScore(a),
};

export const SORT_KEYS = Object.keys(SORTERS);

/** Higher = worse. Mirrors the weights in buildAttentionList. */
function healthScore(o) {
  return o.camerasInError * 100
    + o.failedSessions * 80
    + (o.needsOnboardingHelp ? 70 : 0)
    + (o.cameraCount === 0 ? 40 : 0)
    + (o.activeMembers === 0 ? 30 : 0);
}

/**
 * A compact health verdict per org, used for the status column.
 *
 * Returns the single most important thing wrong, not a list — a table cell has
 * room for one fact, and the detail page has room for all of them.
 */
export function orgHealth(o) {
  if (o.isSuspended) return { level: 'suspended', label: 'Suspended' };
  if (o.camerasInError > 0) {
    return { level: 'error', label: `${o.camerasInError} camera${o.camerasInError > 1 ? 's' : ''} down` };
  }
  if (o.failedSessions > 0) {
    return { level: 'error', label: `${o.failedSessions} failed run${o.failedSessions > 1 ? 's' : ''}` };
  }
  if (o.needsOnboardingHelp) return { level: 'warn', label: 'No zones drawn' };
  if (o.cameraCount === 0) return { level: 'warn', label: 'No cameras' };
  if (o.activeMembers === 0) return { level: 'warn', label: 'No members' };
  if (o.runningSessions > 0) return { level: 'live', label: 'Processing' };
  return { level: 'ok', label: 'Healthy' };
}

/**
 * The organisations list, filtered and sorted.
 *
 * @param opts.q       free-text match on name or slug
 * @param opts.filter  'all' | 'active' | 'suspended' | 'attention'
 * @param opts.sort    one of SORT_KEYS
 */
export async function getOrganisations({ q = '', filter = 'all', sort = 'newest' } = {}) {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured.', orgs: [], counts: emptyCounts() };
  }

  const { data: rows, error } = await supabase
    .from('platform_org_overview')
    .select('*');

  if (error) {
    return { error: error.message, orgs: [], counts: emptyCounts() };
  }

  const all = (rows ?? []).map(mapOrgRow);

  // Counts describe the UNFILTERED set, so the filter chips always show how
  // many rows each option would produce rather than counting what is already
  // on screen.
  const counts = {
    all: all.length,
    active: all.filter((o) => !o.isSuspended).length,
    suspended: all.filter((o) => o.isSuspended).length,
    attention: all.filter((o) => healthScore(o) > 0).length,
  };

  let orgs = all;

  if (filter === 'active') orgs = orgs.filter((o) => !o.isSuspended);
  else if (filter === 'suspended') orgs = orgs.filter((o) => o.isSuspended);
  else if (filter === 'attention') orgs = orgs.filter((o) => healthScore(o) > 0);

  const needle = q.trim().toLowerCase();
  if (needle) {
    orgs = orgs.filter(
      (o) =>
        o.name.toLowerCase().includes(needle) ||
        o.slug.toLowerCase().includes(needle),
    );
  }

  const sorter = SORTERS[sort] ?? SORTERS.newest;
  orgs = [...orgs].sort(sorter);

  return { error: null, orgs, counts, total: all.length };
}

function emptyCounts() {
  return { all: 0, active: 0, suspended: 0, attention: 0 };
}

/* ── Organisation detail ────────────────────────────────────────────────── */

/**
 * Everything the detail page shows for one organisation.
 *
 * Six queries in parallel rather than one nested select, because PostgREST's
 * embedded-resource syntax cannot express the joins here (memberships → profiles
 * crosses an RLS boundary) and a failure in one section should not blank the
 * whole page.
 *
 * WHAT IS DELIBERATELY NOT FETCHED: zone_minute_stats, zone_day_stats, alerts,
 * alert_rules, reports, audit_logs. Not because the UI has no room — because a
 * platform operator holds no RLS grant on them. Asking would return an empty
 * array and imply the customer has no data, which is worse than not asking.
 */
export async function getOrganisationDetail(orgId) {
  const supabase = createClient();
  if (!supabase) return { error: 'Supabase is not configured.', org: null };

  const { data: orgRow, error: orgError } = await supabase
    .from('platform_org_overview')
    .select('*')
    .eq('id', orgId)
    .maybeSingle();

  // Unknown id and "not permitted to see it" are indistinguishable here, which
  // is correct: the caller gets notFound() either way rather than a 403 that
  // would confirm the organisation exists.
  if (orgError || !orgRow) return { error: orgError?.message ?? null, org: null };

  const [members, sites, cameras, zones, sessions] = await Promise.all([
    supabase
      .from('memberships')
      .select('id, role, status, invitedEmail, acceptedAt, createdAt, profileId')
      .eq('orgId', orgId)
      .order('createdAt', { ascending: true }),
    supabase
      .from('sites')
      .select('id, name, location, timezone, totalCapacity, workdayStartMinute, workdayEndMinute, workdays, createdAt')
      .eq('orgId', orgId)
      .is('deletedAt', null)
      .order('name'),
    // cameras_safe, never `cameras` — the view omits rtspUrl entirely, and
    // table-level SELECT on cameras is revoked from authenticated anyway.
    supabase
      .from('cameras_safe')
      .select('*')
      .eq('orgId', orgId)
      .order('name'),
    supabase
      .from('zones')
      .select('id, name, zoneType, capacity, colour, excludeFromUtilisation, cameraId, siteId, createdAt')
      .eq('orgId', orgId)
      .is('deletedAt', null)
      .order('name'),
    supabase
      .from('analysis_sessions')
      .select('id, kind, status, sourceFilename, totalFrames, processedFrames, fpsAchieved, durationSeconds, errorMessage, queuedAt, startedAt, finishedAt, cameraId')
      .eq('orgId', orgId)
      .order('queuedAt', { ascending: false })
      .limit(20),
  ]);

  // Member emails: a membership row carries invitedEmail, but an accepted member
  // may have signed up with a different display name, so the profile is the
  // better source when it is readable.
  const profileIds = (members.data ?? []).map((m) => m.profileId).filter(Boolean);
  let profileMap = new Map();
  if (profileIds.length > 0) {
    const { data: profiles } = await supabase
      .from('profiles')
      .select('id, email, fullName, jobTitle, lastSeenAt')
      .in('id', profileIds);
    profileMap = new Map((profiles ?? []).map((p) => [p.id, p]));
  }

  const cameraNames = new Map((cameras.data ?? []).map((c) => [c.id, c.name]));
  const siteNames = new Map((sites.data ?? []).map((s) => [s.id, s.name]));

  return {
    error: null,
    org: mapOrgRow(orgRow),
    members: (members.data ?? []).map((m) => {
      const p = m.profileId ? profileMap.get(m.profileId) : null;
      return {
        id: m.id,
        role: m.role,
        status: m.status,
        email: p?.email ?? m.invitedEmail,
        fullName: p?.fullName ?? null,
        jobTitle: p?.jobTitle ?? null,
        lastSeenAt: p?.lastSeenAt ?? null,
        joinedAt: m.acceptedAt ?? null,
        invitedAt: m.createdAt,
        isPending: m.status === 'INVITED',
      };
    }),
    sites: (sites.data ?? []).map((s) => ({
      id: s.id,
      name: s.name,
      location: s.location,
      timezone: s.timezone,
      capacity: s.totalCapacity == null ? null : n(s.totalCapacity),
      workdayStartMinute: n(s.workdayStartMinute),
      workdayEndMinute: n(s.workdayEndMinute),
      workdays: Array.isArray(s.workdays) ? s.workdays.map(Number) : [],
      createdAt: s.createdAt,
    })),
    cameras: (cameras.data ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      description: c.description,
      sourceType: c.sourceType,
      hasRtspUrl: Boolean(c.hasRtspUrl),
      deviceIndex: c.deviceIndex == null ? null : n(c.deviceIndex),
      fpsTarget: n(c.fpsTarget),
      frameWidth: c.frameWidth == null ? null : n(c.frameWidth),
      frameHeight: c.frameHeight == null ? null : n(c.frameHeight),
      isCalibrated: Boolean(c.isCalibrated),
      status: c.status,
      lastSeenAt: c.lastSeenAt,
      lastErrorMessage: c.lastErrorMessage,
      siteName: c.siteId ? siteNames.get(c.siteId) ?? null : null,
    })),
    zones: (zones.data ?? []).map((z) => ({
      id: z.id,
      name: z.name,
      zoneType: z.zoneType,
      capacity: z.capacity == null ? null : n(z.capacity),
      colour: z.colour,
      excludeFromUtilisation: Boolean(z.excludeFromUtilisation),
      cameraName: cameraNames.get(z.cameraId) ?? null,
      siteName: z.siteId ? siteNames.get(z.siteId) ?? null : null,
    })),
    sessions: (sessions.data ?? []).map((s) => ({
      id: s.id,
      kind: s.kind,
      status: s.status,
      sourceFilename: s.sourceFilename,
      totalFrames: s.totalFrames == null ? null : n(s.totalFrames),
      processedFrames: n(s.processedFrames),
      fpsAchieved: s.fpsAchieved == null ? null : Number(s.fpsAchieved),
      durationSeconds: s.durationSeconds == null ? null : n(s.durationSeconds),
      errorMessage: s.errorMessage,
      queuedAt: s.queuedAt,
      finishedAt: s.finishedAt,
      cameraName: s.cameraId ? cameraNames.get(s.cameraId) ?? null : null,
    })),
    // Surface per-section failures so a partial page is honest about what it
    // could not load, rather than rendering an empty table.
    sectionErrors: {
      members: members.error?.message ?? null,
      sites: sites.error?.message ?? null,
      cameras: cameras.error?.message ?? null,
      zones: zones.error?.message ?? null,
      sessions: sessions.error?.message ?? null,
    },
  };
}

/* ── Health / operations ────────────────────────────────────────────────── */

/**
 * Cross-organisation triage data.
 *
 * Five independent concerns, fetched in parallel. Each is returned with its own
 * error field rather than throwing, so one failing query leaves the other four
 * sections usable — on a triage page, partial information beats a 500.
 *
 * ORG NAMES ARE RESOLVED CLIENT-SIDE OF THE DATABASE, not with an embedded
 * PostgREST join. `cameras_safe` is a view and `organisations` is filtered by a
 * separate policy, so `select *, organisations(name)` cannot be relied on to
 * traverse that boundary. Fetching the org list once and joining in JS is both
 * correct and cheaper than N embedded selects.
 *
 * Still no occupancy anywhere: statuses, counts and error strings only.
 */
export async function getPlatformHealth() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured.', ...emptyHealth() };
  }

  const { data: orgRows, error: orgError } = await supabase
    .from('platform_org_overview')
    .select('*');

  if (orgError) {
    return { error: orgError.message, ...emptyHealth() };
  }

  const orgs = (orgRows ?? []).map(mapOrgRow);
  const orgName = new Map(orgs.map((o) => [o.id, o.name]));

  const [cameraRes, failedRes, runningRes] = await Promise.all([
    supabase
      .from('cameras_safe')
      .select('id, orgId, siteId, name, sourceType, status, lastSeenAt, lastErrorMessage, hasRtspUrl, fpsTarget')
      .eq('status', 'ERROR')
      .order('lastSeenAt', { ascending: false, nullsFirst: false }),
    supabase
      .from('analysis_sessions')
      .select('id, orgId, cameraId, kind, status, sourceFilename, totalFrames, processedFrames, errorMessage, queuedAt, finishedAt')
      // CANCELLED belongs here too: from a support view, a run that stopped
      // without finishing is the same question as one that errored.
      .in('status', ['ERROR', 'CANCELLED'])
      .order('queuedAt', { ascending: false })
      .limit(40),
    supabase
      .from('analysis_sessions')
      .select('id, orgId, cameraId, kind, status, sourceFilename, totalFrames, processedFrames, queuedAt, startedAt')
      .in('status', ['PROCESSING', 'QUEUED'])
      .order('queuedAt', { ascending: true }),
  ]);

  // Camera names for session attribution — one lookup rather than per-row.
  const cameraIds = [
    ...new Set(
      [...(failedRes.data ?? []), ...(runningRes.data ?? [])]
        .map((s) => s.cameraId)
        .filter(Boolean),
    ),
  ];
  let cameraName = new Map();
  if (cameraIds.length > 0) {
    const { data: cams } = await supabase
      .from('cameras_safe')
      .select('id, name')
      .in('id', cameraIds);
    cameraName = new Map((cams ?? []).map((c) => [c.id, c.name]));
  }

  const decorate = (s) => ({
    id: s.id,
    orgId: s.orgId,
    orgName: orgName.get(s.orgId) ?? 'Unknown organisation',
    cameraName: s.cameraId ? cameraName.get(s.cameraId) ?? null : null,
    kind: s.kind,
    status: s.status,
    sourceFilename: s.sourceFilename,
    totalFrames: s.totalFrames == null ? null : n(s.totalFrames),
    processedFrames: n(s.processedFrames),
    errorMessage: s.errorMessage ?? null,
    queuedAt: s.queuedAt,
    startedAt: s.startedAt ?? null,
    finishedAt: s.finishedAt ?? null,
  });

  // Onboarding stalls, derived from the org view rather than re-counted.
  const stuckNoZones = orgs
    .filter((o) => !o.isSuspended && o.cameraCount > 0 && o.zoneCount === 0)
    .sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));

  const neverStarted = orgs
    .filter((o) => !o.isSuspended && o.cameraCount === 0)
    .sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));

  return {
    error: null,
    camerasInError: (cameraRes.data ?? []).map((c) => ({
      id: c.id,
      orgId: c.orgId,
      orgName: orgName.get(c.orgId) ?? 'Unknown organisation',
      name: c.name,
      sourceType: c.sourceType,
      status: c.status,
      lastSeenAt: c.lastSeenAt,
      lastErrorMessage: c.lastErrorMessage,
      hasRtspUrl: Boolean(c.hasRtspUrl),
      fpsTarget: n(c.fpsTarget),
    })),
    failedSessions: (failedRes.data ?? []).map(decorate),
    runningSessions: (runningRes.data ?? []).map(decorate),
    stuckNoZones,
    neverStarted,
    orgCount: orgs.length,
    sectionErrors: {
      cameras: cameraRes.error?.message ?? null,
      failed: failedRes.error?.message ?? null,
      running: runningRes.error?.message ?? null,
    },
  };
}

function emptyHealth() {
  return {
    camerasInError: [],
    failedSessions: [],
    runningSessions: [],
    stuckNoZones: [],
    neverStarted: [],
    orgCount: 0,
    sectionErrors: { cameras: null, failed: null, running: null },
  };
}
