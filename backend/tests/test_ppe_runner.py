"""
PPE in the live loop (Step 5), with a fake detector so no model is needed.

What has to be true:
  * every person gets a verdict on every frame, from the same evidence rules as
    ppe_evidence.py (not enough clear frames = UNVERIFIABLE, never a guess);
  * the model runs only on every Nth frame;
  * a detector that fails, or is missing, never reads as "no helmet": it adds no
    evidence, and verdicts are UNVERIFIABLE with a stated reason;
  * the PPE model gets the ORIGINAL (large) frame and person boxes are scaled up
    to it, so a distant worker can still count as "clear";
  * a seek clears all evidence;
  * with PPE off, the entities are exactly what they were before.

Run:  venv/Scripts/python.exe backend/tests/test_ppe_runner.py
"""

import asyncio
import json
import logging
import os
import sys
import tempfile

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                                  # noqa: E402

from app.cv.frame_pipeline import process_detections               # noqa: E402
from app.cv.spatial_engine import SpatialEngine                    # noqa: E402
from app.safety import ppe_runner as pr                            # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.ppe_runner import PpeRunner, NOT_LOADED, FAILING   # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


class Fake:
    """A detector that returns whatever it was told, and remembers how it was called."""

    def __init__(self, dets=None, fail=False):
        self.dets = dets or []
        self.fail = fail
        self.calls = []

    def detect(self, frame, imgsz=None):
        self.calls.append((frame.shape, imgsz))
        if self.fail:
            raise RuntimeError("cuda out of memory")
        return list(self.dets)


FRAME = np.zeros((480, 640, 3), dtype=np.uint8)
SHAPE = FRAME.shape
PERSON = {"track_id": 1, "bbox": [200.0, 100.0, 300.0, 400.0]}        # 300 px tall: clear
# a helmet on the head, a vest on the torso, in the same pixel space
HELMET = {"cls": "helmet", "conf": 0.9, "bbox": [225.0, 95.0, 275.0, 135.0]}
VEST = {"cls": "safety_vest", "conf": 0.9, "bbox": [205.0, 190.0, 295.0, 290.0]}
CFG = SafetyConfig()           # every 2nd frame, min 8 clear frames, window 30


def run_frames(runner, n, persons=(PERSON,), frame=FRAME, shape=SHAPE, start=0.0, dt=0.1):
    last = None
    for i in range(n):
        last = runner.process(list(persons), frame, shape, start + i * dt)
    return last


# ── 1. Verdicts from evidence ───────────────────────────────────────────────
print("1. Verdicts come from the evidence rules")
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
out = run_frames(r, 6)
check("few frames in: UNVERIFIABLE, not a guess", out[1]["status"] == "UNVERIFIABLE", str(out[1]))
out = run_frames(r, 30, start=0.6)
check("helmet and vest seen on enough clear frames: COMPLIANT", out[1]["status"] == "COMPLIANT", str(out[1]))
check("the entity carries the shape the UI needs",
      set(out[1]) == {"status", "helmet", "vest", "clear", "helmet_state", "vest_state"}
      and out[1]["helmet"] == 0.9 and out[1]["vest"] == 0.9 and out[1]["clear"] is True
      and out[1]["helmet_state"] == "PRESENT" and out[1]["vest_state"] == "PRESENT", str(out[1]))

det = Fake([VEST])                                     # a vest, but no helmet anywhere
r = PpeRunner(det, CFG)
out = run_frames(r, 40)
check("vest but no helmet in nearly every clear frame: NON_COMPLIANT, and says which item",
      out[1]["status"] == "NON_COMPLIANT" and out[1]["helmet_state"] == "ABSENT" and out[1]["vest_state"] == "PRESENT", str(out[1]))

det = Fake([])
r = PpeRunner(det, CFG)
out = run_frames(r, 40)
check("nothing detected at all on clear frames: NON_COMPLIANT", out[1]["status"] == "NON_COMPLIANT", str(out[1]))

small = {"track_id": 1, "bbox": [200.0, 100.0, 230.0, 160.0]}                # 60 px tall: not clear
r = PpeRunner(Fake([]), CFG)
out = run_frames(r, 60, persons=(small,))
check("a person too small to judge is never NON_COMPLIANT, however long they are watched",
      out[1]["status"] == "UNVERIFIABLE" and out[1]["clear"] is False, str(out[1]))

# ── 2. Every Nth frame ──────────────────────────────────────────────────────
print("2. The model runs on every Nth frame")
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
run_frames(r, 10)
check("every 2nd frame by default: 5 model runs for 10 frames", len(det.calls) == 5, str(len(det.calls)))
det = Fake([HELMET, VEST])
r = PpeRunner(det, SafetyConfig(ppe_every_n_frames=1))
run_frames(r, 10)
check("every_n=1: every frame", len(det.calls) == 10)
det = Fake([HELMET, VEST])
r = PpeRunner(det, SafetyConfig(ppe_every_n_frames=4))
run_frames(r, 10)
check("every_n=4: frames 1, 5, 9", len(det.calls) == 3, str(len(det.calls)))
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
for i in range(10):
    r.process([], FRAME, SHAPE, i * 0.1)
check("no people in view: the model is not run at all", det.calls == [])
out = r.process([PERSON], FRAME, SHAPE, 1.0)
check("...and someone appearing gets a verdict straight away (UNVERIFIABLE while evidence builds)",
      out[1]["status"] == "UNVERIFIABLE")
check("a verdict exists for EVERY person on EVERY frame, detection frame or not",
      all(1 in r.process([PERSON], FRAME, SHAPE, 1.1 + i * 0.1) for i in range(4)))

# ── 3. Failure and absence of a model ───────────────────────────────────────
print("3. A missing or failing model is never 'no helmet'")
r = PpeRunner(None, CFG)
out = run_frames(r, 5)
check("no detector: UNVERIFIABLE with the reason", out[1]["status"] == "UNVERIFIABLE" and r.unavailable_reason == NOT_LOADED, str(out[1]))
check("...for every person", set(run_frames(r, 1, persons=(PERSON, dict(PERSON, track_id=2)))) == {1, 2})

det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
run_frames(r, 40)                                      # a solid COMPLIANT
det.fail = True
out = run_frames(r, 10, start=4.0)
check("a failing detector adds no evidence (the old verdict is not turned into NON_COMPLIANT)",
      out[1]["status"] in ("COMPLIANT", "UNVERIFIABLE"), str(out[1]))
check("...and after three failures in a row the runner says so", r.unavailable_reason == FAILING, str(r.unavailable_reason))
out = run_frames(r, 10, start=40.0)                     # 36 s later: the old evidence is stale
check("with the detector still down, stale evidence ages out to UNVERIFIABLE (never NON_COMPLIANT)",
      out[1]["status"] == "UNVERIFIABLE", str(out[1]))
det.fail = False
run_frames(r, 4, start=60.0)
check("the detector coming back clears the 'failing' state", r.unavailable_reason is None)

det = Fake([])
det.fail = True
r = PpeRunner(det, CFG)
out = run_frames(r, 60)
check("a detector that has NEVER worked can never produce a violation", out[1]["status"] == "UNVERIFIABLE")

# ── 4. Resolution ───────────────────────────────────────────────────────────
print("4. The PPE model gets the original frame, and boxes are scaled to it")
big = np.zeros((1080, 1920, 3), dtype=np.uint8)
pose_shape = (360, 640, 3)
person_pose = {"track_id": 7, "bbox": [100.0, 40.0, 160.0, 160.0]}     # 120 px tall in pose space, 360 px in 1080p
det = Fake([])
r = PpeRunner(det, CFG)
r.process([person_pose], big, pose_shape, 0.0)
check("a 1080p frame reaches the model shrunk to 1280 wide, not 640", det.calls[0][0][:2] == (720, 1280), str(det.calls[0]))
check("the model is told to work at that size (longest side, multiple of 32)", det.calls[0][1] == 1280, str(det.calls[0]))
r = PpeRunner(Fake([]), SafetyConfig(ppe_max_width=640))
d2 = r.detector
r.process([person_pose], big, pose_shape, 0.0)
check("ppe_max_width is honoured", d2.calls[0][0][:2] == (360, 640), str(d2.calls[0]))
small_frame = np.zeros((300, 500, 3), dtype=np.uint8)
d3 = Fake([])
PpeRunner(d3, CFG).process([{"track_id": 1, "bbox": [10.0, 10.0, 60.0, 200.0]}], small_frame, (300, 500, 3), 0.0)
check("a frame narrower than the limit is passed as it is, imgsz rounded up to 32", d3.calls[0] == ((300, 500, 3), 512), str(d3.calls[0]))

# A person 60 px tall in the 640-wide pose frame is 120 px tall at 1280: clear only because of the scaling.
tiny_pose = {"track_id": 1, "bbox": [100.0, 100.0, 130.0, 160.0]}
det = Fake([])
r = PpeRunner(det, CFG)
out = None
for i in range(40):
    out = r.process([tiny_pose], big, pose_shape, i * 0.1)
check("a worker too small in the 640 copy is judged on the large frame (clear: True, so NON_COMPLIANT is possible)",
      out[1]["clear"] is True and out[1]["status"] == "NON_COMPLIANT", str(out[1]))
r = PpeRunner(Fake([]), CFG)
for i in range(40):
    out = r.process([tiny_pose], np.zeros((360, 640, 3), np.uint8), pose_shape, i * 0.1)
check("...whereas with no larger frame to look at, the same worker stays UNVERIFIABLE", out[1]["clear"] is False and out[1]["status"] == "UNVERIFIABLE", str(out[1]))

# helmet found in 1280-space attaches to the person given in 640-space
helmet_big = {"cls": "helmet", "conf": 0.88, "bbox": [210.0, 70.0, 270.0, 130.0]}   # 2x the pose-space head
det = Fake([helmet_big])
r = PpeRunner(det, SafetyConfig(ppe_every_n_frames=1))
out = r.process([person_pose], big, pose_shape, 0.0)
check("a helmet detected at 1280-wide coordinates is matched to the person boxed at 640-wide coordinates",
      out[7]["helmet"] == 0.88, str(out[7]))

# ── 5. Seeks ────────────────────────────────────────────────────────────────
print("5. A jump in the timeline clears the evidence")
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
run_frames(r, 40)
check("before the seek: COMPLIANT", r.process([PERSON], FRAME, SHAPE, 4.0)[1]["status"] == "COMPLIANT")
r.reset("SEEK", 4.0, 0.0)
check("after the seek the person starts again from UNVERIFIABLE", r.process([PERSON], FRAME, SHAPE, 0.0)[1]["status"] == "UNVERIFIABLE")
check("reset returns nothing to write (PPE evidence is not an event)", r.reset("SEEK", 0.0, 0.0) is None)

# ── 6. Housekeeping ─────────────────────────────────────────────────────────
print("6. Memory")
r = PpeRunner(Fake([]), SafetyConfig(ppe_every_n_frames=1))
for i in range(50):
    r.process([{"track_id": 100 + i, "bbox": [200.0, 100.0, 300.0, 400.0]}], FRAME, SHAPE, i * 5.0)
check("tracks not seen for a long time are dropped", len(r._last_seen) <= 8 and len(r._tracker._obs) <= 8, str((len(r._last_seen), len(r._tracker._obs))))

# ── 7. Frame pipeline ───────────────────────────────────────────────────────
print("7. Entities")


class _Anon:
    def __init__(self):
        self.blurred = []

    def blur_face_region(self, frame, bbox, blur_enabled=True):
        self.blurred.append(bbox)


class _Act:
    def update_track(self, *a): pass
    def calculate_activity_score(self, *a): return 0.0
    def get_dwell_time_seconds(self, *a): return 0


dets = [{"track_id": 3, "bbox": [100, 100, 200, 400], "posture": "STANDING"},
        {"track_id": 4, "bbox": [300, 100, 400, 400], "posture": "WALKING"}]
spatial = SpatialEngine(zones_config=[])
a, _ = process_detections(dets, FRAME.copy(), spatial, _Act(), _Anon(), [], False)
b, _ = process_detections(dets, FRAME.copy(), spatial, _Act(), _Anon(), [], False, ppe_by_track=None)
check("with PPE off the entities are exactly what they were (compared as JSON)", json.dumps(a) == json.dumps(b) and all("ppe" not in e for e in a))
ppe = {3: {"status": "COMPLIANT"}}
c, _ = process_detections(dets, FRAME.copy(), spatial, _Act(), _Anon(), [], False, ppe_by_track=ppe)
check("with PPE on, each person carries their own verdict", c[0]["ppe"] == {"status": "COMPLIANT"} and "ppe" not in c[1])
stripped = [{k: v for k, v in e.items() if k != "ppe"} for e in c]
check("nothing else about the entities changes", json.dumps(stripped) == json.dumps(a))

# ── 8. The session ──────────────────────────────────────────────────────────
print("8. The session")
from sqlalchemy import create_engine                               # noqa: E402
from app.db.database import configure_sqlite                      # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

tmp = tempfile.mkdtemp(prefix="vw_ppe_")
dbe = create_engine(f"sqlite:///{os.path.join(tmp, 'db.sqlite')}", connect_args={"check_same_thread": False})
configure_sqlite(dbe, busy_timeout_ms=250)
ensure_safety_tables(dbe)


def make_session(runner):
    return SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")),
                         SafetyWriter("cam", engine=dbe, retry_interval_s=0.0), SafetyConfig(), ppe_runner=runner)


async def scenario():
    plain = make_session(None)
    ft = plain.begin_frame(0)
    got_plain = await plain.detect_ppe(ft, [PERSON], FRAME, SHAPE)
    pay_plain = plain.payload(ft)

    down = make_session(PpeRunner(None, CFG))
    ft = down.begin_frame(0)
    got_down = await down.detect_ppe(ft, [PERSON], FRAME, SHAPE)
    pay_down = down.payload(ft)

    live = make_session(PpeRunner(Fake([HELMET, VEST]), SafetyConfig(ppe_every_n_frames=1)))
    for i in range(12):
        ft = live.begin_frame(i)
        got_live = await live.detect_ppe(ft, [PERSON, {"track_id": None, "bbox": [0, 0, 1, 1]}], FRAME, SHAPE)
    return got_plain, pay_plain, got_down, pay_down, got_live, live.payload(ft), live.engine

got_plain, pay_plain, got_down, pay_down, got_live, pay_live, eng = asyncio.run(scenario())
check("a session without PPE returns nothing and says nothing about PPE", got_plain is None and set(pay_plain) == {"video_time_s", "observed_at", "replay"})
check("a session whose model is missing says why in the payload, and verdicts are UNVERIFIABLE",
      pay_down.get("ppe_unavailable") == NOT_LOADED and got_down[1]["status"] == "UNVERIFIABLE", str(pay_down))
check("a working session builds up to COMPLIANT, and ignores an entity with no track id",
      set(got_live) == {1} and got_live[1]["status"] == "COMPLIANT" and "ppe_unavailable" not in pay_live, str(got_live))
check("the runner is registered with the engine so a seek resets it", any(isinstance(c, PpeRunner) for c in eng.components))

# ── 9. Loading the detector ─────────────────────────────────────────────────
print("9. The detector file")
records = []


class Cap(logging.Handler):
    def emit(self, record):
        records.append(record)


cap = Cap()
logging.getLogger("app.safety.ppe_runner").addHandler(cap)
logging.getLogger("app.safety.ppe_runner").setLevel(logging.INFO)
saved = os.environ.get("PPE_MODEL_PATH")
os.environ["PPE_MODEL_PATH"] = os.path.join(tmp, "nope.pt")
pr._detector, pr._detector_failed_for = None, None
check("missing weights: None, not an exception", pr.get_ppe_detector() is None)
check("...with one clear error naming the path", sum(1 for r_ in records if r_.levelno == logging.ERROR and "nope.pt" in r_.getMessage()) == 1)
pr.get_ppe_detector(); pr.get_ppe_detector()
check("...asked again for each session, it does not repeat the error", sum(1 for r_ in records if r_.levelno == logging.ERROR) == 1)
bad = os.path.join(tmp, "broken.pt")
open(bad, "wb").write(b"not a model")
os.environ["PPE_MODEL_PATH"] = bad
check("a file that is not a model: None, with a clear error", pr.get_ppe_detector() is None
      and any(r_.levelno == logging.ERROR and "could not be loaded" in r_.getMessage() for r_ in records))
n_before = len(records)
pr.get_ppe_detector()
check("...and that failure is remembered, not retried for every session", len(records) == n_before)
if saved is None:
    os.environ.pop("PPE_MODEL_PATH", None)
else:
    os.environ["PPE_MODEL_PATH"] = saved
pr._detector, pr._detector_failed_for = None, None
real = pr.get_ppe_detector()
from app.cv.ppe_detector import DEFAULT_WEIGHTS                    # noqa: E402
if os.path.isfile(DEFAULT_WEIGHTS):
    check("the real trained weights load", real is not None)
    out = real.detect(np.zeros((480, 640, 3), np.uint8), imgsz=640)
    check("...and run on a blank frame without finding anyone's helmet", out == [])
else:
    print("  [SKIP] the trained weights are not on this machine")

# ── 10. Review findings ─────────────────────────────────────────────────────
print("10. A failing detector never leaves a red verdict on screen")
det = Fake([])
r = PpeRunner(det, CFG)
out = run_frames(r, 120, dt=0.1)                       # 12 s of clear frames, nothing detected
check("setup: NON_COMPLIANT from real evidence", out[1]["status"] == "NON_COMPLIANT", str(out[1]))
det.fail = True
seen = []
for i in range(6):
    seen.append(r.process([PERSON], FRAME, SHAPE, 12.0 + i * 0.1)[1]["status"])
check("the moment the detector is declared failing, every verdict is UNVERIFIABLE (no stale red pill beside a 'failing' notice)",
      r.unavailable_reason == FAILING and seen[-1] == "UNVERIFIABLE" and seen[-2] == "UNVERIFIABLE", str(seen))

print("11. Slow sources still reach a verdict")
for fps in (25.0, 10.0, 5.0, 3.0, 2.0, 1.5, 1.0, 0.5):
    det = Fake([HELMET, VEST])
    r = PpeRunner(det, CFG)
    first = None
    final = None
    n = int(max(60, 40 * fps))
    for i in range(n):
        t = i / fps
        final = r.process([PERSON], FRAME, SHAPE, t)[1]["status"]
        if first is None and final == "COMPLIANT":
            first = t
    check(f"{fps} fps: reaches COMPLIANT ({first and round(first, 1)} s) and holds it", first is not None and final == "COMPLIANT", f"first={first} final={final}")
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
for i in range(20):
    r.process([PERSON], FRAME, SHAPE, i / 1.0)
check("at 1 fps the model runs on every frame, not every second one", len(det.calls) == 20, str(len(det.calls)))
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
for i in range(20):
    r.process([PERSON], FRAME, SHAPE, i / 25.0)
check("at 25 fps it still runs on every second frame", len(det.calls) == 10, str(len(det.calls)))
det = Fake([])
r = PpeRunner(det, CFG)
last = None
for i in range(60):
    last = r.process([PERSON], FRAME, SHAPE, i / 1.0)[1]["status"]
check("slow source with nothing detected: NON_COMPLIANT is still reachable (evidence is not starved)", last == "NON_COMPLIANT", last)

print("12. Scaling when the width and height ratios differ")
odd = np.zeros((800, 1280, 3), dtype=np.uint8)          # PPE frame 1280x800, pose frame 640x360: sx 2.0, sy 2.22
person_pose2 = {"track_id": 9, "bbox": [100.0, 50.0, 200.0, 250.0]}
x1, y1, x2, y2 = 100 * 2.0, 50 * (800 / 360.0), 200 * 2.0, 250 * (800 / 360.0)
head = {"cls": "helmet", "conf": 0.8, "bbox": [(x1 + x2) / 2 - 25, y1 - 5, (x1 + x2) / 2 + 25, y1 + 40]}
r = PpeRunner(Fake([head]), SafetyConfig(ppe_every_n_frames=1))
out = r.process([person_pose2], odd, (360, 640, 3), 0.0)
check("a helmet at the head of a person scaled by different x and y ratios is still matched", out[9]["helmet"] == 0.8, str(out[9]))

print("13. Failures outside the detector count too")
r = PpeRunner(Fake([HELMET, VEST]), SafetyConfig(ppe_every_n_frames=1))
for i in range(4):
    out = r.process([PERSON], None, SHAPE, i * 0.1)
check("a step that raises returns UNVERIFIABLE and never raises", out[1]["status"] == "UNVERIFIABLE")
check("...and enough of them make the runner report that it is failing", r.unavailable_reason == FAILING, str(r.unavailable_reason))

print("14. After a seek the cadence starts again")
det = Fake([HELMET, VEST])
r = PpeRunner(det, CFG)
r.process([PERSON], FRAME, SHAPE, 0.0)            # detection frame
r.process([PERSON], FRAME, SHAPE, 0.1)            # skipped
r.process([PERSON], FRAME, SHAPE, 0.2)            # detection frame
n = len(det.calls)
r.process([PERSON], FRAME, SHAPE, 0.3)            # skipped
r.reset("SEEK", 0.3, 0.0)
r.process([PERSON], FRAME, SHAPE, 0.0)
check("the first frame after a seek is a detection frame", len(det.calls) == n + 1, f"{n} -> {len(det.calls)}")

print("15. Order of operations in the sockets")
import re                                                          # noqa: E402
src = open(os.path.join(BACKEND, "app", "api", "routers", "video_upload.py"), encoding="utf-8").read()
for name in ("file", "live_webcam", "browser camera"):
    pass
calls = [m.start() for m in re.finditer(r"await safety\.detect_ppe\(", src)]
procs = [m.start() for m in re.finditer(r"= process_detections\(", src)]
check("three sockets each run the PPE step", len(calls) == 3 and len(procs) == 3, f"{len(calls)} / {len(procs)}")
check("in every socket the PPE step comes BEFORE process_detections (which blurs the frame in place)",
      all(c < p_ for c, p_ in zip(calls, procs)) and all(procs[i] > calls[i] and (i == 0 or calls[i] > procs[i - 1]) for i in range(3)))
check("the original frame is kept before the resize in every socket", src.count("ppe_frame = frame") == 3)

print("16. Settings")
from app.safety.config import load_config                           # noqa: E402
cfg = load_config({"SAFETY_PPE_CONF_FLOOR": "0.5"})
check("a detector floor above the evidence layer's 'weak' line would hide weak sightings: refused",
      cfg.ppe_conf_floor == 0.20 and cfg.ppe_weak_conf == SafetyConfig().ppe_weak_conf)
check("a lower floor is fine", load_config({"SAFETY_PPE_CONF_FLOOR": "0.1"}).ppe_conf_floor == 0.1)

print("17. A failed model load is retried after a while")
import time as _time                                                # noqa: E402
bad2 = os.path.join(tmp, "broken2.pt")
open(bad2, "wb").write(b"nope")
os.environ["PPE_MODEL_PATH"] = bad2
pr._detector, pr._detector_failed_for = None, None
records.clear()
pr.get_ppe_detector()
first_errors = sum(1 for r_ in records if r_.levelno == logging.ERROR)
pr.get_ppe_detector()
check("within the retry window: not tried again", sum(1 for r_ in records if r_.levelno == logging.ERROR) == first_errors == 1)
path_, mtime_, _ = pr._detector_failed_for
pr._detector_failed_for = (path_, mtime_, _time.monotonic() - pr.RETRY_LOAD_AFTER_S - 1)
pr.get_ppe_detector()
check("after the window: tried again", sum(1 for r_ in records if r_.levelno == logging.ERROR) == 2)
if saved is None:
    os.environ.pop("PPE_MODEL_PATH", None)
else:
    os.environ["PPE_MODEL_PATH"] = saved
pr._detector, pr._detector_failed_for = None, None

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
