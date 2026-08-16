'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, BarChart, Maximize, Activity, Zap, Layers } from 'lucide-react';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';
import { StatsBand, HowItWorks, FAQ } from '../components/LandingSections';

const STATS = [
  { value: '99%',    label: 'Accuracy',  note: 'Person detection on clear feeds' },
  { value: '60 FPS', label: 'Real-time', note: 'Single-pass inference on RTX 4060' },
  { value: '17',     label: 'Keypoints', note: 'Full COCO skeleton per person' },
  { value: '3',      label: 'Postures',  note: 'Sitting, standing and walking' },
];

const STEPS = [
  {
    title: 'Connect a feed',
    body: 'Point VisionWorks at an RTSP camera, upload a recording, or use a webcam. No extra hardware to install.',
  },
  {
    title: 'Draw your zones',
    body: 'Outline desks, walkways or meeting rooms directly on the video. Zones can be redrawn at any time.',
  },
  {
    title: 'Read the insights',
    body: 'Occupancy, dwell time and posture balance appear on the dashboard as the footage is processed.',
  },
];

const FAQ_ITEMS = [
  {
    q: 'What cameras does it work with?',
    a: 'Any camera that exposes an RTSP stream, plus uploaded video files in MP4, AVI, WebM, MOV and MKV. You can also run it against a plain webcam for testing.',
  },
  {
    q: 'Do I need a dedicated GPU?',
    a: 'A CUDA GPU gives you real-time throughput — roughly 60 FPS on an RTX 4060. Without one the pipeline falls back to CPU automatically; it still works, just slower.',
  },
  {
    q: 'How does it tell sitting from standing?',
    a: 'It reads the full 17-point skeleton and weighs several signals together: knee and hip angles, how far the thighs drop relative to the torso, and body span against the bounding box. A rolling vote across frames keeps the label from flickering.',
  },
  {
    q: 'Can it track more than one person?',
    a: 'Yes. Every person gets a persistent track ID, so dwell time and activity are measured per individual even as people move between zones.',
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
            <span className="text-accent">understand your space.</span>
          </h1>
          <p className="text-lg sm:text-xl text-ink-muted font-medium leading-relaxed max-w-2xl mx-auto text-balance">
            VisionWorks turns passive camera feeds into activity data you can act on — foot traffic, dwell time, posture and occupancy, updated live.
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

        {/* FEATURE 1: SPATIAL ANALYTICS */}
        <section className="flex flex-col lg:flex-row gap-5 items-stretch">

          <div
            className="flex-1 bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '200ms' }}
          >
            <div className="w-12 h-12 bg-accent text-white rounded-2xl flex items-center justify-center mb-6 shadow-lg shadow-red-600/30 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300">
              <Maximize className="w-6 h-6" />
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">Custom zone mapping</h2>
            <p className="text-[15px] text-ink-muted font-medium leading-relaxed mb-6">
              Draw polygonal zones straight onto your video feed. VisionWorks reports activity only within those boundaries, so you can watch checkout lines, machinery areas or individual workstations without noise from the rest of the frame.
            </p>
            <ul className="flex flex-col gap-3">
              {['Unlimited zones per camera', 'Live coordinate mapping', 'Drag-and-drop boundary editing'].map((item, i) => (
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
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 leading-[1.15] group-hover:text-accent transition-colors">Dwell time tracking</h3>
                <p className="opacity-70 font-medium text-[14px] leading-relaxed">See exactly how long people stay in each zone, so you can spot bottlenecks and the spots that actually get used.</p>
              </div>
            </div>

            <div
              className="bg-accent text-white rounded-3xl p-7 sm:p-8 flex flex-col justify-between shadow-lg shadow-red-600/20 animate-fade-in-up hover:-translate-y-1 hover:shadow-xl hover:shadow-red-600/40 transition-all duration-500 group"
              style={{ animationDelay: '400ms' }}
            >
              <Layers className="w-8 h-8 text-white mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 leading-[1.15]">Live heatmaps</h3>
                <p className="text-red-50 font-medium text-[14px] leading-relaxed">Foot-traffic intensity drawn over your floorplan, so layout problems show up at a glance.</p>
              </div>
            </div>
          </div>

        </section>

        {/* FEATURE 2: SAFETY & ERGONOMICS */}
        <section className="flex flex-col lg:flex-row-reverse gap-5 items-stretch">

          <div
            className="flex-1 bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '500ms' }}
          >
            <div className="w-12 h-12 bg-inverse text-inverse rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 group-hover:bg-accent group-hover:text-white transition-all duration-300">
              <Activity className="w-6 h-6" />
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">Ergonomic safety</h2>
            <p className="text-[15px] text-ink-muted font-medium leading-relaxed mb-6">
              Skeletal pose estimation picks up awkward lifting, slumped desk posture and long sedentary stretches — and flags them before they turn into injuries.
            </p>
            <ul className="flex flex-col gap-3">
              {['Real-time skeletal tracking', 'Configurable thresholds', 'Automated anomaly alerts'].map((item, i) => (
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
               <h3 className="text-4xl sm:text-5xl font-black text-ink tracking-tight leading-[1.15] pb-1 mb-2 tabular-nums">99%</h3>
               <p className="font-bold text-ink-faint uppercase tracking-[0.12em] text-[11px] mb-3">Detection accuracy</p>
               <p className="text-ink-muted font-medium text-[14px] max-w-xs mx-auto leading-relaxed">Built on YOLOv8 detection with persistent multi-object tracking, running fast enough to keep up with live video.</p>
             </div>
          </div>

        </section>

        {/* HOW IT WORKS */}
        <HowItWorks
          steps={STEPS}
          heading="From camera to insight in three steps"
          intro="No rewiring and no new hardware. Most teams are looking at live numbers the same afternoon they start."
          delay={650}
        />

        {/* FAQ */}
        <FAQ items={FAQ_ITEMS} delay={700} />

        {/* BOTTOM CTA */}
        <section
          className="bg-surface border border-line rounded-3xl p-10 sm:p-14 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '750ms' }}
        >
          <h2 className="text-2xl sm:text-3xl font-black text-ink tracking-tight mb-3 relative z-10 leading-[1.15] pb-1">Transform your workspace.</h2>
          <p className="text-ink-muted font-medium text-[15px] max-w-md mx-auto mb-7 relative z-10">
            Get occupancy and ergonomics analytics running in minutes.
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
