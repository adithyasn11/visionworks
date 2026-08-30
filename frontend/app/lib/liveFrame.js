// frontend/app/lib/liveFrame.js
//
// The most recent processed frame, shared between the player and the zone
// editor.
//
// WHY A MODULE AND NOT A PROP
//
// The zone editor needs to draw polygons over what the camera is seeing right
// now — you press "Turn On Camera" on the live feed, switch to Zones, and the
// same view should be there to aim at. The frame arrives on a WebSocket owned
// by VideoCanvasPlayer, and the two components sit in different branches of
// the dashboard tree.
//
// Threading it through as state would re-render the whole dashboard at the
// frame rate: every chart, the heatmap, the alerts panel, several times a
// second, to update one canvas. A subscription means only the component that
// wants frames pays for them, and only while it is listening.
//
// WHY IT HOLDS THE LATEST FRAME RATHER THAN STREAMING
//
// A subscriber that mounts halfway through a session should not wait for the
// next frame to have something to show. `getLatestFrame()` returns whatever
// arrived last, so the editor paints immediately and then updates.
//
// This is deliberately NOT a general event bus. One value, one shape, one
// purpose — a shared mutable store that anything can write to is how state
// becomes impossible to reason about.

let latestFrame = null;      // data: URL of the most recent processed frame
let latestAt = 0;            // Date.now() when it arrived
const subscribers = new Set();

/** How long a frame stays "live" before it is treated as stale. */
export const FRAME_STALE_MS = 4000;

/**
 * Called by the player for every frame it receives.
 *
 * Cheap on purpose: this runs several times a second, so it does no work
 * beyond storing the value and notifying whoever asked. A subscriber that
 * throws is isolated — one broken listener must not stop the video.
 */
export function publishFrame(dataUrl) {
  latestFrame = dataUrl || null;
  latestAt = latestFrame ? Date.now() : 0;
  subscribers.forEach((fn) => {
    try {
      fn(latestFrame);
    } catch {
      // A subscriber's failure is its own problem.
    }
  });
}

/** Called when the session ends, so nothing shows a frozen last frame as live. */
export function clearFrame() {
  latestFrame = null;
  latestAt = 0;
  subscribers.forEach((fn) => {
    try {
      fn(null);
    } catch { /* as above */ }
  });
}

/**
 * The most recent frame, or null when there is none or it has gone stale.
 *
 * Staleness matters: if the pipeline stops, the last frame stays in memory and
 * would otherwise be presented as the live view indefinitely. Drawing zones
 * against a frozen picture of where people were a minute ago is worse than
 * drawing against nothing, because nothing is obviously nothing.
 */
export function getLatestFrame() {
  if (!latestFrame) return null;
  if (Date.now() - latestAt > FRAME_STALE_MS) return null;
  return latestFrame;
}

/** True when a session is actively producing frames. */
export function isLive() {
  return getLatestFrame() !== null;
}

/** Subscribe. Returns the unsubscribe function. */
export function subscribeToFrames(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}
