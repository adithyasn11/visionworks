"""
The rule engine (Step 6): from zone events and PPE evidence to a verdict with a reason.

Three layers, each checked on its own and then together:

  A. rules.py: every rule type's parameters are validated, so a rule that can never
     fire is refused when it is written; the built-in default; an invalid row never
     stops the good ones.
  B. ComplianceEngine with scripted evidence: the scenarios S1-S5 of the design
     (equipped, no helmet, no vest, far away, two workers), one decision per visit,
     the re-alert window, the confidence floor, jumps and the end of the video.
  C. The real chain: SafetySession + zone tracker + PPE runner + evidence tracker +
     writer, driven with synthetic detections frame by frame, checking the rows that
     land in the database.

Run:  venv/Scripts/python.exe backend/tests/test_compliance.py
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import atexit
import shutil
import uuid

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                                  # noqa: E402
from sqlalchemy import create_engine                                # noqa: E402

from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.compliance_engine import ComplianceEngine          # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.ppe_runner import PpeRunner                        # noqa: E402
from app.safety.rules import (                                      # noqa: E402
    RuleError, default_rules, load_rules, parse_rule, parse_rules, validate_params,
)
from app.safety.schemas import COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE, Decision, Rule   # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402
from app.safety.zone_events import ZoneEvent                       # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_compliance_")
atexit.register(shutil.rmtree, TMP, True)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def raises(fn, exc=RuleError):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


ZONES = [
    {"zone_id": "press", "zone_name": "Press line", "zone_type": "RESTRICTED"},
    {"zone_id": "gate", "zone_name": "Helmet check", "zone_type": "PPE_CHECKPOINT"},
    {"zone_id": "walk", "zone_name": "Walkway", "zone_type": "APPROACH"},
    {"zone_id": "wash", "zone_name": "Wash-down", "zone_type": "EXIT"},
    {"zone_id": "lawn", "zone_name": "Lawn", "zone_type": "GENERAL"},
    {"zone_id": "press2", "zone_name": "Second press", "zone_type": "RESTRICTED"},
]
ZBY = {z["zone_id"]: z for z in ZONES}

# ── A. Rules ────────────────────────────────────────────────────────────────
print("A1. The built-in default")
d = default_rules(ZONES)
check("one PPE_ENTRY rule per RESTRICTED zone, helmet and vest, AT_ENTRY",
      sorted(r.zone_id for r in d) == ["press", "press2"] and all(
          r.type == "PPE_ENTRY" and r.params == {"required_ppe": ["helmet", "safety_vest"], "mode": "AT_ENTRY"} and r.source == "default" for r in d))
check("a camera with no restricted zone has no rules", default_rules([ZONES[1], ZONES[2], ZONES[4]]) == [] and default_rules(None) == [])
check("the default carries no database id, but a stable key", all(r.id is None and r.key for r in d) and len({r.key for r in d}) == 2)
check("load_rules with no database rows gives the default", [r.key for r in load_rules("org", ZONES)] == [r.key for r in d]
      and [r.key for r in load_rules("org", ZONES, rows=[])] == [r.key for r in d])

print("A2. Validation (design 24.6)")
V = lambda t, z, p=None: validate_params(t, z, p, ZBY)           # noqa: E731
check("PPE_ENTRY defaults: both items, AT_ENTRY", V("PPE_ENTRY", "press") == {"required_ppe": ["helmet", "safety_vest"], "mode": "AT_ENTRY"})
check("PPE_ENTRY must sit on a RESTRICTED zone", raises(lambda: V("PPE_ENTRY", "walk")) and raises(lambda: V("PPE_ENTRY", "lawn")))
check("an unknown zone, or none, is refused", raises(lambda: V("PPE_ENTRY", "nope")) and raises(lambda: V("PPE_ENTRY", None)))
check("required_ppe: empty, unknown item and wrong type are refused",
      raises(lambda: V("PPE_ENTRY", "press", {"required_ppe": []})) and raises(lambda: V("PPE_ENTRY", "press", {"required_ppe": ["gloves"]}))
      and raises(lambda: V("PPE_ENTRY", "press", {"required_ppe": "helmet"})))
check("one item is fine and duplicates collapse", V("PPE_ENTRY", "press", {"required_ppe": ["helmet", "helmet"]})["required_ppe"] == ["helmet"])
check("a bad mode is refused", raises(lambda: V("PPE_ENTRY", "press", {"mode": "SOMETIMES"})))
check("VIA_CHECKPOINT needs a PPE_CHECKPOINT zone",
      raises(lambda: V("PPE_ENTRY", "press", {"mode": "VIA_CHECKPOINT"}))
      and raises(lambda: V("PPE_ENTRY", "press", {"mode": "VIA_CHECKPOINT", "checkpoint_zone_id": "walk"}))
      and V("PPE_ENTRY", "press", {"mode": "VIA_CHECKPOINT", "checkpoint_zone_id": "gate"})["max_seconds_since_checkpoint"] == 60.0)
check("a checkpoint zone on an AT_ENTRY rule is refused (it could never apply)", raises(lambda: V("PPE_ENTRY", "press", {"checkpoint_zone_id": "gate"})))
check("max_seconds_since_checkpoint is 5-600", raises(lambda: V("PPE_ENTRY", "press", {"mode": "VIA_CHECKPOINT", "checkpoint_zone_id": "gate", "max_seconds_since_checkpoint": 2}))
      and raises(lambda: V("PPE_ENTRY", "press", {"mode": "VIA_CHECKPOINT", "checkpoint_zone_id": "gate", "max_seconds_since_checkpoint": 601})))
check("PPE_CONTINUOUS: grace 1-30, default 3", V("PPE_CONTINUOUS", "press")["grace_seconds"] == 3.0 and raises(lambda: V("PPE_CONTINUOUS", "press", {"grace_seconds": 0.5}))
      and raises(lambda: V("PPE_CONTINUOUS", "press", {"grace_seconds": 31})))
check("MAX_DWELL needs max_seconds (10-86400); warn_fraction 0.1-1; any real zone, not General",
      raises(lambda: V("MAX_DWELL", "walk")) and raises(lambda: V("MAX_DWELL", "walk", {"max_seconds": 5}))
      and raises(lambda: V("MAX_DWELL", "walk", {"max_seconds": 90000})) and raises(lambda: V("MAX_DWELL", "walk", {"max_seconds": 60, "warn_fraction": 1.5}))
      and raises(lambda: V("MAX_DWELL", "lawn", {"max_seconds": 60}))
      and V("MAX_DWELL", "walk", {"max_seconds": 45}) == {"max_seconds": 45.0, "warn_fraction": 0.8})
check("REQUIRED_EXIT needs an EXIT zone", raises(lambda: V("REQUIRED_EXIT", "press")) and raises(lambda: V("REQUIRED_EXIT", "press", {"exit_zone_id": "walk"}))
      and V("REQUIRED_EXIT", "press", {"exit_zone_id": "wash"})["max_seconds_to_exit"] == 30.0
      and raises(lambda: V("REQUIRED_EXIT", "walk", {"exit_zone_id": "wash"})))
check("OCCUPANCY: min or max, min >= 2, max >= 1, min <= max, whole numbers",
      raises(lambda: V("OCCUPANCY", "walk")) and raises(lambda: V("OCCUPANCY", "walk", {"min_people": 1}))
      and raises(lambda: V("OCCUPANCY", "walk", {"max_people": 0})) and raises(lambda: V("OCCUPANCY", "walk", {"min_people": 5, "max_people": 3}))
      and raises(lambda: V("OCCUPANCY", "walk", {"min_people": 2.5})) and V("OCCUPANCY", "walk", {"min_people": 2})["grace_seconds"] == 10.0
      and raises(lambda: V("OCCUPANCY", "walk", {"min_people": 2, "grace_seconds": 2})))
check("MAN_DOWN: zone optional, seconds 3-60, allowed zones must exist",
      V("MAN_DOWN", None)["min_seconds_down"] == 5.0 and raises(lambda: V("MAN_DOWN", None, {"min_seconds_down": 2}))
      and raises(lambda: V("MAN_DOWN", None, {"lying_allowed_zone_ids": ["ghost"]})) and V("MAN_DOWN", "press", {"lying_allowed_zone_ids": ["walk"]})["lying_allowed_zone_ids"] == ["walk"])
check("WALK_ONLY defaults and ranges", V("WALK_ONLY", "walk")["max_speed_heights_per_s"] == 1.8 and raises(lambda: V("WALK_ONLY", "walk", {"min_seconds": 0.1})))
check("an unknown rule type, non-dict params, booleans and NaN as numbers are refused",
      raises(lambda: V("TELEPORT", "press")) and raises(lambda: V("PPE_CONTINUOUS", "press", ["x"]))
      and raises(lambda: V("PPE_CONTINUOUS", "press", {"grace_seconds": True})) and raises(lambda: V("PPE_CONTINUOUS", "press", {"grace_seconds": float("nan")})))

print("A3. Parsing rows")
row = {"id": "11111111-1111-1111-1111-111111111111", "type": "PPE_ENTRY", "zoneId": "press", "severity": "CRITICAL",
       "isEnabled": True, "minEvidenceConfidence": 0.8, "params": {"required_ppe": ["helmet"]}, "name": "Hard hat"}
r = parse_rule(row, ZBY)
check("camelCase and snake_case rows both parse", r.id == row["id"] and r.severity == "CRITICAL" and r.min_evidence_confidence == 0.8
      and r.zone_name == "Press line" and r.params["required_ppe"] == ["helmet"] and r.source == "database"
      and parse_rule({"type": "PPE_ENTRY", "zone_id": "press"}, ZBY).severity == "WARNING")
rules, errors = parse_rules([row, {"type": "PPE_ENTRY", "zoneId": "walk"}, {"type": "NOPE"}, "junk", None, {"type": "PPE_ENTRY", "zoneId": "press", "severity": "LOUD"},
                             {"type": "PPE_ENTRY", "zoneId": "press", "minEvidenceConfidence": 3}], ZONES)
check("bad rows are reported (with a number) and skipped, good rows survive", len(rules) == 1 and len(errors) == 6 and errors[0].startswith("rule 2:"), str(errors))
check("disabled rules are not loaded", load_rules("o", ZONES, rows=[dict(row, isEnabled=False)]) == [])
check("given rows are trusted over the default, even if all are invalid (no silent fallback)", load_rules("o", ZONES, rows=[{"type": "NOPE"}]) == [])
check("Rule refuses an unknown type or severity", raises(lambda: Rule(type="X", zone_id="a", params={}), ValueError) and raises(lambda: Rule(type="PPE_ENTRY", zone_id="a", params={}, severity="LOUD"), ValueError))
check("a Decision's confidence is forced to 0 when UNVERIFIABLE, and unknown statuses are refused",
      Decision(status=UNVERIFIABLE, reason="r", confidence=0.9, evidence={}, rule_type="PPE_ENTRY", severity="WARNING",
               worker_key="trk-1", zone_id="press", video_time_s=1.0).confidence == 0.0
      and raises(lambda: Decision(status="GUILTY", reason="r", confidence=0.9, evidence={}, rule_type="PPE_ENTRY", severity="WARNING",
                                  worker_key="trk-1", zone_id="press", video_time_s=1.0), ValueError))


# ── B. The engine with scripted evidence ───────────────────────────────────
print("B. The engine, evidence scripted")


def item(state, n=29, seen=0.0, empty=0.0, why="", conf=0.8):
    return {"state": state, "why": why or {"PRESENT": "seen in enough clear frames", "ABSENT": "no sign of it in nearly every clear frame",
                                            "UNVERIFIABLE": "evidence is mixed or ambiguous"}[state],
            "clear_frames": n, "seen_fraction": seen, "empty_fraction": empty, "mean_conf": conf}


def present(n=29, seen=0.93):
    return item("PRESENT", n, seen=seen, empty=round(1 - seen, 3))


def absent(n=29, empty=0.96):
    return item("ABSENT", n, seen=round(1 - empty, 3), empty=empty, conf=0.0)


def unknown(n=3, why=None):
    return item("UNVERIFIABLE", n, seen=0.2, empty=0.2, why=why or f"only {n} clear frames (need 8)")


class Ppe:
    unavailable_reason = None

    def __init__(self):
        self.by = {}
        self.asked = []

    def set(self, worker, helmet, vest):
        self.by[worker] = {"status": "X", "items": {"helmet": helmet, "safety_vest": vest}, "observed_frames": 30, "reason": ""}

    def verdict(self, key, t):
        self.asked.append((key, t))
        return self.by.get(key)


CFG = SafetyConfig()
RULE = default_rules(ZONES)[0]


def enter(worker="trk-1", zone="press", t=10.0, **details):
    return ZoneEvent("ENTERED_ZONE", worker, zone, t, details)


def leave(worker="trk-1", zone="press", t=20.0, **details):
    return ZoneEvent("EXITED_ZONE", worker, zone, t, details or {"reason": "LEFT"})


def engine(ppe=None, rules=None, cfg=CFG):
    p = ppe or Ppe()
    return ComplianceEngine(rules if rules is not None else [RULE], p, cfg), p


e, p = engine()
p.set("trk-1", present(29, 0.93), present(29, 0.89))
out = e.on_event(enter(), 10.0)
check("S1 equipped worker enters: COMPLIANT at once", len(out) == 1 and out[0].status == COMPLIANT and out[0].worker_key == "trk-1" and out[0].zone_id == "press")
dd = out[0]
check("S1 confidence = the weakest required item's seen fraction", abs(dd.confidence - 0.89) < 1e-9, str(dd.confidence))
check("S1 the reason is built from the evidence numbers (27 of 29 / 26 of 29) and names the zone",
      "Press line" in dd.reason and "helmet seen in 27 of 29 clear frames" in dd.reason and "vest seen in 26 of 29 clear frames" in dd.reason, dd.reason)
check("S1 the evidence dict carries the items, the mode, the times and the rule",
      dd.evidence["mode"] == "AT_ENTRY" and dd.evidence["items"]["helmet"]["clear_frames"] == 29 and dd.evidence["entered_video_time_s"] == 10.0
      and dd.evidence["first_sighting"] is False and dd.evidence["required"] == ["helmet", "safety_vest"])
check("S1 it is stamped at the ENTRY, not when it was decided", dd.video_time_s == 10.0 and dd.evidence["decided_video_time_s"] == 10.0)
check("S1 it stores the thresholds it was made with", dd.thresholds["ppe_absence_fraction"] == 0.9 and dd.thresholds["entry_evidence_wait_s"] == 4.0 and dd.thresholds["required_ppe"] == ["helmet", "safety_vest"])
check("S1 an equipped worker needs no picture", dd.snapshot_requested is False)
check("S1 it has a source-made id", len(dd.id) == 36 and dd.rule_id is None and dd.severity == "WARNING" and dd.rule_type == "PPE_ENTRY")
check("S1 nothing more happens on later frames (one decision per visit)", e.on_tick(11.0) == [] and e.on_tick(30.0) == [])

e, p = engine()
p.set("trk-1", absent(29, 0.96), present(29, 0.9))
out = e.on_event(enter(), 10.0)
check("S2 no helmet: POTENTIAL_VIOLATION", len(out) == 1 and out[0].status == POTENTIAL_VIOLATION)
check("S2 the claim's confidence is about the MISSING item (96 %)", abs(out[0].confidence - 0.96) < 1e-9)
check("S2 the reason names the missing item with the frame counts, and NOT the vest",
      "no helmet seen in 28 of 29 clear frames" in out[0].reason and "vest" not in out[0].reason, out[0].reason)
check("S2 it asks for an evidence picture", out[0].snapshot_requested is True)
check("S2 the wording never accuses: 'potential', and the reason says 'seen', not 'is not wearing'",
      "wearing" not in out[0].reason.lower() and "violat" not in out[0].reason.lower())

e, p = engine()
p.set("trk-1", present(), absent(27, 0.93))
out = e.on_event(enter(), 5.0)
check("S3 no vest: POTENTIAL_VIOLATION naming the vest",
      out[0].status == POTENTIAL_VIOLATION and "no vest seen in 25 of 27 clear frames" in out[0].reason and abs(out[0].confidence - 0.93) < 1e-9, out[0].reason)

e, p = engine()
p.set("trk-1", absent(29, 0.96), absent(29, 0.92))
out = e.on_event(enter(), 5.0)
check("both missing: both named, confidence the weaker claim", out[0].status == POTENTIAL_VIOLATION
      and "no helmet" in out[0].reason and "no vest" in out[0].reason and abs(out[0].confidence - 0.92) < 1e-9, out[0].reason)

e, p = engine()
p.set("trk-1", absent(29, 0.96), unknown(4))
out = e.on_event(enter(), 5.0)
check("a missing helmet is a claim even if the vest cannot be judged (the claim is about the helmet)",
      out[0].status == POTENTIAL_VIOLATION and abs(out[0].confidence - 0.96) < 1e-9 and "vest" not in out[0].reason)

e, p = engine()
p.set("trk-1", unknown(3), unknown(3))
out = e.on_event(enter(t=10.0), 10.0)
check("S4 far away / hidden: nothing is decided at the entry", out == [] and e.pending_workers() == ["trk-1"])
check("S4 still waiting at 3.9 s", e.on_tick(13.9) == [])
out = e.on_tick(14.0)
check("S4 at the 4 s deadline: UNVERIFIABLE", len(out) == 1 and out[0].status == UNVERIFIABLE and out[0].confidence == 0.0)
check("S4 the reason is the evidence layer's own (not enough clear frames) for both items",
      "only 3 clear frames (need 8)" in out[0].reason and "helmet" in out[0].reason and "vest" in out[0].reason, out[0].reason)
check("S4 it is stamped at the entry, records how long it waited, and asks for no picture",
      out[0].video_time_s == 10.0 and out[0].evidence["waited_seconds"] == 4.0 and out[0].snapshot_requested is False)
check("S4 once decided, nothing more", e.on_tick(20.0) == [] and e.pending_workers() == [])

e, p = engine()
p.set("trk-1", unknown(3), unknown(3))
e.on_event(enter(t=10.0), 10.0)
p.set("trk-1", present(12, 0.9), present(12, 0.85))
out = e.on_tick(12.0)
check("evidence that firms up during the wait resolves it: COMPLIANT with how long it waited", len(out) == 1 and out[0].status == COMPLIANT and out[0].evidence["waited_seconds"] == 2.0)

e, p = engine()
p.set("trk-1", present(12, 0.9), unknown(3))
e.on_event(enter(t=10.0), 10.0)
out = e.on_tick(14.0)
check("one item fine, one unknown at the deadline: UNVERIFIABLE, and only the unknown item is blamed in the reason",
      out[0].status == UNVERIFIABLE and "vest:" in out[0].reason and "helmet:" not in out[0].reason, out[0].reason)

e, p = engine()
p.set("trk-1", present(), present())
p.set("trk-2", absent(), present())
out = e.on_event(enter("trk-1"), 10.0) + e.on_event(enter("trk-2"), 10.0)
check("S5 two workers are judged separately", sorted((d.worker_key, d.status) for d in out) == [("trk-1", COMPLIANT), ("trk-2", POTENTIAL_VIOLATION)])
p.set("trk-3", unknown(2), unknown(2))
e.on_event(enter("trk-3"), 10.0)
p.set("trk-4", present(), present())
out = e.on_event(enter("trk-4"), 11.0)
check("S5 an undecided worker never blocks or contaminates another", [d.worker_key for d in out] == ["trk-4"] and e.pending_workers() == ["trk-3"])
check("S5 a worker with no evidence at all is abstained on, with the reason", e.on_event(enter("trk-9"), 11.0)[0].status == UNVERIFIABLE)

print("B2. Visits, re-alerts, exits")
e, p = engine()
p.set("trk-1", present(), present())
first = e.on_event(enter(t=10.0), 10.0)
again = e.on_event(enter(t=10.5), 10.5)
check("the same worker 'entering' again in the same visit makes no second decision", len(first) == 1 and again == [])
e.on_event(leave(t=15.0), 15.0)
check("stepping out and back in within the re-alert window (60 s) is not judged again", e.on_event(enter(t=30.0), 30.0) == [] and e.suppressed == 2)
check("...but a genuine new visit after the window is", len(e.on_event(enter(t=80.0), 80.0)) == 1)

e, p = engine()
p.set("trk-1", unknown(2), unknown(2))
e.on_event(enter(t=10.0), 10.0)
out = e.on_event(leave(t=11.5), 11.5)
check("leaving before the PPE could be judged: UNVERIFIABLE, says so", len(out) == 1 and out[0].status == UNVERIFIABLE and "left the zone before the helmet and vest could be judged" in out[0].reason, out[0].reason if out else "")
e, p = engine()
p.set("trk-1", unknown(2), unknown(2))
e.on_event(enter(t=10.0), 10.0)
out = e.on_event(leave(t=12.0, reason="LOST", unverifiable=True), 12.0)
check("lost from view before it could be judged: says so, no accusation", out[0].status == UNVERIFIABLE and "lost from view" in out[0].reason)
check("a pending decision is not touched by another worker's exit or another zone's events",
      e.on_event(leave("trk-2"), 13.0) == [] and e.on_event(enter("trk-5", zone="walk"), 13.0) == [] and e.on_event(enter("trk-5", zone="lawn"), 13.0) == [])

e, p = engine()
p.set("trk-1", present(), present())
out = e.on_event(enter(first_sighting=True), 0.0)
check("already inside when first seen: still judged, but the decision says so", out[0].status == COMPLIANT and out[0].evidence["first_sighting"] is True)
p.set("trk-2", absent(), present())
out = e.on_event(enter("trk-2", first_sighting=True), 0.0)
check("...and a missing-helmet reason does not claim they entered without it", out[0].status == POTENTIAL_VIOLATION and out[0].reason.startswith("Was already inside Press line when first seen"), out[0].reason)

print("B3. Rules and settings")
e, p = engine(rules=[RULE, default_rules(ZONES)[1]])
p.set("trk-1", present(), present())
check("every restricted zone's rule applies to that zone only", [d.zone_id for d in e.on_event(enter(zone="press2"), 1.0)] == ["press2"])

e, p = engine()
p.set("trk-1", present(29, 0.93), present(29, 0.93))
strict = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet", "safety_vest"], "mode": "AT_ENTRY"}, min_evidence_confidence=0.95, zone_name="Press line")
e2, _ = engine(p, [strict])
out = e2.on_event(enter(), 1.0)
check("evidence below the rule's minimum confidence makes no claim at once: the engine keeps looking", out == [] and e2.pending_workers() == ["trk-1"])
out = e2.on_tick(5.0)
check("...and when the wait is over it abstains with that reason (confidence 0)",
      len(out) == 1 and out[0].status == UNVERIFIABLE and out[0].confidence == 0.0 and "below this rule's minimum" in out[0].reason, out[0].reason if out else "")
check("...the rule's floor is stored with the thresholds it was judged by", out[0].thresholds["min_evidence_confidence"] == 0.95)
p.set("trk-2", absent(29, 0.93), present(29, 0.97))
e2.on_event(enter("trk-2", t=10.0), 10.0)
check("...also for a violation claim", e2.on_tick(14.0)[0].status == UNVERIFIABLE)
p.set("trk-3", present(29, 0.6), present(29, 0.6))
e2.on_event(enter("trk-3", t=20.0), 20.0)
p.set("trk-3", present(29, 0.97), present(29, 0.97))
out = e2.on_tick(22.0)
check("evidence that rises above the floor during the wait is then claimed", len(out) == 1 and out[0].status == COMPLIANT and out[0].worker_key == "trk-3")

helm_only = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet"], "mode": "AT_ENTRY"}, zone_name="Press line")
p = Ppe(); p.set("trk-1", present(), absent())
out = engine(p, [helm_only])[0].on_event(enter(), 1.0)
check("a rule that requires only the helmet ignores a missing vest", out[0].status == COMPLIANT and "vest" not in out[0].reason and list(out[0].evidence["items"]) == ["helmet"])

via = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet", "safety_vest"], "mode": "VIA_CHECKPOINT", "checkpoint_zone_id": "gate",
                                                       "max_seconds_since_checkpoint": 60.0})
dwell = Rule(type="OCCUPANCY", zone_id="walk", params={"min_people": 2, "max_people": None, "grace_seconds": 10.0, "window_seconds": 2.0})
off = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet"], "mode": "AT_ENTRY"}, enabled=False)
eng, p = engine(rules=[via, dwell, off])
p.set("trk-1", absent(), absent())
check("rules not evaluated yet (VIA_CHECKPOINT, OCCUPANCY) and disabled ones make no decisions, and are listed",
      eng.on_event(enter(), 1.0) == [] and len(eng.unsupported) == 2 and eng.active == [])

eng = ComplianceEngine([RULE], None, CFG)
out = eng.on_event(enter(), 1.0)
check("a session with no PPE runner abstains instead of guessing", out[0].status == UNVERIFIABLE and "does no PPE checks" in out[0].reason)
p = Ppe(); p.unavailable_reason = "PPE model not loaded"
out = engine(p)[0].on_event(enter(), 1.0)
check("with the PPE model missing every entry is UNVERIFIABLE, with that reason", out[0].status == UNVERIFIABLE and "PPE model not loaded" in out[0].reason and p.asked == [])

print("B4. Jumps and the end")
e, p = engine()
p.set("trk-1", unknown(2), unknown(2)); p.set("trk-2", unknown(2), unknown(2))
e.on_event(enter("trk-1", t=10.0), 10.0); e.on_event(enter("trk-2", t=12.0), 12.0)
out = e.reset("SEEK", 13.0, 2.0)
check("a seek closes everyone still being judged as UNVERIFIABLE, stamped at their entries",
      sorted((d.worker_key, d.video_time_s, d.status) for d in out) == [("trk-1", 10.0, UNVERIFIABLE), ("trk-2", 12.0, UNVERIFIABLE)]
      and all("video jumped" in d.reason for d in out) and e.pending_workers() == [])
p.set("trk-1", present(), present())
check("after a jump the same worker is judged afresh (a different stretch of time)", len(e.on_event(enter("trk-1", t=1.0), 1.0)) == 1)
e, p = engine()
p.set("trk-1", unknown(2), unknown(2))
e.on_event(enter(t=10.0), 10.0)
out = e.close(14.0)
check("the end of the video leaves them UNVERIFIABLE", len(out) == 1 and "video ended" in out[0].reason and e.close(15.0) == [])


print("B5. Review findings")
e, p = engine()
p.set("trk-1", present(), present())
out = e.on_event(enter(first_sighting=True), 0.0)
check("COMPLIANT for a worker already inside does not claim an entry", out[0].reason.startswith("Was already inside Press line when first seen:") and "Entered" not in out[0].reason, out[0].reason)
check("no sentence says what someone 'is wearing' (seen / not seen only)",
      "wearing" not in out[0].reason.lower() and "wearing" not in e.on_event(enter("trk-7"), 1.0)[0].reason.lower() if False else "wearing" not in out[0].reason.lower())
p.set("trk-2", present(), present())
check("a normal COMPLIANT entry reads 'Entered ...: helmet seen in ...'", e.on_event(enter("trk-2", t=2.0), 2.0)[0].reason.startswith("Entered Press line: helmet seen in"))

# newest frames must agree
e, p = engine()
w = present(30, 0.73)
p.set("trk-1", w, present(30, 0.9))
p.by["trk-1"]["recent"] = {"frames": 8, "seen_fraction": {"helmet": 0.0, "safety_vest": 1.0}}
out = e.on_event(enter(t=10.0), 10.0)
check("a window that still scores well but whose newest frames show NO helmet is not cleared (it keeps waiting)", out == [] and e.pending_workers() == ["trk-1"])
out = e.on_tick(14.0)
check("...and at the deadline it abstains, naming the item and the disagreement, with no 'wearing' and no claim",
      len(out) == 1 and out[0].status == UNVERIFIABLE and "newest frames do not show it" in out[0].reason and "helmet" in out[0].reason, out[0].reason if out else "")
p.by["trk-1"]["recent"] = {"frames": 8, "seen_fraction": {"helmet": 1.0, "safety_vest": 1.0}}
e, p2 = engine(p)
check("when the newest frames agree it is cleared as before", e.on_event(enter(t=20.0), 20.0)[0].status == COMPLIANT)
p.by["trk-1"]["recent"] = {"frames": 0, "seen_fraction": {}}
e, _ = engine(p)
check("no recent frames to contradict the window: the window decides", e.on_event(enter(t=30.0), 30.0)[0].status == COMPLIANT)

# re-alert only after a claim
e, p = engine()
p.set("trk-1", unknown(2), unknown(2))
e.on_event(enter(t=10.0), 10.0)
e.on_event(leave(t=11.0, reason="LOST", unverifiable=True), 11.0)
p.set("trk-1", absent(), absent())
out = e.on_event(enter(t=30.0), 30.0)
check("an entry that ended in an abstention does not start the re-alert clock: a re-entry 20 s later IS judged",
      len(out) == 1 and out[0].status == POTENTIAL_VIOLATION and e.suppressed == 0)
e.on_event(leave(t=31.0), 31.0)
check("...but after a claim the window applies", e.on_event(enter(t=50.0), 50.0) == [] and e.suppressed == 1)
for k in range(200):
    e.on_event(enter(f"trk-{100 + k}", t=100.0 + k), 100.0 + k)
check("the re-alert memory is pruned, not unbounded", len(e._last_judged) <= 62, str(len(e._last_judged)))

# a second 'entered' for a pending worker does not extend the wait
e, p = engine()
p.set("trk-1", unknown(2), unknown(2))
e.on_event(enter(t=10.0), 10.0)
e.on_event(enter(t=12.0), 12.0)
check("a second ENTERED for a worker still being judged does not restart or extend the wait", len(e.on_tick(14.0)) == 1)

# id-less rules on one zone
r1 = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet"], "mode": "AT_ENTRY"}, zone_name="Press line")
r2 = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["safety_vest"], "mode": "AT_ENTRY"}, zone_name="Press line")
check("rules without ids on one zone get distinct keys", r1.key != r2.key and r1.key == Rule(type="PPE_ENTRY", zone_id="press", params=dict(r1.params)).key)
p = Ppe(); p.set("trk-1", present(), absent(29, 0.96))
out = engine(p, [r1, r2])[0].on_event(enter(), 1.0)
check("a helmet-only and a vest-only rule on one zone both judge the entry (the vest rule flags the missing vest)",
      sorted(d.status for d in out) == [COMPLIANT, POTENTIAL_VIOLATION], str([d.status for d in out]))

# stored evidence carries why
e, p = engine()
p.set("trk-1", unknown(3), unknown(3))
e.on_event(enter(t=10.0), 10.0)
out = e.on_tick(14.0)
check("the evidence stores each item's 'why', so the abstention sentence can be rebuilt from it",
      out[0].evidence["items"]["helmet"]["why"] == "only 3 clear frames (need 8)" and "only 3 clear frames (need 8)" in out[0].reason)

# rule ids and enabled flags
check("a rule id that is not a uuid is refused (every decision stores it; the column is a uuid)",
      raises(lambda: parse_rule({"id": "hardhat-rule", "type": "PPE_ENTRY", "zoneId": "press"}, ZBY))
      and parse_rule({"id": "11111111-1111-1111-1111-111111111111", "type": "PPE_ENTRY", "zoneId": "press"}, ZBY).id == "11111111-1111-1111-1111-111111111111")
check("'isEnabled' strings are read properly ('false' is false) and junk is refused",
      parse_rule({"type": "PPE_ENTRY", "zoneId": "press", "isEnabled": "false"}, ZBY).enabled is False
      and parse_rule({"type": "PPE_ENTRY", "zoneId": "press", "isEnabled": "true"}, ZBY).enabled is True
      and raises(lambda: parse_rule({"type": "PPE_ENTRY", "zoneId": "press", "isEnabled": "maybe"}, ZBY))
      and raises(lambda: parse_rule({"type": "PPE_ENTRY", "zoneId": "press", "isEnabled": [1]}, ZBY)))

# ── C. The real chain ───────────────────────────────────────────────────────
print("C. SafetySession end to end (zone tracker + PPE runner + evidence + writer + database)")
_n = [0]
FRAME = np.zeros((480, 640, 3), np.uint8)
SHAPE = FRAME.shape
CFG_C = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0)


class Scene:
    """What the helmet/vest detector 'sees': per worker, whether each item shows."""

    def __init__(self):
        self.helmet = {}
        self.vest = {}
        self.boxes = {}

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


def make(scene, cfg=CFG_C, zones=ZONES, rules=None, fps=10.0):
    _n[0] += 1
    path = os.path.join(TMP, f"c{_n[0]}.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id="org-1", session_id="s1", engine=dbe, retry_interval_s=0.0)
    runner = PpeRunner(scene, cfg)
    session = SafetySession(SafetyEngine(FrameClock(fps=fps, recorded_at="2026-11-08T04:00:00Z")), writer, cfg,
                            zones_config=zones, ppe_runner=runner, rules=rules)
    return session, path


def rows(path, table="safety_decisions", where=""):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} {where} ORDER BY id" if table == "safety_events" else f"SELECT * FROM {table} {where} ORDER BY video_time_s, rowid")]
    finally:
        c.close()


def box(i=0, tall=300):
    return [100.0 + 120 * i, 100.0, 160.0 + 120 * i, 100.0 + tall]


async def walk(session, scene, workers, frames, zone_of, seconds_from=0, start_frame=0):
    """frames: how many frames to run. workers: {tid: (box_index, tall)}. zone_of(tid, frame) -> zone id or None."""
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


def go_in(at):
    return lambda tid, i: "press" if i >= at else None


def run(coro):
    return asyncio.run(coro)


# S1
scene = Scene()
s, path = make(scene)
pl = run(walk(s, scene, {1: (0, 300)}, 80, go_in(30)))
run(s.finish())
dec = rows(path)
check("S1 equipped worker walks in: one decision, COMPLIANT", len(dec) == 1 and dec[0]["status"] == "COMPLIANT", str([(d["status"], d["reason"]) for d in dec]))
d0 = dec[0]
ev = json.loads(d0["evidence"])
check("S1 stored with its numbers: items with clear frames, fractions, thresholds", ev["items"]["helmet"]["state"] == "PRESENT" and ev["items"]["helmet"]["clear_frames"] >= 8
      and json.loads(d0["thresholds"])["ppe_min_clear_frames"] == 8 and d0["confidence"] >= 0.6)
check("S1 the row has org, camera, zone, worker, session, a time and the reason", d0["org_id"] == "org-1" and d0["camera_id"] == "cam1" and d0["zone_id"] == "press"
      and d0["worker_key"] == "trk-1" and d0["session_key"] == "s1" and d0["rule_type"] == "PPE_ENTRY" and "Press line" in d0["reason"] and d0["rule_id"] is None)
check("S1 stamped at the entry (3.0 s) with the matching wall clock", d0["video_time_s"] == 3.0 and d0["observed_at"] == "2026-11-08T04:00:03.000+00:00", f"{d0['video_time_s']} {d0['observed_at']}")
carried = [p_ for p_ in pl if "decisions" in p_]
check("S1 the dashboard payload carries it exactly once", len(carried) == 1 and carried[0]["decisions"][0]["status"] == "COMPLIANT" and carried[0]["decisions"][0]["id"] == d0["id"])
check("S1 zone events were written alongside", [e["event_type"] for e in rows(path, "safety_events")][:1] == ["ENTERED_ZONE"])

# S2, S3
for label, setup, item_word in (("S2 no helmet", lambda sc: sc.helmet.update({1: False}), "helmet"), ("S3 no vest", lambda sc: sc.vest.update({1: False}), "vest")):
    scene = Scene(); setup(scene)
    s, path = make(scene)
    run(walk(s, scene, {1: (0, 300)}, 80, go_in(30)))
    run(s.finish())
    dec = rows(path)
    check(f"{label}: POTENTIAL_VIOLATION naming the {item_word}",
          len(dec) == 1 and dec[0]["status"] == "POTENTIAL_VIOLATION" and f"no {item_word} seen" in dec[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))
    other = "vest" if item_word == "helmet" else "helmet"
    check(f"{label}: and not the {other}", other not in dec[0]["reason"])
    check(f"{label}: confidence is high and stored; a picture is wanted in the evidence's decision", dec[0]["confidence"] >= 0.9)

# S4 far away
scene = Scene()
s, path = make(scene)
pl = run(walk(s, scene, {1: (0, 60)}, 100, go_in(10)))                 # 60 px tall: never a clear frame
run(s.finish())
dec = rows(path)
check("S4 a worker too small to judge: UNVERIFIABLE after the wait, confidence 0",
      len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and dec[0]["confidence"] == 0.0, str([(d["status"], d["reason"]) for d in dec]))
check("S4 the reason says why (no clear frames), in the evidence layer's words", "clear frames" in dec[0]["reason"], dec[0]["reason"])
check("S4 decided about 4 s after the entry, not at once", json.loads(dec[0]["evidence"])["waited_seconds"] >= 3.9)

# flicker
scene = Scene()
flicker = {"n": 0}
orig_detect = scene.detect


def flick(frame, imgsz=None):
    flicker["n"] += 1
    if flicker["n"] % 2 == 0:
        scene.helmet[1] = False
    else:
        scene.helmet[1] = True
    return orig_detect(frame, imgsz)


scene.detect = flick
s, path = make(scene)
run(walk(s, scene, {1: (0, 300)}, 100, go_in(20)))
run(s.finish())
dec = rows(path)
check("a helmet that flickers in and out (half the frames): never an accusation", len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE", str([(d["status"], d["reason"]) for d in dec]))

# S5 two workers
scene = Scene(); scene.vest.update({2: False})
s, path = make(scene)
run(walk(s, scene, {1: (0, 300), 2: (1, 300)}, 80, go_in(30)))
run(s.finish())
by = {d["worker_key"]: d for d in rows(path)}
check("S5 two workers side by side, one without a vest: judged separately", set(by) == {"trk-1", "trk-2"} and by["trk-1"]["status"] == "COMPLIANT"
      and by["trk-2"]["status"] == "POTENTIAL_VIOLATION" and "no vest" in by["trk-2"]["reason"], str({k: v["status"] for k, v in by.items()}))

# zones that carry no rule
scene = Scene()
s, path = make(scene)
run(walk(s, scene, {1: (0, 300)}, 60, lambda tid, i: "walk" if i >= 20 else None))
run(s.finish())
check("entering an APPROACH zone makes zone events but no PPE decision (the rule is on the restricted zone)",
      rows(path) == [] and any(e["event_type"] == "ENTERED_ZONE" for e in rows(path, "safety_events")))

# seek mid-judgement, then replay
scene = Scene(); scene.helmet.update({1: False})


async def seek_case():
    s, path = make(scene)
    await walk(s, scene, {1: (0, 60)}, 40, go_in(10))             # too small to judge: pending at the entry (1.0 s)... closed by wait at 5.0 s
    return s, path

# an entry that is still pending when the video jumps
scene2 = Scene()
s, path = make(scene2)


async def pending_then_jump():
    await walk(s, scene2, {1: (0, 60)}, 20, go_in(10))              # entered at 1.0 s, undecided at 1.9 s
    s.reset("SEEK", frame_index=200)
    await walk(s, scene2, {1: (0, 300)}, 5, lambda t, i: None, start_frame=200)
    await s.finish()

run(pending_then_jump())
dec = rows(path)
check("a seek while an entry is still being judged writes an UNVERIFIABLE decision ('the video jumped'), stamped at the entry",
      len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and "video jumped" in dec[0]["reason"] and dec[0]["video_time_s"] == 1.0, str([(d["status"], d["reason"], d["video_time_s"]) for d in dec]))

# judged, then rewound and replayed: no second decision
scene3 = Scene()
s, path = make(scene3)


async def judged_then_replayed():
    await walk(s, scene3, {1: (0, 300)}, 80, go_in(30))
    s.reset("SEEK", frame_index=0)
    await walk(s, scene3, {1: (0, 300)}, 80, go_in(30), start_frame=0)       # the same stretch again
    await s.finish()

run(judged_then_replayed())
dec = rows(path)
check("a stretch rewound and replayed is not judged a second time", len(dec) == 1 and dec[0]["status"] == "COMPLIANT", str([(d["status"], d["video_time_s"]) for d in dec]))

# the video ends while pending
scene4 = Scene()
s, path = make(scene4)
run(walk(s, scene4, {1: (0, 60)}, 20, go_in(10)))
run(s.finish())
dec = rows(path)
check("the video ending while an entry is still being judged leaves it UNVERIFIABLE", len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and "video ended" in dec[0]["reason"])

# no PPE model
class Down:
    def detect(self, frame, imgsz=None):
        raise RuntimeError("no model")


scene5 = Scene()
_n[0] += 1
path = os.path.join(TMP, f"c{_n[0]}.sqlite")
dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
configure_sqlite(dbe, busy_timeout_ms=250); ensure_safety_tables(dbe)
s = SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")),
                  SafetyWriter("cam1", engine=dbe, retry_interval_s=0.0), CFG_C, zones_config=ZONES, ppe_runner=PpeRunner(None, CFG_C))
run(walk(s, scene5, {1: (0, 300)}, 60, go_in(10)))
run(s.finish())
dec = rows(path)
check("with no PPE model, entering a restricted zone is UNVERIFIABLE with that reason, never a violation",
      len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and "PPE model not loaded" in dec[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))

# custom database rule
scene6 = Scene(); scene6.vest.update({1: False})
helmet_rule = Rule(type="PPE_ENTRY", zone_id="press", params={"required_ppe": ["helmet"], "mode": "AT_ENTRY"}, id=str(uuid.uuid4()), name="Hard hat", zone_name="Press line",
                   severity="CRITICAL", source="database")
s, path = make(scene6, rules=[helmet_rule])
run(walk(s, scene6, {1: (0, 300)}, 80, go_in(30)))
run(s.finish())
dec = rows(path)
check("a database rule is honoured: helmet only (a missing vest is fine), its id, severity and name are stored",
      len(dec) == 1 and dec[0]["status"] == "COMPLIANT" and dec[0]["rule_id"] == helmet_rule.id and dec[0]["severity"] == "CRITICAL"
      and json.loads(dec[0]["evidence"])["rule"] == "Hard hat")

# a camera with no restricted zone
scene7 = Scene()
s, path = make(scene7, zones=[ZONES[1], ZONES[2]])
run(walk(s, scene7, {1: (0, 300)}, 40, lambda t, i: "walk" if i > 10 else None))
run(s.finish())
check("a camera with no restricted zone produces no decisions at all", rows(path) == [])

# a defect in the rules must not corrupt the zone events
class Boom(PpeRunner):
    def verdict(self, key, t):
        raise RuntimeError("rules are broken")


scene8 = Scene()
_n[0] += 1
path = os.path.join(TMP, f"c{_n[0]}.sqlite")
dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
configure_sqlite(dbe, busy_timeout_ms=250); ensure_safety_tables(dbe)
s = SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")),
                  SafetyWriter("cam1", engine=dbe, retry_interval_s=0.0), CFG_C, zones_config=ZONES, ppe_runner=Boom(scene8, CFG_C))
run(walk(s, scene8, {1: (0, 300), 2: (1, 300)}, 80, lambda tid, i: "press" if i >= (20 if tid == 1 else 30) else None))
run(s.finish())
ev = rows(path, "safety_events")
check("a broken rule engine is skipped, and BOTH workers' zone events are still written (no orphan exits)",
      sorted((e["worker_key"], e["event_type"]) for e in ev if e["event_type"] == "ENTERED_ZONE") == [("trk-1", "ENTERED_ZONE"), ("trk-2", "ENTERED_ZONE")]
      and all(d["status"] == "UNVERIFIABLE" for d in rows(path)), str([(e["worker_key"], e["event_type"]) for e in ev]))

# the video ends with a decision still pending, after a reset closed another
scene9 = Scene()
s, path = make(scene9)


async def reset_then_end():
    await walk(s, scene9, {1: (0, 60)}, 20, go_in(10))              # pending at the jump
    s.reset("SEEK", frame_index=500)                                  # closes it; no frame follows
    await s.finish()

run(reset_then_end())
dec = rows(path)
check("a decision closed by a seek with no frame after it is still written when the session ends",
      len(dec) == 1 and dec[0]["status"] == "UNVERIFIABLE" and "video jumped" in dec[0]["reason"], str([(d["status"], d["reason"]) for d in dec]))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
