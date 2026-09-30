"""演示数据播种：等级/员工/尺码档案/鞋款/库存批次/试穿/发放链路。

一条命令（python -m shoe_system.seed 或 run.sh）准备好三个重点样例：
  1. 缺码候补：E1004 孙安全 41 码两仓均无货，已排队候补
  2. 跨岗位调动：E1006 吴进步 冲压工 S1 → 铸造工 S3，调岗换领单待领取
  3. 提前换领：E1005 周大勇 鞋底/鞋头/鞋帮三照片损坏申请，待主管审批
另含到期队列（E1003 已逾期、E1001 30 天内到期）、清洁复检与报废待处置样例。
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

from . import services as svc
from .adapters import BadgeAdapter
from .db import connect, init_db

KEEPER = {"name": "仓管-刘库", "role": svc.ROLE_KEEPER}
SUPERVISOR = {"name": "主管-陈厂", "role": svc.ROLE_SUPERVISOR}
ADMIN = {"name": "系统管理员", "role": svc.ROLE_ADMIN}

# (slot, 底色 RGB)
PHOTO_STYLE = {
    "sole": ((90, 90, 96), (210, 200, 60)),    # 鞋底灰 + 荧光黄磨损块
    "toe": ((110, 96, 80), (60, 90, 140)),      # 鞋头棕 + 钢蓝
    "upper": ((120, 110, 100), (170, 60, 50)),  # 鞋帮 + 破损红
}


def _png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    raw = b""
    for row in pixels:
        raw += b"\x00" + b"".join(struct.pack("BBB", *p) for p in row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_photo(path: Path, slot: str) -> None:
    """生成 160x120 的占位磨损照片（纯色 + 色块，不依赖第三方库）。"""
    base, mark = PHOTO_STYLE[slot]
    w, h = 160, 120
    px = []
    for y in range(h):
        row = []
        for x in range(w):
            c = base
            if slot == "sole" and 35 < y < 80 and ((x - 80) ** 2 / 3600 + (y - 58) ** 2 / 900) < 1:
                c = mark  # 鞋底磨损斑块
            if slot == "toe" and 25 < x < 70 and 25 < y < 70:
                c = mark  # 鞋头钢头区域
            if slot == "upper" and 90 < x < 130 and 40 < y < 95:
                c = mark  # 鞋帮破口
            row.append(c)
        px.append(row)
    path.write_bytes(_png(w, h, px))


def seed(db_path: str, photo_dir: str, *, reset: bool = True) -> None:
    db = Path(db_path)
    pdir = Path(photo_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    if reset and db.exists():
        db.unlink()
        for suffix in ("-wal", "-shm"):
            side = Path(str(db) + suffix)
            if side.exists():
                side.unlink()
        for f in pdir.glob("demo_*.png"):
            f.unlink()

    conn = connect(db)
    init_db(conn)

    # ---------- 防护等级与授权鞋款 ----------
    levels = [
        ("SB", "基本防砸", "GUARD-SB", 12),
        ("S1", "防砸防静电", "GUARD-S1", 12),
        ("S1P", "防砸防穿刺", "GUARD-S1P", 10),
        ("S3", "防水防穿刺", "GUARD-S3", 8),
    ]
    conn.executemany(
        "INSERT INTO protection_levels(code,name,allowed_models,lifespan_months) VALUES(?,?,?,?)",
        levels)

    sizes_all = ",".join(str(x) for x in range(36, 47))
    models = [
        ("GUARD-SB", "坚固SB低帮", "SB", sizes_all),
        ("GUARD-S1", "稳特S1中帮", "S1", sizes_all),
        ("GUARD-S1P", "铁甲S1P防刺", "S1P", sizes_all),
        ("GUARD-S3", "水盾S3高帮", "S3", sizes_all),
    ]
    conn.executemany("INSERT INTO shoe_models(code,name,grade_code,sizes) VALUES(?,?,?,?)", models)

    # ---------- 员工 ----------
    today = date.today()

    def badge(eid):
        return BadgeAdapter.sync_badge(eid)

    employees = [
        ("E1001", "王建国", "冲压工", "S1"),
        ("E1002", "李秀英", "装配工", "SB"),
        ("E1003", "赵铁锤", "铸造工", "S3"),
        ("E1004", "孙安全", "仓储理货", "SB"),
        ("E1005", "周大勇", "机修钳工", "S1P"),
        ("E1006", "吴进步", "冲压工", "S1"),
        ("E1007", "钱多多", "电工", "S1"),
        ("E1010", "冯守旧", "装配工", "SB"),
        ("E1011", "田报废", "铸造工", "S3"),
    ]
    conn.executemany(
        "INSERT INTO employees(id,name,post,grade_code,badge_no,active) VALUES(?,?,?,?,?,1)",
        [(eid, name, post, grade, badge(eid)) for eid, name, post, grade in employees])

    # ---------- 尺码档案（含左右脚差异） ----------
    profiles = {
        "E1001": (42, 42, "M", ""),
        "E1002": (39, 38.5, "M", "左右脚差半码，按左脚 39 发"),
        "E1003": (44, 44, "W", "宽脚"),
        "E1004": (41, 41, "M", ""),
        "E1005": (42, 42, "M", ""),
        "E1006": (40, 40, "M", ""),
        "E1007": (41, 41, "W", "宽脚，41 顶脚，试穿调码 42"),
        "E1010": (38, 38, "M", ""),
        "E1011": (43, 43, "M", ""),
    }
    for eid, (l, r, w, note) in profiles.items():
        conn.execute(
            "INSERT INTO size_profiles(employee_id,left_size,right_size,width,note,created_at) VALUES(?,?,?,?,?,?)",
            (eid, l, r, w, note, svc.now_str()))
    conn.commit()

    # ---------- 库存批次（A仓/B仓；刻意制造 41 码缺码） ----------
    def batches(model: str, rows: list[tuple[float, str, int, int]]):
        for size, wh, total, used in rows:
            conn.execute(
                "INSERT INTO inventory_batches(model_code,size,warehouse,qty_total,qty_used,received_at) VALUES(?,?,?,?,?,?)",
                (model, size, wh, total, used, (today - timedelta(days=60)).strftime("%Y-%m-%d")))

    common_a = [(s, "A仓", 6, 0) for s in range(36, 47)]
    common_b = [(s, "B仓", 4, 0) for s in range(36, 47)]
    for model in ("GUARD-SB", "GUARD-S1", "GUARD-S1P", "GUARD-S3"):
        batches(model, common_a)
        batches(model, common_b)
    # GUARD-SB 41 码两仓全部用光 → 缺码候补样例
    conn.execute("UPDATE inventory_batches SET qty_used=qty_total WHERE model_code='GUARD-SB' AND size=41")
    conn.commit()

    # ---------- 试穿 + 历史发放 ----------
    def fit_ok(eid: str, model: str, size: float):
        conn.execute(
            """INSERT INTO fittings(employee_id,model_code,tried_size,left_fit,right_fit,result,note,created_at)
               VALUES(?,?,?,'合适','合适','ok','试穿确认',?)""",
            (eid, model, size, svc.now_str()))
        conn.commit()

    # E1001：340 天前发，12 个月期限 → 约 25 天后到期（30 天到期队列）
    fit_ok("E1001", "GUARD-S1", 42)
    svc.issue_shoe(conn, "E1001", "GUARD-S1", 42, "A仓", KEEPER,
                   now=today - timedelta(days=340))

    # E1002：左右脚差异，发较大码 39
    fit_ok("E1002", "GUARD-SB", 39)
    svc.issue_shoe(conn, "E1002", "GUARD-SB", 39, "A仓", KEEPER,
                   now=today - timedelta(days=120))

    # E1003：S3 期限 8 个月，9 个月前发 → 已逾期，可走正常到期换领
    fit_ok("E1003", "GUARD-S3", 44)
    svc.issue_shoe(conn, "E1003", "GUARD-S3", 44, "B仓", KEEPER,
                   now=today - timedelta(days=275))

    # E1007：试穿调码（41 紧 → 调 42 确认），30 天前发
    conn.execute(
        """INSERT INTO fittings(employee_id,model_code,tried_size,left_fit,right_fit,result,note,created_at)
           VALUES('E1007','GUARD-S1',41,'紧','紧','adjust','顶脚，调大一号',?)""",
        (svc.now_str(),))
    fit_ok("E1007", "GUARD-S1", 42)
    svc.issue_shoe(conn, "E1007", "GUARD-S1", 42, "A仓", KEEPER,
                   now=today - timedelta(days=30))

    # E1005：提前磨损样例，100 天前发（远未到期），损坏申请待主管审批
    fit_ok("E1005", "GUARD-S1P", 42)
    svc.issue_shoe(conn, "E1005", "GUARD-S1P", 42, "A仓", KEEPER,
                   now=today - timedelta(days=100))

    # E1006：先在冲压岗位 S1 发鞋，随后主管调岗到 S3，调岗后再试穿新等级鞋款
    fit_ok("E1006", "GUARD-S1", 40)
    svc.issue_shoe(conn, "E1006", "GUARD-S1", 40, "A仓", KEEPER,
                   now=today - timedelta(days=200))
    svc.transfer_post(conn, "E1006", "铸造工", "S3", SUPERVISOR)
    fit_ok("E1006", "GUARD-S3", 40)

    # E1010：历史换领已完成，旧鞋交回为可用 → 清洁复检队列
    fit_ok("E1010", "GUARD-SB", 38)
    svc.issue_shoe(conn, "E1010", "GUARD-SB", 38, "A仓", KEEPER,
                   now=today - timedelta(days=400))

    # E1011：历史换领已完成，旧鞋报废 → 待处置批次
    fit_ok("E1011", "GUARD-S3", 43)
    svc.issue_shoe(conn, "E1011", "GUARD-S3", 43, "A仓", KEEPER,
                   now=today - timedelta(days=300))

    conn.commit()

    # ---------- 重点样例 1：缺码候补 ----------
    fit_ok("E1004", "GUARD-SB", 41)
    try:
        svc.issue_shoe(conn, "E1004", "GUARD-SB", 41, "A仓", KEEPER)
    except svc.BusinessError:
        pass  # 预期：缺码并自动登记候补

    # ---------- 重点样例 3：提前换领（三照片） ----------
    e1005_iss = conn.execute(
        "SELECT id FROM issuances WHERE employee_id='E1005' AND status='active'").fetchone()["id"]
    photos = {}
    for slot in ("sole", "toe", "upper"):
        fname = f"demo_E1005_{slot}.png"
        make_photo(pdir / fname, slot)
        photos[slot] = fname
    svc.create_renewal(
        conn, e1005_iss, "damage",
        "鞋底防滑纹磨平、鞋头钢头外露、鞋帮撕裂，存在 puncture 风险，申请提前换领",
        photos, KEEPER)

    # ---------- 历史换领链路（复检 + 报废处置样例） ----------
    def complete_old_renewal(eid: str, model: str, size: float, condition: str, wh: str):
        old = conn.execute(
            "SELECT id FROM issuances WHERE employee_id=? AND status='active'", (eid,)).fetchone()
        rid = svc.create_renewal(conn, old["id"], "expiry", "到期换新", None, KEEPER)
        svc.pickup_renewal(conn, rid, model, size, wh, condition, KEEPER,
                           now=today - timedelta(days=2))

    complete_old_renewal("E1010", "GUARD-SB", 38, "reusable", "A仓")
    complete_old_renewal("E1011", "GUARD-S3", 43, "scrap", "A仓")

    conn.commit()
    conn.close()
    print("演示数据就绪：")
    print("  缺码候补  E1004 孙安全  GUARD-SB 41 码（两仓无货）")
    print("  跨岗位调动 E1006 吴进步  S1冲压 → S3铸造，调岗换领单待领取")
    print("  提前换领  E1005 周大勇  三照片损坏申请，待主管审批（可勾选旧鞋遗失豁免）")
    print("  到期队列  E1003 已逾期；E1001 30 天内到期")
    print("  旧鞋处置  一双待清洁复检；待处置批次含一双报废鞋")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/shoes.db")
    ap.add_argument("--photos", default="data/photos")
    args = ap.parse_args()
    seed(args.db, args.photos)


if __name__ == "__main__":
    main()
