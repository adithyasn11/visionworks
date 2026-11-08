# backend/app/db/database.py
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import logging
import os

logger = logging.getLogger(__name__)

# How long a connection waits for a lock another connection holds before it
# gives up with "database is locked". Five seconds is far longer than any write
# here takes, so a wait is a brief overlap, not a failure.
SQLITE_BUSY_TIMEOUT_MS = 5000


def configure_sqlite(engine_, busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS):
    """
    Make every connection this engine opens safe for several writers.

    The pipeline now has more than one: telemetry rows, safety events, safety
    decisions, and the sync that stamps `synced_at`. SQLite's default journal
    lets a writer block readers and makes a second writer fail immediately with
    "database is locked". Two settings fix both:

      * WAL journal      readers no longer block the writer, and the writer no
                         longer blocks readers. It is a property of the file,
                         so it persists once set.
      * busy_timeout     a writer that finds the file locked WAITS for it
                         instead of failing at once.

    `synchronous=NORMAL` is the standard pairing with WAL: a crash can lose the
    last moment's commits but never corrupts the file, which is the right trade
    for telemetry that is rebuilt from the video anyway. (Safety DECISIONS are
    protected separately, by the writer's retry buffer.)

    Run on every new connection, because busy_timeout is per-connection.
    """
    @event.listens_for(engine_, "connect")
    def _on_connect(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode and str(mode[0]).lower() != "wal":
                # A filesystem that cannot do WAL (some network drives) keeps
                # working in its old mode; say so once rather than fail.
                logger.warning(f"SQLite WAL not available here (journal_mode={mode[0]}); "
                               "concurrent writers may see 'database is locked'.")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
    return engine_

# SQLite database file located directly inside d:\major project root
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../workplace_analytics.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} # SQLite threading requirement
)
configure_sqlite(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency injection generator yielding DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_lightweight_migrations():
    """
    Adds columns that were introduced after a database was first created.

    Base.metadata.create_all() only ever CREATEs missing tables — it will not
    alter one that already exists. Without this, anyone with a workplace_analytics.db
    from before the floorplan-position columns were added would hit
    "no such column: activity_logs.floor_x" on every analytics query, with no
    obvious fix short of deleting the database.

    Deliberately minimal: this project does not use Alembic, and a single
    additive ALTER is not worth the migration framework. Each statement is
    guarded by an existence check, so running it repeatedly is a no-op.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # column name -> SQL type, per table. Every entry must be nullable with no
    # default: SQLite can only ADD COLUMN that way without rewriting the table.
    #
    # `org_id` is the tenancy column added in Step 2. It is deliberately NULL
    # for every row that already exists — those rows were recorded before the
    # system had a concept of an organisation, so they genuinely have no owner.
    # Backfilling them to whichever org happened to be created first would
    # invent an ownership claim the data never had. Org-scoped queries filter
    # them out, which means a pre-existing database reads as empty until a
    # video is processed under an organisation. That is the honest state.
    additions = {
        "activity_logs": {"floor_x": "FLOAT", "floor_y": "FLOAT", "org_id": "VARCHAR(64)"},
        "zones": {"org_id": "VARCHAR(64)"},
        "cameras": {"org_id": "VARCHAR(64)", "inference_width": "INTEGER"},
    }

    statements = []
    for table, columns in additions.items():
        if table not in tables:
            continue  # create_all() will build it with the current schema.
        existing = {col["name"] for col in inspector.get_columns(table)}
        for column, sql_type in columns.items():
            if column not in existing:
                statements.append(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    # Indexes are created separately and are idempotent via IF NOT EXISTS.
    # Every org-scoped query filters on org_id first, so without these the
    # tenancy filter turns each analytics call into a full table scan.
    index_statements = [
        'CREATE INDEX IF NOT EXISTS ix_activity_logs_org_id ON activity_logs (org_id)',
        'CREATE INDEX IF NOT EXISTS ix_activity_logs_org_time ON activity_logs (org_id, timestamp)',
        'CREATE INDEX IF NOT EXISTS ix_zones_org_id ON zones (org_id)',
        'CREATE INDEX IF NOT EXISTS ix_cameras_org_id ON cameras (org_id)',
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))

        # Re-read the table list rather than reusing the snapshot above: the
        # ALTERs may have just created columns the indexes depend on, and
        # create_all() runs before this function, so a fresh database has all
        # three tables by now. Indexing a table that does not exist is an
        # error, not a no-op.
        present = set(inspect(engine).get_table_names())
        for statement in index_statements:
            table = statement.split(" ON ")[1].split(" ", 1)[0]
            if table in present:
                conn.execute(text(statement))
