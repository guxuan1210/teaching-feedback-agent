from contextlib import asynccontextmanager
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


def create_app(
    database_url: str | None = None,
    secret_key: str | None = None,
    ai_report_generator=None,
    chat_provider=None,
    rewriter=None,
    wecom_config: WecomConfig | None = None,
    wecom_client_factory=None,
) -> FastAPI:
    if database_url is None:
        database_url = DEFAULT_DATABASE_URL

    gateway = None

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        if gateway is not None:
            await gateway.start()
        try:
            yield
        finally:
            if gateway is not None:
                await gateway.stop()

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
    if resolved_wecom_config.enabled:
        gateway = WecomGateway(
            config=resolved_wecom_config,
            session_factory=application.state.session_factory,
            chat_provider=application.state.chat_provider,
            binding_secret=application.state.secret_key,
            client_factory=wecom_client_factory,
            generation_coordinator=application.state.generation_coordinator,
        )
    application.state.wecom_gateway = gateway
    application.state.wecom_config = resolved_wecom_config

    application.include_router(auth_router)
    application.include_router(catalog_router)
    application.include_router(sessions_router)
    application.include_router(feedback_router)
    application.include_router(history_router)
    application.include_router(profiles_router)
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

    return application


def create_default_app(
    database_url: str | None = None,
    env_file: str | Path = DEFAULT_ENV_FILE,
) -> FastAPI:
    """Create the default runtime app with model settings from ``.env``."""
    config = load_model_config_from_dotenv(env_file)
    kwargs = {
        "database_url": database_url,
        "wecom_config": load_wecom_config_from_dotenv(env_file),
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
