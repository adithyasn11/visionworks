# backend/app/api/routers/analytics.py

"""
Where people were seen on the floor, from the pipeline's own anonymous positions (the floor-plan map on the live feed).

    GET /heatmap   density of positions over the last hours, snapped to a grid

The old summary / overview / zone-time / history endpoints were removed with the screens that used them. Everything
here is scoped to the caller's organisation (see below).
"""

from fastapi import APIRouter, Depends, Query, Header
from sqlalchemy.orm import Session
from typing import Dict, Optional
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
