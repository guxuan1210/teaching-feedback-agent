"""Text-message state machine for parents using WeCom Customer Service."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import re

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.family import invitations
from app.catalog.models import Teacher
from app.family.models import (
    FamilyConversation, FamilyMessage, Guardian, GuardianChannelBinding,
    GuardianInvitation, StudentImage, StudentTeacherAssignment,
)
from app.family import conversations
from app.family.parent_assistant import ParentReply, answer_parent_query, answer_parent_question
from app.family.permissions import guardian_can_access_student, list_students_for_guardian


_RELATIONSHIPS = {"父亲": "father", "母亲": "mother", "其他监护人": "other"}
_CODE = re.compile(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}\Z", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _reply(text: str) -> list[ParentReply]:
    return [ParentReply(text)]


def _binding(db: Session, external_user_id: str) -> GuardianChannelBinding:
    row = db.scalar(select(GuardianChannelBinding).where(
        GuardianChannelBinding.channel == "wecom_customer",
        GuardianChannelBinding.external_user_id == external_user_id,
    ))
    if row is None:
        for attempt in range(2):
            row = GuardianChannelBinding(
                channel="wecom_customer", external_user_id=external_user_id,
                status="pending", pending_state_json=None,
            )
            db.add(row)
            try:
                db.commit()
                break
            except (IntegrityError, OperationalError):
                db.rollback()
                row = db.scalar(select(GuardianChannelBinding).where(
                    GuardianChannelBinding.channel == "wecom_customer",
                    GuardianChannelBinding.external_user_id == external_user_id,
                ))
                if row is not None:
                    break
                if attempt == 1:
                    raise
    return row


def _conversation(db: Session, guardian_id: str, student_id: str, external_user_id: str):
    row = db.scalar(select(FamilyConversation).where(
        FamilyConversation.guardian_id == guardian_id,
        FamilyConversation.student_id == student_id,
        FamilyConversation.channel == "wecom_customer",
        FamilyConversation.channel_conversation_id == external_user_id,
    ))
    if row is not None:
        return row
    effective = date.today().isoformat()
    teacher_id = db.scalar(select(StudentTeacherAssignment.teacher_id).join(
        Teacher, Teacher.teacher_id == StudentTeacherAssignment.teacher_id
    ).where(
        StudentTeacherAssignment.student_id == student_id,
        StudentTeacherAssignment.status == "active",
        StudentTeacherAssignment.role == "primary",
        StudentTeacherAssignment.start_date <= effective,
        or_(StudentTeacherAssignment.end_date.is_(None), StudentTeacherAssignment.end_date >= effective),
        Teacher.status == "active",
    ).order_by(StudentTeacherAssignment.start_date.desc()).limit(1))
    row = FamilyConversation(
        guardian_id=guardian_id, student_id=student_id,
        assigned_teacher_id=teacher_id, channel="wecom_customer",
        channel_conversation_id=external_user_id, status="active",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(FamilyConversation).where(
            FamilyConversation.guardian_id == guardian_id,
            FamilyConversation.student_id == student_id,
            FamilyConversation.channel == "wecom_customer",
            FamilyConversation.channel_conversation_id == external_user_id,
        ))
    return row


def _record_inbound(db: Session, conversation: FamilyConversation, message_id: str, text: str) -> bool:
    if db.scalar(select(FamilyMessage.message_id).where(
        FamilyMessage.channel_message_id == message_id
    )) is not None:
        return False
    db.add(FamilyMessage(
        conversation_id=conversation.conversation_id, direction="inbound",
        sender_type="guardian", sender_id=conversation.guardian_id,
        content=text, channel_message_id=message_id, status="completed",
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    conversation.last_message_at = _now()
    db.commit()
    return True


def _deliver_outbox(db: Session, customer, external_user_id: str, image_store, inbound_channel_message_id: str):
    """Send only persisted outbox rows; callers must not resend returned ParentReply values."""
    rows = conversations.pending_replies_for_inbound(db, inbound_channel_message_id)
    for row in rows:
        try:
            if row.image_id:
                image = db.get(StudentImage, row.image_id)
                if image is None or image_store is None:
                    raise ValueError("image payload unavailable")
                with image_store.open(image.storage_path) as source:
                    media_id = customer.upload_image(source.read(), f"{image.image_id}{image.extension}")
                channel_id = customer.send_image(external_user_id, media_id)
            else:
                channel_id = customer.send_text(external_user_id, row.content)
            conversations.mark_message_sent(db, row.message_id, channel_id)
        except Exception as exc:
            # API implementations wrap transport failures, but injected or future
            # clients may raise any ordinary exception. Do not erase the outbox row.
            conversations.mark_message_failed(
                db, row.message_id, f"渠道发送失败：{type(exc).__name__}"
            )


def _replies_for_inbound(db: Session, inbound: FamilyMessage) -> list[ParentReply]:
    rows = conversations.outbound_for_inbound(db, inbound.message_id)
    replies = []
    for row in rows:
        image_path = None
        filename = None
        if row.image_id:
            image = db.get(StudentImage, row.image_id)
            if image is not None:
                image_path, filename = image.storage_path, f"{image.image_id}{image.extension}"
        inbound = db.scalar(select(FamilyMessage).where(
            FamilyMessage.message_id == row.reply_to_message_id
        ))
        handoff = bool(inbound and any(
            term in inbound.content for term in ("请老师回复", "转老师")
        ))
        replies.append(ParentReply(row.content, image_path, filename, handoff))
    return replies


def _queue_query_replies(
    db: Session, conversation: FamilyConversation, inbound: FamilyMessage,
    guardian_id: str, student_id: str, text: str,
) -> list[ParentReply]:
    replies = answer_parent_query(
        db, guardian_id=guardian_id, student_id=student_id, text=text,
    )
    for reply in replies:
        conversations.record_bot_reply(
            db, conversation.conversation_id, reply.content,
            reply_to_message_id=inbound.message_id,
        )
        if reply.image_path:
            image = db.scalar(select(StudentImage).where(
                StudentImage.student_id == student_id,
                StudentImage.storage_path == reply.image_path,
                StudentImage.status == "active",
            ))
            if image is not None:
                conversations.record_bot_reply(
                    db, conversation.conversation_id, reply.content,
                    reply_to_message_id=inbound.message_id, image_id=image.image_id,
                )
    if any(reply.handoff_marker for reply in replies):
        conversations.request_teacher_reply(db, conversation.conversation_id, text)
    return replies


def _queue_simple_replies(
    db: Session, conversation: FamilyConversation, inbound: FamilyMessage,
    customer, external_user_id: str, image_store, replies: list[ParentReply],
) -> list[ParentReply]:
    for reply in replies:
        conversations.record_bot_reply(
            db, conversation.conversation_id, reply.content,
            reply_to_message_id=inbound.message_id,
        )
    _deliver_outbox(db, customer, external_user_id, image_store, inbound.channel_message_id)
    return _replies_for_inbound(db, inbound)


def _invite_by_code(db: Session, text: str, secret_key: str) -> GuardianInvitation | None:
    code = (text or "").strip().upper()
    if not _CODE.fullmatch(code) or not (secret_key or "").strip():
        return None
    digest = hmac.new(secret_key.encode("utf-8"), code.encode("ascii"), hashlib.sha256).hexdigest()
    return db.scalar(select(GuardianInvitation).where(GuardianInvitation.code_hash == digest))


def _choice_prompt(db: Session, guardian_id: str) -> list[ParentReply]:
    students = list_students_for_guardian(db, guardian_id)
    if not students:
        return _reply("【机器人回复】目前没有可访问的学生，请联系老师。")
    options = "；".join(f"{index}：{row.name}（{row.student_id}）" for index, row in enumerate(students, 1))
    return _reply(f"请选择孩子序号：{options}")


def process_parent_text(
    db: Session, customer, *, secret_key: str, external_user_id: str,
    message_id: str, text: str, image_store=None, assistant_provider=None,
) -> list[ParentReply]:
    """Process and deliver deterministic responses through a durable outbox.

    Returned replies describe persisted actions; channel delivery is performed here,
    so adapters must not send these replies a second time.
    """
    external = (external_user_id or "").strip()
    msg_id = (message_id or "").strip()
    body = (text or "").strip()
    if not external or not msg_id:
        return _reply("【机器人回复】消息信息不完整，请稍后重试。")

    # A duplicate callback replays the exact saved outbox and retries only rows that
    # have not reached the channel successfully.
    duplicate = db.scalar(select(FamilyMessage).where(
        FamilyMessage.channel_message_id == msg_id,
        FamilyMessage.direction == "inbound",
    ))
    if duplicate is not None:
        saved = conversations.outbound_for_inbound(db, duplicate.message_id)
        replayable_text = duplicate.content not in (
            "[邀请码已核销]", *_RELATIONSHIPS.keys()
        )
        if not saved and replayable_text:
            binding = db.scalar(select(GuardianChannelBinding).where(
                GuardianChannelBinding.channel == "wecom_customer",
                GuardianChannelBinding.external_user_id == external,
            ))
            guardian = db.get(Guardian, binding.guardian_id) if binding and binding.guardian_id else None
            student_id = binding.active_student_id if binding else None
            if (
                guardian is not None and guardian.status == "active" and student_id
                and binding.status == "active"
                and guardian_can_access_student(db, guardian.guardian_id, student_id)
            ):
                conversation = _conversation(db, guardian.guardian_id, student_id, external)
                _queue_query_replies(
                    db, conversation, duplicate, guardian.guardian_id,
                    student_id, duplicate.content,
                )
                saved = conversations.outbound_for_inbound(db, duplicate.message_id)
        if saved and all(row.status == "completed" for row in saved):
            return _reply("【机器人回复】这条消息已处理。")
        _deliver_outbox(db, customer, external, image_store, msg_id)
        result = _replies_for_inbound(db, duplicate)
        return result or _reply("【机器人回复】这条消息已处理。")

    binding = _binding(db, external)
    guardian = db.get(Guardian, binding.guardian_id) if binding.guardian_id else None
    # Invitation codes are secrets; convert them to opaque state before any
    # inbound transcript is recorded.
    invitation = _invite_by_code(db, body, secret_key)
    if invitation is not None:
        pending = _load(binding.pending_state_json)
        if (
            pending.get("step") == "awaiting_relationship"
            and pending.get("invitation_id") == invitation.invitation_id
            and pending.get("invitation_message_id") == msg_id
        ):
            return _reply("请选择身份：父亲、母亲或其他监护人。")
        binding.pending_state_json = _dump({
            "step": "awaiting_relationship",
            "invitation_id": invitation.invitation_id,
            "invitation_message_id": msg_id,
        })
        db.commit()
        return _reply("请选择身份：父亲、母亲或其他监护人。")

    if binding.status == "active" and guardian is not None and guardian.status == "active":
        active_id = binding.active_student_id
        if active_id and guardian_can_access_student(db, guardian.guardian_id, active_id):
            active_conversation = _conversation(db, guardian.guardian_id, active_id, external)
            if not _record_inbound(db, active_conversation, msg_id, body):
                return _reply("【机器人回复】这条消息已处理。")
            inbound = db.scalar(select(FamilyMessage).where(
                FamilyMessage.channel_message_id == msg_id,
                FamilyMessage.direction == "inbound",
            ))

    pending = _load(binding.pending_state_json)
    if pending.get("step") == "awaiting_relationship":
        relationship = _RELATIONSHIPS.get(body)
        invitation_id = pending.get("invitation_id")
        if relationship is None or not isinstance(invitation_id, str):
            return _reply("请选择身份：父亲、母亲或其他监护人。")
        display_name = (
            getattr(customer, "guardian_name", None)
            or getattr(customer, "name", None)
            or getattr(customer, "nickname", None)
            or "微信家长"
        )
        try:
            guardian = invitations.redeem_guardian_invitation_by_id(
                db, invitation_id, external, relationship, str(display_name),
                secret_key=secret_key,
            )
        except ValueError:
            binding.pending_state_json = None
            db.commit()
            return _reply("【机器人回复】邀请码无效、已过期或已使用，请联系老师重新获取。")
        binding = db.scalar(select(GuardianChannelBinding).where(
            GuardianChannelBinding.channel == "wecom_customer",
            GuardianChannelBinding.external_user_id == external,
        ))
        if binding is not None:
            binding.pending_state_json = None
            db.commit()
            if binding.active_student_id and guardian_can_access_student(
                db, guardian.guardian_id, binding.active_student_id
            ):
                conversation = _conversation(
                    db, guardian.guardian_id, binding.active_student_id, external
                )
                _record_inbound(db, conversation, msg_id, body)
                invitation_message_id = pending.get("invitation_message_id")
                if (
                    isinstance(invitation_message_id, str)
                    and invitation_message_id != msg_id
                ):
                    _record_inbound(
                        db, conversation, invitation_message_id, "[邀请码已核销]"
                    )
        return _reply("【机器人回复】绑定成功。你可以查看最近图片、最近反馈或已定稿周报。")

    if binding.status != "active" or guardian is None or guardian.status != "active":
        return _reply("【机器人回复】请先发送管理员提供的一次性邀请码完成绑定。")

    students = list_students_for_guardian(db, guardian.guardian_id)
    pending = _load(binding.pending_state_json)
    if pending.get("step") == "awaiting_student":
        # Resolve by ordinal or by an ID present in the freshly authorized list.
        index = None
        if body.isdecimal():
            index = int(body) - 1
        elif body:
            index = next((i for i, child in enumerate(students) if child.student_id == body), None)
        if index is None or index < 0 or index >= len(students):
            return _queue_simple_replies(
                db, active_conversation, inbound, customer, external, image_store,
                _choice_prompt(db, guardian.guardian_id),
            )
        selected = students[index]
        if not guardian_can_access_student(db, guardian.guardian_id, selected.student_id):
            binding.pending_state_json = None
            db.commit()
            return _queue_simple_replies(
                db, active_conversation, inbound, customer, external, image_store,
                _reply("【机器人回复】你没有查看该学生的权限。"),
            )
        binding.active_student_id = selected.student_id
        binding.pending_state_json = None
        db.commit()
        return _queue_simple_replies(
            db, active_conversation, inbound, customer, external, image_store,
            _reply(f"【机器人回复】已切换到{selected.name}。"),
        )

    if "切换孩子" in body:
        if len(students) < 2:
            return _queue_simple_replies(
                db, active_conversation, inbound, customer, external, image_store,
                _reply("【机器人回复】目前只绑定了一个孩子。"),
            )
        binding.pending_state_json = _dump({"step": "awaiting_student"})
        db.commit()
        return _queue_simple_replies(
            db, active_conversation, inbound, customer, external, image_store,
            _choice_prompt(db, guardian.guardian_id),
        )

    active_student_id = binding.active_student_id
    if not active_student_id or not guardian_can_access_student(db, guardian.guardian_id, active_student_id):
        if students:
            binding.pending_state_json = _dump({"step": "awaiting_student"})
            db.commit()
            return _choice_prompt(db, guardian.guardian_id)
        return _reply("【机器人回复】目前没有可访问的学生，请联系老师。")

    deterministic_intent = any(
        token in body for token in ("图片", "反馈", "周报", "请老师回复", "转老师")
    )
    if assistant_provider is not None and not deterministic_intent:
        answer_parent_question(
            db, assistant_provider, guardian_id=guardian.guardian_id,
            student_id=active_student_id, text=body, inbound_message=inbound,
        )
        _deliver_outbox(db, customer, external, image_store, msg_id)
        return _replies_for_inbound(db, inbound)

    # Persist every text and image action before any outbound API call. Image sends
    # point to the already archived image row; bytes are never persisted in SQL.
    inbound = db.scalar(select(FamilyMessage).where(
        FamilyMessage.channel_message_id == msg_id,
        FamilyMessage.direction == "inbound",
    ))
    if inbound is None:
        return _reply("【机器人回复】这条消息已处理。")
    _queue_query_replies(
        db, active_conversation, inbound, guardian.guardian_id,
        active_student_id, body,
    )
    _deliver_outbox(db, customer, external, image_store, msg_id)
    return _replies_for_inbound(db, inbound)
