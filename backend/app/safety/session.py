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

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.db.safety_writer import SafetyWriter, persist_safety
from app.safety.clock import utc_iso
from app.safety.compliance_engine import ComplianceEngine
from app.safety.config import SafetyConfig, load_config
from app.safety.engine import FrameTime, SafetyEngine, engine_for_session
from app.safety.ppe_runner import PpeRunner, get_ppe_detector
from app.safety.rules import load_rules
from app.safety import snapshots
from app.safety.schemas import POTENTIAL_VIOLATION, UNVERIFIABLE, Decision
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
                 zones_config=None, ppe_runner: Optional[PpeRunner] = None, rules=None,
                 evidence_enabled: bool = False, evidence_refresh_s: Optional[float] = None, evidence_check=None):
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
        # None: this session does no PPE checks (the payload then says nothing about PPE).
        self.ppe = ppe_runner
        if ppe_runner is not None:
            engine.register(ppe_runner)
        self._tracked_zones = None if zones_config is None else rule_zone_ids(zones_config)
        self._zones_config = list(zones_config or [])
        # Evidence pictures (F11). Whether this organisation allows them is read ONCE, when the
        # session starts, so no network call can happen in the middle of a frame.
        self.evidence_enabled = bool(evidence_enabled)
        # How often a long session re-reads the organisation's switch (None: never, as in tests).
        # An organisation that turns evidence off must not keep getting pictures for the whole
        # length of a live session.
        self._evidence_refresh_s = evidence_refresh_s
        self._evidence_checked_at = None
        self._held: Optional[dict] = None                  # the clean frame of the frame being processed
        # Open alerts of THIS session: an alert is a POTENTIAL_VIOLATION decision that has not been
        # reviewed, and nothing reviews a decision inside a live session, so each flag stays open here.
        # (The dashboard's own count comes from Postgres, where reviews happen.)
        self._open_flags: dict = {}                        # worker key -> number of open flags (the count; never reset)
        # Who is OUTLINED as flagged. Cleared when the timeline jumps: a tracker may hand the same id to
        # a different person after a seek, who must not be drawn as flagged.
        self._flagged: set = set()
        self._evidence_check = evidence_check              # org -> bool, run in a thread
        engine.register(self.tracker)
        # The rule engine: judges entries into restricted zones from the zone events and
        # the PPE evidence. Without a PPE runner there is nothing to judge with.
        # `rules` are the organisation's rules (None: the built-in default).
        self.compliance: Optional[ComplianceEngine] = None
        if ppe_runner is not None:
            self.rules = rules if rules is not None else load_rules(writer.org_id, zones_config)
            self.compliance = ComplianceEngine(self.rules, ppe_runner, self.config)
            engine.register(self.compliance)
        self._frame_decisions: List[dict] = []
        self._closed_decisions: List[Decision] = []
        self._last_frame: Optional[FrameTime] = None
        # (worker, zone) visits whose ENTERED_ZONE fell in replayed footage and so
        # was not written again. Their later events say so.
        self._partial: set = set()
        self._warned: set = set()
        self._frame_events: List[dict] = []
        # The size the people detector sees, fixed by the first frame: its tracker keeps
        # ids across frames, and ids churn when the frame size changes mid-stream.
        self._pose_size = None
        # Visits a jump closed, delivered with the next frame but not yet written.
        # Held here so that a frame that fails before after_frame() cannot lose them.
        self._closed_waiting: List[ZoneEvent] = []
        # Of those, the visits that have since been seen in NEW footage (not a replay).
        self._partial_fresh: set = set()

    # ── the clock ────────────────────────────────────────────────────────────

    def begin_frame(self, frame_index: Optional[int] = None) -> FrameTime:
        self._frame_events = []
        self._frame_decisions = []
        frame_time = self.engine.begin_frame(frame_index)
        self._last_frame = frame_time
        self._closed_waiting.extend(r for r in frame_time.closed if isinstance(r, ZoneEvent))
        self._closed_decisions.extend(r for r in frame_time.closed if isinstance(r, Decision))
        return frame_time

    def pose_input(self, original, working):
        """
        What to show the people detector, and how to bring its boxes back.

        `original` is the frame as the camera sent it, `working` the (usually 640
        px wide) copy the rest of the pipeline uses. A distant worker is a few
        pixels in the copy and is simply not found, so the detector is given the
        ORIGINAL (shrunk only above `pose_max_width`) at a matching input size, and
        its boxes are scaled back to `working`'s pixels so zones, overlays and
        telemetry are unchanged.

        Returns (frame, imgsz, rescale): rescale is None when no scaling is needed.
        """
        import math

        wh, ww = working.shape[:2]
        oh, ow = original.shape[:2]
        limit = int(self.config.pose_max_width)
        longest = max(ow, oh)
        if original is working or (ow <= ww and oh <= wh):
            frame, rescale = working, None
        elif longest > limit:
            # The limit is on the LONGER side, so a portrait phone frame is not
            # handed to the model at 1920 px tall.
            import cv2
            k = limit / float(longest)
            nw, nh = max(1, int(round(ow * k))), max(1, int(round(oh * k)))
            frame = cv2.resize(original, (nw, nh), interpolation=cv2.INTER_AREA)
            rescale = (ww / float(nw), wh / float(nh))
        else:
            frame, rescale = original, (ww / float(ow), wh / float(oh))
        if self._pose_size is None:
            self._pose_size = (frame.shape[1], frame.shape[0])
        elif (frame.shape[1], frame.shape[0]) != self._pose_size:
            import cv2
            pw, ph = self._pose_size
            frame = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
            rescale = (ww / float(pw), wh / float(ph))
        imgsz = max(480, int(math.ceil(max(frame.shape[:2]) / 32.0) * 32))
        return frame, imgsz, rescale

    async def detect_ppe(self, frame_time: FrameTime, detections, ppe_frame, pose_shape) -> Optional[dict]:
        """
        Helmet/vest verdicts for the people in this frame, {track_id: ppe dict},
        or None when this session does no PPE checks. Call BEFORE the privacy
        blur touches the frame. The model runs in a worker thread so the event
        loop (other sockets, the writers) keeps going while it works.
        """
        if self.ppe is None:
            return None
        persons = [{"track_id": d["track_id"], "bbox": d["bbox"]} for d in detections
                   if d.get("track_id") is not None]
        try:
            return await asyncio.to_thread(self.ppe.process, persons, ppe_frame, pose_shape, frame_time.video_time_s)
        except Exception as e:
            self._warn_once("ppe", f"PPE step skipped for a frame: {type(e).__name__}: {e}")
            return None

    def hold_frame(self, clean, working, detections, frame_time: FrameTime) -> None:
        """
        Keep this frame's CLEAN pixels for an evidence picture, until the next frame.

        Call after `detect_ppe` and BEFORE `process_detections`, which blurs the working frame
        in place. `clean` is the frame as the camera sent it; when the pipeline did not shrink
        it, `clean` IS `working` and the blur would write into it, so it is copied. Nothing is
        kept unless this organisation allows evidence pictures.
        """
        if not self.evidence_enabled:
            return
        try:
            if clean is working:
                clean = clean.copy()
            people = [{"track_id": d.get("track_id"), "bbox": list(d["bbox"]), "keypoints": d.get("keypoints")}
                      for d in detections]
            ppe = self.ppe
            self._held = {
                "frame": clean, "people": people, "work_shape": tuple(working.shape[:2]),
                "video_time_s": frame_time.video_time_s,
                # Boxes only when the model ran on THIS frame; an older run's boxes would be drawn
                # on a frame they do not belong to.
                "ppe_dets": list(ppe.last_detections) if ppe is not None and ppe.last_detections_call == ppe._calls else [],
                "ppe_shape": ppe.last_ppe_shape if ppe is not None else None,
            }
        except Exception as e:
            self._held = None
            self._warn_once("hold", f"the frame could not be kept for evidence: {type(e).__name__}: {e}")

    def reset(self, reason: str, frame_index: Optional[int] = None) -> None:
        self._held = None
        self._flagged.clear()
        self.engine.reset(reason, frame_index=frame_index)

    # ── per frame ────────────────────────────────────────────────────────────

    async def _refresh_evidence_switch(self) -> None:
        """Re-read the organisation's evidence switch every `evidence_refresh_s`, off the event loop."""
        if not self._evidence_refresh_s or self._evidence_check is None or not self.writer.org_id:
            return
        now = time.monotonic()
        if self._evidence_checked_at is not None and now - self._evidence_checked_at < self._evidence_refresh_s:
            return
        self._evidence_checked_at = now
        try:
            self.evidence_enabled = bool(await asyncio.to_thread(self._evidence_check, self.writer.org_id))
            if not self.evidence_enabled:
                self._held = None                           # and stop holding frames
        except Exception as e:
            self._warn_once("evidence-switch", f"could not re-read the evidence switch: {type(e).__name__}: {e}")
            self.evidence_enabled = False                   # fail closed, like the first read

    async def after_frame(self, frame_time: FrameTime, tracked_entities) -> None:
        """Turn this frame's positions into zone events and queue them for the database."""
        await self._refresh_evidence_switch()
        try:
            self._zone_events(frame_time, tracked_entities)
        except Exception as e:
            self._warn_once("zone", f"zone events skipped for a frame: {type(e).__name__}: {e}")
        # Tell the picture which people have an open alert (the entities are sent after this call).
        if self._flagged:
            for entity in tracked_entities:
                if entity.get("track_id") is not None and worker_key(entity["track_id"]) in self._flagged:
                    entity["flagged"] = True
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
            original = event
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
            if self.compliance is not None:
                self._judge(lambda ev=original: self.compliance.on_event(ev, frame_time.video_time_s), frame_time)

        if self.compliance is not None and not frame_time.replay:
            self._judge(lambda: self.compliance.on_tick(frame_time.video_time_s), frame_time)

    def _judge(self, ask, frame_time: FrameTime) -> None:
        """
        Run one step of the rule engine and write what it concluded. A failure here
        is logged and skipped: a defect in the rules must never corrupt the zone
        events beside them (a lost entry would leave an orphan exit).
        """
        try:
            decisions = ask()
        except Exception as e:
            self._warn_once("rules", f"the rule engine skipped a step: {type(e).__name__}: {e}")
            return
        for decision in decisions:
            self._write_decision(decision, frame_time, picture=True)

    def _write_decision(self, decision: Decision, reference: FrameTime, picture: bool = False) -> None:
        """`picture`: this decision was made on the frame being processed, so that frame may be its evidence."""
        ref, obscured = None, False
        wants = decision.snapshot_requested or (decision.status == UNVERIFIABLE and self.config.snapshot_unverifiable)
        if picture and wants:
            ref, obscured = self._take_snapshot(decision, reference)
        evidence = {**decision.evidence, "helmet_region_obscured": True} if obscured else decision.evidence
        try:
            self.writer.add_decision(
                decision.rule_type, decision.severity, decision.worker_key, decision.status, decision.reason,
                decision.confidence, evidence, decision.video_time_s,
                self._observed_at(reference, decision.video_time_s),
                zone_id=decision.zone_id, rule_id=decision.rule_id, thresholds=decision.thresholds,
                snapshot_ref=ref, decision_id=decision.id)
        except Exception as e:
            self._warn_once("decision", f"a decision was refused: {type(e).__name__}: {e}")
            if ref:
                self._discard_snapshot(ref)                  # no decision, so no picture
            return
        if decision.status == POTENTIAL_VIOLATION:
            self._open_flags[decision.worker_key] = self._open_flags.get(decision.worker_key, 0) + 1
            self._flagged.add(decision.worker_key)
        if len(self._frame_decisions) < _MAX_EVENTS_IN_PAYLOAD:
            data = decision.as_payload()
            if wants:
                data["snapshot"] = ref is not None           # False: no picture was kept (off, or no frame to show)
            self._frame_decisions.append(data)

    def _take_snapshot(self, decision: Decision, reference: FrameTime):
        """(snapshot_ref, helmet_region_obscured), or (None, False) when no picture is kept."""
        held = self._held
        if not self.evidence_enabled or held is None or reference.replay:
            return None, False
        if abs(held["video_time_s"] - reference.video_time_s) > 1e-6:
            return None, False                               # not the frame this decision was made on
        try:
            match = decision.worker_key[4:] if decision.worker_key.startswith("trk-") else None
            target = int(match) if match is not None and match.isdigit() else None
            zone = next((z for z in self._zones_config if str(z.get("zone_id")) == str(decision.zone_id)), None)
            def clock(t):
                t = int(max(0, t))
                return f"{t // 60:02d}:{t % 60:02d}"
            label = "POTENTIAL VIOLATION (for review)" if decision.status == POTENTIAL_VIOLATION else "COULD NOT VERIFY"
            # Two times, because they differ: the decision is stamped at the entry, the picture is
            # the frame it was decided on (up to the evidence wait later).
            banner = [f"{label}  |  {(zone or {}).get('zone_name') or decision.zone_id or 'camera'}  |  "
                      f"entered {clock(decision.video_time_s)}  |  picture {clock(reference.video_time_s)}  |  "
                      f"{('Worker ' + decision.worker_key[4:]) if decision.worker_key.startswith('trk-') else decision.worker_key}",
                      decision.reason]
            jpeg, obscured = snapshots.render(
                held["frame"], held["people"], held["work_shape"], target_track=target,
                ppe_dets=held["ppe_dets"], ppe_shape=held["ppe_shape"],
                zone_polygon=(zone or {}).get("polygon"), banner=banner)
            return snapshots.save_snapshot(jpeg, self.writer.org_id, decision.id), obscured
        except Exception as e:
            self._warn_once("snapshot", f"an evidence picture could not be saved: {type(e).__name__}: {e}")
            return None, False

    @staticmethod
    def _discard_snapshot(ref: str) -> None:
        try:
            path = snapshots.path_for_ref(ref)
            if path is not None and path.exists():
                path.unlink()
        except OSError:
            pass

    def _write_closed(self, reference: FrameTime, extra=()) -> None:
        """
        Write the visits a jump closed. A closing record is real observation, so
        it is written, with one exception: a visit that began inside rewound
        footage (its entry was not written again) and was closed again before
        reaching anything new describes footage the database already has, and
        writing it would leave an exit with no matching entry.
        """
        # Decisions a jump closed ("the video jumped before they could be judged") are
        # observations of what happened before it, so they are written.
        decisions, self._closed_decisions = self._closed_decisions, []
        for decision in decisions:
            self._write_decision(decision, reference)
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
        if self.ppe is not None and self.ppe.unavailable_reason:
            data["ppe_unavailable"] = self.ppe.unavailable_reason
        if self._frame_events:
            data["events"] = list(self._frame_events)
        if self._frame_decisions:
            data["decisions"] = list(self._frame_decisions)
        if self.compliance is not None:
            # How many flags this session has raised that nobody has reviewed. Always present when
            # the rules run, so the dashboard can show "0" as well as a number.
            data["open_count"] = sum(self._open_flags.values())
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
                drained = self.engine.drain()
                self._closed_waiting.extend(r for r in drained if isinstance(r, ZoneEvent))
                self._closed_decisions.extend(r for r in drained if isinstance(r, Decision))
                last = self.engine.last_seen_s
                end_at = reference.video_time_s if last is None else last
                ending = self.tracker.close_all("SESSION_END", end_at)
                if self.compliance is not None:
                    self._closed_decisions.extend(self.compliance.close(end_at))
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
    with_ppe: bool = True,
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
    config = load_config()
    runner = PpeRunner(get_ppe_detector(config), config) if with_ppe else None
    # Read once, here (the sockets build the session off the event loop): may this
    # organisation's flagged decisions keep a picture?
    enabled = snapshots.evidence_enabled(org_id)
    return SafetySession(engine, writer, config, zones_config=zones_config, ppe_runner=runner, evidence_enabled=enabled,
                         evidence_refresh_s=300.0, evidence_check=snapshots.evidence_enabled)
