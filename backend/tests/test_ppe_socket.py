"""
PPE through the REAL processing socket (Step 5, end to end).

Plays the first part of the sample clip with the real pose model and the real
helmet/vest model, and checks what the browser would receive: every person
carries a verdict, the payload stays plain JSON, the model-missing case says so,
and with the safety layer off nothing changes. Also prints how fast frames go
with pose + PPE (the figure for the evaluation write-up).

Slow. Run:  venv/Scripts/python.exe backend/tests/test_ppe_socket.py
"""

import io
import json
import os
import sys
import time

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient                            # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.main import app                                             # noqa: E402
from app.api.routers import video_upload                             # noqa: E402
from app.db.database import SessionLocal                             # noqa: E402
from app.safety import ppe_runner as pr                              # noqa: E402

FAILS = []
PROBE_ORG = "00000000-0000-0000-0000-00000000dddd"
CLIP = os.path.join(ROOT, "sample_videos", "vtest.avi")
FRAMES = int(os.environ.get("PPE_FRAMES", "300"))


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


async def _fake_org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _fake_org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG
client = TestClient(app)


def upload():
    r = client.post("/api/v1/video/upload",
                    files={"file": ("ppe_probe.avi", io.BytesIO(open(CLIP, "rb").read()), "video/x-msvideo")})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def play(sid, frames=FRAMES):
    got, started = [], time.time()
    with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false") as ws:
        while len(got) < frames:
            msg = ws.receive_json()
            if msg.get("type") == "FRAME":
                got.append(msg)
            elif msg.get("type") in ("COMPLETE", "ERROR") or "error" in msg:
                return got, time.time() - started
        video_upload._active_sessions[sid]["cancel"] = True
        for _ in range(50):
            msg = ws.receive_json()
            if msg.get("type") == "FRAME":
                got.append(msg)
            elif msg.get("type") in ("COMPLETE", "ERROR") or "error" in msg:
                break
    return got, time.time() - started


def cleanup(sids):
    s = SessionLocal()
    try:
        for sid in sids:
            s.execute(text("DELETE FROM safety_events WHERE session_key = :s"), {"s": sid})
        for table in ("activity_logs", "zone_minute_stats"):
            try:
                s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
            except Exception:
                s.rollback()
        s.commit()
    finally:
        s.close()


sids = []
saved = os.environ.get("PPE_MODEL_PATH")
try:
    if not os.path.exists(CLIP):
        raise FileNotFoundError(CLIP)

    print("1. Safety on, real models")
    os.environ["SAFETY_ENABLED"] = "true"
    pr._detector, pr._detector_failed_for = None, None
    sid = upload(); sids.append(sid)
    frames, secs = play(sid)
    with_people = [f for f in frames if f["tracked_entities"]]
    print(f"   {len(frames)} frames in {secs:.1f} s ({len(frames) / secs:.2f} frames/s incl. model load); people in {len(with_people)} frames")
    from collections import Counter
    print("   verdicts seen:", dict(Counter(e["ppe"]["status"] for f in with_people for e in f["tracked_entities"] if "ppe" in e)),
          "| clear person-frames:", sum(1 for f in with_people for e in f["tracked_entities"] if e.get("ppe", {}).get("clear")))
    check("people were detected", len(with_people) > 0)
    check("every person on every frame carries a ppe verdict",
          all("ppe" in e for f in with_people for e in f["tracked_entities"]))
    allowed = {"COMPLIANT", "NON_COMPLIANT", "UNVERIFIABLE"}
    ents = [e for f in with_people for e in f["tracked_entities"]]
    check("verdicts are one of the three", all(e["ppe"]["status"] in allowed for e in ents))
    check("each verdict has the fields the UI reads",
          all(set(e["ppe"]) == {"status", "helmet", "vest", "clear", "helmet_state", "vest_state"} for e in ents))
    check("with the real weights at least one person reaches a real verdict (not everyone stays unverified)",
          any(e["ppe"]["status"] != "UNVERIFIABLE" for e in ents))
    check("nobody is judged on the first frames they appear in (evidence needs time)",
          all(e["ppe"]["status"] == "UNVERIFIABLE" for f in with_people[:3] for e in f["tracked_entities"]))
    check("no 'model missing' notice when the model is there", all("ppe_unavailable" not in f["safety"] for f in frames))
    check("the payload is plain JSON", bool(json.dumps(frames[-1]["tracked_entities"])))
    check("the frame is still sent (PPE does not block the video)", all(f.get("frame_base64") for f in frames))

    print("2. The helmet/vest model is missing")
    os.environ["PPE_MODEL_PATH"] = os.path.join(ROOT, "no_such_weights.pt")
    pr._detector, pr._detector_failed_for = None, None
    sid2 = upload(); sids.append(sid2)
    frames2, _ = play(sid2, 60)
    ents2 = [e for f in frames2 for e in f["tracked_entities"]]
    check("the session still runs and people are still tracked", len(frames2) >= 60 and len(ents2) > 0)
    check("every verdict is UNVERIFIABLE", ents2 and all(e["ppe"]["status"] == "UNVERIFIABLE" for e in ents2))
    check("the payload says why", all(f["safety"].get("ppe_unavailable") == "PPE model not loaded" for f in frames2))

    print("3. Safety off")
    os.environ.pop("SAFETY_ENABLED", None)
    if saved is None:
        os.environ.pop("PPE_MODEL_PATH", None)
    else:
        os.environ["PPE_MODEL_PATH"] = saved
    sid3 = upload(); sids.append(sid3)
    frames3, _ = play(sid3, 40)
    check("no ppe key on any entity and no safety block", all("ppe" not in e for f in frames3 for e in f["tracked_entities"])
          and all("safety" not in f for f in frames3))
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    if saved is None:
        os.environ.pop("PPE_MODEL_PATH", None)
    else:
        os.environ["PPE_MODEL_PATH"] = saved
    cleanup(sids)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
