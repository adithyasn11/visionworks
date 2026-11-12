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
import { reasonLabel, Notice, Panel } from '../safety/common';

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
      <figcaption className="mt-1 text-[11px] text-ink-faint leading-relaxed">
        Each open mark is one setting of the sweep (presence 0.4 to 0.7, absence 0.7 to 0.95; circle, square, triangle and diamond are windows of 10, 20, 30 and 45 frames).
        Lower is better; further right is more often willing to decide. The red dot is the setting the product ships with.
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
      <p className="text-xs text-ink-muted leading-relaxed">
        {main.segments} person-and-item segments from {main.images} labelled images the model never saw in training or in being chosen. Each segment is up to {seg.frames_per_segment} frames
        of the same still scene through slightly different camera moments (shift, zoom, light, blur, compression), each read by the production detector and association. People do not move or turn in these
        segments, so this measures how the decision copes with a real detector's frame-to-frame jitter, not with motion.
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
      <details className="text-xs">
        <summary className="cursor-pointer text-ink-muted hover:text-ink">By how tall the person is in the picture</summary>
        <div className="mt-2">
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
      </details>
    </div>
  );
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
      <p className="text-xs text-ink-muted leading-relaxed">
        Each scenario runs {scen.variants_per_scenario} times (at 10, 15 and 25 frames a second, with the timing and positions varied) through the real engine: zone tracker, rules, evidence layer, man-down,
        head count and the database. What the models would see is scripted; the S1n to S3n rows use the PPE model's measured per-frame error rates instead of a perfect detector.
        In total: <strong className="text-ink">{t.correct} correct, {t.abstained} abstained, {t.wrong} wrong</strong> of {t.runs} runs.
      </p>
      <Table caption="Scenario suite" head={<><Th>Scenario</Th><Th>Expected</Th><Th right>Runs</Th><Th right>Correct</Th><Th right>Abstained</Th><Th right>Wrong</Th><Th>Timing (worst run)</Th></>}>
        {scen.scenarios.map((s) => (
          <tr key={s.id}>
            <Td strong>{s.id} · {s.title}</Td>
            <Td>{s.expected}</Td>
            <Td right>{s.runs}</Td>
            <Td right>{s.correct}</Td>
            <Td right>{s.abstained}</Td>
            <Td right tone={s.wrong ? 'bad' : undefined}>{s.wrong}</Td>
            <Td>{timing(s)}</Td>
          </tr>
        ))}
      </Table>
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
      <p className="text-xs text-ink-muted leading-relaxed">
        The production chain (pose model on the original frame, tracker, PPE model every {real.config?.ppe_every_n_frames} frames, the safety layer and the database) on real CCTV clips, on
        {' '}<strong className="text-ink">{hw.gpu}</strong>, {hw.cores} CPU threads{hw.ram_gb ? `, ${hw.ram_gb} GB RAM` : ''}. A decision is made inside the frame that triggers it, so the time from the event
        to the decision is one frame's processing time; the live view shows it in the same message.
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
      <ul className="text-xs text-ink-muted leading-relaxed list-disc pl-5 flex flex-col gap-1">
        <li>Man-down on footage where nobody falls: <strong className="text-ink">{md.false_alarms} false alarm{md.false_alarms === 1 ? '' : 's'} in {md.video_minutes} minutes</strong> ({md.false_alarms_per_hour} an hour; target at most 1). Too little video to state an hourly rate with confidence.</li>
        {m1?.time_to_alert_s && <li>Time from a fall to the alert (scenario M1): median {m1.time_to_alert_s.median} s, at most {m1.time_to_alert_s.max} s (target 8 s).</li>}
        <li>Target of 10 processed frames a second with pose and PPE: {real.clips.every((c) => c.speed.processed_fps >= 10) ? 'met on every clip' : 'not met on every clip (see the red rows)'}.</li>
      </ul>
    </div>
  );
}

function ReviewSection({ report, error }) {
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!report) return <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading…</p>;
  const judged = report.confirmed + report.dismissed;
  if (!report.flagged_by_system) return <p className="text-xs text-ink-muted">No flag has been raised for this organisation in the last year, so there is nothing for people to have judged.</p>;
  return (
    <div className="flex flex-col gap-2 text-xs text-ink-muted leading-relaxed">
      <p>
        From this organisation's own reviews over the last year: {report.flagged_by_system} flags raised, {judged} judged by a person.
        {judged ? <> People agreed with <strong className="text-ink">{pc(report.confirmed_share_of_reviewed)}</strong> of the flags they judged ({report.confirmed} confirmed, {report.dismissed} dismissed); this is the human-judged precision of the flags.</> : ' None has been judged yet, so the human-judged precision cannot be stated.'}
        {' '}{report.pending} still waiting.
      </p>
      <p>
        Of the {report.insufficient_evidence} decisions the system could not make, a reviewer looked at {report.insufficient_confirmed + report.insufficient_dismissed}: {report.insufficient_confirmed} turned out to be real, {report.insufficient_dismissed} not.
      </p>
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

  if (data.loading) return <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading the results…</p>;
  if (data.error) return <Notice tone="error">{data.error}</Notice>;

  return (
    <div className="flex flex-col gap-5">
      <Panel title="How to read this page" icon={FlaskConical} aside={updated}>
        <p className="text-xs text-ink-muted leading-relaxed">
          A <strong className="text-ink">false accusation</strong> is flagging someone who was wearing the item: the error that matters most, because a flag can have consequences for a person.
          <strong className="text-ink"> Abstaining</strong> (saying it could not tell) is not an error; it is the system refusing to guess. A count of zero is shown with the most it could be at 95 % confidence,
          never as a bare 0 %. Coverage is how often the system was willing to give a verdict.
        </p>
      </Panel>

      <Panel title="Helmet and vest: our evidence layer against two simpler systems" icon={ScanSearch}><PpeSection seg={v?.segments} /></Panel>
      <Panel title="Scenarios through the real engine" icon={ListChecks}><ScenarioSection scen={v?.scenarios} /></Panel>
      <Panel title="The whole chain on real video" icon={Gauge}><FootageSection real={v?.real_footage} scen={v?.scenarios} /></Panel>
      <Panel title="The human review loop (your organisation)" icon={Users}><ReviewSection report={review.report} error={review.error} /></Panel>

      <Panel title="Not measured yet">
        <ul className="text-xs text-ink-muted leading-relaxed list-disc pl-5 flex flex-col gap-1">
          <li>Helmet and vest on your own recorded footage (people walking, turning and being hidden, in your lighting): the benchmark above uses still, labelled images with camera jitter.</li>
          <li>Man-down recall on staged falls (at least 30 falls in two directions to the camera): no fall footage has been recorded; the scenarios use scripted poses, and the real clips only show that it does not alarm on normal activity.</li>
          <li>Human-judged precision on a reviewed set of clips: the review figures above grow as your team reviews flags.</li>
        </ul>
      </Panel>
    </div>
  );
}
