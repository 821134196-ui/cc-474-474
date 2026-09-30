"""测试二：两处仓库并发发放，仅一次成功（有效鞋唯一 + 库存竞争）。"""
import threading

from safety_shoes.db import get_connection
from safety_shoes.services import ServiceError, ShoeService
from safety_shoes.tests.base import KEEPER, ShoeTestCase


class ConcurrencyTests(ShoeTestCase):
    def _worker(self, results, idx, barrier, emp_no, wh, model="HX-300"):
        """独立连接：建试穿单 -> 同时确认。"""
        conn = get_connection(self.db_path)
        try:
            svc = ShoeService(conn=conn)
            fid = svc.start_fitting(*KEEPER, emp_no, model, "42", "42")
            barrier.wait(timeout=10)
            try:
                iid = svc.confirm_fitting(*KEEPER, fid, wh)
                results[idx] = ("ok", iid)
            except ServiceError as exc:
                results[idx] = ("reject", str(exc))
            except Exception as exc:  # sqlite3 锁错误也算失败方
                results[idx] = ("error", f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()

    def test_same_employee_two_warehouses_only_one_wins(self):
        barrier = threading.Barrier(2)
        results: dict = {}
        threads = [
            threading.Thread(target=self._worker, args=(results, 0, barrier, "T001", "A库")),
            threading.Thread(target=self._worker, args=(results, 1, barrier, "T001", "B库")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        winners = [r for r in results.values() if r[0] == "ok"]
        losers = [r for r in results.values() if r[0] != "ok"]
        self.assertEqual(len(winners), 1, f"应有且仅有一次发放成功，实际 {results}")
        self.assertEqual(len(losers), 1)
        # 只有一双 active
        actives = self.conn.execute(
            "SELECT COUNT(*) c FROM issues WHERE emp_no='T001' AND status='active'").fetchone()["c"]
        self.assertEqual(actives, 1)

    def test_last_pair_stock_race_only_one_wins(self):
        """两名 S3 员工抢 A 库唯一一双 HX-300/42。"""
        self.conn.execute(
            "INSERT INTO employees(emp_no,name,position,level) VALUES('T009','测试九','冲压工','S3')")
        self.conn.execute(
            "INSERT INTO size_profiles(emp_no,size_left,size_right) VALUES('T009','42','42')")
        self.assertEqual(self.stock("A库", "HX-300", "42"), 1)

        barrier = threading.Barrier(2)
        results: dict = {}
        threads = [
            threading.Thread(target=self._worker, args=(results, 0, barrier, "T001", "A库")),
            threading.Thread(target=self._worker, args=(results, 1, barrier, "T009", "A库")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        winners = [r for r in results.values() if r[0] == "ok"]
        self.assertEqual(len(winners), 1, f"唯一库存只能发一双，实际 {results}")
        self.assertEqual(self.stock("A库", "HX-300", "42"), 0)
        # 输的一方报缺码，可登记候补
        loser_reason = [r for r in results.values() if r[0] != "ok"][0][1]
        self.assertTrue("缺码" in loser_reason or "有效鞋" in loser_reason)

    def test_two_warehouses_stock_independent(self):
        # 各有一双时两个仓库各发一双给不同员工，都成功
        self.conn.execute(
            "INSERT INTO employees(emp_no,name,position,level) VALUES('T009','测试九','冲压工','S3')")
        self.conn.execute(
            "INSERT INTO size_profiles(emp_no,size_left,size_right) VALUES('T009','42','42')")
        barrier = threading.Barrier(2)
        results: dict = {}
        threads = [
            threading.Thread(target=self._worker, args=(results, 0, barrier, "T001", "A库")),
            threading.Thread(target=self._worker, args=(results, 1, barrier, "T009", "B库")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(sum(1 for r in results.values() if r[0] == "ok"), 2, results)
