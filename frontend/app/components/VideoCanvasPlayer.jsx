'use client';

// frontend/app/components/VideoCanvasPlayer.jsx
import React, { useRef, useEffect, useState, useCallback } from 'react';
import {
  Eye, Upload, Play, Pause, RefreshCw, Shield,
  Sliders, CheckCircle2, Loader2, AlertCircle, Video,
  RotateCcw, RotateCw, Cpu, Maximize2, Camera, CameraOff
} from 'lucide-react';

// Backend origin and the token-attaching helpers. Every processing socket
// carries the Supabase access token so the telemetry it produces is attributed
// to the caller's organisation — see app/lib/backend.js.
import { backendFetch, backendSocketUrl } from '../lib/backend';

/* Detection-box colours, kept inside the red/black/white palette. The three
   postures are separated by weight along one red ramp rather than by unrelated
   hues, so the overlay belongs to the same product as everything under it.
   UNKNOWN stays neutral so an unclassified box never looks like a state. */
const POSTURE_COLORS = {
  SITTING:  '#DC2626',
  STANDING: '#F0736F',
  WALKING:  '#F7B4B0',
  UNKNOWN:  '#8B8792',
};

function formatTime(seconds) {
  if (!seconds || isNaN(seconds) || seconds < 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function drawHUD(ctx, canvas, entities, zones, enableBlur) {
  // Only draw person annotations (Bounding boxes, Track IDs, Posture, Activity Scores, Face Blur)
  entities.forEach((entity) => {
    const [x1, y1, x2, y2] = entity.bbox;
    const w = x2 - x1;
    const h = y2 - y1;
    const color = POSTURE_COLORS[entity.posture] || POSTURE_COLORS.UNKNOWN;

    // Main box
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.strokeRect(x1, y1, w, h);

    // Corner reticles
    const r = 12;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x1, y1 + r); ctx.lineTo(x1, y1); ctx.lineTo(x1 + r, y1);
    ctx.moveTo(x2 - r, y1); ctx.lineTo(x2, y1); ctx.lineTo(x2, y1 + r);
    ctx.moveTo(x1, y2 - r); ctx.lineTo(x1, y2); ctx.lineTo(x1 + r, y2);
    ctx.moveTo(x2 - r, y2); ctx.lineTo(x2, y2); ctx.lineTo(x2, y2 - r);
    ctx.stroke();

    // Tag banner (top)
    const bannerW = Math.max(180, w);
    ctx.fillStyle = 'rgba(11,10,12,0.88)';
    ctx.fillRect(x1, y1 - 28, bannerW, 24);
    ctx.fillStyle = '#FFFFFF';
    ctx.font = '600 11px Inter, sans-serif';
    ctx.fillText(`ID #${entity.track_id}  ${entity.posture}`, x1 + 6, y1 - 12);

    // Score pill
    ctx.fillStyle = color;
    ctx.fillRect(x1 + bannerW - 44, y1 - 24, 40, 16);
    ctx.fillStyle = '#FFFFFF';
    ctx.font = '700 10px Inter, sans-serif';
    ctx.fillText(`${Math.round(entity.activity_score)}`, x1 + bannerW - 35, y1 - 12);
  });
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
  const [sessionId, setSessionId] = useState('');
  const [progress, setProgress] = useState(0);
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [durationSec, setDurationSec] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [enableBlur, setEnableBlur] = useState(true);
  const [deviceInfo, setDeviceInfo] = useState('GPU (CUDA)');
  const [isWebcamActive, setIsWebcamActive] = useState(false);
  const [stats, setStats] = useState({ detected: 0, sitting: 0, standing: 0, walking: 0 });

  const handleFrame = useCallback((data) => {
    if (!data.frame_base64) return;
    if (data.progress_pct !== undefined) setProgress(data.progress_pct);
    if (data.current_time_seconds !== undefined) setCurrentTimeSec(data.current_time_seconds);
    if (data.duration_seconds !== undefined) setDurationSec(data.duration_seconds);
    if (data.is_paused !== undefined) setIsPaused(data.is_paused);

    const entities = data.tracked_entities || [];
    const newStats = { detected: entities.length, sitting: 0, standing: 0, walking: 0 };
    entities.forEach((e) => {
      if (e.posture === 'SITTING')  newStats.sitting++;
      if (e.posture === 'STANDING') newStats.standing++;
      if (e.posture === 'WALKING')  newStats.walking++;
    });
    setStats(newStats);

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
  }, [activeZones, enableBlur, onFrameData]);

  // Connect WebSocket to video file processing session
  const connectProcessingWS = useCallback(async (sid) => {
    if (wsRef.current) wsRef.current.close();

    const epoch = ++connectionEpochRef.current;
    const ws = new WebSocket(await backendSocketUrl(`/api/v1/video/process/${sid}`));
    // A Reset or a second connect attempt during the await above must win —
    // otherwise this socket overwrites wsRef.current with a connection nothing
    // will ever close.
    if (connectionEpochRef.current !== epoch) { ws.close(); return; }
    wsRef.current = ws;

    ws.onopen  = () => setStatusMsg('Connected — AI pipeline running on RTX 4060 GPU...');
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
    setStats({ detected: 0, sitting: 0, standing: 0, walking: 0 });
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
      setPhase('processing');
      setStatusMsg('Upload complete — initialising YOLOv8m (Medium) AI model on RTX 4060 GPU...');

      connectProcessingWS(sid);

    } catch (err) {
      setPhase('error');
      setStatusMsg(`Upload error: ${err.message}`);
    }
  }, [connectProcessingWS, cameraId]);

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
    setStatusMsg('Requesting camera permission & initializing RTX 4060 GPU...');

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
        setStatusMsg('Live Camera Active — Real-Time RTX 4060 GPU AI Stream (0ms Lag)');
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
      setStatusMsg('Live Backend Camera Active — RTX 4060 GPU AI Stream');
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
  const toggleBlur = (checked) => {
    setEnableBlur(checked);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'set_privacy_blur', enabled: checked }));
    }
  };

  const toggleFullScreen = () => {
    if (!containerRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      containerRef.current.requestFullscreen();
    }
  };

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
    ctx.fillText('Turn on Camera or Upload video for GPU AI analytics', 480, 270);
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
          <span>CCTV Video AI Analytics — HUD Feed</span>
          <span className="ml-2 px-2 py-0.5 rounded bg-accent/10 border border-line text-[10px] text-accent font-mono font-bold flex items-center gap-1">
            <Cpu className="w-3 h-3" />
            <span>NVIDIA RTX 4060 (CUDA) • YOLOv8m Medium</span>
          </span>
        </div>

        <div className="flex items-center gap-2">
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
      <div className="relative rounded-xl overflow-hidden border border-line bg-ground shadow-2xl group/canvas">
        <canvas ref={canvasRef} width={960} height={540} className="w-full h-auto block" />

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
              disabled={isWebcamActive || (phase !== 'processing' && phase !== 'done')}
              className="w-full h-2 bg-surface-alt rounded-lg appearance-none cursor-pointer accent-[color:var(--accent)] hover:accent-[color:var(--accent)] transition-all disabled:cursor-not-allowed disabled:opacity-30"
            />
          </div>
          <span className="text-[11px] font-mono text-ink-muted shrink-0 w-12">
            {isWebcamActive ? 'LIVE' : formatTime(durationSec)}
          </span>
        </div>

        {/* Playback Control Buttons Bar */}
        <div className="flex items-center justify-between">
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
              {isWebcamActive ? '🔴 WEBCAM LIVE (GPU AI)' : isPaused ? 'PAUSED' : phase === 'processing' ? 'PLAYING (GPU AI)' : 'IDLE'}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* Privacy Anonymization Toggle */}
            <label className="flex items-center gap-2 cursor-pointer select-none text-xs text-ink-muted">
              <Shield className={`w-4 h-4 ${enableBlur ? 'text-accent' : 'text-ink-faint'}`} />
              <span>Face Anonymisation</span>
              <input
                type="checkbox"
                checked={enableBlur}
                onChange={(e) => toggleBlur(e.target.checked)}
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

      {/* Real-time Detection Statistics Pills */}
      {(phase === 'processing' || isWebcamActive) && (
        <div className="grid grid-cols-4 gap-2">
          {[
            { label: 'Detected', value: stats.detected, color: 'text-ink' },
            { label: 'Sitting',  value: stats.sitting,  color: 'text-accent' },
            { label: 'Standing', value: stats.standing, color: 'text-accent' },
            { label: 'Walking',  value: stats.walking,  color: 'text-accent' },
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
