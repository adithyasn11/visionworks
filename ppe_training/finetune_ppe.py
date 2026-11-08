"""
Fine-tune the production PPE detector on Construction-PPE (plus a sample of its
original data so it does not forget). Run prepare_finetune_data.py first.

Starts from runs/ppe_yolov8m_v1/weights/best.pt (same six classes), so the result
is a drop-in replacement. Writes runs/ppe_yolov8m_ft1/. The best epoch is picked on
valA only; the fair comparison with the current model is on valB
(see compare_finetune.py).

Run:  venv/Scripts/python.exe ppe_training/finetune_ppe.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "ppe_training" / "datasets" / "ppe_finetune" / "data.yaml"
START = ROOT / "ppe_training" / "runs" / "ppe_yolov8m_v1" / "weights" / "best.pt"
RUNS = ROOT / "ppe_training" / "runs"
NAME = "ppe_yolov8m_ft1"


def main():
    from ultralytics import YOLO
    for p in (DATA, START):
        if not p.is_file():
            raise SystemExit(f"missing: {p}")
    model = YOLO(str(START))
    model.train(
        data=str(DATA),
        epochs=30,
        patience=10,
        imgsz=960,
        batch=8,
        device=0,
        workers=2,
        optimizer="AdamW",
        lr0=0.0005,
        lrf=0.05,
        cos_lr=True,
        warmup_epochs=1,
        close_mosaic=5,
        project=str(RUNS),
        name=NAME,
        exist_ok=True,
        pretrained=True,
        amp=True,
        cache=False,
        plots=True,
        seed=0,
        deterministic=True,
        verbose=True,
    )
    print("best:", RUNS / NAME / "weights" / "best.pt")


if __name__ == "__main__":
    main()
