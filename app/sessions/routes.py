"""Today page and session creation, scoped to the logged-in teacher."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Teacher
from app.core.auth import is_admin, require_login
from app.core.database import get_db
from app.sessions import repository

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["sessions"])


def _scoped_choices(db: Session, teacher: Teacher):
    if is_admin(teacher):
        return (
            catalog_repository.list_classes(db, active_only=True),
            catalog_repository.list_teachers(db, active_only=True),
        )
    return (
        catalog_repository.list_classes_for_teacher(db, teacher.teacher_id),
        [teacher],
    )


def _today_context(
    request: Request,
    db: Session,
    on_date: date,
    teacher: Teacher,
    form: dict,
    errors: list[str],
) -> dict:
    classes, teachers = _scoped_choices(db, teacher)
    teacher_id = None if is_admin(teacher) else teacher.teacher_id
    return {
        "on_date": on_date.isoformat(),
        "sessions": repository.list_sessions_for_date(db, on_date, teacher_id=teacher_id),
        "classes": classes,
        "teachers": teachers,
        "form": form,
        "errors": errors,
    }


def _empty_form() -> dict:
    return {
        "session_type": "daily",
        "class_id": "",
        "teacher_id": "",
        "course_name": "",
        "start_time": datetime.now().strftime("%H:%M"),
    }


# ---------------------------------------------------------------------------
# Today page (GET /)
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def today_page(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    selected = request.query_params.get("date", "")
    try:
        on_date = date.fromisoformat(selected)
    except ValueError:
        on_date = date.today()

    return templates.TemplateResponse(
        request,
        "today.html",
        _today_context(request, db, on_date, teacher, _empty_form(), []),
    )


# ---------------------------------------------------------------------------
# Create session (POST /sessions)
# ---------------------------------------------------------------------------
@router.post("/sessions")
def sessions_create(
    request: Request,
    class_id: str = Form(...),
    teacher_id: str = Form(...),
    session_type: str = Form(...),
    session_date: str = Form(...),
    start_time: str = Form(...),
    course_name: str = Form(""),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    form = {
        "session_type": session_type,
        "class_id": class_id,
        "teacher_id": teacher_id,
        "course_name": course_name,
        "start_time": start_time,
    }

    def _error(exc: ValueError):
        try:
            on_date = date.fromisoformat(session_date)
        except ValueError:
            on_date = date.today()
        return templates.TemplateResponse(
            request,
            "today.html",
            _today_context(request, db, on_date, teacher, form, [str(exc)]),
            status_code=422,
        )

    if not is_admin(teacher):
        teacher_id = teacher.teacher_id
        allowed = {c.class_id for c in catalog_repository.list_classes_for_teacher(db, teacher.teacher_id)}
        if class_id not in allowed:
            return _error(ValueError("只能为自己负责的班级创建场次"))

    try:
        parsed_date = date.fromisoformat(session_date)
        parsed_time = time.fromisoformat(start_time)
    except ValueError as exc:
        return _error(exc)

    try:
        created = repository.create_session(
            db,
            class_id=class_id,
            teacher_id=teacher_id,
            session_type=session_type,
            session_date=parsed_date,
            start_time=parsed_time,
            course_name=course_name,
        )
    except ValueError as exc:
        return _error(exc)
    return RedirectResponse(f"/sessions/{created.session_id}", status_code=303)
