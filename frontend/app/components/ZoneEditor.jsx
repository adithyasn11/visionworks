'use client';

// frontend/app/components/ZoneEditor.jsx
//
// Draw-your-own zone polygons over the camera frame.
//
// This is the interactive half of "Custom Zone Mapping": the user clicks points
// on a frame to outline a workstation, meeting area or corridor, and the shape
// is saved through POST /api/v1/zones. The CV pipeline reads those same rows on
// its next run (see load_zones_for_camera in video_upload.py), so a zone drawn
// here changes how occupancy and dwell time are attributed — it is not a
// display-only annotation.
//
// COORDINATE SPACE
//
// Polygons are stored in FRAME pixel coordinates, the space the CV pipeline
// works in, not in screen pixels. The canvas is displayed responsively, so
// every click is converted from CSS pixels to frame pixels before being stored.
// Without that conversion a zone drawn on a laptop would sit somewhere else
// entirely when the window is resized.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Shapes, Plus, Trash2, Check, X, Undo2, Loader2, AlertCircle, MousePointerClick,
} from 'lucide-react';

const BACKEND_HTTP = 'http://localhost:8001';

/** The frame space the backend pipeline operates in (it downscales to 640 wide). */
const FRAME_W = 640;
const FRAME_H = 360;

const MIN_POINTS = 3;

/* Zone outlines, cycled so adjacent areas stay distinguishable while staying
   inside the red/black/white palette. Separation comes from lightness along one
   red ramp rather than from competing hues. */
const ZONE_COLORS = ['#DC2626', '#F0736F', '#8C1A1A', '#F7B4B0', '#B02525'];

const colorFor = (index) => ZONE_COLORS[index % ZONE_COLORS.length];

/** Draws saved zones plus the polygon currently being drawn. */
function paint(canvas, zones, draft, hoverPoint) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  zones.forEach((zone, index) => {
    const pts = zone.polygon_coordinates;
    if (!pts || pts.length < 3) return;
    const color = colorFor(index);

    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    pts.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.closePath();

    ctx.fillStyle = `${color}22`;
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Label at the polygon's centroid, on a plate so it stays readable over
    // whatever the frame happens to show.
    const cx = pts.reduce((sum, p) => sum + p[0], 0) / pts.length;
    const cy = pts.reduce((sum, p) => sum + p[1], 0) / pts.length;
    const label = zone.zone_name || zone.zone_id;
    ctx.font = '600 12px Inter, sans-serif';
    const width = ctx.measureText(label).width;
    ctx.fillStyle = 'rgba(11,10,12,0.82)';
    ctx.fillRect(cx - width / 2 - 6, cy - 10, width + 12, 20);
    ctx.fillStyle = color;
    ctx.fillText(label, cx - width / 2, cy + 4);
  });

  if (draft.length === 0) return;

  // Draft polygon: solid for committed edges, dashed to the cursor so it is
  // obvious which segment has not been placed yet.
  ctx.beginPath();
  ctx.moveTo(draft[0][0], draft[0][1]);
  draft.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.strokeStyle = '#DC2626';
  ctx.lineWidth = 2;
  ctx.stroke();

  if (hoverPoint) {
    ctx.beginPath();
    ctx.moveTo(draft[draft.length - 1][0], draft[draft.length - 1][1]);
    ctx.lineTo(hoverPoint[0], hoverPoint[1]);
    if (draft.length >= MIN_POINTS) {
      ctx.moveTo(hoverPoint[0], hoverPoint[1]);
      ctx.lineTo(draft[0][0], draft[0][1]);
    }
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = '#DC262699';
    ctx.stroke();
    ctx.setLineDash([]);
  }

  draft.forEach(([x, y], i) => {
    ctx.beginPath();
    ctx.arc(x, y, i === 0 ? 6 : 4, 0, Math.PI * 2);
    ctx.fillStyle = i === 0 ? '#DC2626' : '#F7F6F8';
    ctx.fill();
    ctx.strokeStyle = '#0B0A0C';
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

export const ZoneEditor = ({ cameraId = 'live_webcam', onZonesChanged }) => {
  const canvasRef = useRef(null);

  const [zones, setZones] = useState([]);
  const [status, setStatus] = useState('loading');   // loading | ready | error
  const [error, setError] = useState(null);

  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState([]);
  const [hoverPoint, setHoverPoint] = useState(null);
  const [zoneName, setZoneName] = useState('');
  const [saving, setSaving] = useState(false);

  /* ── data ──────────────────────────────────────────────────────────────── */

  const loadZones = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_HTTP}/api/v1/zones/${cameraId}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Zones API returned ${res.status}`);
      const data = await res.json();
      setZones(Array.isArray(data) ? data : []);
      setStatus('ready');
      setError(null);
      onZonesChanged?.(Array.isArray(data) ? data : []);
    } catch (err) {
      setStatus('error');
      setError(
        /failed to fetch|networkerror|load failed/i.test(String(err?.message))
          ? 'Cannot reach the backend. Start the FastAPI server on port 8001.'
          : String(err?.message || err),
      );
    }
  }, [cameraId, onZonesChanged]);

  useEffect(() => { loadZones(); }, [loadZones]);

  /* ── canvas ────────────────────────────────────────────────────────────── */

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas) paint(canvas, zones, draft, hoverPoint);
  }, [zones, draft, hoverPoint]);

  /**
   * Converts a pointer event to frame-pixel coordinates.
   *
   * The canvas is laid out responsively, so its CSS size rarely matches its
   * backing-store size; scaling by that ratio is what keeps a drawn zone
   * aligned with the frame at any window width.
   */
  const toFramePoint = (event) => {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    return [
      Math.round(Math.max(0, Math.min(canvas.width, x))),
      Math.round(Math.max(0, Math.min(canvas.height, y))),
    ];
  };

  const handleClick = (event) => {
    if (!drawing) return;
    setDraft((points) => [...points, toFramePoint(event)]);
  };

  const handleMove = (event) => {
    if (!drawing || draft.length === 0) return;
    setHoverPoint(toFramePoint(event));
  };

  /* ── actions ───────────────────────────────────────────────────────────── */

  const startDrawing = () => {
    setDrawing(true);
    setDraft([]);
    setHoverPoint(null);
    setZoneName('');
    setError(null);
  };

  const cancelDrawing = () => {
    setDrawing(false);
    setDraft([]);
    setHoverPoint(null);
  };

  const saveZone = async () => {
    if (draft.length < MIN_POINTS) return;

    const name = zoneName.trim() || `Zone ${zones.length + 1}`;
    // A stable, collision-resistant id: the API upserts on zone_id, so reusing
    // one would silently overwrite an existing zone.
    const zoneId = `zone_${Date.now().toString(36)}`;

    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND_HTTP}/api/v1/zones/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          zone_id: zoneId,
          camera_id: cameraId,
          zone_name: name,
          zone_type: 'WORKSTATION',
          polygon_coordinates: draft,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `Save failed (${res.status})`);
      }
      cancelDrawing();
      await loadZones();
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setSaving(false);
    }
  };

  const deleteZone = async (zoneId) => {
    setError(null);
    try {
      const res = await fetch(`${BACKEND_HTTP}/api/v1/zones/${zoneId}`, { method: 'DELETE' });
      if (!res.ok && res.status !== 204) throw new Error(`Delete failed (${res.status})`);
      await loadZones();
    } catch (err) {
      setError(String(err?.message || err));
    }
  };

  const canSave = draft.length >= MIN_POINTS && !saving;

  return (
    <div className="glass-panel p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
          <Shapes className="w-4 h-4 text-accent" />
          <span>Zone Mapping</span>
        </div>

        {!drawing ? (
          <button
            type="button"
            onClick={startDrawing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:brightness-110 text-white text-xs font-bold transition-[filter]"
          >
            <Plus className="w-3.5 h-3.5" />
            Draw zone
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setDraft((p) => p.slice(0, -1))}
              disabled={draft.length === 0}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink-muted text-xs font-semibold transition-colors disabled:opacity-40"
            >
              <Undo2 className="w-3.5 h-3.5" />
              Undo
            </button>
            <button
              type="button"
              onClick={saveZone}
              disabled={!canSave}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:brightness-110 text-white text-xs font-bold transition-[filter] disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
              Save
            </button>
            <button
              type="button"
              onClick={cancelDrawing}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink-muted text-xs font-semibold transition-colors"
            >
              <X className="w-3.5 h-3.5" />
              Cancel
            </button>
          </div>
        )}
      </div>

      {drawing && (
        <div className="flex items-center gap-2.5 flex-wrap">
          <input
            type="text"
            value={zoneName}
            onChange={(e) => setZoneName(e.target.value)}
            placeholder={`Zone ${zones.length + 1}`}
            className="flex-1 min-w-[140px] rounded-lg bg-ground border border-line px-3 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)] transition-colors"
          />
          <span className="text-[11px] text-ink-muted font-mono whitespace-nowrap">
            {draft.length} pt{draft.length === 1 ? '' : 's'}
            {draft.length < MIN_POINTS && ` · need ${MIN_POINTS}`}
          </span>
        </div>
      )}

      <div className="relative w-full rounded-xl bg-ground border border-line overflow-hidden">
        <canvas
          ref={canvasRef}
          width={FRAME_W}
          height={FRAME_H}
          onClick={handleClick}
          onMouseMove={handleMove}
          onMouseLeave={() => setHoverPoint(null)}
          className={`w-full h-auto block ${drawing ? 'cursor-crosshair' : 'cursor-default'}`}
        />

        {status === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center bg-[color:var(--ground)]/80">
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
          </div>
        )}

        {status === 'ready' && zones.length === 0 && !drawing && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center pointer-events-none">
            <MousePointerClick className="w-6 h-6 text-ink-faint" strokeWidth={1.8} />
            <p className="text-xs text-ink-muted max-w-xs leading-relaxed">
              No zones yet. Click <span className="text-ink font-semibold">Draw zone</span>,
              then click points on the frame to outline an area. Occupancy and dwell
              time are attributed to whichever zone a person stands in.
            </p>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 text-[11.5px] text-accent leading-relaxed" role="alert">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {zones.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {zones.map((zone, index) => (
            <li
              key={zone.zone_id}
              className="flex items-center gap-2.5 rounded-lg bg-surface-alt border border-line px-3 py-2"
            >
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: colorFor(index) }}
              />
              <span className="text-xs font-semibold text-ink truncate flex-1">
                {zone.zone_name}
              </span>
              <span className="font-mono text-[10px] text-ink-faint whitespace-nowrap">
                {zone.polygon_coordinates?.length ?? 0} pts
              </span>
              <button
                type="button"
                onClick={() => deleteZone(zone.zone_id)}
                aria-label={`Delete ${zone.zone_name}`}
                className="p-1 rounded text-ink-faint hover:text-accent transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
