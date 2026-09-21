from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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
    assert state["invitation_message_id"] == "M1"
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
    receipt = db_session.query(FamilyMessage).filter_by(channel_message_id="M1").one()
    assert receipt.content == "[邀请码已核销]"
    assert code not in receipt.content
    replay = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M1", text=code,
    )
    assert replay[-1].content == "【机器人回复】这条消息已处理。"
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
    assert second[-1].content == first[-1].content
    assert db_session.query(FamilyMessage).filter_by(channel_message_id="M3").count() == 1


def test_concurrent_duplicate_first_contact_creates_one_binding(db_session, engine, monkeypatch):
    from sqlalchemy.orm import Session
    from app.catalog.models import Student
    from app.core.database import build_session_factory

    student = Student(
        student_id="S-CONCURRENT", name="并发学生", grade="一年级",
        current_stage="一阶", status="active",
    )
    db_session.add(student)
    db_session.commit()
    factory = build_session_factory(engine)
    barrier = Barrier(2)
    original_scalar = Session.scalar

    def synchronized_scalar(session, statement, *args, **kwargs):
        result = original_scalar(session, statement, *args, **kwargs)
        if result is None and any(
            table.name == "guardian_channel_binding"
            for table in statement.get_final_froms()
        ):
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(Session, "scalar", synchronized_scalar)

    def send_once():
        with factory() as session:
            return process_parent_text(
                session, object(), secret_key=SECRET, external_user_id="EXT-RACE",
                message_id="MSG-RACE", text="hello",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: send_once(), range(2)))
    assert len(results) == 2
    assert db_session.query(GuardianChannelBinding).filter_by(
        external_user_id="EXT-RACE"
    ).count() == 1
