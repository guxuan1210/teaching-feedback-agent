"""Profile page routes and teacher scoping."""

from datetime import date, timedelta


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


def test_admin_profile_includes_student_images_guardians_and_assignments(client, profile_feedback, db_session):
    from app.catalog.models import Teacher
    from app.family.models import Guardian, StudentGuardian, StudentImage, StudentTeacherAssignment

    db_session.add(Teacher(teacher_id="T-SUBJECT", name="科任老师", role="晚辅教师", status="active"))
    guardian = Guardian(name="李明家长", relationship_type="mother", status="active")
    assignment = StudentTeacherAssignment(student_id="S1", teacher_id="T-SUBJECT", role="subject", start_date="2026-09-01", status="active", origin="manual")
    image = StudentImage(image_id="IMG-PROFILE", student_id="S1", uploaded_by_teacher_id="T1", source_message_id="profile-msg", source_position=0, mime_type="image/png", extension="png", byte_size=32, sha256="a" * 64, storage_path="S1/2026/09/IMG-PROFILE.png")
    db_session.add_all([guardian, assignment, image])
    db_session.flush()
    db_session.add(StudentGuardian(student_id="S1", guardian_id=guardian.guardian_id, status="active"))
    db_session.commit()

    response = client.get("/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07")
    assert response.status_code == 200
    assert "/family/images/IMG-PROFILE" in response.text
    assert "李明家长" in response.text
    assert "科任老师" in response.text
    assert "王老师" in response.text


def test_profile_image_list_filters_dates_and_has_bounded_pagination(client, profile_feedback, db_session):
    from app.family.models import StudentImage

    for index in range(27):
        uploaded = date(2026, 9, 1) + timedelta(days=index)
        is_deleted = index == 0
        db_session.add(StudentImage(
            image_id=f"IMG-PAGE-{index:03d}", student_id="S1", uploaded_by_teacher_id="T1",
            source_message_id=f"profile-page-{index:03d}", source_position=0,
            mime_type="image/png", extension="png", byte_size=32, sha256="b" * 64,
            storage_path=f"S1/2026/09/IMG-PAGE-{index:03d}.png",
            uploaded_at=f"{uploaded}T10:00:00+00:00",
            status="deleted" if is_deleted else "active",
            deleted_at="2026-09-28T12:00:00+00:00" if is_deleted else None,
            deleted_by_teacher_id="ADMIN" if is_deleted else None,
            quarantine_path=f"deleted-{index}.png" if is_deleted else None,
        ))
    db_session.commit()
    base = "/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07"

    filtered = client.get(base + "&image_date_from=2026-09-25&image_date_to=2026-09-25")
    assert filtered.status_code == 200
    assert "IMG-PAGE-024" in filtered.text
    assert "IMG-PAGE-023" not in filtered.text
    first = client.get(base)
    assert first.text.count('class="student-image-card') == 24
    assert "图片已删除" not in first.text
    second = client.get(base + "&image_page=2")
    assert second.text.count('class="student-image-card') == 3
    assert "图片已删除" in second.text
    assert "王老师" in second.text
    assert client.get(base + "&image_page=4168").status_code == 422


def test_teacher_profile_includes_images_but_not_family_management(teacher_client, tmp_path):
    from app.family.models import Guardian
    from app.family.models import StudentGuardian, StudentImage, StudentTeacherAssignment

    store = __import__("app.family.storage", fromlist=["LocalImageStore"]).LocalImageStore(tmp_path / "images")
    teacher_client.app.state.family_image_store = store
    saved = store.save("S-OWNED", "IMG-TEACHER", [b"\x89PNG\r\n\x1a\n" + b"x" * 24])
    with teacher_client.app.state.session_factory() as db:
        db.add(StudentTeacherAssignment(assignment_id="STA-PROFILE", student_id="S-OWNED", teacher_id="T-OTHER", role="primary", start_date="2026-01-01", status="active", origin="manual"))
        db.add(StudentImage(image_id="IMG-TEACHER", student_id="S-OWNED", uploaded_by_teacher_id="T-OTHER", source_message_id="teacher-msg", source_position=0, mime_type=saved.mime_type, extension=saved.extension, byte_size=saved.byte_size, sha256=saved.sha256, storage_path=saved.relative_path))
        db.add(StudentImage(image_id="IMG-TEACHER-DELETED", student_id="S-OWNED", uploaded_by_teacher_id="T-OTHER", source_message_id="teacher-deleted-msg", source_position=0, mime_type="image/png", extension="png", byte_size=32, sha256="c" * 64, storage_path="S-OWNED/deleted.png", status="deleted", deleted_at="2026-09-20T00:00:00+00:00", deleted_by_teacher_id="ADMIN", quarantine_path="teacher-deleted.png"))
        guardian = Guardian(name="保密家长", relationship_type="father", status="active")
        db.add(guardian)
        db.flush()
        db.add(StudentGuardian(student_id="S-OWNED", guardian_id=guardian.guardian_id, status="active"))
        db.commit()

    response = teacher_client.get("/profiles/S-OWNED?class_id=C-OWNED&date_from=2026-09-01&date_to=2026-09-22")
    assert response.status_code == 200
    assert "/family/images/IMG-TEACHER" in response.text
    assert "普通老师" in response.text
    assert "IMG-TEACHER-DELETED" not in response.text
    assert "保密家长" not in response.text
    assert "家长关系管理" not in response.text
