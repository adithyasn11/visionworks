// frontend/app/lib/decisions.js
//
// How a safety decision from the rule engine reads to a person.
//
// A decision is COMPLIANT, POTENTIAL_VIOLATION or UNVERIFIABLE, with the sentence
// that explains it. The wording is deliberate: a flag is "potential" (a person
// reviews it), and "cannot verify" is shown as a result in its own right rather than
// hidden or dressed up as either of the others.

const STATUS = {
  COMPLIANT: { label: 'Compliant', tone: 'ok' },
  POTENTIAL_VIOLATION: { label: 'Potential violation', tone: 'alert' },
  UNVERIFIABLE: { label: 'Cannot verify', tone: 'unknown' },
};

/** @returns {{ label: string, tone: 'ok' | 'alert' | 'unknown' }} */
export function decisionStatus(status) {
  return STATUS[status] || { label: 'Unknown', tone: 'unknown' };
}

/**
 * "93% sure" for a decision that makes a claim; nothing for one that does not. An
 * abstention has no confidence, and showing "0%" would read as a verdict.
 */
export function confidenceLabel(decision) {
  if (!decision || decision.status === 'UNVERIFIABLE') return '';
  const c = Number(decision.confidence);
  return Number.isFinite(c) && c > 0 ? `${Math.round(c * 100)}% sure` : '';
}

/** How many decisions the live list keeps. The database has them all. */
export const MAX_LIVE_DECISIONS = 100;

/** Add this frame's decisions, newest first, capped, never the same id twice. */
export function addDecisions(list, incoming) {
  if (!Array.isArray(incoming) || incoming.length === 0) return list;
  const known = new Set(list.map((d) => d.id));
  const fresh = incoming.filter((d) => d && d.id && !known.has(d.id)).reverse();
  if (fresh.length === 0) return list;
  return [...fresh, ...list].slice(0, MAX_LIVE_DECISIONS);
}

/** Counts for the header: how many of each status are in the list. */
export function tally(list) {
  const out = { COMPLIANT: 0, POTENTIAL_VIOLATION: 0, UNVERIFIABLE: 0 };
  (list || []).forEach((d) => { if (d && d.status in out) out[d.status] += 1; });
  return out;
}
