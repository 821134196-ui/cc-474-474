"""测试三：使用期限计算（月末截断、到期窗口、到期/提前规则）。"""
from datetime import date

from safety_shoes import config
from safety_shoes.services import ServiceError, add_months, days_to_expire
from safety_shoes.tests.base import KEEPER, ShoeTestCase


class PeriodCalcTests(ShoeTestCase):
    def test_add_months_normal(self):
        self.assertEqual(add_months(date(2026, 1, 15), 12), date(2027, 1, 15))
        self.assertEqual(add_months(date(2026, 6, 1), 6), date(2026, 12, 1))

    def test_add_months_end_of_month_clamped(self):
        # 1/31 + 1 月 -> 2/28；闰年 2/29
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        # 发放日 + 12 个月的使用期限
        self.assertEqual(add_months(date(2025, 12, 31), 12), date(2026, 12, 31))

    def test_days_to_expire(self):
        self.assertEqual(days_to_expire(date.today().isoformat()), 0)
        future = (date.today().replace(day=1) if date.today().day != 1 else date.today())
        self.assertIsInstance(days_to_expire("2099-01-01"), int)

    def test_issue_sets_expire_12_months(self):
        iid = self.first_issue()
        issue = self.svc.get_issue(iid)
        expected = add_months(date.today(), 12).isoformat()
        self.assertEqual(issue["expire_date"], expected)

    def test_due_renewal_only_inside_window(self):
        # 刚发 100 天：距到期 265 天 > 30，拒绝到期换领
        fresh = self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 100)
        with self.assertRaises(ServiceError) as cx:
            self.svc.create_due_renewal(*KEEPER, "T002")
        self.assertIn("未到换领窗口", str(cx.exception))
        self.assertEqual(self.svc.active_issue("T002")["id"], fresh)

    def test_due_renewal_allowed_at_threshold(self):
        # 350 天：剩 15 天 <= 30，允许，且建成即批准
        self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 350)
        rid = self.svc.create_due_renewal(*KEEPER, "T002")
        r = self.conn.execute("SELECT * FROM renewals WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["status"], "approved")
        self.assertEqual(r["reason"], "due")

    def test_overdue_renewal_allowed(self):
        self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 400)
        rid = self.svc.create_due_renewal(*KEEPER, "T002")
        self.assertTrue(rid)

    def test_due_scan_generates_reminders(self):
        self.seed_active("T002", "S1", "HX-100", "40", "40", "A库", 350)
        n = self.svc.scan_due()
        self.assertGreaterEqual(n, 1)
        msgs = self.svc.list_reminders()
        self.assertTrue(any("T002" in m["message"] for m in msgs))

    def test_threshold_constant(self):
        self.assertEqual(config.EARLY_RENEW_THRESHOLD_DAYS, 30)
