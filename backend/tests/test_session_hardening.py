"""
Hardening of the video sockets (found by the Step 1-10 test pass).

Runs the REAL app under uvicorn on a local port (a TestClient cancels tasks on disconnect and so
hides some of these) and checks:

  * the live camera sockets refuse a caller with no verified organisation (they used to stream the
    server's own camera to anyone), and the opt-in flag is off by default;
  * a client that disconnects while PAUSED ends the session: it is marked done, its upload is
    deleted, nothing is left running;
  * a file that is not a readable video gives a clean error with no server path in it, and the
    session and its file are removed (they used to stay for ever);
  * a very long filename is shortened, not a 500;
  * a normal run still completes.

    venv/Scripts/python.exe backend/tests/test_session_hardening.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

EVIDENCE = tempfile.mkdtemp(prefix="vw_hard_")
os.environ["SAFETY_EVIDENCE_DIR"] = EVIDENCE
os.environ["SAFETY_ENABLED"] = "true"
os.environ.pop("VISIONWORKS_STANDALONE_LIVE", None)

import requests                                                      # noqa: E402
import urllib3                                                       # noqa: E402
import uvicorn                                                       # noqa: E402
import websockets                                                    # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.main import app                                             # noqa: E402
from app.api.routers import video_upload                             # noqa: E402
from app.db.database import SessionLocal                             # noqa: E402

urllib3.disable_warnings()
FAILS = []
PROBE_ORG = "00000000-0000-0000-0000-00000000ee03"
VIDEO = os.path.join(ROOT, "sample_videos", "ppe_demo_gaps.mp4")


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# The socket's organisation: a verified manager of the probe organisation, unless a test says "nobody".
who = {"org": PROBE_ORG}


async def _org(_websocket):
    return (who["org"], "ADMIN") if who["org"] else (None, None)

video_upload._resolve_session_org = _org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG

PORT = 28811
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)
BASE, WS = f"http://127.0.0.1:{PORT}", f"ws://127.0.0.1:{PORT}"


def upload(name, data, camera="hardening_cam"):
    return requests.post(f"{BASE}/api/v1/video/upload", files={"file": (name, data, "video/mp4")}, data={"camera_id": camera})


def run(coro):
    return asyncio.run(coro)


def cleanup(sids):
    s = SessionLocal()
    try:
        for sid in sids:
            s.execute(text("DELETE FROM safety_decisions WHERE session_key = :s"), {"s": sid})
            s.execute(text("DELETE FROM safety_events WHERE session_key = :s"), {"s": sid})
        for table in ("activity_logs", "zone_minute_stats"):
            try:
                s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
            except Exception:
                s.rollback()
        s.commit()
    finally:
        s.close()
    shutil.rmtree(EVIDENCE, ignore_errors=True)


sids = []
try:
    print("=" * 66)
    print("VIDEO SOCKET HARDENING (real server)")
    print("=" * 66)

    print()
    print("1. The live camera sockets and unverified callers")
    check("standalone live is OFF by default", video_upload.standalone_live_allowed() is False)
    os.environ["VISIONWORKS_STANDALONE_LIVE"] = "true"
    check("...and can be switched on explicitly", video_upload.standalone_live_allowed() is True)
    os.environ.pop("VISIONWORKS_STANDALONE_LIVE")

    async def unauth(path):
        who["org"] = None
        try:
            async with websockets.connect(f"{WS}{path}") as ws:
                got = []
                try:
                    while True:
                        got.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
                except websockets.ConnectionClosed as closed:
                    return got, closed.rcvd.code if closed.rcvd else None
        finally:
            who["org"] = PROBE_ORG

    for path in ("/api/v1/video/live_webcam?camera_id=x", "/api/v1/video/process_webcam_frame?camera_id=x"):
        got, code = run(unauth(path))
        check(f"{path.split('?')[0].rsplit('/', 1)[1]} with nobody signed in: refused with a reason, closed 1008, no frames",
              got and "Sign in" in got[0].get("error", "") and code == 1008 and not any(m.get("type") == "FRAME" for m in got), str((got[:1], code)))

    print()
    print("2. Disconnecting while paused ends the session")
    r = upload("pause_probe.mp4", open(VIDEO, "rb").read())
    sid = r.json()["session_id"]; sids.append(sid)
    path_on_disk = video_upload._active_sessions[sid]["path"]

    async def pause_and_leave():
        async with websockets.connect(f"{WS}/api/v1/video/process/{sid}?realtime=false", max_size=None) as ws:
            n = 0
            while n < 30:
                m = json.loads(await ws.recv())
                n += m.get("type") == "FRAME"
            await ws.send(json.dumps({"action": "pause"}))
            await asyncio.sleep(0.5)
        # the socket is closed here, with the session paused

    run(pause_and_leave())
    for _ in range(80):
        if sid not in video_upload._active_sessions or video_upload._active_sessions[sid]["status"] == "DONE":
            break
        time.sleep(0.25)
    status = video_upload._active_sessions.get(sid, {}).get("status", "removed")
    check("the session is marked DONE within a few seconds (it used to stay PROCESSING for ever)", status in ("DONE", "removed"), status)
    time.sleep(0.5)
    check("...and its upload file was deleted", not os.path.exists(path_on_disk))

    print()
    print("3. Files that are not videos")
    for label, data in (("random bytes", os.urandom(4096)), ("zero bytes", b""), ("text", b"this is not a video\n" * 20)):
        r = upload("bad.mp4", data)
        check(f"{label}: uploaded (the format is checked when it is opened)", r.status_code == 200, r.text[:80])
        sid_bad = r.json()["session_id"]; sids.append(sid_bad)
        bad_path = video_upload._active_sessions[sid_bad]["path"]

        async def open_bad():
            async with websockets.connect(f"{WS}/api/v1/video/process/{sid_bad}?realtime=false") as ws:
                out = []
                try:
                    while True:
                        out.append(json.loads(await asyncio.wait_for(ws.recv(), 10)))
                except websockets.ConnectionClosed:
                    return out

        msgs = run(open_bad())
        err = msgs[0].get("error", "") if msgs else ""
        check(f"{label}: a clear error with no server path in it", "video" in err.lower() and ROOT not in err and "sample_videos" not in err, err)
        time.sleep(0.3)
        check(f"{label}: the session is forgotten and its file removed", sid_bad not in video_upload._active_sessions and not os.path.exists(bad_path))

    print()
    print("4. A very long filename")
    r = upload("a" * 400 + ".mp4", open(VIDEO, "rb").read(2000))
    check("a 400-character name is shortened, not a 500", r.status_code == 200 and len(r.json().get("filename", "x" * 200)) <= 130, f"{r.status_code} {r.text[:80]}")
    if r.status_code == 200:
        sids.append(r.json()["session_id"])
        video_upload._discard_session(r.json()["session_id"])
    r = upload("clip.exe", b"x")
    check("a wrong extension is a 400 whose message reads as words, not a Python tuple", r.status_code == 400 and "(" not in r.json()["detail"], r.text[:120])

    print()
    print("5. A normal run still completes")
    r = upload("normal.mp4", open(VIDEO, "rb").read())
    sid_ok = r.json()["session_id"]; sids.append(sid_ok)

    async def full():
        frames, last = 0, None
        async with websockets.connect(f"{WS}/api/v1/video/process/{sid_ok}?realtime=false", max_size=None) as ws:
            while True:
                m = json.loads(await ws.recv())
                if m.get("type") == "FRAME":
                    frames += 1
                last = m.get("type") or "error"
                if last in ("COMPLETE", "ERROR") or "error" in m:
                    return frames, last

    frames, last = run(full())
    check("all frames processed and COMPLETE", frames == 1080 and last == "COMPLETE", f"{frames} {last}")
finally:
    server.should_exit = True
    time.sleep(1.0)
    cleanup(sids)
    for f in os.listdir(video_upload.SAMPLE_VIDEOS_DIR):
        if any(f.startswith(s + "_") for s in sids):
            try:
                os.remove(os.path.join(video_upload.SAMPLE_VIDEOS_DIR, f))
            except OSError:
                pass

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
