"""测试六：试穿调码、左右脚差异、缺码候补、清洁复检与处置链路。"""
from safety_shoes.services import ServiceError
from safety_shoes.tests.base import KEEPER, SUP, ShoeTestCase


class FittingAndDisposalTests(ShoeTestCase):
    def test_size_adjustment_then_confirm(self):
        fid = self.svc.start_fitting(*KEEPER, "T001", "HX-300", "42", "42")
        self.svc.adjust_fitting(*KEEPER, fid, "43", "43")
        # 43 两仓库都无货 -> 确认失败回滚
        with self.assertRaises(ServiceError):
            self.svc.confirm_fitting(*KEEPER, fid, "A库")
        # 调回 42 后确认成功，调码历史留痕
        self.svc.adjust_fitting(*KEEPER, fid, "42", "42")
        iid = self.svc.confirm_fitting(*KEEPER, fid, "A库")
        issue = self.svc.get_issue(iid)
        self.assertEqual((issue["size_left"], issue["size_right"]), ("42", "42"))
        self.assertEqual(self.svc.get_fitting(fid)["tried_sizes"], "42/42;43/43")
        self.assertEqual(self.stock("A库", "HX-300", "42"), 0)

    def test_mismatched_feet_consume_two_sizes(self):
        # 给 A 库补 41/43 各一双，左右脚异码发放
        self.svc.restock(*KEEPER, "A库", "HX-300", "41", 1)
        self.svc.restock(*KEEPER, "A库", "HX-300", "43", 1)
        fid = self.svc.start_fitting(*KEEPER, "T001", "HX-300", "41", "43")
        iid = self.svc.confirm_fitting(*KEEPER, fid, "A库")
        issue = self.svc.get_issue(iid)
        self.assertEqual(issue["size_left"], "41")
        self.assertEqual(issue["size_right"], "43")
        self.assertEqual(self.stock("A库", "HX-300", "41"), 0)
        self.assertEqual(self.stock("A库", "HX-300", "43"), 0)
        # 余鞋汇入 MIX 库位一双
        self.assertEqual(self.stock("A库", "HX-300", "41/43"), 1)

    def test_waitlist_join_and_notify_on_restock(self):
        # HX-200/42 两仓各 2 双；先发出一双到 1，另一员工再候补同码不缺货则直接试穿。
        # 这里制造缺码：HX-500/44 无货，E? 候补
        self.svc.upsert_profile(*KEEPER, "T003", "44", "44")
        wid = self.svc.join_waitlist(*KEEPER, "T003", "HX-500", "44", "44")
        n = self.svc.scan_waitlist()
        self.assertEqual(n, 0)
        self.svc.restock(*KEEPER, "A库", "HX-500", "44", 1)
        n = self.svc.scan_waitlist()
        self.assertEqual(n, 1)
        wl = self.conn.execute("SELECT status FROM waitlist WHERE id=?", (wid,)).fetchone()
        self.assertEqual(wl["status"], "notified")
        msgs = self.svc.list_reminders()
        self.assertTrue(any("候补到货" in m["message"] for m in msgs))

    def test_missing_size_confirm_fails_no_deduct(self):
        fid = self.svc.start_fitting(*KEEPER, "T002", "HX-100", "46", "46")
        before = self.stock("A库", "HX-100", "40")
        with self.assertRaises(ServiceError):
            self.svc.confirm_fitting(*KEEPER, fid, "A库")
        # 试穿单仍 pending，库存未动，可登记候补
        self.assertEqual(self.svc.get_fitting(fid)["status"], "pending")
        self.assertEqual(self.stock("A库", "HX-100", "40"), before)
        self.svc.join_waitlist(*KEEPER, "T002", "HX-100", "46", "46")

    def test_reusable_return_inspect_back_to_stock(self):
        old_id = self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 350)
        rid = self.svc.create_due_renewal(*KEEPER, "T002")
        self.svc.register_return(*KEEPER, old_id, True, condition="reusable", renewal_id=rid)
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id,)).fetchone()["id"]
        q0 = self.stock("A库", "HX-100", "40")
        self.svc.cleaning_inspect(*KEEPER, ret_id, True)
        self.assertEqual(self.stock("A库", "HX-100", "40"), q0 + 1)
        ret = self.conn.execute("SELECT status FROM returns WHERE id=?", (ret_id,)).fetchone()
        self.assertEqual(ret["status"], "back_to_stock")
        # 复检不过 -> 转待处置
        old_id2 = self.seed_active("T004", "S2", "HX-200", "42", "42", "B库", 350)
        rid2 = self.svc.create_due_renewal(*KEEPER, "T004")
        self.svc.register_return(*KEEPER, old_id2, True, condition="reusable", renewal_id=rid2)
        ret2 = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id2,)).fetchone()["id"]
        self.svc.cleaning_inspect(*KEEPER, ret2, False)
        r2 = self.conn.execute("SELECT status,condition FROM returns WHERE id=?", (ret2,)).fetchone()
        self.assertEqual(r2["status"], "disposal_pending")
        self.assertEqual(r2["condition"], "scrap")

    def test_scrap_to_disposal_batch_and_receipt(self):
        old_id = self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 350)
        rid = self.svc.create_due_renewal(*KEEPER, "T002")
        self.svc.register_return(*KEEPER, old_id, True, condition="scrap", renewal_id=rid)
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id,)).fetchone()["id"]
        bid = self.svc.create_disposal_batch(*KEEPER, "A库", [ret_id])
        ret = self.conn.execute("SELECT status FROM returns WHERE id=?", (ret_id,)).fetchone()
        self.assertEqual(ret["status"], "disposed")
        receipt = self.svc.dispatch_disposal(*KEEPER, bid)
        self.assertTrue(receipt.startswith("RCV-"))
        b = self.conn.execute("SELECT * FROM disposal_batches WHERE id=?", (bid,)).fetchone()
        self.assertEqual(b["status"], "dispatched")
        self.assertEqual(b["receipt_no"], receipt)
        # 不能重复调出
        with self.assertRaises(ServiceError):
            self.svc.dispatch_disposal(*KEEPER, bid)

    def test_disposal_batch_warehouse_isolation(self):
        old_a = self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 350)
        self.svc.register_return(*KEEPER, old_a, True, condition="scrap")
        ret_a = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_a,)).fetchone()["id"]
        # 不能把 A 库的鞋放进 B 库批次
        with self.assertRaises(ServiceError):
            self.svc.create_disposal_batch(*KEEPER, "B库", [ret_a])
