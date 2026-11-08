"""
Safety zone types through the real HTTP layer (Step 1).

test_zone_types.py calls the router functions directly. This drives the same
code through FastAPI's request handling (routing, validation, response model),
because that is where a type can be lost or mangled on the way out: a
ZoneResponse that drops the field, a NULL stored by an older row, a client that
sends `"zone_type": null`.

Auth is replaced with dependency overrides (two organisations, so isolation can
be tested) and the Postgres mirror is stubbed, so nothing leaves this machine.
It uses the real local SQLite file with throwaway probe zones, removed in a
`finally`.

Run:  venv/Scripts/python.exe backend/tests/test_zone_types_http.py
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
ORG_A = "00000000-0000-0000-0000-00000000bbb1"
ORG_B = "00000000-0000-0000-0000-00000000bbb2"
CAM = "probe_zt_http_cam"
SQUARE = [[10, 10], [200, 10], [200, 150], [10, 150]]
TYPES = ["PPE_CHECKPOINT", "APPROACH", "RESTRICTED", "EXIT", "GENERAL"]
mirror_calls = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def as_org(org):
    """Make every zone endpoint see `org` as the verified organisation."""
    app.dependency_overrides[zr.require_zone_edit] = lambda: org
    app.dependency_overrides[zr.current_org] = lambda: org


def body(zone_id, **kw):
    b = {"zone_id": zone_id, "camera_id": CAM, "zone_name": zone_id,
         "polygon_coordinates": SQUARE}
    b.update(kw)
    return b


def types_on_camera(client):
    r = client.get(f"/api/v1/zones/{CAM}")
    return r.status_code, {z["zone_id"]: z["zone_type"] for z in r.json()}


def cleanup():
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM zones WHERE camera_id = :c"), {"c": CAM})
        db.commit()
    finally:
        db.close()


# No network: the mirror is best-effort and irrelevant here.
zones_mirror_original = zr._mirror_zone_to_postgres
zr._mirror_zone_to_postgres = lambda *a, **k: mirror_calls.append(a)

cleanup()
client = TestClient(app)            # no `with`: the aggregator loop is not started

try:
    as_org(ORG_A)

    # ── 1. create each type; the response and the next GET carry it ─────────
    print("1. Create each safety type")
    for t in TYPES:
        r = client.post("/api/v1/zones/", json=body(f"zt_{t.lower()}", zone_type=t))
        check(f"POST {t} -> 201", r.status_code == 201, str(r.status_code))
        check(f"POST {t} response carries the type", r.json().get("zone_type") == t,
              str(r.json().get("zone_type")))
    code, got = types_on_camera(client)
    check("GET returns 200", code == 200)
    check("GET returns every type as created",
          got == {f"zt_{t.lower()}": t for t in TYPES}, str(got))

    # ── 2. create with no type, and with an explicit null ───────────────────
    print("2. Missing and null types")
    r = client.post("/api/v1/zones/", json=body("zt_none"))
    check("create without a type -> 201 GENERAL",
          r.status_code == 201 and r.json()["zone_type"] == "GENERAL", r.text[:80])
    r = client.post("/api/v1/zones/", json=body("zt_null", zone_type=None))
    check("create with null type -> 201 GENERAL",
          r.status_code == 201 and r.json()["zone_type"] == "GENERAL", r.text[:80])

    # ── 3. typos are refused and nothing is written ─────────────────────────
    print("3. Typos")
    r = client.post("/api/v1/zones/", json=body("zt_typo", zone_type="RESTRICED"))
    check("POST typo -> 422", r.status_code == 422, str(r.status_code))
    check("a rejected POST wrote nothing", "zt_typo" not in types_on_camera(client)[1])
    r = client.patch("/api/v1/zones/zt_restricted", json={"zone_type": "RESTRICED"})
    check("PATCH typo -> 422", r.status_code == 422, str(r.status_code))
    check("a rejected PATCH leaves the type alone",
          types_on_camera(client)[1]["zt_restricted"] == "RESTRICTED")
    r = client.patch("/api/v1/zones/zt_restricted", json={"zone_type": "   "})
    check("PATCH with a blank type is refused, not read as GENERAL",
          r.status_code == 422, str(r.status_code))
    check("...and the restricted zone is still restricted",
          types_on_camera(client)[1]["zt_restricted"] == "RESTRICTED")
    r = client.post("/api/v1/zones/", json=body("zt_restricted", zone_type="RESTRICED"))
    check("re-saving an existing zone with a typo -> 422", r.status_code == 422)
    check("...and it kept its type",
          types_on_camera(client)[1]["zt_restricted"] == "RESTRICTED")

    # ── 4. type-only PATCH persists ─────────────────────────────────────────
    print("4. PATCH")
    r = client.patch("/api/v1/zones/zt_general", json={"zone_type": "exit"})
    check("type-only PATCH -> 200 and normalised", r.status_code == 200
          and r.json()["zone_type"] == "EXIT", r.text[:80])
    check("...and the next GET shows it", types_on_camera(client)[1]["zt_general"] == "EXIT")
    r = client.patch("/api/v1/zones/zt_exit", json={"zone_name": "Renamed"})
    check("a rename-only PATCH does not touch the type",
          r.status_code == 200 and types_on_camera(client)[1]["zt_exit"] == "EXIT")

    # ── 5. re-save without a type keeps it; an explicit null does too ───────
    print("5. Re-save")
    reshape = {"zone_id": "zt_restricted", "camera_id": CAM, "zone_name": "zt_restricted",
               "polygon_coordinates": [[0, 0], [50, 0], [50, 50]]}
    r = client.post("/api/v1/zones/", json=reshape)
    check("re-save without a type -> 201", r.status_code == 201, str(r.status_code))
    check("...keeps RESTRICTED", types_on_camera(client)[1]["zt_restricted"] == "RESTRICTED")
    r = client.post("/api/v1/zones/", json={**reshape, "zone_type": None})
    check("re-save with an explicit null keeps RESTRICTED (null is not a downgrade)",
          types_on_camera(client)[1]["zt_restricted"] == "RESTRICTED")
    r = client.post("/api/v1/zones/", json={**reshape, "zone_type": "EXIT"})
    check("re-save with a type changes it", types_on_camera(client)[1]["zt_restricted"] == "EXIT")

    # ── 6. legacy and NULL rows read back sanely ────────────────────────────
    print("6. Legacy and NULL rows")
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO zones (zone_id, org_id, camera_id, zone_name, zone_type, polygon_coordinates) "
            "VALUES ('zt_legacy', :o, :c, 'legacy', 'WORKSTATION', :p), "
            "       ('zt_null_row', :o, :c, 'null row', NULL, :p)"),
            {"o": ORG_A, "c": CAM, "p": '[[1,1],[9,1],[9,9]]'})
        db.commit()
    finally:
        db.close()
    code, got = types_on_camera(client)
    check("GET with legacy and NULL rows still 200", code == 200, str(code))
    check("a legacy type is returned as stored", got.get("zt_legacy") == "WORKSTATION", str(got.get("zt_legacy")))
    check("a NULL type is returned as GENERAL, not null", got.get("zt_null_row") == "GENERAL", str(got.get("zt_null_row")))
    r = client.patch("/api/v1/zones/zt_legacy", json={"zone_name": "legacy renamed"})
    check("a legacy zone can still be renamed", r.status_code == 200)
    r = client.patch("/api/v1/zones/zt_legacy", json={"zone_type": "RESTRICTED"})
    check("...and re-typed to a safety type",
          r.status_code == 200 and types_on_camera(client)[1]["zt_legacy"] == "RESTRICTED")

    # ── 7. another organisation cannot touch these zones ────────────────────
    print("7. Organisation isolation")
    as_org(ORG_B)
    r = client.patch("/api/v1/zones/zt_restricted", json={"zone_type": "GENERAL"})
    check("other org PATCH -> 404", r.status_code == 404, str(r.status_code))
    r = client.post("/api/v1/zones/", json=body("zt_restricted", zone_type="GENERAL"))
    check("other org POST on an existing id -> 409, not an overwrite",
          r.status_code == 409, str(r.status_code))
    r = client.delete("/api/v1/zones/zt_restricted")
    check("other org DELETE -> 404", r.status_code == 404, str(r.status_code))
    code, got = types_on_camera(client)
    check("other org sees none of them", code == 200 and got == {}, str(got))
    as_org(ORG_A)
    check("the owner's zone is unchanged after all that",
          types_on_camera(client)[1].get("zt_restricted") == "EXIT")

    # ── 8. no org, no zones ─────────────────────────────────────────────────
    print("8. Signed-out read")
    app.dependency_overrides[zr.current_org] = lambda: None
    r = client.get(f"/api/v1/zones/{CAM}")
    check("GET with no organisation -> 200 []", r.status_code == 200 and r.json() == [])

    check("the mirror was called for writes (and stubbed, so no network)", len(mirror_calls) > 0,
          str(len(mirror_calls)))
finally:
    app.dependency_overrides.clear()
    zr._mirror_zone_to_postgres = zones_mirror_original
    cleanup()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ZONE TYPES (HTTP): ALL CHECKS PASSED")
