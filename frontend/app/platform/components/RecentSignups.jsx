// frontend/app/platform/components/RecentSignups.jsx
//
// The five newest organisations, with age and a one-line setup summary.
//
// "Age" rather than a date because the useful question is "how long has this
// customer been stuck on nothing?" — a signup from 40 days ago with no cameras
// is a very different situation from one an hour old.

import React from 'react';
import Link from 'next/link';
import { Building2, ChevronRight, Inbox } from 'lucide-react';

/** Compact relative age: 3h, 5d, 2w. Absolute date past ~8 weeks. */
function age(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 14) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 8) return `${weeks}w ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function RecentSignups({ orgs }) {
  const recent = orgs.slice(0, 5);

  return (
    <div className="rounded-xl border border-line bg-surface themed">
      <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
        <div className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
          <h2 className="text-[14px] font-bold tracking-tight text-ink">Recent signups</h2>
        </div>
        {orgs.length > 5 && (
          <Link
            href="/platform/organisations"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-accent hover:underline"
          >
            view all
          </Link>
        )}
      </div>

      {recent.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <Inbox className="w-7 h-7 mx-auto text-ink-faint mb-3" strokeWidth={1.8} />
          <p className="text-[13.5px] font-bold text-ink">No organisations yet</p>
          <p className="text-[12px] text-ink-faint mt-1">
            They appear here as customers complete onboarding.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-[color:var(--line)]">
          {recent.map((o) => (
            <li key={o.id}>
              <Link
                href={`/platform/organisations/${o.id}`}
                className="group flex items-center gap-3 px-4 sm:px-5 py-3 hover:bg-surface-alt transition-colors duration-150"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13.5px] font-bold text-ink truncate">{o.name}</span>
                    {o.isSuspended && (
                      <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] font-bold bg-surface-alt text-ink-faint shrink-0">
                        suspended
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 font-mono text-[10.5px] text-ink-faint truncate">
                    {o.slug} · {o.timezone}
                  </div>
                </div>

                {/* Setup summary — the shape of what they have configured. */}
                <div className="hidden sm:flex items-center gap-3 shrink-0 font-mono text-[10.5px] tabular-nums text-ink-faint">
                  <span title="active members">{o.activeMembers}m</span>
                  <span title="cameras">{o.cameraCount}c</span>
                  <span title="zones">{o.zoneCount}z</span>
                </div>

                <span className="shrink-0 text-[11.5px] text-ink-muted tabular-nums w-[68px] text-right">
                  {age(o.createdAt)}
                </span>

                <ChevronRight className="w-4 h-4 shrink-0 text-ink-faint group-hover:text-accent group-hover:translate-x-0.5 transition-all duration-150" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
