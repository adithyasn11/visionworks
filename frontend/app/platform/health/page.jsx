// frontend/app/platform/health/page.jsx
//
// Founder console — Operations.
//
// Cross-organisation triage, ordered by urgency: things that are broken now,
// then things that are silently producing nothing, then things that never
// started. That ordering is the page's whole design — a support engineer should
// be able to work top to bottom and stop when the sections turn green.
//
// Every value is read from the database. Nothing here is mocked, and no section
// touches occupancy: statuses, counts, timestamps and error strings only.

import React from 'react';
import Link from 'next/link';
import { ArrowLeft, AlertCircle, ShieldCheck, RefreshCw } from 'lucide-react';

import { getPlatformHealth } from '../../lib/platform/queries';
import {
  CamerasInErrorSection, FailedSessionsSection, RunningSessionsSection,
  StuckNoZonesSection, NeverStartedSection,
} from '../components/HealthSections';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Health · Platform · VisionWorks',
  robots: { index: false, follow: false },
};

export default async function PlatformHealthPage() {
  const {
    error, camerasInError, failedSessions, runningSessions,
    stuckNoZones, neverStarted, orgCount, sectionErrors,
  } = await getPlatformHealth();

  // Blocking = someone is losing data right now. Onboarding stalls are real but
  // not urgent, so they are counted separately rather than inflating one number.
  const blocking = camerasInError.length + failedSessions.length;
  const stalled = stuckNoZones.length + neverStarted.length;

  // A failed query returns an empty array, which would otherwise make the page
  // announce "nothing needs attention" when the truth is "we could not look".
  // Claiming all-clear on missing data is the worst failure this page could
  // have, so any section error disqualifies the banner.
  const anySectionFailed = Boolean(
    error || sectionErrors.cameras || sectionErrors.failed || sectionErrors.running,
  );
  const allClear = !anySectionFailed && blocking === 0 && stalled === 0;

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
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
            Operations
          </h1>
          <p className="mt-1.5 text-[13.5px] text-ink-muted max-w-xl leading-relaxed">
            Triage across all {orgCount} organisation{orgCount === 1 ? '' : 's'}, most
            urgent first. Statuses and error messages only — never occupancy.
          </p>
        </div>

        {/* A plain link rather than a client-side refresh button: the page is
            force-dynamic, so navigating to it re-runs every query. */}
        <Link
          href="/platform/health"
          prefetch={false}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-[12.5px] font-bold text-ink-muted hover:text-ink hover:border-field transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </Link>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Could not load health data.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      )}

      {/* ── Summary strip ── */}
      <div className="grid grid-cols-3 gap-3">
        {/* When a query failed, show "—" rather than 0: a reassuring zero on
            missing data is worse than an obvious gap. */}
        {[
          [
            'Blocking',
            anySectionFailed ? '—' : blocking,
            anySectionFailed ? 'could not read' : blocking > 0 ? 'losing data now' : 'nothing broken',
            blocking > 0,
          ],
          [
            'Stalled setup',
            error ? '—' : stalled,
            error ? 'could not read' : stalled > 0 ? 'producing nothing' : 'all configured',
            false,
          ],
          [
            'Running',
            sectionErrors.running ? '—' : runningSessions.length,
            sectionErrors.running ? 'could not read' : 'in progress',
            false,
          ],
        ].map(([label, value, hint, hot]) => (
          <div
            key={label}
            className={`rounded-xl border bg-surface px-4 py-3 themed ${
              hot ? 'border-[color:var(--accent)]' : 'border-line'
            }`}
          >
            <div className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint">
              {label}
            </div>
            <div
              className={`mt-1 text-[24px] font-black tracking-tight tabular-nums leading-none ${
                hot ? 'text-accent' : 'text-ink'
              }`}
            >
              {value}
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">{hint}</div>
          </div>
        ))}
      </div>

      {/* All-clear banner. Worth its own element: on a triage page the absence of
          rows is the result, and it should read as a verdict rather than as five
          empty boxes. */}
      {allClear && (
        <div className="flex items-start gap-2.5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-3.5">
          <ShieldCheck
            className="w-4 h-4 shrink-0 mt-0.5 text-emerald-700 dark:text-emerald-400"
            strokeWidth={2.2}
          />
          <div className="text-[13px] leading-relaxed">
            <span className="font-bold text-emerald-700 dark:text-emerald-300">
              Nothing needs attention.
            </span>{' '}
            <span className="text-ink-muted">
              No camera is in error, no run has failed, and every organisation has
              cameras and zones configured.
            </span>
          </div>
        </div>
      )}

      {/* ── Sections, in triage order ── */}
      <CamerasInErrorSection cameras={camerasInError} error={sectionErrors.cameras} />
      <FailedSessionsSection sessions={failedSessions} error={sectionErrors.failed} />
      <StuckNoZonesSection orgs={stuckNoZones} />
      <NeverStartedSection orgs={neverStarted} />
      <RunningSessionsSection sessions={runningSessions} error={sectionErrors.running} />
    </div>
  );
}
