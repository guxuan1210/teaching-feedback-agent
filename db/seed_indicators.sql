-- =====================================================================
-- 指标字典种子数据 + 参考字典
-- 全部勾选项逐字提取自《晚辅&专项课_量化反馈表全套-1.xlsx》
-- 共 85 条：晚辅 55 条 + 专项 30 条
--
-- 指标 ID 规则：
--   晚辅：K=知识  H=习惯  M=心态  （进步类用主前缀，其余加后缀 W=薄弱/待改、E=错题、N=次日）
--   专项：SK=专项知识  SH=专项习惯（SW=课后巩固  SNP=备课重点  SHS=家校配合）
-- 可重复执行（INSERT OR IGNORE，仅补齐缺失 ID，不覆盖已有行）。
-- =====================================================================

-- ---------------- 晚辅课：维度1 知识掌握 ----------------

-- 核心进步/已掌握项
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('K001','daily_k_progress','校内当日知识点100%掌握',1,1),
('K002','daily_k_progress','作业正确率较昨日提升',2,1),
('K003','daily_k_progress','独立完成订正，无二次错误',3,1),
('K004','daily_k_progress','预习/复习任务完整落实',4,1),
('K005','daily_k_progress','能独立讲解当日核心例题',5,1);

-- 待巩固/薄弱项
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('KW001','daily_k_weak','基础概念理解不透彻',1,1),
('KW002','daily_k_weak','计算粗心，高频出错',2,1),
('KW003','daily_k_weak','知识点不会灵活运用',3,1),
('KW004','daily_k_weak','同类题目反复出错',4,1),
('KW005','daily_k_weak','拓展题完全无思路',5,1);

-- 当日错题核心类型
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('KE001','daily_k_error','审题失误（看错条件/问法）',1,1),
('KE002','daily_k_error','计算类错误',2,1),
('KE003','daily_k_error','概念混淆类错误',3,1),
('KE004','daily_k_error','知识点遗忘类错误',4,1),
('KE005','daily_k_error','答题不规范/步骤缺失',5,1);

-- ---------------- 晚辅课：维度2 学习习惯 ----------------

-- 已固化/进步习惯
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('H001','daily_h_progress','桌面/书包物品规整到位',1,1),
('H002','daily_h_progress','作业计时管理，效率提升',2,1),
('H003','daily_h_progress','审题主动圈画关键信息',3,1),
('H004','daily_h_progress','书写规范，卷面整洁无涂改',4,1),
('H005','daily_h_progress','做完主动自检，独立订正',5,1),
('H006','daily_h_progress','主动做笔记/整理错题',6,1);

-- 待强化习惯
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('HW001','daily_h_weak','丢三落四，作业/书本遗漏',1,1),
('HW002','daily_h_weak','作业拖拉，耗时超预期',2,1),
('HW003','daily_h_weak','审题不仔细，凭感觉做题',3,1),
('HW004','daily_h_weak','字迹潦草，涂改频繁',4,1),
('HW005','daily_h_weak','依赖老师/家长检查，不自检',5,1),
('HW006','daily_h_weak','错题不整理，不复盘',6,1);

-- 次日习惯训练重点
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('HN001','daily_h_next','物品收纳专项训练',1,1),
('HN002','daily_h_next','作业计时管控训练',2,1),
('HN003','daily_h_next','审题圈画专项训练',3,1),
('HN004','daily_h_next','书写规范专项纠正',4,1),
('HN005','daily_h_next','独立自检习惯培养',5,1),
('HN006','daily_h_next','错题整理方法指导',6,1);

-- ---------------- 晚辅课：维度3 心态&学习内驱力 ----------------

-- 正向成长表现
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('M001','daily_m_progress','全程专注，无走神/小动作',1,1),
('M002','daily_m_progress','遇到难题不放弃，主动尝试',2,1),
('M003','daily_m_progress','主动提问，敢于表达想法',3,1),
('M004','daily_m_progress','学习情绪稳定，配合度高',4,1),
('M005','daily_m_progress','有自主学习意识，主动安排任务',5,1);

-- 待引导调整项
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('MW001','daily_m_weak','上课/写作业容易走神',1,1),
('MW002','daily_m_weak','遇难题就畏难，直接放弃',2,1),
('MW003','daily_m_weak','不敢提问，不懂装懂',3,1),
('MW004','daily_m_weak','情绪浮躁，配合度差',4,1),
('MW005','daily_m_weak','学习被动，不停催促',5,1);

-- 次日心态引导方向
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('MN001','daily_m_next','专注力专项引导',1,1),
('MN002','daily_m_next','抗挫心态引导',2,1),
('MN003','daily_m_next','主动表达鼓励引导',3,1),
('MN004','daily_m_next','情绪稳定性引导',4,1),
('MN005','daily_m_next','自主内驱力引导',5,1);

-- ---------------- 晚辅课：核心闭环模块 ----------------

-- 当日核心亮点总结
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('HL001','daily_highlight','知识掌握扎实，进步明显',1,1),
('HL002','daily_highlight','学习习惯有突破性改善',2,1),
('HL003','daily_highlight','心态状态积极，主动性提升',3,1),
('HL004','daily_highlight','综合表现稳定，持续向好',4,1);

-- 家校配合建议
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('HS001','daily_home_school','在家同步督促孩子独立完成作业，不陪写',1,1),
('HS002','daily_home_school','在家引导孩子整理错题，复盘当日内容',2,1),
('HS003','daily_home_school','多鼓励孩子，减少负面催促',3,1);

-- ---------------- 专项课：维度1 专项知识/技能掌握 ----------------

-- 本节课核心收获/已掌握项
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SK001','special_k_progress','掌握本节课核心知识点/技能',1,1),
('SK002','special_k_progress','专项题型解题思路完全掌握，能独立讲解',2,1),
('SK003','special_k_progress','完成专项能力突破',3,1),
('SK004','special_k_progress','达到本节课设定的全部学习目标',4,1),
('SK005','special_k_progress','能举一反三，灵活运用所学内容',5,1);

-- 薄弱点/易错点
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SKW001','special_k_weak','核心概念理解不透彻，不会运用',1,1),
('SKW002','special_k_weak','同类题目反复出错，未掌握核心方法',2,1),
('SKW003','special_k_weak','细节把控不到位，高频失分',3,1),
('SKW004','special_k_weak','未掌握基础方法，无法独立完成练习',4,1),
('SKW005','special_k_weak','对重难点内容完全无思路',5,1);

-- ---------------- 专项课：维度2 课堂学习习惯&状态 ----------------

-- 课堂优秀表现
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SH001','special_h_progress','全程专注，紧跟课堂节奏，主动思考',1,1),
('SH002','special_h_progress','敢于提问，主动表达自己的想法',2,1),
('SH003','special_h_progress','课堂练习执行力强，按时按质完成',3,1),
('SH004','special_h_progress','主动复盘错题，及时纠正问题',4,1),
('SH005','special_h_progress','学习心态积极，遇到难题主动尝试',5,1);

-- 待改进项
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SHW001','special_h_weak','课堂专注度不足，频繁走神',1,1),
('SHW002','special_h_weak','不敢提问，不懂装懂',2,1),
('SHW003','special_h_weak','练习拖沓，无法完成课堂任务',3,1),
('SHW004','special_h_weak','对错题不重视，不复盘不纠正',4,1),
('SHW005','special_h_weak','遇难题就畏难，直接放弃思考',5,1);

-- ---------------- 专项课：核心闭环模块 ----------------

-- 课后巩固任务
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SW001','special_homework','完成专项练习',1,1),
('SW002','special_homework','复习本节课核心知识点，整理笔记',2,1),
('SW003','special_homework','完成打卡任务',3,1),
('SW004','special_homework','预习下节课内容',4,1);

-- 下节课针对性备课重点
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SNP001','special_next_prep','核心知识点二次讲解+专项练习',1,1),
('SNP002','special_next_prep','易错题型专项拆解+变式训练',2,1),
('SNP003','special_next_prep','学习方法/解题思路专项指导',3,1);

-- 家校配合建议
INSERT OR IGNORE INTO indicator (indicator_id, category, text, sort_order, active) VALUES
('SHS001','special_home_school','在家督促孩子完成课后巩固任务，及时打卡',1,1),
('SHS002','special_home_school','多鼓励孩子，肯定课堂上的进步',2,1),
('SHS003','special_home_school','引导孩子复盘本节课内容，主动表达收获',3,1);

-- =====================================================================
-- 备注：本文件只维护 indicator 指标字典（85 条）。九阶阶段、晚辅档位、
-- 入学测评模块/维度/评分锚点由 db/seed_reference_data.sql 独立维护。
-- =====================================================================
