'use client';

// frontend/app/plans/page.jsx
//
// The plans, for anyone, signed in or not. The footer's "Plans" link used to point at /home#pricing, which is
// behind sign-in, so a visitor who wanted to see the prices was shown a login form instead. The cards and prices
// are the same ones Home shows (PlanCard, lib/plans.js), so the two cannot disagree. Choosing one goes to checkout;
// someone signed out passes through sign-in on the way and comes back to that checkout.

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';
import { PlanCard } from '../home/HomeScreen';
import { PLANS, yearlySaving } from '../lib/plans';

export default function PlansPage() {
  const router = useRouter();
  const [period, setPeriod] = useState('monthly');
  const [busyPlan, setBusyPlan] = useState(null);
  const featuredSaving = yearlySaving(PLANS.find((p) => p.featured)?.id ?? 'GROWTH');

  const choose = (planId) => {
    setBusyPlan(planId);
    try {
      router.push(`/home/checkout?plan=${encodeURIComponent(planId)}&period=${encodeURIComponent(period)}`);
    } catch {
      setBusyPlan(null);
    }
  };

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white flex flex-col overflow-x-hidden">
      <LandingNavbar />
      <main className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-8 pt-12 pb-20">
        <section aria-labelledby="plans-title">
          <div className="flex flex-col items-center text-center gap-4 mb-12">
            <span className="text-[0.6875rem] font-black uppercase tracking-[0.16em] text-ink-faint">Plans</span>
            <h1 id="plans-title" className="text-3xl sm:text-4xl font-black tracking-tight text-ink max-w-2xl leading-[1.1]">
              Priced by how much you monitor.
            </h1>
            <p className="text-[0.9375rem] text-ink-muted font-medium max-w-xl leading-relaxed">
              Every plan includes the full analysis pipeline and the same privacy guarantees. Higher tiers raise the
              limits, not the capabilities. Billing is a demonstration: no payment is taken.
            </p>
            <div role="radiogroup" aria-label="Billing period"
              className="inline-flex items-center gap-1 p-1.5 rounded-2xl border border-line bg-surface shadow-lg shadow-black/5 mt-2">
              {[
                { id: 'monthly', label: 'Monthly' },
                { id: 'yearly', label: featuredSaving ? `Yearly · save ${featuredSaving.percent}%` : 'Yearly' },
              ].map(({ id, label }) => (
                <button key={id} type="button" role="radio" aria-checked={period === id} onClick={() => setPeriod(id)}
                  className={`px-5 py-2 rounded-xl text-[0.8125rem] font-bold transition-all duration-300 ${
                    period === id ? 'bg-accent text-white shadow-lg shadow-red-600/30' : 'text-ink-muted hover:text-ink'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 lg:items-start">
            {PLANS.map((plan, i) => (
              <PlanCard key={plan.id} plan={plan} period={period} isPending={false} onChoose={choose} busyPlan={busyPlan}
                delay={`${150 + i * 100}ms`} />
            ))}
          </div>
        </section>
      </main>
      <LandingFooter />
    </div>
  );
}
