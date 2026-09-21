"""Student profile pages: a scoped index and a read-only aggregation detail.

Both routes require a logged-in teacher. Admins see every class and student;
ordinary teachers are scoped to the classes they head. The detail route
validates the filter dates before building the profile and, for non-admins,
rejects a ``class_id`` outside the teacher's allowed set with 403.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

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
            },
            status_code=status_code,
        )

    if not class_id:
        return _render(None, ["请选择班级"], 422)

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
        )
        if not is_admin(teacher) and not teacher_can_access_student(
            db, teacher.teacher_id, student_id
        ):
            profile.images = []
    except ValueError as exc:
        return _render(None, [str(exc)], 404)

    return _render(profile, [], 200)
