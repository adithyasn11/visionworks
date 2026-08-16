'use client';

// frontend/app/components/LandingSections.jsx
// Shared content blocks used by the Features and Security pages.
// Every figure here reflects what the system actually does — YOLOv8m-pose,
// 17 COCO keypoints, three posture states, no video retention.
import React, { useState } from 'react';
import { Plus, Minus } from 'lucide-react';

/* ── Stats band ──────────────────────────────────────────────────────────── */

export const StatsBand = ({ stats, delay = 0 }) => (
  <section
    className="themed animate-fade-in-up border-y border-line bg-surface rounded-3xl overflow-hidden"
    style={{ animationDelay: `${delay}ms` }}
  >
    <dl className="grid grid-cols-2 lg:grid-cols-4 divide-y divide-x divide-line [&>div]:border-line">
      {stats.map(({ value, label, note }) => (
        <div key={label} className="p-6 sm:p-8 flex flex-col gap-1 text-center">
          <dd className="text-3xl sm:text-4xl font-black tracking-tight text-ink tabular-nums leading-[1.15]">
            {value}
          </dd>
          <dt className="text-[11px] font-bold uppercase tracking-[0.12em] text-accent">
            {label}
          </dt>
          <p className="text-[13px] text-ink-muted font-medium leading-snug mt-1">{note}</p>
        </div>
      ))}
    </dl>
  </section>
);

/* ── How it works ────────────────────────────────────────────────────────── */

export const HowItWorks = ({ steps, heading = 'How it works', intro, delay = 0 }) => (
  <section className="themed animate-fade-in-up" style={{ animationDelay: `${delay}ms` }}>
    <div className="max-w-2xl mb-10">
      <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-accent mb-3">
        Getting started
      </p>
      <h2 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1 mb-3">
        {heading}
      </h2>
      {intro && <p className="text-[15px] text-ink-muted font-medium leading-relaxed">{intro}</p>}
    </div>

    {/* Numbered because these are genuinely sequential — each step depends on
        the one before it. */}
    <ol className="grid grid-cols-1 md:grid-cols-3 gap-5">
      {steps.map(({ title, body }, i) => (
        <li
          key={title}
          className="relative bg-surface border border-line rounded-2xl p-6 flex flex-col gap-2.5 hover:border-[color:var(--accent)] transition-colors duration-300 group"
        >
          <span className="w-7 h-7 rounded-lg bg-accent-soft text-accent font-black text-[13px] flex items-center justify-center tabular-nums shrink-0">
            {i + 1}
          </span>
          <h3 className="text-base font-black tracking-tight text-ink group-hover:text-accent transition-colors">
            {title}
          </h3>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed">{body}</p>
        </li>
      ))}
    </ol>
  </section>
);

/* ── FAQ ─────────────────────────────────────────────────────────────────── */

export const FAQ = ({ items, heading = 'Common questions', delay = 0 }) => {
  const [open, setOpen] = useState(0);

  return (
    <section className="themed animate-fade-in-up" style={{ animationDelay: `${delay}ms` }}>
      <div className="max-w-2xl mb-8">
        <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-accent mb-3">FAQ</p>
        <h2 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">
          {heading}
        </h2>
      </div>

      <div className="flex flex-col gap-2.5">
        {items.map(({ q, a }, i) => {
          const isOpen = open === i;
          return (
            <div
              key={q}
              className="bg-surface border border-line rounded-2xl overflow-hidden transition-colors duration-300"
            >
              <button
                type="button"
                onClick={() => setOpen(isOpen ? -1 : i)}
                aria-expanded={isOpen}
                className="w-full flex items-center justify-between gap-4 text-left px-5 sm:px-6 py-4 group"
              >
                <span className="text-[15px] font-bold tracking-tight text-ink group-hover:text-accent transition-colors">
                  {q}
                </span>
                <span className="w-6 h-6 rounded-lg bg-accent-soft text-accent flex items-center justify-center shrink-0">
                  {isOpen ? <Minus className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                </span>
              </button>
              {/* Grid-rows trick animates height without needing a fixed value. */}
              <div
                className={`grid transition-all duration-300 ease-out ${
                  isOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
                }`}
              >
                <div className="overflow-hidden">
                  <p className="px-5 sm:px-6 pb-5 text-[14px] text-ink-muted font-medium leading-relaxed max-w-2xl">
                    {a}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};
