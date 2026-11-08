# backend/app/safety/zone_events.py
"""
Zone events: turn "this worker is in zone X on this frame" into
"entered X at 12.4 s, left X at 45.9 s".

The pipeline only knows where a person is RIGHT NOW. Every safety rule is about
what happened over time (entered, stayed too long, left, vanished), so this is
the layer that makes those facts. It is pure logic: no database, no camera, no
clock. The caller hands in the time `t` (video seconds, from `FrameClock`) and
the zone each worker is in, and gets events back.

THE RULES IT FOLLOWS

* Debounce. A worker only ENTERS a zone after being continuously inside it for
  `enter_confirm_s`, and only LEAVES after being continuously outside for
  `exit_confirm_s`. Someone standing on a polygon edge, with the detection box
  wobbling a few pixels, produces one entry, not forty. The event is stamped
  with when the change really began (the first frame of the new position), not
  with when it was confirmed, so a visit's length is not under-counted.

* A visit is a zone and a worker. Walking straight from zone A into zone B is
  EXITED A and ENTERED B at the same moment. TRANSIT ("outside every zone")
  counts as no zone: it never produces events of its own.

* Losing someone is not the same as them leaving. If a worker who is inside a
  zone stops being seen (occlusion, a missed detection run), the visit is held
  open. After `lost_grace_s` unseen it is reported LOST_IN_ZONE; if they come
  back under the same track it continues (REAPPEARED); if they do not come back
  within `lost_ttl_s` it is closed with EXITED_ZONE and `reason: LOST`. That
  last exit is the tracker saying "I cannot say they left", and rules must read
  it that way: it never counts as a confirmed exit.

* Dwell limits. `watch_dwell()` registers a limit for a zone; DWELL_WARNING
  fires when a visit reaches `warn_fraction` of it and DWELL_EXCEEDED when it
  reaches it, each once per visit, stamped at the moment the threshold was
  crossed. A visit is measured from the (back-dated) entry through short gaps
  in view, and a threshold is only raised on a frame where the worker is seen:
  one crossed while they were out of view is stamped when they came back and
  marked `crossed_unobserved`.

* No event is invented. A worker first seen already inside a zone gets an
  ENTERED_ZONE flagged `first_sighting`: we know they are there, not that they
  walked in. A jump in the timeline (a seek) or the end of the session closes
  every open visit as EXITED_ZONE with `unverifiable: true` and the reason, at
  the last moment actually observed (see engine.py).

EVENT TIMES CAN BE EARLIER THAN THE FRAME THAT PRODUCED THEM (an entry is
back-dated to when it began, a loss to when the worker was last seen). That is
deliberate. They are never LATER than it, and the order within one worker is
always correct.
"""

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from app.safety.config import SafetyConfig

_log = logging.getLogger(__name__)

# What the spatial engine returns for a point outside every drawn zone. It is
# "no zone", never a place anyone enters.
TRANSIT = "TRANSIT_ZONE"

# The zone types safety rules attach to. GENERAL ("ordinary area, no rules") and
# the pre-safety types (WORKSTATION, BREAK, ...) carry no safety meaning, so a
# person walking through them is not a safety event; recording one would only
# bury the real ones.
RULE_ZONE_TYPES = frozenset({"PPE_CHECKPOINT", "APPROACH", "RESTRICTED", "EXIT"})


def rule_zone_ids(zones_config) -> frozenset:
    """The ids of the zones in a camera's config that safety events are made for."""
    return frozenset(
        str(z["zone_id"]) for z in (zones_config or [])
        if str(z.get("zone_type") or "GENERAL").upper() in RULE_ZONE_TYPES
    )


# Workers outside every zone are forgotten once they have been unseen this long,
# so the tracker's memory cannot grow with every track id a long session sees.
_FORGET_AFTER_S = 60.0

_EPS = 1e-9

# A pending change "to no zone". (None already means "no change pending", and a
# real zone id is never an empty string: _norm() turns those into None.)
_NO_ZONE = ""


@dataclass(frozen=True)
class ZoneEvent:
    """One thing the tracker concluded. Mirrors the `safety_events` columns it feeds."""
    event_type: str
    worker_key: str
    zone_id: Optional[str]
    video_time_s: float
    details: dict = field(default_factory=dict)

    def key(self) -> tuple:
        """Identity for duplicate suppression (time deliberately excluded)."""
        return (self.event_type, self.zone_id, tuple(sorted((k, str(v)) for k, v in self.details.items())))


@dataclass
class _Worker:
    first_seen: float
    last_seen: float                      # last frame this worker was seen at all
    current: Optional[str] = None         # confirmed zone, None = none
    entered_at: Optional[float] = None    # back-dated start of the current visit
    candidate: Optional[str] = None       # None: no change pending. _NO_ZONE: leaving to no zone.
    candidate_since: float = 0.0
    left_at: Optional[float] = None       # when they first stopped being in `current` (a pending move, even if its target changed)
    lost_emitted: bool = False
    fired: set = field(default_factory=set)   # dwell events already raised this visit
    last_event_key: Optional[tuple] = None    # duplicate suppression


def _dwell_s(w: "_Worker", until: float) -> float:
    """Seconds from the (back-dated) entry to `until`; never negative. (Entry at 0.0 is a real time.)"""
    if w.entered_at is None:
        return 0.0
    return round(max(0.0, until - w.entered_at), 3)


def _norm(zone) -> Optional[str]:
    """A zone id, or None for 'no zone'."""
    if zone is None or zone == TRANSIT or zone == "":
        return None
    return str(zone)


class ZoneEventTracker:
    """
    One per camera session.

        tracker = ZoneEventTracker(config)
        tracker.watch_dwell("restricted-1", limit_s=120)
        events = tracker.update({"trk-3": "restricted-1", "trk-4": None},
                                {"trk-3", "trk-4"}, t=12.5)

    It is also an engine component: `reset(reason, last_seen_s, resume_at_s)`
    closes every open visit when the timeline jumps.
    """

    def __init__(self, config: Optional[SafetyConfig] = None):
        self.config = config or SafetyConfig()
        self._workers: Dict[str, _Worker] = {}
        # zone_id -> {limit_s: warn_fraction}
        self._dwell: Dict[str, Dict[float, float]] = {}
        self._last_t: Optional[float] = None
        self._intervals: deque = deque(maxlen=9)    # recent frame intervals, for the gap tolerance

    # ── configuration ────────────────────────────────────────────────────────

    def watch_dwell(self, zone_id: str, limit_s: float, warn_fraction: Optional[float] = None) -> None:
        """
        Raise DWELL_WARNING / DWELL_EXCEEDED for visits to `zone_id` that reach
        `limit_s` seconds. Several limits may watch one zone (two rules). Register
        before the session starts: a limit added mid-visit is judged from the
        visit's real start, so it may fire at once for a time already passed.
        """
        zone = _norm(zone_id)
        if zone is None:
            raise ValueError("a dwell limit needs a real zone")
        if isinstance(limit_s, bool) or not isinstance(limit_s, (int, float)) or not math.isfinite(limit_s) or limit_s <= 0:
            raise ValueError(f"limit_s must be a positive number of seconds, got {limit_s!r}")
        fraction = self.config.dwell_warn_fraction if warn_fraction is None else warn_fraction
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction <= 1:
            raise ValueError(f"warn_fraction must be above 0 and at most 1, got {fraction!r}")
        self._dwell.setdefault(zone, {})[float(limit_s)] = float(fraction)

    def clear_dwell_watches(self) -> None:
        self._dwell.clear()

    # ── per frame ────────────────────────────────────────────────────────────

    def update(self, worker_zone: Dict[str, Optional[str]], seen_workers: Optional[Iterable[str]], t: float) -> List[ZoneEvent]:
        """
        Process one frame.

        worker_zone   worker key -> the zone they are in this frame (a zone id,
                      None, or "TRANSIT_ZONE" for no zone).
        seen_workers  every worker visible this frame. None means "exactly the
                      keys of worker_zone". A worker who is in neither is
                      unseen, which is what drives the lost-track handling.
        t             this frame's video time in seconds.

        Returns the events in time order for each worker.
        """
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
            raise ValueError(f"t must be a finite number of seconds >= 0, got {t!r}")
        t = float(t)
        if self._last_t is not None and t < self._last_t - _EPS:
            # The engine announces jumps through reset(); reaching here means the
            # caller skipped that. Refuse rather than build a negative dwell.
            raise ValueError(
                f"time went backwards ({t} after {self._last_t}); the timeline must be reset first")
        if self._last_t is not None and t > self._last_t + _EPS:
            self._intervals.append(t - self._last_t)
        self._last_t = t

        seen = set(worker_zone) if seen_workers is None else set(seen_workers) | set(worker_zone)
        events: List[ZoneEvent] = []

        # Workers already known but not visible now.
        for key in [k for k in self._workers if k not in seen]:
            self._on_unseen(key, self._workers[key], t, events)

        for key in sorted(seen):
            zone = _norm(worker_zone.get(key))
            self._on_seen(key, zone, t, events)

        return events

    def _gap_tolerance(self) -> float:
        """
        How long a worker may be missing from view while a change is being timed
        without the timing restarting: the configured value, but never less than
        about two frame intervals. At 2 fps one missed detection is already a
        second, and a fixed 0.5 s would make every change at CPU speeds restart
        for ever.
        """
        if not self._intervals:
            return self.config.zone_gap_tolerance_s
        return max(self.config.zone_gap_tolerance_s, 2.5 * statistics.median(self._intervals))

    # ── a worker who is visible ─────────────────────────────────────────────

    def _on_seen(self, key: str, zone: Optional[str], t: float, out: List[ZoneEvent]) -> None:
        w = self._workers.get(key)
        if w is not None:
            # Unseen for so long that the visit should already have been closed.
            self._check_lost(key, w, t, out)
            w = self._workers.get(key)
        if w is None:
            w = _Worker(first_seen=t, last_seen=t)
            self._workers[key] = w
        previous_seen = w.last_seen
        gap = t - previous_seen
        unobserved = gap > self._gap_tolerance()

        # Back from a gap that was long enough to be reported as lost.
        if w.lost_emitted:
            self._emit(out, w, ZoneEvent("REAPPEARED", key, w.current, t,
                                         {"gap_s": round(gap, 3), "last_seen_s": round(w.last_seen, 3)}))
            w.lost_emitted = False

        # A change that was being timed needs the worker in view throughout: after
        # a gap longer than a blink it starts again from this frame.
        if w.candidate is not None and unobserved:
            w.candidate = None
            w.left_at = None

        w.last_seen = t

        if zone == w.current:
            w.candidate = None
            w.left_at = None
        else:
            target = zone if zone is not None else _NO_ZONE
            if w.candidate != target:
                # They were already on their way out of `current` when the target
                # changed (out, then straight into another zone): the exit began
                # when they first stopped being here, not when the second move began.
                if w.candidate == _NO_ZONE and w.left_at is None:
                    w.left_at = w.candidate_since
                w.candidate, w.candidate_since = target, t
            confirm = self.config.enter_confirm_s if zone is not None else self.config.exit_confirm_s
            if t - w.candidate_since >= confirm - _EPS:
                self._confirm_change(key, w, zone, t, out)

        # A threshold crossed while they were out of view is reported when they
        # come back, not stamped inside the gap (that would put it before events
        # already raised for it, such as LOST_IN_ZONE).
        self._dwell_events(key, w, self._dwell_horizon(w, t), out,
                           unobserved_after=previous_seen if unobserved else None, stamp_at=t)
        self._forget_if_idle(key, w, t)

    @staticmethod
    def _dwell_horizon(w: _Worker, t: float) -> float:
        """
        How far into the visit dwell may be counted. While the worker appears to
        be leaving, only up to the moment they started to: if the exit is
        confirmed the visit ended there, and a warning stamped after its own
        EXITED would contradict it. If they turn back, the next frame counts the
        time normally.
        """
        if w.candidate is None:
            return t
        return w.left_at if w.left_at is not None else w.candidate_since

    def _confirm_change(self, key: str, w: _Worker, zone: Optional[str], t: float, out: List[ZoneEvent]) -> None:
        change_at = w.candidate_since
        first = w.current is None and change_at <= w.first_seen + _EPS
        if w.current is not None:
            left_at = w.left_at if w.left_at is not None else change_at
            # Count dwell up to the moment they left, then close the visit.
            self._dwell_events(key, w, left_at, out)
            self._emit(out, w, ZoneEvent("EXITED_ZONE", key, w.current, left_at, {
                "reason": "LEFT", "dwell_s": _dwell_s(w, left_at)}))
        w.fired = set()
        w.lost_emitted = False
        w.left_at = None
        if zone is not None:
            # A worker first seen already inside a zone: we know they are there,
            # not that they walked in. Rules decide what that is worth.
            self._emit(out, w, ZoneEvent("ENTERED_ZONE", key, zone, change_at,
                                         {"first_sighting": True} if first else {}))
            w.entered_at = change_at
        else:
            w.entered_at = None
        w.current = zone
        w.candidate = None

    # ── a worker who is not visible ─────────────────────────────────────────

    def _on_unseen(self, key: str, w: _Worker, t: float, out: List[ZoneEvent]) -> None:
        self._check_lost(key, w, t, out)
        w = self._workers.get(key)
        if w is None:
            return
        # A change that was being timed needs the worker in view the whole time.
        if t - w.last_seen > self._gap_tolerance():
            w.candidate = None
            w.left_at = None
        self._forget_if_idle(key, w, t)

    def _check_lost(self, key: str, w: _Worker, t: float, out: List[ZoneEvent]) -> None:
        """Report the loss / close the visit if the worker has been unseen long enough."""
        if w.current is None:
            return
        unseen = t - w.last_seen
        if not w.lost_emitted and unseen > self.config.lost_grace_s + _EPS:
            self._emit(out, w, ZoneEvent("LOST_IN_ZONE", key, w.current, w.last_seen,
                                         {"last_seen_s": round(w.last_seen, 3), "declared_at_s": round(t, 3)}))
            w.lost_emitted = True
        if unseen > self.config.lost_ttl_s + _EPS:
            if not w.lost_emitted:                              # grace == ttl corner
                self._emit(out, w, ZoneEvent("LOST_IN_ZONE", key, w.current, w.last_seen,
                                             {"last_seen_s": round(w.last_seen, 3), "declared_at_s": round(t, 3)}))
            self._dwell_events(key, w, w.last_seen, out)
            self._emit(out, w, ZoneEvent("EXITED_ZONE", key, w.current, w.last_seen, {
                "reason": "LOST", "unverifiable": True,
                "dwell_s": _dwell_s(w, w.last_seen),
                "last_seen_s": round(w.last_seen, 3), "declared_at_s": round(t, 3)}))
            del self._workers[key]

    def _forget_if_idle(self, key: str, w: _Worker, t: float) -> None:
        if w.current is None and w.candidate is None and t - w.last_seen > _FORGET_AFTER_S:
            self._workers.pop(key, None)

    # ── dwell ────────────────────────────────────────────────────────────────

    def _dwell_events(self, key: str, w: _Worker, up_to: float, out: List[ZoneEvent],
                      unobserved_after: Optional[float] = None, stamp_at: Optional[float] = None) -> None:
        """
        Raise the dwell events whose threshold the visit has reached by `up_to`,
        each stamped at the moment it was crossed. One crossed while the worker
        was out of view (after `unobserved_after`) is stamped `stamp_at` and says so.
        """
        limits = self._dwell.get(w.current) if w.current is not None else None
        if not limits or w.entered_at is None:
            return
        for limit in sorted(limits):
            warn_at = w.entered_at + limits[limit] * limit
            exceed_at = w.entered_at + limit
            if limits[limit] < 1.0 and ("W", limit) not in w.fired and warn_at <= up_to + _EPS:
                w.fired.add(("W", limit))
                d = {"limit_s": limit, "warn_fraction": limits[limit]}
                at = warn_at
                if unobserved_after is not None and warn_at > unobserved_after + _EPS:
                    at, d["crossed_unobserved"] = stamp_at, True
                self._emit(out, w, ZoneEvent("DWELL_WARNING", key, w.current, at, d))
            if ("X", limit) not in w.fired and exceed_at <= up_to + _EPS:
                w.fired.add(("X", limit))
                d = {"limit_s": limit}
                at = exceed_at
                if unobserved_after is not None and exceed_at > unobserved_after + _EPS:
                    at, d["crossed_unobserved"] = stamp_at, True
                self._emit(out, w, ZoneEvent("DWELL_EXCEEDED", key, w.current, at, d))

    # ── emitting ─────────────────────────────────────────────────────────────

    def _emit(self, out: List[ZoneEvent], w: _Worker, event: ZoneEvent) -> None:
        """Append unless it exactly repeats this worker's previous event."""
        k = event.key()
        if k == w.last_event_key:
            _log.debug(f"duplicate {event.event_type} for {event.worker_key} dropped")
            return
        w.last_event_key = k
        out.append(event)

    # ── jumps and the end of a session ──────────────────────────────────────

    def close_all(self, reason: str, last_seen_s: float) -> List[ZoneEvent]:
        """
        Close every open visit as unverifiable. Each is stamped at the moment its
        worker was last actually seen (never after `last_seen_s`, never before the
        visit began), and all tracking state is dropped.
        """
        out: List[ZoneEvent] = []
        for key in sorted(self._workers):
            w = self._workers[key]
            if w.current is None:
                continue
            at = min(w.last_seen, last_seen_s)
            at = max(at, w.entered_at if w.entered_at is not None else at)
            self._dwell_events(key, w, at, out)
            self._emit(out, w, ZoneEvent("EXITED_ZONE", key, w.current, at, {
                "reason": reason, "unverifiable": True,
                "dwell_s": _dwell_s(w, at),
                "last_seen_s": round(at, 3)}))
        self._workers.clear()
        self._last_t = None
        self._intervals.clear()
        return out

    def reset(self, reason: str, last_seen_s: float, resume_at_s: float) -> List[ZoneEvent]:
        """Engine component contract: the timeline jumped."""
        return self.close_all(reason, last_seen_s)

    # ── inspection (tests and the live overlay) ─────────────────────────────

    def zone_of(self, worker_key: str) -> Optional[str]:
        """The worker's confirmed zone, or None."""
        w = self._workers.get(worker_key)
        return w.current if w else None

    def open_visits(self) -> Dict[str, str]:
        return {k: w.current for k, w in self._workers.items() if w.current is not None}
