# VisionWorks — Evidence-Based Workplace Safety Compliance Monitoring

**Product and technical documentation** · final-year major project

The design reasoning is in `docs/safety-pivot/PROJECT_SCOPE_AND_DESIGN.md` (with an "As built" list under every step). The measured results are in `docs/SAFETY_EVALUATION.md`. The demonstration plan is in `docs/DEMO_RUNBOOK.md`. This file describes the finished system.

## Contents

1. What it is
2. What it does and does not do
3. How a decision is made
4. Architecture
5. The pipeline in detail
6. Rules
7. Data and privacy
8. Roles and screens
9. API
10. Reports and evaluation
11. Robustness and retention
12. Tests
13. Running it
14. Known limits

---

## 1. What it is

A system that reads ordinary camera feeds and checks them against safety rules set by the site's managers. It finds people with a pose detector, finds helmets and high-visibility vests with a fine-tuned YOLOv8m detector, follows each person with an anonymous track number, works out which zone each person is in, and judges each rule over a window of frames, not a single picture.

The result is never "this person broke the rule". It is one of three verdicts, and anything doubtful goes to a person.

| Verdict | Meaning |
|---------|---------|
| COMPLIANT | The rule was checked and met |
| POTENTIAL_VIOLATION | Evidence is strong enough to raise a flag for review (severity INFO, WARNING or CRITICAL) |
| UNVERIFIABLE | The system could not tell and says why (too far, hidden, flickering, camera stopped, the stay was cut short) |

## 2. What it does and does not do

**Does:** PPE at entry (at the zone or through a checkpoint); PPE kept on while inside; a maximum stay in a zone (with a warning at a set fraction); required exit within a time (and by the right route); minimum or maximum people in a zone (lone worker, over capacity); man-down (a person lying still); a human review queue with notes and an audit trail; corrective actions; blurred evidence pictures; shift reports as PDF and CSV; an honest evaluation page.

**Does not:** identify anyone; store video; store names, faces or biometrics; take any action against a person; raise a flag from one frame; claim a number it has not measured.

## 3. How a decision is made

Three ideas run through everything.

1. **Time is the video's.** Every frame has one time (seconds into the file, or seconds since a live stream began). A seek, a replay or a restart never makes the same stay count twice; what was open when the timeline jumps is closed as UNVERIFIABLE.
2. **Evidence is a window.** For each person and each item the system keeps recent "clear" frames (large enough, not overlapped). An item is PRESENT if seen in at least 60 % of them and ABSENT only if missing in at least 90 %; with fewer than 8 clear frames, or anything in between, the answer is UNVERIFIABLE. Thresholds are on the Evaluation screen's sweep.
3. **Nothing is judged that was not seen.** A person who was already inside when first seen has a stay of unknown length. A person lost from view is not "left". A camera that stops closes every open stay as UNVERIFIABLE with the reason "the camera stopped sending pictures".

## 4. Architecture

```
camera / phone / uploaded video
        │  frames
        ▼
 FastAPI backend ─ people + pose (YOLOv8m-pose) ─ tracking ─ zone assignment
        │             PPE detector (YOLOv8m fine-tuned) ─ association ─ clear-frame gate
        ▼
 SafetySession (one per video session)
   ├─ FrameClock / SafetyEngine   one time per frame, resets on jumps
   ├─ ZoneEventTracker            entered / exited / lost / dwell warnings
   ├─ ComplianceEngine            PPE_ENTRY, PPE_CONTINUOUS, MAX_DWELL, REQUIRED_EXIT
   ├─ ManDownMonitor              MAN_DOWN
   ├─ OccupancyMonitor            OCCUPANCY (too few / too many people)
   └─ SafetyWriter ──► local SQLite (events, decisions) ──sync──► Supabase Postgres
                                         evidence pictures: backend/evidence/<org>/<id>.jpg
Next.js dashboard ◄── Postgres (row-level security) ◄── functions: review, summary, report
```

Two stores: the pipeline writes SQLite next to the backend (so a lost network loses nothing); a sync loop copies unsynced rows to Postgres with the service-role key; the dashboard reads Postgres only. Every event and decision carries a UUID created at the source, so syncing twice is harmless. Browsers cannot write measured data: `safety_events` and `safety_decisions` have SELECT policies only, and reviewing goes through one function that checks the role and writes the audit row in the same transaction.

## 5. The pipeline in detail

- **People:** YOLOv8m-pose on the original frame (up to 1280 px wide), boxes scaled back to the working size. Anonymous per-session track ids (`trk-<n>`).
- **PPE:** detector on every second frame (default) up to 1280 px, with close-up crops for people under 360 px tall. Items are associated to the person whose head or torso region they fall in; hi-vis colour confirms a vest. Overlapping people give "not clear" frames, not guesses.
- **Zones:** polygons per camera with a type (PPE_CHECKPOINT, APPROACH, RESTRICTED, EXIT, GENERAL). The foot point decides; entry needs 0.7 s inside, exit 1.0 s outside, a detection gap under 0.5 s is ignored. Zone events are back-dated to when the change really happened.
- **Man-down:** torso angle more than 60° from vertical, almost still (under 0.15 body-heights a second over 0.6 s), box shape rules out bending over (aspect under 0.9) and mixed cases (0.9–1.1 are unclear); 60 % of the window must be clearly down; 5 s by default; always CRITICAL.
- **Occupancy:** median of the clear head count over a short window; a person is "clearly seen" at 0.4 confidence, little overlap and at least 60 px tall; hidden people count for "too few" only in the safe direction.
- **Evidence pictures:** the frame of the decision with the head region blurred and boxes drawn; saved only if the organisation allows it, kept 7 days by default, shown to managers only, every view audited.

## 6. Rules

Created on the Rules screen, stored per organisation in `safety_rules`, validated by the server (a rule that cannot work is refused with a message).

| Type | Attaches to | Parameters (defaults) |
|------|-------------|------------------------|
| PPE_ENTRY | Restricted zone | `required_ppe` (helmet, safety_vest), `mode` AT_ENTRY or VIA_CHECKPOINT (+ checkpoint zone, `max_seconds_since_checkpoint` 60) |
| PPE_CONTINUOUS | Restricted zone | `required_ppe`, `grace_seconds` 3 |
| MAX_DWELL | Zone | `max_seconds` (≥ 10), `warn_fraction` 0.8 |
| REQUIRED_EXIT | Zone | `exit_zone_id` (an EXIT zone), `max_seconds_to_exit` 30 |
| OCCUPANCY | Zone | `min_people` (≥ 2) and/or `max_people`, `grace_seconds` 10, `window_seconds` 2 |
| MAN_DOWN | Camera or zone | `min_seconds_down` 5, `lying_allowed_zone_ids` |
| WALK_ONLY | Zone | accepted and stored, not yet evaluated (the screen says so) |

Each rule has a severity and a minimum evidence confidence (default 0.6). Every decision stores the rule, the thresholds used and the evidence numbers.

## 7. Data and privacy

Postgres tables of the safety layer (migrations `frontend/prisma/sql/025`–`028`): `safety_rules`, `safety_events`, `safety_decisions`, `corrective_actions`, plus `zones.safetyType`, `reports` (kind SAFETY_SUMMARY) and `audit_logs`. Functions: `review_safety_decision()`, `safety_summary()`, `safety_report()`, `purge_safety_data()` (scheduled nightly with pg_cron).

- Workers are anonymous track keys; no name, face or identity exists in the data.
- Evidence pictures are files, referenced by path, head-blurred; never in the database.
- Every table carries the organisation id; row-level security decides from the row alone.
- Roles are enforced in the interface, the server and Postgres (the last is the real boundary).
- Exports are audited: no audit row, no file.

## 8. Roles and screens

| Role | Can |
|------|-----|
| ADMIN | Everything, plus members, plan, retention |
| MANAGER | Cameras, zones, rules, review, evidence pictures, corrective actions, exports |
| VIEWER | Read decisions, reports, evaluation |

Dashboard screens: **Overview** (flags, review state, people in view), **Review** (queue, confirm / dismiss with notes, evidence), **Live feed** (camera, upload, phone pairing, live verdicts), **Events**, **Zones**, **Rules**, **Actions** (corrective actions), **Reports** (summary, PDF/CSV, audit trail, history), **Evaluation**, **Plan** (admin).

## 9. API

REST under `/api/v1`, WebSockets for video. Authorisation is by the Supabase access token; the role is resolved server-side.

| Area | Endpoints |
|------|-----------|
| Safety | `GET /safety/rules`, `POST /safety/rules/validate`, `POST /safety/rules`, `PATCH`/`DELETE /safety/rules/{id}`; `GET /safety/events`, `/decisions`, `/decisions/{id}`, `POST /decisions/{id}/review`, `GET /decisions/{id}/snapshot`, `GET /safety/summary`, `/export` |
| Reports | `GET /safety/report`, `/report/export` (PDF or CSV), `/report/history`, `/audit-trail`, `/evaluation` |
| Cameras / zones | `GET/POST/PATCH/DELETE /cameras`, `/zones` |
| Phone pairing | `POST /pair/session`, `GET /pair/session/{code}`, WebSocket `/pair/ws/{code}` |
| Video | `POST /video/upload`, WebSockets `/video/process/{session_id}`, `/video/live_webcam`, `/video/process_webcam_frame`; WebSocket `/ws/stream/{camera_id}`; `GET /video/samples`, `/video/snapshot`, `/video/status` |
| Analytics | `GET /analytics/heatmap` |

Interactive documentation at `http://localhost:8001/docs`.

## 10. Reports and evaluation

The report counts what the system flagged, what people confirmed, what they dismissed, what is waiting and what could not be verified, with the time people spent in each zone (stays that began before the camera saw them are counted as "at least"). PDFs are drawn with embedded Noto fonts for Latin, Greek, Cyrillic, Indic scripts, Arabic, Hebrew and Thai.

The evaluation compares the system with a one-frame decision and a majority vote on held-out PPE images (with a 64-setting threshold sweep), runs a scenario suite of 270 runs through the real engine, measures speed on real clips, and lists what was not measured. Re-create it with:

```bash
venv/Scripts/python.exe ppe_training/eval_segments.py
venv/Scripts/python.exe backend/eval/safety_scenarios.py
venv/Scripts/python.exe backend/eval/real_footage.py
venv/Scripts/python.exe backend/eval/write_report.py
```

## 11. Robustness and retention

- **Camera stops:** a live camera silent for `SAFETY_CAMERA_LOST_S` (5 s) closes every open stay, wait and timer as UNVERIFIABLE ("the camera stopped sending pictures"), tells the page, and carries on if pictures return. A dropped connection ends the session the same way.
- **Duplicate sessions:** each session has its own key; two on one camera write their own rows only.
- **Very long sessions:** per-person state is forgotten when the person is; evidence windows and counts are fixed-size; the list of flagged people is capped; the record of processed time is one stretch. Tested over an hour of video with every rule on.
- **Restart:** rows are written to the local file first and marked unsynced; after a restart the sync copies them. Retention never deletes an unsynced row when a service-role key is configured.
- **Retention jobs:** local rows synced more than 7 days ago are purged at start-up (`purge_local`); evidence pictures older than 7 days are deleted by the sync loop (`purge_evidence`), with their references cleared; Postgres purges with `purge_safety_data()` nightly at 03:45 (pg_cron).

## 12. Tests

Run all with `venv/Scripts/python.exe scripts/run_tests.py` (add `--live` to include those that use your Supabase project and clean up after themselves).

| Area | Files in `backend/tests/` |
|------|---------------------------|
| Time and zones | `test_safety_clock`, `test_zone_events`, `test_zone_session`, `test_zone_types`, `test_zone_isolation`, `test_zone_names`, `test_zone_events_socket`, `test_zone_types_http` |
| PPE and phone pairing | `test_pairing`, `test_ppe_runner`, `test_ppe_closeup`, `test_ppe_demo`, `test_ppe_evidence`, `test_ppe_socket` |
| Rules | `test_compliance`, `test_compliance_socket`, `test_rules_more`, `test_alert_dedup`, `test_man_down`, `test_occupancy`, `test_safety_config` |
| Storage and sync | `test_safety_writer`, `test_safety_sync`, `test_snapshots`, `test_snapshots_socket`, `test_safety_sync_live`* |
| API and security | `test_safety_api`, `test_safety_api_live`*, `test_safety_rls`*, `test_permissions_parity` |
| Reports and evaluation | `test_safety_report`, `test_evaluation` |
| Robustness | `test_session_hardening`, `test_robustness`, `test_camera_lost_socket` |

\* uses Supabase.

## 13. Running it

See `README.md`. In short: `python setup.py`, fill `backend/.env` and `frontend/.env.local`, run the SQL in `frontend/prisma/sql/`, `python run_system.py` (add `--https` to pair a phone). `docs/DEMO_RUNBOOK.md` has the preflight check (`scripts/demo_preflight.py`) and the demonstration script.

## 14. Known limits

Stated here, on the Evaluation screen and in `docs/SAFETY_EVALUATION.md`.

- The false-flag target of zero was not met on held-out images (6 of 321 worn items; each one the detector missed in every frame). A person reviews every flag.
- People under about 90 px tall cannot be judged; they are reported UNVERIFIABLE.
- Man-down recall on real falls, human-judged precision, speed with identity on and results on own-site footage were not measured.
- The head count can undercount tiny distant people (below 60 px) and then says "could not verify".
- The PDF cannot draw Chinese, Japanese, Korean or emoji (shown as "?").
- WALK_ONLY rules are stored but not yet evaluated.
