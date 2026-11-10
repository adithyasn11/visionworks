'use client';

// frontend/app/dashboard/safety/EventsScreen.jsx
//
// Everything the safety layer decided and saw, filterable: the DECISIONS (each with its result, reason,
// confidence and review state; open one for the evidence and the review controls) and the raw ZONE ACTIVITY
// behind them. Anyone in the organisation can read it; the CSV export is audited on the server.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Download, ListFilter, Loader2 } from 'lucide-react';
import { downloadExport, listDecisions, listEvents } from '../../lib/safetyApi';
import { listSafetyZones } from '../../lib/safety/management';
import { describeZoneEvent, workerLabel } from '../../lib/zoneEvents';
import DecisionDetail from './DecisionDetail';
import { BTN_QUIET, INPUT, Notice, Panel, ReviewChip, SeverityChip, StatusChip, confidenceText, fmtTime } from './common';

const PAGE = 25;
const STATUS_OPTIONS = [['', 'Any result'], ['POTENTIAL_VIOLATION', 'Potential violation'], ['COMPLIANT', 'Compliant'], ['UNVERIFIABLE', 'Cannot verify']];
const REVIEW_OPTIONS = [['', 'Any review state'], ['PENDING', 'Waiting for review'], ['CONFIRMED', 'Confirmed'], ['DISMISSED', 'Dismissed'], ['NOT_REQUIRED', 'Not reviewed']];

const EVENT_TYPES = [['', 'Any activity'], ['ENTERED_ZONE', 'Entered'], ['EXITED_ZONE', 'Left or closed'], ['LOST_IN_ZONE', 'Lost from view'], ['REAPPEARED', 'Back in view'], ['DWELL_WARNING', 'Time warning'], ['DWELL_EXCEEDED', 'Time limit reached']];

/** An <input type=date> value (a day, in the viewer's own time zone) as the start / end instant of that day. */
const dayStart = (v) => (v ? new Date(`${v}T00:00:00`).toISOString() : undefined);
const dayEnd = (v) => (v ? new Date(new Date(`${v}T00:00:00`).getTime() + 86400000).toISOString() : undefined);

export default function EventsScreen({ role }) {
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
    setData((d) => ({ ...d, loading: true }));
    const r = tab === 'decisions' ? await listDecisions(query) : await listEvents(query);
    if (mine !== request.current) return; // a newer filter, tab or page has taken over
    if (!r.ok) { setData({ loading: false, rows: [], total: null, next: null, error: r.message }); return; }
    setData({ loading: false, rows: tab === 'decisions' ? r.data.decisions : r.data.events, total: r.data.total, next: r.data.next_offset, error: null });
  }, [tab, query]);

  useEffect(() => { load(); }, [load]);

  const set = (patch) => { setFilters((f) => ({ ...f, ...patch })); setOffset(0); setOpen(null); };

  async function exportCsv() {
    setExporting({ busy: true });
    const r = await downloadExport({ since: dayStart(filters.from), until: dayEnd(filters.to), status: filters.status || undefined });
    setExporting(r.ok ? { done: true, truncated: r.truncated } : { error: r.message });
  }

  const from = data.rows.length ? offset + 1 : 0;
  return (
    <Panel title="Events" icon={ListFilter} aside={data.loading || data.total == null ? '' : `${data.total} found`}>
      <div role="tablist" aria-label="What to list" className="flex gap-1 mb-3">
        {[['decisions', 'Decisions'], ['activity', 'Zone activity']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} type="button" onClick={() => { setTab(id); setOffset(0); setOpen(null); setData({ loading: true, rows: [], total: null, next: null, error: null }); }}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${tab === id ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted'}`}>{label}</button>
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-2 mb-3">
        {tab === 'decisions' ? (
          <>
            <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Result
              <select value={filters.status} onChange={(e) => set({ status: e.target.value })} className={INPUT}>{STATUS_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Review
              <select value={filters.reviewState} onChange={(e) => set({ reviewState: e.target.value })} className={INPUT}>{REVIEW_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          </>
        ) : (
          <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Activity
            <select value={filters.eventType} onChange={(e) => set({ eventType: e.target.value })} className={INPUT}>{EVENT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        )}
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Zone
          <select value={filters.zoneId} onChange={(e) => set({ zoneId: e.target.value })} className={INPUT}>
            <option value="">Any zone</option>
            {zones.map((z) => <option key={z.id} value={z.id}>{z.name}{z.camera ? ` (${z.camera})` : ''}</option>)}
          </select></label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">From
          <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set({ from: e.target.value })} className={INPUT} /></label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">To
          <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set({ to: e.target.value })} className={INPUT} /></label>
        {tab === 'decisions' && (
          <button type="button" onClick={exportCsv} disabled={exporting && exporting.busy} className={`${BTN_QUIET} ml-auto`}>
            {exporting && exporting.busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Download className="w-3.5 h-3.5" aria-hidden="true" />} Export CSV
          </button>
        )}
      </div>
      {exporting && exporting.error && <Notice tone="error" className="mb-3">{exporting.error}</Notice>}
      {exporting && exporting.done && (
        <Notice tone="ok" className="mb-3">
          The file was downloaded. It follows the result and date filters above (the zone and review filters do not apply, and with no dates chosen it covers the last 30 days){exporting.truncated ? '; it holds only the newest 20,000 rows of the period, so choose a shorter one for the rest' : ''}.
        </Notice>
      )}

      {data.error && <Notice tone="error" className="mb-3">{data.error}</Notice>}
      {data.loading && data.rows.length === 0 && <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading…</p>}
      {!data.loading && !data.error && data.rows.length === 0 && <p className="text-xs text-ink-muted leading-relaxed">Nothing matches these filters.</p>}

      {tab === 'decisions' ? (
        <ul className="flex flex-col divide-y divide-line/60" aria-label="Decisions, newest first">
          {data.rows.map((d) => {
            const isOpen = open === d.id;
            return (
              <li key={d.id} className="py-2.5">
                <button type="button" onClick={() => setOpen(isOpen ? null : d.id)} aria-expanded={isOpen}
                  className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 text-left text-xs rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]">
                  {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" /> : <ChevronRight className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" />}
                  <span className="font-mono text-ink-faint tabular-nums w-32">{fmtTime(d.observedAt)}</span>
                  <StatusChip status={d.status} />
                  {d.status !== 'COMPLIANT' && <ReviewChip state={d.reviewState} />}
                  {d.status === 'POTENTIAL_VIOLATION' && <SeverityChip severity={d.severity} />}
                  <span className="text-ink font-medium">{workerLabel(d.workerKey)}</span>
                  <span className="text-ink-muted">{[d.camera && d.camera.name, d.zone && d.zone.name].filter(Boolean).join(' · ')}</span>
                  {confidenceText(d) && <span className="text-ink-faint">{confidenceText(d)}</span>}
                  <span className="basis-full pl-6 text-ink-muted leading-relaxed">{d.reason}</span>
                </button>
                {isOpen && <div className="mt-3 sm:pl-6"><DecisionDetail decisionId={d.id} role={role} onChanged={load} /></div>}
              </li>
            );
          })}
        </ul>
      ) : (
        <ul className="flex flex-col divide-y divide-line/60 text-xs" aria-label="Zone activity, newest first">
          {data.rows.map((e) => {
            const d = describeZoneEvent({ event_type: e.eventType, worker_key: e.workerKey, details: e.details });
            const zone = zones.find((z) => z.id === e.zoneId);
            return (
              <li key={e.id} className="flex flex-wrap gap-x-3 gap-y-0.5 py-1.5">
                <span className="font-mono text-ink-faint tabular-nums w-32">{fmtTime(e.observedAt)}</span>
                <span className="text-ink-faint w-32 truncate">{zone ? zone.name : 'No zone'}</span>
                <span className={d.tone === 'unconfirmed' ? 'text-ink-faint' : 'text-ink-muted'}>{d.text}</span>
              </li>
            );
          })}
        </ul>
      )}

      {(offset > 0 || data.next != null) && (
        <nav aria-label="Pages" className="mt-3 flex items-center gap-3 text-xs text-ink-muted">
          <button type="button" disabled={offset === 0 || data.loading} onClick={() => { setOffset(Math.max(0, offset - PAGE)); setOpen(null); }} className={BTN_QUIET}>Previous</button>
          <span className="tabular-nums">{from}-{offset + data.rows.length}{data.total != null ? ` of ${data.total}` : ''}</span>
          <button type="button" disabled={data.next == null || data.loading} onClick={() => { setOffset(data.next); setOpen(null); }} className={BTN_QUIET}>Next</button>
        </nav>
      )}
    </Panel>
  );
}
