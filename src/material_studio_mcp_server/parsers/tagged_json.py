"""支持新旧 MCP 标记名称的标记 JSON 解析器。

此模块提供了从输出中提取标记 JSON 的功能。
"""

from __future__ import annotations

import json
from typing import Any

# 旧的标记
OLD_JSON_BEGIN = "__MATERIAL_STUDIO_MCP_JSON_BEGIN__"
OLD_JSON_END = "__MATERIAL_STUDIO_MCP_JSON_END__"

# 新的标记
NEW_JSON_BEGIN = "__MS_MCP_JSON_START__"
NEW_JSON_END = "__MS_MCP_JSON_END__"


def extract_any_tagged_json(output: str) -> Any | None:
    """提取任何标记的 JSON。

    参数:
        output: 输出文本

    返回:
        解析的 JSON，如果未找到则返回 None
    """
    for begin, end in ((NEW_JSON_BEGIN, NEW_JSON_END), (OLD_JSON_BEGIN, OLD_JSON_END)):
        parsed = _extract(output, begin, end)
        if parsed is not None:
            return parsed
    return None


def _extract(output: str, begin: str, end: str) -> Any | None:
    """提取标记之间的 JSON。

    参数:
        output: 输出文本
        begin: 开始标记
        end: 结束标记

    返回:
        解析的 JSON，如果未找到则返回 None
    """
    start = output.find(begin)
    if start < 0:
        return None
    start += len(begin)
    finish = output.find(end, start)
    if finish < 0:
        return None
    raw = output[start:finish].strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": "标记 JSON 不是有效的 JSON。", "raw": raw}
