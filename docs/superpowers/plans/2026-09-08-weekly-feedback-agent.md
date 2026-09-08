# 晚辅与专项课量化反馈 Agent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有教学反馈 Demo 中增加可追溯的学生成长档案、规则周报、可替换 AI 周报与老师审核定稿流程。

**Architecture:** 学生成长档案保持为基于现有反馈的只读聚合，不复制学生画像事实。周报作为派生记录持久化，并固定其来源反馈；模板与 AI 生成器共享同一个结构化上下文和校验器，任何 AI 配置或输出问题都回退到本地模板。

**Tech Stack:** Python 3.11、FastAPI、Jinja2、SQLAlchemy、SQLite、原生 JavaScript、pytest。

---

## 文件映射

```text
app/profiles/__init__.py             档案模块标记
app/profiles/service.py              档案聚合数据结构和查询
app/profiles/routes.py               档案列表与详情路由
app/templates/profiles/index.html    档案筛选与学生列表
app/templates/profiles/detail.html   学生档案详情

app/reports/__init__.py              周报模块标记
app/reports/models.py                weekly_report、weekly_report_source
app/reports/context.py               生成上下文构建
app/reports/generators.py            模板生成器和 AI 接口
app/reports/providers.py             OpenAI 兼容模型 HTTP 适配器与环境配置
app/reports/validation.py            生成结果校验
app/reports/service.py               生成、编辑、定稿和查询
app/reports/routes.py                周报中心与详情路由
app/templates/reports/index.html     周报筛选、批量生成和列表
app/templates/reports/detail.html    草稿编辑、定稿、复制和来源

app/core/database.py                 导入模型并升级到 Schema v3
app/main.py                          注册 profiles/reports 路由和生成器配置
app/templates/base.html              增加学生档案与周报中心导航
app/static/app.css                   档案、趋势和周报样式
app/static/reports.js                周报复制交互
db/schema.sql                        增加两张周报表
README.md                            双模式生成配置和使用说明
pyproject.toml                       将 httpx 加入运行时依赖

tests/profiles/test_service.py       档案聚合规则
tests/profiles/test_routes.py        档案页面与权限
tests/reports/test_models.py         周报约束和数据库升级
tests/reports/test_context.py        周报上下文
tests/reports/test_generators.py     模板、校验和 AI 回退
tests/reports/test_service.py        生命周期与批量隔离
tests/reports/test_routes.py         周报页面与权限
tests/test_weekly_report_acceptance.py 端到端流程
```

## Task 1: 学生成长档案聚合服务

**Files:**
- Create: `app/profiles/__init__.py`
- Create: `app/profiles/service.py`
- Create: `tests/profiles/test_service.py`

- [ ] **Step 1: 写档案聚合失败测试**

创建反馈夹具，覆盖两个日期、重复指标、备注及一条作废反馈。测试公开接口：

```python
from app.profiles.service import build_student_profile


def test_profile_aggregates_active_feedback_only(db_session, profile_feedback):
    profile = build_student_profile(
        db_session,
        student_id="S1",
        class_id="C1",
        date_from="2026-09-01",
        date_to="2026-09-07",
    )
    assert profile.student.name == "李明"
    assert profile.feedback_count == 2
    assert profile.daily_trends["knowledge"].start == 3
    assert profile.daily_trends["knowledge"].end == 4
    assert profile.daily_trends["knowledge"].change == 1
    assert [(item.text, item.count) for item in profile.strengths] == [
        ("主动检查", 2),
        ("按时完成", 1),
    ]
    assert "已作废备注" not in profile.recent_notes
```

并测试相同频次按 `Indicator.sort_order`、`Indicator.indicator_id` 稳定排序；空周期返回零反馈和空趋势，不抛异常。

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `python -m pytest tests/profiles/test_service.py -v`

Expected: FAIL，原因是 `app.profiles.service` 不存在。

- [ ] **Step 3: 实现档案数据结构和聚合查询**

在 `app/profiles/service.py` 定义：

```python
@dataclass(frozen=True)
class Trend:
    start: int
    end: int
    change: int


@dataclass(frozen=True)
class IndicatorFrequency:
    indicator_id: str
    text: str
    category: str
    count: int


@dataclass(frozen=True)
class ProfileSource:
    feedback_type: str
    feedback_id: str
    session_date: str
    note: str | None


@dataclass
class StudentProfile:
    student: Student
    klass: Class
    feedback_count: int
    daily_trends: dict[str, Trend]
    special_trends: dict[str, Trend]
    strengths: list[IndicatorFrequency]
    concerns: list[IndicatorFrequency]
    recent_notes: list[str]
    sources: list[ProfileSource]


def build_student_profile(
    db: Session, *, student_id: str, class_id: str,
    date_from: str, date_to: str,
) -> StudentProfile:
    ...
```

实现规则：只读取 `status="active"` 且课程日期在闭区间内的反馈；按日期和开始时间排序计算首末评分；`daily_*_progress`、`special_*_progress` 归入 strengths，`daily_*_weak`、`special_*_weak` 归入 concerns；备注按日期倒序取最多 10 条；不存在学生、班级或学生从未属于该班时抛出明确 `ValueError`。

- [ ] **Step 4: 验证档案聚合测试通过**

Run: `python -m pytest tests/profiles/test_service.py -v`

Expected: PASS。

- [ ] **Step 5: 提交档案聚合服务**

```powershell
git add app/profiles tests/profiles/test_service.py tests/conftest.py
git commit -m "feat: aggregate student growth profiles"
```

## Task 2: 学生档案页面和权限

**Files:**
- Create: `app/profiles/routes.py`
- Create: `app/templates/profiles/index.html`
- Create: `app/templates/profiles/detail.html`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/static/app.css`
- Create: `tests/profiles/test_routes.py`

- [ ] **Step 1: 写页面和权限失败测试**

```python
def test_profile_page_shows_aggregated_student_data(client, profile_feedback):
    response = client.get(
        "/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07"
    )
    assert response.status_code == 200
    assert "李明" in response.text
    assert "主动检查" in response.text
    assert "知识掌握" in response.text


def test_teacher_cannot_open_student_from_unowned_class(teacher_client, profile_feedback):
    response = teacher_client.get("/profiles/S1?class_id=C-OTHER")
    assert response.status_code == 403
```

同时断言 `/profiles` 只列出管理员全局可见或普通老师负责班级内的学生。

- [ ] **Step 2: 运行测试确认路由缺失**

Run: `python -m pytest tests/profiles/test_routes.py -v`

Expected: FAIL，返回 404。

- [ ] **Step 3: 实现档案路由**

公开路由：

```text
GET /profiles
GET /profiles/{student_id}?class_id=...&date_from=...&date_to=...
```

两条路由均依赖 `require_login`。管理员使用全部班级；普通老师使用 `list_classes_for_teacher`，并在调用聚合服务前验证 `class_id` 位于允许集合。日期默认最近 28 天，非法日期或 `date_from > date_to` 返回 422 并保留筛选值。

- [ ] **Step 4: 实现档案模板和导航**

`index.html` 提供班级、学生和日期筛选。`detail.html` 展示基础信息、反馈数量、评分首末变化、高频进步项、薄弱项、备注及来源链接。`base.html` 对所有已登录用户显示“学生档案”。趋势使用语义化表格和本地 CSS 条形指示，不引入 CDN。

- [ ] **Step 5: 验证档案页面**

Run: `python -m pytest tests/profiles -v`

Expected: PASS。

- [ ] **Step 6: 提交档案页面**

```powershell
git add app/profiles app/templates/profiles app/templates/base.html app/static/app.css app/main.py tests/profiles
git commit -m "feat: add scoped student profile pages"
```

## Task 3: 周报持久化和 Schema v3

**Files:**
- Create: `app/reports/__init__.py`
- Create: `app/reports/models.py`
- Modify: `app/core/database.py`
- Modify: `db/schema.sql`
- Create: `tests/reports/test_models.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: 写周报约束失败测试**

```python
def test_weekly_report_rejects_reversed_period(db_session, student, classroom, teacher):
    report = WeeklyReport(
        report_id="R1", student_id=student.student_id,
        class_id=classroom.class_id, teacher_id=teacher.teacher_id,
        period_start="2026-09-07", period_end="2026-09-01",
        generation_mode="template", status="draft",
        summary="总结", strengths="[]", concerns="[]", suggestions="[]",
    )
    db_session.add(report)
    with pytest.raises(IntegrityError):
        db_session.commit()
```

再测试非法生成模式、非法状态、重复来源，以及删除周报时来源通过 `ON DELETE CASCADE` 删除。

- [ ] **Step 2: 运行测试确认模型缺失**

Run: `python -m pytest tests/reports/test_models.py -v`

Expected: FAIL，原因是 `app.reports.models` 不存在。

- [ ] **Step 3: 实现两个 SQLAlchemy 模型**

`WeeklyReport` 字段严格匹配产品规格；为周期、生成模式和状态增加 `CheckConstraint`。`WeeklyReportSource` 使用 `(report_id, feedback_type, feedback_id)` 复合主键，`report_id` 外键带 `ondelete="CASCADE"`，`feedback_type` 限定为 `daily` 或 `special`。

- [ ] **Step 4: 升级数据库初始化**

把 `SCHEMA_VERSION` 从 2 改为 3，在 `_import_all_models()` 导入 `app.reports.models`。本次迁移只新增表，`Base.metadata.create_all` 创建表后再写入 `PRAGMA user_version = 3`。保留未来版本先拒绝、旧列增量迁移和字典不覆盖规则。

- [ ] **Step 5: 更新参考 Schema**

在 `db/schema.sql` 增加与模型一致的 `weekly_report` 和 `weekly_report_source`，表总数注释从 15 改为 17。扩展 `tests/test_database.py`，断言两个表名、`generation_note`、`finalized_at` 和表数量。

- [ ] **Step 6: 验证模型与升级**

Run: `python -m pytest tests/reports/test_models.py tests/test_database.py tests/catalog/test_reference_data.py -v`

Expected: PASS；旧版本测试期望的升级版本同步改为 3，未来版本测试使用 4。

- [ ] **Step 7: 提交周报持久化**

```powershell
git add app/reports app/core/database.py db/schema.sql tests/reports/test_models.py tests/test_database.py tests/catalog/test_reference_data.py
git commit -m "feat: persist traceable weekly reports"
```

## Task 4: 周报上下文和离线模板生成

**Files:**
- Create: `app/reports/context.py`
- Create: `app/reports/generators.py`
- Create: `app/reports/validation.py`
- Create: `tests/reports/test_context.py`
- Create: `tests/reports/test_generators.py`

- [ ] **Step 1: 写上下文失败测试**

```python
def test_report_context_is_stable_and_traceable(db_session, profile_feedback):
    context = build_report_context(
        db_session, student_id="S1", class_id="C1",
        period_start="2026-09-01", period_end="2026-09-07",
    )
    assert context.student_name == "李明"
    assert context.feedback_count == 2
    assert context.source_keys == [("daily", "F1"), ("daily", "F2")]
    assert context.rating_changes["knowledge"] == 1
```

断言作废反馈不进入 `source_keys`，无反馈时抛出 `ValueError("周期内没有有效反馈")`。

- [ ] **Step 2: 写模板与校验失败测试**

```python
def test_template_generator_returns_grounded_sections(report_context):
    result = TemplateReportGenerator().generate(report_context)
    validated = validate_report_output(result, report_context)
    assert validated.summary
    assert validated.strengths
    assert all(isinstance(item, str) for item in validated.suggestions)


def test_validator_rejects_unsupported_number(report_context):
    output = ReportOutput(
        summary="成绩提高了 30 分", strengths=[], concerns=[], suggestions=[]
    )
    with pytest.raises(ValueError, match="数据依据"):
        validate_report_output(output, report_context)
```

- [ ] **Step 3: 运行测试确认生成模块缺失**

Run: `python -m pytest tests/reports/test_context.py tests/reports/test_generators.py -v`

Expected: FAIL，原因是上下文和生成器模块不存在。

- [ ] **Step 4: 实现结构化上下文**

定义不可变 `ReportContext`，字段包括学生和班级名称、周期、反馈数、评分首末与变化、高频进步项、高频薄弱项、近期备注、`source_keys`。`build_report_context` 复用 `build_student_profile`，不重复实现统计 SQL。

- [ ] **Step 5: 实现规则生成器和输出校验**

定义：

```python
@dataclass(frozen=True)
class ReportOutput:
    summary: str
    strengths: list[str]
    concerns: list[str]
    suggestions: list[str]


class ReportGenerator(Protocol):
    def generate(self, context: ReportContext) -> ReportOutput: ...


class TemplateReportGenerator:
    def generate(self, context: ReportContext) -> ReportOutput: ...


def validate_report_output(
    output: ReportOutput, context: ReportContext
) -> ReportOutput:
    ...
```

模板只使用上下文中的名称、评分变化、指标和备注；每个列表最多 5 项，单项最多 120 字，总结最多 300 字。校验器拒绝空总结、错误字段类型、超长内容、人格标签和上下文未包含的明确数字。

- [ ] **Step 6: 验证上下文和模板**

Run: `python -m pytest tests/reports/test_context.py tests/reports/test_generators.py -v`

Expected: PASS。

- [ ] **Step 7: 提交生成基础**

```powershell
git add app/reports/context.py app/reports/generators.py app/reports/validation.py tests/reports
git commit -m "feat: build grounded weekly report drafts"
```

## Task 5: 周报生命周期、AI 回退和批量隔离

**Files:**
- Modify: `app/reports/generators.py`
- Create: `app/reports/providers.py`
- Create: `app/reports/service.py`
- Modify: `app/main.py`
- Modify: `pyproject.toml`
- Create: `tests/reports/test_service.py`
- Modify: `tests/reports/test_generators.py`

- [ ] **Step 1: 写生命周期和回退失败测试**

```python
def test_ai_failure_falls_back_to_template(db_session, report_scope):
    result = generate_report(
        db_session, student_id="S1", class_id="C1", teacher_id="T1",
        period_start="2026-09-01", period_end="2026-09-07",
        requested_mode="ai", ai_generator=FailingGenerator(),
    )
    assert result.generation_mode == "template"
    assert "AI 生成失败" in result.generation_note
    assert {(s.feedback_type, s.feedback_id) for s in result.sources} == {
        ("daily", "F1"), ("daily", "F2")
    }


def test_only_one_finalized_report_per_scope(db_session, two_report_drafts):
    finalize_report(db_session, two_report_drafts[0].report_id, teacher_id="T1")
    with pytest.raises(ValueError, match="已有定稿"):
        finalize_report(db_session, two_report_drafts[1].report_id, teacher_id="T1")
```

再测试编辑定稿被拒绝、来源不存在时整个生成事务回滚，以及批量生成中一个无反馈学生失败但其他学生成功。

- [ ] **Step 2: 运行测试确认服务缺失**

Run: `python -m pytest tests/reports/test_service.py -v`

Expected: FAIL，原因是 `app.reports.service` 不存在。

- [ ] **Step 3: 实现可替换 AI 适配器**

`AIReportGenerator` 只依赖注入的同步调用函数：

```python
class AIReportGenerator:
    def __init__(self, invoke: Callable[[dict], dict]) -> None:
        self._invoke = invoke

    def generate(self, context: ReportContext) -> ReportOutput:
        payload = context.to_prompt_payload()
        raw = self._invoke(payload)
        return ReportOutput.from_mapping(raw)
```

在 `providers.py` 实现 OpenAI 兼容 HTTP 适配器。读取三个环境变量：`REPORT_AI_BASE_URL`、`REPORT_AI_API_KEY`、`REPORT_AI_MODEL`；任一缺失时返回 `None`。使用 `httpx.Client(timeout=15.0)` 请求 `{base_url}/chat/completions`，要求 JSON 对象响应，并把 `choices[0].message.content` 解析为字典。把 `httpx` 从开发依赖移入 `project.dependencies`。

`create_app` 接受可选 `ai_report_generator` 覆盖；没有显式覆盖时调用 `build_ai_generator_from_env()`。HTTP 测试使用 `httpx.MockTransport`，不得访问外部网络。未配置环境变量时视为未配置并回退模板。

- [ ] **Step 4: 实现周报服务**

公开接口：

```python
def generate_report(db: Session, *, student_id: str, class_id: str,
                    teacher_id: str, period_start: str, period_end: str,
                    requested_mode: str,
                    ai_generator: ReportGenerator | None) -> WeeklyReport: ...

def generate_reports_for_class(db: Session, *, class_id: str,
                               teacher_id: str, student_ids: list[str],
                               period_start: str, period_end: str,
                               requested_mode: str,
                               ai_generator: ReportGenerator | None
                               ) -> BatchGenerationResult: ...

def update_report_draft(db: Session, report_id: str, *, summary: str,
                        strengths: list[str], concerns: list[str],
                        suggestions: list[str]) -> WeeklyReport: ...

def finalize_report(db: Session, report_id: str, *, teacher_id: str) -> WeeklyReport: ...
```

`generate_report` 在同一事务内保存周报和来源；AI 异常或校验失败时使用模板并记录 `generation_note`。批量接口逐学生调用独立事务并返回 `created` 与 `errors`，不让单个失败中断其他学生。`update_report_draft` 拒绝定稿记录；`finalize_report` 使用同一学生、班级、周期查询阻止第二份定稿。

- [ ] **Step 5: 验证服务和回退**

Run: `python -m pytest tests/reports/test_generators.py tests/reports/test_service.py -v`

Expected: PASS。

- [ ] **Step 6: 提交生命周期服务**

```powershell
git add app/reports app/main.py tests/reports
git commit -m "feat: manage weekly report generation lifecycle"
```

## Task 6: 周报中心页面、编辑、定稿和复制

**Files:**
- Create: `app/reports/routes.py`
- Create: `app/templates/reports/index.html`
- Create: `app/templates/reports/detail.html`
- Create: `app/static/reports.js`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/static/app.css`
- Create: `tests/reports/test_routes.py`

- [ ] **Step 1: 写周报页面失败测试**

```python
def test_generate_template_report_from_center(client, report_scope):
    response = client.post("/reports/generate", data={
        "class_id": "C1", "student_ids": ["S1"],
        "period_start": "2026-09-01", "period_end": "2026-09-07",
        "generation_mode": "template",
    }, follow_redirects=False)
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert "周报草稿" in detail.text
    assert "查看来源反馈" in detail.text


def test_finalize_report_blocks_further_edits(client, report_draft):
    finalized = client.post(f"/reports/{report_draft.report_id}/finalize")
    assert finalized.status_code == 303
    edited = client.post(f"/reports/{report_draft.report_id}", data={
        "summary": "再次修改", "strengths": "[]",
        "concerns": "[]", "suggestions": "[]",
    })
    assert edited.status_code == 409
```

增加普通老师跨班访问列表、详情、生成和定稿均返回 403 的测试；增加 AI 回退提示可见测试。

- [ ] **Step 2: 运行测试确认路由缺失**

Run: `python -m pytest tests/reports/test_routes.py -v`

Expected: FAIL，返回 404。

- [ ] **Step 3: 实现周报路由和权限**

公开路由：

```text
GET  /reports
POST /reports/generate
GET  /reports/{report_id}
POST /reports/{report_id}
POST /reports/{report_id}/finalize
```

所有路由依赖 `require_login`。普通老师的班级范围使用 `list_classes_for_teacher`；详情、编辑和定稿根据周报 `class_id` 再做服务端校验。表单日期非法、周期倒置、模式非法或学生不属于班级时返回 422 并保留输入。

- [ ] **Step 4: 实现周报模板和复制交互**

`index.html` 展示筛选表单、学生反馈数量、复选框、批量生成按钮、生成错误及历史周报。`detail.html` 对草稿显示四段编辑表单和定稿按钮，对定稿显示只读正文；所有状态显示实际生成模式和回退说明。来源链接使用 `/history/{feedback_type}/{feedback_id}`。

`reports.js` 只读取页面内的周报正文并调用 `navigator.clipboard.writeText`；失败时选中文本并提示用户手动复制，不发送网络请求。

- [ ] **Step 5: 验证周报页面**

Run: `python -m pytest tests/reports/test_routes.py tests/test_pages.py -v`

Expected: PASS，页面只引用本地静态资源。

- [ ] **Step 6: 提交周报页面**

```powershell
git add app/reports/routes.py app/templates/reports app/static/reports.js app/static/app.css app/templates/base.html app/main.py tests/reports/test_routes.py tests/test_pages.py
git commit -m "feat: add teacher-reviewed weekly report center"
```

## Task 7: 验收流程、文档与完整验证

**Files:**
- Create: `tests/test_weekly_report_acceptance.py`
- Modify: `README.md`
- Modify: `docs/架构与数据模型设计.md`

- [ ] **Step 1: 写端到端验收测试**

```python
def test_feedback_to_profile_and_weekly_report(client, daily_session_one_student):
    client.post("/sessions/SESSION1/daily/S1", data={
        "rating_knowledge": "4", "rating_habit": "4",
        "rating_mindset": "5", "progress_indicators": ["K001"],
        "note": "能够主动检查作业",
    })
    profile = client.get(
        "/profiles/S1?class_id=C1&date_from=2026-09-01&date_to=2026-09-07"
    )
    assert "能够主动检查作业" in profile.text

    generated = client.post("/reports/generate", data={
        "class_id": "C1", "student_ids": ["S1"],
        "period_start": "2026-09-01", "period_end": "2026-09-07",
        "generation_mode": "template",
    }, follow_redirects=False)
    assert generated.status_code == 303
    detail = client.get(generated.headers["location"])
    assert "能够主动检查" in detail.text
    assert "F" in detail.text
```

- [ ] **Step 2: 运行验收测试并修复集成缺口**

Run: `python -m pytest tests/test_weekly_report_acceptance.py -v`

Expected: PASS。若失败，先使用 systematic-debugging 定位，再以失败测试驱动最小修复。

- [ ] **Step 3: 更新运行和配置文档**

README 增加学生档案、周报中心和规则模式说明，并记录 `REPORT_AI_BASE_URL`、`REPORT_AI_API_KEY`、`REPORT_AI_MODEL` 三个可选环境变量。明确模型服务必须兼容 OpenAI Chat Completions JSON 接口，未完整配置或调用失败时自动使用本地规则模板。架构文档把两张周报表加入现行模型，把自动周报从未来能力移入已实现能力。

- [ ] **Step 4: 运行完整测试套件**

Run: `python -m pytest -q`

Expected: 原有 44 项及所有新增测试全部 PASS，0 failures。

- [ ] **Step 5: 独立验证参考 Schema**

使用 Python `sqlite3` 在临时数据库执行 `db/schema.sql`，验证：

```text
PRAGMA integrity_check = ok
PRAGMA foreign_key_check = []
非内部表数量 = 17
weekly_report 和 weekly_report_source 存在
```

- [ ] **Step 6: 检查差异和敏感信息**

Run: `git diff --check`

Run: `rg -n "api[_-]?key|secret|password" app README.md -g '!app/core/security.py'`

Expected: 无空白错误；没有真实模型密钥进入代码或文档。

- [ ] **Step 7: 提交验收与文档**

```powershell
git add tests/test_weekly_report_acceptance.py README.md docs/架构与数据模型设计.md
git commit -m "test: verify weekly feedback agent workflow"
```

## 完成检查

- 学生档案只聚合现有有效反馈，没有重复画像事实表。
- 周报固定来源，作废反馈不进入新周报。
- 模板模式完全离线可用。
- AI 接口可替换，异常和不合规输出自动回退。
- 老师必须审核；系统没有自动发送能力。
- 普通老师的数据范围在列表和详情两层校验。
- 全量测试、Schema 完整性和敏感信息检查通过。
