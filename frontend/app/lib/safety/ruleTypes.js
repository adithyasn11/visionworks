// frontend/app/lib/safety/ruleTypes.js
//
// What a manager can ask the safety rules to check, as data: one entry per rule type, with the
// parameters it takes and their limits. The rules editor draws its form from this list, and the
// limits are the same ones the backend enforces (backend/app/safety/rules.py, validate_params),
// which has the last word: the editor also asks the server to validate before saving.
//
// `enforced` says whether the safety engine acts on this kind of rule TODAY. Every type can be saved
// and validated, but only helmet-and-vest at entry is evaluated so far; the screen says so plainly
// rather than letting someone believe a dwell limit is protecting a doorway.

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
    blurb: 'Checks that required items stay on for as long as someone is in the zone.',
    zoneTypes: ['RESTRICTED'],
    enforced: () => false,
    defaults: { required_ppe: ['helmet', 'safety_vest'], grace_seconds: 3 },
    fields: [
      { key: 'required_ppe', kind: 'ppe', label: 'Required items' },
      { key: 'grace_seconds', kind: 'number', label: 'Grace period (seconds)', min: 1, max: 30, step: 1 },
    ],
  },
  {
    value: 'MAX_DWELL',
    label: 'Longest stay',
    blurb: 'Flags someone who stays in the zone longer than allowed.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    enforced: () => false,
    defaults: { max_seconds: 300, warn_fraction: 0.8 },
    fields: [
      { key: 'max_seconds', kind: 'number', label: 'Longest stay (seconds)', min: 10, max: 86400, step: 10, required: true },
      { key: 'warn_fraction', kind: 'number', label: 'Warn at this share of the limit', min: 0.1, max: 1, step: 0.05 },
    ],
  },
  {
    value: 'REQUIRED_EXIT',
    label: 'Leave through the exit zone',
    blurb: 'Flags someone who does not pass the exit zone after the restricted area.',
    zoneTypes: ['RESTRICTED'],
    enforced: () => false,
    defaults: { max_seconds_to_exit: 30 },
    fields: [
      { key: 'exit_zone_id', kind: 'zone', label: 'Exit zone', zoneTypes: ['EXIT'], required: true },
      { key: 'max_seconds_to_exit', kind: 'number', label: 'Time allowed to reach it (seconds)', min: 5, max: 3600, step: 5 },
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
    blurb: 'Flags someone who stays down. A whole-camera rule needs no zone.',
    zoneTypes: ['PPE_CHECKPOINT', 'APPROACH', 'RESTRICTED', 'EXIT'],
    zoneOptional: true,
    enforced: () => false,
    defaults: { min_seconds_down: 5 },
    fields: [
      { key: 'min_seconds_down', kind: 'number', label: 'Down for at least (seconds)', min: 3, max: 60, step: 1 },
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
