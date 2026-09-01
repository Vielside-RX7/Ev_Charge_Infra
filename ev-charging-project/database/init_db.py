"""
Database initialisation script.

Reads DATABASE_URL from the environment (via .env file) and creates all
tables defined in models.py using SQLAlchemy's metadata.create_all().

Usage:
    python database/init_db.py
"""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

# Load .env from the project root (one level above database/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# DATABASE_URL is required — no silent fallback
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print(
        "[ERROR] DATABASE_URL is not set.\n"
        "  1. Copy .env.example to .env:  cp .env.example .env\n"
        "  2. Start PostgreSQL:           docker compose up -d\n"
        "  3. Re-run this script:         python database/init_db.py",
        file=sys.stderr,
    )
    sys.exit(1)

# Import Base (which carries all model metadata) after env is loaded
from models import Base  # noqa: E402


def init_db() -> None:
    """Create all tables and print confirmation."""
    print(f"Connecting to: {DATABASE_URL}")

    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(bind=engine)

    # List the tables that now exist in the database
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    print(f"\n[OK] Database initialised successfully -- {len(tables)} table(s) created:")
    for table in tables:
        print(f"  - {table}")

    engine.dispose()


if __name__ == "__main__":
    try:
        init_db()
    except Exception as exc:
        print(f"[ERROR] Failed to initialise database: {exc}", file=sys.stderr)
        sys.exit(1)
