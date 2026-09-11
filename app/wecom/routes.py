from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Teacher
from app.core.auth import require_login
from app.core.database import get_db
from app.wecom import binding as binding_service
from app.wecom.models import TeacherWecomBinding

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(tags=["account"])


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    binding = db.scalar(
        select(TeacherWecomBinding).where(
            TeacherWecomBinding.teacher_id == teacher.teacher_id
        )
    )
    code = request.session.pop("wecom_binding_code", None)
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "binding": binding,
            "binding_code": code,
            "unbound": request.query_params.get("unbound") == "1",
        },
    )


@router.post("/account/wecom-binding-code")
def generate_binding_code(
    request: Request,
    teacher: Teacher = Depends(require_login),
    db: Session = Depends(get_db),
):
    code, _expires_at = binding_service.create_binding_code(
        db, teacher.teacher_id, request.app.state.secret_key
    )
    request.session["wecom_binding_code"] = code
    return RedirectResponse("/account", status_code=303)


@router.post("/account/wecom-unbind")
def unbind(
    teacher: Teacher = Depends(require_login), db: Session = Depends(get_db)
):
    binding_service.unbind_teacher(db, teacher.teacher_id)
    return RedirectResponse("/account?unbound=1", status_code=303)
