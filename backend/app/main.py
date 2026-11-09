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
from app.api.routers import cameras, zones, analytics, websocket, video_upload, pairing, safety

# Create database tables automatically on startup, then add any columns that
# were introduced after an existing database was created (create_all does not
# alter existing tables).
Base.metadata.create_all(bind=engine)
apply_lightweight_migrations()

logger = logging.getLogger(__name__)


def _silence_proactor_disconnects() -> None:
    """
    Stop Windows' asyncio loop shouting about clients that hung up.

    On the Proactor loop, a peer closing a TLS connection abruptly — which is
    every browser tab you close, every phone that sleeps, every reload — makes
    `_ProactorBasePipeTransport._call_connection_lost` call `sock.shutdown()`
    on a socket the peer already tore down. That raises ConnectionResetError
    (WinError 10054) inside a bare callback, so asyncio prints a full traceback
    with no request context attached.

    It is entirely cosmetic: the response has already been delivered and no
    handler is involved. But it prints several times a minute and buries the
    logs that matter — the reason the pairing and pipeline lines were hard to
    find at all.

    Only that exact case is dropped. A ConnectionResetError raised anywhere
    else still reaches the default handler, because a connection dying inside
    a handler is a real event worth seeing.
    """
    if sys.platform != "win32":
        return

    loop = asyncio.get_running_loop()
    default = loop.get_exception_handler()

    def handler(loop_, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
            cb = repr(context.get("handle") or "")
            if "_call_connection_lost" in cb:
                logger.debug("client disconnected abruptly (WinError 10054)")
                return
        if default is not None:
            default(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)


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

    # Needs the running loop, so it belongs here rather than at import time.
    _silence_proactor_disconnects()

    ensure_bucket_table()

    # The safety layer's local tables (events, decisions), plus housekeeping so
    # the file cannot grow for ever. Created on every start, which is cheap and
    # means nothing has to remember to run a migration. Housekeeping must never
    # stop the server from starting.
    try:
        from app.db.minute_aggregator import service_role_configured
        from app.db.safety_models import ensure_safety_tables, purge_local
        from app.safety.config import load_config

        ensure_safety_tables()
        cfg = load_config()
        removed = purge_local(
            synced_days=cfg.local_synced_retention_days,
            # Only with nowhere to sync to are unsynced rows ever deleted.
            unsynced_days=None if service_role_configured() else cfg.local_standalone_retention_days,
        )
        if any(removed.values()):
            logging.getLogger(__name__).info(f"Local safety housekeeping removed {removed}")
    except Exception as e:
        logging.getLogger(__name__).error(f"Safety tables could not be prepared: {e}")

    task = asyncio.create_task(run_aggregator_loop())
    # Copies the safety events and decisions into Postgres (a no-op without a
    # service-role key, so the local tables simply accumulate).
    from app.db.safety_sync import run_safety_sync_loop
    sync_task = asyncio.create_task(run_safety_sync_loop())
    try:
        yield
    finally:
        for running in (task, sync_task):
            running.cancel()
        for running in (task, sync_task):
            try:
                await running
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Background task shutdown: {e}")


app = FastAPI(
    title="VisionWorks Workplace Safety Compliance API",
    description="Camera-based workplace safety compliance: detection, tracking, zones and PPE verdicts.",
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
app.include_router(safety.router, prefix="/api/v1/safety", tags=["Safety"])
# Phone-as-camera pairing. Carries its own /api/v1/pair prefix because the
# WebSocket path is part of the QR code a phone scans, and a prefix declared
# in two places is a prefix that eventually disagrees with itself.
app.include_router(pairing.router, tags=["Pairing"])

@app.get("/")
def read_root():
    return {
        "status": "ONLINE",
        "system": "VisionWorks — Workplace Safety Compliance Engine",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    # Port 8001 matches run_system.py, setup.py, README and the frontend clients.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
