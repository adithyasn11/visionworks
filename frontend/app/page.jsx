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

  if (!mounted) return <div className="min-h-screen bg-white flex flex-col"></div>;

  return (
    <div className="min-h-screen bg-white text-black font-sans selection:bg-red-600 selection:text-white flex flex-col overflow-x-hidden">
      
      {/* NAVBAR */}
      <LandingNavbar />

      <main className="flex-1 w-full max-w-7xl mx-auto px-8">
        
        {/* HERO SECTION */}
        <section className="pt-16 pb-16 flex flex-col lg:flex-row gap-8 items-stretch min-h-[70vh]">
          
          {/* Left: Text & CTA */}
          <div 
            className="flex-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/50 rounded-[2.5rem] p-12 flex flex-col justify-center relative overflow-hidden animate-fade-in-up group hover:border-gray-200 hover:shadow-2xl transition-all duration-500" 
            style={{ animationDelay: '100ms', opacity: 0 }}
          >
            {/* Background animated graphic */}
            <div className="absolute top-0 right-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] group-hover:rotate-45 group-hover:scale-110 transition-all duration-700">
               <Target className="w-80 h-80 text-black transform rotate-12 translate-x-12 -translate-y-12" />
            </div>
            
            <h1 className="text-5xl lg:text-[4rem] font-black tracking-tighter text-gray-900 mb-6 leading-[1.05] relative z-10 group-hover:translate-x-2 transition-transform duration-500">
              Smart cameras.<br/>
              <span className="text-red-600 relative inline-block">
                Safer spaces.
                <span className="absolute bottom-1 left-0 w-full h-2 bg-red-600/20 -z-10 group-hover:h-full transition-all duration-300"></span>
              </span>
            </h1>
            
            <p className="text-lg text-gray-600 mb-10 max-w-md font-medium leading-relaxed relative z-10">
              Understand how your physical space is being used. Track foot traffic, improve safety, and get instant alerts—all without compromising privacy.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-5 relative z-10 mb-8">
              <Link 
                href="/dashboard"
                className="bg-black text-white px-8 py-4 rounded-2xl font-bold text-base hover:bg-red-600 hover:shadow-xl hover:shadow-red-600/30 hover:-translate-y-1 transition-all duration-300 flex items-center justify-center gap-3 group/btn"
              >
                Start for free <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 transition-transform" />
              </Link>
              <a 
                href="#demo"
                className="bg-white text-black border-2 border-gray-200 px-8 py-4 rounded-2xl font-bold text-base hover:border-black hover:bg-gray-50 hover:-translate-y-1 hover:shadow-lg transition-all duration-300 flex items-center justify-center gap-3 group/btn2"
              >
                <PlayCircle className="w-5 h-5 group-hover/btn2:scale-110 group-hover/btn2:text-red-600 transition-transform" /> Watch demo
              </a>
            </div>

            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-6 relative z-10 text-sm font-bold text-gray-400">
              <div className="flex items-center gap-2 group/check hover:text-green-600 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 text-gray-300 group-hover/check:text-green-500 group-hover/check:scale-110 transition-all" /> No hardware required
              </div>
              <div className="flex items-center gap-2 group/check hover:text-green-600 transition-colors cursor-default">
                <CheckCircle2 className="w-5 h-5 text-gray-300 group-hover/check:text-green-500 group-hover/check:scale-110 transition-all" /> Setup in 5 minutes
              </div>
            </div>
          </div>
          
          {/* Right: Data Bento Box */}
          <div className="flex-1 grid grid-cols-2 grid-rows-2 gap-6 lg:max-w-xl">
            {/* Stat 1 */}
            <div 
              className="bg-black text-white rounded-[2.5rem] p-8 flex flex-col justify-between shadow-xl shadow-black/10 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/20 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '200ms', opacity: 0 }}
            >
              <Cpu className="w-10 h-10 text-red-500 mb-4 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-3xl font-black tracking-tighter mb-2 group-hover:text-red-500 transition-colors">Instant</h3>
                <p className="font-bold text-gray-400 uppercase tracking-widest text-[10px]">Alerts & insights</p>
              </div>
            </div>
            
            {/* Stat 2 */}
            <div 
              className="bg-red-600 text-white rounded-[2.5rem] p-8 flex flex-col justify-between shadow-xl shadow-red-600/30 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-300 cursor-default group"
              style={{ animationDelay: '300ms', opacity: 0 }}
            >
              <Video className="w-10 h-10 text-white/90 mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-3xl font-black tracking-tighter mb-2">Universal</h3>
                <p className="font-bold text-red-200 uppercase tracking-widest text-[10px]">Camera Support</p>
              </div>
            </div>
            
            {/* Stat 3 (Wide) */}
            <div 
              className="col-span-2 bg-white border border-gray-100 rounded-[2.5rem] p-8 flex items-center justify-between shadow-xl shadow-gray-200/50 animate-fade-in-up hover:-translate-y-2 hover:border-gray-300 hover:shadow-2xl transition-all duration-300 cursor-default group"
              style={{ animationDelay: '400ms', opacity: 0 }}
            >
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.6)]"></div>
                  <span className="font-extrabold text-[10px] tracking-widest uppercase text-gray-400">System Setup</span>
                </div>
                <h3 className="text-2xl font-black text-black tracking-tight mb-2 group-hover:text-red-600 transition-colors">Plug and play</h3>
                <p className="text-gray-500 font-medium text-sm">No complicated hardware required.</p>
              </div>
              <Activity className="w-16 h-16 text-gray-100 group-hover:text-red-100 group-hover:scale-110 transition-all duration-500 hidden sm:block" />
            </div>
          </div>
          
        </section>

        {/* BENTO GRID FEATURES */}
        <section id="platform" className="py-12">
          <div className="grid grid-cols-1 md:grid-cols-3 md:grid-rows-2 gap-6">
            
            {/* Feature 1 */}
            <div 
              className="md:col-span-2 md:row-span-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[2.5rem] p-10 flex flex-col justify-between relative overflow-hidden animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
              style={{ animationDelay: '500ms', opacity: 0 }}
            >
              <div className="relative z-10 w-2/3">
                <div className="w-12 h-12 bg-gray-50 text-black border border-gray-200 rounded-xl flex items-center justify-center mb-6 group-hover:bg-black group-hover:text-white transition-colors duration-300">
                  <BarChart className="w-6 h-6 group-hover:scale-110 transition-transform" />
                </div>
                <h3 className="text-3xl font-black text-gray-900 tracking-tight mb-4 group-hover:translate-x-2 transition-transform duration-300">See how your space is used</h3>
                <p className="text-gray-600 font-medium text-base leading-relaxed max-w-md group-hover:text-gray-900 transition-colors">
                  Draw zones on your camera feed to instantly see which areas are busy, how long people stay, and when foot traffic peaks.
                </p>
              </div>
              {/* Graphic element */}
              <div className="absolute right-[-5%] bottom-[-30%] w-[50%] h-[150%] bg-gray-50 transform rotate-12 rounded-[3rem] group-hover:rotate-6 group-hover:bg-red-50 transition-all duration-700"></div>
            </div>
            
            {/* Feature 2 */}
            <div 
              className="md:col-span-1 md:row-span-2 bg-black text-white shadow-xl shadow-black/20 rounded-[2.5rem] p-10 flex flex-col justify-between animate-fade-in-up relative overflow-hidden group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-900/30 transition-all duration-500"
              style={{ animationDelay: '600ms', opacity: 0 }}
            >
              <div className="relative z-10">
                <div className="w-12 h-12 bg-red-600 text-white rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300 shadow-lg shadow-red-600/30">
                  <Shield className="w-6 h-6" />
                </div>
                <h3 className="text-3xl font-black tracking-tight mb-4 text-white group-hover:text-red-500 transition-colors">Privacy first.</h3>
                <p className="text-gray-400 font-medium text-base leading-relaxed mb-8 group-hover:text-gray-200 transition-colors">
                  We process the video to count people and movement, then instantly discard it. No raw footage is ever stored or saved to the cloud.
                </p>
              </div>
              <ul className="space-y-4 relative z-10">
                {['Local processing', 'No footage saved', 'Fully encrypted'].map((item, i) => (
                  <li key={i} className="flex items-center gap-3 font-bold text-sm text-gray-300 group-hover:translate-x-2 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                    <CheckCircle2 className="w-5 h-5 text-red-500" />
                    {item}
                  </li>
                ))}
              </ul>
              {/* Decorative circle */}
              <div className="absolute -bottom-10 -right-10 w-48 h-48 border-[20px] border-gray-900 rounded-full group-hover:scale-150 group-hover:border-red-900/30 transition-all duration-700"></div>
            </div>
            
            {/* Feature 3 */}
            <div 
              className="md:col-span-1 md:row-span-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[2.5rem] p-8 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
              style={{ animationDelay: '700ms', opacity: 0 }}
            >
              <Activity className="w-10 h-10 text-red-600 mb-4 group-hover:scale-125 group-hover:rotate-12 transition-transform duration-300" />
              <h3 className="text-2xl font-black text-gray-900 tracking-tight mb-2 group-hover:text-red-600 transition-colors">Safety alerts</h3>
              <p className="text-gray-500 font-medium text-sm leading-relaxed">Get notified automatically when unsafe postures or movements are detected.</p>
            </div>
            
            {/* Feature 4 */}
            <div 
              className="md:col-span-1 md:row-span-1 bg-red-600 text-white shadow-xl shadow-red-600/30 rounded-[2.5rem] p-8 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-500"
              style={{ animationDelay: '800ms', opacity: 0 }}
            >
              <Clock className="w-10 h-10 text-white mb-4 group-hover:scale-125 group-hover:-rotate-12 transition-transform duration-300" />
              <h3 className="text-2xl font-black tracking-tight mb-2 group-hover:text-black transition-colors">Export data</h3>
              <p className="text-red-100 font-medium text-sm leading-relaxed group-hover:text-white transition-colors">Download your activity logs to share reports with your team.</p>
            </div>

          </div>
        </section>

        {/* BOTTOM CTA */}
        <section 
          className="my-12 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[3rem] p-16 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '900ms', opacity: 0 }}
        >
          {/* subtle background effect */}
          <div className="absolute inset-0 bg-gradient-to-br from-white via-white to-red-50 opacity-0 group-hover:opacity-100 transition-opacity duration-700"></div>
          
          <h2 className="text-5xl font-black text-gray-900 tracking-tighter mb-4 relative z-10 group-hover:scale-105 transition-transform duration-500">Ready to get started?</h2>
          <p className="text-gray-500 font-medium text-lg max-w-xl mx-auto mb-8 relative z-10 group-hover:text-gray-900 transition-colors duration-300">
            Connect your cameras today and see how your space is actually being used.
          </p>
          <Link 
            href="/dashboard"
            className="bg-black text-white px-10 py-5 rounded-2xl font-bold text-base hover:bg-red-600 transition-all duration-300 shadow-xl shadow-black/10 hover:shadow-red-600/30 flex items-center gap-3 relative z-10 group/btn"
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
