# backend/app/api/routers/cameras.py
#
# ═══════════════════════════════════════════════════════════════════════════
#  THE BUG THIS FILE HAD
# ═══════════════════════════════════════════════════════════════════════════
#
# Every endpoint here took `db: Session = Depends(get_db)` and nothing else.
# No token, no organisation, no role. Measured against the running backend:
#
#   GET  /api/v1/cameras/         -> 200, every camera of every tenant
#   POST /api/v1/cameras/         -> 201, anyone could register one
#   DEL  /api/v1/cameras/{id}     -> 204, anyone could delete any camera by id
#
# `CameraModel` has carried an `org_id` column the whole time — the scoping
# existed and simply was not used, which is the worst version of this mistake
# because the schema looks correct while the queries ignore it.
#
# The blast radius was smaller than it first appears: these routers speak to the
# LOCAL SQLite pipeline database, not Supabase, so no customer telemetry in
# Postgres was reachable. But the camera registry is real configuration, deletion
# was unauthenticated, and "the other database happened to hold less" is luck
# rather than design.
#
# ═══════════════════════════════════════════════════════════════════════════
#  WHAT IT DOES NOW
# ═══════════════════════════════════════════════════════════════════════════
#
# The same three-layer shape as zones.py, which was already correct:
#
#   current_org()        reads -> the caller's org, or None (fail closed: an
#                        unauthenticated reader gets an EMPTY LIST, never
#                        everyone's cameras)
#   require_camera_edit  writes -> 401 without an org, 403 without the
#                        `cameras.edit` capability (ADMIN or MANAGER)
#
# The organisation always comes from the verified access token, never from the
# request body. A client-supplied org_id would let anyone write a camera into
# any tenant, which is the same class of bug as the one above.
#
# NOTE ON ROWS WITH org_id IS NULL: they predate tenancy, or were written while
# the pipeline ran standalone with no Supabase configured. `== org_id` is never
# true for NULL in SQL, so they belong to nobody and are returned to nobody —
# the correct reading of data with no owner.

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import CameraModel
from app.db.schemas import CameraCreate, CameraResponse
from app.api.deps import resolve_org, resolve_org_role
from app.api.permissions import can, denial_message

router = APIRouter()


def current_org(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    """The caller's organisation id, or None. For reads."""
    return resolve_org(authorization)


def require_camera_edit(authorization: Optional[str] = Header(default=None)) -> str:
    """
    LAYER 2 for camera writes: the caller must hold `cameras.edit`.

    401 when there is no verified organisation, 403 when there is one but the
    role is read-only. The distinction matters — "sign in" and "ask an
    administrator" are different instructions.

    Mirrors require_zone_edit() in zones.py deliberately: two capabilities that
    behave differently for no reason is how a permission model becomes
    impossible to reason about.
    """
    org_id, role = resolve_org_role(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before managing cameras.",
        )
    if not can(role, "cameras.edit"):
        raise HTTPException(status_code=403, detail=denial_message("cameras.edit"))
    return org_id


@router.get("/", response_model=List[CameraResponse])
def get_cameras(
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """The caller's own cameras. Empty when there is no verified tenant."""
    if org_id is None:
        # Report nothing rather than everything. This is the line whose absence
        # was the bug.
        return []
    return db.query(CameraModel).filter(CameraModel.org_id == org_id).all()


@router.post("/", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def register_camera(
    camera: CameraCreate,
    db: Session = Depends(get_db),
    org_id: str = Depends(require_camera_edit),
):
    """Register an RTSP camera stream in the caller's organisation."""
    # Uniqueness is checked WITHIN the organisation, not globally. `camera_id`
    # is the table's primary key, so a genuine collision still has to be
    # refused — but two tenants both naming a camera "front_door" is normal,
    # and the message has to distinguish the two cases or it will read as a
    # bug to whichever tenant hits it second.
    existing = db.query(CameraModel).filter(CameraModel.camera_id == camera.camera_id).first()
    if existing is not None:
        if existing.org_id == org_id:
            raise HTTPException(
                status_code=400,
                detail="A camera with that id is already registered in your organisation.",
            )
        raise HTTPException(
            status_code=409,
            detail="That camera id is already in use. Choose a different one.",
        )

    payload = camera.model_dump()
    # The org is stamped from the TOKEN, overriding anything the body carried.
    # Trusting a client-supplied org_id here would let any authenticated user
    # write a camera into any tenant.
    payload["org_id"] = org_id

    db_camera = CameraModel(**payload)
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    org_id: str = Depends(require_camera_edit),
):
    """Delete a camera registration from the caller's own organisation."""
    # Filtered by BOTH id and org. Without the org clause this deleted any
    # camera in the database by id — the most damaging of the three original
    # endpoints, because it needed nothing but a guessable string.
    camera = (
        db.query(CameraModel)
        .filter(CameraModel.camera_id == camera_id, CameraModel.org_id == org_id)
        .first()
    )
    if camera is None:
        # 404 for "not yours" as well as "does not exist". Distinguishing them
        # would confirm which camera ids exist in other organisations.
        raise HTTPException(status_code=404, detail="Camera not found.")

    db.delete(camera)
    db.commit()
    return None
