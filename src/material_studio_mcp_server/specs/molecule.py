"""结构化分子规格。

此模块定义了用于分子建模的数据模型。
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from .common import StrictModel, Vector3, validate_element_symbol


class AtomSpec(StrictModel):
    """原子规格。

    属性:
        id: 原子唯一标识符
        element: 元素符号
        xyz_angstrom: 笛卡尔坐标（埃）
        charge: 电荷（可选）
    """

    id: str = Field(min_length=1, max_length=50)
    element: str = Field(min_length=1, max_length=3)
    xyz_angstrom: Vector3
    charge: float | None = None

    @field_validator("element")
    @classmethod
    def known_element(cls, value: str) -> str:
        """验证元素符号。"""
        return validate_element_symbol(value)


class BondSpec(StrictModel):
    """键规格。

    属性:
        atom1: 第一个原子 ID
        atom2: 第二个原子 ID
        type: 键类型
    """

    atom1: str = Field(min_length=1, max_length=50)
    atom2: str = Field(min_length=1, max_length=50)
    type: str = Field(default="Single", min_length=1, max_length=50)

    @field_validator("type")
    @classmethod
    def supported_type(cls, value: str) -> str:
        """验证键类型。"""
        allowed = {"Single", "Aromatic", "Partial double", "Double", "Triple"}
        if value not in allowed:
            raise ValueError(f"不支持的键类型: {value}")
        return value


class MoleculeSpec(StrictModel):
    """分子规格。

    属性:
        name: 分子名称
        atoms: 原子列表
        bonds: 键列表
        total_charge: 总电荷（可选）
        spin_multiplicity: 自旋多重度（可选）
    """

    name: str = Field(min_length=1, max_length=120)
    atoms: list[AtomSpec] = Field(min_length=1)
    bonds: list[BondSpec] = Field(default_factory=list)
    total_charge: int | None = None
    spin_multiplicity: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "MoleculeSpec":
        """验证分子图结构。"""
        atom_ids = [atom.id for atom in self.atoms]
        duplicates = sorted({atom_id for atom_id in atom_ids if atom_ids.count(atom_id) > 1})
        if duplicates:
            raise ValueError(f"重复的原子 ID: {', '.join(duplicates)}")
        known = set(atom_ids)
        missing = sorted(({bond.atom1 for bond in self.bonds} | {bond.atom2 for bond in self.bonds}) - known)
        if missing:
            raise ValueError(f"键引用了未知的原子 ID: {', '.join(missing)}")
        for bond in self.bonds:
            if bond.atom1 == bond.atom2:
                raise ValueError("键端点必须是不同的原子")
        return self
