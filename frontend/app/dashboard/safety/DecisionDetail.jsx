'use client';

// frontend/app/dashboard/safety/DecisionDetail.jsx
//
// One decision in full: what was decided and why, the numbers behind it, the picture the system kept,
// the worker's zone activity around that moment, the review controls, and any corrective actions.
//
// Used inside the review queue and the events list. A person with view-only access sees everything
// except the controls and the picture (the picture needs a role that can see evidence, and every view
// of it is audited). Wording stays non-accusing: a flag is a prompt to look, never a verdict.

import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, ListChecks, Loader2, Wrench } from 'lucide-react';
import { getDecision, listEvents, reviewDecision } from '../../lib/safetyApi';
import { listAssignees, listCorrectiveActions } from '../../lib/safety/management';
import { createCorrectiveAction } from '../../lib/safety/actions';
import { can } from '../../lib/permissions';
import { workerLabel, describeZoneEvent } from '../../lib/zoneEvents';
import { durationText } from '../../lib/safety/ruleTypes';
import EvidencePicture from '../../components/EvidencePicture';
import {
  BTN_PRIMARY, BTN_QUIET, INPUT, Notice, ReviewChip, REVIEW_REASONS, SeverityChip, StatusChip,
  confidenceText, fmtDate, fmtTime, reasonLabel,
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
  if (rows.length === 0) return <p className="text-xs text-ink-faint">No measurements were stored for this decision.</p>;
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-t border-line/60 py-1">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="text-ink text-right">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Evidence({ evidence, ruleType }) {
  if (evidence && !evidence.items && (ruleType === 'MAX_DWELL' || ruleType === 'REQUIRED_EXIT' || ruleType === 'MAN_DOWN')) return <Facts ruleType={ruleType} evidence={evidence} />;
  const items = evidence && evidence.items ? Object.entries(evidence.items) : [];
  if (items.length === 0) return <p className="text-xs text-ink-faint">No measurements were stored for this decision.</p>;
  return (
    <div className="overflow-x-auto">
      {ruleType === 'PPE_CONTINUOUS' && Number.isFinite(Number(evidence.run_seconds)) && (
        <p className="mb-2 text-[11px] text-ink-muted">
          Checked all the time the person was inside. A required item was clearly not seen without a break for {durationText(evidence.run_seconds)} (flagged after {durationText(evidence.grace_seconds)}).
        </p>
      )}
      <table className="w-full text-xs">
        <caption className="sr-only">What was measured for each required item</caption>
        <thead>
          <tr className="text-left text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint">
            <th scope="col" className="py-1 pr-3 font-medium">Item</th>
            <th scope="col" className="py-1 pr-3 font-medium">Result</th>
            <th scope="col" className="py-1 pr-3 font-medium">Clear frames</th>
            <th scope="col" className="py-1 pr-3 font-medium">Seen in</th>
            <th scope="col" className="py-1 font-medium">Why</th>
          </tr>
        </thead>
        <tbody>
          {items.map(([name, v]) => (
            <tr key={name} className="border-t border-line/60 align-top">
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
        <p className="mt-2 text-[11px] text-ink-faint">The head was hidden in the saved picture, so the helmet may not be visible in it.</p>
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
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-t border-line/60 py-1">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="text-ink tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ title, icon: Icon, children }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.14em] text-ink-faint">
        {Icon && <Icon className="w-3 h-3" aria-hidden="true" />}
        {title}
      </h4>
      {children}
    </section>
  );
}

function ReviewForm({ decision, onDone }) {
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
    const r = await reviewDecision(decision.id, state, reason, note.trim());
    setBusy(false);
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
          <label key={value} className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold cursor-pointer ${state === value ? 'border-[color:var(--accent)] text-ink bg-surface-alt' : 'border-line text-ink-muted'}`}>
            <input type="radio" name={`judgement-${decision.id}`} value={value} checked={state === value} onChange={() => setState(value)} className="accent-[color:var(--accent)]" />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col sm:flex-row gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint sm:w-1/2">
          Reason (optional)
          <select value={reason} onChange={(e) => setReason(e.target.value)} className={INPUT}>
            <option value="">No reason given</option>
            {REVIEW_REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint flex-1">
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

function ActionForm({ decision, onCreated }) {
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState([]);
  const [form, setForm] = useState({ title: '', description: '', priority: decision.severity || 'WARNING', assignedToId: '', dueDate: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || people.length) return;
    listAssignees().then((r) => setPeople(r.people || []));
  }, [open, people.length]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    const r = await createCorrectiveAction({ decisionId: decision.id, ...form });
    setBusy(false);
    if (!r.ok) { setError(r.message); return; }
    setOpen(false);
    setForm({ title: '', description: '', priority: decision.severity || 'WARNING', assignedToId: '', dueDate: '' });
    onCreated();
  }

  if (!open) {
    return <button type="button" onClick={() => setOpen(true)} className={`${BTN_QUIET} self-start`}><Wrench className="w-3.5 h-3.5" aria-hidden="true" /> Create a corrective action</button>;
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-lg border border-line p-3" aria-label="New corrective action">
      <label className="flex flex-col gap-1 text-[11px] text-ink-faint">
        What will be done
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={200} required placeholder="For example: re-brief the shift on helmet use" className={INPUT} />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-ink-faint">
        Details (optional)
        <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={2000} rows={2} className={`${INPUT} resize-y`} />
      </label>
      <div className="flex flex-wrap gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">
          Priority
          <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={INPUT}>
            <option value="INFO">Info</option><option value="WARNING">Warning</option><option value="CRITICAL">Critical</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">
          Assigned to
          <select value={form.assignedToId} onChange={(e) => setForm({ ...form, assignedToId: e.target.value })} className={INPUT}>
            <option value="">Nobody yet</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">
          Due
          <input type="date" value={form.dueDate} onChange={(e) => setForm({ ...form, dueDate: e.target.value })} className={INPUT} />
        </label>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={BTN_PRIMARY}>{busy && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} Save action</button>
        <button type="button" onClick={() => setOpen(false)} className={BTN_QUIET}>Cancel</button>
      </div>
    </form>
  );
}

export default function DecisionDetail({ decisionId, role, onChanged }) {
  const [state, setState] = useState({ loading: true, decision: null, error: null });
  const [events, setEvents] = useState(null);
  const [actions, setActions] = useState([]);
  const canReview = can(role, 'safety.review');
  const canEvidence = can(role, 'safety.evidence.view');
  const canActions = can(role, 'safety.actions.edit');

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

  const loadActions = useCallback(async () => {
    const r = await listCorrectiveActions({ decisionId });
    setActions(r.actions || []);
  }, [decisionId]);
  useEffect(() => { loadActions(); }, [loadActions]);

  if (state.loading) return <p className="flex items-center gap-2 py-3 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading the details…</p>;
  if (state.error) return <Notice tone="error">{state.error}</Notice>;

  const flag = decision.status !== 'COMPLIANT';
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface-alt/40 p-3 sm:p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusChip status={decision.status} />
        <ReviewChip state={decision.reviewState} />
        {decision.status === 'POTENTIAL_VIOLATION' && <SeverityChip severity={decision.severity} />}
        <span className="text-xs text-ink font-medium">{workerLabel(decision.workerKey)}</span>
        <span className="text-xs text-ink-muted">{[decision.camera && decision.camera.name, decision.zone && decision.zone.name].filter(Boolean).join(' · ')}</span>
        <span className="text-xs font-mono text-ink-faint tabular-nums">{fmtTime(decision.observedAt)}</span>
        {confidenceText(decision) && <span className="text-xs text-ink-faint">{confidenceText(decision)}</span>}
      </div>
      <p className="text-sm text-ink leading-relaxed">{decision.reason}</p>
      <p className="text-[11px] text-ink-faint">
        A flag is a prompt for a person to look at the evidence, never a final verdict. {decision.status === 'UNVERIFIABLE' && 'The system could not tell, and did not guess.'}
      </p>

      {decision.has_picture && canEvidence && <EvidencePicture decisionId={decision.id} />}
      {decision.has_picture && !canEvidence && <p className="text-[11px] text-ink-faint">A picture was kept. Only an administrator or manager can open it.</p>}

      <Section title="What was measured" icon={ListChecks}><Evidence evidence={decision.evidence} ruleType={decision.ruleType} /></Section>
      {decision.thresholds && <Section title="The limits it was judged by"><Thresholds thresholds={decision.thresholds} /></Section>}

      <Section title="This worker around that moment">
        {events === null ? <p className="text-xs text-ink-faint">Loading…</p> : events.length === 0 ? <p className="text-xs text-ink-faint">No zone activity was recorded for this worker nearby.</p> : (
          <ul className="flex flex-col divide-y divide-line/60 text-xs">
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
      </Section>

      {flag && canReview && (
        <Section title="Your review" icon={ClipboardCheck}>
          <ReviewForm key={decision.reviewedAt || 'new'} decision={decision} onDone={(updated) => { setState({ loading: false, decision: { ...decision, ...updated }, error: null }); if (onChanged) onChanged(updated); }} />
        </Section>
      )}
      {flag && !canReview && <Notice>You have view-only access, so the review controls are not shown. {decision.reviewState === 'PENDING' ? 'An administrator or manager can review this.' : ''}</Notice>}

      {(actions.length > 0 || (decision.reviewState === 'CONFIRMED' && canActions)) && (
        <Section title="Corrective actions" icon={Wrench}>
          {actions.length > 0 && (
            <ul className="flex flex-col divide-y divide-line/60 text-xs">
              {actions.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
                  <span className="text-ink font-medium">{a.title}</span>
                  <span className="text-ink-faint">{a.status.toLowerCase().replace('_', ' ')}</span>
                  {a.dueDate && <span className="text-ink-faint">due {fmtDate(a.dueDate)}</span>}
                </li>
              ))}
            </ul>
          )}
          {decision.reviewState === 'CONFIRMED' && canActions && <ActionForm decision={decision} onCreated={loadActions} />}
        </Section>
      )}
    </div>
  );
}
