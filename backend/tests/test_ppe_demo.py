"""
The PPE demo video through the real socket: one worker at a time, large and
clear (built from the PPE model's held-out test images by
ppe_training/make_demo_video.py). Checks that the verdicts follow what is in
the picture: a worker with helmet and vest ends COMPLIANT; one with neither is
NON_COMPLIANT; one missing the vest has the vest flagged, not the helmet.

Slow (real models). Run:  venv/Scripts/python.exe backend/tests/test_ppe_demo.py
"""

import io
import os
import subprocess
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
os.environ["SAFETY_ENABLED"] = "true"

from fastapi.testclient import TestClient                            # noqa: E402

from app.main import app                                             # noqa: E402
from app.api.routers import video_upload                             # noqa: E402

VIDEO = os.path.join(ROOT, "sample_videos", "ppe_demo.mp4")
PROBE_ORG = "00000000-0000-0000-0000-00000000eeee"
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


if not os.path.exists(VIDEO):
    subprocess.check_call([sys.executable, os.path.join(ROOT, "ppe_training", "make_demo_video.py")])

async def _fake_org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _fake_org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG
client = TestClient(app)

sid = client.post("/api/v1/video/upload",
                  files={"file": ("ppe_demo_probe.mp4", io.BytesIO(open(VIDEO, "rb").read()), "video/mp4")}).json()["session_id"]
frames = []
with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false") as ws:
    while True:
        m = ws.receive_json()
        if m.get("type") == "FRAME":
            frames.append(m)
        elif m.get("type") in ("COMPLETE", "ERROR") or "error" in m:
            break

print(f"{len(frames)} frames")
SLIDE = 50                                       # frames per picture: 5 s at 10 fps
slides = [frames[i * SLIDE:(i + 1) * SLIDE] for i in range(len(frames) // SLIDE)]
check("12 pictures, 600 frames", len(frames) == 600 and len(slides) == 12, str(len(frames)))


def last_people(seg):
    for f in reversed(seg):
        if f["tracked_entities"]:
            return [e["ppe"] for e in f["tracked_entities"]]
    return []


people = [last_people(s) for s in slides]
check("a person was found in every picture", all(people), str([len(p) for p in people]))
check("picture 1 (helmet and vest): COMPLIANT by the end", any(p["status"] == "COMPLIANT" for p in people[0]), str(people[0]))
check("picture 5 and 9 (helmet and vest): COMPLIANT by the end",
      all(any(p["status"] == "COMPLIANT" for p in people[i]) for i in (4, 8)), str([people[4], people[8]]))
check("picture 2 (neither): NON_COMPLIANT, with both items not seen",
      any(p["status"] == "NON_COMPLIANT" and p["helmet_state"] == "ABSENT" and p["vest_state"] == "ABSENT" for p in people[1]), str(people[1]))
check("picture 7 (helmet, no vest): the VEST is the missing item, never the helmet",
      any(p["status"] == "NON_COMPLIANT" and p["vest_state"] == "ABSENT" and p["helmet_state"] != "ABSENT" for p in people[6]), str(people[6]))
check("no picture of a worker wearing both is ever called NON_COMPLIANT (no false accusation)",
      all(p["status"] != "NON_COMPLIANT" for i in (0, 4, 8) for p in people[i]), str([people[i] for i in (0, 4, 8)]))
check("the first frames of each worker are never a verdict (evidence needs time)",
      all(e["ppe"]["status"] == "UNVERIFIABLE" for f in slides[0][:3] for e in f["tracked_entities"]))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
