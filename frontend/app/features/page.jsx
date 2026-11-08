'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, BarChart, Maximize, Activity, Zap, Layers } from 'lucide-react';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';
import { StatsBand, HowItWorks, FAQ } from '../components/LandingSections';

// Every figure here is measured and says on what. The PPE numbers come from
// public test images, not from your cameras, which is why the note says so.
const STATS = [
  { value: '95%',   label: 'Helmet recall', note: 'On 213 public test images' },
  { value: '88%',   label: 'Vest recall',   note: 'On the same test images' },
  { value: '14 ms', label: 'PPE detection', note: 'Per image on the project GPU' },
  { value: '3',     label: 'Verdicts',      note: 'Compliant, potential violation, unverifiable' },
];

const STEPS = [
  {
    title: 'Connect a feed',
    body: 'Point VisionWorks at an RTSP camera, upload a recording, or use a webcam or phone. No extra hardware to install.',
  },
  {
    title: 'Draw your zones',
    body: 'Mark the PPE checkpoint, the restricted area and the exit directly on the video. Zones can be redrawn at any time.',
  },
  {
    title: 'Review the verdicts',
    body: 'Each worker gets a verdict with its reasons. Flagged cases wait for a supervisor to confirm or dismiss them.',
  },
];

const FAQ_ITEMS = [
  {
    q: 'What cameras does it work with?',
    a: 'Any camera that exposes an RTSP stream, plus uploaded video files in MP4, AVI, WebM, MOV and MKV. You can also run it against a plain webcam for testing.',
  },
  {
    q: 'Do I need a dedicated GPU?',
    a: 'A CUDA GPU gives you real-time throughput for detecting people and PPE. Without one the pipeline falls back to CPU automatically; recorded files still work, just slower than real time.',
  },
  {
    q: 'What happens when the picture is unclear?',
    a: 'The system says so. A worker who is far away, partly hidden or overlapped gets an unverifiable verdict instead of a guess. A violation needs sustained evidence across many clear frames, and a supervisor confirms every flag.',
  },
  {
    q: 'Does it know who the worker is?',
    a: 'No. Workers are followed as anonymous track IDs within one camera view, so a verdict is about a worker, never a named person.',
  },
];

export default function FeaturesPage() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return <div className="min-h-screen bg-ground flex flex-col"></div>;

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white flex flex-col overflow-x-hidden">
      <LandingNavbar />

      <main className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-8 flex flex-col gap-16 sm:gap-20 pb-20">

        {/* HERO — owns the full first screen so the next section can't peek above the fold. */}
        <section className="hero-screen text-center max-w-3xl mx-auto animate-fade-in-up" style={{ animationDelay: '100ms' }}>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-accent mb-5">
            Platform
          </p>
          {/* leading-[1.15] + pb-1 keeps descenders from clipping. */}
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black tracking-tight text-ink mb-6 leading-[1.15] pb-1 text-balance">
            Everything you need to{' '}
            <span className="text-accent">check safety compliance.</span>
          </h1>
          <p className="text-lg sm:text-xl text-ink-muted font-medium leading-relaxed max-w-2xl mx-auto text-balance">
            VisionWorks turns passive camera feeds into safety decisions you can audit — PPE before entry, time limits, workers down and lone-worker rules, each with its reasons.
          </p>

          <div className="mt-9 flex flex-col sm:flex-row gap-3.5 justify-center">
            <Link
              href="/dashboard"
              className="bg-inverse text-inverse px-7 py-3.5 rounded-2xl font-bold text-[15px] hover:bg-accent hover:text-white transition-all duration-300 shadow-lg flex items-center justify-center gap-2.5 group/btn"
            >
              Open the dashboard <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" />
            </Link>
            <Link
              href="/security"
              className="bg-surface text-ink border border-line px-7 py-3.5 rounded-2xl font-bold text-[15px] hover:border-[color:var(--accent)] hover:text-accent transition-all duration-300 flex items-center justify-center gap-2.5"
            >
              How we handle privacy
            </Link>
          </div>
        </section>

        {/* STATS */}
        <StatsBand stats={STATS} delay={150} />

        {/* FEATURE 1: SAFETY ZONES */}
        <section className="flex flex-col lg:flex-row gap-5 items-stretch">

          <div
            className="flex-1 bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '200ms' }}
          >
            <div className="w-12 h-12 bg-accent text-white rounded-2xl flex items-center justify-center mb-6 shadow-lg shadow-red-600/30 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300">
              <Maximize className="w-6 h-6" />
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">Safety zones</h2>
            <p className="text-[15px] text-ink-muted font-medium leading-relaxed mb-6">
              Draw polygonal zones straight onto your video feed and give each one a meaning: PPE checkpoint, restricted area or exit. Rules attach to zones, so VisionWorks checks only what matters and ignores the rest of the frame.
            </p>
            <ul className="flex flex-col gap-3">
              {['Unlimited zones per camera', 'Checkpoint, restricted and exit zones', 'Drag-and-drop boundary editing'].map((item, i) => (
                <li key={i} className="flex items-center gap-3 font-bold text-[13px] text-ink-faint group-hover:translate-x-1 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                  <div className="w-1.5 h-1.5 rounded-full bg-accent shrink-0"></div>
                  {item}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex-1 grid grid-cols-1 grid-rows-2 gap-5">
            <div
              className="card-dark rounded-3xl p-7 sm:p-8 flex flex-col justify-between animate-fade-in-up hover:-translate-y-1 transition-all duration-500 group"
              style={{ animationDelay: '300ms' }}
            >
              <BarChart className="w-8 h-8 text-accent mb-4 group-hover:scale-110 transition-all duration-300" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 leading-[1.15] group-hover:text-accent transition-colors">Time limits</h3>
                <p className="opacity-70 font-medium text-[14px] leading-relaxed">See exactly how long a worker stays in a zone, with a warning before the limit is reached and a flag when it is passed.</p>
              </div>
            </div>

            <div
              className="bg-accent text-white rounded-3xl p-7 sm:p-8 flex flex-col justify-between shadow-lg shadow-red-600/20 animate-fade-in-up hover:-translate-y-1 hover:shadow-xl hover:shadow-red-600/40 transition-all duration-500 group"
              style={{ animationDelay: '400ms' }}
            >
              <Layers className="w-8 h-8 text-white mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 leading-[1.15]">Evidence you can review</h3>
                <p className="text-red-50 font-medium text-[14px] leading-relaxed">Every flag keeps its reason, the numbers behind it and a snapshot, so a supervisor can check it in seconds.</p>
              </div>
            </div>
          </div>

        </section>

        {/* FEATURE 2: PPE AND PROCEDURE CHECKS */}
        <section className="flex flex-col lg:flex-row-reverse gap-5 items-stretch">

          <div
            className="flex-1 bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '500ms' }}
          >
            <div className="w-12 h-12 bg-inverse text-inverse rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 group-hover:bg-accent group-hover:text-white transition-all duration-300">
              <Activity className="w-6 h-6" />
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">PPE and procedure checks</h2>
            <p className="text-[15px] text-ink-muted font-medium leading-relaxed mb-6">
              Helmet and vest detection is combined across many frames, so one missed detection can never create a violation. When the evidence is weak the answer is unverifiable, not guilty.
            </p>
            <ul className="flex flex-col gap-3">
              {['Evidence over time, not single frames', 'Three-way verdicts', 'Human review of every flag'].map((item, i) => (
                <li key={i} className="flex items-center gap-3 font-bold text-[13px] text-ink-faint group-hover:translate-x-1 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                  <div className="w-1.5 h-1.5 rounded-full bg-ink shrink-0"></div>
                  {item}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex-1 bg-surface-alt border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center items-center text-center animate-fade-in-up group overflow-hidden relative" style={{ animationDelay: '600ms' }}>
             {/* Decorative only: centred behind the text, never intercepts clicks. */}
             <Zap className="w-20 h-20 text-accent opacity-10 group-hover:scale-150 transition-all duration-700 absolute inset-0 m-auto pointer-events-none" aria-hidden="true" />
             <div className="relative z-10">
               <h3 className="text-4xl sm:text-5xl font-black text-ink tracking-tight leading-[1.15] pb-1 mb-2 tabular-nums">95%</h3>
               <p className="font-bold text-ink-faint uppercase tracking-[0.12em] text-[11px] mb-3">Helmet recall</p>
               <p className="text-ink-muted font-medium text-[14px] max-w-xs mx-auto leading-relaxed">Measured on 213 public test images with a YOLOv8 model. Your own cameras are measured separately before you rely on it.</p>
             </div>
          </div>

        </section>

        {/* HOW IT WORKS */}
        <HowItWorks
          steps={STEPS}
          heading="From camera to verdict in three steps"
          intro="No rewiring and no new hardware. Draw the zones, set the rules, and verdicts appear as soon as people are in view."
          delay={650}
        />

        {/* FAQ */}
        <FAQ items={FAQ_ITEMS} delay={700} />

        {/* BOTTOM CTA */}
        <section
          className="bg-surface border border-line rounded-3xl p-10 sm:p-14 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '750ms' }}
        >
          <h2 className="text-2xl sm:text-3xl font-black text-ink tracking-tight mb-3 relative z-10 leading-[1.15] pb-1">Make safety checks routine.</h2>
          <p className="text-ink-muted font-medium text-[15px] max-w-md mx-auto mb-7 relative z-10">
            Set up PPE and procedure checks on the cameras you already have.
          </p>
          <Link
            href="/dashboard"
            className="bg-inverse text-inverse px-7 py-3.5 rounded-2xl font-bold text-[15px] hover:bg-accent hover:text-white transition-all duration-300 shadow-lg flex items-center gap-2.5 relative z-10 group/btn"
          >
            Open the dashboard <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" />
          </Link>
        </section>

      </main>

      <LandingFooter />

    </div>
  );
}
