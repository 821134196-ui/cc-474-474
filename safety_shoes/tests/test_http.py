"""HTTP 端到端测试：登录、页面渲染、角色权限、完整换领链路走表单。"""
from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

from safety_shoes.db import get_connection, init_db
from safety_shoes.seed import seed
from safety_shoes.server import make_handler
from safety_shoes.web.app import App


class Client:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.cookie = ""
        self.last_path = "/"

    def req(self, method, path, data=None, ctype="application/x-www-form-urlencoded",
            follow=True):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        body = urlencode(data, doseq=True).encode() if data is not None else None
        headers = {"Cookie": self.cookie}
        if body:
            headers["Content-Type"] = ctype
            headers["Referer"] = f"http://{self.host}:{self.port}{self.last_path}"
        conn.request(method, quote(path, safe="/?=&%"), body=body, headers=headers)
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8", "replace")
        sc = resp.getheader("Set-Cookie")
        if sc and "sid=" in sc and "Max-Age=0" not in sc:
            self.cookie = sc.split(";")[0]
        location = resp.getheader("Location")
        status = resp.status
        conn.close()
        if follow and status in (301, 302, 303) and location:
            from urllib.parse import urlsplit
            target = urlsplit(location).path + (("?" + urlsplit(location).query) if urlsplit(location).query else "")
            return self.req("GET", target)
        if method == "GET":
            self.last_path = quote(path, safe="/?=&%")
        return status, payload


class HttpFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / "e2e.db"
        import safety_shoes.config as cfg
        cls._old_db, cls._old_data, cls._old_photos = cfg.DB_PATH, cfg.DATA_DIR, cfg.PHOTO_DIR
        cfg.DB_PATH = cls.db_path
        cfg.DATA_DIR = Path(cls.tmp.name)
        cfg.PHOTO_DIR = Path(cls.tmp.name) / "photos"
        import safety_shoes.services as svcmod
        svcmod.config.DB_PATH = cls.db_path
        import safety_shoes.reminders as remmod
        remmod.config.DB_PATH = cls.db_path
        init_db(cls.db_path)
        seed(cls.db_path, verbose=False)
        cls.app = App(db_path=cls.db_path, photo_dir=Path(cls.tmp.name) / "photos")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.host, cls.port = cls.httpd.server_address
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        import safety_shoes.config as cfg
        cfg.DB_PATH, cfg.DATA_DIR = cls._old_db, cls._old_data

    def setUp(self):
        self.sup = Client(self.host, self.port)
        self.keep = Client(self.host, self.port)
        self.sup.req("POST", "/login", {"role": "supervisor"})
        self.keep.req("POST", "/login", {"role": "storekeeper"})

    def test_login_required(self):
        c = Client(self.host, self.port)
        status, body = c.req("GET", "/employees")
        self.assertEqual(status, 200)
        self.assertIn("选择本次操作身份", body)

    def test_all_pages_render(self):
        for path in ["/", "/employees", "/employees/E1005", "/inventory",
                     "/inventory?wh=B库", "/fitting?emp_no=E1007", "/due",
                     "/approvals", "/returns", "/waitlist", "/door", "/issues/1"]:
            status, body = self.keep.req("GET", path)
            self.assertEqual(status, 200, path)
            self.assertNotIn("Traceback", body, path)

    def test_storekeeper_cannot_change_level_via_ui(self):
        # 仓管改岗位等级被拒，闪存错误返回来源页，等级不变
        status, body = self.keep.req("POST", "/employees/E1001/position",
                                     {"position": "铸造工", "level": "S5"})
        self.assertEqual(status, 200)
        self.assertIn("权限不足", body)
        _, emp = self.keep.req("GET", "/employees/E1001")
        self.assertIn("S3", emp)

    def test_inventory_matrix_shows_qty(self):
        _, body = self.keep.req("GET", "/inventory?wh=A库")
        self.assertIn("HX-300", body)
        self.assertIn("matrix", body)

    def test_full_due_renewal_flow_e1001(self):
        # E1001 15 天后到期：登记到期换领（仓管即可）
        status, body = self.keep.req("POST", "/renewals/due", {"emp_no": "E1001"})
        self.assertEqual(status, 200)
        self.assertNotIn("✗", body)
        with get_connection(self.db_path) as conn:
            issue_id = conn.execute(
                "SELECT id FROM issues WHERE emp_no='E1001' AND status='active'").fetchone()["id"]
            rid = conn.execute(
                "SELECT id FROM renewals WHERE old_issue_id=? AND status='approved'",
                (issue_id,)).fetchone()["id"]
        status, body = self.keep.req("POST", "/returns/collect",
                                     {"issue_id": issue_id, "renewal_id": rid, "mode": "reusable"})
        self.assertEqual(status, 200)
        self.assertNotIn("✗", body)
        # 试穿 -> 确认发新鞋
        status, body = self.keep.req("POST", "/fitting/start",
                                     {"emp_no": "E1001", "renewal_id": rid,
                                      "model_code": "HX-300", "size_left": "42", "size_right": "42"})
        self.assertEqual(status, 200)
        with get_connection(self.db_path) as conn:
            fid = conn.execute("SELECT MAX(id) id FROM fittings").fetchone()["id"]
        status, body = self.keep.req("POST", f"/fittings/{fid}/confirm", {"warehouse": "A库"})
        self.assertEqual(status, 200)
        self.assertNotIn("✗", body)
        with get_connection(self.db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) c FROM issues WHERE emp_no='E1001' AND status='active'").fetchone()["c"]
            rn = conn.execute(
                "SELECT status FROM renewals WHERE id=?", (rid,)).fetchone()["status"]
        self.assertEqual(n, 1)
        self.assertEqual(rn, "completed")

    def test_supervisor_approves_early_wear_e1003(self):
        with get_connection(self.db_path) as conn:
            rid = conn.execute(
                "SELECT id FROM renewals WHERE emp_no='E1003' AND status='requested'").fetchone()["id"]
        # 仓管批准被拒（闪存返回来源页）
        s, body = self.keep.req("POST", f"/renewals/{rid}/approve",
                                {"approved": "1", "note": "x"})
        self.assertIn("权限不足", body)
        # 主管批准
        s, _ = self.sup.req("POST", f"/renewals/{rid}/approve",
                            {"approved": "1", "note": "同意提前换领"})
        self.assertEqual(s, 200)
        _, body = self.sup.req("GET", "/approvals")
        self.assertIn("同意提前换领", body)

    def test_waitlist_notified_after_restock(self):
        s, _ = self.keep.req("POST", "/inventory/restock",
                             {"warehouse": "A库", "model_code": "HX-200",
                              "size": "42", "qty": "2"})
        self.assertEqual(s, 200)
        from safety_shoes.reminders import ReminderWorker
        r = ReminderWorker(self.db_path, interval=9999).run_once()
        self.assertGreaterEqual(r["waitlist"], 1)
        _, body = self.keep.req("GET", "/waitlist")
        self.assertIn("已通知", body)

    def test_door_swipe(self):
        s, body = self.keep.req("POST", "/door/swipe", {"emp_no": "E1005"})
        self.assertEqual(s, 200)
        self.assertIn("放行", body)
        self.assertIn("调动", body)


if __name__ == "__main__":
    unittest.main()
