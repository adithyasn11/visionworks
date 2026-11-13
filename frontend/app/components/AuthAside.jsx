'use client';

// frontend/app/components/AuthAside.jsx
// Left-hand panel for the sign-in / sign-up screens.
//
// Little text, one live scene: what the product does, played out on a camera view. Workers (pose skeletons
// with their keypoints, faces blurred) wander the floor on routes of their own: in from any edge, through the
// PPE checkpoint, often into the restricted zone, sometimes loitering, then out. They steer, turn, slow down,
// pause and keep out of each other's way, and smaller figures are further from the camera. A scan line
// sweeps the frame, tracking trails follow each person, the helmet and vest are checked, and each worker gets
// the verdict the real pipeline would give: compliant, flagged (missing PPE) or could-not-verify (partly
// hidden). Now and then someone falls and a critical man-down alert fires until they get up. A live event
// feed under the scene reads out what just happened. It is an illustration, not live data.
//
// One canvas and one animation loop; colours follow the theme (read once, then on theme change).
import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

/* ── Scene ───────────────────────────────────────────────────────────────── */

const ZONES = [
  { id: 'checkpoint', label: 'PPE CHECKPOINT', x: 0.05, y: 0.15, w: 0.36, h: 0.38 },
  { id: 'restricted', label: 'RESTRICTED',     x: 0.59, y: 0.15, w: 0.36, h: 0.38 },
  { id: 'exit',       label: 'EXIT',           x: 0.05, y: 0.66, w: 0.90, h: 0.22 },
];

const HELMET = '#F5A300';   // hard-hat amber
const VEST = '#B9E03B';     // hi-vis lime
const OK = '#10B981';
const WARN = '#F59E0B';
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const inZone = (z, x, y) => x >= z.x && x <= z.x + z.w && y >= z.y && y <= z.y + z.h;
const zoneAt = (x, y) => ZONES.find((z) => inZone(z, x, y))?.id ?? null;
const pointIn = (id, m = 0.05) => {
  const z = ZONES.find((q) => q.id === id);
  // feet in the lower part of the zone, so the person and their label stay inside the frame
  return { x: rand(z.x + m, z.x + z.w - m), y: rand(Math.max(0.36, z.y + z.h * 0.5), z.y + z.h - 0.03) };
};
const edgePoint = () => {
  const side = pick(['left', 'left', 'right', 'bottom']);
  if (side === 'left') return { x: -0.07, y: rand(0.30, 0.95) };
  if (side === 'right') return { x: 1.07, y: rand(0.30, 0.95) };
  return { x: rand(0.15, 0.85), y: 1.08 };
};

// A route of its own for every worker: always the checkpoint first, then a mix of the rest.
function planRoute() {
  const pts = [{ ...pointIn('checkpoint'), stop: rand(0.7, 1.6) }];
  if (Math.random() < 0.7) pts.push({ ...pointIn('restricted'), stop: rand(0.2, 1.8) });
  if (Math.random() < 0.45) pts.push({ x: rand(0.15, 0.85), y: rand(0.50, 0.64), stop: rand(0, 1.2) });  // loiter on the floor
  if (Math.random() < 0.35) pts.push({ ...pointIn(pick(['restricted', 'checkpoint'])), stop: rand(0.2, 1.0) });
  if (Math.random() < 0.6) pts.push({ ...pointIn('exit'), stop: rand(0, 0.8) });
  pts.push({ ...edgePoint(), stop: 0, out: true });
  return pts;
}

let nextId = 11;
function newWorker(delay) {
  nextId = nextId >= 96 ? 11 : nextId + 1 + Math.floor(Math.random() * 5);
  const start = edgePoint();
  const r = Math.random();
  return {
    id: nextId,
    x: start.x, y: start.y,
    heading: Math.atan2(0.5 - start.y, 0.3 - start.x),
    route: planRoute(), leg: 0,
    helmet: r > 0.24,
    vest: r < 0.10 ? false : Math.random() > 0.16,
    hidden: Math.random() < 0.12,          // partly behind something: the system will not guess
    pace: rand(0.85, 1.25),                // own walking speed
    wobble: rand(0, 10),
    stride: 0, wait: delay, stop: 0, moving: 0,
    zone: null, checked: false, verdict: null,
    lock: 0, pulse: 0, trail: [],
    fallen: false, fall: 0, fellOnce: false, alerted: false,
  };
}

// How each kind of event looks in the feed under the scene.
const TONE = {
  ok:   { chip: 'OK',   color: OK },
  warn: { chip: 'PPE',  color: WARN },
  bad:  { chip: 'FLAG', color: 'var(--accent)' },
  crit: { chip: 'CRIT', color: 'var(--accent)' },
  info: { chip: 'INFO', color: '#6B6670' },
};

function LiveScene() {
  const canvasRef = useRef(null);
  const [feed, setFeed] = useState({ rows: [], checked: 0, flagged: 0 });

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

    const rows = [];
    const stats = { checked: 0, flagged: 0 };
    const say = (id, text, tone) => {
      const now = new Date();
      rows.unshift({ key: `${now.getTime()}-${Math.random()}`, stamp: now.toTimeString().slice(0, 8), id, text, tone });
      rows.length = Math.min(rows.length, 4);
      setFeed({ rows: [...rows], ...stats });
    };

    const workers = [newWorker(0), newWorker(1.2), newWorker(2.8), newWorker(4.4), newWorker(6.5)];
    let last = performance.now();
    let scan = 0;
    let raf = 0;

    // Size of a person at a given depth: smaller near the top (further from the camera).
    const base = () => Math.max(28, Math.min(80, h * 0.19, w * 0.12));
    const depth = (y) => 0.70 + 0.42 * Math.max(0, Math.min(1, y));

    const onZoneChange = (wk, z) => {
      if (z === 'checkpoint' && !wk.checked) {
        wk.checked = true; wk.lock = 1; stats.checked += 1;
        const ok = wk.helmet && wk.vest;
        say(wk.id, wk.hidden ? 'checkpoint · view blocked, not verified'
          : ok ? 'checkpoint · helmet ✓ vest ✓'
            : `checkpoint · ${!wk.helmet ? 'no helmet' : 'no vest'} detected`, wk.hidden ? 'info' : ok ? 'ok' : 'warn');
      } else if (z === 'restricted' && !wk.verdict) {
        wk.lock = 1;
        if (wk.hidden) { wk.verdict = 'UNVERIFIABLE'; say(wk.id, 'restricted · could not verify, sent to review', 'info'); }
        else if (wk.helmet && wk.vest) { wk.verdict = 'COMPLIANT'; say(wk.id, 'restricted · compliant', 'ok'); }
        else {
          wk.verdict = 'FLAGGED'; wk.pulse = 1; stats.flagged += 1;
          say(wk.id, `restricted · ${!wk.helmet ? 'no helmet' : 'no vest'}, flagged`, 'bad');
        }
      }
    };

    const step = (wk, dt, t) => {
      if (wk.wait > 0) { wk.wait -= dt; return; }
      if (wk.fallen) {
        wk.fall += dt;
        if (wk.fall > 1.0 && !wk.alerted) { wk.alerted = true; wk.pulse = 1; stats.flagged += 1; say(wk.id, 'person down, not moving · MAN DOWN', 'crit'); }
        if (wk.fall > 3.6) { wk.fallen = false; wk.alerted = false; wk.fall = 0; say(wk.id, 'back on their feet · alert closed', 'ok'); }
        return;
      }
      if (wk.stop > 0) { wk.stop -= dt; wk.moving = Math.max(0, wk.moving - dt * 4); return; }

      const target = wk.route[wk.leg];
      const px = base();
      // steer towards the next point, with a little drift so nobody walks a ruler-straight line
      const want = Math.atan2((target.y - wk.y) * h, (target.x - wk.x) * w)
        + Math.sin(t * 0.9 + wk.wobble) * 0.28;
      let turn = Math.atan2(Math.sin(want - wk.heading), Math.cos(want - wk.heading));
      const maxTurn = 3.2 * dt;
      turn = Math.max(-maxTurn, Math.min(maxTurn, turn));
      wk.heading += turn;

      const dist = Math.hypot((target.x - wk.x) * w, (target.y - wk.y) * h);
      const sharp = 1 - (Math.abs(turn) / (maxTurn + 1e-6)) * 0.35;                  // slow down in turns
      const arrive = Math.min(1, dist / (px * 0.8));                                  // and near the stop
      const speed = px * 1.15 * wk.pace * depth(wk.y) * sharp * (0.35 + 0.65 * arrive)
        * (0.9 + 0.1 * Math.sin(t * 2.3 + wk.wobble));
      wk.moving = Math.min(1, wk.moving + dt * 3);

      let vx = Math.cos(wk.heading) * speed, vy = Math.sin(wk.heading) * speed;
      // keep a little personal space
      for (const o of workers) {
        if (o === wk || o.wait > 0) continue;
        const dx = (wk.x - o.x) * w, dy = (wk.y - o.y) * h, d = Math.hypot(dx, dy);
        if (d > 0 && d < px * 0.8) { vx += (dx / d) * px * 0.9; vy += (dy / d) * px * 0.5; }
      }
      wk.x += (vx * dt) / Math.max(w, 1);
      wk.y += (vy * dt) / Math.max(h, 1);
      if (wk.x > 0.02 && wk.x < 0.98) wk.y = Math.max(0.34, wk.y);       // never walk up into the top readouts
      wk.stride += (speed * dt) / (px * 0.22);

      if (dist < px * 0.12) {
        if (target.out) { Object.assign(wk, newWorker(rand(0.4, 2.2))); return; }
        wk.stop = target.stop; wk.leg += 1;
      }

      const z = zoneAt(wk.x, wk.y);
      if (z !== wk.zone) { wk.zone = z; if (z) onZoneChange(wk, z); }

      // the odd fall, once per person, inside a zone
      if (!wk.fellOnce && wk.zone && wk.leg > 0 && Math.random() < dt * 0.035) {
        wk.fellOnce = true; wk.fallen = true; wk.fall = 0; wk.alerted = false;
      }

      if (wk.trail.length === 0 || Math.hypot((wk.x - wk.trail[0].x) * w, (wk.y - wk.trail[0].y) * h) > 6) {
        wk.trail.unshift({ x: wk.x, y: wk.y });
        if (wk.trail.length > 26) wk.trail.pop();
      }
    };

    const figure = (wk, x, y, s) => {
      const lying = wk.fallen ? Math.min(1, wk.fall / 0.35) : 0;
      const sw = Math.sin(wk.stride) * 0.6 * wk.moving;
      const bob = Math.abs(Math.cos(wk.stride)) * s * 0.025 * wk.moving;
      const lean = Math.cos(wk.heading) * 0.10 * wk.moving;
      const idle = Math.sin(performance.now() / 700 + wk.wobble) * s * 0.01 * (1 - wk.moving);
      const side = Math.cos(wk.heading) >= 0 ? 1 : -1;

      // shadow on the floor
      ctx.fillStyle = ink(0.10);
      ctx.beginPath();
      ctx.ellipse(x + lying * s * 0.45 * side, y + 1, s * (0.22 + lying * 0.35), s * 0.06, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.save();
      ctx.translate(x, y - bob);
      ctx.rotate(lying * (Math.PI / 2) * 0.98 * side + lean * (1 - lying));
      ctx.globalAlpha = wk.hidden ? 0.42 : 1;
      const j = {
        head: [0, -s * 0.86 + idle], neck: [0, -s * 0.70 + idle], hip: [0, -s * 0.34],
        lk: [Math.sin(sw) * s * 0.16, -s * 0.17], rk: [-Math.sin(sw) * s * 0.16, -s * 0.17],
        lf: [Math.sin(sw) * s * 0.30, 0], rf: [-Math.sin(sw) * s * 0.30, 0],
        le: [-s * 0.10 - Math.sin(sw) * s * 0.08, -s * 0.54], re: [s * 0.10 + Math.sin(sw) * s * 0.08, -s * 0.54],
        lh: [-s * 0.08 - Math.sin(sw) * s * 0.20, -s * 0.38], rh: [s * 0.08 + Math.sin(sw) * s * 0.20, -s * 0.38],
      };
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.strokeStyle = ink(0.85);
      ctx.lineWidth = Math.max(1.6, s * 0.06);
      const line = (...p) => { ctx.beginPath(); ctx.moveTo(p[0][0], p[0][1]); for (const q of p.slice(1)) ctx.lineTo(q[0], q[1]); ctx.stroke(); };
      line(j.neck, j.hip); line(j.hip, j.lk, j.lf); line(j.hip, j.rk, j.rf);
      line(j.neck, j.le, j.lh); line(j.neck, j.re, j.rh);
      if (wk.vest) {                                          // hi-vis vest over the torso, with a reflective band
        ctx.strokeStyle = VEST; ctx.lineWidth = Math.max(4, s * 0.18);
        line([0, -s * 0.66 + idle], [0, -s * 0.40]);
        ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = Math.max(1, s * 0.03);
        line([-s * 0.08, -s * 0.50], [s * 0.08, -s * 0.50]);
      }
      // head, blurred: soft discs instead of a face
      ctx.fillStyle = ink(0.32);
      ctx.beginPath(); ctx.arc(j.head[0], j.head[1], s * 0.14, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = ink(0.14);
      ctx.beginPath(); ctx.arc(j.head[0], j.head[1], s * 0.20, 0, Math.PI * 2); ctx.fill();
      if (wk.helmet) {                                        // hard hat
        ctx.fillStyle = HELMET;
        ctx.beginPath(); ctx.arc(j.head[0], j.head[1] - s * 0.02, s * 0.16, Math.PI, 0); ctx.fill();
        ctx.fillRect(j.head[0] - s * 0.21, j.head[1] - s * 0.03, s * 0.42, s * 0.035);
      }
      // pose keypoints, the way the pose model sees a person
      ctx.fillStyle = accent;
      for (const k of ['neck', 'hip', 'lk', 'rk', 'lf', 'rf', 'le', 're', 'lh', 'rh']) {
        ctx.beginPath(); ctx.arc(j[k][0], j[k][1], Math.max(1.3, s * 0.032), 0, Math.PI * 2); ctx.fill();
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

    // A small rounded chip with text, used for labels and the on-screen readouts. Returns its left edge and width.
    const chip = (text, x, y, bg, fgc, align = 'left') => {
      ctx.font = `700 10px ${MONO}`;
      const tw = ctx.measureText(text).width + 12;
      const lx = align === 'right' ? x - tw : align === 'center' ? x - tw / 2 : x;
      const cx = Math.max(4, Math.min(w - tw - 4, lx));
      ctx.fillStyle = bg;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(cx, y, tw, 17, 4); else ctx.rect(cx, y, tw, 17);
      ctx.fill();
      ctx.fillStyle = fgc;
      ctx.fillText(text, cx + 6, y + 12);
      return { left: cx, width: tw };
    };

    const draw = (now) => {
      const dt = reduced ? 0 : Math.min(0.05, (now - last) / 1000);
      const t = now / 1000;
      last = now;
      ctx.clearRect(0, 0, w, h);
      const s0 = base();

      // floor grid; the horizontal lines spread out towards the camera for a sense of depth
      ctx.strokeStyle = ink(0.06); ctx.lineWidth = 1;
      for (let gx = 0; gx < w; gx += 28) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke(); }
      for (let i = 0, gy = 0; gy < h; i += 1, gy += 16 + i * 1.6) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke(); }

      // zones, brighter while someone is inside
      ZONES.forEach((z) => {
        const x = z.x * w, y = z.y * h, zw = z.w * w, zh = z.h * h;
        const hot = z.id === 'restricted';
        const busy = workers.some((wk) => wk.wait <= 0 && wk.zone === z.id);
        ctx.fillStyle = hot ? `rgba(220,38,38,${busy ? 0.11 : 0.06})` : ink(busy ? 0.06 : 0.03);
        ctx.fillRect(x, y, zw, zh);
        ctx.strokeStyle = hot ? `rgba(220,38,38,${busy ? 0.8 : 0.5})` : ink(busy ? 0.5 : 0.32);
        ctx.setLineDash(z.id === 'exit' ? [5, 4] : []);
        ctx.lineWidth = busy ? 1.6 : 1.2;
        ctx.strokeRect(x, y, zw, zh);
        ctx.setLineDash([]);
        if (hot) {                                             // hazard stripes along the top edge
          ctx.save(); ctx.beginPath(); ctx.rect(x, y, zw, 5); ctx.clip();
          ctx.fillStyle = 'rgba(220,38,38,0.4)';
          for (let sx = x - 10; sx < x + zw; sx += 10) {
            ctx.beginPath(); ctx.moveTo(sx, y + 5); ctx.lineTo(sx + 5, y); ctx.lineTo(sx + 9, y); ctx.lineTo(sx + 4, y + 5); ctx.fill();
          }
          ctx.restore();
        }
        ctx.fillStyle = hot ? accent : ink(0.75);
        ctx.font = `700 10px ${MONO}`;
        ctx.fillText(z.label, x + 8, y + 18);
      });

      // scan line sweeping across the frame
      scan = (scan + dt * 0.38) % 1.3;
      if (scan <= 1) {
        const sx = scan * w;
        const g = ctx.createLinearGradient(sx - 50, 0, sx, 0);
        g.addColorStop(0, 'rgba(220,38,38,0)');
        g.addColorStop(1, 'rgba(220,38,38,0.16)');
        ctx.fillStyle = g; ctx.fillRect(sx - 50, 0, 50, h);
        ctx.strokeStyle = 'rgba(220,38,38,0.6)'; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, h); ctx.stroke();
      }

      workers.forEach((wk) => step(wk, dt, t));
      const placed = workers.filter((wk) => wk.wait <= 0).sort((a, b) => a.y - b.y);

      // tracking trails first, under everyone
      for (const wk of placed) {
        for (let i = 1; i < wk.trail.length; i += 2) {
          const p = wk.trail[i];
          const a = 1 - i / wk.trail.length;
          ctx.fillStyle = wk.verdict === 'FLAGGED' ? `rgba(220,38,38,${0.4 * a})` : ink(0.28 * a);
          ctx.beginPath(); ctx.arc(p.x * w, p.y * h, 1.4, 0, Math.PI * 2); ctx.fill();
        }
      }

      let alerts = 0;
      for (const wk of placed) {
        const x = wk.x * w, y = wk.y * h;
        if (x < -s0 || x > w + s0 || y > h + s0) continue;
        const s = s0 * depth(wk.y);
        if (scan <= 1 && Math.abs(scan * w - x) < 6) wk.lock = Math.max(wk.lock, 0.6);
        figure(wk, x, y, s);

        const down = wk.fallen && wk.alerted;
        const bad = wk.verdict === 'FLAGGED' || down;
        if (bad) alerts += 1;
        const side = Math.cos(wk.heading) >= 0 ? 1 : -1;
        const bw = wk.fallen ? s * 1.05 : s * 0.62, bh = wk.fallen ? s * 0.5 : s * 1.12;
        const color = bad ? accent : wk.verdict === 'UNVERIFIABLE' ? ink(0.5) : wk.verdict === 'COMPLIANT' ? OK : ink(0.7);
        const grow = 1 + wk.lock * 0.25;
        const bx = x + (wk.fallen ? s * 0.45 * side : 0);
        bracket(bx, y + 2, bw * grow, bh * grow, color, wk.verdict === 'UNVERIFIABLE');
        wk.lock = Math.max(0, wk.lock - dt * 2.2);

        const tag = down ? 'MAN DOWN'
          : wk.verdict === 'COMPLIANT' ? 'OK' : wk.verdict === 'FLAGGED' ? (!wk.helmet ? 'NO HELMET' : 'NO VEST')
            : wk.verdict === 'UNVERIFIABLE' ? 'UNVERIFIED' : wk.checked ? 'CHECKED' : 'TRACKING';
        const conf = (bad || wk.verdict === 'COMPLIANT') ? ` ${(0.86 + ((wk.id * 7) % 13) / 100).toFixed(2)}` : '';
        const bg = bad ? accent : wk.verdict === 'COMPLIANT' ? OK : ink(0.85);
        chip(`#${wk.id} ${tag}${conf}`, bx, y - bh * grow - 21, bg,
          light || bad || wk.verdict === 'COMPLIANT' ? '#FFFFFF' : '#0B0A0C', 'center');

        if (bad && wk.pulse > 0) {
          const r = (1 - wk.pulse) * s * 1.6 + s * 0.3;
          ctx.strokeStyle = `rgba(220,38,38,${wk.pulse * 0.8})`; ctx.lineWidth = 2.5;
          ctx.beginPath(); ctx.arc(bx, y - (wk.fallen ? s * 0.1 : s * 0.45), r, 0, Math.PI * 2); ctx.stroke();
          wk.pulse -= dt * 0.9;
          if (wk.pulse <= 0) wk.pulse = 1;
        }
      }

      // on-screen readouts: viewfinder corners, camera name, REC, date and time, live counts
      const m = 8, cl = 16;
      ctx.strokeStyle = ink(0.8); ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(m, m + cl); ctx.lineTo(m, m); ctx.lineTo(m + cl, m);
      ctx.moveTo(w - m - cl, m); ctx.lineTo(w - m, m); ctx.lineTo(w - m, m + cl);
      ctx.moveTo(w - m, h - m - cl); ctx.lineTo(w - m, h - m); ctx.lineTo(w - m - cl, h - m);
      ctx.moveTo(m + cl, h - m); ctx.lineTo(m, h - m); ctx.lineTo(m, h - m - cl);
      ctx.stroke();

      const panel = light ? 'rgba(255,255,255,0.88)' : 'rgba(11,10,12,0.78)';
      const text = light ? '#0B0A0C' : '#F7F6F8';
      chip('CAM 01 · NORTH GATE', m + 10, m + 6, panel, text);
      const rec = chip('    REC', w - m - 10, m + 6, panel, text, 'right');
      if (Math.floor(now / 600) % 2 === 0) {
        ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(rec.left + 11, m + 14.5, 3.5, 0, Math.PI * 2); ctx.fill();
      }
      const d = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}  ${d.toTimeString().slice(0, 8)}.${Math.floor(d.getMilliseconds() / 100)}`;
      chip(stamp, m + 10, h - m - 23, panel, text);
      const persons = placed.filter((wk) => wk.x > 0 && wk.x < 1 && wk.y < 1).length;
      chip(`PERSONS ${persons} · ALERTS ${alerts}`, w - m - 10, h - m - 23,
        alerts ? accent : panel, alerts ? '#FFFFFF' : text, 'right');

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); themeWatch.disconnect(); };
  }, []);

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <canvas
        ref={canvasRef}
        className="w-full block flex-1 min-h-[240px] rounded-xl border border-white/10"
        role="img"
        aria-label="Animated camera view: workers walk around a site through a PPE checkpoint, a restricted zone and an exit; each is checked for a helmet and vest and labelled compliant, flagged or could not verify, and a fall raises a man-down alert."
      />
      <EventFeed feed={feed} />
    </div>
  );
}

/* ── Event feed ──────────────────────────────────────────────────────────── */

function EventFeed({ feed }) {
  const slots = [...feed.rows, ...Array(Math.max(0, 4 - feed.rows.length)).fill(null)];
  return (
    <div
      className="shrink-0 rounded-xl border overflow-hidden"
      style={{ borderColor: 'var(--line-soft)', background: 'var(--surface)' }}
      aria-hidden="true"
    >
      <div className="flex items-center justify-between px-3.5 py-2 border-b" style={{ borderColor: 'var(--line-soft)' }}>
        <span className="flex items-center gap-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.16em]">
          <span className="relative flex w-2 h-2">
            <span className="absolute inset-0 rounded-full bg-accent opacity-60 animate-ping" />
            <span className="relative w-2 h-2 rounded-full bg-accent" />
          </span>
          Live events
        </span>
        <span className="font-mono text-[0.625rem] text-ink-muted tabular-nums">
          {feed.checked} checked · <span className="text-accent font-bold">{feed.flagged} flagged</span>
        </span>
      </div>
      <ol>
        {slots.map((e, i) => (
          <li
            key={e ? e.key : `empty-${i}`}
            className={`grid grid-cols-[2.75rem_2.25rem_1fr_auto] items-center gap-2.5 px-3.5 h-8 font-mono text-[0.6875rem] ${i ? 'border-t' : ''} ${e && i === 0 ? 'auth-log-line' : ''}`}
            style={{ borderColor: 'var(--line-soft)', opacity: e ? 1 - i * 0.17 : 1 }}
          >
            {e ? (
              <>
                <span
                  className="text-center rounded px-1 py-[1px] text-[0.5625rem] font-bold tracking-wider text-white"
                  style={{ background: TONE[e.tone].color }}
                >
                  {TONE[e.tone].chip}
                </span>
                <span className="font-bold tabular-nums">#{e.id}</span>
                <span className="truncate text-ink">{e.text}</span>
                <span className="text-ink-faint tabular-nums">{e.stamp}</span>
              </>
            ) : (
              <span className="col-span-4 h-1.5 w-2/3 rounded-full skeleton" />
            )}
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
