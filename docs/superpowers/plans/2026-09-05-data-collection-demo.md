# Teaching Feedback Data Collection Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally runnable FastAPI + SQLite demo for maintaining teaching rosters, batch-entering daily and special-course feedback, reviewing history, and exporting filtered data to Excel.

**Architecture:** A server-rendered FastAPI application separates catalog, session, feedback, history, and export behavior into focused modules. SQLAlchemy owns persistence and transaction handling; Jinja templates plus small local JavaScript files provide the batch-entry interface. SQLite is the first adapter, with database access kept behind repository and use-case functions so a later multi-user deployment can replace it.

**Tech Stack:** Python, FastAPI, Jinja2, SQLAlchemy, SQLite, openpyxl, pytest, FastAPI TestClient, local CSS and JavaScript.

---

## File map

```text
pyproject.toml                         dependencies and test configuration
.gitignore                             local database, cache, and environment exclusions
README.md                              setup, run, backup, and demo workflow
app/__init__.py                        package marker
app/__main__.py                        one-command local startup
app/main.py                            application factory and router registration
app/core/database.py                   engine, sessions, foreign keys, schema version
app/core/ids.py                        UUID-based business identifiers
app/catalog/models.py                  teacher, student, class, enrollment tables
app/catalog/repository.py              catalog reads and writes
app/catalog/routes.py                  catalog pages and form actions
app/sessions/models.py                 class_session table
app/sessions/repository.py             session and active-roster queries
app/sessions/routes.py                 today page and session creation
app/feedback/models.py                 feedback tables and indicator associations
app/feedback/forms.py                  form parsing and validation
app/feedback/service.py                save, edit, void, and indicator-category rules
app/feedback/routes.py                 batch workspace and feedback actions
app/history/routes.py                  filters, detail, edit, and void pages
app/export/service.py                  filtered workbook construction
app/export/routes.py                   .xlsx download route
app/templates/base.html                shared navigation and flash/error area
app/templates/today.html               today's classes and sessions
app/templates/catalog/*.html           teachers, classes, students, enrollments
app/templates/feedback/workspace.html  batch entry workspace
app/templates/history/*.html           history list and detail/edit page
app/static/app.css                     responsive local styling
app/static/workspace.js                dirty-form warning and next-student focus
db/schema.sql                          updated reference schema for the demo
db/seed_indicators.sql                 existing 85 indicator definitions
tests/conftest.py                      isolated application and database fixtures
tests/test_health.py                   startup smoke test
tests/catalog/test_catalog.py          base-information behavior
tests/catalog/test_enrollment.py       effective-date rules
tests/sessions/test_sessions.py        class-session and roster behavior
tests/feedback/test_daily.py           daily feedback workflow
tests/feedback/test_special.py         special feedback workflow
tests/feedback/test_history.py         filtering, editing, and voiding
tests/export/test_workbook.py           workbook contents and filters
tests/test_acceptance.py                end-to-end demo workflow
```

## Task 1: Bootstrap a runnable application

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/__main__.py`
- Create: `app/main.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Initialize version control for the existing project**

Run:

```powershell
git init
git add db docs *.xlsx *.pdf
git commit -m "chore: capture initial teaching data design"
```

Expected: a new Git repository with the existing design assets in the first commit.

- [ ] **Step 2: Write the failing health test**

```python
from fastapi.testclient import TestClient
from app.main import create_app


def test_health_page_reports_ready():
    client = TestClient(create_app(database_url="sqlite+pysqlite:///:memory:"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
```

- [ ] **Step 3: Run the test and confirm the application is missing**

Run: `python -m pytest tests/test_health.py -v`  
Expected: FAIL because `app.main` does not exist.

- [ ] **Step 4: Add packaging, dependencies, and the minimal application factory**

Use this dependency set in `pyproject.toml`:

```toml
[project]
name = "teaching-feedback-demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi", "uvicorn", "jinja2", "python-multipart", "sqlalchemy", "openpyxl"]

[project.optional-dependencies]
dev = ["pytest", "httpx"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `app/main.py` with the stable factory interface:

```python
from fastapi import FastAPI


def create_app(database_url: str | None = None) -> FastAPI:
    application = FastAPI(title="教学反馈数据采集 Demo")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    return application


app = create_app()
```

Create `app/__main__.py`:

```python
import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
```

Ignore `.venv/`, `__pycache__/`, `.pytest_cache/`, `data/*.db`, and `.superpowers/` in `.gitignore`.

- [ ] **Step 5: Install and verify**

Run: `python -m pip install -e ".[dev]"`  
Expected: package and development dependencies install successfully.

Run: `python -m pytest tests/test_health.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit the bootstrap**

```powershell
git add pyproject.toml .gitignore app tests/test_health.py
git commit -m "feat: bootstrap local feedback demo"
```

## Task 2: Build the SQLite persistence foundation

**Files:**
- Create: `app/core/database.py`
- Create: `app/core/ids.py`
- Create: `app/catalog/models.py`
- Create: `app/sessions/models.py`
- Create: `app/feedback/models.py`
- Modify: `app/main.py`
- Modify: `db/schema.sql`
- Create: `tests/conftest.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write failing database invariant tests**

```python
import pytest
from sqlalchemy.exc import IntegrityError
from app.catalog.models import Student
from app.feedback.models import DailyFeedback


def test_foreign_keys_are_enabled(db_session):
    enabled = db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    assert enabled == 1


def test_daily_rating_rejects_values_outside_one_to_five(db_session, daily_session, student):
    row = DailyFeedback(
        feedback_id="F-invalid", session_id=daily_session.session_id,
        student_id=student.student_id, rating_knowledge=0,
        rating_habit=3, rating_mindset=3,
    )
    db_session.add(row)
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 2: Run the tests and verify missing models fail**

Run: `python -m pytest tests/test_database.py -v`  
Expected: FAIL because database and model modules do not exist.

- [ ] **Step 3: Implement connection initialization and identifiers**

Expose `SCHEMA_VERSION = 1`; `build_engine(database_url: str) -> Engine`; `build_session_factory(engine: Engine) -> sessionmaker`; `initialize_database(engine: Engine) -> None`; and `get_db(request) -> Iterator[Session]` from `app/core/database.py`.

`build_engine` must attach a SQLAlchemy `connect` listener that executes `PRAGMA foreign_keys=ON`. File-backed SQLite also receives `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. `initialize_database` imports every model, runs `Base.metadata.create_all(engine)`, then verifies or sets `PRAGMA user_version=1`.

Create IDs through one function in `app/core/ids.py`:

```python
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
```

- [ ] **Step 4: Define the tables and constraints**

Use SQLAlchemy declarative models for:

```text
teacher, student, class, enrollment, class_session,
indicator, daily_feedback, daily_feedback_indicator,
special_feedback, special_feedback_indicator
```

The model contract must include:

```python
CheckConstraint("rating_knowledge BETWEEN 1 AND 5")
CheckConstraint("rating_habit BETWEEN 1 AND 5")
CheckConstraint("rating_mindset BETWEEN 1 AND 5")
UniqueConstraint("session_id", "student_id")
CheckConstraint("status IN ('active','void')")
CheckConstraint("end_date IS NULL OR end_date >= start_date")
```

`ClassSession` stores `session_type`, `session_date`, `start_time`, optional `course_name`, `class_id`, `teacher_id`, and `status`. Both feedback tables reference the session and student. Indicator association foreign keys use `ON DELETE CASCADE`; business records use `NO ACTION` and status changes instead of hard deletion.

Update `db/schema.sql` to mirror these exact tables and constraints so the reference design no longer contradicts the executable model.

- [ ] **Step 5: Create isolated test fixtures**

`tests/conftest.py` must provide a temporary file-backed SQLite database, a transaction-safe `db_session`, a FastAPI `client`, and seeded teacher/student/class/session helpers. Do not use the production `data/` directory in tests.

- [ ] **Step 6: Run persistence tests**

Run: `python -m pytest tests/test_database.py -v`  
Expected: all database invariant tests PASS.

- [ ] **Step 7: Commit the persistence foundation**

```powershell
git add app/core app/catalog/models.py app/sessions/models.py app/feedback/models.py app/main.py db/schema.sql tests
git commit -m "feat: add constrained SQLite persistence model"
```

## Task 3: Seed indicators and implement base-information management

**Files:**
- Create: `app/catalog/repository.py`
- Create: `app/catalog/routes.py`
- Create: `app/templates/base.html`
- Create: `app/templates/catalog/index.html`
- Create: `app/templates/catalog/students.html`
- Create: `app/templates/catalog/classes.html`
- Create: `app/templates/catalog/teachers.html`
- Modify: `app/main.py`
- Create: `tests/catalog/test_catalog.py`

- [ ] **Step 1: Write failing catalog tests**

```python
def test_create_student_and_show_it_in_catalog(client):
    response = client.post("/catalog/students", data={
        "name": "李明", "grade": "三年级", "current_stage": "三阶"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "李明" in response.text


def test_inactive_student_is_not_available_for_new_enrollment(client, student):
    client.post(f"/catalog/students/{student.student_id}/deactivate")
    response = client.get("/catalog/enrollments/new")
    assert student.name not in response.text
```

- [ ] **Step 2: Run and confirm routes are missing**

Run: `python -m pytest tests/catalog/test_catalog.py -v`  
Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement catalog repository functions**

Expose explicit repository functions named `list_students`, `create_student`, `update_student`, `deactivate_student`, `list_classes`, `create_class`, `list_teachers`, and `create_teacher`. List functions accept `active_only: bool = False`; create functions require the fields shown in their HTML forms; update functions accept only an allowlist of editable fields; deactivate functions set `status="inactive"` and `updated_at` without deleting rows.

Reject blank names and values outside the class/status dictionaries before committing.

- [ ] **Step 4: Implement server-rendered catalog routes**

Register `/catalog`, `/catalog/students`, `/catalog/classes`, and `/catalog/teachers`. Each resource page contains a compact create form and a table with edit and deactivate actions. POST success redirects to the relevant GET page with the created business ID in `?created=<id>`; validation failure returns the same template with status 422 and the entered values preserved.

At startup, seed indicators from `db/seed_indicators.sql` only when their IDs are absent. Never use SQLite `REPLACE` against existing rows; convert the seed behavior to insert-missing semantics inside one transaction.

- [ ] **Step 5: Run catalog tests**

Run: `python -m pytest tests/catalog/test_catalog.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit catalog management**

```powershell
git add app/catalog app/templates app/main.py tests/catalog
git commit -m "feat: manage teaching catalog data"
```

## Task 4: Implement effective-dated enrollments

**Files:**
- Modify: `app/catalog/repository.py`
- Modify: `app/catalog/routes.py`
- Create: `app/templates/catalog/enrollments.html`
- Create: `tests/catalog/test_enrollment.py`

- [ ] **Step 1: Write failing effective-date tests**

```python
from datetime import date
import pytest
from app.catalog.repository import enroll_student, leave_class


def test_student_can_rejoin_same_class_after_leaving(db_session, student, classroom):
    first = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))
    leave_class(db_session, first.enrollment_id, date(2026, 9, 10))
    second = enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 20))
    assert second.enrollment_id != first.enrollment_id


def test_overlapping_enrollment_is_rejected(db_session, student, classroom):
    enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 1))
    with pytest.raises(ValueError, match="已在该班级的有效期内"):
        enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 5))
```

- [ ] **Step 2: Run and confirm enrollment functions are missing**

Run: `python -m pytest tests/catalog/test_enrollment.py -v`  
Expected: FAIL on missing repository functions.

- [ ] **Step 3: Implement enrollment rules**

Add `enroll_student(db, student_id: str, class_id: str, start_date: date) -> Enrollment`, `leave_class(db, enrollment_id: int, end_date: date) -> Enrollment`, and `active_roster(db, class_id: str, on_date: date) -> list[Student]`.

`enroll_student` rejects any existing interval where `existing.start_date <= new_end` and `new_start <= existing.end_date`, treating a null end date as infinity. `leave_class` rejects end dates before start dates. `active_roster` uses `start_date <= on_date` and `(end_date IS NULL OR end_date >= on_date)`.

- [ ] **Step 4: Add the enrollment page**

The page lists current and historical memberships, supports enroll and leave actions, and excludes inactive students/classes from new-enrollment selectors while retaining them in history.

- [ ] **Step 5: Run enrollment and catalog tests**

Run: `python -m pytest tests/catalog -v`  
Expected: PASS.

- [ ] **Step 6: Commit enrollment history**

```powershell
git add app/catalog app/templates/catalog tests/catalog
git commit -m "feat: track effective-dated class enrollment"
```

## Task 5: Add today's sessions and the batch workspace shell

**Files:**
- Create: `app/sessions/repository.py`
- Create: `app/sessions/routes.py`
- Create: `app/templates/today.html`
- Create: `app/feedback/routes.py`
- Create: `app/templates/feedback/workspace.html`
- Modify: `app/main.py`
- Create: `tests/sessions/test_sessions.py`

- [ ] **Step 1: Write failing session tests**

```python
def test_same_class_can_have_two_sessions_on_same_day(client, seeded_catalog):
    payload = {"class_id": "C1", "teacher_id": "T1", "session_type": "daily", "session_date": "2026-09-05"}
    first = client.post("/sessions", data={**payload, "start_time": "15:30"}, follow_redirects=False)
    second = client.post("/sessions", data={**payload, "start_time": "17:30"}, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] != second.headers["location"]


def test_workspace_uses_roster_effective_on_session_date(client, session_with_roster):
    response = client.get(f"/sessions/{session_with_roster.session_id}")
    assert "李明" in response.text
    assert "已离班学生" not in response.text
```

- [ ] **Step 2: Run and confirm session routes are missing**

Run: `python -m pytest tests/sessions/test_sessions.py -v`  
Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement session repository and routes**

Expose `create_session(db: Session, *, class_id: str, teacher_id: str, session_type: str, session_date: date, start_time: time, course_name: str | None = None) -> ClassSession`, `list_sessions_for_date(db, session_date: date) -> list[ClassSessionSummary]`, and `get_session_with_roster(db, session_id: str) -> SessionWorkspace`. `ClassSessionSummary` includes completed and total counts; `SessionWorkspace` includes the session, effective roster, and each student's active feedback status.

Validate that class type matches session type, `course_name` is present for special sessions, and teacher/class/student records are active. `POST /sessions` always creates a new session and redirects to `/sessions/{session_id}`. `GET /` lists today's sessions with completed/total counts and provides the new-session form.

- [ ] **Step 4: Render the workspace shell**

The left column lists effective roster students and their feedback status. The right column contains the selected student's server-rendered form area. Use `?student_id=` for deterministic selection; when absent, select the first unfinished student, then the first roster student.

- [ ] **Step 5: Run session tests**

Run: `python -m pytest tests/sessions/test_sessions.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit sessions and workspace shell**

```powershell
git add app/sessions app/feedback/routes.py app/templates app/main.py tests/sessions
git commit -m "feat: create class sessions and batch workspace"
```

## Task 6: Implement daily and special feedback entry

**Files:**
- Create: `app/feedback/forms.py`
- Create: `app/feedback/service.py`
- Modify: `app/feedback/routes.py`
- Modify: `app/templates/feedback/workspace.html`
- Create: `app/static/workspace.js`
- Create: `tests/feedback/test_daily.py`
- Create: `tests/feedback/test_special.py`

- [ ] **Step 1: Write failing daily-feedback tests**

```python
def test_save_daily_feedback_and_redirect_to_next_student(client, daily_session_two_students):
    response = client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "4", "rating_habit": "3", "rating_mindset": "5",
        "progress_indicators": ["K001", "H003", "M002"],
        "weak_indicators": ["KW002"], "note": "今天能主动检查。"
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?student_id=S2")


def test_invalid_rating_preserves_form_values(client, daily_session_two_students):
    response = client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "6", "rating_habit": "3", "rating_mindset": "5",
        "note": "不要清空这段话"
    })
    assert response.status_code == 422
    assert "不要清空这段话" in response.text
    assert "评分必须是1到5" in response.text
```

- [ ] **Step 2: Write failing special-feedback and duplicate tests**

```python
def test_save_special_feedback(client, special_session):
    response = client.post("/sessions/SESSION2/special/S1", data={
        "rating_skill": "4", "rating_habit": "4",
        "progress_indicators": ["SK001", "SH001"],
        "weak_indicators": ["SKW003"], "note": "继续训练细节。"
    }, follow_redirects=False)
    assert response.status_code == 303


def test_resubmitting_opens_existing_record_instead_of_duplicating(client, daily_session_one_student, db_session):
    from sqlalchemy import func, select
    from app.feedback.models import DailyFeedback

    payload = {"rating_knowledge": "4", "rating_habit": "4", "rating_mindset": "4"}
    client.post("/sessions/SESSION1/daily/S1", data=payload)
    client.post("/sessions/SESSION1/daily/S1", data=payload)
    count = db_session.scalar(
        select(func.count()).select_from(DailyFeedback).where(
            DailyFeedback.session_id == "SESSION1",
            DailyFeedback.student_id == "S1",
        )
    )
    assert count == 1
```

- [ ] **Step 3: Run and verify feedback tests fail**

Run: `python -m pytest tests/feedback/test_daily.py tests/feedback/test_special.py -v`  
Expected: FAIL because form parsing and save behavior are missing.

- [ ] **Step 4: Implement form contracts and use cases**

Create form dataclasses or Pydantic models with these interfaces:

```python
from pydantic import BaseModel, Field


class DailyFeedbackInput(BaseModel):
    rating_knowledge: int = Field(ge=1, le=5)
    rating_habit: int = Field(ge=1, le=5)
    rating_mindset: int = Field(ge=1, le=5)
    progress_indicators: list[str] = Field(default_factory=list)
    weak_indicators: list[str] = Field(default_factory=list)
    note: str | None = None

class SpecialFeedbackInput(BaseModel):
    rating_skill: int = Field(ge=1, le=5)
    rating_habit: int = Field(ge=1, le=5)
    progress_indicators: list[str] = Field(default_factory=list)
    weak_indicators: list[str] = Field(default_factory=list)
    note: str | None = None
```

Expose `save_daily_feedback(db, session_id: str, student_id: str, values: DailyFeedbackInput) -> DailyFeedback`, `save_special_feedback(db, session_id: str, student_id: str, values: SpecialFeedbackInput) -> SpecialFeedback`, and `next_unfinished_student(db, session_id: str, after_student_id: str) -> str | None`.

The save functions validate session type, active enrollment on the session date, ratings, and allowed indicator categories. Main row and association rows commit once. When an active record exists, update it rather than insert a duplicate.

- [ ] **Step 5: Complete both workspace forms**

Daily shows only `daily_k_progress`, `daily_k_weak`, `daily_h_progress`, `daily_h_weak`, `daily_m_progress`, and `daily_m_weak`. Special shows only `special_k_progress`, `special_k_weak`, `special_h_progress`, and `special_h_weak`. Both show radio-like 1–5 rating controls, checkbox groups, note, and “保存并填写下一位”.

`workspace.js` sets a dirty flag after input changes and warns before switching students. A successful save clears the flag through a `data-saved="true"` marker in the redirected page.

- [ ] **Step 6: Run feedback tests**

Run: `python -m pytest tests/feedback/test_daily.py tests/feedback/test_special.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit feedback entry**

```powershell
git add app/feedback app/templates/feedback app/static/workspace.js tests/feedback
git commit -m "feat: collect batch daily and special feedback"
```

## Task 7: Add history filtering, editing, and voiding

**Files:**
- Create: `app/history/routes.py`
- Create: `app/templates/history/index.html`
- Create: `app/templates/history/detail.html`
- Modify: `app/feedback/service.py`
- Modify: `app/main.py`
- Create: `tests/feedback/test_history.py`

- [ ] **Step 1: Write failing history tests**

```python
def test_history_filters_by_date_class_student_type_and_status(client, mixed_feedback):
    response = client.get("/history?date_from=2026-09-01&date_to=2026-09-30&class_id=C1&student_id=S1&session_type=daily&status=active")
    assert response.status_code == 200
    assert "李明" in response.text
    assert "王芳" not in response.text
    assert "专项" not in response.text


def test_void_keeps_record_but_hides_it_from_default_history(client, daily_feedback):
    client.post(f"/history/daily/{daily_feedback.feedback_id}/void")
    default_page = client.get("/history")
    void_page = client.get("/history?status=void")
    assert daily_feedback.feedback_id not in default_page.text
    assert daily_feedback.feedback_id in void_page.text
```

- [ ] **Step 2: Run and confirm history routes fail**

Run: `python -m pytest tests/feedback/test_history.py -v`  
Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement history query and mutation contracts**

Expose a `HistoryFilters` value with `date_from`, `date_to`, `session_type`, `class_id`, `student_id`, and `status`. Implement one normalized result row interface for daily and special feedback so the template does not branch on raw table shapes.

Add `query_history(db, filters: HistoryFilters) -> list[HistoryRow]`, `update_daily_feedback(db, feedback_id: str, values: DailyFeedbackInput) -> DailyFeedback`, `update_special_feedback(db, feedback_id: str, values: SpecialFeedbackInput) -> SpecialFeedback`, and `void_feedback(db, feedback_type: str, feedback_id: str) -> None`.

Every update sets an ISO-8601 UTC `updated_at`. Void changes status only and retains associations.

- [ ] **Step 4: Implement history pages**

`GET /history` renders filters and results. `GET /history/{type}/{id}` renders the same field controls as the workspace. POST update returns validation errors with entered values; POST void requires an explicit confirmation form and redirects to history.

- [ ] **Step 5: Run history tests**

Run: `python -m pytest tests/feedback/test_history.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit history behavior**

```powershell
git add app/history app/feedback/service.py app/templates/history app/main.py tests/feedback/test_history.py
git commit -m "feat: review edit and void feedback history"
```

## Task 8: Export filtered records to Excel

**Files:**
- Create: `app/export/service.py`
- Create: `app/export/routes.py`
- Modify: `app/templates/history/index.html`
- Modify: `app/main.py`
- Create: `tests/export/test_workbook.py`

- [ ] **Step 1: Write failing workbook tests**

```python
from io import BytesIO
from openpyxl import load_workbook


def test_export_contains_four_sheets_and_filtered_rows(client, mixed_feedback):
    response = client.get("/export.xlsx?date_from=2026-09-01&date_to=2026-09-30&class_id=C1")
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames == ["学生", "班级", "晚辅反馈", "专项反馈"]
    assert book["晚辅反馈"].max_row == 2


def test_void_feedback_is_excluded_unless_requested(client, void_feedback):
    normal = load_workbook(BytesIO(client.get("/export.xlsx").content))
    included = load_workbook(BytesIO(client.get("/export.xlsx?status=all").content))
    assert normal["晚辅反馈"].max_row < included["晚辅反馈"].max_row
```

- [ ] **Step 2: Run and confirm export route fails**

Run: `python -m pytest tests/export/test_workbook.py -v`  
Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement workbook construction**

Expose `build_workbook(db, filters: HistoryFilters) -> bytes`.

Create the sheets in the exact order `学生`, `班级`, `晚辅反馈`, `专项反馈`. Feedback sheets include session date/time, class, student, teacher, ratings, indicator text joined with `；`, note, status, created time, and updated time. Freeze the header row, enable filters, apply readable column widths, and preserve Chinese text.

- [ ] **Step 4: Implement download route**

`GET /export.xlsx` accepts the same query parameters as history, calls `build_workbook`, and returns MIME type `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` with filename `教学反馈_YYYYMMDD_HHMM.xlsx`. The history page export link preserves the current query string.

- [ ] **Step 5: Run export tests**

Run: `python -m pytest tests/export/test_workbook.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit Excel export**

```powershell
git add app/export app/templates/history/index.html app/main.py tests/export
git commit -m "feat: export filtered teaching feedback workbook"
```

## Task 9: Finish responsive styling and local startup

**Files:**
- Create: `app/static/app.css`
- Modify: `app/templates/base.html`
- Modify: `app/templates/today.html`
- Modify: `app/templates/feedback/workspace.html`
- Modify: `app/templates/history/*.html`
- Modify: `app/templates/catalog/*.html`
- Modify: `app/__main__.py`
- Create: `README.md`
- Create: `tests/test_pages.py`

- [ ] **Step 1: Write failing page-shell tests**

```python
def test_pages_use_local_assets_and_have_primary_navigation(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/static/app.css"' in response.text
    assert "今日填写" in response.text
    assert "历史记录" in response.text
    assert "基础信息" in response.text
    assert "https://" not in response.text
```

- [ ] **Step 2: Run and verify styling test fails**

Run: `python -m pytest tests/test_pages.py -v`  
Expected: FAIL because the shared page shell is incomplete.

- [ ] **Step 3: Implement the responsive visual system**

Use `app.css` for a desktop-first two-column workspace that collapses to a student selector above the form under 800px. Define consistent focus states, 44px minimum interactive height, visible validation summaries, status chips, sticky save actions, and print-safe tables. Do not load fonts, CSS, icons, or scripts from a CDN.

- [ ] **Step 4: Finalize one-command startup and documentation**

`python -m app` must create `data/`, initialize `data/teaching_demo.db`, seed indicators, and start at `http://127.0.0.1:8000`. `README.md` documents:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m app
```

Also document the database location, Excel export flow, safe backup by copying the database while the app is stopped, and that the project is a data-collection demo without login protection.

- [ ] **Step 5: Run page tests**

Run: `python -m pytest tests/test_pages.py -v`  
Expected: PASS.

- [ ] **Step 6: Commit the finished interface**

```powershell
git add app/static app/templates app/__main__.py README.md tests/test_pages.py
git commit -m "feat: finish responsive local demo interface"
```

## Task 10: Prove the complete demo workflow

**Files:**
- Create: `tests/test_acceptance.py`

- [ ] **Step 1: Write the end-to-end acceptance test**

```python
def test_complete_data_collection_workflow(client):
    from urllib.parse import parse_qs, urlparse

    def created_id(path, data):
        response = client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303
        return parse_qs(urlparse(response.headers["location"]).query)["created"][0]

    teacher_id = created_id("/catalog/teachers", {"name": "王老师", "role": "晚辅教师"})
    class_id = created_id("/catalog/classes", {"name": "三年级A班", "grade": "三年级", "class_type": "daily"})
    student_id = created_id("/catalog/students", {"name": "李明", "grade": "三年级", "current_stage": "三阶"})
    client.post("/catalog/enrollments", data={
        "student_id": student_id, "class_id": class_id, "start_date": "2026-09-01"
    })
    created = client.post("/sessions", data={
        "class_id": class_id, "teacher_id": teacher_id, "session_type": "daily",
        "session_date": "2026-09-05", "start_time": "16:30"
    }, follow_redirects=False)
    workspace_url = created.headers["location"]
    saved = client.post(f"{workspace_url}/daily/{student_id}", data={
        "rating_knowledge": "4", "rating_habit": "4", "rating_mindset": "5",
        "progress_indicators": ["K001", "H003", "M002"], "note": "主动性明显提升"
    }, follow_redirects=True)
    assert "1 / 1 已完成" in saved.text
    assert "主动性明显提升" in client.get(f"/history?student_id={student_id}").text
    assert client.get(f"/export.xlsx?student_id={student_id}").status_code == 200
```

Catalog create routes include the created business ID in their redirect query string as `?created=<id>`, which makes both the page confirmation banner and this acceptance test deterministic.

- [ ] **Step 2: Run the acceptance test and observe any gap**

Run: `python -m pytest tests/test_acceptance.py -v`  
Expected: PASS because Tasks 1–9 establish every contract used by this acceptance flow. If it fails, invoke the systematic-debugging skill before changing implementation.

- [ ] **Step 3: Run the complete test suite**

Run: `python -m pytest -v`  
Expected: all tests PASS with no warnings caused by application code.

- [ ] **Step 4: Verify the reference schema**

Create a fresh temporary database from `db/schema.sql`, seed it with `db/seed_indicators.sql`, then run:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT COUNT(*) FROM indicator;
```

Expected: `ok`, no foreign-key rows, and indicator count `85`.

- [ ] **Step 5: Perform the manual demo acceptance pass**

Run: `python -m app`  
Expected: the application starts at `http://127.0.0.1:8000` using `data/teaching_demo.db`.

Verify in the browser:

1. Create a teacher, class, two students, and enroll both students.
2. Create a daily session and save feedback for both students.
3. Create a special session and save one special feedback record.
4. Filter history, edit a rating, and void one record.
5. Export Excel and open all four worksheets.
6. Stop and restart the application; confirm the records remain.

- [ ] **Step 6: Record the prototype question and commit**

Add a short `Demo 试用记录` section to `README.md` containing the question: “老师是否愿意持续使用班级批量工作台录入精简反馈？” and an empty trial-results table with columns for trial date, participating teachers, average entry time, frequently selected indicators, skipped fields, and decision.

```powershell
git add README.md tests/test_acceptance.py
git commit -m "test: verify complete data collection demo"
git status --short
```

Expected: clean working tree after the final commit.
