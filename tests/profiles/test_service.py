"""Profile aggregation rules: active feedback only, stable indicator ordering,
and empty periods that never raise."""

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.feedback.models import DailyFeedback, DailyFeedbackIndicator, Indicator
from app.profiles.service import build_student_profile
from app.sessions.models import ClassSession


def test_profile_aggregates_active_feedback_only(db_session, profile_feedback):
    profile = build_student_profile(
        db_session,
        student_id="S1",
        class_id="C1",
        date_from="2026-09-01",
        date_to="2026-09-07",
    )
    assert profile.student.name == "李明"
    assert profile.feedback_count == 2
    assert profile.daily_trends["knowledge"].start == 3
    assert profile.daily_trends["knowledge"].end == 4
    assert profile.daily_trends["knowledge"].change == 1
    assert [(item.text, item.count) for item in profile.strengths] == [
        ("主动检查", 2),
        ("按时完成", 1),
    ]
    assert "已作废备注" not in profile.recent_notes


def test_strengths_tie_break_by_sort_order_then_indicator_id(db_session):
    teacher = Teacher(
        teacher_id="T-ORDER", name="王老师", role="晚辅教师", status="active"
    )
    db_session.add(teacher)
    db_session.commit()

    klass = Class(
        class_id="C-ORDER", name="三年级A班", grade="三年级", class_type="daily",
        head_teacher_id="T-ORDER", status="active",
    )
    db_session.add(klass)
    db_session.commit()

    student = Student(
        student_id="S-ORDER", name="李明", grade="三年级", current_stage="三阶", status="active"
    )
    db_session.add(student)
    db_session.commit()

    db_session.add(
        Enrollment(
            student_id="S-ORDER", class_id="C-ORDER",
            start_date="2026-09-01", status="active",
        )
    )
    db_session.commit()

    db_session.add(
        ClassSession(
            session_id="SESS-ORDER", class_id="C-ORDER", teacher_id="T-ORDER",
            session_type="daily", course_name=None, session_date="2026-09-02",
            start_time="16:30", status="active",
        )
    )
    db_session.commit()

    db_session.add(
        DailyFeedback(
            feedback_id="F-ORDER", session_id="SESS-ORDER", student_id="S-ORDER",
            rating_knowledge=3, rating_habit=3, rating_mindset=3,
            note=None, status="active",
        )
    )
    db_session.commit()

    db_session.add_all([
        Indicator(indicator_id="I-C", category="daily_h_progress", text="排序C",
                  sort_order=1, active=1),
        Indicator(indicator_id="I-A", category="daily_h_progress", text="排序A",
                  sort_order=5, active=1),
        Indicator(indicator_id="I-B", category="daily_h_progress", text="排序B",
                  sort_order=5, active=1),
    ])
    db_session.commit()

    db_session.add_all([
        DailyFeedbackIndicator(feedback_id="F-ORDER", indicator_id="I-C"),
        DailyFeedbackIndicator(feedback_id="F-ORDER", indicator_id="I-A"),
        DailyFeedbackIndicator(feedback_id="F-ORDER", indicator_id="I-B"),
    ])
    db_session.commit()

    profile = build_student_profile(
        db_session,
        student_id="S-ORDER",
        class_id="C-ORDER",
        date_from="2026-09-01",
        date_to="2026-09-07",
    )
    assert [item.indicator_id for item in profile.strengths] == ["I-C", "I-A", "I-B"]


def test_empty_period_returns_zero_feedback(db_session, profile_feedback):
    profile = build_student_profile(
        db_session,
        student_id="S1",
        class_id="C1",
        date_from="2026-09-08",
        date_to="2026-09-14",
    )
    assert profile.feedback_count == 0
    assert profile.daily_trends == {}
    assert profile.special_trends == {}
    assert profile.strengths == []
    assert profile.concerns == []
    assert profile.recent_notes == []
    assert profile.sources == []
