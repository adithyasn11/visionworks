'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Shield, Lock, EyeOff, Server, Database, CheckCircle2, Video } from 'lucide-react';
import LandingNavbar from '../components/LandingNavbar';
import LandingFooter from '../components/LandingFooter';

export default function SecurityPage() {
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
          <div className="w-20 h-20 bg-red-600 text-white rounded-3xl flex items-center justify-center mx-auto mb-8 shadow-2xl shadow-red-600/40">
            <Shield className="w-10 h-10" />
          </div>
          <h1 className="text-5xl lg:text-[4rem] font-black tracking-tighter text-gray-900 mb-6 leading-[1.05]">
            Enterprise privacy.<br/>
            <span className="text-red-600">Built in.</span>
          </h1>
          <p className="text-xl text-gray-600 font-medium leading-relaxed max-w-2xl mx-auto mb-10">
            We believe that analyzing workspace activity should never compromise employee privacy. Our edge-computing architecture ensures raw video never leaves your facility.
          </p>
        </section>

        {/* SECURITY GRID 1: EDGE PROCESSING */}
        <section className="pb-16">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            
            {/* Edge Processing Architecture */}
            <div 
              className="bg-black text-white shadow-xl shadow-black/20 rounded-[2.5rem] p-12 flex flex-col justify-between animate-fade-in-up relative overflow-hidden group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-900/30 transition-all duration-500"
              style={{ animationDelay: '200ms', opacity: 0 }}
            >
              <div className="relative z-10">
                <div className="w-14 h-14 bg-red-600 text-white rounded-xl flex items-center justify-center mb-8 shadow-lg shadow-red-600/30 group-hover:scale-110 transition-transform duration-300">
                  <Server className="w-7 h-7" />
                </div>
                <h3 className="text-4xl font-black tracking-tight mb-4 text-white group-hover:text-red-500 transition-colors">Local Edge Processing</h3>
                <p className="text-gray-400 font-medium text-lg leading-relaxed mb-8 group-hover:text-gray-200 transition-colors">
                  Cameras process video frames directly on local hardware. The AI extracts metadata (like bounding boxes and coordinates) and immediately drops the raw image frames from memory.
                </p>
              </div>
              <div className="relative z-10 bg-gray-900 p-6 rounded-2xl border border-gray-800 flex items-center justify-between group-hover:border-red-500/30 transition-colors">
                <div className="flex flex-col items-center">
                  <Video className="w-6 h-6 text-gray-500 mb-2" />
                  <span className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Raw Video</span>
                </div>
                <ArrowRight className="w-5 h-5 text-red-500 line-through" />
                <div className="flex flex-col items-center">
                  <Database className="w-6 h-6 text-gray-500 mb-2" />
                  <span className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Cloud Storage</span>
                </div>
              </div>
            </div>

            {/* Zero Retention */}
            <div 
              className="bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[2.5rem] p-12 flex flex-col justify-between relative overflow-hidden animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
              style={{ animationDelay: '300ms', opacity: 0 }}
            >
              <div className="relative z-10">
                <div className="w-14 h-14 bg-gray-100 text-black rounded-xl flex items-center justify-center mb-8 group-hover:bg-black group-hover:text-white transition-colors duration-300">
                  <EyeOff className="w-7 h-7" />
                </div>
                <h3 className="text-4xl font-black text-gray-900 tracking-tight mb-4 group-hover:translate-x-2 transition-transform duration-300">Zero Video Retention</h3>
                <p className="text-gray-600 font-medium text-lg leading-relaxed mb-8">
                  No video is ever saved to disk. No footage is ever transmitted over the network. We store nothing but encrypted numerical telemetry.
                </p>
              </div>
              <ul className="space-y-4">
                {['No cloud video storage', 'No face recognition', 'No personally identifiable info'].map((item, i) => (
                  <li key={i} className="flex items-center gap-3 font-bold text-sm text-gray-400 group-hover:translate-x-2 transition-transform" style={{ transitionDelay: `${i * 100}ms` }}>
                    <CheckCircle2 className="w-5 h-5 text-red-500" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            
          </div>
        </section>

        {/* SECURITY GRID 2: DB SECURITY */}
        <section className="pb-16 flex flex-col lg:flex-row gap-8 items-stretch">
          
          <div 
            className="flex-1 bg-red-600 text-white shadow-xl shadow-red-600/30 rounded-[2.5rem] p-12 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:shadow-red-600/50 transition-all duration-500 relative overflow-hidden"
            style={{ animationDelay: '400ms', opacity: 0 }}
          >
            <div className="absolute right-[-10%] top-[-20%] opacity-10 group-hover:rotate-45 group-hover:scale-125 transition-all duration-1000">
               <Lock className="w-[30rem] h-[30rem]" />
            </div>
            <div className="relative z-10">
              <h3 className="text-4xl font-black tracking-tight mb-4 group-hover:text-black transition-colors">Bank-Level Encryption</h3>
              <p className="text-red-100 font-medium text-lg leading-relaxed mb-8 group-hover:text-white transition-colors">
                All metadata transmitted from the local inference engine to the cloud dashboard is secured using strict TLS 1.3 encryption.
              </p>
            </div>
          </div>
          
          <div 
            className="flex-1 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[2.5rem] p-12 flex flex-col justify-center animate-fade-in-up group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500"
            style={{ animationDelay: '500ms', opacity: 0 }}
          >
            <Database className="w-12 h-12 text-black mb-6 group-hover:scale-110 group-hover:text-red-600 transition-colors duration-300" />
            <h3 className="text-3xl font-black text-gray-900 tracking-tight mb-4 group-hover:translate-x-2 transition-transform duration-300">Row Level Security</h3>
            <p className="text-gray-600 font-medium text-base leading-relaxed">
              Our Supabase-powered backend ensures complete data isolation. Row Level Security (RLS) policies mathematically guarantee that organizations can only access their own telemetry data, protecting against cross-tenant data leaks.
            </p>
          </div>
          
        </section>

        {/* BOTTOM CTA */}
        <section 
          className="my-12 bg-white border border-gray-100 shadow-xl shadow-gray-200/40 rounded-[3rem] p-16 text-center animate-fade-in-up flex flex-col items-center justify-center group hover:-translate-y-2 hover:shadow-2xl hover:border-gray-200 transition-all duration-500 relative overflow-hidden"
          style={{ animationDelay: '600ms', opacity: 0 }}
        >
          <div className="absolute inset-0 bg-gradient-to-br from-white via-white to-red-50 opacity-0 group-hover:opacity-100 transition-opacity duration-700"></div>
          
          <h2 className="text-5xl font-black text-gray-900 tracking-tighter mb-4 relative z-10 group-hover:scale-105 transition-transform duration-500">Secure your workspace.</h2>
          <p className="text-gray-500 font-medium text-lg max-w-xl mx-auto mb-8 relative z-10 group-hover:text-gray-900 transition-colors duration-300">
            Deploy advanced activity analytics without compromising privacy or compliance.
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
