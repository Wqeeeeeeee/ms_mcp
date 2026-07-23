"""尽力而为的结构摘要解析器，用于标记 JSON 载荷。

此模块提供了解析结构摘要的功能。
"""

from __future__ import annotations

from typing import Any


def parse_structure_summary(payload: Any) -> dict[str, Any]:
    """解析结构摘要。

    参数:
        payload: 载荷

    返回:
        解析结果字典
    """
    if not isinstance(payload, dict):
        return {"warnings": ["未找到结构化摘要载荷。"]}
    return {
        "atom_count": payload.get("atom_count"),
        "bond_count": payload.get("bond_count"),
        "elements": payload.get("elements", {}),
        "document_name": payload.get("document_name"),
        "file_path": payload.get("source") or payload.get("output"),
        "warnings": payload.get("warnings", []),
    }
