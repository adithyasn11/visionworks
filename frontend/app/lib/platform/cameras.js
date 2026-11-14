'use server';

// frontend/app/lib/platform/cameras.js
//
// The organisation's cameras, after onboarding.
//
// WHY THIS EXISTS. A camera could only be added in the onboarding wizard. An organisation that finished it
// without one (or removed its camera) had no way to add one, and without a camera nothing the pipeline decides
// ever reaches the dashboard: safety events, decisions and zones are matched to Postgres by the camera's NAME
// (safety_sync, zones._mirror_zone_to_postgres), so a run on an unregistered camera stays on the laptop and is
// eventually retired unsent.
//
// The name IS the camera's id in the pipeline (the dashboard passes it as `camera_id` and the zones are saved
// under it), so it is restricted to characters that are safe in a URL path and limited to 64 characters, the
// length the backend keeps (video_upload.zones_camera_for).
//
// LAYERS, AS EVERYWHERE ELSE
//   1. UI       the add / remove controls are not drawn without `cameras.edit`
//   2. here     the capability is checked again, and the name is validated
//   3. RLS      camera_insert / camera_delete need manage_org_ids(); a trigger enforces the plan's camera limit
// Every query runs as the caller, so Row Level Security scopes it and no orgId filter is trusted from the client.

import { createClient } from '../supabase/server';
import { can, denialMessage } from '../permissions';
import { planName } from '../plans';

const fail = (message) => ({ ok: false, message });
// An ILIKE pattern that matches `s` exactly, ignoring case only (% _ and \ are escaped).
const likeExact = (s) => s.replace(/[\\%_]/g, (c) => '\\' + c);
const SOURCES = new Set(['WEBCAM', 'UPLOAD']);
// Letters (any script), digits, spaces and - _ . : nothing that changes the meaning of a URL path.
const NAME_OK = /^[\p{L}\p{N}][\p{L}\p{N} _.–—-]*$/u;   // en and em dashes too, as in onboarding

async function requireCapability(capability) {
  const supabase = createClient();
  if (!supabase) return { error: 'The database is not configured.' };
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  const { data: profile } = await supabase.from('profiles').select('currentOrgId').eq('id', data.user.id).maybeSingle();
  const orgId = profile?.currentOrgId;
  if (!orgId) return { error: 'Create your organisation first.' };
  const { data: membership } = await supabase
    .from('memberships').select('role').eq('orgId', orgId).eq('profileId', data.user.id).eq('status', 'ACTIVE').maybeSingle();
  if (!membership) return { error: 'You are not an active member of this organisation.' };
  if (!can(membership.role, capability)) return { error: denialMessage(capability) };
  return { supabase, orgId, role: membership.role };
}

function sentence(error, fallback) {
  const msg = String(error?.message ?? '');
  const siteLimit = msg.match(/plan_limit_sites: the (\w+) plan allows (\d+)/);
  if (siteLimit) {
    return `The ${planName(siteLimit[1])} plan allows ${siteLimit[2]} site${siteLimit[2] === '1' ? '' : 's'}. `
      + 'Remove one, or upgrade or renew the plan, to add another.';
  }
  const limit = msg.match(/plan_limit_cameras: the (\w+) plan allows (\d+)/);
  if (limit) {
    return `The ${planName(limit[1])} plan allows ${limit[2]} camera${limit[2] === '1' ? '' : 's'}. `
      + 'Remove one, or upgrade or renew the plan, to add another.';
  }
  if (/row-level security/i.test(msg)) return 'You do not have permission to do that.';
  if (msg) console.error('[cameras] unhandled database error:', msg);
  return fallback;
}

/** The organisation's cameras, oldest first (the first one is the default everywhere). */
export async function listCameras() {
  const supabase = createClient();
  if (!supabase) return { cameras: [], error: 'You are not signed in, or the database is not configured.' };
  try {
    const { data, error } = await supabase
      .from('cameras')
      .select('id, name, sourceType, status, createdAt, siteId')
      .is('deletedAt', null)
      .order('createdAt', { ascending: true })
      .limit(200);
    if (error) throw new Error(error.message);
    return { cameras: data || [] };
  } catch {
    return { cameras: [], error: 'Could not load the cameras.' };
  }
}

/** Add a camera. `sourceType`: WEBCAM (a webcam or a paired phone) or UPLOAD (recorded videos). */
export async function addCamera({ name, sourceType, siteId }) {
  const ctx = await requireCapability('cameras.edit');
  if (ctx.error) return fail(ctx.error);
  const clean = String(name || '').trim().replace(/\s+/g, ' ');
  if (!clean) return fail('Enter a name for the camera.');
  if (clean.length > 64) return fail('A camera name is limited to 64 characters.');
  if (!NAME_OK.test(clean)) return fail('Use letters, numbers, spaces, dashes, dots and underscores only, starting with a letter or number.');
  if (!SOURCES.has(sourceType)) return fail('Choose where the video comes from.');

  // Names must be unique within the organisation: the pipeline finds a camera by its name.
  // Case-insensitive: "Gate camera" and "gate camera" would be two cameras nobody can tell apart.
  const { data: same } = await ctx.supabase.from('cameras').select('id').ilike('name', likeExact(clean)).is('deletedAt', null).limit(1);
  if (same && same.length) return fail('There is already a camera with that name.');

  // The site chosen on the screen, or the organisation's first site, as onboarding does.
  let site = null;
  if (siteId) {
    const { data } = await ctx.supabase.from('sites').select('id').eq('id', siteId).is('deletedAt', null).maybeSingle();
    if (!data) return fail('That site was not found. Choose another.');
    site = data;
  } else {
    const { data } = await ctx.supabase.from('sites').select('id').eq('orgId', ctx.orgId).is('deletedAt', null).order('createdAt', { ascending: true }).limit(1).maybeSingle();
    site = data;
  }

  const row = {
    orgId: ctx.orgId, siteId: site?.id ?? null, name: clean, sourceType, updatedAt: new Date().toISOString(),
    ...(sourceType === 'WEBCAM' ? { deviceIndex: 0 } : {}),
  };
  const { data, error } = await ctx.supabase.from('cameras').insert(row).select('id, name').single();
  if (error) return fail(sentence(error, 'The camera could not be added.'));
  return { ok: true, camera: data };
}

/**
 * Remove a camera. This deletes the row, which removes its zones and the safety history recorded on it
 * (the foreign keys cascade): the screen says so before asking.
 */
export async function removeCamera(id) {
  const ctx = await requireCapability('cameras.edit');
  if (ctx.error) return fail(ctx.error);
  if (!id || typeof id !== 'string') return fail('Choose a camera.');
  const { data, error } = await ctx.supabase.from('cameras').delete().eq('id', id).select('id');
  if (error) return fail(sentence(error, 'The camera could not be removed.'));
  if (!data || !data.length) return fail('That camera was not found. It may already have been removed.');
  return { ok: true };
}

/* ── Sites ──────────────────────────────────────────────────────────────── */
// A site is a building or work area. Onboarding makes the first; more are added here, up to the plan's limit
// (enforced by the site_plan_limit trigger, 029). Cameras belong to a site.

/** The organisation's sites, oldest first, with how many cameras each has. */
export async function listSites() {
  const supabase = createClient();
  if (!supabase) return { sites: [], error: 'You are not signed in, or the database is not configured.' };
  try {
    // Two plain reads rather than an embedded count: aggregate embedding is not enabled for signed-in users.
    const [sitesRes, camsRes] = await Promise.all([
      supabase.from('sites').select('id, name, timezone, createdAt').is('deletedAt', null)
        .order('createdAt', { ascending: true }).limit(200),
      supabase.from('cameras').select('siteId').is('deletedAt', null).limit(1000),
    ]);
    if (sitesRes.error) throw new Error(sitesRes.error.message);
    const perSite = {};
    for (const c of camsRes.data || []) if (c.siteId) perSite[c.siteId] = (perSite[c.siteId] || 0) + 1;
    return { sites: (sitesRes.data || []).map((s) => ({ ...s, cameras: perSite[s.id] || 0 })) };
  } catch (err) {
    console.error('[sites] could not be listed:', err?.message || err);
    return { sites: [], error: 'Could not load the sites.' };
  }
}

/** Add a site. `timezone` is the browser's (an IANA name); the site's working hours default as in onboarding. */
export async function addSite({ name, timezone }) {
  const ctx = await requireCapability('sites.edit');
  if (ctx.error) return fail(ctx.error);
  const clean = String(name || '').trim().replace(/\s+/g, ' ');
  if (!clean) return fail('Enter a name for the site.');
  if (clean.length > 160) return fail('A site name is limited to 160 characters.');
  const tz = /^[A-Za-z0-9_+/-]{1,64}$/.test(String(timezone || '')) ? timezone : null;
  const { data: same } = await ctx.supabase.from('sites').select('id').ilike('name', likeExact(clean)).is('deletedAt', null).limit(1);
  if (same && same.length) return fail('There is already a site with that name.');
  const { data, error } = await ctx.supabase
    .from('sites')
    .insert({ orgId: ctx.orgId, name: clean, timezone: tz, updatedAt: new Date().toISOString() })
    .select('id, name')
    .single();
  if (error) return fail(sentence(error, 'The site could not be added.'));
  return { ok: true, site: data };
}

/** Remove a site. Only an empty site (no cameras), never the last one, and only by an administrator. */
export async function removeSite(id) {
  const ctx = await requireCapability('sites.edit');
  if (ctx.error) return fail(ctx.error);
  if (ctx.role !== 'ADMIN') return fail('Only an administrator can remove a site.');
  if (!id || typeof id !== 'string') return fail('Choose a site.');
  const { count: sites } = await ctx.supabase.from('sites').select('id', { count: 'exact', head: true }).is('deletedAt', null);
  if ((sites ?? 0) <= 1) return fail('An organisation needs at least one site, so the last one cannot be removed.');
  const { count: cams } = await ctx.supabase.from('cameras').select('id', { count: 'exact', head: true }).eq('siteId', id).is('deletedAt', null);
  if ((cams ?? 0) > 0) return fail('Remove the cameras on this site first.');
  const { data, error } = await ctx.supabase.from('sites').delete().eq('id', id).select('id');
  if (error) return fail(sentence(error, 'The site could not be removed.'));
  if (!data || !data.length) return fail('That site was not found. It may already have been removed.');
  return { ok: true };
}
