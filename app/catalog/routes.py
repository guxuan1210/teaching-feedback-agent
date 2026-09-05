"""Server-rendered catalog routes: teachers, classes, students, and a minimal
enrollment-form shell that lists active-only records.

POST success redirects to the relevant GET page with the created business ID in
``?created=<id>``; validation failures re-render the same template with HTTP 422
and the entered values preserved.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.catalog import repository
from app.catalog.models import Class, Student, Teacher
from app.core.database import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/catalog", tags=["catalog"])


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def catalog_index(request: Request):
    return templates.TemplateResponse(request, "catalog/index.html")


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
def _students_context(
    request: Request,
    session: Session,
    form: dict,
    editing: bool = False,
    record_id: str | None = None,
    errors: list[str] | None = None,
) -> dict:
    return {
        "students": repository.list_students(session),
        "form": form,
        "editing": editing,
        "record_id": record_id,
        "action_url": f"/catalog/students/{record_id}" if editing else "/catalog/students",
        "errors": errors or [],
        "created": request.query_params.get("created"),
    }


@router.get("/students", response_class=HTMLResponse)
def students_page(request: Request, session: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "catalog/students.html",
        _students_context(request, session, {"name": "", "grade": "", "current_stage": ""}),
    )


@router.post("/students")
def students_create(
    request: Request,
    name: str = Form(...),
    grade: str = Form(""),
    current_stage: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {"name": name, "grade": grade, "current_stage": current_stage}
    try:
        student = repository.create_student(
            session, name=name, grade=grade, current_stage=current_stage
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/students.html",
            _students_context(request, session, form, errors=[str(exc)]),
            status_code=422,
        )
    return RedirectResponse(
        f"/catalog/students?created={student.student_id}", status_code=303
    )


@router.get("/students/{student_id}/edit", response_class=HTMLResponse)
def students_edit(
    request: Request, student_id: str, session: Session = Depends(get_db)
):
    student = session.get(Student, student_id)
    if student is None:
        return templates.TemplateResponse(
            request,
            "catalog/students.html",
            _students_context(
                request,
                session,
                {"name": "", "grade": "", "current_stage": ""},
                errors=["学生不存在"],
            ),
            status_code=404,
        )
    form = {
        "name": student.name,
        "grade": student.grade or "",
        "current_stage": student.current_stage or "",
    }
    return templates.TemplateResponse(
        request,
        "catalog/students.html",
        _students_context(request, session, form, editing=True, record_id=student_id),
    )


@router.post("/students/{student_id}")
def students_update(
    request: Request,
    student_id: str,
    name: str = Form(...),
    grade: str = Form(""),
    current_stage: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {"name": name, "grade": grade, "current_stage": current_stage}
    try:
        repository.update_student(
            session, student_id, name=name, grade=grade, current_stage=current_stage
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/students.html",
            _students_context(
                request, session, form, editing=True, record_id=student_id, errors=[str(exc)]
            ),
            status_code=422,
        )
    return RedirectResponse("/catalog/students", status_code=303)


@router.post("/students/{student_id}/deactivate")
def students_deactivate(student_id: str, session: Session = Depends(get_db)):
    repository.deactivate_student(session, student_id)
    return RedirectResponse("/catalog/students", status_code=303)


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
def _classes_context(
    request: Request,
    session: Session,
    form: dict,
    editing: bool = False,
    record_id: str | None = None,
    errors: list[str] | None = None,
) -> dict:
    return {
        "classes": repository.list_classes(session),
        "teachers": repository.list_teachers(session),
        "form": form,
        "editing": editing,
        "record_id": record_id,
        "action_url": f"/catalog/classes/{record_id}" if editing else "/catalog/classes",
        "errors": errors or [],
        "created": request.query_params.get("created"),
    }


def _empty_class_form() -> dict:
    return {"name": "", "grade": "", "class_type": "daily", "head_teacher_id": ""}


@router.get("/classes", response_class=HTMLResponse)
def classes_page(request: Request, session: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "catalog/classes.html", _classes_context(request, session, _empty_class_form())
    )


@router.post("/classes")
def classes_create(
    request: Request,
    name: str = Form(...),
    grade: str = Form(""),
    class_type: str = Form("daily"),
    head_teacher_id: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {
        "name": name,
        "grade": grade,
        "class_type": class_type,
        "head_teacher_id": head_teacher_id,
    }
    try:
        klass = repository.create_class(
            session,
            name=name,
            class_type=class_type,
            grade=grade,
            head_teacher_id=head_teacher_id,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/classes.html",
            _classes_context(request, session, form, errors=[str(exc)]),
            status_code=422,
        )
    return RedirectResponse(f"/catalog/classes?created={klass.class_id}", status_code=303)


@router.get("/classes/{class_id}/edit", response_class=HTMLResponse)
def classes_edit(request: Request, class_id: str, session: Session = Depends(get_db)):
    klass = session.get(Class, class_id)
    if klass is None:
        return templates.TemplateResponse(
            request,
            "catalog/classes.html",
            _classes_context(request, session, _empty_class_form(), errors=["班级不存在"]),
            status_code=404,
        )
    form = {
        "name": klass.name,
        "grade": klass.grade or "",
        "class_type": klass.class_type,
        "head_teacher_id": klass.head_teacher_id or "",
    }
    return templates.TemplateResponse(
        request,
        "catalog/classes.html",
        _classes_context(request, session, form, editing=True, record_id=class_id),
    )


@router.post("/classes/{class_id}")
def classes_update(
    request: Request,
    class_id: str,
    name: str = Form(...),
    grade: str = Form(""),
    class_type: str = Form("daily"),
    head_teacher_id: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {
        "name": name,
        "grade": grade,
        "class_type": class_type,
        "head_teacher_id": head_teacher_id,
    }
    try:
        repository.update_class(
            session,
            class_id,
            name=name,
            class_type=class_type,
            grade=grade,
            head_teacher_id=head_teacher_id,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/classes.html",
            _classes_context(
                request, session, form, editing=True, record_id=class_id, errors=[str(exc)]
            ),
            status_code=422,
        )
    return RedirectResponse("/catalog/classes", status_code=303)


@router.post("/classes/{class_id}/deactivate")
def classes_deactivate(class_id: str, session: Session = Depends(get_db)):
    repository.deactivate_class(session, class_id)
    return RedirectResponse("/catalog/classes", status_code=303)


# ---------------------------------------------------------------------------
# Teachers
# ---------------------------------------------------------------------------
def _teachers_context(
    request: Request,
    session: Session,
    form: dict,
    editing: bool = False,
    record_id: str | None = None,
    errors: list[str] | None = None,
) -> dict:
    return {
        "teachers": repository.list_teachers(session),
        "form": form,
        "editing": editing,
        "record_id": record_id,
        "action_url": f"/catalog/teachers/{record_id}" if editing else "/catalog/teachers",
        "errors": errors or [],
        "created": request.query_params.get("created"),
    }


@router.get("/teachers", response_class=HTMLResponse)
def teachers_page(request: Request, session: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "catalog/teachers.html", _teachers_context(request, session, {"name": "", "role": ""})
    )


@router.post("/teachers")
def teachers_create(
    request: Request,
    name: str = Form(...),
    role: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {"name": name, "role": role}
    try:
        teacher = repository.create_teacher(session, name=name, role=role)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/teachers.html",
            _teachers_context(request, session, form, errors=[str(exc)]),
            status_code=422,
        )
    return RedirectResponse(
        f"/catalog/teachers?created={teacher.teacher_id}", status_code=303
    )


@router.get("/teachers/{teacher_id}/edit", response_class=HTMLResponse)
def teachers_edit(
    request: Request, teacher_id: str, session: Session = Depends(get_db)
):
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        return templates.TemplateResponse(
            request,
            "catalog/teachers.html",
            _teachers_context(request, session, {"name": "", "role": ""}, errors=["教师不存在"]),
            status_code=404,
        )
    form = {"name": teacher.name, "role": teacher.role or ""}
    return templates.TemplateResponse(
        request,
        "catalog/teachers.html",
        _teachers_context(request, session, form, editing=True, record_id=teacher_id),
    )


@router.post("/teachers/{teacher_id}")
def teachers_update(
    request: Request,
    teacher_id: str,
    name: str = Form(...),
    role: str = Form(""),
    session: Session = Depends(get_db),
):
    form = {"name": name, "role": role}
    try:
        repository.update_teacher(session, teacher_id, name=name, role=role)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/teachers.html",
            _teachers_context(
                request, session, form, editing=True, record_id=teacher_id, errors=[str(exc)]
            ),
            status_code=422,
        )
    return RedirectResponse("/catalog/teachers", status_code=303)


@router.post("/teachers/{teacher_id}/deactivate")
def teachers_deactivate(teacher_id: str, session: Session = Depends(get_db)):
    repository.deactivate_teacher(session, teacher_id)
    return RedirectResponse("/catalog/teachers", status_code=303)


# ---------------------------------------------------------------------------
# Enrollments (effective-dated membership)
# ---------------------------------------------------------------------------
def _enrollments_context(
    request: Request,
    session: Session,
    form: dict | None = None,
    errors: list[str] | None = None,
) -> dict:
    rows = []
    for enrollment in repository.list_enrollments(session):
        student = session.get(Student, enrollment.student_id)
        klass = session.get(Class, enrollment.class_id)
        rows.append(
            {
                "enrollment_id": enrollment.enrollment_id,
                "student_name": student.name if student else enrollment.student_id,
                "class_name": klass.name if klass else enrollment.class_id,
                "start_date": enrollment.start_date,
                "end_date": enrollment.end_date,
                "status": enrollment.status,
            }
        )
    return {
        "enrollments": rows,
        "students": repository.list_students(session, active_only=True),
        "classes": repository.list_classes(session, active_only=True),
        "form": form or {"student_id": "", "class_id": "", "start_date": ""},
        "errors": errors or [],
        "created": request.query_params.get("created"),
    }


@router.get("/enrollments", response_class=HTMLResponse)
def enrollments_page(request: Request, session: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "catalog/enrollments.html", _enrollments_context(request, session)
    )


@router.post("/enrollments")
def enrollments_create(
    request: Request,
    student_id: str = Form(...),
    class_id: str = Form(...),
    start_date: str = Form(...),
    session: Session = Depends(get_db),
):
    form = {"student_id": student_id, "class_id": class_id, "start_date": start_date}
    try:
        repository.enroll_student(
            session, student_id, class_id, date.fromisoformat(start_date)
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/enrollments.html",
            _enrollments_context(request, session, form=form, errors=[str(exc)]),
            status_code=422,
        )
    return RedirectResponse("/catalog/enrollments", status_code=303)


@router.post("/enrollments/{enrollment_id}/leave")
def enrollments_leave(
    request: Request,
    enrollment_id: int,
    end_date: str = Form(...),
    session: Session = Depends(get_db),
):
    try:
        repository.leave_class(session, enrollment_id, date.fromisoformat(end_date))
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "catalog/enrollments.html",
            _enrollments_context(request, session, errors=[str(exc)]),
            status_code=422,
        )
    return RedirectResponse("/catalog/enrollments", status_code=303)
