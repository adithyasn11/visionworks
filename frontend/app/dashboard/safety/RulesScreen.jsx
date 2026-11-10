'use client';

// frontend/app/dashboard/safety/RulesScreen.jsx
//
// What the safety layer checks, in the organisation's own words. A manager writes a rule (what to check, where,
// how serious), the server validates it as they type, and from the NEXT run of a camera it applies.
//
// Be honest about reach: every kind of rule can be saved and checked for mistakes, but only "helmet and vest on
// entry" is acted on by the safety engine today. The screen says so on every rule that is not, rather than
// letting a stored dwell limit look like protection. Everyone in the organisation can read the rules; only an
// administrator or manager can change them (the database enforces that, not this screen).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Pencil, Plus, SlidersHorizontal, Trash2 } from 'lucide-react';
import { createRule, deleteRule, listRules, updateRule, validateRule } from '../../lib/safetyApi';
import { listSafetyZones } from '../../lib/safety/management';
import { RULE_TYPES, SEVERITIES, PPE_ITEMS, defaultParams, friendlyRuleError, isEnforced, ruleBody, ruleSummary, ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { can } from '../../lib/permissions';
import { BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Panel, SeverityChip } from './common';

const BLANK = { id: null, name: '', type: 'PPE_ENTRY', zoneId: '', severity: 'WARNING', isEnabled: true, minEvidenceConfidence: 0.6, params: defaultParams('PPE_ENTRY') };
const ZONE_TYPE_LABELS = { PPE_CHECKPOINT: 'PPE checkpoint', APPROACH: 'Approach', RESTRICTED: 'Restricted', EXIT: 'Exit', GENERAL: 'General' };

function formFromRule(rule) {
  return {
    id: rule.id, name: rule.name, type: rule.type, zoneId: rule.zoneId || '', severity: rule.severity, isEnabled: rule.isEnabled,
    minEvidenceConfidence: rule.minEvidenceConfidence, params: { ...defaultParams(rule.type), ...(rule.params || {}) },
  };
}

function ParamField({ field, form, setParams, zones }) {
  const value = form.params[field.key];
  if (field.when && !field.when(form.params)) return null;
  const id = `rule-${field.key}`;
  if (field.kind === 'ppe') {
    const list = Array.isArray(value) ? value : [];
    return (
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-[11px] text-ink-faint mb-1">{field.label}</legend>
        <div className="flex flex-wrap gap-2">
          {PPE_ITEMS.map((item) => (
            <label key={item.value} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-line text-xs text-ink-muted cursor-pointer">
              <input type="checkbox" checked={list.includes(item.value)} className="accent-[color:var(--accent)]"
                onChange={(e) => setParams({ [field.key]: e.target.checked ? [...list, item.value] : list.filter((x) => x !== item.value) })} />
              {item.label}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }
  if (field.kind === 'select') {
    return (
      <label htmlFor={id} className="flex flex-col gap-1 text-[11px] text-ink-faint">{field.label}
        <select id={id} value={value ?? ''} onChange={(e) => setParams({ [field.key]: e.target.value })} className={INPUT}>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
    );
  }
  if (field.kind === 'zone') {
    const camera = (zones.find((z) => z.id === form.zoneId) || {}).cameraId;
    const options = zones.filter((z) => field.zoneTypes.includes(z.safetyType) && (!camera || z.cameraId === camera));
    return (
      <label htmlFor={id} className="flex flex-col gap-1 text-[11px] text-ink-faint">{field.label}
        <select id={id} value={value ?? ''} onChange={(e) => setParams({ [field.key]: e.target.value })} className={INPUT}>
          <option value="">{options.length ? 'Choose a zone' : `No ${field.zoneTypes.map((t) => ZONE_TYPE_LABELS[t].toLowerCase()).join(' / ')} zone on this camera`}</option>
          {options.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
        </select>
      </label>
    );
  }
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-[11px] text-ink-faint">{field.label}
      <input id={id} type="number" inputMode="decimal" min={field.min} max={field.max} step={field.step} value={value ?? ''}
        onChange={(e) => setParams({ [field.key]: e.target.value })} className={`${INPUT} w-40`} />
    </label>
  );
}

function RuleForm({ initial, zones, onSaved, onCancel }) {
  const [form, setForm] = useState(initial);
  const [check, setCheck] = useState({ state: 'idle' });
  const [saving, setSaving] = useState(false);
  const formRef = useRef(null);
  // The form opens below the list: bring it into view (and focus its first field) so the click visibly did something.
  useEffect(() => { const el = formRef.current; if (el) { el.scrollIntoView({ block: "nearest", behavior: "smooth" }); const f = el.querySelector("input, select"); if (f) f.focus({ preventScroll: true }); } }, []);
  const [error, setError] = useState(null);
  const meta = ruleTypeMeta(form.type);
  const editing = Boolean(form.id);
  const setParams = (patch) => setForm((f) => ({ ...f, params: { ...f.params, ...patch } }));
  const zoneChoices = useMemo(() => zones.filter((z) => meta.zoneTypes.includes(z.safetyType)), [zones, meta]);
  const body = useMemo(() => ruleBody(form), [form]);

  // Ask the server whether this rule is acceptable, shortly after the last change, so a mistake is named
  // while it is being made and not after Save.
  const token = useRef(0);
  useEffect(() => {
    if (!body.name) { setCheck({ state: 'idle' }); return undefined; }
    setCheck({ state: 'checking' });
    const mine = ++token.current;
    const timer = setTimeout(async () => {
      const r = await validateRule(body);
      if (mine !== token.current) return;
      if (!r.ok) setCheck({ state: 'error', message: friendlyRuleError(r.message) });
      else if (r.data.valid) setCheck({ state: 'valid', enforced: r.data.evaluated_by_engine });
      else setCheck({ state: 'invalid', errors: r.data.errors });
    }, 450);
    return () => clearTimeout(timer);
  }, [body]);

  function changeType(type) {
    setForm((f) => ({ ...f, type, zoneId: '', params: defaultParams(type) }));
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true); setError(null);
    const r = editing ? await updateRule(form.id, body) : await createRule(body);
    setSaving(false);
    if (!r.ok) { setError(friendlyRuleError(r.message)); return; }
    onSaved(r.data);
  }

  return (
    <form ref={formRef} onSubmit={save} className="flex flex-col gap-3 rounded-lg border border-line bg-surface-alt/40 p-3 sm:p-4" aria-label={editing ? 'Edit rule' : 'New rule'}>
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint flex-1 min-w-[200px]">Name
          <input value={form.name} maxLength={160} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="For example: Hard hat and vest at the press" className={INPUT} required />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">What to check
          <select value={form.type} disabled={editing} onChange={(e) => changeType(e.target.value)} className={INPUT}>
            {RULE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
      </div>
      <p className="text-[11px] text-ink-faint">{meta.blurb}{editing ? ' The kind of rule cannot be changed; make a new rule instead.' : ''}</p>

      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Where{meta.zoneOptional ? ' (optional)' : ''}
          <select value={form.zoneId} onChange={(e) => setForm({ ...form, zoneId: e.target.value, params: { ...form.params, ...(form.params.checkpoint_zone_id !== undefined ? { checkpoint_zone_id: '' } : {}), ...(form.params.exit_zone_id !== undefined ? { exit_zone_id: '' } : {}) } })} className={INPUT}>
            <option value="">{meta.zoneOptional ? 'Everywhere (every camera)' : 'Choose a zone'}</option>
            {zoneChoices.map((z) => <option key={z.id} value={z.id}>{z.name}{z.camera ? ` (${z.camera})` : ''} · {ZONE_TYPE_LABELS[z.safetyType]}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">How serious
          <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className={INPUT}>
            {SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-ink-faint">Least evidence to make a claim
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="1" step="0.05" value={form.minEvidenceConfidence} onChange={(e) => setForm({ ...form, minEvidenceConfidence: Number(e.target.value) })} aria-label="Least evidence to make a claim" className="accent-[color:var(--accent)]" />
            <span className="w-10 tabular-nums text-ink">{Math.round(form.minEvidenceConfidence * 100)}%</span>
          </div>
        </label>
      </div>
      {zoneChoices.length === 0 && !meta.zoneOptional && (
        <Notice>There is no {meta.zoneTypes.map((t) => ZONE_TYPE_LABELS[t].toLowerCase()).join(' or ')} zone yet. Draw one in Zones and set its type, then come back.</Notice>
      )}

      <div className="flex flex-wrap gap-3">
        {meta.fields.map((f) => <ParamField key={f.key} field={f} form={form} setParams={setParams} zones={zones} />)}
      </div>

      <label className="inline-flex items-center gap-2 text-xs text-ink-muted self-start cursor-pointer">
        <input type="checkbox" checked={form.isEnabled} onChange={(e) => setForm({ ...form, isEnabled: e.target.checked })} className="accent-[color:var(--accent)]" />
        This rule is on
      </label>

      <div aria-live="polite" className="min-h-[1.25rem]">
        {check.state === 'checking' && <p className="flex items-center gap-2 text-xs text-ink-faint"><Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> Checking…</p>}
        {check.state === 'valid' && (
          <Notice tone="ok">
            This rule is valid. {check.enforced ? 'The safety engine acts on it from the next run of a camera.' : 'It will be saved, but the safety engine does not act on this kind of rule yet.'}
          </Notice>
        )}
        {check.state === 'invalid' && <Notice tone="error">{check.errors.map(friendlyRuleError).join(' ')}</Notice>}
        {check.state === 'error' && <Notice tone="error">{check.message}</Notice>}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <button type="submit" disabled={saving || check.state !== 'valid'} className={BTN_PRIMARY}>
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} {editing ? 'Save changes' : 'Create rule'}
        </button>
        <button type="button" onClick={onCancel} className={BTN_QUIET}>Cancel</button>
      </div>
    </form>
  );
}

export default function RulesScreen({ role }) {
  const [state, setState] = useState({ loading: true, rules: [], error: null });
  const [zones, setZones] = useState([]);
  const [editing, setEditing] = useState(null);       // a form state, or null
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [message, setMessage] = useState(null);
  const canEdit = can(role, 'safety.rules.edit');

  const load = useCallback(async () => {
    const r = await listRules();
    if (!r.ok) { setState({ loading: false, rules: [], error: r.message }); return; }
    setState({ loading: false, rules: r.data.rules, error: null });
  }, []);

  useEffect(() => { load(); listSafetyZones().then((r) => setZones(r.zones || [])); }, [load]);

  async function toggle(rule) {
    setMessage(null);
    const r = await updateRule(rule.id, { is_enabled: !rule.isEnabled });
    if (!r.ok) { setMessage({ tone: 'error', text: friendlyRuleError(r.message) }); return; }
    load();
  }

  async function remove(rule) {
    setMessage(null);
    const r = await deleteRule(rule.id);
    setConfirmDelete(null);
    if (!r.ok) { setMessage({ tone: 'error', text: friendlyRuleError(r.message) }); return; }
    setMessage({ tone: 'ok', text: `"${rule.name}" was deleted. Decisions it made keep their record.` });
    load();
  }

  return (
    <div className="space-y-5">
      <Panel title="Safety rules" icon={SlidersHorizontal} aside={state.loading ? '' : `${state.rules.length} rule${state.rules.length === 1 ? '' : 's'}`}>
        <Notice className="mb-3">
          Every Restricted zone is already checked for a helmet and a vest by a built-in rule. A rule you write for a zone replaces it there.
          To switch that check off for one zone, create a helmet-and-vest rule for the zone and turn it off. Rules apply from the next run of a camera, not to one already running.
        </Notice>
        {!canEdit && !state.loading && <Notice className="mb-3">You have view-only access: you can read the rules, and an administrator or manager changes them.</Notice>}
        {message && <Notice tone={message.tone} className="mb-3">{message.text}</Notice>}
        {state.loading && <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading the rules…</p>}
        {state.error && <Notice tone="error">{state.error}</Notice>}
        {!state.loading && !state.error && state.rules.length === 0 && <p className="text-xs text-ink-muted leading-relaxed">No rules of your own yet, so the built-in helmet-and-vest check applies to every Restricted zone.</p>}

        <ul className="flex flex-col divide-y divide-line/60" aria-label="Rules">
          {state.rules.map((rule) => {
            const meta = ruleTypeMeta(rule.type);
            return (
              <li key={rule.id} className="py-3 flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                  <span className="text-ink font-semibold">{rule.name}</span>
                  <SeverityChip severity={rule.severity} />
                  <span className={rule.isEnabled ? 'text-ink' : 'text-ink-faint'}>{rule.isEnabled ? 'On' : 'Off'}</span>
                  <span className="text-ink-muted">{meta ? meta.label : rule.type}</span>
                  <span className="text-ink-faint">{[rule.camera && rule.camera.name, rule.zone && rule.zone.name].filter(Boolean).join(' · ') || 'Every camera'}</span>
                </div>
                {rule.evaluated_by_engine && ruleSummary(rule.type, rule.params, (id) => (zones.find((z) => z.id === id) || {}).name) && (
                  <p className="text-[11px] text-ink-muted">{ruleSummary(rule.type, rule.params, (id) => (zones.find((z) => z.id === id) || {}).name)}</p>
                )}
                {!rule.evaluated_by_engine && <p className="text-[11px] text-ink-faint">Saved, but the safety engine does not act on this kind of rule yet.</p>}
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => toggle(rule)} className={BTN_QUIET}>{rule.isEnabled ? 'Turn off' : 'Turn on'}</button>
                    <button type="button" onClick={() => { setEditing(formFromRule(rule)); setMessage(null); }} className={BTN_QUIET}><Pencil className="w-3.5 h-3.5" aria-hidden="true" /> Edit</button>
                    {confirmDelete === rule.id ? (
                      <>
                        <span className="self-center text-xs text-ink-muted">Delete this rule?</span>
                        <button type="button" onClick={() => remove(rule)} className={BTN_PRIMARY}>Yes, delete</button>
                        <button type="button" onClick={() => setConfirmDelete(null)} className={BTN_QUIET}>Keep it</button>
                      </>
                    ) : (
                      <button type="button" onClick={() => setConfirmDelete(rule.id)} className={BTN_QUIET}><Trash2 className="w-3.5 h-3.5" aria-hidden="true" /> Delete</button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {canEdit && !editing && (
          <button type="button" onClick={() => { setEditing({ ...BLANK, params: defaultParams('PPE_ENTRY') }); setMessage(null); }} className={`${BTN_PRIMARY} mt-3`}>
            <Plus className="w-3.5 h-3.5" aria-hidden="true" /> New rule
          </button>
        )}
      </Panel>

      {canEdit && editing && (
        <RuleForm key={editing.id || 'new'} initial={editing} zones={zones}
          onSaved={() => { setEditing(null); setMessage({ tone: 'ok', text: 'The rule was saved. It applies from the next run of a camera.' }); load(); }}
          onCancel={() => setEditing(null)} />
      )}
    </div>
  );
}
