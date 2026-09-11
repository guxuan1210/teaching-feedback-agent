"""Weekly report center: filtered index, batch generation, two-column detail,
parent-message editing and previewed rewriting.

Admin teachers see and act on every class and report. Ordinary teachers are
scoped to the classes they head: the index is filtered, cross-class
detail/edit/finalize/rewrite attempts return 403, and a generate request for an
unowned class returns 403.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Class, Student, Teacher
from app.core.auth import is_admin, require_login
from app.core.database import get_db
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.reports import service
from app.reports.context import build_report_context, build_rewrite_context
from app.reports.generators import DIRECTION_LABELS
from app.reports.models import WeeklyReport
from app.reports.period import default_period
from app.reports.validation import validate_parent_message

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["reports"])

STATUS_LABELS = {
    "none": "未生成",
    "no_data": "暂无数据",
    "draft": "待审核",
    "finalized": "已定稿",
}


def _scoped_classes(db: Session, teacher: Teacher) -> list[Class]:
    if is_admin(teacher):
        return catalog_repository.list_classes(db)
    return catalog_repository.list_classes_for_teacher(db, teacher.teacher_id)


def _allowed_class_ids(db: Session, teacher: Teacher) -> set[str] | None:
    if is_admin(teacher):
        return None
    return {c.class_id for c in _scoped_classes(db, teacher)}


def _load_report(db: Session, teacher: Teacher, report_id: str) -> WeeklyReport:
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise HTTPException(status_code=404)
    allowed = _allowed_class_ids(db, teacher)
    if allowed is not None and report.class_id not in allowed:
        raise HTTPException(status_code=403)
    return report


def _load_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
def _index_context(
    request: Request,
    db: Session,
    teacher: Teacher,
    *,
    class_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    status: str | None = None,
    errors: list[str] | None = None,
    success: str | None = None,
) -> dict:
    classes = _scoped_classes(db, teacher)
    default_start, default_end = default_period()

    selected_class = class_id or (classes[0].class_id if classes else "")
    start = period_start or default_start
    end = period_end or default_end
    status_filter = status or "all"

    allowed = _allowed_class_ids(db, teacher)
    rows: list[service.ReportRow] = []
    if selected_class and (allowed is None or selected_class in allowed):
        rows = service.list_report_rows(
            db, class_id=selected_class, period_start=start, period_end=end
        )

    if status_filter != "all":
        rows = [row for row in rows if row.status == status_filter]

    return {
        "classes": classes,
        "selected_class": selected_class,
        "period_start": start,
        "period_end": end,
        "status_filter": status_filter,
        "rows": rows,
        "STATUS_LABELS": STATUS_LABELS,
        "errors": errors or [],
        "success": success,
    }


@router.get("/reports", response_class=HTMLResponse)
def report_index(
    request: Request,
    class_id: str | None = Query(default=None),
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
    status: str | None = Query(default=None),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    errors = request.session.pop("flash_errors", [])
    success = request.session.pop("flash_success", None)
    return templates.TemplateResponse(
        request,
        "reports/index.html",
        _index_context(
            request,
            db,
            teacher,
            class_id=class_id,
            period_start=period_start,
            period_end=period_end,
            status=status,
            errors=errors,
            success=success,
        ),
    )


@router.post("/reports/generate")
def report_generate(
    request: Request,
    class_id: str = Form(...),
    student_ids: list[str] = Form(default=[]),
    period_start: str = Form(...),
    period_end: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    def _error(message: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "reports/index.html",
            _index_context(
                request,
                db,
                teacher,
                class_id=class_id,
                period_start=period_start,
                period_end=period_end,
                errors=[message],
            ),
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
        requested_mode="ai",
        ai_generator=request.app.state.ai_report_generator,
    )

    if len(result.created) == 1 and not result.errors:
        return RedirectResponse(f"/reports/{result.created[0]}", status_code=303)

    request.session["flash_errors"] = [
        f"{err['student_id']}: {err['error']}" for err in result.errors
    ]
    if result.created:
        request.session["flash_success"] = f"已生成 {len(result.created)} 份周报"

    target = (
        f"/reports?class_id={class_id}&period_start={period_start}"
        f"&period_end={period_end}"
    )
    return RedirectResponse(target, status_code=303)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
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

    parent_message = (form or {}).get("parent_message", report.parent_message)

    sources = []
    for source in report.sources:
        feedback = (
            db.get(DailyFeedback, source.feedback_id)
            if source.feedback_type == "daily"
            else db.get(SpecialFeedback, source.feedback_id)
        )
        sources.append(
            {
                "feedback_type": source.feedback_type,
                "feedback_id": source.feedback_id,
                "note": feedback.note if feedback is not None else None,
            }
        )

    rating_changes = _rating_changes(db, report)

    return {
        "report": report,
        "student": student,
        "klass": klass,
        "parent_message": parent_message,
        "strengths": strengths,
        "concerns": concerns,
        "suggestions": suggestions,
        "sources": sources,
        "feedback_count": len(sources),
        "rating_changes": rating_changes,
        "errors": errors or [],
    }


def _rating_changes(db: Session, report: WeeklyReport) -> list[dict]:
    """Best-effort rating trends for the review pane, never fatal on failure."""
    try:
        context = build_report_context(
            db,
            student_id=report.student_id,
            class_id=report.class_id,
            period_start=report.period_start,
            period_end=report.period_end,
        )
    except ValueError:
        return []
    changes = []
    for key, change in context.rating_changes.items():
        changes.append({"label": DIRECTION_LABELS.get(key, key), "change": change})
    changes.sort(key=lambda item: abs(item["change"]), reverse=True)
    return changes


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


@router.post("/reports/{report_id}")
def report_edit(
    request: Request,
    report_id: str,
    parent_message: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)

    def _error(message: str, status_code: int = 422):
        return templates.TemplateResponse(
            request,
            "reports/detail.html",
            _detail_context(
                db, report, form={"parent_message": parent_message}, errors=[message]
            ),
            status_code=status_code,
        )

    try:
        service.update_report_message(db, report_id, parent_message=parent_message)
    except ValueError as exc:
        message = str(exc)
        if "定稿周报不可编辑" in message:
            raise HTTPException(status_code=409)
        if "周报不存在" in message:
            raise HTTPException(status_code=404)
        return _error(message)

    return RedirectResponse(f"/reports/{report_id}", status_code=303)


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


# ---------------------------------------------------------------------------
# Rewrite (quick actions + natural-language instruction)
# ---------------------------------------------------------------------------
def _preview_context(
    db: Session,
    report: WeeklyReport,
    *,
    original: str,
    candidate: str,
    action: str | None = None,
    instruction: str | None = None,
    errors: list[str] | None = None,
) -> dict:
    return {
        "report": report,
        "student": db.get(Student, report.student_id),
        "klass": db.get(Class, report.class_id),
        "original": original,
        "candidate": candidate,
        "action": action,
        "instruction": instruction,
        "errors": errors or [],
    }


@router.post("/reports/{report_id}/rewrite/preview", response_class=HTMLResponse)
def report_rewrite_preview(
    request: Request,
    report_id: str,
    action: str | None = Form(default=None),
    instruction: str | None = Form(default=None),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)
    if report.status == "finalized":
        raise HTTPException(status_code=409, detail="定稿周报不可改写")

    rewriter = request.app.state.rewriter
    if rewriter is None:
        return templates.TemplateResponse(
            request,
            "reports/detail.html",
            _detail_context(
                db, report, errors=["改写服务不可用，请直接编辑正文或稍后重试"]
            ),
            status_code=503,
        )

    try:
        context = build_rewrite_context(db, report)
        candidate = rewriter.rewrite(
            action=action,
            instruction=instruction,
            current_message=report.parent_message,
            context=context,
        )
        candidate = validate_parent_message(candidate, context)
    except Exception:  # noqa: BLE001 - any rewrite failure keeps the original
        return templates.TemplateResponse(
            request,
            "reports/detail.html",
            _detail_context(
                db, report, errors=["改写失败，已保留原稿，您可以直接编辑或重试"]
            ),
            status_code=502,
        )

    return templates.TemplateResponse(
        request,
        "reports/rewrite_preview.html",
        _preview_context(
            db,
            report,
            original=report.parent_message,
            candidate=candidate,
            action=action,
            instruction=instruction,
        ),
    )


@router.post("/reports/{report_id}/rewrite/confirm")
def report_rewrite_confirm(
    request: Request,
    report_id: str,
    candidate: str = Form(...),
    expected_original: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    report = _load_report(db, teacher, report_id)
    if report.status == "finalized":
        raise HTTPException(status_code=409, detail="定稿周报不可改写")

    try:
        context = build_rewrite_context(db, report)
        candidate = validate_parent_message(candidate, context)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "reports/rewrite_preview.html",
            _preview_context(
                db,
                report,
                original=report.parent_message,
                candidate=candidate,
                errors=[str(exc)],
            ),
            status_code=422,
        )

    try:
        service.replace_report_message(
            db,
            report_id,
            parent_message=candidate,
            expected_original=expected_original,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "reports/rewrite_preview.html",
            _preview_context(
                db,
                report,
                original=report.parent_message,
                candidate=candidate,
                errors=[str(exc)],
            ),
            status_code=409,
        )

    return RedirectResponse(f"/reports/{report_id}", status_code=303)
