"""
Step 9: evidence snapshots. A blurred picture for each flagged decision, nothing for the rest.

What is checked: the face-only blur on synthetic frames (the hard hat and the vest keep
their pixels, the face loses its detail), the picture's overlays and size, that the saved
file does its OWN blur whatever the session setting, which decisions get a file, the
organisation switch, path safety, expiry, and the endpoint that serves a picture (401 / 403 /
404 / 410 / 503, no-store, one audit row per view).

    venv/Scripts/python.exe backend/tests/test_snapshots.py
"""
import asyncio
import atexit
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

TMP = tempfile.mkdtemp(prefix="vw_snap_")
atexit.register(shutil.rmtree, TMP, True)
os.environ["SAFETY_EVIDENCE_DIR"] = os.path.join(TMP, "evidence")

import cv2                                                       # noqa: E402
import numpy as np                                               # noqa: E402
from sqlalchemy import create_engine, text                       # noqa: E402

from app.cv.anonymizer import PrivacyAnonymizer                  # noqa: E402
from app.db.database import configure_sqlite                     # noqa: E402
from app.db.safety_models import ensure_safety_tables            # noqa: E402
from app.db.safety_writer import SafetyWriter                    # noqa: E402
from app.safety import snapshots                                 # noqa: E402
from app.safety.clock import FrameClock                          # noqa: E402
from app.safety.config import SafetyConfig                       # noqa: E402
from app.safety.engine import SafetyEngine                       # noqa: E402
from app.safety.ppe_runner import PpeRunner                      # noqa: E402
from app.safety.session import SafetySession                     # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


rng = np.random.default_rng(7)


def textured(h=480, w=640):
    """A frame full of detail, so 'blurred' is measurable as a drop in variance."""
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def variance(img, box):
    x1, y1, x2, y2 = box
    return float(np.var(img[y1:y2, x1:x2].astype(np.float32)))


def mean_diff(a, b, box):
    x1, y1, x2, y2 = box
    return float(np.mean(np.abs(a[y1:y2, x1:x2].astype(np.float32) - b[y1:y2, x1:x2].astype(np.float32))))


BBOX = [100.0, 100.0, 160.0, 400.0]
# nose, left eye, right eye, left ear, right ear (x, y, confidence), then the other 12 unused
KPTS = np.zeros((17, 3), dtype=np.float32)
KPTS[0] = (130, 148, 0.9)
KPTS[1] = (122, 140, 0.9)
KPTS[2] = (138, 140, 0.9)
KPTS[3] = (112, 144, 0.9)
KPTS[4] = (148, 144, 0.9)
FACE_IN = (120, 164, 140, 175)       # inside the face box, clear of the helmet/vest rectangles the picture draws
HELMET = (104, 104, 156, 117)         # between the top of the box and the brow: where a hard hat is
FACE = (118, 130, 142, 160)
VEST = (104, 220, 156, 300)

print("=" * 66)
print("EVIDENCE SNAPSHOTS")
print("=" * 66)

# ── A. The face-only blur ───────────────────────────────────────────────
print()
print("A. Face-only blur")
frame = textured()
before = frame.copy()
used = PrivacyAnonymizer.blur_face_keypoints(frame, KPTS, BBOX)
check("with a located face the face box is used", used is True)
check("the face loses its detail", variance(frame, FACE) < 0.1 * variance(before, FACE), f"{variance(before, FACE):.0f} -> {variance(frame, FACE):.0f}")
check("the hard-hat region above the brow is untouched, pixel for pixel", np.array_equal(frame[HELMET[1]:HELMET[3], HELMET[0]:HELMET[2]], before[HELMET[1]:HELMET[3], HELMET[0]:HELMET[2]]))
check("the vest region is untouched, pixel for pixel", np.array_equal(frame[VEST[1]:VEST[3], VEST[0]:VEST[2]], before[VEST[1]:VEST[3], VEST[0]:VEST[2]]))
check("nothing outside the face box changed", np.count_nonzero(np.any(frame != before, axis=2)) < 60 * 60)

frame = textured()
before = frame.copy()
few = KPTS.copy()
few[1:, 2] = 0.0                                                   # only the nose is confident
used = PrivacyAnonymizer.blur_face_keypoints(frame, few, BBOX)
check("fewer than two confident face keypoints falls back to the head box and says so (False)", used is False)
check("...which covers the helmet region", variance(frame, HELMET) < 0.2 * variance(before, HELMET))
frame = textured()
check("keypoints of None fall back too", PrivacyAnonymizer.blur_face_keypoints(frame, None, BBOX) is False)
nan_kp = KPTS.copy()
nan_kp[0, 0] = float("nan")
nan_kp[1:, 2] = 0.0
check("a NaN keypoint is ignored, not propagated", PrivacyAnonymizer.blur_face_keypoints(textured(), nan_kp, BBOX) is False)
edge = KPTS.copy()
edge[:, 0] = edge[:, 0] - 120                                      # the face hangs off the left edge
check("a face partly or wholly outside the frame does not crash", PrivacyAnonymizer.blur_face_keypoints(textured(), edge, [-20, 100, 40, 400]) in (True, False))
check("two points only still give a face box of a sensible size (a fraction of the person's height)",
      (lambda b: b is not None and (b[2] - b[0]) >= 0.07 * 300)(PrivacyAnonymizer.face_box(np.array([[0, 0, 0]] * 1 + [[120, 140, .9], [124, 140, .9]] + [[0, 0, 0]] * 14, dtype=float), BBOX)))

print("   (strength)")
# A realistic close-up: a face 300 px wide in a 1280 px picture. A fixed 35 px blur leaves such a face
# recognisable; what must hold is that the coarse structure is gone, not just the fine detail.
rng2 = np.random.default_rng(3)
close = np.zeros((720, 1280, 3), np.uint8)
yy, xx = np.mgrid[0:720, 0:1280]
face_cx, face_cy = 640, 300
face = ((xx - face_cx) ** 2 / 150.0 ** 2 + (yy - face_cy) ** 2 / 190.0 ** 2) < 1
close[:] = (60, 80, 100)
close[face] = (150, 170, 210)
for ex in (-60, 60):                                       # eyes, nose, mouth as dark blobs
    close[(xx - (face_cx + ex)) ** 2 + (yy - (face_cy - 40)) ** 2 < 18 ** 2] = (30, 30, 30)
close[(xx - face_cx) ** 2 + (yy - (face_cy + 20)) ** 2 < 14 ** 2] = (90, 110, 160)
close[(np.abs(xx - face_cx) < 55) & (np.abs(yy - (face_cy + 90)) < 8)] = (40, 40, 120)
close = np.clip(close.astype(np.int16) + rng2.integers(-6, 7, close.shape), 0, 255).astype(np.uint8)
kp_big = np.zeros((17, 3), np.float32)
kp_big[0] = (face_cx, face_cy + 20, 0.9)
kp_big[1] = (face_cx - 60, face_cy - 40, 0.9)
kp_big[2] = (face_cx + 60, face_cy - 40, 0.9)
kp_big[3] = (face_cx - 130, face_cy - 20, 0.9)
kp_big[4] = (face_cx + 130, face_cy - 20, 0.9)
person_big = [face_cx - 250, 80, face_cx + 250, 720]
orig_close = close.copy()
box_big = PrivacyAnonymizer.face_box(kp_big, person_big)
check("a close-up face: the box is at least as wide as the head (eye spacing 120 px, ears 260 px apart)", (box_big[2] - box_big[0]) >= 290, f"{box_big[2] - box_big[0]:.0f}")
PrivacyAnonymizer.blur_face_keypoints(close, kp_big, person_big)
bx = [int(v) for v in box_big]
region_a, region_b = orig_close[bx[1]:bx[3], bx[0]:bx[2]], close[bx[1]:bx[3], bx[0]:bx[2]]
detail = lambda r: float(cv2.Laplacian(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
check("a large face keeps under 5% of its fine detail", detail(region_b) < 0.05 * detail(region_a), f"{detail(region_a):.1f} -> {detail(region_b):.1f}")
eyes_a = orig_close[face_cy - 58:face_cy - 22, face_cx - 78:face_cx - 42]
eyes_b = close[face_cy - 58:face_cy - 22, face_cx - 78:face_cx - 42]
check("the eyes are no longer distinct dark features", float(eyes_b.min()) > float(eyes_a.min()) + 40, f"{eyes_a.min()} -> {eyes_b.min()}")
check("the head above the box is untouched", np.array_equal(close[:bx[1] - 1], orig_close[:bx[1] - 1]))

# ── B. Rendering the picture ────────────────────────────────────────────
print()
print("B. The picture")
big = textured(1500, 2000)
orig_big = big.copy()
people = [{"track_id": 7, "bbox": [b * 1.0 for b in BBOX], "keypoints": KPTS}]
# a 2000x1500 original whose working frame was 640x480 (scale 3.125): coordinates are in the working frame
jpeg, obscured = snapshots.render(big, people, (480, 640), target_track=7,
                                  ppe_dets=[{"cls": "safety_vest", "conf": 0.91, "bbox": [105, 220, 155, 300]}], ppe_shape=(480, 640),
                                  zone_polygon=[[10, 10], [300, 10], [300, 450], [10, 450]],
                                  banner=["POTENTIAL VIOLATION (for review) | Press line | 00:12 | trk-7", "Entered Press line with no helmet seen in 28 of 29 clear frames."])
out = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
check("a large original is stored at most 1280 px on its longer side", max(out.shape[:2]) == 1280 and out.shape[:2] == (960, 1280), str(out.shape))
check("the caller's frame was not touched", np.array_equal(big, orig_big))
check("a located face for the flagged person: not obscured", obscured is False)
check("the banner is drawn (a dark bar across the top)", float(out[:40, 200:1200].mean()) < 40.0, f"{out[:40].mean():.0f}")
k = 1280 / 640.0
face_o = tuple(int(v * k) for v in FACE)
helm_o = tuple(int(v * k) for v in HELMET)
vest_o = tuple(int(v * k) for v in (110, 230, 150, 290))
small_ref = cv2.resize(orig_big, (1280, 960), interpolation=cv2.INTER_AREA)
check("the face in the saved picture is blurred", variance(out, face_o) < 0.2 * variance(small_ref, face_o), f"{variance(small_ref, face_o):.0f} -> {variance(out, face_o):.0f}")
check("the hard-hat region keeps its detail (not blurred; only JPEG loss)", variance(out, helm_o) > 0.5 * variance(small_ref, helm_o))
jpeg2, obscured2 = snapshots.render(textured(), [{"track_id": 7, "bbox": BBOX, "keypoints": None}], (480, 640), target_track=7)
check("no keypoints for the flagged person: head box blurred and the picture says the helmet may be hidden", obscured2 is True)
jpeg3, obscured3 = snapshots.render(textured(), [{"track_id": 8, "bbox": BBOX, "keypoints": None}, {"track_id": 7, "bbox": [300, 100, 360, 400], "keypoints": KPTS + np.array([170, 0, 0], np.float32)}],
                                    (480, 640), target_track=7)
check("another person's missing keypoints do not mark the flagged person's helmet as hidden", obscured3 is False)
# a head the pose model did not find: the helmet/vest model saw a helmet on someone not in `people`
missed = textured(480, 640)
helm = {"cls": "helmet", "conf": 0.9, "bbox": [400, 100, 450, 140]}
j_miss, _ = snapshots.render(missed, [], (480, 640), ppe_dets=[helm], ppe_shape=(480, 640))
pic_miss = cv2.imdecode(np.frombuffer(j_miss, np.uint8), cv2.IMREAD_COLOR)
below_helmet = (402, 124, 448, 175)
check("a person the pose model missed still gets the area under their helmet anonymised",
      variance(pic_miss, below_helmet) < 0.15 * variance(missed, below_helmet), f"{variance(missed, below_helmet):.0f} -> {variance(pic_miss, below_helmet):.0f}")
check("...but the helmet itself is left alone", variance(pic_miss, (405, 104, 445, 118)) > 0.25 * variance(missed, (405, 104, 445, 118)))
j_listed, _ = snapshots.render(missed, [{"track_id": 9, "bbox": [380, 90, 470, 400], "keypoints": None}], (480, 640), ppe_dets=[helm], ppe_shape=(480, 640))
check("a helmet on a person who IS listed does not add a second blur", cv2.imdecode(np.frombuffer(j_listed, np.uint8), 1) is not None)
j_with, _ = snapshots.render(textured(), [{"track_id": 7, "bbox": BBOX, "keypoints": KPTS}], (480, 640), target_track=7, banner=["a", "b"])
j_without, _ = snapshots.render(textured(), [{"track_id": 8, "bbox": BBOX, "keypoints": KPTS}], (480, 640), target_track=7, banner=["a", "b"])
dark_rows = lambda j: int((cv2.imdecode(np.frombuffer(j, np.uint8), 1)[:, 40:600].mean(axis=(1, 2)) < 30).sum())
check("when the flagged person is not in the picture the banner says so (an extra line)", dark_rows(j_without) > dark_rows(j_with), f"{dark_rows(j_with)} vs {dark_rows(j_without)} dark rows")
small = textured(300, 400)
jpeg4, _ = snapshots.render(small, [], (300, 400))
check("a small frame is kept at its size, and an empty scene is fine", cv2.imdecode(np.frombuffer(jpeg4, np.uint8), 1).shape[:2] == (300, 400))

# ── C. Paths ────────────────────────────────────────────────────────────
print()
print("C. Paths")
ORG = str(uuid.uuid4())
DID = str(uuid.uuid4())
check("a ref is <org>/<id>.jpg", snapshots.ref_for(ORG, DID) == f"{ORG}/{DID}.jpg")
check("no organisation files under 'local'", snapshots.ref_for(None, DID) == f"local/{DID}.jpg")
for bad_org, bad_id in (("org-1", DID), (ORG, "../../etc/passwd"), (ORG, "not-a-uuid"), (ORG, DID.upper()), ("../x", DID)):
    try:
        snapshots.ref_for(bad_org, bad_id)
        ok = False
    except ValueError:
        ok = True
    check(f"ref_for refuses {bad_org!r} / {bad_id!r}", ok)
base = snapshots.evidence_dir()
check("a valid ref resolves inside the evidence folder", snapshots.path_for_ref(f"{ORG}/{DID}.jpg") == base / ORG / f"{DID}.jpg")
for bad in (f"../{DID}.jpg", f"{ORG}/../{DID}.jpg", f"/{ORG}/{DID}.jpg", f"{ORG}/{DID}.png", f"{ORG}/{DID}", f"{ORG}/sub/{DID}.jpg",
            f"{ORG}\\{DID}.jpg", "", None, f"{ORG}/{DID}.jpg\x00", "C:/windows/x.jpg", f"..\\..\\{DID}.jpg", f"{ORG}/{DID.upper()}.jpg"):
    check(f"path_for_ref refuses {bad!r}", snapshots.path_for_ref(bad) is None)
ref = snapshots.save_snapshot(jpeg, ORG, DID)
check("save_snapshot writes the file atomically and returns the ref",
      ref == f"{ORG}/{DID}.jpg" and (base / ORG / f"{DID}.jpg").read_bytes() == jpeg and not list((base / ORG).glob("*.tmp")))

# ── D. The session ──────────────────────────────────────────────────────
print()
print("D. Which decisions get a picture")
ZONES = [{"zone_id": "press", "zone_name": "Press line", "zone_type": "RESTRICTED", "polygon": [[20, 20], [600, 20], [600, 460], [20, 460]]}]
CFG = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0)
CFG_UNV = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0, snapshot_unverifiable=True)
FRAME_SHAPE = (480, 640, 3)
_n = [0]


class Scene:
    def __init__(self):
        self.helmet, self.vest, self.boxes = {}, {}, {}
        self.fail = False

    def detect(self, frame, imgsz=None):
        if self.fail:
            raise RuntimeError("the detector is down")      # a failure is never evidence: the verdict stays UNVERIFIABLE
        dets = []
        for tid, b in self.boxes.items():
            x1, y1, x2, y2 = b
            h = y2 - y1
            if self.helmet.get(tid, True):
                dets.append({"cls": "helmet", "conf": 0.9, "bbox": [x1 + 0.2 * (x2 - x1), y1 - 0.03 * h, x2 - 0.2 * (x2 - x1), y1 + 0.17 * h]})
            if self.vest.get(tid, True):
                dets.append({"cls": "safety_vest", "conf": 0.9, "bbox": [x1 + 0.05 * (x2 - x1), y1 + 0.2 * h, x2 - 0.05 * (x2 - x1), y1 + 0.55 * h]})
        return dets


def make(scene, cfg=CFG, evidence=True, org=ORG):
    _n[0] += 1
    path = os.path.join(TMP, f"s{_n[0]}.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id=org, session_id="s1", engine=dbe, retry_interval_s=0.0)
    session = SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")), writer, cfg,
                            zones_config=ZONES, ppe_runner=PpeRunner(scene, cfg), evidence_enabled=evidence)
    return session, path, dbe


def rows(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM safety_decisions ORDER BY video_time_s, rowid")]
    finally:
        c.close()


async def walk(session, scene, frames=80, enter_at=30, hold=True, keypoints=KPTS, clean_is_working=False, tid=1):
    payloads, last_clean = [], None
    for i in range(frames):
        ft = session.begin_frame(i)
        b = list(BBOX)
        scene.boxes[tid] = b
        det = {"track_id": tid, "bbox": b, "keypoints": keypoints}
        clean = textured()
        working = clean if clean_is_working else cv2.resize(clean, (640, 480))
        await session.detect_ppe(ft, [det], clean, FRAME_SHAPE)
        if hold:
            session.hold_frame(clean, working, [det], ft)
        last_clean = (clean.copy(), working)
        await session.after_frame(ft, [{"track_id": tid, "zone_id": "press" if i >= enter_at else None, "bbox": b}])
        payloads.append(session.payload(ft))
        if clean_is_working:
            working[:] = 0                                       # what the pipeline's in-place blur does to the working frame
    await session.finish()
    return payloads, last_clean


def run(c):
    return asyncio.run(c)


def files():
    return sorted(str(p) for p in snapshots.evidence_dir().rglob("*.jpg"))


scene = Scene(); scene.helmet[1] = False
before_files = set(files())
s, path, dbe = make(scene)
payloads, (clean_last, _w) = run(walk(s, scene))
dec = rows(path)
flag = [d for d in dec if d["status"] == "POTENTIAL_VIOLATION"]
check("a worker with no helmet seen is flagged", len(flag) == 1 and "no helmet seen" in flag[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))
ref = flag[0]["snapshot_ref"]
check("the flagged decision carries a snapshot_ref <org>/<decision id>.jpg", ref == f"{ORG}/{flag[0]['id']}.jpg", str(ref))
pic_path = snapshots.path_for_ref(ref)
img = cv2.imdecode(np.fromfile(str(pic_path), np.uint8), cv2.IMREAD_COLOR) if pic_path and pic_path.exists() else None
check("...and the file exists and is a 640x480 JPEG", img is not None and img.shape[:2] == (480, 640))
check("the picture was made from the frame the decision was made on (its face is blurred; its hat region is not)",
      img is not None and variance(img, FACE_IN) < 0.15 * variance(clean_last, FACE_IN) and variance(img, HELMET) > 0.25 * variance(clean_last, HELMET),
      (f"face {variance(clean_last, FACE_IN):.0f}->{variance(img, FACE_IN):.0f}; hat {variance(clean_last, HELMET):.0f}->{variance(img, HELMET):.0f}" if img is not None else ""))
carried = [d for p in payloads for d in p.get("decisions", [])]
check("the dashboard payload says a picture was kept", len(carried) == 1 and carried[0]["snapshot"] is True)
check("a located face: the decision is not marked helmet-obscured", "helmet_region_obscured" not in json.loads(flag[0]["evidence"]))
check("exactly one new file for one flag", len(set(files()) - before_files) == 1)

scene = Scene()
before_files = set(files())
s, path, dbe = make(scene)
payloads, _ = run(walk(s, scene))
dec = rows(path)
check("an equipped worker is COMPLIANT and gets NO file", len(dec) == 1 and dec[0]["status"] == "COMPLIANT" and dec[0]["snapshot_ref"] is None and set(files()) == before_files)
check("...and the payload carries no 'snapshot' key for it", all("snapshot" not in d for p in payloads for d in p.get("decisions", [])))

scene = Scene()
before_files = set(files())
s, path, dbe = make(scene)
scene.helmet[1] = scene.vest[1] = False
run(walk(s, scene, frames=60, enter_at=10, hold=True))
# an UNVERIFIABLE decision: the detector fails, so there is never enough evidence and the engine abstains
scene = Scene(); scene.fail = True
s, path, dbe = make(scene)
run(walk(s, scene, frames=80, enter_at=10))
dec = rows(path)
check("an abstention (UNVERIFIABLE) gets no file by default", dec and all(d["status"] == "UNVERIFIABLE" and d["snapshot_ref"] is None for d in dec), str([(d["status"], d["snapshot_ref"]) for d in dec]))
scene = Scene(); scene.fail = True
s, path, dbe = make(scene, cfg=CFG_UNV)
run(walk(s, scene, frames=80, enter_at=10))
dec = rows(path)
check("...unless snapshot_unverifiable is on (then it is labelled 'could not verify')",
      dec and all(d["status"] == "UNVERIFIABLE" and d["snapshot_ref"] for d in dec), str([(d["status"], d["snapshot_ref"]) for d in dec]))

scene = Scene(); scene.helmet[1] = False
before_files = set(files())
s, path, dbe = make(scene, evidence=False)
payloads, _ = run(walk(s, scene))
dec = rows(path)
carried = [d for p in payloads for d in p.get("decisions", [])]
check("with the organisation's evidence switch off: the decision is stored, no file is written, and the payload says so",
      len(dec) == 1 and dec[0]["status"] == "POTENTIAL_VIOLATION" and dec[0]["snapshot_ref"] is None and set(files()) == before_files
      and carried and carried[0]["snapshot"] is False)

scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
payloads, _ = run(walk(s, scene, hold=False))
dec = rows(path)
check("with no clean frame held there is no picture (and no crash): the decision is stored without one", len(dec) == 1 and dec[0]["snapshot_ref"] is None)

scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene, org="org-not-a-uuid")
run(walk(s, scene))
dec = rows(path)
check("an organisation id that is not a uuid cannot name a folder: no picture, decision stored", len(dec) == 1 and dec[0]["snapshot_ref"] is None)

scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene, org=None)
run(walk(s, scene))
dec = rows(path)
check("standalone (no organisation): the picture is filed under local/", len(dec) == 1 and dec[0]["snapshot_ref"] == f"local/{dec[0]['id']}.jpg"
      and snapshots.path_for_ref(dec[0]["snapshot_ref"]).exists())

scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
run(walk(s, scene, keypoints=None))
dec = rows(path)
ev = json.loads(dec[0]["evidence"]) if dec else {}
check("no face keypoints: the head box is blurred and the stored evidence says the helmet may be obscured",
      len(dec) == 1 and ev.get("helmet_region_obscured") is True and dec[0]["snapshot_ref"] is not None, str(ev.get("helmet_region_obscured")))

# the banner names both times, and a stale PPE run's boxes are not drawn
captured = []
_real_render = snapshots.render
snapshots.render = lambda *a, **k: (captured.append(k.get("banner")), _real_render(*a, **k))[1]
scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
run(walk(s, scene))
snapshots.render = _real_render
check("the banner carries the entry time AND the time of the picture (they differ by the evidence wait)",
      captured and "entered 00:03" in captured[0][0] and "picture 00:0" in captured[0][0] and captured[0][0].count("entered") == 1, str(captured[:1]))
s2, _p, _e = make(Scene())
s2.ppe.last_detections = [{"cls": "helmet", "conf": 0.9, "bbox": [1, 1, 2, 2]}]
s2.ppe.last_ppe_shape = (480, 640)
s2.ppe._calls = 7
s2.ppe.last_detections_call = 6
ft = s2.begin_frame(0)
s2.hold_frame(textured(), textured(), [{"track_id": 1, "bbox": BBOX, "keypoints": KPTS}], ft)
check("boxes from an earlier detection run are not drawn on this frame", s2._held["ppe_dets"] == [])
s2.ppe.last_detections_call = 7
s2.hold_frame(textured(), textured(), [{"track_id": 1, "bbox": BBOX, "keypoints": KPTS}], ft)
check("boxes from this frame's run are", len(s2._held["ppe_dets"]) == 1)

before_files = set(files())
scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
s._evidence_refresh_s, s._evidence_check = 0.001, (lambda org: False)
run(walk(s, scene))
dec = rows(path)
check("an organisation that turns evidence off DURING a session stops getting pictures (the switch is re-read every few minutes)",
      len(dec) == 1 and dec[0]["snapshot_ref"] is None and set(files()) == before_files and s.evidence_enabled is False)
scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene, evidence=False)
s._evidence_refresh_s, s._evidence_check = 0.001, (lambda org: True)
run(walk(s, scene))
dec = rows(path)
check("...and turning it back on resumes them", len(dec) == 1 and dec[0]["snapshot_ref"] is not None)
scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
s._evidence_refresh_s, s._evidence_check = 0.001, (lambda org: (_ for _ in ()).throw(RuntimeError("postgres down")))
run(walk(s, scene))
check("a failed re-read fails closed (no picture) and does not break the session", rows(path)[0]["snapshot_ref"] is None and s.evidence_enabled is False)

print()
print("E. The clean frame survives the pipeline's in-place blur")
scene = Scene(); scene.helmet[1] = False
s, path, dbe = make(scene)
payloads, _ = run(walk(s, scene, clean_is_working=True))
dec = rows(path)
img = cv2.imdecode(np.fromfile(str(snapshots.path_for_ref(dec[0]["snapshot_ref"])), np.uint8), cv2.IMREAD_COLOR) if dec and dec[0]["snapshot_ref"] else None
check("when the working frame IS the original (not shrunk) the held copy is not the one the blur writes into",
      img is not None and float(img[130:260, 100:160].mean()) > 60.0, "the picture is black: the held frame was blurred/overwritten in place" if img is not None else "no picture")

# ── F. Expiry ───────────────────────────────────────────────────────────
print()
print("F. Expiry")
e = create_engine(f"sqlite:///{os.path.join(TMP, 'purge.sqlite')}", connect_args={"check_same_thread": False})
configure_sqlite(e, busy_timeout_ms=250)
ensure_safety_tables(e)
w = SafetyWriter("cam1", org_id=ORG, session_id="s", engine=e, retry_interval_s=0.0)
old_id = w.add_decision("PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION", "r", 0.9, {}, 1.0, "2026-11-08T04:00:00.000+00:00", zone_id="press",
                        snapshot_ref=f"{ORG}/{str(uuid.uuid4())}.jpg")
w.flush_sync()
old_uuid = old_id
new_uuid = str(uuid.uuid4())
old_ref, new_ref = snapshots.save_snapshot(jpeg, ORG, old_uuid), snapshots.save_snapshot(jpeg, ORG, new_uuid)
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET snapshot_ref = :r WHERE id = :i"), {"r": old_ref, "i": old_uuid})
old_path = snapshots.path_for_ref(old_ref)
stray = old_path.parent / "half-written.tmp"
stray.write_bytes(b"x")
long_ago = time.time() - 10 * 86400
os.utime(old_path, (long_ago, long_ago))
os.utime(stray, (long_ago, long_ago))
empty_org = snapshots.evidence_dir() / str(uuid.uuid4())
empty_org.mkdir(parents=True)
res = snapshots.purge_evidence(7, engine=e)
check("a picture older than the retention is deleted, a recent one stays", not old_path.exists() and snapshots.path_for_ref(new_ref).exists() and res["files"] == 1)
with sqlite3.connect(os.path.join(TMP, "purge.sqlite")) as c:
    left = c.execute("SELECT snapshot_ref FROM safety_decisions WHERE id = ?", (old_uuid,)).fetchall()
check("...its local snapshot_ref is cleared, and the decision row stays", left == [(None,)], str(left))
check("an unfinished write and an empty organisation folder are tidied up", not stray.exists() and not empty_org.exists())
keep_id = str(uuid.uuid4())
keep_ref = snapshots.save_snapshot(jpeg, ORG, keep_id)
keep_path = snapshots.path_for_ref(keep_ref)
os.utime(keep_path, (long_ago, long_ago))


class _BrokenEngine:
    def begin(self):
        raise RuntimeError("database locked")

    connect = begin


snapshots.purge_evidence(7, engine=_BrokenEngine())
check("if the references cannot be cleared the files are KEPT (so no reference is left pointing at nothing) and the next pass retries",
      keep_path.exists())
check("...and then succeeds", snapshots.purge_evidence(7, engine=e)["files"] == 1 and not keep_path.exists())
check("purging with nothing to do is harmless", snapshots.purge_evidence(7, engine=e)["files"] == 0)
check("retention below one day is refused, not applied", snapshots.purge_evidence(0, engine=e)["files"] == 0 and snapshots.path_for_ref(new_ref).exists())

# ── G. Serving ──────────────────────────────────────────────────────────
print()
print("G. The endpoint")
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception as ex:                                              # pragma: no cover
    print(f"  SKIP: no test client ({ex})")
    TestClient = None

if TestClient is not None:
    import app.api.routers.safety as safety_router

    ORG_B = str(uuid.uuid4())
    USER_M, USER_V = str(uuid.uuid4()), str(uuid.uuid4())
    ide = create_engine(f"sqlite:///{os.path.join(TMP, 'api.sqlite')}", connect_args={"check_same_thread": False})
    configure_sqlite(ide, busy_timeout_ms=250)
    ensure_safety_tables(ide)
    wa = SafetyWriter("cam1", org_id=ORG, session_id="s", engine=ide, retry_interval_s=0.0)
    d_ok = str(uuid.uuid4())
    d_ok = wa.add_decision("PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION", "r", 0.9, {}, 1.0, "2026-11-08T04:00:00.000+00:00",
                           zone_id="press", snapshot_ref=snapshots.save_snapshot(jpeg, ORG, str(uuid.uuid4())))
    wa.flush_sync()
    with ide.begin() as c:
        # point the decision at a real picture
        pic = snapshots.save_snapshot(jpeg, ORG, d_ok)
        c.execute(text("UPDATE safety_decisions SET snapshot_ref = :r WHERE id = :i"), {"r": pic, "i": d_ok})
    d_none = wa.add_decision("PPE_ENTRY", "WARNING", "trk-2", "POTENTIAL_VIOLATION", "r", 0.9, {}, 2.0, "2026-11-08T04:00:01.000+00:00", zone_id="press")
    d_gone = wa.add_decision("PPE_ENTRY", "WARNING", "trk-3", "POTENTIAL_VIOLATION", "r", 0.9, {}, 3.0, "2026-11-08T04:00:02.000+00:00", zone_id="press",
                             snapshot_ref=f"{ORG}/{str(uuid.uuid4())}.jpg")
    d_evil = wa.add_decision("PPE_ENTRY", "WARNING", "trk-4", "POTENTIAL_VIOLATION", "r", 0.9, {}, 4.0, "2026-11-08T04:00:03.000+00:00", zone_id="press",
                             snapshot_ref="../../../outside.jpg")
    wa.flush_sync()
    with ide.begin() as c:
        c.execute(text("UPDATE safety_decisions SET snapshot_ref = :r WHERE id = :i"), {"r": f"{ORG}/{d_gone}.jpg", "i": d_gone})
    wb = SafetyWriter("cam9", org_id=ORG_B, session_id="s", engine=ide, retry_interval_s=0.0)
    d_foreign = wb.add_decision("PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION", "r", 0.9, {}, 1.0, "2026-11-08T04:00:00.000+00:00", zone_id="press",
                                snapshot_ref=snapshots.save_snapshot(jpeg, ORG_B, str(uuid.uuid4())))
    wb.flush_sync()
    audits = []
    state = {"audit_fails": False}
    IDENTITIES = {"Bearer manager": (ORG, USER_M, "MANAGER"), "Bearer admin": (ORG, USER_M, "ADMIN"), "Bearer viewer": (ORG, USER_V, "VIEWER"),
                  "Bearer other": (ORG_B, str(uuid.uuid4()), "MANAGER"), "Bearer stranger": (ORG, USER_V, None)}
    _real_record_view = safety_router._record_view
    safety_router.resolve_identity = lambda token: IDENTITIES.get(token, (None, None, None))
    safety_router._default_engine = ide

    def fake_audit(org, user, decision):
        if state["audit_fails"]:
            raise RuntimeError("audit store down")
        audits.append((org, user, decision))
    safety_router._record_view = fake_audit
    app = FastAPI()
    app.include_router(safety_router.router, prefix="/api/v1/safety")
    client = TestClient(app)
    url = lambda d: f"/api/v1/safety/decisions/{d}/snapshot"
    hdr = lambda who: {"Authorization": who}

    r = client.get(url(d_ok))
    check("no token: 401", r.status_code == 401 and not audits)
    r = client.get(url(d_ok), headers=hdr("Bearer garbage"))
    check("an unverifiable token: 401", r.status_code == 401)
    r = client.get(url(d_ok), headers=hdr("Bearer viewer"))
    check("a VIEWER does not hold safety.evidence.view: 403, no audit row, no picture", r.status_code == 403 and r.headers.get("content-type", "").startswith("application/json") and not audits)
    r = client.get(url(d_ok), headers=hdr("Bearer stranger"))
    check("a member with no active role: 403", r.status_code == 403)
    r = client.get(url(d_ok), headers=hdr("Bearer manager"))
    check("a MANAGER gets the picture", r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == jpeg)
    check("...never cached", r.headers.get("cache-control") == "no-store" and r.headers.get("x-content-type-options") == "nosniff")
    check("...with exactly one audit row naming organisation, user and decision", audits == [(ORG, USER_M, d_ok)], str(audits))
    r = client.get(url(d_ok), headers=hdr("Bearer admin"))
    check("an ADMIN too (and that view is audited as well)", r.status_code == 200 and len(audits) == 2)
    n = len(audits)
    r = client.get(url(d_ok), headers=hdr("Bearer other"))
    check("another organisation's manager gets 404, exactly as for an id that does not exist", r.status_code == 404 and r.json() == client.get(url(str(uuid.uuid4())), headers=hdr("Bearer other")).json())
    r = client.get(url(d_foreign), headers=hdr("Bearer manager"))
    check("...and the other way round", r.status_code == 404)
    r = client.get(url(d_foreign), headers=hdr("Bearer other"))
    check("an organisation sees its own picture", r.status_code == 200)
    n = len(audits)
    for label, d in (("a decision with no picture", d_none), ("a malformed id", "not-a-uuid"), ("a traversal in the id", "..%2F..%2Fx"),
                     ("a stored reference that escapes the folder", d_evil)):
        r = client.get(url(d), headers=hdr("Bearer manager"))
        check(f"{label}: 404 and no audit row", r.status_code == 404 and len(audits) == n, str(r.status_code))
    r = client.get(url(d_gone), headers=hdr("Bearer manager"))
    check("a picture that has expired: 410 Gone, no audit row", r.status_code == 410 and len(audits) == n)
    state["audit_fails"] = True
    r = client.get(url(d_ok), headers=hdr("Bearer manager"))
    check("when the view cannot be audited the picture is NOT sent (503)", r.status_code == 503 and r.headers.get("content-type", "").startswith("application/json") and jpeg[:50] not in r.content)
    state["audit_fails"] = False
    IDENTITIES["Bearer anonymous-user"] = (ORG, None, "MANAGER")
    safety_router._record_view = _real_record_view
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    r = client.get(url(d_ok), headers=hdr("Bearer manager"))
    check("with no audit store configured the picture is NOT served (no audit, no picture)", r.status_code == 503)
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "stub-key"
    os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
    r = client.get(url(d_ok), headers=hdr("Bearer anonymous-user"))
    check("a viewer who cannot be identified is refused (the audit row must name someone)", r.status_code == 503)
    safety_router._record_view = fake_audit

    from app.api import deps
    deps._cache_put("good-token", ORG, USER_M, "MANAGER")
    check("resolve_identity returns organisation, user and role from ONE cache entry", deps.resolve_identity("Bearer good-token") == (ORG, USER_M, "MANAGER"))
    check("...and nothing for no token", deps.resolve_identity(None) == (None, None, None) and deps.resolve_identity("Bearer ") == (None, None, None))

    from app.api.permissions import can
    check("only ADMIN and MANAGER hold the capability, in the Python table", can("ADMIN", "safety.evidence.view") and can("MANAGER", "safety.evidence.view")
          and not can("VIEWER", "safety.evidence.view") and not can(None, "safety.evidence.view"))
    js = open(os.path.join(BACKEND, "..", "frontend", "app", "lib", "permissions.js"), encoding="utf-8").read()
    check("...and the JavaScript table agrees", "'safety.evidence.view': ['ADMIN', 'MANAGER']" in js)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
