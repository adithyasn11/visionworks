// frontend/app/lib/plans.js
//
// THE PLAN CATALOGUE — one source of truth.
//
// Three screens render plans: the pricing section on /home, the checkout
// summary, and the billing panel in settings. They must never disagree about
// what a tier costs or includes, so none of them holds its own copy of the
// numbers. Adding a plan means editing this file and nothing else.
//
// ─────────────────────────────────────────────────────────────────────────────
//  THIS IS DEMO BILLING. NO MONEY MOVES.
// ─────────────────────────────────────────────────────────────────────────────
//
// There is no payment processor wired up. The checkout screen collects NO card
// details — not a number, not a CVC, not a name. It shows the chosen plan,
// simulates a short authorisation delay, and records the selection. Every
// surface that mentions billing says so in the interface, not just in a
// comment, because a convincing-looking checkout that quietly charges nothing
// is worse than an honest one: a user who believes they paid will expect a
// receipt, a refund path, and support.
//
// ─────────────────────────────────────────────────────────────────────────────
//  THE LIMITS ARE ENFORCED, AND THIS FILE IS NOT THE AUTHORITY
// ─────────────────────────────────────────────────────────────────────────────
//
// `limits` here exist to RENDER the pricing cards. The values that actually
// refuse a write live in `public.plan_limits` (prisma/sql/015_plan_limits.sql),
// enforced by BEFORE INSERT triggers on cameras, sites and memberships — so a
// direct PostgREST call with an anon key is refused exactly like a click.
//
// The numbers are therefore written twice, and that is deliberate rather than
// sloppy: a browser cannot import a Postgres function and a trigger cannot
// import a JS module. The duplication is made checkable instead of hidden —
// `node scripts/check-plan-limits.mjs` compares the two and exits non-zero if
// they drift. If they ever disagree, THE DATABASE IS RIGHT: it is the thing
// that actually enforces. Fix this file to match.
//
// Retention is CAPPED rather than enforced retroactively: the trigger stops an
// organisation raising `dataRetentionDays` above its tier, but never lowers an
// existing value — shortening retention deletes measurements, and a pricing
// rule must not destroy data as a side effect.
//
// SECURITY NOTE: the tier is not a security boundary. Cross-tenant isolation is
// RLS, and no policy in prisma/sql/003_rls_policies.sql consults `plan`. A user
// who forged their tier would unlock cosmetic limits and no data whatsoever.

/** Canonical tier ids. Must match the Postgres "PlanTier" enum (014_plans.sql). */
export const PLAN_IDS = ['FREE', 'GROWTH', 'ENTERPRISE'];

export const PLANS = [
  {
    id: 'FREE',
    name: 'Starter',
    tagline: 'For evaluating on one space.',
    // Numbers, not strings: the formatter decides how to render them, and a
    // string here would defeat the "is this the free tier" check below.
    priceMonthly: 0,
    priceYearly: 0,
    blurb: 'Everything you need to point one camera at one room and see whether the numbers match reality.',
    features: [
      { text: '1 camera', included: true },
      { text: '1 site', included: true },
      { text: '3 team members', included: true },
      { text: '7 days of history', included: true },
      { text: 'Live occupancy and posture', included: true },
      { text: 'Zone editor and heatmap', included: true },
      { text: 'Scheduled PDF reports', included: false },
      { text: 'Alert rules', included: false },
    ],
    limits: { cameras: 1, sites: 1, seats: 3, retentionDays: 7 },
    cta: 'Start free',
  },
  {
    id: 'GROWTH',
    name: 'Growth',
    tagline: 'For a floor, or a small building.',
    priceMonthly: 49,
    priceYearly: 490, // two months free — stated in the copy, not implied
    blurb: 'Multiple cameras, real retention, and the alerting that turns a dashboard into something you do not have to watch.',
    features: [
      { text: '10 cameras', included: true },
      { text: '5 sites', included: true },
      { text: '25 team members', included: true },
      { text: '90 days of history', included: true },
      { text: 'Live occupancy and posture', included: true },
      { text: 'Zone editor and heatmap', included: true },
      { text: 'Scheduled PDF reports', included: true },
      { text: 'Alert rules', included: true },
    ],
    limits: { cameras: 10, sites: 5, seats: 25, retentionDays: 90 },
    cta: 'Choose Growth',
    // Exactly one plan may carry this. Enforced by assertion at the bottom of
    // this file, because two "most popular" badges is the kind of thing that
    // survives review and looks broken in a demo.
    featured: true,
  },
  {
    id: 'ENTERPRISE',
    name: 'Enterprise',
    tagline: 'For an estate.',
    priceMonthly: 199,
    priceYearly: 1990,
    blurb: 'Unlimited capture, a year of history, and the audit trail a facilities team needs to answer for what was measured.',
    features: [
      { text: 'Unlimited cameras', included: true },
      { text: 'Unlimited sites', included: true },
      { text: 'Unlimited team members', included: true },
      { text: '365 days of history', included: true },
      { text: 'Live occupancy and posture', included: true },
      { text: 'Zone editor and heatmap', included: true },
      { text: 'Scheduled PDF reports', included: true },
      { text: 'Alert rules and audit export', included: true },
    ],
    // null means "no ceiling" — distinct from 0, which would mean "none
    // allowed". planLimit() below is the only thing that should read these.
    limits: { cameras: null, sites: null, seats: null, retentionDays: 365 },
    cta: 'Choose Enterprise',
  },
];

/** Lookup by id. Returns undefined for an unknown tier — callers must handle it. */
export function getPlan(id) {
  return PLANS.find((p) => p.id === id);
}

/**
 * Is this a recognised tier?
 *
 * Used to validate anything arriving from a URL or a form. Written as an
 * allow-list rather than a "not obviously wrong" check, so an unknown value
 * fails closed.
 */
export function isPlanId(id) {
  return typeof id === 'string' && PLAN_IDS.includes(id);
}

/**
 * The price to charge for a billing period.
 *
 * Returns a NUMBER of whole currency units. No cents anywhere in this file:
 * the demo prices are whole, and floating-point money is a bug waiting for a
 * real integration to expose it. Real billing should carry integer minor units.
 */
export function planPrice(id, period = 'monthly') {
  const plan = getPlan(id);
  if (!plan) return null;
  return period === 'yearly' ? plan.priceYearly : plan.priceMonthly;
}

/**
 * Format a price for display.
 *
 * The free tier reads "Free", not "$0" — "$0/month" invites the question of
 * what happens when it stops being zero.
 */
export function formatPrice(id, period = 'monthly') {
  const amount = planPrice(id, period);
  if (amount === null) return '—';
  if (amount === 0) return 'Free';
  return `$${amount.toLocaleString('en-US')}`;
}

/**
 * A tier's ceiling for one resource.
 *
 * Returns `Infinity` for an unlimited allowance so a caller can write
 * `count < planLimit(...)` without special-casing null, and `0` for an unknown
 * plan or resource so an unrecognised tier grants nothing. Failing closed
 * matters more than failing loudly here: the alternative is an undefined that
 * compares false against every number and silently grants everything.
 */
export function planLimit(id, resource) {
  const plan = getPlan(id);
  if (!plan) return 0;
  const value = plan.limits?.[resource];
  if (value === null) return Infinity;
  if (typeof value !== 'number') return 0;
  return value;
}

/** Human label for a tier, safe on unknown input (used in settings and badges). */
export function planName(id) {
  return getPlan(id)?.name ?? 'Unknown';
}

// Two featured plans renders two "Most popular" badges, which reads as a bug.
// Checked at module load so it fails during the build rather than in front of
// whoever is watching the demo.
if (PLANS.filter((p) => p.featured).length > 1) {
  throw new Error('plans.js: at most one plan may be `featured`.');
}

// The catalogue and the Postgres enum must agree. A tier present here but not
// in the enum would be selectable in the UI and rejected by the database with a
// message nobody could act on.
if (PLANS.length !== PLAN_IDS.length || !PLANS.every((p) => PLAN_IDS.includes(p.id))) {
  throw new Error('plans.js: PLANS and PLAN_IDS have diverged.');
}
