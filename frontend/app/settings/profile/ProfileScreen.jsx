'use client';

// frontend/app/settings/profile/ProfileScreen.jsx
//
// Your own profile: the picture, your name and job title, your time zone and theme, your password, and your
// sessions. On a laptop it is one screen: who you are on the left, what you can change on the right.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Briefcase, Building2, Calendar, Camera, Check, KeyRound, Loader2, LogOut, Mail, Monitor, Moon, ShieldCheck, Sun,
  Trash2, UserRound,
} from 'lucide-react';
import DashboardShell from '../../dashboard/DashboardShell';
import { Banner } from '../../components/AuthFormBits';
import { supabase } from '../../lib/supabase/browser';
import { getProfile, setAvatar, updateProfile } from './actions';

const ROLE_LABEL = { ADMIN: 'Administrator', MANAGER: 'Manager', VIEWER: 'Viewer' };
const INPUT = 'w-full rounded-xl bg-ground border-2 border-field px-3.5 py-2 text-[0.875rem] text-ink placeholder:text-ink-faint transition-colors focus:outline-none focus:border-[color:var(--accent)] hover:border-field-hover disabled:opacity-60';
const LABEL = 'flex flex-col gap-1.5 text-[0.8125rem] font-bold text-ink min-w-0';
const CARD = 'rounded-2xl border border-line bg-surface p-5 min-w-0';
const BTN = 'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[0.84375rem] font-bold transition disabled:opacity-50 disabled:cursor-not-allowed';
const BTN_RED = `${BTN} bg-accent text-white hover:brightness-110`;
const BTN_LINE = `${BTN} border border-line text-ink-muted hover:text-ink hover:border-field`;

const fmtDate = (v) => { try { return v ? new Date(v).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'; } catch { return '—'; } };
const initialsOf = (name, email) => (name || email || '?').split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s) => s[0].toUpperCase()).join('');
const zones = () => { try { return typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : ['UTC']; } catch { return ['UTC']; } };

/** Applies a theme choice to this browser the same way the sidebar toggle does. */
function applyTheme(choice) {
  try {
    const root = document.documentElement;
    if (choice === 'SYSTEM') {
      localStorage.removeItem('theme');
      root.classList.toggle('dark', window.matchMedia('(prefers-color-scheme: dark)').matches);
    } else {
      localStorage.setItem('theme', choice === 'DARK' ? 'dark' : 'light');
      root.classList.toggle('dark', choice === 'DARK');
    }
  } catch { /* storage blocked: the page still saves the choice */ }
}

/** Shrinks a picture to a 256 x 256 square (centre crop) as WebP. */
function squareImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const c = document.createElement('canvas');
      c.width = 256; c.height = 256;
      c.getContext('2d').drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, 256, 256);
      URL.revokeObjectURL(url);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error('encode'))), 'image/webp', 0.88);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('read')); };
    img.src = url;
  });
}

function Card({ icon: Icon, title, aside, children, className = '' }) {
  return (
    <section className={`${CARD} flex flex-col gap-4 ${className}`}>
      <header className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-[0.9375rem] font-black tracking-tight text-ink"><Icon className="w-4 h-4 text-accent" aria-hidden="true" /> {title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

export default function ProfileScreen({ role, viewer }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [banner, setBanner] = useState(null);
  const [saving, setSaving] = useState(false);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [pw, setPw] = useState({ next: '', again: '' });
  const [pwBusy, setPwBusy] = useState(false);
  const [pwMsg, setPwMsg] = useState(null);
  const [sessionBusy, setSessionBusy] = useState(null);
  const [sessionMsg, setSessionMsg] = useState(null);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    const r = await getProfile();
    if (!r.ok) { setBanner({ kind: 'error', text: r.message }); return; }
    setData(r);
    setForm({ fullName: r.profile.fullName, jobTitle: r.profile.jobTitle, timezone: r.profile.timezone, themePreference: r.profile.themePreference });
  }, []);
  useEffect(() => { load(); }, [load]);

  const changed = data && form && ['fullName', 'jobTitle', 'timezone', 'themePreference'].some((k) => (form[k] || '') !== (data.profile[k] || ''));
  const tell = () => window.dispatchEvent(new Event('vw-profile-changed'));

  async function save(e) {
    e.preventDefault();
    setSaving(true); setBanner(null);
    const r = await updateProfile(form);
    setSaving(false);
    if (!r.ok) { setBanner({ kind: 'error', text: r.message }); return; }
    applyTheme(form.themePreference);
    setBanner({ kind: 'success', text: 'Your profile was saved.' });
    await load(); tell();
  }

  async function upload(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) { setBanner({ kind: 'error', text: 'Choose a picture file (JPG, PNG or WebP).' }); return; }
    if (!supabase) { setBanner({ kind: 'error', text: 'The database is not configured.' }); return; }
    setPhotoBusy(true); setBanner(null);
    try {
      const blob = await squareImage(file);
      const uid = data.profile.id;
      const path = `${uid}/avatar-${Date.now()}.webp`;
      const up = await supabase.storage.from('avatars').upload(path, blob, { contentType: 'image/webp', upsert: false });
      if (up.error) throw up.error;
      const { data: pub } = supabase.storage.from('avatars').getPublicUrl(path);
      const r = await setAvatar(pub.publicUrl);
      if (!r.ok) throw new Error(r.message);
      // Older pictures in your folder are no longer used.
      const old = await supabase.storage.from('avatars').list(uid);
      const stale = (old.data || []).map((o) => `${uid}/${o.name}`).filter((p) => p !== path);
      if (stale.length) await supabase.storage.from('avatars').remove(stale);
      setBanner({ kind: 'success', text: 'Your picture was updated.' });
      await load(); tell();
    } catch (err) {
      setBanner({ kind: 'error', text: err?.message && !/^(read|encode)$/.test(err.message) ? `The picture could not be uploaded: ${err.message}` : 'That picture could not be read. Try another one.' });
    } finally {
      setPhotoBusy(false);
    }
  }

  async function removePhoto() {
    setPhotoBusy(true); setBanner(null);
    const r = await setAvatar(null);
    if (r.ok && supabase) {
      const uid = data.profile.id;
      const old = await supabase.storage.from('avatars').list(uid);
      const all = (old.data || []).map((o) => `${uid}/${o.name}`);
      if (all.length) await supabase.storage.from('avatars').remove(all);
    }
    setPhotoBusy(false);
    setBanner({ kind: r.ok ? 'success' : 'error', text: r.ok ? 'Your picture was removed.' : r.message });
    if (r.ok) { await load(); tell(); }
  }

  async function changePassword(e) {
    e.preventDefault();
    setPwMsg(null);
    if (pw.next.length < 8) { setPwMsg({ kind: 'error', text: 'Use at least 8 characters.' }); return; }
    if (pw.next !== pw.again) { setPwMsg({ kind: 'error', text: 'The two passwords do not match.' }); return; }
    setPwBusy(true);
    const { error } = await supabase.auth.updateUser({ password: pw.next });
    setPwBusy(false);
    if (error) { setPwMsg({ kind: 'error', text: error.message || 'The password could not be changed.' }); return; }
    setPw({ next: '', again: '' });
    setPwMsg({ kind: 'success', text: 'Password changed.' });
  }

  async function signOutOthers() {
    setSessionBusy('others'); setSessionMsg(null);
    const { error } = await supabase.auth.signOut({ scope: 'others' });
    setSessionBusy(null);
    setSessionMsg(error ? { kind: 'error', text: 'Could not sign out the other devices.' } : { kind: 'success', text: 'Every other device is signed out.' });
  }
  async function signOutHere() {
    setSessionBusy('here');
    await supabase.auth.signOut();
    window.location.href = '/login';
  }

  const p = data?.profile;
  const shownName = (p && (p.fullName || p.email.split('@')[0])) || viewer.fullName || viewer.email;
  const avatar = p ? p.avatarUrl : viewer.avatarUrl;
  const zoneList = zones();

  return (
    <DashboardShell user={{ ...viewer, fullName: p?.fullName || viewer.fullName, avatarUrl: avatar }} role={role}>
      <div className="animate-view-in flex flex-col gap-5">
        <header className="flex flex-col gap-1.5 pb-4 border-b border-line">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-accent">Profile</p>
          <h1 className="text-[1.625rem] sm:text-[1.875rem] font-black tracking-tight text-ink leading-[1.15]">Your profile</h1>
          <p className="text-[0.84375rem] text-ink-muted">Your picture, your details and how you sign in.</p>
        </header>

        {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

        {!data ? (
          <div className="grid grid-cols-1 lg:grid-cols-[22rem_minmax(0,1fr)] gap-5">
            <div className={`${CARD} h-[26rem] skeleton`} aria-hidden="true" />
            <div className={`${CARD} h-[26rem] skeleton`} aria-hidden="true" />
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[22rem_minmax(0,1fr)] gap-5 items-stretch">
            {/* Who you are. */}
            <aside className="card-dark rounded-2xl p-5 flex flex-col items-center text-center gap-4 relative overflow-hidden">
              <div className="relative">
                <div className="w-24 h-24 rounded-3xl overflow-hidden bg-white/10 border border-white/15 flex items-center justify-center">
                  {avatar
                    ? <img src={avatar} alt="" className="w-full h-full object-cover" />
                    : <span className="text-3xl font-black text-white">{initialsOf(p.fullName, p.email)}</span>}
                </div>
                <button type="button" onClick={() => fileRef.current?.click()} disabled={photoBusy}
                  className="absolute -bottom-2 -right-2 w-10 h-10 rounded-xl bg-accent text-white flex items-center justify-center shadow-lg hover:brightness-110 transition disabled:opacity-60"
                  aria-label="Change picture" title="Change picture">
                  {photoBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
                </button>
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                  onChange={(e) => { upload(e.target.files && e.target.files[0]); e.target.value = ''; }} />
              </div>
              <div className="min-w-0 w-full">
                <p className="text-xl font-black tracking-tight text-white truncate" title={shownName}>{shownName}</p>
                <p className="mt-1 text-[0.875rem] text-white/60 truncate">{p.jobTitle || 'No job title yet'}</p>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {data.role && <span className="px-2.5 py-1 rounded-lg bg-accent text-white text-[0.75rem] font-bold">{ROLE_LABEL[data.role] || data.role}</span>}
                {data.org && <span className="px-2.5 py-1 rounded-lg bg-white/10 text-white/80 text-[0.75rem] font-bold truncate max-w-[12rem]" title={data.org.name}>{data.org.name}</span>}
              </div>
              <ul className="w-full mt-auto flex flex-col gap-2 text-left">
                {[
                  { icon: Mail, label: 'Email', value: p.email },
                  { icon: Calendar, label: 'Joined', value: fmtDate(data.joinedAt || p.createdAt) },
                  { icon: ShieldCheck, label: 'Signs in with', value: p.providers.length ? p.providers.map((x) => (x === 'email' ? 'Email' : x[0].toUpperCase() + x.slice(1))).join(' and ') : 'Email' },
                ].map(({ icon: Icon, label, value }) => (
                  <li key={label} className="flex items-center gap-3 rounded-xl bg-white/[0.06] border border-white/10 px-3.5 py-2 min-w-0">
                    <Icon className="w-4 h-4 text-accent shrink-0" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="block text-[0.6875rem] font-bold text-white/50">{label}</span>
                      <span className="block text-[0.8125rem] font-bold text-white truncate" title={value}>{value}</span>
                    </span>
                  </li>
                ))}
              </ul>
              {avatar && (
                <button type="button" onClick={removePhoto} disabled={photoBusy} className="inline-flex items-center gap-1.5 text-[0.75rem] font-bold text-white/60 hover:text-white transition-colors">
                  <Trash2 className="w-3.5 h-3.5" aria-hidden="true" /> Remove picture
                </button>
              )}
            </aside>

            <div className="flex flex-col gap-5 min-w-0">
              {/* What you can change. */}
              <Card icon={UserRound} title="Your details"
                aside={changed ? <span className="text-[0.75rem] font-bold text-accent">Not saved</span> : <span className="text-[0.75rem] text-ink-faint">Saved</span>}>
                <form onSubmit={save} className="flex flex-col gap-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
                    <label className={LABEL}>Name
                      <input className={INPUT} value={form.fullName} maxLength={160} onChange={(e) => setForm({ ...form, fullName: e.target.value })} placeholder="Your name" autoComplete="name" />
                    </label>
                    <label className={LABEL}><span className="flex items-center gap-1.5"><Briefcase className="w-3.5 h-3.5 text-ink-faint" aria-hidden="true" /> Job title</span>
                      <input className={INPUT} value={form.jobTitle} maxLength={120} onChange={(e) => setForm({ ...form, jobTitle: e.target.value })} placeholder="e.g. Safety officer" autoComplete="organization-title" />
                    </label>
                    <label className={LABEL}>Time zone
                      <select className={INPUT} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
                        <option value="">Same as the organisation{data.org?.timezone ? ` (${data.org.timezone})` : ''}</option>
                        {zoneList.map((z) => <option key={z} value={z}>{z}</option>)}
                      </select>
                    </label>
                    <div className={LABEL}>Theme
                      <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2">
                        {[['SYSTEM', 'Auto', Monitor], ['LIGHT', 'Light', Sun], ['DARK', 'Dark', Moon]].map(([v, label, Icon]) => {
                          const on = form.themePreference === v;
                          return (
                            <button key={v} type="button" role="radio" aria-checked={on} onClick={() => setForm({ ...form, themePreference: v })}
                              className={`flex items-center justify-center gap-1.5 rounded-xl border-2 py-2 text-[0.8125rem] font-bold transition-colors ${on ? 'border-[color:var(--accent)] bg-accent-soft text-accent' : 'border-field text-ink-muted hover:border-field-hover'}`}>
                              <Icon className="w-4 h-4" aria-hidden="true" /> {label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    {changed && <button type="button" className={BTN_LINE} onClick={() => setForm({ fullName: p.fullName, jobTitle: p.jobTitle, timezone: p.timezone, themePreference: p.themePreference })}>Undo</button>}
                    <button type="submit" className={BTN_RED} disabled={saving || !changed}>
                      {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} Save changes
                    </button>
                  </div>
                </form>
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 flex-1">
                <Card icon={KeyRound} title={p.hasPassword ? 'Change password' : 'Add a password'}>
                  <form onSubmit={changePassword} className="flex flex-col gap-3 flex-1">
                    <input className={INPUT} type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} placeholder="New password" autoComplete="new-password" aria-label="New password" />
                    <input className={INPUT} type="password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} placeholder="Type it again" autoComplete="new-password" aria-label="Type the new password again" />
                    {pwMsg && <p className={`text-[0.8125rem] font-semibold ${pwMsg.kind === 'error' ? 'text-accent' : 'text-ink-muted'}`}>{pwMsg.text}</p>}
                    <button type="submit" className={`${BTN_RED} mt-auto`} disabled={pwBusy || !pw.next}>
                      {pwBusy && <Loader2 className="w-4 h-4 animate-spin" />} {p.hasPassword ? 'Change password' : 'Add password'}
                    </button>
                  </form>
                </Card>

                <Card icon={LogOut} title="Where you’re signed in">
                  <div className="flex flex-col gap-3 flex-1">
                    <div className="rounded-xl border border-line bg-surface-alt px-4 py-2.5">
                      <p className="text-[0.8125rem] font-bold text-ink">This device</p>
                      <p className="text-[0.75rem] text-ink-faint">Last sign-in {fmtDate(p.lastSignInAt)}</p>
                    </div>
                    {data.org && (
                      <div className="rounded-xl border border-line bg-surface-alt px-4 py-2.5 flex items-center gap-3 min-w-0">
                        <Building2 className="w-4 h-4 text-accent shrink-0" aria-hidden="true" />
                        <span className="min-w-0">
                          <span className="block text-[0.8125rem] font-bold text-ink truncate">{data.org.name}</span>
                          <span className="block text-[0.75rem] text-ink-faint">{ROLE_LABEL[data.role] || 'Member'}</span>
                        </span>
                      </div>
                    )}
                    {sessionMsg && <p className={`text-[0.8125rem] font-semibold ${sessionMsg.kind === 'error' ? 'text-accent' : 'text-ink-muted'}`}>{sessionMsg.text}</p>}
                    <div className="mt-auto grid grid-cols-2 gap-2">
                      <button type="button" className={BTN_LINE} onClick={signOutOthers} disabled={sessionBusy !== null}>
                        {sessionBusy === 'others' && <Loader2 className="w-4 h-4 animate-spin" />} Other devices
                      </button>
                      <button type="button" className={BTN_RED} onClick={signOutHere} disabled={sessionBusy !== null}>
                        {sessionBusy === 'here' ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogOut className="w-4 h-4" />} Sign out
                      </button>
                    </div>
                  </div>
                </Card>
              </div>
            </div>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
