"""
A live camera that stops, over the real socket (Step 18).

Runs the real app under uvicorn with a stand-in camera that gives pictures, then none for a while, then pictures again:

  * after SAFETY_CAMERA_LOST_S without a picture the page is told once ("CAMERA_LOST"), not on every empty read;
  * the socket stays open, and when pictures come back frames flow again (with the safety payload);
  * a camera that never comes back still ends cleanly when the page says stop;
  * the camera is released.

    venv/Scripts/python.exe backend/tests/test_camera_lost_socket.py
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

EVIDENCE = tempfile.mkdtemp(prefix="vw_lost_")
os.environ["SAFETY_EVIDENCE_DIR"] = EVIDENCE
os.environ["SAFETY_ENABLED"] = "true"
os.environ["SAFETY_CAMERA_LOST_S"] = "1.5"
os.environ.pop("VISIONWORKS_STANDALONE_LIVE", None)

import cv2                                                           # noqa: E402
import uvicorn                                                       # noqa: E402
import websockets                                                    # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.api.routers import video_upload                             # noqa: E402
from app.db.database import SessionLocal                             # noqa: E402
from app.main import app                                             # noqa: E402

FAILS = []
PROBE_ORG = "00000000-0000-0000-0000-00000000ee18"
VIDEO = os.path.join(ROOT, "sample_videos", "ppe_demo_gaps.mp4")


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


async def _org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _org
video_upload.sync_safety_in_background = lambda: None               # nothing of this test goes to Postgres

REAL_CAPTURE = cv2.VideoCapture
state = {"phase": "pictures", "released": False, "opened": 0}


class FakeCamera:
    """Gives frames of a real video while state['phase'] is 'pictures', and nothing while it is 'dark'."""

    def __init__(self, *args, **kwargs):
        state["opened"] += 1
        self._real = REAL_CAPTURE(VIDEO)

    def isOpened(self):
        return True

    def read(self):
        if state["phase"] == "dark":
            time.sleep(0.01)
            return False, None
        ok, frame = self._real.read()
        if not ok:
            self._real.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._real.read()
        time.sleep(0.03)
        return ok, frame

    def get(self, *a):
        return self._real.get(*a)

    def set(self, *a):
        return self._real.set(*a)

    def release(self):
        state["released"] = True
        self._real.release()

    def grab(self):
        return self.read()[0]


cv2.VideoCapture = FakeCamera

PORT = 28818
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)
WS = f"ws://127.0.0.1:{PORT}"


async def collect(ws, seconds):
    got, end = [], time.time() + seconds
    while time.time() < end:
        try:
            got.append(json.loads(await asyncio.wait_for(ws.recv(), max(0.05, end - time.time()))))
        except asyncio.TimeoutError:
            break
    return got


def cleanup():
    s = SessionLocal()
    try:
        for table in ("safety_decisions", "safety_events"):
            s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()
    shutil.rmtree(EVIDENCE, ignore_errors=True)


async def scenario():
    state.update(phase="pictures", released=False)
    async with websockets.connect(f"{WS}/api/v1/video/live_webcam?camera_id=lost_cam", max_size=None) as ws:
        first = []
        deadline = time.time() + 30                                       # the first frame waits for the models to load
        while time.time() < deadline and not any(m.get("type") == "FRAME" for m in first):
            first += await collect(ws, 1.0)
        first += await collect(ws, 2)
        check("pictures flow at first, with the safety payload",
              any(m.get("type") == "FRAME" for m in first) and any("safety" in m for m in first if m.get("type") == "FRAME"), str([m.get("type") for m in first][:5]))
        check("no CAMERA_LOST while pictures arrive", not any(m.get("type") == "CAMERA_LOST" for m in first))

        state["phase"] = "dark"
        dark = await collect(ws, 4.5)
        lost = [m for m in dark if m.get("type") == "CAMERA_LOST"]
        check("after the silence the page is told the camera stopped, exactly once", len(lost) == 1, str([m.get("type") for m in dark]))
        check("the message is plain and says what happened to open stays", lost and "stopped" in lost[0]["message"] and "not verifiable" in lost[0]["message"], str(lost))

        state["phase"] = "pictures"
        back = await collect(ws, 4)
        check("the socket stayed open and frames flow again when the camera returns", sum(1 for m in back if m.get("type") == "FRAME") >= 3, str([m.get("type") for m in back][:6]))
        check("no second CAMERA_LOST while it is back", not any(m.get("type") == "CAMERA_LOST" for m in back))

        state["phase"] = "dark"
        again = await collect(ws, 3.5)
        check("a second stop is reported again (once more)", sum(1 for m in again if m.get("type") == "CAMERA_LOST") == 1, str([m.get("type") for m in again]))

        await ws.send(json.dumps({"action": "stop"}))
        try:
            await asyncio.wait_for(ws.wait_closed(), 8)
        except asyncio.TimeoutError:
            pass
    await asyncio.sleep(1.0)
    check("a camera that never came back still ends cleanly on stop, and the camera is released", state["released"] is True)

try:
    print("=" * 66)
    print("LIVE CAMERA THAT STOPS (real server, stand-in camera)")
    print("=" * 66)
    asyncio.run(scenario())
finally:
    cv2.VideoCapture = REAL_CAPTURE
    server.should_exit = True
    cleanup()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
