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


_ELECTRONIC_RESULT_DOCUMENT_BY_TASK: dict[CastepTask, str | None] = {
    CastepTask.ENERGY: None,
    CastepTask.BAND_STRUCTURE: "BandStructureChart",
    CastepTask.DENSITY_OF_STATES: "DOSChart",
    CastepTask.PROJECTED_DENSITY_OF_STATES: "PartialDOSChart",
}


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


def render_castep_electronic_script(
    spec: CastepEnergySpec,
    input_file: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
    *,
    project_id: str,
    base_revision: int,
) -> str:
    """Render a documented CASTEP Energy/property result workflow."""

    plan = build_castep_materialscript_plan(spec)
    if plan.task not in _ELECTRONIC_RESULT_DOCUMENT_BY_TASK:
        supported = ", ".join(task.value for task in _ELECTRONIC_RESULT_DOCUMENT_BY_TASK)
        raise ValueError(
            f"CASTEP electronic execution supports {supported}; got {plan.task.value}"
        )
    required_document = _ELECTRONIC_RESULT_DOCUMENT_BY_TASK[plan.task]
    static_payload = {
        "schema_version": "material_studio_castep_electronic_result_v1",
        "project_id": project_id,
        "base_revision": base_revision,
        "script_kind": "castep_electronic_calculation",
        "module": "CASTEP",
        "task": plan.task.value,
        "input_structure": str(input_file),
        "output_structure": str(output_structure),
        "output_report": str(output_report),
        "materials_studio_api_contract": "Materials Studio 20.1",
        "result_keys": [
            "Structure",
            "Report",
            "TotalEnergy",
            "FreeEnergy",
            "BandGap",
            "FermiLevel",
            "WorkFunction",
            "WorkFunctionTop",
            "WorkFunctionBottom",
            "BandStructureChart",
            "DOSChart",
            "PartialDOSChart",
        ],
        "required_result_document": required_document,
    }
    payload_prefix = json.dumps(
        static_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:-1]
    return (
        header()
        + _electronic_result_helpers()
        + f"my $input = {perl_string(input_file)};\n"
        + f"my $output_structure = {perl_string(output_structure)};\n"
        + f"my $output_report = {perl_string(output_report)};\n"
        + "my $doc = Documents->Import($input);\n"
        + render_castep_run_snippet(spec)
        + "\n"
        + "my $result_structure = $castep_results->Structure;\n"
        + "$result_structure->Export($output_structure);\n"
        + "my $castep_report = $castep_results->Report;\n"
        + "$castep_report->Export($output_report);\n"
        + 'my $total_energy_json = optional_numeric_result_json($castep_results, "TotalEnergy");\n'
        + 'my $free_energy_json = optional_numeric_result_json($castep_results, "FreeEnergy");\n'
        + 'my $band_gap_json = optional_numeric_result_json($castep_results, "BandGap");\n'
        + 'my $fermi_level_json = optional_numeric_result_json($castep_results, "FermiLevel");\n'
        + 'my $work_function_json = optional_numeric_result_json($castep_results, "WorkFunction");\n'
        + 'my $work_function_top_json = optional_numeric_result_json($castep_results, "WorkFunctionTop");\n'
        + 'my $work_function_bottom_json = optional_numeric_result_json($castep_results, "WorkFunctionBottom");\n'
        + 'my $band_chart_json = optional_document_name_json($castep_results, "BandStructureChart");\n'
        + 'my $dos_chart_json = optional_document_name_json($castep_results, "DOSChart");\n'
        + 'my $partial_dos_chart_json = optional_document_name_json($castep_results, "PartialDOSChart");\n'
        + 'print "__MS_MCP_JSON_START__\\n";\n'
        + f"print {perl_string(payload_prefix)};\n"
        + 'print ",\\\"total_energy_kcal_per_mol\\\":" . $total_energy_json;\n'
        + 'print ",\\\"free_energy_kcal_per_mol\\\":" . $free_energy_json;\n'
        + 'print ",\\\"band_gap_ev\\\":" . $band_gap_json;\n'
        + 'print ",\\\"fermi_level_ev\\\":" . $fermi_level_json;\n'
        + 'print ",\\\"work_function_ev\\\":" . $work_function_json;\n'
        + 'print ",\\\"work_function_top_ev\\\":" . $work_function_top_json;\n'
        + 'print ",\\\"work_function_bottom_ev\\\":" . $work_function_bottom_json;\n'
        + 'print ",\\\"result_document_names\\\":{";\n'
        + 'print "\\\"BandStructureChart\\\":" . $band_chart_json . ",";\n'
        + 'print "\\\"DOSChart\\\":" . $dos_chart_json . ",";\n'
        + 'print "\\\"PartialDOSChart\\\":" . $partial_dos_chart_json . "}}\\n";\n'
        + 'print "__MS_MCP_JSON_END__\\n";\n'
    )


def _electronic_result_helpers() -> str:
    return r'''sub json_escape {
    my ($value) = @_;
    $value = "" unless defined $value;
    $value =~ s/\\/\\\\/g;
    $value =~ s/"/\\"/g;
    $value =~ s/\r/\\r/g;
    $value =~ s/\n/\\n/g;
    $value =~ s/\t/\\t/g;
    return $value;
}

sub optional_numeric_result_json {
    my ($results, $key) = @_;
    my $value;
    my $available = eval { $value = $results->$key; 1; };
    return "null" unless $available && defined $value;
    return 0 + $value;
}

sub optional_document_name_json {
    my ($results, $key) = @_;
    my $document;
    my $available = eval { $document = $results->$key; 1; };
    return "null" unless $available && defined $document;
    my $name;
    my $named = eval { $name = $document->Name; 1; };
    return "null" unless $named && defined $name && length($name);
    return '"' . json_escape($name) . '"';
}

'''


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
