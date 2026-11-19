// frontend/app/lib/alarm.js
//
// The sound for a new CRITICAL flag (a person lying down, a critical safety-gear rule, a no-entry zone set to
// critical). Made with the Web Audio API, so there is no sound file to load. Each flag sounds once, whichever
// screen saw it first (the live feed or the dashboard's poll). The sound can be switched off per browser.

const KEY = 'vw-alarm-on';
const heard = new Set();
let ctx = null;
let playingUntil = 0;

export function alarmEnabled() {
  try { return localStorage.getItem(KEY) !== 'off'; } catch { return true; }
}

export function setAlarmEnabled(on) {
  try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch { /* private window: stays on */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('vw-alarm-changed', { detail: on }));
}

function audio() {
  if (typeof window === 'undefined') return null;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!ctx) ctx = new AC();
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}

// Browsers only allow sound after the person has clicked or pressed a key on the page once; prepare the
// audio on that first touch so a flag that arrives later can sound.
if (typeof window !== 'undefined') {
  const unlock = () => { audio(); window.removeEventListener('pointerdown', unlock); window.removeEventListener('keydown', unlock); };
  window.addEventListener('pointerdown', unlock);
  window.addEventListener('keydown', unlock);
}

/** A two-tone siren, about 2.4 s long. `force` plays it even when switched off (the "test" button). */
export function playAlarm(force = false) {
  if (!force && !alarmEnabled()) return false;
  const ac = audio();
  if (!ac) return false;
  const now = ac.currentTime;
  if (!force && now < playingUntil) return true;              // already sounding: do not stack
  const gain = ac.createGain();
  gain.connect(ac.destination);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.35, now + 0.03);
  const osc = ac.createOscillator();
  osc.type = 'square';
  osc.connect(gain);
  const beeps = 6;
  for (let i = 0; i < beeps; i += 1) {
    osc.frequency.setValueAtTime(i % 2 ? 660 : 990, now + i * 0.4);
  }
  const end = now + beeps * 0.4;
  gain.gain.setValueAtTime(0.35, end - 0.05);
  gain.gain.exponentialRampToValueAtTime(0.0001, end);
  osc.start(now);
  osc.stop(end + 0.02);
  playingUntil = end;
  return true;
}

const isCritical = (d) => d && String(d.severity || '').toUpperCase() === 'CRITICAL'
  && (!d.status || d.status === 'POTENTIAL_VIOLATION');

/**
 * Sound once for any CRITICAL flag in `decisions` not heard before. With `prime`, only remember them (the first
 * load of a screen must not ring for flags that were already waiting).
 */
export function alarmFor(decisions, { prime = false } = {}) {
  let fresh = false;
  (decisions || []).forEach((d) => {
    if (!isCritical(d) || !d.id || heard.has(d.id)) return;
    heard.add(d.id);
    fresh = true;
  });
  if (fresh && !prime) playAlarm();
  return fresh;
}
