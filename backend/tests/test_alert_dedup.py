"""
Step 10: alerts are decisions, raised once, and the live payload says how many are open.

An alert is a POTENTIAL_VIOLATION decision nobody has reviewed. What is checked:

  * standing in a restricted zone for a minute raises ONE decision, and one entry in the payload;
  * leaving and coming straight back does not raise another (re-alert window, 60 s), but coming
    back after the window does;
  * `open_count` in each frame's safety block counts this session's open flags (0 before the first,
    then 1, 2 ...), and abstentions and compliant decisions are not alerts;
  * people with an open flag are marked `flagged` in the entities that are sent, nobody else is;
  * the severity on the decision is the rule's;
  * the whole payload is JSON-serialisable.

    venv/Scripts/python.exe backend/tests/test_alert_dedup.py
"""
import asyncio
import atexit
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                               # noqa: E402
from sqlalchemy import create_engine                             # noqa: E402

from app.db.database import configure_sqlite                     # noqa: E402
from app.db.safety_models import ensure_safety_tables            # noqa: E402
from app.db.safety_writer import SafetyWriter                    # noqa: E402
from app.safety.clock import FrameClock                          # noqa: E402
from app.safety.config import SafetyConfig                       # noqa: E402
from app.safety.engine import SafetyEngine                       # noqa: E402
from app.safety.ppe_runner import PpeRunner                      # noqa: E402
from app.safety.rules import default_rules                       # noqa: E402
from app.safety.session import SafetySession                     # noqa: E402

TMP = tempfile.mkdtemp(prefix="vw_alert_")
atexit.register(shutil.rmtree, TMP, True)
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


ZONES = [{"zone_id": "press", "zone_name": "Press line", "zone_type": "RESTRICTED"}]
CFG = SafetyConfig(ppe_every_n_frames=1, ppe_crop_below_px=0.0, ppe_hivis_min_fraction=0.0)
FRAME = np.zeros((480, 640, 3), np.uint8)
FPS = 10.0
_n = [0]


class Scene:
    def __init__(self):
        self.helmet, self.vest, self.boxes, self.fail = {}, {}, {}, False

    def detect(self, frame, imgsz=None):
        if self.fail:
            raise RuntimeError("detector down")
        dets = []
        for tid, b in self.boxes.items():
            x1, y1, x2, y2 = b
            h = y2 - y1
            if self.helmet.get(tid, True):
                dets.append({"cls": "helmet", "conf": 0.9, "bbox": [x1 + 0.2 * (x2 - x1), y1 - 0.03 * h, x2 - 0.2 * (x2 - x1), y1 + 0.17 * h]})
            if self.vest.get(tid, True):
                dets.append({"cls": "safety_vest", "conf": 0.9, "bbox": [x1 + 0.05 * (x2 - x1), y1 + 0.2 * h, x2 - 0.05 * (x2 - x1), y1 + 0.55 * h]})
        return dets


def make(scene, rules=None):
    _n[0] += 1
    path = os.path.join(TMP, f"a{_n[0]}.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id="00000000-0000-0000-0000-0000000000aa", session_id="s1", engine=dbe, retry_interval_s=0.0)
    session = SafetySession(SafetyEngine(FrameClock(fps=FPS, recorded_at="2026-11-09T04:00:00Z")), writer, CFG,
                            zones_config=ZONES, ppe_runner=PpeRunner(scene, CFG), rules=rules)
    return session, path


def rows(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM safety_decisions ORDER BY video_time_s, rowid")]
    finally:
        c.close()


def box(i):
    return [100.0 + 150 * i, 100.0, 160.0 + 150 * i, 400.0]


async def play(session, scene, workers, frames, zone_of):
    """Returns [(payload, entities)] per frame. workers: {track id: box index}."""
    out = []
    for i in range(frames):
        ft = session.begin_frame(i)
        dets, ents = [], []
        for tid, bi in workers.items():
            scene.boxes[tid] = box(bi)
            dets.append({"track_id": tid, "bbox": box(bi)})
        await session.detect_ppe(ft, dets, FRAME, FRAME.shape)
        for tid, bi in workers.items():
            ents.append({"track_id": tid, "zone_id": zone_of(tid, i), "bbox": box(bi)})
        await session.after_frame(ft, ents)
        out.append((session.payload(ft), ents))
    await session.finish()
    return out


run = asyncio.run
inside = lambda start, end=10 ** 9: (lambda tid, i: "press" if start <= i < end else None)

print("=" * 66)
print("ALERT DE-DUPLICATION AND THE LIVE PAYLOAD")
print("=" * 66)

# ── 1. A minute in the zone: one alert ─────────────────────────────────
print()
print("1. Standing in the restricted zone for a minute")
scene = Scene(); scene.helmet[1] = False
s, path = make(scene)
frames = run(play(s, scene, {1: 0}, 700, inside(20)))        # enters at 2 s, stays 68 s
dec = rows(path)
check("one decision, a POTENTIAL_VIOLATION", len(dec) == 1 and dec[0]["status"] == "POTENTIAL_VIOLATION", str([(d["status"], d["video_time_s"]) for d in dec]))
carried = [d for p, _ in frames for d in p.get("decisions", [])]
check("the payload carried it exactly once", len(carried) == 1 and carried[0]["id"] == dec[0]["id"])
first = next(i for i, (p, _) in enumerate(frames) if p.get("decisions"))
check("open_count is 0 until the flag, then 1, and stays 1 for the rest of the minute",
      all(p["open_count"] == 0 for p, _ in frames[:first]) and all(p["open_count"] == 1 for p, _ in frames[first:]), f"first flag at frame {first}")
check("the flag came within seconds of the entry (the evidence wait), not at the end", first <= 20 + 60, f"frame {first} for an entry at frame 20")
check("the worker is marked 'flagged' in the entities from the flag on, and not before",
      all("flagged" not in e for _, ents in frames[:first] for e in ents) and all(ents[0].get("flagged") is True for _, ents in frames[first:]))
check("the payload is JSON-serialisable", all(json.dumps(p) and json.dumps(ents) for p, ents in frames[::50]))

# ── 2. Out and back ─────────────────────────────────────────────────────
print()
print("2. Leaving and coming back")
scene = Scene(); scene.helmet[1] = False
s, path = make(scene)
zone_of = lambda tid, i: "press" if (20 <= i < 100 or 200 <= i < 250 or 750 <= i < 820) else None      # in 2-10 s, 20-25 s, 75-82 s
frames = run(play(s, scene, {1: 0}, 900, zone_of))
dec = rows(path)
times = [round(d["video_time_s"]) for d in dec]
check("coming straight back (20 s after the first entry) raises no second alert", 20 not in times and 2 in times, str(times))
check("coming back after the 60 s window (75 s) raises a second one", 75 in times and len(dec) == 2, str(times))
check("open_count follows: 1, then 2", frames[-1][0]["open_count"] == 2 and any(p["open_count"] == 1 for p, _ in frames))
check("each decision was carried in the payload once", len([d for p, _ in frames for d in p.get("decisions", [])]) == 2)

# ── 3. Several people ───────────────────────────────────────────────────
print()
print("3. Several people, and what is not an alert")
scene = Scene(); scene.helmet[1] = False; scene.vest[2] = False
s, path = make(scene)
frames = run(play(s, scene, {1: 0, 2: 1, 3: 2}, 200, inside(20)))                  # 3 is fully equipped
dec = rows(path)
status = {d["worker_key"]: d["status"] for d in dec}
check("two flagged, one compliant", status == {"trk-1": "POTENTIAL_VIOLATION", "trk-2": "POTENTIAL_VIOLATION", "trk-3": "COMPLIANT"}, str(status))
last_payload, last_ents = frames[-1]
check("open_count is 2: a compliant decision is not an alert", last_payload["open_count"] == 2)
check("only the two flagged people are marked", {e["track_id"] for e in last_ents if e.get("flagged")} == {1, 2})

scene = Scene(); scene.fail = True
s, path = make(scene)
frames = run(play(s, scene, {1: 0}, 120, inside(20)))
dec = rows(path)
check("an abstention (UNVERIFIABLE) is informational: stored, not counted as an alert, nobody marked",
      dec and all(d["status"] == "UNVERIFIABLE" for d in dec) and frames[-1][0]["open_count"] == 0
      and not any(e.get("flagged") for _, ents in frames for e in ents), str([(d["status"]) for d in dec]))

# ── 3b. A jump in the video ────────────────────────────────────────────
print()
print("3b. After a seek")
scene = Scene(); scene.helmet[1] = False
s, path = make(scene)
frames = run(play(s, scene, {1: 0}, 120, inside(20)))
check("before the jump the worker is flagged and counted", frames[-1][1][0].get("flagged") is True and frames[-1][0]["open_count"] == 1)
s.reset("SEEK", frame_index=500)
ft = s.begin_frame(500)
ents = [{"track_id": 1, "zone_id": None, "bbox": box(0)}]
run(s.after_frame(ft, ents))
check("after a seek nobody is drawn as flagged (a tracker may reuse the id for someone else), but the run's count stays",
      "flagged" not in ents[0] and s.payload(ft)["open_count"] == 1)

# ── 4. Severity is the rule's ───────────────────────────────────────────
print()
print("4. Severity")
scene = Scene(); scene.helmet[1] = False
s, path = make(scene)
frames = run(play(s, scene, {1: 0}, 150, inside(20)))
check("the built-in rule raises a WARNING", rows(path)[0]["severity"] == "WARNING" and [d for p, _ in frames for d in p.get("decisions", [])][0]["severity"] == "WARNING")
import dataclasses                                               # noqa: E402
critical = [dataclasses.replace(r, severity="CRITICAL") for r in default_rules(ZONES)]
scene = Scene(); scene.helmet[1] = False
s, path = make(scene, rules=critical)
frames = run(play(s, scene, {1: 0}, 150, inside(20)))
check("a rule set to CRITICAL raises CRITICAL, in the database and in the payload",
      rows(path)[0]["severity"] == "CRITICAL" and [d for p, _ in frames for d in p.get("decisions", [])][0]["severity"] == "CRITICAL")

# ── 5. Safety rules off ─────────────────────────────────────────────────
print()
print("5. A session with no PPE check has no alerts to count")
_n[0] += 1
dbe = create_engine(f"sqlite:///{os.path.join(TMP, 'plain.sqlite')}", connect_args={"check_same_thread": False})
configure_sqlite(dbe, busy_timeout_ms=250)
ensure_safety_tables(dbe)
plain = SafetySession(SafetyEngine(FrameClock(fps=FPS, recorded_at="2026-11-09T04:00:00Z")),
                      SafetyWriter("cam1", org_id=None, engine=dbe, retry_interval_s=0.0), CFG, zones_config=ZONES)
ft = plain.begin_frame(0)
check("no 'open_count' key when no rules are running (the payload is as before)", "open_count" not in plain.payload(ft))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
