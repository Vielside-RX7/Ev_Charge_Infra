"""
Database Connection & Session Management Module
===============================================
Manages SQLAlchemy engine, session maker, and schema initialization.
"""

import os
import logging
from typing import Generator
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session

# Load environment
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ev_admin:changeme@localhost:5432/ev_charging")

logger = logging.getLogger(__name__)

_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        engine = get_engine()
        _SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _SessionFactory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_schema():
    """Initializes and updates schema tables and columns idempotently."""
    from database.models import Base
    engine = get_engine()
    
    # 1. Create any missing tables (e.g. user_vehicles, trip_histories)
    Base.metadata.create_all(bind=engine)
    
    # 2. Add password_hash column to users if not present
    inspector = inspect(engine)
    user_cols = [c["name"] for c in inspector.get_columns("users")]
    if "password_hash" not in user_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255);"))
            logger.info("Added password_hash column to users table.")
    
    logger.info("Database schema initialized successfully.")


# Run schema init on import
try:
    init_schema()
except Exception as e:
    logger.warning("Database schema init deferred: %s", e)
