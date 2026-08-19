'use client';

// frontend/app/platform/components/RevokeOperatorButton.jsx
//
// Revoke control for one operator row.
//
// Confirmation is required, and revoking YOURSELF asks for more: you are about
// to lose access to the page you are standing on, and the only way back is the
// SQL editor. That asymmetry is deliberate — most destructive-action dialogs
// treat every target the same, and the one that deserves extra weight is the
// one that logs you out.
//
// The database still refuses to remove the final operator regardless of what
// this component allows, so the UI is a courtesy layer over a hard guard.

import React, { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { ShieldOff, Loader2, AlertTriangle, X } from 'lucide-react';
import { revokeOperator } from '../operators/actions';

export default function RevokeOperatorButton({ operator, activeCount }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);

  // Mirrors the database guard so the button explains itself rather than
  // producing an error only after being clicked.
  const isLastActive = operator.isActive && activeCount <= 1;

  const run = () => {
    setError(null);
    startTransition(async () => {
      const r = await revokeOperator(operator.profileId);
      if (r.ok) {
        setOpen(false);
        // Revoking yourself removes access to /platform entirely; refresh lets
        // the layout guard redirect rather than leaving a dead page on screen.
        router.refresh();
      } else {
        setError(r.message);
      }
    });
  };

  if (!operator.isActive) {
    return (
      <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint whitespace-nowrap">
        revoked
      </span>
    );
  }

  if (isLastActive) {
    return (
      <span
        title="The database refuses to revoke the final operator: access can only be granted back from the SQL editor."
        className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-alt px-2.5 py-1.5 text-[11.5px] font-bold text-ink-faint cursor-not-allowed whitespace-nowrap"
      >
        <ShieldOff className="w-3.5 h-3.5" />
        Last operator
      </span>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[11.5px] font-bold text-ink-muted hover:text-accent hover:border-[color:var(--accent)] transition-colors whitespace-nowrap"
      >
        <ShieldOff className="w-3.5 h-3.5" />
        Revoke
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-[color:var(--accent)] bg-accent-soft p-3 max-w-sm">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-accent" strokeWidth={2.2} />
        <div className="min-w-0">
          <p className="text-[12.5px] font-bold text-accent leading-snug">
            {operator.isSelf
              ? 'Revoke your own access?'
              : `Revoke ${operator.email ?? 'this operator'}?`}
          </p>
          <p className="text-[11.5px] text-ink-muted leading-relaxed mt-1">
            {operator.isSelf
              ? 'You will lose access to this console immediately. Only someone with the database password can grant it back.'
              : 'They lose console access immediately. The record is kept so past actions stay attributable.'}
          </p>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-2 text-[11.5px] font-bold text-accent leading-relaxed">
          {error}
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2">
        <button
          type="button"
          onClick={run}
          disabled={pending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-bold text-white hover:brightness-110 transition-[filter] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {pending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldOff className="w-3.5 h-3.5" />}
          {operator.isSelf ? 'Revoke my access' : 'Revoke'}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setError(null); }}
          disabled={pending}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-[12px] font-bold text-ink-muted hover:text-ink transition-colors"
        >
          <X className="w-3.5 h-3.5" />
          Cancel
        </button>
      </div>
    </div>
  );
}
