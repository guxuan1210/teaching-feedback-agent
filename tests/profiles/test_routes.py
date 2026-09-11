"""Profile page routes and teacher scoping."""


def test_profile_page_shows_aggregated_student_data(client, profile_feedback):
    response = client.get(
        "/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07"
    )
    assert response.status_code == 200
    assert "李明" in response.text
    assert "主动检查" in response.text
    assert "知识掌握" in response.text
    assert '<details class="source-details">' in response.text
    assert '<details class="source-details" open>' not in response.text


def test_teacher_cannot_open_student_from_unowned_class(teacher_client, profile_feedback):
    response = teacher_client.get("/profiles/S1?class_id=C1")
    assert response.status_code == 403


def test_profile_index_lists_only_teacher_scoped_students(teacher_client, profile_feedback):
    response = teacher_client.get("/profiles")
    assert response.status_code == 200
    assert "普通学生" in response.text
    assert "李明" not in response.text


def test_profile_index_lists_all_students_for_admin(client, profile_feedback):
    response = client.get("/profiles")
    assert response.status_code == 200
    assert "李明" in response.text


def test_profile_invalid_date_returns_422_and_preserves_filters(client, profile_feedback):
    response = client.get(
        "/profiles/S1?class_id=C1&date_from=not-a-date&date_to=2026-09-07"
    )
    assert response.status_code == 422
    assert "not-a-date" in response.text
    assert "C1" in response.text


def test_profile_reversed_range_returns_422_and_preserves_filters(client, profile_feedback):
    response = client.get(
        "/profiles/S1?class_id=C1&date_from=2026-09-07&date_to=2026-09-01"
    )
    assert response.status_code == 422
    assert "2026-09-07" in response.text
    assert "2026-09-01" in response.text
