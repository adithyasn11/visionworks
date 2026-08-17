// frontend/app/platform/components/OrgTable.jsx
//
// The organisations table. Server Component — no interactivity beyond links, so
// nothing ships to the client.
//
// TWO LAYOUTS, ONE SOURCE. A real <table> on desktop because the whole point is
// comparing counts down a column, and stacked cards below `md` because seven
// numeric columns on a phone is unreadable. Both render from the same array, so
// they cannot disagree.

import React from 'react';
import Link from 'next/link';
import {
  ChevronRight, Building2, SearchX, Users, Camera, Shapes, MapPin,
} from 'lucide-react';
import { orgHealth } from '../../lib/platform/queries';

const HEALTH_STYLE = {
  error:     'bg-accent text-white',
  warn:      'bg-accent-soft text-accent',
  suspended: 'bg-surface-alt text-ink-faint',
  live:      'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  ok:        'bg-surface-alt text-ink-muted',
};

function HealthBadge({ org }) {
  const h = orgHealth(org);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] font-bold whitespace-nowrap ${
        HEALTH_STYLE[h.level] ?? HEALTH_STYLE.ok
      }`}
    >
      {h.level === 'live' && (
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
      )}
      {h.label}
    </span>
  );
}

/** Absolute date — in a comparison table the exact day is what you want. */
function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  });
}

/** Zero renders faint so a column of real numbers stands out from the gaps. */
function Num({ value, warn = false }) {
  const zero = value === 0;
  return (
    <span
      className={`tabular-nums ${
        warn && value > 0 ? 'text-accent font-bold' : zero ? 'text-ink-faint' : 'text-ink font-semibold'
      }`}
    >
      {value}
    </span>
  );
}

function EmptyState({ filtered }) {
  const Icon = filtered ? SearchX : Building2;
  return (
    <div className="px-5 py-16 text-center">
      <Icon className="w-8 h-8 mx-auto text-ink-faint mb-3" strokeWidth={1.8} />
      <p className="text-[14px] font-bold text-ink">
        {filtered ? 'No organisations match' : 'No organisations yet'}
      </p>
      <p className="text-[12.5px] text-ink-faint mt-1 max-w-sm mx-auto leading-relaxed">
        {filtered
          ? 'Try a different search term, or clear the filters above.'
          : 'Organisations appear here once customers sign up and complete onboarding.'}
      </p>
    </div>
  );
}

export default function OrgTable({ orgs, filtered }) {
  if (orgs.length === 0) {
    return (
      <div className="rounded-xl border border-line bg-surface themed">
        <EmptyState filtered={filtered} />
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden themed">
      {/* ── Desktop table ── */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <caption className="sr-only">
            Organisations with member, site, camera and zone counts, health status and signup date
          </caption>
          <thead>
            <tr className="border-b border-line">
              {[
                ['Organisation', 'left'],
                ['Members', 'right'],
                ['Sites', 'right'],
                ['Cameras', 'right'],
                ['Zones', 'right'],
                ['Status', 'left'],
                ['Signed up', 'right'],
              ].map(([label, align]) => (
                <th
                  key={label}
                  scope="col"
                  className={`px-4 py-2.5 font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint font-semibold ${
                    align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  {label}
                </th>
              ))}
              <th scope="col" className="w-8" />
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {orgs.map((o) => (
              <tr key={o.id} className="group hover:bg-surface-alt transition-colors duration-150">
                <th scope="row" className="px-4 py-3 text-left font-normal">
                  <Link href={`/platform/organisations/${o.id}`} className="block min-w-0">
                    <div className="font-bold text-[13.5px] text-ink truncate group-hover:text-accent transition-colors">
                      {o.name}
                    </div>
                    <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                      {o.slug} · {o.timezone}
                    </div>
                  </Link>
                </th>
                <td className="px-4 py-3 text-right">
                  <Num value={o.activeMembers} />
                  {o.pendingInvites > 0 && (
                    <span className="ml-1 font-mono text-[10px] text-ink-faint">
                      +{o.pendingInvites}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right"><Num value={o.siteCount} /></td>
                <td className="px-4 py-3 text-right">
                  <Num value={o.cameraCount} />
                  {o.camerasInError > 0 && (
                    <span className="ml-1 font-mono text-[10px] font-bold text-accent">
                      ⚠{o.camerasInError}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right"><Num value={o.zoneCount} /></td>
                <td className="px-4 py-3"><HealthBadge org={o} /></td>
                <td className="px-4 py-3 text-right text-ink-muted whitespace-nowrap text-[12.5px]">
                  {fmtDate(o.createdAt)}
                </td>
                <td className="pr-3">
                  <Link
                    href={`/platform/organisations/${o.id}`}
                    aria-label={`Open ${o.name}`}
                    className="block p-1 text-ink-faint group-hover:text-accent group-hover:translate-x-0.5 transition-all duration-150"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Mobile cards ── */}
      <ul className="md:hidden divide-y divide-[color:var(--line)]">
        {orgs.map((o) => (
          <li key={o.id}>
            <Link
              href={`/platform/organisations/${o.id}`}
              className="block px-4 py-3.5 hover:bg-surface-alt transition-colors duration-150"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-bold text-[14px] text-ink truncate">{o.name}</div>
                  <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                    {o.slug}
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 shrink-0 mt-1 text-ink-faint" />
              </div>

              <div className="mt-2.5 flex items-center gap-3.5 flex-wrap">
                <HealthBadge org={o} />
                <span className="flex items-center gap-1 text-[11.5px] text-ink-muted">
                  <Users className="w-3 h-3 text-ink-faint" /> {o.activeMembers}
                </span>
                <span className="flex items-center gap-1 text-[11.5px] text-ink-muted">
                  <MapPin className="w-3 h-3 text-ink-faint" /> {o.siteCount}
                </span>
                <span className="flex items-center gap-1 text-[11.5px] text-ink-muted">
                  <Camera className="w-3 h-3 text-ink-faint" /> {o.cameraCount}
                </span>
                <span className="flex items-center gap-1 text-[11.5px] text-ink-muted">
                  <Shapes className="w-3 h-3 text-ink-faint" /> {o.zoneCount}
                </span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
