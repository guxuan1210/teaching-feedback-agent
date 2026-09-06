"""History browsing: filter, view/edit, and void feedback records."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Teacher
from app.core.auth import is_admin, require_login
from app.core.database import get_db
from app.feedback import service
from app.feedback.forms import DailyFeedbackInput, SpecialFeedbackInput

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["history"])

RATING_FIELDS = {
    "daily": ["rating_knowledge", "rating_habit", "rating_mindset"],
    "special": ["rating_skill", "rating_habit"],
}

RATING_LABELS = {
    "daily": {
        "rating_knowledge": "知识掌握",
        "rating_habit": "学习习惯",
        "rating_mindset": "心态与内驱力",
    },
    "special": {
        "rating_skill": "知识／技能",
        "rating_habit": "课堂习惯",
    },
}

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


def _parse_filters(request: Request) -> service.HistoryFilters:
    q = request.query_params
    return service.HistoryFilters(
        date_from=q.get("date_from") or None,
        date_to=q.get("date_to") or None,
        session_type=q.get("session_type") or None,
        class_id=q.get("class_id") or None,
        student_id=q.get("student_id") or None,
        status=q.get("status") or None,
    )


def _scope_teacher_id(teacher: Teacher) -> str | None:
    return None if is_admin(teacher) else teacher.teacher_id


def _scoped_choices(db: Session, teacher: Teacher):
    if is_admin(teacher):
        return catalog_repository.list_classes(db), catalog_repository.list_students(db)
    return (
        catalog_repository.list_classes_for_teacher(db, teacher.teacher_id),
        catalog_repository.list_students_for_teacher(db, teacher.teacher_id),
    )


def _index_context(
    request: Request,
    db: Session,
    teacher: Teacher,
    filters: service.HistoryFilters | None = None,
    errors: list[str] | None = None,
) -> dict:
    if filters is None:
        filters = _parse_filters(request)
    filters.teacher_id = _scope_teacher_id(teacher)
    params = {
        key: value
        for key in ("date_from", "date_to", "session_type", "class_id", "student_id", "status")
        if (value := getattr(filters, key))
    }
    query_string = ("?" + urlencode(params)) if params else ""
    classes, students = _scoped_choices(db, teacher)
    return {
        "rows": service.query_history(db, filters),
        "filters": filters,
        "classes": classes,
        "students": students,
        "RATING_LABELS": RATING_LABELS,
        "export_url": "/export.xlsx" + query_string,
        "errors": errors or [],
    }


def _find_row(
    db: Session, teacher: Teacher, feedback_type: str, feedback_id: str
) -> service.HistoryRow | None:
    filters = service.HistoryFilters(
        status="all", teacher_id=_scope_teacher_id(teacher)
    )
    for row in service.query_history(db, filters):
        if row.feedback_type == feedback_type and row.feedback_id == feedback_id:
            return row
    return None


def _detail_context(
    request: Request,
    db: Session,
    teacher: Teacher,
    feedback_type: str,
    feedback_id: str,
    form: dict | None = None,
    selected_ids: set[str] | None = None,
    errors: list[str] | None = None,
) -> dict | None:
    row = _find_row(db, teacher, feedback_type, feedback_id)
    if row is None:
        return None
    fields = RATING_FIELDS.get(feedback_type)
    if fields is None:
        return None
    categories = service.DAILY_CATEGORIES if feedback_type == "daily" else service.SPECIAL_CATEGORIES
    indicator_groups = service.indicators_by_category(db, categories)
    if form is None:
        form = {field: str(row.ratings[field]) for field in fields} | {
            "note": row.note or ""
        }
    if selected_ids is None:
        selected_ids = set(row.indicator_ids)
    return {
        "row": row,
        "rating_fields": fields,
        "rating_labels": RATING_LABELS[feedback_type],
        "sections": [
            (cat, CATEGORY_LABELS[cat], indicator_groups[cat]) for cat in categories
        ],
        "form": form,
        "selected_ids": selected_ids,
        "errors": errors or [],
    }


@router.get("/history", response_class=HTMLResponse)
def history_index(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "history/index.html", _index_context(request, db, teacher)
    )


@router.get("/history/{feedback_type}/{feedback_id}", response_class=HTMLResponse)
def history_detail(
    request: Request,
    feedback_type: str,
    feedback_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    context = _detail_context(request, db, teacher, feedback_type, feedback_id)
    if context is None:
        return templates.TemplateResponse(
            request,
            "history/index.html",
            _index_context(request, db, teacher, errors=["反馈记录不存在"]),
            status_code=404,
        )
    return templates.TemplateResponse(request, "history/detail.html", context)


@router.post("/history/{feedback_type}/{feedback_id}")
def history_update(
    request: Request,
    feedback_type: str,
    feedback_id: str,
    rating_knowledge: str = Form(""),
    rating_habit: str = Form(""),
    rating_mindset: str = Form(""),
    rating_skill: str = Form(""),
    progress_indicators: list[str] = Form(default=[]),
    weak_indicators: list[str] = Form(default=[]),
    note: str = Form(""),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    fields = RATING_FIELDS.get(feedback_type)
    if fields is None:
        return RedirectResponse("/history", status_code=303)

    raw = {
        "rating_knowledge": rating_knowledge,
        "rating_habit": rating_habit,
        "rating_mindset": rating_mindset,
        "rating_skill": rating_skill,
    }
    form = {field: raw.get(field, "") for field in fields} | {"note": note}
    selected_ids = set(progress_indicators) | set(weak_indicators)

    def _error(message: str):
        return templates.TemplateResponse(
            request,
            "history/detail.html",
            _detail_context(
                request, db, teacher, feedback_type, feedback_id,
                form=form, selected_ids=selected_ids, errors=[message],
            ),
            status_code=422,
        )

    try:
        ratings = {field: int(raw[field]) for field in fields}
    except (ValueError, TypeError):
        return _error("评分必须是1到5")

    try:
        if feedback_type == "daily":
            values = DailyFeedbackInput(
                rating_knowledge=ratings["rating_knowledge"],
                rating_habit=ratings["rating_habit"],
                rating_mindset=ratings["rating_mindset"],
                progress_indicators=progress_indicators,
                weak_indicators=weak_indicators,
                note=note or None,
            )
            service.update_daily_feedback(db, feedback_id, values)
        else:
            values = SpecialFeedbackInput(
                rating_skill=ratings["rating_skill"],
                rating_habit=ratings["rating_habit"],
                progress_indicators=progress_indicators,
                weak_indicators=weak_indicators,
                note=note or None,
            )
            service.update_special_feedback(db, feedback_id, values)
    except ValueError as exc:
        return _error(str(exc))

    return RedirectResponse("/history", status_code=303)


@router.post("/history/{feedback_type}/{feedback_id}/void")
def history_void(
    feedback_type: str,
    feedback_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    row = _find_row(db, teacher, feedback_type, feedback_id)
    if row is not None:
        try:
            service.void_feedback(db, feedback_type, feedback_id)
        except ValueError:
            pass
    return RedirectResponse("/history", status_code=303)
