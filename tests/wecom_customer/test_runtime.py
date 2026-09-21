import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.catalog.models import Student
from app.family.models import FamilyConversation, FamilyMessage, Guardian, GuardianChannelBinding, StudentGuardian
from app.wecom_customer.config import WecomCustomerConfig
from app.wecom_customer.client import SyncResult


DISABLED = WecomCustomerConfig(False, None, None, None, None, None)
ENABLED = WecomCustomerConfig(True, "corp", "secret", "kf", "token", "a" * 43)


class FakeCrypto:
    def verify_url(self, *args):
        return "verified"

    def decrypt_message(self, *args):
        return "<xml><Token>sync-1</Token></xml>"


class FakeCustomer:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.messages = []

    def sync_messages(self, cursor, token):
        return SyncResult(None, self.messages)

    def send_text(self, external_user_id, content):
        self.sent.append((external_user_id, content))
        return f"OUT-{len(self.sent)}"

    def close(self):
        self.closed = True


def test_customer_health_exposes_only_safe_state(database_url):
    app = create_app(database_url=database_url, wecom_customer_config=DISABLED)
    with TestClient(app) as client:
        response = client.get("/health/wecom-customer")
    assert response.json() == {"enabled": False, "ready": False, "last_error_at": None}


def test_disabled_customer_callback_is_not_found(database_url):
    app = create_app(database_url=database_url, wecom_customer_config=DISABLED)
    with TestClient(app) as client:
        response = client.get(
            "/wecom/customer/callback?msg_signature=s&timestamp=1&nonce=n&echostr=e"
        )
    assert response.status_code == 404


def test_enabled_customer_dependencies_are_injected_and_client_closes(database_url):
    customer = FakeCustomer()
    app = create_app(
        database_url=database_url,
        wecom_customer_config=ENABLED,
        wecom_customer_client=customer,
        callback_crypto=FakeCrypto(),
    )
    assert app.state.wecom_customer_client is customer
    assert app.state.callback_crypto.__class__ is FakeCrypto
    assert app.state.image_store is not None
    with TestClient(app) as client:
        payload = client.get("/health/wecom-customer").json()
        assert payload == {"enabled": True, "ready": True, "last_error_at": None}
    assert customer.closed


def test_handoff_callback_notifies_teacher_once_and_persists_sent_state(database_url):
    """A delivered handoff notification must not repeat on callback replay."""
    customer = FakeCustomer()
    app = create_app(
        database_url=database_url,
        wecom_customer_config=ENABLED,
        wecom_customer_client=customer,
        callback_crypto=FakeCrypto(),
    )
    factory = app.state.session_factory
    with factory() as db:
        db.add(Student(student_id="S-RUNTIME", name="运行时学生", grade="三年级",
                       current_stage="三阶", status="active"))
        db.flush()
        guardian = Guardian(guardian_id="G-RUNTIME", name="测试家长",
                            relationship_type="mother", status="active")
        db.add(guardian)
        db.flush()
        db.add(StudentGuardian(student_id="S-RUNTIME", guardian_id=guardian.guardian_id,
                               status="active"))
        db.add(GuardianChannelBinding(
            channel="wecom_customer", external_user_id="EXT-RUNTIME",
            guardian_id=guardian.guardian_id, active_student_id="S-RUNTIME", status="active",
        ))
        db.commit()
    customer.messages = [{
        "msgtype": "text", "external_userid": "EXT-RUNTIME", "msgid": "Q-RUNTIME",
        "text": {"content": "请老师回复"},
    }]
    calls = []

    class Gateway:
        async def notify_teacher(self, conversation_id):
            calls.append(conversation_id)
            return True

    app.state.wecom_gateway = Gateway()
    asyncio.run(app.state.process_wecom_customer_token("sync-handoff"))
    asyncio.run(app.state.process_wecom_customer_token("sync-replay"))
    assert len(calls) == 1
    with factory() as db:
        conversation = db.scalar(select(FamilyConversation).where(
            FamilyConversation.channel_conversation_id == "EXT-RUNTIME"
        ))
        inbound = db.scalar(select(FamilyMessage).where(
            FamilyMessage.channel_message_id == "Q-RUNTIME"
        ))
        assert conversation.status == "waiting_teacher"
        assert conversation.teacher_notification_status == "sent"
        assert inbound is not None

    # A process crash after claiming delivery is recovered for retry on restart.
    with factory() as db:
        conversation = db.scalar(select(FamilyConversation).where(
            FamilyConversation.channel_conversation_id == "EXT-RUNTIME"
        ))
        conversation.teacher_notification_status = "sending"
        db.commit()
    restarted = create_app(
        database_url=database_url, wecom_customer_config=ENABLED,
        wecom_customer_client=customer, callback_crypto=FakeCrypto(),
    )
    restarted.state.wecom_gateway = Gateway()
    with restarted.state.session_factory() as db:
        conversation = db.scalar(select(FamilyConversation).where(
            FamilyConversation.channel_conversation_id == "EXT-RUNTIME"
        ))
        assert conversation.teacher_notification_status == "failed"
    asyncio.run(restarted.state.process_wecom_customer_token("sync-after-restart"))
    assert len(calls) == 2

    # Completing a prior handoff and then requesting another creates a fresh
    # notification cycle, rather than remaining deduplicated forever.
    with restarted.state.session_factory() as db:
        conversation = db.scalar(select(FamilyConversation).where(
            FamilyConversation.channel_conversation_id == "EXT-RUNTIME"
        ))
        conversation.status = "active"
        db.commit()
    customer.messages = [{
        "msgtype": "text", "external_userid": "EXT-RUNTIME", "msgid": "Q-RUNTIME-2",
        "text": {"content": "请老师回复"},
    }]
    asyncio.run(restarted.state.process_wecom_customer_token("sync-new-handoff"))
    assert len(calls) == 3
