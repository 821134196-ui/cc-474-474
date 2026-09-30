"""SQLite 数据层：建库连接与表结构。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 岗位防护等级
CREATE TABLE IF NOT EXISTS protection_levels (
    code        TEXT PRIMARY KEY,          -- SB / S1 / S1P / S3
    name        TEXT NOT NULL,
    allowed_models TEXT NOT NULL,          -- 逗号分隔，授权可领鞋款
    lifespan_months INTEGER NOT NULL       -- 该等级标准使用期限（月）
);

-- 员工
CREATE TABLE IF NOT EXISTS employees (
    id          TEXT PRIMARY KEY,          -- 员工代号，如 E1001
    name        TEXT NOT NULL,
    post        TEXT NOT NULL,             -- 岗位名称
    grade_code  TEXT NOT NULL REFERENCES protection_levels(code),
    badge_no    TEXT NOT NULL UNIQUE,      -- 门禁工号（适配器同步）
    active      INTEGER NOT NULL DEFAULT 1
);

-- 员工脚型尺码档案（可记录左右脚差异）
CREATE TABLE IF NOT EXISTS size_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    left_size   REAL NOT NULL,             -- 左脚 EUR
    right_size  REAL NOT NULL,             -- 右脚 EUR
    width       TEXT NOT NULL DEFAULT 'M', -- 脚宽 窄N/中M/宽W
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

-- 鞋款
CREATE TABLE IF NOT EXISTS shoe_models (
    code        TEXT PRIMARY KEY,          -- 如 GUARD-S3
    name        TEXT NOT NULL,
    grade_code  TEXT NOT NULL REFERENCES protection_levels(code),
    sizes       TEXT NOT NULL              -- 该鞋款生产尺码，逗号分隔，如 38,39,40
);

-- 库存批次（分仓库）
CREATE TABLE IF NOT EXISTS inventory_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_code  TEXT NOT NULL REFERENCES shoe_models(code),
    size        REAL NOT NULL,
    warehouse   TEXT NOT NULL,             -- A仓 / B仓
    qty_total   INTEGER NOT NULL,
    qty_used    INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);

-- 试穿 / 调码记录
CREATE TABLE IF NOT EXISTS fittings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    model_code  TEXT NOT NULL REFERENCES shoe_models(code),
    tried_size  REAL NOT NULL,
    left_fit    TEXT NOT NULL DEFAULT '',  -- 松/合适/紧
    right_fit   TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL,             -- ok 合适 / adjust 需调码 / reject 不合脚
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

-- 候补：员工某尺码缺码时排队
CREATE TABLE IF NOT EXISTS waitlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    model_code  TEXT NOT NULL REFERENCES shoe_models(code),
    size        REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'waiting', -- waiting / notified / cancelled / filled
    created_at  TEXT NOT NULL,
    notified_at TEXT
);

-- 发放链路：一双鞋一条主记录
CREATE TABLE IF NOT EXISTS issuances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     TEXT NOT NULL REFERENCES employees(id),
    grade_code      TEXT NOT NULL,          -- 发放时的防护等级
    model_code      TEXT NOT NULL REFERENCES shoe_models(code),
    size            REAL NOT NULL,          -- 锁定尺码（左右差异时取较大码，见 size_detail）
    size_detail     TEXT NOT NULL DEFAULT '',-- 如 L41/R40
    batch_id        INTEGER REFERENCES inventory_batches(id),
    warehouse       TEXT NOT NULL,
    issued_at       TEXT NOT NULL,          -- 发放日期
    lifespan_months INTEGER NOT NULL,       -- 使用期限（月）
    due_date        TEXT NOT NULL,          -- 到期日
    replaced_id     INTEGER REFERENCES issuances(id), -- 本双换领自哪一双
    status          TEXT NOT NULL DEFAULT 'active',
        -- active 有效 / returned 已交回（换领完成）/ exemption 已豁免交回 / scrapped 已报废
    old_returned_at TEXT,
    disposal_id     INTEGER REFERENCES disposals(id)
);
-- “同一员工同一防护等级只能有一双有效鞋” 由部分唯一索引保证
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_per_grade
    ON issuances(employee_id, grade_code) WHERE status='active';

-- 换领申请（正常到期 / 损坏提前）
CREATE TABLE IF NOT EXISTS renewals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    issuance_id     INTEGER NOT NULL REFERENCES issuances(id),
    employee_id     TEXT NOT NULL REFERENCES employees(id),
    kind            TEXT NOT NULL,          -- expiry 到期 / damage 损坏提前 / transfer 调岗换领
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending 待审批 / approved 已批准 / rejected 已驳回 / completed 已领取 / auto 到期自动可换
    supervisor      TEXT DEFAULT '',        -- 审批主管
    decided_at      TEXT,
    new_issuance_id INTEGER REFERENCES issuances(id),
    created_at      TEXT NOT NULL,
    -- 旧鞋处置方式
    old_shoe_action TEXT DEFAULT '',        -- return 交回 / exemption 豁免
    exemption_reason TEXT DEFAULT '',       -- lost 遗失 / other
    approved_by     TEXT DEFAULT ''
);

-- 旧鞋回收登记
CREATE TABLE IF NOT EXISTS returns_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    issuance_id     INTEGER NOT NULL REFERENCES issuances(id),
    renewal_id      INTEGER REFERENCES renewals(id),
    condition       TEXT NOT NULL,          -- reusable 可用（进入清洁复检） / scrap 报废（待处置）
    received_at     TEXT NOT NULL,
    warehouse       TEXT NOT NULL
);

-- 清洁复检
CREATE TABLE IF NOT EXISTS recondition (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id       INTEGER NOT NULL REFERENCES returns_log(id),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending / passed / failed
    inspector       TEXT DEFAULT '',
    inspected_at    TEXT,
    note            TEXT DEFAULT ''
);

-- 报废处置批次 + 回执
CREATE TABLE IF NOT EXISTS disposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no        TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending 待处置 / done 已处置
    vendor          TEXT DEFAULT '',
    receipt_no      TEXT DEFAULT '',        -- 模拟处置回执号
    created_at      TEXT NOT NULL,
    done_at         TEXT
);
CREATE TABLE IF NOT EXISTS disposal_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    disposal_id     INTEGER NOT NULL REFERENCES disposals(id),
    return_id       INTEGER NOT NULL REFERENCES returns_log(id),
    model_code      TEXT NOT NULL,
    size            REAL NOT NULL
);

-- 跨岗位调动记录
CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     TEXT NOT NULL REFERENCES employees(id),
    from_grade      TEXT NOT NULL,
    to_grade        TEXT NOT NULL,
    from_post       TEXT NOT NULL,
    to_post         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- 进程内任务生成的提醒
CREATE TABLE IF NOT EXISTS reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,          -- due 到期 / waitlist 候补到货
    ref_type        TEXT NOT NULL,          -- issuance / waitlist
    ref_id          INTEGER NOT NULL,
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    read            INTEGER NOT NULL DEFAULT 0
);

-- 损坏申请的三个固定照片位：sole 鞋底 / toe 鞋头 / upper 鞋帮
CREATE TABLE IF NOT EXISTS damage_photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    renewal_id      INTEGER NOT NULL REFERENCES renewals(id),
    slot            TEXT NOT NULL CHECK(slot IN ('sole','toe','upper')),
    file_path       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(renewal_id, slot)
);

-- 角色审计日志（仓管越权改等级等尝试留痕）
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None：应用自行管理显式事务（BEGIN IMMEDIATE 保证并发发放串行）
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
