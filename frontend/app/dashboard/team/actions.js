'use server';

// frontend/app/dashboard/team/actions.js
//
// Step 16 — the team comparison.
//
// Reads `employee_day_stats` across everybody and folds it into one row per
// person, so the figures can be sorted against each other.
//
// WHAT THE DATABASE DECIDES, NOT THIS FILE
//
// `employee_day_stat_select` (migration 022) returns other people's rows only
// to ADMIN and MANAGER; a VIEWER gets exactly one employee's — their own. This
// action does not check that and must not: a permission enforced in a Server
// Action is a permission that a direct PostgREST call bypasses.
//
// What it does instead is REPORT the shape of what came back, so the page can
// say "you are seeing only your own figures" rather than rendering a one-row
// table that looks like an empty office.
//
// READ-ONLY BY CONSTRUCTION
//
// The table has a SELECT policy and no INSERT/UPDATE/DELETE policy at all, and
// `authenticated` holds only SELECT. Measured figures about somebody's working
// day cannot be edited from a browser, whatever this file did.

import { createClient } from '../../lib/supabase/server';

const fail = (message) => ({ ok: false, message });

/** Turn a Postgres error into a sentence. Unrecognised errors are logged, never shown. */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');
  if (/schema cache|does not exist|relation .* does not exist/i.test(msg)) {
    return 'Team statistics are not set up on this deployment yet. Apply the database migrations prisma/sql/020_identity.sql and 022_employee_hours_and_visibility.sql, then reload.';
  }
  if (/row-level security/i.test(msg)) {
    return 'You do not have access to these figures.';
  }
  if (msg) console.error('[team] unhandled database error:', msg);
  return fallback;
}

/**
 * One row per employee, summed across the window.
 *
 * `days` is clamped to 1-90. Ninety days of daily rows for a large org is still
 * a small result — one row per person per day — and clamping stops a crafted
 * value from asking for the entire history in one request.
 */
export async function getTeamComparison(days = 30) {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const window = Math.max(1, Math.min(90, Number(days) || 30));
  const since = new Date();
  since.setUTCDate(since.getUTCDate() - window);
  const sinceStr = since.toISOString().slice(0, 10);

  const [{ data: employees, error: empError }, { data: stats, error: statError }] =
    await Promise.all([
      // The roster stays org-wide readable (022 deliberately did not narrow
      // it), so this is the full list of people — including those with no
      // measured days, who must appear as "not yet measured" rather than
      // vanish.
      supabase
        .from('employees')
        .select('id, displayName, employeeCode, active, assignedZoneId')
        .is('deletedAt', null)
        .order('displayName', { ascending: true }),
      supabase
        .from('employee_day_stats')
        .select('employeeId, statDate, presentMinutes, deskMinutes, ' +
                'seatedMinutes, awayFromDeskCount, breakMinutes, ' +
                'longestFocusBlock, fragmentationIdx, bindingConfidence, ' +
                'unknownMinutes')
        .gte('statDate', sinceStr),
    ]);

  if (empError) return fail(describeDbError(empError, 'Could not load the roster.'));
  if (statError) return fail(describeDbError(statError, 'Could not load the figures.'));

  const roster = employees ?? [];
  const rows = stats ?? [];

  // Which employees did the database actually return figures for? That set is
  // the answer to "am I seeing the team or just myself", and it comes from the
  // policy rather than from a role check here.
  const measuredIds = new Set(rows.map((r) => r.employeeId));

  const byEmployee = new Map();
  for (const r of rows) {
    const acc = byEmployee.get(r.employeeId) ?? {
      days: 0, presentMinutes: 0, deskMinutes: 0, seatedMinutes: 0,
      awayFromDeskCount: 0, breakMinutes: 0, longestFocusBlock: 0,
      unknownMinutes: 0, confWeight: 0, confAcc: 0, lastDay: null,
    };
    acc.days += 1;
    acc.presentMinutes += Number(r.presentMinutes ?? 0);
    acc.deskMinutes += Number(r.deskMinutes ?? 0);
    acc.seatedMinutes += Number(r.seatedMinutes ?? 0);
    acc.awayFromDeskCount += Number(r.awayFromDeskCount ?? 0);
    acc.breakMinutes += Number(r.breakMinutes ?? 0);
    acc.unknownMinutes += Number(r.unknownMinutes ?? 0);
    // The longest focus block across the window is a MAX, not a sum: it is
    // "the longest they managed", and adding them would invent a block that
    // never happened.
    acc.longestFocusBlock = Math.max(acc.longestFocusBlock,
                                     Number(r.longestFocusBlock ?? 0));
    // Weighted by observation, so a 0.95 day with four minutes in it does not
    // outweigh a 0.55 day with six hours.
    const w = Number(r.presentMinutes ?? 0) || 1;
    acc.confWeight += w;
    acc.confAcc += w * Number(r.bindingConfidence ?? 0);
    if (!acc.lastDay || r.statDate > acc.lastDay) acc.lastDay = r.statDate;
    byEmployee.set(r.employeeId, acc);
  }

  const team = roster
    // A VIEWER's policy returns only their own stats, so showing the whole
    // roster with everyone else blank would advertise exactly what it is
    // meant to withhold. When only one person's figures came back, only that
    // person is listed.
    .filter((e) => measuredIds.size <= 1 ? measuredIds.has(e.id) : true)
    .map((e) => {
      const a = byEmployee.get(e.id);
      if (!a) {
        return {
          id: e.id,
          displayName: e.displayName,
          employeeCode: e.employeeCode,
          active: e.active,
          hasDesk: Boolean(e.assignedZoneId),
          measured: false,
          days: 0, presentMinutes: 0, avgPresentMinutes: 0,
          deskMinutes: 0, avgDeskMinutes: 0, seatedMinutes: 0,
          awayFromDeskCount: 0, breakMinutes: 0, longestFocusBlock: 0,
          unknownMinutes: 0, bindingConfidence: 0, lastDay: null,
        };
      }
      return {
        id: e.id,
        displayName: e.displayName,
        employeeCode: e.employeeCode,
        active: e.active,
        hasDesk: Boolean(e.assignedZoneId),
        measured: true,
        days: a.days,
        // Time observed anywhere, not only at an assigned desk. Somebody
        // without a desk has deskMinutes 0 however long they were on camera,
        // and a table showing only desk time reports them as having done
        // nothing — the exact misreading this page warns against.
        presentMinutes: a.presentMinutes,
        avgPresentMinutes: Math.round(a.presentMinutes / a.days),
        deskMinutes: a.deskMinutes,
        // The per-day average is the only fair way to compare people who were
        // observed on different numbers of days. A total would rank whoever
        // the cameras happened to see most often.
        avgDeskMinutes: Math.round(a.deskMinutes / a.days),
        seatedMinutes: a.seatedMinutes,
        awayFromDeskCount: a.awayFromDeskCount,
        breakMinutes: a.breakMinutes,
        longestFocusBlock: a.longestFocusBlock,
        unknownMinutes: a.unknownMinutes,
        bindingConfidence: a.confWeight > 0
          ? Number((a.confAcc / a.confWeight).toFixed(4)) : 0,
        lastDay: a.lastDay,
      };
    });

  const measured = team.filter((t) => t.measured);

  return {
    ok: true,
    window,
    team,
    // `scope` is derived from what the database returned, not from the
    // caller's role — so it stays correct if the policy ever changes.
    scope: measuredIds.size <= 1 ? 'self' : 'team',
    totals: {
      people: team.length,
      measuredPeople: measured.length,
      unmeasuredPeople: team.length - measured.length,
      withoutDesk: team.filter((t) => !t.hasDesk).length,
      totalUnknownMinutes: measured.reduce((a, t) => a + t.unknownMinutes, 0),
      // The team-wide confidence, weighted the same way as each person's.
      bindingConfidence: (() => {
        let w = 0; let acc = 0;
        for (const t of measured) {
          const weight = t.deskMinutes || 1;
          w += weight;
          acc += weight * t.bindingConfidence;
        }
        return w > 0 ? Number((acc / w).toFixed(4)) : 0;
      })(),
    },
  };
}
