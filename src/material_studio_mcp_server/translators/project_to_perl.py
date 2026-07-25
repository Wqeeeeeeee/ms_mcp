"""顶级结构化规格到 Perl 的翻译。

此模块提供了将结构化模型规格转换为 MaterialsScript Perl 脚本的功能。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.castep import CastepEnergySpec
from material_studio_mcp_server.specs.crystal import CrystalSpec
from material_studio_mcp_server.specs.dmol3 import DMol3GeometryOptimizationSpec
from material_studio_mcp_server.specs.forcite import ForciteDynamicsSpec, ForciteOptimizationSpec
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.project import ImportedStructureSpec, ModelSpec

from .castep_to_perl import (
    castep_calculation_preview_metadata,
    render_castep_energy_snippet,
    render_castep_task_script,
)
from .common import header, tagged_json_print
from .crystal_to_perl import render_crystal_preview
from .dmol3_to_perl import (
    dmol3_calculation_preview_metadata,
    render_dmol3_task_script,
)
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
    calculation_preview_script: str | None = None
    calculation_preview: dict[str, object] | None = None


def planned_output_file(spec: ModelSpec, output_dir: str | Path | None = None) -> Path:
    """获取计划的输出文件路径。

    参数:
        spec: 模型规格
        output_dir: 输出目录

    返回:
        输出文件路径
    """
    output_override: str | None = None
    if isinstance(spec.outputs.get("output_file"), str):
        output_override = spec.outputs["output_file"]
    elif spec.simulation is not None and getattr(spec.simulation, "output_file", None):
        output_override = str(getattr(spec.simulation, "output_file"))

    if output_override is not None:
        if output_dir is None:
            return Path(output_override)
        posix_override = PurePosixPath(output_override)
        windows_override = PureWindowsPath(output_override)
        if (
            not output_override.strip()
            or ":" in output_override
            or "/" in output_override
            or "\\" in output_override
            or posix_override.is_absolute()
            or windows_override.is_absolute()
            or windows_override.drive
            or len(posix_override.parts) != 1
            or len(windows_override.parts) != 1
            or output_override in {".", ".."}
        ):
            raise ValueError(
                "output_file must be a relative file name when output_dir is supplied"
            )
        return Path(output_dir) / output_override

    base = Path(output_dir) if output_dir else Path(".")
    if isinstance(spec.model, CrystalSpec):
        return base / f"structure_r{spec.revision:03d}.cif"
    if isinstance(spec.model, ImportedStructureSpec):
        return base / f"structure_r{spec.revision:03d}.xsd"
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
    calculation_preview_script: str | None = None
    calculation_preview: dict[str, object] | None = None

    if isinstance(spec.model, MoleculeSpec):
        script = render_molecule_build(spec.model, output_file, project_id=spec.project_id, revision=spec.revision)
        script = _insert_simulation_before_export(script, spec)
    elif isinstance(spec.model, CrystalSpec):
        script = render_crystal_preview(spec.model, output_file, project_id=spec.project_id, revision=spec.revision)
        warnings.append(
            "Crystal MaterialsScript lattice construction is preview-only until local Copy Script confirms the API; execute mode materializes a CIF for GUI hot-loading."
        )
        executable = False
        if isinstance(spec.simulation, CastepEnergySpec):
            calculation_preview_script = render_castep_task_script(
                spec.simulation,
                output_file,
                project_id=spec.project_id,
                revision=spec.revision,
            )
            calculation_preview = castep_calculation_preview_metadata(
                spec.simulation,
                output_file,
                project_id=spec.project_id,
                revision=spec.revision,
            )
    elif isinstance(spec.model, ImportedStructureSpec):
        script = _render_imported_structure(spec, output_file)
        script = _insert_simulation_before_export(script, spec)
    else:
        raise ValueError("不支持的模型规格")

    if isinstance(spec.simulation, DMol3GeometryOptimizationSpec):
        calculation_preview_script = render_dmol3_task_script(
            spec.simulation,
            output_file,
            project_id=spec.project_id,
            revision=spec.revision,
        )
        calculation_preview = dmol3_calculation_preview_metadata(
            spec.simulation,
            output_file,
            project_id=spec.project_id,
            revision=spec.revision,
        )

    return GeneratedScript(
        script=script,
        warnings=warnings,
        planned_outputs={"structure": str(output_file)},
        executable=executable,
        calculation_preview_script=calculation_preview_script,
        calculation_preview=calculation_preview,
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
    verified_import_source: Path | None = None
    if (
        model.source_file.sha256 is not None
        and model.source_file.role == "immutable_cif_source"
    ):
        verified_import_source = Path(output_file).parent / "in.cif"
    payload = {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": "imported_structure",
        "name": model.name,
        "source": model.source_file.path,
        "source_sha256_expected": model.source_file.sha256,
        "verified_import_source": (
            str(verified_import_source)
            if verified_import_source is not None
            else None
        ),
        "output": str(output_file),
    }
    integrity_check = ""
    if model.source_file.sha256 is not None:
        integrity_check = f"""my $expected_source_sha256 = {perl_string(model.source_file.sha256)};
open(my $source_fh, "<", $source)
    or die "Unable to open imported structure source for SHA-256 verification: $!";
binmode($source_fh);
my $source_digest = Digest::SHA->new(256);
$source_digest->addfile($source_fh);
close($source_fh)
    or die "Unable to close imported structure source after SHA-256 verification: $!";
my $observed_source_sha256 = $source_digest->hexdigest;
die "Imported structure source SHA-256 mismatch"
    unless $observed_source_sha256 eq $expected_source_sha256;
"""
    import_source_setup = "my $import_source = $source;\n"
    if verified_import_source is not None:
        import_source_setup = f"""my $verified_import_source = {perl_string(verified_import_source)};
my $source_bytes = "";
open(my $copy_source_fh, "<", $source)
    or die "Unable to open imported structure source for verified staging: $!";
binmode($copy_source_fh);
while (1) {{
    my $chunk = "";
    my $read_count = read($copy_source_fh, $chunk, 1048576);
    die "Unable to read imported structure source for verified staging: $!"
        unless defined($read_count);
    last if $read_count == 0;
    $source_bytes .= $chunk;
    die "Imported structure source exceeds the 64 MiB staging limit"
        if length($source_bytes) > 67108864;
}}
close($copy_source_fh)
    or die "Unable to close imported structure source after staging: $!";
if (-e $verified_import_source) {{
    die "Verified import staging path is a symbolic link"
        if -l $verified_import_source;
    open(my $existing_stage_fh, "<", $verified_import_source)
        or die "Unable to open existing verified import source: $!";
    binmode($existing_stage_fh);
    my $existing_stage_digest = Digest::SHA->new(256);
    $existing_stage_digest->addfile($existing_stage_fh);
    close($existing_stage_fh)
        or die "Unable to close existing verified import source: $!";
    die "Existing verified import source SHA-256 mismatch"
        unless $existing_stage_digest->hexdigest eq $expected_source_sha256;
}} else {{
    sysopen(
        my $stage_fh,
        $verified_import_source,
        O_WRONLY | O_CREAT | O_EXCL,
    ) or die "Unable to publish verified import source exclusively: $!";
    binmode($stage_fh);
    print {{$stage_fh}} $source_bytes
        or die "Unable to write verified import source: $!";
    close($stage_fh)
        or die "Unable to close verified import source: $!";
}}
open(my $stage_verify_fh, "<", $verified_import_source)
    or die "Unable to reopen verified import source: $!";
binmode($stage_verify_fh);
my $stage_verify_digest = Digest::SHA->new(256);
$stage_verify_digest->addfile($stage_verify_fh);
close($stage_verify_fh)
    or die "Unable to close verified import source after verification: $!";
die "Verified import source SHA-256 mismatch"
    unless $stage_verify_digest->hexdigest eq $expected_source_sha256;
chmod 0444, $verified_import_source;
my $import_source = $verified_import_source;
"""
    return (
        header()
        + (
            "use Digest::SHA;\n"
            + (
                "use Fcntl qw(O_WRONLY O_CREAT O_EXCL);\n"
                if verified_import_source is not None
                else ""
            )
            + "\n"
            if model.source_file.sha256 is not None
            else ""
        )
        + f"""my $source = {perl_string(model.source_file.path)};
my $output = {perl_string(output_file)};
{integrity_check}
{import_source_setup}
my $doc = Documents->Import($import_source);
$doc->Export($output, Settings(Version => "2020"));
"""
        + tagged_json_print(payload)
    )
