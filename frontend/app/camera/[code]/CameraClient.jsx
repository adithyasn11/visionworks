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
  Loader2, SwitchCamera, Radio, RefreshCw,
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

  // WHY A GENERATION COUNTER AND NOT A BOOLEAN.
  //
  // This was `closedByUsRef`, a single shared flag meaning "we are shutting
  // down, do not reconnect". Under React StrictMode — on in this project —
  // dev mounts, unmounts and remounts every component. The unmount set the
  // flag and closed socket #1; the remount opened socket #2; then socket #1's
  // delayed `onclose` fired, saw the flag still true, and stopped everything.
  //
  // From the laptop that looks exactly like what you reported: the phone
  // connects and immediately disconnects, with no error anywhere.
  //
  // A counter fixes it because each socket captures the generation it was
  // born in. A stale socket closing can only ever cancel ITSELF.
  const genRef = useRef(0);
  const aliveRef = useRef(true);

  const [status, setStatus] = useState('connecting');
  // connecting | waiting | streaming | denied | error | expired | replaced
  const [message, setMessage] = useState('Connecting to your dashboard…');
  const [facing, setFacing] = useState('environment');
  const [framesSent, setFramesSent] = useState(0);
  const [isPortrait, setIsPortrait] = useState(false);
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

    // The VIDEO's shape, not the screen's: a phone can report a landscape
    // window while the camera track is still delivering portrait frames, and
    // it is the track that decides what the detector sees.
    setIsPortrait(video.videoHeight > video.videoWidth);

    // SEND A LANDSCAPE FRAME EVEN WHEN THE PHONE IS HELD UPRIGHT.
    //
    // A portrait phone reports 720x1280, so scaling by width alone sent a
    // 640x1138 frame: taller than the laptop screen, and the wrong shape for
    // a pipeline built around a wide CCTV view — a tall frame spends most of
    // its pixels on ceiling and floor.
    //
    // So the frame is fitted into a 16:9 box: the whole camera view is kept
    // (letterboxed, never cropped — cropping would cut people out at the
    // edges) and every frame arrives the same size regardless of how the
    // phone happens to be held. That also keeps zone polygons meaningful,
    // since they are stored in this pixel space and would otherwise shift
    // the moment the phone rotated.
    const SEND_HEIGHT = Math.round(SEND_WIDTH * 9 / 16);
    canvas.width = SEND_WIDTH;
    canvas.height = SEND_HEIGHT;
    const ctx = canvas.getContext('2d');

    const fit = Math.min(SEND_WIDTH / video.videoWidth,
                         SEND_HEIGHT / video.videoHeight);
    const dw = video.videoWidth * fit;
    const dh = video.videoHeight * fit;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, SEND_WIDTH, SEND_HEIGHT);
    ctx.drawImage(video, (SEND_WIDTH - dw) / 2, (SEND_HEIGHT - dh) / 2, dw, dh);

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
    const myGen = genRef.current;
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
      // Both reads go through the updater form so this callback does not
      // close over `status`. Depending on it would rebuild `connect` on every
      // status change, and a rebuilt `connect` is a new socket — the page
      // would reconnect to the room each time it changed what it was showing.
      setStatus((prev) => {
        if (prev !== 'streaming') {
          setMessage('Paired. Waiting for the laptop to start the camera…');
        }
        return prev === 'streaming' ? prev : 'waiting';
      });
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
            genRef.current += 1;   // do not reconnect into a dead room
            setStatus('expired');
            setMessage(msg.message || 'This pairing code has expired.');
          }
          break;
        case 'replaced':
          genRef.current += 1;
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
      // A socket from a previous generation closing says nothing about the
      // current one. Ignoring it is the whole point of the counter.
      if (myGen !== genRef.current || !aliveRef.current) return;

      setOnline(false);
      // Reconnect with backoff. A phone in a lift comes back; a session that
      // gave up on the first drop would not.
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
      retryRef.current += 1;
      setMessage(`Connection lost. Retrying in ${Math.round(delay / 1000)}s…`);
      setTimeout(() => {
        if (myGen === genRef.current && aliveRef.current) connect();
      }, delay);
    };

    ws.onerror = () => { /* onclose follows and handles it */ };
  }, [code, startCapture, stopCapture]);

  useEffect(() => {
    aliveRef.current = true;
    connect();
    return () => {
      // Retire this generation. Sockets opened under it stop being listened
      // to; a remount bumps nothing and simply opens a new one.
      aliveRef.current = false;
      genRef.current += 1;
      stopCapture();
      try { wsRef.current?.close(); } catch { /* already gone */ }
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

  // COLOUR AND MOTION ARE SEPARATE PROPERTIES.
  //
  // They were one `cls` string containing both, applied to the whole badge —
  // so `animate-spin` rotated the label along with the icon and "Ready" span
  // sideways down the corner. Only the icon should ever move.
  const tone = {
    streaming: { Icon: Radio, cls: 'text-emerald-400', label: 'Live' },
    waiting: { Icon: Loader2, cls: 'text-amber-400', label: 'Ready', spin: true },
    connecting: { Icon: Loader2, cls: 'text-white/40', label: 'Connecting', spin: true },
    denied: { Icon: CameraOff, cls: 'text-red-400', label: 'Blocked' },
    error: { Icon: AlertTriangle, cls: 'text-red-400', label: 'Error' },
    expired: { Icon: AlertTriangle, cls: 'text-red-400', label: 'Expired' },
    replaced: { Icon: AlertTriangle, cls: 'text-amber-400', label: 'Replaced' },
  }[status] || { Icon: Loader2, cls: 'text-white/40', label: '', spin: true };

  const StatusIcon = tone.Icon;
  const spinClass = tone.spin ? 'animate-spin' : '';

  return (
    <div className="min-h-[100dvh] bg-[#0b0a0c] text-white flex flex-col">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 bg-[#dc2626] rounded-md grid place-items-center">
            <div className="w-1.5 h-1.5 bg-white rounded-[2px]" />
          </div>
          <span className="font-extrabold text-[0.9375rem] tracking-tight">VisionWorks</span>
        </div>
        <div className="flex items-center gap-2">
          {online
            ? <Wifi className="w-4 h-4 text-white/40" />
            : <WifiOff className="w-4 h-4 text-red-400" />}
          {/* nowrap and shrink-0: without them the flex row squeezes this to
              its icon width and the word wraps one letter per line, which is
              how "Ready" ended up printed vertically down the corner. */}
          <span className={`inline-flex items-center gap-1.5 text-[0.75rem] font-bold whitespace-nowrap shrink-0 ${tone.cls}`}>
            <StatusIcon className={`w-3.5 h-3.5 ${spinClass}`} />
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
            <StatusIcon className={`w-8 h-8 ${tone.cls} ${spinClass}`} strokeWidth={1.8} />
            <p className="text-[0.9375rem] font-bold">{message}</p>

            {status === 'waiting' && (
              <p className="text-[0.8125rem] text-white/50 leading-relaxed max-w-xs">
                Leave this page open. Press <strong className="text-white/80">Turn On
                Camera</strong> on your laptop and the feed starts here.
              </p>
            )}

            {status === 'denied' && (
              <p className="text-[0.8125rem] text-white/50 leading-relaxed max-w-xs">
                Tap the padlock or ⓘ in your browser&apos;s address bar, allow
                Camera for this site, then reload this page.
              </p>
            )}

            {status === 'expired' && (
              <>
                <p className="text-[0.8125rem] text-white/50 leading-relaxed max-w-xs">
                  Codes last five minutes. Press{' '}
                  <strong className="text-white/80">New code</strong> on the
                  laptop, then scan again — or retry if you have just done that.
                </p>
                {/* A dead end is not an acceptable end state on a phone.
                    Reloading is the whole recovery, and asking somebody to
                    find the browser's refresh button while holding a propped-
                    up phone is worse than putting it on screen. */}
                <button
                  type="button"
                  onClick={() => window.location.reload()}
                  className="mt-1 inline-flex items-center gap-2 rounded-lg border border-white/20 px-4 py-2.5 text-[0.875rem] font-bold active:bg-white/10 transition-colors"
                >
                  <RefreshCw className="w-4 h-4" />
                  Try again
                </button>
              </>
            )}

            {(status === 'error' || status === 'replaced') && (
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="mt-1 inline-flex items-center gap-2 rounded-lg border border-white/20 px-4 py-2.5 text-[0.875rem] font-bold active:bg-white/10 transition-colors"
              >
                <RefreshCw className="w-4 h-4" />
                Try again
              </button>
            )}
          </div>
        )}

        {status === 'streaming' && isPortrait && (
          // Frames are letterboxed into 16:9, so an upright phone fills only
          // about a third of the width — the rest is black bars. Nothing
          // breaks, but the detector gets far fewer pixels of the room, so
          // it is worth one line telling the user how to fix it.
          <div className="absolute bottom-3 left-3 right-3 rounded-lg bg-black/70 backdrop-blur px-3 py-2">
            <p className="text-[0.75rem] text-white/80 text-center">
              Turn the phone sideways — a landscape view sees far more of the room.
            </p>
          </div>
        )}

        {status === 'streaming' && (
          <div className="absolute top-3 left-3 flex items-center gap-2 rounded-full bg-black/60 backdrop-blur px-3 py-1.5">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-[0.75rem] font-bold">REC</span>
            <span className="text-[0.6875rem] text-white/50 font-mono">
              {framesSent} frames
            </span>
          </div>
        )}
      </div>

      <footer className="px-4 py-3 border-t border-white/10 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-white/35">
            paired as
          </p>
          <p className="font-mono text-[0.8125rem] font-bold tracking-[0.16em]">{code}</p>
        </div>
        <button
          type="button"
          onClick={flipCamera}
          className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-3 py-2 text-[0.8125rem] font-bold active:bg-white/10 transition-colors"
        >
          <SwitchCamera className="w-4 h-4" />
          {facing === 'environment' ? 'Back' : 'Front'}
        </button>
      </footer>

      {status === 'streaming' && (
        <p className="px-4 pb-3 text-[0.6875rem] text-white/35 leading-relaxed">
          Keep this screen on. If the phone locks, the camera stops — the page
          asks Android to stay awake, but not every browser honours it.
        </p>
      )}
    </div>
  );
}
