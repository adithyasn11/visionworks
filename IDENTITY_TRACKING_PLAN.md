# VisionWorks — Per-Employee Identity Tracking: End-to-End Build Plan

**Author:** Adithya S Nayak (AD16)
**Goal:** Transform the existing anonymous pipeline into a system that identifies
named employees across multiple cameras and produces per-person productivity data.
**Status:** Plan. Nothing here is built yet.

---

## 0. Read this first — what "working" honestly means

Before the plan, the expectation-setting, so you build toward a target that exists.

| Capability | Realistic accuracy | Confidence |
|---|---|---|
| Desk-time per employee (fixed seating) | 85–95% | High |
| Chair-exit / break counting (one camera) | 80–90% | High |
| Re-attaching after leaving/returning to one camera | 80–90% | High |
| Identifying at a door camera by face | 90%+ | High |
| Cross-camera handoff (corridor → pantry) | 60–75% | Medium |
| A full 8-hour day with zero identity swaps | Rare | — |

**The single most important design rule in this whole document:**

> When the system is not confident, it must output `UNKNOWN` rather than guess.

A system that is right 92% of the time and abstains 15% of the time is worth far
more — in production *and* in your evaluation — than one that guesses on
everything and is right 70% of the time. Every stage below carries a confidence
score for exactly this reason.

---

## 1. What already exists (audited, not assumed)

These are the real files and the real integration points.

### The pipeline loop — where identity gets injected

`backend/app/api/routers/video_upload.py:500-541` is the per-frame loop. Today:

```
detections = pose_engine.process_frame_single_pass(...)   # line 498
for det in detections:                                    # line 502
    track_id = det["track_id"]                            # ← ByteTrack id, a FRAGMENT
    ...
    tracked_entities.append({ "track_id": track_id, ... }) # line 532
await persist_frame(activity_writer, tracked_entities)    # line 545
```

**Identity resolution goes between line 502 and line 532.** Nothing else in the
loop changes shape. This is the whole reason the plan is tractable — there is
exactly one place to add it, and it is a loop over detections that already has
the bbox, the zone, and the posture in hand.

The same loop appears three times (lines ~485, ~718, ~891) for upload, webcam,
and single-frame endpoints. Factoring it once and reusing it is Step 3.

### Assets you already have and will reuse

| Asset | File | Reused for |
|---|---|---|
| Zone polygons + containment | `cv/spatial_engine.py:44` | Seat assignment — the strongest signal |
| Homography projection | `cv/spatial_engine.py:74,99` | Height estimation; cross-camera geometry |
| `ground_point()` | `cv/spatial_engine.py:62` | Where a person stands on the floor |
| Sampled telemetry writer | `db/activity_writer.py:83` | Pattern to copy for identity events |
| Minute aggregation | `db/minute_aggregator.py` | Pattern to copy for daily rollups |
| Alert rules + debounce | `db/alerts_engine.py` | Reused unchanged |
| 16 Prisma models, 19 SQL migrations | `frontend/prisma/` | Migration 020 continues the sequence |
| Dashboard shell, charts, auth, RBAC | `frontend/app/dashboard/` | New page slots straight in |

### The three things that block per-employee tracking today

1. **ByteTrack ids are fragments, not people.** `cv/pose_estimator.py:59` uses
   `tracker="bytetrack.yaml"` — motion-only, no appearance memory. Occlusion for
   ~30 frames produces a **new id**. So "track 7 left the chair 5 times" is
   counting occlusions.
2. **No identity table exists.** No `Employee`, no way to say who anyone is.
3. **Aggregation deliberately destroys identity.** `db/minute_aggregator.py:20`
   discards track ids by design. Per-employee data needs a *parallel* path — the
   existing anonymous one stays untouched.

### Face blur — turn it off for development

`cv/anonymizer.py` blurs the top 25% of each bbox and is **on by default**
(`privacy_state = {"blur": True}`, `video_upload.py:527`). It will destroy the
face signal at the door camera. Step 1 makes it default-off and configurable.

---

## 2. The architecture in one picture

```
                         ┌──────────────────────────┐
   DOOR CAMERA  ────────►│  FACE MATCH (once/day)   │
   (1-2m, frontal)       │  "this is Prajwal"       │
                         └────────────┬─────────────┘
                                      │ captures today's signature
                                      ▼
              ┌────────────────────────────────────────────┐
              │      DAILY SIGNATURE REGISTRY (in RAM)     │
              │  prajwal → OSNet vec, HSV colours, height  │
              │  ravi    → OSNet vec, HSV colours, height  │
              └───┬──────────┬──────────┬──────────┬───────┘
                  │          │          │          │
              DESK CAM   CORRIDOR    PANTRY    MEETING
                  │          │          │          │
                  └──────────┴────┬─────┴──────────┘
                                  │  4-signal fusion + confidence
                                  ▼
                    ┌──────────────────────────┐
                    │  identity_id or UNKNOWN  │
                    └────────────┬─────────────┘
                                 ▼
                  ┌───────────────────────────────┐
                  │  identity_events (SQLite)     │
                  │  → employee_day_stats (PG)    │
                  └───────────────────────────────┘
```

**The key insight:** face recognition runs at ONE camera where a face is 80+ px
across. Every other camera matches *appearance*, which survives fine at 640px.

### Why not face recognition on every camera

Measured against your actual pipeline (frames resized to 640px wide,
`video_upload.py:492-494`):

| Distance | Person height (640px frame) | Eye-to-eye distance | Face recog viable? |
|---|---|---|---|
| 2 m | 300 px | 18 px | No |
| 3 m | 200 px | 12 px | No |
| 5 m | 120 px | 7 px | No |
| 8 m | 75 px | 4 px | No |

Face recognition needs ~80 px between the eyes; it degrades sharply below ~40.
Even at full 1080p with no downscaling, a face at 5 m is ~21 px. The information
is not in the frame — this is optics, not model quality.

Meanwhile **OSNet, the Re-ID model, is trained on 128×64 person crops** — smaller
than what your pipeline already produces. It was purpose-built for this distance.

---

## 3. The four signals, and why fusion beats any single one

No single signal is reliable in an office. They fail *independently*, which is
what makes combining them work.

| Signal | Weight | Strength | Fails when |
|---|---|---|---|
| **Seat prior** | 0.40 | Resolution-independent; 85–95% alone in fixed-desk offices | Hot-desking; person away from desk |
| **OSNet appearance** | 0.30 | Works from behind, at distance, while seated | Similar clothing; cross-camera colour shift |
| **Colour histogram** | 0.20 | Very fast; robust to blur | Uniforms / dress code |
| **Height (homography)** | 0.10 | Independent of clothing entirely | Seated; bad homography |

```
score = 0.40·seat + 0.30·osnet + 0.20·colour + 0.10·height

score ≥ 0.75  → accept, bind to employee
0.50–0.75     → tentative: hold, wait for more frames
score < 0.50  → UNKNOWN
```

The weights are a starting point. **Step 13 tunes them on your real footage** —
do not treat these numbers as final.

---

## 4. Database schema — migration 020

Continues your existing sequence (`frontend/prisma/sql/` currently ends at 019).
`zone_minute_stats` is **not modified**; the anonymous path keeps working exactly
as it does now.

```prisma
model Employee {
  id             String  @id @default(uuid()) @db.Uuid
  orgId          String  @db.Uuid
  employeeCode   String  @db.VarChar(64)
  displayName    String  @db.VarChar(160)
  assignedZoneId String? @db.Uuid          // the seat prior
  active         Boolean @default(true)
  createdAt      DateTime @default(now()) @db.Timestamptz(6)

  org        Organisation @relation(fields: [orgId], references: [id], onDelete: Cascade)
  faces      FaceTemplate[]
  dayStats   EmployeeDayStat[]

  @@unique([orgId, employeeCode])
  @@index([orgId, active])
  @@map("employees")
}

/// Enrolment photos, stored as embeddings only. 3-5 per person.
model FaceTemplate {
  id         String   @id @default(uuid()) @db.Uuid
  employeeId String   @db.Uuid
  embedding  Float[]                       // 512-d ArcFace vector
  quality    Float                         // detector confidence at enrolment
  createdAt  DateTime @default(now()) @db.Timestamptz(6)

  employee Employee @relation(fields: [employeeId], references: [id], onDelete: Cascade)
  @@index([employeeId])
  @@map("face_templates")
}

/// Raw identity observations. High volume, short retention (default 7 days).
/// This is the per-person analogue of activity_logs.
model IdentityEvent {
  id           String   @id @default(uuid()) @db.Uuid
  orgId        String   @db.Uuid
  employeeId   String?  @db.Uuid           // NULL = UNKNOWN person
  cameraId     String   @db.Uuid
  zoneId       String?  @db.Uuid
  trackId      Int                          // ByteTrack fragment id
  identityId   String   @db.VarChar(64)     // stitched session identity
  posture      Posture
  confidence   Float                        // fusion score that produced this
  method       String   @db.VarChar(24)     // face | fusion | seat | handoff
  observedAt   DateTime @db.Timestamptz(6)

  @@index([orgId, observedAt])
  @@index([employeeId, observedAt])
  @@index([identityId])
  @@map("identity_events")
}

/// Daily rollup — one row per employee per day. This is what the UI reads.
model EmployeeDayStat {
  id                String   @id @default(uuid()) @db.Uuid
  orgId             String   @db.Uuid
  employeeId        String   @db.Uuid
  statDate          DateTime @db.Date

  firstSeenAt       DateTime? @db.Timestamptz(6)
  lastSeenAt        DateTime? @db.Timestamptz(6)
  presentMinutes    Int      @default(0)
  deskMinutes       Int      @default(0)
  seatedMinutes     Int      @default(0)
  awayFromDeskCount Int      @default(0)
  breakMinutes      Int      @default(0)
  longestFocusBlock Int      @default(0)
  fragmentationIdx  Float    @default(0)

  /// Mean fusion confidence across the day. Below 0.6, the UI must show the
  /// row as low-confidence rather than as fact.
  bindingConfidence Float    @default(0)
  unknownMinutes    Int      @default(0)   // time we could not attribute

  employee Employee @relation(fields: [employeeId], references: [id], onDelete: Cascade)

  @@unique([employeeId, statDate])
  @@index([orgId, statDate])
  @@map("employee_day_stats")
}

/// Physical topology: which camera exits lead to which camera entries.
/// Powers the cross-camera handoff plausibility check.
model CameraLink {
  id           String @id @default(uuid()) @db.Uuid
  orgId        String @db.Uuid
  fromCameraId String @db.Uuid
  toCameraId   String @db.Uuid
  minSeconds   Int                          // fastest plausible walk
  maxSeconds   Int                          // slowest plausible walk

  @@unique([fromCameraId, toCameraId])
  @@map("camera_links")
}
```

SQLite gets mirror tables (`identity_events`, `employee_day_stats`) exactly as
`minute_aggregator.py:151` already does for `zone_minute_stats`.

---

## 5. The build — 18 steps, in dependency order

Each step ends with a **verifiable** result. Do not start a step until the
previous one's verification passes; that is what keeps this error-free.

### Phase A — Foundations (Steps 1–4)

---

#### Step 1 — Make privacy blur configurable
**Files:** `cv/anonymizer.py`, `api/routers/video_upload.py` (3 sites)
**Effort:** 30 min

Blur currently defaults ON and will destroy the face signal at the door camera.

- Read the default from env: `PRIVACY_BLUR_DEFAULT` (default `false` for dev)
- Keep the existing WebSocket `set_privacy_blur` toggle working
- Add a `blur_enabled` parameter so the door camera can always run unblurred

**Verify:** start a session, confirm faces are visible; toggle blur in the UI and
confirm it still works both ways.

---

#### Step 2 — Employee CRUD (schema + UI)
**Files:** new `frontend/prisma/sql/020_identity.sql`, new
`frontend/app/dashboard/employees/page.jsx`
**Effort:** 1 day

- Write migration 020 with all five models from §4
- Add RLS policies mirroring the pattern in `003_rls_policies.sql`
- Build an employee list page: add / edit / deactivate, assign a desk zone
- Reuse `DashboardShell.jsx` and the existing permission helpers in
  `app/lib/permissions.js`

**Verify:** create 3 employees, assign each a zone, confirm they persist and are
org-scoped (a second org cannot see them).

---

#### Step 3 — Extract the shared frame loop
**Files:** new `cv/frame_pipeline.py`; `api/routers/video_upload.py`
**Effort:** half a day

The loop is duplicated three times (lines ~485, ~718, ~891). Identity logic must
not be written three times.

- Extract into `process_detections(detections, spatial_engine, aggregator, ...)`
  returning `tracked_entities`
- Replace all three call sites

**Verify:** all three endpoints (upload, live webcam, single frame) still produce
identical output to before. **This is a pure refactor — no behaviour change.**

---

#### Step 4 — Identity event writer
**Files:** new `db/identity_writer.py`
**Effort:** half a day

Copy the sampling pattern from `db/activity_writer.py` exactly — same 5-second
interval, same `asyncio.to_thread` offloading, same swallow-and-log failure mode.

**Verify:** run a video; confirm `identity_events` rows appear with
`employeeId = NULL` (nothing identifies anyone yet — that is correct).

---

### Phase B — Single-camera identity (Steps 5–8)

This phase alone delivers desk time, chair exits, and break counts. **If you run
short on time, this is the part that must work.**

---

#### Step 5 — Appearance signature extraction
**Files:** new `cv/appearance.py`
**Effort:** 1 day

For each detection bbox, extract:
- **OSNet embedding** (512-d). Use `torchreid` or an ONNX export; crops resize to
  128×64 — the model's native input
- **Colour histogram**: HSV, split upper body (shoulders→hips) and lower body
  (hips→knees), using the pose keypoints you already compute
- **Height estimate**: `ground_point()` + head keypoint, projected through the
  existing homography

Batch all crops in one forward pass per frame — not one call per person.

**Verify:** two crops of the same person score cosine > 0.7; two different people
score < 0.5. **Print the numbers; do not assume.**

---

#### Step 6 — Fragment stitching (the ByteTrack fix)
**Files:** new `cv/identity_tracker.py`
**Effort:** 2 days

Maintains a gallery of active identities within one camera session.

```
For each detection:
  1. Known track_id already bound to an identity?  → reuse it
  2. New track_id → compare signature to gallery of recently-lost identities
     (last 120 s), require cosine > 0.65 AND plausible position
  3. Match → reattach (the occlusion case)
  4. No match → create a new identity_id
```

**Verify:** take a video where someone walks behind a pillar and returns.
Confirm the ByteTrack id changes but the `identity_id` stays the same. Count
raw ids vs. stitched identities — the ratio is your stitch-rate metric.

---

#### Step 7 — Seat-assignment binding
**Files:** `cv/identity_tracker.py`
**Effort:** 1 day

Bind `identity_id → employee_id` without any biometrics:

```
For each identity, accumulate time-in-zone.
If it spends > 60% of its observed time in zone Z,
and exactly one employee has assignedZoneId == Z,
  → bind, confidence = fraction_of_time_in_zone
```

**Verify:** with 3 employees at 3 desks, confirm each is bound correctly and the
reported confidence is sensible.

---

#### Step 8 — Chair exits, breaks, focus blocks
**Files:** new `db/employee_aggregator.py`
**Effort:** 2 days

Mirror the structure of `db/minute_aggregator.py`.

- **Chair exit** = left the assigned zone for **> 90 s** (debounce; bending down
  or leaning back must not count). Tune this threshold on real footage.
- **Break** = in a `BREAK` zone, or absent from all cameras, for > 5 min
- **Focus block** = continuous SEATED in the assigned zone, > 20 min, low motion
- **Fragmentation index** = number of focus blocks ÷ total desk hours

Roll up daily into `employee_day_stats`, then sync to Postgres following the
existing `sync_to_postgres_sync` pattern.

**Verify:** record a 10-minute video where you deliberately stand up 3 times.
Confirm `awayFromDeskCount == 3`. **This is the single most important
verification in the whole plan** — it is the number your sir asked for.

---

### Phase C — Face enrolment at the door (Steps 9–11)

---

#### Step 9 — Face enrolment UI
**Files:** `frontend/app/dashboard/employees/[id]/enroll/page.jsx`
**Effort:** 1 day

- Upload or webcam-capture 3–5 photos per employee
- Backend runs face detection + ArcFace embedding, stores to `face_templates`
- Reject low-quality captures (small face, low detector confidence) **at capture
  time**, with a clear message — a bad enrolment poisons every later match

**Verify:** enrol yourself with 5 photos; confirm 5 rows with quality > 0.8.

---

#### Step 10 — Door camera face matching
**Files:** new `cv/face_identifier.py`
**Effort:** 2 days

- Add a `role` field to cameras: `DOOR` or `AREA`
- On `DOOR` cameras only: detect faces (InsightFace/SCRFD), embed (ArcFace),
  match against `face_templates` with cosine > 0.6
- **Do not downscale door-camera frames to 640** — this is the one camera that
  needs resolution. Add a per-camera `inference_width` (default 640, door 1280)
- On match: capture today's appearance signature and register it

**Verify:** walk past the door camera. Confirm correct identification and that a
signature is registered. Test with an unenrolled person → must return UNKNOWN,
not a wrong name.

---

#### Step 11 — Daily signature registry
**Files:** `cv/identity_tracker.py`
**Effort:** 1 day

A per-day, in-memory (Redis-backed if available) store: `employee_id → today's
signature`. Updated continuously while confidence stays high, so it drifts with
the person (jacket removed, etc.). Cleared at midnight.

**Verify:** identify at the door, then confirm the desk camera picks up the same
person by appearance without any face match.

---

### Phase D — Cross-camera tracking (Steps 12–14)

This is the impressive part **and the least reliable**. Scope it honestly.

---

#### Step 12 — Camera topology editor
**Files:** `frontend/app/dashboard/cameras/topology/page.jsx`
**Effort:** 1 day

UI to declare: "corridor exit → pantry entry, 3–8 seconds". Writes `CameraLink`.

**Verify:** define links between 2–3 cameras and confirm they persist.

---

#### Step 13 — Handoff matching + weight tuning
**Files:** `cv/identity_tracker.py`
**Effort:** 3 days

When an identity disappears from camera A and a new one appears on camera B:

```
plausible = CameraLink(A→B) exists
            AND minSeconds ≤ gap ≤ maxSeconds
score      = 0.40·seat + 0.30·osnet + 0.20·colour + 0.10·height
accept if plausible AND score ≥ 0.75
```

Then **tune the weights on your own footage**. Label 200 handoffs by hand, grid-
search the four weights, pick what maximises accuracy at ≥ 0.75 confidence.
The §3 weights are a starting point, not an answer.

**Verify:** walk camera A → camera B ten times. Report how many handoffs were
correct, wrong, and UNKNOWN. **Expect 6–8 out of 10. That is a normal result.**

---

#### Step 14 — Confidence + UNKNOWN handling
**Files:** all identity modules
**Effort:** 1 day

- Every attribution carries its fusion confidence
- Below 0.5 → `employeeId = NULL`, `method = "unknown"`
- `employee_day_stats.unknownMinutes` accumulates unattributed time
- UI shows a warning banner when `bindingConfidence < 0.6`

**Verify:** confirm low-confidence periods land in `unknownMinutes` rather than
being silently attributed to the nearest guess.

---

### Phase E — Interface and evaluation (Steps 15–18)

---

#### Step 15 — Employee dashboard
**Files:** `frontend/app/dashboard/employees/[id]/page.jsx`
**Effort:** 3 days

Per-employee: daily timeline, desk time, chair exits, breaks, focus blocks,
7/30-day trend. **Show the confidence on every figure** — a number without its
confidence is a claim you cannot defend in the viva.

Reuse `AnalyticsCharts.jsx` and the existing `--chart-1/2/3` palette.

---

#### Step 16 — Team comparison view
**Files:** `frontend/app/dashboard/team/page.jsx`
**Effort:** 2 days

Sortable table across employees, with an explicit note on what the data cannot
tell you (off-camera work, meetings elsewhere).

---

#### Step 17 — Ground-truth evaluation
**Files:** new `backend/eval/`
**Effort:** 3 days

The chapter that turns this from a demo into a project.

- Hand-label 30–60 min of footage: who is where, when
- Measure: identification accuracy, stitch rate, handoff accuracy, chair-exit
  precision/recall, and **accuracy vs. confidence** curves
- Produce a confusion matrix

**This is your strongest result.** "94% accurate on the 78% of cases above 0.75
confidence, correctly abstaining on the rest" is far better than a single
number — and it is honest.

---

#### Step 18 — Failure documentation
**Effort:** 1 day

Record real failure cases with frames: similar clothing, occlusion, cross-camera
colour shift. Put them in the report.

Examiners trust a project that documents its failures far more than one that
claims perfection. This step costs a day and materially raises your grade.

---

## 6. Timeline

| Phase | Steps | Days | Delivers |
|---|---|---|---|
| A — Foundations | 1–4 | 2.5 | Schema, employee CRUD, refactor |
| B — Single camera | 5–8 | 6 | **Desk time, chair exits, breaks** |
| C — Door face | 9–11 | 4 | Named identification |
| D — Cross-camera | 12–14 | 5 | Multi-camera tracking |
| E — UI + eval | 15–18 | 9 | Dashboards, measured results |
| | **Total** | **~27 days** | |

**If time runs short, cut in this order:** Step 16 → Phase D → Step 18.
**Never cut Phase B or Step 17.** Phase B is the working system; Step 17 is the
result that earns the grade.

---

## 7. Dependencies

```
torchreid>=0.2.5          # OSNet Re-ID  (or standalone ONNX export)
insightface>=0.7.3        # SCRFD face detect + ArcFace embed
onnxruntime-gpu>=1.17.0   # you already have onnxruntime
scikit-learn>=1.4.0       # cosine similarity, clustering
```

Models to download once: `osnet_x0_25_market1501.onnx` (~2 MB),
`buffalo_l` (InsightFace, ~300 MB).

**GPU budget:** OSNet adds ~1 ms per person per frame batched. Face runs only on
the door camera. Expect the pipeline to go from ~83 FPS to ~55–65 FPS. Fine.

---

## 8. The five things most likely to go wrong

Written from the failure modes actually visible in this codebase.

1. **Refactoring the three loops breaks one endpoint.** Do Step 3 as a *pure*
   refactor, verify all three, and commit before adding any identity code.
2. **Colour shift between cameras.** The same shirt reads differently on each
   camera. Mitigation: per-camera white-balance normalisation in Step 5, or lean
   harder on OSNet, which is more robust to this than raw histograms.
3. **Chair-exit threshold too sensitive.** 90 s is a guess. Tune it against
   hand-labelled footage in Step 8, or you will report bending down as a break.
4. **Enrolment quality poisons everything.** A blurry enrolment photo makes that
   employee unmatchable all project long. Enforce the quality gate in Step 9.
5. **Track-id reuse across sessions.** ByteTrack restarts ids at 0 for each new
   video. `identity_id` must be namespaced per session — never assume track 3
   today is track 3 tomorrow.

---

## 9. Start here

```
Step 1  (30 min)  — make blur configurable          ← do this first
Step 2  (1 day)   — migration 020 + employee CRUD
Step 3  (0.5 day) — extract the shared frame loop   ← commit before proceeding
Step 4  (0.5 day) — identity event writer
```

After Step 4 you have the full skeleton with no identity logic — every write path
proven, nothing clever yet. That is the right foundation, and it is why the rest
goes in without errors.
