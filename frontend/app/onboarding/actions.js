'use server';

// frontend/app/onboarding/actions.js
//
// The three writes that turn a signed-up user into a tenant.
//
// WHY THIS FILE IS THIN
//
// Step 1 is not implemented here. It is `create_organisation()`, a SECURITY
// DEFINER function in prisma/sql/002_auth_triggers.sql, and it must stay there:
//
//   - It is FOUR writes that must be ATOMIC — organisations, sites, memberships,
//     profiles, plus the audit row. A Server Action calling supabase-js issues
//     each as a separate HTTP request over PostgREST, so there is no enclosing
//     transaction. If the membership insert failed after the org insert
//     succeeded, the user would own an organisation they are not a member of —
//     and `user_org_ids()` filters on membership, so that org would be
//     invisible to them forever. Not fixable from the UI, because every repair
//     query is itself filtered by the same policy. A stranded account.
//
//   - `organisations` deliberately has NO INSERT policy (003_rls_policies.sql
//     line 73). A browser client physically cannot create an org. The definer
//     function is the only door, which is what stops a client from inserting an
//     org row and then not a membership.
//
// So this file wraps that RPC and handles the two genuinely optional steps,
// which are ordinary single-table writes and need no such ceremony.
//
// EVERY ACTION RE-CHECKS AUTH. A Server Action is a POST endpoint the browser
// can call directly; it does not inherit the layout's guard. Postgres RLS is
// still the real boundary, but failing fast here returns a usable message
// instead of an opaque policy violation.

import { revalidatePath } from 'next/cache';
import { createClient } from '../lib/supabase/server';

/* ── Shared helpers ──────────────────────────────────────────────────────── */

const fail = (message) => ({ ok: false, message });

/**
 * Resolve the caller, or explain why we cannot.
 *
 * getUser(), never getSession(): getSession() trusts whatever the cookie says,
 * and a Server Action is exactly the endpoint where that matters.
 */
async function requireUser() {
  const supabase = createClient();
  if (!supabase) {
    return { error: 'Supabase is not configured. Add your project URL and anon key to frontend/.env.local, then restart the dev server.' };
  }
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) return { error: 'Your session has expired. Please sign in again.' };
  return { supabase, user: data.user };
}

/**
 * Turn a Postgres error into something a person can act on.
 *
 * The raw messages are accurate but unreadable ("new row violates row-level
 * security policy for table \"cameras\""). The constraint names are ours, from
 * 001_constraints.sql, so we can map the ones a user can actually trigger.
 */
function describeDbError(error, fallback) {
  const msg = String(error?.message ?? '');

  // Plan limits (015_plan_limits.sql). The trigger raises a message that is
  // already written for a person and already carries the tier and the number,
  // so it is passed through with a suffix rather than replaced by a vaguer
  // sentence that would have to hardcode the limit a second time.
  //
  // The `plan_limit_` prefix is the contract between the trigger and this
  // mapper; matching on it rather than on the prose means the wording can
  // change in SQL without silently falling through to the raw error here.
  const planLimit = msg.match(/plan_limit_(cameras|sites|seats):\s*(.+?)(?:\s*CONTEXT:|$)/is);
  if (planLimit) {
    return `${planLimit[2].trim()} Upgrade the plan to add more.`;
  }

  if (/row-level security/i.test(msg)) {
    return 'You do not have permission to do that in this organisation.';
  }
  if (/sites_orgId_name_key|cameras_orgId_name_key|duplicate key/i.test(msg)) {
    return 'Something with that name already exists here. Choose a different name.';
  }
  if (/sites_workday_window_valid/i.test(msg)) {
    return 'The working day must start before it ends.';
  }
  if (/sites_capacity_positive/i.test(msg)) {
    return 'Capacity must be a positive number.';
  }
  if (/must be called by an authenticated user/i.test(msg)) {
    return 'Your session has expired. Please sign in again.';
  }
  if (/no profile for user/i.test(msg)) {
    return 'Your profile has not finished setting up. Wait a moment and try again.';
  }
  // Unrecognised. Postgres's own words are for the server log, not the screen:
  // returning them leaks schema internals ("relation ... does not exist",
  // "schema cache") to anyone who can provoke an error, and they tell the
  // reader nothing they can act on. Log the real thing, show a sentence.
  if (msg) console.error('[onboarding] unhandled database error:', msg);
  return fallback;
}

/**
 * IANA timezone validation, delegated to the platform rather than a hardcoded
 * list — Intl already ships the full database and it stays current.
 * Rejecting an unknown zone matters: `organisations.timezone` is what every
 * "peak hour" figure is rendered in, and a bad value would silently skew
 * every bucket boundary in the product.
 */
function validTimezone(tz) {
  if (typeof tz !== 'string' || !tz || tz.length > 64) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/* ── Step 1 — create the organisation (mandatory) ────────────────────────── */

/**
 * Create the org, its first site, the ADMIN membership, the profile pointer and
 * the audit entry — atomically, inside Postgres.
 *
 * Returns the new org and site ids so steps 2 and 3 can address them without a
 * second round trip.
 */
export async function createOrganisation(formData) {
  const { supabase, user, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const name = String(formData.get('name') ?? '').trim();
  const timezone = String(formData.get('timezone') ?? '').trim() || 'UTC';
  const siteName = String(formData.get('siteName') ?? '').trim() || 'Main site';

  if (!name) return fail('Enter your organisation’s name.');
  if (name.length > 160) return fail('That name is too long — 160 characters at most.');

  // The slug is derived from the name by stripping everything that is not
  // [a-z0-9], and `organisations_slug_format` requires at least one such
  // character. A name of pure punctuation would slug to '' — the function
  // falls back to 'org', which is valid but meaningless. Rejecting here gives
  // a better message than silently creating "org-7".
  if (!/[a-z0-9]/i.test(name)) {
    return fail('Use at least one letter or number in the name.');
  }
  if (!validTimezone(timezone)) return fail('That timezone is not recognised.');
  if (siteName.length > 160) return fail('That site name is too long — 160 characters at most.');

  const { data, error } = await supabase.rpc('create_organisation', {
    p_name: name,
    p_timezone: timezone,
    p_site_name: siteName,
  });

  if (error) {
    return fail(describeDbError(error, 'Could not create your organisation. Please try again.'));
  }

  // A TABLE-returning function arrives from PostgREST as an array of rows.
  const row = Array.isArray(data) ? data[0] : data;
  if (!row?.org_id) {
    return fail('Could not create your organisation. Please try again.');
  }

  // NOTHING IS REVALIDATED HERE, and that is deliberate.
  //
  // `revalidatePath('/dashboard', 'layout')` re-renders the CURRENT route as
  // part of the Server Action's response — before any of the calling
  // component's code runs. /dashboard and /onboarding share the root layout,
  // so invalidating one re-runs the other's server component, which sees the
  // organisation that was just created and issues `redirect('/dashboard')`.
  // That redirect travels back with the action result and navigates the user
  // away mid-wizard, before `advanceTo(2)` ever executes.
  //
  // Two earlier attempts to fix this failed because they treated the symptom:
  // dropping `revalidatePath('/onboarding')` (the wrong call), then adding a
  // `?step=` marker to the URL (which is not set yet at the moment the action
  // responds).
  //
  // Nothing needs revalidating anyway. `/dashboard` and its layout are both
  // `force-dynamic`, so they re-read `profiles.currentOrgId` on every request
  // — there is no cached render for a stale pointer to survive in. The wizard
  // navigates there itself via `router.replace()` + `router.refresh()` when
  // the user is actually finished.

  return {
    ok: true,
    orgId: row.org_id,
    siteId: row.site_id,
    membershipId: row.membership_id,
    email: user.email ?? null,
  };
}

/* ── Step 2 — describe the site (skippable) ──────────────────────────────── */

/**
 * Fill in the site that create_organisation() already made.
 *
 * UPDATE, not INSERT. The org function creates the first site as part of its
 * transaction, so inserting here would either create a second, empty site or
 * collide with `sites_orgId_name_key` if the names matched. The site id comes
 * back from step 1 for exactly this reason.
 *
 * Working hours are load-bearing, not decoration: analytics excludes
 * out-of-hours buckets, so a cleaner walking through at 03:00 does not depress
 * the utilisation figure. Capacity is the denominator of every percentage the
 * product reports.
 */
export async function updateSite(formData) {
  const { supabase, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const siteId = String(formData.get('siteId') ?? '').trim();
  const name = String(formData.get('name') ?? '').trim();
  const capacityRaw = String(formData.get('totalCapacity') ?? '').trim();
  const startRaw = String(formData.get('workdayStartMinute') ?? '').trim();
  const endRaw = String(formData.get('workdayEndMinute') ?? '').trim();
  const workdaysRaw = String(formData.get('workdays') ?? '').trim();

  if (!siteId) return fail('Missing site. Please restart onboarding.');
  if (!name) return fail('Enter a name for this site.');
  if (name.length > 160) return fail('That site name is too long — 160 characters at most.');

  // Capacity is nullable; blank means "not declared yet", which is different
  // from zero and must not become 0 — `sites_capacity_positive` rejects 0, and
  // a zero denominator would make every utilisation percentage infinite.
  let totalCapacity = null;
  if (capacityRaw !== '') {
    const n = Number(capacityRaw);
    if (!Number.isInteger(n) || n < 1) return fail('Capacity must be a whole number of 1 or more.');
    // smallint column — anything larger overflows in Postgres, not in JS.
    if (n > 32767) return fail('Capacity must be 32,767 or fewer.');
    totalCapacity = n;
  }

  const start = Number(startRaw);
  const end = Number(endRaw);
  if (!Number.isInteger(start) || !Number.isInteger(end)) {
    return fail('Choose valid working hours.');
  }
  // Mirrors `sites_workday_window_valid`. Checked here so the user sees a
  // sentence rather than a constraint name.
  if (start < 0 || end > 1440 || start >= end) {
    return fail('The working day must start before it ends.');
  }

  // ISO-8601 weekdays, 1 = Monday … 7 = Sunday. De-duplicated and sorted so the
  // stored array is canonical regardless of the order the checkboxes were
  // clicked in.
  const workdays = [...new Set(
    workdaysRaw
      .split(',')
      .map((d) => Number(d.trim()))
      .filter((d) => Number.isInteger(d) && d >= 1 && d <= 7),
  )].sort((a, b) => a - b);

  if (workdays.length === 0) return fail('Choose at least one working day.');

  const { data: updated, error } = await supabase
    .from('sites')
    .update({
      name,
      totalCapacity,
      workdayStartMinute: start,
      workdayEndMinute: end,
      workdays,
      // `updatedAt` is a Prisma @updatedAt field, which Prisma maintains in the
      // CLIENT — the column has no DB default and no trigger. Writing through
      // PostgREST bypasses Prisma entirely, so it must be set by hand or the
      // NOT NULL constraint rejects the row.
      updatedAt: new Date().toISOString(),
    })
    .eq('id', siteId)
    // `.select()` is what makes this UPDATE verifiable, and it is not optional.
    //
    // An UPDATE whose rows are all filtered away by RLS is not an error — it is
    // a successful statement that matched nothing. Measured: updating a site in
    // another tenant's org returns `UPDATE 0` with no error at all. Without
    // this, a wrong or forged siteId would report "Saved" to the user while
    // writing nothing, which is worse than failing: it is a lie the user acts
    // on. Asking for the row back turns silence into an empty array we can
    // detect.
    .select('id');

  if (error) return fail(describeDbError(error, 'Could not save this site.'));

  if (!updated || updated.length === 0) {
    return fail('That site could not be found in your organisation. Please restart setup.');
  }

  // Not revalidated, for the same reason as createOrganisation() above: doing
  // so re-renders /onboarding mid-wizard and redirects the user away. The
  // dashboard is force-dynamic and reads fresh data on arrival.
  return { ok: true };
}

/* ── Step 3 — add a camera (skippable) ───────────────────────────────────── */

const SOURCE_TYPES = new Set(['UPLOAD', 'RTSP', 'WEBCAM']);

/**
 * Create the first camera.
 *
 * Left INACTIVE — the column default. A camera is "active" once the pipeline
 * has connected to it, and claiming that here would put a green light next to
 * something nothing has ever reached.
 */
export async function createCamera(formData) {
  const { supabase, user, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const siteId = String(formData.get('siteId') ?? '').trim();
  const name = String(formData.get('name') ?? '').trim();
  const sourceType = String(formData.get('sourceType') ?? '').trim();
  const rtspUrl = String(formData.get('rtspUrl') ?? '').trim();

  if (!name) return fail('Enter a name for this camera.');
  if (name.length > 160) return fail('That camera name is too long — 160 characters at most.');
  if (!SOURCE_TYPES.has(sourceType)) return fail('Choose where this camera’s video comes from.');

  // The org is read from the caller's own profile, NOT from the form.
  //
  // The client sends an orgId, and trusting it would be wrong even though RLS
  // would reject a foreign one: the value that reaches the database should be
  // the one the server established, not the one the browser asserted. Reading
  // it here means the form field is advisory and the write is authoritative.
  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId')
    .eq('id', user.id)
    .maybeSingle();

  const orgId = profile?.currentOrgId;
  if (!orgId) return fail('Create your organisation before adding a camera.');

  // Verify the site belongs to that same org before attaching the camera to it.
  //
  // WHY THIS IS NOT REDUNDANT WITH RLS. The `camera_insert` policy checks
  // `orgId` only — it says nothing about `siteId`, and there is no constraint
  // tying the two together (`cameras_siteId_fkey` points at `sites(id)` and
  // stops there). Measured: an INSERT carrying my own orgId and another
  // tenant's siteId is accepted by Postgres. It leaks nothing outward, because
  // `site_select` still hides that site from me — but it writes a row whose
  // parent I cannot see, which corrupts every later join and silently attaches
  // my camera to someone else's building. RLS is doing its job; org-consistency
  // across two columns is simply not a question a per-table policy can ask.
  //
  // The SELECT is itself RLS-filtered, so a foreign site returns no row and the
  // camera is created unattached rather than wrongly attached.
  let resolvedSiteId = null;
  if (siteId) {
    const { data: site } = await supabase
      .from('sites')
      .select('id')
      .eq('id', siteId)
      .eq('orgId', orgId)
      .maybeSingle();
    resolvedSiteId = site?.id ?? null;
  }

  const row = {
    orgId,
    siteId: resolvedSiteId,
    name,
    sourceType,
    updatedAt: new Date().toISOString(), // see the note in updateSite()
  };

  if (sourceType === 'RTSP') {
    if (!rtspUrl) return fail('Enter the RTSP stream URL.');
    if (rtspUrl.length > 1024) return fail('That URL is too long.');
    // Only the scheme is checked. An RTSP URL commonly carries credentials and
    // a vendor-specific path; anything stricter would reject working cameras.
    if (!/^rtsps?:\/\//i.test(rtspUrl)) {
      return fail('An RTSP URL should start with rtsp:// or rtsps://.');
    }
    row.rtspUrl = rtspUrl;
  } else if (sourceType === 'WEBCAM') {
    // Device 0 is the first camera on the machine and the right default; the
    // manager can change it once the pipeline reports what it found.
    row.deviceIndex = 0;
  }

  const { error } = await supabase.from('cameras').insert(row);
  if (error) return fail(describeDbError(error, 'Could not add this camera.'));

  // Not revalidated, for the same reason as createOrganisation() above: doing
  // so re-renders /onboarding mid-wizard and redirects the user away. The
  // dashboard is force-dynamic and reads fresh data on arrival.
  return { ok: true };
}

/* ── Finish ──────────────────────────────────────────────────────────────── */

/**
 * Mark onboarding complete for a user who skipped straight past step 1.
 *
 * Only reachable when the user already has an org — which means they arrived
 * here with `onboardedAt` already set by create_organisation(). This exists so
 * "Skip for now" has a single, explicit server-side end to the flow rather than
 * the client guessing at navigation, and it is idempotent: COALESCE-style, it
 * never moves a timestamp that is already set.
 */
export async function finishOnboarding() {
  const { supabase, user, error: authError } = await requireUser();
  if (authError) return fail(authError);

  const { data: profile } = await supabase
    .from('profiles')
    .select('currentOrgId, onboardedAt')
    .eq('id', user.id)
    .maybeSingle();

  // No org means step 1 never completed. Sending them to the dashboard would
  // bounce them straight back here by the layout guard, so say so instead.
  if (!profile?.currentOrgId) {
    return fail('Create your organisation before continuing.');
  }

  if (!profile.onboardedAt) {
    await supabase
      .from('profiles')
      .update({ onboardedAt: new Date().toISOString(), updatedAt: new Date().toISOString() })
      .eq('id', user.id);
  }

  revalidatePath('/dashboard', 'layout');
  return { ok: true };
}
