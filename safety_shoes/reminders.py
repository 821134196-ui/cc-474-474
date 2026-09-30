"""进程内提醒任务：后台线程周期生成到期提醒与缺码候补到货提醒。"""
from __future__ import annotations

import threading
import time

from . import config
from .db import get_connection
from .services import ShoeService


class ReminderWorker(threading.Thread):
    daemon = True

    def __init__(self, db_path=None, interval: int | None = None):
        super().__init__(name="reminder-worker")
        self.db_path = db_path or config.DB_PATH
        self.interval = interval or config.REMINDER_INTERVAL_SECONDS
        self._stop = threading.Event()
        self.last_run: dict | None = None

    def stop(self):
        self._stop.set()

    def run_once(self) -> dict:
        conn = get_connection(self.db_path)
        try:
            svc = ShoeService(conn=conn)
            due_n = svc.scan_due()
            wait_n = svc.scan_waitlist()
            return {"due": due_n, "waitlist": wait_n}
        finally:
            conn.close()

    def run(self):
        # 启动即跑一次，之后周期执行
        while not self._stop.is_set():
            try:
                self.last_run = self.run_once()
            except Exception as exc:  # 后台任务不中断进程
                self.last_run = {"error": str(exc)}
            self._stop.wait(self.interval)
