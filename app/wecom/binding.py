from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.catalog.models import Teacher
from app.wecom.models import (
    TeacherWecomBinding,
    WecomBindingCode,
    WecomChatState,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(code: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"), code.encode("ascii"), hashlib.sha256
    ).hexdigest()


def create_binding_code(
    db: Session, teacher_id: str, secret_key: str
) -> tuple[str, str]:
    now = _now()
    for row in db.scalars(
        select(WecomBindingCode).where(
            WecomBindingCode.teacher_id == teacher_id,
            WecomBindingCode.used_at.is_(None),
        )
    ):
        row.used_at = now.isoformat()

    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = _code_hash(code, secret_key)
        if db.scalar(
            select(WecomBindingCode).where(WecomBindingCode.code_hash == digest)
        ) is None:
            break

    expires_at = (now + timedelta(minutes=10)).isoformat()
    db.add(
        WecomBindingCode(
            code_id=new_id("WBC"),
            code_hash=digest,
            teacher_id=teacher_id,
            expires_at=expires_at,
        )
    )
    db.commit()
    return code, expires_at


@dataclass(frozen=True)
class BindingOutcome:
    status: str
    teacher_name: str | None = None


def bind_user(
    db: Session,
    wecom_user_id: str,
    code: str,
    secret_key: str,
    *,
    now: datetime | None = None,
) -> BindingOutcome:
    current_time = now or _now()
    existing = db.get(TeacherWecomBinding, wecom_user_id)
    if existing is not None:
        teacher = db.get(Teacher, existing.teacher_id)
        return BindingOutcome(
            "already_bound", teacher.name if teacher is not None else None
        )

    row = db.scalar(
        select(WecomBindingCode).where(
            WecomBindingCode.code_hash == _code_hash(code, secret_key),
            WecomBindingCode.used_at.is_(None),
        )
    )
    if row is None:
        return BindingOutcome("invalid_code")
    if datetime.fromisoformat(row.expires_at) <= current_time:
        row.used_at = current_time.isoformat()
        db.commit()
        return BindingOutcome("expired_code")

    teacher = db.get(Teacher, row.teacher_id)
    if teacher is None or teacher.status != "active":
        row.used_at = current_time.isoformat()
        db.commit()
        return BindingOutcome("inactive_teacher")
    teacher_binding = db.scalar(
        select(TeacherWecomBinding).where(
            TeacherWecomBinding.teacher_id == teacher.teacher_id
        )
    )
    if teacher_binding is not None:
        row.used_at = current_time.isoformat()
        db.commit()
        return BindingOutcome("teacher_already_bound", teacher.name)

    row.used_at = current_time.isoformat()
    db.add(
        TeacherWecomBinding(
            wecom_user_id=wecom_user_id,
            teacher_id=teacher.teacher_id,
            bound_at=current_time.isoformat(),
        )
    )
    db.commit()
    return BindingOutcome("completed", teacher.name)


def unbind_teacher(db: Session, teacher_id: str) -> None:
    binding = db.scalar(
        select(TeacherWecomBinding).where(
            TeacherWecomBinding.teacher_id == teacher_id
        )
    )
    if binding is not None:
        db.execute(
            delete(WecomChatState).where(
                WecomChatState.wecom_user_id == binding.wecom_user_id
            )
        )
        db.delete(binding)
    db.commit()
