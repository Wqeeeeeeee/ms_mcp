"""结构化晶体规格。

此模块定义了用于晶体建模的数据模型。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from .common import FractionalVector3, StrictModel, validate_element_symbol


class LatticeSpec(StrictModel):
    """晶格规格。

    属性:
        a: 晶格参数 a（埃）
        b: 晶格参数 b（埃）
        c: 晶格参数 c（埃）
        alpha: 晶格角度 alpha（度）
        beta: 晶格角度 beta（度）
        gamma: 晶格角度 gamma（度）
    """

    a: float = Field(gt=0)
    b: float = Field(gt=0)
    c: float = Field(gt=0)
    alpha: float = Field(gt=0, lt=180)
    beta: float = Field(gt=0, lt=180)
    gamma: float = Field(gt=0, lt=180)


class BasisAtomSpec(StrictModel):
    """基原子规格。

    属性:
        id: 原子唯一标识符
        element: 元素符号
        fractional: 分数坐标
    """

    id: str = Field(min_length=1, max_length=50)
    element: str = Field(min_length=1, max_length=3)
    fractional: FractionalVector3

    @field_validator("element")
    @classmethod
    def known_element(cls, value: str) -> str:
        """验证元素符号。"""
        return validate_element_symbol(value)


class CrystalOperation(StrictModel):
    """晶体操作规格。

    属性:
        type: 操作类型
        parameters: 操作参数
    """

    type: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)


class CrystalSpec(StrictModel):
    """晶体规格。

    属性:
        name: 晶体名称
        lattice: 晶格规格
        basis_atoms: 基原子列表
        operations: 操作列表
    """

    name: str = Field(min_length=1, max_length=120)
    lattice: LatticeSpec
    basis_atoms: list[BasisAtomSpec] = Field(min_length=1)
    operations: list[CrystalOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_atom_ids(self) -> "CrystalSpec":
        """验证原子 ID 唯一性。"""
        atom_ids = [atom.id for atom in self.basis_atoms]
        duplicates = sorted({atom_id for atom_id in atom_ids if atom_ids.count(atom_id) > 1})
        if duplicates:
            raise ValueError(f"重复的原子 ID: {', '.join(duplicates)}")
        return self
