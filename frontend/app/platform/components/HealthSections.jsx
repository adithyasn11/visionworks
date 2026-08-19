// frontend/app/platform/components/HealthSections.jsx
//
// The triage sections. Server Components — read-only.
//
// EMPTY IS THE GOOD OUTCOME HERE, and the design has to say so. On this page an
// empty section means nothing is broken, so it gets a green tick and a plain
// sentence rather than the grey "no data" treatment used elsewhere. A support
// engineer glancing at this page needs "all clear" and "could not load" to be
// impossible to confuse.
//
// Every string rendered comes from the database. Error messages are shown in
// full and never truncated — the whole point of the page is the message.

import React from 'react';
import Link from 'next/link';
import {
  CameraOff, XOctagon, Activity, Shapes, PackageOpen,
  CheckCircle2, AlertCircle, ChevronRight, Clock,
} from 'lucide-react';

/* ── shared ───────────────────────────────────────────────────────────────── */

/**
 * @param severity 'critical' | 'warn' | 'info' — drives the header accent, so
 *   the reader can rank sections without reading the titles.
 */
export function TriageSection({
  title, subtitle, icon: Icon, count, severity = 'info',
  error, allClear, children,
}) {
  const hot = count > 0;
  const accent =
    severity === 'critical' ? 'text-accent'
    : severity === 'warn' ? 'text-amber-600 dark:text-amber-400'
    : 'text-ink-faint';

  return (
    <section
      className={`rounded-xl border bg-surface overflow-hidden themed transition-colors duration-200 ${
        hot && severity === 'critical' ? 'border-[color:var(--accent)]' : 'border-line'
      }`}
    >
      <div className="flex items-start justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
        <div className="flex items-start gap-2.5 min-w-0">
          <Icon
            className={`w-4 h-4 shrink-0 mt-0.5 ${hot ? accent : 'text-ink-faint'}`}
            strokeWidth={2.2}
          />
          <div className="min-w-0">
            <h2 className="text-[14px] font-bold tracking-tight text-ink">{title}</h2>
            {subtitle && (
              <p className="text-[11.5px] text-ink-faint mt-0.5 leading-snug">{subtitle}</p>
            )}
          </div>
        </div>
        <span
          className={`shrink-0 rounded px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] font-bold tabular-nums ${
            hot
              ? severity === 'critical'
                ? 'bg-accent text-white'
                : 'bg-accent-soft text-accent'
              : 'bg-surface-alt text-ink-faint'
          }`}
        >
          {count}
        </span>
      </div>

      {error ? (
        <div className="px-4 sm:px-5 py-4 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[12.5px] leading-relaxed">
            <span className="font-bold text-accent">Could not load this section.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      ) : count === 0 ? (
        <div className="px-4 sm:px-5 py-6 flex items-start gap-2.5">
          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 text-emerald-500" strokeWidth={2.2} />
          <p className="text-[12.5px] leading-relaxed text-ink-muted">{allClear}</p>
        </div>
      ) : (
        children
      )}
    </section>
  );
}

const OrgLink = ({ id, name }) => (
  <Link
    href={`/platform/organisations/${id}`}
    className="font-mono text-[10.5px] text-ink-faint hover:text-accent transition-colors"
  >
    {name}
  </Link>
);

const fmtDateTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      })
    : 'never';

/** Compact age, for "how long has this been stuck". */
function age(iso) {
  if (!iso) return '—';
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 14) return `${days}d`;
  const weeks = Math.floor(days / 7);
  return weeks < 8 ? `${weeks}w` : `${Math.floor(days / 30)}mo`;
}

/* ── 1. Cameras in error ──────────────────────────────────────────────────── */

export function CamerasInErrorSection({ cameras, error }) {
  return (
    <TriageSection
      title="Cameras in error"
      subtitle="A camera in this state produces no data at all."
      icon={CameraOff}
      count={cameras.length}
      severity="critical"
      error={error}
      allClear="Every camera across every organisation reports as reachable."
    >
      <ul className="divide-y divide-[color:var(--line)]">
        {cameras.map((c) => (
          <li key={c.id} className="px-4 sm:px-5 py-3.5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-bold text-[13.5px] text-ink truncate">{c.name}</div>
                <div className="mt-0.5 flex items-center gap-2 flex-wrap">
                  <OrgLink id={c.orgId} name={c.orgName} />
                  <span className="font-mono text-[10.5px] text-ink-faint">
                    · {c.sourceType}
                    {c.sourceType === 'RTSP' ? (c.hasRtspUrl ? ' · url set' : ' · no url') : ''}
                  </span>
                </div>
              </div>
              <span className="shrink-0 text-[11.5px] text-ink-muted whitespace-nowrap">
                seen {fmtDateTime(c.lastSeenAt)}
              </span>
            </div>

            {c.lastErrorMessage && (
              <p className="mt-2 rounded-lg bg-accent-soft px-3 py-2 font-mono text-[11px] leading-relaxed text-accent break-words">
                {c.lastErrorMessage}
              </p>
            )}
          </li>
        ))}
      </ul>
    </TriageSection>
  );
}

/* ── 2. Failed runs ───────────────────────────────────────────────────────── */

export function FailedSessionsSection({ sessions, error }) {
  return (
    <TriageSection
      title="Failed and cancelled runs"
      subtitle="The support queue — newest first, with the message the pipeline reported."
      icon={XOctagon}
      count={sessions.length}
      severity="critical"
      error={error}
      allClear="No run has failed or been cancelled."
    >
      <ul className="divide-y divide-[color:var(--line)]">
        {sessions.map((s) => {
          const pct =
            s.totalFrames && s.totalFrames > 0
              ? Math.round((s.processedFrames / s.totalFrames) * 100)
              : null;
          return (
            <li key={s.id} className="px-4 sm:px-5 py-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-bold text-[13.5px] text-ink truncate max-w-[26rem]">
                    {s.sourceFilename || s.kind.replace(/_/g, ' ').toLowerCase()}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 flex-wrap">
                    <OrgLink id={s.orgId} name={s.orgName} />
                    <span className="font-mono text-[10.5px] text-ink-faint">
                      · {s.kind}
                      {s.cameraName ? ` · ${s.cameraName}` : ''}
                    </span>
                  </div>
                </div>

                <div className="shrink-0 text-right">
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold ${
                      s.status === 'ERROR' ? 'bg-accent text-white' : 'bg-surface-alt text-ink-faint'
                    }`}
                  >
                    {s.status.toLowerCase()}
                  </span>
                  <div className="mt-1 text-[11px] text-ink-faint whitespace-nowrap">
                    {age(s.queuedAt)} ago
                  </div>
                </div>
              </div>

              {/* How far it got before dying — tells you whether the input or the
                  pipeline is at fault. */}
              {pct != null && (
                <div className="mt-2 flex items-center gap-2">
                  <div
                    className="h-1 flex-1 rounded-full bg-surface-alt overflow-hidden"
                    role="img"
                    aria-label={`${pct}% of frames processed before stopping`}
                  >
                    <div
                      className="h-full rounded-full bg-[color:var(--accent)]"
                      style={{ width: `${Math.max(2, pct)}%` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] tabular-nums text-ink-faint whitespace-nowrap">
                    {s.processedFrames.toLocaleString()} / {s.totalFrames.toLocaleString()} ({pct}%)
                  </span>
                </div>
              )}

              {s.errorMessage && (
                <p className="mt-2 rounded-lg bg-accent-soft px-3 py-2 font-mono text-[11px] leading-relaxed text-accent break-words">
                  {s.errorMessage}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </TriageSection>
  );
}

/* ── 3. Running now ───────────────────────────────────────────────────────── */

export function RunningSessionsSection({ sessions, error }) {
  return (
    <TriageSection
      title="Running now"
      subtitle="Queued and in-progress runs across the platform."
      icon={Activity}
      count={sessions.length}
      severity="info"
      error={error}
      allClear="Nothing is processing right now."
    >
      <ul className="divide-y divide-[color:var(--line)]">
        {sessions.map((s) => {
          const pct =
            s.totalFrames && s.totalFrames > 0
              ? Math.round((s.processedFrames / s.totalFrames) * 100)
              : null;
          return (
            <li key={s.id} className="px-4 sm:px-5 py-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-bold text-[13.5px] text-ink truncate max-w-[26rem]">
                    {s.sourceFilename || s.kind.replace(/_/g, ' ').toLowerCase()}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 flex-wrap">
                    <OrgLink id={s.orgId} name={s.orgName} />
                    {s.cameraName && (
                      <span className="font-mono text-[10.5px] text-ink-faint">· {s.cameraName}</span>
                    )}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold ${
                      s.status === 'PROCESSING'
                        ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                        : 'bg-surface-alt text-ink-muted'
                    }`}
                  >
                    {s.status === 'PROCESSING' && (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    )}
                    {s.status.toLowerCase()}
                  </span>
                  <div className="mt-1 flex items-center justify-end gap-1 text-[11px] text-ink-faint whitespace-nowrap">
                    <Clock className="w-3 h-3" />
                    {age(s.startedAt ?? s.queuedAt)}
                  </div>
                </div>
              </div>

              {pct != null && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="h-1 flex-1 rounded-full bg-surface-alt overflow-hidden">
                    <div
                      className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
                      style={{ width: `${Math.max(2, pct)}%` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] tabular-nums text-ink-faint">{pct}%</span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </TriageSection>
  );
}

/* ── 4 & 5. Onboarding stalls ─────────────────────────────────────────────── */

/**
 * Shared renderer for the two "customer is stuck" lists. Both answer the same
 * question — how long has this organisation been in this state — so they share a
 * layout, with `age` since signup as the sortable fact.
 */
function StalledList({ orgs, metric }) {
  return (
    <ul className="divide-y divide-[color:var(--line)]">
      {orgs.map((o) => (
        <li key={o.id}>
          <Link
            href={`/platform/organisations/${o.id}`}
            className="group flex items-center gap-3 px-4 sm:px-5 py-3 hover:bg-surface-alt transition-colors duration-150"
          >
            <div className="min-w-0 flex-1">
              <div className="font-bold text-[13.5px] text-ink truncate group-hover:text-accent transition-colors">
                {o.name}
              </div>
              <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                {o.slug} · {metric(o)}
              </div>
            </div>
            <span className="shrink-0 text-[11.5px] text-ink-muted tabular-nums">
              {age(o.createdAt)} old
            </span>
            <ChevronRight className="w-4 h-4 shrink-0 text-ink-faint group-hover:text-accent group-hover:translate-x-0.5 transition-all duration-150" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function StuckNoZonesSection({ orgs }) {
  return (
    <TriageSection
      title="Cameras configured, no zones drawn"
      subtitle="The pipeline runs and produces nothing — every detection falls outside every zone. Silent and total."
      icon={Shapes}
      count={orgs.length}
      severity="warn"
      allClear="Every organisation with a camera has at least one zone."
    >
      <StalledList
        orgs={orgs}
        metric={(o) => `${o.cameraCount} camera${o.cameraCount === 1 ? '' : 's'} · 0 zones`}
      />
    </TriageSection>
  );
}

export function NeverStartedSection({ orgs }) {
  return (
    <TriageSection
      title="Signed up, never configured"
      subtitle="No camera has been added, so nothing can be processed yet."
      icon={PackageOpen}
      count={orgs.length}
      severity="warn"
      allClear="Every organisation has added at least one camera."
    >
      <StalledList
        orgs={orgs}
        metric={(o) =>
          `${o.activeMembers} member${o.activeMembers === 1 ? '' : 's'} · ${o.siteCount} site${o.siteCount === 1 ? '' : 's'} · 0 cameras`
        }
      />
    </TriageSection>
  );
}
