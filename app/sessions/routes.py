"""Today page, session creation, and the batch workspace shell."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.core.database import get_db
from app.sessions import repository

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["sessions"])


# ---------------------------------------------------------------------------
# Today page (GET /)
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, session: Session = Depends(get_db)):
    selected = request.query_params.get("date", "")
    try:
        on_date = date.fromisoformat(selected)
    except ValueError:
        on_date = date.today()

    sessions = repository.list_sessions_for_date(session, on_date)
    return templates.TemplateResponse(
        request,
        "today.html",
        {
            "on_date": on_date.isoformat(),
            "sessions": sessions,
            "classes": catalog_repository.list_classes(session, active_only=True),
            "teachers": catalog_repository.list_teachers(session, active_only=True),
            "form": {
                "session_type": "daily",
                "class_id": "",
                "teacher_id": "",
                "course_name": "",
                "start_time": datetime.now().strftime("%H:%M"),
            },
            "errors": [],
        },
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
    session: Session = Depends(get_db),
):
    form = {
        "session_type": session_type,
        "class_id": class_id,
        "teacher_id": teacher_id,
        "course_name": course_name,
        "start_time": start_time,
    }

    def _error(exc: ValueError):
        return templates.TemplateResponse(
            request,
            "today.html",
            {
                "on_date": session_date,
                "sessions": [],
                "classes": catalog_repository.list_classes(session, active_only=True),
                "teachers": catalog_repository.list_teachers(session, active_only=True),
                "form": form,
                "errors": [str(exc)],
            },
            status_code=422,
        )

    try:
        parsed_date = date.fromisoformat(session_date)
        parsed_time = time.fromisoformat(start_time)
    except ValueError as exc:
        return _error(exc)

    try:
        created = repository.create_session(
            session,
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


# ---------------------------------------------------------------------------
# Batch workspace (GET /sessions/{session_id})
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def workspace_page(
    request: Request, session_id: str, session: Session = Depends(get_db)
):
    try:
        workspace = repository.get_session_with_roster(session, session_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "today.html",
            {
                "on_date": date.today().isoformat(),
                "sessions": [],
                "classes": catalog_repository.list_classes(session, active_only=True),
                "teachers": catalog_repository.list_teachers(session, active_only=True),
                "form": {},
                "errors": [str(exc)],
            },
            status_code=404,
        )

    roster_ids = [s.student_id for s in workspace.roster]
    selected = request.query_params.get("student_id")
    if selected not in roster_ids:
        selected = next(
            (s.student_id for s in workspace.roster if workspace.statuses.get(s.student_id) != "已填写"),
            workspace.roster[0].student_id if workspace.roster else None,
        )

    return templates.TemplateResponse(
        request,
        "feedback/workspace.html",
        {
            "workspace": workspace,
            "selected_student_id": selected,
            "errors": [],
        },
    )
