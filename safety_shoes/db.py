"""SQLite 持久层：发放链路全部落库。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 员工：工号、姓名、岗位、岗位防护等级、当前仓库权限标记
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY,
    emp_no        TEXT NOT NULL UNIQUE,        -- 员工代号（门禁工号同号）
    name          TEXT NOT NULL,
    position      TEXT NOT NULL,               -- 岗位名称
    level         TEXT NOT NULL,               -- 岗位防护等级 S1..S5（仅主管可改）
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 尺码档案：支持左右脚差异
CREATE TABLE IF NOT EXISTS size_profiles (
    id          INTEGER PRIMARY KEY,
    emp_no      TEXT NOT NULL UNIQUE REFERENCES employees(emp_no),
    size_left   TEXT NOT NULL,                 -- 左脚尺码
    size_right  TEXT NOT NULL,                 -- 右脚尺码
    width       TEXT NOT NULL DEFAULT 'M',     -- 脚型 W/M/N
    note        TEXT DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 鞋款
CREATE TABLE IF NOT EXISTS shoe_models (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    level        TEXT NOT NULL,                -- 主防护等级
    lifespan_months INTEGER NOT NULL DEFAULT 12
);

-- 库存批次：仓库 × 鞋款 × 尺码 的二维矩阵单元
CREATE TABLE IF NOT EXISTS inventory (
    id          INTEGER PRIMARY KEY,
    warehouse   TEXT NOT NULL,                 -- A库 / B库
    model_code  TEXT NOT NULL REFERENCES shoe_models(code),
    size        TEXT NOT NULL,
    batch_no    TEXT NOT NULL,                 -- 入库批次
    qty_total   INTEGER NOT NULL,
    qty_on_hand INTEGER NOT NULL,              -- 剩余数量
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(warehouse, model_code, size, batch_no)
);

-- 试穿记录：试穿 -> 确认（锁定尺码并扣库存）/ 调码 / 驳回
CREATE TABLE IF NOT EXISTS fittings (
    id           INTEGER PRIMARY KEY,
    emp_no       TEXT NOT NULL REFERENCES employees(emp_no),
    level        TEXT NOT NULL,                -- 发放时岗位防护等级快照
    model_code   TEXT NOT NULL REFERENCES shoe_models(code),
    size_left    TEXT NOT NULL,
    size_right   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending/confirmed/adjusted/rejected
    final_size_left  TEXT,
    final_size_right TEXT,
    tried_sizes  TEXT DEFAULT '',              -- 调码历史 38->39
    renewal_id   INTEGER REFERENCES renewals(id), -- 换领试穿时关联换领单
    created_by   TEXT NOT NULL,                -- 仓管
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    confirmed_at TEXT
);

-- 发放单（一双鞋一条）：active 唯一约束的业务对象
CREATE TABLE IF NOT EXISTS issues (
    id            INTEGER PRIMARY KEY,
    issue_no      TEXT NOT NULL UNIQUE,        -- ISS-yyyy-xxxx
    emp_no        TEXT NOT NULL REFERENCES employees(emp_no),
    level         TEXT NOT NULL,               -- 防护等级快照
    model_code    TEXT NOT NULL REFERENCES shoe_models(code),
    size_left     TEXT NOT NULL,
    size_right    TEXT NOT NULL,
    warehouse     TEXT NOT NULL,
    inventory_id  INTEGER NOT NULL REFERENCES inventory(id),
    fitting_id    INTEGER REFERENCES fittings(id),
    issue_date    TEXT NOT NULL,               -- 发放日期
    expire_date   TEXT NOT NULL,               -- 使用期限到期日
    status        TEXT NOT NULL DEFAULT 'active',
        -- active(有效) / renewed(已换领) / damaged(损坏批准收回) / scrapped(报废) / transferred(随岗移交结算)
    pair_tag      TEXT NOT NULL,               -- 鞋身唯一标识 SN
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- 同一员工同一防护等级只能有一双有效鞋（部分唯一索引）
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_issue
    ON issues(emp_no, level) WHERE status = 'active';

-- 换领单：正常到期 / 批准损坏 / 提前磨损（主管审批）
CREATE TABLE IF NOT EXISTS renewals (
    id            INTEGER PRIMARY KEY,
    renewal_no    TEXT NOT NULL UNIQUE,
    emp_no        TEXT NOT NULL REFERENCES employees(emp_no),
    level         TEXT NOT NULL,
    old_issue_id  INTEGER REFERENCES issues(id),
    reason        TEXT NOT NULL,               -- due/damage/early_wear/transfer
    status        TEXT NOT NULL DEFAULT 'requested',
        -- requested/approved/rejected/completed
    supervisor    TEXT DEFAULT '',             -- 审批主管
    approve_note  TEXT DEFAULT '',
    requested_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    approved_at   TEXT,
    completed_issue_id INTEGER REFERENCES issues(id)
);

-- 损坏/提前磨损申请及三个固定照片位（鞋底 sole / 鞋头 toe / 鞋帮 upper）
CREATE TABLE IF NOT EXISTS damage_claims (
    id           INTEGER PRIMARY KEY,
    renewal_id   INTEGER NOT NULL REFERENCES renewals(id),
    emp_no       TEXT NOT NULL REFERENCES employees(emp_no),
    photo_sole   TEXT DEFAULT '',
    photo_toe    TEXT DEFAULT '',
    photo_upper  TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 旧鞋回收：领取新鞋必须交回旧鞋或登记豁免
CREATE TABLE IF NOT EXISTS returns (
    id            INTEGER PRIMARY KEY,
    issue_id      INTEGER NOT NULL REFERENCES issues(id),
    renewal_id    INTEGER REFERENCES renewals(id),
    emp_no        TEXT NOT NULL REFERENCES employees(emp_no),
    returned      INTEGER NOT NULL DEFAULT 0,  -- 1 已交回 / 0 豁免（遗失等）
    exemption     INTEGER NOT NULL DEFAULT 0,
    exemption_reason TEXT DEFAULT '',          -- lost(旧鞋遗失)/destroyed/...
    approver      TEXT DEFAULT '',             -- 豁免审批主管
    approve_status TEXT DEFAULT 'none',        -- none/pending/approved/rejected
    condition     TEXT DEFAULT '',             -- reusable(可用->清洁复检) / scrap(报废->待处置)
    status        TEXT NOT NULL DEFAULT 'received',
        -- received/cleaning_inspection/back_to_stock/disposal_pending/disposed/exempt
    warehouse     TEXT DEFAULT 'A库',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 待处置批次（报废鞋）
CREATE TABLE IF NOT EXISTS disposal_batches (
    id           INTEGER PRIMARY KEY,
    batch_no     TEXT NOT NULL UNIQUE,
    warehouse    TEXT NOT NULL,
    item_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending/dispatched
    receipt_no   TEXT DEFAULT '',                  -- 模拟处置回执
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    dispatched_at TEXT
);

CREATE TABLE IF NOT EXISTS disposal_items (
    id          INTEGER PRIMARY KEY,
    batch_id    INTEGER REFERENCES disposal_batches(id),
    return_id   INTEGER NOT NULL REFERENCES returns(id),
    pair_tag    TEXT NOT NULL
);

-- 缺码候补
CREATE TABLE IF NOT EXISTS waitlist (
    id          INTEGER PRIMARY KEY,
    emp_no      TEXT NOT NULL REFERENCES employees(emp_no),
    level       TEXT NOT NULL,
    model_code  TEXT NOT NULL REFERENCES shoe_models(code),
    size_left   TEXT NOT NULL,
    size_right  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'waiting', -- waiting/notified/fulfilled/cancelled
    notified_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 提醒（进程内任务生成）
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,                  -- due / waitlist
    ref_type    TEXT NOT NULL,                  -- issue/waitlist
    ref_id      INTEGER NOT NULL,
    emp_no      TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(kind, ref_type, ref_id)
);

-- 岗位调动记录
CREATE TABLE IF NOT EXISTS transfers (
    id           INTEGER PRIMARY KEY,
    emp_no       TEXT NOT NULL REFERENCES employees(emp_no),
    from_level   TEXT NOT NULL,
    to_level     TEXT NOT NULL,
    from_position TEXT NOT NULL,
    to_position   TEXT NOT NULL,
    handled      INTEGER NOT NULL DEFAULT 0,   -- 原岗位鞋是否已结算
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 操作审计（门禁工号、操作角色）
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    actor       TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """立即开始写事务（BEGIN IMMEDIATE）—— 并发发放竞争在库层串行化。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db(db_path: Path | str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
