"""
Turn real footage into ground truth, so Step 17's numbers can come from video.

    # 1. See what the detector finds, and how long labelling will take
    python -m backend.eval.label --video office.mp4 --inspect

    # 2. Extract signatures once (slow: runs pose + OSNet over the video)
    python -m backend.eval.label --video office.mp4 --extract

    # 3. Label who is who
    python -m backend.eval.label --video office.mp4 --annotate

    # 4. Measure, with the same code the synthetic run uses
    python -m backend.eval.evaluate --truth backend/eval/results/truth_office.json

WHY LABELLING IS BY TRACK, NOT BY FRAME

The plan says "hand-label 30-60 min of footage", which sounds like thousands of
decisions. It is not, if the tool is built right. ByteTrack already groups
detections into fragments; a 40-minute recording of five people produces on the
order of 50-200 fragments. The labeller answers one question per fragment —
"who is this?" — and the answer propagates to every frame in it.

That is also the honest division of labour. A human is good at recognising a
person in a thumbnail and bad at deciding whether frame 8,412 is SITTING; the
detector is the reverse. So the human supplies identity and the pipeline
supplies posture and zone, each doing the part they are actually reliable at.

WHAT THIS DELIBERATELY DOES NOT DO

It does not label posture or zone by hand. Those come from the same
`spatial_engine` and pose classifier the live pipeline uses. Hand-labelling
them would measure the labeller's patience rather than the system, and would
make the chair-exit metric circular — the debounce would be graded against a
human's opinion of when somebody stood up, which is exactly the judgement the
debounce exists to replace.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Two roots on the path, deliberately:
#   the repo root, so `backend.eval.*` resolves when run as a module;
#   backend/,      so `app.cv.*` resolves the same way it does inside the
#                  running server, which imports `app.` not `backend.app.`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from backend.eval.groundtruth import (GroundTruth, Observation,   # noqa: E402
                                      SAMPLE_SECONDS, SOURCE_LABELLED)

RESULTS = os.path.join(os.path.dirname(__file__), "results")
THUMBS = os.path.join(RESULTS, "thumbnails")


def _load_cv():
    """
    Imported lazily and reported clearly.

    These are heavy (torch, ultralytics, OSNet) and only this command needs
    them — `evaluate.py` must stay runnable on a machine that cannot load a
    GPU model.
    """
    try:
        import cv2
        # PostureEstimator, not PoseEstimator — the class is named for what it
        # outputs. Getting this wrong is a silent ImportError inside a lazy
        # loader, which is why it is checked here rather than assumed.
        from app.cv.pose_estimator import PostureEstimator
        from app.cv.appearance import AppearanceExtractor
        from app.cv.spatial_engine import SpatialEngine
        return cv2, PostureEstimator, AppearanceExtractor, SpatialEngine
    except Exception as e:
        print("Could not load the CV stack:", e)
        print("Labelling needs the same dependencies the pipeline uses.")
        raise SystemExit(1)


def inspect(video: str) -> int:
    """How much work is this video, before anybody commits to labelling it."""
    cv2, _PostureEstimator, _a, _s = _load_cv()

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        print(f"Could not open {video}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    print(f"  {os.path.basename(video)}")
    print(f"  {w}x{h}  {fps:.1f} fps  {n} frames  {n / fps / 60:.1f} min")
    print()
    if n / fps < 1800:
        print(f"  NOTE: the plan asks for 30-60 minutes; this is "
              f"{n / fps / 60:.1f}. Shorter footage gives a smaller sample and "
              f"wider error bars, which the report should say plainly.")
    print(f"  Labelling is per TRACK, not per frame — expect on the order of")
    print(f"  50-200 decisions rather than {int(n / fps / SAMPLE_SECONDS)} .")
    return 0


def extract(video: str, out_name: str, stride: int = 1,
            zones_config: list = None) -> int:
    """
    Run the real detector over the video and cache what it found.

    Slow and done once. Everything after this — labelling, evaluating,
    re-evaluating after a threshold change — reads the cache, so tuning a
    threshold does not mean re-running pose estimation.
    """
    cv2, PostureEstimator, AppearanceExtractor, SpatialEngine = _load_cv()

    os.makedirs(RESULTS, exist_ok=True)
    os.makedirs(THUMBS, exist_ok=True)

    pose = PostureEstimator()
    appearance = AppearanceExtractor()
    # Zones come from the same engine the live pipeline uses, so a labelled
    # recording is scored against the same geometry production would apply.
    # Without a zones file every detection is TRANSIT_ZONE, which is honest:
    # desk time cannot be evaluated on footage whose desks were never drawn.
    spatial = SpatialEngine(zones_config=zones_config or [])
    if not (zones_config or []):
        print("  NOTE: no --zones supplied, so every detection is TRANSIT_ZONE.")
        print("  Seat binding, desk time and chair exits cannot be measured")
        print("  from this recording; identification and stitching still can.")

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        print(f"Could not open {video}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    frames = []                 # (t, camera_id, batch) — the runner's shape
    tracks = {}                 # track_id -> {first_t, last_t, n, thumb}
    camera_id = os.path.splitext(os.path.basename(video))[0]

    idx = 0
    kept = 0
    print(f"  extracting from {os.path.basename(video)} ...")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if stride > 1 and idx % stride:
            continue

        t = idx / fps
        detections = pose.process_frame_single_pass(frame)
        if not detections:
            frames.append((round(t, 2), camera_id, []))
            continue

        sigs = appearance.extract_batch(frame, detections)
        batch = []
        for det, sig in zip(detections, sigs):
            tid = det.get("track_id")
            if tid is None:
                continue
            sig["track_id"] = tid
            # The zone is computed, not labelled — see the module docstring on
            # why hand-labelling geometry would make the chair-exit metric
            # circular.
            zone = spatial.check_zone_containment(
                SpatialEngine.ground_point(det.get("bbox", [0, 0, 0, 0])))
            if zone == "TRANSIT_ZONE":
                zone = None
            # A person object is not known yet — labelling supplies it. None
            # here keeps the tuple shape the runner expects.
            batch.append((None, tid, sig, zone,
                          det.get("posture") or "SITTING"))

            info = tracks.setdefault(tid, {"first_t": t, "last_t": t, "n": 0,
                                           "thumb": None})
            info["last_t"] = t
            info["n"] += 1
            # One thumbnail per track, taken from its largest crop — the
            # clearest view is the one a labeller can actually recognise.
            x1, y1, x2, y2 = [int(v) for v in det.get("bbox", (0, 0, 0, 0))]
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > info.get("best_area", 0):
                info["best_area"] = area
                crop = frame[max(0, y1):max(1, y2), max(0, x1):max(1, x2)]
                if crop.size:
                    path = os.path.join(THUMBS, f"{camera_id}_track{tid}.jpg")
                    cv2.imwrite(path, crop)
                    info["thumb"] = path

        frames.append((round(t, 2), camera_id, batch))
        kept += 1
        if kept % 200 == 0:
            print(f"    {t:.0f}s  {len(tracks)} tracks so far")

    cap.release()

    frames_path = os.path.join(RESULTS, f"frames_{out_name}.pkl")
    with open(frames_path, "wb") as f:
        pickle.dump(frames, f)

    manifest = {
        "video": os.path.abspath(video),
        "camera_id": camera_id,
        "fps": fps,
        "duration_seconds": round(idx / fps, 1),
        "frames_path": frames_path,
        "tracks": {str(k): {kk: vv for kk, vv in v.items() if kk != "best_area"}
                   for k, v in sorted(tracks.items())},
    }
    man_path = os.path.join(RESULTS, f"tracks_{out_name}.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)

    print()
    print(f"  {len(frames)} frames, {len(tracks)} tracks")
    print(f"  signatures -> {frames_path}")
    print(f"  track list -> {man_path}")
    print(f"  thumbnails -> {THUMBS}")
    print()
    print(f"  Next: python -m backend.eval.label --video {video} --annotate")
    return 0


def annotate(video: str, out_name: str) -> int:
    """
    Ask who each track is, once per track, and write the ground truth.

    Console-driven on purpose: a labelling GUI is a project of its own, and the
    thumbnail path printed beside each question is enough to open the image in
    whatever viewer the labeller already has.
    """
    man_path = os.path.join(RESULTS, f"tracks_{out_name}.json")
    if not os.path.exists(man_path):
        print(f"No extraction found. Run --extract first.")
        return 1

    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)

    frames_path = manifest["frames_path"]
    with open(frames_path, "rb") as f:
        frames = pickle.load(f)

    tracks = manifest["tracks"]
    print(f"  {len(tracks)} tracks to label.")
    print()
    print("  For each one, enter:")
    print("    an employee id  (e.g. emp-a)     — a known employee")
    print("    ?  or blank                      — a person who is NOT enrolled")
    print("    x                                — not a person / bad detection")
    print("    s                                — stop and save what is done")
    print()

    labels = {}
    for tid, info in sorted(tracks.items(), key=lambda kv: int(kv[0])):
        dur = info["last_t"] - info["first_t"]
        print(f"  track {tid}: {info['first_t']:.0f}s -> {info['last_t']:.0f}s "
              f"({dur:.0f}s, {info['n']} detections)")
        if info.get("thumb"):
            print(f"    {info['thumb']}")
        ans = input("    who? ").strip()
        if ans.lower() == "s":
            break
        if ans.lower() == "x":
            labels[tid] = "__discard__"
        elif ans in ("", "?"):
            labels[tid] = None          # present, but not an employee
        else:
            labels[tid] = ans

    # Seat map, so the seat prior can be evaluated at all.
    print()
    print("  Desk assignments (blank to finish). Format:  emp-a desk_1")
    seat_map = {}
    while True:
        line = input("    ").strip()
        if not line:
            break
        parts = line.split()
        if len(parts) == 2:
            seat_map[parts[1]] = parts[0]

    gt = GroundTruth(
        source=SOURCE_LABELLED,
        description=f"Hand-labelled: {os.path.basename(video)}",
        duration_seconds=manifest["duration_seconds"],
        seat_map=seat_map,
        camera_roles={manifest["camera_id"]: "AREA"},
        notes={"video": manifest["video"],
               "frames_path": frames_path,
               "labelled_tracks": len(labels)},
    )

    for (t, cam, batch) in frames:
        if abs(t % SAMPLE_SECONDS) >= 0.25:
            continue
        for (_p, tid, _sig, zone, posture) in batch:
            key = str(tid)
            if key not in labels or labels[key] == "__discard__":
                continue
            emp = labels[key]
            gt.observations.append(Observation(
                t=t, employee_id=emp, camera_id=cam, zone_id=zone,
                posture=posture, track_id=tid,
                truth_identity=emp or f"person-{tid}"))

    path = os.path.join(RESULTS, f"truth_{out_name}.json")
    gt.save(path)
    print()
    print(f"  {gt.summary()}")
    print(f"  written to {path}")
    print()
    print(f"  Next: python -m backend.eval.evaluate --truth {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Label real footage for Step 17")
    ap.add_argument("--video", required=True)
    ap.add_argument("--name", help="output name (default: the video's basename)")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--stride", type=int, default=1,
                    help="process every Nth frame (default every frame)")
    ap.add_argument("--zones", help="path to a zones JSON (as the pipeline "
                                    "loads it) so desk time can be measured")
    args = ap.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.video))[0]

    if args.inspect:
        return inspect(args.video)
    if args.extract:
        zones = None
        if args.zones:
            with open(args.zones, encoding="utf-8") as f:
                zones = json.load(f)
        return extract(args.video, name, stride=args.stride, zones_config=zones)
    if args.annotate:
        return annotate(args.video, name)

    print("Choose one of --inspect, --extract, --annotate")
    return 1


if __name__ == "__main__":
    sys.exit(main())
