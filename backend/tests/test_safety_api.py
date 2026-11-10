"""
The safety API (Step 11) against a stub PostgREST: who may call what, that every query runs AS THE CALLER
(their token, the public key, an explicit organisation filter), that nothing the caller sends can change
the shape of a query, how database errors are shown, and the audit rows. The real database is covered by
test_safety_api_live.py.

    venv/Scripts/python.exe backend/tests/test_safety_api.py
"""
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient                    # noqa: E402

import app.api.routers.safety as sr                           # noqa: E402
from app.main import app                                      # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


ORG, OTHER = str(uuid.uuid4()), str(uuid.uuid4())
USER_M, USER_V = str(uuid.uuid4()), str(uuid.uuid4())
Z_RESTRICTED, Z_GENERAL, CAM = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
RULE, DEC = str(uuid.uuid4()), str(uuid.uuid4())
ANON_KEY, SERVICE_KEY = "anon-key-for-the-stub", "service-key-for-the-stub"


class Stub:
    def reset(self):
        self.calls = []                  # (method, path, params, apikey, authorization, body)
        self.answer = {}                 # path -> (status, body, extra headers) overriding the default
        self.total = None
        self.big = None                  # a long list of decisions served page by page

    def __init__(self):
        self.reset()


STUB = Stub()


def default_rows(table):
    if table == "zones":
        return [{"id": Z_RESTRICTED, "name": "Press line", "slug": "zone_press", "safetyType": "RESTRICTED", "cameraId": CAM},
                {"id": Z_GENERAL, "name": "Lawn", "slug": "zone_lawn", "safetyType": None, "cameraId": CAM}]
    if table == "safety_rules":
        return [{"id": RULE, "name": "Hat", "type": "PPE_ENTRY", "zoneId": Z_RESTRICTED, "cameraId": CAM, "params": {"required_ppe": ["helmet"], "mode": "AT_ENTRY"},
                 "severity": "WARNING", "isEnabled": True, "minEvidenceConfidence": 0.6, "createdAt": "2026-11-09T00:00:00Z", "updatedAt": "2026-11-09T00:00:00Z",
                 "orgId": ORG}]
    if table == "safety_decisions":
        return [{"id": DEC, "status": "POTENTIAL_VIOLATION", "snapshotRef": f"{ORG}/{DEC}.jpg", "workerKey": "trk-3", "reason": "=HYPERLINK(\"x\")",
                 "severity": "WARNING", "confidence": 0.97, "observedAt": "2026-11-09T04:00:00Z", "reviewState": "PENDING",
                 "zone": {"name": "Press line"}, "camera": {"name": "floor5"}, "rule": None, "reviewedAt": None, "reviewReasonCode": None, "reviewNote": "+1 call me"}]
    if table == "safety_events":
        return [{"id": str(uuid.uuid4()), "eventType": "ENTERED_ZONE", "workerKey": "trk-3"}]
    return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self, method):
        u = urlparse(self.path)
        params = {k: v for k, v in parse_qs(u.query).items()}
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        path = u.path.replace("/rest/v1/", "", 1)
        STUB.calls.append((method, path, params, self.headers.get("apikey"), self.headers.get("Authorization"), body))
        status, payload, headers = STUB.answer.get(path, (None, None, {}))
        if status is None:
            if path.startswith("rpc/"):
                status, payload = 200, ({"id": DEC, "status": "POTENTIAL_VIOLATION", "reviewState": body.get("p_state"), "snapshotRef": None}
                                        if path.endswith("review_safety_decision") else ({"total": 3} if path.endswith("safety_summary") else None))
            elif method == "POST" and path == "safety_rules":
                status, payload = 201, [dict(body, id=RULE)]
            elif method == "POST":
                status, payload = 201, None
            elif method == "PATCH":
                status, payload = 200, [dict(default_rows(path)[0], **(body or {}))]
            else:
                status, payload = 200, default_rows(path)
                if path == "safety_decisions":
                    # page the list like the real thing: a page past the end is EMPTY (the export relies on that)
                    source = STUB.big if getattr(STUB, "big", None) is not None else default_rows(path)
                    off, lim = int(params.get("offset", ["0"])[0]), int(params.get("limit", ["1000"])[0])
                    payload = source[off:off + lim]
        raw = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if STUB.total is not None and method == "GET":
            self.send_header("Content-Range", f"0-{max(0, len(payload or []) - 1)}/{STUB.total}")
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def do_PATCH(self):
        self._go("PATCH")


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{server.server_port}"
os.environ["SUPABASE_KEY"] = ANON_KEY
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = SERVICE_KEY

IDENT = {"Bearer manager-token": (ORG, USER_M, "MANAGER"), "Bearer admin-token": (ORG, USER_M, "ADMIN"),
         "Bearer viewer-token": (ORG, USER_V, "VIEWER"), "Bearer norole-token": (ORG, USER_V, None)}
sr.resolve_identity = lambda token: IDENT.get(token, (None, None, None))
client = TestClient(app)
base = "/api/v1/safety"
M, V = {"Authorization": "Bearer manager-token"}, {"Authorization": "Bearer viewer-token"}


def sent():
    return [c for c in STUB.calls if c[3] == ANON_KEY]


def audits():
    return [c for c in STUB.calls if c[1] == "audit_logs"]


RULE_BODY = {"name": "Hat on entry", "type": "PPE_ENTRY", "zone_id": Z_RESTRICTED, "params": {"required_ppe": ["helmet", "safety_vest"]}, "severity": "CRITICAL"}

print("=" * 66)
print("SAFETY API (stub PostgREST)")
print("=" * 66)

# ── A. Who may call what ────────────────────────────────────────────────
print()
print("A. 401 and 403")
endpoints = [("get", "/rules", None), ("post", "/rules", RULE_BODY), ("patch", f"/rules/{RULE}", {"name": "x"}), ("delete", f"/rules/{RULE}", None),
             ("post", "/rules/validate", RULE_BODY), ("get", "/events", None), ("get", "/decisions", None), ("get", f"/decisions/{DEC}", None),
             ("post", f"/decisions/{DEC}/review", {"state": "CONFIRMED"}), ("get", f"/decisions/{DEC}/snapshot", None), ("get", "/summary", None), ("get", "/export", None)]
needs = {"/rules": {"get": "view", "post": "edit"}}


def call(who_headers, method, path, body):
    return getattr(client, method)(base + path, headers=who_headers, **({"json": body} if body is not None else {}))


STUB.reset()
for method, path, body in endpoints:
    r = call({}, method, path, body)
    check(f"{method.upper()} {path.split('/')[1] if path.count('/') == 1 else path.rsplit('/', 1)[0][:20] + '...'}: no token is 401", r.status_code == 401, str(r.status_code))
    r = call({"Authorization": "Bearer garbage"}, method, path, body)
    check(f"{method.upper()} {path[:28]}: an unverifiable token is 401", r.status_code == 401)
check("no database call was made for any of those", not STUB.calls, str(len(STUB.calls)))

writes = [("post", "/rules", RULE_BODY), ("patch", f"/rules/{RULE}", {"name": "x"}), ("delete", f"/rules/{RULE}", None), ("post", "/rules/validate", RULE_BODY),
          ("post", f"/decisions/{DEC}/review", {"state": "CONFIRMED"}), ("get", f"/decisions/{DEC}/snapshot", None)]
for method, path, body in writes:
    r = call(V, method, path, body)
    check(f"a VIEWER on {method.upper()} {path[:30]}: 403 with a sentence", r.status_code == 403 and "administrator or manager" in r.json()["detail"], f"{r.status_code} {r.text[:80]}")
r = call({"Authorization": "Bearer norole-token"}, "get", "/rules", None)
check("a member with no active role is refused even a read", r.status_code == 403)
check("...and none of the VIEWER's refused calls reached the database", not [c for c in STUB.calls if c[1] != "audit_logs"], str(STUB.calls[:2]))
STUB.reset()
for method, path, body in (("get", "/rules", None), ("get", "/events", None), ("get", "/decisions", None), ("get", f"/decisions/{DEC}", None), ("get", "/summary", None), ("get", "/export", None)):
    r = call(V, method, path, body)
    check(f"a VIEWER may read: GET {path[:20]}", r.status_code == 200, f"{r.status_code} {r.text[:80]}")

# ── B. Runs as the caller ───────────────────────────────────────────────
print()
print("B. Queries run as the caller")
STUB.reset()
STUB.reset()
client.get(base + "/decisions?sort=oldest", headers=M)
check("the review queue asks for the oldest first (time ascending, id as the tiebreaker)", sent()[0][2]["order"] == ["observedAt.asc,id.asc"], str(sent()[0][2]["order"]))
check("an unknown sort is refused", client.get(base + "/decisions?sort=random", headers=M).status_code == 422)
STUB.reset()
r = client.get(base + "/decisions?status=POTENTIAL_VIOLATION&review_state=PENDING&org_id=" + OTHER + "&orgId=" + OTHER, headers=M)
c = sent()[0]
check("the caller's own token and the PUBLIC key are used, never the service key", c[4] == "Bearer manager-token" and c[3] == ANON_KEY and SERVICE_KEY not in json.dumps(STUB.calls))
check("the organisation filter is the verified one; an org_id the caller sends is ignored", c[2]["orgId"] == [f"eq.{ORG}"] and OTHER not in json.dumps(c[2]), str(c[2]))
check("filters become exact-match conditions", c[2]["status"] == ["eq.POTENTIAL_VIOLATION"] and c[2]["reviewState"] == ["eq.PENDING"])
check("the response drops the stored picture path and says whether a picture exists",
      r.json()["decisions"][0]["has_picture"] is True and "snapshotRef" not in r.json()["decisions"][0])

# ── C. Nothing the caller sends changes a query's shape ────────────────
print()
print("C. Input validation")
STUB.reset()
bad = ["status=NOPE", "review_state=x", "rule_type=NOPE", "severity=LOUD", "zone_id=not-a-uuid", "camera_id=1", "worker_key=trk-1%26orgId%3Deq.x",
       "worker_key=trk-1,or=(a.eq.b)", "since=garbage", "until=1999-01-01", "since=2026-11-09&until=2026-11-01", "limit=0", "limit=201", "offset=-1",
       "status=COMPLIANT%26orgId%3Deq.x", "zone_id=" + ORG + "%29%29", "since=2026-11-09T00:00:00Z%29%2C%28x"]
for q in bad:
    r = client.get(base + "/decisions?" + q, headers=M)
    check(f"/decisions?{q[:44]}: 422, no query sent", r.status_code == 422 and not STUB.calls, f"{r.status_code} calls={len(STUB.calls)}")
r = client.get(base + "/events?event_type=NOPE", headers=M)
check("/events: an unknown event type is 422", r.status_code == 422)
r = client.get(base + f"/decisions/{'1' * 5}", headers=M)
check("a malformed decision id is 422", r.status_code == 422)
STUB.reset()
r = client.get(base + f"/events?worker_key=trk-12&zone_id={Z_RESTRICTED}&since=2026-11-01T00:00:00Z&until=2026-11-09T00:00:00%2B05:30&limit=10&offset=20", headers=M)
c = sent()[0]
check("good filters reach the query as separate, exact conditions", r.status_code == 200 and c[2]["workerKey"] == ["eq.trk-12"] and c[2]["zoneId"] == [f"eq.{Z_RESTRICTED}"]
      and c[2]["limit"] == ["10"] and c[2]["offset"] == ["20"] and len(c[2]["and"]) == 2, str(c[2]))
check("the time window is sent as UTC-aware ISO values", all("+00:00" in x or "+05:30" in x for x in c[2]["and"]), str(c[2]["and"]))

STUB.reset(); STUB.total = 120
r = client.get(base + "/decisions?limit=50&offset=50", headers=M)
check("paging reports the total and the next offset", r.json()["total"] == 120 and r.json()["next_offset"] == 100 and r.json()["limit"] == 50)
r = client.get(base + "/decisions?limit=50&offset=100", headers=M)
check("...and no next offset on the last page", r.json()["next_offset"] is None)
STUB.total = None

# ── D. Rules ────────────────────────────────────────────────────────────
print()
print("D. Rules")
STUB.reset()
r = client.get(base + "/rules", headers=V)
check("rules are listed with whether the engine evaluates them today", r.status_code == 200 and r.json()["rules"][0]["evaluated_by_engine"] is True)
STUB.reset()
r = client.post(base + "/rules/validate", headers=M, json=RULE_BODY)
check("a good rule validates, with its parameters filled in", r.json()["valid"] is True and r.json()["params"]["required_ppe"] == ["helmet", "safety_vest"] and r.json()["params"]["mode"] == "AT_ENTRY" and r.json()["evaluated_by_engine"] is True, r.text[:140])
r = client.post(base + "/rules/validate", headers=M, json={**RULE_BODY, "zone_id": Z_GENERAL})
check("PPE_ENTRY on a General zone is reported, not saved", r.status_code == 200 and r.json()["valid"] is False and "RESTRICTED" in r.json()["errors"][0], r.text[:160])
r = client.post(base + "/rules/validate", headers=M, json={**RULE_BODY, "type": "TELEPORT"})
check("an unknown type is reported", r.json()["valid"] is False)
r = client.post(base + "/rules/validate", headers=M, json={**RULE_BODY, "zone_id": str(uuid.uuid4())})
check("a zone that is not the organisation's is reported", r.json()["valid"] is False and "zone" in r.json()["errors"][0].lower(), r.text[:140])
r = client.post(base + "/rules/validate", headers=M, json={**RULE_BODY, "params": {"required_ppe": ["gloves"]}})
check("a bad parameter is reported with the reason", r.json()["valid"] is False and ("required_ppe" in r.json()["errors"][0] or "gloves" in r.json()["errors"][0]), r.text[:140])
r = client.post(base + "/rules/validate", headers=M, json={"name": "Lone", "type": "MAN_DOWN", "params": {}})
check("a camera-wide MAN_DOWN needs no zone", r.json()["valid"] is True)
r = client.post(base + "/rules/validate", headers=M, json={"name": "Crowd", "type": "OCCUPANCY", "zone_id": Z_RESTRICTED, "params": {"max_people": 4}})
check("a rule type the engine does not evaluate yet validates and says so", r.json()["valid"] is True and r.json()["evaluated_by_engine"] is False)
r = client.post(base + "/rules/validate", headers=M, json={**RULE_BODY, "name": ""})
check("a blank name is a 422 from the request model", r.status_code == 422)

STUB.reset()
r = client.post(base + "/rules", headers=M, json=RULE_BODY)
stored = next(c for c in STUB.calls if c[0] == "POST" and c[1] == "safety_rules")
check("creating a rule: 201, run as the caller, orgId set from the token", r.status_code == 201 and stored[4] == "Bearer manager-token" and stored[3] == ANON_KEY and stored[5]["orgId"] == ORG, r.text[:120])
check("...with the validated, filled-in parameters and the severity", stored[5]["params"]["mode"] == "AT_ENTRY" and stored[5]["severity"] == "CRITICAL" and stored[5]["zoneId"] == Z_RESTRICTED)
a = audits()
check("...and an audit row written with the SERVICE key naming the actor", len(a) == 1 and a[0][3] == SERVICE_KEY and a[0][5][0]["action"] == "safety.rule_created"
      and a[0][5][0]["actorId"] == USER_M and a[0][5][0]["orgId"] == ORG and a[0][5][0]["targetId"] == RULE, str(a[:1]))
STUB.reset()
r = client.post(base + "/rules", headers=M, json={**RULE_BODY, "zone_id": Z_GENERAL})
check("an invalid rule is refused before anything is stored", r.status_code == 422 and not [c for c in STUB.calls if c[0] == "POST"])
STUB.reset(); STUB.answer["safety_rules"] = (403, {"code": "42501", "message": "new row violates row-level security policy for table \"safety_rules\""}, {})
r = client.post(base + "/rules", headers=M, json=RULE_BODY)
check("a database permission refusal is a 403 in plain words, not the database's text", r.status_code == 403 and "row-level" not in r.text and "permission" in r.json()["detail"], r.text[:120])
STUB.reset(); STUB.answer["safety_rules"] = (400, {"code": "23514", "message": "the rule's zone is not in this organisation"}, {})
r = client.post(base + "/rules", headers=M, json=RULE_BODY)
check("a constraint the database enforces is a 422 with its own sentence", r.status_code == 422 and "zone" in r.json()["detail"], r.text[:120])
STUB.reset(); STUB.answer["safety_rules"] = (500, {"code": "XX000", "message": "internal secret detail table pg_catalog"}, {})
r = client.post(base + "/rules", headers=M, json=RULE_BODY)
check("an unexpected database failure is a 502 that shows none of the database's text", r.status_code == 502 and "secret" not in r.text and "pg_catalog" not in r.text, r.text[:120])

STUB.reset()
r = client.patch(base + f"/rules/{RULE}", headers=M, json={"severity": "INFO", "is_enabled": False})
patch = next(c for c in STUB.calls if c[0] == "PATCH")
check("changing a rule sends only the editable columns, filtered to this rule and organisation",
      r.status_code == 200 and set(patch[5]) == {"cameraId", "zoneId", "name", "params", "severity", "isEnabled", "minEvidenceConfidence"}
      and patch[2]["id"] == [f"eq.{RULE}"] and patch[2]["orgId"] == [f"eq.{ORG}"] and patch[5]["severity"] == "INFO" and patch[5]["isEnabled"] is False, str(patch[5]))
check("...merged with the stored rule, so unchanged fields stay", patch[5]["name"] == "Hat" and patch[5]["params"]["required_ppe"] == ["helmet"])
check("...audited as an update listing the fields", [c[5][0]["action"] for c in audits()] == ["safety.rule_updated"] and audits()[0][5][0]["metadata"]["fields"] == ["is_enabled", "severity"])
STUB.reset(); STUB.answer["safety_rules"] = (200, [], {})
r = client.patch(base + f"/rules/{RULE}", headers=M, json={"name": "x"})
check("changing a rule that is not visible to the caller is 404", r.status_code == 404)
r = client.patch(base + "/rules/not-an-id", headers=M, json={"name": "x"})
check("a malformed rule id is 422", r.status_code == 422)

STUB.reset()
r = client.delete(base + f"/rules/{RULE}", headers=M)
c = next(c for c in STUB.calls if c[1] == "rpc/soft_delete_safety_rule")
check("deleting calls the audited database function as the caller", r.status_code == 204 and c[5] == {"p_rule_id": RULE} and c[4] == "Bearer manager-token")
check("...and writes no audit row of its own (the function does)", not audits())
STUB.reset(); STUB.answer["rpc/soft_delete_safety_rule"] = (404, {"code": "P0002", "message": "rule not found"}, {})
check("a missing rule is 404", client.delete(base + f"/rules/{RULE}", headers=M).status_code == 404)
STUB.reset(); STUB.answer["rpc/soft_delete_safety_rule"] = (403, {"code": "42501", "message": "not permitted"}, {})
check("a rule the database says is not yours is 403", client.delete(base + f"/rules/{RULE}", headers=M).status_code == 403)

STUB.reset()
r = client.post(base + "/rules", headers=M, json={**RULE_BODY, "isEnabled": False})
check("a camelCase or unknown field is an error, not a silent no-op (it used to create the rule enabled)", r.status_code == 422 and not [c for c in STUB.calls if c[0] == "POST"], r.text[:120])
r = client.patch(base + f"/rules/{RULE}", headers=M, json={"zoneId": Z_RESTRICTED})
check("...on PATCH too", r.status_code == 422)
for field in ("is_enabled", "min_evidence_confidence", "params", "severity", "name"):
    STUB.reset()
    r = client.patch(base + f"/rules/{RULE}", headers=M, json={field: None})
    check(f"PATCH with {field}: null is refused (it used to reset or re-enable)", r.status_code == 422 and not [c for c in STUB.calls if c[0] == "PATCH"], r.text[:100])
STUB.reset()
r = client.post(base + f"/decisions/{DEC}/review", headers=M, json={"state": "CONFIRMED", "extra": 1})
check("an unknown field on a review is refused", r.status_code == 422)

# ── E. Review ───────────────────────────────────────────────────────────
print()
print("E. Review")
STUB.reset()
r = client.post(base + f"/decisions/{DEC}/review", headers=M, json={"state": "CONFIRMED", "reason": "CORRECT", "note": "seen on the replay"})
c = next(c for c in STUB.calls if c[1] == "rpc/review_safety_decision")
check("a review calls the database function as the caller with exactly what was asked",
      r.status_code == 200 and c[5] == {"p_decision_id": DEC, "p_state": "CONFIRMED", "p_reason": "CORRECT", "p_note": "seen on the replay"} and c[4] == "Bearer manager-token", str(c[5]))
check("...and returns the decision without its stored picture path", r.json()["reviewState"] == "CONFIRMED" and "snapshotRef" not in r.json())
STUB.reset()
for payload, label in (({"state": "MAYBE"}, "an unknown state"), ({"state": "PENDING"}, "a state a person cannot set"), ({"state": "CONFIRMED", "reason": "because"}, "a reason outside the list"),
                       ({"state": "CONFIRMED", "note": "x" * 501}, "a note over 500 characters"), ({}, "no state")):
    r = client.post(base + f"/decisions/{DEC}/review", headers=M, json=payload)
    check(f"{label} is 422 and reaches no database", r.status_code == 422 and not STUB.calls, f"{r.status_code}")
STUB.reset(); STUB.answer["rpc/review_safety_decision"] = (400, {"code": "P0001", "message": "compliant decisions are not reviewed"}, {})
r = client.post(base + f"/decisions/{DEC}/review", headers=M, json={"state": "CONFIRMED"})
check("the database's own refusal reason is passed on (422)", r.status_code == 422 and "compliant decisions are not reviewed" in r.json()["detail"])
STUB.reset(); STUB.answer["rpc/review_safety_decision"] = (403, {"code": "42501", "message": "not permitted"}, {})
check("another organisation's decision is 403 from the database's answer", client.post(base + f"/decisions/{DEC}/review", headers=M, json={"state": "DISMISSED"}).status_code == 403)

# ── F. Summary and export ───────────────────────────────────────────────
print()
print("F. Summary and export")
STUB.reset()
r = client.get(base + "/summary?days=30&p_org=" + OTHER, headers=V)
c = next(c for c in STUB.calls if c[1] == "rpc/safety_summary")
check("the summary asks for the caller's own organisation whatever the request says", r.status_code == 200 and c[5]["p_org"] == ORG and OTHER not in json.dumps(c[5]))
check("...over the requested number of days", 29.9 < (__import__("datetime").datetime.fromisoformat(c[5]["p_until"]) - __import__("datetime").datetime.fromisoformat(c[5]["p_since"])).total_seconds() / 86400 < 30.1)
check("a window over a year is refused", client.get(base + "/summary?days=400", headers=V).status_code == 422)

STUB.reset()
r = client.get(base + "/export?status=POTENTIAL_VIOLATION", headers=V)
check("an export is CSV, never cached, with a file name", r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and r.headers["cache-control"] == "no-store"
      and "attachment" in r.headers["content-disposition"], str(r.headers))
body = r.content.decode("utf-8")
check("it starts with a byte-order mark and a readable header row", body.startswith("﻿Time (UTC),Camera,Zone,Rule,Worker,Result"), body[:60])
check("a worker reads 'Worker 3', not an internal key; camera and zone names are shown", "Worker 3" in body and "trk-3" not in body and "floor5" in body and "Press line" in body)
check("a reason or note that starts like a spreadsheet formula is neutralised ('=HYPERLINK, '+1 call me)", "'=HYPERLINK" in body and "'+1 call me" in body and ",=HYPERLINK" not in body, body[-200:])
a = audits()
check("the export is audited before it is returned, with the row count and window", len(a) == 1 and a[0][5][0]["action"] == "safety.export" and a[0][5][0]["metadata"]["rows"] == 1
      and a[0][5][0]["actorId"] == USER_V and a[0][3] == SERVICE_KEY, str(a[:1]))
STUB.reset(); STUB.answer["audit_logs"] = (500, {"message": "down"}, {})
r = client.get(base + "/export", headers=V)
check("if the export cannot be audited it is not produced (503) and no data is in the response", r.status_code == 503 and "Worker" not in r.text)
STUB.reset()
for q in ("until=2026-01-01&since=2026-02-01", "since=2024-01-01&until=2026-01-01", "status=NOPE", "since=garbage"):
    check(f"/export?{q[:38]}: 422", client.get(base + "/export?" + q, headers=V).status_code == 422)
STUB.reset()
sr.EXPORT_PAGE, sr.EXPORT_ROW_CAP = 2, 5
def numbered(n):
    return [dict(default_rows("safety_decisions")[0], workerKey=f"trk-{i}", observedAt=f"2026-11-09T04:{59 - i:02d}:00Z", snapshotRef=None, reason=f"r{i}") for i in range(n)]
STUB.big = numbered(4)                    # newest first, as the query orders them
r = client.get(base + "/export", headers=V)
pages = [c for c in STUB.calls if c[1] == "safety_decisions"]
rows = list(__import__("csv").reader(__import__("io").StringIO(r.content.decode("utf-8-sig"))))
check("a long export is fetched page by page (offsets 0, 2, 4) until an EMPTY page ends it", [c[2]["offset"][0] for c in pages] == ["0", "2", "4"], str([c[2]["offset"] for c in pages]))
check("...every page asked in a stable order (newest first with the id as tiebreaker)", all(c[2]["order"] == ["observedAt.desc,id.desc"] for c in pages))
check("...from a set frozen at the start (rows the sync adds meanwhile cannot move the pages)", any(x.startswith("(createdAt.lte.") for x in pages[0][2]["and"]))
check("a complete export has every row, oldest first, and no truncation note", len(rows) == 5 and rows[1][4] == "Worker 3" and rows[4][4] == "Worker 0" and "X-Export-Truncated" not in r.headers, str([x[4] for x in rows]))
STUB.reset(); STUB.big = numbered(9)
r = client.get(base + "/export", headers=V)
rows = list(__import__("csv").reader(__import__("io").StringIO(r.content.decode("utf-8-sig"))))
check("past the cap the NEWEST rows are kept, the file says so in its last row, and the header says so too",
      r.headers.get("X-Export-Truncated") == "true" and [x[4] for x in rows[1:6]] == ["Worker 4", "Worker 3", "Worker 2", "Worker 1", "Worker 0"] and rows[-1][0].startswith("Only the newest 5"), str(rows[-3:]))
STUB.reset(); STUB.big = numbered(5)
r = client.get(base + "/export", headers=V)
check("exactly the cap is NOT reported as truncated", "X-Export-Truncated" not in r.headers and len(list(__import__("csv").reader(__import__("io").StringIO(r.content.decode("utf-8-sig"))))) == 6)
sr.EXPORT_PAGE, sr.EXPORT_ROW_CAP = 1000, 20000

STUB.reset(); STUB.answer["safety_decisions"] = (401, {"code": "PGRST301", "message": "JWT expired"}, {})
r = client.get(base + "/decisions", headers=M)
check("an expired token the identity cache still remembers is 401 'sign in again', not 403 'no permission'", r.status_code == 401 and "expired" in r.json()["detail"], r.text[:100])
STUB.reset(); STUB.answer["safety_decisions"] = (416, {"code": "PGRST103", "message": "Requested range not satisfiable"}, {})
r = client.get(base + "/decisions?offset=900", headers=M)
check("paging past the last row is an empty page, not a 502", r.status_code == 200 and r.json()["decisions"] == [] and r.json()["next_offset"] is None, r.text[:100])
STUB.reset()
client.get(base + "/events", headers=M); client.get(base + "/decisions", headers=M)
check("lists are ordered by time with the id as a tiebreaker (no duplicated or skipped rows across pages)", all(c[2]["order"] == ["observedAt.desc,id.desc"] for c in sent()), str([c[2]["order"] for c in sent()]))
STUB.reset()
r = client.get(base + "/summary?days=7&tz=Asia/Kolkata", headers=V)
c = next(c for c in STUB.calls if c[1] == "rpc/safety_summary")
check("the summary passes the site's time zone to the database", c[5]["p_tz"] == "Asia/Kolkata")
check("...and refuses a malformed one before asking", client.get(base + "/summary?tz=a%3Bdrop", headers=V).status_code == 422)

# ── G. Errors from the network ──────────────────────────────────────────
print()
print("G. When the database is not there")
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
r = client.get(base + "/decisions", headers=M)
check("an unreachable database is a 502 with a generic sentence", r.status_code == 502 and "127.0.0.1" not in r.text, r.text[:100])
os.environ.pop("SUPABASE_KEY")
r = client.get(base + "/rules", headers=M)
check("a missing key is a 503, not a crash", r.status_code in (502, 503))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
