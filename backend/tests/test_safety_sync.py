"""
Step 8: the SQLite -> Postgres sync, against a stub PostgREST server.

No network and no real database: a small HTTP server plays Supabase (cameras,
zones, rules, and the two safety tables, with the conflict behaviour the real
ones have), and a temporary SQLite file plays the pipeline's local store. What is
checked is the contract: mapping, batching, idempotence, "never overwrite a
review", rows that cannot be mapped, failures that must not lose anything, and
retirement of rows that can never sync.

    venv/Scripts/python.exe backend/tests/test_safety_sync.py
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine, text                      # noqa: E402

from app.db.database import configure_sqlite                    # noqa: E402
from app.db.safety_models import ensure_safety_tables, purge_local   # noqa: E402
from app.db.safety_writer import SafetyWriter                   # noqa: E402
import app.db.safety_sync as sync                               # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_sync_")
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


ORG = str(uuid.uuid4())
CAM_UUID, ZONE_UUID, ZONE2_UUID = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
RULE_UUID = str(uuid.uuid4())


class Stub:
    """The state of the fake Supabase."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.cameras = [{"id": CAM_UUID, "orgId": ORG, "name": "floor5"}]
        self.zones = [{"id": ZONE_UUID, "cameraId": CAM_UUID, "slug": "zone_press"}]
        self.rules = [{"id": RULE_UUID}]
        self.orgs = [{"id": ORG}]
        self.events, self.decisions = {}, {}
        self.posts = []                 # (path, params, prefer, body)
        self.gets = []
        self.fail_post = []             # queue of status codes to answer POSTs with
        self.fail_get = None            # status to answer GETs with
        self.reject_ids = set()         # row ids answered 400 when sent
        self.schema_missing = False     # answer every POST with PostgREST's "table not found"
        self.auth_seen = set()


STUB = Stub()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        STUB.gets.append((u.path, q))
        STUB.auth_seen.add(self.headers.get("apikey"))
        if STUB.fail_get:
            return self._reply(STUB.fail_get, b'{"message":"boom"}')
        table = u.path.rsplit("/", 1)[-1]
        rows = {"cameras": STUB.cameras, "zones": STUB.zones, "safety_rules": STUB.rules, "organisations": STUB.orgs}.get(table, [])
        out = []
        for r in rows:
            ok = True
            for k, v in q.items():
                if k in ("select", "limit"):
                    continue
                if v.startswith("eq.") and str(r.get(k)) != v[3:]:
                    ok = False
                if v.startswith("in.(") and str(r.get(k)) not in v[4:-1].split(","):
                    ok = False
            if ok:
                out.append({"id": r["id"]})
        self._reply(200, json.dumps(out[:int(q.get("limit", 1000))]).encode())

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
        prefer = self.headers.get("Prefer", "")
        STUB.posts.append((u.path.rsplit("/", 1)[-1], q, prefer, body))
        STUB.auth_seen.add(self.headers.get("apikey"))
        if STUB.fail_post:
            code = STUB.fail_post.pop(0)
            if code:
                return self._reply(code, b'{"message":"stub failure"}')
        table = u.path.rsplit("/", 1)[-1]
        if any(str(r.get("id", r.get("eventUid"))) in STUB.reject_ids for r in body):
            return self._reply(400, b'{"code":"23514","message":"violates a constraint"}')
        if STUB.schema_missing:
            return self._reply(404, b'{"code":"PGRST205","message":"Could not find the table in the schema cache"}')
        if table == "safety_events":
            for r in body:                                       # merge-duplicates
                STUB.events.setdefault(r["eventUid"], {}).update(r)
        elif table == "safety_decisions":
            for r in body:
                if "ignore-duplicates" in prefer:                # the real behaviour: keep the stored row
                    STUB.decisions.setdefault(r["id"], dict(r))
                else:
                    STUB.decisions.setdefault(r["id"], {}).update(r)
        self._reply(201)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{server.server_port}"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "service-key-for-the-stub"

_n = [0]


def new_engine():
    _n[0] += 1
    path = os.path.join(TMP, f"t{_n[0]}.sqlite")
    e = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(e, busy_timeout_ms=250)
    ensure_safety_tables(e)
    return e


def iso(days_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="milliseconds")


def fresh():
    STUB.reset()
    sync._rejected.clear()
    sync._negative.clear()
    sync._cursor.clear()
    return new_engine()


def writer(e, camera="floor5", org=ORG, session="sess1"):
    return SafetyWriter(camera, org_id=org, session_id=session, engine=e, retry_interval_s=0.0)


def add_flag(w, zone="zone_press", rule=None, reason="Entered Press line with no helmet seen in 28 of 29 clear frames."):
    return w.add_decision("PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION", reason, 0.97,
                          {"items": {"helmet": {"state": "ABSENT"}}}, 12.5, iso(), zone_id=zone, rule_id=rule,
                          thresholds={"min": 0.6})


def add_ok(w, zone="zone_press"):
    return w.add_decision("PPE_ENTRY", "INFO", "trk-2", "COMPLIANT", "Entered Press line: helmet seen.", 0.95,
                          {"items": {}}, 13.0, iso(), zone_id=zone)


def add_unv(w, zone="zone_press"):
    return w.add_decision("PPE_ENTRY", "INFO", "trk-3", "UNVERIFIABLE", "Could not tell.", 0.0,
                          {"items": {}}, 14.0, iso(), zone_id=zone)


def add_event(w, zone="zone_press", kind="ENTERED_ZONE"):
    return w.add_event(kind, "trk-1", 12.0, iso(), zone_id=zone, details={"first_sighting": False})


def count(e, table, where="1=1"):
    with e.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar()


def run(e, **kw):
    return sync.sync_safety_sync(engine=e, **kw)


print("=" * 66)
print("SAFETY SYNC (stub PostgREST)")
print("=" * 66)

# ── A. mapping and payloads ──────────────────────────────────────────────
print()
print("A. Mapping and payloads")
e = fresh()
w = writer(e)
d_flag, d_ok, d_unv = add_flag(w), add_ok(w), add_unv(w)
ev_zone = add_event(w)
ev_cam = w.add_event("LOST_IN_ZONE", "trk-9", 3.0, iso(), zone_id=None)           # camera-wide, no zone
w.flush_sync()
out = run(e)
check("everything is sent and counted", out["synced_events"] == 2 and out["synced_decisions"] == 3 and "error" not in out, str(out))
check("every row is stamped synced", count(e, "safety_events", "synced_at IS NULL") == 0 and count(e, "safety_decisions", "synced_at IS NULL") == 0)
dec = STUB.decisions
check("a decision arrives with its source uuid, organisation, camera and zone uuids",
      dec[d_flag]["id"] == d_flag and dec[d_flag]["orgId"] == ORG and dec[d_flag]["cameraId"] == CAM_UUID and dec[d_flag]["zoneId"] == ZONE_UUID)
check("...and the measured fields unchanged",
      dec[d_flag]["status"] == "POTENTIAL_VIOLATION" and dec[d_flag]["confidence"] == 0.97 and dec[d_flag]["videoTimeS"] == 12.5
      and dec[d_flag]["reason"].startswith("Entered Press line") and dec[d_flag]["evidence"] == {"items": {"helmet": {"state": "ABSENT"}}}
      and dec[d_flag]["thresholds"] == {"min": 0.6} and dec[d_flag]["ruleType"] == "PPE_ENTRY" and dec[d_flag]["severity"] == "WARNING"
      and dec[d_flag]["sessionKey"] == "sess1" and dec[d_flag]["workerKey"] == "trk-1")
check("a flag starts PENDING review; compliant and unverifiable decisions need none",
      (dec[d_flag]["reviewState"], dec[d_ok]["reviewState"], dec[d_unv]["reviewState"]) == ("PENDING", "NOT_REQUIRED", "NOT_REQUIRED"),
      str([dec[k]["reviewState"] for k in (d_flag, d_ok, d_unv)]))
check("an UNVERIFIABLE decision keeps confidence 0", dec[d_unv]["confidence"] == 0)
check("an event arrives keyed by its eventUid with its details as JSON, not text",
      STUB.events[ev_zone]["eventUid"] == ev_zone and STUB.events[ev_zone]["details"] == {"first_sighting": False}
      and STUB.events[ev_zone]["zoneId"] == ZONE_UUID and STUB.events[ev_zone]["eventType"] == "ENTERED_ZONE")
check("a camera-wide row (no zone) is sent with zoneId null, not dropped", STUB.events[ev_cam]["zoneId"] is None and STUB.events[ev_cam]["cameraId"] == CAM_UUID)
posts = {p[0]: p for p in STUB.posts}
check("events: conflict target eventUid with merge-duplicates",
      posts["safety_events"][1] == {"on_conflict": "eventUid"} and "merge-duplicates" in posts["safety_events"][2], str(posts["safety_events"][1:3]))
check("decisions: conflict target id with IGNORE-duplicates (a re-send can never overwrite a review)",
      posts["safety_decisions"][1] == {"on_conflict": "id"} and "ignore-duplicates" in posts["safety_decisions"][2], str(posts["safety_decisions"][1:3]))
check("the service key is sent", STUB.auth_seen == {"service-key-for-the-stub"})
check("every payload row has the same keys (PostgREST requires it for a bulk insert)",
      all(len({tuple(sorted(r)) for r in p[3]}) == 1 for p in STUB.posts))
check("the camera and zone are looked up once per tick, not once per row",
      sum(1 for g in STUB.gets if g[0].endswith("/cameras")) == 1, str([g[0] for g in STUB.gets]))

# ── B. idempotence and reviews ───────────────────────────────────────────
print()
print("B. Running it again")
n = len(STUB.posts)
out = run(e)
check("a second run sends nothing", len(STUB.posts) == n and out["synced_events"] == 0 and out["synced_decisions"] == 0, str(out))
STUB.decisions[d_flag]["reviewState"] = "CONFIRMED"               # a person reviewed it in Postgres
STUB.decisions[d_flag]["reviewNote"] = "seen on replay"
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET synced_at = NULL"))
    c.execute(text("UPDATE safety_events SET synced_at = NULL"))
out = run(e)
check("forcing a full re-send creates no duplicates",
      len(STUB.decisions) == 3 and len(STUB.events) == 2, f"{len(STUB.decisions)} decisions, {len(STUB.events)} events")
check("...and the human review survives it (ignore-duplicates)",
      STUB.decisions[d_flag]["reviewState"] == "CONFIRMED" and STUB.decisions[d_flag].get("reviewNote") == "seen on replay")

# ── C. batching ──────────────────────────────────────────────────────────
print()
print("C. Batching")
e = fresh()
w = writer(e)
for i in range(620):
    w.add_event("ENTERED_ZONE", f"trk-{i}", 1.0, iso(), zone_id="zone_press")
w.flush_sync()
check("all 620 events were stored locally", count(e, "safety_events") == 620)
out = run(e)
check("one pass sends at most 500, oldest first", out["synced_events"] == 500 and count(e, "safety_events", "synced_at IS NULL") == 120, str(out))
out = run(e)
check("the next pass sends the rest", out["synced_events"] == 120 and count(e, "safety_events", "synced_at IS NULL") == 0)
check("no event was sent twice", len(STUB.events) == 620 and sum(len(p[3]) for p in STUB.posts if p[0] == "safety_events") == 620)

# ── D. rows that cannot be mapped ────────────────────────────────────────
print()
print("D. Unmappable rows")
e = fresh()
w = writer(e)
a = add_flag(w)                                          # maps
b = add_flag(w, zone="zone_unknown")                     # zone not in Postgres
w2 = writer(e, camera="upload_ab12")                     # ad-hoc camera, no Postgres row
c = add_flag(w2)
d_other = writer(e, org=str(uuid.uuid4()))               # another organisation: its camera is not in this stub
f = add_flag(d_other)
for x in (w, w2, d_other):
    x.flush_sync()
out = run(e)
check("only the mappable decision is sent", list(STUB.decisions) == [a], str(list(STUB.decisions)))
check("the others stay unsynced and are counted by camera/zone",
      out["unmapped"] == 3 and out["unmapped_pairs"] == {"floor5/zone_unknown": 1, "upload_ab12/zone_press": 1, "floor5/zone_press": 1}
      and count(e, "safety_decisions", "synced_at IS NULL") == 3, str(out))
check("nothing is invented: no camera or zone was created", len(STUB.cameras) == 1 and len(STUB.zones) == 1)
STUB.zones.append({"id": ZONE2_UUID, "cameraId": CAM_UUID, "slug": "zone_unknown"})
out = run(e)
check("\"no such zone\" is believed for a minute, so a run of unmappable rows costs one request each, not one per row",
      out["synced_decisions"] == 0)
sync._negative.clear()
out = run(e)
check("a zone drawn after the fact releases its rows on the next pass", out["synced_decisions"] == 1 and b in STUB.decisions and STUB.decisions[b]["zoneId"] == ZONE2_UUID, str(out))

# ── E. failures keep the rows ────────────────────────────────────────────
print()
print("E. Failures lose nothing")
for code, label in ((500, "a 500"), (503, "a 503"), (401, "a 401 (bad key)"), (429, "a 429")):
    e = fresh()
    w = writer(e)
    add_flag(w); add_event(w); w.flush_sync()
    STUB.fail_post = [code, code]
    out = run(e)
    check(f"{label} leaves every row unsynced, retires nothing, and does not raise",
          count(e, "safety_decisions", "synced_at IS NULL") == 1 and count(e, "safety_events", "synced_at IS NULL") == 1
          and "error" in out and out["retired"] == 0, str(out))
    STUB.fail_post = []
    out = run(e)
    check("...and the next pass sends them", out["synced_events"] == 1 and out["synced_decisions"] == 1 and "error" not in out, str(out))
e = fresh()
w = writer(e); add_flag(w); w.flush_sync()
STUB.fail_get = 500
out = run(e)
check("a failed LOOKUP stops the batch: not counted as unmapped, nothing sent", out["unmapped"] == 0 and not STUB.posts and "error" in out, str(out))
check("...and an old row is not retired just because Postgres was unreachable", out["retired"] == 0)
STUB.fail_get = None
server_port = server.server_port
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"                 # nothing listens: a connection error
out = run(e)
check("an unreachable server is survived", count(e, "safety_decisions", "synced_at IS NULL") == 1 and "error" in out, str(out))
os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{server_port}"
check("...and recovers", run(e)["synced_decisions"] == 1)

# ── F. a rejected row does not hold back its neighbours ──────────────────
print()
print("F. One bad row")
e = fresh()
w = writer(e)
good1, bad, good2 = add_flag(w), add_flag(w), add_ok(w)
w.flush_sync()
STUB.reject_ids = {bad}
out = run(e)
check("the good rows are sent and stamped", {good1, good2} <= set(STUB.decisions) and bad not in STUB.decisions and out["synced_decisions"] == 2, str(out))
check("the rejected row stays unsynced and is counted", out["rejected"] == 1 and count(e, "safety_decisions", "synced_at IS NULL") == 1)
n = len(STUB.posts)
run(e)
check("it is not re-sent on every tick (cooldown)", len(STUB.posts) == n)

# ── G. unknown rule ids ──────────────────────────────────────────────────
print()
print("G. Rules")
e = fresh()
w = writer(e)
known, unknown, default = add_flag(w, rule=RULE_UUID), add_flag(w, rule=str(uuid.uuid4())), add_flag(w)
w.flush_sync()
out = run(e)
check("a known rule id is sent", STUB.decisions[known]["ruleId"] == RULE_UUID)
check("an unknown rule id is sent as null (the column is a nullable foreign key) and counted",
      STUB.decisions[unknown]["ruleId"] is None and out["rule_missing"] == 1)
check("the built-in default rule is null", STUB.decisions[default]["ruleId"] is None)

# ── H. retirement ────────────────────────────────────────────────────────
print()
print("H. Rows that can never sync are retired, then housekeeping deletes them")
e = fresh()
w = writer(e)
old_unmapped = add_flag(w, zone="zone_unknown")
young_unmapped = add_flag(w, zone="zone_unknown")
w.flush_sync()
wn = writer(e, org="test-org")                           # not a uuid: can never sync
non_uuid = add_flag(wn)
wn.flush_sync()
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET created_at = :t WHERE id = :a"), {"t": iso(31), "a": old_unmapped})
    c.execute(text("UPDATE safety_decisions SET created_at = :t WHERE id = :a"), {"t": iso(8), "a": non_uuid})
out = run(e)
rows = {r[0]: r[1] for r in e.connect().execute(text("SELECT id, synced_at FROM safety_decisions"))}
check("an unmappable row older than 30 days (in an organisation Postgres confirms exists) is retired", rows[old_unmapped] is not None)
check("an unmappable row that is still young is kept for a late zone or camera", rows[young_unmapped] is None)
check("a row whose organisation is not a uuid is retired after the retention days", rows[non_uuid] is not None)
check("retired rows reach housekeeping: purge_local removes them after the synced window",
      purge_local(e, synced_days=1, now=datetime.now(timezone.utc) + timedelta(days=2))["safety_decisions"] == 2)
e = fresh()
w = writer(e)
flag = add_flag(w); w.flush_sync()
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET created_at = :t"), {"t": iso(30)})
STUB.fail_post = [500]
out = run(e)
check("an OLD row that merely could not be sent (server error) is never retired", count(e, "safety_decisions", "synced_at IS NULL") == 1 and out["retired"] == 0)

# ── I. text limits, standalone, locking ──────────────────────────────────
print()
print("I. Edges")
e = fresh()
w = writer(e)
long_id = add_flag(w, reason="x" * 700)
w.flush_sync()
run(e)
check("a reason longer than the 500-character column is cut, not refused", len(STUB.decisions[long_id]["reason"]) == 500 and long_id in STUB.decisions)
key = os.environ.pop("SUPABASE_SERVICE_ROLE_KEY")
e = fresh()
w = writer(e); add_flag(w); w.flush_sync()
out = run(e)
check("without a service key nothing is sent or changed", out.get("reason") == "no service-role key configured" and not STUB.posts
      and count(e, "safety_decisions", "synced_at IS NULL") == 1)
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = key
e = fresh()
w = writer(e); add_flag(w); w.flush_sync()
sync._run_lock.acquire()
try:
    out = run(e)
finally:
    sync._run_lock.release()
check("a pass that starts while another is running does nothing", out.get("reason") == "already running" and not STUB.posts)
check("unsynced_counts reports what is waiting", sync.unsynced_counts(e) == {"safety_events": 0, "safety_decisions": 1}, str(sync.unsynced_counts(e)))

import asyncio                                                      # noqa: E402
out = asyncio.run(sync.sync_safety(engine=e))
check("the async wrapper runs the same sync off the event loop", out["synced_decisions"] == 1, str(out))


# ── J. review findings ───────────────────────────────────────────────────
print()
print("J. Starvation, wrong key, schema errors, bad ids")

# a pile of unmappable rows must not hold back a mappable one behind it
e = fresh()
w = writer(e, camera="upload_pile")
for i in range(1300):
    w.add_event("ENTERED_ZONE", f"trk-{i}", 1.0, iso(2), zone_id="zone_press")
w.flush_sync()
w_good = writer(e)
good_ev = add_event(w_good)
w_good.flush_sync()
passes = 0
while good_ev not in STUB.events and passes < 6:
    run(e)
    sync._negative.clear()
    passes += 1
check("a mappable event behind 1,300 unmappable ones is still reached within a few passes", good_ev in STUB.events, f"after {passes} passes")
check("...and the unmappable pile was not retired meanwhile", count(e, "safety_events", "synced_at IS NULL") == 1300)

# a key that sees nothing (an anon key, say) must never cause a deletion
e = fresh()
STUB.orgs = []                                              # Postgres "shows" no organisation to this key
w = writer(e, camera="upload_wrong_key"); add_flag(w); w.flush_sync()
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET created_at = :t"), {"t": iso(60)})
out = run(e)
check("rows are not retired for an organisation Postgres does not confirm (a non-service key sees no rows at all)",
      out["retired"] == 0 and count(e, "safety_decisions", "synced_at IS NULL") == 1, str(out))

# a half-applied migration (table not found) is a deployment problem, not a bad row
e = fresh()
w = writer(e); add_flag(w); w.flush_sync()
with e.begin() as c:
    c.execute(text("UPDATE safety_decisions SET created_at = :t"), {"t": iso(60)})
STUB.schema_missing = True
out = run(e)
check("a 404 'table not in the schema cache' leaves the row alone: no per-row retries, no cooldown, no retirement",
      "error" in out and out["rejected"] == 0 and out["retired"] == 0 and len(STUB.posts) == 1
      and count(e, "safety_decisions", "synced_at IS NULL") == 1 and not sync._rejected, str(out))
STUB.schema_missing = False
check("...and it is sent once the schema is there", run(e)["synced_decisions"] == 1)

# an organisation id that looks like a uuid but is not one
e = fresh()
w = writer(e, org="zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"); add_flag(w); w.flush_sync()
wg = writer(e); add_ok(wg); wg.flush_sync()
out = run(e)
check("a 36-character non-hex organisation id does not pause the pass or reach Postgres; the good row still syncs",
      "error" not in out and out["invalid"] == 1 and out["synced_decisions"] == 1 and not any(g[1].get("orgId") == "eq.zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz" for g in STUB.gets), str(out))

# names that need encoding
cam_id = str(uuid.uuid4())
e = fresh()
STUB.cameras.append({"id": cam_id, "orgId": ORG, "name": "Dock (north), bay 2 \u00e9"})
w = writer(e, camera="Dock (north), bay 2 \u00e9"); d = add_flag(w, zone=None); w.flush_sync()
run(e)
check("a camera name with commas, brackets, spaces and a non-ASCII letter is looked up correctly", d in STUB.decisions and STUB.decisions[d]["cameraId"] == cam_id)

# the cursor wraps: rows skipped on one lap are looked at again on the next
e = fresh()
w = writer(e, camera="upload_x"); a = add_flag(w, zone=None); w.flush_sync()
run(e)
STUB.cameras.append({"id": str(uuid.uuid4()), "orgId": ORG, "name": "upload_x"})
sync._negative.clear()
check("an unmappable row is tried again on the next lap once its camera exists", run(e)["synced_decisions"] == 1 and a in STUB.decisions)

# a background task is kept alive and finishes
e2 = fresh()
w = writer(e2); add_flag(w); w.flush_sync()
import asyncio                                                    # noqa: E402


async def background_pass():
    sync.sync_safety_in_background()
    held = len(sync._background)
    while sync._background:
        await asyncio.sleep(0.05)
    return held


saved_engine, sync._default_engine = sync._default_engine, e2
try:
    held = asyncio.run(background_pass())
finally:
    sync._default_engine = saved_engine
check("a fire-and-forget pass is held in a set while it runs, released when done, and does its work",
      held == 1 and not sync._background and count(e2, "safety_decisions", "synced_at IS NULL") == 0)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f_ in FAILS:
        print("  -", f_)
    sys.exit(1)
print("ALL PASS")
