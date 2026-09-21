from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.family.models import Guardian, StudentImage


FAMILY_TABLES = {
    "guardian",
    "student_guardian",
    "guardian_channel_binding",
    "guardian_invitation",
    "student_teacher_assignment",
    "student_image",
    "pending_media_assignment",
    "family_conversation",
    "family_message",
}


def test_all_family_tables_are_registered_and_created(engine):
    assert FAMILY_TABLES <= set(inspect(engine).get_table_names())


def test_family_tables_expose_required_check_constraints(engine):
    expected_fragments = {
        "guardian": {"relationship_type IN", "status IN"},
        "student_guardian": {"status IN"},
        "guardian_channel_binding": {"status IN"},
        "student_teacher_assignment": {"role IN", "status IN", "end_date IS NULL"},
        "student_image": {"status IN"},
        "pending_media_assignment": {"status IN"},
        "family_conversation": {"status IN"},
        "family_message": {"direction IN", "sender_type IN", "status IN"},
    }
    inspector = inspect(engine)
    for table, fragments in expected_fragments.items():
        sql = " ".join(
            constraint["sqltext"]
            for constraint in inspector.get_check_constraints(table)
        )
        assert all(fragment in sql for fragment in fragments), (table, sql)


def test_family_tables_expose_required_unique_constraints(engine):
    expected = {
        "student_guardian": {("student_id", "guardian_id")},
        "guardian_channel_binding": {("channel", "external_user_id")},
        "guardian_invitation": {("code_hash",)},
        "student_image": {("source_message_id", "source_position")},
        "pending_media_assignment": {("source_message_id",)},
        "family_conversation": {
            ("guardian_id", "student_id", "channel", "channel_conversation_id")
        },
        "family_message": {("channel_message_id",)},
    }
    inspector = inspect(engine)
    for table, constraints in expected.items():
        actual = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table)
        }
        assert constraints <= actual, (table, actual)


def test_guardian_rejects_invalid_relationship_type(db_session):
    db_session.add(
        Guardian(
            guardian_id="G-invalid",
            name="测试家长",
            relationship_type="uncle",
            status="active",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_student_image_source_position_is_unique(
    db_session, student, teacher
):
    common = {
        "student_id": student.student_id,
        "uploaded_by_teacher_id": teacher.teacher_id,
        "source_message_id": "MSG-1",
        "source_position": 0,
        "mime_type": "image/jpeg",
        "extension": ".jpg",
        "byte_size": 128,
        "sha256": "a" * 64,
        "storage_path": "students/S1/one.jpg",
        "status": "active",
    }
    db_session.add(StudentImage(image_id="IMG-1", **common))
    db_session.commit()
    db_session.add(
        StudentImage(
            image_id="IMG-2",
            **{**common, "storage_path": "students/S1/two.jpg"},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
