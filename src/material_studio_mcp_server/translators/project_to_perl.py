"""顶级结构化规格到 Perl 的翻译。

此模块提供了将结构化模型规格转换为 MaterialsScript Perl 脚本的功能。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.castep import CastepEnergySpec
from material_studio_mcp_server.specs.crystal import CrystalSpec
from material_studio_mcp_server.specs.forcite import ForciteDynamicsSpec, ForciteOptimizationSpec
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.project import ImportedStructureSpec, ModelSpec

from .castep_to_perl import render_castep_energy_snippet
from .common import header, tagged_json_print
from .crystal_to_perl import render_crystal_preview
from .forcite_to_perl import render_forcite_dynamics_snippet, render_forcite_optimization_snippet
from .molecule_to_perl import render_molecule_build


@dataclass(frozen=True)
class GeneratedScript:
    """生成的脚本。

    属性:
        script: 脚本内容
        warnings: 警告列表
        planned_outputs: 计划的输出
        executable: 是否可执行
    """

    script: str
    warnings: list[str]
    planned_outputs: dict[str, str]
    executable: bool = True


def planned_output_file(spec: ModelSpec, output_dir: str | Path | None = None) -> Path:
    """获取计划的输出文件路径。

    参数:
        spec: 模型规格
        output_dir: 输出目录

    返回:
        输出文件路径
    """
    if isinstance(spec.outputs.get("output_file"), str):
        return Path(spec.outputs["output_file"])
    if spec.simulation is not None and getattr(spec.simulation, "output_file", None):
        return Path(str(getattr(spec.simulation, "output_file")))
    base = Path(output_dir) if output_dir else Path(".")
    if isinstance(spec.model, CrystalSpec):
        return base / f"structure_r{spec.revision:03d}.cif"
    return base / f"{spec.project_id}_r{spec.revision:03d}.xsd"


def render_model_to_perl(spec: ModelSpec, output_dir: str | Path | None = None) -> GeneratedScript:
    """将模型规格渲染为 Perl 脚本。

    参数:
        spec: 模型规格
        output_dir: 输出目录

    返回:
        GeneratedScript 实例
    """
    output_file = planned_output_file(spec, output_dir)
    warnings: list[str] = []
    executable = True

    if isinstance(spec.model, MoleculeSpec):
        script = render_molecule_build(spec.model, output_file, project_id=spec.project_id, revision=spec.revision)
        script = _insert_simulation_before_export(script, spec)
    elif isinstance(spec.model, CrystalSpec):
        script = render_crystal_preview(spec.model, output_file, project_id=spec.project_id, revision=spec.revision)
        warnings.append(
            "Crystal MaterialsScript lattice construction is preview-only until local Copy Script confirms the API; execute mode materializes a CIF for GUI hot-loading."
        )
        executable = False
    elif isinstance(spec.model, ImportedStructureSpec):
        script = _render_imported_structure(spec, output_file)
        script = _insert_simulation_before_export(script, spec)
    else:
        raise ValueError("不支持的模型规格")

    return GeneratedScript(
        script=script,
        warnings=warnings,
        planned_outputs={"structure": str(output_file)},
        executable=executable,
    )


def _insert_simulation_before_export(script: str, spec: ModelSpec) -> str:
    """在导出之前插入模拟代码。"""
    if spec.simulation is None:
        return script
    if isinstance(spec.simulation, ForciteOptimizationSpec):
        snippet = render_forcite_optimization_snippet(spec.simulation)
    elif isinstance(spec.simulation, ForciteDynamicsSpec):
        snippet = render_forcite_dynamics_snippet(spec.simulation)
    elif isinstance(spec.simulation, CastepEnergySpec):
        snippet = render_castep_energy_snippet(spec.simulation)
    else:
        return script
    marker = "$doc->Export($output"
    index = script.find(marker)
    if index < 0:
        return script + "\n" + snippet
    return script[:index] + snippet + "\n" + script[index:]


def _render_imported_structure(spec: ModelSpec, output_file: str | Path) -> str:
    """渲染导入结构脚本。"""
    model = spec.model
    assert isinstance(model, ImportedStructureSpec)
    payload = {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": "imported_structure",
        "name": model.name,
        "source": model.source_file.path,
        "output": str(output_file),
    }
    return (
        header()
        + f"""my $source = {perl_string(model.source_file.path)};
my $output = {perl_string(output_file)};
my $doc = Documents->Import($source);
$doc->Export($output, Settings(Version => "2020"));
"""
        + tagged_json_print(payload)
    )
