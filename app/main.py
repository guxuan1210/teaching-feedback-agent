from fastapi import FastAPI


def create_app(database_url: str | None = None) -> FastAPI:
    application = FastAPI(title="教学反馈数据采集 Demo")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    return application


app = create_app()
