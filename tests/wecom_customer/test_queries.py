from __future__ import annotations

from datetime import datetime, timezone

from app.family.invitations import create_guardian_invitation
from app.family.models import (
    FamilyConversation, FamilyMessage, GuardianChannelBinding, StudentGuardian,
    StudentImage, StudentTeacherAssignment,
)
from app.family.storage import LocalImageStore
from app.reports.models import WeeklyReport
from app.wecom_customer.handler import process_parent_text


SECRET = "test-invitation-secret"

def _admin(db_session, teacher):
    teacher.role = "管理员"
    db_session.commit()

def _bind(db_session, student, teacher, code, external="EXT1", prefix="M"):
    process_parent_text(db_session, object(), secret_key=SECRET,
                        external_user_id=external, message_id=f"{prefix}1", text=code)
    process_parent_text(db_session, object(), secret_key=SECRET,
                        external_user_id=external, message_id=f"{prefix}2", text="母亲")
    return db_session.query(GuardianChannelBinding).filter_by(external_user_id=external).one()


def test_parent_can_switch_only_between_bound_children(db_session, student, teacher, tmp_path):
    from app.catalog.models import Student
    _admin(db_session, teacher)
    child2 = Student(student_id="S2", name="另一个孩子", grade="三年级", current_stage="三阶", status="active")
    db_session.add(child2)
    db_session.commit()
    _, code1 = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    binding = _bind(db_session, student, teacher, code1)
    _, code2 = create_guardian_invitation(db_session, child2.student_id, teacher.teacher_id, secret_key=SECRET)
    process_parent_text(db_session, object(), secret_key=SECRET, external_user_id="EXT1", message_id="M3", text=code2)
    assert db_session.query(FamilyMessage).filter_by(channel_message_id="M3").count() == 0
    assert code2 not in " ".join(row.content for row in db_session.query(FamilyMessage).all())
    process_parent_text(db_session, object(), secret_key=SECRET, external_user_id="EXT1", message_id="M4", text="母亲")

    replies = process_parent_text(db_session, object(), secret_key=SECRET,
                                  external_user_id="EXT1", message_id="M5", text="切换孩子")
    assert "测试学生" in replies[-1].content and "另一个孩子" in replies[-1].content
    switched = process_parent_text(db_session, object(), secret_key=SECRET,
                                   external_user_id="EXT1", message_id="M6", text="1")
    assert "另一个孩子" in switched[-1].content
    assert binding.active_student_id == "S2"
    assert db_session.query(StudentGuardian).count() == 2


def test_recent_images_only_returns_authorized_student_images(db_session, student, teacher, tmp_path):
    _admin(db_session, teacher)
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    image_root = tmp_path / "student_media"
    image_root.mkdir()
    image_path = image_root / "S1" / "photo.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"jpeg-bytes")
    db_session.add(StudentImage(
        image_id="IMG1", student_id="S1", uploaded_by_teacher_id="T1",
        source_message_id="SRC1", source_position=0, caption="课堂作品",
        mime_type="image/jpeg", extension=".jpg", byte_size=10,
        sha256="0" * 64, storage_path="S1/photo.jpg", status="active",
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    ))
    db_session.commit()
    class FakeCustomer:
        def __init__(self):
            self.uploaded = []
            self.sent = []
        def upload_image(self, data, filename):
            self.uploaded.append((data, filename))
            return f"MEDIA-{len(self.uploaded)}"
        def send_image(self, external_user_id, media_id):
            self.sent.append((external_user_id, media_id))

    customer = FakeCustomer()
    replies = process_parent_text(
        db_session, customer, secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="看最近的图片", image_store=LocalImageStore(image_root),
    )
    assert any(reply.image_path == "S1/photo.jpg" for reply in replies)
    with LocalImageStore(image_root).open(next(reply.image_path for reply in replies if reply.image_path)) as saved:
        assert saved.read() == b"jpeg-bytes"
    assert "课堂作品" in " ".join(reply.content for reply in replies)
    assert customer.uploaded == [(b"jpeg-bytes", "IMG1.jpg")]
    assert customer.sent == [("EXT1", "MEDIA-1")]


def test_latest_weekly_report_query_never_returns_draft(db_session, report_scope, report_draft):
    from app.catalog.models import Student, Teacher
    student = db_session.get(Student, "S1")
    teacher = db_session.get(Teacher, "T1")
    _admin(db_session, teacher)
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="看周报",
    )
    assert "暂无已定稿周报" in replies[-1].content


def test_weekly_report_returns_latest_finalized_and_never_draft(db_session, report_scope, report_draft):
    from app.catalog.models import Student, Teacher
    student = db_session.get(Student, "S1")
    teacher = db_session.get(Teacher, "T1")
    _admin(db_session, teacher)
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    db_session.add(WeeklyReport(
        report_id="R-OLD-FINAL", student_id="S1", class_id="C1", teacher_id="T1",
        period_start="2026-08-24", period_end="2026-08-30", generation_mode="template",
        status="finalized", parent_message="旧定稿", summary="旧", strengths="[]",
        concerns="[]", suggestions="[]", finalized_at="2026-08-31T00:00:00+00:00",
    ))
    db_session.add(WeeklyReport(
        report_id="R-NEW-FINAL", student_id="S1", class_id="C1", teacher_id="T1",
        period_start="2026-09-08", period_end="2026-09-14", generation_mode="template",
        status="finalized", parent_message="最新定稿", summary="新", strengths="[]",
        concerns="[]", suggestions="[]", finalized_at="2026-09-15T00:00:00+00:00",
    ))
    db_session.commit()
    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="看周报",
    )
    assert "最新定稿" in replies[-1].content
    assert "旧定稿" not in replies[-1].content
    assert "给家长的话" not in replies[-1].content


def test_recent_feedback_uses_profile_summary(db_session, profile_feedback):
    from app.catalog.models import Student, Teacher
    student = db_session.get(Student, "S1")
    teacher = db_session.get(Teacher, "T1")
    _admin(db_session, teacher)
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="看最近反馈",
    )
    assert "今日表现良好" in replies[-1].content
    assert "已作废备注" not in replies[-1].content


def test_conversation_assignment_uses_only_effective_active_primary_teacher(
    db_session, student, teacher
):
    from datetime import date, timedelta
    from app.catalog.models import Teacher
    _admin(db_session, teacher)
    current = date.today()
    alternate = Teacher(
        teacher_id="T2", name="当前主教", role="晚辅教师", status="active"
    )
    inactive = Teacher(
        teacher_id="T3", name="已停用主教", role="晚辅教师", status="inactive"
    )
    db_session.add_all([alternate, inactive])
    db_session.commit()
    db_session.add_all([
        StudentTeacherAssignment(
            student_id="S1", teacher_id="T1", role="primary",
            start_date=(current + timedelta(days=1)).isoformat(), status="active",
        ),
        StudentTeacherAssignment(
            student_id="S1", teacher_id="T2", role="primary",
            start_date=(current - timedelta(days=10)).isoformat(), status="active",
        ),
        StudentTeacherAssignment(
            student_id="S1", teacher_id="T3", role="primary",
            start_date=(current - timedelta(days=20)).isoformat(), status="active",
        ),
    ])
    db_session.commit()
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="你好",
    )
    conversation = db_session.query(FamilyConversation).one()
    assert conversation.assigned_teacher_id == "T2"


def test_teacher_handoff_is_a_fixed_marker_not_an_ai_answer(db_session, student, teacher):
    _admin(db_session, teacher)
    _, code = create_guardian_invitation(db_session, student.student_id, teacher.teacher_id, secret_key=SECRET)
    _bind(db_session, student, teacher, code)
    replies = process_parent_text(
        db_session, object(), secret_key=SECRET, external_user_id="EXT1",
        message_id="M3", text="请老师回复",
    )
    assert replies[-1].handoff_marker is True
    assert replies[-1].content.startswith("【机器人回复】")
