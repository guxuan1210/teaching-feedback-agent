"""Teaching assistant: a two-pane chat page and SSE streaming endpoints.

Page routes render the server-side conversation list and message history. The
``/messages`` and ``/retry`` endpoints stream a model reply as ``text/event-stream``
so the browser can render deltas, stop, and retry without page reloads.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import repository as catalog_repository
from app.catalog.models import Class, Enrollment, Student, Teacher
from app.chat import service
from app.chat.generation import stream_generation
from app.chat.context import friendly_date
from app.chat.models import ChatConversation, ChatMessage
from app.chat.providers import ChatProvider
from app.core.auth import is_admin, require_login
from app.core.database import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

SCOPE_TYPE_LABELS = {"student": "学生", "class": "班级"}


class SendRequest(BaseModel):
    content: str


class RetryRequest(BaseModel):
    message_id: str


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _default_range() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=27)).isoformat(), today.isoformat()


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


def _student_class_map(db: Session, students, classes) -> dict[str, list[str]]:
    allowed = {c.class_id for c in classes}
    mapping: dict[str, list[str]] = {}
    for student in students:
        class_ids = list(
            db.scalars(
                select(Enrollment.class_id).where(
                    Enrollment.student_id == student.student_id,
                    Enrollment.status == "active",
                    Enrollment.class_id.in_(allowed),
                )
            )
        )
        mapping[student.student_id] = class_ids
    return mapping


def _scope_label(db: Session, conversation: ChatConversation) -> str:
    if conversation.scope_type == "student" and conversation.student_id:
        student = db.get(Student, conversation.student_id)
        return student.name if student else conversation.student_id
    klass = db.get(Class, conversation.class_id)
    return klass.name if klass else conversation.class_id


def _group_conversations(
    db: Session, teacher: Teacher, conversations: list[ChatConversation]
) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {"今天": [], "过去 7 天": [], "更早": []}
    for conversation in conversations:
        row = {
            "conversation": conversation,
            "scope_name": _scope_label(db, conversation),
            "is_owner": conversation.owner_teacher_id == teacher.teacher_id,
        }
        groups[_group_label(conversation.last_message_at)].append(row)
    return [(label, groups[label]) for label in ("今天", "过去 7 天", "更早") if groups[label]]


def _group_label(iso: str) -> str:
    try:
        message_date = date.fromisoformat(iso[:10])
    except ValueError:
        return "更早"
    today = date.today()
    if message_date == today:
        return "今天"
    if (today - message_date).days <= 7:
        return "过去 7 天"
    return "更早"


def _message_rows(db: Session, conversation: ChatConversation) -> list[dict]:
    messages = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.conversation_id)
            .order_by(ChatMessage.created_at)
        )
    )
    rows = []
    for message in messages:
        row = {
            "message": message,
            "paragraphs": _paragraphs(message.content),
            "channel_label": _channel_label(message.channel),
            "sources": service.load_message_sources(db, message.message_id)
            if message.role == "assistant" and message.status == "completed"
            else [],
        }
        rows.append(row)
    return rows


def _channel_label(channel: str | None) -> str:
    return {"web": "网页", "wecom": "企业微信"}.get(channel or "", "")


def _paragraphs(text: str) -> list[str]:
    return [part for part in (text or "").split("\n") if part.strip()]


# ---------------------------------------------------------------------------
# Page context
# ---------------------------------------------------------------------------
def _index_context(
    request: Request,
    db: Session,
    teacher: Teacher,
    *,
    conversation: ChatConversation | None = None,
    q: str = "",
    archived: bool = False,
    owner_teacher_id: str | None = None,
    errors: list[str] | None = None,
    form: dict | None = None,
) -> dict:
    classes, students = _scoped_choices(db, teacher)
    conversations = service.list_conversations(
        db, teacher, q=q, archived=archived, owner_teacher_id=owner_teacher_id
    )

    default_from, default_to = _default_range()
    if form is None:
        form = {}

    context: dict = {
        "classes": classes,
        "students": students,
        "student_class_map": _student_class_map(db, students, classes),
        "groups": _group_conversations(db, teacher, conversations),
        "q": q,
        "archived": archived,
        "owner_teacher_id": owner_teacher_id,
        "errors": errors or [],
        "form": {
            "scope_type": form.get("scope_type", "student"),
            "class_id": form.get("class_id", ""),
            "student_id": form.get("student_id", ""),
            "date_from": form.get("date_from", default_from),
            "date_to": form.get("date_to", default_to),
        },
        "SCOPE_TYPE_LABELS": SCOPE_TYPE_LABELS,
        "model_configured": request.app.state.chat_provider is not None,
        "conversation": conversation,
        "can_write": False,
        "is_owner": False,
        "scope_label": "",
        "messages": [],
    }

    if conversation is not None:
        context["scope_label"] = _scope_label(db, conversation)
        context["can_write"] = conversation.owner_teacher_id == teacher.teacher_id
        context["is_owner"] = conversation.owner_teacher_id == teacher.teacher_id
        context["messages"] = _message_rows(db, conversation)

    return context


def _chat_template(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(
        request, f"chat/{name}", context, status_code=status_code
    )


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@router.get("/chat", response_class=HTMLResponse)
def chat_index(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    q = request.query_params.get("q") or ""
    archived = request.query_params.get("archived") == "1"
    owner_teacher_id = request.query_params.get("owner_teacher_id")
    errors = request.session.pop("flash_errors", [])
    return _chat_template(
        request,
        "index.html",
        _index_context(
            request, db, teacher, q=q, archived=archived,
            owner_teacher_id=owner_teacher_id, errors=errors,
        ),
    )


@router.get("/chat/{conversation_id}", response_class=HTMLResponse)
def chat_detail(
    request: Request,
    conversation_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        conversation = service.get_viewable(db, teacher, conversation_id)
    except service.ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    q = request.query_params.get("q") or ""
    return _chat_template(
        request,
        "index.html",
        _index_context(request, db, teacher, conversation=conversation, q=q),
    )


# ---------------------------------------------------------------------------
# Form actions
# ---------------------------------------------------------------------------
@router.post("/chat")
def chat_create(
    request: Request,
    scope_type: str = Form(...),
    class_id: str = Form(...),
    student_id: str = Form(""),
    date_from: str = Form(...),
    date_to: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    form = {
        "scope_type": scope_type,
        "class_id": class_id,
        "student_id": student_id,
        "date_from": date_from,
        "date_to": date_to,
    }

    def _error(message: str):
        return _chat_template(
            request,
            "index.html",
            _index_context(request, db, teacher, form=form, errors=[message]),
            status_code=422,
        )

    try:
        conversation = service.create_conversation(
            db,
            teacher,
            scope_type=scope_type,
            class_id=class_id,
            student_id=student_id or None,
            date_from=date_from,
            date_to=date_to,
        )
    except service.ChatError as exc:
        if exc.status_code in (403, 404):
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        return _error(str(exc))

    return RedirectResponse(f"/chat/{conversation.conversation_id}", status_code=303)


@router.post("/chat/{conversation_id}/rename")
def chat_rename(
    request: Request,
    conversation_id: str,
    title: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        service.rename_conversation(db, teacher, conversation_id, title)
    except service.ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return RedirectResponse(f"/chat/{conversation_id}", status_code=303)


@router.post("/chat/{conversation_id}/archive")
def chat_archive(
    conversation_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        service.archive_conversation(db, teacher, conversation_id)
    except service.ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/{conversation_id}/delete")
def chat_delete(
    conversation_id: str,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        service.delete_conversation(db, teacher, conversation_id)
    except service.ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/{conversation_id}/date-range")
def chat_update_dates(
    request: Request,
    conversation_id: str,
    date_from: str = Form(...),
    date_to: str = Form(...),
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    try:
        service.update_conversation_dates(
            db, teacher, conversation_id, date_from, date_to
        )
    except service.ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return RedirectResponse(f"/chat/{conversation_id}", status_code=303)


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------
def _acquire_generation(request: Request, conversation_id: str) -> bool:
    return request.app.state.generation_coordinator.try_acquire(conversation_id)


def _release_generation(request: Request, conversation_id: str) -> None:
    request.app.state.generation_coordinator.release(conversation_id)


def _sse(name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n"


def _stream_reply(
    request: Request,
    db: Session,
    prepared: service.PreparedSend,
    provider: ChatProvider,
):
    conversation_id = prepared.conversation.conversation_id
    try:
        for event in stream_generation(db, prepared, provider):
            yield _sse(event.name, event.payload)
    finally:
        _release_generation(request, conversation_id)


# ---------------------------------------------------------------------------
# Streaming routes
# ---------------------------------------------------------------------------
@router.post("/chat/{conversation_id}/messages")
def chat_send(
    request: Request,
    conversation_id: str,
    payload: SendRequest,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    provider = request.app.state.chat_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="模型未配置，请联系管理员")

    if not _acquire_generation(request, conversation_id):
        raise HTTPException(status_code=409, detail="该对话正在回答")

    try:
        prepared = service.prepare_send(
            db, teacher, conversation_id, payload.content, provider.model,
            channel="web",
        )
    except service.ChatError as exc:
        _release_generation(request, conversation_id)
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception:
        _release_generation(request, conversation_id)
        logger.exception("Unexpected error while preparing a chat message")
        raise HTTPException(status_code=500, detail="生成准备失败，请稍后重试")

    service.mark_current_conversation(db, teacher, prepared.conversation)

    return StreamingResponse(
        _stream_reply(request, db, prepared, provider),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{conversation_id}/retry")
def chat_retry(
    request: Request,
    conversation_id: str,
    payload: RetryRequest,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    provider = request.app.state.chat_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="模型未配置，请联系管理员")

    if not _acquire_generation(request, conversation_id):
        raise HTTPException(status_code=409, detail="该对话正在回答")

    try:
        prepared = service.prepare_retry(
            db, teacher, conversation_id, payload.message_id, provider.model
        )
    except service.ChatError as exc:
        _release_generation(request, conversation_id)
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception:
        _release_generation(request, conversation_id)
        logger.exception("Unexpected error while preparing a chat retry")
        raise HTTPException(status_code=500, detail="生成准备失败，请稍后重试")

    service.mark_current_conversation(db, teacher, prepared.conversation)

    return StreamingResponse(
        _stream_reply(request, db, prepared, provider),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
