"""将 Forcite 模拟规格翻译为 MaterialsScript Perl 代码片段。

此模块提供了将 Forcite 模拟规格转换为 MaterialsScript Perl 代码片段的功能。
"""

from __future__ import annotations

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.forcite import ForciteDynamicsSpec, ForciteOptimizationSpec


def render_forcite_optimization_snippet(spec: ForciteOptimizationSpec) -> str:
    """渲染 Forcite 优化代码片段。

    参数:
        spec: Forcite 优化规格

    返回:
        Perl 代码片段
    """
    return f"""
Modules->Forcite->ChangeSettings([
    CurrentForcefield => {perl_string(spec.forcefield)},
    Quality => {perl_string(spec.quality.value)},
    AssignForcefieldTypes => "Yes",
    AssignChargeGroups => "Yes",
    ChargeAssignment => {perl_string(spec.charge_assignment)},
    MaxIterations => {spec.max_iterations}
]);

my $forcite_results = Modules->Forcite->GeometryOptimization->Run($doc);
"""


def render_forcite_dynamics_snippet(spec: ForciteDynamicsSpec) -> str:
    """渲染 Forcite 动力学代码片段。

    参数:
        spec: Forcite 动力学规格

    返回:
        Perl 代码片段
    """
    temperature_line = ""
    if spec.temperature_K is not None:
        temperature_line = f"    Temperature => {spec.temperature_K:.10g},\n"
    pressure_line = ""
    if spec.pressure_GPa is not None:
        pressure_line = f"    Pressure => {spec.pressure_GPa:.10g},\n"
    return f"""
Modules->Forcite->ChangeSettings([
    CurrentForcefield => {perl_string(spec.forcefield)},
    Quality => {perl_string(spec.quality.value)},
    ChargeAssignment => {perl_string(spec.charge_assignment)},
    Ensemble3D => {perl_string(spec.ensemble.value)},
{temperature_line}{pressure_line}    TimeStep => {spec.timestep_fs:.10g},
    SimulationTime => {spec.total_time_ps:.10g},
    TrajectoryFrequency => {spec.trajectory_output_frequency}
]);

my $forcite_dynamics_results = Modules->Forcite->Dynamics->Run($doc);
"""
