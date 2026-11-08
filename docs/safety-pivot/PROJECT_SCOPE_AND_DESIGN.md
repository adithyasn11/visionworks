# VisionWorks Safety — Project Scope and Design

**Working title:** Explainable Workplace Safety Compliance Monitoring with Evidence-Based, Abstaining Verdicts
**Short name:** VisionWorks Safety

This is the single reference document for the project: what it is, why it exists, every feature we are going to build, how each one works, what each one depends on, how it will be tested, and what is honestly out of scope. Where something already exists in the repository it says so and names the file; where it does not, it is marked **TO BUILD**.

> **How to read this:** Part I (sections 1–22) is the design — what and why. Part II (sections 23–25) is the build guide — the exact order of work and the full database design.
>
> Status as of this document: the platform (video input, detection, tracking, zones, dashboard, auth) exists. The PPE detector has been trained and measured, and the PPE evidence/verdict module is built and tested. Everything from the zone-event layer upward is still to build.

---

## Table of contents

1. [Summary](#1-summary)
2. [Problem and motivation](#2-problem-and-motivation)
3. [How we got here (why this direction)](#3-how-we-got-here)
4. [Scope: in, out, stretch](#4-scope-in-out-stretch)
5. [Core design principles](#5-core-design-principles)
6. [Users and roles](#6-users-and-roles)
7. [System architecture](#7-system-architecture)
8. [Feature specifications](#8-feature-specifications)
9. [Data model](#9-data-model)
10. [API surface](#10-api-surface)
11. [Dependency map and build order](#11-dependency-map-and-build-order)
12. [Configuration and thresholds](#12-configuration-and-thresholds)
13. [Privacy, ethics and stated limits](#13-privacy-ethics-and-stated-limits)
14. [Evaluation plan](#14-evaluation-plan)
15. [Demo plan](#15-demo-plan)
16. [Roadmap with acceptance criteria](#16-roadmap-with-acceptance-criteria)
17. [Risks and mitigations](#17-risks-and-mitigations)
18. [Expected questions and honest answers](#18-expected-questions-and-honest-answers)
19. [Stretch goal: cross-camera journeys (dropped from core)](#19-stretch-goal-cross-camera-journeys)
20. [What is reused from the original project](#20-what-is-reused-from-the-original-project)
21. [Repository layout](#21-repository-layout)
22. [Glossary](#22-glossary)

**Part II — Build guide**

23. [Step-by-step implementation guide](#23-step-by-step-implementation-guide)
24. [Database design in full](#24-database-design-in-full)
25. [Final checklist](#25-final-checklist-before-calling-the-project-done)

---

## 1. Summary

VisionWorks Safety watches camera feeds of an industrial or workshop area and answers one kind of question: **did the people in this area follow the safety procedure, and how sure are we?**

It does this by combining:

- **person detection and tracking** (YOLOv8 pose + ByteTrack),
- **PPE detection** (helmet and safety vest, YOLOv8m trained by us),
- **safety zones** drawn on the camera view (PPE checkpoint, restricted area, exit, etc.),
- **a rule engine** (for example "PPE must be verified before entering the restricted zone"),
- **a per-worker state machine** that follows each worker through the zones,
- **additional safety detectors** (man-down, lone-worker / occupancy limits),
- **a three-way verdict on everything it decides** — `COMPLIANT`, `POTENTIAL_VIOLATION` or `UNVERIFIABLE` — so that it never accuses someone on weak evidence,
- **a human review workflow** so a supervisor confirms or dismisses each flagged event, which also gives us a measured false-alarm rate,
- **evidence snapshots** (blurred) and an **audit trail**,
- **a dashboard, alerts and reports** built on the existing web application.

The one-sentence pitch: *an explainable safety compliance monitor that is designed to say "I can't tell" rather than wrongly accuse a worker, and measures how often it does each.*

---

## 2. Problem and motivation

Safety procedures in factories, workshops and warehouses are usually enforced by people: a supervisor watching, a paper log, or reviewing footage after an incident. Typical failures:

- A worker enters a hazard area without helmet or vest and nobody notices until later.
- Time limits in hazardous areas (heat, fumes, machinery) are not tracked.
- A person is alone in an area where the rule says two people must be present.
- A worker collapses and is not noticed for minutes.
- After an incident there is no reliable record of who was where, wearing what.

Camera-based monitoring can help, but a naive system has a serious flaw: **a wrong accusation has real consequences** (discipline, loss of trust), and a vision system is wrong some of the time. Most simple projects detect PPE in a single frame and print "violation". This project is built around the opposite stance: evidence is accumulated over time, weak evidence is reported as unverifiable, and every decision carries its reasons.

---

## 3. How we got here

The project started as **workplace activity analytics** — occupancy, posture, desk time per employee — built on a real platform (see §20). It was sent back by the project guide for two reasons:

1. **The identity assumption fails in real offices.** Naming a person by the desk they sit at breaks with hot-desking. Our own evaluation (`docs/FAILURE_CASES.md`) measured it: three people at each other's desks were named wrong 528 times out of 528, at confidence 1.00.
2. **"Time at desk" is not productivity.** The project's own documentation admitted this.

Both objections are about the *application*, not the engine. The detection, tracking, zone and alerting engine was sound. We therefore changed the question the system answers, from "is this employee productive" to **"was the safety procedure followed"** — a binary, rule-based, independently-existing requirement rather than an inferred behaviour.

A first version of the new idea (`PROJECT_PIVOT_PROPOSAL.md`) was built around **cross-camera journey verification**: chaining several camera hand-offs into one verified path. After assessing it honestly we **dropped it from the core** because it is the least reliable component (expected 60–75% per hop even in the project's own design notes, tested only on simulated data), and a core feature that may not work well is the wrong foundation. The code for it still exists and is kept as an optional stretch (§19). The novelty now rests on explainable, abstaining decisions with measured error rates, plus the additional safety detectors.

---

## 4. Scope: in, out, stretch

### In scope (core — must work)

| # | Feature | Section |
|---|---|---|
| F1 | Video input (upload, webcam, phone camera) — exists | 8.1 |
| F2 | Person detection and tracking — exists | 8.2 |
| F3 | PPE detection (helmet, vest) — model trained | 8.3 |
| F4 | PPE-to-person association — built | 8.4 |
| F5 | PPE evidence over time and verdict — built | 8.5 |
| F6 | Safety zones with types and metadata | 8.6 |
| F7 | Zone event generation (enter / exit / dwell) | 8.7 |
| F8 | Safety rule engine | 8.8 |
| F9 | Per-worker state machine and compliance decisions | 8.9 |
| F10 | Alerts and notifications | 8.13 |
| F11 | Evidence snapshots and privacy handling | 8.14 |
| F12 | Human review workflow | 8.15 |
| F13 | Safety dashboard | 8.16 |
| F14 | Safety reports | 8.17 |
| F15 | Roles, permissions, audit — exists, extended | 8.18 |
| F16 | Evaluation harness and metrics | 8.19 |
| F19 | Corrective actions (what was done about a confirmed violation) | 9.2, Step 12.7 |

### In scope (additional detectors — chosen add-ons)

| # | Feature | Section |
|---|---|---|
| F17 | Man-down detection | 8.10 |
| F18 | Lone-worker and occupancy-limit rules | 8.11 |

### Optional (build only if time remains)

| # | Feature | Section |
|---|---|---|
| O1 | Unsafe movement (running in a walk-only zone) | 8.12 |
| O2 | External notifications (email / Telegram) | 8.13 |
| O3 | Cross-camera journey verification | §19 |

### Explicitly out of scope

- Measuring productivity, performance or "how hard someone works".
- Recognising or naming individuals. Face recognition, enrolment and appearance-based re-identification were **removed** with the productivity product; workers are anonymous ByteTrack ids within one camera view, and verdicts are about "a worker", never a named person.
- Storing continuous video.
- Detecting PPE other than helmet and vest in the safety rules (the model also detects gloves, boots, mask, but these are not used in rules — see §8.3).
- Legal/compliance certification. This is a decision-support system for a supervisor, not an automated enforcement system.
- Multi-site fleet management, billing, mobile apps.

### Delivery tiers (the one authoritative scope definition)

Every other section (§11.2, §16, §23.1, §25) refers to these tiers. If time runs short, finish a tier completely before starting the next.

| Tier | Contains | Build steps (§23) | Means |
|---|---|---|---|
| **T1 — Must work (pass)** | F1–F9 with rule R1, SQLite persistence, blurred snapshots (F11), a simple review screen (F12), **own-footage PPE numbers and the baseline comparison (§14.A, §14.G)** | 0–6, 9, a minimal 12.4, evaluation track E1–E2 | The thesis claim is demonstrated and measured |
| **T2 — Strong project** | Postgres + RLS + sync, alerts, full dashboard, R3 dwell, man-down (F17) with staged hard negatives, human-review precision (§14.F), threshold sweep, reports (CSV) | 7, 8, 10, 11, 12, 13 (R3 first), 14, 16, E3–E5 | A complete, defensible product |
| **T3 — If time remains** | R2, R4, lone-worker/occupancy (F18), corrective actions (F19), rule-editor UI (otherwise rules are seeded), PDF export | 13 (R2, R4), 15, 12.7, 12.1 | Breadth |
| **Optional** | O1–O3 | Opt-A–C | Only after T2 is measured |

**Cut order if behind schedule** (cut from the top): O1–O3 → corrective actions → R4 and `VIA_CHECKPOINT` → rule-editor UI (seed rules in SQL instead) → PDF export (keep CSV) → lone-worker/occupancy. Never cut: own-footage evaluation, the baseline comparison, abstention.

---

## 5. Core design principles

These are the rules every feature follows. They are what make the project defensible.

1. **Abstain rather than accuse.** Every decision has three outcomes. If evidence is weak, the answer is `UNVERIFIABLE`, never a guess. A violation requires strong, sustained evidence; a missed detection in one frame can never create one.
2. **Evidence over time, not single frames.** The detector misses roughly 5–12% of real items in any one frame (§8.3). Decisions use a rolling window of "clear" frames.
3. **Only clear frames count.** A distant, cut-off, or overlapped person produces no evidence either way.
4. **Every decision explains itself.** Each stored decision includes the rule, the reason text, the numbers behind it (counts, fractions, confidences), the camera, zone, time and a snapshot.
5. **Humans make the final call.** The system produces `POTENTIAL_VIOLATION`; a supervisor confirms or dismisses it. The system never disciplines anyone.
6. **Privacy by construction.** In safety mode, faces (not helmets) are blurred before any frame leaves the pipeline, whatever the session toggle says; only blurred snapshots for flagged events are stored, with a retention limit; no continuous video.
7. **Three-layer enforcement** (inherited from the platform): UI hides what a role may not do → server re-checks → database row-level security is the real boundary.
8. **Reuse, then extend.** New code is added alongside the existing pipeline through the same patterns (writers, aggregators, service-role sync), not by rewriting it.
9. **Time comes from the video, not the wall clock.** For uploaded files processed faster than real time, all safety timing (dwell, timeouts) must use video time (`frame_index / fps`). See §17, risk R7.

---

## 6. Users and roles

The platform already has organisations, memberships and three roles. They map to safety work as follows.

| Role | In the safety product |
|---|---|
| **ADMIN** | Configures cameras, zones, rules, retention, members; can do everything a manager can |
| **MANAGER (safety officer)** | Runs analysis, reviews flagged events (confirm / dismiss), views evidence, exports reports |
| **VIEWER** | Reads dashboards and reports; cannot review or change anything; cannot see evidence snapshots unless granted |
| **Platform operator** | Vendor-side support console; sees system health but never an organisation's safety data (existing rule) |

New capabilities to add to `backend/app/api/permissions.py` and `frontend/app/lib/permissions.js` (the two must agree; database RLS is the real boundary):

- `safety.view` — ADMIN, MANAGER, VIEWER
- `safety.review` — ADMIN, MANAGER
- `safety.rules.edit` — ADMIN, MANAGER
- `safety.evidence.view` — ADMIN, MANAGER

---

## 7. System architecture

### 7.1 Block diagram

```
 Cameras / files                         BACKEND (FastAPI, Python)                         FRONTEND (Next.js)
┌──────────────┐   frames   ┌────────────────────────────────────────────────────┐
│ Uploaded file│──────────▶ │ 1 decode (OpenCV) + resize                         │
│ Webcam       │            │ 2 person detect + track + pose  (YOLOv8m-pose)     │
│ Phone (QR)   │            │ 3 PPE detect (YOLOv8m, helmet + vest)              │
└──────────────┘            │ 4 associate PPE to people          (ppe_evidence)  │
                            │ 5 zone containment                 (spatial_engine)│
                            │ 6 zone events + per-worker state   (safety layer)  │
                            │ 7 rules + decisions                (safety layer)  │
                            │ 8 extra detectors: man-down, lone-worker/occupancy │
                            │ 9 blur faces, encode, send live frame              │──WebSocket──▶ live view
                            │10 write events/decisions/snapshots (SQLite local)  │
                            └───────────────┬────────────────────────────────────┘
                                            │ background sync (service role)
                                            ▼
                               Postgres (Supabase) + RLS  ◀────── Next.js server actions / queries
                               safety_rules · safety_events                    dashboard · alerts · review · reports
                               safety_decisions · evidence index
```

### 7.2 Processing of one frame (the core loop)

The loop lives in `backend/app/cv/frame_pipeline.py` (`process_detections`) and is called from `backend/app/api/routers/video_upload.py` (upload, live webcam and browser-camera sockets). The safety layer is added into this same loop so that live view, persistence and safety decisions see identical data.

Order matters:

1. Read and resize the frame (640 px wide normally).
2. **Person detection + tracking + pose** — one pass, returns boxes, ByteTrack ids, 17 keypoints, posture.
3. **PPE detection** on the **unblurred** frame — helmets and vests only.
4. **Worker key** — the anonymous ByteTrack id (`trk-<id>`) within this camera view. No appearance signature is computed and nobody is named.
5. **PPE association and evidence** — each helmet/vest is given to at most one person; per-worker rolling evidence updated.
6. **Zone containment** — which zone each person is in.
7. **Zone events** — enter / exit / dwell transitions with debounce.
8. **Rules and state machine** — evaluate rules; produce decisions.
9. **Additional detectors** — man-down, lone-worker, occupancy.
10. **Anonymise** — blur heads **in place** *after* every step that needs the real image (this is an existing, deliberate ordering — blurring first would degrade detection and identity).
11. **Persist and send** — telemetry, events, decisions to SQLite; snapshot for flagged events; frame + entity payload to the browser.

### 7.3 Two databases (existing pattern, kept)

- **Local SQLite** (`workplace_analytics.db`): high-frequency pipeline writes. New tables here for safety events and decisions.
- **Postgres via Supabase** (Prisma schema in `frontend/prisma/schema.prisma`): organisations, cameras, zones, rules, members, and the synced results the dashboard reads, with row-level security.
- A background loop (like `minute_aggregator.run_aggregator_loop`) pushes SQLite results to Postgres using the service role, matching cameras and zones **by name** (the existing bridge between local ids and Postgres UUIDs).

---

## 8. Feature specifications

Each feature below lists: **purpose, how it works, inputs/outputs, depends on, edge cases, how it is tested**.

### 8.1 F1 — Video input *(exists)*

- **Purpose:** get frames into the pipeline.
- **Sources:** uploaded video files (`POST /api/v1/video/upload` then WebSocket `/process/{session_id}`), server webcam (`/live_webcam`), browser camera frames (`/process_webcam_frame`), phone camera via QR pairing (`api/routers/pairing.py`, `PHONE_CAMERA.md`).
- **Files:** `video_upload.py`, `pairing.py`, `websocket.py`, `frontend/app/camera/[code]/CameraClient.jsx`, `PhoneCameraPanel.jsx`.
- **Safety additions (TO BUILD):** every frame carries a **video timestamp**; a camera has a **role/site label** for the dashboard; uploaded videos can optionally carry a recording start time so events land on the right wall-clock time.
- **Depends on:** nothing.

### 8.2 F2 — Person detection and tracking *(exists)*

- **Purpose:** find every person and give each a stable id within one camera.
- **How:** `PostureEstimator.process_frame_single_pass` (`backend/app/cv/pose_estimator.py`) runs `yolov8m-pose.pt` with `bytetrack.yaml` in a single GPU pass: box, confidence, track id, 17 keypoints, smoothed posture (sitting/standing/walking/unknown).
- **Limit:** ByteTrack ids are local to one camera and reset after about 30 frames of occlusion. The earlier OSNet identity tracker that stitched fragments was removed, so the safety state machine keys on `track_id` alone. A worker lost for longer than the track survives therefore becomes a new worker; the zone-event layer handles that by closing the old visit as `LOST` and treating the verdict as `UNVERIFIABLE` rather than guessing (§8.7).
- **Used by:** F4–F9, F17, F18.

### 8.3 F3 — PPE detection

- **Purpose:** detect helmets and safety vests in a frame.
- **Model in use:** `ppe_training/runs/ppe_yolov8m_v1/weights/best.pt` — YOLOv8m fine-tuned on the 6-class Roboflow dataset (boots, gloves, helmet, mask, person, safety_vest), ~4.4k training images, 640 px.
- **Wrapper:** `backend/app/cv/ppe_detector.py` (`PpeDetector.detect(frame)` → helmets and vests only, with a low confidence floor of 0.20 so the evidence layer can tell "ambiguous" from "nothing").
- **Why only helmet + vest:** these are the two items the rules need. The model also detects gloves, boots, mask, but they are not used.
- **Why no "no-helmet" class:** a model with explicit `no-helmet`/`no-vest` classes was trained (`combined12`) and was much worse (mAP50 0.53 vs 0.89). Absence is therefore **inferred** from "person present, no helmet detected, over time" (F4/F5).
- **Measured quality** (`ppe_training/reports/PPE_MODEL_EVALUATION.md`, confidence ≥ 0.35):

  | Split | Helmet P / R | Vest P / R | Person P / R |
  |---|---|---|---|
  | Test (213 images) | 0.83 / 0.955 | 0.86 / 0.877 | 0.87 / 0.924 |
  | Validation (425 images) | 0.92 / 0.94 | 0.90 / 0.93 | 0.87 / 0.93 |

  mAP50 on validation 0.894 (helmet 0.962, vest 0.967). Inference ≈ 14 ms/image on the project GPU.
- **Known limits:** trained on photos resized to 640×640 by stretching; not yet measured on our own cameras (see §14); roughly one in six detections is spurious.
- **If our own footage shows weak numbers:** fine-tune with a few hundred frames labelled from our own cameras and re-measure. Not needed unless the footage test says so.
- **Depends on:** F1.

### 8.4 F4 — PPE-to-person association *(built)*

- **Purpose:** decide which helmet/vest belongs to which person, so one helmet in the frame cannot make everyone look compliant.
- **File:** `backend/app/cv/ppe_evidence.py` — `associate_ppe`, `associate_frame`, `frame_clarity`.
- **Rules:**
  - **Helmet:** its centre must lie inside the person box horizontally and in the top 35% of the box (allowing 10% above). Held hard-hats at hip height do not count.
  - **Vest:** at least 50% of the vest box must lie inside the person box and its centre must be between 12% and 72% of box height.
  - A PPE box is assigned to **at most one person** (nearest to that body part's expected position).
- **Clarity gate** (`frame_clarity`): a frame is *clear* for a person only if the box is at least 90 px tall and 28 px wide, not touching the top frame edge (head cut off), and not overlapping another person (IoU ≥ 0.30). Unclear frames contribute no evidence either way.
- **Output per person per frame:** `{helmet: conf, safety_vest: conf, clear: bool}`.
- **Tested:** `backend/tests/test_ppe_evidence.py` (association, crowd, held-helmet, distant person, edge cut-off).

### 8.5 F5 — PPE evidence over time and verdict *(built)*

- **Purpose:** turn noisy per-frame detections into a trustworthy per-worker PPE verdict.
- **File:** `ppe_evidence.py` — `PpeEvidenceTracker`.
- **How:** keeps a rolling window (default 30 frames) per worker of `(timestamp, helmet conf, vest conf, clear)`. Using only clear frames:
  - an item is **PRESENT** if seen (confidence ≥ 0.45) in ≥ 60% of clear frames;
  - **ABSENT** only if ≥ 90% of clear frames show nothing at all (confidence < 0.20);
  - otherwise **UNVERIFIABLE** (mixed, flickering, or ambiguous evidence);
  - fewer than 8 clear frames → UNVERIFIABLE;
  - evidence older than 10 s is ignored.
- **Overall worker verdict:** `NON_COMPLIANT` if any *required* item is ABSENT; `COMPLIANT` only if every required item is PRESENT; else `UNVERIFIABLE`. Required items are configurable per rule (helmet only, vest only, both).
- **Two vocabularies, on purpose:** `NON_COMPLIANT` is the *PPE state* of a worker at a moment (this module). `POTENTIAL_VIOLATION` is the *decision* a rule makes from it (F9) — "potential" because a person confirms it. A worker can be `NON_COMPLIANT` in the checkpoint without any rule being broken.
- **Asymmetry is deliberate:** presence needs 60%, absence needs 90%. This is *designed* to push errors toward abstention rather than accusation; whether it does on real footage is measured in §14.A and compared with a per-frame baseline in §14.G. The 0.60 / 0.90 values are starting points chosen before measurement and are tuned by the sweep in §14.G.
- **Known weakness:** errors that repeat in every frame (a dark cap consistently detected as a helmet) are not averaged out by a window — they can produce false *compliance*. These correlated cases are tested explicitly in §14.A.
- **Simulation sanity check — not a result** (measured detector recall, 15% unclear frames; frames assumed independent, which real video is not, so this is optimistic):

  | Truth | COMPLIANT | NON_COMPLIANT | UNVERIFIABLE |
  |---|---|---|---|
  | fully equipped | 100% | 0% | 0% |
  | no helmet | 0% | 81.5% | 18.5% |
  | no vest | 0% | 81.1% | 18.9% |
  | neither | 0% | 96.5% | 3.5% |

  In this simulation nobody missing PPE was called compliant and nobody equipped was accused. This only shows the logic behaves as intended under its assumptions; the real numbers come from §14.A and are the only ones to present as results.
- **Depends on:** F3, F4.

### 8.6 F6 — Safety zones *(extend existing)*

- **Purpose:** mark areas of the camera view that carry safety meaning.
- **Exists:** polygon zones drawn in `frontend/app/components/ZoneEditor.jsx`, stored per camera, tested with Shapely point-in-polygon (`backend/app/cv/spatial_engine.py`); persisted by `backend/app/api/routers/zones.py`. Anything outside every polygon is `TRANSIT_ZONE`.
- **TO BUILD — new zone types:**

  | Type | Meaning |
  |---|---|
  | `PPE_CHECKPOINT` | Where PPE is inspected before entry |
  | `APPROACH` | Walkway between checkpoint and hazard (optional) |
  | `RESTRICTED` | The hazardous area the rules protect |
  | `EXIT` | Required exit / decontamination point after the restricted zone |
  | `GENERAL` | Ordinary area, no rules |

- **Where the change lands:** `VALID_ZONE_TYPES` in `zones.py` (currently `WORKSTATION, BREAK, MEETING, TRANSIT`), the zone loader in `spatial_engine.py` (it must keep `zone_type`), and the zone-editor type dropdown. The pipeline reads the type from the local SQLite `zones.zone_type` column. It is also mirrored to a new Postgres column `zones.safetyType` (VARCHAR + CHECK, nullable) so the dashboard can show it and the rule editor can check that, for example, a `PPE_ENTRY` rule sits on a `RESTRICTED` zone. The existing Postgres `ZoneType` enum is not changed (it and the Python validator already disagree — a pre-existing inconsistency this work does not depend on). See Step 1 in §23.
- **Why rules live in a separate table:** zone *type* says what an area is; rule *parameters* (time limits, required PPE) belong to rules (F8) so one zone can be referenced by several rules.
- **Depends on:** F1.

### 8.7 F7 — Zone event generation *(TO BUILD)*

- **Purpose:** the pipeline currently only knows the zone a person is in *right now*. Safety rules need **events**: "entered", "left", "has been inside for N seconds".
- **File (new):** `backend/app/safety/zone_events.py`.
- **State kept per worker:** current zone, time entered, time last seen in it, pending candidate zone (for debounce).
- **Debounce:** a worker only "enters" a new zone after being continuously inside it for `ENTER_CONFIRM_SECONDS` (default 0.7 s); only "exits" after being continuously outside for `EXIT_CONFIRM_SECONDS` (default 1.0 s). This stops a person standing on a polygon edge from generating dozens of events.
- **Track loss:** if a worker disappears while inside a zone, the zone is held open for `LOST_GRACE_SECONDS` (default 5 s). If they reappear under the same `track_id` the visit continues; otherwise an exit is recorded with reason `LOST` (which rules treat carefully — see F8 R4).
- **Event types:**

  ```
  ENTERED_ZONE        EXITED_ZONE         LOST_IN_ZONE        REAPPEARED
  DWELL_WARNING       DWELL_EXCEEDED
  PPE_VERDICT_CHANGED
  ```

  Each event has: org, camera, zone, worker key (`trk-<track_id>`), event type, video timestamp, wall-clock time, details JSON.
- **Duplicate suppression:** identical consecutive events for the same worker/zone are dropped.
- **Depends on:** F2, F6.
- **Tested:** synthetic walks across polygons, boundary jitter, occlusion gaps, re-entry.

### 8.8 F8 — Safety rule engine *(TO BUILD)*

- **Purpose:** express the procedures in configuration, not code.
- **Files (new):** `backend/app/safety/rules.py`, `backend/app/safety/schemas.py`. Rules are stored in Postgres (`safety_rules`) and loaded by the session at start (the same way seat maps and camera links are loaded today), and cached.
- **Rule types (first version):**

  | Code | Rule | Parameters |
  |---|---|---|
  | **R1** | PPE required to enter a zone | `target_zone`, `required_ppe` (helmet/vest), `mode` |
  | **R2** | PPE must remain compliant while inside | `target_zone`, `required_ppe` |
  | **R3** | Maximum time inside a zone | `target_zone`, `max_seconds`, `warn_fraction` (default 0.8) |
  | **R4** | Required exit through a given zone | `target_zone`, `exit_zone`, `max_seconds_to_exit` |
  | **R5** | Minimum / maximum people (lone-worker, occupancy cap) | `target_zone`, `min_people`, `max_people`, `grace_seconds` |
  | **R6** | Man-down | `zone` (or whole camera), `min_seconds_down` |
  | **R7** *(optional)* | Walk-only zone (no running) | `target_zone`, `max_speed` |

  Every rule also has: `name`, `severity` (INFO / WARNING / CRITICAL), `enabled`, `min_evidence_confidence`.
- **R1 modes:**
  - `AT_ENTRY` *(default)* — the worker's PPE verdict is evaluated **at the moment of entering the restricted zone** from that zone's own camera view. Works with a single camera and needs no earlier checkpoint.
  - `VIA_CHECKPOINT` — the worker must have a recent `COMPLIANT` verdict from a `PPE_CHECKPOINT` zone **in the same camera view** within `max_seconds_since_checkpoint`. Used when the checkpoint and the restricted zone are in view together.
- **Output of evaluating a rule on a worker:** a *decision* (see 8.9).
- **Validation:** rules are checked when saved (zone exists, parameters in range, referenced zones belong to the same camera) so a bad rule cannot silently never fire.
- **Depends on:** F6, F7, F5 (for R1/R2).

### 8.9 F9 — Per-worker state machine and compliance decisions *(TO BUILD)*

- **Purpose:** follow each worker through the zones of one camera and produce a verdict for each applicable rule.
- **Files (new):** `backend/app/safety/worker_state.py`, `backend/app/safety/compliance_engine.py`.
- **Per-worker state:**

  ```
  worker key · current zone · zone entered at
  PPE verdict (from F5) and when it was last COMPLIANT
  checkpoint passed at (if any)
  open decisions · visit history
  ```
- **Decision record** (one per rule evaluation):

  ```
  decision_id, org, camera, zone, rule_id, worker key
  status          COMPLIANT | POTENTIAL_VIOLATION | UNVERIFIABLE
  reason          human-readable sentence
  confidence      0..1, derived from the evidence (see below)
  evidence        JSON: PPE item fractions, clear-frame counts, timings, rule parameters
  snapshot_ref    path of blurred image (flagged events only)
  video_time, wall_time
  review_state    NOT_REQUIRED | PENDING | CONFIRMED | DISMISSED  (Postgres only — see 8.15, §24.4)
  ```
- **How each rule decides:**

  - **R1, `AT_ENTRY`:** on `ENTERED_ZONE(RESTRICTED)` read the worker's PPE verdict.
    - `COMPLIANT` → decision `COMPLIANT`.
    - `NON_COMPLIANT` (confident absence) → `POTENTIAL_VIOLATION`, reason "entered restricted zone with no helmet detected in 28/29 clear frames".
    - `UNVERIFIABLE` → the decision is **held open for up to `ENTRY_EVIDENCE_WAIT_SECONDS`** (default 4 s) while more frames accumulate; if it is still unresolved the decision is `UNVERIFIABLE` with the reason (too few clear frames / mixed evidence / worker too far).
  - **R1, `VIA_CHECKPOINT`:** as above, plus `POTENTIAL_VIOLATION` if the worker enters without any checkpoint visit in the allowed time, or `UNVERIFIABLE` if a checkpoint visit exists but its PPE verdict was unverifiable.
  - **R2:** while inside, any confident `NON_COMPLIANT` for longer than `grace` (default 3 s) → `POTENTIAL_VIOLATION`; evidence going unclear while inside produces *no* decision (no accusation from absence of evidence).
  - **R3:** warning event at 80% of the limit; `POTENTIAL_VIOLATION` at the limit. Dwell is measured from the confirmed entry to confirmed exit using video time. If the worker is lost and does not return within the grace period, the visit is closed as `UNVERIFIABLE` (we cannot claim they stayed).
  - **R4:** after a confirmed exit from the restricted zone, the worker must appear in the `EXIT` zone within `max_seconds_to_exit`. If seen elsewhere (confidently in a different zone) → `POTENTIAL_VIOLATION`; if simply lost from view → `UNVERIFIABLE`.
  - **R5, R6, R7:** see 8.10–8.12.
- **Confidence:** for PPE-based decisions it is the fraction of clear frames that support the conclusion, combined with the mean detection confidence of the supporting frames. It is reported alongside the numbers; the *threshold for naming a violation is the 90%/60% rule in F5*, not a tuned score. Decisions below `min_evidence_confidence` are downgraded to `UNVERIFIABLE`.
- **Why `UNVERIFIABLE` is a first-class result:** it appears in the dashboard, in reports and in the evaluation, so we can report an honest abstention rate instead of hiding it.
- **Depends on:** F5, F7, F8.
- **Tested:** scripted scenarios (§14) driven through the real engine with synthetic detections, in the style of `backend/tests/test_ppe_evidence.py` and `test_zone_types.py`.

### 8.10 F17 — Man-down detection *(TO BUILD)*

- **Purpose:** raise an alert when a person appears to have collapsed and is not moving.
- **How it works** (uses pose keypoints the pipeline already computes):
  1. **Orientation:** the shoulder-midpoint → hip-midpoint vector is closer to horizontal than vertical (angle from vertical above `DOWN_ANGLE`, default 60°), **or** the person box is much wider than tall (aspect w/h above ~1.2) with confident keypoints.
  2. **Stillness:** very low movement of the box centre for the whole window (reusing `ActivityAggregator` motion speed).
  3. **Duration:** conditions (1) and (2) hold continuously for `MIN_SECONDS_DOWN` (default 5 s).
  4. **Exclusions:** the existing posture label `SITTING` is ignored **only when the torso is upright** (angle from vertical < 45°). This matters: the current classifier (`pose_estimator.py:311-316`) scores a person lying flat as `SITTING`, because the knees are level with the hips, so excluding every `SITTING` label would suppress every real fall. A worker who is crouching or bending (torso angle changing, head still above hips) does not satisfy (1); an area flagged as "lying allowed" is skipped.
  5. **Inputs:** man-down needs the raw keypoints, which today are in `detections` but are not copied into the per-person entities (`frame_pipeline.py:178-187`). The safety engine therefore receives `detections` (with keypoints) as well as the entities.
  6. **Stillness units:** `get_recent_motion_speed` is pixels per frame keyed by `track_id`; it is converted to *person heights per second* (divide by box height, multiply by fps) so the threshold works at any distance and frame rate.
- **Verdicts:** `POTENTIAL_VIOLATION`-class alert `MAN_DOWN` (severity CRITICAL) when all hold; `UNVERIFIABLE` when keypoints are low-confidence or the person is partly out of frame (reported as a warning, not an emergency).
- **Evaluation:** staged lying-down clips versus hard negatives (sitting on floor, crouching to tie shoes, bending to pick up, reaching under a table). Report recall, false alarms per hour of normal footage, and time-to-alert.
- **Depends on:** F2 (pose), F13 (alerts). Does not depend on PPE.
- **Honest limit:** from one overhead or angled camera, a person lying along the line of sight can look like a standing person; we state the camera placement that was tested.

### 8.11 F18 — Lone-worker and occupancy limits *(TO BUILD)*

- **Purpose:** enforce "never alone in this zone" (`min_people = 2`) and "no more than N people" (`max_people`).
- **How it works:** the pipeline already counts people per zone every frame (`zone_summary` in `process_detections`). The rule engine keeps a short history of that count per zone and uses the **median over a window** (default 2 s) so tracker flicker cannot create a violation.
  - Lone worker / under-staffed: `1 ≤ count < min_people` for longer than `grace_seconds` (default 10 s) → `POTENTIAL_VIOLATION` (`LONE_WORKER`).
  - Over capacity: count above `max_people` for longer than the grace → `POTENTIAL_VIOLATION` (`OVER_CAPACITY`).
  - `UNVERIFIABLE` if the zone is crowded/overlapped enough that detections are unreliable (average clear-frame fraction below a threshold), or the zone just became empty (leaving is not a lone-worker case).
- **Depends on:** F2, F6, F8.
- **Reuse:** the idea of a sustained condition with a cooldown (the old `alerts_engine.py`, now removed, used it); the safety engine implements it for decisions (Step 10).

### 8.12 O1 — Unsafe movement *(optional)*

- **Purpose:** flag running in a walk-only zone.
- **How:** motion speed per person (already computed for the walking posture) normalised by person height so it is distance-independent; sustained above a threshold for a few seconds inside a `WALK_ONLY` zone.
- **Why optional:** needs per-camera calibration of speed thresholds and adds little beyond the core. Built only if time remains.

### 8.13 F10 — Alerts and notifications *(extend existing)*

- **Removed:** the occupancy alert engine (`alerts_engine.py`), its `alerts` / `alert_rules` tables and `AlertsPanel.jsx` were deleted with the productivity product (migration 024). They evaluated aggregated minutes (sedentary, overcrowded, under-used), which is both the wrong idea and too slow for safety events that must fire within seconds.
- **TO BUILD:** an **event-driven path**. In the safety product an **alert is a `POTENTIAL_VIOLATION` decision that is still `PENDING` review**. One source of truth (the decision), one alert per rule/worker/visit (or per episode for continuous rules), acknowledged by being reviewed. A new alerts panel reads decisions. See Step 10 in §23.
- **Alert kinds** (rule type + reason): missing PPE at entry, PPE lost while inside, dwell exceeded, required exit missing, lone worker, over capacity, man-down; `UNVERIFIABLE` events are informational and not alerts; a camera-coverage-lost warning can reuse the existing `CAMERA_OFFLINE` alert.
- **Each alert carries:** severity, rule name, camera, zone, time, reason, confidence, worker reference (anonymous key), snapshot link, review state.
- **Where it shows:** live banner on the dashboard, the alerts list, the event timeline.
- **O2 external notifications (optional):** email or Telegram bot message for CRITICAL alerts (man-down, confirmed violations), via a small outbound webhook. Off by default; destination configured per organisation. Never contains the image.
- **Depends on:** F9, F17, F18.

### 8.14 F11 — Evidence snapshots and privacy handling *(TO BUILD)*

- **Purpose:** a supervisor must be able to *see* what the system saw, without the system recording video.
- **How:**
  - When a `POTENTIAL_VIOLATION` or man-down is created, the pipeline saves **one** JPEG of the current frame **after** the face-only blur (§13, Step 9 — the helmet stays visible), with overlays: person box, helmet/vest boxes, zone outline, decision text.
  - Optionally a small number (e.g. 3) of frames around the event; never a continuous video.
  - Stored locally under a git-ignored evidence folder, named by decision id, organised per organisation; index row in the database.
  - **Retention:** default 7 days, configurable per organisation up to the plan limit; a cleanup job deletes expired files and keeps the decision row (the existing retention schedule is the pattern).
  - **Access:** only through an authenticated endpoint that checks organisation membership and the `safety.evidence.view` capability; every view is written to the audit log.
  - If an organisation disables evidence storage, decisions are stored without images and the UI says "evidence not stored".
- **Ordering rule:** PPE detection runs on the real frame; the blur is applied before anything is saved or sent.
- **Depends on:** F9.

### 8.15 F12 — Human review workflow *(TO BUILD)*

- **Purpose:** the system *suggests*; a person *decides*. It also generates the data for our most valuable measurement.
- **States:** `PENDING → CONFIRMED` (real violation) or `DISMISSED` (false alarm), optional free-text note and reason code (`WRONG_PERSON`, `PPE_PRESENT`, `NOT_IN_ZONE`, `OTHER`).
- **Screen:** a queue of `PENDING` `POTENTIAL_VIOLATION` decisions with the snapshot, the reason, the numbers and the timeline; Confirm / Dismiss buttons.
- **Who:** `safety.review` (ADMIN, MANAGER). Every action is audit-logged with user and time.
- **Metrics produced:** precision of flags as judged by humans (confirmed ÷ reviewed), top dismissal reasons, median time-to-review. `UNVERIFIABLE` events can also be reviewed and labelled, which tells us how often abstentions hid real violations.
- **Depends on:** F9, F11, F15.

### 8.16 F13 — Safety dashboard *(TO BUILD, reusing the dashboard shell)*

New area under `frontend/app/dashboard/safety/`, reusing `DashboardShell`, the zone editor, the live canvas player and the charts.

| Screen | Content |
|---|---|
| **Live** | Live annotated video, per-worker PPE status badges, zone occupancy, current open alerts |
| **Overview** | Today's counts: compliant / potential violations / unverifiable; open alerts; reviewed vs pending; top rule violated |
| **Events** | Filterable list (rule, zone, camera, status, time), each row linking to its evidence |
| **Event detail** | Snapshot, reason, numbers behind the decision, timeline of the worker's zone visits, review controls |
| **Review queue** | Pending decisions (F12) |
| **Rules** | Create/edit rules (R1–R7) with validation (F8); enable/disable |
| **Zones** | Existing editor extended with safety zone types (F6) |
| **Reports / Export** | See F14 |
| **Evaluation** | Existing evaluation screen extended with safety metrics (F16) |

The existing three-layer pattern applies to each screen (hide controls, server re-check, RLS).

### 8.17 F14 — Safety reports *(TO BUILD, reusing report export)*

- **Contents:** compliance rate; potential violations by rule / zone / camera / hour; unverifiable rate; confirmed vs dismissed (human-reviewed); average time inside restricted zones; man-down alerts; lone-worker incidents; PPE-missing counts by item.
- **Distinguish clearly** between: *system-flagged*, *human-confirmed*, *dismissed*, and *insufficient evidence*, so a report can never present an unreviewed flag as a confirmed violation.
- **Formats:** CSV and PDF via the existing report generator (`backend/app/utils/report_generator.py`, `ReportExport.jsx`).
- **Per-shift audit trail:** for each shift, the ordered list of events and decisions.

### 8.18 F15 — Auth, roles, audit *(exists, extended)*

- Supabase auth, organisations, memberships, three roles, platform operators, RLS, audit log — all exist.
- **Additions:** the four safety capabilities in §6; RLS policies for the new tables (members of the org read; only ADMIN/MANAGER write reviews and rules; the pipeline writes through the service role); audit entries for rule edits, reviews, evidence views, exports.

### 8.19 F16 — Evaluation harness *(partly built)*

- **Built:** `ppe_training/eval_ppe_model.py` (per-class precision/recall on dataset splits), `ppe_training/eval_ppe_footage.py` (PPE chain on our own labelled video), `ppe_training/recompute_confusion.py`.
- **TO BUILD:** a safety scenario evaluator (`backend/eval/safety_scenarios.py`, new — the old identity evaluation harness was removed) that drives scripted scenarios through the rule engine and reports per-rule confusion (see §14).

---

## 9. Data model

### 9.1 New local (SQLite) tables — written by the pipeline *(full DDL in §24.3)*

Created at startup the same way `ensure_identity_table()` creates the identity table.

**`safety_events`** — the event stream

| Column | Notes |
|---|---|
| id | autoincrement |
| org_id, camera_id, zone_id | text, same bridging as telemetry |
| worker_key | `trk-<track_id>` (never a name) |
| event_type | ENTERED_ZONE, EXITED_ZONE, DWELL_WARNING, DWELL_EXCEEDED, LOST_IN_ZONE, REAPPEARED, PPE_VERDICT_CHANGED |
| video_time_s, observed_at | video clock and wall clock |
| details | JSON |

**`safety_decisions`** — one row per rule evaluation

| Column | Notes |
|---|---|
| id (uuid), org_id, camera_id, zone_id, rule_id | |
| worker_key | anonymous |
| status | CHECK in (COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE) |
| reason, confidence, evidence (JSON) | |
| snapshot_ref | nullable |
| video_time_s, observed_at | |
| session_key | the pipeline's session id (groups a video run) |
| synced_at | set when pushed to Postgres |

Review fields are **not** in SQLite: reviews happen in the dashboard and live only in Postgres (§24.8).

A `CHECK` constraint keeps `status` and the evidence honest in the same spirit as the identity tables.

### 9.2 New Postgres (Prisma) models *(full SQL in §24.4, Prisma in §24.5)*

- **`SafetyRule`** — id, orgId, name, type (R1–R7), params (JSON), severity, enabled, createdBy, timestamps, soft delete.
- **`SafetyEvent`**, **`SafetyDecision`** — synced mirrors of the local tables (decision includes review fields; review writes go to Postgres and are the source of truth).
- **`CorrectiveAction`** — what was done about a confirmed violation: decision, title, assignee, due date, priority, status `OPEN → IN_PROGRESS → CLOSED | CANCELLED`, closing note. This closes the management loop (detect → decide → review → act → close).
- **`zones.safetyType`** — a new nullable `VARCHAR + CHECK` column (`PPE_CHECKPOINT | APPROACH | RESTRICTED | EXIT | GENERAL`). The existing `ZoneType` enum is left untouched; the safety type is a separate column so the dashboard can show and filter it.
- **`sessionKey`** on events and decisions — the pipeline's session id, so results can be grouped per video run.
- **`ReportKind`** gains `SAFETY_SUMMARY`, so safety reports reuse the existing `reports` table.
- The legacy `alerts` / `alert_rules` tables were dropped by `024_retire_productivity_tables.sql` (alerts are decisions — §8.13).
- Snapshots are files referenced by `snapshotRef`; no separate evidence table.

All carry `orgId`, with RLS written per table from that row alone (same rule the schema already follows), SQL in `frontend/prisma/sql/` as new numbered migrations.

### 9.3 Sync

A background loop (modelled on `run_aggregator_loop`) pushes unsynced events/decisions to Postgres through PostgREST with the service-role key, resolving cameras and zones by name, and marks rows synced. Reviews are written directly to Postgres by the dashboard and read back by reports.

---

## 10. API surface

New router `backend/app/api/routers/safety.py`, prefix `/api/v1/safety`. Organisation is always derived server-side from the verified access token (`api/deps.py`), never from a parameter.

| Method & path | Purpose | Capability |
|---|---|---|
| `GET /rules` | list rules for the organisation | `safety.view` |
| `POST /rules`, `PATCH /rules/{id}`, `DELETE /rules/{id}` | manage rules (validated) | `safety.rules.edit` |
| `GET /events` | filtered event stream | `safety.view` |
| `GET /decisions` | filtered decisions | `safety.view` |
| `GET /decisions/{id}` | one decision with evidence | `safety.view` |
| `POST /decisions/{id}/review` | confirm / dismiss + note | `safety.review` |
| `GET /decisions/{id}/snapshot` | blurred image (audited) | `safety.evidence.view` |
| `GET /summary` | counts for the overview | `safety.view` |
| `GET /export` | CSV / PDF report | `safety.view` |

WebSocket frames from the existing processing sockets gain: per-worker `ppe` status, `safety` flags and any new decision ids, so the live view can annotate people.

---

## 11. Dependency map and build order

### 11.1 Feature dependencies

```
F1 video ─▶ F2 detect/track/pose ─┬─▶ F4 associate ─▶ F5 PPE evidence ──┐
                 │                │                                      ▼
F3 PPE model ────┴───────────────▶┘                        F6 zones ─▶ F7 zone events ─▶ F8 rules ─▶ F9 state machine
                                                                                              │       + decisions
                       F2 pose ─────────────▶ F17 man-down ───────────────────────────────────┤
                       F2 + F6 counts ──────▶ F18 lone-worker/occupancy ──────────────────────┤
                                                                                              ▼
                              F10 alerts ◀── F9 decisions ──▶ F11 snapshots ──▶ F12 review ──▶ F14 reports
                                    │                               │                  │
                                    └──────────────▶ F13 dashboard ◀┴──────────────────┘
                              F15 auth/RLS/audit underlies everything that touches data
                              F16 evaluation measures F5, F9, F17, F18, F12
```

Plain-language dependencies:

- **PPE verdicts** need person tracking and the PPE model.
- **Rules** need zones and zone events; **R1/R2** additionally need PPE verdicts.
- **Man-down** needs only pose; **lone-worker** needs only person counts per zone. They do not depend on PPE, so they can be built and demoed independently.
- **Alerts, snapshots, review, dashboard, reports** all consume *decisions*, so the decision record (F9) is the central interface — get its schema right first.
- **There is no identity stitching** (removed): the state machine keys on `track_id`, so a long occlusion can split a worker's visit. That is why lost-track handling in F7 and the `UNVERIFIABLE` outcomes exist.

### 11.2 Build order

> The authoritative, detailed order is **§23 (Steps 0–18)**; the list below is the short version. Where they differ (for example §23 adds an explicit time-base step and builds persistence before the first decision), §23 wins.

1. **Safety zone types** (F6).
2. **Zone events** (F7) with debounce and track-loss handling.
3. **Wire PPE detector into the live loop** (F3–F5 into `frame_pipeline`/`video_upload`), show per-worker PPE status on the live view.
4. **Decision record + R1 `AT_ENTRY`** (F8, F9) — the first end-to-end safety verdict.
5. **Local persistence + Postgres tables + sync** (§9).
6. **Alerts (event-driven)** (F10) and **snapshots** (F11).
7. **Review workflow** (F12) and **dashboard screens** (F13) for the above.
8. **R3 dwell, R4 required exit, R2 continuous PPE** (F8/F9).
9. **Man-down** (F17) and **lone-worker/occupancy** (F18).
10. **Reports** (F14), **safety evaluation** (F16), **polish and demo**.
11. Optional: O1 unsafe movement, O2 notifications, O3 cross-camera.

What counts as "minimum" is defined once, by the delivery tiers in §4; this list is only the order.

---

## 12. Configuration and thresholds

All of these live in `backend/app/safety/config.py` (env override `SAFETY_<NAME>`, listed in `backend/.env.example`) with the defaults below, are recorded with every decision (so a decision can be reproduced), and are the knobs we tune on real footage.

| Setting | Default | Used by |
|---|---|---|
| PPE detector confidence floor | 0.20 | F3 |
| "Seen" confidence | 0.45 | F5 |
| "Ambiguous" lower bound | 0.20 | F5 |
| Evidence window | 30 frames | F5 |
| Min clear frames | 8 | F5 |
| Presence fraction | 0.60 | F5 |
| Absence fraction | 0.90 | F5 |
| Evidence stale after | 10 s | F5 |
| Min person size for evidence | 90×28 px | F4 |
| Crowd overlap (IoU) | 0.30 | F4 |
| Enter / exit confirm | 0.7 s / 1.0 s | F7 |
| Lost-track grace (→ `LOST_IN_ZONE`) | 5 s | F7 |
| Lost-track TTL (→ visit closed, reason `LOST`) | 8 s | F7 |
| PPE model run every Nth frame | 2 | F3 |
| Re-alert gap for the same rule / zone / worker | 60 s | F10 |
| Entry evidence wait | 4 s | F9 |
| Continuous-PPE grace (R2) | 3 s | F9 |
| Dwell warning fraction | 0.8 | R3 |
| Man-down angle / duration | 60° / 5 s | F17 |
| Man-down: `SITTING` ignored only below torso angle | 45° | F17 |
| Man-down "still" speed | 0.15 person-heights/s | F17 |
| Lone-worker / occupancy grace / median window | 10 s / 2 s | F18 |
| Occupancy: min clear fraction (else `UNVERIFIABLE`) | 0.6 | F18 |
| Evidence retention | 7 days | F11 |
| Alert cooldown | per rule | F10 |

---

## 13. Privacy, ethics and stated limits

**What the system does to protect people**

- **Blurring.** The existing blur (`anonymizer.py`) covers the top 25% of each person box at full width and is **off by default** (`PRIVACY_BLUR_DEFAULT`). That region is exactly where the helmet is, so a reviewer could not check a "no helmet" flag. For the safety product: blur is **on** whenever `SAFETY_ENABLED=true`, and it blurs the **face only**, located from the nose / eye / ear keypoints the pose model already gives, leaving the helmet crown and the vest visible (Step 9). If face keypoints are too weak, the old head-box blur is used and the snapshot is marked "helmet region obscured".
- No continuous video is stored; only flagged-event snapshots, with a retention limit and audited access.
- Safety decisions refer to an anonymous worker key, not a name.
- **No face recognition exists.** Face enrolment, door-camera matching and appearance re-identification were deleted from the code base (backend modules, API routes, tables and screens), so the safety product ships no biometric matching at all.
- Platform operators cannot read an organisation's safety data.

**Consent and data handling (for our own recordings and the demo)**

- Every volunteer signs a short consent form: what is recorded, that it is used only for this project's evaluation, where it is stored, and that it is deleted after evaluation (date stated). Faces in published figures are blurred.
- The design follows the principles of India's Digital Personal Data Protection Act, 2023: a stated purpose, data minimisation (no names, no continuous video), limited retention, and access control with an audit trail. This is a design alignment, not a legal compliance claim.
- **Function creep.** Safety data could be misused to discipline or rank workers. Mitigations built in: no names; flags are "potential" until a person reviews them; reports use honest categories (Step 16); no per-person productivity or ranking report exists; every evidence view and export is audited. The deploying organisation's policy remains its responsibility, and we say so.

**What we do not claim**

- We do not claim to measure productivity or performance.
- We do not claim the system is accurate on any camera until it has been measured there; the published numbers (§8.3, §8.5) are from public images and simulation until our own footage is evaluated (§14).
- We do not claim to replace a supervisor. Output is decision support; people confirm.

**Known limits to state openly**

- Detection recall is not perfect (helmet ≈ 95%, vest ≈ 88–93% on test images) and about one in six detections is spurious.
- Training photos were stretched to 640×640; behaviour on 16:9 video is unmeasured until our footage test.
- Distant, partly hidden, crowded, or backlit workers will often be `UNVERIFIABLE` — by design.
- Man-down from a single camera angle can miss a person lying along the line of sight.
- Within a single camera, a worker lost for long periods may split into two visits.

---

## 14. Evaluation plan

The evaluation is a core deliverable, not an afterthought; it is what separates this from a demo that merely "works once".

### 14.1 Already measured

PPE detector per class on the public test split (§8.3) and a simulation of the verdict logic (§8.5).

### 14.2 To measure

**A. PPE chain on our own footage** (`ppe_training/eval_ppe_footage.py`)
Record one worker per ~10 s segment: fully equipped, no helmet, no vest, neither — at near, mid and far distance, in the demo lighting, with a few sideways turns.
- **Size:** at least 40 segments per condition (4 conditions × 3 distances, so ≥ 480 segments ≈ 80 minutes), with at least 4 different people, 2 lighting set-ups and varied clothing. Recorded in weeks 1–2 (evaluation track E1), before any dashboard work.
- **Hard cases on purpose (correlated errors):** dark cap, hood, hair bun, yellow/orange shirt (vest look-alike), helmet held in hand, helmet worn unbuckled, backlight. These test the false-compliance weakness noted in §8.5.
- **Labelling:** per-segment truth from the recording sheet; a sample of ~300 frames box-labelled for per-frame recall.
- **Report:** per-frame recall and false-detection rate per item; per-segment verdict vs truth (correct / abstained / **false accusation** / **false compliance**).
- **How to state zero:** "0 false accusations in N segments" is reported with its 95% upper bound by the rule of three (≤ 3/N; e.g. N = 360 non-violation segments → ≤ 0.83%), never as a bare "0%".
- **Targets:** false accusations and false compliance 0 in N (with the bound); abstention rate reported, not hidden.
- **Check the public test split for leakage:** Roboflow exports can put near-duplicate frames in train and test. Compute perceptual hashes across splits and report the number of near-duplicates; if many, re-state the public numbers on the de-duplicated test set.

**B. Zone events and rules** — scripted walks through the real zone layout:

| Scenario | Expected |
|---|---|
| S1 equipped worker enters restricted zone | `COMPLIANT` |
| S2 worker without helmet enters | `POTENTIAL_VIOLATION` |
| S3 worker without vest enters | `POTENTIAL_VIOLATION` |
| S4 worker far away / partly hidden enters | `UNVERIFIABLE` |
| S5 two workers close together, one equipped | each judged separately; ambiguity → `UNVERIFIABLE` |
| S6 worker stands on a zone boundary | one entry, not many |
| S7 worker stays beyond the time limit | warning, then `POTENTIAL_VIOLATION` |
| S8 worker leaves the restricted zone via the wrong route | `POTENTIAL_VIOLATION` (or `UNVERIFIABLE` if lost) |
| S9 worker alone in a two-person zone | `LONE_WORKER` after grace |
| S10 worker occluded for several seconds then reappears | same visit continues |

Report timing error (entry/exit within ±1 s), dwell-time error, and the per-rule confusion matrix.

**C. Man-down** — staged falls vs hard negatives (sitting on floor, crouching, bending, reaching). At least 30 staged falls (several people, at least two directions relative to the camera, including one along the line of sight) and at least 1 hour of normal activity with hard negatives. Report recall, false alarms per hour, time-to-alert. **Targets:** recall ≥ 0.85 on falls across the line of sight (falls along it reported separately as a stated limit), ≤ 1 false alarm per hour, median time-to-alert ≤ 8 s (5 s rule + detection).

**D. Lone-worker / occupancy** — counted correctness against hand-counted ground truth over a few clips.

**E. System** — end-to-end alert latency (event → alert visible) and processed frames per second, on the named demo hardware (record GPU model, CPU, RAM, input resolution). **Targets:** ≥ 10 processed FPS with pose + PPE (identity off), ≥ 6 FPS with identity on; alert visible ≤ 3 s after the decision. If a target is missed, report the measured value and run PPE every second frame (the evidence window tolerates it).

**F. Human review loop** — after running a set of clips, a reviewer confirms/dismisses every flag; report the human-judged precision of flags, dismissal reasons, and how many `UNVERIFIABLE` events were in fact violations.

**G. Baseline comparison and threshold sweep — the core experiment.** This is what shows the contribution is real rather than asserted. On the **same** own-footage segments:

| System | How it decides |
|---|---|
| B0 — per-frame | violation if a single frame has no helmet/vest box associated with the person (what a typical "YOLO helmet detector" project does) |
| B1 — majority vote | violation if the item is missing in > 50% of the window's frames, no clear-frame gate, no abstention |
| **Ours** | clear-frame gate + 60% presence / 90% absence + `UNVERIFIABLE` |

Report for each: false accusations, false compliance, abstentions, correct, all with N. Then **sweep** presence ∈ {0.4, 0.5, 0.6, 0.7}, absence ∈ {0.7, 0.8, 0.9, 0.95} and window ∈ {10, 20, 30, 45} frames, and plot **error rate against coverage** (fraction of segments where the system commits to a verdict). The chosen thresholds are justified from this plot, which also answers "why 0.60 and 0.90?". This table and plot are the thesis's main result.

### 14.3 What to report honestly

Per scenario: correct / abstained / wrong, and especially **wrong-and-confident**. A system with a few abstentions and no false accusations is a success; one with no abstentions and a few false accusations is not.

---

## 15. Demo plan

**Setup (scaled-down workshop)**

- One camera (laptop webcam, or a phone paired by QR) with a view covering a taped-out layout: **PPE checkpoint** → short **approach** → **restricted zone** → **exit** marker. Optionally a second phone watching the restricted zone alone (independent camera, no identity link).
- Props: helmet and high-visibility vest (a few of each), two or three volunteers in clearly different clothing.

**Storyline (about 6–8 minutes)**

1. Draw the zones and create the rules in the dashboard live.
2. Equipped worker passes the checkpoint and enters → `COMPLIANT`.
3. Worker without helmet enters → `POTENTIAL_VIOLATION`, alert, snapshot, reason with numbers.
4. Worker far away / partly hidden → `UNVERIFIABLE` (explain why it refuses to accuse).
5. Dwell limit: shorten the limit for the demo; show the warning then violation.
6. Lone worker: one person enters the two-person zone.
7. Man-down: a volunteer lies down; alert in seconds.
8. Review queue: confirm one, dismiss one; show the report and the evaluation numbers.

**Fallback:** a pre-recorded clip of each scenario so the demo works if a camera fails.

---

## 16. Roadmap with acceptance criteria

> High-level phases. The step-by-step build order with files, database changes and tests for each step is in **§23**.

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Done** | PPE model trained and measured; evidence/verdict module + tests; eval scripts; repo cleaned | Tests pass; numbers in `PPE_MODEL_EVALUATION.md` |
| **1. Zones** | Safety zone types (F6) | Types selectable in the editor, validated by API, stored in both databases |
| **2. Events** | Zone events with debounce (F7) | Scenarios S6, S10 pass in tests; boundary jitter gives one event |
| **3. Live PPE** | PPE detector in the live loop with per-worker status on screen | Live view shows PPE state per person at ≥ 10 processed FPS (§14.E) |
| **4. First verdict** | Decision record + R1 `AT_ENTRY` (F8, F9) | S1–S4 produce the expected statuses with stored reasons |
| **5. Persistence** | SQLite tables, Postgres models + RLS, sync (§9) | Decisions appear in the dashboard database with correct org isolation |
| **6. Alerts + evidence** | Event-driven alerts, snapshots (F10, F11) | A violation raises one alert (cooldown works) with a blurred snapshot |
| **7. Review + dashboard** | Review queue and safety screens (F12, F13) | A reviewer can confirm/dismiss; viewer cannot; audit rows written |
| **8. More rules** | R2, R3, R4 | S7, S8 pass |
| **9. Extra detectors** | Man-down, lone-worker/occupancy (F17, F18) | Scenarios S9 and staged falls meet the targets in §14 |
| **10. Reports + evaluation** | Reports, safety evaluation, own-footage numbers (F14, F16) | All §14 measurements recorded in a results document |
| **11. Polish** | Demo rehearsal, fallback clips, documentation | Full storyline runs end to end |
| *Optional* | O1 unsafe movement, O2 notifications, O3 cross-camera | Only after phase 10 |

Which phases are required is defined by the delivery tiers in §4, not here.

### 16.1 Timeline (about 14 weeks, two parallel tracks)

Owners are placeholders — fill in one name per row so each member can state what they built. The evaluation track runs **in parallel from week 1**, because its results decide whether the model needs fine-tuning.

| Weeks | Build track (§23 steps) | Evaluation track | Owner |
|---|---|---|---|
| 1 | Step 0, Step 1 | E1: consent forms, record own footage (§14.A) | _____ |
| 2 | Step 2, Step 3 | E1 cont.; label segments; leakage check | _____ |
| 3 | Step 4 (zone events) | E2: run `eval_ppe_footage.py`; B0 / B1 / ours (§14.G) | _____ |
| 4 | Step 5 (live PPE) | E2 cont.; fine-tune on own frames **only if** E2 shows poor recall | _____ |
| 5 | Step 6 (R1 decision) — **T1 logic complete** | E3: threshold and window sweep | _____ |
| 6 | Step 9 (snapshots, face-only blur), minimal review screen — **T1 complete** | Scenario suite S1–S6 | _____ |
| 7 | Step 7 (Postgres, RLS), Step 8 (sync) | — | _____ |
| 8 | Step 10 (alerts), Step 11 (API) | — | _____ |
| 9 | Step 12 (dashboard screens) | E4: record man-down falls and hard negatives | _____ |
| 10 | Step 13 (R3 first), Step 14 (man-down) | E4 cont. | _____ |
| 11 | Step 16 (CSV report) — **T2 complete** | E5: human review loop, FPS and latency | _____ |
| 12 | T3 items if on schedule (R2, R4, Step 15, 12.7) | Scenario suite S7–S10 | _____ |
| 13 | Step 18 (hardening) | Results chapter, figures | _____ |
| 14 | Demo rehearsal ×3, fallback clips | Thesis and slides | _____ |

If a week slips, apply the cut order in §4 rather than compressing the evaluation track.

---

## 17. Risks and mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | PPE model weaker on our cameras than on public images | Medium | Measure early with own footage (§14.A); fine-tune with a few hundred of our own frames; the verdict logic abstains rather than accuses |
| R2 | Spurious helmet/vest detections cause false compliance | Medium | 60% presence rule over many frames; association to a person's head/torso region; measured |
| R3 | Zone-boundary flicker or occlusion splits visits | Medium | Debounce, lost-track grace, `UNVERIFIABLE` on lost workers (identity stitching was removed, so splits are expected and handled, not hidden) |
| R4 | Man-down false alarms from sitting/bending | Medium | Duration requirement, posture exclusions, hard-negative evaluation, `UNVERIFIABLE` on weak keypoints |
| R5 | Running PPE + pose + identity together is too slow | Low–medium on a GPU, **high on CPU** | A GPU is required for live use; inference moved off the event loop (Step 5); PPE ≈14 ms/image on the project GPU; identity can be turned off (`IDENTITY_TRACKING=false`); PPE can run every second frame since evidence is windowed |
| R6 | Schema and permission drift between Python and JS | Low | Same pattern as today (mirrored tables, RLS is the truth); tests for capability parity |
| R7 | Safety timing wrong on uploaded video because of wall-clock use, or on seek | **Real** | The safety layer uses its own `FrameClock` (video time) and resets on seek (Step 2). The identity tracker is deliberately left on wall time, because its hand-off registry compares against wall-clock departure times |
| R11 | Workers too small to count as "clear" because frames are shrunk to 640 px | **Real** | PPE runs on the full-resolution frame (Step 5); clear-frame rate measured by distance in E2 |
| R12 | Man-down suppressed by the posture classifier, which labels a lying person `SITTING` | **Real** (found in review) | `SITTING` exclusion applies only with an upright torso; dedicated test (Step 14) |
| R13 | A rule cannot be deleted because of RLS | **Real** (found in review) | Soft delete through `soft_delete_safety_rule()` (§24.4), as 020 does for employees |
| R8 | Time lost on the dashboard instead of the detection logic | Medium | Build order puts the verdict logic first; dashboard reuses existing shell and components |
| R9 | A feature not working at demo time | Medium | Layered scope (§4, §16), pre-recorded fallback clips, optional items only after the core |
| R10 | Evidence storage raises privacy objections | Low | Blurred only, flagged events only, short retention, audited access, switch to disable |

---

## 18. Expected questions and honest answers

**Why did you change from productivity?** Productivity can't be measured from a camera — our own documentation said so — and the identity method failed under hot-desking (measured 528/528 wrong). Safety rules are binary and exist independently of the camera.

**Isn't this just YOLO PPE detection?** Detection is the input, and on our own footage we show what per-frame detection alone does: the per-frame baseline B0 versus our verdicts on the same clips (§14.G), with false accusations, false compliance and abstentions counted separately. What we add is the step after detection: evidence over time with a clear-frame gate, abstention instead of guessing, and rules over zone *sequences* (enter, stay, exit) rather than single frames.

**What exactly is your contribution? (state it this way)**
1. An evaluation, on our own footage, of abstaining, evidence-windowed PPE verdicts against per-frame and majority-vote baselines, with false accusation and false compliance measured separately and an error-versus-coverage curve.
2. Zone-sequence safety rules (PPE before entry, maximum dwell, required exit) with explicit `UNVERIFIABLE` handling for lost tracks and unclear evidence.
3. A human-in-the-loop measurement of what the abstentions hid (how many `UNVERIFIABLE` cases were real violations) and of reviewer-judged precision.
4. An integrated, privacy-preserving system (face-only blur, anonymous keys, audited evidence) that makes those decisions reviewable.

**What we do NOT claim:** a new detector or tracker; being "first"; calibrated probabilities; man-down, occupancy or multi-tenant security as research contributions (they are engineering on top). Abstention itself is an old idea (selective classification / reject option, Chow 1970) and PPE-to-worker association appears in construction-safety vision work (e.g. Fang et al. 2018; Nath et al. 2020) — we cite them and apply and measure the ideas in this setting.

**Why 0.60 and 0.90?** They were starting values; the sweep in §14.G shows the error-versus-coverage trade-off and the chosen point is justified from it.

**Is the confidence calibrated?** No, and we do not claim it is. It is a summary of the evidence fractions, shown for explanation. The decision is made by the evidence rule, not by a confidence threshold.

**Your snapshot blurs the head — how does the reviewer check a helmet?** In safety mode only the face is blurred, from pose keypoints; the helmet and vest stay visible (§13, Step 9).

**What did each member build?** See the owner column of §16.1, and §20 for what was inherited from the earlier platform (and is not claimed as new work).

**What if the system is wrong?** It is designed so wrongness shows up as `UNVERIFIABLE` rather than a false accusation: a violation needs ≥ 90% of clear frames to show nothing, presence needs only 60%. A supervisor confirms every flag. We report the measured rates.

**How do you know it works?** Per-class precision/recall on held-out images, scenario tests on the rule engine, and — most importantly — evaluation on our own footage with false accusation and false compliance counted separately.

**Why not use cross-camera tracking?** We built and tested a prototype earlier, but matching people across cameras by appearance is the least reliable part (our own estimate 60–75% per hop, tested only in simulation) and it needs biometric-style appearance signatures. A safety system should not rest on its weakest component, so the prototype was removed and cross-camera journeys are future work.

**Why not badges/RFID?** They answer different questions and work best together: RFID or a badge reader says reliably *who* entered and *when*; a camera says whether PPE is actually *worn* and what happened inside. A real deployment could use a badge reader for entry identity and this system for PPE and behaviour. We use cameras alone because that is the part a badge cannot do.

**Privacy and consent?** Face-only blur in safety mode; no continuous video; only flagged-event snapshots with retention and audited access; anonymous worker keys; face enrolment disabled in safety mode; signed consent from every volunteer; designed in line with India's DPDP Act 2023 principles (§13).

**Commercial products exist (Intenseye, Protex AI, Everguard and others).** Yes — they already offer PPE detection, zones, man-down and review workflows. We do not claim to beat them. They are closed: they publish no false-accusation rates, no abstention behaviour and no baseline comparison. Our project is an open, measured study of *how* such decisions should be made when the detector is imperfect, with the error trade-off made explicit.

**What are the limits?** Listed in §13, stated up front.

---

## 19. Stretch goal: cross-camera journeys

**Dropped from the core, and the prototype was removed.** An earlier version had a pairwise hand-off module (a topology gate plus a weighted appearance score), a camera topology editor, the `camera_links` table and a simulated evaluation. All of it was deleted with the productivity product (migration 024 and the identity clean-up). It remains in git history (the commit `97706bb` and earlier) if it is ever wanted.

**As future work**, a journey layer would have to be rebuilt: it could chain accepted hand-offs into a sequence (checkpoint camera → restricted-zone camera) and run R1 in `VIA_CHECKPOINT` mode across cameras. It would need: multi-hop confidence combination that does not collapse to near zero, ordering and timing checks, and `UNVERIFIABLE` whenever any hop is weak. It would be reported separately, with its own measured accept/reject/unverifiable rates, and the core project would not depend on it.

---

## 20. What is reused from the original project

The original workplace-analytics platform provides roughly the whole non-safety half of the system. Nothing below needs to be rebuilt.

| Capability | Where |
|---|---|
| Video upload / webcam / phone-camera pairing | `video_upload.py`, `pairing.py`, camera client |
| Person detection, tracking, pose, posture | `pose_estimator.py` |
| Polygon zones, containment, floor mapping | `spatial_engine.py`, `ZoneEditor.jsx`, `zones.py` |
| Per-track dwell and motion | `activity_aggregator.py` |
| ~~Appearance identity stitching~~ | removed (the safety product does not identify or re-identify people) |
| Head blurring | `anonymizer.py` |
| Local telemetry and minute aggregation, sync to Postgres | `activity_writer.py`, `minute_aggregator.py` |
| ~~Alerts with cooldown and acknowledgement~~ | removed (migration 024); rebuilt on decisions in Step 10 |
| Dashboard shell, charts, heatmap, reports | `frontend/app/dashboard/`, `components/` |
| Auth, organisations, roles, RLS, audit, retention, plans | Supabase + `frontend/prisma/` |
| ~~Evaluation harness~~ | the identity harness was removed; the safety scenario suite is new work (Step 17) |
| Test style | `backend/tests/` |

**Repositioned, not removed:** occupancy, posture, heatmaps and the existing analytics stay as a secondary "site analytics" view. The desk/seat identity binding and employee desk-time features remain in the code but are not part of the safety product and make no productivity claims.

---

## 21. Repository layout

Existing (after the clean-up) plus **new** items marked ★.

```
backend/
  app/
    api/routers/        cameras, zones, analytics, video_upload, websocket, pairing
                        ★ safety.py
    cv/                 pose_estimator, spatial_engine, frame_pipeline, activity_aggregator,
                        anonymizer, posture_diagnostics, ...
                        ✔ ppe_detector.py, ppe_evidence.py      (built)
    safety/             ★ zone_events.py, rules.py, schemas.py, worker_state.py,
                        ★ compliance_engine.py, man_down.py, occupancy.py, snapshots.py
    db/                 ... activity_writer, minute_aggregator
                        ★ safety_models.py, safety_writer.py, safety_sync.py
  eval/                 ★ safety scenarios (new)
  tests/                existing tests, ✔ test_ppe_evidence.py
                        ★ test_zone_events.py, test_rules.py, test_compliance.py,
                        ★ test_man_down.py, test_occupancy.py
frontend/
  app/dashboard/        existing screens
                        ★ safety/ (live, overview, events, detail, review, rules)
  prisma/               schema + migrations ★ new safety models and SQL (RLS)
ppe_training/           ✔ training, evaluation scripts, datasets/ (ignored), runs/ (ignored), reports/
docs/                   design docs ★ safety-pivot/PROJECT_SCOPE_AND_DESIGN.md (this file)
```

---

## 22. Glossary

| Term | Meaning |
|---|---|
| **PPE** | Personal protective equipment — here, helmet and safety vest |
| **Clear frame** | A frame in which a person is large, uncropped and unobstructed enough to count as evidence |
| **Verdict / decision** | The system's result for a rule: `COMPLIANT`, `POTENTIAL_VIOLATION`, or `UNVERIFIABLE` |
| **Abstention** | Choosing `UNVERIFIABLE` instead of guessing |
| **False accusation** | System says violation, worker was compliant (the error we most want to avoid) |
| **False compliance** | System says compliant, PPE was actually missing |
| **Track id** | ByteTrack's per-camera id for a person; may reset after occlusion |
| **Identity id** | Stitched per-session identity across track fragments (optional) |
| **Zone event** | An enter/exit/dwell transition, as opposed to "the zone they are in now" |
| **Debounce** | Requiring a condition to persist briefly before acting on it |
| **RLS** | Postgres row-level security — the real data-access boundary |
| **Service role** | The privileged key the backend uses to write synced results |
| **Snapshot** | One blurred image saved for a flagged event |
| **Man-down** | A person apparently collapsed and not moving |
| **Lone worker** | One person alone in a zone that requires at least two |

---

# PART II — BUILD GUIDE

Sections 23 and 24 turn the design above into an ordered, step-by-step build plan with the database designed in full. Section 23 is the order of work; section 24 is every table, constraint, policy and sync rule the steps refer to.

---

## 23. Step-by-step implementation guide

### 23.0 Ground rules for every step

1. **Order matters.** Steps are ordered so each one produces something runnable and testable. Do not start a step before the previous step's "Done when" is true.
2. **Feature flag.** All safety code is behind `SAFETY_ENABLED` (env var, default `false`). With it off, the pipeline must behave exactly as it does today — same payload, same writes. This is the same trick `IDENTITY_TRACKING` already uses, and it means we can never break the existing system by working on the new one.
3. **Tests first for logic.** Anything with decision logic (zone events, rules, man-down, occupancy) gets a test file in the style of `backend/tests/test_zone_types.py`: plain script, `check(name, ok, detail)`, prints PASS/FAIL, exit code 1 on failure, runnable with `venv\Scripts\python.exe backend\tests\<file>.py`.
4. **Never break existing tests.** After each step run all existing test files; they must still pass.
5. **Never decide on a single frame, never accuse on weak evidence.** Every step that produces a verdict must be able to return `UNVERIFIABLE`.
6. **Video time, not wall clock.** Every new function that reasons about time takes an explicit timestamp argument.
7. **One commit per step** (or per sub-step) with a message that says what became possible.
8. **Every feature ships with its screen.** A step that adds something a person uses also adds (or changes) the frontend for it in the same step, in the app's existing theme (red accent, Inter, the CSS tokens in `globals.css`). No step is "done" with a backend only. All visible wording is about safety; nothing from the productivity product may remain on screen.
9. **Database changes are additive.** New tables only; the only edits to existing tables are two nullable/defaulted columns (`zones.safetyType`, `organisations.safetyEvidenceEnabled`) and one enum value (`ReportKind.SAFETY_SUMMARY`), all in `025_safety.sql`.

### 23.1 Overview of the steps

| Step | Title | Produces | Type |
|---|---|---|---|
| 0 | Baseline and scaffolding | Branch, `safety/` package, config, feature flag | Setup |
| 1 | Safety zone types | New zone types end to end | Backend + UI |
| 2 | Time base | Video-time clock used everywhere | Backend |
| 3 | Local database | SQLite tables + writer | Database |
| 4 | Zone event generator | Enter / exit / dwell / lost events | Logic |
| 5 | PPE in the live loop | Per-worker PPE status on the live view | Backend + UI |
| 6 | Rules and first decision (R1) | End-to-end verdict for PPE-before-entry | Logic |
| 7 | Postgres schema, RLS, review function | Production database | Database |
| 8 | Sync SQLite → Postgres | Decisions visible to the dashboard | Backend |
| 9 | Evidence snapshots | Blurred image per flagged event | Backend |
| 10 | Alerts and live notification | Immediate alert, no duplicates | Backend + UI |
| 11 | Safety API and permissions | REST endpoints, capability parity | Backend |
| 12 | Dashboard screens | Rules, events, detail, review, overview, live | Frontend |
| 13 | More rules (R2, R3, R4) | Continuous PPE, dwell limit, required exit | Logic |
| 14 | Man-down | Fall / collapse alert | Logic |
| 15 | Lone worker and occupancy | R5 | Logic |
| 16 | Reports and export | CSV / PDF with honest categories | Backend + UI |
| 17 | Evaluation | Scenario suite + own-footage numbers | Evaluation |
| 18 | Hardening and demo | Performance, retention, rehearsal, fallback clips | Polish |
| Opt-A | Unsafe movement (R7) | Running in a walk-only zone | Optional |
| Opt-B | External notifications | Email / Telegram | Optional |
| Opt-C | Cross-camera journeys | See §19 | Optional |

Which steps are required is defined by the **delivery tiers in §4** (T1 = Steps 0–6, 9, a minimal review screen and evaluation E1–E2). The steps are numbered in build order, not priority.

**Evaluation track (runs in parallel from week 1, owned by one member):**

| Track | What | Feeds |
|---|---|---|
| E1 | Consent forms; record and label own footage incl. hard cases (§14.A); public-split leakage check | everything below |
| E2 | `eval_ppe_footage.py` on own footage; baselines B0 / B1 vs ours (§14.G); fine-tune only if recall is poor | Step 5, Step 6 thresholds |
| E3 | Threshold and window sweep, error-vs-coverage plot (§14.G) | final `SafetyConfig` defaults |
| E4 | Man-down staged falls and hard negatives (§14.C) | Step 14 |
| E5 | Human review loop, FPS, latency (§14.E, §14.F) | results chapter |

---

### Step 0 — Baseline and scaffolding

**Goal:** a clean place to build, with nothing able to break the existing system.

**Do:**
1. Commit the current clean-up and PPE module on `main` (no feature branches; the commit before the safety work, `97706bb`, is the recoverable base).
2. Create the package `backend/app/safety/` with `__init__.py`.
3. Create `backend/app/safety/config.py`: one dataclass `SafetyConfig` holding every threshold in §12, each overridable by an env var (`SAFETY_ENTER_CONFIRM_S`, …), plus `safety_enabled()` that reads `SAFETY_ENABLED` the same way `identity_tracking_enabled()` reads its flag.
4. Add the variables (commented, with defaults) to `backend/.env.example`.
5. Add `backend/evidence/` to `.gitignore` (used in Step 9).

**Files:** new `safety/__init__.py`, `safety/config.py`; modify `.env.example`, `.gitignore`.
**Database:** none.
**Tests:** `test_safety_config.py` — defaults load; env override works; invalid values fall back to the default with a warning.
**Done when:** all 14 existing test files still pass and `SAFETY_ENABLED` unset means no behaviour change.

---

### Step 1 — Safety zone types

**Goal:** a zone can be marked as a PPE checkpoint, approach, restricted area, exit, or general area.

**Why first:** every later step refers to zones by type.

**Do:**
1. `backend/app/api/routers/zones.py`: extend `VALID_ZONE_TYPES` with `PPE_CHECKPOINT`, `APPROACH`, `RESTRICTED`, `EXIT`, `GENERAL`. Keep the four existing types so legacy zones stay valid. Update the comment block that documents each type.
2. `backend/app/cv/spatial_engine.py`: `load_zones` currently keeps only `zone_id`, `zone_name` and the polygon. Keep `zone_type` as well and add `zone_type_of(zone_id)`; `check_zone_containment` is unchanged.
3. `frontend/app/components/ZoneEditor.jsx`: add the five types to the type dropdown, give each a distinct colour (restricted = red, checkpoint = blue, exit = green, approach = amber, general = grey), and show a short label on the polygon.
4. `video_upload.py` already passes `zone_type` from the local database into `zones_config`; verify it reaches `SpatialEngine`.
5. Fix the existing gaps in `zones.py` that the new types expose: the create path does not validate `zone_type`, and the POST path for an existing zone ignores a changed `zone_type` (`zones.py:351-359`). Validate against `VALID_ZONE_TYPES` on every write path.
6. **Zone hit point for safety** moves to Step 4, where the zone-event generator first consumes it: the pipeline tests the box *centre* (`frame_pipeline.py:152`); for floor-drawn safety zones use the person's ground point (`SpatialEngine.ground_point`, bottom-centre) when `SAFETY_ENABLED`, so analytics numbers do not change.
7. **Frontend (done with this step):** new shared module `frontend/app/lib/zoneTypes.js` (the five types, labels, hints, colours); `ZoneEditor.jsx` offers only the safety types, defaults a new zone to General, shows a type badge per zone, and colours outlines by type; the live overlay (`lib/overlay.js`) uses the same colours. Legacy types are shown as General. Colours stay inside the app's red / white / grey palette.

**Files:** `zones.py`, `spatial_engine.py`, `frame_pipeline.py`, `ZoneEditor.jsx`.
**Database:** SQLite `zones.zone_type` is already a free `String(32)` — no change. The pipeline reads the type from SQLite. The Postgres mirror is extended in Step 7, once the column exists: `_mirror_zone_to_postgres` gains a `zone_type` argument and loses its early return when the name is unchanged (`zones.py:107-108`), otherwise a PATCH that changes only the type never reaches Postgres. Until 024 is applied the mirror must **not** send `safetyType` (PostgREST would reject the whole request with 400 and the name/polygon update would be lost too) — gate it on a `SAFETY_PG_SCHEMA_READY` setting or a one-time column check.
**Tests:** `test_zone_types.py` (built) — create and PATCH accept each safety type and reject unknown ones; a re-save without a type keeps the existing type; a type-only PATCH is persisted; the engine returns the type for a zone id; placeholder zones are GENERAL.
**Done when:** you can draw a zone, pick `RESTRICTED`, reload, and it persists with its badge and colour; legacy zones still load. *(Backend, tests and editor built; the Postgres mirror of the type is Step 7.)*

---

### Step 2 — Time base

**Goal:** every safety calculation uses the video's own clock so results are the same whether a file is processed at 1× or 10× speed.

**Do:**
1. New `backend/app/safety/clock.py`: `FrameClock(fps, recorded_at=None, live=False)` with `tick(frame_index) -> (video_time_s, observed_at_iso)`. For uploaded files `video_time_s = frame_index / fps` and `observed_at = recorded_at + video_time` (or now-based if no recording time). For live sources `video_time_s` is monotonic seconds since start and `observed_at` is the wall clock.
2. `video_upload.py` (all three sockets): create a `FrameClock` per session; pass `video_time_s` to the safety layer.
3. The safety layer uses only its own `FrameClock`. (The identity tracker and hand-off registry that compared against wall-clock times were removed, so nothing else reads the clock.)
4. **Seek and rewind:** a seek sets `frame_idx = target_f` (`video_upload.py:750-754`), so video time can jump backwards. On any seek while safety is on, call `safety_engine.reset(reason="SEEK")`: open visits are closed as `UNVERIFIABLE`, evidence windows and zone state are cleared, and the clock restarts from the new position. Test: seek back 10 s produces no duplicate decisions and no negative dwell.
5. **Worker key:** always `trk-<track_id>`. There is no identity that can appear mid-visit, so the key cannot change under a visit.
6. Upload endpoint: accept an optional `recorded_at` form field (ISO time) so events land on the correct wall-clock time.

**Files:** new `safety/clock.py`; modify `video_upload.py`.
**Database:** none (but `observed_at` semantics are defined here and used in Steps 3–8).
**Tests:** `test_safety_clock.py` — same synthetic frames fed at different processing speeds give identical event times; live clock is monotonic; `recorded_at` offset applied; a seek triggers a reset.
**Done when:** an uploaded clip run twice at different speeds gives identical timestamps, seeking does not create duplicate decisions, and existing identity tests still pass unchanged.

---

### Step 3 — Local database (SQLite)

**Goal:** a durable local place for events and decisions, written without slowing the video loop.

**Do:**
1. New `backend/app/db/safety_models.py`: `ensure_safety_tables()` creating `safety_events` and `safety_decisions` (DDL in §24.3), modelled on `ensure_identity_table()` (CREATE TABLE IF NOT EXISTS, CHECK constraints, indexes). Call it from the `lifespan` in `main.py` next to `ensure_identity_table()`.
2. New `backend/app/db/safety_writer.py`: `SafetyWriter(camera_id, org_id, session_id)` with `add_event(...)`, `add_decision(...)`, `flush_sync()`, and `async persist_safety(writer)`; buffering, `asyncio.to_thread`, swallow-and-log failures, exactly like `ActivityLogWriter` / `IdentityEventWriter` (a database problem must never stop the video).
3. Event ids are UUIDs created at write time (`event_uid`), decision ids are UUIDs created when the decision is made — these same ids go to Postgres in Step 8, which is what makes the sync idempotent.
4. Local retention: a helper `purge_synced(days=7)` deleting already-synced rows; unsynced rows are never purged (except in standalone mode, where rows older than a configurable limit are purged so the file cannot grow without bound).
5. **Write contention:** the SQLite engine today has no WAL and no busy timeout (`backend/app/db/database.py:10-12`), and safety adds writers (events, decisions, sync updating `synced_at`). Enable `PRAGMA journal_mode=WAL` and `busy_timeout=5000` on connect (a SQLAlchemy `connect` event). Unlike telemetry, a **decision must not be lost**: on "database is locked" the writer keeps the decision in a bounded retry buffer and retries on the next flush, logging only if the buffer overflows.

**Files:** new `db/safety_models.py`, `db/safety_writer.py`; modify `main.py` (lifespan).
**Database:** SQLite `safety_events`, `safety_decisions` (§24.3).
**Tests:** `test_safety_writer.py` — CHECK constraints reject an unknown status/event type/confidence outside 0–1; duplicate `event_uid` is ignored; buffering flushes; a failing database does not raise into the caller; a decision that hits a locked database is written on the next flush; WAL mode is active.
**Done when:** unit tests pass and the backend starts with the tables created.

---

### Step 4 — Zone event generator

**Goal:** turn "person is in zone X this frame" into "entered X at 12.4 s, left X at 45.9 s".

**Do:** `backend/app/safety/zone_events.py` — class `ZoneEventTracker(config)` with `update(worker_zone: dict[worker_key, zone_id], seen_workers, t) -> list[Event]`.

Algorithm (per worker, `TRANSIT_ZONE` counts as "no zone"):

```
state = {current, entered_at, candidate, candidate_since, last_seen, lost_emitted}
each frame, for each seen worker with zone z:
    if z == state.current:           candidate = None; last_seen = t
    elif z != state.candidate:       candidate = z; candidate_since = t     # start timing a change
    elif t - candidate_since >= confirm(z):                                # persisted long enough
         emit EXITED_ZONE(current)   if current is a real zone
         emit ENTERED_ZONE(z)        if z is a real zone
         current = z; entered_at = candidate_since                         # back-dated: dwell not under-counted
each worker not seen this frame:
    if t - last_seen > lost_grace and current and not lost_emitted:  emit LOST_IN_ZONE; lost_emitted = True
    if t - last_seen > lost_ttl and current:                          emit EXITED_ZONE(reason=LOST); clear state
a worker seen again while lost_emitted and within lost_ttl:           emit REAPPEARED; lost_emitted = False
```

`confirm(z)` is `ENTER_CONFIRM_SECONDS` (0.7) when entering a real zone and `EXIT_CONFIRM_SECONDS` (1.0) when leaving to no zone. Dwell: `watch_dwell(zone_id, limit_s, warn_fraction)` registers limits (from Step 6/13 rules); the tracker emits `DWELL_WARNING` / `DWELL_EXCEEDED` once per visit. Suppress exact duplicate consecutive events.

**Key design points:**
- Events carry `worker_key`, `zone_id`, `video_time_s`, `observed_at`, `details` (e.g. `{"reason": "LOST"}`).
- The worker key is `f"trk-{track_id}"`.
- The tracker is pure logic: no database, no camera, no clock — takes `t` as an argument.

**Files:** new `safety/zone_events.py`.
**Database:** events are written through `SafetyWriter` (Step 3).
**Tests:** `test_zone_events.py` — straight walk through two zones (one ENTERED and one EXITED each); standing on a boundary with jitter (exactly one entry); a 2-second occlusion inside a zone (no exit, a REAPPEARED); 8-second disappearance (LOST then EXITED reason=LOST); two workers independent; dwell warning at 80% and exceeded at 100% exactly once; time-shift invariance (adding 1000 s to all timestamps gives the same events).
**Done when:** scenarios S6 and S10 of §14 pass.

---

### Step 5 — PPE detector in the live loop

**Goal:** every person on the live view carries a PPE status.

**Do:**
1. New `backend/app/safety/ppe_runner.py`: `PpeRunner(detector, evidence_tracker, every_n_frames=2)`. On each call: if it is a detection frame, run `PpeDetector.detect(frame)` on the **unblurred** frame; associate with `associate_frame(persons, ppe_dets, frame.shape)`; update the `PpeEvidenceTracker` per worker key with the current time; return per-worker `{status, helmet_conf, vest_conf, clear}`.
   - Running every second frame is deliberate: evidence is windowed, so detection at ~half the frame rate is enough and halves the cost. `every_n_frames` is configurable.
2. Integrate into `frame_pipeline.process_detections` through a new optional parameter `safety_context=None` (the earlier optional identity parameters were removed, so this is the only extension point): when `None`, behaviour is byte-for-byte unchanged. When present, the PPE step runs **before** the head-blur loop (blur is in-place, so anything needing real pixels must run first — the blur is in place, so anything needing real pixels runs first).
3. Entities gain a `ppe` object: `{"status": "COMPLIANT|NON_COMPLIANT|UNVERIFIABLE", "helmet": 0.91, "vest": 0.0, "clear": true}`.
4. `video_upload.py`: when `safety_enabled()`, construct the detector once per process (like `get_extractor()`), a tracker per session, and the `safety_context`. If the weights file is missing (`*.pt` and `ppe_training/runs/` are git-ignored, so a fresh clone has none) log one clear error and run with PPE **unavailable** — every PPE verdict is `UNVERIFIABLE` with reason "PPE model not loaded" — instead of letting `FileNotFoundError` abort the session.
6. **Resolution:** frames are shrunk to `inference_width` (640 px) before pose (`video_upload.py:771-774`), but a worker must be ≥ 90 px tall to count as clear (`ppe_evidence.py:52`). At CCTV distance most people would then never be "clear" and every verdict would be `UNVERIFIABLE`. Keep a reference to the full-resolution frame, run the PPE model on it (or on a 1280-px copy), and scale person boxes up to match. Measure the clear-frame rate at near/mid/far distance in E2.
7. **Do not block the event loop:** pose inference already runs synchronously inside the async socket handler (`video_upload.py:778`). Run pose + PPE for a frame in one `await asyncio.to_thread(...)` call so other sockets, the sync loop and the writers keep running.
5. Frontend `lib/overlay.js` / the live player: draw a small badge on each person's box (green / red / grey) with a tooltip listing helmet and vest state.

**Files:** new `safety/ppe_runner.py`; modify `frame_pipeline.py`, `video_upload.py`, `frontend/app/lib/overlay.js`, `VideoCanvasPlayer.jsx`.
**Database:** none.
**Tests:** `test_ppe_runner.py` with a fake detector — status transitions; PPE detection skipped on off-frames; when disabled the entity payload equals the old payload (compare JSON).
**Measure:** frames per second with pose + PPE (identity off, then on) on the named demo GPU; record it in the evaluation results (§14.E). If below target, increase `every_n_frames` or switch identity off. A GPU is required: on CPU two YOLOv8m models give roughly 1–3 FPS, which is not usable live (uploaded files still work, just slower than real time).
**Done when:** the live view shows a PPE badge per person and the measured FPS is recorded.

---

### Step 6 — Rules and the first decision (R1, PPE before entry)

**Goal:** the first end-to-end safety verdict, with a stored reason.

**Do:**
1. `backend/app/safety/schemas.py`: dataclasses `Rule`, `Decision`, `Evidence`; `Decision.status` is one of the three constants; each decision has `id` (uuid), `rule_id`, `rule_type`, `severity`, `camera_id`, `zone_id`, `worker_key`, `reason`, `confidence`, `evidence` (dict), `thresholds` (config snapshot), `video_time_s`, `observed_at`, `snapshot_requested`.
2. `backend/app/safety/rules.py`: parse and validate rule rows (params schema in §24.6); `load_rules(org_id, camera_zones)` reads `safety_rules` from Postgres with the service role (same style as `load_seat_map`), resolves each rule's zone UUID to the local zone slug via `zones.slug` (migration 023), and caches per session; **fallback:** if Postgres is unavailable or the organisation has no rules, use a built-in default — "R1 AT_ENTRY on every `RESTRICTED` zone, requires helmet and vest" — so the demo works with zero configuration.
3. `backend/app/safety/compliance_engine.py`: `ComplianceEngine(rules, ppe_tracker, config)` with
   `on_event(event) -> list[Decision]` and `on_tick(t) -> list[Decision]`.
   For R1 `AT_ENTRY`:
   - On `ENTERED_ZONE` into a zone targeted by an R1 rule, open a **pending decision** for that worker.
   - On each tick, ask the PPE tracker for the worker's verdict (it already holds frames from *before* the entry, because the evidence window starts at first sighting).
     - `COMPLIANT` → finalise `COMPLIANT` (reason: "helmet and vest seen in 29/30 clear frames").
     - `NON_COMPLIANT` → finalise `POTENTIAL_VIOLATION` (reason names the missing item and the frame counts).
     - `UNVERIFIABLE` → keep waiting until `ENTRY_EVIDENCE_WAIT_SECONDS` (4 s); at the deadline finalise `UNVERIFIABLE` with the reason from the evidence tracker.
   - Decision confidence: `min` over required items of the supporting fraction (`seen_fraction` for present items, `empty_fraction` for absent ones); `0.0` for `UNVERIFIABLE` (no claim made). A decision whose confidence is below the rule's `minEvidenceConfidence` is downgraded to `UNVERIFIABLE`.
   - Exactly one decision per worker per visit per rule.
4. `backend/app/safety/engine.py`: `SafetyEngine` — owns `ZoneEventTracker`, `PpeRunner`, `ComplianceEngine`; `process(entities, frame, t) -> SafetyResult(events, decisions, snapshot_requests, per_worker_flags)`. The upload/live sockets call it once per frame after `process_detections`, and hand events/decisions to `SafetyWriter`.

**Files:** new `safety/schemas.py`, `rules.py`, `compliance_engine.py`, `engine.py`; modify `video_upload.py`.
**Database:** writes to `safety_events` / `safety_decisions` through the writer (Step 3). The Postgres `safety_rules` table is created in Step 7; until then the default rule is used.
**Tests:** `test_compliance.py` — scenarios S1–S5 of §14.2.B driven with synthetic detections through the real engine: equipped → `COMPLIANT`; no helmet → `POTENTIAL_VIOLATION`; no vest → `POTENTIAL_VIOLATION`; far-away worker → `UNVERIFIABLE`; flicker → `UNVERIFIABLE`; two workers close together → judged separately; one decision per visit; confidence below the rule floor downgrades. Also invalid rule params are rejected by `rules.py`.
**Done when:** a staged clip (or synthetic run) yields the right status with a readable reason and the numbers in `evidence`.

---

### Step 7 — Postgres schema, RLS and the review function

**Goal:** production tables the dashboard can read safely, with the browser unable to forge or alter a decision.

**Do:**
1. Write `frontend/prisma/sql/025_safety.sql` (full text in §24.4): tables `safety_rules`, `safety_events`, `safety_decisions`, `corrective_actions`; the `zones.safetyType` column; the `SAFETY_SUMMARY` report kind; CHECK constraints; indexes; `updatedAt` triggers; RLS policies; grants; the `review_safety_decision()` function; `purge_safety_data()`.
2. Add the four models and the `Zone.safetyType` field to `frontend/prisma/schema.prisma` (§24.5) plus the back-relations on `Organisation`, `Camera`, `Zone`, `Profile`; confirm that `Zone.slug` (added by migration 023) is in the Prisma model, add it if missing; run `npx prisma generate`.
3. Apply the SQL to the development Supabase project (the repo applies numbered SQL files by hand after `prisma migrate`).
4. Verify RLS by measurement, as migration 003 did: using real VIEWER / MANAGER / ADMIN sessions —
   - VIEWER can `SELECT` decisions and rules, cannot `INSERT` a rule, cannot `UPDATE` a decision, cannot call `review_safety_decision()` successfully;
   - MANAGER can insert and update rules, soft-delete them through `soft_delete_safety_rule()` (and a direct UPDATE of `deletedAt` fails), and call the review function;
   - nobody authenticated can insert or update `safety_decisions` / `safety_events` directly;
   - a MANAGER can open a corrective action only on a `CONFIRMED` decision of their own organisation;
   - a user from another organisation sees nothing.
5. Record the matrix in `frontend/prisma/README.md` (or the migration header) the way earlier migrations do.

**Files:** new `025_safety.sql`; modify `schema.prisma`, `prisma/README.md`.
**Database:** §24.4.
**Tests:** `backend/tests/test_safety_rls.py` (live-database script like `test_zone_isolation.py`, skips with a clear message when no Supabase is configured).
**Done when:** the matrix in §24.12 passes.

---

### Step 8 — Sync from SQLite to Postgres

**Goal:** decisions made by the pipeline show up in Postgres, exactly once, without ever overwriting a human review.

**Do:** new `backend/app/db/safety_sync.py` and a background loop started in `main.py`'s `lifespan` next to `run_aggregator_loop` (60 s tick; also triggered at the end of a session, like `aggregate_after_session`):

1. Select up to 500 rows with `synced_at IS NULL` from each local table, oldest first.
2. Resolve `org_id` (already UUID text), camera UUID (by name), zone UUID (by `(cameraId, slug)`), and `rule_id` — reusing the logic and cache of `minute_aggregator._resolve_ids()`. Rows whose camera or zone cannot be resolved stay unsynced and are counted in the log (never invented — same rule as today).
3. POST to PostgREST with the service key:
   - events: `.../safety_events?on_conflict=eventUid` with `Prefer: resolution=merge-duplicates`;
   - **decisions: `.../safety_decisions?on_conflict=id` with `Prefer: resolution=ignore-duplicates`** — decisions are immutable; `ignore-duplicates` guarantees a re-sent decision can never overwrite the review fields a person has since set.
4. Mark rows `synced_at` only on a 2xx response; on failure leave them and retry on the next tick (best-effort, never raises into the video loop).
5. Standalone mode: no Supabase configured → the loop does nothing and the local tables simply accumulate (then `purge_synced` never deletes them).

**Files:** new `db/safety_sync.py`; modify `main.py`.
**Database:** uses §24.8.
**Tests:** `test_safety_sync.py` with a stub PostgREST server — mapping, batching, idempotent re-send, unmappable rows retained, ignore-duplicates header on decisions, 5xx leaves rows unsynced.
**Done when:** running a clip makes the decision visible in the Postgres table via the Supabase table editor, and running the sync twice creates no duplicates.

---

### Step 9 — Evidence snapshots

**Goal:** every flagged event has a blurred picture and an explanation.

**Do:**
0. **Face-only blur first.** Add `PrivacyAnonymizer.blur_face_keypoints(frame, keypoints, bbox)`: build the face box from the nose, eye and ear keypoints (confidence > 0.3), pad it ~20%, and blur only that, so the helmet crown above and the vest below stay visible. If fewer than two face keypoints are confident, fall back to the existing head-box blur and set `evidence.helmet_region_obscured = true`. When `SAFETY_ENABLED=true`, blur is forced on and uses this function. Test: on a synthetic frame the helmet region's pixels are unchanged and the face region's variance drops.
1. New `backend/app/safety/snapshots.py`: `save_snapshot(frame_blurred, decision, ppe_dets, person_box, zone_polygon, org_id) -> ref`.
   - **The snapshot does its own blur.** The session blur can be off (`PRIVACY_BLUR_DEFAULT=false`, viewer toggle) or be the old head-box blur, so the saved image must never rely on it. The engine returns `snapshot_requests` carrying the **unblurred** frame copy and the person's keypoints, taken before the pipeline's blur loop; `save_snapshot` applies `blur_face_keypoints` to every person in its own copy, whatever the session setting, then writes the file.
   - Draw overlays: person box, helmet/vest boxes with confidences, zone outline, and a text banner (rule, status, time).
   - JPEG quality ~75, path `backend/evidence/<org_id>/<decision_id>.jpg`; `ref` is the relative path stored in `snapshot_ref`.
2. Only for `POTENTIAL_VIOLATION` (and man-down in Step 14); `UNVERIFIABLE` events optionally (`SAFETY_SNAPSHOT_UNVERIFIABLE`, default off).
3. Organisation switch: `organisations."safetyEvidenceEnabled"` (created by `025_safety.sql`, default true; read by the pipeline when the session's organisation is resolved); when off, no file is written and `snapshot_ref` is null.
4. Retention: `purge_evidence(days)` deletes expired files, nulls `snapshot_ref` locally, and — because the decision sync uses `ignore-duplicates` and will never update an existing row — sends one explicit service-role `PATCH /safety_decisions?id=in.(...)` setting `snapshotRef = null` in Postgres. The snapshot endpoint also returns 410 Gone when the file no longer exists, so a stale reference is harmless. Scheduled from the aggregator loop (default 7 days).
5. Serving: `GET /api/v1/safety/decisions/{id}/snapshot` (Step 11) checks org membership + `safety.evidence.view`, streams the file with `Cache-Control: no-store`, and writes an audit row.

**Files:** new `safety/snapshots.py`; modify `video_upload.py`, `main.py`.
**Database:** `snapshot_ref` column (already in §24).
**Tests:** `test_snapshots.py` — the face region is blurred and the helmet region is not (compare region variance before/after); no file for `COMPLIANT`; path cannot escape the evidence directory; expiry deletes files.
**Done when:** a flagged event produces an image you can open from the dashboard, in which the face is blurred but a reviewer can still see whether a helmet and vest are worn.

---

### Step 10 — Alerts and live notification

**Goal:** a violation appears on screen within seconds, once, and stays until a person acts on it.

**Design decision:** in the safety product an **alert is a `POTENTIAL_VIOLATION` decision that is still `PENDING` review**. The occupancy `alerts` / `alert_rules` tables and the old alerts panel no longer exist (migration 024), so there is one source of truth: the decision. The new alerts panel reads decisions.

**Do:**
1. **De-duplication:** a decision is created once per `(rule, worker, visit)`. Continuous rules (R2, R5, R6) use an *episode*: one decision per continuous condition, with a minimum gap (`SAFETY_REALERT_AFTER_S`, default 60 s) before a new one for the same `(rule, zone, worker)`. Implemented in the engine, not in the database.
2. **Live push:** the processing WebSocket payload gains a `safety` object `{decisions: [...new this frame], open_count}` and per-person flags; the live dashboard shows a banner/toast (severity colour) and plays no sound by default.
3. **Open count** for the header badge comes from the API (Step 11) — decisions with `status='POTENTIAL_VIOLATION' AND reviewState='PENDING'`.
4. Severity comes from the rule (`INFO | WARNING | CRITICAL`); man-down is always `CRITICAL`.

**Files:** modify `safety/engine.py`, `video_upload.py`, live player; new `frontend/app/dashboard/SafetyAlertsPanel.jsx` (shown on the overview).
**Database:** none beyond §24.
**Tests:** `test_alert_dedup.py` — standing in a zone for a minute yields one decision; leaving and re-entering yields a second; episode re-alert only after the gap.
**Done when:** one violation produces one banner, and the count in the header goes up by exactly one.

---

### Step 11 — Safety API and permissions

**Goal:** the dashboard has secure endpoints; the Python and JavaScript permission tables agree.

**Do:**
1. `backend/app/api/permissions.py` and `frontend/app/lib/permissions.js`: add `safety.view` (ADMIN, MANAGER, VIEWER), `safety.review` (ADMIN, MANAGER), `safety.rules.edit` (ADMIN, MANAGER), `safety.evidence.view` (ADMIN, MANAGER). Add a parity test (new — no permission test exists yet): parse both tables and assert they list the same capabilities for the same roles.
2. New `backend/app/api/routers/safety.py` (prefix `/api/v1/safety`, registered in `main.py`) with the endpoints of §10. Organisation always comes from `resolve_org_role(token)` — never a parameter. Reads that the dashboard can do directly through Supabase (rules, decisions) stay in the Next.js server queries; the Python router exists for what only the backend can do (snapshot streaming, summary aggregates, export, rule validation).
3. Rule validation endpoint `POST /rules/validate`: dry-run a rule against the camera's zones and return errors before saving.
4. Audit rows for evidence views and exports. **New work:** the Python backend has never written to `audit_logs` (today only Next.js server actions do), so add a small `write_audit(org, actor, action, target)` helper that inserts through PostgREST with the service key. Rule create/update audit rows are written by the Next.js server actions (existing pattern); rule delete is audited inside `soft_delete_safety_rule()`.
5. **No face naming to turn off:** the enrolment router, door-camera matching and enrolment screens no longer exist (§13). Test: the OpenAPI paths contain no `employees` or `enrol` route.

**Files:** new `routers/safety.py`; modify `permissions.py`, `permissions.js`, `main.py`.
**Database:** reads §24 tables; audit rows into the existing `audit_logs`.
**Tests:** `test_safety_api.py` — 401 without a token, 403 for a VIEWER on write/evidence, org isolation (org A cannot read org B's decision or snapshot), snapshot path traversal rejected.
**Done when:** the endpoint matrix in §10 behaves as specified for all three roles.

---

### Step 12 — Dashboard screens

**Goal:** a safety officer can configure, monitor, review and understand everything.

Build in this order; each screen follows the existing patterns (server component `page.jsx` + client `*Screen.jsx` + `actions.js` server actions; three-layer permission checks; queries in `frontend/app/lib/safety/queries.js` modelled on `lib/analytics/queries.js`).

| Order | Screen (route under `/dashboard/safety/`) | Contents |
|---|---|---|
| 12.1 | **Rules** (`rules/`) | List, create, edit, enable/disable, delete (calls `soft_delete_safety_rule()`); parameter forms per rule type (§24.6) with live validation via `/rules/validate`. *(T3 — until then, rules are seeded in SQL.)* |
| 12.2 | **Events** (`events/`) | Filter by status, rule, zone, camera, date; table with status chip, reason, confidence, review state |
| 12.3 | **Event detail** (`events/[id]/`) | Snapshot (through the audited endpoint), reason, the numbers (`evidence`), the thresholds used, timeline of that worker's zone visits, review controls |
| 12.4 | **Review queue** (`review/`) | Pending `POTENTIAL_VIOLATION`s oldest-first; Confirm / Dismiss with reason code + note via `review_safety_decision()` |
| 12.5 | **Overview** (`page.jsx`) | Counts (compliant / flagged / unverifiable), open alerts, reviewed vs pending, top rule, trend over the selected range |
| 12.6 | **Live** (`live/`) | Existing live player with PPE badges, zone overlay, banner for new decisions |
| 12.7 | **Corrective actions** (`actions/`) | From a confirmed decision: "Create action" (title, assignee, due date). List filtered by status / assignee / overdue; move `OPEN → IN_PROGRESS → CLOSED` with a closing note; overdue highlighted |
| 12.8 | Navigation | Add "Safety" to the dashboard shell; header badge with open-alert count and overdue-action count |

**Files:** new `frontend/app/dashboard/safety/**`, `frontend/app/lib/safety/queries.js`; modify `DashboardShell.jsx`.
**Database:** reads `safety_rules`, `safety_events`, `safety_decisions`, `corrective_actions`; writes rules and corrective actions (RLS-permitted for managers) and reviews (only through the RPC).
**Tests:** role matrix by hand and by script: VIEWER sees data but no edit/review controls and direct calls fail; MANAGER can do everything; build passes (`npm run build`).
**Done when:** the demo storyline steps 1–3 and 8 of §15 can be performed entirely from the UI.

---

### Step 13 — More rules: R2, R3, R4

**R3 — maximum time in a zone**
- Parameters: `max_seconds`, `warn_fraction` (0.8).
- On reaching `warn_fraction` of the limit, write a `DWELL_WARNING` **event** only (no decision, nothing to review). On reaching the limit, write a `DWELL_EXCEEDED` event and create a `POTENTIAL_VIOLATION` **decision** with the measured dwell. A visit that ends within the limit creates a `COMPLIANT` decision. Dwell runs from the confirmed entry (back-dated to when the candidate began) to the confirmed exit, using video time. If the worker is lost and does not return within `LOST_TTL`, close the visit as `UNVERIFIABLE` ("cannot say they stayed").

**R2 — PPE must remain compliant while inside**
- While the worker is inside the target zone, if the PPE verdict is `NON_COMPLIANT` continuously for `grace_seconds` (3 s) → `POTENTIAL_VIOLATION` (one per episode). If evidence becomes unclear while inside, **no decision is made** — absence of evidence is never an accusation.

**R4 — required exit**
- Parameters: `exit_zone`, `max_seconds_to_exit`. On a confirmed `EXITED_ZONE` from the target zone start a timer. If the worker appears in the exit zone in time → `COMPLIANT`. If seen confidently in another real zone first, or the timer expires with the worker still visible → `POTENTIAL_VIOLATION`. If the worker is simply lost from view → `UNVERIFIABLE`.

**Files:** modify `compliance_engine.py`, `rules.py`, dashboard rule forms.
**Database:** none (rule types already allowed by the CHECK in §24.4).
**Tests:** extend `test_compliance.py` with S7 (dwell), S8 (wrong exit / lost), and R2 episodes; boundary behaviour around the limit (limit−1 s vs limit+1 s).
**Done when:** scenarios S7 and S8 pass and the new rule forms save and load.

---

### Step 14 — Man-down

**Goal:** a collapsed, still person raises a `CRITICAL` alert within seconds, without alarming on sitting or bending.

**Do:** `backend/app/safety/man_down.py` — `ManDownDetector(config)` with `update(worker_key, keypoints, bbox, posture, speed, t) -> Optional[Decision]`. Keypoints come from `detections` (passed to the engine alongside entities, §8.10), not from entities, which do not carry them.

Per worker, each frame, compute:
1. `torso_angle` = angle of the (shoulder-midpoint → hip-midpoint) vector from vertical, only if both shoulders and both hips have keypoint confidence > 0.3.
2. `aspect` = box width / height.
3. `is_down_pose` = `torso_angle > DOWN_ANGLE (60°)` **or** `aspect > 1.2` (the second only when keypoints are weak).
4. `is_still` = recent box-centre motion below `STILL_SPEED`, in person-heights per second (`get_recent_motion_speed` is pixels per frame per `track_id`: divide by box height, multiply by fps).
5. Exclusions: posture `SITTING` **only with an upright torso** (`torso_angle < 45°`) — the existing classifier labels a person lying flat as `SITTING` (`pose_estimator.py:311-316, 359-360`), so a blanket exclusion would suppress every real fall; person partly cut by the frame edge; worker in a zone flagged "lying allowed".

State machine per worker: `UP → CANDIDATE_DOWN` when `is_down_pose and is_still`; `CANDIDATE_DOWN → DOWN` after `MIN_SECONDS_DOWN` (5 s) continuously; `DOWN` raises a `MAN_DOWN` decision (`POTENTIAL_VIOLATION`-class, `CRITICAL`, snapshot requested). Any recovery (upright pose or movement) resets to `UP`. If keypoint confidence is too low to judge the orientation for most of the window → `UNVERIFIABLE` (warning, not an emergency). One alert per episode.

**Files:** new `safety/man_down.py`; modify `engine.py`, rule form (R6 params: `min_seconds_down`, optional zone).
**Database:** none; a decision row with `ruleType='MAN_DOWN'`.
**Tests:** `test_man_down.py` — synthetic keypoint sequences: standing, sitting, crouching, bending to pick up, reaching under, lying still **with the posture label `SITTING`** (must still alert — guards the classifier quirk), lying then recovering within 3 s (must not), occluded keypoints (UNVERIFIABLE). Then **staged clips**: record the hard negatives and positives for §14.C and report recall, false alarms per hour, and time-to-alert.
**Done when:** meets the §14.C targets on the staged clips with the camera placement documented.

---

### Step 15 — Lone worker and occupancy

**Do:** `backend/app/safety/occupancy.py` — `OccupancyMonitor(config)`.
1. Each frame, per zone targeted by an R5 rule, count the **clear** people whose containment is that zone (reuse the per-zone counts from `process_detections`; do not recount).
2. Keep a short history; use the **median** of the last `window` (2 s) so single-frame flicker cannot change the count.
3. `min_people` violation: median count satisfies `1 ≤ count < min_people` for longer than `grace_seconds` (10 s), **and** the zone has not just become empty (an empty zone is not a lone-worker case) → `POTENTIAL_VIOLATION` (`LONE_WORKER`).
4. `max_people` violation: median above `max_people` for the grace → `POTENTIAL_VIOLATION` (`OVER_CAPACITY`).
5. If the fraction of clear detections in the zone is below a threshold (crowded or overlapping people) → `UNVERIFIABLE` instead of a count-based accusation.
6. One decision per episode (Step 10 de-duplication).

**Files:** new `safety/occupancy.py`; modify `engine.py`, rule forms.
**Database:** none (`ruleType='OCCUPANCY'`).
**Tests:** `test_occupancy.py` — one person inside for 12 s (violation), two people (none), a person leaving so the zone goes empty (none), a one-frame detection dropout (none), crowded overlap (UNVERIFIABLE), over-capacity with sustained vs momentary excess.
**Done when:** scenario S9 passes and a hand-counted clip matches.

---

### Step 16 — Reports and export

**Do:**
1. Backend `GET /api/v1/safety/summary` and `/export?format=csv|pdf&from=&to=` using the existing `report_generator.py` pattern.
2. Report sections: compliance rate; flags by rule / zone / camera / hour; unverifiable rate; **confirmed / dismissed / pending** counts; missing-helmet and missing-vest counts; average and maximum time in restricted zones; man-down incidents; lone-worker incidents; corrective actions (opened, closed, overdue, average days to close); per-shift audit list (ordered events and decisions).
   Generated reports are recorded in the existing `reports` table with `kind = SAFETY_SUMMARY`.
3. **Honest categories:** a report never labels an unreviewed flag as a "violation". Columns are `Flagged by system`, `Confirmed by reviewer`, `Dismissed`, `Insufficient evidence`.
4. Frontend `ReportExport.jsx` gets the safety option; export writes an audit row.

**Files:** modify `routers/safety.py`, `report_generator.py`, `ReportExport.jsx`; new `safety/reports` page.
**Database:** aggregate SQL over `safety_decisions` (add a covering index if the 30-day query is slow — see §24.4 indexes).
**Tests:** `test_safety_report.py` — counts match a seeded set; pending flags are not counted as confirmed; CSV columns stable.
**Done when:** a seeded dataset produces a report whose numbers you can verify by hand.

---

### Step 17 — Evaluation

**Do:**
1. **Own-footage test, baselines and sweep:** already produced by evaluation tracks E1–E3 (weeks 1–5, see §23.1). Here they are re-run once on the final code and written up: §14.A table with N and rule-of-three bounds, §14.G baseline table and error-vs-coverage plot. Store in `ppe_training/reports/` and `docs/SAFETY_EVALUATION.md`.
2. **Scenario suite:** `backend/eval/safety_scenarios.py` driving S1–S10 through the real engine with scripted detections (scripted detections fed to the real engine), output `backend/eval/results/safety_evaluation.json`, and an MD summary (`docs/SAFETY_EVALUATION.md`).
3. **Man-down and occupancy** measurements per §14.C and §14.D.
4. **System:** FPS and alert latency per §14.E.
5. **Review loop:** run the staged clips, have someone confirm/dismiss every flag in the UI, and compute human-judged precision and dismissal reasons (§14.F).
6. Build a new Evaluation screen under `frontend/app/dashboard/evaluation/` showing the safety metrics.

**Files:** new `backend/eval/safety_scenarios.py`, `docs/SAFETY_EVALUATION.md`, and a new `frontend/app/dashboard/evaluation/` screen (the old Accuracy screen was deleted).
**Database:** reads the decisions created by the runs.
**Done when:** every metric in §14 has a recorded number and the document says plainly where the system is wrong or abstains.

---

### Step 18 — Hardening and demo

**Do:**
1. **Performance:** profile the loop; tune `every_n_frames`, identity on/off; confirm the target FPS with all features enabled.
2. **Robustness:** camera disconnect mid-session (open visits closed as `UNVERIFIABLE`), duplicate sessions, very long sessions (memory bounds on evidence windows and event state), restart recovery (unsynced rows picked up).
3. **Retention jobs:** `purge_synced`, `purge_evidence`, Postgres `purge_safety_data()` on the schedule used by migration 011.
4. **Docs:** update `README.md` and `docs/DOCUMENTATION.md` (it is still the old anonymous-product description), the architecture memory/notes, and add the new environment variables to `.env.example`.
5. **Demo:** build the taped layout, rehearse the storyline of §15 at least three times, record fallback clips of every scenario, prepare the printed results table.
6. **Final regression:** all test files pass; backend boots; frontend builds; `SAFETY_ENABLED=false` still behaves like the old product.

**Done when:** the full storyline runs end to end twice in a row, and the fallback clips work offline.

---

### Optional steps (only after Step 17)

- **Opt-A Unsafe movement (R7):** speed normalised by person height, sustained over a few seconds inside a `WALK_ONLY` zone (requires adding that zone type in Step 1's list).
- **Opt-B External notifications:** an outbound webhook (email/Telegram) for `CRITICAL` decisions, destination per organisation, no image in the message, retry with backoff, off by default.
- **Opt-C Cross-camera journeys:** see §19; a separate package reading the same events, reported separately.

---

## 24. Database design in full

### 24.1 Design principles

1. **Two stores, one id.** The pipeline writes SQLite; the dashboard reads Postgres. Every event and decision carries a UUID created at the source, so syncing is idempotent.
2. **Measured data is read-only to browsers.** `safety_events` and `safety_decisions` have SELECT policies only. They are written by the pipeline through the service role. A browser cannot fabricate or alter a decision.
3. **Review is the one human write, and it goes through one function.** `review_safety_decision()` updates only the review columns, checks the caller's role, and writes the audit row in the same transaction. There is no UPDATE policy on decisions at all.
4. **Configuration is writable by managers.** `safety_rules` follows the same policy shape as `cameras` / `zones` (manager-writable, org-readable).
5. **Honesty is enforced by constraints**, as in the older identity tables: a `COMPLIANT` decision cannot require review; a reviewed decision must say who and when; confidence stays in 0–1; status/event vocabularies are CHECK-constrained.
6. **Vocabularies are `VARCHAR + CHECK`, not Postgres enums**, so adding a rule type later is a one-line constraint change instead of `ALTER TYPE` (the precedent is the `method` column of the older identity table).
7. **Tenancy on every table** (`"orgId"` present even where reachable by a join) so policies are decided from the row alone — the rule at the top of `schema.prisma`.
8. **No names, no biometrics, no images in the database.** Workers are `workerKey`s; snapshots are files referenced by path.

### 24.2 Entity relationship overview

```
organisations ─┬─< cameras ─┬─< zones
               │            │      │
               │            │      └──────────────┐
               ├─< safety_rules  (zoneId, cameraId)│
               │        │                          │
               │        └──────────────┐           │
               ├─< safety_events ──────┼──(zoneId, cameraId)
               │                       │
               ├─< safety_decisions ───┘   ruleId → safety_rules (SET NULL)
               │         │                 reviewedById → profiles (SET NULL)
               │         └─ snapshotRef → file under backend/evidence/<org>/<id>.jpg
               └─< corrective_actions ── decisionId → safety_decisions (CASCADE)
                                         assignedToId / closedById → profiles (SET NULL)
zones.safetyType (new column)   reports.kind = SAFETY_SUMMARY (new enum value)
profiles ──< audit_logs  (review, rule edits, evidence views, exports)
```

### 24.3 Local SQLite DDL (created by `ensure_safety_tables()`)

```sql
CREATE TABLE IF NOT EXISTS safety_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid     TEXT    NOT NULL UNIQUE,          -- uuid4, idempotent sync key
    org_id        TEXT,
    camera_id     TEXT    NOT NULL,
    zone_id       TEXT,                              -- local slug, e.g. 'zone_ab12cd'
    worker_key    TEXT    NOT NULL,                  -- 'trk-<id>'
    session_key   TEXT,                              -- the pipeline's session_id for this video run
    event_type    TEXT    NOT NULL,
    video_time_s  REAL    NOT NULL,
    observed_at   TEXT    NOT NULL,                  -- ISO-8601 UTC
    details       TEXT,                              -- JSON
    synced_at     TEXT,
    created_at    TEXT    NOT NULL,
    CHECK (event_type IN ('ENTERED_ZONE','EXITED_ZONE','LOST_IN_ZONE','REAPPEARED',
                          'DWELL_WARNING','DWELL_EXCEEDED','PPE_VERDICT_CHANGED')),
    CHECK (video_time_s >= 0)
);
CREATE INDEX IF NOT EXISTS ix_se_org_observed    ON safety_events (org_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_se_camera_observed ON safety_events (camera_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_se_worker          ON safety_events (worker_key, observed_at);
CREATE INDEX IF NOT EXISTS ix_se_unsynced        ON safety_events (id) WHERE synced_at IS NULL;

CREATE TABLE IF NOT EXISTS safety_decisions (
    id             TEXT PRIMARY KEY,                 -- uuid4, same id in Postgres
    org_id         TEXT,
    camera_id      TEXT NOT NULL,
    zone_id        TEXT,
    rule_id        TEXT,                             -- Postgres safety_rules.id, NULL for the built-in default rule
    rule_type      TEXT NOT NULL,
    severity       TEXT NOT NULL,
    worker_key     TEXT NOT NULL,
    session_key    TEXT,
    status         TEXT NOT NULL,
    reason         TEXT NOT NULL,
    confidence     REAL NOT NULL,
    evidence       TEXT NOT NULL,                    -- JSON (shapes in §24.7)
    thresholds     TEXT,                             -- JSON snapshot of the config used
    snapshot_ref   TEXT,
    video_time_s   REAL NOT NULL,
    observed_at    TEXT NOT NULL,
    synced_at      TEXT,
    created_at     TEXT NOT NULL,
    CHECK (status IN ('COMPLIANT','POTENTIAL_VIOLATION','UNVERIFIABLE')),
    CHECK (rule_type IN ('PPE_ENTRY','PPE_CONTINUOUS','MAX_DWELL','REQUIRED_EXIT',
                         'OCCUPANCY','MAN_DOWN','WALK_ONLY')),
    CHECK (severity IN ('INFO','WARNING','CRITICAL')),
    CHECK (confidence >= 0 AND confidence <= 1),
    -- UNVERIFIABLE makes no claim, so it must not carry a confidence:
    CHECK (status <> 'UNVERIFIABLE' OR confidence = 0)
);
CREATE INDEX IF NOT EXISTS ix_sd_org_observed ON safety_decisions (org_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_sd_status       ON safety_decisions (org_id, status, observed_at);
CREATE INDEX IF NOT EXISTS ix_sd_unsynced     ON safety_decisions (created_at) WHERE synced_at IS NULL;
```

Notes: `zone_id` / `camera_id` are the pipeline's text identifiers; the sync resolves them to UUIDs by name/slug. `evidence` is JSON because its shape differs by rule type.

### 24.4 Postgres migration `025_safety.sql`

Idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS), applied after `024_retire_productivity_tables.sql`, in the style of `020_identity.sql`. Verify helper names against 003/020 before applying (`public.user_org_ids()`, `public.manage_org_ids()`, `public.touch_updated_at()`, `public.audit_logs` columns).

```sql
-- ═══ TABLES ═════════════════════════════════════════════════════════════

-- Configuration: what to check. Writable by ADMIN and MANAGER.
CREATE TABLE IF NOT EXISTS public.safety_rules (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id)   ON DELETE CASCADE,
  name         VARCHAR(160) NOT NULL,
  type         VARCHAR(24)  NOT NULL,
  params       JSONB        NOT NULL DEFAULT '{}'::jsonb,
  severity     VARCHAR(10)  NOT NULL DEFAULT 'WARNING',
  "isEnabled"  BOOLEAN      NOT NULL DEFAULT TRUE,
  "minEvidenceConfidence" DOUBLE PRECISION NOT NULL DEFAULT 0.6,
  "createdById" UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "createdAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "deletedAt"  TIMESTAMPTZ(6),
  CONSTRAINT safety_rules_type_known CHECK (type IN
    ('PPE_ENTRY','PPE_CONTINUOUS','MAX_DWELL','REQUIRED_EXIT','OCCUPANCY','MAN_DOWN','WALK_ONLY')),
  CONSTRAINT safety_rules_severity_known CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CONSTRAINT safety_rules_confidence_range CHECK
    ("minEvidenceConfidence" >= 0 AND "minEvidenceConfidence" <= 1),
  -- every rule except a camera-wide MAN_DOWN must be anchored to a zone
  CONSTRAINT safety_rules_zone_required CHECK (type = 'MAN_DOWN' OR "zoneId" IS NOT NULL)
);

-- Measured: what the pipeline saw. SELECT only for browsers.
CREATE TABLE IF NOT EXISTS public.safety_events (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "eventUid"   VARCHAR(36) NOT NULL,                      -- from SQLite; makes sync idempotent
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  "workerKey"  VARCHAR(80) NOT NULL,
  "sessionKey" VARCHAR(40),                               -- pipeline session id (not an FK)
  "eventType"  VARCHAR(24) NOT NULL,
  "videoTimeS" DOUBLE PRECISION NOT NULL,
  "observedAt" TIMESTAMPTZ(6) NOT NULL,
  details      JSONB,
  CONSTRAINT safety_events_type_known CHECK ("eventType" IN
    ('ENTERED_ZONE','EXITED_ZONE','LOST_IN_ZONE','REAPPEARED',
     'DWELL_WARNING','DWELL_EXCEEDED','PPE_VERDICT_CHANGED')),
  CONSTRAINT safety_events_uid_unique UNIQUE ("eventUid")
);

-- Measured + reviewed: the compliance decisions. SELECT only; reviewed via RPC.
CREATE TABLE IF NOT EXISTS public.safety_decisions (
  id           UUID PRIMARY KEY,                           -- supplied by the pipeline
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  "ruleId"     UUID REFERENCES public.safety_rules(id) ON DELETE SET NULL,
  "ruleType"   VARCHAR(24) NOT NULL,
  severity     VARCHAR(10) NOT NULL,
  "workerKey"  VARCHAR(80) NOT NULL,
  "sessionKey" VARCHAR(40),
  status       VARCHAR(20) NOT NULL,
  reason       VARCHAR(500) NOT NULL,
  confidence   DOUBLE PRECISION NOT NULL,
  evidence     JSONB NOT NULL,
  thresholds   JSONB,
  "snapshotRef" VARCHAR(300),
  "videoTimeS" DOUBLE PRECISION NOT NULL,
  "observedAt" TIMESTAMPTZ(6) NOT NULL,
  -- review
  "reviewState"      VARCHAR(14) NOT NULL DEFAULT 'NOT_REQUIRED',
  "reviewedById"     UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "reviewedAt"       TIMESTAMPTZ(6),
  "reviewReasonCode" VARCHAR(24),
  "reviewNote"       VARCHAR(500),
  "createdAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT safety_decisions_status_known CHECK
    (status IN ('COMPLIANT','POTENTIAL_VIOLATION','UNVERIFIABLE')),
  CONSTRAINT safety_decisions_type_known CHECK ("ruleType" IN
    ('PPE_ENTRY','PPE_CONTINUOUS','MAX_DWELL','REQUIRED_EXIT','OCCUPANCY','MAN_DOWN','WALK_ONLY')),
  CONSTRAINT safety_decisions_severity_known CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CONSTRAINT safety_decisions_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT safety_decisions_unverifiable_no_claim CHECK (status <> 'UNVERIFIABLE' OR confidence = 0),
  CONSTRAINT safety_decisions_review_known CHECK
    ("reviewState" IN ('NOT_REQUIRED','PENDING','CONFIRMED','DISMISSED')),
  -- a compliant decision is never put in a review queue
  CONSTRAINT safety_decisions_compliant_no_review CHECK
    (status <> 'COMPLIANT' OR "reviewState" = 'NOT_REQUIRED'),
  -- a decision is reviewed exactly when it carries a review time. ("reviewedById"
  -- is deliberately not part of this: it becomes NULL through ON DELETE SET NULL
  -- when a reviewer's profile is removed, and that must stay legal.)
  CONSTRAINT safety_decisions_review_complete CHECK
    (("reviewState" IN ('CONFIRMED','DISMISSED')) = ("reviewedAt" IS NOT NULL))
);

-- Management: what was done about a confirmed violation. Writable by ADMIN and MANAGER.
CREATE TABLE IF NOT EXISTS public.corrective_actions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"        UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "decisionId"   UUID NOT NULL REFERENCES public.safety_decisions(id) ON DELETE CASCADE,
  title          VARCHAR(200) NOT NULL,
  description    VARCHAR(2000),
  priority       VARCHAR(10)  NOT NULL DEFAULT 'WARNING',
  status         VARCHAR(12)  NOT NULL DEFAULT 'OPEN',
  "assignedToId" UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "dueDate"      DATE,
  "createdById"  UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "closedById"   UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "closedAt"     TIMESTAMPTZ(6),
  "closingNote"  VARCHAR(1000),
  "createdAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT corrective_actions_status_known CHECK
    (status IN ('OPEN','IN_PROGRESS','CLOSED','CANCELLED')),
  CONSTRAINT corrective_actions_priority_known CHECK (priority IN ('INFO','WARNING','CRITICAL')),
  -- finished exactly when it carries a finish time
  CONSTRAINT corrective_actions_closed_complete CHECK
    ((status IN ('CLOSED','CANCELLED')) = ("closedAt" IS NOT NULL))
);

-- An action may only be opened for a violation a reviewer CONFIRMED, in the same org.
CREATE OR REPLACE FUNCTION public.corrective_action_check_decision()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.safety_decisions d
                  WHERE d.id = NEW."decisionId" AND d."orgId" = NEW."orgId"
                    AND d."reviewState" = 'CONFIRMED') THEN
    RAISE EXCEPTION 'corrective actions need a confirmed decision in the same organisation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS corrective_actions_check_decision ON public.corrective_actions;
CREATE TRIGGER corrective_actions_check_decision
  BEFORE INSERT OR UPDATE OF "decisionId", "orgId" ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.corrective_action_check_decision();

-- Zone safety type, separate from the existing ZoneType enum (left untouched).
ALTER TABLE public.zones ADD COLUMN IF NOT EXISTS "safetyType" VARCHAR(20);
ALTER TABLE public.zones DROP CONSTRAINT IF EXISTS zones_safety_type_known;
ALTER TABLE public.zones ADD CONSTRAINT zones_safety_type_known CHECK
  ("safetyType" IS NULL OR "safetyType" IN ('PPE_CHECKPOINT','APPROACH','RESTRICTED','EXIT','GENERAL'));

-- Safety reports reuse the existing reports table.
ALTER TYPE public."ReportKind" ADD VALUE IF NOT EXISTS 'SAFETY_SUMMARY';

-- ═══ INDEXES (the read paths, plus FK indexes so cascades do not scan) ══════
CREATE INDEX IF NOT EXISTS safety_rules_org_enabled_idx   ON public.safety_rules ("orgId", "isEnabled");
CREATE INDEX IF NOT EXISTS safety_rules_zone_idx          ON public.safety_rules ("zoneId");
CREATE INDEX IF NOT EXISTS safety_rules_camera_idx        ON public.safety_rules ("cameraId");
CREATE INDEX IF NOT EXISTS safety_rules_created_by_idx    ON public.safety_rules ("createdById");

CREATE INDEX IF NOT EXISTS safety_events_org_observed_idx   ON public.safety_events ("orgId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS safety_events_zone_observed_idx  ON public.safety_events ("zoneId", "observedAt");
CREATE INDEX IF NOT EXISTS safety_events_camera_idx         ON public.safety_events ("cameraId");
CREATE INDEX IF NOT EXISTS safety_events_worker_idx         ON public.safety_events ("workerKey", "observedAt");

CREATE INDEX IF NOT EXISTS safety_decisions_org_observed_idx  ON public.safety_decisions ("orgId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS safety_decisions_org_status_idx    ON public.safety_decisions ("orgId", status, "observedAt" DESC);
CREATE INDEX IF NOT EXISTS safety_decisions_pending_idx       ON public.safety_decisions ("orgId", "observedAt")
  WHERE status = 'POTENTIAL_VIOLATION' AND "reviewState" = 'PENDING';
CREATE INDEX IF NOT EXISTS safety_decisions_zone_idx          ON public.safety_decisions ("zoneId", "observedAt");
CREATE INDEX IF NOT EXISTS safety_decisions_camera_idx        ON public.safety_decisions ("cameraId");
CREATE INDEX IF NOT EXISTS safety_decisions_rule_idx          ON public.safety_decisions ("ruleId");
CREATE INDEX IF NOT EXISTS safety_decisions_reviewer_idx      ON public.safety_decisions ("reviewedById");
CREATE INDEX IF NOT EXISTS safety_decisions_session_idx       ON public.safety_decisions ("orgId", "sessionKey");

CREATE INDEX IF NOT EXISTS corrective_actions_org_status_idx ON public.corrective_actions ("orgId", status, "dueDate");
CREATE INDEX IF NOT EXISTS corrective_actions_decision_idx   ON public.corrective_actions ("decisionId");
CREATE INDEX IF NOT EXISTS corrective_actions_assignee_idx   ON public.corrective_actions ("assignedToId");
CREATE INDEX IF NOT EXISTS corrective_actions_created_by_idx ON public.corrective_actions ("createdById");
CREATE INDEX IF NOT EXISTS corrective_actions_closed_by_idx  ON public.corrective_actions ("closedById");

-- ═══ updatedAt maintained in the database (same reason as 020) ═══════════════
DROP TRIGGER IF EXISTS safety_rules_touch_updated_at ON public.safety_rules;
CREATE TRIGGER safety_rules_touch_updated_at BEFORE UPDATE ON public.safety_rules
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS safety_decisions_touch_updated_at ON public.safety_decisions;
CREATE TRIGGER safety_decisions_touch_updated_at BEFORE UPDATE ON public.safety_decisions
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS corrective_actions_touch_updated_at ON public.corrective_actions;
CREATE TRIGGER corrective_actions_touch_updated_at BEFORE UPDATE ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- ═══ ROW LEVEL SECURITY ═════════════════════════════════════════════════════
ALTER TABLE public.safety_rules     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.corrective_actions ENABLE ROW LEVEL SECURITY;

-- rules: everyone in the org reads; ADMIN + MANAGER write
DROP POLICY IF EXISTS safety_rule_select ON public.safety_rules;
CREATE POLICY safety_rule_select ON public.safety_rules FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);
DROP POLICY IF EXISTS safety_rule_insert ON public.safety_rules;
CREATE POLICY safety_rule_insert ON public.safety_rules FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
DROP POLICY IF EXISTS safety_rule_update ON public.safety_rules;
CREATE POLICY safety_rule_update ON public.safety_rules FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
-- no DELETE policy: rules are soft-deleted (deletedAt) so old decisions keep their reference.
-- A plain UPDATE setting "deletedAt" is REJECTED by RLS, because the row would become invisible
-- to safety_rule_select (measured for the same shape in 020_identity.sql:616-631). Soft delete
-- therefore goes through soft_delete_safety_rule() below, exactly like soft_delete_employee().

-- events and decisions: measured data. SELECT only. Written by the service role.
DROP POLICY IF EXISTS safety_event_select ON public.safety_events;
CREATE POLICY safety_event_select ON public.safety_events FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
DROP POLICY IF EXISTS safety_decision_select ON public.safety_decisions;
CREATE POLICY safety_decision_select ON public.safety_decisions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
-- deliberately NO insert / update / delete policy on events or decisions

-- corrective actions: everyone in the org reads; ADMIN + MANAGER create and update; no delete (cancel instead)
DROP POLICY IF EXISTS corrective_action_select ON public.corrective_actions;
CREATE POLICY corrective_action_select ON public.corrective_actions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
DROP POLICY IF EXISTS corrective_action_insert ON public.corrective_actions;
CREATE POLICY corrective_action_insert ON public.corrective_actions FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
DROP POLICY IF EXISTS corrective_action_update ON public.corrective_actions;
CREATE POLICY corrective_action_update ON public.corrective_actions FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

-- ═══ GRANTS ═════════════════════════════════════════════════════════════════
REVOKE ALL ON public.safety_rules, public.safety_events, public.safety_decisions,
              public.corrective_actions FROM anon;
GRANT SELECT, INSERT, UPDATE ON public.safety_rules, public.corrective_actions TO authenticated;
GRANT SELECT ON public.safety_events, public.safety_decisions TO authenticated;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON public.safety_rules, public.safety_events, public.safety_decisions,
         public.corrective_actions TO service_role;
  END IF;
END $$;

-- ═══ THE ONE HUMAN WRITE ON A DECISION: REVIEW ═════════════════════════════
CREATE OR REPLACE FUNCTION public.review_safety_decision(
  p_decision_id UUID, p_state TEXT, p_reason TEXT DEFAULT NULL, p_note TEXT DEFAULT NULL)
RETURNS public.safety_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE v_row public.safety_decisions;
BEGIN
  IF p_state NOT IN ('CONFIRMED','DISMISSED') THEN
    RAISE EXCEPTION 'review state must be CONFIRMED or DISMISSED';
  END IF;
  SELECT * INTO v_row FROM public.safety_decisions WHERE id = p_decision_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'decision not found'; END IF;
  IF v_row."orgId" NOT IN (SELECT public.manage_org_ids()) THEN
    RAISE EXCEPTION 'not permitted';
  END IF;
  IF v_row.status = 'COMPLIANT' THEN
    RAISE EXCEPTION 'compliant decisions are not reviewed';
  END IF;
  -- a confirmed decision that already has corrective actions cannot be flipped to DISMISSED
  IF v_row."reviewState" = 'CONFIRMED' AND p_state = 'DISMISSED' AND EXISTS
     (SELECT 1 FROM public.corrective_actions a
       WHERE a."decisionId" = v_row.id AND a.status <> 'CANCELLED') THEN
    RAISE EXCEPTION 'cancel the corrective actions before dismissing this decision';
  END IF;
  UPDATE public.safety_decisions
     SET "reviewState" = p_state, "reviewedById" = auth.uid(), "reviewedAt" = NOW(),
         "reviewReasonCode" = p_reason, "reviewNote" = LEFT(p_note, 500)
   WHERE id = p_decision_id
   RETURNING * INTO v_row;
  INSERT INTO public.audit_logs ("orgId", "actorId", action, "targetType", "targetId", metadata)
  VALUES (v_row."orgId", auth.uid(), 'safety.decision_reviewed', 'safety_decision',
          v_row.id::text, jsonb_build_object('state', p_state, 'reason', p_reason));
  RETURN v_row;
END;
$$;
REVOKE ALL ON FUNCTION public.review_safety_decision(UUID, TEXT, TEXT, TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.review_safety_decision(UUID, TEXT, TEXT, TEXT) TO authenticated;

-- ═══ RULE SOFT DELETE (same shape as soft_delete_employee in 020) ═══════════
CREATE OR REPLACE FUNCTION public.soft_delete_safety_rule(p_rule_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE v_org UUID;
BEGIN
  SELECT r."orgId" INTO v_org FROM public.safety_rules r
   WHERE r.id = p_rule_id AND r."deletedAt" IS NULL;
  IF v_org IS NULL THEN RAISE EXCEPTION 'rule not found' USING ERRCODE = 'no_data_found'; END IF;
  -- explicit authorisation: SECURITY DEFINER bypasses RLS
  IF v_org NOT IN (SELECT public.manage_org_ids()) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE public.safety_rules SET "deletedAt" = NOW(), "isEnabled" = FALSE WHERE id = p_rule_id;
  INSERT INTO public.audit_logs ("orgId", "actorId", action, "targetType", "targetId", metadata)
  VALUES (v_org, auth.uid(), 'safety.rule_deleted', 'safety_rule', p_rule_id::text, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION public.soft_delete_safety_rule(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.soft_delete_safety_rule(UUID) TO authenticated;

-- ═══ ORGANISATION SWITCH FOR EVIDENCE SNAPSHOTS (Step 9) ════════════════════
ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "safetyEvidenceEnabled" BOOLEAN NOT NULL DEFAULT TRUE;

-- ═══ RETENTION (scheduled like 011) ════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.purge_safety_data()
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  DELETE FROM public.safety_events e
   USING public.organisations o
   WHERE e."orgId" = o.id AND e."observedAt" < NOW() - make_interval(days => o."dataRetentionDays");
  -- decisions are the compliance record: kept twice as long as raw events
  DELETE FROM public.safety_decisions d
   USING public.organisations o
   WHERE d."orgId" = o.id AND d."observedAt" < NOW() - make_interval(days => 2 * o."dataRetentionDays");
$$;
REVOKE ALL ON FUNCTION public.purge_safety_data() FROM PUBLIC, anon, authenticated;
-- the grant to service_role must be stated or the job fails with "permission denied" (measured in 020)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT EXECUTE ON FUNCTION public.purge_safety_data() TO service_role;
  END IF;
  -- daily schedule, guarded so the file still applies without pg_cron (same pattern as 011/020)
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('purge-safety-data')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'purge-safety-data');
    PERFORM cron.schedule('purge-safety-data', '30 3 * * *', 'SELECT public.purge_safety_data()');
  END IF;
END $$;
```

Design points worth being able to explain:
- **Why decisions have no UPDATE policy and review goes through a function:** an UPDATE policy cannot restrict *which columns* change, so a manager could otherwise edit `status` or `evidence`. The function updates only the review columns and always audits.
- **Why the pipeline supplies the decision `id`:** so the same id lives in SQLite, Postgres, the snapshot filename and the audit log, and re-sending is harmless.
- **Why `reviewState` is `NOT_REQUIRED` for compliant decisions and `UNVERIFIABLE` decisions are reviewable:** a reviewer marking an `UNVERIFIABLE` as `CONFIRMED` means "this was in fact a violation the system could not call" — that is how we measure what abstentions hid.
- **Why rules are soft-deleted, and through a function:** old decisions keep a meaningful `ruleId`; the select policy hides deleted rules from the UI. Because of that same policy, an ordinary UPDATE that sets `deletedAt` is rejected by RLS, so deletion goes through `soft_delete_safety_rule()`, the pattern 020 already uses for employees.
- **Check the pg_cron syntax against 011 before applying** — copy the exact schedule/unschedule form that migration uses.
- **Why corrective actions are a separate table, not columns on the decision:** one confirmed violation can need several actions (re-brief the shift, fix a sign, replace a damaged helmet), and actions have their own owner, deadline and life cycle. The trigger stops an action being opened on an unconfirmed flag, so the system never "acts on" an accusation nobody checked.
- **Why `sessionKey` is text, not a foreign key:** the pipeline's session id is created locally and is not an `analysis_sessions` row; it is a grouping label for evaluation and reports.
- **`ALTER TYPE ... ADD VALUE`** cannot run inside a transaction block on older Postgres versions; if the SQL editor wraps the file in one, run that single line on its own.
- **Why the review CHECK only looks at `reviewedAt`:** `reviewedById` can legitimately become NULL after a reviewer's profile is removed (`ON DELETE SET NULL`); the audit log still records who did it.

### 24.5 Prisma models (added to `schema.prisma`)

```prisma
/// What to check. Writable by ADMIN/MANAGER (025_safety.sql).
model SafetyRule {
  id        String  @id @default(uuid()) @db.Uuid
  orgId     String  @db.Uuid
  cameraId  String? @db.Uuid
  zoneId    String? @db.Uuid
  name      String  @db.VarChar(160)
  /// PPE_ENTRY | PPE_CONTINUOUS | MAX_DWELL | REQUIRED_EXIT | OCCUPANCY | MAN_DOWN | WALK_ONLY
  type      String  @db.VarChar(24)
  params    Json    @default("{}")
  /// INFO | WARNING | CRITICAL
  severity  String  @default("WARNING") @db.VarChar(10)
  isEnabled Boolean @default(true)
  minEvidenceConfidence Float @default(0.6)
  createdById String? @db.Uuid
  createdAt DateTime  @default(now()) @db.Timestamptz(6)
  updatedAt DateTime  @updatedAt @db.Timestamptz(6)
  deletedAt DateTime? @db.Timestamptz(6)

  org       Organisation @relation(fields: [orgId], references: [id], onDelete: Cascade)
  camera    Camera?      @relation(fields: [cameraId], references: [id], onDelete: Cascade)
  zone      Zone?        @relation(fields: [zoneId], references: [id], onDelete: Cascade)
  createdBy Profile?     @relation(fields: [createdById], references: [id], onDelete: SetNull)
  decisions SafetyDecision[]

  @@index([orgId, isEnabled])
  @@index([zoneId])
  @@index([cameraId])
  @@index([createdById])
  @@map("safety_rules")
}

/// Raw zone transitions seen by the pipeline. Measured data: read-only to browsers.
model SafetyEvent {
  id         String   @id @default(uuid()) @db.Uuid
  eventUid   String   @unique @db.VarChar(36)
  orgId      String   @db.Uuid
  cameraId   String   @db.Uuid
  zoneId     String?  @db.Uuid
  workerKey  String   @db.VarChar(80)
  sessionKey String?  @db.VarChar(40)
  eventType  String   @db.VarChar(24)
  videoTimeS Float
  observedAt DateTime @db.Timestamptz(6)
  details    Json?

  org    Organisation @relation(fields: [orgId], references: [id], onDelete: Cascade)
  camera Camera       @relation(fields: [cameraId], references: [id], onDelete: Cascade)
  zone   Zone?        @relation(fields: [zoneId], references: [id], onDelete: SetNull)

  @@index([orgId, observedAt(sort: Desc)])
  @@index([zoneId, observedAt])
  @@index([cameraId])
  @@index([workerKey, observedAt])
  @@map("safety_events")
}

/// One verdict per rule evaluation. Immutable except through review_safety_decision().
model SafetyDecision {
  id          String  @id @db.Uuid
  orgId       String  @db.Uuid
  cameraId    String  @db.Uuid
  zoneId      String? @db.Uuid
  ruleId      String? @db.Uuid
  ruleType    String  @db.VarChar(24)
  severity    String  @db.VarChar(10)
  workerKey   String  @db.VarChar(80)
  sessionKey  String? @db.VarChar(40)
  /// COMPLIANT | POTENTIAL_VIOLATION | UNVERIFIABLE
  status      String  @db.VarChar(20)
  reason      String  @db.VarChar(500)
  confidence  Float
  evidence    Json
  thresholds  Json?
  snapshotRef String? @db.VarChar(300)
  videoTimeS  Float
  observedAt  DateTime @db.Timestamptz(6)

  /// NOT_REQUIRED | PENDING | CONFIRMED | DISMISSED
  reviewState      String    @default("NOT_REQUIRED") @db.VarChar(14)
  reviewedById     String?   @db.Uuid
  reviewedAt       DateTime? @db.Timestamptz(6)
  reviewReasonCode String?   @db.VarChar(24)
  reviewNote       String?   @db.VarChar(500)

  createdAt DateTime @default(now()) @db.Timestamptz(6)
  updatedAt DateTime @updatedAt @db.Timestamptz(6)

  org      Organisation @relation(fields: [orgId], references: [id], onDelete: Cascade)
  camera   Camera       @relation(fields: [cameraId], references: [id], onDelete: Cascade)
  zone     Zone?        @relation(fields: [zoneId], references: [id], onDelete: SetNull)
  rule     SafetyRule?  @relation(fields: [ruleId], references: [id], onDelete: SetNull)
  reviewer Profile?     @relation("SafetyDecisionReviewer", fields: [reviewedById], references: [id], onDelete: SetNull)
  actions  CorrectiveAction[]

  @@index([orgId, observedAt(sort: Desc)])
  @@index([orgId, status, observedAt(sort: Desc)])
  @@index([zoneId, observedAt])
  @@index([cameraId])
  @@index([ruleId])
  @@index([reviewedById])
  @@index([orgId, sessionKey])
  @@map("safety_decisions")
}

/// What was done about a confirmed violation. Writable by ADMIN/MANAGER; never deleted (cancel instead).
model CorrectiveAction {
  id           String    @id @default(uuid()) @db.Uuid
  orgId        String    @db.Uuid
  decisionId   String    @db.Uuid
  title        String    @db.VarChar(200)
  description  String?   @db.VarChar(2000)
  /// INFO | WARNING | CRITICAL (defaults from the rule severity in the UI)
  priority     String    @default("WARNING") @db.VarChar(10)
  /// OPEN | IN_PROGRESS | CLOSED | CANCELLED
  status       String    @default("OPEN") @db.VarChar(12)
  assignedToId String?   @db.Uuid
  dueDate      DateTime? @db.Date
  createdById  String?   @db.Uuid
  closedById   String?   @db.Uuid
  closedAt     DateTime? @db.Timestamptz(6)
  closingNote  String?   @db.VarChar(1000)
  createdAt    DateTime  @default(now()) @db.Timestamptz(6)
  updatedAt    DateTime  @updatedAt @db.Timestamptz(6)

  org        Organisation   @relation(fields: [orgId], references: [id], onDelete: Cascade)
  decision   SafetyDecision @relation(fields: [decisionId], references: [id], onDelete: Cascade)
  assignedTo Profile?       @relation("ActionAssignee", fields: [assignedToId], references: [id], onDelete: SetNull)
  createdBy  Profile?       @relation("ActionCreator",  fields: [createdById],  references: [id], onDelete: SetNull)
  closedBy   Profile?       @relation("ActionCloser",   fields: [closedById],   references: [id], onDelete: SetNull)

  @@index([orgId, status, dueDate])
  @@index([decisionId])
  @@index([assignedToId])
  @@index([createdById])
  @@index([closedById])
  @@map("corrective_actions")
}
```

Also add to the existing `Zone` model: `safetyType String? @db.VarChar(20)` (`PPE_CHECKPOINT | APPROACH | RESTRICTED | EXIT | GENERAL`) and the missing `slug` field from migration 023; to `Organisation`: `safetyEvidenceEnabled Boolean @default(true)`; and add `SAFETY_SUMMARY` to `enum ReportKind`.

Back-relations to add: `Organisation` (`safetyRules`, `safetyEvents`, `safetyDecisions`, `correctiveActions`), `Camera` (rules, events, decisions), `Zone` (same three), `Profile` (`safetyRulesCreated SafetyRule[]`, `safetyReviews SafetyDecision[] @relation("SafetyDecisionReviewer")`, `actionsAssigned`, `actionsCreated`, `actionsClosed` with the three named relations). The partial index on pending decisions exists only in the SQL file (Prisma cannot express partial indexes), exactly like the partial unique indexes in 020.

### 24.6 Rule parameters (`safety_rules.params`)

Validated in `rules.py` (and again by `/rules/validate`) so a rule that can never fire is rejected at save time.

| `type` | Anchored to | Parameters (default) | Validation |
|---|---|---|---|
| `PPE_ENTRY` | `RESTRICTED` zone | `required_ppe`: list of `helmet`, `safety_vest` (both); `mode`: `AT_ENTRY` \| `VIA_CHECKPOINT` (`AT_ENTRY`); `checkpoint_zone_id` (only for `VIA_CHECKPOINT`); `max_seconds_since_checkpoint` (60) | at least one item; checkpoint zone must be a `PPE_CHECKPOINT` zone on the same camera; seconds 5–600 |
| `PPE_CONTINUOUS` | `RESTRICTED` zone | `required_ppe` (both); `grace_seconds` (3) | grace 1–30 |
| `MAX_DWELL` | any real zone | `max_seconds` (required); `warn_fraction` (0.8) | max 10–86400; fraction 0.1–1.0 |
| `REQUIRED_EXIT` | `RESTRICTED` zone | `exit_zone_id` (required); `max_seconds_to_exit` (30) | exit zone must be an `EXIT` zone on the same camera; seconds 3–600 |
| `OCCUPANCY` | any real zone | `min_people` (none); `max_people` (none); `grace_seconds` (10); `window_seconds` (2) | at least one of min/max; min ≥ 2 when used; max ≥ 1; grace 3–120 |
| `MAN_DOWN` | zone or whole camera | `min_seconds_down` (5); `lying_allowed_zone_ids` ([]) | seconds 3–60 |
| `WALK_ONLY` *(optional)* | any real zone | `max_speed_heights_per_s` (1.8); `min_seconds` (2) | ranges checked |

Every rule row also carries `severity`, `isEnabled`, `minEvidenceConfidence`.

### 24.7 Evidence JSON shapes (`safety_decisions.evidence`)

```jsonc
// PPE_ENTRY / PPE_CONTINUOUS
{ "mode": "AT_ENTRY", "required": ["helmet","safety_vest"],
  "observed_frames": 30,
  "items": {
    "helmet":      {"state":"ABSENT","clear_frames":27,"seen_fraction":0.0,"empty_fraction":0.96,"mean_conf":0.0},
    "safety_vest": {"state":"PRESENT","clear_frames":27,"seen_fraction":0.89,"empty_fraction":0.04,"mean_conf":0.82} },
  "waited_seconds": 1.4 }

// MAX_DWELL
{ "entered_video_time_s": 12.4, "exited_video_time_s": 61.0, "dwell_seconds": 48.6, "limit_seconds": 45, "closed_by": "EXIT" }

// REQUIRED_EXIT
{ "left_zone": "zone_a", "expected_exit": "zone_b", "seconds_allowed": 30, "first_seen_in": "zone_c", "seconds_elapsed": 11.2 }

// OCCUPANCY
{ "zone": "zone_a", "median_count": 1, "required_min": 2, "episode_seconds": 12.1, "clear_fraction": 0.93 }

// MAN_DOWN
{ "torso_angle_deg": 78, "aspect": 1.6, "still_seconds": 6.2, "keypoint_confidence": 0.71 }
```

All numbers in a reason sentence come from these fields, so the sentence and the evidence cannot disagree.

### 24.8 Sync protocol

| Aspect | Rule |
|---|---|
| Direction | SQLite → Postgres only. Reviews live only in Postgres. |
| Trigger | 60 s background loop + end of each session |
| Batch | up to 500 rows per table per tick, oldest first |
| Camera mapping | local `camera_id` → `cameras.id` by `(orgId, name)` |
| Zone mapping | local `zone_id` (slug) → `zones.id` by `(cameraId, slug)`; a row with **no** zone (camera-wide man-down, `TRANSIT`) is sent with `zoneId = null` — do not reuse `minute_aggregator._resolve_ids` as-is, it returns nothing when the zone is missing (`minute_aggregator.py:539-543`); a zone that exists but cannot be mapped → leave unsynced and log the count |
| Ad-hoc cameras | uploads without a registered camera fall back to `upload_<sid>`, which has no Postgres row; such rows are skipped by the sync and purged locally after 7 days instead of retrying forever (use a registered camera for anything you want on the dashboard) |
| Rule mapping | rule uuid from the loaded rule; `NULL` for the built-in default rule |
| Events | `POST /rest/v1/safety_events?on_conflict=eventUid`, `Prefer: resolution=merge-duplicates` |
| Decisions | `POST /rest/v1/safety_decisions?on_conflict=id`, **`Prefer: resolution=ignore-duplicates`** (never overwrites a review) |
| Initial review state | writer sets `PENDING` for `POTENTIAL_VIOLATION`, `NOT_REQUIRED` otherwise (sent in the POST body) |
| Success | on 2xx set `synced_at`; otherwise retry next tick |
| Failure | swallow and log; never raise into the video loop |
| Local cleanup | delete rows older than 7 days **only if** `synced_at` is set |

### 24.9 Retention and privacy of stored data

| Data | Where | Default retention | Removed by |
|---|---|---|---|
| Events | Postgres | org `dataRetentionDays` (90) | `purge_safety_data()` |
| Decisions | Postgres | 2 × `dataRetentionDays` | `purge_safety_data()` |
| Snapshots | local files under `backend/evidence/` | 7 days | `purge_evidence()` (also nulls `snapshotRef`) |
| Local SQLite rows | SQLite | 7 days after sync | `purge_synced()` |
| Rules | Postgres | until soft-deleted | manual |
| Corrective actions | Postgres | same as their decision (2 × `dataRetentionDays`) | cascade when `purge_safety_data()` removes the decision |

No names, no face vectors and no continuous video are stored by any safety table.

### 24.10 Identifier mapping

| Concept | SQLite / pipeline | Postgres | Bridge |
|---|---|---|---|
| Organisation | `org_id` text (UUID) | `organisations.id` | identical |
| Camera | `camera_id` text name | `cameras.id` UUID | `(orgId, name)` |
| Zone | `zone_id` text slug | `zones.id` UUID | `(cameraId, slug)` (migration 023) |
| Rule | rule UUID (or NULL for default) | `safety_rules.id` | identical |
| Worker | `worker_key` (`trk-<n>`) | `workerKey` | identical; never a person |
| Event | `event_uid` | `eventUid` | identical |
| Decision | `id` | `id` | identical |
| Video run | `session_key` (pipeline `session_id`) | `sessionKey` | identical; a label, not a foreign key |
| Zone safety type | `zones.zone_type` | `zones.safetyType` | written by the zone mirror |

### 24.11 Migration and rollout checklist

1. Back up the Supabase project (or work on the development project first).
2. Apply `025_safety.sql` (idempotent), then `npx prisma generate`.
3. Run `backend/tests/test_safety_rls.py` (matrix below).
4. Confirm the pipeline can insert through the service role and that a browser session cannot.
5. Confirm `EXPLAIN` of the review queue and the overview queries uses the new indexes with realistic row counts (seed ~100k events) — the same discipline as migrations 009 and 012.
6. Only then enable `SAFETY_ENABLED=true` in an environment.

Rollback: `DROP TABLE ... CASCADE` for the four new tables, `DROP FUNCTION` for the five new functions (`review_safety_decision`, `soft_delete_safety_rule`, `purge_safety_data`, `corrective_action_check_decision`, and the cron job via `cron.unschedule`), `ALTER TABLE zones DROP COLUMN "safetyType"` and `ALTER TABLE organisations DROP COLUMN "safetyEvidenceEnabled"`. The only changes to existing objects are those two columns plus the extra `ReportKind` value (Postgres cannot drop an enum value; an unused value is harmless).

### 24.12 RLS verification matrix

| Actor | `safety_rules` | `safety_events` | `safety_decisions` | `review_safety_decision()` | `corrective_actions` |
|---|---|---|---|---|---|
| VIEWER (same org) | select ✔ · insert ✘ · update ✘ | select ✔ · write ✘ | select ✔ · write ✘ | ✘ "not permitted" | select ✔ · write ✘ |
| MANAGER | select ✔ · insert ✔ · update ✔ · soft delete only via `soft_delete_safety_rule()` (a direct `deletedAt` update fails) | select ✔ · write ✘ | select ✔ · direct update ✘ | ✔ (not on `COMPLIANT`; no CONFIRMED→DISMISSED while actions are open) | select ✔ · insert ✔ (confirmed decision only) · update ✔ · delete ✘ |
| ADMIN | same as MANAGER | same | same | ✔ | same as MANAGER |
| Other organisation | nothing | nothing | nothing | ✘ | nothing; cannot attach an action to another org's decision |
| Anonymous | no privileges | no privileges | no privileges | ✘ | no privileges |
| Service role (pipeline) | all | insert ✔ | insert ✔ | n/a | all (not used by the pipeline) |

Each ✘ is a test that must fail with a permission or RLS error, and each ✔ a test that must succeed — measured, not assumed.

---

## 25. Final checklist before calling the project done

**Build (by tier, §4)**
- [ ] T1 complete: R1 end to end, SQLite persistence, face-only-blur snapshots, review screen
- [ ] T2 complete: Postgres + RLS matrix passes, sync, alerts, dashboard, R3, man-down, CSV report
- [ ] T3 items done or explicitly listed as future work

**Evidence (never cut)**
- [ ] Own-footage PPE results with N, hard cases and rule-of-three bounds (§14.A); public-split leakage checked
- [ ] Baseline B0 / B1 vs ours table and error-vs-coverage plot (§14.G); thresholds justified from it
- [ ] Scenario suite S1–S10 results documented, including abstentions
- [ ] Man-down and occupancy results recorded with the tested camera placement
- [ ] Review loop run; human-judged precision and dismissal reasons recorded
- [ ] Corrective-action loop shown end to end: confirm → create action → close; overdue count visible
- [ ] FPS and alert latency recorded
- [ ] `SAFETY_ENABLED=false` still behaves like the old product; all old tests pass
- [ ] README and `docs/DOCUMENTATION.md` updated; `.env.example` complete
- [ ] Demo rehearsed three times; fallback clips ready
- [ ] Volunteer consent forms signed and stored; recordings deleted on the stated date

**Thesis / report**
- [ ] Literature survey (15–25 papers: PPE detection, PPE-to-worker association, selective classification / reject option, fall detection from pose, privacy in video analytics) and a comparison table with existing work and commercial products
- [ ] SRS with hardware and software requirements
- [ ] Diagrams: block diagram, ER diagram (§24.2), UML use-case, sequence diagram for one frame (§7.2), class diagram of `backend/app/safety/`, state diagrams for zone events, the R1 decision and man-down
- [ ] Test-case table with expected and actual results
- [ ] Results chapter (§14 tables and plots), limitations, future work (cross-camera, calibration, more PPE classes)
- [ ] Per-member contribution, separating new work from the inherited platform (§16.1, §20)
