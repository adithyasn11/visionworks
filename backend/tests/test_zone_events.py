"""
Zone events (Step 4): from "in zone X this frame" to "entered / left / stayed".

The scenarios are walks played frame by frame at a chosen frame rate, so what is
checked is the behaviour over time, not the arithmetic of one call:

  * a straight walk through two zones: one ENTERED and one EXITED each, stamped
    when the change began, not when it was confirmed;
  * a person standing on a boundary with a wobbling detection: one entry;
  * a short occlusion changes nothing; a longer one is reported LOST and then
    REAPPEARED; one that never ends closes the visit with reason LOST;
  * two workers never affect each other;
  * dwell warning at 80 % and exceeded at 100 %, once each per visit;
  * the same events result at any frame rate and at any start time;
  * a jump in the timeline closes visits as unverifiable at the last moment
    observed.

Run:  venv/Scripts/python.exe backend/tests/test_zone_events.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.safety.config import SafetyConfig, load_config            # noqa: E402
from app.safety.engine import SafetyEngine                         # noqa: E402
from app.safety.clock import FrameClock                             # noqa: E402
from app.safety.zone_events import ZoneEventTracker, ZoneEvent      # noqa: E402

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


CFG = SafetyConfig()          # enter 0.7, exit 1.0, grace 5, ttl 8, gap 0.5


def play(tracker, frames, fps=10.0, start=0.0):
    """
    frames: one entry per frame, either a dict {worker: zone}, or None for a frame
    in which nobody is seen. A worker missing from the dict is unseen.
    Returns every event, in the order emitted.
    """
    out = []
    for i, f in enumerate(frames):
        t = start + i / fps
        out.extend(tracker.update(f or {}, None, t))
    return out


def seg(worker, zone, seconds, fps=10.0):
    return [{worker: zone} for _ in range(int(round(seconds * fps)))]


def types(events, worker=None):
    return [e.event_type for e in events if worker is None or e.worker_key == worker]


def kinds(events):
    return [(e.event_type, e.zone_id, round(e.video_time_s, 2)) for e in events]


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


W = "trk-1"

# ── 1. A straight walk through two zones ─────────────────────────────────────
print("1. Straight walk A -> outside -> B -> outside")
tr = ZoneEventTracker(CFG)
walk = (seg(W, None, 2) + seg(W, "A", 5) + seg(W, None, 3) + seg(W, "B", 4) + seg(W, None, 3))
ev = play(tr, walk)
check("exactly ENTERED A, EXITED A, ENTERED B, EXITED B",
      types(ev) == ["ENTERED_ZONE", "EXITED_ZONE", "ENTERED_ZONE", "EXITED_ZONE"], str(kinds(ev)))
check("entry is stamped when the walk into A began (2.0 s), not when it was confirmed",
      approx(ev[0].video_time_s, 2.0) and ev[0].zone_id == "A")
check("exit is stamped when they stepped out (7.0 s)", approx(ev[1].video_time_s, 7.0) and ev[1].zone_id == "A")
check("exit carries reason LEFT and the dwell",
      ev[1].details.get("reason") == "LEFT" and approx(ev[1].details.get("dwell_s"), 5.0), str(ev[1].details))
check("second zone entered at 10.0 and left at 14.0", approx(ev[2].video_time_s, 10.0) and approx(ev[3].video_time_s, 14.0), str(kinds(ev)))
check("nothing is flagged first_sighting for a worker who walked in", not any(e.details.get("first_sighting") for e in ev))
check("the worker key is carried through", all(e.worker_key == W for e in ev))
check("no open visit at the end", tr.open_visits() == {})

# ── 2. A -> B with no gap ────────────────────────────────────────────────────
print("2. Walking straight from A into B")
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, None, 1) + seg(W, "A", 3) + seg(W, "B", 3))
check("EXITED A and ENTERED B at the same moment (when the move began)",
      types(ev) == ["ENTERED_ZONE", "EXITED_ZONE", "ENTERED_ZONE"]
      and approx(ev[1].video_time_s, 4.0) and approx(ev[2].video_time_s, 4.0), str(kinds(ev)))
check("B is confirmed with the ENTER time (0.7 s), not the slower EXIT time",
      tr.zone_of(W) == "B")
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + seg(W, "B", 0.5) + seg(W, "A", 3))
check("a half-second dip into B and back changes nothing", types(ev) == ["ENTERED_ZONE"] and tr.zone_of(W) == "A", str(kinds(ev)))

# ── 3. Boundary jitter ───────────────────────────────────────────────────────
print("3. Standing on a boundary (S6)")
tr = ZoneEventTracker(CFG)
jitter = [{W: "A" if i % 2 == 0 else None} for i in range(80)]            # 8 s at 10 fps
ev = play(tr, seg(W, "A", 2) + jitter + seg(W, "A", 2))
check("walk in, then 8 s of single-frame flicker: exactly one ENTERED, no EXITED", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, jitter)
check("pure flicker from outside never produces an entry", ev == [], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
irregular = [{W: "A"}] * 3 + [{W: None}] * 2 + [{W: "A"}] * 4 + [{W: None}] + [{W: "A"}] * 20
ev = play(tr, seg(W, "A", 1) + irregular * 3)
check("irregular wobble still gives a single entry and no exit", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 2) + seg(W, None, 0.9) + seg(W, "A", 2))
check("stepping out for 0.9 s (under the 1.0 s exit confirm) and back is not an exit", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 2) + seg(W, None, 1.1) + seg(W, "A", 2))
check("stepping out for 1.1 s is an exit and a fresh entry", types(ev) == ["ENTERED_ZONE", "EXITED_ZONE", "ENTERED_ZONE"], str(kinds(ev)))

# ── 4. Occlusion inside a zone (S10) ────────────────────────────────────────
print("4. Occlusion inside a zone (S10)")
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 20 + seg(W, "A", 3))                 # 2 s unseen
check("a 2 s occlusion produces nothing at all (under the 5 s grace)", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))
check("the visit simply continues", tr.zone_of(W) == "A")

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 60 + seg(W, "A", 3))                 # 6 s unseen
check("a 6 s occlusion: LOST_IN_ZONE then REAPPEARED, and no exit",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "REAPPEARED"], str(kinds(ev)))
lost = ev[1]
check("LOST_IN_ZONE is stamped at the last moment the worker was seen (2.9 s)",
      approx(lost.video_time_s, 2.9) and lost.details["last_seen_s"] == 2.9, str(lost))
check("REAPPEARED is stamped when they came back and reports the gap",
      approx(ev[2].video_time_s, 9.0) and approx(ev[2].details["gap_s"], 6.1), str(ev[2]))
check("they are still in zone A afterwards", tr.zone_of(W) == "A")

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 90)                                   # 9 s unseen
check("9 s unseen: LOST_IN_ZONE then EXITED_ZONE (LOST)",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "EXITED_ZONE"], str(kinds(ev)))
x = ev[2]
check("the LOST exit is flagged unverifiable and stamped at the last sighting",
      x.details.get("reason") == "LOST" and x.details.get("unverifiable") is True and approx(x.video_time_s, 2.9), str(x))
check("the visit is closed and the worker forgotten", tr.open_visits() == {} and tr.zone_of(W) is None)

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 85)                                   # 8.5 s: just past ttl
check("8.5 s unseen is past the 8 s limit: visit closed", "EXITED_ZONE" in types(ev), str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 75)                                   # 7.5 s: not yet
check("7.5 s unseen is still held open (LOST, not EXITED)", types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE"], str(kinds(ev)))

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 90 + seg(W, "A", 3))
check("after a LOST exit the same track coming back is a NEW, flagged entry",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "EXITED_ZONE", "ENTERED_ZONE"]
      and ev[3].details.get("first_sighting") is True, str(kinds(ev)))

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 120 + seg(W, "A", 3))               # 12 s gap, returns during the next frame
check("returning after the limit closes the old visit before starting the new one",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "EXITED_ZONE", "ENTERED_ZONE"], str(kinds(ev)))

tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + [None] * 70 + seg(W, None, 3))
check("coming back in a DIFFERENT place after being lost: REAPPEARED, then the exit as normal",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "REAPPEARED", "EXITED_ZONE"], str(kinds(ev)))

tr = ZoneEventTracker(SafetyConfig(lost_grace_s=4.0, lost_ttl_s=4.0))
ev = play(tr, seg(W, "A", 3) + [None] * 50)
check("grace equal to ttl: LOST and EXITED arrive together, in that order",
      types(ev) == ["ENTERED_ZONE", "LOST_IN_ZONE", "EXITED_ZONE"], str(kinds(ev)))

# ── 5. Workers are independent ──────────────────────────────────────────────
print("5. Two workers")
tr = ZoneEventTracker(CFG)
frames = []
for i in range(150):                                           # 15 s
    f = {}
    if 10 <= i < 100:
        f["trk-1"] = "A"
    elif 100 <= i < 140:
        f["trk-1"] = None
    if 30 <= i < 140:
        f["trk-2"] = "B" if i < 90 else None
    frames.append(f)
ev = play(tr, frames)
check("each worker has its own enter and exit",
      types(ev, "trk-1") == ["ENTERED_ZONE", "EXITED_ZONE"] and types(ev, "trk-2") == ["ENTERED_ZONE", "EXITED_ZONE"], str(kinds(ev)))
e1 = [e for e in ev if e.worker_key == "trk-1"]
e2 = [e for e in ev if e.worker_key == "trk-2"]
check("their times are their own", approx(e1[0].video_time_s, 1.0) and approx(e2[0].video_time_s, 3.0), f"{e1[0].video_time_s} {e2[0].video_time_s}")
tr = ZoneEventTracker(CFG)
frames = [{"trk-1": "A", "trk-2": "A"} for _ in range(30)] + [{"trk-2": "A"} for _ in range(100)]
ev = play(tr, frames)
check("one worker vanishing never closes the other's visit",
      types(ev, "trk-1") == ["ENTERED_ZONE", "LOST_IN_ZONE", "EXITED_ZONE"] and types(ev, "trk-2") == ["ENTERED_ZONE"], str(kinds(ev)))

# ── 6. First sighting ───────────────────────────────────────────────────────
print("6. Already inside when first seen")
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3))
check("a worker first seen inside a zone: ENTERED_ZONE flagged first_sighting, stamped at the first frame",
      types(ev) == ["ENTERED_ZONE"] and ev[0].details.get("first_sighting") is True and approx(ev[0].video_time_s, 0.0), str(ev))
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, None, 2) + seg(W, "A", 3))
check("a worker first seen OUTSIDE who walks in is not flagged", not ev[0].details.get("first_sighting"))

# ── 7. Dwell ────────────────────────────────────────────────────────────────
print("7. Dwell limits")
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, None, 1) + seg(W, "A", 15) + seg(W, None, 3))
check("warning, exceeded, then the exit, in order",
      types(ev) == ["ENTERED_ZONE", "DWELL_WARNING", "DWELL_EXCEEDED", "EXITED_ZONE"], str(kinds(ev)))
w_, x_ = ev[1], ev[2]
check("warning is stamped at 80 % of the visit (entry at 1.0 s -> 9.0 s)", approx(w_.video_time_s, 9.0), str(w_))
check("exceeded at 100 % (11.0 s)", approx(x_.video_time_s, 11.0), str(x_))
check("each carries the limit", w_.details["limit_s"] == 10.0 and x_.details["limit_s"] == 10.0 and w_.details["warn_fraction"] == 0.8)
check("each fires exactly once however long they stay", types(ev).count("DWELL_WARNING") == 1 and types(ev).count("DWELL_EXCEEDED") == 1)

tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 7.5) + seg(W, None, 3) + seg(W, "A", 7.5) + seg(W, None, 3))
check("a visit that stays under the warning level raises nothing",
      types(ev) == ["ENTERED_ZONE", "EXITED_ZONE"] * 2, str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 12) + seg(W, None, 3) + seg(W, "A", 12) + seg(W, None, 3))
check("a second visit raises its own warning and exceeded again",
      types(ev).count("DWELL_WARNING") == 2 and types(ev).count("DWELL_EXCEEDED") == 2, str(kinds(ev)))

tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 9.9) + seg(W, None, 3))
check("leaving at 9.9 s: the warning (8 s) fired, exceeded did not",
      types(ev) == ["ENTERED_ZONE", "DWELL_WARNING", "EXITED_ZONE"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 9.8) + seg(W, None, 3))
check("stepping out at 9.8 s and staying out: no DWELL_EXCEEDED stamped after the exit",
      "DWELL_EXCEEDED" not in types(ev) and all(e.video_time_s <= ev[-1].video_time_s + 1e-9 for e in ev), str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 9.8) + seg(W, None, 0.5) + seg(W, "A", 3))
check("stepping out briefly and back: the limit still counts the whole visit (exceeded at 10.0 s)",
      [e for e in ev if e.event_type == "DWELL_EXCEEDED"] and approx([e for e in ev if e.event_type == "DWELL_EXCEEDED"][0].video_time_s, 10.0), str(kinds(ev)))

tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10, warn_fraction=1.0)
ev = play(tr, seg(W, "A", 12))
check("warn_fraction 1.0 means no separate warning", types(ev) == ["ENTERED_ZONE", "DWELL_EXCEEDED"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
tr.watch_dwell("A", limit_s=20)
ev = play(tr, seg(W, "A", 25))
check("two limits on one zone each raise their own events",
      types(ev).count("DWELL_WARNING") == 2 and types(ev).count("DWELL_EXCEEDED") == 2, str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("B", limit_s=5)
ev = play(tr, seg(W, "A", 12))
check("a limit on another zone does nothing here", types(ev) == ["ENTERED_ZONE"])
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=10)
ev = play(tr, seg(W, "A", 3) + [None] * 90)
check("a worker lost mid-visit gets no dwell events while unseen (visit closed at last sighting)",
      "DWELL_EXCEEDED" not in types(ev), str(kinds(ev)))
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=4)
ev = play(tr, seg(W, "A", 2) + [None] * 30 + seg(W, "A", 3))
x_ = [e for e in ev if e.event_type == "DWELL_EXCEEDED"]
check("occluded 3 s inside and back: the visit's dwell includes the gap, and a limit crossed while out of view is "
      "stamped when they came back (5.0 s) and marked, not inside the gap",
      len(x_) == 1 and approx(x_[0].video_time_s, 5.0) and x_[0].details.get("crossed_unobserved") is True, str(kinds(ev)))

check("watch_dwell refuses nonsense",
      raises(lambda: tr.watch_dwell("A", 0)) and raises(lambda: tr.watch_dwell("A", -3))
      and raises(lambda: tr.watch_dwell("A", float("nan"))) and raises(lambda: tr.watch_dwell("A", True))
      and raises(lambda: tr.watch_dwell("A", 10, 0)) and raises(lambda: tr.watch_dwell("A", 10, 1.5))
      and raises(lambda: tr.watch_dwell(None, 10)) and raises(lambda: tr.watch_dwell("TRANSIT_ZONE", 10)))

# ── 8. Time invariance ──────────────────────────────────────────────────────
print("8. The same walk at any start time and frame rate")


def scripted(fps, start):
    t = ZoneEventTracker(CFG)
    t.watch_dwell("A", limit_s=6)
    walk = seg(W, None, 1, fps) + seg(W, "A", 8, fps) + seg(W, None, 2, fps) + seg("trk-2", "B", 3, fps)
    return play(t, walk, fps=fps, start=start)


base = scripted(10.0, 0.0)
shifted = scripted(10.0, 1000.0)
check("adding 1000 s to every timestamp gives the same events, 1000 s later",
      len(base) == len(shifted) and all(
          a.event_type == b.event_type and a.zone_id == b.zone_id and a.worker_key == b.worker_key
          and approx(a.video_time_s + 1000.0, b.video_time_s) and a.details == b.details
          for a, b in zip(base, shifted)), str(kinds(shifted)))
slow, fast = scripted(5.0, 0.0), scripted(30.0, 0.0)
check("5 fps and 30 fps agree on what happened",
      [(e.event_type, e.zone_id, e.worker_key) for e in slow] == [(e.event_type, e.zone_id, e.worker_key) for e in fast]
      == [(e.event_type, e.zone_id, e.worker_key) for e in base], f"{kinds(slow)} / {kinds(fast)}")
check("...and on when, to within one frame at the slower rate",
      all(abs(a.video_time_s - b.video_time_s) <= 0.2 + 1e-9 for a, b in zip(slow, fast)))

# ── 9. Settings ─────────────────────────────────────────────────────────────
print("9. Settings are honoured")
tr = ZoneEventTracker(SafetyConfig(enter_confirm_s=0.0))
ev = play(tr, seg(W, "A", 1))
check("enter_confirm_s=0: entry on the very first frame", types(ev) == ["ENTERED_ZONE"] and approx(ev[0].video_time_s, 0.0))
tr = ZoneEventTracker(SafetyConfig(enter_confirm_s=3.0))
ev = play(tr, seg(W, "A", 2.5))
check("enter_confirm_s=3: 2.5 s inside is not enough", ev == [])
ev = play(tr, seg(W, "A", 1), start=2.5)
check("...and 3 s total is, back-dated to the first frame", types(ev) == ["ENTERED_ZONE"] and approx(ev[0].video_time_s, 0.0), str(ev))
tr = ZoneEventTracker(SafetyConfig(exit_confirm_s=3.0))
ev = play(tr, seg(W, "A", 2) + seg(W, None, 2.5) + seg(W, "A", 1))
check("exit_confirm_s=3: stepping out for 2.5 s is not an exit", types(ev) == ["ENTERED_ZONE"])

# ── 10. Gaps while a change is being timed ──────────────────────────────────
print("10. A missed detection while entering")
tr = ZoneEventTracker(CFG)
frames = seg(W, None, 1) + seg(W, "A", 0.3) + [None] * 3 + seg(W, "A", 0.5)         # 0.3 s gap
ev = play(tr, frames + seg(W, "A", 0.5))
check("a 0.3 s dropout while approaching (under 0.5 s) does not restart the timing",
      types(ev) == ["ENTERED_ZONE"] and approx(ev[0].video_time_s, 1.0), str(kinds(ev)))
tr = ZoneEventTracker(CFG)
frames = seg(W, None, 1) + seg(W, "A", 0.5) + [None] * 10 + seg(W, "A", 0.5)         # 1 s gap
ev = play(tr, frames)
check("a 1 s dropout does restart it (the person was not continuously in view)", ev == [], str(kinds(ev)))
ev = play(tr, seg(W, "A", 1), start=len(frames) / 10.0)
check("...and the person who stays after that does enter, stamped where the timing restarted (2.5 s)",
      types(ev) == ["ENTERED_ZONE"] and approx(ev[0].video_time_s, 2.5), str(kinds(ev)))

# ── 11. Duplicates, memory, bad input ───────────────────────────────────────
print("11. Housekeeping")
tr = ZoneEventTracker(CFG)
play(tr, seg("trk-9", None, 2))
play(tr, [None] * 700, start=2.0)
check("a worker seen only outside every zone is forgotten after a minute", tr._workers == {})
tr = ZoneEventTracker(CFG)
play(tr, [{f"trk-{i}": None} for i in range(200)], fps=100.0)
check("memory stays bounded by workers, not by frames", len(tr._workers) <= 200)

tr = ZoneEventTracker(CFG)
e = ZoneEvent("ENTERED_ZONE", W, "A", 1.0, {})
w = None
tr.update({W: "A"}, None, 0.0)
tr.update({W: "A"}, None, 1.0)
w = tr._workers[W]
out = []
tr._emit(out, w, ZoneEvent("REAPPEARED", W, "A", 2.0, {"gap_s": 1.0}))
tr._emit(out, w, ZoneEvent("REAPPEARED", W, "A", 2.0, {"gap_s": 1.0}))
tr._emit(out, w, ZoneEvent("REAPPEARED", W, "A", 3.0, {"gap_s": 2.0}))
check("an exact repeat of the previous event is dropped, a different one is not", len(out) == 2)

tr = ZoneEventTracker(CFG)
check("update refuses a bad time",
      raises(lambda: tr.update({}, None, -1)) and raises(lambda: tr.update({}, None, float("nan")))
      and raises(lambda: tr.update({}, None, "3")) and raises(lambda: tr.update({}, None, None)))
tr.update({W: "A"}, None, 5.0)
check("update refuses time going backwards without a reset", raises(lambda: tr.update({W: "A"}, None, 4.0)))
check("equal times are fine (a live source can repeat)", not raises(lambda: tr.update({W: "A"}, None, 5.0)))
check("seen_workers=None means the keys of worker_zone; an explicit set may add unseen-in-zone workers",
      not raises(lambda: tr.update({W: "A"}, {W, "trk-5"}, 6.0)))
check("a zone of '' or TRANSIT_ZONE or None are all 'no zone'",
      ZoneEventTracker(CFG).update({W: ""}, None, 0.0) == []
      and ZoneEventTracker(CFG).update({W: "TRANSIT_ZONE"}, None, 0.0) == [])
tr = ZoneEventTracker(SafetyConfig(enter_confirm_s=0.0))
check("TRANSIT_ZONE never produces an entry", play(tr, seg(W, "TRANSIT_ZONE", 3)) == [])

# ── 12. Jumps in the timeline ───────────────────────────────────────────────
print("12. A jump closes visits as unverifiable")
tr = ZoneEventTracker(CFG)
play(tr, seg("trk-1", "A", 4) + [{"trk-1": "A", "trk-2": "B"}] * 20)
out = tr.reset("SEEK", last_seen_s=6.0, resume_at_s=1.0)
check("every open visit is closed", sorted(e.worker_key for e in out) == ["trk-1", "trk-2"], str(out))
check("closed as EXITED_ZONE, unverifiable, with the reason",
      all(e.event_type == "EXITED_ZONE" and e.details.get("unverifiable") is True and e.details.get("reason") == "SEEK" for e in out))
check("stamped at the last moment observed, never at the resume position",
      all(e.video_time_s > 1.0 for e in out) and approx(sorted(out, key=lambda e: e.worker_key)[0].video_time_s, 5.9, 0.11), str(out))
check("dwell in the closing record is never negative", all(e.details["dwell_s"] >= 0 for e in out))
check("...and is the real time inside, including a visit that began at 0.0 s",
      all(approx(e.details["dwell_s"], e.video_time_s - (0.0 if e.worker_key == "trk-1" else 4.0), 0.11) for e in out), str(out))
check("all state is cleared", tr.open_visits() == {} and tr._workers == {})
ev = play(tr, seg("trk-1", "A", 3), start=1.0)
check("time may restart after a reset (and the worker is a fresh sighting)",
      types(ev) == ["ENTERED_ZONE"] and ev[0].details.get("first_sighting") is True)

tr = ZoneEventTracker(CFG)
play(tr, seg(W, "A", 10))
out = tr.close_all("SESSION_END", 3.0)
check("closing earlier than the last sighting is honoured (never later than asked)", out and out[0].video_time_s <= 3.0 + 1e-9, str(out))
tr = ZoneEventTracker(CFG)
play(tr, seg(W, None, 3))
check("a worker not in any zone has nothing to close", tr.close_all("SESSION_END", 3.0) == [])
tr = ZoneEventTracker(CFG)
play(tr, seg(W, "A", 3) + [None] * 70)                                   # lost, last seen 2.9
out = tr.close_all("SESSION_END", 10.0)
check("a worker already LOST is closed at their own last sighting, not at the end time",
      out and approx(out[0].video_time_s, 2.9), str(out))

# the engine drives it
print("13. Driven by the safety engine")
clock = FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z")
engine = SafetyEngine(clock)
tr = ZoneEventTracker(CFG)
engine.register(tr)
got = []
for i in range(60):
    ft = engine.begin_frame(i)
    got.extend(ft.closed)
    got.extend(tr.update({W: "A"}, None, ft.video_time_s))
engine.reset("SEEK", frame_index=20)
ft = engine.begin_frame(20)
closed = list(ft.closed)
check("the engine's seek closes the visit through the tracker's reset()",
      len(closed) == 1 and closed[0].details.get("reason") == "SEEK" and approx(closed[0].video_time_s, 5.9, 0.11), str(closed))
after = tr.update({W: "A"}, None, ft.video_time_s)
check("the tracker follows the rewound footage from a clean state (a fresh sighting)",
      after == [] and tr._workers[W].first_seen == ft.video_time_s and tr.open_visits() == {})


class Boom:
    def reset(self, *a):
        raise RuntimeError("boom")


engine2 = SafetyEngine(FrameClock(fps=10.0, recorded_at="2026-11-08T04:00:00Z"))
tr2 = ZoneEventTracker(CFG)
engine2.register(Boom())
engine2.register(tr2)
for i in range(30):
    ft = engine2.begin_frame(i)
    tr2.update({W: "A"}, None, ft.video_time_s)
engine2.reset("SEEK", frame_index=5)
closed = list(engine2.begin_frame(5).closed)
check("a failing sibling component cannot stop the tracker being reset",
      len(closed) == 1 and closed[0].event_type == "EXITED_ZONE" and tr2.open_visits() == {}, str(closed))

# ── 14. Slow frame rates and review findings ────────────────────────────────
print("14. CPU-speed frame rates")
for fps in (30.0, 10.0, 3.0, 2.0, 1.9, 1.5, 1.0):
    tr = ZoneEventTracker(CFG)
    n = int(round(10 * fps))
    ev = play(tr, [{W: None}] * int(round(1 * fps)) + [{W: "A"}] * n + [{W: None}] * int(round(4 * fps)), fps=fps)
    ok = types(ev) == ["ENTERED_ZONE", "EXITED_ZONE"] and abs(ev[0].video_time_s - 1.0) <= 1.0 / fps + 1e-6 and abs(ev[1].video_time_s - 11.0) <= 1.0 / fps + 1e-6
    check(f"{fps} fps: a walk in and out gives one entry and one exit at the right times", ok, str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, [{W: None}] + [{W: "A"}] * 5 + [None] + [{W: "A"}] * 5, fps=1.0)
check("1 fps: one missed frame while inside is not a loss and not an exit", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, [{W: None}] + [{W: "A"}] + [None] * 2 + [{W: "A"}] * 3, fps=1.0)
check("1 fps: two missed frames while a change is being timed restart the timing (entry stamped after the gap, 4.0 s)",
      types(ev) == ["ENTERED_ZONE"] and approx(ev[0].video_time_s, 4.0), str(kinds(ev)))

print("15. When the update is not called for a stretch (the seen-branch of the gap rule)")
tr = ZoneEventTracker(CFG)
for i in range(21):                                    # 10 fps up to 2.0 s, in view outside
    tr.update({W: "A" if i == 20 else None}, None, i / 10.0)
out = tr.update({W: "A"}, None, 3.0)                  # next call a full second later
check("fast frames, then a 1 s silence while the entry was being timed: the timing restarts (no entry yet)", out == [], str(out))
out = []
for i in range(1, 9):
    out += tr.update({W: "A"}, None, 3.0 + i / 10.0)
check("...and it confirms 0.7 s after the restart, stamped 3.0 s", types(out) == ["ENTERED_ZONE"] and approx(out[0].video_time_s, 3.0), str(out))

print("16. Ordering inside one worker (review finding)")
tr = ZoneEventTracker(CFG)
tr.watch_dwell("A", limit_s=45)
frames = seg(W, "A", 41.0) + [None] * 61 + seg(W, "A", 3.0)                        # lost 41.0 -> back 47.1
ev = play(tr, frames)
ts = [e.video_time_s for e in ev]
check("events for a worker come out in time order, with the dwell limit crossed in the gap stamped at the return",
      ts == sorted(ts), str(kinds(ev)))
d = [e for e in ev if e.event_type == "DWELL_EXCEEDED"]
check("...and it says it was crossed unobserved", len(d) == 1 and d[0].details.get("crossed_unobserved") is True and d[0].video_time_s >= 47.0, str(d))
check("a limit crossed while the worker WAS in view keeps its exact time",
      approx([e for e in ev if e.event_type == "DWELL_WARNING"][0].video_time_s, 36.0) and "crossed_unobserved" not in [e for e in ev if e.event_type == "DWELL_WARNING"][0].details)

print("17. Out, then straight into another zone")
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + seg(W, None, 0.6) + seg(W, "B", 3))
ex = [e for e in ev if e.event_type == "EXITED_ZONE"]
check("A's exit is stamped when they first stepped out (3.0 s), not when they reached B (3.6 s)",
      len(ex) == 1 and approx(ex[0].video_time_s, 3.0) and approx(ex[0].details["dwell_s"], 3.0), str(kinds(ev)))
tr = ZoneEventTracker(CFG)
ev = play(tr, seg(W, "A", 3) + seg(W, None, 0.6) + seg(W, "B", 0.4) + seg(W, "A", 3))
check("stepping out, brushing B and back into A is no exit", types(ev) == ["ENTERED_ZONE"], str(kinds(ev)))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
