# backend/app/cv/detector_tracker.py
from ultralytics import YOLO
import torch
import numpy as np
import logging

logger = logging.getLogger(__name__)

class PersonDetectorTracker:
    """
    Person Detection & Tracking module using YOLOv8 Medium model (yolov8m.pt) and ByteTrack.
    Runs on NVIDIA RTX 4060 GPU via CUDA.
    """
    def __init__(self, model_path: str = "yolov8m.pt", conf_thresh: float = 0.45, device: str = None):
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
            
        logger.info(f"PersonDetectorTracker initializing '{model_path}' on device '{self.device}'")
        self.model = YOLO(model_path)
        # Warmup GPU
        if self.device == "cuda":
            self.model.to("cuda")
        self.conf_thresh = conf_thresh

    def process_frame(self, frame: np.ndarray):
        results = self.model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0], # Class 0 = Person in COCO dataset
            conf=self.conf_thresh,
            device=self.device,
            verbose=False
        )[0]

        detections = []
        if results.boxes is not None and results.boxes.id is not None:
            boxes = results.boxes.xyxy.cpu().numpy()
            track_ids = results.boxes.id.cpu().numpy().astype(int)
            confs = results.boxes.conf.cpu().numpy()

            for box, track_id, conf in zip(boxes, track_ids, confs):
                x1, y1, x2, y2 = map(int, box)
                detections.append({
                    "track_id": int(track_id),
                    "bbox": [x1, y1, x2, y2],
                    "confidence": float(conf)
                })

        return detections
