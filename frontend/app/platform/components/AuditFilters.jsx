'use client';

// frontend/app/platform/components/AuditFilters.jsx
//
// Action / actor / date-range filters for the audit log.
//
// State lives in the URL, same as the organisations list: a filtered view is
// linkable, survives a refresh, and back/forward behave. It also keeps the page
// a Server Component so filtering happens in the query rather than by shipping
// every entry to the browser.
//
// `useTransition` drives a pending state, so a slow query shows the controls
// dimming instead of appearing frozen — the same problem loading.jsx solves for
// full navigations, applied to in-page filter changes.

import React, { useTransition } from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { Filter, Calendar, User, X, Loader2 } from 'lucide-react';

const RANGES = [
  { key: '24h', label: '24 hours' },
  { key: '7d',  label: '7 days' },
  { key: '30d', label: '30 days' },
  { key: '90d', label: '90 days' },
  { key: 'all', label: 'All time' },
];

export default function AuditFilters({ actions, actors, counts, resultCount }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();

  const activeAction = searchParams.get('action') ?? 'all';
  const activeActor = searchParams.get('actor') ?? 'all';
  const activeRange = searchParams.get('range') ?? '30d';

  const write = (patch) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(patch)) {
      // A param is dropped only when it equals THAT key's default, so the URL
      // stays short. `range` is the exception people trip on: its "All time"
      // option is literally the string 'all', which a blanket `v === 'all'`
      // check would delete — silently reverting the range to 30 days while the
      // select still read "All time".
      const isDefault =
        v == null ||
        v === '' ||
        (k === 'range' ? v === '30d' : v === 'all');
      if (isDefault) params.delete(k);
      else params.set(k, v);
    }
    const qs = params.toString();
    startTransition(() => {
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    });
  };

  const isFiltered =
    activeAction !== 'all' || activeActor !== 'all' || activeRange !== '30d';

  const selectClass =
    'appearance-none rounded-lg border-2 border-field bg-ground pl-9 pr-8 py-2 text-[13px] font-semibold text-ink hover:border-field-hover focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] transition-colors duration-150 cursor-pointer disabled:opacity-50';

  return (
    <div className={`space-y-3 transition-opacity duration-150 ${pending ? 'opacity-60' : ''}`}>
      <div className="flex flex-col sm:flex-row flex-wrap gap-3">
        {/* Action */}
        <div className="relative flex-1 min-w-[180px]">
          <Filter
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-faint pointer-events-none"
            aria-hidden="true"
          />
          <select
            value={activeAction}
            onChange={(e) => write({ action: e.target.value })}
            disabled={pending}
            aria-label="Filter by action"
            className={`w-full ${selectClass}`}
          >
            <option value="all">All actions ({Object.values(counts).reduce((a, b) => a + b, 0)})</option>
            {actions.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label} ({counts[a.key] ?? 0})
              </option>
            ))}
          </select>
          <span aria-hidden="true" className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint text-[10px] pointer-events-none">▼</span>
        </div>

        {/* Actor. Only rendered when there is more than one — a dropdown with a
            single option is noise. */}
        {actors.length > 1 && (
          <div className="relative flex-1 min-w-[180px]">
            <User
              className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-faint pointer-events-none"
              aria-hidden="true"
            />
            <select
              value={activeActor}
              onChange={(e) => write({ actor: e.target.value })}
              disabled={pending}
              aria-label="Filter by actor"
              className={`w-full ${selectClass}`}
            >
              <option value="all">All actors</option>
              {actors.map((a) => (
                <option key={a.email} value={a.email}>
                  {a.email} ({a.count})
                </option>
              ))}
            </select>
            <span aria-hidden="true" className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint text-[10px] pointer-events-none">▼</span>
          </div>
        )}

        {/* Range */}
        <div className="relative shrink-0">
          <Calendar
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-faint pointer-events-none"
            aria-hidden="true"
          />
          <select
            value={activeRange}
            onChange={(e) => write({ range: e.target.value })}
            disabled={pending}
            aria-label="Filter by date range"
            className={selectClass}
          >
            {RANGES.map((r) => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </select>
          <span aria-hidden="true" className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint text-[10px] pointer-events-none">▼</span>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        {isFiltered && (
          <button
            type="button"
            onClick={() => write({ action: 'all', actor: 'all', range: '30d' })}
            disabled={pending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12px] font-bold text-ink-muted hover:text-accent hover:border-[color:var(--accent)] transition-colors"
          >
            <X className="w-3 h-3" />
            Clear filters
          </button>
        )}

        <span className="ml-auto flex items-center gap-2 font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink-faint tabular-nums">
          {pending && <Loader2 className="w-3 h-3 animate-spin" />}
          {resultCount} {resultCount === 1 ? 'entry' : 'entries'}
        </span>
      </div>
    </div>
  );
}
