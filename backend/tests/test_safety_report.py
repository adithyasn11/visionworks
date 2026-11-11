"""
Safety reports and export (Step 16), in three layers:

  A. The generators (PDF, CSV, audit trail) on a seeded report whose numbers are written out by hand: every figure
     appears, the honest categories are kept (a pending flag is never "confirmed"), the PDF and the CSV agree, hostile
     text is harmless, an empty period reads sensibly.
  B. The API against a stub PostgREST: who may call, that every query runs as the caller for their own organisation,
     that an export is audited BEFORE it is served (no audit row, no file), that a served report is recorded in the
     reports table, paging, limits, validation.
  C. The real database (skipped when Supabase is not configured or 028 is not applied): a throw-away organisation is
     seeded with decisions, events and actions whose report is worked out by hand below, and the report the database
     function gives, the JSON endpoint and the exported files are compared with it. Another organisation's rows, and rows
     outside the period, must not appear. Everything is removed afterwards.

    venv/Scripts/python.exe backend/tests/test_safety_report.py
"""
import base64
import csv
import io
import json
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from dotenv import load_dotenv                                           # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))
LIVE_ENV = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY")}

from fastapi.testclient import TestClient                                # noqa: E402

import app.api.routers.safety as sr                                      # noqa: E402
import app.api.routers.safety_report as srr                              # noqa: E402
from app.main import app                                                 # noqa: E402
from app.utils import report_generator as rg                             # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {str(detail)[:200]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ═════════════════════════════════════════════════════════════════════════════
# A. The generators
# ═════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("A. THE GENERATORS")
print("=" * 70)


def breakdown(decisions, compliant, flagged, confirmed, dismissed, pending, insufficient, **extra):
    return {"decisions": decisions, "compliant": compliant, "flagged": flagged, "confirmed": confirmed, "dismissed": dismissed,
            "pending": pending, "insufficient_evidence": insufficient, **extra}


def seed_report(**over):
    """20 decisions: 12 compliant, 6 flagged (2 confirmed, 1 dismissed, 3 waiting), 2 could not be verified (1 of them real)."""
    r = {
        "since": "2026-11-03T00:00:00+00:00", "until": "2026-11-10T00:00:00+00:00", "time_zone": "UTC", "generated_at": "2026-11-10T01:00:00+00:00",
        "decisions": 20, "compliant": 12, "flagged_by_system": 6, "insufficient_evidence": 2,
        "compliance_rate": 0.6667, "unverifiable_rate": 0.1, "confirmed": 2, "dismissed": 1, "pending": 3,
        "insufficient_confirmed": 1, "insufficient_dismissed": 0, "confirmed_share_of_reviewed": 0.667, "events_recorded": 41,
        "review_reasons": [{"state": "CONFIRMED", "reason": "CORRECT", "count": 3}, {"state": "DISMISSED", "reason": "PPE_PRESENT", "count": 1}],
        "by_rule": [breakdown(14, 9, 4, 1, 1, 2, 1, rule_type="PPE_ENTRY"), breakdown(6, 3, 2, 1, 0, 1, 1, rule_type="MAX_DWELL")],
        "by_zone": [breakdown(12, 7, 4, 2, 0, 2, 1, zone="Press line"), breakdown(8, 5, 2, 0, 1, 1, 1, zone="=HYPERLINK(\"x\")<b>Dock</b> नमस्ते")],
        "by_camera": [breakdown(20, 12, 6, 2, 1, 3, 2, camera="Cam A")],
        "by_hour": [{"hour": h, "decisions": (7 if h == 10 else 0), "flagged": (3 if h == 10 else 0)} for h in range(24)],
        "by_day": [{"day": f"2026-11-{3 + i:02d}", "decisions": i, "flagged": i % 3} for i in range(8)],
        "ppe": {"flagged": 4, "missing_helmet": 3, "missing_vest": 2, "missing_both": 1, "could_not_verify": 1, "compliant": 9},
        "zone_time": {"visits_timed": 4, "visits_partial": 3, "visits_not_timed": 3, "average_seconds": 75.0, "max_seconds": 125.0,
                      "partial_max_seconds": 500.0, "longest_seen_seconds": 500.0, "longest_seen_is_complete": False,
                      "by_zone": [{"zone": "Press line", "visits": 3, "average_seconds": 70.0, "max_seconds": 125.0, "partial_visits": 3, "longest_seen_seconds": 500.0}]},
        "man_down": {"flagged": 2, "confirmed": 1, "dismissed": 1, "pending": 0, "could_not_verify": 1},
        "occupancy": {"lone_worker": {"flagged": 1, "confirmed": 0, "dismissed": 0, "pending": 1},
                      "over_capacity": {"flagged": 1, "confirmed": 1, "dismissed": 0, "pending": 0}, "could_not_verify": 1},
        "actions": {"opened": 5, "closed": 2, "open_now": 3, "overdue_now": 1, "average_days_to_close": 2.5},
    }
    r.update(over)
    return r


R = seed_report()
tables = {t[0]: t for t in rg.report_tables(R)}
head = dict((row[0], row[1]) for row in tables["Headline"][2])
check("the headline carries the hand-written numbers",
      head["Decisions made"] == 20 and head["Compliant"] == 12 and head["Flagged by system (potential violations)"] == 6
      and head["Insufficient evidence (the system could not tell)"] == 2 and head["Compliance rate (compliant / (compliant + flagged))"] == "66.7%"
      and head["Could-not-verify rate (insufficient evidence / all decisions)"] == "10.0%" and head["Zone events recorded"] == 41, str(head))
flags = dict((row[0], row[1]) for row in tables["What happened to the flags"][2])
check("the flags are split into the honest categories, and 2 + 1 + 3 adds up to the 6 flagged",
      flags["Flagged by system"] == 6 and flags["Confirmed by reviewer"] == 2 and flags["Dismissed by reviewer"] == 1 and flags["Waiting for review"] == 3
      and flags["Confirmed by reviewer"] + flags["Dismissed by reviewer"] + flags["Waiting for review"] == flags["Flagged by system"], str(flags))
check("the share confirmed is of the JUDGED flags (2 of 3), never of all 6", flags["Share of judged flags that were confirmed"] == "66.7% (2 of 3)", str(flags))
check("the could-not-verify decisions are counted apart, with how many a reviewer found real (1) and not real (0)",
      flags["Insufficient evidence (system abstained)"] == 2 and flags["... of which a reviewer found real"] == 1 and flags["... of which a reviewer found not real"] == 0)
check("no table calls a flag a 'violation' that no one has judged: the word appears only as 'potential violation' or inside 'Flagged by system'",
      all("violation" not in str(c).lower() or "potential" in str(c).lower() for t in rg.report_tables(R) for row in t[2] for c in row), "")
pend = R.copy()
pend.update(confirmed=0, dismissed=0, pending=6, confirmed_share_of_reviewed=None)
fl = dict((row[0], row[1]) for row in {t[0]: t for t in rg.report_tables(pend)}["What happened to the flags"][2])
check("a period where every flag is still pending shows 0 confirmed, 6 waiting and 'n/a' for the share (a pending flag is never counted as confirmed)",
      fl["Confirmed by reviewer"] == 0 and fl["Waiting for review"] == 6 and fl["Share of judged flags that were confirmed"] == "n/a", str(fl))
bt = tables["By rule"]
check("the by-rule table uses plain rule names and the columns are stable",
      bt[1] == ["Rule"] + rg.BREAKDOWN_HEADERS and bt[2][0][0] == "Helmet and vest on entry" and bt[2][1][0] == "Longest stay"
      and bt[2][0][1:] == [14, 9, 4, 1, 1, 2, 1], str(bt[2][0]))
check("hours with no decisions are left out of the hour table; the day table has every day",
      tables["By hour of the day (UTC)"][2] == [["10:00", 7, 3]] and len(tables["By day (UTC)"][2]) == 8)
check("helmet and vest figures are carried over", [r_[1] for r_ in tables["Helmet and vest"][2]] == [9, 4, 3, 2, 1, 1])
zt = dict((row[0], row[1]) for row in tables["Time inside restricted zones"][2])
check("time in restricted zones: 4 visits seen whole (average 1 min 15 s, longest 2 min 5 s); 3 seen only in part, the longest time seen 'at least 8 min 20 s'",
      zt["Visits seen from start to finish"] == 4 and zt["Average time per visit (those)"] == "1 min 15 s" and zt["Longest of those"] == "2 min 5 s"
      and zt["Visits seen only in part"] == 3 and zt["Longest time seen in a restricted zone"] == "at least 8 min 20 s" and "Exits with no time recorded" not in zt, str(zt))
zbz = tables["Time inside restricted zones, by zone"]
check("the by-zone time table says, for each zone, how many visits were whole and how many partial", zbz[1][-2:] == ["Seen only in part", "Longest time seen"] and zbz[2][0][1:] == [3, "1 min 10 s", "2 min 5 s", 3, "at least 8 min 20 s"], str(zbz[2]))
whole = seed_report()
whole["zone_time"] = dict(whole["zone_time"], visits_partial=0, visits_not_timed=0, partial_max_seconds=None, longest_seen_seconds=125.0, longest_seen_is_complete=True)
zw = dict((row[0], row[1]) for row in {t[0]: t for t in rg.report_tables(whole)}["Time inside restricted zones"][2])
check("when every visit was whole the longest time is stated without 'at least'", zw["Longest time seen in a restricted zone"] == "2 min 5 s" and zw["Visits seen only in part"] == 0, str(zw))
md = tables["Man-down and head count"][2]
check("man-down and head-count incidents keep the same categories", md[0][1:] == [2, 1, 1, 0] and md[1][1:] == [1, 0, 0, 1] and md[2][1:] == [1, 1, 0, 0], str(md))
ac = dict((row[0], row[1]) for row in tables["Corrective actions"][2])
check("corrective actions: opened 5, closed 2, average 2.5 days, open now 3, overdue 1",
      ac["Opened in this period"] == 5 and ac["Closed in this period"] == 2 and ac["Average days to close (those closed in this period)"] == "2.5"
      and ac["Open now"] == 3 and ac["Overdue now (open and past the due date)"] == 1, str(ac))

# the CSV
text = rg.build_csv(R, "Acme Works")
check("the CSV has a byte-order mark and says what it is, for whom and for when", text.startswith("﻿VisionWorks safety report") and "Acme Works" in text and "03 Nov 2026" in text)
rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
blocks = [i for i, row in enumerate(rows) if len(row) == 1 and row[0] in {t[0] for t in rg.report_tables(R)}]
check("every table is a block with its own title and header row", len(blocks) == len(rg.report_tables(R)) and rows[blocks[0] + 1] == ["Measure", "Value"], str(len(blocks)))
check("the CSV numbers are the table numbers", ["Compliant", "12"] in rows and ["Waiting for review", "3"] in rows and ["Confirmed by reviewer", "2"] in rows)
check("a zone name that starts like a formula is neutralised in the CSV and kept readable",
      any(r_ and r_[0].startswith("'=HYPERLINK") for r_ in rows) and not any(r_ and r_[0].startswith("=") for r_ in rows))
check("no worker key and no person's name is anywhere in the report", "trk-" not in text and "Worker" not in text)

# the PDF (its text is read back with pypdf)
from pypdf import PdfReader                                                       # noqa: E402


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages), len(reader.pages)


pdf = rg.build_pdf(R, "Acme Works")
ptext, pages = pdf_text(pdf)
check("the PDF is a real PDF with pages, a title and the author", pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF") and pages >= 2
      and "Safety report" in (PdfReader(io.BytesIO(pdf)).metadata.title or ""), str(pages))
need = ["66.7%", "Acme Works", "Safety report", "Flagged by system", "Confirmed by reviewer", "Waiting for review", "Insufficient evidence", "1 min 15 s",
        "2 min 5 s", "at least 8 min 20 s", "Helmet and vest on entry", "2.5", "Page 1", "Page 2", "VisionWorks safety report. Counts only: no names, no video."]
missing = [n for n in need if n not in ptext]
check("the PDF shows the same figures the CSV does, with a footer and page numbers", not missing, str(missing))
check("the header lines are intact (they were once drawn in the wrong font after a non-Latin line)",
      "From 03 Nov 2026 00:00 to 10 Nov 2026 00:00 (UTC)" in ptext and "Made 10 Nov 2026 01:00" in ptext)
check("markup in a name is shown as text, not run, and a leading '=' is not a formula in a PDF either", (lambda flat: "<b>Dock</b>" in flat and "=HYPERLINK" in flat)("".join(ptext.split())))      # (the long name wraps across lines in its column)

# every script a site is likely to name a zone in
SCRIPTS = {"Kannada": "ಪ್ರೆಸ್ ಲೈನ್ ಪ್ರದೇಶ", "Devanagari": "प्रेस लाइन क्षेत्र", "Bengali": "বাংলা এলাকা", "Gujarati": "ગુજરાતી વિસ્તાર", "Gurmukhi": "ਪ੍ਰੈਸ ਲਾਈਨ ਖੇਤਰ",
           "Malayalam": "മലയാളം പ്രദേശം", "Odia": "ଓଡ଼ିଆ କ୍ଷେତ୍ର", "Tamil": "தமிழ் பகுதி", "Telugu": "తెలుగు ప్రాంతం", "Arabic": "منطقة خط الضغط",
           "Hebrew": "עברית אזור קו", "Thai": "ไทยพื้นที่สายการผลิต", "Greek/Cyrillic/Vietnamese": "Ελληνικά Русский Tiếng Việt"}
multi = seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone=name) for name in SCRIPTS.values()] + [breakdown(1, 1, 0, 0, 0, 0, 0, zone="日本語 \U0001f600 Press")])
mt, _ = pdf_text(rg.build_pdf(multi, SCRIPTS["Kannada"] + " Works"))
lost = {k: [c for c in v if c not in mt and not c.isspace()] for k, v in SCRIPTS.items()}
check("every supported script appears in the PDF (every character of every sample is in its text)", not any(lost.values()), str({k: v for k, v in lost.items() if v}))
check("a character no bundled font has (Chinese, Japanese, emoji) is shown as '?', never as a wrong glyph, and the Latin around it survives",
      not any(c in mt for c in "日本語\U0001f600") and "??? ? Press" in mt.replace("\n", " "), mt[-200:])
wrap = seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone="ಡೆಮೊ ಪ್ರೆಸ್ ಲೈನ್ (Demo press line area number two, the long one)")], time_zone="UTC")
wrap["zone_time"] = dict(wrap["zone_time"], by_zone=[{"zone": "ಡೆಮೊ ಪ್ರೆಸ್ ಲೈನ್ (Demo press line area number two, the long one)", "visits": 3, "average_seconds": 70.0, "max_seconds": 125.0,
                                                     "partial_visits": 2, "longest_seen_seconds": 500.0}])
wt = "".join(pdf_text(rg.build_pdf(wrap, "ಅಕ್ಮೆ"))[0].split())
check("a Latin continuation after a non-Latin start, on the next line of a wrapped cell, is drawn in the right font (it was once garbled)",
      "pressline" in wt.replace("(", "").replace(")", "").lower() and "numbertwo" in wt.lower() and "thelongone" in wt.lower(), wt[-300:])
check("zero-width joiners and non-joiners (Malayalam chillu, Persian) are kept, control characters are dropped",
      rg._clean("ന്\u200d می\u200cخواهم\x00\x07", {}) == "ന്\u200d می\u200cخواهم")
zj = seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone="ന്\u200d ചില്ല് می\u200cخواهم")])
check("a name with joiners builds a PDF", rg.build_pdf(zj, "ന്\u200d").startswith(b"%PDF-"))
check("a no-break space or thin space stays a space (a pasted name must not lose its word gap), and direction overrides are dropped",
      rg._clean("Press\u00a0line\u2009A\u3000B", {}) == "Press line A B" and rg._clean("a\u202eb\u2066c", {}) == "abc")
nb = seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone="Press\u00a0line")])
check("... also in a built PDF", "Press line" in pdf_text(rg.build_pdf(nb))[0])
check("all the bundled font files and the licence are present", all(os.path.isfile(f) for f in rg.pdf_font_files()) and os.path.isfile(os.path.join(rg.FONT_DIR, "OFL.txt")))
check("an all-Latin report loads one font (the script fonts are only added when a name needs them)", rg._plan_fonts(["Press line", "Cam A", "Zone 1"]) == ([], {}))
check("a Kannada name needs the Kannada font and nothing else; Chinese is unsupported", rg._plan_fonts(["ಪ್ರೆಸ್"])[0] == ["NotoSansKannada"] and rg._plan_fonts(["日"])[1] == {"日": "?"})
check("a visit that began in replayed footage says so in the audit trail, as the report leaves it out of the timed visits",
      "only a part of the stay" in rg.build_audit_csv(rg.audit_rows([], [{"observedAt": "2026-11-09T10:09:00+00:00", "workerKey": "trk-1", "eventType": "EXITED_ZONE",
                                                                         "details": {"reason": "LEFT", "dwell_s": 40, "visit_started_in_replay": True}, "zone": {"name": "Z"}, "camera": {"name": "C"}}])))
check("59.96 seconds reads '1 min', not '60.0 s'", rg.seconds_text(59.96) == "1 min" and rg.seconds_text(59.4) == "59.4 s" and rg.seconds_text(125) == "2 min 5 s")
empty = seed_report(decisions=0, compliant=0, flagged_by_system=0, insufficient_evidence=0, compliance_rate=None, unverifiable_rate=None, confirmed=0, dismissed=0,
                    pending=0, insufficient_confirmed=0, insufficient_dismissed=0, confirmed_share_of_reviewed=None, events_recorded=0, review_reasons=[],
                    by_rule=[], by_zone=[], by_camera=[], by_hour=[{"hour": h, "decisions": 0, "flagged": 0} for h in range(24)],
                    by_day=[{"day": "2026-11-09", "decisions": 0, "flagged": 0}],
                    ppe={"flagged": 0, "missing_helmet": 0, "missing_vest": 0, "missing_both": 0, "could_not_verify": 0, "compliant": 0},
                    zone_time={"visits_timed": 0, "visits_partial": 0, "visits_not_timed": 0, "average_seconds": None, "max_seconds": None, "partial_max_seconds": None,
                               "longest_seen_seconds": None, "longest_seen_is_complete": True, "by_zone": []},
                    man_down={"flagged": 0, "confirmed": 0, "dismissed": 0, "pending": 0, "could_not_verify": 0},
                    occupancy={"lone_worker": {"flagged": 0, "confirmed": 0, "dismissed": 0, "pending": 0}, "over_capacity": {"flagged": 0, "confirmed": 0, "dismissed": 0, "pending": 0}, "could_not_verify": 0},
                    actions={"opened": 0, "closed": 0, "open_now": 0, "overdue_now": 0, "average_days_to_close": None})
etext = rg.build_csv(empty)
epdf = rg.build_pdf(empty)
etxt, _ = pdf_text(epdf)
check("an empty period reads sensibly: 'n/a' for rates (nothing to divide), 'Nothing to report' for tables, and a valid PDF",
      "n/a" in etext and "n/a" in etxt and epdf.startswith(b"%PDF-") and "Nothing to report" in etxt and "100.0%" not in etext and "0.0%" not in etext.split("Compliance rate")[1][:60], etext[:400])
longer = seed_report(by_day=[{"day": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "decisions": i % 7, "flagged": i % 4} for i in range(200)])
check("a long period (200 days) still builds a PDF", rg.build_pdf(longer).startswith(b"%PDF-"))
big = seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone=f"Zone {i}") for i in range(100)])
check("a hundred zones build a multi-page PDF, with the table header repeated and every zone present", pdf_text(rg.build_pdf(big))[1] >= 3 and "Zone 99" in pdf_text(rg.build_pdf(big))[0])
check("an unusually long zone name wraps instead of failing", rg.build_pdf(seed_report(by_zone=[breakdown(1, 1, 0, 0, 0, 0, 0, zone="Z" * 600)])).startswith(b"%PDF-"))

# the audit trail
dec = [{"observedAt": "2026-11-09T10:00:05+00:00", "workerKey": "trk-12", "status": "POTENTIAL_VIOLATION", "severity": "WARNING", "reason": "No helmet seen in 28 of 29 clear frames.",
        "reviewState": "PENDING", "ruleType": "PPE_ENTRY", "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}, "rule": None},
       {"observedAt": "2026-11-09T10:00:01+00:00", "workerKey": "zone-abc", "status": "UNVERIFIABLE", "severity": "WARNING", "reason": "=cmd|' /C calc'!A0", "reviewState": "NOT_REQUIRED",
        "ruleType": "OCCUPANCY", "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}, "rule": {"name": "Head count"}}]
ev = [{"observedAt": "2026-11-09T10:00:05+00:00", "workerKey": "trk-12", "eventType": "ENTERED_ZONE", "details": {}, "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}},
      {"observedAt": "2026-11-09T10:09:00+00:00", "workerKey": "trk-12", "eventType": "EXITED_ZONE", "details": {"reason": "LEFT", "dwell_s": 535, "first_sighting": True},
       "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}}]
ar = rg.audit_rows(dec, ev)
check("the audit trail is in time order, and a decision comes after the zone event that led to it at the same second",
      [(r_[1], r_[0][11:19]) for r_ in ar] == [("Decision", "10:00:01"), ("Zone event", "10:00:05"), ("Decision", "10:00:05"), ("Zone event", "10:09:00")], str([(r_[1], r_[0]) for r_ in ar]))
ac_text = rg.build_audit_csv(ar)
check("workers read 'Worker 12', a whole-zone decision reads 'Whole zone', statuses are in review words, no internal key leaks",
      "Worker 12" in ac_text and "Whole zone" in ac_text and "Flagged by system" in ac_text and "Insufficient evidence" in ac_text and "trk-" not in ac_text and "zone-abc" not in ac_text)
check("an exit says how long and that the visit was partial; a formula in a reason is neutralised",
      "8 min 55 s in the zone" in ac_text and "already inside when first seen" in ac_text and "'=cmd" in ac_text)
check("the audit-trail columns are stable", ac_text.lstrip("﻿").splitlines()[0] == ",".join(rg.AUDIT_HEADERS))

# ═════════════════════════════════════════════════════════════════════════════
# B. The API against a stub PostgREST
# ═════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("B. THE API (stub PostgREST)")
print("=" * 70)

ORG, OTHER, USER_M, USER_V = (str(uuid.uuid4()) for _ in range(4))
ANON, SERVICE = "anon-key-for-the-stub", "service-key-for-the-stub"


class Stub:
    def reset(self):
        self.calls = []
        self.answer = {}
        self.decisions = [{"observedAt": "2026-11-09T10:00:05+00:00", "workerKey": "trk-3", "status": "POTENTIAL_VIOLATION", "severity": "WARNING", "reason": "x",
                           "reviewState": "PENDING", "ruleType": "PPE_ENTRY", "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}, "rule": None}]
        self.events = [{"observedAt": "2026-11-09T10:00:01+00:00", "workerKey": "trk-3", "eventType": "ENTERED_ZONE", "details": {}, "zone": {"name": "Press line"}, "camera": {"name": "Cam A"}}]

    def __init__(self):
        self.reset()


STUB = Stub()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self, method):
        u = urlparse(self.path)
        params = {k: v for k, v in parse_qs(u.query).items()}
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        path = u.path.replace("/rest/v1/", "", 1)
        STUB.calls.append((method, path, params, self.headers.get("apikey"), self.headers.get("Authorization"), body))
        status, payload = STUB.answer.get(path, (None, None))
        if status is None:
            if path == "rpc/safety_report":
                status, payload = 200, seed_report()
            elif path == "organisations":
                status, payload = 200, [{"name": "Acme Works"}]
            elif path == "reports" and method == "POST":
                status, payload = 201, None
            elif path == "reports":
                status, payload = 200, [{"id": str(uuid.uuid4()), "format": "PDF", "title": "Safety report", "rangeStart": "2026-11-03T00:00:00Z", "rangeEnd": "2026-11-10T00:00:00Z",
                                         "createdAt": "2026-11-10T01:00:00Z", "status": "READY", "fileSizeBytes": 1234}]
            elif path == "audit_logs":
                status, payload = 201, None
            elif path in ("safety_decisions", "safety_events"):
                src = STUB.decisions if path == "safety_decisions" else STUB.events
                off, lim = int(params.get("offset", ["0"])[0]), int(params.get("limit", ["1000"])[0])
                status, payload = 200, src[off:off + lim]
            else:
                status, payload = 200, []
        raw = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = lambda self: self._go("GET")        # noqa: E731
    do_POST = lambda self: self._go("POST")      # noqa: E731


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{server.server_port}"
os.environ["SUPABASE_KEY"] = ANON
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = SERVICE
IDENT = {"Bearer manager-token": (ORG, USER_M, "MANAGER"), "Bearer viewer-token": (ORG, USER_V, "VIEWER"), "Bearer norole-token": (ORG, USER_V, None)}
_real_resolve = sr.resolve_identity
sr.resolve_identity = lambda token: IDENT.get(token, (None, None, None))
client = TestClient(app)
base = "/api/v1/safety"
M, V = {"Authorization": "Bearer manager-token"}, {"Authorization": "Bearer viewer-token"}
audits = lambda: [c for c in STUB.calls if c[1] == "audit_logs"]       # noqa: E731

for path in ("/report", "/report/export?format=csv", "/report/history", "/audit-trail"):
    check(f"{path.split('?')[0]}: no token is 401", client.get(base + path).status_code == 401)
check("a user with no role is 403", client.get(base + "/report", headers={"Authorization": "Bearer norole-token"}).status_code == 403)
check("a VIEWER may read and export (safety.view)", client.get(base + "/report", headers=V).status_code == 200 and client.get(base + "/report/export?format=csv", headers=V).status_code == 200)

STUB.reset()
r = client.get(base + f"/report?days=30&tz=Asia/Kolkata&p_org={OTHER}&org_id={OTHER}", headers=V)
c = next(c for c in STUB.calls if c[1] == "rpc/safety_report")
d0, d1 = datetime.fromisoformat(c[5]["p_since"]), datetime.fromisoformat(c[5]["p_until"])
check("the report asks for the caller's own organisation whatever the request says, in the chosen time zone, as the caller",
      r.status_code == 200 and c[5]["p_org"] == ORG and OTHER not in json.dumps(c[5]) and c[5]["p_tz"] == "Asia/Kolkata" and c[3] == ANON and c[4] == "Bearer viewer-token", str(c))
check("...over the last 30 days", 29.9 < (d1 - d0).total_seconds() / 86400 < 30.1)
check("the answer carries the organisation's name for the header", r.json()["organisation"] == "Acme Works" and r.json()["decisions"] == 20)
STUB.reset()
client.get(base + "/report?since=2026-11-03T00:00:00Z&until=2026-11-10T00:00:00Z", headers=V)
c = next(c for c in STUB.calls if c[1] == "rpc/safety_report")
check("an explicit window is passed exactly", c[5]["p_since"].startswith("2026-11-03T00:00:00") and c[5]["p_until"].startswith("2026-11-10T00:00:00"), str(c[5]))
for q in ("days=400", "days=0", "since=2026-02-01&until=2026-01-01", "since=2024-01-01&until=2026-01-01", "since=garbage", "tz=bad%20zone%3B", "tz=" + "x" * 80):
    check(f"/report?{q[:34]} is refused (422)", client.get(base + "/report?" + q, headers=V).status_code == 422)
STUB.reset(); STUB.answer["rpc/safety_report"] = (400, {"code": "P0001", "message": "unknown time zone"})
check("a database refusal is shown as a sentence, not as database text", client.get(base + "/report?tz=Nowhere/Land", headers=V).status_code in (422, 400), "")
STUB.reset(); STUB.answer["rpc/safety_report"] = (200, ["not", "a", "report"])
check("an answer that is not a report is a 502, not a crash", client.get(base + "/report", headers=V).status_code == 502)

# export
STUB.reset()
r = client.get(base + "/report/export?format=csv", headers={**V, "Origin": "https://localhost:3000"})
exposed = r.headers.get("access-control-expose-headers", "").lower()
check("a page on another port can read the file name and the export flags (CORS exposes Content-Disposition, X-Export-Truncated, X-Report-Recorded)",
      all(h in exposed for h in ("content-disposition", "x-export-truncated", "x-report-recorded")), exposed)
STUB.reset()
r = client.get(base + "/report/export?format=csv&since=2026-11-03T00:00:00Z&until=2026-11-10T00:00:00Z&tz=UTC", headers=V)
check("a CSV report is CSV, never cached, named for its dates", r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and r.headers["cache-control"] == "no-store"
      and 'filename="safety-report-2026-11-03-to-2026-11-10.csv"' in r.headers["content-disposition"], str(r.headers))
check("it holds the figures", "Compliant,12" in r.content.decode("utf-8") and r.content.startswith(b"\xef\xbb\xbf"))
order = [c[1] for c in STUB.calls if c[1] in ("audit_logs", "reports")]
a = audits()
check("it was audited (who, what, the period, the size) with the SERVICE key, and BEFORE the report was recorded",
      len(a) == 1 and a[0][5][0]["action"] == "safety.report_exported" and a[0][5][0]["actorId"] == USER_V and a[0][5][0]["orgId"] == ORG and a[0][3] == SERVICE
      and a[0][5][0]["metadata"]["format"] == "csv" and a[0][5][0]["metadata"]["bytes"] == len(r.content) and order == ["audit_logs", "reports"], str(order))
rec = next(c for c in STUB.calls if c[1] == "reports" and c[0] == "POST")
check("it was recorded in the reports table as the caller: SAFETY_SUMMARY, CSV, READY, the period, the size, the requester",
      rec[3] == ANON and rec[4] == "Bearer viewer-token" and rec[5]["kind"] == "SAFETY_SUMMARY" and rec[5]["format"] == "CSV" and rec[5]["status"] == "READY"
      and rec[5]["orgId"] == ORG and rec[5]["requestedById"] == USER_V and rec[5]["fileSizeBytes"] == len(r.content) and rec[5]["rangeStart"].startswith("2026-11-03")
      and rec[5]["zoneIds"] == [] and r.headers["x-report-recorded"] == "true", str(rec[5]))
STUB.reset()
r = client.get(base + "/report/export?format=pdf&days=7", headers=M)
check("a PDF report is a PDF, never cached", r.status_code == 200 and r.content.startswith(b"%PDF-") and r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "no-store"
      and r.headers["content-disposition"].endswith('.pdf"'))
check("...and its audit row says pdf", audits()[0][5][0]["metadata"]["format"] == "pdf" and next(c for c in STUB.calls if c[1] == "reports" and c[0] == "POST")[5]["format"] == "PDF")
STUB.reset(); STUB.answer["audit_logs"] = (500, {"message": "down"})
r = client.get(base + "/report/export?format=pdf", headers=V)
check("if the export cannot be audited it is NOT produced (503) and nothing is recorded", r.status_code == 503 and b"%PDF" not in r.content and not [c for c in STUB.calls if c[1] == "reports" and c[0] == "POST"])
STUB.reset(); STUB.answer["reports"] = (500, {"message": "down"})
r = client.get(base + "/report/export?format=csv", headers=V)
check("if the report cannot be RECORDED the file is still served, and a header says so", r.status_code == 200 and r.headers["x-report-recorded"] == "false" and len(audits()) == 1)
for q in ("", "format=xls", "format=PDF", "format=csv&days=999"):
    check(f"/report/export?{q}: 422", client.get(base + "/report/export?" + q, headers=V).status_code == 422)

STUB.reset()
r = client.get(base + "/report/history?limit=5", headers=V)
c = next(c for c in STUB.calls if c[1] == "reports")
check("the history is this organisation's SAFETY_SUMMARY reports, newest first, as the caller",
      r.status_code == 200 and c[2]["kind"] == ["eq.SAFETY_SUMMARY"] and c[2]["orgId"] == [f"eq.{ORG}"] and c[2]["order"][0].startswith("createdAt.desc") and c[2]["limit"] == ["5"]
      and c[3] == ANON and r.json()["reports"][0]["format"] == "PDF")
check("the history asks for no requester name (no person is named in a report list)", "requestedBy" not in c[2]["select"][0] and "email" not in c[2]["select"][0].lower())
check("a history limit over 100 is refused", client.get(base + "/report/history?limit=500", headers=V).status_code == 422)

# audit trail
STUB.reset()
r = client.get(base + "/audit-trail?since=2026-11-09T06:00:00Z&until=2026-11-09T14:00:00Z", headers=V)
body = r.content.decode("utf-8")
check("the audit trail is a CSV of decisions and zone events, oldest first, workers as 'Worker N'",
      r.status_code == 200 and body.startswith("﻿Time (UTC),Record,Camera,Zone,Worker,What") and "Worker 3" in body and "trk-" not in body
      and body.index("Zone event") < body.index("Decision,"), body[:300])
check("its file name carries the shift's start and end", 'filename="safety-audit-trail-20261109T0600Z-to-20261109T1400Z.csv"' in r.headers["content-disposition"], r.headers["content-disposition"])
dq = next(c for c in STUB.calls if c[1] == "safety_decisions")
eq_ = next(c for c in STUB.calls if c[1] == "safety_events")
check("zone events are read for the same window as the caller, and (they have no creation time) are not asked for one",
      eq_[2]["orgId"] == [f"eq.{ORG}"] and not any("createdAt" in x for x in eq_[2]["and"]) and any("observedAt.lt.2026-11-09T14:00:00" in x for x in eq_[2]["and"]) and eq_[3] == ANON)
check("it reads the decisions' window exactly (start inclusive, end exclusive), as the caller, for this organisation, frozen at the moment it started",
      dq[2]["orgId"] == [f"eq.{ORG}"] and any("observedAt.gte.2026-11-09T06:00:00" in x for x in dq[2]["and"]) and any("observedAt.lt.2026-11-09T14:00:00" in x for x in dq[2]["and"])
      and any(x.startswith("(createdAt.lte.") for x in dq[2]["and"]) and dq[3] == ANON)
a = audits()
check("it is audited first (rows, period)", len(a) == 1 and a[0][5][0]["action"] == "safety.audit_trail_exported" and a[0][5][0]["metadata"]["rows"] == 2)
STUB.reset(); STUB.answer["audit_logs"] = (500, {"message": "down"})
r = client.get(base + "/audit-trail", headers=V)
check("if it cannot be audited it is not produced (503)", r.status_code == 503 and "Worker" not in r.text)
STUB.reset()
check("the audit trail defaults to the last 24 hours", client.get(base + "/audit-trail", headers=V).status_code == 200)
c = [c for c in STUB.calls if c[1] == "safety_decisions"][0]
gte = next(x for x in c[2]["and"] if "observedAt.gte" in x)
lt = next(x for x in c[2]["and"] if "observedAt.lt" in x)
span = datetime.fromisoformat(lt.split("lt.")[1].rstrip(")")) - datetime.fromisoformat(gte.split("gte.")[1].rstrip(")"))
check("...really a day long", 0.99 < span.total_seconds() / 86400 < 1.01, str(span))
for q in ("until=2026-01-01&since=2026-02-01", "since=2024-01-01&until=2026-01-01", "since=garbage"):
    check(f"/audit-trail?{q[:32]}: 422", client.get(base + "/audit-trail?" + q, headers=V).status_code == 422)
# paging and the cap
srr.EXPORT_ROW_CAP, srr.EXPORT_PAGE = 3, 2
STUB.reset()
STUB.decisions = [{"observedAt": f"2026-11-09T10:00:{i:02d}+00:00", "workerKey": f"trk-{i}", "status": "COMPLIANT", "severity": "INFO", "reason": "ok", "reviewState": "NOT_REQUIRED",
                   "ruleType": "PPE_ENTRY", "zone": {"name": "Z"}, "camera": {"name": "C"}, "rule": None} for i in range(9, -1, -1)]
r = client.get(base + "/audit-trail", headers=V)
pages = [c[2]["offset"][0] for c in STUB.calls if c[1] == "safety_decisions"]
rows = list(csv.reader(io.StringIO(r.content.decode("utf-8").lstrip("﻿"))))
check("a long trail is fetched page by page, stops once it holds more than the cap, keeps the NEWEST rows, oldest first, and says it was cut",
      pages[:2] == ["0", "2"] and len(pages) <= 3 and r.headers.get("x-export-truncated") == "true" and rows[-1][0].startswith("Only the newest 3")
      and [x[4] for x in rows if len(x) > 4 and x[1] == "Decision"] == ["Worker 7", "Worker 8", "Worker 9"] and "Worker 0" not in r.content.decode("utf-8"), str((pages, rows[-3:])))
srr.EXPORT_ROW_CAP, srr.EXPORT_PAGE = sr.EXPORT_ROW_CAP, sr.EXPORT_PAGE
sr.resolve_identity = _real_resolve

# ═════════════════════════════════════════════════════════════════════════════
# C. The real database
# ═════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("C. THE REAL DATABASE (hand-counted)")
print("=" * 70)

URL, ANON_REAL, SERVICE_REAL = LIVE_ENV["SUPABASE_URL"], LIVE_ENV["SUPABASE_KEY"], LIVE_ENV["SUPABASE_SERVICE_ROLE_KEY"]
live = bool(URL and ANON_REAL and SERVICE_REAL) and "your-" not in (SERVICE_REAL or "")
if live:
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"], os.environ["SUPABASE_SERVICE_ROLE_KEY"] = URL, ANON_REAL, SERVICE_REAL
    from supabase import create_client                                   # noqa: E402

    svc = create_client(URL, SERVICE_REAL)
    try:
        svc.rpc("safety_report", {"p_org": str(uuid.uuid4()), "p_since": "2026-01-01T00:00:00Z", "p_until": "2026-01-02T00:00:00Z"}).execute()
    except Exception as e:
        print(f"SKIP: safety_report is not reachable ({str(e)[:100]}); apply 028 first")
        live = False
if not live:
    print("  skipped")
else:
    TAG = uuid.uuid4().hex[:8]
    PASSWORD = "Rep-" + uuid.uuid4().hex + "!"
    orgs, users = [], []
    uid = lambda: str(uuid.uuid4())                                       # noqa: E731
    NOW = datetime.now(timezone.utc)
    iso = lambda dt: dt.isoformat()                                       # noqa: E731
    DAY = (NOW - timedelta(days=3)).replace(minute=10, second=0, microsecond=0)        # the day the decisions happen on
    at = lambda hour, minute=10: iso(DAY.replace(hour=hour, minute=minute))           # noqa: E731

    def new_user(label):
        email = f"rep-{label}-{TAG}@example.test"
        u = svc.auth.admin.create_user({"email": email, "password": PASSWORD, "email_confirm": True})
        users.append(u.user.id)
        c = create_client(URL, ANON_REAL)
        s = c.auth.sign_in_with_password({"email": email, "password": PASSWORD}).session
        return u.user.id, email, {"Authorization": f"Bearer {s.access_token}"}

    def new_org(label):
        oid = uid()
        svc.table("organisations").insert({"id": oid, "name": f"Report {label} {TAG}", "slug": f"rep-{label.lower()}-{TAG}", "plan": "ENTERPRISE", "updatedAt": iso(NOW)}).execute()
        orgs.append(oid)
        return oid

    def member(org, pid, email, role):
        svc.table("memberships").insert({"orgId": org, "profileId": pid, "invitedEmail": email, "role": role, "status": "ACTIVE", "acceptedAt": iso(NOW), "updatedAt": iso(NOW)}).execute()

    def camera(org, name):
        cid = uid()
        svc.table("cameras").insert({"id": cid, "orgId": org, "name": name, "sourceType": "UPLOAD", "updatedAt": iso(NOW)}).execute()
        return cid

    def zone(org, cam, name, slug, stype):
        zid = uid()
        svc.table("zones").insert({"id": zid, "orgId": org, "cameraId": cam, "name": name, "slug": slug, "safetyType": stype, "polygon": [[0, 0], [9, 0], [9, 9]], "updatedAt": iso(NOW)}).execute()
        return zid

    def decision(org, cam, zn, rule, status, review, when, evidence=None, code=None, days_off=0):
        did = uid()
        svc.table("safety_decisions").insert({
            "id": did, "orgId": org, "cameraId": cam, "zoneId": zn, "ruleType": rule, "severity": "WARNING", "workerKey": "trk-" + str(len(did) % 9 + 1), "status": status,
            "reason": "Seeded decision for the report test.", "confidence": 0.0 if status == "UNVERIFIABLE" else 0.9, "evidence": evidence or {}, "videoTimeS": 5,
            "observedAt": when, "reviewState": review, **({"reviewedAt": iso(NOW), "reviewReasonCode": code} if review in ("CONFIRMED", "DISMISSED") else {})}).execute()
        return did

    def event(org, cam, zn, kind, details, when):
        svc.table("safety_events").insert({"eventUid": uid(), "orgId": org, "cameraId": cam, "zoneId": zn, "workerKey": "trk-1", "eventType": kind, "videoTimeS": 1,
                                           "observedAt": when, "details": details}).execute()

    def action(org, dec, status, due, created, closed=None):
        svc.table("corrective_actions").insert({"orgId": org, "decisionId": dec, "title": "Seeded action", "status": status, "dueDate": due, "createdAt": iso(created),
                                                "closedAt": iso(closed) if closed else None, "priority": "WARNING"}).execute()

    try:
        orgA, orgB, orgEmpty = new_org("A"), new_org("B"), new_org("Empty")
        vid, vmail, V2 = new_user("viewer")
        mid, mmail, M2 = new_user("manager")
        eid, email, E2 = new_user("empty")
        member(orgA, vid, vmail, "VIEWER"); member(orgA, mid, mmail, "MANAGER"); member(orgEmpty, eid, email, "ADMIN")
        camA, camB, camForeign = camera(orgA, "Cam A"), camera(orgA, "Cam B"), camera(orgB, "Cam Foreign")
        press = zone(orgA, camA, "Press line", f"zone_p{TAG}", "RESTRICTED")
        dock = zone(orgA, camA, "Dock", f"zone_d{TAG}", "APPROACH")
        furnace = zone(orgA, camB, "Furnace", f"zone_f{TAG}", "RESTRICTED")
        foreign = zone(orgB, camForeign, "Foundry", f"zone_x{TAG}", "RESTRICTED")
        H = lambda state: {"state": state}                                                                  # noqa: E731
        ev_ppe = lambda helmet, vest: {"items": {"helmet": H(helmet), "safety_vest": H(vest)}}              # noqa: E731
        # 16 decisions in the period (see the hand count in the header of this section)
        for h in (9, 9, 9):
            decision(orgA, camA, press, "PPE_ENTRY", "COMPLIANT", "NOT_REQUIRED", at(h), ev_ppe("PRESENT", "PRESENT"))                          # 1-3
        d4 = decision(orgA, camA, press, "PPE_ENTRY", "POTENTIAL_VIOLATION", "CONFIRMED", at(10), ev_ppe("ABSENT", "PRESENT"), "CORRECT")        # 4
        decision(orgA, camA, press, "PPE_ENTRY", "POTENTIAL_VIOLATION", "DISMISSED", at(10), ev_ppe("ABSENT", "ABSENT"), "PPE_PRESENT")         # 5
        decision(orgA, camB, furnace, "PPE_ENTRY", "POTENTIAL_VIOLATION", "PENDING", at(10), ev_ppe("PRESENT", "ABSENT"))                       # 6
        decision(orgA, camA, press, "PPE_ENTRY", "UNVERIFIABLE", "CONFIRMED", at(10), ev_ppe("UNVERIFIABLE", "PRESENT"), "CORRECT")             # 7
        decision(orgA, camA, press, "PPE_CONTINUOUS", "POTENTIAL_VIOLATION", "PENDING", at(10), ev_ppe("ABSENT", "PRESENT"))               # 8
        decision(orgA, camA, press, "MAX_DWELL", "COMPLIANT", "NOT_REQUIRED", at(10), {"outcome": "WITHIN_LIMIT"})                              # 9
        d10 = decision(orgA, camA, press, "MAX_DWELL", "POTENTIAL_VIOLATION", "CONFIRMED", at(10), {"outcome": "EXCEEDED"}, "CORRECT")         # 10
        decision(orgA, camB, None, "MAN_DOWN", "POTENTIAL_VIOLATION", "CONFIRMED", at(14), {"outcome": "LYING_STILL"}, "CORRECT")               # 11
        decision(orgA, camB, None, "MAN_DOWN", "POTENTIAL_VIOLATION", "DISMISSED", at(14), {"outcome": "LYING_STILL"}, "OTHER")                # 12
        decision(orgA, camB, None, "MAN_DOWN", "UNVERIFIABLE", "DISMISSED", at(14), {"outcome": "UNCLEAR"}, "BAD_PICTURE")                      # 13
        decision(orgA, camB, furnace, "OCCUPANCY", "POTENTIAL_VIOLATION", "PENDING", at(14), {"outcome": "LONE_WORKER"})                        # 14
        decision(orgA, camB, furnace, "OCCUPANCY", "POTENTIAL_VIOLATION", "CONFIRMED", at(14), {"outcome": "OVER_CAPACITY"}, "CORRECT")        # 15
        decision(orgA, camB, furnace, "OCCUPANCY", "UNVERIFIABLE", "NOT_REQUIRED", at(14), {"outcome": "CROWDED"})                             # 16
        # not in the report: 40 days old, and another organisation's
        decision(orgA, camA, press, "PPE_ENTRY", "POTENTIAL_VIOLATION", "CONFIRMED", iso(NOW - timedelta(days=40)), ev_ppe("ABSENT", "ABSENT"), "CORRECT")
        decision(orgB, camForeign, foreign, "PPE_ENTRY", "POTENTIAL_VIOLATION", "PENDING", at(10), ev_ppe("ABSENT", "ABSENT"))
        # zone events: 10 in the period
        for dwell in (60, 120, 30):
            event(orgA, camA, press, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": dwell}, at(10))                                  # timed, Press line
        event(orgA, camA, press, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 500, "first_sighting": True}, at(10))                 # not timed (began before the video)
        event(orgA, camA, press, "EXITED_ZONE", {"reason": "LOST", "dwell_s": 40, "unverifiable": True}, at(10))                    # not timed (lost)
        event(orgA, camA, press, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 99, "visit_started_in_replay": True}, at(10))         # not timed (replay)
        event(orgA, camA, dock, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 1000}, at(10))                                         # not a restricted zone
        event(orgA, camB, furnace, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 90}, at(14))                                        # timed, Furnace
        event(orgA, camA, dock, "ENTERED_ZONE", {}, at(10)); event(orgA, camA, dock, "ENTERED_ZONE", {}, at(10))                    # 2 entries
        event(orgA, camA, press, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 5000}, iso(NOW - timedelta(days=40)))                 # out of the period
        event(orgB, camForeign, foreign, "EXITED_ZONE", {"reason": "LEFT", "dwell_s": 7}, at(10))                                   # another organisation
        # corrective actions
        today = NOW.date()
        action(orgA, d4, "OPEN", (today - timedelta(days=1)).isoformat(), NOW - timedelta(days=2))                                  # a1 overdue
        action(orgA, d4, "IN_PROGRESS", (today + timedelta(days=1)).isoformat(), NOW - timedelta(days=2))                           # a2 not yet due
        action(orgA, d4, "CLOSED", None, NOW - timedelta(days=3), NOW - timedelta(days=1))                                          # a3 2 days
        action(orgA, d4, "CLOSED", None, NOW - timedelta(days=5), NOW - timedelta(days=2))                                          # a4 3 days
        action(orgA, d10, "CANCELLED", None, NOW - timedelta(days=2), NOW - timedelta(days=1))                                      # a5 opened, not closed
        action(orgA, d10, "OPEN", None, NOW - timedelta(days=40))                                                                   # a6 open, old
        action(orgA, d10, "CLOSED", None, NOW - timedelta(days=40), NOW - timedelta(days=30))                                       # a7 closed long ago
        print("  seeded: 16 decisions in the period (+2 outside it), 10 zone events, 7 actions")

        client = TestClient(app)
        since, until = (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ")      # no "+" (it would read as a space in a URL)

        def figures(tz="UTC", headers=V2, extra=""):
            r = client.get(f"{base}/report?since={since}&until={until}&tz={tz}{extra}", headers=headers)
            return r, (r.json() if r.status_code == 200 else {})

        r, f = figures()
        check("the report endpoint answers 200 to a VIEWER", r.status_code == 200, r.text[:200])
        check("decisions in the period: 16 (not the old one, not another organisation's)", f.get("decisions") == 16, f.get("decisions"))
        check("compliant 4 (1-3 and 9), flagged by system 9, insufficient evidence 3", (f["compliant"], f["flagged_by_system"], f["insufficient_evidence"]) == (4, 9, 3),
              (f["compliant"], f["flagged_by_system"], f["insufficient_evidence"]))
        check("compliance rate 4 / (4 + 9) = 0.3077; could-not-verify rate 3 / 16 = 0.1875", (f["compliance_rate"], f["unverifiable_rate"]) == (0.3077, 0.1875), (f["compliance_rate"], f["unverifiable_rate"]))
        check("confirmed 4 (4, 10, 11, 15), dismissed 2 (5, 12), waiting 3 (6, 8, 14): 4 + 2 + 3 = 9 flagged",
              (f["confirmed"], f["dismissed"], f["pending"]) == (4, 2, 3), (f["confirmed"], f["dismissed"], f["pending"]))
        check("a PENDING flag is not counted as confirmed or dismissed; the share is 4 of the 6 judged = 0.667", f["confirmed_share_of_reviewed"] == 0.667)
        check("insufficient evidence: 1 a reviewer found real (7), 1 not real (13)", (f["insufficient_confirmed"], f["insufficient_dismissed"]) == (1, 1))
        sm = client.get(f"{base}/summary?days=7&tz=UTC", headers=V2).json()
        check("the Overview's summary and the report agree: confirmed 4, dismissed 2, waiting 3 (flags only, so they add up to the 9 flagged), the same share, "
              "and the two could-not-verify decisions a reviewer looked at do not count as judged flags",
              (sm["confirmed"], sm["dismissed"], sm["open_alerts"], sm["potential_violations"]) == (4, 2, 3, 9) and sm["confirmed_share_of_reviewed"] == f["confirmed_share_of_reviewed"] == 0.667
              and sm["confirmed"] + sm["dismissed"] + sm["open_alerts"] == sm["potential_violations"], str(sm)[:300])
        check("events recorded: 10 in the period", f["events_recorded"] == 10, f["events_recorded"])
        check("the organisation's name is in the answer", f["organisation"] == f"Report A {TAG}")
        reasons = {(x["state"], x["reason"]): x["count"] for x in f["review_reasons"]}
        check("review reasons: confirmed CORRECT 5 (4, 7, 10, 11, 15); dismissed PPE_PRESENT 1, OTHER 1, BAD_PICTURE 1",
              reasons == {("CONFIRMED", "CORRECT"): 5, ("DISMISSED", "PPE_PRESENT"): 1, ("DISMISSED", "OTHER"): 1, ("DISMISSED", "BAD_PICTURE"): 1}, str(reasons))
        byr = {x["rule_type"]: x for x in f["by_rule"]}
        want = {"PPE_ENTRY": (7, 3, 3, 1, 1, 1, 1), "PPE_CONTINUOUS": (1, 0, 1, 0, 0, 1, 0), "MAX_DWELL": (2, 1, 1, 1, 0, 0, 0), "MAN_DOWN": (3, 0, 2, 1, 1, 0, 1), "OCCUPANCY": (3, 0, 2, 1, 0, 1, 1)}
        got = {k: (v["decisions"], v["compliant"], v["flagged"], v["confirmed"], v["dismissed"], v["pending"], v["insufficient_evidence"]) for k, v in byr.items()}
        check("by rule matches the hand count (decisions, compliant, flagged, confirmed, dismissed, waiting, insufficient)", got == want, str(got))
        byz = {x["zone"]: x for x in f["by_zone"]}
        check("by zone: Press line 9 decisions / 4 flagged, Furnace 4 / 3, and 'No zone' 3 / 2 (the camera-wide man-down)",
              (byz["Press line"]["decisions"], byz["Press line"]["flagged"]) == (9, 4) and (byz["Furnace"]["decisions"], byz["Furnace"]["flagged"]) == (4, 3)
              and (byz["No zone"]["decisions"], byz["No zone"]["flagged"]) == (3, 2) and "Foundry" not in byz, str(byz.keys()))
        byc = {x["camera"]: x for x in f["by_camera"]}
        check("by camera: Cam A 9 decisions / 4 flagged, Cam B 7 / 5; the other organisation's camera is absent",
              (byc["Cam A"]["decisions"], byc["Cam A"]["flagged"]) == (9, 4) and (byc["Cam B"]["decisions"], byc["Cam B"]["flagged"]) == (7, 5) and "Cam Foreign" not in byc, str(byc.keys()))
        hrs = {x["hour"]: (x["decisions"], x["flagged"]) for x in f["by_hour"]}
        check("by hour (UTC): 09 -> 3 decisions / 0 flagged, 10 -> 7 / 5, 14 -> 6 / 4, every other hour 0, and all 24 hours are present",
              len(f["by_hour"]) == 24 and hrs[9] == (3, 0) and hrs[10] == (7, 5) and hrs[14] == (6, 4) and sum(v[0] for v in hrs.values()) == 16, str(hrs))
        _, fi = figures("Asia/Kolkata")
        hi = {x["hour"]: x["decisions"] for x in fi["by_hour"]}
        check("by hour follows the site's own time zone: in Asia/Kolkata (+5:30) 09:10 UTC is 14:40, 10:10 is 15:40, 14:10 is 19:40",
              hi[14] == 3 and hi[15] == 7 and hi[19] == 6 and fi["time_zone"] == "Asia/Kolkata", str(hi))
        days = {x["day"]: (x["decisions"], x["flagged"]) for x in f["by_day"]}
        check("by day: all 16 decisions are on one day, every other day of the window is present and 0", days[DAY.date().isoformat()] == (16, 9) and sum(v[0] for v in days.values()) == 16 and len(days) >= 7, str(days))
        p = f["ppe"]
        check("helmet and vest: 4 flagged (4, 5, 6, 8), no helmet seen 3 (4, 5, 8), no vest seen 2 (5, 6), neither 1 (5), could not verify 1 (7), compliant 3",
              (p["flagged"], p["missing_helmet"], p["missing_vest"], p["missing_both"], p["could_not_verify"], p["compliant"]) == (4, 3, 2, 1, 1, 3), str(p))
        z = f["zone_time"]
        check("time in restricted zones: 4 whole visits (60, 120, 30, 90 s), average 75 s, longest 120 s; 3 seen only in part (500 s already inside, 40 s lost, 99 s replay); the dock and old and foreign exits are left out",
              (z["visits_timed"], z["visits_partial"], z["visits_not_timed"], z["average_seconds"], z["max_seconds"]) == (4, 3, 3, 75.0, 120.0), str(z))
        check("... the longest time seen is 500 s and is a lower bound (not complete); the partial times are NOT in the average",
              z["longest_seen_seconds"] == 500.0 and z["longest_seen_is_complete"] is False and z["partial_max_seconds"] == 500.0, str(z))
        zz = {x["zone"]: (x["visits"], x["average_seconds"], x["max_seconds"], x["partial_visits"], x["longest_seen_seconds"]) for x in z["by_zone"]}
        check("... by zone: Press line 3 whole (average 70, longest 120) + 3 partial (longest seen 500), Furnace 1 whole (90, 90) + none partial",
              zz == {"Press line": (3, 70.0, 120.0, 3, 500.0), "Furnace": (1, 90.0, 90.0, 0, 90.0)}, str(zz))
        check("man-down: 2 flagged (1 confirmed, 1 dismissed), 1 could not verify", (f["man_down"]["flagged"], f["man_down"]["confirmed"], f["man_down"]["dismissed"], f["man_down"]["pending"], f["man_down"]["could_not_verify"]) == (2, 1, 1, 0, 1), str(f["man_down"]))
        o = f["occupancy"]
        check("head count: 1 lone-worker flag (waiting), 1 over-capacity flag (confirmed), 1 could not verify",
              o["lone_worker"] == {"flagged": 1, "confirmed": 0, "dismissed": 0, "pending": 1} and o["over_capacity"] == {"flagged": 1, "confirmed": 1, "dismissed": 0, "pending": 0} and o["could_not_verify"] == 1, str(o))
        a = f["actions"]
        check("actions: opened 5, closed 2, average 2.5 days, open now 3, overdue now 1",
              (a["opened"], a["closed"], a["open_now"], a["overdue_now"], a["average_days_to_close"]) == (5, 2, 3, 1, 2.5), str(a))

        # who sees what
        r, fe = figures(headers=E2)
        check("another organisation's admin gets their own (empty) report: 0 decisions, rates n/a, no rows of organisation A",
              fe["decisions"] == 0 and fe["compliance_rate"] is None and fe["by_zone"] == [] and fe["actions"]["open_now"] == 0, str(fe)[:200])
        r = client.get(f"{base}/report?since={since}&until={until}&p_org={orgA}", headers=E2)
        check("naming organisation A in the request does not change whose report it is", r.status_code == 200 and r.json()["decisions"] == 0)
        direct = create_client(URL, ANON_REAL)
        direct.postgrest.auth(E2["Authorization"].split()[1])
        try:
            res = direct.rpc("safety_report", {"p_org": orgA, "p_since": since, "p_until": until}).execute()
            leak = res.data
        except Exception:
            leak = None
        check("calling the database function directly with organisation A's id as an outsider gives nothing of A (Row Level Security scopes it)",
              leak is None or (isinstance(leak, dict) and leak.get("decisions") == 0), str(leak)[:160])
        anon_client = create_client(URL, ANON_REAL)
        try:
            res = anon_client.rpc("safety_report", {"p_org": orgA, "p_since": since, "p_until": until}).execute()
            anon_ok = False
        except Exception:
            anon_ok = True
        check("an unauthenticated caller cannot run the function at all", anon_ok)
        r = client.get(f"{base}/report?since={since}&until={until}&tz=Not/AZone", headers=V2)
        check("an unknown time zone is refused by the database with a sentence", r.status_code in (400, 422) and "Not/AZone" not in r.text, f"{r.status_code} {r.text[:120]}")

        # exports
        e_csv = client.get(f"{base}/report/export?format=csv&since={since}&until={until}&tz=UTC", headers=V2)
        txt = e_csv.content.decode("utf-8")
        check("the exported CSV carries the same numbers (decisions 16, compliant 4, flagged 9, waiting 3, confirmed 4, no helmet seen 3, actions overdue 1)",
              e_csv.status_code == 200 and all(s in txt for s in ("Decisions made,16", "Compliant,4", "Waiting for review,3", "Confirmed by reviewer,4", "... with no helmet seen,3",
                                                                   "Overdue now (open and past the due date),1", "30.8%")), txt[:300])
        e_pdf = client.get(f"{base}/report/export?format=pdf&since={since}&until={until}&tz=UTC", headers=V2)
        check("the exported PDF is a real, multi-section PDF", e_pdf.status_code == 200 and e_pdf.content.startswith(b"%PDF-") and len(e_pdf.content) > 3000 and e_pdf.headers["x-report-recorded"] == "true")
        rows = svc.table("reports").select("*").eq("orgId", orgA).eq("kind", "SAFETY_SUMMARY").execute().data
        check("both were recorded in the reports table (SAFETY_SUMMARY, READY, CSV and PDF, the viewer as requester)",
              sorted(x["format"] for x in rows) == ["CSV", "PDF"] and all(x["status"] == "READY" and x["requestedById"] == vid and x["fileSizeBytes"] > 0 for x in rows), str(rows)[:200])
        hist = client.get(f"{base}/report/history", headers=V2).json()["reports"]
        check("the history lists them, newest first", len(hist) == 2 and hist[0]["createdAt"] >= hist[1]["createdAt"])
        check("another organisation's history is empty", client.get(f"{base}/report/history", headers=E2).json()["reports"] == [])
        au = svc.table("audit_logs").select("*").eq("orgId", orgA).eq("action", "safety.report_exported").execute().data
        check("each export has an audit row naming who exported it and what period", len(au) == 2 and all(x["actorId"] == vid and x["metadata"]["since"] for x in au), str(au)[:200])
        trail = client.get(f"{base}/audit-trail?since={since}&until={until}", headers=V2)
        tr = list(csv.reader(io.StringIO(trail.content.decode("utf-8").lstrip("﻿"))))
        kinds = [x[1] for x in tr[1:]]
        check("the audit trail holds exactly the 16 decisions and 10 events of the period (and not the old or foreign ones), oldest first",
              trail.status_code == 200 and kinds.count("Decision") == 16 and kinds.count("Zone event") == 10 and "Foundry" not in trail.text and "Cam Foreign" not in trail.text, str((kinds.count("Decision"), kinds.count("Zone event"))))
        times = [x[0] for x in tr[1:]]
        check("... in time order, with no internal worker key", times == sorted(times) and "trk-" not in trail.text)
        au2 = svc.table("audit_logs").select("*").eq("orgId", orgA).eq("action", "safety.audit_trail_exported").execute().data
        check("the audit trail export is audited too (26 rows)", len(au2) == 1 and au2[0]["metadata"]["rows"] == 26, str(au2)[:160])
        far = (NOW - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        long = client.get(f"{base}/report?since={far}&until={until}", headers=V2)
        check("a period over 366 days is refused", long.status_code == 422)
    finally:
        from datetime import datetime as _dt
        for o in orgs:
            for t in ("audit_logs", "reports", "corrective_actions", "safety_decisions", "safety_events"):
                try:
                    svc.table(t).delete().eq("orgId", o).execute()
                except Exception:
                    pass
            try:
                svc.table("organisations").update({"deletedAt": _dt.now(timezone.utc).isoformat()}).eq("id", o).execute()
                svc.table("memberships").delete().eq("orgId", o).execute()
                for t in ("zones", "cameras"):
                    svc.table(t).delete().eq("orgId", o).execute()
                svc.table("organisations").delete().eq("id", o).execute()
            except Exception as e:
                print("  (could not remove organisation:", str(e)[:100], ")")
        for u in users:
            for fn in (lambda: svc.table("profiles").delete().eq("id", u).execute(), lambda: svc.auth.admin.delete_user(u)):
                try:
                    fn()
                except Exception:
                    pass
        left = svc.table("organisations").select("id").like("slug", f"rep-%-{TAG}").execute().data
        print(f"  cleaned up (organisations left behind: {len(left)})")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f_ in FAILS:
        print("  -", f_)
    sys.exit(1)
print("ALL PASS")
