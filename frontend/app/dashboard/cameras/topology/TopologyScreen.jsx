'use client';

// frontend/app/dashboard/cameras/topology/TopologyScreen.jsx
//
// The camera layout: which exit leads to which entry, and how long the walk
// takes.
//
// Step 12 of IDENTITY_TRACKING_PLAN.md.
//
// WHY THIS SCREEN IS HONEST ABOUT WHAT IT BUYS
//
// The plan is blunt: cross-camera tracking is "the impressive part AND the
// least reliable", 60-75% where single-camera desk time is 85-95%. A UI that
// presents this as a solved feature would be setting somebody up to trust a
// number they should not. So the page says what the links are for, and what
// happens when they are wrong.
//
// The walk-time window is the part people get wrong. Too narrow and every real
// handoff is rejected; too wide and two different people walking the same
// corridor a minute apart become one person. The guidance below says to time
// the walk rather than guess it, because that is genuinely the difference
// between this working and not.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  Waypoints, Plus, Loader2, Trash2, ArrowRight, Check, X,
  AlertTriangle, Timer, Video,
} from 'lucide-react';

import DashboardShell from '../../DashboardShell';
import { Banner } from '../../../components/AuthFormBits';
import { can, denialMessage } from '../../../lib/permissions';
import { listTopology, createLink, updateLink, deleteLink } from './actions';

export default function TopologyScreen({ orgName, initialRole, viewer }) {
  const [state, setState] = useState({
    cameras: [], links: [], viewerRole: initialRole,
    limits: { min: 1, max: 300 }, loading: true,
  });
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ minSeconds: '', maxSeconds: '' });
  const [form, setForm] = useState({
    fromCameraId: '', toCameraId: '', minSeconds: '3', maxSeconds: '8',
  });

  const role = state.viewerRole ?? initialRole;
  const canEdit = can(role, 'cameras.edit');

  const refresh = useCallback(async () => {
    try {
      const res = await listTopology();
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setState((s) => ({ ...s, loading: false }));
        return;
      }
      setState({
        cameras: res.cameras, links: res.links,
        viewerRole: res.viewerRole, limits: res.limits, loading: false,
      });
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
      setState((s) => ({ ...s, loading: false }));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const nameOf = useCallback(
    (id) => state.cameras.find((c) => c.id === id)?.name ?? 'a removed camera',
    [state.cameras],
  );

  /**
   * Which (from, to) pairs already exist.
   *
   * Used to disable a direction that is already declared, rather than letting
   * somebody submit it and meet a database uniqueness error. The database
   * still enforces it — this only decides how the refusal reads.
   */
  const taken = useMemo(
    () => new Set(state.links.map((l) => `${l.fromCameraId}>${l.toCameraId}`)),
    [state.links],
  );

  const run = async (key, fn) => {
    setBusy(key);
    setBanner(null);
    try {
      const res = await fn();
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return false; }
      setBanner({ kind: 'success', text: res.message });
      await refresh();
      return true;
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    const ok = await run('create', () => createLink(form));
    if (ok) setForm({ ...form, fromCameraId: '', toCameraId: '' });
  };

  const startEdit = (link) => {
    setEditing(link.id);
    setEditForm({ minSeconds: String(link.minSeconds), maxSeconds: String(link.maxSeconds) });
  };

  const enoughCameras = state.cameras.length >= 2;

  return (
    <DashboardShell user={viewer} role={role}>
      <div className="mx-auto max-w-3xl">

        <header className="flex flex-col gap-2 mb-6">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Camera layout
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            Which camera someone reaches next, and how long the walk takes. This is
            what lets one person be followed from a corridor into a pantry instead of
            becoming two unrelated sightings.
          </p>
        </header>

        {/* The honest caveat, on the page rather than in a footnote. */}
        <div className="mb-5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start gap-2.5">
          <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
            <span className="font-bold text-ink">
              Cross-camera tracking is the least reliable part of this system
            </span>{' '}
            — expect roughly 6 to 8 correct out of 10, against 85–95% for desk time on a
            single camera. When it is unsure it records the person as unattributed rather
            than guessing a name, which is the behaviour you want.
          </p>
        </div>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

        {!canEdit && role && (
          <div className="mb-5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <p className="text-[13px] text-ink-muted font-medium leading-relaxed">
              {denialMessage('cameras.edit')} You can see the layout, but only an
              administrator or manager can change it.
            </p>
          </div>
        )}

        {/* Add a link */}
        {canEdit && (
          <section className="mb-6 rounded-xl border border-line bg-surface p-4 sm:p-5">
            <h2 className="text-[15px] font-black tracking-tight text-ink mb-1 flex items-center gap-2">
              <Plus className="w-4 h-4 text-accent" />
              Add a route
            </h2>
            <p className="text-[12.5px] text-ink-muted font-medium mb-4 leading-relaxed">
              Routes are one-way. If people walk both ways between two cameras, add both
              directions — the walk can genuinely take longer one way than the other.
            </p>

            {state.loading ? (
              <div className="py-4 flex justify-center">
                <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
              </div>
            ) : !enoughCameras ? (
              <p className="text-[13px] text-ink-muted font-medium leading-relaxed">
                You need at least two cameras before you can describe a route between
                them. {state.cameras.length === 1
                  ? `Only "${state.cameras[0].name}" is registered.`
                  : 'No cameras are registered yet.'}
              </p>
            ) : (
              <form onSubmit={submit} className="flex flex-col gap-4">
                <div className="grid sm:grid-cols-[1fr_auto_1fr] gap-3 items-end">
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="fromCameraId" className="text-[13px] font-bold text-ink">
                      They leave
                    </label>
                    <select
                      id="fromCameraId" value={form.fromCameraId}
                      onChange={(e) => setForm({ ...form, fromCameraId: e.target.value })}
                      className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors focus:outline-none focus:border-[color:var(--accent)]"
                    >
                      <option value="">Choose a camera</option>
                      {state.cameras.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  </div>

                  <ArrowRight className="w-4 h-4 text-ink-faint hidden sm:block mb-3.5" />

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="toCameraId" className="text-[13px] font-bold text-ink">
                      They arrive at
                    </label>
                    <select
                      id="toCameraId" value={form.toCameraId}
                      onChange={(e) => setForm({ ...form, toCameraId: e.target.value })}
                      className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors focus:outline-none focus:border-[color:var(--accent)]"
                    >
                      <option value="">Choose a camera</option>
                      {state.cameras.map((c) => {
                        const dup = form.fromCameraId &&
                          taken.has(`${form.fromCameraId}>${c.id}`);
                        const self = c.id === form.fromCameraId;
                        return (
                          <option key={c.id} value={c.id} disabled={dup || self}>
                            {c.name}
                            {self ? ' — same camera' : dup ? ' — already linked' : ''}
                          </option>
                        );
                      })}
                    </select>
                  </div>
                </div>

                <div className="grid sm:grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="minSeconds" className="text-[13px] font-bold text-ink">
                      Fastest walk (seconds)
                    </label>
                    <input
                      id="minSeconds" type="number" inputMode="numeric"
                      min={state.limits.min} max={state.limits.max}
                      value={form.minSeconds}
                      onChange={(e) => setForm({ ...form, minSeconds: e.target.value })}
                      className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors focus:outline-none focus:border-[color:var(--accent)]"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="maxSeconds" className="text-[13px] font-bold text-ink">
                      Slowest walk (seconds)
                    </label>
                    <input
                      id="maxSeconds" type="number" inputMode="numeric"
                      min={state.limits.min} max={state.limits.max}
                      value={form.maxSeconds}
                      onChange={(e) => setForm({ ...form, maxSeconds: e.target.value })}
                      className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors focus:outline-none focus:border-[color:var(--accent)]"
                    />
                  </div>
                </div>

                {/* The one instruction that decides whether this works. */}
                <p className="text-[12px] text-ink-faint font-medium leading-relaxed flex items-start gap-1.5">
                  <Timer className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    Walk it yourself and time it, twice — once hurrying, once dawdling. Too
                    narrow a window rejects real journeys; too wide and two different people
                    walking the same corridor a minute apart get merged into one.
                  </span>
                </p>

                <button
                  type="submit" disabled={busy === 'create'}
                  className="self-start flex items-center gap-2 px-4 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] disabled:opacity-60"
                >
                  {busy === 'create' ? <Loader2 className="w-4 h-4 animate-spin" />
                                     : <Plus className="w-4 h-4" />}
                  Add route
                </button>
              </form>
            )}
          </section>
        )}

        {/* The routes */}
        <section>
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
              <Waypoints className="w-4 h-4 text-accent" />
              Routes
            </h2>
            {!state.loading && (
              <p className="font-mono text-[11px] text-ink-faint">
                {state.links.length} route{state.links.length === 1 ? '' : 's'} ·{' '}
                {state.cameras.length} camera{state.cameras.length === 1 ? '' : 's'}
              </p>
            )}
          </div>

          {state.loading ? (
            <div className="rounded-xl border border-line bg-surface px-4 py-10 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
            </div>
          ) : state.links.length === 0 ? (
            <div className="rounded-xl border border-line bg-surface px-4 py-8 text-center">
              <p className="text-[14px] font-bold text-ink mb-1">No routes yet</p>
              <p className="text-[13px] text-ink-muted font-medium leading-relaxed max-w-sm mx-auto">
                Without a declared route, somebody walking from one camera to another is
                recorded as two separate people. That is deliberate — with no layout to
                check against, there is no evidence they are the same person.
              </p>
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {state.links.map((link) => {
                const isEditing = editing === link.id;
                const isBusy = busy === link.id;
                return (
                  <li key={link.id} className="rounded-xl border border-line bg-surface p-4">
                    <div className="flex items-center gap-3 flex-wrap">
                      <Video className="w-4 h-4 text-ink-faint shrink-0" />
                      <span className="text-[14px] font-black text-ink">
                        {nameOf(link.fromCameraId)}
                      </span>
                      <ArrowRight className="w-4 h-4 text-accent shrink-0" />
                      <span className="text-[14px] font-black text-ink">
                        {nameOf(link.toCameraId)}
                      </span>

                      {!isEditing && (
                        <span className="font-mono text-[11.5px] text-ink-muted ml-auto">
                          {link.minSeconds}–{link.maxSeconds}s
                        </span>
                      )}

                      {canEdit && !isEditing && (
                        <div className="flex items-center gap-1">
                          <button
                            type="button" onClick={() => startEdit(link)} disabled={isBusy}
                            title="Change the walk time"
                            aria-label={`Change the walk time for ${nameOf(link.fromCameraId)} to ${nameOf(link.toCameraId)}`}
                            className="p-2 rounded-lg text-ink-muted hover:text-ink hover:bg-surface-alt transition-colors disabled:opacity-60"
                          >
                            <Timer className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => run(link.id, () => deleteLink(link.id))}
                            disabled={isBusy}
                            title="Remove this route"
                            aria-label={`Remove the route from ${nameOf(link.fromCameraId)} to ${nameOf(link.toCameraId)}`}
                            className="p-2 rounded-lg text-ink-muted hover:text-accent hover:bg-accent-soft transition-colors disabled:opacity-60"
                          >
                            {isBusy ? <Loader2 className="w-4 h-4 animate-spin" />
                                    : <Trash2 className="w-4 h-4" />}
                          </button>
                        </div>
                      )}
                    </div>

                    {isEditing && (
                      <div className="mt-3 flex flex-wrap items-end gap-3">
                        <div className="flex flex-col gap-1.5">
                          <label htmlFor={`min-${link.id}`} className="text-[12px] font-bold text-ink">
                            Fastest (s)
                          </label>
                          <input
                            id={`min-${link.id}`} type="number" inputMode="numeric"
                            min={state.limits.min} max={state.limits.max}
                            value={editForm.minSeconds}
                            onChange={(e) => setEditForm({ ...editForm, minSeconds: e.target.value })}
                            className="w-28 rounded-lg bg-ground border-2 border-field px-3 py-2 text-[14px] text-ink focus:outline-none focus:border-[color:var(--accent)]"
                          />
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <label htmlFor={`max-${link.id}`} className="text-[12px] font-bold text-ink">
                            Slowest (s)
                          </label>
                          <input
                            id={`max-${link.id}`} type="number" inputMode="numeric"
                            min={state.limits.min} max={state.limits.max}
                            value={editForm.maxSeconds}
                            onChange={(e) => setEditForm({ ...editForm, maxSeconds: e.target.value })}
                            className="w-28 rounded-lg bg-ground border-2 border-field px-3 py-2 text-[14px] text-ink focus:outline-none focus:border-[color:var(--accent)]"
                          />
                        </div>
                        <button
                          type="button"
                          onClick={async () => {
                            const ok = await run(link.id, () => updateLink(link.id, editForm));
                            if (ok) setEditing(null);
                          }}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-accent text-white font-bold text-[13px] hover:brightness-110 transition-[filter,opacity] disabled:opacity-60"
                        >
                          {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  : <Check className="w-3.5 h-3.5" />}
                          Save
                        </button>
                        <button
                          type="button" onClick={() => setEditing(null)} disabled={isBusy}
                          className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg border-2 border-field text-ink font-bold text-[13px] hover:border-field-hover transition-colors disabled:opacity-60"
                        >
                          <X className="w-3.5 h-3.5" />
                          Cancel
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <p className="mt-6 text-[12px] text-ink-faint font-medium leading-relaxed">
          A route is checked before appearance is even considered: two cameras with no
          route between them will never be matched, however alike two people look. That is
          what stops a coincidence of clothing becoming a journey nobody made.
        </p>

        <div className="mt-6">
          <Link
            href="/dashboard/employees"
            className="text-[13px] font-bold text-ink-muted hover:text-ink transition-colors"
          >
            ← Employees
          </Link>
        </div>
      </div>
    </DashboardShell>
  );
}
