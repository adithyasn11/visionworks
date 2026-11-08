"""Recompute the Ultralytics confusion matrix and print the helmet/vest columns.

Used to check a reading of confusion_matrix_normalized.png against a direct
measurement. workers=0 because the default dataloader workers stall on Windows.
"""
import sys
from pathlib import Path
import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "ppe_training/runs/ppe_yolov8m_v1/weights/best.pt"
Y = ROOT / "ppe_training/datasets/ppe6_roboflow/data.yaml"
split = sys.argv[1] if len(sys.argv) > 1 else "val"
names = ["boots", "gloves", "helmet", "mask", "person", "vest", "background"]
r = YOLO(str(W)).val(data=str(Y), split=split, conf=0.25, iou=0.45, device=0,
                     workers=0, verbose=False, plots=True,
                     project=sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "ppe_training" / "reports"),
                     name="confusion_check", exist_ok=True)
cm = r.confusion_matrix.matrix            # rows = predicted, cols = true
n = cm / np.maximum(cm.sum(0, keepdims=True), 1)
print("mAP50", round(float(r.box.map50), 3))
print("true-class totals:", dict(zip(names, cm.sum(0).astype(int).tolist())))
for col in (2, 5):
    print(f"true {names[col]} ->", {names[i]: round(float(n[i, col]), 3) for i in range(7)})
