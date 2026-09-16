from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.core.auth import is_admin
from app.chat import service as chat_service
from app.chat.generation import GenerationCoordinator, stream_generation
from app.chat.models import ChatConversation, ChatMessage
from app.chat.providers import ChatProvider
from app.wecom.binding import bind_user, unbind_teacher
from app.wecom.models import (
    TeacherWecomBinding,
    WecomChatState,
    WecomInboundMessage,
)
from app.wecom.scope import ScopeChoice, resolve_scope

_BIND_RE = re.compile(r"^绑定\s*(\d{6})$")


@dataclass(frozen=True)
class BotEvent:
    status: str
    content: str
    finish: bool = True
    choices: list[dict[str, str | None]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finish(db: Session, inbound: WecomInboundMessage, status: str) -> None:
    inbound.status = status
    inbound.completed_at = _now()
    db.commit()


def _state(db: Session, wecom_user_id: str) -> WecomChatState:
    state = db.get(WecomChatState, wecom_user_id)
    if state is None:
        state = WecomChatState(wecom_user_id=wecom_user_id)
        db.add(state)
        db.commit()
    return state


def _scope_header(scope: ScopeChoice) -> str:
    name = scope.student_name or scope.class_name
    return f"分析对象：{name}｜{scope.date_from} 至 {scope.date_to}"


def _scope_from_conversation(db: Session, conversation: ChatConversation) -> ScopeChoice:
    klass = db.get(Class, conversation.class_id)
    student = db.get(Student, conversation.student_id) if conversation.student_id else None
    return ScopeChoice(
        conversation.scope_type,
        conversation.class_id,
        klass.name if klass else conversation.class_id,
        conversation.student_id,
        student.name if student else None,
        conversation.date_from,
        conversation.date_to,
    )


def _save_scope(db: Session, state: WecomChatState, scope: ScopeChoice) -> None:
    state.scope_type = scope.scope_type
    state.class_id = scope.class_id
    state.student_id = scope.student_id
    state.date_from = scope.date_from
    state.date_to = scope.date_to
    state.pending_scope_json = None
    state.pending_question = None
    state.updated_at = _now()
    db.commit()


def _conversation_for_scope(
    db: Session, teacher: Teacher, state: WecomChatState, scope: ScopeChoice
) -> ChatConversation:
    conversation = (
        db.get(ChatConversation, state.conversation_id)
        if state.conversation_id
        else None
    )
    reusable = (
        conversation is not None
        and conversation.status == "active"
        and conversation.owner_teacher_id == teacher.teacher_id
        and conversation.scope_type == scope.scope_type
        and conversation.class_id == scope.class_id
        and conversation.student_id == scope.student_id
        and conversation.date_from == scope.date_from
        and conversation.date_to == scope.date_to
    )
    if reusable:
        return conversation
    conversation = chat_service.create_conversation(
        db,
        teacher,
        scope_type=scope.scope_type,
        class_id=scope.class_id,
        student_id=scope.student_id,
        date_from=scope.date_from,
        date_to=scope.date_to,
    )
    state.conversation_id = conversation.conversation_id
    state.updated_at = _now()
    db.commit()
    return conversation


def _choice_payload(scope: ScopeChoice) -> dict[str, str | None]:
    return {
        "scope_type": scope.scope_type,
        "class_id": scope.class_id,
        "student_id": scope.student_id,
        "label": scope.label,
        "class_name": scope.class_name,
        "student_name": scope.student_name,
        "date_from": scope.date_from,
        "date_to": scope.date_to,
    }


def _binding_failures(db: Session, wecom_user_id: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    return int(
        db.scalar(
            select(func.count()).select_from(WecomInboundMessage).where(
                WecomInboundMessage.wecom_user_id == wecom_user_id,
                WecomInboundMessage.status == "binding_failed",
                WecomInboundMessage.received_at >= cutoff,
            )
        )
        or 0
    )


def _choice_is_allowed(db: Session, teacher: Teacher, scope: ScopeChoice) -> bool:
    klass = db.get(Class, scope.class_id)
    if (
        klass is None
        or klass.status != "active"
        or (not is_admin(teacher) and klass.head_teacher_id != teacher.teacher_id)
    ):
        return False
    if scope.scope_type == "class":
        return scope.student_id is None
    student = db.get(Student, scope.student_id)
    if student is None or student.status != "active":
        return False
    return db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student.student_id,
            Enrollment.class_id == klass.class_id,
            Enrollment.status == "active",
        )
    ) is not None


def _binding_reply(status: str, teacher_name: str | None) -> BotEvent:
    messages = {
        "completed": f"绑定成功：{teacher_name}。现在可以直接问“分析张三最近的表现”。",
        "invalid_code": "绑定码无效，请在 Teaching Agent 的账号设置中重新生成。",
        "expired_code": "绑定码已过期，请在 Teaching Agent 的账号设置中重新生成。",
        "inactive_teacher": "教师账号已停用，请联系管理员。",
        "already_bound": f"当前企业微信已绑定：{teacher_name}。",
        "teacher_already_bound": f"{teacher_name}已绑定其他企业微信，请先在网页解除绑定。",
    }
    return BotEvent("completed" if status in {"completed", "already_bound"} else "failed", messages[status])


def _stream_answer(
    db: Session,
    teacher: Teacher,
    state: WecomChatState,
    inbound: WecomInboundMessage,
    provider: ChatProvider,
    prepared,
    conversation: ChatConversation,
    scope: ScopeChoice,
    public_base_url: str | None,
) -> Iterator[BotEvent]:
    chat_service.mark_current_conversation(db, teacher, conversation)
    state.conversation_id = conversation.conversation_id
    state.updated_at = _now()
    db.commit()
    header = _scope_header(scope)
    yield BotEvent("generating", f"{header}\n\n正在分析…", finish=False)
    answer = ""
    failed = False
    for event in stream_generation(db, prepared, provider):
        if event.name == "delta":
            answer += event.payload["text"]
            yield BotEvent("generating", f"{header}\n\n{answer}", finish=False)
        elif event.name == "error":
            failed = True
            state.last_failed_message_id = prepared.assistant_message_id
            state.updated_at = _now()
            db.commit()
            _finish(db, inbound, "failed")
            yield BotEvent("failed", f"{header}\n\n{event.payload['message']}")
        elif event.name == "done":
            state.last_failed_message_id = None
            state.updated_at = _now()
            db.commit()

    if failed:
        return
    suffix = ""
    if public_base_url:
        link = f"{public_base_url.rstrip('/')}/chat/{conversation.conversation_id}"
        suffix = f"\n\n[查看依据]({link})（打开后需要登录）"
    else:
        suffix = "\n\n可在 Teaching Agent 网页的教学助手中查看依据。"
    _finish(db, inbound, "completed")
    yield BotEvent("completed", f"{header}\n\n{answer}{suffix}")


def process_text(
    db: Session,
    provider: ChatProvider | None,
    secret_key: str,
    message_id: str,
    wecom_user_id: str,
    text: str,
    *,
    chattype: str = "single",
    today: date | None = None,
    public_base_url: str | None = None,
    forced_scope: ScopeChoice | None = None,
    coordinator: GenerationCoordinator | None = None,
) -> Iterator[BotEvent]:
    coordinator = coordinator or GenerationCoordinator()
    if db.get(WecomInboundMessage, message_id) is not None:
        return
    inbound = WecomInboundMessage(
        message_id=message_id,
        wecom_user_id=wecom_user_id,
        status="processing",
    )
    db.add(inbound)
    db.commit()

    if chattype != "single":
        _finish(db, inbound, "failed")
        yield BotEvent("failed", "为保护学生信息，Teaching Agent 首版仅支持老师私聊。")
        return

    content = (text or "").strip()
    match = _BIND_RE.fullmatch(content)
    if match:
        if _binding_failures(db, wecom_user_id) >= 5:
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "绑定尝试次数过多，请10分钟后再试。")
            return
        outcome = bind_user(db, wecom_user_id, match.group(1), secret_key)
        _finish(
            db,
            inbound,
            "binding_failed" if outcome.status == "invalid_code" else outcome.status,
        )
        yield _binding_reply(outcome.status, outcome.teacher_name)
        return

    binding = db.get(TeacherWecomBinding, wecom_user_id)
    if binding is None:
        _finish(db, inbound, "binding_required")
        yield BotEvent(
            "binding_required",
            "请先登录 Teaching Agent，在“账号设置”生成6位绑定码，然后发送：绑定 123456",
        )
        return
    teacher = db.get(Teacher, binding.teacher_id)
    if teacher is None or teacher.status != "active":
        _finish(db, inbound, "failed")
        yield BotEvent("failed", "教师账号已停用，请联系管理员。")
        return
    if content == "解除绑定":
        unbind_teacher(db, teacher.teacher_id)
        _finish(db, inbound, "completed")
        yield BotEvent("completed", "已解除企业微信绑定。如需继续使用，请重新生成绑定码。")
        return
    if content == "帮助":
        _finish(db, inbound, "completed")
        yield BotEvent(
            "completed",
            "直接发送问题即可，例如：\n- 分析张三最近的表现\n- 帮我写一段给李明家长的话\n- 总结三年级A班最近的共性问题\n\n默认分析最近28天。",
        )
        return

    state = _state(db, wecom_user_id)

    if content == "重试":
        if not state.last_failed_message_id:
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "当前没有可重试的回答。")
            return
        if provider is None:
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "模型暂未配置，请联系管理员。")
            return
        failed_message = db.get(ChatMessage, state.last_failed_message_id)
        if failed_message is None:
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "当前没有可重试的回答。")
            return
        conversation_id = failed_message.conversation_id
        if not coordinator.try_acquire(conversation_id):
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "当前问题仍在分析，请稍后重试。")
            return
        try:
            try:
                prepared = chat_service.prepare_retry(
                    db,
                    teacher,
                    conversation_id,
                    state.last_failed_message_id,
                    provider.model,
                )
            except chat_service.ChatError as exc:
                _finish(db, inbound, "failed")
                yield BotEvent("failed", str(exc))
                return
            conversation = prepared.conversation
            scope = _scope_from_conversation(db, conversation)
            yield from _stream_answer(
                db, teacher, state, inbound, provider, prepared, conversation, scope,
                public_base_url,
            )
        finally:
            coordinator.release(conversation_id)
        return

    scope: ScopeChoice
    if forced_scope is not None:
        if not _choice_is_allowed(db, teacher, forced_scope):
            _finish(db, inbound, "failed")
            yield BotEvent("failed", "该分析对象已失效或无权访问，请重新提问。")
            return
        scope = forced_scope
        _save_scope(db, state, scope)
        conversation = _conversation_for_scope(db, teacher, state, scope)
    else:
        scope_result = resolve_scope(db, teacher, content, today=today)
        if scope_result.status == "scope_required":
            current = chat_service.get_current_conversation(db, teacher)
            if current is None:
                _finish(db, inbound, "scope_required")
                yield BotEvent(
                    "scope_required",
                    "请在问题中说出学生或班级，例如：“分析张三最近的表现”。",
                )
                return
            conversation = current
            scope = _scope_from_conversation(db, conversation)
        elif scope_result.status == "scope_ambiguous":
            choices = [_choice_payload(item) for item in scope_result.candidates]
            state.pending_scope_json = json.dumps(choices, ensure_ascii=False)
            state.pending_question = content
            state.updated_at = _now()
            db.commit()
            _finish(db, inbound, "scope_ambiguous")
            labels = "\n".join(f"- {item['label']}" for item in choices)
            yield BotEvent(
                "scope_ambiguous", f"找到多个同名对象，请选择：\n{labels}", choices=choices
            )
            return
        else:
            scope = scope_result.scope
            _save_scope(db, state, scope)
            conversation = _conversation_for_scope(db, teacher, state, scope)

    if provider is None:
        _finish(db, inbound, "failed")
        yield BotEvent("failed", "模型暂未配置，请联系管理员。")
        return

    if not coordinator.try_acquire(conversation.conversation_id):
        _finish(db, inbound, "failed")
        yield BotEvent("failed", "当前问题仍在分析，请稍后重试。")
        return
    try:
        try:
            prepared = chat_service.prepare_send(
                db, teacher, conversation.conversation_id, content, provider.model,
                channel="wecom",
            )
        except chat_service.ChatError as exc:
            _finish(db, inbound, "failed")
            yield BotEvent("failed", str(exc))
            return
        yield from _stream_answer(
            db, teacher, state, inbound, provider, prepared, conversation, scope,
            public_base_url,
        )
    finally:
        coordinator.release(conversation.conversation_id)


def process_scope_choice(
    db: Session,
    provider: ChatProvider | None,
    secret_key: str,
    message_id: str,
    wecom_user_id: str,
    choice_index: int,
    *,
    today: date | None = None,
    public_base_url: str | None = None,
    coordinator: GenerationCoordinator | None = None,
) -> Iterator[BotEvent]:
    binding = db.get(TeacherWecomBinding, wecom_user_id)
    state = db.get(WecomChatState, wecom_user_id)
    if (
        binding is None
        or state is None
        or not state.pending_scope_json
        or not state.pending_question
    ):
        yield BotEvent("failed", "该选择已失效，请重新提问。")
        return
    try:
        choices = json.loads(state.pending_scope_json)
        item = choices[choice_index]
        scope = ScopeChoice(
            scope_type=item["scope_type"],
            class_id=item["class_id"],
            class_name=item["class_name"],
            student_id=item.get("student_id"),
            student_name=item.get("student_name"),
            date_from=item["date_from"],
            date_to=item["date_to"],
        )
    except (ValueError, TypeError, KeyError, IndexError):
        yield BotEvent("failed", "该选择已失效，请重新提问。")
        return
    question = state.pending_question
    state.pending_scope_json = None
    state.pending_question = None
    state.updated_at = _now()
    db.commit()
    yield from process_text(
        db,
        provider,
        secret_key,
        message_id,
        wecom_user_id,
        question,
        today=today,
        public_base_url=public_base_url,
        forced_scope=scope,
        coordinator=coordinator,
    )
