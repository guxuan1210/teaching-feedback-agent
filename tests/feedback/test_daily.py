from sqlalchemy import func, select

from app.feedback.models import DailyFeedback


def test_save_daily_feedback_and_redirect_to_next_student(client, daily_session_two_students):
    response = client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "4", "rating_habit": "3", "rating_mindset": "5",
        "progress_indicators": ["K001", "H003", "M002"],
        "weak_indicators": ["KW002"], "note": "今天能主动检查。",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?student_id=S2")


def test_invalid_rating_preserves_form_values(client, daily_session_two_students):
    response = client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "6", "rating_habit": "3", "rating_mindset": "5",
        "note": "不要清空这段话"
    })
    assert response.status_code == 422
    assert "不要清空这段话" in response.text
    assert "评分必须是1到5" in response.text


def test_resubmitting_opens_existing_record_instead_of_duplicating(client, daily_session_one_student, db_session):
    payload = {"rating_knowledge": "4", "rating_habit": "4", "rating_mindset": "4"}
    client.post("/sessions/SESSION1/daily/S1", data=payload)
    client.post("/sessions/SESSION1/daily/S1", data=payload)
    count = db_session.scalar(
        select(func.count()).select_from(DailyFeedback).where(
            DailyFeedback.session_id == "SESSION1",
            DailyFeedback.student_id == "S1",
        )
    )
    assert count == 1
