# backend/app/db/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from datetime import datetime
from app.db.database import Base

class CameraModel(Base):
    __tablename__ = "cameras"

    camera_id = Column(String(64), primary_key=True, index=True)

    # Owning organisation (a Supabase `organisations.id` UUID, stored as text).
    #
    # Nullable because rows written before tenancy existed have no owner, and
    # because the pipeline still runs standalone with no Supabase configured.
    # Every org-scoped query filters `org_id == <the caller's org>`, so a NULL
    # here means "invisible to every tenant", which is the correct reading of
    # data that predates organisations.
    org_id = Column(String(64), nullable=True, index=True)

    name = Column(String(128), nullable=False)
    rtsp_url = Column(String(512), nullable=False)
    fps_target = Column(Integer, default=8)
    status = Column(String(32), default="ACTIVE")

    # DOOR or AREA (Step 10).
    #
    # A DOOR camera is the one place where a face is close enough to recognise:
    # the plan's optics table shows ~18 px between the eyes at 2 m and ~7 px at
    # 5 m, against the ~80 px face recognition needs. So face matching runs on
    # DOOR cameras and nowhere else — not as an optimisation, but because the
    # information is not in the frame anywhere else.
    role = Column(String(16), default="AREA")

    # What width frames are resized to before inference.
    #
    # 640 everywhere else, for 60 FPS throughput. A door camera keeps 1280,
    # because downscaling is exactly what destroys the face signal it exists to
    # capture. NULL means "use the default for this role".
    inference_width = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

class ZoneModel(Base):
    __tablename__ = "zones"

    zone_id = Column(String(64), primary_key=True, index=True)

    # See the note on CameraModel.org_id.
    org_id = Column(String(64), nullable=True, index=True)

    camera_id = Column(String(64), nullable=False, index=True)
    zone_name = Column(String(128), nullable=False)
    zone_type = Column(String(32), default="WORKSTATION") # WORKSTATION, MEETING, BREAK, CORRIDOR
    polygon_coordinates = Column(JSON, nullable=False) # Array of [x, y] points
    homography_matrix = Column(JSON, nullable=True) # 3x3 matrix array

class ActivityLogModel(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Owning organisation. This is the column that makes the analytics
    # multi-tenant: every endpoint filters on it, and a row with NULL belongs
    # to no organisation and is returned to nobody. See CameraModel.org_id.
    org_id = Column(String(64), nullable=True, index=True)

    camera_id = Column(String(64), nullable=False, index=True)
    zone_id = Column(String(64), nullable=False, index=True)
    track_id = Column(Integer, nullable=False)
    posture_state = Column(String(32), nullable=False) # SITTING, STANDING, WALKING, AWAY
    activity_score = Column(Float, nullable=False)
    dwell_duration_seconds = Column(Integer, nullable=False)

    # Floorplan position, in normalised 0..1 coordinates.
    #
    # Stored normalised rather than in pixels so the heatmap is independent of
    # the camera resolution and of the size the floorplan is rendered at: the
    # frontend multiplies by whatever box it draws into. Nullable because rows
    # written before this column existed have no position, and because a
    # centroid that falls outside the frame is dropped rather than clamped.
    floor_x = Column(Float, nullable=True)
    floor_y = Column(Float, nullable=True)
