"""Weekly report lifecycle: generation, batch isolation, editing and finalizing.

Generation persists a report and its pinned source feedback in a single atomic
transaction. AI generation and validation failures always fall back to the
offline template and record a human-readable ``generation_note``, so a missing
or broken model service can never block a teacher from producing a report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.reports.context import build_report_context
from app.reports.generators import ReportGenerator, TemplateReportGenerator
from app.reports.models import WeeklyReport, WeeklyReportSource, _utcnow
from app.reports.validation import validate_report_output


@dataclass
class BatchGenerationResult:
    created: list[str]
    errors: list[dict]  # each {"student_id": str, "error": str}


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


def update_report_draft(
    db: Session,
    report_id: str,
    *,
    summary: str,
    strengths: list[str],
    concerns: list[str],
    suggestions: list[str],
) -> WeeklyReport:
    """Edit a draft report; finalized reports are immutable."""
    report = db.get(WeeklyReport, report_id)
    if report is None:
        raise ValueError("周报不存在")
    if report.status == "finalized":
        raise ValueError("定稿周报不可编辑")

    report.summary = summary
    report.strengths = json.dumps(strengths, ensure_ascii=False)
    report.concerns = json.dumps(concerns, ensure_ascii=False)
    report.suggestions = json.dumps(suggestions, ensure_ascii=False)
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
