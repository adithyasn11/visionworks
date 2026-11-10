"""
Man-down (Step 14, scenario S9): a collapsed, still person raises a CRITICAL alert within seconds,
without alarming on sitting, bending, reaching or crouching.

  A. The detector driven with synthetic pose keypoints, frame by frame (10 frames a second):
     standing, sitting upright, bending to pick something up (moving, and still for under the time),
     reaching under, crouching, lying still WITH the posture label SITTING (the classifier's quirk: it must
     still alert), lying then getting up in time, one bad frame inside a fall, a person who is crawling,
     weak keypoints (UNVERIFIABLE), a person cut by the picture's edge, a place where lying is allowed,
     a zone-anchored rule, one alert per episode, a second fall later, the timeline jumping, a person lost.
  B. The real chain: SafetySession + writer + database, the row that lands.

Run:  venv/Scripts/python.exe backend/tests/test_man_down.py
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import atexit
import shutil

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                                  # noqa: E402
from sqlalchemy import create_engine                                # noqa: E402

from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.compliance_engine import ComplianceEngine, is_evaluated   # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.man_down import ManDownDetector, ManDownMonitor, torso_angle   # noqa: E402
from app.safety.rules import parse_rule                            # noqa: E402
from app.safety.schemas import POTENTIAL_VIOLATION, UNVERIFIABLE   # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

FAILS = []
FPS = 10.0
W, H = 640, 480
CFG = SafetyConfig()


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   <- {detail}"))
    if not ok:
        FAILS.append(label)


ZONES = [
    {"zone_id": "press", "zone_name": "Press hall", "zone_type": "RESTRICTED"},
    {"zone_id": "rest", "zone_name": "Rest area", "zone_type": "GENERAL"},
]
ZBY = {z["zone_id"]: {"zone_type": z["zone_type"], "zone_name": z["zone_name"]} for z in ZONES}


def rule(zone=None, severity="CRITICAL", **params):
    return parse_rule({"type": "MAN_DOWN", "zoneId": zone, "params": params, "severity": severity, "name": "Man down"}, ZBY)


# ── synthetic people ────────────────────────────────────────────────────────

def kp(points, conf=0.9):
    a = np.zeros((17, 3))
    for i, (x, y) in points.items():
        a[i] = (x, y, conf)
    return a


def standing(cx=320, top=100, conf=0.9):
    return kp({5: (cx - 20, top + 40), 6: (cx + 20, top + 40), 11: (cx - 15, top + 100), 12: (cx + 15, top + 100)}, conf), \
        [cx - 30, top, cx + 30, top + 220]


def lying(cx=320, cy=300, conf=0.9, tilt=0):
    return kp({5: (cx - 60, cy - 20 + tilt), 6: (cx - 60, cy + 20 + tilt), 11: (cx, cy - 15), 12: (cx, cy + 15)}, conf), \
        [cx - 90, cy - 40, cx + 130, cy + 40]


def leaning(angle, cx=320, top=100):
    """Torso `angle` degrees from vertical (bending forward)."""
    import math
    dx, dy = 100 * math.sin(math.radians(angle)), 100 * math.cos(math.radians(angle))
    return kp({5: (cx - 20, top + 40), 6: (cx + 20, top + 40), 11: (cx - 20 + dx, top + 40 + dy), 12: (cx + 20 + dx, top + 40 + dy)}), \
        [cx - 40, top, cx + 40 + dx, top + 140 + dy]


class Run:
    """One person (or more) through a detector, frame by frame."""

    def __init__(self, r=None, **cfg):
        self.det = ManDownDetector(r or rule(), SafetyConfig(**cfg) if cfg else CFG, {"press": "Press hall"})
        self.t = 0.0
        self.out = []
        self.first_alert_t = None

    def feed(self, seconds, make, key="trk-1", zone=None, posture=None, jitter=0.0):
        n = int(round(seconds * FPS))
        for _ in range(n):
            k, b = make(self.t) if callable(make) else make
            if jitter:
                b = [v + jitter * np.sin(self.t * 7) for v in b]
            people = [{"worker_key": key, "keypoints": k, "bbox": b, "posture": posture}]
            got = self.det.update(people, self.t, W, H, {key: zone})
            if got and self.first_alert_t is None:
                self.first_alert_t = self.t
            self.out.extend(got)
            self.t += 1.0 / FPS
        return self

    def gap(self, seconds):
        for _ in range(int(round(seconds * FPS))):
            self.det.update([], self.t, W, H, {})
            self.t += 1.0 / FPS
        return self

    def kinds(self):
        return [(d.status, d.severity) for d in self.out]


# ── A ───────────────────────────────────────────────────────────────────────
print("A. The detector")

a = torso_angle(standing()[0]); b = torso_angle(lying()[0])
check("the torso angle: about 0 degrees standing, about 90 lying; None when a torso keypoint is weak",
      a is not None and a < 10 and b is not None and b > 80 and torso_angle(lying(conf=0.1)[0]) is None, f"{a} {b}")

r = Run().feed(30, standing())
check("standing still for 30 s: nothing", r.out == [])

r = Run().feed(30, standing(), posture="SITTING")
check("sitting upright (label SITTING, torso upright) for 30 s: nothing", r.out == [])

r = Run().feed(2, standing()).feed(3, lambda t: leaning(75, cx=320 + 40 * np.sin(t * 2)), jitter=0).feed(2, standing())
check("bending to pick something up (torso over 60 degrees, but the person is moving): nothing", r.out == [])

r = Run().feed(2, standing()).feed(3.5, leaning(75)).feed(2, standing())
check("reaching under something, still, for 3.5 s (under the 5 s): nothing", r.out == [])

r = Run().feed(2, standing()).feed(8, leaning(50)).feed(2, standing())
check("crouching or a deep bend (torso 50 degrees, below the lying angle), still, for 8 s: nothing", r.out == [])

r = Run().feed(2, standing()).feed(9, lying(), posture="SITTING")
check("lying still with the posture label SITTING (the classifier's quirk) still alerts: ONE potential violation, CRITICAL",
      r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))
d = r.out[0]
check("... time to alert is the rule's 5 s plus the moment it takes to be still (under 2 s), the picture is wanted and the reason asks for help",
      5.0 <= r.first_alert_t - 2.0 <= 7.0 and d.snapshot_requested and "check on them" in d.reason and d.evidence["outcome"] == "LYING_STILL"
      and d.evidence["posture_labels"].get("SITTING") and d.confidence >= 0.9 and d.rule_type == "MAN_DOWN", f"{r.first_alert_t} {d.reason}")
check("... the decision is stamped when the lying began, and carries the numbers it was made from",
      abs(d.video_time_s - 2.0) < 1.0 and d.evidence["seconds_down"] >= 5 and d.evidence["torso_angle_deg"] > 80 and d.thresholds["man_down_angle_deg"] == 60.0,
      str(d.evidence))

r = Run().feed(2, standing()).feed(3, lying()).feed(3, standing())
check("lying for 3 s, then getting up: nothing", r.out == [])
r = Run().feed(1, standing()).feed(4.2, lying()).feed(0.5, standing()).feed(0.5, standing(), posture="STANDING")
check("lying for 4 s and up for under a second does not reset the episode only if it resumes (here it ended: nothing)", r.out == [])

r = Run().feed(1, standing()).feed(3, lying()).feed(0.1, standing()).feed(5, lying())
check("one bad pose frame inside a fall does not reset it: still ONE alert", r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

r = Run().feed(1, standing()).feed(10, lambda t: lying(cx=200 + 60 * (t - 1.0)))
check("someone crawling or dragging along the floor (moving, torso flat): nothing", r.out == [], str(r.kinds()))

r = Run().feed(1, standing()).feed(30, lying())
check("lying for 30 s: exactly ONE decision for the episode", len(r.out) == 1)
r.feed(3, standing()).feed(9, lying())
check("... and getting up, then falling again later, is a second episode with its own decision", len(r.out) == 2, str(r.kinds()))

# weak keypoints
r = Run().feed(1, standing()).feed(14, lambda t: (kp({}, 0.0), lying()[1]))
check("a still wide box with no usable keypoints for twice the time: UNVERIFIABLE (a warning), no picture, not an alarm",
      [d.status for d in r.out] == [UNVERIFIABLE] and r.out[0].confidence == 0.0 and not r.out[0].snapshot_requested and "does not show the body clearly" in r.out[0].reason
      and r.out[0].evidence["outcome"] == "UNCLEAR", str([(d.status, d.reason) for d in r.out]))
r = Run().feed(1, standing()).feed(20, lambda t: (kp({}, 0.0), standing()[1]))
check("weak keypoints but a tall box (someone standing far away): nothing", r.out == [])

# a fall in a picture where only half of the people can be read: enough clear frames still alert
frames = [0]
def flicker(t):
    frames[0] += 1
    return lying() if frames[0] % 5 else (kp({}, 0.0), lying()[1])
r = Run().feed(1, standing()).feed(8, flicker)
check("lying with the keypoints lost on 1 frame in 5: still alerts (80 % of frames are clearly lying)", r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

# at the picture's edge
r = Run().feed(1, standing()).feed(9, (lying(cx=100, cy=300)[0], [0, 260, 220, 340]))
check("lying clearly at the picture's edge (wide box, flat torso): still alerts",
      r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

# bent over, on their feet
def bent(angle, cx=320, top=100):
    k, b = leaning(angle, cx, top)
    return k, [cx - 40, top, cx + 40 + 100 * np.sin(np.radians(angle)), top + 230]     # the legs keep the box tall
r = Run().feed(1, standing()).feed(30, bent(80))
check("bent over a bench or the floor for 30 s (torso 80 degrees, but the box is tall: they are on their feet): nothing, not even a warning", r.out == [], str(r.kinds()))
r = Run().feed(1, standing()).feed(30, bent(63))
check("... also leaning about 62 degrees", r.out == [], str(r.kinds()))
sq = lambda t: (leaning(80)[0], [250, 100, 480, 330])                                   # a squarish box with a flat torso
r = Run().feed(1, standing()).feed(12, sq)
check("a flat torso in a squarish box is not sure enough to alarm: at most a warning, never CRITICAL",
      all(d.status != POTENTIAL_VIOLATION for d in r.out), str(r.kinds()))

# unclear first, then a clear fall
r = Run().feed(1, standing()).feed(11, lambda t: (kp({}, 0.0), lying()[1])).feed(25, lying())
check("keypoints unreadable for the first 11 s of a fall, then clear while the person is still down: a warning first, then the alert (not lost)",
      [d.status for d in r.out] == [UNVERIFIABLE, POTENTIAL_VIOLATION], str([(d.status, d.severity) for d in r.out]))

# a slow frame rate
def slow(fps, seconds=14):
    r = Run()
    n = int(seconds * fps)
    for i in range(n):
        k, b = standing() if i < fps else lying()
        r.out.extend(r.det.update([{"worker_key": "trk-1", "keypoints": k, "bbox": b, "posture": "SITTING"}], i / fps, W, H, {}))
    return r
check("at 1.5 frames a second (a slow machine or a live camera): a fall is still found", [d.status for d in slow(1.5).out] == [POTENTIAL_VIOLATION], str(slow(1.5).kinds()))
check("... and at 1 frame a second", [d.status for d in slow(1.0).out] == [POTENTIAL_VIOLATION])

# the tracker hands out new ids
r = Run()
for i, key in enumerate(["trk-1", "trk-2", "trk-3", "trk-4"]):
    r.feed(1 if i == 0 else 0, standing(), key=key)
    r.feed(4 if i else 0, lying(), key=key)
r2 = Run().feed(1, standing(), key="trk-1")
for key in ["trk-2", "trk-3", "trk-4", "trk-5"]:
    r2.feed(3.0, lying(), key=key)
check("the tracker gives a new id every 3 s to a person lying on the same spot: the episode carries over and ONE alert is raised",
      [d.status for d in r2.out] == [POTENTIAL_VIOLATION], str([(d.worker_key, d.status) for d in r2.out]))
r3 = Run().feed(1, standing(), key="trk-1").feed(3.0, lying(), key="trk-2")
r3.feed(3.5, lying(cx=120), key="trk-9")
check("... but a different person elsewhere is not the same episode", r3.out == [], str(r3.kinds()))

# a zone rule when the ground point crosses the edge
flip = {"i": 0}
def zone_flicker():
    flip["i"] += 1
    return "press" if flip["i"] % 7 else None
zr = Run(rule(zone="press")).feed(1, standing(), zone="press")
for _ in range(100):
    zr.feed(0.1, lying(), zone=zone_flicker())
check("a person lying on a zone's edge (their ground point flickers in and out of it): still ONE alert", [d.status for d in zr.out] == [POTENTIAL_VIOLATION], str(zr.kinds()))

# lying allowed / zone-anchored rules
r = Run(rule(lying_allowed_zone_ids=["rest"])).feed(1, standing(), zone="rest").feed(12, lying(), zone="rest")
check("a zone where lying is allowed: nothing", r.out == [])
r = Run(rule(lying_allowed_zone_ids=["rest"])).feed(1, standing(), zone="press").feed(8, lying(), zone="press")
check("... the same fall outside it alerts", [d.status for d in r.out] == [POTENTIAL_VIOLATION])

r = Run(rule(zone="press")).feed(1, standing(), zone="press").feed(8, lying(), zone="press")
check("a rule anchored to a zone alerts for a fall in that zone, names it, and carries the zone id",
      [d.status for d in r.out] == [POTENTIAL_VIOLATION] and r.out[0].zone_id == "press" and "Press hall" in r.out[0].reason, str([(d.zone_id, d.reason) for d in r.out]))
r = Run(rule(zone="press")).feed(1, standing(), zone=None).feed(10, lying(), zone=None)
check("... and not for a fall outside it", r.out == [])
r = Run().feed(1, standing()).feed(8, lying())
check("a whole-camera rule has no zone on its decision", r.out and r.out[0].zone_id is None and " in " not in r.out[0].reason.split("lying")[0])

r = Run(rule(severity="INFO")).feed(1, standing()).feed(8, lying())
check("man-down is CRITICAL whatever severity the rule was saved with (a person down is never a mere warning)", r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

# the rule's own time
r = Run(rule(min_seconds_down=10)).feed(1, standing()).feed(20, lying())
check("min_seconds_down = 10: the alert comes at about 10 s, not 5", r.first_alert_t is not None and 10.0 <= r.first_alert_t - 1.0 <= 12.5, str(r.first_alert_t))

# two people: one up, one down
people_run = Run()
for i in range(100):
    t = i / FPS
    ks, bs = standing(cx=200)
    kl, bl = lying(cx=420)
    people_run.out.extend(people_run.det.update(
        [{"worker_key": "trk-1", "keypoints": ks, "bbox": bs, "posture": "STANDING"},
         {"worker_key": "trk-2", "keypoints": kl, "bbox": bl, "posture": "SITTING"}], t, W, H, {}))
check("two people, one standing and one lying: one alert, for the one lying", [(d.worker_key, d.status) for d in people_run.out] == [("trk-2", POTENTIAL_VIOLATION)],
      str([(d.worker_key, d.status) for d in people_run.out]))

# time jumps and lost people
r = Run().feed(1, standing()).feed(4, lying())
r.det.reset()
r.feed(4, lying())
check("the timeline jumping in the middle of a fall: nobody can say how long, so nothing (the clock starts again)", r.out == [])
r = Run().feed(1, standing()).feed(4, lying()).gap(3).feed(4, lying())
check("a person out of view for 3 s in the middle of a fall: the episode is cut, nothing", r.out == [], str(r.kinds()))
r = Run().feed(1, standing()).feed(4, lying()).gap(0.5).feed(3, lying())
check("... but a half-second dropout inside a fall does not cut it", r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

# jitter: a still person's box moves a little
r = Run().feed(1, standing()).feed(9, lying(), jitter=3.0)
check("normal tracker jitter (a few pixels) on a lying person is still 'still'", r.kinds() == [(POTENTIAL_VIOLATION, "CRITICAL")], str(r.kinds()))

# rules
check("MAN_DOWN is evaluated, and the compliance engine does not list it as 'not evaluated yet' or try to judge it",
      is_evaluated("MAN_DOWN", {}) and ComplianceEngine([rule()], None, CFG).unsupported == [] and ComplianceEngine([rule()], None, CFG).active == [])
check("a monitor with no MAN_DOWN rule is falsy (the session skips it)", not ManDownMonitor([], CFG) and bool(ManDownMonitor([rule()], CFG)))

# ── B ───────────────────────────────────────────────────────────────────────
print("B. The real chain")
tmp = tempfile.mkdtemp(prefix="mandown_")
atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))
path = os.path.join(tmp, "t.db")
dbe = create_engine(f"sqlite:///{path}")
configure_sqlite(dbe)
ensure_safety_tables(dbe)
writer = SafetyWriter("cam1", org_id="org-1", session_id="s1", engine=dbe, retry_interval_s=0.0)
session = SafetySession(SafetyEngine(FrameClock(fps=FPS, recorded_at="2026-11-08T04:00:00Z")), writer, CFG,
                        zones_config=ZONES, ppe_runner=None, rules=[rule()])


async def play():
    for i in range(160):
        t = i / FPS
        k, b = standing() if t < 2 else lying()
        det = [{"track_id": 7, "bbox": b, "keypoints": k, "posture": "SITTING" if t >= 2 else "STANDING", "confidence": 0.9}]
        ft = session.begin_frame(i)
        ent = [{"track_id": 7, "zone_id": None, "bbox": b}]
        await session.after_frame(ft, ent, detections=det, frame_shape=(H, W, 3))
    await session.finish()

asyncio.run(play())
c = sqlite3.connect(path)
c.row_factory = sqlite3.Row
rows = [dict(x) for x in c.execute("SELECT * FROM safety_decisions")]
c.close()
check("the session writes ONE MAN_DOWN decision row: CRITICAL, potential violation, no zone, a real confidence",
      len(rows) == 1 and rows[0]["rule_type"] == "MAN_DOWN" and rows[0]["severity"] == "CRITICAL" and rows[0]["status"] == "POTENTIAL_VIOLATION"
      and rows[0]["zone_id"] is None and 0.6 <= rows[0]["confidence"] <= 1.0 and rows[0]["worker_key"] == "trk-7", str(rows))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
