'use client';

// frontend/app/components/SupabaseModal.jsx
import React, { useState } from 'react';
import { Database, Key, Check, ExternalLink, ShieldCheck } from 'lucide-react';

export const SupabaseModal = ({ isOpen, onClose, onSave }) => {
  const [url, setUrl] = useState('');
  const [key, setKey] = useState('');
  const [isSaved, setIsSaved] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (onSave) onSave({ url, key });
    setIsSaved(true);
    setTimeout(() => {
      setIsSaved(false);
      onClose();
    }, 1200);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 backdrop-blur-md p-4">
      <div className="glass-panel w-full max-w-lg p-6 flex flex-col gap-5 border-line/80 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-accent/10 border border-line text-accent">
              <Database className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-ink">Connect Your Supabase Project</h2>
              <p className="text-xs text-ink-muted">Enter your project credentials to store live activity logs</p>
            </div>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink text-sm">✕</button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="block text-xs font-semibold text-ink-muted mb-1">Supabase Project URL</label>
            <div className="relative">
              <input
                type="url"
                required
                placeholder="https://xyzproject.supabase.co"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="w-full bg-ground border border-line rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)]"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-ink-muted mb-1">Supabase Anon / API Key</label>
            <div className="relative">
              <input
                type="password"
                required
                placeholder="eyJhbGciOi..."
                value={key}
                onChange={(e) => setKey(e.target.value)}
                className="w-full bg-ground border border-line rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-[color:var(--accent)]"
              />
            </div>
          </div>

          <div className="p-3 rounded-lg bg-ground/60 border border-line text-[11px] text-ink-muted flex items-start gap-2">
            <ShieldCheck className="w-4 h-4 text-accent shrink-0 mt-0.5" />
            <span>
              Your credentials are stored securely in local browser environment state and used to publish time-series CCTV metrics directly to your Supabase <b>activity_logs</b> table.
            </span>
          </div>

          <div className="flex items-center justify-between pt-2">
            <a
              href="https://supabase.com/dashboard"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-accent hover:underline flex items-center gap-1"
            >
              <span>Get API Keys from Supabase</span>
              <ExternalLink className="w-3 h-3" />
            </a>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 rounded-lg bg-surface-alt hover:bg-surface-alt text-xs text-ink-muted transition-all"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 rounded-lg bg-accent hover:brightness-110 text-white font-bold text-xs flex items-center gap-1.5 transition-all"
              >
                {isSaved ? <Check className="w-4 h-4" /> : null}
                <span>{isSaved ? 'Connected!' : 'Save & Connect'}</span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
