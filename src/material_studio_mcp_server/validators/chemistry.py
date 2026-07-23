"""化学验证助手。

此模块提供了化学相关的验证功能。
"""

from __future__ import annotations

from material_studio_mcp_server.specs.common import validate_element_symbol
from material_studio_mcp_server.specs.molecule import BondSpec, MoleculeSpec


def validate_element(element: str) -> str:
    """验证元素符号。

    参数:
        element: 元素符号

    返回:
        验证后的元素符号
    """
    return validate_element_symbol(element)


def validate_molecule_graph(spec: MoleculeSpec) -> None:
    """验证分子图结构。

    参数:
        spec: 分子规格

    异常:
        ValueError: 如果键引用了未知的原子
    """
    atom_ids = {atom.id for atom in spec.atoms}
    for bond in spec.bonds:
        if bond.atom1 not in atom_ids or bond.atom2 not in atom_ids:
            raise ValueError(f"键引用了未知的原子: {bond.atom1}-{bond.atom2}")
    _ = [BondSpec.model_validate(bond.model_dump(mode="json")) for bond in spec.bonds]
