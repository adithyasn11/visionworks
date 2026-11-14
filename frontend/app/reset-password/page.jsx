'use client';

// frontend/app/reset-password/page.jsx
// The second half of "Forgot password?". The emailed link went through /auth/callback, which signed the person
// in; here they choose a new password (same rules as sign-up) and go on to their dashboard. Without a session
// (an old or reused link) the page says so and offers a new link.
import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Check } from 'lucide-react';
import { supabase } from '../lib/supabaseClient';
import { ThemeToggle } from '../components/ThemeToggle';
import AuthAside from '../components/AuthAside';
import { PasswordField, Banner, SubmitButton } from '../components/AuthFormBits';

const ASIDE = { eyebrow: 'account // recovery', headline: 'Choose a new password.' };
const RULES = [
  { id: 'len', label: 'At least 8 characters', test: (v) => v.length >= 8 },
  { id: 'case', label: 'Upper and lower case', test: (v) => /[a-z]/.test(v) && /[A-Z]/.test(v) },
  { id: 'digit', label: 'A number', test: (v) => /\d/.test(v) },
];

export default function ResetPasswordPage() {
  const router = useRouter();
  const [session, setSession] = useState('checking');     // checking | yes | no
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!supabase) { setSession('no'); return; }
    supabase.auth.getUser().then(({ data, error }) => setSession(!error && data?.user ? 'yes' : 'no'));
  }, []);

  const rules = useMemo(() => RULES.map((r) => ({ ...r, ok: r.test(password) })), [password]);

  const submit = async (e) => {
    e.preventDefault();
    setBanner(null);
    const next = {};
    if (!rules.every((r) => r.ok)) next.password = 'Your password doesn’t meet the requirements below yet.';
    if (confirm !== password) next.confirm = 'The two passwords don’t match.';
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) {
        setBanner({
          kind: 'error',
          text: /different from the old|same password/i.test(error.message || '')
            ? 'Choose a password different from your old one.'
            : 'The password could not be changed. Request a new reset link and try again.',
        });
        return;
      }
      setBanner({ kind: 'success', text: 'Password changed. Taking you to your dashboard…' });
      setTimeout(() => { router.replace('/home'); router.refresh(); }, 900);
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
              <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">Choose a new password</h1>
              {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}
              {session === 'no' && (
                <>
                  <Banner kind="error">This reset link has expired or was already used.</Banner>
                  <Link href="/forgot-password" className="text-[0.8125rem] font-bold text-accent hover:underline">Send a new reset link</Link>
                </>
              )}
              {session === 'yes' && (
                <form onSubmit={submit} noValidate className="flex flex-col gap-5">
                  <div className="flex flex-col gap-3">
                    <PasswordField id="new-password" label="New password" value={password}
                      onChange={(e) => { setPassword(e.target.value); setErrors((s) => ({ ...s, password: undefined })); }}
                      autoComplete="new-password" error={errors.password} />
                    <ul className="flex flex-col gap-1.5">
                      {rules.map(({ id, label, ok }) => (
                        <li key={id} className={`flex items-center gap-2 text-[0.75rem] font-bold ${ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-ink-faint'}`}>
                          <span className={`w-3.5 h-3.5 rounded-full flex items-center justify-center ${ok ? 'bg-emerald-500/20' : 'bg-[color:var(--surface-alt)]'}`}>
                            {ok && <Check className="w-2.5 h-2.5" />}
                          </span>
                          {label}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <PasswordField id="confirm-password" label="Repeat it" value={confirm}
                    onChange={(e) => { setConfirm(e.target.value); setErrors((s) => ({ ...s, confirm: undefined })); }}
                    autoComplete="new-password" error={errors.confirm} />
                  <SubmitButton busy={busy}>Save new password</SubmitButton>
                </form>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
