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
import Link from 'next/link';
import {
  ArrowLeft, UserPlus, Loader2, Copy, Check, X, MoreHorizontal,
  ShieldCheck, Wrench, Eye, Clock, AlertCircle, RotateCw, Trash2, Ban,
} from 'lucide-react';

import { ThemeToggle } from '../../components/ThemeToggle';
import { Field, Banner, SubmitButton } from '../../components/AuthFormBits';
import {
  listMembers, inviteMember, resendInvite, revokeInvite,
  changeRole, setMemberStatus, removeMember,
} from './actions';
import { can } from '../../lib/permissions';

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
    : member.status === 'ACTIVE'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
      : member.status === 'INVITED'
        ? 'border-[color:var(--line)] bg-surface-alt text-ink-muted'
        : 'border-[color:var(--accent)] bg-accent-soft text-accent';

  const label = member.expired
    ? 'Invite expired'
    : member.status === 'ACTIVE'
      ? 'Active'
      : member.status === 'INVITED'
        ? 'Invited'
        : 'Suspended';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${tone}`}>
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
          <p className="text-[13px] font-black text-accent">
            Invitation link for {invite.email}
          </p>
          <p className="text-[12px] text-ink-muted font-medium mt-0.5 leading-relaxed">
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
          className="flex-1 min-w-0 rounded-lg bg-ground border-2 border-field px-3 py-2 text-[12px] font-mono text-ink"
        />
        <button
          type="button"
          onClick={copy}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-accent text-white px-3 py-2 text-[12.5px] font-bold hover:brightness-110 transition-[filter]"
        >
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      <p className="text-[11.5px] text-ink-faint font-medium leading-relaxed">
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
  const busy = busyId === member.id;
  const RoleIcon = ROLE_META[member.role]?.icon ?? Eye;

  // The database refuses to demote, suspend or remove the final active admin
  // (memberships_keep_an_admin). Disabling those controls here explains why
  // before the click, instead of surfacing the trigger's exception after it.
  const lockedByLastAdmin = isLastActiveAdmin && member.role === 'ADMIN' && member.status === 'ACTIVE';

  const close = () => setMenuOpen(false);
  const act = (fn) => { close(); onAction(member.id, fn); };

  return (
    <li className="flex items-center gap-3 px-4 py-3 border-b border-line last:border-b-0">
      <div className="w-9 h-9 rounded-lg bg-accent-soft text-accent flex items-center justify-center font-bold text-[12px] shrink-0">
        {initialsOf(member)}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13.5px] font-bold text-ink truncate">
            {member.fullName || member.email}
          </span>
          {member.isSelf && (
            <span className="text-[10px] font-black uppercase tracking-wider text-ink-faint">You</span>
          )}
        </div>
        <div className="text-[12px] text-ink-muted truncate">
          {member.fullName ? member.email : null}
          {member.status === 'ACTIVE' && member.joinedAt && (
            <span className="text-ink-faint">
              {member.fullName ? ' · ' : ''}Joined {formatDate(member.joinedAt)}
            </span>
          )}
          {member.status === 'INVITED' && (
            <span className="text-ink-faint">
              {member.fullName ? ' · ' : ''}
              {member.expired ? 'Expired ' : 'Expires '}{formatDate(member.expiresAt)}
            </span>
          )}
        </div>
      </div>

      <span className="hidden sm:inline-flex items-center gap-1.5 text-[12px] font-bold text-ink-muted shrink-0">
        <RoleIcon className="w-3.5 h-3.5" />
        {ROLE_META[member.role]?.label ?? member.role}
      </span>

      <StatusPill member={member} />

      {canManage ? (
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
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
                ) : (
                  <>
                    <p className="px-3 pt-1.5 pb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
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

                    <div className="h-px bg-[color:var(--line)] my-1.5" />

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
                      onClick={() => act(() => removeMember(member.id))}
                    >
                      Remove from organisation
                    </MenuItem>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        <span className="w-8 shrink-0" aria-hidden="true" />
      )}
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
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] font-semibold text-left transition-colors ${
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

export default function MembersScreen({ orgName, viewer }) {
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
  const pendingInvites = state.members.filter((m) => m.status === 'INVITED');

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white">
      <div className="mx-auto max-w-3xl px-[clamp(1.25rem,4vw,2rem)] py-[clamp(1.25rem,3vh,2.5rem)]">

        <div className="flex items-center justify-between gap-4 mb-8">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 text-[13px] font-bold text-ink-muted hover:text-accent transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to dashboard
          </Link>
          <ThemeToggle />
        </div>

        <header className="flex flex-col gap-2 mb-7">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Members
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            Everyone who can reach this organisation’s data. Roles decide what they can
            change — measurements are visible to every member, and to nobody outside.
          </p>
        </header>

        {banner && (
          <div className="mb-5">
            <Banner kind={banner.kind}>{banner.text}</Banner>
          </div>
        )}

        {invite && (
          <div className="mb-5">
            <InviteLinkPanel invite={invite} onDismiss={() => setInvite(null)} />
          </div>
        )}

        {/* The invite form is ADMIN-only, matching membership_insert. A manager
            sees the roster below but no way to change it. */}
        {canInvite && (
          <section className="mb-7 rounded-xl border border-line bg-surface p-4 sm:p-5">
            <h2 className="text-[15px] font-black tracking-tight text-ink mb-1 flex items-center gap-2">
              <UserPlus className="w-4 h-4 text-accent" />
              Invite someone
            </h2>
            <p className="text-[12.5px] text-ink-muted font-medium mb-4 leading-relaxed">
              They accept by signing up with this address — no token to paste, and it works
              with Google sign-in too.
            </p>

            <form onSubmit={submitInvite} noValidate className="flex flex-col gap-4">
              <Field
                id="inviteEmail"
                label="Work email"
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setFieldError(null); }}
                placeholder="colleague@company.com"
                autoComplete="off"
                maxLength={320}
                error={fieldError}
              />

              <fieldset className="flex flex-col gap-1.5">
                <legend className="text-[13px] font-bold text-ink mb-1.5">Role</legend>
                <div className="flex flex-col gap-1.5">
                  {ROLE_ORDER.map((id) => {
                    const meta = ROLE_META[id];
                    const on = role === id;
                    const Icon = meta.icon;
                    return (
                      <label
                        key={id}
                        className={`flex items-start gap-2.5 rounded-lg border-2 px-3 py-2.5 cursor-pointer transition-colors duration-150 ${
                          on ? 'border-[color:var(--accent)] bg-accent-soft' : 'border-field hover:border-field-hover'
                        }`}
                      >
                        <input
                          type="radio"
                          name="inviteRole"
                          value={id}
                          checked={on}
                          onChange={() => setRole(id)}
                          className="mt-0.5 accent-[color:var(--accent)]"
                        />
                        <span className="flex flex-col gap-0.5 min-w-0">
                          <span className={`text-[13px] font-bold flex items-center gap-1.5 ${on ? 'text-accent' : 'text-ink'}`}>
                            <Icon className="w-3.5 h-3.5" />
                            {meta.label}
                          </span>
                          <span className="text-[12px] text-ink-faint font-medium leading-snug">
                            {meta.blurb}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <SubmitButton busy={inviting}>
                {inviting ? 'Creating invitation…' : 'Create invitation'}
              </SubmitButton>
            </form>
          </section>
        )}

        {!canInvite && state.status === 'ready' && (
          <div className="mb-6 flex items-start gap-2.5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" />
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              Only an administrator can invite people or change roles. You can see who is in{' '}
              {orgName}, which is your colleague list.
            </p>
          </div>
        )}

        {state.status === 'loading' ? (
          <div className="rounded-xl border border-line bg-surface divide-y divide-[color:var(--line)]">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-3.5">
                <div className="w-9 h-9 rounded-lg bg-surface-alt animate-pulse" />
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 w-40 rounded bg-surface-alt animate-pulse" />
                  <div className="h-3 w-56 rounded bg-surface-alt animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <>
            <section className="mb-6">
              <h2 className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint mb-2.5">
                Members · {activeMembers.length}
              </h2>
              <ul className="rounded-xl border border-line bg-surface overflow-hidden">
                {activeMembers.map((member) => (
                  <MemberRow
                    key={member.id}
                    member={member}
                    canManage={canManage}
                    isLastActiveAdmin={isLastActiveAdmin}
                    busyId={busyId}
                    onAction={runAction}
                  />
                ))}
              </ul>
            </section>

            {pendingInvites.length > 0 && (
              <section>
                <h2 className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint mb-2.5">
                  Pending invitations · {pendingInvites.length}
                </h2>
                <ul className="rounded-xl border border-line bg-surface overflow-hidden">
                  {pendingInvites.map((member) => (
                    <MemberRow
                      key={member.id}
                      member={member}
                      canManage={canManage}
                      isLastActiveAdmin={isLastActiveAdmin}
                      busyId={busyId}
                      onAction={runAction}
                    />
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
