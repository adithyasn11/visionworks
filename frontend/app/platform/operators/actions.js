'use server';

// frontend/app/platform/operators/actions.js
//
// Revoking platform access.
//
// The real enforcement is in the database: revoke_operator() checks that the
// caller is an active operator, refuses to remove the last one, and writes the
// audit entry itself. This action is a thin wrapper — it exists so the page can
// call the RPC and revalidate, not to be the security boundary.
//
// The isPlatformAdmin() check here is still worth having: a Server Action is a
// POST endpoint the client can call directly and does not inherit the layout's
// guard, so failing fast avoids a pointless round trip to Postgres.

import { revalidatePath } from 'next/cache';
import { createClient, isPlatformAdmin } from '../../lib/supabase/server';

export async function revokeOperator(profileId) {
  if (typeof profileId !== 'string' || profileId.length < 10) {
    return { ok: false, message: 'Invalid operator.' };
  }

  if (!(await isPlatformAdmin())) {
    return { ok: false, message: 'Not permitted.' };
  }

  const supabase = createClient();
  const { data, error } = await supabase.rpc('revoke_operator', {
    p_profile_id: profileId,
  });

  if (error) return { ok: false, message: error.message };

  // The function returns a single (ok, message) row. Supabase surfaces a
  // TABLE-returning function as an array.
  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.ok) {
    return { ok: false, message: row?.message ?? 'Could not revoke access.' };
  }

  revalidatePath('/platform/operators');
  revalidatePath('/platform');

  return { ok: true, message: row.message };
}
