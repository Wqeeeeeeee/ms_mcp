"""保守的晶体预览翻译器。

此模块提供了将晶体规格转换为预览用 MaterialsScript Perl 脚本的功能。
"""

from __future__ import annotations

from pathlib import Path

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.crystal import CrystalSpec

from .common import header, tagged_json_print


def render_crystal_preview(
    crystal: CrystalSpec,
    output_file: str | Path,
    *,
    project_id: str,
    revision: int,
) -> str:
    """渲染晶体预览脚本。

    参数:
        crystal: 晶体规格
        output_file: 输出文件路径
        project_id: 项目 ID
        revision: 修订版本号

    返回:
        生成的 Perl 脚本
    """
    payload = {
        "project_id": project_id,
        "revision": revision,
        "model_type": "crystal",
        "name": crystal.name,
        "output": str(output_file),
        "atom_count": len(crystal.basis_atoms),
        "warning": "晶体晶格构建仅用于预览，直到本地 Copy Script 输出确认 API 调用。",
    }
    return (
        header()
        + f"""# preview-only crystal script.
# 仅用于预览的晶体脚本。
# 晶格: a={crystal.lattice.a}, b={crystal.lattice.b}, c={crystal.lattice.c},
# alpha={crystal.lattice.alpha}, beta={crystal.lattice.beta}, gamma={crystal.lattice.gamma}
my $name = {perl_string(crystal.name)};
my $output = {perl_string(output_file)};
my $doc = Documents->New($name . ".xsd");

"""
        + "\n".join(
            f"# atom {atom.id} {atom.element} fractional=({atom.fractional.x}, {atom.fractional.y}, {atom.fractional.z})"
            for atom in crystal.basis_atoms
        )
        + "\n"
        + tagged_json_print(payload)
        + "die \"MaterialsScript 晶格构建仅用于预览；请使用 execute 模式生成 CIF 热加载，或用 Copy Script 确认晶格 API。\\n\";\n"
    )
