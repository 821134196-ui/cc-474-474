"""核心业务服务：发放链路全部事务规则。

所有函数接收 sqlite3.Connection 与调用方 actor:
actor = {"name": str, "role": "keeper" 仓管 / "supervisor" 主管 / "admin"}

连接使用 isolation_level=None（autocommit），多语句写操作统一用 _tx 显式
BEGIN IMMEDIATE，保证两处仓库并发发放时整笔事务串行、仅一次成功。
"""
from __future__ import annotations

import calendar
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from .adapters import DisposalVendor


class BusinessError(Exception):
    """业务规则冲突，消息可直接展示给用户。"""


ROLE_KEEPER = "keeper"
ROLE_SUPERVISOR = "supervisor"
ROLE_ADMIN = "admin"

# ---------------- 工具 ----------------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_months(d: date, months: int) -> date:
    """到期日：发放日 + N 个月，月末日期做钳制。"""
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def _today(now: date | None) -> date:
    return now or date.today()


@contextmanager
def _tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _audit(conn, actor: dict, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(actor, action, detail, created_at) VALUES(?,?,?,?)",
        (actor["name"], action, detail, now_str()))


def _require_role(actor: dict, roles: tuple[str, ...], action: str) -> None:
    if actor.get("role") not in roles:
        raise BusinessError(f"权限不足：{action}需要 {'/'.join(roles)} 角色")


# ---------------- 员工与尺码档案 ----------------

def list_employees(conn) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT e.*, pl.name AS grade_name, pl.lifespan_months
           FROM employees e JOIN protection_levels pl ON pl.code=e.grade_code
           WHERE e.active=1 ORDER BY e.id"""))


def get_employee(conn, employee_id: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT e.*, pl.name AS grade_name, pl.allowed_models, pl.lifespan_months
           FROM employees e JOIN protection_levels pl ON pl.code=e.grade_code
           WHERE e.id=?""", (employee_id,)).fetchone()
    if not row:
        raise BusinessError("员工不存在")
    return row


def latest_profile(conn, employee_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM size_profiles WHERE employee_id=? ORDER BY id DESC LIMIT 1",
        (employee_id,)).fetchone()


def upsert_profile(conn, employee_id: str, left_size: float, right_size: float,
                   width: str, note: str, actor: dict) -> int:
    get_employee(conn, employee_id)
    if not (34 <= left_size <= 48 and 34 <= right_size <= 48):
        raise BusinessError("尺码超出 34–48 范围")
    if width not in ("N", "M", "W"):
        raise BusinessError("脚宽必须为 窄N/中M/宽W")
    with _tx(conn):
        cur = conn.execute(
            """INSERT INTO size_profiles(employee_id,left_size,right_size,width,note,created_at)
               VALUES(?,?,?,?,?,?)""",
            (employee_id, left_size, right_size, width, note, now_str()))
        _audit(conn, actor, "尺码档案", f"{employee_id} L{left_size}/R{right_size} 脚宽{width}")
    return cur.lastrowid


def transfer_post(conn, employee_id: str, to_post: str, to_grade: str, actor: dict):
    """跨岗位调动：仅主管/admin 可执行（仓管不能修改岗位等级）。"""
    _require_role(actor, (ROLE_SUPERVISOR, ROLE_ADMIN), "跨岗位调动")
    emp = get_employee(conn, employee_id)
    grade = conn.execute("SELECT code FROM protection_levels WHERE code=?", (to_grade,)).fetchone()
    if not grade:
        raise BusinessError("目标防护等级不存在")
    if emp["grade_code"] == to_grade:
        raise BusinessError("新旧岗位防护等级相同，无需调岗换领")
    with _tx(conn):
        conn.execute(
            "INSERT INTO transfers(employee_id,from_grade,to_grade,from_post,to_post,created_at) VALUES(?,?,?,?,?,?)",
            (employee_id, emp["grade_code"], to_grade, emp["post"], to_post, now_str()))
        conn.execute("UPDATE employees SET post=?, grade_code=? WHERE id=?",
                     (to_post, to_grade, employee_id))
        # 旧等级有效鞋 → 自动生成已批准的调岗换领单（领新鞋时交回旧鞋）
        old = conn.execute(
            "SELECT * FROM issuances WHERE employee_id=? AND grade_code=? AND status='active'",
            (employee_id, emp["grade_code"])).fetchone()
        renewal_id = None
        if old:
            renewal_id = conn.execute(
                """INSERT INTO renewals(issuance_id,employee_id,kind,reason,status,supervisor,decided_at,created_at)
                   VALUES(?,?,?,?,'approved',?,?,?)""",
                (old["id"], employee_id, "transfer",
                 f"调岗 {emp['post']}({emp['grade_code']}) → {to_post}({to_grade})",
                 actor["name"], now_str(), now_str())).lastrowid
        _audit(conn, actor, "跨岗位调动", f"{employee_id} {emp['grade_code']}→{to_grade}")
    return renewal_id


# ---------------- 岗位授权 → 可领规格 ----------------

def eligible_specs(conn, employee_id: str) -> dict:
    """岗位授权后生成可领规格：等级授权鞋款 × 脚型尺码。"""
    emp = get_employee(conn, employee_id)
    profile = latest_profile(conn, employee_id)
    models = [m for m in emp["allowed_models"].split(",") if m]
    result = []
    for code in models:
        m = conn.execute("SELECT * FROM shoe_models WHERE code=?", (code,)).fetchone()
        if not m:
            continue
        produced = sorted(float(x) for x in m["sizes"].split(","))
        sizes = []
        lock = None
        if profile:
            for s in (profile["left_size"], profile["right_size"]):
                if s in produced and s not in sizes:
                    sizes.append(s)
            cand = max(profile["left_size"], profile["right_size"])
            lock = cand if cand in produced else None
        result.append({
            "code": m["code"], "name": m["name"], "sizes": sizes,
            "produced": produced, "lock_size": lock,
        })
    return {"employee": dict(emp), "profile": dict(profile) if profile else None, "models": result}


# ---------------- 试穿（确认锁码 / 调码） ----------------

def record_fitting(conn, employee_id: str, model_code: str, tried_size: float,
                   left_fit: str, right_fit: str, result: str, note: str,
                   actor: dict) -> int:
    emp = get_employee(conn, employee_id)
    model = conn.execute("SELECT * FROM shoe_models WHERE code=?", (model_code,)).fetchone()
    if not model:
        raise BusinessError("鞋款不存在")
    if model["grade_code"] != emp["grade_code"]:
        raise BusinessError("该鞋款不属于员工当前岗位防护等级，不可试穿")
    if tried_size not in {float(x) for x in model["sizes"].split(",")}:
        raise BusinessError("鞋款无此生产尺码")
    if result not in ("ok", "adjust", "reject"):
        raise BusinessError("试穿结果非法")
    with _tx(conn):
        cur = conn.execute(
            """INSERT INTO fittings(employee_id,model_code,tried_size,left_fit,right_fit,result,note,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (employee_id, model_code, tried_size, left_fit, right_fit, result, note, now_str()))
        _audit(conn, actor, "试穿登记", f"{employee_id} {model_code} {tried_size} {result}")
    return cur.lastrowid


def latest_ok_fitting(conn, employee_id: str, model_code: str, size: float):
    """最近一条试穿记录：若为该尺码的 ok 确认，则尺码已锁定；
    若最近是 adjust/reject（试穿调码），则原确认失效，须重新确认。"""
    r = conn.execute(
        "SELECT * FROM fittings WHERE employee_id=? AND model_code=? ORDER BY id DESC LIMIT 1",
        (employee_id, model_code)).fetchone()
    if r and r["result"] == "ok" and r["tried_size"] == size:
        return r
    return None


# ---------------- 库存矩阵 ----------------

def inventory_matrix(conn) -> dict:
    levels = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM protection_levels")}
    out = {}
    for m in conn.execute("SELECT * FROM shoe_models ORDER BY grade_code, code"):
        sizes = sorted(float(x) for x in m["sizes"].split(","))
        grid = {s: {"total": 0, "available": 0, "warehouses": {}} for s in sizes}
        for b in conn.execute(
                "SELECT * FROM inventory_batches WHERE model_code=? ORDER BY size", (m["code"],)):
            cell = grid[b["size"]]
            avail = b["qty_total"] - b["qty_used"]
            cell["total"] += b["qty_total"]
            cell["available"] += avail
            wh = cell["warehouses"].setdefault(
                b["warehouse"], {"total": 0, "available": 0, "batches": []})
            wh["total"] += b["qty_total"]
            wh["available"] += avail
            wh["batches"].append({"id": b["id"], "total": b["qty_total"],
                                  "used": b["qty_used"], "available": avail,
                                  "received_at": b["received_at"]})
        out[m["code"]] = {"name": m["name"], "grade": m["grade_code"],
                          "grade_name": levels.get(m["grade_code"], m["grade_code"]),
                          "sizes": sizes, "grid": grid}
    return out


def stock_qty(conn, model_code: str, size: float, warehouse: str | None = None) -> int:
    q = ("SELECT COALESCE(SUM(qty_total-qty_used),0) FROM inventory_batches "
         "WHERE model_code=? AND size=?")
    args: list = [model_code, size]
    if warehouse:
        q += " AND warehouse=?"
        args.append(warehouse)
    return conn.execute(q, args).fetchone()[0]


def receive_stock(conn, model_code: str, size: float, warehouse: str, qty: int,
                  actor: dict) -> int:
    """到货入库：新增批次。下一轮提醒任务会通知该规格的缺码候补。"""
    _require_role(actor, (ROLE_KEEPER, ROLE_ADMIN), "入库")
    if qty <= 0:
        raise BusinessError("入库数量必须大于 0")
    if not conn.execute("SELECT 1 FROM shoe_models WHERE code=?", (model_code,)).fetchone():
        raise BusinessError("鞋款不存在")
    with _tx(conn):
        bid = conn.execute(
            "INSERT INTO inventory_batches(model_code,size,warehouse,qty_total,qty_used,received_at) VALUES(?,?,?,?,0,?)",
            (model_code, size, warehouse, qty, now_str())).lastrowid
        _audit(conn, actor, "入库", f"{model_code} {size:g} {warehouse} +{qty}")
    return bid


# ---------------- 发放（含并发扣减与唯一有效鞋） ----------------

def _join_waitlist(conn, employee_id: str, model_code: str, size: float) -> int:
    exists = conn.execute(
        "SELECT id FROM waitlist WHERE employee_id=? AND model_code=? AND size=? AND status='waiting'",
        (employee_id, model_code, size)).fetchone()
    if exists:
        return exists["id"]
    return conn.execute(
        "INSERT INTO waitlist(employee_id,model_code,size,status,created_at) VALUES(?,?,?,'waiting',?)",
        (employee_id, model_code, size, now_str())).lastrowid


def _issue_locked(conn, emp, model_code: str, size: float, warehouse: str,
                  lifespan: int, today: date, profile, actor: dict,
                  replaced_id: int | None):
    """调用方必须已持有 BEGIN IMMEDIATE 事务。

    返回 ("ok", issuance_id) / ("dup", existing_id) / ("missing", waitlist_id)
    """
    # 同一员工同一防护等级只能有一双有效鞋
    dup = conn.execute(
        "SELECT id FROM issuances WHERE employee_id=? AND grade_code=? AND status='active'",
        (emp["id"], emp["grade_code"])).fetchone()
    if dup:
        return ("dup", dup["id"])

    # FIFO 取该仓该规格有余量的批次；条件更新做原子扣减
    batches = conn.execute(
        """SELECT id FROM inventory_batches
           WHERE model_code=? AND size=? AND warehouse=? AND qty_used<qty_total
           ORDER BY id""", (model_code, size, warehouse)).fetchall()
    batch_id = None
    for b in batches:
        cur = conn.execute(
            "UPDATE inventory_batches SET qty_used=qty_used+1 WHERE id=? AND qty_used<qty_total",
            (b["id"],))
        if cur.rowcount == 1:
            batch_id = b["id"]
            break
    if batch_id is None:
        return ("missing", _join_waitlist(conn, emp["id"], model_code, size))

    due = add_months(today, lifespan)
    size_detail = f"L{profile['left_size']:g}/R{profile['right_size']:g}" if profile else ""
    cur = conn.execute(
        """INSERT INTO issuances(employee_id,grade_code,model_code,size,size_detail,batch_id,
              warehouse,issued_at,lifespan_months,due_date,replaced_id,status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,'active')""",
        (emp["id"], emp["grade_code"], model_code, size, size_detail, batch_id,
         warehouse, today.strftime("%Y-%m-%d"), lifespan, due.strftime("%Y-%m-%d"), replaced_id))
    issuance_id = cur.lastrowid
    conn.execute(
        "UPDATE waitlist SET status='filled' WHERE employee_id=? AND model_code=? AND size=? AND status='waiting'",
        (emp["id"], model_code, size))
    _audit(conn, actor, "发放",
           f"issuance#{issuance_id} {emp['id']} {model_code} {size:g} {warehouse}仓")
    return ("ok", issuance_id)


def _validate_issue(conn, employee_id: str, model_code: str, size: float, warehouse: str,
                    actor: dict, *, renewal_id: int | None = None):
    """发放前的资格校验（事务外只读）。renewal_id 为正在领取的换领单，免于
    “未完成调岗单”拦截（它本身就是该单的交旧领新）。"""
    _require_role(actor, (ROLE_KEEPER, ROLE_ADMIN), "发放")
    emp = get_employee(conn, employee_id)
    model = conn.execute("SELECT * FROM shoe_models WHERE code=?", (model_code,)).fetchone()
    if not model:
        raise BusinessError("鞋款不存在")
    if model_code not in [x for x in emp["allowed_models"].split(",") if x]:
        raise BusinessError("岗位未授权该鞋款")
    if size not in {float(x) for x in model["sizes"].split(",")}:
        raise BusinessError("鞋款无此尺码")
    profile = latest_profile(conn, employee_id)
    if profile and size < max(profile["left_size"], profile["right_size"]):
        raise BusinessError("发放码小于左右脚较大码，不合脚")
    if not latest_ok_fitting(conn, employee_id, model_code, size):
        raise BusinessError("该鞋款尺码未试穿确认，不能发放（请先完成试穿锁码）")
    # 有未完成的调岗换领单时，必须走“交旧领新”，不能绕过旧鞋直接在新等级发放
    open_transfer = conn.execute(
        """SELECT r.id FROM renewals r
           WHERE r.employee_id=? AND r.kind='transfer' AND r.status IN ('approved','pending')
             AND r.id <> COALESCE(?, -1)""",
        (employee_id, renewal_id)).fetchone()
    if open_transfer:
        raise BusinessError(
            f"该员工有未完成的调岗换领单（#{open_transfer['id']}），请在换领审核页交回旧鞋后领取新鞋")
    lifespan = conn.execute(
        "SELECT lifespan_months FROM protection_levels WHERE code=?", (emp["grade_code"],)).fetchone()[0]
    return emp, model, profile, lifespan


def issue_shoe(conn, employee_id: str, model_code: str, size: float, warehouse: str,
               actor: dict, *, replaced_id: int | None = None, now: date | None = None) -> int:
    """试穿确认后发放。两处仓库并发发放同一员工同一等级时，仅一次成功。

    返回 issuance id；缺码时登记候补并抛 BusinessError。
    """
    emp, model, profile, lifespan = _validate_issue(
        conn, employee_id, model_code, size, warehouse, actor)
    today = _today(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        outcome, ref = _issue_locked(
            conn, emp, model_code, size, warehouse, lifespan, today, profile, actor, replaced_id)
        if outcome == "ok":
            conn.commit()
            return ref
        if outcome == "missing":
            conn.commit()  # 候补登记保留
            raise BusinessError(f"{warehouse} 该规格缺码，已登记缺码候补（#{ref}），到货后通知")
        conn.rollback()
        raise BusinessError(f"重复领用拦截：该员工在 {emp['grade_code']} 等级已有有效鞋（#{ref}）")
    except BusinessError:
        raise
    except sqlite3.IntegrityError as e:
        # 部分唯一索引 ux_one_active_per_grade —— 并发下第二个发放在此失败
        conn.rollback()
        raise BusinessError("并发发放冲突：该员工此防护等级已有一双有效鞋，本次发放失败") from e
    except Exception:
        conn.rollback()
        raise


def list_issuances(conn, employee_id: str | None = None, status: str | None = None):
    q = """SELECT i.*, e.name AS employee_name, m.name AS model_name
           FROM issuances i
           JOIN employees e ON e.id=i.employee_id
           JOIN shoe_models m ON m.code=i.model_code WHERE 1=1"""
    args = []
    if employee_id:
        q += " AND i.employee_id=?"; args.append(employee_id)
    if status:
        q += " AND i.status=?"; args.append(status)
    return list(conn.execute(q + " ORDER BY i.id DESC", args))


# ---------------- 换领申请（到期 / 损坏提前 / 遗失豁免） ----------------

SLOT_NAMES = {"sole": "鞋底", "toe": "鞋头", "upper": "鞋帮"}


def create_renewal(conn, issuance_id: int, kind: str, reason: str,
                   photo_paths: dict | None, actor: dict, *, now: date | None = None) -> int:
    """kind=expiry 正常到期（自动可换）；kind=damage 损坏提前（主管审批，必须三照片）。"""
    iss = conn.execute("SELECT * FROM issuances WHERE id=?", (issuance_id,)).fetchone()
    if not iss:
        raise BusinessError("发放记录不存在")
    if iss["status"] != "active":
        raise BusinessError("该鞋已非有效状态，不能再申请换领")
    today = _today(now)

    existing = conn.execute(
        "SELECT id FROM renewals WHERE issuance_id=? AND status IN ('pending','approved','auto')",
        (issuance_id,)).fetchone()
    if existing:
        raise BusinessError("该鞋已有进行中的换领申请，禁止重复申请")

    if kind == "expiry":
        due = datetime.strptime(iss["due_date"], "%Y-%m-%d").date()
        if today < due:
            raise BusinessError(f"未到正常到期日（{iss['due_date']}），提前换请按损坏申请并由主管审批")
        status, supervisor = "auto", "系统（到期）"
    elif kind == "damage":
        photo_paths = photo_paths or {}
        for slot in ("sole", "toe", "upper"):
            if not photo_paths.get(slot):
                raise BusinessError(f"损坏提前换领缺少磨损照片：{SLOT_NAMES[slot]}")
        status, supervisor = "pending", ""
    else:
        raise BusinessError("换领类型只能是 expiry 或 damage（调岗由系统自动建单）")

    with _tx(conn):
        rid = conn.execute(
            """INSERT INTO renewals(issuance_id,employee_id,kind,reason,status,supervisor,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (issuance_id, iss["employee_id"], kind, reason, status, supervisor, now_str())).lastrowid
        if kind == "damage":
            for slot in ("sole", "toe", "upper"):
                conn.execute(
                    "INSERT INTO damage_photos(renewal_id,slot,file_path,created_at) VALUES(?,?,?,?)",
                    (rid, slot, photo_paths[slot], now_str()))
        _audit(conn, actor, "换领申请", f"renewal#{rid} issuance#{issuance_id} {kind}")
    return rid


def decide_renewal(conn, renewal_id: int, approve: bool, actor: dict,
                   grant_exemption: bool = False, exemption_reason: str = "") -> None:
    """主管审批提前（损坏）换领；可同时登记旧鞋遗失豁免。仓管无权审批。"""
    _require_role(actor, (ROLE_SUPERVISOR, ROLE_ADMIN), "换领审批")
    r = conn.execute("SELECT * FROM renewals WHERE id=?", (renewal_id,)).fetchone()
    if not r:
        raise BusinessError("换领单不存在")
    if r["status"] != "pending":
        raise BusinessError("该申请已审批，不能重复审批")
    if grant_exemption and not approve:
        raise BusinessError("驳回时不能授予豁免")
    if grant_exemption and exemption_reason not in ("lost", "other"):
        raise BusinessError("豁免必须登记原因：lost 遗失 / other 其他")
    with _tx(conn):
        conn.execute(
            """UPDATE renewals SET status=?, supervisor=?, decided_at=?,
                   old_shoe_action=?, exemption_reason=?, approved_by=? WHERE id=?""",
            ("approved" if approve else "rejected", actor["name"], now_str(),
             "exemption" if grant_exemption else r["old_shoe_action"],
             exemption_reason if grant_exemption else r["exemption_reason"],
             actor["name"] if grant_exemption else r["approved_by"], renewal_id))
        _audit(conn, actor, "换领审批",
               f"renewal#{renewal_id} {'批准' if approve else '驳回'}"
               + ("，旧鞋豁免(" + exemption_reason + ")" if grant_exemption else ""))


# ---------------- 领取新鞋：必须交回旧鞋或登记豁免 ----------------

def pickup_renewal(conn, renewal_id: int, model_code: str, size: float, warehouse: str,
                   old_condition: str, actor: dict, *, now: date | None = None) -> int:
    """领取新鞋。旧鞋：主管已授豁免 → 登记豁免；否则当场回收
    （reusable 进清洁复检 / scrap 进待处置批次）。整笔事务，失败不留半截记录。"""
    _require_role(actor, (ROLE_KEEPER, ROLE_ADMIN), "发放")
    r = conn.execute("SELECT * FROM renewals WHERE id=?", (renewal_id,)).fetchone()
    if not r:
        raise BusinessError("换领单不存在")
    if r["status"] == "completed":
        raise BusinessError("该换领单已完成领取，禁止重复领用")
    if r["status"] not in ("approved", "auto"):
        raise BusinessError("换领申请未批准，不能领取新鞋")
    if old_condition not in ("reusable", "scrap"):
        raise BusinessError("旧鞋状况必须是 reusable 可再用 或 scrap 报废")

    emp, model, profile, lifespan = _validate_issue(
        conn, r["employee_id"], model_code, size, warehouse, actor, renewal_id=renewal_id)
    old = conn.execute("SELECT * FROM issuances WHERE id=?", (r["issuance_id"],)).fetchone()
    exempt = r["old_shoe_action"] == "exemption" and r["approved_by"]
    if exempt and not r["exemption_reason"]:
        raise BusinessError("豁免缺少原因登记")

    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1) 重复领用校验 + 原子抢批次（先抢库存，避免留下半截回收记录）
        #    排除正在换领交回的旧鞋本身
        dup = conn.execute(
            "SELECT id FROM issuances WHERE employee_id=? AND grade_code=? AND status='active' AND id<>?",
            (emp["id"], emp["grade_code"], old["id"])).fetchone()
        if dup:
            raise BusinessError(
                f"重复领用拦截：该员工在 {emp['grade_code']} 等级已有有效鞋（#{dup['id']}）")
        batches = conn.execute(
            """SELECT id FROM inventory_batches
               WHERE model_code=? AND size=? AND warehouse=? AND qty_used<qty_total
               ORDER BY id""", (model_code, size, warehouse)).fetchall()
        batch_id = None
        for b in batches:
            cur = conn.execute(
                "UPDATE inventory_batches SET qty_used=qty_used+1 WHERE id=? AND qty_used<qty_total",
                (b["id"],))
            if cur.rowcount == 1:
                batch_id = b["id"]
                break
        if batch_id is None:
            conn.rollback()
            with _tx(conn):
                wid = _join_waitlist(conn, emp["id"], model_code, size)
            raise BusinessError(
                f"{warehouse} 该规格缺码，已登记缺码候补（#{wid}），到货后再领取")

        # 2) 旧鞋有着落：回收（可用→清洁复检 / 报废→待处置批次）或豁免
        if not exempt:
            return_id = conn.execute(
                "INSERT INTO returns_log(issuance_id,renewal_id,condition,received_at,warehouse) VALUES(?,?,?,?,?)",
                (old["id"], renewal_id, old_condition, now_str(), warehouse)).lastrowid
            if old_condition == "reusable":
                conn.execute(
                    "INSERT INTO recondition(return_id,status) VALUES(?,'pending')", (return_id,))
            else:
                batch = _ensure_pending_disposal(conn)
                conn.execute(
                    "INSERT INTO disposal_items(disposal_id,return_id,model_code,size) VALUES(?,?,?,?)",
                    (batch, return_id, old["model_code"], old["size"]))

        # 3) 先交回旧鞋（离开“有效”唯一索引），再插同等级新鞋，始终保持一双有效
        conn.execute(
            "UPDATE issuances SET status=?, old_returned_at=? WHERE id=?",
            ("exemption" if exempt else "returned", now_str(), old["id"]))

        # 4) 在同一事务内发新鞋
        today = _today(now)
        due = add_months(today, lifespan)
        size_detail = f"L{profile['left_size']:g}/R{profile['right_size']:g}" if profile else ""
        try:
            new_id = conn.execute(
                """INSERT INTO issuances(employee_id,grade_code,model_code,size,size_detail,batch_id,
                      warehouse,issued_at,lifespan_months,due_date,replaced_id,status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'active')""",
                (emp["id"], emp["grade_code"], model_code, size, size_detail, batch_id,
                 warehouse, today.strftime("%Y-%m-%d"), lifespan,
                 due.strftime("%Y-%m-%d"), old["id"])).lastrowid
        except sqlite3.IntegrityError as e2:
            raise BusinessError(
                "并发发放冲突：该员工此防护等级已有一双有效鞋，本次领取失败") from e2
        conn.execute(
            "UPDATE waitlist SET status='filled' WHERE employee_id=? AND model_code=? AND size=? AND status='waiting'",
            (emp["id"], model_code, size))

        # 5) 落定换领单
        conn.execute(
            "UPDATE renewals SET status='completed', new_issuance_id=? WHERE id=?",
            (new_id, renewal_id))
        _audit(conn, actor, "换领领取",
               f"renewal#{renewal_id} new#{new_id} 旧鞋{'豁免:'+r['exemption_reason'] if exempt else old_condition}")
        conn.commit()
        return new_id
    except BusinessError:
        # 缺码分支已自行 rollback；其他分支若事务仍在则回滚
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise BusinessError("并发发放冲突：该员工此防护等级已有一双有效鞋，本次领取失败") from e
    except Exception:
        conn.rollback()
        raise


# ---------------- 清洁复检 / 报废处置 ----------------

def list_returns(conn):
    return list(conn.execute(
        """SELECT rl.*, e.name AS employee_name, m.name AS model_name,
                  rc.status AS recon_status, rc.id AS recon_id
           FROM returns_log rl
           JOIN issuances i ON i.id=rl.issuance_id
           JOIN employees e ON e.id=i.employee_id
           JOIN shoe_models m ON m.code=i.model_code
           LEFT JOIN recondition rc ON rc.return_id=rl.id
           ORDER BY rl.id DESC"""))


def inspect_return(conn, return_id: int, passed: bool, note: str, actor: dict) -> None:
    _require_role(actor, (ROLE_KEEPER, ROLE_ADMIN), "清洁复检")
    rc = conn.execute("SELECT * FROM recondition WHERE return_id=?", (return_id,)).fetchone()
    rl = conn.execute("SELECT * FROM returns_log WHERE id=?", (return_id,)).fetchone()
    if not rc:
        raise BusinessError("该回收鞋不在复检队列（报废鞋不做复检）")
    if rc["status"] != "pending":
        raise BusinessError("已复检，不能重复登记")
    with _tx(conn):
        if not passed:
            # 复检不合格 → 转入待处置批次
            batch = _ensure_pending_disposal(conn)
            iss = conn.execute("SELECT * FROM issuances WHERE id=?", (rl["issuance_id"],)).fetchone()
            conn.execute(
                "INSERT INTO disposal_items(disposal_id,return_id,model_code,size) VALUES(?,?,?,?)",
                (batch, return_id, iss["model_code"], iss["size"]))
            conn.execute("UPDATE returns_log SET condition='scrap' WHERE id=?", (return_id,))
        conn.execute(
            "UPDATE recondition SET status=?, inspector=?, inspected_at=?, note=? WHERE return_id=?",
            ("passed" if passed else "failed", actor["name"], now_str(), note, return_id))
        _audit(conn, actor, "清洁复检", f"return#{return_id} {'合格' if passed else '不合格转处置'}")


def _ensure_pending_disposal(conn) -> int:
    row = conn.execute(
        "SELECT id FROM disposals WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO disposals(batch_no,status,created_at) VALUES(?,?,?)",
        (f"SCRAP-{datetime.now().strftime('%Y%m')}-待处置", "pending", now_str())).lastrowid


def list_disposals(conn):
    out = []
    for d in conn.execute("SELECT * FROM disposals ORDER BY id DESC"):
        items = list(conn.execute("SELECT * FROM disposal_items WHERE disposal_id=?", (d["id"],)))
        out.append({**dict(d), "items": [dict(i) for i in items]})
    return out


def execute_disposal(conn, disposal_id: int, actor: dict) -> str:
    """模拟处置厂商取走待处置批次，返回回执号。"""
    _require_role(actor, (ROLE_KEEPER, ROLE_ADMIN), "处置出厂")
    d = conn.execute("SELECT * FROM disposals WHERE id=?", (disposal_id,)).fetchone()
    if not d:
        raise BusinessError("处置批次不存在")
    if d["status"] != "pending":
        raise BusinessError("该批次已处置，回执号：" + d["receipt_no"])
    items = conn.execute("SELECT COUNT(*) c FROM disposal_items WHERE disposal_id=?",
                         (disposal_id,)).fetchone()["c"]
    if items == 0:
        raise BusinessError("空批次不能处置")
    receipt = DisposalVendor().process(d["batch_no"], items)
    with _tx(conn):
        conn.execute(
            "UPDATE disposals SET status='done', vendor=?, receipt_no=?, done_at=? WHERE id=?",
            (receipt["vendor"], receipt["receipt_no"], now_str(), disposal_id))
        _audit(conn, actor, "报废处置", f"batch#{disposal_id} 回执 {receipt['receipt_no']}")
    return receipt["receipt_no"]


# ---------------- 到期队列 / 候补 / 提醒 ----------------

def due_queue(conn, *, now: date | None = None, days: int = 30):
    today = _today(now)
    horizon = today + timedelta(days=days)
    return list(conn.execute(
        """SELECT i.*, e.name AS employee_name, e.post, m.name AS model_name,
                  CASE WHEN i.due_date < ? THEN 1 ELSE 0 END AS overdue
           FROM issuances i
           JOIN employees e ON e.id=i.employee_id
           JOIN shoe_models m ON m.code=i.model_code
           WHERE i.status='active' AND i.due_date <= ?
           ORDER BY i.due_date""",
        (today.strftime("%Y-%m-%d"), horizon.strftime("%Y-%m-%d"))))


def list_waitlist(conn):
    return list(conn.execute(
        """SELECT w.*, e.name AS employee_name, m.name AS model_name
           FROM waitlist w
           JOIN employees e ON e.id=w.employee_id
           JOIN shoe_models m ON m.code=w.model_code
           ORDER BY (w.status='waiting') DESC, w.id"""))


def list_renewals(conn, status: str | None = None):
    q = """SELECT r.*, e.name AS employee_name, i.model_code, i.size, m.name AS model_name,
                  (SELECT COUNT(*) FROM damage_photos p WHERE p.renewal_id=r.id) AS photo_count
           FROM renewals r
           JOIN employees e ON e.id=r.employee_id
           JOIN issuances i ON i.id=r.issuance_id
           JOIN shoe_models m ON m.code=i.model_code"""
    if status:
        return list(conn.execute(q + " WHERE r.status=? ORDER BY r.id DESC", (status,)))
    return list(conn.execute(q + " ORDER BY r.id DESC"))


def get_renewal(conn, renewal_id: int):
    r = conn.execute(
        """SELECT r.*, e.name AS employee_name, m.name AS model_name
           FROM renewals r
           JOIN employees e ON e.id=r.employee_id
           JOIN shoe_models m ON m.code=(SELECT model_code FROM issuances WHERE id=r.issuance_id)
           WHERE r.id=?""", (renewal_id,)).fetchone()
    if not r:
        raise BusinessError("换领单不存在")
    photos = list(conn.execute("SELECT * FROM damage_photos WHERE renewal_id=?", (renewal_id,)))
    iss = conn.execute("SELECT * FROM issuances WHERE id=?", (r["issuance_id"],)).fetchone()
    return dict(r), [dict(p) for p in photos], dict(iss)


def generate_reminders(conn, *, now: date | None = None) -> dict:
    """进程内任务周期调用：到期提醒 + 候补到货提醒。"""
    today = _today(now)
    horizon = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    today_s = today.strftime("%Y-%m-%d")
    created = {"due": 0, "waitlist": 0}
    with _tx(conn):
        for i in conn.execute(
                """SELECT i.*, e.name FROM issuances i JOIN employees e ON e.id=i.employee_id
                   WHERE i.status='active' AND i.due_date<=?
                     AND NOT EXISTS (SELECT 1 FROM reminders rm
                                     WHERE rm.kind='due' AND rm.ref_type='issuance' AND rm.ref_id=i.id)""",
                (horizon,)):
            msg = (f"{i['employee_id']} {i['name']} 的 {i['model_code']} {i['size']:g} 码"
                   + ("已到期" if i["due_date"] < today_s else f"将于 {i['due_date']} 到期")
                   + "，请安排换领")
            conn.execute(
                "INSERT INTO reminders(kind,ref_type,ref_id,message,created_at) VALUES('due','issuance',?,?,?)",
                (i["id"], msg, now_str()))
            created["due"] += 1

        for w in conn.execute("SELECT * FROM waitlist WHERE status='waiting'"):
            if stock_qty(conn, w["model_code"], w["size"]) > 0:
                conn.execute(
                    "UPDATE waitlist SET status='notified', notified_at=? WHERE id=?",
                    (now_str(), w["id"]))
                conn.execute(
                    "INSERT INTO reminders(kind,ref_type,ref_id,message,created_at) VALUES('waitlist','waitlist',?,?,?)",
                    (w["id"], f"候补到货：{w['employee_id']} 登记的 {w['model_code']} {w['size']:g} 码已入库，请通知试穿",
                     now_str()))
                created["waitlist"] += 1
    return created


def list_reminders(conn):
    return list(conn.execute("SELECT * FROM reminders ORDER BY read, id DESC LIMIT 100"))
