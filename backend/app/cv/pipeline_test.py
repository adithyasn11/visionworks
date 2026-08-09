# backend/app/cv/pipeline_test.py
import cv2
import numpy as np
import time
import os
import sys

# Ensure backend package is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.cv.stream_reader import ThreadedStreamReader
from app.cv.detector_tracker import PersonDetectorTracker
from app.cv.pose_estimator import PostureEstimator
from app.cv.spatial_engine import SpatialEngine
from app.cv.activity_aggregator import ActivityAggregator
from app.cv.anonymizer import PrivacyAnonymizer

def run_test_pipeline(source_path: str = 0, num_frames_to_test: int = 50):
    """
    Integration test for AI Computer Vision Pipeline.
    Runs detection, tracking, pose extraction, zone containment, activity indexing, and face blur.
    """
    print(f"=== Starting AI Vision Core Pipeline Test on Source: {source_path} ===")

    # Define test workstation zones
    sample_zones = [
        {
            "zone_id": "workstation_01",
            "zone_name": "Desk 1",
            "polygon": [[50, 50], [400, 50], [400, 400], [50, 400]]
        },
        {
            "zone_id": "workstation_02",
            "zone_name": "Desk 2",
            "polygon": [[420, 50], [800, 50], [800, 400], [420, 400]]
        }
    ]

    detector = PersonDetectorTracker(model_path="yolov8m.pt", conf_thresh=0.45)
    posture_estimator = PostureEstimator(pose_model_path="yolov8m-pose.pt")
    spatial_engine = SpatialEngine(zones_config=sample_zones)
    activity_aggregator = ActivityAggregator()
    anonymizer = PrivacyAnonymizer()

    # Check source mode
    if source_path == "synthetic" or (isinstance(source_path, str) and not os.path.exists(source_path)):
        print(f"Testing in synthetic mode (generating test video frames)...")
        test_mode = "synthetic"
    else:
        test_mode = "stream"
        reader = ThreadedStreamReader(source_path=source_path, fps_target=8)
        reader.start()

    processed_frames = 0
    start_time = time.time()

    try:
        while processed_frames < num_frames_to_test:
            if test_mode == "stream":
                frame = reader.read_frame()
                if frame is None:
                    time.sleep(0.01)
                    continue
            else:
                # Generate synthetic test frame with moving mock human bounding box
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                # Draw mock desk regions
                cv2.rectangle(frame, (50, 50), (400, 400), (50, 50, 50), 2)
                cv2.rectangle(frame, (420, 50), (800, 400), (50, 50, 50), 2)
                # Mock moving person
                px = 150 + (processed_frames * 2)
                py = 150
                cv2.rectangle(frame, (px, py), (px + 80, py + 200), (255, 255, 255), -1)

            # 1. Detect & Track People
            detections = detector.process_frame(frame)

            # 2. Extract Pose Keypoints
            pose_keypoints = posture_estimator.extract_keypoints_for_bboxes(frame, detections)

            for idx, det in enumerate(detections):
                track_id = det["track_id"]
                bbox = det["bbox"]
                keypoints = pose_keypoints[idx]

                centroid = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]

                # 3. Classify Posture
                posture = posture_estimator.classify_posture(keypoints, bbox)

                # 4. Check Zone Containment
                zone_id = spatial_engine.check_zone_containment(centroid)

                # 5. Update Activity Telemetry
                activity_aggregator.update_track(track_id, centroid, posture)
                activity_score = activity_aggregator.calculate_activity_score(track_id)
                dwell_sec = activity_aggregator.get_dwell_time_seconds(track_id)

                # 6. Apply Privacy Blur
                frame = anonymizer.blur_face_region(frame, bbox)

                print(f"[Frame {processed_frames+1}] Track ID #{track_id} | Posture: {posture} | Zone: {zone_id} | Score: {activity_score} | Dwell: {dwell_sec}s")

            processed_frames += 1

    finally:
        if test_mode == "stream":
            reader.stop()

    elapsed = time.time() - start_time
    avg_fps = processed_frames / (elapsed + 1e-6)
    print(f"\n=== Test Completed Successfully! Processed {processed_frames} frames in {elapsed:.2f}s (Avg FPS: {avg_fps:.2f}) ===")
    return True

if __name__ == "__main__":
    # Test on synthetic/camera stream
    run_test_pipeline(source_path="synthetic", num_frames_to_test=15)
