"""
The safety layer's time base (Step 2).

What has to be true:
  * a frame's time is where it sits in the VIDEO, so the same clip gives the
    same times at any processing speed, on any machine, run after run;
  * `recorded_at` puts events on the real timeline (and is validated);
  * a live source's clock only ever goes forwards;
  * a seek can never corrupt a decision: open state is closed, replayed footage
    raises nothing twice, and no duration goes negative;
  * with SAFETY_ENABLED off the pipeline payload is exactly what it was.

Sections 1-8 are pure logic. Section 9 drives the REAL processing socket with
the real pose model on a short clip (slow: it loads the model three times), to
prove the wiring and not just the parts. It writes a few probe rows to the
local SQLite file under a throwaway organisation id and removes them.

Run:  venv/Scripts/python.exe backend/tests/test_safety_clock.py
"""

import io
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)           # the server runs from backend/, where the model weights are

from app.safety.clock import FrameClock, parse_recorded_at, utc_iso     # noqa: E402
from app.safety.engine import SafetyEngine, engine_for_session           # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


class FakeTime:
    """An injectable pair of clocks that only moves when told to."""

    def __init__(self, wall=1_700_000_000.0, mono=1000.0):
        self.w, self.m = wall, mono

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def advance(self, seconds):
        self.w += seconds
        self.m += seconds


UTC = timezone.utc

# ── 1. A recorded file: time is a function of the frame index ────────────────
print("1. File clock")
c = FrameClock(fps=25.0, recorded_at="2026-11-08T09:30:00+05:30")
check("frame 0 is t=0", c.tick(0)[0] == 0.0)
check("frame 50 at 25 fps is t=2.0", c.tick(50)[0] == 2.0)
check("observed_at = recorded_at + video time, in UTC",
      c.tick(50)[1] == "2026-11-08T04:00:02.000+00:00", c.tick(50)[1])
check("observed_at is millisecond ISO-8601 UTC",
      c.tick(1)[1] == "2026-11-08T04:00:00.040+00:00", c.tick(1)[1])
check("a non-terminating fraction rounds to the nearest ms (1/30 s = 33 ms)",
      FrameClock(fps=30.0, recorded_at="2026-11-08T00:00:00Z").tick(1)[1].endswith(":00.033+00:00"))
check("frame index accepts numpy-style integers",
      c.tick(type("I", (int,), {})(25))[0] == 1.0)
check("an integral float index is accepted", c.tick(25.0)[0] == 1.0)
check("time_at does not advance anything", c.time_at(100) == 4.0 and c.generation == 0)
check("tick is idempotent for the same frame", c.tick(77) == c.tick(77))

# ── 2. Processing speed does not matter ──────────────────────────────────────
print("2. Same footage, different processing speeds")
def run(speed_seed, recorded_at):
    ft = FakeTime(wall=1_700_000_000.0 + speed_seed * 777)     # a different "now" per run
    rnd = random.Random(speed_seed)
    clk = FrameClock(fps=29.97, recorded_at=recorded_at, wall=ft.wall, mono=ft.mono)
    out = []
    for idx in range(0, 600):
        ft.advance(rnd.uniform(0.0005, 0.5))                   # slow or fast machine
        out.append(clk.tick(idx))
    return out

a = run(1, "2026-11-08T09:30:00+05:30")
b = run(2, "2026-11-08T09:30:00+05:30")
check("with recorded_at, two runs at different speeds are identical", a == b)
a2, b2 = run(3, None), run(4, None)
check("video times are identical with no recorded_at",
      [x[0] for x in a2] == [x[0] for x in b2])
def offsets(seq):
    t0 = datetime.fromisoformat(seq[0][1])
    return [round((datetime.fromisoformat(x[1]) - t0).total_seconds(), 3) for x in seq]
check("with no recorded_at, every OFFSET is identical across runs", offsets(a2) == offsets(b2))
check("with no recorded_at the date is the moment of upload (not 1970)",
      datetime.fromisoformat(a2[0][1]).year >= 2023)

# ── 3. recorded_at ───────────────────────────────────────────────────────────
print("3. recorded_at parsing")
check("offset is converted to UTC",
      parse_recorded_at("2026-11-08T09:30:00+05:30") == datetime(2026, 11, 8, 4, 0, tzinfo=UTC))
check("Z is accepted", parse_recorded_at("2026-11-08T04:00:00Z") == datetime(2026, 11, 8, 4, 0, tzinfo=UTC))
check("a value with no timezone is read as UTC",
      parse_recorded_at("2026-11-08T04:00:00") == datetime(2026, 11, 8, 4, 0, tzinfo=UTC))
check("a datetime object is accepted", parse_recorded_at(datetime(2026, 11, 8, 4, 0, tzinfo=UTC)) is not None)
check("None is 'not given'", parse_recorded_at(None) is None)
check("blank is 'not given'", parse_recorded_at("   ") is None and parse_recorded_at("") is None)
check("garbage is rejected", raises(lambda: parse_recorded_at("yesterday")))
check("a wrong-type value is rejected", raises(lambda: parse_recorded_at(12345)))
check("before 2000 is rejected", raises(lambda: parse_recorded_at("1999-12-31T00:00:00Z")))
check("more than a day in the future is rejected",
      raises(lambda: parse_recorded_at("2099-01-01T00:00:00Z")))
now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
check("a few hours ahead (camera clock skew) is tolerated",
      parse_recorded_at("2026-11-08T15:00:00Z", now=now) is not None)
check("25 hours ahead is rejected",
      raises(lambda: parse_recorded_at("2026-11-09T13:00:00Z", now=now)))
try:
    parse_recorded_at("nope")
except ValueError as e:
    check("the error message says what to send", "ISO-8601" in str(e) and "2026" in str(e), str(e))
check("a value too large to express in UTC is a ValueError, not an OverflowError",
      raises(lambda: parse_recorded_at("9999-12-31T23:59:59-05:00")))
check("a value too small to express in UTC is a ValueError, not an OverflowError",
      raises(lambda: parse_recorded_at("0001-01-01T00:00:00+05:00")))
check("a lowercase z is accepted too", parse_recorded_at("2026-11-08T04:00:00z") == datetime(2026, 11, 8, 4, 0, tzinfo=UTC))
check("fractional seconds are kept to the millisecond",
      FrameClock(fps=25.0, recorded_at="2026-11-08T04:00:00.250Z").tick(0)[1] == "2026-11-08T04:00:00.250+00:00")
check("utc_iso rejects a naive datetime", raises(lambda: utc_iso(datetime(2026, 1, 1))))

# ── 4. A live source ─────────────────────────────────────────────────────────
print("4. Live clock")
ft = FakeTime()
live = FrameClock(live=True, wall=ft.wall, mono=ft.mono)
t0 = live.tick()
check("starts at 0", t0[0] == 0.0)
ft.advance(1.25)
check("advances with the monotonic clock", abs(live.tick()[0] - 1.25) < 1e-9)
ft.m -= 5.0                               # a misbehaving clock steps backwards
check("never goes backwards", live.tick()[0] >= 1.25)
before = live.tick()[0]
ft.w -= 3600                              # the WALL clock is changed by an hour (NTP, DST)
check("a wall-clock change does not touch video time", live.tick()[0] >= before)
check("observed_at follows the wall clock",
      live.tick()[1] == utc_iso(datetime.fromtimestamp(ft.w, UTC)))
check("the frame index is ignored", live.tick(123456)[0] >= before)
check("a live clock refuses a recorded_at", raises(lambda: FrameClock(live=True, recorded_at="2026-11-08T00:00:00Z")))
check("a live clock cannot be asked about arbitrary frames", raises(lambda: live.time_at(5)))
seq = []
for _ in range(500):
    ft.advance(random.random() * 0.1)
    seq.append(live.tick()[0])
check("500 ticks are non-decreasing", all(y >= x for x, y in zip(seq, seq[1:])))

# ── 5. Bad input is refused, not guessed at ──────────────────────────────────
print("5. Validation")
for bad in (None, 0, -25, float("nan"), float("inf"), "25", True):
    check(f"fps={bad!r} is refused", raises(lambda b=bad: FrameClock(fps=b)))
for bad in (0.001, 0.009, 1001, 1e9, 1e-9, 1e-300):
    check(f"fps={bad!r} is outside the sane range and is refused at start", raises(lambda b=bad: FrameClock(fps=b)))
for ok_fps in (0.01, 1, 23.976, 29.97, 30000 / 1001, 59.94, 1000):
    check(f"fps={ok_fps!r} is accepted", FrameClock(fps=ok_fps).tick(7)[0] > 0)
file_clock = FrameClock(fps=25.0, recorded_at="2026-11-08T04:00:00Z")
for bad in (None, -1, 2.5, float("nan"), "7", True):
    check(f"frame_index={bad!r} is refused", raises(lambda b=bad: file_clock.tick(b)))

# ── 6. The engine: frames, replay, resets ────────────────────────────────────
print("6. Engine and seeking")


class Visit:
    """
    A stand-in for the zone-event / dwell machinery later steps will add. It
    records every way the contract could be broken so a test can fail on it:
    a negative duration, and an emission made on a replayed frame.
    """

    def __init__(self, limit_s, honour_replay=True):
        self.limit = limit_s
        self.honour_replay = honour_replay
        self.entered = None
        self.fired = False
        self.decisions = []
        self.negative = []

    def update(self, ft, present):
        if not present:
            self.entered, self.fired = None, False
            return
        if self.entered is None:
            self.entered = ft.video_time_s
        dwell = ft.video_time_s - self.entered
        if dwell < 0:
            self.negative.append(dwell)
        if dwell >= self.limit and not self.fired:
            self.fired = True
            if not (self.honour_replay and ft.replay):
                self.decisions.append(("DWELL_EXCEEDED", round(ft.video_time_s, 3)))

    def reset(self, reason, last_seen_s, resume_at_s):
        out = []
        if self.entered is not None:
            duration = last_seen_s - self.entered
            if duration < 0:
                self.negative.append(duration)
            out.append(("UNVERIFIABLE", reason, round(duration, 3), round(last_seen_s, 3)))
        self.entered, self.fired = None, False
        return out


FPS = 10.0


def play(eng, comp, first, last, present=lambda t: True):
    """Feed frames first..last (inclusive); returns the FrameTimes."""
    out = []
    for idx in range(first, last + 1):
        ft = eng.begin_frame(idx)
        out.append(ft)
        comp.update(ft, present(ft.video_time_s))
    return out


def make(honour=True, limit=5.0):
    e = SafetyEngine(FrameClock(fps=FPS, recorded_at="2026-11-08T04:00:00Z"))
    v = Visit(limit_s=limit, honour_replay=honour)
    e.register(v)
    return e, v


# A. The headline scenario: a worker enters at 25 s, the operator rewinds to
#    20 s after watching to 33 s, and the footage plays again.
def scenario_a(honour=True, engine_cls=SafetyEngine):
    e = engine_cls(FrameClock(fps=FPS, recorded_at="2026-11-08T04:00:00Z"))
    v = Visit(limit_s=5.0, honour_replay=honour)
    e.register(v)
    enter = lambda t: t >= 25.0
    play(e, v, 0, 330, enter)                       # 0 .. 33.0 s; limit crossed at 30.0
    first_pass = list(v.decisions)
    e.reset("SEEK", frame_index=200)                # rewind 13 s, to 20.0 s
    again = play(e, v, 200, 360, enter)             # replays 20..33, then 3 s of new footage
    return e, v, first_pass, again


e, v, first_pass, again = scenario_a()
check("first pass: exactly one decision, at the 5 s dwell limit (30.0 s)",
      first_pass == [("DWELL_EXCEEDED", 30.0)], str(first_pass))
check("seek back: the visit is closed as UNVERIFIABLE at the last moment observed (33.0 s), 8.0 s long",
      again[0].closed == (("UNVERIFIABLE", "SEEK", 8.0, 33.0),), str(again[0].closed))
check("seek back: no duration is ever negative", v.negative == [], str(v.negative))
check("seek back: the replayed stretch crossed the limit again but raised nothing",
      v.decisions == first_pass, str(v.decisions))
check("every frame of the rewound stretch is a replay",
      all(f.replay for f in again if f.video_time_s <= 33.0 + 1e-9))
check("the first frame past the old high-water mark is new",
      [f.replay for f in again if abs(f.video_time_s - 33.1) < 1e-6] == [False])
check("the clock generation moved on", e.clock.generation == 1)

# The test must be able to FAIL: a component that ignores `replay` duplicates...
_, v_bad, first_bad, _ = scenario_a(honour=False)
check("(guard) a component that emits on replayed frames IS caught: two decisions",
      len(v_bad.decisions) == 2, str(v_bad.decisions))


# ...and an engine that closes at the NEW position goes negative.
class WrongEngine(SafetyEngine):
    def _do_reset(self, reason, resume_at_s):
        for comp in self.components:
            self._pending_closed.extend(comp.reset(reason, resume_at_s, resume_at_s) or [])
        self.clock.restart()
        self._last_video_time = None


_, v_wrong, _, _ = scenario_a(engine_cls=WrongEngine)
check("(guard) closing at the position jumped TO is caught: negative duration",
      v_wrong.negative == [-5.0], str(v_wrong.negative))

# B. Forward seek, then back to footage that was SKIPPED (never processed).
e, v = make()
play(e, v, 0, 30)                                   # 0 .. 3.0 s
e.reset("SEEK", frame_index=900)                    # forward to 90 s
f90 = play(e, v, 900, 930)                          # 90 .. 93 s
check("a forward seek lands on new footage", not any(f.replay for f in f90))
e.reset("SEEK", frame_index=400)                    # back to 40 s: NEVER processed
f40 = play(e, v, 400, 410)
check("skipped footage visited for the first time is NOT a replay (a violation there must be raised)",
      not any(f.replay for f in f40), str([f.replay for f in f40]))
e.reset("SEEK", frame_index=20)                     # back to 2.0 s: inside 0..3.0, processed
f2 = play(e, v, 20, 30)
check("footage inside a stretch already processed IS a replay", all(f.replay for f in f2))
f3 = play(e, v, 31, 33)
check("...and the first frame past that stretch is new", not any(f.replay for f in f3))
e.reset("SEEK", frame_index=905)                    # back into 90.5 s, processed
check("a stretch processed earlier in the session is still a replay after other seeks",
      all(f.replay for f in play(e, v, 905, 930)))

# C. A backwards step nobody announced.
e, v = make(limit=1000.0)
play(e, v, 0, 99)                                   # 0 .. 9.9 s, present throughout
ft = e.begin_frame(10)                              # time 1.0 s: went backwards
check("an unannounced backwards step triggers a TIME_REGRESSED reset",
      e.resets[-1]["reason"] == "TIME_REGRESSED")
check("...the closing record rides on that very frame, stamped at the last observed time (9.9 s)",
      ft.closed == (("UNVERIFIABLE", "TIME_REGRESSED", 9.9, 9.9),), str(ft.closed))
check("...and that frame is a replay", ft.replay is True)
check("...with no negative duration", v.negative == [], str(v.negative))
check("the same index twice in a row is a replay, not a reset",
      (lambda x: (x.begin_frame(5), x.begin_frame(5))[1].replay and not x.resets)(SafetyEngine(FrameClock(fps=FPS))))

# D. Records are never lost: a seek with no further frames.
e, v = make(limit=1000.0)
play(e, v, 0, 50)
e.reset("SEEK", frame_index=0)
drained = e.drain()
check("drain() returns records no frame delivered", drained == [("UNVERIFIABLE", "SEEK", 5.0, 5.0)], str(drained))
check("a second drain is empty", e.drain() == [])
check("and the next frame has nothing left to deliver", e.begin_frame(0).closed == ())

# E. Two seeks before any frame arrives.
e, v = make(limit=1000.0)
play(e, v, 0, 50)
e.reset("SEEK", frame_index=100)
e.reset("SEEK", frame_index=200)
first_frame = e.begin_frame(200)
check("a second seek before a frame closes nothing more",
      first_frame.closed == (("UNVERIFIABLE", "SEEK", 5.0, 5.0),), str(first_frame.closed))

# F. Bookkeeping stays small.
e = SafetyEngine(FrameClock(fps=FPS))
for i in range(20000):
    e.begin_frame(i)
check("a straight play-through is ONE processed stretch however long it runs", len(e._processed) == 1)
e.reset("SEEK", frame_index=5000)
for i in range(5000, 5100):
    assert e.begin_frame(i).replay
check("replaying inside it adds no stretches", len(e._processed) == 1)

# G. A reset before any frame.
e, v = make()
e.reset("SEEK", frame_index=100)
check("a reset before any frame is harmless", e.drain() == [] and e.clock.generation == 1)

# Components that misbehave must not break the video.
class Broken:
    def reset(self, reason, last_seen_s, resume_at_s):
        raise RuntimeError("boom")


class Silent:
    def reset(self, reason, last_seen_s, resume_at_s):
        return None


e = SafetyEngine(FrameClock(fps=FPS))
good = Visit(limit_s=1000.0)
e.register(Broken()); e.register(Silent()); e.register(good)
play(e, good, 0, 50)
e.reset("SEEK", frame_index=100)
check("a failing component does not stop the reset or hide the others' records",
      e.drain() == [("UNVERIFIABLE", "SEEK", 5.0, 5.0)])
check("...and the clock still restarted", e.clock.generation == 1)
check("reset history is kept", e.resets[-1]["reason"] == "SEEK" and e.resets[-1]["last_seen_s"] == 5.0)

# live engine: never a replay, never regresses
ftm = FakeTime()
le = SafetyEngine(FrameClock(live=True, wall=ftm.wall, mono=ftm.mono))
flags = []
for _ in range(50):
    ftm.advance(0.0)                         # the same instant, repeatedly
    flags.append(le.begin_frame().replay)
check("a live source is never a replay, even at the same instant", not any(flags))
check("a live engine has no resets without being asked", not le.resets)
le.reset("STOP")
check("a live reset needs no frame index", le.drain() == [] and le.clock.generation == 1)

# ── 7. The payload ───────────────────────────────────────────────────────────
print("7. Payload")
fe = SafetyEngine(FrameClock(fps=25.0, recorded_at="2026-11-08T04:00:00Z"))
p = fe.begin_frame(50).as_payload()
check("payload carries exactly the public fields",
      set(p) == {"video_time_s", "observed_at", "replay"}, str(p))
check("payload values", p == {"video_time_s": 2.0, "observed_at": "2026-11-08T04:00:02.000+00:00", "replay": False}, str(p))
check("payload is JSON-serialisable", bool(json.dumps(p)))

# ── 8. The switch ────────────────────────────────────────────────────────────
print("8. SAFETY_ENABLED")
saved = os.environ.pop("SAFETY_ENABLED", None)
try:
    check("off by default: no engine, nothing built", engine_for_session(fps=25.0) is None)
    os.environ["SAFETY_ENABLED"] = "false"
    check("explicitly off: no engine", engine_for_session(live=True) is None)
    os.environ["SAFETY_ENABLED"] = "true"
    e = engine_for_session(fps=25.0, recorded_at="2026-11-08T04:00:00Z")
    check("on: a file engine", isinstance(e, SafetyEngine) and not e.clock.live)
    check("on: a live engine", engine_for_session(live=True).clock.live)
    check("on: a bad recorded_at is refused at construction",
          raises(lambda: engine_for_session(fps=25.0, recorded_at="2099-01-01T00:00:00Z")))
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    if saved is not None:
        os.environ["SAFETY_ENABLED"] = saved

# ── 9. The real socket ───────────────────────────────────────────────────────
print("9. The real processing socket (loads the pose model; slow)")

from fastapi.testclient import TestClient                                # noqa: E402
from sqlalchemy import text                                              # noqa: E402

from app.main import app                                                 # noqa: E402
from app.api.routers import video_upload                                 # noqa: E402
from app.db.database import SessionLocal                                 # noqa: E402

PROBE_ORG = "00000000-0000-0000-0000-00000000bbbb"
CLIP = os.path.join(ROOT, "sample_videos", "vtest.avi")
RECORDED_AT = "2026-11-08T09:30:00+05:30"

async def _fake_org(_websocket):
    return PROBE_ORG, "ADMIN"

video_upload._resolve_session_org = _fake_org
app.dependency_overrides[video_upload.require_analysis_run] = lambda: PROBE_ORG
client = TestClient(app)


def cleanup_rows():
    s = SessionLocal()
    try:
        for table in ("activity_logs", "zone_minute_stats"):
            try:
                s.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": PROBE_ORG})
            except Exception:
                s.rollback()
        s.commit()
    finally:
        s.close()


def upload(recorded_at=None, data=None):
    body = open(CLIP, "rb").read()
    form = {} if recorded_at is None else {"recorded_at": recorded_at}
    return client.post("/api/v1/video/upload",
                       files={"file": ("probe_clip.avi", io.BytesIO(body), "video/x-msvideo")},
                       data=form)


def frames_from(ws, want, stop_when=None):
    out, init = [], None
    while len(out) < want:
        msg = ws.receive_json()
        if msg.get("type") == "INIT":
            init = msg
        elif msg.get("type") == "FRAME":
            out.append(msg)
            if stop_when and stop_when(msg):
                break
        elif msg.get("type") in ("COMPLETE", "ERROR") or "error" in msg:
            break
    return init, out


try:
    if not os.path.exists(CLIP):
        raise FileNotFoundError(CLIP)

    # -- upload validation ----------------------------------------------------
    before = set(os.listdir(video_upload.SAMPLE_VIDEOS_DIR))
    bad = upload("not-a-date")
    check("upload: a bad recorded_at is a 422", bad.status_code == 422, str(bad.status_code))
    check("upload: ...with a message", "ISO-8601" in bad.json().get("detail", ""), bad.text[:120])
    check("upload: ...and nothing was written to disk",
          set(os.listdir(video_upload.SAMPLE_VIDEOS_DIR)) == before)
    check("upload: a future recorded_at is a 422", upload("2099-01-01T00:00:00Z").status_code == 422)
    check("upload: an out-of-range recorded_at is a 422, not a 500",
          upload("9999-12-31T23:59:59-05:00").status_code == 422)
    check("upload: still nothing written", set(os.listdir(video_upload.SAMPLE_VIDEOS_DIR)) == before)

    good = upload(RECORDED_AT)
    check("upload: a valid recorded_at is accepted", good.status_code == 200, str(good.status_code))
    sid_a = good.json()["session_id"]
    check("upload: it is stored on the session as UTC",
          video_upload._active_sessions[sid_a]["recorded_at"] == "2026-11-08T04:00:00+00:00",
          str(video_upload._active_sessions[sid_a]["recorded_at"]))
    blank = upload("")
    check("upload: a blank recorded_at means 'not given'",
          blank.status_code == 200 and video_upload._active_sessions[blank.json()["session_id"]]["recorded_at"] is None)
    video_upload._active_sessions.pop(blank.json()["session_id"], None)
    omitted = upload(None)
    check("upload: omitting it is fine",
          omitted.status_code == 200 and video_upload._active_sessions[omitted.json()["session_id"]]["recorded_at"] is None)

    # -- safety OFF: the payload is exactly what it was ----------------------
    os.environ.pop("SAFETY_ENABLED", None)
    with client.websocket_connect(f"/api/v1/video/process/{omitted.json()['session_id']}?realtime=false") as ws:
        init, frames = frames_from(ws, 3)
    check("socket (safety off): frames arrive", len(frames) == 3)
    check("socket (safety off): no 'safety' key in any payload",
          all("safety" not in f for f in frames))

    # -- safety ON ---------------------------------------------------------------
    os.environ["SAFETY_ENABLED"] = "true"

    def one_run(sid, seek_to=None, want=18):
        with client.websocket_connect(f"/api/v1/video/process/{sid}?realtime=false") as ws:
            init, frames = frames_from(ws, want)
            extra = []
            if seek_to is not None:
                ws.send_text(json.dumps({"action": "seek", "frame": seek_to}))
                # read until the seek has taken effect and the replay has finished
                high = max(f["frame_index"] for f in frames)
                seen_back = False
                for _ in range(200):
                    msg = ws.receive_json()
                    if msg.get("type") != "FRAME":
                        break
                    extra.append(msg)
                    if msg["frame_index"] < high:
                        seen_back = True
                    if seen_back and not msg["safety"]["replay"]:
                        break
            return init, frames, extra

    init_a, frames_a, _ = one_run(sid_a)
    fps = init_a["source_fps"]
    check("socket: every frame carries the clock", all("safety" in f for f in frames_a))
    check("socket: video time = (frame_index - 1) / fps, so the first frame is t=0",
          all(abs(f["safety"]["video_time_s"] - round((f["frame_index"] - 1) / fps, 3)) < 1e-9 for f in frames_a),
          str([(f["frame_index"], f["safety"]["video_time_s"]) for f in frames_a[:3]]))
    check("socket: the first frame is t=0", frames_a[0]["safety"]["video_time_s"] == 0.0)
    anchor = datetime(2026, 11, 8, 4, 0, tzinfo=UTC)
    ok = True
    for f in frames_a:
        expect = utc_iso(anchor + timedelta(milliseconds=round(((f["frame_index"] - 1) / fps) * 1000)))
        ok = ok and f["safety"]["observed_at"] == expect
    check("socket: observed_at = the uploaded recorded_at + video time", ok,
          frames_a[-1]["safety"]["observed_at"])
    check("socket: no frame is a replay on a straight play-through",
          not any(f["safety"]["replay"] for f in frames_a))

    # same clip, same recorded_at, a separate session (a different processing speed in practice)
    sid_b = upload(RECORDED_AT).json()["session_id"]
    init_b, frames_b, _ = one_run(sid_b)
    sig = lambda fs: [(f["frame_index"], f["safety"]["video_time_s"], f["safety"]["observed_at"]) for f in fs]
    check("socket: a second run of the clip gives identical timestamps", sig(frames_a) == sig(frames_b))

    # seek back
    sid_c = upload(RECORDED_AT).json()["session_id"]
    init_c, before_seek, after_seek = one_run(sid_c, seek_to=5, want=18)
    # A few frames were already in flight when the seek was sent. The jump is
    # the first frame whose index is lower than its predecessor's.
    seq = [f["frame_index"] for f in before_seek + after_seek]
    jump = next((i for i in range(1, len(seq)) if seq[i] < seq[i - 1]), None)
    check("seek: the stream really went backwards", jump is not None, str(seq))
    pre = (before_seek + after_seek)[:jump]
    post = (before_seek + after_seek)[jump:]
    high = max(f["frame_index"] for f in pre)             # everything processed before the jump
    check("seek: the first frame after the jump is the requested frame (t = 5 / fps)",
          abs(post[0]["safety"]["video_time_s"] - 5 / fps) < 1e-3,
          str(post[0]["safety"]["video_time_s"]))
    replayed = [f for f in post if f["frame_index"] <= high]
    check("seek: every re-played frame is flagged replay",
          bool(replayed) and all(f["safety"]["replay"] for f in replayed),
          str([(f["frame_index"], f["safety"]["replay"]) for f in post[:4]]))
    fresh = [f for f in post if f["frame_index"] > high]
    check("seek: the first frame past the old high-water mark is new again",
          bool(fresh) and fresh[0]["frame_index"] == high + 1 and not fresh[0]["safety"]["replay"],
          str([(f["frame_index"], f["safety"]["replay"]) for f in post[-3:]]))
    times = [f["safety"]["video_time_s"] for f in post]
    check("seek: after the jump, video time only moves forwards",
          all(y > x for x, y in zip(times, times[1:])))
    check("seek: the frames before the jump were all fresh", not any(f["safety"]["replay"] for f in pre))

    # the live sockets build a live engine (no camera needed: check the factory they call)
    e = engine_for_session(live=True)
    check("live sockets: the factory they call returns a live engine", e is not None and e.clock.live)
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    app.dependency_overrides.clear()
    for sid in list(video_upload._active_sessions):
        meta = video_upload._active_sessions.get(sid, {})
        if meta.get("org_id") == PROBE_ORG:
            try:
                if os.path.exists(meta.get("path", "")):
                    os.remove(meta["path"])
            except OSError:
                pass
            video_upload._active_sessions.pop(sid, None)
    for name in os.listdir(video_upload.SAMPLE_VIDEOS_DIR):
        if name.endswith("_probe_clip.avi"):
            try:
                os.remove(os.path.join(video_upload.SAMPLE_VIDEOS_DIR, name))
            except OSError:
                pass
    cleanup_rows()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("SAFETY CLOCK: ALL CHECKS PASSED")
