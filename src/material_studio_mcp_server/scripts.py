"""MCP 工具使用的 MaterialsScript Perl 模板。

此模块提供了用于生成 MaterialsScript Perl 脚本的模板和函数。
"""

from __future__ import annotations

from pathlib import Path

from .castep_materialscript import render_castep_run_snippet
from .runner import JSON_BEGIN, JSON_END, perl_string
from .specs.castep import CastepEnergySpec


# 脚本头部
SCRIPT_HEADER = """#!perl
use strict;
use warnings;
use Getopt::Long;
use MaterialsScript qw(:all);

"""


def validate_materialscript(script: str) -> dict[str, object]:
    """对 MaterialsScript Perl 内容执行轻量级验证。

    参数:
        script: Perl 脚本内容

    返回:
        验证结果字典，包含 valid、errors 和 warnings
    """
    warnings: list[str] = []
    errors: list[str] = []
    if "use MaterialsScript" not in script:
        errors.append("脚本未导入 MaterialsScript。请添加: use MaterialsScript qw(:all);")
    if "use strict" not in script:
        warnings.append("建议添加 'use strict;' 以实现更安全的 Perl 执行。")
    if "Documents->Import" not in script and "$Documents{" not in script and "Documents->New" not in script:
        warnings.append("未检测到文档导入/新建操作。")
    if "RunMatserver" in script or "RunMatScript" in script:
        warnings.append("脚本似乎包含 runner 命令；请仅将 Perl 代码传递给此 MCP 工具。")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def import_export_script(
    source_file: str | Path,
    output_file: str | Path,
    *,
    visual_output_file: str | Path | None = None,
) -> str:
    """创建导入一个文档并导出它的脚本。

    参数:
        source_file: 源文件路径
        output_file: 输出文件路径

    返回:
        生成的 Perl 脚本
    """
    visual_output = "" if visual_output_file is None else str(visual_output_file)
    visual_requested = 0 if visual_output_file is None else 1
    return (
        SCRIPT_HEADER
        + _json_escape_sub()
        + f"""my $source = {perl_string(source_file)};
my $output = {perl_string(output_file)};
my $visual_output = {perl_string(visual_output)};
my $doc = Documents->Import($source);
$doc->Export($output);

my $visual_requested = {visual_requested};
my $calculate_bonds_ok = 0;
my $visual_export_ok = 0;
my $visual_atom_count = -1;
my $calculated_bond_count = -1;
my $unit_cell_bond_count = -1;
my $calculate_error = "";
my $visual_export_error = "";

if ($visual_requested) {{
    {{
        local $@;
        my $ok = eval {{
            $visual_atom_count = $doc->UnitCell->Atoms->Count;
            my $calculated_bonds = $doc->CalculateBonds(Settings(
                MinBondLength => 0.60,
                MaxBondLength => 1.15
            ));
            $calculated_bond_count = $calculated_bonds->Count;
            $unit_cell_bond_count = $doc->UnitCell->Bonds->Count;
            1;
        }};
        if ($ok) {{
            $calculate_bonds_ok = 1;
        }} else {{
            $calculate_error = "$@";
        }}
    }}
    if ($calculate_bonds_ok) {{
        {{
            local $@;
            my $ok = eval {{
                $doc->Export($visual_output);
                1;
            }};
            if ($ok) {{
                $visual_export_ok = 1;
            }} else {{
                $visual_export_error = "$@";
            }}
        }}
    }}
}}

print "{JSON_BEGIN}\\n";
print "{{";
print "\\\"source\\\":\\\"" . json_escape($source) . "\\\",";
print "\\\"output\\\":\\\"" . json_escape($output) . "\\\",";
print "\\\"document_name\\\":\\\"" . json_escape($doc->Name) . "\\\",";
print "\\\"visual_bonded\\\":{{";
print "\\\"requested\\\":$visual_requested,";
print "\\\"output\\\":\\\"" . json_escape($visual_output) . "\\\",";
print "\\\"criteria\\\":{{\\\"min_bond_length\\\":0.60,\\\"max_bond_length\\\":1.15}},";
print "\\\"calculate_bonds_ok\\\":$calculate_bonds_ok,";
print "\\\"visual_export_ok\\\":$visual_export_ok,";
print "\\\"atom_count\\\":$visual_atom_count,";
print "\\\"calculated_bond_count\\\":$calculated_bond_count,";
print "\\\"unit_cell_bond_count\\\":$unit_cell_bond_count,";
print "\\\"calculate_error\\\":\\\"" . json_escape($calculate_error) . "\\\",";
print "\\\"export_error\\\":\\\"" . json_escape($visual_export_error) . "\\\"";
print "}}";
print "}}\\n";
print "{JSON_END}\\n";
"""
    )


def structure_summary_script(source_file: str | Path) -> str:
    """创建导入结构并报告基本计数的脚本。

    参数:
        source_file: 源文件路径

    返回:
        生成的 Perl 脚本
    """
    return (
        SCRIPT_HEADER
        + _json_escape_sub()
        + f"""my $source = {perl_string(source_file)};
my $doc = Documents->Import($source);
my $atom_count = 0;
my $bond_count = 0;
my $formula = "";

eval {{
    my $atoms = $doc->UnitCell->Atoms;
    foreach my $atom (@$atoms) {{ $atom_count++; }}
}};
if ($@ || $atom_count == 0) {{
    eval {{
        my $atoms = $doc->Atoms;
        foreach my $atom (@$atoms) {{ $atom_count++; }}
    }};
}}

eval {{
    my $bonds = $doc->Bonds;
    foreach my $bond (@$bonds) {{ $bond_count++; }}
}};

eval {{
    $formula = $doc->ChemicalFormula;
}};

print "{JSON_BEGIN}\\n";
print "{{";
print "\\\"source\\\":\\\"" . json_escape($source) . "\\\",";
print "\\\"document_name\\\":\\\"" . json_escape($doc->Name) . "\\\",";
print "\\\"atom_count\\\":$atom_count,";
print "\\\"bond_count\\\":$bond_count,";
print "\\\"formula\\\":\\\"" . json_escape($formula) . "\\\"";
print "}}\\n";
print "{JSON_END}\\n";
"""
    )


def forcite_geometry_optimization_script(
    input_file: str | Path,
    output_file: str | Path | None,
    *,
    forcefield: str,
    quality: str,
    charge_assignment: str,
    max_iterations: int,
    convergence: str,
) -> str:
    """创建 Forcite 几何优化脚本。

    参数:
        input_file: 输入文件路径
        output_file: 输出文件路径
        forcefield: 力场名称
        quality: 计算质量
        charge_assignment: 电荷分配模式
        max_iterations: 最大迭代次数
        convergence: 收敛级别

    返回:
        生成的 Perl 脚本
    """
    export_line = ""
    if output_file:
        export_line = f"$doc->Export({perl_string(output_file)});\n"

    return (
        SCRIPT_HEADER
        + _json_escape_sub()
        + f"""my $input = {perl_string(input_file)};
my $doc = Documents->Import($input);

Modules->Forcite->ChangeSettings([
    CurrentForcefield => {perl_string(forcefield)},
    Quality => {perl_string(quality)},
    AssignForcefieldTypes => "Yes",
    AssignChargeGroups => "Yes",
    ChargeAssignment => {perl_string(charge_assignment)},
    MaxIterations => {max_iterations},
    Convergence => {perl_string(convergence)}
]);

my $results = Modules->Forcite->GeometryOptimization->Run($doc);
{export_line}print "{JSON_BEGIN}\\n";
print "{{";
print "\\\"input\\\":\\\"" . json_escape($input) . "\\\",";
print "\\\"output\\\":\\\"" . json_escape({perl_string(output_file or "")}) . "\\\",";
print "\\\"document_name\\\":\\\"" . json_escape($doc->Name) . "\\\",";
print "\\\"forcefield\\\":\\\"" . json_escape({perl_string(forcefield)}) . "\\\",";
print "\\\"quality\\\":\\\"" . json_escape({perl_string(quality)}) . "\\\"";
print "}}\\n";
print "{JSON_END}\\n";
"""
    )


def build_molecule_script(
    name: str,
    output_file: str | Path,
    atoms: list[dict[str, object]],
    bonds: list[dict[str, str]],
    *,
    optimize: bool,
    forcefield: str | None,
    quality: str | None,
    max_iterations: int | None,
) -> str:
    """创建使用 MS API 构建分子的 MaterialsScript 脚本。

    参数:
        name: 分子名称
        output_file: 输出文件路径
        atoms: 原子列表
        bonds: 键列表
        optimize: 是否优化
        forcefield: 力场名称
        quality: 计算质量
        max_iterations: 最大迭代次数

    返回:
        生成的 Perl 脚本
    """
    atom_lines: list[str] = []
    for atom in atoms:
        atom_id = str(atom["id"])
        element = str(atom["element"])
        x = float(atom["x"])
        y = float(atom["y"])
        z = float(atom["z"])
        atom_lines.append(
            f"$atoms{{{perl_string(atom_id)}}} = $doc->CreateAtom({perl_string(element)}, "
            f"Point(X => {x:.8g}, Y => {y:.8g}, Z => {z:.8g}));"
        )

    bond_lines: list[str] = []
    for bond in bonds:
        atom1 = str(bond["atom1"])
        atom2 = str(bond["atom2"])
        bond_type = str(bond["type"])
        bond_lines.append(
            f"$doc->CreateBond($atoms{{{perl_string(atom1)}}}, $atoms{{{perl_string(atom2)}}}, {perl_string(bond_type)});"
        )

    optimize_block = "my $optimization_ok = 0;\nmy $optimization_error = \"\";\n"
    if optimize:
        settings_lines: list[str] = []
        if forcefield:
            settings_lines.append(f"    CurrentForcefield => {perl_string(forcefield)}")
        if quality:
            settings_lines.append(f"    Quality => {perl_string(quality)}")
        if max_iterations is not None:
            settings_lines.append(f"    MaxIterations => {max_iterations}")
        settings_block = ""
        if settings_lines:
            settings_block = "    Modules->Forcite->ChangeSettings([\n" + ",\n".join(settings_lines) + "\n    ]);\n"
        optimize_block += f"""eval {{
{settings_block}    Modules->Forcite->GeometryOptimization->Run($doc);
    $optimization_ok = 1;
}};
if ($@) {{
    $optimization_error = $@;
}}
"""

    atom_count = len(atoms)
    bond_count = len(bonds)
    return (
        SCRIPT_HEADER
        + _json_escape_sub()
        + f"""my $name = {perl_string(name)};
my $output = {perl_string(output_file)};
my $doc = Documents->New($name . ".xsd");
my %atoms;

"""
        + "\n".join(atom_lines)
        + "\n\n"
        + "\n".join(bond_lines)
        + "\n\n"
        + optimize_block
        + f"""
$doc->Export($output, Settings(Version => "2020"));

print "{JSON_BEGIN}\\n";
print "{{";
print "\\\"name\\\":\\\"" . json_escape($name) . "\\\",";
print "\\\"output\\\":\\\"" . json_escape($output) . "\\\",";
print "\\\"atom_count\\\":{atom_count},";
print "\\\"bond_count\\\":{bond_count},";
print "\\\"optimized\\\":" . ($optimization_ok ? "true" : "false") . ",";
print "\\\"optimization_error\\\":\\\"" . json_escape($optimization_error) . "\\\"";
print "}}\\n";
print "{JSON_END}\\n";
"""
    )


def castep_energy_script(
    input_file: str | Path,
    *,
    quality: str,
    task: str,
    functional: str,
    cutoff_energy_ev: int | None,
    kpoint_separation: float | None,
    kpoints: tuple[int, int, int] | None = None,
    total_charge: int | None = None,
    spin_treatment: str | None = None,
    use_formal_spin: bool | None = None,
    initial_spin: int | None = None,
    optimize_total_spin: bool | None = None,
    dipole_correction: str | None = None,
    max_iterations: int | None = None,
    displacement_convergence_angstrom: float | None = None,
    energy_convergence_ev_per_atom: float | None = None,
    force_convergence_ev_per_angstrom: float | None = None,
    cell_optimization: str | None = None,
    optimization_algorithm: str | None = None,
) -> str:
    """Create a task-aware CASTEP script using the MS 20.1 contract.

    参数:
        input_file: 输入文件路径
        quality: 质量设置
        task: 任务名称
        functional: 交换关联泛函
        cutoff_energy_ev: 截断能量（eV）
        kpoint_separation: k 点间距

    返回:
        生成的 Perl 脚本
    """
    spec = CastepEnergySpec(
        task=task,
        quality=quality,
        functional=functional,
        cutoff_energy_ev=cutoff_energy_ev,
        kpoint_separation=kpoint_separation,
        kpoints=kpoints,
        total_charge=total_charge,
        spin_treatment=spin_treatment,
        use_formal_spin=use_formal_spin,
        initial_spin=initial_spin,
        optimize_total_spin=optimize_total_spin,
        dipole_correction=dipole_correction,
        max_iterations=max_iterations,
        displacement_convergence_angstrom=displacement_convergence_angstrom,
        energy_convergence_ev_per_atom=energy_convergence_ev_per_atom,
        force_convergence_ev_per_angstrom=force_convergence_ev_per_angstrom,
        cell_optimization=cell_optimization,
        optimization_algorithm=optimization_algorithm,
    )
    run_snippet = render_castep_run_snippet(spec, results_variable="$results")

    return (
        SCRIPT_HEADER
        + f"""my $input = {perl_string(input_file)};
my $doc = Documents->Import($input);
{run_snippet}
print "CASTEP {spec.task.value} finished for " . $doc->Name . "\\n";
"""
    )


def template_catalog() -> list[dict[str, str]]:
    """返回内置的脚本模板。

    返回:
        模板列表
    """
    return [
        {
            "name": "import_export",
            "tool": "material_studio_import_export",
            "description": "导入结构文件并将其导出为另一种 Materials Studio 支持的格式。",
        },
        {
            "name": "structure_summary",
            "tool": "material_studio_structure_summary",
            "description": "导入结构并以标记 JSON 形式发出基本的原子/键/公式元数据。",
        },
        {
            "name": "forcite_geometry_optimization",
            "tool": "material_studio_forcite_geometry_optimization",
            "description": "使用力场、质量和收敛设置运行 Forcite GeometryOptimization。",
        },
        {
            "name": "build_molecule",
            "tool": "material_studio_build_molecule",
            "description": "使用 MaterialsScript CreateAtom/CreateBond 构建分子 XSD，而不是手写 XML。",
        },
        {
            "name": "build_tnt",
            "tool": "material_studio_build_tnt",
            "description": "使用内置的原子/键模板构建 2,4,6-三硝基甲苯。",
        },
        {
            "name": "castep_energy",
            "tool": "material_studio_castep_energy_script",
            "description": "Generate a task-aware CASTEP MaterialsScript preview for a licensed Materials Studio 20.1 installation.",
        },
    ]


def _json_escape_sub() -> str:
    """返回 JSON 转义的 Perl 子程序。"""
    return r"""sub json_escape {
    my ($value) = @_;
    $value = "" unless defined $value;
    $value =~ s/\\/\\\\/g;
    $value =~ s/"/\\"/g;
    $value =~ s/\r/\\r/g;
    $value =~ s/\n/\\n/g;
    $value =~ s/\t/\\t/g;
    return $value;
}

"""
