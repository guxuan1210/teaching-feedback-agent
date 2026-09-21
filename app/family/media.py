"""Teacher image ingestion, pending assignment, and student image lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import Student, Teacher
from app.core.auth import ADMIN_ROLES
from app.core.ids import new_id
from app.family.models import PendingMediaAssignment, StudentImage
from app.family.permissions import teacher_can_access_student
from app.family.storage import ImageStorageError, ImageTooLarge, LocalImageStore, UnsupportedImage

PENDING_MINUTES = 15
IMAGE_PAGE_SIZE = 24
MAX_IMAGE_PAGE_SIZE = 100
MAX_IMAGE_OFFSET = 100_000
_SAFE_MESSAGE = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


@dataclass(frozen=True)
class StudentChoice:
    student_id: str
    name: str


@dataclass(frozen=True)
class MediaArchiveResult:
    status: Literal["archived", "needs_student", "duplicate", "rejected"]
    student_id: str | None
    image_ids: Sequence[str]
    choices: Sequence[StudentChoice]
    message: str


def _moment(now: datetime | None) -> datetime:
    moment = datetime.now(timezone.utc) if now is None else now
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(timezone.utc)


def _result(status, message, student_id=None, image_ids=(), choices=()):
    return MediaArchiveResult(status, student_id, tuple(image_ids), tuple(choices), message)


def _choices(db: Session, teacher_id: str, on) -> list[StudentChoice]:
    return [StudentChoice(row.student_id, row.name) for row in db.scalars(select(Student).where(Student.status == "active").order_by(Student.name, Student.student_id)) if teacher_can_access_student(db, teacher_id, row.student_id, on)]


def _match(caption: str, choices: list[StudentChoice]):
    tokens = re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]{0,159}(?![A-Za-z0-9_-])", caption)
    accessible_ids = {choice.student_id for choice in choices}
    # Student identifiers in this app use S-prefixed IDs. Read the first
    # explicit ID token so an unauthorized one cannot be bypassed by another.
    student = next((token for token in tokens if token in accessible_ids or re.fullmatch(r"S[A-Za-z0-9_-]*", token)), None)
    if student is not None:
        choice = next((c for c in choices if c.student_id == student), None)
        stripped = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(student)}(?![A-Za-z0-9_-])", " ", caption)
        if choice:
            stripped = re.sub(rf"(?<!\w){re.escape(choice.name)}(?!\w)", " ", stripped)
        return (student if choice else "!rejected"), stripped
    matched = [choice for choice in choices if choice.name and re.search(rf"(?<!\w){re.escape(choice.name)}(?!\w)", caption)]
    stripped = caption
    for choice in matched:
        stripped = re.sub(rf"(?<!\w){re.escape(choice.name)}(?!\w)", " ", stripped)
    return (matched[0].student_id if len(matched) == 1 else None), stripped


def _chunks(payload):
    if isinstance(payload, (bytes, bytearray, memoryview)):
        yield bytes(payload)
    else:
        yield from payload


def _stage(store: LocalImageStore, payloads):
    paths, metadata = [], []
    try:
        for payload in payloads:
            fd, absolute = tempfile.mkstemp(prefix=".media-", suffix=".tmp", dir=store.root)
            path = Path(absolute)
            rel = path.name
            paths.append(rel)
            digest, size, prefix = hashlib.sha256(), 0, bytearray()
            with os.fdopen(fd, "wb") as out:
                for chunk in _chunks(payload):
                    if not isinstance(chunk, bytes):
                        raise UnsupportedImage("image chunks must be bytes")
                    size += len(chunk)
                    if size > store.max_bytes:
                        raise ImageTooLarge("image exceeds configured limit")
                    if len(prefix) < 12:
                        prefix.extend(chunk[:12-len(prefix)])
                    out.write(chunk)
                    digest.update(chunk)
            fmt = LocalImageStore._identify(bytes(prefix))
            if fmt is None:
                raise UnsupportedImage("unsupported image signature")
            metadata.append({"mime_type": fmt[0], "extension": fmt[1], "byte_size": size, "sha256": digest.hexdigest(), "position": len(metadata)})
        if not metadata:
            raise UnsupportedImage("at least one image is required")
        return paths, metadata
    except Exception:
        for rel in paths:
            try: store.remove_temporary(rel)
            except (FileNotFoundError, ImageStorageError): pass
        raise


def _cleanup(store, paths):
    for path in paths:
        try: store.remove_temporary(path)
        except (FileNotFoundError, ImageStorageError): pass


def _existing(db, source):
    return list(db.scalars(select(StudentImage).where(StudentImage.source_message_id == source).order_by(StudentImage.source_position)))


def _archive_staged(db, store, *, teacher_id, student_id, source, caption, paths, metadata, moment, pending=None):
    made = []
    try:
        for path, meta in zip(paths, metadata):
            image_id = new_id("IMG")
            with store.open(path) as stream:
                saved = store.save(student_id, image_id, iter(lambda: stream.read(65536), b""), now=moment)
            made.append(saved.relative_path)
            db.add(StudentImage(image_id=image_id, student_id=student_id, uploaded_by_teacher_id=teacher_id,
                source_message_id=source, source_position=meta["position"], caption=caption,
                mime_type=saved.mime_type, extension=saved.extension, byte_size=saved.byte_size,
                sha256=saved.sha256, storage_path=saved.relative_path))
        if pending is not None:
            pending.status = "completed"
            pending.temporary_paths_json = "[]"
        db.commit()
        return _result("archived", "图片已归档", student_id, [row.image_id for row in db.scalars(select(StudentImage).where(StudentImage.source_message_id == source).order_by(StudentImage.source_position))])
    except Exception:
        db.rollback()
        for path in made:
            try: store.remove_temporary(path)
            except Exception: pass
        raise


def archive_teacher_images(db: Session, store: LocalImageStore, *, teacher_id: str, source_message_id: str,
                           caption: str, payloads, now: datetime | None = None,
                           pending_media_minutes: int = PENDING_MINUTES) -> MediaArchiveResult:
    moment = _moment(now)
    if isinstance(pending_media_minutes, bool) or not isinstance(pending_media_minutes, int) or pending_media_minutes < 1:
        raise ValueError("pending_media_minutes must be a positive integer")
    if not isinstance(source_message_id, str) or not _SAFE_MESSAGE.fullmatch(source_message_id):
        return _result("rejected", "消息标识无效")
    teacher = db.get(Teacher, teacher_id)
    if teacher is None or teacher.status != "active":
        return _result("rejected", "当前教师不可用")
    existing = _existing(db, source_message_id)
    if existing:
        if not teacher_can_access_student(db, teacher_id, existing[0].student_id, moment.date()):
            return _result("rejected", "当前教师无权访问该学生")
        return _result("duplicate", "该消息图片已归档", existing[0].student_id, [r.image_id for r in existing])
    choices = _choices(db, teacher_id, moment.date())
    if not choices:
        return _result("rejected", "当前教师无可归档学生")
    pending = db.scalar(select(PendingMediaAssignment).where(PendingMediaAssignment.source_message_id == source_message_id))
    if pending:
        if pending.teacher_id != teacher_id:
            return _result("rejected", "该消息已由其他教师处理")
        current_ids = {choice.student_id for choice in choices}
        saved_choices = [StudentChoice(**v) for v in json.loads(pending.choices_json) if v["student_id"] in current_ids]
        return _result("needs_student" if saved_choices else "rejected", "请选择学生" if saved_choices else "当前教师无可访问候选学生", choices=saved_choices)
    student_id, clean_caption = _match(caption or "", choices)
    if student_id == "!rejected": return _result("rejected", "学生标识无效或无权限")
    if student_id is None and not choices: return _result("rejected", "当前教师无可归档学生")
    try:
        paths, metadata = _stage(store, payloads)
    except (ImageStorageError, OSError, ValueError):
        return _result("rejected", "图片格式或大小无效")
    note = clean_caption.strip() or None
    if student_id:
        try:
            return _archive_staged(db, store, teacher_id=teacher_id, student_id=student_id, source=source_message_id, caption=note, paths=paths, metadata=metadata, moment=moment)
        finally:
            _cleanup(store, paths)
    row = PendingMediaAssignment(teacher_id=teacher_id, source_message_id=source_message_id, caption=note,
        media_json=json.dumps(metadata), choices_json=json.dumps([c.__dict__ for c in choices], ensure_ascii=False),
        temporary_paths_json=json.dumps(paths), expires_at=(moment + timedelta(minutes=pending_media_minutes)).isoformat(), status="pending")
    try:
        db.add(row); db.commit()
    except Exception:
        db.rollback(); _cleanup(store, paths); raise
    return _result("needs_student", "请选择学生", choices=choices)


def confirm_pending_media(db: Session, store: LocalImageStore, *, teacher_id: str, pending_id: str, student_id: str, now: datetime | None = None) -> MediaArchiveResult:
    moment = _moment(now)
    pending = db.get(PendingMediaAssignment, pending_id)
    if pending is None or pending.teacher_id != teacher_id or pending.status != "pending": return _result("rejected", "待确认图片不可用")
    paths = json.loads(pending.temporary_paths_json)
    if datetime.fromisoformat(pending.expires_at) <= moment:
        pending.status = "expired"; pending.temporary_paths_json = "[]"; db.commit(); _cleanup(store, paths)
        return _result("rejected", "待确认图片已过期")
    choices = {c["student_id"] for c in json.loads(pending.choices_json)}
    if student_id not in choices:
        return _result("rejected", "所选学生不在待选名单中")
    if not teacher_can_access_student(db, teacher_id, student_id, moment.date()):
        _cleanup(store, paths); pending.temporary_paths_json = "[]"; pending.status = "expired"; db.commit()
        return _result("rejected", "所选学生无权限")
    try:
        result = _archive_staged(db, store, teacher_id=teacher_id, student_id=student_id, source=pending.source_message_id, caption=pending.caption,
                                 paths=paths, metadata=json.loads(pending.media_json), moment=moment, pending=pending)
        _cleanup(store, paths)
        return result
    except Exception:
        _cleanup(store, paths); raise


def expire_pending_media(db: Session, store: LocalImageStore, *, now: datetime | None = None) -> int:
    moment = _moment(now); count = 0
    rows = list(db.scalars(select(PendingMediaAssignment).where(PendingMediaAssignment.status == "pending")))
    for row in rows:
        if datetime.fromisoformat(row.expires_at) <= moment:
            _cleanup(store, json.loads(row.temporary_paths_json)); row.temporary_paths_json = "[]"; row.status = "expired"; count += 1
    if count: db.commit()
    return count


def _image_query(
    student_id: str,
    *,
    include_deleted: bool,
    date_from: date | None,
    date_to: date | None,
):
    if date_from is not None and (
        isinstance(date_from, datetime) or not isinstance(date_from, date)
    ):
        raise ValueError("invalid image start date")
    if date_to is not None and (
        isinstance(date_to, datetime) or not isinstance(date_to, date)
    ):
        raise ValueError("invalid image end date")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("image start date must not follow end date")

    stmt = select(StudentImage).where(StudentImage.student_id == student_id)
    if not include_deleted:
        stmt = stmt.where(StudentImage.status == "active")
    if date_from is not None:
        stmt = stmt.where(StudentImage.uploaded_at >= date_from.isoformat())
    if date_to is not None and date_to < date.max:
        stmt = stmt.where(
            StudentImage.uploaded_at < (date_to + timedelta(days=1)).isoformat()
        )
    return stmt


def _validate_image_pagination(limit: int, offset: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_IMAGE_PAGE_SIZE
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or offset > MAX_IMAGE_OFFSET
    ):
        raise ValueError("invalid pagination")


def list_student_images(
    db: Session,
    student_id: str,
    *,
    include_deleted: bool = False,
    limit: int = IMAGE_PAGE_SIZE,
    offset: int = 0,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[StudentImage]:
    _validate_image_pagination(limit, offset)
    stmt = _image_query(
        student_id,
        include_deleted=include_deleted,
        date_from=date_from,
        date_to=date_to,
    )
    return list(
        db.scalars(
            stmt.order_by(
                StudentImage.uploaded_at.desc(),
                StudentImage.source_position,
                StudentImage.image_id,
            )
            .limit(limit)
            .offset(offset)
        )
    )


def count_student_images(
    db: Session,
    student_id: str,
    *,
    include_deleted: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    stmt = _image_query(
        student_id,
        include_deleted=include_deleted,
        date_from=date_from,
        date_to=date_to,
    )
    return int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


def image_uploader_names(db: Session, images: list[StudentImage]) -> dict[str, str]:
    uploader_ids = {image.uploaded_by_teacher_id for image in images}
    if not uploader_ids:
        return {}
    return {
        teacher_id: name
        for teacher_id, name in db.execute(
            select(Teacher.teacher_id, Teacher.name).where(
                Teacher.teacher_id.in_(uploader_ids)
            )
        )
    }


def _admin(db, actor):
    teacher = db.get(Teacher, actor)
    if teacher is None or teacher.role not in ADMIN_ROLES: raise PermissionError("administrator required")


def soft_delete_image(db: Session, store: LocalImageStore, image_id: str, deleted_by_teacher_id: str) -> StudentImage:
    _admin(db, deleted_by_teacher_id)
    row = db.get(StudentImage, image_id)
    if row is None: raise LookupError("image not found")
    if row.status == "deleted": return row
    quarantine = store.quarantine(row.storage_path)
    row.status = "deleted"; row.deleted_at = datetime.now(timezone.utc).isoformat(); row.deleted_by_teacher_id = deleted_by_teacher_id; row.quarantine_path = quarantine
    try: db.commit()
    except Exception:
        db.rollback(); store.restore(quarantine, row.storage_path); raise
    return row


def restore_image(db: Session, store: LocalImageStore, image_id: str, restored_by_teacher_id: str) -> StudentImage:
    _admin(db, restored_by_teacher_id)
    row = db.get(StudentImage, image_id)
    if row is None: raise LookupError("image not found")
    if row.status == "active": return row
    quarantine = row.quarantine_path
    store.restore(quarantine, row.storage_path)
    row.status = "active"; row.deleted_at = None; row.deleted_by_teacher_id = None; row.quarantine_path = None
    try: db.commit()
    except Exception:
        db.rollback(); row = db.get(StudentImage, image_id); row.quarantine_path = store.quarantine(row.storage_path); db.commit(); raise
    return row
