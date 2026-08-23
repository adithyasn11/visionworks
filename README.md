# VisionWorks — Vision-Based Workplace Activity Analytics

A full-stack, real-time workplace monitoring platform built with **FastAPI**, **Next.js 14**, **YOLOv8**, and **Supabase**. It transforms passive CCTV feeds into actionable business intelligence — tracking zone occupancy, dwell times, ergonomic safety, and foot traffic — all while keeping privacy at the center of its architecture.

---

## ✨ Features

- **Custom Zone Mapping** — Draw polygonal zones directly over your camera feeds to track any area you care about
- **Real-Time Occupancy** — Live person detection and counting per zone using YOLOv8 + SORT tracking
- **Dwell Time Analytics** — Understand exactly how long people stay in specific areas
- **Ergonomic Safety** — Skeletal pose estimation to detect unsafe postures and trigger alerts
- **Live Heatmaps** — Visualize foot traffic intensity across your facility
- **Data Export** — Download structured activity reports as CSV/PDF
- **Privacy by Design** — Video is processed locally; only numerical metadata is stored

---

## 🗂 Project Structure

```
major project/
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── api/routers/      # REST & WebSocket endpoints
│   │   ├── cv/               # YOLOv8 inference, SORT tracker, spatial engine
│   │   ├── db/               # Supabase client, models, schemas
│   │   ├── services/         # Business logic layer
│   │   ├── utils/            # Report generator, helpers
│   │   └── main.py           # FastAPI app entry point
│   ├── requirements.txt
│   └── .env.example          # ← Copy to .env and fill in credentials
│
├── frontend/                 # Next.js 14 (App Router) frontend
│   ├── app/
│   │   ├── components/       # Shared UI (LandingNavbar, LandingFooter, etc.)
│   │   ├── dashboard/        # Main authenticated dashboard
│   │   ├── features/         # Public features page
│   │   ├── security/         # Public security page
│   │   ├── lib/              # Supabase client config
│   │   └── page.jsx          # Landing page
│   ├── package.json
│   └── .env.local.example    # ← Copy to .env.local and fill in credentials
│
├── sample_videos/            # Drop test .mp4 files here (git-ignored)
├── setup.py                  # ← Run this first to bootstrap everything
├── run_system.py             # ← Run this to start the full system
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites

Make sure you have these installed before running anything:

| Tool | Version | Download |
|------|---------|----------|
| Python | 3.10+ | https://python.org |
| Node.js | 18+ | https://nodejs.org |
| Git | Any | https://git-scm.com |

---

### Step 1 — Clone the repo

```bash
git clone https://github.com/adithyasn11/majorproject.git
cd visionworks
```

---

### Step 2 — Run the setup script

This single command handles **everything**: creates a virtual environment, installs all Python and Node.js dependencies, downloads YOLOv8 model weights, and creates template `.env` files.

```bash
python setup.py
```

> ⏱ This takes 3–10 minutes the first time (downloading PyTorch and Node packages). Subsequent runs are fast.

---

### Step 3 — Configure your credentials

The setup script creates two pre-filled template files. Open them and add your real Supabase credentials.

**Backend** → `backend/.env`
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key
# Needed to WRITE minute buckets and alerts. zone_minute_stats and alerts have
# no INSERT policy — measured data is deliberately read-only to browser
# clients — so the only credential that can write them is the one that
# bypasses RLS. Backend only. Never NEXT_PUBLIC_.
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

**Frontend** → `frontend/.env.local`
```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project-id.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key

# Prisma needs BOTH, and they are not interchangeable:
#   6543  PgBouncer transaction pooler — the running app
#   5432  a real session — `prisma migrate`, which takes advisory locks
#         that a transaction-mode pooler cannot hold
DATABASE_URL=postgresql://postgres.<ref>:<password>@<host>:6543/postgres?pgbouncer=true
DIRECT_URL=postgresql://postgres.<ref>:<password>@<host>:5432/postgres
```

> 📍 API keys: Supabase → **Settings → API**
> 📍 Connection strings: Supabase → **Settings → Database → Connection string**

---

### Step 4 — Add a test video *(optional)*

Drop any `.mp4` file into the `sample_videos/` directory. You can point a camera at this through the dashboard.

---

### Step 5 — Start the system

```bash
python run_system.py
```

This starts both servers and keeps them running:

| Service | URL |
|---------|-----|
| Frontend (Next.js) | http://localhost:3000 |
| Backend API (FastAPI) | http://localhost:8001 |
| API Docs (Swagger) | http://localhost:8001/docs |

Press **`Ctrl+C`** to shut down everything cleanly.

---

## 🔒 Security Notes

- **`.env` and `.env.local` are git-ignored** — they will never be committed
- Video frames are processed in-memory and immediately discarded; no raw footage is stored
- Only numerical telemetry (bounding box coordinates, person counts) is sent to Supabase
- Supabase Row-Level Security (RLS) ensures strict per-organization data isolation

---

## 🧰 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI + Uvicorn |
| Computer Vision | YOLOv8 (Ultralytics) + SORT Tracking |
| Pose Estimation | YOLOv8-Pose |
| Spatial Engine | Shapely |
| Database | Supabase (PostgreSQL) |
| Frontend | Next.js 14 (App Router) + Tailwind CSS |
| Auth | Supabase Auth |
| Real-time | WebSockets (FastAPI) |

---

## 🧭 Using the app

### First run

1. **Sign up** at `/signup` (email or Google).
2. You land on **`/onboarding`** — not the dashboard. A user with no
   organisation cannot see anything, because every Row-Level Security policy
   resolves through membership. Create the organisation; a site and a camera are
   optional.
3. **Draw a zone** on the Zones tab. Occupancy is attributed to whichever zone a
   person is standing in, so nothing is measured until at least one exists.
4. **Upload a video** or start the camera on the Live feed tab.
5. Within ~2 minutes the **minute-bucket aggregator** folds that telemetry into
   `zone_minute_stats`, and the dashboard tiles and charts populate.
6. **Export** a CSV or PDF from the Reports tab.

> ⚠️ **The dashboard reads zero until step 5 completes.** Telemetry recorded
> before an organisation existed carries `org_id = NULL`, belongs to no tenant,
> and is invisible to everyone — deliberately.

### Roles

| Role | Can | Cannot |
|------|-----|--------|
| **ADMIN** | Everything, plus members, org settings and retention | — |
| **MANAGER** | Cameras, zones, run analysis, exports, acknowledge alerts | Manage members or org settings |
| **VIEWER** | Read dashboards, zones and reports | Change anything |

Enforced in three places — hidden controls, a re-check in every server action,
and Postgres RLS. The last one is the real boundary: a VIEWER POSTing directly
to the zone-save endpoint gets **403**, and the policy would refuse the write
even if that check were removed.

### Inviting your team

`/settings/members` → invite an address at a role. There is **no email
provider configured**, so you are shown a one-time link to send yourself. The
invite works regardless: signing up with the invited address activates the
membership automatically, including through Google.

### Retention

`/settings/organisation` sets `dataRetentionDays` per organisation. A nightly
`pg_cron` job deletes minute buckets older than that, per org, and writes a
`retention.purged` audit row. Shortening retention shows how many minutes it
will destroy before you confirm.

---

## 📋 Environment Variables Reference

### `backend/.env`

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Supabase **anon** key — verifies user tokens; RLS still applies |
| `SUPABASE_SERVICE_ROLE_KEY` | Writes minute buckets and alerts. Bypasses every RLS policy — backend only |

### `frontend/.env.local`

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon (public) key |
| `DATABASE_URL` | Pooled connection (port 6543) — used by the running app |
| `DIRECT_URL` | Direct connection (port 5432) — used by `prisma migrate` and the seed |

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-new-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/my-new-feature`
5. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
