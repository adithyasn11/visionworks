'use client';

// frontend/app/components/PhoneCameraPanel.jsx
//
// Pair a phone once, then use it as a camera with one click.
//
// THE SHAPE OF THIS
//
//   1. Laptop asks the backend for a pairing code and renders it as a QR.
//   2. Phone scans it, opens /camera/<code>, grants camera permission ONCE.
//   3. Phone waits, connected to the pairing room.
//   4. Laptop clicks "Turn on phone camera" -> "start" goes into the room.
//   5. Phone opens its camera and streams JPEG frames back.
//   6. This component forwards each frame to /process_webcam_frame, which is
//      the same endpoint the laptop's own webcam uses, and paints what comes
//      back.
//
// Step 6 is why this works at all: the AI pipeline does not need to know or
// care that the pixels came from a phone. It receives base64 JPEGs exactly as
// it does from the laptop camera, runs YOLO and the zone logic, and returns an
// annotated frame. No new pipeline, no second code path to keep correct.
//
// WHY THE FRAMES GO THROUGH THE LAPTOP
//
// The phone could stream straight to the backend. Routing through the laptop
// costs one hop on a LAN — nothing — and buys two things worth more: the
// laptop is the thing holding the signed-in session, so the AI socket carries
// a real token rather than the phone needing one; and the laptop sees every
// frame, which is what lets it show a preview and a frame counter without a
// second subscription.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Smartphone, QrCode, Loader2, CheckCircle2, AlertCircle, X,
  Play, Square, RefreshCw, Wifi, Maximize2, Minimize2,
} from 'lucide-react';

import { backendFetch, backendSocketUrl, BACKEND_HTTP } from '../lib/backend';
import { publishFrame, clearFrame } from '../lib/liveFrame';
import { drawHUD } from '../lib/overlay';

/** How often to re-check whether the phone has joined, while showing the QR. */
const POLL_MS = 1500;

function wsBase() {
  return BACKEND_HTTP.replace(/^http/, 'ws');
}

/**
 * The address the PHONE must open — not the one this laptop is using.
 *
 * `lanHost` comes from the BACKEND, which is the only party that knows this
 * machine's address on the network. The browser only knows the address it was
 * itself opened at, and that is normally localhost — which, scanned by a
 * phone, means the phone. The QR then points at nothing and the scan appears
 * to work right up until the page fails to load.
 *
 * An earlier version built the URL from `window.location.hostname` and merely
 * WARNED when that was loopback. A warning is not a fix: it tells somebody
 * they have a problem and leaves them to solve it. Using the address the
 * server reports means the QR is correct however the dashboard was opened.
 *
 * The protocol still follows the page, because that is genuinely a property
 * of how the servers were started, not of the network.
 */
function phoneUrlFor(code, lanHost) {
  if (typeof window === 'undefined') return '';
  const host = lanHost || window.location.hostname;
  const port = window.location.port ? `:${window.location.port}` : '';
  return `${window.location.protocol}//${host}${port}/camera/${code}`;
}

export default function PhoneCameraPanel({ cameraId, onFrame, disabled = false, activeZones = [] }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(null);
  const [lanHost, setLanHost] = useState(null);
  const [qrSvg, setQrSvg] = useState(null);
  const [paired, setPaired] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [frames, setFrames] = useState(0);
  // Frames the pipeline actually returned. Shown next to the received count
  // because the two diverging is the signature of frames being dropped, and
  // that failure is otherwise completely invisible.
  const [analysed, setAnalysed] = useState(0);
  const [busy, setBusy] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const roomRef = useRef(null);     // socket to the pairing room
  const aiRef = useRef(null);       // socket to the AI pipeline
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const pollRef = useRef(null);
  const pendingRef = useRef(false);  // a frame is with the GPU right now
  const stageRef = useRef(null);     // the preview, for fullscreen

  const host = typeof window !== 'undefined' ? window.location.hostname : '';
  const isLoopback = ['localhost', '127.0.0.1', '::1'].includes(host);

  // Only one problem is left to warn about. The loopback case — a QR pointing
  // the phone at its own localhost — used to be the other, and is now fixed
  // rather than announced: the address comes from the server.
  const insecure = typeof window !== 'undefined'
    && window.location.protocol !== 'https:'
    && !isLoopback;

  const toggleFullScreen = useCallback(() => {
    const el = stageRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else el.requestFullscreen?.().catch(() => {});
  }, []);

  // Escape and F11 leave fullscreen without touching the button, so the flag
  // follows the DOM rather than the click.
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  /* ── teardown ───────────────────────────────────────────────────────── */

  const teardown = useCallback((keepRoom = false) => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (aiRef.current) {
      try { aiRef.current.close(); } catch { /* already gone */ }
      aiRef.current = null;
    }
    if (!keepRoom && roomRef.current) {
      try { roomRef.current.close(); } catch { /* already gone */ }
      roomRef.current = null;
    }
    pendingRef.current = false;
    setStreaming(false);
    setFrames(0);
    setAnalysed(0);
    clearFrame();
  }, []);

  useEffect(() => () => teardown(), [teardown]);

  /* ── the AI pipeline socket ─────────────────────────────────────────── */

  const openAiSocket = useCallback(async () => {
    if (aiRef.current?.readyState === WebSocket.OPEN) return aiRef.current;
    const url = await backendSocketUrl(
      `/api/v1/video/process_webcam_frame?camera_id=${encodeURIComponent(cameraId)}`);
    const ws = new WebSocket(url);

    ws.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.error) { setError(data.error); return; }
      if (!data.frame_base64) {
        // INIT and other non-frame messages still mean the pipeline is alive.
        return;
      }

      // A frame came back, so the pipeline has finished with the last one and
      // is ready for the next. See the comment on pendingRef.
      pendingRef.current = false;
      setAnalysed((n) => n + 1);

      // Paint the frame AND the detection overlay on the panel's preview.
      //
      // The backend sends a clean jpeg plus `tracked_entities`; it does not
      // burn boxes into the pixels. Drawing only the image — which is what
      // this did — gives a live feed with no boxes on it, indistinguishable
      // from the AI not running at all. Same drawHUD the laptop path uses.
      const canvas = canvasRef.current;
      if (canvas) {
        if (!imgRef.current) imgRef.current = new window.Image();
        const img = imgRef.current;
        const entities = data.tracked_entities || [];
        img.onload = () => {
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0);
          drawHUD(ctx, canvas, entities, activeZones, data.privacy_blur);
        };
        img.src = data.frame_base64;
      }

      // The RAW frame, so the zone editor gets a clean backdrop to draw over
      // — the same contract the laptop camera publishes under.
      publishFrame(data.frame_base64);

      // And hand the frame to the parent player, which paints it on the big
      // canvas and updates the posture counts. Without this the phone feed
      // only ever appeared in this small preview.
      if (onFrame) onFrame(data);
    };

    ws.onclose = (event) => {
      aiRef.current = null;
      pendingRef.current = false;
      // 1008 is the policy-violation code the endpoint uses when the session
      // lacks analysis.run. Without this the socket just vanishes and every
      // frame is dropped in silence — the phone streams happily and nothing
      // is ever detected, which is indistinguishable from a broken model.
      if (event.code === 1008) {
        setError(event.reason
          || 'Your account is not allowed to run analysis, so the phone’s frames cannot be processed.');
      }
    };

    aiRef.current = ws;

    // WAIT FOR THE HANDSHAKE BEFORE ANY FRAME ARRIVES
    //
    // `new WebSocket()` returns instantly in CONNECTING. Telling the phone to
    // start at that moment means the first frames land while readyState is
    // still CONNECTING, and the forwarder drops them. Whether ANY frame ever
    // got through was a race against the handshake — which is why the feed
    // looked live while nothing was ever analysed.
    await new Promise((resolve, reject) => {
      if (ws.readyState === WebSocket.OPEN) return resolve();
      const timer = setTimeout(
        () => reject(new Error('the analysis pipeline did not accept the connection')),
        10000);
      ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      ws.addEventListener('error', () => {
        clearTimeout(timer);
        reject(new Error('could not connect to the analysis pipeline'));
      }, { once: true });
    });

    return ws;
  }, [cameraId, onFrame, activeZones]);

  /* ── the pairing room ───────────────────────────────────────────────── */

  const joinRoom = useCallback(async (roomCode) => {
    const url = await backendSocketUrl(
      `/api/v1/pair/ws/${encodeURIComponent(roomCode)}?role=host`);
    const ws = new WebSocket(url);
    roomRef.current = ws;

    ws.onmessage = async (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      switch (msg.type) {
        case 'peer-joined':
          setPaired(true);
          setError(null);
          break;

        case 'frame': {
          // A frame from the phone. Forward it to the AI pipeline in the shape
          // that endpoint already expects from the laptop camera.
          setFrames((n) => n + 1);
          const ai = aiRef.current;
          if (ai?.readyState !== WebSocket.OPEN) break;

          // ONE FRAME IN FLIGHT AT A TIME
          //
          // The phone streams on its own clock; the GPU takes as long as it
          // takes. Without this the socket queues every frame the phone ever
          // sent, and the overlay drifts further behind real time the longer
          // it runs — the same reason the laptop path pipelines on ACK.
          // Dropping the newest frame is right: a stale frame is worthless.
          if (pendingRef.current) break;
          pendingRef.current = true;

          // `image_base64` is the key /process_webcam_frame reads — the same
          // one the laptop camera sends. Checked against the handler rather
          // than guessed: a wrong key is silently ignored there, so every
          // frame would vanish with no error anywhere.
          ai.send(JSON.stringify({
            image_base64: msg.data,
            captured_at: msg.t,
          }));
          break;
        }

        case 'camera-started':
          setStreaming(true);
          setError(null);
          break;

        case 'camera-error':
          setError(msg.message === 'permission denied'
            ? 'The phone refused camera access. Allow it in the phone’s browser settings and reload that page.'
            : `Phone camera error: ${msg.message}`);
          setStreaming(false);
          break;

        case 'peer-left':
          setPaired(false);
          setStreaming(false);
          setError('The phone disconnected. Reopen the camera page on it, or scan a new code.');
          break;

        case 'error':
          setError(msg.message || 'Pairing failed.');
          break;

        default:
          break;
      }
    };

    ws.onclose = () => { roomRef.current = null; };
  }, []);

  /* ── actions ────────────────────────────────────────────────────────── */

  const startPairing = useCallback(async () => {
    setBusy(true);
    setError(null);
    setFrames(0);
    try {
      const res = await backendFetch('/api/v1/pair/session', { method: 'POST' });
      if (!res.ok) {
        let detail = `Could not start pairing (${res.status})`;
        try { const b = await res.json(); if (b?.detail) detail = b.detail; } catch { /* status will do */ }
        throw new Error(detail);
      }
      const { code: newCode, lan_host: host } = await res.json();
      setCode(newCode);
      setLanHost(host || null);
      setPaired(false);

      const QRCode = (await import('qrcode')).default;
      setQrSvg(await QRCode.toString(phoneUrlFor(newCode, host), {
        type: 'svg', margin: 1, width: 200,
        color: { dark: '#0b0a0c', light: '#ffffff' },
      }));

      await joinRoom(newCode);
      setOpen(true);
    } catch (err) {
      setError(String(err?.message || err));
    } finally {
      setBusy(false);
    }
  }, [joinRoom]);

  const startPhoneCamera = useCallback(async () => {
    setError(null);
    try {
      await openAiSocket();
      roomRef.current?.send(JSON.stringify({ type: 'start' }));
    } catch (err) {
      setError(`Could not reach the analysis pipeline: ${err?.message || err}`);
    }
  }, [openAiSocket]);

  const stopPhoneCamera = useCallback(() => {
    roomRef.current?.send(JSON.stringify({ type: 'stop' }));
    teardown(true);      // keep the room, so one click restarts it
  }, [teardown]);

  /* ── rendering ──────────────────────────────────────────────────────── */

  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={startPairing}
          disabled={disabled || busy}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border-2 border-field text-ink font-bold text-[13px] hover:border-field-hover transition-colors disabled:opacity-60"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Smartphone className="w-4 h-4" />}
          Use my phone as a camera
        </button>
        {error && (
          <p className="text-[12px] text-accent font-medium flex items-start gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-line bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[14px] font-black text-ink flex items-center gap-2">
          <Smartphone className="w-4 h-4 text-accent" />
          Phone camera
        </h3>
        <button
          type="button"
          onClick={() => { teardown(); setOpen(false); setCode(null); }}
          aria-label="Close"
          className="p-1 rounded text-ink-faint hover:text-ink transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* The QR now names the address the SERVER reported, so opening the
          dashboard at localhost is no longer a problem to warn about — it is
          simply handled. What remains worth saying is when the phone will be
          able to load the page but not use its camera. */}
      {insecure && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5">
          <p className="text-[12px] text-ink-muted font-medium leading-relaxed">
            <span className="font-bold text-ink">This page is not on HTTPS.</span>{' '}
            The QR address is correct and the phone will load the page — but it
            will then be refused access to its camera, because browsers only
            allow that on a secure origin. Restart with{' '}
            <code className="font-mono text-[11px] bg-ground px-1 rounded">
              python run_system.py --https
            </code>{' '}
            and reload the dashboard.
          </p>
        </div>
      )}

      {!paired ? (
        <>
          <div className="flex items-start gap-4">
            {qrSvg && (
              <div
                className="shrink-0 rounded-lg bg-white p-2"
                dangerouslySetInnerHTML={{ __html: qrSvg }}
              />
            )}
            <div className="min-w-0 flex flex-col gap-1.5">
              <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
                Scan this with your phone&apos;s camera. Tap through the
                security warning once — the certificate is self-signed, so the
                phone has not seen it before.
              </p>
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint mt-1">
                or type this address
              </p>
              <p className="font-mono text-[11.5px] text-ink break-all">
                {code ? phoneUrlFor(code, lanHost) : ''}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[12px] text-ink-muted font-medium">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent" />
            Waiting for the phone to connect…
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2 text-[12.5px] font-bold text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="w-4 h-4" />
            Phone paired
            {streaming && (
              <span className="ml-1 font-mono text-[11px] font-medium text-ink-faint">
                · {analysed}/{frames} analysed
              </span>
            )}
          </div>

          <div
            ref={stageRef}
            className={`relative overflow-hidden bg-ground ${
              isFullscreen
                ? 'flex items-center justify-center w-screen h-screen'
                : 'rounded-lg border border-line'
            }`}
          >
            {/* Same portrait-frame cap as the main player canvas. This is a
                side panel, so it gets a tighter ceiling — lifted in
                fullscreen, where there is no layout left to protect. */}
            <canvas
              ref={canvasRef}
              className={isFullscreen
                ? 'max-w-full max-h-full w-auto h-auto object-contain block'
                : 'w-full h-auto max-h-[45vh] object-contain block mx-auto'}
            />

            {streaming && (
              <button
                type="button"
                onClick={toggleFullScreen}
                title={isFullscreen ? 'Exit full screen (Esc)' : 'Full screen'}
                aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'}
                className="absolute top-2 right-2 z-20 p-1.5 rounded-md bg-black/55 hover:bg-black/75 text-white backdrop-blur-sm transition-colors"
              >
                {isFullscreen
                  ? <Minimize2 className="w-3.5 h-3.5" />
                  : <Maximize2 className="w-3.5 h-3.5" />}
              </button>
            )}
            {!streaming && (
              <div className="absolute inset-0 grid place-items-center px-6 text-center">
                <p className="text-[12.5px] text-ink-muted font-medium">
                  Press start and the phone opens its camera.
                </p>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {streaming ? (
              <button
                type="button"
                onClick={stopPhoneCamera}
                className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border-2 border-field text-ink font-bold text-[13px] hover:border-field-hover transition-colors"
              >
                <Square className="w-3.5 h-3.5" />
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={startPhoneCamera}
                className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-accent text-white font-bold text-[13px] hover:brightness-110 transition-[filter]"
              >
                <Play className="w-3.5 h-3.5" />
                Turn on phone camera
              </button>
            )}
            <button
              type="button"
              onClick={startPairing}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-line text-ink-muted font-bold text-[12px] hover:text-ink hover:border-field transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              New code
            </button>
          </div>
        </>
      )}

      {error && (
        <p className="text-[12px] text-accent font-medium flex items-start gap-1.5" role="alert">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          {error}
        </p>
      )}
    </div>
  );
}
