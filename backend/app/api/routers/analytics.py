# backend/app/api/routers/analytics.py
import io
import os
import tempfile

from fastapi import APIRouter, Depends, Query, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.db.database import get_db
from app.db.models import ActivityLogModel
from app.api.deps import resolve_org

router = APIRouter()


# ── Tenancy ────────────────────────────────────────────────────────────────
#
# Every endpoint in this file is org-scoped. The organisation is NOT a request
# parameter: it is derived from the caller's verified Supabase access token in
# api/deps.py, because this service has permissive CORS and no session of its
# own, so anything the client asserts about its own identity is editable in a
# URL bar. See the module docstring in deps.py for the full reasoning.
#
# The dependency returns None when there is no usable token, and every endpoint
# treats None as "no tenant" and returns an empty result. Failing closed is the
# entire point: an unauthenticated caller reading all tenants' telemetry is the
# bug this design exists to prevent.


def current_org(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    """FastAPI dependency: the caller's organisation id, or None."""
    return resolve_org(authorization)


def scoped(db: Session, org_id: Optional[str]):
    """
    A query over activity_logs restricted to one organisation.

    Rows with org_id IS NULL are excluded by construction — `== org_id` is never
    true for NULL in SQL. Those rows predate tenancy and belong to nobody, so
    every tenant correctly sees none of them.
    """
    return db.query(ActivityLogModel).filter(ActivityLogModel.org_id == org_id)


EMPTY_OVERVIEW = {
    "has_data": False,
    "people": 0, "zones_active": 0, "avg_activity": 0,
    "sitting_pct": 0, "standing_pct": 0, "walking_pct": 0,
    "peak_zone": None, "longest_dwell_minutes": 0, "last_seen": None,
}

@router.get("/summary")
def get_analytics_summary(
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """Returns instantaneous summary of this organisation's occupancy and posture ratio"""
    if org_id is None:
        # No verified tenant: report nothing rather than everything.
        return {
            "total_logs": 0,
            "average_activity_score": 0.0,
            "posture_distribution": {
                "sitting_percentage": 0.0,
                "standing_percentage": 0.0,
                "walking_percentage": 0.0,
            },
        }

    total_logs = scoped(db, org_id).count()

    # Calculate posture breakdown
    sitting_count = scoped(db, org_id).filter(ActivityLogModel.posture_state == "SITTING").count()
    standing_count = scoped(db, org_id).filter(ActivityLogModel.posture_state == "STANDING").count()
    walking_count = scoped(db, org_id).filter(ActivityLogModel.posture_state == "WALKING").count()

    total_observed = max(1, sitting_count + standing_count + walking_count)

    # Average activity score. Defaults to 0.0, not 65.0: a placeholder number
    # on an empty tenant would be indistinguishable from a real measurement.
    avg_score = (
        scoped(db, org_id)
        .with_entities(func.avg(ActivityLogModel.activity_score))
        .scalar()
        or 0.0
    )

    return {
        "total_logs": total_logs,
        "average_activity_score": round(float(avg_score), 2),
        "posture_distribution": {
            "sitting_percentage": round((sitting_count / total_observed) * 100, 1),
            "standing_percentage": round((standing_count / total_observed) * 100, 1),
            "walking_percentage": round((walking_count / total_observed) * 100, 1)
        }
    }

@router.get("/overview")
def get_manager_overview(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """
    The headline numbers for the manager's home screen.

    Deliberately a small, fixed set. A manager opening the app wants to know
    "is it running, how busy was it, and is anyone sitting too long" — not
    twelve tiles of everything the schema can count.

    `people` counts DISTINCT tracks, not rows: activity_logs holds a sampled
    series per person, so counting rows would report a number many times larger
    than the number of people actually observed.
    """
    if org_id is None:
        return {"hours": hours, **EMPTY_OVERVIEW}

    since = datetime.utcnow() - timedelta(hours=hours)
    window = scoped(db, org_id).filter(ActivityLogModel.timestamp >= since)

    total_rows = window.count()

    if total_rows == 0:
        return {"hours": hours, **EMPTY_OVERVIEW}

    people = window.with_entities(
        func.count(func.distinct(ActivityLogModel.track_id))
    ).scalar() or 0

    zones_active = window.with_entities(
        func.count(func.distinct(ActivityLogModel.zone_id))
    ).scalar() or 0

    avg_activity = window.with_entities(
        func.avg(ActivityLogModel.activity_score)
    ).scalar() or 0.0

    counts = {
        state: window.filter(ActivityLogModel.posture_state == state).count()
        for state in ("SITTING", "STANDING", "WALKING")
    }
    observed = max(1, sum(counts.values()))

    # Busiest zone by distinct people, excluding transit: a corridor everyone
    # walks through is not a utilisation signal.
    peak = (
        window.with_entities(
            ActivityLogModel.zone_id,
            func.count(func.distinct(ActivityLogModel.track_id)).label("n"),
        )
        .filter(ActivityLogModel.zone_id != "TRANSIT_ZONE")
        .group_by(ActivityLogModel.zone_id)
        .order_by(func.count(func.distinct(ActivityLogModel.track_id)).desc())
        .first()
    )

    longest = window.with_entities(
        func.max(ActivityLogModel.dwell_duration_seconds)
    ).scalar() or 0

    last_seen = window.with_entities(func.max(ActivityLogModel.timestamp)).scalar()

    return {
        "hours": hours,
        "has_data": True,
        "people": int(people),
        "zones_active": int(zones_active),
        "avg_activity": round(float(avg_activity), 1),
        "sitting_pct": round(counts["SITTING"] / observed * 100, 1),
        "standing_pct": round(counts["STANDING"] / observed * 100, 1),
        "walking_pct": round(counts["WALKING"] / observed * 100, 1),
        "peak_zone": {"zone": peak[0], "people": int(peak[1])} if peak else None,
        "longest_dwell_minutes": round(int(longest) / 60.0, 1),
        "last_seen": last_seen.isoformat(sep=" ", timespec="seconds") if last_seen else None,
    }


@router.get("/report/csv")
def export_csv_report(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """
    Downloads raw telemetry for the window as CSV.

    Streamed from memory rather than written into the project directory: a
    report is a point-in-time export, and leaving generated files on disk means
    they accumulate and go stale with nothing responsible for cleaning them up.
    """
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to export telemetry.",
        )

    since_time = datetime.utcnow() - timedelta(hours=hours)
    logs = (
        scoped(db, org_id)
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
def export_pdf_report(
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """
    Downloads the executive summary as a PDF.

    Reuses get_analytics_summary() rather than recomputing the same aggregates,
    so the numbers in the PDF can never drift from the numbers on the dashboard.
    """
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to export a report.",
        )

    summary = get_analytics_summary(db=db, org_id=org_id)

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


@router.get("/sync_health")
def get_sync_health(org_id: Optional[str] = Depends(current_org)):
    """
    How many locally-aggregated minute buckets have not reached Postgres yet.

    Not org-defensive in the paranoid sense — an unsynced count leaks no
    occupancy data, only a number of pending rows — but still returns 0 rather
    than the whole installation's count when there is no verified caller, for
    the same "report nothing rather than everything" reason every other
    endpoint here follows.

    A persistently nonzero count means telemetry is being produced under a
    camera/zone name that does not match anything registered in this org (see
    minute_aggregator._resolve_ids) — the dashboard's Overview page reads from
    Postgres, not SQLite, so those buckets are invisible until this resolves.
    """
    if org_id is None:
        return {"unsynced_buckets": 0}

    from app.db.minute_aggregator import unsynced_bucket_count_sync

    return {"unsynced_buckets": unsynced_bucket_count_sync(org_id)}


@router.get("/heatmap")
def get_floorplan_heatmap(
    hours: int = Query(24, ge=1, le=168),
    grid: int = Query(24, ge=4, le=64),
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
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
    if org_id is None:
        return {"points": [], "max": 0, "total_samples": 0, "grid": grid, "hours": hours}

    since_time = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        scoped(db, org_id)
        .with_entities(ActivityLogModel.floor_x, ActivityLogModel.floor_y)
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
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """
    Total dwell time per zone, for the zone-utilisation bar chart.

    Dwell is reported per (zone, track): activity_logs holds a sampled series of
    rows for the same person, each carrying the running dwell total for that
    track. Summing the column directly would therefore count the same seconds
    many times over, so the MAX per track is taken and those maxima are summed.
    """
    if org_id is None:
        return []

    since_time = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        scoped(db, org_id)
        .with_entities(
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
    db: Session = Depends(get_db),
    org_id: Optional[str] = Depends(current_org),
):
    """Fetches hourly averaged activity score and posture stats for historical charts"""
    if org_id is None:
        return []

    since_time = datetime.utcnow() - timedelta(hours=hours)
    logs = scoped(db, org_id).filter(ActivityLogModel.timestamp >= since_time).all()

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
