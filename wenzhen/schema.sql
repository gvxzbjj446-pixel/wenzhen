-- 王艳霞中医门诊 · 数据库结构
-- 每次启动都会执行；全部使用 IF NOT EXISTS，不会覆盖已有数据。

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- 患者档案
CREATE TABLE IF NOT EXISTS patients (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    gender         TEXT NOT NULL DEFAULT '',
    birth_date     TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD
    phone          TEXT NOT NULL DEFAULT '',
    address        TEXT NOT NULL DEFAULT '',
    occupation     TEXT NOT NULL DEFAULT '',
    allergies      TEXT NOT NULL DEFAULT '',   -- 过敏史
    past_history   TEXT NOT NULL DEFAULT '',   -- 既往史
    family_history TEXT NOT NULL DEFAULT '',   -- 家族史
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name);
CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone);

-- 门诊病历（每次就诊一条）
CREATE TABLE IF NOT EXISTS visits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id          INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    visit_date          TEXT NOT NULL,                  -- YYYY-MM-DD
    visit_type          TEXT NOT NULL DEFAULT '初诊',    -- 初诊 / 复诊
    -- 主诉与病史
    chief_complaint     TEXT NOT NULL DEFAULT '',
    present_illness     TEXT NOT NULL DEFAULT '',
    -- 十问
    cold_heat           TEXT NOT NULL DEFAULT '',
    sweating            TEXT NOT NULL DEFAULT '',
    head_body           TEXT NOT NULL DEFAULT '',
    chest_abdomen       TEXT NOT NULL DEFAULT '',
    diet                TEXT NOT NULL DEFAULT '',
    thirst              TEXT NOT NULL DEFAULT '',
    sleep               TEXT NOT NULL DEFAULT '',
    stool               TEXT NOT NULL DEFAULT '',
    urine               TEXT NOT NULL DEFAULT '',
    ears_eyes           TEXT NOT NULL DEFAULT '',
    menstruation        TEXT NOT NULL DEFAULT '',
    -- 望闻切
    inspection          TEXT NOT NULL DEFAULT '',
    tongue_body         TEXT NOT NULL DEFAULT '',
    tongue_coating      TEXT NOT NULL DEFAULT '',
    pulse               TEXT NOT NULL DEFAULT '',
    listening           TEXT NOT NULL DEFAULT '',
    physical_exam       TEXT NOT NULL DEFAULT '',
    lab_results         TEXT NOT NULL DEFAULT '',
    -- 诊断与治法
    tcm_disease         TEXT NOT NULL DEFAULT '',
    syndrome            TEXT NOT NULL DEFAULT '',
    western_diagnosis   TEXT NOT NULL DEFAULT '',
    treatment_principle TEXT NOT NULL DEFAULT '',
    -- 处方（药物明细见 prescription_items）
    formula_name        TEXT NOT NULL DEFAULT '',
    dose_count          INTEGER NOT NULL DEFAULT 0,     -- 剂数
    usage               TEXT NOT NULL DEFAULT '',       -- 煎服法
    -- 其他
    other_treatment     TEXT NOT NULL DEFAULT '',
    advice              TEXT NOT NULL DEFAULT '',
    fee                 REAL NOT NULL DEFAULT 0,
    next_visit_date     TEXT NOT NULL DEFAULT '',       -- 预约复诊日期
    created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_visits_patient ON visits(patient_id, visit_date);
CREATE INDEX IF NOT EXISTS idx_visits_date ON visits(visit_date);
CREATE INDEX IF NOT EXISTS idx_visits_next ON visits(next_visit_date);

CREATE TABLE IF NOT EXISTS prescription_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL REFERENCES visits(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    herb     TEXT NOT NULL,
    dose     REAL,
    unit     TEXT NOT NULL DEFAULT 'g',
    note     TEXT NOT NULL DEFAULT ''    -- 先煎、后下、包煎等
);
CREATE INDEX IF NOT EXISTS idx_prescription_items_visit ON prescription_items(visit_id);
CREATE INDEX IF NOT EXISTS idx_prescription_items_herb ON prescription_items(herb);

-- 方剂库（常用方 / 经验方模板）
CREATE TABLE IF NOT EXISTS formulas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    source     TEXT NOT NULL DEFAULT '',   -- 出处
    indication TEXT NOT NULL DEFAULT '',   -- 功用主治
    usage      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS formula_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_id INTEGER NOT NULL REFERENCES formulas(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    herb       TEXT NOT NULL,
    dose       REAL,
    unit       TEXT NOT NULL DEFAULT 'g',
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_formula_items_formula ON formula_items(formula_id);

-- ================================================================ 理疗康复

-- 理疗项目（针刺、艾灸、推拿、拔罐等，可在“理疗项目”页增删改）
CREATE TABLE IF NOT EXISTS therapy_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    category   TEXT NOT NULL DEFAULT '',   -- 针法 / 灸法 / 拔罐 / 推拿 / 外治 / 理疗
    minutes    INTEGER NOT NULL DEFAULT 0, -- 默认时长（分钟），0 表示不计时
    price      REAL NOT NULL DEFAULT 0,    -- 参考价格（元），0 表示未定价
    notes      TEXT NOT NULL DEFAULT '',   -- 操作要点、禁忌
    active     INTEGER NOT NULL DEFAULT 1, -- 停用后不在录入时列出，历史记录不受影响
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 理疗疗程（一个病症的一段系统治疗，如“腰痛 针刺+推拿 10 次”）
CREATE TABLE IF NOT EXISTS therapy_courses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id         INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    visit_id           INTEGER REFERENCES visits(id) ON DELETE SET NULL,  -- 开具疗程的那次就诊
    start_date         TEXT NOT NULL,                     -- YYYY-MM-DD
    status             TEXT NOT NULL DEFAULT '进行中',     -- 进行中 / 已完成 / 已中止
    end_date           TEXT NOT NULL DEFAULT '',
    diagnosis          TEXT NOT NULL DEFAULT '',
    body_part          TEXT NOT NULL DEFAULT '',          -- 治疗部位
    goal               TEXT NOT NULL DEFAULT '',          -- 康复目标
    planned_sessions   INTEGER NOT NULL DEFAULT 10,       -- 计划治疗次数
    frequency          TEXT NOT NULL DEFAULT '',          -- 每日1次、隔日1次等
    initial_pain       INTEGER,                           -- 治疗前疼痛评分（VAS 0–10），空为未评
    initial_assessment TEXT NOT NULL DEFAULT '',          -- 初次评估：症状、体征、活动度
    precautions        TEXT NOT NULL DEFAULT '',          -- 注意事项与禁忌
    fee                REAL NOT NULL DEFAULT 0,           -- 疗程收费（预收，可为 0）
    final_pain         INTEGER,                           -- 疗程结束时疼痛评分
    final_assessment   TEXT NOT NULL DEFAULT '',          -- 疗程小结
    outcome            TEXT NOT NULL DEFAULT '',          -- 疗效：痊愈 / 显效 / 有效 / 无效
    created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_therapy_courses_patient ON therapy_courses(patient_id, start_date);
CREATE INDEX IF NOT EXISTS idx_therapy_courses_status ON therapy_courses(status);

-- 疗程计划的治疗项目；每次治疗默认按此填写
CREATE TABLE IF NOT EXISTS therapy_course_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES therapy_courses(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL DEFAULT 0,
    therapy   TEXT NOT NULL,              -- 项目名称
    site      TEXT NOT NULL DEFAULT '',   -- 部位 / 穴位
    minutes   INTEGER,                    -- 时长（分钟）
    note      TEXT NOT NULL DEFAULT ''    -- 手法、参数，如：平补平泻、电针疏密波
);
CREATE INDEX IF NOT EXISTS idx_therapy_course_items_course ON therapy_course_items(course_id);

-- 治疗记录（每次治疗一条）
CREATE TABLE IF NOT EXISTS therapy_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES therapy_courses(id) ON DELETE CASCADE,
    session_date TEXT NOT NULL,                  -- YYYY-MM-DD
    pain_before  INTEGER,                        -- 治疗前疼痛评分（VAS 0–10）
    pain_after   INTEGER,                        -- 治疗后疼痛评分
    reaction     TEXT NOT NULL DEFAULT '',       -- 治疗反应
    notes        TEXT NOT NULL DEFAULT '',       -- 病情变化、方案调整
    therapist    TEXT NOT NULL DEFAULT '',       -- 治疗者
    fee          REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_therapy_sessions_course ON therapy_sessions(course_id, session_date);
CREATE INDEX IF NOT EXISTS idx_therapy_sessions_date ON therapy_sessions(session_date);

CREATE TABLE IF NOT EXISTS therapy_session_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES therapy_sessions(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    therapy    TEXT NOT NULL,
    site       TEXT NOT NULL DEFAULT '',
    minutes    INTEGER,
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_therapy_session_items_session ON therapy_session_items(session_id);
CREATE INDEX IF NOT EXISTS idx_therapy_session_items_therapy ON therapy_session_items(therapy);
