'use client';

// frontend/app/platform/components/OrgActions.jsx
//
// Suspend/restore and retention controls.
//
// Suspension requires typing the organisation's name. That is deliberate
// friction: it takes a customer's whole account offline, and a misplaced click
// on a support call should not be able to do it. Retention is a number field
// with the same bounds as the CHECK constraint, so the UI rejects what the
// database would reject anyway — but with a readable message.

import React, { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import {
  PauseCircle, PlayCircle, Clock, Loader2, AlertTriangle, Check, X,
} from 'lucide-react';
import { setSuspended, setRetentionDays } from '../organisations/[id]/actions';

function Result({ result }) {
  if (!result) return null;
  const ok = result.ok;
  return (
    <p
      role="status"
      className={`flex items-center gap-1.5 text-[12px] font-bold ${ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-accent'}`}
    >
      {ok ? <Check className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
      {ok ? (result.unchanged ? 'No change needed.' : 'Saved.') : result.error}
    </p>
  );
}

export default function OrgActions({ org }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [suspendResult, setSuspendResult] = useState(null);

  const [days, setDays] = useState(String(org.retentionDays));
  const [retentionResult, setRetentionResult] = useState(null);

  const nameMatches = confirmText.trim() === org.name;

  const runSuspend = (next) => {
    setSuspendResult(null);
    startTransition(async () => {
      const r = await setSuspended(org.id, next);
      setSuspendResult(r);
      if (r.ok) {
        setConfirmOpen(false);
        setConfirmText('');
        router.refresh();
      }
    });
  };

  const runRetention = (e) => {
    e.preventDefault();
    setRetentionResult(null);
    startTransition(async () => {
      const r = await setRetentionDays(org.id, Number(days));
      setRetentionResult(r);
      if (r.ok) router.refresh();
    });
  };

  return (
    <section className="rounded-xl border border-line bg-surface themed">
      <div className="px-4 sm:px-5 py-3.5 border-b border-line">
        <h2 className="text-[14px] font-bold tracking-tight text-ink">Actions</h2>
        <p className="text-[11.5px] text-ink-faint mt-0.5">
          Every change here is written to the platform audit log.
        </p>
      </div>

      <div className="divide-y divide-[color:var(--line)]">
        {/* ── Retention ── */}
        <form onSubmit={runRetention} className="px-4 sm:px-5 py-4 space-y-2.5">
          <div className="flex items-start gap-2">
            <Clock className="w-4 h-4 mt-0.5 shrink-0 text-ink-faint" strokeWidth={2.2} />
            <div className="min-w-0">
              <label htmlFor="retention" className="text-[13px] font-bold text-ink block">
                Data retention
              </label>
              <p className="text-[11.5px] text-ink-muted leading-relaxed mt-0.5">
                Minute buckets older than this are deleted by the nightly job.
                Shortening it destroys data on the next run.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 pl-6">
            <input
              id="retention"
              type="number"
              min={1}
              max={730}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              disabled={pending}
              className="w-24 rounded-lg border-2 border-field bg-ground px-3 py-1.5 text-[13px] tabular-nums text-ink hover:border-field-hover focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] transition-colors disabled:opacity-50"
            />
            <span className="text-[12.5px] text-ink-muted">days</span>
            <button
              type="submit"
              disabled={pending || Number(days) === org.retentionDays}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[12.5px] font-bold text-ink hover:border-field transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {pending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Save
            </button>
          </div>
          <div className="pl-6"><Result result={retentionResult} /></div>
        </form>

        {/* ── Suspend / restore ── */}
        <div className="px-4 sm:px-5 py-4 space-y-2.5">
          <div className="flex items-start gap-2">
            {org.isSuspended
              ? <PlayCircle className="w-4 h-4 mt-0.5 shrink-0 text-ink-faint" strokeWidth={2.2} />
              : <PauseCircle className="w-4 h-4 mt-0.5 shrink-0 text-ink-faint" strokeWidth={2.2} />}
            <div className="min-w-0">
              <h3 className="text-[13px] font-bold text-ink">
                {org.isSuspended ? 'Restore access' : 'Suspend organisation'}
              </h3>
              <p className="text-[11.5px] text-ink-muted leading-relaxed mt-0.5">
                {org.isSuspended
                  ? 'Members will be able to sign in and see their data again.'
                  : 'Members lose access immediately. No data is deleted, and the change is reversible.'}
              </p>
            </div>
          </div>

          <div className="pl-6">
            {org.isSuspended ? (
              <button
                type="button"
                onClick={() => runSuspend(false)}
                disabled={pending}
                className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[12.5px] font-bold text-ink hover:border-field transition-colors disabled:opacity-50"
              >
                {pending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlayCircle className="w-3.5 h-3.5" />}
                Restore access
              </button>
            ) : !confirmOpen ? (
              <button
                type="button"
                onClick={() => setConfirmOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border-2 border-[color:var(--accent)] bg-transparent px-3 py-1.5 text-[12.5px] font-bold text-accent hover:bg-accent-soft transition-colors"
              >
                <PauseCircle className="w-3.5 h-3.5" />
                Suspend…
              </button>
            ) : (
              <div className="rounded-lg border border-[color:var(--accent)] bg-accent-soft p-3 space-y-2.5">
                <p className="text-[12px] font-bold text-accent leading-relaxed">
                  Type <span className="font-mono">{org.name}</span> to confirm.
                </p>
                <input
                  type="text"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  autoComplete="off"
                  aria-label={`Type ${org.name} to confirm suspension`}
                  className="w-full rounded-lg border-2 border-field bg-ground px-3 py-1.5 text-[13px] text-ink focus:outline-none focus:border-[color:var(--accent)] transition-colors"
                />
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => runSuspend(true)}
                    disabled={!nameMatches || pending}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {pending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PauseCircle className="w-3.5 h-3.5" />}
                    Suspend
                  </button>
                  <button
                    type="button"
                    onClick={() => { setConfirmOpen(false); setConfirmText(''); }}
                    disabled={pending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[12.5px] font-bold text-ink-muted hover:text-ink transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                    Cancel
                  </button>
                </div>
              </div>
            )}
            <div className="mt-2"><Result result={suspendResult} /></div>
          </div>
        </div>
      </div>
    </section>
  );
}
