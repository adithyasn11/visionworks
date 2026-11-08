# backend/app/db/safety_models.py
"""
The local (SQLite) home of the safety layer's output: events and decisions.

TWO TABLES, TWO KINDS OF RECORD

  safety_events      what the pipeline SAW: a worker entered / left a zone, was
                     lost, reappeared, passed a dwell warning. Facts with a
                     video time, nothing judged.
  safety_decisions   what a RULE concluded from them: COMPLIANT,
                     POTENTIAL_VIOLATION or UNVERIFIABLE, with its reason, its
                     confidence, and the numbers behind it.

They are the pipeline's own durable record. The dashboard reads Postgres; the
sync (Step 8) copies these rows across. That is why every row carries a UUID
made at the SOURCE: re-sending a row is then harmless, because Postgres sees the
same id and ignores it.

HONESTY IS ENFORCED BY THE TABLE, NOT BY GOOD INTENTIONS

Each vocabulary (event types, statuses, rule types, severities) is a CHECK
constraint, confidence is held to 0..1, and an UNVERIFIABLE decision must carry
confidence 0 — it makes no claim, so it must not appear to have a confidence.
A bug in the safety code therefore fails loudly at the database instead of
quietly storing something the dashboard cannot interpret.

The vocabularies below are the single source: the DDL is built from them, and
the writer validates against the same tuples.

Created the same way as `ensure_bucket_table()`: CREATE TABLE IF NOT EXISTS, no
migration framework, safe to run on every start.
"""

from typing import Optional

from sqlalchemy import text

from app.db.database import engine as _default_engine

EVENT_TYPES = (
    "ENTERED_ZONE", "EXITED_ZONE", "LOST_IN_ZONE", "REAPPEARED",
    "DWELL_WARNING", "DWELL_EXCEEDED", "PPE_VERDICT_CHANGED",
)
DECISION_STATUSES = ("COMPLIANT", "POTENTIAL_VIOLATION", "UNVERIFIABLE")
RULE_TYPES = (
    "PPE_ENTRY", "PPE_CONTINUOUS", "MAX_DWELL", "REQUIRED_EXIT",
    "OCCUPANCY", "MAN_DOWN", "WALK_ONLY",
)
SEVERITIES = ("INFO", "WARNING", "CRITICAL")


def _in_list(values) -> str:
    return ",".join(f"'{v}'" for v in values)


def ddl_statements() -> list:
    """The CREATE statements, in order. Kept as a function so tests can read them."""
    return [
        f"""
        CREATE TABLE IF NOT EXISTS safety_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid     TEXT    NOT NULL UNIQUE,
            org_id        TEXT,
            camera_id     TEXT    NOT NULL,
            zone_id       TEXT,
            worker_key    TEXT    NOT NULL,
            session_key   TEXT,
            event_type    TEXT    NOT NULL,
            video_time_s  REAL    NOT NULL,
            observed_at   TEXT    NOT NULL,
            details       TEXT,
            synced_at     TEXT,
            created_at    TEXT    NOT NULL,
            CHECK (event_type IN ({_in_list(EVENT_TYPES)})),
            CHECK (video_time_s >= 0)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_se_org_observed    ON safety_events (org_id, observed_at)",
        "CREATE INDEX IF NOT EXISTS ix_se_camera_observed ON safety_events (camera_id, observed_at)",
        "CREATE INDEX IF NOT EXISTS ix_se_worker          ON safety_events (worker_key, observed_at)",
        "CREATE INDEX IF NOT EXISTS ix_se_unsynced        ON safety_events (id) WHERE synced_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_se_session         ON safety_events (session_key, video_time_s)",
        f"""
        CREATE TABLE IF NOT EXISTS safety_decisions (
            id             TEXT PRIMARY KEY NOT NULL,
            org_id         TEXT,
            camera_id      TEXT NOT NULL,
            zone_id        TEXT,
            rule_id        TEXT,
            rule_type      TEXT NOT NULL,
            severity       TEXT NOT NULL,
            worker_key     TEXT NOT NULL,
            session_key    TEXT,
            status         TEXT NOT NULL,
            reason         TEXT NOT NULL,
            confidence     REAL NOT NULL,
            evidence       TEXT NOT NULL,
            thresholds     TEXT,
            snapshot_ref   TEXT,
            video_time_s   REAL NOT NULL,
            observed_at    TEXT NOT NULL,
            synced_at      TEXT,
            created_at     TEXT NOT NULL,
            CHECK (status IN ({_in_list(DECISION_STATUSES)})),
            CHECK (rule_type IN ({_in_list(RULE_TYPES)})),
            CHECK (severity IN ({_in_list(SEVERITIES)})),
            CHECK (confidence >= 0 AND confidence <= 1),
            CHECK (status <> 'UNVERIFIABLE' OR confidence = 0),
            CHECK (video_time_s >= 0)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_sd_org_observed ON safety_decisions (org_id, observed_at)",
        "CREATE INDEX IF NOT EXISTS ix_sd_status       ON safety_decisions (org_id, status, observed_at)",
        "CREATE INDEX IF NOT EXISTS ix_sd_unsynced     ON safety_decisions (created_at) WHERE synced_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_sd_session      ON safety_decisions (session_key, video_time_s)",
    ]


def ensure_safety_tables(engine=None) -> None:
    """Create the two tables and their indexes if they are not there. Idempotent."""
    with (engine or _default_engine).begin() as conn:
        for statement in ddl_statements():
            conn.execute(text(statement))


def purge_local(
    engine=None,
    *,
    synced_days: int = 7,
    unsynced_days: Optional[int] = None,
    now=None,
) -> dict:
    """
    Keep the local file from growing for ever.

    * Rows already copied to Postgres (`synced_at` set) are deleted once they
      are older than `synced_days`. Postgres holds the durable copy.
    * Rows NOT yet synced are never deleted by default: they are the only copy
      of a decision. Only when `unsynced_days` is given (standalone mode, where
      there is no Postgres to sync to and nothing will ever stamp them) are
      unsynced rows older than that removed too.

    "Older" is measured on `created_at` for unsynced rows and on `synced_at` for
    synced ones, both ISO-8601 UTC strings, which sort correctly as text.
    Returns how many rows were deleted from each table.
    """
    from datetime import datetime, timedelta, timezone

    if synced_days < 1 or (unsynced_days is not None and unsynced_days < 1):
        raise ValueError("retention must be at least one day")

    moment = now or datetime.now(timezone.utc)
    synced_cutoff = (moment - timedelta(days=synced_days)).isoformat(timespec="milliseconds")
    unsynced_cutoff = (
        (moment - timedelta(days=unsynced_days)).isoformat(timespec="milliseconds")
        if unsynced_days is not None else None
    )

    deleted = {"safety_events": 0, "safety_decisions": 0}
    with (engine or _default_engine).begin() as conn:
        for table in deleted:
            n = conn.execute(
                text(f"DELETE FROM {table} WHERE synced_at IS NOT NULL AND synced_at < :cut"),
                {"cut": synced_cutoff},
            ).rowcount
            if unsynced_cutoff is not None:
                n += conn.execute(
                    text(f"DELETE FROM {table} WHERE synced_at IS NULL AND created_at < :cut"),
                    {"cut": unsynced_cutoff},
                ).rowcount
            deleted[table] = n
    return deleted
