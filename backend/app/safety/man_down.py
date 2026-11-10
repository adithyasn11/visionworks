# backend/app/safety/man_down.py
"""
Man-down: a person who has collapsed and is not moving.

Every frame, for every person the pose model found, three questions are asked:

  Is the body lying?   The angle of the shoulder-midpoint -> hip-midpoint line from vertical, only when both
                       shoulders and both hips are seen with confidence (> `KEYPOINT_CONF`). Above
                       `man_down_angle_deg` (60 degrees) the torso is closer to flat than upright.
  Is the person still? How far the middle of the person's box moved in the last 0.6 s, as a share of the
                       person's size per second (the longer side of the box, so a lying person, whose box is
                       wide, is measured by their length). Below `man_down_still_speed` is still. This is
                       the box, not the posture label: it does not depend on the classifier.
  Can we tell?         Weak keypoints leave the angle unknown. A still, wide box (width over `ASPECT_DOWN` times
                       height) with unknown angle is "maybe lying", never a certainty.

Each frame of a person is one of

  UP        moving, or still and upright, or excluded (sitting upright, or in a place where lying is allowed)
  DOWN      still, the torso clearly lying
  UNCLEAR   still, and it might be lying but the keypoints or the picture cannot say

THE POSTURE LABEL IS NOT TRUSTED FOR "LYING"

  The existing classifier (cv/pose_estimator.py) labels a person lying flat as SITTING, because the knees are level
  with the hips. So SITTING excludes a frame only when the torso is upright (under `man_down_upright_deg`, 45 degrees),
  or when the angle is unknown and the box is not wide. Someone lying flat and labelled SITTING still counts.

ONE EPISODE, ONE DECISION

  An episode begins at the first non-UP frame of a still person. The person is UP again once `RECOVER_S` of
  consecutive UP frames has passed: a single bad pose frame does not reset five seconds of lying, and a person who
  gets up does. After `min_seconds_down` (the rule's, default 5 s):

     at least `CLEAR_FRACTION` (60 %) of the episode's frames are DOWN   -> POTENTIAL_VIOLATION, CRITICAL, picture wanted
     otherwise we keep watching, and when the episode is twice as old
     as the rule's time and still not clear                              -> UNVERIFIABLE (a warning, not an emergency)

  Either way the episode is judged once. A person who gets up and goes down again later is a new episode.

A person who is lost (not seen for `LOST_S`) ends their episode, and so does a jump of the timeline: nobody can say
how long they were down.
"""

import math
import statistics
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from app.safety.config import SafetyConfig
from app.safety.schemas import POTENTIAL_VIOLATION, UNVERIFIABLE, Decision, Rule

KEYPOINT_CONF = 0.3          # a torso keypoint counts at or above this
ASPECT_DOWN = 1.2            # box width / height above this looks lying, when the keypoints cannot say
ASPECT_UPRIGHT = 0.9         # a box narrower than this is a person on their feet, however far the torso leans (bent over a bench)
ASPECT_LYING = 1.1           # a box at least this wide, with a flat torso, is a person lying
CLEAR_FRACTION = 0.6         # share of an episode's frames that must be clearly DOWN to name it
RECOVER_S = 1.0              # this long upright (or moving) ends an episode
LOST_S = 1.5                 # unseen this long ends an episode
MOTION_WINDOW_S = 0.6        # how far back the box's movement is measured (the fall itself is movement, so this is also the lag before "still")
IOU_CARRY = 0.3              # a new track id that overlaps the box of a person lost a moment ago is the same person

UP, DOWN, UNCLEAR = "UP", "DOWN", "UNCLEAR"

_L_SHOULDER, _R_SHOULDER, _L_HIP, _R_HIP = 5, 6, 11, 12


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def torso_angle(keypoints) -> Optional[float]:
    """Degrees from vertical of the shoulder-midpoint -> hip-midpoint line, or None when a torso keypoint is weak."""
    try:
        pts = [keypoints[i] for i in (_L_SHOULDER, _R_SHOULDER, _L_HIP, _R_HIP)]
        if any(float(p[2]) < KEYPOINT_CONF for p in pts):
            return None
        sx = (float(pts[0][0]) + float(pts[1][0])) / 2.0
        sy = (float(pts[0][1]) + float(pts[1][1])) / 2.0
        hx = (float(pts[2][0]) + float(pts[3][0])) / 2.0
        hy = (float(pts[2][1]) + float(pts[3][1])) / 2.0
    except (TypeError, ValueError, IndexError):
        return None
    dx, dy = abs(hx - sx), abs(hy - sy)
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy))


class _Episode:
    def __init__(self, started_at: float, zone_id: Optional[str]):
        self.started_at = started_at
        self.zone_id = zone_id
        self.frames = 0
        self.down = 0
        self.unclear = 0
        self.angles: List[float] = []
        self.postures: Dict[str, int] = {}
        self.judged = False                 # the alert (or, with nothing better, the "could not tell") was said
        self.warned = False                 # a "could not tell" was said; a clear fall afterwards still alerts
        self.since = started_at             # start of the stretch of frames the next judgement reads
        self.up_since: Optional[float] = None


class _Person:
    def __init__(self):
        self.track: Deque[Tuple[float, float, float]] = deque()    # (t, centre x, centre y)
        self.last_seen: float = 0.0
        self.last_bbox: Optional[List[float]] = None
        self.zone_seen: float = 0.0                                 # last time inside the rule's zone
        self.episode: Optional[_Episode] = None


class ManDownDetector:
    """One MAN_DOWN rule. `update` takes one frame's people and returns the decisions it produces."""

    def __init__(self, rule: Rule, config: Optional[SafetyConfig] = None, zone_names: Optional[Dict[str, str]] = None):
        self.rule = rule
        self.config = config or SafetyConfig()
        self.zone_names = dict(zone_names or {})
        self.min_s = float(rule.params.get("min_seconds_down", self.config.man_down_min_s))
        self.allowed = {str(z) for z in (rule.params.get("lying_allowed_zone_ids") or [])}
        self._people: Dict[str, _Person] = {}

    # ── frame classification ────────────────────────────────────────────────

    def _speed(self, person: _Person, t: float, cx: float, cy: float, size: float) -> Optional[float]:
        """Person-sizes per second over the last MOTION_WINDOW_S, or None when there is not a window yet."""
        person.track.append((t, cx, cy))
        while len(person.track) > 2 and t - person.track[0][0] > MOTION_WINDOW_S:
            person.track.popleft()                                   # (always keeping the previous sample: at a slow frame rate it is the only one)
        past = [p for p in person.track if t - p[0] >= MOTION_WINDOW_S * 0.5]    # at least half a window old, so a frame-to-frame step is not "speed"
        if not past or size <= 0:
            return None
        # the oldest point inside the window, and the largest movement from any earlier point to now
        moved = max(math.hypot(cx - p[1], cy - p[2]) / max(t - p[0], 1e-6) for p in past)
        return moved / size

    def _classify(self, kp, bbox, posture, frame_w, frame_h, speed, zone_id) -> Tuple[str, Optional[float], str]:
        c = self.config
        if zone_id is not None and zone_id in self.allowed:
            return UP, None, "lying allowed here"
        if speed is None:
            return UP, None, "not enough movement history yet"
        if speed >= c.man_down_still_speed:
            return UP, None, "moving"
        angle = torso_angle(kp)
        w, h = max(1.0, bbox[2] - bbox[0]), max(1.0, bbox[3] - bbox[1])
        aspect = w / h
        if angle is not None:
            if angle < c.man_down_upright_deg and str(posture).upper() == "SITTING":
                return UP, angle, "sitting upright"
            if angle <= c.man_down_angle_deg:
                return UP, angle, "upright"
            # The torso is flat. A person bent over a bench or the floor has a flat torso too, but is still tall (the
            # legs): only a box that is as wide as it is tall says the whole body is down.
            if aspect < ASPECT_UPRIGHT:
                return UP, angle, "bent over, on their feet"
            if aspect < ASPECT_LYING:
                return UNCLEAR, angle, "flat torso, box not wide enough to be sure"
            return DOWN, angle, "lying"
        # The angle is unknown.
        if aspect > ASPECT_DOWN:
            return UNCLEAR, None, "wide box, keypoints too weak to tell"
        return UP, None, "upright box"

    # ── per frame ───────────────────────────────────────────────────────────

    def update(self, people: List[dict], t: float, frame_w: float, frame_h: float,
               worker_zone: Optional[Dict[str, Optional[str]]] = None) -> List[Decision]:
        """
        people: [{"worker_key", "keypoints", "bbox", "posture"}]. `worker_zone` {worker_key: zone id or None} is
        where each person stands (only used by a rule anchored to a zone, and by "lying allowed" zones).
        """
        out: List[Decision] = []
        worker_zone = worker_zone or {}
        seen = set()
        here = {p["worker_key"] for p in people}
        for p in people:
            key = p["worker_key"]
            zone = worker_zone.get(key)
            bbox = [float(v) for v in p["bbox"]]
            if self.rule.zone_id is not None and zone != self.rule.zone_id:
                person = self._people.get(key)
                if person is None or t - person.zone_seen > LOST_S:
                    self._leave(key)                               # outside the rule's zone: nothing to watch
                else:
                    seen.add(key)                                  # a step over the zone's edge (a person's ground point moves when they go down)
                    person.last_seen = t
                continue
            seen.add(key)
            if key not in self._people:
                self._adopt(key, bbox, t, here)
            person = self._people.setdefault(key, _Person())
            person.zone_seen = t
            if person.episode is not None and t - person.last_seen > LOST_S:
                person.episode = None                              # out of view too long: the episode is not continuous
                person.track.clear()
            person.last_seen = t
            person.last_bbox = bbox
            size = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
            speed = self._speed(person, t, (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0, size)
            state, angle, _why = self._classify(p.get("keypoints"), bbox, p.get("posture"), frame_w, frame_h, speed, zone)
            d = self._step(key, person, state, angle, str(p.get("posture") or ""), t, zone, speed)
            if d is not None:
                out.append(d)
        for key in [k for k in self._people if k not in seen]:
            person = self._people[key]
            if t - person.last_seen > LOST_S:
                del self._people[key]                              # gone: its episode (judged or not) ends with it
        return out

    def _leave(self, key: str) -> None:
        self._people.pop(key, None)

    def _adopt(self, key: str, bbox: List[float], t: float, here: set) -> None:
        """
        A new track id that sits on the box of a person lost a moment ago is that person: a fall is exactly when the
        tracker's box changes shape and it may hand out a new id. The episode, and the fact that it was judged, carry over.
        """
        best, best_iou = None, IOU_CARRY
        for old, person in self._people.items():
            if old in here or person.episode is None or person.last_bbox is None or t - person.last_seen > LOST_S:
                continue
            iou = _iou(bbox, person.last_bbox)
            if iou >= best_iou:
                best, best_iou = old, iou
        if best is not None:
            self._people[key] = self._people.pop(best)

    def _step(self, key, person: _Person, state, angle, posture, t, zone, speed) -> Optional[Decision]:
        ep = person.episode
        if state == UP:
            if ep is not None:
                ep.frames += 1
                if ep.up_since is None:
                    ep.up_since = t
                if t - ep.up_since >= RECOVER_S:
                    person.episode = None                          # got up (or moved): over
            return None
        # DOWN or UNCLEAR
        if ep is None:
            ep = person.episode = _Episode(t, zone)
        ep.up_since = None
        ep.frames += 1
        if state == DOWN:
            ep.down += 1
            if angle is not None:
                ep.angles.append(angle)
        else:
            ep.unclear += 1
        if posture:
            ep.postures[posture] = ep.postures.get(posture, 0) + 1
        if ep.judged:
            return None
        age = t - ep.since                                         # the stretch the evidence is read over
        if age < self.min_s:
            return None
        fraction = ep.down / ep.frames if ep.frames else 0.0
        if fraction >= CLEAR_FRACTION:
            ep.judged = True
            return self._violation(key, ep, t, fraction, speed)
        if age >= 2.0 * self.min_s and not ep.warned:
            # Say once that it could not be read, then read the picture afresh from here: if the keypoints
            # clear up while the person is still down, that is still an alert.
            ep.warned = True
            d = self._unclear(key, ep, t, fraction)
            ep.since, ep.frames, ep.down, ep.unclear = t, 0, 0, 0
            return d
        return None

    # ── the decisions ───────────────────────────────────────────────────────

    def _where(self, ep: _Episode) -> str:
        if ep.zone_id is None:
            return ""
        name = self.zone_names.get(ep.zone_id) or self.rule.zone_name or ep.zone_id
        return f" in {name}"

    def _thresholds(self) -> dict:
        c = self.config
        return {
            "min_seconds_down": self.min_s, "man_down_angle_deg": c.man_down_angle_deg,
            "man_down_upright_deg": c.man_down_upright_deg, "man_down_still_speed": c.man_down_still_speed,
            "clear_fraction": CLEAR_FRACTION, "recover_s": RECOVER_S, "keypoint_confidence": KEYPOINT_CONF,
            "min_evidence_confidence": self.rule.min_evidence_confidence,
        }

    def _evidence(self, ep: _Episode, t: float, fraction: float, outcome: str) -> dict:
        return {
            "outcome": outcome, "down_since_video_time_s": round(ep.started_at, 2),
            "seconds_down": round(t - ep.started_at, 1), "min_seconds_down": self.min_s,
            "frames": ep.frames, "frames_lying": ep.down, "frames_unclear": ep.unclear,
            "lying_fraction": round(fraction, 3),
            "torso_angle_deg": round(statistics.median(ep.angles), 1) if ep.angles else None,
            "posture_labels": dict(ep.postures), "zone_id": ep.zone_id,
        }

    def _violation(self, key, ep: _Episode, t: float, fraction: float, speed) -> Decision:
        angle = statistics.median(ep.angles) if ep.angles else None
        how = f", torso about {angle:.0f} degrees from upright" if angle is not None else ""
        reason = (f"A person{self._where(ep)} has been lying still for {t - ep.started_at:.0f} s "
                  f"(lying in {ep.down} of {ep.frames} frames{how}). They may need help: please check on them.")
        return Decision(
            status=POTENTIAL_VIOLATION, reason=reason, confidence=min(1.0, round(fraction, 3)),
            evidence=self._evidence(ep, t, fraction, "LYING_STILL"), rule_type="MAN_DOWN", severity="CRITICAL",   # always: a person down is not a "warning"
            worker_key=key, zone_id=self.rule.zone_id, video_time_s=ep.started_at, rule_id=self.rule.id,
            thresholds=self._thresholds(), snapshot_requested=True)

    def _unclear(self, key, ep: _Episode, t: float, fraction: float) -> Decision:
        reason = (f"A person{self._where(ep)} has been still for {t - ep.started_at:.0f} s and may be lying down, but the "
                  f"picture does not show the body clearly enough to say (clearly lying in {ep.down} of {ep.frames} frames). "
                  f"Worth a look.")
        return Decision(
            status=UNVERIFIABLE, reason=reason, confidence=0.0,
            evidence=self._evidence(ep, t, fraction, "UNCLEAR"), rule_type="MAN_DOWN", severity="WARNING",
            worker_key=key, zone_id=self.rule.zone_id, video_time_s=ep.started_at, rule_id=self.rule.id,
            thresholds=self._thresholds(), snapshot_requested=False)

    # ── engine contract ─────────────────────────────────────────────────────

    def reset(self) -> None:
        """The timeline jumped: nobody can say how long anyone was down."""
        self._people.clear()


class ManDownMonitor:
    """All of a session's MAN_DOWN rules."""

    def __init__(self, rules: List[Rule], config: Optional[SafetyConfig] = None, zone_names: Optional[Dict[str, str]] = None):
        self.detectors = [ManDownDetector(r, config, zone_names) for r in rules if r.type == "MAN_DOWN" and r.enabled]

    def __bool__(self) -> bool:
        return bool(self.detectors)

    def update(self, people, t, frame_w, frame_h, worker_zone=None) -> List[Decision]:
        out: List[Decision] = []
        for d in self.detectors:
            out.extend(d.update(people, t, frame_w, frame_h, worker_zone))
        return out

    def reset(self, reason=None, last_seen_s=None, resume_at_s=None) -> list:
        """Engine component contract: the timeline jumped. Nothing is decided about an episode that was cut."""
        for d in self.detectors:
            d.reset()
        return []
