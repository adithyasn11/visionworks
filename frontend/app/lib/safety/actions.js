'use server';

// frontend/app/lib/safety/actions.js
//
// Corrective actions: what was done about a CONFIRMED violation. ADMIN and MANAGER only.
//
// LAYERS, AS EVERYWHERE ELSE
//   1. UI       the controls are not drawn for a VIEWER
//   2. here     requireCapability('safety.actions.edit') -> a sentence, not an error page
//   3. RLS and triggers   corrective_action_insert / _update need manage_org_ids(); a trigger refuses an action
//      on a decision that is not CONFIRMED (or is another organisation's); the database stamps who created and
//      who closed it and when (026), so none of that comes from here.
// Layer 3 holds on its own.

import { createClient } from '../supabase/server';
import { can, denialMessage } from '../permissions';

const fail = (message) => ({ ok: false, message });
const ACTIVE = ['OPEN', 'IN_PROGRESS'];
const NEXT = { OPEN: ['IN_PROGRESS', 'CLOSED', 'CANCELLED'], IN_PROGRESS: ['OPEN', 'CLOSED', 'CANCELLED'], CLOSED: ['OPEN'], CANCELLED: ['OPEN'] };
const words = (s) => String(s).toLowerCase().replace('_', ' ');

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
  return { supabase, orgId };
}

/** Database words are for the server log; the screen gets a sentence. */
function sentence(error, fallback) {
  const msg = String(error?.message ?? '');
  if (/confirmed decision/i.test(msg)) return 'An action can only be opened for a violation a reviewer has confirmed.';
  if (/active member/i.test(msg)) return 'An action can only be assigned to an active member of the organisation.';
  if (/row-level security/i.test(msg)) return 'You do not have permission to do that.';
  if (/title_present/i.test(msg)) return 'An action needs a title.';
  if (msg) console.error('[corrective actions] unhandled database error:', msg);
  return fallback;
}

/** Open an action for a confirmed decision. */
export async function createCorrectiveAction({ decisionId, title, description, priority, assignedToId, dueDate }) {
  const ctx = await requireCapability('safety.actions.edit');
  if (ctx.error) return fail(ctx.error);
  const name = String(title || '').trim();
  if (!name) return fail('An action needs a title.');
  if (name.length > 200) return fail('The title is limited to 200 characters.');
  if (description && String(description).length > 2000) return fail('The description is limited to 2000 characters.');
  if (!['INFO', 'WARNING', 'CRITICAL'].includes(priority || 'WARNING')) return fail('Choose a priority.');
  if (dueDate && !/^\d{4}-\d{2}-\d{2}$/.test(dueDate)) return fail('The due date must be a date.');

  const { data, error } = await ctx.supabase
    .from('corrective_actions')
    .insert({
      orgId: ctx.orgId, decisionId, title: name, description: description ? String(description).trim() : null,
      priority: priority || 'WARNING', assignedToId: assignedToId || null, dueDate: dueDate || null,
    })
    .select('id')
    .single();
  if (error) return fail(sentence(error, 'The action could not be saved.'));
  return { ok: true, id: data.id };
}

/**
 * Move an action on, reassign it or change its due date. Status follows OPEN, IN_PROGRESS, then CLOSED or
 * CANCELLED (and back to OPEN). Closing needs a note, so the record says what was done. The closer and the
 * time are stamped by the database.
 */
export async function updateCorrectiveAction(id, { status, closingNote, assignedToId, dueDate } = {}) {
  const ctx = await requireCapability('safety.actions.edit');
  if (ctx.error) return fail(ctx.error);
  const { data: current, error: readError } = await ctx.supabase
    .from('corrective_actions').select('id, status').eq('id', id).maybeSingle();
  if (readError || !current) return fail('That action was not found.');

  const patch = {};
  if (status && status !== current.status) {
    if (!(NEXT[current.status] || []).includes(status)) return fail(`An action that is ${words(current.status)} cannot become ${words(status)}.`);
    if (status === 'CLOSED' && !String(closingNote || '').trim()) return fail('Say what was done before closing the action.');
    patch.status = status;
  }
  if (closingNote !== undefined && (patch.status === 'CLOSED' || patch.status === 'CANCELLED' || !ACTIVE.includes(current.status))) {
    patch.closingNote = String(closingNote || '').trim().slice(0, 1000) || null;
  }
  if (assignedToId !== undefined) patch.assignedToId = assignedToId || null;
  if (dueDate !== undefined) {
    if (dueDate && !/^\d{4}-\d{2}-\d{2}$/.test(dueDate)) return fail('The due date must be a date.');
    patch.dueDate = dueDate || null;
  }
  if (Object.keys(patch).length === 0) return { ok: true };

  const { data, error } = await ctx.supabase.from('corrective_actions').update(patch).eq('id', id).select('id');
  if (error) return fail(sentence(error, 'The action could not be saved.'));
  if (!data || data.length === 0) return fail('You do not have permission to change that action.');
  return { ok: true };
}
