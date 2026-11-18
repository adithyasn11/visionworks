'use client';

// frontend/app/settings/members/MembersScreen.jsx
//
// The member roster, the invite form, and the per-row actions.
//
// WHAT THE UI IS AND IS NOT RESPONSIBLE FOR
//
// Every destructive rule here is enforced in Postgres — the last-admin trigger,
// the admin-only write policies, the invite-status constraints. What this file
// does is make the enforced rules *legible*: a disabled button with a reason
// attached is better than an enabled button that fails, and far better than a
// raw constraint name in a toast.
//
// So where a control is hidden or disabled, the database would have refused
// anyway. Nothing here is load-bearing for security, and nothing here weakens
// what the database allows.
//
// ROLE VISIBILITY
//
// A non-admin sees the roster and no controls at all. That mirrors
// `membership_select` (any member reads the roster) against
// `membership_insert/update/delete` (admins only). Hiding the list from a
// manager would hide something they are entitled to see; showing them buttons
// that always fail would be worse.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  UserPlus, Loader2, Copy, Check, X, MoreHorizontal,
  ShieldCheck, Wrench, Eye, Clock, AlertCircle, RotateCw, Trash2, Ban, UserRound, Users,
} from 'lucide-react';

import { Banner } from '../../components/AuthFormBits';
import {
  listMembers, inviteMember, resendInvite, revokeInvite,
  changeRole, setMemberStatus, removeMember,
} from './actions';
import { can } from '../../lib/permissions';
import DashboardShell from '../../dashboard/DashboardShell';

const ROLE_META = {
  ADMIN:   { label: 'Administrator', icon: ShieldCheck, blurb: 'Full control, including members and billing' },
  MANAGER: { label: 'Manager',       icon: Wrench,      blurb: 'Cameras, zones, analysis and reports' },
  VIEWER:  { label: 'Viewer',        icon: Eye,         blurb: 'Read dashboards and reports only' },
};

const ROLE_ORDER = ['ADMIN', 'MANAGER', 'VIEWER'];

/** Absolute date. Relative times ("2 days ago") go stale in an open tab. */
function formatDate(value) {
  if (!value) return null;
  try {
    return new Date(value).toLocaleDateString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric',
    });
  } catch {
    return null;
  }
}

function initialsOf(member) {
  return (member.fullName || member.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');
}

/* ── Status pill ─────────────────────────────────────────────────────────── */

function StatusPill({ member }) {
  // An expired invitation is still an INVITED row — nothing sweeps them — so it
  // gets its own appearance. "Invited" on a link that can no longer be used
  // would be a lie the admin acts on.
  const tone = member.expired
    ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400'
    : member.declined
      ? 'border-[color:var(--line-soft)] bg-surface-alt text-ink-muted'
      : member.status === 'ACTIVE'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
      : member.status === 'INVITED'
        ? 'border-[color:var(--line-soft)] bg-surface-alt text-ink-muted'
        : 'border-[color:var(--accent)] bg-accent-soft text-accent';

  const label = member.expired
    ? 'Invite expired'
    : member.declined
      ? 'Declined'
      : member.status === 'ACTIVE'
      ? 'Active'
      : member.status === 'INVITED'
        ? 'Invited'
        : 'Suspended';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold ${tone}`}>
      {member.status === 'INVITED' && <Clock className="w-3 h-3" />}
      {label}
    </span>
  );
}

/* ── The one-time invite link ────────────────────────────────────────────── */

/**
 * Shown once, after an invite is created or resent.
 *
 * The raw token exists only in this response — it is never stored (only its
 * SHA-256 hash is) and cannot be retrieved again. Resending issues a new one.
 * The panel says so, because an admin who assumes they can come back for it
 * later will lose it.
 */
function InviteLinkPanel({ invite, onDismiss }) {
  const [copied, setCopied] = useState(false);

  // `signup?invite=` carries the token so a future token-verifying route has it,
  // and `email=` prefills the address — acceptance itself matches on the email,
  // so the link works even if the token is ever dropped.
  const link = useMemo(() => {
    if (typeof window === 'undefined') return '';
    const url = new URL('/signup', window.location.origin);
    url.searchParams.set('invite', invite.inviteToken);
    url.searchParams.set('email', invite.email);
    return url.toString();
  }, [invite]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be blocked by permissions; the input is selectable, so
      // the admin can still copy by hand.
    }
  };

  return (
    <div className="rounded-xl border-2 border-[color:var(--accent)] bg-accent-soft p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.8125rem] font-black text-accent">
            Invitation link for {invite.email}
          </p>
          <p className="text-[0.75rem] text-ink-muted font-medium mt-0.5 leading-relaxed">
            Send this to them yourself — no email is sent automatically.{' '}
            <strong className="text-ink">This link is shown once</strong> and cannot be
            retrieved later; only a hash of it is stored. Resending issues a new one.
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss invitation link"
          className="shrink-0 text-ink-muted hover:text-accent transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex items-center gap-2">
        <input
          readOnly
          value={link}
          onFocus={(e) => e.target.select()}
          aria-label="Invitation link"
          className="flex-1 min-w-0 rounded-lg bg-ground border-2 border-field px-3 py-2 text-[0.75rem] font-mono text-ink"
        />
        <button
          type="button"
          onClick={copy}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-accent text-white px-3 py-2 text-[0.78125rem] font-bold hover:brightness-110 transition-[filter]"
        >
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      <p className="text-[0.71875rem] text-ink-faint font-medium leading-relaxed">
        They accept by signing up with <strong className="text-ink-muted">{invite.email}</strong> —
        including with Google. Their membership activates automatically and they skip
        onboarding entirely.
      </p>
    </div>
  );
}

/* ── One row ─────────────────────────────────────────────────────────────── */

function MemberRow({ member, canManage, isLastActiveAdmin, busyId, onAction }) {
  const [menuOpen, setMenuOpen] = useState(false);
  // Removing someone takes their access away at once, so it asks twice: the first click turns the item into a
  // confirmation, the second removes. Closing the menu forgets it.
  const [confirmRemove, setConfirmRemove] = useState(false);
  const busy = busyId === member.id;
  const RoleIcon = ROLE_META[member.role]?.icon ?? Eye;

  // The database refuses to demote, suspend or remove the final active admin
  // (memberships_keep_an_admin). Disabling those controls here explains why
  // before the click, instead of surfacing the trigger's exception after it.
  const lockedByLastAdmin = isLastActiveAdmin && member.role === 'ADMIN' && member.status === 'ACTIVE';

  const close = () => { setMenuOpen(false); setConfirmRemove(false); };
  const act = (fn) => { close(); onAction(member.id, fn); };
  const remove = () => (confirmRemove ? act(() => removeMember(member.id)) : setConfirmRemove(true));

  return (
    <li className={`relative rounded-2xl border bg-surface p-4 flex flex-col gap-3 min-w-0 ${member.isSelf ? 'border-[color:var(--accent)]' : 'border-line'}`}>
      <div className="flex items-start gap-3 min-w-0">
      <div className="w-11 h-11 rounded-xl bg-accent-soft text-accent flex items-center justify-center font-black text-[0.8125rem] shrink-0 overflow-hidden">
        {member.avatarUrl ? <img src={member.avatarUrl} alt="" className="w-full h-full object-cover" /> : initialsOf(member)}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[0.9375rem] font-black text-ink truncate" title={member.fullName || member.email}>
            {member.fullName || member.email}
          </span>
        </div>
        <div className="text-[0.78125rem] text-ink-muted truncate" title={member.email}>
          {member.fullName ? member.email : (member.jobTitle || '\u00a0')}
        </div>
      </div>

      {canManage ? (
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => { setMenuOpen((o) => !o); setConfirmRemove(false); }}
            disabled={busy}
            aria-label={`Actions for ${member.email}`}
            aria-expanded={menuOpen}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-ink-muted hover:text-ink hover:bg-surface-alt transition-colors disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <MoreHorizontal className="w-4 h-4" />}
          </button>

          {menuOpen && (
            <>
              {/* Click-away layer. A menu that only closes via its own button
                  strands the user when they click elsewhere. */}
              <div className="fixed inset-0 z-10" onClick={close} aria-hidden="true" />
              <div
                role="menu"
                className="absolute right-0 top-9 z-20 w-56 rounded-xl border border-line bg-surface shadow-lg py-1.5"
              >
                {member.status === 'INVITED' ? (
                  <>
                    <MenuItem icon={RotateCw} onClick={() => act(() => resendInvite(member.id))}>
                      Resend — new link
                    </MenuItem>
                    <MenuItem icon={Trash2} danger onClick={() => act(() => revokeInvite(member.id))}>
                      Withdraw invitation
                    </MenuItem>
                  </>
                ) : member.declined ? (
                  <>
                    {/* They turned the invitation down: there is no access to restore, only a new invitation. */}
                    <MenuItem icon={RotateCw} onClick={() => act(() => resendInvite(member.id))}>
                      Invite again — new link
                    </MenuItem>
                    <MenuItem icon={Trash2} danger onClick={remove}>
                      {confirmRemove ? 'Click again to remove' : 'Remove from the list'}
                    </MenuItem>
                  </>
                ) : (
                  <>
                    <p className="px-3 pt-1.5 pb-1 font-mono text-[0.625rem] uppercase tracking-[0.14em] text-ink-faint">
                      Role
                    </p>
                    {ROLE_ORDER.map((role) => (
                      <MenuItem
                        key={role}
                        icon={ROLE_META[role].icon}
                        selected={member.role === role}
                        disabled={member.role === role || (lockedByLastAdmin && role !== 'ADMIN')}
                        title={
                          lockedByLastAdmin && role !== 'ADMIN'
                            ? 'This is the only administrator'
                            : undefined
                        }
                        onClick={() => act(() => changeRole(member.id, role))}
                      >
                        {ROLE_META[role].label}
                      </MenuItem>
                    ))}

                    <div className="h-px bg-[color:var(--line-soft)] my-1.5" />

                    {member.status === 'ACTIVE' ? (
                      <MenuItem
                        icon={Ban}
                        disabled={lockedByLastAdmin}
                        title={lockedByLastAdmin ? 'This is the only administrator' : undefined}
                        onClick={() => act(() => setMemberStatus(member.id, 'SUSPENDED'))}
                      >
                        Suspend access
                      </MenuItem>
                    ) : (
                      <MenuItem icon={Check} onClick={() => act(() => setMemberStatus(member.id, 'ACTIVE'))}>
                        Restore access
                      </MenuItem>
                    )}

                    <MenuItem
                      icon={Trash2}
                      danger
                      disabled={lockedByLastAdmin}
                      title={lockedByLastAdmin ? 'This is the only administrator' : undefined}
                      onClick={remove}
                    >
                      {confirmRemove ? 'Click again to remove them' : 'Remove from organisation'}
                    </MenuItem>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      ) : null}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-alt px-2.5 py-1 text-[0.6875rem] font-bold text-ink-muted">
          <RoleIcon className="w-3 h-3" />
          {ROLE_META[member.role]?.label ?? member.role}
        </span>
        <StatusPill member={member} />
        {member.isSelf && <span className="px-2 py-1 rounded-full bg-accent-soft text-accent text-[0.6875rem] font-black">You</span>}
        <span className="ml-auto text-[0.6875rem] text-ink-faint whitespace-nowrap">
          {member.status === 'ACTIVE' && member.joinedAt ? `Joined ${formatDate(member.joinedAt)}`
            : member.status === 'INVITED' ? `${member.expired ? 'Expired' : 'Expires'} ${formatDate(member.expiresAt)}` : ''}
        </span>
      </div>
    </li>
  );
}

function MenuItem({ icon: Icon, children, onClick, danger, disabled, selected, title }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-[0.8125rem] font-semibold text-left transition-colors ${
        disabled
          ? 'text-ink-faint cursor-not-allowed'
          : danger
            ? 'text-accent hover:bg-accent-soft'
            : 'text-ink hover:bg-surface-alt'
      }`}
    >
      <Icon className="w-3.5 h-3.5 shrink-0" />
      <span className="flex-1 truncate">{children}</span>
      {selected && <Check className="w-3.5 h-3.5 shrink-0 text-accent" />}
    </button>
  );
}

/* ── Screen ──────────────────────────────────────────────────────────────── */

export default function MembersScreen({ orgName, viewer, viewerRole: initialRole }) {
  const [state, setState] = useState({ status: 'loading', members: [], viewerRole: null, activeAdminCount: 0 });
  const [banner, setBanner] = useState(null);
  const [invite, setInvite] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const [email, setEmail] = useState('');
  const [role, setRole] = useState('MANAGER');
  const [inviting, setInviting] = useState(false);
  const [fieldError, setFieldError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await listMembers();
      if (!res.ok) {
        setState({ status: 'error', members: [], viewerRole: null, activeAdminCount: 0 });
        setBanner({ kind: 'error', text: res.message });
        return;
      }
      setState({
        status: 'ready',
        members: res.members,
        viewerRole: res.viewerRole,
        activeAdminCount: res.activeAdminCount,
      });
    } catch {
      setState({ status: 'error', members: [], viewerRole: null, activeAdminCount: 0 });
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // The result banner is at the top; the row menus are further down. Bring it into view so an action visibly
  // did something (and a refusal is not missed).
  useEffect(() => {
    if (banner) window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [banner]);

  // Resolved through lib/permissions.js, not a hardcoded 'ADMIN' string, so
  // this and the server action that re-checks it read from one table. LAYER 1:
  // hiding the controls. The actions re-check (layer 2) and
  // membership_insert/update/delete require admin_org_ids() (layer 3).
  const canManage = can(state.viewerRole, 'members.manage');
  const canInvite = can(state.viewerRole, 'members.invite');
  const isLastActiveAdmin = state.activeAdminCount <= 1;

  const submitInvite = async (e) => {
    e.preventDefault();
    setBanner(null);
    setFieldError(null);

    const trimmed = email.trim();
    if (!trimmed) { setFieldError('Enter an email address.'); return; }

    setInviting(true);
    try {
      const fd = new FormData();
      fd.set('email', trimmed);
      fd.set('role', role);

      const res = await inviteMember(fd);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }

      setInvite({ inviteToken: res.inviteToken, email: res.email });
      setBanner({ kind: 'success', text: res.message });
      setEmail('');
      await load();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setInviting(false);
    }
  };

  /** Runs a row action, then refreshes so the list matches the database. */
  const runAction = async (membershipId, fn) => {
    setBanner(null);
    setBusyId(membershipId);
    try {
      const res = await fn();
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }
      // resend returns a fresh one-time link; surface it the same way.
      if (res.inviteToken) setInvite({ inviteToken: res.inviteToken, email: res.email });
      setBanner({ kind: 'success', text: res.message });
      await load();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusyId(null);
    }
  };

  const activeMembers = state.members.filter((m) => m.status !== 'INVITED');
  const members = activeMembers.filter((m) => !m.declined);
  const pendingInvites = state.members.filter((m) => m.status === 'INVITED');

  return (
    /* Wrapped in the same shell the dashboard uses, so the sidebar is present
       on every signed-in page rather than vanishing when you open settings.
       No `onViewChange` is passed, which puts the shell in routed mode: the
       section nav becomes links back to /dashboard instead of view switches. */
    <DashboardShell user={viewer} role={state.viewerRole ?? initialRole}>
      <div className="animate-view-in flex flex-col gap-5">

        <header className="flex flex-col gap-1.5 pb-4 border-b border-line">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-accent">{orgName}</p>
          <h1 className="text-[1.625rem] sm:text-[1.875rem] font-black tracking-tight text-ink leading-[1.15]">Members</h1>
          <p className="text-[0.84375rem] text-ink-muted">Who can see this organisation’s safety data, and what each person can change.</p>
        </header>

        {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}
        {invite && <InviteLinkPanel invite={invite} onDismiss={() => setInvite(null)} />}

        {/* Counts. */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { icon: UserRound, label: 'Members', value: members.length },
            { icon: ShieldCheck, label: 'Admins', value: members.filter((m) => m.role === 'ADMIN' && m.status === 'ACTIVE').length },
            { icon: Wrench, label: 'Managers', value: members.filter((m) => m.role === 'MANAGER' && m.status === 'ACTIVE').length },
            { icon: Clock, label: 'Invites', value: pendingInvites.length, strong: pendingInvites.length > 0 },
          ].map(({ icon: Icon, label, value, strong }) => (
            <div key={label} className="rounded-2xl border border-line bg-surface p-5 flex items-center gap-4 min-w-0">
              <span className="w-11 h-11 rounded-xl bg-accent-soft text-accent flex items-center justify-center shrink-0"><Icon className="w-5 h-5" aria-hidden="true" /></span>
              <span className="min-w-0">
                <span className={`block text-3xl font-black tabular-nums leading-none ${strong ? 'text-accent' : 'text-ink'}`}>{state.status === 'loading' ? '—' : value}</span>
                <span className="block mt-1 text-[0.8125rem] font-bold text-ink-muted leading-tight">{label}</span>
              </span>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_24rem] gap-5 items-stretch">
          {/* The people. */}
          <section className="rounded-2xl border border-line bg-surface p-5 flex flex-col gap-4 min-w-0 xl:min-h-[calc(100dvh-20.5rem)]">
            <h2 className="flex items-center gap-2 text-[0.9375rem] font-black text-ink"><Users className="w-4 h-4 text-accent" aria-hidden="true" /> People</h2>
            {state.status === 'loading' ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-3">
                {Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-[7.5rem] rounded-2xl skeleton" />)}
              </div>
            ) : (
              <ul className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-3 content-start">
                {activeMembers.map((member) => (
                  <MemberRow key={member.id} member={member} canManage={canManage} isLastActiveAdmin={isLastActiveAdmin} busyId={busyId} onAction={runAction} />
                ))}
              </ul>
            )}

            {pendingInvites.length > 0 && (
              <>
                <h2 className="mt-2 flex items-center gap-2 text-[0.9375rem] font-black text-ink"><Clock className="w-4 h-4 text-accent" aria-hidden="true" /> Invites waiting</h2>
                <ul className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-3 content-start">
                  {pendingInvites.map((member) => (
                    <MemberRow key={member.id} member={member} canManage={canManage} isLastActiveAdmin={isLastActiveAdmin} busyId={busyId} onAction={runAction} />
                  ))}
                </ul>
              </>
            )}
          </section>

          {/* Adding people, and what each role can do. */}
          <aside className="flex flex-col gap-5 min-w-0">
            {canInvite ? (
              <section className="rounded-2xl border border-line bg-surface p-5 flex flex-col gap-4 xl:flex-1">
                <h2 className="flex items-center gap-2 text-[0.9375rem] font-black text-ink"><UserPlus className="w-4 h-4 text-accent" aria-hidden="true" /> Invite someone</h2>
                <form onSubmit={submitInvite} noValidate className="flex flex-col gap-3 flex-1">
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setFieldError(null); }}
                    placeholder="name@company.com"
                    autoComplete="off"
                    maxLength={320}
                    aria-label="Work email"
                    className="w-full rounded-xl bg-ground border-2 border-field px-3.5 py-2.5 text-[0.875rem] text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)]"
                  />
                  {fieldError && <p className="text-[0.75rem] font-semibold text-accent">{fieldError}</p>}
                  <div role="radiogroup" aria-label="Role" className="flex flex-col gap-2">
                    {ROLE_ORDER.map((id) => {
                      const meta = ROLE_META[id];
                      const on = role === id;
                      const Icon = meta.icon;
                      const n = members.filter((m) => m.role === id && m.status === 'ACTIVE').length;
                      return (
                        <button key={id} type="button" role="radio" aria-checked={on} onClick={() => setRole(id)}
                          className={`flex items-center gap-3 rounded-xl border-2 px-3 py-2 text-left transition-colors ${on ? 'border-[color:var(--accent)] bg-accent-soft' : 'border-field hover:border-field-hover'}`}>
                          <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${on ? 'bg-accent text-white' : 'bg-surface-alt text-ink-muted'}`}><Icon className="w-4 h-4" aria-hidden="true" /></span>
                          <span className="min-w-0 flex-1">
                            <span className={`block text-[0.8125rem] font-black ${on ? 'text-accent' : 'text-ink'}`}>{meta.label}</span>
                            <span className="block text-[0.71875rem] text-ink-faint leading-snug">{meta.blurb}</span>
                          </span>
                          <span className="text-[0.75rem] font-bold text-ink-faint tabular-nums" title={`${n} now`}>{n}</span>
                        </button>
                      );
                    })}
                  </div>
                  <button type="submit" disabled={inviting}
                    style={{ marginTop: 'auto' }}
                    title="No email is sent: you send them the link. They accept by signing up with this address."
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-[0.875rem] font-bold text-white hover:brightness-110 transition disabled:opacity-60">
                    {inviting ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />} {inviting ? 'Creating…' : 'Create invite link'}
                  </button>
                </form>
              </section>
            ) : state.status === 'ready' && (
              <section className="rounded-2xl border border-line bg-surface-alt p-5 flex items-start gap-3">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" />
                <p className="text-[0.8125rem] text-ink-muted">Only an administrator can invite people or change roles.</p>
              </section>
            )}


          </aside>
        </div>
      </div>
    </DashboardShell>
  );
}
