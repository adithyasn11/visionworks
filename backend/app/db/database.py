# backend/app/db/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# SQLite database file located directly inside d:\major project root
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../workplace_analytics.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} # SQLite threading requirement
)

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
    if "activity_logs" not in inspector.get_table_names():
        return  # create_all() will build it with the current schema.

    existing = {col["name"] for col in inspector.get_columns("activity_logs")}
    missing = [c for c in ("floor_x", "floor_y") if c not in existing]
    if not missing:
        return

    with engine.begin() as conn:
        for column in missing:
            conn.execute(text(f"ALTER TABLE activity_logs ADD COLUMN {column} FLOAT"))
