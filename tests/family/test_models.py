from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.family.models import (
    FamilyConversation,
    FamilyMessage,
    Guardian,
    GuardianChannelBinding,
    GuardianInvitation,
    PendingMediaAssignment,
    StudentGuardian,
    StudentImage,
    StudentTeacherAssignment,
)


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


def test_family_tables_expose_high_frequency_indexes(engine):
    expected = {
        "student_guardian": {("guardian_id", "status")},
        "student_teacher_assignment": {("teacher_id", "student_id", "status")},
        "student_image": {("student_id", "status", "uploaded_at")},
        "family_message": {("conversation_id", "created_at")},
    }
    inspector = inspect(engine)
    for table, indexes in expected.items():
        actual = {
            tuple(index["column_names"])
            for index in inspector.get_indexes(table)
            if not index["unique"]
        }
        assert indexes <= actual, (table, actual)


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


def test_generated_family_business_ids_have_expected_prefixes(
    db_session, student, teacher
):
    guardian = Guardian(name="测试家长", relationship_type="father")
    second_guardian = Guardian(name="另一家长", relationship_type="mother")
    db_session.add_all([guardian, second_guardian])
    db_session.flush()

    rows = [
        guardian,
        StudentGuardian(student_id=student.student_id, guardian_id=guardian.guardian_id),
        GuardianChannelBinding(channel="wechat_customer", external_user_id="EXT-1"),
        GuardianInvitation(
            student_id=student.student_id,
            code_hash="hash-1",
            expires_at="2026-09-22T00:00:00+00:00",
            max_uses=1,
            created_by_teacher_id=teacher.teacher_id,
        ),
        StudentTeacherAssignment(
            student_id=student.student_id,
            teacher_id=teacher.teacher_id,
            role="primary",
            start_date="2026-09-21",
        ),
        StudentImage(
            student_id=student.student_id,
            uploaded_by_teacher_id=teacher.teacher_id,
            source_message_id="MSG-generated",
            source_position=0,
            mime_type="image/jpeg",
            extension="jpg",
            byte_size=3,
            sha256="b" * 64,
            storage_path="S1/generated.jpg",
        ),
        PendingMediaAssignment(
            teacher_id=teacher.teacher_id,
            source_message_id="MSG-pending",
            media_json="[]",
            choices_json="[]",
            temporary_paths_json="[]",
            expires_at="2026-09-22T00:00:00+00:00",
        ),
    ]
    db_session.add_all(rows[1:])
    db_session.flush()
    conversation = FamilyConversation(
        guardian_id=guardian.guardian_id,
        student_id=student.student_id,
        channel="wechat_customer",
        channel_conversation_id="EXT-1",
    )
    db_session.add(conversation)
    db_session.flush()
    message = FamilyMessage(
        conversation_id=conversation.conversation_id,
        direction="inbound",
        sender_type="guardian",
        sender_id=guardian.guardian_id,
        content="你好",
        status="completed",
    )
    db_session.add(message)
    db_session.flush()

    generated = rows + [conversation, message]
    expected_prefixes = ["G_", "SG_", "GCB_", "GI_", "STA_", "IMG_", "PMA_", "FC_", "FM_"]
    ids = [
        guardian.guardian_id,
        rows[1].relation_id,
        rows[2].binding_id,
        rows[3].invitation_id,
        rows[4].assignment_id,
        rows[5].image_id,
        rows[6].pending_id,
        conversation.conversation_id,
        message.message_id,
    ]
    assert all(value.startswith(prefix) for value, prefix in zip(ids, expected_prefixes))
    assert len(set(ids + [second_guardian.guardian_id])) == len(generated) + 1


def test_active_channel_binding_requires_guardian(db_session):
    db_session.add(
        GuardianChannelBinding(
            binding_id="GCB-invalid",
            channel="wechat_customer",
            external_user_id="EXT-invalid",
            status="active",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize("max_uses,used_count", [(0, 0), (1, -1), (1, 2)])
def test_guardian_invitation_rejects_invalid_usage_counts(
    db_session, student, teacher, max_uses, used_count
):
    db_session.add(
        GuardianInvitation(
            invitation_id=f"GI-{max_uses}-{used_count}",
            student_id=student.student_id,
            code_hash=f"hash-{max_uses}-{used_count}",
            expires_at="2026-09-22T00:00:00+00:00",
            max_uses=max_uses,
            used_count=used_count,
            created_by_teacher_id=teacher.teacher_id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "status,deleted_at,with_deleted_by",
    [
        ("active", "2026-09-21T00:00:00+00:00", True),
        ("deleted", None, True),
        ("deleted", "2026-09-21T00:00:00+00:00", False),
    ],
)
def test_student_image_rejects_inconsistent_deletion_metadata(
    db_session, student, teacher, status, deleted_at, with_deleted_by
):
    db_session.add(
        StudentImage(
            image_id=f"IMG-{status}-{deleted_at}-{with_deleted_by}",
            student_id=student.student_id,
            uploaded_by_teacher_id=teacher.teacher_id,
            source_message_id=f"MSG-{status}-{deleted_at}-{with_deleted_by}",
            source_position=0,
            mime_type="image/jpeg",
            extension="jpg",
            byte_size=3,
            sha256="c" * 64,
            storage_path="S1/invalid.jpg",
            status=status,
            deleted_at=deleted_at,
            deleted_by_teacher_id=teacher.teacher_id if with_deleted_by else None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def _family_conversation(db_session, student, teacher):
    guardian = Guardian(
        guardian_id="G-message", name="消息家长", relationship_type="other"
    )
    db_session.add(guardian)
    db_session.flush()
    conversation = FamilyConversation(
        conversation_id="FC-message",
        guardian_id=guardian.guardian_id,
        student_id=student.student_id,
        assigned_teacher_id=teacher.teacher_id,
        channel="wechat_customer",
        channel_conversation_id="EXT-message",
    )
    db_session.add(conversation)
    db_session.flush()
    return guardian, conversation


@pytest.mark.parametrize(
    "direction,sender_type,sender_id,status,failure_reason,sent_at",
    [
        ("inbound", "bot", None, "completed", None, None),
        ("outbound", "guardian", "G-message", "completed", None, "sent"),
        ("inbound", "guardian", None, "completed", None, None),
        ("outbound", "teacher", None, "pending", None, None),
        ("outbound", "bot", "BOT-1", "pending", None, None),
        ("outbound", "bot", None, "failed", None, None),
        ("inbound", "guardian", "G-message", "completed", "unexpected", None),
        ("outbound", "bot", None, "completed", None, None),
        ("outbound", "bot", None, "pending", None, "sent"),
    ],
)
def test_family_message_rejects_inconsistent_cross_field_state(
    db_session,
    student,
    teacher,
    direction,
    sender_type,
    sender_id,
    status,
    failure_reason,
    sent_at,
):
    _, conversation = _family_conversation(db_session, student, teacher)
    db_session.add(
        FamilyMessage(
            message_id="FM-invalid",
            conversation_id=conversation.conversation_id,
            direction=direction,
            sender_type=sender_type,
            sender_id=sender_id,
            content="测试",
            status=status,
            failure_reason=failure_reason,
            sent_at=sent_at,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_inbound_completed_message_does_not_require_sent_at(
    db_session, student, teacher
):
    guardian, conversation = _family_conversation(db_session, student, teacher)
    db_session.add(
        FamilyMessage(
            message_id="FM-inbound-valid",
            conversation_id=conversation.conversation_id,
            direction="inbound",
            sender_type="guardian",
            sender_id=guardian.guardian_id,
            content="测试",
            status="completed",
        )
    )
    db_session.commit()


def test_student_with_image_cannot_be_hard_deleted(
    db_session, student, teacher
):
    image = StudentImage(
        image_id="IMG-protected",
        student_id=student.student_id,
        uploaded_by_teacher_id=teacher.teacher_id,
        source_message_id="MSG-protected",
        source_position=0,
        mime_type="image/jpeg",
        extension="jpg",
        byte_size=3,
        sha256="d" * 64,
        storage_path="S1/protected.jpg",
    )
    db_session.add(image)
    db_session.commit()
    db_session.delete(student)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(StudentImage, image.image_id) is not None


def test_guardian_with_conversation_cannot_be_hard_deleted(
    db_session, student, teacher
):
    guardian, conversation = _family_conversation(db_session, student, teacher)
    db_session.commit()
    db_session.delete(guardian)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(FamilyConversation, conversation.conversation_id) is not None


def test_conversation_with_messages_cannot_be_hard_deleted(
    db_session, student, teacher
):
    guardian, conversation = _family_conversation(db_session, student, teacher)
    message = FamilyMessage(
        message_id="FM-protected",
        conversation_id=conversation.conversation_id,
        direction="inbound",
        sender_type="guardian",
        sender_id=guardian.guardian_id,
        content="历史消息",
        status="completed",
    )
    db_session.add(message)
    db_session.commit()
    db_session.delete(conversation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(FamilyMessage, message.message_id) is not None
