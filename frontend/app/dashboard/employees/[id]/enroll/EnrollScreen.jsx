'use client';

// frontend/app/dashboard/employees/[id]/enroll/EnrollScreen.jsx
//
// Face enrolment: 3-5 photos per person, graded before they are stored.
//
// WHY THE REJECTIONS ARE THE INTERFACE
//
// The plan is blunt that a bad enrolment "poisons every later match" and makes
// that employee "unmatchable all project long". The failure is silent — the
// photo saves, the row looks fine, and six weeks later that one person is the
// only one the door camera never recognises, which reads as a model problem
// rather than a data problem.
//
// So this screen is built around the refusal, not the success. Every rejection
// shows the measurement that failed and what to do about it, because "poor
// quality, try again" leaves someone taking the same bad photo five times.
//
// WHAT NEVER LEAVES THE BROWSER
//
// The photo is posted to the backend, graded, converted to a 512-d vector, and
// discarded. It is never written to disk, never stored, and never returned.
// The preview thumbnails here live in memory for the length of the page visit
// and are revoked on unmount. Migration 020 revokes read access to the
// embedding column from every browser session, so even an admin cannot read
// back what was stored.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ScanFace, Upload, Camera, Trash2, Check, Loader2, AlertTriangle,
  ArrowLeft, ShieldCheck, X,
} from 'lucide-react';

import DashboardShell from '../../../DashboardShell';
import { Banner } from '../../../../components/AuthFormBits';
import { can, denialMessage } from '../../../../lib/permissions';
import { backendFetch } from '../../../../lib/backend';

/** What a good enrolment looks like, shown before anyone takes a photo. */
const GUIDANCE = [
  'Face the camera straight on, with your whole head in frame',
  'Light in front of you, not behind — a window at your back is the usual culprit',
  'Hold still: a blurry photo is the one failure that is invisible later',
  'Only you in the shot',
  'Vary it slightly between photos — a small turn of the head, not five identical frames',
];

function QualityPill({ quality }) {
  // 0.8 is the plan's bar. Anything under it is stored but flagged, because a
  // marginal template is worse than a missing one and the person enrolling
  // should be able to see which photo is dragging the set down.
  const good = quality >= 0.8;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold ${
        good
          ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
          : 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400'
      }`}
      title={good ? 'Good enough to match against' : 'Below the 0.8 bar — consider retaking'}
    >
      {good ? <Check className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
      {quality.toFixed(2)}
    </span>
  );
}

/**
 * A rejection, with the numbers behind it.
 *
 * Showing the measurement is the difference between a user who fixes the photo
 * and a user who retakes the same one. "28 px between the eyes, move closer"
 * is actionable; "low quality" is not.
 */
function RejectionNotice({ rejection, onDismiss }) {
  const m = rejection.measurements || {};
  const facts = [
    m.det_score !== undefined && `detector confidence ${(m.det_score * 100).toFixed(0)}%`,
    m.eye_distance_px !== undefined && `${m.eye_distance_px.toFixed(0)} px between the eyes`,
    m.sharpness !== undefined && `sharpness ${m.sharpness.toFixed(0)}`,
    m.face_width_px !== undefined && `face ${m.face_width_px}×${m.face_height_px} px`,
  ].filter(Boolean);

  return (
    <div role="alert" className="rounded-xl border border-[color:var(--accent)] bg-accent-soft p-4">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="w-4 h-4 text-accent shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-bold text-accent leading-relaxed">
            {rejection.message}
          </p>
          {facts.length > 0 && (
            <p className="mt-1.5 font-mono text-[11px] text-ink-muted">
              {facts.join(' · ')}
            </p>
          )}
        </div>
        <button
          type="button" onClick={onDismiss} aria-label="Dismiss"
          className="p-1 rounded text-ink-muted hover:text-ink shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

export default function EnrollScreen({ orgName, initialRole, employee, viewer }) {
  const [templates, setTemplates] = useState([]);
  const [limits, setLimits] = useState({ min: 3, max: 5 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState(null);
  const [rejection, setRejection] = useState(null);
  const [camera, setCamera] = useState(false);

  const fileRef = useRef(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const role = initialRole;
  const canEdit = can(role, 'employees.edit');
  const full = templates.length >= limits.max;

  const refresh = useCallback(async () => {
    try {
      const res = await backendFetch(`/api/v1/employees/${employee.id}/templates`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setBanner({ kind: 'error', text: describe(body, res.status) });
        setLoading(false);
        return;
      }
      const data = await res.json();
      setTemplates(data.templates || []);
      setLimits({ min: data.min_templates ?? 3, max: data.max_templates ?? 5 });
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not reach the analysis backend. Is it running on port 8001?',
      });
    } finally {
      setLoading(false);
    }
  }, [employee.id]);

  useEffect(() => { refresh(); }, [refresh]);

  // Releasing the camera on unmount is not optional: a live webcam left running
  // after the page closes is a hardware light staying on for no reason.
  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    setCamera(false);
  }, []);

  useEffect(() => stopCamera, [stopCamera]);

  const startCamera = async () => {
    setRejection(null);
    setBanner(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
      });
      streamRef.current = stream;
      setCamera(true);
      // The <video> only exists once `camera` is true, so attaching has to wait
      // for the render that creates it.
      requestAnimationFrame(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {});
        }
      });
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not open the camera. Allow camera access for this site, or upload a photo instead.',
      });
    }
  };

  const send = async (body) => {
    setBusy(true);
    setRejection(null);
    setBanner(null);
    try {
      const res = await backendFetch(`/api/v1/employees/${employee.id}/templates`, {
        method: 'POST',
        body,
      });
      const data = await res.json().catch(() => ({}));

      if (res.status === 422) {
        // A graded rejection: the request was fine, the photo was not.
        const d = data.detail || {};
        setRejection({
          message: d.message || 'That photo could not be used.',
          measurements: d.measurements,
        });
        return false;
      }
      if (!res.ok) {
        setBanner({ kind: 'error', text: describe(data, res.status) });
        return false;
      }

      setBanner({
        kind: 'success',
        text: `Photo ${data.count} of ${data.max_templates} enrolled — quality ${Number(data.quality).toFixed(2)}.`,
      });
      await refresh();
      return true;
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not reach the analysis backend. Is it running on port 8001?',
      });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = '';   // allow re-picking the same file
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    await send(body);
  };

  const capture = async () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    // JPEG at 0.92: high enough that the sharpness gate is measuring the photo
    // rather than the compression.
    const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
    const body = new FormData();
    body.append('image_base64', dataUrl);
    await send(body);
  };

  const remove = async (templateId) => {
    setBusy(true);
    try {
      const res = await backendFetch(
        `/api/v1/employees/${employee.id}/templates/${templateId}`,
        { method: 'DELETE' });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setBanner({ kind: 'error', text: describe(d, res.status) });
        return;
      }
      setBanner({ kind: 'success', text: 'Photo removed.' });
      await refresh();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the analysis backend.' });
    } finally {
      setBusy(false);
    }
  };

  const enrolled = templates.length >= limits.min;
  const avg = templates.length
    ? templates.reduce((s, t) => s + t.quality, 0) / templates.length
    : 0;

  return (
    <DashboardShell user={viewer} role={role}>
      <div className="mx-auto max-w-3xl">

        <Link
          href="/dashboard/employees"
          className="inline-flex items-center gap-1.5 text-[13px] font-bold text-ink-muted hover:text-ink mb-4 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Employees
        </Link>

        <header className="flex flex-col gap-2 mb-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName} · {employee.employeeCode}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Enrol {employee.displayName}
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            {limits.min}–{limits.max} photos, so the door camera can recognise them by
            face. Each one is graded before it is stored.
          </p>
        </header>

        {/* The privacy statement belongs where the photo is taken, not in a
            policy page nobody reads. */}
        <div className="mb-5 rounded-xl border border-line bg-surface-alt px-4 py-3 flex items-start gap-2.5">
          <ShieldCheck className="w-4 h-4 text-accent shrink-0 mt-0.5" />
          <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
            <span className="font-bold text-ink">No photograph is kept.</span>{' '}
            Each image is converted to a numeric signature and discarded — the picture
            is never written to disk, and the signature cannot be turned back into a
            face. Even an administrator cannot read it back.
          </p>
        </div>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}
        {rejection && (
          <div className="mb-5">
            <RejectionNotice rejection={rejection} onDismiss={() => setRejection(null)} />
          </div>
        )}

        {!canEdit && role && (
          <div className="mb-5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <p className="text-[13px] text-ink-muted font-medium leading-relaxed">
              {denialMessage('employees.edit')} You can see how many photos are enrolled,
              but only an administrator or manager can add or remove them.
            </p>
          </div>
        )}

        {/* Progress */}
        <section className="mb-6 rounded-xl border border-line bg-surface p-4 sm:p-5">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
              <ScanFace className="w-4 h-4 text-accent" />
              {loading ? 'Loading…' : `${templates.length} of ${limits.max} photos`}
            </h2>
            {!loading && templates.length > 0 && (
              <span className="font-mono text-[11px] text-ink-faint">
                average quality {avg.toFixed(2)}
              </span>
            )}
          </div>

          <div className="flex gap-1.5 mb-4" aria-hidden="true">
            {Array.from({ length: limits.max }).map((_, i) => (
              <div
                key={i}
                className={`h-1.5 flex-1 rounded-full ${
                  i < templates.length
                    ? 'bg-[color:var(--accent)]'
                    : i < limits.min ? 'bg-surface-alt border border-line' : 'bg-surface-alt'
                }`}
              />
            ))}
          </div>

          <p className="text-[12.5px] font-medium leading-relaxed mb-4">
            {loading ? (
              <span className="text-ink-faint">Checking what is already enrolled…</span>
            ) : enrolled ? (
              <span className="text-emerald-600 dark:text-emerald-400">
                Enrolled. {employee.displayName} can be recognised at a door camera.
                {templates.length < limits.max
                  ? ` Adding up to ${limits.max - templates.length} more would make it more reliable.`
                  : ''}
              </span>
            ) : (
              <span className="text-ink-muted">
                {limits.min - templates.length} more photo
                {limits.min - templates.length === 1 ? '' : 's'} needed before this
                person can be recognised.
              </span>
            )}
          </p>

          {loading ? (
            <div className="py-6 flex justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
            </div>
          ) : templates.length === 0 ? (
            <p className="text-[13px] text-ink-faint font-medium">No photos yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {templates.map((t, i) => (
                <li key={t.id}
                    className="flex items-center gap-3 rounded-lg border border-line bg-ground px-3 py-2">
                  <span className="font-mono text-[11px] text-ink-faint w-6">#{i + 1}</span>
                  <QualityPill quality={t.quality} />
                  <span className="text-[12px] text-ink-muted flex-1 truncate">
                    {formatDate(t.created_at)}
                  </span>
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => remove(t.id)}
                      disabled={busy}
                      title="Remove this photo"
                      aria-label={`Remove photo ${i + 1}`}
                      className="p-1.5 rounded-lg text-ink-muted hover:text-accent hover:bg-accent-soft transition-colors disabled:opacity-60"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Capture */}
        {canEdit && (
          <section className="rounded-xl border border-line bg-surface p-4 sm:p-5">
            <h2 className="text-[15px] font-black tracking-tight text-ink mb-1">
              Add a photo
            </h2>
            <ul className="text-[12.5px] text-ink-muted font-medium leading-relaxed mb-4 space-y-0.5">
              {GUIDANCE.map((g) => (
                <li key={g} className="flex gap-1.5">
                  <span className="text-ink-faint">·</span>{g}
                </li>
              ))}
            </ul>

            {full ? (
              <p className="text-[13px] font-bold text-ink-muted">
                All {limits.max} photos are enrolled. Remove one to add another.
              </p>
            ) : camera ? (
              <div className="flex flex-col gap-3">
                <div className="relative rounded-lg overflow-hidden bg-ground border border-line">
                  {/* Mirrored, because an un-mirrored self-view is disorienting
                      to aim. The CAPTURE is not mirrored — the canvas draws the
                      raw frame, so the stored signature matches reality. */}
                  <video
                    ref={videoRef} playsInline muted
                    className="w-full max-h-[380px] object-contain scale-x-[-1]"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button" onClick={capture} disabled={busy}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] disabled:opacity-60"
                  >
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <Camera className="w-4 h-4" />}
                    Capture
                  </button>
                  <button
                    type="button" onClick={stopCamera} disabled={busy}
                    className="px-4 py-2.5 rounded-lg border-2 border-field text-ink font-bold text-[14px] hover:border-field-hover transition-colors disabled:opacity-60"
                  >
                    Stop camera
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button" onClick={startCamera} disabled={busy}
                  className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] disabled:opacity-60"
                >
                  <Camera className="w-4 h-4" />
                  Use the camera
                </button>
                <button
                  type="button" onClick={() => fileRef.current?.click()} disabled={busy}
                  className="flex items-center gap-2 px-4 py-2.5 rounded-lg border-2 border-field text-ink font-bold text-[14px] hover:border-field-hover transition-colors disabled:opacity-60"
                >
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" />
                        : <Upload className="w-4 h-4" />}
                  Upload a photo
                </button>
                <input
                  ref={fileRef} type="file" accept="image/*"
                  onChange={onFile} className="hidden"
                />
              </div>
            )}
          </section>
        )}

        <p className="mt-6 text-[12px] text-ink-faint font-medium leading-relaxed">
          Face matching runs only at a door camera, where somebody is close enough for it
          to work. Everywhere else people are matched by appearance and by which desk
          they sit at — no face data is used.
        </p>
      </div>
    </DashboardShell>
  );
}

/** Absolute date. Relative times go stale in an open tab. */
function formatDate(value) {
  if (!value) return '';
  try {
    return new Date(value).toLocaleString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return '';
  }
}

/**
 * A backend error, as a sentence.
 *
 * FastAPI puts the message in `detail`, which is sometimes a string and
 * sometimes the structured object the 422 path returns. Both are handled here
 * so a shape change upstream cannot render "[object Object]" at somebody.
 */
function describe(body, status) {
  const d = body?.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && d.message) return d.message;
  if (status === 401) return 'Your session has expired. Sign in again.';
  if (status === 403) return 'Only an administrator or manager can enrol a face.';
  if (status === 404) return 'That employee no longer exists.';
  if (status === 503) return 'Face enrolment is not configured on this deployment yet.';
  return 'Something went wrong. Please try again.';
}
