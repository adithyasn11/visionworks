'use client';

// frontend/app/components/PpeStatus.jsx
//
// Who on screen has been checked for helmet and vest, in words. The badge drawn
// on the video carries the verdict; this list carries the detail (which item was
// seen, which was not) as real text, which a canvas cannot offer. It appears only
// when the backend is running the safety layer.

import React from 'react';
import { HardHat } from 'lucide-react';
import { describePpe } from '../lib/ppe';
import { workerLabel } from '../lib/zoneEvents';

const MAX_ROWS = 8;

export default function PpeStatus({ entities, unavailable }) {
  const rows = (entities || []).filter((e) => e && e.ppe).slice(0, MAX_ROWS);
  const hidden = Math.max(0, (entities || []).filter((e) => e && e.ppe).length - rows.length);

  return (
    <section className="glass-card p-3 flex flex-col gap-2.5" aria-label="PPE check">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex items-center gap-2 text-ink text-sm font-semibold">
          <HardHat className="w-4 h-4 text-accent" />
          <h3 className="font-semibold">PPE check</h3>
        </div>
        <span className="text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint">
          Judged over several frames
        </span>
      </header>

      {unavailable ? (
        <p className="text-xs text-ink-muted leading-relaxed">
          {unavailable}. Nobody can be checked for helmet or vest, so every person stays unverified.
        </p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-ink-muted leading-relaxed">No one in view.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line/60">
          {rows.map((e) => {
            const { label, tone, detail } = describePpe(e.ppe);
            return (
              <li key={e.track_id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 py-1.5 text-xs">
                <span className="w-20 shrink-0 text-ink font-medium">{workerLabel(`trk-${e.track_id}`)}</span>
                <span
                  className={
                    tone === 'alert' ? 'text-accent font-semibold'
                      : tone === 'ok' ? 'text-ink font-semibold'
                      : 'text-ink-faint'
                  }
                >
                  {label}
                </span>
                <span className="text-ink-faint">{detail}</span>
              </li>
            );
          })}
          {hidden > 0 && <li className="py-1.5 text-xs text-ink-faint">and {hidden} more</li>}
        </ul>
      )}
    </section>
  );
}
