'use client';

// frontend/app/onboarding/OnboardingWizard.jsx
//
// Three steps, one mandatory.
//
//   1. Organisation   required — this is the write that creates the membership
//                     row, and nothing in the product works without it
//   2. Site           skippable — describes the space being measured
//   3. Camera         skippable — the video source
//
// WHY ONLY STEP 1 IS REQUIRED
//
// A manager evaluating the product should be able to look around before
// committing to a floor plan. Trapping them in a four-field wizard to reach an
// empty dashboard is how evaluations end. Steps 2 and 3 are also both
// reachable later from the dashboard's own Zones and Reports sections, so
// nothing is lost by deferring them.
//
// WHY STEP 1 IS IRREVERSIBLE ONCE DONE
//
// create_organisation() commits a transaction. After it returns there is a real
// organisation with a real membership, so the Back button disappears — offering
// it would imply the org could be un-created, which it cannot from here. The
// step indicator marks it done instead.
//
// Visual language is deliberately the sign-up screen's: same AuthAside, same
// Field/SubmitButton/Banner primitives, so onboarding reads as the last screen
// of signup rather than a different application. It does NOT reuse
// `.auth-screen`, though — that class is `height: 100svh; overflow: hidden`,
// sized for a five-field form. Step 2 carries capacity, two time inputs and
// seven weekday toggles, which would be clipped with no way to scroll to the
// submit button. This shell is min-height instead and scrolls when it must.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Check, Building2, MapPin, Video, Loader2 } from 'lucide-react';

import AuthAside from '../components/AuthAside';
import { ThemeToggle } from '../components/ThemeToggle';
import { Field, Banner, SubmitButton } from '../components/AuthFormBits';
import { planName } from '../lib/plans';
import { createOrganisation, updateSite, createCamera, finishOnboarding } from './actions';

const ASIDE = {
  eyebrow: 'step 1 of 3 // ~2 min',
  headline: 'One organisation, then the space it measures.',
  sub: 'Your organisation is the boundary every measurement lives inside. Nobody outside it can read your occupancy data — not even us.',
  facts: [
    { term: 'isolation', detail: 'Enforced in Postgres, not in the interface' },
    { term: 'timezone',  detail: 'Decides what “peak hour” means for your reports' },
    { term: 'later',     detail: 'Sites and cameras can wait — only step 1 is required' },
  ],
};

const STEPS = [
  { id: 1, label: 'Organisation', icon: Building2 },
  { id: 2, label: 'Site',         icon: MapPin },
  { id: 3, label: 'Camera',       icon: Video },
];

/* ── Time helpers ────────────────────────────────────────────────────────── */

// Working hours are stored as minutes from midnight (sites.workdayStartMinute).
// <input type="time"> speaks "HH:MM", so the two forms convert at the edge and
// the component only ever holds the string.
const toMinutes = (hhmm) => {
  const [h, m] = String(hhmm).split(':').map(Number);
  if (!Number.isInteger(h) || !Number.isInteger(m)) return NaN;
  return h * 60 + m;
};
const fromMinutes = (mins) =>
  `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`;

// ISO-8601: 1 = Monday … 7 = Sunday. Same convention as sites.workdays, so no
// translation layer is needed between the checkbox and the column.
const WEEKDAYS = [
  { n: 1, short: 'Mon' }, { n: 2, short: 'Tue' }, { n: 3, short: 'Wed' },
  { n: 4, short: 'Thu' }, { n: 5, short: 'Fri' }, { n: 6, short: 'Sat' },
  { n: 7, short: 'Sun' },
];

const SOURCES = [
  { id: 'UPLOAD', label: 'Upload a video',  hint: 'Process a recorded file once' },
  { id: 'WEBCAM', label: 'Webcam',          hint: 'A camera on the machine running the pipeline' },
  { id: 'RTSP',   label: 'IP camera (RTSP)', hint: 'A networked CCTV stream' },
];

/* ── Chrome ──────────────────────────────────────────────────────────────── */

/** Progress rail. Communicates "you are three short steps from done". */
function StepRail({ current, orgCreated }) {
  return (
    <ol className="flex items-center gap-2" aria-label="Setup progress">
      {STEPS.map((step, i) => {
        // Step 1 shows a tick the moment the transaction commits, even while
        // the user is standing on step 2 — the org genuinely exists by then.
        const done = step.id < current || (step.id === 1 && orgCreated);
        const active = step.id === current;
        return (
          <li key={step.id} className="flex items-center gap-2">
            <span
              aria-current={active ? 'step' : undefined}
              className={`flex items-center gap-2 rounded-full pl-1.5 pr-3 py-1.5 text-[12px] font-bold transition-colors duration-200 ${
                active
                  ? 'bg-accent-soft text-accent'
                  : done
                    ? 'text-ink-muted'
                    : 'text-ink-faint'
              }`}
            >
              <span
                className={`w-5 h-5 rounded-full flex items-center justify-center shrink-0 text-[10px] font-black transition-colors duration-200 ${
                  done
                    ? 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                    : active
                      ? 'bg-accent text-white'
                      : 'bg-[color:var(--surface-alt)] text-ink-faint'
                }`}
              >
                {done ? <Check className="w-3 h-3" strokeWidth={3} /> : step.id}
              </span>
              <span className="hidden sm:inline">{step.label}</span>
              <span className="sr-only sm:hidden">{step.label}</span>
            </span>
            {i < STEPS.length - 1 && (
              <span aria-hidden="true" className="w-4 h-px bg-[color:var(--line)]" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** "Skip for now" — the escape hatch that keeps steps 2 and 3 genuinely optional. */
function SkipButton({ onClick, disabled, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full text-center text-[13px] font-bold text-ink-muted hover:text-accent transition-colors disabled:opacity-50 disabled:cursor-not-allowed py-1"
    >
      {children}
    </button>
  );
}

/* ── Wizard ──────────────────────────────────────────────────────────────── */

export default function OnboardingWizard({ firstName, email, pendingPlan = null }) {
  const router = useRouter();

  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState(null);
  const [errors, setErrors] = useState({});

  // Ids returned by create_organisation(). `siteId` is the site that function
  // created inside its own transaction — step 2 updates that row rather than
  // inserting a second one.
  const [ids, setIds] = useState({ orgId: null, siteId: null });

  // Step 1
  const [orgName, setOrgName] = useState('');
  const [timezone, setTimezone] = useState('UTC');

  // Step 2 — seeded with the same default the schema uses (09:00–18:00, Mon–Fri)
  // so the common case is a single click.
  const [siteName, setSiteName] = useState('Main site');
  const [capacity, setCapacity] = useState('');
  const [startTime, setStartTime] = useState(fromMinutes(540));
  const [endTime, setEndTime] = useState(fromMinutes(1080));
  const [workdays, setWorkdays] = useState([1, 2, 3, 4, 5]);

  // Step 3
  const [cameraName, setCameraName] = useState('');
  const [sourceType, setSourceType] = useState('UPLOAD');
  const [rtspUrl, setRtspUrl] = useState('');

  // Focus the heading on each step change so a screen reader announces the new
  // step rather than leaving the user's focus on a button that just vanished.
  const headingRef = useRef(null);
  useEffect(() => { headingRef.current?.focus(); }, [step]);

  // The browser is the only thing that knows the user's timezone; the server
  // renders in UTC. Read it after mount so the markup is deterministic.
  useEffect(() => {
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz) setTimezone(tz);
    } catch {
      // Keep the UTC default. Never fatal — the org can be created either way
      // and the timezone is editable in settings later.
    }
  }, []);

  const slugPreview = useMemo(() => {
    // Mirrors the slugify inside create_organisation() so the preview matches
    // what is actually stored. The de-duplicating suffix is NOT shown: only
    // Postgres knows whether the slug is taken, and guessing "-2" here would be
    // wrong more often than right.
    const s = orgName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return s || null;
  }, [orgName]);

  const aside = useMemo(
    () => ({ ...ASIDE, eyebrow: `step ${step} of 3 // ~2 min` }),
    [step],
  );

  /* ── Actions ───────────────────────────────────────────────────────────── */

  /**
   * Advance a step AND record it in the URL.
   *
   * The `?step=` marker is what stops the server guard ejecting the user: from
   * step 2 onward the organisation exists, so a bare /onboarding would redirect
   * to /dashboard on any re-render. `history.replaceState` rather than a router
   * push — this is not a navigation, it is a note to the guard, and it must not
   * add a back-stack entry or remount the wizard (which would lose the org and
   * site ids held in state).
   */
  const advanceTo = (next) => {
    setStep(next);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('step', String(next));
      window.history.replaceState(null, '', url.toString());
    } catch {
      // A failed URL update only costs the guard's hint; the wizard still works
      // until something re-renders the page.
    }
  };

  const goDashboard = () => {
    // router.replace, not push: onboarding must not sit in the back stack. The
    // server guard would bounce a Back press straight to /dashboard anyway, but
    // that flashes a redirect the user did not ask for.
    router.replace('/dashboard');
    // The dashboard layout does a database read; refresh() makes sure the
    // client cache does not serve the pre-org render of it.
    router.refresh();
  };

  const submitOrg = async (e) => {
    e.preventDefault();
    setBanner(null);

    const name = orgName.trim();
    if (!name) { setErrors({ orgName: 'Enter your organisation’s name.' }); return; }
    if (!/[a-z0-9]/i.test(name)) {
      setErrors({ orgName: 'Use at least one letter or number.' });
      return;
    }
    setErrors({});

    setBusy(true);
    try {
      const fd = new FormData();
      fd.set('name', name);
      fd.set('timezone', timezone);
      fd.set('siteName', siteName.trim() || 'Main site');

      const res = await createOrganisation(fd);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }

      setIds({ orgId: res.orgId, siteId: res.siteId });
      advanceTo(2);
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  const submitSite = async (e) => {
    e.preventDefault();
    setBanner(null);

    const name = siteName.trim();
    const next = {};
    if (!name) next.siteName = 'Enter a name for this site.';

    if (capacity !== '') {
      const n = Number(capacity);
      if (!Number.isInteger(n) || n < 1) next.capacity = 'A whole number of 1 or more.';
      else if (n > 32767) next.capacity = 'That is larger than this field allows.';
    }

    const start = toMinutes(startTime);
    const end = toMinutes(endTime);
    if (Number.isNaN(start) || Number.isNaN(end)) next.hours = 'Choose valid working hours.';
    else if (start >= end) next.hours = 'The working day must start before it ends.';

    if (workdays.length === 0) next.workdays = 'Choose at least one working day.';

    setErrors(next);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const fd = new FormData();
      fd.set('siteId', ids.siteId ?? '');
      fd.set('name', name);
      fd.set('totalCapacity', capacity.trim());
      fd.set('workdayStartMinute', String(start));
      fd.set('workdayEndMinute', String(end));
      fd.set('workdays', workdays.join(','));

      const res = await updateSite(fd);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }
      advanceTo(3);
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  const submitCamera = async (e) => {
    e.preventDefault();
    setBanner(null);

    const name = cameraName.trim();
    const next = {};
    if (!name) next.cameraName = 'Enter a name for this camera.';
    if (sourceType === 'RTSP') {
      if (!rtspUrl.trim()) next.rtspUrl = 'Enter the stream URL.';
      else if (!/^rtsps?:\/\//i.test(rtspUrl.trim())) next.rtspUrl = 'Should start with rtsp:// or rtsps://.';
    }
    setErrors(next);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const fd = new FormData();
      // No orgId is sent: createCamera() reads it from the caller's own profile
      // on the server. The client asserting which tenant to write into is
      // exactly the input that should not be trusted.
      fd.set('siteId', ids.siteId ?? '');
      fd.set('name', name);
      fd.set('sourceType', sourceType);
      fd.set('rtspUrl', rtspUrl.trim());

      const res = await createCamera(fd);
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }
      goDashboard();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  /** Leave the wizard from step 2 or 3. The org already exists by then. */
  const skipToDashboard = async () => {
    setBanner(null);
    setBusy(true);
    try {
      const res = await finishOnboarding();
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return; }
      goDashboard();
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
    } finally {
      setBusy(false);
    }
  };

  const toggleWorkday = (n) => {
    setErrors((s) => ({ ...s, workdays: undefined }));
    setWorkdays((days) =>
      days.includes(n) ? days.filter((d) => d !== n) : [...days, n].sort((a, b) => a - b),
    );
  };

  /* ── Render ────────────────────────────────────────────────────────────── */

  return (
    <div className="themed min-h-screen bg-ground text-ink font-sans selection:bg-red-600 selection:text-white">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] min-h-screen">

        {/* LEFT — the same editorial panel as sign-up, so this reads as the
            final screen of registration rather than a separate product. */}
        <div className="hidden lg:block h-screen sticky top-0">
          <AuthAside {...aside} />
        </div>

        {/* RIGHT — the wizard. Scrolls rather than clipping: step 2 is tall. */}
        <main className="flex flex-col px-[clamp(1.25rem,4vw,2.5rem)] py-[clamp(1rem,2.2vh,2rem)] min-h-screen">
          <div className="flex items-center justify-between gap-4">
            <StepRail current={step} orgCreated={Boolean(ids.orgId)} />
            <ThemeToggle />
          </div>

          <div className="flex-1 flex items-center justify-center py-8">
            <div className="w-full max-w-sm flex flex-col gap-[clamp(0.85rem,2.4vh,1.75rem)]">

              {/* ── STEP 1 — Organisation (required) ── */}
              {step === 1 && (
                <>
                  <header className="flex flex-col gap-2">
                    <h1
                      ref={headingRef}
                      tabIndex={-1}
                      className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1 outline-none"
                    >
                      {firstName ? `Welcome, ${firstName}.` : 'Create your organisation'}
                    </h1>
                    <p className="text-[14px] text-ink-muted font-medium leading-relaxed">
                      Everything you measure lives inside an organisation. You’ll be its
                      administrator.
                    </p>

                    {/* Carries the checkout choice forward, so the wizard is
                        visibly the next step of the flow rather than an
                        unrelated form. Absent when no plan was chosen — the
                        organisation is still created, on the free tier (see the
                        note in page.jsx). */}
                    {pendingPlan && (
                      <p className="inline-flex items-center gap-1.5 self-start bg-accent-soft text-accent px-2.5 py-1 rounded-full text-[11px] font-black uppercase tracking-[0.1em]">
                        <Check className="w-3 h-3" strokeWidth={3} aria-hidden="true" />
                        {planName(pendingPlan)} plan
                      </p>
                    )}
                  </header>

                  {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

                  <form onSubmit={submitOrg} noValidate className="flex flex-col gap-[clamp(0.7rem,1.9vh,1.25rem)]">
                    <Field
                      id="orgName"
                      label="Organisation name"
                      value={orgName}
                      onChange={(e) => { setOrgName(e.target.value); setErrors({}); }}
                      placeholder="Acme Facilities"
                      autoComplete="organization"
                      maxLength={160}
                      error={errors.orgName}
                      hint={slugPreview ? `Your handle will be ${slugPreview}` : 'Usually your company or building operator’s name.'}
                    />

                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="timezone" className="text-[13px] font-bold text-ink">
                        Timezone
                      </label>
                      <select
                        id="timezone"
                        name="timezone"
                        value={timezone}
                        onChange={(e) => setTimezone(e.target.value)}
                        className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)]"
                      >
                        {/* Populated from the browser's own timezone database
                            where available, so the list is complete and current
                            rather than a hand-maintained subset. The detected
                            zone is preselected. */}
                        {(typeof Intl.supportedValuesOf === 'function'
                          ? Intl.supportedValuesOf('timeZone')
                          : [timezone, 'UTC'].filter((v, i, a) => a.indexOf(v) === i)
                        ).map((tz) => (
                          <option key={tz} value={tz}>{tz}</option>
                        ))}
                      </select>
                      <p className="text-[12px] text-ink-faint font-medium">
                        Buckets are stored in UTC and read in this zone — so “peak hour”
                        means your local peak.
                      </p>
                    </div>

                    <SubmitButton busy={busy}>
                      {busy ? 'Creating…' : 'Create organisation'}
                      {!busy && <ArrowRight className="w-4 h-4" />}
                    </SubmitButton>
                  </form>

                  <p className="text-[12px] text-ink-faint font-medium leading-relaxed text-center">
                    Signed in as {email ?? 'your account'}.
                  </p>
                </>
              )}

              {/* ── STEP 2 — Site (skippable) ── */}
              {step === 2 && (
                <>
                  <header className="flex flex-col gap-2">
                    <h1
                      ref={headingRef}
                      tabIndex={-1}
                      className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1 outline-none"
                    >
                      Describe the space
                    </h1>
                    <p className="text-[14px] text-ink-muted font-medium leading-relaxed">
                      A site is a building or a floor. Its capacity and hours are what every
                      utilisation percentage is measured against.
                    </p>
                  </header>

                  {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

                  <form onSubmit={submitSite} noValidate className="flex flex-col gap-[clamp(0.7rem,1.9vh,1.25rem)]">
                    <Field
                      id="siteName"
                      label="Site name"
                      value={siteName}
                      onChange={(e) => { setSiteName(e.target.value); setErrors((s) => ({ ...s, siteName: undefined })); }}
                      placeholder="HQ — Level 4"
                      maxLength={160}
                      error={errors.siteName}
                    />

                    <Field
                      id="capacity"
                      label="Desk capacity"
                      type="number"
                      min="1"
                      max="32767"
                      inputMode="numeric"
                      value={capacity}
                      onChange={(e) => { setCapacity(e.target.value); setErrors((s) => ({ ...s, capacity: undefined })); }}
                      placeholder="60"
                      error={errors.capacity}
                      hint="Optional. The denominator for “this floor runs at 71%”."
                    />

                    <div className="flex flex-col gap-1.5">
                      <span className="text-[13px] font-bold text-ink">Working hours</span>
                      <div className="flex items-center gap-2">
                        <input
                          type="time"
                          aria-label="Working day starts"
                          value={startTime}
                          onChange={(e) => { setStartTime(e.target.value); setErrors((s) => ({ ...s, hours: undefined })); }}
                          className="flex-1 min-w-0 rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3 py-2.5 text-[14px] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)]"
                        />
                        <span className="text-[13px] font-bold text-ink-faint shrink-0">to</span>
                        <input
                          type="time"
                          aria-label="Working day ends"
                          value={endTime}
                          onChange={(e) => { setEndTime(e.target.value); setErrors((s) => ({ ...s, hours: undefined })); }}
                          className="flex-1 min-w-0 rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3 py-2.5 text-[14px] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)]"
                        />
                      </div>
                      {errors.hours ? (
                        <p className="text-[12px] font-bold text-accent">{errors.hours}</p>
                      ) : (
                        <p className="text-[12px] text-ink-faint font-medium">
                          Buckets outside these hours are excluded — a cleaner at 03:00
                          shouldn’t look like underuse.
                        </p>
                      )}
                    </div>

                    <fieldset className="flex flex-col gap-1.5">
                      <legend className="text-[13px] font-bold text-ink mb-1.5">Working days</legend>
                      <div className="flex flex-wrap gap-1.5">
                        {WEEKDAYS.map(({ n, short }) => {
                          const on = workdays.includes(n);
                          return (
                            <button
                              key={n}
                              type="button"
                              onClick={() => toggleWorkday(n)}
                              aria-pressed={on}
                              className={`px-2.5 py-1.5 rounded-lg border-2 text-[12px] font-bold transition-colors duration-150 ${
                                on
                                  ? 'border-[color:var(--accent)] bg-accent-soft text-accent'
                                  : 'border-field text-ink-muted hover:border-field-hover'
                              }`}
                            >
                              {short}
                            </button>
                          );
                        })}
                      </div>
                      {errors.workdays && (
                        <p className="text-[12px] font-bold text-accent mt-1">{errors.workdays}</p>
                      )}
                    </fieldset>

                    <SubmitButton busy={busy}>
                      {busy ? 'Saving…' : 'Save and continue'}
                      {!busy && <ArrowRight className="w-4 h-4" />}
                    </SubmitButton>
                  </form>

                  <div className="flex flex-col gap-2">
                    <SkipButton onClick={() => { setBanner(null); setErrors({}); advanceTo(3); }} disabled={busy}>
                      Skip — add a camera instead
                    </SkipButton>
                    <SkipButton onClick={skipToDashboard} disabled={busy}>
                      {busy ? 'Finishing…' : 'Finish setup later'}
                    </SkipButton>
                  </div>
                </>
              )}

              {/* ── STEP 3 — Camera (skippable) ── */}
              {step === 3 && (
                <>
                  <header className="flex flex-col gap-2">
                    <h1
                      ref={headingRef}
                      tabIndex={-1}
                      className="text-2xl sm:text-3xl font-black tracking-tight text-ink leading-[1.15] pb-1 outline-none"
                    >
                      Add a camera
                    </h1>
                    <p className="text-[14px] text-ink-muted font-medium leading-relaxed">
                      The video source. Nothing is connected yet — you’ll draw its zones on
                      the dashboard next.
                    </p>
                  </header>

                  {banner && <Banner kind={banner.kind}>{banner.text}</Banner>}

                  <form onSubmit={submitCamera} noValidate className="flex flex-col gap-[clamp(0.7rem,1.9vh,1.25rem)]">
                    <Field
                      id="cameraName"
                      label="Camera name"
                      value={cameraName}
                      onChange={(e) => { setCameraName(e.target.value); setErrors((s) => ({ ...s, cameraName: undefined })); }}
                      placeholder="Floor 4 — North"
                      maxLength={160}
                      error={errors.cameraName}
                    />

                    <fieldset className="flex flex-col gap-1.5">
                      <legend className="text-[13px] font-bold text-ink mb-1.5">Video source</legend>
                      <div className="flex flex-col gap-1.5">
                        {SOURCES.map(({ id, label, hint }) => {
                          const on = sourceType === id;
                          return (
                            <label
                              key={id}
                              className={`flex items-start gap-2.5 rounded-lg border-2 px-3 py-2.5 cursor-pointer transition-colors duration-150 ${
                                on
                                  ? 'border-[color:var(--accent)] bg-accent-soft'
                                  : 'border-field hover:border-field-hover'
                              }`}
                            >
                              <input
                                type="radio"
                                name="sourceType"
                                value={id}
                                checked={on}
                                onChange={() => { setSourceType(id); setErrors((s) => ({ ...s, rtspUrl: undefined })); }}
                                className="mt-0.5 accent-[color:var(--accent)]"
                              />
                              <span className="flex flex-col gap-0.5 min-w-0">
                                <span className={`text-[13px] font-bold ${on ? 'text-accent' : 'text-ink'}`}>
                                  {label}
                                </span>
                                <span className="text-[12px] text-ink-faint font-medium leading-snug">
                                  {hint}
                                </span>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>

                    {sourceType === 'RTSP' && (
                      <Field
                        id="rtspUrl"
                        label="Stream URL"
                        value={rtspUrl}
                        onChange={(e) => { setRtspUrl(e.target.value); setErrors((s) => ({ ...s, rtspUrl: undefined })); }}
                        placeholder="rtsp://camera.local:554/stream1"
                        autoComplete="off"
                        spellCheck={false}
                        maxLength={1024}
                        error={errors.rtspUrl}
                        hint="Stored encrypted and never sent back to the browser."
                      />
                    )}

                    <SubmitButton busy={busy}>
                      {busy ? 'Adding…' : 'Add camera and finish'}
                      {!busy && <ArrowRight className="w-4 h-4" />}
                    </SubmitButton>
                  </form>

                  <SkipButton onClick={skipToDashboard} disabled={busy}>
                    {busy ? 'Finishing…' : 'Skip — go to the dashboard'}
                  </SkipButton>
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
