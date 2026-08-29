'use server';

// frontend/app/dashboard/cameras/topology/actions.js
//
// Camera topology: which exits lead to which entries, and how long the walk
// takes.
//
// Step 12 of IDENTITY_TRACKING_PLAN.md.
//
// WHY THIS IS WORTH A UI RATHER THAN A CONFIG FILE
//
// The topology is the one piece of evidence in cross-camera tracking that
// cannot be faked by a coincidence of appearance. Two strangers may look
// alike; they cannot both have walked through a door that only connects two
// specific rooms, in the time it actually takes to walk it. Step 13 checks the
// link FIRST and absolutely — a perfect appearance score across two unconnected
// cameras is still rejected.
//
// Which means the quality of every cross-camera answer depends on the quality
// of what somebody drew here. A wrong walk-time window does not degrade the
// matching gracefully; it either blocks every real handoff or admits ones that
// never happened.
//
// WHAT THE DATABASE ALREADY ENFORCES
//
// Migration 020 created `camera_links` with the constraints that matter, and
// each was verified against a real Postgres:
//
//   camera_links_from_to_key       UNIQUE (fromCameraId, toCameraId)
//   camera_links_not_self          CHECK (fromCameraId <> toCameraId)
//   camera_links_window_ordered    CHECK (minSeconds >= 0 AND maxSeconds >= minSeconds)
//   camera_link_insert/update/delete  RLS on manage_org_ids()
//
// So the checks here produce a readable sentence and fail fast; they are not
// the boundary. Called directly as a POST endpoint, RLS still holds.

import { revalidatePath } from 'next/cache';
import { createClient } from '../../../lib/supabase/server';
import { can, denialMessage } from '../../../lib/permissions';

const fail = (message) => ({ ok: false, message });

// A walk nobody would call a walk. Somebody who reappears in under a second
// did not cross a building; the two cameras overlap, which is a different
// relationship and not one this models.
const MIN_WALK_SECONDS = 1;
// Beyond five minutes "they walked next door" stops being the simplest
// explanation, and Step 13's departure TTL drops the candidate anyway.
const MAX_WALK_SECONDS = 300;

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
    .from('memberships').select('role')
    .eq('orgId', orgId).eq('profileId', user.id).eq('status', 'ACTIVE')
    .maybeSingle();

  if (!membership) return { error: 'You are not an active member of this organisation.' };
  if (!can(membership.role, capability)) return { error: denialMessage(capability) };

  return { supabase, user, orgId, role: membership.role };
}

/**
 * A Postgres error, as a sentence.
 *
 * Each branch names a constraint migration 020 actually creates. Anything
 * unrecognised becomes the fallback and is logged rather than shown — the same
 * rule the other action files follow, because "relation does not exist" tells
 * a user nothing and leaks the schema to anyone who can provoke an error.
 */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');
  if (/camera_links_from_to_key/i.test(msg)) {
    return 'Those two cameras are already linked in that direction. Edit the existing link instead.';
  }
  if (/camera_links_not_self/i.test(msg)) {
    return 'A camera cannot lead to itself.';
  }
  if (/camera_links_window_ordered/i.test(msg)) {
    return 'The longest walk must be at least as long as the shortest, and neither can be negative.';
  }
  if (/row-level security/i.test(msg)) {
    return 'Only an administrator or manager can change the camera layout.';
  }
  if (/schema cache|does not exist|relation .* does not exist/i.test(msg)) {
    return 'Camera links are not set up on this deployment yet. Apply the database migration prisma/sql/020_identity.sql, then reload.';
  }
  if (msg) console.error('[topology] unhandled database error:', msg);
  return fallback;
}

/* ── Read ────────────────────────────────────────────────────────────────── */

/**
 * The cameras and the links between them.
 *
 * Readable by every member: `camera_link_select` returns the rows to anyone in
 * the org, and the layout of a building is not a secret from the people
 * working in it. Only writing is gated.
 */
export async function listTopology() {
  const supabase = createClient();
  if (!supabase) return fail('Supabase is not configured.');

  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return fail('Your session has expired. Please sign in again.');

  const { data: profile } = await supabase
    .from('profiles').select('currentOrgId').eq('id', auth.user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return fail('Create your organisation first.');

  const { data: membership } = await supabase
    .from('memberships').select('role')
    .eq('orgId', orgId).eq('profileId', auth.user.id).eq('status', 'ACTIVE')
    .maybeSingle();
  if (!membership) return fail('You are not an active member of this organisation.');

  const [{ data: cameras }, { data: links, error }] = await Promise.all([
    supabase.from('cameras')
      .select('id, name, role, status')
      .is('deletedAt', null)
      .order('name', { ascending: true }),
    supabase.from('camera_links')
      .select('id, fromCameraId, toCameraId, minSeconds, maxSeconds, createdAt')
      .order('createdAt', { ascending: true }),
  ]);

  if (error) return fail(describeDbError(error, 'Could not load the camera layout.'));

  return {
    ok: true,
    cameras: cameras ?? [],
    links: links ?? [],
    viewerRole: membership.role,
    limits: { min: MIN_WALK_SECONDS, max: MAX_WALK_SECONDS },
  };
}

/* ── Create ──────────────────────────────────────────────────────────────── */

export async function createLink(form) {
  const { supabase, orgId, error } = await requireCapability('cameras.edit');
  if (error) return fail(error);

  const fromCameraId = form?.fromCameraId || null;
  const toCameraId = form?.toCameraId || null;
  const minSeconds = Number(form?.minSeconds);
  const maxSeconds = Number(form?.maxSeconds);

  if (!fromCameraId || !toCameraId) return fail('Choose both cameras.');
  if (fromCameraId === toCameraId) return fail('A camera cannot lead to itself.');
  if (!Number.isFinite(minSeconds) || !Number.isFinite(maxSeconds)) {
    return fail('Enter both walk times in seconds.');
  }
  if (minSeconds < MIN_WALK_SECONDS || maxSeconds > MAX_WALK_SECONDS) {
    return fail(`Walk times must be between ${MIN_WALK_SECONDS} and ${MAX_WALK_SECONDS} seconds.`);
  }
  if (maxSeconds < minSeconds) {
    return fail('The longest walk must be at least as long as the shortest.');
  }

  const { data, error: dbError } = await supabase
    .from('camera_links')
    .insert({
      orgId,
      fromCameraId,
      toCameraId,
      minSeconds: Math.round(minSeconds),
      maxSeconds: Math.round(maxSeconds),
    })
    .select('id, fromCameraId, toCameraId, minSeconds, maxSeconds, createdAt')
    .maybeSingle();

  if (dbError) return fail(describeDbError(dbError, 'Could not save this link.'));

  revalidatePath('/dashboard/cameras/topology');
  return { ok: true, link: data, message: 'Link saved.' };
}

/* ── Update ──────────────────────────────────────────────────────────────── */

export async function updateLink(id, form) {
  const { supabase, error } = await requireCapability('cameras.edit');
  if (error) return fail(error);
  if (!id) return fail('No link was specified.');

  const minSeconds = Number(form?.minSeconds);
  const maxSeconds = Number(form?.maxSeconds);
  if (!Number.isFinite(minSeconds) || !Number.isFinite(maxSeconds)) {
    return fail('Enter both walk times in seconds.');
  }
  if (minSeconds < MIN_WALK_SECONDS || maxSeconds > MAX_WALK_SECONDS) {
    return fail(`Walk times must be between ${MIN_WALK_SECONDS} and ${MAX_WALK_SECONDS} seconds.`);
  }
  if (maxSeconds < minSeconds) {
    return fail('The longest walk must be at least as long as the shortest.');
  }

  const { data, error: dbError } = await supabase
    .from('camera_links')
    .update({ minSeconds: Math.round(minSeconds), maxSeconds: Math.round(maxSeconds) })
    .eq('id', id)
    .select('id, fromCameraId, toCameraId, minSeconds, maxSeconds, createdAt')
    .maybeSingle();

  if (dbError) return fail(describeDbError(dbError, 'Could not update this link.'));
  // Zero rows means RLS filtered it out — another organisation's link, or one
  // this role may not touch. Postgres reports no error for that, so silence
  // here would look like success.
  if (!data) return fail('That link could not be updated. It may belong to another organisation.');

  revalidatePath('/dashboard/cameras/topology');
  return { ok: true, link: data, message: 'Walk time updated.' };
}

/* ── Delete ──────────────────────────────────────────────────────────────── */

export async function deleteLink(id) {
  const { supabase, error } = await requireCapability('cameras.edit');
  if (error) return fail(error);
  if (!id) return fail('No link was specified.');

  const { error: dbError } = await supabase
    .from('camera_links').delete().eq('id', id);

  if (dbError) return fail(describeDbError(dbError, 'Could not remove this link.'));

  revalidatePath('/dashboard/cameras/topology');
  return { ok: true, message: 'Link removed.' };
}
