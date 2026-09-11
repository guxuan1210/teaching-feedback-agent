import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.wecom.config import WecomConfig, load_wecom_config, load_wecom_config_from_dotenv
from app.wecom.gateway import SafeWecomLogger, WecomGateway


class FakeClient:
    def __init__(self):
        self.handlers = {}
        self.replies = []
        self.disconnected = False

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler
        return register

    async def connect(self):
        self.handlers["connected"]()
        self.handlers["authenticated"]()

    def disconnect(self):
        self.disconnected = True
        self.handlers["disconnected"]("closed")

    async def reply_stream(self, frame, stream_id, content, finish):
        self.replies.append((content, finish))

    async def reply_template_card(self, frame, card):
        self.replies.append((card, True))

    async def update_template_card(self, frame, card, userids=None):
        self.replies.append((card, True))

    async def send_message(self, chatid, body):
        self.replies.append((body["markdown"]["content"], True))


def test_load_wecom_config_defaults_to_disabled():
    config = load_wecom_config({})
    assert config.enabled is False
    assert config.bot_id is None


def test_enabled_config_requires_bot_credentials():
    with pytest.raises(ValueError, match="WECOM_BOT_ID"):
        load_wecom_config({"WECOM_BOT_ENABLED": "true"})


def test_explicit_process_disable_overrides_enabled_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WECOM_BOT_ENABLED=true\nWECOM_BOT_ID=bot\nWECOM_BOT_SECRET=secret\n",
        encoding="utf-8",
    )

    config = load_wecom_config_from_dotenv(
        env_file, environ={"WECOM_BOT_ENABLED": "false"}
    )

    assert config.enabled is False


def test_sdk_logger_does_not_emit_message_content(caplog):
    sdk_logger = SafeWecomLogger()
    secret_content = "张三最近数学退步"
    sdk_logger.debug(secret_content)
    sdk_logger.info(secret_content)
    sdk_logger.warn(secret_content)
    sdk_logger.error(secret_content)

    assert secret_content not in caplog.text


def test_gateway_connects_handles_message_and_disconnects(engine):
    from app.core.database import build_session_factory

    fake = FakeClient()
    gateway = WecomGateway(
        config=WecomConfig(True, "bot", "secret", None),
        session_factory=build_session_factory(engine),
        chat_provider=None,
        binding_secret="app-secret",
        client_factory=lambda _config: fake,
    )

    async def scenario():
        await gateway.start()
        await fake.handlers["message.text"](
            {
                "headers": {"req_id": "r1"},
                "body": {
                    "msgid": "m1", "chattype": "single",
                    "from": {"userid": "u1"},
                    "text": {"content": "你好"},
                },
            }
        )
        await gateway.stop()

    asyncio.run(scenario())

    assert fake.replies[-1][0].startswith("请先登录 Teaching Agent")
    assert fake.replies[-1][1] is True
    assert gateway.connected is False
    assert fake.disconnected is True


def test_health_endpoint_exposes_no_credentials(database_url):
    application = create_app(database_url=database_url)
    with TestClient(application) as client:
        response = client.get("/health/wecom")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "connected": False,
        "authenticated": False,
        "last_error_at": None,
    }
    assert "secret" not in response.text.lower()


def test_application_lifespan_starts_and_stops_enabled_gateway(database_url):
    fake = FakeClient()
    application = create_app(
        database_url=database_url,
        wecom_config=WecomConfig(True, "bot", "secret", None),
        wecom_client_factory=lambda _config: fake,
    )

    with TestClient(application) as client:
        status = client.get("/health/wecom").json()
        assert status["connected"] is True
        assert status["authenticated"] is True

    assert fake.disconnected is True


def test_gateway_handles_expired_scope_card_click(engine):
    from app.core.database import build_session_factory

    fake = FakeClient()
    gateway = WecomGateway(
        config=WecomConfig(True, "bot", "secret", None),
        session_factory=build_session_factory(engine),
        chat_provider=None,
        binding_secret="app-secret",
        client_factory=lambda _config: fake,
    )

    async def scenario():
        await gateway.start()
        await fake.handlers["event.template_card_event"](
            {
                "headers": {"req_id": "r2"},
                "body": {
                    "msgid": "choice-message", "chattype": "single",
                    "from": {"userid": "u1"},
                    "event": {
                        "eventtype": "template_card_event",
                        "template_card_event": {
                            "event_key": "scope_0", "task_id": "scope_m1"
                        },
                    },
                },
            }
        )
        await gateway.stop()

    asyncio.run(scenario())

    assert any("选择已收到" in str(reply[0]) for reply in fake.replies)
    assert fake.replies[-1][0] == "该选择已失效，请重新提问。"
