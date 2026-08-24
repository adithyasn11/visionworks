'use client';

// frontend/app/home/InvitationsPanel.jsx
//
// "You have been invited to join X." — the screen that did not exist.
//
// Inviting somebody who ALREADY had an account produced nothing visible
// anywhere in the product: acceptance lived only in the signup trigger, which
// never fires again for an existing user. This is the missing surface.
//
// IT RENDERS NOTHING WHEN THERE IS NOTHING
//
// Almost every visitor to /home has no pending invitation, and a permanent
// empty "no invitations" card would be noise on the one screen that is trying
// to sell them a plan. The panel returns null until it has something to say.
//
// Design is the page's own bento language — rounded-3xl, shadow-xl, the accent
// border — so it reads as part of /home rather than a system notice bolted on.

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Mail, Check, X, Loader2, ArrowRight } from 'lucide-react';

import { Banner } from '../components/AuthFormBits';
import { listMyInvitations, acceptInvitation, declineInvitation } from './inviteActions';

/** ADMIN -> "an administrator", so the copy reads as a sentence. */
const ROLE_COPY = {
  ADMIN: 'an administrator',
  MANAGER: 'a manager',
  VIEWER: 'a viewer',
};

export default function InvitationsPanel() {
  const router = useRouter();
  const [invites, setInvites] = useState(null);   // null = not loaded yet
  const [busy, setBusy] = useState(null);         // membership id being acted on
  const [banner, setBanner] = useState(null);

  const load = useCallback(async () => {
    const res = await listMyInvitations();
    setInvites(res.invitations ?? []);
  }, []);

  useEffect(() => { load(); }, [load]);

  const accept = async (id) => {
    setBanner(null);
    setBusy(id);
    try {
      const res = await acceptInvitation(id);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setBusy(null);
        // Re-read: the usual reason acceptance fails is that the invitation was
        // withdrawn or expired while this page sat open, and the list should
        // stop showing something that is no longer actionable.
        load();
        return;
      }
      // `busy` is deliberately NOT cleared — the navigation is in flight, and
      // re-enabling the button would let a second click fire.
      router.replace(res.next);
      router.refresh();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
      setBusy(null);
    }
  };

  const decline = async (id) => {
    setBanner(null);
    setBusy(id);
    try {
      const res = await declineInvitation(id);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        return;
      }
      setBanner({ kind: 'success', text: res.message });
      await load();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(null);
    }
  };

  // Nothing to show, or not loaded yet. No skeleton: this resolves in one round
  // trip and a placeholder that usually resolves to nothing is worse than the
  // brief absence of a card nobody was waiting for.
  if (!invites || invites.length === 0) {
    // Keep a dismissed-success message visible even once the list empties, so
    // "Invitation declined." does not vanish the instant it becomes true.
    if (banner?.kind === 'success') {
      return (
        <div className="mt-8 mb-2">
          <Banner kind="success">{banner.text}</Banner>
        </div>
      );
    }
    return null;
  }

  return (
    <section
      className="mt-8 mb-2 bg-surface border-2 border-accent shadow-xl shadow-red-600/10 rounded-3xl p-6 sm:p-8 animate-fade-in-up"
      style={{ animationDelay: '50ms' }}
      aria-labelledby="invitations-heading"
    >
      <div className="flex items-center gap-3 mb-5">
        <span className="w-10 h-10 rounded-xl bg-accent text-white flex items-center justify-center shrink-0 shadow-lg shadow-red-600/30">
          <Mail className="w-5 h-5" aria-hidden="true" />
        </span>
        <div>
          <h2 id="invitations-heading" className="text-xl font-black tracking-tight text-ink">
            {invites.length === 1 ? 'You have an invitation' : `You have ${invites.length} invitations`}
          </h2>
          <p className="text-[13px] text-ink-muted font-medium">
            Join an existing workspace — no plan needed.
          </p>
        </div>
      </div>

      {banner && <div className="mb-4"><Banner kind={banner.kind}>{banner.text}</Banner></div>}

      <ul className="flex flex-col gap-3">
        {invites.map((inv) => {
          const working = busy === inv.id;
          const anyWorking = busy !== null;
          return (
            <li
              key={inv.id}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-2xl border border-line bg-surface-alt px-5 py-4"
            >
              <div className="min-w-0">
                <p className="text-[15px] font-black text-ink truncate">{inv.orgName}</p>
                <p className="text-[12.5px] text-ink-muted font-medium mt-0.5">
                  You’ll join as {ROLE_COPY[inv.role] ?? inv.role.toLowerCase()}
                  {inv.invitedBy ? <> · invited by {inv.invitedBy}</> : null}
                </p>
                {/* Only rendered when there IS an expiry. Most invitations have
                    one, but a NULL means "does not expire" and printing
                    "Expires Invalid Date" would be worse than saying nothing. */}
                {inv.expiresAt && (
                  <p className="text-[11.5px] text-ink-faint font-medium mt-1">
                    Expires{' '}
                    {new Date(inv.expiresAt).toLocaleDateString(undefined, {
                      day: 'numeric', month: 'long', year: 'numeric',
                    })}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2.5 shrink-0">
                <button
                  type="button"
                  onClick={() => decline(inv.id)}
                  disabled={anyWorking}
                  className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-xl border-2 border-line text-[13px] font-bold text-ink-muted hover:text-accent hover:border-[color:var(--accent)] transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <X className="w-3.5 h-3.5" aria-hidden="true" />
                  Decline
                </button>
                <button
                  type="button"
                  onClick={() => accept(inv.id)}
                  disabled={anyWorking}
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent text-white text-[13px] font-bold hover:brightness-110 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-red-600/30 transition-all duration-200 disabled:opacity-60 disabled:cursor-not-allowed disabled:translate-y-0 group/btn"
                >
                  {working ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                      Joining…
                    </>
                  ) : (
                    <>
                      <Check className="w-3.5 h-3.5" strokeWidth={3} aria-hidden="true" />
                      Accept
                      <ArrowRight className="w-3.5 h-3.5 group-hover/btn:translate-x-0.5 transition-transform" aria-hidden="true" />
                    </>
                  )}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
