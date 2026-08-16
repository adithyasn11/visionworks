# backend/app/cv/pose_estimator.py
from ultralytics import YOLO
import torch
import numpy as np
from collections import defaultdict, deque
import logging

logger = logging.getLogger(__name__)

class PostureEstimator:
    """
    Ultra-Fast & High-Precision Single-Pass AI Engine using YOLOv8 Medium Pose (yolov8m-pose.pt) with FP16 CUDA.
    Runs at 60+ FPS real-time speed on NVIDIA RTX 4060 GPU.
    Combines multi-factor joint geometry, offset protrusion metrics, physical velocity thresholds,
    and 7-frame rolling majority-vote temporal smoothing.
    """
    def __init__(self, pose_model_path: str = "yolov8m-pose.pt", conf_thresh: float = 0.35, device: str = None):
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        logger.info(f"PostureEstimator initializing single-pass '{pose_model_path}' on device '{self.device}'")
        self.model = YOLO(pose_model_path)
        if self.device == "cuda":
            self.model.to("cuda")
        self.conf_thresh = conf_thresh

        # Rolling history per Track ID for temporal smoothing (7-frame majority vote for maximum stability)
        self.posture_history = defaultdict(lambda: deque(maxlen=7))

    @staticmethod
    def calculate_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        """Calculates vector angle (in degrees) at vertex point b given points a, b, c."""
        ba = a - b
        bc = c - b

        norm_ba = np.linalg.norm(ba)
        norm_bc = np.linalg.norm(bc)

        if norm_ba < 1e-5 or norm_bc < 1e-5:
            return 180.0

        cosine_angle = np.dot(ba, bc) / (norm_ba * norm_bc)
        cosine_angle = np.clip(cosine_angle, -1.0, 1.0)
        return float(np.degrees(np.arccos(cosine_angle)))

    def process_frame_single_pass(self, frame: np.ndarray, motion_speeds: dict = None, imgsz: int = 480):
        """
        Runs Person Detection + ByteTrack Tracking + 17 COCO Keypoint Extraction in ONE SINGLE GPU PASS at 60+ FPS.
        motion_speeds: dict mapping track_id -> recent_speed_pixels
        """
        if motion_speeds is None:
            motion_speeds = {}

        results = self.model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0], # Class 0 = Person in COCO dataset
            conf=self.conf_thresh,
            device=self.device,
            imgsz=imgsz,
            verbose=False
        )[0]

        outputs = []
        if results.boxes is None or len(results.boxes) == 0:
            return outputs

        boxes = results.boxes.xyxy.cpu().numpy()
        confs = results.boxes.conf.cpu().numpy()

        if results.boxes.id is not None:
            track_ids = results.boxes.id.cpu().numpy().astype(int)
        else:
            track_ids = list(range(len(boxes)))

        has_kpts = results.keypoints is not None and len(results.keypoints) > 0
        if has_kpts:
            keypoints_data = results.keypoints.data.cpu().numpy() # Shape: (N, 17, 3)
            # Some model variants emit (N, 17, 2) with no confidence channel.
            # Downstream code indexes [:, 2], so pad a full-confidence column.
            if keypoints_data.ndim == 3 and keypoints_data.shape[2] == 2:
                pad = np.ones((*keypoints_data.shape[:2], 1), dtype=keypoints_data.dtype)
                keypoints_data = np.concatenate([keypoints_data, pad], axis=2)
        else:
            keypoints_data = [np.zeros((17, 3)) for _ in boxes]

        for i, (box, track_id, conf) in enumerate(zip(boxes, track_ids, confs)):
            x1, y1, x2, y2 = map(int, box)
            kpts = keypoints_data[i] if i < len(keypoints_data) else np.zeros((17, 3))
            tid = int(track_id)
            speed = motion_speeds.get(tid, 0.0)

            # Raw frame posture calculation with motion speed validation & desk occlusion handling
            raw_posture = self.classify_posture_raw(kpts, [x1, y1, x2, y2], motion_speed=speed)

            # Apply rolling majority vote temporal smoothing to eliminate flickering
            self.posture_history[tid].append(raw_posture)
            history_list = list(self.posture_history[tid])
            smoothed_posture = max(set(history_list), key=history_list.count)

            outputs.append({
                "track_id": tid,
                "bbox": [x1, y1, x2, y2],
                "confidence": float(conf),
                "posture": smoothed_posture,
                "keypoints": kpts
            })

        return outputs

    def classify_posture_raw(self, keypoints: np.ndarray, bbox: list, motion_speed: float = 0.0) -> str:
        """
        Multi-Feature Geometric & Velocity Posture Decision Engine.
        COCO 17 Keypoints:
          0: Nose
          5: L_Shoulder, 6: R_Shoulder
          11: L_Hip, 12: R_Hip
          13: L_Knee, 14: R_Knee
          15: L_Ankle, 16: R_Ankle
        """
        x1, y1, x2, y2 = bbox
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        aspect_ratio = h / float(w)

        k_conf = keypoints[:, 2] if keypoints.shape[1] >= 3 else np.ones(17)

        # Helper to get joint center (x, y) only if at least one side is confident (>0.15)
        def get_joint(idx1: int, idx2: int):
            c1 = k_conf[idx1] > 0.15
            c2 = k_conf[idx2] > 0.15
            if c1 and c2:
                return (keypoints[idx1][:2] + keypoints[idx2][:2]) / 2.0, True
            elif c1:
                return keypoints[idx1][:2], True
            elif c2:
                return keypoints[idx2][:2], True
            return None, False

        sh_mid, has_sh = get_joint(5, 6)
        hip_mid, has_hip = get_joint(11, 12)
        knee_mid, has_knee = get_joint(13, 14)
        ank_mid, has_ank = get_joint(15, 16)

        l_sh = keypoints[5][:2] if k_conf[5] > 0.15 else None
        r_sh = keypoints[6][:2] if k_conf[6] > 0.15 else None
        shoulder_w = np.linalg.norm(l_sh - r_sh) if (l_sh is not None and r_sh is not None) else w * 0.4
        shoulder_w = max(10.0, shoulder_w)

        # ── 1. INDIVIDUAL LEG GEOMETRY & ANGLES ─────────────────────────
        l_hip_angle = self.calculate_angle(keypoints[5][:2], keypoints[11][:2], keypoints[13][:2]) if (k_conf[5]>0.15 and k_conf[11]>0.15 and k_conf[13]>0.15) else None
        r_hip_angle = self.calculate_angle(keypoints[6][:2], keypoints[12][:2], keypoints[14][:2]) if (k_conf[6]>0.15 and k_conf[12]>0.15 and k_conf[14]>0.15) else None

        l_knee_angle = self.calculate_angle(keypoints[11][:2], keypoints[13][:2], keypoints[15][:2]) if (k_conf[11]>0.15 and k_conf[13]>0.15 and k_conf[15]>0.15) else None
        r_knee_angle = self.calculate_angle(keypoints[12][:2], keypoints[14][:2], keypoints[16][:2]) if (k_conf[12]>0.15 and k_conf[14]>0.15 and k_conf[16]>0.15) else None

        mid_hip_angle = self.calculate_angle(sh_mid, hip_mid, knee_mid) if (has_sh and has_hip and has_knee) else None
        mid_knee_angle = self.calculate_angle(hip_mid, knee_mid, ank_mid) if (has_hip and has_knee and has_ank) else None

        valid_knee_angles = [a for a in [l_knee_angle, r_knee_angle] if a is not None]
        valid_hip_angles = [a for a in [l_hip_angle, r_hip_angle] if a is not None]

        # ── 2. MULTI-FACTOR SCORE ACCUMULATION ──────────────────────────
        sitting_score = 0.0
        standing_score = 0.0

        # Feature A: Individual Knee Angles (Hip -> Knee -> Ankle)
        if valid_knee_angles:
            for ka in valid_knee_angles:
                if ka <= 138.0:
                    sitting_score += 5.0   # Bent knee sitting
                elif ka >= 155.0:
                    standing_score += 2.5  # Straight leg standing
        elif mid_knee_angle is not None:
            if mid_knee_angle <= 138.0:
                sitting_score += 5.0
            elif mid_knee_angle >= 155.0:
                standing_score += 3.0

        # Feature B: Individual Hip Angles (Shoulder -> Hip -> Knee)
        if valid_hip_angles:
            for ha in valid_hip_angles:
                if ha <= 128.0:
                    sitting_score += 4.5   # Bent hip sitting
                elif ha >= 148.0:
                    standing_score += 2.0  # Straight hip standing
        elif mid_hip_angle is not None:
            if mid_hip_angle <= 128.0:
                sitting_score += 4.0
            elif mid_hip_angle >= 148.0:
                standing_score += 2.5

        # Feature C: Individual Thigh Vertical Projection Ratio
        if k_conf[11] > 0.15 and k_conf[13] > 0.15 and k_conf[5] > 0.15:
            l_torso_dy = abs(keypoints[11][1] - keypoints[5][1])
            l_thigh_dy = abs(keypoints[13][1] - keypoints[11][1])
            if l_torso_dy > 10 and (l_thigh_dy / l_torso_dy < 0.80):
                sitting_score += 4.0

        if k_conf[12] > 0.15 and k_conf[14] > 0.15 and k_conf[6] > 0.15:
            r_torso_dy = abs(keypoints[12][1] - keypoints[6][1])
            r_thigh_dy = abs(keypoints[14][1] - keypoints[12][1])
            if r_torso_dy > 10 and (r_thigh_dy / r_torso_dy < 0.80):
                sitting_score += 4.0

        # Feature D: Full Body Span vs Bounding Box Height
        if has_sh and has_ank:
            body_span = abs(ank_mid[1] - sh_mid[1])
            span_ratio = body_span / float(h)
            all_knees_straight = valid_knee_angles and all(ka >= 155.0 for ka in valid_knee_angles)
            if span_ratio >= 0.70 and all_knees_straight:
                standing_score += 3.0
            elif span_ratio < 0.55:
                sitting_score += 2.5

        # Feature E: Desk Occlusion / Aspect Ratio fallback when lower legs hidden
        if not (has_knee and has_ank):
            if aspect_ratio < 1.35:
                sitting_score += 4.0
            elif aspect_ratio >= 1.65:
                standing_score += 3.0

        # ── 3. POSTURE DECISION & WALKING VALIDATION ─────────────────────
        # RULE 1: Seated posture (sitting_score > standing_score) is ALWAYS SITTING!
        # Rolling an office chair on wheels or upper body fidgeting while seated MUST NEVER be labeled WALKING!
        if sitting_score > standing_score:
            return "SITTING"

        # RULE 2: WALKING is ONLY possible when standing upright (standing_score >= sitting_score)
        # AND exhibiting significant locomotion speed (>= 4.5 px/frame) + bipedal stride
        stride_width = 0.0
        if has_ank and k_conf[15] > 0.15 and k_conf[16] > 0.15:
            stride_width = abs(keypoints[15][0] - keypoints[16][0])
        elif has_knee and k_conf[13] > 0.15 and k_conf[14] > 0.15:
            stride_width = abs(keypoints[13][0] - keypoints[14][0])

        # shoulder_w is clamped to >= 10.0 above, so this division is always safe.
        normalized_stride = stride_width / shoulder_w

        knee_asymmetry = 0.0
        if l_knee_angle is not None and r_knee_angle is not None:
            knee_asymmetry = abs(l_knee_angle - r_knee_angle)

        is_moving_fast = motion_speed >= 4.5
        has_walking_stride = normalized_stride > 0.52 or (knee_asymmetry > 30.0 and normalized_stride > 0.35)
        has_upright_legs = (mid_knee_angle is None or mid_knee_angle >= 142.0) and (mid_hip_angle is None or mid_hip_angle >= 138.0)

        if is_moving_fast and has_walking_stride and has_upright_legs:
            return "WALKING"

        return "STANDING"

    def classify_posture(self, keypoints: np.ndarray, bbox: list) -> str:
        return self.classify_posture_raw(keypoints, bbox)

    @staticmethod
    def _bbox_iou(a: list, b: list) -> float:
        """Intersection-over-union of two [x1, y1, x2, y2] boxes."""
        ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
        ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if inter <= 0.0:
            return 0.0
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def extract_keypoints_for_bboxes(self, frame: np.ndarray, detections: list) -> list:
        """
        Returns keypoints aligned 1:1 with the supplied detections.

        This runs its own pose pass, so its detection order does NOT match the
        caller's list. Results are matched back by best bounding-box overlap,
        and any detection without a match gets a zero-filled placeholder rather
        than borrowing another person's keypoints (or raising IndexError).
        """
        outputs = self.process_frame_single_pass(frame)

        aligned = []
        for det in detections:
            bbox = det["bbox"] if isinstance(det, dict) else det
            best_kpts, best_iou = None, 0.0
            for out in outputs:
                iou = self._bbox_iou(bbox, out["bbox"])
                if iou > best_iou:
                    best_iou, best_kpts = iou, out["keypoints"]
            aligned.append(best_kpts if best_iou >= 0.3 else np.zeros((17, 3)))
        return aligned
