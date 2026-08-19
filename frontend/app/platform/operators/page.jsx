// frontend/app/platform/operators/page.jsx
//
// Founder console — who holds platform access.
//
// THE ABSENT "ADD" BUTTON IS THE FEATURE. Granting platform access has no API
// path at all: platform_admins has no INSERT policy, and grant_platform_admin()
// is revoked from `authenticated`. Escalating to platform level requires the
// database password. The page states that plainly rather than leaving a reader
// hunting for a button that was never built.
//
// Revoking IS exposed, because the risks are asymmetric — needing to open the
// SQL editor to cut off a departing colleague is exactly the friction that
// leaves stale access in place for weeks.

import React from 'react';
import Link from 'next/link';
import {
  ArrowLeft, AlertCircle, ShieldCheck, Terminal, KeyRound, Clock, UserCheck,
} from 'lucide-react';

import { getPlatformOperators } from '../../lib/platform/queries';
import RevokeOperatorButton from '../components/RevokeOperatorButton';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Operators · Platform · VisionWorks',
  robots: { index: false, follow: false },
};

const fmtDate = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString(undefined, {
        day: 'numeric', month: 'short', year: 'numeric',
      })
    : '—';

const fmtDateTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        day: 'numeric', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    : null;

function initials(op) {
  return (op.fullName || op.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');
}

export default async function PlatformOperatorsPage() {
  const { error, operators, activeCount } = await getPlatformOperators();

  const revokedCount = operators.length - (activeCount ?? 0);

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
        <h1 className="text-[26px] sm:text-[30px] font-black tracking-tight leading-[1.15] text-ink">
          Platform operators
        </h1>
        <p className="mt-1.5 text-[13.5px] text-ink-muted max-w-2xl leading-relaxed">
          People who can reach this console. Platform access is separate from
          organisation roles — an operator belongs to no customer and sees
          configuration and health only, never occupancy.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color:var(--accent)] bg-accent-soft px-4 py-3"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[13px] leading-relaxed">
            <span className="font-bold text-accent">Could not load operators.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      )}

      {/* ── Counts ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {[
          ['Active', activeCount ?? 0, 'can reach the console', UserCheck],
          ['Revoked', revokedCount, revokedCount ? 'access removed' : 'none', ShieldCheck],
          ['Grant path', 'SQL', 'editor only, by design', Terminal],
        ].map(([label, value, hint, Icon]) => (
          <div key={label} className="rounded-xl border border-line bg-surface px-4 py-3 themed">
            <div className="flex items-start justify-between gap-2">
              <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint">
                {label}
              </span>
              <Icon className="w-[15px] h-[15px] shrink-0 text-ink-faint" strokeWidth={2.1} />
            </div>
            <div className="mt-1 text-[24px] font-black tracking-tight tabular-nums leading-none text-ink">
              {value}
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">{hint}</div>
          </div>
        ))}
      </div>

      {/* ── Why there is no Add button ── */}
      <section className="rounded-xl border border-line bg-surface-alt px-4 sm:px-5 py-4 themed">
        <div className="flex items-start gap-2.5">
          <KeyRound className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" strokeWidth={2.2} />
          <div className="min-w-0">
            <h2 className="text-[13.5px] font-bold tracking-tight text-ink">
              Granting access is deliberately not possible from this page
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-muted max-w-3xl">
              A platform operator is the one actor row-level security does not
              constrain, so gaining that access should require the database
              password — not a form. There is no INSERT policy on{' '}
              <code className="font-mono text-[11.5px] text-ink">platform_admins</code>, and{' '}
              <code className="font-mono text-[11.5px] text-ink">grant_platform_admin()</code>{' '}
              is revoked from the API role. A bug in a route handler cannot
              escalate anyone.
            </p>

            <div className="mt-3 rounded-lg border border-line bg-ground overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 border-b border-line">
                <Terminal className="w-3 h-3 text-ink-faint" />
                <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint">
                  Supabase → SQL editor
                </span>
              </div>
              <pre className="px-3 py-2.5 overflow-x-auto font-mono text-[11.5px] leading-relaxed text-ink">
                {"select public.grant_platform_admin('name@example.com', 'on-call');"}
              </pre>
            </div>

            <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
              They must have signed up first — the profile has to exist before it
              can be promoted. Revoking, by contrast, is available below: the
              risks are asymmetric, and needing a database password to cut off a
              departing colleague is what leaves stale access in place.
            </p>
          </div>
        </div>
      </section>

      {/* ── The list ── */}
      <section className="rounded-xl border border-line bg-surface overflow-hidden themed">
        <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
          <h2 className="text-[14px] font-bold tracking-tight text-ink">Access list</h2>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint tabular-nums">
            {operators.length} total
          </span>
        </div>

        {operators.length === 0 ? (
          <div className="px-5 py-12 text-center">
            <ShieldCheck className="w-7 h-7 mx-auto text-ink-faint mb-3" strokeWidth={1.8} />
            <p className="text-[13.5px] font-bold text-ink">No operators recorded</p>
            <p className="text-[12px] text-ink-faint mt-1 max-w-sm mx-auto leading-relaxed">
              If you are reading this page you hold access, so this most likely
              means the list could not be read rather than that it is empty.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-[color:var(--line)]">
            {operators.map((op) => (
              <li
                key={op.profileId}
                className={`px-4 sm:px-5 py-4 transition-colors duration-150 ${
                  op.isActive ? 'hover:bg-surface-alt' : 'opacity-60'
                }`}
              >
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <div
                      className={`w-9 h-9 rounded-lg flex items-center justify-center font-bold text-[12px] shrink-0 ${
                        op.isActive
                          ? 'bg-accent-soft text-accent'
                          : 'bg-surface-alt text-ink-faint'
                      }`}
                      aria-hidden="true"
                    >
                      {initials(op)}
                    </div>

                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-bold text-[14px] text-ink truncate">
                          {op.fullName || op.email || 'Unknown user'}
                        </span>
                        {op.isSelf && (
                          <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold bg-accent text-white">
                            you
                          </span>
                        )}
                        {op.note && (
                          <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold bg-surface-alt text-ink-muted">
                            {op.note}
                          </span>
                        )}
                        {!op.isActive && (
                          <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold bg-surface-alt text-ink-faint">
                            revoked
                          </span>
                        )}
                      </div>

                      {op.fullName && op.email && (
                        <div className="font-mono text-[11px] text-ink-faint truncate mt-0.5">
                          {op.email}
                        </div>
                      )}

                      <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
                        <div className="flex items-baseline gap-1.5">
                          <dt className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
                            granted
                          </dt>
                          <dd className="text-[12px] text-ink-muted">{fmtDate(op.grantedAt)}</dd>
                        </div>

                        <div className="flex items-baseline gap-1.5">
                          <dt className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
                            by
                          </dt>
                          <dd className="text-[12px] text-ink-muted truncate max-w-[18rem]">
                            {op.isBootstrap ? (
                              // No granter row exists for the first operator —
                              // there was nobody to do it.
                              <span className="inline-flex items-center gap-1">
                                <Terminal className="w-3 h-3 text-ink-faint" />
                                SQL editor
                              </span>
                            ) : (
                              op.grantedByEmail ?? 'unknown'
                            )}
                          </dd>
                        </div>

                        {!op.isActive && (
                          <div className="flex items-baseline gap-1.5">
                            <dt className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
                              revoked
                            </dt>
                            <dd className="text-[12px] text-ink-muted">{fmtDate(op.revokedAt)}</dd>
                          </div>
                        )}

                        {op.isActive && fmtDateTime(op.lastSeenAt) && (
                          <div className="flex items-baseline gap-1.5">
                            <dt className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
                              <Clock className="w-3 h-3 inline -mt-0.5" />
                            </dt>
                            <dd className="text-[12px] text-ink-muted">
                              seen {fmtDateTime(op.lastSeenAt)}
                            </dd>
                          </div>
                        )}
                      </dl>
                    </div>
                  </div>

                  <div className="shrink-0">
                    <RevokeOperatorButton operator={op} activeCount={activeCount ?? 0} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── What an operator can and cannot do ── */}
      <section className="rounded-xl border border-line bg-surface px-4 sm:px-5 py-4 themed">
        <h2 className="text-[13.5px] font-bold tracking-tight text-ink mb-3">
          What platform access allows
        </h2>
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-2">
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400 mb-1.5">
              can see
            </p>
            <ul className="space-y-1 text-[12px] text-ink-muted leading-relaxed">
              <li>Every organisation, its name and signup date</li>
              <li>Member, site, camera and zone counts</li>
              <li>Camera status and error messages</li>
              <li>Processing runs and why they failed</li>
            </ul>
          </div>
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-accent mb-1.5">
              cannot see
            </p>
            <ul className="space-y-1 text-[12px] text-ink-muted leading-relaxed">
              <li>Occupancy counts or utilisation</li>
              <li>Posture data of any kind</li>
              <li>Alerts and generated reports</li>
              <li>A customer&rsquo;s own audit log</li>
            </ul>
          </div>
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-faint">
          Enforced by row-level security in Postgres. Those queries return no
          rows from a platform session regardless of what the interface asks for.
        </p>
      </section>
    </div>
  );
}
