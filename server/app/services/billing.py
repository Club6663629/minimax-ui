"""计费：成本计算 + 积分流水记账。"""
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CreditLog, User


def compute_cost(duration: int, resolution: str) -> int:
    """单次生成消耗积分。1K/2K 升级走本地超分池（2K 另有云端降级通道）。"""
    _BASE = {
        5: settings.cost_768p_5s,
        8: settings.cost_768p_8s,
        10: settings.cost_768p_10s,
        15: settings.cost_768p_15s,
    }
    cost = _BASE.get(duration, settings.cost_768p_10s)
    if resolution == "1k":
        cost += settings.cost_1k_extra
    elif resolution == "2k":
        cost += settings.cost_2k_extra
    return cost


def compute_upgrade_cost(resolution: str) -> int:
    """高清升级附加费用（仅超分成本，无基础生成费）。"""
    if resolution == "1k":
        return settings.cost_1k_extra
    if resolution == "2k":
        return settings.cost_2k_extra
    return 0


def add_credits(
    db: Session,
    user: User,
    amount: int,
    log_type: str,
    note: str = "",
    task_id: Optional[int] = None,
) -> None:
    """入账/出账并记流水。调用方负责提交事务。"""
    user.credits += amount
    db.add(
        CreditLog(
            user_id=user.id,
            amount=amount,
            type=log_type,
            note=note,
            task_id=task_id,
        )
    )


# 充值套餐（展示用；v1 通过兑换码充值）
PACKAGES = [
    {"name": "基础包", "credits": 100, "price": "¥29", "tag": "", "description": "约 10 条 768p·5s 视频"},
    {"name": "热门包", "credits": 500, "price": "¥119", "tag": "最受欢迎", "description": "约 50 条 768p·5s 视频"},
    {"name": "高级包", "credits": 2000, "price": "¥399", "tag": "超值", "description": "约 200 条 768p·5s 视频"},
]
