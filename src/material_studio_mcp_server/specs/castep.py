"""CASTEP 模拟规格。

此模块定义了用于 CASTEP 模拟的数据模型。
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .common import SimulationModule, StrictModel


class CastepEnergySpec(StrictModel):
    """CASTEP 能量规格。

    属性:
        module: 模拟模块
        task: 任务类型
        functional: 交换关联泛函
        quality: 计算质量
        cutoff_energy_ev: 截断能量（eV）
        kpoint_separation: k 点间距
        kpoints: k 点网格
        output_file: 输出文件路径
    """

    module: SimulationModule = SimulationModule.CASTEP
    task: str = "Energy"
    functional: str = Field(default="PBE", min_length=1, max_length=100)
    quality: str = Field(default="Medium", min_length=1, max_length=100)
    cutoff_energy_ev: int | None = Field(default=None, ge=1, le=100_000)
    kpoint_separation: float | None = Field(default=None, gt=0, le=10)
    kpoints: tuple[int, int, int] | None = None
    output_file: str | None = None

    @model_validator(mode="after")
    def validate_kpoints(self) -> "CastepEnergySpec":
        """验证 k 点设置。"""
        if self.kpoints is not None and any(value <= 0 for value in self.kpoints):
            raise ValueError("k 点必须是正整数")
        if self.kpoints is not None and self.kpoint_separation is not None:
            raise ValueError("使用 kpoints 或 kpoint_separation，不要同时使用")
        return self
