"""Pydantic 数据模型定义。

此模块定义了用于 Materials Studio MCP 服务器的数据模型，包括：
- CrystalSpec: 晶体结构规格
- RunSettings: 运行设置
- CuSiO2InterfaceSpec: Cu/SiO2 界面规格
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CrystalSpec(BaseModel):
    """晶体结构输入规格，用于 CIF 文件生成。

    属性:
        name: 结构名称，必须包含至少一个字母或数字
        lattice: 晶格参数字典，包含 a, b, c, alpha, beta, gamma
        atoms: 原子列表，每个原子包含元素和分数坐标
        space_group: 空间群符号（可选）
        output_format: 输出格式，默认为 "cif"
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    lattice: dict[str, float] = Field(description="a,b,c,alpha,beta,gamma")
    atoms: list[dict[str, Any]]
    space_group: str | None = None
    output_format: Literal["cif"] = "cif"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """验证名称不为空且包含至少一个字母或数字。"""
        if not value.strip():
            raise ValueError("名称不能为空")
        if not re.search(r"[A-Za-z0-9]", value):
            raise ValueError("名称必须包含至少一个字母或数字")
        return value

    @field_validator("lattice")
    @classmethod
    def validate_lattice(cls, value: dict[str, float]) -> dict[str, float]:
        """验证晶格参数完整且为正数。"""
        required = ("a", "b", "c", "alpha", "beta", "gamma")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"缺少晶格参数: {', '.join(missing)}")
        for key in required:
            if float(value[key]) <= 0:
                raise ValueError(f"晶格参数 {key} 必须为正数")
        return value

    @field_validator("atoms")
    @classmethod
    def validate_atoms(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """验证原子列表不为空。"""
        if not value:
            raise ValueError("原子列表不能为空")
        return value


class RunSettings(BaseModel):
    """MaterialsScript 几何优化执行设置。

    属性:
        engine: 计算引擎，可选 Forcite, CASTEP, DMol3, DFTB
        task: 任务类型，默认为 GeometryOptimization
        quality: 计算质量，默认为 Medium
        parameters: 额外参数字典
        cores: 并行核心数（可选）
    """

    model_config = ConfigDict(extra="forbid")

    engine: Literal["Forcite", "CASTEP", "DMol3", "DFTB"]
    task: str = "GeometryOptimization"
    quality: str | None = "Medium"
    parameters: dict[str, Any] = Field(default_factory=dict)
    cores: int | None = Field(default=None, ge=1)


class CuSiO2InterfaceSpec(BaseModel):
    """Cu/beta-cristobalite SiO2 晶格匹配界面默认设置。

    属性:
        name: 界面名称
        cu_lattice: 体心立方 Cu 晶格常数（埃）
        sio2_lattice: 理想 beta-cristobalite SiO2 立方晶格常数（埃）
        cu_supercell_x/y: Cu 超胞尺寸
        sio2_supercell_x/y: SiO2 超胞尺寸
        cu_layers: Cu(100) 原子层数
        interface_gap: 初始 Cu-SiO2 间距（埃）
        vacuum: SiO2 板上方的真空层厚度（埃）
        bottom_padding: Cu 板下方的填充厚度（埃）
        match_to: 匹配目标，可选 "sio2", "cu", "average"
    """

    model_config = ConfigDict(extra="forbid")

    name: str = "cu_sio2_interface"
    cu_lattice: float = Field(default=3.615, gt=0, description="体心立方 Cu 晶格常数（埃）")
    sio2_lattice: float = Field(
        default=7.160,
        gt=0,
        description="理想 beta-cristobalite SiO2 立方晶格常数（埃）",
    )
    cu_supercell_x: int = Field(default=2, ge=1)
    cu_supercell_y: int = Field(default=2, ge=1)
    sio2_supercell_x: int = Field(default=1, ge=1)
    sio2_supercell_y: int = Field(default=1, ge=1)
    cu_layers: int = Field(default=6, ge=2, description="Cu(100) 原子层数")
    interface_gap: float = Field(default=2.0, gt=0, description="初始 Cu-SiO2 间距（埃）")
    vacuum: float = Field(default=15.0, ge=0, description="SiO2 板上方的真空层厚度（埃）")
    bottom_padding: float = Field(default=1.0, ge=0, description="Cu 板下方的填充厚度（埃）")
    match_to: Literal["sio2", "cu", "average"] = "sio2"
