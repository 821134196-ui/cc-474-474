"""测试公共夹具：临时库 + 鞋款/库存/员工。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from safety_shoes.db import get_connection, init_db
from safety_shoes.services import ShoeService, add_months

SUP = ("M01 周主管", "supervisor")
KEEPER = ("K01 钱仓管", "storekeeper")


class ShoeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        init_db(self.db_path)
        conn = get_connection(self.db_path)
        self.conn = conn
        self.svc = ShoeService(conn=conn)
        self._bootstrap()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _bootstrap(self):
        c = self.conn
        for full, levels in __import__("safety_shoes.config", fromlist=["MODEL_LEVELS"]).MODEL_LEVELS.items():
            code, name = full.split(" ", 1)
            c.execute("INSERT INTO shoe_models(code,name,level,lifespan_months) VALUES(?,?,?,12)",
                      (code, name, levels[0]))
        # 员工：E1 S3 冲压，E2 S1 装配，E3 S5 铸造
        for no, name, pos, lv in [
            ("T001", "测试甲", "冲压工", "S3"),
            ("T002", "测试乙", "装配工", "S1"),
            ("T003", "测试丙", "铸造工", "S5"),
            ("T004", "测试丁", "电焊工", "S2"),
        ]:
            c.execute("INSERT INTO employees(emp_no,name,position,level) VALUES(?,?,?,?)",
                      (no, name, pos, lv))
            c.execute("INSERT INTO size_profiles(emp_no,size_left,size_right,width) VALUES(?,?,?, 'M')",
                      (no, "42", "42"))
        # 两仓库库存：HX-300 42 各 1 双（竞争用）；HX-100 40 各 3
        for wh in ("A库", "B库"):
            for model, size, qty in [("HX-300", "42", 1), ("HX-100", "40", 3),
                                     ("HX-200", "42", 2), ("HX-500", "43", 2)]:
                c.execute(
                    """INSERT INTO inventory(warehouse,model_code,size,batch_no,qty_total,qty_on_hand)
                       VALUES(?,?,?,?,?,?)""",
                    (wh, model, size, f"B-{wh}-{model}", qty, qty))

    # -- 便捷流程 ---------------------------------------------------------

    def first_issue(self, emp_no="T001", model="HX-300", sl="42", sr="42", wh="A库"):
        """完整首发：试穿 -> 确认，返回 issue_id。"""
        fid = self.svc.start_fitting(*KEEPER, emp_no, model, sl, sr)
        return self.svc.confirm_fitting(*KEEPER, fid, wh)

    def seed_active(self, emp_no, level, model, sl, sr, wh, issued_days_ago):
        """直接插入一双指定发放天数的有效鞋，返回 issue_id。"""
        d = date.today() - timedelta(days=issued_days_ago)
        row = self.conn.execute(
            "SELECT id FROM inventory WHERE warehouse=? AND model_code=? AND size=? AND qty_on_hand>0 ORDER BY id LIMIT 1",
            (wh, model, sl)).fetchone()
        self.conn.execute("UPDATE inventory SET qty_on_hand=qty_on_hand-1 WHERE id=?", (row["id"],))
        expire = add_months(d, 12)
        cur = self.conn.execute(
            """INSERT INTO issues(issue_no,emp_no,level,model_code,size_left,size_right,
                                  warehouse,inventory_id,issue_date,expire_date,status,pair_tag)
               VALUES(?,?,?,?,?,?,?,?,?,?,'active','X')""",
            (f"ISS-OLD-{emp_no}", emp_no, level, model, sl, sr, wh, row["id"],
             d.isoformat(), expire.isoformat()))
        iid = cur.lastrowid
        self.conn.execute("UPDATE issues SET pair_tag=? WHERE id=?", (f"SN-{iid:05d}", iid))
        return iid

    def stock(self, wh, model, size):
        return self.svc.stock_on_hand(wh, model, size)

    def photos(self):
        return {"sole": "s.svg", "toe": "t.svg", "upper": "u.svg"}
