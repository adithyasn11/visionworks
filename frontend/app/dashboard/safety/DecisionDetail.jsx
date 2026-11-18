'use client';

// frontend/app/dashboard/safety/DecisionDetail.jsx
//
// One decision in full: what was decided and why, the numbers behind it, the picture the system kept,
// the worker's zone activity around that moment, and the review controls.
//
// Used inside the review queue and the events list. A person with view-only access sees everything
// except the controls and the picture (the picture needs a role that can see evidence, and every view
// of it is audited). Wording stays non-accusing: a flag is a prompt to look, never a verdict.

import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, ListChecks, Loader2 } from 'lucide-react';
import { getDecision, listEvents, reviewDecision } from '../../lib/safetyApi';
import { can } from '../../lib/permissions';
import { describeZoneEvent } from '../../lib/zoneEvents';
import { durationText } from '../../lib/safety/ruleTypes';
import EvidencePicture from '../../components/EvidencePicture';
import {
  BTN_PRIMARY, BTN_QUIET, INPUT, MoreInfo, Notice, ReviewChip, REVIEW_REASONS, SeverityChip, StatusChip,
  fmtTime, reasonLabel, Skeleton
} from './common';

const ITEM_NAMES = { helmet: 'Helmet', safety_vest: 'Safety vest' };
const ITEM_STATE = { PRESENT: 'Seen', ABSENT: 'Not seen', UNVERIFIABLE: 'Could not tell' };

const OUTCOMES = {
  EXCEEDED: 'The limit was reached',
  WITHIN_LIMIT: 'Left within the limit',
  PARTIAL: 'Already inside when first seen, so only part of the stay was seen',
  LOST: 'Lost from view and did not come back',
  INCOMPLETE: 'The video ended or jumped before the stay finished',
  REACHED_EXIT: 'Reached the exit zone in time',
  REACHED_EXIT_LATE: 'Reached the exit zone, but late',
  OTHER_ZONE: 'Went to another zone first',
  NOT_REACHED: 'Still in view without having reached the exit zone',
  OUT_OF_VIEW: 'Out of view before reaching the exit zone',
  LYING_STILL: 'Lying still for longer than the rule allows',
  UNCLEAR: 'Still, and might be lying down, but the picture is not clear enough to say',
  LONE_WORKER: 'Fewer people were seen than the rule asks for',
  OVER_CAPACITY: 'More people were seen than the rule allows',
  EMPTY_ZONE: 'Nobody was in a zone that must have someone in it',
  CROWDED: 'The head count could not be trusted (people overlapping or partly hidden)',
};

/** The measurements of a decision that is not about helmets and vests: a stay, or the way out of a zone. */
function Facts({ ruleType, evidence }) {
  const sec = (v) => (v !== null && v !== undefined && Number.isFinite(Number(v)) ? durationText(Number(v)) : null);
  let rows = [];
  if (ruleType === 'MAX_DWELL') {
    rows = [
      ['What happened', OUTCOMES[evidence.outcome] || evidence.outcome],
      ['Time in the zone', sec(evidence.dwell_seconds)],
      ['Longest stay allowed', sec(evidence.limit_seconds)],
      ['Warning recorded at', evidence.warn_fraction != null && evidence.warn_fraction < 1 ? `${Math.round(evidence.warn_fraction * 100)}% of the limit` : null],
      ['Already inside when first seen', evidence.first_sighting ? 'Yes, so the stay is counted from then' : null],
      ['Out of view when the limit was reached', evidence.crossed_unobserved ? 'Yes, for part of the time' : null],
    ];
  } else if (ruleType === 'REQUIRED_EXIT') {
    rows = [
      ['What happened', OUTCOMES[evidence.outcome] || evidence.outcome],
      ['Exit zone required', evidence.exit_zone],
      ['Time allowed', sec(evidence.max_seconds_to_exit)],
      ['Time since leaving', sec(evidence.elapsed_seconds)],
      ['Went to instead', evidence.went_to || null],
    ];
  } else if (ruleType === 'OCCUPANCY') {
    rows = [
      ['What happened', OUTCOMES[evidence.outcome] || evidence.outcome],
      ['People seen', evidence.people_seen != null ? String(evidence.people_seen) : null],
      ['Not in view but still counted as there', evidence.hidden_people ? String(evidence.hidden_people) : null],
      ['The rule asks for', evidence.min_people != null ? `at least ${evidence.min_people}` : null],
      ['The rule allows', evidence.max_people != null ? `at most ${evidence.max_people}` : null],
      ['For', sec(evidence.seconds)],
      ['Flagged after', sec(evidence.grace_seconds)],
      ['People clearly countable', evidence.clear_fraction != null ? `${Math.round(evidence.clear_fraction * 100)}% of those counted` : null],
    ];
  } else if (ruleType === 'MAN_DOWN') {
    const lying = evidence.frames ? `${evidence.frames_lying} of ${evidence.frames} frames` : null;
    rows = [
      ['What happened', OUTCOMES[evidence.outcome] || evidence.outcome],
      ['Still for', sec(evidence.seconds_down)],
      ['Time the rule allows', sec(evidence.min_seconds_down)],
      ['Clearly lying in', lying],
      ['Not clear enough to tell in', evidence.frames_unclear ? `${evidence.frames_unclear} of ${evidence.frames} frames` : null],
      ['Torso angle from upright', evidence.torso_angle_deg != null ? `about ${Math.round(evidence.torso_angle_deg)} degrees` : null],
    ];
  }
  rows = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (rows.length === 0) return <p className="text-[0.8125rem] text-ink-faint">No measurements were stored for this decision.</p>;
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-[0.8125rem]">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-t border-line-soft py-1">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="text-ink text-right">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Evidence({ evidence, ruleType }) {
  if (evidence && !evidence.items && (ruleType === 'MAX_DWELL' || ruleType === 'REQUIRED_EXIT' || ruleType === 'MAN_DOWN' || ruleType === 'OCCUPANCY')) return <Facts ruleType={ruleType} evidence={evidence} />;
  const items = evidence && evidence.items ? Object.entries(evidence.items) : [];
  if (items.length === 0) return <p className="text-[0.8125rem] text-ink-faint">No measurements were stored for this decision.</p>;
  return (
    <div className="overflow-x-auto">
      {ruleType === 'PPE_CONTINUOUS' && Number.isFinite(Number(evidence.run_seconds)) && (
        <p className="mb-2 text-xs text-ink-muted">
          Checked all the time the person was inside. A required item was clearly not seen without a break for {durationText(evidence.run_seconds)} (flagged after {durationText(evidence.grace_seconds)}).
        </p>
      )}
      <table className="w-full text-[0.8125rem]">
        <caption className="sr-only">What was measured for each required item</caption>
        <thead>
          <tr className="text-left text-[0.6875rem] font-mono uppercase tracking-[0.12em] text-ink-faint">
            <th scope="col" className="py-1 pr-3 font-medium">Item</th>
            <th scope="col" className="py-1 pr-3 font-medium">Result</th>
            <th scope="col" className="py-1 pr-3 font-medium">Clear frames</th>
            <th scope="col" className="py-1 pr-3 font-medium">Seen in</th>
            <th scope="col" className="py-1 font-medium">Why</th>
          </tr>
        </thead>
        <tbody>
          {items.map(([name, v]) => (
            <tr key={name} className="border-t border-line-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-semibold text-ink">{ITEM_NAMES[name] || name}</th>
              <td className="py-1.5 pr-3 text-ink-muted">{ITEM_STATE[v.state] || v.state}</td>
              <td className="py-1.5 pr-3 tabular-nums text-ink-muted">{v.clear_frames ?? ''}</td>
              <td className="py-1.5 pr-3 tabular-nums text-ink-muted">{Number.isFinite(Number(v.seen_fraction)) ? `${Math.round(v.seen_fraction * 100)}%` : ''}</td>
              <td className="py-1.5 text-ink-faint">{v.why || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {evidence.helmet_region_obscured && (
        <p className="mt-2 text-xs text-ink-faint">The head was hidden in the saved picture, so the helmet may not be visible in it.</p>
      )}
    </div>
  );
}

function Thresholds({ thresholds }) {
  if (!thresholds) return null;
  const rows = [
    ['Frames needed to judge', thresholds.ppe_min_clear_frames],
    ['Seen in at least this share to count as worn', thresholds.ppe_presence_fraction != null ? `${Math.round(thresholds.ppe_presence_fraction * 100)}%` : null],
    ['Not seen in at least this share to count as missing', thresholds.ppe_absence_fraction != null ? `${Math.round(thresholds.ppe_absence_fraction * 100)}%` : null],
    ['Lowest confidence to make a claim', thresholds.min_evidence_confidence != null ? `${Math.round(thresholds.min_evidence_confidence * 100)}%` : null],
    ['Time allowed to gather evidence', thresholds.entry_evidence_wait_s != null ? `${thresholds.entry_evidence_wait_s} s` : null],
    ['Not seen without a break for at least', thresholds.grace_seconds != null ? durationText(thresholds.grace_seconds) : null],
    ['Longest stay allowed', thresholds.max_seconds != null ? durationText(thresholds.max_seconds) : null],
    ['Warning at this share of the limit', thresholds.warn_fraction != null ? `${Math.round(thresholds.warn_fraction * 100)}%` : null],
    ['Lost from view after', thresholds.lost_grace_s != null ? `${thresholds.lost_grace_s} s` : null],
    ['Visit closed as lost after', thresholds.lost_ttl_s != null ? `${thresholds.lost_ttl_s} s` : null],
    ['Time allowed to reach the exit', thresholds.max_seconds_to_exit != null ? durationText(thresholds.max_seconds_to_exit) : null],
    ['Counts as still in view if seen within', thresholds.exit_visible_slack_s != null ? `${thresholds.exit_visible_slack_s} s` : null],
  ].filter(([, v]) => v !== null && v !== undefined);
  if (rows.length === 0) return null;
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-[0.8125rem]">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-t border-line-soft py-1">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="text-ink tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ title, icon: Icon, children }) {
  return (
    <section className="flex flex-col gap-2.5">
      <h4 className="flex items-center gap-1.5 text-[0.6875rem] font-mono uppercase tracking-[0.14em] text-ink-faint">
        {Icon && <Icon className="w-3 h-3" aria-hidden="true" />}
        {title}
      </h4>
      {children}
    </section>
  );
}

function ReviewForm({ decision, onDone, onStale }) {
  const [state, setState] = useState(decision.reviewState === 'CONFIRMED' || decision.reviewState === 'DISMISSED' ? decision.reviewState : '');
  const [reason, setReason] = useState(decision.reviewReasonCode || '');
  const [note, setNote] = useState(decision.reviewNote || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const reviewed = decision.reviewState === 'CONFIRMED' || decision.reviewState === 'DISMISSED';

  async function submit(e) {
    e.preventDefault();
    if (!state) { setError('Choose Confirm or Dismiss.'); return; }
    setBusy(true); setError(null);
    // Sent with the state on screen, so a review someone else made in the meantime is not silently replaced.
    const r = await reviewDecision(decision.id, state, reason, note.trim(), decision.reviewState);
    setBusy(false);
    if (!r.ok && r.status === 409 && onStale) { onStale(r.message); return; }
    if (!r.ok) { setError(r.message); return; }
    onDone(r.data);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Review this decision">
      {reviewed && (
        <Notice>
          Already {decision.reviewState === 'CONFIRMED' ? 'confirmed' : 'dismissed'}
          {decision.reviewedAt ? ` on ${fmtTime(decision.reviewedAt)}` : ''}
          {decision.reviewReasonCode ? `: ${reasonLabel(decision.reviewReasonCode)}` : ''}. You can change your judgement below.
        </Notice>
      )}
      <fieldset className="flex flex-wrap gap-2">
        <legend className="sr-only">Your judgement</legend>
        {[['CONFIRMED', 'Confirm: it was a real violation'], ['DISMISSED', 'Dismiss: it was not']].map(([value, label]) => (
          <label key={value} className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[0.8125rem] font-semibold cursor-pointer ${state === value ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted'}`}>
            <input type="radio" name={`judgement-${decision.id}`} value={value} checked={state === value} onChange={() => setState(value)} className="accent-[color:var(--accent)]" />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col sm:flex-row gap-2">
        <label className="flex flex-col gap-1 text-xs text-ink-faint sm:w-1/2">
          Reason (optional)
          <select value={reason} onChange={(e) => setReason(e.target.value)} className={INPUT}>
            <option value="">No reason given</option>
            {REVIEW_REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-faint flex-1">
          Note (optional, up to 500 characters)
          <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2} className={`${INPUT} resize-y`} />
        </label>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={BTN_PRIMARY}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <ClipboardCheck className="w-3.5 h-3.5" aria-hidden="true" />}
          {reviewed ? 'Save my judgement' : 'Save review'}
        </button>
      </div>
    </form>
  );
}

export default function DecisionDetail({ decisionId, role, onChanged, split = false }) {
  const [state, setState] = useState({ loading: true, decision: null, error: null });
  const [events, setEvents] = useState(null);
  // Someone else reviewed this flag while it was open here (the API refused with 409); said above the form.
  const [stale, setStale] = useState(null);
  const canReview = can(role, 'safety.review');
  const canEvidence = can(role, 'safety.evidence.view');

  const load = useCallback(async () => {
    const r = await getDecision(decisionId);
    if (!r.ok) { setState({ loading: false, decision: null, error: r.message }); return; }
    setState({ loading: false, decision: r.data, error: null });
  }, [decisionId]);

  useEffect(() => { setState({ loading: true, decision: null, error: null }); load(); }, [load]);

  const decision = state.decision;

  // The worker's zone activity around that moment (ten minutes either side, same camera)
  useEffect(() => {
    if (!decision) return;
    const at = new Date(decision.observedAt).getTime();
    if (!Number.isFinite(at)) return;
    let live = true;
    // Ten minutes either side, or the whole stay when the decision is about a long one.
    const stay = Number((decision.evidence || {}).dwell_seconds);
    const before = Math.max(10 * 60000, Number.isFinite(stay) ? (stay + 60) * 1000 : 0);
    listEvents({
      worker_key: decision.workerKey, camera_id: decision.cameraId, limit: 30,
      since: new Date(at - before).toISOString(), until: new Date(at + 10 * 60000).toISOString(),
    }).then((r) => { if (live) setEvents(r.ok ? r.data.events.slice().reverse() : []); });
    return () => { live = false; };
  }, [decision]);

  if (state.loading) return <Skeleton rows={3} label="Loading the details" />;
  if (state.error) return <Notice tone="error">{state.error}</Notice>;

  const flag = decision.status !== 'COMPLIANT';
  const picture = decision.has_picture
    ? (canEvidence
      ? <EvidencePicture decisionId={decision.id} fill={split} />
      : <p className={`text-xs ${split ? 'text-white/70 px-6 text-center' : 'text-ink-faint'}`}>A picture was kept. Only an administrator or manager can open it.</p>)
    : (split ? <p className="text-[0.875rem] text-white/60">No picture was kept for this one.</p> : null);
  const column = (
    <div className={split ? 'flex flex-col gap-5 p-5 min-h-0 overflow-y-auto' : 'flex flex-col gap-5 rounded-xl border border-line bg-surface-alt/40 p-4 sm:p-5'}>
      {/* The row this opens under already says who, where, when and why; here it is only the state, which a review changes. */}
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={decision.status} />
        <ReviewChip state={decision.reviewState} />
        {decision.status === 'POTENTIAL_VIOLATION' && <SeverityChip severity={decision.severity} />}
      </div>
      {decision.status === 'UNVERIFIABLE' && <p className="text-xs text-ink-faint">The system could not tell, and did not guess.</p>}

      {!split && picture}

      <Section title="What was measured" icon={ListChecks}><Evidence evidence={decision.evidence} ruleType={decision.ruleType} /></Section>
      {decision.thresholds && <MoreInfo summary="The limits it was judged by"><Thresholds thresholds={decision.thresholds} /></MoreInfo>}

      <MoreInfo summary="This worker around that moment">
        {events === null ? <Skeleton rows={2} label="Loading the timeline" /> : events.length === 0 ? <p className="text-[0.8125rem] text-ink-faint">No zone activity was recorded for this worker nearby.</p> : (
          <ul className="flex flex-col divide-y divide-line-soft text-[0.8125rem]">
            {events.map((e) => {
              const d = describeZoneEvent({ event_type: e.eventType, worker_key: e.workerKey, details: e.details });
              return (
                <li key={e.id} className="flex gap-3 py-1.5">
                  <span className="w-32 shrink-0 font-mono text-ink-faint tabular-nums">{fmtTime(e.observedAt)}</span>
                  <span className={d.tone === 'unconfirmed' ? 'text-ink-faint' : 'text-ink-muted'}>{d.text}{e.zone && e.zone.name ? ` · ${e.zone.name}` : ''}</span>
                </li>
              );
            })}
          </ul>
        )}
      </MoreInfo>

      {flag && canReview && (
        <Section title="Your review" icon={ClipboardCheck}>
          {stale && <Notice tone="error" className="mb-3">{stale}</Notice>}
          <ReviewForm key={decision.reviewedAt || 'new'} decision={decision}
            onDone={(updated) => { setStale(null); setState({ loading: false, decision: { ...decision, ...updated }, error: null }); if (onChanged) onChanged(updated); }}
            onStale={(message) => { setStale(message); load(); }} />
        </Section>
      )}
      {flag && !canReview && <Notice>You have view-only access, so the review controls are not shown. {decision.reviewState === 'PENDING' ? 'An administrator or manager can review this.' : ''}</Notice>}

    </div>
  );
  if (!split) return column;
  return (
    <div className="grid grid-cols-1 h-full min-h-0 grid-rows-[minmax(12rem,40%)_minmax(0,1fr)] lg:grid-rows-1 lg:grid-cols-[minmax(0,1.4fr)_minmax(22rem,1fr)]">
      <div className="bg-black flex items-center justify-center min-h-0 p-2">{picture}</div>
      {column}
    </div>
  );
}
