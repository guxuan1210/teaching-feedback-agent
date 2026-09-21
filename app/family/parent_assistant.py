"""Deterministic, permission-checked parent queries for the customer channel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog.models import Enrollment, Student
from app.family import media as family_media
from app.family.permissions import guardian_can_access_student
from app.family import conversations
from app.family.models import FamilyMessage
from app.family.parent_prompts import PARENT_ASSISTANT_PROMPT
from app.profiles.service import build_student_profile
from app.reports.models import WeeklyReport


@dataclass(frozen=True)
class ParentReply:
    content: str
    image_path: str | None = None
    image_filename: str | None = None
    handoff_marker: bool = False


@dataclass(frozen=True)
class ParentAnswerResult:
    inbound_message: FamilyMessage
    message: FamilyMessage
    outbound_text: str
    route_to_teacher: bool


def _parent_context(db: Session, student_id: str) -> list[dict[str, str]]:
    """Return only parent-visible, current-student data for model context."""
    student = db.get(Student, student_id)
    rows: list[dict[str, str]] = [{
        "student_id": student.student_id,
        "student_name": student.name,
        "grade": student.grade or "",
        "stage": student.current_stage or "",
    }]
    today = date.today().isoformat()
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.student_id == student_id,
        Enrollment.status == "active",
        Enrollment.start_date <= today,
        or_(Enrollment.end_date.is_(None), Enrollment.end_date >= today),
    ).order_by(Enrollment.start_date.desc())).all()
    for enrollment in enrollments:
        profile = build_student_profile(
            db, student_id=student_id, class_id=enrollment.class_id,
            date_from=(date.today() - timedelta(days=90)).isoformat(), date_to=today,
        )
        for note in profile.recent_notes:
            if note and note.strip():
                rows.append({"student_id": student_id, "recent_feedback": note.strip()[:1000]})
    report = db.scalar(select(WeeklyReport).where(
        WeeklyReport.student_id == student_id, WeeklyReport.status == "finalized"
    ).order_by(WeeklyReport.period_end.desc()).limit(1))
    if report is not None:
        rows.append({
            "student_id": student_id,
            "finalized_weekly_report": (report.parent_message or "")[:2000],
        })
    for image in family_media.list_student_images(db, student_id, limit=10):
        rows.append({
            "student_id": student_id,
            "image_metadata": f"{image.uploaded_at[:10]} {image.caption or ''}"[:500],
        })
    return rows


def answer_parent_question(
    db: Session, provider, *, guardian_id: str, student_id: str, text: str,
    inbound_message: FamilyMessage | None = None,
) -> ParentAnswerResult:
    """Persist an inbound question and labelled bot response, or queue a teacher handoff."""
    from app.core.ids import new_id
    from app.family.models import FamilyConversation

    if not guardian_can_access_student(db, guardian_id, student_id):
        raise PermissionError("你没有查看该学生的权限")
    conversation = db.scalar(select(FamilyConversation).where(
        FamilyConversation.guardian_id == guardian_id,
        FamilyConversation.student_id == student_id,
    ).order_by(FamilyConversation.updated_at.desc()).limit(1))
    if conversation is None:
        raise ValueError("家庭会话不存在")
    inbound = inbound_message or conversations.record_inbound_parent_message(
        db, conversation.conversation_id, new_id("IN"), (text or "")[:4000]
    )
    route_to_teacher = False
    answer = ""
    try:
        if any(term in (text or "") for term in ("投诉", "安全", "费用", "请老师", "找老师")):
            raise RuntimeError("requires_teacher")
        context = _parent_context(db, student_id)
        response = provider.answer(PARENT_ASSISTANT_PROMPT, context)
        if not isinstance(response, str) or not response.strip() or len(response) > 2000:
            raise ValueError("invalid model response")
        answer = response.strip()
    except Exception:
        route_to_teacher = True
        answer = "我已记录问题并转交负责老师。"
        conversations.request_teacher_reply(db, conversation.conversation_id, "家长问题需人工处理")
    outbound = conversations.record_bot_reply(
        db, conversation.conversation_id, answer,
        reply_to_message_id=inbound.message_id,
    )
    return ParentAnswerResult(
        inbound_message=inbound, message=outbound, outbound_text=outbound.content,
        route_to_teacher=route_to_teacher,
    )


_STUDENT_ID = re.compile(r"(?<![A-Za-z0-9_-])(S[A-Za-z0-9_-]{1,159})(?![A-Za-z0-9_-])")


def _reply(content: str) -> list[ParentReply]:
    return [ParentReply(content)]


def answer_parent_query(
    db: Session,
    *,
    guardian_id: str,
    student_id: str,
    text: str,
) -> list[ParentReply]:
    """Answer a small fixed intent set; never delegates a parent query to a model."""
    explicit = _STUDENT_ID.search(text or "")
    requested_id = explicit.group(1) if explicit else student_id
    if not guardian_can_access_student(db, guardian_id, requested_id):
        return _reply("【机器人回复】你没有查看该学生的权限。")
    student = db.get(Student, requested_id)
    if student is None:
        return _reply("【机器人回复】暂时找不到该学生档案。")
    normalized = (text or "").strip()

    if "图片" in normalized and any(word in normalized for word in ("最近", "查看", "看")):
        images = family_media.list_student_images(db, requested_id, limit=10)
        if not images:
            return _reply("【机器人回复】目前还没有可查看的图片。")
        names = family_media.image_uploader_names(db, images)
        replies = []
        for row in images:
            date_text = row.uploaded_at[:10]
            uploader = names.get(row.uploaded_by_teacher_id, "老师")
            caption = row.caption.strip() if row.caption and row.caption.strip() else "（无说明）"
            content = f"【机器人回复】{date_text} · {caption} · 上传老师：{uploader}"
            # Return a storage reference, not a collection of image byte arrays.
            # The channel handler streams one authorized file at a time.
            replies.append(ParentReply(
                content, row.storage_path,
                f"{row.image_id}{row.extension}",
            ))
        return replies

    if "反馈" in normalized and any(word in normalized for word in ("最近", "查看", "看")):
        enrollments = list(db.scalars(
            select(Enrollment).where(
                Enrollment.student_id == requested_id,
                Enrollment.status == "active",
                Enrollment.start_date <= date.today().isoformat(),
                or_(
                    Enrollment.end_date.is_(None),
                    Enrollment.end_date >= date.today().isoformat(),
                ),
            ).order_by(Enrollment.start_date.desc(), Enrollment.enrollment_id.desc())
        ))
        notes: list[str] = []
        for enrollment in enrollments:
            profile = build_student_profile(
                db, student_id=requested_id, class_id=enrollment.class_id,
                date_from=(date.today() - timedelta(days=365)).isoformat(),
                date_to=date.today().isoformat(),
            )
            for note in profile.recent_notes:
                if note and note.strip() and note.strip() not in notes:
                    notes.append(note.strip())
        if not notes:
            return _reply("【机器人回复】最近暂无有效反馈记录。")
        summary = "；".join(notes[:10])
        return _reply(f"【机器人回复】{student.name}最近反馈：{summary}")

    if "周报" in normalized:
        report = db.scalar(
            select(WeeklyReport).where(
                WeeklyReport.student_id == requested_id,
                WeeklyReport.status == "finalized",
            ).order_by(WeeklyReport.period_end.desc(), WeeklyReport.finalized_at.desc())
            .limit(1)
        )
        if report is None:
            return _reply("【机器人回复】暂无已定稿周报。")
        return _reply(
            f"【机器人回复】{student.name} {report.period_start} 至 {report.period_end}周报：{report.parent_message}"
        )

    if "请老师回复" in normalized or "转老师" in normalized:
        return [ParentReply("【机器人回复】已标记转老师请求，老师会尽快回复。", handoff_marker=True)]

    return _reply("【机器人回复】我可以帮你查看最近图片、最近反馈或已定稿周报，也可以转请老师回复。")
