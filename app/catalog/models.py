"""Catalog tables: teacher, student, class, and effective-dated enrollment."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Teacher(Base):
    __tablename__ = "teacher"

    teacher_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String)
    password_hash: Mapped[str | None] = mapped_column(String)
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
    late_care_level: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="ck_student_status"),
    )


class StageDict(Base):
    __tablename__ = "stage_dict"

    stage_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LateCareLevelDict(Base):
    __tablename__ = "late_care_level_dict"

    level_id: Mapped[str] = mapped_column(String, primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AssessmentModule(Base):
    __tablename__ = "assessment_module"

    module_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AssessmentDimension(Base):
    __tablename__ = "assessment_dimension"

    dimension_id: Mapped[str] = mapped_column(String, primary_key=True)
    module_id: Mapped[str] = mapped_column(
        String, ForeignKey("assessment_module.module_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AssessmentScoreAnchor(Base):
    __tablename__ = "assessment_score_anchor"

    dimension_id: Mapped[str] = mapped_column(
        String, ForeignKey("assessment_dimension.dimension_id"), primary_key=True
    )
    score: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 5", name="ck_assessment_anchor_score"),
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
