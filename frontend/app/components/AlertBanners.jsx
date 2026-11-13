'use client';

// frontend/app/components/AlertBanners.jsx
//
// The banner a new alert raises over the live view: one entry per alert, coloured by the
// rule's severity, silent (no sound), and cleared after a few seconds. The alert is ALSO
// listed in the "Safety decisions" panel for the length of the run, and under "Open alerts"
// once it has reached the database, so a banner that clears is never the only record.
//
// The region is always mounted (a live region that appears together with its content is
// announced unreliably), announces only what is ADDED, and pauses its countdown while the
// pointer or keyboard focus is on it, so a banner cannot vanish while someone is reading or
// reaching for its Dismiss button.

import React from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { severityMeta } from '../lib/alerts';
import { workerLabel } from '../lib/zoneEvents';

const TONE = {
  // Critical is the loudest thing on the screen: solid red.
  critical: 'bg-accent text-white border-[color:var(--accent)]',
  // Warning: red outline on the dark surface.
  warning: 'bg-surface text-ink border-[color:var(--accent)]',
  // Info: neutral.
  info: 'bg-surface text-ink border-line',
};

export default function AlertBanners({ banners, zones = [], flaggedCount = 0, onDismiss, onHold }) {
  const byId = new Map((zones || []).map((z) => [String(z.zone_id), z]));
  const list = banners || [];

  return (
    <div
      className={`absolute top-2 left-2 right-2 z-20 flex flex-col gap-2 ${list.length === 0 ? 'pointer-events-none' : ''}`}
      aria-live="assertive"
      aria-atomic="false"
      aria-relevant="additions"
      onMouseEnter={() => onHold && onHold(true)}
      onMouseLeave={() => onHold && onHold(false)}
      onFocus={() => onHold && onHold(true)}
      onBlur={() => onHold && onHold(false)}
    >
      {list.map((b) => {
        const meta = severityMeta(b.severity);
        const zone = byId.get(String(b.zone_id));
        return (
          <div key={b.id} role="group" aria-label="New alert" className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 shadow-md ${TONE[meta.tone]}`}>
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-xs leading-relaxed">
              <p className="font-bold">
                Potential violation <span className="font-mono text-[0.625rem] uppercase tracking-[0.12em] opacity-80">· {meta.label}</span>
              </p>
              <p className="opacity-95">
                {(zone && (zone.zone_name || zone.zone_id)) || b.zone_id || 'Zone'} · {workerLabel(b.worker_key)} — {b.reason}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onDismiss(b.id)}
              aria-label="Dismiss this banner"
              className="shrink-0 rounded p-0.5 opacity-80 hover:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-white"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
      {list.length > 0 && (
        <p className="self-start rounded bg-ground/80 px-2 py-0.5 text-[0.625rem] font-mono uppercase tracking-[0.12em] text-ink-muted">
          {flaggedCount} flagged in this run · for review, not a final verdict · listed below
        </p>
      )}
    </div>
  );
}
