"""Batch workspace rendering and feedback save actions."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.feedback import service
from app.feedback.forms import DailyFeedbackInput, SpecialFeedbackInput
from app.feedback.models import (
    DailyFeedback,
    DailyFeedbackIndicator,
    SpecialFeedback,
    SpecialFeedbackIndicator,
)
from app.sessions import repository as sessions_repository
from app.sessions.models import ClassSession

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["feedback"])

CATEGORY_LABELS = {
    "daily_k_progress": "知识进步项",
    "daily_k_weak": "知识薄弱项",
    "daily_h_progress": "习惯进步项",
    "daily_h_weak": "习惯薄弱项",
    "daily_m_progress": "心态进步项",
    "daily_m_weak": "心态薄弱项",
    "special_k_progress": "已掌握项",
    "special_k_weak": "薄弱项",
    "special_h_progress": "课堂优秀表现",
    "special_h_weak": "待改进项",
}


def _rating_fields(is_daily: bool) -> list[str]:
    if is_daily:
        return ["rating_knowledge", "rating_habit", "rating_mindset"]
    return ["rating_skill", "rating_habit"]


def _rating_labels(is_daily: bool) -> dict[str, str]:
    if is_daily:
        return {
            "rating_knowledge": "知识掌握",
            "rating_habit": "学习习惯",
            "rating_mindset": "心态与内驱力",
        }
    return {"rating_skill": "知识／技能", "rating_habit": "课堂习惯"}


def _empty_form(is_daily: bool) -> dict:
    return {field: "" for field in _rating_fields(is_daily)} | {"note": ""}


def _load_existing(db: Session, session: ClassSession, student_id: str):
    if session.session_type == "daily":
        model, junction = DailyFeedback, DailyFeedbackIndicator
        fields = _rating_fields(True)
    else:
        model, junction = SpecialFeedback, SpecialFeedbackIndicator
        fields = _rating_fields(False)
    feedback = db.scalar(
        select(model).where(
            model.session_id == session.session_id,
            model.student_id == student_id,
            model.status == "active",
        )
    )
    if feedback is None:
        return None, None
    selected_ids = set(
        db.scalars(select(junction.indicator_id).where(junction.feedback_id == feedback.feedback_id))
    )
    form = {field: str(getattr(feedback, field)) for field in fields} | {
        "note": feedback.note or ""
    }
    return form, selected_ids


def _workspace_context(
    request: Request,
    db: Session,
    session_id: str,
    form: dict | None = None,
    selected_ids: set[str] | None = None,
    errors: list[str] | None = None,
    selected_student_id: str | None = None,
) -> dict:
    workspace = sessions_repository.get_session_with_roster(db, session_id)
    is_daily = workspace.session.session_type == "daily"
    categories = service.DAILY_CATEGORIES if is_daily else service.SPECIAL_CATEGORIES
    indicator_groups = service.indicators_by_category(db, categories)

    roster_ids = [s.student_id for s in workspace.roster]
    selected = selected_student_id or request.query_params.get("student_id")
    if selected not in roster_ids:
        selected = next(
            (s.student_id for s in workspace.roster if workspace.statuses.get(s.student_id) != "已填写"),
            roster_ids[0] if roster_ids else None,
        )

    if form is None and selected is not None:
        existing_form, existing_ids = _load_existing(db, workspace.session, selected)
        if existing_form is not None:
            form, selected_ids = existing_form, existing_ids

    return {
        "workspace": workspace,
        "selected_student_id": selected,
        "sections": [
            (cat, CATEGORY_LABELS[cat], indicator_groups[cat]) for cat in categories
        ],
        "rating_fields": _rating_fields(is_daily),
        "rating_labels": _rating_labels(is_daily),
        "form": form or _empty_form(is_daily),
        "selected_ids": selected_ids or set(),
        "errors": errors or [],
    }


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def workspace_page(request: Request, session_id: str, db: Session = Depends(get_db)):
    try:
        context = _workspace_context(request, db, session_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "today.html",
            {
                "on_date": "",
                "sessions": [],
                "classes": [],
                "teachers": [],
                "form": {},
                "errors": [str(exc)],
            },
            status_code=404,
        )
    return templates.TemplateResponse(request, "feedback/workspace.html", context)


def _save(
    request: Request,
    db: Session,
    session_id: str,
    student_id: str,
    is_daily: bool,
    form: dict,
    progress: list[str],
    weak: list[str],
    note: str,
):
    fields = _rating_fields(is_daily)
    try:
        ratings = {field: int(form[field]) for field in fields}
    except (ValueError, TypeError):
        return _render_error(request, db, session_id, student_id, form, progress, weak, "评分必须是1到5")

    values = (
        DailyFeedbackInput(
            rating_knowledge=ratings.get("rating_knowledge", 0),
            rating_habit=ratings["rating_habit"],
            rating_mindset=ratings.get("rating_mindset", 0),
            progress_indicators=progress,
            weak_indicators=weak,
            note=note or None,
        )
        if is_daily
        else SpecialFeedbackInput(
            rating_skill=ratings.get("rating_skill", 0),
            rating_habit=ratings["rating_habit"],
            progress_indicators=progress,
            weak_indicators=weak,
            note=note or None,
        )
    )
    try:
        if is_daily:
            service.save_daily_feedback(db, session_id, student_id, values)
        else:
            service.save_special_feedback(db, session_id, student_id, values)
    except ValueError as exc:
        return _render_error(request, db, session_id, student_id, form, progress, weak, str(exc))

    next_id = service.next_unfinished_student(db, session_id, student_id)
    if next_id:
        return RedirectResponse(f"/sessions/{session_id}?student_id={next_id}", status_code=303)
    return RedirectResponse(f"/sessions/{session_id}", status_code=303)


def _render_error(
    request: Request,
    db: Session,
    session_id: str,
    student_id: str,
    form: dict,
    progress: list[str],
    weak: list[str],
    message: str,
):
    selected_ids = set(progress) | set(weak)
    try:
        context = _workspace_context(
            request, db, session_id, form=form, selected_ids=selected_ids,
            errors=[message], selected_student_id=student_id,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "today.html",
            {
                "on_date": "",
                "sessions": [],
                "classes": [],
                "teachers": [],
                "form": {},
                "errors": [str(exc)],
            },
            status_code=404,
        )
    return templates.TemplateResponse(
        request, "feedback/workspace.html", context, status_code=422
    )


@router.post("/sessions/{session_id}/daily/{student_id}")
def save_daily(
    request: Request,
    session_id: str,
    student_id: str,
    rating_knowledge: str = Form(...),
    rating_habit: str = Form(...),
    rating_mindset: str = Form(...),
    progress_indicators: list[str] = Form(default=[]),
    weak_indicators: list[str] = Form(default=[]),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    form = {
        "rating_knowledge": rating_knowledge,
        "rating_habit": rating_habit,
        "rating_mindset": rating_mindset,
        "note": note,
    }
    return _save(
        request, db, session_id, student_id, True, form,
        progress_indicators, weak_indicators, note,
    )


@router.post("/sessions/{session_id}/special/{student_id}")
def save_special(
    request: Request,
    session_id: str,
    student_id: str,
    rating_skill: str = Form(...),
    rating_habit: str = Form(...),
    progress_indicators: list[str] = Form(default=[]),
    weak_indicators: list[str] = Form(default=[]),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    form = {
        "rating_skill": rating_skill,
        "rating_habit": rating_habit,
        "note": note,
    }
    return _save(
        request, db, session_id, student_id, False, form,
        progress_indicators, weak_indicators, note,
    )
