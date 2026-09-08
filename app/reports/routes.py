"""Weekly report center: index + batch generation, draft editing, finalizing.

Admin teachers see and act on every class and report. Ordinary teachers are
scoped to the classes they head: the index list is filtered, cross-class
detail/edit/finalize attempts return 403, and a generate request for an
unowned class returns 403.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Class, Student, Teacher
from app.core.auth import is_admin, require_login
from app.core.database import get_db
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.reports import service
from app.reports.models import WeeklyReport

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["reports"])

GENERATION_MODE_LABELS = {
    "template": "规则模板",
    "ai": "AI 生成",
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


def _allowed_class_ids(db: Session, teacher: Teacher) -> set[str] | None:
    if is_admin(teacher):
        return None
    return {
        c.class_id
        for c in catalog_repository.list_classes_for_teacher(db, teacher.teacher_id)
    }


def _load_report(db: Session, teacher: Teacher, report_id: str) -> WeeklyReport:
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise HTTPException(status_code=404)
    allowed = _allowed_class_ids(db, teacher)
    if allowed is not None and report.class_id not in allowed:
        raise HTTPException(status_code=403)
    return report


def _index_context(
    request: Request,
    db: Session,
    teacher: Teacher,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
    success: str | None = None,
) -> dict:
    classes, students = _scoped_choices(db, teacher)
    allowed = _allowed_class_ids(db, teacher)
    reports = service.list_reports(db, class_ids=allowed)

    student_names = {s.student_id: s.name for s in students}
    class_names = {c.class_id: c.name for c in classes}

    default_from, default_to = _default_range()
    if form is None:
        form = {}
    class_id = form.get("class_id", "")
    selected_student_ids = set(form.get("selected_student_ids", []))
    period_start = form.get("period_start") or default_from
    period_end = form.get("period_end") or default_to
    generation_mode = form.get("generation_mode") or "template"

    return {
        "classes": classes,
        "students": students,
        "reports": reports,
        "student_names": student_names,
        "class_names": class_names,
        "form": {
            "class_id": class_id,
            "selected_student_ids": selected_student_ids,
            "period_start": period_start,
            "period_end": period_end,
            "generation_mode": generation_mode,
        },
        "GENERATION_MODE_LABELS": GENERATION_MODE_LABELS,
        "errors": errors or [],
        "success": success,
    }


@router.get("/reports", response_class=HTMLResponse)
def report_index(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    errors = request.session.pop("flash_errors", [])
    success = request.session.pop("flash_success", None)
    return templates.TemplateResponse(
        request,
        "reports/index.html",
        _index_context(request, db, teacher, errors=errors, success=success),
    )


@router.post("/reports/generate")
def report_generate(
    request: Request,
    class_id: str = Form(...),
    student_ids: list[str] = Form(default=[]),
    period_start: str = Form(...),
    period_end: str = Form(...),
    generation_mode: str = Form("template"),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    form = {
        "class_id": class_id,
        "selected_student_ids": set(student_ids),
        "period_start": period_start,
        "period_end": period_end,
        "generation_mode": generation_mode,
    }

    def _error(message: str):
        return templates.TemplateResponse(
            request,
            "reports/index.html",
            _index_context(request, db, teacher, form=form, errors=[message]),
            status_code=422,
        )

    if not class_id:
        return _error("请选择班级")

    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError:
        return _error("日期格式无效，请使用 YYYY-MM-DD")

    if end < start:
        return _error("开始日期不能晚于结束日期")

    if generation_mode not in {"template", "ai"}:
        return _error("生成模式无效")

    if not student_ids:
        return _error("请选择学生")

    allowed = _allowed_class_ids(db, teacher)
    if allowed is not None and class_id not in allowed:
        raise HTTPException(status_code=403)

    result = service.generate_reports_for_class(
        db,
        class_id=class_id,
        teacher_id=teacher.teacher_id,
        student_ids=student_ids,
        period_start=period_start,
        period_end=period_end,
        requested_mode=generation_mode,
        ai_generator=request.app.state.ai_report_generator,
    )

    if len(result.created) == 1 and not result.errors:
        return RedirectResponse(f"/reports/{result.created[0]}", status_code=303)

    request.session["flash_errors"] = [
        f"{err['student_id']}: {err['error']}" for err in result.errors
    ]
    if result.created:
        request.session["flash_success"] = f"已生成 {len(result.created)} 份周报"
    return RedirectResponse("/reports", status_code=303)


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail(
    request: Request,
    report_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)
    return templates.TemplateResponse(
        request,
        "reports/detail.html",
        _detail_context(db, report),
    )


def _detail_context(
    db: Session,
    report: WeeklyReport,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
) -> dict:
    student = db.get(Student, report.student_id)
    klass = db.get(Class, report.class_id)

    strengths = _load_json_list(report.strengths)
    concerns = _load_json_list(report.concerns)
    suggestions = _load_json_list(report.suggestions)

    if form is None:
        form = {}
    summary = form.get("summary", report.summary)
    strengths_raw = form.get(
        "strengths", json.dumps(strengths, ensure_ascii=False)
    )
    concerns_raw = form.get("concerns", json.dumps(concerns, ensure_ascii=False))
    suggestions_raw = form.get(
        "suggestions", json.dumps(suggestions, ensure_ascii=False)
    )

    sources = []
    for source in report.sources:
        if source.feedback_type == "daily":
            feedback = db.get(DailyFeedback, source.feedback_id)
        else:
            feedback = db.get(SpecialFeedback, source.feedback_id)
        sources.append(
            {
                "feedback_type": source.feedback_type,
                "feedback_id": source.feedback_id,
                "note": feedback.note if feedback is not None else None,
            }
        )

    return {
        "report": report,
        "student": student,
        "klass": klass,
        "generation_mode_label": GENERATION_MODE_LABELS.get(
            report.generation_mode, report.generation_mode
        ),
        "generation_note": report.generation_note,
        "strengths": strengths,
        "concerns": concerns,
        "suggestions": suggestions,
        "sources": sources,
        "form": {
            "summary": summary,
            "strengths": strengths_raw,
            "concerns": concerns_raw,
            "suggestions": suggestions_raw,
        },
        "errors": errors or [],
    }


def _load_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


@router.post("/reports/{report_id}")
def report_edit(
    request: Request,
    report_id: str,
    summary: str = Form(...),
    strengths: str = Form(...),
    concerns: str = Form(...),
    suggestions: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)

    form = {
        "summary": summary,
        "strengths": strengths,
        "concerns": concerns,
        "suggestions": suggestions,
    }

    def _error(message: str, status_code: int = 422):
        return templates.TemplateResponse(
            request,
            "reports/detail.html",
            _detail_context(db, report, form=form, errors=[message]),
            status_code=status_code,
        )

    parsed_strengths = _parse_json_list_field(strengths)
    parsed_concerns = _parse_json_list_field(concerns)
    parsed_suggestions = _parse_json_list_field(suggestions)
    if (
        parsed_strengths is None
        or parsed_concerns is None
        or parsed_suggestions is None
    ):
        return _error("优势／顾虑／建议字段必须是 JSON 数组")

    try:
        service.update_report_draft(
            db,
            report_id,
            summary=summary,
            strengths=parsed_strengths,
            concerns=parsed_concerns,
            suggestions=parsed_suggestions,
        )
    except ValueError as exc:
        message = str(exc)
        if "定稿周报不可编辑" in message:
            raise HTTPException(status_code=409)
        if "周报不存在" in message:
            raise HTTPException(status_code=404)
        return _error(message)

    return RedirectResponse(f"/reports/{report_id}", status_code=303)


def _parse_json_list_field(raw: str) -> list[str] | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


@router.post("/reports/{report_id}/finalize")
def report_finalize(
    request: Request,
    report_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)

    try:
        service.finalize_report(db, report_id, teacher_id=teacher.teacher_id)
    except ValueError as exc:
        if "已有定稿" in str(exc):
            return templates.TemplateResponse(
                request,
                "reports/detail.html",
                _detail_context(db, report, errors=[str(exc)]),
                status_code=409,
            )
        if "周报不存在" in str(exc):
            raise HTTPException(status_code=404)
        raise

    return RedirectResponse(f"/reports/{report_id}", status_code=303)
