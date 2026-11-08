"""
Build demo videos for the live view from labelled PPE images: a still held for a
few seconds is a fixed camera looking at a scene, which is all the live view
needs to show its badges and zone events.

Three videos (all git-ignored, generated into sample_videos/):

  ppe_demo.mp4                    --source ppe6          one large worker at a time (the model's
                                                          held-out test images)
  ppe_demo_construction.mp4       --source construction  one large worker at a time (Construction-PPE)
  ppe_demo_cctv_like.mp4          --source construction --view wide
                                                          3-8 small workers seen from a distance, like a
                                                          fixed camera over a site (each held 8 s)

Close views are grouped helmet+vest / neither / helmet only / vest only and
interleaved, so the verdicts change from one worker to the next. The labels are
used only to choose that mix, never to decide a verdict.

Sources and licences: ppe6 = Roboflow "ppe-detection-ozhfb-restt" (CC BY 4.0);
construction = Ultralytics Construction-PPE (CC BY 4.0, partly generated
imagery; download_construction_ppe.py fetches it).

Run:
  venv/Scripts/python.exe ppe_training/make_demo_video.py
  venv/Scripts/python.exe ppe_training/make_demo_video.py --source construction
  venv/Scripts/python.exe ppe_training/make_demo_video.py --source construction --view wide
"""

import argparse
import glob
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "sample_videos"))
FPS = 10
PER_GROUP = 3


def read_rows(path):
    return [line.split() for line in open(path, encoding="utf-8") if line.strip()]


def interleave(groups, pick_index):
    order = []
    for i in range(PER_GROUP):
        for key in ("HV", "--", "H-", "-V"):
            items = groups[key]
            if i < len(items):
                order.append((key, items[i * pick_index % len(items)]))
    return order


def pick_ppe6():
    root = os.path.join(HERE, "datasets", "ppe6_roboflow", "test")
    HELMET, PERSON, VEST = "2", "4", "5"
    groups = {"HV": [], "--": [], "H-": [], "-V": []}
    for name in sorted(os.listdir(os.path.join(root, "labels"))):
        rows = read_rows(os.path.join(root, "labels", name))
        people = [r for r in rows if r[0] == PERSON]
        if len(people) != 1 or float(people[0][4]) < 0.7:
            continue
        classes = {r[0] for r in rows}
        key = ("H" if HELMET in classes else "-") + ("V" if VEST in classes else "-")
        groups[key].append(os.path.join(root, "images", name[:-4] + ".jpg"))
    return interleave(groups, 7), 5, (960, 540)


def pick_construction(view):
    root = os.path.join(HERE, "datasets", "construction_ppe")
    HELMET, VEST, PERSON, NO_HELMET, NO_VEST = "0", "2", "5", "6", "11"
    groups = {"HV": [], "--": [], "H-": [], "-V": []}
    wide = []
    for lab in sorted(glob.glob(os.path.join(root, "labels", "*", "*.txt"))):
        rows = read_rows(lab)
        people = [r for r in rows if r[0] == PERSON]
        img = lab.replace(os.sep + "labels" + os.sep, os.sep + "images" + os.sep)[:-4] + ".jpg"
        classes = {r[0] for r in rows}
        if view == "wide":
            if 3 <= len(people) <= 8 and max(float(p[4]) for p in people) < 0.3:
                wide.append(img)
            continue
        if len(people) != 1 or float(people[0][4]) < 0.6:
            continue
        key = (("H" if HELMET in classes and NO_HELMET not in classes else "-")
               + ("V" if VEST in classes and NO_VEST not in classes else "-"))
        groups[key].append(img)
    if view == "wide":
        step = max(1, len(wide) // 8)
        return [("wide", p) for p in wide[::step][:8]], 8, (1280, 720)
    return interleave(groups, 11), 5, (960, 540)


def frame_for(path, size):
    W, H = size
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(W / w, H / h)
    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    canvas = np.full((H, W, 3), 24, dtype=np.uint8)
    y, x = (H - img.shape[0]) // 2, (W - img.shape[1]) // 2
    canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["ppe6", "construction"], default="ppe6")
    ap.add_argument("--view", choices=["close", "wide"], default="close")
    ap.add_argument("--gap", type=float, default=0.0, metavar="SECONDS",
                    help="plain empty frames after each picture, long enough for the tracker to forget the worker, "
                         "so every picture is a NEW worker walking in (default 0: pictures cut straight to each other)")
    args = ap.parse_args()
    if args.source == "ppe6" and args.view == "wide":
        sys.exit("the ppe6 images have no wide views; use --source construction --view wide")

    if args.source == "ppe6":
        order, hold, size = pick_ppe6()
        out = os.path.join(OUT_DIR, "ppe_demo.mp4")
    else:
        order, hold, size = pick_construction(args.view)
        out = os.path.join(OUT_DIR, "ppe_demo_cctv_like.mp4" if args.view == "wide" else "ppe_demo_construction.mp4")
    if not order:
        sys.exit("no suitable images found (is the dataset downloaded?)")
    if args.gap > 0:
        out = out.replace(".mp4", "_gaps.mp4")

    os.makedirs(OUT_DIR, exist_ok=True)
    writer = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
    if not writer.isOpened():
        sys.exit("could not open the video writer")
    for key, path in order:
        frame = frame_for(path, size)
        if frame is None:
            continue
        print(f"{key}  {os.path.basename(path)}")
        for _ in range(hold * FPS):
            writer.write(frame)
        empty = np.full((size[1], size[0], 3), 24, dtype=np.uint8)
        for _ in range(int(round(args.gap * FPS))):
            writer.write(empty)
    writer.release()
    print(f"wrote {out}  ({len(order) * (hold + args.gap):.0f} s, {size[0]}x{size[1]})")


if __name__ == "__main__":
    main()
