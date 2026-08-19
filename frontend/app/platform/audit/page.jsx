// frontend/app/platform/audit/page.jsx
//
// Founder console — the platform audit log.
//
// WHY THIS PAGE MATTERS MORE THAN IT LOOKS
//
// A platform operator is the one actor row-level security does not constrain.
// Everywhere else in this schema, the database refuses what a caller may not do;
// here, the record of what was done is the accountability that replaces the
// missing enforcement. That is why the table has no UPDATE or DELETE policy and
// no grant for either — it is append-only in the database, not merely in the UI.
//
// The page is deliberately read-only. There is no "clear log" control, because
// a log its own subject can erase is not evidence.
//
// This shows PLATFORM actions only. A customer's own audit_logs are separate and
// unreadable from here — no policy grants an operator access to them.

import React, { Suspense } from 'react';
import Link from 'next/link';
import {
  ArrowLeft, AlertCircle, ScrollText, Lock, Terminal,
  PauseCircle, PlayCircle, Clock, ShieldPlus, ShieldOff, Inbox, SearchX,
} from 'lucide-react';

import { getPlatformAudit, PLATFORM_ACTIONS, AUDIT_RANGES } from '../../lib/platform/queries';
import AuditFilters from '../components/AuditFilters';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Audit log · Platform · VisionWorks',
  robots: { index: false, follow: false },
};

/** Icon and tone per action, so the log is scannable without reading every row. */
const ACTION_META = {
  'platform.org_suspended':     { icon: PauseCircle, tone: 'danger', label: 'Organisation suspended' },
  'platform.org_restored':      { icon: PlayCircle,  tone: 'good',   label: 'Organisation restored' },
  'platform.retention_changed': { icon: Clock,       tone: 'warn',   label: 'Retention changed' },
  'platform.admin_granted':     { icon: ShieldPlus,  tone: 'danger', label: 'Operator granted' },
  'platform.admin_revoked':     { icon: ShieldOff,   tone: 'warn',   label: 'Operator revoked' },
};

const TONE = {
  danger: 'bg-accent text-white',
  warn:   'bg-accent-soft text-accent',
  good:   'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  muted:  'bg-surface-alt text-ink-muted',
};

const fmtFull = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });

function relative(iso) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 14) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return weeks < 8 ? `${weeks}w ago` : `${Math.floor(days / 30)}mo ago`;
}

/**
 * Turn an entry's metadata into one readable sentence.
 *
 * Each action stores a different shape, so a generic JSON dump would make the
 * reader decode it every time. The raw object is still available below for
 * anything not covered here.
 */
function describe(entry) {
  const m = entry.metadata ?? {};
  switch (entry.action) {
    case 'platform.retention_changed':
      return m.from != null && m.to != null
        ? `Changed from ${m.from} to ${m.to} days${m.to < m.from ? ' — shortening deletes data on the next retention run' : ''}`
        : null;
    case 'platform.admin_granted':
      return m.email ? `Granted to ${m.email}${m.note ? ` (${m.note})` : ''}` : null;
    case 'platform.admin_revoked':
      return m.targetEmail
        ? `Revoked from ${m.targetEmail}${m.selfRevoke ? ' — self-revoked' : ''}`
        : null;
    case 'platform.org_suspended':
      return 'Members lost access immediately. No data was deleted.';
    case 'platform.org_restored':
      return m.previousDeletedAt
        ? `Access restored, suspended since ${new Date(m.previousDeletedAt).toLocaleDateString()}`
        : 'Access restored.';
    default:
      return null;
  }
}

const VALID_RANGES = new Set(Object.keys(AUDIT_RANGES));
const VALID_ACTIONS = new Set(PLATFORM_ACTIONS.map((a) => a.key));

export default async function PlatformAuditPage({ searchParams }) {
  // Everything from the URL is validated before reaching a query, so a mangled
  // link degrades to the default view rather than an empty one.
  const action = VALID_ACTIONS.has(searchParams?.action) ? searchParams.action : 'all';
  const range = VALID_RANGES.has(searchParams?.range) ? searchParams.range : '30d';
  const actor =
    typeof searchParams?.actor === 'string' && searchParams.actor.length <= 320
      ? searchParams.actor
      : 'all';

  const { error, entries, actors, counts, total } = await getPlatformAudit({
    action, actor, range,
  });

  const isFiltered = action !== 'all' || actor !== 'all' || range !== '30d';

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <header>
        <Link
          href="/platform"
          className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint hover:text-accent transition-colors mb-2"
        >
          <ArrowLeft className="w-3 h-3" />
          Overview
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
            Platform audit log
          </h1>
          <span className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] font-bold bg-surface-alt text-ink-muted">
            <Lock className="w-3 h-3" />
            append-only
          </span>
        </div>
        <p className="mt-1.5 text-[13.5px] text-ink-muted max-w-2xl leading-relaxed">
          Every action taken from this console. A platform operator is the one
          actor row-level security does not constrain, so this record is the
          accountability that replaces the missing enforcement.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Could not load the audit log.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      )}

      <Suspense fallback={<div className="h-[104px] rounded-xl bg-surface-alt animate-pulse" />}>
        <AuditFilters
          actions={PLATFORM_ACTIONS}
          actors={actors}
          counts={counts}
          resultCount={entries.length}
        />
      </Suspense>

      {/* ── Timeline ── */}
      <section className="rounded-xl border border-line bg-surface overflow-hidden themed">
        <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
          <div className="flex items-center gap-2">
            <ScrollText className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
            <h2 className="text-[14px] font-bold tracking-tight text-ink">Activity</h2>
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint tabular-nums">
            {total} total
          </span>
        </div>

        {entries.length === 0 ? (
          <div className="px-5 py-14 text-center">
            {isFiltered ? (
              <>
                <SearchX className="w-7 h-7 mx-auto text-ink-faint mb-3" strokeWidth={1.8} />
                <p className="text-[13.5px] font-bold text-ink">Nothing in this window</p>
                <p className="text-[12px] text-ink-faint mt-1 max-w-sm mx-auto leading-relaxed">
                  No platform action matches these filters. Widen the date range
                  or clear them to see the full log.
                </p>
              </>
            ) : (
              <>
                <Inbox className="w-7 h-7 mx-auto text-ink-faint mb-3" strokeWidth={1.8} />
                <p className="text-[13.5px] font-bold text-ink">No platform actions yet</p>
                <p className="text-[12px] text-ink-faint mt-1 max-w-md mx-auto leading-relaxed">
                  Suspending an organisation, changing its retention, or granting
                  and revoking operator access all appear here.
                </p>
              </>
            )}
          </div>
        ) : (
          <ol className="divide-y divide-[color:var(--line)]">
            {entries.map((e) => {
              const meta = ACTION_META[e.action] ?? {
                icon: ScrollText, tone: 'muted', label: e.action,
              };
              const Icon = meta.icon;
              const summary = describe(e);
              // Anything the summary did not already say.
              const extraKeys = Object.keys(e.metadata ?? {}).filter(
                (k) => !['from', 'to', 'email', 'note', 'targetEmail', 'selfRevoke', 'previousDeletedAt'].includes(k),
              );

              return (
                <li key={e.id} className="px-4 sm:px-5 py-3.5 hover:bg-surface-alt transition-colors duration-150">
                  <div className="flex items-start gap-3">
                    <span
                      className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${TONE[meta.tone]}`}
                      aria-hidden="true"
                    >
                      <Icon className="w-3.5 h-3.5" strokeWidth={2.3} />
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-3 flex-wrap">
                        <span className="font-bold text-[13.5px] text-ink">{meta.label}</span>
                        <time
                          dateTime={e.createdAt}
                          title={fmtFull(e.createdAt)}
                          className="shrink-0 text-[11.5px] text-ink-faint tabular-nums whitespace-nowrap"
                        >
                          {relative(e.createdAt)}
                        </time>
                      </div>

                      {/* Who and against which organisation. */}
                      <div className="mt-1 flex items-center gap-2 flex-wrap text-[11.5px]">
                        {e.actorEmail ? (
                          <span className="font-mono text-ink-muted">{e.actorEmail}</span>
                        ) : (
                          // auth.uid() is null when run from the SQL editor —
                          // the bootstrap grant is the canonical case.
                          <span className="inline-flex items-center gap-1 font-mono text-ink-faint">
                            <Terminal className="w-3 h-3" />
                            SQL editor
                          </span>
                        )}

                        {e.targetOrgName && (
                          <>
                            <span className="text-ink-faint">→</span>
                            {e.targetOrgId ? (
                              <Link
                                href={`/platform/organisations/${e.targetOrgId}`}
                                className="font-semibold text-ink-muted hover:text-accent transition-colors"
                              >
                                {e.targetOrgName}
                              </Link>
                            ) : (
                              <span className="font-semibold text-ink-muted">{e.targetOrgName}</span>
                            )}
                          </>
                        )}

                        {e.ipAddress && (
                          <span className="font-mono text-[10.5px] text-ink-faint">· {e.ipAddress}</span>
                        )}
                      </div>

                      {summary && (
                        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-muted">
                          {summary}
                        </p>
                      )}

                      {extraKeys.length > 0 && (
                        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                          {extraKeys.map((k) => (
                            <div key={k} className="flex items-baseline gap-1.5">
                              <dt className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
                                {k}
                              </dt>
                              <dd className="font-mono text-[11px] text-ink-muted break-all max-w-[22rem]">
                                {String(e.metadata[k])}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {/* ── Why there is no delete ── */}
      <section className="rounded-xl border border-line bg-surface-alt px-4 sm:px-5 py-4 themed">
        <div className="flex items-start gap-2.5">
          <Lock className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" strokeWidth={2.2} />
          <div className="min-w-0">
            <h2 className="text-[13.5px] font-bold tracking-tight text-ink">
              This log cannot be edited or cleared
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-muted max-w-3xl">
              <code className="font-mono text-[11.5px] text-ink">platform_audit_logs</code>{' '}
              has no UPDATE or DELETE policy and no grant for either, so there is
              no request an operator can make that changes a past entry — not
              through this page, and not through a hand-crafted call. A log its
              own subject can erase is not evidence.
            </p>
            <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
              Customer organisations keep their own separate audit trail, which
              is not readable from a platform session.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
