"""Database setup. SQLite is a single file (data/dropship.db) - no server needed."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import PROJECT_ROOT


class Base(DeclarativeBase):
    """All database tables inherit from this."""


def make_engine(database_url: str) -> Engine:
    if database_url == "sqlite://" or database_url == "sqlite:///:memory:":
        # In-memory database (used by tests): share one connection so data persists.
        return create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    if database_url.startswith("sqlite:///"):
        # Make relative SQLite paths relative to the project, and create the folder.
        file_part = database_url.removeprefix("sqlite:///")
        db_path = Path(file_part)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{db_path}"
    return create_engine(database_url, connect_args={"check_same_thread": False})


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create any tables that don't exist yet. Safe to run repeatedly."""
    from app.core import models  # noqa: F401  (import registers the tables)

    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """When a later phase adds a new (optional) column to an existing table, add it to
    your existing database too, so you never have to delete your data to upgrade."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
