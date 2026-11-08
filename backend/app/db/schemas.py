# backend/app/db/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class CameraBase(BaseModel):
    camera_id: str = Field(..., example="cam_01")
    name: str = Field(..., example="Floor 1 Workspace")
    rtsp_url: str = Field(..., example="rtsp://192.168.1.100/live")
    fps_target: Optional[int] = 8

class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    """
    What may be changed on an existing camera: the frame width the pose model
    sees. Name and rtsp_url identify the camera, and letting them drift would
    orphan the telemetry keyed to them.
    """
    inference_width: Optional[int] = Field(default=None, example=1280)


class CameraResponse(CameraBase):
    status: str
    created_at: datetime
    inference_width: Optional[int] = None

    class Config:
        from_attributes = True

class ZoneBase(BaseModel):
    zone_id: str = Field(..., example="restricted_01")
    camera_id: str = Field(..., example="cam_01")
    zone_name: str = Field(..., example="Press line")
    zone_type: Optional[str] = "GENERAL"
    polygon_coordinates: List[List[float]] # Array of [x, y]
    homography_matrix: Optional[List[List[float]]] = None

class ZoneCreate(ZoneBase):
    pass

class ZoneResponse(ZoneBase):
    class Config:
        from_attributes = True

class ActivityLogCreate(BaseModel):
    camera_id: str
    zone_id: str
    track_id: int
    posture_state: str
    activity_score: float
    dwell_duration_seconds: int

class ActivityLogResponse(ActivityLogCreate):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True
