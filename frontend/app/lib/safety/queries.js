'use server';

// frontend/app/lib/safety/queries.js
//
// The safety decisions the dashboard shows, read from Postgres through the CALLER's own
// session. Row Level Security scopes every query (`safety_decision_select`: the caller's
// organisations only), so there is no orgId filter here and none is needed.
//
// An ALERT is a POTENTIAL_VIOLATION decision that is still waiting for a person
// (reviewState PENDING). There is no separate alert table.

import { createClient } from '../supabase/server';
import { countOverdueActions } from './management';

const ALERT_LIMIT = 25;

/**
 * The open alerts AND the overdue-actions count, in ONE server call.
 *
 * Next.js runs a page's server actions one at a time, so the badge poll used to be two calls queued behind
 * each other (and in front of whatever the screen itself was loading). Here the two reads run together.
 */
export async function getBadgeCounts(day) {
  const [alerts, overdue] = await Promise.all([getOpenAlerts(), countOverdueActions(day).catch(() => 0)]);
  return { ...alerts, overdue: overdue || 0 };
}

/**
 * The open alerts, newest first, and how many there are in all.
 *
 * `count` is exact and counts every open alert, not just the ones returned, so the badge
 * stays right when there are more than a page of them.
 *
 * @returns {Promise<{ count: number, alerts: Array, error?: string }>}
 */
export async function getOpenAlerts() {
  const supabase = createClient();
  // Not signed in, or the database is not configured: say so, rather than reporting "nothing waiting".
  if (!supabase) return { count: 0, alerts: [], error: 'You are not signed in, or the database is not configured.' };

  try {
    const { data, error, count } = await supabase
      .from('safety_decisions')
      .select(
        'id, severity, reason, confidence, observedAt, workerKey, snapshotRef, zone:zones(name), camera:cameras(name), rule:safety_rules(name)',
        { count: 'exact' },
      )
      .eq('status', 'POTENTIAL_VIOLATION')
      .eq('reviewState', 'PENDING')
      .order('observedAt', { ascending: false })
      .limit(ALERT_LIMIT);
    if (error) throw new Error(error.message);

    return {
      count: count ?? (data ? data.length : 0),
      alerts: (data || []).map((d) => ({
        id: d.id,
        severity: d.severity,
        reason: d.reason,
        confidence: d.confidence,
        observedAt: d.observedAt,
        workerKey: d.workerKey,
        hasPicture: Boolean(d.snapshotRef),
        zone: d.zone?.name ?? null,
        camera: d.camera?.name ?? null,
        rule: d.rule?.name ?? null,
      })),
    };
  } catch (e) {
    return { count: 0, alerts: [], error: String(e?.message || e) };
  }
}
