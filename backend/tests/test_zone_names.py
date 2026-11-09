"""
Zone names (found by the Step 1-10 test pass): two zones with one name made decisions and alerts
ambiguous, a control character in a name ended up inside decision sentences, and a long name was
accepted then cut. Drives the real HTTP layer with a stubbed Postgres mirror.

    venv/Scripts/python.exe backend/tests/test_zone_names.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient                    # noqa: E402
from sqlalchemy import text                                  # noqa: E402

from app.api.routers import zones as zr                      # noqa: E402
from app.db.database import SessionLocal                     # noqa: E402
from app.main import app                                     # noqa: E402

FAILS = []
ORG = "00000000-0000-0000-0000-00000000bbc1"
CAM, CAM2 = "probe_zn_cam", "probe_zn_cam2"
SQUARE = [[10, 10], [200, 10], [200, 150], [10, 150]]


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


zr._mirror_zone_to_postgres = lambda *a, **k: None
app.dependency_overrides[zr.require_zone_edit] = lambda: ORG
app.dependency_overrides[zr.current_org] = lambda: ORG
client = TestClient(app)


def cleanup():
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM zones WHERE camera_id IN (:a, :b)"), {"a": CAM, "b": CAM2})
        db.commit()
    finally:
        db.close()


def post(zone_id, name, camera=CAM, zone_type="RESTRICTED"):
    return client.post("/api/v1/zones/", json={"zone_id": zone_id, "camera_id": camera, "zone_name": name,
                                                "zone_type": zone_type, "polygon_coordinates": SQUARE})


cleanup()
try:
    print("ZONE NAMES")
    r = post("zn_1", "Walkway")
    check("a first zone is created", r.status_code == 201, r.text[:100])
    r = post("zn_2", "Walkway")
    check("a second zone with the same name on the same camera is refused (409)", r.status_code == 409 and "already exists" in r.json()["detail"], r.text[:120])
    r = post("zn_3", "  walkway  ")
    check("...whatever the case or the spaces around it", r.status_code == 409)
    r = post("zn_4", "Walkway", camera=CAM2)
    check("the same name on ANOTHER camera is fine", r.status_code == 201, r.text[:100])
    r = post("zn_1", "Walkway")
    check("re-saving a zone under its own name is fine (an update, not a duplicate)", r.status_code == 201, r.text[:100])
    r = post("zn_5", "Press line")
    check("a different name is fine", r.status_code == 201)
    r = post("zn_6", "Bad\u0000name")
    check("a name with a NUL character is refused (422)", r.status_code == 422 and "control" in r.json()["detail"], r.text[:100])
    r = post("zn_7", "Line\nbreak")
    check("a name with a line break is refused", r.status_code == 422)
    r = post("zn_8", "x" * 81)
    check("a name over 80 characters is refused, not cut", r.status_code == 422 and "80" in r.json()["detail"], r.text[:100])
    r = post("zn_9", "   ")
    check("a blank name is refused", r.status_code == 422)
    r = post("zn_10", "Zone éè 中文")
    check("ordinary unicode letters are fine", r.status_code == 201, r.text[:100])

    r = client.patch("/api/v1/zones/zn_5", json={"zone_name": "Walkway"})
    check("renaming a zone to another zone's name is refused (409)", r.status_code == 409, r.text[:100])
    r = client.patch("/api/v1/zones/zn_5", json={"zone_name": "Press line"})
    check("...but keeping its own name is fine", r.status_code == 200, r.text[:100])
    r = client.patch("/api/v1/zones/zn_5", json={"zone_name": "Bad\u0000"})
    check("a PATCH with a control character is refused", r.status_code == 422)
    r = client.patch("/api/v1/zones/zn_5", json={"zone_name": "Press floor", "zone_type": "EXIT"})
    check("a normal rename and type change works", r.status_code == 200 and r.json()["zone_name"] == "Press floor" and r.json()["zone_type"] == "EXIT", r.text[:120])
finally:
    cleanup()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
