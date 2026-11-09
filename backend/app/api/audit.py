# backend/app/api/audit.py
"""
Write a row to the organisation's audit log from the Python backend.

Until now only the Next.js server actions wrote audit rows. Safety actions (an evidence view, an
export, a rule created or changed) are audited here, through PostgREST with the SERVICE key: the
`audit_insert` policy deliberately refuses `safety.*` actions from a signed-in user (026), so a member
cannot write or forge them. The caller supplies the organisation and the actor from the VERIFIED
token, never from a request body.

Raises when the row cannot be written. Callers that must not act without an audit row (serving
evidence, an export) let that stop them.
"""

from typing import Optional


def write_audit(org_id: str, actor_id: Optional[str], action: str, target_type: str, target_id: str,
                metadata: Optional[dict] = None) -> None:
    if not org_id or not actor_id:
        raise RuntimeError("an audit row needs an organisation and the person who acted")
    if not str(action).startswith("safety."):
        raise ValueError("this helper is for safety.* actions")
    from app.db.safety_sync import _call, service_role_configured
    if not service_role_configured():
        raise RuntimeError("no audit store is configured")
    _call("POST", "audit_logs", None, [{
        "orgId": org_id, "actorId": actor_id, "action": action,
        "targetType": target_type, "targetId": str(target_id)[:64], "metadata": metadata or {},
    }], "return=minimal")
