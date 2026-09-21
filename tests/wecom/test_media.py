import asyncio

import pytest

from app.catalog.models import Student, Teacher
from app.core.database import build_session_factory
from app.family.models import StudentTeacherAssignment, StudentImage, PendingMediaAssignment
from app.wecom.config import WecomConfig, load_wecom_config
from app.wecom.gateway import WecomGateway
from app.wecom.media_handler import parse_media_message
from app.wecom.models import TeacherWecomBinding


IMAGE = b"\xff\xd8\xffteacher-image"


class FakeClient:
    def __init__(self):
        self.handlers, self.stream_replies, self.cards, self.files, self.messages = {}, [], [], [], []

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
        config=WecomConfig(True, "bot", "secret", None, media_root=tmp_path / "media"),
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
    assert db_session.query(PendingMediaAssignment).filter_by(source_message_id="M2").one()


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
