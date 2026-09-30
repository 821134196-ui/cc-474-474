"""测试四：旧鞋豁免审批（遗失）+ 损坏提前磨损审批链路。"""
from safety_shoes.services import PermissionDenied, ServiceError
from safety_shoes.tests.base import KEEPER, SUP, ShoeTestCase


class ExemptionAndDamageTests(ShoeTestCase):
    def _approved_damage_renewal(self, emp_no="T003", model="HX-500", size="43", wh="A库"):
        iid = self.seed_active(emp_no, "S5", model, size, size, wh, 220)
        rid = self.svc.create_damage_renewal(
            *KEEPER, emp_no, "early_wear", "鞋底过早磨平", self.photos())
        return iid, rid

    def test_damage_claim_requires_three_photos(self):
        self.seed_active("T003", "S5", "HX-500", "43", "43", "A库", 220)
        with self.assertRaises(ServiceError):
            self.svc.create_damage_renewal(
                *KEEPER, "T003", "damage", "描述",
                {"sole": "a.svg", "toe": "", "upper": "c.svg"})

    def test_storekeeper_cannot_approve_renewal(self):
        _, rid = self._approved_damage_renewal()
        with self.assertRaises(PermissionDenied):
            self.svc.approve_renewal(*KEEPER, rid, True)
        r = self.conn.execute("SELECT status FROM renewals WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["status"], "requested")

    def test_supervisor_approves_then_early_renewal_flow(self):
        old_id, rid = self._approved_damage_renewal()
        self.svc.approve_renewal(*SUP, rid, True, "确认提前磨损")
        # 批准后未交旧鞋，仍不能领新鞋
        fid = self.svc.start_fitting(*KEEPER, "T003", "HX-500", "43", "43", renewal_id=rid)
        with self.assertRaises(ServiceError) as cx:
            self.svc.confirm_fitting(*KEEPER, fid, "A库")
        self.assertIn("旧鞋尚未交回", str(cx.exception))
        # 交回报废旧鞋
        self.svc.register_return(*KEEPER, old_id, True, condition="scrap", renewal_id=rid)
        new_id = self.svc.confirm_fitting(*KEEPER, fid, "A库")
        self.assertEqual(self.svc.get_issue(new_id)["status"], "active")
        self.assertEqual(self.svc.get_issue(old_id)["status"], "renewed")
        rn = self.conn.execute("SELECT status FROM renewals WHERE id=?", (rid,)).fetchone()
        self.assertEqual(rn["status"], "completed")

    def test_lost_exemption_pending_blocks_new_shoes(self):
        old_id, rid = self._approved_damage_renewal()
        self.svc.approve_renewal(*SUP, rid, True)
        self.svc.register_return(*KEEPER, old_id, False, exemption_reason="lost", renewal_id=rid)
        # 豁免待批 -> 新鞋发不出
        fid = self.svc.start_fitting(*KEEPER, "T003", "HX-500", "43", "43", renewal_id=rid)
        with self.assertRaises(ServiceError):
            self.svc.confirm_fitting(*KEEPER, fid, "A库")
        # 仓管不能批豁免
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id,)).fetchone()["id"]
        with self.assertRaises(PermissionDenied):
            self.svc.approve_exemption(*KEEPER, ret_id, True)

    def test_lost_exemption_approved_allows_new_shoes(self):
        old_id, rid = self._approved_damage_renewal()
        self.svc.approve_renewal(*SUP, rid, True)
        self.svc.register_return(*KEEPER, old_id, False, exemption_reason="lost", renewal_id=rid)
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id,)).fetchone()["id"]
        self.svc.approve_exemption(*SUP, ret_id, True)
        fid = self.svc.start_fitting(*KEEPER, "T003", "HX-500", "43", "43", renewal_id=rid)
        new_id = self.svc.confirm_fitting(*KEEPER, fid, "B库")
        self.assertTrue(self.svc.get_issue(new_id)["issue_no"])
        # 豁免记录已批
        ret = self.conn.execute("SELECT * FROM returns WHERE id=?", (ret_id,)).fetchone()
        self.assertEqual(ret["approve_status"], "approved")

    def test_exemption_rejected_employee_must_return(self):
        old_id, rid = self._approved_damage_renewal()
        self.svc.approve_renewal(*SUP, rid, True)
        self.svc.register_return(*KEEPER, old_id, False, exemption_reason="lost", renewal_id=rid)
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (old_id,)).fetchone()["id"]
        self.svc.approve_exemption(*SUP, ret_id, False)
        fid = self.svc.start_fitting(*KEEPER, "T003", "HX-500", "43", "43", renewal_id=rid)
        with self.assertRaises(ServiceError):
            self.svc.confirm_fitting(*KEEPER, fid, "A库")

    def test_double_return_rejected(self):
        old_id, rid = self._approved_damage_renewal()
        self.svc.approve_renewal(*SUP, rid, True)
        self.svc.register_return(*KEEPER, old_id, True, condition="reusable", renewal_id=rid)
        with self.assertRaises(ServiceError):
            self.svc.register_return(*KEEPER, old_id, True, condition="scrap", renewal_id=rid)
