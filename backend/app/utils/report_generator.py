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

THE PDF shows every script a site is likely to name its zones in. It is drawn with fpdf2 and uharfbuzz (text SHAPING:
Indic conjuncts and vowel signs, Arabic joining and right-to-left order are laid out correctly, not letter by letter)
and the Noto Sans fonts in `utils/fonts/` (SIL Open Font Licence, see OFL.txt there): Latin, Greek, Cyrillic, Vietnamese,
Devanagari, Bengali, Gujarati, Gurmukhi, Kannada, Malayalam, Odia, Tamil, Telugu, Arabic, Hebrew and Thai. A character
none of them has (Chinese, Japanese, Korean, emoji) is shown as "?" rather than a wrong glyph, and the CSV and the screen
show it exactly. The CSV is UTF-8 with a byte-order mark so a spreadsheet reads it, and a cell that starts like a formula is
prefixed so it is shown, not run.
"""

import csv
import io
import os
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    partial = z.get("visits_partial", 0)
    unmeasured = max(0, z["visits_not_timed"] - partial)
    longest = z.get("longest_seen_seconds")
    rows = [
        ["Visits seen from start to finish", z["visits_timed"]],
        ["Average time per visit (those)", seconds_text(z["average_seconds"])],
        ["Longest of those", seconds_text(z["max_seconds"])],
        ["Visits seen only in part", partial],
        ["Longest time seen in a restricted zone", ("" if z.get("longest_seen_is_complete", True) else "at least ") + seconds_text(longest)],
    ]
    if unmeasured:
        rows.append(["Exits with no time recorded", unmeasured])
    tables.append(("Time inside restricted zones", ["Measure", "Value"], rows,
                   "Seen only in part: the worker was already inside when the video began, was lost from view, or the video stopped. "
                   "They stayed at least the time seen, so those times are not averaged in as if they were whole stays; "
                   "the longest time seen reads 'at least' when it comes from such a visit."))
    if z.get("by_zone"):
        tables.append(("Time inside restricted zones, by zone", ["Zone", "Visits from start to finish", "Average", "Longest", "Seen only in part", "Longest time seen"],
                       [[x["zone"], x["visits"], seconds_text(x["average_seconds"]), seconds_text(x["max_seconds"]), x.get("partial_visits", 0),
                         ("at least " if x.get("partial_visits", 0) and (x.get("longest_seen_seconds") or 0) > (x["max_seconds"] or 0) else "") + seconds_text(x.get("longest_seen_seconds"))]
                        for x in z["by_zone"]], None))

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

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
# The main font, then one font per script it does not cover (tried in this order for each character).
FONT_MAIN = "NotoSans"
FONT_SCRIPTS = ["NotoSansDevanagari", "NotoSansBengali", "NotoSansGujarati", "NotoSansGurmukhi", "NotoSansKannada", "NotoSansMalayalam",
                "NotoSansOriya", "NotoSansTamil", "NotoSansTelugu", "NotoSansArabic", "NotoSansHebrew", "NotoSansThai"]

_COVERAGE: Dict[str, frozenset] = {}


def _font_path(name: str, bold: bool = False) -> str:
    return os.path.join(FONT_DIR, f"{name}-{'Bold' if bold else 'Regular'}.ttf")


def _covers(name: str) -> frozenset:
    """The code points a font file has a glyph for (read once per process)."""
    if name not in _COVERAGE:
        from fontTools.ttLib import TTFont
        font = TTFont(_font_path(name), lazy=True)
        _COVERAGE[name] = frozenset(font.getBestCmap().keys())
        font.close()
    return _COVERAGE[name]


def _plan_fonts(texts) -> Tuple[List[str], Dict[str, str]]:
    """
    Which script fonts a report needs, and what to do with a character no font has. Returns (fonts to add, {character: "?"}).
    Looks only at characters the main font lacks, so an all-Latin report loads one font.
    """
    chars = {c for t in texts for c in str(t) if ord(c) > 0x7E and c.isprintable() and unicodedata.category(c) != "Cf"}
    if not chars:
        return [], {}
    main = _covers(FONT_MAIN)
    need = [c for c in chars if ord(c) not in main]
    used: List[str] = []
    unsupported: Dict[str, str] = {}
    for c in need:
        for name in FONT_SCRIPTS:
            if ord(c) in _covers(name):
                if name not in used:
                    used.append(name)
                break
        else:
            unsupported[c] = "?"
    return [n for n in FONT_SCRIPTS if n in used], unsupported


def _clean(text: Any, unsupported: Dict[str, str]) -> str:
    """Text as the PDF will show it: control characters out, characters no bundled font has as '?'."""
    out = []
    for c in "" if text is None else str(text):
        if c in unsupported:
            out.append(unsupported[c])
        elif unicodedata.category(c) in ("Zs", "Zl", "Zp"):
            out.append(" ")                                  # a no-break space, a thin space, an ideographic space: still a space
        elif "\u202a" <= c <= "\u202e" or "\u2066" <= c <= "\u2069":
            continue                                         # direction OVERRIDES and isolates: a name must not reorder the text around it
        elif c in ("\n", "\t") or c.isprintable() or unicodedata.category(c) == "Cf":
            # (Cf: the zero-width joiner and non-joiner and the direction marks are invisible but Malayalam, Kannada, Persian
            # and mixed right-to-left names need them to be spelt and shaped right, so they stay)
            out.append(" " if c == "\t" else c)
    return "".join(out)


def pdf_font_files() -> List[str]:
    """Every font file the PDF can use (the tests check that they are all present)."""
    return [_font_path(n, b) for n in [FONT_MAIN] + FONT_SCRIPTS for b in (False, True)]


def build_pdf(report: dict, organisation: str = "", compress: bool = True) -> bytes:
    """The report as a PDF (A4)."""
    from fpdf import FPDF
    from fpdf.enums import Align, XPos, YPos
    from fpdf.fonts import FontFace

    tz = report.get("time_zone") or "UTC"
    tables = report_tables(report)
    organisation = organisation or ""
    heading = "Safety report"
    texts = [organisation, heading, report_title(report)]
    for title, headers, rows, note in tables:
        texts += [title, note or ""] + list(headers) + [c for row in rows for c in row]
    extra, unsupported = _plan_fonts(texts)
    clean = lambda t: _clean(t, unsupported)                                          # noqa: E731

    RED, INK, MUTED, LINE, WASH = (220, 38, 38), (17, 17, 17), (85, 85, 85), (221, 221, 221), (246, 246, 246)

    class _Doc(FPDF):
        def footer(self):
            self.set_y(-11)
            self.set_font(FONT_MAIN, "", 7.5)
            self.set_text_color(*MUTED)
            self.cell(0, 5, "VisionWorks safety report. Counts only: no names, no video.", align="L")
            self.set_x(self.l_margin)
            self.cell(0, 5, f"Page {self.page_no()}", align="R")

    if hasattr(FPDF, "_render_styled_text_line"):
        def _announce_font_after_each_line(self, *args, **kwargs):
            # fpdf2 remembers the font it last announced, not the fallback font it switches to for a non-Latin run, so the NEXT
            # line (the second line of a wrapped cell, the next paragraph) could be drawn with the wrong font: garbled letters.
            # After every line, make it announce the font again (one small operator per line). Only where fpdf2 has this
            # (private) method, so a different fpdf2 version loses the workaround, not the report: the generator also makes it
            # announce the font after each paragraph and table (fresh_font below).
            try:
                return FPDF._render_styled_text_line(self, *args, **kwargs)
            finally:
                self.current_font_is_set_on_page = False

        _Doc._render_styled_text_line = _announce_font_after_each_line

    pdf = _Doc(orientation="P", unit="mm", format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_compression(compress)
    pdf.set_title(clean(report_title(report)))
    pdf.set_author("VisionWorks")
    pdf.set_creator("VisionWorks")
    pdf.add_font(FONT_MAIN, "", _font_path(FONT_MAIN))
    pdf.add_font(FONT_MAIN, "B", _font_path(FONT_MAIN, True))
    for name in extra:
        pdf.add_font(name, "", _font_path(name))
        pdf.add_font(name, "B", _font_path(name, True))
    if extra:
        pdf.set_fallback_fonts(extra)
    pdf.set_text_shaping(True)
    pdf.add_page()
    width = pdf.epw

    def fresh_font():
        # fpdf2 remembers the font it last announced, not the fallback font it may have switched to for a non-Latin run, so the
        # next line of plain text could be drawn with the wrong one (garbled). Making it announce the font again is cheap.
        pdf.current_font_is_set_on_page = False

    def para(text, size=8.5, color=INK, bold=False, gap=0.0, line=4.4, align="L"):
        pdf.set_font(FONT_MAIN, "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(width, line, clean(text), align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        fresh_font()
        if gap:
            pdf.ln(gap)

    para(heading, size=20, bold=True, line=9)
    if organisation:
        para(organisation, size=9.5, color=MUTED, line=5)
    para(f"From {_moment(report['since'], tz)} to {_moment(report['until'], tz)} ({tz})", size=9.5, color=MUTED, line=5)
    para(f"Made {_moment(report['generated_at'], tz)}", size=9.5, color=MUTED, line=5, gap=3)
    pdf.set_font(FONT_MAIN, "B", 8)
    pdf.set_text_color(*INK)
    pdf.write(4.2, "How to read this report. ")
    pdf.set_font(FONT_MAIN, "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 4.2, "'Flagged by system' is a potential violation raised by the software, for a person to judge. Only 'Confirmed by reviewer' has been "
                   "judged by a person. 'Insufficient evidence' is where the system could not tell and did not guess. The report holds counts, "
                   "zones, cameras, rules and times only.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    fresh_font()

    for title, headers, rows, note in tables:
        if pdf.get_y() > pdf.h - pdf.b_margin - 38:                    # a title never sits alone at the foot of a page
            pdf.add_page()
        pdf.ln(4)
        para(title, size=12.5, bold=True, line=7)
        if not rows:
            para("Nothing to report for this period.", size=8, color=MUTED)
        else:
            many = len(headers) > 5
            first = (0.2 if len(headers) == 6 else 0.22) if many else (0.42 if len(headers) > 3 else 0.62)      # a wide table gets narrow words, not broken ones
            widths = [first] + [(1 - first) / (len(headers) - 1)] * (len(headers) - 1)
            pdf.set_font(FONT_MAIN, "", 8 if many else 8.5)
            pdf.set_text_color(*INK)
            pdf.set_draw_color(*LINE)
            pdf.set_fill_color(*WASH)                                       # (the chart changes the fill colour, which the table would otherwise reuse)
            with pdf.table(col_widths=tuple(w * width for w in widths), width=width, line_height=4.6 if not many else 4.2,
                           headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=RED, size_pt=7.5 if many else 8.5),
                           cell_fill_color=WASH, cell_fill_mode="ROWS", borders_layout="HORIZONTAL_LINES",
                           text_align=tuple(["LEFT"] * (2 if title.startswith("Why reviewers") else 1) + ["RIGHT"] * (len(headers) - (2 if title.startswith("Why reviewers") else 1)))) as table:
                head = table.row()
                for h in headers:
                    head.cell(clean(h))
                for row in rows:
                    r = table.row()
                    for c in row:
                        r.cell(clean(c))
            fresh_font()
        if note:
            para(note, size=7.5, color=MUTED, line=3.8)
        if title.startswith("By day") and 1 < len(report["by_day"]) <= 92:
            days = report["by_day"]
            if pdf.get_y() > pdf.h - pdf.b_margin - 52:
                pdf.add_page()
            pdf.ln(2)
            para("Flagged by system, per day", size=7.5, color=MUTED, line=4)
            x0, y0, w, h = pdf.l_margin + 8, pdf.get_y() + 2, width - 10, 34
            peak = max(1, max(d["flagged"] for d in days))
            pdf.set_draw_color(*MUTED)
            pdf.line(x0, y0, x0, y0 + h)
            pdf.line(x0, y0 + h, x0 + w, y0 + h)
            pdf.set_font(FONT_MAIN, "", 6)
            pdf.set_text_color(*MUTED)
            pdf.text(x0 - 5, y0 + 2, str(peak))
            pdf.text(x0 - 3, y0 + h, "0")
            slot = w / len(days)
            every = max(1, -(-len(days) // 16))
            pdf.set_fill_color(*RED)
            for k, d in enumerate(days):
                bar = (d["flagged"] / peak) * (h - 2)
                if d["flagged"]:
                    pdf.rect(x0 + k * slot + slot * 0.15, y0 + h - bar, slot * 0.7, bar, style="F")
                if k % every == 0:
                    pdf.text(x0 + k * slot, y0 + h + 3.5, d["day"][5:])
            pdf.set_y(y0 + h + 7)
            pdf.set_fill_color(*WASH)
            fresh_font()

    return bytes(pdf.output())


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
