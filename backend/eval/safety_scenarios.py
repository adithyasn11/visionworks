"""
The safety scenario suite (design §14.B, Step 17): scenarios S1-S10, plus two man-down scenarios (M1, M2), driven through
the REAL engine many times each, with the results written to backend/eval/results/safety_evaluation.json.

WHAT IS REAL AND WHAT IS SCRIPTED

  Real: SafetySession end to end. The zone tracker (debounce, loss, back-dated entries), the rule engine (PPE at entry,
  longest stay, required exit), the PPE runner and evidence layer (clear-frame gate, 60 % / 90 % rule, the window), the
  man-down detector, the head count, and the writer into a real SQLite file. Every number below is read back from that
  database, as the dashboard would read it.

  Scripted: what the cameras' models would say. Each frame, the script says where each worker is (their box), which
  zone their feet are in, whether the helmet and vest detector finds the items on them, and (for M1/M2) their pose
  keypoints. So these results measure the LOGIC on top of the models, not the models' eyes; the PPE model is measured on
  real labelled images in ppe_training/eval_segments.py, and the whole chain on real video in the evaluation document.

EACH SCENARIO RUNS IN VARIANTS: frame rate 10, 15 and 25, the moment things happen shifted by up to 2 s, and the worker
in different places in the picture (seeded, so every run of this file gives the same results).

OUTCOME of a run, per scenario (the expectation is written next to the scenario):

  correct     the decision the scenario expects (or, where nothing should be raised, nothing)
  abstained   the system said it could not tell where the scenario expected a claim
  wrong       anything else; "wrong and confident" is a claim (compliant or a flag) that contradicts the truth

TIMING: entry and exit events against the scripted moment (the first frame inside, the first frame outside), and the
stay the tracker measured against the scripted stay.

Run:    venv/Scripts/python.exe backend/eval/safety_scenarios.py
Writes: backend/eval/results/safety_evaluation.json (and prints a summary)
"""

import asyncio
import json
import math
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
os.environ.setdefault("SAFETY_EVIDENCE_DIR", tempfile.mkdtemp(prefix="eval_evidence_"))

import numpy as np                                                  # noqa: E402
from sqlalchemy import create_engine                                # noqa: E402

from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.ppe_runner import PpeRunner                        # noqa: E402
from app.safety.rules import parse_rule                            # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
FRAME = np.zeros((480, 640, 3), np.uint8)
SHAPE = FRAME.shape
# The scripted detector draws boxes on a blank frame, so the close-up crops and the hi-vis colour cue (which look at the
# pixels) are switched off; everything else is the production configuration.
CFG = SafetyConfig(ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0)
VARIANTS = 18                                                      # runs per scenario (6 per frame rate)

ZONES = [
    {"zone_id": "gate", "zone_name": "PPE checkpoint", "zone_type": "PPE_CHECKPOINT"},
    {"zone_id": "walk", "zone_name": "Approach", "zone_type": "APPROACH"},
    {"zone_id": "press", "zone_name": "Press line", "zone_type": "RESTRICTED"},
    {"zone_id": "press2", "zone_name": "Second press", "zone_type": "RESTRICTED"},
    {"zone_id": "exit", "zone_name": "Exit", "zone_type": "EXIT"},
]
ZBY = {z["zone_id"]: z for z in ZONES}


def rule(rule_type, zone="press", severity="WARNING", **params):
    return parse_rule({"type": rule_type, "zoneId": zone, "params": params, "severity": severity, "name": rule_type}, ZBY)


# ── the scripted "models" ─────────────────────────────────────────────────────

# The PPE model's MEASURED per-frame reading on clear frames (ppe_training/reports/CONSTRUCTION_PPE_EVAL.md, "clear frames
# only"): (share read present, share read ambiguous); the rest read as nothing there.
MEASURED = {"helmet": {True: (0.802, 0.079), False: (0.014, 0.028)},
            "safety_vest": {True: (0.761, 0.087), False: (0.013, 0.008)}}


class Scene:
    """
    Stands in for the PPE detector: finds the items the script says each worker is wearing, where they would be. A worker
    marked `noisy` is read with the model's measured per-frame error rates instead: a worn helmet is sometimes missed or
    half-seen, a missing one is now and then "seen". (Frame by frame independently; real errors also run together across
    frames, which the segment evaluation on real images covers.)
    """

    def __init__(self):
        self.workers = {}

    @staticmethod
    def _conf(w, item, worn):
        if not w.get("noisy"):
            return (0.88 if item == "helmet" else 0.86) if worn else None
        p_present, p_amb = MEASURED[item][bool(worn)]
        r = w["rng"].random()
        return 0.8 if r < p_present else (0.32 if r < p_present + p_amb else None)

    def detect(self, frame, imgsz=None):
        dets = []
        for w in self.workers.values():
            if not w.get("visible", True):
                continue
            x1, y1, x2, y2 = w["bbox"]
            h = y2 - y1
            c = self._conf(w, "helmet", w.get("helmet", True))
            if c is not None:
                dets.append({"cls": "helmet", "conf": c, "bbox": [x1 + 0.2 * (x2 - x1), y1 - 0.03 * h, x2 - 0.2 * (x2 - x1), y1 + 0.17 * h]})
            c = self._conf(w, "safety_vest", w.get("vest", True))
            if c is not None:
                dets.append({"cls": "safety_vest", "conf": c, "bbox": [x1 + 0.05 * (x2 - x1), y1 + 0.2 * h, x2 - 0.05 * (x2 - x1), y1 + 0.55 * h]})
        return dets


def standing_kp(bbox):
    x1, y1, x2, y2 = bbox
    cx, h = (x1 + x2) / 2, y2 - y1
    a = np.zeros((17, 3))
    for i, (x, y) in {5: (cx - 18, y1 + 0.2 * h), 6: (cx + 18, y1 + 0.2 * h), 11: (cx - 14, y1 + 0.5 * h), 12: (cx + 14, y1 + 0.5 * h)}.items():
        a[i] = (x, y, 0.9)
    return a


def lying(cx, cy):
    a = np.zeros((17, 3))
    for i, (x, y) in {5: (cx - 60, cy - 20), 6: (cx - 60, cy + 20), 11: (cx, cy - 15), 12: (cx, cy + 15)}.items():
        a[i] = (x, y, 0.9)
    return a, [cx - 90, cy - 40, cx + 130, cy + 40]


def bent(cx, top):
    import math as _m
    dx, dy = 100 * _m.sin(_m.radians(78)), 100 * _m.cos(_m.radians(78))
    a = np.zeros((17, 3))
    for i, (x, y) in {5: (cx - 20, top + 40), 6: (cx + 20, top + 40), 11: (cx - 20 + dx, top + 40 + dy), 12: (cx + 20 + dx, top + 40 + dy)}.items():
        a[i] = (x, y, 0.9)
    return a, [cx - 40, top, cx + 40 + dx, top + 230]


# ── one run ──────────────────────────────────────────────────────────────────

async def _play(session, scene, script, frames, fps):
    for i in range(frames):
        t = i / fps
        workers = script(i, t)
        scene.workers = {w["id"]: w for w in workers}
        ft = session.begin_frame(i)
        visible = [w for w in workers if w.get("visible", True)]
        dets = [{"track_id": w["id"], "bbox": list(w["bbox"]), "confidence": 0.9, "posture": w.get("posture", "STANDING"),
                 "keypoints": w.get("kp") if w.get("kp") is not None else standing_kp(w["bbox"])} for w in visible]
        await session.detect_ppe(ft, [{"track_id": d["track_id"], "bbox": d["bbox"]} for d in dets], FRAME, SHAPE)
        ents = [{"track_id": w["id"], "zone_id": w.get("zone"), "bbox": list(w["bbox"])} for w in visible]
        await session.after_frame(ft, ents, detections=dets, frame_shape=SHAPE)
    await session.finish()


def run(script, rules, seconds, fps, workdir):
    path = os.path.join(workdir, f"run{len(os.listdir(workdir))}.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id="11111111-1111-4111-8111-111111111111", session_id="eval", engine=dbe, retry_interval_s=0.0)
    scene = Scene()
    session = SafetySession(SafetyEngine(FrameClock(fps=fps, recorded_at="2026-11-08T04:00:00Z")), writer, CFG,
                            zones_config=ZONES, ppe_runner=PpeRunner(scene, CFG), rules=rules)
    asyncio.run(_play(session, scene, script, int(round(seconds * fps)), fps))
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        decisions = [dict(r) for r in c.execute("SELECT * FROM safety_decisions ORDER BY video_time_s, rowid")]
        events = [dict(r) for r in c.execute("SELECT * FROM safety_events ORDER BY id")]
    finally:
        c.close()
    dbe.dispose()
    for d in decisions:
        d["evidence"] = json.loads(d["evidence"] or "{}")
    for e in events:
        e["details"] = json.loads(e["details"] or "{}")
    return decisions, events


# ── the scenarios ────────────────────────────────────────────────────────────

def box(x, tall=300, y=110):
    return [x, y, x + 0.22 * tall, y + tall]


def walk_through(t_in, t_out, helmet=True, vest=True, tall=300, x=120, y=110, out_zone=None, gate=True, noisy_rng=None):
    """A worker: checkpoint -> approach -> restricted zone at t_in -> out (to `out_zone`) at t_out."""
    def script(i, t):
        if t < t_in - 4:
            zone = "gate" if gate else None
        elif t < t_in:
            zone = "walk"
        elif t < t_out:
            zone = "press"
        else:
            zone = out_zone
        w = {"id": 1, "bbox": box(x, tall, y), "zone": zone, "helmet": helmet, "vest": vest}
        if noisy_rng is not None:
            w.update(noisy=True, rng=noisy_rng)
        return [w]
    return script


def entry_decision(decisions, worker="trk-1", rule_type="PPE_ENTRY"):
    ds = [d for d in decisions if d["rule_type"] == rule_type and d["worker_key"] == worker]
    return ds[0]["status"] if ds else None


def timing(events, t_in, t_out=None, zone="press", worker="trk-1"):
    ent = [e for e in events if e["event_type"] == "ENTERED_ZONE" and e["zone_id"] == zone and e["worker_key"] == worker]
    ext = [e for e in events if e["event_type"] == "EXITED_ZONE" and e["zone_id"] == zone and e["worker_key"] == worker]
    out = {}
    if ent:
        out["entry_error_s"] = round(ent[0]["video_time_s"] - t_in, 3)
    if t_out is not None and ext:
        out["exit_error_s"] = round(ext[0]["video_time_s"] - t_out, 3)
        if isinstance(ext[0]["details"].get("dwell_s"), (int, float)):
            out["dwell_error_s"] = round(ext[0]["details"]["dwell_s"] - (t_out - t_in), 3)
    return out


def classify(expected, got, claims=("COMPLIANT", "POTENTIAL_VIOLATION")):
    if got == expected:
        return "correct"
    if got == "UNVERIFIABLE" and expected in claims:
        return "abstained"
    return "wrong"


def scenarios():
    """(id, title, expectation, rule under test, variant -> (script, rules, seconds, truth, judge))."""
    out = []

    def s1(rng, fps):
        t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
        def judge(dec, ev):
            got = entry_decision(dec)
            return classify("COMPLIANT", got), got, timing(ev, t_in, t_out)
        return walk_through(t_in, t_out, x=rng.uniform(60, 420)), [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S1", "Equipped worker enters the restricted zone", "COMPLIANT", "PPE_ENTRY", s1))

    def s2(rng, fps):
        t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
        def judge(dec, ev):
            got = entry_decision(dec)
            return classify("POTENTIAL_VIOLATION", got), got, timing(ev, t_in, t_out)
        return walk_through(t_in, t_out, helmet=False, x=rng.uniform(60, 420)), [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S2", "Worker without a helmet enters", "POTENTIAL_VIOLATION", "PPE_ENTRY", s2))

    def s3(rng, fps):
        t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
        def judge(dec, ev):
            got = entry_decision(dec)
            return classify("POTENTIAL_VIOLATION", got), got, timing(ev, t_in, t_out)
        return walk_through(t_in, t_out, vest=False, x=rng.uniform(60, 420)), [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S3", "Worker without a vest enters", "POTENTIAL_VIOLATION", "PPE_ENTRY", s3))

    for sid, title, helmet, vest, expected in (("S1n", "S1 with the model's measured per-frame errors", True, True, "COMPLIANT"),
                                              ("S2n", "S2 with the model's measured per-frame errors", False, True, "POTENTIAL_VIOLATION"),
                                              ("S3n", "S3 with the model's measured per-frame errors", True, False, "POTENTIAL_VIOLATION")):
        def noisy(rng, fps, helmet=helmet, vest=vest, expected=expected):
            t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
            def judge(dec, ev):
                got = entry_decision(dec)
                return classify(expected, got), got, timing(ev, t_in, t_out)
            return (walk_through(t_in, t_out, helmet=helmet, vest=vest, x=rng.uniform(60, 420), noisy_rng=random.Random(rng.random())),
                    [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge)
        out.append((sid, title, expected + " (or UNVERIFIABLE; never the opposite claim)", "PPE_ENTRY", noisy))

    def s4(rng, fps):
        t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
        far = rng.random() < 0.5
        # far away: 60 px tall; partly hidden: the head cut off by the top of the picture. Either way, no helmet is visible.
        script = walk_through(t_in, t_out, helmet=False, tall=60 if far else 300, y=110 if far else 0, x=rng.uniform(60, 420))
        def judge(dec, ev):
            got = entry_decision(dec)
            return classify("UNVERIFIABLE", got), got, {"variant": "far away" if far else "head cut off"}
        return script, [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S4", "Worker far away or partly hidden enters (no helmet visible)", "UNVERIFIABLE", "PPE_ENTRY", s4))

    def s5(rng, fps):
        t_in, t_out = rng.uniform(6, 8), rng.uniform(16, 19)
        overlap = rng.random() < 0.5
        gap = 20 if overlap else 110                                 # 20 px apart: the boxes overlap (a crowd); 110 px: side by side
        x0 = rng.uniform(60, 300)
        def script(i, t):
            zone = "gate" if t < t_in - 4 else ("walk" if t < t_in else ("press" if t < t_out else None))
            return [{"id": 1, "bbox": box(x0), "zone": zone, "helmet": True, "vest": True},
                    {"id": 2, "bbox": box(x0 + gap), "zone": zone, "helmet": False, "vest": True}]
        def judge(dec, ev):
            a, b = entry_decision(dec, "trk-1"), entry_decision(dec, "trk-2")
            if overlap:
                # tangled together: nobody can be judged from those frames; the equipped one must never be flagged
                ok = a != "POTENTIAL_VIOLATION" and b in ("UNVERIFIABLE", "POTENTIAL_VIOLATION")
                verdict = "correct" if a == "UNVERIFIABLE" and b == "UNVERIFIABLE" else ("abstained" if ok else "wrong")
            else:
                verdict = "correct" if (a, b) == ("COMPLIANT", "POTENTIAL_VIOLATION") else (
                    "abstained" if a in ("COMPLIANT", "UNVERIFIABLE") and b in ("POTENTIAL_VIOLATION", "UNVERIFIABLE") else "wrong")
            return verdict, f"{a} / {b}", {"variant": "overlapping" if overlap else "side by side"}
        return script, [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S5", "Two workers close together, only one equipped", "each judged on their own; overlapping -> UNVERIFIABLE", "PPE_ENTRY", s5))

    def s6(rng, fps):
        t_in = rng.uniform(6, 8)
        period = rng.choice([2, 3, 4])                               # frames: the foot point crosses the line this often
        def script(i, t):
            if t < t_in:
                zone = None
            elif t < t_in + 8:
                zone = "press" if (i // period) % 2 == 0 else None    # standing on the boundary
            else:
                zone = "press"
            return [{"id": 1, "bbox": box(200), "zone": zone}]
        def judge(dec, ev):
            entries = [e for e in ev if e["event_type"] == "ENTERED_ZONE" and e["zone_id"] == "press"]
            exits = [e for e in ev if e["event_type"] == "EXITED_ZONE" and e["zone_id"] == "press" and e["details"].get("reason") == "LEFT"]
            ok = len(entries) == 1 and not exits
            return ("correct" if ok else "wrong"), f"{len(entries)} entries, {len(exits)} exits", {}
        return script, [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_in + 14, judge
    out.append(("S6", "Worker stands on a zone boundary (the foot point flickers across it)", "one entry, not many", "zone events", s6))

    def s7(rng, fps):
        t_in = rng.uniform(5, 7)
        over = rng.random() < 0.6
        stay = rng.uniform(26, 32) if over else rng.uniform(10, 15)   # limit 20 s; warning at 16 s
        t_out = t_in + stay
        def judge(dec, ev):
            ds = [d for d in dec if d["rule_type"] == "MAX_DWELL"]
            got = ds[0]["status"] if ds else None
            warned = any(e["event_type"] == "DWELL_WARNING" for e in ev)
            if over:
                verdict = classify("POTENTIAL_VIOLATION", got) if warned else "wrong"
            else:
                verdict = classify("COMPLIANT", got)
            extra = timing(ev, t_in, t_out)
            if ds and isinstance(ds[0]["evidence"].get("dwell_seconds"), (int, float)) and not over:
                extra["measured_stay_error_s"] = round(ds[0]["evidence"]["dwell_seconds"] - stay, 3)
            return verdict, f"{got}{' after a warning' if warned else ''}", {**extra, "variant": "over the limit" if over else "within the limit"}
        return walk_through(t_in, t_out, x=rng.uniform(60, 420)), [rule("MAX_DWELL", max_seconds=20)], t_out + 4, judge
    out.append(("S7", "Worker stays beyond the time limit (and, as a control, within it)", "warning, then POTENTIAL_VIOLATION; within: COMPLIANT", "MAX_DWELL", s7))

    def s8(rng, fps):
        t_in, t_out = rng.uniform(5, 7), rng.uniform(14, 17)
        kind = rng.choice(["wrong route", "lost", "right route"])
        def script(i, t):
            if t < t_in:
                zone, vis = "walk", True
            elif t < t_out:
                zone, vis = "press", True
            elif t < t_out + 3:
                zone, vis = None, True                               # walking away from the zone
            else:
                zone = {"wrong route": "press2", "lost": None, "right route": "exit"}[kind]
                vis = kind != "lost"
            return [{"id": 1, "bbox": box(200), "zone": zone, "visible": vis}]
        expected = {"wrong route": "POTENTIAL_VIOLATION", "lost": "UNVERIFIABLE", "right route": "COMPLIANT"}[kind]
        def judge(dec, ev):
            ds = [d for d in dec if d["rule_type"] == "REQUIRED_EXIT"]
            got = ds[0]["status"] if ds else None
            return classify(expected, got), got, {"variant": kind}
        return script, [rule("REQUIRED_EXIT", exit_zone_id="exit", max_seconds_to_exit=15)], t_out + 25, judge
    out.append(("S8", "Worker leaves by the wrong route (and, as controls, is lost from view, or uses the exit)",
                "wrong route: POTENTIAL_VIOLATION; lost: UNVERIFIABLE; exit: COMPLIANT", "REQUIRED_EXIT", s8))

    def s9(rng, fps):
        t_in = rng.uniform(4, 6)
        alone = rng.random() < 0.6
        def script(i, t):
            ws = [{"id": 1, "bbox": box(80), "zone": "press" if t >= t_in else None}]
            if not alone:
                ws.append({"id": 2, "bbox": box(380), "zone": "press" if t >= t_in else None})
            return ws
        def judge(dec, ev):
            ds = [d for d in dec if d["rule_type"] == "OCCUPANCY"]
            got = ds[0]["evidence"].get("outcome") if ds else None
            if alone:
                verdict = "correct" if ds and ds[0]["status"] == "POTENTIAL_VIOLATION" and got == "LONE_WORKER" else (
                    "abstained" if ds and ds[0]["status"] == "UNVERIFIABLE" else "wrong")
            else:
                verdict = "correct" if not ds else "wrong"
            return verdict, got or "nothing", {"variant": "alone" if alone else "two people"}
        return script, [rule("OCCUPANCY", min_people=2, grace_seconds=10)], t_in + 16, judge
    out.append(("S9", "Worker alone in a two-person zone (and, as a control, two workers)", "LONE_WORKER after the grace; two: nothing", "OCCUPANCY", s9))

    def s10(rng, fps):
        t_in = rng.uniform(4, 6)
        gap = rng.choice([2.0, 3.5, 6.0])                            # seconds out of view; the tracker calls them lost after 5 s
        t_hide = t_in + 6
        t_out = t_hide + gap + 6
        def script(i, t):
            vis = not (t_hide <= t < t_hide + gap)
            zone = "press" if t_in <= t < t_out else None
            return [{"id": 1, "bbox": box(200), "zone": zone, "visible": vis}]
        def judge(dec, ev):
            entries = [e for e in ev if e["event_type"] == "ENTERED_ZONE" and e["zone_id"] == "press"]
            exits = [e for e in ev if e["event_type"] == "EXITED_ZONE" and e["zone_id"] == "press"]
            lost = [e for e in ev if e["event_type"] == "LOST_IN_ZONE"]
            ok = len(entries) == 1 and len(exits) == 1 and exits[0]["details"].get("reason") == "LEFT"
            extra = timing(ev, t_in, t_out)
            return ("correct" if ok else "wrong"), f"{len(entries)} entry, {len(exits)} exit{', marked lost then back' if lost else ''}", {**extra, "variant": f"{gap:g} s out of view"}
        return script, [rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"])], t_out + 3, judge
    out.append(("S10", "Worker hidden for a few seconds inside the zone, then seen again", "the same visit continues", "zone events", s10))

    def m1(rng, fps):
        t_fall = rng.uniform(4, 7)
        cx, cy = rng.uniform(220, 420), rng.uniform(260, 360)
        def script(i, t):
            if t < t_fall:
                return [{"id": 1, "bbox": box(cx - 30, 220, cy - 220), "zone": None, "posture": "STANDING"}]
            kp, b = lying(cx, cy)
            return [{"id": 1, "bbox": b, "zone": None, "kp": kp, "posture": "SITTING"}]
        def judge(dec, ev):
            ds = [d for d in dec if d["rule_type"] == "MAN_DOWN"]
            got = ds[0]["status"] if ds else None
            extra = {}
            if ds and ds[0]["status"] == "POTENTIAL_VIOLATION":
                decided = ds[0]["video_time_s"] + ds[0]["evidence"].get("seconds_down", 0)
                extra["time_to_alert_s"] = round(decided - t_fall, 2)
            return classify("POTENTIAL_VIOLATION", got), got, extra
        return script, [rule("MAN_DOWN", zone=None)], t_fall + 12, judge
    out.append(("M1", "A worker falls and lies still (classifier labels them SITTING)", "POTENTIAL_VIOLATION (CRITICAL) within seconds", "MAN_DOWN", m1))

    def m2(rng, fps):
        t0 = rng.uniform(4, 7)
        kind = rng.choice(["bending over", "sitting upright", "crawling"])
        cx = rng.uniform(220, 380)
        def script(i, t):
            if t < t0:
                return [{"id": 1, "bbox": box(cx - 30, 220, 120), "zone": None}]
            if kind == "bending over":
                kp, b = bent(cx, 120)
                return [{"id": 1, "bbox": b, "zone": None, "kp": kp, "posture": "STANDING"}]
            if kind == "sitting upright":
                b = box(cx - 30, 220, 120)
                return [{"id": 1, "bbox": b, "zone": None, "kp": standing_kp(b), "posture": "SITTING"}]
            kp, b = lying(cx - 120 + 50 * (t - t0), 320)               # moving along the floor
            return [{"id": 1, "bbox": b, "zone": None, "kp": kp, "posture": "SITTING"}]
        def judge(dec, ev):
            ds = [d for d in dec if d["rule_type"] == "MAN_DOWN"]
            got = ds[0]["status"] if ds else None
            return ("correct" if got is None else "wrong"), got or "nothing", {"variant": kind}
        return script, [rule("MAN_DOWN", zone=None)], t0 + 20, judge
    out.append(("M2", "Hard negatives: bending over, sitting upright, crawling", "nothing", "MAN_DOWN", m2))
    return out


def summarise(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    a = [abs(v) for v in vals]
    return {"n": len(vals), "mean_abs": round(statistics.mean(a), 3), "max_abs": round(max(a), 3), "mean": round(statistics.mean(vals), 3)}


def main():
    t_start = time.time()
    workdir = tempfile.mkdtemp(prefix="eval_runs_")
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "variants_per_scenario": VARIANTS, "frame_rates": [10, 15, 25],
              "config": {k: v for k, v in CFG.as_dict().items() if not k.startswith("local_")}, "scenarios": [], "confusion": {}}
    confusion = defaultdict(Counter)
    try:
        for sid, title, expectation, under_test, make in scenarios():
            runs = []
            for v in range(VARIANTS):
                fps = (10, 15, 25)[v % 3]
                rng = random.Random(f"{sid}-{v}")
                script, rules, seconds, judge = make(rng, fps)
                dec, ev = run(script, rules, seconds, fps, workdir)
                verdict, got, extra = judge(dec, ev)
                runs.append({"variant": v, "fps": fps, "outcome": verdict, "got": got, **extra})
                exp_key = extra.get("variant") or expectation
                confusion[f"{sid} {under_test}"][f"{exp_key} -> {got}"] += 1
            counts = Counter(r["outcome"] for r in runs)
            entry_err = summarise([r.get("entry_error_s") for r in runs])
            exit_err = summarise([r.get("exit_error_s") for r in runs])
            dwell_err = summarise([r.get("dwell_error_s") for r in runs])
            tta = [r["time_to_alert_s"] for r in runs if "time_to_alert_s" in r]
            item = {"id": sid, "title": title, "expected": expectation, "rule": under_test, "runs": len(runs),
                    "correct": counts["correct"], "abstained": counts["abstained"], "wrong": counts["wrong"],
                    "timing": {k: v for k, v in (("entry_error_s", entry_err), ("exit_error_s", exit_err), ("dwell_error_s", dwell_err)) if v},
                    "results": runs}
            if tta:
                item["time_to_alert_s"] = {"median": round(statistics.median(tta), 2), "max": round(max(tta), 2), "min": round(min(tta), 2)}
            report["scenarios"].append(item)
            print(f"{sid:4s} {counts['correct']:2d} correct, {counts['abstained']:2d} abstained, {counts['wrong']:2d} wrong   {title}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    report["confusion"] = {k: dict(v) for k, v in confusion.items()}
    total = Counter()
    for s in report["scenarios"]:
        total.update({"correct": s["correct"], "abstained": s["abstained"], "wrong": s["wrong"]})
    report["totals"] = {**total, "runs": sum(total.values())}
    report["seconds"] = round(time.time() - t_start, 1)
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(report, open(os.path.join(RESULTS, "safety_evaluation.json"), "w", encoding="utf-8"), indent=1, default=str)
    print(f"total: {dict(total)} in {report['seconds']} s -> backend/eval/results/safety_evaluation.json")
    return 0 if total["wrong"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
