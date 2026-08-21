'use client';

// frontend/app/signup/page.jsx
import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import { supabase, isSupabaseConfigured } from '../lib/supabaseClient';
import { ThemeToggle } from '../components/ThemeToggle';
import AuthAside from '../components/AuthAside';
import { GoogleButton, Divider, Field, PasswordField, Banner, SubmitButton } from '../components/AuthFormBits';

const ASIDE = {
  eyebrow: 'setup // ~5 min',
  headline: 'Point it at a camera you already own.',
  sub: 'Draw two zones on the feed and the occupancy, dwell time and posture data starts the same afternoon.',
  facts: [
    { term: 'input',     detail: 'RTSP stream, uploaded file, or a plain webcam' },
    { term: 'hardware',  detail: 'CUDA GPU for real time; CPU fallback works too' },
    { term: 'retention', detail: 'No footage written to disk, ever' },
  ],
};

// Password rules, checked live so the requirement is never a surprise on submit.
const RULES = [
  { id: 'len',   label: 'At least 8 characters', test: (v) => v.length >= 8 },
  { id: 'case',  label: 'Upper and lower case',  test: (v) => /[a-z]/.test(v) && /[A-Z]/.test(v) },
  { id: 'digit', label: 'A number',              test: (v) => /\d/.test(v) },
];

/**
 * Supabase surfaces a bare "Failed to fetch" when the project host can't be
 * reached (paused project, wrong URL, or no network). Translate that into
 * something the reader can act on.
 */
function describeAuthError(err) {
  const msg = String(err?.message || err || '');
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return 'Couldn’t reach the authentication service. Check that your Supabase project is active and that NEXT_PUBLIC_SUPABASE_URL in frontend/.env.local is correct.';
  }
  return msg || 'Something went wrong. Please try again.';
}

export default function SignupPage() {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    // getUser() rather than getSession(): a locally-cached token the server has
    // already revoked would otherwise look valid and redirect away from the
    // signup form.
    supabase.auth.getUser().then(({ data, error }) => {
      if (active && !error && data?.user) router.replace('/dashboard');
    });
    return () => { active = false; };
  }, [router]);

  const ruleState = useMemo(
    () => RULES.map((r) => ({ ...r, ok: r.test(password) })),
    [password]
  );
  const passwordValid = ruleState.every((r) => r.ok);

  const validate = () => {
    const next = {};
    if (!name.trim()) next.name = 'Enter your name.';
    if (!email.trim()) next.email = 'Enter your email address.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) next.email = 'That email address doesn’t look right.';
    if (!password) next.password = 'Choose a password.';
    else if (!passwordValid) next.password = 'Your password doesn’t meet the requirements below yet.';
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setBanner(null);
    if (!validate()) return;

    if (!isSupabaseConfigured || !supabase) {
      setBanner({ kind: 'error', text: 'Sign-up is not connected yet. Add your Supabase URL and anon key to frontend/.env.local, then restart the dev server.' });
      return;
    }

    setBusy(true);
    try {
      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: {
          data: { full_name: name.trim() },
          emailRedirectTo: `${window.location.origin}/login`,
        },
      });

      if (error) {
        setBanner({
          kind: 'error',
          text: /already registered|already exists/i.test(error.message)
            ? 'An account with that email already exists. Try signing in instead.'
            : describeAuthError(error),
        });
        return;
      }

      // With email confirmation on, Supabase returns a user but no session.
      if (data?.session) {
        router.replace('/dashboard');
      } else {
        setDone(true);
      }
    } catch (err) {
      setBanner({ kind: 'error', text: describeAuthError(err) });
    } finally {
      setBusy(false);
    }
  };

  const handleGoogle = async () => {
    setBanner(null);
    if (!isSupabaseConfigured || !supabase) {
      setBanner({ kind: 'error', text: 'Google sign-up is not connected yet. Add your Supabase credentials to frontend/.env.local and enable the Google provider in your Supabase project.' });
      return;
    }
    setGoogleBusy(true);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          // Must land on the server callback, not a page: the PKCE code has to
          // be exchanged server-side to set httpOnly session cookies.
          redirectTo: `${window.location.origin}/auth/callback`,
          queryParams: {
            // Force the account chooser rather than silently reusing whichever
            // Google account the browser is already signed in to.
            prompt: 'select_account',
          },
        },
      });
      if (error) {
        setBanner({ kind: 'error', text: describeAuthError(error) });
        setGoogleBusy(false);
      }
    } catch (err) {
      setBanner({ kind: 'error', text: describeAuthError(err) });
      setGoogleBusy(false);
    }
  };

  if (!mounted) return <div className="min-h-screen bg-ground" />;

  return (
    <div className="themed auth-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] h-full">

        {/* LEFT — editorial panel */}
        <AuthAside {...ASIDE} />

        {/* RIGHT — the form */}
        <main className="auth-pane flex flex-col">
          <div className="flex items-center justify-between">
            <Link
              href="/"
              className="inline-flex items-center gap-2 text-[13px] font-bold text-ink-muted hover:text-accent transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to site
            </Link>
            <ThemeToggle />
          </div>

          <div className="flex-1 min-h-0 flex items-center justify-center overflow-y-auto">
            <div className="w-full max-w-sm auth-col">

              {done ? (
                <div className="flex flex-col gap-5">
                  <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">
                    Confirm your email
                  </h1>
                  <Banner kind="success">
                    We’ve sent a confirmation link to <strong>{email.trim()}</strong>. Open it to activate your account, then sign in.
                  </Banner>
                  <Link
                    href="/login"
                    className="w-full flex items-center justify-center gap-2.5 px-5 py-3 rounded-xl bg-accent text-white font-bold text-[14px] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-red-600/30 transition-all duration-200"
                  >
                    Go to sign in <ArrowRight className="w-4 h-4" />
                  </Link>
                </div>
              ) : (
                <>
                  <header className="flex flex-col gap-2">
                    <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">
                      Create your account
                    </h1>
                    <p className="text-[14px] text-ink-muted font-medium leading-relaxed">
                      Connect a camera and see how your space is used — usually within the hour.
                    </p>
                  </header>

                  {!isSupabaseConfigured && (
                    <Banner kind="error">
                      Authentication isn’t configured. Add <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
                      <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to{' '}
                      <code className="font-mono">frontend/.env.local</code>, then restart the dev server.
                    </Banner>
                  )}

                  {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

                  <GoogleButton
                    onClick={handleGoogle}
                    disabled={googleBusy || busy}
                    label={googleBusy ? 'Opening Google…' : 'Sign up with Google'}
                  />

                  <Divider>or</Divider>

                  <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
                    <Field
                      id="name"
                      label="Full name"
                      value={name}
                      onChange={(e) => { setName(e.target.value); setErrors((s) => ({ ...s, name: undefined })); }}
                      placeholder="Alex Fernandes"
                      autoComplete="name"
                      error={errors.name}
                    />

                    <Field
                      id="email"
                      label="Work email"
                      type="email"
                      value={email}
                      onChange={(e) => { setEmail(e.target.value); setErrors((s) => ({ ...s, email: undefined })); }}
                      placeholder="you@company.com"
                      autoComplete="email"
                      error={errors.email}
                    />

                    <div className="flex flex-col gap-2.5">
                      <PasswordField
                        id="new-password"
                        label="Password"
                        value={password}
                        onChange={(e) => { setPassword(e.target.value); setErrors((s) => ({ ...s, password: undefined })); }}
                        autoComplete="new-password"
                        error={errors.password}
                      />
                      <ul className="flex flex-col gap-1.5">
                        {ruleState.map(({ id, label, ok }) => (
                          <li
                            key={id}
                            className={`flex items-center gap-2 text-[12px] font-bold transition-colors ${
                              ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-ink-faint'
                            }`}
                          >
                            <span
                              className={`w-3.5 h-3.5 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                                ok ? 'bg-emerald-500/20' : 'bg-[color:var(--surface-alt)]'
                              }`}
                            >
                              {ok && <Check className="w-2.5 h-2.5" />}
                            </span>
                            {label}
                          </li>
                        ))}
                      </ul>
                    </div>

                    <SubmitButton busy={busy}>
                      {busy ? 'Creating account…' : 'Create account'}
                      {!busy && <ArrowRight className="w-4 h-4" />}
                    </SubmitButton>

                    <p className="text-[12px] text-ink-faint font-medium leading-relaxed text-center">
                      By creating an account you agree to our{' '}
                      <Link href="/security" className="font-bold text-ink-muted hover:text-accent">privacy approach</Link>.
                    </p>
                  </form>

                  <p className="text-[13px] text-ink-muted font-medium text-center">
                    Already have an account?{' '}
                    <Link href="/login" className="font-bold text-accent hover:underline">
                      Sign in
                    </Link>
                  </p>
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
