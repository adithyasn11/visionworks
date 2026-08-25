# backend/app/api/routers/zones.py
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db.database import get_db
from app.db.models import ZoneModel
from app.db.schemas import ZoneCreate, ZoneResponse
from app.api.deps import resolve_org, resolve_org_role
from app.api.permissions import can, denial_message

logger = logging.getLogger(__name__)
router = APIRouter()


def _mirror_zone_to_postgres(org_id: str, camera_name: str, zone_id: str, polygon: list) -> None:
    """
    Ensures a Postgres `zones` row exists NAMED `zone_id`, under the Postgres
    camera of name `camera_name`, in this org.

    WHY THE POSTGRES `name` HOLDS A `zone_id`, NOT THE HUMAN LABEL.
    `minute_aggregator.sync_to_postgres_sync()` calls
    `_resolve_ids(row["org_id"], row["camera_id"], row["zone_id"], cache)` —
    it passes the SQLite `zone_id` (e.g. "workstation_01"), not `zone_name`
    (e.g. "Workstation 1"), as the value `_resolve_ids` matches against
    Postgres `zones.name`. Mirroring under the human-readable label instead
    would create a Postgres row nothing ever looks up, and buckets would stay
    unmapped exactly as before. This function matches what the aggregator
    actually queries for, not what a user would recognise on a form — an
    inconsistency in the existing sync design, not a choice made here.

    WHY THIS EXISTS AT ALL. `_resolve_ids()` deliberately never invents a
    missing camera or zone (see that function's own docstring) — fabricating
    one would attribute telemetry to configuration the user never drew. That
    means a zone drawn here, in the local pipeline's zone editor, must ALSO
    exist in Postgres under the name the aggregator will look for, or every
    bucket for it stays unmapped forever with nothing to fix it. This is that
    missing other half: when a zone is actually saved here (a real user
    action, not a guess), mirror it into Postgres immediately. Best-effort —
    a Postgres outage or missing service-role key must not block saving the
    zone locally, which is what the pipeline actually depends on to run.
    """
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key or "your-" in key:
        return

    import json
    import urllib.parse
    import urllib.request
    from datetime import datetime, timezone

    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

    def get(table: str, params: dict):
        url = f"{base}/rest/v1/{table}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8") or "[]")

    try:
        cameras = get("cameras", {"select": "id", "orgId": f"eq.{org_id}", "name": f"eq.{camera_name}", "limit": "1"})
        if not cameras:
            # No Postgres camera by this name yet — nothing to attach the zone
            # to. Not an error: a user may draw zones before that camera is
            # registered through the onboarding flow.
            return
        camera_id = cameras[0]["id"]

        existing = get("zones", {"select": "id", "cameraId": f"eq.{camera_id}", "name": f"eq.{zone_id}", "limit": "1"})
        if existing:
            return

        # `polygon` is NOT NULL jsonb in Postgres; PostgREST wants the JSON
        # array encoded directly, not as a string. `updatedAt` is Prisma's
        # client-side @updatedAt with no DB default — the same trap
        # minute_aggregator.sync_to_postgres_sync() already documents.
        payload = json.dumps([{
            "orgId": org_id,
            "cameraId": camera_id,
            "name": zone_id,
            "polygon": polygon,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }]).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/rest/v1/zones",
            data=payload,
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status not in (200, 201, 204):
                logger.warning(f"Postgres zone mirror returned {response.status} for '{zone_name}'")
    except Exception as e:
        logger.warning(f"Could not mirror zone '{zone_id}' to Postgres: {type(e).__name__}: {e}")


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
        _mirror_zone_to_postgres(org_id, existing.camera_id, existing.zone_id, existing.polygon_coordinates)
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
    _mirror_zone_to_postgres(org_id, db_zone.camera_id, db_zone.zone_id, db_zone.polygon_coordinates)
    return db_zone
