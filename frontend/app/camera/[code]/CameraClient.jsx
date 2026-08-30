'use client';

// frontend/app/camera/[code]/CameraClient.jsx
//
// The phone acting as a CCTV camera.
//
// THE ONE-CLICK PROMISE, AND WHAT IT REALLY COSTS
//
// A laptop cannot switch on a phone's camera — no browser permits it, and one
// that did would let a web page watch you from a device you were not holding.
// So the phone grants permission ONCE, here, and then waits. After that the
// laptop's click is genuinely one click: it sends "start" into the pairing
// room and this page, which already holds permission, opens the camera.
//
// WHAT MAKES THIS HARD IS NOT THE CAMERA
//
// It is that a phone propped against a monitor is a hostile environment for a
// web page. The screen dims, the browser throttles the tab, Wi-Fi stalls in a
// lift, the laptop lid closes. Each of those is handled explicitly:
//
//   - a wake lock keeps the screen on while streaming, because a locked phone
//     suspends the camera and the feed simply stops
//   - capture runs off a timer that is re-armed on visibility change, since a
//     backgrounded tab has its timers throttled to once a minute
//   - the socket reconnects with backoff and rejoins the same room, so a
//     30-second Wi-Fi drop costs 30 seconds rather than the session
//   - a heartbeat keeps the room alive while the phone waits, which may be
//     minutes before anybody presses start on the laptop
//
// FRAMES, NOT VIDEO
//
// Each frame is a JPEG carrying its own capture timestamp. A continuous WebM
// stream would use less bandwidth, but it has to be buffered before it can be
// decoded, which costs 1-3 seconds of latency and blurs exactly the timing the
// chair-exit debounce and the cross-camera handoff window depend on. On a LAN
// the bandwidth is free and the timing is not.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Camera, CameraOff, Wifi, WifiOff, AlertTriangle, CheckCircle2,
  Loader2, SwitchCamera, Radio,
} from 'lucide-react';

/** Frames per second sent to the backend. */
const CAPTURE_FPS = 10;

/** JPEG quality. 0.6 is visually fine at 640px and roughly halves the payload. */
const JPEG_QUALITY = 0.6;

/** The width frames are sent at — the space the CV pipeline works in. */
const SEND_WIDTH = 640;

/** Keep the room alive while waiting for the laptop to say "start". */
const HEARTBEAT_MS = 20000;

function backendWsBase() {
  // Same host the page was served from, so a phone that reached the laptop at
  // 192.168.3.5 talks back to 192.168.3.5 — not to "localhost", which on a
  // phone means the phone.
  const explicit = process.env.NEXT_PUBLIC_BACKEND_HTTP;
  if (explicit) return explicit.replace(/^http/, 'ws');
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.hostname}:8001`;
}

export default function CameraClient({ code }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const wsRef = useRef(null);
  const timerRef = useRef(null);
  const wakeLockRef = useRef(null);
  const retryRef = useRef(0);
  const closedByUsRef = useRef(false);

  const [status, setStatus] = useState('connecting');
  // connecting | waiting | streaming | denied | error | expired | replaced
  const [message, setMessage] = useState('Connecting to your dashboard…');
  const [facing, setFacing] = useState('environment');
  const [framesSent, setFramesSent] = useState(0);
  const [online, setOnline] = useState(true);

  /* ── the camera ─────────────────────────────────────────────────────── */

  const stopCapture = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    if (wakeLockRef.current) {
      wakeLockRef.current.release?.().catch(() => {});
      wakeLockRef.current = null;
    }
  }, []);

  const sendFrame = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    const ws = wsRef.current;
    if (!video || !canvas || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (!video.videoWidth) return;   // not ready yet

    const scale = SEND_WIDTH / video.videoWidth;
    canvas.width = SEND_WIDTH;
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    try {
      ws.send(JSON.stringify({
        type: 'frame',
        // The capture instant, not the arrival instant. Everything downstream
        // that measures duration — chair exits, focus blocks, the handoff
        // window — is only as accurate as this.
        t: Date.now(),
        data: canvas.toDataURL('image/jpeg', JPEG_QUALITY),
      }));
      setFramesSent((n) => n + 1);
    } catch {
      // A failed send is not fatal; the next frame is 100ms away.
    }
  }, []);

  const startCapture = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: facing,
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }

      // A locked phone suspends the camera. Without this the feed dies the
      // moment the screen times out, which on most phones is under a minute.
      try {
        if ('wakeLock' in navigator) {
          wakeLockRef.current = await navigator.wakeLock.request('screen');
        }
      } catch {
        // Not fatal — the banner tells the user to keep the screen on.
      }

      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(sendFrame, Math.round(1000 / CAPTURE_FPS));

      setStatus('streaming');
      setMessage('Streaming to your dashboard.');
      wsRef.current?.send(JSON.stringify({ type: 'camera-started' }));
    } catch (err) {
      const denied = /NotAllowed|Permission/i.test(String(err?.name || err));
      setStatus(denied ? 'denied' : 'error');
      setMessage(denied
        ? 'Camera access was refused. Allow it in your browser settings for this site, then reload.'
        : `Could not open the camera: ${err?.message || err}`);
      wsRef.current?.send(JSON.stringify({
        type: 'camera-error',
        message: denied ? 'permission denied' : String(err?.message || err),
      }));
    }
  }, [facing, sendFrame]);

  /* ── the pairing socket ─────────────────────────────────────────────── */

  const connect = useCallback(() => {
    if (!code) return;
    const url = `${backendWsBase()}/api/v1/pair/ws/${encodeURIComponent(code)}?role=camera`;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch {
      setStatus('error');
      setMessage('Could not reach the dashboard. Is the laptop still running?');
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0;
      setOnline(true);
      setStatus((s) => (s === 'streaming' ? s : 'waiting'));
      setMessage((m) => (status === 'streaming' ? m : 'Paired. Waiting for the laptop to start the camera…'));
    };

    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      switch (msg.type) {
        case 'start':
          startCapture();
          break;
        case 'stop':
          stopCapture();
          setStatus('waiting');
          setMessage('Stopped by the laptop. Waiting…');
          break;
        case 'error':
          if (msg.reason === 'expired') {
            closedByUsRef.current = true;   // do not reconnect into a dead room
            setStatus('expired');
            setMessage(msg.message || 'This pairing code has expired.');
          }
          break;
        case 'replaced':
          closedByUsRef.current = true;
          stopCapture();
          setStatus('replaced');
          setMessage('Another phone took over this pairing.');
          break;
        case 'peer-gone':
        case 'peer-left':
          // The laptop closed the tab. Stop the camera rather than keep
          // filming into a socket nobody is reading.
          stopCapture();
          setStatus('waiting');
          setMessage('The dashboard disconnected. Waiting for it to come back…');
          break;
        default:
          break;
      }
    };

    ws.onclose = () => {
      setOnline(false);
      if (closedByUsRef.current) return;
      // Reconnect with backoff. A phone in a lift comes back; a session that
      // gave up on the first drop would not.
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
      retryRef.current += 1;
      setMessage(`Connection lost. Retrying in ${Math.round(delay / 1000)}s…`);
      setTimeout(() => {
        if (!closedByUsRef.current) connect();
      }, delay);
    };

    ws.onerror = () => { /* onclose follows and handles it */ };
  }, [code, startCapture, stopCapture, status]);

  useEffect(() => {
    closedByUsRef.current = false;
    connect();
    return () => {
      closedByUsRef.current = true;
      stopCapture();
      wsRef.current?.close();
    };
    // `connect` is intentionally not a dependency: re-running this effect on
    // every status change would tear the socket down mid-session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  // Keep the room alive while nothing else is being sent.
  useEffect(() => {
    const t = setInterval(() => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, HEARTBEAT_MS);
    return () => clearInterval(t);
  }, []);

  // A backgrounded tab has its timers throttled to roughly once a minute, so
  // the frame rate collapses without the interval being re-armed on return.
  // The wake lock is also released when the page is hidden and must be retaken.
  useEffect(() => {
    const onVisible = async () => {
      if (document.visibilityState !== 'visible') return;
      if (status !== 'streaming') return;
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(sendFrame, Math.round(1000 / CAPTURE_FPS));
      try {
        if ('wakeLock' in navigator && !wakeLockRef.current) {
          wakeLockRef.current = await navigator.wakeLock.request('screen');
        }
      } catch { /* the banner already warns about this */ }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [status, sendFrame]);

  // Switching between front and back cameras mid-session.
  const flipCamera = async () => {
    const next = facing === 'environment' ? 'user' : 'environment';
    setFacing(next);
    if (status === 'streaming') {
      stopCapture();
      // startCapture reads `facing` from the closure, so it has to run after
      // the state settles — one tick is enough.
      setTimeout(() => startCapture(), 60);
    }
  };

  /* ── rendering ──────────────────────────────────────────────────────── */

  const tone = {
    streaming: { Icon: Radio, cls: 'text-emerald-400', label: 'Live' },
    waiting: { Icon: Loader2, cls: 'text-amber-400 animate-spin', label: 'Ready' },
    connecting: { Icon: Loader2, cls: 'text-ink-faint animate-spin', label: 'Connecting' },
    denied: { Icon: CameraOff, cls: 'text-red-400', label: 'Blocked' },
    error: { Icon: AlertTriangle, cls: 'text-red-400', label: 'Error' },
    expired: { Icon: AlertTriangle, cls: 'text-red-400', label: 'Expired' },
    replaced: { Icon: AlertTriangle, cls: 'text-amber-400', label: 'Replaced' },
  }[status] || { Icon: Loader2, cls: 'text-ink-faint', label: '' };

  const StatusIcon = tone.Icon;

  return (
    <div className="min-h-[100dvh] bg-[#0b0a0c] text-white flex flex-col">
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 bg-[#dc2626] rounded-md grid place-items-center">
            <div className="w-1.5 h-1.5 bg-white rounded-[2px]" />
          </div>
          <span className="font-extrabold text-[15px] tracking-tight">VisionWorks</span>
        </div>
        <div className="flex items-center gap-2">
          {online
            ? <Wifi className="w-4 h-4 text-white/40" />
            : <WifiOff className="w-4 h-4 text-red-400" />}
          <span className={`inline-flex items-center gap-1.5 text-[12px] font-bold ${tone.cls}`}>
            <StatusIcon className="w-3.5 h-3.5" />
            {tone.label}
          </span>
        </div>
      </header>

      <div className="relative flex-1 bg-black">
        <video
          ref={videoRef}
          playsInline
          muted
          autoPlay
          className="absolute inset-0 w-full h-full object-contain"
        />
        <canvas ref={canvasRef} className="hidden" />

        {status !== 'streaming' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <StatusIcon className={`w-8 h-8 ${tone.cls}`} strokeWidth={1.8} />
            <p className="text-[15px] font-bold">{message}</p>

            {status === 'waiting' && (
              <p className="text-[13px] text-white/50 leading-relaxed max-w-xs">
                Leave this page open. Press <strong className="text-white/80">Turn On
                Camera</strong> on your laptop and the feed starts here.
              </p>
            )}

            {status === 'denied' && (
              <p className="text-[13px] text-white/50 leading-relaxed max-w-xs">
                Tap the padlock or ⓘ in your browser&apos;s address bar, allow
                Camera for this site, then reload this page.
              </p>
            )}

            {status === 'expired' && (
              <p className="text-[13px] text-white/50 leading-relaxed max-w-xs">
                Codes last five minutes. Generate a new one on the laptop and
                scan it again.
              </p>
            )}
          </div>
        )}

        {status === 'streaming' && (
          <div className="absolute top-3 left-3 flex items-center gap-2 rounded-full bg-black/60 backdrop-blur px-3 py-1.5">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-[12px] font-bold">REC</span>
            <span className="text-[11px] text-white/50 font-mono">
              {framesSent} frames
            </span>
          </div>
        )}
      </div>

      <footer className="px-4 py-3 border-t border-white/10 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/35">
            paired as
          </p>
          <p className="font-mono text-[13px] font-bold tracking-[0.16em]">{code}</p>
        </div>
        <button
          type="button"
          onClick={flipCamera}
          className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-3 py-2 text-[13px] font-bold active:bg-white/10 transition-colors"
        >
          <SwitchCamera className="w-4 h-4" />
          {facing === 'environment' ? 'Back' : 'Front'}
        </button>
      </footer>

      {status === 'streaming' && (
        <p className="px-4 pb-3 text-[11px] text-white/35 leading-relaxed">
          Keep this screen on. If the phone locks, the camera stops — the page
          asks Android to stay awake, but not every browser honours it.
        </p>
      )}
    </div>
  );
}
