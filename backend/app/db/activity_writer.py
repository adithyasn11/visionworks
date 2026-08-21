# backend/app/db/activity_writer.py
"""
Persistence layer for per-track activity telemetry.

WHY THIS EXISTS

The CV pipeline (detect -> track -> pose -> zone -> aggregate) produces a result
for every tracked person on every frame, but those results only ever went out
over the WebSocket to the browser. Nothing was written to `activity_logs`, so
the analytics endpoints in api/routers/analytics.py queried a table that was
permanently empty and every chart read zero.

This module closes that gap.

TWO PROBLEMS IT HAS TO SOLVE

1. VOLUME. The pipeline runs at up to ~60 FPS. Writing one row per person per
   frame would be thousands of rows a minute for a single video and would make
   the `activity_logs` table useless (and huge) within minutes. So writes are
   SAMPLED, not continuous: a track is persisted at most once every
   MIN_WRITE_INTERVAL_SECONDS, with one exception — a posture CHANGE is always
   written immediately, because a sitting->standing transition is the single
   most interesting event this system observes and sampling could miss it
   entirely.

2. BLOCKING. SQLAlchemy + SQLite are synchronous. Calling them directly from
   the async WebSocket handlers would stall the event loop and stutter the video
   stream. Every write therefore goes through asyncio.to_thread(), and failures
   are swallowed and logged rather than raised — telemetry is a side effect, and
   losing a row must never kill a live video session.
"""

import asyncio
import time
import logging
from typing import Optional

from app.db.database import SessionLocal
from app.db.models import ActivityLogModel

logger = logging.getLogger(__name__)

# How often a single continuously-visible track is written to the database.
# 5s keeps a 10-minute video with 3 people to roughly a few hundred rows while
# still giving the hourly charts in analytics.py enough resolution to be useful.
MIN_WRITE_INTERVAL_SECONDS = 5.0


def _normalised_floor_position(entity: dict):
    """
    Extracts a tracked entity's floorplan position as normalised 0..1 coords.

    The caller supplies `floor_point` (already homography-projected onto the
    floorplan) together with `floor_size` (the width/height of that floorplan
    space). Storing the ratio rather than raw pixels keeps the heatmap
    independent of camera resolution and of the size it is later drawn at.

    Returns (None, None) when there is no usable position, or when the point
    falls outside the floorplan — a projection that lands off-plan is bad data,
    and clamping it to the edge would pile up a false hotspot on the border.
    """
    point = entity.get("floor_point")
    size = entity.get("floor_size")
    if not point or not size:
        return None, None

    try:
        x, y = float(point[0]), float(point[1])
        width, height = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return None, None

    if width <= 0 or height <= 0:
        return None, None

    nx, ny = x / width, y / height
    if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
        return None, None

    return round(nx, 4), round(ny, 4)


class ActivityLogWriter:
    """
    Sampled writer for one processing session.

    One instance per WebSocket session, so the "when did I last write this
    track" state is scoped to that session and cannot leak between concurrent
    videos. Not thread-safe by design: it is only ever driven from a single
    session's frame loop.
    """

    def __init__(self, camera_id: str, min_interval_seconds: float = MIN_WRITE_INTERVAL_SECONDS):
        self.camera_id = camera_id
        self.min_interval = min_interval_seconds
        # track_id -> {"last_write": float, "last_posture": str}
        self._track_state: dict = {}
        self.rows_written = 0

    def _should_write(self, track_id: int, posture: str, now: float) -> bool:
        """
        Sampling rule: write if this track is new, if its posture just changed,
        or if min_interval has elapsed since its last write.
        """
        state = self._track_state.get(track_id)
        if state is None:
            return True
        if state["last_posture"] != posture:
            return True
        return (now - state["last_write"]) >= self.min_interval

    def collect(self, tracked_entities: list) -> list:
        """
        Filters one frame's tracked entities down to the rows that are due to be
        written, and records that they were written.

        Returns a list of plain dicts (not ORM objects) so the caller can hand
        them to a worker thread without carrying a Session across threads.
        """
        now = time.time()
        due = []

        for entity in tracked_entities:
            track_id = entity.get("track_id")
            posture = entity.get("posture")
            if track_id is None or posture is None:
                continue

            if not self._should_write(track_id, posture, now):
                continue

            floor_x, floor_y = _normalised_floor_position(entity)

            due.append({
                "camera_id": self.camera_id,
                "zone_id": entity.get("zone_id") or "TRANSIT_ZONE",
                "track_id": int(track_id),
                # The schema documents these as upper-case (SITTING / STANDING /
                # WALKING / AWAY) and analytics.py filters on exactly those
                # strings, so normalise here rather than trusting the caller.
                "posture_state": str(posture).upper(),
                "activity_score": float(entity.get("activity_score") or 0.0),
                "dwell_duration_seconds": int(entity.get("dwell_duration_seconds") or 0),
                "floor_x": floor_x,
                "floor_y": floor_y,
            })

            self._track_state[track_id] = {"last_write": now, "last_posture": posture}

        return due

    def flush_sync(self, rows: list) -> int:
        """
        Writes rows in a single transaction. Runs in a worker thread.

        Returns the number of rows written; 0 on failure. Never raises — a
        telemetry write must not be able to terminate a live video session.
        """
        if not rows:
            return 0

        session = SessionLocal()
        try:
            session.bulk_save_objects([ActivityLogModel(**row) for row in rows])
            session.commit()
            return len(rows)
        except Exception as e:
            session.rollback()
            logger.warning(f"activity_logs write failed ({len(rows)} rows dropped): {e}")
            return 0
        finally:
            session.close()


async def persist_frame(writer: Optional[ActivityLogWriter], tracked_entities: list) -> None:
    """
    Async entry point called from the frame loop.

    Selects the rows that are due and writes them off the event loop. Returns
    immediately when nothing is due, which is the common case — most frames add
    no rows at all, so the overhead per frame is a dict lookup per track.
    """
    if writer is None or not tracked_entities:
        return

    rows = writer.collect(tracked_entities)
    if not rows:
        return

    written = await asyncio.to_thread(writer.flush_sync, rows)
    writer.rows_written += written
