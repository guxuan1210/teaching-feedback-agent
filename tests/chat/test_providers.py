"""Unified config loading and streaming client parsing."""

from __future__ import annotations

import httpx

from app import main as app_main
from app.chat.providers import ChatProvider, ModelConfig, load_model_config


def test_load_model_config_prefers_llm(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("REPORT_AI_BASE_URL", "https://report.example")
    monkeypatch.setenv("REPORT_AI_API_KEY", "report-key")
    monkeypatch.setenv("REPORT_AI_MODEL", "report-model")

    config = load_model_config()
    assert config == ModelConfig("https://llm.example", "llm-key", "llm-model")


def test_load_model_config_falls_back_to_report(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("REPORT_AI_BASE_URL", "https://report.example")
    monkeypatch.setenv("REPORT_AI_API_KEY", "report-key")
    monkeypatch.setenv("REPORT_AI_MODEL", "report-model")

    config = load_model_config()
    assert config == ModelConfig("https://report.example", "report-key", "report-model")


def test_load_model_config_partial_is_unconfigured(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("REPORT_AI_BASE_URL", raising=False)
    monkeypatch.delenv("REPORT_AI_API_KEY", raising=False)
    monkeypatch.delenv("REPORT_AI_MODEL", raising=False)

    assert load_model_config() is None


def test_default_app_loads_model_from_dotenv(tmp_path, monkeypatch):
    for name in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "REPORT_AI_BASE_URL",
        "REPORT_AI_API_KEY",
        "REPORT_AI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_BASE_URL=https://llm.example\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=test-model\n",
        encoding="utf-8",
    )

    factory = getattr(app_main, "create_default_app", None)
    assert factory is not None, "默认应用工厂必须读取项目 .env"

    application = factory(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'app.db').as_posix()}",
        env_file=env_file,
    )
    assert application.state.chat_provider is not None
    assert application.state.chat_provider.model == "test-model"


def test_stream_yields_deltas_and_stops_at_done():
    def handler(request):
        body = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[{"delta":{}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    provider = ChatProvider(
        ModelConfig("https://llm.example", "key", "m"),
        transport=httpx.MockTransport(handler),
    )
    chunks = list(provider.stream([{"role": "user", "content": "hi"}]))
    assert chunks == ["你", "好"]
