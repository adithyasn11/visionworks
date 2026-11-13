'use client';

// frontend/app/dashboard/safety/ReportsScreen.jsx
//
// The safety report: the numbers for a chosen period on screen, the same report as a PDF or CSV, the row-by-row
// decisions CSV, an audit trail for a shift, and the reports made before.
//
// The figures come from the database (exact, in the site's own time zone) and are laid out here; nothing is
// recomputed in the browser. The words are the review workflow's own so a report can never present a flag nobody
// has judged as a violation:
//   Flagged by system     what the software raised, for a person to judge
//   Confirmed / Dismissed what a person decided
//   Waiting for review    not yet looked at
//   Insufficient evidence what the system could not tell (it did not guess)
// Anyone in the organisation can read and export; every export is audited on the server.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CalendarClock, CalendarRange, Camera, ClipboardList, Clock, Download, FileDown, FileSpreadsheet, FileText, HardHat, History, Loader2, PersonStanding, RefreshCw, Shapes, ShieldCheck, SlidersHorizontal, Timer, Wrench } from 'lucide-react';
import { downloadAuditTrail, downloadExport, downloadReport, getReport, getReportHistory } from '../../lib/safetyApi';
import { durationText, ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { reasonLabel, BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Panel, Skeleton } from './common';
import { getPlanUsage, setReportSchedule } from '../planActions';
import { can } from '../../lib/permissions';
import { planName } from '../../lib/plans';

const PRESETS = [
  { id: '24h', label: 'Last 24 hours', hours: 24 },
  { id: '7d', label: 'Last 7 days', hours: 24 * 7 },
  { id: '30d', label: 'Last 30 days', hours: 24 * 30 },
  { id: '90d', label: 'Last 90 days', hours: 24 * 90 },
];

const pct = (v) => (v === null || v === undefined ? 'n/a' : `${(Number(v) * 100).toFixed(1)}%`);
const secs = (v) => (v === null || v === undefined ? 'n/a' : durationText(Number(v)));
const num = (v) => (v === null || v === undefined ? 'n/a' : Number(v).toLocaleString());
const ruleName = (type) => (ruleTypeMeta(type) || {}).label || type;

const siteZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };
const pad = (n) => String(n).padStart(2, '0');
const localDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localDateTime = (d) => `${localDay(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const MAX_DAYS = 366;

function fmtWhen(iso, tz) {
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: tz }).format(new Date(iso));
  } catch {
    return String(iso);
  }
}

function Tile({ label, value, hint, strong }) {
  return (
    <div className="rounded-lg border border-line bg-surface-alt/40 px-3 py-2.5">
      <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint">{label}</div>
      <div className={`mt-1 text-2xl font-extrabold tabular-nums ${strong ? 'text-accent' : 'text-ink'}`}>{value}</div>
      {hint && <div className="text-[0.6875rem] text-ink-faint leading-snug">{hint}</div>}
    </div>
  );
}

/** A small two-column list of facts. Rows whose value is null are left out. */
function Facts({ rows }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
      {shown.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-t border-line-soft py-1">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="text-ink text-right tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const HEADERS = ['Decisions', 'Compliant', 'Flagged by system', 'Confirmed', 'Dismissed', 'Waiting', 'Insufficient evidence'];

/** The by-rule / by-zone / by-camera table. */
function Breakdown({ title, first, rows, label, empty }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 8);
  if (!rows.length) return <p className="text-xs text-ink-muted">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr className="text-left text-[0.625rem] font-mono uppercase tracking-[0.1em] text-ink-faint">
            <th scope="col" className="py-1 pr-3 font-medium min-w-[9rem]">{first}</th>
            {HEADERS.map((h) => <th key={h} scope="col" className="py-1 px-2 font-medium text-right align-bottom leading-tight">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={`${label(r)}-${i}`} className="border-t border-line-soft">
              <th scope="row" className="py-1.5 pr-3 font-medium text-ink text-left">{label(r)}</th>
              {[r.decisions, r.compliant, r.flagged, r.confirmed, r.dismissed, r.pending, r.insufficient_evidence].map((v, j) => (
                <td key={j} className={`py-1.5 px-2 text-right tabular-nums ${j === 2 && v > 0 ? 'text-accent font-semibold' : 'text-ink-muted'}`}>{v}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 8 && (
        <button type="button" className="mt-2 text-[0.6875rem] text-ink-muted underline hover:text-ink" onClick={() => setAll((a) => !a)}>
          {all ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}

/** Bars for a list of { label, total, flagged }. `flagged` is drawn in the accent colour over the total. */
function Bars({ items, ariaLabel, every = 1 }) {
  const peak = Math.max(1, ...items.map((x) => x.total));
  return (
    <div role="img" aria-label={ariaLabel}>
      <div className="flex items-end gap-[3px] h-20 border-b border-line">
        {items.map((x) => (
          <div key={x.label} className="flex-1 min-w-[3px] flex flex-col justify-end h-full" title={`${x.label}: ${x.total} decisions, ${x.flagged} flagged by system`}>
            <div className="w-full rounded-sm relative" style={{ height: `${(x.total / peak) * 100}%`, minHeight: x.total ? 3 : 0, background: 'var(--field)' }}>
              {x.flagged > 0 && <div className="absolute bottom-0 left-0 right-0 rounded-sm" style={{ height: `${(x.flagged / x.total) * 100}%`, background: 'var(--accent)' }} />}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-[3px] text-[0.5625rem] font-mono text-ink-faint">
        {items.map((x, i) => <span key={x.label} className="flex-1 min-w-[3px] text-center overflow-visible whitespace-nowrap">{i % every === 0 ? x.short : ''}</span>)}
      </div>
    </div>
  );
}

function FlagSplit({ r }) {
  const total = r.flagged_by_system;
  const part = (n) => (total ? (n / total) * 100 : 0);
  const judged = r.confirmed + r.dismissed;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-line-soft" role="img"
        aria-label={`${total} flagged: ${r.confirmed} confirmed, ${r.dismissed} dismissed, ${r.pending} waiting for review`}>
        <div style={{ width: `${part(r.confirmed)}%`, background: 'var(--accent)' }} title={`${r.confirmed} confirmed`} />
        <div style={{ width: `${part(r.dismissed)}%`, background: 'var(--field)' }} title={`${r.dismissed} dismissed`} />
        <div style={{ width: `${part(r.pending)}%`, backgroundImage: 'repeating-linear-gradient(45deg, var(--field) 0 3px, transparent 3px 7px)' }} title={`${r.pending} waiting`} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-ink-faint" aria-hidden="true">
        <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--accent)' }} /> confirmed</span>
        <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--field)' }} /> dismissed</span>
        <span className="flex items-center gap-1.5"><i className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundImage: 'repeating-linear-gradient(45deg, var(--field) 0 2px, transparent 2px 4px)' }} /> waiting for review</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        <Tile label="Flagged by system" value={r.flagged_by_system} hint="raised by the software, for a person to judge" />
        <Tile label="Confirmed by reviewer" value={r.confirmed} hint="a person looked and agreed" strong={r.confirmed > 0} />
        <Tile label="Dismissed" value={r.dismissed} hint="a person looked and disagreed" />
        <Tile label="Waiting for review" value={r.pending} hint={r.pending ? 'not yet looked at' : 'nothing waiting'} strong={r.pending > 0} />
      </div>
      <p className="text-xs text-ink-muted leading-relaxed">
        {judged === 0
          ? 'No flag in this period has been judged yet, so none of them is a confirmed violation.'
          : `${judged} of ${r.flagged_by_system} flags have been judged: ${r.confirmed} confirmed and ${r.dismissed} dismissed (people agreed with ${pct(r.confirmed_share_of_reviewed)} of the flags they judged).`}
        {' '}
        {r.insufficient_evidence
          ? `The system could not tell in ${r.insufficient_evidence} decision${r.insufficient_evidence === 1 ? '' : 's'} and did not guess; a reviewer found ${r.insufficient_confirmed} of them real and ${r.insufficient_dismissed} not real.`
          : 'There were no decisions the system could not tell.'}
      </p>
      {r.review_reasons.length > 0 && (
        <div>
          <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint mb-1">Why reviewers decided as they did</div>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
            {r.review_reasons.map((x) => (
              <li key={`${x.state}-${x.reason}`}><span className="text-ink">{x.state === 'CONFIRMED' ? 'Confirmed' : 'Dismissed'}</span>: {x.reason === 'NONE' ? 'no reason given' : reasonLabel(x.reason)} <span className="tabular-nums">({x.count})</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ReportBody({ r }) {
  const tz = r.time_zone;
  const hours = r.by_hour.map((h) => ({ label: `${pad(h.hour)}:00`, short: pad(h.hour), total: h.decisions, flagged: h.flagged }));
  const days = r.by_day.map((d) => ({ label: d.day, short: d.day.slice(8), total: d.decisions, flagged: d.flagged }));
  const m = r.man_down;
  const lone = r.occupancy.lone_worker;
  const over = r.occupancy.over_capacity;
  const z = r.zone_time;
  return (
    <div className="flex flex-col gap-5">
      <Panel title="At a glance" icon={ShieldCheck} aside={`${fmtWhen(r.since, tz)} to ${fmtWhen(r.until, tz)}`}>
        {r.decisions === 0 ? (
          <p className="text-xs text-ink-muted leading-relaxed">No safety decisions in this period. {r.events_recorded ? `${r.events_recorded} zone events were recorded.` : ''}</p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
              <Tile label="Decisions" value={num(r.decisions)} hint={`${num(r.events_recorded)} zone events recorded`} />
              <Tile label="Compliant" value={num(r.compliant)} hint={`compliance rate ${pct(r.compliance_rate)}`} />
              <Tile label="Flagged by system" value={num(r.flagged_by_system)} hint="potential violations, for a person to judge" strong={r.flagged_by_system > 0} />
              <Tile label="Insufficient evidence" value={num(r.insufficient_evidence)} hint={`${pct(r.unverifiable_rate)} of decisions: the system did not guess`} />
            </div>
            <p className="text-[0.6875rem] text-ink-faint leading-relaxed">
              Compliance rate is compliant divided by (compliant plus flagged): of the decisions where the system made a claim. A flag is a potential violation until a person confirms it.
            </p>
          </div>
        )}
      </Panel>

      {r.decisions > 0 && (
        <>
          <Panel title="What happened to the flags" icon={ClipboardList}><FlagSplit r={r} /></Panel>

          <Panel title="By rule" icon={SlidersHorizontal}>
            <Breakdown title="Decisions by rule" first="Rule" rows={r.by_rule} label={(x) => ruleName(x.rule_type)} empty="No decisions." />
          </Panel>
          <Panel title="By zone" icon={Shapes}>
            <Breakdown title="Decisions by zone" first="Zone" rows={r.by_zone} label={(x) => x.zone} empty="No decisions." />
          </Panel>
          <Panel title="By camera" icon={Camera}>
            <Breakdown title="Decisions by camera" first="Camera" rows={r.by_camera} label={(x) => x.camera} empty="No decisions." />
          </Panel>

          <Panel title="When" icon={Clock} aside={`site time, ${tz}`}>
            <div className="flex flex-col gap-4">
              <div>
                <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint mb-1.5">By hour of the day (grey: decisions, red: flagged by system)</div>
                <Bars items={hours} every={3} ariaLabel={`Decisions by hour of the day: ${hours.filter((h) => h.total).map((h) => `${h.label} ${h.total}`).join(', ') || 'none'}`} />
              </div>
              {days.length > 1 && (
                <div>
                  <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint mb-1.5">By day (the first and last day may be partial)</div>
                  <Bars items={days} every={Math.max(1, Math.ceil(days.length / 14))} ariaLabel={`Decisions by day: ${days.map((d) => `${d.label} ${d.total}`).join(', ')}`} />
                </div>
              )}
            </div>
          </Panel>
        </>
      )}

      <Panel title="Helmet and vest" icon={HardHat}>
        <Facts rows={[
          ['Checks that were fine', num(r.ppe.compliant)],
          ['Flagged by system', num(r.ppe.flagged)],
          ['… with no helmet seen', num(r.ppe.missing_helmet)],
          ['… with no vest seen', num(r.ppe.missing_vest)],
          ['… with neither seen', num(r.ppe.missing_both)],
          ['Could not verify', num(r.ppe.could_not_verify)],
        ]} />
        <p className="mt-2 text-[0.6875rem] text-ink-faint leading-relaxed">"Not seen" means the system found none. It is a potential violation until a person confirms it.</p>
      </Panel>

      <Panel title="Time inside restricted zones" icon={Timer}>
        <Facts rows={[
          ['Visits seen from start to finish', num(z.visits_timed)],
          ['Average time per visit (those)', secs(z.average_seconds)],
          ['Longest of those', secs(z.max_seconds)],
          ['Visits seen only in part', num(z.visits_partial)],
          ['Longest time seen in a restricted zone', z.longest_seen_seconds === null || z.longest_seen_seconds === undefined ? 'n/a' : `${z.longest_seen_is_complete ? '' : 'at least '}${secs(z.longest_seen_seconds)}`],
          ['Exits with no time recorded', z.visits_not_timed - z.visits_partial > 0 ? num(z.visits_not_timed - z.visits_partial) : null],
        ]} />
        {z.by_zone.length > 0 && (
          <ul className="mt-3 text-xs text-ink-muted flex flex-col gap-1">
            {z.by_zone.map((x) => (
              <li key={`${x.zone}-${z.by_zone.indexOf(x)}`} className="flex justify-between gap-3 border-t border-line-soft pt-1">
                <span className="text-ink">{x.zone}</span>
                <span className="tabular-nums text-right">
                  {x.visits} from start to finish (average {secs(x.average_seconds)}, longest {secs(x.max_seconds)})
                  {x.partial_visits > 0 ? `; ${x.partial_visits} seen only in part, longest time seen ${(x.longest_seen_seconds || 0) > (x.max_seconds || 0) ? 'at least ' : ''}${secs(x.longest_seen_seconds)}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-[0.6875rem] text-ink-faint leading-relaxed">
          "Seen only in part": already inside at the start, lost from view, or the video stopped. Those stays count as "at least" and are not averaged.
        </p>
      </Panel>

      <Panel title="Man-down and head count" icon={PersonStanding}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">Man-down and head-count incidents</caption>
            <thead>
              <tr className="text-left text-[0.625rem] font-mono uppercase tracking-[0.1em] text-ink-faint">
                <th scope="col" className="py-1 pr-3 font-medium">Incident</th>
                {['Flagged by system', 'Confirmed', 'Dismissed', 'Waiting'].map((h) => <th key={h} scope="col" className="py-1 px-2 font-medium text-right whitespace-nowrap">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {[['Person lying still', m], ['Lone worker (fewer people than required)', lone], ['Over capacity (more people than allowed)', over]].map(([name, x]) => (
                <tr key={name} className="border-t border-line-soft">
                  <th scope="row" className="py-1.5 pr-3 font-medium text-ink text-left">{name}</th>
                  {[x.flagged, x.confirmed, x.dismissed, x.pending].map((v, i) => <td key={i} className="py-1.5 px-2 text-right tabular-nums text-ink-muted">{v}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[0.6875rem] text-ink-faint">Could not verify: man-down {m.could_not_verify}, head count {r.occupancy.could_not_verify}.</p>
      </Panel>

      <Panel title="Corrective actions" icon={Wrench}>
        <Facts rows={[
          ['Opened in this period', num(r.actions.opened)],
          ['Closed in this period', num(r.actions.closed)],
          ['Average days to close', r.actions.average_days_to_close === null ? 'n/a' : `${r.actions.average_days_to_close}`],
          ['Open now', num(r.actions.open_now)],
          ['Overdue now', num(r.actions.overdue_now)],
        ]} />
        <p className="mt-2 text-[0.6875rem] text-ink-faint">Open and overdue are counted as of now.</p>
      </Panel>
    </div>
  );
}

const SCHEDULES = [
  { id: 'OFF', label: 'Off' },
  { id: 'DAILY', label: 'Daily' },
  { id: 'WEEKLY', label: 'Weekly' },
  { id: 'MONTHLY', label: 'Monthly' },
];

/**
 * Scheduled PDF reports (Growth and Enterprise, while the plan is in force). The database makes each one when it
 * is due (029, pg_cron) and lists it with the reports made before; downloading rebuilds it from the data.
 */
function ScheduledReports({ role, onSaved }) {
  const [usage, setUsage] = useState(null);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const canEdit = can(role, 'cameras.edit');           // administrators and managers, as set_report_schedule() checks

  const load = useCallback(async () => {
    const r = await getPlanUsage();
    if (r?.ok) setUsage(r.usage);
  }, []);
  useEffect(() => { load(); }, [load]);

  const choose = async (id) => {
    setBusy(id);
    setMessage(null);
    const r = await setReportSchedule(id);
    setBusy(null);
    setMessage({ tone: r.ok ? 'ok' : 'error', text: r.message });
    if (r.ok) { await load(); onSaved?.(); }
  };

  if (!usage) return <Panel title="Scheduled reports" icon={CalendarClock}><Skeleton rows={1} label="Loading" /></Panel>;

  const locked = usage.effectivePlan === 'FREE';
  const current = usage.reportSchedule || 'OFF';
  return (
    <Panel title="Scheduled reports" icon={CalendarClock} aside={locked ? 'Growth and Enterprise' : null}>
      <div className="flex flex-col gap-3">
        {locked ? (
          <Notice>
            {usage.expired
              ? `Your ${planName(usage.plan)} plan has ended, so scheduled reports are paused. Renew it on the Plan screen to start them again.`
              : 'Scheduled PDF reports are part of the Growth and Enterprise plans. Upgrade on the Plan screen to have a report made for you every day, week or month.'}
          </Notice>
        ) : (
          <>
            <p className="text-xs text-ink-muted leading-relaxed">
              A PDF safety report is made for you on a schedule and added to the reports made before.
            </p>
            <div role="group" aria-label="Schedule" className="flex flex-wrap items-center gap-1.5">
              {SCHEDULES.map((x) => (
                <button
                  key={x.id}
                  type="button"
                  aria-pressed={current === x.id}
                  disabled={!canEdit || busy !== null}
                  onClick={() => current !== x.id && choose(x.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-60 ${current === x.id ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted hover:text-ink'}`}
                >
                  {busy === x.id ? <Loader2 className="inline w-3 h-3 animate-spin mr-1" aria-hidden="true" /> : null}{x.label}
                </button>
              ))}
            </div>
            {current !== 'OFF' && usage.reportNextAt && (
              <p className="text-[0.6875rem] text-ink-faint">
                Next report: {new Date(usage.reportNextAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}.
              </p>
            )}
            {!canEdit && <p className="text-[0.6875rem] text-ink-faint">Only an administrator or manager can change the schedule.</p>}
          </>
        )}
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
      </div>
    </Panel>
  );
}

export default function ReportsScreen({ role }) {
  const [tz, setTz] = useState(siteZone);                 // the browser's own zone; UTC if the database does not know its name
  const [tick, setTick] = useState(0);                     // bumped to re-anchor "now" (a preset chosen again, or Refresh)
  const [preset, setPreset] = useState('7d');
  const today = useMemo(() => new Date(), []);
  const [custom, setCustom] = useState({ from: localDay(new Date(today.getTime() - 6 * 86400000)), to: localDay(today) });
  const [state, setState] = useState({ loading: true, report: null, error: null });
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState({ loading: true, rows: [], error: null });
  const [shift, setShift] = useState(() => {
    // The last eight hours up to the NEXT full hour, so what just happened is inside it (ending at the top of the
    // current hour left the latest minutes out, and an audit trail exported right after an incident came out empty).
    const end = new Date();
    end.setMinutes(0, 0, 0);
    end.setTime(end.getTime() + 3600000);
    return { from: localDateTime(new Date(end.getTime() - 8 * 3600000)), to: localDateTime(end) };
  });
  const request = useRef(0);
  const anchor = useRef(Date.now());                    // "now" for the presets: fixed until a period is chosen again

  /** The period as ISO instants (UTC), or an error sentence. */
  const period = useMemo(() => {
    if (preset === 'custom') {
      if (!custom.from || !custom.to) return { error: 'Choose a first and last day.' };
      const start = new Date(`${custom.from}T00:00:00`);
      const last = new Date(`${custom.to}T00:00:00`);
      const end = new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1);          // the last day is included, up to the next local midnight
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { error: 'Choose a first and last day.' };
      if (end <= start) return { error: 'The last day cannot be before the first.' };
      if (end - start > MAX_DAYS * 86400000) return { error: `A report covers at most ${MAX_DAYS} days.` };
      return { since: start.toISOString(), until: end.toISOString() };
    }
    const p = PRESETS.find((x) => x.id === preset) || PRESETS[1];
    const end = new Date(anchor.current);
    return { since: new Date(end.getTime() - p.hours * 3600000).toISOString(), until: end.toISOString() };
  }, [preset, custom, tick]);

  const load = useCallback(async () => {
    const mine = ++request.current;                      // also retires a request still in flight for an earlier period
    if (period.error) { setState({ loading: false, report: null, error: null }); return; }
    setState((s) => ({ ...s, loading: true, error: null }));
    const r = await getReport({ since: period.since, until: period.until, tz });
    if (mine !== request.current) return;                 // a newer period has taken over
    if (!r.ok && /time zone/i.test(r.message || '') && tz !== 'UTC') { setTz('UTC'); return; }      // load() runs again with UTC
    setState(r.ok ? { loading: false, report: r.data, error: null } : { loading: false, report: null, error: r.message });
  }, [period, tz]);

  useEffect(() => { load(); }, [load]);

  const loadHistory = useCallback(async () => {
    const r = await getReportHistory(10);
    setHistory(r.ok ? { loading: false, rows: r.data.reports || [], error: null } : { loading: false, rows: [], error: r.message });
  }, []);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const choose = (id) => { anchor.current = Date.now(); setPreset(id); setTick((t) => t + 1); setResult(null); };

  // A report from the list, rebuilt for its own period (not listed a second time).
  const [again, setAgain] = useState(null);
  async function downloadAgain(h) {
    setAgain(h.id);
    setResult(null);
    const r = await downloadReport(String(h.format || 'PDF').toLowerCase(), { since: h.rangeStart, until: h.rangeEnd, tz, record: false });
    setAgain(null);
    setResult(r);
  }

  async function run(kind) {
    setBusy(kind);
    setResult(null);
    let r;
    if (kind === 'pdf' || kind === 'csv') r = await downloadReport(kind, { since: period.since, until: period.until, tz });
    else if (kind === 'decisions') r = await downloadExport({ since: period.since, until: period.until });
    else {
      const a = new Date(shift.from);
      const b = new Date(shift.to);
      if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime()) || b <= a) { setBusy(null); setResult({ ok: false, message: 'Choose a shift that starts before it ends.' }); return; }
      r = await downloadAuditTrail({ since: a.toISOString(), until: b.toISOString() });
    }
    setBusy(null);
    setResult(r);
    if (r.ok && (kind === 'pdf' || kind === 'csv')) loadHistory();
  }

  const r = state.report;
  const invalid = Boolean(period.error);
  const shiftBad = !shift.from || !shift.to || new Date(shift.to) <= new Date(shift.from);

  return (
    <div className="flex flex-col gap-5">
      <Panel title="Safety report" icon={CalendarRange} aside={`time zone ${tz}`}>
        <div className="flex flex-col gap-3">
          <div role="group" aria-label="Period" className="flex flex-wrap items-center gap-1.5">
            {PRESETS.map((p) => (
              <button key={p.id} type="button" aria-pressed={preset === p.id} onClick={() => choose(p.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${preset === p.id ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted hover:text-ink'}`}>{p.label}</button>
            ))}
            <button type="button" aria-pressed={preset === 'custom'} onClick={() => choose('custom')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${preset === 'custom' ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted hover:text-ink'}`}>Choose days</button>
            <button type="button" onClick={() => choose(preset)} disabled={state.loading} className={`${BTN_QUIET} ml-auto`}>
              {state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />} Refresh
            </button>
          </div>
          {preset === 'custom' && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-[0.6875rem] text-ink-faint">First day
                <input type="date" value={custom.from} max={custom.to || undefined} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))} className={INPUT} /></label>
              <label className="flex flex-col gap-1 text-[0.6875rem] text-ink-faint">Last day
                <input type="date" value={custom.to} min={custom.from || undefined} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))} className={INPUT} /></label>
            </div>
          )}
          {invalid && <Notice tone="error">{period.error}</Notice>}
          <p className="text-[0.6875rem] text-ink-faint leading-relaxed">
            Counts and times only, never video or names. Hours follow the site's time zone.
          </p>
        </div>
      </Panel>

      {state.loading && !r && <Skeleton rows={3} label="Making the report" />}
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {r && <div className={state.loading ? 'opacity-60 transition-opacity' : ''} aria-busy={state.loading}><ReportBody r={r} /></div>}

      <Panel title="Download" icon={FileDown}>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint">This report, for the period above</div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={BTN_PRIMARY} disabled={invalid || busy !== null} onClick={() => run('pdf')}>
                {busy === 'pdf' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileText className="w-3.5 h-3.5" aria-hidden="true" />} PDF report
              </button>
              <button type="button" className={BTN_QUIET} disabled={invalid || busy !== null} onClick={() => run('csv')}>
                {busy === 'csv' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileSpreadsheet className="w-3.5 h-3.5" aria-hidden="true" />} Figures (CSV)
              </button>
              <button type="button" className={BTN_QUIET} disabled={invalid || busy !== null} onClick={() => run('decisions')}>
                {busy === 'decisions' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileSpreadsheet className="w-3.5 h-3.5" aria-hidden="true" />} Every decision (CSV)
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-2 border-t border-line-soft pt-3">
            <div className="text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-faint">Audit trail for a shift</div>
            <p className="text-xs text-ink-muted leading-relaxed">Every decision and zone event in the period you choose, in order.</p>
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-[0.6875rem] text-ink-faint">Starts
                <input type="datetime-local" value={shift.from} onChange={(e) => setShift((s) => ({ ...s, from: e.target.value }))} className={INPUT} /></label>
              <label className="flex flex-col gap-1 text-[0.6875rem] text-ink-faint">Ends
                <input type="datetime-local" value={shift.to} min={shift.from || undefined} onChange={(e) => setShift((s) => ({ ...s, to: e.target.value }))} className={INPUT} /></label>
              <button type="button" className={BTN_QUIET} disabled={shiftBad || busy !== null} onClick={() => run('trail')}>
                {busy === 'trail' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <FileSpreadsheet className="w-3.5 h-3.5" aria-hidden="true" />} Audit trail (CSV)
              </button>
            </div>
          </div>

          {result && result.ok && (
            <Notice tone="ok">
              {result.name ? `${result.name} was downloaded.` : 'The file was downloaded.'}
              {result.truncated ? ' It holds only the newest records of the period; choose a shorter one to get the rest.' : ''}
              {result.recorded === false ? ' It could not be added to the list of reports made before.' : ''}
              {' '}This download was recorded in the audit log.
            </Notice>
          )}
          {result && !result.ok && <Notice tone="error">{result.message}</Notice>}
        </div>
      </Panel>

      <ScheduledReports role={role} onSaved={loadHistory} />

      <Panel title="Reports made before" icon={History}>
        {history.loading && <Skeleton rows={3} label="Loading" />}
        {history.error && <Notice tone="error">{history.error}</Notice>}
        {!history.loading && !history.error && history.rows.length === 0 && <p className="text-xs text-ink-muted">No reports yet. Your PDF and CSV downloads appear here.</p>}
        {history.rows.length > 0 && (
          <ul className="flex flex-col divide-y divide-line-soft text-xs">
            {history.rows.map((h) => (
              <li key={h.id} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-mono text-ink-faint w-10">{h.format}</span>
                <span className="text-ink min-w-0 flex-1">
                  {/^Scheduled /.test(h.title || '') && (
                    <span className="mr-2 rounded px-1.5 py-[1px] font-mono text-[0.5625rem] font-bold uppercase tracking-wider text-white bg-accent">Scheduled</span>
                  )}
                  {h.title}
                </span>
                <span className="text-ink-faint">made {fmtWhen(h.createdAt, tz)}{h.fileSizeBytes ? `, ${Math.max(1, Math.round(Number(h.fileSizeBytes) / 1024))} KB` : ''}</span>
                <button type="button" className={BTN_QUIET} disabled={again !== null} onClick={() => downloadAgain(h)} aria-label={`Download ${h.title}`}>
                  {again === h.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Download className="w-3.5 h-3.5" aria-hidden="true" />}
                  <span className="hidden sm:inline">Download</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-[0.6875rem] text-ink-faint">Each report is rebuilt from the data when you download it.</p>
      </Panel>
    </div>
  );
}
