"""尽力而为的 Forcite 日志解析器。

此模块提供了解析 Forcite 日志的功能。
"""

from __future__ import annotations

import re
from typing import Any


# 能量值正则表达式
ENERGY_RE = re.compile(r"\b(?:total\s+)?energy\s*[=:]\s*(-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)", re.IGNORECASE)


def parse_forcite_convergence(text: str) -> dict[str, Any]:
    """解析 Forcite 收敛性。

    参数:
        text: 日志文本

    返回:
        解析结果字典，包含 converged 和 energy
    """
    lowered = text.lower()
    energies = [float(match.group(1)) for match in ENERGY_RE.finditer(text)]
    return {
        "converged": "not converged" not in lowered and ("converged" in lowered or "successfully completed" in lowered),
        "energy": energies[-1] if energies else None,
    }
