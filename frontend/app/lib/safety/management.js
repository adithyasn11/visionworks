'use server';

// frontend/app/lib/safety/management.js
//
// What the safety screens read besides decisions (those come from the safety API): the organisation's
// zones (for pickers). All through the
// CALLER's own session, so Row Level Security scopes every query and no orgId filter is written here.

import { createClient } from '../supabase/server';

// Zones and memberships are also filtered on the caller's own organisation: RLS lets a platform operator read
// every organisation's zones and members, so an operator who is also a member was offered other customers'
// zones in these pickers.
async function currentOrgId(supabase) {
  const { data } = await supabase.auth.getUser();
  if (!data?.user) return null;
  const { data: profile } = await supabase.from('profiles').select('currentOrgId').eq('id', data.user.id).maybeSingle();
  return profile?.currentOrgId ?? null;
}

/** The organisation's live zones with their camera and safety type, for pickers. */
export async function listSafetyZones() {
  const supabase = createClient();
  if (!supabase) return { zones: [], error: 'You are not signed in, or the database is not configured.' };
  try {
    const orgId = await currentOrgId(supabase);
    if (!orgId) return { zones: [] };
    const { data, error } = await supabase
      .from('zones')
      .select('id, name, slug, safetyType, cameraId, camera:cameras(name)')
      .eq('orgId', orgId)
      .is('deletedAt', null)
      .order('name', { ascending: true })
      .limit(500);
    if (error) throw new Error(error.message);
    return {
      zones: (data || []).map((z) => ({
        id: z.id, name: z.name, slug: z.slug || z.name, safetyType: z.safetyType || 'GENERAL', cameraId: z.cameraId, camera: z.camera?.name ?? null,
      })),
    };
  } catch {
    return { zones: [], error: 'Could not load the zones.' };
  }
}

