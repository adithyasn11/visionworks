'use server';

// frontend/app/lib/analytics/queries.js
//
// Dashboard analytics, read from Postgres `zone_minute_stats`.
//
// WHY THIS REPLACES THE FASTAPI CALLS
//
// The dashboard used to read SQLite through the Python backend. That worked,
// but it was per-installation rather than per-tenant, and it could only see
// whatever the local pipeline had recorded. Reading the bucket table instead
// gives the dashboard the whole tenant history — three months and 824k rows of
// it in this project — and makes the numbers the same ones the retention job,
// the day rollups and the alerts engine work from.
//
// TENANCY IS NOT WRITTEN HERE
//
// There is no `WHERE orgId = ?` in this file, and that is the point. Every
// query runs through the caller's own Supabase session, so `zms_select`
// (`orgId IN (SELECT user_org_ids())`) scopes it in the database. If a filter
// were written in app code and someone forgot it on one query, that query would
// leak; forgetting it here returns the same rows either way, because the policy
// is doing the work. The same reasoning the founder console already uses.
//
// A platform operator gets nothing from these functions, also by policy —
// `user_org_ids()` is membership-based, so an operator with full console access
// still reads zero occupancy rows.
//
// WHAT STILL COMES FROM THE PYTHON BACKEND
//
// The live video overlay and the floorplan heatmap. Both need per-sample
// `floor_x`/`floor_y` coordinates, which `zone_minute_stats` deliberately does
// not have — that absence is what makes a closed minute anonymous. Those two
// panels read the live session; everything historical reads Postgres. Each
// source answers only what it actually can.

import { createClient } from '../supabase/server';

/** BigInt-safe numeric coercion — the Postgres driver returns counts as BigInt. */
const n = (v) => (v == null ? 0 : Number(v));

/**
 * WHY EVERY QUERY BELOW IS AN RPC AND NOT A `.select()`
 *
 * The first implementation fetched buckets and folded them in JavaScript. That
 * silently produced wrong numbers, and the measurement is worth keeping:
 *
 *   .limit(50000) on a 30-day window -> 1,000 rows returned
 *   rows that actually matched        -> 146,359
 *
 * PostgREST caps responses (`max-rows`, 1000 on Supabase) AFTER filtering, with
 * no error. So "last 30 days" was computed from the oldest 1,000 minutes of it
 * and rendered as fact. A confidently wrong number is worse than a failure.
 *
 * Aggregating in Postgres also keeps the payload tiny — one row instead of
 * 146,000 — and lets the planner use the (orgId, bucketStart) index.
 *
 * The functions are NOT `SECURITY DEFINER`: they run as the caller, so
 * `zms_select` scopes them exactly as it scopes a direct query. See
 * prisma/sql/009_dashboard_analytics.sql.
 */

/**
 * Run a Supabase RPC, retrying once if Postgres cancelled it on a timeout.
 *
 * Only retries THAT error — a genuine failure (bad params, permission, syntax)
 * is returned immediately rather than doubling the user's wait for the same
 * result.
 */
async function withTimeoutRetry(run) {
  const first = await run();
  const message = String(first?.error?.message ?? '');
  if (!/statement timeout|canceling statement/i.test(message)) return first;
  return run();
}

/**
 * Resolve a `days` parameter into a window.
 *
 * Clamped to 1..365. A caller asking for ten years would scan the whole table
 * and return a chart nobody can read; refusing quietly at the top is better
 * than a slow query nobody attributes to the date picker.
 */
export async function resolveRange(days = 7) {
  const span = Math.min(365, Math.max(1, Number(days) || 7));
  const until = new Date();
  const since = new Date(until.getTime() - span * 86400_000);
  return { since, until, days: span };
}

/* ── Overview ────────────────────────────────────────────────────────────── */

const EMPTY_OVERVIEW = {
  has_data: false,
  people: 0, zones_active: 0, avg_activity: 0,
  sitting_pct: 0, standing_pct: 0, walking_pct: 0,
  peak_zone: null, longest_dwell_minutes: 0, last_seen: null,
};

/**
 * The six headline tiles.
 *
 * Field names deliberately match what the FastAPI endpoint returned, so
 * `OverviewSection` renders unchanged — the shape is the component's contract,
 * and changing the source should not change the contract.
 *
 * `people` is the SUM of `uniqueTrackCount`, not a distinct count: the bucket
 * table holds no track ids to be distinct over. That is the anonymity trade
 * made in Step 5, and it means someone present across three minutes counts
 * three times. It is activity volume, not headcount.
 */
export async function getOverview(days = 7) {
  const supabase = createClient();
  if (!supabase) return { ...EMPTY_OVERVIEW, days };

  const { since, until, days: span } = await resolveRange(days);

  try {
    // Retried once on a statement timeout.
    //
    // A wide window over a cold cache can exceed Postgres's statement timeout
    // on the FIRST request — measured at 8.4s cold, then 1.4s warm once the
    // pages are in shared buffers. That produces an error for exactly one
    // user and a working page for everyone after them, which is the worst
    // failure to diagnose. The first attempt warms the cache even when it is
    // cancelled, so the retry is nearly always the fast path.
    //
    // `012_dashboard_covering_index.sql` reduces how often this happens; the
    // retry is what makes it invisible when it still does.
    const { data, error } = await withTimeoutRetry(() =>
      supabase.rpc('dashboard_overview', {
        p_since: since.toISOString(),
        p_until: until.toISOString(),
      }),
    );
    if (error) throw new Error(error.message);

    const row = Array.isArray(data) ? data[0] : data;
    if (!row?.has_data) return { ...EMPTY_OVERVIEW, days: span };

    return {
      days: span,
      has_data: true,
      people: n(row.people),
      zones_active: n(row.zones_active),
      avg_activity: n(row.avg_activity),
      sitting_pct: n(row.sitting_pct),
      standing_pct: n(row.standing_pct),
      walking_pct: n(row.walking_pct),
      peak_zone: row.peak_zone_name
        ? { zone: row.peak_zone_name, people: n(row.peak_zone_people) }
        : null,
      longest_dwell_minutes: n(row.longest_dwell_minutes),
      // The component splits on a space and reads the date/time halves.
      last_seen: row.last_seen
        ? new Date(row.last_seen).toISOString().replace('T', ' ').slice(0, 19)
        : null,
      sample_frames: n(row.sample_frames),
    };
  } catch (error) {
    return { ...EMPTY_OVERVIEW, days: span, error: String(error?.message ?? error) };
  }
}

/* ── Trend ───────────────────────────────────────────────────────────────── */

/**
 * A time series for the trend charts.
 *
 * Granularity adapts to the span: a 90-day window at minute resolution is
 * ~130,000 points, unreadable as a chart and slow to ship. Hourly up to a week,
 * daily beyond it, keeping every window to a few hundred points at most.
 */
export async function getTrend(days = 7) {
  const supabase = createClient();
  if (!supabase) return [];

  const { since, until, days: span } = await resolveRange(days);

  try {
    const { data, error } = await withTimeoutRetry(() =>
      supabase.rpc('dashboard_trend', {
      p_since: since.toISOString(),
        p_until: until.toISOString(),
        p_granularity: span > 7 ? 'day' : 'hour',
      }),
    );
    if (error) throw new Error(error.message);

    return (data ?? []).map((row) => ({
      time: span > 7
        ? new Date(row.bucket).toISOString().slice(0, 10)
        : `${new Date(row.bucket).toISOString().slice(0, 13)}:00`,
      avg_activity_score: n(row.avg_activity_score),
      sitting_count: n(row.sitting_count),
      standing_count: n(row.standing_count),
      walking_count: n(row.walking_count),
      people: n(row.people),
      avg_occupancy: n(row.avg_occupancy),
    }));
  } catch {
    return [];
  }
}

/* ── Zones ───────────────────────────────────────────────────────────────── */

/**
 * Dwell and utilisation per zone.
 *
 * Summing `totalDwellSeconds` across buckets is correct: Step 5 stores it as
 * presence WITHIN each minute, not as a cumulative per-track counter. Summing
 * `activity_logs.dwell_duration_seconds` would double-count.
 */
export async function getZoneUtilisation(days = 7) {
  const supabase = createClient();
  if (!supabase) return [];

  const { since, until } = await resolveRange(days);

  try {
    const { data, error } = await withTimeoutRetry(() =>
      supabase.rpc('dashboard_zones', {
      p_since: since.toISOString(),
        p_until: until.toISOString(),
      }),
    );
    if (error) throw new Error(error.message);

    return (data ?? []).map((row) => ({
      zone: row.zone_name ?? 'Unnamed zone',
      zoneId: row.zone_id,
      minutes: n(row.minutes),
      seconds: n(row.seconds),
      visitors: n(row.visitors),
      peak_occupancy: n(row.peak_occupancy),
      avg_occupancy: n(row.avg_occupancy),
      active_minutes: n(row.active_minutes),
    }));
  } catch {
    return [];
  }
}

/* ── Summary ─────────────────────────────────────────────────────────────── */

/** Posture mix and totals — the shape the charts' summary panel expects. */
export async function getSummary(days = 7) {
  const overview = await getOverview(days);
  return {
    total_logs: overview.sample_frames ?? 0,
    average_activity_score: overview.avg_activity ?? 0,
    posture_distribution: {
      sitting_percentage: overview.sitting_pct ?? 0,
      standing_percentage: overview.standing_pct ?? 0,
      walking_percentage: overview.walking_pct ?? 0,
    },
  };
}

/**
 * Everything the charts panel needs, in one round trip.
 *
 * The three used to be three separate browser fetches — which also meant three
 * chances for the views to disagree about which window they described. One
 * server action, three RPCs in parallel, one window.
 */
export async function getChartData(days = 7) {
  const [summary, trend, zones] = await Promise.all([
    getSummary(days),
    getTrend(days),
    getZoneUtilisation(days),
  ]);
  return { summary, trend, zones };
}

/* ── Coverage ────────────────────────────────────────────────────────────── */

/**
 * The full span of data this org actually has.
 *
 * Drives the date-range picker: offering "last 90 days" to an org whose first
 * bucket is yesterday produces an empty chart and a support question. Knowing
 * the real extent lets the UI disable ranges it cannot fill.
 */
export async function getDataCoverage() {
  const supabase = createClient();
  if (!supabase) return { first: null, last: null, hasData: false };

  try {
    const { data, error } = await supabase.rpc('dashboard_coverage');
    if (error) throw new Error(error.message);
    const row = Array.isArray(data) ? data[0] : data;
    return {
      first: row?.first_bucket ?? null,
      last: row?.last_bucket ?? null,
      hasData: n(row?.bucket_count) > 0,
    };
  } catch {
    return { first: null, last: null, hasData: false };
  }
}
