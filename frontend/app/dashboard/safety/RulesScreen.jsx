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
import { listCameras } from '../../lib/platform/cameras';
import { backendFetch } from '../../lib/backend';
import { RULE_TYPES, SEVERITIES, PPE_ITEMS, defaultParams, friendlyRuleError, isEnforced, ruleBody, ruleSummary, ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { can } from '../../lib/permissions';
import { BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Panel, SeverityChip, Skeleton } from './common';

const ZONE_TYPE_LABELS = { PPE_CHECKPOINT: 'PPE checkpoint', APPROACH: 'Approach', RESTRICTED: 'Restricted', EXIT: 'Exit', GENERAL: 'General' };

// One form can add several checks for the same place (say, safety gear on entry AND a time limit): each checked kind
// is saved as its own rule, with its own settings, sharing the name, place and seriousness.
const newForm = () => ({ id: null, name: '', types: ['PPE_ENTRY'], paramsByType: { PPE_ENTRY: defaultParams('PPE_ENTRY') }, zoneId: '',
  severity: 'WARNING', isEnabled: true, minEvidenceConfidence: 0.6 });

function formFromRule(rule) {
  return {
    id: rule.id, name: rule.name, types: [rule.type], paramsByType: { [rule.type]: { ...defaultParams(rule.type), ...(rule.params || {}) } },
    zoneId: rule.zoneId || '', severity: rule.severity, isEnabled: rule.isEnabled, minEvidenceConfidence: rule.minEvidenceConfidence,
  };
}

/** What the place must be for every checked kind: the zone types they all accept, and whether "everywhere" is allowed. */
function combinedMeta(types) {
  const metas = types.map(ruleTypeMeta).filter(Boolean);
  if (!metas.length) return { zoneTypes: [], zoneOptional: false };
  return {
    zoneTypes: metas[0].zoneTypes.filter((t) => metas.every((m) => m.zoneTypes.includes(t))),
    zoneOptional: metas.every((m) => m.zoneOptional),
  };
}

// A zone drawn on the Zones page starts as General, which no rule attaches to. Such a zone is still offered here
// (marked), and saving gives it the type the checks need. Only General zones are offered this way, so saving never
// turns a zone that already has a safety meaning into something else. Safety-gear and exit checks need a Restricted
// zone; any other mix gets an Approach zone, which has no built-in safety-gear check.
const mainTarget = (meta) => (meta.zoneTypes.length === 1 ? meta.zoneTypes[0] : meta.zoneTypes.includes('APPROACH') ? 'APPROACH' : meta.zoneTypes[0]);
const ZONE_TYPE_ERROR = /must be anchored to a RESTRICTED zone|needs a real zone|checkpoint_zone_id must be|exit_zone_id must be/i;
// "whole:<camera name>": the camera's full picture. Saving makes a zone covering it (640 x 360, the space zones are in).
const WHOLE = 'whole:';
const wholeCamera = (zoneId) => (typeof zoneId === 'string' && zoneId.startsWith(WHOLE) ? zoneId.slice(WHOLE.length) : null);
const FULL_FRAME = [[0, 0], [640, 0], [640, 360], [0, 360]];
const SOURCE_LABELS = { UPLOAD: 'recorded videos', WEBCAM: 'webcam or phone', RTSP: 'network camera' };

/** The General zones the checks refer to, each with the type it gets when they are saved. */
function zoneChanges(form, zones) {
  const out = [];
  const add = (id, to) => {
    const z = zones.find((x) => x.id === id);
    if (z && z.safetyType === 'GENERAL' && !out.some((c) => c.zone.id === z.id)) out.push({ zone: z, to });
  };
  if (form.zoneId) add(form.zoneId, mainTarget(combinedMeta(form.types)));
  form.types.forEach((t) => {
    const params = form.paramsByType[t] || {};
    (ruleTypeMeta(t)?.fields || []).forEach((f) => {
      if (f.kind === 'zone' && (!f.when || f.when(params)) && params[f.key]) add(params[f.key], f.zoneTypes[0]);
    });
  });
  return out;
}

/** 'exit zone' or 'checkpoint zone' when a rule refers to one that no longer exists, else null. */
function missingZoneRef(rule, zones) {
  const p = rule.params || {};
  const gone = (id) => id && !zones.some((z) => z.id === id);
  if (gone(p.exit_zone_id)) return 'exit zone';
  if (p.mode === 'VIA_CHECKPOINT' && gone(p.checkpoint_zone_id)) return 'checkpoint zone';
  return null;
}

// A length of time the manager types in the unit they think in (seconds, minutes or hours); stored as seconds.
const UNITS = [{ value: 1, label: 'seconds' }, { value: 60, label: 'minutes' }, { value: 3600, label: 'hours' }];
function DurationField({ field, value, onChange }) {
  const secs = Number(value);
  const [unit, setUnit] = useState(() => (secs >= 3600 && secs % 3600 === 0 ? 3600 : secs >= 60 && secs % 60 === 0 ? 60 : 1));
  const [text, setText] = useState(() => (Number.isFinite(secs) && value !== '' && value != null ? String(secs / unit) : ''));
  const send = (t, u) => onChange(t === '' || !Number.isFinite(Number(t)) ? '' : Math.round(Number(t) * u));
  const id = `rule-${field.key}`;
  return (
    <div className="flex flex-col gap-1 text-xs text-ink-faint">
      <label htmlFor={id}>{field.label}</label>
      <div className="flex items-center gap-2">
        <input id={id} type="number" inputMode="decimal" min="0" step="any" value={text} required={field.required}
          onChange={(e) => { setText(e.target.value); send(e.target.value, unit); }} className={`${INPUT} w-24`} />
        <select aria-label={`${field.label}: unit`} value={unit} onChange={(e) => { const u = Number(e.target.value); setUnit(u); send(text, u); }} className={INPUT}>
          {UNITS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
        </select>
      </div>
    </div>
  );
}

function ParamField({ field, form, setParams, zones, cameras = [] }) {
  const value = form.params[field.key];
  if (field.when && !field.when(form.params)) return null;
  const id = `rule-${field.key}`;
  if (field.kind === 'ppe') {
    const list = Array.isArray(value) ? value : [];
    return (
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-xs text-ink-faint mb-1">{field.label}</legend>
        <div className="flex flex-wrap gap-2">
          {PPE_ITEMS.map((item) => (
            <label key={item.value} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-line text-[0.8125rem] text-ink-muted cursor-pointer">
              <input type="checkbox" checked={list.includes(item.value)} className="accent-[color:var(--accent)]"
                onChange={(e) => setParams({ [field.key]: e.target.checked ? [...list, item.value] : list.filter((x) => x !== item.value) })} />
              {item.label}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }
  if (field.kind === 'duration') {
    return <DurationField field={field} value={value} onChange={(v) => setParams({ [field.key]: v })} />;
  }
  if (field.kind === 'select') {
    return (
      <label htmlFor={id} className="flex flex-col gap-1 text-xs text-ink-faint">{field.label}
        <select id={id} value={value ?? ''} onChange={(e) => setParams({ [field.key]: e.target.value })} className={INPUT}>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
    );
  }
  if (field.kind === 'zone') {
    const camera = wholeCamera(form.zoneId)
      ? (cameras.find((c) => c.name === wholeCamera(form.zoneId)) || {}).id
      : (zones.find((z) => z.id === form.zoneId) || {}).cameraId;
    const sameCamera = (z) => !camera || z.cameraId === camera;
    const options = zones.filter((z) => field.zoneTypes.includes(z.safetyType) && sameCamera(z));
    const general = zones.filter((z) => z.safetyType === 'GENERAL' && z.id !== form.zoneId && sameCamera(z));
    return (
      <label htmlFor={id} className="flex flex-col gap-1 text-xs text-ink-faint">{field.label}
        <select id={id} value={value ?? ''} onChange={(e) => setParams({ [field.key]: e.target.value })} className={INPUT}>
          <option value="">{options.length || general.length ? 'Choose a zone' : `No ${field.zoneTypes.map((t) => ZONE_TYPE_LABELS[t].toLowerCase()).join(' / ')} zone on this camera`}</option>
          {options.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
          {general.length > 0 && (
            <optgroup label={`General zones (become ${ZONE_TYPE_LABELS[field.zoneTypes[0]]})`}>
              {general.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </optgroup>
          )}
        </select>
      </label>
    );
  }
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-xs text-ink-faint">{field.label}
      <input id={id} type="number" inputMode="decimal" min={field.min} max={field.max} step={field.step} value={value ?? ''}
        onChange={(e) => setParams({ [field.key]: e.target.value })} className={`${INPUT} w-40`} />
    </label>
  );
}

function RuleForm({ initial, zones, cameras = [], onSaved, onCancel, onGone, onZonesChanged, onListChanged }) {
  const [form, setForm] = useState(initial);
  const [check, setCheck] = useState({ state: 'idle' });
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const formRef = useRef(null);
  // The form opens below the list: bring it into view (and focus its first field) so the click visibly did something.
  useEffect(() => { const el = formRef.current; if (el) { el.scrollIntoView({ block: "nearest", behavior: "smooth" }); const f = el.querySelector("input, select"); if (f) f.focus({ preventScroll: true }); } }, []);
  const [error, setError] = useState(null);
  const editing = Boolean(form.id);
  const meta = useMemo(() => combinedMeta(form.types), [form.types]);
  const zoneChoices = useMemo(() => zones.filter((z) => meta.zoneTypes.includes(z.safetyType)), [zones, meta]);
  const generalZones = useMemo(() => zones.filter((z) => z.safetyType === 'GENERAL'), [zones]);
  const changes = useMemo(() => zoneChanges(form, zones), [form, zones]);
  const whole = wholeCamera(form.zoneId);
  // Every saved camera, with its zones under it; zones whose camera is not in the list (none, or removed) last.
  const groups = useMemo(() => {
    const fits = (z) => meta.zoneTypes.includes(z.safetyType) || z.safetyType === 'GENERAL';
    const out = cameras.map((c) => ({ key: c.id, name: c.name, label: `${c.name} · ${SOURCE_LABELS[c.sourceType] || 'camera'}`, zones: zones.filter((z) => z.cameraId === c.id && fits(z)) }));
    const rest = zones.filter((z) => !cameras.some((c) => c.id === z.cameraId) && fits(z));
    if (rest.length) out.push({ key: 'other', name: null, label: 'Other zones', zones: rest });
    return out;
  }, [cameras, zones, meta]);
  // The camera box: picked by hand, or worked out from the rule's zone (when editing, or once the lists arrive).
  const zoneCamKey = (zoneId) => {
    const w = wholeCamera(zoneId);
    if (w) return (cameras.find((c) => c.name === w) || {}).id || '';
    const z = zones.find((x) => x.id === zoneId);
    if (!z) return '';
    return cameras.some((c) => c.id === z.cameraId) ? z.cameraId : 'other';
  };
  const [pickedCam, setPickedCam] = useState(null);
  useEffect(() => {
    if (pickedCam === null && !form.zoneId && groups.length === 1 && groups[0].name && !meta.zoneOptional) setPickedCam(groups[0].key);
  }, [groups.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const camKey = pickedCam ?? zoneCamKey(form.zoneId) ?? '';
  const camGroup = groups.find((g) => g.key === camKey) || null;
  // A new zone (or camera) clears what the checks' own zone settings (checkpoint, exit) pointed at.
  const setZone = (zoneId) => setForm((f) => ({ ...f, zoneId, paramsByType: Object.fromEntries(Object.entries(f.paramsByType).map(([t, p]) => [t, { ...p, ...(p.checkpoint_zone_id !== undefined ? { checkpoint_zone_id: '' } : {}), ...(p.exit_zone_id !== undefined ? { exit_zone_id: '' } : {}) }])) }));
  const chooseCamera = (key) => { setPickedCam(key); setZone(''); };
  const many = form.types.length > 1;
  const labelOf = (t) => (ruleTypeMeta(t) || {}).label || t;
  // One rule per checked kind; with several, each name says which check it is.
  const bodies = useMemo(() => form.types.map((t) => ruleBody({
    ...form, zoneId: wholeCamera(form.zoneId) ? '' : form.zoneId, type: t, params: form.paramsByType[t] || {}, name: many && form.name.trim() ? `${form.name.trim().slice(0, 157 - labelOf(t).length)} · ${labelOf(t)}` : form.name,
  })), [form, many]); // eslint-disable-line react-hooks/exhaustive-deps

  const setParamsFor = (t) => (patch) => setForm((f) => ({ ...f, paramsByType: { ...f.paramsByType, [t]: { ...f.paramsByType[t], ...patch } } }));
  function toggleType(t, on) {
    setForm((f) => {
      const types = on ? RULE_TYPES.map((x) => x.value).filter((v) => v === t || f.types.includes(v)) : f.types.filter((x) => x !== t);
      const paramsByType = { ...f.paramsByType };
      if (on && !paramsByType[t]) paramsByType[t] = defaultParams(t);
      return { ...f, types, paramsByType };
    });
  }

  // Ask the server whether these rules are acceptable, shortly after the last change, so a mistake is named
  // while it is being made and not after Save.
  const token = useRef(0);
  useEffect(() => {
    if (!form.name.trim() || !bodies.length) { setCheck({ state: 'idle' }); return undefined; }
    setCheck({ state: 'checking' });
    const mine = ++token.current;
    const timer = setTimeout(async () => {
      const results = await Promise.all(bodies.map((body) => validateRule(body)));
      if (mine !== token.current) return;
      const failed = results.find((r) => !r.ok);
      if (failed) { setCheck({ state: 'error', message: friendlyRuleError(failed.message) }); return; }
      const errors = [];
      results.forEach((r, i) => {
        if (r.data.valid) return;
        const own = r.data.errors.filter((x) => !(changes.length && ZONE_TYPE_ERROR.test(x))
          && !(whole && (ZONE_TYPE_ERROR.test(x) || /must be anchored to a zone/i.test(x))));
        own.forEach((x) => errors.push(many ? `${labelOf(form.types[i])}: ${friendlyRuleError(x)}` : friendlyRuleError(x)));
      });
      if (errors.length) setCheck({ state: 'invalid', errors });
      else setCheck({ state: 'valid', enforced: results.every((r) => r.data.valid ? r.data.evaluated_by_engine : true) });
    }, 450);
    return () => clearTimeout(timer);
  }, [bodies, changes.length, whole]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(e) {
    e.preventDefault();
    if (!form.name.trim()) { setError('Give the rule a name.'); return; }
    if (!form.types.length) { setError('Choose at least one check.'); return; }
    if (check.state === 'invalid') { setError(check.errors.join(' ')); return; }
    // A ref, not the `saving` state: a double click sends the second click before React has re-rendered the
    // button as disabled, and that used to create the rule twice.
    if (inFlight.current) return;
    inFlight.current = true;
    setSaving(true); setError(null);
    const stop = (msg) => { inFlight.current = false; setSaving(false); setError(msg); };
    for (const c of changes) {
      try {
        const res = await backendFetch(`/api/v1/zones/${encodeURIComponent(c.zone.slug)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ zone_type: c.to }),
        });
        if (!res.ok) throw new Error(String(res.status));
      } catch {
        stop(`Could not make "${c.zone.name}" ${/^[AEIOU]/.test(ZONE_TYPE_LABELS[c.to]) ? 'an' : 'a'} ${ZONE_TYPE_LABELS[c.to]} zone. Change its type on the Zones page, then try again.`);
        return;
      }
    }
    if (changes.length && onZonesChanged) await onZonesChanged();

    let finalBodies = bodies;
    if (whole) {
      const taken = new Set(zones.filter((z) => z.camera === whole).map((z) => z.name.toLowerCase()));
      let zname = 'Whole view';
      for (let i = 2; taken.has(zname.toLowerCase()); i += 1) zname = `Whole view ${i}`;
      const slug = `zone_${Date.now().toString(36)}`;
      let fresh = null;
      try {
        const res = await backendFetch('/api/v1/zones/', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ zone_id: slug, camera_id: whole, zone_name: zname, zone_type: mainTarget(meta), polygon_coordinates: FULL_FRAME }),
        });
        if (!res.ok) throw new Error(String(res.status));
        const list = onZonesChanged ? await onZonesChanged() : [];
        fresh = (list || []).find((z) => z.slug === slug) || null;
      } catch { /* reported below */ }
      if (!fresh) { stop(`Could not add a whole-picture zone to "${whole}". Draw one on the Zones page, then try again.`); return; }
      setForm((f) => ({ ...f, zoneId: fresh.id }));       // a retry after a refused rule reuses it
      finalBodies = bodies.map((b) => ({ ...b, zone_id: fresh.id }));
    }

    if (editing) {
      // An update may not carry `type` (a rule's kind cannot change, and the API refuses unknown fields).
      const { type: _kind, ...patch } = finalBodies[0];
      const r = await updateRule(form.id, patch);
      inFlight.current = false; setSaving(false);
      // Deleted meanwhile (in another window, or by someone else): close the form and refresh the list.
      if (!r.ok && r.status === 404 && onGone) { onGone(); return; }
      if (!r.ok) { setError(friendlyRuleError(r.message)); return; }
      onSaved(1);
      return;
    }

    // One rule per check, in order. If one is refused, the ones already saved stay saved and leave the form, so
    // pressing Create again does not add them twice.
    const saved = [];
    for (let i = 0; i < finalBodies.length; i += 1) {
      const r = await createRule(finalBodies[i]);
      if (!r.ok) {
        const t = form.types[i];
        if (saved.length) {
          setForm((f) => ({ ...f, types: f.types.filter((x) => !saved.includes(x)) }));
          if (onListChanged) onListChanged();
        }
        stop(`${saved.length ? `Saved: ${saved.map(labelOf).join(', ')}. ` : ''}${many ? `${labelOf(t)}: ` : ''}${friendlyRuleError(r.message)}`);
        return;
      }
      saved.push(form.types[i]);
    }
    inFlight.current = false; setSaving(false);
    onSaved(saved.length);
  }

  return (
    <form ref={formRef} onSubmit={save} className="flex flex-col gap-3 rounded-lg border border-line bg-surface-alt/40 p-3 sm:p-4" aria-label={editing ? 'Edit rule' : 'New rule'}>
      <label className="flex flex-col gap-1 text-xs text-ink-faint">Name
        <input value={form.name} maxLength={140} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="For example: Press area" className={INPUT} required />
      </label>

      {/* What to check: one or more. Each checked kind has its own settings below. */}
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-xs text-ink-faint mb-1">What to check{editing ? '' : ' (choose one or more)'}</legend>
        {editing ? (
          <p className="text-[0.875rem] font-bold text-ink">{labelOf(form.types[0])}</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
            {RULE_TYPES.filter((t) => t.enforced({})).map((t) => {
              const on = form.types.includes(t.value);
              return (
                <label key={t.value} title={t.blurb}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[0.8125rem] cursor-pointer transition-colors ${on ? 'border-[color:var(--accent)] bg-accent-soft text-ink font-bold' : 'border-line text-ink-muted hover:border-field'}`}>
                  <input type="checkbox" checked={on} onChange={(e) => toggleType(t.value, e.target.checked)} className="accent-[color:var(--accent)]" />
                  {t.label}
                </label>
              );
            })}
          </div>
        )}
      </fieldset>

      <div className="flex flex-wrap gap-3">
        {/* First the camera, then one of ITS zones (or its whole picture). */}
        <label className="flex flex-col gap-1 text-xs text-ink-faint">Camera
          <select value={camKey} onChange={(e) => chooseCamera(e.target.value)} className={INPUT} aria-label="Camera">
            <option value="">{meta.zoneOptional ? 'Every camera' : 'Choose a camera'}</option>
            {groups.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-faint">Zone
          <select value={form.zoneId} disabled={!camGroup} onChange={(e) => setZone(e.target.value)} className={`${INPUT} disabled:opacity-50`} aria-label="Zone">
            <option value="">{camGroup ? 'Choose a zone' : meta.zoneOptional ? 'Every zone' : 'Choose a camera first'}</option>
            {camGroup && camGroup.zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name} · {z.safetyType === 'GENERAL' && !meta.zoneTypes.includes('GENERAL') ? `General, becomes ${ZONE_TYPE_LABELS[mainTarget(meta)]}` : ZONE_TYPE_LABELS[z.safetyType]}
              </option>
            ))}
            {camGroup && camGroup.name && <option value={`${WHOLE}${camGroup.name}`}>Whole view (the full picture)</option>}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-faint">How serious
          <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className={INPUT}>
            {SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-faint">Least evidence to make a claim
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="1" step="0.05" value={form.minEvidenceConfidence} onChange={(e) => setForm({ ...form, minEvidenceConfidence: Number(e.target.value) })} aria-label="Least evidence to make a claim" className="accent-[color:var(--accent)]" />
            <span className="w-10 tabular-nums text-ink">{Math.round(form.minEvidenceConfidence * 100)}%</span>
          </div>
        </label>
      </div>
      {changes.length > 0 && (
        <Notice>{changes.map((c) => `"${c.zone.name}" becomes ${/^[AEIOU]/.test(ZONE_TYPE_LABELS[c.to]) ? 'an' : 'a'} ${ZONE_TYPE_LABELS[c.to]} zone`).join('; ')} when you save.</Notice>
      )}
      {whole && <Notice>A zone covering the whole picture of "{whole}" is added when you save.</Notice>}
      {form.types.length > 0 && cameras.length === 0 && zoneChoices.length === 0 && generalZones.length === 0 && !meta.zoneOptional && (
        <Notice>There is no {meta.zoneTypes.map((t) => ZONE_TYPE_LABELS[t].toLowerCase()).join(' or ')} zone yet. Draw one in Zones, then come back.</Notice>
      )}

      {/* Each checked kind's own settings. */}
      {form.types.map((t) => {
        const m = ruleTypeMeta(t);
        if (!m || !m.fields.length) return null;
        const pf = { ...form, params: form.paramsByType[t] || {} };
        return (
          <div key={t} className="flex flex-col gap-2 rounded-lg border border-line bg-surface p-3">
            <p className="text-[0.8125rem] font-bold text-ink">{m.label}</p>
            <div className="flex flex-wrap gap-3">
              {m.fields.map((f) => <ParamField key={f.key} field={f} form={pf} setParams={setParamsFor(t)} zones={zones} cameras={cameras} />)}
            </div>
          </div>
        );
      })}

      <label className="inline-flex items-center gap-2 text-[0.8125rem] text-ink-muted self-start cursor-pointer">
        <input type="checkbox" checked={form.isEnabled} onChange={(e) => setForm({ ...form, isEnabled: e.target.checked })} className="accent-[color:var(--accent)]" />
        {many ? 'These rules are on' : 'This rule is on'}
      </label>

      <div aria-live="polite" className="min-h-[1.25rem]">
        {check.state === 'checking' && <p className="flex items-center gap-2 text-[0.8125rem] text-ink-faint"><Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> Checking…</p>}
        {check.state === 'valid' && (
          <Notice tone="ok">
            {many ? `All ${form.types.length} checks are valid.` : 'This rule is valid.'} {check.enforced ? `${many ? 'They apply' : 'It applies'} from the next run of a camera.` : 'Saved, but the safety engine does not act on every one of these yet.'}
          </Notice>
        )}
        {check.state === 'invalid' && <Notice tone="error">{check.errors.join(' ')}</Notice>}
        {check.state === 'error' && <Notice tone="error">{check.message}</Notice>}
      </div>
      {/* Not twice: a save refused for the reason the live check above already shows. */}
      {error && !(check.state === 'invalid' && error === check.errors.join(' ')) && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={BTN_PRIMARY}>
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} {editing ? 'Save changes' : many ? `Create ${form.types.length} rules` : 'Create rule'}
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

  // zonesReady: a rule is only called broken once the zone list has actually arrived.
  const [zonesReady, setZonesReady] = useState(false);
  const loadZones = useCallback(() => listSafetyZones().then((r) => { setZones(r.zones || []); setZonesReady(!r.error); return r.zones || []; }), []);
  const [cameras, setCameras] = useState([]);
  const loadCameras = useCallback(() => listCameras().then((r) => setCameras(r.cameras || [])), []);
  useEffect(() => { load(); loadZones(); loadCameras(); }, [load, loadZones, loadCameras]);
  // A zone drawn a moment ago on the Zones page is in the list as soon as the form opens.
  useEffect(() => { if (editing) { loadZones(); loadCameras(); } }, [Boolean(editing)]); // eslint-disable-line react-hooks/exhaustive-deps

  // A rule deleted elsewhere since this list was loaded.
  const gone = () => { setEditing(null); setMessage({ tone: 'error', text: 'That rule has been deleted, perhaps in another window. The list is up to date now.' }); load(); };

  async function toggle(rule) {
    setMessage(null);
    const r = await updateRule(rule.id, { is_enabled: !rule.isEnabled });
    if (!r.ok && r.status === 404) { gone(); return; }
    if (!r.ok) { setMessage({ tone: 'error', text: friendlyRuleError(r.message) }); return; }
    load();
  }

  async function remove(rule) {
    setMessage(null);
    const r = await deleteRule(rule.id);
    setConfirmDelete(null);
    if (!r.ok && r.status === 404) { gone(); return; }
    if (!r.ok) { setMessage({ tone: 'error', text: friendlyRuleError(r.message) }); return; }
    setMessage({ tone: 'ok', text: `"${rule.name}" was deleted. Decisions it made keep their record.` });
    load();
  }

  return (
    <div className="space-y-5">
      <Panel title="Safety rules" icon={SlidersHorizontal} aside={state.loading ? '' : `${state.rules.length} rule${state.rules.length === 1 ? '' : 's'}`}>
        <Notice className="mb-3">
          Restricted zones are checked for a helmet and vest on entry by default; your own entry rule for a zone replaces that check. Rules apply from a camera's next run.
        </Notice>
        {!canEdit && !state.loading && <Notice className="mb-3">You have view-only access: you can read the rules, and an administrator or manager changes them.</Notice>}
        {message && <Notice tone={message.tone} className="mb-3">{message.text}</Notice>}
        {state.loading && <Skeleton rows={3} label="Loading the rules" />}
        {state.error && <Notice tone="error">{state.error}</Notice>}
        {!state.loading && !state.error && state.rules.length === 0 && <p className="text-[0.8125rem] text-ink-muted leading-relaxed">No rules of your own yet, so the built-in helmet-and-vest check applies to every Restricted zone.</p>}

        <ul className="flex flex-col divide-y divide-line-soft" aria-label="Rules">
          {state.rules.map((rule) => {
            const meta = ruleTypeMeta(rule.type);
            return (
              <li key={rule.id} className="py-4 first:pt-1 flex flex-col gap-2.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem]">
                  <span className="text-sm text-ink font-bold">{rule.name}</span>
                  <SeverityChip severity={rule.severity} />
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[0.6875rem] font-bold uppercase tracking-wide ${rule.isEnabled ? 'border-line text-ink bg-surface-alt' : 'border-line text-ink-faint'}`}>{rule.isEnabled ? 'On' : 'Off'}</span>
                  <span className="text-ink-muted">{meta ? meta.label : rule.type}</span>
                  <span className="text-ink-faint">{[(rule.camera && rule.camera.name) || (zones.find((z) => z.id === rule.zoneId) || {}).camera, rule.zone && rule.zone.name].filter(Boolean).join(' · ') || 'Every camera'}</span>
                </div>
                {rule.evaluated_by_engine && ruleSummary(rule.type, rule.params, (id) => (zones.find((z) => z.id === id) || {}).name) && (
                  <p className="text-[0.8125rem] text-ink-muted leading-relaxed">{ruleSummary(rule.type, rule.params, (id) => (zones.find((z) => z.id === id) || {}).name)}</p>
                )}
                {!rule.evaluated_by_engine && <p className="text-xs text-ink-faint">Saved, but the safety engine does not act on this kind of rule yet.</p>}
                {/* A zone this rule names in its settings (the exit, or the checkpoint) was deleted. Deleting a zone removes
                    the rules ON it, but not this reference, and the engine then skips the rule: say so, here. */}
                {zonesReady && missingZoneRef(rule, zones) && (
                  <Notice tone="error">The {missingZoneRef(rule, zones)} this rule uses was deleted, so the rule is not checked. Edit it to choose another, or delete it.</Notice>
                )}
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => toggle(rule)} className={BTN_QUIET}>{rule.isEnabled ? 'Turn off' : 'Turn on'}</button>
                    <button type="button" onClick={() => { setEditing(formFromRule(rule)); setMessage(null); }} className={BTN_QUIET}><Pencil className="w-3.5 h-3.5" aria-hidden="true" /> Edit</button>
                    {confirmDelete === rule.id ? (
                      <>
                        <span className="self-center text-[0.8125rem] text-ink-muted">Delete this rule?</span>
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
          <button type="button" onClick={() => { setEditing(newForm()); setMessage(null); }} className={`${BTN_PRIMARY} mt-3`}>
            <Plus className="w-3.5 h-3.5" aria-hidden="true" /> New rule
          </button>
        )}
      </Panel>

      {canEdit && editing && (
        <RuleForm key={editing.id || 'new'} initial={editing} zones={zones} cameras={cameras} onZonesChanged={loadZones}
          onListChanged={load}
          onSaved={(n) => { setEditing(null); setMessage({ tone: 'ok', text: `${n > 1 ? `${n} rules were saved` : 'The rule was saved'}. ${n > 1 ? 'They apply' : 'It applies'} from the next run of a camera.` }); load(); }}
          onCancel={() => setEditing(null)} onGone={gone} />
      )}
    </div>
  );
}
