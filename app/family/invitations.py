"""One-time guardian invitation creation, redemption, and revocation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.catalog.models import Student, Teacher
from app.core.auth import ADMIN_ROLES
from app.family.models import (
    Guardian,
    GuardianChannelBinding,
    GuardianInvitation,
    StudentGuardian,
)
from app.family.permissions import teacher_can_access_student


CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
RELATIONSHIPS = {
    "father": "father",
    "父亲": "father",
    "mother": "mother",
    "母亲": "mother",
    "other": "other",
    "其他监护人": "other",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_code(code: str | None) -> str:
    return (code or "").strip().upper()


def _digest(code: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"), code.encode("ascii"), hashlib.sha256
    ).hexdigest()


def _require_secret(secret_key: str | None) -> str:
    secret = secret_key or ""
    if not secret.strip():
        raise ValueError("邀请码密钥不能为空")
    return secret


def _active_teacher(db: Session, teacher_id: str) -> Teacher:
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise ValueError("教师不存在")
    if teacher.status != "active":
        raise ValueError("教师已停用")
    return teacher


def _active_student(db: Session, student_id: str) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError("学生不存在")
    if student.status != "active":
        raise ValueError("学生已停用")
    return student


def create_guardian_invitation(
    db: Session,
    student_id: str,
    created_by_teacher_id: str,
    *,
    secret_key: str,
    now: datetime | None = None,
    ttl: timedelta = timedelta(days=7),
) -> tuple[GuardianInvitation, str]:
    """Create a single-use invitation while persisting only its HMAC digest."""
    secret = _require_secret(secret_key)
    current = now or _now()
    if ttl <= timedelta(0):
        raise ValueError("邀请码有效期必须大于零")
    _active_student(db, student_id)
    teacher = _active_teacher(db, created_by_teacher_id)
    if teacher.role not in ADMIN_ROLES and not teacher_can_access_student(
        db, created_by_teacher_id, student_id, current.date()
    ):
        raise ValueError("教师没有权限为该学生创建邀请码")

    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code_hash = _digest(code, secret)
        if db.scalar(
            select(GuardianInvitation.invitation_id).where(
                GuardianInvitation.code_hash == code_hash
            )
        ) is None:
            break

    invitation = GuardianInvitation(
        student_id=student_id,
        code_hash=code_hash,
        expires_at=(current + ttl).isoformat(),
        max_uses=1,
        used_count=0,
        created_by_teacher_id=created_by_teacher_id,
        created_at=current.isoformat(),
    )
    db.add(invitation)
    db.commit()
    return invitation, code


def redeem_guardian_invitation(
    db: Session,
    code: str,
    external_user_id: str,
    relationship: str,
    guardian_name: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> Guardian:
    """Atomically claim an invitation and bind or reuse its guardian identity."""
    secret = _require_secret(secret_key)
    normalized = _normalized_code(code)
    if not normalized:
        raise ValueError("邀请码不能为空")
    if len(normalized) != 8 or any(char not in CODE_ALPHABET for char in normalized):
        raise ValueError("邀请码无效")
    external_id = (external_user_id or "").strip()
    if not external_id:
        raise ValueError("外部用户标识不能为空")
    name = (guardian_name or "").strip()
    if not name:
        raise ValueError("监护人姓名不能为空")
    relation_type = RELATIONSHIPS.get((relationship or "").strip())
    if relation_type is None:
        raise ValueError("监护人关系无效")
    current = now or _now()
    code_hash = _digest(normalized, secret)
    invitation = db.scalar(
        select(GuardianInvitation).where(GuardianInvitation.code_hash == code_hash)
    )
    if invitation is None:
        raise ValueError("邀请码无效")
    if invitation.revoked_at is not None:
        raise ValueError("邀请码已撤销")
    if datetime.fromisoformat(invitation.expires_at) <= current:
        raise ValueError("邀请码已过期")
    if invitation.used_count >= invitation.max_uses:
        raise ValueError("邀请码已使用")
    _active_student(db, invitation.student_id)

    binding = db.scalar(
        select(GuardianChannelBinding).where(
            GuardianChannelBinding.channel == "wecom_customer",
            GuardianChannelBinding.external_user_id == external_id,
        )
    )
    guardian: Guardian
    if binding is not None and binding.guardian_id is not None:
        guardian = db.get(Guardian, binding.guardian_id)
        if guardian is None or guardian.status != "active":
            db.rollback()
            raise ValueError("已有监护人绑定无效")
        if guardian.relationship_type != relation_type:
            db.rollback()
            raise ValueError("已有监护人身份与本次信息冲突")
    else:
        guardian = Guardian(
            name=name,
            relationship_type=relation_type,
            status="active",
            created_at=current.isoformat(),
            updated_at=current.isoformat(),
        )
        db.add(guardian)
        db.flush()
        if binding is None:
            binding = GuardianChannelBinding(
                channel="wecom_customer",
                external_user_id=external_id,
                guardian_id=guardian.guardian_id,
                status="active",
                bound_at=current.isoformat(),
                updated_at=current.isoformat(),
            )
            db.add(binding)
        else:
            binding.guardian_id = guardian.guardian_id

    relation = db.scalar(
        select(StudentGuardian).where(
            StudentGuardian.student_id == invitation.student_id,
            StudentGuardian.guardian_id == guardian.guardian_id,
        )
    )
    if relation is None:
        relation = StudentGuardian(
            student_id=invitation.student_id,
            guardian_id=guardian.guardian_id,
            status="active",
            bound_at=current.isoformat(),
        )
        db.add(relation)
    else:
        relation.status = "active"
        relation.revoked_at = None
        relation.bound_at = current.isoformat()

    binding.status = "active"
    binding.active_student_id = invitation.student_id
    binding.pending_state_json = None
    binding.updated_at = current.isoformat()
    db.flush()

    claimed = db.execute(
        update(GuardianInvitation)
        .where(
            GuardianInvitation.invitation_id == invitation.invitation_id,
            GuardianInvitation.code_hash == code_hash,
            GuardianInvitation.revoked_at.is_(None),
            GuardianInvitation.expires_at > current.isoformat(),
            GuardianInvitation.used_count < GuardianInvitation.max_uses,
        )
        .values(used_count=GuardianInvitation.used_count + 1)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise ValueError("邀请码已使用或失效")
    db.commit()
    db.refresh(invitation)
    return guardian


def revoke_guardian_invitation(
    db: Session, invitation_id: str, revoked_by_teacher_id: str
) -> GuardianInvitation:
    """Revoke an invitation; only active administrative teachers may do so."""
    teacher = _active_teacher(db, revoked_by_teacher_id)
    if teacher.role not in ADMIN_ROLES:
        raise ValueError("仅管理员或校区负责人可撤销邀请码")
    invitation = db.get(GuardianInvitation, invitation_id)
    if invitation is None:
        raise ValueError("邀请码不存在")
    if invitation.revoked_at is None:
        invitation.revoked_at = _now().isoformat()
        db.commit()
    return invitation
