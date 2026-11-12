'use client';

// frontend/app/dashboard/safety/ReviewQueue.jsx
//
// The flags waiting for a person, longest-waiting first. Opening one shows the evidence and, for an
// administrator or manager, the Confirm / Dismiss controls. Reviewing goes through the database's own
// review function (audited, role-checked there), so a view-only member sees the queue but no controls.

import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, ClipboardCheck, Loader2 } from 'lucide-react';
import { listDecisions } from '../../lib/safetyApi';
import { can } from '../../lib/permissions';
import { workerLabel } from '../../lib/zoneEvents';
import DecisionDetail from './DecisionDetail';
import { BTN_QUIET, Notice, Panel, SeverityChip, confidenceText, fmtTime } from './common';

const PAGE = 20;

export default function ReviewQueue({ role, onCountChange }) {
  const [state, setState] = useState({ loading: true, items: [], total: 0, next: null, error: null });
  const [open, setOpen] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const canReview = can(role, 'safety.review');

  const load = useCallback(async () => {
    const r = await listDecisions({ status: 'POTENTIAL_VIOLATION', review_state: 'PENDING', sort: 'oldest', limit: PAGE });
    if (!r.ok) { setState({ loading: false, items: [], total: 0, next: null, error: r.message }); return; }
    setState({ loading: false, items: r.data.decisions, total: r.data.total ?? r.data.decisions.length, next: r.data.next_offset, error: null });
  }, []);

  useEffect(() => { load(); }, [load]);

  async function more() {
    setLoadingMore(true);
    const r = await listDecisions({ status: 'POTENTIAL_VIOLATION', review_state: 'PENDING', sort: 'oldest', limit: PAGE, offset: state.next });
    setLoadingMore(false);
    if (!r.ok) { setState((s) => ({ ...s, error: r.message })); return; }
    setState((s) => ({ ...s, items: [...s.items, ...r.data.decisions.filter((d) => !s.items.some((x) => x.id === d.id))], next: r.data.next_offset, error: null }));
  }

  // A reviewed flag leaves the queue; the badge and the overview follow.
  function reviewed(id) {
    // The server's pending list shrinks by one too, so the next page starts one place earlier.
    setState((s) => ({ ...s, items: s.items.filter((d) => d.id !== id), total: Math.max(0, s.total - 1), next: s.next == null ? s.next : Math.max(0, s.next - 1) }));
    setOpen(null);
    if (onCountChange) onCountChange();
  }

  return (
    <Panel title="Review queue" icon={ClipboardCheck} aside={state.loading ? '' : `${state.total} waiting · oldest first`}>
      {!canReview && !state.loading && (
        <Notice className="mb-3">You have view-only access: you can see what is waiting, and an administrator or manager reviews it.</Notice>
      )}
      {state.loading && <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading the queue…</p>}
      {state.error && <Notice tone="error" className="mb-3">{state.error}</Notice>}
      {!state.loading && !state.error && state.items.length === 0 && (
        <p className="text-xs text-ink-muted leading-relaxed">Nothing is waiting for review. When a rule flags something that may be a violation, it appears here until someone judges it.</p>
      )}
      <ul className="flex flex-col divide-y divide-line-soft" aria-label="Flags waiting for review, oldest first">
        {state.items.map((d) => {
          const isOpen = open === d.id;
          return (
            <li key={d.id} className="py-2.5">
              <button
                type="button"
                onClick={() => setOpen(isOpen ? null : d.id)}
                aria-expanded={isOpen}
                className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 text-left text-xs rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]"
              >
                {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" /> : <ChevronRight className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" />}
                <SeverityChip severity={d.severity} />
                <span className="text-ink font-medium">{workerLabel(d.workerKey)}</span>
                <span className="text-ink-muted">{[d.camera && d.camera.name, d.zone && d.zone.name].filter(Boolean).join(' · ') || 'Unknown location'}</span>
                <span className="font-mono text-ink-faint tabular-nums">{fmtTime(d.observedAt)}</span>
                {confidenceText(d) && <span className="text-ink-faint">{confidenceText(d)}</span>}
                <span className="basis-full pl-6 text-ink-muted leading-relaxed">{d.reason}</span>
              </button>
              {isOpen && (
                <div className="mt-3 pl-0 sm:pl-6">
                  <DecisionDetail decisionId={d.id} role={role} onChanged={() => reviewed(d.id)} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {state.next !== null && state.next !== undefined && (
        <button type="button" onClick={more} disabled={loadingMore} className={`${BTN_QUIET} mt-3`}>
          {loadingMore && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} Show the next {PAGE}
        </button>
      )}
    </Panel>
  );
}
