# backend/app/api/routers/cameras.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.db.models import CameraModel
from app.db.schemas import CameraCreate, CameraResponse

router = APIRouter()

@router.get("/", response_model=List[CameraResponse])
def get_cameras(db: Session = Depends(get_db)):
    """Retrieves all registered RTSP camera streams"""
    cameras = db.query(CameraModel).all()
    return cameras

@router.post("/", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def register_camera(camera: CameraCreate, db: Session = Depends(get_db)):
    """Registers a new RTSP camera stream"""
    existing = db.query(CameraModel).filter(CameraModel.camera_id == camera.camera_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Camera ID already registered.")

    db_camera = CameraModel(**camera.model_dump())
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera

@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: str, db: Session = Depends(get_db)):
    """Deletes camera registration"""
    camera = db.query(CameraModel).filter(CameraModel.camera_id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found.")
    db.delete(camera)
    db.commit()
    return None
