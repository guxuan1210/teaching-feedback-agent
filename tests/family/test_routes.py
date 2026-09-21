"""Web access control for family relationships and student images."""

from datetime import date
from sqlalchemy import select

from app.catalog.models import Student, Teacher
from app.family.models import FamilyConversation, FamilyMessage, Guardian, GuardianInvitation, StudentImage, StudentTeacherAssignment
from app.family.storage import LocalImageStore


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 24


def _owned_image(teacher_client, tmp_path):
    store = LocalImageStore(tmp_path / "images")
    teacher_client.app.state.family_image_store = store
    saved = store.save("S-OWNED", "IMG-OWNED", [PNG])
    with teacher_client.app.state.session_factory() as db:
        db.add(StudentTeacherAssignment(assignment_id="STA-OWNED", student_id="S-OWNED", teacher_id="T-OTHER", role="primary", start_date="2026-01-01", status="active", origin="manual"))
        db.add(StudentImage(image_id="IMG-OWNED", student_id="S-OWNED", uploaded_by_teacher_id="T-OTHER",
            source_message_id="msg-owned", source_position=0, mime_type=saved.mime_type,
            extension=saved.extension, byte_size=saved.byte_size, sha256=saved.sha256, storage_path=saved.relative_path))
        db.commit()
    return store


def _foreign_image(teacher_client, tmp_path):
    store = LocalImageStore(tmp_path / "foreign-images")
    path = store.save("S-FOR", "IMG-FOREIGN", [PNG])
    with teacher_client.app.state.session_factory() as db:
        db.add(Teacher(teacher_id="T-FOR", name="外班老师", role="晚辅教师", status="active"))
        db.add(Student(student_id="S-FOR", name="外班学生", grade="三年级", status="active"))
        db.flush()
        db.add(StudentTeacherAssignment(assignment_id="STA-FOR", student_id="S-FOR", teacher_id="T-FOR", role="primary", start_date="2026-01-01", status="active", origin="manual"))
        db.add(StudentImage(image_id="IMG-FOREIGN", student_id="S-FOR", uploaded_by_teacher_id="T-FOR", source_message_id="msg-for", source_position=0, mime_type=path.mime_type, extension=path.extension, byte_size=path.byte_size, sha256=path.sha256, storage_path=path.relative_path))
        db.commit()
    teacher_client.app.state.family_image_store = store
    return store


def test_teacher_can_view_owned_student_image(teacher_client, tmp_path):
    _owned_image(teacher_client, tmp_path)
    response = teacher_client.get("/family/images/IMG-OWNED")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private" in response.headers["cache-control"]
    assert "inline; filename=\"IMG-OWNED.png\"" == response.headers["content-disposition"]
    assert response.content == PNG


def test_teacher_cannot_view_foreign_student_image(teacher_client, tmp_path):
    _foreign_image(teacher_client, tmp_path)
    assert teacher_client.get("/family/images/IMG-FOREIGN").status_code == 403


def test_admin_can_view_foreign_student_image(client, tmp_path):
    _foreign_image(client, tmp_path)
    response = client.get("/family/images/IMG-FOREIGN")
    assert response.status_code == 200
    assert response.content == PNG


def test_image_route_requires_login(database_url):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(database_url=database_url)) as anonymous:
        assert anonymous.get("/family/images/IMG-MISSING", follow_redirects=False).status_code == 303


def test_admin_can_create_invitation_and_plain_code_is_shown_once(client, student):
    response = client.post(f"/family/students/{student.student_id}/invitations", follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "请立即复制" in page.text
    assert "明文" not in page.text
    code = page.text.split('class="invitation-code">', 1)[1].split("</strong>", 1)[0]
    assert len(code) == 8
    assert code not in client.get(response.headers["location"]).text
    with client.app.state.session_factory() as db:
        invitation = db.scalar(select(GuardianInvitation))
        assert invitation is not None
        assert len(invitation.code_hash) == 64


def test_teacher_cannot_create_invitation(teacher_client):
    response = teacher_client.post("/family/students/S-OWNED/invitations")
    assert response.status_code == 403


def test_admin_can_revoke_invitation(client, student):
    created = client.post(f"/family/students/{student.student_id}/invitations", follow_redirects=False)
    assert created.status_code == 303
    with client.app.state.session_factory() as db:
        invitation_id = db.scalar(select(GuardianInvitation.invitation_id))
    response = client.post(f"/family/invitations/{invitation_id}/revoke", follow_redirects=False)
    assert response.status_code == 303
    with client.app.state.session_factory() as db:
        assert db.get(GuardianInvitation, invitation_id).revoked_at is not None


def test_student_management_page_scopes_teacher_images(teacher_client, tmp_path):
    _owned_image(teacher_client, tmp_path)
    response = teacher_client.get("/family/students/S-OWNED")
    assert response.status_code == 200
    assert "/family/images/IMG-OWNED" in response.text
    assert "家长关系管理" not in response.text


def test_teacher_cannot_manage_unassigned_student(teacher_client, tmp_path):
    _foreign_image(teacher_client, tmp_path)
    assert teacher_client.get("/family/students/S-FOR").status_code == 403


def test_admin_can_delete_and_restore_image(client, student, tmp_path):
    store = LocalImageStore(tmp_path / "images")
    client.app.state.family_image_store = store
    saved = store.save(student.student_id, "IMG-ADMIN", [PNG])
    with client.app.state.session_factory() as db:
        db.add(StudentImage(image_id="IMG-ADMIN", student_id=student.student_id, uploaded_by_teacher_id="ADMIN", source_message_id="msg-admin", source_position=0, mime_type=saved.mime_type, extension=saved.extension, byte_size=saved.byte_size, sha256=saved.sha256, storage_path=saved.relative_path))
        db.commit()
    deleted = client.post("/family/images/IMG-ADMIN/delete", follow_redirects=False)
    assert deleted.status_code == 303
    with client.app.state.session_factory() as db:
        row = db.get(StudentImage, "IMG-ADMIN")
        assert row.status == "deleted"
        assert row.quarantine_path
    restored = client.post("/family/images/IMG-ADMIN/restore", follow_redirects=False)
    assert restored.status_code == 303
    with client.app.state.session_factory() as db:
        assert db.get(StudentImage, "IMG-ADMIN").status == "active"


def test_teacher_cannot_delete_image(teacher_client, tmp_path):
    _owned_image(teacher_client, tmp_path)
    assert teacher_client.post("/family/images/IMG-OWNED/delete").status_code == 403


def test_admin_can_assign_and_revoke_teacher(client, student, db_session):
    db_session.add(Teacher(teacher_id="T-ASSIGN", name="新增老师", role="晚辅教师", status="active"))
    db_session.commit()
    assigned = client.post(f"/family/students/{student.student_id}/teachers", data={"teacher_id": "T-ASSIGN", "role": "subject", "start_date": date.today().isoformat()}, follow_redirects=False)
    assert assigned.status_code == 303
    with client.app.state.session_factory() as db:
        row = db.scalar(select(StudentTeacherAssignment).where(StudentTeacherAssignment.teacher_id == "T-ASSIGN"))
        assignment_id = row.assignment_id
    revoked = client.post(f"/family/teacher-assignments/{assignment_id}/revoke", data={"end_date": date.today().isoformat()}, follow_redirects=False)
    assert revoked.status_code == 303
    with client.app.state.session_factory() as db:
        assert db.get(StudentTeacherAssignment, assignment_id).status == "revoked"


def test_teacher_cannot_assign_teachers(teacher_client):
    response = teacher_client.post("/family/students/S-OWNED/teachers", data={"teacher_id": "T-OTHER", "role": "subject", "start_date": "2026-09-01"})
    assert response.status_code == 403


def test_admin_conversation_view_shows_sender_labels(client, student, db_session):
    guardian = Guardian(name="家长甲", relationship_type="father", status="active")
    db_session.add(guardian); db_session.flush()
    conversation = FamilyConversation(guardian_id=guardian.guardian_id, student_id=student.student_id, channel="wecom_customer", channel_conversation_id="external-1", status="active")
    db_session.add(conversation); db_session.flush()
    db_session.add_all([
        FamilyMessage(conversation_id=conversation.conversation_id, direction="outbound", sender_type="bot", content="机器人回复", status="pending"),
        FamilyMessage(conversation_id=conversation.conversation_id, direction="outbound", sender_type="teacher", sender_id="ADMIN", content="老师回复", status="pending"),
    ])
    db_session.commit()
    response = client.get(f"/family/conversations/{conversation.conversation_id}")
    assert response.status_code == 200
    assert "机器人" in response.text
    assert "老师" in response.text


def test_teacher_cannot_open_conversation(teacher_client):
    assert teacher_client.get("/family/conversations/FC-UNKNOWN").status_code == 403
