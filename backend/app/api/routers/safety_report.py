# backend/app/api/routers/safety_report.py
"""
Safety reports (Step 16), under the same prefix as the rest of the safety API (/api/v1/safety):

    GET /report                 the report figures for a period, as JSON                      safety.view
    GET /report/export          the same report as a PDF or a CSV file (audited, recorded)    safety.view
    GET /report/history         the reports made before (kind SAFETY_SUMMARY)                 safety.view
    GET /audit-trail            a CSV of every decision and zone event in a period, in order  safety.view

(The row-level decisions CSV, GET /export, is in safety.py.)

Same two layers as safety.py. The organisation, user and role come from one verified token lookup; every read
runs AS THE CALLER, so Row Level Security decides what is in a report. The figures are computed by the database
function `safety_report` (028_safety_report.sql), never recomputed here, and the PDF and CSV are laid out from
one list of tables (utils/report_generator.py) so they cannot disagree.

A file is built first; then the export is AUDITED (`safety.report_exported`, `safety.audit_trail_exported`);
no audit row, no file. A report that was served is also recorded in the `reports` table (kind SAFETY_SUMMARY)
so the screen can list the reports made before; if that record cannot be written the file is still served and
`X-Report-Recorded: false` says so.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

from app.api.audit import write_audit
from app.api.pg_user import user_request
from app.api.routers.safety import EXPORT_PAGE, EXPORT_ROW_CAP, _NO_STORE, _pg, _when, _who
from app.utils import report_generator as rg

logger = logging.getLogger(__name__)
router = APIRouter()

_TZ_PATTERN = r"^[A-Za-z0-9_+/-]+$"
MAX_DAYS = 366


def _window(days: int, since: Optional[str], until: Optional[str]):
    end = _when(until, "until") or datetime.now(timezone.utc)
    start = _when(since, "since") or (end - timedelta(days=days))
    if end <= start:
        raise HTTPException(status_code=422, detail="until must be after since.")
    if end - start > timedelta(days=MAX_DAYS):
        raise HTTPException(status_code=422, detail=f"A report covers at most {MAX_DAYS} days.")
    return start, end


def _organisation_name(token: str, org_id: str) -> str:
    """The organisation's name for the report header. A failure here must not stop a report: it is a label."""
    try:
        _, _, rows = user_request("GET", "organisations", token, params={"select": "name", "id": f"eq.{org_id}", "limit": "1"})
        return str((rows or [{}])[0].get("name") or "")
    except Exception as e:
        logger.warning("The organisation's name could not be read for a report: %s", e)
        return ""


def _figures(token: str, org_id: str, start: datetime, end: datetime, tz: str) -> Dict[str, Any]:
    _, _, data = _pg(lambda: user_request("POST", "safety_report", token, rpc=True, body={
        "p_org": org_id, "p_since": start.isoformat(), "p_until": end.isoformat(), "p_tz": tz}))
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="The report could not be made. Try again.")
    return data


@router.get("/report")
def report(days: int = Query(7, ge=1, le=MAX_DAYS), since: Optional[str] = None, until: Optional[str] = None,
           tz: str = Query("UTC", max_length=64, pattern=_TZ_PATTERN), authorization: Optional[str] = Header(default=None)):
    """The figures of a period (`since` / `until`, or the last `days`). `tz` (an IANA name) sets the hour and day buckets."""
    org_id, _, token = _who(authorization, "safety.view")
    start, end = _window(days, since, until)
    data = _figures(token, org_id, start, end, tz)
    return {**data, "organisation": _organisation_name(token, org_id)}


@router.get("/report/export")
def export_report(format: str = Query(..., pattern=r"^(pdf|csv)$"), days: int = Query(7, ge=1, le=MAX_DAYS),
                  since: Optional[str] = None, until: Optional[str] = None,
                  tz: str = Query("UTC", max_length=64, pattern=_TZ_PATTERN), authorization: Optional[str] = Header(default=None)):
    org_id, user_id, token = _who(authorization, "safety.view")
    start, end = _window(days, since, until)
    data = _figures(token, org_id, start, end, tz)
    name = _organisation_name(token, org_id)
    try:
        if format == "pdf":
            payload, media = rg.build_pdf(data, name), "application/pdf"
        else:
            payload, media = rg.build_csv(data, name).encode("utf-8"), "text/csv; charset=utf-8"
    except Exception as e:
        logger.error("A safety report could not be laid out: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="The report could not be made. Try again.")
    # No audit row, no report.
    try:
        write_audit(org_id, user_id, "safety.report_exported", "safety_report", format,
                    {"format": format, "since": start.isoformat(), "until": end.isoformat(), "time_zone": tz, "bytes": len(payload)})
    except Exception as e:
        logger.error("A report could not be audited, so it was not served: %s", e)
        raise HTTPException(status_code=503, detail="The report could not be recorded, so it was not produced. Try again.")
    recorded = True
    try:
        user_request("POST", "reports", token, prefer="return=minimal", body={
            "orgId": org_id, "kind": "SAFETY_SUMMARY", "format": format.upper(), "status": "READY",
            "title": rg.report_title(data)[:200], "rangeStart": start.isoformat(), "rangeEnd": end.isoformat(),
            "zoneIds": [], "siteIds": [], "fileSizeBytes": len(payload), "requestedById": user_id,
            "completedAt": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        recorded = False
        logger.warning("A served report could not be recorded in the reports table: %s", e)
    filename = f"safety-report-{start.date()}-to-{end.date()}.{format}"
    headers = {**_NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"', "X-Report-Recorded": "true" if recorded else "false"}
    return Response(content=payload, media_type=media, headers=headers)


@router.get("/report/history")
def report_history(limit: int = Query(20, ge=1, le=100), authorization: Optional[str] = Header(default=None)):
    """The safety reports made before, newest first (the reports table, kind SAFETY_SUMMARY)."""
    org_id, _, token = _who(authorization, "safety.view")
    _, _, rows = _pg(lambda: user_request("GET", "reports", token, params={
        "select": "id,format,title,rangeStart,rangeEnd,createdAt,status,fileSizeBytes", "orgId": f"eq.{org_id}", "kind": "eq.SAFETY_SUMMARY",
        "order": "createdAt.desc,id.desc", "limit": str(limit)}))
    return {"reports": rows or []}


def _fetch_newest(token: str, table: str, select: str, params: Dict[str, Any]) -> List[dict]:
    """Up to EXPORT_ROW_CAP + 1 newest rows (the extra one says it was cut), paged, an empty page ends it."""
    rows: List[dict] = []
    while len(rows) <= EXPORT_ROW_CAP:
        _, _, page = _pg(lambda: user_request("GET", table, token, params={**params, "select": select, "limit": str(EXPORT_PAGE), "offset": str(len(rows))}))
        if not page:
            break
        rows.extend(page)
    return rows


@router.get("/audit-trail")
def audit_trail(since: Optional[str] = None, until: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """
    Every decision and zone event of a period, oldest first, as a CSV (a shift's record: choose the shift's start and end).
    Defaults to the last 24 hours.
    """
    org_id, user_id, token = _who(authorization, "safety.view")
    start, end = _window(1, since, until)
    started = datetime.now(timezone.utc)
    window = [f"(observedAt.gte.{start.isoformat()})", f"(observedAt.lt.{end.isoformat()})", f"(createdAt.lte.{started.isoformat()})"]
    base = {"orgId": f"eq.{org_id}", "order": "observedAt.desc,id.desc"}
    # The decisions are frozen at the moment the export starts (rows the sync adds meanwhile cannot shift its pages). Zone
    # events have no creation time, so they are bounded by the period only.
    decisions = _fetch_newest(token, "safety_decisions",
                              "observedAt,workerKey,status,severity,reason,reviewState,ruleType,zone:zones(name),camera:cameras(name),rule:safety_rules(name)",
                              {**base, "and": window})
    events = _fetch_newest(token, "safety_events", "observedAt,workerKey,eventType,details,zone:zones(name),camera:cameras(name)", {**base, "and": window[:2]})
    truncated = len(decisions) > EXPORT_ROW_CAP or len(events) > EXPORT_ROW_CAP
    rows = rg.audit_rows(decisions[:EXPORT_ROW_CAP], events[:EXPORT_ROW_CAP])      # the newest of each kind (they were fetched newest first)
    note = (f"Only the newest {EXPORT_ROW_CAP:,} decisions and {EXPORT_ROW_CAP:,} zone events of this period are included. Choose a shorter period to get the rest."
            if truncated else None)
    try:
        write_audit(org_id, user_id, "safety.audit_trail_exported", "safety_audit_trail", "csv",
                    {"rows": len(rows), "since": start.isoformat(), "until": end.isoformat(), "truncated": truncated})
    except Exception as e:
        logger.error("An audit trail could not be audited, so it was not served: %s", e)
        raise HTTPException(status_code=503, detail="The export could not be recorded, so it was not produced. Try again.")
    payload = rg.build_audit_csv(rows, note).encode("utf-8")
    stamp = lambda d: d.strftime("%Y%m%dT%H%MZ")              # noqa: E731
    headers = {**_NO_STORE, "Content-Disposition": f'attachment; filename="safety-audit-trail-{stamp(start)}-to-{stamp(end)}.csv"'}
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(content=payload, media_type="text/csv; charset=utf-8", headers=headers)
