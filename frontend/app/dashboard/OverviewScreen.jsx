'use client';

// frontend/app/dashboard/OverviewScreen.jsx
//
// The first screen after signing in. Same bento language as the public landing page: a large greeting card
// beside a few bold tiles, then the period's numbers, the trend, where the flags come from, and what is
// waiting for a person. Every number comes from the database (safety_summary, plan_usage), never estimated
// here. A new organisation with no checks yet gets its three setup steps instead of a wall of zeros.

import React, { useEffect, useState } from 'react';
import {
  ArrowRight, Camera, CheckCircle2, Circle, ClipboardCheck, MapPin, PlayCircle, ShieldCheck, Sparkles,
  TrendingUp, Users,
} from 'lucide-react';
import { getSummary } from '../lib/safetyApi';
import { Notice, Skeleton } from './safety/common';
import SafetyAlertsPanel from './SafetyAlertsPanel';

const CARD = 'bg-surface border border-line rounded-3xl shadow-xl shadow-black/5';
const EYEBROW = 'text-[0.6875rem] font-black uppercase tracking-[0.14em]';

function greeting(date = new Date()) {
  const h = date.getHours();
  return h < 5 ? 'Good evening' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

const shortDay = (iso) => {
  const [y, m, d] = String(iso).split('-').map(Number);
  if (!y) return '';
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
};

const pct = (n, d) => (d > 0 ? Math.round((n / d) * 100) : null);

/** One of the four numbers under the hero. */
function Kpi({ label, value, note, accent }) {
  return (
    <div className="px-6 py-6 flex flex-col items-center text-center gap-1.5">
      <span className={`text-3xl sm:text-[2.125rem] font-black tracking-tight tabular-nums leading-none ${accent ? 'text-accent' : 'text-ink'}`}>{value}</span>
      <span className={`${EYEBROW} text-accent mt-1`}>{label}</span>
      <span className="text-[0.8125rem] text-ink-muted leading-snug">{note}</span>
    </div>
  );
}

/** A heading for the cards lower down. */
function CardHead({ icon: Icon, title, aside }) {
  return (
    <div className="flex items-center justify-between gap-3 mb-5">
      <div className="flex items-center gap-2.5">
        <span className="w-8 h-8 rounded-xl bg-accent-soft text-accent flex items-center justify-center">
          <Icon className="w-4 h-4" strokeWidth={2.4} aria-hidden="true" />
        </span>
        <h3 className="text-[1rem] font-black tracking-tight text-ink">{title}</h3>
      </div>
      {aside && <span className="font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-ink-faint">{aside}</span>}
    </div>
  );
}

/** Decisions per day: a bar each, its flagged share in red. */
function DayChart({ days }) {
  const peak = Math.max(1, ...days.map((x) => x.decisions));
  const dense = days.length > 45;
  const ticks = days.length <= 10 ? days : [days[0], days[Math.floor(days.length / 2)], days[days.length - 1]];
  return (
    <div>
      <div
        className={`flex items-end h-44 ${dense ? 'gap-px' : 'gap-1.5'}`}
        role="img"
        aria-label={`Decisions per day: ${days.map((x) => `${x.day} ${x.decisions}, ${x.potential_violations} flagged`).join('; ')}`}
      >
        {days.map((x) => {
          const h = x.decisions ? Math.max(6, (x.decisions / peak) * 100) : 2;
          return (
            <div key={x.day} className="flex-1 h-full flex flex-col justify-end group" title={`${shortDay(x.day)}: ${x.decisions} checked, ${x.potential_violations} flagged`}>
              <div className={`w-full ${dense ? 'rounded-[1px]' : 'rounded-md'} bg-surface-alt border border-line overflow-hidden flex flex-col justify-end transition-colors group-hover:border-[color:var(--accent)]`} style={{ height: `${h}%` }}>
                {x.potential_violations > 0 && <div className="w-full bg-accent" style={{ height: `${(x.potential_violations / x.decisions) * 100}%` }} />}
              </div>
            </div>
          );
        })}
      </div>
      {/* Every day under its bar on a wide screen; on a phone only the first and last, so nothing wraps. */}
      <div className={`mt-2 hidden sm:flex ${days.length <= 10 ? '' : 'justify-between'} text-[0.75rem] text-ink-faint tabular-nums whitespace-nowrap`}>
        {ticks.map((x) => <span key={x.day} className={days.length <= 10 ? 'flex-1 text-center' : ''}>{shortDay(x.day)}</span>)}
      </div>
      <div className="mt-2 flex sm:hidden justify-between text-[0.75rem] text-ink-faint tabular-nums whitespace-nowrap">
        <span>{shortDay(days[0]?.day)}</span>
        <span>{shortDay(days[days.length - 1]?.day)}</span>
      </div>
      <div className="mt-4 flex items-center gap-5 text-[0.8125rem] text-ink-muted">
        <span className="inline-flex items-center gap-2"><span className="w-3 h-3 rounded-sm bg-surface-alt border border-line" /> checked</span>
        <span className="inline-flex items-center gap-2"><span className="w-3 h-3 rounded-sm bg-accent" /> flagged</span>
      </div>
    </div>
  );
}

/** First-run checklist, for an organisation with no checks yet. */
function SetupSteps({ steps, onNavigate }) {
  return (
    <div className={`${CARD} p-7 sm:p-8`}>
      <CardHead icon={Sparkles} title="Get your first check running" aside={`${steps.filter((s) => s.done).length} of ${steps.length} done`} />
      <ol className="grid md:grid-cols-3 gap-4">
        {steps.map((s, i) => (
          <li key={s.title}>
            <button
              type="button"
              onClick={() => onNavigate(s.view)}
              className={`w-full h-full text-left rounded-2xl border p-5 flex flex-col gap-2 transition-all hover:-translate-y-0.5 hover:border-[color:var(--accent)] ${s.done ? 'border-line bg-surface-alt/60' : 'border-line bg-surface'}`}
            >
              <span className="flex items-center gap-2">
                {s.done
                  ? <CheckCircle2 className="w-5 h-5 text-accent" aria-hidden="true" />
                  : <Circle className="w-5 h-5 text-ink-faint" aria-hidden="true" />}
                <span className="font-mono text-[0.75rem] text-ink-faint">0{i + 1}</span>
              </span>
              <span className="text-[0.9375rem] font-black text-ink">{s.title}</span>
              <span className="text-[0.84375rem] text-ink-muted leading-relaxed">{s.text}</span>
              <span className="mt-auto pt-2 inline-flex items-center gap-1.5 text-[0.8125rem] font-bold text-accent">
                {s.done ? 'Done' : s.cta} {!s.done && <ArrowRight className="w-4 h-4" aria-hidden="true" />}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function OverviewScreen({
  user, orgName, days, rangeLabel, actions, refreshKey, alerts, cameras, activeZones, cameraId, planUsage, onNavigate,
}) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let live = true;
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } })();
    setState((s) => ({ ...s, loading: true }));
    getSummary(days, tz).then((r) => {
      if (!live) return;
      setState(r.ok ? { loading: false, data: r.data, error: null } : { loading: false, data: null, error: r.message });
    });
    return () => { live = false; };
  }, [days, refreshKey]);

  const d = state.data;
  const firstName = (user?.fullName || '').trim().split(/\s+/)[0] || null;
  const waiting = alerts.status === 'ready' || alerts.count > 0 ? alerts.count : (d ? d.open_alerts : 0);
  const judgedFlags = d ? d.compliant + d.potential_violations : 0;
  const compliance = d ? pct(d.compliant, judgedFlags) : null;
  const reviewed = d ? d.confirmed + d.dismissed : 0;
  const agreement = d && d.confirmed_share_of_reviewed != null ? Math.round(Number(d.confirmed_share_of_reviewed) * 100) : null;
  const zones = d ? d.by_zone.filter((z) => z.decisions > 0).sort((a, b) => b.potential_violations - a.potential_violations || b.decisions - a.decisions).slice(0, 5) : [];
  const zonePeak = Math.max(1, ...zones.map((z) => z.decisions));
  const noChecksYet = d && d.total === 0;

  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
  const headline = waiting > 0
    ? { top: `${waiting} flag${waiting === 1 ? '' : 's'}`, bottom: 'waiting for you.' }
    : noChecksYet
      ? { top: 'Your site,', bottom: 'ready to watch.' }
      : { top: 'All clear.', bottom: 'Nothing waiting.' };

  const steps = [
    { title: 'Add a camera', text: 'Register the camera or recording the checks will use.', view: 'cameras', cta: 'Add a camera', done: cameras.length > 0 },
    { title: 'Draw your zones', text: 'Mark the PPE checkpoint and the restricted area on its view.', view: 'zones', cta: 'Draw zones', done: activeZones.length > 0 },
    { title: 'Run a check', text: 'Upload a video or turn the camera on. Verdicts appear here.', view: 'live', cta: 'Open live feed', done: Boolean(d && d.total > 0) },
  ];

  return (
    <div className="flex flex-col gap-6">
      {/* ── HERO: greeting beside three bold tiles ─────────────────────── */}
      <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-5">
        <div className={`${CARD} p-7 sm:p-9 flex flex-col justify-between gap-8 relative overflow-hidden`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="inline-flex items-center gap-2 bg-accent-soft text-accent px-3 py-1.5 rounded-full">
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              <span className={EYEBROW}>{greeting()}{firstName ? `, ${firstName}` : ''}</span>
            </span>
            <span className="text-[0.8125rem] font-semibold text-ink-faint">{today}</span>
          </div>
          <div>
            <h1 className="text-[2.25rem] sm:text-5xl font-black tracking-tight leading-[1.08] text-ink">
              {headline.top}<br />
              <span className="text-accent">{headline.bottom}</span>
            </h1>
            <p className="mt-4 text-[0.9375rem] sm:text-base text-ink-muted leading-relaxed max-w-md">
              {orgName ? <><span className="font-bold text-ink">{orgName}</span> · </> : null}
              {noChecksYet
                ? 'No checks yet. Three short steps below get the first one running.'
                : waiting > 0
                  ? 'Each one needs a person to look at the evidence and confirm or dismiss it.'
                  : `Every flag from the ${rangeLabel} has been looked at.`}
            </p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3">
            <button
              type="button"
              onClick={() => onNavigate(waiting > 0 ? 'review' : noChecksYet ? steps.find((s) => !s.done)?.view || 'live' : 'live')}
              className="bg-inverse text-inverse px-6 py-3.5 rounded-2xl font-bold text-[0.9375rem] hover:bg-accent hover:-translate-y-0.5 transition-all flex items-center justify-center gap-2.5"
            >
              {waiting > 0 ? 'Review now' : noChecksYet ? 'Start setting up' : 'Open live feed'}
              <ArrowRight className="w-4 h-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => onNavigate(waiting > 0 || noChecksYet ? 'live' : 'reports')}
              className="border-2 border-line text-ink px-6 py-3.5 rounded-2xl font-bold text-[0.9375rem] hover:border-[color:var(--accent)] hover:-translate-y-0.5 transition-all flex items-center justify-center gap-2.5"
            >
              <PlayCircle className="w-4 h-4" aria-hidden="true" />
              {waiting > 0 || noChecksYet ? 'Live feed' : 'Safety report'}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <div className="rounded-3xl border border-line bg-accent-soft p-6 flex flex-col justify-between gap-6 min-h-[11rem]">
            <ShieldCheck className="w-7 h-7 text-accent" strokeWidth={2} aria-hidden="true" />
            <div>
              <p className="text-4xl font-black tracking-tight text-ink tabular-nums leading-none">{compliance == null ? '—' : `${compliance}%`}</p>
              <p className="mt-2 text-[0.9375rem] font-black text-ink">Compliance</p>
              <p className={`${EYEBROW} text-ink-faint mt-1`}>{compliance == null ? 'no checks yet' : `${d.compliant} of ${judgedFlags} checks`}</p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => onNavigate('review')}
            className="text-left rounded-3xl bg-accent text-white p-6 flex flex-col justify-between gap-6 min-h-[11rem] shadow-xl shadow-red-600/20 hover:-translate-y-1 transition-transform"
          >
            <ClipboardCheck className="w-7 h-7" strokeWidth={2} aria-hidden="true" />
            <div>
              <p className="text-4xl font-black tracking-tight tabular-nums leading-none">{waiting}</p>
              <p className="mt-2 text-[0.9375rem] font-black">Waiting for review</p>
              <p className={`${EYEBROW} text-white/80 mt-1`}>{waiting ? 'open the queue' : 'nothing waiting'}</p>
            </div>
          </button>

          <div className={`col-span-2 ${CARD} p-6 flex flex-col gap-4`}>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse" aria-hidden="true" />
              <span className={`${EYEBROW} text-ink-muted`}>Your setup</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              {[
                { icon: Camera, label: 'Cameras', value: planUsage?.cameras?.used ?? cameras.length, max: planUsage?.cameras?.max, view: 'cameras' },
                { icon: MapPin, label: 'Zones', title: cameraId && cameras.length ? `Zones on ${cameraId}` : 'Zones', value: activeZones.length, view: 'zones' },
                { icon: Users, label: 'Members', value: planUsage?.seats?.used ?? '—', max: planUsage?.seats?.max, view: 'members' },
              ].map(({ icon: Icon, label, title, value, max, view }) => (
                <button key={label} type="button" title={title || label} onClick={() => onNavigate(view)} className="text-left rounded-2xl border border-line px-4 py-3.5 hover:border-[color:var(--accent)] transition-colors min-w-0">
                  <Icon className="w-4 h-4 text-accent" aria-hidden="true" />
                  <p className="mt-2 text-2xl font-black text-ink tabular-nums leading-none">
                    {value}{max ? <span className="text-[0.8125rem] font-bold text-ink-faint"> / {max}</span> : null}
                  </p>
                  <p className="mt-1.5 text-[0.75rem] font-bold text-ink-muted truncate">{label}</p>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── THE PERIOD ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4 pt-2">
        <div>
          <p className={`${EYEBROW} text-accent`}>The {rangeLabel}</p>
          <h2 className="mt-1 text-2xl font-black tracking-tight text-ink">How the site did</h2>
        </div>
        {actions}
      </div>

      {state.error && <Notice tone="error">{state.error}</Notice>}
      {state.loading && !d && <div className={`${CARD} p-7`}><Skeleton rows={4} label="Loading the numbers" /></div>}

      {noChecksYet && <SetupSteps steps={steps} onNavigate={onNavigate} />}

      {d && d.total > 0 && (
        <>
          <div className={`${CARD} grid grid-cols-2 lg:grid-cols-4 divide-x divide-y lg:divide-y-0 divide-line overflow-hidden`}>
            <Kpi label="Checked" value={d.total} note="safety decisions made" />
            <Kpi label="Compliant" value={d.compliant} note="checked and fine" />
            <Kpi label="Flagged" value={d.potential_violations} note="for a person to judge" accent={d.potential_violations > 0} />
            <Kpi label="Could not verify" value={d.abstentions} note="the system did not guess" />
          </div>

          <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5">
            <div className={`${CARD} p-7`}>
              <CardHead icon={TrendingUp} title="Checks per day" aside={rangeLabel} />
              <DayChart days={d.by_day} />
            </div>
            <div className={`${CARD} p-7 flex flex-col`}>
              <CardHead icon={MapPin} title="Where flags come from" />
              {zones.length === 0 ? (
                <p className="text-[0.875rem] text-ink-muted">No zone has a decision in this period.</p>
              ) : (
                <ul className="flex flex-col gap-4">
                  {zones.map((z) => (
                    <li key={z.zone_id || z.zone}>
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-[0.9375rem] font-bold text-ink truncate">{z.zone}</span>
                        <span className="text-[0.8125rem] text-ink-muted tabular-nums shrink-0">
                          <span className={z.potential_violations ? 'text-accent font-bold' : ''}>{z.potential_violations}</span> of {z.decisions} flagged
                        </span>
                      </div>
                      <div className="mt-2 h-2 rounded-full bg-surface-alt overflow-hidden flex">
                        <div className="h-full bg-accent" style={{ width: `${(z.potential_violations / zonePeak) * 100}%` }} />
                        <div className="h-full bg-[color:var(--line)]" style={{ width: `${((z.decisions - z.potential_violations) / zonePeak) * 100}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-auto pt-6 grid grid-cols-3 gap-3 border-t border-line-soft">
                {[
                  ['Confirmed', d.confirmed],
                  ['Dismissed', d.dismissed],
                  ['Agreed', agreement == null ? '—' : `${agreement}%`],
                ].map(([label, value]) => (
                  <div key={label} className="pt-4">
                    <p className="text-xl font-black text-ink tabular-nums">{value}</p>
                    <p className="text-[0.75rem] font-bold text-ink-muted">{label}</p>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[0.75rem] text-ink-faint">{reviewed ? `${reviewed} flag${reviewed === 1 ? '' : 's'} judged by a person.` : 'No flag judged by a person yet.'}</p>
            </div>
          </section>
        </>
      )}

      {/* What is waiting, newest first (unchanged list, so a flag reads the same everywhere). */}
      {(waiting > 0 || alerts.status === 'error') && (
        <SafetyAlertsPanel status={alerts.status} count={alerts.count} alerts={alerts.alerts.slice(0, 3)} error={alerts.error} onReview={() => onNavigate('review')} />
      )}
    </div>
  );
}
