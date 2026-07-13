"""将分子规格翻译为 MaterialsScript Perl。

此模块提供了将分子规格转换为 MaterialsScript Perl 脚本的功能。
"""

from __future__ import annotations

from pathlib import Path

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.molecule import MoleculeSpec

from .common import header, tagged_json_print


def render_molecule_build(
    molecule: MoleculeSpec,
    output_file: str | Path,
    *,
    project_id: str,
    revision: int,
) -> str:
    """渲染分子构建脚本。

    参数:
        molecule: 分子规格
        output_file: 输出文件路径
        project_id: 项目 ID
        revision: 修订版本号

    返回:
        生成的 Perl 脚本
    """
    # 生成原子创建行
    atom_lines: list[str] = []
    for atom in molecule.atoms:
        x, y, z = atom.xyz_angstrom.as_tuple()
        atom_lines.append(
            f"$atoms{{{perl_string(atom.id)}}} = $doc->CreateAtom({perl_string(atom.element)}, "
            f"Point(X => {x:.10g}, Y => {y:.10g}, Z => {z:.10g}));"
        )

    # 生成键创建行
    bond_lines: list[str] = []
    for bond in molecule.bonds:
        bond_lines.append(
            f"$doc->CreateBond($atoms{{{perl_string(bond.atom1)}}}, "
            f"$atoms{{{perl_string(bond.atom2)}}}, {perl_string(bond.type)});"
        )

    # 生成载荷
    payload = {
        "project_id": project_id,
        "revision": revision,
        "model_type": "molecule",
        "name": molecule.name,
        "output": str(output_file),
        "atom_count": len(molecule.atoms),
        "bond_count": len(molecule.bonds),
    }
    return (
        header()
        + f"""my $name = {perl_string(molecule.name)};
my $output = {perl_string(output_file)};
my $doc = Documents->New($name . ".xsd");
my %atoms;

"""
        + "\n".join(atom_lines)
        + "\n\n"
        + "\n".join(bond_lines)
        + "\n\n"
        + "$doc->Export($output, Settings(Version => \"2020\"));\n"
        + tagged_json_print(payload)
    )
