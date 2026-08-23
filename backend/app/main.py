# backend/app/main.py
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import sys
import os

# Load environment variables from backend/.env (this file is backend/app/main.py,
# so backend/ is one level up — setup.py creates the .env there).
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import engine, Base, apply_lightweight_migrations
from app.api.routers import cameras, zones, analytics, websocket, video_upload

# Create database tables automatically on startup, then add any columns that
# were introduced after an existing database was created (create_all does not
# alter existing tables).
Base.metadata.create_all(bind=engine)
apply_lightweight_migrations()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Owns the minute aggregator's lifetime.

    It runs on a 60s timer rather than only after a session, because a live
    stream can run for hours: waiting for it to end would leave the analytics
    empty for the whole run. Sessions ALSO trigger it directly, so a short
    upload does not wait up to a minute for its numbers to appear.

    The task is cancelled on shutdown so reload does not leave orphans stacking
    up, each aggregating the same window.
    """
    from app.db.minute_aggregator import ensure_bucket_table, run_aggregator_loop

    ensure_bucket_table()
    task = asyncio.create_task(run_aggregator_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Aggregator shutdown: {e}")


app = FastAPI(
    title="Vision-Based Workplace Activity Analytics System API",
    description="Real-time CCTV AI analytics, posture detection, and zone occupancy metrics backend.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register REST Routers
app.include_router(cameras.router, prefix="/api/v1/cameras", tags=["Cameras"])
app.include_router(zones.router, prefix="/api/v1/zones", tags=["Zones"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(websocket.router, prefix="/api/v1/ws", tags=["WebSockets"])
app.include_router(video_upload.router, prefix="/api/v1/video", tags=["Video Upload"])

@app.get("/")
def read_root():
    return {
        "status": "ONLINE",
        "system": "Vision-Based Workplace Activity Analytics Engine",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    # Port 8001 matches run_system.py, setup.py, README and the frontend clients.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
