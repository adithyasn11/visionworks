'use client';

// frontend/app/dashboard/safety/ReportsScreen.jsx
//
// The safety report, kept to what an administrator acts on: the period, the headline numbers, what happened to the
// flags, when things happened, the rules and zones with the most flags, the safety-gear figures, and the PDF.
// Scheduled reports and the reports made before sit at the bottom.
//
// The figures come from the database (exact, in the site's own time zone); nothing is recomputed here. A flag is a
// potential violation until a person confirms it, and the words say so.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarClock, Download, FileSpreadsheet, FileText, HardHat, History, Loader2, MapPin, RefreshCw, ShieldCheck,
  SlidersHorizontal, BarChart3, ClipboardCheck,
} from 'lucide-react';
import { downloadExport, downloadReport, getReport, getReportHistory } from '../../lib/safetyApi';
import { ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Skeleton } from './common';
import { getPlanUsage, setReportSchedule } from '../planActions';
import { can } from '../../lib/permissions';

const PRESETS = [
  { id: '24h', label: '24 hours', hours: 24 },
  { id: '7d', label: '7 days', hours: 24 * 7 },
  { id: '30d', label: '30 days', hours: 24 * 30 },
  { id: '90d', label: '90 days', hours: 24 * 90 },
];
const SCHEDULES = [{ id: 'OFF', label: 'Off' }, { id: 'DAILY', label: 'Daily' }, { id: 'WEEKLY', label: 'Weekly' }, { id: 'MONTHLY', label: 'Monthly' }];
const MAX_DAYS = 366;

const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(Number(v) * 100)}%`);
const num = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString());
const ruleName = (type) => (ruleTypeMeta(type) || {}).label || type;
const pad = (n) => String(n).padStart(2, '0');
const localDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const siteZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };
const fmtDay = (iso, tz) => { try { return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', timeZone: tz }).format(new Date(iso)); } catch { return String(iso).slice(0, 10); } };
const fmtWhen = (iso, tz) => { try { return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: tz }).format(new Date(iso)); } catch { return String(iso); } };

const CARD = 'rounded-2xl border border-line bg-surface p-5 min-w-0';
const TITLE = 'flex items-center gap-2 text-[0.9375rem] font-black text-ink';
const SEG = (on) => `px-3 py-1.5 rounded-lg text-[0.8125rem] font-bold transition-colors ${on ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink'}`;

function Card({ icon: Icon, title, aside, children, className = '' }) {
  return (
    <section className={`${CARD} flex flex-col gap-4 ${className}`}>
      <header className="flex items-center justify-between gap-3">
        <h3 className={TITLE}><Icon className="w-4 h-4 text-accent" aria-hidden="true" /> {title}</h3>
        {aside && <span className="text-[0.75rem] text-ink-faint truncate">{aside}</span>}
      </header>
      {children}
    </section>
  );
}

function Kpi({ label, value, sub, strong }) {
  return (
    <div className={`${CARD} flex flex-col gap-1`}>
      <span className="text-[0.75rem] font-bold text-ink-muted">{label}</span>
      <span className={`text-3xl font-black tracking-tight tabular-nums leading-none mt-1 ${strong ? 'text-accent' : 'text-ink'}`}>{value}</span>
      {sub && <span className="text-[0.75rem] text-ink-faint truncate">{sub}</span>}
    </div>
  );
}

/** Columns for decisions over time; the flagged part in red. */
function Activity({ items, every }) {
  const peak = Math.max(1, ...items.map((x) => x.total));
  if (!items.some((x) => x.total)) return <p className="flex-1 flex items-center justify-center text-[0.8125rem] text-ink-muted py-10">No checks in this period.</p>;
  return (
    <div className="flex-1 flex flex-col min-h-[11rem]" role="img" aria-label={`Checks over time: ${items.filter((x) => x.total).map((x) => `${x.label} ${x.total}`).join(', ')}`}>
      <div className="flex-1 flex items-end gap-1 border-b border-line">
        {items.map((x) => (
          <div key={x.label} className="flex-1 min-w-[3px] h-full flex flex-col justify-end" title={`${x.label}: ${x.total} checked, ${x.flagged} flagged`}>
            <div className="w-full rounded-t-md relative overflow-hidden" style={{ height: `${(x.total / peak) * 100}%`, minHeight: x.total ? 4 : 0, background: 'var(--field)' }}>
              {x.flagged > 0 && <div className="absolute bottom-0 inset-x-0" style={{ height: `${(x.flagged / x.total) * 100}%`, background: 'var(--accent)' }} />}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-1 text-[0.6875rem] text-ink-faint tabular-nums">
        {items.map((x, i) => <span key={x.label} className="flex-1 min-w-[3px] text-center whitespace-nowrap">{i % every === 0 ? x.short : ''}</span>)}
      </div>
      <div className="mt-3 flex gap-4 text-[0.75rem] text-ink-faint">
        <span className="flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--field)' }} /> Checked</span>
        <span className="flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-sm bg-accent" /> Flagged</span>
      </div>
    </div>
  );
}

/** The top few rows of a breakdown, with a bar for how many were flagged. */
function TopList({ rows, label, empty }) {
  const top = [...rows].sort((a, b) => b.flagged - a.flagged || b.decisions - a.decisions).slice(0, 5);
  const peak = Math.max(1, ...top.map((r) => r.decisions));
  if (!top.length) return <p className="text-[0.8125rem] text-ink-muted">{empty}</p>;
  return (
    <ul className="flex flex-col gap-3">
      {top.map((r, i) => (
        <li key={`${label(r)}-${i}`} className="min-w-0">
          <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
            <span className="text-ink font-bold truncate" title={label(r)}>{label(r)}</span>
            <span className="shrink-0 tabular-nums text-ink-muted"><b className={r.flagged ? 'text-accent' : 'text-ink'}>{r.flagged}</b> / {r.decisions}</span>
          </div>
          <div className="mt-1.5 h-1.5 rounded-full bg-line-soft overflow-hidden">
            <div className="h-full rounded-full relative" style={{ width: `${(r.decisions / peak) * 100}%`, background: 'var(--field)' }}>
              {r.flagged > 0 && <div className="absolute inset-y-0 left-0 bg-accent rounded-full" style={{ width: `${(r.flagged / r.decisions) * 100}%` }} />}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Flags({ r }) {
  const total = r.flagged_by_system || 0;
  const rows = [
    { label: 'Confirmed', value: r.confirmed, color: 'var(--accent)' },
    { label: 'Dismissed', value: r.dismissed, color: 'var(--field)' },
    { label: 'Waiting', value: r.pending, color: 'var(--line)' },
  ];
  return (
    <div className="flex-1 flex flex-col gap-4">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-line-soft" role="img" aria-label={`${total} flagged: ${r.confirmed} confirmed, ${r.dismissed} dismissed, ${r.pending} waiting`}>
        {rows.map((x) => <div key={x.label} style={{ width: `${total ? (x.value / total) * 100 : 0}%`, background: x.color }} />)}
      </div>
      <ul className="flex flex-col divide-y divide-line-soft">
        {rows.map((x) => (
          <li key={x.label} className="flex items-center justify-between py-2 text-[0.875rem]">
            <span className="flex items-center gap-2 text-ink-muted"><i className="w-2.5 h-2.5 rounded-sm" style={{ background: x.color }} /> {x.label}</span>
            <span className="font-black text-ink tabular-nums">{x.value}</span>
          </li>
        ))}
      </ul>
      <p className="mt-auto text-[0.75rem] text-ink-faint">{r.confirmed + r.dismissed ? `People agreed with ${pct(r.confirmed_share_of_reviewed)} of the flags they judged.` : 'No flag judged yet.'}</p>
    </div>
  );
}

function Scheduled({ role, onSaved }) {
  const [usage, setUsage] = useState(null);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const canEdit = can(role, 'cameras.edit');
  const load = useCallback(async () => { const r = await getPlanUsage(); if (r?.ok) setUsage(r.usage); }, []);
  useEffect(() => { load(); }, [load]);
  const choose = async (id) => {
    setBusy(id); setMessage(null);
    const r = await setReportSchedule(id);
    setBusy(null);
    if (!r.ok) setMessage(r.message);
    else { await load(); onSaved?.(); }
  };
  const locked = usage && usage.effectivePlan === 'FREE';
  const current = (usage && usage.reportSchedule) || 'OFF';
  return (
    <Card icon={CalendarClock} title="Send me a PDF" aside={locked ? 'Growth and Enterprise' : null}>
      {!usage ? <Skeleton rows={1} label="Loading" /> : locked ? (
        <p className="text-[0.8125rem] text-ink-muted">Upgrade on the Plan page to get a report every day, week or month.</p>
      ) : (
        <>
          <div role="group" aria-label="Schedule" className="inline-flex self-start p-1 rounded-xl bg-surface-alt border border-line">
            {SCHEDULES.map((x) => (
              <button key={x.id} type="button" aria-pressed={current === x.id} disabled={!canEdit || busy !== null}
                onClick={() => current !== x.id && choose(x.id)} className={`${SEG(current === x.id)} disabled:opacity-60`}>
                {busy === x.id && <Loader2 className="inline w-3 h-3 animate-spin mr-1" aria-hidden="true" />}{x.label}
              </button>
            ))}
          </div>
          <p className="text-[0.75rem] text-ink-faint">
            {current !== 'OFF' && usage.reportNextAt
              ? `Next one ${new Date(usage.reportNextAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}.`
              : canEdit ? 'Pick how often. It shows up under Past reports.' : 'Only an admin or manager can change this.'}
          </p>
        </>
      )}
      {message && <Notice tone="error">{message}</Notice>}
    </Card>
  );
}

export default function ReportsScreen({ role }) {
  const [tz, setTz] = useState(siteZone);
  const [tick, setTick] = useState(0);
  const [preset, setPreset] = useState('7d');
  const today = useMemo(() => new Date(), []);
  const [custom, setCustom] = useState({ from: localDay(new Date(today.getTime() - 6 * 86400000)), to: localDay(today) });
  const [state, setState] = useState({ loading: true, report: null, error: null });
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState({ loading: true, rows: [], error: null });
  const [again, setAgain] = useState(null);
  const request = useRef(0);
  const anchor = useRef(Date.now());

  const period = useMemo(() => {
    if (preset === 'custom') {
      if (!custom.from || !custom.to) return { error: 'Choose a first and last day.' };
      const start = new Date(`${custom.from}T00:00:00`);
      const last = new Date(`${custom.to}T00:00:00`);
      const end = new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { error: 'Choose a first and last day.' };
      if (end <= start) return { error: 'The last day cannot be before the first.' };
      if (end - start > MAX_DAYS * 86400000) return { error: `A report covers at most ${MAX_DAYS} days.` };
      return { since: start.toISOString(), until: end.toISOString() };
    }
    const p = PRESETS.find((x) => x.id === preset) || PRESETS[1];
    const end = new Date(anchor.current);
    return { since: new Date(end.getTime() - p.hours * 3600000).toISOString(), until: end.toISOString() };
  }, [preset, custom, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    const mine = ++request.current;
    if (period.error) { setState({ loading: false, report: null, error: null }); return; }
    setState((s) => ({ ...s, loading: true, error: null }));
    const r = await getReport({ since: period.since, until: period.until, tz });
    if (mine !== request.current) return;
    if (!r.ok && /time zone/i.test(r.message || '') && tz !== 'UTC') { setTz('UTC'); return; }
    setState(r.ok ? { loading: false, report: r.data, error: null } : { loading: false, report: null, error: r.message });
  }, [period, tz]);
  useEffect(() => { load(); }, [load]);

  const loadHistory = useCallback(async () => {
    const r = await getReportHistory(6);
    setHistory(r.ok ? { loading: false, rows: r.data.reports || [], error: null } : { loading: false, rows: [], error: r.message });
  }, []);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const choose = (id) => { anchor.current = Date.now(); setPreset(id); setTick((t) => t + 1); setResult(null); };

  async function run(kind) {
    setBusy(kind); setResult(null);
    const r = kind === 'pdf'
      ? await downloadReport('pdf', { since: period.since, until: period.until, tz })
      : await downloadExport({ since: period.since, until: period.until });
    setBusy(null); setResult(r);
    if (r.ok && kind === 'pdf') loadHistory();
  }
  async function downloadAgain(h) {
    setAgain(h.id); setResult(null);
    const r = await downloadReport(String(h.format || 'PDF').toLowerCase(), { since: h.rangeStart, until: h.rangeEnd, tz, record: false });
    setAgain(null); setResult(r);
  }

  const r = state.report;
  const invalid = Boolean(period.error);
  const hours = r ? r.by_hour.map((h) => ({ label: `${pad(h.hour)}:00`, short: pad(h.hour), total: h.decisions, flagged: h.flagged })) : [];
  const days = r ? r.by_day.map((d) => ({ label: d.day, short: d.day.slice(8), total: d.decisions, flagged: d.flagged })) : [];
  const byDay = days.length > 1;
  const ppe = r && r.ppe;

  return (
    <div className="flex flex-col gap-4">
      {/* Period and downloads. */}
      <div className={`${CARD} flex flex-wrap items-center gap-3`}>
        <div role="group" aria-label="Period" className="inline-flex flex-wrap p-1 rounded-xl bg-surface-alt border border-line">
          {PRESETS.map((p) => <button key={p.id} type="button" aria-pressed={preset === p.id} onClick={() => choose(p.id)} className={SEG(preset === p.id)}>{p.label}</button>)}
          <button type="button" aria-pressed={preset === 'custom'} onClick={() => choose('custom')} className={SEG(preset === 'custom')}>Pick days</button>
        </div>
        {preset === 'custom' && (
          <div className="flex items-center gap-2">
            <input type="date" aria-label="First day" value={custom.from} max={custom.to || undefined} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))} className={INPUT} />
            <span className="text-ink-faint">to</span>
            <input type="date" aria-label="Last day" value={custom.to} min={custom.from || undefined} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))} className={INPUT} />
          </div>
        )}
        <button type="button" onClick={() => choose(preset)} disabled={state.loading} className={BTN_QUIET} aria-label="Refresh" title="Refresh">
          {state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />}
        </button>
        <div className="ml-auto flex flex-wrap gap-2">
          <button type="button" className={BTN_QUIET} disabled={invalid || busy !== null} onClick={() => run('decisions')} title="Every decision in the period, one row each">
            {busy === 'decisions' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileSpreadsheet className="w-3.5 h-3.5" aria-hidden="true" />} CSV
          </button>
          <button type="button" className={BTN_PRIMARY} disabled={invalid || busy !== null} onClick={() => run('pdf')}>
            {busy === 'pdf' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileText className="w-3.5 h-3.5" aria-hidden="true" />} Download PDF
          </button>
        </div>
      </div>
      {invalid && <Notice tone="error">{period.error}</Notice>}
      {result && result.ok && <Notice tone="ok">{result.name ? `${result.name} downloaded.` : 'Downloaded.'}{result.truncated ? ' Only the newest rows; pick a shorter period for the rest.' : ''}</Notice>}
      {result && !result.ok && <Notice tone="error">{result.message}</Notice>}
      {state.error && <Notice tone="error">{state.error}</Notice>}

      {state.loading && !r && <div className={CARD}><Skeleton rows={4} label="Making the report" /></div>}

      {r && (
        <div className={`flex flex-col gap-4 ${state.loading ? 'opacity-60 transition-opacity' : ''}`} aria-busy={state.loading}>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
            <Kpi label="Checked" value={num(r.decisions)} sub={`${fmtDay(r.since, r.time_zone)} – ${fmtDay(r.until, r.time_zone)}`} />
            <Kpi label="Compliance" value={pct(r.compliance_rate)} sub={`${num(r.compliant)} were fine`} />
            <Kpi label="Flagged" value={num(r.flagged_by_system)} sub="for a person to judge" strong={r.flagged_by_system > 0} />
            <Kpi label="Confirmed" value={num(r.confirmed)} sub={`${num(r.dismissed)} dismissed`} />
            <Kpi label="Waiting" value={num(r.pending)} sub={r.pending ? 'in the Review queue' : 'nothing waiting'} strong={r.pending > 0} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card icon={BarChart3} title={byDay ? 'By day' : 'By hour'} aside={`site time, ${r.time_zone}`} className="lg:col-span-2">
              <Activity items={byDay ? days : hours} every={byDay ? Math.max(1, Math.ceil(days.length / 14)) : 3} />
            </Card>
            <Card icon={ClipboardCheck} title="The flags" aside={`${num(r.flagged_by_system)} in all`}><Flags r={r} /></Card>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <Card icon={SlidersHorizontal} title="Rules with most flags" aside="flagged / checked">
              <TopList rows={r.by_rule} label={(x) => ruleName(x.rule_type)} empty="No checks in this period." />
            </Card>
            <Card icon={MapPin} title="Zones with most flags" aside="flagged / checked">
              <TopList rows={r.by_zone} label={(x) => x.zone} empty="No checks in this period." />
            </Card>
            <Card icon={HardHat} title="Safety gear" className="md:col-span-2 xl:col-span-1">
              <div className="grid grid-cols-2 gap-3">
                {[['No helmet', ppe.missing_helmet], ['No vest', ppe.missing_vest], ['Neither', ppe.missing_both], ['Could not tell', ppe.could_not_verify]].map(([k, v]) => (
                  <div key={k} className="rounded-xl border border-line bg-surface-alt px-3 py-3">
                    <p className={`text-2xl font-black tabular-nums leading-none ${v ? 'text-accent' : 'text-ink'}`}>{num(v)}</p>
                    <p className="mt-1.5 text-[0.75rem] font-bold text-ink-muted">{k}</p>
                  </div>
                ))}
              </div>
              <p className="text-[0.75rem] text-ink-faint">{num(ppe.compliant)} checks had everything on.</p>
            </Card>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-4">
        <Scheduled role={role} onSaved={loadHistory} />
        <Card icon={History} title="Past reports">
          {history.loading && <Skeleton rows={2} label="Loading" />}
          {history.error && <Notice tone="error">{history.error}</Notice>}
          {!history.loading && !history.error && history.rows.length === 0 && <p className="text-[0.8125rem] text-ink-muted">None yet. PDFs you download show up here.</p>}
          {history.rows.length > 0 && (
            <ul className="flex flex-col divide-y divide-line-soft">
              {history.rows.map((h) => (
                <li key={h.id} className="py-2 flex items-center gap-3 text-[0.8125rem] min-w-0">
                  <span className="shrink-0 w-10 text-center rounded-md border border-line bg-surface-alt py-0.5 text-[0.6875rem] font-bold text-ink-muted">{h.format}</span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-ink font-bold truncate" title={h.title}>{/^Scheduled /.test(h.title || '') ? 'Scheduled · ' : ''}{(h.title || '').replace(/^Scheduled /, '')}</span>
                    <span className="block text-[0.75rem] text-ink-faint">{fmtWhen(h.createdAt, tz)}</span>
                  </span>
                  <button type="button" className={BTN_QUIET} disabled={again !== null} onClick={() => downloadAgain(h)} aria-label={`Download ${h.title}`} title="Download">
                    {again === h.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Download className="w-3.5 h-3.5" aria-hidden="true" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <p className="flex items-center gap-1.5 text-[0.75rem] text-ink-faint"><ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" /> Counts and times only, never video or names. Every download is logged.</p>
    </div>
  );
}
