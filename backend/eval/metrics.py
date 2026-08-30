"""
The measurements Step 17 asks for, and nothing that flatters them.

  - identification accuracy
  - stitch rate
  - handoff accuracy
  - chair-exit precision / recall
  - accuracy vs. confidence curves
  - a confusion matrix

THE HEADLINE NUMBER, AND WHY IT HAS TWO HALVES

The plan is explicit about the shape of the result it wants:

    "94% accurate on the 78% of cases above 0.75 confidence, correctly
     abstaining on the rest"

That is two numbers because one number cannot describe an abstaining system.
A single "accuracy" figure has to decide what to do with the cases the system
declined to answer, and both choices lie:

  - Count abstentions as WRONG, and a system that correctly refuses to guess
    scores worse than one that guesses randomly. It is punished for honesty.
  - Count abstentions as RIGHT, and a system that abstains on everything
    scores 100%. It is rewarded for uselessness.

So coverage and accuracy are reported separately and never multiplied
together. Accuracy is measured over answered cases only; coverage says how
many it answered. Neither is interpretable alone, which is the point.

ABSTAINING ON A STRANGER IS A CORRECT ANSWER

Ground truth distinguishes "this is Prajwal" from "this is a person we have
never enrolled". When the truth says nobody, UNKNOWN is the right answer, and
counting it as an abstention would understate coverage while counting it as an
error would punish exactly the behaviour §0 of the plan demands. It is scored
as a correct rejection.
"""

from __future__ import annotations

from collections import defaultdict, Counter
from dataclasses import dataclass, field
from typing import Optional

from .groundtruth import pair_by_time


# The plan's operating point (§3): accept at ≥ 0.75, UNKNOWN below 0.50.
ACCEPT_THRESHOLD = 0.75
ABSTAIN_FLOOR = 0.50

# Confidence bands for the accuracy-vs-confidence curve. Deliberately wider at
# the bottom: nothing below 0.5 should be attributed at all, so fine resolution
# there measures a region the system is not supposed to operate in.
CURVE_BANDS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7),
               (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


@dataclass
class IdentificationResult:
    """Who the system said each person was, scored honestly."""
    total: int = 0                  # labelled observations of a known employee
    answered: int = 0               # ...where the system named somebody
    correct: int = 0                # ...and named the right person
    wrong: int = 0                  # ...and named the wrong person
    abstained: int = 0              # system said UNKNOWN about a known employee
    # Truth says "not an employee":
    strangers: int = 0
    correct_rejections: int = 0     # ...and the system also said UNKNOWN
    false_names: int = 0            # ...but the system named an employee
    hallucinations: int = 0         # a prediction with no person there at all
    missed: int = 0                 # a labelled person the system never saw

    @property
    def accuracy(self) -> float:
        """Of the cases it ANSWERED, how many were right."""
        return self.correct / self.answered if self.answered else 0.0

    @property
    def coverage(self) -> float:
        """Of the cases it could have answered, how many it did."""
        return self.answered / self.total if self.total else 0.0

    @property
    def abstention_rate(self) -> float:
        return self.abstained / self.total if self.total else 0.0

    @property
    def rejection_accuracy(self) -> float:
        """Of the strangers, how many it correctly declined to name."""
        return (self.correct_rejections / self.strangers
                if self.strangers else 1.0)

    def headline(self) -> str:
        return (f"{self.accuracy:.1%} accurate on the {self.coverage:.1%} "
                f"of cases it answered, abstaining on the rest")


@dataclass
class BandResult:
    low: float
    high: float
    n: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


@dataclass
class StitchResult:
    """
    Did the tracker turn ByteTrack fragments back into people?

    The plan's own definition: "Count raw ids vs. stitched identities — the
    ratio is your stitch-rate metric."
    """
    raw_track_ids: int = 0
    produced_identities: int = 0
    true_people: int = 0
    correct_stitches: int = 0       # two fragments of one person, joined
    missed_stitches: int = 0        # ...left apart
    wrong_stitches: int = 0         # two DIFFERENT people, merged

    @property
    def fragmentation_before(self) -> float:
        """Raw ids per real person. 1.0 would mean ByteTrack never broke."""
        return self.raw_track_ids / self.true_people if self.true_people else 0.0

    @property
    def fragmentation_after(self) -> float:
        return (self.produced_identities / self.true_people
                if self.true_people else 0.0)

    @property
    def stitch_rate(self) -> float:
        """Of the joins that should have happened, how many did."""
        opportunities = self.correct_stitches + self.missed_stitches
        return self.correct_stitches / opportunities if opportunities else 1.0

    @property
    def purity(self) -> float:
        """
        Of the joins that DID happen, how many were right.

        A wrong stitch is worse than a missed one — it welds two people's days
        together — so this is reported beside the rate rather than folded into
        it.
        """
        made = self.correct_stitches + self.wrong_stitches
        return self.correct_stitches / made if made else 1.0


@dataclass
class HandoffResult:
    """Camera A → camera B. The plan expects 6-8 of 10 and says so."""
    attempts: int = 0
    correct: int = 0
    wrong: int = 0
    unknown: int = 0

    @property
    def accuracy(self) -> float:
        answered = self.correct + self.wrong
        return self.correct / answered if answered else 0.0

    @property
    def coverage(self) -> float:
        return ((self.correct + self.wrong) / self.attempts
                if self.attempts else 0.0)


@dataclass
class ChairExitResult:
    """
    Precision and recall on the number the plan calls the most important one.

    A predicted exit counts as a true positive when a real exit began within
    MATCH_WINDOW seconds of it. The window exists because the debounce means
    the system reports an exit ~90 s after the person actually stood up;
    demanding exact alignment would score a correct detection as both a miss
    and a false alarm.
    """
    MATCH_WINDOW = 120.0

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        p = self.true_positives + self.false_positives
        return self.true_positives / p if p else 1.0

    @property
    def recall(self) -> float:
        r = self.true_positives + self.false_negatives
        return self.true_positives / r if r else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class Evaluation:
    """Everything, in one object the report can render."""
    source: str = ""
    description: str = ""
    identification: IdentificationResult = field(default_factory=IdentificationResult)
    bands: list = field(default_factory=list)
    stitch: StitchResult = field(default_factory=StitchResult)
    handoff: HandoffResult = field(default_factory=HandoffResult)
    chair_exits: ChairExitResult = field(default_factory=ChairExitResult)
    confusion: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)

    def headline_at(self, threshold: float = ACCEPT_THRESHOLD) -> str:
        """The plan's sentence, computed rather than asserted."""
        above = [b for b in self.bands if b.low >= threshold]
        n = sum(b.n for b in above)
        c = sum(b.correct for b in above)
        total = sum(b.n for b in self.bands)
        if not total:
            return "no attributions to measure"
        acc = c / n if n else 0.0
        cov = n / total
        return (f"{acc:.0%} accurate on the {cov:.0%} of cases above "
                f"{threshold:.2f} confidence, correctly abstaining on the rest")


def evaluate_identification(truth, predictions, failures=None) -> tuple:
    """
    Score every labelled observation, and build the confusion matrix and the
    confidence curve in the same pass.

    Returns (IdentificationResult, bands, confusion).
    """
    res = IdentificationResult()
    bands = [BandResult(lo, hi) for lo, hi in CURVE_BANDS]
    confusion = defaultdict(Counter)     # truth -> Counter(predicted)

    for obs, pred in pair_by_time(truth, predictions):
        # A prediction about somebody who was not there.
        if obs is None:
            if pred.employee_id:
                res.hallucinations += 1
                confusion["(nobody)"][pred.employee_id] += 1
                if failures is not None:
                    failures.append({
                        "kind": "hallucination",
                        "t": pred.t, "camera": pred.camera_id,
                        "said": pred.employee_id,
                        "confidence": pred.confidence, "method": pred.method,
                        "why": "named somebody at a moment the truth has no person",
                    })
            continue

        # Truth: a person who is not an enrolled employee.
        if obs.employee_id is None:
            res.strangers += 1
            if pred is None or pred.employee_id is None:
                res.correct_rejections += 1
                confusion["(stranger)"]["UNKNOWN"] += 1
            else:
                res.false_names += 1
                confusion["(stranger)"][pred.employee_id] += 1
                if failures is not None:
                    failures.append({
                        "kind": "false_name",
                        "t": obs.t, "camera": obs.camera_id,
                        "truth": "not an employee", "said": pred.employee_id,
                        "confidence": pred.confidence, "method": pred.method,
                        "why": "named an enrolled employee for an unenrolled person",
                    })
            continue

        # Truth: a known employee.
        res.total += 1

        if pred is None:
            # The system produced nothing at all for a person who was there.
            res.missed += 1
            confusion[obs.employee_id]["(no output)"] += 1
            if failures is not None:
                failures.append({
                    "kind": "missed",
                    "t": obs.t, "camera": obs.camera_id,
                    "truth": obs.employee_id,
                    "why": "person present in the truth, no prediction produced",
                })
            continue

        if pred.employee_id is None:
            res.abstained += 1
            confusion[obs.employee_id]["UNKNOWN"] += 1
            if failures is not None:
                failures.append({
                    "kind": "abstention",
                    "t": obs.t, "camera": obs.camera_id,
                    "truth": obs.employee_id,
                    "confidence": pred.confidence, "method": pred.method,
                    "why": "declined to name a known employee",
                })
            continue

        res.answered += 1
        right = pred.employee_id == obs.employee_id
        confusion[obs.employee_id][pred.employee_id] += 1

        if right:
            res.correct += 1
        else:
            res.wrong += 1
            if failures is not None:
                failures.append({
                    "kind": "misidentification",
                    "t": obs.t, "camera": obs.camera_id,
                    "truth": obs.employee_id, "said": pred.employee_id,
                    "confidence": pred.confidence, "method": pred.method,
                    "why": "named the wrong employee — the worst failure mode",
                })

        # The curve counts only ANSWERED cases: a band's accuracy is
        # "when it spoke at this confidence, how often was it right".
        for b in bands:
            if b.low <= pred.confidence < b.high:
                b.n += 1
                if right:
                    b.correct += 1
                break

    return res, bands, {k: dict(v) for k, v in confusion.items()}


def evaluate_stitching(truth, predictions) -> StitchResult:
    """
    Compare the identity threads the system produced with the true ones.

    Needs `truth_identity` on the observations — the thread a person really
    followed across fragments. A human labeller supplies it by naming the
    person; the simulator knows it by construction.
    """
    res = StitchResult()

    raw_ids = {(o.camera_id, o.track_id) for o in truth if o.track_id is not None}
    res.raw_track_ids = len(raw_ids)
    res.true_people = len({o.truth_identity or o.employee_id for o in truth
                           if (o.truth_identity or o.employee_id)})
    res.produced_identities = len({p.identity_id for p in predictions
                                   if p.identity_id})

    # Which system identity did each true person's fragments land in?
    by_truth = defaultdict(set)          # true person -> {system identity}
    by_system = defaultdict(set)         # system identity -> {true person}

    paired = pair_by_time(truth, predictions)
    for obs, pred in paired:
        if obs is None or pred is None or not pred.identity_id:
            continue
        thread = obs.truth_identity or obs.employee_id
        if not thread:
            continue
        by_truth[thread].add(pred.identity_id)
        by_system[pred.identity_id].add(thread)

    # A person split across N system identities missed N-1 joins.
    for thread, ids in by_truth.items():
        if len(ids) > 1:
            res.missed_stitches += len(ids) - 1

    # A system identity holding N true people made N-1 wrong joins; one holding
    # exactly one person, across more than one fragment, made correct ones.
    fragments_per_identity = defaultdict(set)
    for obs, pred in paired:
        if obs is None or pred is None or not pred.identity_id:
            continue
        if obs.track_id is not None:
            fragments_per_identity[pred.identity_id].add(obs.track_id)

    for ident, people in by_system.items():
        if len(people) > 1:
            res.wrong_stitches += len(people) - 1
        else:
            frags = len(fragments_per_identity.get(ident, ()))
            if frags > 1:
                res.correct_stitches += frags - 1

    return res


def evaluate_handoffs(truth_handoffs, predictions) -> HandoffResult:
    """
    `truth_handoffs`: [(employee_id, from_camera, to_camera, t_arrival)] —
    the moments a person really crossed between cameras.
    """
    res = HandoffResult()
    for emp, _from_cam, to_cam, t in truth_handoffs:
        res.attempts += 1
        # What did the system say about that person, on that camera, then?
        window = [p for p in predictions
                  if p.camera_id == to_cam and abs(p.t - t) <= 30.0]
        if not window:
            res.unknown += 1
            continue
        named = [p for p in window if p.employee_id]
        if not named:
            res.unknown += 1
        elif any(p.employee_id == emp for p in named[:3]):
            res.correct += 1
        else:
            res.wrong += 1
    return res


def evaluate_chair_exits(truth_exits, predicted_exits) -> ChairExitResult:
    """
    `truth_exits` / `predicted_exits`: [(employee_id, t_seconds)].

    Greedy nearest match within MATCH_WINDOW, each truth consumed once — so a
    system that emits ten exits for one real departure scores one true positive
    and nine false alarms, which is the honest reading.
    """
    res = ChairExitResult()
    remaining = list(truth_exits)

    for emp, t in sorted(predicted_exits, key=lambda x: x[1]):
        candidates = [(abs(tt - t), i) for i, (e, tt) in enumerate(remaining)
                      if e == emp and abs(tt - t) <= ChairExitResult.MATCH_WINDOW]
        if candidates:
            _, idx = min(candidates)
            remaining.pop(idx)
            res.true_positives += 1
        else:
            res.false_positives += 1

    res.false_negatives = len(remaining)
    return res


def render_confusion(confusion: dict, labels: list = None) -> str:
    """The confusion matrix as text, for the console and the report."""
    if not confusion:
        return "(no attributions to tabulate)"

    rows = labels or sorted(confusion.keys())
    cols = sorted({c for r in confusion.values() for c in r})
    # UNKNOWN and the no-output column belong at the right, after the names:
    # they are outcomes, not people.
    names = [c for c in cols if c not in ("UNKNOWN", "(no output)")]
    tail = [c for c in cols if c in ("UNKNOWN", "(no output)")]
    cols = names + tail

    w = max([len(str(r)) for r in rows] + [12])
    head = " " * (w + 2) + "".join(f"{c[:11]:>13}" for c in cols)
    lines = [head, " " * (w + 2) + "-" * (13 * len(cols))]

    for r in rows:
        cells = "".join(f"{confusion.get(r, {}).get(c, 0):>13}" for c in cols)
        lines.append(f"{str(r)[:w]:<{w}} |{cells}")

    return "\n".join(lines)
