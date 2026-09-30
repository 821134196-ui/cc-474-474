"""进程内提醒任务：后台线程周期生成到期提醒与候补到货提醒。"""
from __future__ import annotations

import threading
import time

from . import services


class ReminderWorker(threading.Thread):
    daemon = True

    def __init__(self, conn_factory, interval_sec: int = 60):
        super().__init__(name="reminder-worker")
        self.conn_factory = conn_factory
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self.last_result = {"due": 0, "waitlist": 0}
        self.last_run = None

    def run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                self.tick()
            except Exception as exc:  # 后台任务不能因单次异常退出
                print(f"[reminder] tick 失败: {exc}")

    def tick(self) -> dict:
        conn = self.conn_factory()
        try:
            result = services.generate_reminders(conn)
        finally:
            conn.close()
        if result["due"] or result["waitlist"]:
            print(f"[reminder] 新增提醒：到期 {result['due']} 条，候补到货 {result['waitlist']} 条")
        self.last_result = result
        self.last_run = time.strftime("%Y-%m-%d %H:%M:%S")
        return result

    def stop(self) -> None:
        self._stop.set()
