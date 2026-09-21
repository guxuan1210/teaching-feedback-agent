from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.sql.dml import Update

from app.catalog.models import Student, Teacher
from app.core.database import build_session_factory
from app.family.invitations import (
    create_guardian_invitation,
    redeem_guardian_invitation,
    revoke_guardian_invitation,
)
from app.family.models import (
    Guardian,
    GuardianChannelBinding,
    GuardianInvitation,
    StudentGuardian,
    StudentTeacherAssignment,
)
from app.family.permissions import list_students_for_guardian


NOW = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
SECRET = "test-secret"


def _scope(db, *, teacher_role="管理员"):
    teacher = Teacher(teacher_id="T1", name="老师", role=teacher_role, status="active")
    student = Student(student_id="S1", name="学生", status="active")
    db.add_all([teacher, student])
    db.commit()
    db.add(
        StudentTeacherAssignment(
            student_id="S1", teacher_id="T1", role="primary",
            start_date="2026-09-01", status="active",
        )
    )
    db.commit()
    return teacher, student


def test_create_invitation_stores_only_digest_and_defaults_to_once_for_seven_days(db_session):
    _scope(db_session)

    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )

    assert len(code) == 8 and code.isalnum() and code == code.upper()
    assert invitation.code_hash != code
    assert code not in invitation.code_hash
    assert invitation.max_uses == 1
    assert invitation.used_count == 0
    assert datetime.fromisoformat(invitation.expires_at) == NOW + timedelta(days=7)
    assert not any(code in str(value) for value in vars(invitation).values())


@pytest.mark.parametrize("relationship", ["father", "母亲", "其他监护人"])
def test_redeem_creates_guardian_binding_and_relationship(db_session, relationship):
    _scope(db_session)
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )

    guardian = redeem_guardian_invitation(
        db_session, code.lower(), "WX-1", relationship, "王家长",
        secret_key=SECRET, now=NOW + timedelta(minutes=1),
    )

    db_session.refresh(invitation)
    binding = db_session.query(GuardianChannelBinding).one()
    relation = db_session.query(StudentGuardian).one()
    assert guardian.relationship_type in {"father", "mother", "other"}
    assert (binding.channel, binding.external_user_id, binding.guardian_id) == (
        "wecom_customer", "WX-1", guardian.guardian_id
    )
    assert binding.status == "active"
    assert binding.active_student_id == "S1"
    assert binding.pending_state_json is None
    assert relation.status == "active"
    assert invitation.used_count == 1


@pytest.mark.parametrize("mode", ["expired", "revoked", "used", "wrong"])
def test_redeem_rejects_unusable_or_wrong_codes(db_session, mode):
    _scope(db_session, teacher_role="管理员")
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW,
        ttl=timedelta(minutes=5),
    )
    when = NOW + timedelta(minutes=1)
    attempted = code
    if mode == "expired":
        when = NOW + timedelta(minutes=6)
    elif mode == "revoked":
        revoke_guardian_invitation(db_session, invitation.invitation_id, "T1")
    elif mode == "used":
        redeem_guardian_invitation(
            db_session, code, "WX-FIRST", "father", "甲",
            secret_key=SECRET, now=when,
        )
    else:
        attempted = "ZZZZZZZZ"

    with pytest.raises(ValueError):
        redeem_guardian_invitation(
            db_session, attempted, "WX-2", "mother", "乙",
            secret_key=SECRET, now=when,
        )
    assert db_session.query(Guardian).count() == (1 if mode == "used" else 0)


def test_distinct_invitations_bind_distinct_guardians_to_one_student(db_session):
    _scope(db_session)
    first, code1 = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    second, code2 = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)

    father = redeem_guardian_invitation(
        db_session, code1, "WX-F", "父亲", "父亲", secret_key=SECRET, now=NOW
    )
    mother = redeem_guardian_invitation(
        db_session, code2, "WX-M", "母亲", "母亲", secret_key=SECRET, now=NOW
    )

    assert father.guardian_id != mother.guardian_id
    assert db_session.query(StudentGuardian).count() == 2
    assert (first.used_count, second.used_count) == (1, 1)


def test_existing_binding_is_reused_for_second_child_and_lists_both(db_session):
    _scope(db_session)
    second_student = Student(student_id="S2", name="二宝", status="active")
    db_session.add(second_student)
    db_session.commit()
    db_session.add(
        StudentTeacherAssignment(
            student_id="S2", teacher_id="T1", role="primary",
            start_date="2026-09-01", status="active",
        )
    )
    db_session.commit()
    _, code1 = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    _, code2 = create_guardian_invitation(db_session, "S2", "T1", secret_key=SECRET, now=NOW)
    guardian1 = redeem_guardian_invitation(
        db_session, code1, "WX-ONE", "father", "王先生", secret_key=SECRET, now=NOW
    )
    guardian2 = redeem_guardian_invitation(
        db_session, code2, "WX-ONE", "父亲", "王先生", secret_key=SECRET, now=NOW
    )

    assert guardian1.guardian_id == guardian2.guardian_id
    assert db_session.query(Guardian).count() == 1
    assert [s.student_id for s in list_students_for_guardian(db_session, guardian1.guardian_id)] == [
        "S2", "S1"
    ]


def test_existing_binding_rejects_conflicting_identity(db_session):
    _scope(db_session)
    _, code1 = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    second_invitation, code2 = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )
    redeem_guardian_invitation(
        db_session, code1, "WX-ONE", "father", "王先生", secret_key=SECRET, now=NOW
    )

    with pytest.raises(ValueError, match="身份.*冲突"):
        redeem_guardian_invitation(
            db_session, code2, "WX-ONE", "mother", "王女士", secret_key=SECRET, now=NOW
        )
    db_session.refresh(second_invitation)
    assert second_invitation.used_count == 0


def test_existing_binding_rejects_name_conflict_without_mutation(db_session):
    _scope(db_session)
    guardian = Guardian(name="王先生", relationship_type="father", status="active")
    db_session.add(guardian)
    db_session.commit()
    binding = GuardianChannelBinding(
        channel="wecom_customer", external_user_id="WX-NAME-CONFLICT",
        guardian_id=guardian.guardian_id, active_student_id=None,
        pending_state_json='{"step":"invite"}', status="active",
    )
    db_session.add(binding)
    db_session.commit()
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )

    with pytest.raises(ValueError, match="身份.*冲突"):
        redeem_guardian_invitation(
            db_session, code, "WX-NAME-CONFLICT", "father", " 李先生 ",
            secret_key=SECRET, now=NOW,
        )

    db_session.refresh(invitation)
    db_session.refresh(binding)
    db_session.refresh(guardian)
    assert invitation.used_count == 0
    assert guardian.name == "王先生"
    assert binding.guardian_id == guardian.guardian_id
    assert binding.active_student_id is None
    assert binding.pending_state_json == '{"step":"invite"}'
    assert binding.status == "active"
    assert db_session.query(StudentGuardian).count() == 0


def test_revoked_binding_reuses_guardian_and_rejects_identity_conflict(db_session):
    _scope(db_session)
    guardian = Guardian(name="王先生", relationship_type="father", status="active")
    db_session.add(guardian)
    db_session.commit()
    binding = GuardianChannelBinding(
        channel="wecom_customer", external_user_id="WX-REVOKED",
        guardian_id=guardian.guardian_id, status="revoked",
    )
    db_session.add(binding)
    db_session.commit()
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )

    redeemed = redeem_guardian_invitation(
        db_session, code, "WX-REVOKED", "父亲", "王先生",
        secret_key=SECRET, now=NOW,
    )

    assert redeemed.guardian_id == guardian.guardian_id
    assert db_session.query(Guardian).count() == 1
    assert binding.guardian_id == guardian.guardian_id
    assert binding.status == "active"
    assert binding.bound_at == NOW.isoformat()
    assert invitation.used_count == 1

    conflict_invitation, conflict_code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )
    binding.status = "revoked"
    db_session.commit()
    with pytest.raises(ValueError, match="身份.*冲突"):
        redeem_guardian_invitation(
            db_session, conflict_code, "WX-REVOKED", "mother", "王女士",
            secret_key=SECRET, now=NOW,
        )
    assert binding.guardian_id == guardian.guardian_id
    assert binding.status == "revoked"
    assert db_session.query(Guardian).count() == 1
    db_session.refresh(conflict_invitation)
    assert conflict_invitation.used_count == 0


def test_redeem_restores_revoked_student_guardian(db_session):
    _scope(db_session)
    guardian = Guardian(name="王先生", relationship_type="father", status="active")
    db_session.add(guardian)
    db_session.commit()
    db_session.add(
        GuardianChannelBinding(
            channel="wecom_customer", external_user_id="WX-RESTORE",
            guardian_id=guardian.guardian_id, status="active",
        )
    )
    relation = StudentGuardian(
        student_id="S1", guardian_id=guardian.guardian_id,
        status="revoked", revoked_at=NOW.isoformat(),
    )
    db_session.add(relation)
    db_session.commit()
    _, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )

    redeem_guardian_invitation(
        db_session, code, "WX-RESTORE", "father", "王先生",
        secret_key=SECRET, now=NOW,
    )

    assert relation.status == "active"
    assert relation.revoked_at is None


def test_failed_final_invitation_claim_rolls_back_prepared_relationships(
    db_session, monkeypatch
):
    _scope(db_session)
    _, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )
    real_execute = db_session.execute
    observed: dict[str, int] = {}

    class LostClaim:
        rowcount = 0

    def execute_with_lost_claim(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "guardian_invitation":
            observed["guardians"] = db_session.query(Guardian).count()
            observed["bindings"] = db_session.query(GuardianChannelBinding).count()
            observed["relations"] = db_session.query(StudentGuardian).count()
            return LostClaim()
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", execute_with_lost_claim)
    with pytest.raises(ValueError, match="邀请码.*使用|邀请码.*失效"):
        redeem_guardian_invitation(
            db_session, code, "WX-RACE", "father", "王先生",
            secret_key=SECRET, now=NOW,
        )

    assert observed == {"guardians": 1, "bindings": 1, "relations": 1}
    assert db_session.query(Guardian).count() == 0
    assert db_session.query(GuardianChannelBinding).count() == 0
    assert db_session.query(StudentGuardian).count() == 0


def test_create_validates_inputs_status_and_teacher_scope(db_session):
    teacher, student = _scope(db_session)
    with pytest.raises(ValueError, match="密钥"):
        create_guardian_invitation(db_session, "S1", "T1", secret_key="", now=NOW)

    teacher2 = Teacher(teacher_id="T2", name="无权限", role="晚辅教师", status="active")
    db_session.add(teacher2)
    db_session.commit()
    with pytest.raises(ValueError, match="管理员|校区负责人"):
        create_guardian_invitation(db_session, "S1", "T2", secret_key=SECRET, now=NOW)

    student.status = "inactive"
    db_session.commit()
    with pytest.raises(ValueError, match="学生.*停用"):
        create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)

    student.status = "active"
    teacher.status = "inactive"
    db_session.commit()
    with pytest.raises(ValueError, match="教师.*停用"):
        create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)


def test_regular_teacher_cannot_create_invitation_even_with_assignment(db_session):
    _scope(db_session, teacher_role="晚辅教师")
    with pytest.raises(ValueError, match="管理员|校区负责人"):
        create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)


@pytest.mark.parametrize("role", ["管理员", "校区负责人"])
def test_admin_roles_can_create_invitation(db_session, role):
    _scope(db_session, teacher_role=role)
    invitation, _ = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    assert invitation.created_by_teacher_id == "T1"


def test_invitation_rejects_naive_datetimes(db_session):
    _scope(db_session)
    naive = datetime(2026, 9, 21, 8, 0)
    with pytest.raises(ValueError, match="时区"):
        create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=naive)
    _, code = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    with pytest.raises(ValueError, match="时区"):
        redeem_guardian_invitation(db_session, code, "WX-NAIVE", "father", "家长", secret_key=SECRET, now=naive)


def test_timezone_is_normalized_to_utc_and_exact_expiry_is_invalid(db_session):
    _scope(db_session)
    local_now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=local_now, ttl=timedelta(minutes=5)
    )
    assert invitation.created_at == "2026-09-21T08:00:00+00:00"
    assert invitation.expires_at == "2026-09-21T08:05:00+00:00"
    with pytest.raises(ValueError, match="过期"):
        redeem_guardian_invitation(
            db_session, code, "WX-BOUNDARY", "father", "家长",
            secret_key=SECRET, now=datetime(2026, 9, 21, 16, 5, tzinfo=timezone(timedelta(hours=8))),
        )


def test_real_unique_binding_race_rolls_back_and_keeps_session_usable(db_session, engine):
    _scope(db_session)
    invitation, code = create_guardian_invitation(
        db_session, "S1", "T1", secret_key=SECRET, now=NOW
    )
    rival = build_session_factory(engine)()

    def insert_rival_binding(session, flush_context, instances):
        rival_guardian = Guardian(name="另一身份", relationship_type="mother", status="active")
        rival.add(rival_guardian)
        rival.flush()
        rival.add(GuardianChannelBinding(
            channel="wecom_customer", external_user_id="WX-RACE-REAL",
            guardian_id=rival_guardian.guardian_id, status="active",
        ))
        rival.commit()

    event.listen(db_session, "before_flush", insert_rival_binding, once=True)
    try:
        with pytest.raises(ValueError, match="身份.*冲突"):
            redeem_guardian_invitation(
                db_session, code, "WX-RACE-REAL", "father", "王先生",
                secret_key=SECRET, now=NOW,
            )
    finally:
        rival.close()

    db_session.refresh(invitation)
    assert invitation.used_count == 0
    assert db_session.query(Guardian).count() == 1
    assert db_session.query(StudentGuardian).count() == 0
    db_session.query(Guardian).one().updated_at = (NOW + timedelta(minutes=1)).isoformat()
    db_session.commit()


def test_redeem_validates_blank_fields_and_relationship(db_session):
    _scope(db_session)
    _, code = create_guardian_invitation(db_session, "S1", "T1", secret_key=SECRET, now=NOW)
    for attempted_code, relationship, name, match in [
        ("", "father", "家长", "邀请码"),
        (code, "uncle", "家长", "关系"),
        (code, "father", "", "姓名"),
    ]:
        with pytest.raises(ValueError, match=match):
            redeem_guardian_invitation(
                db_session, attempted_code, "WX-1", relationship, name,
                secret_key=SECRET, now=NOW,
            )


def test_redeem_rejects_non_ascii_code_with_clear_error(db_session):
    _scope(db_session)

    with pytest.raises(ValueError, match="邀请码无效"):
        redeem_guardian_invitation(
            db_session, "错误邀请码", "WX-1", "father", "家长",
            secret_key=SECRET, now=NOW,
        )


def test_only_active_admin_can_revoke_and_repeated_revoke_is_idempotent(db_session):
    _scope(db_session, teacher_role="晚辅教师")
    admin = Teacher(teacher_id="ADMIN", name="管理员", role="校区负责人", status="active")
    db_session.add(admin)
    db_session.commit()
    invitation, _ = create_guardian_invitation(
        db_session, "S1", "ADMIN", secret_key=SECRET, now=NOW
    )

    with pytest.raises(ValueError, match="管理员"):
        revoke_guardian_invitation(db_session, invitation.invitation_id, "T1")
    first = revoke_guardian_invitation(db_session, invitation.invitation_id, "ADMIN")
    revoked_at = first.revoked_at
    second = revoke_guardian_invitation(db_session, invitation.invitation_id, "ADMIN")
    assert second.revoked_at == revoked_at
