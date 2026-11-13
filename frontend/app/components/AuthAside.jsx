'use client';

// frontend/app/components/AuthAside.jsx
// Left-hand panel for the sign-in / sign-up screens.
//
// Little text, one live scene: what the product does, played out on a camera view. Workers (drawn as pose
// skeletons with their keypoints, faces blurred) walk through a PPE checkpoint, into a restricted zone and
// out through an exit. A scan line sweeps the frame, detection boxes lock on, the helmet and vest are checked,
// and each worker gets the verdict the real pipeline would give: compliant, flagged (missing PPE, with a
// pulsing ring) or could-not-verify (a worker hidden behind something). Now and then someone falls in the
// exit zone and a critical man-down alert fires until they get up. A short event log under the scene reads
// out what just happened. It is an illustration, not live data.
//
// One canvas and one animation loop; colours follow the theme (read once, then on theme change).
import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

/* ── Scene ───────────────────────────────────────────────────────────────── */

const ZONES = [
  { id: 'checkpoint', label: 'PPE CHECKPOINT', x: 0.04, y: 0.10, w: 0.40, h: 0.44 },
  { id: 'restricted', label: 'RESTRICTED',     x: 0.56, y: 0.10, w: 0.40, h: 0.44 },
  { id: 'exit',       label: 'EXIT',           x: 0.04, y: 0.64, w: 0.92, h: 0.28 },
];

// The route every worker takes: in from the left, stop at the checkpoint, into the restricted zone, down to
// the exit, out to the right. `stop` is how long (s) they pause there and what happens.
const ROUTE = [
  { x: -0.06, y: 0.40 },
  { x: 0.24,  y: 0.40, stop: 1.1, at: 'checkpoint' },
  { x: 0.76,  y: 0.40, stop: 0.9, at: 'restricted' },
  { x: 0.56,  y: 0.86, stop: 0.6, at: 'exit' },
  { x: 1.06,  y: 0.86 },
];

const HELMET = '#F5A300';   // hard-hat amber
const VEST = '#B9E03B';     // hi-vis lime
const OK = '#10B981';

let nextId = 3;
function newWorker(delay) {
  const r = Math.random();
  nextId = nextId >= 29 ? 3 : nextId + Math.ceil(Math.random() * 4);
  return {
    id: nextId,
    helmet: r > 0.22,
    vest: r < 0.12 ? false : Math.random() > 0.15,
    hidden: Math.random() < 0.12,          // partly behind something: the system will not guess
    seg: 0, t: 0, pause: 0, wait: delay,
    speed: 0.16 + Math.random() * 0.07,    // route lengths per second
    phase: Math.random() * 6,
    jitter: (Math.random() - 0.5) * 0.10,
    verdict: null, checked: false, lock: 0, pulse: 0,
    fall: 0, fallen: false, fellOnce: false, alerted: false,
  };
}

function lerp(a, b, t) { return a + (b - a) * t; }
function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

function LiveScene() {
  const canvasRef = useRef(null);
  const [log, setLog] = useState([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let w = 0, h = 0;
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = r.width; h = r.height;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // Theme colours, read once and again only when the theme changes.
    let light = true, fg = '11,10,12', accent = '#DC2626';
    const readTheme = () => {
      const root = document.documentElement;
      light = !root.classList.contains('dark');
      fg = light ? '11,10,12' : '247,246,248';
      accent = getComputedStyle(root).getPropertyValue('--accent').trim() || '#DC2626';
    };
    readTheme();
    const themeWatch = new MutationObserver(readTheme);
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    const ink = (a) => `rgba(${fg},${light ? Math.min(1, a * 1.25) : a})`;

    const events = [];
    const say = (text, tone) => {
      const now = new Date();
      events.unshift({ key: `${now.getTime()}-${Math.random()}`, stamp: now.toTimeString().slice(0, 8), text, tone });
      events.length = Math.min(events.length, 4);
      setLog([...events]);
    };

    const workers = [newWorker(0), newWorker(1.6), newWorker(3.3), newWorker(5.0)];
    let last = performance.now();
    let scan = 0;
    let raf = 0;

    const segLen = (i) => {
      const a = ROUTE[i], b = ROUTE[i + 1];
      return Math.hypot((b.x - a.x) * w, (b.y - a.y) * h) / Math.max(w, 1);
    };

    const step = (wk, dt) => {
      if (wk.wait > 0) { wk.wait -= dt; return; }
      if (wk.fallen) {
        wk.fall += dt;
        if (wk.fall > 1.0 && !wk.alerted) { wk.alerted = true; wk.pulse = 1; say(`#${wk.id} lying still · MAN DOWN`, 'bad'); }
        if (wk.fall > 3.2) { wk.fallen = false; wk.alerted = false; wk.pulse = 0; wk.fall = 0; say(`#${wk.id} back on their feet`, 'ok'); }
        return;
      }
      if (wk.pause > 0) { wk.pause -= dt; return; }
      wk.t += (wk.speed * dt) / Math.max(segLen(wk.seg), 0.05);
      wk.phase += dt * 9;
      if (wk.t >= 1) {
        wk.t = 0;
        wk.seg += 1;
        if (wk.seg >= ROUTE.length - 1) { Object.assign(wk, newWorker(0.6 + Math.random() * 1.2)); return; }
        const stop = ROUTE[wk.seg];
        if (stop.stop) wk.pause = stop.stop;
        if (stop.at === 'checkpoint') {
          wk.checked = true; wk.lock = 1;
          say(`#${wk.id} at checkpoint · helmet ${wk.helmet ? '✓' : '✗'} vest ${wk.vest ? '✓' : '✗'}`, wk.helmet && wk.vest ? 'ok' : 'warn');
        } else if (stop.at === 'restricted') {
          wk.lock = 1;
          if (wk.hidden) { wk.verdict = 'UNVERIFIABLE'; say(`#${wk.id} entered restricted · could not verify`, 'muted'); }
          else if (wk.helmet && wk.vest) { wk.verdict = 'COMPLIANT'; say(`#${wk.id} entered restricted · compliant`, 'ok'); }
          else { wk.verdict = 'FLAGGED'; wk.pulse = 1; say(`#${wk.id} no ${!wk.helmet ? 'helmet' : 'vest'} → flagged for review`, 'bad'); }
        } else if (stop.at === 'exit') {
          if (!wk.fellOnce && Math.random() < 0.28) { wk.fallen = true; wk.fellOnce = true; wk.alerted = false; wk.fall = 0; }
        }
      }
    };

    const pos = (wk) => {
      const a = ROUTE[wk.seg], b = ROUTE[Math.min(wk.seg + 1, ROUTE.length - 1)];
      const e = ease(wk.t);
      return { x: lerp(a.x, b.x, e) * w, y: (lerp(a.y, b.y, e) + wk.jitter) * h };
    };

    const figure = (wk, x, y, s) => {
      const lying = wk.fallen ? Math.min(1, wk.fall / 0.35) : 0;
      const walking = wk.pause <= 0 && !wk.fallen && wk.wait <= 0;
      const sw = walking ? Math.sin(wk.phase) * 0.55 : 0;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(lying * (Math.PI / 2) * 0.98);
      ctx.globalAlpha = wk.hidden ? 0.45 : 1;
      const j = {
        head: [0, -s * 0.86], neck: [0, -s * 0.70], hip: [0, -s * 0.34],
        lk: [Math.sin(sw) * s * 0.16, -s * 0.17], rk: [-Math.sin(sw) * s * 0.16, -s * 0.17],
        lf: [Math.sin(sw) * s * 0.30, 0], rf: [-Math.sin(sw) * s * 0.30, 0],
        lh: [-Math.sin(sw) * s * 0.26, -s * 0.40], rh: [Math.sin(sw) * s * 0.26, -s * 0.40],
      };
      ctx.lineCap = 'round';
      ctx.strokeStyle = ink(0.85);
      ctx.lineWidth = Math.max(1.6, s * 0.06);
      const line = (a, b) => { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
      line(j.neck, j.hip); line(j.hip, j.lk); line(j.lk, j.lf); line(j.hip, j.rk); line(j.rk, j.rf);
      line(j.neck, j.lh); line(j.neck, j.rh);
      if (wk.vest) {                                          // hi-vis vest over the torso
        ctx.strokeStyle = VEST; ctx.lineWidth = Math.max(4, s * 0.17);
        line([0, -s * 0.66], [0, -s * 0.42]);
      }
      // head, blurred: a soft disc instead of a face
      ctx.fillStyle = ink(0.30);
      ctx.beginPath(); ctx.arc(j.head[0], j.head[1], s * 0.15, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = ink(0.16);
      ctx.beginPath(); ctx.arc(j.head[0], j.head[1], s * 0.20, 0, Math.PI * 2); ctx.fill();
      if (wk.helmet) {                                        // hard hat
        ctx.fillStyle = HELMET;
        ctx.beginPath(); ctx.arc(j.head[0], j.head[1] - s * 0.02, s * 0.165, Math.PI, 0); ctx.fill();
        ctx.fillRect(j.head[0] - s * 0.21, j.head[1] - s * 0.03, s * 0.42, s * 0.035);
      }
      // pose keypoints, the way the pose model sees a person
      ctx.fillStyle = accent;
      for (const k of ['neck', 'hip', 'lk', 'rk', 'lf', 'rf', 'lh', 'rh']) {
        ctx.beginPath(); ctx.arc(j[k][0], j[k][1], Math.max(1.4, s * 0.035), 0, Math.PI * 2); ctx.fill();
      }
      ctx.restore();
    };

    const bracket = (x, y, bw, bh, color, dashed) => {
      const c = Math.min(bw, bh) * 0.28;
      ctx.strokeStyle = color; ctx.lineWidth = 1.6;
      ctx.setLineDash(dashed ? [3, 3] : []);
      const L = x - bw / 2, R = x + bw / 2, T = y - bh, B = y;
      ctx.beginPath();
      ctx.moveTo(L, T + c); ctx.lineTo(L, T); ctx.lineTo(L + c, T);
      ctx.moveTo(R - c, T); ctx.lineTo(R, T); ctx.lineTo(R, T + c);
      ctx.moveTo(R, B - c); ctx.lineTo(R, B); ctx.lineTo(R - c, B);
      ctx.moveTo(L + c, B); ctx.lineTo(L, B); ctx.lineTo(L, B - c);
      ctx.stroke();
      ctx.setLineDash([]);
    };

    const draw = (now) => {
      const dt = reduced ? 0 : Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, w, h);
      const s = Math.max(30, Math.min(84, h * 0.2, w * 0.13));

      // faint grid
      ctx.strokeStyle = ink(0.06); ctx.lineWidth = 1;
      for (let gx = 0; gx < w; gx += 28) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke(); }
      for (let gy = 0; gy < h; gy += 28) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke(); }

      // zones
      ZONES.forEach((z) => {
        const x = z.x * w, y = z.y * h, zw = z.w * w, zh = z.h * h;
        const hot = z.id === 'restricted';
        ctx.fillStyle = hot ? 'rgba(220,38,38,0.07)' : ink(0.035);
        ctx.fillRect(x, y, zw, zh);
        ctx.strokeStyle = hot ? 'rgba(220,38,38,0.55)' : ink(0.35);
        ctx.setLineDash(z.id === 'exit' ? [5, 4] : []);
        ctx.lineWidth = 1.2;
        ctx.strokeRect(x, y, zw, zh);
        ctx.setLineDash([]);
        ctx.fillStyle = hot ? accent : ink(0.75);
        ctx.font = '700 10px ui-monospace, SFMono-Regular, Menlo, monospace';
        ctx.fillText(z.label, x + 8, y + 15);
      });

      // scan line sweeping across the frame
      scan = (scan + dt * 0.42) % 1.25;
      if (scan <= 1) {
        const sx = scan * w;
        const g = ctx.createLinearGradient(sx - 40, 0, sx, 0);
        g.addColorStop(0, 'rgba(220,38,38,0)');
        g.addColorStop(1, 'rgba(220,38,38,0.18)');
        ctx.fillStyle = g; ctx.fillRect(sx - 40, 0, 40, h);
        ctx.strokeStyle = 'rgba(220,38,38,0.65)'; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, h); ctx.stroke();
      }

      // workers, back to front
      const placed = workers.map((wk) => { step(wk, dt); return { wk, p: pos(wk) }; })
        .filter(({ wk }) => wk.wait <= 0)
        .sort((a, b) => a.p.y - b.p.y);

      for (const { wk, p } of placed) {
        if (p.x < -s || p.x > w + s) continue;
        // a pass of the scan line re-locks the box
        if (scan <= 1 && Math.abs(scan * w - p.x) < 6) wk.lock = Math.max(wk.lock, 0.6);
        figure(wk, p.x, p.y, s);

        const down = wk.fallen && wk.alerted;
        const bad = wk.verdict === 'FLAGGED' || down;
        const bw = wk.fallen ? s * 1.05 : s * 0.62, bh = wk.fallen ? s * 0.5 : s * 1.12;
        const color = bad ? accent : wk.verdict === 'UNVERIFIABLE' ? ink(0.5) : wk.verdict === 'COMPLIANT' ? OK : ink(0.7);
        const grow = 1 + wk.lock * 0.25;
        const bx = p.x + (wk.fallen ? s * 0.45 : 0);
        bracket(bx, p.y + 2, bw * grow, bh * grow, color, wk.verdict === 'UNVERIFIABLE');
        wk.lock = Math.max(0, wk.lock - dt * 2.2);

        // label
        const tag = down ? 'MAN DOWN'
          : wk.verdict === 'COMPLIANT' ? 'OK' : wk.verdict === 'FLAGGED' ? (!wk.helmet ? 'NO HELMET' : 'NO VEST')
            : wk.verdict === 'UNVERIFIABLE' ? '?' : wk.checked ? 'CHECKED' : 'TRACKING';
        const text = `#${wk.id} ${tag}`;
        ctx.font = '700 10px ui-monospace, SFMono-Regular, Menlo, monospace';
        const tw = ctx.measureText(text).width + 10;
        const lx = Math.max(2, Math.min(w - tw - 2, bx - tw / 2)), ly = p.y - bh * grow - 18;
        ctx.fillStyle = bad ? accent : wk.verdict === 'COMPLIANT' ? OK : ink(0.85);
        ctx.fillRect(lx, ly, tw, 15);
        ctx.fillStyle = light || bad || wk.verdict === 'COMPLIANT' ? '#FFFFFF' : '#0B0A0C';
        ctx.fillText(text, lx + 5, ly + 11);

        // alert ring, pulsing while a worker is flagged or down
        if (bad && wk.pulse > 0) {
          const r = (1 - wk.pulse) * s * 1.6 + s * 0.3;
          ctx.strokeStyle = `rgba(220,38,38,${wk.pulse * 0.8})`; ctx.lineWidth = 2.5;
          ctx.beginPath(); ctx.arc(bx, p.y - (wk.fallen ? s * 0.1 : s * 0.45), r, 0, Math.PI * 2); ctx.stroke();
          wk.pulse -= dt * 0.9;
          if (wk.pulse <= 0) wk.pulse = 1;
        }
      }

      // viewfinder: corners, REC and the clock
      const m = 8, cl = 16;
      ctx.strokeStyle = ink(0.8); ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(m, m + cl); ctx.lineTo(m, m); ctx.lineTo(m + cl, m);
      ctx.moveTo(w - m - cl, m); ctx.lineTo(w - m, m); ctx.lineTo(w - m, m + cl);
      ctx.moveTo(w - m, h - m - cl); ctx.lineTo(w - m, h - m); ctx.lineTo(w - m - cl, h - m);
      ctx.moveTo(m + cl, h - m); ctx.lineTo(m, h - m); ctx.lineTo(m, h - m - cl);
      ctx.stroke();
      if (Math.floor(now / 600) % 2 === 0) {
        ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(w - 58, h - 17, 4, 0, Math.PI * 2); ctx.fill();
      }
      ctx.fillStyle = ink(0.85);
      ctx.font = '700 10px ui-monospace, SFMono-Regular, Menlo, monospace';
      ctx.fillText('REC', w - 50, h - 13.5);
      ctx.fillText(new Date().toTimeString().slice(0, 8), m + 8, h - 13.5);

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); themeWatch.disconnect(); };
  }, []);

  const toneClass = { ok: 'text-emerald-600 dark:text-emerald-400', bad: 'text-accent', warn: 'text-amber-600 dark:text-amber-400', muted: 'text-ink-muted' };

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <canvas
        ref={canvasRef}
        className="w-full block flex-1 min-h-[220px] rounded-xl border border-white/10"
        role="img"
        aria-label="Animated camera view: workers walk through a PPE checkpoint into a restricted zone and out through an exit; each is checked for a helmet and vest and labelled compliant, flagged or could not verify, and a fall raises a man-down alert."
      />
      {/* What just happened, newest first. */}
      <ol className="font-mono text-[0.6875rem] leading-[1.55] min-h-[4.6rem]" aria-hidden="true">
        {log.map((e, i) => (
          <li key={e.key} className={`flex gap-3 auth-log-line ${i > 0 ? 'opacity-60' : ''}`}>
            <span className="text-ink-faint shrink-0">{e.stamp}</span>
            <span className={`${toneClass[e.tone] || ''} truncate`}>{e.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* ── Panel ───────────────────────────────────────────────────────────────── */

// `sub` and `facts` are still accepted from the pages but no longer shown: the scene says it.
export const AuthAside = ({ eyebrow, headline }) => (
  <aside className="card-dark relative hidden lg:flex flex-col gap-[clamp(1rem,2.6vh,2rem)] overflow-hidden h-full min-h-0 auth-pane">
    <div className="relative z-10 shrink-0 flex items-center justify-between">
      <Link href="/" className="flex items-center gap-2.5 w-max group">
        <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg group-hover:rotate-12 transition-transform duration-300">
          <div className="w-2 h-2 bg-white rounded-sm" />
        </div>
        <span className="font-extrabold text-lg tracking-tight">VisionWorks</span>
      </Link>
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-accent flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
        {eyebrow}
      </span>
    </div>

    <h2 className="relative z-10 shrink-0 text-2xl xl:text-[1.75rem] font-black tracking-tight leading-[1.2] text-balance max-w-sm">
      {headline}
    </h2>

    <div className="relative z-10 flex-1 min-h-0 flex flex-col">
      <LiveScene />
    </div>
  </aside>
);

export default AuthAside;
