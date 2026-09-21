"""Student profile pages: a scoped index and a read-only aggregation detail.

Both routes require a logged-in teacher. Admins see every class and student;
ordinary teachers are scoped to the classes they head. The detail route
validates the filter dates before building the profile and, for non-admins,
rejects a ``class_id`` outside the teacher's allowed set with 403.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Enrollment, Teacher
from app.core.auth import is_admin, require_login
from app.core.database import get_db
from app.profiles import service
from app.family.permissions import teacher_can_access_student
from app.family.media import IMAGE_PAGE_SIZE, MAX_IMAGE_OFFSET

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["profiles"])

DAILY_TREND_LABELS = {
    "knowledge": "知识掌握",
    "habit": "学习习惯",
    "mindset": "心态与内驱力",
}

SPECIAL_TREND_LABELS = {
    "skill": "知识／技能",
    "habit": "课堂习惯",
}

MAX_IMAGE_PAGE = MAX_IMAGE_OFFSET // IMAGE_PAGE_SIZE + 1


def _image_filter_date(value: str, field: str) -> date | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"图片{field}日期格式无效") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"图片{field}日期格式无效")
    return parsed


def _image_page_number(value: str) -> int:
    if not value.isdecimal() or len(value) > len(str(MAX_IMAGE_PAGE)):
        raise ValueError("图片页码无效")
    page = int(value)
    if page < 1 or page > MAX_IMAGE_PAGE:
        raise ValueError("图片页码超出范围")
    return page


def _image_page_url(student_id: str, *, class_id: str, date_from: str,
                    date_to: str, image_date_from: str, image_date_to: str,
                    page: int) -> str:
    params = {
        "class_id": class_id,
        "date_from": date_from,
        "date_to": date_to,
        "image_page": str(page),
    }
    if image_date_from:
        params["image_date_from"] = image_date_from
    if image_date_to:
        params["image_date_to"] = image_date_to
    return f"/profiles/{student_id}?{urlencode(params)}"


def _scoped_choices(db: Session, teacher: Teacher):
    if is_admin(teacher):
        return (
            catalog_repository.list_classes(db),
            catalog_repository.list_students(db),
        )
    return (
        catalog_repository.list_classes_for_teacher(db, teacher.teacher_id),
        catalog_repository.list_students_for_teacher(db, teacher.teacher_id),
    )


def _default_range() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=28)).isoformat(), today.isoformat()


def _student_links(db: Session, students, classes):
    """Pair each scoped student with their first in-scope class for quick links."""
    allowed = {c.class_id for c in classes}
    links = []
    for student in students:
        class_id = None
        if allowed:
            class_id = db.scalar(
                select(Enrollment.class_id)
                .where(
                    Enrollment.student_id == student.student_id,
                    Enrollment.class_id.in_(allowed),
                )
                .order_by(Enrollment.start_date)
                .limit(1)
            )
        links.append((student, class_id))
    return links


@router.get("/profiles", response_class=HTMLResponse)
def profile_index(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    classes, students = _scoped_choices(db, teacher)
    date_from, date_to = _default_range()
    return templates.TemplateResponse(
        request,
        "profiles/index.html",
        {
            "classes": classes,
            "students": students,
            "student_links": _student_links(db, students, classes),
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@router.get("/profiles/{student_id}", response_class=HTMLResponse)
def profile_detail(
    request: Request,
    student_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    q = request.query_params
    class_id = q.get("class_id") or ""
    date_from = q.get("date_from") or ""
    date_to = q.get("date_to") or ""

    default_from, default_to = _default_range()
    if not date_from:
        date_from = default_from
    if not date_to:
        date_to = default_to

    classes, students = _scoped_choices(db, teacher)
    image_previous_url = None
    image_next_url = None

    def _render(profile, errors, status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "profiles/detail.html",
            {
                "student_id": student_id,
                "class_id": class_id,
                "date_from": date_from,
                "date_to": date_to,
                "classes": classes,
                "students": students,
                "profile": profile,
                "errors": errors,
                "DAILY_TREND_LABELS": DAILY_TREND_LABELS,
                "SPECIAL_TREND_LABELS": SPECIAL_TREND_LABELS,
                "is_admin": is_admin(teacher),
                "image_date_from": request.query_params.get("image_date_from", ""),
                "image_date_to": request.query_params.get("image_date_to", ""),
                "image_page": request.query_params.get("image_page", "1"),
                "image_page_count": max(1, (profile.image_total + IMAGE_PAGE_SIZE - 1) // IMAGE_PAGE_SIZE) if profile else 1,
                "image_total": profile.image_total if profile else 0,
                "image_previous_url": image_previous_url,
                "image_next_url": image_next_url,
            },
            status_code=status_code,
        )

    if not class_id:
        return _render(None, ["请选择班级"], 422)

    image_date_from_text = q.get("image_date_from", "")
    image_date_to_text = q.get("image_date_to", "")
    try:
        image_date_from = _image_filter_date(image_date_from_text, "开始")
        image_date_to = _image_filter_date(image_date_to_text, "结束")
        image_page = _image_page_number(q.get("image_page", "1"))
    except ValueError as exc:
        return _render(None, [str(exc)], 422)
    if image_date_from is not None and image_date_to is not None and image_date_from > image_date_to:
        return _render(None, ["图片开始日期不能晚于结束日期"], 422)
    image_offset = (image_page - 1) * IMAGE_PAGE_SIZE
    page_count = 1

    try:
        from_date = date.fromisoformat(date_from)
        to_date = date.fromisoformat(date_to)
    except ValueError:
        return _render(None, ["日期格式无效，请使用 YYYY-MM-DD"], 422)

    if from_date > to_date:
        return _render(None, ["开始日期不能晚于结束日期"], 422)

    if not is_admin(teacher):
        allowed = {
            c.class_id
            for c in catalog_repository.list_classes_for_teacher(db, teacher.teacher_id)
        }
        if class_id not in allowed:
            raise HTTPException(status_code=403)

    try:
        profile = service.build_student_profile(
            db,
            student_id=student_id,
            class_id=class_id,
            date_from=date_from,
            date_to=date_to,
            include_family=is_admin(teacher),
            include_deleted_images=is_admin(teacher),
            image_date_from=image_date_from,
            image_date_to=image_date_to,
            image_limit=IMAGE_PAGE_SIZE,
            image_offset=image_offset,
        )
        if not is_admin(teacher) and not teacher_can_access_student(
            db, teacher.teacher_id, student_id
        ):
            profile.images = []
            profile.image_total = 0
            profile.image_uploader_names = {}
        page_count = max(1, (profile.image_total + IMAGE_PAGE_SIZE - 1) // IMAGE_PAGE_SIZE)
        if image_page > page_count:
            return _render(None, ["图片页码超出当前筛选范围"], 422)
        if image_page > 1:
            image_previous_url = _image_page_url(
                student_id, class_id=class_id, date_from=date_from, date_to=date_to,
                image_date_from=image_date_from_text, image_date_to=image_date_to_text,
                page=image_page - 1,
            )
        if image_page < page_count:
            image_next_url = _image_page_url(
                student_id, class_id=class_id, date_from=date_from, date_to=date_to,
                image_date_from=image_date_from_text, image_date_to=image_date_to_text,
                page=image_page + 1,
            )
    except ValueError as exc:
        return _render(None, [str(exc)], 404)

    return _render(profile, [], 200)
