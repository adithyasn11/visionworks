"""Per-class precision / recall of the PPE model at fixed confidence thresholds.

mAP hides the number that matters for safety enforcement: at the threshold we
will actually run, how often is a real helmet/vest missed (false violation) and
how often is something hallucinated (false compliance)?

Usage (from repo root, with the venv):
    venv\\Scripts\\python.exe ppe_training\\eval_ppe_model.py --split test
    venv\\Scripts\\python.exe ppe_training\\eval_ppe_model.py --split valid --out reports.json

Matching is class-aware greedy IoU >= 0.5, highest-confidence prediction first.
It also reports, for every missed ground-truth box, what the model predicted
there instead (the confusion that the Ultralytics matrix summarises).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "ppe_training" / "datasets" / "ppe6_roboflow"
WEIGHTS = ROOT / "ppe_training" / "runs" / "ppe_yolov8m_v1" / "weights" / "best.pt"
NAMES = ["boots", "gloves", "helmet", "mask", "person", "safety_vest"]
THRESHOLDS = (0.25, 0.35, 0.50, 0.65)
IOU_MATCH = 0.5


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def load_gt(label_path: Path, w: int, h: int):
    boxes = []
    if not label_path.is_file():
        return boxes
    for line in label_path.read_text().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        c = int(p[0])
        cx, cy, bw, bh = (float(x) for x in p[1:5])
        boxes.append((c, [(cx - bw / 2) * w, (cy - bh / 2) * h,
                          (cx + bw / 2) * w, (cy + bh / 2) * h]))
    return boxes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["train", "valid", "test"])
    ap.add_argument("--weights", default=str(WEIGHTS))
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None,
                    help="text file of image file names to keep (e.g. reports/ppe6_test_deduplicated.txt "
                         "from check_leakage.py); others are skipped")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    ds = Path(args.dataset)
    images = sorted((ds / args.split / "images").glob("*.*"))
    if args.only:
        keep = {l.strip() for l in Path(args.only).read_text(encoding="utf-8").splitlines() if l.strip()}
        images = [i for i in images if i.name in keep]
    print(f"[INFO] {len(images)} images in {args.split}")

    # One pass at a very low conf; thresholds are applied afterwards.
    per_thr = {t: {c: Counter() for c in range(len(NAMES))} for t in THRESHOLDS}
    miss_as = {t: defaultdict(Counter) for t in THRESHOLDS}
    gt_count = Counter()

    for img in images:
        res = model.predict(str(img), conf=0.10, iou=0.6, verbose=False,
                            device=0)[0]
        h, w = res.orig_shape
        gt = load_gt(ds / args.split / "labels" / (img.stem + ".txt"), w, h)
        preds = []
        if res.boxes is not None and len(res.boxes):
            for b, c, s in zip(res.boxes.xyxy.cpu().numpy(),
                               res.boxes.cls.cpu().numpy().astype(int),
                               res.boxes.conf.cpu().numpy()):
                preds.append((int(c), float(s), b.tolist()))
        for c, _ in gt:
            gt_count[c] += 1

        for t in THRESHOLDS:
            ps = sorted([p for p in preds if p[1] >= t], key=lambda p: -p[1])
            used = set()
            for pc, _, pb in ps:
                best, bi = 0.0, None
                for gi, (gc, gb) in enumerate(gt):
                    if gi in used or gc != pc:
                        continue
                    v = iou(pb, gb)
                    if v > best:
                        best, bi = v, gi
                if bi is not None and best >= IOU_MATCH:
                    used.add(bi)
                    per_thr[t][pc]["tp"] += 1
                else:
                    per_thr[t][pc]["fp"] += 1
            for gi, (gc, gb) in enumerate(gt):
                if gi in used:
                    continue
                per_thr[t][gc]["fn"] += 1
                # what did the model say at this location instead?
                other = [(pc, iou(pb, gb)) for pc, _, pb in ps
                         if pc != gc and iou(pb, gb) >= IOU_MATCH]
                label = (NAMES[max(other, key=lambda o: o[1])[0]]
                         if other else "nothing")
                miss_as[t][NAMES[gc]][label] += 1

    report = {"split": args.split, "images": len(images),
              "gt_instances": {NAMES[c]: n for c, n in gt_count.items()},
              "thresholds": {}}
    for t in THRESHOLDS:
        rows = {}
        for c, name in enumerate(NAMES):
            tp, fp, fn = (per_thr[t][c][k] for k in ("tp", "fp", "fn"))
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * p * r / (p + r) if p + r else 0.0
            rows[name] = {"tp": tp, "fp": fp, "fn": fn,
                          "precision": round(p, 3), "recall": round(r, 3),
                          "f1": round(f1, 3),
                          "missed_as": dict(miss_as[t][name])}
        report["thresholds"][str(t)] = rows

    print(f"\nGT instances: {report['gt_instances']}")
    for t in THRESHOLDS:
        print(f"\n=== conf >= {t} ===")
        print(f"{'class':12s} {'P':>6s} {'R':>6s} {'F1':>6s}  tp/fp/fn   missed as")
        for name in NAMES:
            r = report["thresholds"][str(t)][name]
            print(f"{name:12s} {r['precision']:6.3f} {r['recall']:6.3f} "
                  f"{r['f1']:6.3f}  {r['tp']}/{r['fp']}/{r['fn']}   {r['missed_as']}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\n[INFO] wrote {args.out}")


if __name__ == "__main__":
    main()
