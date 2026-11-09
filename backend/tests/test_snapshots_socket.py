"""
Evidence snapshots through the REAL socket (Step 9, end to end).

Plays the PPE demo video with the session's privacy blur OFF (`?blur=false`), a RESTRICTED
zone over the whole frame, the real pose and PPE models, and checks what lands on disk:

  * every POTENTIAL_VIOLATION has a picture, filed under <organisation>/<decision id>.jpg;
  * a COMPLIANT decision has none, and the folder holds exactly the flagged ones;
  * each picture is a real JPEG the size of the working frame, with the banner on top;
  * the picture's face region is blurred even though the session blur was off (the pose
    model, run on the saved picture, finds fewer face keypoints than on the same worker's
    unblurred frame);
  * the frame payload says a picture was kept; with the organisation's evidence switch off
    the decisions are still stored, with no file.

Slow (real models). Run:  venv/Scripts/python.exe backend/tests/test_snapshots_socket.py
"""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

EVIDENCE = tempfile.mkdtemp(prefix="vw_evidence_")
os.environ["SAFETY_EVIDENCE_DIR"] = EVIDENCE

import cv2                                                           # noqa: E402
import numpy as np                                                   # noqa: E402
from fastapi.testclient import TestClient                            # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.main import app                                             # noqa: E402
from app.api.routers import video_upload                             # noqa: E402
from app.db.database import SessionLocal, engine as DB_ENGINE        # noqa: E402
from app.db.models import ZoneModel                                  # noqa: E402
from app.safety import snapshots                                     # noqa: E402

FAILS = []
PROBE_ORG = "00000000-0000-0000-0000-00000000ee02"
VIDEO = os.path.join(ROOT, "sample_videos", "ppe_demo_gaps.mp4")


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


if not os.path.exists(VIDEO):
    subprocess.check_call([sys.executable, os.path.join(ROOT, "ppe_training", "make_demo_video.py"), "--gap", "4"])


async def _fake_org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _fake_org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG
switch = {"on": True}
captured = []                                              # (clean frame, people, work_shape, target, jpeg) of every picture rendered
_real_render = snapshots.render


def _capturing_render(clean, people, work_shape, **kw):
    jpeg, obscured = _real_render(clean, people, work_shape, **kw)
    captured.append((clean.copy(), [dict(p) for p in people], work_shape, kw.get("target_track"), jpeg))
    return jpeg, obscured


snapshots.render = _capturing_render
snapshots.evidence_enabled = lambda org: switch["on"]     # the organisation's switch (read from Postgres in real use)
client = TestClient(app)


def upload(camera):
    r = client.post("/api/v1/video/upload",
                    files={"file": ("snap_probe.mp4", io.BytesIO(open(VIDEO, "rb").read()), "video/mp4")},
                    data={"camera_id": camera, "recorded_at": "2026-11-08T09:30:00+05:30"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def play(sid):
    got = []
    with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false&blur=false") as ws:
        while True:
            m = ws.receive_json()
            if m.get("type") == "FRAME":
                got.append(m)
            elif m.get("type") in ("COMPLETE", "ERROR") or "error" in m:
                break
    return got


def decisions(sid, wait_s=20.0):
    deadline = time.time() + wait_s
    while time.time() < deadline:
        c = sqlite3.connect(DB_ENGINE.url.database)
        c.row_factory = sqlite3.Row
        try:
            out = [dict(r) for r in c.execute("SELECT * FROM safety_decisions WHERE session_key = ? ORDER BY video_time_s, rowid", (sid,))]
        finally:
            c.close()
        if out:
            time.sleep(1.0)
            c = sqlite3.connect(DB_ENGINE.url.database)
            c.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in c.execute("SELECT * FROM safety_decisions WHERE session_key = ? ORDER BY video_time_s, rowid", (sid,))]
            finally:
                c.close()
        time.sleep(0.5)
    return []


def cleanup(sids, zone_ids):
    s = SessionLocal()
    try:
        for sid in sids:
            s.execute(text("DELETE FROM safety_decisions WHERE session_key = :s"), {"s": sid})
            s.execute(text("DELETE FROM safety_events WHERE session_key = :s"), {"s": sid})
        for z in zone_ids:
            s.execute(text("DELETE FROM zones WHERE zone_id = :z"), {"z": z})
        for table in ("activity_logs", "zone_minute_stats"):
            try:
                s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
            except Exception:
                s.rollback()
        s.commit()
    finally:
        s.close()
    shutil.rmtree(EVIDENCE, ignore_errors=True)


def face_keypoints(model, image):
    """How many of nose / eyes / ears the pose model is confident about, summed over the people it finds."""
    res = model.predict(image, conf=0.25, verbose=False)[0]
    if res.keypoints is None or len(res.keypoints) == 0:
        return 0
    data = res.keypoints.data.cpu().numpy()
    return int((data[:, :5, 2] > 0.3).sum())


sids, zones = [], []
try:
    os.environ["SAFETY_ENABLED"] = "true"
    camera = f"snap_cam_{uuid.uuid4().hex[:6]}"
    zid = f"sz-{uuid.uuid4().hex[:6]}"
    s = SessionLocal()
    s.add(ZoneModel(zone_id=zid, org_id=PROBE_ORG, camera_id=camera, zone_name="Press line", zone_type="RESTRICTED",
                    polygon_coordinates=[[0, 0], [3000, 0], [3000, 3000], [0, 3000]]))
    s.commit(); s.close()
    zones.append(zid)

    print("1. The PPE demo video, session blur OFF, evidence on")
    sid = upload(camera); sids.append(sid)
    frames = play(sid)
    check("the session blur really was off", frames and all(f.get("privacy_blur") is False for f in frames))
    dec = decisions(sid)
    flags = [d for d in dec if d["status"] == "POTENTIAL_VIOLATION"]
    others = [d for d in dec if d["status"] != "POTENTIAL_VIOLATION"]
    print("   ", [(round(d["video_time_s"], 1), d["status"][:5], bool(d["snapshot_ref"])) for d in dec])
    check("the video produced flags and non-flags", len(flags) >= 2 and len(others) >= 1, f"{len(flags)} flags, {len(others)} others")
    check("every flag has a snapshot_ref <organisation>/<decision id>.jpg", all(d["snapshot_ref"] == f"{PROBE_ORG}/{d['id']}.jpg" for d in flags))
    check("no other decision has one", all(d["snapshot_ref"] is None for d in others))
    def stored():
        base = snapshots.evidence_dir()
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.jpg"))
    on_disk = stored()
    check("the evidence folder holds exactly the flagged pictures, nothing else", on_disk == sorted(d["snapshot_ref"] for d in flags), str(on_disk))
    first = cv2.imdecode(np.fromfile(str(snapshots.path_for_ref(flags[0]["snapshot_ref"])), np.uint8), cv2.IMREAD_COLOR)
    import base64
    sent = frames[0]["frame_base64"].split(",", 1)[1]
    sent_shape = cv2.imdecode(np.frombuffer(base64.b64decode(sent), np.uint8), cv2.IMREAD_COLOR).shape
    check("a picture is a JPEG at the camera's own resolution (not the shrunk working frame), same shape, with the banner on top",
          first is not None and first.shape[1] >= sent_shape[1] and abs(first.shape[0] / first.shape[1] - sent_shape[0] / sent_shape[1]) < 0.01
          and float(first[:30].mean()) < 50.0, f"{None if first is None else first.shape} vs {sent_shape}")
    carried = [d for f in frames for d in f.get("safety", {}).get("decisions", [])]
    by_id = {d["id"]: d for d in carried}
    check("the payload told the dashboard which flags kept a picture", all(by_id[d["id"]].get("snapshot") is True for d in flags))
    check("...and carries no 'snapshot' key for a compliant decision", all("snapshot" not in by_id[d["id"]] for d in others if d["status"] == "COMPLIANT"))
    check("the reason sentence is on the picture's banner area (never an accusation)", all("wearing" not in d["reason"].lower() for d in flags))

    keep = os.getenv("KEEP_EVIDENCE_SAMPLE")                 # copy a few pictures out to look at them
    if keep:
        os.makedirs(keep, exist_ok=True)
        for n, d in enumerate(flags[:6]):
            shutil.copy(str(snapshots.path_for_ref(d["snapshot_ref"])), os.path.join(keep, f"flag{n}.jpg"))

    print("2. The face in the saved picture is blurred although the session blur was off")
    from app.cv.anonymizer import PrivacyAnonymizer

    def sharpness(img, box):
        x1, y1, x2, y2 = [max(0, int(v)) for v in box]
        region = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(region, cv2.CV_64F).var()) if region.size > 100 else None

    rows_ = []
    for clean, people, work_shape, target, jpeg in captured:
        # The same inputs without the overlays (box lines, labels, banner), so the measurement is of the
        # blur alone and not of lines drawn over the face afterwards.
        plain, _ = _real_render(clean, people, work_shape, target_track=target)
        pic = cv2.imdecode(np.frombuffer(plain, np.uint8), cv2.IMREAD_COLOR)
        target_person = next((p for p in people if p.get("track_id") == target), None)
        if target_person is None or pic is None:
            continue
        box = PrivacyAnonymizer.face_box(target_person["keypoints"], target_person["bbox"])
        if box is None:
            continue
        k = pic.shape[1] / float(work_shape[1])
        scaled = [v * k for v in box]
        ref = cv2.resize(clean, (pic.shape[1], pic.shape[0]), interpolation=cv2.INTER_AREA)
        before_s, after_s = sharpness(ref, scaled), sharpness(pic, scaled)
        if before_s and after_s is not None:
            rows_.append((before_s, after_s))
    for b_, a_ in rows_:
        print(f"    face region sharpness {b_:.1f} -> {a_:.1f}")
    check("pictures with a located face were checked", len(rows_) >= 5, str(len(rows_)))
    check("in every one the face region is far less sharp than in the clean frame (the blur ran although the session blur was off)",
          rows_ and all(a_ < 0.35 * b_ for b_, a_ in rows_), str([(round(b_), round(a_)) for b_, a_ in rows_ if not a_ < 0.35 * b_]))

    print("3. Evidence switched off for the organisation")
    switch["on"] = False
    before = set(on_disk)
    sid2 = upload(camera); sids.append(sid2)
    frames2 = play(sid2)
    dec2 = decisions(sid2)
    after = stored()
    check("decisions are still stored, flags included", any(d["status"] == "POTENTIAL_VIOLATION" for d in dec2))
    check("but no file was written and no snapshot_ref is set", set(after) == before and all(d["snapshot_ref"] is None for d in dec2))
    carried2 = [d for f in frames2 for d in f.get("safety", {}).get("decisions", [])]
    check("the payload says no picture was kept for the flags", all(d.get("snapshot") is False for d in carried2 if d["status"] == "POTENTIAL_VIOLATION"))
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    cleanup(sids, zones)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
