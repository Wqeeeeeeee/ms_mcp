"""Materials Studio 适配器基类。

此模块定义了 Materials Studio 脚本执行的抽象基类。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class MaterialsStudioAdapter(ABC):
    """Materials Studio 脚本执行适配器的抽象基类。

    所有适配器必须实现以下方法:
    - create_structure: 创建结构
    - run_geometry_optimization: 运行几何优化
    - run_script: 运行脚本
    """

    @abstractmethod
    def create_structure(self, spec: dict[str, Any], job_dir: Path) -> Path:
        """创建结构文件。

        参数:
            spec: 结构规格字典
            job_dir: 作业目录路径

        返回:
            创建的结构文件路径

        异常:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    @abstractmethod
    def run_geometry_optimization(
        self,
        structure_path: Path,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """运行几何优化。

        参数:
            structure_path: 结构文件路径
            settings: 运行设置字典

        返回:
            包含作业信息的字典

        异常:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    @abstractmethod
    def run_script(
        self,
        script_path: Path,
        job_dir: Path,
        timeout_sec: int = 3600,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行脚本。

        参数:
            script_path: 脚本文件路径
            job_dir: 作业目录路径
            timeout_sec: 超时时间（秒）
            args: 命令行参数列表

        返回:
            包含执行结果的字典

        异常:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError
