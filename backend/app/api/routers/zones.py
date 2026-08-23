# backend/app/api/routers/zones.py
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db.database import get_db
from app.db.models import ZoneModel
from app.db.schemas import ZoneCreate, ZoneResponse
from app.api.deps import resolve_org, resolve_org_role
from app.api.permissions import can, denial_message

router = APIRouter()


# Zones are org-scoped for the same reason telemetry is. Two organisations both
# using the default camera name "live_webcam" would otherwise see and overwrite
# each other's polygons. The organisation comes from the caller's verified
# access token, never from the request body — a client-supplied org_id would
# let anyone write zones into any tenant. See api/deps.py.
def current_org(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    return resolve_org(authorization)


def require_org(authorization: Optional[str] = Header(default=None)) -> str:
    """Like current_org, but 401s instead of returning None — for writes."""
    org_id = resolve_org(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before editing zones.",
        )
    return org_id


def require_zone_edit(authorization: Optional[str] = Header(default=None)) -> str:
    """
    LAYER 2 for zone writes: the caller must hold `zones.edit`.

    401 when there is no verified organisation, 403 when there is one but the
    role is read-only — the distinction matters, because "sign in" and "ask an
    administrator" are different instructions.

    Not the boundary. `zone_insert/update/delete` all require
    `manage_org_ids()`, so a VIEWER is stopped by Postgres even if this check
    were removed. What this adds is a correct status code and a sentence,
    instead of a policy violation leaking out of the database layer.
    """
    org_id, role = resolve_org_role(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before editing zones.",
        )
    if not can(role, "zones.edit"):
        raise HTTPException(status_code=403, detail=denial_message("zones.edit"))
    return org_id

@router.get("/{camera_id}", response_model=List[ZoneResponse])
def get_zones_for_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """Fetches this organisation's ROI polygons for a specific camera"""
    if org_id is None:
        return []
    zones = (
        db.query(ZoneModel)
        .filter(ZoneModel.camera_id == camera_id, ZoneModel.org_id == org_id)
        .all()
    )
    return zones


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(
    zone_id: str,
    db: Session = Depends(get_db),
    org_id: str = Depends(require_zone_edit),
):
    """
    Removes one zone.

    Past activity_logs rows keep referencing this zone_id by design: they record
    where someone was measured at the time, and rewriting history because a zone
    was later redrawn would silently change past analytics.
    """
    # Scoped by org so one tenant cannot delete another's zone by guessing an
    # id. A zone belonging to someone else reads as "not found", which is the
    # right answer: confirming it exists would leak that another tenant uses it.
    zone = (
        db.query(ZoneModel)
        .filter(ZoneModel.zone_id == zone_id, ZoneModel.org_id == org_id)
        .first()
    )
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")

    db.delete(zone)
    db.commit()
    return None

@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_zone(
    zone: ZoneCreate,
    db: Session = Depends(get_db),
    org_id: str = Depends(require_zone_edit),
):
    """Creates or updates a workstation ROI polygon boundary"""
    # Shapely needs at least three points to build a polygon; anything less is
    # rejected here rather than being written and then silently skipped by the
    # spatial engine at load time.
    if not zone.polygon_coordinates or len(zone.polygon_coordinates) < 3:
        raise HTTPException(
            status_code=422,
            detail="A zone polygon needs at least 3 points.",
        )
    if any(len(point) != 2 for point in zone.polygon_coordinates):
        raise HTTPException(
            status_code=422,
            detail="Each polygon point must be an [x, y] pair.",
        )

    # Look the zone up within this organisation only. Matching on zone_id alone
    # would let one tenant overwrite another's polygon by reusing its id —
    # likely rather than exotic, because ids like "workstation_01" are the
    # obvious name for everyone's first zone.
    existing = (
        db.query(ZoneModel)
        .filter(ZoneModel.zone_id == zone.zone_id, ZoneModel.org_id == org_id)
        .first()
    )
    if existing:
        existing.zone_name = zone.zone_name
        existing.polygon_coordinates = zone.polygon_coordinates
        existing.homography_matrix = zone.homography_matrix
        db.commit()
        db.refresh(existing)
        return existing

    # `zone_id` is the PRIMARY KEY of this table, so it is unique across the
    # whole installation, not per organisation. If another tenant already owns
    # this id — and "workstation_01" is the obvious name for everyone's first
    # zone, so this is likely rather than exotic — the INSERT below would raise
    # an IntegrityError and surface as an opaque 500. Detect it and say what
    # actually happened instead.
    #
    # Namespacing the id per org would be the cleaner fix, but it would change
    # the id already written into every historical activity_logs row, silently
    # detaching past telemetry from its zone. Reporting the collision is the
    # honest option until that migration is done.
    taken = db.query(ZoneModel).filter(ZoneModel.zone_id == zone.zone_id).first()
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Zone id '{zone.zone_id}' is already in use. "
                "Choose a different id for this zone."
            ),
        )

    # org_id comes from the verified token, never from the request body.
    db_zone = ZoneModel(**zone.model_dump(), org_id=org_id)
    db.add(db_zone)
    db.commit()
    db.refresh(db_zone)
    return db_zone
