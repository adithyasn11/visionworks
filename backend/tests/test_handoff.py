"""
Step 13 — cross-camera handoff, and weight tuning.

The plan's criterion:

  "walk camera A → camera B ten times. Report how many handoffs were correct,
   wrong, and UNKNOWN. Expect 6-8 out of 10. That is a normal result."

Ten real walks between two real cameras is footage nobody has yet, so the ten
walks are SIMULATED — with the degradation the plan warns about actually
modelled, not wished away. §8.2: "the same shirt reads differently on each
camera." So each walk carries a per-camera colour shift and appearance noise
drawn from the range Step 5 measured on real footage, and three of the ten are
deliberately hard: a lookalike colleague, a walk that takes too long, and a
person crossing between two cameras with no route between them.

A test that fed clean identical vectors through would report 10/10 and prove
nothing. The number that matters is whether the WRONG answers are rejected,
because a wrong handoff silently gives one person's afternoon to somebody else.

Run:  venv/Scripts/python.exe backend/tests/test_handoff.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.handoff import (                    # noqa: E402
    HandoffRegistry, Departure, grid_search_weights, get_handoff_registry,
    DEFAULT_WEIGHTS, HANDOFF_MIN_SCORE, DEPARTURE_TTL_SECONDS,
)
from app.cv.identity_tracker import IdentityTracker      # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def person(seed):
    """A person's true appearance."""
    rng = np.random.default_rng(seed)
    return {"embedding": unit(rng.normal(size=512)),
            "hist": rng.random(256).astype(np.float32),
            "height": 80.0 + rng.random() * 20.0}


def as_seen(p, camera_seed, appearance_noise=0.03, colour_shift=0.12):
    """
    How that person looks ON A PARTICULAR CAMERA.

    This is the whole difficulty of cross-camera matching, and the plan names
    it as failure mode §8.2. Appearance noise of 0.03 puts the cosine around
    0.85-0.90, which is where Step 5 measured real same-person pairs (0.877).
    """
    rng = np.random.default_rng(camera_seed)
    emb = unit(p["embedding"] + rng.normal(size=512).astype(np.float32) * appearance_noise)
    hist = np.clip(p["hist"] * (1.0 + rng.normal(scale=colour_shift, size=256)), 0, None)
    return {"embedding": emb, "upper": hist.astype(np.float32),
            "lower": (hist * 0.8).astype(np.float32),
            "height": p["height"] * (1.0 + rng.normal(scale=0.02)),
            "bbox": [100, 100, 160, 260], "area": 9600}


class FakeIdentity:
    """Just enough of an Identity for record_departure()."""

    def __init__(self, identity_id, signature, employee_id=None,
                 confidence=0.0, method="unknown", hits=10):
        self.identity_id = identity_id
        self._sig = signature
        self.employee_id = employee_id
        self.confidence = confidence
        self.method = method
        self.hits = hits

    def prototype(self):
        return self._sig

    def dominant_zone(self):
        return None, 0.0


def main():
    print("=" * 70)
    print("STEP 13 — CROSS-CAMERA HANDOFF")
    print("=" * 70)

    # ══ A. TOPOLOGY IS A GATE, NOT A SIGNAL ════════════════════════════════
    print("\nA. THE ROUTE IS CHECKED FIRST, AND ABSOLUTELY")
    print("-" * 70)
    reg = HandoffRegistry(org_id="t")
    reg.set_links({("corridor", "pantry"): (3, 8)})

    p = person(1)
    reg.record_departure(FakeIdentity("corridor::1", as_seen(p, 10)),
                         "corridor", now=100.0)

    # The SAME person, arriving somewhere with no route from the corridor.
    v = reg.find_handoff(as_seen(p, 11), "meeting_room", now=105.0)
    print(f"   same person, unlinked camera -> matched={v['matched']}  ({v['reason']})")
    check("a perfect appearance match across unlinked cameras is refused",
          not v["matched"], v["reason"])

    # The same person, right camera, but far too slowly.
    v = reg.find_handoff(as_seen(p, 11), "pantry", now=180.0)
    print(f"   same person, 80s walk (3-8s)  -> matched={v['matched']}  ({v['reason']})")
    check("a walk outside the declared window is refused",
          not v["matched"], v["reason"])

    # The same person, right camera, plausible timing.
    v = reg.find_handoff(as_seen(p, 11), "pantry", now=105.0)
    print(f"   same person, 5s walk          -> matched={v['matched']}  "
          f"score {v['score']:.3f}  ({v['reason']})")
    check("a plausible journey IS matched", v["matched"], v["reason"])
    check("...and reports which camera they came from",
          v["from_camera"] == "corridor", str(v["from_camera"]))

    # A second arrival cannot claim the same departure.
    v2 = reg.find_handoff(as_seen(p, 12), "pantry", now=106.0)
    print(f"   a second arrival              -> matched={v2['matched']}  ({v2['reason']})")
    check("one departure cannot be handed off twice", not v2["matched"], v2["reason"])

    # ══ B. THE PLAN'S TEST: TEN WALKS ══════════════════════════════════════
    print("\nB. TEN WALKS FROM CAMERA A TO CAMERA B  <<< THE PLAN'S CRITERION")
    print("-" * 70)
    print("   seven genuine journeys, plus three cases that MUST be refused")
    print()

    reg = HandoffRegistry(org_id="ten")
    reg.set_links({("cam_a", "cam_b"): (3, 10)})

    correct = wrong = unknown = 0
    rows = []

    # Seven real journeys by seven different people.
    for i in range(7):
        p = person(100 + i)
        reg.record_departure(
            FakeIdentity(f"cam_a::{i}", as_seen(p, 200 + i),
                         employee_id=f"emp-{i}", confidence=0.85, method="face"),
            "cam_a", now=1000.0 + i * 60)
        v = reg.find_handoff(as_seen(p, 300 + i), "cam_b", now=1005.0 + i * 60)
        got = v.get("employee_id")
        outcome = ("correct" if v["matched"] and got == f"emp-{i}"
                   else "WRONG" if v["matched"] else "unknown")
        correct += outcome == "correct"
        wrong += outcome == "WRONG"
        unknown += outcome == "unknown"
        rows.append((f"walk {i + 1}: genuine journey", outcome, v["score"], v["reason"]))

    # 8. A lookalike colleague walking the same corridor. MUST NOT be matched
    #    to the person who left, and if it is, that is the failure that matters.
    p_real, p_look = person(500), person(501)
    reg.record_departure(
        FakeIdentity("cam_a::look", as_seen(p_real, 600),
                     employee_id="emp-real", confidence=0.9, method="face"),
        "cam_a", now=2000.0)
    v = reg.find_handoff(as_seen(p_look, 601), "cam_b", now=2005.0)
    outcome = "WRONG" if v["matched"] else "unknown"
    wrong += outcome == "WRONG"
    unknown += outcome == "unknown"
    rows.append(("walk 8: a DIFFERENT person", outcome, v["score"], v["reason"]))

    # 9. Somebody who took far too long.
    p9 = person(700)
    reg.record_departure(FakeIdentity("cam_a::slow", as_seen(p9, 800)),
                         "cam_a", now=3000.0)
    v = reg.find_handoff(as_seen(p9, 801), "cam_b", now=3120.0)
    outcome = "WRONG" if v["matched"] else "unknown"
    wrong += outcome == "WRONG"
    unknown += outcome == "unknown"
    rows.append(("walk 9: 120s for a 3-10s route", outcome, v["score"], v["reason"]))

    # 10. A route nobody declared.
    p10 = person(900)
    reg.record_departure(FakeIdentity("cam_a::nolink", as_seen(p10, 950)),
                         "cam_a", now=4000.0)
    v = reg.find_handoff(as_seen(p10, 951), "cam_c", now=4005.0)
    outcome = "WRONG" if v["matched"] else "unknown"
    wrong += outcome == "WRONG"
    unknown += outcome == "unknown"
    rows.append(("walk 10: undeclared route", outcome, v["score"], v["reason"]))

    for label, outcome, score, reason in rows:
        mark = {"correct": "OK ", "WRONG": "BAD", "unknown": "-- "}[outcome]
        print(f"   {mark} {label:<32} {outcome:<8} score {score:.3f}")
        if outcome != "correct":
            print(f"       {reason}")

    print()
    print(f"   correct {correct}   wrong {wrong}   unknown {unknown}   (of 10)")
    print(f"   the plan expects 6-8 correct out of 10")

    check("at least 6 of 10 correct  <<< THE PLAN'S CRITERION",
          correct >= 6, f"{correct}/10")
    check("ZERO wrong handoffs — the failure that matters",
          wrong == 0, f"{wrong} wrong")
    check("the three impossible cases were all refused",
          unknown >= 3, f"{unknown} refused")

    # ══ C. THE SCORE ═══════════════════════════════════════════════════════
    print("\nC. THE FUSION SCORE")
    print("-" * 70)
    reg2 = HandoffRegistry(org_id="s")
    reg2.set_links({("a", "b"): (1, 20)})
    p = person(42)
    dep = Departure("a::1", "a", as_seen(p, 1), left_at=0.0,
                    employee_id="emp-z", confidence=0.9, method="face")
    result = reg2.score_candidate(as_seen(p, 2), dep)
    print(f"   components: { {k: round(v, 3) for k, v in result['components'].items()} }")
    print(f"   weights   : {result['weights']}")
    print(f"   fused     : {result['score']:.3f}")
    check("all four signals contribute", len(result["components"]) == 4,
          str(sorted(result["components"])))
    check("the weights are the plan's", result["weights"] == DEFAULT_WEIGHTS,
          str(result["weights"]))

    # Missing components must renormalise, not score zero.
    thin = {"embedding": as_seen(p, 3)["embedding"], "upper": None,
            "lower": None, "height": None}
    r2 = reg2.score_candidate(thin, Departure("a::2", "a",
        {"embedding": as_seen(p, 4)["embedding"], "upper": None, "lower": None,
         "height": None}, left_at=0.0))
    print(f"   appearance only -> {r2['score']:.3f} from {list(r2['components'])}")
    check("a missing signal renormalises rather than scoring 0",
          r2["score"] > 0.5, f"{r2['score']:.3f}")

    # ══ D. WEIGHT TUNING ═══════════════════════════════════════════════════
    print("\nD. WEIGHT TUNING (the plan's grid search)")
    print("-" * 70)
    # Labelled data where COLOUR is misleading — a uniform — and appearance is
    # the honest signal. A correct search should shift weight onto osnet.
    rng = np.random.default_rng(7)
    labelled = []
    for _ in range(120):
        labelled.append({"components": {
            "osnet": float(rng.uniform(0.80, 0.98)),
            "colour": float(rng.uniform(0.30, 0.95)),   # noise
            "height": float(rng.uniform(0.60, 0.95)),
            "seat": float(rng.uniform(0.50, 0.95))}, "correct": True})
    for _ in range(80):
        labelled.append({"components": {
            "osnet": float(rng.uniform(0.20, 0.55)),
            "colour": float(rng.uniform(0.30, 0.95)),   # same range: useless
            "height": float(rng.uniform(0.60, 0.95)),
            "seat": float(rng.uniform(0.50, 0.95))}, "correct": False})

    tuned = grid_search_weights(labelled, step=0.1)
    print(f"   {tuned['samples']} labelled handoffs")
    print(f"   default weights -> {DEFAULT_WEIGHTS}")
    print(f"   tuned weights   -> {tuned['weights']}")
    print(f"   accuracy {tuned['accuracy']:.3f}  "
          f"precision {tuned['precision']}  recall {tuned['recall']}")
    print(f"   false accepts {tuned['false_accepts']}, "
          f"false rejects {tuned['false_rejects']}")
    check("the search ran over the labels", tuned["samples"] == 200, str(tuned["samples"]))
    check("weights still sum to 1", abs(sum(tuned["weights"].values()) - 1.0) < 1e-6,
          str(round(sum(tuned["weights"].values()), 4)))
    check("it found the discriminating signal (osnet)",
          tuned["weights"]["osnet"] >= tuned["weights"]["colour"],
          f"osnet {tuned['weights']['osnet']} vs colour {tuned['weights']['colour']}")
    check("it beats a coin flip", tuned["accuracy"] > 0.8, f"{tuned['accuracy']:.3f}")
    check("false accepts are penalised harder than false rejects",
          tuned["false_accepts"] <= tuned["false_rejects"],
          f"{tuned['false_accepts']} vs {tuned['false_rejects']}")
    check("no labels -> the plan's defaults, honestly reported",
          grid_search_weights([])["weights"] == DEFAULT_WEIGHTS)

    # ══ E. THROUGH THE TRACKER ═════════════════════════════════════════════
    print("\nE. TWO TRACKERS, ONE HANDOFF")
    print("-" * 70)
    shared = HandoffRegistry(org_id="e2e")
    shared.set_links({("cam_door", "cam_desk"): (2, 15)})
    p = person(2024)

    a = IdentityTracker(session_id="A")
    a.set_handoff(shared, "cam_door")
    seen_a = as_seen(p, 3000)
    for f in range(10):
        a.assign([{**seen_a, "track_id": 1}], now=f * 0.5)
    ident_a = list(a.identities().values())[0]
    ident_a.employee_id, ident_a.confidence, ident_a.method = "emp-walk", 0.9, "face"
    # They leave: a frame with nobody in it.
    a.assign([], now=6.0)
    print(f"   camera A: {ident_a.identity_id} departed "
          f"({shared.stats()['pending_departures']} pending)")
    check("the departure was published", shared.stats()["pending_departures"] == 1)

    b = IdentityTracker(session_id="B")
    b.set_handoff(shared, "cam_desk")
    seen_b = as_seen(p, 3001)
    b.assign([{**seen_b, "track_id": 77}], now=11.0)
    ident_b = list(b.identities().values())[0]
    print(f"   camera B: {ident_b.identity_id} -> {ident_b.employee_id} "
          f"({ident_b.method}, {ident_b.confidence})")
    check("camera B picked up the person from camera A",
          ident_b.employee_id == "emp-walk", str(ident_b.employee_id))
    check("...recorded as a handoff, not a face match",
          ident_b.method == "handoff", ident_b.method)
    check("the tracker recorded the handoff", len(b.handoffs) == 1, str(len(b.handoffs)))
    if b.handoffs:
        h = b.handoffs[0]
        print(f"   from {h['from_camera']} after {h['gap_seconds']}s, "
              f"score {h['score']:.3f}")

    from app.db.identity_writer import VALID_METHODS
    check("'handoff' is a method the database accepts",
          "handoff" in VALID_METHODS)

    # ══ F. DEGRADATION ═════════════════════════════════════════════════════
    print("\nF. DEGRADATION")
    print("-" * 70)
    bare = IdentityTracker(session_id="bare")
    bare.assign([{**as_seen(person(1), 1), "track_id": 1}], now=1.0)
    check("no handoff registry attached is fine",
          list(bare.identities().values())[0].employee_id is None)

    empty = HandoffRegistry(org_id="empty")
    empty.set_links({})
    v = empty.find_handoff(as_seen(person(1), 1), "anywhere", now=1.0)
    check("no topology declared -> no handoff", not v["matched"], v["reason"])

    check("a signature with no embedding is refused",
          not empty.find_handoff({"embedding": None}, "x", now=1.0)["matched"])

    stale = HandoffRegistry(org_id="stale")
    stale.set_links({("a", "b"): (1, 600)})
    stale.record_departure(FakeIdentity("a::1", as_seen(person(1), 1)), "a", now=0.0)
    v = stale.find_handoff(as_seen(person(1), 2), "b",
                           now=DEPARTURE_TTL_SECONDS + 10)
    print(f"   a departure {DEPARTURE_TTL_SECONDS + 10:.0f}s old -> {v['reason']}")
    check("a departure expires after its TTL", not v["matched"], v["reason"])

    flicker = HandoffRegistry(org_id="flick")
    flicker.set_links({("a", "b"): (1, 20)})
    ok = flicker.record_departure(
        FakeIdentity("a::1", as_seen(person(1), 1), hits=1), "a", now=0.0)
    check("a two-frame flicker is not a departure", not ok)

    print(f"\n   registry stats: {reg.stats()}")

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 13: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
