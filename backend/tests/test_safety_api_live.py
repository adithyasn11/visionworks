"""
The safety API (Step 11) for real: the live Supabase project, real users with real tokens, the real app.

Creates two throw-away organisations and four users (a VIEWER, a MANAGER and an ADMIN of one, a manager of
the other), signs each in, and drives /api/v1/safety through the real FastAPI app (a TestClient; the token
verification, the Row Level Security and the database functions are all the real ones). Also checks that a
session picks up the organisation's own rules. Removes everything it created. Skips with a message when
Supabase is not configured or the safety tables / 026 / 027 are not applied.

    venv/Scripts/python.exe backend/tests/test_safety_api_live.py
"""
import base64
import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
from dotenv import load_dotenv                                           # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))
URL, ANON, SERVICE = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
if not (URL and ANON and SERVICE) or "your-" in SERVICE:
    print("SKIP: Supabase is not configured in backend/.env")
    sys.exit(0)

from supabase import create_client                                       # noqa: E402

svc = create_client(URL, SERVICE)
try:
    svc.table("safety_decisions").select("id").limit(1).execute()
    svc.rpc("safety_summary", {"p_org": str(uuid.uuid4()), "p_since": "2026-01-01T00:00:00Z", "p_until": "2026-01-02T00:00:00Z"}).execute()
except Exception as e:
    print(f"SKIP: the safety tables or functions are not reachable ({str(e)[:100]}); apply 025, 026 and 027 first")
    sys.exit(0)

from fastapi.testclient import TestClient                                # noqa: E402

from app.main import app                                                 # noqa: E402
from app.safety.rules_store import rules_for_session                     # noqa: E402

FAILS = []
TAG = uuid.uuid4().hex[:8]
PASSWORD = "Api-" + uuid.uuid4().hex + "!"
orgs, users = [], []
now = lambda d=0: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {str(detail)[:160]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def uid():
    return str(uuid.uuid4())


def new_user(label):
    email = f"api-{label}-{TAG}@example.test"
    u = svc.auth.admin.create_user({"email": email, "password": PASSWORD, "email_confirm": True})
    users.append(u.user.id)
    c = create_client(URL, ANON)
    session = c.auth.sign_in_with_password({"email": email, "password": PASSWORD}).session
    return u.user.id, email, {"Authorization": f"Bearer {session.access_token}"}


def new_org(label):
    oid = uid()
    svc.table("organisations").insert({"id": oid, "name": f"API {label} {TAG}", "slug": f"api-{label.lower()}-{TAG}", "plan": "ENTERPRISE", "updatedAt": now()}).execute()
    orgs.append(oid)
    return oid


def member(org, pid, email, role):
    svc.table("memberships").insert({"orgId": org, "profileId": pid, "invitedEmail": email, "role": role, "status": "ACTIVE", "acceptedAt": now(), "updatedAt": now()}).execute()


def camera(org, name):
    cid = uid()
    svc.table("cameras").insert({"id": cid, "orgId": org, "name": name, "sourceType": "UPLOAD", "updatedAt": now()}).execute()
    return cid


def zone(org, cam, name, slug, stype):
    zid = uid()
    svc.table("zones").insert({"id": zid, "orgId": org, "cameraId": cam, "name": name, "slug": slug, "safetyType": stype,
                               "polygon": [[0, 0], [9, 0], [9, 9]], "updatedAt": now()}).execute()
    return zid


def decision(org, cam, zn, status, review, worker, days=0, reason="Entered Press line with no helmet seen in 28 of 29 clear frames.", picture=None):
    did = uid()
    svc.table("safety_decisions").insert({
        "id": did, "orgId": org, "cameraId": cam, "zoneId": zn, "ruleType": "PPE_ENTRY", "severity": "WARNING", "workerKey": worker, "status": status,
        "reason": reason, "confidence": 0.0 if status == "UNVERIFIABLE" else 0.97, "evidence": {"items": {"helmet": {"state": "ABSENT"}}}, "videoTimeS": 5,
        "observedAt": now(days), "reviewState": review, "snapshotRef": picture,
        **({"reviewedAt": now()} if review in ("CONFIRMED", "DISMISSED") else {})}).execute()
    return did


print("=" * 70)
print("SAFETY API: LIVE SUPABASE, REAL TOKENS")
print("=" * 70)
try:
    orgA, orgB = new_org("A"), new_org("B")
    vid, vmail, V = new_user("viewer")
    mid, mmail, M = new_user("manager")
    aid, amail, A = new_user("admin")
    oid, omail, O = new_user("other")
    member(orgA, vid, vmail, "VIEWER"); member(orgA, mid, mmail, "MANAGER"); member(orgA, aid, amail, "ADMIN"); member(orgB, oid, omail, "ADMIN")
    camA, camA2, camB = camera(orgA, "Cam A"), camera(orgA, "Cam A2"), camera(orgB, "Cam B")
    zR, zG, zOther, zB = (zone(orgA, camA, "Press line", f"zone_p{TAG}", "RESTRICTED"), zone(orgA, camA, "Lawn", f"zone_l{TAG}", "GENERAL"),
                          zone(orgA, camA2, "Dock", f"zone_d{TAG}", "RESTRICTED"), zone(orgB, camB, "Foundry", f"zone_f{TAG}", "RESTRICTED"))
    d_flag = decision(orgA, camA, zR, "POTENTIAL_VIOLATION", "PENDING", "trk-3", picture=f"{orgA}/x.jpg")
    d_flag2 = decision(orgA, camA, zR, "POTENTIAL_VIOLATION", "PENDING", "trk-4", days=2, reason="=cmd|' /C calc'!A0 formula-looking reason")
    d_ok = decision(orgA, camA, zR, "COMPLIANT", "NOT_REQUIRED", "trk-5", days=1)
    d_unv = decision(orgA, camA, zR, "UNVERIFIABLE", "NOT_REQUIRED", "trk-6", days=3)
    d_old = decision(orgA, camA, zR, "POTENTIAL_VIOLATION", "DISMISSED", "trk-7", days=40)
    d_foreign = decision(orgB, camB, zB, "POTENTIAL_VIOLATION", "PENDING", "trk-1")
    for i in range(3):
        svc.table("safety_events").insert({"eventUid": uid(), "orgId": orgA, "cameraId": camA, "zoneId": zR, "workerKey": f"trk-{i}", "eventType": "ENTERED_ZONE",
                                           "videoTimeS": i, "observedAt": now()}).execute()
    svc.table("safety_events").insert({"eventUid": uid(), "orgId": orgB, "cameraId": camB, "zoneId": zB, "workerKey": "trk-1", "eventType": "ENTERED_ZONE", "videoTimeS": 1, "observedAt": now()}).execute()
    client = TestClient(app)
    base = "/api/v1/safety"
    audit = lambda action, org=orgA: svc.table("audit_logs").select("*").eq("orgId", org).eq("action", action).execute().data
    print("  seeded two organisations, four users with real sessions, and decisions")

    print()
    print("  Identity")
    check("no token is 401", client.get(base + "/decisions").status_code == 401)
    check("a forged token is 401", client.get(base + "/decisions", headers={"Authorization": "Bearer " + base64.b64encode(b'{"alg":"none"}').decode() + ".e30."}).status_code == 401)
    check("a VIEWER reads decisions (200)", client.get(base + "/decisions", headers=V).status_code == 200)

    print()
    print("  Reading (real Row Level Security under the caller's own token)")
    r = client.get(base + "/decisions", headers=V).json()
    ids = {d["id"] for d in r["decisions"]}
    check("a viewer sees exactly their organisation's five decisions, never the other's", ids == {d_flag, d_flag2, d_ok, d_unv, d_old} and r["total"] == 5, str(r["total"]))
    check("newest first", [d["observedAt"] for d in r["decisions"]] == sorted((d["observedAt"] for d in r["decisions"]), reverse=True))
    check("zone and camera names come with them; the picture path does not (only has_picture)", r["decisions"][0]["zone"]["name"] == "Press line" and r["decisions"][0]["camera"]["name"] == "Cam A"
          and all("snapshotRef" not in d for d in r["decisions"]) and next(d for d in r["decisions"] if d["id"] == d_flag)["has_picture"] is True)
    q = lambda s: client.get(base + "/decisions?" + s, headers=V).json()
    check("filter: status", {d["id"] for d in q("status=UNVERIFIABLE")["decisions"]} == {d_unv})
    check("filter: review_state=PENDING", {d["id"] for d in q("review_state=PENDING")["decisions"]} == {d_flag, d_flag2})
    check("filter: status and review_state together", {d["id"] for d in q("status=POTENTIAL_VIOLATION&review_state=DISMISSED")["decisions"]} == {d_old})
    check("filter: zone", q(f"zone_id={zR}")["total"] == 5 and q(f"zone_id={zG}")["total"] == 0)
    check("filter: worker", {d["id"] for d in q("worker_key=trk-5")["decisions"]} == {d_ok})
    since = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    check("filter: since (the 40-day-old one is outside the window)", d_old not in {d["id"] for d in q("since=" + since)["decisions"]} and q("since=" + since)["total"] == 4)
    check("another organisation's zone as a filter returns nothing, not an error", q(f"zone_id={zB}")["total"] == 0)
    check("paging past the last row is an empty page, not an error", q("limit=2&offset=500")["decisions"] == [] and q("limit=2&offset=500")["next_offset"] is None)
    page1, page2 = q("limit=2&offset=0"), q("limit=2&offset=2")
    check("paging: two pages of two, then the rest, with no overlap", len(page1["decisions"]) == 2 and page1["next_offset"] == 2 and page2["next_offset"] == 4
          and not ({d["id"] for d in page1["decisions"]} & {d["id"] for d in page2["decisions"]}) and q("limit=2&offset=4")["next_offset"] is None)
    check("one decision, with its evidence and thresholds", client.get(base + f"/decisions/{d_flag}", headers=V).json()["evidence"]["items"]["helmet"]["state"] == "ABSENT")
    check("another organisation's decision is 404, the same as one that does not exist",
          client.get(base + f"/decisions/{d_foreign}", headers=V).status_code == 404 and client.get(base + f"/decisions/{uid()}", headers=V).status_code == 404)
    ev = client.get(base + "/events", headers=V).json()
    check("events: only this organisation's three", ev["total"] == 3 and all(e["eventType"] == "ENTERED_ZONE" for e in ev["events"]))
    check("events: the other organisation's manager sees only theirs", client.get(base + "/events", headers=O).json()["total"] == 1)
    check("a viewer of one organisation cannot see the other's decisions through a filter on its id",
          client.get(base + f"/decisions?camera_id={camB}", headers=V).json()["total"] == 0)

    print()
    print("  Summary")
    s = client.get(base + "/summary?days=7", headers=V).json()
    check("counts for the last 7 days: 4 decisions, 2 flags, 1 compliant, 1 abstention, 2 open", (s["total"], s["potential_violations"], s["compliant"], s["abstentions"], s["open_alerts"]) == (4, 2, 1, 1, 2), str(s))
    check("a 60-day window includes the old dismissed flag", client.get(base + "/summary?days=60", headers=V).json()["dismissed"] == 1)
    check("by zone, ordered by flags", s["by_zone"][0]["zone"] == "Press line" and s["by_zone"][0]["decisions"] == 4, str(s["by_zone"][:1]))
    check("by day: EVERY day of the window, zero-filled", len(s["by_day"]) in (7, 8) and sum(d["decisions"] for d in s["by_day"]) == 4 and any(d["decisions"] == 0 for d in s["by_day"]), str(s["by_day"]))
    k = client.get(base + "/summary?days=7&tz=Asia/Kolkata", headers=V).json()
    check("a site time zone is accepted and reported; an unknown one is refused", k["time_zone"] == "Asia/Kolkata" and client.get(base + "/summary?tz=Mars/Base", headers=V).status_code == 422)
    check("the other organisation's summary is its own", client.get(base + "/summary?days=7", headers=O).json()["total"] == 1)

    print()
    print("  Review")
    r = client.post(base + f"/decisions/{d_flag}/review", headers=V, json={"state": "CONFIRMED"})
    check("a VIEWER cannot review (403)", r.status_code == 403)
    r = client.post(base + f"/decisions/{d_ok}/review", headers=M, json={"state": "CONFIRMED"})
    check("a COMPLIANT decision cannot be reviewed (422, in the database's words)", r.status_code == 422 and "compliant" in r.json()["detail"], r.text[:100])
    r = client.post(base + f"/decisions/{d_foreign}/review", headers=M, json={"state": "CONFIRMED"})
    check("another organisation's decision cannot be reviewed (403)", r.status_code == 403)
    r = client.post(base + f"/decisions/{d_flag}/review", headers=M, json={"state": "CONFIRMED", "reason": "CORRECT", "note": "seen on the replay"})
    check("a MANAGER confirms a flag", r.status_code == 200 and r.json()["reviewState"] == "CONFIRMED" and r.json()["reviewReasonCode"] == "CORRECT" and r.json()["reviewedById"] == mid, r.text[:140])
    rev = [a for a in audit("safety.decision_reviewed") if a["targetId"] == d_flag]
    check("...with exactly one audit row naming the manager", len(rev) == 1 and rev[0]["actorId"] == mid and rev[0]["metadata"]["state"] == "CONFIRMED", str(rev))
    r = client.post(base + f"/decisions/{d_flag2}/review", headers=A, json={"state": "DISMISSED", "reason": "WRONG_PERSON"})
    check("an ADMIN dismisses one", r.status_code == 200 and r.json()["reviewState"] == "DISMISSED")
    s = client.get(base + "/summary?days=7", headers=V).json()
    check("the summary follows: 0 open, 1 confirmed, 1 dismissed, half of the judged flags agreed with", (s["open_alerts"], s["confirmed"], s["dismissed"]) == (0, 1, 1) and float(s["confirmed_share_of_reviewed"]) == 0.5, str(s))
    check("the open-alerts filter now returns nothing", client.get(base + "/decisions?review_state=PENDING", headers=V).json()["total"] == 0)

    print()
    print("  Rules")
    body = {"name": "Hat and vest", "type": "PPE_ENTRY", "zone_id": zR, "params": {"required_ppe": ["helmet", "safety_vest"]}, "severity": "CRITICAL"}
    check("a VIEWER cannot create, change, validate or delete a rule (403 each)",
          [client.post(base + "/rules", headers=V, json=body).status_code, client.post(base + "/rules/validate", headers=V, json=body).status_code,
           client.patch(base + f"/rules/{uid()}", headers=V, json={"name": "x"}).status_code, client.delete(base + f"/rules/{uid()}", headers=V).status_code] == [403] * 4)
    v = client.post(base + "/rules/validate", headers=M, json=body).json()
    check("validate: a good rule", v["valid"] is True and v["params"]["mode"] == "AT_ENTRY")
    check("validate: a General zone is refused with a reason", client.post(base + "/rules/validate", headers=M, json={**body, "zone_id": zG}).json()["valid"] is False)
    check("validate: another organisation's zone is refused (it is not visible to this caller)", client.post(base + "/rules/validate", headers=M, json={**body, "zone_id": zB}).json()["valid"] is False)
    check("validate: a zone on another camera than the one named is refused",
          client.post(base + "/rules/validate", headers=M, json={**body, "zone_id": zOther, "camera_id": camA}).json()["valid"] is False)
    r = client.post(base + "/rules", headers=M, json=body)
    rule_id = r.json().get("id")
    check("a MANAGER creates a rule (201), owned by them, dated now", r.status_code == 201 and r.json()["createdById"] == mid and r.json()["severity"] == "CRITICAL" and r.json()["evaluated_by_engine"] is True, r.text[:140])
    check("...with an audit row naming them", [a["actorId"] for a in audit("safety.rule_created") if a["targetId"] == rule_id] == [mid])
    check("a viewer lists it, and the engine flag is shown", [x["name"] for x in client.get(base + "/rules", headers=V).json()["rules"]] == ["Hat and vest"])
    check("a camelCase field (what a read returns) is refused on write instead of silently ignored", client.post(base + "/rules", headers=M, json={**body, "isEnabled": False}).status_code == 422)
    check("an invalid rule is not stored (422)", client.post(base + "/rules", headers=M, json={**body, "zone_id": zG}).status_code == 422 and len(client.get(base + "/rules", headers=V).json()["rules"]) == 1)
    r = client.patch(base + f"/rules/{rule_id}", headers=A, json={"severity": "INFO", "is_enabled": False})
    check("an ADMIN changes it; unchanged fields stay", r.status_code == 200 and r.json()["severity"] == "INFO" and r.json()["isEnabled"] is False and r.json()["name"] == "Hat and vest", r.text[:140])
    check("...and it is audited", [a["metadata"]["fields"] for a in audit("safety.rule_updated") if a["targetId"] == rule_id] == [["is_enabled", "severity"]])
    check("another organisation's manager cannot see, change or delete it",
          client.patch(base + f"/rules/{rule_id}", headers=O, json={"name": "x"}).status_code == 404 and client.delete(base + f"/rules/{rule_id}", headers=O).status_code in (403, 404))

    print()
    print("  A session picks up the organisation's own rules")
    local_zones = [{"zone_id": f"zone_p{TAG}", "zone_name": "Press line", "zone_type": "RESTRICTED"}, {"zone_id": f"zone_x{TAG}", "zone_name": "Other", "zone_type": "RESTRICTED"}]
    got = rules_for_session(orgA, local_zones, "Cam A")
    by_zone = {r.zone_id: r for r in got}
    check("a DISABLED rule on a zone switches that zone's default off (no rule at all for it)", f"zone_p{TAG}" not in by_zone, str([(r.zone_id, r.source) for r in got]))
    check("...while a restricted zone with no rule keeps the built-in default", by_zone[f"zone_x{TAG}"].source == "default")
    client.patch(base + f"/rules/{rule_id}", headers=M, json={"is_enabled": True, "severity": "CRITICAL", "params": {"required_ppe": ["helmet"]}})
    got = {r.zone_id: r for r in rules_for_session(orgA, local_zones, "Cam A")}
    r1 = got[f"zone_p{TAG}"]
    check("enabled again: the organisation's rule runs, with its id, severity and parameters, in place of the default",
          r1.source == "database" and r1.id == rule_id and r1.severity == "CRITICAL" and r1.params["required_ppe"] == ["helmet"], str(r1))
    check("a session of another organisation gets none of it", all(r.source == "default" for r in rules_for_session(orgB, local_zones, "Cam B")))
    check("a rule for a zone this camera does not have is ignored", all(r.zone_id != zOther for r in rules_for_session(orgA, [local_zones[1]], "Cam A")))
    check("an unregistered camera name gets the default and nothing else", all(r.source == "default" for r in rules_for_session(orgA, local_zones, "no-such-camera")))

    # two cameras, ONE slug: a rule on the other camera's zone must not reach (or switch off) this camera
    zDup1 = zone(orgA, camA, "Dup on A", f"zone_dup{TAG}", "RESTRICTED")
    zDup2 = zone(orgA, camA2, "Dup on A2", f"zone_dup{TAG}", "RESTRICTED")
    dup_local = [{"zone_id": f"zone_dup{TAG}", "zone_name": "Dup", "zone_type": "RESTRICTED"}]
    r_off = client.post(base + "/rules", headers=M, json={"name": "off on A2", "type": "PPE_ENTRY", "zone_id": zDup2, "is_enabled": False, "params": {"required_ppe": ["helmet"]}})
    check("a (disabled) rule is created for the zone on the OTHER camera", r_off.status_code == 201, r_off.text[:100])
    on_a = rules_for_session(orgA, dup_local, "Cam A")
    on_a2 = rules_for_session(orgA, dup_local, "Cam A2")
    check("camera A is still protected by the default (the rule on A2's zone of the same name does not switch it off)", [r.source for r in on_a] == ["default"], str([(r.source, r.zone_id) for r in on_a]))
    check("camera A2, whose zone the rule is for, has its default switched off", on_a2 == [])

    # rules the engine cannot run must not remove the default
    client.delete(base + f"/rules/{r_off.json()['id']}", headers=M)
    zChk = zone(orgA, camA, "Checkpoint", f"zone_chk{TAG}", "PPE_CHECKPOINT")
    chk_local = [{"zone_id": f"zone_dup{TAG}", "zone_name": "Dup", "zone_type": "RESTRICTED"}, {"zone_id": f"zone_chk{TAG}", "zone_name": "Gate", "zone_type": "PPE_CHECKPOINT"}]
    r_via = client.post(base + "/rules", headers=M, json={"name": "via gate", "type": "PPE_ENTRY", "zone_id": zDup1,
                                                           "params": {"required_ppe": ["helmet"], "mode": "VIA_CHECKPOINT", "checkpoint_zone_id": zChk}})
    check("a VIA_CHECKPOINT rule (references the checkpoint by its id) is accepted and flagged as not evaluated yet", r_via.status_code == 201 and r_via.json()["evaluated_by_engine"] is False, r_via.text[:140])
    got = rules_for_session(orgA, chk_local, "Cam A")
    check("...and it does NOT remove the zone's default: the zone is still checked at entry", any(r.source == "default" and r.zone_id == f"zone_dup{TAG}" for r in got), str([(r.source, r.zone_id, r.params.get("mode")) for r in got]))
    check("...while the rule itself arrives with its checkpoint translated to the zone's slug, ready for the day the engine runs it",
          any(r.source == "database" and r.params.get("checkpoint_zone_id") == f"zone_chk{TAG}" for r in got))
    client.delete(base + f"/rules/{r_via.json()['id']}", headers=M)
    # a rule that no longer validates in the session (the zone's local type is not RESTRICTED) keeps the default too
    r_ok = client.post(base + "/rules", headers=M, json={"name": "hat", "type": "PPE_ENTRY", "zone_id": zDup1, "params": {"required_ppe": ["helmet"]}})
    mismatched = [{"zone_id": f"zone_dup{TAG}", "zone_name": "Dup", "zone_type": "GENERAL"}]
    check("a rule that does not validate against the session's own zone does not leave it unprotected (no rule, no default: this zone is General here)", rules_for_session(orgA, mismatched, "Cam A") == [])
    client.delete(base + f"/rules/{r_ok.json()['id']}", headers=M)
    r = client.delete(base + f"/rules/{rule_id}", headers=M)
    check("a MANAGER deletes the rule (204), audited by the database function", r.status_code == 204 and [a["actorId"] for a in audit("safety.rule_deleted") if a["targetId"] == rule_id] == [mid])
    check("it is gone from the list and the default is back for its zone", client.get(base + "/rules", headers=V).json()["rules"] == []
          and {r.zone_id: r.source for r in rules_for_session(orgA, local_zones, "Cam A")}[f"zone_p{TAG}"] == "default")
    check("deleting it again is 404", client.delete(base + f"/rules/{rule_id}", headers=M).status_code == 404)

    print()
    print("  Export")
    r = client.get(base + "/export?since=" + (datetime.now(timezone.utc) - timedelta(days=60)).isoformat().replace("+00:00", "Z"), headers=V)
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    check("a viewer exports CSV: header and one row per decision (5)", r.status_code == 200 and rows[0][:3] == ["Time (UTC)", "Camera", "Zone"] and len(rows) == 6, str(len(rows)))
    check("workers read 'Worker N'; a formula-looking reason is neutralised", {r_[4] for r_ in rows[1:]} == {"Worker 3", "Worker 4", "Worker 5", "Worker 6", "Worker 7"} and any(r_[8].startswith("'=cmd") for r_ in rows[1:]))
    check("only this organisation's rows", all(r_[1] == "Cam A" for r_ in rows[1:]))
    check("the export wrote an audit row naming the viewer and the row count", [(a["actorId"], a["metadata"]["rows"]) for a in audit("safety.export")][-1:] == [(vid, 5)])
    check("a status filter narrows it", len(list(csv.reader(io.StringIO(client.get(base + "/export?status=UNVERIFIABLE", headers=V).content.decode("utf-8-sig"))))) == 2)

    print()
    print("  A member cannot forge what the API audits")
    from postgrest import ReturnMethod
    pv = create_client(URL, ANON); pv.auth.sign_in_with_password({"email": vmail, "password": PASSWORD})
    try:
        pv.table("audit_logs").insert({"orgId": orgA, "action": "safety.export", "targetType": "x", "targetId": "x", "metadata": {}}, returning=ReturnMethod.minimal).execute()
        forged = True
    except Exception:
        forged = False
    check("a viewer's own direct insert of a 'safety.export' audit row is refused by the database", forged is False)
finally:
    for oid_ in orgs:
        try:
            svc.table("organisations").update({"deletedAt": now()}).eq("id", oid_).execute()
            svc.table("memberships").delete().eq("orgId", oid_).execute()
            svc.table("organisations").delete().eq("id", oid_).execute()
        except Exception as e:
            print(f"  cleanup: organisation {oid_[:8]}: {str(e)[:100]}")
    for pid in users:
        for fn in (lambda: svc.table("profiles").delete().eq("id", pid).execute(), lambda: svc.auth.admin.delete_user(pid)):
            try:
                fn()
            except Exception:
                pass
    left = svc.table("organisations").select("id").like("slug", f"api-%-{TAG}").execute().data
    print(f"  cleaned up (organisations left behind: {len(left)})")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
