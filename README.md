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
├── setup.py                  # First-run setup (start_visionworks.bat runs it)
└── run_system.py             # Starts backend and frontend
```

---

## Getting started

### On a new computer (Windows): three steps

You need **Python 3.11** (https://www.python.org/downloads/, tick *Add python.exe to PATH* in the installer), **Node.js LTS** (https://nodejs.org) and **Git** (https://git-scm.com). An NVIDIA graphics card makes video analysis real-time; without one it still works, more slowly.

1. Get the code:
   ```bash
   git clone https://github.com/adithyasn11/visionworks.git
   ```
2. Put **`visionworks-keys.env`** in the `visionworks` folder, next to `start_visionworks.bat`. The project owner sends you this file privately; it is never in git.
3. Double-click **`start_visionworks.bat`**.

The first run sets everything up by itself (10–20 minutes, a few GB of downloads: PyTorch, the packages, the detection models), then opens http://localhost:3000. After that, double-clicking it starts VisionWorks in about a minute. Close its window to stop.

You share the owner's database, so sign in with your own account (or create one at **Create an account**).

To update later: `git pull`, then double-click `start_visionworks.bat` again (the dashboard rebuilds itself when the code changed).

**For the project owner — making the keys file:** run `venv\Scripts\python scripts\make_keys_file.py`. It writes `visionworks-keys.env` from your `backend/.env` and `frontend/.env.local`. Send it privately (never commit or post it): it holds the Supabase service-role key, which can read and change everything in the database. The database password is not in it; the app does not need it.

**If something goes wrong:**

| Message | Fix |
|---------|-----|
| *Python 3.10 to 3.13 was not found* | Install Python 3.11 with *Add python.exe to PATH* ticked, then run the file again. |
| *Node.js is not installed* | Install Node.js LTS, then run the file again. |
| *The keys file is missing* | Put `visionworks-keys.env` next to `start_visionworks.bat`. |
| A download failed | Run the file again: finished steps are skipped. |
| Port 3000 or 8001 already in use | Close the other program (or the other VisionWorks window) and run it again. |

**Using a phone as a camera.** A phone only opens its camera on a secure (https) address, so double-click **`start_visionworks_phone.bat`** instead (close the normal window first). The first time on a computer, its window prints one command that makes this computer trust VisionWorks' own certificate; run it in a Command Prompt, confirm in the Windows dialog, and reopen the browser. Then open https://localhost:3000 → **Live feed** → **Use my phone** and scan the code with a phone on the same Wi-Fi (the phone shows a security warning the first time: tap Advanced, then continue). `start_visionworks.bat dev` runs the development server.

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3000 |
| API | http://localhost:8001 |
| API docs | http://localhost:8001/docs |

### A brand-new Supabase project (only when not sharing the owner's)

Create the tables with `npx prisma db push` (needs `DATABASE_URL` and `DIRECT_URL` in `frontend/.env.local`, from Supabase → **Settings → Database**), then run the files in `frontend/prisma/sql/` in numeric order in the Supabase SQL editor. Put the project's URL, anon key and service-role key (Supabase → **Settings → API**) in `visionworks-keys.env` and start as above.

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
