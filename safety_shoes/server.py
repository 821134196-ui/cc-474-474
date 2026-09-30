"""HTTP 服务器启动 + 进程内提醒任务。"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .db import init_db
from .reminders import ReminderWorker
from .web.app import App


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默；演示系统不刷访问日志

        def _serve(self, method):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            status, headers, payload = app.handle(method, self.path, self.headers, body)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000, reseed: bool = False,
          reminders: bool = True):
    init_db()
    if reseed:
        from .seed import seed
        seed()
    app = App()
    worker = None
    if reminders:
        worker = ReminderWorker()
        worker.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"劳保鞋系统已启动：http://{host}:{port}")
    print(f"数据库 {config.DB_PATH} · 照片目录 {config.PHOTO_DIR}")
    print("演示账号：M01 周主管（主管） / K01 钱仓管（仓管）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if worker:
            worker.stop()
        httpd.server_close()
