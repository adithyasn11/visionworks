// frontend/app/lib/zoneEvents.js
//
// How a zone event from the safety layer reads to a person.
//
// The backend sends facts ("ENTERED_ZONE", "trk-3", a zone id, a video time,
// some details). This turns one into a sentence and a tone, in one place, so the
// live feed and later screens (review queue, reports) say the same thing about
// the same event.
//
// Wording follows the product's rule of abstaining rather than accusing: a visit
// the system could not follow to its end is said to be unconfirmed, never that
// the person "left" or "stayed".

/** "Worker 3" from the anonymous per-camera key "trk-3". Nobody is named. */
export function workerLabel(key) {
  const match = /^trk-(\d+)$/.exec(String(key || ''));
  return match ? `Worker ${match[1]}` : 'Worker';
}

const CLOSED_BY = {
  LOST: 'lost from view, visit closed',
  SEEK: 'video jumped, visit closed',
  SESSION_END: 'still inside when the video ended',
  TIME_REGRESSED: 'video time went backwards, visit closed',
};

function seconds(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n)} s` : '';
}

/**
 * @param event one entry of payload.safety.events
 * @returns {{ text: string, tone: 'normal' | 'attention' | 'unconfirmed' }}
 *   `unconfirmed` marks a record the system closed without being able to see the
 *   person leave; the UI shows it quieter and says so.
 */
export function describeZoneEvent(event) {
  const worker = workerLabel(event && event.worker_key);
  const details = (event && event.details) || {};

  switch (event && event.event_type) {
    case 'ENTERED_ZONE':
      return {
        text: details.first_sighting
          ? `${worker} was already inside when first seen`
          : `${worker} entered`,
        tone: 'normal',
      };
    case 'EXITED_ZONE': {
      if (details.unverifiable || (details.reason && details.reason !== 'LEFT')) {
        const why = CLOSED_BY[details.reason] || 'visit closed, exit not seen';
        return { text: `${worker}: ${why}`, tone: 'unconfirmed' };
      }
      const stayed = seconds(details.dwell_s);
      return { text: `${worker} left${stayed ? ` after ${stayed}` : ''}`, tone: 'normal' };
    }
    case 'LOST_IN_ZONE':
      return { text: `${worker} lost from view inside`, tone: 'unconfirmed' };
    case 'REAPPEARED':
      return { text: `${worker} back in view`, tone: 'normal' };
    case 'DWELL_WARNING':
      return { text: `${worker} is nearing the ${seconds(details.limit_s)} limit`, tone: 'attention' };
    case 'DWELL_EXCEEDED':
      return { text: `${worker} passed the ${seconds(details.limit_s)} limit`, tone: 'attention' };
    default:
      return { text: `${worker}: ${String((event && event.event_type) || 'event').toLowerCase().replace(/_/g, ' ')}`, tone: 'normal' };
  }
}

/** How many events the live list keeps. Older ones scroll away; the database has them all. */
export const MAX_LIVE_EVENTS = 100;

/**
 * Add this frame's events to the list, newest first, capped. Each gets an `id`
 * so React keys stay stable however many arrive at once.
 */
export function addZoneEvents(list, incoming, nextId) {
  if (!Array.isArray(incoming) || incoming.length === 0) return list;
  const fresh = incoming
    .map((ev, i) => ({ ...ev, id: nextId + i }))
    .reverse();                       // newest first within the frame as well
  return [...fresh, ...list].slice(0, MAX_LIVE_EVENTS);
}
