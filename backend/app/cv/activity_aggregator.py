# backend/app/cv/activity_aggregator.py
import time
from collections import defaultdict, deque
import numpy as np
import logging

logger = logging.getLogger(__name__)

class ActivityAggregator:
    """
    Telemetry and Activity Score Index Calculator over rolling time windows.
    Tracks dwell duration, posture transitions, and keypoint displacement variance.
    """
    def __init__(self, history_window_seconds: int = 900, max_positions_history: int = 30):
        self.history_window = history_window_seconds
        self.max_positions = max_positions_history
        self.track_history = defaultdict(lambda: {
            "first_seen": time.time(),
            "last_seen": time.time(),
            "positions": deque(maxlen=self.max_positions),
            "postures": deque(maxlen=self.max_positions),
            "posture_shifts": 0,
            "last_posture": None
        })

    def update_track(self, track_id: int, centroid: list, posture: str):
        """Updates positional queue and posture state transitions for a track ID"""
        now = time.time()
        track = self.track_history[track_id]
        track["last_seen"] = now
        track["positions"].append(centroid)

        if track["last_posture"] and track["last_posture"] != posture:
            track["posture_shifts"] += 1

        track["last_posture"] = posture
        track["postures"].append(posture)

    def get_recent_motion_speed(self, track_id: int) -> float:
        """Returns average frame-to-frame centroid displacement (in pixels) over recent frames"""
        if track_id not in self.track_history:
            return 0.0
        positions = list(self.track_history[track_id]["positions"])
        if len(positions) < 3:
            return 0.0
        pts = np.array(positions[-6:])
        diffs = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        return float(np.mean(diffs))

    def calculate_activity_score(self, track_id: int) -> float:
        """
        Calculates Physical Activity Index (0 to 100) based on movement variance
        and posture transition frequency.
        """
        if track_id not in self.track_history:
            return 50.0

        track = self.track_history[track_id]
        positions = np.array(track["positions"])

        if len(positions) < 2:
            return 50.0

        motion_std = float(np.std(positions, axis=0).mean())
        motion_score = min(100.0, (motion_std / 15.0) * 100.0)
        shift_bonus = min(25.0, track["posture_shifts"] * 8.0)

        final_score = (0.75 * motion_score) + shift_bonus
        return round(float(np.clip(final_score, 0.0, 100.0)), 2)

    def get_dwell_time_seconds(self, track_id: int) -> int:
        """Returns total active dwell time in seconds for track ID"""
        if track_id not in self.track_history:
            return 0
        track = self.track_history[track_id]
        return int(track["last_seen"] - track["first_seen"])

    def cleanup_stale_tracks(self, max_idle_seconds: int = 300):
        """Purges tracks that have not been seen for max_idle_seconds"""
        now = time.time()
        stale_ids = [
            tid for tid, data in self.track_history.items()
            if (now - data["last_seen"]) > max_idle_seconds
        ]
        for tid in stale_ids:
            del self.track_history[tid]
