'use client';

// frontend/app/dashboard/safety/ReviewQueue.jsx
//
// The flags waiting for a person, longest-waiting first. Opening one shows the evidence and, for an
// administrator or manager, the Confirm / Dismiss controls. Reviewing goes through the database's own
// review function (audited, role-checked there), so a view-only member sees the queue but no controls.

import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRight, ClipboardCheck, Image as ImageIcon, Loader2 } from 'lucide-react';
import { listDecisions } from '../../lib/safetyApi';
import { can } from '../../lib/permissions';
import { workerLabel } from '../../lib/zoneEvents';
import DecisionModal from './DecisionModal';
import { ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { BTN_QUIET, Notice, Panel, SeverityChip, fmtTime, Skeleton } from './common';

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

  // A reviewed flag leaves the queue; the window moves on to the next one (or closes when none is left).
  function reviewed(id) {
    // The server's pending list shrinks by one too, so the next page starts one place earlier.
    setState((s) => {
      const items = s.items.filter((d) => d.id !== id);
      setOpen((i) => (i === null || items.length === 0 ? null : Math.min(i, items.length - 1)));
      return { ...s, items, total: Math.max(0, s.total - 1), next: s.next == null ? s.next : Math.max(0, s.next - 1) };
    });
    if (onCountChange) onCountChange();
  }

  return (
    <Panel title="Review queue" icon={ClipboardCheck} aside={state.loading ? '' : `${state.total} waiting · oldest first`}>
      {!canReview && !state.loading && (
        <Notice className="mb-3">You have view-only access: you can see what is waiting, and an administrator or manager reviews it.</Notice>
      )}
      {state.loading && <Skeleton rows={3} label="Loading the queue" />}
      {state.error && <Notice tone="error" className="mb-3">{state.error}</Notice>}
      {!state.loading && !state.error && state.items.length === 0 && (
        <p className="text-[0.8125rem] text-ink-muted leading-relaxed">Nothing to review. New flags will appear here.</p>
      )}
      {/* One card per flag; a card opens the flag in a window with its picture and everything about it. */}
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Flags waiting for review, oldest first">
        {state.items.map((d, i) => {
          const kind = (ruleTypeMeta(d.ruleType) || {}).label || 'Safety check';
          const where = [d.camera && d.camera.name, d.zone && d.zone.name].filter(Boolean).join(' · ');
          return (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => setOpen(i)}
                className="w-full h-full text-left rounded-2xl border border-line bg-surface p-4 flex flex-col gap-2.5 hover:border-[color:var(--accent)] hover:-translate-y-0.5 transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--accent)]"
              >
                <span className="flex items-center justify-between gap-2">
                  <SeverityChip severity={d.severity} />
                  <span className="font-mono text-xs text-ink-faint tabular-nums">{fmtTime(d.observedAt)}</span>
                </span>
                <span className="text-[0.9375rem] font-black text-ink">{kind}</span>
                <span className="text-[0.8125rem] text-ink-muted">{workerLabel(d.workerKey)}{where ? ` · ${where}` : ''}</span>
                <span className="text-[0.8125rem] text-ink-muted leading-snug line-clamp-2">{d.reason}</span>
                <span className="mt-auto pt-1 flex items-center justify-between text-[0.8125rem]">
                  <span className="inline-flex items-center gap-1.5 text-ink-faint">
                    {d.has_picture && <><ImageIcon className="w-3.5 h-3.5" aria-hidden="true" /> Picture</>}
                  </span>
                  <span className="inline-flex items-center gap-1 font-bold text-accent">Open <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" /></span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {open !== null && state.items[open] && (
        <DecisionModal items={state.items} index={open} onIndex={setOpen} onClose={() => setOpen(null)} role={role}
          onChanged={(id, updated) => { if (updated && updated.reviewState && updated.reviewState !== 'PENDING') reviewed(id); }} />
      )}
      {state.next !== null && state.next !== undefined && (
        <button type="button" onClick={more} disabled={loadingMore} className={`${BTN_QUIET} mt-3`}>
          {loadingMore && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} Show the next {PAGE}
        </button>
      )}
    </Panel>
  );
}
