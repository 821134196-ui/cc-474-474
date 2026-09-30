"""测试一：同一员工同一防护等级只能有一双有效鞋。"""
from safety_shoes.services import PermissionDenied, ServiceError
from safety_shoes.tests.base import KEEPER, SUP, ShoeTestCase


class UniqueActiveShoeTests(ShoeTestCase):
    def test_first_issue_succeeds(self):
        iid = self.first_issue()
        issue = self.svc.get_issue(iid)
        self.assertEqual(issue["status"], "active")
        self.assertEqual(issue["pair_tag"], f"SN-{iid:05d}")

    def test_second_issue_without_renewal_rejected(self):
        self.first_issue()
        # 仓管再次发起试穿即被拦
        with self.assertRaises(ServiceError) as cx:
            self.svc.start_fitting(*KEEPER, "T001", "HX-300", "42", "42")
        self.assertIn("已有一双有效鞋", str(cx.exception))

    def test_two_issues_different_levels_allowed_after_transfer(self):
        # 原等级一双
        self.first_issue("T001", "HX-300", "42", "42", "A库")
        self.assertTrue(self.svc.active_issue("T001", "S3"))
        # 主管调岗 S3 -> S1
        self.svc.change_position(*SUP, "T001", "装配工", "S1")
        # 未结算旧鞋，新等级不能领
        ok, msg = self.svc.can_issue_now("T001")
        self.assertFalse(ok)
        self.svc.settle_transfer(*KEEPER, "T001", True, condition="reusable")
        # 旧鞋不再 active，新等级首发成功
        self.assertIsNone(self.svc.active_issue("T001", "S3"))
        fid = self.svc.start_fitting(*KEEPER, "T001", "HX-100", "40", "40")
        iid = self.svc.confirm_fitting(*KEEPER, fid, "A库")
        self.assertEqual(self.svc.get_issue(iid)["level"], "S1")
        self.assertIsNotNone(self.svc.active_issue("T001", "S1"))

    def test_duplicate_active_row_blocked_by_db_constraint(self):
        # 数据库层部分唯一索引兜底（绕过服务层直插也不行）
        iid = self.first_issue()
        inv = self.conn.execute("SELECT id FROM inventory WHERE warehouse='B库' AND model_code='HX-300' AND size='42'").fetchone()["id"]
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO issues(issue_no,emp_no,level,model_code,size_left,size_right,
                                      warehouse,inventory_id,issue_date,expire_date,status,pair_tag)
                   VALUES('DUP','T001','S3','HX-300','42','42','B库',?,date('now'),date('now','+1 year'),'active','X2')""",
                (inv,))

    def test_storekeeper_cannot_change_level(self):
        with self.assertRaises(PermissionDenied):
            self.svc.change_position(*KEEPER, "T001", "铸造工", "S5")
        emp = self.svc.get_employee("T001")
        self.assertEqual(emp["level"], "S3")

    def test_wrong_level_model_rejected(self):
        # S1 员工不能领 S3 鞋款
        with self.assertRaises(ServiceError):
            self.svc.start_fitting(*KEEPER, "T002", "HX-300", "42", "42")
