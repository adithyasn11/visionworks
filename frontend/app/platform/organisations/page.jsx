// frontend/app/platform/organisations/page.jsx
//
// Founder console — the organisations list.
//
// Filter/search/sort state arrives as searchParams, so the whole page stays a
// Server Component: filtering happens next to the data instead of shipping every
// organisation to the browser and hiding rows with CSS. The only client code is
// the filter bar, which needs input handling.

import React, { Suspense } from 'react';
import Link from 'next/link';
import { AlertCircle, ArrowLeft } from 'lucide-react';

import { getOrganisations, SORT_KEYS } from '../../lib/platform/queries';
import OrgFilters from '../components/OrgFilters';
import OrgTable from '../components/OrgTable';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Organisations · Platform · VisionWorks',
  robots: { index: false, follow: false },
};

const VALID_FILTERS = new Set(['all', 'active', 'suspended', 'attention']);

export default async function OrganisationsPage({ searchParams }) {
  // Validate everything from the URL before it reaches a query. Unknown values
  // fall back to the default rather than producing an empty list, so a mangled
  // link degrades to "all organisations" instead of looking broken.
  const q = typeof searchParams?.q === 'string' ? searchParams.q.slice(0, 120) : '';
  const filter = VALID_FILTERS.has(searchParams?.filter) ? searchParams.filter : 'all';
  const sort = SORT_KEYS.includes(searchParams?.sort) ? searchParams.sort : 'newest';

  const { error, orgs, counts, total } = await getOrganisations({ q, filter, sort });

  const isFiltered = Boolean(q) || filter !== 'all';

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link
            href="/platform"
            className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint hover:text-accent transition-colors mb-2"
          >
            <ArrowLeft className="w-3 h-3" />
            Overview
          </Link>
          <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
            Organisations
          </h1>
          <p className="mt-1.5 text-[13.5px] text-ink-muted max-w-xl leading-relaxed">
            {total === 0
              ? 'No customers yet.'
              : `${total} organisation${total === 1 ? '' : 's'} on the platform. Configuration and health only — occupancy stays with the customer.`}
          </p>
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <div className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Could not load organisations.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </div>
        </div>
      )}

      {/* useSearchParams() inside OrgFilters needs a Suspense boundary, the same
          constraint that applies to the login page. */}
      <Suspense
        fallback={<div className="h-[92px] rounded-xl bg-surface-alt animate-pulse" />}
      >
        <OrgFilters counts={counts} resultCount={orgs.length} />
      </Suspense>

      <OrgTable orgs={orgs} filtered={isFiltered} />
    </div>
  );
}
