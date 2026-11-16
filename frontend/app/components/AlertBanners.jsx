'use client';

// frontend/app/components/AlertBanners.jsx
//
// A new alert pops up at the bottom right of the screen (never over the video): what was broken, and who and where.
// Silent, cleared after a few seconds, held while the pointer or focus is on it. The full sentence is in the
// tooltip and in the Review queue; the alert is also listed in "Safety decisions" for the length of the run.
// In full screen the pop-ups sit in the corner of the picture, the only place still on screen.

import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, X } from 'lucide-react';
import { severityMeta } from '../lib/alerts';
import { workerLabel } from '../lib/zoneEvents';
import { ruleTypeMeta } from '../lib/safety/ruleTypes';

const TONE = {
  critical: 'bg-accent text-white border-[color:var(--accent)]',
  warning: 'bg-surface text-ink border-[color:var(--accent)]',
  info: 'bg-surface text-ink border-line',
};

export default function AlertBanners({ banners, zones = [], onDismiss, onHold, inside = false }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
  const byId = new Map((zones || []).map((z) => [String(z.zone_id), z]));
  const list = banners || [];

  const region = (
    <div
      className={`${inside ? 'absolute bottom-3 right-3' : 'fixed bottom-4 right-4'} z-[60] w-[min(20rem,calc(100vw-2rem))] flex flex-col gap-2 ${list.length === 0 ? 'pointer-events-none' : ''}`}
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
        const where = (zone && (zone.zone_name || zone.zone_id)) || (b.zone_id && b.zone_id !== 'TRANSIT_ZONE' ? b.zone_id : null);
        const what = (ruleTypeMeta(b.rule_type) || {}).label || 'Potential violation';
        return (
          <div key={b.id} role="group" aria-label="New alert" title={b.reason}
            className={`flex items-start gap-2.5 rounded-xl border px-3 py-2.5 shadow-lg ${TONE[meta.tone]}`}>
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-[0.8125rem] leading-snug">
              <p className="font-bold">{what}</p>
              <p className="opacity-90 truncate">{[workerLabel(b.worker_key), where].filter(Boolean).join(' · ')}</p>
            </div>
            <button
              type="button"
              onClick={() => onDismiss(b.id)}
              aria-label="Dismiss"
              className="shrink-0 rounded p-0.5 opacity-80 hover:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
  // On <body>: the dashboard's animated sections would otherwise hold a fixed box inside them.
  if (inside || !mounted) return inside ? region : null;
  return createPortal(region, document.body);
}
