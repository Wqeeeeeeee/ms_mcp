"""仅用于预览的 Materials Studio 2026 Python 脚手架。

此模块提供了生成 Python 预览脚本的功能。
"""

from __future__ import annotations

from material_studio_mcp_server.specs.molecule import MoleculeSpec


def render_molecule_python_preview(spec: MoleculeSpec) -> str:
    """渲染分子 Python 预览脚本。

    参数:
        spec: 分子规格

    返回:
        生成的 Python 脚本
    """
    lines = [
        "# 仅用于预览的 MaterialsScript Python 脚手架。",
        "# Perl RunMatScript 仍然是 Materials Studio 2020/20.1 的默认后端。",
        "from MaterialsScript import *",
        f"doc = Documents.New({spec.name!r} + '.xsd')",
    ]
    for atom in spec.atoms:
        lines.append(
            f"# CreateAtom {atom.id} {atom.element} at "
            f"({atom.xyz_angstrom.x}, {atom.xyz_angstrom.y}, {atom.xyz_angstrom.z})"
        )
    return "\n".join(lines) + "\n"
