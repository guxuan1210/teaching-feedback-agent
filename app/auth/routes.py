"""Login and logout routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Teacher
from app.core.auth import is_admin
from app.core.database import get_db
from app.core.security import verify_password

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"errors": []})


@router.post("/login")
def login_submit(
    request: Request,
    name: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    name = name.strip()
    teacher = db.scalar(
        select(Teacher).where(Teacher.name == name, Teacher.status == "active")
    )
    if teacher is None or not teacher.password_hash or not verify_password(
        password, teacher.password_hash
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"errors": ["用户名或密码错误"]},
            status_code=422,
        )

    request.session["teacher_id"] = teacher.teacher_id
    request.session["teacher_name"] = teacher.name
    request.session["is_admin"] = is_admin(teacher)

    next_url = request.query_params.get("next") or "/"
    return RedirectResponse(next_url, status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
