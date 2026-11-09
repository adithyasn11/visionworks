# backend/app/api/routers/safety.py
"""
Safety endpoints that only the backend can serve. (The dashboard reads rules and
decisions straight from Postgres through Supabase; this router is for what needs the
files and the audit trail that live here.)

    GET /api/v1/safety/decisions/{id}/snapshot     the blurred evidence picture

WHO MAY SEE A PICTURE

  * The organisation always comes from the verified access token, never from a parameter.
  * The role must hold `safety.evidence.view` (ADMIN and MANAGER).
  * The decision must belong to that organisation. "Not yours" and "does not exist" get
    the same 404, so a decision id cannot be probed across organisations.
  * The view is written to the audit log BEFORE the picture is sent, naming the signed-in user.
    If it cannot be recorded (no audit store, no identified user, a failed write) the picture
    is not sent (503): evidence nobody can account for is not served.
  * Standalone pictures (filed under local/, no organisation) cannot be opened through this
    endpoint, which needs an organisation; they stay on the machine for the operator.
  * Never cached by a browser or a proxy (Cache-Control: no-store).
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.api.deps import resolve_identity
from app.api.permissions import can, denial_message
from app.db.database import engine as _default_engine
from app.safety import snapshots

logger = logging.getLogger(__name__)
router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _local_decision(decision_id: str, org_id: str, engine=None) -> Optional[dict]:
    with (engine or _default_engine).connect() as conn:
        row = conn.execute(
            text("SELECT id, org_id, snapshot_ref FROM safety_decisions WHERE id = :id AND org_id = :org"),
            {"id": decision_id, "org": org_id}).first()
    return dict(row._mapping) if row else None


def _record_view(org_id: str, user_id: Optional[str], decision_id: str) -> None:
    """Write the audit row. Raises if it cannot be written, for any reason: no audit, no picture."""
    from app.db.safety_sync import _call, service_role_configured
    if not service_role_configured():
        raise RuntimeError("no audit store is configured")
    if not user_id:
        raise RuntimeError("the viewer could not be identified")
    _call("POST", "audit_logs", None, [{
        "orgId": org_id, "actorId": user_id, "action": "safety.evidence_viewed",
        "targetType": "safety_decision", "targetId": decision_id, "metadata": {},
    }], "return=minimal")


@router.get("/decisions/{decision_id}/snapshot")
def decision_snapshot(decision_id: str, authorization: Optional[str] = Header(default=None)):
    org_id, user_id, role = resolve_identity(authorization)
    if org_id is None:
        raise HTTPException(status_code=401, detail="Sign in to an organisation to see evidence.")
    if not can(role, "safety.evidence.view"):
        raise HTTPException(status_code=403, detail=denial_message("safety.evidence.view"))
    try:
        decision_id = str(uuid.UUID(decision_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Decision not found.")
    row = _local_decision(decision_id, org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found.")
    if not row["snapshot_ref"]:
        raise HTTPException(status_code=404, detail="No evidence picture was stored for this decision.")
    path = snapshots.path_for_ref(row["snapshot_ref"])
    if path is None:
        logger.error("Decision %s has an unusable snapshot reference", decision_id)
        raise HTTPException(status_code=404, detail="No evidence picture was stored for this decision.")
    if not path.is_file():
        raise HTTPException(status_code=410, detail="This evidence picture has expired and was deleted.")
    try:
        _record_view(org_id, user_id, decision_id)
    except Exception as e:
        logger.error("Evidence view could not be audited, so it was not served: %s", e)
        raise HTTPException(status_code=503, detail="The access could not be recorded, so the picture was not shown. Try again.")
    return FileResponse(str(path), media_type="image/jpeg", headers=_NO_STORE)
