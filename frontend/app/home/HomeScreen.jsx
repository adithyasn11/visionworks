'use client';

// frontend/app/home/HomeScreen.jsx
//
// The plan gate. Shown to a signed-in user who has NO organisation yet, and to
// nobody else — app/home/page.jsx redirects members straight to /dashboard.
//
// Because of that, this component has NO "do you have an org" branches. It used
// to, and every one of them was dead code that made the file harder to read
// than the screen it draws. There is exactly one audience and one action:
// choose a plan.
//
// ─────────────────────────────────────────────────────────────────────────────
//  THIS IS THE LANDING PAGE'S VISUAL LANGUAGE, NOT A NEW ONE.
// ─────────────────────────────────────────────────────────────────────────────
//
// A signed-in user should feel they walked through a door into the same
// building. The specifics are what make it read as the same design:
//
//   * BENTO CARDS.       rounded-3xl, bg-surface + border-line, shadow-xl
//                        shadow-black/5. Not the dashboard's rounded-2xl
//                        `.glass-panel` — that is the *app* surface, and using
//                        it here made the page read as a settings screen.
//   * ASYMMETRIC GRID.   A text card beside a 2x2 bento, one tile solid accent.
//   * STAGGERED ENTRY.   animate-fade-in-up with inline animationDelay in 100ms
//                        steps. Inline, not a `delay-N` class: Tailwind cannot
//                        see a class name built at runtime, and the landing
//                        page already does it this way.
//   * HOVER LIFT.        -translate-y-2, shadow-2xl, border goes accent, with
//                        decorative graphics rotating behind the content.
//   * hero-screen.       The hero owns the first viewport (globals.css sizes it
//                        as 100svh - 4rem) so the next section cannot peek
//                        above the fold and read as a half-cut box.
//
// Every colour is a token, so the page follows the theme toggle. If it is not
// on the landing page, it is not here.
//
// ─────────────────────────────────────────────────────────────────────────────
//  HONEST ABOUT BEING A DEMO
// ─────────────────────────────────────────────────────────────────────────────
//
// The disclosure is on the page, not only in a comment. A checkout that looks
// real and charges nothing is worse than one that admits it: a user who
// believes they paid expects a receipt and a refund path that do not exist.

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowRight, Minus, Building2, Video, LayoutDashboard,
  ShieldCheck, Activity, Target, CheckCircle2, Sparkles,
} from 'lucide-react';

import AppHeader from '../components/AppHeader';
import LandingFooter from '../components/LandingFooter';
import InvitationsPanel from './InvitationsPanel';
import { PLANS, formatPrice, planName } from '../lib/plans';

/* ── Setup steps ─────────────────────────────────────────────────────────── */

const STEPS = [
  {
    icon: Building2,
    title: 'Create your organisation',
    body: 'The boundary every measurement lives inside. Nobody outside it can read your occupancy data — enforced in Postgres, not in the interface.',
  },
  {
    icon: Video,
    title: 'Point it at a space',
    body: 'Upload a recording, use a webcam, or connect an RTSP camera. Draw zones over the frame to mark desks, walkways and meeting areas.',
  },
  {
    icon: LayoutDashboard,
    title: 'Read the numbers',
    body: 'Occupancy, posture and dwell time per zone per minute — with the identifying frames discarded. What remains after the people are gone.',
  },
];

/* ── Pricing ─────────────────────────────────────────────────────────────── */

function PlanCard({ plan, period, isPending, onChoose, busyPlan, delay }) {
  const busy = busyPlan === plan.id;
  const anyBusy = busyPlan !== null;
  const featured = Boolean(plan.featured);

  // "Continue checkout" resumes an abandoned attempt. Without it the pending
  // choice is invisible and the user starts the flow over.
  const label = isPending ? 'Continue checkout' : plan.cta;

  // The featured card is the dark one, mirroring the landing page's bento —
  // where the tall "Privacy first." panel is `card-dark` against light
  // neighbours. That is what carries the eye, not a badge alone.
  const dark = featured;

  return (
    <div
      className={`relative rounded-3xl p-7 sm:p-8 flex flex-col animate-fade-in-up transition-all duration-500 group ${
        dark
          ? 'card-dark shadow-xl shadow-black/20 hover:shadow-2xl hover:shadow-red-900/30 lg:-translate-y-4 hover:-translate-y-6'
          : 'bg-surface border border-line shadow-xl shadow-black/5 hover:shadow-2xl hover:border-[color:var(--accent)] hover:-translate-y-2'
      }`}
      style={{ animationDelay: delay }}
    >
      {/* Decorative ring, same device as the landing page's privacy panel. */}
      {dark && (
        <div
          aria-hidden="true"
          className="absolute -bottom-12 -right-12 w-48 h-48 border-[20px] border-white/10 rounded-full group-hover:scale-150 group-hover:border-red-900/30 transition-all duration-700 pointer-events-none"
        />
      )}

      {featured && (
        <span className="absolute -top-3 left-7 bg-accent text-white text-[10px] font-black uppercase tracking-[0.14em] px-3 py-1 rounded-full shadow-lg shadow-red-600/30 z-20">
          Most popular
        </span>
      )}
      {isPending && (
        <span className="absolute -top-3 right-7 bg-emerald-500 text-white text-[10px] font-black uppercase tracking-[0.14em] px-3 py-1 rounded-full shadow-lg shadow-emerald-600/30 z-20">
          Chosen
        </span>
      )}

      <div className="relative z-10 flex flex-col flex-1">
        <div className="flex flex-col gap-1.5">
          <h3 className={`text-2xl font-black tracking-tight transition-colors ${dark ? 'text-white group-hover:text-accent' : 'text-ink group-hover:text-accent'}`}>
            {plan.name}
          </h3>
          <p className={`text-[13px] font-bold ${dark ? 'opacity-60' : 'text-ink-faint'}`}>
            {plan.tagline}
          </p>
        </div>

        <div className="flex items-baseline gap-1.5 mt-6">
          <span className={`text-5xl font-black tracking-tight ${dark ? 'text-white' : 'text-ink'}`}>
            {formatPrice(plan.id, period)}
          </span>
          {plan.priceMonthly > 0 && (
            <span className={`text-[13px] font-bold ${dark ? 'opacity-60' : 'text-ink-faint'}`}>
              /{period === 'yearly' ? 'year' : 'month'}
            </span>
          )}
        </div>
        {/* Shown only where true, and as the arithmetic rather than a vague
            "save more" — the yearly price is ten months, not twelve. */}
        {period === 'yearly' && plan.priceMonthly > 0 && (
          <p className="text-[12px] font-black text-emerald-500 mt-1.5 uppercase tracking-wider">
            Two months free
          </p>
        )}

        <p className={`text-[14px] font-medium leading-relaxed mt-5 transition-colors ${dark ? 'opacity-70 group-hover:opacity-90' : 'text-ink-muted group-hover:text-ink'}`}>
          {plan.blurb}
        </p>

        <ul className="flex flex-col gap-3 mt-7 flex-1">
          {plan.features.map((f) => (
            <li key={f.text} className="flex items-start gap-3">
              {f.included ? (
                <CheckCircle2
                  className={`w-[18px] h-[18px] shrink-0 mt-0.5 ${dark ? 'text-red-500' : 'text-accent'}`}
                  aria-hidden="true"
                />
              ) : (
                <Minus
                  className={`w-[18px] h-[18px] shrink-0 mt-0.5 ${dark ? 'opacity-30' : 'text-ink-faint'}`}
                  strokeWidth={3}
                  aria-hidden="true"
                />
              )}
              {/* Excluded rows are dimmed AND prefixed for screen readers, so
                  the distinction never rests on colour or the icon alone. */}
              <span
                className={`text-[13.5px] font-bold ${
                  f.included
                    ? dark ? 'opacity-80' : 'text-ink-muted'
                    : dark ? 'opacity-35 line-through' : 'text-ink-faint line-through'
                }`}
              >
                <span className="sr-only">{f.included ? 'Included: ' : 'Not included: '}</span>
                {f.text}
              </span>
            </li>
          ))}
        </ul>

        <button
          type="button"
          onClick={() => onChoose(plan.id)}
          disabled={anyBusy}
          className={`mt-8 w-full flex items-center justify-center gap-2.5 px-5 py-3.5 rounded-2xl text-[14px] font-bold transition-all duration-300 disabled:cursor-not-allowed disabled:opacity-60 group/btn ${
            dark
              ? 'bg-accent text-white hover:brightness-110 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-red-600/40'
              : 'bg-inverse text-inverse hover:bg-accent hover:text-white hover:-translate-y-0.5 hover:shadow-xl hover:shadow-red-600/30'
          }`}
        >
          {busy ? 'Opening checkout…' : label}
          {!anyBusy && (
            <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  );
}

/* ── Screen ──────────────────────────────────────────────────────────────── */

export default function HomeScreen({ email, fullName, pendingPlan }) {
  const router = useRouter();
  const [period, setPeriod] = useState('monthly');
  const [busyPlan, setBusyPlan] = useState(null);

  const firstName = (fullName ?? '').trim().split(/\s+/)[0] || null;

  /**
   * Send the user to checkout for a tier.
   *
   * The FREE tier goes through checkout too rather than shortcutting to
   * onboarding. It looks like a detour, but it means ONE path records a plan
   * and ONE place decides where to go next — and the free card still needs to
   * state what it does and does not include before someone commits.
   *
   * `busyPlan` is not cleared on the success path: the navigation is in flight,
   * and re-enabling the buttons would let a second click start a second one. It
   * clears only if the push itself throws.
   */
  const choose = (planId) => {
    setBusyPlan(planId);
    try {
      router.push(`/home/checkout?plan=${encodeURIComponent(planId)}`);
    } catch {
      setBusyPlan(null);
    }
  };

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-accent selection:text-white flex flex-col overflow-x-hidden">
      {/* No `hasOrg` — this page is unreachable to members, so the header never
          has a workspace to link to. */}
      <AppHeader email={email} fullName={fullName} />

      <main className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-8">

        {/* ── PENDING INVITATIONS ─────────────────────────────────────────
            Above the hero, because it changes what this page is FOR. Someone
            invited to an existing workspace does not need to choose a plan at
            all — accepting takes them straight to the dashboard. Burying that
            under the pricing cards would sell them something they do not need.

            Renders nothing at all when there are no invitations, which is the
            case for almost everyone — so it is mounted WITHOUT a spacing
            wrapper. The panel owns its own `mb-8`, and any padding here would
            apply whether or not it drew anything, shrinking the hero's
            full-viewport sizing on every ordinary visit. (`empty:` cannot help:
            the div is not empty in CSS terms, it holds a component that
            returned null.) */}
        <InvitationsPanel />

        {/* ── HERO ────────────────────────────────────────────────────────
            Text card beside a bento of tiles — the landing hero's shape. */}
        <section className="hero-screen hero-cards flex flex-col lg:flex-row gap-5 items-stretch">

          <div
            className="flex-1 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-8 sm:p-10 flex flex-col justify-center relative overflow-hidden animate-fade-in-up group hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-500"
            style={{ animationDelay: '100ms' }}
          >
            <div
              aria-hidden="true"
              className="absolute top-0 right-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] group-hover:rotate-45 group-hover:scale-110 transition-all duration-700 pointer-events-none"
            >
              <Target className="w-80 h-80 text-ink transform rotate-12 translate-x-12 -translate-y-12" />
            </div>

            <div className="inline-flex items-center gap-2 bg-accent-soft text-accent px-3 py-1.5 rounded-full mb-6 w-max relative z-10">
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              <span className="text-[10px] font-black uppercase tracking-[0.14em]">
                {firstName ? `Welcome, ${firstName}` : 'Welcome to VisionWorks'}
              </span>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black tracking-tight text-ink mb-6 leading-[1.15] pb-1 relative z-10 group-hover:translate-x-2 transition-transform duration-500">
              Let’s set up<br />
              <span className="text-accent relative inline-block">
                your space.
                <span aria-hidden="true" className="absolute bottom-1 left-0 w-full h-2 bg-accent/20 -z-10 group-hover:h-full transition-all duration-300" />
              </span>
            </h1>

            <p className="text-lg text-ink-muted mb-8 max-w-md font-medium leading-relaxed relative z-10">
              Measure how a space is actually used — occupancy, posture and dwell time per
              zone — without keeping the footage or identifying anyone in it. Choose a plan
              to get started.
            </p>

            <div className="flex flex-col sm:flex-row gap-4 relative z-10 mb-8">
              <a
                href="#pricing"
                className="bg-inverse text-inverse px-8 py-4 rounded-2xl font-bold text-base hover:bg-accent hover:shadow-xl hover:shadow-red-600/30 hover:-translate-y-1 transition-all duration-300 flex items-center justify-center gap-3 group/btn"
              >
                Choose a plan
                <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 transition-transform" aria-hidden="true" />
              </a>

              {pendingPlan && (
                <Link
                  href={`/home/checkout?plan=${encodeURIComponent(pendingPlan)}`}
                  className="bg-surface text-ink border-2 border-line px-8 py-4 rounded-2xl font-bold text-base hover:border-[color:var(--accent)] hover:bg-surface-alt hover:-translate-y-1 hover:shadow-lg transition-all duration-300 flex items-center justify-center gap-3"
                >
                  Resume {planName(pendingPlan)}
                </Link>
              )}
            </div>

            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-5 relative z-10 text-sm font-bold text-ink-muted">
              <div className="flex items-center gap-2 group/check hover:text-emerald-500 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 opacity-70 group-hover/check:text-emerald-500 group-hover/check:scale-110 transition-all" aria-hidden="true" />
                No hardware required
              </div>
              <div className="flex items-center gap-2 group/check2 hover:text-emerald-500 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 opacity-70 group-hover/check2:text-emerald-500 group-hover/check2:scale-110 transition-all" aria-hidden="true" />
                Setup in 5 minutes
              </div>
            </div>
          </div>

          {/* Right: bento — same 2x2 shape as the landing hero's stat tiles,
              but each tile is an actual setup step, so the grid carries
              information rather than decoration. */}
          <div className="flex-1 grid grid-cols-2 grid-rows-[1.15fr_1fr] gap-5 lg:max-w-xl">

            <div
              className="card-dark rounded-3xl p-7 flex flex-col justify-end gap-4 shadow-xl shadow-black/10 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/20 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '200ms' }}
            >
              <Building2 className="w-10 h-10 text-accent mb-4 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300" aria-hidden="true" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 group-hover:text-accent transition-colors">
                  Organisation
                </h3>
                <p className="font-bold text-ink-muted uppercase tracking-widest text-[10px]">
                  Your private boundary
                </p>
              </div>
            </div>

            <div
              className="bg-accent text-white rounded-3xl p-7 flex flex-col justify-end gap-4 shadow-xl shadow-red-600/30 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '300ms' }}
            >
              <Video className="w-10 h-10 text-white mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" aria-hidden="true" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2">Any camera</h3>
                <p className="font-bold text-red-50 uppercase tracking-widest text-[10px]">
                  Upload · Webcam · RTSP
                </p>
              </div>
            </div>

            <div
              className="col-span-2 bg-surface border border-line rounded-3xl p-8 flex items-center justify-between shadow-xl shadow-black/5 animate-fade-in-up hover:-translate-y-2 hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-300 cursor-default group"
              style={{ animationDelay: '400ms' }}
            >
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <div aria-hidden="true" className="w-3 h-3 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.6)]" />
                  <span className="font-extrabold text-[10px] tracking-widest uppercase text-ink-muted">
                    Ready when you are
                  </span>
                </div>
                <h3 className="text-lg font-black text-ink tracking-tight mb-2 group-hover:text-accent transition-colors">
                  Live in minutes
                </h3>
                <p className="text-ink-muted font-medium text-[14px]">
                  Pick a plan, name your organisation, and point it at a space.
                </p>
              </div>
              <Activity className="w-16 h-16 text-ink-muted/30 group-hover:text-accent group-hover:scale-110 transition-all duration-500 hidden sm:block" aria-hidden="true" />
            </div>
          </div>
        </section>

        {/* Trust strip — same device as the landing page: fills the band under
            the hero with something useful and hands the eye down the page. */}
        <div
          className="mt-2 mb-4 flex flex-wrap items-center justify-center gap-x-8 gap-y-3 text-ink-muted animate-fade-in-up"
          style={{ animationDelay: '500ms' }}
        >
          <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-faint">
            What you get
          </span>
          <span aria-hidden="true" className="hidden sm:block w-px h-4 bg-[color:var(--line)]" />
          {['Runs on your hardware', 'No footage retained', 'Anonymous by design'].map((t) => (
            <span key={t} className="flex items-center gap-2 text-[13px] font-bold">
              <CheckCircle2 className="w-4 h-4 text-accent shrink-0" aria-hidden="true" />
              {t}
            </span>
          ))}
        </div>

        {/* ── SETUP STEPS ─────────────────────────────────────────────────── */}
        <section className="py-12">
          <div className="flex flex-col gap-3 mb-8">
            <span className="text-[11px] font-black uppercase tracking-[0.16em] text-ink-faint">
              What happens next
            </span>
            <h2 className="text-2xl sm:text-3xl font-black tracking-tight text-ink max-w-2xl leading-[1.15]">
              Three steps from an empty account to a measured room.
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {STEPS.map(({ icon: Icon, title, body }, i) => (
              <div
                key={title}
                className="bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-7 flex flex-col animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-[color:var(--accent)] transition-all duration-500"
                style={{ animationDelay: `${600 + i * 100}ms` }}
              >
                <div className="flex items-center justify-between mb-6">
                  <div className="w-12 h-12 bg-surface-alt text-ink border border-line rounded-xl flex items-center justify-center group-hover:bg-accent group-hover:text-white group-hover:border-[color:var(--accent)] transition-colors duration-300">
                    <Icon className="w-6 h-6 group-hover:scale-110 transition-transform" aria-hidden="true" />
                  </div>
                  <span className="font-mono text-[11px] font-black text-ink-faint tracking-widest">
                    0{i + 1}
                  </span>
                </div>
                <h3 className="text-xl font-black text-ink tracking-tight mb-3 group-hover:translate-x-1 transition-transform duration-300">
                  {title}
                </h3>
                <p className="text-ink-muted font-medium text-[14px] leading-relaxed group-hover:text-ink transition-colors">
                  {body}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* ── PRICING ─────────────────────────────────────────────────────
            scroll-mt-20 clears the 4rem sticky header, or the anchor lands
            with the heading hidden behind the navbar. */}
        <section id="pricing" className="scroll-mt-20 py-12">
          <div className="flex flex-col items-center text-center gap-4 mb-12">
            <span className="text-[11px] font-black uppercase tracking-[0.16em] text-ink-faint">
              Plans
            </span>
            <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-ink max-w-2xl leading-[1.1]">
              Priced by how much space you measure.
            </h2>
            <p className="text-[15px] text-ink-muted font-medium max-w-xl leading-relaxed">
              Every plan includes the full analysis pipeline and the same privacy guarantees.
              Higher tiers raise the limits, not the capabilities.
            </p>

            {/* Two named choices, so a radiogroup rather than a checkbox — a
                checkbox would have to imply one is the default state of the
                other. */}
            <div
              role="radiogroup"
              aria-label="Billing period"
              className="inline-flex items-center gap-1 p-1.5 rounded-2xl border border-line bg-surface shadow-lg shadow-black/5 mt-2"
            >
              {[
                { id: 'monthly', label: 'Monthly' },
                { id: 'yearly', label: 'Yearly' },
              ].map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={period === id}
                  onClick={() => setPeriod(id)}
                  className={`px-5 py-2 rounded-xl text-[13px] font-bold transition-all duration-300 ${
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

          {/* items-start so the featured card's -translate-y does not stretch
              its neighbours to match. */}
          <div className="grid lg:grid-cols-3 gap-5 lg:items-start">
            {PLANS.map((plan, i) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                period={period}
                isPending={pendingPlan === plan.id}
                onChoose={choose}
                busyPlan={busyPlan}
                delay={`${900 + i * 100}ms`}
              />
            ))}
          </div>

          {/* Closing reassurance, in the page's own bento language. Replaces
              what used to be a billing disclosure panel; the shape is kept
              because the pricing grid needs something to land on rather than
              ending flush against the footer. */}
          <div
            className="mt-12 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-8 sm:p-10 flex flex-col sm:flex-row items-start gap-6 animate-fade-in-up group hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-500"
            style={{ animationDelay: '1200ms' }}
          >
            <div className="w-12 h-12 bg-surface-alt text-ink border border-line rounded-xl flex items-center justify-center shrink-0 group-hover:bg-accent group-hover:text-white group-hover:border-[color:var(--accent)] transition-colors duration-300">
              <ShieldCheck className="w-6 h-6" aria-hidden="true" />
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-xl font-black text-ink tracking-tight">
                Private by design, on every plan
              </h3>
              <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-2xl group-hover:text-ink transition-colors">
                Frames are discarded the moment inference finishes. What is kept is a count
                per zone per minute — no faces, no track ids, no coordinates — so the
                database physically cannot answer what any individual did. That guarantee is
                structural, and it does not change with your tier.
              </p>
            </div>
          </div>
        </section>
      </main>

      <LandingFooter />
    </div>
  );
}
