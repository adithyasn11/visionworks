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
