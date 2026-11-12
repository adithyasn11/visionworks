'use client';

// frontend/app/dashboard/CamerasScreen.jsx
//
// The organisation's cameras: list, add, remove. A camera has to exist here before anything a run decides can
// reach Review, Events, Reports or the Overview (decisions and zones are matched to it by name), so the Live
// feed and Zones screens point here when there is none.

import React, { useState } from 'react';
import { Camera, Loader2, Plus, Trash2, Video, Upload } from 'lucide-react';

import { addCamera, removeCamera } from '../lib/platform/cameras';
import { can, denialMessage } from '../lib/permissions';
import { Panel, Notice, Skeleton, BTN_PRIMARY, BTN_QUIET, INPUT, fmtTime } from './safety/common';

const SOURCE_LABEL = { WEBCAM: 'Webcam or phone', UPLOAD: 'Recorded videos', RTSP: 'Network camera (RTSP)' };

export default function CamerasScreen({ role, cameras, status, error, selected, onSelect, onChanged }) {
  const canEdit = can(role, 'cameras.edit');
  const [name, setName] = useState('');
  const [source, setSource] = useState('WEBCAM');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);           // { tone, text }
  const [confirming, setConfirming] = useState(null);     // camera id waiting for a second click
  const [removing, setRemoving] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const r = await addCamera({ name, sourceType: source });
      if (!r?.ok) {
        setMessage({ tone: 'error', text: r?.message || 'The camera could not be added.' });
        return;
      }
      setName('');
      setMessage({ tone: 'ok', text: `Added “${r.camera.name}”. Draw its zones on the Zones screen, then run it from the Live feed.` });
      await onChanged?.(r.camera.name);
    } catch {
      setMessage({ tone: 'error', text: 'The camera could not be added. Check the connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (cam) => {
    if (confirming !== cam.id) {
      setConfirming(cam.id);
      return;
    }
    setRemoving(cam.id);
    setMessage(null);
    try {
      const r = await removeCamera(cam.id);
      if (!r?.ok) {
        setMessage({ tone: 'error', text: r?.message || 'The camera could not be removed.' });
        return;
      }
      setMessage({ tone: 'ok', text: `Removed “${cam.name}”.` });
      await onChanged?.();
    } catch {
      setMessage({ tone: 'error', text: 'The camera could not be removed. Check the connection and try again.' });
    } finally {
      setRemoving(null);
      setConfirming(null);
    }
  };

  return (
    <div className="space-y-5">
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <Panel title="Cameras" icon={Camera} aside={status === 'ready' ? `${cameras.length} registered` : null}>
        {status === 'loading' && <Skeleton rows={2} label="Loading cameras" />}
        {status === 'error' && <Notice tone="error">{error || 'Could not load the cameras.'}</Notice>}
        {status === 'ready' && cameras.length === 0 && (
          <Notice>
            No camera is registered yet. Until one is, what a run decides stays on this computer and does not reach
            Review, Events, Reports or the Overview.{canEdit ? ' Add one below.' : ' Ask an administrator or manager to add one.'}
          </Notice>
        )}
        {status === 'ready' && cameras.length > 0 && (
          <ul className="divide-y divide-[color:var(--line-soft)]">
            {cameras.map((cam) => (
              <li key={cam.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    {cam.sourceType === 'UPLOAD'
                      ? <Upload className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" />
                      : <Video className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" />}
                    <span className="text-[13px] font-bold text-ink break-all">{cam.name}</span>
                    {selected === cam.name && (
                      <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-accent">in use</span>
                    )}
                  </div>
                  <p className="text-[11.5px] text-ink-faint mt-0.5">
                    {SOURCE_LABEL[cam.sourceType] || cam.sourceType} · added {fmtTime(cam.createdAt)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selected !== cam.name && (
                    <button type="button" className={BTN_QUIET} onClick={() => onSelect?.(cam.name)}>
                      Use this camera
                    </button>
                  )}
                  {canEdit && (
                    <button
                      type="button"
                      className={confirming === cam.id ? BTN_PRIMARY : BTN_QUIET}
                      onClick={() => remove(cam)}
                      disabled={removing === cam.id}
                      onBlur={() => { if (confirming === cam.id && removing !== cam.id) setConfirming(null); }}
                    >
                      {removing === cam.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                        : <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />}
                      {confirming === cam.id ? 'Remove it and its history' : 'Remove'}
                    </button>
                  )}
                </div>
                {confirming === cam.id && (
                  <p className="basis-full text-[11.5px] text-ink-muted">
                    Removing a camera also deletes its zones and every safety decision, zone event and corrective
                    action recorded on it. Click again to confirm.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {canEdit ? (
        <Panel title="Add a camera" icon={Plus}>
          <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px_auto] items-end">
            <label className="grid gap-1 text-[11.5px] font-semibold text-ink-muted">
              Name
              <input
                className={INPUT}
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={64}
                placeholder="e.g. Gate camera"
                required
              />
            </label>
            <label className="grid gap-1 text-[11.5px] font-semibold text-ink-muted">
              Video comes from
              <select className={INPUT} value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="WEBCAM">{SOURCE_LABEL.WEBCAM}</option>
                <option value="UPLOAD">{SOURCE_LABEL.UPLOAD}</option>
              </select>
            </label>
            <button type="submit" className={BTN_PRIMARY} disabled={busy || !name.trim()}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
              Add camera
            </button>
          </form>
          <p className="mt-3 text-[11.5px] text-ink-faint leading-relaxed">
            The name cannot be changed later. Your plan sets how many cameras you can add.
          </p>
        </Panel>
      ) : (
        role && <Notice>You have view-only access here. {denialMessage('cameras.edit')}</Notice>
      )}
    </div>
  );
}
