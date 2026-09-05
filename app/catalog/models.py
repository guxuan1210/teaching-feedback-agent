"""Catalog tables: teacher, student, class, and effective-dated enrollment."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Teacher(Base):
    __tablename__ = "teacher"

    teacher_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="ck_teacher_status"),
    )


class Student(Base):
    __tablename__ = "student"

    student_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str | None] = mapped_column(String)
    current_stage: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="ck_student_status"),
    )


class Class(Base):
    __tablename__ = "class"

    class_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str | None] = mapped_column(String)
    class_type: Mapped[str] = mapped_column(String, nullable=False)
    head_teacher_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("teacher.teacher_id")
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("class_type IN ('daily','special')", name="ck_class_type"),
        CheckConstraint("status IN ('active','inactive')", name="ck_class_status"),
    )


class Enrollment(Base):
    __tablename__ = "enrollment"

    enrollment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(
        String, ForeignKey("student.student_id"), nullable=False
    )
    class_id: Mapped[str] = mapped_column(
        String, ForeignKey("class.class_id"), nullable=False
    )
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_enrollment_dates"
        ),
        CheckConstraint("status IN ('active','left')", name="ck_enrollment_status"),
    )
