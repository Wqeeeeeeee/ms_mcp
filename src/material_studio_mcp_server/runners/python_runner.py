"""默认禁用的 MaterialsScript Python runner 脚手架。

此模块提供了 Python runner 的脚手架。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PythonMaterialsScriptRunner:
    """Python MaterialsScript runner。

    属性:
        runner_path: runner 路径
    """

    runner_path: Path | None = None

    def available(self) -> bool:
        """检查 runner 是否可用。

        返回:
            是否可用
        """
        return bool(self.runner_path and self.runner_path.exists())

    def explain_unavailable(self) -> str:
        """解释为什么不可用。

        返回:
            解释字符串
        """
        return (
            "MaterialsScript Python 执行已为较新的 Materials Studio 版本搭建脚手架，"
            "但未配置经过验证的 Python runner。Perl RunMatScript 仍然是默认的。"
        )
