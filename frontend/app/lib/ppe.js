// frontend/app/lib/ppe.js
//
// How a PPE verdict from the safety layer reads to a person.
//
// The backend sends, per person, an evidence-based status and the state of each
// item (PRESENT / ABSENT / UNVERIFIABLE). There is no "no helmet" detector: an
// absent item means "nothing seen in nearly every clear frame", so the wording
// says "not seen", never "is not wearing". A person who cannot be judged
// (too small, too far, partly hidden, or not enough frames yet) is "unverified",
// and that is shown plainly instead of being dressed up as compliant or not.

const ITEM_WORD = {
  PRESENT: 'seen',
  ABSENT: 'not seen',
  UNVERIFIABLE: "can't tell",
};

/**
 * @param ppe one entity's `ppe` object from the frame payload
 * @returns {{ label: string, tone: 'ok' | 'alert' | 'unknown', detail: string }}
 */
export function describePpe(ppe) {
  const helmet = (ppe && ppe.helmet_state) || 'UNVERIFIABLE';
  const vest = (ppe && ppe.vest_state) || 'UNVERIFIABLE';
  const detail = `Helmet: ${ITEM_WORD[helmet] || ITEM_WORD.UNVERIFIABLE} · Vest: ${ITEM_WORD[vest] || ITEM_WORD.UNVERIFIABLE}`;

  const status = ppe && ppe.status;
  if (status === 'COMPLIANT') return { label: 'PPE OK', tone: 'ok', detail };
  if (status === 'NON_COMPLIANT') {
    const missing = [];
    if (helmet === 'ABSENT') missing.push('Helmet');
    if (vest === 'ABSENT') missing.push('Vest');
    const label = missing.length === 2 ? 'Helmet and vest not seen'
      : missing.length === 1 ? `${missing[0]} not seen` : 'PPE not seen';
    return { label, tone: 'alert', detail };
  }
  return { label: 'PPE unverified', tone: 'unknown', detail };
}
