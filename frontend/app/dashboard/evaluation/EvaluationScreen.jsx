'use client';

// frontend/app/dashboard/evaluation/EvaluationScreen.jsx
//
// How well the system works, in measured numbers (design §14, Step 17):
//
//   1. Helmet and vest: our evidence layer against two simpler systems, on held-out labelled images, with the threshold
//      sweep (error against coverage). The thesis's main result.
//   2. The scenario suite S1-S10 (+ man-down) through the real engine: correct / abstained / wrong, timing errors.
//   3. The whole chain on real video: speed, alert latency, man-down false alarms where nobody falls.
//   4. The human review loop, from THIS organisation's own reviews.
//   5. What has not been measured yet, said plainly.
//
// The first three come from files the evaluation scripts write (GET /api/v1/safety/evaluation); a result that has not
// been produced shows as "not run yet". A zero is never shown as a bare "0 %": it comes with its 95 % upper bound.

import React, { useEffect, useMemo, useState } from 'react';
import { FlaskConical, Gauge, ListChecks, Loader2, ScanSearch, Users } from 'lucide-react';
import { getEvaluation, getReport } from '../../lib/safetyApi';
import { reasonLabel, MoreInfo, Notice, Panel, Skeleton, Stat } from '../safety/common';

const pc = (v, digits = 1) => (v === null || v === undefined ? 'n/a' : `${(Number(v) * 100).toFixed(digits)}%`);
const when = (iso) => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch { return ''; } };
const siteZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };

function Th({ children, right }) {
  return <th scope="col" className={`py-1 px-2 font-medium align-bottom leading-tight ${right ? 'text-right' : 'text-left'}`}>{children}</th>;
}

function Td({ children, right, strong, tone }) {
  return <td className={`py-1.5 px-2 ${right ? 'text-right tabular-nums' : ''} ${strong ? 'font-semibold text-ink' : 'text-ink-muted'} ${tone === 'bad' ? 'text-accent font-semibold' : ''}`}>{children}</td>;
}

function Table({ caption, head, children }) {
  return (
    <div className="overflow-x-auto">
      {/* Wide enough that the first column (the row's name) is not squeezed into five lines; on a narrow
          screen the table scrolls sideways inside its card instead. */}
      <table className="w-full text-xs min-w-[760px] [&_td:first-child]:min-w-[11rem] [&_th:first-child]:min-w-[11rem]">
        <caption className="sr-only">{caption}</caption>
        <thead><tr className="text-left text-[10px] font-mono uppercase tracking-[0.08em] text-ink-faint">{head}</tr></thead>
        <tbody className="[&>tr]:border-t [&>tr]:border-line-soft">{children}</tbody>
      </table>
    </div>
  );
}

/** "3 of 120 (2.5 %; at most 5.9 %)" or, for a zero, "0 of 120 (at most 2.5 %)". */
function errorCell(k, of, rate, upper) {
  if (!of) return 'n/a';
  return k === 0 ? `0 of ${of} (at most ${pc(upper)})` : `${k} of ${of} (${pc(rate)}; at most ${pc(upper)})`;
}

function Sweep({ sweep, baselines }) {
  const W = 560, H = 260, L = 46, B = 34, T = 10, R = 12;
  const pts = sweep.filter((s) => s.error_rate !== null && s.coverage !== null);
  const all = [...pts.map((s) => s.error_rate), baselines['B0 per-frame']?.error_rate, baselines['B1 majority vote']?.error_rate].filter((v) => v !== null && v !== undefined);
  const yMax = Math.max(0.05, Math.ceil(Math.max(...all) * 20) / 20);
  const x = (c) => L + c * (W - L - R);
  const y = (e) => T + (1 - e / yMax) * (H - T - B);
  const chosen = pts.find((s) => s.presence === 0.6 && s.absence === 0.9 && s.window === 30);
  const marks = { 10: 'circle', 20: 'square', 30: 'triangle', 45: 'diamond' };
  const shape = (s, key) => {
    const cx = x(s.coverage), cy = y(s.error_rate);
    const common = { fill: 'none', stroke: 'var(--field)', strokeWidth: 1.2 };
    if (marks[s.window] === 'square') return <rect key={key} {...common} x={cx - 3.5} y={cy - 3.5} width={7} height={7} />;
    if (marks[s.window] === 'triangle') return <polygon key={key} {...common} points={`${cx},${cy - 4.5} ${cx - 4},${cy + 3} ${cx + 4},${cy + 3}`} />;
    if (marks[s.window] === 'diamond') return <polygon key={key} {...common} points={`${cx},${cy - 4.5} ${cx + 4},${cy} ${cx},${cy + 4.5} ${cx - 4},${cy}`} />;
    return <circle key={key} {...common} cx={cx} cy={cy} r={3.6} />;
  };
  const yTicks = [0, yMax / 4, yMax / 2, (3 * yMax) / 4, yMax];
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[640px]" role="img"
        aria-label={`Error rate against coverage for ${pts.length} threshold settings. As shipped: coverage ${pc(chosen?.coverage)}, error ${pc(chosen?.error_rate)}. Per-frame: coverage 100%, error ${pc(baselines['B0 per-frame']?.error_rate)}. Majority vote: error ${pc(baselines['B1 majority vote']?.error_rate)}.`}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" strokeWidth="0.8" />
            <text x={L - 6} y={y(t) + 3} textAnchor="end" fontSize="9" fill="var(--ink-faint)">{(t * 100).toFixed(t * 100 < 10 ? 1 : 0)}%</text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map((c) => (
          <text key={c} x={x(c)} y={H - B + 14} textAnchor="middle" fontSize="9" fill="var(--ink-faint)">{c * 100}%</text>
        ))}
        <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize="10" fill="var(--ink-muted)">Coverage: share of segments given a verdict</text>
        <text x={12} y={(T + H - B) / 2} textAnchor="middle" fontSize="10" fill="var(--ink-muted)" transform={`rotate(-90 12 ${(T + H - B) / 2})`}>Error among verdicts</text>
        {pts.map((s, i) => shape(s, i))}
        {['B0 per-frame', 'B1 majority vote'].map((name, i) => {
          const r = baselines[name];
          if (!r || r.error_rate === null) return null;
          const cx = x(r.coverage), cy = y(r.error_rate);
          return (
            <g key={name}>
              <path d={i === 0 ? `M${cx - 5},${cy - 5} L${cx + 5},${cy + 5} M${cx + 5},${cy - 5} L${cx - 5},${cy + 5}` : `M${cx - 6},${cy} L${cx + 6},${cy} M${cx},${cy - 6} L${cx},${cy + 6}`} stroke="var(--ink)" strokeWidth="2" />
              <text x={cx - 8} y={cy - 8 - i * 11} textAnchor="end" fontSize="9" fill="var(--ink)">{name}</text>
            </g>
          );
        })}
        {chosen && (
          <g>
            <circle cx={x(chosen.coverage)} cy={y(chosen.error_rate)} r="6.5" fill="var(--accent)" />
            {/* Above the dot, away from the cluster of sweep marks, with a halo in the card colour so it stays
                readable where it crosses a mark or grid line. */}
            <text x={x(chosen.coverage) + 10} y={y(chosen.error_rate) - 10} textAnchor="start" fontSize="9" fontWeight="700"
              fill="var(--accent)" stroke="var(--surface)" strokeWidth="3" paintOrder="stroke" strokeLinejoin="round">as shipped</text>
          </g>
        )}
      </svg>
      <figcaption className="mt-1.5 text-[12px] text-ink-faint leading-relaxed">
        Lower is better; further right decides more often. The red dot is the shipped setting; open marks are the other settings tried.
      </figcaption>
    </figure>
  );
}

function PpeSection({ seg }) {
  if (!seg) return <Notice>The helmet and vest benchmark has not been run yet (<code>ppe_training/eval_segments.py</code>).</Notice>;
  const main = seg.sets?.valB;
  if (!main) return <Notice>The benchmark file has no main result.</Notice>;
  const both = main.results['both items'];
  const order = ['B0 per-frame', 'B1 majority vote', 'ours'];
  const nice = { 'B0 per-frame': 'Per-frame (one frame decides)', 'B1 majority vote': 'Majority vote over the window', ours: 'Ours (clear frames, 60 % / 90 %, may abstain)' };
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-ink-muted leading-relaxed max-w-3xl">
        {main.segments} people from {main.images} images the model never saw, judged by three systems.
      </p>
      <Table caption="Helmet and vest: three systems compared" head={<><Th>System</Th><Th right>Segments</Th><Th right>False accusations (of those wearing it)</Th><Th right>False compliance (of those without it)</Th><Th right>Abstained</Th><Th right>Coverage</Th><Th right>Error among verdicts</Th></>}>
        {order.map((k) => {
          const r = both[k];
          return (
            <tr key={k}>
              <Td strong>{nice[k]}</Td>
              <Td right>{r.n}</Td>
              <Td right tone={r.false_accusation > 0 && k !== 'ours' ? 'bad' : undefined}>{errorCell(r.false_accusation, r.worn, r.false_accusation_rate, r.false_accusation_upper95)}</Td>
              <Td right>{errorCell(r.false_compliance, r.missing, r.false_compliance_rate, r.false_compliance_upper95)}</Td>
              <Td right>{r.abstained}</Td>
              <Td right>{pc(r.coverage)}</Td>
              <Td right>{pc(r.error_rate)}</Td>
            </tr>
          );
        })}
      </Table>
      <Sweep sweep={main.sweep} baselines={both} />
      <MoreInfo summary="How this was measured">
        <p>
          Each person-and-item segment is up to {seg.frames_per_segment} frames of the same still scene through slightly different camera moments (shift, zoom, light, blur,
          compression), each read by the production detector and association. People do not move or turn here, so this measures how the decision copes with a real detector's
          frame-to-frame jitter, not with motion.
        </p>
      </MoreInfo>
      <MoreInfo summary="By how tall the person is in the picture">
        <div>
          <Table caption="False accusations by person height" head={<><Th>Person height</Th><Th right>Segments</Th><Th right>Per-frame</Th><Th right>Majority vote</Th><Th right>Ours</Th><Th right>Ours abstained</Th></>}>
            {Object.entries(main.by_size).map(([label, r]) => (
              <tr key={label}>
                <Td strong>{label}</Td>
                <Td right>{r.ours.n}</Td>
                {['B0 per-frame', 'B1 majority vote', 'ours'].map((k) => <Td key={k} right>{errorCell(r[k].false_accusation, r[k].worn, r[k].false_accusation_rate, r[k].false_accusation_upper95)}</Td>)}
                <Td right>{r.ours.abstained}</Td>
              </tr>
            ))}
          </Table>
        </div>
      </MoreInfo>
    </div>
  );
}

/** The scenario file's outcome codes in the words the rest of the product uses. */
function plainOutcome(text) {
  return String(text || '')
    .replace(/POTENTIAL_VIOLATION/g, 'flag')
    .replace(/UNVERIFIABLE/g, 'could not verify')
    .replace(/COMPLIANT/g, 'compliant')
    .replace(/LONE_WORKER/g, 'lone-worker flag')
    .replace(/CRITICAL/g, 'critical')
    .replace(/\(classifier labels them SITTING\)/g, '');
}

function ScenarioSection({ scen }) {
  if (!scen) return <Notice>The scenario suite has not been run yet (<code>backend/eval/safety_scenarios.py</code>).</Notice>;
  const t = scen.totals || {};
  const timing = (s) => {
    const parts = [];
    if (s.timing?.entry_error_s) parts.push(`entry ±${s.timing.entry_error_s.max_abs.toFixed(2)} s`);
    if (s.timing?.exit_error_s) parts.push(`exit ±${s.timing.exit_error_s.max_abs.toFixed(2)} s`);
    if (s.timing?.dwell_error_s) parts.push(`stay ±${s.timing.dwell_error_s.max_abs.toFixed(2)} s`);
    if (s.time_to_alert_s) parts.push(`alert after ${s.time_to_alert_s.median} s (median)`);
    return parts.join(', ') || '';
  };
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-ink-muted leading-relaxed max-w-3xl">
        <strong className="text-ink">{t.correct} correct, {t.abstained} abstained, {t.wrong} wrong</strong> in {t.runs} scripted runs through the real engine.
      </p>
      <MoreInfo summary={`See all ${scen.scenarios.length} scenarios`}>
      <Table caption="Scenario suite" head={<><Th>Scenario</Th><Th>Expected</Th><Th right>Runs</Th><Th right>Correct</Th><Th right>Abstained</Th><Th right>Wrong</Th><Th>Timing (worst run)</Th></>}>
        {scen.scenarios.map((s) => (
          <tr key={s.id}>
            <Td strong>{s.id} · {s.title}</Td>
            <Td>{plainOutcome(s.expected)}</Td>
            <Td right>{s.runs}</Td>
            <Td right>{s.correct}</Td>
            <Td right>{s.abstained}</Td>
            <Td right tone={s.wrong ? 'bad' : undefined}>{s.wrong}</Td>
            <Td>{timing(s)}</Td>
          </tr>
        ))}
      </Table>
      </MoreInfo>
      <MoreInfo summary="How this was measured">
        <p>
          Each scenario runs {scen.variants_per_scenario} times (at 10, 15 and 25 frames a second, with timing and positions varied) through the zone tracker, rules, evidence layer,
          man-down, head count and the database. What the models would see is scripted; the S1n to S3n rows use the PPE model's measured per-frame error rates instead of a
          perfect detector.
        </p>
      </MoreInfo>
    </div>
  );
}

function FootageSection({ real, scen }) {
  if (!real) return <Notice>The real-footage run has not been done yet (<code>backend/eval/real_footage.py</code>).</Notice>;
  const hw = real.hardware || {};
  const m1 = scen?.scenarios?.find((s) => s.id === 'M1');
  const md = real.man_down_overall || {};
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-ink-muted leading-relaxed max-w-3xl">
        Real CCTV clips through the full system on <strong className="text-ink">{hw.gpu}</strong>.
      </p>
      <Table caption="Speed on real video" head={<><Th>Clip</Th><Th right>Seconds</Th><Th right>Input</Th><Th right>Frames a second</Th><Th right>ms per frame (95th pct)</Th><Th right>Pose / PPE / safety ms</Th><Th right>Man-down flags</Th></>}>
        {real.clips.map((c) => (
          <tr key={c.clip}>
            <Td strong>{c.clip.replace(/\.(mp4|avi)$/i, '')}</Td>
            <Td right>{c.video_seconds}</Td>
            <Td right>{c.source_resolution}</Td>
            <Td right tone={c.speed.processed_fps < 10 ? 'bad' : undefined}>{c.speed.processed_fps}</Td>
            <Td right>{c.speed.ms_per_frame_mean} ({c.speed.ms_per_frame_p95})</Td>
            <Td right>{c.speed.pose_ms_mean} / {c.speed.ppe_ms_mean} / {c.speed.safety_layer_ms_mean}</Td>
            <Td right tone={c.man_down.flags ? 'bad' : undefined}>{c.man_down.flags}</Td>
          </tr>
        ))}
      </Table>
      <ul className="text-[13px] text-ink-muted leading-relaxed list-disc pl-5 flex flex-col gap-1.5">
        <li>Man-down false alarms: <strong className="text-ink">{md.false_alarms} in {md.video_minutes} minutes</strong> of normal activity (target: at most 1 an hour).</li>
        {m1?.time_to_alert_s && <li>Fall to alert: <strong className="text-ink">{m1.time_to_alert_s.median} s</strong> median, {m1.time_to_alert_s.max} s at most (target 8 s).</li>}
        <li>10 frames a second target: <strong className="text-ink">{real.clips.every((c) => c.speed.processed_fps >= 10) ? 'met on every clip' : 'not met on every clip'}</strong>.</li>
      </ul>
      <MoreInfo summary="How this was measured">
        <p>
          The production chain (pose model on the original frame, tracker, PPE model every {real.config?.ppe_every_n_frames} frames, the safety layer and the database), on
          {' '}{hw.gpu}, {hw.cores} CPU threads{hw.ram_gb ? `, ${hw.ram_gb} GB RAM` : ''}. A decision is made inside the frame that triggers it, so the time from the event to the
          decision is one frame's processing time. The man-down count is from footage where nobody falls: too little video to state an hourly rate with confidence.
        </p>
      </MoreInfo>
    </div>
  );
}

function ReviewSection({ report, error }) {
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!report) return <Skeleton rows={3} label="Loading" />;
  const judged = report.confirmed + report.dismissed;
  if (!report.flagged_by_system) return <p className="text-xs text-ink-muted">No flag has been raised for this organisation in the last year, so there is nothing for people to have judged.</p>;
  return (
    <div className="flex flex-col gap-3 text-[13px] text-ink-muted leading-relaxed">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Flags raised" value={report.flagged_by_system} note="last 12 months" />
        <Stat label="Judged by a person" value={judged} note={`${report.pending} still waiting`} />
        <Stat label="People agreed" value={judged ? pc(report.confirmed_share_of_reviewed) : '—'} note={judged ? `${report.confirmed} confirmed, ${report.dismissed} dismissed` : 'nothing judged yet'} tone="accent" />
        <Stat label="Could not verify" value={report.insufficient_evidence} note={`${report.insufficient_confirmed} later found real`} />
      </div>
      {report.review_reasons?.length > 0 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {report.review_reasons.map((x) => <li key={`${x.state}-${x.reason}`}><span className="text-ink">{x.state === 'CONFIRMED' ? 'Confirmed' : 'Dismissed'}</span>: {x.reason === 'NONE' ? 'no reason given' : reasonLabel(x.reason)} ({x.count})</li>)}
        </ul>
      )}
    </div>
  );
}

export default function EvaluationScreen() {
  const [data, setData] = useState({ loading: true, value: null, error: null });
  const [review, setReview] = useState({ report: null, error: null });

  useEffect(() => {
    let live = true;
    getEvaluation().then((r) => { if (live) setData(r.ok ? { loading: false, value: r.data, error: null } : { loading: false, value: null, error: r.message }); });
    const until = new Date();
    const since = new Date(until.getTime() - 365 * 86400000);
    getReport({ since: since.toISOString(), until: until.toISOString(), tz: siteZone() })
      .then((r) => { if (live) setReview(r.ok ? { report: r.data, error: null } : { report: null, error: r.message }); });
    return () => { live = false; };
  }, []);

  const v = data.value;
  const updated = useMemo(() => {
    if (!v) return '';
    const times = [v.segments?._file_updated, v.scenarios?._file_updated, v.real_footage?._file_updated].filter(Boolean).sort();
    return times.length ? `results from ${when(times[0])}${times.length > 1 ? ` to ${when(times[times.length - 1])}` : ''}` : '';
  }, [v]);

  if (data.loading) return <Skeleton rows={3} label="Loading the results" />;
  if (data.error) return <Notice tone="error">{data.error}</Notice>;

  // The headline numbers, read from the same files as the tables below.
  const both = v?.segments?.sets?.valB?.results?.['both items'];
  const ours = both?.ours, perFrame = both?.['B0 per-frame'];
  const tot = v?.scenarios?.totals;
  const fps = (v?.real_footage?.clips || []).map((c) => c.speed?.processed_fps).filter((x) => typeof x === 'number');

  return (
    <div className="flex flex-col gap-5">
      {(ours || tot || fps.length > 0) && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {ours && <Stat label="Wrongly flagged" value={`${ours.false_accusation} / ${ours.worn}`} note={perFrame ? `${perFrame.false_accusation} with a one-frame check` : undefined} tone="accent" />}
          {ours && <Stat label="Error in verdicts" value={pc(ours.error_rate)} note={perFrame ? `${pc(perFrame.error_rate)} with a one-frame check` : undefined} />}
          {tot && <Stat label="Scenario runs correct" value={`${tot.correct} / ${tot.runs}`} note={`${tot.wrong} wrong`} />}
          {fps.length > 0 && <Stat label="Frames a second" value={`${Math.round(Math.min(...fps))}–${Math.round(Math.max(...fps))}`} note="target 10" />}
        </div>
      )}

      <Panel title="How to read this page" icon={FlaskConical} aside={updated}>
        <dl className="grid gap-3 sm:grid-cols-3 text-[13px] leading-relaxed">
          <div><dt className="font-bold text-ink">False accusation</dt><dd className="text-ink-muted">Flagging someone who was wearing the item. The error that matters most.</dd></div>
          <div><dt className="font-bold text-ink">Abstaining</dt><dd className="text-ink-muted">Saying it could not tell. Not an error: the system refuses to guess.</dd></div>
          <div><dt className="font-bold text-ink">Coverage</dt><dd className="text-ink-muted">How often the system gave a verdict. A zero is shown with its 95 % upper bound.</dd></div>
        </dl>
      </Panel>

      <Panel title="Helmet and vest: our evidence layer against two simpler systems" icon={ScanSearch}><PpeSection seg={v?.segments} /></Panel>
      <Panel title="Scenarios through the real engine" icon={ListChecks}><ScenarioSection scen={v?.scenarios} /></Panel>
      <Panel title="The whole chain on real video" icon={Gauge}><FootageSection real={v?.real_footage} scen={v?.scenarios} /></Panel>
      <Panel title="The human review loop (your organisation)" icon={Users}><ReviewSection report={review.report} error={review.error} /></Panel>

      <Panel title="Not measured yet">
        <ul className="text-[13px] text-ink-muted leading-relaxed list-disc pl-5 flex flex-col gap-1.5">
          <li><strong className="text-ink">Your own footage.</strong> The benchmark uses still images, not people walking and turning in your lighting.</li>
          <li><strong className="text-ink">Real falls.</strong> No staged falls recorded yet; man-down is tested on scripted poses.</li>
          <li><strong className="text-ink">Precision on reviewed clips.</strong> The review numbers above grow as your team judges flags.</li>
        </ul>
      </Panel>
    </div>
  );
}
