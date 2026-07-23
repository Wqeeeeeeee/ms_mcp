"""模拟验证助手。

此模块提供了模拟相关的验证功能。
"""

from __future__ import annotations

from material_studio_mcp_server.specs.castep import CastepEnergySpec
from material_studio_mcp_server.specs.forcite import ForciteDynamicsSpec, ForciteOptimizationSpec


def validate_simulation(spec: ForciteOptimizationSpec | ForciteDynamicsSpec | CastepEnergySpec | None) -> list[str]:
    """验证模拟规格。

    参数:
        spec: 模拟规格

    返回:
        警告列表
    """
    warnings: list[str] = []
    if spec is None:
        return warnings
    if isinstance(spec, CastepEnergySpec):
        warnings.append("CASTEP 作业可能很昂贵；请在执行前预览。")
    if isinstance(spec, ForciteDynamicsSpec) and spec.total_time_ps > 1000:
        warnings.append("请求了长时间动力学运行；请在执行前确认运行时间。")
    return warnings
