"""
Step 14 — confidence and UNKNOWN handling.

The plan's criterion:

  "confirm low-confidence periods land in unknownMinutes rather than being
   silently attributed to the nearest guess."

That word SILENTLY is the whole test. A system that attributes a 0.30 match to
somebody is not making a small error — it is making an invisible one. The
minutes look like that person's day, the confidence figure looks plausible
because it is averaged with better observations, and nobody has any reason to
check. So this file tests both halves: that the floor refuses, AND that the
refused time is still counted somewhere a reader will see it.

Run:  venv/Scripts/python.exe backend/tests/test_confidence.py
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.identity_tracker import (          # noqa: E402
    Identity, IdentityTracker, IDENTITY_MIN_CONFIDENCE,
)
from app.cv.signature_registry import DailySignatureRegistry   # noqa: E402
from app.cv.handoff import HandoffRegistry, Departure          # noqa: E402
from app.db.employee_aggregator import (       # noqa: E402
    IDENTITY_MIN_CONFIDENCE as AGG_FLOOR,
    aggregate_day_sync, ensure_stats_table,
)
from app.db.identity_writer import ensure_identity_table       # noqa: E402

FAILS = []
DB = os.path.join(os.path.dirname(__file__), "..", "..", "workplace_analytics.db")
ORG = "step14-org"
T0 = datetime(2026, 8, 29, 9, 0, 0, tzinfo=timezone.utc)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def sig(seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=512).astype(np.float32)
    v /= np.linalg.norm(v)
    hist = rng.random(256).astype(np.float32)
    return {"embedding": v, "upper": hist, "lower": hist * 0.8, "height": 85.0,
            "bbox": [100, 100, 160, 260], "area": 9600}


def main():
    print("=" * 70)
    print("STEP 14 — CONFIDENCE AND UNKNOWN HANDLING")
    print("=" * 70)

    # ══ A. ONE FLOOR, ENFORCED IN ONE PLACE ════════════════════════════════
    print("\nA. THE 0.5 FLOOR")
    print("-" * 70)
    print(f"   tracker    IDENTITY_MIN_CONFIDENCE = {IDENTITY_MIN_CONFIDENCE}")
    print(f"   aggregator IDENTITY_MIN_CONFIDENCE = {AGG_FLOOR}")
    check("the floor is the plan's 0.5", IDENTITY_MIN_CONFIDENCE == 0.5,
          str(IDENTITY_MIN_CONFIDENCE))
    check("the tracker and the aggregator agree",
          IDENTITY_MIN_CONFIDENCE == AGG_FLOOR, f"{IDENTITY_MIN_CONFIDENCE} vs {AGG_FLOOR}")

    ident = Identity("t::1", 1, now=0.0)
    for conf, want in ((0.95, True), (0.51, True), (0.50, True),
                       (0.49, False), (0.30, False), (0.0, False)):
        fresh = Identity("t::x", 1, now=0.0)
        got = fresh.attribute("emp-a", conf, "face")
        state = f"{fresh.employee_id}/{fresh.method}"
        print(f"   attribute at {conf:.2f} -> {'accepted' if got else 'REFUSED '}  {state}")
        if got != want:
            FAILS.append(f"floor at {conf}")
        # A refusal must leave the identity genuinely UNKNOWN, not half-named.
        if not got and (fresh.employee_id is not None or fresh.method != "unknown"):
            FAILS.append(f"half-attributed at {conf}")
    check("everything at or above 0.5 is accepted, everything below refused",
          not [f for f in FAILS if f.startswith("floor at")], "")
    check("a refusal leaves employee_id=None AND method='unknown'",
          not [f for f in FAILS if f.startswith("half-attributed")], "")

    ident.attribute("emp-a", 0.9, "face")
    ident.rejected_low_confidence = 0
    ident.attribute("emp-b", 0.2, "seat")
    check("a refusal is counted, not silent", ident.rejected_low_confidence == 1,
          str(ident.rejected_low_confidence))
    check("...and does not overwrite a good attribution",
          ident.employee_id == "emp-a" and ident.method == "face",
          f"{ident.employee_id}/{ident.method}")

    # ══ B. EVERY PATH GOES THROUGH IT ══════════════════════════════════════
    print("\nB. ALL FOUR ATTRIBUTION PATHS OBEY THE FLOOR")
    print("-" * 70)

    # face
    tk = IdentityTracker(session_id="f")
    for f in range(6):
        tk.assign([{**sig(1), "track_id": 1}], now=f * 0.5)
    tk.apply_face_matches({1: {"employee_id": "emp-face", "confidence": 0.35,
                               "name": "Weak"}})
    i = list(tk.identities().values())[0]
    print(f"   face   at 0.35 -> {i.employee_id} ({i.method})")
    check("a weak face match names nobody", i.employee_id is None, str(i.employee_id))

    # seat
    tk2 = IdentityTracker(session_id="s")
    tk2.set_seat_map({"desk": ["emp-seat"]})
    # 40% of the time at the desk: below the seat rule AND below the floor.
    for f in range(200):
        z = "desk" if f < 80 else None
        tk2.assign([{**sig(2), "track_id": 1}], zone_by_track={1: z}, now=f * 0.5)
    i2 = list(tk2.identities().values())[0]
    print(f"   seat   at {i2.confidence:.2f} -> {i2.employee_id} ({i2.method})")
    check("a weak seat prior names nobody", i2.employee_id is None, str(i2.employee_id))

    # fusion, via the registry
    reg = DailySignatureRegistry(org_id="c14")
    reg.clear()
    reg.register("emp-fuse", sig(3), confidence=0.95)
    # Force a weak entry so the match returns a low confidence.
    reg._store["emp-fuse"]["confidence"] = 0.30
    tk3 = IdentityTracker(session_id="u")
    tk3.set_registry(reg, "AREA")
    for f in range(30):
        tk3.assign([{**sig(3), "track_id": 1}], now=f * 0.5)
    i3 = list(tk3.identities().values())[0]
    print(f"   fusion at 0.30 -> {i3.employee_id} ({i3.method})")
    check("a weak registry match names nobody", i3.employee_id is None, str(i3.employee_id))

    # handoff
    ho = HandoffRegistry(org_id="c14h")
    ho.set_links({("a", "b"): (1, 30)})
    dep_ident = Identity("a::1", 1, now=0.0)
    dep_ident.hits = 10
    dep_ident.embeddings.append(sig(4)["embedding"])
    dep_ident.uppers.append(sig(4)["upper"])
    dep_ident.employee_id, dep_ident.confidence, dep_ident.method = "emp-hand", 0.30, "seat"
    ho.record_departure(dep_ident, "a", now=0.0)
    tk4 = IdentityTracker(session_id="h")
    tk4.set_handoff(ho, "b")
    tk4.assign([{**sig(4), "track_id": 9}], now=5.0)
    i4 = list(tk4.identities().values())[0]
    print(f"   handoff at 0.30 -> {i4.employee_id} ({i4.method})")
    check("a weak handoff names nobody", i4.employee_id is None, str(i4.employee_id))

    # And the same paths at a good confidence still work — a floor that blocks
    # everything is not a floor, it is a bug.
    tk5 = IdentityTracker(session_id="ok")
    for f in range(6):
        tk5.assign([{**sig(5), "track_id": 1}], now=f * 0.5)
    tk5.apply_face_matches({1: {"employee_id": "emp-ok", "confidence": 0.88, "name": "OK"}})
    i5 = list(tk5.identities().values())[0]
    print(f"   face   at 0.88 -> {i5.employee_id} ({i5.method})")
    check("a confident match STILL works", i5.employee_id == "emp-ok", str(i5.employee_id))

    # ══ C. A COLLAPSED CONFIDENCE IS WITHDRAWN ═════════════════════════════
    print("\nC. AN ATTRIBUTION WHOSE EVIDENCE COLLAPSES IS WITHDRAWN")
    print("-" * 70)
    tk6 = IdentityTracker(session_id="drift")
    tk6.set_seat_map({"desk": ["emp-drift"]})
    # Long enough at the desk to bind confidently...
    for f in range(120):
        tk6.assign([{**sig(6), "track_id": 1}], zone_by_track={1: "desk"}, now=f * 0.5)
    i6 = list(tk6.identities().values())[0]
    bound_at = i6.confidence
    print(f"   bound at {bound_at:.2f} ({i6.employee_id})")
    check("bound while the evidence was strong", i6.employee_id == "emp-drift")

    # ...then away for long enough that the fraction falls under the floor.
    for f in range(120, 700):
        tk6.assign([{**sig(6), "track_id": 1}], zone_by_track={1: None}, now=f * 0.5)
    print(f"   after wandering: {i6.confidence:.2f} -> {i6.employee_id} ({i6.method})")
    check("an attribution below the floor is withdrawn, not merely flagged",
          i6.employee_id is None and i6.method == "unknown",
          f"{i6.employee_id}/{i6.confidence:.2f}")

    # ══ C2. REINSTATEMENT ══════════════════════════════════════════════════
    print("\nC2. A WITHDRAWN SEAT BINDING IS RESTORED WHEN THE EVIDENCE RETURNS")
    print("-" * 70)
    # Withdrawing at 0.50 while only binding above 0.60 would leave a dead band:
    # somebody who dips during a long meeting and comes back to finish the day
    # at 0.55 would stay UNKNOWN for the rest of the session. That is a
    # threshold gap, not caution — their own afternoon at their own desk would
    # land in unknownMinutes.
    tk7 = IdentityTracker(session_id="reinstate")
    tk7.set_seat_map({"desk": ["emp-back"]})
    script = [(120, "desk"), (120, None), (120, "desk"), (100, None),
              (60, "desk"), (95, None), (85, "desk")]     # 385/700 = 0.550
    seen = []
    t = 0.0
    for secs, zone in script:
        for _ in range(int(secs / 0.5)):
            tk7.assign([{**sig(7), "track_id": 1}], zone_by_track={1: zone}, now=t)
            t += 0.5
        i7 = list(tk7.identities().values())[0]
        seen.append(i7.employee_id)
        print(f"   {'desk' if zone else 'away'} -> {i7.employee_id} @ {i7.confidence:.3f}")
    check("the name IS withdrawn while the evidence is under the floor",
          None in seen, "never dipped")
    check("...and restored once it returns, at the measured fraction",
          i7.employee_id == "emp-back" and abs(i7.confidence - 0.55) < 0.02,
          f"{i7.employee_id} @ {i7.confidence:.3f} (true fraction 0.550)")

    # Reinstatement must not become a back door: a different desk is a
    # different claim and has to clear the full binding gate on its own.
    tk8 = IdentityTracker(session_id="elsewhere")
    tk8.set_seat_map({"desk": ["emp-back"], "other": ["emp-other"]})
    t = 0.0
    for secs, zone in [(120, "desk"), (200, None)]:
        for _ in range(int(secs / 0.5)):
            tk8.assign([{**sig(8), "track_id": 1}], zone_by_track={1: zone}, now=t)
            t += 0.5
    i8 = list(tk8.identities().values())[0]
    withdrawn = i8.employee_id is None
    for _ in range(int(300 / 0.5)):          # now sit at somebody ELSE's desk
        tk8.assign([{**sig(8), "track_id": 1}], zone_by_track={1: "other"}, now=t)
        t += 0.5
    print(f"   withdrawn at desk, then 300s at 'other' -> {i8.employee_id}")
    check("reinstatement does not hand the old name to a new desk",
          withdrawn and i8.employee_id != "emp-back", str(i8.employee_id))

    # ══ D. THE PLAN'S TEST: WHERE THE TIME GOES ════════════════════════════
    print("\nD. LOW-CONFIDENCE TIME LANDS IN unknownMinutes  <<< THE PLAN'S TEST")
    print("-" * 70)
    ensure_identity_table()
    ensure_stats_table()
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))

    # Ten minutes of a named person at 0.90, then ten minutes of the SAME
    # person at 0.30 — the exact case the plan describes.
    def write(minutes, employee, confidence, method, start):
        t = start
        for _ in range(int(minutes * 60 / 5)):
            c.execute(
                "INSERT INTO identity_events (org_id, employee_id, camera_id, zone_id,"
                " track_id, identity_id, posture, confidence, method, observed_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ORG, employee, "cam1", "desk_1", 1, "s::1", "SITTING",
                 confidence, method, t.isoformat(),
                 datetime.now(timezone.utc).isoformat()))
            t += timedelta(seconds=5)
        return t

    after = write(10, "emp-x", 0.90, "face", T0)
    write(10, "emp-x", 0.30, "seat", after)
    c.commit()
    c.close()

    res = aggregate_day_sync(day=T0.date(), org_id=ORG)
    print(f"   {res['rows_read']} events, {res['low_confidence_rows']} below the floor")

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    row = dict(c.execute(
        "SELECT * FROM employee_day_stats WHERE org_id=? AND employee_id=?",
        (ORG, "emp-x")).fetchone())
    c.close()

    print(f"   desk_minutes       {row['desk_minutes']}   (only the confident 10 min)")
    print(f"   unknown_minutes    {row['unknown_minutes']}   (the low-confidence 10 min)")
    print(f"   binding_confidence {row['binding_confidence']}   (not dragged down by the weak half)")

    check("the low-confidence half was NOT attributed",
          row["desk_minutes"] <= 11, f"{row['desk_minutes']} min")
    check("...it landed in unknown_minutes instead  <<< THE PLAN'S TEST",
          row["unknown_minutes"] >= 8, f"{row['unknown_minutes']} min")
    check("...and the person's confidence stays honest",
          row["binding_confidence"] >= 0.85, str(row["binding_confidence"]))
    check("the abstention is reported, not silent",
          res["low_confidence_rows"] > 0, str(res["low_confidence_rows"]))

    # The contrast: the same twenty minutes, all confident.
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    after = write(10, "emp-y", 0.90, "face", T0)
    write(10, "emp-y", 0.88, "face", after)
    c.commit()
    c.close()
    aggregate_day_sync(day=T0.date(), org_id=ORG)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    good = dict(c.execute(
        "SELECT * FROM employee_day_stats WHERE org_id=? AND employee_id=?",
        (ORG, "emp-y")).fetchone())
    c.close()
    print(f"   the same 20 min, all confident -> desk {good['desk_minutes']} min, "
          f"unknown {good['unknown_minutes']} min")
    check("confident time IS attributed — the floor is not blocking everything",
          good["desk_minutes"] >= 18, f"{good['desk_minutes']} min")

    # ══ E. THE UI CONTRACT ═════════════════════════════════════════════════
    print("\nE. THE UI THRESHOLD")
    print("-" * 70)
    import re
    ui = open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend",
                           "app", "components", "ConfidenceBanner.jsx"),
              encoding="utf-8").read()
    low = re.search(r"export const LOW_CONFIDENCE = ([\d.]+)", ui)
    floor = re.search(r"export const ABSTAIN_FLOOR = ([\d.]+)", ui)
    print(f"   UI warns below       {low.group(1)}   (the plan says 0.6)")
    print(f"   UI abstention floor  {floor.group(1)}   (must match the backend)")
    check("the UI warns below 0.6 as the plan requires", low.group(1) == "0.6",
          low.group(1))
    check("the UI floor matches the backend floor",
          float(floor.group(1)) == IDENTITY_MIN_CONFIDENCE,
          f"{floor.group(1)} vs {IDENTITY_MIN_CONFIDENCE}")

    # ══ F. CLEANUP ═════════════════════════════════════════════════════════
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    c.commit()
    c.close()
    reg.clear()

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 14: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
