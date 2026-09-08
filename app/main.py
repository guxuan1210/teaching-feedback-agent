from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.routes import router as auth_router
from app.catalog.routes import router as catalog_router
from app.export.routes import router as export_router
from app.feedback.routes import router as feedback_router
from app.history.routes import router as history_router
from app.profiles.routes import router as profiles_router
from app.reports.providers import build_ai_generator_from_env
from app.sessions.routes import router as sessions_router
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


def create_app(
    database_url: str | None = None,
    secret_key: str | None = None,
    ai_report_generator=None,
) -> FastAPI:
    if database_url is None:
        database_url = DEFAULT_DATABASE_URL

    application = FastAPI(title="教学反馈数据采集 Demo")

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

    application.include_router(auth_router)
    application.include_router(catalog_router)
    application.include_router(sessions_router)
    application.include_router(feedback_router)
    application.include_router(history_router)
    application.include_router(profiles_router)
    application.include_router(export_router)

    static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    return application


app = create_app()
