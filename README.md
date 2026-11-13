# VisionWorks — Evidence-Based Workplace Safety Compliance Monitoring

A full-stack system that watches ordinary camera feeds and checks them against safety rules: helmets and high-visibility vests, restricted zones, time limits, exit routes, head counts and people lying still. It is built with **FastAPI**, **Next.js 14**, **YOLOv8** and **Supabase**.

Every check ends in one of three honest verdicts:

| Verdict | Meaning |
|---------|---------|
| **COMPLIANT** | The rule was checked and met. |
| **POTENTIAL VIOLATION** | The evidence is strong enough to raise a flag for a person to review. |
| **UNVERIFIABLE** | The system could not tell (too far away, hidden, a flicker, the camera stopped) and says so instead of guessing. |

Nothing is punished by the machine. A flag goes to a review queue, where a manager confirms or dismisses it. Every flag carries its numbers (how many frames, how confident), a blurred evidence picture, and an audit trail.

---

## Features

- **Zones with meaning** — draw a PPE checkpoint, approach, restricted or exit zone over the camera view.
- **Rules** — PPE at entry or at a checkpoint, PPE kept on inside, maximum time in a zone, required exit within a time, minimum or maximum people in a zone, man-down (a person lying still), each with a severity.
- **Evidence-based verdicts** — a rolling window of clear frames decides; an item has to be missing in nearly all of them before anything is flagged.
- **Review queue** — confirm or dismiss, with notes; corrective actions can be assigned and closed.
- **Reports** — shift and date-range safety reports as PDF (every script the fonts cover) and CSV, a shift audit trail, report history.
- **Evaluation screen** — the system compared with two simpler baselines on held-out images, a scenario suite, real-footage speed, and a plain list of what was *not* measured.
- **Privacy by design** — video is processed in memory; workers are anonymous track keys; evidence pictures are blurred at the head, kept for a limited time and only visible to managers.
- **Roles** — ADMIN, MANAGER, VIEWER, enforced in the interface, in the server and by Postgres row-level security.

---

## Project structure

```
major project/
├── backend/                  # FastAPI backend
│   ├── app/
│   │   ├── api/routers/      # REST and WebSocket endpoints
│   │   ├── cv/               # Detection, tracking, PPE model, spatial engine
│   │   ├── safety/           # Clock, zone events, rule engine, man-down, occupancy, evidence
│   │   ├── db/               # Local SQLite (events, decisions), writer, sync to Postgres
│   │   └── utils/            # Report generator (PDF/CSV) and fonts
│   ├── eval/                 # Evaluation harness (scenario suite, real footage, report writer)
│   ├── tests/                # Test scripts (run each with the venv's python)
│   └── .env.example          # Every setting, with its default
│
├── frontend/                 # Next.js 14 dashboard
│   ├── app/                  # Pages, dashboard screens, lib
│   └── prisma/sql/           # Postgres migrations: tables, RLS, report functions, retention
│
├── ppe_training/             # PPE (helmet / vest) detector: training and evaluation
├── docs/                     # Documentation, design, evaluation write-up, demo runbook
├── scripts/                  # Dev HTTPS certificate and launcher
├── sample_videos/            # Test videos (git-ignored)
├── setup.py                  # Bootstraps everything
└── run_system.py             # Starts backend and frontend
```

---

## Getting started

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ |
| Node.js | 18+ |
| Git | any |
| GPU | optional (an NVIDIA GPU makes it real-time; CPU works, slower) |

### 1. Clone and set up

```bash
git clone https://github.com/adithyasn11/majorproject.git
cd majorproject
python setup.py
```

Setup creates the virtual environment, installs Python and Node packages, downloads the detector weights and writes template `.env` files (3–10 minutes the first time).

### 2. Add credentials

**`backend/.env`**
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key
# Writes decisions and events to Postgres. Bypasses RLS: backend only, never in the browser.
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
# Turns the safety rules on. Off, the system only detects and draws zones.
SAFETY_ENABLED=true
```

**`frontend/.env.local`**
```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project-id.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
DATABASE_URL=postgresql://postgres.<ref>:<password>@<host>:6543/postgres?pgbouncer=true
DIRECT_URL=postgresql://postgres.<ref>:<password>@<host>:5432/postgres
```

Supabase → **Settings → API** for the keys, **Settings → Database** for the connection strings.

### 3. Create the database objects

Run the files in `frontend/prisma/sql/` in numeric order (000 … 028) in the Supabase SQL editor, after `prisma db push` has created the tables. Migrations 025–028 are the safety layer: tables, review function, summary and report functions, nightly retention job.

### 4. Start

```bash
python run_system.py
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3000 |
| API | http://localhost:8001 |
| API docs | http://localhost:8001/docs |

To use a phone as a camera, a browser needs a secure address:

```bash
python run_system.py --https
```

This prints the address to open on the phone. Trust the generated certificate on the laptop first (see `docs/DEMO_RUNBOOK.md`).

`Ctrl+C` stops everything.

**On Windows, one click:** double-click `start_visionworks.bat`. It runs the dashboard as a fast production build at http://localhost:3000 (built automatically the first time and after code changes, about a minute), installs the dashboard's packages on the first run, and opens the browser when it is ready. `start_visionworks.bat phone` uses HTTPS so a phone can be paired as a camera; `start_visionworks.bat dev` runs the development server. Close its window to stop.

---

## Using the app

1. **Sign up**, create an organisation on `/onboarding`.
2. **Cameras** — add a camera (or check the one from onboarding). Decisions reach Review, Events and Reports only for a run on a registered camera; the Live feed and Zones screens say so when there is none.
3. **Zones** — draw zones over the camera view and give each a type.
4. **Rules** — add the rules that apply, with severity and parameters.
5. **Live feed** — start the camera, upload a video, or pair a phone. Flags appear as they happen.
6. **Review** — confirm or dismiss each flag. Open one to see the reason, the numbers and the picture.
7. **Reports** and **Evaluation** — export a shift report; read how well the system measured up and where it is wrong.

| Role | Can | Cannot |
|------|-----|--------|
| **ADMIN** | Everything, plus members, plan and retention | — |
| **MANAGER** | Cameras, zones, rules, review, evidence pictures, actions, exports | Manage members or plan |
| **VIEWER** | Read decisions, reports and the evaluation | Change anything, see evidence pictures |

Invites show a one-time link (no email provider is configured).

---

## Tests

Each test file runs on its own:

```bash
venv/Scripts/python.exe backend/tests/test_compliance.py
```

`docs/DOCUMENTATION.md` lists them by area. Files named `*_live.py` and `test_safety_rls.py` talk to your Supabase and clean up after themselves. To run everything that needs no database:

```bash
venv/Scripts/python.exe scripts/run_tests.py
```

---

## Settings reference

All safety thresholds are optional environment variables, listed with their defaults and meaning in `backend/.env.example`.

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL`, `SUPABASE_KEY` | Project URL and anon key (verifies tokens; RLS still applies) |
| `SUPABASE_SERVICE_ROLE_KEY` | Lets the backend write decisions and events. Backend only |
| `SAFETY_ENABLED` | Master switch for the safety rules (default off) |
| `PPE_MODEL_PATH` | Use another PPE detector's weights |
| `SAFETY_EVIDENCE_DIR`, `SAFETY_EVIDENCE_RETENTION_DAYS` | Where evidence pictures live and how long (default 7 days) |
| `SAFETY_LOCAL_*_RETENTION_DAYS` | How long the local file keeps synced or (standalone) unsynced rows |
| `SAFETY_CAMERA_LOST_S` | A live camera silent this long counts as stopped (default 5 s) |
| `PRIVACY_BLUR_DEFAULT` | Start sessions with head blur on |
| `VISIONWORKS_STANDALONE_LIVE` | Single-machine demo with no accounts (off by default) |

Frontend: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `DATABASE_URL`, `DIRECT_URL`.

---

## What it does not claim

The write-up in `docs/SAFETY_EVALUATION.md` and the Evaluation screen state it plainly: the false-accusation target of zero was not met on held-out images (6 of 321 worn items, every one an item the detector missed in every frame); man-down recall on real falls was not measured; the head count under-counts tiny, distant people and reports "could not verify" instead; the PDF cannot draw Chinese, Japanese, Korean or emoji.

---

## License

MIT — see [LICENSE](LICENSE).
