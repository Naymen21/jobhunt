"""SQLite setup. Tables for jobs, applications and contacts get added here in later phases."""
from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from finder.config import DATA_DIR

DB_PATH = Path(os.environ.get("JOBHUNT_DB", str(DATA_DIR / "jobhunt.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


# Columns added after a table first shipped: (table, column, SQL type). create_all() only creates
# missing tables, so older databases get these with ALTER TABLE and keep their data.
_ADDED_COLUMNS = [
    ("found_email", "title", "VARCHAR"),
    ("found_email", "department", "VARCHAR"),
    ("found_email", "seniority", "VARCHAR"),
    ("profile", "resume_link", "VARCHAR DEFAULT ''"),
    ("application", "cover_table_json", "VARCHAR DEFAULT ''"),
    ("application", "cover_table_ai_json", "VARCHAR DEFAULT ''"),
    ("application", "table_edited", "BOOLEAN DEFAULT 0"),
    ("application", "needs_person", "BOOLEAN DEFAULT 0"),
    ("contact", "mode", "VARCHAR DEFAULT ''"),
    ("contact", "company_key", "VARCHAR DEFAULT ''"),
    ("company_profile", "about", "VARCHAR DEFAULT ''"),
    ("company_profile", "cold_status", "VARCHAR DEFAULT ''"),
    ("profile", "student_level", "VARCHAR DEFAULT 'high_school'"),
    ("profile", "degree_program", "VARCHAR DEFAULT ''"),
    ("profile", "home_state", "VARCHAR DEFAULT ''"),
    ("profile", "home_cities", "VARCHAR DEFAULT ''"),
]


def _upgrade(conn) -> None:
    for table, column, sqltype in _ADDED_COLUMNS:
        cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}")


def init_db() -> None:
    from . import tables  # noqa: F401  (registers models)

    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        _upgrade(conn)
    from .jobs_repo import JobRepo
    JobRepo().apply_student()  # student level + home area from the profile, for the engine


def get_session():
    with Session(engine) as session:
        yield session
