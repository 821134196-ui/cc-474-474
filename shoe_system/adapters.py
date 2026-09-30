"""模拟外部适配器。

- BadgeAdapter：门禁系统工号同步（本地模拟：根据员工代号生成稳定工号并模拟刷卡校验）
- DisposalVendor：报废鞋处置厂商，取走批次后出具回执
"""
from __future__ import annotations

import hashlib


class BadgeAdapter:
    """模拟门禁/HR 系统：按员工代号同步门禁工号。"""

    PROVIDER = "门禁系统(模拟)"

    @staticmethod
    def sync_badge(employee_id: str) -> str:
        h = hashlib.sha1(employee_id.encode()).hexdigest()[:8].upper()
        return f"BD-{h}"

    @staticmethod
    def verify(badge_no: str) -> bool:
        """模拟门禁刷卡：BD- 开头 11 位即视为有效工号。"""
        return bool(badge_no) and badge_no.startswith("BD-") and len(badge_no) == 11

    @staticmethod
    def swipecard(badge_no: str) -> dict:
        """模拟员工在发放窗口刷卡，返回门禁侧员工信息。"""
        if not BadgeAdapter.verify(badge_no):
            return {"ok": False, "message": "门禁拒绝：工号无效"}
        return {"ok": True, "provider": BadgeAdapter.PROVIDER,
                "message": f"门禁核验通过 {badge_no}"}


class DisposalVendor:
    """模拟有资质的劳保鞋销毁厂商。"""

    PROVIDER = "绿环再生(模拟)"

    def process(self, batch_no: str, count: int) -> dict:
        digest = hashlib.sha1(f"{batch_no}:{count}".encode()).hexdigest()[:10].upper()
        return {
            "vendor": self.PROVIDER,
            "receipt_no": f"RC-{digest}",
            "batch_no": batch_no,
            "count": count,
        }
