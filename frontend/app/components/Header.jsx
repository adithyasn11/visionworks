'use client';

// frontend/app/components/Header.jsx
import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Activity, ShieldCheck, Clock, Database, LogOut, Loader2 } from 'lucide-react';
import { supabase } from '../lib/supabase/browser';

export const Header = ({ isConnected, onOpenSupabaseModal }) => {
  const router = useRouter();
  const [time, setTime] = useState('');
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      if (supabase) await supabase.auth.signOut();
      // replace(), not push(): signed-out users shouldn't reach this page with
      // Back. refresh() clears cached Server Component payloads for /login.
      router.replace('/login');
      router.refresh();
    } catch {
      setSigningOut(false);
    }
  };

  useEffect(() => {
    setTime(new Date().toLocaleTimeString());
    const timer = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header className="glass-panel sticky top-0 z-50 px-6 py-4 mb-6 flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <div className="p-2.5 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sky-400">
          <Activity className="w-6 h-6 animate-pulse" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-slate-100 tracking-tight">
            Workplace Activity Analytics
          </h1>
          <p className="text-xs text-slate-400">
            Next.js App Router • Vision CCTV AI & Supabase Telemetry
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4 text-sm">
        {/* Supabase Connect Button */}
        <button
          onClick={onOpenSupabaseModal}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 text-xs font-semibold transition-all"
        >
          <Database className="w-3.5 h-3.5" />
          <span>Connect Supabase</span>
        </button>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/50">
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
          <span className="text-xs font-medium text-slate-300">Privacy Blur</span>
        </div>

        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? 'bg-emerald-400 shadow-emerald-500/50 shadow-lg' : 'bg-amber-400 animate-ping'}`} />
          <span className="text-xs text-slate-400 font-mono">
            {isConnected ? 'LIVE WEBSOCKET' : 'AI ENGINE ACTIVE'}
          </span>
        </div>

        <div className="flex items-center gap-2 text-slate-400 text-xs font-mono">
          <Clock className="w-3.5 h-3.5" />
          <span>{time || '--:--:--'}</span>
        </div>

        <button
          onClick={signOut}
          disabled={signingOut}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/60 hover:bg-slate-800 border border-slate-700/50 text-slate-300 text-xs font-semibold transition-all disabled:opacity-50"
        >
          {signingOut ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );
};
