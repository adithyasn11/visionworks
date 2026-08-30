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
  Play, Square, RefreshCw, Wifi,
} from 'lucide-react';

import { backendFetch, backendSocketUrl, BACKEND_HTTP } from '../lib/backend';
import { publishFrame, clearFrame } from '../lib/liveFrame';

/** How often to re-check whether the phone has joined, while showing the QR. */
const POLL_MS = 1500;

function wsBase() {
  return BACKEND_HTTP.replace(/^http/, 'ws');
}

/**
 * The address the PHONE must open — not the one the laptop is using.
 *
 * A laptop on `localhost:3000` that puts "localhost" in a QR code sends the
 * phone to the phone's own localhost, where nothing is listening. The phone
 * needs this machine's LAN address, and it needs https, because browsers
 * refuse camera access on an insecure origin that is not localhost.
 */
function phoneUrlFor(code) {
  if (typeof window === 'undefined') return '';
  const host = window.location.hostname;
  const port = window.location.port ? `:${window.location.port}` : '';
  return `${window.location.protocol}//${host}${port}/camera/${code}`;
}

export default function PhoneCameraPanel({ cameraId, onFrame, disabled = false }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(null);
  const [qrSvg, setQrSvg] = useState(null);
  const [paired, setPaired] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [frames, setFrames] = useState(0);
  const [busy, setBusy] = useState(false);

  const roomRef = useRef(null);     // socket to the pairing room
  const aiRef = useRef(null);       // socket to the AI pipeline
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const pollRef = useRef(null);

  const host = typeof window !== 'undefined' ? window.location.hostname : '';
  const isLoopback = ['localhost', '127.0.0.1', '::1'].includes(host);

  // TWO DIFFERENT PROBLEMS, TWO DIFFERENT MESSAGES.
  //
  // `onLoopback` — the QR would point the phone at ITS OWN localhost, where
  // nothing is listening. The scan appears to work and then the page never
  // loads, which is the most confusing possible failure.
  //
  // `insecure` — the address is right but the protocol is http, so the phone
  // will load the page and then be refused camera access.
  //
  // Warning about only one of them, as this first did, leaves somebody
  // debugging the wrong thing.
  const onLoopback = isLoopback;
  const insecure = typeof window !== 'undefined'
    && window.location.protocol !== 'https:'
    && !isLoopback;

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
    setStreaming(false);
    clearFrame();
  }, []);

  useEffect(() => () => teardown(), [teardown]);

  /* ── the AI pipeline socket ─────────────────────────────────────────── */

  const openAiSocket = useCallback(async () => {
    if (aiRef.current) return aiRef.current;
    const url = await backendSocketUrl(
      `/api/v1/video/process_webcam_frame?camera_id=${encodeURIComponent(cameraId)}`);
    const ws = new WebSocket(url);

    ws.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.error) { setError(data.error); return; }
      if (!data.frame_base64) return;

      // Paint the annotated frame, and publish it so the zone editor can use
      // it as a backdrop exactly as it does the laptop camera's.
      const canvas = canvasRef.current;
      if (canvas) {
        if (!imgRef.current) imgRef.current = new window.Image();
        const img = imgRef.current;
        img.onload = () => {
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          canvas.getContext('2d').drawImage(img, 0, 0);
        };
        img.src = data.frame_base64;
      }
      publishFrame(data.frame_base64);
      if (onFrame) onFrame(data);
    };

    ws.onclose = () => { aiRef.current = null; };
    aiRef.current = ws;
    return ws;
  }, [cameraId, onFrame]);

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
          const ai = aiRef.current;
          if (ai?.readyState === WebSocket.OPEN) {
            // `image_base64` is the key /process_webcam_frame reads — the same
            // one the laptop camera sends. Checked against the handler rather
            // than guessed: a wrong key is silently ignored there, so every
            // frame would vanish with no error anywhere.
            ai.send(JSON.stringify({
              image_base64: msg.data,
              captured_at: msg.t,
            }));
            setFrames((n) => n + 1);
          }
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
      const { code: newCode } = await res.json();
      setCode(newCode);
      setPaired(false);

      const QRCode = (await import('qrcode')).default;
      setQrSvg(await QRCode.toString(phoneUrlFor(newCode), {
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

      {onLoopback && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5">
          <p className="text-[12px] text-ink-muted font-medium leading-relaxed">
            <span className="font-bold text-ink">
              This dashboard is open at “{host}”, so the QR code points there too —
              and on your phone that means the PHONE.
            </span>{' '}
            Open the dashboard at this laptop&apos;s network address instead, then
            generate a new code. Run{' '}
            <code className="font-mono text-[11px] bg-ground px-1 rounded">
              python scripts/make_dev_cert.py
            </code>{' '}
            to see the address, and{' '}
            <code className="font-mono text-[11px] bg-ground px-1 rounded">
              npm run dev:https
            </code>{' '}
            so the phone is allowed to use its camera.
          </p>
        </div>
      )}

      {insecure && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5">
          <p className="text-[12px] text-ink-muted font-medium leading-relaxed">
            <span className="font-bold text-ink">This page is not on HTTPS.</span>{' '}
            The phone will load the camera page and then be refused access to
            its camera — browsers only allow it on a secure origin. Restart the
            dev server with{' '}
            <code className="font-mono text-[11px] bg-ground px-1 rounded">npm run dev:https</code>{' '}
            and reopen the dashboard at{' '}
            <code className="font-mono text-[11px] bg-ground px-1 rounded">
              https://{host}:3000
            </code>.
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
                {code ? phoneUrlFor(code) : ''}
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
                · {frames} frames
              </span>
            )}
          </div>

          <div className="relative rounded-lg overflow-hidden bg-ground border border-line">
            <canvas ref={canvasRef} className="w-full h-auto block" />
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
