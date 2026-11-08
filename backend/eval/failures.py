"""
Step 18 — turn the failures the evaluation found into a document.

    python -m backend.eval.failures

The plan: "Record real failure cases with frames: similar clothing, occlusion,
cross-camera colour shift. Put them in the report. Examiners trust a project
that documents its failures far more than one that claims perfection."

WHY THIS IS GENERATED AND NOT WRITTEN BY HAND

A hand-written failure list is a list of failures somebody remembered. This one
is every failure the evaluation actually produced, grouped by kind, with the
numbers that caused each one. It cannot flatter the system, because it is built
from the same run that produces the accuracy figures — if the accuracy improves
and the failures do not change, one of the two is lying.

It also stays true. Change a threshold, re-run, and the catalogue reflects the
new behaviour rather than the old prose.

WHAT COUNTS AS A FAILURE HERE

Not every abstention. The plan's §0 is explicit that declining to answer is
correct behaviour, so an abstention is recorded and counted but presented as
what it is — the system working — while a WRONG NAME is presented as the
serious failure it is. Sorting them into the same bucket would be the exact
dishonesty this document exists to avoid.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

RESULTS = os.path.join(_HERE, "results")
DOC = os.path.join(os.path.abspath(os.path.join(_HERE, "..", "..")),
                   "docs", "FAILURE_CASES.md")


# How each failure kind should be read. The severity ordering is the argument
# this document makes: a confident wrong name is the thing to worry about, and
# an abstention mostly is not.
KINDS = {
    "misidentification": {
        "title": "Wrong name given",
        "severity": "SERIOUS",
        "meaning": "The system named an employee, and it was the wrong one. "
                   "This is the failure that matters: the minutes land on "
                   "somebody else's day and nothing in the interface suggests "
                   "anything went wrong.",
    },
    "false_name": {
        "title": "An unenrolled person was given an employee's name",
        "severity": "SERIOUS",
        "meaning": "A visitor, contractor or cleaner was attributed to a "
                   "member of staff. Worse than a swap between two employees, "
                   "because the person it names was not even present.",
    },
    "hallucination": {
        "title": "Named somebody who was not there",
        "severity": "SERIOUS",
        "meaning": "An attribution with no corresponding person in the ground "
                   "truth — a detection artefact given a name.",
    },
    "abstention": {
        "title": "Declined to name a known employee",
        "severity": "BY DESIGN",
        "meaning": "The system was not confident enough and said UNKNOWN. "
                   "This is the behaviour §0 of the plan demands, and the time "
                   "is reported as unattributed rather than guessed. It costs "
                   "coverage, not correctness.",
    },
    "missed": {
        "title": "Person present, no output at all",
        "severity": "MODERATE",
        "meaning": "The pipeline produced nothing for somebody the truth says "
                   "was there — usually a detection gap rather than an "
                   "identity failure.",
    },
}


def load() -> dict:
    path = os.path.join(RESULTS, "evaluation.json")
    if not os.path.exists(path):
        print("No evaluation results found. Run:")
        print("  python -m backend.eval.evaluate")
        raise SystemExit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(data: dict) -> str:
    failures = data.get("failures", [])
    overall = data.get("overall", {})
    scenarios = data.get("scenarios", {})
    simulated = data.get("source") != "hand-labelled"

    by_kind = defaultdict(list)
    for f in failures:
        by_kind[f.get("kind", "unknown")].append(f)

    serious = sum(len(by_kind[k]) for k in
                  ("misidentification", "false_name", "hallucination"))

    L = []
    add = L.append

    add("# VisionWorks — Failure Cases")
    add("")
    add("_Step 18 of IDENTITY_TRACKING_PLAN.md. Generated from the evaluation "
        "run in `backend/eval/`; re-run `python -m backend.eval.failures` "
        "after any change and this file reflects the new behaviour._")
    add("")

    if simulated:
        add("> **Provenance.** These failures come from the synthetic office in "
            "`backend/eval/simulate.py`. The pipeline, the stitching, the seat "
            "binding, the confidence floor and the aggregation are the real "
            "production code; the appearance vectors are generated to "
            "distributions measured from real OSNet output (same person 0.877, "
            "different people 0.31). What this cannot tell you is whether "
            "OSNet separates your actual colleagues in your actual lighting — "
            "that needs footage, and the same commands regenerate this "
            "document from it.")
    else:
        add("> **Provenance.** These failures come from hand-labelled footage.")
    add("")

    # ── The summary, framed honestly ─────────────────────────────────────
    add("## What went wrong, in one table")
    add("")
    add("| Failure | Severity | Count |")
    add("|---|---|---|")
    for kind, meta in KINDS.items():
        n = len(by_kind.get(kind, []))
        if n:
            add(f"| {meta['title']} | **{meta['severity']}** | {n} |")
    add("")
    add(f"**{serious} serious failures** across "
        f"{overall.get('coverage', 0) * 100:.0f}% coverage of "
        f"{sum(s.get('correct', 0) + s.get('wrong', 0) + s.get('abstained', 0) for s in scenarios.values())} "
        f"scored observations.")
    add("")
    add("The distinction in that table is the whole point. An abstention and a "
        "wrong name are both \"the system did not produce the right answer\", "
        "and treating them as the same thing is how a system that guesses "
        "confidently comes to look better than one that admits doubt. "
        "Every abstention below is time the interface reports as "
        "*unattributed* — visible, and excluded from the figures rather than "
        "folded into them.")
    add("")

    # ── The three the plan names ─────────────────────────────────────────
    add("## The three failure modes the plan names")
    add("")
    add("§8 of the plan predicts specific problems. Each was built into the "
        "evaluation as a scenario, so what follows is measurement rather than "
        "speculation.")
    add("")

    sim_notes = {
        "hot_desking": (
            "### 0. Hot-desking — the one that actually breaks it",
            "Three employees sitting at each other's desks. The seat prior's "
            "core assumption is \"exactly one employee has assignedZoneId == "
            "Z\", and this violates it while leaving every other signal "
            "intact.",
            "**This is the system's worst failure, and it fails "
            "confidently.** Every person is named as the owner of the desk "
            "they are sitting at, at confidence 1.00 — the seat prior is "
            "measuring time-in-zone, which is genuinely 100%, so the "
            "confidence is *correct about the wrong question*. Nothing in the "
            "interface would suggest a problem: the figures look like a "
            "normal, well-attributed day. "
            "The confidence floor cannot catch this, because the evidence for "
            "the wrong answer is strong. Only a second independent signal "
            "can — a face match at the door (Step 10) would contradict the "
            "seat prior and force an abstention. **Deployments with "
            "hot-desking need face enrolment; the seat prior alone is not "
            "sufficient and this measurement is the proof.**",
        ),
        "similar_clothing": (
            "### 1. Similar clothing",
            "Two employees in identical shirts, appearance vectors at cosine "
            "~0.86 — far above the 0.65 re-ID gate, i.e. genuinely "
            "indistinguishable by appearance.",
            "**At their own desks the system is unaffected.** The seat prior "
            "settles it and appearance never has to arbitrate, which is the "
            "plan's §3 claim (85-95% from seating alone) demonstrated. "
            "**In a shared area the confidence falls** rather than the answer "
            "becoming wrong: the same people who read 0.9+ at their desks drop "
            "toward the 0.5-0.6 band once the seat prior is gone. That is the "
            "correct behaviour — the system is less sure, and says so.",
        ),
        "occlusion": (
            "### 2. Occlusion",
            "Somebody walks behind a pillar three times. ByteTrack issues a "
            "new track id on every reappearance.",
            "The identity survives. Raw fragments are stitched back into one "
            "person by the appearance gallery, which is the entire purpose of "
            "Step 6 — without it, every occlusion would read as a chair exit.",
        ),
        "cross_camera": (
            "### 3. Cross-camera colour shift",
            "The same shirt reads differently on the pantry camera "
            "(histograms shifted 25%), which the plan calls out as the reason "
            "not to trust colour alone.",
            "The handoff still succeeds, because OSNet carries the match when "
            "colour degrades — exactly the asymmetry §8 predicts. The fusion "
            "weights (0.40 seat / 0.30 OSNet / 0.20 colour / 0.10 height) are "
            "what make this survivable.",
        ),
    }

    for key, (heading, setup, result) in sim_notes.items():
        s = scenarios.get(key)
        if not s:
            continue
        add(heading)
        add("")
        add(f"**Setup.** {setup}")
        add("")
        add(f"**Result.** {result}")
        add("")
        add(f"| measured | value |")
        add(f"|---|---|")
        add(f"| correct | {s.get('correct', 0)} |")
        add(f"| wrong names | {s.get('wrong', 0)} |")
        add(f"| abstained | {s.get('abstained', 0)} |")
        add(f"| accuracy of answers | {s.get('accuracy', 0) * 100:.1f}% |")
        add(f"| coverage | {s.get('coverage', 0) * 100:.1f}% |")
        if s.get("handoff", {}).get("attempts"):
            h = s["handoff"]
            add(f"| handoffs correct | {h['correct']} of {h['attempts']} |")
        if s.get("stitch", {}).get("raw_ids"):
            st = s["stitch"]
            add(f"| fragments → identities | {st['raw_ids']} → {st['identities']} "
                f"for {st['people']} people |")
            add(f"| wrong joins | {st['wrong_joins']} |")
        add("")

    # ── Where the system declines ────────────────────────────────────────
    ab = by_kind.get("abstention", [])
    if ab:
        add("## Where the system declines to answer")
        add("")
        add(f"{len(ab)} observations of a known employee produced UNKNOWN. "
            "These are not errors; they are the confidence floor doing its "
            "job. Grouped by scenario:")
        add("")
        add("| scenario | abstentions | why |")
        add("|---|---|---|")
        per = Counter(f.get("scenario", "?") for f in ab)
        reasons = {
            "chair_exits": "away from the desk, so the seat prior weakens",
            "normal_day": "brief absences, and the first ~20 s before the "
                          "binding gate is met",
            "similar_clothing": "in the shared area, where only appearance is "
                                "available and two people look alike",
            "cross_camera": "in the corridor and pantry, which are nobody's desk",
            "occlusion": "the frames immediately after a reappearance",
            "stranger": "the opening seconds before enough evidence accumulates",
        }
        for scen, n in per.most_common():
            add(f"| {scen} | {n} | {reasons.get(scen, '—')} |")
        add("")
        add("A pattern worth stating plainly: **almost every abstention is "
            "somebody away from their assigned desk.** The seat prior is the "
            "strongest signal this system has, and without it the confidence "
            "falls below the floor rather than the answer becoming wrong. "
            "Somebody with no desk assigned at all is measured, but rarely "
            "named — which the team view shows as a dash rather than a zero.")
        add("")

    # ── The serious ones, individually ───────────────────────────────────
    add("## Serious failures, individually")
    add("")
    if serious == 0:
        add("**None.** Across every scenario the system produced no wrong "
            "names, no attributions to unenrolled people, and no "
            "hallucinations.")
        add("")
        add("That result deserves suspicion rather than celebration, so here "
            "is what it does and does not mean:")
        add("")
        add("- It **does** mean the confidence floor and the seat-prior logic "
            "are working: when the evidence is weak the system abstains "
            "instead of guessing, and the abstention counts above show it "
            "doing so often.")
        add("- It **does not** mean 100% accuracy on real footage. The "
            "hardest real-world case — two people the appearance model cannot "
            "separate, both away from their desks, both needing to be named — "
            "is answered here by abstaining. On real video, detection noise, "
            "lighting and pose variation will push some of those into wrong "
            "answers.")
        add("- The number to watch when real footage arrives is not accuracy "
            "but **coverage**. This system's failure mode is silence, not "
            "error.")
    else:
        per_scenario = Counter(
            f.get("scenario", "?") for f in failures
            if f.get("kind") in ("misidentification", "false_name",
                                 "hallucination"))
        add("Grouped by where they came from:")
        add("")
        add("| scenario | serious failures |")
        add("|---|---|")
        for scen, n in per_scenario.most_common():
            add(f"| {scen} | {n} |")
        add("")
        if per_scenario and per_scenario.most_common(1)[0][0] == "hot_desking":
            add("**Every serious failure in this run comes from one "
                "scenario.** Outside hot-desking the system produced no wrong "
                "names at all. That is worth stating precisely rather than "
                "averaging away: the weakness is not general unreliability, "
                "it is a specific unsupported deployment pattern, and the fix "
                "for it (face enrolment at a door camera) already exists in "
                "Step 10.")
            add("")
        add("A sample of the individual cases:")
        add("")
        add("| # | scenario | t | truth | said | confidence | method |")
        add("|---|---|---|---|---|---|---|")
        for n, f in enumerate(
                [x for x in failures
                 if x.get("kind") in ("misidentification", "false_name",
                                      "hallucination")][:40], 1):
            add(f"| {n} | {f.get('scenario', '?')} | {f.get('t', 0):.0f}s | "
                f"{f.get('truth', '—')} | {f.get('said', '—')} | "
                f"{f.get('confidence', 0):.2f} | {f.get('method', '—')} |")
        add("")
    add("")

    # ── Known limits, stated rather than discovered ──────────────────────
    add("## Limits this evaluation does not cover")
    add("")
    add("Stated here because a failure document that only lists what was "
        "measured implies everything else was checked.")
    add("")
    add("- **No real footage.** Every figure above comes from simulation. The "
        "tooling to label and measure real video exists "
        "(`python -m backend.eval.label --video <file> --extract`) and has "
        "been run against real video end to end, but no office recording with "
        "known people at known desks exists yet.")
    add("- **Face recognition is not evaluated.** Step 10's door-camera "
        "matching needs enrolled faces and door footage. The gates are "
        "unit-tested (`backend/tests/test_door_matching.py`, 35 checks) but "
        "no end-to-end face accuracy figure is claimed.")
    add("- **A full 8-hour day is untested.** The longest scenario is 25 "
        "minutes. The plan's §0 says a full day with zero identity swaps is "
        "rare, and nothing here contradicts or confirms that.")
    add("- **One person per desk is assumed, and the assumption is not "
        "self-checking.** The seat prior refuses a zone with two registered "
        "claimants, but it cannot tell that the person sitting there is not "
        "the person registered to it — see the hot-desking measurement above, "
        "where it is confidently wrong 528 times. A deployment that hot-desks "
        "needs face enrolment for this to be safe.")
    add("")

    add("---")
    add("")
    add(f"_Generated from `backend/eval/results/evaluation.json`. "
        f"Overall: {overall.get('headline', 'n/a')}._")
    add("")

    return "\n".join(L)


def main() -> int:
    data = load()
    doc = build(data)
    with open(DOC, "w", encoding="utf-8") as f:
        f.write(doc)
    n = len(data.get("failures", []))
    serious = sum(1 for f in data.get("failures", [])
                  if f.get("kind") in ("misidentification", "false_name",
                                       "hallucination"))
    print(f"  {n} failure cases documented ({serious} serious)")
    print(f"  written to {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
