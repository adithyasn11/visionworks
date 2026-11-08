"""Train YOLOv8m on the five-class core PPE dataset."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "ppe_training" / "datasets" / "core5"
DATA_YAML = DATASET / "data.yaml"
BASE_MODEL = ROOT / "backend" / "yolov8m.pt"
RUNS_DIR = ROOT / "ppe_training" / "runs"
RUN_NAME = "ppe_core_yolov8m_v1"
EXPECTED_CLASSES = ["helmet", "safety_vest", "gloves", "boots", "mask"]


def fail(message: str) -> None:
    print(f"[ERROR] {message}")
    raise SystemExit(1)


def check_dataset() -> None:
    if not DATA_YAML.is_file():
        fail(f"Core dataset YAML not found: {DATA_YAML}")
    if not BASE_MODEL.is_file():
        fail(f"Base YOLOv8m model not found: {BASE_MODEL}")
    for split in ("train", "val", "test"):
        image_dir = DATASET / "images" / split
        label_dir = DATASET / "labels" / split
        if not image_dir.is_dir() or not label_dir.is_dir():
            fail(f"Missing {split} images or labels directory.")
        images = {p.stem for p in image_dir.iterdir() if p.is_file()}
        labels = {p.stem for p in label_dir.glob("*.txt")}
        if images != labels:
            fail(f"{split} image/label mismatch.")
    yaml_text = DATA_YAML.read_text(encoding="utf-8")
    for index, name in enumerate(EXPECTED_CLASSES):
        if f"{index}: {name}" not in yaml_text:
            fail(f"data.yaml is missing class mapping {index}: {name}")


def main() -> None:
    check_dataset()
    print(f"[INFO] Dataset: {DATA_YAML}")
    print(f"[INFO] Base model: {BASE_MODEL}")
    print(f"[INFO] Output: {RUNS_DIR / RUN_NAME}")
    try:
        from ultralytics import YOLO
    except ImportError:
        fail("Ultralytics is missing.")
    model = YOLO(str(BASE_MODEL))
    model.train(
        data=str(DATA_YAML), epochs=150, imgsz=640, batch=-1, patience=35,
        device=0, workers=4, pretrained=True, project=str(RUNS_DIR),
        name=RUN_NAME, exist_ok=False, save=True, plots=True, cache=False,
        amp=True, verbose=True,
    )
    best_model = RUNS_DIR / RUN_NAME / "weights" / "best.pt"
    if not best_model.is_file():
        fail(f"Training finished but best.pt was not found: {best_model}")
    print(f"[SUCCESS] Best model: {best_model}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Training cancelled by the user.")
        sys.exit(130)
