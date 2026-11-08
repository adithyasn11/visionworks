"""
PPE association and temporal evidence.

What matters is not that a worker WITH a helmet is called compliant. It is that
nobody is ever accused on weak evidence: a distant worker, a flickering weak
detection, a crowded frame and an ordinary detector miss must all come back
UNVERIFIABLE (or COMPLIANT where the evidence really is there), never
NON_COMPLIANT. The last section measures that over simulated workers using the
detector's MEASURED test-split recall.

Run:  venv/Scripts/python.exe backend/tests/test_ppe_evidence.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.ppe_evidence import (                       # noqa: E402
    associate_ppe, associate_frame, frame_clarity, PpeEvidenceTracker,
    HELMET, VEST, COMPLIANT, NON_COMPLIANT, UNVERIFIABLE,
)

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


PERSON = [200, 100, 300, 400]           # 100 x 300 px, clear


def helmet_box(person=PERSON, conf=0.9):
    cx = (person[0] + person[2]) / 2
    return {"cls": HELMET, "conf": conf, "bbox": [cx - 15, person[1] + 5, cx + 15, person[1] + 40]}


def vest_box(person=PERSON, conf=0.9):
    return {"cls": VEST, "conf": conf,
            "bbox": [person[0] + 10, person[1] + 60, person[2] - 10, person[1] + 190]}


def feed(tracker, key, obs_list, t0=0.0, dt=0.1):
    for i, o in enumerate(obs_list):
        tracker.update(key, o, t0 + i * dt)
    return t0 + len(obs_list) * dt


def obs(helmet=0.0, vest=0.0, clear=True):
    return {HELMET: helmet, VEST: vest, "clear": clear}


def main():
    print("=" * 70)
    print("PPE ASSOCIATION")
    print("=" * 70)

    a = {"track_id": 1, "bbox": PERSON}
    b = {"track_id": 2, "bbox": [500, 100, 600, 400]}
    r = associate_ppe([a, b], [helmet_box(PERSON), vest_box(PERSON)])
    check("helmet and vest go to the person wearing them",
          r[1][HELMET] > 0 and r[1][VEST] > 0)
    check("the neighbour gets nothing",
          r[2][HELMET] == 0 and r[2][VEST] == 0)

    # One helmet over two overlapping people must not make both compliant.
    c = {"track_id": 3, "bbox": [230, 100, 330, 400]}
    r = associate_ppe([a, c], [helmet_box(PERSON)])
    given = [t for t in (1, 3) if r[t][HELMET] > 0]
    check("one helmet is given to exactly one of two overlapping people",
          len(given) == 1, f"given to {given}")

    low_helmet = {"cls": HELMET, "conf": 0.9, "bbox": [240, 300, 260, 330]}
    r = associate_ppe([a], [low_helmet])
    check("a 'helmet' at hip height (a held hard hat) is not worn",
          r[1][HELMET] == 0)

    far_vest = {"cls": VEST, "conf": 0.9, "bbox": [600, 150, 700, 300]}
    r = associate_ppe([a], [far_vest])
    check("a vest outside the person box is ignored", r[1][VEST] == 0)

    print("\n" + "=" * 70)
    print("CLARITY GATE")
    print("=" * 70)
    check("a normal person is clear", frame_clarity(PERSON, []))
    check("a distant person (50 px) is NOT clear",
          not frame_clarity([200, 100, 220, 150], []))
    check("head cut off at the frame top is NOT clear",
          not frame_clarity([200, 0, 300, 300], []))
    check("a person overlapped by another is NOT clear",
          not frame_clarity(PERSON, [[230, 100, 330, 400]]))
    rr = associate_frame([a, c], [])
    check("associate_frame marks both members of a crowd unclear",
          not rr[1]["clear"] and not rr[3]["clear"])

    print("\n" + "=" * 70)
    print("TEMPORAL VERDICTS")
    print("=" * 70)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.9, 0.9)] * 30)
    check("helmet + vest in every clear frame -> COMPLIANT",
          t.status("w", now)["status"] == COMPLIANT)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.0, 0.9)] * 30)
    s = t.status("w", now)
    check("vest but nothing on the head, clearly seen -> NON_COMPLIANT",
          s["status"] == NON_COMPLIANT and s["items"][HELMET]["state"] == "ABSENT", s["reason"])

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.0, 0.0, clear=False)] * 30)
    check("a worker only ever seen from too far away -> UNVERIFIABLE, not a violation",
          t.status("w", now)["status"] == UNVERIFIABLE)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.0, 0.9)] * 5)
    check("too few clear frames -> UNVERIFIABLE",
          t.status("w", now)["status"] == UNVERIFIABLE)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.30, 0.9)] * 30)
    check("a helmet flickering at 0.30 confidence -> UNVERIFIABLE, not a violation",
          t.status("w", now)["status"] == UNVERIFIABLE)

    t = PpeEvidenceTracker()
    seq = [obs(0.9, 0.9)] * 12 + [obs(0.0, 0.9)] * 18
    now = feed(t, "w", seq)
    check("helmet in 40% of frames only -> not COMPLIANT and not NON_COMPLIANT",
          t.status("w", now)["status"] == UNVERIFIABLE)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.9, 0.9)] * 30)
    check("evidence goes stale",
          t.status("w", now + 60)["status"] == UNVERIFIABLE)

    t = PpeEvidenceTracker()
    now = feed(t, "w", [obs(0.9, 0.9, clear=False)] * 15 + [obs(0.0, 0.9)] * 15)
    check("unclear frames do not count toward either side",
          t.status("w", now)["items"][HELMET]["clear_frames"] == 15)

    t = PpeEvidenceTracker(required=(HELMET,))
    now = feed(t, "w", [obs(0.9, 0.0)] * 30)
    check("a rule requiring only a helmet ignores the vest",
          t.status("w", now)["status"] == COMPLIANT)

    print("\n" + "=" * 70)
    print("SIMULATED WORKERS (detector recall/precision from the test split)")
    print("=" * 70)
    # Measured at conf 0.35: helmet recall 0.955, vest recall 0.877. False
    # detections where nothing is worn: modelled at 6% per frame per item,
    # at a spread of confidences. Frames are drawn independently, which is
    # OPTIMISTIC (real misses are correlated across frames), so this is a
    # floor on the error rates, not a promise.
    rng = random.Random(7)

    def detect(worn, recall):
        if worn:
            if rng.random() < recall:
                return rng.uniform(0.5, 0.95)
            return rng.uniform(0.0, 0.18)
        if rng.random() < 0.06:
            return rng.uniform(0.2, 0.7)
        return 0.0

    def run(helmet_worn, vest_worn, n=2000):
        counts = {COMPLIANT: 0, NON_COMPLIANT: 0, UNVERIFIABLE: 0}
        for i in range(n):
            tr = PpeEvidenceTracker()
            seq = [obs(detect(helmet_worn, 0.955), detect(vest_worn, 0.877),
                       clear=rng.random() < 0.85) for _ in range(30)]
            now = feed(tr, i, seq)
            counts[tr.status(i, now)["status"]] += 1
        return {k: v / n for k, v in counts.items()}

    ok = run(True, True)
    print(f"   fully equipped   : {ok}")
    check("fully equipped workers are called COMPLIANT >= 90% of the time",
          ok[COMPLIANT] >= 0.90)
    check("fully equipped workers are falsely ACCUSED < 0.5% of the time",
          ok[NON_COMPLIANT] < 0.005, f"{ok[NON_COMPLIANT]:.4f}")

    nh = run(False, True)
    print(f"   no helmet        : {nh}")
    check("a worker with no helmet is NEVER called COMPLIANT",
          nh[COMPLIANT] == 0.0)
    check("a worker with no helmet is flagged NON_COMPLIANT >= 80% of the time",
          nh[NON_COMPLIANT] >= 0.80, f"{nh[NON_COMPLIANT]:.3f}")

    nv = run(True, False)
    print(f"   no vest          : {nv}")
    check("a worker with no vest is NEVER called COMPLIANT", nv[COMPLIANT] == 0.0)

    nn = run(False, False)
    print(f"   neither          : {nn}")
    check("a worker with neither is NEVER called COMPLIANT", nn[COMPLIANT] == 0.0)

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("PPE EVIDENCE: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
