"""顶级项目规格模型。

此模块定义了用于结构化建模工作流的顶级数据模型。
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from .castep import CastepEnergySpec
from .common import AcceptanceCriteria, FileRef, ModelType, StrictModel, UnitSystem
from .crystal import CrystalSpec
from .dmol3 import DMol3GeometryOptimizationSpec
from .forcite import ForciteDynamicsSpec, ForciteOptimizationSpec
from .molecule import MoleculeSpec


# 模拟规格类型
SimulationSpec = Annotated[
    ForciteOptimizationSpec
    | ForciteDynamicsSpec
    | CastepEnergySpec
    | DMol3GeometryOptimizationSpec,
    Field(union_mode="left_to_right"),
]


class ImportedStructureSpec(StrictModel):
    """导入结构规格。

    属性:
        name: 结构名称
        source_file: 源文件引用
        format: 文件格式（可选）
    """

    name: str = Field(min_length=1, max_length=120)
    source_file: FileRef
    format: str | None = None

    @model_validator(mode="after")
    def require_digest_for_immutable_source(self) -> "ImportedStructureSpec":
        if (
            self.source_file.role == "immutable_cif_source"
            and self.source_file.sha256 is None
        ):
            raise ValueError(
                "immutable_cif_source requires an exact lowercase SHA-256 digest"
            )
        return self


class ModelSpec(StrictModel):
    """模型规格。

    属性:
        project_id: 项目 ID
        revision: 修订版本号
        software: 软件名称
        unit_system: 单位系统
        model_type: 模型类型
        model: 模型数据
        simulation: 模拟规格
        outputs: 输出配置
        acceptance: 验收标准
        metadata: 元数据
    """

    project_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=0, ge=0)
    software: str = "Materials Studio"
    unit_system: UnitSystem = Field(default_factory=UnitSystem)
    model_type: ModelType
    model: Annotated[MoleculeSpec | CrystalSpec | ImportedStructureSpec, Field(union_mode="left_to_right")]
    simulation: SimulationSpec | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    acceptance: AcceptanceCriteria = Field(default_factory=AcceptanceCriteria)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("project_id")
    @classmethod
    def safe_project_id(cls, value: str) -> str:
        """验证项目 ID。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("project_id 只能包含字母、数字、下划线和破折号")
        return value

    @model_validator(mode="after")
    def validate_model_type(self) -> "ModelSpec":
        """验证模型类型。"""
        expected = {
            ModelType.MOLECULE: MoleculeSpec,
            ModelType.CRYSTAL: CrystalSpec,
            ModelType.IMPORTED_STRUCTURE: ImportedStructureSpec,
        }[self.model_type]
        if not isinstance(self.model, expected):
            raise ValueError(f"model_type {self.model_type.value!r} 与模型载荷不匹配")
        if isinstance(self.simulation, DMol3GeometryOptimizationSpec):
            if not isinstance(self.model, MoleculeSpec):
                raise ValueError(
                    "DMol3 GeometryOptimization currently requires a molecule model"
                )
            if (
                self.model.total_charge is not None
                and self.simulation.charge != self.model.total_charge
            ):
                raise ValueError(
                    "DMol3 simulation charge must match molecule total_charge"
                )
            if self.model.spin_multiplicity not in {None, 1}:
                raise ValueError(
                    "DMol3 non-singlet molecules are not supported by the current strict settings contract"
                )
        return self
