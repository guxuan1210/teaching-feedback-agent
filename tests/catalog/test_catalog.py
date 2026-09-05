def test_create_student_and_show_it_in_catalog(client):
    response = client.post("/catalog/students", data={
        "name": "李明", "grade": "三年级", "current_stage": "三阶"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "李明" in response.text


def test_inactive_student_is_not_available_for_new_enrollment(client, student):
    client.post(f"/catalog/students/{student.student_id}/deactivate")
    response = client.get("/catalog/enrollments/new")
    assert student.name not in response.text
