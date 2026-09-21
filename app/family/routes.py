"""Authenticated web management for student-family access and images."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Student, Teacher
from app.core.auth import is_admin, require_admin, require_login
from app.core.database import get_db
from app.family import invitations, media, relationships
from app.family.models import (
    FamilyConversation,
    FamilyMessage,
    Guardian,
    GuardianInvitation,
    StudentGuardian,
    StudentImage,
    StudentTeacherAssignment,
)
from app.family.permissions import teacher_can_access_student
from app.family.storage import ImageStorageError, LocalImageStore

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(prefix="/family", tags=["family"])
PAGE_SIZE = 24
_SAFE_IMAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")


def get_image_store(request: Request) -> LocalImageStore:
    store = getattr(request.app.state, "family_image_store", None)
    if store is not None:
        return store
    gateway = getattr(request.app.state, "wecom_gateway", None)
    if gateway is not None:
        return gateway.image_store
    config = request.app.state.wecom_config
    store = LocalImageStore(config.media_root, max_bytes=config.media_max_bytes)
    request.app.state.family_image_store = store
    return store


def _student(db: Session, student_id: str) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="学生不存在")
    return student


def _require_student_access(db: Session, teacher: Teacher, student_id: str) -> Student:
    student = _student(db, student_id)
    if not is_admin(teacher) and not teacher_can_access_student(
        db, teacher.teacher_id, student_id
    ):
        raise HTTPException(status_code=403, detail="无权访问该学生")
    return student


def _redirect_student(student_id: str) -> RedirectResponse:
    return RedirectResponse(f"/family/students/{student_id}", status_code=303)


@router.get("/students/{student_id}", response_class=HTMLResponse)
def student_family_page(
    request: Request,
    student_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    student = _require_student_access(db, teacher, student_id)
    admin = is_admin(teacher)
    images = media.list_student_images(
        db, student_id, include_deleted=admin, limit=PAGE_SIZE, offset=0
    )
    guardians = []
    assignments = []
    invitation_rows = []
    conversations = []
    teachers = []
    teacher_names = {}
    if admin:
        guardians = list(db.scalars(
            select(Guardian)
            .join(StudentGuardian, StudentGuardian.guardian_id == Guardian.guardian_id)
            .where(StudentGuardian.student_id == student_id, StudentGuardian.status == "active", Guardian.status == "active")
            .order_by(Guardian.name)
        ).unique())
        assignments = list(db.scalars(
            select(StudentTeacherAssignment)
            .where(StudentTeacherAssignment.student_id == student_id)
            .order_by(StudentTeacherAssignment.status, StudentTeacherAssignment.role, StudentTeacherAssignment.start_date.desc())
        ))
        invitation_rows = list(db.scalars(
            select(GuardianInvitation)
            .where(GuardianInvitation.student_id == student_id)
            .order_by(GuardianInvitation.created_at.desc())
        ))
        teachers = list(db.scalars(select(Teacher).where(Teacher.status == "active").order_by(Teacher.name)))
        teacher_names = {item.teacher_id: item.name for item in db.scalars(select(Teacher))}
        conversations = list(db.scalars(
            select(FamilyConversation)
            .where(FamilyConversation.student_id == student_id)
            .order_by(FamilyConversation.last_message_at.desc())
        ))
    one_time_invitation = request.session.pop("new_guardian_invitation", None)
    if one_time_invitation and one_time_invitation["student_id"] != student_id:
        one_time_invitation = None
    return templates.TemplateResponse(
        request,
        "family/manage.html",
        {
            "student": student,
            "images": images,
            "guardians": guardians,
            "assignments": assignments,
            "invitations": invitation_rows,
            "conversations": conversations,
            "teachers": teachers,
            "teacher_names": teacher_names,
            "is_admin": admin,
            "one_time_invitation": one_time_invitation,
            "today": date.today().isoformat(),
        },
    )


@router.post("/students/{student_id}/teachers")
def assign_student_teacher(
    student_id: str,
    teacher_id: str = Form(...),
    role: str = Form(...),
    start_date: str = Form(...),
    actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        effective = date.fromisoformat(start_date)
        relationships.assign_teacher(db, student_id, teacher_id, role, effective)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _redirect_student(student_id)


@router.post("/teacher-assignments/{assignment_id}/revoke")
def revoke_student_teacher(
    assignment_id: str,
    end_date: str = Form(...),
    _actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
):
    assignment = db.get(StudentTeacherAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="教师关系不存在")
    try:
        relationships.revoke_teacher_assignment(
            db, assignment_id, date.fromisoformat(end_date)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _redirect_student(assignment.student_id)


@router.post("/students/{student_id}/invitations")
def create_invitation(
    request: Request,
    student_id: str,
    actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        invitation, code = invitations.create_guardian_invitation(
            db,
            student_id,
            actor.teacher_id,
            secret_key=request.app.state.secret_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.session["new_guardian_invitation"] = {
        "student_id": student_id,
        "invitation_id": invitation.invitation_id,
        "code": code,
    }
    return _redirect_student(student_id)


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: str,
    actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        invitation = invitations.revoke_guardian_invitation(
            db, invitation_id, actor.teacher_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _redirect_student(invitation.student_id)


@router.get("/images/{image_id}")
def get_student_image(
    image_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
    store: LocalImageStore = Depends(get_image_store),
):
    image = db.get(StudentImage, image_id)
    if image is None or image.status != "active":
        raise HTTPException(status_code=404, detail="图片不存在")
    if not is_admin(teacher) and not teacher_can_access_student(
        db, teacher.teacher_id, image.student_id
    ):
        raise HTTPException(status_code=403, detail="无权查看该学生图片")
    try:
        stream = store.open(image.storage_path)
    except (OSError, ImageStorageError) as exc:
        raise HTTPException(status_code=404, detail="图片文件不存在") from exc

    mime = image.mime_type if image.mime_type in {"image/jpeg", "image/png", "image/webp"} else "application/octet-stream"
    suffix = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "bin")
    safe_id = image_id if _SAFE_IMAGE_ID.fullmatch(image_id) else "student-image"

    def content():
        try:
            while chunk := stream.read(65536):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        content(),
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=0, no-cache",
            "Content-Disposition": f'inline; filename="{safe_id}.{suffix}"',
        },
    )


@router.post("/images/{image_id}/delete")
def delete_student_image(
    image_id: str,
    actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
    store: LocalImageStore = Depends(get_image_store),
):
    try:
        image = media.soft_delete_image(db, store, image_id, actor.teacher_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc
    return _redirect_student(image.student_id)


@router.post("/images/{image_id}/restore")
def restore_student_image(
    image_id: str,
    actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
    store: LocalImageStore = Depends(get_image_store),
):
    try:
        image = media.restore_image(db, store, image_id, actor.teacher_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc
    return _redirect_student(image.student_id)


@router.get("/conversations/{conversation_id}", response_class=HTMLResponse)
def conversation_detail(
    request: Request,
    conversation_id: str,
    _actor: Teacher = Depends(require_admin),
    db: Session = Depends(get_db),
):
    conversation = db.get(FamilyConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = list(db.scalars(
        select(FamilyMessage)
        .where(FamilyMessage.conversation_id == conversation_id)
        .order_by(FamilyMessage.created_at, FamilyMessage.message_id)
    ))
    return templates.TemplateResponse(
        request,
        "family/conversation.html",
        {"conversation": conversation, "messages": messages},
    )
