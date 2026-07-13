"""单位验证助手。

此模块提供了单位验证功能。
"""

from __future__ import annotations

import math


def validate_positive(value: float, label: str) -> float:
    """验证正数。

    参数:
        value: 数值
        label: 标签

    返回:
        验证后的数值

    异常:
        ValueError: 如果值不是正有限数
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} 必须是正有限数")
    return float(value)
