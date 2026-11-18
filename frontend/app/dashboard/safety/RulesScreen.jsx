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
import { createPortal } from 'react-dom';
import {
  AlertTriangle, Camera, Check, Footprints, HardHat, ListChecks, Loader2, MapPin, Pencil, PersonStanding, Plus, Power,
  ShieldCheck, SlidersHorizontal, Timer, Trash2, Users, X,
} from 'lucide-react';
import { createRule, deleteRule, listRules, updateRule, validateRule } from '../../lib/safetyApi';
import { listSafetyZones } from '../../lib/safety/management';
import { listCameras } from '../../lib/platform/cameras';
import { backendFetch } from '../../lib/backend';
import { RULE_TYPES, SEVERITIES, PPE_ITEMS, defaultParams, friendlyRuleError, isEnforced, ruleBody, ruleSummary, ruleTypeMeta } from '../../lib/safety/ruleTypes';
import { can } from '../../lib/permissions';
import { BTN_PRIMARY, BTN_QUIET, INPUT, Notice, Panel, SeverityChip, Skeleton } from './common';

// An icon and one short line per kind of check, for the picker and the rule boxes.
const TYPE_ICON = { PPE_ENTRY: HardHat, MAX_DWELL: Timer, PPE_CONTINUOUS: ShieldCheck, OCCUPANCY: Users, MAN_DOWN: PersonStanding, WALK_ONLY: Footprints };
const TYPE_HINT = {
  PPE_ENTRY: 'Helmet and vest when someone walks in.',
  MAX_DWELL: 'Flag anyone who stays too long.',
  PPE_CONTINUOUS: 'Gear must stay on the whole time.',
  OCCUPANCY: 'Too few or too many people.',
  MAN_DOWN: 'Someone lying still on the floor.',
  WALK_ONLY: 'No running in this area.',
};

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
  if (field.kind === 'toggle') {
    const on = value != null && value !== '' ? Boolean(value) : Boolean(field.auto && field.auto(form.params));
    return (
      <label className="flex items-center gap-3 self-end rounded-xl border border-line px-3.5 py-2 cursor-pointer">
        <input type="checkbox" checked={on} onChange={(e) => setParams({ [field.key]: e.target.checked })} className="sr-only" />
        <span aria-hidden="true" className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${on ? 'bg-accent' : 'bg-line'}`}>
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? 'left-[1.125rem]' : 'left-0.5'}`} />
        </span>
        <span className="text-[0.8125rem] font-bold text-ink">{field.label}</span>
      </label>
    );
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

function RuleForm({ initial, zones, cameras = [], rules = [], onSaved, onCancel, onGone, onZonesChanged, onListChanged }) {
  const [form, setForm] = useState(initial);
  const [check, setCheck] = useState({ state: 'idle' });
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const formRef = useRef(null);
  // The form opens below the list: bring it into view (and focus its first field) so the click visibly did something.
  useEffect(() => {
    const el = formRef.current;
    const f = el && el.querySelector('input:not(.sr-only), select');
    if (f) f.focus({ preventScroll: true });
    const onKey = (e) => { if (e.key === 'Escape' && !inFlight.current) onCancel(); };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [error, setError] = useState(null);
  const editing = Boolean(form.id);
  const meta = useMemo(() => combinedMeta(form.types), [form.types]);
  const zoneChoices = useMemo(() => zones.filter((z) => meta.zoneTypes.includes(z.safetyType)), [zones, meta]);
  const generalZones = useMemo(() => zones.filter((z) => z.safetyType === 'GENERAL'), [zones]);
  const changes = useMemo(() => zoneChanges(form, zones), [form, zones]);
  const whole = wholeCamera(form.zoneId);
  // Every saved camera, with its zones under it; zones whose camera is not in the list (none, or removed) last.
  const groups = useMemo(() => {
    // With nothing ticked (say, the only check was already set on this zone) every zone stays listed.
    const fits = (z) => !form.types.length || meta.zoneTypes.includes(z.safetyType) || z.safetyType === 'GENERAL';
    const out = cameras.map((c) => ({ key: c.id, name: c.name, label: c.sourceType === 'UPLOAD' ? `${c.name} · uploaded videos` : c.name, zones: zones.filter((z) => z.cameraId === c.id && fits(z)) }));
    const rest = zones.filter((z) => !cameras.some((c) => c.id === z.cameraId) && fits(z));
    if (rest.length) out.push({ key: 'other', name: null, label: 'Other zones', zones: rest });
    return out;
  }, [cameras, zones, meta, form.types.length]);
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
  const labelOf = (t) => (ruleTypeMeta(t) || {}).label || t;
  // The rules already set on the chosen zone (another rule of a kind that is already there would check it twice).
  const existingHere = useMemo(() => {
    if (!form.zoneId || whole) return [];
    return rules.filter((r) => r.zoneId === form.zoneId && r.id !== form.id);
  }, [rules, form.zoneId, form.id, whole]);
  const takenTypes = useMemo(() => new Set(existingHere.map((r) => r.type)), [existingHere]);
  const chosenZone = zones.find((z) => z.id === form.zoneId) || null;
  // A zone that already has one of the checked kinds: that kind is unticked, and its tile says it is already set.
  const [dropped, setDropped] = useState([]);
  useEffect(() => {
    if (editing) return;
    const gone = form.types.filter((t) => takenTypes.has(t));
    setDropped(gone);
    if (gone.length) setForm((f) => ({ ...f, types: f.types.filter((t) => !takenTypes.has(t)) }));
  }, [takenTypes]); // eslint-disable-line react-hooks/exhaustive-deps
  const many = form.types.length > 1;
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

  const SECTION = 'flex flex-col gap-2.5';
  const HEAD = 'text-[0.8125rem] font-black text-ink';
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 sm:p-6" onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onCancel(); }}>
      <form ref={formRef} onSubmit={save} role="dialog" aria-modal="true" aria-label={editing ? 'Edit rule' : 'New rule'}
        className="themed w-full max-w-3xl max-h-[92vh] flex flex-col rounded-2xl border border-line bg-surface shadow-2xl overflow-hidden">
        <header className="flex items-center gap-3 px-5 sm:px-6 py-4 border-b border-line">
          <span className="w-10 h-10 rounded-xl bg-accent text-white flex items-center justify-center shrink-0">
            {editing ? <Pencil className="w-4 h-4" aria-hidden="true" /> : <Plus className="w-5 h-5" aria-hidden="true" />}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-black tracking-tight text-ink">{editing ? 'Edit rule' : 'New rule'}</h2>
            <p className="text-[0.8125rem] text-ink-muted">Applies from the camera’s next run.</p>
          </div>
          <button type="button" onClick={onCancel} className="p-2 rounded-lg border border-line text-ink-muted hover:text-ink hover:border-field transition-colors" aria-label="Close">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </header>

        <div className="flex-1 min-h-0 overflow-y-auto px-5 sm:px-6 py-5 flex flex-col gap-6">
          <label className={SECTION}>
            <span className={HEAD}>Name</span>
            <input value={form.name} maxLength={140} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Press area" className={`${INPUT} w-full text-[0.9375rem] py-2.5`} required />
          </label>

          <fieldset className={SECTION}>
            <legend className={`${HEAD} mb-2.5`}>What to check{editing ? '' : <span className="font-semibold text-ink-faint"> · pick one or more</span>}</legend>
            {editing ? (
              <div className="flex items-center gap-3 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3">
                {React.createElement(TYPE_ICON[form.types[0]] || ShieldCheck, { className: 'w-5 h-5 text-accent', 'aria-hidden': true })}
                <span className="text-[0.9375rem] font-black text-ink">{labelOf(form.types[0])}</span>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {RULE_TYPES.filter((t) => t.enforced({})).map((t) => {
                  const taken = takenTypes.has(t.value);
                  const on = form.types.includes(t.value);
                  const Icon = TYPE_ICON[t.value] || ShieldCheck;
                  const already = taken ? existingHere.find((r) => r.type === t.value) : null;
                  return (
                    <label key={t.value} title={taken ? `Already set on this zone: ${already.name}` : t.blurb}
                      className={`relative flex items-start gap-3 rounded-xl border-2 px-3.5 py-3 transition-colors ${taken ? 'border-line bg-surface-alt cursor-not-allowed' : on ? 'border-[color:var(--accent)] bg-accent-soft cursor-pointer' : 'border-line hover:border-field cursor-pointer'}`}>
                      <input type="checkbox" checked={on || taken} disabled={taken} onChange={(e) => toggleType(t.value, e.target.checked)} className="sr-only" />
                      <span style={taken ? { background: 'var(--ink)', color: 'var(--surface)' } : undefined}
                        className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${taken ? '' : on ? 'bg-accent text-white' : 'bg-surface-alt text-ink-muted'}`}>
                        {taken ? <Check className="w-4 h-4" aria-hidden="true" /> : <Icon className="w-4 h-4" aria-hidden="true" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`block text-[0.875rem] font-black ${taken ? 'text-ink-muted pr-20' : 'text-ink'}`}>{t.label}</span>
                        <span className="block text-[0.75rem] text-ink-muted line-clamp-2">
                          {taken ? `${already.name}${already.isEnabled ? '' : ' (switched off)'}` : TYPE_HINT[t.value] || ''}
                        </span>
                      </span>
                      {taken
                        ? <span style={{ background: 'var(--ink)', color: 'var(--surface)' }} className="absolute top-2 right-2 rounded-md px-1.5 py-0.5 text-[0.625rem] font-black uppercase tracking-wider">Already set</span>
                        : on && <Check className="absolute top-2.5 right-2.5 w-4 h-4 text-accent" aria-hidden="true" />}
                    </label>
                  );
                })}
              </div>
            )}
          </fieldset>

          <div className={SECTION}>
            <span className={HEAD}>Where</span>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <select value={camKey} onChange={(e) => chooseCamera(e.target.value)} className={`${INPUT} w-full py-2.5`} aria-label="Camera">
                <option value="">{meta.zoneOptional ? 'Every camera' : 'Choose a camera'}</option>
                {groups.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
              </select>
              <select value={form.zoneId} disabled={!camGroup} onChange={(e) => setZone(e.target.value)} className={`${INPUT} w-full py-2.5 disabled:opacity-50`} aria-label="Zone">
                <option value="">{camGroup ? 'Choose a zone' : meta.zoneOptional ? 'Every zone' : 'Choose a camera first'}</option>
                {camGroup && camGroup.zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name} · {z.safetyType === 'GENERAL' && !meta.zoneTypes.includes('GENERAL') ? `General, becomes ${ZONE_TYPE_LABELS[mainTarget(meta)]}` : ZONE_TYPE_LABELS[z.safetyType]}
                  </option>
                ))}
                {camGroup && camGroup.name && <option value={`${WHOLE}${camGroup.name}`}>Whole view (the full picture)</option>}
              </select>
            </div>
            {changes.length > 0 && (
              <p className="text-[0.75rem] text-ink-muted">{changes.map((c) => `"${c.zone.name}" becomes ${/^[AEIOU]/.test(ZONE_TYPE_LABELS[c.to]) ? 'an' : 'a'} ${ZONE_TYPE_LABELS[c.to]} zone`).join('; ')} when you save.</p>
            )}
            {whole && <p className="text-[0.75rem] text-ink-muted">A zone covering the whole picture of "{whole}" is added when you save.</p>}
            {existingHere.length > 0 && (
              <div className="rounded-xl border border-line bg-surface-alt px-4 py-3 flex flex-col gap-2">
                <p className="text-[0.8125rem] font-black text-ink">Already checked in {chosenZone ? `"${chosenZone.name}"` : 'this zone'}</p>
                <ul className="flex flex-wrap gap-2">
                  {existingHere.map((r) => {
                    const Icon = TYPE_ICON[r.type] || ShieldCheck;
                    return (
                      <li key={r.id} title={r.name} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-[0.75rem] font-bold text-ink">
                        <Icon className="w-3.5 h-3.5 text-accent" aria-hidden="true" /> {labelOf(r.type)}
                        {!r.isEnabled && <span className="text-ink-faint font-semibold">· off</span>}
                      </li>
                    );
                  })}
                </ul>
                {!editing && dropped.length > 0 && (
                  <p className="text-[0.75rem] text-ink-muted">Unticked {dropped.map(labelOf).join(', ')}: already set here. Edit that rule instead.</p>
                )}
                {!editing && form.types.length === 0 && (
                  <p className="text-[0.75rem] text-ink-muted">Pick another check above to add it here.</p>
                )}
              </div>
            )}
            {form.types.length > 0 && cameras.length === 0 && zoneChoices.length === 0 && generalZones.length === 0 && !meta.zoneOptional && (
              <Notice>No {meta.zoneTypes.map((t) => ZONE_TYPE_LABELS[t].toLowerCase()).join(' or ')} zone yet. Draw one on the Zones page first.</Notice>
            )}
          </div>

          <div className={SECTION}>
            <span className={HEAD}>How serious</span>
            <div role="radiogroup" aria-label="How serious" className="grid grid-cols-3 gap-2">
              {SEVERITIES.map((sv) => {
                const on = form.severity === sv.value;
                const tone = sv.value === 'CRITICAL' ? 'bg-accent text-white border-[color:var(--accent)]' : sv.value === 'WARNING' ? 'bg-accent-soft text-accent border-[color:var(--accent)]' : 'bg-surface-alt text-ink border-field';
                return (
                  <button key={sv.value} type="button" role="radio" aria-checked={on} onClick={() => setForm({ ...form, severity: sv.value })}
                    className={`rounded-xl border-2 px-3 py-2.5 text-[0.875rem] font-black transition-colors ${on ? tone : 'border-line text-ink-muted hover:border-field'}`}>
                    {sv.label}
                  </button>
                );
              })}
            </div>
          </div>

          {form.types.map((t) => {
            const m = ruleTypeMeta(t);
            if (!m || !m.fields.length) return null;
            const pf = { ...form, params: form.paramsByType[t] || {} };
            const Icon = TYPE_ICON[t] || ShieldCheck;
            return (
              <div key={t} className="flex flex-col gap-3 rounded-xl border border-line bg-surface-alt p-4">
                <p className="flex items-center gap-2 text-[0.8125rem] font-black text-ink"><Icon className="w-4 h-4 text-accent" aria-hidden="true" /> {m.label}</p>
                <div className="flex flex-wrap gap-4">
                  {m.fields.map((f) => <ParamField key={f.key} field={f} form={pf} setParams={setParamsFor(t)} zones={zones} cameras={cameras} />)}
                </div>
              </div>
            );
          })}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="flex flex-col gap-2 rounded-xl border border-line px-4 py-3">
              <span className="flex items-center justify-between text-[0.8125rem] font-black text-ink">
                Sure enough to flag <span className="tabular-nums text-accent">{Math.round(form.minEvidenceConfidence * 100)}%</span>
              </span>
              <input type="range" min="0" max="1" step="0.05" value={form.minEvidenceConfidence} onChange={(e) => setForm({ ...form, minEvidenceConfidence: Number(e.target.value) })}
                aria-label="How sure the system must be before it flags" className="accent-[color:var(--accent)]" />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-xl border border-line px-4 py-3 cursor-pointer">
              <span className="text-[0.8125rem] font-black text-ink">{many ? 'Turn these on now' : 'Turn on now'}</span>
              <input type="checkbox" checked={form.isEnabled} onChange={(e) => setForm({ ...form, isEnabled: e.target.checked })} className="sr-only peer" />
              <span aria-hidden="true" className={`relative w-10 h-6 rounded-full transition-colors ${form.isEnabled ? 'bg-accent' : 'bg-line'}`}>
                <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${form.isEnabled ? 'left-[1.125rem]' : 'left-0.5'}`} />
              </span>
            </label>
          </div>
        </div>

        <footer className="flex flex-wrap items-center gap-3 px-5 sm:px-6 py-4 border-t border-line">
          <div aria-live="polite" className="flex-1 min-w-[12rem] text-[0.8125rem]">
            {check.state === 'checking' && <span className="flex items-center gap-2 text-ink-faint"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Checking…</span>}
            {check.state === 'valid' && <span className="flex items-center gap-2 text-ink-muted"><Check className="w-4 h-4 text-accent" aria-hidden="true" /> {many ? `All ${form.types.length} look good.` : 'Looks good.'}</span>}
            {check.state === 'invalid' && <span className="text-accent font-semibold">{check.errors.join(' ')}</span>}
            {check.state === 'error' && <span className="text-accent font-semibold">{check.message}</span>}
            {error && !(check.state === 'invalid' && error === check.errors.join(' ')) && <span className="block text-accent font-semibold">{error}</span>}
          </div>
          <button type="button" onClick={onCancel} className={BTN_QUIET}>Cancel</button>
          <button type="submit" disabled={saving || !form.types.length} className={`${BTN_PRIMARY} px-5`}>
            {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />} {editing ? 'Save changes' : many ? `Add ${form.types.length} rules` : 'Add rule'}
          </button>
        </footer>
      </form>
    </div>,
    document.body,
  );
}

/** A small on/off switch. */
function Switch({ on, onClick, label, disabled }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={onClick} disabled={disabled}
      className={`relative w-10 h-6 rounded-full transition-colors shrink-0 disabled:opacity-50 ${on ? 'bg-accent' : 'bg-line'}`}>
      <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${on ? 'left-[1.125rem]' : 'left-0.5'}`} />
    </button>
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
  const gone = () => { setEditing(null); setMessage({ tone: 'error', text: 'That rule was deleted, perhaps in another window. The list is up to date now.' }); load(); };

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
    setMessage({ tone: 'ok', text: `Deleted "${rule.name}".` });
    load();
  }

  const rules = state.rules;
  const on = rules.filter((x) => x.isEnabled).length;
  const critical = rules.filter((x) => x.isEnabled && x.severity === 'CRITICAL').length;
  const openNew = () => { setEditing(newForm()); setMessage(null); };
  const coverage = cameras.map((c) => {
    const camZones = zones.filter((z) => z.cameraId === c.id);
    const onCam = rules.filter((x) => x.isEnabled && (x.cameraId === c.id || (x.camera && x.camera.name === c.name) || camZones.some((z) => z.id === x.zoneId) || (!x.cameraId && !x.zoneId && !x.camera)));
    return {
      id: c.id, name: c.name, rules: onCam.length,
      zones: camZones.map((z) => ({ ...z, types: [...new Set(onCam.filter((x) => x.zoneId === z.id).map((x) => x.type))] })),
    };
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Counts and the one button. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { icon: ListChecks, label: 'Rules', value: rules.length },
          { icon: Power, label: 'Switched on', value: on },
          { icon: AlertTriangle, label: 'Critical', value: critical, strong: critical > 0 },
          { icon: Camera, label: 'Cameras checked', value: coverage.filter((c) => c.rules > 0).length, max: cameras.length },
        ].map(({ icon: Icon, label, value, strong, max }) => (
          <div key={label} className="rounded-2xl border border-line bg-surface p-5 flex items-center gap-4 min-w-0">
            <span className="w-11 h-11 rounded-xl bg-accent-soft text-accent flex items-center justify-center shrink-0"><Icon className="w-5 h-5" aria-hidden="true" /></span>
            <span className="min-w-0">
              <span className={`block text-3xl font-black tabular-nums leading-none ${strong ? 'text-accent' : 'text-ink'}`}>
                {state.loading ? '—' : value}{max ? <span className="text-[0.875rem] font-bold text-ink-faint"> / {max}</span> : null}
              </span>
              <span className="block mt-1 text-[0.8125rem] font-bold text-ink-muted truncate">{label}</span>
            </span>
          </div>
        ))}
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {state.error && <Notice tone="error">{state.error}</Notice>}

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_20rem] gap-4 items-stretch">
      <section className="rounded-2xl border border-line bg-surface p-5 flex flex-col gap-4 lg:min-h-[calc(100dvh-25.5rem)] min-w-0">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-[0.9375rem] font-black text-ink"><SlidersHorizontal className="w-4 h-4 text-accent" aria-hidden="true" /> Your rules</h3>
            <p className="mt-0.5 text-[0.75rem] text-ink-faint" title="Your own entry rule for a zone replaces that check.">Restricted zones check helmet and vest by default.</p>
          </div>
          {canEdit && rules.length > 0 && (
            <button type="button" onClick={openNew} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-[0.875rem] font-bold text-white hover:brightness-110 transition">
              <Plus className="w-4 h-4" aria-hidden="true" /> New rule
            </button>
          )}
        </header>

        {state.loading && <Skeleton rows={3} label="Loading the rules" />}

        {!state.loading && !state.error && rules.length === 0 && (
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-4 py-10">
            <span className="flex items-center justify-center w-14 h-14 rounded-2xl border border-line bg-surface-alt">
              <SlidersHorizontal className="w-6 h-6 text-ink-muted" aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-xl sm:text-2xl font-black tracking-tight text-ink">No rules yet</h2>
              <p className="mt-2 text-[0.9375rem] text-ink-muted">Add one to choose what gets checked, and where.</p>
            </div>
            {canEdit && (
              <button type="button" onClick={openNew} className="inline-flex items-center gap-2 px-5 py-2.5 rounded-2xl bg-inverse text-inverse font-bold text-[0.875rem] hover:bg-accent transition-colors">
                <Plus className="w-4 h-4" aria-hidden="true" /> New rule
              </button>
            )}
          </div>
        )}

        {rules.length > 0 && (
          <ul className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-3 content-start" aria-label="Rules">
            {rules.map((rule) => {
              const meta = ruleTypeMeta(rule.type);
              const Icon = TYPE_ICON[rule.type] || ShieldCheck;
              const where = [(rule.camera && rule.camera.name) || (zones.find((z) => z.id === rule.zoneId) || {}).camera, rule.zone && rule.zone.name].filter(Boolean).join(' · ') || 'Every camera';
              const summary = rule.evaluated_by_engine ? ruleSummary(rule.type, rule.params, (id) => (zones.find((z) => z.id === id) || {}).name) : 'Saved, not checked yet.';
              const broken = zonesReady && missingZoneRef(rule, zones);
              return (
                <li key={rule.id} className={`rounded-2xl border bg-surface p-4 flex flex-col gap-3 min-w-0 transition-opacity ${broken ? 'border-[color:var(--accent)]' : 'border-line'} ${rule.isEnabled ? '' : 'opacity-60'}`}>
                  <div className="flex items-start gap-3 min-w-0">
                    <span className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${rule.isEnabled ? 'bg-accent-soft text-accent' : 'bg-surface-alt text-ink-faint'}`}>
                      <Icon className="w-4 h-4" aria-hidden="true" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[0.9375rem] font-black text-ink truncate" title={rule.name}>{rule.name}</p>
                      <p className="text-[0.8125rem] text-ink-muted truncate">{meta ? meta.label : rule.type}</p>
                    </div>
                    <SeverityChip severity={rule.severity} />
                  </div>
                  <p className="flex items-center gap-1.5 text-[0.8125rem] text-ink-muted min-w-0"><MapPin className="w-3.5 h-3.5 shrink-0 text-ink-faint" aria-hidden="true" /> <span className="truncate">{where}</span></p>
                  {summary && <p className="text-[0.75rem] text-ink-faint line-clamp-2" title={summary}>{summary}</p>}
                  {broken && <p className="text-[0.75rem] font-semibold text-accent">Its {broken} was deleted, so it isn’t checked. Edit it.</p>}
                  <div className="mt-auto pt-2 border-t border-line-soft flex items-center gap-2">
                    <Switch on={rule.isEnabled} onClick={() => canEdit && toggle(rule)} disabled={!canEdit} label={rule.isEnabled ? `Turn off ${rule.name}` : `Turn on ${rule.name}`} />
                    <span className="text-[0.75rem] font-bold text-ink-muted">{rule.isEnabled ? 'On' : 'Off'}</span>
                    {canEdit && (
                      <span className="ml-auto flex items-center gap-1.5">
                        {confirmDelete === rule.id ? (
                          <>
                            <button type="button" onClick={() => remove(rule)} className={BTN_PRIMARY}>Delete</button>
                            <button type="button" onClick={() => setConfirmDelete(null)} className={BTN_QUIET}>Keep</button>
                          </>
                        ) : (
                          <>
                            <button type="button" onClick={() => { setEditing(formFromRule(rule)); setMessage(null); }} className={BTN_QUIET} aria-label={`Edit ${rule.name}`} title="Edit"><Pencil className="w-3.5 h-3.5" aria-hidden="true" /></button>
                            <button type="button" onClick={() => setConfirmDelete(rule.id)} className={BTN_QUIET} aria-label={`Delete ${rule.name}`} title="Delete"><Trash2 className="w-3.5 h-3.5" aria-hidden="true" /></button>
                          </>
                        )}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Each camera: how many rules run on it, and its zones. */}
      <aside className="rounded-2xl border border-line bg-surface p-5 flex flex-col gap-4 min-w-0">
        <h3 className="flex items-center gap-2 text-[0.9375rem] font-black text-ink"><Camera className="w-4 h-4 text-accent" aria-hidden="true" /> By camera</h3>
        {coverage.length === 0 ? (
          <p className="text-[0.8125rem] text-ink-muted">No cameras yet. Add one on the Cameras page.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {coverage.map((c) => (
              <li key={c.id} className="rounded-xl border border-line bg-surface-alt p-3.5 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[0.875rem] font-black text-ink truncate" title={c.name}>{c.name}</span>
                  <span className={`shrink-0 px-2 py-0.5 rounded-md text-[0.6875rem] font-bold ${c.rules ? 'bg-accent-soft text-accent' : 'bg-surface text-ink-faint border border-line'}`}>
                    {c.rules} rule{c.rules === 1 ? '' : 's'}
                  </span>
                </div>
                {c.zones.length === 0 ? (
                  <p className="mt-2 text-[0.75rem] text-ink-faint">No zones drawn</p>
                ) : (
                  <ul className="mt-2.5 flex flex-col gap-1.5">
                    {c.zones.map((z) => (
                      <li key={z.id} className="flex items-center justify-between gap-2 text-[0.8125rem] min-w-0">
                        <span className="flex items-center gap-1.5 min-w-0 text-ink-muted"><MapPin className="w-3 h-3 shrink-0 text-ink-faint" aria-hidden="true" /><span className="truncate">{z.name}</span></span>
                        <span className="flex items-center gap-1 shrink-0">
                          {z.types.length === 0
                            ? <span className="text-[0.6875rem] text-ink-faint">{z.safetyType === 'RESTRICTED' ? 'default check' : 'not checked'}</span>
                            : z.types.map((t) => React.createElement(TYPE_ICON[t] || ShieldCheck, { key: t, className: 'w-3.5 h-3.5 text-accent', 'aria-label': (ruleTypeMeta(t) || {}).label || t }))}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </aside>
      </div>

      {canEdit && editing && (
        <RuleForm key={editing.id || 'new'} initial={editing} zones={zones} cameras={cameras} rules={rules} onZonesChanged={loadZones}
          onListChanged={load}
          onSaved={(n) => { setEditing(null); setMessage({ tone: 'ok', text: `${n > 1 ? `${n} rules saved` : 'Rule saved'}. ${n > 1 ? 'They apply' : 'It applies'} from the next run.` }); load(); }}
          onCancel={() => setEditing(null)} onGone={gone} />
      )}
    </div>
  );
}
