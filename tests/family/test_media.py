from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.catalog.models import Student, Teacher
from app.family.models import PendingMediaAssignment, StudentImage, StudentTeacherAssignment
from app.family.relationships import assign_teacher
from app.family.storage import LocalImageStore
from app.family.media import (
    archive_teacher_images,
    confirm_pending_media,
    expire_pending_media,
    list_student_images,
    restore_image,
    soft_delete_image,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"payload"
NOW = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def media_env(db_session, tmp_path):
    teacher = Teacher(teacher_id="T1", name="王老师", role="晚辅教师", status="active")
    admin = Teacher(teacher_id="ADMIN", name="管理员", role="管理员", status="active")
    db_session.add_all([teacher, admin])
    students = [
        Student(student_id="S1", name="李明", status="active"),
        Student(student_id="S2", name="王芳", status="active"),
    ]
    db_session.add_all(students)
    db_session.commit()
    assign_teacher(db_session, "S1", "T1", "primary", NOW.date())
    assign_teacher(db_session, "S2", "T1", "primary", NOW.date())
    db_session.commit()
    return db_session, LocalImageStore(tmp_path / "images"), teacher, admin, students


def archive(db, store, caption="", payloads=(PNG,), source="MSG-1"):
    return archive_teacher_images(
        db, store, teacher_id="T1", source_message_id=source,
        caption=caption, payloads=payloads, now=NOW,
    )


def test_exact_student_id_takes_precedence_and_strips_match_token(media_env):
    db, store, *_ = media_env
    result = archive(db, store, "S1 李明 今天的照片")
    image = db.get(StudentImage, result.image_ids[0])
    assert result.status == "archived"
    assert image.student_id == "S1"
    assert image.caption == "今天的照片"
    assert not list(store.root.glob(".media-*.tmp"))


def test_unique_name_archives_and_ambiguous_name_creates_pending(media_env):
    db, store, *_ = media_env
    unique = archive(db, store, "王芳 作业", source="MSG-unique")
    assert unique.status == "archived" and db.get(StudentImage, unique.image_ids[0]).student_id == "S2"
    ambiguous = archive(db, store, "同学们", source="MSG-ambiguous")
    assert ambiguous.status == "needs_student"
    assert {choice.student_id for choice in ambiguous.choices} == {"S1", "S2"}
    pending = db.scalar(select(PendingMediaAssignment).where(PendingMediaAssignment.source_message_id == "MSG-ambiguous"))
    assert PNG.decode("latin1") not in pending.media_json


def test_partial_student_name_is_not_a_match(media_env):
    db, store, *_ = media_env
    result = archive(db, store, "李明天的作品", source="MSG-partial-name")
    assert result.status == "needs_student"


def test_explicit_unknown_or_unauthorized_student_id_rejects_without_name_fallback(media_env):
    db, store, *_ = media_env
    result = archive(db, store, "S999 李明", source="MSG-bad-id")
    assert result.status == "rejected"
    assert db.scalar(select(PendingMediaAssignment).where(PendingMediaAssignment.source_message_id == "MSG-bad-id")) is None
    assert archive(db, store, "S999 S1", source="MSG-two-ids").status == "rejected"


def test_repeated_archived_and_pending_message_are_idempotent(media_env):
    db, store, *_ = media_env
    first = archive(db, store, "S1", source="MSG-archived")
    repeated = archive(db, store, "S1", source="MSG-archived")
    assert repeated.status == "duplicate" and repeated.image_ids == first.image_ids
    pending = archive(db, store, "unknown", source="MSG-pending")
    again = archive(db, store, "unknown", source="MSG-pending")
    assert again.status == "needs_student" and len(again.choices) == len(pending.choices)
    assert db.query(PendingMediaAssignment).filter_by(source_message_id="MSG-pending").count() == 1


def test_confirmation_rechecks_teacher_scope_and_preserves_positions(media_env):
    db, store, *_ = media_env
    pending = archive(db, store, "", (PNG, PNG), "MSG-confirm")
    result = confirm_pending_media(db, store, teacher_id="T1", pending_id=db.query(PendingMediaAssignment).filter_by(source_message_id="MSG-confirm").one().pending_id, student_id="S1", now=NOW)
    assert result.status == "archived"
    assert [row.source_position for row in db.scalars(select(StudentImage).where(StudentImage.source_message_id == "MSG-confirm").order_by(StudentImage.source_position))] == [0, 1]


def test_expiry_removes_temporary_media(media_env):
    db, store, *_ = media_env
    archive(db, store, "", source="MSG-expire")
    pending = db.query(PendingMediaAssignment).filter_by(source_message_id="MSG-expire").one()
    assert expire_pending_media(db, store, now=NOW + timedelta(minutes=16)) == 1
    assert pending.status == "expired"
    assert pending.temporary_paths_json == "[]"
    assert not list(store.root.iterdir())


def test_confirmation_rejects_revoked_scope_and_removes_temporary_files(media_env):
    db, store, *_ = media_env
    archive(db, store, "", source="MSG-revoked")
    pending = db.query(PendingMediaAssignment).filter_by(source_message_id="MSG-revoked").one()
    paths = __import__("json").loads(pending.temporary_paths_json)
    assignment = db.query(StudentTeacherAssignment).filter_by(student_id="S1", teacher_id="T1").one()
    assignment.status = "revoked"
    db.commit()
    result = confirm_pending_media(db, store, teacher_id="T1", pending_id=pending.pending_id, student_id="S1", now=NOW)
    assert result.status == "rejected"
    assert pending.status == "expired"
    assert all(not (store.root / path).exists() for path in paths)


def test_archival_commit_failure_removes_saved_files_and_rows(media_env, monkeypatch):
    db, store, *_ = media_env
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError):
        archive(db, store, "S1", source="MSG-rollback")
    monkeypatch.setattr(db, "commit", real_commit)
    assert db.query(StudentImage).count() == 0
    assert not list(store.root.rglob("*.png"))
    assert not list(store.root.glob(".media-*.tmp"))


@pytest.mark.parametrize("payload", [b"not-image", b"\x89PNG\r\n\x1a\n" + b"x" * 100])
def test_invalid_or_oversized_media_is_rejected_without_records(media_env, payload):
    db, store, *_ = media_env
    store.max_bytes = 64
    result = archive(db, store, "S1", (payload,), "MSG-invalid")
    assert result.status == "rejected"
    assert db.query(StudentImage).count() == 0
    assert db.query(PendingMediaAssignment).count() == 0
    assert list(store.root.rglob("*")) == []


def test_list_pagination_and_admin_soft_delete_restore(media_env):
    db, store, _teacher, _admin, _students = media_env
    result = archive(db, store, "S1", (PNG, PNG), "MSG-list")
    first = list_student_images(db, "S1", limit=1)
    assert len(first) == 1
    deleted = soft_delete_image(db, store, result.image_ids[0], "ADMIN")
    assert deleted.status == "deleted" and deleted.quarantine_path
    assert list_student_images(db, "S1") == [db.get(StudentImage, result.image_ids[1])]
    restored = restore_image(db, store, deleted.image_id, "ADMIN")
    assert restored.status == "active" and restored.quarantine_path is None
    assert len(list_student_images(db, "S1")) == 2


def test_non_admin_cannot_delete_or_restore(media_env):
    db, store, *_ = media_env
    result = archive(db, store, "S1")
    with pytest.raises(PermissionError):
        soft_delete_image(db, store, result.image_ids[0], "T1")


def test_listing_validates_pagination(media_env):
    db, *_ = media_env
    with pytest.raises(ValueError):
        list_student_images(db, "S1", limit=0)
    with pytest.raises(ValueError):
        list_student_images(db, "S1", offset=-1)
