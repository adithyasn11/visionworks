'use client';

// frontend/app/login/page.jsx
import React, { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { supabase, isSupabaseConfigured } from '../lib/supabaseClient';
import { ThemeToggle } from '../components/ThemeToggle';
import AuthAside from '../components/AuthAside';
import { GoogleButton, Divider, Field, PasswordField, Banner, SubmitButton } from '../components/AuthFormBits';

const ASIDE = {
  eyebrow: 'cam_floor_01 // live',
  headline: 'Every person counted. No face ever stored.',
  sub: 'Detection, tracking and posture run on your own hardware. What leaves the machine is a number, not a frame.',
  facts: [
    { term: 'model',     detail: 'YOLOv8m-pose, 17-point COCO skeleton' },
    { term: 'posture',   detail: 'Sitting / standing / walking, smoothed across frames' },
    { term: 'retention', detail: 'Frames dropped from memory after inference' },
  ],
};

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

/**
 * Where to send someone after a successful sign-in.
 *
 * Platform operators go to the founder console; everyone else to /dashboard.
 * The role check is a database call (is_platform_admin()), not a hardcoded
 * list, so revoking access in platform_admins takes effect on the next sign-in
 * with nothing to redeploy.
 *
 * WHY /dashboard AND NOT /home
 *
 * The dashboard is the product. A member signing in wants their workspace, not
 * a marketing page they must click through — so they land there directly.
 *
 * A user with NO organisation is not stranded by this: the dashboard layout
 * guard redirects them to /onboarding, which redirects to /home when they have
 * no plan yet. The chain resolves to the right screen in one hop each way, and
 * every link in it is a server redirect, so nothing renders before it moves.
 *
 * `next` is honoured only when it is a same-origin absolute path — an
 * attacker-supplied `?next=https://evil.example` would otherwise turn the login
 * page into an open redirect.
 */
async function resolveLandingPath(client, next) {
  if (next && next.startsWith('/') && !next.startsWith('//')) return next;
  try {
    const { data, error } = await client.rpc('is_platform_admin');
    if (!error && data === true) return '/platform';
  } catch {
    // Fall through to /dashboard: a failed role probe should not block a
    // successful sign-in.
  }
  return '/dashboard';
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mounted, setMounted] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

  useEffect(() => setMounted(true), []);

  // The OAuth callback redirects back here with ?authError=... when the provider
  // or the code exchange failed. Surfacing it beats a silent bounce to a blank
  // login form — "provider is not enabled" is a config fix the reader can act on.
  useEffect(() => {
    const authError = searchParams.get('authError');
    if (!authError) return;
    setBanner({
      kind: 'error',
      text: /provider is not enabled/i.test(authError)
        ? 'Google sign-in is not enabled on this project yet. Enable the Google provider in Supabase → Authentication → Providers.'
        : describeAuthError({ message: authError }),
    });
  }, [searchParams]);

  // Already signed in? Route by role rather than always to /dashboard, so a
  // returning operator lands in the console. Middleware does the same check
  // server-side; this is the belt-and-braces client path for a soft navigation.
  useEffect(() => {
    if (!supabase) return;
    let active = true;

    // getUser(), not getSession(): getSession() trusts whatever token is cached
    // locally, so a session the server has already revoked still looks valid
    // and bounces the user straight back off the login page — the exact
    // "clicking login logs me in automatically" symptom. getUser() validates
    // against the auth server, so a revoked session correctly resolves to null
    // and the form is shown.
    supabase.auth.getUser().then(async ({ data, error }) => {
      if (!active || error || !data?.user) return;
      const target = await resolveLandingPath(supabase, searchParams.get('next'));
      if (active) router.replace(target);
    });

    return () => { active = false; };
  }, [router, searchParams]);

  const validate = () => {
    const next = {};
    if (!email.trim()) next.email = 'Enter your email address.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) next.email = 'That email address doesn’t look right.';
    if (!password) next.password = 'Enter your password.';
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setBanner(null);
    if (!validate()) return;

    if (!isSupabaseConfigured || !supabase) {
      setBanner({ kind: 'error', text: 'Sign-in is not connected yet. Add your Supabase URL and anon key to frontend/.env.local, then restart the dev server.' });
      return;
    }

    setBusy(true);
    try {
      const { error } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (error) {
        // Supabase returns the same message for wrong password and unknown
        // account, which is correct — don't leak which one it was.
        // Unconfirmed accounts are the most common first-run stumble, so name
        // the fix rather than echoing Supabase's terse message.
        const notConfirmed = /email not confirmed/i.test(error.message)
          || error.code === 'email_not_confirmed';
        setBanner({
          kind: 'error',
          text: notConfirmed
            ? 'Check your inbox and open the confirmation link we sent, then sign in again.'
            : /invalid login|invalid credentials/i.test(error.message)
              ? 'That email and password combination doesn’t match an account.'
              : describeAuthError(error),
        });
        return;
      }
      const target = await resolveLandingPath(supabase, searchParams.get('next'));
      router.replace(target);
      // The session now lives in cookies, so the server needs to re-render with
      // the new auth state. Without refresh(), a cached Server Component payload
      // for the target route can still show the signed-out view.
      router.refresh();
    } catch (err) {
      setBanner({ kind: 'error', text: describeAuthError(err) });
    } finally {
      setBusy(false);
    }
  };

  const handleGoogle = async () => {
    setBanner(null);
    if (!isSupabaseConfigured || !supabase) {
      setBanner({ kind: 'error', text: 'Google sign-in is not connected yet. Add your Supabase credentials to frontend/.env.local and enable the Google provider in your Supabase project.' });
      return;
    }
    setGoogleBusy(true);
    try {
      const nextParam = searchParams.get('next');
      const callback = new URL('/auth/callback', window.location.origin);
      // Preserve where the user was heading, so the callback can send them
      // there instead of dropping them on a default landing page.
      if (nextParam && nextParam.startsWith('/') && !nextParam.startsWith('//')) {
        callback.searchParams.set('next', nextParam);
      }

      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          // Must land on the server callback, not a page: the PKCE code has to
          // be exchanged server-side to set httpOnly session cookies.
          redirectTo: callback.toString(),
          queryParams: {
            // Without prompt=select_account Google silently reuses whichever
            // account is already signed in to the browser and bounces straight
            // back — so the user never sees a chooser and cannot switch
            // accounts. Forcing the chooser is what makes "Continue with
            // Google" behave the way people expect.
            prompt: 'select_account',
          },
        },
      });
      if (error) {
        setBanner({ kind: 'error', text: describeAuthError(error) });
        setGoogleBusy(false);
      }
      // On success the browser is redirected to Google, so nothing else to do.
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

              <header className="flex flex-col gap-2">
                <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1">
                  Sign in
                </h1>
                <p className="text-[14px] text-ink-muted font-medium leading-relaxed">
                  Pick up where you left off with your live activity dashboard.
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
                label={googleBusy ? 'Opening Google…' : 'Continue with Google'}
              />

              <Divider>or</Divider>

              <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
                <Field
                  id="email"
                  label="Email"
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setErrors((s) => ({ ...s, email: undefined })); }}
                  placeholder="you@company.com"
                  autoComplete="email"
                  error={errors.email}
                />

                <PasswordField
                  id="password"
                  label="Password"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setErrors((s) => ({ ...s, password: undefined })); }}
                  autoComplete="current-password"
                  error={errors.password}
                />

                <SubmitButton busy={busy}>
                  {busy ? 'Signing in…' : 'Sign in'}
                  {!busy && <ArrowRight className="w-4 h-4" />}
                </SubmitButton>
              </form>

              <p className="text-[13px] text-ink-muted font-medium text-center">
                New to VisionWorks?{' '}
                <Link href="/signup" className="font-bold text-accent hover:underline">
                  Create an account
                </Link>
              </p>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * useSearchParams() opts a route out of static prerendering unless it sits
 * inside a Suspense boundary — the build fails with "should be wrapped in a
 * suspense boundary" otherwise. The fallback matches the pre-mount shell the
 * form itself renders, so there is no visible flash between the two.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-ground" />}>
      <LoginForm />
    </Suspense>
  );
}
