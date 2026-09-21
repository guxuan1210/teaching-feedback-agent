"""Deterministic, permission-checked parent queries for the customer channel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Enrollment, Student
from app.family import media as family_media
from app.family.permissions import guardian_can_access_student
from app.profiles.service import build_student_profile
from app.reports.models import WeeklyReport


@dataclass(frozen=True)
class ParentReply:
    content: str
    image_path: str | None = None
    image_filename: str | None = None
    handoff_marker: bool = False


_STUDENT_ID = re.compile(r"(?<![A-Za-z0-9_-])(S[A-Za-z0-9_-]{1,159})(?![A-Za-z0-9_-])")


def _reply(content: str) -> list[ParentReply]:
    return [ParentReply(content)]


def answer_parent_query(
    db: Session,
    *,
    guardian_id: str,
    student_id: str,
    text: str,
) -> list[ParentReply]:
    """Answer a small fixed intent set; never delegates a parent query to a model."""
    explicit = _STUDENT_ID.search(text or "")
    requested_id = explicit.group(1) if explicit else student_id
    if not guardian_can_access_student(db, guardian_id, requested_id):
        return _reply("【机器人回复】你没有查看该学生的权限。")
    student = db.get(Student, requested_id)
    if student is None:
        return _reply("【机器人回复】暂时找不到该学生档案。")
    normalized = (text or "").strip()

    if "图片" in normalized and any(word in normalized for word in ("最近", "查看", "看")):
        images = family_media.list_student_images(db, requested_id, limit=10)
        if not images:
            return _reply("【机器人回复】目前还没有可查看的图片。")
        names = family_media.image_uploader_names(db, images)
        replies = []
        for row in images:
            date_text = row.uploaded_at[:10]
            uploader = names.get(row.uploaded_by_teacher_id, "老师")
            caption = row.caption.strip() if row.caption and row.caption.strip() else "（无说明）"
            content = f"【机器人回复】{date_text} · {caption} · 上传老师：{uploader}"
            # Return a storage reference, not a collection of image byte arrays.
            # The channel handler streams one authorized file at a time.
            replies.append(ParentReply(
                content, row.storage_path,
                f"{row.image_id}{row.extension}",
            ))
        return replies

    if "反馈" in normalized and any(word in normalized for word in ("最近", "查看", "看")):
        enrollments = list(db.scalars(
            select(Enrollment).where(
                Enrollment.student_id == requested_id,
                Enrollment.status == "active",
                Enrollment.start_date <= date.today().isoformat(),
            ).order_by(Enrollment.start_date.desc(), Enrollment.enrollment_id.desc())
        ))
        notes: list[str] = []
        for enrollment in enrollments:
            profile = build_student_profile(
                db, student_id=requested_id, class_id=enrollment.class_id,
                date_from=(date.today() - timedelta(days=365)).isoformat(),
                date_to=date.today().isoformat(),
            )
            for note in profile.recent_notes:
                if note and note.strip() and note.strip() not in notes:
                    notes.append(note.strip())
        if not notes:
            return _reply("【机器人回复】最近暂无有效反馈记录。")
        summary = "；".join(notes[:10])
        return _reply(f"【机器人回复】{student.name}最近反馈：{summary}")

    if "周报" in normalized:
        report = db.scalar(
            select(WeeklyReport).where(
                WeeklyReport.student_id == requested_id,
                WeeklyReport.status == "finalized",
            ).order_by(WeeklyReport.period_end.desc(), WeeklyReport.finalized_at.desc())
            .limit(1)
        )
        if report is None:
            return _reply("【机器人回复】暂无已定稿周报。")
        return _reply(
            f"【机器人回复】{student.name} {report.period_start} 至 {report.period_end}周报：{report.parent_message}"
        )

    if "请老师回复" in normalized or "转老师" in normalized:
        return [ParentReply("【机器人回复】已标记转老师请求，老师会尽快回复。", handoff_marker=True)]

    return _reply("【机器人回复】我可以帮你查看最近图片、最近反馈或已定稿周报，也可以转请老师回复。")
