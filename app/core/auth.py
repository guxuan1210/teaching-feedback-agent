"""Authentication and authorization dependencies.

A session cookie (Starlette ``SessionMiddleware``) stores the logged-in
teacher's ID. ``require_login`` loads that teacher from the database on every
request so a deactivated account is immediately locked out; ``require_admin``
gates catalog management to privileged roles.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.catalog.models import Teacher
from app.core.database import get_db

ADMIN_ROLES = {"管理员", "校区负责人"}

LOGIN_URL = "/login"


def is_admin(teacher: Teacher) -> bool:
    return (teacher.role or "") in ADMIN_ROLES


def _redirect_to_login() -> HTTPException:
    return HTTPException(status_code=303, headers={"Location": LOGIN_URL})


def require_login(
    request: Request, db: Session = Depends(get_db)
) -> Teacher:
    teacher_id = request.session.get("teacher_id")
    if not teacher_id:
        raise _redirect_to_login()
    teacher = db.get(Teacher, teacher_id)
    if teacher is None or teacher.status != "active":
        raise _redirect_to_login()
    return teacher


def require_admin(teacher: Teacher = Depends(require_login)) -> Teacher:
    if not is_admin(teacher):
        raise HTTPException(status_code=403, detail="无权限访问")
    return teacher
