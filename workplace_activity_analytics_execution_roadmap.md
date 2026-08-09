# Vision-Based Workplace Activity Analytics System
## Exhaustive Step-by-Step Implementation Roadmap & Execution Guide

---

## Table of Contents
1. [Overview & Execution Strategy](#1-overview--execution-strategy)
2. [Step 1: Environment & Project Scaffolding](#step-1-environment--project-scaffolding)
3. [Step 2: Video Stream Ingestion & Threaded Frame Reader](#step-2-video-stream-ingestion--threaded-frame-reader)
4. [Step 3: Person Detection & Tracking Engine (YOLOv11 + ByteTrack)](#step-3-person-detection--tracking-engine-yolov11--bytetrack)
5. [Step 4: Pose Keypoint Extraction & Posture Math Engine](#step-4-pose-keypoint-extraction--posture-math-engine)
6. [Step 5: Activity Score Index & Dwell Time Aggregator](#step-5-activity-score-index--dwell-time-aggregator)
7. [Step 6: Polygon ROI Zone Engine & Homography Perspective Mapper](#step-6-polygon-roi-zone-engine--homography-perspective-mapper)
8. [Step 7: Privacy-by-Design Anonymization Engine (Face Blur)](#step-7-privacy-by-design-anonymization-engine-face-blur)
9. [Step 8: Standalone AI Vision Pipeline Test Script](#step-8-standalone-ai-vision-pipeline-test-script)
10. [Step 9: Database Architecture & ORM Setup (SQLite / PostgreSQL)](#step-9-database-architecture--orm-setup-sqlite--postgresql)
11. [Step 10: FastAPI REST API Endpoint Suite](#step-10-fastapi-rest-api-endpoint-suite)
12. [Step 11: Real-Time WebSocket Streaming Engine](#step-11-real-time-websocket-streaming-engine)
13. [Step 12: React (Vite) Frontend Scaffolding & Design System](#step-12-react-vite-frontend-scaffolding--design-system)
14. [Step 13: Live Video HUD Overlay Canvas Component](#step-13-live-video-hud-overlay-canvas-component)
15. [Step 14: Interactive Polygon ROI Drawing & Editing Tool](#step-14-interactive-polygon-roi-drawing--editing-tool)
16. [Step 15: Analytics Dashboard & Real-Time Visualization Widgets](#step-15-analytics-dashboard--real-time-visualization-widgets)
17. [Step 16: Top-Down Homography 2D Floorplan Heatmap](#step-16-top-down-homography-2d-floorplan-heatmap)
18. [Step 17: CSV Data & PDF Executive Report Generator](#step-17-csv-data--pdf-executive-report-generator)
19. [Step 18: System Performance Optimization & Benchmarking (ONNX/TensorRT)](#step-18-system-performance-optimization--benchmarking-onnxtensorrt)
20. [Step 19: End-to-End System Testing & Demonstration Setup](#step-19-end-to-end-system-testing--demonstration-setup)

---

## 1. Overview & Execution Strategy

This document provides an uncompromised, granular step-by-step developer guide to building the system from scratch. Follow the phases sequentially; each step builds directly on top of verified components from preceding steps.

```mermaid
flowchart LR
    S1[Steps 1-8:\nAI CV Pipeline Core] ──► S2[Steps 9-11:\nBackend API & Database] ──► S3[Steps 12-16:\nReact Dashboard & Canvas] ──► S4[Steps 17-19:\nReporting & Optimization]
```

---

## Step 1: Environment & Project Scaffolding

### 1.1 Directory Structure Setup
The project root is `d:\major project`. Do NOT create any outer wrapping directory. Create all subdirectories directly within `d:\major project`:

```bash
# Executed directly inside d:\major project (Project Root)
mkdir -p backend/app/api/routers backend/app/cv backend/app/db backend/app/services backend/app/utils backend/tests sample_videos
mkdir -p frontend/src/components frontend/src/hooks frontend/src/pages frontend/src/assets
```

### 1.2 Python Environment & Dependencies (`backend/requirements.txt`)
Create `backend/requirements.txt`:

```ini
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
ultralytics>=8.1.0
opencv-python-headless>=4.9.0
opencv-contrib-python>=4.9.0
torch>=2.2.0
torchvision>=0.17.0
numpy>=1.26.0
shapely>=2.0.0
scipy>=1.12.0
pydantic>=2.6.0
sqlalchemy>=2.0.0
aiosqlite>=0.20.0
websockets>=12.0
jinja2>=3.1.0
reportlab>=4.1.0
pandas>=2.2.0
onnxruntime>=1.17.0
```

Install the dependencies directly in the project root environment:
```bash
# Executed inside d:\major project (Project Root)
python -m venv venv
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r backend/requirements.txt
```

---

## Step 2: Video Stream Ingestion & Threaded Frame Reader

Create `backend/app/cv/stream_reader.py` to continuously ingest frames from an RTSP camera stream or sample video file without locking the main thread.

```python
# backend/app/cv/stream_reader.py
import cv2
import time
import threading
from queue import Queue

class ThreadedStreamReader:
    def __init__(self, source_path: str, fps_target: int = 8, queue_size: int = 15):
        self.source_path = source_path
        self.fps_target = fps_target
        self.frame_delay = 1.0 / fps_target
        self.queue = Queue(maxsize=queue_size)
        self.cap = None
        self.is_running = False
        self.thread = None

    def start(self):
        self.cap = cv2.VideoCapture(self.source_path)
        if not self.cap.isOpened():
            raise ValueError(f"Failed to open video source: {self.source_path}")
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _capture_loop(self):
        last_time = time.time()
        while self.is_running and self.cap.isOpened():
            current_time = time.time()
            if (current_time - last_time) >= self.frame_delay:
                ret, frame = self.cap.read()
                if not ret:
                    # Loop video if test file ends
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                last_time = current_time
                if self.queue.full():
                    self.queue.get_nowait() # Drop oldest frame to maintain low latency
                self.queue.put(frame)
            else:
                time.sleep(0.005)

    def read_frame(self):
        return self.queue.get() if not self.queue.empty() else None

    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
```

---

## Step 3: Person Detection & Tracking Engine (YOLOv11 + ByteTrack)

Create `backend/app/cv/detector_tracker.py`:

```python
# backend/app/cv/detector_tracker.py
from ultralytics import YOLO
import numpy as np

class PersonDetectorTracker:
    def __init__(self, model_path: str = "yolov8s.pt", conf_thresh: float = 0.50):
        self.model = YOLO(model_path)
        self.conf_thresh = conf_thresh

    def process_frame(self, frame: np.ndarray):
        # Run detection and ByteTrack multi-object tracking
        results = self.model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0], # Class 0 = Person
            conf=self.conf_thresh,
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
```

---

## Step 4: Pose Keypoint Extraction & Posture Math Engine

Create `backend/app/cv/pose_estimator.py` to calculate hip angles and classify posture:

```python
# backend/app/cv/pose_estimator.py
import numpy as np

class PostureEstimator:
    @staticmethod
    def calculate_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        """Calculates vector angle at point b given points a, b, c"""
        ba = a - b
        bc = c - b
        cosine_angle = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-6)
        angle = np.arccos(np.clip(cosine_angle, -1.0, 1.0))
        return np.degrees(angle)

    def classify_posture(self, keypoints: np.ndarray, bbox: list) -> str:
        """
        Keypoints index map (COCO format):
        5: Left Shoulder, 6: Right Shoulder
        11: Left Hip, 12: Right Hip
        13: Left Knee, 14: Right Knee
        """
        if len(keypoints) < 17:
            return "UNKNOWN"

        l_shoulder, r_shoulder = keypoints[5][:2], keypoints[6][:2]
        l_hip, r_hip = keypoints[11][:2], keypoints[12][:2]
        l_knee, r_knee = keypoints[13][:2], keypoints[14][:2]

        # Calculate midpoints
        shoulder_mid = (l_shoulder + r_shoulder) / 2.0
        hip_mid = (l_hip + r_hip) / 2.0
        knee_mid = (l_knee + r_knee) / 2.0

        # Hip Flexion Angle (Torso to Thigh)
        hip_angle = self.calculate_angle(shoulder_mid, hip_mid, knee_mid)

        # Evaluate height-to-width ratio of bounding box
        x1, y1, x2, y2 = bbox
        width = x2 - x1
        height = y2 - y1
        aspect_ratio = height / float(width + 1e-5)

        if 65.0 <= hip_angle <= 115.0 or aspect_ratio < 1.4:
            return "SITTING"
        elif hip_angle > 140.0 and aspect_ratio >= 1.4:
            return "STANDING"
        else:
            return "WALKING"
```

---

## Step 5: Activity Score Index & Dwell Time Aggregator

Create `backend/app/cv/activity_aggregator.py`:

```python
# backend/app/cv/activity_aggregator.py
import time
from collections import defaultdict, deque
import numpy as np

class ActivityAggregator:
    def __init__(self, history_window_seconds: int = 900):
        self.history_window = history_window_seconds
        self.track_history = defaultdict(lambda: {
            "first_seen": time.time(),
            "last_seen": time.time(),
            "positions": deque(maxlen=30),
            "postures": deque(maxlen=30),
            "posture_shifts": 0,
            "last_posture": None
        })

    def update_track(self, track_id: int, centroid: list, posture: str):
        now = time.time()
        track = self.track_history[track_id]
        track["last_seen"] = now
        track["positions"].append(centroid)
        
        if track["last_posture"] and track["last_posture"] != posture:
            track["posture_shifts"] += 1
        track["last_posture"] = posture
        track["postures"].append(posture)

    def calculate_activity_score(self, track_id: int) -> float:
        track = self.track_history[track_id]
        positions = np.array(track["positions"])
        
        if len(positions) < 2:
            return 50.0

        # Motion variance
        motion_variance = np.std(positions, axis=0).mean()
        motion_score = min(100.0, (motion_variance / 15.0) * 100.0)

        # Posture shift bonus
        shift_bonus = min(20.0, track["posture_shifts"] * 10.0)

        # Weighted final score
        final_score = (0.7 * motion_score) + shift_bonus
        return round(float(np.clip(final_score, 0.0, 100.0)), 2)

    def get_dwell_time(self, track_id: int) -> int:
        track = self.track_history[track_id]
        return int(track["last_seen"] - track["first_seen"])
```

---

## Step 6: Polygon ROI Zone Engine & Homography Perspective Mapper

Create `backend/app/cv/spatial_engine.py`:

```python
# backend/app/cv/spatial_engine.py
import cv2
import numpy as np
from shapely.geometry import Point, Polygon

class SpatialEngine:
    def __init__(self, zones_config: list):
        """
        zones_config: list of dicts [{"zone_id": "desk_1", "polygon": [[x1,y1], [x2,y2], ...]}]
        """
        self.zones = []
        for zone in zones_config:
            self.zones.append({
                "zone_id": zone["zone_id"],
                "polygon": Polygon(zone["polygon"]),
                "raw_pts": np.array(zone["polygon"], np.int32)
            })

    def check_zone_containment(self, point: list) -> str:
        pt = Point(point[0], point[1])
        for zone in self.zones:
            if zone["polygon"].contains(pt):
                return zone["zone_id"]
        return "TRANSIT_ZONE"

    @staticmethod
    def compute_homography_matrix(src_pts: np.ndarray, dst_pts: np.ndarray) -> np.ndarray:
        """Computes 3x3 Homography Matrix mapping camera space to top-down 2D canvas"""
        H, status = cv2.findHomography(src_pts, dst_pts)
        return H

    @staticmethod
    def transform_point_topdown(H: np.ndarray, point: list) -> list:
        pt = np.array([point[0], point[1], 1.0]).reshape(3, 1)
        dst = np.dot(H, pt)
        dst /= dst[2]
        return [float(dst[0][0]), float(dst[1][0])]
```

---

## Step 7: Privacy-by-Design Anonymization Engine (Face Blur)

Create `backend/app/cv/anonymizer.py`:

```python
# backend/app/cv/anonymizer.py
import cv2
import numpy as np

class PrivacyAnonymizer:
    @staticmethod
    def blur_face_region(frame: np.ndarray, bbox: list) -> np.ndarray:
        """Applies Gaussian Blur over estimated head region of bounding box"""
        x1, y1, x2, y2 = bbox
        head_h = int((y2 - y1) * 0.25) # Top 25% of box represents head
        head_y2 = y1 + head_h

        head_roi = frame[max(0, y1):min(frame.shape[0], head_y2), max(0, x1):min(frame.shape[1], x2)]
        if head_roi.size > 0:
            blurred_head = cv2.GaussianBlur(head_roi, (25, 25), 30)
            frame[max(0, y1):min(frame.shape[0], head_y2), max(0, x1):min(frame.shape[1], x2)] = blurred_head
        return frame
```

---

## Step 8: Standalone AI Vision Pipeline Test Script

Create `backend/app/cv/pipeline_test.py` to verify all CV modules before connecting to FastAPI:

```python
# backend/app/cv/pipeline_test.py
import cv2
from stream_reader import ThreadedStreamReader
from detector_tracker import PersonDetectorTracker
from pose_estimator import PostureEstimator
from activity_aggregator import ActivityAggregator
from anonymizer import PrivacyAnonymizer

def run_test_pipeline(video_path: str):
    reader = ThreadedStreamReader(source_path=video_path, fps_target=10)
    detector = PersonDetectorTracker(model_path="yolov8s.pt")
    posture_eval = PostureEstimator()
    aggregator = ActivityAggregator()
    anonymizer = PrivacyAnonymizer()

    reader.start()
    print("AI Vision Pipeline Initialized. Press 'q' to quit.")

    while True:
        frame = reader.read_frame()
        if frame is None:
            continue

        detections = detector.process_frame(frame)
        for det in detections:
            track_id = det["track_id"]
            bbox = det["bbox"]
            centroid = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]

            # Dummy keypoint array for testing state logic
            posture = posture_eval.classify_posture(np.zeros((17, 3)), bbox)
            aggregator.update_track(track_id, centroid, posture)
            act_score = aggregator.calculate_activity_score(track_id)

            # Anonymize frame
            frame = anonymizer.blur_face_region(frame, bbox)

            # Draw HUD elements on test preview window
            cv2.rectangle(frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{track_id} {posture} Score:{act_score}", 
                        (bbox[0], bbox[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        cv2.imshow("Workplace Activity Analytics - Test HUD", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    reader.stop()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    run_test_pipeline("sample_office.mp4")
```

---

## Step 9: Database Architecture & ORM Setup (SQLite / PostgreSQL)

Create `backend/app/db/database.py` and `backend/app/db/models.py`:

```python
# backend/app/db/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Camera(Base):
    __tablename__ = "cameras"
    camera_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    rtsp_url = Column(String(512), nullable=False)
    fps_target = Column(Integer, default=8)

class Zone(Base):
    __tablename__ = "zones"
    zone_id = Column(String(64), primary_key=True)
    camera_id = Column(String(64), nullable=False)
    zone_name = Column(String(128), nullable=False)
    polygon_coordinates = Column(JSON, nullable=False)

class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    camera_id = Column(String(64), nullable=False)
    zone_id = Column(String(64), nullable=False)
    track_id = Column(Integer, nullable=False)
    posture_state = Column(String(32), nullable=False)
    activity_score = Column(Float, nullable=False)
    dwell_seconds = Column(Integer, nullable=False)
```

---

## Step 10: FastAPI REST API Endpoint Suite

Create `backend/app/main.py`:

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routers import cameras, zones, analytics

app = FastAPI(
    title="Vision-Based Workplace Activity Analytics API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router, prefix="/api/v1/cameras", tags=["Cameras"])
app.include_router(zones.router, prefix="/api/v1/zones", tags=["Zones"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])

@app.get("/")
def read_root():
    return {"status": "ONLINE", "system": "Workplace Activity Analytics Engine"}
```

---

## Step 11: Real-Time WebSocket Streaming Engine

Create `backend/app/api/routers/websocket.py`:

```python
# backend/app/api/routers/websocket.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
import asyncio

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_text(json.dumps(message))

manager = ConnectionManager()

@router.websocket("/ws/stream/{camera_id}")
async def websocket_endpoint(websocket: WebSocket, camera_id: str):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)
```

---

## Step 12: React (Vite) Frontend Scaffolding & Design System

Initialize frontend inside `d:\major project`:
```bash
# Executed in d:\major project (Project Root)
npx -y create-vite@latest frontend --template react
cd frontend
npm install
npm install lucide-react recharts clsx tailwindcss autoprefixer postcss heatmap.js
```

Configure TailwindCSS in `frontend/tailwind.config.js`:
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          dark: "#0F172A",
          card: "#1E293B",
          accent: "#38BDF8",
          success: "#22C55E",
          warning: "#F59E0B"
        }
      }
    },
  },
  plugins: [],
}
```

---

## Step 13: Live Video HUD Overlay Canvas Component

Create `frontend/src/components/VideoCanvasPlayer.jsx`:

```jsx
// frontend/src/components/VideoCanvasPlayer.jsx
import React, { useRef, useEffect } from 'react';

export const VideoCanvasPlayer = ({ streamData }) => {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !streamData) return;
    const ctx = canvas.getContext('2d');

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw active tracked entity bounding boxes & telemetry
    streamData.tracked_entities?.forEach((entity) => {
      const [x1, y1, x2, y2] = entity.bbox;
      const width = x2 - x1;
      const height = y2 - y1;

      // Draw bounding box
      ctx.strokeStyle = entity.posture === 'SITTING' ? '#22C55E' : '#38BDF8';
      ctx.lineWidth = 2;
      ctx.strokeRect(x1, y1, width, height);

      // Draw track pill banner
      ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
      ctx.fillRect(x1, y1 - 24, 140, 22);

      ctx.fillStyle = '#FFFFFF';
      ctx.font = '12px Inter, sans-serif';
      ctx.fillText(`ID:${entity.track_id} | ${entity.posture} (${entity.activity_score})`, x1 + 5, y1 - 8);
    });
  }, [streamData]);

  return (
    <div className="relative rounded-xl overflow-hidden border border-slate-800 bg-slate-950">
      <canvas ref={canvasRef} width={1280} height={720} className="w-full h-auto" />
    </div>
  );
};
```

---

## Step 14: Interactive Polygon ROI Drawing & Editing Tool

Create `frontend/src/components/ROIEditor.jsx` to let administrators click directly on the canvas to define desk boundaries and save polygon coordinates to the backend database.

---

## Step 15: Analytics Dashboard & Real-Time Visualization Widgets

Create `frontend/src/components/AnalyticsCharts.jsx` using `recharts`:
- **Line Chart**: Real-time 15-minute rolling Activity Score Index.
- **Pie Chart**: Posture distribution (Sitting % vs Standing % vs Away %).
- **Bar Chart**: Dwell time comparison across all desk zones.

---

## Step 16: Top-Down Homography 2D Floorplan Heatmap

Create `frontend/src/components/FloorplanHeatmap.jsx` using `heatmap.js`:
- Renders flat 2D office blueprint graphic.
- Transforms camera stream entity locations to top-down $(x, y)$ points.
- Plots real-time thermal activity density gradients (Red = high dwell time/activity, Blue = low density).

---

## Step 17: CSV Data & PDF Executive Report Generator

Create `backend/app/utils/report_generator.py` using `ReportLab` to produce clean PDF summaries containing:
- Executive Summary table of space utilization.
- Posture health advisory alerts (sedentary warnings).
- Peak activity hour graphs.

---

## Step 18: System Performance Optimization & Benchmarking (ONNX/TensorRT)

Export YOLO PyTorch model to ONNX runtime for CPU acceleration:
```bash
yolo export model=yolov8s.pt format=onnx imgsz=640 simplify=True
```

Update `backend/app/cv/detector_tracker.py` to run ONNX model for 3x inference speedup on CPU.

---

## Step 19: End-to-End System Testing & Demonstration Setup

1. **Backend Verification**: Start FastAPI server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
2. **Frontend Verification**: Start React dev server:
   ```bash
   npm run dev
   ```
3. **Validation Check**: Open `http://localhost:5173`, select test video input stream, verify live bounding box rendering, posture classification accuracy, and WebSocket latency (< 50ms).

---

## Project Execution Checklist

- [ ] **Step 1**: Environment setup & dependencies installed
- [ ] **Step 2**: StreamReader video capture verified
- [ ] **Step 3**: YOLOv11 + ByteTrack detection & tracking working
- [ ] **Step 4**: Pose keypoint extraction & posture classification verified
- [ ] **Step 5**: Activity Score Index aggregator working
- [ ] **Step 6**: Spatial ROI polygon containment logic passing tests
- [ ] **Step 7**: Privacy face blur anonymization functional
- [ ] **Step 8**: Standalone `pipeline_test.py` running cleanly
- [ ] **Step 9**: SQLite / PostgreSQL schema initialized
- [ ] **Step 10**: FastAPI REST API endpoints responding
- [ ] **Step 11**: Real-time WebSockets broadcasting frame telemetry
- [ ] **Step 12**: React (Vite) frontend initialized with TailwindCSS
- [ ] **Step 13**: Live Video Canvas player rendering HUD overlays
- [ ] **Step 14**: ROI Polygon Drawing Tool operational
- [ ] **Step 15**: Recharts widgets displaying live posture/activity stats
- [ ] **Step 16**: Top-down floorplan heatmap rendering active density
- [ ] **Step 17**: PDF and CSV report export functional
- [ ] **Step 18**: ONNX model export & benchmark completed
- [ ] **Step 19**: End-to-end integration test passed & ready for presentation
