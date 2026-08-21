# backend/app/api/routers/analytics.py
import io
import os
import tempfile

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Dict, Any
from datetime import datetime, timedelta
from app.db.database import get_db
from app.db.models import ActivityLogModel

router = APIRouter()

@router.get("/summary")
def get_analytics_summary(db: Session = Depends(get_db)):
    """Returns instantaneous summary of current workplace occupancy and posture ratio"""
    total_logs = db.query(ActivityLogModel).count()
    
    # Calculate posture breakdown
    sitting_count = db.query(ActivityLogModel).filter(ActivityLogModel.posture_state == "SITTING").count()
    standing_count = db.query(ActivityLogModel).filter(ActivityLogModel.posture_state == "STANDING").count()
    walking_count = db.query(ActivityLogModel).filter(ActivityLogModel.posture_state == "WALKING").count()

    total_observed = max(1, sitting_count + standing_count + walking_count)
    
    # Average activity score
    avg_score = db.query(func.avg(ActivityLogModel.activity_score)).scalar() or 65.0

    return {
        "total_logs": total_logs,
        "average_activity_score": round(float(avg_score), 2),
        "posture_distribution": {
            "sitting_percentage": round((sitting_count / total_observed) * 100, 1),
            "standing_percentage": round((standing_count / total_observed) * 100, 1),
            "walking_percentage": round((walking_count / total_observed) * 100, 1)
        }
    }

@router.get("/report/csv")
def export_csv_report(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """
    Downloads raw telemetry for the window as CSV.

    Streamed from memory rather than written into the project directory: a
    report is a point-in-time export, and leaving generated files on disk means
    they accumulate and go stale with nothing responsible for cleaning them up.
    """
    since_time = datetime.utcnow() - timedelta(hours=hours)
    logs = (
        db.query(ActivityLogModel)
        .filter(ActivityLogModel.timestamp >= since_time)
        .order_by(ActivityLogModel.timestamp.asc())
        .all()
    )

    if not logs:
        raise HTTPException(
            status_code=404,
            detail="No telemetry in this window yet. Process a video or run the live camera first.",
        )

    rows = [
        {
            "timestamp": log.timestamp.isoformat(sep=" ", timespec="seconds"),
            "camera_id": log.camera_id,
            "zone_id": log.zone_id,
            "track_id": log.track_id,
            "posture_state": log.posture_state,
            "activity_score": log.activity_score,
            "dwell_duration_seconds": log.dwell_duration_seconds,
            "floor_x": log.floor_x,
            "floor_y": log.floor_y,
        }
        for log in logs
    ]

    from app.utils.report_generator import AnalyticsReportGenerator

    # The generator writes to a path, so use a temp file and stream its bytes
    # back, rather than changing a working module to satisfy the transport.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "report.csv")
        AnalyticsReportGenerator.generate_csv_report(rows, path)
        with open(path, "rb") as handle:
            payload = handle.read()

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="activity_telemetry_{stamp}.csv"'
        },
    )


@router.get("/report/pdf")
def export_pdf_report(db: Session = Depends(get_db)):
    """
    Downloads the executive summary as a PDF.

    Reuses get_analytics_summary() rather than recomputing the same aggregates,
    so the numbers in the PDF can never drift from the numbers on the dashboard.
    """
    summary = get_analytics_summary(db=db)

    if not summary.get("total_logs"):
        raise HTTPException(
            status_code=404,
            detail="No telemetry recorded yet. Process a video or run the live camera first.",
        )

    from app.utils.report_generator import AnalyticsReportGenerator

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "report.pdf")
        AnalyticsReportGenerator.generate_pdf_report(summary, path)
        with open(path, "rb") as handle:
            payload = handle.read()

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="workplace_report_{stamp}.pdf"'
        },
    )


@router.get("/heatmap")
def get_floorplan_heatmap(
    hours: int = Query(24, ge=1, le=168),
    grid: int = Query(24, ge=4, le=64),
    db: Session = Depends(get_db)
):
    """
    Occupancy density across the floorplan, for the top-down heatmap.

    Positions are stored normalised (0..1) by the CV pipeline after homography
    projection, so they are resolution-independent and the frontend can scale
    them to whatever size it draws the floorplan at.

    Raw points are snapped to a coarse grid rather than returned individually.
    Two reasons: a busy session holds tens of thousands of rows and shipping
    them all would be a multi-megabyte response, and heatmap.js renders far
    better from aggregated buckets than from a dense scatter of near-identical
    points. `grid` is the number of cells per axis.
    """
    since_time = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        db.query(ActivityLogModel.floor_x, ActivityLogModel.floor_y)
        .filter(
            ActivityLogModel.timestamp >= since_time,
            ActivityLogModel.floor_x.isnot(None),
            ActivityLogModel.floor_y.isnot(None),
        )
        .all()
    )

    buckets: Dict[tuple, int] = {}
    for fx, fy in rows:
        # min() guards the exact-1.0 edge, which would otherwise land in a
        # cell one past the end of the grid.
        cx = min(int(fx * grid), grid - 1)
        cy = min(int(fy * grid), grid - 1)
        key = (cx, cy)
        buckets[key] = buckets.get(key, 0) + 1

    points = [
        {
            # Cell centre, back in normalised space.
            "x": round((cx + 0.5) / grid, 4),
            "y": round((cy + 0.5) / grid, 4),
            "value": count,
        }
        for (cx, cy), count in buckets.items()
    ]
    points.sort(key=lambda p: p["value"], reverse=True)

    return {
        "points": points,
        "max": max((p["value"] for p in points), default=0),
        "total_samples": len(rows),
        "grid": grid,
        "hours": hours,
    }


@router.get("/zones")
def get_zone_dwell(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """
    Total dwell time per zone, for the zone-utilisation bar chart.

    Dwell is reported per (zone, track): activity_logs holds a sampled series of
    rows for the same person, each carrying the running dwell total for that
    track. Summing the column directly would therefore count the same seconds
    many times over, so the MAX per track is taken and those maxima are summed.
    """
    since_time = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        db.query(
            ActivityLogModel.zone_id,
            ActivityLogModel.track_id,
            func.max(ActivityLogModel.dwell_duration_seconds).label("dwell"),
        )
        .filter(ActivityLogModel.timestamp >= since_time)
        .group_by(ActivityLogModel.zone_id, ActivityLogModel.track_id)
        .all()
    )

    totals: Dict[str, Dict[str, Any]] = {}
    for zone_id, _track_id, dwell in rows:
        bucket = totals.setdefault(zone_id, {"seconds": 0, "visitors": 0})
        bucket["seconds"] += int(dwell or 0)
        bucket["visitors"] += 1

    return [
        {
            "zone": zone_id,
            "minutes": round(data["seconds"] / 60.0, 1),
            "seconds": data["seconds"],
            "visitors": data["visitors"],
        }
        for zone_id, data in sorted(totals.items(), key=lambda kv: kv[1]["seconds"], reverse=True)
    ]


@router.get("/historical")
def get_historical_telemetry(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db)
):
    """Fetches hourly averaged activity score and posture stats for historical charts"""
    since_time = datetime.utcnow() - timedelta(hours=hours)
    logs = db.query(ActivityLogModel).filter(ActivityLogModel.timestamp >= since_time).all()

    # Aggregate by hour
    hourly_stats: Dict[str, Any] = {}
    for log in logs:
        hour_key = log.timestamp.strftime("%Y-%m-%d %H:00")
        if hour_key not in hourly_stats:
            hourly_stats[hour_key] = {"scores": [], "sitting": 0, "standing": 0, "walking": 0}
        hourly_stats[hour_key]["scores"].append(log.activity_score)
        if log.posture_state == "SITTING":
            hourly_stats[hour_key]["sitting"] += 1
        elif log.posture_state == "STANDING":
            hourly_stats[hour_key]["standing"] += 1
        elif log.posture_state == "WALKING":
            hourly_stats[hour_key]["walking"] += 1

    formatted_data = []
    for hour, data in sorted(hourly_stats.items()):
        avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
        formatted_data.append({
            "time": hour,
            "avg_activity_score": round(avg_score, 2),
            "sitting_count": data["sitting"],
            "standing_count": data["standing"],
            "walking_count": data["walking"]
        })

    return formatted_data
