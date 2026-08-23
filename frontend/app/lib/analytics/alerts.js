'use server';

// frontend/app/lib/analytics/alerts.js
//
// Reading and acknowledging alerts.
//
// THE ASYMMETRY IS DELIBERATE
//
// `alerts` has SELECT and UPDATE policies but **no INSERT policy** — the same
// shape as `zone_minute_stats`. A browser client can read its org's alerts and
// change their state, but cannot fabricate one, exactly as it cannot fabricate
// a measurement. Alerts are written by the engine using the service-role key
// (backend/app/db/alerts_engine.py), which is the only credential that
// bypasses RLS, and which never leaves the backend.
//
// So this file has no `create`. That is not an omission.
//
// As everywhere else, there is no `WHERE orgId = ?`: `alert_select` scopes
// reads and `alert_update` scopes writes to `user_org_ids()`.

import { revalidatePath } from 'next/cache';
import { createClient } from '../supabase/server';
import { can, denialMessage } from '../permissions';

const n = (v) => (v == null ? 0 : Number(v));

/**
 * Open and recently-resolved alerts, newest first.
 *
 * Acknowledged alerts stay in the list rather than disappearing: the person who
 * acknowledged one usually still needs to act on it, and a list that empties on
 * acknowledgement trains people to acknowledge in order to clear the badge.
 */
export async function listAlerts({ limit = 20 } = {}) {
  const supabase = createClient();
  if (!supabase) return { ok: true, alerts: [], openCount: 0 };

  try {
    const { data, error } = await supabase
      .from('alerts')
      .select('id, state, severity, message, triggeredValue, thresholdValue, triggeredAt, acknowledgedAt, zoneId, alert_rules:ruleId (name, type), zones:zoneId (name)')
      .in('state', ['OPEN', 'ACKNOWLEDGED'])
      .order('triggeredAt', { ascending: false })
      .limit(limit);

    if (error) throw new Error(error.message);

    const alerts = (data ?? []).map((row) => ({
      id: row.id,
      state: row.state,
      severity: row.severity,
      message: row.message,
      value: n(row.triggeredValue),
      threshold: n(row.thresholdValue),
      triggeredAt: row.triggeredAt,
      acknowledgedAt: row.acknowledgedAt,
      ruleName: row.alert_rules?.name ?? 'Rule',
      type: row.alert_rules?.type ?? null,
      zone: row.zones?.name ?? null,
    }));

    return {
      ok: true,
      alerts,
      openCount: alerts.filter((a) => a.state === 'OPEN').length,
    };
  } catch (error) {
    return { ok: false, alerts: [], openCount: 0, message: String(error?.message ?? error) };
  }
}

/**
 * Acknowledge an alert — "seen, being handled".
 *
 * Records WHO acknowledged it, not just when: `alerts_ack_has_actor` in
 * 001_constraints.sql requires both, on the reasoning that a trail with a
 * timestamp and no actor is not accountability.
 *
 * Acknowledging also re-arms the rule: the engine skips firing while an alert
 * is still OPEN for that zone, so clearing it is how the operator says "tell me
 * again if this continues".
 *
 * Gated on `analysis.run` (ADMIN + MANAGER) — acknowledging is an operational
 * act, and a VIEWER is read-only by definition. The `.select()` guard is the
 * Step 1 lesson: an UPDATE filtered away by RLS returns no error and no rows,
 * and reporting that as success would be a lie the user acts on.
 */
export async function acknowledgeAlert(alertId) {
  const supabase = createClient();
  if (!supabase) return { ok: false, message: 'Supabase is not configured.' };
  if (typeof alertId !== 'string' || !alertId) return { ok: false, message: 'Invalid alert.' };

  const { data: userData, error: authError } = await supabase.auth.getUser();
  if (authError || !userData?.user) {
    return { ok: false, message: 'Your session has expired. Please sign in again.' };
  }
  const user = userData.user;

  // Role comes from the caller's ACTIVE membership, never from the client.
  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', user.id).maybeSingle();
  if (!profile?.currentOrgId) return { ok: false, message: 'No organisation selected.' };

  const { data: membership } = await supabase
    .from('memberships')
    .select('role')
    .eq('orgId', profile.currentOrgId)
    .eq('profileId', user.id)
    .eq('status', 'ACTIVE')
    .maybeSingle();

  if (!can(membership?.role, 'analysis.run')) {
    return { ok: false, message: denialMessage('analysis.run') };
  }

  const { data: updated, error } = await supabase
    .from('alerts')
    .update({
      state: 'ACKNOWLEDGED',
      acknowledgedAt: new Date().toISOString(),
      acknowledgedById: user.id,
    })
    .eq('id', alertId)
    .eq('state', 'OPEN')
    .select('id');

  if (error) return { ok: false, message: error.message };
  if (!updated || updated.length === 0) {
    return { ok: false, message: 'That alert is no longer open.' };
  }

  revalidatePath('/dashboard');
  return { ok: true, message: 'Alert acknowledged.' };
}
