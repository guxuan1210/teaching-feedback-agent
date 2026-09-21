from __future__ import annotations

import asyncio
import inspect
import logging
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy import or_, select

from app.chat.generation import GenerationCoordinator
from app.family.storage import LocalImageStore
from app.catalog.models import Student, Teacher
from app.family.models import (
    FamilyConversation, FamilyMessage, Guardian, GuardianChannelBinding,
    StudentGuardian,
    StudentTeacherAssignment,
)
from app.wecom.config import WecomConfig
from app.wecom.handler import BotEvent, process_scope_choice, process_text
from app.wecom.models import TeacherWecomBinding
from app.wecom.media_handler import (
    download_and_decrypt_images,
    is_bound_teacher,
    parse_media_message,
    process_media_choice,
    process_teacher_media,
)

logger = logging.getLogger(__name__)


class SafeWecomLogger:
    """SDK logger that never forwards message bodies or credentials."""

    def debug(self, _message: str, *_args) -> None:
        return

    def info(self, _message: str, *_args) -> None:
        logger.info("Enterprise WeChat SDK status changed")

    def warn(self, _message: str, *_args) -> None:
        logger.warning("Enterprise WeChat SDK warning")

    def error(self, _message: str, *_args) -> None:
        logger.error("Enterprise WeChat SDK error")


def _default_client_factory(config: WecomConfig):
    from aibot import WSClient, WSClientOptions

    return WSClient(
        WSClientOptions(
            bot_id=config.bot_id,
            secret=config.bot_secret,
            max_reconnect_attempts=-1,
            logger=SafeWecomLogger(),
        )
    )


class WecomGateway:
    def __init__(
        self,
        *,
        config: WecomConfig,
        session_factory,
        chat_provider,
        binding_secret: str,
        client_factory: Callable[[WecomConfig], object] | None = None,
        generation_coordinator: GenerationCoordinator | None = None,
        image_store: LocalImageStore | None = None,
        customer_client=None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.chat_provider = chat_provider
        self.binding_secret = binding_secret
        self.client_factory = client_factory or _default_client_factory
        self.generation_coordinator = generation_coordinator or GenerationCoordinator()
        self.image_store = image_store or LocalImageStore(
            config.media_root, max_bytes=config.media_max_bytes
        )
        self.customer_client = customer_client
        self.client = None
        self.connected = False
        self.authenticated = False
        self.last_error_at: str | None = None
        self._user_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(self) -> None:
        if not self.config.enabled or self.client is not None:
            return
        self.client = self.client_factory(self.config)

        @self.client.on("connected")
        def _connected():
            self.connected = True

        @self.client.on("authenticated")
        def _authenticated():
            self.authenticated = True

        @self.client.on("disconnected")
        def _disconnected(_reason=None):
            self.connected = False
            self.authenticated = False

        @self.client.on("error")
        def _error(_error=None):
            self.last_error_at = datetime.now(timezone.utc).isoformat()

        @self.client.on("message.text")
        async def _text(frame):
            await self._handle_text(frame)

        for event_name in ("message.image", "message.mixed"):
            @self.client.on(event_name)
            async def _media(frame):
                await self._handle_media(frame)

        for event_name in (
            "message.voice",
            "message.file",
            "message.video",
        ):
            @self.client.on(event_name)
            async def _unsupported(frame):
                stream_id = secrets.token_urlsafe(12)
                await self.client.reply_stream(
                    frame, stream_id, "首版仅支持文本消息，请直接输入问题。", True
                )

        @self.client.on("event.enter_chat")
        async def _welcome(frame):
            if hasattr(self.client, "reply_welcome"):
                await self.client.reply_welcome(
                    frame,
                    {
                        "msgtype": "text",
                        "text": {
                            "content": "你好，我是 Teaching Agent。首次使用请发送“绑定 6位码”；绑定后可直接询问学生或班级情况。"
                        },
                    },
                )

        @self.client.on("event.template_card_event")
        async def _template_card_event(frame):
            await self._handle_scope_choice(frame)

        try:
            await self.client.connect()
        except Exception:  # noqa: BLE001 - connection state is exposed by health endpoint
            self.last_error_at = datetime.now(timezone.utc).isoformat()
            self.client = None
            raise

    async def stop(self) -> None:
        if self.client is None:
            return
        result = self.client.disconnect()
        if inspect.isawaitable(result):
            await result
        self.connected = False
        self.authenticated = False
        self.client = None

    async def notify_teacher(self, conversation_id: str) -> bool:
        """Notify only the current primary teacher of a waiting family conversation."""
        effective = date.today().isoformat()
        with self.session_factory() as db:
            conversation = db.get(FamilyConversation, conversation_id)
            if (
                conversation is None
                or conversation.channel != "wecom_customer"
                or conversation.status != "waiting_teacher"
            ):
                return False
            teacher_id = db.scalar(select(StudentTeacherAssignment.teacher_id).join(
                Teacher, Teacher.teacher_id == StudentTeacherAssignment.teacher_id
            ).where(
                StudentTeacherAssignment.student_id == conversation.student_id,
                StudentTeacherAssignment.role == "primary",
                StudentTeacherAssignment.status == "active",
                StudentTeacherAssignment.start_date <= effective,
                or_(StudentTeacherAssignment.end_date.is_(None), StudentTeacherAssignment.end_date >= effective),
                Teacher.status == "active",
            ).order_by(StudentTeacherAssignment.start_date.desc()).limit(1))
            if teacher_id is None:
                return False
            teacher_binding = db.scalar(select(TeacherWecomBinding.wecom_user_id).where(
                TeacherWecomBinding.teacher_id == teacher_id
            ))
            guardian = db.get(Guardian, conversation.guardian_id)
            student = db.get(Student, conversation.student_id)
            relation = db.scalar(select(StudentGuardian.relation_id).where(
                StudentGuardian.student_id == conversation.student_id,
                StudentGuardian.guardian_id == conversation.guardian_id,
                StudentGuardian.status == "active",
            ))
            parent_binding = db.scalar(select(GuardianChannelBinding.binding_id).where(
                GuardianChannelBinding.channel == "wecom_customer",
                GuardianChannelBinding.external_user_id == conversation.channel_conversation_id,
                GuardianChannelBinding.guardian_id == conversation.guardian_id,
                GuardianChannelBinding.status == "active",
            ))
            message = db.scalar(select(FamilyMessage).where(
                FamilyMessage.conversation_id == conversation_id,
                FamilyMessage.direction == "inbound",
                FamilyMessage.sender_type == "guardian",
            ).order_by(FamilyMessage.created_at.desc(), FamilyMessage.message_id.desc()).limit(1))
            if (
                not teacher_binding or guardian is None or guardian.status != "active"
                or student is None or student.status != "active" or relation is None
                or parent_binding is None or message is None
            ):
                return False
            relationship = {"father": "父亲", "mother": "母亲", "other": "监护人"}.get(
                guardian.relationship_type, "监护人"
            )
            # Render untrusted names and parent text as plain markdown text.
            def safe(value: str) -> str:
                return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            content = (
                f"**家长请求老师回复**\n\n"
                f"学生：{safe(student.name)}（{safe(student.student_id)}）\n"
                f"家长：{safe(relationship)} {safe(guardian.name)}\n"
                f"问题：{safe(message.content)}\n\n"
                f"会话编号：{safe(conversation.conversation_id)}\n"
                f"请回复：`回复 {safe(conversation.conversation_id)} 回复内容`"
            )
        if self.client is None:
            return False
        try:
            result = self.client.send_message(
                teacher_binding,
                {"msgtype": "markdown", "markdown": {"content": content}},
            )
            if inspect.isawaitable(result):
                await result
            return True
        except Exception as exc:  # noqa: BLE001 - do not log parent content
            logger.warning("WeCom family teacher notification failed type=%s", type(exc).__name__)
            self.last_error_at = datetime.now(timezone.utc).isoformat()
            return False

    async def _produce_events(self, body: dict, queue: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()

        def produce() -> None:
            try:
                with self.session_factory() as db:
                    events = process_text(
                        db,
                        self.chat_provider,
                        self.binding_secret,
                        body.get("msgid", ""),
                        (body.get("from") or {}).get("userid", ""),
                        (body.get("text") or {}).get("content", ""),
                        chattype=body.get("chattype", "single"),
                        public_base_url=self.config.public_base_url,
                        coordinator=self.generation_coordinator,
                        customer_client=self.customer_client,
                    )
                    for event in events:
                        asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
            except Exception as exc:  # noqa: BLE001 - do not log message-bearing exceptions
                logger.error(
                    "Enterprise WeChat message processing failed type=%s",
                    type(exc).__name__,
                )
                asyncio.run_coroutine_threadsafe(
                    queue.put(BotEvent("failed", "处理失败，请稍后重试。")), loop
                ).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        await asyncio.to_thread(produce)

    async def _handle_text(self, frame: dict) -> None:
        body = frame.get("body") or {}
        user_id = (body.get("from") or {}).get("userid", "")
        if not body.get("msgid") or not user_id:
            return
        async with self._user_locks[user_id]:
            queue: asyncio.Queue[BotEvent | None] = asyncio.Queue()
            producer = asyncio.create_task(self._produce_events(body, queue))
            stream_id = secrets.token_urlsafe(12)
            last_content = ""
            last_sent = 0.0
            loop = asyncio.get_running_loop()
            while True:
                event = await queue.get()
                if event is None:
                    break
                if event.choices:
                    buttons = [
                        {"text": str(choice["label"]), "key": f"scope_{index}", "style": 1}
                        for index, choice in enumerate(event.choices[:6])
                    ]
                    await self.client.reply_template_card(
                        frame,
                        {
                            "card_type": "button_interaction",
                            "main_title": {"title": "请选择分析对象", "desc": "存在同名学生或多个班级"},
                            "button_list": buttons,
                            "task_id": f"scope_{body['msgid'][:40]}",
                        },
                    )
                    continue
                now = loop.time()
                if not event.finish and last_content:
                    if len(event.content) - len(last_content) < 50 and now - last_sent < 0.3:
                        continue
                await self.client.reply_stream(
                    frame, stream_id, event.content, event.finish
                )
                last_content = event.content
                last_sent = now
            await producer

    async def _handle_scope_choice(self, frame: dict) -> None:
        body = frame.get("body") or {}
        user_id = (body.get("from") or {}).get("userid", "")
        event = body.get("event") or {}
        detail = event.get("template_card_event") or event
        event_key = detail.get("event_key", "")
        media_match = re.fullmatch(r"media_(PMA_[a-f0-9]{32})_(S[A-Za-z0-9_-]{0,159})", event_key)
        scope_match = re.fullmatch(r"scope_(\d+)", event_key)
        if not user_id or not body.get("msgid") or (media_match is None and scope_match is None):
            return
        if media_match is not None:
            task_id = detail.get("task_id") or f"media_{media_match.group(1)}"
            await self.client.update_template_card(
                frame,
                {"card_type": "text_notice", "main_title": {"title": "选择已收到", "desc": "正在归档图片"}, "task_id": task_id},
            )
            async with self._user_locks[user_id]:
                try:
                    def choose():
                        with self.session_factory() as db:
                            return process_media_choice(
                                db, self.image_store, wecom_user_id=user_id,
                                event_key=event_key,
                            )
                    result = await asyncio.to_thread(choose)
                except Exception as exc:  # noqa: BLE001 - exception text may contain media URLs
                    logger.error("WeCom media choice failed type=%s", type(exc).__name__)
                    result = None
                content = result.content if result else "处理失败，请稍后重试。"
                await self.client.send_message(
                    user_id, {"msgtype": "markdown", "markdown": {"content": content}}
                )
            return
        task_id = detail.get("task_id") or "scope_choice"
        await self.client.update_template_card(
            frame,
            {
                "card_type": "text_notice",
                "main_title": {"title": "选择已收到", "desc": "Teaching Agent 正在处理"},
                "task_id": task_id,
            },
        )
        async with self._user_locks[user_id]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[BotEvent | None] = asyncio.Queue()

            def produce() -> None:
                try:
                    with self.session_factory() as db:
                        for result in process_scope_choice(
                            db,
                            self.chat_provider,
                            self.binding_secret,
                            body["msgid"],
                            user_id,
                            int(scope_match.group(1)),
                            public_base_url=self.config.public_base_url,
                            coordinator=self.generation_coordinator,
                        ):
                            asyncio.run_coroutine_threadsafe(
                                queue.put(result), loop
                            ).result()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Enterprise WeChat scope choice failed type=%s",
                        type(exc).__name__,
                    )
                    asyncio.run_coroutine_threadsafe(
                        queue.put(BotEvent("failed", "处理失败，请稍后重试。")), loop
                    ).result()
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

            producer = asyncio.create_task(asyncio.to_thread(produce))
            sent_generating = False
            while True:
                result = await queue.get()
                if result is None:
                    break
                if not result.finish:
                    if sent_generating:
                        continue
                    sent_generating = True
                await self.client.send_message(
                    user_id,
                    {"msgtype": "markdown", "markdown": {"content": result.content}},
                )
            await producer

    async def _handle_media(self, frame: dict) -> None:
        body = frame.get("body") or {}
        user_id = (body.get("from") or {}).get("userid", "")
        if not body.get("msgid") or not user_id:
            return
        if body.get("chattype") != "single":
            await self.client.reply_stream(
                frame, secrets.token_urlsafe(12), "图片归档仅支持老师私聊机器人。", True
            )
            return
        async with self._user_locks[user_id]:
            try:
                parsed = parse_media_message(body)
                def is_authorized():
                    with self.session_factory() as db:
                        return is_bound_teacher(db, user_id)
                if not await asyncio.to_thread(is_authorized):
                    await self.client.reply_stream(
                        frame, secrets.token_urlsafe(12),
                        "请先登录 Teaching Agent 并绑定老师账号。", True,
                    )
                    return
                payloads = await download_and_decrypt_images(
                    self.client, parsed, max_bytes=self.config.media_max_bytes
                )

                def archive():
                    with self.session_factory() as db:
                        return process_teacher_media(
                            db, self.image_store, binding_secret=self.binding_secret,
                            wecom_user_id=user_id, parsed=parsed, payloads=payloads,
                            pending_media_minutes=self.config.pending_media_minutes,
                        )

                result = await asyncio.to_thread(archive)
            except Exception as exc:  # noqa: BLE001 - never expose URLs, keys, or message bodies
                logger.warning("WeCom media ingestion failed type=%s", type(exc).__name__)
                result = None
            if result is None:
                content = "图片处理失败，请检查格式和大小后重试。"
            elif result.choices:
                buttons = [
                    {"text": name, "key": key, "style": 1}
                    for _student_id, name, key in result.choices
                ]
                await self.client.reply_template_card(
                    frame,
                    {
                        "card_type": "button_interaction",
                        "main_title": {"title": "请选择图片所属学生", "desc": "未能从消息中识别学生"},
                        "button_list": buttons,
                        "task_id": f"media_{result.pending_id}",
                    },
                )
                return
            else:
                content = result.content
            await self.client.reply_stream(
                frame, secrets.token_urlsafe(12), content, True
            )
