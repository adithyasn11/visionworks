"""
Robustness (Step 18), on the real engine with the scripted detectors of the scenario suite:

  A. A camera that stops: every open visit, wait and exit timer is closed as UNVERIFIABLE, nothing is accused, the wording
     says the camera stopped, and a camera that comes back starts a fresh stretch.
  B. A very long session: an hour of video with people arriving and leaving, every rule on. State must stay bounded
     (tracked people, evidence, samples, processed-time spans) and memory must stop growing.
  C. Two sessions on one camera at once (a duplicate tab, a reconnect) write their own rows and never each other's.
  D. A restart: rows written before the process died are still there, still unsynced, and retention does not touch them.
  E. The master switch: SAFETY_ENABLED=false builds no engine and no session.

    venv/Scripts/python.exe backend/tests/test_robustness.py
"""
import asyncio
import gc
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import tracemalloc
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "eval"))

import safety_scenarios as ss                                        # noqa: E402
from sqlalchemy import create_engine                                # noqa: E402

from app.db.database import configure_sqlite                       # noqa: E402
from app.db.safety_models import ensure_safety_tables, purge_local  # noqa: E402
from app.db.safety_writer import SafetyWriter                      # noqa: E402
from app.safety.clock import FrameClock                            # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.ppe_runner import PpeRunner                        # noqa: E402
from app.safety.session import SafetySession                       # noqa: E402

FAILS = []
ORG = "11111111-1111-4111-8111-111111111111"


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {str(detail)[:260]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def make(work, name, session_id="s1", fps=10.0, rules=None):
    path = os.path.join(work, name)
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    writer = SafetyWriter("cam1", org_id=ORG, session_id=session_id, engine=dbe, retry_interval_s=0.0)
    scene = ss.Scene()
    session = SafetySession(SafetyEngine(FrameClock(fps=fps, recorded_at="2026-11-08T04:00:00Z")), writer, ss.CFG,
                            zones_config=ss.ZONES, ppe_runner=PpeRunner(scene, ss.CFG), rules=rules)
    return session, scene, dbe, path


def worker(i, x, zone, **kw):
    return {"id": i, "bbox": ss.box(x), "zone": zone, **kw}


async def frame(session, scene, workers, i):
    scene.workers = {w["id"]: w for w in workers}
    ft = session.begin_frame(i)
    dets = [{"track_id": w["id"], "bbox": list(w["bbox"]), "confidence": 0.9, "posture": "STANDING", "keypoints": ss.standing_kp(w["bbox"])}
            for w in workers]
    await session.detect_ppe(ft, [{"track_id": d["track_id"], "bbox": d["bbox"]} for d in dets], ss.FRAME, ss.SHAPE)
    await session.after_frame(ft, [{"track_id": w["id"], "zone_id": w.get("zone"), "bbox": list(w["bbox"])} for w in workers],
                              detections=dets, frame_shape=ss.SHAPE)


def read(path, table):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        c.close()


work = tempfile.mkdtemp(prefix="vw_robust_")
try:
    # ── A. the camera stops ──────────────────────────────────────────────────
    print("A. A camera that stops")
    rules = [ss.rule("MAX_DWELL", max_seconds=60), ss.rule("PPE_ENTRY", required_ppe=["helmet"], mode="AT_ENTRY")]
    session, scene, dbe, path = make(work, "a.sqlite", rules=rules)

    async def a():
        for i in range(120):                                             # 12 s: a worker with a helmet stands in the press zone
            await frame(session, scene, [worker(1, 200, "press")], i)
        session.reset("CAMERA_LOST")                                     # the live loop calls this when pictures stop
        await session.finish()                                           # and the connection is dropped (nobody reconnects)
    asyncio.run(a())
    dec, ev = read(path, "safety_decisions"), read(path, "safety_events")
    exits = [e for e in ev if e["event_type"] == "EXITED_ZONE"]
    check("the open visit is closed, once, with the reason CAMERA_LOST and marked unverifiable",
          len(exits) == 1 and json.loads(exits[0]["details"]).get("reason") == "CAMERA_LOST" and json.loads(exits[0]["details"]).get("unverifiable") is True, str(exits))
    dwell = [d for d in dec if d["rule_type"] == "MAX_DWELL"]
    check("the stay gets an UNVERIFIABLE decision that says the camera stopped, never an accusation",
          len(dwell) == 1 and dwell[0]["status"] == "UNVERIFIABLE" and "camera stopped sending pictures" in dwell[0]["reason"], str(dwell)[:300])
    check("nothing was flagged as a potential violation", not any(d["status"] == "POTENTIAL_VIOLATION" for d in dec), str([d["status"] for d in dec]))
    check("finish() after the reset writes no duplicates", len(dwell) == 1 and len([e for e in ev if e["event_type"] == "ENTERED_ZONE"]) == 1)
    dbe.dispose()

    # a worker mid-way through an exit timer, then the camera stops
    rules = [ss.rule("REQUIRED_EXIT", zone="press", exit_zone_id="exit", max_seconds=30)]
    session, scene, dbe, path = make(work, "a2.sqlite", rules=rules)

    async def a2():
        for i in range(60):
            await frame(session, scene, [worker(1, 200, "press")], i)
        for i in range(60, 120):                                         # walks out of the zone towards the exit
            await frame(session, scene, [worker(1, 200, "walk")], i)
        session.reset("CAMERA_LOST")
        await session.finish()
    asyncio.run(a2())
    dec = read(path, "safety_decisions")
    ex = [d for d in dec if d["rule_type"] == "REQUIRED_EXIT"]
    check("a person who had left but not yet reached the exit is UNVERIFIABLE, with the camera wording",
          len(ex) == 1 and ex[0]["status"] == "UNVERIFIABLE" and "camera stopped sending pictures" in ex[0]["reason"], str(ex)[:300])
    dbe.dispose()

    # the camera comes back: a fresh stretch, nothing carried over
    rules = [ss.rule("MAX_DWELL", max_seconds=60)]
    session, scene, dbe, path = make(work, "a3.sqlite", rules=rules)

    async def a3():
        for i in range(50):
            await frame(session, scene, [worker(1, 200, "press")], i)
        session.reset("CAMERA_LOST")
        for i in range(50, 150):                                         # pictures resume, the same track id is inside again
            await frame(session, scene, [worker(1, 200, "press")], i)
        await session.finish()
    asyncio.run(a3())
    ev = read(path, "safety_events")
    check("after the camera returns the run goes on: two visits, the first closed by the stop, the second by the end",
          [json.loads(e["details"]).get("reason") for e in ev if e["event_type"] == "EXITED_ZONE"] == ["CAMERA_LOST", "SESSION_END"]
          and len([e for e in ev if e["event_type"] == "ENTERED_ZONE"]) == 2, str([(e["event_type"], e["details"][:60]) for e in ev]))
    dbe.dispose()

    # ── B. a very long session ───────────────────────────────────────────────
    print("B. An hour of video, people coming and going, every rule on")
    FPS, HOURS_S = 5.0, 3600
    rules = [ss.rule("MAX_DWELL", max_seconds=45), ss.rule("PPE_ENTRY", required_ppe=["helmet", "safety_vest"], mode="AT_ENTRY"),
             ss.rule("PPE_CONTINUOUS", required_ppe=["helmet"]), ss.rule("REQUIRED_EXIT", zone="press", exit_zone_id="exit", max_seconds=30),
             ss.rule("MAN_DOWN", zone="press"), ss.rule("OCCUPANCY", zone="press", max_people=3)]
    import app.safety.session as session_module
    session_module._MAX_FLAGGED_KEYS = 5                                 # small, so the cap is reached and tested
    session, scene, dbe, path = make(work, "b.sqlite", fps=FPS, rules=rules)
    rng = random.Random(18)
    frames = int(HOURS_S * FPS)
    marks = {}

    def sizes():
        """How much state the session is holding, as a count of remembered things."""
        n = {}
        n["engine_spans"] = len(session.engine._processed)
        n["tracker_workers"] = len(session.tracker._workers)
        n["ppe_workers"] = len(session.ppe._last) + len(session.ppe._last_seen)
        c = session.compliance
        n["compliance_live"] = sum(len(getattr(c, a)) for a in ("_pending", "_visits", "_timers", "_cont"))      # what is being judged right now
        n["compliance_memory"] = sum(len(getattr(c, a)) for a in ("_cont_flag_t", "_seen_at", "_last_judged"))  # remembered for re-alert timing, pruned
        n["man_down"] = sum(len(d._people) for d in session.man_down.detectors) if session.man_down is not None else 0
        n["occupancy"] = sum(len(d._samples) for d in session.occupancy.detectors) if session.occupancy is not None else 0
        n["flagged"] = len(session._flagged)
        n["frame_buffers"] = len(session._closed_decisions) + len(session._closed_waiting)
        return n

    async def b():
        people = {}
        next_id = 1
        tracemalloc.start()
        for i in range(frames):
            if rng.random() < 0.012 and len(people) < 4:                 # someone arrives (about one a minute)
                people[next_id] = {"id": next_id, "x": rng.uniform(60, 400), "left": i + int(rng.uniform(100, 900)),
                                   "zone": rng.choice(["press", "walk", "gate"]), "helmet": rng.random() > 0.3, "vest": rng.random() > 0.3}
                next_id += 1
            for pid in [p for p, v in people.items() if v["left"] <= i]:
                del people[pid]
            ws = [{"id": p["id"], "bbox": ss.box(p["x"]), "zone": p["zone"], "helmet": p["helmet"], "vest": p["vest"]} for p in people.values()]
            await frame(session, scene, ws, i)
            if i in (frames // 4, frames // 2, frames - 1):
                gc.collect()
                marks[i] = (tracemalloc.get_traced_memory()[0], sizes())
        await session.finish()
        tracemalloc.stop()
    asyncio.run(b())
    q1, q2, q4 = sorted(marks)
    (m1, s1), (m2, s2), (m4, s4) = marks[q1], marks[q2], marks[q4]
    print(f"    memory in use: {m1 / 1e6:.2f} MB at 15 min, {m2 / 1e6:.2f} MB at 30 min, {m4 / 1e6:.2f} MB at 60 min; state at the end: {s4}")
    check("people who left are forgotten (tracked people, evidence and timers stay in single digits)",
          s4["tracker_workers"] <= 6 and s4["ppe_workers"] <= 12 and s4["compliance_live"] <= 24 and s4["man_down"] <= 6, str(s4))
    check("what the rules remember for re-alert timing is pruned (well under its 2000-entry cap; not one entry per second)",
          s4["compliance_memory"] <= 400 and s4["compliance_memory"] - s2["compliance_memory"] <= 100, f"{s2['compliance_memory']} -> {s4['compliance_memory']}")
    check("the record of processed time is one stretch, not one entry per frame", s4["engine_spans"] <= 2, str(s4))
    check("occupancy samples are a window, not a history", s4["occupancy"] <= 200, str(s4))
    check("the list of people outlined as flagged stops at its cap, and the flag count keeps counting",
          s4["flagged"] <= 5 and session._open_count > 5, f"{s4['flagged']} {session._open_count}")
    check("nothing piles up between frames", s4["frame_buffers"] <= 50, str(s4))
    check("memory stops growing: the last half hour added under 3 MB over the first half hour", m4 - m2 < 3e6, f"{m2} -> {m4}")
    dec = read(path, "safety_decisions")
    check("the hour produced decisions and no row is missing its video time or reason", len(dec) > 5 and all(d["reason"] and d["video_time_s"] is not None for d in dec), len(dec))
    dbe.dispose()

    # ── C. two sessions on one camera ────────────────────────────────────────
    print("C. Two sessions on one camera at the same time")
    path = os.path.join(work, "c.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    sessions = []
    for sid in ("tab-1", "tab-2"):
        scene = ss.Scene()
        w = SafetyWriter("cam1", org_id=ORG, session_id=sid, engine=dbe, retry_interval_s=0.0)
        sessions.append((SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")), w, ss.CFG, zones_config=ss.ZONES,
                                       ppe_runner=PpeRunner(scene, ss.CFG), rules=[ss.rule("MAX_DWELL", max_seconds=60)]), scene))

    async def c():
        for i in range(80):
            for k, (s, sc) in enumerate(sessions):
                await frame(s, sc, [worker(1 + k, 200, "press")], i)     # same camera, different people in each tab
        for s, _ in sessions:
            await s.finish()
    asyncio.run(c())
    ev = read(path, "safety_events")
    by = {}
    for e in ev:
        by.setdefault(e["session_key"], set()).add(e["worker_key"])
    check("each session wrote its own rows only", by == {"tab-1": {"trk-1"}, "tab-2": {"trk-2"}}, str(by))
    check("every visit is closed (two entries, two exits), none doubled", [e["event_type"] for e in ev].count("ENTERED_ZONE") == 2 and [e["event_type"] for e in ev].count("EXITED_ZONE") == 2)
    ids = [e["id"] for e in ev]
    check("every row has its own id", len(ids) == len(set(ids)))
    dbe.dispose()

    # ── D. a restart ─────────────────────────────────────────────────────────
    print("D. Restart: rows from before the process died")
    path = os.path.join(work, "d.sqlite")
    dbe = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe, busy_timeout_ms=250)
    ensure_safety_tables(dbe)
    scene = ss.Scene()
    w = SafetyWriter("cam1", org_id=ORG, session_id="before", engine=dbe, retry_interval_s=0.0)
    s = SafetySession(SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")), w, ss.CFG, zones_config=ss.ZONES,
                      ppe_runner=PpeRunner(scene, ss.CFG), rules=[ss.rule("PPE_ENTRY", required_ppe=["helmet"], mode="AT_ENTRY")])

    async def d():
        for i in range(80):
            await frame(s, scene, [worker(1, 200, "press", helmet=False)], i)
        await s.finish()
    asyncio.run(d())
    before = (len(read(path, "safety_events")), len(read(path, "safety_decisions")))
    dbe.dispose()                                                        # the process "dies"; a new one opens the same file
    dbe2 = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(dbe2, busy_timeout_ms=250)
    ensure_safety_tables(dbe2)                                           # what startup does: harmless on an existing file
    after = (len(read(path, "safety_events")), len(read(path, "safety_decisions")))
    check("the rows survive the restart", before == after and before[0] >= 1 and before[1] >= 1, f"{before} {after}")
    unsynced = sqlite3.connect(path).execute("SELECT COUNT(*) FROM safety_decisions WHERE synced_at IS NULL").fetchone()[0]
    check("they are still marked as not yet copied to Postgres, so the sync picks them up", unsynced == after[1] and unsynced >= 1, unsynced)
    far = datetime.now(timezone.utc) + timedelta(days=400)
    gone = purge_local(dbe2, synced_days=7, unsynced_days=None, now=far)
    check("retention with a service key never deletes an unsynced row, however old", gone == {"safety_events": 0, "safety_decisions": 0}
          and len(read(path, "safety_decisions")) == after[1], str(gone))
    gone = purge_local(dbe2, synced_days=7, unsynced_days=30, now=far)
    check("with nowhere to sync to (standalone) the same rows go after the standalone limit", gone["safety_decisions"] == after[1], str(gone))
    dbe2.dispose()
    # ── E. the master switch ─────────────────────────────────────────────────
    print("E. SAFETY_ENABLED=false")
    from app.safety.engine import engine_for_session
    from app.safety.session import safety_session_for
    saved = os.environ.get("SAFETY_ENABLED")
    try:
        os.environ["SAFETY_ENABLED"] = "false"
        check("with the switch off no engine is built, so the pipeline runs without any safety code", engine_for_session(fps=10.0) is None)
        check("with the switch off no session is built", safety_session_for(fps=10.0, camera_id="cam1", org_id=ORG, zones_config=ss.ZONES) is None)
        os.environ["SAFETY_ENABLED"] = "true"
        check("with the switch on an engine is built", engine_for_session(fps=10.0) is not None)
    finally:
        if saved is None:
            os.environ.pop("SAFETY_ENABLED", None)
        else:
            os.environ["SAFETY_ENABLED"] = saved
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
