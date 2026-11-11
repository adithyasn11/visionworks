"""
The evaluation code itself (Step 17):

  A. The PPE benchmark's scoring (ppe_training/eval_segments.py): the three systems, the outcome of a verdict against the
     label, the rule of three for a zero, coverage and error rate, on hand-made frame readings.
  B. The scenario suite (backend/eval/safety_scenarios.py): every scenario, two variants each, through the real engine;
     none may be wrong. (The full run with 18 variants writes the results file; this one writes nothing.)
  C. GET /api/v1/safety/evaluation: who may read it, and what it returns when a results file is there or missing.

    venv/Scripts/python.exe backend/tests/test_evaluation.py
"""
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "ppe_training"))
sys.path.insert(0, os.path.join(BACKEND, "eval"))

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {str(detail)[:200]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ── A. scoring ───────────────────────────────────────────────────────────────
print("A. The PPE benchmark's scoring")
import eval_segments as es                                           # noqa: E402

seen = [[0.8, True]] * 45                    # the item seen in every frame
gone = [[0.0, True]] * 45                    # nothing there in any frame
flicker = [[0.8, True] if i % 2 else [0.0, True] for i in range(45)]
small = [[0.0, False]] * 45                  # never a clear frame (far away)
first_missed = [[0.0, True]] + [[0.8, True]] * 44

check("B0 decides from ONE frame: a helmet missed in frame 0 is a violation even if seen in all the rest", es.b0(first_missed) == "violation" and es.b0(seen) == "compliant")
check("B1 is a majority over the window, never abstains", es.b1(seen, 30) == "compliant" and es.b1(gone, 30) == "violation" and es.b1(small, 30) == "violation")
check("ours: seen -> compliant, gone -> violation", es.ours(seen, 30) == "compliant" and es.ours(gone, 30) == "violation")
check("ours: a 50/50 flicker is neither 60 % seen nor 90 % gone -> abstain", es.ours(flicker, 30) == "abstain")
check("ours: no clear frame (far away) -> abstain, where B1 would accuse", es.ours(small, 30) == "abstain")
check("ours: one missed frame among many seen is not an accusation", es.ours(first_missed, 30) == "compliant")
check("outcomes against the label", es.outcome("PRESENT", "violation") == "false_accusation" and es.outcome("ABSENT", "compliant") == "false_compliance"
      and es.outcome("ABSENT", "violation") == "caught" and es.outcome("PRESENT", "compliant") == "cleared" and es.outcome("PRESENT", "abstain") == "abstained")
segs = [{"truth": "PRESENT", "frames": seen}] * 100 + [{"truth": "ABSENT", "frames": gone}] * 50 + [{"truth": "PRESENT", "frames": small}] * 10
r = es.score(segs, lambda f: es.ours(f, 30))
check("score: 160 segments, 100 cleared, 50 caught, 10 abstained, coverage 150/160, no error",
      (r["n"], r["cleared"], r["caught"], r["abstained"], r["coverage"], r["error_rate"]) == (160, 100, 50, 10, round(150 / 160, 4), 0.0), str(r))
check("a zero is reported with the rule of three: 0 false accusations of 110 worn -> at most 3/110",
      r["false_accusation"] == 0 and r["false_accusation_upper95"] == round(3 / 110, 4), str(r))
r1 = es.score(segs, lambda f: es.b1(f, 30))
check("B1 on the same set accuses the 10 far-away workers who were wearing it", r1["false_accusation"] == 10 and r1["abstained"] == 0 and r1["coverage"] == 1.0, str(r1))
check("a non-zero count gets the Wilson upper bound (above the observed rate)", r1["false_accusation_upper95"] > r1["false_accusation_rate"])

# ── B. the scenario suite, two variants each ────────────────────────────────
print("B. The scenario suite (two variants each)")
import safety_scenarios as ss                                        # noqa: E402

work = tempfile.mkdtemp(prefix="eval_test_")
try:
    for sid, title, expectation, under_test, make in ss.scenarios():
        outcomes = []
        for v in range(2):
            fps = (10, 25)[v]
            script, rules, seconds, judge = make(random.Random(f"test-{sid}-{v}"), fps)
            dec, ev = ss.run(script, rules, seconds, fps, work)
            outcomes.append(judge(dec, ev)[:2])
        check(f"{sid} {title}: never wrong", all(o[0] != "wrong" for o in outcomes), str(outcomes))
finally:
    shutil.rmtree(work, ignore_errors=True)

# ── C. the endpoint ─────────────────────────────────────────────────────────
print("C. GET /api/v1/safety/evaluation")
from fastapi.testclient import TestClient                           # noqa: E402

import app.api.routers.safety as sr                                  # noqa: E402
import app.api.routers.safety_report as srr                          # noqa: E402
from app.main import app                                             # noqa: E402

real_resolve = sr.resolve_identity
sr.resolve_identity = lambda token: {"Bearer viewer": ("o1", "u1", "VIEWER"), "Bearer norole": ("o1", "u1", None)}.get(token, (None, None, None))
saved = dict(srr.EVALUATION_FILES)
tmp = tempfile.mkdtemp(prefix="eval_api_")
try:
    json.dump({"totals": {"correct": 3, "abstained": 0, "wrong": 0, "runs": 3}, "scenarios": [], "config": {"x": 1}}, open(os.path.join(tmp, "s.json"), "w"))
    srr.EVALUATION_FILES.update({"scenarios": os.path.join(tmp, "s.json"), "real_footage": os.path.join(tmp, "missing.json"), "segments": os.path.join(tmp, "bad.json")})
    open(os.path.join(tmp, "bad.json"), "w").write("{not json")
    c = TestClient(app)
    check("no token: 401", c.get("/api/v1/safety/evaluation").status_code == 401)
    check("no role: 403", c.get("/api/v1/safety/evaluation", headers={"Authorization": "Bearer norole"}).status_code == 403)
    r = c.get("/api/v1/safety/evaluation", headers={"Authorization": "Bearer viewer"})
    d = r.json()
    check("a VIEWER reads it; a results file that is there comes back (with when it was made, without the config dump)",
          r.status_code == 200 and d["scenarios"]["totals"]["correct"] == 3 and "_file_updated" in d["scenarios"] and "config" not in d["scenarios"], str(d)[:200])
    check("a missing or unreadable results file is null, not an error", d["real_footage"] is None and d["segments"] is None)
finally:
    srr.EVALUATION_FILES.clear()
    srr.EVALUATION_FILES.update(saved)
    sr.resolve_identity = real_resolve
    shutil.rmtree(tmp, ignore_errors=True)
check("the real results files are where the endpoint looks", all(os.path.dirname(p) and os.path.isdir(os.path.dirname(p)) for p in srr.EVALUATION_FILES.values()))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
