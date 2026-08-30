"""
Do zones drawn on one camera leak onto another?

The question is worth answering by experiment rather than by reading the
filter, because there are three places a camera can be lost: the write, the
read, and what the CV pipeline loads when it runs. A zone stored against the
wrong camera reads back "correctly" from a filtered query and still ends up
attributing one room's desk time to another room's polygon.
"""
import os, sys, json
sys.path.insert(0, os.path.abspath("backend"))
from dotenv import load_dotenv
load_dotenv("backend/.env")

from app.db.database import SessionLocal
from app.db.models import ZoneModel
from sqlalchemy import text

ORG = "0fedf5a4-8994-4f37-ac21-e3b2dad32639"
FAILS = []

def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok: FAILS.append(name)

s = SessionLocal()
# Clean slate for the two probe cameras.
s.execute(text("DELETE FROM zones WHERE camera_id IN ('probe_cam_A','probe_cam_B')"))
s.commit()

print("=" * 66)
print("ZONE ISOLATION BETWEEN CAMERAS")
print("=" * 66)
print()

# Two zones, same NAME and same SHAPE, on two different cameras. Identical in
# every way except the camera — which is the only thing that should separate
# them, and therefore the only honest test.
same_polygon = [[10, 10], [200, 10], [200, 150], [10, 150]]
for cam in ("probe_cam_A", "probe_cam_B"):
    s.add(ZoneModel(
        zone_id=f"probe_{cam}", camera_id=cam, org_id=ORG,
        zone_name="Desk 1", zone_type="WORKSTATION",
        polygon_coordinates=same_polygon))
s.commit()
print("  seeded 'Desk 1' on probe_cam_A and probe_cam_B (identical polygons)")
print()

def zones_for(cam):
    return [z.zone_id for z in s.query(ZoneModel)
            .filter(ZoneModel.camera_id == cam, ZoneModel.org_id == ORG).all()]

a, b = zones_for("probe_cam_A"), zones_for("probe_cam_B")
print(f"   probe_cam_A -> {a}")
print(f"   probe_cam_B -> {b}")
check("camera A sees exactly one zone", len(a) == 1, str(a))
check("camera B sees exactly one zone", len(b) == 1, str(b))
check("A does not see B's zone", "probe_probe_cam_B" not in a)
check("B does not see A's zone", "probe_probe_cam_A" not in b)

# A camera with no zones must see none — not "all of them".
none = zones_for("probe_cam_C_never_used")
print(f"   probe_cam_C (never used) -> {none}")
check("an unused camera sees no zones at all", none == [], str(none))

# And the path the CV pipeline actually uses.
print()
print("  what the PIPELINE loads (load_zones_for_camera):")
from app.api.routers.video_upload import load_zones_for_camera
pa = load_zones_for_camera("probe_cam_A", ORG)
pb = load_zones_for_camera("probe_cam_B", ORG)
print(f"   probe_cam_A -> {[z['zone_id'] for z in pa]}")
print(f"   probe_cam_B -> {[z['zone_id'] for z in pb]}")
check("the pipeline loads only camera A's zone for A",
      [z["zone_id"] for z in pa] == ["probe_probe_cam_A"], str(pa))
check("the pipeline loads only camera B's zone for B",
      [z["zone_id"] for z in pb] == ["probe_probe_cam_B"], str(pb))

# Deleting one must not touch the other.
s.query(ZoneModel).filter(ZoneModel.zone_id == "probe_probe_cam_A").delete()
s.commit()
print()
print(f"   after deleting A's zone: A={zones_for('probe_cam_A')} B={zones_for('probe_cam_B')}")
check("deleting A's zone leaves B's intact", zones_for("probe_cam_B") == ["probe_probe_cam_B"])

# Cleanup.
s.execute(text("DELETE FROM zones WHERE camera_id IN ('probe_cam_A','probe_cam_B')"))
s.commit()
s.close()

print()
print("=" * 66)
print("FAILED: " + ", ".join(FAILS) if FAILS else "ZONES ARE PER-CAMERA: ALL CHECKS PASSED")
sys.exit(1 if FAILS else 0)
