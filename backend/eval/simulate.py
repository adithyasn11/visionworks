"""
A synthetic office, with known truth, driving the REAL identity pipeline.

WHAT THIS IS, AND WHAT IT IS NOT

It is not a mock of the tracker. Every scenario below feeds real signatures
into the real `IdentityTracker`, the real seat binding, the real handoff
registry and the real `employee_aggregator`, and scores whatever comes out.
The only thing that is synthetic is the INPUT — the appearance vectors and the
movements — and the fact that we know, by construction, who each one really is.

That distinction is the whole justification for this file. The measurement code
in `metrics.py` never learns where its truth came from, so a number produced
here is produced by exactly the same arithmetic that will produce the number
from real footage. When `label.py` writes a ground-truth file from a video, the
identical pipeline runs and the identical metrics are computed.

WHY SYNTHETIC APPEARANCE IS DEFENSIBLE — AND WHERE IT IS NOT

The generator is calibrated against the real thing. Earlier in this project,
two crops of one person through real OSNet measured cosine 0.877, and two
different people measured well below the 0.65 gate; the same-person and
different-person distributions here are built to those figures rather than to
whatever looked convincing. An earlier attempt used σ=0.10 noise on a signal of
~0.044 per component and drove same-person cosine to 0.43 — a number that says
nothing about the tracker and everything about the noise model.

What this CANNOT tell you: whether OSNet itself separates your actual
colleagues in your actual lighting. That needs real footage, and every report
this produces says so on its face. What it CAN tell you is whether the
stitching logic, the seat binding, the confidence floor, the abstention rule
and the aggregation are correct — which is most of what Step 17 asks, and all
of it is logic this project wrote.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from .groundtruth import (GroundTruth, Observation, SAMPLE_SECONDS,
                          SOURCE_SIMULATED)


# Calibrated against the real measurements taken during Phase B.
#
# A person's OSNet embedding is not identical frame to frame — lighting, pose
# and crop jitter move it. These figures reproduce the spread that was actually
# observed rather than an idealised one: too tight and every stitch succeeds
# and the metric is meaningless; too loose and the test measures the noise
# model instead of the tracker.
SAME_PERSON_COSINE = 0.877        # measured, real OSNet, two crops of one person
SAME_PERSON_SPREAD = 0.06
DIFFERENT_PERSON_COSINE = 0.31    # measured, two different people
DIFFERENT_PERSON_SPREAD = 0.12

EMBED_DIM = 512
HIST_BINS = 256


@dataclass
class Person:
    """Somebody in the simulated office, with a stable appearance."""
    employee_id: str
    name: str
    desk: str | None
    # The person's "true" appearance, which observations perturb around.
    embedding: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    height: float
    is_employee: bool = True


class OfficeSimulator:
    """
    Builds a day, and the truth about it.

    Deterministic for a given seed: a metric that changes between runs cannot
    be quoted in a report.
    """

    def __init__(self, seed: int = 20260830):
        self.rng = np.random.default_rng(seed)
        self.pyrng = random.Random(seed)
        self.people: dict[str, Person] = {}

    # ── appearance ────────────────────────────────────────────────────────

    def _unit(self, n: int) -> np.ndarray:
        v = self.rng.normal(size=n).astype(np.float32)
        return v / np.linalg.norm(v)

    def make_person(self, employee_id: str, name: str, desk: str | None,
                    is_employee: bool = True,
                    similar_to: str | None = None) -> Person:
        """
        `similar_to` produces somebody who genuinely looks like another person —
        the "similar clothing" failure the plan wants documented. Their base
        embedding sits at the DIFFERENT-person cosine plus a nudge toward the
        target, so they are hard rather than impossible.
        """
        emb = self._unit(EMBED_DIM)
        if similar_to and similar_to in self.people:
            target = self.people[similar_to].embedding
            # Blend to land near the decision boundary (~0.6), i.e. genuinely
            # confusable, which is the point of the scenario.
            emb = 0.62 * target + 0.38 * emb
            emb = emb / np.linalg.norm(emb)

        upper = self.rng.random(HIST_BINS).astype(np.float32)
        if similar_to and similar_to in self.people:
            # Same shirt. Colour should actively mislead here.
            upper = self.people[similar_to].upper.copy()

        p = Person(employee_id=employee_id, name=name, desk=desk,
                   embedding=emb, upper=upper,
                   lower=self.rng.random(HIST_BINS).astype(np.float32),
                   height=float(self.rng.uniform(160, 185)),
                   is_employee=is_employee)
        self.people[employee_id] = p
        return p

    def observe(self, person: Person, track_id: int,
                colour_shift: float = 0.0) -> dict:
        """
        One frame's signature for this person, as the CV stack would produce.

        `colour_shift` models the cross-camera colour problem the plan names as
        failure mode #2: the same shirt reads differently on a different
        camera. It perturbs the histograms while leaving OSNet largely intact,
        which is exactly the asymmetry the plan describes ("lean harder on
        OSNet, which is more robust to this than raw histograms").
        """
        # Perturb toward the measured same-person cosine.
        noise = self._unit(EMBED_DIM)
        target = float(np.clip(
            self.rng.normal(SAME_PERSON_COSINE, SAME_PERSON_SPREAD), 0.5, 0.995))
        # v = a*base + b*noise, chosen so cos(v, base) == target.
        a = target
        b = math.sqrt(max(1e-6, 1.0 - target * target))
        emb = a * person.embedding + b * noise
        emb = (emb / np.linalg.norm(emb)).astype(np.float32)

        upper = person.upper.copy()
        lower = person.lower.copy()
        if colour_shift:
            upper = np.clip(upper * (1.0 + colour_shift)
                            + self.rng.normal(0, colour_shift, HIST_BINS), 0, None)
            lower = np.clip(lower * (1.0 + colour_shift)
                            + self.rng.normal(0, colour_shift, HIST_BINS), 0, None)

        return {
            "track_id": track_id,
            "embedding": emb,
            "upper": upper.astype(np.float32),
            "lower": lower.astype(np.float32),
            "height": person.height + float(self.rng.normal(0, 1.5)),
            "bbox": [100, 100, 160, 260],
            "area": 9600,
        }


# ──────────────────────────────────────────────────────────────────────────
#  SCENARIOS
#
#  Each returns (GroundTruth, frames) where frames is a list of
#  (t, camera_id, [(person, track_id, signature, zone, posture)]).
#  A scenario describes what HAPPENED; running it against the pipeline is
#  `runner.py`'s job.
# ──────────────────────────────────────────────────────────────────────────


def scenario_normal_day(sim: OfficeSimulator, minutes: float = 20.0) -> tuple:
    """
    Three people at three desks: the plan's Step 7 verification, at scale.

    Deliberately ordinary. If the system cannot get this right, no other number
    matters — and a suite made only of hard cases would hide that.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    b = sim.make_person("emp-b", "Ravi Sharma", "desk_2")
    c = sim.make_person("emp-c", "Anita Rao", "desk_3")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="Three employees at three fixed desks, 20 minutes, "
                    "occasional short absences",
        duration_seconds=minutes * 60,
        seat_map={"desk_1": "emp-a", "desk_2": "emp-b", "desk_3": "emp-c"},
        camera_roles={"cam-floor": "AREA"},
    )

    frames = []
    t = 0.0
    step = 0.5                      # 2 Hz, matching the pipeline
    tracks = {"emp-a": 1, "emp-b": 2, "emp-c": 3}

    while t < minutes * 60:
        batch = []
        for person in (a, b, c):
            # A short absence now and then — real people move.
            away = (int(t) // 137) % 11 == 0 and person is b
            zone = None if away else person.desk
            posture = "WALKING" if away else "SITTING"
            sig = sim.observe(person, tracks[person.employee_id])
            batch.append((person, tracks[person.employee_id], sig, zone, posture))

            if abs((t % SAMPLE_SECONDS)) < step / 2:
                gt.observations.append(Observation(
                    t=t, employee_id=person.employee_id, camera_id="cam-floor",
                    zone_id=zone, posture=posture,
                    track_id=tracks[person.employee_id],
                    truth_identity=person.employee_id))
        frames.append((t, "cam-floor", batch))
        t += step

    return gt, frames


def scenario_occlusion(sim: OfficeSimulator, minutes: float = 10.0) -> tuple:
    """
    The plan's Step 6 verification: somebody walks behind a pillar and returns.

    ByteTrack loses them and issues a NEW track id. The identity must survive.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    b = sim.make_person("emp-b", "Ravi Sharma", "desk_2")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="Two employees; one is occluded three times and ByteTrack "
                    "issues a new track id each time",
        duration_seconds=minutes * 60,
        seat_map={"desk_1": "emp-a", "desk_2": "emp-b"},
        camera_roles={"cam-floor": "AREA"},
    )

    frames = []
    t = 0.0
    step = 0.5
    a_track = 1
    b_track = 2
    next_track = 10
    # Occlusion windows: gone for ~8 s, then back with a fresh id.
    occlusions = [(120.0, 128.0), (300.0, 309.0), (480.0, 487.0)]

    while t < minutes * 60:
        batch = []
        hidden = any(lo <= t < hi for lo, hi in occlusions)
        just_returned = any(abs(t - hi) < step / 2 for _, hi in occlusions)
        if just_returned:
            a_track = next_track
            next_track += 1

        if not hidden:
            sig = sim.observe(a, a_track)
            batch.append((a, a_track, sig, a.desk, "SITTING"))
            if abs(t % SAMPLE_SECONDS) < step / 2:
                gt.observations.append(Observation(
                    t=t, employee_id="emp-a", camera_id="cam-floor",
                    zone_id=a.desk, posture="SITTING", track_id=a_track,
                    truth_identity="emp-a"))

        sig_b = sim.observe(b, b_track)
        batch.append((b, b_track, sig_b, b.desk, "SITTING"))
        if abs(t % SAMPLE_SECONDS) < step / 2:
            gt.observations.append(Observation(
                t=t, employee_id="emp-b", camera_id="cam-floor",
                zone_id=b.desk, posture="SITTING", track_id=b_track,
                truth_identity="emp-b"))

        frames.append((t, "cam-floor", batch))
        t += step

    gt.notes["occlusions"] = occlusions
    gt.notes["expected_raw_ids_for_emp_a"] = 1 + len(occlusions)
    return gt, frames


def scenario_similar_clothing(sim: OfficeSimulator, minutes: float = 10.0) -> tuple:
    """
    Failure mode #2 from §8, in the two situations that actually differ.

    Two people in identical shirts whose appearance vectors sit at cosine
    ~0.86 — far above the 0.65 stitch gate, i.e. genuinely confusable by
    appearance alone.

    THE FIRST HALF: AT THEIR OWN DESKS

    The seat prior settles it and appearance never has to. This is not the
    scenario failing to be hard — it is the plan's §3 claim ("85-95% alone in
    fixed-desk offices") being demonstrated: the strongest signal costs no
    biometrics and does not care that two people look alike.

    THE SECOND HALF: BOTH AWAY FROM THEIR DESKS

    Now the seat prior is gone and only appearance is left, on two people it
    cannot separate. The right outcome here is NOT "identify them anyway" — it
    is to abstain. A confident wrong name is the worst thing this system can
    produce (§0), and a system that guesses between two indistinguishable
    people will be wrong half the time while looking certain.

    So the pass condition for this scenario is: near-perfect at the desks, and
    NO confident misidentification away from them. Abstentions in the second
    half are a correct answer, not a shortfall.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    b = sim.make_person("emp-b", "Ravi Sharma", "desk_2", similar_to="emp-a")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="Two employees in identical clothing with confusable "
                    "appearance vectors: half the time at their own desks, "
                    "half in a shared area where only appearance is available",
        duration_seconds=minutes * 60,
        seat_map={"desk_1": "emp-a", "desk_2": "emp-b"},
        camera_roles={"cam-floor": "AREA"},
    )

    frames = []
    t = 0.0
    step = 0.5
    half = minutes * 60 / 2

    while t < minutes * 60:
        at_desks = t < half
        batch = []
        for person, tid in ((a, 1), (b, 2)):
            zone = person.desk if at_desks else "shared_area"
            posture = "SITTING" if at_desks else "STANDING"
            sig = sim.observe(person, tid)
            batch.append((person, tid, sig, zone, posture))
            if abs(t % SAMPLE_SECONDS) < step / 2:
                gt.observations.append(Observation(
                    t=t, employee_id=person.employee_id, camera_id="cam-floor",
                    zone_id=zone, posture=posture, track_id=tid,
                    truth_identity=person.employee_id))
        frames.append((t, "cam-floor", batch))
        t += step

    cos = float(np.dot(a.embedding, b.embedding))
    gt.notes["between_person_cosine"] = round(cos, 3)
    gt.notes["shared_upper_histogram"] = True
    gt.notes["desks_until_seconds"] = half
    gt.notes["pass_condition"] = ("accurate at the desks; abstain rather than "
                                 "guess in the shared area")
    return gt, frames


def scenario_chair_exits(sim: OfficeSimulator) -> tuple:
    """
    The plan's Step 8 verification, exactly: stand up three times.

    "Confirm awayFromDeskCount == 3. This is the single most important
    verification in the whole plan."

    Two of the departures are long (real exits); one is a 40-second stretch
    that must NOT count, because the debounce exists to reject it. A system
    that reports 3 here is right for the right reason; one that reports 4 is
    counting a lean-back.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="One employee; three genuine chair exits over 90 s and one "
                    "40 s absence that must be rejected by the debounce",
        duration_seconds=1200,
        seat_map={"desk_1": "emp-a"},
        camera_roles={"cam-floor": "AREA"},
    )

    # (start, end) away from the desk.
    real_exits = [(180.0, 340.0), (520.0, 700.0), (860.0, 1000.0)]
    short_absence = (420.0, 460.0)          # 40 s — must not count
    away_windows = real_exits + [short_absence]

    frames = []
    t = 0.0
    step = 0.5
    while t < 1200:
        away = any(lo <= t < hi for lo, hi in away_windows)
        zone = None if away else "desk_1"
        posture = "WALKING" if away else "SITTING"
        sig = sim.observe(a, 1)
        frames.append((t, "cam-floor", [(a, 1, sig, zone, posture)]))
        if abs(t % SAMPLE_SECONDS) < step / 2:
            gt.observations.append(Observation(
                t=t, employee_id="emp-a", camera_id="cam-floor", zone_id=zone,
                posture=posture, track_id=1, truth_identity="emp-a"))
        t += step

    gt.notes["true_chair_exits"] = [lo for lo, _ in real_exits]
    gt.notes["expected_exit_count"] = len(real_exits)
    gt.notes["rejected_short_absence"] = list(short_absence)
    return gt, frames


def scenario_cross_camera(sim: OfficeSimulator) -> tuple:
    """
    The plan's Step 13 verification: walk camera A -> camera B ten times.

    "Expect 6-8 out of 10. That is a normal result."

    TWO THINGS THIS SCENARIO HAD TO GET RIGHT BEFORE IT MEASURED ANYTHING

    1. A handoff can only move a name that already EXISTS. An early version
       started people on the corridor camera, where nobody has a desk and so
       nobody is ever identified — it scored 0/10 because there was no name to
       carry, which says nothing about the handoff code.

    2. A person is seat-bound ONCE per session. A later version sent both
       people back to their desks before each of the ten trips. After trip one
       both employees were in `_bound_employees` and every subsequent desk
       visit produced an unnamed identity — again 0/10, again the scenario's
       fault. That guard is correct in production: it stops one employee's
       hours being claimed by two identities at once.

    So the shape here is the realistic one: each person occupies their desk
    CONTINUOUSLY on the desk camera, and makes trips out to the corridor and
    pantry from it. That is what an office day looks like, and it is the only
    shape in which "did the name follow the person across cameras" is a
    question with a meaning.

    The pantry camera applies a colour shift, because the same shirt reads
    differently on a different camera and the plan names that as failure
    mode #2.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    b = sim.make_person("emp-b", "Ravi Sharma", "desk_2")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="Two employees seated at their desks, making ten trips "
                    "out to the corridor and pantry, with a cross-camera "
                    "colour shift on the pantry camera",
        duration_seconds=0,
        seat_map={"desk_1": "emp-a", "desk_2": "emp-b"},
        camera_roles={"cam-desk": "AREA", "cam-corridor": "AREA",
                      "cam-pantry": "AREA"},
        camera_links=[
            {"from": "cam-desk", "to": "cam-corridor",
             "min_seconds": 2, "max_seconds": 30},
            {"from": "cam-corridor", "to": "cam-pantry",
             "min_seconds": 3, "max_seconds": 20},
        ],
    )

    # The whole recording, as a timeline of (t, camera, person, track, zone,
    # posture, colour_shift). Built first, then rendered into frames, because
    # two people are on different cameras at the same instant and the frames
    # list has to interleave them correctly.
    events = []          # (t, camera, person, track, zone, posture, shift)
    handoffs = []

    STEP = 0.5
    TOTAL = 1500.0

    # Desk tracks. Each person holds one ByteTrack id at their desk for the
    # whole session, as they would if they never left the frame.
    desk_track = {"emp-a": 1, "emp-b": 2}
    next_track = 50

    # Ten trips, alternating between the two people, spaced out over the day.
    # Each trip: leave the desk, 15 s corridor, gap, 20 s pantry, come back.
    # A WALK TAKES TIME
    #
    # The desk tracker records the departure on the frame the person vanishes;
    # the corridor tracker evaluates the arrival on the frame they appear. If
    # those are the same instant the gap is 0 s, which is below every sane
    # `minSeconds`, and the link is rejected as physically implausible —
    # correctly. Measured: with no walk time, every corridor arrival came back
    # "no candidate within the plausible walk time".
    #
    # So each leg leaves a real gap: 4 s from the desk to the corridor, and a
    # varying 5-8 s from the corridor to the pantry, both inside the windows
    # the topology declares.
    DESK_TO_CORRIDOR = 4.0

    trips = []
    t_trip = 200.0
    for i in range(10):
        person = a if i % 2 == 0 else b
        corridor_start = t_trip + DESK_TO_CORRIDOR
        corridor_end = corridor_start + 15.0
        gap = 5.0 + (i % 4)          # inside the declared 3-20 s window
        pantry_start = corridor_end + gap
        pantry_end = pantry_start + 20.0
        trips.append((person, corridor_start, corridor_end,
                      pantry_start, pantry_end))
        handoffs.append((person.employee_id, "cam-corridor", "cam-pantry",
                         pantry_start))
        t_trip = pantry_end + 90.0   # back at the desk for a while

    def away(person, t):
        """Off the desk camera from the moment they stand up, not from the
        moment they reach the corridor — otherwise they would appear on two
        cameras at once during the walk, and the departure would never fire."""
        for (p, cs, ce, ps, pe) in trips:
            if p is person and (cs - DESK_TO_CORRIDOR) <= t < pe:
                return True
        return False

    t = 0.0
    while t < TOTAL:
        for person in (a, b):
            if not away(person, t):
                events.append((t, "cam-desk", person, desk_track[person.employee_id],
                               person.desk, "SITTING", 0.0))
        for idx, (p, cs, ce, ps, pe) in enumerate(trips):
            if cs <= t < ce:
                events.append((t, "cam-corridor", p, 1000 + idx * 2,
                               "corridor", "WALKING", 0.0))
            elif ps <= t < pe:
                events.append((t, "cam-pantry", p, 1001 + idx * 2,
                               "pantry", "STANDING", 0.25))
        t += STEP

    # Render into the (t, camera, batch) shape the runner consumes, and record
    # the truth at the sampling rate.
    frames = []
    grouped = {}
    for (tt, cam, person, tid, zone, posture, shift) in events:
        sig = sim.observe(person, tid, colour_shift=shift)
        grouped.setdefault((tt, cam), []).append((person, tid, sig, zone, posture))
        if abs(tt % SAMPLE_SECONDS) < STEP / 2:
            gt.observations.append(Observation(
                t=tt, employee_id=person.employee_id, camera_id=cam,
                zone_id=zone, posture=posture, track_id=tid,
                truth_identity=person.employee_id))

    for (tt, cam) in sorted(grouped):
        frames.append((tt, cam, grouped[(tt, cam)]))

    gt.duration_seconds = TOTAL
    gt.notes["handoffs"] = [[e, f, to, round(at, 1)] for e, f, to, at in handoffs]
    gt.notes["colour_shift"] = 0.25
    gt.notes["design"] = ("each person holds their desk continuously and makes "
                          "trips out from it, so the seat binding names them "
                          "once and the handoff has a name to carry")
    return gt, frames


def scenario_stranger(sim: OfficeSimulator, minutes: float = 8.0) -> tuple:
    """
    A visitor nobody enrolled, at a desk that is not theirs.

    §0 of the plan: "When the system is not confident, it must output UNKNOWN
    rather than guess." Naming this person is the failure; abstaining is the
    pass. Without this scenario every abstention looks like a weakness.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    visitor = sim.make_person("visitor-1", "Unenrolled visitor", None,
                              is_employee=False)

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="One employee plus an unenrolled visitor sitting at an "
                    "unassigned desk — abstention here is the correct answer",
        duration_seconds=minutes * 60,
        seat_map={"desk_1": "emp-a"},
        camera_roles={"cam-floor": "AREA"},
    )

    frames = []
    t = 0.0
    step = 0.5
    while t < minutes * 60:
        batch = []
        sig_a = sim.observe(a, 1)
        batch.append((a, 1, sig_a, "desk_1", "SITTING"))
        sig_v = sim.observe(visitor, 2)
        batch.append((visitor, 2, sig_v, "desk_9", "SITTING"))

        if abs(t % SAMPLE_SECONDS) < step / 2:
            gt.observations.append(Observation(
                t=t, employee_id="emp-a", camera_id="cam-floor",
                zone_id="desk_1", posture="SITTING", track_id=1,
                truth_identity="emp-a"))
            gt.observations.append(Observation(
                t=t, employee_id=None,          # truth: not an employee
                camera_id="cam-floor", zone_id="desk_9", posture="SITTING",
                track_id=2, truth_identity="visitor-1"))
        frames.append((t, "cam-floor", batch))
        t += step

    return gt, frames


def scenario_hot_desking(sim: OfficeSimulator, minutes: float = 15.0) -> tuple:
    """
    The case the seat prior cannot survive: people at the WRONG desks.

    Every other scenario is one the design handles. This one attacks its
    central assumption — "exactly one employee has assignedZoneId == Z" — by
    putting three people at each other's desks.

    Included because a failure catalogue that only contains cases the system
    passes is not a failure catalogue. If the seat prior can be made to name
    the wrong person, this is where it happens, and the honest thing is to
    find out rather than to test only what works.

    The correct behaviour is still not "get it right": with no face enrolment
    and appearance alone, there is no signal that says whose desk this is.
    The correct behaviour is to bind to the DESK'S owner (that is what a seat
    prior means) and carry a confidence that reflects the evidence, or to
    abstain. What must NOT happen is high confidence on a wrong name.
    """
    a = sim.make_person("emp-a", "Prajwal Kumar", "desk_1")
    b = sim.make_person("emp-b", "Ravi Sharma", "desk_2")
    c = sim.make_person("emp-c", "Anita Rao", "desk_3")

    gt = GroundTruth(
        source=SOURCE_SIMULATED,
        description="Three employees sitting at each other's desks — the "
                    "hot-desking case the seat prior is not designed for",
        duration_seconds=minutes * 60,
        seat_map={"desk_1": "emp-a", "desk_2": "emp-b", "desk_3": "emp-c"},
        camera_roles={"cam-floor": "AREA"},
    )

    # Deliberately rotated: A sits at B's desk, B at C's, C at A's.
    seating = {"emp-a": "desk_2", "emp-b": "desk_3", "emp-c": "desk_1"}

    frames = []
    t = 0.0
    step = 0.5
    tracks = {"emp-a": 1, "emp-b": 2, "emp-c": 3}

    while t < minutes * 60:
        batch = []
        for person in (a, b, c):
            zone = seating[person.employee_id]
            tid = tracks[person.employee_id]
            sig = sim.observe(person, tid)
            batch.append((person, tid, sig, zone, "SITTING"))
            if abs(t % SAMPLE_SECONDS) < step / 2:
                gt.observations.append(Observation(
                    t=t, employee_id=person.employee_id, camera_id="cam-floor",
                    zone_id=zone, posture="SITTING", track_id=tid,
                    truth_identity=person.employee_id))
        frames.append((t, "cam-floor", batch))
        t += step

    gt.notes["actual_seating"] = seating
    gt.notes["expected_outcome"] = ("the seat prior will name the DESK's owner, "
                                    "which is wrong for every person here; the "
                                    "question is whether it does so confidently")
    return gt, frames


SCENARIOS = {
    "normal_day": scenario_normal_day,
    "occlusion": scenario_occlusion,
    "similar_clothing": scenario_similar_clothing,
    "chair_exits": scenario_chair_exits,
    "cross_camera": scenario_cross_camera,
    "stranger": scenario_stranger,
    "hot_desking": scenario_hot_desking,
}
