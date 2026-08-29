# backend/app/api/routers/video_upload.py
"""
Video Upload, Playback & Ultra-Fast Live Webcam AI Processing Router.
Optimized for 60+ FPS Real-Time CUDA GPU Performance on NVIDIA RTX 4060.
Handles:
  - POST /upload  -> Save video file & trigger AI processing over WebSocket
  - GET  /samples -> List saved sample videos
  - GET  /status  -> Current processing status
  - WS   /process/{session_id} -> Single-Pass Real-Time AI video stream with playback controls
  - WS   /live_webcam -> Real-time live webcam capture processed through RTX 4060 GPU
  - WS   /process_webcam_frame -> Real-time browser camera stream processed through RTX 4060 GPU
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header
from typing import Optional
import os
import shutil
import cv2
import base64
import asyncio
import uuid
import json
import logging
import re
import time
import torch
import numpy as np

from app.cv.anonymizer import privacy_blur_default
from app.cv.frame_pipeline import process_detections
from app.db.activity_writer import ActivityLogWriter, persist_frame
from app.db.identity_writer import IdentityEventWriter, persist_identity_frame
from app.cv.appearance import get_extractor
from app.cv.identity_tracker import IdentityTracker
from app.db.minute_aggregator import aggregate_after_session
from app.api.deps import extract_token, resolve_org_role
from app.api.permissions import can, denial_message

logger = logging.getLogger(__name__)
router = APIRouter()


async def _resolve_session_org(websocket):
    """
    (org_id, role) for a processing socket, or (None, None).

    Running an analysis WRITES telemetry, so it is gated on `analysis.run`
    (ADMIN + MANAGER) rather than on mere membership — a VIEWER watching a live
    feed would be creating data they are not permitted to create.

    Returns (None, None) for an unverified caller. The socket still runs: the
    pipeline is usable standalone with no Supabase configured, and telemetry
    written without an org is visible to nobody. What a VIEWER must not get is
    an org-attributed write, which is what the role check prevents.
    """
    import asyncio
    token = extract_token(websocket)
    if not token:
        return None, None
    return await asyncio.to_thread(resolve_org_role, token)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
SAMPLE_VIDEOS_DIR = os.path.join(ROOT_DIR, "sample_videos")
os.makedirs(SAMPLE_VIDEOS_DIR, exist_ok=True)

# Global state: map session_id -> {"path": ..., "status": ..., "cancel": False}
_active_sessions: dict = {}

# Uploaded files are named "<8-hex-session-id>_<original name>". Only files
# matching that shape are ever auto-deleted, so videos a user placed in
# sample_videos/ by hand are left untouched.
_UPLOAD_NAME_RE = re.compile(r'^[0-9a-f]{8}_')
ORPHAN_UPLOAD_MAX_AGE_SECONDS = 6 * 3600


# Used when a camera has no zones drawn yet, so a first-time user still sees
# zone attribution working instead of everything falling into TRANSIT_ZONE.
DEFAULT_ZONES = [
    {
        "zone_id": "workstation_01",
        "zone_name": "Workstation 1",
        "polygon": [[100, 60], [480, 60], [480, 480], [100, 480]],
    },
    {
        "zone_id": "workstation_02",
        "zone_name": "Workstation 2",
        "polygon": [[500, 60], [900, 60], [900, 480], [500, 480]],
    },
]


def resolve_registered_camera(camera_id: Optional[str], org_id: Optional[str]) -> Optional[str]:
    """
    Validates a caller-supplied camera_id against this organisation's own
    registered cameras, returning it only if it genuinely exists and is theirs.

    WHY THIS MATTERS. Telemetry is synced to Postgres by matching the local
    camera_id/zone_id against a Postgres row BY NAME (see
    minute_aggregator._resolve_ids, which deliberately never invents a missing
    one). A session that runs under an auto-generated name like
    "upload_<session>" therefore produces buckets that can never be mapped to
    anything the user registered, and they stay unsynced forever — captured,
    but invisible to every dashboard.

    Returning the id only when it is both real and owned by the caller keeps
    this from becoming a way to write into another tenant's camera. None means
    "not registered, or none supplied", and the caller falls back to its
    previous ad-hoc id rather than failing the session outright — a video that
    processes with unmapped telemetry is still better than one that refuses to
    run at all.
    """
    if not camera_id or not org_id:
        return None

    from app.db.database import SessionLocal
    from app.db.models import CameraModel

    session = SessionLocal()
    try:
        row = (
            session.query(CameraModel)
            .filter(CameraModel.camera_id == camera_id, CameraModel.org_id == org_id)
            .first()
        )
        return row.camera_id if row is not None else None
    except Exception as e:
        logger.warning(f"Could not validate camera_id '{camera_id}': {e}")
        return None
    finally:
        session.close()


def identity_tracking_enabled() -> bool:
    """
    Whether to run appearance extraction and fragment stitching (Steps 5-6).

    On by default. Set IDENTITY_TRACKING=false to turn the whole identity path
    off — the pipeline then behaves exactly as it did after Step 4, still
    writing UNKNOWN identity_events, just without OSNet in the loop. That is
    the switch to reach for on a machine with no GPU headroom, or for a
    deployment that does not want per-person tracking at all.
    """
    raw = os.getenv("IDENTITY_TRACKING")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def resolve_initial_blur(websocket) -> bool:
    """
    The blur state a socket starts in.

    Precedence: an explicit `?blur=` query param, else `PRIVACY_BLUR_DEFAULT`
    from the environment. The query param is what lets one camera differ from
    the deployment default without a restart — a door camera opens its socket
    with `?blur=false` and keeps a usable face signal while every other camera
    still honours the deployment setting.

    An unparseable value is ignored rather than guessed at, so a typo falls
    back to the configured default instead of silently choosing a privacy
    posture nobody asked for.
    """
    raw = websocket.query_params.get("blur")
    if raw is not None:
        val = raw.strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        logger.warning(f"Ignoring unparseable ?blur={raw!r}; using PRIVACY_BLUR_DEFAULT")
    return privacy_blur_default()


def load_zones_for_camera(camera_id: str, org_id: str = None) -> list:
    """
    Reads a camera's zones from the database, in the shape SpatialEngine wants.

    Zones are drawn by the user against the displayed frame and stored in the
    same pixel space the CV pipeline works in, so no rescaling is needed here.

    TENANCY. When `org_id` is given, only that organisation's zones are loaded.
    Without this filter two organisations that both drew zones on a camera named
    "live_webcam" — a shared default name, so this is likely rather than
    exotic — would inherit each other's polygons, and their telemetry would be
    attributed to zones they never drew.

    Zones with org_id IS NULL predate tenancy and are skipped for the same
    reason activity_logs rows are: they belong to no organisation.

    Falls back to DEFAULT_ZONES when nothing has been drawn for this camera, and
    also on a database error: losing zone attribution should degrade the
    analytics, not take down a live video session.
    """
    from app.db.database import SessionLocal
    from app.db.models import ZoneModel

    session = SessionLocal()
    try:
        query = session.query(ZoneModel).filter(ZoneModel.camera_id == camera_id)
        if org_id is not None:
            query = query.filter(ZoneModel.org_id == org_id)
        rows = query.all()
        zones = [
            {
                "zone_id": row.zone_id,
                "zone_name": row.zone_name,
                "polygon": row.polygon_coordinates,
            }
            for row in rows
            if row.polygon_coordinates and len(row.polygon_coordinates) >= 3
        ]
        return zones or DEFAULT_ZONES
    except Exception as e:
        logger.warning(f"Could not load zones for '{camera_id}', using defaults: {e}")
        return DEFAULT_ZONES
    finally:
        session.close()


def _purge_orphaned_uploads(max_age_seconds: int = ORPHAN_UPLOAD_MAX_AGE_SECONDS):
    """
    Deletes uploaded videos that are older than max_age_seconds and are not
    referenced by any live session. Without this, an upload whose WebSocket is
    never opened stays on disk forever.
    """
    live_paths = {s.get("path") for s in _active_sessions.values()}
    now = time.time()
    for name in os.listdir(SAMPLE_VIDEOS_DIR):
        if not _UPLOAD_NAME_RE.match(name):
            continue
        path = os.path.join(SAMPLE_VIDEOS_DIR, name)
        if path in live_paths or not os.path.isfile(path):
            continue
        try:
            if (now - os.path.getmtime(path)) > max_age_seconds:
                os.remove(path)
                logger.info(f"Purged orphaned upload: {name}")
        except OSError as e:
            logger.warning(f"Could not purge orphaned upload {name}: {e}")


def require_analysis_run(authorization: Optional[str] = Header(default=None)) -> str:
    """
    LAYER 2 for the video endpoints: the caller must hold `analysis.run`.

    THE BUG THIS FIXES. /upload, /samples and /status took no auth dependency
    at all — measured against the running backend, an unauthenticated caller
    got 200 from all three and could POST a file onto the server's disk.

    Running analysis is a WRITE, not a read: it produces telemetry rows. So it
    is gated on `analysis.run` (ADMIN or MANAGER), matching the WebSocket that
    processes the upload and the capability the dashboard already checks before
    drawing the button. A VIEWER watching a live feed would be creating data.

    401 with no verified organisation, 403 with one but a read-only role.
    """
    org_id, role = resolve_org_role(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before running analysis.",
        )
    if not can(role, "analysis.run"):
        raise HTTPException(status_code=403, detail=denial_message("analysis.run"))
    return org_id


@router.get("/samples")
def list_sample_videos(org_id: str = Depends(require_analysis_run)):
    """Lists all available video files stored in sample_videos/"""
    files = [
        f for f in os.listdir(SAMPLE_VIDEOS_DIR)
        if f.lower().endswith(('.mp4', '.avi', '.webm', '.mov', '.mkv'))
    ]
    return {"sample_videos": files}


@router.get("/status")
def get_processing_status(org_id: str = Depends(require_analysis_run)):
    """
    Active sessions belonging to the CALLER's organisation.

    It used to return every session id on the server regardless of who asked,
    which told one tenant how much another was processing — and a session id is
    the key the processing WebSocket accepts.
    """
    mine = {
        sid: meta for sid, meta in _active_sessions.items()
        if meta.get("org_id") == org_id
    }
    return {"active_sessions": len(mine), "sessions": list(mine.keys())}


@router.post("/upload")
async def upload_video_file(
    file: UploadFile = File(...),
    camera_id: Optional[str] = Form(default=None),
    org_id: str = Depends(require_analysis_run),
):
    """
    Uploads a video file to sample_videos/ and returns a session_id.
    The frontend then connects via WS /api/v1/video/process/{session_id} to receive frames.

    `camera_id` is optional and, when given, must name a camera already
    registered in the caller's own organisation — see
    resolve_registered_camera(). Omitting it keeps the previous behaviour of an
    ad-hoc per-session camera, whose telemetry will not sync to the dashboard.
    """
    allowed_exts = ('.mp4', '.avi', '.webm', '.mov', '.mkv')
    if not file.filename or not file.filename.lower().endswith(allowed_exts):
        raise HTTPException(status_code=400, detail=f"Invalid format. Supported: {allowed_exts}")

    # Strip any directory components: a filename like "../../x.mp4" must never
    # be able to escape SAMPLE_VIDEOS_DIR and write elsewhere on disk.
    safe_name = os.path.basename(file.filename.replace("\\", "/"))
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', safe_name).lstrip('.')
    if not safe_name.lower().endswith(allowed_exts):
        raise HTTPException(status_code=400, detail=f"Invalid format. Supported: {allowed_exts}")

    session_id = str(uuid.uuid4())[:8]
    save_path = os.path.join(SAMPLE_VIDEOS_DIR, f"{session_id}_{safe_name}")

    try:
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")

    # See resolve_registered_camera(). None (unregistered id, or none given)
    # falls back to the ad-hoc "upload_<session_id>" camera at process time.
    resolved_camera_id = resolve_registered_camera(camera_id, org_id)
    if camera_id and not resolved_camera_id:
        logger.warning(
            f"Session {session_id}: camera_id '{camera_id}' is not registered "
            f"in this organisation; telemetry will not sync to the dashboard."
        )

    _active_sessions[session_id] = {
        # The owning organisation, taken from the verified token — never from
        # the request. Without it a session id is a bearer token anyone who
        # guesses it can process against.
        "org_id": org_id,
        "camera_id": resolved_camera_id,
        "filename": safe_name,
        "path": save_path,
        "status": "READY",
        "cancel": False,
        "created_at": time.time()
    }

    # Reclaim uploads that were never processed (client never opened the
    # WebSocket, or the server restarted mid-session) so they don't accumulate.
    _purge_orphaned_uploads()

    logger.info(f"Video uploaded: {safe_name} -> session {session_id}")
    return {
        "status": "SUCCESS",
        "session_id": session_id,
        "filename": safe_name,
        "message": "Video ready. Connect WebSocket at /api/v1/video/process/{session_id}"
    }


@router.websocket("/process/{session_id}")
async def process_video_websocket(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint using Single-Pass YOLOv8 Pose AI Engine on NVIDIA RTX 4060 GPU.
    Runs at 60+ FPS real-time speed with Play, Pause, Seek playback controls.
    """
    await websocket.accept()

    # The organisation is derived from the caller's verified access token, not
    # from anything they assert about themselves. None means the telemetry this
    # session produces is written with no owner and will be visible to nobody —
    # see api/deps.py.
    org_id, role = await _resolve_session_org(websocket)
    if org_id is not None and not can(role, "analysis.run"):
        # A VIEWER may read this org's measurements but not create new ones.
        # Refusing here rather than silently writing unattributed rows: the
        # caller asked to run an analysis, and they cannot.
        # send_json() immediately followed by close() races: the frame can be
        # dropped before delivery, and the client sees only an abnormal 1006
        # with no reason. Measured — the refusal worked but was unexplained.
        # Sending, then closing with an explicit policy-violation code (1008)
        # and the reason in the handshake, means the client learns WHY even if
        # the JSON frame loses the race.
        reason = denial_message("analysis.run")
        try:
            await websocket.send_json({"error": reason})
        except Exception:
            pass
        await websocket.close(code=1008, reason=reason)
        return
    if org_id is None:
        logger.info(f"Session {session_id}: no verified organisation; telemetry will not be attributed.")

    if session_id not in _active_sessions:
        await websocket.send_json({"error": f"Session '{session_id}' not found. Upload a video first."})
        await websocket.close()
        return

    session = _active_sessions[session_id]

    # THE SESSION MUST BELONG TO THE CALLER'S ORGANISATION.
    #
    # The role check above asks "may this person run analysis at all". It does
    # NOT ask "is this their session" — so a MANAGER of org A could process a
    # video org B had uploaded, simply by guessing the eight-character id. The
    # frames would be theirs to watch and the telemetry would be attributed to
    # whoever connected.
    #
    # Sessions uploaded before this check existed carry no `org_id`; they are
    # treated as unowned and refused rather than granted to the first caller.
    # The same message is used for "not yours" and "not found", so a caller
    # cannot probe which session ids exist in other organisations.
    session_org = session.get("org_id")
    if session_org != org_id or org_id is None:
        logger.warning(
            f"Session {session_id}: refused — belongs to {session_org!r}, caller is {org_id!r}"
        )
        await websocket.send_json({"error": f"Session '{session_id}' not found. Upload a video first."})
        await websocket.close(code=1008, reason="Session not found")
        return
    video_path = session["path"]
    session["status"] = "PROCESSING"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Session {session_id} starting single-pass AI pipeline on device: {device}")

    try:
        from app.cv.pose_estimator import PostureEstimator
        from app.cv.spatial_engine import SpatialEngine
        from app.cv.activity_aggregator import ActivityAggregator
        from app.cv.anonymizer import PrivacyAnonymizer

        # Prefer the caller's registered camera (resolved at upload time), so
        # this session's telemetry carries a name the Postgres sync can map.
        # Falls back to the ad-hoc per-session id when none was supplied.
        effective_camera_id = session.get("camera_id") or f"upload_{session_id}"

        pose_engine = PostureEstimator(pose_model_path="yolov8m-pose.pt", conf_thresh=0.35, device=device)
        zones_config = load_zones_for_camera(effective_camera_id, org_id=org_id)
        spatial_engine = SpatialEngine(zones_config=zones_config)
        activity_aggregator = ActivityAggregator()
        anonymizer = PrivacyAnonymizer()
        # Telemetry for this session is attributed to the uploaded file, so rows
        # from different videos stay distinguishable in activity_logs.
        activity_writer = ActivityLogWriter(camera_id=effective_camera_id, org_id=org_id)
        # The per-person analogue, written alongside the anonymous path
        # rather than replacing it. Step 4 writes every row as UNKNOWN;
        # Steps 5-14 fill in employee_id without changing this wiring.
        identity_writer = IdentityEventWriter(
            camera_id=effective_camera_id, org_id=org_id, session_id=session_id
        )

        # Steps 5-6: appearance signatures and fragment stitching. The
        # extractor is process-wide (the model is stateless); the tracker
        # is per session, so its identity namespace and its gallery cannot
        # leak between concurrent videos.
        identity_on = identity_tracking_enabled()
        appearance_extractor = get_extractor() if identity_on else None
        identity_tracker = (
            IdentityTracker(session_id=session_id) if identity_on else None
        )

    except Exception as e:
        logger.error(f"AI init error: {e}")
        await websocket.send_json({"error": f"Failed to initialize AI pipeline: {str(e)}"})
        await websocket.close()
        session["status"] = "ERROR"
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        await websocket.send_json({"error": f"Cannot open video: {video_path}"})
        await websocket.close()
        session["status"] = "ERROR"
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / max(fps, 1.0)

    await websocket.send_json({
        "type": "INIT",
        "message": f"Single-pass AI engine active on RTX 4060 GPU ({device.upper()}). 60 FPS mode.",
        "total_frames": total_frames,
        "source_fps": fps,
        "duration_seconds": duration_sec,
        "device": device
    })

    control_state = {
        "paused": False,
        "seek_frame": None
    }
    # Starting state comes from PRIVACY_BLUR_DEFAULT (or this socket's ?blur=),
    # not from a hardcoded True: the identity pipeline needs an unblurred face
    # signal in development, while a deployment that wants privacy-by-default
    # sets the env var. The set_privacy_blur control below still overrides this
    # at any point in the session.
    privacy_state = {"blur": resolve_initial_blur(websocket)}

    async def receive_controls():
        try:
            while True:
                msg_text = await websocket.receive_text()
                try:
                    data = json.loads(msg_text)
                    action = data.get("action")
                    if action == "pause":
                        control_state["paused"] = True
                    elif action in ("play", "resume"):
                        control_state["paused"] = False
                    elif action == "set_privacy_blur":
                        privacy_state["blur"] = bool(data.get("enabled", privacy_state["blur"]))
                    elif action == "seek":
                        target_pct = data.get("pct")
                        if target_pct is not None:
                            tf = int((target_pct / 100.0) * total_frames)
                        else:
                            tf = int(data.get("frame", 0))
                        control_state["seek_frame"] = max(0, min(total_frames - 1, tf))
                except Exception as ex:
                    logger.warning(f"Control parse error: {ex}")
        except Exception:
            pass

    control_task = asyncio.create_task(receive_controls())

    frame_idx = 0
    processed_count = 0

    try:
        while True:
            if session.get("cancel"):
                break

            if control_state["seek_frame"] is not None:
                target_f = control_state["seek_frame"]
                control_state["seek_frame"] = None
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)
                frame_idx = target_f

            if control_state["paused"]:
                await asyncio.sleep(0.1)
                continue

            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1

            # Scale to 640px width for 60 FPS real-time throughput
            h, w = frame.shape[:2]
            if w > 640:
                scale = 640.0 / w
                frame = cv2.resize(frame, (640, int(h * scale)))

            # Collect recent motion speeds per track ID to validate WALKING vs STANDING
            motion_speeds = {tid: activity_aggregator.get_recent_motion_speed(tid) for tid in activity_aggregator.track_history}
            detections = pose_engine.process_frame_single_pass(frame, motion_speeds=motion_speeds, imgsz=480)

            # One shared implementation — see cv/frame_pipeline.py. Identity
            # resolution (Steps 5-14) goes inside that function, once, rather
            # than in three places that could drift apart.
            tracked_entities, zone_summary = process_detections(
                detections,
                frame,
                spatial_engine,
                activity_aggregator,
                anonymizer,
                zones_config,
                blur_enabled=privacy_state["blur"],
                appearance_extractor=appearance_extractor,
                identity_tracker=identity_tracker,
            )

            # Persist sampled telemetry. Runs off the event loop and swallows its
            # own failures, so it cannot stall or break the video stream.
            await persist_frame(activity_writer, tracked_entities)
            await persist_identity_frame(identity_writer, tracked_entities)

            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            frame_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')

            processed_count += 1
            current_time_sec = frame_idx / max(fps, 1.0)

            payload = {
                "type": "FRAME",
                "frame_index": frame_idx,
                "processed_count": processed_count,
                "total_frames": total_frames,
                "current_time_seconds": round(current_time_sec, 1),
                "duration_seconds": round(duration_sec, 1),
                "progress_pct": round((frame_idx / max(total_frames, 1)) * 100, 1),
                "frame_base64": frame_b64,
                "tracked_entities": tracked_entities,
                "total_detected": len(tracked_entities),
                "zone_occupancy": zone_summary,
                "is_paused": control_state["paused"],
                "privacy_blur": privacy_state["blur"]
            }

            await websocket.send_json(payload)
            await asyncio.sleep(0.001)

        session["status"] = "DONE"
        logger.info(
            f"Session {session_id}: {processed_count} frames processed, "
            f"{activity_writer.rows_written} telemetry rows, "
            f"{identity_writer.rows_written} identity rows written. "
            f"stitching: {identity_tracker.stats() if identity_tracker else 'off'}"
        )
        await websocket.send_json({
            "type": "COMPLETE",
            "message": f"Video analysis complete. Processed {processed_count} frames.",
            "total_processed": processed_count,
            "telemetry_rows_written": activity_writer.rows_written,
            "identity_rows_written": identity_writer.rows_written,
            # The Step 6 stitch-rate metric, surfaced to the client so the
            # dashboard can show how fragmented the tracking was.
            "identity_stats": identity_tracker.stats() if identity_tracker else None
        })

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from session {session_id}")
    except Exception as e:
        logger.error(f"Error in video processing session {session_id}: {e}")
        try:
            await websocket.send_json({"type": "ERROR", "error": str(e)})
        except Exception:
            pass
    finally:
        control_task.cancel()
        cap.release()
        session["status"] = "DONE"
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass
        _active_sessions.pop(session_id, None)
        logger.info(f"Session {session_id} closed and cleaned up.")

        # Roll this run's telemetry into minute buckets. Fire-and-forget: the
        # helper sleeps out the settle window first (so the final samples land
        # in a closed minute), and the session must not block on it. The 60s
        # timer would eventually catch these rows anyway; this just means a
        # short upload's numbers appear promptly rather than up to a minute
        # later.
        if getattr(activity_writer, "org_id", None):
            asyncio.create_task(
                aggregate_after_session(activity_writer.camera_id, activity_writer.org_id)
            )


@router.websocket("/live_webcam")
async def live_webcam_websocket(websocket: WebSocket):
    """
    Ultra-Fast Real-Time Live Webcam AI Endpoint on NVIDIA RTX 4060 GPU.
    Runs single-pass detection, tracking, and pose keypoint estimation at 60 FPS.
    """
    await websocket.accept()
    org_id, role = await _resolve_session_org(websocket)
    if org_id is not None and not can(role, "analysis.run"):
        # send_json() immediately followed by close() races: the frame can be
        # dropped before delivery, and the client sees only an abnormal 1006
        # with no reason. Measured — the refusal worked but was unexplained.
        # Sending, then closing with an explicit policy-violation code (1008)
        # and the reason in the handshake, means the client learns WHY even if
        # the JSON frame loses the race.
        reason = denial_message("analysis.run")
        try:
            await websocket.send_json({"error": reason})
        except Exception:
            pass
        await websocket.close(code=1008, reason=reason)
        return
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # See resolve_registered_camera(): a caller may pass ?camera_id=<own camera>
    # so this session's telemetry maps to something the dashboard can read.
    requested_camera_id = websocket.query_params.get("camera_id")
    effective_camera_id = resolve_registered_camera(requested_camera_id, org_id) or "live_webcam"

    try:
        from app.cv.pose_estimator import PostureEstimator
        from app.cv.spatial_engine import SpatialEngine
        from app.cv.activity_aggregator import ActivityAggregator
        from app.cv.anonymizer import PrivacyAnonymizer

        pose_engine = PostureEstimator(pose_model_path="yolov8m-pose.pt", conf_thresh=0.35, device=device)
        zones_config = load_zones_for_camera(effective_camera_id, org_id=org_id)
        spatial_engine = SpatialEngine(zones_config=zones_config)
        activity_aggregator = ActivityAggregator()
        anonymizer = PrivacyAnonymizer()
        activity_writer = ActivityLogWriter(camera_id=effective_camera_id, org_id=org_id)
        # The per-person analogue, written alongside the anonymous path
        # rather than replacing it. Step 4 writes every row as UNKNOWN;
        # Steps 5-14 fill in employee_id without changing this wiring.
        identity_writer = IdentityEventWriter(
            camera_id=effective_camera_id, org_id=org_id, session_id=None
        )

        # Steps 5-6: appearance signatures and fragment stitching. The
        # extractor is process-wide (the model is stateless); the tracker
        # is per session, so its identity namespace and its gallery cannot
        # leak between concurrent videos.
        identity_on = identity_tracking_enabled()
        appearance_extractor = get_extractor() if identity_on else None
        identity_tracker = (
            IdentityTracker(session_id=None) if identity_on else None
        )

    except Exception as e:
        await websocket.send_json({"error": f"Failed to initialize AI models: {str(e)}"})
        await websocket.close()
        return

    cap = None
    for cam_idx in (0, 1, 2):
        temp_cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
        if temp_cap.isOpened():
            ret, _ = temp_cap.read()
            if ret:
                cap = temp_cap
                logger.info(f"Opened webcam at index {cam_idx}")
                break
            else:
                temp_cap.release()

    if cap is None or not cap.isOpened():
        await websocket.send_json({"error": "No physical camera detected on server. Use Browser Camera capture fallback."})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "INIT",
        "message": f"Single-pass 60 FPS Live Webcam active on RTX 4060 GPU ({device.upper()}).",
        "device": device,
        "is_live_cam": True
    })

    control_state = {"stop": False}
    privacy_state = {"blur": resolve_initial_blur(websocket)}

    async def listen_control():
        try:
            while True:
                msg = await websocket.receive_text()
                data = json.loads(msg)
                action = data.get("action")
                if action == "stop":
                    control_state["stop"] = True
                    break
                if action == "set_privacy_blur":
                    privacy_state["blur"] = bool(data.get("enabled", privacy_state["blur"]))
        except Exception:
            pass

    listen_task = asyncio.create_task(listen_control())
    start_time = time.time()
    frame_idx = 0

    try:
        while not control_state["stop"]:
            ret, frame = cap.read()
            if not ret:
                await asyncio.sleep(0.005)
                continue

            frame_idx += 1

            h, w = frame.shape[:2]
            if w > 640:
                scale = 640.0 / w
                frame = cv2.resize(frame, (640, int(h * scale)))

            motion_speeds = {tid: activity_aggregator.get_recent_motion_speed(tid) for tid in activity_aggregator.track_history}
            detections = pose_engine.process_frame_single_pass(frame, motion_speeds=motion_speeds, imgsz=480)

            # One shared implementation — see cv/frame_pipeline.py. Identity
            # resolution (Steps 5-14) goes inside that function, once, rather
            # than in three places that could drift apart.
            tracked_entities, zone_summary = process_detections(
                detections,
                frame,
                spatial_engine,
                activity_aggregator,
                anonymizer,
                zones_config,
                blur_enabled=privacy_state["blur"],
                appearance_extractor=appearance_extractor,
                identity_tracker=identity_tracker,
            )

            await persist_frame(activity_writer, tracked_entities)
            await persist_identity_frame(identity_writer, tracked_entities)

            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            frame_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')

            elapsed = time.time() - start_time

            payload = {
                "type": "FRAME",
                "frame_index": frame_idx,
                "current_time_seconds": round(elapsed, 1),
                "frame_base64": frame_b64,
                "tracked_entities": tracked_entities,
                "total_detected": len(tracked_entities),
                "zone_occupancy": zone_summary,
                "is_live_cam": True,
                "privacy_blur": privacy_state["blur"]
            }

            await websocket.send_json(payload)
            await asyncio.sleep(0.001)

    except WebSocketDisconnect:
        logger.info("Client disconnected from live webcam")
    finally:
        listen_task.cancel()
        if cap:
            cap.release()
        logger.info("Live webcam released.")


@router.websocket("/process_webcam_frame")
async def process_webcam_frame_websocket(websocket: WebSocket):
    """
    Single-Pass Real-Time Browser Camera Stream Endpoint.
    Runs on NVIDIA RTX 4060 CUDA GPU at 60 FPS.
    """
    await websocket.accept()
    org_id, role = await _resolve_session_org(websocket)
    if org_id is not None and not can(role, "analysis.run"):
        # send_json() immediately followed by close() races: the frame can be
        # dropped before delivery, and the client sees only an abnormal 1006
        # with no reason. Measured — the refusal worked but was unexplained.
        # Sending, then closing with an explicit policy-violation code (1008)
        # and the reason in the handshake, means the client learns WHY even if
        # the JSON frame loses the race.
        reason = denial_message("analysis.run")
        try:
            await websocket.send_json({"error": reason})
        except Exception:
            pass
        await websocket.close(code=1008, reason=reason)
        return
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # See resolve_registered_camera(): a caller may pass ?camera_id=<own camera>
    # so this session's telemetry maps to something the dashboard can read.
    requested_camera_id = websocket.query_params.get("camera_id")
    effective_camera_id = resolve_registered_camera(requested_camera_id, org_id) or "live_webcam"

    try:
        from app.cv.pose_estimator import PostureEstimator
        from app.cv.spatial_engine import SpatialEngine
        from app.cv.activity_aggregator import ActivityAggregator
        from app.cv.anonymizer import PrivacyAnonymizer

        pose_engine = PostureEstimator(pose_model_path="yolov8m-pose.pt", conf_thresh=0.35, device=device)
        # Falls back to the same camera id as the /live_webcam path and as
        # ZoneEditor's default `cameraId` prop (see dashboard/page.jsx
        # CAMERA_ID). The browser-camera and backend-direct-camera paths are two
        # ways of reaching the SAME "Live feed" the user draws zones against, so
        # they must share one id or zones drawn here would silently attribute to
        # DEFAULT_ZONES instead of what the user configured.
        zones_config = load_zones_for_camera(effective_camera_id, org_id=org_id)
        spatial_engine = SpatialEngine(zones_config=zones_config)
        activity_aggregator = ActivityAggregator()
        anonymizer = PrivacyAnonymizer()
        activity_writer = ActivityLogWriter(camera_id=effective_camera_id, org_id=org_id)
        # The per-person analogue, written alongside the anonymous path
        # rather than replacing it. Step 4 writes every row as UNKNOWN;
        # Steps 5-14 fill in employee_id without changing this wiring.
        identity_writer = IdentityEventWriter(
            camera_id=effective_camera_id, org_id=org_id, session_id=None
        )

        # Steps 5-6: appearance signatures and fragment stitching. The
        # extractor is process-wide (the model is stateless); the tracker
        # is per session, so its identity namespace and its gallery cannot
        # leak between concurrent videos.
        identity_on = identity_tracking_enabled()
        appearance_extractor = get_extractor() if identity_on else None
        identity_tracker = (
            IdentityTracker(session_id=None) if identity_on else None
        )

    except Exception as e:
        await websocket.send_json({"error": f"Failed to initialize AI models: {str(e)}"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "INIT",
        "message": f"Single-pass 60 FPS Browser Stream active on RTX 4060 GPU ({device.upper()}).",
        "device": device,
        "is_live_cam": True
    })

    frame_idx = 0
    start_time = time.time()
    privacy_state = {"blur": resolve_initial_blur(websocket)}

    try:
        while True:
            msg_text = await websocket.receive_text()
            data = json.loads(msg_text)

            action = data.get("action")
            if action == "stop":
                break
            if action == "set_privacy_blur":
                privacy_state["blur"] = bool(data.get("enabled", privacy_state["blur"]))
                continue

            img_bytes_b64 = data.get("image_base64")
            if not img_bytes_b64:
                continue

            if "," in img_bytes_b64:
                img_bytes_b64 = img_bytes_b64.split(",")[1]

            nparr = np.frombuffer(base64.b64decode(img_bytes_b64), np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                continue

            frame_idx += 1

            h, w = frame.shape[:2]
            if w > 640:
                scale = 640.0 / w
                frame = cv2.resize(frame, (640, int(h * scale)))

            motion_speeds = {tid: activity_aggregator.get_recent_motion_speed(tid) for tid in activity_aggregator.track_history}
            detections = pose_engine.process_frame_single_pass(frame, motion_speeds=motion_speeds, imgsz=480)

            # One shared implementation — see cv/frame_pipeline.py. Identity
            # resolution (Steps 5-14) goes inside that function, once, rather
            # than in three places that could drift apart.
            tracked_entities, zone_summary = process_detections(
                detections,
                frame,
                spatial_engine,
                activity_aggregator,
                anonymizer,
                zones_config,
                blur_enabled=privacy_state["blur"],
                appearance_extractor=appearance_extractor,
                identity_tracker=identity_tracker,
            )

            await persist_frame(activity_writer, tracked_entities)
            await persist_identity_frame(identity_writer, tracked_entities)

            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            frame_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')

            elapsed = time.time() - start_time

            payload = {
                "type": "FRAME",
                "frame_index": frame_idx,
                "current_time_seconds": round(elapsed, 1),
                "frame_base64": frame_b64,
                "tracked_entities": tracked_entities,
                "total_detected": len(tracked_entities),
                "zone_occupancy": zone_summary,
                "is_live_cam": True,
                "privacy_blur": privacy_state["blur"]
            }

            await websocket.send_json(payload)

    except WebSocketDisconnect:
        logger.info("Browser webcam client disconnected")
    except Exception as e:
        logger.error(f"Browser webcam error: {e}")
