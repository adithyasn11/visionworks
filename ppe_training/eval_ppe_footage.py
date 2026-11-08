"""Measure the PPE pipeline on YOUR OWN footage.

The Roboflow test split says how the model does on Roboflow-style photos. This
says how the whole chain (person tracking -> helmet/vest detection ->
association -> temporal verdict) does on the cameras and room you will demo in.

HOW TO RECORD
  Film each worker for ~10 s, one worker per segment, whole body visible,
  facing roughly the camera. Do several segments of each kind:
    fully equipped, no helmet, no vest, neither.
  Vary distance (near / mid / far), lighting, and turn sideways once or twice.

LABEL FILE (CSV, header required; seconds into the video; 1 = worn, 0 = not)
    start_s,end_s,helmet,vest
    0,10,1,1
    12,22,0,1
    24,34,1,0

RUN
    venv\\Scripts\\python.exe ppe_training\\eval_ppe_footage.py --video demo.mp4 --labels demo.csv

OUTPUT
  * per-frame helmet/vest recall and false-detection rate at the detector
    threshold (tells you if the MODEL works on your footage),
  * per-segment verdict vs truth (tells you if the VERDICT LOGIC works).
  The number that matters most: NON_COMPLIANT on a worker who WAS equipped
  (a false accusation). It should be 0.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.cv.ppe_evidence import (  # noqa: E402
    HELMET, VEST, COMPLIANT, NON_COMPLIANT, UNVERIFIABLE,
    PpeEvidenceTracker, associate_frame,
)

DET_THRESHOLD = 0.35


def load_labels(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"start": float(r["start_s"]), "end": float(r["end_s"]),
                         "helmet": int(r["helmet"]), "vest": int(r["vest"])})
    return rows


def expected(seg):
    return COMPLIANT if seg["helmet"] and seg["vest"] else NON_COMPLIANT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--person-model", default=str(ROOT / "backend" / "yolov8m.pt"))
    ap.add_argument("--width", type=int, default=960, help="resize frames to this width")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO
    from app.cv.ppe_detector import PpeDetector

    segs = load_labels(args.labels)
    persons_model = YOLO(args.person_model)
    ppe = PpeDetector()
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    frame_stats = {HELMET: Counter(), VEST: Counter()}
    trackers = [PpeEvidenceTracker() for _ in segs]
    clear_by_seg = [Counter() for _ in segs]
    last_t = [0.0] * len(segs)

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        idx += 1
        si = next((i for i, s in enumerate(segs) if s["start"] <= t < s["end"]), None)
        if si is None:
            continue
        h, w = frame.shape[:2]
        if w > args.width:
            frame = cv2.resize(frame, (args.width, int(h * args.width / w)))

        r = persons_model.track(frame, persist=True, classes=[0], conf=0.35,
                                tracker="bytetrack.yaml", verbose=False)[0]
        persons = []
        if r.boxes is not None and r.boxes.id is not None:
            for b, tid in zip(r.boxes.xyxy.cpu().numpy(),
                              r.boxes.id.cpu().numpy().astype(int)):
                persons.append({"track_id": int(tid), "bbox": [float(v) for v in b]})
        if not persons:
            continue

        assoc = associate_frame(persons, ppe.detect(frame), frame.shape)
        seg = segs[si]
        for p in persons:
            a = assoc[p["track_id"]]
            trackers[si].update(p["track_id"], a, t)
            if a["clear"]:
                clear_by_seg[si][p["track_id"]] += 1
        last_t[si] = t

        # Per-frame detector quality, on the largest clear person only
        # (segments are one-worker-each by construction).
        main_p = max(persons, key=lambda q: (q["bbox"][2] - q["bbox"][0])
                     * (q["bbox"][3] - q["bbox"][1]))
        a = assoc[main_p["track_id"]]
        if a["clear"]:
            for item, truth in ((HELMET, seg["helmet"]), (VEST, seg["vest"])):
                hit = a[item] >= DET_THRESHOLD
                key = ("tp" if hit else "fn") if truth else ("fp" if hit else "tn")
                frame_stats[item][key] += 1
    cap.release()

    print("\nPER-FRAME DETECTOR QUALITY (clear frames, conf >= %.2f)" % DET_THRESHOLD)
    summary = {"frames": {}, "segments": []}
    for item in (HELMET, VEST):
        c = frame_stats[item]
        rec = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
        fpr = c["fp"] / (c["fp"] + c["tn"]) if c["fp"] + c["tn"] else None
        print(f"  {item:12s} recall={rec if rec is None else round(rec,3)}  "
              f"false-detection rate={fpr if fpr is None else round(fpr,3)}  {dict(c)}")
        summary["frames"][item] = {"recall": rec, "false_detection_rate": fpr, **c}

    print("\nPER-SEGMENT VERDICT")
    outcome = Counter()
    for i, seg in enumerate(segs):
        if not clear_by_seg[i]:
            verdict = {"status": UNVERIFIABLE, "reason": "no clear person in segment"}
        else:
            tid = clear_by_seg[i].most_common(1)[0][0]
            verdict = trackers[i].status(tid, last_t[i])
        exp = expected(seg)
        got = verdict["status"]
        if got == exp:
            tag = "correct"
        elif got == UNVERIFIABLE:
            tag = "abstained"
        elif got == NON_COMPLIANT and exp == COMPLIANT:
            tag = "FALSE ACCUSATION"
        else:
            tag = "FALSE COMPLIANCE"
        outcome[tag] += 1
        print(f"  {seg['start']:6.1f}-{seg['end']:6.1f}s  truth={exp:14s} "
              f"got={got:14s} {tag}")
        summary["segments"].append({**seg, "expected": exp, "got": got, "tag": tag,
                                    "reason": verdict.get("reason")})
    print(f"\n  {dict(outcome)}")
    if outcome["FALSE ACCUSATION"] or outcome["FALSE COMPLIANCE"]:
        print("  >>> at least one wrong verdict: look at the reason strings in --out")
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
