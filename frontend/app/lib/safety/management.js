'use server';

// frontend/app/lib/safety/management.js
//
// What the safety screens read besides decisions (those come from the safety API): the organisation's
// zones (for pickers), its corrective actions, and who an action can be assigned to. All through the
// CALLER's own session, so Row Level Security scopes every query and no orgId filter is written here.

import { createClient } from '../supabase/server';

const ACTION_LIMIT = 100;
// "Today" is the viewer's own calendar day (the screens pass it); the UTC day is only a fallback.
const today = (day) => (typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : new Date().toISOString().slice(0, 10));

/** The organisation's live zones with their camera and safety type, for pickers. */
export async function listSafetyZones() {
  const supabase = createClient();
  if (!supabase) return { zones: [], error: 'You are not signed in, or the database is not configured.' };
  try {
    const { data, error } = await supabase
      .from('zones')
      .select('id, name, safetyType, cameraId, camera:cameras(name)')
      .is('deletedAt', null)
      .order('name', { ascending: true })
      .limit(500);
    if (error) throw new Error(error.message);
    return {
      zones: (data || []).map((z) => ({
        id: z.id, name: z.name, safetyType: z.safetyType || 'GENERAL', cameraId: z.cameraId, camera: z.camera?.name ?? null,
      })),
    };
  } catch {
    return { zones: [], error: 'Could not load the zones.' };
  }
}

/**
 * Corrective actions, newest first. `status`: OPEN | IN_PROGRESS | CLOSED | CANCELLED | 'ACTIVE' (open or in
 * progress). `overdue`: only actions still active whose due date has passed.
 */
export async function listCorrectiveActions({ status, overdue, decisionId, today: day } = {}) {
  const supabase = createClient();
  if (!supabase) return { actions: [], count: 0, error: 'You are not signed in, or the database is not configured.' };
  try {
    let q = supabase
      .from('corrective_actions')
      .select('id, decisionId, title, description, priority, status, assignedToId, dueDate, createdAt, closedAt, closingNote, decision:safety_decisions(reason, workerKey, observedAt, zone:zones(name))', { count: 'exact' })
      .order('createdAt', { ascending: false })
      .limit(ACTION_LIMIT);
    if (status === 'ACTIVE') q = q.in('status', ['OPEN', 'IN_PROGRESS']);
    else if (status) q = q.eq('status', status);
    if (overdue) q = q.in('status', ['OPEN', 'IN_PROGRESS']).lt('dueDate', today(day));
    if (decisionId) q = q.eq('decisionId', decisionId);
    const { data, error, count } = await q;
    if (error) throw new Error(error.message);
    return {
      count: count ?? (data || []).length,
      actions: (data || []).map((a) => ({
        id: a.id, decisionId: a.decisionId, title: a.title, description: a.description, priority: a.priority, status: a.status,
        assignedToId: a.assignedToId, dueDate: a.dueDate, createdAt: a.createdAt, closedAt: a.closedAt, closingNote: a.closingNote,
        reason: a.decision?.reason ?? null, workerKey: a.decision?.workerKey ?? null, observedAt: a.decision?.observedAt ?? null,
        zone: a.decision?.zone?.name ?? null,
      })),
    };
  } catch {
    return { actions: [], count: 0, error: 'Could not load the corrective actions.' };
  }
}

/**
 * The actions list and the people they can be assigned to, in ONE server call (the two reads run together).
 * Next.js queues a page's server actions, so two separate calls cost two waits in a row.
 */
export async function listActionsAndAssignees(options = {}) {
  const [actions, people] = await Promise.all([listCorrectiveActions(options), listAssignees()]);
  return { ...actions, people: people.people || [] };
}

/** How many active actions are past their due date (for the navigation badge). */
export async function countOverdueActions(day) {
  const supabase = createClient();
  if (!supabase) return 0;
  try {
    const { count } = await supabase
      .from('corrective_actions')
      .select('id', { count: 'exact', head: true })
      .in('status', ['OPEN', 'IN_PROGRESS'])
      .lt('dueDate', today(day));
    return count || 0;
  } catch {
    return 0;
  }
}

/** The organisation's active members, for assigning an action (names only as far as the members page already shows them). */
export async function listAssignees() {
  const supabase = createClient();
  if (!supabase) return { people: [] };
  try {
    const { data, error } = await supabase
      .from('memberships')
      .select('profileId, role, profiles:profileId (fullName, email)')
      .eq('status', 'ACTIVE');
    if (error) throw new Error(error.message);
    return {
      people: (data || []).filter((m) => m.profileId).map((m) => ({
        id: m.profileId, role: m.role, name: m.profiles?.fullName || m.profiles?.email || 'Member',
      })),
    };
  } catch {
    return { people: [] };
  }
}
