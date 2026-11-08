// frontend/app/lib/zoneTypes.js
//
// What a safety zone can BE, in one place.
//
// The zone editor (where a zone is drawn and typed) and the live overlay (where
// it is watched) both need the same list and the same colours. Two copies drift,
// and a zone that is one colour where it is drawn and another where it is
// watched reads as two different zones.
//
// A zone's type decides which safety rules can attach to it:
//   PPE_CHECKPOINT  where helmet and vest are inspected before entry
//   APPROACH        the walkway between the checkpoint and the hazard
//   RESTRICTED      the hazardous area the rules protect
//   EXIT            the required exit / decontamination point
//   GENERAL         an ordinary area; no rules apply
//
// Must stay in step with SAFETY_ZONE_TYPES in backend/app/api/routers/zones.py.
//
// COLOURS stay inside the product's red / black / white / grey palette. Types
// are told apart by lightness along the red ramp plus white and neutral grey,
// never by competing hues, so the editor still looks like the rest of the app.
// Identity never rests on colour alone: every zone also carries a text badge.

export const ZONE_TYPES = [
  {
    value: 'RESTRICTED',
    label: 'Restricted',
    badge: 'RESTR',
    color: '#DC2626',
    hint: 'The hazardous area. Rules such as “helmet and vest to enter” attach here',
  },
  {
    value: 'PPE_CHECKPOINT',
    label: 'PPE checkpoint',
    badge: 'PPE',
    color: '#F7B4B0',
    hint: 'Where helmet and vest are inspected before entry',
  },
  {
    value: 'APPROACH',
    label: 'Approach',
    badge: 'APPR',
    color: '#F0736F',
    hint: 'The walkway between the checkpoint and the restricted area',
  },
  {
    value: 'EXIT',
    label: 'Exit',
    badge: 'EXIT',
    color: '#FFFFFF',
    hint: 'The required way out after the restricted area',
  },
  {
    value: 'GENERAL',
    label: 'General',
    badge: 'GEN',
    color: '#8B8792',
    hint: 'An ordinary area. No rules apply',
  },
];

const BY_VALUE = Object.fromEntries(ZONE_TYPES.map((t) => [t.value, t]));

/**
 * The type to show for a stored value.
 *
 * Zones drawn before the safety types existed carry an older value, or none.
 * They have no safety meaning, so they read as General rather than as a type
 * the editor cannot offer. The stored value is left alone until somebody
 * edits the zone.
 */
export function zoneTypeMeta(value) {
  return BY_VALUE[String(value || '').toUpperCase()] ?? BY_VALUE.GENERAL;
}

export const zoneTypeColor = (value) => zoneTypeMeta(value).color;

/**
 * The text drawn on a zone's polygon: its short type badge, then its name, e.g.
 * "RESTR · Press line". Shared by the editor canvas and the live overlay so the
 * two can never label the same zone differently. A legacy or missing type reads
 * as GEN, and a zone with no name falls back to its id, then to `fallback`.
 */
export function zoneCanvasLabel(zone, fallback = '') {
  const name = (zone && (zone.zone_name || zone.zone_id)) || fallback;
  const badge = zoneTypeMeta(zone && zone.zone_type).badge;
  return name ? `${badge} · ${name}` : badge;
}
