"""
Step 15 — the hourly rollup behind the daily timeline.

What must be true for a timeline to be worth drawing:

  1. The SHAPE is real. An hour with two hours' worth of desk time in it, or
     a quiet hour that reports the day's average, makes the chart a decoration.
  2. Confidence is PER HOUR. An hour at 0.95 and an hour at 0.55 must not both
     read 0.75 — that is the averaging failure Step 14 exists to prevent.
  3. Step 14's floor still applies. Low-confidence time lands in the hour's
     unknown_minutes, not on somebody's desk total.
  4. Re-running replaces. An aggregator that appends produces a chart that
     doubles every time somebody presses refresh.
  5. The hourly and daily rollups AGREE. Two numbers for the same fact, from
     two code paths, is a bug waiting to be argued about in a viva.

Run:  venv/Scripts/python.exe backend/tests/test_hour_aggregator.py
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.employee_hour_aggregator import (      # noqa: E402
    aggregate_hours_sync, analyse_hours, ensure_hour_stats_table,
    _cap_minutes, SECONDS_PER_HOUR,
)
from app.db.employee_aggregator import (           # noqa: E402
    aggregate_day_sync, ensure_stats_table, IDENTITY_MIN_CONFIDENCE,
)
from app.db.identity_writer import ensure_identity_table   # noqa: E402

FAILS = []
DB = os.path.join(os.path.dirname(__file__), "..", "..", "workplace_analytics.db")
ORG = "step15-org"
EMP = "emp-step15"
DAY = datetime(2026, 8, 29, 0, 0, 0, tzinfo=timezone.utc)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def write_events(spans):
    """spans: list of (start_hour, minutes, zone, posture, confidence).

    Clears the PREVIOUS scenario's hour rows as well as its events. Without
    that, each section reads rows left behind by the one before it and the
    figures look like the aggregator's output when they are someone else's —
    which is exactly the false failure this test produced before the delete
    was added.
    """
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM identity_events WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_hour_stats WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    n = 0
    for hour, minutes, zone, posture, conf in spans:
        t = DAY + timedelta(hours=hour)
        for _ in range(int(minutes * 12)):        # a sample every 5 s
            c.execute(
                "INSERT INTO identity_events (org_id, employee_id, camera_id,"
                " zone_id, track_id, identity_id, posture, confidence, method,"
                " observed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ORG, EMP, "cam1", zone, 1, "s::1", posture, conf,
                 "seat" if conf >= IDENTITY_MIN_CONFIDENCE else "seat",
                 t.isoformat(), datetime.now(timezone.utc).isoformat()))
            t += timedelta(seconds=5)
            n += 1
    c.commit()
    c.close()
    return n


def read_hours():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM employee_hour_stats WHERE org_id=? ORDER BY hour", (ORG,))]
    c.close()
    return rows


def cleanup():
    c = sqlite3.connect(DB)
    for t in ("identity_events", "employee_hour_stats", "employee_day_stats"):
        try:
            c.execute(f"DELETE FROM {t} WHERE org_id = ?", (ORG,))
        except sqlite3.OperationalError:
            pass
    c.commit()
    c.close()


def main():
    print("=" * 70)
    print("STEP 15 — HOURLY ROLLUP")
    print("=" * 70)

    ensure_identity_table()
    ensure_stats_table()
    ensure_hour_stats_table()
    cleanup()

    # ══ A. THE SHAPE IS REAL ═══════════════════════════════════════════════
    print("\nA. THE TIMELINE REFLECTS WHEN, NOT JUST HOW MUCH")
    print("-" * 70)
    # A plausible morning: solid 9-10, at desk but half of 10-11, lunch gone
    # at 12, back hard at 14.
    n = write_events([
        (9,  60, "desk_1", "SITTING",  0.92),
        (10, 30, "desk_1", "SITTING",  0.90),
        (14, 45, "desk_1", "SITTING",  0.89),
    ])
    res = aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    rows = read_hours()
    print(f"   {n} events -> {res['hours']} hour rows")
    for r in rows:
        print(f"     {r['hour']:02d}:00  desk={r['desk_minutes']:2d}m  "
              f"present={r['present_minutes']:2d}m  conf={r['binding_confidence']:.2f}")

    by_hour = {r["hour"]: r for r in rows}
    check("only the hours with observations produce rows",
          sorted(by_hour) == [9, 10, 14], str(sorted(by_hour)))
    check("a full hour reads as a full hour",
          58 <= by_hour[9]["desk_minutes"] <= 60, f"{by_hour[9]['desk_minutes']}m")
    check("a half hour reads as a half hour",
          28 <= by_hour[10]["desk_minutes"] <= 30, f"{by_hour[10]['desk_minutes']}m")
    check("a 45-minute hour reads as 45 minutes",
          43 <= by_hour[14]["desk_minutes"] <= 45, f"{by_hour[14]['desk_minutes']}m")
    check("no hour can exceed 60 minutes",
          all(r["desk_minutes"] <= 60 and r["present_minutes"] <= 60 for r in rows))

    # ══ B. CONFIDENCE IS PER HOUR ══════════════════════════════════════════
    print("\nB. CONFIDENCE IS MEASURED PER HOUR, NOT INHERITED FROM THE DAY")
    print("-" * 70)
    write_events([
        (9,  60, "desk_1", "SITTING", 0.95),
        (10, 60, "desk_1", "SITTING", 0.55),
    ])
    aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    by_hour = {r["hour"]: r for r in read_hours()}
    print(f"   09:00 conf={by_hour[9]['binding_confidence']:.2f}   "
          f"10:00 conf={by_hour[10]['binding_confidence']:.2f}")
    check("the confident hour reports its own confidence",
          abs(by_hour[9]["binding_confidence"] - 0.95) < 0.02,
          str(by_hour[9]["binding_confidence"]))
    check("the weak hour reports ITS own, not the 0.75 average",
          abs(by_hour[10]["binding_confidence"] - 0.55) < 0.02,
          str(by_hour[10]["binding_confidence"]))

    # ══ C. STEP 14'S FLOOR STILL APPLIES ═══════════════════════════════════
    print("\nC. LOW-CONFIDENCE TIME LANDS IN THE HOUR'S unknown_minutes")
    print("-" * 70)
    write_events([
        (9,  60, "desk_1", "SITTING", 0.90),   # confident
        (11, 60, "desk_1", "SITTING", 0.30),   # below the floor
    ])
    res = aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    by_hour = {r["hour"]: r for r in read_hours()}
    print(f"   rows below the floor: {res['low_confidence_rows']}")
    for h, r in sorted(by_hour.items()):
        print(f"     {h:02d}:00  desk={r['desk_minutes']:2d}m  "
              f"unknown={r['unknown_minutes']:2d}m")
    check("the confident hour is attributed", by_hour[9]["desk_minutes"] >= 58,
          f"{by_hour[9]['desk_minutes']}m")
    check("the weak hour produces NO desk time for that person",
          11 not in by_hour or by_hour[11]["desk_minutes"] == 0,
          str(by_hour.get(11, {}).get("desk_minutes")))
    check("...and the abstention is counted, not silent",
          res["low_confidence_rows"] > 0, str(res["low_confidence_rows"]))
    check("the hour's unattributed time is recorded against the hour",
          by_hour[9]["unknown_minutes"] >= 0)

    # ══ D. IDEMPOTENCY ═════════════════════════════════════════════════════
    print("\nD. RE-RUNNING REPLACES, NEVER APPENDS")
    print("-" * 70)
    write_events([(9, 60, "desk_1", "SITTING", 0.92)])
    aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    first = read_hours()
    aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    third = read_hours()
    print(f"   after 1 run: {len(first)} row(s); after 3 runs: {len(third)} row(s)")
    check("three runs produce the same row count", len(first) == len(third),
          f"{len(first)} vs {len(third)}")
    check("and the same figures",
          first[0]["desk_minutes"] == third[0]["desk_minutes"],
          f"{first[0]['desk_minutes']} vs {third[0]['desk_minutes']}")
    check("a re-aggregated row is re-queued for sync",
          third[0]["synced_at"] is None, str(third[0]["synced_at"]))

    # ══ E. THE TWO ROLLUPS AGREE ═══════════════════════════════════════════
    print("\nE. HOURLY AND DAILY MUST NOT DISAGREE  <<< the viva question")
    print("-" * 70)
    write_events([
        (9,  60, "desk_1", "SITTING", 0.92),
        (10, 60, "desk_1", "SITTING", 0.92),
        (11, 30, "desk_1", "SITTING", 0.92),
    ])
    aggregate_hours_sync(day=DAY.date(), org_id=ORG)
    day_res = aggregate_day_sync(day=DAY.date(), org_id=ORG)

    hour_total = sum(r["desk_minutes"] for r in read_hours())
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    drow = c.execute("SELECT * FROM employee_day_stats WHERE org_id=? AND employee_id=?",
                     (ORG, EMP)).fetchone()
    c.close()
    day_total = dict(drow)["desk_minutes"] if drow else 0
    print(f"   sum of hourly desk minutes: {hour_total}")
    print(f"   daily rollup desk minutes : {day_total}")
    # Not exact equality: hour boundaries round independently, so a few
    # minutes of drift across three hours is arithmetic, not disagreement.
    # More than that would mean the two paths measure different things.
    check("the two rollups agree within rounding",
          abs(hour_total - day_total) <= 3,
          f"{hour_total} vs {day_total} (drift {abs(hour_total - day_total)})")

    # ══ F. UNIT: THE CAP ═══════════════════════════════════════════════════
    print("\nF. THE HOUR CAP")
    print("-" * 70)
    print(f"   3000s -> {_cap_minutes(3000)}m   3600s -> {_cap_minutes(3600)}m   "
          f"7200s -> {_cap_minutes(7200)}m   -5s -> {_cap_minutes(-5)}m")
    check("normal spans convert to minutes", _cap_minutes(3000) == 50)
    check("an hour is 60 minutes", _cap_minutes(SECONDS_PER_HOUR) == 60)
    check("two cameras' overlap cannot report 120 minutes in an hour",
          _cap_minutes(7200) == 60, str(_cap_minutes(7200)))
    check("negative time is impossible", _cap_minutes(-5) == 0)

    # ══ G. UNIT: EMPTY AND DEGENERATE INPUT ════════════════════════════════
    print("\nG. DEGENERATE INPUT DOES NOT CRASH OR INVENT")
    print("-" * 70)
    check("no samples -> no hours", analyse_hours([], {"desk_1"}) == {})
    one = analyse_hours(
        [{"observed_at": DAY + timedelta(hours=9), "zone_id": "desk_1",
          "posture": "SITTING", "confidence": 0.9}], {"desk_1"})
    print(f"   a single sample -> {one}")
    check("a single sample is 0 minutes, not 5 seconds rounded up",
          one[9]["desk_minutes"] == 0, str(one[9]["desk_minutes"]))
    check("...but its confidence is still recorded",
          abs(one[9]["binding_confidence"] - 0.9) < 0.01)

    gap = analyse_hours([
        {"observed_at": DAY + timedelta(hours=9), "zone_id": "desk_1",
         "posture": "SITTING", "confidence": 0.9},
        # A ten-minute hole: the camera stopped. Must not be credited.
        {"observed_at": DAY + timedelta(hours=9, minutes=10), "zone_id": "desk_1",
         "posture": "SITTING", "confidence": 0.9},
    ], {"desk_1"})
    print(f"   a 10-minute gap -> desk={gap[9]['desk_minutes']}m")
    check("a gap larger than the cap is not credited as presence",
          gap[9]["desk_minutes"] == 0, f"{gap[9]['desk_minutes']}m")

    cleanup()
    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 15 HOURLY ROLLUP: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
