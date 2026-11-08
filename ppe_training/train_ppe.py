"""Train the VisionWorks PPE detector with Ultralytics YOLOv8."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "ppe_training" / "datasets" / "ppe6_roboflow"
DATA_YAML = DATASET_DIR / "data.yaml"
MODEL = ROOT / "backend" / "yolov8m.pt"
RUNS_DIR = ROOT / "ppe_training" / "runs"


def fail(message: str) -> None:
    print(f"[ERROR] {message}")
    raise SystemExit(1)


def main() -> None:
    if not DATA_YAML.is_file():
        fail(f"Dataset configuration was not found: {DATA_YAML}")
    if not MODEL.is_file():
        fail(f"YOLOv8 model was not found: {MODEL}")

    try:
        from ultralytics import YOLO
    except ImportError:
        fail(
            "Ultralytics is not installed in the project virtual environment. "
            "Run: venv\\Scripts\\python.exe -m pip install ultralytics"
        )

    print(f"[INFO] Dataset: {DATA_YAML}")
    print(f"[INFO] Base model: {MODEL}")
    print("[INFO] Training YOLOv8m with pretrained weights.")
    print("[INFO] Results will be written under runs\\ppe.")

    model = YOLO(str(MODEL))
    model.train(
        data=str(DATA_YAML),
        epochs=150,
        imgsz=640,
        batch=-1,
        patience=30,
        device=0,
        workers=4,
        pretrained=True,
        project=str(RUNS_DIR),
        name="ppe_yolov8m_v1",
        exist_ok=True,
        save=True,
        plots=True,
        cache=False,
        amp=True,
        verbose=True,
    )

    best_model = RUNS_DIR / "ppe_yolov8m_v1" / "weights" / "best.pt"
    if not best_model.is_file():
        fail(f"Training finished but best.pt was not found at {best_model}")

    print(f"[INFO] Best model: {best_model}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Training cancelled by the user.")
        sys.exit(130)
