# 登录权限与参考字典一致性收尾实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让登录权限和参考字典扩展在 SQLAlchemy 模型、数据库升级、参考 Schema、项目文档与测试中保持一致。

**Architecture:** 以 SQLAlchemy 模型作为可执行结构基准，以 `SCHEMA_VERSION = 2` 的增量迁移兼容旧数据库，并让 `db/schema.sql` 独立创建同构的新数据库。文档只描述当前已实现能力，把入学测评业务记录和月度汇总明确保留在未来范围。

**Tech Stack:** Python 3.11、SQLAlchemy、SQLite、pytest、Markdown。

---

## 文件映射

```text
tests/test_database.py                         参考 Schema 字段镜像测试
tests/catalog/test_reference_data.py          版本 1 → 2 数据升级回归测试
db/schema.sql                                 全新数据库的 15 表参考结构
docs/架构与数据模型设计.md                    当前架构、数据模型和未来范围
docs/superpowers/specs/2026-09-05-data-collection-demo-design.md
                                               旧版首期规格的后续实现说明
README.md                                      当前能力与安全边界说明
```

### Task 1: 用失败测试锁定 Schema 和升级兼容要求

**Files:**
- Modify: `tests/test_database.py`
- Modify: `tests/catalog/test_reference_data.py`

- [ ] **Step 1: 扩展参考 Schema 镜像测试**

将现有测试改为：

```python
def test_schema_sql_mirrors_current_model_fields():
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    assert "password_hash" in schema
    assert "late_care_level TEXT" in schema
    for table in (
        "stage_dict", "late_care_level_dict", "assessment_module",
        "assessment_dimension", "assessment_score_anchor",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `python -m pytest tests/test_database.py::test_schema_sql_mirrors_current_model_fields -v`

Expected: FAIL，原因是 `db/schema.sql` 缺少 `password_hash`。

- [ ] **Step 3: 扩展版本 1 升级测试**

在旧库中创建不含密码列的教师表和教师记录：

```python
connection.exec_driver_sql(
    "CREATE TABLE teacher (teacher_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
    "role TEXT, status TEXT NOT NULL DEFAULT 'active', "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
connection.exec_driver_sql(
    "INSERT INTO teacher VALUES "
    "('T-LEGACY','旧教师','晚辅教师','active','old','old')"
)
```

升级后断言：

```python
teacher_columns = {
    row[1] for row in connection.exec_driver_sql("PRAGMA table_info(teacher)")
}
assert "password_hash" in teacher_columns
assert connection.exec_driver_sql(
    "SELECT name FROM teacher WHERE teacher_id='T-LEGACY'"
).scalar_one() == "旧教师"
```

- [ ] **Step 4: 验证现有增量迁移满足兼容要求**

Run: `python -m pytest tests/catalog/test_reference_data.py::test_v1_database_upgrades_without_rewriting_legacy_stage -v`

Expected: PASS；该测试补足已有迁移逻辑的覆盖，无需新增生产迁移代码。

### Task 2: 修正全新数据库参考 Schema

**Files:**
- Modify: `db/schema.sql`
- Test: `tests/test_database.py`

- [ ] **Step 1: 在教师表加入可空密码哈希**

在 `role` 与 `status` 之间加入：

```sql
password_hash TEXT,
```

- [ ] **Step 2: 运行 Schema 测试**

Run: `python -m pytest tests/test_database.py -v`

Expected: 全部 PASS。

- [ ] **Step 3: 独立验证参考 Schema**

用 Python `sqlite3` 在临时文件执行 `db/schema.sql`，然后断言：

```text
PRAGMA integrity_check = ok
PRAGMA foreign_key_check = []
非内部表数量 = 15
teacher 字段包含 password_hash
```

### Task 3: 让文档区分当前表与未来能力

**Files:**
- Modify: `docs/架构与数据模型设计.md`
- Modify: `docs/superpowers/specs/2026-09-05-data-collection-demo-design.md`
- Modify: `README.md`

- [ ] **Step 1: 增加当前实现状态**

在架构文档开头增加：

```markdown
## 当前实现状态（2026-09-06）

当前应用已经实现登录与角色范围控制、基础信息管理、课程场次、晚辅/专项反馈、历史记录和 Excel 导出。数据库包含 15 张现行表；入学测评目前只提供模块、维度和评分锚点字典，尚无测评记录表和录入页面；月度汇总尚未实现。
```

- [ ] **Step 2: 修正模型和权限描述**

把 ER 图和查询说明中的 `assessment`、`assessment_score`、`monthly_summary` 移到未来能力说明。在教师字段表加入：

```markdown
| password_hash | TEXT | 可空 | PBKDF2-SHA256 密码哈希；为空时不能登录 |
```

权限说明必须反映当前实现：管理员和校区负责人可管理全局数据；普通教师只能访问自己负责的班级、自己创建的课程场次及对应历史和导出。

- [ ] **Step 3: 标注旧版规格边界**

在旧规格状态后增加：

```markdown
> 后续状态说明（2026-09-06）：首期规格已完成；登录和角色范围控制已作为后续扩展实现。因此下文“不包含登录、账号与权限控制”只描述首期交付边界，不代表当前应用状态。
```

- [ ] **Step 4: 校准 README 安全说明**

说明 `password_hash` 为空的教师不能登录，并保留默认密码、默认会话密钥不适合生产部署的警告。

- [ ] **Step 5: 检索关键术语**

Run: `rg -n "assessment_score|monthly_summary|password_hash|当前实现状态|后续状态说明" README.md docs db/schema.sql`

Expected: `password_hash` 出现在 Schema 和教师字段说明；未来表只出现在来源映射或未来规划语境。

### Task 4: 完整回归与交付检查

**Files:**
- Verify: all changed files

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -q`

Expected: 44 个以上测试全部 PASS，0 failures。

- [ ] **Step 2: 检查差异范围**

Run: `git diff --check`

Run: `git status --short`

Expected: 差异检查无错误；状态中没有缓存或临时数据库文件被误加入版本控制。

- [ ] **Step 3: 汇总结果**

报告测试数量、Schema 独立验证结果、修改文件和仍未实现的未来能力。不要覆盖用户进入本轮前已有的其他未提交改动。

