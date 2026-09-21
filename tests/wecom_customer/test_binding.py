from __future__ import annotations

import json

from app.family.invitations import create_guardian_invitation
from app.family.models import FamilyMessage, GuardianChannelBinding, StudentGuardian
from app.wecom_customer.handler import process_parent_text


SECRET = "test-invitation-secret"

def _admin(db_session, teacher):
    teacher.role = "管理员"
    db_session.commit()


def _bind(db_session, student, teacher, code, relationship="父亲", message_id="M2"):
    process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M1", text=code,
    )
    return process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id=message_id, text=relationship,
    )


def test_parent_redeems_invitation_then_selects_relationship(db_session, student, teacher):
    _admin(db_session, teacher)
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key=SECRET
    )

    first = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M1", text=code,
    )
    assert first[-1].content == "请选择身份：父亲、母亲或其他监护人。"
    binding = db_session.query(GuardianChannelBinding).one()
    state = json.loads(binding.pending_state_json)
    assert state["step"] == "awaiting_relationship"
    assert "code" not in state
    assert code not in binding.pending_state_json

    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M2", text="父亲",
    )
    assert replies[-1].content.startswith("【机器人回复】绑定成功")
    assert binding.pending_state_json is None
    assert binding.guardian_id
    assert db_session.query(StudentGuardian).one().student_id == student.student_id
    duplicate = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M2", text="父亲",
    )
    assert duplicate[-1].content == "【机器人回复】这条消息已处理。"
    assert db_session.query(FamilyMessage).filter_by(channel_message_id="M2").count() == 1


def test_parent_cannot_request_unbound_student(db_session, student, teacher):
    _admin(db_session, teacher)
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key=SECRET
    )
    _bind(db_session, student, teacher, code)
    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="查看 S-FOREIGN 最近的图片",
    )
    assert replies[-1].content == "【机器人回复】你没有查看该学生的权限。"


def test_duplicate_bound_message_is_idempotent(db_session, student, teacher):
    _admin(db_session, teacher)
    _invitation, code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id, secret_key=SECRET
    )
    _bind(db_session, student, teacher, code)
    first = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="切换孩子",
    )
    second = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="切换孩子",
    )
    assert second[-1].content == "【机器人回复】这条消息已处理。"
    assert db_session.query(FamilyMessage).filter_by(channel_message_id="M3").count() == 1
