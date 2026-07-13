"""MCP 工具使用的 MaterialsScript Perl 模板。

此模块提供了用于生成 MaterialsScript Perl 脚本的模板和函数。
"""

from __future__ import annotations

from pathlib import Path

from .runner import JSON_BEGIN, JSON_END, perl_string


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


def import_export_script(source_file: str | Path, output_file: str | Path) -> str:
    """创建导入一个文档并导出它的脚本。

    参数:
        source_file: 源文件路径
        output_file: 输出文件路径

    返回:
        生成的 Perl 脚本
    """
    return (
        SCRIPT_HEADER
        + _json_escape_sub()
        + f"""my $source = {perl_string(source_file)};
my $output = {perl_string(output_file)};
my $doc = Documents->Import($source);
$doc->Export($output);
print "{JSON_BEGIN}\\n";
print "{{";
print "\\\"source\\\":\\\"" . json_escape($source) . "\\\",";
print "\\\"output\\\":\\\"" . json_escape($output) . "\\\",";
print "\\\"document_name\\\":\\\"" . json_escape($doc->Name) . "\\\"";
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
) -> str:
    """创建 CASTEP Energy 脚本模板。

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
    settings = [
        f"    Quality => {perl_string(quality)}",
        f"    Task => {perl_string(task)}",
        f"    XCFunctional => {perl_string(functional)}",
    ]
    if cutoff_energy_ev is not None:
        settings.append(f"    CutoffEnergy => {cutoff_energy_ev}")
    if kpoint_separation is not None:
        settings.append(f"    KPointSeparation => {kpoint_separation}")
    joined_settings = ",\n".join(settings)

    return (
        SCRIPT_HEADER
        + f"""my $input = {perl_string(input_file)};
my $doc = Documents->Import($input);
my $results = Modules->CASTEP->Energy->Run($doc, Settings(
{joined_settings}
));
print "CASTEP Energy finished for " . $doc->Name . "\\n";
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
            "description": "为已获许可的 CASTEP 安装生成 CASTEP Energy MaterialsScript 模板。",
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
