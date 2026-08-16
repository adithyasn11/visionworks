# VisionWorks — Workspace Utilisation Analytics

**Product & Technical Documentation**
Final-year major project · Version 1.0 · August 2026

---

## Table of contents

1. [What this product is](#1-what-this-product-is)
2. [The problem and who has it](#2-the-problem-and-who-has-it)
3. [What the system does — and deliberately does not do](#3-what-the-system-does--and-deliberately-does-not-do)
4. [How it works end to end](#4-how-it-works-end-to-end)
5. [Technology stack](#5-technology-stack)
6. [System architecture](#6-system-architecture)
7. [The computer vision pipeline](#7-the-computer-vision-pipeline)
8. [Data model](#8-data-model)
9. [Roles and permissions](#9-roles-and-permissions)
10. [Pages — every screen and what it does](#10-pages--every-screen-and-what-it-does)
11. [Feature specifications](#11-feature-specifications)
12. [API reference](#12-api-reference)
13. [Privacy, ethics and compliance](#13-privacy-ethics-and-compliance)
14. [Build plan](#14-build-plan)
15. [Current status](#15-current-status)
16. [Future work](#16-future-work)
17. [Running the project](#17-running-the-project)

---

## 1. What this product is

VisionWorks turns CCTV cameras a company **already owns** into a workspace analytics sensor.

It watches physical space and answers three questions that cost real money:

- **Which desks and rooms are actually used, and when?**
- **Where does congestion build up?**
- **Are people sitting for unhealthy stretches?**

It answers them **without storing video and without identifying anyone**. Frames enter memory, get converted to numbers, and are discarded. What persists is anonymous, aggregated counts per zone per minute.

**One-line positioning:** *Understand how your workspace is used — from the cameras you already have, without recording anyone.*

---

## 2. The problem and who has it

### The buyer

**Facility and workplace managers** at companies with 50–500 desks. After salaries, office real estate is typically the largest line item, and most of these managers are deciding whether to renew a lease, shrink a floor, or reconfigure a layout — using guesswork.

### Why existing options fail

| Approach | Why it doesn't work |
|---|---|
| **Badge/door swipes** | Tells you someone entered the building, not whether they used a desk |
| **Desk booking software** | Records intent, not reality — people book and don't show up |
| **Under-desk IoT sensors** | £40–80 per desk, plus batteries and installation across hundreds of desks |
| **Wearables / apps** | Employees refuse; unions object; it tracks individuals |
| **Manual clipboard audits** | A snapshot of one afternoon, expensive to repeat |
| **Traditional CCTV** | Passive. Footage is only reviewed reactively after incidents |

### The gap VisionWorks fills

Cameras are already installed and already powered. The missing piece is software that converts what they see into utilisation data **without creating a surveillance system** — because a system that identifies individuals is unsaleable in an office with a works council, GDPR obligations, or ordinary employee goodwill.

**The privacy guarantee is the product**, not a feature bolted on.

---

## 3. What the system does — and deliberately does not do

### It does

- Detect people in a video frame and track them across frames within a session
- Classify posture as **sitting**, **standing**, or **walking** from body geometry
- Determine which user-defined **zone** each person occupies
- Aggregate occupancy, dwell time, posture balance and an activity index per zone per minute
- Raise alerts for prolonged sitting and zone overcrowding
- Export CSV and PDF summaries

### It explicitly does not

| Not this | Why |
|---|---|
| **No facial recognition** | No face embeddings are computed or stored. Ever. |
| **No identity linking** | Track IDs are session-scoped integers, reset per run. Track `#7` today has no relationship to `#7` tomorrow. |
| **No video retention** | Frames live in RAM during inference and are overwritten. Nothing is written to disk. |
| **No productivity scoring** | The system measures *physical states*, not work quality. Reading, thinking and whiteboarding all look like "sitting". A high activity score is not a good employee; a low one is not a bad one. |
| **No keystroke or screen monitoring** | Input is ambient video only. |
| **No individual dashboards** | There is no screen anywhere that shows one person's day. Aggregation is enforced by the schema, not by UI choice. |

> **Design principle:** the database physically cannot answer "what did Priya do today", because no row in it refers to a person. Anonymity is structural, not a policy promise.

---

## 4. How it works end to end

```
┌──────────────────────────────────────────────────────────────────┐
│  1. INPUT                                                         │
│     Uploaded video file  ·  RTSP camera stream  ·  Webcam        │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. DETECTION + TRACKING          (single GPU pass, ~60 FPS)      │
│     YOLOv8m-pose → person boxes + 17 COCO keypoints              │
│     ByteTrack    → a persistent track_id per person               │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. POSTURE CLASSIFICATION                                        │
│     Knee & hip angles, thigh projection, body span, motion speed  │
│     7-frame majority vote → SITTING | STANDING | WALKING          │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. SPATIAL RESOLUTION                                            │
│     Person centroid tested against zone polygons (Shapely)        │
│     → workstation_01 | workstation_02 | … | TRANSIT_ZONE         │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  5. AGGREGATION            ← the privacy boundary                 │
│     Per-person data accumulates in memory for 60 seconds,         │
│     then collapses into ONE anonymous row per zone per minute.    │
│     Individual tracks are discarded and never persisted.          │
└──────────────┬──────────────────────────────┬────────────────────┘
               ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────────────┐
│  6a. LIVE (transient)    │   │  6b. STORED (permanent)          │
│  Annotated frames over   │   │  ZoneMinuteStat rows in Postgres │
│  WebSocket → dashboard   │   │  → charts, reports, alerts       │
│  Nothing saved.          │   │  ~500 rows/camera/day            │
└──────────────────────────┘   └──────────────────────────────────┘
```

### Why minute-buckets matter

Storing one row per person per frame would mean **~20 million rows per camera per day** at 60 FPS. That is unqueryable, expensive, and — critically — a dense movement trail from which an individual could be re-identified.

Minute-bucket aggregation produces **~500 rows per camera per day**, makes every dashboard query a fast indexed range scan, and destroys individual-level detail by construction.

---

## 5. Technology stack

All versions below are the ones actually installed and verified in this project.

### Frontend

| Technology | Version | Role |
|---|---|---|
| **Next.js** | 14.2 (App Router) | React framework, routing, SSR |
| **React** | 18.2 | UI library |
| **Tailwind CSS** | 3.4 | Styling, design tokens, theming |
| **Recharts** | 2.12 | Analytics charts |
| **lucide-react** | 0.359 | Icon set |
| **heatmap.js** | 2.0 | Floorplan density overlay |
| **@supabase/supabase-js** | 2.110 | Auth client and session handling |
| **HTML5 Canvas** | — | Live HUD overlay and zone-drawing editor |

### Backend

| Technology | Version | Role |
|---|---|---|
| **Python** | 3.11.9 | Runtime |
| **FastAPI** | 0.139 | REST API and WebSocket server |
| **Uvicorn** | 0.28+ | ASGI server |
| **Ultralytics YOLOv8** | 8.1+ | Detection, tracking and pose in one pass |
| **PyTorch** | 2.5.1+cu121 | Inference engine (CUDA 12.1) |
| **OpenCV** | 4.9+ | Video decode, resize, JPEG encode |
| **Shapely** | 2.0+ | Point-in-polygon zone tests |
| **ReportLab** | 4.1+ | PDF report generation |
| **pandas** | 2.2+ | CSV export and aggregation |

### Data & infrastructure

| Technology | Role |
|---|---|
| **PostgreSQL** (via Supabase) | Primary database |
| **Prisma** | ORM, schema source of truth, migrations |
| **Supabase Auth** | Email/password + Google OAuth, JWT sessions |
| **Row Level Security** | Database-enforced tenant isolation |

### Hardware (verified)

- **NVIDIA RTX 4060 Laptop GPU**, CUDA available, ~60 FPS at 640px inference
- CPU fallback works automatically when no CUDA device is present

---

## 6. System architecture

```
┌─────────────────────────────────────────────────────────────┐
│  BROWSER                                                     │
│  Next.js app · Canvas HUD · Recharts · Supabase auth client  │
└──────┬──────────────────────────┬───────────────────────────┘
       │ HTTPS (CRUD via Prisma)  │ WebSocket (live frames)
       ▼                          ▼
┌──────────────────────┐  ┌──────────────────────────────────┐
│  NEXT.JS SERVER      │  │  FASTAPI  :8001                  │
│  Route handlers      │  │  ┌────────────────────────────┐  │
│  Prisma Client       │  │  │ CV pipeline (RTX 4060)     │  │
│  Session validation  │  │  │ detect → track → pose      │  │
│  Role checks         │  │  │ → posture → zone → aggregate│ │
└──────────┬───────────┘  │  └────────────────────────────┘  │
           │              └──────────────┬───────────────────┘
           │                             │ writes ZoneMinuteStat
           ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│  SUPABASE (PostgreSQL)                                       │
│  auth.users · organisations · sites · cameras · zones        │
│  zone_minute_stats · alerts · reports · audit_logs           │
│  Row Level Security enforces org isolation                   │
└─────────────────────────────────────────────────────────────┘
```

### Why two backends

This is a deliberate split, and a defensible one:

- **Next.js** handles product CRUD — organisations, cameras, zones, users. Prisma gives type-safe queries and migrations, and running it server-side keeps database credentials off the browser.
- **FastAPI** handles computer vision. PyTorch, Ultralytics and OpenCV are Python-native; reimplementing them in Node would be pointless.

They share one PostgreSQL database. **Prisma owns the schema**; FastAPI writes aggregate rows into tables Prisma defines.

> **Migration note:** the project currently also contains SQLAlchemy models and a local SQLite file from early development. These are superseded by Prisma + Postgres and will be removed, so there is exactly one source of schema truth.

---

## 7. The computer vision pipeline

### 7.1 Detection and tracking

A single `YOLOv8m-pose` forward pass per frame returns bounding boxes, 17 COCO keypoints, and — via ByteTrack — a persistent `track_id`. Doing detection, tracking and pose in **one pass** rather than three is what makes ~60 FPS achievable.

- Class filter: person only (COCO class 0)
- Confidence threshold: 0.35
- Inference size: 480px; frames downscaled to 640px width

### 7.2 Posture classification

Posture is decided by weighted geometric evidence, not a black box — which matters for a viva, because every decision is explainable.

**Keypoints used:** shoulders (5, 6), hips (11, 12), knees (13, 14), ankles (15, 16)

**Features scored:**

| Feature | Evidence for sitting | Evidence for standing |
|---|---|---|
| Knee angle (hip→knee→ankle) | ≤ 138° (bent) | ≥ 155° (straight) |
| Hip angle (shoulder→hip→knee) | ≤ 128° (folded) | ≥ 148° (open) |
| Thigh vertical drop ÷ torso height | < 0.80 | — |
| Body span ÷ bounding-box height | < 0.55 | ≥ 0.70 with straight knees |
| Bounding-box aspect ratio *(fallback when legs occluded by a desk)* | < 1.35 | ≥ 1.65 |

**Decision rules:**

1. If `sitting_score > standing_score` → **SITTING**. This rule is absolute: someone rolling an office chair or gesturing while seated must never be labelled walking.
2. **WALKING** requires *all three*: upright posture, motion ≥ 4.5 px/frame, and a bipedal stride signature (normalised stride width, or knee asymmetry > 30°).
3. Otherwise → **STANDING**.

**Temporal smoothing:** a 7-frame rolling majority vote per track eliminates single-frame flicker.

### 7.3 Spatial engine

Zone polygons are stored as arrays of `[x, y]` points and compiled into Shapely polygons. Each person's centroid is tested for containment; anything outside all defined zones is `TRANSIT_ZONE`.

An optional 3×3 **homography matrix** maps the oblique camera view to a flat top-down floorplan, which is what drives the heatmap.

### 7.4 Activity index

A 0–100 index derived from **movement variance** and **posture-change frequency**:

```
motion_score = min(100, (std_dev(positions) / 15) × 100)
shift_bonus  = min(25, posture_changes × 8)
activity     = clamp(0.75 × motion_score + shift_bonus, 0, 100)
```

> **Interpretation warning, stated in the product itself:** this measures *physical movement*, not productivity. A focused developer at a desk scores low by design. It is used for ergonomic insight (sedentary detection), never for performance ranking.

### 7.5 Privacy anonymiser

Gaussian blur is applied over the head region (top 25% of each bounding box) before any frame is transmitted to a browser, with array-bounds clamping so it can never read outside the frame.

---

## 8. Data model

Managed by **Prisma**, hosted on **Supabase PostgreSQL**.

### Entity relationships

```
Organisation ─┬─ Membership ── User (Supabase auth.users)
              │     └── role: ADMIN | MANAGER | VIEWER
              ├─ Site ── Camera ── Zone
              │                     └── ZoneMinuteStat   ← analytics
              ├─ AnalysisSession
              ├─ AlertRule ── Alert
              ├─ Report
              └─ AuditLog
```

### Core tables

**Organisation** — the tenant boundary. Every other row belongs to exactly one.
`id, name, slug, dataRetentionDays (default 90), createdAt`

**Membership** — joins a Supabase user to an organisation with a role.
`id, orgId, userId, role, invitedEmail, acceptedAt`

**Site** — a building or floor. Lets a company compare locations.
`id, orgId, name, timezone`

**Camera** — a video source.
`id, orgId, siteId, name, sourceType (UPLOAD|RTSP|WEBCAM), rtspUrl, fpsTarget, status, lastSeenAt`

**Zone** — a polygon drawn on a camera's view.
`id, orgId, cameraId, name, zoneType (WORKSTATION|MEETING|BREAK|CORRIDOR), polygon (JSON), capacity, homography (JSON)`

**ZoneMinuteStat** — *the analytics table.* One anonymous row per zone per minute.

```
id, orgId, siteId, cameraId, zoneId
bucketStart          DateTime   -- truncated to the minute
occupancyMax         Int
occupancyAvg         Float
sittingCount         Int
standingCount        Int
walkingCount         Int
avgActivityScore     Float
totalDwellSeconds    Int

@@unique([zoneId, bucketStart])
@@index([orgId, bucketStart])
```

The unique constraint makes writes idempotent — reprocessing the same footage updates rather than duplicates. **No `trackId`, no coordinates, no person reference exists in this table.**

**AnalysisSession** — one upload or live run, so history survives a restart.
`id, orgId, cameraId, status (QUEUED|PROCESSING|DONE|ERROR|CANCELLED), sourceFilename, totalFrames, processedFrames, startedAt, finishedAt, errorMessage`

**AlertRule** / **Alert** — thresholds and the events they produce.
`AlertRule: id, orgId, type (SEDENTARY|OVERCROWDING), thresholdValue, zoneId?, isEnabled`
`Alert: id, orgId, ruleId, zoneId, triggeredAt, value, message, acknowledgedAt, acknowledgedBy`

**Report** — a generated export.
`id, orgId, requestedBy, format (CSV|PDF), rangeStart, rangeEnd, filePath, createdAt`

**AuditLog** — accountability for privacy-sensitive actions.
`id, orgId, actorId, action, targetType, targetId, metadata, createdAt`

### Enums

```
Role        ADMIN | MANAGER | VIEWER
SourceType  UPLOAD | RTSP | WEBCAM
ZoneType    WORKSTATION | MEETING | BREAK | CORRIDOR
Posture     SITTING | STANDING | WALKING
SessionStatus QUEUED | PROCESSING | DONE | ERROR | CANCELLED
AlertType   SEDENTARY | OVERCROWDING
```

### Retention

A scheduled job deletes `ZoneMinuteStat` rows older than the organisation's `dataRetentionDays` (default **90**). Uploaded video files are deleted immediately after processing, and orphaned uploads are purged after 6 hours.

---

## 9. Roles and permissions

| Capability | Admin | Manager | Viewer |
|---|:---:|:---:|:---:|
| View dashboards and analytics | ✓ | ✓ | ✓ |
| Download reports | ✓ | ✓ | ✓ |
| Acknowledge alerts | ✓ | ✓ | ✓ |
| Upload video / start analysis | ✓ | ✓ | |
| Draw and edit zones | ✓ | ✓ | |
| Add / edit / delete cameras | ✓ | ✓ | |
| Configure alert rules | ✓ | ✓ | |
| Invite and remove members | ✓ | | |
| Change roles | ✓ | | |
| Organisation settings, retention | ✓ | | |
| View audit log | ✓ | | |

**Enforced twice, deliberately:**

1. **Application layer** — Next.js route handlers check the caller's membership role before mutating. Governs UX.
2. **Database layer** — PostgreSQL Row Level Security policies scope every query to the caller's `orgId`. This is the *real* boundary: even a bug in the API cannot return another organisation's rows.

---

## 10. Pages — every screen and what it does

### Public

#### `/` — Landing page ✅ *built*
Product pitch. Hero, feature grid, privacy positioning, CTAs to sign up.
**Functions:** navigate to features/security/signup; theme toggle.

#### `/features` — Features ✅ *built*
What the platform does: zone mapping, dwell tracking, heatmaps, ergonomic safety. Stats band, 3-step "how it works", FAQ.

#### `/security` — Security & privacy ✅ *built*
The trust page. Edge processing, zero retention, encryption, row-level isolation. Answers the questions a works council or DPO will ask.

#### `/login` — Sign in ✅ *built*
Email/password and Google OAuth. Split layout: animated live-tracking floorplan on the left, form on the right.
**Functions:** validate input; authenticate via Supabase; friendly errors for wrong credentials and unconfirmed email; redirect to dashboard; link to signup.

#### `/signup` — Create account ✅ *built*
**Functions:** name/email/password with live password-strength rules; Google signup; email-confirmation state; duplicate-account detection.

### Authenticated

#### `/onboarding` — First-run setup 🔨 *to build*
Shown once, when a user has no organisation.
**Functions:** create organisation (name, timezone); create first site; optionally add first camera; assign creator as Admin. Blocks other app routes until complete.

#### `/dashboard` — Live operations ✅ *built, needs rework*
The real-time view.
**Functions:**
- Upload a video or start webcam/RTSP
- Live HUD: bounding boxes, track IDs, posture labels, activity scores over the frame
- Playback controls: play/pause, seek, ±5s skip, fullscreen
- Live counters: detected / sitting / standing / walking
- Zone occupancy panel
- Top-down heatmap
- **To fix:** currently ungated (needs auth guard) and charts show mock data

#### `/analytics` — Historical analysis 🔨 *to build*
Where the business questions get answered. Reads `ZoneMinuteStat`.
**Functions:**
- Date-range picker (today / 7d / 30d / custom)
- Occupancy over time, per zone
- Posture balance (sitting vs standing vs walking) as trend and share
- Peak hours heatmap (hour × weekday)
- **Zone comparison table** — utilisation %, peak occupancy, average dwell, ranked
- **Underused zone flag** — desks below a utilisation threshold, the core real-estate insight
- Export current view to CSV/PDF

#### `/cameras` — Camera management 🔨 *to build*
**Functions:** list cameras with status and last-seen; add camera (name, site, source type, RTSP URL); edit; delete with confirmation; test connection; link to zone editor.

#### `/cameras/[id]/zones` — Zone editor 🔨 *to build — highest-value missing feature*
Where a manager teaches the system what the space means.
**Functions:**
- Display a still frame from the camera as canvas backdrop
- **Click to place polygon vertices; double-click to close the shape**
- Name the zone, set type and capacity
- Drag vertices to adjust; delete zones
- Colour-coded overlay by zone type
- Save to the `Zone` table
- Optional: set 4 point-pairs to compute the homography matrix for top-down mapping

#### `/sessions` — Analysis history 🔨 *to build*
**Functions:** list past runs with status, source, duration, frames processed; live progress for running sessions; cancel a running session; view resulting analytics; error messages for failures.

#### `/alerts` — Alerts & rules 🔨 *to build*
**Functions:**
- Feed of triggered alerts, newest first, unacknowledged highlighted
- Acknowledge (records who and when)
- Configure rules:
  - **Sedentary** — zone occupied by seated posture > N minutes continuously
  - **Overcrowding** — zone occupancy > capacity for > N minutes
- Enable/disable per rule; scope to a zone or all zones

#### `/reports` — Reports & export 🔨 *to build*
**Functions:** choose range and zones; generate **CSV** (raw minute buckets via pandas) or **PDF** (executive summary via ReportLab — totals, posture ratios, utilisation table); download; list previously generated reports.

#### `/settings/team` — Team management 🔨 *to build* · Admin only
**Functions:** list members with role and join date; invite by email; change role; remove member; show pending invitations.

#### `/settings/organisation` — Org settings 🔨 *to build* · Admin only
**Functions:** rename organisation; set timezone; **set data retention days**; view audit log; danger zone (delete organisation).

#### `/settings/profile` — Personal settings 🔨 *to build*
**Functions:** display name; theme preference; change password; sign out.

---

## 11. Feature specifications

### F1 — Zone drawing *(priority: highest)*

The feature that converts a generic person-detector into a workspace analytics tool. Without it, zones are hardcoded and the product does not exist.

**Flow:** open camera → grab still frame → click vertices → close polygon → name it, pick type, set capacity → save → subsequent analysis reports against that zone.

**Acceptance:** a manager can define 4 zones on an uploaded video in under 2 minutes, and analytics immediately report against them.

### F2 — Real analytics *(priority: highest)*

Replace every mock chart with queries against `ZoneMinuteStat`.

**Acceptance:** after processing a video, charts show data derived from that video, and the numbers reconcile with what the HUD displayed.

### F3 — Alerts

Evaluated as minute-buckets are written. Sedentary: continuous seated occupancy beyond threshold. Overcrowding: occupancy above zone capacity beyond threshold.

**Acceptance:** processing footage of a person seated past the threshold produces exactly one alert, acknowledgeable, not re-fired while the same condition persists.

### F4 — Reports

CSV via pandas; PDF via the existing ReportLab generator, wired to a real endpoint.

**Acceptance:** a PDF for a chosen range downloads and contains totals, posture ratios and a per-zone utilisation table matching the dashboard.

### F5 — Team & roles

Invite by email, assign role, enforce in API and RLS.

**Acceptance:** a Viewer receives 403 attempting to edit a zone, and cannot see any other organisation's data even with a hand-crafted request.

---

## 12. API reference

### Existing (FastAPI, `:8001`)

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/cameras/` | List cameras |
| `POST` | `/api/v1/cameras/` | Register camera |
| `DELETE` | `/api/v1/cameras/{id}` | Remove camera |
| `GET` | `/api/v1/zones/{camera_id}` | Zones for a camera |
| `POST` | `/api/v1/zones/` | Create or update a zone |
| `GET` | `/api/v1/analytics/summary` | Instantaneous summary |
| `GET` | `/api/v1/analytics/historical` | Hourly aggregates |
| `POST` | `/api/v1/video/upload` | Upload video, returns `session_id` |
| `GET` | `/api/v1/video/samples` | List uploaded videos |
| `GET` | `/api/v1/video/status` | Active sessions |
| `WS` | `/api/v1/video/process/{session_id}` | Stream processed frames + telemetry |
| `WS` | `/api/v1/video/live_webcam` | Server-side webcam |
| `WS` | `/api/v1/video/process_webcam_frame` | Browser-camera frames |
| `WS` | `/api/v1/ws/stream/{camera_id}` | Live telemetry channel |

### To add

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/analytics/occupancy` | Minute-bucket series for a range |
| `GET` | `/api/analytics/zone-comparison` | Utilisation ranked by zone |
| `POST` | `/api/reports/generate` | Produce CSV/PDF |
| `GET` | `/api/alerts` | List alerts |
| `POST` | `/api/alerts/{id}/acknowledge` | Acknowledge |
| `GET/POST/PATCH` | `/api/alert-rules` | Manage rules |
| `GET/POST/PATCH/DELETE` | `/api/team` | Membership management |
| `GET/POST` | `/api/organisation` | Org settings |

**WebSocket frame payload** (existing):

```json
{
  "type": "FRAME",
  "frame_index": 412,
  "progress_pct": 34.2,
  "frame_base64": "data:image/jpeg;base64,…",
  "tracked_entities": [
    { "track_id": 7, "bbox": [120, 80, 210, 340],
      "posture": "SITTING", "activity_score": 62.4,
      "zone_id": "workstation_01", "dwell_duration_seconds": 428 }
  ],
  "zone_occupancy": { "workstation_01": 2, "workstation_02": 1, "TRANSIT_ZONE": 1 }
}
```

---

## 13. Privacy, ethics and compliance

### Technical guarantees

| Guarantee | How it is enforced |
|---|---|
| No footage stored | Frames exist only in RAM during inference; uploads deleted after processing; orphans purged after 6 hours |
| No biometric data | No face embeddings computed. Keypoints are geometric coordinates, discarded after posture classification |
| No individual histories | `ZoneMinuteStat` has no person reference. Individual detail is destroyed at the minute boundary |
| Session-scoped IDs | Track IDs are integers valid only within one run; they carry no identity and are never reused meaningfully |
| Faces obscured in transit | Gaussian blur over the head region before frames reach any browser |
| Tenant isolation | PostgreSQL RLS scopes every query by `orgId` at the database level |
| Encryption in transit | TLS 1.3 to Supabase; WSS in production |
| Accountability | Audit log records privacy-relevant configuration changes |

### GDPR alignment

- **Article 25 (Data protection by design)** — anonymisation happens *before* storage, not after
- **Article 5 (Data minimisation)** — only aggregate counts are retained
- **Anonymous vs pseudonymous** — because no key exists to re-link a `ZoneMinuteStat` row to a person, the stored data is arguably outside GDPR's definition of personal data entirely

### Ethical position, stated plainly

This system can tell you *a desk was occupied for 6 hours*. It cannot and must not tell you *who* sat there or *whether they worked well*.

The activity index measures physical movement. Deep focus looks identical to idleness on camera. **Any deployment that uses this data for individual performance management is a misuse of the product**, and the design actively prevents it: there is no individual record to misuse.

**Recommended deployment practice:** notify staff before enabling; publish which zones are monitored; avoid pointing cameras at break areas or anywhere with an expectation of privacy; involve works councils where applicable.

---

## 14. Build plan

Roughly 14 weeks, sequenced so a demoable product exists early.

### Phase 1 — Foundation (weeks 1–3)
- Prisma schema, migrations, seed data
- Retire SQLAlchemy/SQLite; single Postgres source of truth
- Auth guards on app routes; sign-out
- `/onboarding`; organisation and membership creation
- RLS policies for tenant isolation

### Phase 2 — Core product (weeks 4–8)
- **`/cameras/[id]/zones` zone editor** ← highest value
- `/cameras` management
- Minute-bucket aggregation writing from the CV pipeline
- `/analytics` with real queries replacing all mocks
- `/sessions` history and progress

### Phase 3 — Product completeness (weeks 9–12)
- Alerts engine and `/alerts`
- `/reports` wired to ReportLab and pandas
- `/settings/team`, `/settings/organisation`, `/settings/profile`
- Retention job

### Phase 4 — Polish and defence (weeks 13–14)
- Accuracy evaluation on annotated clips: posture confusion matrix, occupancy MAE
- Performance benchmarks: FPS vs resolution, GPU vs CPU
- Seed a realistic demo dataset
- Rehearse the demo; write the dissertation chapters

### Suggested demo narrative

1. Sign up → onboarding → create organisation
2. Upload an office clip
3. **Draw three zones on the first frame**
4. Process — HUD shows live tracking and posture
5. Open `/analytics` — charts populated from that footage
6. Show an underused desk flagged
7. Show a sedentary alert raised
8. Export the PDF report
9. Close on `/security` — explain why no video was ever stored

---

## 15. Current status

### Working and verified

- ✅ CV pipeline end to end on RTX 4060 — detection, tracking, 17-point pose, posture classification, zone containment, activity index
- ✅ Video upload → WebSocket → annotated live frames with playback controls
- ✅ Live webcam and browser-camera paths
- ✅ FastAPI REST endpoints for cameras, zones, analytics
- ✅ Landing, features, security pages
- ✅ Login and signup — Supabase email/password verified working against the live project
- ✅ Light/dark theming with animated transition; contrast verified to WCAG AA
- ✅ Privacy anonymiser, orphaned-upload purge, path-traversal hardening

### Known gaps

- ⚠️ Google OAuth provider not yet enabled in Supabase (code is ready; needs console configuration)
- ⚠️ Dashboard is not auth-gated; no sign-out control
- ⚠️ Analytics charts display mock data
- ⚠️ Zones are hardcoded — no drawing UI
- ⚠️ Three overlapping data layers (SQLAlchemy, raw Supabase SQL, Prisma) to consolidate
- ⚠️ Report generator exists but is not exposed via any endpoint

---

## 16. Future work

Named deliberately as *not in v1*, to show direction without over-promising:

- **Multi-site rollups** — compare buildings and floors in one view
- **Scheduled email reports** — weekly summary to facility managers
- **Slack / Teams integration** — push alerts into existing channels
- **Desk-booking integration** — compare booked vs actually occupied
- **Mobile companion app** — occupancy at a glance
- **Model improvements** — fine-tuning on office-specific footage; YOLOv11 migration
- **Multi-camera fusion** — one person tracked across overlapping views
- **Public API + webhooks** — customer-built integrations
- **Anomaly detection** — learned normal patterns, flag deviations

---

## 17. Running the project

### Prerequisites
Python 3.10+, Node.js 18+, optionally an NVIDIA GPU with CUDA, and a Supabase project.

### Setup

```bash
python setup.py          # venv, Python deps, npm install, .env files, YOLO weights
```

### Configure

`backend/.env`
```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<service-role-or-anon-key>
```

`frontend/.env.local`
```
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
DATABASE_URL=postgresql://…        # for Prisma
```

### Supabase configuration

1. **Authentication → URL Configuration** — Site URL `http://localhost:3000`; add redirect URLs `http://localhost:3000/dashboard` and `http://localhost:3000/login`
2. **Authentication → Providers → Email** — enabled (disable "Confirm email" for faster local testing)
3. **Authentication → Providers → Google** — enable and paste OAuth client ID/secret; authorised redirect URI is `https://<project>.supabase.co/auth/v1/callback`

### Run

```bash
python run_system.py     # FastAPI :8001 + Next.js :3000
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8001 |
| API docs (Swagger) | http://localhost:8001/docs |

---

*VisionWorks — measure the space, not the people.*
