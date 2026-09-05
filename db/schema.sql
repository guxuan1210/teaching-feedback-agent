-- =====================================================================
-- 晚辅&专项课 量化反馈系统 · 数据库 Schema（SQLite）
-- 设计原则：一人一档、一课一记录、一班一视图、一次填写、自动汇总
-- 数据库不按「一学生一表 / 一班级一文件」存，而是拆成标准化记录；
-- 学生档案、班级视图都是对同一批记录的查询视图。
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. 学生表 Student
--    一名学生只建立一次基础档案；后续所有学情记录都通过 student_id 关联，
--    不依赖姓名（防重名）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student (
    student_id      TEXT PRIMARY KEY,                       -- 业务编号，如 S001
    name            TEXT NOT NULL,                          -- 姓名
    grade           TEXT,                                   -- 年级，如「三年级」
    current_stage   TEXT,                                   -- 当前九阶阶段，如「三阶」
    late_care_level TEXT,                                   -- 晚辅档位，如「固本A」
    status          TEXT NOT NULL DEFAULT '在读',            -- 在读 / 结课 / 停课
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- 2. 教师表 Teacher
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teacher (
    teacher_id  TEXT PRIMARY KEY,                           -- 业务编号，如 T001
    name        TEXT NOT NULL,
    role        TEXT,                                       -- 晚辅教师 / 专项教师 / 校区负责人 / 管理员
    phone       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- 3. 班级表 Class
--    晚辅班与专项班统一建模；class_type 区分。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS class (
    class_id        TEXT PRIMARY KEY,                       -- 业务编号，如 C001
    name            TEXT NOT NULL,                          -- 如「三年级A班」
    grade           TEXT,                                   -- 年级
    class_type      TEXT NOT NULL DEFAULT '晚辅',            -- 晚辅 / 专项
    head_teacher_id TEXT REFERENCES teacher(teacher_id),    -- 主任老师
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- 4. 学生—班级关系表 Enrollment
--    一个学生可同时参加多个班（晚辅班 + 专项班）、可换班/升班；
--    用 end_date + status 记录历史，不删除旧关系。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrollment (
    enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    class_id      TEXT NOT NULL REFERENCES class(class_id),
    start_date    TEXT,
    end_date      TEXT,                                     -- NULL = 在读
    status        TEXT NOT NULL DEFAULT '在读',              -- 在读 / 已结课 / 已转班
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(student_id, class_id)
);

-- ---------------------------------------------------------------------
-- 5. 晚辅每日学情记录 DailyFeedback
--    一次晚辅课生成一条记录；评级为 1-5 整数；
--    勾选项走 daily_feedback_indicator 多对多，不硬编码成列。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_feedback (
    feedback_id   TEXT PRIMARY KEY,                         -- 如 F20260904001
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    class_id      TEXT NOT NULL REFERENCES class(class_id),
    teacher_id    TEXT NOT NULL REFERENCES teacher(teacher_id),
    date          TEXT NOT NULL,                            -- 如 2026-09-04

    rating_knowledge INTEGER,                               -- 维度1 知识掌握 1-5
    rating_habit     INTEGER,                               -- 维度2 学习习惯 1-5
    rating_mindset   INTEGER,                               -- 维度3 心态&内驱力 1-5

    supp_knowledge   TEXT,                                  -- 知识·核心进步 补充
    supp_weak        TEXT,                                  -- 知识·待巩固 补充
    supp_error_type  TEXT,                                  -- 错题类型 补充
    supp_habit       TEXT,                                  -- 习惯·已固化 补充
    supp_habit_weak  TEXT,                                  -- 习惯·待强化 补充
    supp_habit_next  TEXT,                                  -- 次日习惯训练 补充
    supp_mindset     TEXT,                                  -- 心态·正向 补充
    supp_mindset_weak TEXT,                                 -- 心态·待引导 补充
    supp_mindset_next TEXT,                                 -- 次日心态引导 补充
    supp_highlight   TEXT,                                  -- 核心亮点 其他
    supp_home_school TEXT,                                  -- 家校配合 其他

    next_prep_knowledge TEXT,                               -- 次日备课：知识补漏
    next_prep_habit     TEXT,                               -- 次日备课：习惯强化
    next_prep_mindset   TEXT,                               -- 次日备课：心态引导

    teacher_sign    TEXT,                                   -- 老师签字
    parent_feedback TEXT,                                   -- 家长反馈/签字
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_daily_student ON daily_feedback(student_id);
CREATE INDEX IF NOT EXISTS idx_daily_class   ON daily_feedback(class_id);
CREATE INDEX IF NOT EXISTS idx_daily_date    ON daily_feedback(student_id, date);

-- ---------------------------------------------------------------------
-- 6. 专项课单次记录 SpecialFeedback
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS special_feedback (
    spec_id       TEXT PRIMARY KEY,                         -- 如 SF20260904001
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    class_id      TEXT NOT NULL REFERENCES class(class_id),
    teacher_id    TEXT NOT NULL REFERENCES teacher(teacher_id),
    course_name   TEXT,                                     -- 课程名称，如「数学思维」
    current_stage TEXT,                                     -- 匹配九阶阶段
    date          TEXT NOT NULL,

    rating_skill  INTEGER,                                  -- 维度1 专项知识/技能 1-5
    rating_habit  INTEGER,                                  -- 维度2 课堂学习习惯&状态 1-5

    supp_skill   TEXT,                                      -- 核心收获 补充
    supp_weak    TEXT,                                      -- 薄弱点 补充
    supp_good    TEXT,                                      -- 优秀表现 补充
    supp_improve TEXT,                                      -- 待改进 补充

    homework_exercise TEXT,                                 -- 课后巩固：完成专项练习（页/题）
    homework_review   TEXT,                                 -- 课后巩固：复习知识点
    homework_checkin  TEXT,                                 -- 课后巩固：打卡任务
    homework_preview  TEXT,                                 -- 课后巩固：预习
    supp_homework     TEXT,                                 -- 课后巩固 补充

    supp_next_prep  TEXT,                                   -- 下节课备课 其他
    supp_home_school TEXT,                                  -- 家校配合 其他

    teacher_sign    TEXT,
    parent_feedback TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_special_student ON special_feedback(student_id);
CREATE INDEX IF NOT EXISTS idx_special_date    ON special_feedback(student_id, date);

-- ---------------------------------------------------------------------
-- 7. 入学测评记录 Assessment + 各维度得分 AssessmentScore
--    维度可配置（扫描件《入学多维测评》正文待确认），故用键值表存维度得分。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assessment (
    assessment_id TEXT PRIMARY KEY,                         -- 如 A20260828001
    student_id    TEXT NOT NULL REFERENCES student(student_id),
    date          TEXT NOT NULL,
    stage_result  TEXT,                                     -- 测评对应九阶阶段
    total_score   TEXT,                                     -- 总分 / 综合结论
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS assessment_score (
    assessment_id TEXT NOT NULL REFERENCES assessment(assessment_id),
    dimension     TEXT NOT NULL,                            -- 维度名，如「数感」「逻辑思维」
    score         TEXT,                                     -- 得分 / 评级
    PRIMARY KEY (assessment_id, dimension)
);

-- ---------------------------------------------------------------------
-- 8. 月度学情汇总 MonthlySummary
--    月初/月末评级与成长变化可由 daily_feedback 实时计算；此处存快照 + 叙事。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monthly_summary (
    summary_id  TEXT PRIMARY KEY,                           -- 如 M202609-S001
    student_id  TEXT NOT NULL REFERENCES student(student_id),
    period      TEXT NOT NULL,                              -- 统计周期，如 2026-09
    class_id    TEXT REFERENCES class(class_id),
    teacher_id  TEXT REFERENCES teacher(teacher_id),

    rating_k_start INTEGER, rating_k_end INTEGER,           -- 知识 月初/月末
    rating_h_start INTEGER, rating_h_end INTEGER,           -- 习惯
    rating_m_start INTEGER, rating_m_end INTEGER,           -- 心态

    k_progress TEXT, k_weak TEXT, k_plan TEXT,              -- 知识维度叙事
    h_progress TEXT, h_weak TEXT, h_plan TEXT,              -- 习惯维度叙事
    m_progress TEXT, m_weak TEXT, m_plan TEXT,              -- 心态维度叙事

    late_class_advice TEXT,                                 -- 晚辅班级：维持/升班
    special_advice    TEXT,                                 -- 专项课：维持/升级/新增
    comment           TEXT,                                 -- 老师综合评语

    parent_read  TEXT,                                      -- 家长反馈：已知悉 / 有疑问
    parent_sign  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(student_id, period)
);

-- ---------------------------------------------------------------------
-- 9. 指标字典 Indicator
--    存放 Excel 中的标准勾选项；改措辞/增指标只改本表，不动其它表结构。
--    active=0 表示停用（保留历史引用）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS indicator (
    indicator_id TEXT PRIMARY KEY,                          -- 如 K001 / HW003 / SK005
    category     TEXT NOT NULL,                             -- 分类（见 seed_indicators.sql）
    text         TEXT NOT NULL,                             -- 指标文案
    sort_order   INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1                 -- 1=启用 0=停用
);
CREATE INDEX IF NOT EXISTS idx_indicator_cat ON indicator(category);

-- ---------------------------------------------------------------------
-- 10. 晚辅反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_feedback_indicator (
    feedback_id  TEXT NOT NULL REFERENCES daily_feedback(feedback_id),
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id),
    PRIMARY KEY (feedback_id, indicator_id)
);

-- ---------------------------------------------------------------------
-- 11. 专项反馈 ↔ 指标 多对多
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS special_feedback_indicator (
    spec_id      TEXT NOT NULL REFERENCES special_feedback(spec_id),
    indicator_id TEXT NOT NULL REFERENCES indicator(indicator_id),
    PRIMARY KEY (spec_id, indicator_id)
);

-- ---------------------------------------------------------------------
-- 12. 参考字典：九阶阶段 StageDict
--    一阶~九阶；语义以《九阶全周期学能成才体系》PDF 为准（扫描件待校对）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage_dict (
    stage_id   TEXT PRIMARY KEY,                            -- 一阶 / 二阶 ... 九阶
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
