'use client';

// frontend/app/dashboard/safety/ActionsScreen.jsx
//
// What was done about the violations a reviewer CONFIRMED: who is doing what, by when, and whether it is
// overdue. An action is opened from a confirmed decision (in the review queue or Events). Everyone in the
// organisation can read the list; administrators and managers move actions on. Closing an action needs a note
// saying what was done, and the database records who closed it and when.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Wrench } from 'lucide-react';
import { listAssignees, listCorrectiveActions } from '../../lib/safety/management';
import { updateCorrectiveAction } from '../../lib/safety/actions';
import { can } from '../../lib/permissions';
import { workerLabel } from '../../lib/zoneEvents';
import { BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Panel, SeverityChip, fmtDate, fmtTime } from './common';

const TABS = [['ACTIVE', 'Active'], ['OVERDUE', 'Overdue'], ['CLOSED', 'Closed'], ['ALL', 'All']];
const STATUS_WORDS = { OPEN: 'Open', IN_PROGRESS: 'In progress', CLOSED: 'Closed', CANCELLED: 'Cancelled' };

/** Today as YYYY-MM-DD in the viewer's own time zone, to compare with a due date (a plain date). */
const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
export const isOverdue = (a, today = localToday()) => (a.status === 'OPEN' || a.status === 'IN_PROGRESS') && Boolean(a.dueDate) && String(a.dueDate).slice(0, 10) < today;

function ActionCard({ action, people, canEdit, onChanged }) {
  const [closing, setClosing] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const overdue = isOverdue(action);
  const active = action.status === 'OPEN' || action.status === 'IN_PROGRESS';
  const assignee = people.find((p) => p.id === action.assignedToId);

  async function run(patch) {
    setBusy(true); setError(null);
    const r = await updateCorrectiveAction(action.id, patch);
    setBusy(false);
    if (!r.ok) { setError(r.message); return false; }
    setClosing(false); setNote('');
    onChanged();
    return true;
  }

  return (
    <li className={`py-3 flex flex-col gap-1.5 ${overdue ? 'border-l-2 border-[color:var(--accent)] pl-3' : ''}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="text-ink font-semibold">{action.title}</span>
        <SeverityChip severity={action.priority} />
        <span className="text-ink-muted">{STATUS_WORDS[action.status] || action.status}</span>
        {action.dueDate && (
          <span className={overdue ? 'text-accent font-semibold' : 'text-ink-faint'}>
            {overdue ? 'Overdue: ' : 'Due '}{fmtDate(action.dueDate)}
          </span>
        )}
        <span className="text-ink-faint">{assignee ? `Assigned to ${assignee.name}` : 'Not assigned'}</span>
      </div>
      {action.description && <p className="text-xs text-ink-muted leading-relaxed">{action.description}</p>}
      <p className="text-[11px] text-ink-faint">
        About {action.workerKey ? workerLabel(action.workerKey) : 'a worker'}{action.zone ? ` in ${action.zone}` : ''}{action.observedAt ? `, ${fmtTime(action.observedAt)}` : ''}: {action.reason}
      </p>
      {action.status === 'CLOSED' && (
        <p className="text-xs text-ink-muted">Closed{action.closedAt ? ` on ${fmtTime(action.closedAt)}` : ''}{action.closingNote ? `: ${action.closingNote}` : ''}</p>
      )}
      {action.status === 'CANCELLED' && <p className="text-xs text-ink-faint">Cancelled{action.closingNote ? `: ${action.closingNote}` : ''}</p>}

      {canEdit && (
        <div className="flex flex-wrap items-end gap-2">
          {active && (
            <>
              <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Assigned to
                <select value={action.assignedToId || ''} disabled={busy} onChange={(e) => run({ assignedToId: e.target.value || null })} className={INPUT}>
                  <option value="">Nobody</option>
                  {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Due
                <input type="date" value={action.dueDate ? String(action.dueDate).slice(0, 10) : ''} disabled={busy} onChange={(e) => run({ dueDate: e.target.value || null })} className={INPUT} />
              </label>
            </>
          )}
          {action.status === 'OPEN' && <button type="button" disabled={busy} onClick={() => run({ status: 'IN_PROGRESS' })} className={BTN_QUIET}>Start</button>}
          {action.status === 'IN_PROGRESS' && <button type="button" disabled={busy} onClick={() => run({ status: 'OPEN' })} className={BTN_QUIET}>Back to open</button>}
          {active && !closing && <button type="button" disabled={busy} onClick={() => setClosing(true)} className={BTN_PRIMARY}>Close</button>}
          {active && <button type="button" disabled={busy} onClick={() => run({ status: 'CANCELLED', closingNote: 'Cancelled' })} className={BTN_QUIET}>Cancel the action</button>}
          {!active && <button type="button" disabled={busy} onClick={() => run({ status: 'OPEN' })} className={BTN_QUIET}>Reopen</button>}
        </div>
      )}
      {closing && (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-ink-faint">What was done
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={2} className={`${INPUT} resize-y`} autoFocus />
          </label>
          <div className="flex gap-2">
            <button type="button" disabled={busy || !note.trim()} onClick={() => run({ status: 'CLOSED', closingNote: note })} className={BTN_PRIMARY}>
              {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} Close the action
            </button>
            <button type="button" onClick={() => setClosing(false)} className={BTN_QUIET}>Not yet</button>
          </div>
        </div>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </li>
  );
}

export default function ActionsScreen({ role, onCountChange }) {
  const [tab, setTab] = useState('ACTIVE');
  const [state, setState] = useState({ loading: true, actions: [], error: null });
  const [people, setPeople] = useState([]);
  const canEdit = can(role, 'safety.actions.edit');
  const request = useRef(0);

  const load = useCallback(async () => {
    const filter = tab === 'ACTIVE' ? { status: 'ACTIVE' } : tab === 'OVERDUE' ? { overdue: true } : tab === 'CLOSED' ? { status: 'CLOSED' } : {};
    const mine = ++request.current;
    const r = await listCorrectiveActions({ ...filter, today: localToday() });
    if (mine !== request.current) return; // a newer tab or reload has taken over
    if (r.error) { setState({ loading: false, actions: [], error: r.error }); return; }
    setState({ loading: false, actions: r.actions, error: null });
  }, [tab]);

  useEffect(() => { setState({ loading: true, actions: [], error: null }); load(); }, [load]);
  useEffect(() => { listAssignees().then((r) => setPeople(r.people || [])); }, []);

  const changed = () => { load(); if (onCountChange) onCountChange(); };
  const overdueCount = useMemo(() => state.actions.filter((a) => isOverdue(a)).length, [state.actions]);

  return (
    <Panel title="Corrective actions" icon={Wrench} aside={state.loading ? '' : `${state.actions.length} shown`}>
      <Notice className="mb-3">
        An action is opened from a violation a reviewer has confirmed: open it in Events (filter Review: Confirmed) and choose Create a corrective action.
      </Notice>
      <div role="tablist" aria-label="Which actions" className="flex flex-wrap gap-1 mb-3">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} type="button" onClick={() => setTab(id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${tab === id ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted'}`}>{label}</button>
        ))}
      </div>
      {state.loading && <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading…</p>}
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {!state.loading && !state.error && state.actions.length === 0 && (
        <p className="text-xs text-ink-muted leading-relaxed">{tab === 'OVERDUE' ? 'Nothing is overdue.' : tab === 'ACTIVE' ? 'No actions are waiting to be done.' : 'No actions here.'}</p>
      )}
      {tab === 'ACTIVE' && overdueCount > 0 && <p className="mb-2 text-xs text-accent font-semibold">{overdueCount} overdue</p>}
      <ul className="flex flex-col divide-y divide-line/60" aria-label="Corrective actions">
        {state.actions.map((a) => <ActionCard key={a.id} action={a} people={people} canEdit={canEdit} onChanged={changed} />)}
      </ul>
    </Panel>
  );
}
