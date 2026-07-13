"""生成的 MaterialsScript Perl 的安全检查。

此模块提供了对生成的脚本进行安全检查的功能。
"""

from __future__ import annotations

import re

from material_studio_mcp_server.scripts import validate_materialscript


# 危险模式列表
DANGEROUS_PATTERNS = [
    re.compile(r"\bsystem\s*\(", re.IGNORECASE),
    re.compile(r"`[^`]+`"),
    re.compile(r"\bqx\s*[/({\[]", re.IGNORECASE),
    re.compile(r"\bunlink\b", re.IGNORECASE),
    re.compile(r"\brmdir\b", re.IGNORECASE),
    re.compile(r"\bFile::Path\b", re.IGNORECASE),
    re.compile(r"\bLWP::|HTTP::|IO::Socket", re.IGNORECASE),
]


def validate_generated_script(script: str) -> dict[str, object]:
    """验证结构化生成的脚本，比自定义脚本更严格。

    参数:
        script: 脚本内容

    返回:
        验证结果字典，包含 valid、errors 和 warnings
    """
    base = validate_materialscript(script)
    errors = list(base["errors"])
    warnings = list(base["warnings"])
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(script):
            errors.append(f"生成的脚本包含不允许的模式: {pattern.pattern}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
