# VisionWorks — System Design & Product Flow

**Vision-Based Workplace Activity Analytics**
Last updated: 22 August 2026

---

## 0. How to read this document

This describes both **what is built** and **what is designed but not yet wired**.
Every section is marked so the two are never confused:

| Mark | Meaning |
|------|---------|
| ✅ **Built** | Implemented, verified, running today |
| 🟡 **Partial** | Exists but not connected end-to-end |
| ⬜ **Designed** | Schema/spec exists, code does not |

Claiming a 🟡 or ⬜ as done is the fastest way to lose a viva, so the marks are
deliberately conservative.

---

## 1. What the system is

VisionWorks turns an ordinary CCTV camera into a **space-utilisation and
ergonomic-wellness sensor**, without storing footage or identity.

A camera feed is processed locally through detection → tracking → pose
estimation → spatial mapping. What leaves the machine is **numbers**: how many
people, in which zone, in what posture, for how long. Frames are discarded after
inference.

**What it measures:** occupancy, dwell time, posture (sitting / standing /
walking), zone utilisation, movement patterns.

**What it explicitly does not measure:** productivity, concentration, task
output, or identity. Posture is a *physical state*, not a judgement about work.
This boundary is a design constraint, not a limitation to apologise for — it is
what makes the system deployable.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        CAPTURE                                   │
│  Video upload  ·  Server webcam  ·  Browser camera               │
└───────────────────────────┬──────────────────────────────────────┘
                            │  frames (in memory only)
┌───────────────────────────▼──────────────────────────────────────┐
│                  CV PIPELINE  (FastAPI, Python)                  │
│                                                                  │
│   YOLOv8m-pose ──▶ ByteTrack ──▶ PostureEstimator                │
│   detect+keypoints   identity      SITTING/STANDING/WALKING      │
│         │                               │                        │
│         ▼                               ▼                        │
│   SpatialEngine (homography)     ActivityAggregator              │
│   camera px ──▶ floorplan        dwell, activity index           │
│         │                               │                        │
│         └────────────┬──────────────────┘                        │
│                      ▼                                           │
│              PrivacyAnonymizer (head blur, ON by default)        │
└──────────────────────┬───────────────────────────────────────────┘
                       │  sampled numeric telemetry ONLY
                       │  (never frames, never identity)
┌──────────────────────▼───────────────────────────────────────────┐
│                       PERSISTENCE                                │
│                                                                  │
│   SQLite  activity_logs · zones · cameras      ← pipeline output │
│   Supabase/Postgres  16 tables, RLS on all     ← accounts, orgs  │
└──────────────────────┬───────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│                  NEXT.JS 14 (App Router)                         │
│   /              landing — redirects signed-in users by role     │
│   /dashboard     manager workspace                               │
│   /platform      founder console (operators only)                │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 Why two databases

This is a deliberate split, not an accident:

- **SQLite** holds high-frequency pipeline telemetry. It is local, fast, and
  requires no network round-trip per frame.
- **Supabase/Postgres** holds accounts, organisations, memberships and audit
  history, with Row-Level Security enforcing tenant isolation.

The trade-off: dashboard analytics currently read SQLite, so they are **per
installation**, not per organisation. Unifying them means building a
minute-bucket aggregator that writes `zone_minute_stats` — see §8.

---

## 3. Roles

Three application roles plus one platform role. Roles are **per organisation** —
the same person can be ADMIN of one org and VIEWER of another.

| Role | Scope | Can do | Cannot do |
|------|-------|--------|-----------|
| **ADMIN** | one org | Everything below, plus: invite/remove members, change roles, edit org settings, retention policy, delete org | Touch another org |
| **MANAGER** | one org | Cameras, zones, run analysis, view analytics, export reports, manage alert rules | Manage members or billing |
| **VIEWER** | one org | Read dashboards, view reports | Change any configuration |
| **PLATFORM OPERATOR** | whole platform | Org metadata, health, support triage, suspend/restore orgs, audit log | **Read occupancy data — RLS returns zero rows** |

### 3.1 The operator boundary (important)

A platform operator can administer an organisation but **cannot read its
measurements**. This is enforced in Postgres, not in the UI:

```sql
-- zone_minute_stats policy
USING ("orgId" IN (SELECT user_org_ids()))
```

`user_org_ids()` is **membership**-based, not admin-based. So an operator with
full console access still gets zero rows from occupancy tables. A support
engineer can fix your cameras without ever seeing who sat where.

✅ **Built** — verified: RLS enabled on all 16 tables, `is_platform_admin()` is
`SECURITY DEFINER` with pinned `search_path`, reading `platform_admins` with
`revokedAt IS NULL` so revocation is immediate.

---

## 4. Authentication

### 4.1 Session mechanics ✅ Built

- **Cookie-based** via `@supabase/ssr` (not localStorage) — so middleware,
  Server Components and Route Handlers can all read the session.
- **PKCE OAuth**: Google → `/auth/callback` (server route) → code exchanged
  server-side → httpOnly cookies set. A client-side exchange cannot write
  httpOnly cookies, so middleware would never see the session.
- **`prompt=select_account`** forces the Google chooser instead of silently
  reusing the signed-in account.
- **Sign-out** is a **server route** (`/auth/signout`): only the server that set
  httpOnly cookies can delete them. It revokes globally (`scope: 'global'`) then
  expires every `sb-*` cookie. `/auth/*` bypasses middleware so the token
  refresh cannot race the deletion.
- **`getUser()` everywhere, never `getSession()`** — `getSession()` trusts the
  cached token; `getUser()` validates against the auth server, so a revoked
  session correctly reads as signed-out.

### 4.2 Three-layer authorisation ✅ Built

```
Layer 1  middleware.js        fast redirect + session refresh
Layer 2  platform/layout.jsx  authoritative is_platform_admin()
Layer 3  Postgres RLS         returns nothing regardless of app bugs
```

Middleware is **not** the security boundary — it can be bypassed by a direct
fetch to a route handler. It exists to make redirects fast. Layer 3 is the real
enforcement.

### 4.3 Routing by role ✅ Built

```
/  (landing)
 ├─ signed out ──────────────▶ marketing page
 ├─ operator ────────────────▶ redirect /platform
 └─ member ──────────────────▶ redirect /dashboard
```

The landing page renders **nothing** about the viewer — no email, no role. It
resolves the session server-side and redirects. Putting identity in public
markup would leak who is signed in to anything that sees the response.

---

## 5. The product flow

### 5.1 Current state (honest)

```
signup ──▶ profile row created by DB trigger ──▶ /dashboard
                                                     │
                                          ⚠️ no organisation
```

**Verified from the live database:** 3 profiles, **0 memberships**, 2 demo orgs.
Every user has `currentOrgId = NULL` and `onboardedAt = NULL`.

So today the dashboard works, but it is **org-unaware** — it reads the local
SQLite pipeline, not a tenant. This is the gap the rest of this section closes.

### 5.2 Target flow ⬜ Designed

```
                    ┌──────────────┐
   signup / OAuth ─▶│  DB trigger  │  creates profiles row (✅ built)
                    └──────┬───────┘
                           │
              ┌────────────▼─────────────┐
              │ pending invite for this  │
              │ email address?           │
              └───┬──────────────────┬───┘
                  │ YES              │ NO
                  ▼                  ▼
      ┌───────────────────┐   ┌──────────────────┐
      │ ✅ AUTO-ACCEPTED  │   │  ✅ /onboarding  │
      │ membership ACTIVE │   │  create org      │
      │ currentOrgId set  │   │  → ADMIN of it   │
      │ onboardedAt set   │   └────────┬─────────┘
      └─────────┬─────────┘            │
                └──────────┬───────────┘
                           ▼
                      /dashboard
```

**The invited path is already fully automated in Postgres.** The trigger
`handle_new_auth_user()` looks for an unexpired `INVITED` membership matching the
email, flips it to `ACTIVE`, sets `currentOrgId` and `onboardedAt`, and writes an
audit row. Accepting an invite *is* signing up with that address — no token to
paste, and it works identically for OAuth, which never touches the signup form.

**The uninvited path is now built too.** `/onboarding` is reserved in
`middleware.js` `PROTECTED_PREFIXES` but the route does not exist.

### 5.3 What `/onboarding` does ✅ Built

A guard, then a three-step wizard:

**Guard** — in `dashboard/layout.jsx`:
```
profile.currentOrgId == null  →  redirect('/onboarding')
```
and the inverse in `/onboarding`, so an onboarded user cannot re-run it.

**Step 1 — Create the organisation**
- Name → slug (auto-generated, uniqueness checked)
- Timezone (IANA; defaults from the browser) — because "peak hour" must mean
  peak *local* hour
- On submit, inside **one transaction**: insert `organisations`, insert
  `memberships` (role `ADMIN`, status `ACTIVE`), set `profiles.currentOrgId` +
  `onboardedAt`, write `audit_logs`. Partial success here would strand a user
  with an org they are not a member of.

**Step 2 — Add a site**
- Name, capacity, working hours, working days → `sites`
- Working hours matter: utilisation outside them is not underuse.

**Step 3 — Add a camera + draw a zone**
- Camera: name, source (UPLOAD / RTSP), site
- Then hand off to the existing zone editor

Every step skippable except step 1. A manager who wants to look around first
should not be trapped in a wizard.

### 5.4 Inviting a team ✅ Built

The `memberships` table already models this completely: `invitedEmail`
(lower-cased at write), `inviteTokenHash` (**hashed, never raw** — a leaked table
must not grant access), `inviteExpiresAt`, and `@@unique([orgId, invitedEmail])`
preventing duplicate outstanding invites.

`/settings/members` is built. Because the DB trigger handles acceptance, the
flow is:

```
ADMIN enters email + role
   → INSERT membership (status INVITED, 7-day expiry, SHA-256 token hash)
   → admin copies a one-time link and sends it       ⬜ no email provider
   → invitee signs up with that address
   → trigger activates them automatically            ✅ verified end to end
```

Only the **automated delivery** step is outstanding — there is no email provider
in the project. The invite works without it, because acceptance matches on the
email address rather than the token. See §11 Step 3 for the measurements.

---

## 6. What each role sees

### 6.1 Manager workspace — `/dashboard` ✅ Built

Sidebar shell matching the founder console. Four sections:

| Section | Contents |
|---------|----------|
| **Overview** | 6 KPI tiles (people, activity index, zones active, busiest zone, longest sitting, last detection), posture mix bar, trend charts |
| **Live feed** | Camera/upload player with detection + posture overlay, occupancy heatmap |
| **Zones** | Polygon zone editor — draw the areas occupancy is attributed to |
| **Reports** | CSV (raw telemetry) and PDF (executive summary) export |

Design notes worth defending:
- **Only one tile can turn red** (longest sitting ≥ 45 min). Colour that appears
  everywhere means nothing.
- **People counted as `DISTINCT track_id`**, not row count — `activity_logs`
  holds a sampled series per person, so `COUNT(*)` would report a wildly
  inflated headcount.
- **Chart palette validated, not chosen by eye.** Light `#B91C1C · #D97706 ·
  #9A3412` passes all six checks; dark `#D13A3A · #B8821A · #2F8FB8` passes with
  one CVD warning, which is why **every posture segment is direct-labelled** —
  identity never rests on colour alone.
- Sections are **view state, not routes**, because they share a polling source
  and a live WebSocket that routing would tear down on every click.

### 6.2 Founder console — `/platform` ✅ Built

| Page | Purpose |
|------|---------|
| Overview | Platform totals, signups chart, attention queue |
| Organisations | All orgs, filterable; detail page per org |
| Health | Cross-org triage ordered by urgency |
| Operators | Grant/revoke operator access |
| Audit log | Every operator action, filterable |

Every page is `force-dynamic` with a skeleton `loading.jsx`, because each
navigation runs real queries.

---

## 7. The CV pipeline

### 7.1 Posture classification ✅ Built

Multi-feature geometric scoring, not a trained classifier:

| Feature | Signal | Needs |
|---------|--------|-------|
| A | Knee angle (hip→knee→ankle) | ankles |
| B | Hip angle (shoulder→hip→knee) | knees |
| C | Thigh vertical projection | knees |
| D | Body span vs box height | ankles |
| **E1** | **Torso–thigh fold angle** | **hips + knees only** |
| **E2** | **Thigh drop, torso-normalised** | **hips + knees only** |
| E3 | Aspect ratio (last resort) | nothing |

**E1/E2 exist because of a real bug:** the old fallback fired whenever *ankles*
were missing and scored on bounding-box aspect ratio. A person standing at a
desk produces a wide, short box → scored as SITTING, with every other feature
contributing zero. Standing was effectively unclassifiable when legs were out of
frame.

E1 asks "is the torso in line with the thigh" (standing ≈ 165–180°, sitting ≈
70–120°) and E2 asks "does the knee sit below the hip" — both need only
shoulder/hip/knee. Verified: at aspect ratio **1.12**, where the old rule forced
SITTING, standing now returns STANDING while a genuinely seated person in the
same crop still returns SITTING.

Output is smoothed by a **7-frame rolling majority vote** so labels do not
flicker frame to frame.

> ⚠️ **No confusion matrix exists, and none is claimed.** The models are stock
> COCO-pretrained YOLOv8 weights used for inference only — there is no training
> or validation pipeline and no labelled dataset. Posture classification is
> rule-based geometry, not a learned classifier. Reporting accuracy metrics would
> mean reporting numbers that were never computed. Cite YOLOv8's published COCO
> benchmarks for detection, and state the posture rules as rules.

### 7.2 Privacy ✅ Built

- Head-region Gaussian blur applied **before JPEG encoding**, so an unblurred
  frame never leaves the server. Verified: head-region variance drops 5383 → 13.8.
- Runtime toggle, **default ON**.
- Frames never written to disk; uploads deleted after processing.
- Only numeric telemetry persisted: counts, zone ids, posture labels, timestamps,
  normalised floor coordinates.

### 7.3 Telemetry write path ✅ Built

Sampled, not per-frame — at 60 FPS, per-frame writes would be thousands of rows
per minute:

- One row per track per **5 seconds**, **plus** an immediate row on any **posture
  change** (a sitting→standing transition is the most meaningful event the system
  observes; sampling could miss it entirely).
- Writes run via `asyncio.to_thread()` — SQLAlchemy is synchronous and would
  otherwise stall the event loop and stutter the video.
- All DB failures are caught and logged. Telemetry is a side effect; it must
  never kill a live session.

---

## 8. Known gaps

Listed plainly, because a design doc that hides them is not useful.

| Gap | Impact | Effort |
|-----|--------|--------|
| ~~⬜ `/onboarding` missing~~ | ✅ **Done.** New users create an org and get an ACTIVE ADMIN membership | — |
| ~~⬜ Member invite UI~~ | ✅ **Done.** `/settings/members`; copy-link delivery (no email provider configured) | — |
| ~~⬜ Dashboard not org-scoped~~ | ✅ **Done.** Telemetry carries `org_id`; every endpoint derives the tenant from a verified token | — |
| ~~🟡 `zone_minute_stats` unused~~ | ✅ **Done.** Written (Step 5) and read by the dashboard (Step 6) |
| 🟡 Homography uncalibrated | Falls back to proportional mapping — spatially faithful, not true perspective correction (needs 4 surveyed point pairs) | Small |
| ~~⬜ Alerts~~ | ✅ **Done.** Engine evaluates 4 rule types on the 60s tick, debounced (§11 Step 7) |

### 8.1 Recommended order

1. ~~**`/onboarding` + org guard**~~ ✅ **Done** — see §11 Step 1
2. ~~**Org-scope the dashboard**~~ ✅ **Done** — see §11 Step 2. Note the tenant
   is derived from the caller's verified token, not passed in by the client
3. ~~**Member invites UI**~~ ✅ **Done** — see §11 Step 3
4. ~~**Role enforcement**~~ ✅ **Done** — see §11 Step 4
5. ~~**Minute-bucket aggregator**~~ ✅ **Done** — see §11 Step 5

---

## 9. Data model reference

**Supabase/Postgres** (16 tables, RLS on all):

```
organisations ──┬── memberships ──── profiles ──── platform_admins
                ├── sites ──── cameras ──── zones
                ├── zone_minute_stats · zone_day_stats
                ├── analysis_sessions
                ├── alert_rules ──── alerts
                └── reports · audit_logs · platform_audit_logs
```

**SQLite** (pipeline output):

```
cameras · zones · activity_logs(timestamp, camera_id, zone_id, track_id,
                                posture_state, activity_score,
                                dwell_duration_seconds, floor_x, floor_y)
```

Floor coordinates are stored **normalised 0–1**, so the heatmap is independent of
camera resolution and render size.

---

## 10. Security summary

| Control | Status |
|---------|--------|
| RLS on every table | ✅ 16/16 |
| httpOnly cookie sessions | ✅ |
| Server-side sign-out | ✅ |
| JWT validated (`getUser`) | ✅ |
| Three-layer authorisation | ✅ |
| Operators blocked from measurements | ✅ RLS-enforced |
| Open-redirect guards on `next=` | ✅ same-origin paths only |
| Upload path traversal guard | ✅ basename + charset filter |
| Invite tokens hashed | ✅ schema; ⬜ no issuer yet |
| Secrets git-ignored | ✅ `.env` never committed |

---

*Marks in this document reflect verified state as of 22 August 2026. Anything
marked ⬜ or 🟡 should be described as planned, not delivered.*

---

# 11. Completion roadmap — do these in order

Eleven steps from here to a complete product. They are **strictly sequential**:
each one unblocks the next, and doing them out of order means building on
something that does not exist yet.

Each step lists what to build, **how to verify it**, and what it unblocks. A step
is not done until its verification passes — "it compiles" is not verification.

---

## THE BLOCKER — why nothing shows tenant data today

Read this before starting. It explains why Step 1 is not optional.

Every RLS policy on customer data resolves through this function:

```sql
CREATE FUNCTION public.user_org_ids() RETURNS SETOF uuid AS $$
  SELECT "orgId" FROM public.memberships
  WHERE "profileId" = (SELECT auth.uid())
    AND status = 'ACTIVE';
$$;
```

**You have zero memberships.** So `user_org_ids()` returns an empty set, and
every policy of the form `USING ("orgId" IN (SELECT user_org_ids()))` correctly
returns **nothing** — to everyone, including you.

That is not a bug. The database is behaving exactly as designed. But it means:

- 824,312 rows in `zone_minute_stats` are invisible to every user
- 24 zones, 5 cameras, 3 sites are invisible
- No amount of frontend work will reveal them

**Creating one membership row is what switches the entire multi-tenant half of
the product on.** That is Step 1.

---

## Step 1 — Onboarding & organisation creation ✅ Built

**Why first:** unblocks every tenant-scoped feature. Nothing downstream works
without a membership row.

### 1a. Database — no migration needed ✅
The schema is ready. `organisations`, `memberships`, `profiles.currentOrgId`,
`profiles.onboardedAt` all exist with correct constraints.

### 1b. Server actions — `app/onboarding/actions.js` ✅

The four writes are atomic, but **not** by hand-rolling a transaction in the
action. They are `create_organisation()`, the SECURITY DEFINER function already
in `prisma/sql/002_auth_triggers.sql`:

```
BEGIN                                   -- one function, one transaction
  INSERT organisations (name, slug, timezone)
  INSERT sites         (first site)
  INSERT memberships   (role='ADMIN', status='ACTIVE', acceptedAt=now())
  UPDATE profiles      SET currentOrgId, onboardedAt
  INSERT audit_logs    ('organisation.created')
COMMIT
```

**Why the function and not the action.** A Server Action calling supabase-js
issues each write as a separate PostgREST request — there is no enclosing
transaction, so "use a transaction, not four sequential calls" is not something
the action *can* do. Worse, `organisations` deliberately has **no INSERT
policy** (`003_rls_policies.sql`), so a browser-scoped client cannot create an
org at all. The definer function is the only door, and it is the door that makes
partial success impossible.

The action layer is therefore thin: validate, call the RPC, translate Postgres
errors into sentences, `revalidatePath('/dashboard', 'layout')`.

Slug: lower-case, hyphenated, de-duplicated with a numeric suffix — measured as
`acme-facilities` then `acme-facilities-1` (the function counts from 1, not 2).

Two details the schema forces on steps 2 and 3:

- **Step 2 UPDATEs the site, it does not INSERT one.** `create_organisation()`
  already made the first site inside its transaction; inserting here would
  either orphan an empty site or collide with `sites_orgId_name_key`.
- **`updatedAt` must be written by hand.** It is a Prisma `@updatedAt` field,
  which Prisma maintains *in the client* — the column has no DB default and no
  trigger. Writing through PostgREST bypasses Prisma, so an insert that omits it
  fails NOT NULL.

### 1c. Route — `app/onboarding/page.jsx` + `OnboardingWizard.jsx` ✅
Server page runs the inverse guard, then a three-step client wizard:

1. **Organisation** — name, timezone (default from `Intl.DateTimeFormat()`)
2. **Site** — name, capacity, working hours/days *(skippable)*
3. **Camera** — name, source type *(skippable)*

Reuses the auth-screen aesthetic (`AuthAside`, `Field`, `SubmitButton`) but
**not** `.auth-screen` itself: that class is `height:100svh; overflow:hidden`,
sized for a five-field form, and step 2's seven weekday toggles would be clipped
with the submit button unreachable. The wizard shell is `min-height` and scrolls.

Step 1 has no Back button once it commits — the transaction is done and the org
genuinely exists, so offering "back" would imply it could be un-created.

### 1d. Guard — `app/dashboard/layout.jsx` ✅
```
profile.currentOrgId == null  →  redirect('/onboarding')
```
And the inverse inside `/onboarding`. The two conditions are exact complements,
so there is no redirect loop. The guard is a layout, not middleware: middleware
runs on the whole matcher and would need a DB read per request; this runs once
per dashboard navigation and `redirect()` throws, so it cannot be fallen
through. Every entry into `/dashboard` — OAuth callback, login, signup,
middleware, the `/platform` non-operator bounce — passes through it.

### ✅ Verified against the live database
Run as the real user `adithyasn2487@gmail.com` under `SET LOCAL ROLE
authenticated`, inside a rolled-back transaction so no state was written:

- `create_organisation('Acme Facilities','Asia/Kolkata','HQ — Level 4')` →
  returns `(org_id, site_id, membership_id)`
- membership: `ADMIN` / `ACTIVE` / `acceptedAt` set — **1 row**
- profile: `currentOrgId` pointed at the new org, `onboardedAt` set
- audit: one `organisation.created` row
- slug collision: second "Acme Facilities" → `acme-facilities-1`
- site UPDATE and camera INSERT both accepted under RLS as ADMIN
  - ⚠️ **Corrected later.** This was measured with hand-written SQL supplying
    `gen_random_uuid()`. The *action's* camera insert went through PostgREST and
    omitted `id`, which had no DB default — so `createCamera()` failed in
    practice until `008_uuid_defaults.sql`. Re-verified through the real client
    afterwards. See §11 Step 3, "The bug the first pass missed".
- **the RLS unlock, measured:** a profile with **0** memberships sees
  `count(*) FROM zone_minute_stats = 0`; granting **one** ACTIVE membership in
  `demo-northgate` makes the same query return **549,374**, plus 24 zones and
  5 cameras. Isolation holds — Meridian's 274,938 buckets stay invisible.

### 🔎 Two defects found by adversarial re-testing, and fixed

The first pass "worked" and built cleanly. Attacking it with forged form
values found two real bugs. Both are recorded because both are the kind that
survive a happy-path demo and fail in production.

**1. Silent success on a filtered UPDATE.** An UPDATE whose rows are all
removed by RLS is *not* an error — it is a successful statement matching zero
rows. Measured: updating another tenant's site returns `UPDATE 0` with
`error === null`. `updateSite()` treated that as success and told the user
"Saved" while writing nothing. Nothing leaked, but the interface lied.
*Fix:* the UPDATE now carries `.select('id')` and an empty result is a failure.
Re-measured: forged siteId → 0 rows → error shown; own siteId → 1 row → saves.

**2. Cross-tenant camera attachment.** `camera_insert` checks `orgId` and
nothing else, and `cameras_siteId_fkey` points at `sites(id)` without requiring
the site's org to match. Measured: an INSERT carrying *my* orgId and *another
tenant's* siteId **was accepted**. It leaks nothing outward — `site_select`
still hides that site — but it writes a row whose parent I cannot see,
corrupting later joins and attaching my camera to someone else's building.
A per-table policy structurally cannot ask a cross-column question, so this is
not an RLS failure; it is a check the application owes.
*Fix:* `createCamera()` no longer accepts `orgId` from the client at all — it
reads `currentOrgId` from the caller's profile — and resolves `siteId` through
an RLS-filtered lookup scoped to that org, attaching nothing rather than
attaching wrongly. Re-measured: cross-tenant camera rows = 0.

> The general lesson, worth stating once: **RLS guarantees isolation, not
> integrity.** It answers "may this row be seen or written by this caller",
> never "is this row internally consistent". Every write joining two
> org-scoped ids needs an application check on top.

> ⚠️ Note on the original acceptance criterion. "Create org → 824k rows become
> visible" is **not** what happens, and claiming it would fail a viva. A newly
> created organisation has no buckets — the 824,312 rows belong to the two
> seeded demo orgs. Creating an org proves the membership/RLS mechanism works
> and returns 0 rows *correctly*. Seeing real data requires either a membership
> in a demo org or Step 2 (org-scoping the pipeline) so new telemetry is written
> against the new tenant.

---

## Step 2 — Org-scope the CV pipeline ✅ Built

**Why now:** telemetry had no tenant. Until it did, every organisation would see
every other organisation's data the moment the dashboard read real numbers.

### 2a. Migration — `org_id` on three SQLite tables ✅
```sql
ALTER TABLE activity_logs ADD COLUMN org_id VARCHAR(64);
ALTER TABLE zones         ADD COLUMN org_id VARCHAR(64);
ALTER TABLE cameras       ADD COLUMN org_id VARCHAR(64);
```
Added to `apply_lightweight_migrations()`, which was generalised from a
single-table helper to a `{table: {column: type}}` map. Four indexes come with
it (`org_id`, and `(org_id, timestamp)` on `activity_logs`) — every org-scoped
query filters on `org_id` first, so without them the tenancy filter turns each
analytics call into a full table scan. Verified idempotent across three
consecutive runs.

### 2b. The tenant does NOT come from the client ⚠️ design change

The roadmap said "frontend sends `orgId` when opening a processing WebSocket"
and "endpoints take `org_id` and filter on it". **That was not implemented as
written, because it does not produce a boundary.**

This backend runs `allow_origins=["*"]` with no session of its own. A
self-asserted `?org_id=` is a URL anyone can edit, so any user could read any
tenant's telemetry by changing one query parameter. Worse, the roadmap's own
acceptance test — "query with a different org id → returns zero rows" — would
have **passed** while the system was wide open, because a client honestly
passing its own id sees only its own rows. A green tick over an open door.

What is built instead (`backend/app/api/deps.py`):

```
Authorization: Bearer <supabase access token>
   ──▶ auth.get_user(token)          verifies the JWT signature
   ──▶ auth user id                  cannot be forged
   ──▶ memberships WHERE profileId = uid AND status = 'ACTIVE'
   ──▶ org_id                        derived, never asserted
```

The membership lookup runs **as that user**, carrying their token, so Postgres
RLS applies to it. The backend holds the **anon** key, never `service_role` —
so even a bug here cannot read another tenant's rows. `currentOrgId` is
cross-checked against an ACTIVE membership rather than trusted alone: a
suspended member keeps a stale pointer, and honouring it would keep writing
their telemetry into an org they were removed from.

This is the same rule Step 1 arrived at the hard way — `createCamera()` reads
`currentOrgId` from the profile, not the form. **The value that reaches the
database is the one the server established.**

Everything else follows:
- `ActivityLogWriter(camera_id=..., org_id=...)` stamps every row
- `load_zones_for_camera(camera_id, org_id=...)` filters by tenant — without it,
  two orgs both using the default camera name `live_webcam` would inherit each
  other's polygons
- All seven analytics endpoints and all three zone endpoints scope on it
- Frontend sends the token via `app/lib/backend.js` (`backendFetch`,
  `backendSocketUrl`); browsers cannot set WebSocket headers, so the socket
  carries it in the query string

**Fail closed.** No token, expired, forged, or no membership all resolve to
`None`, and `None` returns nothing. Reads return empty; exports return 401.

### ✅ Verified against a running server with two real tenants

Two real Supabase users were created, each given an org through the actual
`create_organisation()` function, then given 5 and 9 telemetry rows. All test
data has since been removed and the database restored (70 legacy rows,
3 profiles, 2 demo orgs, 0 memberships).

| Case | Result |
|------|--------|
| No token → `/summary` | **0** |
| Forged / garbage token → `/summary` | **0** |
| User A → `/summary` | **5** ✓ |
| User B → `/summary` | **9** ✓ |
| **A passing B's `?org_id=`** | **5** — parameter ignored entirely ✓ |
| A `/zones` vs B `/zones` | `['ws_alpha']` vs `['ws_beta']` ✓ |
| No token → `/heatmap`, `/historical`, `/zones` | empty ✓ |
| No token → CSV / PDF export | **401** ✓ |
| A's CSV export | 6 lines = header + A's 5 rows ✓ |
| Zone write with no token | **401** ✓ |
| B reusing A's `zone_id` | **409**, A's zone untouched ✓ |
| B deleting A's zone | **404**, A's zone survives ✓ |
| Write path: A / B / anon | stamped `orgA` / `orgB` / `NULL` ✓ |
| After writes: 101 rows in DB | A sees 6, B sees 10, anon sees **0** ✓ |

### 🔎 A concurrency bug found by testing, and fixed

The first implementation resolved the org with `client.postgrest.auth(token)`
on a shared Supabase client. **The auth token is process-global state on that
object**, so two threads resolving different users interleave their writes to
it — and the three WebSocket handlers reach this path through
`asyncio.to_thread()`, so concurrent sessions hit it directly.

Measured: 16 concurrent threads across two users produced **one** distinct org
id instead of two, plus `permission denied for table profiles` errors. One
caller's identity was being used for another caller's query. The
restore-to-anon in the `finally` block made it worse, clearing a token another
thread was still using.

*Fix:* the membership lookup is now a plain PostgREST request carrying the
token as an ordinary `Authorization` header — identity stays on the request,
no mutable state is shared. Re-measured: 16 threads → **2** distinct orgs, each
correct, no errors.

> Sequential testing would never have found this. It only appears when two
> tenants are in flight at once, which is the normal case in production and the
> rare case in a demo.

### Consequences worth stating plainly

- **The dashboard reads zero until a video is processed while signed in.** The
  70 pre-existing rows keep `org_id = NULL` and belong to nobody, so they are
  invisible to every tenant. That is the honest state, not a regression —
  backfilling them would invent an ownership claim the data never had.
- **`zone_id` is still a global PRIMARY KEY**, so two orgs cannot both use
  `workstation_01`. The collision now returns a clear **409** instead of an
  opaque 500. Namespacing ids per org would be cleaner but would detach every
  historical `activity_logs` row from its zone, so it is deliberately deferred.
- **`SUPABASE_URL`/`SUPABASE_KEY` must be set in `backend/.env`** or the backend
  cannot verify tokens and every org-scoped read returns empty. It logs a
  warning at startup rather than failing, so the pipeline still runs standalone.

---

## Step 3 — Member invitations ✅ Built

**Why now:** the org exists and has an ADMIN who can invite. The database half
already worked — the trigger activates invitees automatically on signup.

### 3a. One migration WAS needed — `008_uuid_defaults.sql` ⚠️
`memberships` already had `invitedEmail`, `inviteTokenHash`, `inviteExpiresAt`,
`invitedById`, and `@@unique([orgId, invitedEmail])`, so the roadmap was right
that the *invite* schema was complete.

But a re-audit found the first implementation **could not insert a single row**.
See "The bug the first pass missed" below.

More importantly, **every rule 3c asks for was already enforced in Postgres**,
which was verified by attacking it rather than assumed:

| Rule | Enforced by | Measured |
|------|-------------|----------|
| Only ADMIN may invite | `membership_insert` WITH CHECK | MANAGER insert → RLS violation |
| Only ADMIN may change roles | `membership_update` | MANAGER self-promote → 0 rows |
| Cannot fabricate an ACTIVE member | `membership_insert … status='INVITED'` | direct ACTIVE insert → rejected |
| No duplicate invites | `@@unique([orgId, invitedEmail])` | second invite → unique violation |
| Last-admin rule | `memberships_keep_an_admin` trigger | demote/suspend/delete last admin → all rejected |
| Cross-org anything | `admin_org_ids()` | B inviting into A's org → rejected; A's roster invisible to B |

So no new SQL was written. The server actions are thin wrappers that produce a
readable message and fail fast; the database remains the boundary.

### 3b. `/settings/members` ✅
Roster (member · role · status · joined date), invite form, and per-row actions:
resend, withdraw invite, change role, suspend/restore, remove.

**A non-admin sees the roster but no controls.** That mirrors the database
exactly — `membership_select` returns the roster to *every* member of the org,
while writes require `admin_org_ids()`. Redirecting a manager away would hide
their own colleague list; showing them buttons that always fail would be worse.
This satisfies "MANAGER cannot see the invite form" without pretending the page
does not exist.

Reachable from the dashboard sidebar footer — a real route, placed below the
view-switching nav, because that nav shares a polling source and a WebSocket
that routing would tear down.

### 3c. Server actions ✅

**Invite:** 32 random bytes → store **SHA-256 hash only** → `INVITED` row with a
7-day expiry. Verified: hash is 64 chars (the column is exactly `VarChar(64)`),
and the raw token appears **nowhere** in the table. It is returned to the
inviting admin once and never persisted.

**⚠️ Delivery is a copy-link, not an email.** This project has no email provider
installed or configured (no Resend/SendGrid/nodemailer, no SMTP vars), so
"send email" could not be implemented and verified. The admin is shown a
one-time link to send however they like. The invite is **fully functional
without it** — acceptance matches on the email address, not the token.

**What the token is actually for.** Acceptance does **not** require it:
`handle_new_auth_user()` matches on EMAIL, which is what makes OAuth invitees
work — they never touch a signup form and have no field to paste a token into.
The token makes the link unguessable and gives a future token-verifying route
something to check against.

### ✅ Verified against the live database

Two passes. The first used `SET LOCAL ROLE authenticated` with hand-written
SQL — good for proving the policies, **insufficient for proving the product**
(see the bug below). The second drove a real signed-in `supabase-js` client
through the actual action code. Database confirmed unchanged afterwards:
3 profiles, 2 demo orgs, 0 memberships, 0 audit rows.

- Invite → row appears with status **`INVITED`**, `profileId` null ✓
- Token: 43-char raw / **64-char hash**; raw token stored nowhere ✓
- Sign up with that address → **trigger flips it to `ACTIVE`** automatically,
  `profileId` set, `acceptedAt` set, **`inviteTokenHash` burned to NULL** ✓
- `currentOrgId` pointed and `onboardedAt` set → new user **skips onboarding**
  and lands on `/dashboard` ✓
- `audit_logs` contains **`member.invite_accepted`** ✓
- MANAGER sees the roster (2 rows) but **cannot invite or self-promote** ✓
- ADMIN with a second admin present **can** demote themselves (1 row) ✓
- ADMIN suspending a member: allowed; the suspended member's `user_org_ids()`
  drops to **0**, and orgs/sites/cameras all read **0** ✓

> One measurement needed a second look. A suspended member initially appeared to
> still see sites and cameras. Investigating rather than accepting it: that
> account is a **platform operator**, and what was visible was the operator
> boundary (`zone_minute_stats` stayed at **0** — operators administer but never
> read measurements). Re-run with a non-operator: every count drops to zero on
> suspension. The apparent leak was the test subject, not the policy.

### 🔎 The bug the first pass missed — and how it got missed

The first Step 3 pass built cleanly, and every guard test passed. **It was still
completely broken**, and the reason is worth recording because the flawed
verification looked rigorous.

```
supabase.from('memberships').insert({ orgId, role, status, ... })
  -> ERROR: null value in column "id" of relation "memberships"
            violates not-null constraint
```

**Cause.** Every `id` is `@default(uuid())` in Prisma. That default is generated
by the **Prisma client, in JavaScript** — it is not a Postgres column default,
so `prisma migrate` created these columns as bare `uuid NOT NULL`. Invisible
while every write goes through Prisma; fatal the moment one goes through
PostgREST, which is what every Supabase Server Action does.

**Blast radius — three real paths, across two steps:**

| Path | Step | State |
|------|------|-------|
| `inviteMember()` → `memberships` | 3 | **every invite failed** |
| `writeAudit()` → `audit_logs` | 3 | **every audit row failed** |
| `createCamera()` → `cameras` | **1** | **every camera insert failed** |

**How the earlier verification missed it.** Step 1 and Step 3 were both
"verified against the live database" — but with hand-written SQL that supplied
`gen_random_uuid()` explicitly. That proves the *policies* allow the write. It
does not exercise **the code the application actually runs**. The two are
different programs, and only one of them ships.

**Fix:** `008_uuid_defaults.sql` sets `DEFAULT gen_random_uuid()` on all 13
generated-id tables. Safe alongside Prisma — a column DEFAULT only applies when
the INSERT omits the column, and Prisma keeps supplying its own. `profiles` and
`platform_admins` are deliberately excluded: their ids are the `auth.users`
uuid, and generating one would create a row matching no auth user.

**Re-verified through the real code paths**, using a genuine signed-in
`supabase-js` client rather than SQL:

- invite insert → **OK**, id auto-generated ✓
- audit insert → **OK** ✓
- camera insert (Step 1 path) → **OK** ✓
- roster embed `profiles:profileId (...)` resolves for both an ACTIVE member and
  a pending INVITED row ✓
- resend → 1 row · changeRole → 1 row · suspend → 1 row · remove → 1 row ·
  revokeInvite → 1 row ✓
- accept: `ACTIVE`, `profileId` set, token burned, `currentOrgId` pointed,
  `onboardedAt` set ✓
- audit trail: `organisation.created` → `member.invited` →
  `member.invite_accepted` ✓

> **The lesson, stated once.** RLS tests and application tests are not the same
> test. "The database permits this write" and "the code performs this write"
> are separate claims, and only the second one is the product. Every future
> verification in this project should drive the real client.

### Notes carried forward

- **`member.invite_resent` / `member.invite_revoked` / `member.suspended` /
  `member.restored` / `member.removed` / `member.role_changed`** are written by
  the actions. `member.role_changed` records **from and to** — a log with only
  the new value cannot answer "what was it before".
- **Silent-failure guard applied throughout.** Every UPDATE and DELETE carries
  `.select('id')` and treats an empty result as a failure — the Step 1 bug where
  an RLS-filtered UPDATE returns no error and no rows, and the UI reports
  success. Measured again here: a MANAGER's self-promotion returns 0 rows with
  no error.
- **Expired invitations are surfaced, not hidden.** Nothing sweeps them, so an
  expired `INVITED` row still exists; the UI labels it "Invite expired" rather
  than "Invited", because the latter is a lie an admin would act on.
- **`/settings` added to `PROTECTED_PREFIXES`** in `middleware.js`.
- **Signup prefills `?email=`** from the invitation link (wrapped in Suspense,
  as `useSearchParams` requires). Left editable — a forwarded link may need
  correcting, and a mismatch simply matches no invite rather than breaking signup.

### Still open

| Gap | Why it is not done |
|-----|--------------------|
| ⬜ Automated invite email | No email provider in the project; adding one needs an account, a verified domain and a secret, and could not be tested |
| ⬜ Token-based acceptance route | Not required — email matching already accepts invites, including for OAuth |
| ⬜ Expired-invite cleanup job | Expired rows are labelled in the UI; nothing deletes them |

---

## Step 4 — Role enforcement ✅ Built

**Why now:** three roles existed and were meaningless until something enforced
them.

### 4a. `lib/permissions.js` — the single source of truth ✅

```js
can(role, 'zones.edit')      // ADMIN, MANAGER
can(role, 'members.invite')  // ADMIN
can(role, 'reports.export')  // ADMIN, MANAGER, VIEWER
can(role, 'org.settings')    // ADMIN
```

12 capabilities. **Fails closed on both axes** — an unknown role AND an unknown
capability both return `false`. The second matters more than it looks: if a
typo returned `true`, a misspelled capability would silently grant everyone
everything and still look like working code. Denying makes a typo visible as a
missing button rather than invisible as a hole.

`denialMessage()` names the role that *would* be able to act — "Only an
administrator or manager can do that" — because "you do not have permission"
leaves the reader with nowhere to go.

**There is a second copy: `backend/app/api/permissions.py`.** Zone writes and
analysis sockets go to FastAPI, not to a Next Server Action, so gating only the
JS side would leave `POST /api/v1/zones/` open to any authenticated member.
The two tables are **verified identical** — 12 capabilities each, no key
differences, no role differences.

### 4b. Applied in all three places ✅

| Layer | Where | What it does |
|-------|-------|--------------|
| 1. UI | dashboard, ZoneEditor, VideoCanvasPlayer, MembersScreen | hides controls a role cannot use |
| 2. Server | members actions · `require_zone_edit` · `_resolve_session_org` | re-checks; 403 with a sentence |
| 3. RLS | `manage_org_ids()` / `admin_org_ids()` | the boundary that actually holds |

Layer 1 is courtesy. Layer 2 exists because a Server Action is a POST endpoint
the browser can call directly — hiding a button removes it from the screen, not
from the network. **Layer 3 is the one that cannot be bypassed**, and it was
measured independently: a VIEWER inserting a zone is rejected with
`violates row-level security policy for table zones`, while MANAGER and ADMIN
succeed.

`analysis.run` is gated at ADMIN + MANAGER because **running the pipeline
WRITES telemetry** — a VIEWER watching a live feed would be creating data. That
makes it a configuration act, not a read.

### ✅ Verified — three real users, one per role, against a live server

| Check | Result |
|-------|--------|
| **VIEWER sees dashboards, no edit controls** | `GET /zones/` **200** with data; analytics **200**; editor renders "View only" ✓ |
| **VIEWER calling zone-save directly → rejected** | **403** `"Only an administrator or manager can do that."` ✓ |
| VIEWER `DELETE /zones/{id}` | **403** ✓ |
| MANAGER / ADMIN `POST /zones/` | **201** ✓ |
| No token | **401** (not 403 — "sign in" ≠ "ask an admin") ✓ |
| VIEWER live_webcam socket | **refused**, close **1008** + reason ✓ |
| MANAGER live_webcam socket | accepted, frames flowing ✓ |
| **Telemetry attribution** | MANAGER's session wrote **1 row**, correctly attributed; VIEWER's refused session wrote **0** ✓ |
| Capability matrix | matches the roadmap exactly; JS and Python tables identical ✓ |

### 🔎 One bug found and fixed during verification

The VIEWER socket refusal *worked* but closed with code **1006** (abnormal) and
no reason — `send_json()` immediately followed by `close()` races, so the frame
could be dropped and the client learned only that something went wrong.

Fixed by closing with **1008** (policy violation) and the reason in the
handshake, so the client learns *why* even if the JSON frame loses the race.
Re-measured: VIEWER now receives both the error JSON and
`close 1008 "Only an administrator or manager can do that."`

### ⚠️ Deviation from the roadmap's third check

The roadmap says **"MANAGER cannot reach `/settings/members`"**. That
contradicts Step 3, where any member may *view* the roster — because
`membership_select` returns it to every member of the org, and hiding a
colleague list from a manager would hide information the database willingly
returns.

**Resolved in favour of Step 3** (confirmed with the user): a MANAGER reaches
the page and sees the roster **read-only** — no invite form, no row actions.
The UI mirrors `membership_select` (all members read) against
`admin_org_ids()` (admins write) exactly, rather than inventing a stricter rule
the database does not have. Writes remain blocked at all three layers.

---

## Step 5 — Minute-bucket aggregator ✅ Built

**Why now:** the bridge from raw telemetry to the analytics schema.

`backend/app/db/minute_aggregator.py` folds per-sample `activity_logs` into
per-zone-per-minute buckets.

### Anonymity is structural — proven, not asserted ✅

The written row holds **no track id, no coordinates, no person reference**.
Track ids are used inside the fold — to count distinct people and measure each
one's presence span — and are discarded before anything is written.

Verified by trying to query them back:

```
SELECT track_id  FROM zone_minute_stats -> no such column: track_id
SELECT floor_x   FROM zone_minute_stats -> no such column: floor_x
SELECT floor_y   FROM zone_minute_stats -> no such column: floor_y
```

`activity_logs` *does* hold all three — which is exactly why buckets exist. The
aggregation is a **one-way projection**: counts survive, identity does not. The
database physically cannot answer "what did this person do today".

`uniqueTrackCount` is a count, not an id. It distinguishes "one person for an
hour" from "sixty people for a minute each", which `occupancyAvg` alone cannot.

### The three things that were easy to get wrong

1. **Dwell is cumulative per track.** `dwell_duration_seconds` runs 0, 5, 10,
   15… so summing the column multiplies the same seconds — the trap
   `analytics.py` already documents. `totalDwellSeconds` is instead each track's
   **presence span within the minute**, clamped by
   `zms_dwell_within_minute` to `60 × max(occupancyMax, 1)`.
2. **`sampleFrames` counts SAMPLES, not frames.** The writer samples every ~5s,
   so a bucket holds ~12 per person, not 480. Ratios are correct; absolute
   counts are not comparable with the 8fps seeded rows — which is precisely
   what the schema comment already warns about, and why every consumer divides
   by `sampleFrames`.
3. **AWAY counts toward `sampleFrames` but no posture column.** That is why
   `zms_posture_frames_within_sample` is `<=` and not `=`, and why the
   roadmap's check reads **≈**.

### ✅ Verified with a deterministic dataset

20 samples in one minute — track 1 SITTING ×12 plus AWAY ×1, track 2 STANDING
×6, track 3 WALKING ×1:

| Column | Value | Why |
|---|---|---|
| `bucketStart` | `10:05:00` | second=0, microsecond=0 — matches `date_trunc('minute')` ✓ |
| `occupancyMax/Avg/Min` | 3 / 1.58 / 1 | min ≤ avg ≤ max ✓ |
| sitting/standing/walking | 12 / 6 / 1 | = **19** |
| `sampleFrames` | **20** | 19 + 1 AWAY — `19 ≤ 20` ✓ |
| `totalDwellSeconds` | 80 | 55 + 25 + 0 ≤ 60×3 ✓ |
| `uniqueTrackCount` | 3 | a count, not ids ✓ |

Plus:
- **Idempotent** — 4 consecutive runs, still exactly 1 row (`UNIQUE(zone_id, bucket_start)`)
- **Bucketing** — 18 samples over 3 minutes × 2 zones → exactly **6** buckets
- **Settle window** — a sample 10 min old is aggregated; one 5 s old is
  **excluded**, because that minute can still receive samples
- **Background timer** — seeded rows and waited: the running server's 60 s tick
  wrote the bucket **unattended**, confirming the lifespan task is live

### Run triggers ✅
- 60 s timer, started from the FastAPI `lifespan` (cancelled on shutdown so
  reload does not stack orphans)
- **and** directly after each upload session, which sleeps out the settle
  window first so a short video's numbers appear promptly

### ✅ The Postgres leg — verified end to end

`zone_minute_stats` has **no INSERT policy**: measured data is deliberately
read-only to every browser client, so only the service-role key can write it.
That key is now in `backend/.env` (documented in `.env.example`, backend-only,
never `NEXT_PUBLIC_`).

Verified against the live database with a real org, a real camera and a real
zone, then fully cleaned up:

| Check | Result |
|---|---|
| `service_role_configured()` | **True**; the key is a genuine `service_role` JWT for this project |
| Service role reads the table | **824,312** rows visible (the anon key sees 0 without membership — RLS working) |
| 60 samples → aggregate → sync | **3 buckets, 0 unmapped** ✓ |
| Foreign keys | `cameraId` and `zoneId` match the created UUIDs exactly ✓ |
| All 8 CHECK constraints | posture ≤ sample · occ ordered · dwell cap · minute-truncated — all pass ✓ |
| **Idempotency** | 3 consecutive aggregate+sync rounds → **3** Postgres rows, not 9 ✓ |
| **Unmapped path** | telemetry for a zone with no Postgres match → `unmapped: 1`, held locally unsynced, **0 leaked** ✓ |
| **Timer, unattended** | fresh samples were aggregated **and synced** by the running server's 60 s tick with no manual trigger ✓ |
| **Member reads them** | a real member saw exactly their own **3** buckets through RLS; the 824,312 demo rows stayed invisible ✓ |
| Anonymity in Postgres | **no** `trackId` / `floorX` / `floorY` / `personId` column exists ✓ |

That last row is the whole chain proven in one measurement: pipeline →
telemetry → aggregation → Postgres → tenant-scoped read.

**Id mapping.** SQLite carries text ids (`browser_camera`, `workstation_01`);
Postgres wants UUIDs with FKs. The sync matches on `name` within the row's org.
Rows that do not resolve are **counted as `unmapped` and left unsynced** — never
invented. Auto-creating a camera or zone would fabricate configuration the user
never drew, and a bucket with the wrong `zoneId` is worse than no bucket. Draw
the zone later and the next sync picks them up — measured: the unmapped bucket
stayed local and retryable.

### 🔎 One bug found during verification

`aggregate_window_sync()` raised `TypeError: can't compare offset-naive and
offset-aware datetimes` when a caller passed a naive datetime. Fixed by
normalising naive inputs to UTC — the pipeline writes UTC throughout, so that is
the correct reading, and this function must not blow up on an unremarkable
argument.

---

## Step 6 — Switch the dashboard to Postgres ✅ Built

The historical panels now read `zone_minute_stats` through Supabase. **The 824k
demo rows are real dashboard content**: three months of history instead of an
empty table.

### Tenancy is not written in app code ✅
No `WHERE orgId = ?` appears anywhere in `app/lib/analytics/queries.js`. Every
query runs through the caller's own session, so `zms_select` scopes it in the
database. The SQL functions are deliberately **not** `SECURITY DEFINER` — a
definer function would run with owner rights and hand every tenant's occupancy
to any caller.

### What stayed on the Python backend, and why
The **live video overlay** and the **floorplan heatmap**. Both need per-sample
`floor_x`/`floor_y`, which `zone_minute_stats` deliberately lacks — that absence
is what makes a closed minute anonymous. Each source answers only what it can.

### ✅ Verified
| Check | Result |
|---|---|
| Data for the signed-in user's org only | Northgate **457,429** people / 16 zones / 549,374 buckets |
| A second org sees entirely different numbers | Meridian **228,622** / 8 zones / 274,938 buckets — and 549,374 + 274,938 = **824,312** ✓ |
| Date range across the full span | 7d / 30d / 90d all return, percentages sum to 100 ✓ |
| Coverage | 20 May → 17 Aug detected; ranges the data cannot fill are disabled with the reason in the tooltip |

### 🔎 Two bugs found by measurement

**1. Silent truncation — the serious one.** The first implementation fetched
buckets and folded them in JavaScript:

```
.limit(50000) on a 30-day window -> 1,000 rows returned
rows that actually matched        -> 146,359
```

PostgREST caps responses (`max-rows`) **after** filtering, with no error. So
"last 30 days" was computed from the oldest 1,000 minutes of it and rendered as
fact — 3,198 people instead of 457,539. A confidently wrong number is worse
than a failure.
*Fix:* aggregation moved into Postgres (`009_dashboard_analytics.sql`).

**2. Statement timeout at 90 days.** The first SQL version scanned the window
twice — once for totals, once for the peak zone. Measured: totals 366 ms, peak
1.6 s, combined function **7.5 s**, past Supabase's timeout, so a 90-day range
failed outright.
*Fix:* one `per_zone` CTE aggregated once and rolled up. 90 days now returns
1,596,934 people.

**3. Two components were missing their token.** `AnalyticsCharts` and
`FloorplanHeatmap` still used bare `fetch()` with a hardcoded backend URL —
missed in Step 2, so after the backend became tenant-aware they were reading
**nothing**. Both now go through the token-attaching helpers.

---

## Step 7 — Alerts engine ✅ Built

`backend/app/db/alerts_engine.py`, evaluated on the aggregator's 60-second tick
— where the buckets are produced, not when someone opens the dashboard. An
overnight sedentary condition must fire with nobody watching.

Four rule types: **SEDENTARY**, **OVERCROWDING**, **UNDERUTILISATION**,
**CAMERA_OFFLINE**. `ZONE_EMPTY` is deliberately **not** evaluated — it needs
booking data the system does not collect, and a rule that silently never fires
is worse than one openly unimplemented.

### The debounce is the whole problem ✅
A two-hour condition spans 120 buckets. Three mechanisms, all from columns the
schema already had, keep it to one alert:

- `sustainedMinutes` — must hold this long before firing at all
- `cooldownMinutes` — silent for this long after firing
- **OPEN-state check** — a rule with an alert still open for that zone does not
  fire again; acknowledging is what re-arms it

### ✅ Verified
| Check | Result |
|---|---|
| **Seed sustained sitting → one alert, not fifty** | 50 buckets of sitting, engine run **5 times** → `fired: 1`, then `suppressed: 1` every time. **Exactly 1 row.** ✓ |
| Message quality | "Alert Test Desk has been predominantly seated for 44 minutes (91% of samples). Threshold is 30 minutes." ✓ |
| **Acknowledging persists across reload** | state `ACKNOWLEDGED`, `acknowledgedById` recorded; re-read after reload confirms ✓ |
| Double-acknowledge | 0 rows — correctly refused ✓ |

### 🔎 A policy gap found and fixed — `010_alert_update_role.sql`

`alert_update` used `user_org_ids()`, which covers **all three roles**. Measured:
a VIEWER's UPDATE on an OPEN alert returned **1 row updated**.

That is not what a VIEWER is, and acknowledging has a real effect — it re-arms
the rule. The app already refused it, but the app was the *only* thing standing
there, contradicting the layering every other table has.

*Fix:* the policy now uses `manage_org_ids()` (ADMIN + MANAGER). Re-measured:
VIEWER **0 rows**, MANAGER **1 row**. Reading is deliberately left open to all
roles — knowing the space is overcrowded is not a privileged fact.

---

## Step 8 — Organisation settings ✅ Built

`/settings/organisation`. Name, timezone, `dataRetentionDays`,
`purgeVideoAfterProcessing`, the two zone defaults, and a danger zone that
soft-deletes via `deletedAt`.

### The retention field is not a dropdown ✅
Shortening retention destroys data the next time the nightly job runs, so the
form **counts** rather than warning. Before saving a shortened value it asks the
server how many buckets fall outside the new window and makes the reader confirm
that number. Measured against a real org: shortening 90 → 30 days reports
**370,566 buckets** would be destroyed; → 7 days reports **507,041**.

"This will delete 370,566 minutes of history" is a decision someone can make.
"Shortening destroys data" is a sentence people click past.

### ✅ Verified
| Check | Result |
|---|---|
| **Changing retention writes `audit_logs`** | `organisation.retention_changed` with `{from: 90, to: 45, shortened: true}` ✓ |
| **MANAGER gets refused server-side, not just a hidden link** | `UPDATE organisations` → **0 rows** at the RLS layer ✓ |
| VIEWER cannot change settings | **0 rows** ✓ |
| MANAGER/VIEWER *can* read settings | mirrors `org_select` (all members) vs `org_update` (admins) ✓ |
| ADMIN can save | 1 row ✓ |

Deletion requires typing the organisation's name — a confirm dialog is dismissed
by reflex; typing the name is not.

---

## Step 9 — Retention job ✅ Built

### The logic already existed
`purge_expired_minute_stats()` was written in `004_secrets_and_retention.sql`:
per-org (not global), reading each organisation's own `dataRetentionDays`,
writing a `retention.purged` audit row per org that lost rows, `SECURITY
DEFINER` with EXECUTE revoked from both PUBLIC and `authenticated`. Only the
**schedule** was missing.

### `011_retention_schedule.sql` ✅
`pg_cron` enabled and three jobs scheduled — verified active:

| Job | Schedule | Why |
|---|---|---|
| `visionworks-day-rollup` | 02:45 UTC | Day rollups must be built **before** the minute rows they summarise are deleted, or a 90-day policy silently loses the year-over-year trend |
| `visionworks-retention` | 03:15 UTC | The purge |
| `visionworks-expire-reports` | 03:30 UTC | Expire generated export files |

Not scheduled from the Python backend deliberately: a privacy guarantee that
only holds while a process happens to be running is a weak one.

### ✅ Verified
Set retention to 1 day on a test org, ran the function:
- 5 buckets aged 10/5/3/2/0 days → **4 deleted, 1 kept** ✓
- audit row: `{deletedRows: 4, retentionDays: 1}` ✓
- other orgs' bucket counts **unchanged** ✓

### 🔎 A seed bug this exposed

The first run also deleted **185,389** of Meridian's rows — and the function was
right to. The seed generates `DAYS = 90` of buckets for **both** demo orgs but
set Meridian's `dataRetentionDays` to **30**, so two thirds of its own demo data
was outside the policy it declared. A correct nightly job looked like data loss.

*Fix:* the seed now sets 90 for both. A differing retention value is still
useful for demonstrating that retention is per-org, but it has to be at least
the span of data the seed writes, or the demo destroys itself overnight.

---

## Step 10 — Homography calibration 🟡

**Why late:** improves accuracy but blocks nothing.

Today `project_to_floor()` falls back to proportional frame mapping — spatially
faithful, but not true perspective correction. Add a UI to click **4 camera
points ↔ 4 floorplan points**, store the matrix in `zones.homographyMatrix`
(column exists), and pass it to `SpatialEngine`.

### ✅ Verify
- Two people at different depths map to correctly separated floorplan positions
- Uncalibrated cameras still work via the fallback

---

## Step 11 — Documentation & polish 🟡 Partial

### ✅ README updated
- Real clone URL (`github.com/adithyasn11/majorproject.git`)
- **`SUPABASE_SERVICE_ROLE_KEY`** documented, with why it exists
  (`zone_minute_stats` and `alerts` have no INSERT policy) and the warning that
  it bypasses every RLS policy
- **`DATABASE_URL` / `DIRECT_URL`** rows, with the 6543-vs-5432 distinction
  spelled out — they are not interchangeable
- A **"Using the app"** section: the onboarding-first flow, the note that the
  dashboard reads zero until a video is processed under an org, the role matrix,
  the copy-link invite flow, and how retention works

### ✅ Security review re-run on the final diff
| Check | Result |
|---|---|
| Service-role key referenced in frontend | **0** occurrences |
| Hardcoded JWTs in hand-written code | **0** files |
| `.env` / `.env.local` git-ignored | ✓ |
| Dynamic SQL in new functions | **none** (the four `EXECUTE` hits were `GRANT EXECUTE`) |
| `SECURITY DEFINER` on dashboard functions | **none** — they run as the caller so RLS applies |
| `WHERE orgId` in app code | **none** — the three hits were explanatory comments |
| RLS gaps across all tables | **0** |
| Write policies scoped by membership only (weak) | **none** on zones, cameras, sites, alerts, organisations, memberships |

### ⬜ Not done — the demo recording
Recording signup → onboarding → zone → video → dashboard → PDF needs the app
driven by hand. That is yours to do; everything it would show is verified below.

---

## Full-system test — 47 checks

Run against the live database with real users, real tokens and real RLS.

| Area | Result |
|---|---|
| **Step 1** Onboarding | **9/9** — trigger creates profile, new user has no org, atomic org creation, ADMIN/ACTIVE membership, audit row, site UPDATE returns rows, camera INSERT works |
| **Step 2** Org-scoped pipeline | **6/6** — token→org, token→role, forged token→none, no token→none, writer stamps `org_id`, legacy rows stay NULL |
| **Step 3** Invitations | **7/7** — invite created, 64-char hash, raw token stored nowhere, signup auto-activates, invitee skips onboarding, audit written, duplicate blocked |
| **Step 4** Role enforcement | **7/7** — ADMIN/MANAGER write zones, VIEWER blocked, VIEWER reads, MANAGER cannot invite, MANAGER/VIEWER cannot change org settings |
| **Step 5** Aggregator | **6/6** — 3 buckets, posture ≤ sample, minute-truncated, **no person columns**, idempotent, synced to Postgres |
| **Step 6** Dashboard | **11/11** — overview returns, org-scoped, percentages sum 100, coverage scoped, ranges 1/7/30/90d, trend, zones |
| **Step 7** Alerts | **8/8** — rule created, **0→1 alert across 5 runs**, embeds resolve, VIEWER reads, VIEWER cannot acknowledge, MANAGER can, acknowledgement persists with actor |
| **Step 8** Org settings | **4/4** — MANAGER reads, ADMIN saves, audit with from/to, impact preview counts |
| **Step 9** Retention | **4/4** — per-org purge, other orgs untouched, audit written, 3 cron jobs active |
| **Backend integrity** | modules import, 12 capabilities, 4 alert types, fail-closed on bad roles and unknown capabilities |

### 🔎 One real bug the test found

**A 90-day dashboard range timed out on a cold cache.** Measured: 8.4s cold
(827,510 buffer reads on a freshly-seeded table), 1.4s warm. That produces an
error for exactly the first user and a working page for everyone after — the
worst kind to diagnose.

Two fixes, because either alone is incomplete:
- `012_dashboard_covering_index.sql` — an `INCLUDE` index makes the scan
  **Index Only**, verified in the plan, so the heap is never touched
- a **retry on statement-timeout only** in the query layer, because the
  cancelled first attempt still warms the cache, so the retry is the fast path

### Two "failures" that were test bugs, not product bugs
Recorded because dismissing a red result without proving why is how real bugs
survive:
- *"retention impact preview returned null"* — the count works and returned
  **0** correctly; that org had already been purged and had only today's data.
  Re-verified on an org with history: **370,566**.
- *"other orgs affected by purge"* — the purge was correct; the **seed** was
  wrong (see Step 9).

---

