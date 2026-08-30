"""
Steps 17/18 — is the evaluation itself trustworthy?

An evaluation harness is the one piece of code nothing else checks. If
`metrics.py` counts wrong, every number in the report is wrong and every test
above it still passes. So this file grades the grader, on cases where the right
answer is known by construction.

The properties that matter:

  1. Accuracy must be computed over ANSWERED cases only. Fold abstentions in
     and an honest system scores worse than a guessing one.
  2. Abstaining on a stranger must count as CORRECT, not as a miss.
  3. A wrong name must never be scored as anything but wrong.
  4. The metrics must not care where the truth came from — the same function
     scores simulated and hand-labelled data identically.
  5. The pairing must not silently drop predictions, or hallucinations vanish.

Run:  venv/Scripts/python.exe backend/tests/test_evaluation.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.eval.groundtruth import (Observation, Prediction, GroundTruth,   # noqa: E402
                                      pair_by_time, SOURCE_SIMULATED,
                                      SOURCE_LABELLED)
from backend.eval import metrics as M                                          # noqa: E402
from backend.eval.simulate import OfficeSimulator, SCENARIOS                   # noqa: E402
from backend.eval.runner import run_scenario                                   # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def obs(t, emp, cam="c1", zone="z1", track=1):
    return Observation(t=t, employee_id=emp, camera_id=cam, zone_id=zone,
                       track_id=track, truth_identity=emp or f"stranger-{track}")


def pred(t, emp, conf=0.9, cam="c1", zone="z1", track=1, ident="i1"):
    return Prediction(t=t, employee_id=emp, camera_id=cam, zone_id=zone,
                      confidence=conf, method="seat" if emp else "unknown",
                      track_id=track, identity_id=ident)


def main():
    print("=" * 70)
    print("STEPS 17/18 — GRADING THE GRADER")
    print("=" * 70)

    # ══ A. THE ARITHMETIC ══════════════════════════════════════════════════
    print("\nA. ACCURACY IS OVER ANSWERED CASES, NOT ALL CASES")
    print("-" * 70)
    truth = [obs(i * 5.0, "emp-a") for i in range(10)]
    # 6 right, 2 wrong, 2 abstentions.
    preds = ([pred(i * 5.0, "emp-a") for i in range(6)]
             + [pred(i * 5.0, "emp-b") for i in (6, 7)]
             + [pred(i * 5.0, None, conf=0.2) for i in (8, 9)])
    r, bands, conf = M.evaluate_identification(truth, preds)
    print(f"   10 observations: 6 correct, 2 wrong, 2 abstained")
    print(f"   accuracy {r.accuracy:.1%}  coverage {r.coverage:.1%}  "
          f"abstention {r.abstention_rate:.1%}")
    check("accuracy is 6/8, not 6/10", abs(r.accuracy - 0.75) < 1e-9,
          f"{r.accuracy:.3f}")
    check("coverage is 8/10", abs(r.coverage - 0.8) < 1e-9, f"{r.coverage:.3f}")
    check("abstention rate is 2/10", abs(r.abstention_rate - 0.2) < 1e-9)
    check("the wrong answers are counted", r.wrong == 2, str(r.wrong))

    # The failure mode this guards against.
    naive = r.correct / r.total
    print(f"   (folding abstentions in would report {naive:.1%} — punishing honesty)")
    check("the honest figure differs from the naive one", abs(naive - r.accuracy) > 0.01)

    # ══ B. ABSTAINING ON A STRANGER IS CORRECT ═════════════════════════════
    print("\nB. A STRANGER CORRECTLY UNNAMED IS A CORRECT ANSWER")
    print("-" * 70)
    truth = [obs(0.0, None, track=2), obs(5.0, None, track=2)]
    preds = [pred(0.0, None, conf=0.1, track=2), pred(5.0, None, conf=0.1, track=2)]
    r, _, _ = M.evaluate_identification(truth, preds)
    print(f"   strangers={r.strangers} correct_rejections={r.correct_rejections} "
          f"false_names={r.false_names}")
    check("a stranger is not counted as a missed employee", r.total == 0,
          f"total={r.total}")
    check("refusing to name them is a correct rejection",
          r.correct_rejections == 2, str(r.correct_rejections))
    check("rejection accuracy is 100%", r.rejection_accuracy == 1.0)

    # ...and naming one is a serious error.
    preds = [pred(0.0, "emp-a", track=2), pred(5.0, "emp-a", track=2)]
    r2, _, _ = M.evaluate_identification(truth, preds)
    print(f"   naming them instead -> false_names={r2.false_names}")
    check("naming a stranger is counted as a false name", r2.false_names == 2,
          str(r2.false_names))
    check("...and is NOT counted as correct", r2.correct_rejections == 0)

    # ══ C. HALLUCINATIONS AND MISSES SURVIVE THE PAIRING ═══════════════════
    print("\nC. NOTHING IS SILENTLY DROPPED")
    print("-" * 70)
    truth = [obs(0.0, "emp-a")]
    preds = [pred(0.0, "emp-a"), pred(60.0, "emp-b", track=9)]   # 2nd is spurious
    pairs = pair_by_time(truth, preds)
    print(f"   1 truth + 2 predictions -> {len(pairs)} pairs")
    check("an unmatched prediction still appears in the pairing",
          len(pairs) == 2, str(len(pairs)))
    r, _, _ = M.evaluate_identification(truth, preds)
    check("...and is counted as a hallucination", r.hallucinations == 1,
          str(r.hallucinations))

    truth = [obs(0.0, "emp-a"), obs(5.0, "emp-a")]
    r, _, _ = M.evaluate_identification(truth, [pred(0.0, "emp-a")])
    check("a person with no prediction is counted as missed", r.missed == 1,
          str(r.missed))
    check("...and is not silently treated as an abstention", r.abstained == 0)

    # ══ D. THE CONFIDENCE CURVE ════════════════════════════════════════════
    print("\nD. THE CURVE BINS BY THE PREDICTION'S OWN CONFIDENCE")
    print("-" * 70)
    truth = [obs(i * 5.0, "emp-a") for i in range(4)]
    preds = [pred(0.0, "emp-a", conf=0.95), pred(5.0, "emp-a", conf=0.85),
             pred(10.0, "emp-b", conf=0.55), pred(15.0, "emp-b", conf=0.65)]
    r, bands, _ = M.evaluate_identification(truth, preds)
    filled = {(b.low, b.high): (b.n, b.correct) for b in bands if b.n}
    print(f"   {filled}")
    check("each prediction lands in its own band", len(filled) == 4, str(filled))
    check("the high bands are correct", filled.get((0.9, 1.01)) == (1, 1))
    check("the low bands are wrong", filled.get((0.5, 0.6)) == (1, 0))

    ev = M.Evaluation(bands=bands)
    head = ev.headline_at(0.75)
    print(f"   headline: {head}")
    check("the headline reports accuracy above the threshold only",
          "100%" in head and "50%" in head, head)

    # ══ E. CHAIR EXITS ═════════════════════════════════════════════════════
    print("\nE. CHAIR-EXIT MATCHING IS ONE-TO-ONE")
    print("-" * 70)
    # The debounce means a detection lags the real departure; the window allows
    # it. Ten detections of one real exit must not score ten true positives.
    res = M.evaluate_chair_exits([("emp-a", 100.0)],
                                 [("emp-a", 150.0)] )
    check("a detection inside the window matches", res.true_positives == 1)
    res = M.evaluate_chair_exits([("emp-a", 100.0)],
                                 [("emp-a", 110.0 + i) for i in range(10)])
    print(f"   1 real exit, 10 reported -> tp={res.true_positives} "
          f"fp={res.false_positives}")
    check("each truth is consumed once", res.true_positives == 1,
          str(res.true_positives))
    check("the surplus are false alarms", res.false_positives == 9,
          str(res.false_positives))
    res = M.evaluate_chair_exits([("emp-a", 100.0)], [("emp-a", 1000.0)])
    check("a detection far outside the window is not a match",
          res.true_positives == 0 and res.false_negatives == 1)
    res = M.evaluate_chair_exits([("emp-a", 100.0)], [("emp-b", 105.0)])
    check("an exit attributed to the wrong person does not match",
          res.true_positives == 0, "matched across employees")

    # ══ F. STITCHING ═══════════════════════════════════════════════════════
    print("\nF. STITCH RATE AND PURITY MEASURE DIFFERENT THINGS")
    print("-" * 70)
    # One person, two fragments, joined into one identity: a correct stitch.
    truth = [obs(0.0, "emp-a", track=1), obs(5.0, "emp-a", track=2)]
    preds = [pred(0.0, "emp-a", track=1, ident="i1"),
             pred(5.0, "emp-a", track=2, ident="i1")]
    s = M.evaluate_stitching(truth, preds)
    print(f"   joined: correct={s.correct_stitches} wrong={s.wrong_stitches} "
          f"rate={s.stitch_rate:.0%} purity={s.purity:.0%}")
    check("joining one person's fragments is a correct stitch",
          s.correct_stitches == 1, str(s.correct_stitches))
    check("purity stays 100% when the join was right", s.purity == 1.0)

    # Two DIFFERENT people merged into one identity: a wrong stitch.
    truth = [obs(0.0, "emp-a", track=1), obs(5.0, "emp-b", track=2)]
    preds = [pred(0.0, "emp-a", track=1, ident="i1"),
             pred(5.0, "emp-a", track=2, ident="i1")]
    s = M.evaluate_stitching(truth, preds)
    print(f"   merged two people: wrong={s.wrong_stitches} purity={s.purity:.0%}")
    check("merging two people is a WRONG stitch", s.wrong_stitches == 1,
          str(s.wrong_stitches))
    check("purity falls when a join was wrong", s.purity < 1.0,
          f"{s.purity:.2f}")

    # One person split across two identities: a missed stitch.
    truth = [obs(0.0, "emp-a", track=1), obs(5.0, "emp-a", track=2)]
    preds = [pred(0.0, "emp-a", track=1, ident="i1"),
             pred(5.0, "emp-a", track=2, ident="i2")]
    s = M.evaluate_stitching(truth, preds)
    print(f"   split one person: missed={s.missed_stitches} rate={s.stitch_rate:.0%}")
    check("splitting one person is a missed stitch", s.missed_stitches == 1)
    check("the rate falls when a join was missed", s.stitch_rate < 1.0)

    # ══ G. PROVENANCE CANNOT BE LOST ═══════════════════════════════════════
    print("\nG. A SIMULATED RESULT CANNOT BE MISTAKEN FOR A REAL ONE")
    print("-" * 70)
    gt = GroundTruth(source=SOURCE_SIMULATED, description="x",
                     duration_seconds=10)
    check("simulated truth reports itself as not real", gt.is_real is False)
    gt2 = GroundTruth(source=SOURCE_LABELLED, description="x",
                      duration_seconds=10)
    check("hand-labelled truth reports itself as real", gt2.is_real is True)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json")
        gt.observations = [obs(0.0, "emp-a")]
        gt.save(path)
        back = GroundTruth.load(path)
    check("provenance survives a save/load round trip",
          back.source == SOURCE_SIMULATED, back.source)
    check("...and so do the observations",
          len(back.observations) == 1 and back.observations[0].employee_id == "emp-a")

    # ══ H. THE SCENARIOS ACTUALLY EXERCISE THE REAL PIPELINE ═══════════════
    print("\nH. THE SCENARIOS DRIVE THE REAL TRACKER, NOT A MOCK")
    print("-" * 70)
    sim = OfficeSimulator(seed=20260830)
    gt, frames = SCENARIOS["occlusion"](sim)
    run = run_scenario(gt, frames)
    trackers = run["trackers"]
    from app.cv.identity_tracker import IdentityTracker
    check("the runner used the production IdentityTracker",
          all(isinstance(t, IdentityTracker) for t in trackers.values()))
    check("it produced predictions", len(run["predictions"]) > 0,
          str(len(run["predictions"])))
    s = M.evaluate_stitching(gt.observations, run["predictions"])
    print(f"   occlusion: {s.raw_track_ids} raw ids -> "
          f"{s.produced_identities} identities for {s.true_people} people")
    check("the occlusion scenario really does fragment the tracks",
          s.raw_track_ids > s.true_people,
          f"{s.raw_track_ids} vs {s.true_people}")
    check("...and the tracker stitches them back", s.wrong_stitches == 0
          and s.produced_identities <= s.true_people + 1,
          f"{s.produced_identities} identities")

    # ══ I. DETERMINISM ═════════════════════════════════════════════════════
    print("\nI. THE SAME SEED PRODUCES THE SAME NUMBERS")
    print("-" * 70)
    def run_once():
        s = OfficeSimulator(seed=4242)
        g, f = SCENARIOS["normal_day"](s)
        r = run_scenario(g, f)
        res, _, _ = M.evaluate_identification(g.observations, r["predictions"])
        return res.correct, res.wrong, res.abstained
    a1 = run_once()
    a2 = run_once()
    print(f"   run 1: {a1}\n   run 2: {a2}")
    check("a metric that changes between runs cannot be quoted", a1 == a2,
          f"{a1} vs {a2}")

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("EVALUATION HARNESS: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
