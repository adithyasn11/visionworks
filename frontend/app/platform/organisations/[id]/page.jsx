// frontend/app/platform/organisations/[id]/page.jsx
//
// Founder console — one organisation in full.
//
// Server Component. Every number on this page is read from the database through
// the operator's own session, so RLS applies to each query. There is no mock or
// placeholder data anywhere: sections that are empty say so and explain what
// would fill them, because "0 cameras" and "we could not read cameras" are
// different support situations and must not look alike.
//
// DELIBERATELY ABSENT: occupancy, utilisation, posture, alerts, reports. Those
// are not omitted by the UI — no RLS policy grants a platform operator access to
// the measurement tables, so the queries are not even attempted.

import React from 'react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft, AlertCircle, Building2 } from 'lucide-react';

import { getOrganisationDetail, orgHealth } from '../../../lib/platform/queries';
import {
  MembersSection, SitesSection, CamerasSection, ZonesSection, SessionsSection,
  OccupancyNotice,
} from '../../components/OrgSections';
import OrgActions from '../../components/OrgActions';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }) {
  const { org } = await getOrganisationDetail(params.id);
  return {
    title: org ? `${org.name} · Platform · VisionWorks` : 'Organisation · Platform',
    robots: { index: false, follow: false },
  };
}

const HEALTH_STYLE = {
  error:     'bg-accent text-white',
  warn:      'bg-accent-soft text-accent',
  suspended: 'bg-surface-alt text-ink-faint',
  live:      'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  ok:        'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
};

export default async function OrganisationDetailPage({ params }) {
  const {
    error, org, members, sites, cameras, zones, sessions, sectionErrors,
  } = await getOrganisationDetail(params.id);

  // Unknown id, or an org this caller may not see — indistinguishable by design.
  if (!org) notFound();

  const health = orgHealth(org);

  const facts = [
    ['Slug', org.slug],
    ['Timezone', org.timezone],
    ['Retention', `${org.retentionDays} days`],
    ['Signed up', new Date(org.createdAt).toLocaleDateString(undefined, {
      day: 'numeric', month: 'long', year: 'numeric',
    })],
  ];

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <header>
        <Link
          href="/platform/organisations"
          className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint hover:text-accent transition-colors mb-2"
        >
          <ArrowLeft className="w-3 h-3" />
          Organisations
        </Link>

        <div className="flex flex-wrap items-center gap-3">
          <Building2 className="w-6 h-6 text-ink-faint shrink-0" strokeWidth={2} />
          <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
            {org.name}
          </h1>
          <span
            className={`rounded px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] font-bold ${
              HEALTH_STYLE[health.level] ?? HEALTH_STYLE.ok
            }`}
          >
            {health.label}
          </span>
        </div>

        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
          {facts.map(([term, detail]) => (
            <div key={term} className="flex items-baseline gap-2">
              <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint">
                {term}
              </dt>
              <dd className="text-[13px] font-semibold text-ink">{detail}</dd>
            </div>
          ))}
        </dl>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Partial load.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      )}

      {/* ── Setup summary ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          ['Members', members.length, `${members.filter((m) => m.isPending).length} invited`],
          ['Sites', sites.length, null],
          ['Cameras', cameras.length, cameras.filter((c) => c.status === 'ERROR').length
            ? `${cameras.filter((c) => c.status === 'ERROR').length} in error` : null],
          ['Zones', zones.length, zones.length === 0 ? 'none drawn' : null],
        ].map(([label, value, hint]) => (
          <div key={label} className="rounded-xl border border-line bg-surface px-4 py-3 themed">
            <div className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint">
              {label}
            </div>
            <div className="mt-1 text-[22px] font-black tracking-tight tabular-nums leading-none text-ink">
              {value}
            </div>
            {hint && <div className="mt-1 text-[11px] text-ink-faint">{hint}</div>}
          </div>
        ))}
      </div>

      {/* ── Data sections ── */}
      <MembersSection members={members} error={sectionErrors.members} />
      <SitesSection sites={sites} error={sectionErrors.sites} />
      <CamerasSection cameras={cameras} error={sectionErrors.cameras} />
      <ZonesSection zones={zones} error={sectionErrors.zones} />
      <SessionsSection sessions={sessions} error={sectionErrors.sessions} />

      <OrgActions org={org} />
      <OccupancyNotice orgName={org.name} />
    </div>
  );
}
