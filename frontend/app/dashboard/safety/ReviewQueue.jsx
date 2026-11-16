'use client';

// frontend/app/dashboard/safety/ReviewQueue.jsx
//
// The flags waiting for a person, longest-waiting first. Opening one shows the evidence and, for an
// administrator or manager, the Confirm / Dismiss controls. Reviewing goes through the database's own
// review function (audited, role-checked there), so a view-only member sees the queue but no controls.

import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, History, Image as ImageIcon, Loader2, PlayCircle } from 'lucide-react';
import { listDecisions, getSummary } from '../../lib/safetyApi';
import { can } from '../../lib/permissions';
import { workerLabel } from '../../lib/zoneEvents';
import DecisionModal from './DecisionModal';
import { ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { BTN_QUIET, Notice, Panel, SeverityChip, fmtTime, Skeleton } from './common';

const PAGE = 20;

/** How long a flag has waited: "5 min", "3 h", "2 days". */
function waited(iso) {
  const t = Date.parse(iso);
  if (!t) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? '' : 's'}`;
}

/** Nothing waiting: a calm, centred "all caught up" with the week's reviews and where to go next. */
function CaughtUp({ onNavigate }) {
  const [week, setWeek] = useState(null);
  useEffect(() => {
    let live = true;
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } })();
    getSummary(7, tz).then((r) => { if (live && r.ok) setWeek(r.data); });
    return () => { live = false; };
  }, []);
  const stats = [
    ['Checked', week ? week.total : '—'],
    ['Confirmed', week ? week.confirmed : '—'],
    ['Dismissed', week ? week.dismissed : '—'],
  ];
  return (
    <div className="flex flex-col items-center justify-center text-center gap-5 py-8 lg:min-h-[calc(100dvh-19.5rem)]">
      <span className="flex items-center justify-center w-14 h-14 rounded-2xl border border-line bg-surface-alt">
        <ClipboardCheck className="w-6 h-6 text-ink-muted" strokeWidth={2} aria-hidden="true" />
      </span>
      <div>
        <h2 className="text-xl sm:text-2xl font-black tracking-tight text-ink">All caught up</h2>
        <p className="mt-2 text-[0.9375rem] text-ink-muted">No flags waiting. New ones show up here.</p>
      </div>
      <dl className="grid grid-cols-3 w-full max-w-md rounded-2xl border border-line divide-x divide-line overflow-hidden">
        {stats.map(([label, value]) => (
          <div key={label} className="px-3 py-4">
            <dd className="text-2xl font-black text-ink tabular-nums leading-none">{value}</dd>
            <dt className="mt-1.5 text-[0.75rem] font-bold text-ink-muted">{label}</dt>
          </div>
        ))}
      </dl>
      <p className="-mt-3 text-[0.75rem] text-ink-faint">Last 7 days</p>
      {onNavigate && (
        <div className="flex flex-col sm:flex-row gap-3">
          <button type="button" onClick={() => onNavigate('live')}
            className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-2xl bg-inverse text-inverse font-bold text-[0.875rem] hover:bg-accent transition-colors">
            <PlayCircle className="w-4 h-4" aria-hidden="true" /> Live feed
          </button>
          <button type="button" onClick={() => onNavigate('events')}
            className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-2xl border-2 border-line text-ink font-bold text-[0.875rem] hover:border-[color:var(--accent)] transition-colors">
            <History className="w-4 h-4" aria-hidden="true" /> Past decisions
          </button>
        </div>
      )}
    </div>
  );
}

export default function ReviewQueue({ role, onCountChange, onNavigate }) {
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
    <Panel title="Review queue" icon={ClipboardCheck} aside={state.loading || !state.total ? '' : `${state.total} waiting · oldest first`}>
      {!canReview && !state.loading && (
        <Notice className="mb-3">You have view-only access: you can see what is waiting, and an administrator or manager reviews it.</Notice>
      )}
      {state.loading && <Skeleton rows={3} label="Loading the queue" />}
      {state.error && <Notice tone="error" className="mb-3">{state.error}</Notice>}
      {!state.loading && !state.error && state.items.length === 0 && (
        <CaughtUp onNavigate={onNavigate} />
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
                title={d.reason}
                className="w-full h-full text-left rounded-2xl border border-line bg-surface p-4 flex flex-col gap-2 hover:border-[color:var(--accent)] hover:-translate-y-0.5 transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--accent)]"
              >
                <span className="flex items-center justify-between gap-2">
                  <SeverityChip severity={d.severity} />
                  <span className="text-xs text-ink-faint tabular-nums" title={fmtTime(d.observedAt)}>{waited(d.observedAt)}</span>
                </span>
                <span className="text-[0.9375rem] font-black text-ink truncate">{kind}</span>
                <span className="flex items-center justify-between gap-2 text-[0.8125rem] text-ink-muted min-w-0">
                  <span className="truncate">{[workerLabel(d.workerKey), where].filter(Boolean).join(' · ')}</span>
                  {d.has_picture && <ImageIcon className="w-3.5 h-3.5 shrink-0 text-ink-faint" aria-label="Has a picture" />}
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
