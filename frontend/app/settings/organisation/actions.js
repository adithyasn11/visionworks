'use server';

// frontend/app/settings/organisation/actions.js
//
// Organisation settings — ADMIN only.
//
// WHAT MAKES THESE FIELDS DIFFERENT FROM ORDINARY SETTINGS
//
// Three of them are not preferences, they are policy:
//
//   dataRetentionDays          decides when measurements are DESTROYED
//   purgeVideoAfterProcessing  decides whether footage survives inference
//   deletedAt                  soft-deletes the whole organisation
//
// A settings form that treats "90 -> 7" as a dropdown change is lying about
// what it does: the nightly retention job will delete every bucket older than
// the new value the next time it runs, and nothing brings them back. So the
// action reports how many rows the change puts at risk, and the UI makes the
// user confirm that number rather than the setting.
//
// LAYERS, AS EVERYWHERE ELSE
//
//   1. UI       the page renders read-only for a non-admin
//   2. here     requireCapability('org.settings') -> a sentence, not a 403 page
//   3. RLS      org_update USING (id IN admin_org_ids())
//
// Layer 3 holds on its own: a MANAGER POSTing directly to this action gets zero
// rows updated even if layers 1 and 2 were removed. Verified, not assumed.

import { revalidatePath } from 'next/cache';
import { createClient } from '../../lib/supabase/server';
import { can, denialMessage } from '../../lib/permissions';

const fail = (message) => ({ ok: false, message });

/** IANA timezone check, delegated to the platform rather than a hardcoded list. */
function validTimezone(tz) {
  if (typeof tz !== 'string' || !tz || tz.length > 64) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/**
 * The caller, their org, and their role — cross-checked against an ACTIVE
 * membership rather than trusting `currentOrgId` alone. A suspended member
 * keeps a stale pointer, and honouring it would let someone edit the settings
 * of an organisation that removed them.
 */
async function requireCapability(capability) {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured. Add your project URL and anon key to frontend/.env.local, then restart the dev server.' };
  }

  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  const user = data.user;

  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return { error: 'Create your organisation first.' };

  const { data: membership } = await supabase
    .from('memberships')
    .select('role')
    .eq('orgId', orgId)
    .eq('profileId', user.id)
    .eq('status', 'ACTIVE')
    .maybeSingle();

  if (!membership) return { error: 'You are not an active member of this organisation.' };
  if (!can(membership.role, capability)) return { error: denialMessage(capability) };

  return { supabase, user, orgId, role: membership.role };
}

/** Best-effort audit row. Never blocks the change it records. */
async function writeAudit(supabase, { orgId, user, action, metadata }) {
  try {
    await supabase.from('audit_logs').insert({
      orgId,
      actorId: user.id,
      actorEmail: user.email ?? null,
      action,
      targetType: 'Organisation',
      targetId: orgId,
      metadata: metadata ?? null,
    });
  } catch {
    // Deliberately silent — an audit failure must not undo a saved setting.
  }
}

function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');
  if (/organisations_retention_range/i.test(msg)) {
    return 'Retention must be between 1 and 730 days.';
  }
  if (/row-level security/i.test(msg)) return 'Only an administrator can change these settings.';
  return msg || fallback;
}

/* ── Read ────────────────────────────────────────────────────────────────── */

/**
 * Current settings, plus the caller's role so the page knows what to render.
 *
 * Readable by every member — `org_select` returns the row to anyone in the org,
 * and hiding the retention policy from a manager would hide something they have
 * a legitimate interest in. Only WRITING is admin-gated.
 */
export async function getOrganisationSettings() {
  const supabase = createClient();
  if (!supabase) return { ok: false, message: 'Supabase is not configured.' };

  const { data: userData, error: authError } = await supabase.auth.getUser();
  if (authError || !userData?.user) return { ok: false, message: 'Your session has expired.' };

  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', userData.user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return { ok: false, message: 'No organisation selected.' };

  const [{ data: org }, { data: membership }] = await Promise.all([
    supabase
      .from('organisations')
      // No `plan` column here. The tier is shown in the dashboard's Plan
      // section, which reads it through getViewerRole() — one source, subject
      // to the same ACTIVE-membership check as the role. Selecting it here too
      // would be a second reader of the same value with different filtering.
      .select('id, name, slug, timezone, dataRetentionDays, purgeVideoAfterProcessing, defaultSedentaryThresholdMinutes, defaultUtilisationFloorPct, createdAt')
      .eq('id', orgId)
      .maybeSingle(),
    supabase
      .from('memberships').select('role')
      .eq('orgId', orgId).eq('profileId', userData.user.id).eq('status', 'ACTIVE').maybeSingle(),
  ]);

  if (!org) return { ok: false, message: 'Organisation not found.' };

  return { ok: true, organisation: org, viewerRole: membership?.role ?? null };
}

/**
 * How many buckets a proposed retention value would destroy.
 *
 * The whole point of Step 8's warning. "Shortening destroys data" is abstract;
 * "this deletes 412,908 minutes of history, permanently" is a decision someone
 * can actually make. Counted through the caller's own session, so RLS scopes it.
 */
export async function previewRetentionImpact(nextDays) {
  const { supabase, orgId, error } = await requireCapability('org.settings');
  if (error) return { ok: false, message: error };

  const days = Number(nextDays);
  if (!Number.isInteger(days) || days < 1 || days > 730) {
    return { ok: false, message: 'Retention must be between 1 and 730 days.' };
  }

  const cutoff = new Date(Date.now() - days * 86400_000).toISOString();

  const { count, error: dbError } = await supabase
    .from('zone_minute_stats')
    .select('id', { count: 'exact', head: true })
    .lt('bucketStart', cutoff);

  if (dbError) return { ok: false, message: dbError.message };

  return { ok: true, atRisk: count ?? 0, cutoff, days };
}

/* ── Write ───────────────────────────────────────────────────────────────── */

/**
 * Save the settings.
 *
 * Retention is audited SEPARATELY from the rest, with before and after values.
 * It is the one field whose change destroys data, so "what was it before" must
 * be answerable without replaying every prior entry — the same reasoning as
 * `member.role_changed` in Step 3.
 */
export async function updateOrganisationSettings(formData) {
  const { supabase, user, orgId, error } = await requireCapability('org.settings');
  if (error) return fail(error);

  const name = String(formData.get('name') ?? '').trim();
  const timezone = String(formData.get('timezone') ?? '').trim();
  const retentionRaw = String(formData.get('dataRetentionDays') ?? '').trim();
  const purge = String(formData.get('purgeVideoAfterProcessing') ?? '') === 'true';
  const sedentaryRaw = String(formData.get('defaultSedentaryThresholdMinutes') ?? '').trim();
  const floorRaw = String(formData.get('defaultUtilisationFloorPct') ?? '').trim();

  if (!name) return fail('Enter your organisation’s name.');
  if (name.length > 160) return fail('That name is too long — 160 characters at most.');
  if (!validTimezone(timezone)) return fail('That timezone is not recognised.');

  const retention = Number(retentionRaw);
  // Mirrors `organisations_retention_range`. Checked here so the user sees a
  // sentence rather than a constraint name.
  if (!Number.isInteger(retention) || retention < 1 || retention > 730) {
    return fail('Retention must be between 1 and 730 days.');
  }

  const sedentary = Number(sedentaryRaw);
  if (!Number.isInteger(sedentary) || sedentary < 1 || sedentary > 1440) {
    return fail('The sedentary threshold must be between 1 and 1440 minutes.');
  }

  const floor = Number(floorRaw);
  if (!Number.isFinite(floor) || floor < 0 || floor > 100) {
    return fail('The utilisation floor must be between 0 and 100 percent.');
  }

  // Read the current row first, so the audit entry can record what changed
  // rather than only what it became.
  const { data: before } = await supabase
    .from('organisations')
    .select('name, timezone, dataRetentionDays, purgeVideoAfterProcessing, defaultSedentaryThresholdMinutes, defaultUtilisationFloorPct')
    .eq('id', orgId)
    .maybeSingle();

  const { data: updated, error: dbError } = await supabase
    .from('organisations')
    .update({
      name,
      timezone,
      dataRetentionDays: retention,
      purgeVideoAfterProcessing: purge,
      defaultSedentaryThresholdMinutes: sedentary,
      defaultUtilisationFloorPct: floor,
      updatedAt: new Date().toISOString(),
    })
    .eq('id', orgId)
    // An UPDATE filtered away by RLS returns no error and no rows — the Step 1
    // silent-success bug. An empty result is a failure, not a save.
    .select('id');

  if (dbError) return fail(describeDbError(dbError, 'Could not save these settings.'));
  if (!updated || updated.length === 0) {
    return fail('Only an administrator can change these settings.');
  }

  await writeAudit(supabase, {
    orgId, user,
    action: 'organisation.settings_updated',
    metadata: {
      name: before?.name !== name ? { from: before?.name, to: name } : undefined,
      timezone: before?.timezone !== timezone ? { from: before?.timezone, to: timezone } : undefined,
      purgeVideoAfterProcessing: before?.purgeVideoAfterProcessing !== purge
        ? { from: before?.purgeVideoAfterProcessing, to: purge } : undefined,
      defaultSedentaryThresholdMinutes: before?.defaultSedentaryThresholdMinutes !== sedentary
        ? { from: before?.defaultSedentaryThresholdMinutes, to: sedentary } : undefined,
      defaultUtilisationFloorPct: before?.defaultUtilisationFloorPct !== floor
        ? { from: before?.defaultUtilisationFloorPct, to: floor } : undefined,
    },
  });

  // Retention gets its own audit action. It is the field that destroys data, so
  // it should be findable by filtering the log on one verb rather than by
  // reading the metadata of every settings change.
  if (before && before.dataRetentionDays !== retention) {
    await writeAudit(supabase, {
      orgId, user,
      action: 'organisation.retention_changed',
      metadata: {
        from: before.dataRetentionDays,
        to: retention,
        // Recorded because shortening is destructive and the log should say so
        // even if nobody was watching the confirmation dialog.
        shortened: retention < before.dataRetentionDays,
      },
    });
  }

  revalidatePath('/settings/organisation');
  revalidatePath('/dashboard', 'layout');
  return { ok: true, message: 'Settings saved.' };
}

/* ── Danger zone ─────────────────────────────────────────────────────────── */

/**
 * Soft-delete the organisation.
 *
 * `deletedAt`, not a DELETE. Three reasons, all already encoded in the schema:
 * `org_select` filters on `deletedAt IS NULL` so the org vanishes from the app
 * immediately; audit history is never orphaned; and the action stays reversible
 * for a grace period by an operator who can reach the database.
 *
 * Requires typing the organisation's name. A confirm dialog is dismissed by
 * reflex; typing the name is not.
 */
export async function deleteOrganisation(confirmationName) {
  const { supabase, orgId, error } = await requireCapability('org.settings');
  if (error) return fail(error);

  const { data: org } = await supabase
    .from('organisations').select('name').eq('id', orgId).maybeSingle();
  if (!org) return fail('Organisation not found.');

  if (String(confirmationName ?? '').trim() !== org.name) {
    return fail('The name you typed does not match. Nothing was deleted.');
  }

  // Goes through a SECURITY DEFINER function rather than a direct UPDATE.
  //
  // A plain `update({ deletedAt })` is rejected by RLS — reproduced through a
  // real signed-in client, and narrowed by elimination: updating `name` or
  // `dataRetentionDays` succeeds, updating `deletedAt` alone does not. A row
  // that makes itself invisible is fundamentally awkward for a symmetric
  // USING/WITH CHECK, and widening the check did not fix it.
  //
  // So deletion takes the same shape organisation CREATION already does:
  // `organisations` has no INSERT policy either, and `create_organisation()`
  // is the only door. `soft_delete_organisation()` is the matching exit — it
  // verifies an ACTIVE ADMIN membership itself before touching anything, and
  // writes the audit row from inside the definer context, where it can still
  // see the org the caller just made invisible to themselves.
  const { data, error: dbError } = await supabase.rpc('soft_delete_organisation', {
    p_org_id: orgId,
  });

  if (dbError) return fail(describeDbError(dbError, 'Could not delete this organisation.'));

  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) return fail(row?.message ?? 'Could not delete this organisation.');

  revalidatePath('/dashboard', 'layout');
  return { ok: true, message: row.message };
}

