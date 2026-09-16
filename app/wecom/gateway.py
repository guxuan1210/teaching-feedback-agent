from __future__ import annotations

import asyncio
import inspect
import logging
import re
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from app.chat.generation import GenerationCoordinator
from app.wecom.config import WecomConfig
from app.wecom.handler import BotEvent, process_scope_choice, process_text

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
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.chat_provider = chat_provider
        self.binding_secret = binding_secret
        self.client_factory = client_factory or _default_client_factory
        self.generation_coordinator = generation_coordinator or GenerationCoordinator()
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

        for event_name in (
            "message.image",
            "message.voice",
            "message.file",
            "message.mixed",
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
        match = re.fullmatch(r"scope_(\d+)", detail.get("event_key", ""))
        if not user_id or not body.get("msgid") or match is None:
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
                            int(match.group(1)),
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
