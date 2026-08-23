'use client';

// frontend/app/settings/organisation/OrganisationSettingsScreen.jsx
//
// Organisation settings.
//
// THE RETENTION FIELD IS NOT A DROPDOWN
//
// Every other field here is a preference. `dataRetentionDays` decides when
// measurements are permanently destroyed, and shortening it means the nightly
// job deletes everything older than the new value the next time it runs.
//
// So the form does not just warn — it COUNTS. Before saving a shortened
// retention it asks the server how many buckets fall outside the new window and
// makes the reader confirm that number. "This will delete 412,908 minutes of
// history" is a decision someone can make; "shortening destroys data" is a
// sentence people click past.
//
// Non-admins see every field, disabled, with one line explaining why. That
// mirrors `org_select` (all members read) against `org_update` (admins write),
// the same split the members screen uses.

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft, Building2, Clock, ShieldAlert, Trash2, Loader2, AlertCircle, Info,
} from 'lucide-react';

import { ThemeToggle } from '../../components/ThemeToggle';
import { Field, Banner } from '../../components/AuthFormBits';
import { can } from '../../lib/permissions';
import {
  getOrganisationSettings, updateOrganisationSettings,
  previewRetentionImpact, deleteOrganisation,
} from './actions';

/** Section wrapper — matches the card treatment on /settings/members. */
function Section({ icon: Icon, title, description, children, danger = false }) {
  return (
    <section
      className={`rounded-xl border p-4 sm:p-5 ${
        danger ? 'border-[color:var(--accent)] bg-accent-soft' : 'border-line bg-surface'
      }`}
    >
      <h2 className={`flex items-center gap-2 text-[15px] font-black tracking-tight mb-1 ${danger ? 'text-accent' : 'text-ink'}`}>
        <Icon className="w-4 h-4" />
        {title}
      </h2>
      {description && (
        <p className="text-[12.5px] text-ink-muted font-medium mb-4 leading-relaxed">
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

export default function OrganisationSettingsScreen() {
  const [state, setState] = useState({ status: 'loading', org: null, role: null });
  const [banner, setBanner] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  const [form, setForm] = useState(null);
  // Set when a shortened retention needs confirming; holds the counted impact.
  const [retentionWarning, setRetentionWarning] = useState(null);

  const [deleteText, setDeleteText] = useState('');
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    const res = await getOrganisationSettings();
    if (!res.ok) {
      setState({ status: 'error', org: null, role: null });
      setBanner({ kind: 'error', text: res.message });
      return;
    }
    setState({ status: 'ready', org: res.organisation, role: res.viewerRole });
    setForm({
      name: res.organisation.name ?? '',
      timezone: res.organisation.timezone ?? 'UTC',
      dataRetentionDays: String(res.organisation.dataRetentionDays ?? 90),
      purgeVideoAfterProcessing: Boolean(res.organisation.purgeVideoAfterProcessing),
      defaultSedentaryThresholdMinutes: String(res.organisation.defaultSedentaryThresholdMinutes ?? 60),
      defaultUtilisationFloorPct: String(res.organisation.defaultUtilisationFloorPct ?? 30),
    });
  }, []);

  useEffect(() => { load(); }, [load]);

  const canEdit = can(state.role, 'org.settings');
  const set = (key) => (value) => {
    setForm((f) => ({ ...f, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
    setRetentionWarning(null);
  };

  const save = async (skipRetentionCheck = false) => {
    setBanner(null);
    setSaving(true);
    try {
      const next = Number(form.dataRetentionDays);
      const current = state.org.dataRetentionDays;

      // Shortening is the destructive direction. Count first, confirm second.
      if (!skipRetentionCheck && Number.isInteger(next) && next < current) {
        const impact = await previewRetentionImpact(next);
        if (impact.ok && impact.atRisk > 0) {
          setRetentionWarning({ ...impact, from: current });
          return;
        }
      }

      const fd = new FormData();
      Object.entries(form).forEach(([k, v]) => fd.set(k, String(v)));

      const res = await updateOrganisationSettings(fd);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }

      setRetentionWarning(null);
      setBanner({ kind: 'success', text: res.message });
      await load();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setBanner(null);
    setDeleting(true);
    try {
      const res = await deleteOrganisation(deleteText);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }
      // The org is gone from this user's view, so there is nothing left to
      // render here. The dashboard guard sends them to onboarding.
      window.location.href = '/onboarding';
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setDeleting(false);
    }
  };

  if (state.status === 'loading' || !form) {
    return (
      <div className="themed min-h-screen bg-ground flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-accent animate-spin" />
      </div>
    );
  }

  const inputClass = `w-full rounded-lg bg-ground border-2 px-3.5 py-2.5 text-[14px] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] border-field hover:border-field-hover disabled:opacity-60 disabled:cursor-not-allowed`;

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white">
      <div className="mx-auto max-w-3xl px-[clamp(1.25rem,4vw,2rem)] py-[clamp(1.25rem,3vh,2.5rem)]">

        <div className="flex items-center justify-between gap-4 mb-8">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 text-[13px] font-bold text-ink-muted hover:text-accent transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to dashboard
          </Link>
          <ThemeToggle />
        </div>

        <header className="flex flex-col gap-2 mb-7">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {state.org.slug}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Organisation
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            Identity, data retention, and the defaults every new zone inherits.
          </p>
        </header>

        {banner && <div className="mb-5"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

        {!canEdit && (
          <div className="mb-6 flex items-start gap-2.5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <Info className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" />
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              Only an administrator can change these settings. You can see them because they
              describe how your organisation’s data is handled.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-5">
          {/* ── Identity ── */}
          <Section
            icon={Building2}
            title="Identity"
            description="The name shown across the product, and the timezone every report is read in."
          >
            <div className="flex flex-col gap-4">
              <Field
                id="orgName"
                label="Organisation name"
                value={form.name}
                onChange={(e) => set('name')(e.target.value)}
                maxLength={160}
                disabled={!canEdit}
                error={errors.name}
              />
              <div className="flex flex-col gap-1.5">
                <label htmlFor="orgTimezone" className="text-[13px] font-bold text-ink">Timezone</label>
                <select
                  id="orgTimezone"
                  value={form.timezone}
                  onChange={(e) => set('timezone')(e.target.value)}
                  disabled={!canEdit}
                  className={inputClass}
                >
                  {(typeof Intl.supportedValuesOf === 'function'
                    ? Intl.supportedValuesOf('timeZone')
                    : [form.timezone, 'UTC'].filter((v, i, a) => a.indexOf(v) === i)
                  ).map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                </select>
                <p className="text-[12px] text-ink-faint font-medium">
                  Buckets are stored in UTC and read in this zone, so “peak hour” means your
                  local peak.
                </p>
              </div>
            </div>
          </Section>

          {/* ── Privacy & retention ── */}
          <Section
            icon={ShieldAlert}
            title="Privacy & retention"
            description="How long measurements are kept, and what happens to uploaded video."
          >
            <div className="flex flex-col gap-4">
              <Field
                id="retention"
                label="Keep measurements for"
                type="number"
                min="1"
                max="730"
                value={form.dataRetentionDays}
                onChange={(e) => set('dataRetentionDays')(e.target.value)}
                disabled={!canEdit}
                error={errors.dataRetentionDays}
                hint="Days, 1–730. A nightly job permanently deletes minute buckets older than this."
              />

              {retentionWarning && (
                <div className="rounded-xl border-2 border-[color:var(--accent)] bg-accent-soft p-4 flex flex-col gap-3">
                  <div className="flex items-start gap-2.5">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
                    <div>
                      <p className="text-[13px] font-black text-accent">
                        This permanently deletes {retentionWarning.atRisk.toLocaleString()} minute
                        {retentionWarning.atRisk === 1 ? '' : 's'} of history
                      </p>
                      <p className="text-[12.5px] text-ink-muted font-medium mt-1 leading-relaxed">
                        Shortening retention from {retentionWarning.from} to{' '}
                        {retentionWarning.days} days puts every bucket recorded before{' '}
                        <strong className="text-ink">
                          {new Date(retentionWarning.cutoff).toLocaleDateString()}
                        </strong>{' '}
                        outside the window. The nightly job will delete them, and they cannot be
                        recovered.
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={() => save(true)}
                      disabled={saving}
                      className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-[13px] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-60"
                    >
                      {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      Yes, shorten retention
                    </button>
                    <button
                      type="button"
                      onClick={() => setRetentionWarning(null)}
                      className="rounded-lg border border-line bg-surface px-3.5 py-2 text-[13px] font-bold text-ink-muted hover:text-ink transition-colors"
                    >
                      Keep {retentionWarning.from} days
                    </button>
                  </div>
                </div>
              )}

              <label className={`flex items-start gap-2.5 rounded-lg border-2 px-3 py-2.5 transition-colors ${
                canEdit ? 'cursor-pointer border-field hover:border-field-hover' : 'border-field opacity-60'
              }`}>
                <input
                  type="checkbox"
                  checked={form.purgeVideoAfterProcessing}
                  onChange={(e) => set('purgeVideoAfterProcessing')(e.target.checked)}
                  disabled={!canEdit}
                  className="mt-0.5 accent-[color:var(--accent)]"
                />
                <span className="flex flex-col gap-0.5 min-w-0">
                  <span className="text-[13px] font-bold text-ink">
                    Delete uploaded video after processing
                  </span>
                  <span className="text-[12px] text-ink-faint font-medium leading-snug">
                    Recommended. The frames are the sensitive artefact; the buckets are not.
                    Turning this off keeps footage on disk.
                  </span>
                </span>
              </label>
            </div>
          </Section>

          {/* ── Defaults ── */}
          <Section
            icon={Clock}
            title="Zone defaults"
            description="Inherited by every new zone, so policy is set once rather than per zone."
          >
            <div className="grid sm:grid-cols-2 gap-4">
              <Field
                id="sedentary"
                label="Sedentary threshold"
                type="number"
                min="1"
                max="1440"
                value={form.defaultSedentaryThresholdMinutes}
                onChange={(e) => set('defaultSedentaryThresholdMinutes')(e.target.value)}
                disabled={!canEdit}
                hint="Minutes of sustained sitting before a zone is flagged."
              />
              <Field
                id="floor"
                label="Utilisation floor"
                type="number"
                min="0"
                max="100"
                value={form.defaultUtilisationFloorPct}
                onChange={(e) => set('defaultUtilisationFloorPct')(e.target.value)}
                disabled={!canEdit}
                hint="Percent. Below this across a window, a zone reads as underused."
              />
            </div>
          </Section>

          {canEdit && !retentionWarning && (
            /* A plain button, not SubmitButton: that component is
               `type="submit"` and ignores onClick, so using it outside a <form>
               would render a control that silently does nothing. */
            <button
              type="button"
              onClick={() => save(false)}
              disabled={saving}
              className="w-full flex items-center justify-center gap-2 px-3.5 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          )}

          {/* ── Danger zone ── */}
          {canEdit && (
            <Section
              icon={Trash2}
              title="Delete this organisation"
              description="Members lose access immediately. Measurements and audit history are kept for a grace period, so this can be reversed by support — but nobody in your team will be able to reach the data."
              danger
            >
              <div className="flex flex-col gap-3">
                <Field
                  id="confirmDelete"
                  label={`Type “${state.org.name}” to confirm`}
                  value={deleteText}
                  onChange={(e) => setDeleteText(e.target.value)}
                  placeholder={state.org.name}
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={remove}
                  // A confirm dialog is dismissed by reflex; typing the name is not.
                  disabled={deleting || deleteText.trim() !== state.org.name}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-3.5 py-2.5 text-[13.5px] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                  Delete organisation
                </button>
              </div>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
