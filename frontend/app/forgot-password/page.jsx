'use client';

// frontend/app/forgot-password/page.jsx
// "Forgot password?": asks Supabase to email a reset link. The link returns through /auth/callback (which signs
// the person in) to /reset-password, where they choose a new password. The answer is the same whether or not
// the address has an account, so this page cannot be used to find out who is registered.
import React, { useState } from 'react';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { supabase, isSupabaseConfigured } from '../lib/supabaseClient';
import { ThemeToggle } from '../components/ThemeToggle';
import AuthAside from '../components/AuthAside';
import { Field, Banner, SubmitButton } from '../components/AuthFormBits';

const ASIDE = { eyebrow: 'account // recovery', headline: 'Back in, in a minute.' };

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(null); setBanner(null);
    const clean = email.trim().toLowerCase();
    if (!clean) { setError('Enter your email address.'); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean)) { setError('That email address doesn’t look right.'); return; }
    if (!isSupabaseConfigured || !supabase) { setBanner({ kind: 'error', text: 'Sign-in is not connected yet.' }); return; }
    setBusy(true);
    try {
      const { error: err } = await supabase.auth.resetPasswordForEmail(clean, {
        redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
      });
      // Rate limits are worth saying; anything else gets the same answer as success (no account enumeration).
      if (err && /rate|too many|seconds/i.test(err.message || '')) {
        setBanner({ kind: 'error', text: 'Too many reset requests. Wait a minute, then try again.' });
        return;
      }
      setSent(true);
    } catch {
      setBanner({ kind: 'error', text: 'Couldn’t reach the authentication service. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="themed auth-screen bg-ground text-ink font-sans">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] h-full">
        <AuthAside {...ASIDE} />
        <main className="auth-pane flex flex-col">
          <div className="flex items-center justify-between">
            <Link href="/login" className="inline-flex items-center gap-2 text-[0.8125rem] font-bold text-ink-muted hover:text-accent transition-colors">
              <ArrowLeft className="w-4 h-4" /> Back to sign in
            </Link>
            <ThemeToggle />
          </div>
          <div className="flex-1 min-h-0 flex items-center justify-center overflow-y-auto">
            <div className="w-full max-w-sm auth-col flex flex-col gap-5">
              <header className="flex flex-col gap-2">
                <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">Reset your password</h1>
                <p className="text-[0.875rem] text-ink-muted font-medium leading-relaxed">
                  Enter the email you sign in with and we’ll send a link to choose a new password.
                </p>
              </header>
              {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}
              {sent ? (
                <>
                  <Banner kind="success">
                    If an account uses <strong>{email.trim().toLowerCase()}</strong>, a reset link is on its way. Open it on this device, then choose a new password.
                  </Banner>
                  <p className="text-[0.8125rem] text-ink-muted">
                    Signed up with Google? Use <strong>Continue with Google</strong> on the sign-in page instead; there is no password to reset.
                  </p>
                  <Link href="/login" className="text-[0.8125rem] font-bold text-accent hover:underline">Back to sign in</Link>
                </>
              ) : (
                <form onSubmit={submit} noValidate className="flex flex-col gap-5">
                  <Field
                    id="email"
                    label="Email"
                    type="email"
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setError(null); }}
                    placeholder="you@company.com"
                    autoComplete="email"
                    error={error}
                  />
                  <SubmitButton busy={busy}>Send reset link</SubmitButton>
                </form>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
