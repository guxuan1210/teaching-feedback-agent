"""SQLite persistence foundation: engine/session wiring, foreign-key pragmas,
schema versioning, and the shared declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SCHEMA_VERSION = 9


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
    from app.chat import models as chat_models  # noqa: F401
    from app.sessions import models as session_models  # noqa: F401
    from app.feedback import models as feedback_models  # noqa: F401
    from app.reports import models as report_models  # noqa: F401
    from app.wecom import models as wecom_models  # noqa: F401
    from app.family import models as family_models  # noqa: F401


def _add_missing_columns(engine: Engine) -> None:
    """Additive migrations for columns introduced after the initial schema.

    ``create_all`` only creates missing tables, not missing columns, so a
    pre-existing demo database needs a lightweight ALTER. Each migration checks
    the current column set first and is therefore idempotent.
    """
    with engine.begin() as conn:
        teacher_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(teacher)")}
        if "password_hash" not in teacher_cols:
            conn.exec_driver_sql(
                "ALTER TABLE teacher ADD COLUMN password_hash VARCHAR"
            )
        student_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(student)")}
        if "late_care_level" not in student_cols:
            conn.exec_driver_sql(
                "ALTER TABLE student ADD COLUMN late_care_level VARCHAR"
            )
        report_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(weekly_report)")}
        if "parent_message" not in report_cols:
            conn.exec_driver_sql(
                "ALTER TABLE weekly_report ADD COLUMN parent_message TEXT"
            )
        wecom_state_cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(wecom_chat_state)")
        }
        if wecom_state_cols and "pending_question" not in wecom_state_cols:
            conn.exec_driver_sql(
                "ALTER TABLE wecom_chat_state ADD COLUMN pending_question TEXT"
            )
        message_cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(chat_message)")
        }
        if message_cols and "channel" not in message_cols:
            conn.exec_driver_sql(
                "ALTER TABLE chat_message ADD COLUMN channel VARCHAR"
            )


def _migrate_student_teacher_assignments(engine: Engine) -> None:
    """Rebuild legacy assignment storage with source checks and safe uniqueness."""
    today = datetime.now(timezone.utc).date().isoformat()
    with engine.connect() as conn:
        table_sql = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='student_teacher_assignment'"
        ).scalar_one_or_none()
        conn.commit()
        if table_sql is None:
            return
        columns = {
            row[1]
            for row in conn.exec_driver_sql(
                "PRAGMA table_info(student_teacher_assignment)"
            )
        }
        conn.commit()
        needs_rebuild = not {"origin", "source_enrollment_id"} <= columns or not (
            "origin = 'manual'" in table_sql and "origin = 'class_sync'" in table_sql
        )

        if not needs_rebuild:
            with conn.begin():
                _canonicalize_active_assignments(conn, today)
                _create_assignment_indexes(conn)
            return

        origin_sql = "old.origin" if "origin" in columns else "'manual'"
        source_sql = (
            "old.source_enrollment_id" if "source_enrollment_id" in columns else "NULL"
        )
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            with conn.begin():
                conn.exec_driver_sql("DROP TABLE IF EXISTS student_teacher_assignment_new")
                conn.exec_driver_sql(
                    "CREATE TABLE student_teacher_assignment_new ("
                    "assignment_id VARCHAR PRIMARY KEY, "
                    "student_id VARCHAR NOT NULL REFERENCES student(student_id), "
                    "teacher_id VARCHAR NOT NULL REFERENCES teacher(teacher_id), "
                    "role VARCHAR NOT NULL, start_date VARCHAR NOT NULL, "
                    "end_date VARCHAR, status VARCHAR NOT NULL DEFAULT 'active', "
                    "origin VARCHAR NOT NULL DEFAULT 'manual', "
                    "source_enrollment_id INTEGER REFERENCES enrollment(enrollment_id), "
                    "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL, "
                    "CONSTRAINT ck_student_teacher_assignment_role "
                    "CHECK (role IN ('primary','subject','collaborator')), "
                    "CONSTRAINT ck_student_teacher_assignment_dates "
                    "CHECK (end_date IS NULL OR end_date >= start_date), "
                    "CONSTRAINT ck_student_teacher_assignment_status "
                    "CHECK (status IN ('active','revoked')), "
                    "CONSTRAINT ck_student_teacher_assignment_origin CHECK ("
                    "(origin = 'manual' AND source_enrollment_id IS NULL) OR "
                    "(origin = 'class_sync' AND source_enrollment_id IS NOT NULL "
                    "AND role = 'primary')))"
                )
                conn.exec_driver_sql(
                    "WITH ranked AS ("
                    "SELECT *, ROW_NUMBER() OVER ("
                    "PARTITION BY student_id,teacher_id,role "
                    "ORDER BY start_date,assignment_id) AS active_rank "
                    "FROM student_teacher_assignment WHERE status='active') "
                    "INSERT INTO student_teacher_assignment_new "
                    "(assignment_id,student_id,teacher_id,role,start_date,end_date,"
                    "status,origin,source_enrollment_id,created_at,updated_at) "
                    "SELECT old.assignment_id,old.student_id,old.teacher_id,old.role,"
                    "old.start_date,CASE WHEN ranked.active_rank > 1 THEN "
                    "CASE WHEN old.start_date > :today THEN old.start_date ELSE :today END "
                    "ELSE old.end_date END,CASE WHEN ranked.active_rank > 1 "
                    "THEN 'revoked' ELSE old.status END,"
                    f"{origin_sql},{source_sql},old.created_at,old.updated_at "
                    "FROM student_teacher_assignment AS old "
                    "LEFT JOIN ranked ON ranked.assignment_id=old.assignment_id",
                    {"today": today},
                )
                conn.exec_driver_sql("DROP TABLE student_teacher_assignment")
                conn.exec_driver_sql(
                    "ALTER TABLE student_teacher_assignment_new "
                    "RENAME TO student_teacher_assignment"
                )
                _create_assignment_indexes(conn)
                violations = conn.exec_driver_sql(
                    "PRAGMA foreign_key_check(student_teacher_assignment)"
                ).all()
                if violations:
                    raise RuntimeError("student teacher assignment migration violated foreign keys")
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def _canonicalize_active_assignments(conn, today: str) -> None:
    conn.exec_driver_sql(
        "WITH ranked AS ("
        "SELECT assignment_id,ROW_NUMBER() OVER ("
        "PARTITION BY student_id,teacher_id,role "
        "ORDER BY start_date,assignment_id) AS active_rank "
        "FROM student_teacher_assignment WHERE status='active') "
        "UPDATE student_teacher_assignment SET status='revoked', "
        "end_date=CASE WHEN start_date > :today THEN start_date ELSE :today END "
        "WHERE assignment_id IN (SELECT assignment_id FROM ranked WHERE active_rank > 1)",
        {"today": today},
    )


def _create_assignment_indexes(conn) -> None:
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_student_teacher_assignment_lookup "
        "ON student_teacher_assignment(teacher_id, student_id, status)"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_student_teacher_active_role "
        "ON student_teacher_assignment(student_id, teacher_id, role) "
        "WHERE status = 'active'"
    )


def _compose_legacy_parent_message(
    summary: str, strengths: str, concerns: str, suggestions: str
) -> str:
    """Combine the pre-redesign structured fields into a readable message.

    Used only to backfill ``parent_message`` for reports created before the
    two-layer content model; it never calls a model or introduces new facts.
    """
    import json

    def _items(raw: str) -> list[str]:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    lines = [summary.strip()]
    if (strength_items := _items(strengths)):
        lines.append("优势：" + "；".join(strength_items))
    if (concern_items := _items(concerns)):
        lines.append("待关注：" + "；".join(concern_items))
    if (suggestion_items := _items(suggestions)):
        lines.append("建议：" + "；".join(suggestion_items))
    return "\n\n".join(lines)


def _backfill_parent_message(engine: Engine) -> None:
    """Populate ``parent_message`` for any report that predates the column."""
    from sqlalchemy import select

    from app.reports.models import WeeklyReport

    factory = build_session_factory(engine)
    with factory() as session:
        reports = session.scalars(
            select(WeeklyReport).where(WeeklyReport.parent_message.is_(None))
        ).all()
        for report in reports:
            report.parent_message = _compose_legacy_parent_message(
                report.summary, report.strengths, report.concerns, report.suggestions
            )
        session.commit()


def _backfill_teacher_chat_state(engine: Engine) -> None:
    """Backfill ``teacher_chat_state`` from still-valid wecom conversation state.

    A wecom row's ``conversation_id`` is promoted to the unified current
    conversation only when the conversation still exists, belongs to the bound
    teacher, and is active. Anything deleted, foreign, or archived is skipped.
    """
    from sqlalchemy import select

    from app.chat.models import ChatConversation, TeacherChatState
    from app.wecom.models import TeacherWecomBinding, WecomChatState

    factory = build_session_factory(engine)
    with factory() as session:
        bindings = {
            row.wecom_user_id: row.teacher_id
            for row in session.scalars(select(TeacherWecomBinding))
        }
        states = session.scalars(
            select(WecomChatState).where(WecomChatState.conversation_id.is_not(None))
        ).all()
        for state in states:
            teacher_id = bindings.get(state.wecom_user_id)
            if teacher_id is None:
                continue
            conversation = session.get(ChatConversation, state.conversation_id)
            if (
                conversation is None
                or conversation.owner_teacher_id != teacher_id
                or conversation.status != "active"
            ):
                continue
            existing = session.get(TeacherChatState, teacher_id)
            if existing is None:
                session.add(
                    TeacherChatState(
                        teacher_id=teacher_id,
                        current_conversation_id=conversation.conversation_id,
                    )
                )
            elif existing.current_conversation_id != conversation.conversation_id:
                existing.current_conversation_id = conversation.conversation_id
        session.commit()


def initialize_database(engine: Engine) -> None:
    """Create all tables and verify/set the schema version pragma."""
    with engine.connect() as conn:
        current = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"schema version mismatch: database has {current}, "
            f"expected at most {SCHEMA_VERSION}"
        )

    _import_all_models()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _migrate_student_teacher_assignments(engine)
    _backfill_parent_message(engine)
    _backfill_teacher_chat_state(engine)

    if current < SCHEMA_VERSION:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _indicator_seed_path() -> Path:
    """Resolve the indicator seed file relative to the package, not the CWD."""
    return Path(__file__).resolve().parent.parent.parent / "db" / "seed_indicators.sql"


def _reference_seed_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "db" / "seed_reference_data.sql"


def _run_seed_script(engine: Engine, path: Path) -> None:
    script = path.read_text(encoding="utf-8")

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


def seed_indicators(engine: Engine) -> None:
    """Seed the indicator dictionary using insert-missing semantics.

    ``db/seed_indicators.sql`` uses ``INSERT OR IGNORE`` so existing rows are
    never overwritten. Every statement runs inside a single transaction so a
    partial failure leaves the indicator table unchanged.
    """
    _run_seed_script(engine, _indicator_seed_path())


def seed_reference_data(engine: Engine) -> None:
    """Seed stages, late-care levels, and admission-assessment rubrics."""
    _run_seed_script(engine, _reference_seed_path())


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session for one request."""
    factory: sessionmaker = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


DEFAULT_ADMIN_PASSWORD = "admin123"


def seed_admin(engine: Engine) -> None:
    """Create a default admin account on first run so the app is usable.

    Only inserts when no ``管理员``-role teacher exists yet, so it never
    overrides accounts created later. The default password is for the local
    demo only.
    """
    from sqlalchemy import select

    from app.catalog.models import Teacher
    from app.core.security import hash_password

    factory = build_session_factory(engine)
    with factory() as session:
        existing = session.scalar(
            select(Teacher).where(Teacher.role == "管理员").limit(1)
        )
        if existing is not None:
            return
        session.add(
            Teacher(
                teacher_id="ADMIN",
                name="管理员",
                role="管理员",
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                status="active",
            )
        )
        session.commit()
