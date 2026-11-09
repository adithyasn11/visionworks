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
ORIGINAL_MIRROR = zones_router._mirror_zone_to_postgres
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

# ── 6. The pipeline loads the type, in all three sockets ─────────────────────
print("6. Type reaches the SpatialEngine in every socket")
import re                                                    # noqa: E402
from app.api.routers import video_upload                     # noqa: E402
from app.api.routers.video_upload import load_zones_for_camera  # noqa: E402

source = open(video_upload.__file__, encoding="utf-8").read()
loads = re.findall(r"load_zones_for_camera\(zones_camera_id, org_id=org_id\)\n"
                   r"\s*spatial_engine = SpatialEngine\(zones_config=zones_config\)", source)
check("all three sockets (upload, live webcam, phone) load zones and build the engine the same way",
      len(loads) == 3, f"{len(loads)} of 3")
check("...and nothing else builds a SpatialEngine in the router",
      source.count("SpatialEngine(") == 3, str(source.count("SpatialEngine(")))

CAM2, OTHER_ORG = "probe_zone_types_load_cam", "00000000-0000-0000-0000-00000000aaab"
db = SessionLocal()
try:
    db.execute(text("DELETE FROM zones WHERE camera_id = :c"), {"c": CAM2})
    for zid, kind, org in [("zl_r", "RESTRICTED", ORG), ("zl_p", "PPE_CHECKPOINT", ORG),
                           ("zl_e", "EXIT", ORG), ("zl_a", "APPROACH", ORG),
                           ("zl_g", "GENERAL", ORG), ("zl_w", "WORKSTATION", ORG),
                           ("zl_n", None, ORG), ("zl_other", "RESTRICTED", OTHER_ORG)]:
        db.add(ZoneModel(zone_id=zid, camera_id=CAM2, org_id=org, zone_name=zid,
                         zone_type=kind, polygon_coordinates=SQUARE))
    db.commit()

    loaded = load_zones_for_camera(CAM2, org_id=ORG)
    engine = SpatialEngine(zones_config=loaded)
    for zid, expected in [("zl_r", "RESTRICTED"), ("zl_p", "PPE_CHECKPOINT"), ("zl_e", "EXIT"),
                          ("zl_a", "APPROACH"), ("zl_g", "GENERAL"),
                          ("zl_w", "WORKSTATION"), ("zl_n", "GENERAL")]:
        check(f"{zid}: engine.zone_type_of = {expected}", engine.zone_type_of(zid) == expected,
              str(engine.zone_type_of(zid)))
    check("another organisation's zone is not loaded", engine.zone_type_of("zl_other") is None)

    fallback = SpatialEngine(zones_config=load_zones_for_camera("probe_no_such_camera", org_id=ORG))
    check("no zones drawn -> DEFAULT_ZONES, typed GENERAL in the engine",
          [fallback.zone_type_of(z["zone_id"]) for z in DEFAULT_ZONES] == ["GENERAL", "GENERAL"])
finally:
    db.execute(text("DELETE FROM zones WHERE camera_id = :c"), {"c": CAM2})
    db.commit()
    db.close()

# ── 7. The Postgres mirror never sends a type (Step 7 adds it) ───────────────
print("7. Postgres mirror")
import json                                                  # noqa: E402
import urllib.request                                        # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._p, self.status = payload, 200

    def read(self):
        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_mirror(zones_rows, zone_name, zone_type=None):
    """Call the REAL mirror with a fake PostgREST; return the write requests."""
    writes = []

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if request.get_method() in ("POST", "PATCH"):
            writes.append((request.get_method(), json.loads(request.data.decode())))
            return _Resp([])
        if "/cameras?" in url:
            return _Resp([{"id": "cam-uuid"}])
        return _Resp(zones_rows)

    saved = (urllib.request.urlopen, os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
             os.environ.get("SUPABASE_URL"))
    urllib.request.urlopen = fake_urlopen
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
    os.environ["SUPABASE_URL"] = "http://postgrest.invalid"
    try:
        ORIGINAL_MIRROR(ORG, "cam", "zone_x", SQUARE, zone_name, zone_type)
    finally:
        urllib.request.urlopen = saved[0]
        for key, val in (("SUPABASE_SERVICE_ROLE_KEY", saved[1]), ("SUPABASE_URL", saved[2])):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    return writes


def keys_of(writes):
    out = set()
    for _method, payload in writes:
        for row in (payload if isinstance(payload, list) else [payload]):
            out |= set(row)
    return out


created = run_mirror([], "Press line", "RESTRICTED")
check("a new zone is mirrored with a POST", [m for m, _ in created] == ["POST"], str(created)[:80])
check("...carrying its safety type (Step 7: zones.safetyType)", created and created[0][1][0].get("safetyType") == "RESTRICTED", str(created)[:120])
legacy = run_mirror([], "Press line", "WORKSTATION")
check("a legacy type is stored as NULL (it has no safety meaning)", legacy and legacy[0][1][0].get("safetyType") is None)
renamed = run_mirror([{"id": "z1", "name": "old name", "safetyType": "RESTRICTED", "polygon": SQUARE}], "Press line", "RESTRICTED")
check("a renamed zone is mirrored with a PATCH", [m for m, _ in renamed] == ["PATCH"], str(renamed)[:80])
same_name = run_mirror([{"id": "z1", "name": "Press line", "safetyType": "RESTRICTED", "polygon": SQUARE}], "Press line", "RESTRICTED")
check("nothing changed: nothing is sent", same_name == [], str(same_name))
type_only = run_mirror([{"id": "z1", "name": "Press line", "safetyType": "RESTRICTED", "polygon": SQUARE}], "Press line", "EXIT")
check("a type-only change (same name) is now mirrored (it used to be skipped)", [m for m, _ in type_only] == ["PATCH"] and type_only[0][1].get("safetyType") == "EXIT", str(type_only))
no_type = run_mirror([{"id": "z1", "name": "Press line", "safetyType": "RESTRICTED", "polygon": SQUARE}], "Press line", None)
check("a caller that passes no type leaves the stored type alone", no_type == [], str(no_type))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ZONE TYPES: ALL CHECKS PASSED")
