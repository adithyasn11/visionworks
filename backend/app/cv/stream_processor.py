# backend/app/cv/stream_processor.py
import cv2
import base64
import time
import asyncio
import numpy as np
import os
import logging
from app.cv.detector_tracker import PersonDetectorTracker
from app.cv.pose_estimator import PostureEstimator
from app.cv.spatial_engine import SpatialEngine
from app.cv.activity_aggregator import ActivityAggregator
from app.cv.anonymizer import PrivacyAnonymizer
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

class ActiveVideoProcessor:
    """
    Processes video files/streams frame-by-frame using YOLO Medium + Pose Estimation,
    calculates spatial zone presence & posture activity scores, and prepares base64 WebSocket frames.
    """
    def __init__(self, model_size: str = "medium"):
        model_name = "yolov8m.pt" if model_size == "medium" else "yolov8s.pt"
        pose_model_name = "yolov8m-pose.pt" if model_size == "medium" else "yolov8s-pose.pt"

        self.detector = PersonDetectorTracker(model_path=model_name, conf_thresh=0.45)
        self.pose_estimator = PostureEstimator(pose_model_path=pose_model_name, conf_thresh=0.35)
        self.activity_aggregator = ActivityAggregator()
        self.anonymizer = PrivacyAnonymizer()
        
        # Default workstation zones
        self.spatial_engine = SpatialEngine(zones_config=[
            {
                "zone_id": "workstation_01",
                "zone_name": "Workstation 1",
                "polygon": [[100, 80], [450, 80], [450, 480], [100, 480]]
            },
            {
                "zone_id": "workstation_02",
                "zone_name": "Workstation 2",
                "polygon": [[480, 80], [850, 80], [850, 480], [480, 480]]
            }
        ])

    def process_video_frame(self, frame: np.ndarray, enable_privacy_blur: bool = True):
        """
        Runs full detection, tracking, pose classification, zone containment, and telemetry calculation on a single frame.
        """
        frame_h, frame_w = frame.shape[:2]

        # 1. Person Detection & ByteTrack tracking
        detections = self.detector.process_frame(frame)

        # 2. Extract Pose Keypoints
        pose_keypoints = self.pose_estimator.extract_keypoints_for_bboxes(frame, detections)

        tracked_entities = []
        zone_summary = {"workstation_01": 0, "workstation_02": 0, "TRANSIT_ZONE": 0}

        for idx, det in enumerate(detections):
            track_id = det["track_id"]
            bbox = det["bbox"]
            keypoints = pose_keypoints[idx]

            centroid = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]

            # 3. Posture state classification
            posture = self.pose_estimator.classify_posture(keypoints, bbox)

            # 4. Zone containment check
            zone_id = self.spatial_engine.check_zone_containment(centroid)
            if zone_id in zone_summary:
                zone_summary[zone_id] += 1
            else:
                zone_summary["TRANSIT_ZONE"] += 1

            # 5. Activity telemetry update
            self.activity_aggregator.update_track(track_id, centroid, posture)
            activity_score = self.activity_aggregator.calculate_activity_score(track_id)
            dwell_seconds = self.activity_aggregator.get_dwell_time_seconds(track_id)

            # 6. Apply Privacy Blur
            if enable_privacy_blur:
                frame = self.anonymizer.blur_face_region(frame, bbox)

            tracked_entities.append({
                "track_id": track_id,
                "bbox": bbox,
                "posture": posture,
                "activity_score": activity_score,
                "zone_id": zone_id,
                "dwell_duration_seconds": dwell_seconds
            })

        # Encode frame to JPEG Base64
        _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        frame_base64 = base64.b64encode(buffer).decode('utf-8')

        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "frame_base64": f"data:image/jpeg;base64,{frame_base64}",
            "tracked_entities": tracked_entities,
            "total_detected": len(tracked_entities),
            "zone_occupancy": zone_summary
        }
