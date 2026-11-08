# backend/app/safety/engine.py
"""
The safety engine's frame-level skeleton: the clock, and what to do when the
timeline jumps.

Later steps hang the real machinery on this (zone events in Step 4, PPE
evidence in Step 5, the rule engine in Step 6, ...). They attach as
*components*: any object with

    reset(reason, last_seen_s, resume_at_s) -> iterable of records or None

that closes its open state and returns the records it had to emit. This module
owns the two guarantees every one of them needs:

1. A frame has ONE time, and it is the video's. `begin_frame()` is the only
   place the clock is read.

2. A jump in the timeline can never corrupt a decision. Seeking changes where
   the next frame is in the video. If state carried across a jump, a zone visit
   would span footage that was skipped (or run backwards: a negative dwell),
   and replaying a stretch already processed would raise the same decision a
   second time.

   So on any jump:
     * `reset(reason, ...)` closes open visits as UNVERIFIABLE ("we can no
       longer say how long they stayed"), clears evidence windows and zone
       state, and restarts the clock's generation. Closing records are stamped
       at `last_seen_s`, the last moment actually observed BEFORE the jump,
       never at the position being jumped to, so a visit that began at 25 s
       cannot be closed "at 20 s" and read as minus five seconds;
     * the engine remembers WHICH stretches of video it has processed (not just
       the furthest point). Frames inside one are flagged `replay=True`.
       Components may keep tracking state on them, but must not EMIT events or
       decisions from a replayed frame, so rewinding ten seconds and playing
       them again produces nothing twice. Footage that was skipped over by an
       earlier forward seek and is now visited for the first time is NOT a
       replay: a violation in it must still be raised.

   A backwards step the caller did not announce (a loop, a missed seek path) is
   caught here too and handled as a reset with reason TIME_REGRESSED.

Closing records from a reset are queued and delivered on the next frame as
`FrameTime.closed` (and by `drain()` when a session ends), so there is one path
to the sink whether the jump was announced or detected.

Nothing here touches the database or the network, and nothing here may raise
into the video loop: a failing component is logged and skipped.
"""

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, List, Optional

from app.safety.clock import FrameClock
from app.safety.config import safety_enabled

_log = logging.getLogger(__name__)

# Float equality is exact for a file clock (time is index / fps for the same
# fps), so this only absorbs representation noise.
_EPS = 1e-9


@dataclass(frozen=True)
class FrameTime:
    """What every safety component is told about the frame in front of it."""
    frame_index: Optional[int]       # None on live sources
    video_time_s: float
    observed_at: str                 # ISO-8601 UTC
    replay: bool                     # True: this stretch of video was already processed
    generation: int                  # bumps on every reset
    closed: tuple = ()               # records closed by a reset since the previous frame

    def as_payload(self) -> dict:
        """The part the dashboard may see (no internals)."""
        return {
            "video_time_s": round(self.video_time_s, 3),
            "observed_at": self.observed_at,
            "replay": self.replay,
        }


class SafetyEngine:
    def __init__(self, clock: FrameClock):
        self.clock = clock
        self.components: List[Any] = []
        self._last_video_time: Optional[float] = None   # for regression detection; cleared by a reset
        self._last_seen: Optional[float] = None         # last time actually observed; never cleared
        # Merged, sorted [start, end] stretches of video time already processed.
        self._processed: List[List[float]] = []
        self._tolerance = 1.5 / clock.fps if clock.fps else 0.0   # next consecutive frame
        self._pending_closed: list = []
        self.resets: Deque[dict] = deque(maxlen=20)

    # ── wiring ───────────────────────────────────────────────────────────────

    def register(self, component) -> None:
        """Attach something with `reset(reason, last_seen_s, resume_at_s)`."""
        self.components.append(component)

    @property
    def last_seen_s(self) -> Optional[float]:
        """The last video time a frame was actually processed at (None before the first)."""
        return self._last_seen

    # ── per frame ────────────────────────────────────────────────────────────

    def begin_frame(self, frame_index: Optional[int] = None) -> FrameTime:
        """
        The time of the frame about to be processed. Call exactly once per
        frame, before anything else reads the time.
        """
        video_time, observed_at = self.clock.tick(frame_index)

        if (not self.clock.live and self._last_video_time is not None
                and video_time < self._last_video_time - _EPS):
            # Time went backwards and nobody said so. Treat it as a seek.
            self._do_reset("TIME_REGRESSED", video_time)

        self._last_video_time = video_time
        self._last_seen = video_time

        # A live source never revisits a moment, so it has no replays.
        replay = False if self.clock.live else self._mark_processed(video_time)

        closed, self._pending_closed = tuple(self._pending_closed), []
        return FrameTime(
            frame_index=frame_index,
            video_time_s=video_time,
            observed_at=observed_at,
            replay=replay,
            generation=self.clock.generation,
            closed=closed,
        )

    def _mark_processed(self, t: float) -> bool:
        """
        Record that video time `t` has been processed. Returns True if it
        already had been (this frame is a replay).

        A frame next to an existing stretch extends it, so a straight
        play-through stays one interval however long it runs.
        """
        tol = self._tolerance
        spans = self._processed
        for span in spans:
            if span[0] - _EPS <= t <= span[1] + _EPS:
                return True
        for i, span in enumerate(spans):
            if span[1] < t <= span[1] + tol + _EPS:        # just past the end
                span[1] = t
                self._merge_from(i)
                return False
            if span[0] - tol - _EPS <= t < span[0]:        # just before the start
                span[0] = t
                self._merge_from(max(i - 1, 0))
                return False
        spans.append([t, t])
        spans.sort()
        return False

    def _merge_from(self, i: int) -> None:
        spans = self._processed
        spans.sort()
        merged = [spans[0]]
        for span in spans[1:]:
            if span[0] <= merged[-1][1] + self._tolerance + _EPS:
                merged[-1][1] = max(merged[-1][1], span[1])
            else:
                merged.append(span)
        self._processed = merged

    # ── jumps ────────────────────────────────────────────────────────────────

    def reset(self, reason: str, frame_index: Optional[int] = None) -> None:
        """
        The timeline is about to jump (a seek). Close what is open and start
        clean. The records the components emit while closing are queued for the
        next frame's `closed` (or `drain()`).

        `frame_index` is where the NEXT frame will be. On a live clock it is
        ignored.
        """
        if self.clock.live or frame_index is None:
            resume = self._last_seen if self._last_seen is not None else 0.0
        else:
            resume = self.clock.time_at(frame_index)
        self._do_reset(reason, resume)

    def drain(self) -> list:
        """Closing records no frame has delivered yet (call when a session ends)."""
        out, self._pending_closed = self._pending_closed, []
        return out

    def _do_reset(self, reason: str, resume_at_s: float) -> None:
        # Close at the last moment really observed. Before any frame there is
        # nothing open to close, so the resume time is a harmless stand-in.
        last_seen = self._last_seen if self._last_seen is not None else resume_at_s
        emitted: list = []
        for component in self.components:
            try:
                out = component.reset(reason, last_seen, resume_at_s)
                if out:
                    emitted.extend(out)
            except Exception as e:
                # A reset must never break the video. The component is left as
                # it is; the failure is on record.
                _log.warning(f"safety reset({reason}) failed in {type(component).__name__}: {e}")
        generation = self.clock.restart()
        self._last_video_time = None
        self._pending_closed.extend(emitted)
        self.resets.append({
            "reason": reason,
            "last_seen_s": last_seen,
            "resume_at_s": resume_at_s,
            "generation": generation,
            "closed": len(emitted),
        })


def engine_for_session(
    *,
    fps: Optional[float] = None,
    recorded_at=None,
    live: bool = False,
) -> Optional[SafetyEngine]:
    """
    The engine for one video session, or None when safety is off.

    None means the pipeline runs exactly as it did before the safety layer
    existed: no clock is built, nothing is added to the payload.
    """
    if not safety_enabled():
        return None
    return SafetyEngine(FrameClock(fps=fps, recorded_at=recorded_at, live=live))
