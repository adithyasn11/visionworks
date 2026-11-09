'use client';

// frontend/app/components/ZoneActivity.jsx
//
// The live list of who entered and left which zone, fed by the safety layer's
// zone events. It appears only when the backend is running the safety layer
// (the frame payload then carries a `safety` block); with it off the player
// looks exactly as before.

import React from 'react';
import { MapPin } from 'lucide-react';
import { describeZoneEvent } from '../lib/zoneEvents';
import { zoneTypeMeta } from '../lib/zoneTypes';

function clock(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return '00:00';
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function ZoneActivity({ events, zones = [] }) {
  const byId = new Map((zones || []).map((z) => [String(z.zone_id), z]));

  return (
    <section className="glass-card p-3 flex flex-col gap-2.5" aria-label="Zone activity">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex items-center gap-2 text-ink text-sm font-semibold">
          <MapPin className="w-4 h-4 text-accent" />
          <h3 className="font-semibold">Zone activity</h3>
        </div>
        <span className="text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint">
          Times are from the video
        </span>
      </header>

      {events.length === 0 ? (
        <p className="text-xs text-ink-muted leading-relaxed">
          Nothing yet. Entries and exits appear here for Restricted, PPE checkpoint, Approach and Exit
          zones. General zones are not tracked.
        </p>
      ) : (
        <ul
          role="log"
          aria-label="Zone events, newest first"
          tabIndex={0}
          className="flex flex-col divide-y divide-line/60 max-h-56 overflow-y-auto pr-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)] rounded"
        >
          {events.map((ev) => {
            const zone = byId.get(String(ev.zone_id));
            const meta = zoneTypeMeta(zone && zone.zone_type);
            const { text, tone } = describeZoneEvent(ev);
            return (
              <li key={ev.id} className="flex items-center gap-3 py-1.5 text-xs">
                <span className="w-11 shrink-0 font-mono text-ink-faint tabular-nums">{clock(ev.video_time_s)}</span>
                <span
                  className="inline-flex items-center gap-1.5 shrink-0 max-w-[40%] rounded-md border border-line px-1.5 py-0.5 text-[10px] font-mono text-ink-muted"
                  title={`${meta.label} zone`}
                >
                  <span aria-hidden="true" className="w-2 h-2 rounded-full shrink-0 border border-line" style={{ background: meta.color }} />
                  <span className="truncate">{(zone && (zone.zone_name || zone.zone_id)) || ev.zone_id || 'Zone'}</span>
                </span>
                <span
                  className={
                    tone === 'attention' ? 'text-accent font-semibold'
                      : tone === 'unconfirmed' ? 'text-ink-faint'
                      : 'text-ink'
                  }
                >
                  {text}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
