"""
Step 8, for real: the sync against the live Supabase project from backend/.env.

Creates a throw-away organisation, camera, zone and rule, writes rows through the
real SafetyWriter into a temporary SQLite file, runs the real sync, and looks at
what arrived in Postgres. Also exercises the zone mirror's new safetyType. Skips
with a message when Supabase is not configured or 025_safety.sql is not applied.
Everything it creates is removed again.

    venv/Scripts/python.exe backend/tests/test_safety_sync_live.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
from dotenv import load_dotenv                                           # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))
URL, SERVICE = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
if not (URL and SERVICE) or "your-" in SERVICE:
    print("SKIP: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set in backend/.env")
    sys.exit(0)

from supabase import create_client                                       # noqa: E402

svc = create_client(URL, SERVICE)
try:
    svc.table("safety_decisions").select("id").limit(1).execute()
except Exception as e:
    print(f"SKIP: the safety tables are not reachable ({str(e)[:100]}); apply 025_safety.sql first")
    sys.exit(0)

from sqlalchemy import create_engine, text                               # noqa: E402

from app.db.database import configure_sqlite                             # noqa: E402
from app.db.safety_models import ensure_safety_tables                    # noqa: E402
from app.db.safety_writer import SafetyWriter                            # noqa: E402
import app.db.safety_sync as sync                                        # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_sync_live_")
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


TAG = uuid.uuid4().hex[:8]
ORG, CAM, ZONE, RULE = (str(uuid.uuid4()) for _ in range(4))
CAM_NAME, SLUG = f"synccam-{TAG}", f"zone_{TAG}"
now = lambda d=0: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat(timespec="milliseconds")

path = os.path.join(TMP, "local.sqlite")
engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
configure_sqlite(engine, busy_timeout_ms=250)
ensure_safety_tables(engine)


def pg(table, **eq):
    q = svc.table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data


print("=" * 66)
print("SAFETY SYNC: LIVE SUPABASE")
print("=" * 66)
try:
    svc.table("organisations").insert({"id": ORG, "name": f"Sync {TAG}", "slug": f"sync-{TAG}", "plan": "ENTERPRISE",
                                       "updatedAt": now()}).execute()
    svc.table("cameras").insert({"id": CAM, "orgId": ORG, "name": CAM_NAME, "sourceType": "UPLOAD", "updatedAt": now()}).execute()
    svc.table("zones").insert({"id": ZONE, "orgId": ORG, "cameraId": CAM, "name": "Press line", "slug": SLUG,
                               "polygon": [[0, 0], [9, 0], [9, 9]], "safetyType": "RESTRICTED", "updatedAt": now()}).execute()
    svc.table("safety_rules").insert({"id": RULE, "orgId": ORG, "cameraId": CAM, "zoneId": ZONE, "name": "Helmet and vest",
                                      "type": "PPE_ENTRY", "params": {"required_ppe": ["helmet", "safety_vest"]}}).execute()

    print()
    print("  Zone mirror")
    from app.api.routers.zones import _mirror_zone_to_postgres
    mirror_slug = f"zone_m{TAG}"
    poly = [[1, 1], [8, 1], [8, 8]]
    _mirror_zone_to_postgres(ORG, CAM_NAME, mirror_slug, poly, "Mirror", "RESTRICTED")
    z = pg("zones", cameraId=CAM, slug=mirror_slug)
    check("a new zone is mirrored with its safety type", len(z) == 1 and z[0]["safetyType"] == "RESTRICTED", str(z)[:120])
    _mirror_zone_to_postgres(ORG, CAM_NAME, mirror_slug, poly, "Mirror", "EXIT")
    check("changing only the type updates Postgres (it used to return early when the name matched)",
          pg("zones", cameraId=CAM, slug=mirror_slug)[0]["safetyType"] == "EXIT")
    _mirror_zone_to_postgres(ORG, CAM_NAME, mirror_slug, poly, "Mirror", "WORKSTATION")
    check("a legacy type is stored as NULL (no safety meaning)", pg("zones", cameraId=CAM, slug=mirror_slug)[0]["safetyType"] is None)
    _mirror_zone_to_postgres(ORG, CAM_NAME, mirror_slug, poly, "Mirror", "RESTRICTED")
    _mirror_zone_to_postgres(ORG, CAM_NAME, mirror_slug, [[2, 2], [7, 2], [7, 7]], "Mirror", None)
    z = pg("zones", cameraId=CAM, slug=mirror_slug)[0]
    check("a reshaped polygon is mirrored, and a caller that passes no type leaves the type alone",
          z["polygon"] == [[2, 2], [7, 2], [7, 7]] and z["safetyType"] == "RESTRICTED")
    svc.table("zones").delete().eq("id", z["id"]).execute()

    print()
    print("  Sync")
    w = SafetyWriter(CAM_NAME, org_id=ORG, session_id=f"s{TAG}", engine=engine, retry_interval_s=0.0)
    d_flag = w.add_decision("PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION",
                            "Entered Press line with no helmet seen in 28 of 29 clear frames.", 0.97,
                            {"items": {"helmet": {"state": "ABSENT", "seen_fraction": 0.03}}, "wait_s": 4.0}, 12.5, now(),
                            zone_id=SLUG, rule_id=RULE, thresholds={"min_evidence_confidence": 0.6})
    d_ok = w.add_decision("PPE_ENTRY", "INFO", "trk-2", "COMPLIANT", "Entered Press line: helmet seen in 29 of 29 clear frames.",
                          0.97, {"items": {}}, 13.0, now(), zone_id=SLUG)
    d_unv = w.add_decision("PPE_ENTRY", "INFO", "trk-3", "UNVERIFIABLE", "Could not tell whether the helmet was seen.", 0.0,
                           {"items": {}}, 14.0, now(), zone_id=SLUG, rule_id=str(uuid.uuid4()))
    ev1 = w.add_event("ENTERED_ZONE", "trk-1", 12.0, now(), zone_id=SLUG, details={"first_sighting": False, "dwell_s": None})
    ev2 = w.add_event("LOST_IN_ZONE", "trk-9", 3.0, now(), zone_id=None)
    ev3 = w.add_event("EXITED_ZONE", "trk-1", 20.0, now(), zone_id="zone_not_drawn")        # cannot map yet
    w.flush_sync()
    out = sync.sync_safety_sync(engine=engine)
    check("two events and three decisions arrive; the one with an undrawn zone waits",
          out["synced_events"] == 2 and out["synced_decisions"] == 3 and out["unmapped"] == 1 and "error" not in out, str(out))
    got = {r["id"]: r for r in pg("safety_decisions", orgId=ORG)}
    check("all three decisions are in Postgres under their source ids", set(got) == {d_flag, d_ok, d_unv})
    f = got[d_flag]
    check("...with real camera, zone and rule uuids", f["cameraId"] == CAM and f["zoneId"] == ZONE and f["ruleId"] == RULE)
    check("...and the measured values intact",
          f["status"] == "POTENTIAL_VIOLATION" and f["confidence"] == 0.97 and f["videoTimeS"] == 12.5 and f["severity"] == "WARNING"
          and f["evidence"]["items"]["helmet"]["state"] == "ABSENT" and f["thresholds"] == {"min_evidence_confidence": 0.6}
          and f["reason"].startswith("Entered Press line with no helmet seen") and f["sessionKey"] == f"s{TAG}")
    check("a flag is PENDING review, the others need none",
          (f["reviewState"], got[d_ok]["reviewState"], got[d_unv]["reviewState"]) == ("PENDING", "NOT_REQUIRED", "NOT_REQUIRED"))
    check("an unknown rule id arrived as null rather than failing the batch", got[d_unv]["ruleId"] is None)
    evs = {r["eventUid"]: r for r in pg("safety_events", orgId=ORG)}
    check("events arrived, one of them with no zone", set(evs) == {ev1, ev2} and evs[ev2]["zoneId"] is None and evs[ev1]["zoneId"] == ZONE
          and evs[ev1]["details"]["first_sighting"] is False)

    print()
    print("  Again, and a review in between")
    check("a second run changes nothing", sync.sync_safety_sync(engine=engine)["synced_decisions"] == 0
          and len(pg("safety_decisions", orgId=ORG)) == 3)
    svc.table("safety_decisions").update({"reviewState": "CONFIRMED", "reviewedAt": now(), "reviewReasonCode": "CORRECT",
                                          "reviewNote": "seen on replay"}).eq("id", d_flag).execute()
    with engine.begin() as c:
        c.execute(text("UPDATE safety_decisions SET synced_at = NULL"))
        c.execute(text("UPDATE safety_events SET synced_at = NULL"))
    sync._rejected.clear()
    out = sync.sync_safety_sync(engine=engine)
    check("forcing a re-send of everything creates no duplicates",
          len(pg("safety_decisions", orgId=ORG)) == 3 and len(pg("safety_events", orgId=ORG)) == 2, str(out))
    f = pg("safety_decisions", id=d_flag)[0]
    check("a human review set in Postgres survives the re-send (ignore-duplicates, measured)",
          f["reviewState"] == "CONFIRMED" and f["reviewNote"] == "seen on replay" and f["reviewReasonCode"] == "CORRECT")

    print()
    print("  Late zone")
    svc.table("zones").insert({"orgId": ORG, "cameraId": CAM, "name": "Dock", "slug": "zone_not_drawn",
                               "polygon": [[0, 0], [9, 0], [9, 9]], "updatedAt": now()}).execute()
    sync._rejected.clear()
    sync._negative.clear()
    out = sync.sync_safety_sync(engine=engine)
    check("a zone drawn afterwards releases the waiting event", len(pg("safety_events", orgId=ORG)) == 3 and out["unmapped"] == 0, str(out))
    long_w = SafetyWriter(CAM_NAME, org_id=ORG, session_id=f"s{TAG}", engine=engine, retry_interval_s=0.0)
    d_long = long_w.add_decision("PPE_ENTRY", "INFO", "trk-4", "COMPLIANT", "r" * 650, 0.9, {"items": {}}, 1.0, now(), zone_id=SLUG)
    long_w.flush_sync()
    sync.sync_safety_sync(engine=engine)
    check("an over-long reason is stored cut to 500 characters instead of being refused",
          len(pg("safety_decisions", id=d_long)[0]["reason"]) == 500)
finally:
    try:
        svc.table("organisations").update({"deletedAt": now()}).eq("id", ORG).execute()
        svc.table("memberships").delete().eq("orgId", ORG).execute()
        svc.table("organisations").delete().eq("id", ORG).execute()
    except Exception as e:
        print(f"  cleanup failed: {str(e)[:120]}")
    left = svc.table("organisations").select("id").eq("id", ORG).execute().data
    print(f"  cleaned up (organisation left behind: {len(left)})")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f_ in FAILS:
        print("  -", f_)
    sys.exit(1)
print("ALL PASS")
