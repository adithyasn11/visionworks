'use client';

// frontend/app/components/Header.jsx
import React, { useState, useEffect } from 'react';
import { Activity, ShieldCheck, Clock, Database, LogOut, Loader2 } from 'lucide-react';
import { supabase } from '../lib/supabase/browser';

export const Header = ({ isConnected, onOpenSupabaseModal }) => {
  const [time, setTime] = useState('');
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      // Clear the client's in-memory session first so nothing tries to refresh
      // the token mid-teardown, then hand off to the server route — only it can
      // delete the httpOnly cookies middleware actually reads.
      if (supabase) await supabase.auth.signOut({ scope: 'global' }).catch(() => {});
    } finally {
      // A full navigation, not router.replace(): this must reach the server so
      // the Set-Cookie deletions are applied and no cached RSC payload for a
      // signed-in view survives.
      window.location.href = '/auth/signout';
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
        <div className="p-2.5 rounded-xl bg-accent-soft border border-line text-accent">
          <Activity className="w-6 h-6 animate-pulse" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-ink tracking-tight">
            Workplace Activity Analytics
          </h1>
          <p className="text-xs text-ink-muted">
            Next.js App Router • Vision CCTV AI & Supabase Telemetry
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4 text-sm">
        {/* Supabase Connect Button */}
        <button
          onClick={onOpenSupabaseModal}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent-soft hover:bg-accent-soft border border-line text-accent text-xs font-semibold transition-all"
        >
          <Database className="w-3.5 h-3.5" />
          <span>Connect Supabase</span>
        </button>

        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-alt border border-line">
          <ShieldCheck className="w-3.5 h-3.5 text-accent" />
          <span className="text-xs font-medium text-ink-muted">Privacy Blur</span>
        </div>

        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? 'bg-accent shadow-lg shadow-red-600/40' : 'bg-[color:var(--ink-faint)] animate-pulse'}`} />
          <span className="text-xs text-ink-muted font-mono">
            {isConnected ? 'LIVE WEBSOCKET' : 'AI ENGINE ACTIVE'}
          </span>
        </div>

        <div className="flex items-center gap-2 text-ink-muted text-xs font-mono">
          <Clock className="w-3.5 h-3.5" />
          <span>{time || '--:--:--'}</span>
        </div>

        <button
          onClick={signOut}
          disabled={signingOut}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink-muted text-xs font-semibold transition-all disabled:opacity-50"
        >
          {signingOut ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );
};
