"""尽力而为的 CASTEP 输出解析器。

此模块提供了解析 CASTEP 输出的功能。
"""

from __future__ import annotations

import re
from typing import Any


# CASTEP 能量正则表达式
CASTEP_ENERGY_RE = re.compile(r"final\s+energy.*?(-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)", re.IGNORECASE)


def parse_castep_energy(text: str) -> dict[str, Any]:
    """解析 CASTEP 能量。

    参数:
        text: 输出文本

    返回:
        解析结果字典，包含 energy 和 finished
    """
    matches = [float(match.group(1)) for match in CASTEP_ENERGY_RE.finditer(text)]
    return {
        "energy": matches[-1] if matches else None,
        "finished": "finished" in text.lower() or "completed" in text.lower(),
    }
