"""
The whole chain on real video (design §14.C, §14.D, §14.E): speed, alert latency, and the false alarms of man-down and
the head count on footage where nothing of the kind happens.

For each clip in sample_videos/ it runs what the upload socket runs, frame by frame: the pose model on the original frame
(SafetySession.pose_input), the tracker, the PPE model through the PPE runner, the zone and rule layer, man-down and the
head count, and the writer into a throw-away SQLite file. Nothing is sent to the dashboard or to Postgres.

What it reports per clip:
  speed           processed frames per second, end to end, and the time of each stage (pose, PPE, the safety layer)
  latency         a decision is made inside the frame that triggers it, so "event -> decision" is one frame's processing
                  time; the dashboard then shows it in the same socket message (live view) and, through the sync and
                  the 15 s poll, in the Review queue (stated, not measured here: it depends on the network)
  man-down        none of these clips shows a fall, so EVERY man-down flag is a false alarm: false alarms per hour of video
  head count      a whole-picture zone with a "no more than 2 people" and an "at least 2 people" rule: what it raised, and the
                  people it counted per second, to compare with a count by eye (written beside the clip in the document)

Run:    venv/Scripts/python.exe backend/eval/real_footage.py [--seconds 120]
Writes: backend/eval/results/real_footage.json
"""

import argparse
import asyncio
import glob
import json
import os
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
os.chdir(ROOT)
os.environ.setdefault("SAFETY_EVIDENCE_DIR", tempfile.mkdtemp(prefix="eval_evidence_"))

import cv2                                                          # noqa: E402
import torch                                                        # noqa: E402
from sqlalchemy import create_engine                                # noqa: E402

from app.cv.activity_aggregator import ActivityAggregator           # noqa: E402
from app.cv.anonymizer import PrivacyAnonymizer                     # noqa: E402
from app.cv.frame_pipeline import process_detections                # noqa: E402
from app.cv.pose_estimator import PostureEstimator                  # noqa: E402
from app.cv.spatial_engine import SpatialEngine                     # noqa: E402
from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.config import load_config                          # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.ppe_runner import PpeRunner, get_ppe_detector      # noqa: E402
from app.safety.rules import parse_rule                            # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
POSE = os.path.join(BACKEND, "yolov8m-pose.pt")
WIDTH = 640


def hardware():
    info = {"gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU)",
            "cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(), "python": platform.python_version(),
            "torch": torch.__version__, "os": f"{platform.system()} {platform.release()}"}
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
    except Exception:
        pass
    return info


async def one_clip(path, seconds, pose, ppe_detector, config):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w0, h0 = int(cap.get(3)), int(cap.get(4))
    scale = WIDTH / w0 if w0 > WIDTH else 1.0
    W, H = int(w0 * scale), int(h0 * scale)
    zones = [{"zone_id": "z", "zone_name": "Whole picture", "zone_type": "RESTRICTED", "polygon": [[0, 0], [W, 0], [W, H], [0, H]]}]
    zby = {"z": {"zone_type": "RESTRICTED", "zone_name": "Whole picture"}}
    rules = [parse_rule({"type": "MAN_DOWN", "zoneId": None, "params": {}, "severity": "CRITICAL", "name": "Man down"}, zby),
             parse_rule({"type": "OCCUPANCY", "zoneId": "z", "params": {"max_people": 2, "grace_seconds": 10}, "severity": "WARNING", "name": "At most 2"}, zby),
             parse_rule({"type": "OCCUPANCY", "zoneId": "z", "params": {"min_people": 2, "grace_seconds": 10}, "severity": "WARNING", "name": "At least 2"}, zby),
             parse_rule({"type": "PPE_ENTRY", "zoneId": "z", "params": {"required_ppe": ["helmet", "safety_vest"]}, "severity": "WARNING", "name": "PPE"}, zby)]
    tmp = tempfile.mkdtemp(prefix="eval_clip_")
    dbpath = os.path.join(tmp, "c.sqlite")
    dbe = create_engine(f"sqlite:///{dbpath}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("eval_cam", org_id="11111111-1111-4111-8111-111111111111", session_id="eval", engine=dbe, retry_interval_s=0.0)
    session = SafetySession(SafetyEngine(FrameClock(fps=fps, recorded_at="2026-11-08T04:00:00Z")), writer, config,
                            zones_config=zones, ppe_runner=PpeRunner(ppe_detector, config), rules=rules)
    spatial, act, anon = SpatialEngine(zones_config=zones), ActivityAggregator(), PrivacyAnonymizer()
    limit = int(min(total or 10 ** 9, seconds * fps)) if seconds else (total or 10 ** 9)
    stage = {"pose": [], "ppe": [], "safety": [], "frame": []}
    counts_per_s = {}
    i = 0
    while i < limit:
        ok, original = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        frame = cv2.resize(original, (W, H)) if scale < 1.0 else original.copy()
        ft = session.begin_frame(i)
        speeds = {tid: act.get_recent_motion_speed(tid) for tid in act.track_history}
        a = time.perf_counter()
        pf, isz, rs = session.pose_input(original, frame)
        dets = pose.process_frame_single_pass(pf, motion_speeds=speeds, imgsz=isz, rescale=rs)
        b = time.perf_counter()
        ppe = await session.detect_ppe(ft, dets, original, frame.shape)
        c = time.perf_counter()
        ents, _ = process_detections(dets, frame, spatial, act, anon, zones, blur_enabled=True, ground_point_zones=True, ppe_by_track=ppe)
        await session.after_frame(ft, ents, detections=dets, frame_shape=frame.shape)
        d = time.perf_counter()
        stage["pose"].append(b - a)
        stage["ppe"].append(c - b)
        stage["safety"].append(d - c)
        stage["frame"].append(d - t0)
        counts_per_s.setdefault(int(i / fps), []).append(sum(1 for e in ents if e.get("zone_id") == "z"))
        i += 1
    await session.finish()
    cap.release()
    con = sqlite3.connect(dbpath)
    con.row_factory = sqlite3.Row
    decisions = [dict(r) for r in con.execute("SELECT rule_type, status, reason, video_time_s, evidence FROM safety_decisions ORDER BY video_time_s")]
    con.close()
    dbe.dispose()
    shutil.rmtree(tmp, ignore_errors=True)
    video_s = i / fps
    md = [d for d in decisions if d["rule_type"] == "MAN_DOWN"]
    md_flags = [d for d in md if d["status"] == "POTENTIAL_VIOLATION"]
    occ = [d for d in decisions if d["rule_type"] == "OCCUPANCY"]
    ms = lambda xs: round(statistics.mean(xs) * 1000, 1) if xs else None      # noqa: E731
    p95 = lambda xs: round(sorted(xs)[int(0.95 * (len(xs) - 1))] * 1000, 1) if xs else None   # noqa: E731
    return {
        "clip": os.path.basename(path), "source_resolution": f"{w0}x{h0}", "working_resolution": f"{W}x{H}", "video_fps": round(fps, 2),
        "frames": i, "video_seconds": round(video_s, 1),
        "speed": {"processed_fps": round(i / sum(stage["frame"]), 2) if stage["frame"] else None,
                  "ms_per_frame_mean": ms(stage["frame"]), "ms_per_frame_p95": p95(stage["frame"]),
                  "pose_ms_mean": ms(stage["pose"]), "ppe_ms_mean": ms([x for x in stage["ppe"] if x > 0.002]) or ms(stage["ppe"]),
                  "ppe_every_n_frames": config.ppe_every_n_frames, "safety_layer_ms_mean": ms(stage["safety"])},
        "latency": {"event_to_decision_ms_mean": ms(stage["frame"]), "event_to_decision_ms_p95": p95(stage["frame"])},
        "man_down": {"flags": len(md_flags), "could_not_tell": len([d for d in md if d["status"] == "UNVERIFIABLE"]),
                     "false_alarms_per_hour": round(len(md_flags) / (video_s / 3600), 2) if video_s else None,
                     "flags_detail": [{"at_s": round(d["video_time_s"], 1), "reason": d["reason"]} for d in md_flags]},
        "head_count": {"people_seen_per_second": {str(k): round(statistics.median(v), 1) for k, v in sorted(counts_per_s.items())},
                       "decisions": [{"at_s": round(d["video_time_s"], 1), "status": d["status"], "outcome": json.loads(d["evidence"]).get("outcome"),
                                      "reason": d["reason"]} for d in occ]},
        "ppe_decisions": dict(__import__("collections").Counter(d["status"] for d in decisions if d["rule_type"] == "PPE_ENTRY")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120, help="at most this much of each clip (0 = all)")
    ap.add_argument("--clips", nargs="*", default=None)
    args = ap.parse_args()
    config = load_config()
    device = 0 if torch.cuda.is_available() else "cpu"
    pose = PostureEstimator(pose_model_path=POSE, conf_thresh=0.35, device=device)
    ppe_detector = get_ppe_detector(config)
    # Warm the models up first (the GPU's first frames are slow while it picks its kernels), so no clip's speed is start-up.
    import numpy as np
    blank = np.full((720, 1280, 3), 90, np.uint8)
    for _ in range(5):
        pose.process_frame_single_pass(blank, imgsz=1280)
        ppe_detector.detect(blank, imgsz=1280)
    clips = args.clips or (sorted(p for p in glob.glob(os.path.join(ROOT, "sample_videos", "*")) if p.lower().endswith((".mp4", ".avi"))
                                  and not os.path.basename(p).startswith("ppe_demo"))
                           + [p for p in [os.path.join(ROOT, "PPE-test.mp4")] if os.path.isfile(p)])
    out = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "hardware": hardware(), "seconds_per_clip": args.seconds,
           "config": {"pose_max_width": config.pose_max_width, "ppe_max_width": config.ppe_max_width, "ppe_every_n_frames": config.ppe_every_n_frames,
                      "ppe_crop_below_px": config.ppe_crop_below_px}, "clips": []}
    for path in clips:
        # warm the models up on the first frame so the first clip's speed is not the model's start-up
        r = asyncio.run(one_clip(path, args.seconds, pose, ppe_detector, config))
        out["clips"].append(r)
        print(f"{r['clip'][:48]:48s} {r['video_seconds']:6.1f} s  {r['speed']['processed_fps']} fps  man-down flags {r['man_down']['flags']}  "
              f"head-count decisions {len(r['head_count']['decisions'])}")
    total_s = sum(c["video_seconds"] for c in out["clips"])
    flags = sum(c["man_down"]["flags"] for c in out["clips"])
    out["man_down_overall"] = {"video_minutes": round(total_s / 60, 1), "false_alarms": flags,
                               "false_alarms_per_hour": round(flags / (total_s / 3600), 2) if total_s else None}
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(out, open(os.path.join(RESULTS, "real_footage.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(out["man_down_overall"]))


if __name__ == "__main__":
    main()
