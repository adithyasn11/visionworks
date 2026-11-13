'use client';

// frontend/app/components/AuthAside.jsx
// Left-hand panel for the sign-in / sign-up screens.
//
// The visual is a top-down site plan: tracked figures walk from a PPE
// checkpoint into a restricted zone and out through an exit, and each carries
// the verdict the real pipeline would give it (track id -> zone -> verdict).
// The counters below it are driven by the simulation, so what you read always
// matches what moved. It is an illustration, not live data.
import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

/* ── Simulation ──────────────────────────────────────────────────────────── */

const ZONES = [
  { id: 'checkpoint', label: 'PPE checkpoint', x: 0.06, y: 0.10, w: 0.40, h: 0.46 },
  { id: 'restricted', label: 'Restricted',     x: 0.54, y: 0.10, w: 0.40, h: 0.46 },
  { id: 'exit',       label: 'Exit',           x: 0.06, y: 0.68, w: 0.88, h: 0.22 },
];

// Waypoints inside each zone: where a figure pauses before its next leg.
const SEATS = [
  { x: 0.16, y: 0.30, zone: 0 }, { x: 0.34, y: 0.36, zone: 0 },
  { x: 0.64, y: 0.30, zone: 1 }, { x: 0.84, y: 0.36, zone: 1 },
  { x: 0.20, y: 0.79, zone: 2 }, { x: 0.50, y: 0.79, zone: 2 }, { x: 0.80, y: 0.79, zone: 2 },
];

// A figure keeps one verdict for the whole leg: compliant, flagged for a
// reviewer, or unverifiable (the system declines to guess).
const VERDICTS = ['COMPLIANT', 'FLAGGED', 'UNVERIFIABLE'];

function pickVerdict() {
  const r = Math.random();
  return r < 0.6 ? 0 : r < 0.8 ? 1 : 2;
}

function makeAgents() {
  // Fixed ids so the labels read like real track ids from the tracker.
  return [
    { id: 4,  at: 0, to: 4, t: 0.0,  speed: 0.0042, posture: 0, dwell: 0, hold: 40 },
    { id: 7,  at: 2, to: 5, t: 0.35, speed: 0.0035, posture: 0, dwell: 0, hold: 0 },
    { id: 11, at: 5, to: 3, t: 0.6,  speed: 0.0050, posture: 2, dwell: 0, hold: 0 },
    { id: 13, at: 1, to: 6, t: 0.15, speed: 0.0038, posture: 1, dwell: 0, hold: 210 },
    { id: 18, at: 3, to: 0, t: 0.75, speed: 0.0031, posture: 0, dwell: 0, hold: 0 },
    { id: 22, at: 6, to: 2, t: 0.48, speed: 0.0044, posture: 0, dwell: 0, hold: 300 },
  ];
}

function Floorplan() {
  const canvasRef = useRef(null);
  const agentsRef = useRef(makeAgents());
  const rafRef = useRef(0);
  const [counts, setCounts] = useState({ checkpoint: 2, restricted: 1, exit: 1 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let w = 0, h = 0, dpr = 1;
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = r.width; h = r.height;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // Colours follow the theme, read every frame so switching light / dark repaints at once: light lines
    // on the dark panel, dark lines on the light one.
    let fg = '255,255,255';
    let accent = '#F05252';
    let light = false;
    const readTheme = () => {
      const root = document.documentElement;
      light = !root.classList.contains('dark');
      fg = light ? '11,10,12' : '255,255,255';
      accent = getComputedStyle(root).getPropertyValue('--accent').trim() || '#F05252';
    };
    // Faint white reads well on the dark panel; the same faintness in black on the light panel looked grey
    // and washed out, so light mode draws everything much darker.
    const al = (a) => (light ? Math.min(1, a * 2.6 + 0.12) : a);

    let lastCount = 0;

    // Read once, then again only when the theme changes (not every frame: a style read per frame forces the
    // browser to recompute styles 60 times a second, which made the page stutter while it animated in).
    readTheme();
    const themeWatch = new MutationObserver(readTheme);
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    const draw = (time) => {
      ctx.clearRect(0, 0, w, h);

      // Zone rectangles — hairline, no fills competing with the figures.
      ZONES.forEach((z) => {
        const x = z.x * w, y = z.y * h, zw = z.w * w, zh = z.h * h;
        ctx.strokeStyle = `rgba(${fg},${al(0.14)})`;
        ctx.lineWidth = 1;
        ctx.setLineDash(z.id === 'exit' ? [4, 4] : []);
        ctx.strokeRect(x, y, zw, zh);
        ctx.setLineDash([]);

        ctx.fillStyle = `rgba(${fg},${al(0.30)})`;
        ctx.font = '600 10px ui-monospace, SFMono-Regular, Menlo, monospace';
        ctx.fillText(z.label.toUpperCase(), x + 8, y + 17);
      });

      const agents = agentsRef.current;
      const tally = { checkpoint: 0, restricted: 0, exit: 0 };
      const placed = [];

      agents.forEach((a) => {
        const from = SEATS[a.at], to = SEATS[a.to];

        // Hold at the waypoint between legs so the verdict is readable —
        // otherwise every figure is always in motion and the accent colour
        // stops meaning anything.
        if (a.hold > 0) {
          if (!reduced) a.hold -= 1;
        } else if (!reduced) {
          a.t += a.speed;
        }

        if (a.t >= 1) {
          a.t = 0;
          a.at = a.to;
          // Pick a new destination, preferring a different zone so figures
          // actually cross boundaries and the counters change.
          let next = Math.floor(Math.random() * SEATS.length);
          let guard = 0;
          while ((next === a.at || SEATS[next].zone === SEATS[a.at].zone) && guard++ < 12) {
            next = Math.floor(Math.random() * SEATS.length);
          }
          a.to = next;
          a.posture = SEATS[a.at].zone === 2 ? 0 : pickVerdict();
          a.hold = 130 + Math.floor(Math.random() * 190); // ~2-5s at 60fps
          a.dwell = 0;
        }

        // Ease so movement reads as walking, not linear sliding.
        const e = a.t < 0.5 ? 2 * a.t * a.t : 1 - Math.pow(-2 * a.t + 2, 2) / 2;
        const px = (from.x + (to.x - from.x) * e) * w;
        const py = (from.y + (to.y - from.y) * e) * h;

        const zoneIdx = e < 0.5 ? from.zone : to.zone;
        tally[ZONES[zoneIdx].id] += 1;
        a.dwell += 1 / 60;

        const moving = a.hold <= 0 && a.t > 0.02 && a.t < 0.98;
        const verdict = VERDICTS[a.posture];
        // Only a flagged worker takes the accent; unverifiable stays quiet.
        const hot = verdict === 'FLAGGED';

        // Trail behind moving figures.
        if (moving) {
          const tx = (from.x + (to.x - from.x) * Math.max(0, e - 0.08)) * w;
          const ty = (from.y + (to.y - from.y) * Math.max(0, e - 0.08)) * h;
          ctx.strokeStyle = hot ? 'rgba(240,82,82,0.35)' : `rgba(${fg},${al(0.18)})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(px, py); ctx.stroke();
        }

        // Detection box + centroid dot, the way the HUD draws them.
        const box = 22;
        ctx.strokeStyle = hot ? accent : `rgba(${fg},${al(0.55)})`;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(px - box / 2, py - box / 2, box, box);

        ctx.fillStyle = hot ? accent : `rgba(${fg},${al(0.85)})`;
        ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();

        // Track label — pushed down when another label already sits nearby,
        // so overlapping figures stay readable.
        const label = `#${a.id} ${verdict}`;
        ctx.font = '600 9px ui-monospace, SFMono-Regular, Menlo, monospace';
        let ly = py + 3;
        const lx = px + box / 2 + 5;
        for (const p of placed) {
          if (Math.abs(p.x - lx) < 74 && Math.abs(p.y - ly) < 11) ly = p.y + 12;
        }
        placed.push({ x: lx, y: ly });
        ctx.fillStyle = hot ? accent : `rgba(${fg},${al(0.55)})`;
        ctx.fillText(label, lx, ly);
      });

      // Throttle React updates to ~4/sec; 60fps setState would thrash.
      if (time - lastCount > 260) {
        lastCount = time;
        setCounts({ ...tally });
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(rafRef.current); ro.disconnect(); themeWatch.disconnect(); };
  }, []);

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-white/40">
          cam_gate_01 · top-down
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-white/40 flex items-center gap-1.5">
          <span className="w-1 h-1 rounded-full bg-emerald-400" />
          tracking
        </span>
      </div>

      <canvas
        ref={canvasRef}
        className="w-full block flex-1 min-h-[130px] max-h-[250px]"
        role="img"
        aria-label="Animated top-down site plan showing tracked workers moving from a PPE checkpoint into a restricted zone and out through an exit, each labelled compliant, flagged or unverifiable."
      />

      {/* Zone counts read out of the same simulation that drives the canvas. */}
      <div className="grid grid-cols-3 font-mono text-[0.625rem] uppercase tracking-[0.12em] border-t border-white/10 pt-3">
        {ZONES.map((z) => (
          <div key={z.id} className="flex flex-col gap-1">
            <span className="text-white/40">{z.id}</span>
            <span className="text-lg font-sans font-black tracking-tight tabular-nums text-white">
              {counts[z.id]}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Panel ───────────────────────────────────────────────────────────────── */

export const AuthAside = ({ eyebrow, headline, sub, facts }) => (
  <aside className="card-dark relative hidden lg:flex flex-col justify-between gap-[clamp(1rem,3vh,2.5rem)] overflow-hidden h-full min-h-0 auth-pane">
    <div className="relative z-10 shrink-0">
      <Link href="/" className="flex items-center gap-2.5 w-max group">
        <div className="w-7 h-7 bg-accent flex items-center justify-center rounded-lg group-hover:rotate-12 transition-transform duration-300">
          <div className="w-2 h-2 bg-white rounded-sm" />
        </div>
        <span className="font-extrabold text-lg tracking-tight">VisionWorks</span>
      </Link>
    </div>

    <div className="relative z-10 auth-aside-stack flex-1 justify-center min-h-0">
      <div>
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-accent mb-4">
          {eyebrow}
        </p>
        <h2 className="text-2xl xl:text-[1.75rem] font-black tracking-tight leading-[1.2] text-balance max-w-sm">
          {headline}
        </h2>
        <p className="mt-3 text-[0.875rem] leading-relaxed text-white/60 max-w-sm">
          {sub}
        </p>
      </div>

      <Floorplan />
    </div>

    {/* Facts as a definition list, not a fake testimonial. */}
    <dl className="relative z-10 shrink-0 border-t border-white/10 pt-[clamp(0.85rem,2vh,1.5rem)] flex flex-col gap-2">
      {facts.map(({ term, detail }) => (
        <div key={term} className="grid grid-cols-[7.5rem_1fr] gap-3 items-baseline">
          <dt className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-white/40">
            {term}
          </dt>
          <dd className="text-[0.8125rem] text-white/75 leading-snug">{detail}</dd>
        </div>
      ))}
    </dl>
  </aside>
);

export default AuthAside;
