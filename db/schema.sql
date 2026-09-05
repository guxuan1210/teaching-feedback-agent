-- =====================================================================
-- 教学反馈数据采集 Demo · 数据库 Schema（SQLite）
-- 本文件是可执行 SQLAlchemy 模型（app/*/models.py）的忠实镜像，
-- 共 10 张表。业务数据不硬删除：学生/老师/班级停用，反馈作废。
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. 教师 Teacher
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teacher (
    teacher_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status IN ('active', 'inactive'))
);

-- ---------------------------------------------------------------------
-- 2. 学生 Student
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student (
    student_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    grade         TEXT,
    current_stage TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status IN ('active', 'inactive'))
);

-- ---------------------------------------------------------------------
-- 3. 班级 Class
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS class (
    class_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    grade           TEXT,
    class_type      TEXT NOT NULL,
    head_teacher_id TEXT REFERENCES teacher(teacher_id),
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (class_type IN ('daily', 'special')),
    CHECK (status IN ('active', 'inactive'))
);

-- ---------------------------------------------------------------------
-- 4. 学生—班级关系 Enrollment（有效日期）
--    同一学生可在同一班级有多段不重叠的入班历史，故不使用
--    UNIQUE(student_id, class_id)。end_date 为空表示当前在班。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrollment (
    enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    class_id      TEXT NOT NULL REFERENCES class(class_id),
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK (status IN ('active', 'left'))
);

-- ---------------------------------------------------------------------
-- 5. 课程场次 ClassSession
--    定义「一课」：班级、老师、课程类型和日期在反馈中不重复解释。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS class_session (
    session_id   TEXT PRIMARY KEY,
    class_id     TEXT NOT NULL REFERENCES class(class_id),
    teacher_id   TEXT NOT NULL REFERENCES teacher(teacher_id),
    session_type TEXT NOT NULL,
    course_name  TEXT,
    session_date TEXT NOT NULL,
    start_time   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (session_type IN ('daily', 'special')),
    CHECK (status IN ('active', 'cancelled'))
);

-- ---------------------------------------------------------------------
-- 6. 指标字典 Indicator
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS indicator (
    indicator_id TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    text         TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_indicator_cat ON indicator(category);

-- ---------------------------------------------------------------------
-- 7. 晚辅每日反馈 DailyFeedback
--    三项评分 1-5；同一场次同一学生唯一。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_feedback (
    feedback_id      TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES class_session(session_id),
    student_id       TEXT NOT NULL REFERENCES student(student_id),
    rating_knowledge INTEGER NOT NULL,
    rating_habit     INTEGER NOT NULL,
    rating_mindset   INTEGER NOT NULL,
    note             TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (rating_knowledge BETWEEN 1 AND 5),
    CHECK (rating_habit BETWEEN 1 AND 5),
    CHECK (rating_mindset BETWEEN 1 AND 5),
    CHECK (status IN ('active', 'void')),
    UNIQUE (session_id, student_id)
);

-- ---------------------------------------------------------------------
-- 8. 专项课反馈 SpecialFeedback
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS special_feedback (
    feedback_id  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES class_session(session_id),
    student_id   TEXT NOT NULL REFERENCES student(student_id),
    rating_skill INTEGER NOT NULL,
    rating_habit INTEGER NOT NULL,
    note         TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (rating_skill BETWEEN 1 AND 5),
    CHECK (rating_habit BETWEEN 1 AND 5),
    CHECK (status IN ('active', 'void')),
    UNIQUE (session_id, student_id)
);

-- ---------------------------------------------------------------------
-- 9. 晚辅反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_feedback_indicator (
    feedback_id  TEXT NOT NULL REFERENCES daily_feedback(feedback_id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id) ON DELETE CASCADE,
    PRIMARY KEY (feedback_id, indicator_id)
);

-- ---------------------------------------------------------------------
-- 10. 专项反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS special_feedback_indicator (
    feedback_id  TEXT NOT NULL REFERENCES special_feedback(feedback_id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id) ON DELETE CASCADE,
    PRIMARY KEY (feedback_id, indicator_id)
);
