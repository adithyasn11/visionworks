"""
Step 17 — run the evaluation and report the numbers.

    venv/Scripts/python.exe -m backend.eval.evaluate
    venv/Scripts/python.exe -m backend.eval.evaluate --scenario chair_exits
    venv/Scripts/python.exe -m backend.eval.evaluate --truth path/to/labels.json

With no arguments it runs every synthetic scenario. With `--truth` it runs
against a hand-labelled recording produced by `label.py`, using identical
measurement code — that is the whole design: the source of truth changes, the
arithmetic does not.

WHAT THE OUTPUT IS FOR

The plan asks for a specific sentence: "94% accurate on the 78% of cases above
0.75 confidence, correctly abstaining on the rest." That sentence is printed
here, computed from the data rather than written by hand, along with everything
needed to defend it — the confusion matrix, the per-band curve, and the
individual failures.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The Windows console defaults to cp1252, which cannot encode the arrows and
# box characters these reports use. Reconfiguring stdout is better than
# stripping the characters: the report should read the same on every platform,
# and a crash halfway through printing results is worse than a mojibake glyph.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.eval.groundtruth import GroundTruth, SOURCE_SIMULATED   # noqa: E402
from backend.eval.simulate import OfficeSimulator, SCENARIOS         # noqa: E402
from backend.eval.runner import run_scenario                         # noqa: E402
from backend.eval import metrics as M                                # noqa: E402


OUT_DIR = os.path.join(os.path.dirname(__file__), "results")


def evaluate_one(gt, frames, name: str) -> M.Evaluation:
    """Run one scenario end to end and score everything it supports."""
    run = run_scenario(gt, frames)
    predictions = run["predictions"]

    ev = M.Evaluation(source=gt.source, description=gt.description)
    ev.failures = []

    ident, bands, confusion = M.evaluate_identification(
        gt.observations, predictions, failures=ev.failures)
    ev.identification = ident
    ev.bands = bands
    ev.confusion = confusion

    ev.stitch = M.evaluate_stitching(gt.observations, predictions)

    # Handoffs, when the scenario declares them.
    truth_handoffs = [tuple(h) for h in gt.notes.get("handoffs", [])]
    if truth_handoffs:
        ev.handoff = M.evaluate_handoffs(truth_handoffs, predictions)

    # Chair exits, when the scenario declares them.
    true_exit_times = gt.notes.get("true_chair_exits")
    if true_exit_times is not None:
        # Every exit in these scenarios belongs to the single seated employee.
        emp = next(iter(gt.employees), None)
        truth_exits = [(emp, float(t)) for t in true_exit_times]
        ev.chair_exits = M.evaluate_chair_exits(truth_exits, run["exits"])

    # Annotate failures with the scenario, so the catalogue in Step 18 can say
    # which situation produced each one.
    for f in ev.failures:
        f["scenario"] = name
    return ev, run


def print_evaluation(name: str, ev: M.Evaluation, gt) -> None:
    print()
    print("=" * 74)
    print(f"  {name.upper().replace('_', ' ')}")
    print(f"  {ev.description}")
    print(f"  source: {ev.source}")
    print("=" * 74)

    i = ev.identification
    if i.total or i.strangers:
        print()
        print("IDENTIFICATION")
        print("-" * 74)
        print(f"  labelled observations of a known employee : {i.total}")
        print(f"    named somebody                          : {i.answered}")
        print(f"      correct                               : {i.correct}")
        print(f"      WRONG                                 : {i.wrong}")
        print(f"    abstained (said UNKNOWN)                : {i.abstained}")
        print(f"    no output at all                        : {i.missed}")
        if i.strangers:
            print(f"  observations of an unenrolled person      : {i.strangers}")
            print(f"    correctly refused to name               : {i.correct_rejections}")
            print(f"    WRONGLY named an employee               : {i.false_names}")
        if i.hallucinations:
            print(f"  named somebody who was not there          : {i.hallucinations}")
        print()
        print(f"  accuracy (of answered)   {i.accuracy:>7.1%}")
        print(f"  coverage (answered/all)  {i.coverage:>7.1%}")
        print(f"  abstention rate          {i.abstention_rate:>7.1%}")
        if i.strangers:
            print(f"  correct-rejection rate   {i.rejection_accuracy:>7.1%}")

    if any(b.n for b in ev.bands):
        print()
        print("ACCURACY vs CONFIDENCE")
        print("-" * 74)
        print("  band          n     correct    accuracy")
        for b in ev.bands:
            if not b.n:
                continue
            bar = "#" * int(round(b.accuracy * 28))
            print(f"  {b.low:.1f}-{b.high:.1f}  {b.n:>6} {b.correct:>10}"
                  f"    {b.accuracy:>6.1%}  {bar}")
        print()
        print(f"  >>> {ev.headline_at(M.ACCEPT_THRESHOLD)}")

    s = ev.stitch
    if s.raw_track_ids:
        print()
        print("FRAGMENT STITCHING")
        print("-" * 74)
        print(f"  raw ByteTrack ids            : {s.raw_track_ids}")
        print(f"  identities produced          : {s.produced_identities}")
        print(f"  real people                  : {s.true_people}")
        print(f"  fragmentation before / after : "
              f"{s.fragmentation_before:.2f} -> {s.fragmentation_after:.2f} ids per person")
        print(f"  joins made correctly         : {s.correct_stitches}")
        print(f"  joins missed                 : {s.missed_stitches}")
        print(f"  WRONG joins (two people merged): {s.wrong_stitches}")
        print(f"  stitch rate {s.stitch_rate:.1%}   purity {s.purity:.1%}")

    h = ev.handoff
    if h.attempts:
        print()
        print("CROSS-CAMERA HANDOFF")
        print("-" * 74)
        print(f"  crossings          : {h.attempts}")
        print(f"    correct          : {h.correct}")
        print(f"    wrong            : {h.wrong}")
        print(f"    UNKNOWN          : {h.unknown}")
        print(f"  accuracy {h.accuracy:.1%} of the {h.coverage:.1%} it answered")
        print(f"  (the plan expects 6-8 of 10 and calls that a normal result)")

    c = ev.chair_exits
    if c.true_positives or c.false_positives or c.false_negatives:
        print()
        print("CHAIR EXITS  <<< the number the plan calls most important")
        print("-" * 74)
        print(f"  detected correctly : {c.true_positives}")
        print(f"  false alarms       : {c.false_positives}")
        print(f"  missed             : {c.false_negatives}")
        print(f"  precision {c.precision:.1%}   recall {c.recall:.1%}   F1 {c.f1:.1%}")
        expected = gt.notes.get("expected_exit_count")
        if expected is not None:
            got = c.true_positives + c.false_positives
            verdict = "MATCHES" if got == expected else "DOES NOT MATCH"
            print(f"  reported {got} exits, truth has {expected} -> {verdict}")

    if ev.confusion:
        print()
        print("CONFUSION MATRIX  (rows = truth, columns = what the system said)")
        print("-" * 74)
        print(M.render_confusion(ev.confusion))

    if ev.failures:
        kinds = {}
        for f in ev.failures:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        print()
        print(f"FAILURES: {len(ev.failures)}  " +
              ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 17 — ground-truth evaluation")
    ap.add_argument("--scenario", help="run one synthetic scenario by name")
    ap.add_argument("--truth", help="path to a hand-labelled ground-truth file")
    ap.add_argument("--seed", type=int, default=20260830)
    ap.add_argument("--json", help="write the full results to this path")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    all_results = {}
    all_failures = []

    if args.truth:
        # Real footage. Frames must come from the same file the labeller used;
        # `label.py` writes them alongside the labels.
        gt = GroundTruth.load(args.truth)
        frames_path = gt.notes.get("frames_path")
        if not frames_path or not os.path.exists(frames_path):
            print("This ground-truth file has no extracted signatures beside it.")
            print("Run:  python -m backend.eval.label --video <file> --extract")
            return 1
        import pickle
        with open(frames_path, "rb") as f:
            frames = pickle.load(f)
        ev, _run = evaluate_one(gt, frames, "labelled")
        print_evaluation("labelled footage", ev, gt)
        all_results["labelled"] = ev
        all_failures.extend(ev.failures)
    else:
        names = [args.scenario] if args.scenario else list(SCENARIOS)
        for name in names:
            if name not in SCENARIOS:
                print(f"unknown scenario '{name}'. Available: {', '.join(SCENARIOS)}")
                return 1
            # A fresh simulator per scenario so one cannot influence the next.
            sim = OfficeSimulator(seed=args.seed)
            gt, frames = SCENARIOS[name](sim)
            gt.save(os.path.join(OUT_DIR, f"truth_{name}.json"))
            ev, _run = evaluate_one(gt, frames, name)
            if not args.quiet:
                print_evaluation(name, ev, gt)
            all_results[name] = ev
            all_failures.extend(ev.failures)

    # ── the overall picture ───────────────────────────────────────────────
    print()
    print("=" * 74)
    print("  OVERALL")
    print("=" * 74)

    total = M.IdentificationResult()
    bands = [M.BandResult(lo, hi) for lo, hi in M.CURVE_BANDS]
    for ev in all_results.values():
        i = ev.identification
        total.total += i.total
        total.answered += i.answered
        total.correct += i.correct
        total.wrong += i.wrong
        total.abstained += i.abstained
        total.missed += i.missed
        total.strangers += i.strangers
        total.correct_rejections += i.correct_rejections
        total.false_names += i.false_names
        total.hallucinations += i.hallucinations
        for a, b in zip(bands, ev.bands):
            a.n += b.n
            a.correct += b.correct

    combined = M.Evaluation(source=SOURCE_SIMULATED if not args.truth else "hand-labelled")
    combined.identification = total
    combined.bands = bands

    print(f"  scenarios run           : {len(all_results)}")
    print(f"  observations scored     : {total.total + total.strangers}")
    print(f"  accuracy (of answered)  : {total.accuracy:.1%}")
    print(f"  coverage                : {total.coverage:.1%}")
    print(f"  wrong names             : {total.wrong}")
    print(f"  correct rejections      : {total.correct_rejections}/{total.strangers}"
          if total.strangers else "  correct rejections      : n/a")
    print()
    print(f"  >>> {combined.headline_at(M.ACCEPT_THRESHOLD)}")
    print()
    if not args.truth:
        print("  NOTE: these figures come from a SIMULATED office. The pipeline,")
        print("  the stitching, the seat binding and the aggregation are real;")
        print("  the appearance vectors are generated to measured distributions.")
        print("  Re-run with --truth once real footage has been labelled.")

    # ── persist ───────────────────────────────────────────────────────────
    out = args.json or os.path.join(OUT_DIR, "evaluation.json")
    payload = {
        "source": combined.source,
        "scenarios": {},
        "overall": {
            "accuracy": round(total.accuracy, 4),
            "coverage": round(total.coverage, 4),
            "abstention_rate": round(total.abstention_rate, 4),
            "wrong": total.wrong,
            "strangers": total.strangers,
            "correct_rejections": total.correct_rejections,
            "headline": combined.headline_at(M.ACCEPT_THRESHOLD),
            "bands": [{"low": b.low, "high": b.high, "n": b.n,
                       "correct": b.correct, "accuracy": round(b.accuracy, 4)}
                      for b in bands],
        },
        "failures": all_failures,
    }
    for name, ev in all_results.items():
        i = ev.identification
        payload["scenarios"][name] = {
            "description": ev.description,
            "accuracy": round(i.accuracy, 4),
            "coverage": round(i.coverage, 4),
            "correct": i.correct, "wrong": i.wrong,
            "abstained": i.abstained, "missed": i.missed,
            "strangers": i.strangers,
            "correct_rejections": i.correct_rejections,
            "false_names": i.false_names,
            "hallucinations": i.hallucinations,
            "stitch": {
                "raw_ids": ev.stitch.raw_track_ids,
                "identities": ev.stitch.produced_identities,
                "people": ev.stitch.true_people,
                "rate": round(ev.stitch.stitch_rate, 4),
                "purity": round(ev.stitch.purity, 4),
                "wrong_joins": ev.stitch.wrong_stitches,
            },
            "handoff": {
                "attempts": ev.handoff.attempts,
                "correct": ev.handoff.correct,
                "wrong": ev.handoff.wrong,
                "unknown": ev.handoff.unknown,
                "accuracy": round(ev.handoff.accuracy, 4),
            },
            "chair_exits": {
                "tp": ev.chair_exits.true_positives,
                "fp": ev.chair_exits.false_positives,
                "fn": ev.chair_exits.false_negatives,
                "precision": round(ev.chair_exits.precision, 4),
                "recall": round(ev.chair_exits.recall, 4),
                "f1": round(ev.chair_exits.f1, 4),
            },
            "confusion": ev.confusion,
            "bands": [{"low": b.low, "high": b.high, "n": b.n,
                       "correct": b.correct} for b in ev.bands],
        }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"  results written to {out}")
    print(f"  {len(all_failures)} failure case(s) recorded for Step 18")

    return 0


if __name__ == "__main__":
    sys.exit(main())
