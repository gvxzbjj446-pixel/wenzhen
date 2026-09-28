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
