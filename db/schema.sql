-- =====================================================================
-- 教学反馈数据采集 Demo · 数据库 Schema（SQLite）
-- 本文件是可执行 SQLAlchemy 模型（app/*/models.py）的忠实镜像，
-- Schema version 10，共 34 张表。业务数据不硬删除：学生/老师/班级停用，反馈作废。
-- v8 升级时既有 guardian_invitation.max_uses 保持原列定义；服务始终显式写入 1。
-- v8 重复 active 教师关系全部保留；按 start_date、assignment_id 最早者继续 active，其余撤销。
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. 教师 Teacher
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teacher (
    teacher_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT,
    password_hash TEXT,
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
    late_care_level TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status IN ('active', 'inactive'))
);

-- ---------------------------------------------------------------------
-- 3. 九阶阶段字典
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage_dict (
    stage_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    summary    TEXT NOT NULL,
    goal       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- 4. 晚辅档位字典
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS late_care_level_dict (
    level_id   TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- 5. 入学测评模块、维度与评分锚点
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assessment_module (
    module_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS assessment_dimension (
    dimension_id TEXT PRIMARY KEY,
    module_id    TEXT NOT NULL REFERENCES assessment_module(module_id),
    name         TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS assessment_score_anchor (
    dimension_id TEXT NOT NULL REFERENCES assessment_dimension(dimension_id),
    score        INTEGER NOT NULL,
    description  TEXT NOT NULL,
    PRIMARY KEY (dimension_id, score),
    CHECK (score BETWEEN 0 AND 5)
);

-- ---------------------------------------------------------------------
-- 6. 班级 Class
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
-- 7. 学生—班级关系 Enrollment（有效日期）
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
-- 8. 课程场次 ClassSession
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
-- 9. 指标字典 Indicator
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
-- 10. 晚辅每日反馈 DailyFeedback
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
-- 11. 专项课反馈 SpecialFeedback
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
-- 12. 晚辅反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_feedback_indicator (
    feedback_id  TEXT NOT NULL REFERENCES daily_feedback(feedback_id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id) ON DELETE CASCADE,
    PRIMARY KEY (feedback_id, indicator_id)
);

-- ---------------------------------------------------------------------
-- 13. 专项反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS special_feedback_indicator (
    feedback_id  TEXT NOT NULL REFERENCES special_feedback(feedback_id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id) ON DELETE CASCADE,
    PRIMARY KEY (feedback_id, indicator_id)
);

-- ---------------------------------------------------------------------
-- 14. 周报 WeeklyReport
--     派生的学生成长记录，固定其来源反馈；生成模式可替换、老师审核定稿。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_report (
    report_id       TEXT PRIMARY KEY,
    student_id      TEXT NOT NULL REFERENCES student(student_id),
    class_id        TEXT NOT NULL REFERENCES class(class_id),
    teacher_id      TEXT NOT NULL REFERENCES teacher(teacher_id),
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    generation_mode TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    parent_message  TEXT NOT NULL,
    summary         TEXT NOT NULL,
    strengths       TEXT NOT NULL,
    concerns        TEXT NOT NULL,
    suggestions     TEXT NOT NULL,
    generation_note TEXT,
    finalized_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (period_end >= period_start),
    CHECK (generation_mode IN ('template', 'ai')),
    CHECK (status IN ('draft', 'finalized'))
);

-- ---------------------------------------------------------------------
-- 15. 周报 ↔ 来源反馈 多对多（feedback_id 复用 daily/special 的 id）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_report_source (
    report_id     TEXT NOT NULL REFERENCES weekly_report(report_id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL,
    feedback_id   TEXT NOT NULL,
    PRIMARY KEY (report_id, feedback_type, feedback_id),
    CHECK (feedback_type IN ('daily', 'special'))
);

-- ---------------------------------------------------------------------
-- 16-18. 教学助手对话、消息与回答来源
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_conversation (
    conversation_id TEXT PRIMARY KEY,
    owner_teacher_id TEXT NOT NULL REFERENCES teacher(teacher_id),
    scope_type       TEXT NOT NULL,
    class_id         TEXT NOT NULL REFERENCES class(class_id),
    student_id       TEXT REFERENCES student(student_id),
    title            TEXT NOT NULL,
    date_from        TEXT NOT NULL,
    date_to          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    last_message_at  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (scope_type IN ('student', 'class')),
    CHECK (date_to >= date_from),
    CHECK (status IN ('active', 'archived')),
    CHECK ((scope_type = 'student' AND student_id IS NOT NULL)
        OR (scope_type = 'class' AND student_id IS NULL))
);

CREATE TABLE IF NOT EXISTS chat_message (
    message_id            TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL REFERENCES chat_conversation(conversation_id) ON DELETE CASCADE,
    role                  TEXT NOT NULL,
    content               TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'completed',
    model                 TEXT,
    channel               TEXT,
    context_date_from     TEXT,
    context_date_to       TEXT,
    context_snapshot      TEXT,
    error_message         TEXT,
    created_at            TEXT NOT NULL,
    CHECK (role IN ('user', 'assistant')),
    CHECK (status IN ('completed', 'failed')),
    CHECK (channel IS NULL OR channel IN ('web', 'wecom'))
);

CREATE TABLE IF NOT EXISTS chat_message_source (
    message_id  TEXT NOT NULL REFERENCES chat_message(message_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    cited       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (message_id, source_type, source_id),
    CHECK (source_type IN ('daily', 'special', 'weekly_report')),
    CHECK (cited IN (0, 1))
);

CREATE TABLE IF NOT EXISTS teacher_chat_state (
    teacher_id             TEXT PRIMARY KEY REFERENCES teacher(teacher_id) ON DELETE CASCADE,
    current_conversation_id TEXT REFERENCES chat_conversation(conversation_id) ON DELETE SET NULL,
    updated_at             TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 19-22. 企业微信绑定、会话状态与入站消息去重
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teacher_wecom_binding (
    wecom_user_id TEXT PRIMARY KEY,
    teacher_id    TEXT NOT NULL UNIQUE REFERENCES teacher(teacher_id) ON DELETE CASCADE,
    bound_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wecom_binding_code (
    code_id         TEXT PRIMARY KEY,
    code_hash       TEXT NOT NULL UNIQUE,
    teacher_id      TEXT NOT NULL REFERENCES teacher(teacher_id) ON DELETE CASCADE,
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wecom_chat_state (
    wecom_user_id         TEXT PRIMARY KEY REFERENCES teacher_wecom_binding(wecom_user_id) ON DELETE CASCADE,
    conversation_id       TEXT REFERENCES chat_conversation(conversation_id) ON DELETE SET NULL,
    scope_type            TEXT,
    class_id              TEXT REFERENCES class(class_id),
    student_id            TEXT REFERENCES student(student_id),
    date_from             TEXT,
    date_to               TEXT,
    pending_scope_json    TEXT,
    pending_question      TEXT,
    last_failed_message_id TEXT,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wecom_inbound_message (
    message_id    TEXT PRIMARY KEY,
    wecom_user_id TEXT NOT NULL,
    status        TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    completed_at  TEXT
);

-- ---------------------------------------------------------------------
-- 26-29. 家长、学生关系、渠道绑定与邀请码
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS guardian (
    guardian_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (relationship_type IN ('father', 'mother', 'other')),
    CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS student_guardian (
    relation_id TEXT PRIMARY KEY,
    student_id  TEXT NOT NULL REFERENCES student(student_id),
    guardian_id TEXT NOT NULL REFERENCES guardian(guardian_id),
    status      TEXT NOT NULL DEFAULT 'active',
    bound_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    revoked_at  TEXT,
    UNIQUE (student_id, guardian_id),
    CHECK (status IN ('active', 'revoked'))
);
CREATE INDEX IF NOT EXISTS ix_student_guardian_guardian_status
    ON student_guardian(guardian_id, status);

CREATE TABLE IF NOT EXISTS guardian_channel_binding (
    binding_id        TEXT PRIMARY KEY,
    channel           TEXT NOT NULL,
    external_user_id  TEXT NOT NULL,
    guardian_id       TEXT REFERENCES guardian(guardian_id),
    active_student_id TEXT REFERENCES student(student_id) ON DELETE SET NULL,
    pending_state_json TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    bound_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (channel, external_user_id),
    CHECK (status IN ('pending', 'active', 'revoked')),
    CHECK (status != 'active' OR guardian_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS guardian_invitation (
    invitation_id        TEXT PRIMARY KEY,
    student_id           TEXT NOT NULL REFERENCES student(student_id),
    code_hash            TEXT NOT NULL UNIQUE,
    expires_at           TEXT NOT NULL,
    max_uses             INTEGER NOT NULL DEFAULT 1,
    used_count           INTEGER NOT NULL DEFAULT 0,
    revoked_at           TEXT,
    created_by_teacher_id TEXT NOT NULL REFERENCES teacher(teacher_id),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (max_uses > 0),
    CHECK (used_count >= 0),
    CHECK (used_count <= max_uses)
);

-- ---------------------------------------------------------------------
-- 30-32. 学生教师分配、图片与待分配媒体
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student_teacher_assignment (
    assignment_id TEXT PRIMARY KEY,
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    teacher_id    TEXT NOT NULL REFERENCES teacher(teacher_id),
    role          TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    origin        TEXT NOT NULL DEFAULT 'manual',
    source_enrollment_id INTEGER REFERENCES enrollment(enrollment_id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (role IN ('primary', 'subject', 'collaborator')),
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK (status IN ('active', 'revoked')),
    CHECK ((origin = 'manual' AND source_enrollment_id IS NULL)
        OR (origin = 'class_sync' AND source_enrollment_id IS NOT NULL AND role = 'primary'))
);
CREATE INDEX IF NOT EXISTS ix_student_teacher_assignment_lookup
    ON student_teacher_assignment(teacher_id, student_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_student_teacher_active_role
    ON student_teacher_assignment(student_id, teacher_id, role) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS student_image (
    image_id               TEXT PRIMARY KEY,
    student_id             TEXT NOT NULL REFERENCES student(student_id),
    uploaded_by_teacher_id TEXT NOT NULL REFERENCES teacher(teacher_id),
    source_message_id      TEXT NOT NULL,
    source_position        INTEGER NOT NULL,
    caption                TEXT,
    mime_type              TEXT NOT NULL,
    extension              TEXT NOT NULL,
    byte_size              INTEGER NOT NULL,
    sha256                 TEXT NOT NULL,
    storage_path           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'active',
    uploaded_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at             TEXT,
    deleted_by_teacher_id  TEXT REFERENCES teacher(teacher_id),
    quarantine_path TEXT,
    UNIQUE (source_message_id, source_position),
    CHECK (status IN ('active', 'deleted')),
    CHECK ((status = 'active' AND deleted_at IS NULL AND deleted_by_teacher_id IS NULL AND quarantine_path IS NULL)
        OR (status = 'deleted' AND deleted_at IS NOT NULL AND deleted_by_teacher_id IS NOT NULL AND quarantine_path IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_student_image_student_status_uploaded
    ON student_image(student_id, status, uploaded_at);

CREATE TABLE IF NOT EXISTS pending_media_assignment (
    pending_id          TEXT PRIMARY KEY,
    teacher_id         TEXT NOT NULL REFERENCES teacher(teacher_id),
    source_message_id   TEXT NOT NULL UNIQUE,
    caption             TEXT,
    media_json          TEXT NOT NULL,
    choices_json        TEXT NOT NULL,
    temporary_paths_json TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status IN ('pending', 'completed', 'expired'))
);

-- ---------------------------------------------------------------------
-- 33-34. 家校会话与消息
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_conversation (
    conversation_id        TEXT PRIMARY KEY,
    guardian_id            TEXT NOT NULL REFERENCES guardian(guardian_id),
    student_id             TEXT NOT NULL REFERENCES student(student_id),
    assigned_teacher_id    TEXT REFERENCES teacher(teacher_id) ON DELETE SET NULL,
    channel                TEXT NOT NULL,
    channel_conversation_id TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'active',
    teacher_notification_status TEXT,
    teacher_notification_error TEXT,
    last_message_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (guardian_id, student_id, channel, channel_conversation_id),
    CHECK (status IN ('active', 'waiting_teacher', 'closed'))
);

CREATE TABLE IF NOT EXISTS family_message (
    message_id         TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES family_conversation(conversation_id),
    direction          TEXT NOT NULL,
    sender_type        TEXT NOT NULL,
    sender_id          TEXT,
    content            TEXT NOT NULL,
    image_id           TEXT REFERENCES student_image(image_id) ON DELETE SET NULL,
      channel_message_id TEXT UNIQUE,
      reply_to_message_id TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',
    failure_reason     TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at            TEXT,
    CHECK (direction IN ('inbound', 'outbound')),
    CHECK (sender_type IN ('guardian', 'bot', 'teacher')),
    CHECK (status IN ('pending', 'completed', 'failed')),
    CHECK ((direction = 'inbound' AND sender_type = 'guardian')
        OR (direction = 'outbound' AND sender_type IN ('bot', 'teacher'))),
    CHECK ((status = 'failed' AND failure_reason IS NOT NULL)
        OR (status != 'failed' AND failure_reason IS NULL)),
    CHECK (direction != 'outbound' OR status != 'completed' OR sent_at IS NOT NULL),
    CHECK (status != 'pending' OR sent_at IS NULL),
    CHECK ((sender_type = 'bot' AND sender_id IS NULL)
        OR (sender_type IN ('guardian', 'teacher') AND sender_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_family_message_conversation_created
    ON family_message(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_family_message_reply_to_message_id
    ON family_message(reply_to_message_id);
