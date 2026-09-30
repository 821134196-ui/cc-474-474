"""模拟门禁适配器：刷工号进厂，校验员工是否在册、当日是否已领鞋等。

真实环境会对接门禁控制器；这里返回确定性的模拟结果，便于演示和测试。
"""
from __future__ import annotations

from datetime import date


class MockDoorAdapter:
    def __init__(self, service):
        self.svc = service

    def swipe(self, emp_no: str) -> dict:
        emp = self.svc.get_emp_no_safe(emp_no)
        if not emp:
            return {"ok": False, "code": "UNKNOWN", "message": f"未知工号 {emp_no}：门禁拒绝"}
        if not emp["active"]:
            return {"ok": False, "code": "DISABLED", "message": f"{emp_no} 已停用，门禁拒绝"}
        tr = self.svc.pending_transfer(emp_no)
        warn = None
        if tr:
            warn = "有未结算的跨岗位调动，请到劳保窗口结算旧鞋"
        active = None
        try:
            active = self.svc.active_issue(emp_no)
        except Exception:
            pass
        return {
            "ok": True,
            "code": "ALLOW",
            "emp_no": emp_no,
            "name": emp["name"],
            "position": emp["position"],
            "level": emp["level"],
            "has_shoes": bool(active),
            "warning": warn,
            "message": f"{date.today():%Y-%m-%d} 放行 {emp['name']}（{emp['position']}·{emp['level']}）",
        }
