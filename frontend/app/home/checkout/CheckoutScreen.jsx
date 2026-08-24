'use client';

// frontend/app/home/checkout/CheckoutScreen.jsx
//
// The demo checkout. Two bento cards: the order on the left, the confirmation
// panel on the right — the shape a real checkout uses, in the landing page's
// visual language (rounded-3xl, shadow-xl, hover lift, staggered entry), so the
// flow never leaves the design it started in.
//
// ─────────────────────────────────────────────────────────────────────────────
//  THERE ARE NO CARD FIELDS ON THIS SCREEN, AND THAT IS DELIBERATE.
// ─────────────────────────────────────────────────────────────────────────────
//
// It would be easy to render a card number input and a CVC box to make the demo
// look complete. That is exactly the wrong instinct. A form that looks like it
// takes a card invites someone to type a real one, and this build has no
// processor, no PCI scope, and no intention of handling it — the number would
// land in a plain Server Action argument and in any request log along the way.
// The screen states what it is instead, and the "payment method" row is a fixed,
// obviously-inert placeholder that accepts no input.
//
// If real billing is added: the card must go from the browser DIRECTLY to the
// provider's hosted element (Stripe Elements or equivalent). It must never reach
// a Server Action, and this file should still contain no card input.

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowLeft, ArrowRight, Check, CheckCircle2, Lock, ShieldCheck,
  Loader2, CreditCard, Target,
} from 'lucide-react';

import AppHeader from '../../components/AppHeader';
import LandingFooter from '../../components/LandingFooter';
import { Banner } from '../../components/AuthFormBits';
import { formatPrice } from '../../lib/plans';
import { confirmPlan } from '../actions';

export default function CheckoutScreen({
  plan, email, fullName,
}) {
  const router = useRouter();
  const [period, setPeriod] = useState('monthly');
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState(null);
  const [done, setDone] = useState(false);

  const isFree = plan.priceMonthly === 0;

  /**
   * Record the plan, then navigate where the server said to go.
   *
   * The short delay before the action is theatre, and it is the only theatre
   * here: a real authorisation takes a moment, and a plan that switches
   * instantly reads as "nothing happened". It is 900ms of a spinner labelled
   * "Confirming", not a fake progress bar implying measured work.
   *
   * `busy` is NOT cleared on success. The navigation is in flight; re-enabling
   * the button would allow a second submission recording the same plan twice.
   * It clears only on a failure the user can retry from.
   */
  const submit = async (e) => {
    e.preventDefault();
    setBanner(null);
    setBusy(true);

    try {
      await new Promise((r) => setTimeout(r, 900));

      const fd = new FormData();
      fd.set('plan', plan.id);

      const res = await confirmPlan(fd);
      if (!res.ok) {
        setBanner({ kind: 'error', text: res.message });
        setBusy(false);
        return;
      }

      // Show the confirmed state before leaving, so the transition is legible
      // rather than a screen that vanishes mid-click.
      setDone(true);
      setTimeout(() => {
        // `res.next` is decided on the SERVER, never guessed here. In practice
        // this screen is pre-membership only, so it is /onboarding — but the
        // server is the thing that knows, and asking the client to infer a
        // destination is how a flow ends up navigating somewhere stale.
        router.replace(res.next);
        router.refresh();
      }, 700);
    } catch {
      setBanner({
        kind: 'error',
        text: 'Could not reach the server. Check your connection and try again.',
      });
      setBusy(false);
    }
  };

  const included = plan.features.filter((f) => f.included);

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-accent selection:text-white flex flex-col overflow-x-hidden">
      <AppHeader email={email} fullName={fullName} />

      <main className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-8 py-10 sm:py-14">

        <Link
          href="/home#pricing"
          className="inline-flex items-center gap-2 text-[13px] font-bold text-ink-muted hover:text-accent transition-colors mb-8 group"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" aria-hidden="true" />
          Back to plans
        </Link>

        <div className="grid lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-5 items-start">

          {/* ── ORDER SUMMARY ───────────────────────────────────────────── */}
          <div
            className="bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-8 sm:p-10 flex flex-col relative overflow-hidden animate-fade-in-up group hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-500"
            style={{ animationDelay: '100ms' }}
          >
            <div
              aria-hidden="true"
              className="absolute top-0 right-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] group-hover:rotate-45 group-hover:scale-110 transition-all duration-700 pointer-events-none"
            >
              <Target className="w-72 h-72 text-ink transform rotate-12 translate-x-12 -translate-y-12" />
            </div>

            <div className="relative z-10 flex flex-col gap-7">
              <div className="flex flex-col gap-3">
                <span className="text-[11px] font-black uppercase tracking-[0.16em] text-ink-faint">
                  Your plan
                </span>
                <div className="flex items-baseline justify-between gap-4 flex-wrap">
                  <h1 className="text-4xl sm:text-5xl font-black tracking-tight text-ink group-hover:translate-x-1 transition-transform duration-500">
                    {plan.name}
                  </h1>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-3xl font-black tracking-tight text-accent">
                      {formatPrice(plan.id, period)}
                    </span>
                    {!isFree && (
                      <span className="text-[13px] font-bold text-ink-faint">
                        /{period === 'yearly' ? 'year' : 'month'}
                      </span>
                    )}
                  </div>
                </div>
                <p className="text-[15px] text-ink-muted font-medium leading-relaxed max-w-lg">
                  {plan.blurb}
                </p>
              </div>

              {/* Hidden entirely on the free tier — a yearly option for
                  something that costs nothing is a control that does nothing,
                  and a disabled one just raises the question. */}
              {!isFree && (
                <div className="flex flex-col gap-2.5">
                  <span className="text-[13px] font-black text-ink">Billing period</span>
                  <div
                    role="radiogroup"
                    aria-label="Billing period"
                    className="inline-flex items-center gap-1 p-1.5 rounded-2xl border border-line bg-ground w-max"
                  >
                    {[
                      { id: 'monthly', label: 'Monthly' },
                      { id: 'yearly', label: 'Yearly · 2 months free' },
                    ].map(({ id, label }) => (
                      <button
                        key={id}
                        type="button"
                        role="radio"
                        aria-checked={period === id}
                        onClick={() => setPeriod(id)}
                        disabled={busy || done}
                        className={`px-4 py-2 rounded-xl text-[12.5px] font-bold transition-all duration-300 disabled:opacity-60 disabled:cursor-not-allowed ${
                          period === id
                            ? 'bg-accent text-white shadow-lg shadow-red-600/30'
                            : 'text-ink-muted hover:text-ink'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div aria-hidden="true" className="h-px bg-[color:var(--line)]" />

              <div className="flex flex-col gap-4">
                <span className="text-[13px] font-black text-ink">What’s included</span>
                <ul className="grid sm:grid-cols-2 gap-3">
                  {included.map((f) => (
                    <li key={f.text} className="flex items-start gap-3">
                      <CheckCircle2 className="w-[18px] h-[18px] text-accent shrink-0 mt-0.5" aria-hidden="true" />
                      <span className="text-[13.5px] text-ink-muted font-bold">{f.text}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div aria-hidden="true" className="h-px bg-[color:var(--line)]" />

              {/* The total. Zero for every tier, because that is the truth —
                  the price above is illustrative and this is what would
                  actually be charged. */}
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-[15px] font-black text-ink">Total charged today</span>
                <span className="text-3xl font-black tracking-tight text-emerald-500">
                  $0.00
                </span>
              </div>
            </div>
          </div>

          {/* ── CONFIRMATION PANEL ──────────────────────────────────────── */}
          <div
            className="card-dark rounded-3xl p-7 sm:p-8 flex flex-col gap-6 relative overflow-hidden shadow-xl shadow-black/20 animate-fade-in-up group hover:shadow-2xl hover:shadow-red-900/30 transition-all duration-500 lg:sticky lg:top-24"
            style={{ animationDelay: '200ms' }}
          >
            <div
              aria-hidden="true"
              className="absolute -bottom-12 -right-12 w-48 h-48 border-[20px] border-white/10 rounded-full group-hover:scale-150 group-hover:border-red-900/30 transition-all duration-700 pointer-events-none"
            />

            <div className="relative z-10 flex flex-col gap-6">
              <div className="flex items-center gap-3">
                <div className="w-11 h-11 bg-accent text-white rounded-xl flex items-center justify-center shrink-0 shadow-lg shadow-red-600/30 group-hover:scale-110 transition-transform duration-300">
                  <Lock className="w-5 h-5" aria-hidden="true" />
                </div>
                <h2 className="text-xl font-black tracking-tight text-white">
                  Confirm selection
                </h2>
              </div>

              {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

              {/* The placeholder "payment method". An obviously-inert row, NOT
                  an input. See the file header for why no card field exists
                  anywhere on this screen. */}
              <div className="flex flex-col gap-2.5">
                <span className="text-[12px] font-black uppercase tracking-[0.14em] opacity-60">
                  Payment method
                </span>
                <div
                  aria-disabled="true"
                  className="flex items-center gap-3.5 rounded-2xl border-2 border-dashed border-white/15 bg-white/5 px-4 py-4 cursor-not-allowed"
                >
                  <CreditCard className="w-5 h-5 opacity-40 shrink-0" aria-hidden="true" />
                  <div className="flex flex-col min-w-0">
                    <span className="text-[13.5px] font-black opacity-70">
                      None required
                    </span>
                    <span className="text-[12px] font-medium opacity-45">
                      Start now and add billing details later.
                    </span>
                  </div>
                </div>
              </div>

              <form onSubmit={submit} className="flex flex-col gap-3">
                <button
                  type="submit"
                  disabled={busy || done}
                  className="w-full flex items-center justify-center gap-2.5 px-5 py-4 rounded-2xl bg-accent text-white font-bold text-[15px] hover:brightness-110 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-red-600/40 transition-all duration-300 disabled:opacity-70 disabled:cursor-not-allowed disabled:translate-y-0 group/btn"
                >
                  {done ? (
                    <>
                      <Check className="w-5 h-5" strokeWidth={3} aria-hidden="true" />
                      Confirmed
                    </>
                  ) : busy ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" aria-hidden="true" />
                      Confirming…
                    </>
                  ) : (
                    <>
                      {isFree ? 'Start on Starter' : `Confirm ${plan.name}`}
                      <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 transition-transform" aria-hidden="true" />
                    </>
                  )}
                </button>

                {/* aria-live so the outcome is announced, not just shown. */}
                <p className="text-[12px] font-medium text-center leading-relaxed opacity-55" aria-live="polite">
                  {done
                    ? 'Taking you to set up your organisation…'
                    : 'Next you’ll name your organisation, then you’re in.'}
                </p>
              </form>

              <div aria-hidden="true" className="h-px bg-white/10" />

              <div className="flex items-start gap-3">
                <ShieldCheck className="w-[18px] h-[18px] text-red-500 shrink-0 mt-0.5" aria-hidden="true" />
                <p className="text-[12.5px] font-medium leading-relaxed opacity-70">
                  <span className="font-black opacity-100">Change or cancel any time.</span>{' '}
                  Your plan can be switched from the workspace whenever you need a different
                  tier.
                </p>
              </div>

              <p className="text-[12px] font-medium leading-relaxed opacity-45">
                Change plan any time from{' '}
                <Link href="/home#pricing" className="font-black text-red-500 hover:underline">
                  Plans
                </Link>
                . Signed in as {email ?? 'your account'}.
              </p>
            </div>
          </div>
        </div>
      </main>

      <LandingFooter />
    </div>
  );
}
