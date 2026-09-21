"""End-to-end family media and communication acceptance through both adapters."""

import asyncio
import re
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.catalog.models import Class, Enrollment, Student, Teacher
from app.core.security import hash_password
from app.family.models import (
    FamilyConversation,
    FamilyMessage,
    GuardianChannelBinding,
    StudentImage,
    StudentTeacherAssignment,
)
from app.family.relationships import assign_teacher, revoke_teacher_assignment
from app.wecom.config import WecomConfig
from app.wecom.models import TeacherWecomBinding
from app.wecom_customer.client import SyncResult
from app.wecom_customer.config import WecomCustomerConfig
from app.main import create_app


PNG = b"\x89PNG\r\n\x1a\n" + b"acceptance-image-payload"
CUSTOMER_CONFIG = WecomCustomerConfig(
    True, "corp", "corp-secret", "open-kfid", "callback-token", "a" * 43
)


class FakeBot:
    def __init__(self):
        self.handlers = {}
        self.replies = []
        self.downloaded = []
        self.disconnected = False

    def on(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler
        return register

    async def connect(self):
        self.handlers["connected"]()
        self.handlers["authenticated"]()

    def disconnect(self):
        self.disconnected = True
        self.handlers["disconnected"]("closed")

    async def reply_stream(self, _frame, _stream_id, content, finish):
        self.replies.append((content, finish))

    async def reply_template_card(self, _frame, card):
        self.replies.append((card, True))

    async def update_template_card(self, _frame, card, userids=None):
        self.replies.append((card, True))

    async def send_message(self, chatid, body):
        self.replies.append((chatid, body))

    async def download_file_limited(self, url, key, limit):
        self.downloaded.append((url, key, limit))
        return PNG


class FakeCustomer:
    def __init__(self):
        self.messages = []
        self.sent_text = []
        self.sent_images = []
        self.uploads = []

    def sync_messages(self, cursor, token):
        return SyncResult(cursor, list(self.messages))

    def send_text(self, external_user_id, content):
        self.sent_text.append((external_user_id, content))
        return f"OUT-TEXT-{len(self.sent_text)}"

    def upload_image(self, content, filename):
        self.uploads.append((content, filename))
        return f"MEDIA-{len(self.uploads)}"

    def send_image(self, external_user_id, media_id):
        self.sent_images.append((external_user_id, media_id))
        return f"OUT-IMAGE-{len(self.sent_images)}"

    def close(self):
        pass


class FakeCrypto:
    def verify_url(self, *args):
        return "verified"

    def decrypt_message(self, body, *_args):
        return body.decode("utf-8")


class FamilyCommunicationHarness:
    """Small local-only adapter harness; production services remain real."""

    def __init__(self, database_url, media_root):
        self.bot = FakeBot()
        self.customer = FakeCustomer()
        self.app = create_app(
            database_url=database_url,
            secret_key="acceptance-test-secret",
            wecom_config=WecomConfig(
                True, "fake-bot", "fake-secret", None, media_root, 1024 * 1024, 15
            ),
            wecom_client_factory=lambda _config: self.bot,
            wecom_customer_config=CUSTOMER_CONFIG,
            wecom_customer_client=self.customer,
            callback_crypto=FakeCrypto(),
        )
        self.factory = self.app.state.session_factory
        with self.factory() as db:
            teacher = Teacher(
                teacher_id="T-E2E", name="王老师", role="班主任",
                password_hash=hash_password("teacher-pass"), status="active",
            )
            student = Student(
                student_id="S-E2E", name="李明", grade="三年级",
                current_stage="三阶", status="active",
            )
            db.add_all([teacher, student])
            db.flush()
            klass = Class(
                class_id="C-E2E", name="三年级A班", grade="三年级",
                class_type="daily", head_teacher_id=teacher.teacher_id, status="active",
            )
            db.add(klass)
            db.flush()
            db.add(Enrollment(
                student_id=student.student_id, class_id=klass.class_id,
                start_date=date.today().isoformat(), status="active",
            ))
            db.commit()
            # The media service evaluates authorization in UTC; start the
            # assignment yesterday so the test is stable around local midnight.
            assign_teacher(db, student.student_id, teacher.teacher_id, "primary", date.today() - timedelta(days=1))

        self.admin = TestClient(self.app)
        self.teacher = TestClient(self.app)
        self.admin.__enter__()
        self.teacher.__enter__()
        admin_login = self.admin.post("/login", data={"name": "管理员", "password": "admin123"}, follow_redirects=False)
        assert admin_login.status_code == 303, admin_login.text
        teacher_login = self.teacher.post("/login", data={"name": "王老师", "password": "teacher-pass"}, follow_redirects=False)
        assert teacher_login.status_code == 303, teacher_login.text

    def close(self):
        self.teacher.__exit__(None, None, None)
        self.admin.__exit__(None, None, None)

    def bind_teacher_and_archive_image(self):
        self.teacher.post("/account/wecom-binding-code", follow_redirects=False)
        page = self.teacher.get("/account")
        code = re.search(r"绑定\s+(\d{6})", page.text).group(1)
        asyncio.run(self.bot.handlers["message.text"]({"body": {
            "msgid": "teacher-bind", "chattype": "single",
            "from": {"userid": "WX-TEACHER"}, "text": {"content": f"绑定 {code}"},
        }}))
        asyncio.run(self.bot.handlers["message.mixed"]({"body": {
            "msgid": "teacher-image-1", "chattype": "single", "msgtype": "mixed",
            "from": {"userid": "WX-TEACHER"},
            "mixed": {"msg_item": [
                {"msgtype": "text", "text": {"content": "S-E2E 今天作业"}},
                {"msgtype": "image", "image": {"url": "https://fake.invalid/image", "aeskey": "fake"}},
            ]},
        }}))

    def create_parent_invite(self):
        self.admin.post("/family/students/S-E2E/invitations", follow_redirects=False)
        page = self.admin.get("/family/students/S-E2E")
        return re.search(r'class="invitation-code">([^<]+)', page.text).group(1)

    def parent_sends(self, external_id, message_id, text):
        self.customer.messages = [{
            "msgtype": "text", "external_userid": external_id,
            "msgid": message_id, "text": {"content": text},
        }]
        token = f"sync-{message_id}"
        response = self.admin.post(
            "/wecom/customer/callback?msg_signature=fake&timestamp=1&nonce=n",
            content=f"<xml><Token>{token}</Token></xml>",
            headers={"content-type": "application/xml"},
        )
        assert response.status_code == 200
        return token


def test_teacher_image_parent_binding_queries_handoff_reply_and_permissions(database_url, tmp_path):
    harness = FamilyCommunicationHarness(database_url, tmp_path / "student_media")
    try:
        harness.bind_teacher_and_archive_image()
        with harness.factory() as db:
            images = list(db.scalars(select(StudentImage).where(StudentImage.student_id == "S-E2E")))
            assert len(images) == 1
            image_id = images[0].image_id
            assert db.get(TeacherWecomBinding, "WX-TEACHER").teacher_id == "T-E2E"

        code = harness.create_parent_invite()
        harness.parent_sends("EXT-PARENT", "parent-invite", code)
        harness.parent_sends("EXT-PARENT", "parent-relation", "母亲")
        harness.parent_sends("EXT-PARENT", "parent-image-query", "看最近的图片")
        assert len(harness.customer.sent_images) == 1
        assert harness.customer.uploads[0][0] == PNG
        assert "上传老师：王老师" in harness.customer.sent_text[-1][1]

        handoff_token = harness.parent_sends(
            "EXT-PARENT", "parent-handoff", "请老师回复：孩子需要带什么？"
        )
        with harness.factory() as db:
            conversation = db.scalar(select(FamilyConversation).where(
                FamilyConversation.channel_conversation_id == "EXT-PARENT"
            ))
            conversation_id = conversation.conversation_id
            assert conversation.teacher_notification_status == "sent"
        notifications = [
            item for item in harness.bot.replies
            if isinstance(item, tuple) and item[0] == "WX-TEACHER"
            and "家长请求老师回复" in item[1]["markdown"]["content"]
        ]
        assert len(notifications) == 1
        escaped_conversation_id = conversation_id.replace("_", r"\_")
        assert escaped_conversation_id in notifications[0][1]["markdown"]["content"]

        # Replaying the exact callback token must not notify the teacher twice.
        replay = harness.admin.post(
            "/wecom/customer/callback?msg_signature=fake&timestamp=1&nonce=n",
            content=f"<xml><Token>{handoff_token}</Token></xml>",
            headers={"content-type": "application/xml"},
        )
        assert replay.status_code == 200
        assert len([
            item for item in harness.bot.replies
            if isinstance(item, tuple) and item[0] == "WX-TEACHER"
            and "家长请求老师回复" in item[1]["markdown"]["content"]
        ]) == 1

        asyncio.run(harness.bot.handlers["message.text"]({"body": {
            "msgid": "teacher-family-reply", "chattype": "single",
            "from": {"userid": "WX-TEACHER"},
            "text": {"content": f"回复 {conversation_id} 请带订正本。"},
        }}))
        assert harness.customer.sent_text[-1] == (
            "EXT-PARENT", "【老师：王老师】请带订正本。"
        )

        # A revoked teacher assignment prevents outbound delivery even while
        # the parent binding and handoff are still active.
        with harness.factory() as db:
            conversation = db.get(FamilyConversation, conversation_id)
            conversation.status = "waiting_teacher"
            assignment = db.scalar(select(StudentTeacherAssignment).where(
                StudentTeacherAssignment.student_id == "S-E2E",
                StudentTeacherAssignment.teacher_id == "T-E2E",
            ))
            revoke_teacher_assignment(db, assignment.assignment_id, date.today() - timedelta(days=1))
            db.refresh(conversation)
        sent_before = len(harness.customer.sent_text)
        asyncio.run(harness.bot.handlers["message.text"]({"body": {
            "msgid": "teacher-reply-after-assignment-revoke", "chattype": "single",
            "from": {"userid": "WX-TEACHER"},
            "text": {"content": f"回复 {conversation_id} 已撤销任课后的回复。"},
        }}))
        assert len(harness.customer.sent_text) == sent_before

        # Re-enable the assignment only to isolate the separate guardian
        # binding check, then revoke that binding before another teacher send.
        with harness.factory() as db:
            assignment = db.scalar(select(StudentTeacherAssignment).where(
                StudentTeacherAssignment.student_id == "S-E2E",
                StudentTeacherAssignment.teacher_id == "T-E2E",
            ))
            assignment.status = "active"
            assignment.end_date = None
            binding = db.scalar(select(GuardianChannelBinding).where(
                GuardianChannelBinding.external_user_id == "EXT-PARENT"
            ))
            binding.status = "revoked"
            conversation = db.get(FamilyConversation, conversation_id)
            conversation.status = "waiting_teacher"
            db.commit()
        sent_before = len(harness.customer.sent_text)
        asyncio.run(harness.bot.handlers["message.text"]({"body": {
            "msgid": "teacher-reply-after-revoke", "chattype": "single",
            "from": {"userid": "WX-TEACHER"},
            "text": {"content": f"回复 {conversation_id} 不应送达。"},
        }}))
        assert len(harness.customer.sent_text) == sent_before
        harness.parent_sends("EXT-PARENT", "parent-query-after-revoke", "看最近的图片")
        assert len(harness.customer.sent_images) == 1

        with harness.factory() as db:
            messages = list(db.scalars(select(FamilyMessage).where(
                FamilyMessage.conversation_id == conversation_id,
                FamilyMessage.channel_message_id.is_not(None),
            ).order_by(FamilyMessage.created_at, FamilyMessage.message_id)))
            assert any(row.sender_type == "guardian" for row in messages)
            assert any(row.sender_type == "bot" for row in messages)
            assert any(row.sender_type == "teacher" for row in messages)
            assert all(row.image_id != image_id or row.sender_type == "bot" for row in messages)
    finally:
        harness.close()
