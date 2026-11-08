"""
How often does the PPE detector's per-frame reading agree with explicit labels?

Uses the Ultralytics Construction-PPE images (download_construction_ppe.py),
which label not only helmets and vests but also heads and torsos that are
MISSING one (`no_helmet`, `no_vest`). Our own pipeline infers absence from "no
box found"; here there is a ground truth for it.

WHAT IS COMPARED (per person, per item: helmet and vest)

  ground truth   PRESENT  a labelled helmet / vest belongs to the person
                 ABSENT   a labelled no_helmet / no_vest belongs to the person
                 (people with neither label for an item are skipped for it)
  our reading    PRESENT  the best matching detection is >= 0.45
                 ABSENT   no detection at all above 0.20 matches the person
                 AMBIGUOUS in between   (the evidence layer treats this as "cannot tell")

Association uses the production code (`ppe_evidence.associate_ppe`), the same
head and torso rules, with the labelled person boxes standing in for the pose
model's boxes. Images are shrunk to the same width the live loop uses
(`ppe_max_width`, 1280) first.

WHAT THIS DOES NOT MEASURE

  * The TEMPORAL layer (a verdict needs 8+ clear frames over a window). A still
    image is one frame, so this is the per-frame reading that layer is built on:
    a per-frame accuse-on-absence rule would be baseline B0 in the design's
    comparison. The temporal layer's whole job is to do better than the numbers
    here.
  * Real CCTV. Part of this dataset is generated imagery, and the viewpoints are
    mostly close and level. Own footage is still required for the final results.

Run:  venv/Scripts/python.exe ppe_training/eval_construction_ppe.py [--limit N] [--split val|train|all]
Writes: ppe_training/reports/CONSTRUCTION_PPE_EVAL.md and .json
"""

import argparse
import collections
import glob
import json
import math
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backend")))

from app.cv.ppe_evidence import (                                  # noqa: E402
    PRESENT_CONF, WEAK_CONF, associate_frame, associate_ppe,
)
from app.cv.ppe_detector import PpeDetector                        # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402

ROOT = os.path.join(HERE, "datasets", "construction_ppe")
CLS = {"helmet": 0, "vest": 2, "person": 5, "no_helmet": 6, "no_vest": 11}
MAX_W = SafetyConfig().ppe_max_width


def load_labels(path, w, h, scale):
    """{class_id: [[x1,y1,x2,y2], ...]} in pixels of the (scaled) image."""
    boxes = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        p = line.split()
        if len(p) != 5:
            continue
        c, cx, cy, bw, bh = int(p[0]), *map(float, p[1:])
        boxes[c].append([(cx - bw / 2) * w * scale, (cy - bh / 2) * h * scale,
                         (cx + bw / 2) * w * scale, (cy + bh / 2) * h * scale])
    return boxes


def gt_state(persons, present_boxes, absent_boxes, item_cls):
    """{track_id: 'PRESENT' | 'ABSENT' | None} from labelled boxes, using the production association."""
    present = associate_ppe(persons, [{"cls": item_cls, "conf": 1.0, "bbox": b} for b in present_boxes])
    absent = associate_ppe(persons, [{"cls": item_cls, "conf": 1.0, "bbox": b} for b in absent_boxes])
    out = {}
    for p in persons:
        tid = p["track_id"]
        if present[tid][item_cls] > 0:
            out[tid] = "PRESENT"
        elif absent[tid][item_cls] > 0:
            out[tid] = "ABSENT"
        else:
            out[tid] = None
    return out


def reading(conf):
    if conf >= PRESENT_CONF:
        return "PRESENT"
    if conf < WEAK_CONF:
        return "ABSENT"
    return "AMBIGUOUS"


def size_bucket(height_px):
    return "<90 px" if height_px < 90 else "90-200 px" if height_px < 200 else ">=200 px"


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--split", default="all", choices=["all", "train", "val"])
    ap.add_argument("--weights", default=None, help="PPE weights to evaluate (default: the production model)")
    ap.add_argument("--only", default=None, help="text file listing image paths; only images with these file names are used")
    ap.add_argument("--tag", default="", help="suffix for the report file names")
    ap.add_argument("--wide", action="store_true",
                    help="only wide scenes: 3-8 people, all under 30%% of the image height (a camera over a site)")
    args = ap.parse_args()

    splits = ["train", "val"] if args.split == "all" else [args.split]
    files = sorted(f for s in splits for f in glob.glob(os.path.join(ROOT, "images", s, "*.jpg")))
    if args.only:
        wanted = {os.path.basename(line.strip()) for line in open(args.only, encoding="utf-8") if line.strip()}
        files = [f for f in files if os.path.basename(f) in wanted]
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit("no images; run download_construction_ppe.py first")

    detector = PpeDetector(weights=args.weights)
    # item -> condition -> Counter(GT state, reading)
    table = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    persons_total = clear_total = 0
    images_with_people = 0

    for n, path in enumerate(files, 1):
        img = cv2.imread(path)
        if img is None:
            continue
        h0, w0 = img.shape[:2]
        scale = MAX_W / float(w0) if w0 > MAX_W else 1.0
        if scale < 1.0:
            img = cv2.resize(img, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
        label_path = path.replace(os.sep + "images" + os.sep, os.sep + "labels" + os.sep)[:-4] + ".txt"
        if not os.path.isfile(label_path):
            continue
        boxes = load_labels(label_path, w0, h0, scale)
        persons = [{"track_id": i, "bbox": b} for i, b in enumerate(boxes.get(CLS["person"], []))]
        if not persons:
            continue
        if args.wide and not (3 <= len(persons) <= 8 and max(p["bbox"][3] - p["bbox"][1] for p in persons) < 0.3 * h):
            continue
        images_with_people += 1

        dets = detector.detect(img, imgsz=int(math.ceil(max(h, w) / 32.0) * 32))
        seen = associate_frame(persons, dets, img.shape)
        truth = {
            "helmet": gt_state(persons, boxes.get(CLS["helmet"], []), boxes.get(CLS["no_helmet"], []), "helmet"),
            "vest": gt_state(persons, boxes.get(CLS["vest"], []), boxes.get(CLS["no_vest"], []), "safety_vest"),
        }
        for p in persons:
            tid = p["track_id"]
            height = p["bbox"][3] - p["bbox"][1]
            clear = bool(seen[tid]["clear"])
            persons_total += 1
            clear_total += clear
            for item, key in (("helmet", "helmet"), ("vest", "safety_vest")):
                gt = truth[item][tid]
                if gt is None:
                    continue
                r = reading(seen[tid][key])
                for cond in ("all people", "clear frames only" if clear else None, f"height {size_bucket(height)}"):
                    if cond:
                        table[item][cond][(gt, r)] += 1
        if n % 100 == 0:
            print(f"  {n}/{len(files)} images")

    lines = []
    out = lines.append
    out("# Construction-PPE: per-frame reading against explicit labels\n")
    out(("WIDE SCENES ONLY (3-8 people, each under 30% of the image height). " if args.wide else "")
        + f"Images: {len(files)} ({args.split}); used: {images_with_people}; people: {persons_total} "
        f"(of which clear by the live loop's gate: {clear_total}).\n")
    out("Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. "
        "Our PPE model was not trained on this export, but its source images may overlap with public sets we used.\n")
    out("Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. "
        "This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the "
        "numbers the temporal layer starts from, not the system's final accuracy.\n")
    summary = {}
    for item in ("helmet", "vest"):
        out(f"\n## {item.capitalize()}\n")
        out("| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |")
        out("|---|---:|---|---:|---:|---:|")
        for cond in sorted(table[item], key=lambda c: (not c.startswith("all"), c)):
            t = table[item][cond]
            for gt in ("PRESENT", "ABSENT"):
                row = [t[(gt, r)] for r in ("PRESENT", "AMBIGUOUS", "ABSENT")]
                tot = sum(row)
                if tot:
                    out(f"| {cond} | {tot} | {gt} | {row[0]} ({pct(row[0], tot)}) | {row[1]} ({pct(row[1], tot)}) | {row[2]} ({pct(row[2], tot)}) |")
        t = table[item]["all people"]
        gp = sum(t[("PRESENT", r)] for r in ("PRESENT", "AMBIGUOUS", "ABSENT"))
        ga = sum(t[("ABSENT", r)] for r in ("PRESENT", "AMBIGUOUS", "ABSENT"))
        tc = table[item].get("clear frames only", collections.Counter())
        cp = sum(tc[("PRESENT", r)] for r in ("PRESENT", "AMBIGUOUS", "ABSENT"))
        ca = sum(tc[("ABSENT", r)] for r in ("PRESENT", "AMBIGUOUS", "ABSENT"))
        summary[item] = {
            "labelled_present": gp, "labelled_absent": ga,
            "wrongly_read_absent_of_present": t[("PRESENT", "ABSENT")],
            "absent_caught": t[("ABSENT", "ABSENT")], "absent_missed_as_present": t[("ABSENT", "PRESENT")],
            "clear_present": cp, "clear_wrongly_read_absent": tc[("PRESENT", "ABSENT")],
            "clear_absent": ca, "clear_absent_caught": tc[("ABSENT", "ABSENT")],
        }
        out(f"\n- **False accusation, one frame** (labelled worn but read ABSENT): all {pct(t[('PRESENT', 'ABSENT')], gp)}, "
            f"clear frames only {pct(tc[('PRESENT', 'ABSENT')], cp)}.")
        out(f"- **Violation caught, one frame** (labelled missing and read ABSENT): all {pct(t[('ABSENT', 'ABSENT')], ga)}, "
            f"clear frames only {pct(tc[('ABSENT', 'ABSENT')], ca)}.")
        out(f"- **Violation missed as present:** all {pct(t[('ABSENT', 'PRESENT')], ga)}.")

    report = "\n".join(lines) + "\n"
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    base = os.path.join(HERE, "reports", "CONSTRUCTION_PPE_EVAL" + ("_WIDE" if args.wide else "") + args.tag)
    open(base + ".md", "w", encoding="utf-8").write(report)
    json.dump({"images": len(files), "people": persons_total, "clear_people": clear_total, "items": summary},
              open(base + ".json", "w", encoding="utf-8"), indent=2)
    print(report)


if __name__ == "__main__":
    main()
