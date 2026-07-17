"""Translate CASTEP specs to MaterialsScript Perl snippets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_materialscript import (
    build_castep_materialscript_plan,
    render_castep_run_snippet,
)
from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.castep import CastepEnergySpec, CastepTask

from .common import header, tagged_json_print


def render_castep_energy_snippet(spec: CastepEnergySpec) -> str:
    """Render a compatibility-named, task-aware CASTEP snippet."""

    return "\n" + render_castep_run_snippet(spec) + "\n"


def render_castep_task_script(
    spec: CastepEnergySpec,
    input_file: str | Path,
    *,
    project_id: str,
    revision: int,
) -> str:
    """Render a standalone CASTEP task script for a materialized structure."""

    plan = build_castep_materialscript_plan(spec)
    payload = {
        "project_id": project_id,
        "revision": revision,
        "script_kind": "castep_task",
        "module": "CASTEP",
        "task": plan.task.value,
        "input_structure": str(input_file),
        "dispatch": plan.summary(),
    }
    return (
        header()
        + f"my $input = {perl_string(input_file)};\n"
        + "my $doc = Documents->Import($input);\n"
        + render_castep_run_snippet(spec)
        + "\n"
        + tagged_json_print(payload)
    )


def render_castep_geometry_optimization_script(
    spec: CastepEnergySpec,
    input_file: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
    *,
    project_id: str,
    base_revision: int,
) -> str:
    """Render the documented MS 20.1 geometry-optimization result workflow."""

    plan = build_castep_materialscript_plan(spec)
    if plan.task is not CastepTask.GEOMETRY_OPTIMIZATION:
        raise ValueError(
            "CASTEP relaxation script requires task GeometryOptimization"
        )
    static_payload = {
        "schema_version": "material_studio_castep_geometry_optimization_result_v1",
        "project_id": project_id,
        "base_revision": base_revision,
        "script_kind": "castep_geometry_optimization",
        "module": "CASTEP",
        "task": plan.task.value,
        "output_structure": str(output_structure),
        "output_report": str(output_report),
        "materials_studio_api_contract": "Materials Studio 20.1",
        "result_keys": [
            "Structure",
            "Report",
            "TotalEnergy",
            "Enthalpy",
            "Converged",
        ],
    }
    payload_prefix = json.dumps(
        static_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:-1]
    return (
        header()
        + f"my $input = {perl_string(input_file)};\n"
        + f"my $output_structure = {perl_string(output_structure)};\n"
        + f"my $output_report = {perl_string(output_report)};\n"
        + "my $doc = Documents->Import($input);\n"
        + render_castep_run_snippet(spec)
        + "\n"
        + "my $optimized_structure = $castep_results->Structure;\n"
        + "$optimized_structure->Export($output_structure);\n"
        + "my $castep_report = $castep_results->Report;\n"
        + "$castep_report->Export($output_report);\n"
        + "my $converged_json = $castep_results->Converged ? \"true\" : \"false\";\n"
        + "my $total_energy_json = defined($castep_results->TotalEnergy) "
        + "? (0 + $castep_results->TotalEnergy) : \"null\";\n"
        + "my $enthalpy_json = defined($castep_results->Enthalpy) "
        + "? (0 + $castep_results->Enthalpy) : \"null\";\n"
        + 'print "__MS_MCP_JSON_START__\\n";\n'
        + f"print {perl_string(payload_prefix)};\n"
        + 'print ",\\\"converged\\\":" . $converged_json;\n'
        + 'print ",\\\"total_energy_kcal_per_mol\\\":" . $total_energy_json;\n'
        + 'print ",\\\"enthalpy_kcal_per_mol\\\":" . $enthalpy_json . "}\\n";\n'
        + 'print "__MS_MCP_JSON_END__\\n";\n'
    )


def castep_calculation_preview_metadata(
    spec: CastepEnergySpec,
    input_file: str | Path,
) -> dict[str, Any]:
    """Return an explicit non-execution receipt for a crystal CASTEP preview."""

    plan = build_castep_materialscript_plan(spec)
    geometry_execution_supported = plan.task is CastepTask.GEOMETRY_OPTIMIZATION
    return {
        "available": True,
        "kind": "castep_task",
        "module": "CASTEP",
        "task": plan.task.value,
        "input_structure": str(input_file),
        "dispatch": plan.summary(),
        "execution_policy": (
            "explicit_execute_only"
            if geometry_execution_supported
            else "preview_only"
        ),
        "execution_supported_by_structured_workflow": geometry_execution_supported,
        "execution_tool": (
            "material_studio_castep_relax_current"
            if geometry_execution_supported
            else None
        ),
        "structure_materialization_executes_calculation": False,
        "requires_explicit_separate_execution": True,
        "calculation_executed": False,
        "calculation_result_available": False,
    }
