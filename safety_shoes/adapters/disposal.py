"""模拟危废/旧鞋处置回执适配器。"""
from __future__ import annotations

import hashlib


class MockDisposalAdapter:
    """生成确定性的处置回执编号（不依赖随机数，便于测试比对）。"""

    def dispatch(self, batch_no: str, item_count: int) -> str:
        digest = hashlib.sha1(f"{batch_no}:{item_count}".encode()).hexdigest()[:8].upper()
        return f"RCV-{batch_no.replace('DSP-', '')}-{digest}"
