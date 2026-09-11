"""Chat use cases: permissions, conversation lifecycle, and turn preparation.

Write access is owner-only for everyone (admins may only read others'
conversations). The service builds the aggregated context, persists the user
message, and returns everything the route needs to stream a reply. Persistence
of the assistant reply (success or failure) lives here too, so the route stays
a thin transport wrapper.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat.context import (
    ChatContext,
    build_chat_context,
    friendly_date,
)
from app.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageSource,
    _utcnow,
)
from app.chat.prompts import build_messages
from app.core.auth import is_admin
from app.core.ids import new_id
from app.feedback.models import DailyFeedback, SpecialFeedback
from app.reports.models import WeeklyReport
from app.sessions.models import ClassSession

MAX_TITLE_LENGTH = 80
MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_MESSAGES = 20
MAX_CLASS_STUDENTS = 40

_CITATION_RE = re.compile(r"\[S(\d+)\]")


class ChatError(Exception):
    """A domain error carrying the HTTP status code the route should return."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _allowed_class_ids(db: Session, teacher: Teacher) -> set[str] | None:
    if is_admin(teacher):
        return None
    return {
        c.class_id
        for c in catalog_repository.list_classes_for_teacher(db, teacher.teacher_id)
    }


def _can_view(conversation: ChatConversation, teacher: Teacher) -> bool:
    return is_admin(teacher) or conversation.owner_teacher_id == teacher.teacher_id


def _can_write(conversation: ChatConversation, teacher: Teacher) -> bool:
    return conversation.owner_teacher_id == teacher.teacher_id


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
def list_conversations(
    db: Session,
    teacher: Teacher,
    *,
    q: str | None = None,
    archived: bool = False,
    owner_teacher_id: str | None = None,
) -> list[ChatConversation]:
    stmt = select(ChatConversation).where(
        ChatConversation.status == ("archived" if archived else "active")
    )
    if not is_admin(teacher):
        stmt = stmt.where(ChatConversation.owner_teacher_id == teacher.teacher_id)
    elif owner_teacher_id:
        stmt = stmt.where(ChatConversation.owner_teacher_id == owner_teacher_id)

    if q:
        like = f"%{q}%"
        conditions = [ChatConversation.title.like(like)]
        student_ids = list(
            db.scalars(select(Student.student_id).where(Student.name.like(like)))
        )
        class_ids = list(
            db.scalars(select(Class.class_id).where(Class.name.like(like)))
        )
        if student_ids:
            conditions.append(ChatConversation.student_id.in_(student_ids))
        if class_ids:
            conditions.append(ChatConversation.class_id.in_(class_ids))
        stmt = stmt.where(or_(*conditions))

    stmt = stmt.order_by(ChatConversation.last_message_at.desc())
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------
def get_viewable(db: Session, teacher: Teacher, conversation_id: str) -> ChatConversation:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise ChatError("对话不存在", 404)
    if not _can_view(conversation, teacher):
        raise ChatError("无权查看该对话", 403)
    return conversation


def get_writable(db: Session, teacher: Teacher, conversation_id: str) -> ChatConversation:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise ChatError("对话不存在", 404)
    if not _can_write(conversation, teacher):
        raise ChatError("无权修改该对话", 403)
    return conversation


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def create_conversation(
    db: Session,
    teacher: Teacher,
    *,
    scope_type: str,
    class_id: str,
    student_id: str | None,
    date_from: str,
    date_to: str,
) -> ChatConversation:
    scope_type = (scope_type or "").strip()
    if scope_type not in {"student", "class"}:
        raise ChatError("范围类型无效", 422)

    allowed = _allowed_class_ids(db, teacher)
    if allowed is not None and class_id not in allowed:
        raise ChatError("无权访问该班级", 403)

    klass = db.get(Class, class_id)
    if klass is None or klass.status != "active":
        raise ChatError("班级无效或已停用", 422)

    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError:
        raise ChatError("日期格式无效，请使用 YYYY-MM-DD", 422)
    if end < start:
        raise ChatError("开始日期不能晚于结束日期", 422)

    student: Student | None = None
    if scope_type == "student":
        student = db.get(Student, student_id)
        if student is None or student.status != "active":
            raise ChatError("学生无效或已停用", 422)
        if not _has_active_enrollment(db, student_id, class_id):
            raise ChatError("学生未在该班级有效报名", 422)
        title = f"{student.name}教学分析"
    else:
        roster = catalog_repository.active_roster(db, class_id, end)
        if len(roster) > MAX_CLASS_STUDENTS:
            raise ChatError("班级学生超过 40 人，暂不支持班级对话", 422)
        title = f"{klass.name}教学分析"

    conversation = ChatConversation(
        conversation_id=new_id("CHAT"),
        owner_teacher_id=teacher.teacher_id,
        scope_type=scope_type,
        class_id=class_id,
        student_id=student.student_id if student else None,
        title=title,
        date_from=date_from,
        date_to=date_to,
        status="active",
    )
    db.add(conversation)
    db.commit()
    return conversation


def _has_active_enrollment(db: Session, student_id: str, class_id: str) -> bool:
    return (
        db.scalar(
            select(Enrollment).where(
                Enrollment.student_id == student_id,
                Enrollment.class_id == class_id,
                Enrollment.status == "active",
            )
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def rename_conversation(
    db: Session, teacher: Teacher, conversation_id: str, title: str
) -> ChatConversation:
    conversation = get_writable(db, teacher, conversation_id)
    title = (title or "").strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        raise ChatError("标题长度需为 1–80 个字符", 422)
    conversation.title = title
    conversation.updated_at = _utcnow()
    db.commit()
    return conversation


def archive_conversation(
    db: Session, teacher: Teacher, conversation_id: str
) -> ChatConversation:
    conversation = get_writable(db, teacher, conversation_id)
    conversation.status = "archived"
    conversation.updated_at = _utcnow()
    db.commit()
    return conversation


def delete_conversation(
    db: Session, teacher: Teacher, conversation_id: str
) -> None:
    conversation = get_writable(db, teacher, conversation_id)
    db.delete(conversation)
    db.commit()


def update_conversation_dates(
    db: Session,
    teacher: Teacher,
    conversation_id: str,
    date_from: str,
    date_to: str,
) -> ChatConversation:
    conversation = get_writable(db, teacher, conversation_id)
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError:
        raise ChatError("日期格式无效，请使用 YYYY-MM-DD", 422)
    if end < start:
        raise ChatError("开始日期不能晚于结束日期", 422)
    conversation.date_from = date_from
    conversation.date_to = date_to
    conversation.updated_at = _utcnow()
    db.commit()
    return conversation


# ---------------------------------------------------------------------------
# Turn preparation
# ---------------------------------------------------------------------------
@dataclass
class PreparedSend:
    conversation: ChatConversation
    context: ChatContext
    messages: list[dict]
    user_message: ChatMessage
    assistant_message_id: str
    model: str


def _recent_history(db: Session, conversation_id: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.status == "completed",
                ChatMessage.role.in_(("user", "assistant")),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_HISTORY_MESSAGES)
        )
    )
    rows.reverse()
    return [{"role": m.role, "content": m.content} for m in rows]


def _validate_writable_active(db: Session, teacher: Teacher, conversation: ChatConversation) -> None:
    if conversation.status != "active":
        raise ChatError("对话已归档，无法发送", 403)

    klass = db.get(Class, conversation.class_id)
    if klass is None or klass.status != "active":
        raise ChatError("对话关联班级已失效，无法发送", 403)

    allowed = _allowed_class_ids(db, teacher)
    if allowed is not None and conversation.class_id not in allowed:
        raise ChatError("您已失去该班级权限，无法发送", 403)


def prepare_send(
    db: Session,
    teacher: Teacher,
    conversation_id: str,
    content: str,
    model: str,
) -> PreparedSend:
    conversation = get_writable(db, teacher, conversation_id)
    _validate_writable_active(db, teacher, conversation)

    content = (content or "").strip()
    if not content:
        raise ChatError("消息不能为空", 422)
    if len(content) > MAX_MESSAGE_LENGTH:
        raise ChatError("消息超过 4000 字限制", 422)

    history = _recent_history(db, conversation.conversation_id)
    try:
        context = build_chat_context(
            db, conversation, conversation.date_from, conversation.date_to
        )
    except ValueError as exc:
        raise ChatError(str(exc), 422)

    messages = build_messages(history, context.data_block, content)

    user_message = ChatMessage(
        message_id=new_id("MSG"),
        conversation_id=conversation.conversation_id,
        role="user",
        content=content,
        status="completed",
    )
    db.add(user_message)
    conversation.last_message_at = _utcnow()
    conversation.updated_at = _utcnow()
    db.commit()

    return PreparedSend(
        conversation=conversation,
        context=context,
        messages=messages,
        user_message=user_message,
        assistant_message_id=new_id("MSG"),
        model=model,
    )


def prepare_retry(
    db: Session,
    teacher: Teacher,
    conversation_id: str,
    failed_message_id: str,
    model: str,
) -> PreparedSend:
    conversation = get_writable(db, teacher, conversation_id)
    _validate_writable_active(db, teacher, conversation)

    failed = db.get(ChatMessage, failed_message_id)
    if (
        failed is None
        or failed.conversation_id != conversation_id
        or failed.role != "assistant"
        or failed.status != "failed"
    ):
        raise ChatError("无法重试该消息", 422)

    user_message = db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role == "user",
            ChatMessage.status == "completed",
            ChatMessage.created_at <= failed.created_at,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    if user_message is None:
        raise ChatError("未找到可重试的用户消息", 422)

    later_success = db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role == "assistant",
            ChatMessage.status == "completed",
            ChatMessage.created_at > user_message.created_at,
        )
        .limit(1)
    )
    if later_success is not None:
        raise ChatError("该问题已有成功回复，无法重试", 409)

    # Remove the failed reply so a fresh one replaces it (sources cascade).
    db.delete(failed)
    db.commit()

    history = _recent_history(db, conversation.conversation_id)
    try:
        context = build_chat_context(
            db, conversation, conversation.date_from, conversation.date_to
        )
    except ValueError as exc:
        raise ChatError(str(exc), 422)

    messages = build_messages(history, context.data_block, user_message.content)

    conversation.last_message_at = _utcnow()
    conversation.updated_at = _utcnow()
    db.commit()

    return PreparedSend(
        conversation=conversation,
        context=context,
        messages=messages,
        user_message=user_message,
        assistant_message_id=new_id("MSG"),
        model=model,
    )


# ---------------------------------------------------------------------------
# Reply persistence
# ---------------------------------------------------------------------------
def save_assistant_message(
    db: Session,
    prepared: PreparedSend,
    content: str,
    cited: set[int],
) -> ChatMessage:
    snapshot = json.dumps(prepared.context.snapshot, ensure_ascii=False)
    message = ChatMessage(
        message_id=prepared.assistant_message_id,
        conversation_id=prepared.conversation.conversation_id,
        role="assistant",
        content=content,
        status="completed",
        model=prepared.model,
        context_date_from=prepared.context.date_from,
        context_date_to=prepared.context.date_to,
        context_snapshot=snapshot,
    )
    db.add(message)
    for index, source in enumerate(prepared.context.sources, start=1):
        db.add(
            ChatMessageSource(
                message_id=prepared.assistant_message_id,
                source_type=source.source_type,
                source_id=source.source_id,
                cited=1 if index in cited else 0,
            )
        )
    _touch_conversation(db, prepared.conversation)
    db.commit()
    return message


def save_failed_message(
    db: Session,
    prepared: PreparedSend,
    partial_text: str,
    error_message: str,
) -> ChatMessage:
    snapshot = json.dumps(prepared.context.snapshot, ensure_ascii=False)
    message = ChatMessage(
        message_id=prepared.assistant_message_id,
        conversation_id=prepared.conversation.conversation_id,
        role="assistant",
        content=partial_text,
        status="failed",
        model=prepared.model,
        context_date_from=prepared.context.date_from,
        context_date_to=prepared.context.date_to,
        context_snapshot=snapshot,
        error_message=error_message,
    )
    db.add(message)
    _touch_conversation(db, prepared.conversation)
    db.commit()
    return message


def _touch_conversation(db: Session, conversation: ChatConversation) -> None:
    conversation.last_message_at = _utcnow()
    conversation.updated_at = _utcnow()


def parse_citations(text: str, sources: list) -> set[int]:
    cited: set[int] = set()
    for match in _CITATION_RE.finditer(text):
        number = int(match.group(1))
        if 1 <= number <= len(sources):
            cited.add(number)
    return cited


def sources_payload(sources: list, cited: set[int]) -> list[dict]:
    cited_items: list[dict] = []
    other_items: list[dict] = []
    for index, source in enumerate(sources, start=1):
        item = {
            "type": source.source_type,
            "id": source.source_id,
            "label": source.label,
            "url": source.url,
            "cited": index in cited,
        }
        (cited_items if index in cited else other_items).append(item)
    return cited_items + other_items


# ---------------------------------------------------------------------------
# Source resolution for rendering
# ---------------------------------------------------------------------------
def load_message_sources(db: Session, message_id: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(ChatMessageSource).where(ChatMessageSource.message_id == message_id)
        )
    )
    resolved = [_resolve_source(db, row) for row in rows]
    resolved.sort(key=lambda item: (0 if item["cited"] else 1, item["label"]))
    return resolved


def _resolve_source(db: Session, row: ChatMessageSource) -> dict:
    if row.source_type in ("daily", "special"):
        model = DailyFeedback if row.source_type == "daily" else SpecialFeedback
        feedback = db.get(model, row.source_id)
        label = row.source_id
        if feedback is not None:
            session = db.get(ClassSession, feedback.session_id)
            date_str = session.session_date if session else feedback.created_at
            kind = "晚辅反馈" if row.source_type == "daily" else "专项反馈"
            label = f"{friendly_date(date_str)}{kind}"
        return {
            "type": row.source_type,
            "id": row.source_id,
            "label": label,
            "url": f"/history/{row.source_type}/{row.source_id}",
            "cited": bool(row.cited),
        }

    report = db.get(WeeklyReport, row.source_id)
    label = f"周报 {row.source_id}"
    if report is not None:
        label = f"周报 {report.period_start}~{report.period_end}"
    return {
        "type": row.source_type,
        "id": row.source_id,
        "label": label,
        "url": f"/reports/{row.source_id}",
        "cited": bool(row.cited),
    }
