'use client';

// frontend/app/dashboard/evaluation/EvaluationScreen.jsx
//
// Step 17's results, in the product rather than only in a terminal.
//
// WHY THIS PAGE EXISTS
//
// Every other screen shows what the system BELIEVES. This one shows how often
// that belief is right, which is the only context in which the other screens
// are readable. "Prajwal was at his desk 6h 12m" means something different
// depending on whether identification is 96% accurate or 76%.
//
// THE TWO NUMBERS, AND WHY THEY ARE NEVER MULTIPLIED
//
// Accuracy is measured over the cases the system ANSWERED. Coverage is how
// many it answered. A single combined figure would have to decide what to do
// with abstentions, and both choices lie: count them wrong and an honest
// system scores worse than a guessing one; count them right and a system that
// abstains on everything scores 100%.
//
// So they sit side by side, the same way the report does.

import React, { useCallback, useEffect, useState } from 'react';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from 'recharts';
import {
  FlaskConical, Loader2, AlertTriangle, CheckCircle2, HelpCircle,
  Target, Layers, ArrowLeftRight, DoorOpen, FileWarning, Info,
} from 'lucide-react';

import DashboardShell from '../DashboardShell';
import { Banner } from '../../components/AuthFormBits';

const ACCEPT = 0.75;

function pct(v) {
  return `${(Number(v ?? 0) * 100).toFixed(1)}%`;
}

/** Chart colours from CSS variables, re-read on a theme change. */
function usePalette() {
  const read = () => {
    if (typeof window === 'undefined') {
      return { good: '#16a34a', warn: '#d97706', bad: '#dc2626',
               grid: '#ECEAE7', axis: '#6B6772', tip: '#fff', line: '#E7E5E9',
               ink: '#0B0A0C' };
    }
    const v = (n, f) => getComputedStyle(document.documentElement)
      .getPropertyValue(n).trim() || f;
    return {
      good: '#16a34a', warn: '#d97706', bad: v('--chart-1', '#dc2626'),
      grid: v('--chart-grid', '#ECEAE7'),
      axis: v('--chart-axis', '#6B6772'),
      tip: v('--chart-tooltip-bg', '#FFFFFF'),
      line: v('--line', '#E7E5E9'),
      ink: v('--ink', '#0B0A0C'),
    };
  };
  const [p, setP] = useState(read);
  useEffect(() => {
    const u = () => setP(read());
    u();
    const o = new MutationObserver(u);
    o.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => o.disconnect();
  }, []);
  return p;
}

function Stat({ icon: Icon, label, value, hint, tone }) {
  const colour = tone === 'good' ? 'text-emerald-700 dark:text-emerald-400'
    : tone === 'bad' ? 'text-accent'
    : tone === 'warn' ? 'text-amber-700 dark:text-amber-400'
    : 'text-ink';
  return (
    <div className="rounded-lg border border-line bg-ground px-3 py-2.5">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint flex items-center gap-1.5">
        {Icon && <Icon className="w-3 h-3" />}
        {label}
      </p>
      <p className={`text-[20px] font-black mt-0.5 leading-none ${colour}`}>{value}</p>
      {hint && <p className="text-[11px] text-ink-faint font-medium mt-1 leading-snug">{hint}</p>}
    </div>
  );
}

function Panel({ title, icon: Icon, children, right }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
        <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
          {Icon && <Icon className="w-4 h-4 text-accent" />}
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export default function EvaluationScreen({ orgName, initialRole, viewer }) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const palette = usePalette();

  const load = useCallback(async () => {
    try {
      // A static artefact, not an API: the evaluation is generated offline by
      // `python -m backend.eval.report` and committed. Fetching it from the
      // public directory keeps this page working with the backend stopped,
      // which matters because it is the page somebody opens to explain the
      // system rather than to operate it.
      const res = await fetch('/eval/evaluation.json', { cache: 'no-store' });
      if (!res.ok) throw new Error('not-generated');
      setState({ data: await res.json(), loading: false, error: null });
    } catch {
      setState({
        data: null, loading: false,
        error: 'No evaluation has been generated yet. Run '
             + '"python -m backend.eval.evaluate" and then '
             + '"python -m backend.eval.report" to produce it.',
      });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const d = state.data;
  const o = d?.overall ?? {};
  const scenarios = d?.scenarios ?? {};
  const simulated = d?.source !== 'hand-labelled';

  const bands = (o.bands ?? []).filter((b) => b.n > 0).map((b) => ({
    name: `${b.low.toFixed(1)}`,
    accuracy: b.n ? Math.round((b.correct / b.n) * 1000) / 10 : 0,
    n: b.n,
    low: b.low,
  }));

  const serious = (d?.failure_summary?.misidentification ?? 0)
    + (d?.failure_summary?.false_name ?? 0)
    + (d?.failure_summary?.hallucination ?? 0);
  const abstentions = d?.failure_summary?.abstention ?? 0;

  const tooltipStyle = {
    backgroundColor: palette.tip, borderColor: palette.line,
    borderRadius: '10px', color: palette.ink, fontSize: '12px',
  };

  return (
    <DashboardShell user={viewer} role={initialRole}>
      <div className="mx-auto max-w-5xl">
        <header className="flex flex-col gap-2 mb-5">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            How accurate is this?
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-2xl">
            Measured, not claimed. Every other screen shows what the system
            believes; this one shows how often that belief is correct, which is
            the context the rest of them need to be read in.
          </p>
        </header>

        {state.loading ? (
          <div className="rounded-xl border border-line bg-surface px-4 py-16 flex items-center justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
          </div>
        ) : state.error ? (
          <div className="rounded-xl border border-line bg-surface px-4 py-10 text-center">
            <FlaskConical className="w-6 h-6 text-ink-faint mx-auto mb-2" strokeWidth={1.8} />
            <p className="text-[14px] font-bold text-ink mb-1">No evaluation yet</p>
            <p className="text-[13px] text-ink-muted font-medium max-w-lg mx-auto leading-relaxed">
              {state.error}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-5">

            {/* THE HEADLINE — the plan's own sentence, computed. */}
            <div className="rounded-xl border-2 border-accent/30 bg-accent-soft px-5 py-4">
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent mb-1.5">
                headline result
              </p>
              <p className="text-[17px] sm:text-[19px] font-black text-ink leading-snug">
                {o.headline}
              </p>
              <p className="text-[12.5px] text-ink-muted font-medium mt-2 leading-relaxed">
                Accuracy counts only the cases the system answered; coverage says
                how many it answered. They are never multiplied together — a
                single number cannot describe a system that is allowed to say
                it does not know.
              </p>
            </div>

            {simulated && (
              <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start gap-2.5">
                <Info className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
                  <span className="font-bold text-ink">These figures come from a simulated office.</span>{' '}
                  The tracker, the stitching, the seat binding, the confidence floor
                  and the aggregation are the real production code — only the
                  appearance vectors are generated, to distributions measured from
                  real OSNet output. Label real footage and every number here is
                  recomputed by the same code.
                </p>
              </div>
            )}

            <Panel title="At a glance" icon={Target}>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat icon={CheckCircle2} label="Accuracy" value={pct(o.accuracy)}
                      hint="of the cases it answered"
                      tone={o.accuracy >= 0.9 ? 'good' : o.accuracy >= 0.75 ? 'warn' : 'bad'} />
                <Stat icon={Layers} label="Coverage" value={pct(o.coverage)}
                      hint="how many it answered" />
                <Stat icon={AlertTriangle} label="Wrong names" value={o.wrong ?? 0}
                      hint="named the wrong person"
                      tone={(o.wrong ?? 0) === 0 ? 'good' : 'bad'} />
                <Stat icon={HelpCircle} label="Correct rejections"
                      value={`${o.correct_rejections ?? 0}/${o.strangers ?? 0}`}
                      hint="refused to name a stranger"
                      tone={(o.strangers ?? 0) > 0 &&
                            o.correct_rejections === o.strangers ? 'good' : undefined} />
              </div>
            </Panel>

            {bands.length > 0 && (
              <Panel title="Accuracy vs confidence" icon={Target}>
                <p className="text-[12.5px] text-ink-muted font-medium mb-3 leading-relaxed">
                  When the system speaks at a given confidence, how often is it
                  right? Bars at or above {ACCEPT.toFixed(2)} are the operating
                  point the plan specifies.
                </p>
                <div className="h-56 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={bands} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={palette.grid} vertical={false} />
                      <XAxis dataKey="name" stroke={palette.axis} tick={{ fontSize: 10 }} />
                      <YAxis stroke={palette.axis} tick={{ fontSize: 10 }} domain={[0, 100]} />
                      <Tooltip
                        contentStyle={tooltipStyle}
                        formatter={(v, _n, e) => [`${v}% correct of ${e?.payload?.n} cases`,
                                                  'Accuracy']}
                        labelFormatter={(l) => `confidence ${l}+`}
                      />
                      <Bar dataKey="accuracy" radius={[5, 5, 0, 0]} isAnimationActive={false}>
                        {bands.map((b) => (
                          <Cell key={b.name}
                                fill={b.low >= ACCEPT ? palette.good
                                     : b.low >= 0.5 ? palette.warn : palette.bad} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-2 text-[11px] font-semibold text-ink-muted">
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: palette.good }} />
                    at or above the {ACCEPT.toFixed(2)} accept threshold
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: palette.warn }} />
                    tentative band
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: palette.bad }} />
                    below the abstention floor
                  </span>
                </div>
              </Panel>
            )}

            <Panel title="By scenario" icon={Layers}>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse min-w-[620px]">
                  <thead>
                    <tr className="border-b border-line">
                      {['Scenario', 'Correct', 'Wrong', 'Abstained', 'Accuracy', 'Coverage']
                        .map((h, i) => (
                          <th key={h}
                              className={`px-3 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-faint ${i ? 'text-right' : ''}`}>
                            {h}
                          </th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(scenarios).map(([name, s]) => (
                      <tr key={name} className="border-b border-line last:border-0">
                        <td className="px-3 py-2.5">
                          <span className="text-[13px] font-bold text-ink capitalize">
                            {name.replace(/_/g, ' ')}
                          </span>
                          <span className="block text-[11px] text-ink-faint font-medium max-w-md">
                            {s.description}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                          {s.correct}
                        </td>
                        <td className={`px-3 py-2.5 text-right text-[13px] font-bold ${
                          s.wrong > 0 ? 'text-accent' : 'text-ink'}`}>
                          {s.wrong}
                        </td>
                        <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink-muted">
                          {s.abstained}
                        </td>
                        <td className={`px-3 py-2.5 text-right text-[13px] font-black ${
                          s.accuracy >= 0.9 ? 'text-emerald-700 dark:text-emerald-400'
                          : s.accuracy >= 0.5 ? 'text-amber-700 dark:text-amber-400'
                          : 'text-accent'}`}>
                          {pct(s.accuracy)}
                        </td>
                        <td className="px-3 py-2.5 text-right text-[13px] font-bold text-ink">
                          {pct(s.coverage)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            {/* Stitching + handoff + chair exits, only where measured. */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
              <Panel title="Fragment stitching" icon={Layers}>
                <p className="text-[12.5px] text-ink-muted font-medium mb-3 leading-relaxed">
                  ByteTrack produces fragments, not people. A <em>wrong</em> join
                  is worse than a missed one — it welds two people&apos;s days
                  together.
                </p>
                {Object.entries(scenarios)
                  .filter(([, s]) => s.stitch?.raw_ids)
                  .map(([name, s]) => (
                    <div key={name} className="flex items-center justify-between gap-3 py-1.5 border-b border-line last:border-0">
                      <span className="text-[12.5px] font-medium text-ink-muted capitalize">
                        {name.replace(/_/g, ' ')}
                      </span>
                      <span className="text-[12.5px] font-bold text-ink whitespace-nowrap">
                        {s.stitch.raw_ids} → {s.stitch.identities} for {s.stitch.people}
                        {s.stitch.wrong_joins > 0 && (
                          <span className="text-accent"> · {s.stitch.wrong_joins} wrong</span>
                        )}
                      </span>
                    </div>
                  ))}
              </Panel>

              <Panel title="Cross-camera handoff" icon={ArrowLeftRight}>
                <p className="text-[12.5px] text-ink-muted font-medium mb-3 leading-relaxed">
                  The plan expects 6–8 out of 10 and calls that a normal result.
                </p>
                {Object.entries(scenarios)
                  .filter(([, s]) => s.handoff?.attempts)
                  .map(([name, s]) => (
                    <div key={name} className="flex items-center justify-between gap-3 py-1.5 border-b border-line last:border-0">
                      <span className="text-[12.5px] font-medium text-ink-muted capitalize">
                        {name.replace(/_/g, ' ')}
                      </span>
                      <span className="text-[12.5px] font-bold text-ink">
                        {s.handoff.correct}/{s.handoff.attempts} correct
                        {s.handoff.wrong > 0 && (
                          <span className="text-accent"> · {s.handoff.wrong} wrong</span>
                        )}
                      </span>
                    </div>
                  ))}
                {!Object.values(scenarios).some((s) => s.handoff?.attempts) && (
                  <p className="text-[12.5px] text-ink-faint font-medium">Not measured in this run.</p>
                )}
              </Panel>
            </div>

            {Object.values(scenarios).some((s) => s.chair_exits?.tp || s.chair_exits?.fp) && (
              <Panel title="Chair exits" icon={DoorOpen}>
                <p className="text-[12.5px] text-ink-muted font-medium mb-3 leading-relaxed">
                  The plan calls this the single most important verification in
                  the whole document.
                </p>
                {Object.entries(scenarios)
                  .filter(([, s]) => s.chair_exits?.tp || s.chair_exits?.fp || s.chair_exits?.fn)
                  .map(([name, s]) => (
                    <div key={name} className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                      <Stat label="Detected" value={s.chair_exits.tp} />
                      <Stat label="False alarms" value={s.chair_exits.fp}
                            tone={s.chair_exits.fp === 0 ? 'good' : 'bad'} />
                      <Stat label="Missed" value={s.chair_exits.fn}
                            tone={s.chair_exits.fn === 0 ? 'good' : 'warn'} />
                      <Stat label="Precision" value={pct(s.chair_exits.precision)} />
                      <Stat label="Recall" value={pct(s.chair_exits.recall)} />
                    </div>
                  ))}
              </Panel>
            )}

            <Panel title="Failures" icon={FileWarning}>
              <div className="grid grid-cols-2 gap-2 mb-3">
                <Stat label="Serious" value={serious}
                      hint="a wrong name, not an abstention"
                      tone={serious === 0 ? 'good' : 'bad'} />
                <Stat label="Abstentions" value={abstentions}
                      hint="declined to guess — by design" />
              </div>
              <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
                An abstention and a wrong name are both &ldquo;not the right
                answer&rdquo;, and treating them as equivalent is how a system
                that guesses confidently comes to look better than one that
                admits doubt. The full catalogue, with the reasoning behind each
                case, is in <code className="font-mono text-[11.5px] bg-ground px-1 py-0.5 rounded">FAILURE_CASES.md</code>.
              </p>
            </Panel>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
