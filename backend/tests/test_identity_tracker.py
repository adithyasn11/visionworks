"""
Step 6 verification — fragment stitching (the ByteTrack fix).

The plan's criterion:

  "take a video where someone walks behind a pillar and returns. Confirm the
   ByteTrack id changes but the identity_id stays the same. Count raw ids vs.
   stitched identities — the ratio is your stitch-rate metric."

Real footage of a convenient pillar is not reproducible, so the occlusion is
SYNTHESISED on real footage: a black bar is painted over the frame for a stretch
long enough that ByteTrack gives up and issues a new id when the person
reappears. That is exactly the failure this step exists to fix, and making it
deterministic means the test means the same thing on every run.

The occlusion is applied to the FRAME, before detection — so the detector and
tracker genuinely lose the person, rather than the test faking a track break.

Run:  venv/Scripts/python.exe backend/tests/test_identity_tracker.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.appearance import AppearanceExtractor          # noqa: E402
from app.cv.identity_tracker import IdentityTracker        # noqa: E402
from app.cv.spatial_engine import SpatialEngine            # noqa: E402

VIDEO = os.path.join(os.path.dirname(__file__), "..", "..", "sample_videos", "vtest.avi")
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def _cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def run_with_occlusion(occlude_from, occlude_to, max_frames=200):
    """
    Play the footage, blacking out the whole frame between two indices.

    Returns the per-frame record of (frame, track_id -> identity_id) plus the
    tracker, so the caller can compare ids either side of the gap.
    """
    import cv2
    from app.cv.pose_estimator import PostureEstimator

    pose = PostureEstimator(
        pose_model_path=os.path.join(os.path.dirname(__file__), "..", "yolov8m-pose.pt"),
        conf_thresh=0.25,
        device="cuda" if _cuda() else "cpu",
    )
    extractor = AppearanceExtractor()
    engine = SpatialEngine(zones_config=[])
    tracker = IdentityTracker(session_id="steptest")

    cap = cv2.VideoCapture(VIDEO)
    timeline = []
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    f = 0
    while f < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        f += 1
        occluded = occlude_from <= f <= occlude_to
        if occluded:
            # Everything is hidden: the tracker must lose every track, which is
            # the honest version of "walked behind a pillar".
            frame = np.zeros_like(frame)

        dets = pose.process_frame_single_pass(frame, motion_speeds={}, imgsz=768)
        sigs = extractor.extract_batch(frame, dets, spatial_engine=engine)
        # Virtual clock from the video's own fps, so the 120 s gallery TTL and
        # the position gate are evaluated in VIDEO time, not wall-clock time.
        assigned = tracker.assign(sigs, now=f / fps)
        timeline.append((f, occluded, {t: a["identity_id"] for t, a in assigned.items()},
                         {t: a["reattached"] for t, a in assigned.items()}))
    cap.release()
    return timeline, tracker


def main():
    if not os.path.exists(VIDEO):
        print(f"Test footage missing: {VIDEO}")
        return 1

    print("=" * 68)
    print("STEP 6 — FRAGMENT STITCHING (the ByteTrack fix)")
    print("=" * 68)

    # ── A. Baseline: no occlusion, ids must stay put ────────────────────────
    print("\nA. BASELINE — clean footage, nothing should be stitched")
    print("-" * 68)
    timeline, tracker = run_with_occlusion(10**9, 10**9, max_frames=80)
    s = tracker.stats()
    print(f"   raw ByteTrack ids : {s['raw_track_ids']}")
    print(f"   identities        : {s['identities']}")
    print(f"   reattachments     : {s['reattachments']}")
    # NOT asserting 1:1 here. Measured on this footage, ByteTrack drops and
    # reissues ids even with no occlusion at all — tracks 1, 2 and 3 all die
    # within 35 frames while the people are still plainly walking. Demanding
    # one identity per raw id would be asserting that ByteTrack is reliable,
    # which is the very premise Step 6 exists to reject.
    #
    # What must hold on clean footage is the SAFETY property: fewer identities
    # than raw ids (merging happened) and never more (nothing was split).
    check("clean footage never invents extra identities",
          s["identities"] <= s["raw_track_ids"],
          f"{s['raw_track_ids']} ids -> {s['identities']} identities "
          f"({s['reattachments']} stitches of naturally-dropped tracks)")

    # Every track keeps ONE identity for its whole life.
    per_track = {}
    for _, _, ids, _ in timeline:
        for t, iid in ids.items():
            per_track.setdefault(t, set()).add(iid)
    unstable = {t: v for t, v in per_track.items() if len(v) > 1}
    check("no track ever changes identity mid-life", not unstable,
          f"{len(unstable)} unstable" if unstable else "all stable")

    # ── B. The plan's test: occlusion, then reappearance ────────────────────
    print("\nB. OCCLUSION TEST — the plan's 'walks behind a pillar and returns'")
    print("-" * 68)
    OCC_FROM, OCC_TO = 30, 48       # ~1.8 s at 10 fps: long enough for ByteTrack to give up
    print(f"   frames {OCC_FROM}-{OCC_TO} are blacked out before detection")
    timeline, tracker = run_with_occlusion(OCC_FROM, OCC_TO, max_frames=120)

    before = {}   # track_id -> identity_id, last frame before the occlusion
    after = {}    # track_id -> identity_id, first appearance after it
    for f, occluded, ids, _ in timeline:
        if f < OCC_FROM:
            for t, iid in ids.items():
                before[t] = iid
        elif f > OCC_TO:
            for t, iid in ids.items():
                after.setdefault(t, iid)

    ids_before = set(before)
    ids_after = set(after)
    print(f"   ByteTrack ids before : {sorted(ids_before)}")
    print(f"   ByteTrack ids after  : {sorted(ids_after)}")
    print(f"   identities before    : {sorted(set(before.values()))}")
    print(f"   identities after     : {sorted(set(after.values()))}")

    new_track_ids = ids_after - ids_before
    check("ByteTrack DID issue new ids after the occlusion",
          bool(new_track_ids),
          f"new raw ids: {sorted(new_track_ids)}")

    # The heart of the step: a NEW ByteTrack id carrying an identity that
    # existed before the occlusion is a successful stitch.
    identities_before = set(before.values())
    stitched = {t: iid for t, iid in after.items()
                if t in new_track_ids and iid in identities_before}
    print(f"\n   stitched (new raw id, OLD identity):")
    for t, iid in sorted(stitched.items()):
        old = [ot for ot, oi in before.items() if oi == iid]
        print(f"     ByteTrack {sorted(old)} -> {t}   identity {iid}  (PRESERVED)")
    if not stitched:
        print("     (none)")

    check("at least one identity survived the occlusion",
          bool(stitched),
          f"{len(stitched)} of {len(new_track_ids)} new raw ids reattached")

    reattach_events = sum(1 for _, _, _, r in timeline for v in r.values() if v)
    print(f"\n   reattachment events reported by the tracker: {reattach_events}")

    st = tracker.stats()
    print("\n" + "-" * 68)
    print("STITCH-RATE METRIC (the plan's headline number for this step)")
    print("-" * 68)
    print(f"   raw ByteTrack ids       : {st['raw_track_ids']}")
    print(f"   distinct identities     : {st['identities']}")
    print(f"   stitch rate (raw/ident) : {st['stitch_rate']:.2f}")
    print(f"   reattachments           : {st['reattachments']}")
    print(f"   rejected by appearance  : {st['rejected_by_appearance']}")
    print(f"   rejected by position    : {st['rejected_by_position']}")

    check("stitch rate > 1.0 (fragments were merged)",
          st["stitch_rate"] > 1.0,
          f"{st['stitch_rate']:.2f}")
    check("fewer identities than raw ids",
          st["identities"] < st["raw_track_ids"],
          f"{st['identities']} < {st['raw_track_ids']}")

    # ── C. The safety property: no two people merged ────────────────────────
    print("\nC. SAFETY — a wrong stitch is worse than a missed one")
    print("-" * 68)
    # Two ByteTrack ids alive in the SAME frame are, by construction, two
    # different people. They must never share an identity.
    collisions = []
    for f, _, ids, _ in timeline:
        seen = {}
        for t, iid in ids.items():
            if iid in seen:
                collisions.append((f, seen[iid], t, iid))
            seen[iid] = t
    check("no identity ever held by two people in one frame",
          not collisions,
          f"{len(collisions)} collisions" if collisions else "0 collisions")

    # ── D. Gates behave ─────────────────────────────────────────────────────
    print("\nD. THE GATES")
    print("-" * 68)
    from app.cv.identity_tracker import Identity

    tk = IdentityTracker(session_id="gate")
    ident = Identity("gate::1", 1, now=0.0)
    ident.last_bbox = [100, 100, 150, 250]
    ident.last_seen = 0.0

    ok_near, d_near, allow_near = tk._plausible_position(ident, [110, 100, 160, 250], now=0.5)
    ok_far, d_far, allow_far = tk._plausible_position(ident, [3000, 100, 3050, 250], now=0.5)
    print(f"   nearby reappearance : dist {d_near:6.1f} px, allowance {allow_near:6.1f} -> {'accept' if ok_near else 'reject'}")
    print(f"   teleport            : dist {d_far:6.1f} px, allowance {allow_far:6.1f} -> {'accept' if ok_far else 'reject'}")
    check("position gate accepts a plausible reappearance", ok_near)
    check("position gate rejects a teleport", not ok_far)

    # The appearance gate is the number Step 5 measured.
    print(f"   appearance gate     : cosine >= {tk.cosine_min} "
          f"(Step 5 measured 97.9% same-person accepted, 5.7% different wrongly accepted)")
    check("appearance gate is the plan's 0.65", abs(tk.cosine_min - 0.65) < 1e-9)

    # Gallery expiry: an identity gone longer than the TTL must not come back.
    tk2 = IdentityTracker(session_id="ttl", gallery_ttl=120.0)
    old = Identity("ttl::1", 1, now=0.0)
    old.hits = 10
    old.last_seen = 0.0
    tk2._lost["ttl::1"] = old
    tk2._expire(now=119.0)
    still = "ttl::1" in tk2._lost
    tk2._expire(now=121.0)
    gone = "ttl::1" not in tk2._lost
    print(f"   gallery TTL         : present at 119 s = {still}, expired at 121 s = {gone}")
    check("gallery expires exactly at the 120 s TTL", still and gone)

    # A tiny detection must not be allowed to reattach.
    tiny = {"track_id": 9, "embedding": np.ones(512, dtype=np.float32) / np.sqrt(512),
            "upper": None, "lower": None, "height": None,
            "bbox": [0, 0, 10, 10], "area": 100}
    m, _ = tk._match_lost(tiny, now=1.0)
    check("a tiny crop is never used to reattach", m is None,
          "area 100 px < 900 px floor")

    # ── E. Degradation ──────────────────────────────────────────────────────
    print("\nE. DEGRADATION — identity work must never break the pipeline")
    print("-" * 68)
    tk3 = IdentityTracker(session_id="deg")
    empty = tk3.assign([], now=1.0)
    check("empty frame is handled", empty == {})

    no_emb = tk3.assign([{"track_id": 1, "embedding": None, "upper": None,
                          "lower": None, "height": None, "bbox": [0, 0, 50, 100],
                          "area": 5000}], now=2.0)
    check("a signature with no embedding still gets an identity",
          no_emb.get(1, {}).get("identity_id") is not None,
          no_emb.get(1, {}).get("identity_id"))

    off = IdentityTracker(session_id="off", enabled=False)
    res = off.assign([{"track_id": 7, "embedding": None, "bbox": [0, 0, 9, 9], "area": 81}], now=1.0)
    check("disabled tracker falls back to 1:1 track ids",
          res[7]["identity_id"] == "off::7", res[7]["identity_id"])

    a = IdentityTracker(session_id="s1")
    b = IdentityTracker(session_id="s2")
    ra = a.assign([{"track_id": 3, "embedding": None, "bbox": [0, 0, 50, 90], "area": 4500}], now=1.0)
    rb = b.assign([{"track_id": 3, "embedding": None, "bbox": [0, 0, 50, 90], "area": 4500}], now=1.0)
    check("sessions never collide on track ids",
          ra[3]["identity_id"] != rb[3]["identity_id"],
          f"{ra[3]['identity_id']} vs {rb[3]['identity_id']}")

    # ── F. Everything is still UNKNOWN ──────────────────────────────────────
    print("\nF. NOTHING IS NAMED YET (Step 7 does the binding)")
    print("-" * 68)
    named = [i for i in tracker.identities().values() if i.employee_id is not None]
    check("every identity is still UNKNOWN", not named,
          f"{len(tracker.identities())} identities, 0 named")
    methods = {i.method for i in tracker.identities().values()}
    check("method is 'unknown' throughout", methods <= {"unknown"}, str(methods))

    print("\n" + "=" * 68)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 6: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
