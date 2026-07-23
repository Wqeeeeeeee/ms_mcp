"""共享的翻译器助手。

此模块提供了翻译器共享的辅助函数。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from material_studio_mcp_server.parsers.tagged_json import NEW_JSON_BEGIN, NEW_JSON_END
from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.scripts import SCRIPT_HEADER


def tagged_json_print(payload: dict[str, Any]) -> str:
    """生成打印标记 JSON 的 Perl 代码。

    参数:
        payload: 要打印的载荷

    返回:
        Perl 代码字符串
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        f'print "{NEW_JSON_BEGIN}\\n";\n'
        f"print {perl_string(raw)};\n"
        'print "\\n";\n'
        f'print "{NEW_JSON_END}\\n";\n'
    )


def path_literal(path: str | Path) -> str:
    """返回路径的 Perl 字面量。

    参数:
        path: 路径

    返回:
        Perl 字符串字面量
    """
    return perl_string(path)


def header() -> str:
    """返回脚本头部。

    返回:
        脚本头部字符串
    """
    return SCRIPT_HEADER
