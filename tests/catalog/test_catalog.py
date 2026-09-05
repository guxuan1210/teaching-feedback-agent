def test_create_student_and_show_it_in_catalog(client):
    response = client.post("/catalog/students", data={
        "name": "李明", "grade": "三年级", "current_stage": "三阶"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "李明" in response.text


def test_inactive_student_is_not_available_for_new_enrollment(client, student):
    client.post(f"/catalog/students/{student.student_id}/deactivate")
    response = client.get("/catalog/enrollments")
    assert student.name not in response.text


def test_edit_can_clear_optional_fields(client, db_session, student, teacher, classroom):
    # Student: blanking grade/current_stage must clear them, not keep old values.
    client.post(f"/catalog/students/{student.student_id}", data={
        "name": student.name, "grade": "", "current_stage": ""
    })
    db_session.expire_all()
    assert student.grade is None
    assert student.current_stage is None

    # Class: blanking head_teacher_id must clear it.
    client.post(f"/catalog/classes/{classroom.class_id}", data={
        "name": classroom.name, "grade": "", "class_type": classroom.class_type,
        "head_teacher_id": "",
    })
    db_session.expire_all()
    assert classroom.head_teacher_id is None
    assert classroom.grade is None

    # Teacher: blanking role must clear it.
    client.post(f"/catalog/teachers/{teacher.teacher_id}", data={
        "name": teacher.name, "role": ""
    })
    db_session.expire_all()
    assert teacher.role is None
