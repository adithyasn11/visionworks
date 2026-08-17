// frontend/app/platform/page.jsx
//
// Founder console — Overview.
//
// A Server Component: every query runs on the server through the operator's own
// session, so RLS applies and no customer data is shipped to the browser beyond
// what is rendered. The only client components on this page are the chart (which
// needs the DOM) and the shell (which needs interactivity).

import React from 'react';
import Link from 'next/link';
import {
  Building2, Users, Camera, Shapes, CameraOff, XOctagon, Activity, PauseCircle,
  ArrowRight, AlertCircle,
} from 'lucide-react';

import { getPlatformOverview, buildAttentionList } from '../lib/platform/queries';
import StatTile from './components/StatTile';
import SignupsChart from './components/SignupsChart';
import AttentionList from './components/AttentionList';
import RecentSignups from './components/RecentSignups';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Overview · Platform · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function PlatformOverviewPage() {
  const { error, orgs, stats, signups } = await getPlatformOverview();
  const attention = buildAttentionList(orgs);

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent mb-2">
            Platform overview
          </p>
          <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
            Every organisation, at a glance
          </h1>
          <p className="mt-1.5 text-[13.5px] text-ink-muted max-w-xl leading-relaxed">
            Configuration and health across the whole platform. Occupancy
            measurements stay with the organisations that produced them.
          </p>
        </div>

        <Link
          href="/platform/organisations"
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-[13px] font-bold text-white hover:brightness-110 transition-[filter] duration-150"
        >
          All organisations
          <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </header>

      {/* Query failure is shown, not swallowed — a console that silently renders
          zeros is worse than one that admits it could not read. */}
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <div className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Could not load platform data.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </div>
        </div>
      )}

      {/* ── Stat tiles ── */}
      <section aria-label="Platform totals">
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <StatTile
            label="Organisations"
            value={stats?.totalOrgs ?? 0}
            hint={
              stats?.suspendedOrgs
                ? `${stats.activeOrgs} active · ${stats.suspendedOrgs} suspended`
                : 'all active'
            }
            icon={Building2}
          />
          <StatTile
            label="Members"
            value={stats?.totalMembers ?? 0}
            hint={stats?.pendingInvites ? `${stats.pendingInvites} invite(s) pending` : 'across all orgs'}
            icon={Users}
          />
          <StatTile
            label="Cameras"
            value={stats?.totalCameras ?? 0}
            hint={`${stats?.totalSites ?? 0} site${(stats?.totalSites ?? 0) === 1 ? '' : 's'}`}
            icon={Camera}
          />
          <StatTile
            label="Zones"
            value={stats?.totalZones ?? 0}
            hint="drawn by customers"
            icon={Shapes}
          />
          <StatTile
            label="Cameras in error"
            value={stats?.camerasInError ?? 0}
            hint={stats?.camerasInError ? 'needs investigation' : 'all reachable'}
            icon={CameraOff}
            tone="danger"
          />
          <StatTile
            label="Failed runs"
            value={stats?.failedSessions ?? 0}
            hint={stats?.failedSessions ? 'see health page' : 'no failures'}
            icon={XOctagon}
            tone="danger"
          />
        </div>
      </section>

      {/* ── Chart + attention, side by side on wide screens ── */}
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
        <SignupsChart data={signups ?? []} />
        <AttentionList items={attention} />
      </div>

      {/* ── Recent signups + live activity ── */}
      <div className="grid lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4">
        <RecentSignups orgs={orgs} />

        <div className="space-y-3">
          <div className="rounded-xl border border-line bg-surface px-4 py-3.5 themed">
            <div className="flex items-center gap-2 mb-3">
              <Activity className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
              <h2 className="text-[14px] font-bold tracking-tight text-ink">Right now</h2>
            </div>
            <dl className="space-y-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[12.5px] text-ink-muted">Runs in progress</dt>
                <dd className="text-[15px] font-bold tabular-nums text-ink">
                  {stats?.runningSessions ?? 0}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[12.5px] text-ink-muted">Orgs needing help</dt>
                <dd className={`text-[15px] font-bold tabular-nums ${attention.length ? 'text-accent' : 'text-ink'}`}>
                  {attention.length}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-[12.5px] text-ink-muted">Pending invites</dt>
                <dd className="text-[15px] font-bold tabular-nums text-ink">
                  {stats?.pendingInvites ?? 0}
                </dd>
              </div>
            </dl>
          </div>

          {/* The boundary, restated where an operator will actually read it.
              This is the panel to point at in a demo. */}
          <div className="rounded-xl border border-line bg-surface-alt px-4 py-3.5 themed">
            <div className="flex items-center gap-2 mb-2">
              <PauseCircle className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
              <h3 className="text-[13px] font-bold tracking-tight text-ink">
                Occupancy not accessible
              </h3>
            </div>
            <p className="text-[11.5px] leading-relaxed text-ink-muted">
              Platform operators can read configuration and health — never
              measurements. Enforced by row-level security in Postgres, not by
              this interface. A query for occupancy data from this session
              returns no rows.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
