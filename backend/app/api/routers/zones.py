# backend/app/api/routers/zones.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.db.models import ZoneModel
from app.db.schemas import ZoneCreate, ZoneResponse

router = APIRouter()

@router.get("/{camera_id}", response_model=List[ZoneResponse])
def get_zones_for_camera(camera_id: str, db: Session = Depends(get_db)):
    """Fetches all workstation ROI polygons for a specific camera"""
    zones = db.query(ZoneModel).filter(ZoneModel.camera_id == camera_id).all()
    return zones


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: str, db: Session = Depends(get_db)):
    """
    Removes one zone.

    Past activity_logs rows keep referencing this zone_id by design: they record
    where someone was measured at the time, and rewriting history because a zone
    was later redrawn would silently change past analytics.
    """
    zone = db.query(ZoneModel).filter(ZoneModel.zone_id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")

    db.delete(zone)
    db.commit()
    return None

@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_zone(zone: ZoneCreate, db: Session = Depends(get_db)):
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

    existing = db.query(ZoneModel).filter(ZoneModel.zone_id == zone.zone_id).first()
    if existing:
        existing.zone_name = zone.zone_name
        existing.polygon_coordinates = zone.polygon_coordinates
        existing.homography_matrix = zone.homography_matrix
        db.commit()
        db.refresh(existing)
        return existing

    db_zone = ZoneModel(**zone.model_dump())
    db.add(db_zone)
    db.commit()
    db.refresh(db_zone)
    return db_zone
