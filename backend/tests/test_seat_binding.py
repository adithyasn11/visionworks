"""
Step 7 verification — seat-assignment binding.

The plan's criterion:

  "with 3 employees at 3 desks, confirm each is bound correctly and the
   reported confidence is sensible."

Real footage of three named colleagues at three known desks is not something a
test can conjure, so the SPATIAL half is synthesised precisely — three people
who each sit in their own zone — while the binding logic under test is the real
thing. Part D then runs the rule over genuine video to confirm it behaves on
messy input too.

"Sensible confidence" is the part worth being strict about. Confidence here is
not a tuned score: it IS the fraction of observed time spent at that desk, so a
person who sat still all session must report ~1.0 and a person who wandered
must report proportionally less. A test that only checked "is it bound" would
pass on a system that returned 0.99 for everyone.

Run:  venv/Scripts/python.exe backend/tests/test_seat_binding.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.identity_tracker import (      # noqa: E402
    IdentityTracker, SEAT_BIND_MIN_FRACTION, SEAT_BIND_MIN_SECONDS,
)

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def sig(track_id, bbox, seed):
    """A stable, distinct signature per person, so stitching does not merge them."""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=512).astype(np.float32)
    v /= np.linalg.norm(v)
    return {"track_id": track_id, "embedding": v, "upper": None, "lower": None,
            "height": None, "bbox": bbox, "area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])}


DESKS = {
    "desk_1": [100, 100, 160, 260],
    "desk_2": [300, 100, 360, 260],
    "desk_3": [500, 100, 560, 260],
}
SEAT_MAP = {"desk_1": ["emp-prajwal"], "desk_2": ["emp-ravi"], "desk_3": ["emp-anita"]}


def run_session(zone_sequence, seat_map=SEAT_MAP, fps=10.0, tracker=None):
    """
    Drive the tracker with a scripted zone-per-person timeline.

    `zone_sequence` is {track_id: [zone_id per frame]}. None means "not in any
    zone" (walking the corridor), which is what makes the fraction meaningful.
    """
    tk = tracker or IdentityTracker(session_id="seat")
    tk.set_seat_map(seat_map)
    n = max(len(v) for v in zone_sequence.values())
    for f in range(n):
        sigs, zones = [], {}
        for tid, seq in zone_sequence.items():
            if f >= len(seq) or seq[f] == "GONE":
                continue
            zone = seq[f]
            bbox = DESKS.get(zone, [700, 100, 760, 260])
            sigs.append(sig(tid, bbox, seed=tid))
            zones[tid] = zone
        tk.assign(sigs, zone_by_track=zones, now=f / fps)
    return tk


def main():
    print("=" * 68)
    print("STEP 7 — SEAT-ASSIGNMENT BINDING")
    print("=" * 68)

    # ── A. The plan's test: 3 employees, 3 desks ────────────────────────────
    print("\nA. THREE EMPLOYEES AT THREE DESKS (the plan's criterion)")
    print("-" * 68)
    # 600 frames at 10 fps = 60 s each. Each person is at their own desk the
    # whole time except a short wander, so the fractions differ and a test that
    # returns a constant confidence cannot pass.
    seq = {
        1: ["desk_1"] * 600,                              # never leaves
        2: ["desk_2"] * 540 + [None] * 60,                # 90% at desk
        3: ["desk_3"] * 420 + [None] * 180,               # 70% at desk
    }
    tk = run_session(seq)
    report = tk.binding_report()

    print(f"   {'identity':<14} {'employee':<14} {'zone':<8} {'conf':>6} {'observed':>9}  reason")
    for r in report:
        print(f"   {r['identity_id']:<14} {str(r['employee_id']):<14} "
              f"{str(r['dominant_zone']):<8} {r['confidence']:>6.3f} "
              f"{r['observed_seconds']:>8.1f}s  {r['reason']}")

    bound = {r["employee_id"]: r for r in report if r["employee_id"]}
    check("all three employees bound", len(bound) == 3, f"{len(bound)}/3")
    check("each bound to the right desk",
          all(SEAT_MAP[r["dominant_zone"]][0] == r["employee_id"] for r in bound.values()),
          ", ".join(f"{r['employee_id']}@{r['dominant_zone']}" for r in bound.values()))
    check("bound by the seat prior, no biometrics",
          all(r["method"] == "seat" for r in bound.values()), "method=seat")

    print("\n   CONFIDENCE IS THE MEASURED FRACTION, not a fixed score:")
    expected = {"emp-prajwal": 1.00, "emp-ravi": 0.90, "emp-anita": 0.70}
    for emp, want in expected.items():
        got = bound[emp]["confidence"] if emp in bound else None
        ok = got is not None and abs(got - want) < 0.03
        print(f"     {emp:<14} expected ~{want:.2f}  got {got}")
        if not ok:
            FAILS.append(f"confidence for {emp}")
    check("confidences match time actually spent at the desk",
          all(emp in bound and abs(bound[emp]["confidence"] - w) < 0.03
              for emp, w in expected.items()),
          "within 0.03 of the true fraction")
    check("confidences are distinct (not a constant)",
          len({round(r['confidence'], 2) for r in bound.values()}) == 3,
          str(sorted(round(r["confidence"], 2) for r in bound.values())))

    # ── B. Abstention ───────────────────────────────────────────────────────
    print("\nB. WHEN IT MUST REFUSE TO BIND")
    print("-" * 68)

    # Never reaches the threshold: away first, so no early window ever qualifies.
    tk = run_session({1: [None] * 350 + ["desk_1"] * 250})
    r = tk.binding_report()[0]
    print(f"   42% at desk_1, away first -> {r['employee_id']}  ({r['reason']})")
    check("refuses when the fraction never exceeds 60%",
          r["employee_id"] is None, r["reason"])

    # The same 42%, but at the desk FIRST. This one DOES bind, and that is
    # correct: for the first 25 s the person really was 100% at that desk, and
    # the gates were genuinely satisfied. What must not happen is the
    # confidence continuing to claim 1.00 after they wandered off.
    #
    # The binding is deliberately not revoked — a person's morning must not
    # vanish retroactively because of how their afternoon went — but the number
    # falls to the truth, and migration 020 defines 0.6 as the line below which
    # the UI stops presenting a row as fact.
    tk = run_session({1: ["desk_1"] * 250 + [None] * 350})
    r = tk.binding_report()[0]
    print(f"   42% at desk_1, desk first -> {r['employee_id']}  "
          f"conf={r['confidence']:.3f}  ({r['reason']})")
    check("an early binding survives, but its confidence tells the truth",
          r["employee_id"] is not None and r["confidence"] < SEAT_BIND_MIN_FRACTION,
          f"bound with confidence {r['confidence']:.3f} < {SEAT_BIND_MIN_FRACTION}")
    check("the collapsed confidence is flagged as low",
          r["confidence"] < 0.6, f"{r['confidence']:.3f} < 0.6 -> UI shows low-confidence")

    # Too brief to mean anything, even at 100%.
    tk = run_session({1: ["desk_1"] * 50})     # 5 s at 10 fps
    r = tk.binding_report()[0]
    print(f"   100% of 5s    -> {r['employee_id']}  ({r['reason']})")
    check("refuses a brief pass-through even at 100%",
          r["employee_id"] is None, r["reason"])

    # Two employees claim one desk: the rule says EXACTLY one.
    tk = run_session({1: ["desk_1"] * 600},
                     seat_map={"desk_1": ["emp-a", "emp-b"]})
    r = tk.binding_report()[0]
    print(f"   2 claimants   -> {r['employee_id']}  ({r['reason']})")
    check("refuses when a zone has two claimants",
          r["employee_id"] is None, r["reason"])

    # Nobody assigned to that zone.
    tk = run_session({1: ["desk_9"] * 600}, seat_map={"desk_1": ["emp-a"]})
    r = tk.binding_report()[0]
    print(f"   unassigned    -> {r['employee_id']}  ({r['reason']})")
    check("refuses in a zone with no employee", r["employee_id"] is None, r["reason"])

    # Never inside any zone at all.
    tk = run_session({1: [None] * 600})
    r = tk.binding_report()[0]
    print(f"   never in zone -> {r['employee_id']}  ({r['reason']})")
    check("refuses when never inside a zone", r["employee_id"] is None, r["reason"])

    # No seat map configured at all.
    tk = IdentityTracker(session_id="nomap")
    for f in range(300):
        tk.assign([sig(1, DESKS["desk_1"], 1)], zone_by_track={1: "desk_1"}, now=f / 10.0)
    check("no seat map -> nothing is named",
          all(i.employee_id is None for i in tk.identities().values()), "")

    # ── C. One employee cannot be two identities ────────────────────────────
    print("\nC. ONE EMPLOYEE, ONE IDENTITY")
    print("-" * 68)
    # Two identities both sitting in desk_1 — what a failed stitch looks like.
    # Binding both would double-count that person's whole day.
    tk = run_session({1: ["desk_1"] * 600, 2: ["desk_1"] * 600})
    rep = tk.binding_report()
    claimed = [r for r in rep if r["employee_id"] == "emp-prajwal"]
    for r in rep:
        print(f"   {r['identity_id']:<14} -> {str(r['employee_id']):<14} {r['reason']}")
    check("an employee is bound to at most one identity",
          len(claimed) <= 1, f"{len(claimed)} identities claim emp-prajwal")

    # ── D. The rule on REAL video ───────────────────────────────────────────
    print("\nD. THE SAME RULE ON REAL FOOTAGE")
    print("-" * 68)
    video = os.path.join(os.path.dirname(__file__), "..", "..", "sample_videos", "vtest.avi")
    if not os.path.exists(video):
        print("   (footage missing, skipping)")
    else:
        import cv2
        from app.cv.pose_estimator import PostureEstimator
        from app.cv.appearance import AppearanceExtractor
        from app.cv.spatial_engine import SpatialEngine

        # A zone covering the left half of the frame, with one employee in it.
        zones = [{"zone_id": "left_desk", "zone_name": "Left",
                  "polygon": [[0, 0], [384, 0], [384, 576], [0, 576]],
                  "zone_type": "WORKSTATION"}]
        engine = SpatialEngine(zones_config=zones)
        pose = PostureEstimator(
            pose_model_path=os.path.join(os.path.dirname(__file__), "..", "yolov8m-pose.pt"),
            conf_thresh=0.25,
            device="cuda" if _cuda() else "cpu")
        ex = AppearanceExtractor()
        tk = IdentityTracker(session_id="real")
        tk.set_seat_map({"left_desk": ["emp-left"]})

        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
        for f in range(400):
            ok, frame = cap.read()
            if not ok:
                break
            dets = pose.process_frame_single_pass(frame, motion_speeds={}, imgsz=768)
            zbt = {}
            for d in dets:
                b = d["bbox"]
                zbt[d["track_id"]] = engine.check_zone_containment(
                    [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0])
            tk.assign(ex.extract_batch(frame, dets, spatial_engine=engine),
                      zone_by_track=zbt, now=f / fps)
        cap.release()

        rep = tk.binding_report()
        print(f"   {len(rep)} identities from real footage:")
        for r in rep[:8]:
            print(f"     {r['identity_id']:<12} zone={str(r['dominant_zone']):<14} "
                  f"{r['fraction']:.0%} of {r['observed_seconds']:>5.1f}s -> "
                  f"{str(r['employee_id'])}  ({r['reason']})")
        real_bound = [r for r in rep if r["employee_id"]]
        check("at most one identity bound to the single seated employee",
              len(real_bound) <= 1, f"{len(real_bound)} bound")
        for r in real_bound:
            check("a real binding reports a plausible confidence",
                  SEAT_BIND_MIN_FRACTION < r["confidence"] <= 1.0,
                  f"{r['confidence']:.3f}")
            check("a real binding rests on enough observation",
                  r["observed_seconds"] >= SEAT_BIND_MIN_SECONDS,
                  f"{r['observed_seconds']:.1f}s")
        # Nobody in TRANSIT_ZONE should ever be named.
        transit = [r for r in rep
                   if r["dominant_zone"] == "TRANSIT_ZONE" and r["employee_id"]]
        check("nobody in TRANSIT_ZONE is ever named", not transit,
              f"{len(transit)} wrongly named")

    print("\n" + "=" * 68)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 7: ALL CHECKS PASSED")
    return 0


def _cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
