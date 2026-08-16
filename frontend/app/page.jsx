'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Activity, Shield, Clock, Target, PlayCircle, Cpu, Video, CheckCircle2, BarChart } from 'lucide-react';
import LandingNavbar from './components/LandingNavbar';
import LandingFooter from './components/LandingFooter';
export default function LandingPage() {
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return <div className="min-h-screen bg-ground flex flex-col"></div>;

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-accent selection:text-white flex flex-col overflow-x-hidden">
      
      {/* NAVBAR */}
      <LandingNavbar />

      <main className="flex-1 w-full max-w-6xl mx-auto px-6 sm:px-8">
        
        {/* HERO SECTION — owns the full first screen so the next section can't peek above the fold. */}
        <section className="hero-screen hero-cards flex flex-col lg:flex-row gap-5 items-stretch">

          {/* Left: Text & CTA */}
          <div
            className="flex-1 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-8 sm:p-10 flex flex-col justify-center relative overflow-hidden animate-fade-in-up group hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-500"
            style={{ animationDelay: '100ms' }}
          >
            {/* Background animated graphic */}
            <div className="absolute top-0 right-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] group-hover:rotate-45 group-hover:scale-110 transition-all duration-700">
               <Target className="w-80 h-80 text-ink transform rotate-12 translate-x-12 -translate-y-12" />
            </div>
            
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black tracking-tight text-ink mb-6 leading-[1.15] pb-1 relative z-10 group-hover:translate-x-2 transition-transform duration-500">
              Smart cameras.<br/>
              <span className="text-accent relative inline-block">
                Safer spaces.
                <span className="absolute bottom-1 left-0 w-full h-2 bg-accent/20 -z-10 group-hover:h-full transition-all duration-300"></span>
              </span>
            </h1>
            
            <p className="text-lg text-ink-muted mb-8 max-w-md font-medium leading-relaxed relative z-10">
              Understand how your physical space is being used. Track foot traffic, improve safety, and get instant alerts—all without compromising privacy.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-5 relative z-10 mb-8">
              <Link 
                href="/signup"
                className="bg-inverse text-inverse px-8 py-4 rounded-2xl font-bold text-base hover:bg-accent hover:shadow-xl hover:shadow-red-600/30 hover:-translate-y-1 transition-all duration-300 flex items-center justify-center gap-3 group/btn"
              >
                Start for free <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 transition-transform" />
              </Link>
              <a 
                href="#demo"
                className="bg-surface text-ink border-2 border-line px-8 py-4 rounded-2xl font-bold text-base hover:border-[color:var(--accent)] hover:bg-surface-alt hover:-translate-y-1 hover:shadow-lg transition-all duration-300 flex items-center justify-center gap-3 group/btn2"
              >
                <PlayCircle className="w-5 h-5 group-hover/btn2:scale-110 group-hover/btn2:text-accent transition-transform" /> Watch demo
              </a>
            </div>

            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-5 relative z-10 text-sm font-bold text-ink-muted">
              <div className="flex items-center gap-2 group/check hover:text-emerald-500 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 opacity-70 group-hover/check:text-emerald-500 group-hover/check:scale-110 transition-all" /> No hardware required
              </div>
              <div className="flex items-center gap-2 group/check hover:text-emerald-500 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 opacity-70 group-hover/check:text-emerald-500 group-hover/check:scale-110 transition-all" /> Setup in 5 minutes
              </div>
            </div>
          </div>
          
          {/* Right: Data Bento Box */}
          <div className="flex-1 grid grid-cols-2 grid-rows-[1.15fr_1fr] gap-5 lg:max-w-xl">
            {/* Stat 1 */}
            <div 
              className="card-dark rounded-3xl p-7 flex flex-col justify-end gap-4 shadow-xl shadow-black/10 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/20 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '200ms' }}
            >
              <Cpu className="w-10 h-10 text-accent mb-4 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2 group-hover:text-accent transition-colors">Instant</h3>
                <p className="font-bold text-ink-muted uppercase tracking-widest text-[10px]">Alerts & insights</p>
              </div>
            </div>
            
            {/* Stat 2 */}
            <div 
              className="bg-accent text-white rounded-3xl p-7 flex flex-col justify-end gap-4 shadow-xl shadow-red-600/30 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '300ms' }}
            >
              <Video className="w-10 h-10 text-white mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-lg sm:text-xl font-black tracking-tight mb-2">Universal</h3>
                <p className="font-bold text-red-50 uppercase tracking-widest text-[10px]">Camera Support</p>
              </div>
            </div>
            
            {/* Stat 3 (Wide) */}
            <div 
              className="col-span-2 bg-surface border border-line rounded-3xl p-8 flex items-center justify-between shadow-xl shadow-black/5 animate-fade-in-up hover:-translate-y-2 hover:border-[color:var(--accent)] hover:shadow-2xl transition-all duration-300 cursor-default group"
              style={{ animationDelay: '400ms' }}
            >
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.6)]"></div>
                  <span className="font-extrabold text-[10px] tracking-widest uppercase text-ink-muted">System Setup</span>
                </div>
                <h3 className="text-lg font-black text-ink tracking-tight mb-2 group-hover:text-accent transition-colors">Plug and play</h3>
                <p className="text-ink-muted font-medium text-[14px]">No complicated hardware required.</p>
              </div>
              <Activity className="w-16 h-16 text-ink-muted/30 group-hover:text-accent group-hover:scale-110 transition-all duration-500 hidden sm:block" />
            </div>
          </div>

        </section>

        {/* Trust strip — fills the space below the hero cards with something
            useful instead of empty ground, and hands the eye off to the
            sections beneath. */}
        <div className="mt-2 mb-4 flex flex-wrap items-center justify-center gap-x-8 gap-y-3 text-ink-muted animate-fade-in-up" style={{ animationDelay: '500ms' }}>
          <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-faint">
            Built for the workplace
          </span>
          <span className="hidden sm:block w-px h-4 bg-[color:var(--line)]" aria-hidden="true"></span>
          {['Runs on your hardware', 'No footage retained', 'Live in minutes'].map((t) => (
            <span key={t} className="flex items-center gap-2 text-[13px] font-bold">
              <CheckCircle2 className="w-4 h-4 text-accent shrink-0" />
              {t}
            </span>
          ))}
        </div>

        {/* BENTO GRID FEATURES */}
        <section id="platform" className="py-12">
          <div className="grid grid-cols-1 md:grid-cols-3 md:grid-rows-2 gap-5">
            
            {/* Feature 1 */}
            <div 
              className="md:col-span-2 md:row-span-1 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-7 sm:p-7 flex flex-col justify-between relative overflow-hidden animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-[color:var(--accent)] transition-all duration-500"
              style={{ animationDelay: '500ms' }}
            >
              <div className="relative z-10 w-2/3">
                <div className="w-12 h-12 bg-surface-alt text-ink border border-line rounded-xl flex items-center justify-center mb-6 group-hover:bg-accent group-hover:text-white transition-colors duration-300">
                  <BarChart className="w-6 h-6 group-hover:scale-110 transition-transform" />
                </div>
                <h3 className="text-xl sm:text-2xl font-black text-ink tracking-tight mb-4 group-hover:translate-x-2 transition-transform duration-300">See how your space is used</h3>
                <p className="text-ink-muted font-medium text-[15px] leading-relaxed max-w-md group-hover:text-ink transition-colors">
                  Draw zones on your camera feed to instantly see which areas are busy, how long people stay, and when foot traffic peaks.
                </p>
              </div>
              {/* Graphic element */}
              <div className="absolute right-[-5%] bottom-[-30%] w-[50%] h-[150%] bg-surface-alt transform rotate-12 rounded-3xl group-hover:rotate-6 group-hover:bg-red-50 transition-all duration-700"></div>
            </div>
            
            {/* Feature 2 */}
            <div 
              className="md:col-span-1 md:row-span-2 card-dark shadow-xl shadow-black/20 rounded-3xl p-7 sm:p-7 flex flex-col justify-between animate-fade-in-up relative overflow-hidden group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-900/30 transition-all duration-500"
              style={{ animationDelay: '600ms' }}
            >
              <div className="relative z-10">
                <div className="w-12 h-12 bg-accent text-white rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300 shadow-lg shadow-red-600/30">
                  <Shield className="w-6 h-6" />
                </div>
                <h3 className="text-3xl font-black tracking-tight mb-4 text-white group-hover:text-accent transition-colors">Privacy first.</h3>
                <p className="opacity-70 font-medium text-[15px] leading-relaxed mb-8 group-hover:opacity-90 transition-colors">
                  We process the video to count people and movement, then instantly discard it. No raw footage is ever stored or saved to the cloud.
                </p>
              </div>
              <ul className="space-y-4 relative z-10">
                {['Local processing', 'No footage saved', 'Fully encrypted'].map((item, i) => (
                  <li key={i} className="flex items-center gap-3 font-bold text-sm opacity-70 group-hover:translate-x-2 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                    <CheckCircle2 className="w-5 h-5 text-red-500" />
                    {item}
                  </li>
                ))}
              </ul>
              {/* Decorative circle */}
              <div className="absolute -bottom-10 -right-10 w-48 h-48 border-[20px] border-white/10 rounded-full group-hover:scale-150 group-hover:border-red-900/30 transition-all duration-700"></div>
            </div>
            
            {/* Feature 3 */}
            <div 
              className="md:col-span-1 md:row-span-1 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-7 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-[color:var(--accent)] transition-all duration-500"
              style={{ animationDelay: '700ms' }}
            >
              <Activity className="w-10 h-10 text-accent mb-4 group-hover:scale-125 group-hover:rotate-12 transition-transform duration-300" />
              <h3 className="text-2xl font-black text-ink tracking-tight mb-2 group-hover:text-accent transition-colors">Safety alerts</h3>
              <p className="text-ink-muted font-medium text-[14px] leading-relaxed">Get notified automatically when unsafe postures or movements are detected.</p>
            </div>
            
            {/* Feature 4 */}
            <div 
              className="md:col-span-1 md:row-span-1 bg-accent text-white shadow-xl shadow-red-600/30 rounded-3xl p-7 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-500"
              style={{ animationDelay: '800ms' }}
            >
              <Clock className="w-10 h-10 text-white mb-4 group-hover:scale-125 group-hover:-rotate-12 transition-transform duration-300" />
              <h3 className="text-2xl font-black tracking-tight mb-2 group-hover:text-black transition-colors">Export data</h3>
              <p className="text-red-50 font-medium text-sm leading-relaxed group-hover:text-white transition-colors">Download your activity logs to share reports with your team.</p>
            </div>

          </div>
        </section>

        {/* BOTTOM CTA */}
        <section 
          className="my-12 bg-surface border border-line shadow-xl shadow-black/5 rounded-3xl p-10 sm:p-14 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-2 hover:shadow-2xl hover:border-[color:var(--accent)] transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '900ms' }}
        >
          {/* subtle background effect */}
          <div className="absolute inset-0 bg-gradient-to-br from-transparent via-transparent to-red-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-700"></div>
          
          <h2 className="text-2xl sm:text-3xl font-black text-ink tracking-tight mb-4 relative z-10 group-hover:scale-105 transition-transform duration-500">Ready to get started?</h2>
          <p className="text-ink-muted font-medium text-[15px] max-w-xl mx-auto mb-8 relative z-10 group-hover:text-ink transition-colors duration-300">
            Connect your cameras today and see how your space is actually being used.
          </p>
          <Link 
            href="/signup"
            className="bg-inverse text-inverse px-10 py-5 rounded-2xl font-bold text-base hover:bg-accent transition-all duration-300 shadow-xl shadow-black/10 hover:shadow-red-600/30 flex items-center gap-3 relative z-10 group/btn"
          >
            Create an account <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 group-hover/btn:scale-110 transition-transform" />
          </Link>
        </section>

      </main>

      {/* HIGH-END FOOTER */}
      <LandingFooter />

    </div>
  );
}
