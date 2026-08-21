'use client';

// frontend/app/components/FloorplanHeatmap.jsx
//
// Top-down occupancy heatmap, rendered from real homography-projected positions.
//
// The CV pipeline projects each detected person's GROUND point (the bottom edge
// of their bounding box — the only part of them actually on the floor plane)
// onto floorplan coordinates, normalises it to 0..1 and stores it with the rest
// of the telemetry. This component reads those positions back as a density grid
// and paints them. Nothing here is placeholder data: an empty floorplan says it
// is empty rather than showing invented hotspots.
//
// Colours come from the shared theme tokens, so the panel follows the light/dark
// toggle like the rest of the product, and density ramps up the product accent
// red — a hotspot reads as "attention" in the same visual language used
// everywhere else in the app.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import h337 from 'heatmap.js';
import { MapPin, Loader2, AlertCircle, Inbox } from 'lucide-react';

const BACKEND_HTTP = 'http://localhost:8001';

const WINDOW_HOURS = 24;
const REFRESH_INTERVAL_MS = 15000;
const GRID = 24;

/* Density ramp built entirely from the product red.
   A quiet floor is a pale wash; a busy one saturates to full accent. Using one
   hue at increasing strength (rather than a rainbow) keeps the panel in the
   red/black/white palette and still reads correctly in greyscale, because the
   ramp is a lightness ramp as well as a saturation one. */
const GRADIENT = {
  '0.20': 'rgba(220,38,38,0.22)',
  '0.50': 'rgba(220,38,38,0.48)',
  '0.80': 'rgba(220,38,38,0.78)',
  '1.0':  '#DC2626',
};

/** Zone guides drawn under the heat layer, purely as spatial reference. */
const FLOOR_GUIDES = [
  'Workstation Zone A',
  'Workstation Zone B',
  'Collaborative Desk',
  'Break Lounge',
];

function Overlay({ kind, message }) {
  const { Icon, cls } = {
    loading: { Icon: Loader2, cls: 'text-accent animate-spin' },
    error:   { Icon: AlertCircle, cls: 'text-accent' },
    empty:   { Icon: Inbox, cls: 'text-ink-faint' },
  }[kind];

  return (
    <div
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2.5 px-6 text-center bg-[color:var(--ground)]/80 backdrop-blur-[1px]"
      role="status"
      aria-live="polite"
    >
      <Icon className={`w-6 h-6 ${cls}`} strokeWidth={1.8} />
      <p className="text-xs leading-relaxed max-w-xs text-ink-muted">{message}</p>
    </div>
  );
}

export const FloorplanHeatmap = () => {
  const containerRef = useRef(null);
  const heatmapRef = useRef(null);

  const [state, setState] = useState({
    status: 'loading',  // 'loading' | 'ready' | 'error'
    data: null,
    error: null,
  });

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState((s) => ({ ...s, status: 'loading' }));
    try {
      const res = await fetch(
        `${BACKEND_HTTP}/api/v1/analytics/heatmap?hours=${WINDOW_HOURS}&grid=${GRID}`,
        { cache: 'no-store' },
      );
      if (!res.ok) throw new Error(`Heatmap API returned ${res.status}`);
      const data = await res.json();
      setState({ status: 'ready', data, error: null });
    } catch (err) {
      setState((s) => ({
        ...s,
        status: 'error',
        error: /failed to fetch|networkerror|load failed/i.test(String(err?.message))
          ? 'Cannot reach the analytics backend. Make sure the FastAPI server is running on port 8001.'
          : String(err?.message || err),
      }));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load();
    const timer = setInterval(() => {
      if (!cancelled) load({ silent: true });
    }, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  const points = state.data?.points ?? [];
  const hasPoints = points.length > 0;

  // Paint whenever the data changes, and re-paint on resize: heatmap.js works
  // in device pixels, so normalised points have to be re-scaled to the box's
  // current size or the heat drifts away from the floorplan beneath it.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasPoints) return;

    if (!heatmapRef.current) {
      heatmapRef.current = h337.create({
        container,
        radius: 34,
        maxOpacity: 0.72,
        minOpacity: 0.05,
        blur: 0.8,
        gradient: GRADIENT,
      });
    }

    const paint = () => {
      const { width, height } = container.getBoundingClientRect();
      if (!width || !height) return;

      heatmapRef.current.setData({
        max: state.data?.max || 1,
        data: points.map((p) => ({
          x: Math.round(p.x * width),
          y: Math.round(p.y * height),
          value: p.value,
        })),
      });
    };

    paint();

    const observer = new ResizeObserver(paint);
    observer.observe(container);
    return () => observer.disconnect();
  }, [points, hasPoints, state.data?.max]);

  // heatmap.js paints into a canvas it appends to the container and never
  // clears on its own. Without this, points from a previous session linger
  // under an "empty" message.
  useEffect(() => {
    if (!hasPoints && heatmapRef.current) {
      heatmapRef.current.setData({ max: 1, data: [] });
    }
  }, [hasPoints]);

  const overlay =
    state.status === 'loading' ? { kind: 'loading', message: 'Loading floorplan telemetry…' }
    : state.status === 'error' ? { kind: 'error', message: state.error }
    : !hasPoints ? {
        kind: 'empty',
        message: 'No positions recorded yet. Process a video or start the live camera — detected people appear here as floor density.',
      }
    : null;

  const samples = state.data?.total_samples ?? 0;

  return (
    <div className="glass-panel p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
          <MapPin className="w-4 h-4 text-accent" />
          <span>Top-Down Occupancy Heatmap</span>
        </div>
        <span className="text-[11px] text-ink-muted font-mono whitespace-nowrap">
          {samples > 0 ? `${samples.toLocaleString()} samples · ${WINDOW_HOURS}h` : `last ${WINDOW_HOURS}h`}
        </span>
      </div>

      <div className="relative w-full h-72 rounded-xl bg-ground border border-line overflow-hidden">
        {/* Zone guides sit beneath the heat layer as spatial reference. */}
        <div className="absolute inset-0 p-4 grid grid-cols-2 gap-4 pointer-events-none">
          {FLOOR_GUIDES.map((label) => (
            <div
              key={label}
              className="border border-line rounded-lg p-2 text-[10px] text-ink-faint font-mono"
            >
              {label}
            </div>
          ))}
        </div>

        {/* heatmap.js appends its canvas here and positions it absolutely. */}
        <div ref={containerRef} className="absolute inset-0" />

        {overlay && <Overlay kind={overlay.kind} message={overlay.message} />}
      </div>

      {hasPoints && (
        <div className="flex items-center gap-2.5 text-[11px] text-ink-muted">
          <span className="font-mono uppercase tracking-[0.14em] text-[9.5px] text-ink-faint">
            Density
          </span>
          <span
            className="h-1.5 flex-1 rounded-full"
            style={{ background: 'linear-gradient(to right, rgba(220,38,38,0.18), rgba(220,38,38,0.5), #DC2626)' }}
          />
          <span className="font-mono tabular-nums">peak {state.data?.max ?? 0}</span>
        </div>
      )}
    </div>
  );
};
