'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Shield, Lock, EyeOff, Server, Database, CheckCircle2, Video } from 'lucide-react';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';
import { StatsBand, HowItWorks, FAQ } from '../components/LandingSections';

const STATS = [
  { value: '0',        label: 'Video stored',  note: 'Only blurred snapshots of flagged events are kept' },
  { value: 'On-site',  label: 'Processing',    note: 'Analysis runs on your hardware' },
  { value: 'TLS 1.3',  label: 'In transit',    note: 'Encrypted results, never video' },
  { value: 'Row-level',label: 'Isolation',     note: 'Tenants can only read their own data' },
];

const STEPS = [
  {
    title: 'Frame enters memory',
    body: 'A frame is pulled from the camera and handed straight to the local model. It is never written to disk.',
  },
  {
    title: 'Only results come out',
    body: 'The models return positions, a track ID and whether a helmet and vest were seen. The image itself is dropped immediately, apart from a blurred snapshot when something is flagged.',
  },
  {
    title: 'Results sync',
    body: 'Verdicts travel to your dashboard over TLS 1.3 and land in a database partitioned per organisation.',
  },
];

const FAQ_ITEMS = [
  {
    q: 'Is any video saved or uploaded?',
    a: 'No continuous video. Frames are analysed in memory and released as soon as the models have read them. What leaves the machine is data: positions, track IDs, verdicts and times. The one exception is a snapshot of a flagged event, with faces blurred, kept for a limited time and visible only to authorised reviewers.',
  },
  {
    q: 'Can VisionWorks identify specific people?',
    a: 'No. Workers are followed as anonymous track IDs within a single camera view, so a verdict is about “a worker”, never a named person. The ID lets the system follow someone across frames; it carries no identity and is not reused later.',
  },
  {
    q: 'Who can see our data?',
    a: 'Only your organisation. Row Level Security policies are enforced in the database itself, so a query from one tenant cannot return another tenant’s rows even if the application layer were compromised.',
  },
  {
    q: 'Do we need to tell workers they are being monitored?',
    a: 'Yes — treat notice as a requirement, not an option. Rules vary by jurisdiction (in India, the Digital Personal Data Protection Act, 2023 applies), so check with your own counsel rather than relying on this page. Tell workers what is checked and why, and keep safety data for safety.',
  },
];

export default function SecurityPage() {
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
          <div className="w-16 h-16 bg-accent text-white rounded-2xl flex items-center justify-center mx-auto mb-7 shadow-lg shadow-red-600/30 shrink-0">
            <Shield className="w-8 h-8" />
          </div>
          {/* leading-[1.15] + pb-1 keeps descenders from clipping. */}
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black tracking-tight text-ink mb-6 leading-[1.15] pb-1 text-balance">
            Safety monitoring.{' '}
            <span className="text-accent">Privacy built in.</span>
          </h1>
          <p className="text-lg sm:text-xl text-ink-muted font-medium leading-relaxed max-w-2xl mx-auto text-balance">
            Checking that people follow safety rules shouldn&apos;t cost them their privacy. Everything is processed on site, and no continuous video ever leaves the building.
          </p>

          <div className="mt-9 flex flex-col sm:flex-row gap-3.5 justify-center">
            <Link
              href="/dashboard"
              className="bg-inverse text-inverse px-7 py-3.5 rounded-2xl font-bold text-[15px] hover:bg-accent hover:text-white transition-all duration-300 shadow-lg flex items-center justify-center gap-2.5 group/btn"
            >
              Open the dashboard <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" />
            </Link>
            <Link
              href="/features"
              className="bg-surface text-ink border border-line px-7 py-3.5 rounded-2xl font-bold text-[15px] hover:border-[color:var(--accent)] hover:text-accent transition-all duration-300 flex items-center justify-center gap-2.5"
            >
              Explore the platform
            </Link>
          </div>
        </section>

        {/* STATS */}
        <StatsBand stats={STATS} delay={150} />

        {/* SECURITY GRID 1: EDGE PROCESSING */}
        <section className="grid grid-cols-1 md:grid-cols-2 gap-5">

          {/* Edge Processing Architecture */}
          <div
            className="card-dark rounded-3xl p-8 sm:p-10 flex flex-col justify-between animate-fade-in-up relative overflow-hidden group hover:-translate-y-1 transition-all duration-500"
            style={{ animationDelay: '200ms' }}
          >
            <div className="relative z-10">
              <div className="w-11 h-11 bg-accent text-white rounded-xl flex items-center justify-center mb-6 shadow-lg shadow-red-600/30 group-hover:scale-110 transition-transform duration-300">
                <Server className="w-5 h-5" />
              </div>
              <h3 className="text-xl sm:text-2xl font-black tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors">Local edge processing</h3>
              <p className="opacity-70 font-medium text-[15px] leading-relaxed mb-6">
                Frames are analysed on your own hardware. The models extract positions, track IDs and PPE detections, then drop the image from memory before the next frame arrives.
              </p>
            </div>
            <div className="relative z-10 bg-white/5 p-5 rounded-2xl border border-white/10 flex items-center justify-between gap-4 group-hover:border-[color:var(--accent)] transition-colors">
              <div className="flex flex-col items-center gap-1.5">
                <Video className="w-5 h-5 opacity-70" />
                <span className="text-[10px] font-bold opacity-80 uppercase tracking-[0.12em]">Raw video</span>
              </div>
              {/* The slash is the point: this path is deliberately severed. */}
              <div className="flex-1 flex items-center justify-center relative" aria-hidden="true">
                <div className="h-px w-full bg-white/20"></div>
                <span className="absolute w-6 h-0.5 bg-accent rotate-45 rounded-full"></span>
              </div>
              <div className="flex flex-col items-center gap-1.5">
                <Database className="w-5 h-5 opacity-70" />
                <span className="text-[10px] font-bold opacity-80 uppercase tracking-[0.12em]">Cloud storage</span>
              </div>
            </div>
            <p className="sr-only">Raw video is never sent to cloud storage.</p>
          </div>

          {/* Zero Retention */}
          <div
            className="bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-between relative overflow-hidden animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '300ms' }}
          >
            <div className="relative z-10">
              <div className="w-11 h-11 bg-surface-alt text-ink rounded-xl flex items-center justify-center mb-6 group-hover:bg-accent group-hover:text-white transition-colors duration-300">
                <EyeOff className="w-5 h-5" />
              </div>
              <h3 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">No continuous video</h3>
              <p className="text-ink-muted font-medium text-[15px] leading-relaxed mb-6">
                Nothing continuous is written to disk and nothing is streamed off-site. What persists is encrypted data about what was detected, plus short-lived blurred snapshots of flagged events.
              </p>
            </div>
            <ul className="flex flex-col gap-3">
              {['No cloud video storage', 'Workers known by anonymous ID only', 'Faces blurred in snapshots'].map((item, i) => (
                <li key={i} className="flex items-center gap-3 font-bold text-[13px] text-ink-faint group-hover:translate-x-1 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                  <CheckCircle2 className="w-4 h-4 text-accent shrink-0" />
                  {item}
                </li>
              ))}
            </ul>
          </div>

        </section>

        {/* SECURITY GRID 2: DB SECURITY */}
        <section className="flex flex-col lg:flex-row gap-5 items-stretch">

          <div
            className="flex-1 bg-accent text-white rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:shadow-xl hover:shadow-red-600/40 transition-all duration-500 relative overflow-hidden shadow-lg shadow-red-600/20"
            style={{ animationDelay: '400ms' }}
          >
            <div className="absolute right-[-8%] top-[-15%] opacity-10 group-hover:rotate-45 group-hover:scale-125 transition-all duration-1000 pointer-events-none" aria-hidden="true">
               <Lock className="w-72 h-72" />
            </div>
            <div className="relative z-10">
              <h3 className="text-xl sm:text-2xl font-black tracking-tight mb-3 leading-[1.15] pb-1">Encrypted in transit</h3>
              <p className="text-red-50 font-medium text-[15px] leading-relaxed">
                Every result sent from the local analysis engine to your dashboard travels over TLS 1.3. Nothing moves in the clear.
              </p>
            </div>
          </div>

          <div
            className="flex-1 bg-surface border border-line rounded-3xl p-8 sm:p-10 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500"
            style={{ animationDelay: '500ms' }}
          >
            <Database className="w-10 h-10 text-ink mb-5 group-hover:scale-110 group-hover:text-accent transition-all duration-300" />
            <h3 className="text-lg sm:text-xl font-black text-ink tracking-tight mb-3 leading-[1.15] pb-1 group-hover:text-accent transition-colors duration-300">Row level security</h3>
            <p className="text-ink-muted font-medium text-[15px] leading-relaxed">
              Isolation is enforced by the database, not just the app. Row Level Security policies mean one organisation&apos;s query can never return another&apos;s rows — even if the application layer were compromised.
            </p>
          </div>

        </section>

        {/* HOW IT WORKS */}
        <HowItWorks
          steps={STEPS}
          heading="What happens to a single frame"
          intro="The privacy guarantee isn't a policy sitting on top of the system — it's how the pipeline is built."
          delay={550}
        />

        {/* FAQ */}
        <FAQ items={FAQ_ITEMS} heading="Privacy questions, answered" delay={600} />

        {/* BOTTOM CTA */}
        <section
          className="bg-surface border border-line rounded-3xl p-10 sm:p-14 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-1 hover:border-[color:var(--accent)] transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '650ms' }}
        >
          <h2 className="text-2xl sm:text-3xl font-black text-ink tracking-tight mb-3 relative z-10 leading-[1.15] pb-1">Monitor safety responsibly.</h2>
          <p className="text-ink-muted font-medium text-[15px] max-w-md mx-auto mb-7 relative z-10">
            Run safety checks without giving up privacy or compliance.
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
