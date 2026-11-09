# backend/app/api/routers/safety.py
"""
The safety API (Step 11): rules, events, decisions, review, summary, export, and the evidence picture.

    GET    /rules                       list the organisation's rules                       safety.view
    POST   /rules                       create a rule (validated)                           safety.rules.edit
    PATCH  /rules/{id}                  change a rule (validated)                           safety.rules.edit
    DELETE /rules/{id}                  soft-delete a rule (audited in the database)        safety.rules.edit
    POST   /rules/validate              dry-run a rule, return the problems before saving   safety.rules.edit
    GET    /events                      the event stream, filtered and paged                safety.view
    GET    /decisions                   decisions, filtered and paged                       safety.view
    GET    /decisions/{id}              one decision with its evidence                      safety.view
    POST   /decisions/{id}/review       confirm or dismiss a flag, with a note              safety.review
    GET    /decisions/{id}/snapshot     the blurred evidence picture (audited)              safety.evidence.view
    GET    /summary                     counts for the overview                             safety.view
    GET    /export                      CSV of decisions (audited)                          safety.view

TWO LAYERS, AND WHICH ONE HOLDS

  1. Here: the organisation, user and role come from ONE verified token lookup (never a parameter), the
     capability is checked first (an honest 401 / 403 and a sentence), every filter value is validated
     before it reaches a query, and the database's own words are not shown back.
  2. Postgres: every read and write runs AS THE CALLER (their token with the public key, see pg_user.py), so
     Row Level Security and the functions' own checks decide what is allowed. A bug in this file can only
     ever return what the caller could have read directly. This is the boundary; layer 1 is for manners.

Audit rows for evidence views, exports and rule changes are written with the service key (members cannot
write `safety.*` audit rows), BEFORE the thing is returned for views and exports: no audit row, no picture
and no export.
"""

import csv
import io
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.audit import write_audit
from app.api.deps import resolve_identity
from app.api.permissions import can, denial_message
from app.api.pg_user import PgError, bearer, user_request
from app.db.database import engine as _default_engine
from app.safety import snapshots
from app.safety.rules import RuleError, parse_rule

logger = logging.getLogger(__name__)
router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

STATUSES = ("COMPLIANT", "POTENTIAL_VIOLATION", "UNVERIFIABLE")
REVIEW_STATES = ("NOT_REQUIRED", "PENDING", "CONFIRMED", "DISMISSED")
RULE_TYPES = ("PPE_ENTRY", "PPE_CONTINUOUS", "MAX_DWELL", "REQUIRED_EXIT", "OCCUPANCY", "MAN_DOWN", "WALK_ONLY")
SEVERITIES = ("INFO", "WARNING", "CRITICAL")
EVENT_TYPES = ("ENTERED_ZONE", "EXITED_ZONE", "LOST_IN_ZONE", "REAPPEARED", "DWELL_WARNING", "DWELL_EXCEEDED", "PPE_VERDICT_CHANGED")
REVIEW_REASONS = ("CORRECT", "PPE_PRESENT", "WRONG_PERSON", "NOT_IN_ZONE", "BAD_PICTURE", "OTHER")
MAX_PAGE = 200
EXPORT_ROW_CAP = 20000
EXPORT_PAGE = 1000

_WORKER_RE = re.compile(r"^trk-[0-9]{1,9}\Z")


# ── identity and validation ──────────────────────────────────────────────────

def _who(authorization: Optional[str], capability: str):
    """(org_id, user_id, token) for a verified caller who holds `capability`, else 401 / 403."""
    org_id, user_id, role = resolve_identity(authorization)
    if org_id is None:
        raise HTTPException(status_code=401, detail="Sign in to an organisation to use the safety API.")
    if not can(role, capability):
        raise HTTPException(status_code=403, detail=denial_message(capability))
    token = bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to an organisation to use the safety API.")
    return org_id, user_id, token


def _uuid(value: Optional[str], what: str) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what} must be an id.")


def _one_of(value: Optional[str], allowed, what: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if value not in allowed:
        raise HTTPException(status_code=422, detail=f"{what} must be one of {', '.join(allowed)}.")
    return value


def _when(value: Optional[str], what: str) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{what} must be an ISO-8601 date or time.")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if not (datetime(2000, 1, 1, tzinfo=timezone.utc) <= moment <= datetime(2100, 1, 1, tzinfo=timezone.utc)):
        raise HTTPException(status_code=422, detail=f"{what} is out of range.")
    return moment


def _page(limit: int, offset: int):
    return max(1, min(int(limit), MAX_PAGE)), max(0, min(int(offset), 1_000_000))


def _http(e: PgError) -> HTTPException:
    if e.code not in ("P0001", "P0002", "42501") and (e.status >= 500 or e.code == ""):
        logger.error("Safety API database failure: %s", e)           # an unexpected failure; the caller gets a generic sentence
    return e.to_http()


def _pg(fn):
    """Run a database call; a PgError becomes the right HTTP error."""
    try:
        return fn()
    except PgError as e:
        raise _http(e)


def _total(headers: dict) -> Optional[int]:
    """The total row count from a PostgREST Content-Range header ('0-49/123')."""
    for k, v in headers.items():
        if k.lower() == "content-range" and "/" in v:
            tail = v.rsplit("/", 1)[1]
            return int(tail) if tail.isdigit() else None
    return None


def _list(table: str, token: str, params: dict, limit: int, offset: int, key: str) -> dict:
    try:
        _, headers, rows = user_request(
            "GET", table, token, params={**params, "limit": str(limit), "offset": str(offset)}, prefer="count=exact")
    except PgError as e:
        if e.status == 416:                          # an offset past the last row: an empty page, not an error
            return {key: [], "limit": limit, "offset": offset, "total": None, "next_offset": None}
        raise _http(e)
    rows = rows or []
    total = _total(headers)
    more = (total is None and len(rows) == limit) or (total is not None and offset + limit < total)
    return {key: rows, "limit": limit, "offset": offset, "total": total, "next_offset": offset + limit if more else None}


# ── rules ────────────────────────────────────────────────────────────────────

class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")           # a misspelt or camelCase field is an error, not a silent no-op
    name: str = Field(min_length=1, max_length=160)
    type: str
    zone_id: Optional[str] = None            # the Postgres zone id (uuid)
    camera_id: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    severity: str = "WARNING"
    is_enabled: bool = True
    min_evidence_confidence: float = 0.6


class RulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    zone_id: Optional[str] = None
    camera_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    severity: Optional[str] = None
    is_enabled: Optional[bool] = None
    min_evidence_confidence: Optional[float] = None


def _zones_for_validation(token: str, org_id: str, camera_id: Optional[str]) -> Dict[str, dict]:
    """The organisation's live zones, keyed by id, in the shape the rule validator reads."""
    params = {"select": "id,name,slug,safetyType,cameraId", "orgId": f"eq.{org_id}", "deletedAt": "is.null", "limit": "1000"}
    if camera_id:
        params["cameraId"] = f"eq.{camera_id}"
    _, _, rows = _pg(lambda: user_request("GET", "zones", token, params=params))
    return {str(z["id"]): {"zone_id": str(z["id"]), "zone_name": z.get("name"),
                           "zone_type": (z.get("safetyType") or "GENERAL"), "camera_id": z.get("cameraId")} for z in (rows or [])}


def _validated_rule(token: str, org_id: str, data: dict) -> dict:
    """A rule row ready to store, or a 422 saying what is wrong. `data` uses the API's field names."""
    if data.get("type") not in RULE_TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of {', '.join(RULE_TYPES)}.")
    if data.get("severity") not in SEVERITIES:
        raise HTTPException(status_code=422, detail=f"severity must be one of {', '.join(SEVERITIES)}.")
    if not str(data.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="A rule needs a name.")
    zone_id, camera_id = _uuid(data.get("zone_id"), "zone_id"), _uuid(data.get("camera_id"), "camera_id")
    zones = _zones_for_validation(token, org_id, camera_id)
    try:
        rule = parse_rule({
            "type": data["type"], "zoneId": zone_id, "params": data.get("params") or {}, "severity": data["severity"],
            "minEvidenceConfidence": data["min_evidence_confidence"], "isEnabled": data["is_enabled"], "name": data["name"],
        }, zones)
    except RuleError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "orgId": org_id, "cameraId": camera_id, "zoneId": zone_id, "name": data["name"].strip(), "type": rule.type,
        "params": rule.params, "severity": rule.severity, "isEnabled": rule.enabled,
        "minEvidenceConfidence": rule.min_evidence_confidence,
    }


def _evaluated(row: dict) -> bool:
    """Does the engine evaluate this rule today? (Only helmet-and-vest at entry; the others are stored and validated.)"""
    return row.get("type") == "PPE_ENTRY" and (row.get("params") or {}).get("mode", "AT_ENTRY") == "AT_ENTRY"


@router.get("/rules")
def list_rules(authorization: Optional[str] = Header(default=None)):
    org_id, _, token = _who(authorization, "safety.view")
    _, _, rows = _pg(lambda: user_request("GET", "safety_rules", token, params={
        "select": "id,name,type,zoneId,cameraId,params,severity,isEnabled,minEvidenceConfidence,createdAt,updatedAt,zone:zones(name),camera:cameras(name)",
        "orgId": f"eq.{org_id}", "order": "createdAt.asc", "limit": "500"}))
    rules = rows or []
    for r in rules:
        r["evaluated_by_engine"] = _evaluated(r)
    return {"rules": rules}


@router.post("/rules/validate")
def validate_rule(body: RuleIn, authorization: Optional[str] = Header(default=None)):
    org_id, _, token = _who(authorization, "safety.rules.edit")
    try:
        row = _validated_rule(token, org_id, body.model_dump())
    except HTTPException as e:
        if e.status_code == 422:
            return {"valid": False, "errors": [str(e.detail)]}
        raise
    return {"valid": True, "params": row["params"], "evaluated_by_engine": _evaluated(row)}


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, authorization: Optional[str] = Header(default=None)):
    org_id, user_id, token = _who(authorization, "safety.rules.edit")
    row = _validated_rule(token, org_id, body.model_dump())
    _, _, created = _pg(lambda: user_request("POST", "safety_rules", token, body=row, prefer="return=representation"))
    rule = (created or [{}])[0]
    if not rule.get("id"):
        raise HTTPException(status_code=502, detail="The rule could not be saved.")
    # The rule is saved; if its audit row cannot be written the response says so instead of pretending
    # (the change cannot be taken back here, so it is reported, and logged for the operator).
    rule["audit_recorded"] = True
    try:
        write_audit(org_id, user_id, "safety.rule_created", "safety_rule", rule["id"],
                    {"type": row["type"], "zoneId": row["zoneId"], "severity": row["severity"], "isEnabled": row["isEnabled"]})
    except Exception as e:
        rule["audit_recorded"] = False
        logger.error("A rule was created but its audit row could not be written: %s", e)
    rule["evaluated_by_engine"] = _evaluated(rule)
    return rule


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, authorization: Optional[str] = Header(default=None)):
    org_id, user_id, token = _who(authorization, "safety.rules.edit")
    rule_id = _uuid(rule_id, "the rule id")
    _, _, found = _pg(lambda: user_request("GET", "safety_rules", token, params={
        "select": "*", "id": f"eq.{rule_id}", "orgId": f"eq.{org_id}", "limit": "1"}))
    if not found:
        raise HTTPException(status_code=404, detail="Rule not found.")
    current = found[0]
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None and key not in ("zone_id", "camera_id"):
            raise HTTPException(status_code=422, detail=f"{key} cannot be null; leave it out to keep it unchanged.")
    merged = {
        "name": current["name"], "type": current["type"], "zone_id": current.get("zoneId"), "camera_id": current.get("cameraId"),
        "params": current.get("params") or {}, "severity": current["severity"], "is_enabled": current["isEnabled"],
        "min_evidence_confidence": current["minEvidenceConfidence"], **changes}
    row = _validated_rule(token, org_id, merged)
    patch = {k: row[k] for k in ("cameraId", "zoneId", "name", "params", "severity", "isEnabled", "minEvidenceConfidence")}
    _, _, updated = _pg(lambda: user_request("PATCH", "safety_rules", token, params={"id": f"eq.{rule_id}", "orgId": f"eq.{org_id}"},
                                              body=patch, prefer="return=representation"))
    if not updated:
        raise HTTPException(status_code=404, detail="Rule not found.")
    # what changed, with the values for the simple fields (a rule's parameters are noted as changed, not copied)
    simple = {"name": "name", "severity": "severity", "is_enabled": "isEnabled", "min_evidence_confidence": "minEvidenceConfidence",
              "zone_id": "zoneId", "camera_id": "cameraId"}
    before = {simple[k]: current.get(simple[k]) for k in changes if k in simple}
    after = {simple[k]: row[simple[k]] for k in changes if k in simple}
    audit_recorded = True
    try:
        write_audit(org_id, user_id, "safety.rule_updated", "safety_rule", rule_id,
                    {"fields": sorted(changes), "before": before, "after": after, "paramsChanged": "params" in changes})
    except Exception as e:
        audit_recorded = False
        logger.error("A rule was updated but its audit row could not be written: %s", e)
    out = updated[0]
    out["audit_recorded"] = audit_recorded
    out["evaluated_by_engine"] = _evaluated(out)
    return out


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, authorization: Optional[str] = Header(default=None)):
    _, _, token = _who(authorization, "safety.rules.edit")
    rule_id = _uuid(rule_id, "the rule id")
    # soft_delete_safety_rule() checks the caller's role itself and writes the audit row in the same transaction
    _pg(lambda: user_request("POST", "soft_delete_safety_rule", token, body={"p_rule_id": rule_id}, rpc=True))
    return Response(status_code=204)


# ── events and decisions ─────────────────────────────────────────────────────

def _window_filters(params: dict, since: Optional[str], until: Optional[str]) -> None:
    s, u = _when(since, "since"), _when(until, "until")
    if s and u and u <= s:
        raise HTTPException(status_code=422, detail="until must be after since.")
    conds = []
    if s:
        conds.append(f"(observedAt.gte.{s.isoformat()})")
    if u:
        conds.append(f"(observedAt.lt.{u.isoformat()})")
    if conds:
        params["and"] = conds


@router.get("/events")
def list_events(
    since: Optional[str] = None, until: Optional[str] = None, zone_id: Optional[str] = None, camera_id: Optional[str] = None,
    worker_key: Optional[str] = None, event_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0),
    authorization: Optional[str] = Header(default=None),
):
    org_id, _, token = _who(authorization, "safety.view")
    params: Dict[str, Any] = {"select": "id,eventUid,cameraId,zoneId,workerKey,sessionKey,eventType,videoTimeS,observedAt,details",
                              "orgId": f"eq.{org_id}", "order": "observedAt.desc,id.desc"}
    if _uuid(zone_id, "zone_id"):
        params["zoneId"] = f"eq.{_uuid(zone_id, 'zone_id')}"
    if _uuid(camera_id, "camera_id"):
        params["cameraId"] = f"eq.{_uuid(camera_id, 'camera_id')}"
    if worker_key:
        if not _WORKER_RE.match(worker_key):
            raise HTTPException(status_code=422, detail="worker_key looks like trk-12.")
        params["workerKey"] = f"eq.{worker_key}"
    if _one_of(event_type, EVENT_TYPES, "event_type"):
        params["eventType"] = f"eq.{event_type}"
    _window_filters(params, since, until)
    limit, offset = _page(limit, offset)
    return _list("safety_events", token, params, limit, offset, "events")


_DECISION_LIST = ("id,cameraId,zoneId,ruleId,ruleType,severity,workerKey,sessionKey,status,reason,confidence,videoTimeS,observedAt,"
                  "reviewState,reviewedById,reviewedAt,reviewReasonCode,reviewNote,snapshotRef,"
                  "zone:zones(name),camera:cameras(name),rule:safety_rules(name)")


def _decision_view(d: dict) -> dict:
    d["has_picture"] = bool(d.get("snapshotRef"))
    d.pop("snapshotRef", None)                         # the stored path is not for clients; the picture has its own endpoint
    return d


@router.get("/decisions")
def list_decisions(
    since: Optional[str] = None, until: Optional[str] = None, status: Optional[str] = None, review_state: Optional[str] = None,
    rule_type: Optional[str] = None, severity: Optional[str] = None, zone_id: Optional[str] = None,
    camera_id: Optional[str] = None, worker_key: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0),
    authorization: Optional[str] = Header(default=None),
):
    org_id, _, token = _who(authorization, "safety.view")
    params: Dict[str, Any] = {"select": _DECISION_LIST, "orgId": f"eq.{org_id}", "order": "observedAt.desc,id.desc"}
    for column, value, allowed, what in (("status", status, STATUSES, "status"), ("reviewState", review_state, REVIEW_STATES, "review_state"),
                                          ("ruleType", rule_type, RULE_TYPES, "rule_type"), ("severity", severity, SEVERITIES, "severity")):
        if _one_of(value, allowed, what):
            params[column] = f"eq.{value}"
    for column, value, what in (("zoneId", zone_id, "zone_id"), ("cameraId", camera_id, "camera_id")):
        if _uuid(value, what):
            params[column] = f"eq.{_uuid(value, what)}"
    if worker_key:
        if not _WORKER_RE.match(worker_key):
            raise HTTPException(status_code=422, detail="worker_key looks like trk-12.")
        params["workerKey"] = f"eq.{worker_key}"
    _window_filters(params, since, until)
    limit, offset = _page(limit, offset)
    out = _list("safety_decisions", token, params, limit, offset, "decisions")
    out["decisions"] = [_decision_view(d) for d in out["decisions"]]
    return out


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, authorization: Optional[str] = Header(default=None)):
    org_id, _, token = _who(authorization, "safety.view")
    decision_id = _uuid(decision_id, "the decision id")
    _, _, rows = _pg(lambda: user_request("GET", "safety_decisions", token, params={
        "select": _DECISION_LIST + ",evidence,thresholds", "id": f"eq.{decision_id}", "orgId": f"eq.{org_id}", "limit": "1"}))
    if not rows:
        raise HTTPException(status_code=404, detail="Decision not found.")
    return _decision_view(rows[0])


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    reason: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/decisions/{decision_id}/review")
def review_decision(decision_id: str, body: ReviewIn, authorization: Optional[str] = Header(default=None)):
    _, _, token = _who(authorization, "safety.review")
    decision_id = _uuid(decision_id, "the decision id")
    _one_of(body.state, ("CONFIRMED", "DISMISSED"), "state")
    if body.reason is not None:
        _one_of(body.reason, REVIEW_REASONS, "reason")
    # review_safety_decision() checks the caller's role and organisation, refuses a COMPLIANT decision, guards
    # CONFIRMED to DISMISSED while actions are open, and writes the audit row in the same transaction
    _, _, row = _pg(lambda: user_request("POST", "review_safety_decision", token, rpc=True,
                                         body={"p_decision_id": decision_id, "p_state": body.state, "p_reason": body.reason, "p_note": body.note}))
    return _decision_view(row if isinstance(row, dict) else (row or [{}])[0])


# ── summary and export ───────────────────────────────────────────────────────

@router.get("/summary")
def summary(days: int = Query(7, ge=1, le=366), tz: str = Query("UTC", max_length=64, pattern=r"^[A-Za-z0-9_+/-]+$"),
            authorization: Optional[str] = Header(default=None)):
    """Counts over the last `days`. `tz` is an IANA zone ("Asia/Kolkata"): the by-day buckets follow that site's days."""
    org_id, _, token = _who(authorization, "safety.view")
    until = datetime.now(timezone.utc)
    _, _, data = _pg(lambda: user_request("POST", "safety_summary", token, rpc=True, body={
        "p_org": org_id, "p_since": (until - timedelta(days=days)).isoformat(), "p_until": until.isoformat(), "p_tz": tz}))
    return data


_CSV_COLUMNS = [("observedAt", "Time (UTC)"), ("camera", "Camera"), ("zone", "Zone"), ("rule", "Rule"), ("workerKey", "Worker"),
                ("status", "Result"), ("severity", "Severity"), ("confidence", "Confidence"), ("reason", "Reason"),
                ("reviewState", "Review"), ("reviewedAt", "Reviewed at (UTC)"), ("reviewReasonCode", "Review reason"), ("reviewNote", "Review note")]


def _csv_cell(value) -> str:
    """One CSV cell. A text that starts like a spreadsheet formula is prefixed so it is shown, not run."""
    if value is None:
        return ""
    s = str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get("/export")
def export_decisions(
    since: Optional[str] = None, until: Optional[str] = None, status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    org_id, user_id, token = _who(authorization, "safety.view")
    end = _when(until, "until") or datetime.now(timezone.utc)
    start = _when(since, "since") or (end - timedelta(days=30))
    if end <= start:
        raise HTTPException(status_code=422, detail="until must be after since.")
    if end - start > timedelta(days=366):
        raise HTTPException(status_code=422, detail="An export covers at most 366 days.")
    started = datetime.now(timezone.utc)
    params: Dict[str, Any] = {
        "select": "observedAt,workerKey,status,severity,confidence,reason,reviewState,reviewedAt,reviewReasonCode,reviewNote,ruleType,zone:zones(name),camera:cameras(name),rule:safety_rules(name)",
        "orgId": f"eq.{org_id}", "order": "observedAt.desc,id.desc",
        # the set is frozen at the moment the export starts: rows the sync adds meanwhile (with older observedAt)
        # cannot shift the pages under it
        "and": [f"(observedAt.gte.{start.isoformat()})", f"(observedAt.lt.{end.isoformat()})", f"(createdAt.lte.{started.isoformat()})"]}
    if _one_of(status, STATUSES, "status"):
        params["status"] = f"eq.{status}"
    # the NEWEST rows are kept when a period holds more than the cap; one extra row tells us it was cut
    rows: List[dict] = []
    while len(rows) <= EXPORT_ROW_CAP:
        _, _, page = _pg(lambda: user_request("GET", "safety_decisions", token, params={**params, "limit": str(EXPORT_PAGE), "offset": str(len(rows))}))
        if not page:
            break                                    # an empty page ends it (a server that caps pages below ours must not end it early)
        rows.extend(page)
    truncated = len(rows) > EXPORT_ROW_CAP
    rows = list(reversed(rows[:EXPORT_ROW_CAP]))     # oldest first in the file
    # No audit row, no export.
    try:
        write_audit(org_id, user_id, "safety.export", "safety_decisions", "csv",
                    {"rows": len(rows), "since": start.isoformat(), "until": end.isoformat(), "status": status, "truncated": truncated})
    except Exception as e:
        logger.error("An export could not be audited, so it was not served: %s", e)
        raise HTTPException(status_code=503, detail="The export could not be recorded, so it was not produced. Try again.")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([label for _, label in _CSV_COLUMNS])
    for d in rows:
        d = dict(d)
        d["camera"], d["zone"] = (d.get("camera") or {}).get("name"), (d.get("zone") or {}).get("name")
        # a deleted rule is hidden from the caller, and the built-in default has no rule row: say which kind of rule it was
        d["rule"] = (d.get("rule") or {}).get("name") or d.get("ruleType")
        if str(d.get("workerKey", "")).startswith("trk-"):
            d["workerKey"] = "Worker " + d["workerKey"][4:]
        writer.writerow([_csv_cell(d.get(k)) for k, _ in _CSV_COLUMNS])
    if truncated:
        writer.writerow([f"Only the newest {EXPORT_ROW_CAP} decisions of this period are included. Choose a shorter period to get the rest."])
    name = f"safety-decisions-{start.date()}-to-{end.date()}.csv"
    headers = {**_NO_STORE, "Content-Disposition": f'attachment; filename="{name}"'}
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(content="\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


# ── the evidence picture ─────────────────────────────────────────────────────

def _local_decision(decision_id: str, org_id: str, engine=None) -> Optional[dict]:
    with (engine or _default_engine).connect() as conn:
        row = conn.execute(
            text("SELECT id, org_id, snapshot_ref FROM safety_decisions WHERE id = :id AND org_id = :org"),
            {"id": decision_id, "org": org_id}).first()
    return dict(row._mapping) if row else None


def _caller_can_see(token: str, org_id: str, decision_id: str) -> bool:
    """
    Can the signed-in user read this decision in Postgres RIGHT NOW? The picture is a local file, so this is the
    one place the caller's rights are asked of the database explicitly: a member removed a moment ago, or an
    organisation that has been deleted, is refused here even while the identity cache still remembers them.
    """
    _, _, rows = _pg(lambda: user_request("GET", "safety_decisions", token, params={
        "select": "id", "id": f"eq.{decision_id}", "orgId": f"eq.{org_id}", "limit": "1"}))
    return bool(rows)


def _record_view(org_id: str, user_id: Optional[str], decision_id: str) -> None:
    """Write the audit row. Raises if it cannot be written, for any reason: no audit, no picture."""
    write_audit(org_id, user_id, "safety.evidence_viewed", "safety_decision", decision_id)


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
    token = bearer(authorization)
    if not token or not _caller_can_see(token, org_id, decision_id):
        raise HTTPException(status_code=404, detail="Decision not found.")
    row = _local_decision(decision_id, org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found.")
    if not row["snapshot_ref"]:
        raise HTTPException(status_code=404, detail="No evidence picture was stored for this decision.")
    # The reference must be exactly this organisation's and this decision's own file: a stored value
    # pointing at anyone else's picture is never followed.
    try:
        expected = snapshots.ref_for(org_id, decision_id)
    except ValueError:
        expected = None
    path = snapshots.path_for_ref(row["snapshot_ref"]) if row["snapshot_ref"] == expected else None
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
