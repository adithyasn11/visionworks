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

  const LABEL = 'grid grid-cols-[minmax(0,1fr)] gap-1 min-w-0 text-[0.78125rem] font-semibold text-ink-muted';
  const FIELD = `${INPUT} w-full min-w-0`;

  // Cameras grouped by the site they belong to; a camera whose site is gone sits under "No site".
  const groups = sites.rows.map((site) => ({ site, cams: cameras.filter((c) => c.siteId === site.id) }));
  const loose = cameras.filter((c) => !sites.rows.some((x) => x.id === c.siteId));
  if (loose.length && sites.status === 'ready') groups.push({ site: null, cams: loose });

  const cameraBox = (cam) => {
    const Icon = cam.sourceType === 'UPLOAD' ? Upload : Video;
    const inUse = selected === cam.name;
    const zs = zonesOf(cam);
    const rc = rulesOf(cam);
    const facts = [
      SOURCE_LABEL[cam.sourceType] || cam.sourceType,
      zs.length ? `${zs.length} zone${zs.length === 1 ? '' : 's'}` : 'No zones yet',
      `${rc} rule${rc === 1 ? '' : 's'}`,
    ].join(' · ');
    return (
      <li key={cam.id} className={`rounded-xl border bg-surface p-3 min-w-0 ${inUse ? 'border-[color:var(--accent)]' : 'border-line'}`}>
        <div className="flex items-center gap-3 min-w-0">
          <span className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${inUse ? 'bg-accent text-white' : 'bg-accent-soft text-accent'}`}>
            <Icon className="w-4 h-4" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[0.9375rem] font-black text-ink truncate" title={cam.name}>{cam.name}</span>
              {inUse && <span className="shrink-0 px-2 py-0.5 rounded-md text-[0.6875rem] font-bold bg-accent-soft text-accent">In use</span>}
            </div>
            <p className="text-[0.78125rem] text-ink-muted truncate"
              title={`${zs.map((z) => z.name).join(', ') || 'No zones yet'} · added ${fmtTime(cam.createdAt)}`}>{facts}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {!inUse && <button type="button" className={BTN_QUIET} onClick={() => onSelect?.(cam.name)}>Use</button>}
            {canEdit && (
              <button
                type="button"
                className={confirming === cam.id ? BTN_PRIMARY : BTN_QUIET}
                onClick={() => remove(cam)}
                disabled={removing === cam.id}
                aria-label={confirming === cam.id ? `Click again to remove ${cam.name}` : `Remove ${cam.name}`}
                title="Remove"
                onBlur={() => { if (confirming === cam.id && removing !== cam.id) setConfirming(null); }}
              >
                {removing === cam.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />}
                {confirming === cam.id ? 'Sure?' : ''}
              </button>
            )}
          </div>
        </div>
        {confirming === cam.id && <p className="mt-2 text-[0.75rem] text-ink-muted">Its zones and history go too. Click again to remove.</p>}
      </li>
    );
  };

  return (
    <div className="space-y-5">
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      {/* Adding: a camera and a site, side by side. */}
      <div className={`grid grid-cols-1 gap-4 ${canEditSites ? 'xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]' : ''}`}>
        {canEdit ? (
          <Panel title="New camera" icon={Camera}>
            <form onSubmit={submit} className={`grid grid-cols-1 gap-3 items-end ${sites.rows.length > 1 ? 'sm:grid-cols-2 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_auto]' : 'sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]'}`}>
              <label className={LABEL}>
                Name
                <input ref={nameRef} className={FIELD} value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="e.g. Gate camera" required />
              </label>
              <label className={LABEL}>
                Video comes from
                <select className={FIELD} value={source} onChange={(e) => setSource(e.target.value)}>
                  <option value="WEBCAM">{SOURCE_LABEL.WEBCAM}</option>
                  <option value="UPLOAD">{SOURCE_LABEL.UPLOAD}</option>
                  <option value="RTSP">{SOURCE_LABEL.RTSP}</option>
                </select>
              </label>
              {sites.rows.length > 1 && (
                <label className={LABEL}>
                  Site
                  <select className={FIELD} value={siteId} onChange={(e) => setSiteId(e.target.value)}>
                    {sites.rows.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </select>
                </label>
              )}
              <button type="submit" className={`${BTN_PRIMARY} justify-center py-2`} disabled={busy || !name.trim() || (source === 'RTSP' && !rtspUrl.trim())}>
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
                Add camera
              </button>
              {source === 'RTSP' && (
                <label className={`${LABEL} col-span-full`} title="Kept on the server. It can include the camera's user name and password.">
                  Stream address
                  <input
                    className={FIELD}
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
            </form>
            <p className="mt-2 text-[0.75rem] text-ink-faint" title="Your plan sets how many cameras you can add.">The name can’t be changed later.</p>
          </Panel>
        ) : (
          role && <Notice>You have view-only access here. {denialMessage('cameras.edit')}</Notice>
        )}

        {canEditSites && (
          <Panel title="New site" icon={MapPin}>
            <form onSubmit={submitSite} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 items-end">
              <label className={LABEL}>
                Name
                <input className={FIELD} value={siteName} onChange={(e) => setSiteName(e.target.value)} maxLength={160} placeholder="e.g. Plant 2, Warehouse" />
              </label>
              <button type="submit" className={`${BTN_PRIMARY} py-2`} disabled={siteBusy || !siteName.trim()}>
                {siteBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
                Add site
              </button>
            </form>
            <p className="mt-2 text-[0.75rem] text-ink-faint" title="Your plan sets how many sites you can have.">A site groups the cameras in one place.</p>
          </Panel>
        )}
      </div>

      {/* Every site, with its cameras under it. */}
      {(status === 'loading' || sites.status === 'loading') && <Panel><Skeleton rows={3} label="Loading cameras" /></Panel>}
      {status === 'error' && <Notice tone="error">{error || 'Could not load the cameras.'}</Notice>}
      {sites.status === 'error' && <Notice tone="error">{sites.error}</Notice>}
      {status === 'ready' && sites.status === 'ready' && (
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
      {groups.map(({ site, cams }) => (
        <section key={site ? site.id : 'none'} className="glass-panel themed px-5 py-5 min-w-0">
          <header className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-3 min-w-0">
              <span className="w-9 h-9 rounded-xl bg-surface-alt border border-line flex items-center justify-center shrink-0">
                <MapPin className="w-4 h-4 text-accent" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <h3 className="text-[1.0625rem] font-black tracking-tight text-ink truncate">{site ? site.name : 'No site'}</h3>
                <p className="text-[0.78125rem] text-ink-faint">
                  {cams.length} camera{cams.length === 1 ? '' : 's'}{site && site.timezone ? ` · ${site.timezone}` : ''}
                </p>
              </div>
            </div>
            {site && isAdmin && sites.rows.length > 1 && cams.length === 0 && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className={siteConfirm === site.id ? BTN_PRIMARY : BTN_QUIET}
                  onClick={() => dropSite(site)}
                  disabled={siteBusy}
                  onBlur={() => { if (siteConfirm === site.id && !siteBusy) setSiteConfirm(null); }}
                >
                  <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                  {siteConfirm === site.id ? 'Sure?' : 'Remove'}
                </button>
              </div>
            )}
          </header>
          <ul className="flex flex-col gap-2">
            {cams.map(cameraBox)}
            {cams.length === 0 && <li className="rounded-xl border border-line bg-surface-alt px-3 py-3 text-[0.8125rem] text-ink-muted">No cameras here yet. Add one above and pick this site.</li>}
          </ul>
        </section>
      ))}
      </div>
      )}
    </div>
  );
}
