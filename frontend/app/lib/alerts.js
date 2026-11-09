// frontend/app/lib/alerts.js
//
// Alerts in the safety product are POTENTIAL_VIOLATION decisions that nobody has
// reviewed yet. There is no separate alert record: the decision is the single source of
// truth, and an alert stops being open when a person reviews it.
//
// This file holds the small, pure pieces both screens share: how severe an alert
// looks, and the short-lived banners the live view raises when a new one arrives.

const SEVERITY = {
  CRITICAL: { label: 'Critical', rank: 3, tone: 'critical' },
  WARNING: { label: 'Warning', rank: 2, tone: 'warning' },
  INFO: { label: 'Info', rank: 1, tone: 'info' },
};

/** @returns {{ label: string, rank: number, tone: 'critical' | 'warning' | 'info' }} */
export function severityMeta(severity) {
  return SEVERITY[severity] || SEVERITY.WARNING;
}

/** An alert is a flag still waiting for a person. UNVERIFIABLE and COMPLIANT decisions are not alerts. */
export function isAlert(decision) {
  return Boolean(decision) && decision.status === 'POTENTIAL_VIOLATION';
}

/** How long a live banner stays up before it clears itself (the decision itself stays in the list). */
export const BANNER_MS = 12000;
/** At most this many banners are shown at once; the oldest is dropped first. */
export const MAX_BANNERS = 3;

/**
 * Add the alerts among `incoming` decisions to the list of banners, newest first, never the
 * same decision twice, capped at MAX_BANNERS. Returns the same array when nothing changed, so
 * a frame with no new alert causes no re-render.
 */
export function addBanners(list, incoming, now = Date.now()) {
  if (!Array.isArray(incoming) || incoming.length === 0) return list;
  const known = new Set(list.map((b) => b.id));
  const fresh = incoming
    .filter((d) => isAlert(d) && d.id && !known.has(d.id))
    .map((d) => ({ ...d, raisedAt: now }));
  if (fresh.length === 0) return list;
  return [...fresh.reverse(), ...list].slice(0, MAX_BANNERS);
}

/** Drop banners older than BANNER_MS. Same array back when none expired. */
export function expireBanners(list, now = Date.now()) {
  const kept = list.filter((b) => now - b.raisedAt < BANNER_MS);
  return kept.length === list.length ? list : kept;
}
