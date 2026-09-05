def test_same_class_can_have_two_sessions_on_same_day(client, seeded_catalog):
    payload = {"class_id": "C1", "teacher_id": "T1", "session_type": "daily", "session_date": "2026-09-05"}
    first = client.post("/sessions", data={**payload, "start_time": "15:30"}, follow_redirects=False)
    second = client.post("/sessions", data={**payload, "start_time": "17:30"}, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] != second.headers["location"]


def test_workspace_uses_roster_effective_on_session_date(client, session_with_roster):
    response = client.get(f"/sessions/{session_with_roster.session_id}")
    assert "李明" in response.text
    assert "已离班学生" not in response.text


def test_special_session_requires_course_name(client, special_classroom, teacher):
    response = client.post("/sessions", data={
        "class_id": special_classroom.class_id, "teacher_id": teacher.teacher_id,
        "session_type": "special", "session_date": "2026-09-05",
        "start_time": "17:30", "course_name": "",
    })
    assert response.status_code == 422


def test_today_page_lists_created_sessions(client, seeded_catalog):
    client.post("/sessions", data={
        "class_id": "C1", "teacher_id": "T1", "session_type": "daily",
        "session_date": "2026-09-05", "start_time": "16:30",
    })
    response = client.get("/?date=2026-09-05")
    assert response.status_code == 200
    assert "三年级A班" in response.text
