# backend/app/safety/clock.py
"""
The safety layer's clock: time comes from the VIDEO, not from the machine.

WHY THIS EXISTS

A dwell limit of 45 seconds means 45 seconds of footage. Measured with the
machine's wall clock, an uploaded clip processed at ten times real speed would
report a 4.5-second visit, and the same clip on a slower laptop would report a
different one. Every safety timing (confirm-before-entry debounce, dwell,
lost-track grace, man-down duration, lone-worker grace) therefore reads this
clock and nothing else.

TWO KINDS OF SOURCE

* A recorded file (`live=False`). A frame's time is where it sits in the
  video: `video_time_s = frame_index / fps`. It is a pure function of the frame
  index, so the same clip gives the same times at any processing speed, on any
  machine, any number of times.

* A live source (`live=True`: webcam, phone, browser camera). There is no
  frame index worth trusting, so time is seconds since the clock was created,
  read from a MONOTONIC clock and never allowed to go backwards.

`observed_at` is the wall-clock moment the frame represents, as an ISO-8601
UTC string. For a file it is the recording time plus the video time, so events
land on the timeline where they really happened: processing yesterday's
footage today still reports yesterday. When the recording time is unknown it is
anchored to the moment the clock was created, which keeps every OFFSET correct
even though the absolute date is only the upload time.

WHAT THIS DOES NOT DO

It does not know about seeking. A seek moves where the next frame is in the
video, so video time can jump; the SafetyEngine (engine.py) owns what to do
about that (reset state, suppress replayed decisions). The clock only reports
where a frame sits.

The wall clock and the monotonic clock are injectable so tests can prove the
claims above without sleeping.
"""

import math
import time
from datetime import datetime, timedelta, timezone
from numbers import Integral, Real
from typing import Callable, Optional, Tuple, Union

# A recording cannot have been made in the future. A small allowance covers a
# camera whose own clock runs ahead; beyond it the value is a typo (wrong year)
# and silently accepting it would stamp every event with a date that has not
# happened yet.
FUTURE_TOLERANCE = timedelta(hours=24)

# Nothing in this product was recorded before this.
EARLIEST_YEAR = 2000

# A real camera never reports a frame rate outside this. Bounding it here makes
# an absurd value fail when the session starts, instead of overflowing the
# millisecond arithmetic half-way through a recording.
MIN_FPS = 0.01
MAX_FPS = 1000.0


def utc_iso(moment: datetime) -> str:
    """An aware datetime as ISO-8601 UTC with millisecond precision."""
    if moment.tzinfo is None:
        raise ValueError("utc_iso needs a timezone-aware datetime")
    moment = moment.astimezone(timezone.utc)
    # Round to the nearest millisecond (isoformat would TRUNCATE, which put an event at video time
    # 0 a millisecond BEFORE the recording started whenever float arithmetic landed just under).
    moment = moment.replace(microsecond=0) + timedelta(milliseconds=round(moment.microsecond / 1000.0))
    return moment.isoformat(timespec="milliseconds")


def parse_recorded_at(
    value: Union[str, datetime, None],
    *,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    A recording time as an aware UTC datetime, or None when none was given.

    Accepts an ISO-8601 string ("2026-11-08T09:30:00+05:30", "...Z", or a bare
    "2026-11-08T09:30:00") or a datetime. A value with NO timezone is read as
    UTC rather than guessed as local time: the server does not know where the
    camera was, and a guess that is off by hours is worse than a stated
    convention. A value with an offset is converted to UTC.

    Blank input is "not given", not an error. Anything unparseable, before
    2000, or more than a day in the future raises ValueError with a message
    that is safe to show the caller.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # `fromisoformat` only learned the trailing "Z" in Python 3.11; the
        # browser sends exactly that, so normalise it here and the field works
        # on any interpreter.
        if text[-1] in "Zz":
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(
                "recorded_at must be an ISO-8601 date and time, "
                "for example 2026-11-08T09:30:00+05:30"
            ) from None
    elif isinstance(value, datetime):
        moment = value
    else:
        raise ValueError("recorded_at must be an ISO-8601 string")

    try:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
    except OverflowError:
        # "9999-12-31T23:59:59-05:00" cannot be expressed in UTC. It is out of
        # range, which is a bad value, not a server error.
        raise ValueError("recorded_at is out of range") from None

    if moment.year < EARLIEST_YEAR:
        raise ValueError(f"recorded_at is before {EARLIEST_YEAR}; check the year")
    reference = now or datetime.now(timezone.utc)
    if moment > reference + FUTURE_TOLERANCE:
        raise ValueError("recorded_at is in the future; a recording cannot have been made yet")
    return moment


def _frame_number(frame_index) -> int:
    """A validated, non-negative integer frame index."""
    if isinstance(frame_index, bool) or frame_index is None:
        raise ValueError("a recorded file needs an integer frame_index")
    if isinstance(frame_index, Integral):
        number = int(frame_index)
    elif isinstance(frame_index, Real) and math.isfinite(frame_index) and float(frame_index).is_integer():
        number = int(frame_index)
    else:
        raise ValueError(f"frame_index must be a whole number, got {frame_index!r}")
    if number < 0:
        raise ValueError(f"frame_index must not be negative, got {number}")
    return number


class FrameClock:
    """
    Maps a frame to (video_time_s, observed_at).

        clock = FrameClock(fps=25.0, recorded_at="2026-11-08T09:30:00+05:30")
        clock.tick(0)     # (0.0,  "2026-11-08T04:00:00.000+00:00")
        clock.tick(50)    # (2.0,  "2026-11-08T04:00:02.000+00:00")

        live = FrameClock(live=True)
        live.tick()       # (0.0, "<now>"); later calls only ever increase

    `generation` counts restarts (see restart()). It lets a consumer holding
    state from before a seek see that the clock has moved on.
    """

    def __init__(
        self,
        fps: Optional[float] = None,
        recorded_at: Union[str, datetime, None] = None,
        live: bool = False,
        *,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ):
        self.live = bool(live)
        self._wall = wall
        self._mono = mono
        self.generation = 0

        if self.live:
            if recorded_at not in (None, ""):
                raise ValueError("a live clock has no recorded_at")
            self.fps = None
            self.anchor = None
            self._t0 = mono()
            self._last_live = 0.0
        else:
            if (fps is None or isinstance(fps, bool) or not isinstance(fps, Real)
                    or not math.isfinite(fps) or not (MIN_FPS <= fps <= MAX_FPS)):
                raise ValueError(
                    f"a recorded file needs an fps between {MIN_FPS} and {MAX_FPS}, got {fps!r}")
            self.fps = float(fps)
            # The recording time when known, otherwise the moment this clock
            # was created: offsets stay exact, only the absolute date is "now".
            self.anchor = parse_recorded_at(recorded_at) or datetime.fromtimestamp(wall(), timezone.utc)

    def tick(self, frame_index: Optional[int] = None) -> Tuple[float, str]:
        """(video_time_s, observed_at_iso) for this frame."""
        if self.live:
            # Monotonic and never backwards, even if the underlying clock
            # misbehaves. The frame index is ignored: a live source has none
            # worth trusting.
            elapsed = max(self._last_live, self._mono() - self._t0)
            self._last_live = elapsed
            observed = utc_iso(datetime.fromtimestamp(self._wall(), timezone.utc))
            return elapsed, observed

        number = _frame_number(frame_index)
        video_time = number / self.fps
        # Whole milliseconds, rounded, via integer arithmetic on the offset, so
        # the string is a pure function of the frame index (no float drift
        # between runs or machines).
        offset = timedelta(milliseconds=round(video_time * 1000))
        return video_time, utc_iso(self.anchor + offset)

    def time_at(self, frame_index: int) -> float:
        """Video time of a frame without advancing anything (file clocks)."""
        if self.live:
            raise ValueError("a live clock cannot be asked about an arbitrary frame")
        return _frame_number(frame_index) / self.fps

    def restart(self) -> int:
        """
        Note that the position has jumped (a seek). Returns the new generation.

        A file clock is stateless — time is a function of the frame index — so
        there is nothing to rewind; the generation is what tells consumers the
        timeline they were following is gone.
        """
        self.generation += 1
        return self.generation
