"""SQLite persistence foundation: engine/session wiring, foreign-key pragmas,
schema versioning, and the shared declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SCHEMA_VERSION = 1


class Base(DeclarativeBase):
    """Shared declarative base for every model in the demo."""


def build_engine(database_url: str) -> Engine:
    """Create a SQLite engine with foreign keys always enabled.

    File-backed databases (anything other than ``:memory:``) also get WAL
    journaling and a busy timeout so a later multi-user deployment does not
    deadlock on writes.
    """
    url = make_url(database_url)
    is_memory = url.database == ":memory:"

    if not is_memory and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if not is_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker:
    """Return a session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps ORM instances readable after a commit so
    fixtures can hand committed rows back to tests without an extra round trip.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _import_all_models() -> None:
    """Import every model module so its table registers on ``Base.metadata``."""
    from app.catalog import models as catalog_models  # noqa: F401
    from app.sessions import models as session_models  # noqa: F401
    from app.feedback import models as feedback_models  # noqa: F401


def initialize_database(engine: Engine) -> None:
    """Create all tables and verify/set the schema version pragma."""
    _import_all_models()
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        current = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
        if current != SCHEMA_VERSION:
            if current != 0:
                raise RuntimeError(
                    f"schema version mismatch: database has {current}, "
                    f"expected {SCHEMA_VERSION}"
                )
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _indicator_seed_path() -> Path:
    """Resolve the indicator seed file relative to the package, not the CWD."""
    return Path(__file__).resolve().parent.parent.parent / "db" / "seed_indicators.sql"


def seed_indicators(engine: Engine) -> None:
    """Seed the indicator dictionary using insert-missing semantics.

    ``db/seed_indicators.sql`` uses ``INSERT OR IGNORE`` so existing rows are
    never overwritten. Every statement runs inside a single transaction so a
    partial failure leaves the indicator table unchanged.
    """
    script = _indicator_seed_path().read_text(encoding="utf-8")

    statements: list[str] = []
    for chunk in script.split(";"):
        stmt = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stmt:
            statements.append(stmt)

    with engine.begin() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session for one request."""
    factory: sessionmaker = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()
