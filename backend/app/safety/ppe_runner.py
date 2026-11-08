# backend/app/safety/ppe_runner.py
"""
PPE in the live loop: every person on screen carries a helmet / vest verdict.

WHAT IT DOES, FRAME BY FRAME

  1. Takes the people the pose model found (anonymous track ids and boxes).
  2. On every Nth frame (`ppe_every_n_frames`, default 2) runs the helmet/vest
     detector on the UNBLURRED frame, matches each helmet and vest to at most one
     person (`associate_frame`), and adds what it saw to that person's rolling
     evidence (`PpeEvidenceTracker`, keyed `trk-<id>`).
  3. Returns, for every person, the evidence-based verdict so far:
         {"status": COMPLIANT | NON_COMPLIANT | UNVERIFIABLE,
          "helmet": 0.91, "vest": 0.0,            # confidence of the last detection
          "clear": true,                          # was that frame good evidence
          "helmet_state": PRESENT | ABSENT | UNVERIFIABLE, "vest_state": ...}
     On frames between detections the verdict is read from the same evidence, so
     it is present on every frame.

THE RULES THIS FOLLOWS

  * Absence is inferred, never detected (there is no "no helmet" class), so a
    verdict is only ever as good as the evidence window. Fewer than a minimum of
    clear frames, or mixed evidence, is UNVERIFIABLE. See ppe_evidence.py.
  * A frame where detection FAILED is not evidence. A broken model must never
    read as "no helmet seen", so a failure adds nothing to anyone's window, and
    old evidence ages out on its own (`ppe_stale_s`), turning verdicts
    UNVERIFIABLE while the detector is down.
  * No model, no verdict. Without the weights every person is UNVERIFIABLE with
    the reason "PPE model not loaded"; the session still runs.
  * Resolution. The pose model sees a 640-px-wide copy, but a worker must be
    ~90 px tall to count as clear. The PPE model therefore gets the ORIGINAL
    frame (shrunk only if wider than `ppe_max_width`, 1280 by default) and the
    person boxes are scaled up to match.
  * Faces. The detector sees the frame before the privacy blur, because the blur
    covers exactly where a helmet is. That frame never leaves the server.

The runner is an engine component: a jump in the timeline clears every window
(evidence from before a seek says nothing about footage after it).

Nothing here may raise into the video loop.
"""

import logging
import math
import os
import statistics
import threading
import time
from collections import deque
from typing import Dict, List, Optional

from app.cv.ppe_evidence import (
    HELMET, VEST, UNVERIFIABLE, PpeEvidenceTracker, associate_frame,
)
from app.safety.config import SafetyConfig

_log = logging.getLogger(__name__)

NOT_LOADED = "PPE model not loaded"
FAILING = "PPE detection is failing"

# This many detection attempts in a row failing means the detector is down.
_FAILURE_LIMIT = 3

# A source slower than this (seconds between frames, about 2.5 fps) gets the
# model on every frame instead of every Nth.
SLOW_FRAME_S = 0.4

# A model that failed to load is tried again after this long (a copy that was
# still in progress, a GPU that was busy), not only when the file changes.
RETRY_LOAD_AFTER_S = 60.0


def worker_key(track_id) -> str:
    return f"trk-{int(track_id)}"


class PpeRunner:
    def __init__(self, detector, config: Optional[SafetyConfig] = None):
        """detector: anything with `detect(frame, imgsz=None) -> [{"cls","conf","bbox"}]`, or None."""
        self.detector = detector
        self.config = config or SafetyConfig()
        self.every_n = max(1, int(self.config.ppe_every_n_frames))
        self._tracker = self._new_tracker()
        self._last: Dict[str, dict] = {}        # worker key -> last associated detection
        self._last_seen: Dict[str, float] = {}
        self._calls = 0
        self._failures = 0
        self._warned: set = set()
        self._frame_gaps: deque = deque(maxlen=9)       # recent seconds between calls (frames with people)
        self._detect_gaps: deque = deque(maxlen=9)      # recent seconds between detection runs
        self._prev_t: Optional[float] = None
        self._prev_detect_t: Optional[float] = None
        self.detections_run = 0                 # how many frames the model actually ran on

    def _new_tracker(self) -> PpeEvidenceTracker:
        c = self.config
        return PpeEvidenceTracker(
            window=c.ppe_window_frames, min_clear=c.ppe_min_clear_frames,
            presence=c.ppe_presence_fraction, absence=c.ppe_absence_fraction,
            stale_seconds=c.ppe_stale_s)

    # ── availability ─────────────────────────────────────────────────────────

    @property
    def unavailable_reason(self) -> Optional[str]:
        """Why PPE verdicts cannot be given right now, or None when they can."""
        if self.detector is None:
            return NOT_LOADED
        if self._failures >= _FAILURE_LIMIT:
            return FAILING
        return None

    # ── per frame ────────────────────────────────────────────────────────────

    def process(self, persons: List[dict], ppe_frame, pose_shape, t: float) -> Dict[int, dict]:
        """
        persons     [{"track_id", "bbox"}] in the pose frame's pixel space.
        ppe_frame   the original BGR frame, not blurred.
        pose_shape  (height, width, ...) of the frame the boxes belong to.
        t           this frame's video time in seconds.

        Returns {track_id: ppe dict}. Never raises.
        """
        try:
            return self._process(persons, ppe_frame, pose_shape, float(t))
        except Exception as e:
            # Counts like a failed detection: not evidence, and enough of them in
            # a row makes the runner say it is failing.
            self._failures += 1
            self._warn(f"PPE step failed: {type(e).__name__}: {e}", key=type(e).__name__)
            return {p["track_id"]: self._unverifiable(p["track_id"]) for p in persons}

    def _process(self, persons, ppe_frame, pose_shape, t) -> Dict[int, dict]:
        if self.detector is None:
            return {p["track_id"]: self._unverifiable(p["track_id"]) for p in persons}

        self._calls += 1
        if self._prev_t is not None and t > self._prev_t:
            self._frame_gaps.append(t - self._prev_t)
        self._prev_t = t
        for p in persons:
            self._last_seen[worker_key(p["track_id"])] = t

        if persons and (self._calls - 1) % self._cadence() == 0:
            self._detect(persons, ppe_frame, pose_shape, t)
        self._forget_old(t)

        # While the detector is down, nobody can be judged: old evidence must not
        # keep a verdict (above all not a red one) alive next to a notice saying
        # nothing can be checked.
        if self._failures >= _FAILURE_LIMIT:
            return {p["track_id"]: self._unverifiable(p["track_id"]) for p in persons}
        self._tracker.stale_seconds = self._stale_seconds()

        out = {}
        for p in persons:
            key = worker_key(p["track_id"])
            verdict = self._tracker.status(key, t)
            last = self._last.get(key, {})
            out[p["track_id"]] = {
                "status": verdict["status"],
                "helmet": round(last.get(HELMET, 0.0), 2),
                "vest": round(last.get(VEST, 0.0), 2),
                "clear": bool(last.get("clear", False)),
                "helmet_state": verdict["items"][HELMET]["state"],
                "vest_state": verdict["items"][VEST]["state"],
            }
        return out

    def _cadence(self) -> int:
        """
        Run the model every Nth call, but never sparsely on a slow source: at
        about 2 fps or less every frame is needed to gather the minimum clear
        frames in time, and skipping half of them would leave nobody judged.
        """
        if self._frame_gaps and statistics.median(self._frame_gaps) >= SLOW_FRAME_S:
            return 1
        return self.every_n

    def _stale_seconds(self) -> float:
        """
        How old evidence may be and still count. At least the configured value,
        and always long enough to hold the minimum clear frames at the rate the
        model is actually running, with some room, so a slow machine is not
        stuck in 'not enough evidence' for ever.
        """
        floor = float(self.config.ppe_stale_s)
        if not self._detect_gaps:
            return floor
        spacing = statistics.median(self._detect_gaps)
        return max(floor, (self.config.ppe_min_clear_frames + 2) * spacing)

    def _detect(self, persons, ppe_frame, pose_shape, t) -> None:
        small, sx, sy = self._prepare(ppe_frame, pose_shape)
        scaled = [{"track_id": p["track_id"],
                   "bbox": [p["bbox"][0] * sx, p["bbox"][1] * sy, p["bbox"][2] * sx, p["bbox"][3] * sy]}
                  for p in persons]
        try:
            detections = self.detector.detect(small, imgsz=self._imgsz(small))
        except Exception as e:
            # Not evidence of anything. Nothing is added to any window.
            self._failures += 1
            self._warn(f"PPE detection failed: {type(e).__name__}: {e}")
            return
        self._failures = 0
        self.detections_run += 1
        if self._prev_detect_t is not None and t > self._prev_detect_t:
            self._detect_gaps.append(t - self._prev_detect_t)
        self._prev_detect_t = t

        seen = associate_frame(scaled, detections, small.shape)
        for p in persons:
            key = worker_key(p["track_id"])
            obs = seen[p["track_id"]]
            self._tracker.update(key, obs, t)
            self._last[key] = dict(obs)

    def _prepare(self, ppe_frame, pose_shape):
        """The frame the PPE model will see, and the factors that map pose-frame pixels onto it."""
        h, w = ppe_frame.shape[:2]
        limit = int(self.config.ppe_max_width)
        if w > limit:
            import cv2
            new_w = limit
            new_h = max(1, int(round(h * limit / float(w))))
            small = cv2.resize(ppe_frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            small = ppe_frame
        pose_h, pose_w = pose_shape[:2]
        sx = small.shape[1] / float(pose_w) if pose_w else 1.0
        sy = small.shape[0] / float(pose_h) if pose_h else 1.0
        return small, sx, sy

    @staticmethod
    def _imgsz(frame) -> int:
        """Longest side rounded up to the network's stride (32), so no pixel is thrown away."""
        side = max(frame.shape[0], frame.shape[1])
        return int(math.ceil(side / 32.0) * 32)

    def _forget_old(self, t: float) -> None:
        horizon = max(self.config.ppe_stale_s * 3.0, 30.0)
        for key in [k for k, seen in self._last_seen.items() if t - seen > horizon]:
            self._tracker.drop(key)
            self._last.pop(key, None)
            self._last_seen.pop(key, None)

    def _unverifiable(self, track_id) -> dict:
        return {"status": UNVERIFIABLE, "helmet": 0.0, "vest": 0.0, "clear": False,
                "helmet_state": UNVERIFIABLE, "vest_state": UNVERIFIABLE}

    def _warn(self, message: str, key: str = "detect") -> None:
        # Once per kind of failure, not per frame: a dead detector would
        # otherwise flood the log, but a NEW kind of failure must still show.
        if key not in self._warned and len(self._warned) < 5:
            self._warned.add(key)
            _log.warning(message)

    # ── engine component ─────────────────────────────────────────────────────

    def reset(self, reason: str, last_seen_s: float, resume_at_s: float):
        """The timeline jumped: evidence from before says nothing about what follows."""
        self._tracker = self._new_tracker()
        self._last.clear()
        self._last_seen.clear()
        self._calls = 0
        self._frame_gaps.clear()
        self._detect_gaps.clear()
        self._prev_t = self._prev_detect_t = None
        return None


# ── the detector, one per process ───────────────────────────────────────────

_detector = None
_detector_failed_for: Optional[tuple] = None      # (path, file mtime or None, when) of the last failed attempt
_lock = threading.Lock()


def get_ppe_detector(config: Optional[SafetyConfig] = None):
    """
    The helmet/vest detector, loaded once per process, or None.

    None when the weights are missing (`*.pt` files and `ppe_training/runs/` are
    git-ignored, so a fresh clone has none) or cannot be loaded. The reason is
    logged once, and sessions then run with PPE unavailable instead of failing.
    """
    global _detector, _detector_failed_for
    from app.cv.ppe_detector import DEFAULT_WEIGHTS
    weights = os.getenv("PPE_MODEL_PATH") or DEFAULT_WEIGHTS
    try:
        signature = (weights, os.path.getmtime(weights))
    except OSError:
        signature = (weights, None)
    with _lock:
        if _detector is not None:
            return _detector
        failed = _detector_failed_for
        if failed and failed[:2] == signature and time.monotonic() - failed[2] < RETRY_LOAD_AFTER_S:
            return None                            # same file, failed a moment ago: not retried per session
        if signature[1] is None:
            _detector_failed_for = (*signature, time.monotonic())
            _log.error(f"PPE model not found at {weights}. Zone events still run; every PPE verdict "
                       f"will be UNVERIFIABLE until the weights are there (or PPE_MODEL_PATH points at them).")
            return None
        try:
            from app.cv.ppe_detector import PpeDetector
            floor = (config or SafetyConfig()).ppe_conf_floor
            _detector = PpeDetector(weights=weights, conf_floor=floor)
            _detector_failed_for = None
            _log.info(f"PPE model loaded: {weights}")
            return _detector
        except Exception as e:
            _detector_failed_for = (*signature, time.monotonic())
            _log.error(f"PPE model could not be loaded ({type(e).__name__}: {e}); PPE verdicts will be UNVERIFIABLE.")
            return None
