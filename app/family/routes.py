"""Authenticated web management for student-family access and images."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import secrets
from threading import Lock
import time
from urllib.parse import urlencode

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
PAGE_SIZE = media.IMAGE_PAGE_SIZE
MAX_PAGE = media.MAX_IMAGE_OFFSET // PAGE_SIZE + 1
_SAFE_IMAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")
_NOTICE_CACHE_INIT_LOCK = Lock()
_NOTICE_TTL_SECONDS = 300


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


def _notice_cache(request: Request) -> tuple[dict, Lock]:
    cache = getattr(request.app.state, "guardian_invitation_notice_cache", None)
    lock = getattr(request.app.state, "guardian_invitation_notice_lock", None)
    if cache is None or lock is None:
        with _NOTICE_CACHE_INIT_LOCK:
            cache = getattr(request.app.state, "guardian_invitation_notice_cache", None)
            lock = getattr(request.app.state, "guardian_invitation_notice_lock", None)
            if cache is None:
                cache = {}
                request.app.state.guardian_invitation_notice_cache = cache
            if lock is None:
                lock = Lock()
                request.app.state.guardian_invitation_notice_lock = lock
    return cache, lock


def _store_invitation_notice(request: Request, *, nonce: str, notice: dict) -> None:
    cache, lock = _notice_cache(request)
    now = time.monotonic()
    with lock:
        for key, value in list(cache.items()):
            if value["expires_at"] <= now:
                cache.pop(key, None)
        cache[nonce] = {**notice, "expires_at": now + _NOTICE_TTL_SECONDS}


def _consume_invitation_notice(request: Request, nonce: str | None, student_id: str):
    if not nonce:
        return None
    cache, lock = _notice_cache(request)
    with lock:
        notice = cache.pop(nonce, None)
    if (
        notice is None
        or notice["expires_at"] <= time.monotonic()
        or notice["student_id"] != student_id
    ):
        return None
    return notice


def _date_filter(value: str | None, field: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} 日期格式无效") from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail=f"{field} 日期格式无效")
    return parsed


def _image_page(request: Request) -> int:
    value = request.query_params.get("page", "1")
    if not value.isdecimal() or len(value) > len(str(MAX_PAGE)):
        raise HTTPException(status_code=422, detail="图片页码无效")
    page = int(value)
    if page < 1 or page > MAX_PAGE:
        raise HTTPException(status_code=422, detail="图片页码超出范围")
    return page


def _page_url(path: str, *, date_from: str | None, date_to: str | None, page: int) -> str:
    params = {"date_from": date_from or "", "date_to": date_to or "", "page": str(page)}
    return f"{path}?{urlencode(params)}"


@router.get("/students/{student_id}", response_class=HTMLResponse)
def student_family_page(
    request: Request,
    student_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    student = _require_student_access(db, teacher, student_id)
    admin = is_admin(teacher)
    date_from = _date_filter(request.query_params.get("date_from"), "开始")
    date_to = _date_filter(request.query_params.get("date_to"), "结束")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    page = _image_page(request)
    offset = (page - 1) * PAGE_SIZE
    image_total = media.count_student_images(
        db, student_id, include_deleted=admin, date_from=date_from, date_to=date_to
    )
    page_count = max(1, (image_total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > page_count:
        raise HTTPException(status_code=422, detail="图片页码超出当前筛选范围")
    images = media.list_student_images(
        db, student_id, include_deleted=admin, limit=PAGE_SIZE, offset=offset,
        date_from=date_from, date_to=date_to,
    )
    image_uploader_names = media.image_uploader_names(db, images)
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
    nonce = request.session.pop("new_guardian_invitation_nonce", None)
    one_time_invitation = _consume_invitation_notice(request, nonce, student_id)
    page_base = f"/family/students/{student_id}"
    return templates.TemplateResponse(
        request,
        "family/manage.html",
        {
            "student": student,
            "images": images,
            "image_uploader_names": image_uploader_names,
            "image_total": image_total,
            "image_page": page,
            "image_page_count": page_count,
            "image_date_from": date_from.isoformat() if date_from else "",
            "image_date_to": date_to.isoformat() if date_to else "",
            "image_previous_url": _page_url(page_base, date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None, page=page - 1) if page > 1 else None,
            "image_next_url": _page_url(page_base, date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None, page=page + 1) if page < page_count else None,
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
    nonce = secrets.token_urlsafe(32)
    _store_invitation_notice(request, nonce=nonce, notice={
        "student_id": student_id,
        "invitation_id": invitation.invitation_id,
        "code": code,
    })
    request.session["new_guardian_invitation_nonce"] = nonce
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
