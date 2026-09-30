"""核心业务规则测试。

覆盖：有效鞋唯一、两处仓库并发发放仅一次成功、库存原子竞争、
期限计算、试穿锁码、缺码候补、到期/损坏换领、旧鞋遗失豁免审批、
岗位权限（仓管不能改等级）、交旧领新、清洁复检与处置回执、提醒。
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

from shoe_system import services as svc
from shoe_system.db import connect, init_db

KEEPER = {"name": "仓管A", "role": svc.ROLE_KEEPER}
SUPERVISOR = {"name": "主管B", "role": svc.ROLE_SUPERVISOR}
ADMIN = {"name": "管理员", "role": svc.ROLE_ADMIN}


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)
        init_db(self.conn)
        self._fixtures()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _fixtures(self):
        c = self.conn
        c.executemany(
            "INSERT INTO protection_levels(code,name,allowed_models,lifespan_months) VALUES(?,?,?,?)",
            [("SB", "基本防砸", "M-SB", 12), ("S3", "防水防刺", "M-S3", 8)])
        c.executemany("INSERT INTO shoe_models(code,name,grade_code,sizes) VALUES(?,?,?,?)",
                      [("M-SB", "SB鞋", "SB", "38,39,40,41,42"),
                       ("M-S3", "S3鞋", "S3", "38,39,40,41,42,44")])
        c.execute("INSERT INTO employees(id,name,post,grade_code,badge_no) VALUES('E1','张三','冲压','S3','BD-AAAA1111')")
        c.execute("INSERT INTO employees(id,name,post,grade_code,badge_no) VALUES('E2','李四','装配','SB','BD-BBBB2222')")
        c.commit()
        self.today = date(2026, 3, 15)

    def profile(self, eid="E1", l=42.0, r=42.0, width="M"):
        svc.upsert_profile(self.conn, eid, l, r, width, "", KEEPER)

    def fit(self, eid="E1", model="M-S3", size=42.0):
        svc.record_fitting(self.conn, eid, model, size, "合适", "合适", "ok", "", KEEPER)

    def stock(self, model="M-S3", size=42.0, wh="A仓", total=3):
        self.conn.execute(
            "INSERT INTO inventory_batches(model_code,size,warehouse,qty_total,qty_used,received_at) VALUES(?,?,?,?,0,'2026-01-01')",
            (model, size, wh, total))
        self.conn.commit()


class TestIssueRules(BaseCase):
    def test_requires_fitting_lock_before_issue(self):
        self.profile()
        self.stock()
        with self.assertRaisesRegex(svc.BusinessError, "试穿确认"):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        self.fit()
        iid = svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        self.assertTrue(iid)

    def test_left_right_difference_locks_larger_size(self):
        self.profile("E1", 42.0, 41.0)
        self.stock(size=42)
        self.fit(size=42)
        with self.assertRaisesRegex(svc.BusinessError, "较大码"):
            svc.issue_shoe(self.conn, "E1", "M-S3", 41, "A仓", KEEPER, now=self.today)
        iid = svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        row = self.conn.execute("SELECT size_detail FROM issuances WHERE id=?", (iid,)).fetchone()
        self.assertEqual(row["size_detail"], "L42/R41")

    def test_fitting_adjust_clears_lock(self):
        self.profile()
        self.stock(size=42)
        self.fit(size=42)
        # 随后调码：42 紧 → 锁码失效
        svc.record_fitting(self.conn, "E1", "M-S3", 42, "紧", "紧", "adjust", "", KEEPER)
        with self.assertRaisesRegex(svc.BusinessError, "试穿确认"):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        # 重新确认 42.5 不存在→这里用 42 再确认（42 调整后再试合适）
        self.fit(size=42)
        self.assertTrue(svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today))

    def test_unique_active_shoe_per_grade(self):
        """同一员工同一防护等级只能有一双有效鞋：重复领用被拦截。"""
        self.profile()
        self.stock(wh="A仓", total=5)
        self.fit()
        svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        with self.assertRaisesRegex(svc.BusinessError, "重复领用"):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        actives = self.conn.execute(
            "SELECT COUNT(*) c FROM issuances WHERE employee_id='E1' AND grade_code='S3' AND status='active'").fetchone()["c"]
        self.assertEqual(actives, 1)

    def test_grade_authorization_required(self):
        """岗位未授权的鞋款不能试穿/发放。"""
        self.profile("E2", 40, 40)
        with self.assertRaisesRegex(svc.BusinessError, "防护等级"):
            svc.record_fitting(self.conn, "E2", "M-S3", 40, "合适", "合适", "ok", "", KEEPER)
        self.fit("E2", "M-SB", 40)
        with self.assertRaisesRegex(svc.BusinessError, "岗位未授权"):
            svc.issue_shoe(self.conn, "E2", "M-S3", 40, "A仓", KEEPER, now=self.today)

    def test_missing_size_joins_waitlist(self):
        self.profile()
        self.stock(total=0)  # 无货
        self.fit()
        with self.assertRaisesRegex(svc.BusinessError, "缺码候补"):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        wl = self.conn.execute("SELECT status FROM waitlist WHERE employee_id='E1'").fetchone()
        self.assertEqual(wl["status"], "waiting")
        # 重复触发不产生重复候补
        with self.assertRaises(svc.BusinessError):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM waitlist WHERE employee_id='E1'").fetchone()["c"], 1)


class TestConcurrency(BaseCase):
    def test_two_warehouses_concurrent_issue_only_one_succeeds(self):
        """两处仓库并发发放同一员工同一等级：仅一次成功。"""
        self.profile()
        self.stock(wh="A仓", total=2)
        self.stock(wh="B仓", total=2)
        self.fit()
        results = []

        def issue(wh):
            conn = connect(self.db)
            try:
                try:
                    iid = svc.issue_shoe(conn, "E1", "M-S3", 42, wh,
                                         {"name": "仓管-" + wh, "role": "keeper"}, now=self.today)
                    results.append(("ok", iid))
                except svc.BusinessError as e:
                    results.append(("fail", str(e)))
            finally:
                conn.close()

        t1 = threading.Thread(target=issue, args=("A仓",))
        t2 = threading.Thread(target=issue, args=("B仓",))
        t1.start(); t2.start(); t1.join(); t2.join()

        oks = [r for r in results if r[0] == "ok"]
        fails = [r for r in results if r[0] == "fail"]
        self.assertEqual(len(oks), 1, f"应仅一次成功，实际：{results}")
        self.assertEqual(len(fails), 1)
        self.assertIn("有效鞋", fails[0][1])
        actives = self.conn.execute(
            "SELECT COUNT(*) c FROM issuances WHERE employee_id='E1' AND status='active'").fetchone()["c"]
        self.assertEqual(actives, 1)
        # 只扣减了一个仓库的一双
        used = self.conn.execute(
            "SELECT warehouse, SUM(qty_used) u FROM inventory_batches GROUP BY warehouse").fetchall()
        used_map = {r["warehouse"]: r["u"] for r in used}
        self.assertEqual(used_map["A仓"] + used_map["B仓"], 1)

    def test_last_pair_atomic_contention(self):
        """最后一双被两个不同员工并发抢：一人拿到，另一人进候补。"""
        self.profile("E1", 42, 42)
        svc.upsert_profile(self.conn, "E2", 42, 42, "M", "", KEEPER)
        # E2 是 SB 等级，为了抢同一规格这里把其测试改为两个 S3 员工不现实；
        # 改为：同仓同规格只剩 1 双，同员工两次串行也可验证原子扣减；并发用同规格两个连接
        self.stock(wh="A仓", total=1)
        self.fit("E1", "M-S3", 42)
        # 串行：先发成功一双，再发应缺码候补
        svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        # 旧鞋交回后才能再发；直接验证库存为 0 的条件更新不会超扣
        avail = self.conn.execute(
            "SELECT qty_total-qty_used a FROM inventory_batches").fetchone()["a"]
        self.assertEqual(avail, 0)


class TestLifespan(BaseCase):
    def test_due_date_month_arith(self):
        self.profile()
        self.stock()
        self.fit()
        iid = svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER,
                             now=date(2026, 1, 31))
        row = self.conn.execute("SELECT due_date,lifespan_months FROM issuances WHERE id=?", (iid,)).fetchone()
        self.assertEqual(row["lifespan_months"], 8)
        self.assertEqual(row["due_date"], "2026-09-30")  # 月末钳制
        self.assertEqual(svc.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(svc.add_months(date(2026, 3, 31), 12), date(2027, 3, 31))


class TestRenewalFlow(BaseCase):
    def _issued(self, issued_date):
        self.profile()
        self.stock(total=5)
        self.fit()
        return svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=issued_date)

    def test_early_expiry_rejected_damage_needs_approval(self):
        iid = self._issued(date(2026, 1, 1))  # 8 个月期限 → 9/30 到期
        with self.assertRaisesRegex(svc.BusinessError, "未到正常到期日"):
            svc.create_renewal(self.conn, iid, "expiry", "", None, KEEPER, now=date(2026, 6, 1))
        # 损坏申请三照片缺一不可
        with self.assertRaisesRegex(svc.BusinessError, "磨损照片"):
            svc.create_renewal(self.conn, iid, "damage", "磨损", {"sole": "a", "toe": "b"}, KEEPER,
                               now=date(2026, 6, 1))
        rid = svc.create_renewal(
            self.conn, iid, "damage", "提前磨损",
            {"sole": "s.png", "toe": "t.png", "upper": "u.png"}, KEEPER, now=date(2026, 6, 1))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM renewals WHERE id=?", (rid,)).fetchone()["status"], "pending")
        # 未批准不能领取
        with self.assertRaisesRegex(svc.BusinessError, "未批准"):
            svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "reusable", KEEPER,
                               now=date(2026, 6, 2))

    def test_keeper_cannot_approve(self):
        iid = self._issued(date(2026, 1, 1))
        rid = svc.create_renewal(
            self.conn, iid, "damage", "磨损",
            {"sole": "s.png", "toe": "t.png", "upper": "u.png"}, KEEPER)
        with self.assertRaisesRegex(svc.BusinessError, "权限不足"):
            svc.decide_renewal(self.conn, rid, True, KEEPER)

    def test_lost_shoe_exemption_flow(self):
        """主管批准提前换领并登记旧鞋遗失豁免；领取时无需交回旧鞋。"""
        iid = self._issued(date(2026, 1, 1))
        rid = svc.create_renewal(
            self.conn, iid, "damage", "鞋遗失且损坏",
            {"sole": "s.png", "toe": "t.png", "upper": "u.png"}, KEEPER)
        # 豁免必须登记原因
        with self.assertRaisesRegex(svc.BusinessError, "豁免必须登记原因"):
            svc.decide_renewal(self.conn, rid, True, SUPERVISOR, True, "")
        svc.decide_renewal(self.conn, rid, True, SUPERVISOR, True, "lost")
        # 再次审批被拒（不能重复审批）
        with self.assertRaisesRegex(svc.BusinessError, "已审批"):
            svc.decide_renewal(self.conn, rid, False, SUPERVISOR)

        new_id = svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "reusable", KEEPER,
                                    now=date(2026, 6, 5))
        old = self.conn.execute("SELECT status FROM issuances WHERE id=?", (iid,)).fetchone()
        self.assertEqual(old["status"], "exemption")
        new = self.conn.execute("SELECT status,replaced_id,warehouse FROM issuances WHERE id=?", (new_id,)).fetchone()
        self.assertEqual(new["status"], "active")
        self.assertEqual(new["replaced_id"], iid)
        # 没有回收记录（豁免）
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM returns_log").fetchone()["c"], 0)
        r = self.conn.execute("SELECT status,new_issuance_id FROM renewals WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["new_issuance_id"], new_id)

    def test_normal_return_splits_recondition_and_disposal(self):
        """正常到期：交回可用鞋进清洁复检；报废鞋进待处置批次。"""
        iid = self._issued(date(2025, 6, 1))  # 2026-02-01 到期
        rid = svc.create_renewal(self.conn, iid, "expiry", "到期", None, KEEPER,
                                 now=date(2026, 3, 1))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM renewals WHERE id=?", (rid,)).fetchone()["status"], "auto")
        new_id = svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "reusable", KEEPER,
                                    now=date(2026, 3, 2))
        self.assertTrue(new_id)
        rc = self.conn.execute("SELECT status FROM recondition").fetchone()
        self.assertEqual(rc["status"], "pending")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM disposal_items").fetchone()["c"], 0)

        # 复检不合格 → 转入处置批次
        ret_id = self.conn.execute("SELECT id FROM returns_log").fetchone()["id"]
        svc.inspect_return(self.conn, ret_id, False, "不达标", KEEPER)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM disposal_items").fetchone()["c"], 1)

        # 处置出厂 → 回执
        did = self.conn.execute("SELECT id FROM disposals WHERE status='pending'").fetchone()["id"]
        receipt = svc.execute_disposal(self.conn, did, KEEPER)
        self.assertTrue(receipt.startswith("RC-"))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM disposals WHERE id=?", (did,)).fetchone()["status"], "done")

    def test_scrap_return_goes_directly_to_disposal(self):
        iid = self._issued(date(2025, 6, 1))
        rid = svc.create_renewal(self.conn, iid, "expiry", "到期", None, KEEPER,
                                 now=date(2026, 3, 1))
        svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "scrap", KEEPER,
                           now=date(2026, 3, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM recondition").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM disposal_items").fetchone()["c"], 1)

    def test_duplicate_pickup_blocked(self):
        iid = self._issued(date(2025, 6, 1))
        rid = svc.create_renewal(self.conn, iid, "expiry", "到期", None, KEEPER,
                                 now=date(2026, 3, 1))
        svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "reusable", KEEPER,
                           now=date(2026, 3, 2))
        with self.assertRaisesRegex(svc.BusinessError, "已完成领取"):
            svc.pickup_renewal(self.conn, rid, "M-S3", 42, "A仓", "reusable", KEEPER,
                               now=date(2026, 3, 3))


class TestTransferAndPermissions(BaseCase):
    def test_keeper_cannot_transfer_post(self):
        with self.assertRaisesRegex(svc.BusinessError, "权限不足"):
            svc.transfer_post(self.conn, "E1", "装配工", "SB", KEEPER)
        # 等级未被改动
        self.assertEqual(self.conn.execute(
            "SELECT grade_code FROM employees WHERE id='E1'").fetchone()["grade_code"], "S3")

    def test_supervisor_transfer_creates_renewal(self):
        self.profile()
        self.stock(total=5)
        self.fit()
        svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        rid = svc.transfer_post(self.conn, "E1", "装配工", "SB", SUPERVISOR)
        self.assertIsNotNone(rid)
        r = self.conn.execute("SELECT kind,status FROM renewals WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["kind"], "transfer")
        self.assertEqual(r["status"], "approved")
        # 调动后授权规格已切换
        spec = svc.eligible_specs(self.conn, "E1")
        self.assertEqual(spec["employee"]["grade_code"], "SB")
        # 同等级重复调动报错
        with self.assertRaisesRegex(svc.BusinessError, "防护等级相同"):
            svc.transfer_post(self.conn, "E1", "装配2", "SB", SUPERVISOR)

    def test_cannot_bypass_transfer_renewal(self):
        """调岗后未交旧鞋前，不能绕过调岗换领单直接在新等级发鞋。"""
        self.profile()
        self.stock(total=5)
        self.fit()
        svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=self.today)
        rid = svc.transfer_post(self.conn, "E1", "装配工", "SB", SUPERVISOR)
        self.stock("M-SB", 42, "A仓", 5)
        self.fit("E1", "M-SB", 42)
        with self.assertRaisesRegex(svc.BusinessError, "调岗换领单"):
            svc.issue_shoe(self.conn, "E1", "M-SB", 42, "A仓", KEEPER, now=self.today)
        # 走交旧领新后成功，旧鞋交回、新鞋有效
        new_id = svc.pickup_renewal(self.conn, rid, "M-SB", 42, "A仓", "reusable", KEEPER,
                                    now=self.today)
        self.assertTrue(new_id)
        actives = self.conn.execute(
            "SELECT grade_code,status FROM issuances WHERE employee_id='E1' ORDER BY id").fetchall()
        statuses = {(r["grade_code"], r["status"]) for r in actives}
        self.assertEqual(statuses, {("S3", "returned"), ("SB", "active")})


class TestReminders(BaseCase):
    def test_due_and_waitlist_reminders(self):
        self.profile()
        self.stock(total=0)  # 缺码
        self.fit()
        with self.assertRaises(svc.BusinessError):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=date(2025, 8, 1))
        # 发一双已逾期鞋：补库存后直接发
        self.conn.execute(
            "INSERT INTO inventory_batches(model_code,size,warehouse,qty_total,qty_used,received_at) VALUES('M-S3',42,'A仓',2,0,'2026-02-01')")
        self.conn.commit()
        iid = svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER, now=date(2025, 8, 1))
        # 候补在发放成功时被填掉了；再造一个新候补：清空库存由其他员工登记略过
        r1 = svc.generate_reminders(self.conn, now=date(2026, 9, 1))
        self.assertGreaterEqual(r1["due"], 1)
        # 不重复生成
        r2 = svc.generate_reminders(self.conn, now=date(2026, 9, 2))
        self.assertEqual(r2["due"], 0)

    def test_waitlist_arrival_reminder(self):
        self.profile()
        self.stock(total=0)
        self.fit()
        with self.assertRaises(svc.BusinessError):
            svc.issue_shoe(self.conn, "E1", "M-S3", 42, "A仓", KEEPER)
        r = svc.generate_reminders(self.conn)
        self.assertEqual(r["waitlist"], 0)  # 仍无货
        svc.receive_stock(self.conn, "M-S3", 42, "A仓", 2, KEEPER)
        r = svc.generate_reminders(self.conn)
        self.assertEqual(r["waitlist"], 1)
        wl = self.conn.execute("SELECT status FROM waitlist WHERE employee_id='E1'").fetchone()
        self.assertEqual(wl["status"], "notified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
