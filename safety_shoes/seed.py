"""演示数据种子。

一条命令准备全部样例：
    python3 -m safety_shoes.seed

样例覆盖：正常有效鞋、临近到期、缺码候补、左右脚异码、
跨岗位调动待结算、提前磨损待主管审批（含三张照片）、
旧鞋遗失豁免待批、清洁复检中、报废待处置，以及两处仓库的库存矩阵。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from . import config
from .db import get_connection, init_db
from .services import ShoeService, add_months

PHOTO_SLOT_LABELS = {
    "sole": "鞋底",
    "toe": "鞋头",
    "upper": "鞋帮",
}

PHOTO_SVG = """<svg xmlns='http://www.w3.org/2000/svg' width='320' height='200'>
<rect width='100%' height='100%' fill='#F4F6F8'/>
<rect x='8' y='8' width='304' height='184' fill='none' stroke='#2F5D7C' stroke-width='3' stroke-dasharray='8 6'/>
<text x='160' y='95' font-size='26' text-anchor='middle' fill='#1E3D52' font-family='sans-serif'>{label}磨损照片</text>
<text x='160' y='130' font-size='16' text-anchor='middle' fill='#2F5D7C' font-family='sans-serif'>{emp} {slot} · 演示占位图</text>
</svg>
"""


def reset_db(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in ("disposal_items", "disposal_batches", "reminders", "waitlist",
              "damage_claims", "returns", "renewals", "issues", "fittings",
              "inventory", "shoe_models", "size_profiles", "transfers",
              "employees", "audit_log"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("PRAGMA foreign_keys = ON")


def save_photo(emp_no: str, slot: str) -> str:
    config.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    name = f"claim-{emp_no}-{slot}.svg"
    (config.PHOTO_DIR / name).write_text(
        PHOTO_SVG.format(label=PHOTO_SLOT_LABELS[slot], emp=emp_no, slot=slot),
        encoding="utf-8")
    return name


def _deduct(conn, warehouse, model_code, size):
    row = conn.execute(
        """SELECT id FROM inventory
           WHERE warehouse=? AND model_code=? AND size=? AND qty_on_hand>0
           ORDER BY id LIMIT 1""",
        (warehouse, model_code, size)).fetchone()
    if not row:
        raise RuntimeError(f"种子库存不足：{warehouse} {model_code} {size}")
    conn.execute("UPDATE inventory SET qty_on_hand=qty_on_hand-1 WHERE id=?", (row["id"],))
    return row["id"]


_pair_counter = {"n": 0}


def issue_pair(conn, emp_no, level, model_code, sl, sr, warehouse, issue_d, status="active"):
    """直接落一双鞋（种子专用，绕过试穿流程）。"""
    _pair_counter["n"] += 1
    inv = _deduct(conn, warehouse, model_code, sl)
    if sl != sr:
        _deduct(conn, warehouse, model_code, sr)
    expire = add_months(issue_d, 12)
    cur = conn.execute(
        """INSERT INTO issues(issue_no,emp_no,level,model_code,size_left,size_right,
                              warehouse,inventory_id,issue_date,expire_date,status,pair_tag)
           VALUES(?,?,?,?,?,?,?,?,?,?,?, 'PENDING')""",
        (f"ISS-SEED-{_pair_counter['n']:03d}-{emp_no}", emp_no, level, model_code, sl, sr,
         warehouse, inv, issue_d.isoformat(), expire.isoformat(), status),
    )
    iid = cur.lastrowid
    tag = f"SN-{iid:05d}"
    conn.execute("UPDATE issues SET pair_tag=? WHERE id=?", (tag, iid))
    return iid


def seed(db_path: Path | str | None = None, verbose: bool = True) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    svc = ShoeService(conn=conn)
    today = date.today()
    try:
        conn.execute("BEGIN IMMEDIATE")
        reset_db(conn)

        # ---- 鞋款 --------------------------------------------------------
        for full, levels in config.MODEL_LEVELS.items():
            code, name = full.split(" ", 1)
            conn.execute(
                "INSERT INTO shoe_models(code,name,level,lifespan_months) VALUES(?,?,?,?)",
                (code, name, levels[0], config.DEFAULT_LIFESPAN_MONTHS))

        # ---- 库存矩阵：两仓库，鞋款×尺码 --------------------------------
        # (warehouse, model, size, qty)
        stock = []
        plan = {
            "HX-100": {"A库": {"37": 4, "38": 6, "39": 8, "40": 7, "41": 5, "42": 3, "43": 2, "44": 1},
                       "B库": {"38": 3, "39": 4, "40": 5, "41": 6, "42": 4, "43": 3, "45": 1}},
            # HX-200 的 42 码两仓库全部缺码 -> E1004 候补
            "HX-200": {"A库": {"38": 3, "39": 5, "40": 4, "41": 2, "43": 1, "44": 1},
                       "B库": {"39": 2, "40": 3, "41": 4, "43": 2}},
            "HX-300": {"A库": {"39": 3, "40": 4, "41": 5, "42": 6, "43": 4, "44": 2},
                       "B库": {"40": 2, "41": 3, "42": 3, "43": 5, "44": 1, "45": 1}},
            "HX-400": {"A库": {"39": 2, "40": 3, "41": 4, "42": 3, "43": 2},
                       "B库": {"41": 1, "42": 2, "43": 2, "44": 1}},
            "HX-500": {"A库": {"40": 2, "41": 3, "42": 4, "43": 3, "44": 2},
                       "B库": {"42": 2, "43": 2, "44": 1, "45": 1}},
        }
        for model, whs in plan.items():
            for wh, sizes in whs.items():
                for size, qty in sizes.items():
                    conn.execute(
                        """INSERT INTO inventory(warehouse,model_code,size,batch_no,qty_total,qty_on_hand)
                           VALUES(?,?,?,?,?,?)""",
                        (wh, model, size, f"SEED-{wh[0]}-{model}", qty, qty))

        # ---- 员工 --------------------------------------------------------
        employees = [
            ("E1001", "王建国", "冲压工", "S3", "42", "42", "M", ""),
            ("E1002", "李秀兰", "装配工", "S1", "40", "40", "M", ""),
            ("E1003", "张大力", "铸造工", "S5", "43", "43", "W", "脚背高，需宽楦"),
            ("E1004", "赵敏",   "电焊工", "S2", "42", "42", "M", "缺码候补中"),
            ("E1005", "陈志强", "冲压工", "S3", "42", "42", "M", "即将调入仓库理货岗，旧S3鞋待结算"),
            ("E1006", "刘芳",   "电检工", "S4", "41", "41", "N", "旧鞋遗失豁免审批中"),
            ("E1007", "孙涛",   "维修工", "S3", "44", "44", "M", "新入职未领鞋"),
            ("E1008", "周晓东", "装配工", "S1", "42", "43", "M", "左右脚差一码"),
            ("E1009", "吴海",   "涂装工", "S2", "41", "41", "M", "旧鞋清洁复检中"),
            ("E1010", "郑磊",   "装配工", "S1", "39", "39", "M", "旧鞋报废待处置"),
        ]
        for emp_no, name, pos, level, sl, sr, width, note in employees:
            conn.execute(
                "INSERT INTO employees(emp_no,name,position,level) VALUES(?,?,?,?)",
                (emp_no, name, pos, level))
            conn.execute(
                "INSERT INTO size_profiles(emp_no,size_left,size_right,width,note) VALUES(?,?,?,?,?)",
                (emp_no, sl, sr, width, note))

        # ---- 已发放鞋 ----------------------------------------------------
        # E1001：15 天后到期 -> 到期队列
        issue_pair(conn, "E1001", "S3", "HX-300", "42", "42", "A库",
                   today - timedelta(days=350))
        # E1002：正常使用中
        issue_pair(conn, "E1002", "S1", "HX-100", "40", "40", "B库",
                   today - timedelta(days=100))
        # E1003：提前磨损样例（铸造 S5，穿 200 天鞋底磨穿）
        e1003 = issue_pair(conn, "E1003", "S5", "HX-500", "43", "43", "A库",
                           today - timedelta(days=200))

        # E1005：调岗前发的 S3 鞋仍在脚上，随后主管调岗 -> transfer 待结算
        e1005 = issue_pair(conn, "E1005", "S3", "HX-300", "42", "42", "A库",
                           today - timedelta(days=300))
        conn.execute("COMMIT")

        # 调岗走服务方法（生成 transfers 记录）
        svc.change_position("M01 周主管", "supervisor", "E1005", "仓库理货员", "S1")

        conn.execute("BEGIN IMMEDIATE")
        # E1006：旧鞋遗失，损坏换领已批准，豁免仍待主管批 -> 新鞋发不出去
        e1006 = issue_pair(conn, "E1006", "S4", "HX-400", "41", "41", "B库",
                           today - timedelta(days=260))
        # E1008：左右脚异码
        issue_pair(conn, "E1008", "S1", "HX-100", "42", "43", "A库",
                   today - timedelta(days=60))
        # E1009：旧鞋交回（可用）清洁复检中，新鞋已穿
        old9 = issue_pair(conn, "E1009", "S2", "HX-200", "41", "41", "A库",
                          today - timedelta(days=340), status="renewed")
        issue_pair(conn, "E1009", "S2", "HX-200", "41", "41", "A库",
                   today - timedelta(days=20))
        # E1010：旧鞋报废待处置，新鞋已穿
        old10 = issue_pair(conn, "E1010", "S1", "HX-100", "39", "39", "B库",
                           today - timedelta(days=330), status="renewed")
        issue_pair(conn, "E1010", "S1", "HX-100", "39", "39", "B库",
                           today - timedelta(days=12))
        conn.execute("COMMIT")

        # ---- 业务样例（走服务层，保证链路真实） --------------------------
        # 提前磨损申请：三张固定照片位，待主管审批
        photos = {slot: save_photo("E1003", slot) for slot in ("sole", "toe", "upper")}
        rid = svc.create_damage_renewal(
            "K01 钱仓管", "storekeeper", "E1003", "early_wear",
            "铸造车间高温环境，鞋底大底剥落、鞋头烫伤、鞋帮开裂", photos)

        # 缺码候补：HX-200 / 42 两仓库均无货
        svc.join_waitlist("K01 钱仓管", "storekeeper", "E1004", "HX-200", "42", "42")

        # E1006：损坏换领已批准 + 旧鞋遗失豁免待批
        rid6 = svc.create_damage_renewal(
            "K01 钱仓管", "storekeeper", "E1006", "damage",
            "下班途中鞋子遗失，已报案登记",
            {s: save_photo("E1006", s) for s in ("sole", "toe", "upper")})
        svc.approve_renewal("M01 周主管", "supervisor", rid6, True, "情况属实，先批换领，豁免另行审批")
        svc.register_return("K01 钱仓管", "storekeeper", e1006, False,
                            exemption_reason="lost", renewal_id=rid6)

        # E1009 旧鞋可用 -> 清洁复检中
        svc.register_return("K01 钱仓管", "storekeeper", old9, True,
                            condition="reusable")
        # E1010 旧鞋报废 -> 待处置批次
        svc.register_return("K01 钱仓管", "storekeeper", old10, True,
                            condition="scrap")

        # 首次扫描生成到期/候补提醒
        svc.scan_due()
        svc.scan_waitlist()

        if verbose:
            print("演示数据就绪：")
            print(f"  员工 10 名（含调岗待结算 E1005、提前磨损待审批 E1003、缺码候补 E1004、遗失豁免 E1006）")
            print(f"  鞋款 5 个，库存矩阵 {config.WAREHOUSES}（HX-200 的 42 码两仓缺码）")
            print(f"  提前磨损换领单 #{rid} 待主管审批；磨损照片位于 {config.PHOTO_DIR}")
            print("  提示：审批角色用 M01 周主管（主管），发放角色用 K01 钱仓管（仓管）")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
