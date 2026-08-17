'use server';

// frontend/app/platform/organisations/[id]/actions.js
//
// The console's only write paths: suspend/unsuspend an organisation, and adjust
// its data-retention window.
//
// FOUR THINGS EVERY ACTION HERE DOES
//
//   1. Re-checks isPlatformAdmin(). A Server Action is a POST endpoint the
//      client can call directly — it does not inherit the layout's guard, so
//      the check has to be repeated here. This is the layer that would catch a
//      forged request.
//   2. Validates its input rather than trusting the form.
//   3. Writes a platform_audit_logs row. A platform operator is the one actor
//      RLS does not constrain, so the log is the accountability that replaces
//      the missing enforcement.
//   4. revalidatePath() so the page reflects the change without a manual reload.
//
// WHAT IS NOT HERE, ON PURPOSE
//
//   · deleting an organisation — cascades 800k+ rows and is unrecoverable
//   · editing a customer's zones, cameras or sites — that is their
//     configuration, not the vendor's
//   · anything touching occupancy data — no RLS grant exists for it

import { revalidatePath } from 'next/cache';
import { createClient, isPlatformAdmin } from '../../../lib/supabase/server';

/** Same bounds as the organisations_retention_range CHECK constraint in 001. */
const RETENTION_MIN = 1;
const RETENTION_MAX = 730;

async function audit(supabase, { action, orgId, orgName, metadata }) {
  const { data: userData } = await supabase.auth.getUser();
  const user = userData?.user;

  // Audit failure must not silently swallow the outcome of the action, but it
  // also must not roll it back — the write already happened. Log and continue.
  const { error } = await supabase.from('platform_audit_logs').insert({
    actorId: user?.id ?? null,
    actorEmail: user?.email ?? null,
    action,
    targetOrgId: orgId,
    targetOrgName: orgName ?? null,
    metadata: metadata ?? null,
  });
  if (error) {
    console.error('[platform audit] failed to record', action, error.message);
  }
}

/**
 * Suspend or restore an organisation.
 *
 * Implemented as a soft delete (`deletedAt`) rather than a status enum because
 * that is the column the RLS policies and the org_select filter already read —
 * one concept, one field. Restoring is simply nulling it.
 */
export async function setSuspended(orgId, suspended) {
  if (typeof orgId !== 'string' || orgId.length < 10) {
    return { ok: false, error: 'Invalid organisation.' };
  }
  if (typeof suspended !== 'boolean') {
    return { ok: false, error: 'Invalid state.' };
  }

  if (!(await isPlatformAdmin())) {
    return { ok: false, error: 'Not permitted.' };
  }

  const supabase = createClient();

  // Read the name first: after suspension the org_select policy filters
  // deletedAt IS NOT NULL, so the row becomes unreadable through the normal
  // path and the audit entry would lose its subject.
  const { data: before } = await supabase
    .from('organisations')
    .select('id, name, deletedAt')
    .eq('id', orgId)
    .maybeSingle();

  if (!before) return { ok: false, error: 'Organisation not found.' };

  const { error } = await supabase
    .from('organisations')
    .update({ deletedAt: suspended ? new Date().toISOString() : null })
    .eq('id', orgId);

  if (error) return { ok: false, error: error.message };

  await audit(supabase, {
    action: suspended ? 'platform.org_suspended' : 'platform.org_restored',
    orgId,
    orgName: before.name,
    metadata: { previousDeletedAt: before.deletedAt },
  });

  revalidatePath(`/platform/organisations/${orgId}`);
  revalidatePath('/platform/organisations');
  revalidatePath('/platform');

  return { ok: true };
}

/**
 * Change how long an organisation's minute buckets are kept.
 *
 * This is a privacy-relevant setting — shortening it destroys data on the next
 * retention run — so the audit entry records both the old and new values.
 */
export async function setRetentionDays(orgId, days) {
  if (typeof orgId !== 'string' || orgId.length < 10) {
    return { ok: false, error: 'Invalid organisation.' };
  }

  const parsed = Number(days);
  if (!Number.isInteger(parsed) || parsed < RETENTION_MIN || parsed > RETENTION_MAX) {
    return {
      ok: false,
      error: `Retention must be a whole number between ${RETENTION_MIN} and ${RETENTION_MAX} days.`,
    };
  }

  if (!(await isPlatformAdmin())) {
    return { ok: false, error: 'Not permitted.' };
  }

  const supabase = createClient();

  const { data: before } = await supabase
    .from('organisations')
    .select('id, name, dataRetentionDays')
    .eq('id', orgId)
    .maybeSingle();

  if (!before) return { ok: false, error: 'Organisation not found.' };
  if (Number(before.dataRetentionDays) === parsed) {
    return { ok: true, unchanged: true };
  }

  const { error } = await supabase
    .from('organisations')
    .update({ dataRetentionDays: parsed })
    .eq('id', orgId);

  if (error) return { ok: false, error: error.message };

  await audit(supabase, {
    action: 'platform.retention_changed',
    orgId,
    orgName: before.name,
    metadata: { from: Number(before.dataRetentionDays), to: parsed },
  });

  revalidatePath(`/platform/organisations/${orgId}`);
  revalidatePath('/platform/organisations');

  return { ok: true };
}
