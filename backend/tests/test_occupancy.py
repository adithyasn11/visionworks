"""
Lone worker and occupancy (Step 15, scenario S9): "never alone in this zone" and "no more than N people".

  A. The detector, one frame at a time (10 frames a second): one person for 12 s (flagged), two people (not),
     the grace boundary, a person leaving so the zone empties (not a lone worker), a one-frame dropout (not), a
     second person hidden behind something (not alone), too many people for long enough and for a moment, a crowd
     (UNVERIFIABLE), a crowd that separates, one decision per episode, the same story not re-flagged, a short break
     inside an episode, two zones, the timeline jumping.
  B. The monitor: the "clear" test (confidence, overlap, size) from real detection boxes.
  C. The real chain: SafetySession + zone tracker + writer + database.

Run:  venv/Scripts/python.exe backend/tests/test_occupancy.py
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import atexit
import shutil

_EVID = tempfile.mkdtemp(prefix="occ_evid_")
os.environ["SAFETY_EVIDENCE_DIR"] = _EVID
atexit.register(lambda: shutil.rmtree(_EVID, ignore_errors=True))
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
from app.safety.occupancy import OccupancyDetector, OccupancyMonitor   # noqa: E402
from app.safety.rules import RuleError, parse_rule                 # noqa: E402
from app.safety.schemas import POTENTIAL_VIOLATION, UNVERIFIABLE   # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

FAILS = []
FPS = 10.0
CFG = SafetyConfig()


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   <- {detail}"))
    if not ok:
        FAILS.append(label)


ZONES = [
    {"zone_id": "press", "zone_name": "Press hall", "zone_type": "RESTRICTED"},
    {"zone_id": "dock", "zone_name": "Loading dock", "zone_type": "APPROACH"},
    {"zone_id": "lawn", "zone_name": "Lawn", "zone_type": "GENERAL"},
]
ZBY = {z["zone_id"]: {"zone_type": z["zone_type"], "zone_name": z["zone_name"]} for z in ZONES}


def rule(zone="press", severity="WARNING", **params):
    return parse_rule({"type": "OCCUPANCY", "zoneId": zone, "params": params, "severity": severity, "name": "Head count"}, ZBY)


def folk(n, clear=True, start=1):
    return [{"worker_key": f"trk-{start + i}", "clear": clear} for i in range(n)]


class Run:
    def __init__(self, r=None, **cfg):
        self.det = OccupancyDetector(r or rule(min_people=2), SafetyConfig(**cfg) if cfg else CFG)
        self.t = 0.0
        self.out = []

    def feed(self, seconds, people, hidden=0):
        for _ in range(int(round(seconds * FPS))):
            ppl = people(self.t) if callable(people) else people
            self.out.extend(self.det.update(self.t, ppl, hidden(self.t) if callable(hidden) else hidden))
            self.t += 1.0 / FPS
        return self

    def kinds(self):
        return [(d.status, d.evidence["outcome"]) for d in self.out]


# ── A ───────────────────────────────────────────────────────────────────────
print("A. The detector")

check("OCCUPANCY is evaluated, and the compliance engine leaves it to occupancy.py",
      is_evaluated("OCCUPANCY", {}) and ComplianceEngine([rule(min_people=2)], None, CFG).unsupported == [])
try:
    rule()
    check("a rule with neither min_people nor max_people is refused", False)
except RuleError:
    check("a rule with neither min_people nor max_people is refused", True)

r = Run().feed(2, folk(2)).feed(12, folk(1))
check("one person alone for 12 s with min_people = 2: ONE potential violation (LONE_WORKER) naming the zone and the rule",
      r.kinds() == [(POTENTIAL_VIOLATION, "LONE_WORKER")] and "Press hall" in r.out[0].reason and "at least 2" in r.out[0].reason
      and "Only 1 person was seen" in r.out[0].reason and r.out[0].worker_key == "trk-1" and r.out[0].snapshot_requested
      and r.out[0].zone_id == "press" and r.out[0].rule_type == "OCCUPANCY", str([(d.status, d.reason) for d in r.out]))
check("... stamped when the person became alone, with the numbers it was made from",
      abs(r.out[0].video_time_s - 2.1) < 1.3 and r.out[0].evidence["people_seen"] == 1 and r.out[0].evidence["clear_fraction"] == 1.0
      and r.out[0].thresholds["grace_seconds"] == 10.0, str(r.out[0].evidence))

r = Run().feed(30, folk(2))
check("two people for 30 s: nothing", r.out == [])
r = Run().feed(2, folk(2)).feed(9, folk(1)).feed(10, folk(2))
check("alone for 9 s (grace 10 s) and then joined: nothing", r.out == [])
r = Run().feed(2, folk(2)).feed(11.5, folk(1))
check("alone for 11.5 s: flagged", r.kinds() == [(POTENTIAL_VIOLATION, "LONE_WORKER")], str(r.kinds()))

r = Run().feed(2, folk(2)).feed(3, folk(1)).feed(30, folk(0))
check("the last person leaves so the zone is empty: nothing (an empty zone is not a lone worker)", r.out == [])
r = Run().feed(30, folk(0))
check("an empty zone for 30 s: nothing", r.out == [])

n = {"i": 0}
def dropout(t):
    n["i"] += 1
    return folk(1) if n["i"] % 9 else folk(0)
r = Run().feed(2, folk(2)).feed(14, dropout)
check("a lone worker the detector drops for a frame now and then is still ONE flag (the median ignores the gaps)", len(r.out) == 1, str(r.kinds()))
m = {"i": 0}
def two_dropout(t):
    m["i"] += 1
    return folk(2) if m["i"] % 9 else folk(1)
r = Run().feed(40, two_dropout)
check("two people with one dropped for a frame in nine: nothing", r.out == [])

r = Run().feed(2, folk(2)).feed(14, folk(1), hidden=1)
check("a second person the tracker still holds as inside but cannot see (behind a pillar): not alone, nothing", r.out == [], str(r.kinds()))
r = Run().feed(2, folk(2)).feed(14, folk(0), hidden=1)
check("... and nobody in view at all is never a lone worker", r.out == [])

# episodes
r = Run().feed(2, folk(2)).feed(40, folk(1))
check("alone for 40 s: still exactly ONE decision for the episode", len(r.out) == 1)
r = Run().feed(2, folk(2)).feed(12, folk(1)).feed(3, folk(2)).feed(12, folk(1))
check("alone, joined for 3 s, alone again: the same story inside the re-alert window, not flagged again", len(r.out) == 1, str(r.kinds()))
r = Run(rule(min_people=2), realert_after_s=5.0).feed(2, folk(2)).feed(12, folk(1)).feed(3, folk(2)).feed(12, folk(1))
check("... but a new episode after the re-alert window is flagged", len(r.out) == 2, str(r.kinds()))
r = Run().feed(2, folk(2)).feed(6, folk(1)).feed(0.5, folk(2)).feed(6, folk(1))
check("a half-second of company inside a lone stretch does not restart it: it is flagged", len(r.out) == 1, str(r.kinds()))

# a person who has just left is not "alone": nothing is flagged on a frame with nobody there
r = Run().feed(2, folk(2)).feed(9.8, folk(1)).feed(3, folk(0))
check("a lone person who leaves just before the grace runs out is not flagged after the zone is empty", r.out == [], str(r.kinds()))
# the flag is stamped for the stretch that was judged, and says how long that was
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(12, folk(5, clear=False)).feed(12, folk(5))
check("after a 'could not tell', the later flag counts and stamps only the clear stretch (about 10 s, not 24)",
      len(r.out) == 2 and r.out[1].status == POTENTIAL_VIOLATION and 9.5 <= r.out[1].evidence["seconds"] <= 11 and r.out[1].video_time_s > r.out[0].video_time_s + 5,
      str([(d.status, d.evidence["seconds"], d.video_time_s) for d in r.out]))
# a long gap in the frames drops the old samples
r = Run().feed(2, folk(1))
r.t += 30
r.feed(3, folk(2))
check("a long gap in the frames (30 s) forgets the earlier count", r.out == [])

# slow frame rates
def slow(fps, seconds, people):
    d = OccupancyDetector(rule(min_people=2), CFG)
    out = []
    for i in range(int(seconds * fps)):
        t = i / fps
        out.extend(d.update(t, people(t), 0))
    return out
check("at 1 frame a second a lone worker is still found", [x.status for x in slow(1.0, 40, lambda t: folk(2) if t < 3 else folk(1))] == [POTENTIAL_VIOLATION])
check("... and at half a frame a second", [x.status for x in slow(0.5, 80, lambda t: folk(2) if t < 3 else folk(1))] == [POTENTIAL_VIOLATION])
check("... while two people at half a frame a second raise nothing", slow(0.5, 80, lambda t: folk(2)) == [])

# crowding
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(12, folk(4))
check("4 people where 3 are allowed for 12 s: ONE potential violation (OVER_CAPACITY) with the zone's key",
      r.kinds() == [(POTENTIAL_VIOLATION, "OVER_CAPACITY")] and r.out[0].worker_key == "zone-press" and "at most 3" in r.out[0].reason
      and "4 people were seen" in r.out[0].reason, str([(d.status, d.worker_key, d.reason) for d in r.out]))
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(6, folk(4)).feed(10, folk(3))
check("over the limit for 6 s (grace 10 s): nothing (momentary excess)", r.out == [])
k = {"i": 0}
def spike(t):
    k["i"] += 1
    return folk(4) if k["i"] % 20 == 0 else folk(3)
r = Run(rule(max_people=3)).feed(30, spike)
check("one extra detection for a single frame in twenty: nothing", r.out == [])
r = Run(rule(min_people=2, max_people=4)).feed(2, folk(3)).feed(20, folk(3))
check("a rule with both limits and a count in range: nothing", r.out == [])
r = Run(rule(min_people=2, max_people=4)).feed(2, folk(3)).feed(12, folk(5))
check("... and over the upper limit: flagged as over capacity (not as a lone worker)", r.kinds() == [(POTENTIAL_VIOLATION, "OVER_CAPACITY")], str(r.kinds()))

# unclear
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(12, folk(5, clear=False))
check("5 people in a crowd where none can be counted clearly (overlapping): UNVERIFIABLE, no picture, no accusation",
      r.kinds() == [(UNVERIFIABLE, "CROWDED")] and r.out[0].confidence == 0.0 and not r.out[0].snapshot_requested
      and "could not be trusted" in r.out[0].reason, str([(d.status, d.reason) for d in r.out]))
r = Run().feed(2, folk(2)).feed(12, folk(1, clear=False))
check("a lone person who is not clearly visible (so a second person could be hidden): UNVERIFIABLE, not a flag", r.kinds() == [(UNVERIFIABLE, "CROWDED")], str(r.kinds()))
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(12, folk(5, clear=False)).feed(12, folk(5, clear=True))
check("a crowd that separates while still over the limit: first 'could not tell', then, after a clear grace, the flag",
      r.kinds() == [(UNVERIFIABLE, "CROWDED"), (POTENTIAL_VIOLATION, "OVER_CAPACITY")], str(r.kinds()))
r = Run(rule(max_people=3)).feed(2, folk(3)).feed(40, folk(5, clear=False))
check("a crowd that stays unreadable says so ONCE", [d.status for d in r.out] == [UNVERIFIABLE], str(r.kinds()))
mix = lambda t: folk(1) + [{"worker_key": "trk-9", "clear": False}]               # noqa: E731
r = Run(rule(max_people=1)).feed(2, folk(1)).feed(12, mix)
check("2 people seen where 1 is allowed, one of them not clear (half the people are clear: 50 % is under the 60 % needed): UNVERIFIABLE",
      r.kinds() == [(UNVERIFIABLE, "CROWDED")], str(r.kinds()))

# zones and jumps
det = OccupancyDetector(rule(min_people=2), CFG)
mon = OccupancyMonitor([rule(min_people=2), rule(zone="dock", min_people=2)], CFG, {"press": "Press hall", "dock": "Loading dock"})
check("two rules on two zones make two detectors; no rules make a falsy monitor", len(mon.detectors) == 2 and not OccupancyMonitor([], CFG))

r = Run().feed(2, folk(2)).feed(7, folk(1))
r.det.reset()
r.feed(7, folk(1))
check("the timeline jumping in the middle of a lone stretch: nobody can say how long, so nothing", r.out == [], str(r.kinds()))

# ── B: the monitor and the clear test ───────────────────────────────────────
print("B. The monitor")


def ent(tid, zone="press"):
    return {"track_id": tid, "zone_id": zone, "bbox": [0, 0, 1, 1]}


def det(tid, box, conf=0.9):
    return {"track_id": tid, "bbox": box, "confidence": conf}


wk = lambda tid: f"trk-{tid}"                                                                   # noqa: E731
mon = OccupancyMonitor([rule(min_people=2)], CFG, {"press": "Press hall"})
flags = mon._clear_flags([det(1, [10, 10, 60, 160]), det(2, [200, 10, 250, 160]), det(3, [400, 10, 450, 160], conf=0.3),
                          det(4, [10, 300, 20, 310]), det(5, [100, 10, 150, 160]), det(6, [110, 10, 160, 160])])
check("clear: a confident, separate, full-size person is clear",
      flags[1] and flags[2])
check("not clear: low confidence, tiny, and two people on top of each other", not flags[3] and not flags[4] and not flags[5] and not flags[6], str(flags))

mon = OccupancyMonitor([rule(min_people=2)], CFG, {"press": "Press hall"})
out = []
for i in range(160):
    t = i / FPS
    two = i < 20
    ents = [ent(1), ent(2)] if two else [ent(1)]
    dets = [det(1, [10, 10, 60, 160]), det(2, [200, 10, 250, 160])] if two else [det(1, [10, 10, 60, 160])]
    out.extend(mon.update(t, ents, dets, wk, lambda z: 0))
check("through the monitor: two people then one for 14 s: ONE lone-worker flag naming trk-1",
      [(d.status, d.worker_key) for d in out] == [(POTENTIAL_VIOLATION, "trk-1")], str([(d.status, d.worker_key) for d in out]))
# a person standing on the zone edge: feet outside the polygon on most frames, but the tracker has them inside
mon = OccupancyMonitor([rule(min_people=2)], CFG, {"press": "Press hall"})
out = []
for i in range(160):
    edge = ent(2, "press" if i % 5 == 0 else None)
    out.extend(mon.update(i / FPS, [ent(1), edge], [det(1, [10, 10, 60, 160]), det(2, [200, 10, 250, 160])], wk, lambda z: 0,
                          lambda key: "press"))
check("a second person on the zone edge (feet mostly outside, but confirmed inside by the tracker) still counts: not alone", out == [], str([(d.status, d.reason) for d in out]))
mon = OccupancyMonitor([rule(min_people=2)], CFG, {"press": "Press hall"})
out = []
for i in range(160):
    edge = ent(2, "press" if i % 5 == 0 else None)
    out.extend(mon.update(i / FPS, [ent(1), edge], [det(1, [10, 10, 60, 160]), det(2, [200, 10, 250, 160])], wk, lambda z: 0))
check("... where the tracker is not asked, the same edge person is missed (so the session must give it)", len(out) == 1, str(out))

mon = OccupancyMonitor([rule(min_people=2)], CFG, {"press": "Press hall"})
out = []
for i in range(160):
    out.extend(mon.update(i / FPS, [ent(1, "dock"), ent(2, "dock")], [det(1, [10, 10, 60, 160]), det(2, [200, 10, 250, 160])], wk, lambda z: 0))
check("people standing in another zone do not count for this zone: an empty press hall is nothing", out == [])

# ── C: the real chain ───────────────────────────────────────────────────────
print("C. The real chain")
tmp = tempfile.mkdtemp(prefix="occ_")
atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))


def session_for(rules, evidence=False):
    path = os.path.join(tmp, f"t{len(os.listdir(tmp))}.db")
    dbe = create_engine(f"sqlite:///{path}")
    configure_sqlite(dbe)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id="11111111-1111-4111-8111-111111111111", session_id="s1", engine=dbe, retry_interval_s=0.0)
    return SafetySession(SafetyEngine(FrameClock(fps=FPS, recorded_at="2026-11-08T04:00:00Z")), writer, CFG,
                         zones_config=ZONES, ppe_runner=None, rules=rules, evidence_enabled=evidence), path


def rows(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(x) for x in c.execute("SELECT * FROM safety_decisions ORDER BY video_time_s")]
    finally:
        c.close()


async def play(session, schedule, frames, hold=False):
    for i in range(frames):
        t = i / FPS
        people = schedule(t)                     # [(track id, zone)]
        dets = [{"track_id": tid, "bbox": [20 + 120 * tid, 40, 70 + 120 * tid, 200], "keypoints": None, "posture": "STANDING", "confidence": 0.9}
                for tid, _ in people]
        ents = [{"track_id": tid, "zone_id": z, "bbox": d["bbox"]} for (tid, z), d in zip(people, dets)]
        ft = session.begin_frame(i)
        if hold:
            img = np.full((360, 640, 3), 90, np.uint8)
            session.hold_frame(img, img.copy(), dets, ft)
        await session.after_frame(ft, ents, detections=dets, frame_shape=(360, 640, 3))
    await session.finish()


s, path = session_for([rule(min_people=2)])
asyncio.run(play(s, lambda t: [(1, "press"), (2, "press")] if t < 3 else [(1, "press"), (2, None)], 200))
got = rows(path)
check("session: two people, then one walks out of the zone and one stays for 17 s: writes ONE OCCUPANCY decision row: potential violation, the rule's severity, the zone, worker trk-1",
      len(got) == 1 and got[0]["rule_type"] == "OCCUPANCY" and got[0]["status"] == "POTENTIAL_VIOLATION" and got[0]["severity"] == "WARNING"
      and got[0]["zone_id"] == "press" and got[0]["worker_key"] == "trk-1" and 0.6 <= got[0]["confidence"] <= 1.0, str(got))

s, path = session_for([rule(min_people=2)])
asyncio.run(play(s, lambda t: [(1, "press"), (2, "press")] if t < 3 else [(1, "press")], 320))
# the second person vanishes from view (not seen leaving): the tracker holds them as inside for lost_ttl_s (8 s), so the
# lone worker is not flagged until that time has passed and the grace has run after it
got = rows(path)
check("session: the second person vanishes from view at 3 s (not seen leaving): no flag while the tracker still holds them inside, then one",
      len(got) == 1 and got[0]["video_time_s"] >= 9.0, str([(g["video_time_s"], g["status"]) for g in got]))

s, path = session_for([rule(max_people=2, severity="CRITICAL")])
asyncio.run(play(s, lambda t: [(1, "press"), (2, "press"), (3, "press")], 200))
got = rows(path)
check("session: three people where two are allowed: ONE over-capacity row keyed to the zone",
      len(got) == 1 and got[0]["worker_key"] == "zone-press" and got[0]["severity"] == "CRITICAL" and got[0]["status"] == "POTENTIAL_VIOLATION", str(got))

s, path = session_for([rule(max_people=2)], evidence=True)
asyncio.run(play(s, lambda t: [(1, "press"), (2, "press"), (3, "press")], 200, hold=True))
got = rows(path)
pic = got[0]["snapshot_ref"] if got else None
check("session: an over-capacity decision (no single worker to point at) still keeps its evidence picture",
      len(got) == 1 and pic and any(pic.split("/")[-1] in f for _r, _d, fs in os.walk(_EVID) for f in fs), str(got))
s, path = session_for([rule(min_people=2)], evidence=True)
asyncio.run(play(s, lambda t: [(1, "press"), (2, "press")] if t < 3 else [(1, "press"), (2, None)], 200, hold=True))
got = rows(path)
check("session: a lone-worker decision keeps its evidence picture too", len(got) == 1 and got[0]["snapshot_ref"], str(got))

s, path = session_for([rule(min_people=2)])
asyncio.run(play(s, lambda t: [(1, "dock")], 200))
check("session: a lone person in another zone is not this rule's business", rows(path) == [])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
