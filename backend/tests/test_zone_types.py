"""
Safety zone types (Step 1).

A zone's type decides which safety rules can attach to it, so the failure that
matters is a zone silently ending up as the WRONG type: a typo that saves as a
restricted area with no rules, a re-save that resets a restricted area, or a
placeholder that looks like a hazard. Each is tested directly.

Runs against the real local SQLite database using throwaway probe zones, which
are removed afterwards (same approach as test_zone_isolation.py).

Run:  venv/Scripts/python.exe backend/tests/test_zone_types.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException                           # noqa: E402
from sqlalchemy import text                                 # noqa: E402

from app.api.routers import zones as zones_router           # noqa: E402
from app.api.routers.video_upload import DEFAULT_ZONES      # noqa: E402
from app.cv.spatial_engine import SpatialEngine             # noqa: E402
from app.db.database import SessionLocal                    # noqa: E402
from app.db.models import ZoneModel                         # noqa: E402
from app.db.schemas import ZoneCreate                       # noqa: E402

FAILS = []
ORG = "00000000-0000-0000-0000-00000000aaaa"
CAM = "probe_zone_types_cam"
SQUARE = [[10, 10], [200, 10], [200, 150], [10, 150]]


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def raises_422(fn):
    try:
        fn()
    except HTTPException as e:
        return e.status_code == 422
    return False


# The router mirrors zones to Postgres on every write. That is a network call
# and has nothing to do with what is tested here.
zones_router._mirror_zone_to_postgres = lambda *a, **k: None

# ── 1. The type lists ────────────────────────────────────────────────────────
print("1. Type lists")
check("safety types are exactly the five documented",
      zones_router.SAFETY_ZONE_TYPES ==
      {"PPE_CHECKPOINT", "APPROACH", "RESTRICTED", "EXIT", "GENERAL"})
check("legacy types are still accepted",
      zones_router.LEGACY_ZONE_TYPES <= zones_router.VALID_ZONE_TYPES)
check("safety and legacy sets do not overlap",
      not (zones_router.SAFETY_ZONE_TYPES & zones_router.LEGACY_ZONE_TYPES))

# ── 2. normalise_zone_type ───────────────────────────────────────────────────
print("2. normalise_zone_type")
norm = zones_router.normalise_zone_type
for t in sorted(zones_router.SAFETY_ZONE_TYPES):
    check(f"{t} accepted", norm(t) == t)
check("lower case is normalised", norm("restricted") == "RESTRICTED")
check("whitespace is trimmed", norm("  exit ") == "EXIT")
check("legacy value passes through", norm("WORKSTATION") == "WORKSTATION")
check("None -> GENERAL", norm(None) == "GENERAL")
check("blank -> GENERAL", norm("   ") == "GENERAL")
check("a typo is rejected, not stored", raises_422(lambda: norm("RESTRICED")))
check("an unknown type is rejected", raises_422(lambda: norm("BANANA")))
check("the default type has no rules attached (GENERAL)", zones_router.DEFAULT_ZONE_TYPE == "GENERAL")

# ── 3. SpatialEngine keeps the type ──────────────────────────────────────────
print("3. SpatialEngine")
eng = SpatialEngine([
    {"zone_id": "r", "zone_name": "Press", "polygon": SQUARE, "zone_type": "restricted"},
    {"zone_id": "n", "zone_name": "No type", "polygon": [[300, 10], [400, 10], [400, 100]]},
    {"zone_id": "bad", "zone_name": "Two points", "polygon": [[0, 0], [1, 1]], "zone_type": "EXIT"},
])
check("type is kept and upper-cased", eng.zone_type_of("r") == "RESTRICTED")
check("a zone with no type is GENERAL", eng.zone_type_of("n") == "GENERAL")
check("an unknown zone id has no type", eng.zone_type_of("nope") is None)
check("TRANSIT_ZONE (outside every polygon) has no type", eng.zone_type_of("TRANSIT_ZONE") is None)
check("a degenerate polygon is not loaded", eng.zone_type_of("bad") is None)
check("containment is unchanged by types",
      eng.check_zone_containment([50, 50]) == "r"
      and eng.check_zone_containment([900, 900]) == "TRANSIT_ZONE")

# ── 4. Placeholder zones are not hazards ─────────────────────────────────────
print("4. Placeholder zones")
check("DEFAULT_ZONES are all GENERAL",
      all(z["zone_type"] == "GENERAL" for z in DEFAULT_ZONES))
check("DEFAULT_ZONES carry no old-idea names",
      not any("orkstation" in z["zone_name"] or "esk" in z["zone_name"] for z in DEFAULT_ZONES))

# ── 5. Create / update through the router functions ──────────────────────────
print("5. Router create and update")
db = SessionLocal()
db.execute(text("DELETE FROM zones WHERE camera_id = :c"), {"c": CAM})
db.commit()


def make(zone_id, **kw):
    body = dict(zone_id=zone_id, camera_id=CAM, zone_name=zone_id,
                polygon_coordinates=SQUARE)
    body.update(kw)
    return ZoneCreate(**body)


def stored(zone_id):
    db.expire_all()
    row = db.query(ZoneModel).filter(ZoneModel.zone_id == zone_id).first()
    return row.zone_type if row else None


try:
    zones_router.create_or_update_zone(make("zt_a", zone_type="RESTRICTED"), db=db, org_id=ORG)
    check("create stores the given type", stored("zt_a") == "RESTRICTED")

    zones_router.create_or_update_zone(make("zt_b"), db=db, org_id=ORG)
    check("create with no type stores GENERAL", stored("zt_b") == "GENERAL")

    check("create with a typo is rejected",
          raises_422(lambda: zones_router.create_or_update_zone(
              make("zt_c", zone_type="RESTRICED"), db=db, org_id=ORG)))
    check("a rejected create writes nothing", stored("zt_c") is None)

    # Re-saving the SHAPE from a client that sends no type must not reset a
    # restricted area. (ZoneCreate defaults the field, so "not sent" is
    # visible only through model_fields_set.)
    reshaped = ZoneCreate.model_validate({
        "zone_id": "zt_a", "camera_id": CAM, "zone_name": "zt_a",
        "polygon_coordinates": [[0, 0], [50, 0], [50, 50]]})
    zones_router.create_or_update_zone(reshaped, db=db, org_id=ORG)
    check("re-save without a type keeps RESTRICTED", stored("zt_a") == "RESTRICTED")

    zones_router.create_or_update_zone(make("zt_a", zone_type="EXIT"), db=db, org_id=ORG)
    check("re-save with a type updates it", stored("zt_a") == "EXIT")

    zones_router.update_zone("zt_a", zones_router.ZoneUpdate(zone_type="ppe_checkpoint"),
                             db=db, org_id=ORG)
    check("PATCH accepts a safety type (any case)", stored("zt_a") == "PPE_CHECKPOINT")

    check("PATCH with a typo is rejected",
          raises_422(lambda: zones_router.update_zone(
              "zt_a", zones_router.ZoneUpdate(zone_type="RESTRICED"), db=db, org_id=ORG)))
    check("a rejected PATCH leaves the type alone", stored("zt_a") == "PPE_CHECKPOINT")

    zones_router.update_zone("zt_a", zones_router.ZoneUpdate(zone_name="Renamed"),
                             db=db, org_id=ORG)
    check("renaming does not touch the type", stored("zt_a") == "PPE_CHECKPOINT")

    zones_router.update_zone("zt_b", zones_router.ZoneUpdate(zone_type="WORKSTATION"),
                             db=db, org_id=ORG)
    check("a legacy type can still be set", stored("zt_b") == "WORKSTATION")
finally:
    db.execute(text("DELETE FROM zones WHERE camera_id = :c"), {"c": CAM})
    db.commit()
    db.close()

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ZONE TYPES: ALL CHECKS PASSED")
