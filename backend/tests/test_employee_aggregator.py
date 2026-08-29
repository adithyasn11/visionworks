"""
Step 8 verification — chair exits, breaks, focus blocks.

The plan's criterion, and it calls this "the single most important verification
in the whole plan":

  "record a 10-minute video where you deliberately stand up 3 times.
   Confirm awayFromDeskCount == 3."

Nobody can record that from inside a test, so the timeline is BUILT to be that
video: 10 minutes of samples at the real 5-second rate, with three deliberate
departures from the desk. The measurement code under test is the production
code, and the samples are exactly the shape `identity_writer.py` writes.

Then — and this is the part that makes the number trustworthy — the same
timeline is replayed with the departures made SHORTER than the 90-second
debounce, and the count must drop to zero. A measure that counts three
departures but also counts three lean-backs is not measuring chair exits.

Run:  venv/Scripts/python.exe backend/tests/test_employee_aggregator.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.employee_aggregator import (        # noqa: E402
    analyse_timeline, aggregate_day_sync, ensure_stats_table,
    CHAIR_EXIT_MIN_SECONDS, BREAK_MIN_SECONDS, FOCUS_BLOCK_MIN_SECONDS,
    MAX_SAMPLE_GAP_SECONDS,
)

FAILS = []
T0 = datetime(2026, 8, 29, 9, 0, 0, tzinfo=timezone.utc)
SAMPLE = 5.0          # the writer's real interval
DESK = {"desk_1"}
BREAKS = {"pantry"}


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def build(script):
    """
    (seconds, zone, posture) segments -> samples at the real 5 s rate.

    A zone of None means "not in any zone" — walking the office. That is what
    leaving your desk actually looks like in the data.
    """
    samples, t = [], T0
    for seconds, zone, posture in script:
        n = max(1, int(seconds / SAMPLE))
        for _ in range(n):
            samples.append({"observed_at": t, "zone_id": zone,
                            "posture": posture, "confidence": 0.95})
            t += timedelta(seconds=SAMPLE)
    return samples


def main():
    print("=" * 70)
    print("STEP 8 — CHAIR EXITS, BREAKS, FOCUS BLOCKS")
    print("=" * 70)

    # ══ A. THE PLAN'S TEST ═════════════════════════════════════════════════
    print("\nA. TEN MINUTES, STANDING UP THREE TIMES")
    print("   (the plan's single most important verification)")
    print("-" * 70)

    # 10 minutes. Three departures, each comfortably past the 90 s debounce.
    script = [
        (120, "desk_1", "SITTING"),      # 2 min at the desk
        (120, None,     "WALKING"),      # AWAY #1  — 2 min
        (120, "desk_1", "SITTING"),
        (100, None,     "WALKING"),      # AWAY #2  — 100 s
        (60,  "desk_1", "SITTING"),
        (95,  None,     "STANDING"),     # AWAY #3  — 95 s, just over the line
        (85,  "desk_1", "SITTING"),
    ]
    total = sum(s[0] for s in script)
    r = analyse_timeline(build(script), DESK, BREAKS)

    print(f"   timeline      : {total}s ({total/60:.1f} min), "
          f"{r['samples']} samples at {SAMPLE:.0f}s")
    print(f"   desk time     : {r['desk_seconds']:.0f}s")
    print(f"   present       : {r['present_seconds']:.0f}s")
    for i, e in enumerate(r["chair_exits"], 1):
        print(f"   chair exit #{i} : {e['seconds']:.0f}s away")
    print(f"\n   awayFromDeskCount = {r['away_from_desk_count']}")

    check("awayFromDeskCount == 3  <<< THE NUMBER",
          r["away_from_desk_count"] == 3, str(r["away_from_desk_count"]))
    check("desk time excludes the time away",
          abs(r["desk_seconds"] - 385) < 15, f"{r['desk_seconds']:.0f}s of {total}s")

    # ══ B. THE DEBOUNCE ════════════════════════════════════════════════════
    print("\nB. THE DEBOUNCE — bending down must NOT count")
    print("-" * 70)
    # The same three departures, each 30 s instead of 95-120 s. A person
    # reaching for a drawer, leaning out of the polygon, briefly occluded.
    short = [
        (120, "desk_1", "SITTING"), (30, None, "STANDING"),
        (120, "desk_1", "SITTING"), (30, None, "STANDING"),
        (120, "desk_1", "SITTING"), (30, None, "STANDING"),
        (120, "desk_1", "SITTING"),
    ]
    rs = analyse_timeline(build(short), DESK, BREAKS)
    print(f"   three 30s departures -> awayFromDeskCount = {rs['away_from_desk_count']}")
    check("brief departures are not chair exits",
          rs["away_from_desk_count"] == 0, str(rs["away_from_desk_count"]))

    # Right at the boundary, from both sides.
    for secs, want in ((85, 0), (95, 1)):
        rb = analyse_timeline(
            build([(120, "desk_1", "SITTING"), (secs, None, "STANDING"),
                   (120, "desk_1", "SITTING")]), DESK, BREAKS)
        print(f"   a {secs}s departure   -> {rb['away_from_desk_count']} "
              f"(threshold {CHAIR_EXIT_MIN_SECONDS:.0f}s)")
        check(f"{secs}s departure counts as {want}",
              rb["away_from_desk_count"] == want, str(rb["away_from_desk_count"]))

    # ══ C. BREAKS ══════════════════════════════════════════════════════════
    print("\nC. BREAKS")
    print("-" * 70)
    rb = analyse_timeline(build([
        (600, "desk_1", "SITTING"),
        (420, "pantry", "STANDING"),      # 7 min in the pantry
        (600, "desk_1", "SITTING"),
    ]), DESK, BREAKS)
    print(f"   7 min in a BREAK zone -> break_seconds = {rb['break_seconds']:.0f}s, "
          f"{len(rb['breaks'])} break(s)")
    check("time in a BREAK zone is a break",
          len(rb["breaks"]) == 1 and rb["break_seconds"] >= BREAK_MIN_SECONDS,
          f"{rb['break_seconds']:.0f}s")

    rshort = analyse_timeline(build([
        (600, "desk_1", "SITTING"), (120, "pantry", "STANDING"),
        (600, "desk_1", "SITTING"),
    ]), DESK, BREAKS)
    print(f"   2 min in the pantry   -> {len(rshort['breaks'])} break(s) "
          f"(needs {BREAK_MIN_SECONDS/60:.0f} min)")
    check("a short pantry trip is not a break", len(rshort["breaks"]) == 0)

    # Absent from every camera.
    gap_samples = build([(300, "desk_1", "SITTING")])
    resume = gap_samples[-1]["observed_at"] + timedelta(seconds=900)   # 15 min gone
    gap_samples += [{"observed_at": resume + timedelta(seconds=i * SAMPLE),
                     "zone_id": "desk_1", "posture": "SITTING", "confidence": 0.9}
                    for i in range(60)]
    rg = analyse_timeline(gap_samples, DESK, BREAKS)
    print(f"   15 min unobserved     -> {len(rg['breaks'])} break(s), "
          f"{rg['break_seconds']:.0f}s")
    check("absence from all cameras is a break", len(rg["breaks"]) >= 1)
    check("...and does NOT count as desk time",
          rg["desk_seconds"] < 1000,
          f"{rg['desk_seconds']:.0f}s, not {300 + 900 + 300}s")
    check("...and is also a chair exit", rg["away_from_desk_count"] >= 1,
          str(rg["away_from_desk_count"]))

    # ══ D. FOCUS BLOCKS ════════════════════════════════════════════════════
    print("\nD. FOCUS BLOCKS")
    print("-" * 70)
    rf = analyse_timeline(build([
        (1500, "desk_1", "SITTING"),      # 25 min unbroken
        (120,  None,     "WALKING"),
        (1500, "desk_1", "SITTING"),      # another 25 min
    ]), DESK, BREAKS)
    print(f"   two 25-min seated runs -> {rf['focus_blocks']} focus block(s), "
          f"longest {rf['longest_focus_block']/60:.1f} min")
    check("two focus blocks found", rf["focus_blocks"] == 2, str(rf["focus_blocks"]))
    check("longest block is ~25 min",
          abs(rf["longest_focus_block"] - 1500) < 30,
          f"{rf['longest_focus_block']:.0f}s")

    rshort = analyse_timeline(build([(900, "desk_1", "SITTING")]), DESK, BREAKS)
    print(f"   one 15-min run         -> {rshort['focus_blocks']} "
          f"(needs {FOCUS_BLOCK_MIN_SECONDS/60:.0f} min)")
    check("15 min is not a focus block", rshort["focus_blocks"] == 0)

    # Standing up mid-run breaks it — that is the "low motion" requirement.
    rbroken = analyse_timeline(build([
        (700, "desk_1", "SITTING"), (30, "desk_1", "STANDING"),
        (700, "desk_1", "SITTING"),
    ]), DESK, BREAKS)
    print(f"   23 min split by standing -> {rbroken['focus_blocks']} focus block(s)")
    check("standing up interrupts a focus block", rbroken["focus_blocks"] == 0,
          "neither half reaches 20 min")

    print(f"\n   fragmentation index (blocks per desk hour): {rf['fragmentation_idx']:.2f}")
    check("fragmentation index is a rate, not a count",
          0 < rf["fragmentation_idx"] < 5, f"{rf['fragmentation_idx']:.2f}")

    # ══ E. THE SAMPLING TRAP ═══════════════════════════════════════════════
    print("\nE. THE SAMPLING TRAP — two distant rows are not four hours at a desk")
    print("-" * 70)
    trap = [
        {"observed_at": T0, "zone_id": "desk_1", "posture": "SITTING", "confidence": 0.9},
        {"observed_at": T0 + timedelta(hours=4), "zone_id": "desk_1",
         "posture": "SITTING", "confidence": 0.9},
    ]
    rt = analyse_timeline(trap, DESK, BREAKS)
    print(f"   two samples 4h apart -> desk time {rt['desk_seconds']:.0f}s "
          f"(gap rule: {MAX_SAMPLE_GAP_SECONDS:.0f}s)")
    check("a 4-hour gap is not credited as desk time",
          rt["desk_seconds"] < 60, f"{rt['desk_seconds']:.0f}s")

    # ══ F. THROUGH THE DATABASE ════════════════════════════════════════════
    print("\nF. END TO END THROUGH THE DATABASE")
    print("-" * 70)
    import sqlite3
    db = os.path.join(os.path.dirname(__file__), "..", "..", "workplace_analytics.db")
    ORG, EMP = "step8-org", "emp-step8"
    ensure_stats_table()

    from app.db.identity_writer import ensure_identity_table
    ensure_identity_table()
    c = sqlite3.connect(db)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    # Write the SAME ten-minute, three-departure timeline as real rows.
    for s in build(script):
        c.execute(
            "INSERT INTO identity_events (org_id, employee_id, camera_id, zone_id,"
            " track_id, identity_id, posture, confidence, method, observed_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ORG, EMP, "cam1", s["zone_id"], 1, "sess::1", s["posture"],
             0.95, "seat", s["observed_at"].isoformat(),
             datetime.now(timezone.utc).isoformat()))
    c.commit()
    c.close()

    res = aggregate_day_sync(day=T0.date(), org_id=ORG)
    print(f"   aggregated {res['rows_read']} events -> {res['employees']} employee row(s)")

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    row = dict(c.execute(
        "SELECT * FROM employee_day_stats WHERE org_id = ? AND employee_id = ?",
        (ORG, EMP)).fetchone())
    c.close()

    for k in ("stat_date", "present_minutes", "desk_minutes", "seated_minutes",
              "away_from_desk_count", "break_minutes", "longest_focus_block",
              "fragmentation_idx", "binding_confidence", "unknown_minutes"):
        print(f"     {k:<22} {row[k]}")

    check("the database row records awayFromDeskCount = 3  <<< THE NUMBER",
          row["away_from_desk_count"] == 3, str(row["away_from_desk_count"]))
    check("desk minutes are recorded", row["desk_minutes"] > 0, str(row["desk_minutes"]))
    check("binding confidence carried through",
          0.9 <= row["binding_confidence"] <= 1.0, str(row["binding_confidence"]))
    check("row is queued for the Postgres sync", row["synced_at"] is None)

    # Idempotent: re-running must update, never duplicate.
    aggregate_day_sync(day=T0.date(), org_id=ORG)
    c = sqlite3.connect(db)
    n = c.execute("SELECT COUNT(*) FROM employee_day_stats WHERE org_id = ?",
                  (ORG,)).fetchone()[0]
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    c.commit()
    c.close()
    check("re-running updates rather than duplicating", n == 1, f"{n} row(s)")

    # ══ G. UNKNOWN IS NOT ATTRIBUTED ═══════════════════════════════════════
    print("\nG. UNKNOWN TIME IS NEVER GIVEN TO ANYONE")
    print("-" * 70)
    c = sqlite3.connect(db)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    for s in build([(300, "desk_1", "SITTING")]):
        c.execute(
            "INSERT INTO identity_events (org_id, employee_id, camera_id, zone_id,"
            " track_id, identity_id, posture, confidence, method, observed_at, created_at)"
            " VALUES (?,NULL,?,?,?,?,?,?,?,?,?)",
            (ORG, "cam1", s["zone_id"], 9, "sess::9", s["posture"],
             0.0, "unknown", s["observed_at"].isoformat(),
             datetime.now(timezone.utc).isoformat()))
    c.commit()
    c.close()
    res = aggregate_day_sync(day=T0.date(), org_id=ORG)
    print(f"   {res['rows_read']} UNKNOWN events -> {res['employees']} employee row(s)")
    check("unattributed observations create no employee row",
          res["employees"] == 0, f"{res['employees']} rows")

    c = sqlite3.connect(db)
    c.execute("DELETE FROM identity_events    WHERE org_id = ?", (ORG,))
    c.execute("DELETE FROM employee_day_stats WHERE org_id = ?", (ORG,))
    c.commit()
    c.close()

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 8: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
