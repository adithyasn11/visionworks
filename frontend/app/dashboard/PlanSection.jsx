'use client';

// frontend/app/dashboard/PlanSection.jsx
//
// The plan panel inside the workspace.
//
// WHY THIS EXISTS
//
// /home is a pre-membership gate — a member is redirected to /dashboard before
// it can render. So everything the home page said about tiers had to move
// somewhere a member can actually reach, and the workspace is that place. This
// is the whole of it: which tier the organisation is on, what it grants, and
// what the other tiers would.
//
// WHY IT DOES NOT LET ANYONE CHANGE TIER
//
// There is exactly ONE path that records a plan — the checkout flow calling
// `select_plan()` — and that path is closed to members by design. Adding a
// second here would mean two places writing the same column and two places to
// keep honest, which is how a billing surface starts lying. The comparison
// table is informational; upgrading in this build is a conversation, not a
// button, and the panel says so rather than offering a control that does
// nothing.
//
// DESIGN
//
// This is the DASHBOARD, so it uses the dashboard's surfaces (`.glass-panel`,
// rounded-2xl) — not the landing page's rounded-3xl bento. The two idioms are
// deliberate: bento for the marketing/gate screens, glass panels for the app.
// Mixing them is what made the first version of the home page read like a
// settings screen.

import React, { useEffect, useState } from 'react';
import { CreditCard, Check, Minus, ShieldCheck, Sparkles, Loader2 } from 'lucide-react';

import { PLANS, planName, formatPrice, getPlan } from '../lib/plans';
import { getPlanUsage } from './planActions';

/**
 * One allowance tile: "2 / 10" with a bar, or "2" with no bar when the tier is
 * unlimited.
 *
 * `max === null` means UNLIMITED, never zero — the same distinction the SQL
 * table makes. Rendering a progress bar against no ceiling would be meaningless,
 * so unlimited tiers show the count alone.
 *
 * The bar turns accent-coloured at the cap. It is not a warning state so much
 * as an answer to "why can I not add another camera", which is the question
 * this panel exists to pre-empt.
 */
function UsageTile({ label, used, max, suffix }) {
  const unlimited = max === null || max === undefined;
  const atCap = !unlimited && used >= max;
  // Clamped: an org that was over its limit before enforcement existed would
  // otherwise render a bar wider than its track.
  const pct = unlimited || max === 0 ? 0 : Math.min(100, Math.round((used / max) * 100));

  return (
    <div className="rounded-xl border border-line bg-surface-alt px-4 py-3">
      <dt className="text-[10px] font-black uppercase tracking-[0.12em] text-ink-faint">
        {label}
      </dt>
      <dd className="text-[17px] font-black text-ink mt-1 flex items-baseline gap-1">
        {used === null ? '—' : `${used}${suffix ?? ''}`}
        {!unlimited && (
          <span className="text-[12px] font-bold text-ink-faint">
            / {max}{suffix ?? ''}
          </span>
        )}
        {unlimited && (
          <span className="text-[11px] font-bold text-ink-faint uppercase tracking-wider">
            unlimited
          </span>
        )}
      </dd>

      {!unlimited && (
        <div
          className="mt-2 h-1.5 rounded-full bg-[color:var(--line)] overflow-hidden"
          role="progressbar"
          aria-valuenow={used}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-label={`${label}: ${used} of ${max} used`}
        >
          <div
            className={`h-full rounded-full transition-all duration-500 ${atCap ? 'bg-accent' : 'bg-emerald-500'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

export default function PlanSection({ plan, planSelectedAt, orgName }) {
  // Live counts from plan_usage(), the SAME query shape the enforcement
  // triggers use — so what this panel shows and what the database refuses can
  // never disagree. Null until it resolves; the tiles render em-dashes rather
  // than a misleading zero.
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    let active = true;
    getPlanUsage().then((res) => {
      if (active && res.ok) setUsage(res.usage);
    });
    return () => { active = false; };
  }, []);

  // Fails closed: `getPlan` returns undefined for an unrecognised tier, and
  // `planName` renders it as "Unknown" rather than blank. The allowance tiles
  // do not depend on this at all — they come from plan_usage() in the database,
  // which is the same source the triggers enforce from.
  const current = getPlan(plan);
  const isFree = !current || current.priceMonthly === 0;

  return (
    <div className="flex flex-col gap-5">

      {/* ── Current tier ────────────────────────────────────────────────── */}
      <section className="glass-panel p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap mb-5">
          <div className="flex items-center gap-3">
            <span className="w-10 h-10 rounded-xl bg-accent-soft text-accent flex items-center justify-center shrink-0">
              <CreditCard className="w-5 h-5" aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-[16px] font-black tracking-tight text-ink">
                {planName(plan)}
              </h2>
              <p className="text-[12.5px] text-ink-muted font-medium">
                {orgName ? `${orgName}'s plan` : 'Your plan'}
              </p>
            </div>
          </div>

          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-black tracking-tight text-ink">
              {formatPrice(plan, 'monthly')}
            </span>
            {!isFree && (
              <span className="text-[12.5px] font-bold text-ink-faint">/month</span>
            )}
          </div>
        </div>

        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <UsageTile label="Cameras" used={usage?.cameras.used ?? null} max={usage?.cameras.max ?? null} />
          <UsageTile label="Sites"   used={usage?.sites.used ?? null}   max={usage?.sites.max ?? null} />
          <UsageTile label="Members" used={usage?.seats.used ?? null}   max={usage?.seats.max ?? null} />
          <UsageTile label="History" used={usage?.retention.used ?? null} max={usage?.retention.max ?? null} suffix=" days" />
        </dl>

        {!usage && (
          <p className="flex items-center gap-2 text-[12px] text-ink-faint font-medium mt-3">
            <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
            Reading your usage…
          </p>
        )}

        {/* Rendered only when there is a real timestamp. `planSelectedAt` is
            NULL on the free tier by design (014_plans.sql), and printing
            "Selected Invalid Date" is worse than printing nothing. */}
        {planSelectedAt && (
          <p className="text-[12px] text-ink-faint font-medium mt-4">
            Selected{' '}
            {new Date(planSelectedAt).toLocaleDateString(undefined, {
              year: 'numeric', month: 'long', day: 'numeric',
            })}
          </p>
        )}
      </section>

      {/* ── Comparison ──────────────────────────────────────────────────── */}
      <section className="glass-panel p-5 sm:p-6">
        <h2 className="text-[15px] font-black tracking-tight text-ink mb-1">
          All plans
        </h2>
        <p className="text-[12.5px] text-ink-muted font-medium mb-5 leading-relaxed">
          Every tier includes the full analysis pipeline and the same privacy guarantees.
          Higher tiers raise the limits, not the capabilities.
        </p>

        <div className="grid md:grid-cols-3 gap-4">
          {PLANS.map((p) => {
            const active = p.id === plan;
            return (
              <div
                key={p.id}
                className={`relative rounded-xl border p-5 flex flex-col transition-colors duration-200 ${
                  active
                    ? 'border-[color:var(--accent)] bg-accent-soft'
                    : 'border-line bg-surface-alt'
                }`}
              >
                {active && (
                  <span className="absolute -top-2.5 left-4 bg-accent text-white text-[9px] font-black uppercase tracking-[0.14em] px-2.5 py-0.5 rounded-full">
                    Current
                  </span>
                )}

                <div className="flex items-baseline justify-between gap-3 mb-1">
                  <h3 className={`text-[15px] font-black tracking-tight ${active ? 'text-accent' : 'text-ink'}`}>
                    {p.name}
                  </h3>
                  <span className={`text-[15px] font-black ${active ? 'text-accent' : 'text-ink'}`}>
                    {formatPrice(p.id, 'monthly')}
                  </span>
                </div>
                <p className="text-[12px] text-ink-faint font-bold mb-4">{p.tagline}</p>

                <ul className="flex flex-col gap-2">
                  {p.features.map((f) => (
                    <li key={f.text} className="flex items-start gap-2">
                      {f.included ? (
                        <Check className="w-3.5 h-3.5 text-accent shrink-0 mt-0.5" strokeWidth={3} aria-hidden="true" />
                      ) : (
                        <Minus className="w-3.5 h-3.5 text-ink-faint shrink-0 mt-0.5" strokeWidth={3} aria-hidden="true" />
                      )}
                      {/* Prefixed for screen readers so the included/excluded
                          distinction never rests on the icon alone. */}
                      <span className={`text-[12.5px] font-medium ${f.included ? 'text-ink-muted' : 'text-ink-faint line-through'}`}>
                        <span className="sr-only">{f.included ? 'Included: ' : 'Not included: '}</span>
                        {f.text}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── The demo disclosure ─────────────────────────────────────────── */}
      <section className="glass-panel p-5 sm:p-6 flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <ShieldCheck className="w-5 h-5 text-accent shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex flex-col gap-1.5">
            <h2 className="text-[14px] font-black tracking-tight text-ink">
              Demonstration billing — no payment was taken
            </h2>
            <p className="text-[12.5px] text-ink-muted font-medium leading-relaxed">
              This build has no payment processor connected. Your tier was recorded when you
              chose it during setup; nothing was charged, no card was requested, and no card
              details are stored anywhere. Prices are shown to illustrate the tiers.
            </p>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <Sparkles className="w-5 h-5 text-ink-faint shrink-0 mt-0.5" aria-hidden="true" />
          <p className="text-[12.5px] text-ink-faint font-medium leading-relaxed">
            Plan limits are advisory in this build and are not enforced against your usage —
            nothing stops you adding more cameras than your tier lists. Changing tier is not
            available in the interface; the checkout that records a plan runs once, during
            setup.
          </p>
        </div>
      </section>
    </div>
  );
}
