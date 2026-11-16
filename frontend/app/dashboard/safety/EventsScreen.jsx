'use client';

// frontend/app/dashboard/safety/EventsScreen.jsx
//
// Everything the safety layer decided and saw, filterable: the DECISIONS (each with its result, reason,
// confidence and review state; open one for the evidence and the review controls) and the raw ZONE ACTIVITY
// behind them. Anyone in the organisation can read it; the CSV export is audited on the server.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Download, History, ListFilter, Loader2, PlayCircle, X } from 'lucide-react';
import { downloadExport, listDecisions, listEvents } from '../../lib/safetyApi';
import { listSafetyZones } from '../../lib/safety/management';
import { describeZoneEvent, workerLabel } from '../../lib/zoneEvents';
import DecisionModal from './DecisionModal';
import { BTN_QUIET, INPUT, Notice, Panel, fmtTime, Skeleton } from './common';
import { ruleTypeMeta } from '../../lib/safety/ruleTypes';

const PAGE = 25;
const STATUS_OPTIONS = [['', 'Any result'], ['POTENTIAL_VIOLATION', 'Potential violation'], ['COMPLIANT', 'Compliant'], ['UNVERIFIABLE', 'Cannot verify']];
const REVIEW_OPTIONS = [['', 'Any review state'], ['PENDING', 'Waiting for review'], ['CONFIRMED', 'Confirmed'], ['DISMISSED', 'Dismissed'], ['NOT_REQUIRED', 'Not reviewed']];

const EVENT_TYPES = [['', 'Any activity'], ['ENTERED_ZONE', 'Entered'], ['EXITED_ZONE', 'Left or closed'], ['LOST_IN_ZONE', 'Lost from view'], ['REAPPEARED', 'Back in view'], ['DWELL_WARNING', 'Time warning'], ['DWELL_EXCEEDED', 'Time limit reached']];

/** An <input type=date> value (a day, in the viewer's own time zone) as the start / end instant of that day. */
const dayStart = (v) => (v ? new Date(`${v}T00:00:00`).toISOString() : undefined);
const dayEnd = (v) => (v ? new Date(new Date(`${v}T00:00:00`).getTime() + 86400000).toISOString() : undefined);

// One tag per decision: the result, or (for a flag) where its review stands.
const TAG = {
  COMPLIANT: { label: 'OK', cls: 'border-line text-ink-muted bg-surface-alt' },
  UNVERIFIABLE: { label: 'Unsure', cls: 'border-line text-ink-faint bg-transparent' },
  PENDING: { label: 'Flagged', cls: 'border-[color:var(--accent)] text-white bg-accent' },
  CONFIRMED: { label: 'Confirmed', cls: 'border-[color:var(--accent)] text-accent bg-accent-soft' },
  DISMISSED: { label: 'Dismissed', cls: 'border-line text-ink-muted bg-surface-alt line-through decoration-1' },
};
const tagOf = (d) => (d.status === 'POTENTIAL_VIOLATION' ? TAG[d.reviewState] || TAG.PENDING : TAG[d.status] || TAG.UNVERIFIABLE);

/** Nothing to list: plain and centred. With filters on, the way out is to clear them. */
function Empty({ filtered, onClear, onNavigate, tab }) {
  return (
    <div className="flex flex-col items-center justify-center text-center gap-4 py-10 lg:min-h-[calc(100dvh-30.5rem)] xl:min-h-[calc(100dvh-27rem)]">
      <span className="flex items-center justify-center w-14 h-14 rounded-2xl border border-line bg-surface-alt">
        <History className="w-6 h-6 text-ink-muted" strokeWidth={2} aria-hidden="true" />
      </span>
      <div>
        <h2 className="text-xl sm:text-2xl font-black tracking-tight text-ink">{filtered ? 'Nothing matches' : tab === 'decisions' ? 'No decisions yet' : 'No zone activity yet'}</h2>
        <p className="mt-2 text-[0.9375rem] text-ink-muted">{filtered ? 'Try other filters or dates.' : 'Run a check and everything it sees shows up here.'}</p>
      </div>
      {filtered ? (
        <button type="button" onClick={onClear}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-2xl border-2 border-line text-ink font-bold text-[0.875rem] hover:border-[color:var(--accent)] transition-colors">
          <X className="w-4 h-4" aria-hidden="true" /> Clear filters
        </button>
      ) : onNavigate && (
        <button type="button" onClick={() => onNavigate('live')}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-2xl bg-inverse text-inverse font-bold text-[0.875rem] hover:bg-accent transition-colors">
          <PlayCircle className="w-4 h-4" aria-hidden="true" /> Live feed
        </button>
      )}
    </div>
  );
}

export default function EventsScreen({ role, onNavigate }) {
  const [tab, setTab] = useState('decisions');
  const [zones, setZones] = useState([]);
  const [filters, setFilters] = useState({ status: '', reviewState: '', zoneId: '', from: '', to: '', eventType: '' });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ loading: true, rows: [], total: null, next: null, error: null });
  const [open, setOpen] = useState(null);
  const [exporting, setExporting] = useState(null);
  const request = useRef(0);

  useEffect(() => { listSafetyZones().then((r) => setZones(r.zones || [])); }, []);

  const query = useMemo(() => ({
    status: tab === 'decisions' ? filters.status : undefined,
    review_state: tab === 'decisions' ? filters.reviewState : undefined,
    event_type: tab === 'activity' ? filters.eventType : undefined,
    zone_id: filters.zoneId,
    since: dayStart(filters.from),
    until: dayEnd(filters.to),
    limit: PAGE,
    offset,
  }), [tab, filters, offset]);

  const load = useCallback(async () => {
    const mine = ++request.current;
    // A last day before the first (typed, or picked before the other field limited the calendar) is answered
    // here in words, not with the server's "until must be after since".
    if (filters.from && filters.to && filters.to < filters.from) {
      setData({ loading: false, rows: [], total: null, next: null, error: 'The last day cannot be before the first.' });
      return;
    }
    setData((d) => ({ ...d, loading: true }));
    const r = tab === 'decisions' ? await listDecisions(query) : await listEvents(query);
    if (mine !== request.current) return; // a newer filter, tab or page has taken over
    if (!r.ok) { setData({ loading: false, rows: [], total: null, next: null, error: r.message }); return; }
    setData({ loading: false, rows: tab === 'decisions' ? r.data.decisions : r.data.events, total: r.data.total, next: r.data.next_offset, error: null });
  }, [tab, query, filters.from, filters.to]);

  useEffect(() => { load(); }, [load]);

  const set = (patch) => { setFilters((f) => ({ ...f, ...patch })); setOffset(0); setOpen(null); };

  async function exportCsv() {
    if (filters.from && filters.to && filters.to < filters.from) { setExporting({ error: 'The last day cannot be before the first.' }); return; }
    setExporting({ busy: true });
    const r = await downloadExport({ since: dayStart(filters.from), until: dayEnd(filters.to), status: filters.status || undefined });
    setExporting(r.ok ? { done: true, truncated: r.truncated } : { error: r.message });
  }

  const from = data.rows.length ? offset + 1 : 0;
  const filtered = Boolean(filters.zoneId || filters.from || filters.to || (tab === 'decisions' ? filters.status || filters.reviewState : filters.eventType));
  const clear = () => set({ status: '', reviewState: '', zoneId: '', from: '', to: '', eventType: '' });
  return (
    <Panel title="Events" icon={ListFilter} aside={data.loading || data.total == null ? '' : `${data.total} found`}>
      <div role="tablist" aria-label="What to list" className="inline-flex p-1 mb-4 rounded-xl bg-surface-alt border border-line">
        {[['decisions', 'Decisions'], ['activity', 'Zone activity']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} type="button" onClick={() => { setTab(id); setOffset(0); setOpen(null); setData({ loading: true, rows: [], total: null, next: null, error: null }); }}
            className={`px-4 py-1.5 rounded-lg text-[0.8125rem] font-bold transition-colors ${tab === id ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink'}`}>{label}</button>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 items-end gap-3 pb-4 mb-1 border-b border-line-soft">
        {tab === 'decisions' ? (
          <>
            <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">Result
              <select value={filters.status} onChange={(e) => set({ status: e.target.value })} className={`${INPUT} w-full`}>{STATUS_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">Review
              <select value={filters.reviewState} onChange={(e) => set({ reviewState: e.target.value })} className={`${INPUT} w-full`}>{REVIEW_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          </>
        ) : (
          <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">Activity
            <select value={filters.eventType} onChange={(e) => set({ eventType: e.target.value })} className={`${INPUT} w-full`}>{EVENT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        )}
        <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">Zone
          <select value={filters.zoneId} onChange={(e) => set({ zoneId: e.target.value })} className={`${INPUT} w-full`}>
            <option value="">Any zone</option>
            {zones.map((z) => <option key={z.id} value={z.id}>{z.name}{z.camera ? ` (${z.camera})` : ''}</option>)}
          </select></label>
        <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">From
          <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set({ from: e.target.value })} className={`${INPUT} w-full`} /></label>
        <label className="flex flex-col gap-1 min-w-0 text-xs text-ink-faint">To
          <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set({ to: e.target.value })} className={`${INPUT} w-full`} /></label>
        {tab === 'decisions' && (
          <button type="button" onClick={exportCsv} disabled={exporting && exporting.busy} className={`${BTN_QUIET} col-span-2 md:col-span-1 xl:col-start-6 xl:justify-self-end`}>
            {exporting && exporting.busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Download className="w-3.5 h-3.5" aria-hidden="true" />} Export CSV
          </button>
        )}
      </div>
      {/* Not twice: a reversed range already shows the same sentence for the list below. */}
      {exporting && exporting.error && exporting.error !== data.error && <Notice tone="error" className="mb-3">{exporting.error}</Notice>}
      {exporting && exporting.done && (
        <Notice tone="ok" className="mt-3">
          Downloaded. It uses the result and dates (last 30 days if none){exporting.truncated ? '; only the newest 20,000 rows, so pick a shorter period' : ''}.
        </Notice>
      )}

      {data.error && <Notice tone="error" className="mb-3">{data.error}</Notice>}
      {data.loading && data.rows.length === 0 && <div className="pt-4"><Skeleton rows={3} label="Loading" /></div>}
      {!data.loading && !data.error && data.rows.length === 0 && <Empty filtered={filtered} onClear={clear} onNavigate={onNavigate} tab={tab} />}

      {tab === 'decisions' ? (
        <ul className="flex flex-col divide-y divide-line-soft" aria-label="Decisions, newest first">
          {data.rows.map((d, i) => {
            const tag = tagOf(d);
            const what = (ruleTypeMeta(d.ruleType) || {}).label || 'Safety check';
            const where = [workerLabel(d.workerKey), d.camera && d.camera.name, d.zone && d.zone.name].filter(Boolean).join(' · ');
            return (
              <li key={d.id}>
                <button type="button" onClick={() => setOpen(i)} aria-haspopup="dialog" title={d.reason}
                  className="w-full grid grid-cols-[auto_minmax(0,1fr)_auto] sm:grid-cols-[8.75rem_6rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-3 px-2 -mx-2 rounded-lg text-left text-[0.8125rem] hover:bg-surface-alt transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]">
                  <span className="hidden sm:block text-ink-faint tabular-nums whitespace-nowrap">{fmtTime(d.observedAt)}</span>
                  <span className={`justify-self-start px-2 py-0.5 rounded-md border text-[0.6875rem] font-bold ${tag.cls}`}>{tag.label}</span>
                  <span className="min-w-0 flex flex-col sm:flex-row sm:items-baseline sm:gap-3">
                    <span className="text-ink font-bold truncate">{what}</span>
                    <span className="text-ink-faint truncate">{where}</span>
                    <span className="sm:hidden text-ink-faint tabular-nums">{fmtTime(d.observedAt)}</span>
                  </span>
                  <ChevronRight className="w-4 h-4 text-ink-faint" aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <ul className="flex flex-col divide-y divide-line-soft text-[0.8125rem]" aria-label="Zone activity, newest first">
          {data.rows.map((e) => {
            const d = describeZoneEvent({ event_type: e.eventType, worker_key: e.workerKey, details: e.details });
            const zone = zones.find((z) => z.id === e.zoneId);
            return (
              <li key={e.id} className="grid grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[8.75rem_minmax(0,1fr)_auto] items-center gap-x-3 py-3">
                <span className="hidden sm:block text-ink-faint tabular-nums whitespace-nowrap">{fmtTime(e.observedAt)}</span>
                <span className="min-w-0 flex flex-col">
                  <span className={`truncate ${d.tone === 'unconfirmed' ? 'text-ink-muted' : 'text-ink font-medium'}`}>{d.text}</span>
                  <span className="sm:hidden text-ink-faint tabular-nums">{fmtTime(e.observedAt)}</span>
                </span>
                <span className="text-ink-faint truncate max-w-[10rem] text-right">{zone ? zone.name : '—'}</span>
              </li>
            );
          })}
        </ul>
      )}

      {tab === 'decisions' && open !== null && data.rows[open] && (
        <DecisionModal items={data.rows} index={open} onIndex={setOpen} onClose={() => setOpen(null)} role={role} onChanged={() => load()} />
      )}

      {(offset > 0 || data.next != null) && (
        <nav aria-label="Pages" className="mt-3 pt-3 border-t border-line-soft flex items-center justify-between gap-3 text-[0.8125rem] text-ink-muted">
          <button type="button" disabled={offset === 0 || data.loading} onClick={() => { setOffset(Math.max(0, offset - PAGE)); setOpen(null); }} className={BTN_QUIET}>Previous</button>
          <span className="tabular-nums">{from}-{offset + data.rows.length}{data.total != null ? ` of ${data.total}` : ''}</span>
          <button type="button" disabled={data.next == null || data.loading} onClick={() => { setOffset(data.next); setOpen(null); }} className={BTN_QUIET}>Next</button>
        </nav>
      )}
    </Panel>
  );
}
