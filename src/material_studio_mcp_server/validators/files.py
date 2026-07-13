"""工作区路径验证助手。

此模块提供了工作区路径验证功能。
"""

from __future__ import annotations

from pathlib import Path


def ensure_within_workspace(path: str | Path, workspace_root: str | Path) -> Path:
    """确保路径在工作区内。

    参数:
        path: 路径
        workspace_root: 工作区根目录

    返回:
        解析后的路径

    异常:
        ValueError: 如果路径逃逸工作区根目录
    """
    root = Path(workspace_root).expanduser().resolve()
    resolved = Path(path).expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"路径逃逸工作区根目录: {resolved}")
    return resolved


def validate_output_path(path: str | Path, workspace_root: str | Path, *, allow_absolute: bool = False) -> Path:
    """验证输出路径。

    参数:
        path: 路径
        workspace_root: 工作区根目录
        allow_absolute: 是否允许绝对路径

    返回:
        验证后的路径
    """
    output = Path(path).expanduser()
    if output.is_absolute() and allow_absolute:
        return output.resolve()
    return ensure_within_workspace(output if output.is_absolute() else Path(workspace_root) / output, workspace_root)
