// frontend/app/platform/components/OrgSections.jsx
//
// The detail page's data sections. All Server Components — read-only, so nothing
// ships to the client.
//
// Every value rendered here comes from the database. There is no placeholder
// data anywhere in this file: an empty section says it is empty and explains
// what would fill it, because "0 cameras" and "we could not load cameras" are
// completely different support situations.

import React from 'react';
import {
  Users, MapPin, Camera, Shapes, History, AlertCircle, Inbox,
  ShieldCheck, Wrench, Eye, CircleDot, Wifi, WifiOff, Upload, Video,
} from 'lucide-react';

/* ── shared bits ──────────────────────────────────────────────────────────── */

export function Section({ title, icon: Icon, count, error, children, empty, emptyHint }) {
  return (
    <section className="rounded-xl border border-line bg-surface overflow-hidden themed">
      <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3.5 border-b border-line">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="w-4 h-4 shrink-0 text-ink-faint" strokeWidth={2.2} />
          <h2 className="text-[14px] font-bold tracking-tight text-ink truncate">{title}</h2>
        </div>
        {count != null && (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint tabular-nums shrink-0">
            {count}
          </span>
        )}
      </div>

      {error ? (
        <div className="px-4 sm:px-5 py-4 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-accent" />
          <p className="text-[12.5px] leading-relaxed">
            <span className="font-bold text-accent">Could not load.</span>{' '}
            <span className="text-ink-muted">{error}</span>
          </p>
        </div>
      ) : empty ? (
        <div className="px-5 py-8 text-center">
          <Inbox className="w-6 h-6 mx-auto text-ink-faint mb-2.5" strokeWidth={1.8} />
          <p className="text-[13px] font-bold text-ink">{empty}</p>
          {emptyHint && (
            <p className="text-[11.5px] text-ink-faint mt-1 max-w-sm mx-auto leading-relaxed">
              {emptyHint}
            </p>
          )}
        </div>
      ) : (
        children
      )}
    </section>
  );
}

const Th = ({ children, align = 'left' }) => (
  <th
    scope="col"
    className={`px-4 py-2.5 font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint font-semibold whitespace-nowrap ${
      align === 'right' ? 'text-right' : 'text-left'
    }`}
  >
    {children}
  </th>
);

const Chip = ({ tone = 'muted', children, dot = false }) => {
  const tones = {
    muted:  'bg-surface-alt text-ink-muted',
    accent: 'bg-accent text-white',
    soft:   'bg-accent-soft text-accent',
    good:   'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] font-bold whitespace-nowrap ${tones[tone]}`}>
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />}
      {children}
    </span>
  );
};

const fmtDate = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
    : '—';

const fmtDateTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      })
    : '—';

/** Minutes-from-midnight → "09:00". */
const fmtMinute = (m) =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

const DAY_NAMES = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/* ── Members ──────────────────────────────────────────────────────────────── */

const ROLE_ICON = { ADMIN: ShieldCheck, MANAGER: Wrench, VIEWER: Eye };

export function MembersSection({ members, error }) {
  const active = members.filter((m) => !m.isPending);
  const pending = members.filter((m) => m.isPending);

  return (
    <Section
      title="Members"
      icon={Users}
      count={members.length === 0 ? '0' : `${active.length} active${pending.length ? ` · ${pending.length} invited` : ''}`}
      error={error}
      empty={members.length === 0 ? 'No members yet' : null}
      emptyHint="A member row appears when someone signs up and creates or joins this organisation."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <Th>Email</Th>
              <Th>Role</Th>
              <Th>Status</Th>
              <Th align="right">Joined</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {members.map((m) => {
              const RoleIcon = ROLE_ICON[m.role] ?? Eye;
              return (
                <tr key={m.id} className="hover:bg-surface-alt transition-colors duration-150">
                  <td className="px-4 py-3 min-w-0">
                    <div className="font-semibold text-ink truncate">
                      {m.fullName || m.email}
                    </div>
                    {m.fullName && (
                      <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                        {m.email}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-ink-muted">
                      <RoleIcon className="w-3.5 h-3.5 text-ink-faint" strokeWidth={2.2} />
                      {m.role}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {m.isPending
                      ? <Chip tone="soft">invited</Chip>
                      : m.status === 'SUSPENDED'
                        ? <Chip tone="muted">suspended</Chip>
                        : <Chip tone="good">active</Chip>}
                  </td>
                  <td className="px-4 py-3 text-right text-ink-muted whitespace-nowrap text-[12.5px]">
                    {m.isPending ? `invited ${fmtDate(m.invitedAt)}` : fmtDate(m.joinedAt)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── Sites ────────────────────────────────────────────────────────────────── */

export function SitesSection({ sites, error }) {
  return (
    <Section
      title="Sites"
      icon={MapPin}
      count={String(sites.length)}
      error={error}
      empty={sites.length === 0 ? 'No sites' : null}
      emptyHint="A site is created during onboarding and groups the cameras on one floor or building."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <Th>Site</Th>
              <Th align="right">Capacity</Th>
              <Th>Working hours</Th>
              <Th>Days</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {sites.map((s) => (
              <tr key={s.id} className="hover:bg-surface-alt transition-colors duration-150">
                <td className="px-4 py-3 min-w-0">
                  <div className="font-semibold text-ink truncate">{s.name}</div>
                  <div className="text-[11.5px] text-ink-faint truncate mt-0.5">
                    {s.location || '—'}
                    {s.timezone ? ` · ${s.timezone}` : ''}
                  </div>
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {s.capacity == null
                    ? <span className="text-ink-faint">not set</span>
                    : <span className="font-semibold text-ink">{s.capacity}</span>}
                </td>
                <td className="px-4 py-3 font-mono text-[12px] text-ink-muted whitespace-nowrap">
                  {fmtMinute(s.workdayStartMinute)}–{fmtMinute(s.workdayEndMinute)}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5, 6, 7].map((d) => {
                      const on = s.workdays.includes(d);
                      return (
                        <span
                          key={d}
                          title={DAY_NAMES[d]}
                          className={`w-[18px] h-[18px] rounded flex items-center justify-center font-mono text-[8.5px] font-bold ${
                            on ? 'bg-accent-soft text-accent' : 'bg-surface-alt text-ink-faint'
                          }`}
                        >
                          {DAY_NAMES[d][0]}
                        </span>
                      );
                    })}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── Cameras ──────────────────────────────────────────────────────────────── */

const SOURCE_ICON = { RTSP: Wifi, UPLOAD: Upload, WEBCAM: Video };

export function CamerasSection({ cameras, error }) {
  const inError = cameras.filter((c) => c.status === 'ERROR').length;

  return (
    <Section
      title="Cameras"
      icon={Camera}
      count={cameras.length === 0 ? '0' : `${cameras.length}${inError ? ` · ${inError} in error` : ''}`}
      error={error}
      empty={cameras.length === 0 ? 'No cameras' : null}
      emptyHint="Until a camera is added, nothing can be processed for this organisation."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <Th>Camera</Th>
              <Th>Source</Th>
              <Th>Status</Th>
              <Th align="right">Last seen</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {cameras.map((c) => {
              const SrcIcon = SOURCE_ICON[c.sourceType] ?? Camera;
              return (
                <tr key={c.id} className="hover:bg-surface-alt transition-colors duration-150 align-top">
                  <td className="px-4 py-3 min-w-0">
                    <div className="font-semibold text-ink truncate">{c.name}</div>
                    <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                      {c.siteName ? `${c.siteName} · ` : ''}
                      {c.frameWidth && c.frameHeight ? `${c.frameWidth}×${c.frameHeight} · ` : ''}
                      {c.fpsTarget}fps
                      {c.isCalibrated ? ' · calibrated' : ''}
                    </div>
                    {/* An error message is the single most useful thing on a
                        support call, so it is shown in full rather than truncated. */}
                    {c.lastErrorMessage && (
                      <p className="mt-1.5 font-mono text-[10.5px] leading-relaxed text-accent break-words max-w-md">
                        {c.lastErrorMessage}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-ink-muted whitespace-nowrap">
                      <SrcIcon className="w-3.5 h-3.5 text-ink-faint" strokeWidth={2.2} />
                      {c.sourceType}
                    </span>
                    {/* Presence of a credential, never its value. */}
                    {c.sourceType === 'RTSP' && (
                      <div className="font-mono text-[10px] text-ink-faint mt-0.5">
                        {c.hasRtspUrl ? 'url set' : 'no url'}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {c.status === 'ACTIVE'   && <Chip tone="good">active</Chip>}
                    {c.status === 'ERROR'    && <Chip tone="accent">error</Chip>}
                    {c.status === 'INACTIVE' && <Chip tone="muted">inactive</Chip>}
                  </td>
                  <td className="px-4 py-3 text-right text-ink-muted whitespace-nowrap text-[12.5px]">
                    {fmtDateTime(c.lastSeenAt)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── Zones ────────────────────────────────────────────────────────────────── */

export function ZonesSection({ zones, error }) {
  return (
    <Section
      title="Zones"
      icon={Shapes}
      count={String(zones.length)}
      error={error}
      empty={zones.length === 0 ? 'No zones drawn' : null}
      emptyHint="Without zones the pipeline runs but produces nothing — every detection falls outside every area. This is the most common reason a customer sees no data."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <Th>Zone</Th>
              <Th>Type</Th>
              <Th align="right">Capacity</Th>
              <Th>Camera</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {zones.map((z) => (
              <tr key={z.id} className="hover:bg-surface-alt transition-colors duration-150">
                <td className="px-4 py-3 min-w-0">
                  <div className="flex items-center gap-2">
                    {/* The customer's own colour choice, so the console reflects
                        what they see in their editor. */}
                    <span
                      aria-hidden="true"
                      className="w-2.5 h-2.5 rounded-sm shrink-0 border border-line"
                      style={z.colour ? { backgroundColor: z.colour } : undefined}
                    />
                    <span className="font-semibold text-ink truncate">{z.name}</span>
                  </div>
                  {z.excludeFromUtilisation && (
                    <div className="font-mono text-[10px] text-ink-faint mt-0.5 pl-4.5">
                      excluded from utilisation
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-[12.5px] font-semibold text-ink-muted">
                  {z.zoneType}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {z.capacity == null
                    ? <span className="text-ink-faint">n/a</span>
                    : <span className="font-semibold text-ink">{z.capacity}</span>}
                </td>
                <td className="px-4 py-3 font-mono text-[11.5px] text-ink-muted truncate">
                  {z.cameraName ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── Sessions ─────────────────────────────────────────────────────────────── */

const STATUS_CHIP = {
  DONE:       { tone: 'good',   label: 'done' },
  ERROR:      { tone: 'accent', label: 'error' },
  PROCESSING: { tone: 'soft',   label: 'processing', dot: true },
  QUEUED:     { tone: 'muted',  label: 'queued' },
  CANCELLED:  { tone: 'muted',  label: 'cancelled' },
};

export function SessionsSection({ sessions, error }) {
  return (
    <Section
      title="Recent runs"
      icon={History}
      count={sessions.length === 0 ? '0' : `last ${sessions.length}`}
      error={error}
      empty={sessions.length === 0 ? 'No processing runs' : null}
      emptyHint="A run is recorded whenever a video is uploaded or a live stream is analysed."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <Th>Source</Th>
              <Th>Status</Th>
              <Th align="right">Frames</Th>
              <Th align="right">Started</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line)]">
            {sessions.map((s) => {
              const chip = STATUS_CHIP[s.status] ?? STATUS_CHIP.QUEUED;
              const pct =
                s.totalFrames && s.totalFrames > 0
                  ? Math.round((s.processedFrames / s.totalFrames) * 100)
                  : null;
              return (
                <tr key={s.id} className="hover:bg-surface-alt transition-colors duration-150 align-top">
                  <td className="px-4 py-3 min-w-0">
                    <div className="font-semibold text-ink truncate max-w-[22rem]">
                      {s.sourceFilename || s.kind.replace(/_/g, ' ').toLowerCase()}
                    </div>
                    <div className="font-mono text-[10.5px] text-ink-faint truncate mt-0.5">
                      {s.kind}
                      {s.cameraName ? ` · ${s.cameraName}` : ''}
                      {s.fpsAchieved ? ` · ${s.fpsAchieved.toFixed(1)}fps` : ''}
                    </div>
                    {s.errorMessage && (
                      <p className="mt-1.5 font-mono text-[10.5px] leading-relaxed text-accent break-words max-w-md">
                        {s.errorMessage}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Chip tone={chip.tone} dot={chip.dot}>{chip.label}</Chip>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                    <span className="font-semibold text-ink">
                      {s.processedFrames.toLocaleString()}
                    </span>
                    {s.totalFrames != null && (
                      <span className="text-ink-faint">
                        {' / '}{s.totalFrames.toLocaleString()}
                      </span>
                    )}
                    {pct != null && pct < 100 && (
                      <div className="font-mono text-[10px] text-ink-faint mt-0.5">{pct}%</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-ink-muted whitespace-nowrap text-[12.5px]">
                    {fmtDateTime(s.queuedAt)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── The boundary ─────────────────────────────────────────────────────────── */

export function OccupancyNotice({ orgName }) {
  return (
    <div className="rounded-xl border border-line bg-surface-alt px-4 sm:px-5 py-4 themed">
      <div className="flex items-center gap-2 mb-2">
        <CircleDot className="w-4 h-4 text-ink-faint" strokeWidth={2.2} />
        <h2 className="text-[13.5px] font-bold tracking-tight text-ink">
          Occupancy not accessible
        </h2>
      </div>
      <p className="text-[12px] leading-relaxed text-ink-muted max-w-2xl">
        This page shows configuration and health only. {orgName}&rsquo;s occupancy
        counts, utilisation, alerts and reports are not readable from a platform
        session — no row-level-security policy grants a platform operator access
        to the measurement tables, so those queries return nothing regardless of
        what this interface asks for.
      </p>
    </div>
  );
}
