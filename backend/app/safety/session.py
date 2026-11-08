# backend/app/safety/session.py
"""
Everything the safety layer does for ONE video session, behind four calls, so the
three sockets in video_upload.py (uploaded file, server webcam, browser camera)
each add a handful of lines and cannot drift apart.

    safety = safety_session_for(fps=fps, recorded_at=..., live=False,
                                camera_id=..., org_id=..., session_id=...)   # None if safety is off
    ...
    frame_time = safety.begin_frame(frame_index)            # once per frame, first
    tracked, zones = process_detections(..., ground_point_zones=True)
    await safety.after_frame(frame_time, tracked)            # zone events -> database
    payload["safety"] = safety.payload(frame_time)
    ...
    await safety.finish()                                    # session over: close visits, last flush

Later steps (PPE evidence, the rule engine) hang off `after_frame()`.

The contract with the video loop: nothing in here may raise into it. A failure in
the safety layer is logged (once, not per frame) and the video carries on.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.db.safety_writer import SafetyWriter, persist_safety
from app.safety.clock import utc_iso
from app.safety.config import SafetyConfig, load_config
from app.safety.engine import FrameTime, SafetyEngine, engine_for_session
from app.safety.zone_events import ZoneEvent, ZoneEventTracker, rule_zone_ids

_log = logging.getLogger(__name__)

# Events shown to the dashboard per frame are only the ones raised on that frame;
# this caps a pathological burst (a mass reset) so one payload stays small.
_MAX_EVENTS_IN_PAYLOAD = 50


def worker_key(track_id) -> str:
    """The anonymous per-camera key for a tracked person. Not an identity."""
    return f"trk-{int(track_id)}"


class SafetySession:
    def __init__(self, engine: SafetyEngine, writer: SafetyWriter, config: Optional[SafetyConfig] = None,
                 zones_config=None):
        """
        zones_config: the camera's zones. Only the ones safety rules can attach
        to (PPE_CHECKPOINT, APPROACH, RESTRICTED, EXIT) produce events; a person
        in a GENERAL or legacy-typed zone counts as being in no zone. None means
        "every zone", which is what tests that name no types want.
        """
        self.engine = engine
        self.writer = writer
        self.config = config or load_config()
        self.tracker = ZoneEventTracker(self.config)
        self._tracked_zones = None if zones_config is None else rule_zone_ids(zones_config)
        engine.register(self.tracker)
        self._last_frame: Optional[FrameTime] = None
        # (worker, zone) visits whose ENTERED_ZONE fell in replayed footage and so
        # was not written again. Their later events say so.
        self._partial: set = set()
        self._warned: set = set()
        self._frame_events: List[dict] = []
        # Visits a jump closed, delivered with the next frame but not yet written.
        # Held here so that a frame that fails before after_frame() cannot lose them.
        self._closed_waiting: List[ZoneEvent] = []
        # Of those, the visits that have since been seen in NEW footage (not a replay).
        self._partial_fresh: set = set()

    # ── the clock ────────────────────────────────────────────────────────────

    def begin_frame(self, frame_index: Optional[int] = None) -> FrameTime:
        self._frame_events = []
        frame_time = self.engine.begin_frame(frame_index)
        self._last_frame = frame_time
        self._closed_waiting.extend(r for r in frame_time.closed if isinstance(r, ZoneEvent))
        return frame_time

    def reset(self, reason: str, frame_index: Optional[int] = None) -> None:
        self.engine.reset(reason, frame_index=frame_index)

    # ── per frame ────────────────────────────────────────────────────────────

    async def after_frame(self, frame_time: FrameTime, tracked_entities) -> None:
        """Turn this frame's positions into zone events and queue them for the database."""
        try:
            self._zone_events(frame_time, tracked_entities)
        except Exception as e:
            self._warn_once("zone", f"zone events skipped for a frame: {type(e).__name__}: {e}")
        await persist_safety(self.writer)

    def _zone_events(self, frame_time: FrameTime, tracked_entities) -> None:
        # Visits a jump closed since the previous frame. These are real records of
        # what was observed before the jump, so they are written even when the
        # frame that carries them is itself a replay.
        self._write_closed(frame_time)

        worker_zone = {}
        for entity in tracked_entities:
            track_id = entity.get("track_id")
            if track_id is None:
                continue
            zone = entity.get("zone_id")
            if self._tracked_zones is not None and zone not in self._tracked_zones:
                zone = None
            worker_zone[worker_key(track_id)] = zone

        events = self.tracker.update(worker_zone, None, frame_time.video_time_s)
        if not frame_time.replay and self._partial:
            for pair in self._partial:
                if self.tracker.zone_of(pair[0]) == pair[1]:
                    self._partial_fresh.add(pair)

        for event in events:
            if frame_time.replay:
                # Footage already processed. The tracker keeps following people
                # so its state is right when new footage begins, but nothing is
                # raised a second time.
                if event.event_type == "ENTERED_ZONE":
                    self._partial.add((event.worker_key, event.zone_id))
                continue
            if (event.worker_key, event.zone_id) in self._partial:
                event = ZoneEvent(event.event_type, event.worker_key, event.zone_id, event.video_time_s,
                                  {**event.details, "visit_started_in_replay": True})
                if event.event_type == "EXITED_ZONE":
                    self._partial.discard((event.worker_key, event.zone_id))
                    self._partial_fresh.discard((event.worker_key, event.zone_id))
            self._write(event, frame_time)

    def _write_closed(self, reference: FrameTime, extra=()) -> None:
        """
        Write the visits a jump closed. A closing record is real observation, so
        it is written, with one exception: a visit that began inside rewound
        footage (its entry was not written again) and was closed again before
        reaching anything new describes footage the database already has, and
        writing it would leave an exit with no matching entry.
        """
        waiting, self._closed_waiting = self._closed_waiting, []
        for record in [*waiting, *extra]:
            pair = (record.worker_key, record.zone_id)
            began_in_replay = pair in self._partial
            reached_new = pair in self._partial_fresh
            self._partial.discard(pair)
            self._partial_fresh.discard(pair)
            if began_in_replay:
                if not reached_new:
                    continue
                record = ZoneEvent(record.event_type, record.worker_key, record.zone_id,
                                   record.video_time_s, {**record.details, "visit_started_in_replay": True})
            self._write(record, reference)

    def _write(self, event: ZoneEvent, frame_time: FrameTime) -> None:
        observed_at = self._observed_at(frame_time, event.video_time_s)
        try:
            self.writer.add_event(
                event.event_type, event.worker_key, event.video_time_s, observed_at,
                zone_id=event.zone_id, details=event.details or None)
        except Exception as e:
            self._warn_once("write", f"a zone event was refused: {type(e).__name__}: {e}")
            return
        if len(self._frame_events) < _MAX_EVENTS_IN_PAYLOAD:
            self._frame_events.append({
                "event_type": event.event_type, "worker_key": event.worker_key,
                "zone_id": event.zone_id, "video_time_s": round(event.video_time_s, 3),
                "details": event.details,
            })

    @staticmethod
    def _observed_at(reference: FrameTime, video_time_s: float) -> str:
        """
        Wall-clock time of an event at `video_time_s`, given a frame whose own
        wall-clock time is known. On a file the two differ by a constant, so this
        is exact; on a live source the event is at most a few seconds before the
        frame, which is also when it happened.
        """
        frame_moment = datetime.fromisoformat(reference.observed_at)
        if frame_moment.tzinfo is None:
            frame_moment = frame_moment.replace(tzinfo=timezone.utc)
        shift = timedelta(seconds=reference.video_time_s - video_time_s)
        return utc_iso(frame_moment - shift)

    def payload(self, frame_time: FrameTime) -> dict:
        """The `safety` block of a frame payload: the clock, plus events raised on this frame."""
        data = frame_time.as_payload()
        if self._frame_events:
            data["events"] = list(self._frame_events)
        return data

    # ── the end ──────────────────────────────────────────────────────────────

    async def finish(self) -> None:
        """
        The session is over. Close whatever is still open (a person standing in a
        zone when the video ends), then make one last, forced attempt to store
        everything.
        """
        try:
            reference = self._last_frame
            if reference is not None:
                self._closed_waiting.extend(r for r in self.engine.drain() if isinstance(r, ZoneEvent))
                last = self.engine.last_seen_s
                ending = self.tracker.close_all("SESSION_END", reference.video_time_s if last is None else last)
                self._write_closed(reference, ending)
        except Exception as e:
            self._warn_once("finish", f"closing the safety session failed: {type(e).__name__}: {e}")
        try:
            await persist_safety(self.writer, force=True)
        except Exception as e:
            self._warn_once("flush", f"final safety flush failed: {type(e).__name__}: {e}")

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            _log.warning(message)


def safety_session_for(
    *,
    fps: Optional[float] = None,
    recorded_at=None,
    live: bool = False,
    camera_id: str,
    org_id: Optional[str] = None,
    session_id: Optional[str] = None,
    zones_config=None,
) -> Optional[SafetySession]:
    """
    The safety session for one video, or None when SAFETY_ENABLED is off (the
    pipeline then runs exactly as it did before the safety layer existed).
    Raises ValueError for a bad fps / recorded_at, which the caller reports.
    """
    engine = engine_for_session(fps=fps, recorded_at=recorded_at, live=live)
    if engine is None:
        return None
    writer = SafetyWriter(camera_id=camera_id, org_id=org_id, session_id=session_id)
    return SafetySession(engine, writer, zones_config=zones_config)
