'use client';

// frontend/app/platform/components/OrgFilters.jsx
//
// Search box, filter chips and sort selector for the organisations list.
//
// STATE LIVES IN THE URL, not in React. Three reasons:
//   · a filtered view is linkable and survives a refresh
//   · the page stays a Server Component, so filtering happens next to the data
//     rather than shipping every org to the browser
//   · back/forward work the way the reader expects
//
// The search input is debounced so typing does not fire a server round trip per
// keystroke, and `replace` is used rather than `push` so a search does not bury
// the previous page in history.

import React, { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { Search, X, ArrowUpDown, Loader2 } from 'lucide-react';

const FILTERS = [
  { key: 'all',       label: 'All' },
  { key: 'active',    label: 'Active' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'suspended', label: 'Suspended' },
];

const SORTS = [
  { key: 'newest',  label: 'Newest first' },
  { key: 'oldest',  label: 'Oldest first' },
  { key: 'name',    label: 'Name A–Z' },
  { key: 'health',  label: 'Worst health' },
  { key: 'members', label: 'Most members' },
  { key: 'cameras', label: 'Most cameras' },
  { key: 'zones',   label: 'Most zones' },
];

export default function OrgFilters({ counts, resultCount }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const activeFilter = searchParams.get('filter') ?? 'all';
  const activeSort = searchParams.get('sort') ?? 'newest';
  const urlQuery = searchParams.get('q') ?? '';

  const [query, setQuery] = useState(urlQuery);
  const [pending, setPending] = useState(false);
  const debounce = useRef(null);
  const firstRender = useRef(true);

  // Keep the input in step when the URL changes from outside this component
  // (back button, a chip click that clears the search).
  useEffect(() => {
    setQuery(urlQuery);
  }, [urlQuery]);

  const write = (patch) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '' || v === undefined) params.delete(k);
      else params.set(k, v);
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  // Debounced search → URL.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (query === urlQuery) return;

    setPending(true);
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      write({ q: query.trim() || null });
      setPending(false);
    }, 280);

    return () => clearTimeout(debounce.current);
    // `write` and `urlQuery` are intentionally omitted: including them would
    // re-arm the timer on every navigation and cancel the user's own typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row gap-3">
        {/* Search */}
        <div className="relative flex-1 min-w-0">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-faint pointer-events-none"
            aria-hidden="true"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or slug…"
            aria-label="Search organisations"
            className="w-full rounded-lg border-2 border-field bg-ground pl-9 pr-9 py-2 text-[13.5px] text-ink placeholder:text-ink-faint hover:border-field-hover focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] transition-colors duration-150"
          />
          {pending ? (
            <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-faint animate-spin" />
          ) : query ? (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-2.5 top-1/2 -translate-y-1/2 w-5 h-5 rounded flex items-center justify-center text-ink-faint hover:text-accent transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          ) : null}
        </div>

        {/* Sort */}
        <div className="relative shrink-0">
          <ArrowUpDown
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-faint pointer-events-none"
            aria-hidden="true"
          />
          <select
            value={activeSort}
            onChange={(e) => write({ sort: e.target.value === 'newest' ? null : e.target.value })}
            aria-label="Sort organisations"
            className="appearance-none rounded-lg border-2 border-field bg-ground pl-9 pr-8 py-2 text-[13px] font-semibold text-ink hover:border-field-hover focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] transition-colors duration-150 cursor-pointer"
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
          <span
            aria-hidden="true"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint text-[10px] pointer-events-none"
          >
            ▼
          </span>
        </div>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const on = activeFilter === f.key;
          const count = counts?.[f.key] ?? 0;
          const isAttention = f.key === 'attention' && count > 0;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => write({ filter: f.key === 'all' ? null : f.key })}
              aria-pressed={on}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-bold border transition-colors duration-150 ${
                on
                  ? 'border-[color:var(--accent)] bg-accent-soft text-accent'
                  : 'border-line bg-surface text-ink-muted hover:text-ink hover:border-field'
              }`}
            >
              {f.label}
              <span
                className={`tabular-nums font-mono text-[10.5px] ${
                  on ? 'text-accent' : isAttention ? 'text-accent' : 'text-ink-faint'
                }`}
              >
                {count}
              </span>
            </button>
          );
        })}

        {/* Result count, so a filtered-to-empty list is obviously filtered
            rather than looking like a data failure. */}
        <span className="ml-auto font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink-faint tabular-nums">
          {resultCount} shown
        </span>
      </div>
    </div>
  );
}
