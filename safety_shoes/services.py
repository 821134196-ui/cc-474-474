"""核心业务服务：资格、试穿发放、到期/损坏换领、旧鞋回收、候补、调岗。

所有写操作都在 BEGIN IMMEDIATE 事务内完成并复查库存/唯一约束，
两个仓库进程并发发放同一双鞋时只有一个事务能成功。
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config
from .db import get_connection, transaction


class ServiceError(Exception):
    """业务规则拒绝。"""


class PermissionDenied(ServiceError):
    """角色权限不足（如仓管修改岗位等级）。"""


# ---------------------------------------------------------------- 日期工具

def add_months(d: date, months: int) -> date:
    """发放日 + 使用期限月数 = 到期日（月底截断，如 2/29 +12月 -> 2/28）。"""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def days_to_expire(expire_date: str, today: date | None = None) -> int:
    today = today or date.today()
    return (datetime.strptime(expire_date, "%Y-%m-%d").date() - today).days


# ---------------------------------------------------------------- 服务

class ShoeService:
    def __init__(self, db_path: Path | str | None = None, conn: sqlite3.Connection | None = None):
        self._owns_conn = conn is None
        self.conn = conn or get_connection(db_path)

    def close(self):
        if self._owns_conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 审计 & 权限 ----------------------------------------------------

    def _audit(self, actor: str, role: str, action: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO audit_log(actor, role, action, detail) VALUES(?,?,?,?)",
            (actor, role, action, detail),
        )

    @staticmethod
    def _require_role(role: str, allowed: tuple[str, ...], action: str):
        if role not in allowed:
            raise PermissionDenied(f"只有{'/'.join(config.ROLE_NAMES[r] for r in allowed)}可以{action}")

    # ---- 员工 / 岗位授权 ------------------------------------------------

    def list_employees(self, include_inactive: bool = False):
        sql = "SELECT * FROM employees"
        if not include_inactive:
            sql += " WHERE active=1"
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY emp_no")]

    def get_employee(self, emp_no: str):
        row = self.conn.execute("SELECT * FROM employees WHERE emp_no=?", (emp_no,)).fetchone()
        if not row:
            raise ServiceError(f"工号 {emp_no} 不存在")
        return dict(row)

    def add_employee(self, actor: str, role: str, emp_no: str, name: str,
                     position: str, level: str):
        self._require_role(role, ("supervisor", "storekeeper"), "员工建档")
        if level not in config.PROTECTION_LEVELS:
            raise ServiceError(f"非法防护等级：{level}")
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO employees(emp_no,name,position,level) VALUES(?,?,?,?)",
                (emp_no, name, position, level))
            self._audit(actor, role, "add_employee", f"{emp_no} {name} {level}")

    def get_profile(self, emp_no: str):
        self.get_employee(emp_no)
        row = self.conn.execute(
            "SELECT * FROM size_profiles WHERE emp_no=?", (emp_no,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_profile(self, actor: str, role: str, emp_no: str,
                       size_left: str, size_right: str, width: str = "M", note: str = ""):
        self._require_role(role, ("supervisor", "storekeeper"), "维护尺码档案")
        for s in (size_left, size_right):
            if s not in config.SIZES:
                raise ServiceError(f"非法尺码：{s}")
        with transaction(self.conn):
            self.conn.execute(
                """INSERT INTO size_profiles(emp_no,size_left,size_right,width,note,updated_at)
                   VALUES(?,?,?,?,?,datetime('now','localtime'))
                   ON CONFLICT(emp_no) DO UPDATE SET
                     size_left=excluded.size_left, size_right=excluded.size_right,
                     width=excluded.width, note=excluded.note,
                     updated_at=datetime('now','localtime')""",
                (emp_no, size_left, size_right, width, note),
            )
            self._audit(actor, role, "upsert_profile", f"{emp_no} {size_left}/{size_right} {width}")

    def change_position(self, actor: str, role: str, emp_no: str,
                        to_position: str, to_level: str):
        """岗位调动 / 修改防护等级 —— 仅主管。仓管无权改等级。"""
        self._require_role(role, ("supervisor",), "调整岗位与防护等级")
        if to_level not in config.PROTECTION_LEVELS:
            raise ServiceError(f"非法防护等级：{to_level}")
        emp = self.get_employee(emp_no)
        if emp["position"] == to_position and emp["level"] == to_level:
            return
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE employees SET position=?, level=? WHERE emp_no=?",
                (to_position, to_level, emp_no),
            )
            if emp["level"] != to_level:
                self.conn.execute(
                    """INSERT INTO transfers(emp_no,from_level,to_level,from_position,to_position)
                       VALUES(?,?,?,?,?)""",
                    (emp_no, emp["level"], to_level, emp["position"], to_position),
                )
            self._audit(actor, role, "change_position",
                        f"{emp_no} {emp['level']}->{to_level}")

    def eligible_models(self, level: str):
        """岗位授权后生成可领规格：覆盖该防护等级的全部鞋款。"""
        return [
            {"code": name.split(" ")[0], "name": name}
            for name, levels in config.MODEL_LEVELS.items()
            if level in levels
        ]

    # ---- 鞋款 / 库存 ----------------------------------------------------

    def list_models(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM shoe_models ORDER BY code")]

    def stock_on_hand(self, warehouse: str, model_code: str, size: str) -> int:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(qty_on_hand),0) AS q FROM inventory
               WHERE warehouse=? AND model_code=? AND size=? AND qty_on_hand>0""",
            (warehouse, model_code, size),
        ).fetchone()
        return int(row["q"])

    def inventory_matrix(self, warehouse: str):
        """{model_code: {size: qty}} 二维矩阵 + 异码混码单元。"""
        matrix: dict[str, dict[str, int]] = {}
        for r in self.conn.execute(
            """SELECT model_code, size, SUM(qty_on_hand) AS q
               FROM inventory WHERE warehouse=? GROUP BY model_code, size""",
            (warehouse,),
        ):
            matrix.setdefault(r["model_code"], {})[r["size"]] = int(r["q"])
        return matrix

    def restock(self, actor: str, role: str, warehouse: str, model_code: str,
                size: str, qty: int, batch_no: str | None = None):
        self._require_role(role, ("supervisor", "storekeeper"), "入库")
        if qty <= 0:
            raise ServiceError("入库数量必须大于 0")
        if not self.conn.execute("SELECT 1 FROM shoe_models WHERE code=?", (model_code,)).fetchone():
            raise ServiceError("鞋款不存在")
        batch_no = batch_no or f"B{date.today():%Y%m%d}-{warehouse}-{model_code}-{size}"
        with transaction(self.conn):
            row = self.conn.execute(
                "SELECT id,qty_total,qty_on_hand FROM inventory WHERE warehouse=? AND model_code=? AND size=? AND batch_no=?",
                (warehouse, model_code, size, batch_no),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE inventory SET qty_total=qty_total+?, qty_on_hand=qty_on_hand+?, updated_at=datetime('now','localtime') WHERE id=?",
                    (qty, qty, row["id"]),
                )
            else:
                self.conn.execute(
                    """INSERT INTO inventory(warehouse,model_code,size,batch_no,qty_total,qty_on_hand)
                       VALUES(?,?,?,?,?,?)""",
                    (warehouse, model_code, size, batch_no, qty, qty),
                )
            self._audit(actor, role, "restock", f"{warehouse} {model_code} {size} +{qty}")

    def _deduct_one(self, conn, warehouse: str, model_code: str, size: str) -> int:
        """从最早批次扣 1 双，返回库存行 id（调用方已持写锁）。"""
        row = conn.execute(
            """SELECT id, qty_on_hand FROM inventory
               WHERE warehouse=? AND model_code=? AND size=? AND qty_on_hand>0
               ORDER BY id LIMIT 1""",
            (warehouse, model_code, size),
        ).fetchone()
        if not row:
            raise ServiceError(f"库存不足：{warehouse} {model_code} {size} 缺码")
        conn.execute("UPDATE inventory SET qty_on_hand=qty_on_hand-1 WHERE id=?", (row["id"],))
        return int(row["id"])

    def _consume_pair(self, conn, warehouse: str, model_code: str,
                      size_left: str, size_right: str) -> int:
        """扣一双鞋；左右脚异码时从两个尺码各取一只，并登记一只异码回笼鞋。"""
        if size_left == size_right:
            return self._deduct_one(conn, warehouse, model_code, size_left)
        inv_l = self._deduct_one(conn, warehouse, model_code, size_left)
        try:
            self._deduct_one(conn, warehouse, model_code, size_right)
        except ServiceError:
            raise
        # 两双各取一只，余下两只合成一双异码鞋回到 MIX 库位
        mix_size = f"{min(size_left, size_right)}/{max(size_left, size_right)}"
        mix_batch = "MIX-POOL"
        row = conn.execute(
            "SELECT id FROM inventory WHERE warehouse=? AND model_code=? AND size=? AND batch_no=?",
            (warehouse, model_code, mix_size, mix_batch),
        ).fetchone()
        if row:
            conn.execute("UPDATE inventory SET qty_on_hand=qty_on_hand+1 WHERE id=?", (row["id"],))
        else:
            conn.execute(
                """INSERT INTO inventory(warehouse,model_code,size,batch_no,qty_total,qty_on_hand)
                   VALUES(?,?,?,?,?,?)""",
                (warehouse, model_code, mix_size, mix_batch, 1, 1),
            )
        return inv_l

    # ---- 有效鞋 & 换领资格 ----------------------------------------------

    def active_issue(self, emp_no: str, level: str | None = None):
        sql = "SELECT * FROM issues WHERE emp_no=? AND status='active'"
        params: list = [emp_no]
        if level:
            sql += " AND level=?"
            params.append(level)
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None

    def list_active_issues(self):
        return [dict(r) for r in self.conn.execute(
            """SELECT i.*, e.name, e.position FROM issues i
               JOIN employees e ON e.emp_no=i.emp_no
               WHERE i.status='active' ORDER BY i.expire_date""")]

    def _next_doc_no(self, prefix: str, table: str, col: str) -> str:
        n = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] + 1
        return f"{prefix}-{date.today():%Y%m%d}-{n:04d}"

    # ---- 试穿 -----------------------------------------------------------

    def start_fitting(self, actor: str, role: str, emp_no: str, model_code: str,
                      size_left: str, size_right: str, renewal_id: int | None = None):
        self._require_role(role, ("supervisor", "storekeeper"), "发起试穿")
        emp = self.get_employee(emp_no)
        model = self.conn.execute("SELECT * FROM shoe_models WHERE code=?", (model_code,)).fetchone()
        if not model:
            raise ServiceError("鞋款不存在")
        if emp["level"] not in config.MODEL_LEVELS.get(f"{model['code']} {model['name']}", []):
            raise ServiceError(f"鞋款 {model_code} 不适用于岗位防护等级 {emp['level']}")
        for s in (size_left, size_right):
            if s not in config.SIZES:
                raise ServiceError(f"非法尺码：{s}")

        active = self.active_issue(emp_no, emp["level"])
        renewal = None
        with transaction(self.conn):
            if active:
                # 有效鞋存在：必须持有已批准的换领单才能试新鞋（防重复领用）
                if renewal_id is None:
                    raise ServiceError("该员工在此防护等级已有一双有效鞋，需先创建到期/损坏换领")
                renewal = self.conn.execute(
                    "SELECT * FROM renewals WHERE id=?", (renewal_id,)
                ).fetchone()
                if not renewal or renewal["emp_no"] != emp_no or renewal["status"] != "approved":
                    raise ServiceError("换领单不存在或尚未批准")
                if renewal["old_issue_id"] != active["id"]:
                    raise ServiceError("换领单与当前有效鞋不匹配")
            elif renewal_id is not None:
                raise ServiceError("无有效鞋时应直接首发登记，不应挂换领单")

            cur = self.conn.execute(
                """INSERT INTO fittings(emp_no,level,model_code,size_left,size_right,
                                        final_size_left,final_size_right,created_by,renewal_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (emp_no, emp["level"], model_code, size_left, size_right,
                 size_left, size_right, actor, renewal_id),
            )
            fid = cur.lastrowid
            self._audit(actor, role, "start_fitting", f"fitting#{fid} {emp_no}")
            return fid

    def adjust_fitting(self, actor: str, role: str, fitting_id: int,
                       size_left: str, size_right: str):
        """试穿调码：记录调码历史，更新待确认尺码。"""
        self._require_role(role, ("supervisor", "storekeeper"), "试穿调码")
        for s in (size_left, size_right):
            if s not in config.SIZES:
                raise ServiceError(f"非法尺码：{s}")
        with transaction(self.conn):
            f = self.conn.execute("SELECT * FROM fittings WHERE id=?", (fitting_id,)).fetchone()
            if not f or f["status"] not in ("pending", "adjusted"):
                raise ServiceError("试穿单不存在或已结束")
            history = (f["tried_sizes"] or "")
            history += (";" if history else "") + f"{f['final_size_left']}/{f['final_size_right']}"
            self.conn.execute(
                """UPDATE fittings SET final_size_left=?, final_size_right=?,
                                       tried_sizes=?, status='adjusted' WHERE id=?""",
                (size_left, size_right, history, fitting_id),
            )
            self._audit(actor, role, "adjust_fitting",
                        f"fitting#{fitting_id} ->{size_left}/{size_right}")

    def get_fitting(self, fitting_id: int):
        row = self.conn.execute("SELECT * FROM fittings WHERE id=?", (fitting_id,)).fetchone()
        if not row:
            raise ServiceError("试穿单不存在")
        return dict(row)

    def confirm_fitting(self, actor: str, role: str, fitting_id: int, warehouse: str) -> int:
        """试穿确认：锁定尺码、扣减库存、生成发放单。并发安全。"""
        self._require_role(role, ("supervisor", "storekeeper"), "确认发放")
        if warehouse not in config.WAREHOUSES:
            raise ServiceError("仓库非法")

        with transaction(self.conn):  # BEGIN IMMEDIATE —— 两处仓库并发在此串行
            f = self.conn.execute("SELECT * FROM fittings WHERE id=?", (fitting_id,)).fetchone()
            if not f:
                raise ServiceError("试穿单不存在")
            if f["status"] not in ("pending", "adjusted"):
                raise ServiceError("试穿单已结束，不能重复确认")
            sl, sr = f["final_size_left"], f["final_size_right"]

            # 有效鞋唯一复查（与部分唯一索引双保险；挡住重复领用）
            exists = self.conn.execute(
                "SELECT 1 FROM issues WHERE emp_no=? AND level=? AND status='active'",
                (f["emp_no"], f["level"]),
            ).fetchone()

            renewal = None
            old_issue = None
            renewal_id = f["renewal_id"]
            if renewal_id:
                renewal = self.conn.execute(
                    "SELECT * FROM renewals WHERE id=?", (renewal_id,)).fetchone()
                if not renewal or renewal["status"] != "approved":
                    raise ServiceError("换领单未批准，不能发新鞋")
                old_issue = self.conn.execute(
                    "SELECT * FROM issues WHERE id=?", (renewal["old_issue_id"],)).fetchone()
                if not old_issue or old_issue["status"] != "active":
                    raise ServiceError("旧鞋发放单状态异常")
                # 领取新鞋前：旧鞋必须已交回，或豁免已审批
                ret = self.conn.execute(
                    """SELECT * FROM returns WHERE issue_id=?
                       ORDER BY id DESC LIMIT 1""",
                    (old_issue["id"],),
                ).fetchone()
                if not ret or not (
                    ret["returned"] == 1
                    or (ret["exemption"] == 1 and ret["approve_status"] == "approved")
                ):
                    raise ServiceError("旧鞋尚未交回，且无已批准的遗失豁免，不能发新鞋")
                # 先释放同等级旧鞋的 active 占位，再插新发放单（满足部分唯一索引）
                self.conn.execute(
                    "UPDATE issues SET status='renewed' WHERE id=?", (old_issue["id"],))
            elif exists:
                raise ServiceError("该员工在此防护等级已有有效鞋，不能重复领用")

            # 扣库存（不足直接抛错，整体回滚）
            inventory_id = self._consume_pair(self.conn, warehouse, f["model_code"], sl, sr)

            model = self.conn.execute(
                "SELECT * FROM shoe_models WHERE code=?", (f["model_code"],)).fetchone()
            issue_date = date.today()
            expire = add_months(issue_date, int(model["lifespan_months"]))
            issue_no = self._next_doc_no("ISS", "issues", "issue_no")

            cur = self.conn.execute(
                """INSERT INTO issues(issue_no,emp_no,level,model_code,size_left,size_right,
                                      warehouse,inventory_id,fitting_id,issue_date,expire_date,
                                      status,pair_tag)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?, 'active',?)""",
                (issue_no, f["emp_no"], f["level"], f["model_code"], sl, sr,
                 warehouse, inventory_id, fitting_id, issue_date.isoformat(),
                 expire.isoformat(), "PENDING"),
            )
            issue_id = cur.lastrowid
            pair_tag = f"SN-{issue_id:05d}"
            self.conn.execute("UPDATE issues SET pair_tag=? WHERE id=?", (pair_tag, issue_id))

            self.conn.execute(
                """UPDATE fittings SET status='confirmed', confirmed_at=datetime('now','localtime')
                   WHERE id=?""",
                (fitting_id,),
            )

            if renewal is not None:
                self.conn.execute(
                    """UPDATE renewals SET status='completed', completed_issue_id=?,
                                           approved_at=COALESCE(approved_at, datetime('now','localtime'))
                       WHERE id=?""",
                    (issue_id, renewal["id"]),
                )
            # 兑现任意候补（同员工同鞋款）
            for w in self.conn.execute(
                """SELECT id FROM waitlist
                   WHERE emp_no=? AND model_code=? AND status IN ('waiting','notified')""",
                (f["emp_no"], f["model_code"]),
            ):
                self.conn.execute("UPDATE waitlist SET status='fulfilled' WHERE id=?", (w["id"],))

            self._audit(actor, role, "confirm_fitting",
                        f"{issue_no} {f['emp_no']} {f['model_code']} {sl}/{sr} @{warehouse}")
            return issue_id

    def get_issue(self, issue_id: int):
        row = self.conn.execute(
            """SELECT i.*, e.name, e.position FROM issues i
               JOIN employees e ON e.emp_no=i.emp_no WHERE i.id=?""",
            (issue_id,),
        ).fetchone()
        if not row:
            raise ServiceError("发放单不存在")
        return dict(row)

    # ---- 到期 / 损坏 / 提前磨损 换领 ------------------------------------

    def create_due_renewal(self, actor: str, role: str, emp_no: str):
        """正常到期：距到期 <= 阈值才可建单，建成即批准。"""
        self._require_role(role, ("supervisor", "storekeeper"), "登记到期换领")
        active = self.active_issue(emp_no)
        if not active:
            raise ServiceError("没有有效鞋，应走首发试穿")
        d = days_to_expire(active["expire_date"])
        if d > config.EARLY_RENEW_THRESHOLD_DAYS:
            raise ServiceError(
                f"未到换领窗口（还剩 {d} 天，提前 {config.EARLY_RENEW_THRESHOLD_DAYS} 天内才可到期换领）")
        with transaction(self.conn):
            dup = self.conn.execute(
                "SELECT 1 FROM renewals WHERE old_issue_id=? AND status IN ('requested','approved')",
                (active["id"],)).fetchone()
            if dup:
                raise ServiceError("该鞋已有进行中的换领单")
            no = self._next_doc_no("REN", "renewals", "renewal_no")
            cur = self.conn.execute(
                """INSERT INTO renewals(renewal_no,emp_no,level,old_issue_id,reason,
                                        status,supervisor,approved_at)
                   VALUES(?,?,?,?,?, 'approved', '规则到期', datetime('now','localtime'))""",
                (no, emp_no, active["level"], active["id"], "due"),
            )
            self._audit(actor, role, "create_due_renewal", f"{no} {emp_no}")
            return cur.lastrowid

    def create_damage_renewal(self, actor: str, role: str, emp_no: str,
                              reason: str, description: str,
                              photos: dict[str, str]) -> int:
        """损坏申请 / 提前磨损：三个固定照片位（鞋底 sole/鞋头 toe/鞋帮 upper），主管审批。"""
        self._require_role(role, ("supervisor", "storekeeper"), "提交损坏/磨损申请")
        if reason not in ("damage", "early_wear"):
            raise ServiceError("原因必须是 damage 或 early_wear")
        active = self.active_issue(emp_no)
        if not active:
            raise ServiceError("没有有效鞋，不存在损坏换领")
        for slot in ("sole", "toe", "upper"):
            if not photos.get(slot):
                raise ServiceError("鞋底、鞋头、鞋帮三张照片必须齐全")
        with transaction(self.conn):
            dup = self.conn.execute(
                "SELECT 1 FROM renewals WHERE old_issue_id=? AND status IN ('requested','approved')",
                (active["id"],)).fetchone()
            if dup:
                raise ServiceError("该鞋已有进行中的换领单")
            no = self._next_doc_no("REN", "renewals", "renewal_no")
            rid = self.conn.execute(
                """INSERT INTO renewals(renewal_no,emp_no,level,old_issue_id,reason,status)
                   VALUES(?,?,?,?,?, 'requested')""",
                (no, emp_no, active["level"], active["id"], reason),
            ).lastrowid
            self.conn.execute(
                """INSERT INTO damage_claims(renewal_id,emp_no,photo_sole,photo_toe,photo_upper,description)
                   VALUES(?,?,?,?,?,?)""",
                (rid, emp_no, photos["sole"], photos["toe"], photos["upper"], description),
            )
            self._audit(actor, role, "create_damage_renewal",
                        f"{no} {emp_no} {reason}")
            return rid

    def approve_renewal(self, actor: str, role: str, renewal_id: int,
                        approved: bool, note: str = ""):
        """主管审批损坏/提前磨损换领。"""
        self._require_role(role, ("supervisor",), "审批换领")
        with transaction(self.conn):
            r = self.conn.execute("SELECT * FROM renewals WHERE id=?", (renewal_id,)).fetchone()
            if not r:
                raise ServiceError("换领单不存在")
            if r["status"] != "requested":
                raise ServiceError("该换领单不在待审批状态")
            self.conn.execute(
                """UPDATE renewals SET status=?, supervisor=?, approve_note=?,
                                       approved_at=datetime('now','localtime') WHERE id=?""",
                ("approved" if approved else "rejected", actor, note, renewal_id),
            )
            self._audit(actor, role, "approve_renewal",
                        f"renewal#{renewal_id} ->{'批准' if approved else '驳回'}")

    def list_renewals(self, status: str | None = None):
        sql = ("SELECT r.*, i.pair_tag, i.model_code, i.expire_date, c.photo_sole, c.photo_toe,"
               " c.photo_upper, c.description AS claim_desc, e.name"
               " FROM renewals r"
               " JOIN issues i ON i.id=r.old_issue_id"
               " JOIN employees e ON e.emp_no=r.emp_no"
               " LEFT JOIN damage_claims c ON c.renewal_id=r.id")
        if status:
            sql += " WHERE r.status=?"
            rows = self.conn.execute(sql + " ORDER BY r.id", (status,))
        else:
            rows = self.conn.execute(sql + " ORDER BY r.id")
        return [dict(r) for r in rows]

    # ---- 旧鞋回收 / 豁免 ------------------------------------------------

    def register_return(self, actor: str, role: str, issue_id: int,
                        returned: bool, condition: str = "",
                        exemption_reason: str = "", renewal_id: int | None = None):
        """交回旧鞋（可用->清洁复检；报废->待处置），或登记豁免（需主管审批）。"""
        self._require_role(role, ("supervisor", "storekeeper"), "回收登记")
        issue = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if not issue:
            raise ServiceError("发放单不存在")
        with transaction(self.conn):
            self._register_return_locked(
                actor, role, dict(issue), returned, condition, exemption_reason, renewal_id)

    def _register_return_locked(self, actor, role, issue, returned: bool,
                                condition: str, exemption_reason: str, renewal_id):
        """调用方已持有写锁。"""
        issue_id = issue["id"]
        dup = self.conn.execute(
            "SELECT 1 FROM returns WHERE issue_id=?", (issue_id,)).fetchone()
        if dup:
            raise ServiceError("该鞋已做过回收登记")
        if returned:
            if condition not in ("reusable", "scrap"):
                raise ServiceError("需指明旧鞋品相：reusable 或 scrap")
            status = "cleaning_inspection" if condition == "reusable" else "disposal_pending"
            self.conn.execute(
                """INSERT INTO returns(issue_id,renewal_id,emp_no,returned,condition,
                                       status,warehouse,approve_status)
                   VALUES(?,?,?,1,?,?,?, 'none')""",
                (issue_id, renewal_id, issue["emp_no"], condition, status,
                 issue["warehouse"]),
            )
        else:
            if exemption_reason not in ("lost", "destroyed"):
                raise ServiceError("豁免原因必须是 lost(旧鞋遗失) 或 destroyed")
            self.conn.execute(
                """INSERT INTO returns(issue_id,renewal_id,emp_no,returned,exemption,
                                       exemption_reason,status,warehouse,approve_status)
                   VALUES(?,?,?,0,1,?, 'exempt', ?, 'pending')""",
                (issue_id, renewal_id, issue["emp_no"], exemption_reason,
                 issue["warehouse"]),
            )
        self._audit(actor, role, "register_return",
                    f"issue#{issue_id} returned={int(returned)} {condition or exemption_reason}")

    def approve_exemption(self, actor: str, role: str, return_id: int, approved: bool):
        """主管审批旧鞋遗失/销毁豁免。仓管无权。"""
        self._require_role(role, ("supervisor",), "审批旧鞋豁免")
        with transaction(self.conn):
            r = self.conn.execute("SELECT * FROM returns WHERE id=?", (return_id,)).fetchone()
            if not r or r["exemption"] != 1:
                raise ServiceError("豁免记录不存在")
            if r["approve_status"] != "pending":
                raise ServiceError("该豁免已处理")
            self.conn.execute(
                "UPDATE returns SET approver=?, approve_status=? WHERE id=?",
                (actor, "approved" if approved else "rejected", return_id),
            )
            self._audit(actor, role, "approve_exemption",
                        f"return#{return_id} ->{'批准' if approved else '驳回'}")

    def list_returns(self, status: str | None = None):
        sql = "SELECT r.*, i.pair_tag, i.model_code, i.size_left, i.size_right FROM returns r JOIN issues i ON i.id=r.issue_id"
        if status:
            return [dict(x) for x in self.conn.execute(sql + " WHERE r.status=? ORDER BY r.id", (status,))]
        return [dict(x) for x in self.conn.execute(sql + " ORDER BY r.id")]

    def cleaning_inspect(self, actor: str, role: str, return_id: int, passed: bool):
        """清洁复检：通过回库（生成 REUSE 批次）；不通过转待处置。"""
        self._require_role(role, ("supervisor", "storekeeper"), "清洁复检")
        with transaction(self.conn):
            r = self.conn.execute("SELECT * FROM returns WHERE id=?", (return_id,)).fetchone()
            if not r or r["status"] != "cleaning_inspection":
                raise ServiceError("该鞋不在清洁复检环节")
            issue = self.conn.execute("SELECT * FROM issues WHERE id=?", (r["issue_id"],)).fetchone()
            if passed:
                if issue["size_left"] == issue["size_right"]:
                    cells = [(issue["size_left"], 1)]
                else:
                    cells = [(f"{min(issue['size_left'], issue['size_right'])}/{max(issue['size_left'], issue['size_right'])}", 1)]
                for size, qty in cells:
                    self.conn.execute(
                        """INSERT INTO inventory(warehouse,model_code,size,batch_no,qty_total,qty_on_hand)
                           VALUES(?,?,?,?,?,?)""",
                        (issue["warehouse"], issue["model_code"], size,
                         f"REUSE-{return_id}", qty, qty),
                    )
                self.conn.execute("UPDATE returns SET status='back_to_stock' WHERE id=?", (return_id,))
            else:
                self.conn.execute("UPDATE returns SET status='disposal_pending', condition='scrap' WHERE id=?", (return_id,))
            self._audit(actor, role, "cleaning_inspect",
                        f"return#{return_id} passed={int(passed)}")

    # ---- 报废处置批次 ---------------------------------------------------

    def pending_scrap(self):
        return self.list_returns("disposal_pending")

    def create_disposal_batch(self, actor: str, role: str, warehouse: str,
                              return_ids: list[int]):
        self._require_role(role, ("supervisor", "storekeeper"), "建立处置批次")
        ids = set(return_ids)
        if not ids:
            raise ServiceError("请选择报废鞋")
        with transaction(self.conn):
            rows = self.conn.execute(
                f"SELECT * FROM returns WHERE status='disposal_pending' AND warehouse=? AND id IN ({','.join('?'*len(ids))})",
                [warehouse, *ids],
            ).fetchall()
            if len(rows) != len(ids):
                raise ServiceError("存在不属于该仓库待处置的记录")
            no = self._next_doc_no("DSP", "disposal_batches", "batch_no")
            bid = self.conn.execute(
                "INSERT INTO disposal_batches(batch_no,warehouse,item_count) VALUES(?,?,?)",
                (no, warehouse, len(rows)),
            ).lastrowid
            for r in rows:
                issue = self.conn.execute("SELECT * FROM issues WHERE id=?", (r["issue_id"],)).fetchone()
                self.conn.execute(
                    "INSERT INTO disposal_items(batch_id,return_id,pair_tag) VALUES(?,?,?)",
                    (bid, r["id"], issue["pair_tag"]),
                )
                self.conn.execute("UPDATE returns SET status='disposed' WHERE id=?", (r["id"],))
            self._audit(actor, role, "create_disposal_batch", f"{no} {len(rows)}双")
            return bid

    def dispatch_disposal(self, actor: str, role: str, batch_id: int, receipt: str | None = None):
        """调出处置：向模拟处置适配器索要回执。"""
        self._require_role(role, ("supervisor", "storekeeper"), "调出处置")
        from .adapters.disposal import MockDisposalAdapter
        with transaction(self.conn):
            b = self.conn.execute("SELECT * FROM disposal_batches WHERE id=?", (batch_id,)).fetchone()
            if not b:
                raise ServiceError("处置批次不存在")
            if b["status"] != "pending":
                raise ServiceError("该批次已调出")
            receipt = receipt or MockDisposalAdapter().dispatch(b["batch_no"], b["item_count"])
            self.conn.execute(
                """UPDATE disposal_batches SET status='dispatched', receipt_no=?,
                                               dispatched_at=datetime('now','localtime') WHERE id=?""",
                (receipt, batch_id),
            )
            self._audit(actor, role, "dispatch_disposal", f"batch#{batch_id} {receipt}")
            return receipt

    def list_disposal_batches(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM disposal_batches ORDER BY id DESC")]

    # ---- 缺码候补 -------------------------------------------------------

    def join_waitlist(self, actor: str, role: str, emp_no: str,
                      model_code: str, size_left: str, size_right: str):
        self._require_role(role, ("supervisor", "storekeeper"), "登记缺码候补")
        emp = self.get_employee(emp_no)
        for s in (size_left, size_right):
            if s not in config.SIZES:
                raise ServiceError(f"非法尺码：{s}")
        with transaction(self.conn):
            dup = self.conn.execute(
                "SELECT 1 FROM waitlist WHERE emp_no=? AND model_code=? AND status='waiting'",
                (emp_no, model_code),
            ).fetchone()
            if dup:
                raise ServiceError("已在同鞋款候补名单中")
            wid = self.conn.execute(
                """INSERT INTO waitlist(emp_no,level,model_code,size_left,size_right)
                   VALUES(?,?,?,?,?)""",
                (emp_no, emp["level"], model_code, size_left, size_right),
            ).lastrowid
            self._audit(actor, role, "join_waitlist", f"waitlist#{wid} {emp_no}")
            return wid

    def list_waitlist(self, status: str | None = None):
        sql = ("SELECT w.*, e.name FROM waitlist w JOIN employees e ON e.emp_no=w.emp_no")
        if status:
            return [dict(r) for r in self.conn.execute(sql + " WHERE w.status=? ORDER BY w.id", (status,))]
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY w.id")]

    def cancel_waitlist(self, actor: str, role: str, waitlist_id: int):
        self._require_role(role, ("supervisor", "storekeeper"), "取消候补")
        with transaction(self.conn):
            self.conn.execute("UPDATE waitlist SET status='cancelled' WHERE id=?", (waitlist_id,))

    # ---- 跨岗位调动的旧鞋结算 -------------------------------------------

    def pending_transfer(self, emp_no: str):
        row = self.conn.execute(
            "SELECT * FROM transfers WHERE emp_no=? AND handled=0 ORDER BY id DESC LIMIT 1",
            (emp_no,),
        ).fetchone()
        return dict(row) if row else None

    def settle_transfer(self, actor: str, role: str, emp_no: str,
                        returned: bool, condition: str = "", exemption_reason: str = ""):
        """调岗后结算原岗位有效鞋（同样需交回或豁免审批），结算后方可领新等级鞋。"""
        self._require_role(role, ("supervisor", "storekeeper"), "调岗旧鞋结算")
        tr = self.pending_transfer(emp_no)
        if not tr:
            raise ServiceError("没有待结算的岗位调动")
        old = self.active_issue(emp_no, tr["from_level"])
        if not old:
            with transaction(self.conn):
                self.conn.execute("UPDATE transfers SET handled=1 WHERE id=?", (tr["id"],))
            return
        with transaction(self.conn):
            self._register_return_locked(
                actor, role, dict(old), returned, condition, exemption_reason, None)
            final_status = "scrapped" if (returned and condition == "scrap") else "transferred"
            self.conn.execute("UPDATE issues SET status=? WHERE id=?", (final_status, old["id"]))
            self.conn.execute("UPDATE transfers SET handled=1 WHERE id=?", (tr["id"],))
            self._audit(actor, role, "settle_transfer", f"{emp_no} 旧鞋 {final_status}")

    def can_issue_now(self, emp_no: str) -> tuple[bool, str]:
        """试穿前的资格总检查。"""
        emp = self.get_emp_no_safe(emp_no)
        if not emp:
            return False, "工号不存在"
        if not emp["active"]:
            return False, "员工已停用"
        if not self.get_profile(emp_no):
            return False, "缺少尺码档案"
        if self.pending_transfer(emp_no):
            return False, "跨岗位调动未结算旧鞋"
        return True, "ok"

    def get_emp_no_safe(self, emp_no: str):
        row = self.conn.execute("SELECT * FROM employees WHERE emp_no=?", (emp_no,)).fetchone()
        return dict(row) if row else None

    # ---- 提醒 -----------------------------------------------------------

    def list_reminders(self, limit: int = 50):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM reminders ORDER BY id DESC LIMIT ?", (limit,))]

    def scan_due(self, today: date | None = None) -> int:
        """到期队列：N 天内到期或已逾期 -> 生成/刷新到期提醒。"""
        today = today or date.today()
        n = 0
        with transaction(self.conn):
            rows = self.conn.execute("SELECT * FROM issues WHERE status='active'").fetchall()
            for i in rows:
                d = days_to_expire(i["expire_date"], today)
                if d <= config.DUE_SOON_DAYS:
                    msg = (f"{i['emp_no']} 的 {i['pair_tag']}（{i['model_code']} {i['size_left']}/{i['size_right']}）"
                           + ("已到期" if d < 0 else f"{d} 天后到期") + "，请安排到期换领")
                    self.conn.execute(
                        """INSERT INTO reminders(kind,ref_type,ref_id,emp_no,message)
                           VALUES('due','issue',?,?,?)
                           ON CONFLICT(kind,ref_type,ref_id) DO UPDATE SET message=excluded.message""",
                        (i["id"], i["emp_no"], msg),
                    )
                    n += 1
        return n

    def scan_waitlist(self) -> int:
        """缺码候补：两仓库任一补齐了候补尺码 -> 通知。"""
        n = 0
        with transaction(self.conn):
            for w in self.conn.execute("SELECT * FROM waitlist WHERE status='waiting'").fetchall():
                ql = sum(self.stock_on_hand(wh, w["model_code"], w["size_left"]) for wh in config.WAREHOUSES)
                qr = sum(self.stock_on_hand(wh, w["model_code"], w["size_right"]) for wh in config.WAREHOUSES)
                need = 1 if w["size_left"] == w["size_right"] else 2
                have = ql + qr
                if (w["size_left"] == w["size_right"] and ql >= 1) or have >= need:
                    self.conn.execute(
                        """INSERT INTO reminders(kind,ref_type,ref_id,emp_no,message)
                           VALUES('waitlist','waitlist',?,?,?)
                           ON CONFLICT(kind,ref_type,ref_id) DO UPDATE SET message=excluded.message""",
                        (w["id"], w["emp_no"],
                         f"候补到货：{w['emp_no']} 等候的 {w['model_code']} {w['size_left']}/{w['size_right']} 已可试穿"),
                    )
                    self.conn.execute(
                        "UPDATE waitlist SET status='notified', notified_at=datetime('now','localtime') WHERE id=?",
                        (w["id"],))
                    n += 1
        return n
