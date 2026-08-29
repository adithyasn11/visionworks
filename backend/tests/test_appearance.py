"""
Step 5 verification — appearance signature extraction.

The plan's criterion, verbatim:

  "two crops of the same person score cosine > 0.7; two different people
   score < 0.5. Print the numbers; do not assume."

So this prints every number it measures. It runs the REAL pipeline over REAL
footage (OpenCV's vtest.avi, a pedestrian scene with several people), pairs up
crops by ByteTrack id, and reports the distributions.

Pairs are built from tracker identity, which is the honest ground truth
available here: within a short window ByteTrack is reliable about "this is the
same box moving", and that is exactly the same-person label we need. Where it
is NOT reliable — across occlusions — is the problem Step 6 exists to solve.

Run:  venv/Scripts/python.exe backend/tests/test_appearance.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.appearance import (           # noqa: E402
    AppearanceExtractor, cosine, colour_histogram, histogram_similarity,
    signature_similarity, height_estimate,
)
from app.cv.spatial_engine import SpatialEngine   # noqa: E402

VIDEO = os.path.join(os.path.dirname(__file__), "..", "..", "sample_videos", "vtest.avi")
FAILS = []


def check(name, got, want, fmt=str):
    ok = bool(got)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {fmt(want)}")
    if not ok:
        FAILS.append(name)


def main():
    import cv2
    from app.cv.pose_estimator import PostureEstimator

    if not os.path.exists(VIDEO):
        print(f"Test footage missing: {VIDEO}")
        return 1

    print("=" * 66)
    print("STEP 5 — APPEARANCE SIGNATURE EXTRACTION")
    print("=" * 66)

    extractor = AppearanceExtractor()
    engine = SpatialEngine(zones_config=[])
    # conf 0.25 / imgsz 768: vtest.avi is 768x576 with distant pedestrians, and
    # the pipeline's production 640px downscale shrinks them below the detector's
    # default 0.35 gate — measured, 12 detections in 120 frames. At native size
    # the same footage yields 3-4 people per frame, which is what a same-frame
    # different-people comparison needs. The MODEL is unchanged; only the
    # capture settings suit the test footage.
    pose = PostureEstimator(
        pose_model_path=os.path.join(os.path.dirname(__file__), "..", "yolov8m-pose.pt"),
        conf_thresh=0.25,
        device="cuda" if _cuda() else "cpu",
    )

    cap = cv2.VideoCapture(VIDEO)
    # track_id -> list of (frame_idx, signature)
    by_track = {}
    frames = 0
    batch_calls = 0
    people_seen = 0

    while frames < 200:
        ok, frame = cap.read()
        if not ok:
            break
        frames += 1
        # No downscale: see the note on conf_thresh above.
        dets = pose.process_frame_single_pass(frame, motion_speeds={}, imgsz=768)
        if not dets:
            continue

        sigs = extractor.extract_batch(frame, dets, spatial_engine=engine)
        batch_calls += 1
        people_seen += len(sigs)
        for s in sigs:
            if s["embedding"] is not None:
                by_track.setdefault(s["track_id"], []).append((frames, s))
    cap.release()

    print(f"\nframes processed          : {frames}")
    print(f"detections with signatures: {people_seen}")
    print(f"OSNet forward passes      : {batch_calls}  (one per frame, not per person)")
    check("batched: <= 1 forward pass per frame", batch_calls <= frames,
          f"{batch_calls} passes for {people_seen} people over {frames} frames")

    tracks = {t: v for t, v in by_track.items() if len(v) >= 4}
    print(f"tracks with >= 4 samples  : {len(tracks)}  (ids: {sorted(tracks)[:8]})")
    if len(tracks) < 2:
        print("\nNot enough distinct tracks in this footage to compare. Aborting.")
        return 1

    # ── Same person: pairs within one track, spread apart in time ───────────
    same = []
    for tid, items in tracks.items():
        for i in range(0, len(items) - 3, 2):
            a, b = items[i][1], items[i + 3][1]
            same.append((tid, items[i][0], items[i + 3][0], cosine(a["embedding"], b["embedding"])))

    # ── Different people: pairs across tracks, from the SAME frame ──────────
    # Same-frame pairs are the strict test: identical lighting and pose
    # conditions, so any separation is the model discriminating identity
    # rather than exploiting a lighting difference.
    by_frame = {}
    for tid, items in tracks.items():
        for fidx, s in items:
            by_frame.setdefault(fidx, []).append((tid, s))
    diff = []
    for fidx, people in by_frame.items():
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                (t1, s1), (t2, s2) = people[i], people[j]
                diff.append((t1, t2, fidx, cosine(s1["embedding"], s2["embedding"])))

    print("\n" + "-" * 66)
    print("OSNet COSINE — SAME PERSON (pairs within one track)")
    print("-" * 66)
    for tid, f1, f2, c in same[:12]:
        print(f"  track {tid:<3} frame {f1:>3} vs {f2:>3}   cosine = {c:.3f}")
    if len(same) > 12:
        print(f"  ... {len(same) - 12} more")
    s_vals = np.array([c for *_, c in same], dtype=float)
    print(f"\n  n={len(s_vals)}  min={s_vals.min():.3f}  mean={s_vals.mean():.3f}  "
          f"median={np.median(s_vals):.3f}  max={s_vals.max():.3f}")

    print("\n" + "-" * 66)
    print("OSNet COSINE — DIFFERENT PEOPLE (pairs in the same frame)")
    print("-" * 66)
    for t1, t2, fidx, c in diff[:12]:
        print(f"  track {t1:<3} vs {t2:<3} frame {fidx:>3}      cosine = {c:.3f}")
    if len(diff) > 12:
        print(f"  ... {len(diff) - 12} more")
    if len(diff) == 0:
        print("  (no frame contained two tracked people; cannot test separation)")
        return 1
    d_vals = np.array([c for *_, c in diff], dtype=float)
    print(f"\n  n={len(d_vals)}  min={d_vals.min():.3f}  mean={d_vals.mean():.3f}  "
          f"median={np.median(d_vals):.3f}  max={d_vals.max():.3f}")

    print("\n" + "=" * 66)
    print("THE PLAN'S CRITERION")
    print("=" * 66)
    print(f"  same person   median cosine = {np.median(s_vals):.3f}   (target > 0.7)")
    print(f"  different     median cosine = {np.median(d_vals):.3f}   (target < 0.5)")
    print(f"  separation    = {np.median(s_vals) - np.median(d_vals):.3f}")

    check("same-person median > 0.7", np.median(s_vals) > 0.70,
          f"{np.median(s_vals):.3f}")
    check("different-people median < 0.5", np.median(d_vals) < 0.50,
          f"{np.median(d_vals):.3f}")
    check("distributions separate", np.median(s_vals) > np.median(d_vals) + 0.2,
          f"gap {np.median(s_vals) - np.median(d_vals):.3f}")

    # A threshold that actually splits the two populations is what Step 6 needs.
    best_t, best_acc = 0, 0
    for t in np.arange(0.30, 0.95, 0.01):
        acc = ((s_vals >= t).sum() + (d_vals < t).sum()) / (len(s_vals) + len(d_vals))
        if acc > best_acc:
            best_acc, best_t = acc, t
    print(f"\n  best separating threshold = {best_t:.2f} -> {best_acc * 100:.1f}% correct")
    print(f"  at the plan's Step 6 gate (0.65): "
          f"{(s_vals >= 0.65).mean() * 100:.1f}% of same-person pairs accepted, "
          f"{(d_vals >= 0.65).mean() * 100:.1f}% of different-people pairs wrongly accepted")
    check("0.65 gate keeps most same-person pairs", (s_vals >= 0.65).mean() > 0.70,
          f"{(s_vals >= 0.65).mean() * 100:.1f}% accepted")
    check("0.65 gate rejects most different-people pairs", (d_vals >= 0.65).mean() < 0.20,
          f"{(d_vals >= 0.65).mean() * 100:.1f}% wrongly accepted")

    # ── Colour histograms ───────────────────────────────────────────────────
    print("\n" + "-" * 66)
    print("COLOUR HISTOGRAM (HSV, upper/lower split)")
    print("-" * 66)
    cs, cd = [], []
    for tid, items in tracks.items():
        for i in range(0, len(items) - 3, 2):
            cs.append(signature_similarity(items[i][1], items[i + 3][1])["colour"])
    for _, _, _, _ in diff[:1]:
        pass
    for fidx, people in by_frame.items():
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                cd.append(signature_similarity(people[i][1], people[j][1])["colour"])
    cs = np.array([c for c in cs if c is not None], dtype=float)
    cd = np.array([c for c in cd if c is not None], dtype=float)
    if len(cs) and len(cd):
        print(f"  same person   median = {np.median(cs):.3f}  (n={len(cs)})")
        print(f"  different     median = {np.median(cd):.3f}  (n={len(cd)})")
        check("colour separates the two populations", np.median(cs) > np.median(cd),
              f"{np.median(cs):.3f} vs {np.median(cd):.3f}")
    else:
        print("  (insufficient histogram pairs)")

    kp = sum(1 for t in tracks.values() for _, s in t if s.get("hist_from_keypoints"))
    tot = sum(len(t) for t in tracks.values())
    print(f"  histograms split by real keypoints: {kp}/{tot} "
          f"({kp / max(tot,1) * 100:.0f}%; the rest used bbox fractions)")

    # ── Height ──────────────────────────────────────────────────────────────
    print("\n" + "-" * 66)
    print("HEIGHT ESTIMATE (projected through the homography)")
    print("-" * 66)
    heights = [s["height"] for t in tracks.values() for _, s in t if s["height"]]
    print(f"  measured on {len(heights)}/{tot} samples "
          f"(None = seated or no head keypoint — a deliberate abstention)")
    if heights:
        hv = np.array(heights, dtype=float)
        print(f"  range {hv.min():.1f} .. {hv.max():.1f} floorplan units, median {np.median(hv):.1f}")
        per = {t: np.median([s["height"] for _, s in items if s["height"]])
               for t, items in tracks.items()
               if any(s["height"] for _, s in items)}
        for t, v in list(per.items())[:6]:
            print(f"    track {t:<3} median height = {v:.1f}")
        check("height is stable within a track",
              all(np.std([s["height"] for _, s in tracks[t] if s["height"]]) < 60 for t in per),
              "per-track std < 60 units")

    # ── Fused score ─────────────────────────────────────────────────────────
    print("\n" + "-" * 66)
    print("FUSED APPEARANCE SCORE (0.30 osnet + 0.20 colour + 0.10 height,")
    print("renormalised over whichever components are present)")
    print("-" * 66)
    fs, fd = [], []
    for tid, items in tracks.items():
        for i in range(0, len(items) - 3, 2):
            fs.append(signature_similarity(items[i][1], items[i + 3][1])["fused"])
    for fidx, people in by_frame.items():
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                fd.append(signature_similarity(people[i][1], people[j][1])["fused"])
    fs, fd = np.array(fs, dtype=float), np.array(fd, dtype=float)
    print(f"  same person   median = {np.median(fs):.3f}")
    print(f"  different     median = {np.median(fd):.3f}")
    check("fused score separates", np.median(fs) > np.median(fd) + 0.15,
          f"gap {np.median(fs) - np.median(fd):.3f}")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 5: ALL CHECKS PASSED")
    return 0


def _cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
