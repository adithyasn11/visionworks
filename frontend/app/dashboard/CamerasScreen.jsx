'use client';

// frontend/app/dashboard/CamerasScreen.jsx
//
// The organisation's cameras: list, add, remove. A camera has to exist here before anything a run decides can
// reach Review, Events, Reports or the Overview (decisions and zones are matched to it by name), so the Live
// feed and Zones screens point here when there is none.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Loader2, Plus, Trash2, Video, Upload, MapPin } from 'lucide-react';

import { addCamera, removeCamera, listSites, addSite, removeSite } from '../lib/platform/cameras';
import { listSafetyZones } from '../lib/safety/management';
import { listRules } from '../lib/safetyApi';
import { can, denialMessage } from '../lib/permissions';
import { Panel, Notice, Skeleton, BTN_PRIMARY, BTN_QUIET, INPUT, fmtTime } from './safety/common';

const SOURCE_LABEL = { WEBCAM: 'Camera', UPLOAD: 'Uploaded videos', RTSP: 'Network camera (RTSP)' };

export default function CamerasScreen({ role, cameras, status, error, selected, onSelect, onChanged }) {
  const canEdit = can(role, 'cameras.edit');
  const [name, setName] = useState('');
  const [source, setSource] = useState('WEBCAM');
  const [rtspUrl, setRtspUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);           // { tone, text }
  const [confirming, setConfirming] = useState(null);     // camera id waiting for a second click
  const [removing, setRemoving] = useState(null);
  // Sites: a camera belongs to one. The plan sets how many there can be.
  const [sites, setSites] = useState({ status: 'loading', rows: [], error: null });
  const [siteId, setSiteId] = useState('');
  const [siteName, setSiteName] = useState('');
  const [siteBusy, setSiteBusy] = useState(false);
  const [siteConfirm, setSiteConfirm] = useState(null);
  const canEditSites = can(role, 'sites.edit');
  const isAdmin = role === 'ADMIN';

  const loadSites = useCallback(async () => {
    const r = await listSites();
    setSites(r.error ? { status: 'error', rows: [], error: r.error } : { status: 'ready', rows: r.sites, error: null });
    setSiteId((cur) => (cur && r.sites?.some((x) => x.id === cur) ? cur : r.sites?.[0]?.id ?? ''));
  }, []);
  useEffect(() => { loadSites(); }, [loadSites]);

  // Each camera's zones and how many rules run on it, for its box.
  const nameRef = useRef(null);
  const [setup, setSetup] = useState({ zones: [], rules: [] });
  useEffect(() => {
    let live = true;
    Promise.all([listSafetyZones(), listRules()]).then(([z, r]) => {
      if (live) setSetup({ zones: z.zones || [], rules: r.ok ? r.data.rules || [] : [] });
    });
    return () => { live = false; };
  }, [cameras]);
  const zonesOf = (cam) => setup.zones.filter((z) => z.cameraId === cam.id);
  const rulesOf = (cam) => {
    const camOfZone = new Map(setup.zones.map((z) => [z.id, z.cameraId]));
    return setup.rules.filter((x) => x.isEnabled !== false && (x.cameraId ? x.cameraId === cam.id : x.zoneId ? camOfZone.get(x.zoneId) === cam.id : true)).length;
  };

  const submitSite = async (e) => {
    e.preventDefault();
    setSiteBusy(true);
    setMessage(null);
    try {
      let tz = null;
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { tz = null; }
      const r = await addSite({ name: siteName, timezone: tz });
      if (!r?.ok) { setMessage({ tone: 'error', text: r?.message || 'The site could not be added.' }); return; }
      setSiteName('');
      setMessage({ tone: 'ok', text: `Added the site “${r.site.name}”. Choose it when you add a camera.` });
      await loadSites();
    } catch {
      setMessage({ tone: 'error', text: 'The site could not be added. Check the connection and try again.' });
    } finally {
      setSiteBusy(false);
    }
  };

  const dropSite = async (site) => {
    if (siteConfirm !== site.id) { setSiteConfirm(site.id); return; }
    setSiteBusy(true);
    setMessage(null);
    try {
      const r = await removeSite(site.id);
      setMessage(r?.ok ? { tone: 'ok', text: `Removed the site “${site.name}”.` } : { tone: 'error', text: r?.message || 'The site could not be removed.' });
      if (r?.ok) await loadSites();
    } catch {
      setMessage({ tone: 'error', text: 'The site could not be removed. Check the connection and try again.' });
    } finally {
      setSiteBusy(false);
      setSiteConfirm(null);
    }
  };
  const siteNameOf = (id) => sites.rows.find((x) => x.id === id)?.name;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const r = await addCamera({ name, sourceType: source, siteId: siteId || undefined, rtspUrl: source === 'RTSP' ? rtspUrl : undefined });
      if (!r?.ok) {
        setMessage({ tone: 'error', text: r?.message || 'The camera could not be added.' });
        return;
      }
      setName(''); setRtspUrl('');
      setMessage({ tone: 'ok', text: `Added “${r.camera.name}”. Draw its zones on the Zones screen, then run it from the Live feed.` });
      await onChanged?.(r.camera.name);
      loadSites();
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
      loadSites();
    } catch {
      setMessage({ tone: 'error', text: 'The camera could not be removed. Check the connection and try again.' });
    } finally {
      setRemoving(null);
      setConfirming(null);
    }
  };

  const LABEL = 'grid gap-1 text-[0.78125rem] font-semibold text-ink-muted';
  return (
    <div className="space-y-4">
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      {/* One screen on a laptop: cameras on the left, adding and sites on the right. Long lists scroll inside. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[minmax(0,1fr)_24rem] lg:h-[calc(100dvh-15.5rem)] lg:min-h-[32rem]">
        <Panel title="Cameras" icon={Camera} aside={status === 'ready' ? `${cameras.length} added` : null} className="flex flex-col min-h-0">
          {status === 'loading' && <Skeleton rows={2} label="Loading cameras" />}
          {status === 'error' && <Notice tone="error">{error || 'Could not load the cameras.'}</Notice>}
          {status === 'ready' && cameras.length === 0 && (
            <div className="flex-1 flex flex-col items-center justify-center text-center gap-4 py-10">
              <span className="flex items-center justify-center w-14 h-14 rounded-2xl border border-line bg-surface-alt">
                <Camera className="w-6 h-6 text-ink-muted" strokeWidth={2} aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-xl sm:text-2xl font-black tracking-tight text-ink">No cameras yet</h2>
                <p className="mt-2 text-[0.9375rem] text-ink-muted max-w-sm" title="Until one is added, results stay on this computer and do not reach Review, Events, Reports or the Overview.">
                  {canEdit ? 'Add one so results are saved to Review, Events and Reports.' : 'Ask an admin or manager to add one.'}
                </p>
              </div>
            </div>
          )}
          {status === 'ready' && cameras.length > 0 && (
            <ul className="flex-1 min-h-0 overflow-y-auto grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-3 content-start -mx-1 px-1 pb-1">
              {cameras.map((cam) => {
                const Icon = cam.sourceType === 'UPLOAD' ? Upload : Video;
                const inUse = selected === cam.name;
                return (
                  <li key={cam.id} className={`rounded-2xl border bg-surface p-4 flex flex-col gap-3 min-w-0 ${inUse ? 'border-[color:var(--accent)]' : 'border-line'}`}>
                    <div className="flex items-start gap-3 min-w-0">
                      <span className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${inUse ? 'bg-accent text-white' : 'bg-accent-soft text-accent'}`}>
                        <Icon className="w-4 h-4" aria-hidden="true" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[0.9375rem] font-black text-ink truncate" title={cam.name}>{cam.name}</span>
                          {inUse && <span className="shrink-0 px-2 py-0.5 rounded-md text-[0.6875rem] font-bold bg-accent-soft text-accent">In use</span>}
                        </div>
                        <p className="text-[0.8125rem] text-ink-muted truncate" title={`Added ${fmtTime(cam.createdAt)}`}>
                          {SOURCE_LABEL[cam.sourceType] || cam.sourceType}{siteNameOf(cam.siteId) ? ` · ${siteNameOf(cam.siteId)}` : ''}
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 min-w-0">
                      {zonesOf(cam).length === 0 ? (
                        <span className="text-[0.75rem] text-ink-faint">No zones yet</span>
                      ) : (
                        <>
                          {zonesOf(cam).slice(0, 3).map((z) => (
                            <span key={z.id} className="max-w-[9rem] truncate px-2 py-0.5 rounded-md border border-line bg-surface-alt text-[0.75rem] font-semibold text-ink-muted">{z.name}</span>
                          ))}
                          {zonesOf(cam).length > 3 && <span className="px-1 py-0.5 text-[0.75rem] text-ink-faint">+{zonesOf(cam).length - 3}</span>}
                        </>
                      )}
                    </div>
                    <p className="text-[0.75rem] text-ink-faint">
                      {zonesOf(cam).length} zone{zonesOf(cam).length === 1 ? '' : 's'} · {rulesOf(cam)} rule{rulesOf(cam) === 1 ? '' : 's'} checked
                    </p>
                    {confirming === cam.id && (
                      <p className="text-[0.78125rem] text-ink-muted">Its zones and history go too. Click again to remove.</p>
                    )}
                    <div className="mt-auto flex flex-wrap items-center gap-2">
                      {!inUse && (
                        <button type="button" className={BTN_QUIET} onClick={() => onSelect?.(cam.name)}>Use this camera</button>
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
                          {confirming === cam.id ? 'Sure? Remove' : 'Remove'}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
              {canEdit && (
                <li className="min-w-0">
                  <button type="button" onClick={() => nameRef.current?.focus()}
                    className="w-full h-full min-h-[9rem] rounded-2xl border-2 border-dashed border-line text-ink-muted hover:text-accent hover:border-[color:var(--accent)] transition-colors flex flex-col items-center justify-center gap-2">
                    <Plus className="w-5 h-5" aria-hidden="true" />
                    <span className="text-[0.875rem] font-bold">Add another</span>
                  </button>
                </li>
              )}
            </ul>
          )}
        </Panel>

        <div className="flex flex-col gap-4 min-h-0">
          {canEdit ? (
            <Panel title="Add a camera" icon={Plus} className="shrink-0">
              <form onSubmit={submit} className="grid gap-3">
                <label className={LABEL}>
                  Name
                  <input ref={nameRef} className={INPUT} value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="e.g. Gate camera" required />
                </label>
                <div className={`grid gap-3 ${sites.rows.length > 1 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                  <label className={LABEL}>
                    Video comes from
                    <select className={INPUT} value={source} onChange={(e) => setSource(e.target.value)}>
                      <option value="WEBCAM">{SOURCE_LABEL.WEBCAM}</option>
                      <option value="UPLOAD">{SOURCE_LABEL.UPLOAD}</option>
                      <option value="RTSP">{SOURCE_LABEL.RTSP}</option>
                    </select>
                  </label>
                  {sites.rows.length > 1 && (
                    <label className={LABEL}>
                      Site
                      <select className={INPUT} value={siteId} onChange={(e) => setSiteId(e.target.value)}>
                        {sites.rows.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                      </select>
                    </label>
                  )}
                </div>
                {source === 'RTSP' && (
                  <label className={LABEL} title="Kept on the server. It can include the camera's user name and password.">
                    Stream address
                    <input
                      className={INPUT}
                      value={rtspUrl}
                      onChange={(e) => setRtspUrl(e.target.value)}
                      maxLength={1024}
                      placeholder="rtsp://user:password@192.168.1.50:554/stream1"
                      autoComplete="off"
                      spellCheck={false}
                      required
                    />
                  </label>
                )}
                <button type="submit" className={`${BTN_PRIMARY} justify-center`} disabled={busy || !name.trim() || (source === 'RTSP' && !rtspUrl.trim())}>
                  {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
                  Add camera
                </button>
              </form>
              <p className="mt-2 text-[0.75rem] text-ink-faint" title="Your plan sets how many cameras you can add.">The name can’t be changed later.</p>
            </Panel>
          ) : (
            role && <Notice>You have view-only access here. {denialMessage('cameras.edit')}</Notice>
          )}

          <Panel title="Sites" icon={MapPin} aside={sites.status === 'ready' ? `${sites.rows.length} site${sites.rows.length === 1 ? '' : 's'}` : null}
            className="flex flex-col lg:flex-1 lg:min-h-[13rem] min-h-0">
            {sites.status === 'loading' && <Skeleton rows={2} label="Loading sites" />}
            {sites.status === 'error' && <Notice tone="error">{sites.error}</Notice>}
            {sites.status === 'ready' && sites.rows.length > 0 && (
              <ul className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2">
                {sites.rows.map((site) => (
                  <li key={site.id} className="flex items-center justify-between gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 shrink-0">
                    <div className="min-w-0">
                      <span className="block text-[0.875rem] font-bold text-ink truncate" title={site.name}>{site.name}</span>
                      <p className="text-[0.75rem] text-ink-faint truncate">
                        {site.cameras} camera{site.cameras === 1 ? '' : 's'}{site.timezone ? ` · ${site.timezone}` : ''}
                      </p>
                    </div>
                    {isAdmin && sites.rows.length > 1 && (
                      <button
                        type="button"
                        className={siteConfirm === site.id ? BTN_PRIMARY : BTN_QUIET}
                        onClick={() => dropSite(site)}
                        disabled={siteBusy}
                        title="A site with cameras on it cannot be removed."
                        aria-label={siteConfirm === site.id ? `Click again to remove ${site.name}` : `Remove ${site.name}`}
                        onBlur={() => { if (siteConfirm === site.id && !siteBusy) setSiteConfirm(null); }}
                      >
                        <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                        {siteConfirm === site.id ? 'Sure?' : ''}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {canEditSites && (
              <form onSubmit={submitSite} className="mt-3 pt-3 border-t border-line-soft grid grid-cols-[minmax(0,1fr)_auto] gap-2 shrink-0">
                <input
                  className={INPUT}
                  value={siteName}
                  onChange={(e) => setSiteName(e.target.value)}
                  maxLength={160}
                  placeholder="New site, e.g. Plant 2"
                  aria-label="New site"
                />
                <button type="submit" className={BTN_PRIMARY} disabled={siteBusy || !siteName.trim()} title="Your plan sets how many sites you can have.">
                  {siteBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
                  Add
                </button>
              </form>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
