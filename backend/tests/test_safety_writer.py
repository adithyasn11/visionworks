"""
The safety layer's local database and its writer (Step 3).

What has to be true:
  * the tables exist, are created idempotently, and REFUSE nonsense (unknown
    vocabulary, confidence outside 0..1, an UNVERIFIABLE with a confidence);
  * re-writing a row is harmless (ids are made at the source);
  * a database problem never reaches the video loop, and a DECISION is never
    lost to a busy database: it is kept and written on a later flush;
  * one refused row cannot hold up the good rows behind it;
  * several writers can share the file (WAL + busy timeout);
  * housekeeping deletes what it should and never an unsynced row by default.

Almost everything runs against a throwaway SQLite file with the same engine
settings as the app. The last section starts the real app and checks the real
file.

Run:  venv/Scripts/python.exe backend/tests/test_safety_writer.py
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

import numpy as np                                                       # noqa: E402
from sqlalchemy import create_engine                                    # noqa: E402

from app.db.database import configure_sqlite                             # noqa: E402
from app.db.safety_models import (                                       # noqa: E402
    DECISION_STATUSES, EVENT_TYPES, RULE_TYPES, SEVERITIES,
    ddl_statements, ensure_safety_tables, purge_local,
)
from app.db.safety_writer import SafetyWriter, persist_safety            # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="vw_safety_writer_")
NOW = "2026-11-08T04:00:00.000+00:00"


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


_n = [0]


def new_db(busy_ms=250):
    """A fresh database file with the app's engine settings; returns (engine, path)."""
    _n[0] += 1
    path = os.path.join(TMP, f"db{_n[0]}.sqlite")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite(engine, busy_timeout_ms=busy_ms)
    return engine, path


def count(path, table, where=""):
    c = sqlite3.connect(path)
    try:
        return c.execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0]
    finally:
        c.close()


def raw(path, sql, params=()):
    c = sqlite3.connect(path)
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.fetchall()
    finally:
        c.close()


def writer(engine, **kw):
    return SafetyWriter("gate_1", org_id="org-1", session_id="sess-1", engine=engine, **kw)


def good_decision(w, **kw):
    args = dict(rule_type="PPE_ENTRY", severity="WARNING", worker_key="trk-7",
                status="POTENTIAL_VIOLATION", reason="no helmet in 27 of 29 clear frames",
                confidence=0.93, evidence={"helmet": {"state": "ABSENT"}}, video_time_s=12.4,
                observed_at=NOW)
    args.update(kw)
    return w.add_decision(**args)


def good_event(w, **kw):
    args = dict(event_type="ENTERED_ZONE", worker_key="trk-7", video_time_s=12.4, observed_at=NOW)
    args.update(kw)
    return w.add_event(**args)


# ── 1. Tables ────────────────────────────────────────────────────────────────
print("1. Tables")
engine, path = new_db()
ensure_safety_tables(engine)
ensure_safety_tables(engine)
names = {r[0] for r in raw(path, "SELECT name FROM sqlite_master WHERE type='table'")}
check("both tables exist and creating twice is harmless", {"safety_events", "safety_decisions"} <= names)
idx = {r[0]: r[1] for r in raw(path, "SELECT name, sql FROM sqlite_master WHERE type='index' AND name LIKE 'ix_s%'")}
for needed in ("ix_se_org_observed", "ix_se_camera_observed", "ix_se_worker", "ix_se_unsynced",
               "ix_sd_org_observed", "ix_sd_status", "ix_sd_unsynced"):
    check(f"index {needed} exists", needed in idx)
check("the 'unsynced' indexes are partial (only rows still to sync)",
      "synced_at IS NULL" in idx["ix_se_unsynced"] and "synced_at IS NULL" in idx["ix_sd_unsynced"])
sql = {r[0]: r[1] for r in raw(path, "SELECT name, sql FROM sqlite_master WHERE type='table'")}
import re
for table, column, vocab in (("safety_events", "event_type", EVENT_TYPES),
                             ("safety_decisions", "status", DECISION_STATUSES),
                             ("safety_decisions", "rule_type", RULE_TYPES),
                             ("safety_decisions", "severity", SEVERITIES)):
    listed = re.findall(rf"{column} IN \(([^)]*)\)", sql[table])
    got = tuple(x.strip().strip("'") for x in listed[0].split(",")) if listed else ()
    check(f"{table}.{column} CHECK lists exactly the code's vocabulary", got == tuple(vocab), str(got))
check("the DDL is idempotent text (every CREATE says IF NOT EXISTS)",
      all("IF NOT EXISTS" in s for s in ddl_statements()))

# ── 2. The database itself refuses nonsense ──────────────────────────────────
print("2. Constraints (straight SQL, bypassing the writer)")
def insert_event(**o):
    row = dict(event_uid=str(uuid.uuid4()), camera_id="c", worker_key="trk-1", event_type="ENTERED_ZONE",
               video_time_s=1.0, observed_at=NOW, created_at=NOW)
    row.update(o)
    cols = ",".join(row)
    return raw(path, f"INSERT INTO safety_events ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

def insert_decision(**o):
    row = dict(id=str(uuid.uuid4()), camera_id="c", rule_type="PPE_ENTRY", severity="WARNING",
               worker_key="trk-1", status="POTENTIAL_VIOLATION", reason="r", confidence=0.9,
               evidence="{}", video_time_s=1.0, observed_at=NOW, created_at=NOW)
    row.update(o)
    cols = ",".join(row)
    return raw(path, f"INSERT INTO safety_decisions ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

def refused(fn):
    try:
        fn()
    except sqlite3.IntegrityError:
        return True
    return False

check("a valid event is accepted", insert_event() is not None)
check("a valid decision is accepted", insert_decision() is not None)
check("unknown event_type refused", refused(lambda: insert_event(event_type="TELEPORTED")))
check("negative video_time_s refused (event)", refused(lambda: insert_event(video_time_s=-0.1)))
check("duplicate event_uid refused by raw SQL", refused(lambda: (lambda u: (insert_event(event_uid=u), insert_event(event_uid=u)))(str(uuid.uuid4()))))
check("unknown status refused", refused(lambda: insert_decision(status="GUILTY")))
check("unknown rule_type refused", refused(lambda: insert_decision(rule_type="VIBES")))
check("unknown severity refused", refused(lambda: insert_decision(severity="MEH")))
check("confidence above 1 refused", refused(lambda: insert_decision(confidence=1.01)))
check("confidence below 0 refused", refused(lambda: insert_decision(confidence=-0.01)))
check("an UNVERIFIABLE with a confidence is refused",
      refused(lambda: insert_decision(status="UNVERIFIABLE", confidence=0.4)))
check("an UNVERIFIABLE with confidence 0 is accepted",
      insert_decision(status="UNVERIFIABLE", confidence=0) is not None)
check("negative video_time_s refused (decision)", refused(lambda: insert_decision(video_time_s=-1)))
check("a decision with no evidence refused", refused(lambda: insert_decision(evidence=None)))
check("a decision with no id refused", refused(lambda: insert_decision(id=None)))

# ── 3. The writer validates before queuing ───────────────────────────────────
print("3. Writer validation")
engine, path = new_db()
w = writer(engine)
bad_events = [
    dict(event_type="NOPE"), dict(worker_key=""), dict(worker_key=None), dict(video_time_s=-1),
    dict(video_time_s=float("nan")), dict(video_time_s=True), dict(observed_at="yesterday"),
    dict(observed_at=""), dict(event_uid="not-a-uuid"),
]
for bad in bad_events:
    check(f"event {bad} is refused and nothing is queued", raises(lambda b=bad: good_event(w, **b)) and w.pending_events == 0)
bad_decisions = [
    dict(rule_type="NOPE"), dict(severity="MEH"), dict(status="GUILTY"), dict(confidence=1.5),
    dict(confidence=-0.1), dict(confidence=float("nan")), dict(confidence="high"), dict(reason=" "),
    dict(evidence=None), dict(evidence=[1, 2]), dict(worker_key=""), dict(video_time_s=-3),
    dict(observed_at="soon"), dict(decision_id="x"), dict(rule_id="x"),
    dict(status="UNVERIFIABLE", confidence=0.5),
]
for bad in bad_decisions:
    check(f"decision {bad} is refused and nothing is queued", raises(lambda b=bad: good_decision(w, **b)) and w.pending_decisions == 0)
check("an UNVERIFIABLE with confidence 0 is accepted",
      good_decision(w, status="UNVERIFIABLE", confidence=0, reason="far away") and w.pending_decisions == 1)
w = writer(engine)
check("Z-suffixed timestamps are accepted", bool(good_event(w, observed_at="2026-11-08T04:00:00Z")))
check("ids are UUIDs made at the source", uuid.UUID(good_event(w)) and uuid.UUID(good_decision(w)))
mine = str(uuid.uuid4())
check("a caller-supplied id is used (and normalised)", good_event(w, event_uid=mine.upper()) == mine)

# ── 4. Writing, buffering, idempotence ───────────────────────────────────────
print("4. Writing")
engine, path = new_db()
w = writer(engine)
ids = [good_event(w, video_time_s=float(i)) for i in range(50)]
dec = [good_decision(w, video_time_s=float(i)) for i in range(10)]
check("rows are buffered, not written, until a flush", w.pending_events == 50 and w.pending_decisions == 10)
out = w.flush_sync()
check("one flush writes everything", out == {"events": 50, "decisions": 10}, str(out))
check("the buffer is empty afterwards", w.pending_events == 0 and w.pending_decisions == 0)
check("rows really are in the file", count(path, "safety_events") == 50 and count(path, "safety_decisions") == 10)
check("counters track what was stored", w.events_written == 50 and w.decisions_written == 10)
row = raw(path, "SELECT org_id, camera_id, session_key, synced_at, created_at FROM safety_events LIMIT 1")[0]
check("rows carry org, camera, session; synced_at is empty; created_at is set",
      row[0] == "org-1" and row[1] == "gate_1" and row[2] == "sess-1" and row[3] is None and row[4])
check("an empty flush does nothing and does not raise", w.flush_sync() == {"events": 0, "decisions": 0})

w2 = writer(engine)
same = str(uuid.uuid4())
good_event(w2, event_uid=same); good_event(w2, event_uid=same)
good_decision(w2, decision_id=same); good_decision(w2, decision_id=same)
w2.flush_sync()
w2b = writer(engine)
good_event(w2b, event_uid=same); good_decision(w2b, decision_id=same)
w2b.flush_sync()
check("the same event id written twice, in one flush or across two, is one row",
      count(path, "safety_events", f"WHERE event_uid='{same}'") == 1)
check("the same decision id written twice is one row",
      count(path, "safety_decisions", f"WHERE id='{same}'") == 1)
check("...and the re-write did not count as new", w2b.events_written == 0 and w2b.decisions_written == 0)

w3 = writer(engine)
good_event(w3, details={"x": np.float32(0.5), "arr": np.array([1, 2]), "nan": float("nan"),
                        "inf": float("inf"), "when": datetime(2026, 1, 1, tzinfo=timezone.utc),
                        "set": {2, 1}, "nested": {"a": [np.int64(3)]}, 5: "intkey"})
good_decision(w3, evidence={"v": np.float64(1.25)}, thresholds={"window": 30}, snapshot_ref="a/b.jpg", zone_id="zone_a")
w3.flush_sync()
d = json.loads(raw(path, "SELECT details FROM safety_events ORDER BY id DESC LIMIT 1")[0][0])
check("numpy, NaN, datetimes, sets and odd keys all become valid JSON",
      d["x"] == 0.5 and d["arr"] == [1, 2] and d["nan"] is None and d["inf"] is None
      and d["set"] == [1, 2] and d["nested"] == {"a": [3]} and d["5"] == "intkey" and "2026" in d["when"], str(d))
r = raw(path, "SELECT evidence, thresholds, snapshot_ref, zone_id, confidence FROM safety_decisions ORDER BY rowid DESC LIMIT 1")[0]
check("decision JSON, thresholds, snapshot and zone round-trip",
      json.loads(r[0]) == {"v": 1.25} and json.loads(r[1]) == {"window": 30} and r[2] == "a/b.jpg" and r[3] == "zone_a")

# ── 5. A refused row cannot hold up the good ones ────────────────────────────
print("5. One bad row among good ones")
engine, path = new_db()
w = writer(engine)
good_decision(w, video_time_s=1.0)
poison = dict(w._decisions[0]); poison["id"] = str(uuid.uuid4()); poison["status"] = "BOGUS"   # slips past add_*
w._decisions.append(poison)
good_decision(w, video_time_s=3.0)
good_event(w)
poison_e = dict(w._events[0]); poison_e["event_uid"] = str(uuid.uuid4()); poison_e["video_time_s"] = -5
w._events.appendleft(poison_e)
out = w.flush_sync()
check("the good rows were written", out == {"events": 1, "decisions": 2}, str(out))
check("the refused rows were counted and dropped, not retried for ever",
      w.rejected == 2 and w.pending_events == 0 and w.pending_decisions == 0)
check("only the good rows are in the file", count(path, "safety_events") == 1 and count(path, "safety_decisions") == 2)

# ── 6. A locked database: nothing raises, nothing is lost ────────────────────
print("6. A locked database")
engine, path = new_db(busy_ms=150)
w = writer(engine, retry_interval_s=0.0)
good_event(w); w.flush_sync()                       # tables exist, one row in
w = writer(engine, retry_interval_s=0.0)
dec_ids = [good_decision(w, video_time_s=float(i)) for i in range(4)]
ev_ids = [good_event(w, video_time_s=float(i)) for i in range(6)]
blocker = sqlite3.connect(path, timeout=0, isolation_level=None)
blocker.execute("BEGIN EXCLUSIVE")                  # another writer holds the file
try:
    out = w.flush_sync()
    check("a locked database does not raise into the caller", True)
    check("nothing was written", out == {"events": 0, "decisions": 0})
    check("every decision is still buffered", w.pending_decisions == 4, str(w.pending_decisions))
    check("every event is still buffered", w.pending_events == 6)
    check("the failure was noted (back-off was armed)", w._backoff_until > 0)
finally:
    blocker.execute("ROLLBACK"); blocker.close()
out = w.flush_sync()
check("after the lock is released the next flush writes them all", out == {"events": 6, "decisions": 4}, str(out))
got = {r[0] for r in raw(path, "SELECT id FROM safety_decisions")}
check("exactly the decisions that were added, once each", got == set(dec_ids))
check("the order they were added in is preserved",
      [r[0] for r in raw(path, "SELECT video_time_s FROM safety_decisions ORDER BY rowid")] == [0.0, 1.0, 2.0, 3.0])
check("nothing was dropped or rejected", w.dropped_decisions == 0 and w.dropped_events == 0 and w.rejected == 0)

# rows added WHILE the database was locked are not overtaken or lost
engine, path = new_db(busy_ms=100)
w = writer(engine, retry_interval_s=0.0)
good_decision(w, video_time_s=1.0); w.flush_sync()
good_decision(w, video_time_s=2.0)
blocker = sqlite3.connect(path, timeout=0, isolation_level=None); blocker.execute("BEGIN EXCLUSIVE")
w.flush_sync()
good_decision(w, video_time_s=3.0)                  # arrives after the failed flush
blocker.execute("ROLLBACK"); blocker.close()
w.flush_sync()
check("rows added after a failed flush queue behind the retried ones, in order",
      [r[0] for r in raw(path, "SELECT video_time_s FROM safety_decisions ORDER BY rowid")] == [1.0, 2.0, 3.0])

# a database that cannot be opened at all
engine_bad = create_engine("sqlite:///" + os.path.join(TMP, "no_such_dir", "x.sqlite"))
wb = writer(engine_bad, retry_interval_s=0.0)
good_decision(wb); good_event(wb)
try:
    out = wb.flush_sync()
    check("an unopenable database does not raise", True)
    check("...and the rows are kept", wb.pending_decisions == 1 and wb.pending_events == 1)
except Exception as exc:                             # pragma: no cover
    check("an unopenable database does not raise", False, repr(exc))

# ── 7. The buffer is bounded ─────────────────────────────────────────────────
print("7. Bounded buffer")
engine, path = new_db()
w = writer(engine, max_pending_events=5, max_pending_decisions=3)
uids = [good_event(w, video_time_s=float(i)) for i in range(8)]
check("events beyond the limit drop the OLDEST", w.pending_events == 5 and w.dropped_events == 3)
check("the newest are the ones kept", [r["event_uid"] for r in w._events] == uids[3:])
dids = [good_decision(w, video_time_s=float(i)) for i in range(5)]
check("decisions have their own limit", w.pending_decisions == 3 and w.dropped_decisions == 2)
check("filling decisions did not push events out", w.pending_events == 5)
w.flush_sync()
check("what was kept is what gets written",
      count(path, "safety_events") == 5 and count(path, "safety_decisions") == 3)

# ── 8. Threads ───────────────────────────────────────────────────────────────
print("8. Threads: adding while flushing")
engine, path = new_db(busy_ms=5000)
w = writer(engine, retry_interval_s=0.0)
errors = []
stop = threading.Event()

def adder(k):
    try:
        for i in range(300):
            good_event(w, worker_key=f"trk-{k}", video_time_s=float(i))
            if i % 10 == 0:
                good_decision(w, worker_key=f"trk-{k}", video_time_s=float(i))
    except Exception as ex:
        errors.append(ex)

def flusher():
    while not stop.is_set():
        w.flush_sync()

threads = [threading.Thread(target=adder, args=(k,)) for k in range(4)]
fl = [threading.Thread(target=flusher) for _ in range(2)]
for t in threads + fl:
    t.start()
for t in threads:
    t.join()
stop.set()
for t in fl:
    t.join()
w.flush_sync()
check("no thread raised", errors == [], str(errors))
check("every event added from four threads was stored, once", count(path, "safety_events") == 1200, str(count(path, "safety_events")))
check("every decision was stored, once", count(path, "safety_decisions") == 120, str(count(path, "safety_decisions")))
check("nothing is left in the buffer", not w.has_pending())
engine, path = new_db()
w = writer(engine)
good_event(w)
w._flush_lock.acquire()
try:
    check("a flush already in progress makes a second one a no-op, not a deadlock",
          w.flush_sync() == {"events": 0, "decisions": 0} and w.pending_events == 1)
finally:
    w._flush_lock.release()

# ── 9. The async entry point ─────────────────────────────────────────────────
print("9. persist_safety")
engine, path = new_db(busy_ms=100)
clock = [100.0]
w = writer(engine, retry_interval_s=2.0, monotonic=lambda: clock[0])
calls = []
real_flush = w.flush_sync
w.flush_sync = lambda: (calls.append(1), real_flush())[1]

async def run():
    await persist_safety(None)
    await persist_safety(w)                          # nothing pending: no flush
    check("nothing pending, no database work", calls == [])
    good_decision(w)
    blocker = sqlite3.connect(path, timeout=0, isolation_level=None)
    await persist_safety(w)                          # tables get created; written fine
    check("a normal call writes in a thread", count(path, "safety_decisions") == 1 and not w.has_pending())
    blocker.execute("BEGIN EXCLUSIVE")
    good_decision(w)
    await persist_safety(w)                          # locked: fails, enters back-off
    n1 = len(calls)
    await persist_safety(w)
    await persist_safety(w)
    check("after a failure the loop backs off instead of retrying every frame", len(calls) == n1, f"{n1} -> {len(calls)}")
    await persist_safety(w, force=True)
    check("force=True tries anyway (end of session)", len(calls) == n1 + 1)
    blocker.execute("ROLLBACK"); blocker.close()
    await persist_safety(w)
    check("still backed off until the interval passes", w.pending_decisions == 1)
    clock[0] += 2.5
    await persist_safety(w)
    check("after the interval it writes", w.pending_decisions == 0 and count(path, "safety_decisions") == 2)

asyncio.run(run())

# ── 10. Housekeeping ─────────────────────────────────────────────────────────
print("10. purge_local")
engine, path = new_db()
ensure_safety_tables(engine)
base = datetime(2026, 11, 8, tzinfo=timezone.utc)
iso = lambda dt: dt.isoformat(timespec="milliseconds")
def seed(table, uid_col, synced_at, created_at, n=1):
    for _ in range(n):
        u = str(uuid.uuid4())
        if table == "safety_events":
            raw(path, "INSERT INTO safety_events (event_uid,camera_id,worker_key,event_type,video_time_s,observed_at,created_at,synced_at) VALUES (?,?,?,?,?,?,?,?)",
                (u, "c", "trk-1", "ENTERED_ZONE", 1.0, NOW, created_at, synced_at))
        else:
            raw(path, "INSERT INTO safety_decisions (id,camera_id,rule_type,severity,worker_key,status,reason,confidence,evidence,video_time_s,observed_at,created_at,synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (u, "c", "PPE_ENTRY", "WARNING", "trk-1", "POTENTIAL_VIOLATION", "r", 0.9, "{}", 1.0, NOW, created_at, synced_at))
for t in ("safety_events", "safety_decisions"):
    seed(t, "", iso(base - timedelta(days=10)), iso(base - timedelta(days=11)))   # synced, old
    seed(t, "", iso(base - timedelta(days=1)), iso(base - timedelta(days=2)))     # synced, recent
    seed(t, "", None, iso(base - timedelta(days=60)))                              # unsynced, very old
    seed(t, "", None, iso(base - timedelta(days=1)))                               # unsynced, recent
out = purge_local(engine, synced_days=7, now=base)
check("synced rows older than the limit are deleted", out == {"safety_events": 1, "safety_decisions": 1}, str(out))
for t in ("safety_events", "safety_decisions"):
    check(f"{t}: 3 rows remain", count(path, t) == 3)
    check(f"{t}: an unsynced row is NEVER deleted by default, however old",
          count(path, t, "WHERE synced_at IS NULL") == 2)
out = purge_local(engine, synced_days=7, unsynced_days=30, now=base)
check("standalone mode also deletes old unsynced rows", out == {"safety_events": 1, "safety_decisions": 1}, str(out))
for t in ("safety_events", "safety_decisions"):
    check(f"{t}: recent unsynced and recent synced rows survive", count(path, t) == 2)
check("purge is idempotent", purge_local(engine, synced_days=7, unsynced_days=30, now=base) == {"safety_events": 0, "safety_decisions": 0})
check("a retention under one day is refused", raises(lambda: purge_local(engine, synced_days=0)) and raises(lambda: purge_local(engine, unsynced_days=0)))
fresh, _ = new_db()
ensure_safety_tables(fresh)
check("purge on empty tables deletes nothing", purge_local(fresh) == {"safety_events": 0, "safety_decisions": 0})

# ── 11. WAL and the busy timeout ─────────────────────────────────────────────
print("11. Concurrency settings")
engine, path = new_db(busy_ms=1234)
with engine.connect() as c:
    check("journal mode is WAL on a new connection", c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal")
    check("busy_timeout is applied", c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 1234)
    check("synchronous is NORMAL", c.exec_driver_sql("PRAGMA synchronous").scalar() == 1)
engine.dispose()
engine2 = create_engine(f"sqlite:///{path}")
configure_sqlite(engine2)
with engine2.connect() as c:
    check("the default timeout is 5 s", c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000)
# a reader is not blocked by a writer in WAL mode
engine, path = new_db(busy_ms=150)
ensure_safety_tables(engine)
raw(path, "INSERT INTO safety_events (event_uid,camera_id,worker_key,event_type,video_time_s,observed_at,created_at) VALUES ('a','c','t','ENTERED_ZONE',1,'x','y')")
writer_conn = sqlite3.connect(path, timeout=0, isolation_level=None)
writer_conn.execute("BEGIN IMMEDIATE")
writer_conn.execute("INSERT INTO safety_events (event_uid,camera_id,worker_key,event_type,video_time_s,observed_at,created_at) VALUES ('b','c','t','ENTERED_ZONE',1,'x','y')")
try:
    check("a reader is not blocked while a writer holds the file (WAL)", count(path, "safety_events") == 1)
finally:
    writer_conn.execute("ROLLBACK"); writer_conn.close()

# ── 12. The real app ─────────────────────────────────────────────────────────
print("12. The real app creates the tables on start")
from fastapi.testclient import TestClient                               # noqa: E402
from app.main import app                                                # noqa: E402
from app.db.database import engine as real_engine                       # noqa: E402
from sqlalchemy import text                                             # noqa: E402

with TestClient(app):                                                   # runs the lifespan
    with real_engine.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy = c.exec_driver_sql("PRAGMA busy_timeout").scalar()
check("startup created safety_events and safety_decisions in the real database",
      {"safety_events", "safety_decisions"} <= tables)
check("the real database is in WAL mode", mode.lower() == "wal", mode)
check("the real engine waits for locks", busy == 5000, str(busy))

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("SAFETY WRITER: ALL CHECKS PASSED")
