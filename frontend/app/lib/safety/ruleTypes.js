// frontend/app/lib/safety/ruleTypes.js
//
// What a manager can ask the safety rules to check, as data: one entry per rule type, with the
// parameters it takes and their limits. The rules editor draws its form from this list, and the
// limits are the same ones the backend enforces (backend/app/safety/rules.py, validate_params),
// which has the last word: the editor also asks the server to validate before saving.
//
// `enforced` says whether the safety engine acts on this kind of rule TODAY. Every type can be saved
// and validated, but only helmet-and-vest at entry, helmet-and-vest while inside, the longest stay, the
// required exit and a person lying still are evaluated so far; the screen says so plainly rather than letting someone
// believe a crowding limit is protecting a doorway. (The server also says, as `evaluated_by_engine`.)

export const PPE_ITEMS = [
  { value: 'helmet', label: 'Helmet' },
  { value: 'safety_vest', label: 'Safety vest' },
];

export const SEVERITIES = [
  { value: 'INFO', label: 'Info' },
  { value: 'WARNING', label: 'Warning' },
  { value: 'CRITICAL', label: 'Critical' },
];

// field kinds: 'ppe' (checkboxes), 'select', 'number', 'zone' (a zone of a given safety type)
export const RULE_TYPES = [
  {
    value: 'PPE_ENTRY',
    label: 'Helmet and vest on entry',
    blurb: 'Checks the required items when someone enters the zone.',
    zoneTypes: ['RESTRICTED'],
    enforced: (params) => (params.mode || 'AT_ENTRY') === 'AT_ENTRY',
    defaults: { required_ppe: ['helmet', 'safety_vest'], mode: 'AT_ENTRY' },
    fields: [
      { key: 'required_ppe', kind: 'ppe', label: 'Required items' },
      { key: 'mode', kind: 'select', label: 'When it is checked', options: [
        { value: 'AT_ENTRY', label: 'At the moment of entry' },
        { value: 'VIA_CHECKPOINT', label: 'At a PPE checkpoint beforehand' },
      ] },
      { key: 'checkpoint_zone_id', kind: 'zone', label: 'Checkpoint zone', zoneTypes: ['PPE_CHECKPOINT'], when: (p) => p.mode === 'VIA_CHECKPOINT' },
      { key: 'max_seconds_since_checkpoint', kind: 'number', label: 'Valid for (seconds after the checkpoint)', min: 5, max: 600, step: 5, when: (p) => p.mode === 'VIA_CHECKPOINT' },
    ],
  },
  {
    value: 'PPE_CONTINUOUS',
    label: 'Keep helmet and vest on while inside',
    blurb: 'Raises a flag for review when a required item is clearly not seen, without a break, for longer than the grace period while someone is in the zone. If the picture is unclear, nothing is flagged.',
    zoneTypes: ['RESTRICTED'],
    enforced: () => true,
    defaults: { required_ppe: ['helmet', 'safety_vest'], grace_seconds: 3 },
    fields: [
      { key: 'required_ppe', kind: 'ppe', label: 'Required items' },
      { key: 'grace_seconds', kind: 'number', label: 'Grace period (seconds)', min: 1, max: 30, step: 1 },
    ],
  },
  {
    value: 'MAX_DWELL',
    label: 'Longest stay',
    blurb: 'Raises a flag for review when someone stays in the zone longer than allowed. A warning is recorded earlier, at the chosen share of the limit (below 100%), but nothing is flagged until the limit is reached.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    enforced: () => true,
    defaults: { max_seconds: 300, warn_fraction: 0.8 },
    fields: [
      { key: 'max_seconds', kind: 'number', label: 'Longest stay (seconds)', min: 10, max: 86400, step: 10, required: true },
      { key: 'warn_fraction', kind: 'number', label: 'Warn at this share of the limit', min: 0.1, max: 1, step: 0.05 },
    ],
  },
  {
    value: 'REQUIRED_EXIT',
    label: 'Leave through the exit zone',
    blurb: 'After someone leaves the restricted zone, checks that they reach the exit zone in time. Going into another restricted zone first, or still being in view without reaching the exit, is flagged; passing through an approach or checkpoint zone on the way is fine. If they are out of view, it says it could not tell.',
    zoneTypes: ['RESTRICTED'],
    enforced: () => true,
    defaults: { max_seconds_to_exit: 30 },
    fields: [
      { key: 'exit_zone_id', kind: 'zone', label: 'Exit zone', zoneTypes: ['EXIT'], required: true },
      { key: 'max_seconds_to_exit', kind: 'number', label: 'Time allowed to reach it (seconds)', min: 3, max: 600, step: 1 },
    ],
  },
  {
    value: 'OCCUPANCY',
    label: 'People in the zone (lone worker, crowding)',
    blurb: 'Flags too few or too many people in the zone for too long.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    enforced: () => false,
    defaults: { min_people: 2, grace_seconds: 10 },
    fields: [
      { key: 'min_people', kind: 'number', label: 'At least this many people (optional)', min: 2, max: 100, step: 1 },
      { key: 'max_people', kind: 'number', label: 'At most this many people (optional)', min: 1, max: 1000, step: 1 },
      { key: 'grace_seconds', kind: 'number', label: 'Grace period (seconds)', min: 5, max: 3600, step: 5 },
    ],
  },
  {
    value: 'MAN_DOWN',
    label: 'Person lying down',
    blurb: 'Raises a critical flag when someone lies still for longer than the time below, so a person who may have fallen is found quickly. Sitting, crouching and bending over on their feet are not flagged, and if the picture is not clear enough it says it could not tell. Leave the place empty to watch everywhere, on every camera.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    zoneOptional: true,
    enforced: () => true,
    defaults: { min_seconds_down: 5 },
    fields: [
      { key: 'min_seconds_down', kind: 'number', label: 'Lying still for at least (seconds)', min: 3, max: 60, step: 1 },
    ],
  },
  {
    value: 'WALK_ONLY',
    label: 'Walk, do not run',
    blurb: 'Flags someone moving faster than walking pace in the zone.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    enforced: () => false,
    defaults: {},
    fields: [
      { key: 'max_speed_heights_per_s', kind: 'number', label: 'Fastest allowed (body heights per second)', min: 0.5, max: 10, step: 0.1 },
      { key: 'min_seconds', kind: 'number', label: 'For at least (seconds)', min: 0.5, max: 30, step: 0.5 },
    ],
  },
];

export const ruleTypeMeta = (value) => RULE_TYPES.find((t) => t.value === value) || null;

/** The type's default parameters, as a fresh object. */
export const defaultParams = (type) => JSON.parse(JSON.stringify((ruleTypeMeta(type) || {}).defaults || {}));

/** Is this rule acted on by the safety engine today? (The server also says, as `evaluated_by_engine`.) */
export function isEnforced(type, params) {
  const meta = ruleTypeMeta(type);
  return Boolean(meta && meta.enforced(params || {}));
}

/**
 * The parameters to send: only the fields this type's form shows (and that are currently visible), with
 * number fields as numbers and empty optional fields left out, so a stale value from another mode is never sent.
 */
export function cleanParams(type, params) {
  const meta = ruleTypeMeta(type);
  const out = {};
  if (!meta) return out;
  meta.fields.forEach((f) => {
    if (f.when && !f.when(params)) return;
    const v = params[f.key];
    if (v === undefined || v === null || v === '') return;
    out[f.key] = f.kind === 'number' ? Number(v) : v;
  });
  return out;
}

/** The body for create / validate / update, from the form's state. */
export function ruleBody(form) {
  return {
    name: (form.name || '').trim(),
    type: form.type,
    zone_id: form.zoneId || null,
    params: cleanParams(form.type, form.params || {}),
    severity: form.severity,
    is_enabled: form.isEnabled !== false,
    min_evidence_confidence: Number(form.minEvidenceConfidence),
  };
}

const FRIENDLY = [
  [/must be anchored to a RESTRICTED zone/i, 'This rule only applies to a Restricted zone. Choose a Restricted zone, or change the zone type on the Zones page.'],
  [/must be anchored to a zone/i, 'Choose the zone this rule applies to.'],
  [/is not one of this camera's zones/i, 'That zone is not available. Choose another one.'],
  [/needs a real zone/i, 'A General zone has no safety meaning. Choose a zone with a safety type.'],
  [/required_ppe must list at least one/i, 'Choose at least one item to check for.'],
  [/checkpoint_zone_id is required/i, 'Choose the checkpoint zone workers pass first.'],
  [/checkpoint_zone_id must be/i, 'The checkpoint must be a PPE checkpoint zone on the same camera.'],
  [/exit_zone_id is required/i, 'Choose the exit zone.'],
  [/exit_zone_id must be/i, 'The exit must be an Exit zone on the same camera.'],
  [/give min_people, max_people or both/i, 'Give a smallest number of people, a largest number, or both.'],
  [/min_people cannot be more than max_people/i, 'The smallest number cannot be larger than the largest.'],
  [/max_seconds is required/i, 'Say how long a stay may last.'],
];

/** A server validation sentence in words a site manager would use (anything unrecognised is shown as it came). */
export function friendlyRuleError(message) {
  const text = String(message || '');
  const hit = FRIENDLY.find(([re]) => re.test(text));
  return hit ? hit[1] : text;
}

/** 125 -> "2 min 5 s". Plain words for a length of time in the rule list. */
export function durationText(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return '';
  if (n < 60) return `${Math.round(n)} s`;
  const total = Math.round(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h) return m ? `${h} h ${m} min` : `${h} h`;
  return sec ? `${m} min ${sec} s` : `${m} min`;
}

/** One line saying what a rule asks, from its parameters. `zoneName(id)` turns a zone id into a name. */
export function ruleSummary(type, params, zoneName = () => null) {
  const p = params || {};
  const raw = (Array.isArray(p.required_ppe) ? p.required_ppe : []).map((i) => (i === 'safety_vest' ? 'vest' : i)).join(' and ');
  const items = raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : '';
  switch (type) {
    case 'PPE_ENTRY':
      return (p.mode || 'AT_ENTRY') === 'AT_ENTRY' ? `${items || 'Required items'} checked when someone enters` : 'Checked at a PPE checkpoint beforehand';
    case 'PPE_CONTINUOUS':
      return `${items || 'Required items'} checked all the time someone is inside; flagged after ${durationText(p.grace_seconds)} without a break`;
    case 'MAX_DWELL':
      return (p.warn_fraction ?? 0.8) < 1
        ? `Longest stay ${durationText(p.max_seconds)}; a warning is recorded at ${Math.round((p.warn_fraction ?? 0.8) * 100)}% of it`
        : `Longest stay ${durationText(p.max_seconds)}; no earlier warning`;
    case 'MAN_DOWN':
      return `Critical flag when someone lies still for ${durationText(p.min_seconds_down ?? 5)}`;
    case 'REQUIRED_EXIT': {
      const name = zoneName(p.exit_zone_id);
      return `After leaving, reach ${name ? `the exit zone "${name}"` : 'the exit zone'} within ${durationText(p.max_seconds_to_exit)}`;
    }
    default:
      return '';
  }
}
