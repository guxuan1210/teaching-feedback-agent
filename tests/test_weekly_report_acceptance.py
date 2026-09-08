def test_feedback_to_profile_and_weekly_report(client, daily_session_one_student):
    client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "4", "rating_habit": "4",
        "rating_mindset": "5", "progress_indicators": ["K001"],
        "note": "能够主动检查作业",
    })
    profile = client.get(
        "/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07"
    )
    assert "能够主动检查作业" in profile.text

    generated = client.post("/reports/generate", data={
        "class_id": "C1", "student_ids": ["S1"],
        "period_start": "2026-09-01", "period_end": "2026-09-07",
        "generation_mode": "template",
    }, follow_redirects=False)
    assert generated.status_code == 303
    detail = client.get(generated.headers["location"])
    assert "能够主动检查" in detail.text
    assert "F" in detail.text
