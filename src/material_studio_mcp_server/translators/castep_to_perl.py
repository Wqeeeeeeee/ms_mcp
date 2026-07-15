"""Translate CASTEP specs to MaterialsScript Perl snippets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_materialscript import (
    build_castep_materialscript_plan,
    render_castep_run_snippet,
)
from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.castep import CastepEnergySpec

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


def castep_calculation_preview_metadata(
    spec: CastepEnergySpec,
    input_file: str | Path,
) -> dict[str, Any]:
    """Return an explicit non-execution receipt for a crystal CASTEP preview."""

    plan = build_castep_materialscript_plan(spec)
    return {
        "available": True,
        "kind": "castep_task",
        "module": "CASTEP",
        "task": plan.task.value,
        "input_structure": str(input_file),
        "dispatch": plan.summary(),
        "execution_policy": "preview_only",
        "execution_supported_by_structured_workflow": False,
        "structure_materialization_executes_calculation": False,
        "requires_explicit_separate_execution": True,
        "calculation_executed": False,
        "calculation_result_available": False,
    }
