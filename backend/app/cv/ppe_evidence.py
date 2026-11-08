# backend/app/cv/ppe_evidence.py
"""
PPE-to-person association and temporal evidence.

WHY THIS IS NOT "IF HELMET DETECTED"

The PPE detector has no "no-helmet" class (the combined model that had one
scored mAP50 0.53 against 0.89, so absence is inferred, not detected). That
makes every verdict here an inference from the ABSENCE of a box, and the
model's measured behaviour on its own test split (conf 0.35) is:

    helmet       precision 0.83  recall 0.955
    safety_vest  precision 0.86  recall 0.88

A single frame therefore cannot accuse anyone: 12% of real vests are simply
not detected in any one frame. Two defences are built in, mirroring the
identity code's "abstain rather than guess" rule:

  1. A frame only counts as evidence when the person is CLEAR — big enough,
     not cut off at the top of the frame, not tangled with another person.
  2. A violation needs ABSENCE_FRACTION (90%) of the clear frames in the window
     to contain no sign of the item at all, while presence needs only 60%.
     Anything in between — including a half-visible helmet flickering at 0.3 —
     is UNVERIFIABLE, never a violation.

The result per person is COMPLIANT / NON_COMPLIANT / UNVERIFIABLE, with the
numbers that led to it, so a supervisor can see why.

Pure Python: no torch, no OpenCV, so it is testable without a GPU.
"""

from collections import deque

HELMET = "helmet"
VEST = "safety_vest"

COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
UNVERIFIABLE = "UNVERIFIABLE"

PRESENT = "PRESENT"
ABSENT = "ABSENT"

# ── association geometry (fractions of the person box height) ───────────────
HEAD_TOP_SLACK = 0.10      # a helmet may poke this far above the person box
HEAD_BOTTOM = 0.35         # ...and its centre must lie above this line
VEST_TOP = 0.12            # vest centre must be between these two lines
VEST_BOTTOM = 0.72
VEST_MIN_CONTAINMENT = 0.50  # share of the vest box that must be inside the person

# ── "clear" frame gate ──────────────────────────────────────────────────────
MIN_PERSON_HEIGHT_PX = 90.0   # smaller than this the gear is a few pixels
MIN_PERSON_WIDTH_PX = 28.0
EDGE_MARGIN_PX = 3.0          # box touching the top edge => head cut off
CROWD_IOU = 0.30              # overlap with another person => occluded

# ── temporal decision ───────────────────────────────────────────────────────
PRESENT_CONF = 0.45           # a detection at/above this counts as "seen"
WEAK_CONF = 0.20              # between WEAK and PRESENT is "ambiguous"
WINDOW_FRAMES = 30
MIN_CLEAR_FRAMES = 8
PRESENCE_FRACTION = 0.60
ABSENCE_FRACTION = 0.90
STALE_SECONDS = 10.0


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def _containment(inner, outer):
    """Share of `inner`'s area that lies inside `outer`."""
    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


def _centre(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def associate_ppe(persons, ppe_dets):
    """
    Decide which helmet / vest belongs to which person, for ONE frame.

    persons:  [{"track_id": int, "bbox": [x1,y1,x2,y2]}, ...]
    ppe_dets: [{"cls": "helmet"|"safety_vest", "conf": float, "bbox": [...]}, ...]

    Returns {track_id: {"helmet": conf, "safety_vest": conf, "clear": bool}}
    where conf is the best associated detection (0.0 when none).

    One PPE box is given to AT MOST one person, so a helmet seen over two
    overlapping workers can never make both look compliant — the case the
    gap document warns about ("all workers compliant because one helmet exists
    somewhere in the frame").
    """
    out = {p["track_id"]: {HELMET: 0.0, VEST: 0.0, "clear": True} for p in persons}

    for det in ppe_dets:
        cls, box, conf = det["cls"], det["bbox"], float(det["conf"])
        if cls not in (HELMET, VEST):
            continue
        cx, cy = _centre(box)
        best_id, best_dist = None, None
        for p in persons:
            px1, py1, px2, py2 = p["bbox"]
            h = max(1.0, py2 - py1)
            if cls == HELMET:
                ok = (px1 <= cx <= px2
                      and py1 - HEAD_TOP_SLACK * h <= cy <= py1 + HEAD_BOTTOM * h)
                target = ((px1 + px2) / 2.0, py1 + 0.12 * h)
            else:
                ok = (_containment(box, p["bbox"]) >= VEST_MIN_CONTAINMENT
                      and py1 + VEST_TOP * h <= cy <= py1 + VEST_BOTTOM * h)
                target = ((px1 + px2) / 2.0, py1 + 0.40 * h)
            if not ok:
                continue
            dist = (cx - target[0]) ** 2 + (cy - target[1]) ** 2
            if best_dist is None or dist < best_dist:
                best_id, best_dist = p["track_id"], dist
        if best_id is not None and conf > out[best_id][cls]:
            out[best_id][cls] = conf

    return out


def frame_clarity(person_bbox, others, frame_shape=None):
    """
    Is this frame good enough to count as EVIDENCE about this person?

    False means "do not learn anything from this frame" — it is excluded from
    both the presence and the absence tally, which is what stops a distant,
    cropped or half-hidden worker from being recorded as a missing-PPE case.
    """
    x1, y1, x2, y2 = person_bbox
    if (y2 - y1) < MIN_PERSON_HEIGHT_PX or (x2 - x1) < MIN_PERSON_WIDTH_PX:
        return False
    if y1 <= EDGE_MARGIN_PX:
        return False  # head is cut off by the top of the frame
    for o in others:
        if _iou(person_bbox, o) >= CROWD_IOU:
            return False
    return True


def associate_frame(persons, ppe_dets, frame_shape=None):
    """associate_ppe() plus the clarity flag, using every other person as the crowd."""
    result = associate_ppe(persons, ppe_dets)
    for p in persons:
        others = [q["bbox"] for q in persons if q["track_id"] != p["track_id"]]
        result[p["track_id"]]["clear"] = frame_clarity(p["bbox"], others, frame_shape)
    return result


class PpeEvidenceTracker:
    """
    Rolling PPE evidence per track.

    Keyed by whatever id the caller passes — a local ByteTrack id, or an
    identity_id once the identity tracker has stitched fragments. Use the
    identity_id when you have it: evidence then survives an occlusion.
    """

    def __init__(self, required=(HELMET, VEST), window=WINDOW_FRAMES,
                 min_clear=MIN_CLEAR_FRAMES, presence=PRESENCE_FRACTION,
                 absence=ABSENCE_FRACTION, stale_seconds=STALE_SECONDS):
        self.required = tuple(required)
        self.window = window
        self.min_clear = min_clear
        self.presence = presence
        self.absence = absence
        self.stale_seconds = stale_seconds
        self._obs = {}  # key -> deque[(ts, {item: conf}, clear)]

    def update(self, key, observation, ts):
        """observation: one entry of associate_frame()'s result."""
        dq = self._obs.setdefault(key, deque(maxlen=self.window))
        dq.append((ts, {HELMET: observation.get(HELMET, 0.0),
                        VEST: observation.get(VEST, 0.0)},
                   bool(observation.get("clear", True))))

    def _item_status(self, item, clear_obs):
        n = len(clear_obs)
        seen = sum(1 for _, c, _ in clear_obs if c[item] >= PRESENT_CONF)
        nothing = sum(1 for _, c, _ in clear_obs if c[item] < WEAK_CONF)
        confs = [c[item] for _, c, _ in clear_obs if c[item] >= PRESENT_CONF]
        info = {"clear_frames": n,
                "seen_fraction": round(seen / n, 3) if n else 0.0,
                "empty_fraction": round(nothing / n, 3) if n else 0.0,
                "mean_conf": round(sum(confs) / len(confs), 3) if confs else 0.0}
        if n < self.min_clear:
            return UNVERIFIABLE, f"only {n} clear frames (need {self.min_clear})", info
        if seen / n >= self.presence:
            return PRESENT, "seen in enough clear frames", info
        if nothing / n >= self.absence:
            return ABSENT, "no sign of it in nearly every clear frame", info
        return UNVERIFIABLE, "evidence is mixed or ambiguous", info

    def status(self, key, now):
        """
        {"status", "items": {item: {...}}, "reason"} for one track.

        NON_COMPLIANT if any required item is confidently ABSENT; COMPLIANT
        only if every required item is PRESENT; otherwise UNVERIFIABLE.
        """
        dq = self._obs.get(key)
        fresh = [o for o in (dq or []) if now - o[0] <= self.stale_seconds]
        clear_obs = [o for o in fresh if o[2]]

        items, reasons = {}, []
        for item in self.required:
            st, why, info = self._item_status(item, clear_obs)
            items[item] = {"state": st, "why": why, **info}
            reasons.append(f"{item}: {st} ({why})")

        states = [items[i]["state"] for i in self.required]
        if ABSENT in states:
            overall = NON_COMPLIANT
        elif all(s == PRESENT for s in states):
            overall = COMPLIANT
        else:
            overall = UNVERIFIABLE
        return {"status": overall, "items": items,
                "observed_frames": len(fresh), "reason": "; ".join(reasons)}

    def drop(self, key):
        self._obs.pop(key, None)
