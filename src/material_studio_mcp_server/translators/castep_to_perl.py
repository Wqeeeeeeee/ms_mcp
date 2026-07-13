"""将 CASTEP 规格翻译为 MaterialsScript Perl 代码片段。

此模块提供了将 CASTEP 规格转换为 MaterialsScript Perl 代码片段的功能。
"""

from __future__ import annotations

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.castep import CastepEnergySpec


def render_castep_energy_snippet(spec: CastepEnergySpec) -> str:
    """渲染 CASTEP 能量代码片段。

    参数:
        spec: CASTEP 能量规格

    返回:
        Perl 代码片段
    """
    settings = [
        f"    Quality => {perl_string(spec.quality)}",
        f"    Task => {perl_string(spec.task)}",
        f"    XCFunctional => {perl_string(spec.functional)}",
    ]
    if spec.cutoff_energy_ev is not None:
        settings.append(f"    CutoffEnergy => {spec.cutoff_energy_ev}")
    if spec.kpoint_separation is not None:
        settings.append(f"    KPointSeparation => {spec.kpoint_separation:.10g}")
    if spec.kpoints is not None:
        settings.append(f"    KPoints => [{spec.kpoints[0]}, {spec.kpoints[1]}, {spec.kpoints[2]}]")
    joined = ",\n".join(settings)
    return f"""
my $castep_results = Modules->CASTEP->Energy->Run($doc, Settings(
{joined}
));
"""
