"""
Zone events through the REAL processing socket (Step 4, end to end).

Uploads the sample clip, draws a RESTRICTED zone over it, plays part of it with
the real pose model, drops the connection, and reads the rows the pipeline wrote
to the real local database. Then the same with a GENERAL zone, which must write
nothing, and with safety switched off, which must change nothing.

Slow (loads the pose model, runs frames on CPU). Run:
    venv/Scripts/python.exe backend/tests/test_zone_events_socket.py
"""

import io
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient                            # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.main import app                                             # noqa: E402
from app.api.routers import video_upload                             # noqa: E402
from app.db.database import SessionLocal, engine as DB_ENGINE        # noqa: E402
from app.db.models import ZoneModel                                  # noqa: E402

import logging
if os.environ.get('ZE_LOG'):
    logging.basicConfig(level=logging.INFO)

FAILS = []
PROBE_ORG = "00000000-0000-0000-0000-00000000cccc"
CLIP = os.path.join(ROOT, "sample_videos", "vtest.avi")
RECORDED_AT = "2026-11-08T09:30:00+05:30"       # = 04:00:00 UTC
FRAMES = int(os.environ.get("ZE_FRAMES", "230"))


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


async def _fake_org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _fake_org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG
client = TestClient(app)


def upload(recorded_at=RECORDED_AT, camera_id=None):
    body = open(CLIP, "rb").read()
    form = {"recorded_at": recorded_at}
    if camera_id:
        form["camera_id"] = camera_id
    r = client.post("/api/v1/video/upload",
                    files={"file": ("zone_probe.avi", io.BytesIO(body), "video/x-msvideo")},
                    data=form)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def add_zone(camera_id, zone_type):
    s = SessionLocal()
    try:
        zid = f"probe-{uuid.uuid4().hex[:8]}"
        s.add(ZoneModel(zone_id=zid, org_id=PROBE_ORG, camera_id=camera_id, zone_name="Probe",
                        zone_type=zone_type,
                        polygon_coordinates=[[0, 0], [3000, 0], [3000, 3000], [0, 3000]]))
        s.commit()
        return zid
    finally:
        s.close()


def play(sid, frames=FRAMES):
    """Play `frames` frames, then stop the session the way the app does (cancel) and wait for it to finish."""
    got = []
    with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false") as ws:
        while len(got) < frames:
            msg = ws.receive_json()
            if msg.get("type") == "FRAME":
                got.append(msg)
            elif msg.get("type") in ("COMPLETE", "ERROR") or "error" in msg:
                return got
        video_upload._active_sessions[sid]["cancel"] = True
        for _ in range(50):                 # frames already in flight, then the COMPLETE
            msg = ws.receive_json()
            if msg.get("type") == "FRAME":
                got.append(msg)
            elif msg.get("type") in ("COMPLETE", "ERROR") or "error" in msg:
                break
    return got


def events_for(sid, wait_s=20.0):
    """Rows for a session; waits for the server side to notice the disconnect and flush."""
    deadline = time.time() + wait_s
    rows = []
    while time.time() < deadline:
        c = sqlite3.connect(DB_ENGINE.url.database)
        try:
            rows = c.execute(
                "SELECT event_type, worker_key, zone_id, video_time_s, observed_at, details, org_id, camera_id "
                "FROM safety_events WHERE session_key = ? ORDER BY id", (sid,)).fetchall()
        finally:
            c.close()
        if rows and rows[-1][0] == "EXITED_ZONE" and "SESSION_END" in (rows[-1][5] or ""):
            return rows
        time.sleep(0.5)
    return rows


def cleanup(sids, zone_ids):
    s = SessionLocal()
    try:
        for sid in sids:
            s.execute(text("DELETE FROM safety_events WHERE session_key = :s"), {"s": sid})
        for zid in zone_ids:
            s.execute(text("DELETE FROM zones WHERE zone_id = :z"), {"z": zid})
        for table in ("activity_logs", "zone_minute_stats"):
            try:
                s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
            except Exception:
                s.rollback()
        s.commit()
    finally:
        s.close()


sids, zones = [], []
try:
    if not os.path.exists(CLIP):
        raise FileNotFoundError(CLIP)

    # -- RESTRICTED zone over the whole frame ----------------------------------
    print("1. A RESTRICTED zone: people in the clip produce events")
    os.environ["SAFETY_ENABLED"] = "true"
    sid = upload(); sids.append(sid)
    zones.append(add_zone(f"upload_{sid}", "RESTRICTED"))
    frames = play(sid)
    print("   people per frame:", "".join(str(min(f["total_detected"], 9)) for f in frames))
    check("frames arrive", FRAMES <= len(frames) <= FRAMES + 20, str(len(frames)))
    check("people were detected", any(f["total_detected"] for f in frames))
    seen_events = [e for f in frames for e in f.get("safety", {}).get("events", [])]
    if not seen_events:                      # diagnostics for a failing run
        mid = frames[len(frames) // 2]
        print("   occupancy:", mid["zone_occupancy"], "| entities:",
              [(e["track_id"], e["zone_id"], e["bbox"]) for e in mid["tracked_entities"]][:3],
              "| zone id drawn:", zones[0])
    check("events were announced in the frame payloads", len(seen_events) > 0, str(seen_events[:2]))

    rows = events_for(sid)
    check("rows were written to the real safety_events table", len(rows) > 0)
    first_seen = {}
    for f in frames:
        for e in f["tracked_entities"]:
            first_seen.setdefault(f"trk-{e['track_id']}", f["safety"]["video_time_s"])
    check("an ENTERED_ZONE is flagged first_sighting exactly when the worker's very first frame was already inside the zone",
          all(bool(json.loads(r[5] or "{}").get("first_sighting")) == (abs(r[3] - first_seen[r[1]]) < 0.06)
              for r in rows if r[0] == "ENTERED_ZONE"),
          str([(r[1], r[3], first_seen.get(r[1])) for r in rows if r[0] == "ENTERED_ZONE"][:4]))
    check("worker keys are anonymous track keys", all(r[1].startswith("trk-") for r in rows))
    check("org and camera are on the row", all(r[6] == PROBE_ORG and r[7] == f"upload_{sid}" for r in rows))
    check("the zone is the one drawn", all(r[2] == zones[0] for r in rows))
    check("anyone still in the zone when the session ended was closed (SESSION_END, unverifiable)",
          any(r[0] == "EXITED_ZONE" and json.loads(r[5]).get("reason") == "SESSION_END"
              and json.loads(r[5]).get("unverifiable") for r in rows), str(rows[-2:]))
    fps = None
    anchor = datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
    ok = True
    for r in rows:
        want = (anchor + timedelta(seconds=r[3])).isoformat(timespec="milliseconds")
        ok = ok and r[4] == want
    check("observed_at = recorded_at + video time, on every row", ok, str([(r[3], r[4]) for r in rows[:2]]))
    check("event times lie inside what was played", all(0 <= r[3] <= max(f["safety"]["video_time_s"] for f in frames) + 1e-6 for r in rows))
    per_worker = {}
    for r in rows:
        per_worker.setdefault(r[1], []).append(r[0])
    check("per worker: ENTERED always comes before EXITED and the sequence alternates",
          all(all(a != b for a, b in zip(seq, seq[1:])) and seq[0] == "ENTERED_ZONE" for seq in per_worker.values()), str(per_worker))

    # -- GENERAL zone ------------------------------------------------------------
    print("2. A GENERAL zone writes nothing")
    sid2 = upload(); sids.append(sid2)
    zones.append(add_zone(f"upload_{sid2}", "GENERAL"))
    frames2 = play(sid2, 40)
    time.sleep(3)
    rows2 = events_for(sid2, wait_s=4.0)
    check("people were detected", any(f["total_detected"] for f in frames2))
    check("no events for a GENERAL zone", rows2 == [] and not any("events" in f["safety"] for f in frames2), str(rows2[:2]))

    # -- the camera the caller names, even if never registered -------------------
    print("3. Zones drawn for a named (unregistered) camera apply to an upload")
    named = f"probe_cam_{uuid.uuid4().hex[:6]}"
    sid4 = upload(camera_id=named); sids.append(sid4)
    zones.append(add_zone(named, "RESTRICTED"))
    frames4 = play(sid4)
    rows4 = events_for(sid4)
    check("events were written for the zone drawn under the caller's camera name", len(rows4) > 0, str(len(rows4)))
    check("they are stored under that camera and zone", all(r[7] == named and r[2] == zones[-1] for r in rows4))
    other = upload(camera_id=f"probe_cam_{uuid.uuid4().hex[:6]}"); sids.append(other)
    frames5 = play(other, 60)
    check("a camera name with no zones of its own does not borrow another's",
          not any("events" in f["safety"] for f in frames5))

    # -- safety off --------------------------------------------------------------
    print("4. Safety off changes nothing")
    os.environ.pop("SAFETY_ENABLED", None)
    sid3 = upload(); sids.append(sid3)
    zones.append(add_zone(f"upload_{sid3}", "RESTRICTED"))
    frames3 = play(sid3, 20)
    time.sleep(2)
    check("no 'safety' key in any payload", all("safety" not in f for f in frames3))
    check("no rows written", events_for(sid3, wait_s=3.0) == [])
    check("entities still carry a zone id (the centre-versus-feet rule itself is tested in test_zone_session.py)",
          all(isinstance(e.get("zone_id"), str) for f in frames3 for e in f["tracked_entities"]))
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    cleanup(sids, zones)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
