def test_history_filters_by_date_class_student_type_and_status(client, mixed_feedback):
    response = client.get(
        "/history?date_from=2026-09-01&date_to=2026-09-30"
        "&class_id=C1&student_id=S1&session_type=daily&status=active"
    )
    assert response.status_code == 200
    # Feedback IDs appear only in the result links (never in filter dropdowns),
    # so they unambiguously prove which records survived filtering.
    assert "F-DAILY-S1" in response.text
    assert "F-DAILY-S2" not in response.text
    assert "F-SPECIAL-S1" not in response.text


def test_void_keeps_record_but_hides_it_from_default_history(client, daily_feedback):
    client.post(f"/history/daily/{daily_feedback.feedback_id}/void")
    default_page = client.get("/history")
    void_page = client.get("/history?status=void")
    assert daily_feedback.feedback_id not in default_page.text
    assert daily_feedback.feedback_id in void_page.text
