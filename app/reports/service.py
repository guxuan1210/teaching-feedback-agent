"""Weekly report lifecycle: generation, batch isolation, editing and finalizing.

Generation persists a report and its pinned source feedback in a single atomic
transaction. AI generation and validation failures always fall back to the
offline template and record a human-readable ``generation_note``, so a missing
or broken model service can never block a teacher from producing a report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.core.ids import new_id
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.reports.context import build_report_context
from app.reports.generators import ReportGenerator, TemplateReportGenerator
from app.reports.models import WeeklyReport, WeeklyReportSource, _utcnow
from app.reports.validation import validate_report_output
from app.sessions.models import ClassSession


@dataclass
class BatchGenerationResult:
    created: list[str]
    errors: list[dict]  # each {"student_id": str, "error": str}


@dataclass
class ReportRow:
    """One student's projected row on the report-center index."""

    student_id: str
    student_name: str
    feedback_count: int
    report: WeeklyReport | None
    status: str  # "none" | "no_data" | "draft" | "finalized"
    updated_at: str | None
    excerpt: str | None


def list_reports(db: Session, *, class_ids: list[str] | None = None) -> list[WeeklyReport]:
    stmt = select(WeeklyReport).order_by(WeeklyReport.created_at.desc())
    if class_ids is not None:
        stmt = stmt.where(WeeklyReport.class_id.in_(class_ids))
    return list(db.scalars(stmt))


def generate_report(
    db: Session,
    *,
    student_id: str,
    class_id: str,
    teacher_id: str,
    period_start: str,
    period_end: str,
    requested_mode: str,
    ai_generator: ReportGenerator | None = None,
) -> WeeklyReport:
    """Generate and persist a draft report for one student.

    Raises ``ValueError`` when the period has no valid feedback; the report and
    its sources are committed atomically.
    """
    context = build_report_context(
        db,
        student_id=student_id,
        class_id=class_id,
        period_start=period_start,
        period_end=period_end,
    )

    generation_mode: str
    generation_note: str | None = None

    if requested_mode == "ai":
        if ai_generator is not None:
            try:
                output = ai_generator.generate(context)
                output = validate_report_output(output, context)
                generation_mode = "ai"
            except Exception as exc:  # noqa: BLE001 - fall back on any failure
                output = TemplateReportGenerator().generate(context)
                generation_mode = "template"
                generation_note = f"AI 生成失败，已回退规则模板：{exc}"
        else:
            output = TemplateReportGenerator().generate(context)
            generation_mode = "template"
            generation_note = "AI 未配置，已回退规则模板"
    else:
        output = TemplateReportGenerator().generate(context)
        generation_mode = "template"

    report = WeeklyReport(
        report_id=new_id("R"),
        student_id=student_id,
        class_id=class_id,
        teacher_id=teacher_id,
        period_start=period_start,
        period_end=period_end,
        generation_mode=generation_mode,
        status="draft",
        parent_message=output.parent_message,
        summary=output.summary,
        strengths=json.dumps(output.strengths, ensure_ascii=False),
        concerns=json.dumps(output.concerns, ensure_ascii=False),
        suggestions=json.dumps(output.suggestions, ensure_ascii=False),
        generation_note=generation_note,
    )
    db.add(report)
    db.flush()

    for feedback_type, feedback_id in context.source_keys:
        db.add(
            WeeklyReportSource(
                report_id=report.report_id,
                feedback_type=feedback_type,
                feedback_id=feedback_id,
            )
        )

    db.commit()
    return report


def generate_reports_for_class(
    db: Session,
    *,
    class_id: str,
    teacher_id: str,
    student_ids: list[str],
    period_start: str,
    period_end: str,
    requested_mode: str,
    ai_generator: ReportGenerator | None = None,
) -> BatchGenerationResult:
    """Generate reports for many students, isolating individual failures."""
    created: list[str] = []
    errors: list[dict] = []

    for student_id in student_ids:
        try:
            report = generate_report(
                db,
                student_id=student_id,
                class_id=class_id,
                teacher_id=teacher_id,
                period_start=period_start,
                period_end=period_end,
                requested_mode=requested_mode,
                ai_generator=ai_generator,
            )
            created.append(report.report_id)
        except ValueError as exc:
            db.rollback()
            errors.append({"student_id": student_id, "error": str(exc)})

    return BatchGenerationResult(created=created, errors=errors)


def update_report_message(
    db: Session,
    report_id: str,
    *,
    parent_message: str,
) -> WeeklyReport:
    """Edit a draft's parent-facing message; finalized reports are immutable."""
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise ValueError("周报不存在")
    if report.status == "finalized":
        raise ValueError("定稿周报不可编辑")

    parent_message = (parent_message or "").strip()
    if not parent_message:
        raise ValueError("家长沟通正文不能为空")

    report.parent_message = parent_message
    report.updated_at = _utcnow()

    db.commit()
    return report


def replace_report_message(
    db: Session,
    report_id: str,
    *,
    parent_message: str,
    expected_original: str,
) -> WeeklyReport:
    """Confirm a rewrite preview by replacing the message, guarding staleness.

    If the draft's message changed since the preview was generated (another
    save happened), the replacement is rejected so it never overwrites newer
    teacher edits.
    """
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise ValueError("周报不存在")
    if report.status == "finalized":
        raise ValueError("定稿周报不可编辑")
    if report.parent_message != expected_original:
        raise ValueError("原稿已被更新，请重新生成预览")

    parent_message = (parent_message or "").strip()
    if not parent_message:
        raise ValueError("家长沟通正文不能为空")

    report.parent_message = parent_message
    report.updated_at = _utcnow()

    db.commit()
    return report


def finalize_report(db: Session, report_id: str, *, teacher_id: str) -> WeeklyReport:
    """Finalize a draft, allowing only one finalized report per scope."""
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise ValueError("周报不存在")
    if report.status == "finalized":
        return report

    existing = db.scalar(
        select(WeeklyReport).where(
            WeeklyReport.student_id == report.student_id,
            WeeklyReport.class_id == report.class_id,
            WeeklyReport.period_start == report.period_start,
            WeeklyReport.period_end == report.period_end,
            WeeklyReport.status == "finalized",
            WeeklyReport.report_id != report_id,
        )
    )
    if existing is not None:
        raise ValueError("该学生本周期已有定稿周报")

    report.status = "finalized"
    report.finalized_at = _utcnow()
    report.updated_at = _utcnow()

    db.commit()
    return report


def _preferred_report(reports: list[WeeklyReport]) -> WeeklyReport | None:
    """Prefer the finalized report, else the most recently updated draft."""
    if not reports:
        return None
    finalized = [r for r in reports if r.status == "finalized"]
    pool = finalized or reports
    return max(pool, key=lambda r: r.updated_at or r.created_at or "")


def _feedback_counts(
    db: Session,
    class_id: str,
    period_start: str,
    period_end: str,
    student_ids: list[str],
) -> dict[str, int]:
    counts = {sid: 0 for sid in student_ids}
    for model in (DailyFeedback, SpecialFeedback):
        rows = db.execute(
            select(model.student_id, func.count())
            .join(ClassSession, model.session_id == ClassSession.session_id)
            .where(
                model.student_id.in_(student_ids),
                model.status == "active",
                ClassSession.class_id == class_id,
                ClassSession.session_date >= period_start,
                ClassSession.session_date <= period_end,
            )
            .group_by(model.student_id)
        ).all()
        for student_id, count in rows:
            counts[student_id] = counts[student_id] + count
    return counts


def _excerpt(parent_message: str | None) -> str | None:
    text = (parent_message or "").strip()
    if not text:
        return None
    return text[:60] + ("…" if len(text) > 60 else "")


def list_report_rows(
    db: Session,
    *,
    class_id: str,
    period_start: str,
    period_end: str,
) -> list[ReportRow]:
    """Project one row per enrolled-in-class student for a class and period."""
    roster = catalog_repository.active_roster(
        db, class_id, date.fromisoformat(period_end)
    )
    if not roster:
        return []

    student_ids = [s.student_id for s in roster]
    counts = _feedback_counts(db, class_id, period_start, period_end, student_ids)

    reports = db.scalars(
        select(WeeklyReport).where(
            WeeklyReport.class_id == class_id,
            WeeklyReport.period_start == period_start,
            WeeklyReport.period_end == period_end,
            WeeklyReport.student_id.in_(student_ids),
        )
    ).all()
    by_student: dict[str, list[WeeklyReport]] = {}
    for report in reports:
        by_student.setdefault(report.student_id, []).append(report)

    rows: list[ReportRow] = []
    for student in roster:
        report = _preferred_report(by_student.get(student.student_id, []))
        feedback_count = counts.get(student.student_id, 0)

        if report is not None and report.status == "finalized":
            status = "finalized"
        elif report is not None:
            status = "draft"
        elif feedback_count == 0:
            status = "no_data"
        else:
            status = "none"

        rows.append(
            ReportRow(
                student_id=student.student_id,
                student_name=student.name,
                feedback_count=feedback_count,
                report=report,
                status=status,
                updated_at=report.updated_at if report is not None else None,
                excerpt=_excerpt(report.parent_message) if report is not None else None,
            )
        )
    return rows
