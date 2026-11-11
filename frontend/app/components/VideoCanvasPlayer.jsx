'use client';

// frontend/app/components/VideoCanvasPlayer.jsx
import React, { useRef, useEffect, useState, useCallback } from 'react';
import {
  Eye, Upload, Play, Pause, RefreshCw, Shield,
  Sliders, CheckCircle2, Loader2, AlertCircle, Video,
  RotateCcw, RotateCw, Cpu, Maximize2, Minimize2, Camera, CameraOff
} from 'lucide-react';

// Backend origin and the token-attaching helpers. Every processing socket
// carries the Supabase access token so the telemetry it produces is attributed
// to the caller's organisation — see app/lib/backend.js.
import { publishFrame, clearFrame } from '../lib/liveFrame';
import PhoneCameraPanel from './PhoneCameraPanel';
import { backendFetch, backendSocketUrl } from '../lib/backend';
import { drawHUD } from '../lib/overlay';
import ZoneActivity from './ZoneActivity';
import PpeStatus from './PpeStatus';
import DecisionFeed from './DecisionFeed';
import { addDecisions } from '../lib/decisions';
import { addBanners, expireBanners } from '../lib/alerts';
import AlertBanners from './AlertBanners';
import { addZoneEvents } from '../lib/zoneEvents';

/** mm:ss for the scrubber. */
function formatTime(seconds) {
  if (!seconds || isNaN(seconds) || seconds < 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}


/** "Now" as a datetime-local value in the viewer's own zone: the picker's upper bound. */
function localNowValue() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

/**
 * `readOnly` hides the controls that START an analysis — upload and camera.
 * Running the pipeline WRITES telemetry, so it is a configuration act
 * (`analysis.run`, ADMIN + MANAGER), not a read. LAYER 1 only: the three
 * processing WebSockets refuse a VIEWER server-side (layer 2), and any rows
 * that did reach the database are still org-scoped by RLS (layer 3).
 */
export const VideoCanvasPlayer = ({ activeZones = [], onFrameData, readOnly = false, cameraId = 'live_webcam' }) => {
  const canvasRef   = useRef(null);
  const containerRef = useRef(null);
  const stageRef = useRef(null);      // just the canvas + its overlays
  const fileInputRef = useRef(null);
  const hiddenVideoRef = useRef(null);
  const offscreenCanvasRef = useRef(null);
  const wsRef       = useRef(null);
  const imgRef      = useRef(null);
  const captureIntervalRef = useRef(null);
  const localStreamRef = useRef(null);
  // Holds startBackendDirectWebcam, which is declared further down but needs to
  // be callable from startLiveWebcam's error/catch paths above it.
  const startBackendDirectWebcamRef = useRef(null);
  // Bumped by every connect/reset/unmount so an in-flight `await
  // backendSocketUrl(...)` can tell it is no longer the active attempt and
  // close the socket it just opened instead of installing it into wsRef —
  // otherwise a fast Reset/Upload double-click during that await window
  // leaks an orphaned, uncleaned-up WebSocket.
  const connectionEpochRef = useRef(0);

  const [phase, setPhase] = useState('idle'); // idle | uploading | processing | webcam | done | error
  const [fileName, setFileName] = useState('');
  // When the footage was recorded, as the browser's datetime-local value
  // ("2026-11-08T09:30"). Optional: sent with the upload so safety events are
  // stamped on the timeline where they really happened. Empty means "now".
  const [recordedAt, setRecordedAt] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [progress, setProgress] = useState(0);
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [durationSec, setDurationSec] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  // Not `useState(true)`: the server owns this now (PRIVACY_BLUR_DEFAULT),
  // and a hardcoded `true` here would show "Face Anonymisation ✓" over
  // unblurred frames — a privacy claim the pixels do not support. `null`
  // means "not yet told", and the control stays disabled until the first
  // frame reports the real state.
  const [enableBlur, setEnableBlur] = useState(null);
  // A click is optimistic; the server echoes privacy_blur on EVERY frame,
  // so without this the next frame in flight would snap the checkbox back
  // to the pre-click value for a few hundred ms. Holds the value we asked
  // for until the server's echo agrees with it.
  const pendingBlurRef = useRef(null);
  const [deviceInfo, setDeviceInfo] = useState('GPU (CUDA)');
  const [isWebcamActive, setIsWebcamActive] = useState(false);
  const [stats, setStats] = useState({ detected: 0, flagged: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);
  // Zone events from the safety layer, newest first. `safetyOn` is true once a
  // frame carries a `safety` block, i.e. the backend is running the safety layer;
  // with it off nothing here appears.
  // Uploaded videos play at the speed they were filmed; switch off to process as
  // fast as the machine can.
  const [realtime, setRealtime] = useState(true);
  const realtimeRef = useRef(true);
  const [zoneEvents, setZoneEvents] = useState([]);
  const [decisions, setDecisions] = useState([]);
  // New alerts raise a short-lived banner over the picture; `openCount` is how many flags this run has raised.
  const [banners, setBanners] = useState([]);
  const [openCount, setOpenCount] = useState(0);
  const bannersHeldRef = useRef(false);               // the pointer or focus is on the banners: do not clear them
  const bannerSeenRef = useRef(new Set());            // alerts already bannered this run, so a re-sent one cannot banner twice
  const [safetyOn, setSafetyOn] = useState(false);
  // The latest frame's people (for the PPE list) and, when the helmet/vest model
  // is not available, why.
  const [ppeEntities, setPpeEntities] = useState([]);
  const [ppeUnavailable, setPpeUnavailable] = useState(null);
  const zoneEventIdRef = useRef(0);

  // Banners clear themselves; the alert stays in the decisions list.
  useEffect(() => {
    if (banners.length === 0) return undefined;
    const timer = setInterval(() => { if (!bannersHeldRef.current) setBanners((list) => expireBanners(list)); }, 1000);
    return () => clearInterval(timer);
  }, [banners.length]);

  const handleFrame = useCallback((data) => {
    if (!data.frame_base64) return;
    if (data.progress_pct !== undefined) setProgress(data.progress_pct);
    if (data.current_time_seconds !== undefined) setCurrentTimeSec(data.current_time_seconds);
    if (data.duration_seconds !== undefined) setDurationSec(data.duration_seconds);
    if (data.is_paused !== undefined) setIsPaused(data.is_paused);
    // The server is the authority on whether the frame we just received was
    // blurred, so the toggle mirrors what actually happened to the pixels.
    // While a click is in flight, ignore echoes of the OLD value; once the
    // server confirms the requested value, drop the pending marker.
    if (data.privacy_blur !== undefined) {
      const pending = pendingBlurRef.current;
      if (pending === null) {
        setEnableBlur(data.privacy_blur);
      } else if (pending === data.privacy_blur) {
        pendingBlurRef.current = null;
        setEnableBlur(data.privacy_blur);
      }
    }

    const entities = data.tracked_entities || [];
    setStats({ detected: entities.length, flagged: entities.filter((e) => e.flagged === true).length });

    if (data.safety) {
      setSafetyOn(true);
      setPpeUnavailable(data.safety.ppe_unavailable || null);
      setPpeEntities(entities);
      if (Array.isArray(data.safety.decisions) && data.safety.decisions.length > 0) {
        const made = data.safety.decisions;
        setDecisions((list) => addDecisions(list, made));
        const unseen = made.filter((d) => d && d.id && !bannerSeenRef.current.has(d.id));
        unseen.forEach((d) => bannerSeenRef.current.add(d.id));
        setBanners((list) => addBanners(list, unseen));
      }
      if (typeof data.safety.open_count === 'number') setOpenCount(data.safety.open_count);
      const incoming = data.safety.events;
      if (Array.isArray(incoming) && incoming.length > 0) {
        const first = zoneEventIdRef.current;
        zoneEventIdRef.current += incoming.length;
        setZoneEvents((list) => addZoneEvents(list, incoming, first));
      }
    }

    if (onFrameData) onFrameData(data);

    if (typeof window === 'undefined') return;
    if (!imgRef.current) imgRef.current = new window.Image();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    imgRef.current.onload = () => {
      canvas.width  = imgRef.current.naturalWidth;
      canvas.height = imgRef.current.naturalHeight;
      ctx.drawImage(imgRef.current, 0, 0);
      drawHUD(ctx, canvas, entities, activeZones, enableBlur);
    };
    imgRef.current.src = data.frame_base64;

    // Share the RAW frame with anything else that wants to see what the
    // camera sees — the zone editor draws polygons over it, so "Turn On
    // Camera" on this page gives you a live backdrop on the Zones page.
    //
    // The raw frame, deliberately, not the annotated canvas: the editor draws
    // its own zone outlines, and shipping the HUD's bounding boxes over there
    // would double up the overlay.
    publishFrame(data.frame_base64);
  }, [activeZones, enableBlur, onFrameData]);

  // Connect WebSocket to video file processing session
  const connectProcessingWS = useCallback(async (sid) => {
    if (wsRef.current) wsRef.current.close();

    const epoch = ++connectionEpochRef.current;
    const ws = new WebSocket(await backendSocketUrl(`/api/v1/video/process/${sid}${realtimeRef.current ? '' : '?realtime=false'}`));
    // A Reset or a second connect attempt during the await above must win —
    // otherwise this socket overwrites wsRef.current with a connection nothing
    // will ever close.
    if (connectionEpochRef.current !== epoch) { ws.close(); return; }
    wsRef.current = ws;

    ws.onopen  = () => setStatusMsg('Connected — detection running...');
    ws.onclose = () => { setPhase(p => p === 'processing' ? 'done' : p); };
    ws.onerror = () => { setPhase('error'); setStatusMsg('WebSocket error — is the backend running?'); };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'INIT') {
          setStatusMsg(msg.message);
          if (msg.duration_seconds) setDurationSec(msg.duration_seconds);
          if (msg.device) setDeviceInfo(msg.device.toUpperCase());
        }
        if (msg.type === 'FRAME')    { handleFrame(msg); }
        if (msg.type === 'COMPLETE') { setPhase('done'); setProgress(100); setStatusMsg(msg.message); }
        if (msg.type === 'ERROR')    { setPhase('error'); setStatusMsg(msg.error); }
        if (msg.error && !msg.type)  { setPhase('error'); setStatusMsg(msg.error); }
      } catch (_) {}
    };
  }, [handleFrame]);

  // Stop Webcam Stream cleanly.
  // Declared before handleFileChange / startLiveWebcam because both call it;
  // `const` bindings are not hoisted, so defining it later threw a ReferenceError.
  const stopWebcamStream = useCallback(() => {
    // Invalidates any connect attempt still awaiting backendSocketUrl(), so it
    // closes its socket instead of installing it after we just cleaned up.
    connectionEpochRef.current += 1;
    if (captureIntervalRef.current) {
      clearInterval(captureIntervalRef.current);
      captureIntervalRef.current = null;
    }
    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach(track => track.stop());
      localStreamRef.current = null;
    }
    // Detach the stopped stream from the hidden <video> element too — leaving
    // it attached keeps the (now-stopped) tracks referenced and a stale
    // srcObject around for the next startLiveWebcam() to trip over.
    if (hiddenVideoRef.current) {
      hiddenVideoRef.current.srcObject = null;
    }
    // Nothing is producing frames any more. Without this the zone editor keeps
    // showing the last frame as though the camera were still on.
    clearFrame();
    if (wsRef.current) {
      try {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ action: 'stop' }));
        }
        wsRef.current.close();
      } catch (_) {}
      wsRef.current = null;
    }
    setIsWebcamActive(false);
  }, []);

  const handleReset = useCallback(() => {
    stopWebcamStream();
    setPhase('idle');
    setFileName('');
    setSessionId('');
    setProgress(0);
    setCurrentTimeSec(0);
    setDurationSec(0);
    setIsPaused(false);
    setStatusMsg('');
    setStats({ detected: 0, flagged: 0 });
    setZoneEvents([]);
    setDecisions([]);
    setBanners([]);
    setOpenCount(0);
    bannerSeenRef.current = new Set();
    setSafetyOn(false);
    setPpeEntities([]);
    setPpeUnavailable(null);
    // Forget the last session's privacy setting. The next session re-reads it
    // from the server, which may well have a different one (a door camera
    // opening with ?blur=false, or a changed PRIVACY_BLUR_DEFAULT).
    setEnableBlur(null);
    pendingBlurRef.current = null;
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#090D16';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
  }, [stopWebcamStream]);

  // Upload Video File
  const handleFileChange = useCallback(async (e) => {
    stopWebcamStream();
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';

    // A new run starts clean: the previous run's decisions, zone activity, banners and PPE list
    // belong to another video (worker numbers restart per run, so they would be mixed up).
    setZoneEvents([]);
    setDecisions([]);
    setBanners([]);
    setOpenCount(0);
    bannerSeenRef.current = new Set();
    setPpeEntities([]);
    setPpeUnavailable(null);

    setFileName(file.name);
    setPhase('uploading');
    setStatusMsg('Uploading video to backend...');
    setProgress(0);
    setCurrentTimeSec(0);
    setDurationSec(0);
    setIsPaused(false);
    setIsWebcamActive(false);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('camera_id', cameraId);
      if (recordedAt) {
        // The picker gives LOCAL time with no zone. Converting here pins it to
        // the viewer's own zone and sends an unambiguous UTC instant.
        const when = new Date(recordedAt);
        if (!Number.isNaN(when.getTime())) formData.append('recorded_at', when.toISOString());
      }

      const res = await backendFetch('/api/v1/video/upload', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Upload failed');
      }

      const data = await res.json();
      const sid = data.session_id;
      setSessionId(sid);
      // One recording time belongs to one file. Clearing it means the next
      // upload cannot silently inherit this one's.
      setRecordedAt('');
      setPhase('processing');
      setStatusMsg('Upload complete — starting the detection model...');

      connectProcessingWS(sid);

    } catch (err) {
      setPhase('error');
      setStatusMsg(`Upload error: ${err.message}`);
    }
  }, [connectProcessingWS, cameraId, recordedAt]);

  // Start Live Webcam Stream
  const startLiveWebcam = useCallback(async () => {
    handleReset();
    // Captured AFTER handleReset (which bumps the epoch via stopWebcamStream),
    // so this attempt has its own id to check against once the awaits below
    // resolve — a second click or a Reset while getUserMedia/backendSocketUrl
    // is pending must not let this stream/socket land afterwards.
    const epoch = connectionEpochRef.current;
    setPhase('webcam');
    setIsWebcamActive(true);
    setStatusMsg('Requesting camera permission...');

    try {
      // 1. First try browser camera capture via getUserMedia
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 960 }, height: { ideal: 540 }, facingMode: 'user' }
      });
      if (connectionEpochRef.current !== epoch) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      localStreamRef.current = stream;

      if (hiddenVideoRef.current) {
        hiddenVideoRef.current.srcObject = stream;
        await hiddenVideoRef.current.play();
      }

      // Connect WebSocket to process_webcam_frame (Zero-Lag ACK Pipelining)
      const ws = new WebSocket(await backendSocketUrl(
        `/api/v1/video/process_webcam_frame?camera_id=${encodeURIComponent(cameraId)}`
      ));
      if (connectionEpochRef.current !== epoch) { ws.close(); return; }
      wsRef.current = ws;

      let isAwaitingResponse = false;

      const sendNextFrame = () => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || isAwaitingResponse) return;
        const videoEl = hiddenVideoRef.current;
        if (!videoEl || videoEl.paused || videoEl.ended || !videoEl.videoWidth) return;

        isAwaitingResponse = true;

        if (!offscreenCanvasRef.current) {
          offscreenCanvasRef.current = document.createElement('canvas');
        }
        const offCanvas = offscreenCanvasRef.current;
        offCanvas.width = 640; // 640x360 for high-speed transmission
        offCanvas.height = 360;
        const offCtx = offCanvas.getContext('2d');
        offCtx.drawImage(videoEl, 0, 0, 640, 360);

        const base64Img = offCanvas.toDataURL('image/jpeg', 0.65);
        wsRef.current.send(JSON.stringify({
          action: 'frame',
          image_base64: base64Img
        }));
      };

      ws.onopen = () => {
        setStatusMsg('Live camera active — detection running');
        isAwaitingResponse = false;
        sendNextFrame();
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'INIT') {
            setStatusMsg(msg.message);
            if (msg.device) setDeviceInfo(msg.device.toUpperCase());
          }
          if (msg.type === 'FRAME') {
            handleFrame(msg);
            isAwaitingResponse = false;
            // Instantly send next frame for 0ms lag real-time stream
            requestAnimationFrame(sendNextFrame);
          }
          if (msg.error) {
            setStatusMsg(msg.error);
            isAwaitingResponse = false;
          }
        } catch (_) {
          isAwaitingResponse = false;
        }
      };

      ws.onerror = () => {
        setStatusMsg('WebSocket connection error — using backend camera stream fallback...');
        // The browser's own camera stream is being abandoned for the backend's
        // direct capture — stop it here or the camera indicator stays lit and
        // the track keeps running for a stream nothing is reading from anymore.
        if (localStreamRef.current) {
          localStreamRef.current.getTracks().forEach((t) => t.stop());
          localStreamRef.current = null;
        }
        if (hiddenVideoRef.current) hiddenVideoRef.current.srcObject = null;
        startBackendDirectWebcamRef.current?.();
      };

      ws.onclose = () => {
        // Uses the updater form: `isWebcamActive` captured here would be the
        // value from the render that opened the socket, which is always false.
        setIsWebcamActive((active) => {
          if (active) setPhase('idle');
          return false;
        });
      };

    } catch (err) {
      console.warn("Browser camera access error or declined:", err);
      // Fallback: connect directly to backend /live_webcam
      startBackendDirectWebcamRef.current?.();
    }
  }, [handleFrame, handleReset, cameraId]);

  // Fallback: backend direct camera capture.
  // Reached from startLiveWebcam through startBackendDirectWebcamRef, so the
  // call sites above do not depend on this declaration order.
  const startBackendDirectWebcam = useCallback(async () => {
    if (wsRef.current) wsRef.current.close();
    const epoch = connectionEpochRef.current;

    const ws = new WebSocket(await backendSocketUrl(
      `/api/v1/video/live_webcam?camera_id=${encodeURIComponent(cameraId)}`
    ));
    if (connectionEpochRef.current !== epoch) { ws.close(); return; }
    wsRef.current = ws;

    ws.onopen = () => {
      setStatusMsg('Server camera active — detection running');
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'INIT') {
          setStatusMsg(msg.message);
          if (msg.device) setDeviceInfo(msg.device.toUpperCase());
        }
        if (msg.type === 'FRAME') { handleFrame(msg); }
        if (msg.error) {
          setPhase('error');
          setStatusMsg(msg.error);
        }
      } catch (_) {}
    };

    ws.onerror = () => {
      setPhase('error');
      setStatusMsg('Failed to open camera on backend or browser.');
    };
  }, [handleFrame, cameraId]);

  // Keep the ref pointing at the latest implementation so callers declared
  // earlier in the component can reach it.
  useEffect(() => {
    startBackendDirectWebcamRef.current = startBackendDirectWebcam;
  }, [startBackendDirectWebcam]);

  // Playback Control Actions
  const togglePlayPause = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const nextState = !isPaused;
    setIsPaused(nextState);
    wsRef.current.send(JSON.stringify({ action: nextState ? 'pause' : 'play' }));
  };

  const handleSeek = (e) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || isWebcamActive) return;
    const newPct = parseFloat(e.target.value);
    setProgress(newPct);
    if (durationSec > 0) {
      setCurrentTimeSec((newPct / 100) * durationSec);
    }
    wsRef.current.send(JSON.stringify({ action: 'seek', pct: newPct }));
  };

  const handleSkip = (secondsDelta) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || durationSec <= 0 || isWebcamActive) return;
    const targetTime = Math.max(0, Math.min(durationSec, currentTimeSec + secondsDelta));
    const targetPct = (targetTime / durationSec) * 100;
    setProgress(targetPct);
    setCurrentTimeSec(targetTime);
    wsRef.current.send(JSON.stringify({ action: 'seek', pct: targetPct }));
  };

  // The checkbox only controlled local drawing state — drawHUD never read it,
  // and the actual blur runs server-side (video_upload.py's privacy_state),
  // toggled only by this message. Without sending it the control did nothing:
  // frames stayed blurred (or not) regardless of what the user clicked.
  //
  // The local update is deliberately conditional on the send succeeding. Moving
  // the checkbox when no socket is open would claim a privacy state the server
  // never heard about, which is the one direction this control must never lie in.
  // Real time on/off. Remembered for the next upload as well as sent to the live
  // session, so the choice made before pressing Upload is the one that applies.
  const toggleRealtime = (checked) => {
    setRealtime(checked);
    realtimeRef.current = checked;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'set_realtime', enabled: checked }));
    }
  };

  const toggleBlur = (checked) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ action: 'set_privacy_blur', enabled: checked }));
    pendingBlurRef.current = checked;
    setEnableBlur(checked);
  };

  // Fullscreen targets the CANVAS WRAPPER, not the whole panel: expanding the
  // panel fills the screen with upload buttons and stat pills around a video
  // that is still the same size, which is not what anyone means by fullscreen.
  const toggleFullScreen = () => {
    const el = stageRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      el.requestFullscreen?.().catch(() => {});
    }
  };

  // The browser also exits fullscreen on Escape and on the F11 key, neither of
  // which routes through the button — so the flag has to follow the DOM rather
  // than the click, or the max-height cap stays lifted after leaving.
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  // Initial canvas grid render
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#090D16';
    ctx.fillRect(0, 0, 960, 540);
    ctx.strokeStyle = 'rgba(51,65,85,0.3)';
    ctx.lineWidth = 1;
    for (let x = 0; x < 960; x += 80) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 540); ctx.stroke();
    }
    for (let y = 0; y < 540; y += 80) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(960, y); ctx.stroke();
    }
    ctx.fillStyle = '#334155';
    ctx.font = '600 16px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Turn on Camera or Upload video to start monitoring', 480, 270);
    ctx.textAlign = 'left';
  }, []);

  // Cleanup webcam stream on unmount
  useEffect(() => {
    return () => {
      stopWebcamStream();
    };
  }, [stopWebcamStream]);

  const PhaseIcon = phase === 'uploading' || phase === 'processing'
    ? <Loader2 className="w-3.5 h-3.5 animate-spin text-accent" />
    : phase === 'webcam'
    ? <span className="w-2.5 h-2.5 rounded-full bg-accent animate-ping" />
    : phase === 'done'
    ? <CheckCircle2 className="w-3.5 h-3.5 text-accent" />
    : phase === 'error'
    ? <AlertCircle className="w-3.5 h-3.5 text-accent" />
    : <Video className="w-3.5 h-3.5 text-ink-muted" />;

  return (
    <div ref={containerRef} className="glass-panel p-4 flex flex-col gap-4 relative group">
      {/* Hidden HTML5 Video element for webcam capture */}
      <video ref={hiddenVideoRef} className="hidden" playsInline muted />

      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-ink font-semibold text-sm">
          <Eye className="w-4 h-4 text-accent" />
          <span>Live Camera Feed — Detection View</span>
          <span className="ml-2 px-2 py-0.5 rounded bg-accent/10 border border-line text-[10px] text-accent font-mono font-bold flex items-center gap-1">
            <Cpu className="w-3 h-3" />
            <span>YOLOv8m pose</span>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {readOnly && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-line text-[10px] font-bold text-ink-faint">
              <Eye className="w-3 h-3" />
              VIEW ONLY
            </span>
          )}
          {/* Turn On Camera Button */}
          {readOnly ? null : isWebcamActive ? (
            <button
              onClick={stopWebcamStream}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent/20 border border-[color:var(--accent)] text-accent text-xs font-bold transition-all hover:bg-accent/30"
            >
              <CameraOff className="w-3.5 h-3.5" />
              <span>Stop Camera</span>
            </button>
          ) : (
            <button
              onClick={startLiveWebcam}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent/10 hover:bg-accent/20 border border-line text-accent text-xs font-bold transition-all shadow-lg shadow-red-600/10"
            >
              <Camera className="w-3.5 h-3.5 animate-pulse" />
              <span>Turn On Camera</span>
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept="video/mp4,video/webm,video/avi,video/quicktime,.mkv"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            onClick={handleReset}
            className="p-1.5 rounded-lg bg-surface-alt hover:bg-surface-alt text-ink-muted transition-all"
            title="Reset Player"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          {!readOnly && (
          <label
            className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint"
            title="When this footage was recorded. Safety events are stamped with it. Leave empty to use the upload time."
          >
            <span>Recorded</span>
            <input
              type="datetime-local"
              value={recordedAt}
              max={localNowValue()}
              onChange={(e) => setRecordedAt(e.target.value)}
              disabled={phase === 'uploading' || isWebcamActive}
              className="rounded-lg bg-ground border border-field hover:border-field-hover px-2 py-1 text-[11px] normal-case tracking-normal font-sans text-ink [color-scheme:light] dark:[color-scheme:dark] focus:outline-none focus:border-[color:var(--accent)] disabled:opacity-40 transition-colors"
            />
          </label>
          )}
          {!readOnly && (
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={phase === 'uploading' || isWebcamActive}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-accent-soft hover:bg-accent/20 border border-line text-accent text-xs font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Upload className="w-3.5 h-3.5" />
            <span>{phase === 'uploading' ? 'Uploading...' : 'Upload Video'}</span>
          </button>
          )}
        </div>
      </div>

      {/* Phone as a camera.
          
          A laptop webcam points at your face from 40cm; a CCTV camera looks
          down at a room from a corner. A phone propped on a shelf is the only
          way to produce the second view without buying hardware, and a worker
          seen from a corner is exactly what the zone rules are written for. */}
      {!readOnly && (
        <PhoneCameraPanel
          cameraId={cameraId}
          disabled={isWebcamActive}
          activeZones={activeZones}
          onFrame={handleFrame}
        />
      )}

      {/* Status Bar */}
      {(fileName || statusMsg || isWebcamActive) && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-alt/80 border border-line/60 text-xs text-ink-muted font-mono">
          {PhaseIcon}
          <span className="truncate">
            {isWebcamActive ? `🔴 LIVE WEBCAM FEED — ${statusMsg}` : (statusMsg || fileName)}
          </span>
          {phase === 'processing' && !isWebcamActive && (
            <span className="ml-auto text-accent shrink-0 font-bold">{progress.toFixed(1)}%</span>
          )}
        </div>
      )}

      {/* Main Canvas Player Display */}
      <div
        ref={stageRef}
        className={`relative overflow-hidden bg-ground group/canvas ${
          isFullscreen
            ? 'flex items-center justify-center w-screen h-screen'
            : 'rounded-xl border border-line shadow-2xl'
        }`}
      >
        {/*
          CAP THE HEIGHT, NOT JUST THE WIDTH.

          `w-full h-auto` alone is fine for 16:9 laptop video, but a phone held
          upright sends a PORTRAIT frame — the canvas takes its intrinsic size
          from whatever arrives, so at full width that renders taller than the
          screen and pushes every control below the fold.

          object-contain keeps the whole frame visible and letterboxes the
          remainder, rather than cropping people out of the edges of a feed
          whose whole purpose is watching them.
        */}
        <canvas
          ref={canvasRef}
          width={960}
          height={540}
          className={
            isFullscreen
              // The cap exists to stop a tall frame overflowing the PAGE. In
              // fullscreen there is no page to overflow, and keeping it would
              // pin the video to 70% of the screen — the one place the user
              // explicitly asked for all of it.
              ? 'max-w-full max-h-full w-auto h-auto object-contain block'
              : 'w-full h-auto max-h-[70vh] object-contain block mx-auto'
          }
        />

        {/*
          Fullscreen, on the video itself — where every video player puts it.
          There is a second one in the controls bar below, but that row is
          about scrubbing a FILE and is disabled during a live stream, so it
          reads as unavailable exactly when a live feed most wants expanding.

          Always visible rather than hover-only: a control you cannot see is
          one you do not know exists, and this one had gone unnoticed.
        */}
        <button
          type="button"
          onClick={toggleFullScreen}
          title={isFullscreen ? 'Exit full screen (Esc)' : 'Full screen'}
          aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'}
          className="absolute top-3 right-3 z-20 p-2 rounded-lg bg-black/55 hover:bg-black/75 text-white backdrop-blur-sm transition-colors"
        >
          {isFullscreen
            ? <Minimize2 className="w-4 h-4" />
            : <Maximize2 className="w-4 h-4" />}
        </button>

        {/* Live Camera Badge Overlay */}
        {isWebcamActive && (
          <div className="absolute top-3 left-3 px-3 py-1 rounded-full bg-accent/90 text-white font-bold text-xs font-mono flex items-center gap-1.5 shadow-lg backdrop-blur-sm animate-pulse">
            <span className="w-2 h-2 rounded-full bg-white animate-ping" />
            <span>LIVE WEBCAM AI</span>
          </div>
        )}

        {/* Play/Pause Center Canvas Click Overlay (for video files) */}
        {phase === 'processing' && !isWebcamActive && (
          <div
            onClick={togglePlayPause}
            className={`absolute inset-0 bg-ground/30 backdrop-blur-[2px] flex items-center justify-center cursor-pointer transition-opacity duration-200 ${
              isPaused ? 'opacity-100' : 'opacity-0 hover:opacity-100'
            }`}
          >
            <div className="p-4 rounded-full bg-surface/90 border border-[color:var(--accent)] text-accent shadow-2xl transform hover:scale-110 transition-transform">
              {isPaused ? <Play className="w-10 h-10 fill-current ml-1" /> : <Pause className="w-10 h-10 fill-current" />}
            </div>
          </div>
        )}

        {/* A new alert: a banner over the picture, silent, cleared after a few seconds */}
        <AlertBanners
          banners={banners}
          zones={activeZones}
          flaggedCount={openCount}
          onDismiss={(id) => setBanners((list) => list.filter((b) => b.id !== id))}
          onHold={(held) => {
            bannersHeldRef.current = held;
            // Leaving the banners gives each a fresh few seconds rather than clearing them at once.
            if (!held) setBanners((list) => list.map((b) => ({ ...b, raisedAt: Date.now() })));
          }}
        />
      </div>

      {/* Movie-Style Video Playback Controls Bar */}
      <div className="glass-card p-3 flex flex-col gap-2.5 bg-surface/90 border-line">
        {/* Interactive Timeline Scrubber Slider (disabled during live webcam) */}
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono text-ink-muted shrink-0 w-12 text-right">
            {formatTime(currentTimeSec)}
          </span>
          <div className="relative flex-1 flex items-center">
            <input
              type="range"
              min="0"
              max="100"
              step="0.1"
              value={progress}
              onChange={handleSeek}
              aria-label="Seek the video"
              aria-valuetext={`${formatTime(currentTimeSec)} of ${formatTime(durationSec)}`}
              disabled={isWebcamActive || (phase !== 'processing' && phase !== 'done')}
              className="w-full h-2 bg-surface-alt rounded-lg appearance-none cursor-pointer accent-[color:var(--accent)] hover:accent-[color:var(--accent)] transition-all disabled:cursor-not-allowed disabled:opacity-30"
            />
          </div>
          <span className="text-[11px] font-mono text-ink-muted shrink-0 w-12">
            {isWebcamActive ? 'LIVE' : formatTime(durationSec)}
          </span>
        </div>

        {/* Playback Control Buttons Bar */}
        <div className="flex flex-wrap items-center justify-between gap-y-2">
          <div className="flex items-center gap-2">
            {/* Rewind 5s */}
            <button
              onClick={() => handleSkip(-5)}
              disabled={isWebcamActive || phase !== 'processing'}
              className="p-2 rounded-lg bg-surface-alt/80 hover:bg-surface-alt text-ink-muted disabled:opacity-30 transition-all"
              title="Rewind 5 Seconds"
            >
              <RotateCcw className="w-4 h-4" />
            </button>

            {/* Main Play / Pause Button */}
            <button
              onClick={togglePlayPause}
              disabled={isWebcamActive || phase !== 'processing'}
              className="p-2.5 rounded-xl bg-accent hover:brightness-110 text-white font-bold disabled:opacity-30 shadow-lg shadow-red-600/20 transition-all"
              title={isPaused ? "Play" : "Pause"}
            >
              {isPaused ? <Play className="w-5 h-5 fill-current ml-0.5" /> : <Pause className="w-5 h-5 fill-current" />}
            </button>

            {/* Skip 5s */}
            <button
              onClick={() => handleSkip(5)}
              disabled={isWebcamActive || phase !== 'processing'}
              className="p-2 rounded-lg bg-surface-alt/80 hover:bg-surface-alt text-ink-muted disabled:opacity-30 transition-all"
              title="Skip 5 Seconds Forward"
            >
              <RotateCw className="w-4 h-4" />
            </button>

            <span className="text-xs text-ink-muted font-mono ml-2">
              {isWebcamActive ? '🔴 WEBCAM LIVE' : isPaused ? 'PAUSED' : phase === 'processing' ? 'PLAYING' : 'IDLE'}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {/* Privacy Anonymization Toggle.
                `enableBlur === null` means no frame has arrived yet, so the
                server's setting is still unknown. The control renders unchecked
                and disabled rather than guessing — `checked={null}` would also
                make React treat the input as uncontrolled. */}
            <label
              className={`flex items-center gap-2 select-none text-xs text-ink-muted ${
                enableBlur === null ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
              }`}
              title={enableBlur === null
                ? 'Waiting for the analysis stream to report its privacy setting'
                : 'Blur head regions server-side before frames are sent'}
            >
              <Shield className={`w-4 h-4 ${enableBlur ? 'text-accent' : 'text-ink-faint'}`} />
              <span>Face Anonymisation</span>
              <input
                type="checkbox"
                checked={enableBlur === true}
                disabled={enableBlur === null}
                onChange={(e) => toggleBlur(e.target.checked)}
                className="accent-[color:var(--accent)] cursor-pointer disabled:cursor-not-allowed"
              />
            </label>

            {/* Real time: an uploaded video plays at the speed it was filmed, so
                labels can be read; off processes it as fast as the machine can. */}
            <label
              className="flex items-center gap-2 select-none text-xs text-ink-muted cursor-pointer"
              title="Play an uploaded video at its own speed. Switch off to process it as fast as possible"
            >
              <span>Real time</span>
              <input
                type="checkbox"
                checked={realtime}
                onChange={(e) => toggleRealtime(e.target.checked)}
                className="accent-[color:var(--accent)] cursor-pointer"
              />
            </label>

            {/* Fullscreen Button */}
            <button
              onClick={toggleFullScreen}
              className="p-2 rounded-lg bg-surface-alt hover:bg-surface-alt text-ink-muted transition-all"
              title="Toggle Fullscreen"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Zone entries and exits, once the safety layer is running */}
      {safetyOn && (phase === 'processing' || phase === 'done' || isWebcamActive) && (
        <>
          <DecisionFeed decisions={decisions} zones={activeZones} />
          <ZoneActivity events={zoneEvents} zones={activeZones} />
        </>
      )}
      {safetyOn && (phase === 'processing' || isWebcamActive) && (
        <PpeStatus entities={ppeEntities} unavailable={ppeUnavailable} />
      )}

      {/* What is in view right now: how many people, and how many of them have an open flag. */}
      {(phase === 'processing' || isWebcamActive) && (
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'People in view', value: stats.detected, color: 'text-ink' },
            { label: 'With an open flag', value: stats.flagged, color: stats.flagged > 0 ? 'text-accent' : 'text-ink' },
          ].map(({ label, value, color }) => (
            <div key={label} className="glass-card p-2.5 text-center">
              <p className={`text-xl font-bold ${color}`}>{value}</p>
              <p className="text-[10px] text-ink-faint uppercase tracking-wider">{label}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
