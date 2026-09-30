"""工厂劳保鞋尺码发放与到期换领系统 —— 全局配置。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SAFETY_SHOES_DATA", BASE_DIR / "data"))
PHOTO_DIR = DATA_DIR / "photos"
DB_PATH = Path(os.environ.get("SAFETY_SHOES_DB", DATA_DIR / "safety_shoes.db"))

# 工业配色：工业白 / 钢蓝 / 荧光黄
COLORS = {
    "white": "#F4F6F8",
    "steel": "#2F5D7C",
    "steel_dark": "#1E3D52",
    "hi_viz": "#F5D00F",
}

# 默认使用期限（月）—— 正常到期换领
DEFAULT_LIFESPAN_MONTHS = 12
# 提前磨损审批：距到期仍超过该天数视为"提前"，需主管审批
EARLY_RENEW_THRESHOLD_DAYS = 30
# 到期提醒窗口
DUE_SOON_DAYS = 21
# 候补提醒窗口（库存补齐后通知）
REMINDER_INTERVAL_SECONDS = 60

# 岗位防护等级 -> 允许发放的鞋款
# S1 基础防砸 / S2 防砸防水 / S3 防砸防穿刺 / S4 电绝缘 / S5 耐高温
PROTECTION_LEVELS = ["S1", "S2", "S3", "S4", "S5"]

# 固定尺码序列（中国码）
SIZES = [str(n) for n in range(35, 47)]

# 鞋款 -> 可覆盖的防护等级
MODEL_LEVELS = {
    "HX-100 基础防砸": ["S1"],
    "HX-200 防水工装": ["S2"],
    "HX-300 防穿刺安全靴": ["S3", "S2"],
    "HX-400 电绝缘鞋": ["S4"],
    "HX-500 耐高温铸工靴": ["S5"],
}

ROLES = ["supervisor", "storekeeper"]
ROLE_NAMES = {"supervisor": "主管", "storekeeper": "仓管"}
WAREHOUSES = ["A库", "B库"]
