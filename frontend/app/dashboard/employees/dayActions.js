'use server';

// frontend/app/dashboard/employees/dayActions.js
//
// Reading `employee_day_stats` — the per-person daily rollup Step 8 writes.
//
// Step 14 needs somewhere to show its confidence banner, and a number without
// its confidence is exactly what the plan warns against. So this returns both
// together, always: no caller can accidentally render the desk time without
// the figure that qualifies it.
//
// READ-ONLY BY CONSTRUCTION
//
// `employee_day_stats` has a SELECT policy and no INSERT/UPDATE/DELETE policy
// at all (migration 020), and `authenticated` holds only SELECT on the table.
// Measured data is written by the CV pipeline through the service role and
// cannot be edited from a browser, whatever this file did. That is deliberate:
// a figure about somebody's working day that a manager could quietly adjust
// would be worth nothing.

import { createClient } from '../../lib/supabase/server';

const fail = (message) => ({ ok: false, message });

/**
 * Turn a Postgres error into a sentence.
 *
 * Unrecognised errors are logged and replaced, never shown — the same rule the
 * other action files follow. "relation does not exist" tells a user nothing
 * and leaks the schema to anyone who can provoke it.
 */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');
  if (/schema cache|does not exist|relation .* does not exist/i.test(msg)) {
    return 'Employee statistics are not set up on this deployment yet. Apply the database migration prisma/sql/020_identity.sql, then reload.';
  }
  if (/row-level security/i.test(msg)) {
    return 'You do not have access to these figures.';
  }
  if (msg) console.error('[employee-days] unhandled database error:', msg);
  return fallback;
}

/**
 * The last `days` of rollups for one employee, newest first.
 *
 * Readable by every member — `employee_day_stat_select` returns the rows to
 * anyone in the org, the same basis on which they can see the roster.
 */
export async function getEmployeeDays(employeeId, days = 14) {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');
  if (!employeeId) return fail('No employee was specified.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const since = new Date();
  since.setUTCDate(since.getUTCDate() - Math.max(1, Math.min(90, days)));

  const [{ data: employee }, { data: rows, error }] = await Promise.all([
    supabase.from('employees')
      .select('id, displayName, employeeCode, active')
      .eq('id', employeeId).maybeSingle(),
    supabase.from('employee_day_stats')
      .select('statDate, presentMinutes, deskMinutes, seatedMinutes, ' +
              'awayFromDeskCount, breakMinutes, longestFocusBlock, ' +
              'fragmentationIdx, bindingConfidence, unknownMinutes, ' +
              'firstSeenAt, lastSeenAt')
      .eq('employeeId', employeeId)
      .gte('statDate', since.toISOString().slice(0, 10))
      .order('statDate', { ascending: false }),
  ]);

  if (error) return fail(describeDbError(error, 'Could not load these figures.'));
  // RLS scopes the employee lookup to the caller's org, so an id from another
  // organisation returns nothing and becomes "not found" — the same answer as
  // an id that does not exist.
  if (!employee) return fail('Employee not found.');

  const list = rows ?? [];

  // The rollup across the window, computed here so every caller sees the same
  // arithmetic. `bindingConfidence` is weighted by how much was actually
  // observed each day: a 0.95 day with four minutes in it must not outweigh a
  // 0.55 day with six hours.
  let weight = 0;
  let weighted = 0;
  const totals = {
    presentMinutes: 0, deskMinutes: 0, seatedMinutes: 0,
    awayFromDeskCount: 0, breakMinutes: 0, unknownMinutes: 0,
  };
  for (const r of list) {
    for (const k of Object.keys(totals)) totals[k] += Number(r[k] ?? 0);
    const w = Number(r.presentMinutes ?? 0) || 1;
    weight += w;
    weighted += w * Number(r.bindingConfidence ?? 0);
  }

  return {
    ok: true,
    employee,
    days: list,
    totals: {
      ...totals,
      days: list.length,
      bindingConfidence: weight > 0 ? Number((weighted / weight).toFixed(4)) : 0,
    },
  };
}

/**
 * The 7- and 30-day trend for one employee.
 *
 * Two windows in one call, from one query, because the comparison IS the
 * insight: "6h 12m at desk" means nothing until you know whether that is a
 * normal day for this person. Two round trips could also return two windows
 * computed from different data if a sync landed between them.
 *
 * Days with no observation are excluded from the averages rather than counted
 * as zero. A weekend, or a day the camera was off, is not a day somebody
 * worked zero hours, and averaging it in would understate every figure.
 */
export async function getEmployeeTrend(employeeId) {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');
  if (!employeeId) return fail('No employee was specified.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const since = new Date();
  since.setUTCDate(since.getUTCDate() - 30);

  const { data: rows, error } = await supabase
    .from('employee_day_stats')
    .select('statDate, presentMinutes, deskMinutes, seatedMinutes, ' +
            'awayFromDeskCount, breakMinutes, longestFocusBlock, ' +
            'fragmentationIdx, bindingConfidence, unknownMinutes')
    .eq('employeeId', employeeId)
    .gte('statDate', since.toISOString().slice(0, 10))
    .order('statDate', { ascending: true });

  if (error) return fail(describeDbError(error, 'Could not load the trend.'));

  const list = rows ?? [];
  const cutoff = new Date();
  cutoff.setUTCDate(cutoff.getUTCDate() - 7);
  const cutoffStr = cutoff.toISOString().slice(0, 10);

  const summarise = (subset) => {
    if (!subset.length) return null;
    const sum = (k) => subset.reduce((a, r) => a + Number(r[k] ?? 0), 0);
    return {
      days: subset.length,
      avgDeskMinutes: Math.round(sum('deskMinutes') / subset.length),
      avgSeatedMinutes: Math.round(sum('seatedMinutes') / subset.length),
      avgBreakMinutes: Math.round(sum('breakMinutes') / subset.length),
      avgExits: Number((sum('awayFromDeskCount') / subset.length).toFixed(1)),
      avgFocusBlock: Math.round(sum('longestFocusBlock') / subset.length),
      totalUnknownMinutes: sum('unknownMinutes'),
      // Weighted by observation, for the reason getEmployeeDays documents: a
      // 0.95 day with four minutes in it must not outweigh a 0.55 day with six
      // hours.
      bindingConfidence: (() => {
        let w = 0; let acc = 0;
        for (const r of subset) {
          const weight = Number(r.presentMinutes ?? 0) || 1;
          w += weight;
          acc += weight * Number(r.bindingConfidence ?? 0);
        }
        return w > 0 ? Number((acc / w).toFixed(4)) : 0;
      })(),
    };
  };

  return {
    ok: true,
    series: list,
    last7: summarise(list.filter((r) => r.statDate >= cutoffStr)),
    last30: summarise(list),
  };
}

/**
 * One day's hourly rollup for one employee — the Step 15 timeline.
 *
 * Returns all 24 hours, including the empty ones. A chart drawn only from the
 * hours that have data silently rescales its own axis: a person seen from 09:00
 * to 11:00 and a person seen from 06:00 to 22:00 produce identically-shaped
 * charts, and the reader has no way to see that one of them is a two-hour day.
 * Filling the gaps here means the axis always means the same thing.
 *
 * `observed` distinguishes "this hour was measured and was empty" from "this
 * hour is padding", which the chart needs in order to draw them differently.
 */
export async function getEmployeeHours(employeeId, statDate) {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');
  if (!employeeId) return fail('No employee was specified.');
  if (!statDate) return fail('No date was specified.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const { data: rows, error } = await supabase
    .from('employee_hour_stats')
    .select('hour, presentMinutes, deskMinutes, seatedMinutes, ' +
            'unknownMinutes, awayFromDeskCount, bindingConfidence')
    .eq('employeeId', employeeId)
    .eq('statDate', statDate)
    .order('hour', { ascending: true });

  if (error) return fail(describeDbError(error, 'Could not load this timeline.'));

  const byHour = new Map((rows ?? []).map((r) => [Number(r.hour), r]));
  const hours = Array.from({ length: 24 }, (_, h) => {
    const r = byHour.get(h);
    return {
      hour: h,
      label: `${String(h).padStart(2, '0')}:00`,
      observed: Boolean(r),
      presentMinutes: Number(r?.presentMinutes ?? 0),
      deskMinutes: Number(r?.deskMinutes ?? 0),
      seatedMinutes: Number(r?.seatedMinutes ?? 0),
      unknownMinutes: Number(r?.unknownMinutes ?? 0),
      awayFromDeskCount: Number(r?.awayFromDeskCount ?? 0),
      bindingConfidence: Number(r?.bindingConfidence ?? 0),
    };
  });

  // Trim to the working window actually observed, with an hour of margin, so
  // a 9-to-5 day does not render as a mostly-empty 24-hour axis. Falls back to
  // the whole day when nothing was observed.
  const seen = hours.filter((h) => h.observed).map((h) => h.hour);
  const from = seen.length ? Math.max(0, Math.min(...seen) - 1) : 0;
  const to = seen.length ? Math.min(23, Math.max(...seen) + 1) : 23;

  return {
    ok: true,
    statDate,
    hours: hours.slice(from, to + 1),
    observedHours: seen.length,
  };
}
