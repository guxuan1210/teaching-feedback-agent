"""Server-side pre-aggregation of teaching data for a chat turn.

The context builder never hands raw database records to the model. It reuses
the read-only profile aggregation from :mod:`app.profiles.service` and the
finalized weekly reports, then produces:

* a plain-text ``data_block`` with ``[S#]`` source markers, and
* an ordered :class:`ChatSource` list (feedback first, then reports).

Sources are truncated to ``MAX_SOURCES`` and ordered stably by recency so the
model has a bounded, deterministic view of the allowed data range.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Class
from app.catalog.repository import active_roster
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.profiles.service import build_student_profile
from app.reports.models import WeeklyReport
from app.sessions.models import ClassSession

MAX_SOURCES = 50
MAX_CLASS_STUDENTS = 40
MAX_RECENT_NOTES = 10
MAX_REPORTS = 5
MAX_TOP_INDICATORS = 10

_DAILY_TREND_LABELS = {
    "knowledge": "知识掌握",
    "habit": "学习习惯",
    "mindset": "心态与内驱力",
}
_SPECIAL_TREND_LABELS = {
    "skill": "知识／技能",
    "habit": "课堂习惯",
}


def friendly_date(iso: str) -> str:
    try:
        year, month, day = iso.split("-")
        return f"{int(month)}月{int(day)}日"
    except (ValueError, AttributeError):
        return iso


@dataclass(frozen=True)
class ChatSource:
    source_type: str
    source_id: str
    label: str
    url: str


@dataclass(frozen=True)
class ChatContext:
    scope_type: str
    class_id: str
    class_name: str
    student_id: str | None
    student_name: str | None
    date_from: str
    date_to: str
    feedback_count: int
    data_block: str
    sources: list[ChatSource]
    snapshot: dict


def build_chat_context(
    db: Session,
    conversation,
    date_from: str,
    date_to: str,
) -> ChatContext:
    """Build a turn's context from a conversation and its current date range."""
    klass = db.get(Class, conversation.class_id)
    if klass is None:
        raise ValueError("班级不存在")

    if conversation.scope_type == "student":
        return _build_student_context(db, conversation, klass, date_from, date_to)
    return _build_class_context(db, conversation, klass, date_from, date_to)


# ---------------------------------------------------------------------------
# Source collection
# ---------------------------------------------------------------------------
def _report_sources(
    db: Session,
    class_id: str,
    student_id: str | None,
) -> list[tuple[str, str, ChatSource]]:
    """Return ``(sort_date, sort_id, ChatSource)`` tuples for finalized reports."""
    stmt = (
        select(WeeklyReport)
        .where(WeeklyReport.class_id == class_id, WeeklyReport.status == "finalized")
        .order_by(WeeklyReport.period_end.desc(), WeeklyReport.report_id)
    )
    if student_id is not None:
        stmt = stmt.where(WeeklyReport.student_id == student_id)
    reports = list(db.scalars(stmt.limit(MAX_REPORTS)))
    return [
        (
            r.period_end,
            r.report_id,
            ChatSource(
                source_type="weekly_report",
                source_id=r.report_id,
                label=f"周报 {r.period_start}~{r.period_end}",
                url=f"/reports/{r.report_id}",
            ),
        )
        for r in reports
    ]


def _feedback_source(feedback_type: str, feedback_id: str, session_date: str) -> ChatSource:
    kind = "晚辅反馈" if feedback_type == "daily" else "专项反馈"
    return ChatSource(
        source_type=feedback_type,
        source_id=feedback_id,
        label=f"{friendly_date(session_date)}{kind}",
        url=f"/history/{feedback_type}/{feedback_id}",
    )


def _finalize_sources(
    feedback_items: list[tuple[str, str, ChatSource]],
    report_items: list[tuple[str, str, ChatSource]],
) -> list[ChatSource]:
    """Order feedback before reports, truncate to ``MAX_SOURCES``."""
    feedback_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
    report_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
    ordered = feedback_items + report_items
    return [source for _date, _id, source in ordered[:MAX_SOURCES]]


def _source_lines(sources: list[ChatSource]) -> list[str]:
    return [f"[S{i}] {source.label}" for i, source in enumerate(sources, start=1)]


# ---------------------------------------------------------------------------
# Student scope
# ---------------------------------------------------------------------------
def _build_student_context(
    db: Session,
    conversation,
    klass: Class,
    date_from: str,
    date_to: str,
) -> ChatContext:
    profile = build_student_profile(
        db,
        student_id=conversation.student_id,
        class_id=conversation.class_id,
        date_from=date_from,
        date_to=date_to,
    )

    feedback_items = [
        (
            src.session_date,
            src.feedback_id,
            _feedback_source(src.feedback_type, src.feedback_id, src.session_date),
        )
        for src in profile.sources
    ]
    report_items = _report_sources(db, conversation.class_id, conversation.student_id)
    sources = _finalize_sources(feedback_items, report_items)

    lines: list[str] = []
    lines.append("【对话范围】")
    lines.append(f"学生：{profile.student.name}（{klass.name}）")
    lines.append(f"数据周期：{date_from} 至 {date_to}")
    lines.append("")
    lines.append("【有效反馈统计】")
    lines.append(f"有效反馈：{profile.feedback_count} 次")

    rating_changes = _rating_change_lines(profile.daily_trends, profile.special_trends)
    if rating_changes:
        lines.append("")
        lines.append("【评分变化】")
        lines.extend(rating_changes)

    lines.extend(
        _indicator_sections(
            [(item.text, item.count) for item in profile.strengths],
            [(item.text, item.count) for item in profile.concerns],
        )
    )
    lines.extend(_notes_section(profile.recent_notes))

    reports = db.scalars(
        select(WeeklyReport)
        .where(
            WeeklyReport.class_id == conversation.class_id,
            WeeklyReport.student_id == conversation.student_id,
            WeeklyReport.status == "finalized",
        )
        .order_by(WeeklyReport.period_end.desc())
        .limit(MAX_REPORTS)
    ).all()
    lines.extend(_reports_section(reports))

    lines.extend(_sources_section(sources))
    data_block = "\n".join(lines)

    snapshot = {
        "scope_type": "student",
        "class_id": conversation.class_id,
        "class_name": klass.name,
        "student_id": conversation.student_id,
        "student_name": profile.student.name,
        "date_from": date_from,
        "date_to": date_to,
        "feedback_count": profile.feedback_count,
        "rating_changes": {
            key: {"start": trend.start, "end": trend.end, "change": trend.change}
            for key, trend in {
                **profile.daily_trends,
                **{f"special_{k}": v for k, v in profile.special_trends.items()},
            }.items()
        },
        "strengths": [{"text": i.text, "count": i.count} for i in profile.strengths],
        "concerns": [{"text": i.text, "count": i.count} for i in profile.concerns],
        "recent_notes": list(profile.recent_notes),
        "sources": _snapshot_sources(sources),
    }

    return ChatContext(
        scope_type="student",
        class_id=conversation.class_id,
        class_name=klass.name,
        student_id=conversation.student_id,
        student_name=profile.student.name,
        date_from=date_from,
        date_to=date_to,
        feedback_count=profile.feedback_count,
        data_block=data_block,
        sources=sources,
        snapshot=snapshot,
    )


# ---------------------------------------------------------------------------
# Class scope
# ---------------------------------------------------------------------------
def _build_class_context(
    db: Session,
    conversation,
    klass: Class,
    date_from: str,
    date_to: str,
) -> ChatContext:
    roster = active_roster(db, conversation.class_id, date.fromisoformat(date_to))
    if len(roster) > MAX_CLASS_STUDENTS:
        raise ValueError(f"班级学生超过 {MAX_CLASS_STUDENTS} 人，暂不支持班级对话")

    profiles = []
    for student in roster:
        profiles.append(
            build_student_profile(
                db,
                student_id=student.student_id,
                class_id=conversation.class_id,
                date_from=date_from,
                date_to=date_to,
            )
        )

    feedback_items: list[tuple[str, str, ChatSource]] = []
    for profile in profiles:
        for src in profile.sources:
            feedback_items.append(
                (
                    src.session_date,
                    src.feedback_id,
                    _feedback_source(src.feedback_type, src.feedback_id, src.session_date),
                )
            )
    report_items = _report_sources(db, conversation.class_id, None)
    sources = _finalize_sources(feedback_items, report_items)

    total_feedback = sum(p.feedback_count for p in profiles)
    active_profiles = [p for p in profiles if p.feedback_count > 0]

    strength_counter: Counter = Counter()
    concern_counter: Counter = Counter()
    for profile in active_profiles:
        for item in profile.strengths:
            strength_counter[(item.text, item.category)] += item.count
        for item in profile.concerns:
            concern_counter[(item.text, item.category)] += item.count

    lines: list[str] = []
    lines.append("【对话范围】")
    lines.append(f"班级：{klass.name}")
    lines.append(f"数据周期：{date_from} 至 {date_to}")
    lines.append("")
    lines.append("【有效反馈统计】")
    lines.append(f"有效反馈：{total_feedback} 次（涉及 {len(active_profiles)} 名学生）")

    if active_profiles:
        lines.append("")
        lines.append("【学生概览】")
        for profile in active_profiles:
            lines.append(f"- {profile.student.name}：{profile.feedback_count} 次反馈")

    lines.extend(_indicator_sections(
        _top_indicators(strength_counter),
        _top_indicators(concern_counter),
    ))

    recent_notes = _recent_class_notes(db, conversation.class_id, date_from, date_to)
    lines.extend(_notes_section(recent_notes))

    reports = db.scalars(
        select(WeeklyReport)
        .where(
            WeeklyReport.class_id == conversation.class_id,
            WeeklyReport.status == "finalized",
        )
        .order_by(WeeklyReport.period_end.desc())
        .limit(MAX_REPORTS)
    ).all()
    lines.extend(_reports_section(reports))

    lines.extend(_sources_section(sources))
    data_block = "\n".join(lines)

    snapshot = {
        "scope_type": "class",
        "class_id": conversation.class_id,
        "class_name": klass.name,
        "student_id": None,
        "date_from": date_from,
        "date_to": date_to,
        "feedback_count": total_feedback,
        "students": [
            {"name": p.student.name, "feedback_count": p.feedback_count}
            for p in active_profiles
        ],
        "strengths": [{"text": t, "count": c} for (t, _cat), c in _top_indicators(strength_counter)],
        "concerns": [{"text": t, "count": c} for (t, _cat), c in _top_indicators(concern_counter)],
        "recent_notes": recent_notes,
        "sources": _snapshot_sources(sources),
    }

    return ChatContext(
        scope_type="class",
        class_id=conversation.class_id,
        class_name=klass.name,
        student_id=None,
        student_name=None,
        date_from=date_from,
        date_to=date_to,
        feedback_count=total_feedback,
        data_block=data_block,
        sources=sources,
        snapshot=snapshot,
    )


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------
def _rating_change_lines(daily_trends, special_trends) -> list[str]:
    lines: list[str] = []
    for key, trend in daily_trends.items():
        label = _DAILY_TREND_LABELS.get(key, key)
        lines.append(f"{label}：{trend.start} → {trend.end}（{_direction(trend.change)}）")
    for key, trend in special_trends.items():
        label = _SPECIAL_TREND_LABELS.get(key, key)
        lines.append(f"{label}：{trend.start} → {trend.end}（{_direction(trend.change)}）")
    return lines


def _direction(change: int) -> str:
    if change > 0:
        return "上升"
    if change < 0:
        return "下降"
    return "持平"


def _top_indicators(counter: Counter) -> list[tuple[str, int]]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0][0]))
    return [(text, count) for (text, _category), count in items[:MAX_TOP_INDICATORS]]


def _indicator_sections(strengths, concerns) -> list[str]:
    lines: list[str] = []
    if strengths:
        lines.append("")
        lines.append("【高频进步项】")
        for text, count in strengths:
            lines.append(f"- {text}（{count} 次）")
    if concerns:
        lines.append("")
        lines.append("【高频薄弱项】")
        for text, count in concerns:
            lines.append(f"- {text}（{count} 次）")
    return lines


def _notes_section(notes: list[str]) -> list[str]:
    if not notes:
        return []
    lines = ["", "【近期老师备注】"]
    lines.extend(f"- {note}" for note in notes[:MAX_RECENT_NOTES])
    return lines


def _reports_section(reports: list[WeeklyReport]) -> list[str]:
    if not reports:
        return []
    lines = ["", "【定稿周报摘要】"]
    lines.extend(f"- {report.summary}" for report in reports[:MAX_REPORTS])
    return lines


def _sources_section(sources: list[ChatSource]) -> list[str]:
    if not sources:
        return []
    lines = ["", "【来源记录】"]
    lines.extend(_source_lines(sources))
    return lines


def _snapshot_sources(sources: list[ChatSource]) -> list[dict]:
    return [
        {
            "index": index,
            "type": source.source_type,
            "id": source.source_id,
            "label": source.label,
        }
        for index, source in enumerate(sources, start=1)
    ]


def _recent_class_notes(
    db: Session, class_id: str, date_from: str, date_to: str
) -> list[str]:
    notes: list[tuple[str, str]] = []
    for model in (DailyFeedback, SpecialFeedback):
        stmt = (
            select(model, ClassSession)
            .join(ClassSession, model.session_id == ClassSession.session_id)
            .where(
                model.status == "active",
                ClassSession.class_id == class_id,
                ClassSession.session_date >= date_from,
                ClassSession.session_date <= date_to,
            )
        )
        for feedback, session in db.execute(stmt).all():
            if feedback.note and feedback.note.strip():
                notes.append((session.session_date, feedback.note.strip()))
    notes.sort(key=lambda item: item[0], reverse=True)
    return [note for _date, note in notes[:MAX_RECENT_NOTES]]
