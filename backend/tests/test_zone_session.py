"""
The safety session around a video (Step 4): clock + zone events + database.

Checks the glue the tracker tests cannot: that frame positions become rows in
the real table with the right wall-clock time, that a seek or a replay never
writes the same thing twice, that the end of a video closes open visits, and
that nothing here can raise into the video loop.

Everything runs against a throwaway SQLite file with the app's engine settings.

Run:  venv/Scripts/python.exe backend/tests/test_zone_session.py
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

from sqlalchemy import create_engine                                # noqa: E402

from app.cv.frame_pipeline import process_detections              # noqa: E402
from app.cv.spatial_engine import SpatialEngine                    # noqa: E402
from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables              # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.config import SafetyConfig                         # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.session import SafetySession, safety_session_for, worker_key   # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_zone_session_")
atexit.register(shutil.rmtree, TMP, True)
_n = [0]


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def new_session(fps=10.0, recorded_at="2026-11-08T04:00:00Z", live=False, config=None):
    _n[0] += 1
    path = os.path.join(TMP, f"s{_n[0]}.sqlite")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(engine, busy_timeout_ms=250)
    ensure_safety_tables(engine)
    writer = SafetyWriter("cam1", org_id="org-1", session_id="sess-1", engine=engine, retry_interval_s=0.0)
    if live:
        # A fake machine clock that moves 0.1 s per reading, so a live test
        # does not have to sleep.
        ticks = {"n": 0}

        def mono():
            ticks["n"] += 1
            return ticks["n"] * 0.1

        clock = FrameClock(live=True, wall=lambda: 1_794_000_000.0 + ticks["n"] * 0.1, mono=mono)
    else:
        clock = FrameClock(fps=fps, recorded_at=recorded_at)
    session = SafetySession(SafetyEngine(clock), writer, config or SafetyConfig())
    return session, path


def rows(path, where=""):
    c = sqlite3.connect(path)
    try:
        return c.execute(
            "SELECT event_type, worker_key, zone_id, video_time_s, observed_at, details, session_key, org_id, camera_id "
            f"FROM safety_events {where} ORDER BY id").fetchall()
    finally:
        c.close()


def ent(track, zone):
    return {"track_id": track, "zone_id": zone}


async def drive(session, start, end, entities_for, seek_to=None):
    """Run frames [start, end) through the session as a video loop would."""
    for i in range(start, end):
        ft = session.begin_frame(i)
        await session.after_frame(ft, entities_for(i))


def run(coro):
    return asyncio.run(coro)


# ── 1. A walk becomes rows ───────────────────────────────────────────────────
print("1. A walk becomes correctly timed rows")
session, path = new_session()


def walk(i):
    if i < 20:
        return [ent(7, "TRANSIT_ZONE")]
    if i < 70:
        return [ent(7, "A")]
    return [ent(7, "TRANSIT_ZONE")]


async def scenario1():
    await drive(session, 0, 100, walk)
    await session.finish()

run(scenario1())
r = rows(path)
check("ENTERED_ZONE and EXITED_ZONE are stored", [x[0] for x in r] == ["ENTERED_ZONE", "EXITED_ZONE"], str(r))
check("the worker key is the anonymous track key", all(x[1] == "trk-7" for x in r) and worker_key(7) == "trk-7")
check("video times are the back-dated change times (2.0 s in, 7.0 s out)", r[0][3] == 2.0 and r[1][3] == 7.0, str([x[3] for x in r]))
check("observed_at is the recording time plus the video time (04:00:02 / 04:00:07)",
      r[0][4] == "2026-11-08T04:00:02.000+00:00" and r[1][4] == "2026-11-08T04:00:07.000+00:00", str([x[4] for x in r]))
check("the row carries org, camera, session and zone", all(x[7] == "org-1" and x[8] == "cam1" and x[6] == "sess-1" for x in r) and r[0][2] == "A")
check("the exit's details are stored as JSON", json.loads(r[1][5]).get("reason") == "LEFT", str(r[1][5]))

# ── 2. End of video closes open visits ──────────────────────────────────────
print("2. A person still inside when the video ends")
session, path = new_session()


async def scenario2():
    await drive(session, 0, 50, lambda i: [ent(3, "A")])
    await session.finish()

run(scenario2())
r = rows(path)
check("the open visit is closed as EXITED_ZONE (SESSION_END), unverifiable",
      [x[0] for x in r] == ["ENTERED_ZONE", "EXITED_ZONE"]
      and json.loads(r[1][5]).get("reason") == "SESSION_END" and json.loads(r[1][5]).get("unverifiable") is True, str(r))
check("stamped at the last frame (4.9 s), with the matching wall-clock time",
      r[1][3] == 4.9 and r[1][4] == "2026-11-08T04:00:04.900+00:00", str(r[1]))
check("the first event says the worker was already there (first sighting)", json.loads(r[0][5]).get("first_sighting") is True)
run(session.finish())
check("finishing twice writes nothing more", len(rows(path)) == 2)

# ── 3. Seek ─────────────────────────────────────────────────────────────────
print("3. Seeking")
session, path = new_session()


async def scenario3():
    await drive(session, 0, 60, lambda i: [ent(1, "A")])      # 0-5.9 s in A
    session.reset("SEEK", frame_index=200)                   # forward jump to 20 s
    await drive(session, 200, 230, lambda i: [ent(1, "A")])
    await session.finish()

run(scenario3())
r = rows(path)
t = [(x[0], x[3]) for x in r]
check("seek forward: the first visit is closed unverifiable at 5.9 s, then a fresh visit begins at 20 s",
      t == [("ENTERED_ZONE", 0.0), ("EXITED_ZONE", 5.9), ("ENTERED_ZONE", 20.0), ("EXITED_ZONE", 22.9)], str(t))
check("the seek closing record says why",
      json.loads(r[1][5]).get("reason") == "SEEK" and json.loads(r[3][5]).get("reason") == "SESSION_END")
check("no closing record is stamped at the resume position (negative or reversed dwell)",
      all(json.loads(x[5]).get("dwell_s", 0) >= 0 for x in r if x[0] == "EXITED_ZONE"))

session, path = new_session()


async def scenario3b():
    await drive(session, 0, 100, lambda i: [ent(1, "A")] if i >= 5 else [])   # in A from 0.5, to 9.9 s
    session.reset("SEEK", frame_index=30)                    # back to 3 s
    await drive(session, 30, 100, lambda i: [ent(1, "A")])     # replay 3.0-9.9
    await drive(session, 100, 150, lambda i: [ent(1, "A")] if i < 130 else [ent(1, "TRANSIT_ZONE")])
    await session.finish()

run(scenario3b())
r = rows(path)
t = [(x[0], x[3]) for x in r]
check("rewind: the replayed stretch writes no second ENTERED_ZONE",
      sum(1 for x in t if x[0] == "ENTERED_ZONE") == 1 and ("ENTERED_ZONE", 0.5) in t, str(t))
check("...but footage after the replay is processed normally (the exit at 13.0 s is written)",
      ("EXITED_ZONE", 13.0) in t, str(t))
leave = [x for x in r if x[0] == "EXITED_ZONE" and x[3] == 13.0][0]
check("the exit of a visit whose entry fell in replayed footage says so",
      json.loads(leave[5]).get("visit_started_in_replay") is True, str(leave[5]))
check("the seek's own closing record IS written (it is a record of real observation)",
      any(x[0] == "EXITED_ZONE" and json.loads(x[5]).get("reason") == "SEEK" and x[3] == 9.9 for x in r), str(t))

# ── 4. Payload ──────────────────────────────────────────────────────────────
print("4. What the dashboard is told")
session, path = new_session()


async def scenario4():
    payloads = []
    for i in range(30):
        ft = session.begin_frame(i)
        await session.after_frame(ft, [ent(2, "A")])
        payloads.append(session.payload(ft))
    return payloads

pl = run(scenario4())
withev = [p for p in pl if "events" in p]
check("an event appears in the payload of the frame that raised it (and only that frame)",
      len(withev) == 1 and withev[0]["events"][0]["event_type"] == "ENTERED_ZONE", str(withev))
check("payload events are plain JSON", json.loads(json.dumps(withev[0])) == withev[0])
check("frames with nothing new carry only the clock", set(pl[0]) == {"video_time_s", "observed_at", "replay"}, str(pl[0]))

# ── 5. Live source ──────────────────────────────────────────────────────────
print("5. A live source")
session, path = new_session(live=True)


async def scenario5():
    for i in range(30):
        ft = session.begin_frame()
        await session.after_frame(ft, [ent(4, "A")])
    await session.finish()

run(scenario5())
r = rows(path)
check("live events are stored with a wall-clock observed_at",
      r and r[0][4].endswith("+00:00") and r[0][4].startswith("20"), str(r[:1]))
check("a live visit open at the end is closed", [x[0] for x in r][-1] == "EXITED_ZONE")

# ── 6. It can never raise into the video ───────────────────────────────────
print("6. Failures stay out of the video loop")
session, path = new_session()


class BrokenWriter:
    def add_event(self, *a, **k):
        raise RuntimeError("disk on fire")

    def has_pending(self):
        return False

    def in_backoff(self):
        return False

session.writer = BrokenWriter()


async def scenario6():
    await drive(session, 0, 40, lambda i: [ent(1, "A")])
    await session.finish()

try:
    run(scenario6())
    ok = True
except Exception as e:
    ok = False
    print("   raised:", e)
check("a writer that raises on every event does not break the frame loop", ok)

session, path = new_session()
async def scenario6b():
    await drive(session, 0, 20, lambda i: [{"zone_id": "A"}, {"track_id": None, "zone_id": "A"}, ent(1, "A")])
    await session.finish()
try:
    run(scenario6b())
    ok = True
except Exception as e:
    ok = False
check("entities without a track id are skipped, not fatal", ok)
check("...and the good worker was still tracked", [x[0] for x in rows(path)] == ["ENTERED_ZONE", "EXITED_ZONE"])

session, path = new_session()
async def scenario6c():
    await drive(session, 0, 10, lambda i: [ent(1, "A")])
    session.tracker.update = None     # break the tracker
    await drive(session, 10, 20, lambda i: [ent(1, "A")])
try:
    run(scenario6c())
    ok = True
except Exception:
    ok = False
check("a broken tracker is skipped, not fatal", ok)

# ── 7. The session is created only when safety is on ────────────────────────
print("7. The switch")
os.environ.pop("SAFETY_ENABLED", None)
check("off by default: no session at all", safety_session_for(fps=25.0, camera_id="c") is None)
os.environ["SAFETY_ENABLED"] = "true"
try:
    s_ = safety_session_for(fps=25.0, recorded_at="2026-11-08T04:00:00Z", camera_id="c", org_id=None, session_id="x")
    check("on: a session whose engine drives the tracker", s_ is not None and s_.tracker in s_.engine.components)
    try:
        safety_session_for(fps=0, camera_id="c")
        bad = False
    except ValueError:
        bad = True
    check("a bad fps raises ValueError (the socket reports it and stops)", bad)
    l_ = safety_session_for(live=True, camera_id="c")
    check("a live session needs no fps", l_ is not None and l_.engine.clock.live)
finally:
    os.environ.pop("SAFETY_ENABLED", None)

# ── 8. Zones judged on the feet ─────────────────────────────────────────────
print("8. The zone hit test uses the feet when safety is on")


class _Anonymizer:
    def blur_face_region(self, *a, **k):
        pass


class _Activity:
    def update_track(self, *a): pass
    def calculate_activity_score(self, *a): return 0.0
    def get_dwell_time_seconds(self, *a): return 0


import numpy as np                                                    # noqa: E402
zone = [{"zone_id": "floor", "zone_name": "Floor", "zone_type": "RESTRICTED",
         "polygon": [[0, 300], [640, 300], [640, 480], [0, 480]]}]
spatial = SpatialEngine(zones_config=zone)
frame = np.zeros((480, 640, 3), dtype=np.uint8)
# A tall person: the box centre (y=250) is above the floor polygon, the feet (y=400) are inside.
det = [{"track_id": 1, "bbox": [100, 100, 200, 400], "posture": "STANDING"}]
legacy, _ = process_detections(det, frame.copy(), spatial, _Activity(), _Anonymizer(), zone, False)
feet, summary = process_detections(det, frame.copy(), spatial, _Activity(), _Anonymizer(), zone, False, ground_point_zones=True)
check("default (analytics): the box centre decides, exactly as before", legacy[0]["zone_id"] == "TRANSIT_ZONE")
check("safety: the feet decide, so the person is in the floor zone", feet[0]["zone_id"] == "floor" and summary["floor"] == 1)

# ── 9. Only zones a rule can attach to make events ────────────────────────
print("9. GENERAL and legacy zones are not safety events")
from app.safety.zone_events import rule_zone_ids                      # noqa: E402

zc = [
    {"zone_id": "chk", "zone_type": "PPE_CHECKPOINT"}, {"zone_id": "app", "zone_type": "approach"},
    {"zone_id": "res", "zone_type": "RESTRICTED"}, {"zone_id": "ex", "zone_type": "EXIT"},
    {"zone_id": "gen", "zone_type": "GENERAL"}, {"zone_id": "old", "zone_type": "WORKSTATION"},
    {"zone_id": "none", "zone_type": None}, {"zone_id": "blank"},
]
check("rule_zone_ids picks exactly the four rule types (case-insensitive)",
      rule_zone_ids(zc) == frozenset({"chk", "app", "res", "ex"}), str(rule_zone_ids(zc)))
check("no zones configured -> none tracked", rule_zone_ids([]) == frozenset() and rule_zone_ids(None) == frozenset())

_n[0] += 1
path = os.path.join(TMP, f"s{_n[0]}.sqlite")
dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
configure_sqlite(dbe, busy_timeout_ms=250)
ensure_safety_tables(dbe)
session = SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")),
                        SafetyWriter("cam1", engine=dbe, retry_interval_s=0.0), SafetyConfig(), zones_config=zc)


async def scenario9():
    await drive(session, 0, 40, lambda i: [ent(1, "gen"), ent(2, "old"), ent(3, "res"), ent(4, "TRANSIT_ZONE")])
    await session.finish()

run(scenario9())
r = rows(path)
check("only the worker in the RESTRICTED zone produced events", {x[1] for x in r} == {"trk-3"} and r[0][2] == "res", str(r))

# ── 10. Review findings ─────────────────────────────────────────────────────
print("10. A seek during replayed footage writes no orphan exit")
session, path = new_session()


async def scenario10():
    await drive(session, 0, 500, lambda i: [ent(1, "A")] if 100 <= i < 500 else [])   # in A from 10 s
    session.reset("SEEK", frame_index=0)
    await drive(session, 0, 300, lambda i: [ent(1, "A")] if i >= 100 else [])           # replay up to 30 s
    session.reset("SEEK", frame_index=0)                                                # seek again, still in replay
    await drive(session, 0, 500, lambda i: [ent(1, "A")] if i >= 100 else [])           # replay everything
    await session.finish()

run(scenario10())
r = rows(path)
t = [(x[0], x[3]) for x in r]
check("one ENTERED and exactly the original SEEK closing: nothing for the replayed stretch",
      [x[0] for x in r] == ["ENTERED_ZONE", "EXITED_ZONE"], str(t))
session, path = new_session()


async def scenario10b():
    await drive(session, 0, 300, lambda i: [ent(1, "A")] if i >= 100 else [])           # to 29.9 s
    session.reset("SEEK", frame_index=100)
    await drive(session, 100, 400, lambda i: [ent(1, "A")])                              # replay, then 10 s of new footage
    session.reset("SEEK", frame_index=0)                                                 # seek again after new footage
    await drive(session, 0, 20, lambda i: [])
    await session.finish()

run(scenario10b())
r = rows(path)
closing = [x for x in r if x[0] == "EXITED_ZONE"]
check("a visit that began in replay and reached NEW footage before the next seek is closed, and flagged",
      len(closing) == 2 and json.loads(closing[1][5]).get("visit_started_in_replay") is True and closing[1][3] == 39.9, str([(x[0], x[3], x[5]) for x in r]))

print("11. A frame that fails after a seek cannot lose the closing record")
session, path = new_session()


async def scenario11():
    await drive(session, 0, 50, lambda i: [ent(1, "A")])
    session.reset("SEEK", frame_index=200)
    session.begin_frame(200)                 # the frame, then the pipeline raises before after_frame()
    await session.finish()

run(scenario11())
check("the visit the seek closed is still written at the end of the session",
      [x[0] for x in rows(path)] == ["ENTERED_ZONE", "EXITED_ZONE"] and json.loads(rows(path)[1][5]).get("reason") == "SEEK", str(rows(path)))

print("12. Finishing while in replay")
session, path = new_session()


async def scenario12():
    await drive(session, 0, 200, lambda i: [ent(1, "A")])
    session.reset("SEEK", frame_index=50)
    await drive(session, 50, 120, lambda i: [ent(1, "A")])                # replaying, session ends here
    await session.finish()

run(scenario12())
check("no orphan SESSION_END for footage the database already has",
      [json.loads(x[5]).get("reason") for x in rows(path) if x[0] == "EXITED_ZONE"] == ["SEEK"], str([(x[0], x[3], x[5]) for x in rows(path)]))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
