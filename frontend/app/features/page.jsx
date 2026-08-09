'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, BarChart, Maximize, Activity, Zap, Layers } from 'lucide-react';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';

export default function FeaturesPage() {
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return <div className="min-h-screen bg-white flex flex-col"></div>;

  return (
    <div className="min-h-screen bg-white text-black font-sans selection:bg-red-600 selection:text-white flex flex-col overflow-x-hidden">
      <LandingNavbar />

      <main className="flex-1 w-full max-w-7xl mx-auto px-8">
        
        {/* HERO SECTION */}
        <section className="pt-20 pb-16 text-center max-w-4xl mx-auto animate-fade-in-up" style={{ animationDelay: '100ms', opacity: 0 }}>
          <h1 className="text-5xl lg:text-[4rem] font-black tracking-tighter text-gray-900 mb-6 leading-[1.05]">
            Everything you need to <br/>
            <span className="text-red-600">understand your space.</span>
          </h1>
          <p className="text-xl text-gray-600 font-medium leading-relaxed max-w-2xl mx-auto mb-10">
            VisionWorks transforms passive camera feeds into active business intelligence. Track foot traffic, optimize layouts, and monitor safety in real-time.
          </p>
        </section>

        {/* FEATURE 1: SPATIAL ANALYTICS */}
        <section className="pb-16 flex flex-col lg:flex-row gap-8 items-stretch">
          
          <div 
            className="flex-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/50 rounded-[2.5rem] p-12 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
            style={{ animationDelay: '200ms', opacity: 0 }}
          >
            <div className="w-16 h-16 bg-red-600 text-white rounded-2xl flex items-center justify-center mb-8 shadow-lg shadow-red-600/30 group-hover:scale-110 group-hover:rotate-12 transition-transform duration-300">
              <Maximize className="w-8 h-8" />
            </div>
            <h2 className="text-4xl font-black text-gray-900 tracking-tight mb-4 group-hover:text-red-600 transition-colors duration-300">Custom Zone Mapping</h2>
            <p className="text-lg text-gray-600 font-medium leading-relaxed mb-8">
              Draw polygonal zones directly over your video feed using our interactive dashboard. VisionWorks tracks activity specifically within these boundaries, allowing you to monitor high-priority areas like checkout lines, hazardous machinery zones, or specific workstations.
            </p>
            <ul className="space-y-4">
              {['Unlimited custom zones per camera', 'Live coordinate mapping', 'Drag-and-drop boundary editing'].map((item, i) => (
                <li key={i} className="flex items-center gap-3 font-bold text-sm text-gray-400 group-hover:translate-x-2 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                  <div className="w-2 h-2 rounded-full bg-red-500"></div>
                  {item}
                </li>
              ))}
            </ul>
          </div>
          
          <div className="flex-1 grid grid-cols-1 grid-rows-2 gap-8">
            <div 
              className="bg-black text-white rounded-[2.5rem] p-10 flex flex-col justify-between shadow-xl shadow-black/10 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl transition-all duration-500 group"
              style={{ animationDelay: '300ms', opacity: 0 }}
            >
              <BarChart className="w-10 h-10 text-red-500 mb-4 group-hover:scale-110 group-hover:text-white transition-all duration-300" />
              <div>
                <h3 className="text-3xl font-black tracking-tighter mb-2 group-hover:text-red-500 transition-colors">Dwell Time Tracking</h3>
                <p className="text-gray-400 font-medium text-sm leading-relaxed">Understand exactly how long people stay in specific zones to identify bottlenecks or high-engagement areas.</p>
              </div>
            </div>
            
            <div 
              className="bg-red-600 text-white rounded-[2.5rem] p-10 flex flex-col justify-between shadow-xl shadow-red-600/30 animate-fade-in-up hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-500 group"
              style={{ animationDelay: '400ms', opacity: 0 }}
            >
              <Layers className="w-10 h-10 text-white/90 mb-4 group-hover:scale-110 group-hover:-rotate-12 transition-transform duration-300" />
              <div>
                <h3 className="text-3xl font-black tracking-tighter mb-2">Live Heatmaps</h3>
                <p className="text-red-100 font-medium text-sm leading-relaxed">Visualize foot traffic intensity overlaid directly on your floorplan to optimize layouts instantly.</p>
              </div>
            </div>
          </div>
          
        </section>

        {/* FEATURE 2: SAFETY & ERGONOMICS */}
        <section className="pb-16 flex flex-col lg:flex-row-reverse gap-8 items-stretch">
          
          <div 
            className="flex-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/50 rounded-[2.5rem] p-12 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
            style={{ animationDelay: '500ms', opacity: 0 }}
          >
            <div className="w-16 h-16 bg-black text-white rounded-2xl flex items-center justify-center mb-8 shadow-lg shadow-black/20 group-hover:scale-110 group-hover:bg-red-600 transition-all duration-300">
              <Activity className="w-8 h-8" />
            </div>
            <h2 className="text-4xl font-black text-gray-900 tracking-tight mb-4 group-hover:translate-x-2 transition-transform duration-300">Ergonomic Safety</h2>
            <p className="text-lg text-gray-600 font-medium leading-relaxed mb-8">
              Protect your workforce. VisionWorks leverages advanced skeletal pose estimation to identify improper lifting techniques, poor desk posture, and prolonged sedentary behavior, sending automated alerts before injuries occur.
            </p>
            <ul className="space-y-4">
              {['Real-time skeletal tracking', 'Configurable safety thresholds', 'Automated anomaly alerts'].map((item, i) => (
                <li key={i} className="flex items-center gap-3 font-bold text-sm text-gray-400 group-hover:translate-x-2 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                  <div className="w-2 h-2 rounded-full bg-black"></div>
                  {item}
                </li>
              ))}
            </ul>
          </div>
          
          <div className="flex-1 bg-gray-50 border border-gray-200 rounded-[2.5rem] p-10 flex flex-col justify-center items-center text-center shadow-inner animate-fade-in-up group overflow-hidden relative" style={{ animationDelay: '600ms', opacity: 0 }}>
             <Zap className="w-24 h-24 text-red-600/20 mb-6 group-hover:scale-150 group-hover:text-red-600/10 transition-all duration-700 absolute" />
             <div className="relative z-10">
               <h3 className="text-6xl font-black text-black tracking-tighter mb-2">99%</h3>
               <p className="font-bold text-gray-500 uppercase tracking-widest text-sm mb-4">Detection Accuracy</p>
               <p className="text-gray-600 font-medium text-sm max-w-xs mx-auto">Powered by state-of-the-art YOLOv8 object detection and SORT tracking algorithms, running blazingly fast at the edge.</p>
             </div>
          </div>
          
        </section>

        {/* BOTTOM CTA */}
        <section 
          className="my-12 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[3rem] p-16 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '700ms', opacity: 0 }}
        >
          <div className="absolute inset-0 bg-gradient-to-br from-white via-white to-red-50 opacity-0 group-hover:opacity-100 transition-opacity duration-700"></div>
          
          <h2 className="text-5xl font-black text-gray-900 tracking-tighter mb-4 relative z-10 group-hover:scale-105 transition-transform duration-500">Transform your workspace.</h2>
          <p className="text-gray-500 font-medium text-lg max-w-xl mx-auto mb-8 relative z-10 group-hover:text-gray-900 transition-colors duration-300">
            Get comprehensive analytics on occupancy and ergonomics in minutes.
          </p>
          <Link 
            href="/dashboard"
            className="bg-black text-white px-10 py-5 rounded-2xl font-bold text-base hover:bg-red-600 transition-all duration-300 shadow-xl shadow-black/10 hover:shadow-red-600/30 flex items-center gap-3 relative z-10 group/btn"
          >
            Create an account <ArrowRight className="w-5 h-5 group-hover/btn:translate-x-1 group-hover/btn:scale-110 transition-transform" />
          </Link>
        </section>

      </main>

      <LandingFooter />

    </div>
  );
}
