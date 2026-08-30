"""
The ground-truth format, and the one place that defines it.

Step 17 of IDENTITY_TRACKING_PLAN.md: "Hand-label 30-60 min of footage: who is
where, when."

WHAT A LABEL IS

One row per person per sampled instant:

    (t_seconds, employee_id, camera_id, zone_id, posture)

`employee_id = None` means "a person was there and it is genuinely nobody we
have enrolled" — a visitor, a cleaner. That is NOT the same as the system
outputting UNKNOWN, and keeping the two apart is the whole point of the
abstention metrics: abstaining on a stranger is CORRECT, abstaining on a known
employee is a miss.

WHY THE SAME FORMAT SERVES BOTH SOURCES

The evaluation must not care whether its truth came from a person watching
video or from a simulator that knows the answer by construction. Both produce
this format, `metrics.py` consumes only this format, and the numbers are
computed by identical code either way.

That is what makes the synthetic run meaningful: it is not a different, easier
test — it is the same test with a different source of truth. When real footage
arrives, `label.py` writes this same file and every number is recomputed with
no change to the measurement code.

HONESTY ABOUT PROVENANCE

Every ground-truth file records where it came from (`source`) and every report
prints it. A number measured on simulated data must never be presentable as a
number measured on real footage — so the provenance travels with the data
rather than living in someone's memory of how they ran it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional


# Ground truth is sampled at this interval, matching the pipeline's own
# telemetry rate (activity_writer's 5 s). Labelling every frame would be
# 10x the work for no gain: nobody's posture is meaningfully different 100 ms
# apart, and the system only ever writes one row per 5 s anyway.
SAMPLE_SECONDS = 5.0

# Provenance values. Kept as constants so a typo cannot silently produce a
# report that claims real footage.
SOURCE_SIMULATED = "simulated"
SOURCE_LABELLED = "hand-labelled"


@dataclass
class Observation:
    """One person, at one instant, as the truth says they were."""
    t: float                        # seconds from the start of the recording
    employee_id: Optional[str]      # None = a real person who is not an employee
    camera_id: str
    zone_id: Optional[str]          # None = in view but not inside any zone
    posture: str = "SITTING"        # SITTING | STANDING | WALKING
    # The ByteTrack fragment this observation corresponds to, when known.
    # Only the simulator can know this; a human labeller cannot see track ids,
    # so it is optional and the stitch metric degrades gracefully without it.
    track_id: Optional[int] = None
    # True identity thread across fragments — what the stitcher SHOULD produce.
    truth_identity: Optional[str] = None


@dataclass
class GroundTruth:
    """A labelled recording, and everything needed to evaluate against it."""
    source: str                     # SOURCE_SIMULATED | SOURCE_LABELLED
    description: str
    duration_seconds: float
    observations: list = field(default_factory=list)
    # employee_id -> zone_id. The seat map in force during the recording; the
    # seat prior cannot be evaluated without knowing what it was.
    seat_map: dict = field(default_factory=dict)
    # camera_id -> "DOOR" | "AREA"
    camera_roles: dict = field(default_factory=dict)
    # (from, to) -> [min_seconds, max_seconds], as camera_links declares.
    # Stored as a list of dicts because JSON has no tuple keys.
    camera_links: list = field(default_factory=list)
    # Free-form: video path, labeller name, anything a reader needs to judge
    # whether the numbers mean what they appear to mean.
    notes: dict = field(default_factory=dict)

    @property
    def is_real(self) -> bool:
        return self.source == SOURCE_LABELLED

    @property
    def employees(self) -> set:
        return {o.employee_id for o in self.observations if o.employee_id}

    @property
    def cameras(self) -> set:
        return {o.camera_id for o in self.observations}

    def by_time(self) -> list:
        return sorted(self.observations, key=lambda o: (o.t, o.camera_id,
                                                        o.track_id or -1))

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = asdict(self)
        payload["observations"] = [asdict(o) if not isinstance(o, dict) else o
                                   for o in self.observations]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> "GroundTruth":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        obs = [Observation(**o) for o in raw.pop("observations", [])]
        gt = cls(**raw)
        gt.observations = obs
        return gt

    def summary(self) -> str:
        n_people = len(self.employees)
        strangers = sum(1 for o in self.observations if o.employee_id is None)
        return (f"{self.source}: {len(self.observations)} observations, "
                f"{n_people} employees, {len(self.cameras)} camera(s), "
                f"{self.duration_seconds / 60:.1f} min"
                + (f", {strangers} unenrolled-person observations" if strangers else ""))


@dataclass
class Prediction:
    """What the system said, in the same shape as the truth."""
    t: float
    employee_id: Optional[str]      # None = the system output UNKNOWN
    camera_id: str
    zone_id: Optional[str]
    confidence: float
    method: str                     # face | fusion | seat | handoff | unknown
    track_id: Optional[int] = None
    identity_id: Optional[str] = None


def pair_by_time(truth: list, predictions: list, tolerance: float = 2.5) -> list:
    """
    Line up predictions with the truth they are answering.

    Returns [(observation, prediction_or_None)] for every observation, plus
    (None, prediction) for any prediction with no matching truth — a
    hallucination, which must count against the system rather than be silently
    dropped.

    MATCHING RULE

    Same camera, nearest time within `tolerance`. Track id is used when both
    sides have one, because two people on the same camera at the same instant
    are otherwise indistinguishable to the pairing and would be matched
    arbitrarily — which would make the identification accuracy a coin flip
    rather than a measurement.

    The default tolerance is half the sample interval: a prediction more than
    2.5 s from any labelled instant is answering a different moment.
    """
    remaining = list(predictions)
    pairs = []

    for o in truth:
        best = None
        best_gap = tolerance
        for p in remaining:
            if p.camera_id != o.camera_id:
                continue
            # When both sides know the fragment, it must be the same fragment.
            if o.track_id is not None and p.track_id is not None:
                if o.track_id != p.track_id:
                    continue
            gap = abs(p.t - o.t)
            if gap <= best_gap:
                best, best_gap = p, gap
        if best is not None:
            remaining.remove(best)
        pairs.append((o, best))

    # Anything left over is a prediction about a person who was not there.
    for p in remaining:
        pairs.append((None, p))

    return pairs
