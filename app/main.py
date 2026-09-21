from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import inspect
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.routes import router as auth_router
from app.catalog.routes import router as catalog_router
from app.chat.generation import GenerationCoordinator
from app.chat.providers import (
    ChatProvider,
    build_chat_provider_from_env,
    load_model_config_from_dotenv,
)
from app.chat.routes import router as chat_router
from app.export.routes import router as export_router
from app.feedback.routes import router as feedback_router
from app.family.routes import router as family_router
from app.history.routes import router as history_router
from app.profiles.routes import router as profiles_router
from app.reports.generators import AIReportGenerator
from app.reports.providers import build_ai_generator_from_env, build_openai_invoke
from app.reports.rewriter import build_rewriter, build_rewriter_from_env
from app.reports.routes import router as reports_router
from app.sessions.routes import router as sessions_router
from app.wecom.routes import router as wecom_router
from app.wecom.config import WecomConfig, load_wecom_config_from_dotenv
from app.wecom.gateway import WecomGateway
from app.wecom_customer.client import WecomCustomerClient
from app.wecom_customer.config import WecomCustomerConfig, load_customer_config
from app.wecom_customer.crypto import WecomCallbackCrypto
from app.wecom_customer.handler import process_parent_text
from app.wecom_customer.routes import create_router as create_customer_router
from app.family.models import FamilyConversation, FamilyMessage
from app.family.storage import LocalImageStore
from app.family.relationships import sync_head_teacher_assignments
from app.family import conversations as family_conversations
from sqlalchemy import select
from sqlalchemy import update
from app.core.database import (
    build_engine,
    build_session_factory,
    initialize_database,
    seed_admin,
    seed_indicators,
    seed_reference_data,
)

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///data/teaching_demo.db"
DEFAULT_SECRET_KEY = "dev-secret-key-change-me"
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
logger = logging.getLogger(__name__)


def create_app(
    database_url: str | None = None,
    secret_key: str | None = None,
    ai_report_generator=None,
    chat_provider=None,
    rewriter=None,
    wecom_config: WecomConfig | None = None,
    wecom_client_factory=None,
    image_store=None,
    wecom_customer_config: WecomCustomerConfig | None = None,
    wecom_customer_client=None,
    callback_crypto=None,
) -> FastAPI:
    if database_url is None:
        database_url = DEFAULT_DATABASE_URL

    gateway = None
    customer_client = wecom_customer_client

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        gateway_started = False
        try:
            if gateway is not None:
                await gateway.start()
                gateway_started = True
            yield
        finally:
            try:
                if gateway is not None and gateway_started:
                    await gateway.stop()
            finally:
                if customer_client is not None:
                    close = getattr(customer_client, "close", None)
                    if close is not None:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result

    application = FastAPI(title="教学反馈数据采集 Demo", lifespan=lifespan)
    application.state.secret_key = secret_key or DEFAULT_SECRET_KEY

    application.add_middleware(
        SessionMiddleware,
        secret_key=secret_key or DEFAULT_SECRET_KEY,
        session_cookie="teaching_session",
    )

    engine = build_engine(database_url)
    initialize_database(engine)
    seed_indicators(engine)
    seed_reference_data(engine)
    seed_admin(engine)
    application.state.engine = engine
    application.state.session_factory = build_session_factory(engine)
    with application.state.session_factory() as db:
        sync_head_teacher_assignments(db)
        # Resume failed notification delivery after a process restart. The
        # external API may have accepted the send immediately before a crash,
        # so this is intentionally at-least-once and may produce a duplicate.
        db.execute(update(FamilyConversation).where(
            FamilyConversation.teacher_notification_status == "sending"
        ).values(
            teacher_notification_status="failed",
            teacher_notification_error="RecoveredAfterRestart",
        ))
        db.commit()
    application.state.ai_report_generator = (
        ai_report_generator
        if ai_report_generator is not None
        else build_ai_generator_from_env()
    )
    application.state.chat_provider = (
        chat_provider if chat_provider is not None else build_chat_provider_from_env()
    )
    application.state.rewriter = (
        rewriter if rewriter is not None else build_rewriter_from_env()
    )
    application.state.generation_coordinator = GenerationCoordinator()
    resolved_wecom_config = wecom_config or WecomConfig(False, None, None, None)
    if image_store is None:
        image_store = LocalImageStore(
            resolved_wecom_config.media_root,
            max_bytes=resolved_wecom_config.media_max_bytes,
        )
    if resolved_wecom_config.enabled:
        gateway = WecomGateway(
            config=resolved_wecom_config,
            session_factory=application.state.session_factory,
            chat_provider=application.state.chat_provider,
            binding_secret=application.state.secret_key,
            client_factory=wecom_client_factory,
            generation_coordinator=application.state.generation_coordinator,
            image_store=image_store,
        )
    application.state.wecom_gateway = gateway
    application.state.wecom_config = resolved_wecom_config
    customer_config = wecom_customer_config or load_customer_config(os.environ)
    if customer_config.enabled and customer_client is None:
        customer_client = WecomCustomerClient(customer_config)
    if customer_config.enabled and callback_crypto is None:
        callback_crypto = WecomCallbackCrypto(
            customer_config.corp_id, customer_config.callback_token,
            customer_config.callback_aes_key,
        )
    application.state.image_store = image_store
    application.state.wecom_customer_config = customer_config
    application.state.wecom_customer_client = customer_client
    application.state.callback_crypto = callback_crypto
    application.state.wecom_customer_last_error_at = None

    async def notify_waiting_teacher(conversation_id: str, inbound_message_id: str) -> None:
        active_gateway = application.state.wecom_gateway
        if active_gateway is None:
            return
        with application.state.session_factory() as db:
            inbound = db.get(FamilyMessage, inbound_message_id)
            conversation = db.get(FamilyConversation, conversation_id)
            if (
                inbound is None or inbound.direction != "inbound"
                or inbound.sender_type != "guardian"
                or conversation is None or inbound.conversation_id != conversation_id
                or conversation.status != "waiting_teacher"
                or conversation.teacher_notification_status not in ("pending", "failed")
                or not family_conversations.outbound_for_inbound(db, inbound_message_id)
            ):
                return
            conversation.teacher_notification_status = "sending"
            conversation.teacher_notification_error = None
            db.commit()
        try:
            delivered = await active_gateway.notify_teacher(conversation_id)
        except Exception as exc:  # noqa: BLE001 - never log family text or credentials
            logger.warning("WeCom family teacher notification failed type=%s", type(exc).__name__)
            delivered = False
            error = type(exc).__name__
        else:
            error = None if delivered else "DeliveryUnavailable"
            if not delivered:
                logger.warning("WeCom family teacher notification was not delivered")
        with application.state.session_factory() as db:
            conversation = db.get(FamilyConversation, conversation_id)
            if conversation is not None and conversation.teacher_notification_status == "sending":
                conversation.teacher_notification_status = "sent" if delivered else "failed"
                conversation.teacher_notification_error = None if delivered else error
                db.commit()

    async def call_customer_method(method, *args):
        """Run blocking client methods in a worker, awaiting async variants safely."""
        if inspect.iscoroutinefunction(method):
            return await method(*args)
        result = await asyncio.to_thread(method, *args)
        if inspect.isawaitable(result):
            return await result
        return result

    def process_customer_message(message: dict) -> tuple[str | None, str | None, bool]:
        if message.get("msgtype") != "text":
            return None, None, False
        external = message.get("external_userid") or message.get("external_user_id") or ""
        message_id = message.get("msgid") or ""
        text = (message.get("text") or {}).get("content", "")
        if not external or not message_id:
            return None, None, False
        with application.state.session_factory() as db:
            process_parent_text(
                db, customer_client, secret_key=application.state.secret_key,
                external_user_id=external, message_id=message_id, text=text,
                image_store=application.state.image_store,
                assistant_provider=application.state.chat_provider,
            )
            inbound = db.scalar(select(FamilyMessage).where(
                FamilyMessage.channel_message_id == message_id,
                FamilyMessage.direction == "inbound",
                FamilyMessage.sender_type == "guardian",
            ))
            conversation_id = inbound.conversation_id if inbound else None
            inbound_message_id = inbound.message_id if inbound else None
            failed_delivery = bool(inbound and any(
                row.status == "failed"
                for row in family_conversations.outbound_for_inbound(db, inbound.message_id)
            ))
        return conversation_id, inbound_message_id, failed_delivery

    async def process_customer_token(token: str) -> None:
        if customer_client is None:
            raise RuntimeError("微信客服客户端未配置")
        try:
            batch = await call_customer_method(customer_client.sync_messages, None, token)
            while True:
                for message in batch.messages:
                    conversation_id, inbound_message_id, failed_delivery = await asyncio.to_thread(
                        process_customer_message, message
                    )
                    if failed_delivery:
                        application.state.wecom_customer_last_error_at = datetime.now(timezone.utc).isoformat()
                    if conversation_id and inbound_message_id:
                        await notify_waiting_teacher(conversation_id, inbound_message_id)
                if not batch.has_more:
                    break
                batch = await call_customer_method(
                    customer_client.sync_messages, batch.next_cursor, token
                )
        except Exception as exc:  # noqa: BLE001 - callback responses are sanitized
            application.state.wecom_customer_last_error_at = datetime.now(timezone.utc).isoformat()
            logger.warning("WeCom customer sync failed type=%s", type(exc).__name__)
            raise

    application.state.process_wecom_customer_token = process_customer_token
    application.include_router(create_customer_router(
        customer_config, callback_crypto or object(), process_customer_token,
    ))

    application.include_router(auth_router)
    application.include_router(catalog_router)
    application.include_router(sessions_router)
    application.include_router(feedback_router)
    application.include_router(history_router)
    application.include_router(profiles_router)
    application.include_router(family_router)
    application.include_router(reports_router)
    application.include_router(chat_router)
    application.include_router(export_router)
    application.include_router(wecom_router)

    static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    @application.get("/health/wecom")
    def wecom_health():
        active_gateway = application.state.wecom_gateway
        return {
            "enabled": application.state.wecom_config.enabled,
            "connected": bool(active_gateway and active_gateway.connected),
            "authenticated": bool(active_gateway and active_gateway.authenticated),
            "last_error_at": active_gateway.last_error_at if active_gateway else None,
        }

    @application.get("/health/wecom-customer")
    def wecom_customer_health():
        enabled = application.state.wecom_customer_config.enabled
        return {
            "enabled": enabled,
            "ready": bool(enabled and application.state.wecom_customer_client is not None
                           and application.state.callback_crypto is not None),
            "last_error_at": application.state.wecom_customer_last_error_at,
        }

    return application


def create_default_app(
    database_url: str | None = None,
    env_file: str | Path = DEFAULT_ENV_FILE,
) -> FastAPI:
    """Create the default runtime app with model settings from ``.env``."""
    config = load_model_config_from_dotenv(env_file)
    from dotenv import dotenv_values
    env_values = {key: value for key, value in dotenv_values(env_file).items() if value is not None}
    env_values.update(os.environ)
    kwargs = {
        "database_url": database_url,
        "wecom_config": load_wecom_config_from_dotenv(env_file),
        "wecom_customer_config": load_customer_config(env_values),
    }
    if config is not None:
        kwargs.update(
            ai_report_generator=AIReportGenerator(
                build_openai_invoke(config.base_url, config.api_key, config.model)
            ),
            chat_provider=ChatProvider(config),
            rewriter=build_rewriter(config),
        )
    return create_app(**kwargs)


app = create_default_app()
