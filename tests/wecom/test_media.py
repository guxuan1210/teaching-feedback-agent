import asyncio
from dataclasses import replace

import pytest

from app.catalog.models import Student, Teacher
from app.core.database import build_session_factory
from datetime import datetime
from app.family.models import StudentTeacherAssignment, StudentImage, PendingMediaAssignment
from app.wecom.config import WecomConfig, load_wecom_config
from app.wecom.gateway import WecomGateway
from app.wecom.media_handler import parse_media_message
from app.wecom.media_handler import download_and_decrypt_images, InvalidMediaMessage
from app.wecom.models import TeacherWecomBinding


IMAGE = b"\xff\xd8\xffteacher-image"


class FakeClient:
    def __init__(self):
        self.handlers, self.stream_replies, self.cards, self.files, self.messages = {}, [], [], [], []
        self.download_payload = IMAGE

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler
        return register

    async def connect(self):
        pass

    async def reply_stream(self, frame, stream_id, content, finish):
        self.stream_replies.append(content)

    async def reply_template_card(self, frame, card):
        self.cards.append(card)

    async def update_template_card(self, *args, **kwargs):
        pass

    async def send_message(self, chatid, body):
        self.messages.append(body["markdown"]["content"])

    async def download_file(self, url, aes_key=None):
        self.files.append((url, aes_key))
        return IMAGE, "photo.jpg"

    async def download_file_limited(self, url, aes_key, max_bytes):
        self.files.append((url, aes_key))
        payload = self.download_payload
        if len(payload) > max_bytes:
            raise InvalidMediaMessage("media exceeds size limit")
        return payload

    def disconnect(self):
        pass


def test_mixed_parser_combines_text_and_preserves_image_order():
    parsed = parse_media_message({
        "msgid": "M-PARSE",
        "msgtype": "mixed",
        "mixed": {"msg_item": [
            {"msgtype": "text", "text": {"content": "S1 作业"}},
            {"msgtype": "image", "image": {"url": "u1", "aeskey": "a1"}},
            {"msgtype": "text", "text": {"content": "订正"}},
            {"msgtype": "image", "image": {"url": "u2", "aeskey": "a2"}},
        ]},
    })
    assert parsed.caption == "S1 作业\n订正"
    assert [image.url for image in parsed.images] == ["u1", "u2"]


def test_load_wecom_config_reads_media_storage_options(tmp_path):
    config = load_wecom_config({
        "STUDENT_MEDIA_ROOT": str(tmp_path / "images"),
        "STUDENT_MEDIA_MAX_BYTES": "2048",
        "PENDING_MEDIA_MINUTES": "9",
    })
    assert config.media_root == tmp_path / "images"
    assert config.media_max_bytes == 2048
    assert config.pending_media_minutes == 9


@pytest.fixture
def media_gateway(engine, tmp_path):
    teacher = Teacher(teacher_id="T-M", name="王老师", role="晚辅教师", status="active")
    student = Student(student_id="S-M", name="测试学生", grade="三年级", status="active")
    with build_session_factory(engine)() as db:
        db.add_all([teacher, student])
        db.commit()
        db.add(StudentTeacherAssignment(
            student_id="S-M", teacher_id="T-M", role="primary",
            start_date="2020-01-01", status="active", origin="manual",
        ))
        db.add(TeacherWecomBinding(wecom_user_id="WX-M", teacher_id="T-M"))
        db.commit()
    fake = FakeClient()
    gateway = WecomGateway(
        config=WecomConfig(True, "bot", "secret", None, media_root=tmp_path / "media", pending_media_minutes=9),
        session_factory=build_session_factory(engine), chat_provider=None,
        binding_secret="secret", client_factory=lambda _: fake,
    )
    return gateway, fake


def test_mixed_message_archives_image_and_replies_with_student(media_gateway, db_session):
    gateway, fake = media_gateway

    async def scenario():
        await gateway.start()
        await fake.handlers["message.mixed"]({
            "headers": {"req_id": "r1"},
            "body": {"msgid": "M1", "chattype": "single", "from": {"userid": "WX-M"},
                "msgtype": "mixed", "mixed": {"msg_item": [
                    {"msgtype": "text", "text": {"content": "S-M 作业订正"}},
                    {"msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
                ]}},
        })

    asyncio.run(scenario())
    image = db_session.query(StudentImage).filter_by(source_message_id="M1").one()
    assert image.student_id == "S-M"
    assert image.caption == "作业订正"
    assert fake.stream_replies[-1].endswith("已归档到测试学生（S-M），共 1 张。")
    assert fake.files == [("https://media", "key")]


def test_image_without_student_sends_choice_card(media_gateway, db_session):
    gateway, fake = media_gateway

    async def scenario():
        await gateway.start()
        await fake.handlers["message.image"]({
            "headers": {"req_id": "r2"},
            "body": {"msgid": "M2", "chattype": "single", "from": {"userid": "WX-M"},
                "msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
        })

    asyncio.run(scenario())
    card = fake.cards[-1]
    assert card["main_title"]["title"] == "请选择图片所属学生"
    button = card["button_list"][0]
    assert button["key"].startswith("media_")
    pending = db_session.query(PendingMediaAssignment).filter_by(source_message_id="M2").one()
    created = datetime.fromisoformat(pending.created_at)
    expires = datetime.fromisoformat(pending.expires_at)
    assert abs((expires - created).total_seconds() - 9 * 60) < 3


def test_media_choice_click_archives_pending_image(media_gateway, db_session):
    gateway, fake = media_gateway

    async def scenario():
        await gateway.start()
        await fake.handlers["message.image"]({
            "headers": {"req_id": "r3"},
            "body": {"msgid": "M3", "chattype": "single", "from": {"userid": "WX-M"},
                "msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
        })
        event_key = fake.cards[-1]["button_list"][0]["key"]
        await fake.handlers["event.template_card_event"]({
            "body": {"msgid": "CLICK", "from": {"userid": "WX-M"},
                "event": {"template_card_event": {"event_key": event_key}}},
        })

    asyncio.run(scenario())
    assert db_session.query(StudentImage).filter_by(source_message_id="M3").count() == 1
    assert any("已归档到测试学生" in reply for reply in fake.messages)


def test_unbound_sender_is_rejected_before_media_download(media_gateway):
    gateway, fake = media_gateway

    async def scenario():
        await gateway.start()
        await fake.handlers["message.image"]({
            "body": {"msgid": "M4", "chattype": "single", "from": {"userid": "UNKNOWN"},
                "msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
        })

    asyncio.run(scenario())
    assert fake.files == []
    assert "绑定老师账号" in fake.stream_replies[-1]


@pytest.mark.parametrize("chattype", [None, "group", "invalid"])
def test_media_requires_explicit_private_chat(media_gateway, chattype):
    gateway, fake = media_gateway

    async def scenario():
        await gateway.start()
        body = {"msgid": "M-CHAT", "from": {"userid": "WX-M"},
                "msgtype": "image", "image": {"url": "https://media"}}
        if chattype is not None:
            body["chattype"] = chattype
        await fake.handlers["message.image"]({"body": body})

    asyncio.run(scenario())
    assert fake.files == []
    assert "仅支持老师私聊" in fake.stream_replies[-1]


def test_download_stops_before_aggregate_limit_and_does_not_return_partial_payloads():
    parsed = parse_media_message({"msgid": "M-LIMIT", "msgtype": "mixed", "mixed": {"msg_item": [
        {"msgtype": "image", "image": {"url": "u1"}},
        {"msgtype": "image", "image": {"url": "u2"}},
    ]}})
    class BoundedClient:
        def __init__(self): self.calls = []
        async def download_file_limited(self, url, aes_key, max_bytes):
            self.calls.append((url, max_bytes))
            if url == "u2":
                raise InvalidMediaMessage("media exceeds size limit")
            return b"1234"
    client = BoundedClient()
    async def run():
        with pytest.raises(InvalidMediaMessage):
            await download_and_decrypt_images(client, parsed, max_bytes=6, downloader=client.download_file_limited)
    asyncio.run(run())
    assert client.calls == [("u1", 6), ("u2", 2)]


def test_download_rejects_too_many_images_before_fetching():
    parsed = parse_media_message({"msgid": "M-COUNT", "msgtype": "mixed", "mixed": {"msg_item": [
        *[{"msgtype": "image", "image": {"url": f"u{i}"}} for i in range(7)]
    ]}})
    client = FakeClient()
    async def run():
        with pytest.raises(InvalidMediaMessage, match="too many"):
            await download_and_decrypt_images(client, parsed, max_bytes=100, max_images=6)
    asyncio.run(run())
    assert client.files == []


def test_oversized_download_is_never_archived(media_gateway, db_session):
    gateway, fake = media_gateway
    fake.download_payload = b"x" * 32
    gateway.config = replace(gateway.config, media_max_bytes=16)

    async def scenario():
        await gateway.start()
        await fake.handlers["message.image"]({"body": {
            "msgid": "M-OVERSIZE", "chattype": "single", "from": {"userid": "WX-M"},
            "msgtype": "image", "image": {"url": "https://media"},
        }})

    asyncio.run(scenario())
    assert db_session.query(StudentImage).filter_by(source_message_id="M-OVERSIZE").count() == 0
    assert not list((gateway.image_store.root).rglob("*.jpg"))


def test_streaming_download_stops_on_ciphertext_cap(monkeypatch):
    import app.wecom.media_handler as media_handler

    class Content:
        def __init__(self): self.yielded = 0
        async def iter_chunked(self, _size):
            for chunk in (b"1234", b"5678", b"later"):
                self.yielded += len(chunk)
                yield chunk
    content = Content()
    class Response:
        headers = {}
        def __init__(self): self.content = content
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        def raise_for_status(self): pass
    class Session:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        def get(self, _url): return Response()
    monkeypatch.setattr(media_handler.aiohttp, "ClientSession", Session)
    async def scenario():
        with pytest.raises(InvalidMediaMessage, match="size limit"):
            await media_handler._download_limited(object(), "https://media", None, 5)
    asyncio.run(scenario())
    assert content.yielded == 8  # Does not read the remaining response after crossing the cap.
