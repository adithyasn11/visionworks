"""
Step 7 — who may read and write the safety tables, measured with real sessions.

Runs against the live Supabase project from backend/.env (SUPABASE_URL, the anon
key and the service-role key) and skips with a clear message when none is
configured or 025_safety.sql has not been applied. It creates two throw-away
organisations and four throw-away users (a VIEWER, a MANAGER and an ADMIN of
organisation A, and a member of organisation B), signs each in for a real JWT,
asks the questions of the matrix in design doc section 24.12 through PostgREST
(the same path the browser takes), and removes everything again at the end.

Every "refused" is a test that must fail with a permission or RLS error, or
change nothing; every "allowed" is one that must succeed. Nothing is assumed.

    venv/Scripts/python.exe backend/tests/test_safety_rls.py
"""
import os
import sys
import uuid
import base64
import json

sys.path.insert(0, os.path.abspath("backend"))
from dotenv import load_dotenv

load_dotenv("backend/.env")

URL = os.getenv("SUPABASE_URL")
ANON = os.getenv("SUPABASE_KEY")
SERVICE = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
if not (URL and ANON and SERVICE):
    print("SKIP: SUPABASE_URL / SUPABASE_KEY / SUPABASE_SERVICE_ROLE_KEY are not set in backend/.env")
    sys.exit(0)

from supabase import create_client  # noqa: E402
from postgrest import ReturnMethod  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def role_of(key):
    try:
        part = key.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("role")
    except Exception:
        return None


if role_of(ANON) != "anon" or role_of(SERVICE) != "service_role":
    print("SKIP: backend/.env keys are not an anon + service_role pair")
    sys.exit(0)

svc = create_client(URL, SERVICE)
try:
    svc.table("safety_rules").select("id").limit(1).execute()
except Exception as e:
    print(f"SKIP: the safety tables are not reachable ({str(e)[:120]}); apply frontend/prisma/sql/025_safety.sql first")
    sys.exit(0)

TAG = uuid.uuid4().hex[:8]
PASSWORD = "Rls-" + uuid.uuid4().hex + "!"
created_users, created_orgs = [], []


def uid():
    return str(uuid.uuid4())


def refused(fn):
    """True when the call is rejected (an error) or is silently filtered to nothing."""
    try:
        r = fn()
    except Exception as e:
        return True, str(e)[:140]
    data = getattr(r, "data", None)
    return (not data), "no error, but nothing changed" if not data else "ALLOWED"


def allowed(fn):
    try:
        r = fn()
        return True, getattr(r, "data", None)
    except Exception as e:
        return False, str(e)[:160]


def new_user(label):
    email = f"rls-{label}-{TAG}@example.test"
    u = svc.auth.admin.create_user({"email": email, "password": PASSWORD, "email_confirm": True})
    created_users.append(u.user.id)
    return u.user.id, email


def client_for(email):
    c = create_client(URL, ANON)
    c.auth.sign_in_with_password({"email": email, "password": PASSWORD})
    return c


def new_org(label):
    oid = uid()
    svc.table("organisations").insert(
        {"id": oid, "name": f"RLS {label} {TAG}", "slug": f"rls-{label.lower()}-{TAG}",
         "plan": "ENTERPRISE", "updatedAt": "2026-01-01T00:00:00Z"}).execute()
    created_orgs.append(oid)
    return oid


def member(org, profile, email, role):
    svc.table("memberships").insert(
        {"orgId": org, "profileId": profile, "invitedEmail": email, "role": role,
         "status": "ACTIVE", "acceptedAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}).execute()


def camera(org, name):
    cid = uid()
    svc.table("cameras").insert(
        {"id": cid, "orgId": org, "name": name, "sourceType": "UPLOAD", "updatedAt": "2026-01-01T00:00:00Z"}).execute()
    return cid


def zone(org, cam, name):
    zid = uid()
    svc.table("zones").insert(
        {"id": zid, "orgId": org, "cameraId": cam, "name": name, "slug": f"zone_{zid[:6]}",
         "polygon": [[0, 0], [10, 0], [10, 10]], "safetyType": "RESTRICTED",
         "updatedAt": "2026-01-01T00:00:00Z"}).execute()
    return zid


def decision(org, cam, zn, status, review="NOT_REQUIRED", rule=None, worker="trk-1", observed=None):
    did = uid()
    conf = 0.0 if status == "UNVERIFIABLE" else 0.97
    extra = {"reviewedAt": ago(0)} if review in ("CONFIRMED", "DISMISSED") else {}
    svc.table("safety_decisions").insert(
        {"id": did, "orgId": org, "cameraId": cam, "zoneId": zn, "ruleId": rule, "ruleType": "PPE_ENTRY",
         "severity": "WARNING", "workerKey": worker, "status": status,
         "reason": "Entered Press line with no helmet seen in 28 of 29 clear frames.",
         "confidence": conf, "evidence": {"items": {}}, "videoTimeS": 12.5,
         "observedAt": observed or ago(0.01), "reviewState": review, **extra}).execute()
    return did


def audit_count(org, action):
    r = svc.table("audit_logs").select("id").eq("orgId", org).eq("action", action).execute()
    return len(r.data or [])


print("=" * 70)
print("SAFETY TABLES: ROW LEVEL SECURITY MATRIX (design doc 24.12)")
print("=" * 70)

try:
    # ── Setup ────────────────────────────────────────────────────────────
    orgA, orgB = new_org("A"), new_org("B")
    viewer_id, viewer_mail = new_user("viewer")
    manager_id, manager_mail = new_user("manager")
    admin_id, admin_mail = new_user("admin")
    other_id, other_mail = new_user("other")
    member(orgA, viewer_id, viewer_mail, "VIEWER")
    member(orgA, manager_id, manager_mail, "MANAGER")
    member(orgA, admin_id, admin_mail, "ADMIN")
    member(orgB, other_id, other_mail, "ADMIN")
    camA, camA2, camB = camera(orgA, "Cam A"), camera(orgA, "Cam A2"), camera(orgB, "Cam B")
    zA, zA2, zB = zone(orgA, camA, "Press line"), zone(orgA, camA2, "Dock"), zone(orgB, camB, "Foundry")

    ruleA = svc.table("safety_rules").insert(
        {"orgId": orgA, "cameraId": camA, "zoneId": zA, "name": "Helmet and vest", "type": "PPE_ENTRY",
         "params": {"required_ppe": ["helmet", "safety_vest"], "mode": "AT_ENTRY"}}).execute().data[0]["id"]
    d_flag = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="PENDING", rule=ruleA)
    d_ok = decision(orgA, camA, zA, "COMPLIANT", worker="trk-2")
    d_unv = decision(orgA, camA, zA, "UNVERIFIABLE", review="PENDING", worker="trk-3")
    d_flag2 = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="PENDING", worker="trk-4")
    d_foreign = decision(orgB, camB, zB, "POTENTIAL_VIOLATION", review="PENDING")
    d_conf_a = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="CONFIRMED", worker="trk-5")
    d_conf_b = decision(orgB, camB, zB, "POTENTIAL_VIOLATION", review="CONFIRMED", worker="trk-6")
    svc.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgA, "cameraId": camA, "zoneId": zA, "workerKey": "trk-1",
         "eventType": "ENTERED_ZONE", "videoTimeS": 3.0, "observedAt": "2026-11-09T10:00:00Z"}).execute()
    svc.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgB, "cameraId": camB, "zoneId": zB, "workerKey": "trk-1",
         "eventType": "ENTERED_ZONE", "videoTimeS": 3.0, "observedAt": "2026-11-09T10:00:00Z"}).execute()
    print(f"  seeded two organisations, four users, rules, decisions and events (run {TAG})")

    viewer, manager, admin, other = (client_for(viewer_mail), client_for(manager_mail),
                                     client_for(admin_mail), client_for(other_mail))
    anon = create_client(URL, ANON)
    print("  signed in a VIEWER, a MANAGER, an ADMIN and an outsider")

    new_rule = lambda c, **kw: c.table("safety_rules").insert(
        {"orgId": orgA, "zoneId": zA, "name": "Probe", "type": "PPE_ENTRY", "params": {}, **kw}).execute()

    # ── VIEWER ───────────────────────────────────────────────────────────
    print()
    print("  VIEWER (same organisation)")
    check("reads the organisation's rules", len(viewer.table("safety_rules").select("id").execute().data) == 1)
    check("reads its decisions (all five, never the other organisation's)",
          sorted(r["id"] for r in viewer.table("safety_decisions").select("id").execute().data)
          == sorted([d_flag, d_ok, d_unv, d_flag2, d_conf_a]))
    check("reads its events", len(viewer.table("safety_events").select("id").execute().data) == 1)
    ok, why = refused(lambda: new_rule(viewer))
    check("cannot insert a rule", ok, why)
    ok, why = refused(lambda: viewer.table("safety_rules").update({"name": "x"}).eq("id", ruleA).execute())
    check("cannot update a rule", ok and svc.table("safety_rules").select("name").eq("id", ruleA).execute().data[0]["name"] == "Helmet and vest", why)
    ok, why = refused(lambda: viewer.table("safety_decisions").update({"status": "COMPLIANT"}).eq("id", d_flag).execute())
    check("cannot update a decision", ok, why)
    ok, why = refused(lambda: viewer.table("safety_decisions").insert(
        {"id": uid(), "orgId": orgA, "cameraId": camA, "ruleType": "PPE_ENTRY", "severity": "INFO",
         "workerKey": "trk-9", "status": "COMPLIANT", "reason": "forged", "confidence": 1,
         "evidence": {}, "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z"}).execute())
    check("cannot insert a decision", ok, why)
    ok, why = refused(lambda: viewer.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgA, "cameraId": camA, "workerKey": "trk-9", "eventType": "ENTERED_ZONE",
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z"}).execute())
    check("cannot insert an event", ok, why)
    ok, why = refused(lambda: viewer.rpc("review_safety_decision", {"p_decision_id": d_flag, "p_state": "CONFIRMED"}).execute())
    check("cannot call review_safety_decision()", ok and "permitted" in why, why)
    ok, why = refused(lambda: viewer.rpc("soft_delete_safety_rule", {"p_rule_id": ruleA}).execute())
    check("cannot call soft_delete_safety_rule()", ok, why)
    ok, why = refused(lambda: viewer.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_conf_a, "title": "x"}).execute())
    check("cannot open a corrective action, even on a CONFIRMED decision (the RLS policy, not the trigger)",
          ok and "row-level security" in why, why)
    check("...and the decision it tried to touch is unchanged",
          svc.table("safety_decisions").select("status,reviewState").eq("id", d_flag).execute().data[0]
          == {"status": "POTENTIAL_VIOLATION", "reviewState": "PENDING"})

    # ── MANAGER: rules ───────────────────────────────────────────────────
    print()
    print("  MANAGER")
    ok, data = allowed(lambda: new_rule(manager, name="Manager rule", cameraId=camA))
    check("inserts a rule", ok, str(data)[:120] if not ok else "")
    rule2 = data[0]["id"] if ok else None
    ok2, data2 = allowed(lambda: new_rule(manager, name="Forged owner", createdById=admin_id))
    check("a rule's creator is the signed-in user, whatever the request says",
          ok2 and data2[0]["createdById"] == manager_id, str(data2)[:100])
    ok, data = allowed(lambda: manager.table("safety_rules").update({"name": "Renamed"}).eq("id", rule2).execute())
    check("updates a rule", ok and data and data[0]["name"] == "Renamed", str(data)[:100])
    check("the database stamps updatedAt on that update (not the client)",
          svc.table("safety_rules").select("createdAt,updatedAt").eq("id", rule2).execute().data[0]["updatedAt"]
          > svc.table("safety_rules").select("createdAt").eq("id", rule2).execute().data[0]["createdAt"])
    ok, why = refused(lambda: manager.table("safety_rules").update({"deletedAt": ago(0)}, returning=ReturnMethod.minimal).eq("id", rule2).execute())
    check("a direct UPDATE of deletedAt is refused even without RETURNING (no column privilege)", ok and "permission denied" in why, why)
    ok, why = refused(lambda: manager.table("safety_rules").update({"createdById": admin_id}, returning=ReturnMethod.minimal).eq("id", rule2).execute())
    check("...and attribution columns cannot be rewritten", ok and "permission denied" in why, why)
    ok, why = refused(lambda: manager.table("safety_rules").update({"orgId": orgB}, returning=ReturnMethod.minimal).eq("id", rule2).execute())
    check("...nor can a rule be moved to another organisation", ok and "permission denied" in why, why)
    check("...and the rule is still there",
          svc.table("safety_rules").select("deletedAt").eq("id", rule2).execute().data[0]["deletedAt"] is None)
    before = audit_count(orgA, "safety.rule_deleted")
    ok, data = allowed(lambda: manager.rpc("soft_delete_safety_rule", {"p_rule_id": rule2}).execute())
    check("soft-deletes it through soft_delete_safety_rule()", ok, str(data)[:100])
    row = svc.table("safety_rules").select("deletedAt,isEnabled").eq("id", rule2).execute().data[0]
    check("...which stamps deletedAt, disables it, and writes an audit row",
          row["deletedAt"] is not None and row["isEnabled"] is False and audit_count(orgA, "safety.rule_deleted") == before + 1)
    check("...and the rule is no longer visible to the organisation",
          rule2 not in [r["id"] for r in viewer.table("safety_rules").select("id").execute().data])
    ok, why = refused(lambda: manager.rpc("soft_delete_safety_rule", {"p_rule_id": rule2}).execute())
    check("soft-deleting it again says not found", ok, why)
    ok, why = refused(lambda: manager.table("safety_rules").delete().eq("id", ruleA).execute())
    check("cannot hard-delete a rule", ok, why)
    ok, why = refused(lambda: manager.table("safety_rules").insert(
        {"orgId": orgB, "name": "Cross org", "type": "MAN_DOWN", "params": {}}).execute())
    check("cannot insert a rule into another organisation (the RLS policy: no zone or camera involved)",
          ok and "row-level security" in why, why)
    ok, why = refused(lambda: new_rule(manager, zoneId=zB))
    check("cannot anchor a rule of this organisation to another organisation's zone", ok and "zone" in why, why)
    ok, why = refused(lambda: new_rule(manager, cameraId=camB))
    check("cannot name another organisation's camera", ok and "camera" in why, why)
    ok, why = refused(lambda: new_rule(manager, cameraId=camA, zoneId=zA2))
    check("cannot pair a camera with a zone that is on a different camera", ok, why)
    ok, why = refused(lambda: new_rule(manager, type="MAN_DOWN_X"))
    check("an unknown rule type is refused by the database", ok, why)
    ok, why = refused(lambda: new_rule(manager, zoneId=None))
    check("a zone-less rule is refused unless it is MAN_DOWN", ok, why)
    ok, data = allowed(lambda: manager.table("safety_rules").insert(
        {"orgId": orgA, "name": "Whole camera", "type": "MAN_DOWN", "params": {}, "cameraId": camA}).execute())
    check("a camera-wide MAN_DOWN rule needs no zone", ok, str(data)[:100] if not ok else "")

    # ── MANAGER: measured data ───────────────────────────────────────────
    ok, why = refused(lambda: manager.table("safety_decisions").update({"status": "COMPLIANT", "reviewState": "NOT_REQUIRED"}).eq("id", d_flag).execute())
    check("a direct UPDATE of a decision is refused (no policy exists)", ok, why)
    ok, why = refused(lambda: manager.table("safety_decisions").update({"reviewState": "CONFIRMED"}).eq("id", d_flag).execute())
    check("...even of just the review column", ok, why)
    ok, why = refused(lambda: manager.table("safety_decisions").delete().eq("id", d_flag).execute())
    check("a decision cannot be deleted by a browser session", ok, why)
    ok, why = refused(lambda: manager.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgA, "cameraId": camA, "workerKey": "trk-9", "eventType": "ENTERED_ZONE",
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z"}).execute())
    check("an event cannot be written by a browser session", ok, why)
    check("none of that changed the stored decision",
          svc.table("safety_decisions").select("status,reviewState,reviewedAt").eq("id", d_flag).execute().data[0]
          == {"status": "POTENTIAL_VIOLATION", "reviewState": "PENDING", "reviewedAt": None})

    # ── MANAGER: review ──────────────────────────────────────────────────
    print()
    print("  Review (MANAGER)")
    before = audit_count(orgA, "safety.decision_reviewed")
    ok, data = allowed(lambda: manager.rpc("review_safety_decision", {
        "p_decision_id": d_flag, "p_state": "CONFIRMED", "p_reason": "CORRECT", "p_note": "Seen on the replay."}).execute())
    check("confirms a potential violation", ok and data and data["reviewState"] == "CONFIRMED", str(data)[:120])
    row = svc.table("safety_decisions").select("*").eq("id", d_flag).execute().data[0]
    check("...stamping the reviewer, the time, the reason and the note",
          row["reviewedById"] == manager_id and row["reviewedAt"] and row["reviewReasonCode"] == "CORRECT"
          and row["reviewNote"] == "Seen on the replay.")
    check("...and changing nothing that was measured",
          row["status"] == "POTENTIAL_VIOLATION" and row["confidence"] == 0.97 and row["evidence"] == {"items": {}}
          and row["reason"].startswith("Entered Press line"))
    check("...and writes exactly one audit row", audit_count(orgA, "safety.decision_reviewed") == before + 1)
    ok, data = allowed(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_conf_a, "p_state": "DISMISSED", "p_reason": "OTHER"}).execute())
    meta = [r["metadata"] for r in svc.table("audit_logs").select("metadata").eq("orgId", orgA).eq("action", "safety.decision_reviewed").execute().data
            if r["metadata"].get("reason") == "OTHER"]
    check("a re-review is allowed and its audit row records the previous state and reviewer",
          ok and len(meta) == 1 and meta[0]["previousState"] == "CONFIRMED", str(meta))
    ok, data = allowed(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_unv, "p_state": "DISMISSED"}).execute())
    check("an UNVERIFIABLE decision can be reviewed too (that is how abstentions are measured)", ok, str(data)[:100])
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_ok, "p_state": "CONFIRMED"}).execute())
    check("a COMPLIANT decision cannot be reviewed", ok and "compliant" in why.lower(), why)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "MAYBE"}).execute())
    check("a review state other than CONFIRMED / DISMISSED is refused", ok, why)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_foreign, "p_state": "CONFIRMED"}).execute())
    check("another organisation's decision cannot be reviewed", ok and "permitted" in why, why)
    ok, why2 = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": uid(), "p_state": "CONFIRMED"}).execute())
    check("...and a decision that does not exist gets the same answer, so ids cannot be probed",
          ok and "permitted" in why2, why2)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {
        "p_decision_id": d_flag2, "p_state": "CONFIRMED", "p_reason": "x" * 40}).execute())
    check("an over-long reason code is refused, not truncated", ok, why)

    # ── MANAGER: corrective actions ──────────────────────────────────────
    print()
    print("  Corrective actions")
    ok, why = refused(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_flag2, "title": "Re-brief the shift"}).execute())
    check("cannot be opened on a decision that is still PENDING", ok and "confirmed" in why, why)
    ok, why = refused(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_unv, "title": "x"}).execute())
    check("...nor on a DISMISSED one", ok, why)
    ok, why = refused(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_ok, "title": "x"}).execute())
    check("...nor on a COMPLIANT one", ok, why)
    ok, why = refused(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_conf_b, "title": "x"}).execute())
    check("cannot be attached to another organisation's CONFIRMED decision (the same-organisation trigger)", ok and "same organisation" in why, why)
    ok, why = refused(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgB, "decisionId": d_conf_b, "title": "x"}).execute())
    check("...nor created in another organisation at all (the RLS policy)", ok and "row-level security" in why, why)
    ok, data = allowed(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_flag, "title": "Re-brief the shift", "priority": "WARNING",
         "createdById": admin_id, "closedById": admin_id}).execute())
    check("is opened on a CONFIRMED decision of the same organisation", ok, str(data)[:140] if not ok else "")
    action = data[0]["id"] if ok else None
    check("...recording the signed-in user as creator (a forged createdById is overwritten)",
          ok and data[0]["createdById"] == manager_id, str(data)[:100])
    ok, why = refused(lambda: manager.table("corrective_actions").update({"assignedToId": other_id}, returning=ReturnMethod.minimal).eq("id", action).execute())
    check("cannot be assigned to someone outside the organisation", ok, why)
    ok, why = refused(lambda: manager.table("corrective_actions").update({"decisionId": d_conf_a}, returning=ReturnMethod.minimal).eq("id", action).execute())
    check("cannot be re-pointed at another decision", ok and "permission denied" in why, why)
    # closing and reopening (026): the database stamps who and when, a client cannot choose
    ok, data = allowed(lambda: manager.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": d_flag, "title": "Second", "closedById": admin_id, "closedAt": "2001-01-01T00:00:00Z"}).execute())
    action2 = data[0]["id"] if ok else None
    check("a new action ignores a forged closedById / closedAt (it is open, so both are empty)",
          ok and data[0]["closedById"] is None and data[0]["closedAt"] is None, str(data)[:120])
    ok, data = allowed(lambda: manager.table("corrective_actions").update({"status": "CLOSED", "closedAt": "2001-01-01T00:00:00Z"}).eq("id", action2).execute())
    check("closing stamps the closer and the time itself (a backdated closedAt is replaced)",
          ok and data[0]["closedById"] == manager_id and data[0]["closedAt"] and data[0]["closedAt"][:4] != "2001", str(data)[:120])
    ok, data = allowed(lambda: manager.table("corrective_actions").update({"status": "OPEN"}).eq("id", action2).execute())
    check("reopening clears the old closure (closer, time)", ok and data[0]["closedById"] is None and data[0]["closedAt"] is None, str(data)[:120])
    ok, data = allowed(lambda: manager.table("corrective_actions").update({"status": "CANCELLED"}).eq("id", action2).execute())
    check("...and the second action ends cancelled", ok and data[0]["status"] == "CANCELLED")
    ok, data = allowed(lambda: manager.table("corrective_actions").update(
        {"status": "IN_PROGRESS", "assignedToId": admin_id}).eq("id", action).execute())
    check("can be assigned and moved on", ok and data and data[0]["status"] == "IN_PROGRESS", str(data)[:100])
    ok, why = refused(lambda: manager.table("corrective_actions").delete().eq("id", action).execute())
    check("cannot be deleted (cancel instead)", ok, why)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag, "p_state": "DISMISSED"}).execute())
    check("its decision cannot be flipped to DISMISSED while the action is open", ok and "corrective" in why, why)
    ok, data = allowed(lambda: manager.table("corrective_actions").update(
        {"status": "CANCELLED", "closedAt": ago(0)}).eq("id", action).execute())
    check("...but can once the action is cancelled, which records who closed it",
          ok and data and data[0]["closedById"] == manager_id, str(data)[:100])
    ok, data = allowed(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag, "p_state": "DISMISSED"}).execute())
    check("...then the decision can be dismissed", ok and data["reviewState"] == "DISMISSED", str(data)[:100])
    ok, why = refused(lambda: manager.table("corrective_actions").update({"status": "OPEN"}, returning=ReturnMethod.minimal).eq("id", action).execute())
    check("a cancelled action cannot be set back to OPEN once its decision is DISMISSED (the guard cannot be bypassed)", ok and "confirmed" in why, why)
    check("a viewer sees the actions", len(viewer.table("corrective_actions").select("id").execute().data) == 2)

    # ── ADMIN ────────────────────────────────────────────────────────────
    print()
    print("  ADMIN")
    ok, data = allowed(lambda: new_rule(admin, name="Admin rule"))
    check("inserts a rule", ok, str(data)[:100] if not ok else "")
    ok, data = allowed(lambda: admin.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "CONFIRMED"}).execute())
    check("reviews a decision", ok and data["reviewedById"] == admin_id, str(data)[:100])
    ok, why = refused(lambda: admin.table("safety_decisions").update({"status": "COMPLIANT"}).eq("id", d_flag2).execute())
    check("cannot update a decision directly either", ok, why)
    ok, why = refused(lambda: admin.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgA, "cameraId": camA, "workerKey": "t", "eventType": "ENTERED_ZONE",
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z"}).execute())
    check("cannot write an event", ok, why)

    # ── Other organisation ───────────────────────────────────────────────
    print()
    print("  Another organisation")
    for table in ("safety_rules", "safety_decisions", "safety_events", "corrective_actions"):
        rows = other.table(table).select("id,orgId").execute().data
        check(f"sees none of organisation A's {table}", all(r["orgId"] == orgB for r in rows),
              f"{len(rows)} row(s) visible, all its own")
    check("sees only its own decisions",
          sorted(r["id"] for r in other.table("safety_decisions").select("id").execute().data) == sorted([d_foreign, d_conf_b]))
    ok, why = refused(lambda: other.table("safety_rules").update({"name": "x"}).eq("id", ruleA).execute())
    check("cannot update organisation A's rule (and it is unchanged)",
          ok and svc.table("safety_rules").select("name").eq("id", ruleA).execute().data[0]["name"] == "Helmet and vest", why)
    ok, why = refused(lambda: other.rpc("soft_delete_safety_rule", {"p_rule_id": ruleA}).execute())
    check("cannot soft-delete organisation A's rule", ok, why)
    ok, why = refused(lambda: other.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "CONFIRMED"}).execute())
    check("cannot review organisation A's decision", ok, why)
    check("organisation B's own decision is still unreviewed",
          svc.table("safety_decisions").select("reviewState").eq("id", d_foreign).execute().data[0]["reviewState"] == "PENDING")

    # ── Hardening (026) ──────────────────────────────────────────────────
    print()
    print("  Hardening (026)")
    forged = lambda c, action_name, org=orgA: c.table("audit_logs").insert(
        {"orgId": org, "actorId": viewer_id if c is viewer else manager_id, "action": action_name, "targetType": "safety_decision",
         "targetId": d_flag, "metadata": {}}, returning=ReturnMethod.minimal).execute()
    for who, c in (("a VIEWER", viewer), ("a MANAGER", manager)):
        for act in ("safety.decision_reviewed", "safety.evidence_viewed", "safety.rule_deleted"):
            ok, why = refused(lambda c=c, act=act: forged(c, act))
            check(f"{who} cannot write a '{act}' audit row", ok, why)
    ok, data = allowed(lambda: forged(viewer, "zone.created"))
    check("...while an ordinary audit row from a member still works (the policy is narrow)", ok, str(data)[:100])
    ok, data = allowed(lambda: manager.table("safety_rules").insert(
        {"orgId": orgA, "zoneId": zA, "name": "Born deleted?", "type": "PPE_ENTRY", "params": {}, "deletedAt": ago(1),
         "createdAt": "2001-01-01T00:00:00Z", "createdById": admin_id}, returning=ReturnMethod.representation).execute())
    visible = [r for r in manager.table("safety_rules").select("id,createdAt,deletedAt,createdById").eq("name", "Born deleted?").execute().data]
    check("a rule inserted with deletedAt / createdAt / createdById is born alive, dated now, and owned by the signer",
          visible and visible[0]["deletedAt"] is None and visible[0]["createdAt"][:4] != "2001" and visible[0]["createdById"] == manager_id, str(visible)[:140])
    ok, why = refused(lambda: new_rule(manager, name="   "))
    check("a blank rule name is refused", ok and "name_present" in why, why)
    ok, why = refused(lambda: new_rule(manager, params={"x": "y" * 9000}))
    check("an oversized params object is refused", ok and "params_small" in why, why)
    ok, why = refused(lambda: manager.table("corrective_actions").insert({"orgId": orgA, "decisionId": d_conf_a, "title": "  "}).execute())
    check("a blank action title is refused (before or after the decision check)", ok, why)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "CONFIRMED", "p_reason": "because"}).execute())
    check("a review reason outside the offered list is refused", ok and "unknown review reason" in why, why)
    ok, data = allowed(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "CONFIRMED", "p_reason": "PPE_PRESENT"}).execute())
    check("...and one from the list is accepted", ok, str(data)[:100])

    # a soft-deleted organisation loses access, and gets it back when restored
    svc.table("organisations").update({"deletedAt": ago(0)}).eq("id", orgA).execute()
    check("in a soft-deleted organisation a manager sees no decisions, rules or events",
          not manager.table("safety_decisions").select("id").execute().data and not manager.table("safety_rules").select("id").execute().data
          and not manager.table("safety_events").select("id").execute().data)
    ok, why = refused(lambda: new_rule(manager))
    check("...cannot add a rule", ok, why)
    ok, why = refused(lambda: manager.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "DISMISSED"}).execute())
    check("...cannot review", ok and "permitted" in why, why)
    ok, why = refused(lambda: manager.rpc("soft_delete_safety_rule", {"p_rule_id": ruleA}).execute())
    check("...cannot delete a rule", ok, why)
    svc.table("organisations").update({"deletedAt": None}).eq("id", orgA).execute()
    check("restoring the organisation restores access", len(manager.table("safety_decisions").select("id").execute().data) >= 4)

    # ── Anonymous ────────────────────────────────────────────────────────
    print()
    print("  Anonymous")
    for table in ("safety_rules", "safety_decisions", "safety_events", "corrective_actions"):
        ok, why = refused(lambda t=table: anon.table(t).select("id").execute())
        check(f"has no access to {table}", ok, why)
    ok, why = refused(lambda: anon.rpc("review_safety_decision", {"p_decision_id": d_flag2, "p_state": "CONFIRMED"}).execute())
    check("cannot call review_safety_decision()", ok, why)
    ok, why = refused(lambda: anon.rpc("purge_safety_data", {}).execute())
    check("cannot call purge_safety_data()", ok, why)
    ok, why = refused(lambda: manager.rpc("purge_safety_data", {}).execute())
    check("nor can a signed-in manager (it is for the scheduler only)", ok, why)

    # ── Service role (the pipeline) ──────────────────────────────────────
    print()
    print("  Service role (the pipeline)")
    ok, data = allowed(lambda: svc.table("safety_events").insert(
        {"eventUid": "dup-" + TAG, "orgId": orgA, "cameraId": camA, "workerKey": "trk-1", "eventType": "EXITED_ZONE",
         "videoTimeS": 9, "observedAt": "2026-11-09T10:00:09Z"}).execute())
    check("inserts an event", ok, str(data)[:100] if not ok else "")
    ok, why = refused(lambda: svc.table("safety_events").insert(
        {"eventUid": "dup-" + TAG, "orgId": orgA, "cameraId": camA, "workerKey": "trk-1", "eventType": "EXITED_ZONE",
         "videoTimeS": 9, "observedAt": "2026-11-09T10:00:09Z"}).execute())
    check("re-sending the same event is refused by the unique key (sync stays idempotent)", ok, why)
    ok, why = refused(lambda: svc.table("safety_decisions").insert(
        {"id": uid(), "orgId": orgA, "cameraId": camA, "ruleType": "PPE_ENTRY", "severity": "WARNING",
         "workerKey": "t", "status": "UNVERIFIABLE", "reason": "r", "confidence": 0.9, "evidence": {},
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z"}).execute())
    check("even the service role cannot store a confidence on an UNVERIFIABLE decision", ok, why)
    ok, why = refused(lambda: svc.table("safety_decisions").insert(
        {"id": uid(), "orgId": orgA, "cameraId": camA, "ruleType": "PPE_ENTRY", "severity": "WARNING",
         "workerKey": "t", "status": "COMPLIANT", "reason": "r", "confidence": 1, "evidence": {},
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z", "reviewState": "PENDING"}).execute())
    check("...nor put a COMPLIANT decision into the review queue", ok, why)
    ok, why = refused(lambda: svc.table("safety_decisions").insert(
        {"id": uid(), "orgId": orgA, "cameraId": camA, "ruleType": "PPE_ENTRY", "severity": "WARNING",
         "workerKey": "t", "status": "POTENTIAL_VIOLATION", "reason": "r", "confidence": 0.9, "evidence": {},
         "videoTimeS": 1, "observedAt": "2026-11-09T10:00:00Z", "reviewState": "CONFIRMED"}).execute())
    check("...nor a reviewed decision without a review time", ok, why)

    # ── zones.safetyType, retention ──────────────────────────────────────
    print()
    print("  Other changes")
    ok, why = refused(lambda: manager.table("zones").update({"safetyType": "FORBIDDEN"}).eq("id", zA).execute())
    check("zones.safetyType only takes the five known values", ok, why)
    ok, data = allowed(lambda: manager.table("zones").update({"safetyType": "PPE_CHECKPOINT"}).eq("id", zA).execute())
    check("a manager can set a zone's safetyType", ok and data and data[0]["safetyType"] == "PPE_CHECKPOINT", str(data)[:100])
    check("organisations.safetyEvidenceEnabled defaults to on",
          svc.table("organisations").select("safetyEvidenceEnabled").eq("id", orgA).execute().data[0]["safetyEvidenceEnabled"] is True)

    # retention (dates are relative to now so the test does not age): events go after the
    # window, decisions after twice the window, and decisions still awaiting review or
    # with an open action are kept. NB purge_safety_data() is global: it also removes
    # whatever is genuinely expired in other organisations, as the nightly job does.
    svc.table("organisations").update({"dataRetentionDays": 7}).eq("id", orgA).execute()
    old_ev = uid()
    svc.table("safety_events").insert(
        {"eventUid": old_ev, "orgId": orgA, "cameraId": camA, "workerKey": "trk-old", "eventType": "ENTERED_ZONE",
         "videoTimeS": 1, "observedAt": ago(40)}).execute()
    svc.table("safety_events").insert(
        {"eventUid": uid(), "orgId": orgA, "cameraId": camA, "workerKey": "trk-new", "eventType": "ENTERED_ZONE",
         "videoTimeS": 1, "observedAt": ago(3)}).execute()
    gone = decision(orgA, camA, zA, "COMPLIANT", worker="old1", observed=ago(40))
    keep_young = decision(orgA, camA, zA, "COMPLIANT", worker="old2", observed=ago(10))   # inside 2 x 7 days
    keep_pending = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="PENDING", worker="old3", observed=ago(40))
    keep_open = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="CONFIRMED", worker="old4", observed=ago(40))
    svc.table("corrective_actions").insert({"orgId": orgA, "decisionId": keep_open, "title": "Still open"}).execute()
    gone_closed = decision(orgA, camA, zA, "POTENTIAL_VIOLATION", review="CONFIRMED", worker="old5", observed=ago(40))
    svc.table("corrective_actions").insert(
        {"orgId": orgA, "decisionId": gone_closed, "title": "Done", "status": "CLOSED", "closedAt": ago(30)}).execute()
    svc.rpc("purge_safety_data", {}).execute()
    has = lambda t, col, v: bool(svc.table(t).select("id").eq(col, v).execute().data)
    check("purge_safety_data() removes events past the retention window", not has("safety_events", "eventUid", old_ev))
    check("...keeps recent ones", has("safety_events", "workerKey", "trk-new"))
    check("...removes a 40-day-old decision (twice the window is 14 days)", not has("safety_decisions", "id", gone))
    check("...keeps a 10-day-old decision (inside twice the window)", has("safety_decisions", "id", keep_young))
    check("...keeps an old violation that still awaits review", has("safety_decisions", "id", keep_pending))
    check("...keeps an old decision whose corrective action is still open (and the action with it)",
          has("safety_decisions", "id", keep_open) and has("corrective_actions", "decisionId", keep_open))
    check("...removes an old decision whose actions are all closed", not has("safety_decisions", "id", gone_closed))
    check("...and writes an audit row saying how much it removed",
          audit_count(orgA, "safety.events_purged") >= 1 and audit_count(orgA, "safety.decisions_purged") >= 1)

    # a rule cannot be attached to a deleted zone or camera, even by the service role
    svc.table("zones").update({"deletedAt": ago(0)}).eq("id", zA2).execute()
    ok, why = refused(lambda: svc.table("safety_rules").insert(
        {"orgId": orgA, "cameraId": camA2, "zoneId": zA2, "name": "On a deleted zone", "type": "PPE_ENTRY", "params": {}}).execute())
    check("a rule cannot be attached to a deleted zone", ok and "deleted" in why, why)

finally:
    for oid in created_orgs:
        try:
            # an organisation must keep an active admin unless it is soft-deleted (019), so mark it first
            svc.table("organisations").update({"deletedAt": "2026-11-09T00:00:00Z"}).eq("id", oid).execute()
            svc.table("memberships").delete().eq("orgId", oid).execute()   # the guard only looks at live organisations
            svc.table("organisations").delete().eq("id", oid).execute()
        except Exception as e:
            print(f"  cleanup: organisation {oid[:8]}: {str(e)[:100]}")
    for pid in created_users:
        try:
            svc.table("profiles").delete().eq("id", pid).execute()
        except Exception:
            pass
        try:
            svc.auth.admin.delete_user(pid)
        except Exception as e:
            print(f"  cleanup: user {pid[:8]}: {str(e)[:100]}")
    left = svc.table("organisations").select("id").like("slug", f"rls-%-{TAG}").execute().data
    print(f"  cleaned up (organisations left behind: {len(left)})")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
