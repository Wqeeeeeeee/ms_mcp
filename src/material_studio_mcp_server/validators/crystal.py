"""晶体验证助手。

此模块提供了晶体相关的验证功能。
"""

from __future__ import annotations

from material_studio_mcp_server.specs.crystal import CrystalSpec


def validate_crystal(spec: CrystalSpec) -> None:
    """验证晶体规格。

    参数:
        spec: 晶体规格

    异常:
        ValueError: 如果存在重复的原子 ID
    """
    seen: set[str] = set()
    for atom in spec.basis_atoms:
        if atom.id in seen:
            raise ValueError(f"重复的原子 ID: {atom.id}")
        seen.add(atom.id)
