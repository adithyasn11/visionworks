'use client';

// frontend/app/dashboard/AlertsPanel.jsx
//
// The alerts feed.
//
// DESIGN: ONE THING IS ALLOWED TO BE RED
//
// The dashboard already spends its accent on a single sedentary tile, on the
// reasoning that colour appearing everywhere means nothing. This panel keeps
// that discipline: only CRITICAL alerts take the accent, WARNING takes amber,
// and INFO stays neutral. A feed where every row shouts is a feed people mute.
//
// Acknowledged alerts stay visible, dimmed. They are not gone — somebody still
// has to deal with them — and a list that empties on acknowledgement trains
// people to acknowledge in order to clear the badge.
//
// Acknowledging is gated on `analysis.run` (ADMIN + MANAGER). A VIEWER sees the
// alerts, because knowing the space is overcrowded is not a privileged fact,
// but cannot act on them. The server action re-checks (layer 2) and
// `alert_update` requires the org in `user_org_ids()` (layer 3).

import React, { useCallback, useEffect, useState } from 'react';
import { BellRing, Check, Loader2, ShieldAlert, Info, AlertTriangle } from 'lucide-react';

import { listAlerts, acknowledgeAlert } from '../lib/analytics/alerts';
import { can } from '../lib/permissions';

const SEVERITY = {
  CRITICAL: {
    icon: ShieldAlert,
    chip: 'border-[color:var(--accent)] bg-accent-soft text-accent',
    label: 'Critical',
  },
  WARNING: {
    icon: AlertTriangle,
    chip: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
    label: 'Warning',
  },
  INFO: {
    icon: Info,
    chip: 'border-[color:var(--line)] bg-surface-alt text-ink-muted',
    label: 'Info',
  },
};

/** Absolute date-time. A relative label goes stale in a tab left open. */
function formatWhen(value) {
  if (!value) return null;
  try {
    return new Date(value).toLocaleString(undefined, {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return null;
  }
}

function AlertRow({ alert, canAck, busyId, onAcknowledge }) {
  const meta = SEVERITY[alert.severity] ?? SEVERITY.INFO;
  const Icon = meta.icon;
  const busy = busyId === alert.id;
  const acknowledged = alert.state === 'ACKNOWLEDGED';

  return (
    <li
      className={`flex items-start gap-3 px-4 py-3 border-b border-line last:border-b-0 transition-opacity ${
        acknowledged ? 'opacity-60' : ''
      }`}
    >
      <span className={`shrink-0 mt-0.5 inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-black uppercase tracking-wider ${meta.chip}`}>
        <Icon className="w-3 h-3" />
        {meta.label}
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold text-ink leading-snug">{alert.message}</p>
        <p className="text-[11.5px] text-ink-faint font-medium mt-1">
          {alert.ruleName}
          {alert.zone ? ` · ${alert.zone}` : ''}
          {' · '}
          {formatWhen(alert.triggeredAt)}
          {acknowledged && ' · acknowledged'}
        </p>
      </div>

      {canAck && !acknowledged && (
        <button
          type="button"
          onClick={() => onAcknowledge(alert.id)}
          disabled={busy}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12px] font-bold text-ink-muted hover:text-ink hover:border-field transition-colors disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          Acknowledge
        </button>
      )}
    </li>
  );
}

export default function AlertsPanel({ role }) {
  const [state, setState] = useState({ status: 'loading', alerts: [], openCount: 0, error: null });
  const [busyId, setBusyId] = useState(null);

  const canAck = can(role, 'analysis.run');

  const load = useCallback(async () => {
    try {
      const res = await listAlerts();
      if (!res.ok) {
        setState({ status: 'error', alerts: [], openCount: 0, error: res.message });
        return;
      }
      setState({ status: 'ready', alerts: res.alerts, openCount: res.openCount, error: null });
    } catch (err) {
      setState({ status: 'error', alerts: [], openCount: 0, error: String(err?.message || err) });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const acknowledge = async (alertId) => {
    setBusyId(alertId);
    try {
      const res = await acknowledgeAlert(alertId);
      if (!res.ok) {
        setState((s) => ({ ...s, error: res.message }));
        return;
      }
      // Re-read rather than patching local state: acknowledging re-arms the
      // rule, so the server is the only place that knows what the feed looks
      // like now.
      await load();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="glass-panel overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line">
        <h2 className="flex items-center gap-2 text-[13.5px] font-bold text-ink tracking-tight">
          <BellRing className="w-4 h-4 text-accent" />
          Alerts
          {state.openCount > 0 && (
            <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-accent text-white text-[11px] font-black">
              {state.openCount}
            </span>
          )}
        </h2>
        {state.status === 'ready' && state.alerts.length > 0 && (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            {state.openCount} open
          </span>
        )}
      </header>

      {state.status === 'loading' ? (
        <div className="px-4 py-10 text-center">
          <Loader2 className="w-5 h-5 mx-auto text-accent animate-spin" />
          <p className="text-[12.5px] text-ink-muted mt-2.5">Loading alerts…</p>
        </div>
      ) : state.status === 'error' ? (
        <div className="px-4 py-6 text-center">
          <p className="text-[12.5px] text-ink-muted">{state.error}</p>
        </div>
      ) : state.alerts.length === 0 ? (
        <div className="px-4 py-10 text-center">
          {/* An empty feed is the good outcome, so it should read as one
              rather than as a missing panel. */}
          <Check className="w-5 h-5 mx-auto text-emerald-500" />
          <p className="text-[13px] font-bold text-ink mt-2.5">Nothing needs attention</p>
          <p className="text-[12px] text-ink-faint mt-1 max-w-xs mx-auto leading-relaxed">
            Rules are evaluated every minute against new measurements. Anything they
            catch appears here.
          </p>
        </div>
      ) : (
        <ul>
          {state.alerts.map((alert) => (
            <AlertRow
              key={alert.id}
              alert={alert}
              canAck={canAck}
              busyId={busyId}
              onAcknowledge={acknowledge}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
