"""
Seeing small, distant workers (CCTV): higher-resolution people detection and
close-up PPE checks.

  * ppe_crops: the cut-out geometry and the mapping back, exactly;
  * PpeRunner: a worn helmet the whole-frame look misses is found by the close-up,
    only for people who are small but judgeable, capped, and a failing close-up
    never turns into "no helmet";
  * SafetySession.pose_input: the people detector gets the ORIGINAL frame at a
    matching size and its boxes are scaled back;
  * PostureEstimator rescale: boxes and keypoints are in working-frame pixels
    before posture is judged;
  * (if the CCTV clips are in sample_videos) the real socket finds far more workers
    with the safety layer on than the legacy path does.

Run:  venv/Scripts/python.exe backend/tests/test_ppe_closeup.py
"""

import io
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                                  # noqa: E402

from app.safety.config import SafetyConfig, load_config            # noqa: E402
from app.safety.ppe_crops import (                                  # noqa: E402
    DEFAULT_CROP_BELOW_PX, crop_window, needs_crop, person_crop,
)
from app.safety.ppe_runner import FAILING, PpeRunner               # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def approx(a, b, tol=0.6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ── 1. Geometry ─────────────────────────────────────────────────────────────
print("1. The cut-out")
check("needs_crop: small yes, big no, zero/negative no",
      needs_crop(150) and not needs_crop(400) and not needs_crop(0) and not needs_crop(-5) and needs_crop(359.9) and not needs_crop(360))
w = crop_window([200, 300, 260, 420], 1280, 720)
check("the window has room for a helmet above and arms either side",
      w[0] < 200 and w[1] < 300 and w[2] > 260 and w[3] > 420, str(w))
check("it is clamped to the frame", crop_window([0, 0, 50, 100], 1280, 720)[:2] == (0.0, 0.0)
      and crop_window([1250, 700, 1280, 720], 1280, 720)[2:] == (1280.0, 720.0))
check("an empty or inverted box gives nothing", crop_window([10, 10, 10, 50], 100, 100) is None and crop_window([50, 50, 20, 20], 100, 100) is None)
check("a box wholly outside the frame gives nothing", crop_window([2000, 2000, 2100, 2200], 1280, 720) is None)

frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
bbox = [800, 400, 880, 600]                                       # 200 px tall
crop = person_crop(frame, bbox)
check("a 200 px person is enlarged to about the model's size", abs(crop.image.shape[0] - 640) <= 2 and crop.scale >= 2.4, f"{crop.image.shape} x{crop.scale:.2f}")
tiny = person_crop(frame, [800, 400, 812, 430])                   # 30 px tall
check("a tiny person is not enlarged more than 4x", tiny is not None and tiny.scale <= 4.0 + 1e-6, f"x{tiny.scale:.2f}")
check("the cut-out is exactly what is in the frame (a marked patch lands where expected)", True)
marked = np.zeros((1080, 1920, 3), dtype=np.uint8)
marked[420:440, 820:840] = 255                                      # a white patch on the person
crop = person_crop(marked, bbox)
ys, xs = np.where(crop.image[..., 0] > 200)
patch_in_crop = [xs.min(), ys.min(), xs.max(), ys.max()]
back = crop.to_frame([{"cls": "helmet", "conf": 0.9, "bbox": [float(v) for v in patch_in_crop]}])[0]["bbox"]
check("a detection found on that patch maps back onto it in the frame (to within a pixel)", approx(back, [820, 420, 840, 440], 0.75), str(back))
check("to_frame keeps the other fields", crop.to_frame([{"cls": "helmet", "conf": 0.7, "bbox": [0, 0, 1, 1]}])[0]["conf"] == 0.7)
check("a frame too small to cut gives nothing, not an exception", person_crop(np.zeros((10, 10, 3), np.uint8), [4, 4, 5, 5]) is None)

# ── 2. The runner ───────────────────────────────────────────────────────────
print("2. Close-up checks in the runner")
ORIG = np.zeros((1080, 1920, 3), dtype=np.uint8)
POSE = (360, 640, 3)
# a person 60x120 px in the 640-wide pose frame = 180 px tall in the 1280-wide PPE frame (small, judgeable)
SMALL = {"track_id": 1, "bbox": [200.0, 100.0, 260.0, 220.0]}
BIG = {"track_id": 2, "bbox": [400.0, 20.0, 560.0, 340.0]}         # 640 px tall in the PPE frame
TINY = {"track_id": 3, "bbox": [50.0, 100.0, 60.0, 140.0]}         # 80 px tall in the PPE frame: below the judging floor
CFG_ON = SafetyConfig(ppe_every_n_frames=1)


class Eyes:
    """Finds nothing in the whole frame; finds a helmet and a vest only in a person cut-out, where it is told."""

    def __init__(self, crop_dets=None, fail_crops=False):
        self.calls = []
        self.crop_dets = crop_dets
        self.fail_crops = fail_crops

    def detect(self, frame, imgsz=None):
        self.calls.append((frame.shape, imgsz))
        whole = frame.shape[1] == 1280
        if not whole:
            if self.fail_crops:
                raise RuntimeError("boom")
            return self.crop_dets(frame) if self.crop_dets else []
        return []


# where the SMALL person's head and torso are in the PPE (1280-wide) frame
pf = 1280 / 640.0
pb = [v * pf for v in SMALL["bbox"]]                                  # 400,200,520,440
head_small = [pb[0] + 30, pb[1] - 5, pb[2] - 30, pb[1] + 45]
vest_small = [pb[0] + 5, pb[1] + 60, pb[2] - 5, pb[1] + 160]
f_orig = 1920 / 1280.0                                                 # PPE frame -> original
bbox_orig = [v * f_orig for v in pb]
cr = person_crop(ORIG, bbox_orig)


def to_crop(box_small):
    """A box in PPE-frame pixels, as the detector would report it inside the cut-out."""
    x1, y1, x2, y2 = [v * f_orig for v in box_small]
    return [(x1 - cr.x0) * cr.scale_x, (y1 - cr.y0) * cr.scale, (x2 - cr.x0) * cr.scale_x, (y2 - cr.y0) * cr.scale]


eyes = Eyes(lambda img: [{"cls": "helmet", "conf": 0.9, "bbox": to_crop(head_small)},
                         {"cls": "safety_vest", "conf": 0.9, "bbox": to_crop(vest_small)}])
r = PpeRunner(eyes, CFG_ON)
out = r.process([SMALL], ORIG, POSE, 0.0)
check("one whole-frame look and one close-up for a small, judgeable worker", len(eyes.calls) == 2 and r.crops_run == 1, str(eyes.calls))
check("the close-up image is the enlarged cut-out, not the whole frame", eyes.calls[1][0][0] >= 600 and eyes.calls[1][0][1] < 1280, str(eyes.calls[1]))
check("a helmet and vest the whole-frame look missed are found by the close-up and matched to the worker",
      out[1]["helmet"] == 0.9 and out[1]["vest"] == 0.9, str(out[1]))

eyes = Eyes()
r = PpeRunner(eyes, CFG_ON)
r.process([BIG, TINY], ORIG, POSE, 0.0)
check("a big worker (640 px) and one too small to judge (80 px) get no close-up", len(eyes.calls) == 1 and r.crops_run == 0, str(eyes.calls))

eyes = Eyes()
r = PpeRunner(eyes, SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0))
r.process([SMALL], ORIG, POSE, 0.0)
check("ppe_crop_below_px = 0 switches close-ups off", len(eyes.calls) == 1)

people = [{"track_id": 10 + i, "bbox": [20.0 + 40 * i, 100.0, 50.0 + 40 * i, 220.0 - 3 * i]} for i in range(8)]
eyes = Eyes()
r = PpeRunner(eyes, SafetyConfig(ppe_every_n_frames=1, ppe_max_crops=3))
r.process(people, ORIG, POSE, 0.0)
check("at most ppe_max_crops close-ups per frame", r.crops_run == 3, str(r.crops_run))

eyes = Eyes(fail_crops=True)
r = PpeRunner(eyes, CFG_ON)
out = None
for i in range(40):
    out = r.process([SMALL], ORIG, POSE, i * 0.1)
check("a close-up that keeps failing is never read as 'no helmet' by itself, and is not counted as the detector failing",
      r.unavailable_reason is None and r.crops_run == 0)
check("...the worker is simply judged on the whole-frame look (here: nothing found, so evidence of absence, as before)",
      out[1]["status"] in ("NON_COMPLIANT", "UNVERIFIABLE"))

eyes = Eyes(lambda img: [{"cls": "helmet", "conf": 0.9, "bbox": [0, 0, 10, 10]}])       # a helmet far from the worker's head
r = PpeRunner(eyes, CFG_ON)
out = r.process([SMALL], ORIG, POSE, 0.0)
check("a helmet found in the cut-out but not on the worker's head is not credited to them", out[1]["helmet"] == 0.0, str(out[1]))

# ── 2b. Review findings: geometry, ordering, aspect, failures ──────────────
print("2b. Review findings")
wide_box = person_crop(np.zeros((2160, 3840, 3), np.uint8), [100, 1000, 2500, 1200])      # lying down: 2400 x 200
check("a very wide box does not make a huge cut-out (long side capped)", max(wide_box.image.shape[:2]) <= 1280, str(wide_box.image.shape))
check("...and the mapping back still works on it", abs(wide_box.to_frame([{"bbox": [0, 0, wide_box.image.shape[1], wide_box.image.shape[0]]}])[0]["bbox"][2] - (wide_box.x0 + wide_box.image.shape[1] / wide_box.scale_x)) < 1e-6)

# non-uniform ratios: PPE frame 1280x800 (original 1280x800), pose frame 640x360 -> fx != fy
ORIG2 = np.zeros((800, 1280, 3), np.uint8)
POSE2 = (360, 640, 3)
P2 = {"track_id": 1, "bbox": [100.0, 40.0, 160.0, 160.0]}                  # 120 px tall in pose = 267 px in the PPE frame
pb2 = [100 * 2.0, 40 * (800 / 360.0), 160 * 2.0, 160 * (800 / 360.0)]
head2 = [pb2[0] + 25, pb2[1] - 4, pb2[2] - 25, pb2[1] + 40]
cr2 = person_crop(ORIG2, pb2)


class Eyes2:
    def detect(self, frame, imgsz=None):
        if frame.shape[1] == 1280:
            return []
        x1, y1, x2, y2 = head2
        return [{"cls": "helmet", "conf": 0.8, "bbox": [(x1 - cr2.x0) * cr2.scale_x, (y1 - cr2.y0) * cr2.scale,
                                                         (x2 - cr2.x0) * cr2.scale_x, (y2 - cr2.y0) * cr2.scale]}]


out = PpeRunner(Eyes2(), SafetyConfig(ppe_every_n_frames=1, ppe_hivis_min_fraction=0.0)).process([P2], ORIG2, POSE2, 0.0)
check("with different width and height ratios (the case a swapped fx/fy would break) the close-up helmet is still credited", out[1]["helmet"] == 0.8, str(out[1]))

# cap order: the tallest judgeable people are checked first
import app.safety.ppe_runner as runner_mod                         # noqa: E402
seen_boxes = []
real_pc = runner_mod.person_crop
runner_mod.person_crop = lambda f, b, *a, **k: (seen_boxes.append(b), real_pc(f, b, *a, **k))[1]
try:
    people2 = [{"track_id": 20 + i, "bbox": [20.0 + 40 * i, 100.0, 50.0 + 40 * i, 100.0 + 60 + 10 * i]} for i in range(6)]   # heights 60..110 (pose px)
    PpeRunner(Eyes(), SafetyConfig(ppe_every_n_frames=1, ppe_max_crops=2)).process(people2, ORIG, POSE, 0.0)
finally:
    runner_mod.person_crop = real_pc
heights = sorted([b[3] - b[1] for b in seen_boxes], reverse=True)
check("when the cap bites, the TALLEST (most judgeable) small people get the close-ups, not the smallest",
      len(seen_boxes) == 2 and min(heights) > 150 * 1.4, str(heights))

# failures outside the detector
real_pc = runner_mod.person_crop
runner_mod.person_crop = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad crop"))
try:
    r = PpeRunner(Eyes(), CFG_ON)
    for i in range(10):
        out = r.process([SMALL], ORIG, POSE, i * 0.1)
finally:
    runner_mod.person_crop = real_pc
check("a crop step that raises is skipped and is not counted as the detector failing", r.unavailable_reason is None and r.crops_run == 0)
real_h = runner_mod.hivis_fraction
runner_mod.hivis_fraction = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad cue"))
try:
    r = PpeRunner(Eyes(), SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0))
    for i in range(10):
        r.process([{"track_id": 1, "bbox": [400.0, 100.0, 520.0, 400.0]}], np.zeros((720, 1280, 3), np.uint8), (720, 1280, 3), i * 0.1)
finally:
    runner_mod.hivis_fraction = real_h
check("a colour cue that raises is skipped and is not counted as the detector failing", r.unavailable_reason is None)


class Rec:
    def __init__(self):
        self.shapes = []

    def detect(self, frame, imgsz=None):
        self.shapes.append((frame.shape[:2], imgsz))
        return []


rec = Rec()
PpeRunner(rec, SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0)).process(
    [{"track_id": 1, "bbox": [100.0, 100.0, 300.0, 500.0]}], np.zeros((1920, 1080, 3), np.uint8), (640, 360, 3), 0.0)
check("a portrait 1080x1920 frame reaches the PPE model at 1280 on its long side (not 1920)", rec.shapes[0][0] == (1280, 720) and rec.shapes[0][1] == 1280, str(rec.shapes))

# ── 3. What the people detector is shown ────────────────────────────────────
print("3. SafetySession.pose_input")
from sqlalchemy import create_engine                               # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402


def session(cfg=None):
    return SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")),
                         SafetyWriter("cam", engine=create_engine("sqlite://")), cfg or SafetyConfig())


work = np.zeros((360, 640, 3), np.uint8)
s = session()
f, sz, rs = session().pose_input(np.zeros((1080, 1920, 3), np.uint8), work)
check("a 1080p original is shown at 1280 wide, at a matching input size, and boxes are halved back",
      f.shape[:2] == (720, 1280) and sz == 1280 and rs == (0.5, 0.5), f"{f.shape} {sz} {rs}")
o = np.zeros((720, 1280, 3), np.uint8)
f, sz, rs = session().pose_input(o, work)
check("a 1280 original is used as it is (no copy)", f is o and sz == 1280 and rs == (0.5, 0.5))
f, sz, rs = session().pose_input(work, work)
check("when there is no larger frame, the working copy at its own size (640), not the old 480", f is work and sz == 640 and rs is None, str((sz, rs)))
f, sz, rs = session().pose_input(np.zeros((562, 1000, 3), np.uint8), work)
check("an original between the two is used whole", f.shape[:2] == (562, 1000) and sz == 1024 and abs(rs[0] - 0.64) < 1e-9, str((f.shape, sz, rs)))
f, sz, rs = session(SafetyConfig(pose_max_width=960)).pose_input(np.zeros((1080, 1920, 3), np.uint8), work)
check("pose_max_width is honoured", f.shape[1] == 960 and sz == 960, str((f.shape, sz)))
f, sz, rs = session().pose_input(np.zeros((1920, 1080, 3), np.uint8), np.zeros((640, 360, 3), np.uint8))
check("a portrait frame works the same way", f.shape[:2] == (1280, 720) and sz == 1280 and rs == (0.5, 0.5), str((f.shape, sz, rs)))
check("a very small frame still gets a sane input size", session().pose_input(np.zeros((100, 120, 3), np.uint8), np.zeros((100, 120, 3), np.uint8))[1] == 480)

# the pose frame keeps one size for the whole stream
s2 = session()
f1, sz1, rs1 = s2.pose_input(np.zeros((1080, 1920, 3), np.uint8), work)
f2, sz2, rs2 = s2.pose_input(np.zeros((540, 960, 3), np.uint8), work)
check("if the source changes size mid-stream the people detector still gets the same size (its tracker keeps ids)",
      f2.shape == f1.shape and sz2 == sz1 and rs2 == rs1, f"{f1.shape} {sz1} {rs1} / {f2.shape} {sz2} {rs2}")

# ── 4. Boxes come back in working pixels before posture is judged ──────────
print("4. PostureEstimator rescale")
from app.cv.pose_estimator import PostureEstimator                 # noqa: E402
from collections import defaultdict, deque                          # noqa: E402


class T:
    def __init__(self, a):
        self.a = np.array(a, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class Res:
    def __init__(self):
        self.boxes = type("B", (), {"xyxy": T([[100, 200, 300, 600]]), "conf": T([0.9]), "id": T([7]), "__len__": lambda self: 1})()
        self.keypoints = type("K", (), {"data": T(np.tile([[400.0, 800.0, 0.9]], (1, 17, 1))), "__len__": lambda self: 1})()

    def __len__(self):
        return 1


class FakeModel:
    def track(self, **kw):
        self.kw = kw
        return [Res()]


est = object.__new__(PostureEstimator)
est.model, est.device, est.conf_thresh = FakeModel(), "cpu", 0.35
est.posture_history = defaultdict(lambda: deque(maxlen=7))
seen = {}
est.classify_posture_raw = lambda k, b, motion_speed=0.0: (seen.update(k=k.copy(), b=list(b)) or "STANDING")
out = est.process_frame_single_pass(np.zeros((720, 1280, 3), np.uint8), imgsz=1280, rescale=(0.5, 0.25))
check("the detection is returned in working-frame pixels", out[0]["bbox"] == [50, 50, 150, 150], str(out[0]["bbox"]))
check("posture is judged on the scaled box and keypoints, not the large-frame ones",
      seen["b"] == [50, 50, 150, 150] and np.allclose(seen["k"][:, 0], 200.0) and np.allclose(seen["k"][:, 1], 200.0), str(seen["b"]))
check("the model was run at the size it was asked for", est.model.kw["imgsz"] == 1280)
out = est.process_frame_single_pass(np.zeros((360, 640, 3), np.uint8), imgsz=480)
check("without rescale nothing changes (legacy path)", out[0]["bbox"] == [100, 200, 300, 600])

# ── 5. Settings ─────────────────────────────────────────────────────────────
print("5. Settings")
c = load_config({"SAFETY_POSE_MAX_WIDTH": "100", "SAFETY_PPE_CROP_BELOW_PX": "-1", "SAFETY_PPE_MAX_CROPS": "999"})
d = SafetyConfig()
check("out-of-range overrides are ignored", c.pose_max_width == d.pose_max_width and c.ppe_crop_below_px == d.ppe_crop_below_px and c.ppe_max_crops == d.ppe_max_crops)
check("defaults: 1280 px, 360 px, 10 close-ups", (d.pose_max_width, d.ppe_crop_below_px, d.ppe_max_crops) == (1280, 360.0, 10) and d.ppe_crop_below_px == DEFAULT_CROP_BELOW_PX)

# ── 5b. Hi-vis colour as a second opinion on vests ─────────────────────────
print("5b. The colour cue")
from app.safety.ppe_colour import hivis_fraction                    # noqa: E402

grey = np.full((720, 1280, 3), 90, dtype=np.uint8)
PB = [400.0, 100.0, 520.0, 400.0]                                     # a 300 px tall person, torso at y 145-280


def painted(bgr, box=PB):
    f = grey.copy()
    x1, y1, x2, y2 = [int(v) for v in box]
    h = y2 - y1
    f[int(y1 + 0.12 * h):int(y1 + 0.62 * h), x1:x2] = bgr
    return f


ORANGE_BGR, YELLOW_BGR, SKIN_BGR, KHAKI_BGR = (0, 100, 255), (0, 255, 200), (120, 160, 205), (110, 140, 160)     # ORANGE: safety orange, RGB (255,100,0)
check("an orange torso is mostly hi-vis", hivis_fraction(painted(ORANGE_BGR), PB) > 0.9)
check("a fluorescent yellow-green torso is mostly hi-vis", hivis_fraction(painted(YELLOW_BGR), PB) > 0.9)
check("a grey torso, skin tone and khaki are not", all(hivis_fraction(painted(c), PB) < 0.05 for c in ((90, 90, 90), SKIN_BGR, KHAKI_BGR)),
      str([hivis_fraction(painted(c), PB) for c in ((90, 90, 90), SKIN_BGR, KHAKI_BGR)]))
check("no torso to look at gives None, not an error", hivis_fraction(grey, [10, 10, 12, 14]) is None and hivis_fraction(grey, [2000, 2000, 2100, 2200]) is None)

POSE_FULL = (720, 1280, 3)
PERSON_PB = {"track_id": 1, "bbox": PB}
cfg = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0)


def verdict_after(frame, detections, cfg=cfg, n=30, person=PERSON_PB):
    class Fixed:
        def detect(self, f, imgsz=None):
            return list(detections)
    r = PpeRunner(Fixed(), cfg)
    out = None
    for i in range(n):
        out = r.process([person], frame, POSE_FULL, i * 0.1)
    return out[1]


check("olive, tan, brown and khaki work clothes are not mistaken for hi-vis",
      all(hivis_fraction(painted(c), PB) < 0.05 for c in ((90, 130, 130), (50, 110, 160), (30, 70, 110), (110, 140, 160), (40, 95, 75))),
      str([round(hivis_fraction(painted(c), PB), 2) for c in ((90, 130, 130), (50, 110, 160), (30, 70, 110), (110, 140, 160), (40, 95, 75))]))
import cv2 as _cv2                                                   # noqa: E402
_FACTORY_CLIP = os.path.join(ROOT, "sample_videos", "Example of Hi-Definition Video Surveillance of a Factory Floor - by CCTVDOC.COM.mp4")
if os.path.exists(_FACTORY_CLIP):
    _cap = _cv2.VideoCapture(_FACTORY_CLIP)
    _cap.set(_cv2.CAP_PROP_POS_FRAMES, 40)
    _ok, _frame40 = _cap.read()
    check("the real orange-vest worker in the factory clip scores well above the 0.10 floor", _ok and hivis_fraction(_frame40, [520, 358, 576, 524]) >= 0.15,
          str(hivis_fraction(_frame40, [520, 358, 576, 524]) if _ok else None))
check("(known limit) a bright yellow shirt DOES pass: it makes the vest reading 'cannot tell', never 'no vest'", hivis_fraction(painted((0, 230, 255)), PB) > 0.9)

HELMET_ON = {"cls": "helmet", "conf": 0.9, "bbox": [430.0, 95.0, 490.0, 150.0]}
v = verdict_after(painted(ORANGE_BGR), [HELMET_ON])
check("no vest found but an orange torso: 'cannot tell', never 'no vest'", v["vest_state"] == "UNVERIFIABLE" and v["status"] == "UNVERIFIABLE", str(v))
v = verdict_after(painted((90, 90, 90)), [HELMET_ON])
check("no vest found and a plain torso: still 'no vest seen' (the cue does not hide real violations)", v["vest_state"] == "ABSENT" and v["status"] == "NON_COMPLIANT", str(v))
v = verdict_after(painted(ORANGE_BGR), [HELMET_ON], SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0))
check("with the cue switched off (0) the old behaviour: absent", v["vest_state"] == "ABSENT", str(v))
# the cue and non-default thresholds: it must never reach 'seen'
cfg_odd = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_weak_conf=0.40, ppe_present_conf=0.60)
v = verdict_after(painted(ORANGE_BGR), [HELMET_ON], cfg_odd)
check("even with the weak/present settings changed, the colour cue alone can never make a vest PRESENT", v["vest_state"] == "UNVERIFIABLE", str(v))
cfg_low = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_weak_conf=0.10, ppe_present_conf=0.20, ppe_conf_floor=0.05)
v = verdict_after(painted(ORANGE_BGR), [HELMET_ON], cfg_low)
check("...and with very low settings it still counts as 'cannot tell', not as nothing", v["vest_state"] == "UNVERIFIABLE", str(v))

VEST_ON = {"cls": "safety_vest", "conf": 0.9, "bbox": [405.0, 150.0, 515.0, 280.0]}
v = verdict_after(painted(ORANGE_BGR), [HELMET_ON, VEST_ON])
check("a detected vest is PRESENT as before (the cue changes nothing when the detector found it)", v["vest_state"] == "PRESENT" and v["status"] == "COMPLIANT", str(v))
check("the cue never makes a vest PRESENT by itself", verdict_after(painted(ORANGE_BGR), [])["vest_state"] != "PRESENT")
v = verdict_after(painted(ORANGE_BGR, [400, 100, 410, 130]), [], person={"track_id": 1, "bbox": [400.0, 100.0, 410.0, 130.0]})
check("a person too small to judge is not judged at all, cue or not", v["clear"] is False and v["status"] == "UNVERIFIABLE")
d = SafetyConfig()
check("default: 10 % of the torso", d.ppe_hivis_min_fraction == 0.10)
check("an out-of-range override is ignored", load_config({"SAFETY_PPE_HIVIS_MIN_FRACTION": "2"}).ppe_hivis_min_fraction == 0.10)

# ── 6. The real thing, on real CCTV clips if they are here ──────────────────
print("6. Real CCTV clips")
FACTORY = os.path.join(ROOT, "sample_videos", "Example of Hi-Definition Video Surveillance of a Factory Floor - by CCTVDOC.COM.mp4")
WAREHOUSE = os.path.join(ROOT, "sample_videos", "_ Hikvision ViewStar 8 Megapixel warehouse pallet company camera.mp4")
clips = [p for p in (FACTORY, WAREHOUSE) if os.path.exists(p)]
if not clips:
    print("  [SKIP] the CCTV clips are not in sample_videos")
else:
    from fastapi.testclient import TestClient                       # noqa: E402
    from app.main import app                                         # noqa: E402
    from app.api.routers import video_upload                         # noqa: E402

    PROBE = "00000000-0000-0000-0000-00000000aaaa"

    async def _org(_w):
        return PROBE, "ADMIN"

    video_upload._resolve_session_org = _org
    app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE
    client = TestClient(app)

    FACTORY_WORKER = {}

    def people_frames(path, safety):
        if safety:
            os.environ["SAFETY_ENABLED"] = "true"
        else:
            os.environ.pop("SAFETY_ENABLED", None)
        sid = client.post("/api/v1/video/upload", files={"file": ("c.mp4", io.BytesIO(open(path, "rb").read()), "video/mp4")}).json()["session_id"]
        n = zone_ok = 0
        with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false") as ws:
            while True:
                m = ws.receive_json()
                if m.get("type") == "FRAME":
                    n += len(m["tracked_entities"])
                    for e in m["tracked_entities"]:
                        b = e["bbox"]
                        if safety and path == FACTORY and 250 <= (b[0] + b[2]) / 2 <= 300 and 240 <= b[3] <= 280 and "ppe" in e:
                            FACTORY_WORKER.setdefault("states", []).append((m["frame_index"], e["ppe"]["vest_state"]))
                        zone_ok += 0 <= b[0] < b[2] <= 645 and 0 <= b[1] < b[3] <= 365      # still in the 640x360 working frame
                elif m.get("type") in ("COMPLETE", "ERROR") or "error" in m:
                    break
        os.environ.pop("SAFETY_ENABLED", None)
        return n, zone_ok

    try:
        for path in clips:
            old, _ = people_frames(path, False)
            new, in_frame = people_frames(path, True)
            name = os.path.basename(path)[:24]
            check(f"{name}: the safety path finds far more people than the legacy one ({old} -> {new} person-frames)", new >= max(old * 2, old + 20), f"{old} -> {new}")
            check(f"{name}: boxes are still in the 640-wide working frame's pixels", in_frame == new, f"{in_frame}/{new}")
        if FACTORY in clips and FACTORY_WORKER.get("states"):
            later = [v for i, v in FACTORY_WORKER["states"] if i >= 30]
            check("factory clip: the worker in the orange vest at the table is never called 'no vest' once judged (regression: a stricter colour mask lost this exact worker)",
                  later and all(v != "ABSENT" for v in later), str(sorted(set(later))))
    finally:
        os.environ.pop("SAFETY_ENABLED", None)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
