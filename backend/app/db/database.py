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
