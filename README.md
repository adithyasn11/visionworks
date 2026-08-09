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
git clone https://github.com/your-username/visionworks.git
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
SUPABASE_KEY=your-anon-or-service-role-key
```

**Frontend** → `frontend/.env.local`
```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project-id.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

> 📍 Get these from your Supabase project: **Settings → API**

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

## 📋 Environment Variables Reference

### `backend/.env`

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Supabase service role or anon key |

### `frontend/.env.local`

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon (public) key |

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
