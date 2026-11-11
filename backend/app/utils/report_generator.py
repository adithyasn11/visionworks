# backend/app/utils/report_generator.py
"""
The safety report as a PDF and as a CSV, both built from the SAME list of tables, so the two can never disagree.

The figures come from the database function `safety_report` (028_safety_report.sql); this module only lays them
out. It never invents or recomputes a number, and it keeps the honest categories the review workflow uses:

    Flagged by system       every potential violation the system raised; nobody has necessarily looked at it
    Confirmed by reviewer   a person looked and agreed
    Dismissed               a person looked and disagreed
    Waiting for review      not yet looked at
    Insufficient evidence   what the system could not decide (it did not guess)

A flag nobody has judged is never called a violation. Workers are never named (the report holds counts, zones,
cameras, rules and times only), and a reader is told in plain words what each figure means.

PDF text is Latin-1 (the standard PDF fonts); anything outside it is shown as "?" rather than a wrong glyph. The CSV is
UTF-8 with a byte-order mark so a spreadsheet reads it, and a cell that starts like a formula is prefixed so it is
shown, not run.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

RULE_LABELS = {          # the same names the rule screens use
    "PPE_ENTRY": "Helmet and vest on entry",
    "PPE_CONTINUOUS": "Keep helmet and vest on while inside",
    "MAX_DWELL": "Longest stay",
    "REQUIRED_EXIT": "Leave through the exit zone",
    "OCCUPANCY": "People in the zone (lone worker, crowding)",
    "MAN_DOWN": "Person lying down",
    "WALK_ONLY": "Walk, do not run",
}

REASON_LABELS = {
    "CORRECT": "The flag was right",
    "PPE_PRESENT": "The equipment was being worn",
    "WRONG_PERSON": "Wrong person",
    "NOT_IN_ZONE": "Not in the zone",
    "BAD_PICTURE": "Picture not good enough",
    "OTHER": "Other",
    "NONE": "No reason given",
}

Table = Tuple[str, List[str], List[List[Any]], Optional[str]]      # (title, headers, rows, note)


# ── small formatting helpers ────────────────────────────────────────────────

def pct(value) -> str:
    """0.9524 -> '95.2%'. None -> 'n/a' (there was nothing to divide)."""
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def num(value) -> str:
    if value is None:
        return "n/a"
    return f"{int(value):,}" if float(value) == int(value) else f"{float(value):,.1f}"


def seconds_text(value) -> str:
    """125 -> '2 min 5 s'."""
    if value is None:
        return "n/a"
    n = float(value)
    if n < 59.95:
        return f"{n:.1f} s" if n != int(n) else f"{int(n)} s"
    total = int(round(n))
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    if h:
        return f"{h} h {m} min"
    return f"{m} min {s} s" if s else f"{m} min"


def rule_label(rule_type: str) -> str:
    return RULE_LABELS.get(rule_type, str(rule_type))


def _moment(value, tz_name: str = "UTC") -> str:
    """An ISO instant as 'DD Mon YYYY HH:MM' in the report's own time zone."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y %H:%M")
    except Exception:
        return str(value)


def csv_cell(value) -> str:
    """One CSV cell. A text that starts like a spreadsheet formula is prefixed so it is shown, not run."""
    if value is None:
        return ""
    s = str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


# ── the tables (shared by the PDF and the CSV) ──────────────────────────────

def _breakdown_rows(items: List[dict], key: str, label=lambda x: x) -> List[List[Any]]:
    return [[label(i.get(key)), i["decisions"], i["compliant"], i["flagged"], i["confirmed"], i["dismissed"], i["pending"], i["insufficient_evidence"]]
            for i in items]


BREAKDOWN_HEADERS = ["Decisions", "Compliant", "Flagged by system", "Confirmed by reviewer", "Dismissed", "Waiting for review", "Insufficient evidence"]


def report_tables(report: dict) -> List[Table]:
    """Every table of the report, in reading order. Rows hold plain numbers and short strings."""
    r = report
    tz = r.get("time_zone") or "UTC"
    tables: List[Table] = []

    tables.append(("Headline", ["Measure", "Value"], [
        ["Decisions made", r["decisions"]],
        ["Compliant", r["compliant"]],
        ["Flagged by system (potential violations)", r["flagged_by_system"]],
        ["Insufficient evidence (the system could not tell)", r["insufficient_evidence"]],
        ["Compliance rate (compliant / (compliant + flagged))", pct(r["compliance_rate"])],
        ["Could-not-verify rate (insufficient evidence / all decisions)", pct(r["unverifiable_rate"])],
        ["Zone events recorded", r["events_recorded"]],
    ], "A flag is a potential violation for a person to judge. Only 'Confirmed by reviewer' has been judged."))

    judged = r["confirmed"] + r["dismissed"]
    tables.append(("What happened to the flags", ["Category", "Count"], [
        ["Flagged by system", r["flagged_by_system"]],
        ["Confirmed by reviewer", r["confirmed"]],
        ["Dismissed by reviewer", r["dismissed"]],
        ["Waiting for review", r["pending"]],
        ["Share of judged flags that were confirmed", pct(r["confirmed_share_of_reviewed"]) + (f" ({r['confirmed']} of {judged})" if judged else "")],
        ["Insufficient evidence (system abstained)", r["insufficient_evidence"]],
        ["... of which a reviewer found real", r["insufficient_confirmed"]],
        ["... of which a reviewer found not real", r["insufficient_dismissed"]],
    ], "Flagged = confirmed + dismissed + waiting. Insufficient-evidence decisions are counted apart: the system did not guess."))

    reasons = r.get("review_reasons") or []
    if reasons:
        tables.append(("Why reviewers decided as they did", ["Decision", "Reason", "Count"],
                       [["Confirmed" if x["state"] == "CONFIRMED" else "Dismissed", REASON_LABELS.get(x["reason"], x["reason"]), x["count"]] for x in reasons], None))

    tables.append(("By rule", ["Rule"] + BREAKDOWN_HEADERS, _breakdown_rows(r["by_rule"], "rule_type", rule_label), None))
    tables.append(("By zone", ["Zone"] + BREAKDOWN_HEADERS, _breakdown_rows(r["by_zone"], "zone"), None))
    tables.append(("By camera", ["Camera"] + BREAKDOWN_HEADERS, _breakdown_rows(r["by_camera"], "camera"), None))

    tables.append((f"By hour of the day ({tz})", ["Hour", "Decisions", "Flagged by system"],
                   [[f"{h['hour']:02d}:00", h["decisions"], h["flagged"]] for h in r["by_hour"] if h["decisions"]], "Hours with no decisions are left out."))
    tables.append((f"By day ({tz})", ["Day", "Decisions", "Flagged by system"], [[d["day"], d["decisions"], d["flagged"]] for d in r["by_day"]],
                   "The first and last day may be partial."))

    p = r["ppe"]
    tables.append(("Helmet and vest", ["Measure", "Count"], [
        ["Checks that were fine (compliant)", p["compliant"]],
        ["Flagged by system", p["flagged"]],
        ["... with no helmet seen", p["missing_helmet"]],
        ["... with no vest seen", p["missing_vest"]],
        ["... with neither seen", p["missing_both"]],
        ["Could not verify", p["could_not_verify"]],
    ], "'No helmet seen' means the system found none; it is a potential violation until a person confirms it."))

    z = r["zone_time"]
    tables.append(("Time inside restricted zones", ["Measure", "Value"], [
        ["Visits timed from start to finish", z["visits_timed"]],
        ["Average time per visit", seconds_text(z["average_seconds"])],
        ["Longest visit", seconds_text(z["max_seconds"])],
        ["Visits not timed", z["visits_not_timed"]],
    ], "Not timed: the worker was already inside when the video began, or was lost from view, or the video stopped. "
       "Their time would only be a part of the stay, so it is left out."))
    if z.get("by_zone"):
        tables.append(("Time inside restricted zones, by zone", ["Zone", "Visits timed", "Average", "Longest"],
                       [[x["zone"], x["visits"], seconds_text(x["average_seconds"]), seconds_text(x["max_seconds"])] for x in z["by_zone"]], None))

    m = r["man_down"]
    o = r["occupancy"]
    lone, over = o["lone_worker"], o["over_capacity"]
    tables.append(("Man-down and head count", ["Incident", "Flagged by system", "Confirmed by reviewer", "Dismissed", "Waiting for review"], [
        ["Person lying still (man-down)", m["flagged"], m["confirmed"], m["dismissed"], m["pending"]],
        ["Lone worker (fewer people than required)", lone["flagged"], lone["confirmed"], lone["dismissed"], lone["pending"]],
        ["Over capacity (more people than allowed)", over["flagged"], over["confirmed"], over["dismissed"], over["pending"]],
    ], f"Could not verify: man-down {m['could_not_verify']}, head count {o['could_not_verify']}."))

    a = r["actions"]
    tables.append(("Corrective actions", ["Measure", "Value"], [
        ["Opened in this period", a["opened"]],
        ["Closed in this period", a["closed"]],
        ["Average days to close (those closed in this period)", num(a["average_days_to_close"])],
        ["Open now", a["open_now"]],
        ["Overdue now (open and past the due date)", a["overdue_now"]],
    ], "Opened counts every action created in the period, including any since cancelled. Open and overdue are as of the moment the report was made."))
    return tables


def report_title(report: dict) -> str:
    tz = report.get("time_zone") or "UTC"
    return f"Safety report {_moment(report['since'], tz)} to {_moment(report['until'], tz)}"


# ── CSV ──────────────────────────────────────────────────────────────────────

def build_csv(report: dict, organisation: str = "") -> str:
    """The report as one CSV: a few lines about it, then every table, each with its own header row, blank line between."""
    tz = report.get("time_zone") or "UTC"
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["VisionWorks safety report"])
    if organisation:
        w.writerow(["Organisation", csv_cell(organisation)])
    w.writerow(["From", _moment(report["since"], tz)])
    w.writerow(["To", _moment(report["until"], tz)])
    w.writerow(["Time zone", tz])
    w.writerow(["Made at", _moment(report["generated_at"], tz)])
    w.writerow(["Note", "Flagged by system means a potential violation for a person to judge. Only Confirmed by reviewer has been judged."])
    for title, headers, rows, note in report_tables(report):
        w.writerow([])
        w.writerow([title])
        w.writerow(headers)
        for row in rows:
            w.writerow([csv_cell(c) for c in row])
        if note:
            w.writerow(["Note", note])
    return "﻿" + out.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────

def _latin(text: Any) -> str:
    """Text the standard PDF fonts can show, escaped for reportlab's paragraph markup."""
    s = "" if text is None else str(text)
    return escape(s.encode("latin-1", "replace").decode("latin-1"))


def build_pdf(report: dict, organisation: str = "", compress: bool = True) -> bytes:
    """The report as a PDF (A4). `compress=False` keeps the page text readable in the bytes (used by the tests)."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table as PdfTable, TableStyle

    tz = report.get("time_zone") or "UTC"
    red, ink, muted, line, wash = colors.HexColor("#DC2626"), colors.HexColor("#111111"), colors.HexColor("#555555"), colors.HexColor("#DDDDDD"), colors.HexColor("#F6F6F6")
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Heading1"], fontSize=20, textColor=ink, spaceAfter=2, leading=24)
    sub = ParagraphStyle("sub", parent=base["Normal"], fontSize=9.5, textColor=muted, leading=13)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=12.5, textColor=ink, spaceBefore=12, spaceAfter=4, leading=15)
    note_style = ParagraphStyle("note", parent=base["Normal"], fontSize=8, textColor=muted, leading=10.5, spaceBefore=2)
    cell = ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=10.5, textColor=ink)
    cell_head = ParagraphStyle("cellh", parent=cell, textColor=colors.white, fontName="Helvetica-Bold", fontSize=7.5, leading=9)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=report_title(report), author="VisionWorks", pageCompression=1 if compress else 0)
    width = A4[0] - 36 * mm

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 9 * mm, "VisionWorks safety report. Counts only: no names, no video.")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    story: List[Any] = [
        Paragraph("Safety report", h1),
        Paragraph(f"{_latin(organisation)}" if organisation else "&nbsp;", sub),
        Paragraph(f"From {_latin(_moment(report['since'], tz))} to {_latin(_moment(report['until'], tz))} ({_latin(tz)})", sub),
        Paragraph(f"Made {_latin(_moment(report['generated_at'], tz))}", sub),
        Spacer(1, 6),
        Paragraph("<b>How to read this report.</b> 'Flagged by system' is a potential violation raised by the software, for a person to judge. "
                  "Only 'Confirmed by reviewer' has been judged by a person. 'Insufficient evidence' is where the system could not tell and did not guess. "
                  "The report holds counts, zones, cameras, rules and times only.", note_style),
    ]

    for title, headers, rows, note in report_tables(report):
        block: List[Any] = [Paragraph(_latin(title), h2)]
        if not rows:
            block.append(Paragraph("Nothing to report for this period.", note_style))
        else:
            data = [[Paragraph(_latin(h), cell_head) for h in headers]] + [[Paragraph(_latin(c), cell) for c in row] for row in rows]
            first = 0.22 if len(headers) > 5 else (0.42 if len(headers) > 3 else 0.62)      # a wide table gets narrow words, not broken ones
            widths = [width * first] + [width * (1 - first) / (len(headers) - 1)] * (len(headers) - 1)
            t = PdfTable(data, colWidths=widths, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), red),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, wash]),
                ("GRID", (0, 0), (-1, -1), 0.4, line),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            block.append(t)
        if note:
            block.append(Paragraph(_latin(note), note_style))
        story.append(KeepTogether(block[:2]))               # the title stays with the start of its table
        story.extend(block[2:])
        if title.startswith("By day") and 1 < len(report["by_day"]) <= 92:
            days = report["by_day"]
            d = Drawing(width, 120)
            chart = VerticalBarChart()
            chart.x, chart.y, chart.width, chart.height = 28, 22, width - 40, 86
            chart.data = [[x["flagged"] for x in days]]
            chart.categoryAxis.categoryNames = [x["day"][5:] for x in days]
            chart.categoryAxis.labels.fontSize = 6
            chart.categoryAxis.labels.angle = 90
            chart.categoryAxis.labels.boxAnchor = "e"
            chart.categoryAxis.labels.dy = -4
            chart.valueAxis.valueMin = 0
            chart.valueAxis.valueMax = max(1, max(x["flagged"] for x in days))
            chart.valueAxis.valueStep = max(1, chart.valueAxis.valueMax // 5)
            chart.valueAxis.labels.fontSize = 7
            chart.bars[0].fillColor = red
            chart.bars[0].strokeColor = None
            d.add(chart)
            story.append(KeepTogether([Paragraph("Flagged by system, per day", note_style), d]))      # the caption stays with its chart

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


# ── the audit trail (a CSV of what happened, in order) ──────────────────────

AUDIT_HEADERS = ["Time (UTC)", "Record", "Camera", "Zone", "Worker", "What", "Result", "Severity", "Review", "Reason or detail"]

STATUS_LABELS = {"COMPLIANT": "Compliant", "POTENTIAL_VIOLATION": "Flagged by system", "UNVERIFIABLE": "Insufficient evidence"}
REVIEW_LABELS = {"NOT_REQUIRED": "Not reviewed", "PENDING": "Waiting for review", "CONFIRMED": "Confirmed by reviewer", "DISMISSED": "Dismissed by reviewer"}
EVENT_LABELS = {
    "ENTERED_ZONE": "Entered the zone", "EXITED_ZONE": "Left the zone", "LOST_IN_ZONE": "Lost from view inside the zone",
    "REAPPEARED": "Back in view", "DWELL_WARNING": "Time warning", "DWELL_EXCEEDED": "Time limit reached", "PPE_VERDICT_CHANGED": "Helmet or vest reading changed",
}


def _worker(key) -> str:
    key = str(key or "")
    return "Worker " + key[4:] if key.startswith("trk-") else ("Whole zone" if key.startswith("zone-") else "Worker")


def _event_detail(event: dict) -> str:
    d = event.get("details") or {}
    kind = event.get("eventType")
    if kind == "EXITED_ZONE":
        why = {"LEFT": "left", "LOST": "lost from view, visit closed", "SEEK": "video jumped, visit closed", "SESSION_END": "still inside when the video ended"}.get(d.get("reason"), "")
        dwell = d.get("dwell_s")
        parts = [why] if why else []
        if isinstance(dwell, (int, float)):
            parts.append(f"{seconds_text(dwell)} in the zone")
        if d.get("first_sighting"):
            parts.append("already inside when first seen")
        if d.get("visit_started_in_replay"):
            parts.append("began in footage that was replayed, so the time is only a part of the stay")
        return "; ".join(parts)
    if kind == "ENTERED_ZONE":
        return "already inside when first seen" if d.get("first_sighting") else ""
    if kind in ("DWELL_WARNING", "DWELL_EXCEEDED"):
        lim = d.get("limit_s")
        return f"limit {seconds_text(lim)}" if isinstance(lim, (int, float)) else ""
    return ""


def audit_rows(decisions: List[dict], events: List[dict]) -> List[List[Any]]:
    """Decisions and zone events merged, oldest first. Same time: decisions after the events that led to them."""
    merged: List[Tuple[str, int, List[Any]]] = []
    for e in events:
        merged.append((str(e.get("observedAt")), 0, [
            str(e.get("observedAt")), "Zone event", (e.get("camera") or {}).get("name", ""), (e.get("zone") or {}).get("name", ""), _worker(e.get("workerKey")),
            EVENT_LABELS.get(e.get("eventType"), e.get("eventType")), "", "", "", _event_detail(e)]))
    for d in decisions:
        merged.append((str(d.get("observedAt")), 1, [
            str(d.get("observedAt")), "Decision", (d.get("camera") or {}).get("name", ""), (d.get("zone") or {}).get("name", ""), _worker(d.get("workerKey")),
            (d.get("rule") or {}).get("name") or rule_label(d.get("ruleType")), STATUS_LABELS.get(d.get("status"), d.get("status")), d.get("severity", ""),
            REVIEW_LABELS.get(d.get("reviewState"), d.get("reviewState")), d.get("reason", "")]))
    merged.sort(key=lambda m: (m[0], m[1]))
    return [m[2] for m in merged]


def build_audit_csv(rows: List[List[Any]], truncated_note: Optional[str] = None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(AUDIT_HEADERS)
    for row in rows:
        w.writerow([csv_cell(c) for c in row])
    if truncated_note:
        w.writerow([truncated_note])
    return "﻿" + out.getvalue()
