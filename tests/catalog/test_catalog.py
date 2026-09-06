from sqlalchemy import text


def test_create_student_and_show_it_in_catalog(client):
    response = client.post("/catalog/students", data={
        "name": "李明", "grade": "三年级", "current_stage": "三阶",
        "late_care_level": "固本A",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "李明" in response.text
    assert "固本A" in response.text


def test_student_form_uses_sorted_stage_and_late_care_dictionaries(client):
    response = client.get("/catalog/students")
    assert response.status_code == 200
    assert response.text.index("零阶｜语言发展关键期启蒙") < response.text.index(
        "九阶｜专属学法与中考自律登顶"
    )
    assert response.text.index("速习蜕变班") < response.text.index("跨学闭环班")


def test_student_rejects_unknown_or_inactive_dictionary_values(client, db_session):
    unknown = client.post("/catalog/students", data={
        "name": "未知字典值", "current_stage": "十阶", "late_care_level": "固本A",
    })
    assert unknown.status_code == 422
    assert "九阶阶段无效" in unknown.text
    assert "十阶（历史值，待修正）" not in unknown.text

    db_session.execute(
        text("UPDATE late_care_level_dict SET active=0 WHERE level_id='固本A'")
    )
    db_session.commit()
    inactive = client.post("/catalog/students", data={
        "name": "停用字典值", "current_stage": "三阶", "late_care_level": "固本A",
    })
    assert inactive.status_code == 422
    assert "晚辅档位无效" in inactive.text


def test_student_dictionary_fields_can_be_blank(client, db_session):
    response = client.post("/catalog/students", data={
        "name": "待测评学生", "current_stage": "", "late_care_level": "",
    }, follow_redirects=False)
    assert response.status_code == 303


def test_legacy_stage_is_shown_as_needing_correction(client, db_session):
    from app.catalog.models import Student

    student = Student(
        student_id="S-LEGACY", name="旧学生", current_stage="自定义阶段", status="active"
    )
    db_session.add(student)
    db_session.commit()

    page = client.get("/catalog/students/S-LEGACY/edit")
    assert page.status_code == 200
    assert "自定义阶段（历史值，待修正）" in page.text

    unchanged = client.post("/catalog/students/S-LEGACY", data={
        "name": "旧学生", "current_stage": "自定义阶段", "late_care_level": "",
    })
    assert unchanged.status_code == 422
    assert "九阶阶段无效" in unchanged.text


def test_inactive_student_is_not_available_for_new_enrollment(client, student):
    client.post(f"/catalog/students/{student.student_id}/deactivate")
    response = client.get("/catalog/enrollments")
    assert student.name not in response.text


def test_edit_can_clear_optional_fields(client, db_session, student, teacher, classroom):
    # Student: blanking grade/current_stage must clear them, not keep old values.
    client.post(f"/catalog/students/{student.student_id}", data={
        "name": student.name, "grade": "", "current_stage": "", "late_care_level": ""
    })
    db_session.expire_all()
    assert student.grade is None
    assert student.current_stage is None
    assert student.late_care_level is None

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
