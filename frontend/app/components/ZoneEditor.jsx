'use client';

// frontend/app/components/ZoneEditor.jsx
//
// Draw-your-own safety zones over the camera frame.
//
// The user clicks points on a frame to outline a PPE checkpoint, a restricted
// area or an exit, gives it a type, and the shape is saved through
// POST /api/v1/zones. The CV pipeline reads those same rows on its next run
// (see load_zones_for_camera in video_upload.py), and the safety rules attach
// to a zone by its type — so a zone drawn here changes what is checked, it is
// not a display-only annotation.
//
// COORDINATE SPACE
//
// Polygons are stored in FRAME pixel coordinates, the space the CV pipeline
// works in, not in screen pixels. The canvas is displayed responsively, so
// every click is converted from CSS pixels to frame pixels before being stored.
// Without that conversion a zone drawn on a laptop would sit somewhere else
// entirely when the window is resized.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { backendFetch } from '../lib/backend';
import { subscribeToFrames, getLatestFrame } from '../lib/liveFrame';
import { ZONE_TYPES, zoneTypeMeta, zoneTypeColor, zoneCanvasLabel } from '../lib/zoneTypes';
import {
  Shapes, Plus, Trash2, Check, X, Undo2, Loader2, AlertCircle, MousePointerClick, Eye,
  RefreshCw, Pencil,
} from 'lucide-react';


/** The frame space the backend pipeline operates in (it downscales to 640 wide). */
const FRAME_W = 640;
const FRAME_H = 360;

const MIN_POINTS = 3;

/* A zone's outline colour comes from its TYPE (see lib/zoneTypes.js), so the
   same zone is the same colour here and over the live feed. */

/** Draws saved zones plus the polygon currently being drawn. */
function paint(canvas, zones, draft, hoverPoint) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  zones.forEach((zone, index) => {
    const pts = zone.polygon_coordinates;
    if (!pts || pts.length < 3) return;
    const color = zoneTypeColor(zone.zone_type);

    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    pts.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.closePath();

    ctx.fillStyle = `${color}22`;
    ctx.fill();
    // A dark under-stroke first, so the pale types (white, pink) still read
    // over a bright wall and the red ones over a dark corner.
    ctx.strokeStyle = 'rgba(11,10,12,0.55)';
    ctx.lineWidth = 4;
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Label at the polygon's centroid, on a plate so it stays readable over
    // whatever the frame happens to show. Type badge first, then the name.
    const cx = pts.reduce((sum, p) => sum + p[0], 0) / pts.length;
    const cy = pts.reduce((sum, p) => sum + p[1], 0) / pts.length;
    const label = zoneCanvasLabel(zone, `Zone ${index + 1}`);
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

/**
 * `readOnly` is LAYER 1 only: it hides the drawing and delete controls from a
 * role that lacks `zones.edit`. It is not a security boundary — the backend
 * refuses the write with a 403 (layer 2) and `zone_insert/update/delete`
 * require manage_org_ids() in Postgres (layer 3, the one that actually holds).
 * A VIEWER still sees the zones, because reading them is permitted.
 */
export const ZoneEditor = ({ cameraId = 'live_webcam', onZonesChanged, readOnly = false }) => {
  const canvasRef = useRef(null);

  const [zones, setZones] = useState([]);
  const [status, setStatus] = useState('loading');   // loading | ready | error
  const [error, setError] = useState(null);

  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState([]);
  const [hoverPoint, setHoverPoint] = useState(null);
  const [zoneName, setZoneName] = useState('');
  const [saving, setSaving] = useState(false);

  // THE PICTURE BEHIND THE CANVAS.
  //
  // Without it this editor asked people to outline areas on a black
  // rectangle. Drawing a polygon you cannot see is not a workflow — it is a
  // guess, and a wrong guess silently moves a restricted area for as long as
  // the zone exists.
  //
  // `snapshot` is an object URL for a JPEG from GET /video/snapshot. It is
  // revoked on replacement so a long editing session does not leak blobs.
  const [snapshot, setSnapshot] = useState(null);
  const [snapSource, setSnapSource] = useState('webcam');
  const [snapVideos, setSnapVideos] = useState([]);
  const [snapState, setSnapState] = useState('idle');  // idle|loading|ready|error
  const [snapError, setSnapError] = useState(null);

  // Inline rename / retype of an existing zone.
  const [editingZone, setEditingZone] = useState(null);
  const [editName, setEditName] = useState('');
  const [editType, setEditType] = useState('GENERAL');

  // The kind of the zone currently being drawn. Defaults to General, which no
  // rule attaches to, so a zone whose type was forgotten can never be treated
  // as a restricted area by accident.
  const [newType, setNewType] = useState('GENERAL');

  /* ── data ──────────────────────────────────────────────────────────────── */

  const loadZones = useCallback(async () => {
    try {
      const res = await backendFetch(`/api/v1/zones/${cameraId}`);
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

  /* ── the camera frame behind the canvas ────────────────────────────────── */

  /** Which videos could be used as a backdrop, if there is no webcam. */
  const loadSnapSources = useCallback(async () => {
    try {
      const res = await backendFetch('/api/v1/video/samples');
      if (!res.ok) return;
      const data = await res.json();
      setSnapVideos(Array.isArray(data?.sample_videos) ? data.sample_videos : []);
    } catch {
      // Not fatal: the webcam option still works, and the picker just has
      // fewer entries.
    }
  }, []);

  const grabSnapshot = useCallback(async (source) => {
    setSnapState('loading');
    setSnapError(null);
    try {
      const res = await backendFetch(
        `/api/v1/video/snapshot?source=${encodeURIComponent(source)}`);
      if (!res.ok) {
        let detail = `Snapshot failed (${res.status})`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch { /* the body was not JSON; the status line will do */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      setSnapshot((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
      setSnapState('ready');
    } catch (err) {
      setSnapState('error');
      setSnapError(
        /failed to fetch|networkerror|load failed/i.test(String(err?.message))
          ? 'Cannot reach the backend. Start the FastAPI server first.'
          : String(err?.message || err),
      );
    }
  }, []);

  // FOLLOW THE LIVE SESSION.
  //
  // When the player on the Live feed page is running, its frames appear here
  // as the backdrop — press "Turn On Camera" there, come to Zones, and you
  // are drawing over what the camera is seeing right now rather than over a
  // still taken at some earlier moment.
  //
  // The subscription supersedes the snapshot while it is producing frames and
  // steps aside when it stops, so a paused session falls back to the picker
  // rather than freezing on its last frame.
  const [liveFrame, setLiveFrame] = useState(null);

  useEffect(() => {
    setLiveFrame(getLatestFrame());
    return subscribeToFrames(setLiveFrame);
  }, []);

  // Is a session running RIGHT NOW? Checked on a timer as well as through the
  // subscription, because `clearFrame()` fires during startup — the player
  // stops any previous stream before opening a new one — and a single null
  // must not be read as "the session ended".
  const [liveActive, setLiveActive] = useState(false);
  useEffect(() => {
    const tick = () => setLiveActive(Boolean(getLatestFrame()));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  // Try the webcam once on mount. Failing is fine and common — most people
  // are drawing zones for an uploaded video, not a live camera — so the
  // failure is reported inline rather than as an error state for the page.
  //
  // THE CLEANUP DOES NOT CLEAR THE SNAPSHOT, and that is the whole point.
  //
  // It used to. Under React StrictMode — on in this project — dev mounts,
  // unmounts and remounts every component, so the cleanup ran and nulled the
  // image the first mount had just fetched, while the second fetch resolved
  // into state that had already been wiped. The picker rendered (its data
  // comes from a different effect) but the frame never appeared, which looked
  // exactly like a failing request and was not one.
  //
  // Object URLs are revoked in grabSnapshot when they are REPLACED, which is
  // where the old one actually becomes garbage. What leaks here is at most one
  // URL per editor instance, released when the tab closes.
  useEffect(() => {
    loadSnapSources();
    // Only grab a still if nothing is already streaming. When the live feed
    // owns the webcam, asking the backend to open it too fails with a 503 —
    // and would be pointless anyway, since the live frames are a better
    // backdrop than a still.
    if (!getLatestFrame()) grabSnapshot('webcam');
  }, [loadSnapSources, grabSnapshot]);

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
    // Two zones with one name would make decisions and alerts ambiguous ("which Walkway?").
    if (zones.some((z) => String(z.zone_name || '').trim().toLowerCase() === name.toLowerCase())) {
      setError(`A zone named "${name}" already exists on this camera. Give this one a different name.`);
      return;
    }
    // A stable, collision-resistant id: the API upserts on zone_id, so reusing
    // one would silently overwrite an existing zone.
    const zoneId = `zone_${Date.now().toString(36)}`;

    setSaving(true);
    setError(null);
    try {
      const res = await backendFetch('/api/v1/zones/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          zone_id: zoneId,
          camera_id: cameraId,
          zone_name: name,
          zone_type: newType,
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

  /**
   * Rename a zone, or change what kind of zone it is.
   *
   * A PATCH rather than delete-and-redraw. Redrawing produces a NEW zone with
   * a new id, which orphans every rule and past record pointing at the old
   * one — so correcting a typo would silently detach them.
   */
  const saveZoneEdit = async (zoneId) => {
    const name = editName.trim();
    if (!name) { setError('A zone name is required.'); return; }
    if (zones.some((z) => z.zone_id !== zoneId && String(z.zone_name || '').trim().toLowerCase() === name.toLowerCase())) {
      setError(`A zone named "${name}" already exists on this camera. Choose a different name.`);
      return;
    }
    try {
      const res = await backendFetch(`/api/v1/zones/${zoneId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zone_name: name, zone_type: editType }),
      });
      if (!res.ok) {
        let detail = `Could not save (${res.status})`;
        try { const b = await res.json(); if (b?.detail) detail = b.detail; } catch {}
        throw new Error(detail);
      }
      setEditingZone(null);
      setError(null);
      await loadZones();
    } catch (err) {
      setError(String(err?.message || err));
    }
  };

  const deleteZone = async (zoneId) => {
    setError(null);
    try {
      const res = await backendFetch(`/api/v1/zones/${zoneId}`, { method: 'DELETE' });
      if (!res.ok && res.status !== 204) throw new Error(`Delete failed (${res.status})`);
      await loadZones();
    } catch (err) {
      setError(String(err?.message || err));
    }
  };

  const canSave = draft.length >= MIN_POINTS && !saving;

  // The live session wins when it is running: a moving picture of the room
  // right now beats a still of it a minute ago, and it is what somebody
  // pressing "Turn On Camera" expects to see here.
  const backdrop = liveFrame || snapshot;
  const backdropIsLive = Boolean(liveFrame) || liveActive;

  return (
    <div className="glass-panel p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
          <Shapes className="w-4 h-4 text-accent" />
          <span>Zone Mapping</span>
        </div>

        {readOnly ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-ink-faint">
            <Eye className="w-3.5 h-3.5" />
            View only
          </span>
        ) : !drawing ? (
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
            aria-label="Zone name"
            maxLength={80}
            className="flex-1 min-w-[140px] rounded-lg bg-ground border border-line px-3 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)] transition-colors"
          />
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            title={ZONE_TYPES.find((t) => t.value === newType)?.hint}
            aria-label="Zone type"
            className="rounded-lg bg-ground border border-line px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:border-[color:var(--accent)] transition-colors"
          >
            {ZONE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <span className="text-[11px] text-ink-muted font-mono whitespace-nowrap">
            {draft.length} pt{draft.length === 1 ? '' : 's'}
            {draft.length < MIN_POINTS && ` · click at least ${MIN_POINTS} points to outline an area`}
          </span>
        </div>
      )}

      {/* WHERE THE PICTURE COMES FROM.
          
          A webcam if one is connected, otherwise any uploaded video. Offered
          as a plain picker rather than guessed at, because "no webcam" is the
          normal case when somebody is setting up zones for footage they have
          already recorded — and silently showing black taught them nothing. */}
      {!readOnly && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-ink-faint">
            backdrop
          </span>
          <select
            value={snapSource}
            disabled={backdropIsLive}
            onChange={(e) => { setSnapSource(e.target.value); grabSnapshot(e.target.value); }}
            className="max-w-full min-w-0 rounded-lg bg-ground border border-line px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:border-[color:var(--accent)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <option value="webcam">Live webcam</option>
            {snapVideos.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => grabSnapshot(snapSource)}
            disabled={snapState === 'loading'}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink-muted text-xs font-semibold transition-colors disabled:opacity-60"
          >
            {snapState === 'loading'
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <RefreshCw className="w-3.5 h-3.5" />}
            Refresh
          </button>
          {snapState === 'error' && (
            <span className="text-[11px] text-accent font-medium">{snapError}</span>
          )}
        </div>
      )}

      <div className="relative w-full rounded-xl bg-ground border border-line overflow-hidden">
        {/* The frame sits UNDER the canvas, at the same aspect ratio, so a
            click at (x, y) on screen lands on the same pixel in both. */}
        {backdrop && (
          <img
            src={backdrop}
            alt=""
            aria-hidden="true"
            className="absolute inset-0 w-full h-full object-cover select-none pointer-events-none"
            draggable={false}
          />
        )}
        <canvas
          ref={canvasRef}
          width={FRAME_W}
          height={FRAME_H}
          onClick={handleClick}
          onMouseMove={handleMove}
          onMouseLeave={() => setHoverPoint(null)}
          className={`relative w-full h-auto block ${drawing ? 'cursor-crosshair' : 'cursor-default'}`}
        />

        {status === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center bg-[color:var(--ground)]/80">
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
          </div>
        )}

        {/* Two different empty states, because they need two different
            actions. "No picture" is a setup problem the person must fix
            before drawing is even possible; "no zones" is an invitation to
            start. Showing the second one over a black rectangle — which is
            what this did — tells somebody to click points on a frame they
            cannot see. */}
        {status === 'ready' && !backdrop && snapState !== 'loading' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center">
            <AlertCircle className="w-6 h-6 text-ink-faint" strokeWidth={1.8} />
            <p className="text-xs text-ink-muted max-w-sm leading-relaxed">
              <span className="text-ink font-semibold">No camera image.</span>{' '}
              Zones are drawn onto the actual view, so there is nothing to aim
              at yet. Connect a webcam, or pick an uploaded video from the
              <span className="text-ink font-semibold"> backdrop</span> menu
              above.
            </p>
          </div>
        )}

        {status === 'ready' && backdrop && zones.length === 0 && !drawing && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center pointer-events-none">
            {/* Opaque, not translucent. Over a bright window or a patterned
                curtain, 85% transparency left the text unreadable — and an
                instruction you cannot read is the same as no instruction. */}
            <div className="rounded-xl border border-line bg-surface px-4 py-3 shadow-lg max-w-[19rem]">
              <MousePointerClick className="w-6 h-6 text-accent mx-auto mb-1.5" strokeWidth={1.8} />
              <p className="text-[12px] text-ink-muted leading-relaxed">
                <span className="text-ink font-bold">Click “Draw zone”</span>, then
                click each corner of the area, then press Save to close the
                shape.
              </p>
            </div>
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
          {zones.map((zone, index) => {
            const isEditing = editingZone === zone.zone_id;
            return (
            <li
              key={zone.zone_id}
              className="flex items-center gap-2.5 rounded-lg bg-surface-alt border border-line px-3 py-2"
            >
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0 border border-[color:var(--field)]"
                style={{ backgroundColor: zoneTypeColor(zone.zone_type) }}
              />

              {isEditing ? (
                /* Inline, not a modal. Renaming a zone is a two-second
                   correction and should not take over the screen. */
                <>
                  <input
                    autoFocus
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveZoneEdit(zone.zone_id);
                      if (e.key === 'Escape') setEditingZone(null);
                    }}
                    maxLength={80}
                    className="flex-1 min-w-0 rounded bg-ground border border-field px-2 py-1 text-xs text-ink focus:outline-none focus:border-[color:var(--accent)]"
                  />
                  <select
                    value={editType}
                    onChange={(e) => setEditType(e.target.value)}
                    title={zoneTypeMeta(editType).hint}
                    className="rounded bg-ground border border-field px-1.5 py-1 text-[11px] text-ink focus:outline-none focus:border-[color:var(--accent)]"
                  >
                    {ZONE_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => saveZoneEdit(zone.zone_id)}
                    aria-label="Save"
                    className="p-1 rounded text-accent hover:brightness-125 transition-[filter]"
                  >
                    <Check className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingZone(null)}
                    aria-label="Cancel"
                    className="p-1 rounded text-ink-faint hover:text-ink transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </>
              ) : (
                <>
                  <span className="text-xs font-semibold text-ink truncate flex-1">
                    {zone.zone_name}
                  </span>
                  {/* The kind is shown, not hidden in an edit form. A zone
                      drawn as the wrong type has the wrong rules attached,
                      with nothing on screen explaining why. */}
                  <span
                    title={zoneTypeMeta(zone.zone_type).hint}
                    className={`font-mono text-[9.5px] uppercase tracking-[0.1em] px-1.5 py-0.5 rounded whitespace-nowrap ${
                      zoneTypeMeta(zone.zone_type).value === 'RESTRICTED'
                        ? 'bg-accent-soft text-accent'
                        : 'bg-ground text-ink-faint'
                    }`}
                  >
                    {zoneTypeMeta(zone.zone_type).badge}
                  </span>
                  <span className="font-mono text-[10px] text-ink-faint whitespace-nowrap">
                    {zone.polygon_coordinates?.length ?? 0} pts
                  </span>
                  {!readOnly && (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingZone(zone.zone_id);
                          setEditName(zone.zone_name || '');
                          setEditType(zoneTypeMeta(zone.zone_type).value);
                        }}
                        aria-label={`Rename ${zone.zone_name}`}
                        className="p-1 rounded text-ink-faint hover:text-ink transition-colors"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteZone(zone.zone_id)}
                        aria-label={`Delete ${zone.zone_name}`}
                        className="p-1 rounded text-ink-faint hover:text-accent transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                </>
              )}
            </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};
