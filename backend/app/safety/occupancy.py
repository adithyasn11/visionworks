# backend/app/safety/occupancy.py
"""
Lone worker and occupancy limits: "never alone in this zone" and "no more than N people".

An OCCUPANCY rule is anchored to a zone and gives `min_people`, `max_people` or both, a `grace_seconds`
(how long the situation must last) and a `window_seconds` (how much the count is smoothed).

THE COUNT

  Every frame, the people the tracker puts in the zone are counted from the same entities the zone events are
  made from (the count is not redone). A person counts as CLEAR when the detector is confident of them
  (`occupancy_min_person_conf`), they are not mostly covered by or covering another person
  (`occupancy_overlap_iou`) and they are not tiny (`occupancy_min_height_px`). The number used is the MEDIAN
  over the last `window_seconds`, so a person the detector drops for one frame cannot change it.

  For "too few" the count also includes people the zone tracker still holds as inside but cannot see right now
  (they walked behind something): someone who was there a moment ago has not left, and "alone" must not be said
  while a second person may simply be hidden. For "too many" only people actually seen are counted.

THE CONDITIONS

  LONE_WORKER    at least one person seen, and (seen + hidden) is below `min_people`, without a break for
                 `grace_seconds`. An empty zone is nobody being alone: it is not flagged, and a zone whose last
                 person just left is not flagged either.
  OVER_CAPACITY  more than `max_people` seen, without a break for `grace_seconds`.

  Either one lasting the whole grace raises ONE decision for the episode. A break shorter than `RECOVER_S` is
  part of the same episode; a new episode within `realert_after_s` of the last flag of the same kind is the
  same story and is not flagged again (two people who keep coming and going do not raise a flag every minute).

WHEN THE COUNT CANNOT BE TRUSTED

  If, over the stretch that would be flagged, fewer than `occupancy_min_clear_fraction` of the people counted
  were CLEAR (a crowd, people overlapping, far away, half hidden), the decision is UNVERIFIABLE: "the count could
  not be trusted", not an accusation. It is said once, and the picture is then read afresh from that moment: if
  people separate and a clear stretch lasts the whole grace, that is flagged.

The decision names the zone, how many were seen, for how long and what the rule asks. A lone-worker decision
carries the one person's key (so their outline shows), an over-capacity one the zone's ("zone-<id>").
"""

import statistics
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.safety.config import SafetyConfig
from app.safety.schemas import POTENTIAL_VIOLATION, UNVERIFIABLE, Decision, Rule

RECOVER_S = 1.0          # the situation must be over this long to end an episode
MIN_SAMPLES = 3          # the median needs at least this many frames in its window


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class _Episode:
    """One of the two conditions (too few / too many) for one rule."""

    def __init__(self):
        self.reset()
        self.last_flag_t: Optional[float] = None

    def reset(self):
        self.started: Optional[float] = None         # when the situation began (the whole episode)
        self.since: Optional[float] = None           # start of the stretch being judged (moves on after a "could not tell")
        self.false_since: Optional[float] = None
        self.judged = False
        self.warned = False
        self.sum_n = 0
        self.sum_clear = 0
        self.max_unseen = 0
        self.counts: List[int] = []
        self.key: Optional[str] = None


class OccupancyDetector:
    """One OCCUPANCY rule."""

    def __init__(self, rule: Rule, config: Optional[SafetyConfig] = None, zone_name: Optional[str] = None):
        self.rule = rule
        self.config = config or SafetyConfig()
        self.zone_name = zone_name or rule.zone_name or str(rule.zone_id)
        p = rule.params
        self.min_people: Optional[int] = None if p.get("min_people") is None else int(p["min_people"])
        self.max_people: Optional[int] = None if p.get("max_people") is None else int(p["max_people"])
        self.grace = float(p.get("grace_seconds", self.config.occupancy_grace_s))
        self.window = float(p.get("window_seconds", self.config.occupancy_window_s))
        self._samples: Deque[Tuple[float, int, int, int]] = deque()   # (t, seen, seen + hidden, clear)
        self._lone = _Episode()
        self._over = _Episode()
        self._last_t: Optional[float] = None

    # ── per frame ───────────────────────────────────────────────────────────

    def update(self, t: float, people: List[dict], hidden: int = 0) -> List[Decision]:
        """
        people: the people seen in the zone this frame, [{"worker_key", "clear"}].
        hidden: people the zone tracker still holds as inside that are not in view this frame.
        """
        if self._last_t is not None and t - self._last_t > max(5.0, 3.0 * self.window):
            self.reset()                                     # a long gap in the frames: the old samples say nothing about now
        seen = len(people)
        clear = sum(1 for p in people if p.get("clear"))
        self._samples.append((t, seen, seen + max(0, int(hidden)), clear))
        while len(self._samples) > MIN_SAMPLES and t - self._samples[0][0] > self.window:
            self._samples.popleft()                          # (always keeping MIN_SAMPLES: at a slow frame rate the window is wider)
        self._last_t = t
        window = list(self._samples)
        if len(window) < MIN_SAMPLES or (t - window[0][0] < 0.5 * self.window and len(window) < 2 * MIN_SAMPLES):
            return []                                        # not enough of a window yet to say anything
        med_seen = statistics.median(s[1] for s in window)
        med_eff = statistics.median(s[2] for s in window)

        out: List[Decision] = []
        lone = self.min_people is not None and med_seen >= 1 and med_eff < self.min_people
        over = self.max_people is not None and med_seen > self.max_people
        # The smoothed count lags the picture by up to half a window. A decision is only made while the
        # picture of THIS frame agrees with it: nobody is flagged for being alone in a zone they have just left.
        d = self._episode(self._lone, "LONE_WORKER", lone, t, seen, clear, hidden, people, med_seen, seen >= 1)
        if d:
            out.append(d)
        d = self._episode(self._over, "OVER_CAPACITY", over, t, seen, clear, hidden, people, med_seen,
                          self.max_people is not None and seen > self.max_people)
        if d:
            out.append(d)
        return out

    def _episode(self, ep: _Episode, kind: str, cond: bool, t: float, seen: int, clear: int, hidden: int,
                 people: List[dict], median_count: float, now_ok: bool = True) -> Optional[Decision]:
        if not cond:
            if ep.started is not None:
                if ep.false_since is None:
                    ep.false_since = t
                elif t - ep.false_since >= RECOVER_S:
                    ep.reset()                               # over: the next time is a new episode
            return None
        ep.false_since = None
        if ep.started is None:
            ep.started = ep.since = t
        ep.sum_n += seen
        ep.sum_clear += clear
        ep.max_unseen = max(ep.max_unseen, hidden)
        ep.counts.append(seen)
        if kind == "LONE_WORKER" and len(people) == 1:
            ep.key = people[0]["worker_key"]
        elif kind == "OVER_CAPACITY":
            ep.key = f"zone-{self.rule.zone_id}"
        if ep.judged or t - ep.since < self.grace or not now_ok:
            return None
        fraction = ep.sum_clear / ep.sum_n if ep.sum_n else 0.0
        if fraction < self.config.occupancy_min_clear_fraction:
            if ep.warned:
                ep.judged = True                             # already said it could not be read; nothing more to say
                return None
            ep.warned = True
            d = self._decision(ep, kind, UNVERIFIABLE, t, fraction, median_count)
            ep.since, ep.sum_n, ep.sum_clear, ep.counts = t, 0, 0, []
            return d
        ep.judged = True
        if ep.last_flag_t is not None and t - ep.last_flag_t < self.config.realert_after_s:
            return None                                      # the same story as the last flag
        ep.last_flag_t = t
        return self._decision(ep, kind, POTENTIAL_VIOLATION, t, fraction, median_count)

    # ── the decisions ───────────────────────────────────────────────────────

    def _decision(self, ep: _Episode, kind: str, status: str, t: float, fraction: float, median_count: float) -> Decision:
        typical = int(round(statistics.median(ep.counts))) if ep.counts else int(round(median_count))
        who = lambda n: "1 person was" if n == 1 else f"{n} people were"          # noqa: E731
        began = ep.since if ep.since is not None else ep.started        # the stretch this decision is about (after a "could not tell", the clear one)
        lasted = max(0.0, t - began)
        if status == UNVERIFIABLE:
            reason = (f"The head count in {self.zone_name} could not be trusted for {lasted:.0f} s (people were overlapping, "
                      f"far away or partly hidden), so no one is flagged. About {typical} {'person was' if typical == 1 else 'people were'} seen.")
            outcome = "CROWDED"
        elif kind == "LONE_WORKER":
            more = f" (and {ep.max_unseen} more not in view)" if ep.max_unseen else ""
            reason = (f"Only {typical} {'person was' if typical == 1 else 'people were'} seen in {self.zone_name} for {lasted:.0f} s{more}; "
                      f"this rule asks for at least {self.min_people}.")
            outcome = kind
        else:
            reason = f"{who(typical).capitalize()} seen in {self.zone_name} for {lasted:.0f} s; this rule allows at most {self.max_people}."
            outcome = kind
        evidence = {
            "outcome": outcome, "people_seen": typical, "min_people": self.min_people, "max_people": self.max_people,
            "seconds": round(lasted, 1), "grace_seconds": self.grace, "window_seconds": self.window,
            "clear_fraction": round(fraction, 3), "hidden_people": ep.max_unseen, "frames": len(ep.counts),
            "began_video_time_s": round(began, 2), "zone_name": self.zone_name,
        }
        c = self.config
        thresholds = {
            "min_people": self.min_people, "max_people": self.max_people, "grace_seconds": self.grace,
            "window_seconds": self.window, "min_clear_fraction": c.occupancy_min_clear_fraction,
            "min_person_conf": c.occupancy_min_person_conf, "overlap_iou": c.occupancy_overlap_iou,
            "min_person_height_px": c.occupancy_min_height_px, "realert_after_s": c.realert_after_s,
            "recover_s": RECOVER_S, "min_evidence_confidence": self.rule.min_evidence_confidence,
        }
        return Decision(
            status=status, reason=reason[:500], confidence=0.0 if status == UNVERIFIABLE else min(1.0, round(fraction, 3)),
            evidence=evidence, rule_type="OCCUPANCY", severity=self.rule.severity,
            worker_key=ep.key or f"zone-{self.rule.zone_id}", zone_id=self.rule.zone_id, video_time_s=began,
            rule_id=self.rule.id, thresholds=thresholds, snapshot_requested=(status == POTENTIAL_VIOLATION))

    def reset(self) -> None:
        self._samples.clear()
        self._lone.reset()
        self._over.reset()


class OccupancyMonitor:
    """All of a session's OCCUPANCY rules."""

    def __init__(self, rules: List[Rule], config: Optional[SafetyConfig] = None, zone_names: Optional[Dict[str, str]] = None):
        self.config = config or SafetyConfig()
        names = zone_names or {}
        self.detectors = [OccupancyDetector(r, self.config, names.get(str(r.zone_id)))
                          for r in rules if r.type == "OCCUPANCY" and r.enabled and r.zone_id]

    def __bool__(self) -> bool:
        return bool(self.detectors)

    def _clear_flags(self, detections) -> Dict[object, bool]:
        """{track id: is this person clearly countable?} for everyone detected this frame."""
        c = self.config
        boxes = [(d.get("track_id"), [float(v) for v in d["bbox"]], float(d.get("confidence", 1.0))) for d in detections]
        flags = {}
        for tid, box, conf in boxes:
            if tid is None:
                continue
            tall = (box[3] - box[1]) >= c.occupancy_min_height_px
            overlapped = any(_iou(box, ob) >= c.occupancy_overlap_iou for other, ob, _ in boxes if other != tid)
            flags[tid] = bool(conf >= c.occupancy_min_person_conf and tall and not overlapped)
        return flags

    def update(self, t: float, entities, detections, worker_key: Callable, hidden_in_zone: Callable[[str], int],
               zone_of: Optional[Callable[[str], Optional[str]]] = None) -> List[Decision]:
        """
        A person is in a zone when the picture puts their feet there OR the zone tracker has them confirmed inside
        (`zone_of`): someone standing on the edge of the polygon, whose foot point flickers across it, is still there.
        """
        flags = self._clear_flags(detections)
        by_zone: Dict[str, List[dict]] = {}
        for e in entities:
            tid = e.get("track_id")
            if tid is None:
                continue
            key = worker_key(tid)
            zones = {str(e["zone_id"])} if e.get("zone_id") is not None else set()
            confirmed = zone_of(key) if zone_of is not None else None
            if confirmed is not None:
                zones.add(str(confirmed))
            for z in zones:
                by_zone.setdefault(z, []).append({"worker_key": key, "clear": flags.get(tid, False)})
        out: List[Decision] = []
        for d in self.detectors:
            zone = str(d.rule.zone_id)
            out.extend(d.update(t, by_zone.get(zone, []), hidden_in_zone(zone)))
        return out

    def reset(self, reason=None, last_seen_s=None, resume_at_s=None) -> list:
        """Engine component contract: the timeline jumped; a count across the jump means nothing."""
        for d in self.detectors:
            d.reset()
        return []
