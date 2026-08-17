// frontend/app/platform/components/AttentionList.jsx
//
// The support queue: organisations that need a human, most urgent first.
//
// This is the one part of the overview that does real work. The ranking comes
// from buildAttentionList() in lib/platform/queries.js, weighted by what
// actually blocks a customer — a camera in error stops data arriving at all,
// whereas "no active members" is merely untidy.

import React from 'react';
import Link from 'next/link';
import {
  AlertTriangle, CameraOff, XOctagon, Shapes, UserX, CheckCircle2, ChevronRight,
} from 'lucide-react';

const REASON_ICON = {
  camera_error: CameraOff,
  failed_session: XOctagon,
  no_zones: Shapes,
  no_cameras: CameraOff,
  no_members: UserX,
};

/** severity 3 = blocking, 2 = degraded, 1 = incomplete setup. */
const SEVERITY = {
  3: { label: 'blocking',   cls: 'bg-accent text-white' },
  2: { label: 'degraded',   cls: 'bg-accent-soft text-accent' },
  1: { label: 'incomplete', cls: 'bg-surface-alt text-ink-muted' },
};

export default function AttentionList({ items }) {
  return (
    <div className="rounded-xl border border-line bg-surface themed">
      <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-accent" strokeWidth={2.2} />
          <h2 className="text-[14px] font-bold tracking-tight text-ink">Needs attention</h2>
        </div>
        {items.length > 0 && (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint tabular-nums">
            {items.length} org{items.length > 1 ? 's' : ''}
          </span>
        )}
      </div>

      {items.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <CheckCircle2 className="w-7 h-7 mx-auto text-emerald-500 mb-3" strokeWidth={2} />
          <p className="text-[13.5px] font-bold text-ink">Nothing needs attention</p>
          <p className="text-[12px] text-ink-faint mt-1">
            Every organisation has cameras, zones and a healthy last run.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-[color:var(--line)]">
          {items.map(({ org, reasons, severity }) => {
            const sev = SEVERITY[severity] ?? SEVERITY[1];
            return (
              <li key={org.id}>
                <Link
                  href={`/platform/organisations/${org.id}`}
                  className="group flex items-start gap-3 px-4 sm:px-5 py-3.5 hover:bg-surface-alt transition-colors duration-150"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13.5px] font-bold text-ink truncate">{org.name}</span>
                      <span className={`rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold ${sev.cls}`}>
                        {sev.label}
                      </span>
                      {org.isSuspended && (
                        <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold bg-surface-alt text-ink-faint">
                          suspended
                        </span>
                      )}
                    </div>

                    <ul className="mt-1.5 flex flex-wrap gap-x-3.5 gap-y-1">
                      {reasons.map((r) => {
                        const Icon = REASON_ICON[r.kind] ?? AlertTriangle;
                        return (
                          <li key={r.kind} className="flex items-center gap-1.5 text-[11.5px] text-ink-muted">
                            <Icon className="w-3 h-3 shrink-0 text-ink-faint" strokeWidth={2.2} />
                            {r.label}
                          </li>
                        );
                      })}
                    </ul>
                  </div>

                  <ChevronRight className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint group-hover:text-accent group-hover:translate-x-0.5 transition-all duration-150" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
