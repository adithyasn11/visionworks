'use client';

// frontend/app/dashboard/PlanSection.jsx
//
// Subscription: the current term, what it grants, and how to change it.
//
// ADMIN ONLY. The nav entry is gated on `org.settings` in DashboardShell and
// this panel is only rendered from that view, matching every other governance
// surface — organisation settings, member management, deletion. A MANAGER runs
// the space; billing is an account matter.
//
// ─────────────────────────────────────────────────────────────────────────────
//  A NOTE FOR WHOEVER READS THIS NEXT
// ─────────────────────────────────────────────────────────────────────────────
//
// No payment processor is connected to this build. Choosing a tier records a
// choice and starts a term; nothing is charged, and no card is ever requested
// — there is deliberately no card field anywhere in the codebase.
//
// The interface no longer says so, by request. That is fine for a controlled
// demo and NOT fine the moment real users arrive: someone who completes an
// upgrade that looks real will believe they paid. Before this is exposed to
// anyone outside the team, either wire a processor or restore the disclosure.
//
// ─────────────────────────────────────────────────────────────────────────────
//  DESIGN
// ─────────────────────────────────────────────────────────────────────────────
//
// The old version was three flat stacked panels and read like a settings form.
// This is built around a HERO card — the current tier as a dark, full-bleed
// statement with the term dates and a progress rail through the billing
// period — with the tier comparison below as selectable cards rather than a
// static table.
//
// It borrows the landing page's confident shapes (the dark panel, the
// decorative ring, the accent fill) while keeping the app's rounded-2xl
// geometry, so it reads as the most important screen in the workspace without
// looking like a different product.

import React, { useCallback, useEffect, useState } from 'react';
import {
  Check, Minus, Loader2, Calendar, TrendingUp, Sparkles,
  AlertCircle, ArrowRight, Zap,
} from 'lucide-react';

import { PLANS, planName, formatPrice, getPlan } from '../lib/plans';
import { Banner } from '../components/AuthFormBits';
import { getPlanUsage, changePlan } from './planActions';

/* ── Formatting ──────────────────────────────────────────────────────────── */

/** "24 August 2026", or a dash when there is genuinely no date. */
const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
};

/**
 * Whole days from now until `iso`, or null when there is no end date.
 *
 * Compared at UTC-midnight rather than by dividing the raw millisecond
 * difference: `(end - now) / 86400000` on a renewal 20 hours away floors to 0
 * and reads "renews today" when it renews tomorrow.
 */
function daysUntil(iso) {
  if (!iso) return null;
  const end = new Date(iso);
  if (Number.isNaN(end.getTime())) return null;
  const a = Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate());
  const now = new Date();
  const b = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((a - b) / 86400000);
}

/* ── Usage tile ──────────────────────────────────────────────────────────── */

/**
 * One allowance: "2 / 10" with a bar, or a bare count when the tier is
 * unlimited.
 *
 * `consumable` separates "you have used 2 of 10 cameras" from "your history
 * window is 90 days and the tier allows 90". The first can run out; the second
 * is a SETTING sitting at its ceiling, which is the normal healthy state — and
 * painting it red told a paying customer they were out of something they were
 * simply using fully.
 */
function UsageTile({ label, used, max, suffix, consumable = true, dark = false }) {
  const unlimited = max === null || max === undefined;
  const atCap = consumable && !unlimited && used >= max;
  // Clamped: an organisation that predates enforcement could be over its limit,
  // and an unclamped bar would render wider than its own track.
  const pct = unlimited || !max ? 0 : Math.min(100, Math.round((used / max) * 100));

  return (
    <div className={`rounded-xl px-4 py-3.5 ${dark ? 'bg-white/[0.06] border border-white/10' : 'bg-surface-alt border border-line'}`}>
      <dt className={`text-[10px] font-black uppercase tracking-[0.12em] ${dark ? 'opacity-55' : 'text-ink-faint'}`}>
        {label}
      </dt>
      <dd className={`mt-1 flex items-baseline gap-1.5 ${dark ? 'text-white' : 'text-ink'}`}>
        <span className="text-[19px] font-black tracking-tight">
          {used === null ? '—' : `${used}${suffix ?? ''}`}
        </span>
        {!unlimited && used !== null && (
          <span className={`text-[12px] font-bold ${dark ? 'opacity-55' : 'text-ink-faint'}`}>
            / {max}{suffix ?? ''}
          </span>
        )}
        {unlimited && (
          <span className={`text-[10px] font-black uppercase tracking-wider ${dark ? 'opacity-55' : 'text-ink-faint'}`}>
            unlimited
          </span>
        )}
      </dd>

      {!unlimited && used !== null && (
        <div
          className={`mt-2.5 h-1.5 rounded-full overflow-hidden ${dark ? 'bg-white/10' : 'bg-[color:var(--line)]'}`}
          role="progressbar"
          aria-valuenow={used}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-label={`${label}: ${used} of ${max} used`}
        >
          <div
            className={`h-full rounded-full transition-all duration-700 ${
              atCap ? 'bg-accent' : consumable ? 'bg-emerald-500' : 'bg-white/30'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/* ── Screen ──────────────────────────────────────────────────────────────── */

export default function PlanSection({ plan, orgName, canManage = false }) {
  const [usage, setUsage] = useState(null);
  const [banner, setBanner] = useState(null);
  const [period, setPeriod] = useState('MONTHLY');
  const [pending, setPending] = useState(null);   // tier id awaiting confirmation
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const res = await getPlanUsage();
    if (res.ok) {
      setUsage(res.usage);
      // Follow the stored term rather than resetting the toggle to monthly on
      // every load — a yearly customer opening this page should see yearly.
      if (res.usage.billingPeriod) setPeriod(res.usage.billingPeriod);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const apply = async (planId) => {
    setBanner(null);
    setBusy(true);
    try {
      const res = await changePlan(planId, period);
      if (!res.ok) {
        // Refusals here are informative, not failures — "that plan allows 1
        // camera and you have 8" is the whole point of the downgrade guard.
        setBanner({ kind: 'error', text: res.message });
        return;
      }
      setBanner({ kind: 'success', text: res.message });
      setPending(null);
      await load();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  // Fails closed on an unrecognised tier: planName renders "Unknown" rather
  // than blank, and the allowances come from the database regardless.
  const current = getPlan(plan);
  const isFree = !current || current.priceMonthly === 0;
  const remaining = daysUntil(usage?.renewsAt);

  return (
    <div className="flex flex-col gap-5">

      {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

      {/* ── HERO: the current subscription ──────────────────────────────── */}
      <section className="card-dark rounded-2xl p-6 sm:p-8 relative overflow-hidden shadow-xl shadow-black/20 group">
        <div
          aria-hidden="true"
          className="absolute -top-16 -right-16 w-64 h-64 border-[24px] border-white/[0.04] rounded-full group-hover:scale-110 transition-transform duration-1000 pointer-events-none"
        />

        <div className="relative z-10">
          <div className="flex flex-wrap items-start justify-between gap-5 mb-7">
            <div>
              <div className="flex items-center gap-2 mb-2.5">
                <Sparkles className="w-3.5 h-3.5 text-accent" aria-hidden="true" />
                <span className="text-[10px] font-black uppercase tracking-[0.16em] opacity-55">
                  {orgName ? `${orgName} · current plan` : 'Current plan'}
                </span>
              </div>
              <h2 className="text-4xl sm:text-5xl font-black tracking-tight text-white leading-none">
                {planName(plan)}
              </h2>
            </div>

            <div className="text-right">
              <div className="flex items-baseline gap-1.5 justify-end">
                <span className="text-3xl font-black tracking-tight text-accent">
                  {formatPrice(plan, period === 'YEARLY' ? 'yearly' : 'monthly')}
                </span>
                {!isFree && (
                  <span className="text-[13px] font-bold opacity-55">
                    /{period === 'YEARLY' ? 'year' : 'month'}
                  </span>
                )}
              </div>
              {usage && !isFree && (
                <p className="text-[11.5px] font-bold opacity-45 mt-1">
                  Billed {usage.billingPeriod === 'YEARLY' ? 'yearly' : 'monthly'}
                </p>
              )}
            </div>
          </div>

          {/* ── Term ── */}
          <div className="rounded-xl bg-white/[0.06] border border-white/10 p-5 mb-5">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-2.5">
                <Calendar className="w-4 h-4 text-accent shrink-0" aria-hidden="true" />
                <span className="text-[12px] font-black uppercase tracking-[0.12em] opacity-55">
                  Billing period
                </span>
              </div>
              {/* Only meaningful when there IS an end date. The free tier does
                  not expire, so a countdown would be inventing one. */}
              {remaining !== null && (
                <span className="text-[11.5px] font-bold text-accent">
                  {remaining > 0
                    ? `${remaining} day${remaining === 1 ? '' : 's'} remaining`
                    : remaining === 0
                      ? 'Renews today'
                      : 'Term ended'}
                </span>
              )}
            </div>

            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.12em] opacity-45">
                  Started
                </p>
                <p className="text-[15px] font-black text-white mt-1">
                  {usage ? fmtDate(usage.startedAt) : '—'}
                </p>
              </div>
              <div className="sm:text-right">
                <p className="text-[10px] font-black uppercase tracking-[0.12em] opacity-45">
                  {isFree ? 'Expires' : 'Renews'}
                </p>
                <p className="text-[15px] font-black text-white mt-1">
                  {usage
                    ? (usage.renewsAt ? fmtDate(usage.renewsAt) : 'Does not expire')
                    : '—'}
                </p>
              </div>
            </div>

            {/* Progress through the term. Rendered only with both endpoints —
                a rail with one date is a bar with no meaning. */}
            {usage?.startedAt && usage?.renewsAt && (() => {
              const start = new Date(usage.startedAt).getTime();
              const end = new Date(usage.renewsAt).getTime();
              const now = Date.now();
              const pct = end > start
                ? Math.min(100, Math.max(0, Math.round(((now - start) / (end - start)) * 100)))
                : 0;
              return (
                <div
                  className="mt-5 h-1.5 rounded-full bg-white/10 overflow-hidden"
                  role="progressbar"
                  aria-valuenow={pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Progress through the current billing period"
                >
                  <div
                    className="h-full rounded-full bg-accent transition-all duration-700"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              );
            })()}
          </div>

          {/* ── Allowances ── */}
          <dl className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <UsageTile dark label="Cameras" used={usage?.cameras.used ?? null} max={usage?.cameras.max ?? null} />
            <UsageTile dark label="Sites"   used={usage?.sites.used ?? null}   max={usage?.sites.max ?? null} />
            <UsageTile dark label="Members" used={usage?.seats.used ?? null}   max={usage?.seats.max ?? null} />
            {/* Not consumable: a 90-day window on a 90-day tier is the setting
                at its ceiling, not an allowance about to run out. */}
            <UsageTile
              dark
              label="History"
              used={usage?.retention.used ?? null}
              max={usage?.retention.max ?? null}
              suffix="d"
              consumable={false}
            />
          </dl>

          {!usage && (
            <p className="flex items-center gap-2 text-[12px] font-medium opacity-45 mt-4">
              <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
              Reading your usage…
            </p>
          )}
        </div>
      </section>

      {/* ── CHANGE PLAN ─────────────────────────────────────────────────── */}
      <section className="glass-panel p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
          <div>
            <h2 className="flex items-center gap-2 text-[16px] font-black tracking-tight text-ink">
              <TrendingUp className="w-4 h-4 text-accent" aria-hidden="true" />
              {canManage ? 'Change plan' : 'All plans'}
            </h2>
            <p className="text-[12.5px] text-ink-muted font-medium mt-1 leading-relaxed max-w-xl">
              Every tier includes the full analysis pipeline and the same privacy guarantees.
              Higher tiers raise the limits, not the capabilities.
            </p>
          </div>

          {canManage && (
            <div
              role="radiogroup"
              aria-label="Billing period"
              className="inline-flex items-center gap-1 p-1 rounded-xl border border-line bg-surface-alt shrink-0"
            >
              {[
                { id: 'MONTHLY', label: 'Monthly' },
                { id: 'YEARLY', label: 'Yearly' },
              ].map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={period === id}
                  onClick={() => setPeriod(id)}
                  disabled={busy}
                  className={`px-4 py-1.5 rounded-lg text-[12.5px] font-bold transition-all duration-200 disabled:opacity-50 ${
                    period === id
                      ? 'bg-accent text-white shadow-sm shadow-red-600/30'
                      : 'text-ink-muted hover:text-ink'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          {PLANS.map((p) => {
            const active = p.id === plan;
            const confirming = pending === p.id;
            // Index order in PLANS is the price ladder, so comparing positions
            // answers "is this an upgrade" without hardcoding tier names.
            const isUpgrade = PLANS.findIndex((x) => x.id === p.id) > PLANS.findIndex((x) => x.id === plan);

            return (
              <div
                key={p.id}
                className={`relative rounded-xl border p-5 flex flex-col transition-all duration-300 ${
                  active
                    ? 'border-[color:var(--accent)] bg-accent-soft'
                    : confirming
                      ? 'border-[color:var(--accent)] bg-surface-alt shadow-lg'
                      : 'border-line bg-surface-alt hover:border-[color:var(--accent)] hover:-translate-y-0.5'
                }`}
              >
                {active && (
                  <span className="absolute -top-2.5 left-4 bg-accent text-white text-[9px] font-black uppercase tracking-[0.14em] px-2.5 py-0.5 rounded-full">
                    Current
                  </span>
                )}
                {!active && p.featured && (
                  <span className="absolute -top-2.5 right-4 bg-inverse text-inverse text-[9px] font-black uppercase tracking-[0.14em] px-2.5 py-0.5 rounded-full">
                    Popular
                  </span>
                )}

                <div className="flex items-baseline justify-between gap-3 mb-1">
                  <h3 className={`text-[16px] font-black tracking-tight ${active ? 'text-accent' : 'text-ink'}`}>
                    {p.name}
                  </h3>
                  <div className="flex items-baseline gap-1">
                    <span className={`text-[17px] font-black ${active ? 'text-accent' : 'text-ink'}`}>
                      {formatPrice(p.id, period === 'YEARLY' ? 'yearly' : 'monthly')}
                    </span>
                    {p.priceMonthly > 0 && (
                      <span className="text-[10.5px] font-bold text-ink-faint">
                        /{period === 'YEARLY' ? 'yr' : 'mo'}
                      </span>
                    )}
                  </div>
                </div>
                <p className="text-[11.5px] text-ink-faint font-bold mb-4">{p.tagline}</p>

                <ul className="flex flex-col gap-2 flex-1">
                  {p.features.map((f) => (
                    <li key={f.text} className="flex items-start gap-2">
                      {f.included ? (
                        <Check className="w-3.5 h-3.5 text-accent shrink-0 mt-0.5" strokeWidth={3} aria-hidden="true" />
                      ) : (
                        <Minus className="w-3.5 h-3.5 text-ink-faint shrink-0 mt-0.5" strokeWidth={3} aria-hidden="true" />
                      )}
                      {/* Prefixed for screen readers so included/excluded never
                          rests on the icon alone. */}
                      <span className={`text-[12px] font-medium ${f.included ? 'text-ink-muted' : 'text-ink-faint line-through'}`}>
                        <span className="sr-only">{f.included ? 'Included: ' : 'Not included: '}</span>
                        {f.text}
                      </span>
                    </li>
                  ))}
                </ul>

                {canManage && (
                  <div className="mt-5">
                    {active ? (
                      <div className="w-full text-center py-2.5 rounded-lg border border-line text-[12.5px] font-bold text-ink-faint">
                        Your current plan
                      </div>
                    ) : confirming ? (
                      // Two-step, because a DOWNGRADE can strand the
                      // organisation over its new limits and an upgrade starts
                      // a fresh term. One click is too few for either.
                      <div className="flex flex-col gap-2">
                        <p className="text-[11.5px] text-ink-muted font-medium leading-relaxed text-center">
                          Switch to {p.name}, billed {period === 'YEARLY' ? 'yearly' : 'monthly'}?
                        </p>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => setPending(null)}
                            disabled={busy}
                            className="flex-1 py-2.5 rounded-lg border border-line text-[12.5px] font-bold text-ink-muted hover:text-ink transition-colors disabled:opacity-50"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => apply(p.id)}
                            disabled={busy}
                            className="flex-1 py-2.5 rounded-lg bg-accent text-white text-[12.5px] font-bold hover:brightness-110 transition-[filter] disabled:opacity-60 flex items-center justify-center gap-1.5"
                          >
                            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Check className="w-3.5 h-3.5" strokeWidth={3} aria-hidden="true" />}
                            Confirm
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => { setBanner(null); setPending(p.id); }}
                        disabled={busy}
                        className={`w-full py-2.5 rounded-lg text-[12.5px] font-bold transition-all duration-200 flex items-center justify-center gap-1.5 group/btn disabled:opacity-50 ${
                          isUpgrade
                            ? 'bg-accent text-white hover:brightness-110'
                            : 'border border-line text-ink-muted hover:text-ink hover:border-field'
                        }`}
                      >
                        {isUpgrade && <Zap className="w-3.5 h-3.5" aria-hidden="true" />}
                        {isUpgrade ? `Upgrade to ${p.name}` : `Switch to ${p.name}`}
                        {isUpgrade && (
                          <ArrowRight className="w-3.5 h-3.5 group-hover/btn:translate-x-0.5 transition-transform" aria-hidden="true" />
                        )}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Only shown to someone who cannot act, so it answers "why is there no
            button" rather than stating a rule at the person who has it. */}
        {!canManage && (
          <div className="flex items-start gap-2.5 mt-5 rounded-lg border border-line bg-surface-alt px-4 py-3">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-ink-faint" aria-hidden="true" />
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              Only an <strong className="text-ink">administrator</strong> can change the plan.
              Ask one of your organisation’s admins if you need a different tier.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
