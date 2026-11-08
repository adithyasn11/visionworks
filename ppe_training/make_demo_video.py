"""
Build a short demo video for the live view from the PPE model's held-out test
images: one worker at a time, large and clear, in four groups (helmet + vest,
helmet only, vest only, neither), so the PPE badges are easy to see.

Each image is held for HOLD_SECONDS and shown on a plain dark background at
960x540, so every worker is far above the 90 px "clear frame" height.

The images are from the dataset in ppe_training/datasets/ppe6_roboflow
(Roboflow, CC BY 4.0: workspace water-garbage-detection, project
ppe-detection-ozhfb-restt). They are the TEST split, which the PPE model never
trained on. The labels say which items are visible in each image; they are used
only to choose a mix, never to decide a verdict.

Run:   venv/Scripts/python.exe ppe_training/make_demo_video.py
Makes: sample_videos/ppe_demo.mp4  (git-ignored: it is generated)
"""

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "datasets", "ppe6_roboflow", "test")
OUT = os.path.abspath(os.path.join(HERE, "..", "sample_videos", "ppe_demo.mp4"))

W, H, FPS, HOLD_SECONDS = 960, 540, 10, 5
PER_GROUP = 3
HELMET, PERSON, VEST = "2", "4", "5"


def pick():
    groups = {"HV": [], "--": [], "H-": [], "-V": []}
    for name in sorted(os.listdir(os.path.join(TEST, "labels"))):
        rows = [line.split() for line in open(os.path.join(TEST, "labels", name)) if line.strip()]
        people = [r for r in rows if r[0] == PERSON]
        if len(people) != 1 or float(people[0][4]) < 0.7:     # one person, filling most of the picture
            continue
        classes = {r[0] for r in rows}
        key = ("H" if HELMET in classes else "-") + ("V" if VEST in classes else "-")
        groups[key].append(name[:-4] + ".jpg")
    # Interleave the groups so the verdicts change from one worker to the next.
    order = []
    for i in range(PER_GROUP):
        for key in ("HV", "--", "H-", "-V"):
            if i < len(groups[key]):
                order.append((key, groups[key][i * 7 % len(groups[key])]))
    return order


def frame_for(path):
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
    order = pick()
    if not order:
        sys.exit("no suitable test images found")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not writer.isOpened():
        sys.exit("could not open the video writer")
    for key, name in order:
        frame = frame_for(os.path.join(TEST, "images", name))
        if frame is None:
            continue
        print(f"{key}  {name}")
        for _ in range(HOLD_SECONDS * FPS):
            writer.write(frame)
    writer.release()
    print(f"wrote {OUT}  ({len(order) * HOLD_SECONDS} s)")


if __name__ == "__main__":
    main()
