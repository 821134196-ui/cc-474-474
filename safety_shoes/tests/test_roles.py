"""测试五：岗位权限与适配器（门禁工号 / 处置回执）。"""
from safety_shoes.adapters.door import MockDoorAdapter
from safety_shoes.adapters.disposal import MockDisposalAdapter
from safety_shoes.services import PermissionDenied
from safety_shoes.tests.base import KEEPER, SUP, ShoeTestCase


class RolePermissionTests(ShoeTestCase):
    def test_storekeeper_cannot_change_position_level(self):
        with self.assertRaises(PermissionDenied):
            self.svc.change_position(*KEEPER, "T001", "铸造工", "S5")

    def test_supervisor_can_change_position_level(self):
        self.svc.change_position(*SUP, "T001", "铸造工", "S5")
        emp = self.svc.get_employee("T001")
        self.assertEqual(emp["level"], "S5")
        self.assertIsNotNone(self.svc.pending_transfer("T001"))

    def test_storekeeper_cannot_approve_anything(self):
        iid = self.seed_active("T003", "S5", "HX-500", "43", "43", "A库", 230)
        rid = self.svc.create_damage_renewal(
            *KEEPER, "T003", "damage", "x", self.photos())
        with self.assertRaises(PermissionDenied):
            self.svc.approve_renewal(*KEEPER, rid, True)
        self.svc.approve_renewal(*SUP, rid, True)
        self.svc.register_return(*KEEPER, iid, False, exemption_reason="lost")
        ret_id = self.conn.execute(
            "SELECT id FROM returns WHERE issue_id=?", (iid,)).fetchone()["id"]
        with self.assertRaises(PermissionDenied):
            self.svc.approve_exemption(*KEEPER, ret_id, True)

    def test_eligible_models_by_level(self):
        s3 = {m["code"] for m in self.svc.eligible_models("S3")}
        self.assertEqual(s3, {"HX-300"})
        s2 = {m["code"] for m in self.svc.eligible_models("S2")}
        self.assertEqual(s2, {"HX-200", "HX-300"})
        s5 = {m["code"] for m in self.svc.eligible_models("S5")}
        self.assertEqual(s5, {"HX-500"})

    def test_door_adapter_unknown_and_active(self):
        door = MockDoorAdapter(self.svc)
        bad = door.swipe("NOPE")
        self.assertFalse(bad["ok"])
        ok = door.swipe("T001")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["level"], "S3")
        self.assertEqual(ok["name"], "测试甲")

    def test_door_warns_on_pending_transfer(self):
        door = MockDoorAdapter(self.svc)
        self.svc.change_position(*SUP, "T001", "装配工", "S1")
        r = door.swipe("T001")
        self.assertTrue(r["ok"])
        self.assertIn("调动", r["warning"])

    def test_disposal_adapter_receipt_deterministic(self):
        ad = MockDisposalAdapter()
        r1 = ad.dispatch("DSP-20260930-0007", 3)
        r2 = ad.dispatch("DSP-20260930-0007", 3)
        self.assertEqual(r1, r2)
        self.assertTrue(r1.startswith("RCV-"))
        r3 = ad.dispatch("DSP-20260930-0008", 3)
        self.assertNotEqual(r1, r3)
