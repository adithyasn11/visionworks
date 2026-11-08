# backend/app/cv/ppe_detector.py
"""
Helmet / safety-vest detector (YOLOv8m, ppe_training/runs/ppe_yolov8m_v1).

Deliberately returns ONLY helmet and safety_vest. People come from the pose
model that already tracks them (ByteTrack ids, keypoints); using this model's
own `person` class as well would give two competing person boxes per worker.

The model was trained on 640x640 STRETCHED squares (Roboflow "Resize to
640x640 (Stretch)"). Frames are therefore letterboxed by Ultralytics as usual,
but expect slightly different behaviour on 16:9 video than the validation
numbers suggest — measure on your own footage (ppe_training/eval_ppe_footage.py).
"""

import os

DEFAULT_WEIGHTS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "ppe_training", "runs", "ppe_yolov8m_v1", "weights", "best.pt"))

# Low floor on purpose: the evidence layer needs to see weak (0.20-0.45)
# detections to tell "ambiguous" from "nothing there".
DEFAULT_CONF_FLOOR = 0.20
WANTED = ("helmet", "safety_vest")


class PpeDetector:
    def __init__(self, weights: str = None, device=None,
                 conf_floor: float = DEFAULT_CONF_FLOOR, imgsz: int = 640):
        from ultralytics import YOLO
        import torch

        self.weights = weights or os.getenv("PPE_MODEL_PATH") or DEFAULT_WEIGHTS
        if not os.path.isfile(self.weights):
            raise FileNotFoundError(f"PPE model not found: {self.weights}")
        self.device = device if device is not None else (
            0 if torch.cuda.is_available() else "cpu")
        self.model = YOLO(self.weights)
        self.conf_floor = conf_floor
        self.imgsz = imgsz
        self.names = self.model.names

    def detect(self, frame):
        """[{"cls", "conf", "bbox"}] for helmets and vests in a BGR frame."""
        res = self.model.predict(frame, conf=self.conf_floor, iou=0.6,
                                 imgsz=self.imgsz, device=self.device,
                                 verbose=False)[0]
        out = []
        if res.boxes is None or len(res.boxes) == 0:
            return out
        for b, c, s in zip(res.boxes.xyxy.cpu().numpy(),
                           res.boxes.cls.cpu().numpy().astype(int),
                           res.boxes.conf.cpu().numpy()):
            name = self.names[int(c)]
            if name in WANTED:
                out.append({"cls": name, "conf": float(s),
                            "bbox": [float(v) for v in b]})
        return out
