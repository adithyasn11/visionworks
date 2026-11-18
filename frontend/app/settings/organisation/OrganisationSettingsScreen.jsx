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

import { daysText, planName, termDate } from '../../lib/plans';
import React, { useCallback, useEffect, useState } from 'react';
import {
  Building2, ShieldAlert, Trash2, Loader2, AlertCircle, Info, CreditCard, Globe2, Archive, Film, Check,
} from 'lucide-react';

import { Field, Banner } from '../../components/AuthFormBits';
import { can } from '../../lib/permissions';
import DashboardShell from '../../dashboard/DashboardShell';
import { Skeleton } from '../../dashboard/safety/common';
import {
  getOrganisationSettings, updateOrganisationSettings,
  previewRetentionImpact, deleteOrganisation,
} from './actions';

/** Section wrapper — matches the card treatment on /settings/members. */
function Section({ icon: Icon, title, description, children, danger = false, className = '' }) {
  return (
    <section
      className={`rounded-2xl border p-5 sm:p-6 flex flex-col min-w-0 ${
        danger ? 'border-[color:var(--accent)] bg-accent-soft' : 'border-line bg-surface'
      } ${className}`}
    >
      <h2 className={`flex items-center gap-2 text-[0.9375rem] font-black tracking-tight mb-1 ${danger ? 'text-accent' : 'text-ink'}`}>
        <Icon className="w-4 h-4" />
        {title}
      </h2>
      {description && (
        <p className="text-[0.78125rem] text-ink-muted font-medium mb-4 leading-relaxed">
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

export default function OrganisationSettingsScreen({ role: viewerRole, viewer }) {
  const [state, setState] = useState({ status: 'loading', org: null, role: null });
  const [banner, setBanner] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  // The result banner sits at the top of the page and Save at the bottom: bring the banner into view, or the
  // person who just clicked Save sees nothing happen.
  useEffect(() => {
    if (banner) window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [banner]);

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
    setState({ status: 'ready', org: res.organisation, role: res.viewerRole, historyLimit: res.historyLimit ?? null });
    setForm({
      name: res.organisation.name ?? '',
      timezone: res.organisation.timezone ?? 'UTC',
      dataRetentionDays: String(res.organisation.dataRetentionDays ?? 90),
      purgeVideoAfterProcessing: Boolean(res.organisation.purgeVideoAfterProcessing),
    });
  }, []);

  useEffect(() => { load(); }, [load]);

  const canEdit = can(state.role, 'org.settings');
  const dirty = Boolean(form && state.org && (
    form.name !== (state.org.name ?? '') || form.timezone !== (state.org.timezone ?? 'UTC')
    || String(form.dataRetentionDays) !== String(state.org.dataRetentionDays ?? 90)
    || Boolean(form.purgeVideoAfterProcessing) !== Boolean(state.org.purgeVideoAfterProcessing)));
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

  // Error BEFORE loading, and never `!form` on its own.
  //
  // The first version spun forever on failure: `getOrganisationSettings()`
  // would return an error, `state.status` became 'error', but `form` was still
  // null — so `state.status === 'loading' || !form` stayed true and the
  // spinner never resolved. The user saw an infinite load with no explanation.
  //
  // That is exactly what happened after deleting an organisation: the profile
  // still pointed at it, `org_select` hides deleted rows, the read returned
  // nothing, and the page hung.
  if (state.status === 'error') {
    return (
      <DashboardShell user={viewer} role={viewerRole}>
        <div className="mx-auto max-w-lg py-16 text-center">
          <AlertCircle className="w-6 h-6 mx-auto text-accent" />
          <h1 className="text-[1.0625rem] font-black tracking-tight text-ink mt-3">
            Could not load organisation settings
          </h1>
          <p className="text-[0.8125rem] text-ink-muted mt-2 leading-relaxed">
            {banner?.text ?? 'Something went wrong.'}
          </p>
          <a
            href="/dashboard"
            className="inline-flex items-center gap-2 mt-5 rounded-lg bg-accent px-3.5 py-2 text-[0.8125rem] font-bold text-white hover:brightness-110 transition-[filter]"
          >
            Back to dashboard
          </a>
        </div>
      </DashboardShell>
    );
  }

  if (state.status === 'loading' || !form) {
    return (
      <DashboardShell user={viewer} role={viewerRole}>
        <div className="space-y-6">
          <div className="pb-5 border-b border-line space-y-3" aria-hidden="true">
            <div className="skeleton h-2.5 w-20" /><div className="skeleton h-8 w-56" /><div className="skeleton h-3 w-80 max-w-full" />
          </div>
          <div className="glass-panel p-6"><Skeleton rows={4} label="Loading organisation settings" /></div>
          <div className="glass-panel p-6"><Skeleton rows={3} label="Loading" /></div>
        </div>
      </DashboardShell>
    );
  }

  const inputClass = `w-full rounded-lg bg-ground border-2 px-3.5 py-2.5 text-[0.875rem] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] border-field hover:border-field-hover disabled:opacity-60 disabled:cursor-not-allowed`;

  return (
    /* Same shell as the dashboard — see MembersScreen for why. */
    <DashboardShell user={viewer} role={viewerRole}>
      <div className="animate-view-in flex flex-col gap-5">

        {/* Same page header as the dashboard screens (eyebrow, title, line of context, rule underneath). */}
        <header className="flex flex-wrap items-end justify-between gap-4 pb-4 border-b border-line">
          <div className="flex flex-col gap-2">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-accent">
            {state.org.name || state.org.slug}
          </p>
          <h1 className="text-[1.625rem] sm:text-[1.875rem] font-black tracking-tight text-ink leading-[1.15]">
            Organisation
          </h1>
          <p className="text-[0.84375rem] text-ink-muted">Its name, its clock, and how long its data is kept.</p>
          </div>
          {canEdit && !retentionWarning && (
          <div className="flex items-center gap-3">
            <span className={`text-[0.8125rem] font-bold ${dirty ? 'text-accent' : 'text-ink-faint'}`}>{dirty ? 'Not saved yet' : 'All saved'}</span>
            <button
              type="button"
              onClick={() => save(false)}
              disabled={saving || !dirty}
              className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-accent text-white font-bold text-[0.875rem] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          </div>
        )}
        </header>

        {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

        {!canEdit && (
          <div className="flex items-start gap-2.5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <Info className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" />
            <p className="text-[0.78125rem] text-ink-muted font-medium leading-relaxed">
              Only an administrator can change these settings. You can see them because they
              describe how your organisation’s data is handled.
            </p>
          </div>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { icon: CreditCard, label: 'Plan', value: planName(state.org.plan), href: '/dashboard?view=plan' },
            { icon: Globe2, label: 'Time zone', value: state.org.timezone || 'UTC' },
            { icon: Archive, label: 'Records kept', value: `${state.org.dataRetentionDays} days` },
            { icon: Film, label: 'Uploaded video', value: state.org.purgeVideoAfterProcessing ? 'Deleted after use' : 'Kept' },
          ].map(({ icon: Icon, label, value, href }) => {
            const body = (
              <>
                <span className="w-11 h-11 rounded-xl bg-accent-soft text-accent flex items-center justify-center shrink-0"><Icon className="w-5 h-5" aria-hidden="true" /></span>
                <span className="min-w-0">
                  <span className="block text-[1.125rem] font-black text-ink leading-tight break-words" title={value}>{value}</span>
                  <span className="block mt-0.5 text-[0.8125rem] font-bold text-ink-muted leading-tight">{label}</span>
                </span>
              </>
            );
            const cls = 'rounded-2xl border border-line bg-surface p-5 flex items-center gap-4 min-w-0';
            return href
              ? <a key={label} href={href} className={`${cls} hover:border-[color:var(--accent)] transition-colors`}>{body}</a>
              : <div key={label} className={cls}>{body}</div>;
          })}
        </div>

        <div className="flex flex-col gap-5">
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5 items-stretch">
          {/* ── Identity ── */}
          <Section
            icon={Building2}
            title="Identity"
            description="The name everyone sees, and the clock reports use."
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
                <label htmlFor="orgTimezone" className="text-[0.8125rem] font-bold text-ink">Timezone</label>
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
                <p className="text-[0.75rem] text-ink-faint font-medium">Alerts and reports show times in this zone.</p>
              </div>
            </div>
          </Section>

          {/* ── Privacy & retention ── */}
          <Section
            icon={ShieldAlert}
            title="Privacy & retention"
            description="How long records are kept, and uploaded video."
          >
            <div className="flex flex-col gap-4">
              <Field
                id="retention"
                label="Keep records for"
                type="number"
                min="1"
                max={String(Math.max(state.historyLimit?.max ?? 730, Number(state.org?.dataRetentionDays) || 0))}
                value={form.dataRetentionDays}
                onChange={(e) => set('dataRetentionDays')(e.target.value)}
                disabled={!canEdit}
                error={errors.dataRetentionDays}
                hint={`Days, 1–${state.historyLimit?.max ?? 730}${state.historyLimit ? ` on ${planName(state.historyLimit.plan)}` : ''}. Older records are deleted each night.`}
              />

              {retentionWarning && (
                <div className="rounded-xl border-2 border-[color:var(--accent)] bg-accent-soft p-4 flex flex-col gap-3">
                  <div className="flex items-start gap-2.5">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
                    <div>
                      <p className="text-[0.8125rem] font-black text-accent">
                        This permanently deletes {retentionWarning.atRisk.toLocaleString()} record
                        {retentionWarning.atRisk === 1 ? '' : 's'} of history
                      </p>
                      <p className="text-[0.78125rem] text-ink-muted font-medium mt-1 leading-relaxed">
                        Shortening retention from {retentionWarning.from} to{' '}
                        {retentionWarning.days} days puts every record made before{' '}
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
                      className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-[0.8125rem] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-60"
                    >
                      {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      Yes, shorten retention
                    </button>
                    <button
                      type="button"
                      onClick={() => setRetentionWarning(null)}
                      className="rounded-lg border border-line bg-surface px-3.5 py-2 text-[0.8125rem] font-bold text-ink-muted hover:text-ink transition-colors"
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
                  <span className="text-[0.8125rem] font-bold text-ink">
                    Delete uploaded video after processing
                  </span>
                  <span className="text-[0.75rem] text-ink-faint font-medium leading-snug">
                    Recommended: the footage is the sensitive part. Off keeps it on disk.
                  </span>
                </span>
              </label>
            </div>
          </Section>

          {/* Plan lives in the dashboard (Plan section), not here.
              Two surfaces showing the same tier is two places to keep honest,
              and the tier is read-only in both — there was nothing to configure
              on this page. The sidebar's Plan entry is one click away. */}

          {/* ── Danger zone ── */}
          {canEdit && (
            <Section
              icon={Trash2}
              title="Delete organisation"
              description="Everyone loses access at once. Support can restore it for a short while."
              danger
            >
              <div className="flex-1 flex flex-col gap-3">
                {/* Paid time is not lost: it stays with this administrator's account for their next organisation (032). */}
                {(() => {
                  const end = state.org.plan && state.org.plan !== 'FREE' ? state.org.planRenewsAt : null;
                  const left = end ? Math.ceil((new Date(end).getTime() - Date.now()) / 86400000) : 0;
                  return left > 0 ? (
                    <p className="text-[0.78125rem] text-ink-muted leading-relaxed"
                      title={`The next organisation you create continues them until ${termDate(end)}, with nothing to pay again.`}>
                      Your {daysText(left)} of {planName(state.org.plan)} stay with your account.
                    </p>
                  ) : null;
                })()}
                <Field
                  id="confirmDelete"
                  label={`Type “${state.org.name}” to confirm`}
                  value={deleteText}
                  onChange={(e) => setDeleteText(e.target.value)}
                  // No placeholder: showing the name here made the box look already filled in.
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={remove}
                  // A confirm dialog is dismissed by reflex; typing the name is not.
                  disabled={deleting || deleteText.trim() !== state.org.name}
                  className="mt-auto inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-3.5 py-2.5 text-[0.84375rem] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-40 disabled:cursor-not-allowed"
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
    </DashboardShell>
  );
}
