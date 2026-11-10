"""
More rules (Step 13): the maximum stay (MAX_DWELL, scenario S7), PPE kept on while inside
(PPE_CONTINUOUS) and the required exit (REQUIRED_EXIT, scenario S8).

Three layers:

  A. Rules: the new rule types validate and are reported as evaluated.
  B. The rule engine driven by the REAL zone tracker, frame by frame, with scripted PPE
     evidence. Boundaries (limit - 1 s and limit + 1 s), occlusion, a lost worker, a
     jump, the end of the video, evidence that turns unclear, a removal that comes back.
  C. The real chain: SafetySession + zone tracker + PPE runner + writer + database, with
     a synthetic detector, checking the rows that land in the database.

Run:  venv/Scripts/python.exe backend/tests/test_rules_more.py
"""

import asyncio
import json
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
from app.safety.compliance_engine import ComplianceEngine, is_evaluated, _dur   # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.ppe_runner import PpeRunner                        # noqa: E402
from app.safety.rules import RuleError, parse_rule                 # noqa: E402
from app.safety.schemas import COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE   # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402
from app.safety.zone_events import ZoneEventTracker                # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_rules_more_")
atexit.register(shutil.rmtree, TMP, True)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


ZONES = [
    {"zone_id": "press", "zone_name": "Press line", "zone_type": "RESTRICTED"},
    {"zone_id": "wash", "zone_name": "Wash-down", "zone_type": "EXIT"},
    {"zone_id": "walk", "zone_name": "Walkway", "zone_type": "APPROACH"},
    {"zone_id": "lawn", "zone_name": "Lawn", "zone_type": "GENERAL"},
    {"zone_id": "press2", "zone_name": "Second press", "zone_type": "RESTRICTED"},
]
ZBY = {z["zone_id"]: z for z in ZONES}
NAMES = {z["zone_id"]: z["zone_name"] for z in ZONES}
TYPES = {z["zone_id"]: z["zone_type"] for z in ZONES}


def rule(rule_type, zone="press", severity="WARNING", **params):
    return parse_rule({"type": rule_type, "zoneId": zone, "params": params, "severity": severity, "name": f"{rule_type} test"}, ZBY)


# ── A. Rules ────────────────────────────────────────────────────────────────
print("A. The new rule types")
check("MAX_DWELL, PPE_CONTINUOUS and REQUIRED_EXIT are evaluated; PPE_ENTRY only AT_ENTRY; MAN_DOWN too; the rest are not yet",
      is_evaluated("MAX_DWELL", {}) and is_evaluated("PPE_CONTINUOUS", {}) and is_evaluated("REQUIRED_EXIT", {})
      and is_evaluated("PPE_ENTRY", {}) and not is_evaluated("PPE_ENTRY", {"mode": "VIA_CHECKPOINT"})
      and not is_evaluated("OCCUPANCY", {}) and is_evaluated("MAN_DOWN", {}) and not is_evaluated("WALK_ONLY", {}))
r = rule("MAX_DWELL", max_seconds=30)
check("a stay limit fills in its warning fraction", r.params == {"max_seconds": 30.0, "warn_fraction": 0.8}, str(r.params))
r = rule("PPE_CONTINUOUS")
check("continuous PPE defaults to helmet, vest and a 3 s grace", r.params == {"required_ppe": ["helmet", "safety_vest"], "grace_seconds": 3.0}, str(r.params))
r = rule("REQUIRED_EXIT", exit_zone_id="wash", max_seconds_to_exit=20)
check("a required exit names an exit zone and a time", r.params == {"exit_zone_id": "wash", "max_seconds_to_exit": 20.0}, str(r.params))
for bad, why in ((lambda: rule("MAX_DWELL"), "a stay limit without a limit"), (lambda: rule("MAX_DWELL", max_seconds=5), "a stay limit under 10 s"),
                 (lambda: rule("REQUIRED_EXIT", exit_zone_id="walk"), "an exit that is not an EXIT zone"),
                 (lambda: rule("PPE_CONTINUOUS", zone="walk"), "continuous PPE on a non-restricted zone")):
    try:
        bad()
        check(f"refused: {why}", False)
    except RuleError:
        check(f"refused: {why}", True)

# ── B. The engine with the real zone tracker ───────────────────────────────
print("B. The rule engine, driven by the real zone tracker")
CFG = SafetyConfig()


def item(state, n=29, seen=0.0, empty=0.0, why=""):
    return {"state": state, "why": why or {"PRESENT": "seen in enough clear frames", "ABSENT": "no sign of it in nearly every clear frame",
                                            "UNVERIFIABLE": "evidence is mixed or ambiguous"}[state],
            "clear_frames": n, "seen_fraction": seen, "empty_fraction": empty, "mean_conf": 0.8}


PRESENT_ = item("PRESENT", seen=0.95, empty=0.05)
ABSENT_ = item("ABSENT", seen=0.04, empty=0.96)
UNSURE_ = item("UNVERIFIABLE", seen=0.4, empty=0.4)


class Sched:
    """PPE evidence by worker and time: fn(t) -> (helmet item, vest item)."""
    unavailable_reason = None

    def __init__(self):
        self.fn = {}

    def verdict(self, key, t):
        f = self.fn.get(key)
        if f is None:
            return None
        h, v = f(t)
        return {"status": "X", "items": {"helmet": h, "safety_vest": v}, "observed_frames": 30, "reason": ""}


class Rig:
    def __init__(self, rules, ppe=None, cfg=CFG):
        self.cfg = cfg
        self.tracker = ZoneEventTracker(cfg)
        self.ppe = ppe
        self.engine = ComplianceEngine(rules, ppe, cfg, zone_names=NAMES, zone_types=TYPES)
        for z, limit, warn in self.engine.dwell_watches():
            self.tracker.watch_dwell(z, limit, warn)
        self.events, self.decisions = [], []
        self.t = 0.0

    def step(self, t, zones, seen=True):
        """One frame at video time t. zones: {worker: zone or None}. seen=False: the workers listed are NOT in view."""
        evs = self.tracker.update(zones if seen else {}, None, t)
        self.t = t
        for e in evs:
            self.events.append(e)
            self.decisions += self.engine.on_event(e, t)
        self.decisions += self.engine.on_tick(t, set(zones) if seen else set())

    def run(self, t0, t1, zones, dt=0.5, seen=True):
        t = t0
        while t <= t1 + 1e-9:
            self.step(round(t, 6), zones, seen)
            t += dt

    def kinds(self, status=None):
        return [d for d in self.decisions if status is None or d.status == status]

    def ev(self, kind):
        return [e for e in self.events if e.event_type == kind]


# --- S7: the stay limit ---------------------------------------------------
LIM = 30.0
w = lambda zone: {"trk-1": zone}                                         # noqa: E731

g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None))
g.run(2.0, 26.0, w("press"))                                              # inside from 2.0; the 80 % warning is at 2.0 + 24
check("S7 the warning fires at 80 % of the limit and is an EVENT only: no decision, nothing to review",
      len(g.ev("DWELL_WARNING")) == 1 and g.kinds() == [], f"{[(e.event_type, e.video_time_s) for e in g.events]}")
g.run(26.5, 31.5, w("press"))
check("S7 just under the limit: still no decision", g.kinds() == [] and g.ev("DWELL_EXCEEDED") == [])
g.run(32.0, 33.0, w("press"))
v = g.kinds()
check("S7 reaching the limit: exactly one POTENTIAL_VIOLATION", len(v) == 1 and v[0].status == POTENTIAL_VIOLATION, str([(d.status, d.reason) for d in v]))
d0 = v[0]
check("S7 it says the stay and the limit, names the zone, never accuses",
      "Press line" in d0.reason and "30 s limit" in d0.reason and "violat" not in d0.reason.lower() and "should" not in d0.reason.lower(), d0.reason)
check("S7 stamped at the moment the limit was reached, with a picture requested, severity from the rule",
      abs(d0.video_time_s - 32.0) < 1e-6 and d0.snapshot_requested and d0.severity == "WARNING" and d0.rule_type == "MAX_DWELL")
check("S7 confidence 1.0 (a measured time), evidence holds the numbers",
      d0.confidence == 1.0 and d0.evidence["limit_seconds"] == 30.0 and d0.evidence["outcome"] == "EXCEEDED" and d0.evidence["entered_video_time_s"] == 2.0
      and d0.evidence["first_sighting"] is False and d0.thresholds["max_seconds"] == 30.0)
g.run(33.5, 60.0, w("press"))
check("S7 staying longer raises nothing more (one decision per visit)", len(g.kinds()) == 1)
g.run(60.5, 62.0, w(None))
check("S7 and leaving after the flag adds no second decision", len(g.kinds()) == 1)

# boundary: limit - 1 s and limit + 1 s
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.5, w("press")); g.run(31.0, 34.0, w(None))     # in for 2.0 .. 31.0  (29 s)
c = g.kinds()
check("S7 boundary: a stay of limit - 1 s is COMPLIANT, once, with the stay named",
      len(c) == 1 and c[0].status == COMPLIANT and "29 s" in c[0].reason and "within the 30 s limit" in c[0].reason and c[0].confidence == 1.0, str([(d.status, d.reason) for d in c]))
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 32.5, w("press")); g.run(33.0, 36.0, w(None))     # in for 2.0 .. 33.0  (31 s)
c = g.kinds()
check("S7 boundary: a stay of limit + 1 s is ONE POTENTIAL_VIOLATION (and no COMPLIANT when it leaves)",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION, str([(d.status, d.reason) for d in c]))

# first sighting
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 33.0, w("press"))
c = g.kinds()
check("a worker already inside when first seen who passes the limit: flagged, saying it is counted from when first seen",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and "first seen" in c[0].reason and c[0].evidence["first_sighting"] is True, str([(d.status, d.reason) for d in c]))
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 10.0, w("press")); g.run(10.5, 13.0, w(None))
c = g.kinds()
check("a worker already inside when first seen who leaves within the limit: UNVERIFIABLE (only part of the stay was seen), not COMPLIANT",
      len(c) == 1 and c[0].status == UNVERIFIABLE and c[0].confidence == 0.0 and "already inside" in c[0].reason, str([(d.status, d.reason) for d in c]))

# lost
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 8.0, w("press")); g.run(8.5, 25.0, w("press"), seen=False)
c = g.kinds()
check("a worker lost from view and not back within the lost limit: UNVERIFIABLE ('cannot state the stay'), never a claim",
      len(c) == 1 and c[0].status == UNVERIFIABLE and c[0].confidence == 0.0 and "lost from view" in c[0].reason and c[0].evidence["outcome"] == "LOST", str([(d.status, d.reason) for d in c]))

# occlusion shorter than the lost grace: same visit continues and the limit is still reached once
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 20.0, w("press")); g.run(20.5, 23.0, w("press"), seen=False); g.run(23.5, 40.0, w("press"))
c = g.kinds()
check("S10 an occlusion of a few seconds: the same visit continues, one violation at the limit, no UNVERIFIABLE",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and abs(c[0].video_time_s - 32.0) < 1e-6, str([(d.status, d.video_time_s) for d in c]))
# the limit falls inside the gap
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press")); g.run(30.5, 34.0, w("press"), seen=False); g.run(34.5, 38.0, w("press"))
c = g.kinds()
check("the limit reached while the worker was out of view: flagged once the worker is seen again, and the reason says part of it was unobserved",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and c[0].evidence["crossed_unobserved"] is True and "out of view" in c[0].reason, str([(d.status, d.reason) for d in c]))

# end of the video and a jump
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 12.0, w("press"))
out = g.engine.close(12.0)
check("the video ends mid-stay: UNVERIFIABLE 'the video ended', stamped at the entry",
      len(out) == 1 and out[0].status == UNVERIFIABLE and "video ended" in out[0].reason and out[0].video_time_s == 2.0 and "10 s" in out[0].reason, str([(d.status, d.reason) for d in out]))
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 12.0, w("press"))
out = g.engine.reset("SEEK", 12.0, 40.0)
check("the timeline jumps mid-stay: UNVERIFIABLE 'the video jumped'", len(out) == 1 and out[0].status == UNVERIFIABLE and "video jumped" in out[0].reason)
check("and a second close finds nothing left", g.engine.close(13.0) == [])

# two workers are independent; two rules on one zone
g = Rig([rule("MAX_DWELL", max_seconds=LIM)])
g.run(0.0, 1.5, {"trk-1": None, "trk-2": None})
g.run(2.0, 33.0, {"trk-1": "press", "trk-2": None})
g.run(33.5, 34.0, {"trk-1": "press", "trk-2": "press"})
c = g.kinds()
check("two workers are judged separately (only the one who stayed is flagged)", len(c) == 1 and c[0].worker_key == "trk-1")
g = Rig([rule("MAX_DWELL", max_seconds=20, severity="INFO"), rule("MAX_DWELL", max_seconds=30, severity="CRITICAL")])
g.run(0.0, 1.5, w(None)); g.run(2.0, 35.0, w("press"))
c = g.kinds()
check("two limits on one zone each raise their own decision at their own time and severity",
      sorted((d.severity, round(d.video_time_s)) for d in c) == [("CRITICAL", 32), ("INFO", 22)], str([(d.severity, d.video_time_s) for d in c]))

# a visit that began in replayed footage was never announced to the engine, but its limit event is new and is judged
from app.safety.zone_events import ZoneEvent                                  # noqa: E402
eng = ComplianceEngine([rule("MAX_DWELL", max_seconds=LIM)], None, CFG, zone_names=NAMES, zone_types=TYPES)
out = eng.on_event(ZoneEvent("DWELL_EXCEEDED", "trk-9", "press", 50.0, {"limit_s": 30.0}), 50.0)
check("a limit reached by a visit this engine never saw begin (it began in replayed footage) is still flagged, once",
      len(out) == 1 and out[0].status == POTENTIAL_VIOLATION and out[0].evidence["entered_video_time_s"] == 20.0
      and eng.on_event(ZoneEvent("DWELL_EXCEEDED", "trk-9", "press", 50.5, {"limit_s": 30.0}), 50.5) == [], str([(d.status, d.reason) for d in out]))

# a warning fraction of 100 % means no earlier warning; the limit still flags
g = Rig([rule("MAX_DWELL", max_seconds=LIM, warn_fraction=1.0)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 40.0, w("press"))
check("warn_fraction 1.0: no DWELL_WARNING event at all, and the limit still raises its one flag",
      g.ev("DWELL_WARNING") == [] and len(g.ev("DWELL_EXCEEDED")) == 1 and len(g.kinds()) == 1)

# --- R2: PPE kept on while inside ------------------------------------------
print("   -- PPE_CONTINUOUS")


def cont_rig(fn, rules=None, cfg=CFG, worker="trk-1"):
    ppe = Sched()
    ppe.fn[worker] = fn
    return Rig(rules or [rule("PPE_CONTINUOUS", grace_seconds=3.0)], ppe, cfg), ppe


def helmet_off_from(t0, until=None):
    return lambda t: (ABSENT_ if t >= t0 and (until is None or t < until) else PRESENT_, PRESENT_)


g, _ = cont_rig(helmet_off_from(10.0))
g.run(0.0, 1.5, w(None)); g.run(2.0, 40.0, w("press"))
c = g.kinds()
check("R2 a helmet confidently absent without a break for the grace: ONE POTENTIAL_VIOLATION, naming only the helmet",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and "no helmet seen" in c[0].reason and "vest" not in c[0].reason, str([(d.status, d.reason) for d in c]))
d0 = c[0]
check("R2 stamped when the run began (10.0), decided 3 s later, evidence carries the run and the items",
      d0.video_time_s == 10.0 and d0.evidence["episode_started_video_time_s"] == 10.0 and abs(d0.evidence["decided_video_time_s"] - 13.0) < 1e-6
      and d0.evidence["items"]["helmet"]["state"] == "ABSENT" and d0.evidence["mode"] == "CONTINUOUS" and d0.snapshot_requested)
check("R2 confidence is the missing item's 'nothing there' share; thresholds hold the grace",
      abs(d0.confidence - 0.96) < 1e-9 and d0.thresholds["grace_seconds"] == 3.0 and d0.rule_type == "PPE_CONTINUOUS")
check("R2 the reason says it was inside the named zone and for how long without a break", "Press line" in d0.reason and "3 s" in d0.reason, d0.reason)

g, _ = cont_rig(helmet_off_from(10.0, 12.5))                               # absent for 2.5 s, then back on
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press"))
check("R2 boundary: absent for grace - 0.5 s then seen again: nothing", g.kinds() == [])
g, _ = cont_rig(helmet_off_from(10.0, 13.5))                               # absent for 3.5 s
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press"))
check("R2 boundary: absent for grace + 0.5 s: flagged", len(g.kinds()) == 1)

g, _ = cont_rig(lambda t: ((ABSENT_ if (9.0 <= t < 11.0 or t >= 12.0) else (UNSURE_ if 11.0 <= t < 12.0 else PRESENT_)), PRESENT_))
g.run(0.0, 1.5, w(None)); g.run(2.0, 14.5, w("press"))
check("R2 evidence that turns unclear breaks the run (no decision, never a guess): absent 9-11, unclear 11-12, absent again: not yet flagged at 14.5",
      g.kinds() == [])
g.run(15.0, 16.0, w("press"))
check("R2 ... but the new run reaches the grace on its own and is then flagged", len(g.kinds()) == 1 and g.kinds()[0].video_time_s == 12.0, str([d.video_time_s for d in g.kinds()]))

g, _ = cont_rig(helmet_off_from(5.0))
g.run(0.0, 1.5, w(None)); g.run(2.0, 6.0, w("press")); g.run(6.5, 7.0, w("press"), seen=False); g.run(7.5, 9.5, w("press"))
check("R2 a worker out of view breaks the run: absent from 5 but out of view 6.5-7.0, so at 9.5 (2 s into the new run) nothing yet", g.kinds() == [])

g, _ = cont_rig(helmet_off_from(5.0))
g.run(0.0, 1.5, w(None)); g.run(2.0, 6.5, w("press")); g.run(7.0, 20.0, w(None)); g.run(20.5, 21.0, w("press"))
check("R2 leaving the zone before the grace: nothing (a visit that simply ends raises nothing)", g.kinds() == [])

cfg20 = SafetyConfig(realert_after_s=20.0)
g, _ = cont_rig(lambda t: ((ABSENT_ if (5.0 <= t < 15.0) or (22.0 <= t < 32.0) or (50.0 <= t < 60.0) else PRESENT_), PRESENT_), cfg=cfg20)
g.run(0.0, 1.5, w(None)); g.run(2.0, 70.0, w("press"))
c = g.kinds()
check("R2 one decision per episode; a removal soon after the last flag (inside the re-alert window) is the same story, a later one is a new decision",
      [round(d.video_time_s) for d in c] == [5, 50], str([d.video_time_s for d in c]))

# stepping out of the zone and back in while the helmet is still off is the same story, not a second flag
g, _ = cont_rig(helmet_off_from(5.0), cfg=cfg20)
g.run(0.0, 1.5, w(None)); g.run(2.0, 12.0, w("press")); g.run(12.5, 15.0, w(None)); g.run(15.5, 30.0, w("press"))
check("R2 leaving the zone and coming back inside the re-alert window with the helmet still off: still ONE decision",
      len(g.kinds()) == 1 and g.kinds()[0].video_time_s == 5.0, str([d.video_time_s for d in g.kinds()]))
g, _ = cont_rig(helmet_off_from(5.0), cfg=cfg20)
g.run(0.0, 1.5, w(None)); g.run(2.0, 12.0, w("press")); g.run(12.5, 40.0, w(None)); g.run(40.5, 55.0, w("press"))
check("R2 ... but coming back after the re-alert window is a new decision", len(g.kinds()) == 2, str([d.video_time_s for d in g.kinds()]))

g, ppe = cont_rig(helmet_off_from(5.0), rules=[rule("PPE_CONTINUOUS", required_ppe=["safety_vest"], grace_seconds=3.0)])
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press"))
check("R2 a rule that requires only the vest ignores a missing helmet", g.kinds() == [])

g, ppe = cont_rig(helmet_off_from(5.0))
ppe.unavailable_reason = "the helmet detector is not loaded"
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press"))
check("R2 with no PPE detector nothing is decided", g.kinds() == [])

g, _ = cont_rig(helmet_off_from(5.0), rules=[rule("PPE_CONTINUOUS", grace_seconds=3.0)])
g.engine.rules[0] = g.engine.rules[0]
floor = parse_rule({"type": "PPE_CONTINUOUS", "zoneId": "press", "params": {"grace_seconds": 3.0}, "minEvidenceConfidence": 0.99}, ZBY)
g, _ = cont_rig(helmet_off_from(5.0), rules=[floor])
g.run(0.0, 1.5, w(None)); g.run(2.0, 30.0, w("press"))
check("R2 a claim below the rule's evidence floor (0.96 < 0.99) is not made", g.kinds() == [])

g, _ = cont_rig(helmet_off_from(0.0))
g.run(0.0, 10.0, w("press"))
c = g.kinds()
check("R2 a worker first seen already inside is judged too, and the stamp is the start of the run",
      len(c) == 1 and c[0].evidence["first_sighting"] is True, str([(d.status, d.reason) for d in c]))

g, _ = cont_rig(helmet_off_from(5.0))
g.run(0.0, 1.5, w(None)); g.run(2.0, 9.0, w("press"))
g.engine.reset("SEEK", 9.0, 50.0)
check("R2 a jump clears the run and decides nothing", g.engine.on_tick(10.0, {"trk-1"}) == [] and g.engine.close(11.0) == [])

# --- R4: the required exit --------------------------------------------------
print("   -- REQUIRED_EXIT")
XR = lambda: rule("REQUIRED_EXIT", exit_zone_id="wash", max_seconds_to_exit=20.0)      # noqa: E731


def exit_rig(**kw):
    g = Rig([XR()], **kw)
    g.run(0.0, 1.5, w(None))
    g.run(2.0, 10.0, w("press"))                                         # inside; the exit is confirmed at ~11.0, stamped 10.5
    return g


g = exit_rig()
g.run(10.5, 17.5, w(None)); g.run(18.0, 20.0, w("wash"))
c = g.kinds()
check("S8 leaves the restricted zone and reaches the exit in time: COMPLIANT, with the time it took",
      len(c) == 1 and c[0].status == COMPLIANT and "Wash-down" in c[0].reason and "within 20 s" in c[0].reason and c[0].confidence == 1.0, str([(d.status, d.reason) for d in c]))
check("S8 stamped at the moment of leaving; evidence has the deadline and where they went",
      c[0].video_time_s == 10.5 and c[0].evidence["outcome"] == "REACHED_EXIT" and c[0].evidence["deadline_video_time_s"] == 30.5 and c[0].evidence["exit_zone"] == "Wash-down")

g = exit_rig()
g.run(10.5, 12.0, w(None)); g.run(12.5, 15.0, w("press2"))
c = g.kinds()
check("S8 leaves by the wrong route (into another RESTRICTED zone first): POTENTIAL_VIOLATION naming both zones, a picture wanted",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and "Second press" in c[0].reason and "Wash-down" in c[0].reason and c[0].snapshot_requested
      and c[0].evidence["outcome"] == "OTHER_ZONE" and c[0].evidence["went_to"] == "Second press", str([(d.status, d.reason) for d in c]))
check("S8 the wording states what was seen, nothing about intent", "not the required exit" in c[0].reason and "violat" not in c[0].reason.lower())
check("S8 a straight-away change of zone reads as a sentence, not 'straight away leaving'", "straight away" not in c[0].reason and c[0].reason.endswith("."), c[0].reason)

g = exit_rig()
g.run(10.5, 40.0, w(None))
c = g.kinds()
check("S8 still in view when the time runs out without having reached the exit: POTENTIAL_VIOLATION",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and c[0].evidence["outcome"] == "NOT_REACHED" and "still in view" in c[0].reason, str([(d.status, d.reason) for d in c]))
check("S8 ... decided just after the deadline plus the time an arrival takes to confirm, not before", 30.5 < c[0].evidence["decided_video_time_s"] <= 32.5, str(c[0].evidence["decided_video_time_s"]))

g = exit_rig()
g.run(10.5, 15.0, w(None)); g.run(15.5, 40.0, w(None), seen=False)
c = g.kinds()
check("S8 out of view when the time runs out: UNVERIFIABLE (the route cannot be judged), never a claim",
      len(c) == 1 and c[0].status == UNVERIFIABLE and c[0].confidence == 0.0 and c[0].evidence["outcome"] == "OUT_OF_VIEW" and "out of view" in c[0].reason, str([(d.status, d.reason) for d in c]))

g = exit_rig()
g.run(10.5, 29.5, w(None)); g.run(30.0, 33.0, w("wash"))
c = g.kinds()
check("S8 boundary: reaching the exit at limit - 1 s is COMPLIANT", len(c) == 1 and c[0].status == COMPLIANT, str([(d.status, d.reason) for d in c]))
g = exit_rig()
g.run(10.5, 32.0, w(None)); g.run(32.5, 36.0, w("wash"))
c = g.kinds()
check("S8 boundary: reaching the exit at limit + 2 s is a POTENTIAL_VIOLATION, once (not 'compliant' and not twice)",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION, str([(d.status, d.reason) for d in c]))

g = exit_rig()
g.run(10.5, 12.0, w(None)); g.run(12.5, 15.0, w("walk")); g.run(15.5, 17.0, w(None)); g.run(17.5, 20.0, w("wash"))
c = g.kinds()
check("S8 passing through an APPROACH zone on the way to the exit is fine: COMPLIANT when the exit is reached in time",
      len(c) == 1 and c[0].status == COMPLIANT, str([(d.status, d.reason) for d in c]))
g = exit_rig()
g.run(10.5, 12.0, w(None)); g.run(12.5, 40.0, w("walk"))
c = g.kinds()
check("S8 ... but staying in the approach zone and never reaching the exit is still flagged when the time runs out",
      len(c) == 1 and c[0].status == POTENTIAL_VIOLATION and c[0].evidence["outcome"] == "NOT_REACHED", str([(d.status, d.reason) for d in c]))

g = exit_rig()
g.run(10.5, 13.0, w(None)); g.run(13.5, 20.0, w("press")); g.run(20.5, 60.0, w("press"))
check("going back into the zone cancels the timer: they did not leave, nothing is decided", g.kinds() == [])

g = Rig([XR()])
g.run(0.0, 1.5, w(None)); g.run(2.0, 10.0, w("press")); g.run(10.5, 40.0, w("press"), seen=False)
c = g.kinds()
check("a worker LOST inside the zone (never seen leaving) starts no timer: no decision from the exit rule", c == [] and g.ev("EXITED_ZONE") and g.ev("EXITED_ZONE")[0].details.get("reason") == "LOST")

g = Rig([XR()])
g.run(0.0, 1.5, w(None)); g.run(2.0, 10.0, w("press")); g.run(10.5, 12.5, w("wash"))
c = g.kinds()
check("straight from the zone into the exit (no gap): COMPLIANT, and a few seconds at most",
      len(c) == 1 and c[0].status == COMPLIANT and c[0].evidence["elapsed_seconds"] <= 1.5, str([(d.status, d.reason) for d in c]))

g = exit_rig()
g.run(10.5, 14.0, w(None))
out = g.engine.close(14.0)
check("the video ends while the route is being followed: UNVERIFIABLE 'the video ended'", len(out) == 1 and out[0].status == UNVERIFIABLE and "video ended" in out[0].reason, str([(d.status, d.reason) for d in out]))
g = exit_rig()
g.run(10.5, 14.0, w(None))
out = g.engine.reset("SEEK", 14.0, 60.0)
check("a jump while the route is being followed: UNVERIFIABLE 'the video jumped', and the timer is gone",
      len(out) == 1 and "video jumped" in out[0].reason and g.engine.on_tick(80.0, set()) == [])

# unknown visibility is read as "out of view": the honest direction
g = exit_rig()
g.run(10.5, 15.0, w(None))
g.decisions += g.engine.on_tick(40.0)
c = g.kinds()
check("when the caller cannot say who is in view, a timer that runs out is UNVERIFIABLE, never a violation", len(c) == 1 and c[0].status == UNVERIFIABLE)

# the three rules together; disabled rules; a camera with none of them
cfgA = SafetyConfig()
many = [rule("MAX_DWELL", max_seconds=LIM), rule("REQUIRED_EXIT", exit_zone_id="wash", max_seconds_to_exit=20.0), rule("PPE_CONTINUOUS", grace_seconds=3.0)]
ppe = Sched()
ppe.fn["trk-1"] = lambda t: (PRESENT_, PRESENT_)
g = Rig(many, ppe, cfgA)
g.run(0.0, 1.5, w(None)); g.run(2.0, 12.0, w("press")); g.run(12.5, 14.0, w(None)); g.run(14.5, 18.0, w("wash"))
st = sorted((d.rule_type, d.status) for d in g.kinds())
check("the three rules on one zone and one worker each judge on their own: a short stay with equipment, leaving by the exit",
      st == [("MAX_DWELL", COMPLIANT), ("REQUIRED_EXIT", COMPLIANT)], str(st))
check("the dwell watches are registered for the tracker", g.engine.dwell_watches() == [("press", 30.0, 0.8)])
off = rule("MAX_DWELL", max_seconds=LIM)
off = type(off)(**{**off.__dict__, "enabled": False})
g = Rig([off])
g.run(0.0, 1.5, w(None)); g.run(2.0, 60.0, w("press"))
check("a disabled rule does nothing and registers no watch", g.kinds() == [] and g.engine.dwell_watches() == [] and g.ev("DWELL_EXCEEDED") == [])
check("durations read naturally", [_dur(x) for x in (29, 30, 0.4, 59.96, 125, 3700, 7200)] == ["29 s", "30 s", "0.4 s", "60 s", "2 min 5 s", "1 h 1 min", "2 h"], str([_dur(x) for x in (29, 30, 0.4, 59.96, 125, 3700, 7200)]))

# ── C. The real chain ───────────────────────────────────────────────────────
print("C. SafetySession end to end (zone tracker + PPE runner + evidence + writer + database)")
_n = [0]
FRAME = np.zeros((480, 640, 3), np.uint8)
SHAPE = FRAME.shape
CFG_C = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0)


class Scene:
    def __init__(self):
        self.helmet, self.vest, self.boxes = {}, {}, {}

    def detect(self, frame, imgsz=None):
        dets = []
        for tid, b in self.boxes.items():
            x1, y1, x2, y2 = b
            h = y2 - y1
            if self.helmet.get(tid, True):
                dets.append({"cls": "helmet", "conf": 0.9, "bbox": [x1 + 0.2 * (x2 - x1), y1 - 0.03 * h, x2 - 0.2 * (x2 - x1), y1 + 0.17 * h]})
            if self.vest.get(tid, True):
                dets.append({"cls": "safety_vest", "conf": 0.9, "bbox": [x1 + 0.05 * (x2 - x1), y1 + 0.2 * h, x2 - 0.05 * (x2 - x1), y1 + 0.55 * h]})
        return dets


def make(scene, rules, cfg=CFG_C, fps=10.0):
    _n[0] += 1
    path = os.path.join(TMP, f"c{_n[0]}.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id="org-1", session_id="s1", engine=dbe, retry_interval_s=0.0)
    runner = PpeRunner(scene, cfg)
    session = SafetySession(SafetyEngine(FrameClock(fps=fps, recorded_at="2026-11-08T04:00:00Z")), writer, cfg,
                            zones_config=ZONES, ppe_runner=runner, rules=rules)
    return session, path


def rows(path, table="safety_decisions"):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        order = "id" if table == "safety_events" else "video_time_s, rowid"
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    finally:
        c.close()


def box(i=0, tall=300):
    return [100.0 + 120 * i, 100.0, 160.0 + 120 * i, 100.0 + tall]


async def walk(session, scene, workers, frames, zone_of, start_frame=0):
    payloads = []
    for k in range(frames):
        i = start_frame + k
        ft = session.begin_frame(i)
        dets, ents = [], []
        for tid, (bi, tall) in workers.items():
            b = box(bi, tall)
            scene.boxes[tid] = b
            dets.append({"track_id": tid, "bbox": b})
        await session.detect_ppe(ft, dets, FRAME, SHAPE)
        for tid, (bi, tall) in workers.items():
            ents.append({"track_id": tid, "zone_id": zone_of(tid, i), "bbox": box(bi, tall)})
        await session.after_frame(ft, ents)
        payloads.append(session.payload(ft))
    return payloads


def run(coro):
    return asyncio.run(coro)


# S7 through the session: in at frame 20 (2.0 s), limit 20 s -> warning at 18 s, exceeded at 22 s
scene = Scene()
s, path = make(scene, [rule("MAX_DWELL", max_seconds=20)])
pl = run(walk(s, scene, {1: (0, 300)}, 300, lambda tid, i: "press" if i >= 20 else None))
run(s.finish())
dec, evs = rows(path), rows(path, "safety_events")
check("S7 real chain: the warning is stored as an event and the limit as ONE flagged decision",
      [e["event_type"] for e in evs if e["event_type"].startswith("DWELL")] == ["DWELL_WARNING", "DWELL_EXCEEDED"]
      and len([d for d in dec if d["rule_type"] == "MAX_DWELL"]) == 1 and dec[0]["status"] == "POTENTIAL_VIOLATION", str([(d["status"], d["reason"]) for d in dec]))
check("S7 real chain: stamped at the moment the limit was reached (22.0 s) with the matching wall clock",
      dec[0]["video_time_s"] == 22.0 and dec[0]["observed_at"] == "2026-11-08T04:00:22.000+00:00", f"{dec[0]['video_time_s']} {dec[0]['observed_at']}")
carried = [p_ for p_ in pl if "decisions" in p_]
check("S7 real chain: the dashboard payload carries the flag once and counts it open",
      len(carried) == 1 and carried[0]["decisions"][0]["rule_type"] == "MAX_DWELL" and pl[-1]["open_count"] == 1)

# a stay that ends within the limit
scene = Scene()
s, path = make(scene, [rule("MAX_DWELL", max_seconds=20)])
run(walk(s, scene, {1: (0, 300)}, 200, lambda tid, i: "press" if 20 <= i < 120 else None))
run(s.finish())
dec = rows(path)
check("S7 real chain: a 10 s stay within a 20 s limit is COMPLIANT", len(dec) == 1 and dec[0]["status"] == "COMPLIANT" and "within the 20 s limit" in dec[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))

# the video ends mid-stay
scene = Scene()
s, path = make(scene, [rule("MAX_DWELL", max_seconds=60)])
run(walk(s, scene, {1: (0, 300)}, 100, lambda tid, i: "press" if i >= 20 else None))
run(s.finish())
dec = rows(path)
check("S7 real chain: the video ends with the worker still inside: ONE UNVERIFIABLE decision, 'the video ended'",
      len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and "video ended" in dec[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))

# S8 through the session: leaves press at 5 s and walks into the walkway instead of the wash-down
scene = Scene()
s, path = make(scene, [rule("REQUIRED_EXIT", exit_zone_id="wash", max_seconds_to_exit=20)])
route = lambda tid, i: "press" if 10 <= i < 50 else ("press2" if i >= 60 else None)      # noqa: E731
run(walk(s, scene, {1: (0, 300)}, 150, route))
run(s.finish())
dec = rows(path)
check("S8 real chain: leaving into another restricted zone instead of the exit is ONE POTENTIAL_VIOLATION naming both zones",
      len(dec) == 1 and dec[0]["status"] == "POTENTIAL_VIOLATION" and "Second press" in dec[0]["reason"] and "Wash-down" in dec[0]["reason"] and dec[0]["rule_type"] == "REQUIRED_EXIT",
      str([(d["status"], d["reason"]) for d in dec]))
ev = json.loads(dec[0]["evidence"])
check("S8 real chain: stamped at the moment of leaving (5.0 s) and the evidence says where they went",
      dec[0]["video_time_s"] == 5.0 and ev["went_to"] == "Second press" and json.loads(dec[0]["thresholds"])["max_seconds_to_exit"] == 20.0, f"{dec[0]['video_time_s']}")

scene = Scene()
s, path = make(scene, [rule("REQUIRED_EXIT", exit_zone_id="wash", max_seconds_to_exit=20)])
route = lambda tid, i: "press" if 10 <= i < 50 else ("wash" if i >= 90 else None)       # noqa: E731
run(walk(s, scene, {1: (0, 300)}, 150, route))
run(s.finish())
dec = rows(path)
check("S8 real chain: leaving by the exit is COMPLIANT", len(dec) == 1 and dec[0]["status"] == "COMPLIANT", str([(d["status"], d["reason"]) for d in dec]))

# R2 through the session: the helmet comes off 6 s into the stay and stays off
scene = Scene()
s, path = make(scene, [rule("PPE_CONTINUOUS", grace_seconds=3.0)])


async def helmet_story():
    out = []
    for lo, hi in ((0, 100), (100, 400)):
        scene.helmet[1] = hi == 100
        out += await walk(s, scene, {1: (0, 300)}, hi - lo, lambda tid, i: "press" if i >= 20 else None, start_frame=lo)
    return out


# frames 0-99 equipped; from frame 100 (10 s) the helmet is gone for the rest of the stay
async def story():
    out = await walk(s, scene, {1: (0, 300)}, 100, lambda tid, i: "press" if i >= 20 else None, start_frame=0)
    scene.helmet[1] = False
    out += await walk(s, scene, {1: (0, 300)}, 300, lambda tid, i: "press" if i >= 20 else None, start_frame=100)
    return out


pl = run(story())
run(s.finish())
dec = rows(path)
cont = [d for d in dec if d["rule_type"] == "PPE_CONTINUOUS"]
check("R2 real chain: a helmet that comes off and stays off while inside is ONE POTENTIAL_VIOLATION naming the helmet",
      len(cont) == 1 and cont[0]["status"] == "POTENTIAL_VIOLATION" and "no helmet seen" in cont[0]["reason"] and "vest" not in cont[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))
check("R2 real chain: flagged some seconds after it came off (the evidence window and the grace), not at once and not never",
      10.0 < cont[0]["video_time_s"] < 20.0, str(cont[0]["video_time_s"]))
check("R2 real chain: the row carries the run, the items and the thresholds",
      json.loads(cont[0]["evidence"])["mode"] == "CONTINUOUS" and json.loads(cont[0]["evidence"])["items"]["helmet"]["state"] == "ABSENT"
      and json.loads(cont[0]["thresholds"])["grace_seconds"] == 3.0 and cont[0]["confidence"] >= 0.9)

# equipped for the whole stay: nothing
scene = Scene()
s, path = make(scene, [rule("PPE_CONTINUOUS", grace_seconds=3.0)])
run(walk(s, scene, {1: (0, 300)}, 300, lambda tid, i: "press" if i >= 20 else None))
run(s.finish())
check("R2 real chain: equipped for the whole stay: no decision at all", rows(path) == [])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
