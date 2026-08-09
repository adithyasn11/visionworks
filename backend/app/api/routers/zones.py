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

@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_zone(zone: ZoneCreate, db: Session = Depends(get_db)):
    """Creates or updates a workstation ROI polygon boundary"""
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
