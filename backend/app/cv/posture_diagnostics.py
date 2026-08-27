# backend/app/cv/posture_diagnostics.py
"""
Diagnostic harness for the posture classifier.

WHY THIS EXISTS

classify_posture_raw() returns a single string, which tells you WHAT it decided
and nothing about WHY. When it labels a standing person SITTING there is no way
to tell which of the eight scoring features fired, or whether the keypoints it
scored were even visible.

This runs the same classifier over a video and dumps the intermediate scores
per detection, so a misclassification can be attributed to a specific feature
rather than guessed at.

Usage:
    python -m app.cv.posture_diagnostics <video path> [--frames N] [--device cuda]
"""

import argparse
import sys
from collections import Counter

import numpy as np


# COCO keypoint indices used by the classifier, for readable output.
KP = {
    "nose": 0, "l_sh": 5, "r_sh": 6, "l_hip": 11, "r_hip": 12,
    "l_knee": 13, "r_knee": 14, "l_ank": 15, "r_ank": 16,
}


def describe_visibility(kpts: np.ndarray, thresh: float = 0.15) -> str:
    """Which of the joints the classifier depends on were actually seen."""
    conf = kpts[:, 2] if kpts.shape[1] >= 3 else np.ones(len(kpts))
    seen = [name for name, idx in KP.items() if conf[idx] > thresh]
    return ",".join(seen) if seen else "NONE"


def score_breakdown(estimator, kpts: np.ndarray, bbox: list, motion_speed: float) -> dict:
    """
    Re-derives the individual feature contributions.

    Deliberately mirrors classify_posture_raw rather than importing its
    internals: the classifier accumulates into two floats and discards the
    per-feature detail, so the only way to see the breakdown is to recompute it.
    Any change to the classifier must be mirrored here, which is why this lives
    beside it rather than in a test directory.
    """
    conf = kpts[:, 2] if kpts.shape[1] >= 3 else np.ones(len(kpts))
    ok = lambda i: conf[i] > 0.15

    def joint(i, j):
        if ok(i) and ok(j):
            return (kpts[i][:2] + kpts[j][:2]) / 2.0, True
        if ok(i):
            return kpts[i][:2], True
        if ok(j):
            return kpts[j][:2], True
        return None, False

    sh_mid, has_sh = joint(5, 6)
    hip_mid, has_hip = joint(11, 12)
    knee_mid, has_knee = joint(13, 14)
    ank_mid, has_ank = joint(15, 16)

    angle = estimator.calculate_angle

    l_hip_a = angle(kpts[5][:2], kpts[11][:2], kpts[13][:2]) if (ok(5) and ok(11) and ok(13)) else None
    r_hip_a = angle(kpts[6][:2], kpts[12][:2], kpts[14][:2]) if (ok(6) and ok(12) and ok(14)) else None
    l_knee_a = angle(kpts[11][:2], kpts[13][:2], kpts[15][:2]) if (ok(11) and ok(13) and ok(15)) else None
    r_knee_a = angle(kpts[12][:2], kpts[14][:2], kpts[16][:2]) if (ok(12) and ok(14) and ok(16)) else None

    hip_angles = [a for a in (l_hip_a, r_hip_a) if a is not None]
    knee_angles = [a for a in (l_knee_a, r_knee_a) if a is not None]

    torso_len = None
    if has_sh and has_hip:
        torso_len = float(np.linalg.norm(np.asarray(sh_mid) - np.asarray(hip_mid)))

    thigh_drop = None
    if torso_len and torso_len > 8.0 and has_knee:
        thigh_drop = (float(np.asarray(knee_mid)[1]) - float(np.asarray(hip_mid)[1])) / torso_len

    hip_fold = max(hip_angles) if hip_angles else None

    return {
        "visible": describe_visibility(kpts),
        "hip_angles": [round(a, 1) for a in hip_angles],
        "knee_angles": [round(a, 1) for a in knee_angles],
        "hip_fold": round(hip_fold, 1) if hip_fold is not None else None,
        "thigh_drop": round(thigh_drop, 2) if thigh_drop is not None else None,
        "torso_len": round(torso_len, 1) if torso_len else None,
        "has_knee": has_knee,
        "has_ank": has_ank,
        "motion": round(motion_speed, 2),
    }


def run(video_path: str, max_frames: int = 120, device: str = None):
    import cv2
    from app.cv.pose_estimator import PostureEstimator
    from app.cv.activity_aggregator import ActivityAggregator

    estimator = PostureEstimator(pose_model_path="yolov8m-pose.pt", conf_thresh=0.35, device=device)
    aggregator = ActivityAggregator()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"ERROR: cannot open {video_path}")
        return 1

    per_track = {}
    frame_idx = 0

    while frame_idx < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        h, w = frame.shape[:2]
        if w > 640:
            scale = 640.0 / w
            frame = cv2.resize(frame, (640, int(h * scale)))

        speeds = {t: aggregator.get_recent_motion_speed(t) for t in aggregator.track_history}
        dets = estimator.process_frame_single_pass(frame, motion_speeds=speeds, imgsz=480)

        for det in dets:
            tid = det["track_id"]
            bbox = det["bbox"]
            kpts = det["keypoints"]
            centroid = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
            aggregator.update_track(tid, centroid, det["posture"])

            raw = estimator.classify_posture_raw(kpts, bbox, motion_speed=speeds.get(tid, 0.0))
            info = score_breakdown(estimator, kpts, bbox, speeds.get(tid, 0.0))

            entry = per_track.setdefault(tid, {"raw": Counter(), "smoothed": Counter(), "samples": []})
            entry["raw"][raw] += 1
            entry["smoothed"][det["posture"]] += 1
            if len(entry["samples"]) < 3:
                entry["samples"].append((frame_idx, raw, det["posture"], info))

    cap.release()

    print(f"\n=== {frame_idx} frames, {len(per_track)} track ids ===\n")
    for tid in sorted(per_track):
        e = per_track[tid]
        print(f"TRACK {tid}  ({sum(e['raw'].values())} detections)")
        print(f"  raw      : {dict(e['raw'])}")
        print(f"  smoothed : {dict(e['smoothed'])}")
        for fno, raw, sm, info in e["samples"]:
            print(f"    f{fno:<4} raw={raw:<8} smoothed={sm:<8} "
                  f"hip_fold={info['hip_fold']} thigh_drop={info['thigh_drop']} "
                  f"knees={info['knee_angles']} motion={info['motion']}")
            print(f"          visible={info['visible']}")
        print()

    totals = Counter()
    for e in per_track.values():
        totals.update(e["smoothed"])
    print(f"OVERALL (smoothed): {dict(totals)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()
    sys.exit(run(args.video, args.frames, args.device))
