"""Decode and process teacher-uploaded WeCom image messages safely."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.family.media import archive_teacher_images, confirm_pending_media
from app.family.models import PendingMediaAssignment
from app.family.storage import LocalImageStore
from app.wecom.models import TeacherWecomBinding

logger = logging.getLogger(__name__)


class InvalidMediaMessage(ValueError):
    """The inbound event does not contain usable image metadata."""


@dataclass(frozen=True)
class RemoteImage:
    url: str
    aes_key: str | None


@dataclass(frozen=True)
class ParsedMediaMessage:
    message_id: str
    caption: str
    images: tuple[RemoteImage, ...]


@dataclass(frozen=True)
class MediaBotReply:
    content: str
    choices: tuple[tuple[str, str, str], ...] = ()
    pending_id: str | None = None


def is_bound_teacher(db: Session, wecom_user_id: str) -> bool:
    return db.get(TeacherWecomBinding, wecom_user_id) is not None


def _image(item: object) -> RemoteImage:
    if not isinstance(item, dict):
        raise InvalidMediaMessage("invalid image item")
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        raise InvalidMediaMessage("image URL is missing")
    aes_key = item.get("aeskey") or item.get("aes_key")
    if aes_key is not None and not isinstance(aes_key, str):
        raise InvalidMediaMessage("invalid image key")
    return RemoteImage(url.strip(), aes_key)


def parse_media_message(body: dict) -> ParsedMediaMessage:
    """Parse the SDK's image or mixed message body without logging its payload."""
    if not isinstance(body, dict):
        raise InvalidMediaMessage("invalid message body")
    message_id = body.get("msgid")
    if not isinstance(message_id, str) or not message_id:
        raise InvalidMediaMessage("message ID is missing")
    msgtype = body.get("msgtype")
    images: list[RemoteImage] = []
    texts: list[str] = []
    if msgtype == "image":
        images.append(_image(body.get("image")))
    elif msgtype == "mixed":
        mixed = body.get("mixed") or {}
        entries = mixed.get("msg_item", []) if isinstance(mixed, dict) else []
        if not isinstance(entries, list):
            raise InvalidMediaMessage("invalid mixed message")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("msgtype") == "text":
                content = (entry.get("text") or {}).get("content")
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip())
            elif entry.get("msgtype") == "image":
                images.append(_image(entry.get("image")))
    else:
        raise InvalidMediaMessage("unsupported media message type")
    if not images:
        raise InvalidMediaMessage("no images in message")
    return ParsedMediaMessage(message_id, "\n".join(texts), tuple(images))


async def download_and_decrypt_images(client, parsed: ParsedMediaMessage) -> list[bytes]:
    """Fetch/decrypt each SDK media URL in original message order."""
    payloads = []
    for image in parsed.images:
        downloaded = await client.download_file(image.url, image.aes_key)
        payload = downloaded[0] if isinstance(downloaded, tuple) else downloaded
        if not isinstance(payload, bytes):
            raise InvalidMediaMessage("media download returned invalid bytes")
        payloads.append(payload)
    return payloads


def process_teacher_media(
    db: Session,
    store: LocalImageStore,
    *,
    binding_secret: str,
    wecom_user_id: str,
    parsed: ParsedMediaMessage,
    payloads: list[bytes],
    pending_media_minutes: int = 15,
) -> MediaBotReply:
    del binding_secret  # Teacher identity is established by the persisted binding.
    binding = db.get(TeacherWecomBinding, wecom_user_id)
    if binding is None:
        return MediaBotReply("请先登录 Teaching Agent 并绑定老师账号。")
    result = archive_teacher_images(
        db, store, teacher_id=binding.teacher_id,
        source_message_id=parsed.message_id, caption=parsed.caption,
        payloads=payloads, pending_media_minutes=pending_media_minutes,
    )
    if result.status == "archived":
        from app.catalog.models import Student

        student = db.get(Student, result.student_id)
        return MediaBotReply(
            f"已归档到{student.name if student else '学生'}（{result.student_id}），共 {len(result.image_ids)} 张。"
        )
    if result.status == "duplicate":
        from app.catalog.models import Student

        student = db.get(Student, result.student_id)
        return MediaBotReply(
            f"这条消息的图片已经归档到{student.name if student else '学生'}（{result.student_id}）。"
        )
    if result.status == "needs_student":
        pending = db.scalar(select(PendingMediaAssignment).where(
            PendingMediaAssignment.source_message_id == parsed.message_id,
            PendingMediaAssignment.teacher_id == binding.teacher_id,
            PendingMediaAssignment.status == "pending",
        ))
        if pending is None:
            return MediaBotReply("待归档图片不可用，请重新发送。")
        choices = tuple((item.student_id, item.name, f"media_{pending.pending_id}_{item.student_id}") for item in result.choices[:6])
        return MediaBotReply("请先选择图片所属学生。", choices, pending.pending_id)
    return MediaBotReply(result.message + "。")


def process_media_choice(
    db: Session,
    store: LocalImageStore,
    *,
    wecom_user_id: str,
    event_key: str,
) -> MediaBotReply:
    match = re.fullmatch(r"media_(PMA_[a-f0-9]{32})_(S[A-Za-z0-9_-]{0,159})", event_key or "")
    binding = db.get(TeacherWecomBinding, wecom_user_id)
    if match is None or binding is None:
        return MediaBotReply("该选择已失效，请重新发送图片。")
    pending_id, student_id = match.groups()
    result = confirm_pending_media(
        db, store, teacher_id=binding.teacher_id,
        pending_id=pending_id, student_id=student_id,
    )
    if result.status != "archived":
        return MediaBotReply(result.message + "。")
    from app.catalog.models import Student

    student = db.get(Student, student_id)
    return MediaBotReply(
        f"已归档到{student.name if student else '学生'}（{student_id}），共 {len(result.image_ids)} 张。"
    )
