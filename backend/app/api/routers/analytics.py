# backend/app/api/routers/analytics.py
from fastapi import APIRouter, Depends, Query
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
