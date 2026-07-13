"""Forcite 模拟规格。

此模块定义了用于 Forcite 模拟的数据模型。
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .common import SimulationModule, StrictModel


class ForciteQuality(str, Enum):
    """Forcite 质量枚举。"""

    COARSE = "Coarse"
    MEDIUM = "Medium"
    FINE = "Fine"
    ULTRA_FINE = "Ultra-fine"


class ForciteConvergence(str, Enum):
    """Forcite 收敛枚举。"""

    COARSE = "Coarse"
    MEDIUM = "Medium"
    FINE = "Fine"
    ULTRA_FINE = "Ultra-fine"


class ForciteOptimizationSpec(StrictModel):
    """Forcite 优化规格。

    属性:
        module: 模拟模块
        task: 任务类型
        forcefield: 力场名称
        quality: 计算质量
        charge_assignment: 电荷分配模式
        max_iterations: 最大迭代次数
        convergence: 收敛级别
        output_file: 输出文件路径
    """

    module: SimulationModule = SimulationModule.FORCITE
    task: str = "GeometryOptimization"
    forcefield: str = Field(default="COMPASS", min_length=1, max_length=100)
    quality: ForciteQuality = ForciteQuality.MEDIUM
    charge_assignment: str = Field(default="Forcefield assigned", min_length=1, max_length=100)
    max_iterations: int = Field(default=500, ge=1, le=1_000_000)
    convergence: ForciteConvergence = ForciteConvergence.MEDIUM
    output_file: str | None = None


class DynamicsEnsemble(str, Enum):
    """动力学系综枚举。"""

    NVE = "NVE"
    NVT = "NVT"
    NPT = "NPT"


class ForciteDynamicsSpec(StrictModel):
    """Forcite 动力学规格。

    属性:
        module: 模拟模块
        task: 任务类型
        ensemble: 系综类型
        temperature_K: 温度（开尔文）
        pressure_GPa: 压力（吉帕斯卡）
        timestep_fs: 时间步长（飞秒）
        total_time_ps: 总时间（皮秒）
        trajectory_output_frequency: 轨迹输出频率
        forcefield: 力场名称
        quality: 计算质量
        charge_assignment: 电荷分配模式
        output_file: 输出文件路径
    """

    module: SimulationModule = SimulationModule.FORCITE
    task: str = "Dynamics"
    ensemble: DynamicsEnsemble
    temperature_K: float | None = Field(default=None, gt=0)
    pressure_GPa: float | None = None
    timestep_fs: float = Field(gt=0)
    total_time_ps: float = Field(gt=0)
    trajectory_output_frequency: int = Field(default=100, ge=1)
    forcefield: str = Field(default="COMPASS", min_length=1, max_length=100)
    quality: ForciteQuality = ForciteQuality.MEDIUM
    charge_assignment: str = Field(default="Forcefield assigned", min_length=1, max_length=100)
    output_file: str | None = None

    @model_validator(mode="after")
    def validate_ensemble(self) -> "ForciteDynamicsSpec":
        """验证系综设置。"""
        if self.ensemble in {DynamicsEnsemble.NVT, DynamicsEnsemble.NPT} and self.temperature_K is None:
            raise ValueError("NVT 和 NPT 动力学需要 temperature_K")
        return self
