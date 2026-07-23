"""Materials Studio 20.1-verified DMol3 MaterialsScript rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .dmol3_contract import (
    DMOL3_GEOMETRY_API_OBJECT,
    DMOL3_GEOMETRY_RESULT_SCHEMA,
    DMOL3_MATERIALSCRIPT_CONTRACT,
    DMOL3_REVIEWED_RESULT_KEYS,
)
from .runner import perl_string
from .specs.dmol3 import DMol3GeometryOptimizationSpec


_PERL_SCALAR = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DMol3MaterialScriptPlan:
    """Resolved DMol3 task object and allowlisted Settings entries."""

    task: str
    api_object: str
    settings: tuple[tuple[str, str | int], ...]

    @property
    def run_method(self) -> str:
        return f"{self.api_object}->Run"

    def summary(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "api_object": self.api_object,
            "run_method": self.run_method,
            "settings": [
                {"name": name, "value": value} for name, value in self.settings
            ],
            "reviewed_result_keys": list(DMOL3_REVIEWED_RESULT_KEYS),
            "materials_studio_api_contract": DMOL3_MATERIALSCRIPT_CONTRACT,
            "execution_policy": "preview_only",
            "structured_execution_supported": True,
            "structured_execution_tool": "material_studio_dmol3_relax_current",
        }


def build_dmol3_materialscript_plan(
    spec: DMol3GeometryOptimizationSpec,
) -> DMol3MaterialScriptPlan:
    """Map a strict spec to the reviewed MS 20.1 DMol3 surface."""

    geometry_quality = spec.geometry_optimization_quality or spec.quality
    settings: tuple[tuple[str, str | int], ...] = (
        ("Quality", spec.quality.value),
        ("TheoryLevel", spec.theory_level.value),
        ("GeometryOptimizationQuality", geometry_quality.value),
        ("Charge", spec.charge),
        ("UseSymmetry", spec.use_symmetry.value),
        (
            "CreateEnergyEvolutionChart",
            spec.create_energy_evolution_chart.value,
        ),
    )
    return DMol3MaterialScriptPlan(
        task=spec.task,
        api_object=DMOL3_GEOMETRY_API_OBJECT,
        settings=settings,
    )


def render_dmol3_run_snippet(
    spec: DMol3GeometryOptimizationSpec,
    *,
    document_variable: str = "$doc",
    results_variable: str = "$dmol3_results",
) -> str:
    """Render one deterministic DMol3 GeometryOptimization Run call."""

    if not _PERL_SCALAR.fullmatch(document_variable):
        raise ValueError("document_variable must be a Perl scalar identifier")
    if not _PERL_SCALAR.fullmatch(results_variable):
        raise ValueError("results_variable must be a Perl scalar identifier")

    plan = build_dmol3_materialscript_plan(spec)
    settings = ",\n".join(
        f"    {name} => {_render_setting_value(value)}"
        for name, value in plan.settings
    )
    return (
        f"# {DMOL3_MATERIALSCRIPT_CONTRACT} DMol3 dispatch: "
        f"{plan.task} via {plan.run_method}\n"
        f"my {results_variable} = {plan.run_method}({document_variable}, Settings(\n"
        f"{settings}\n"
        "));"
    )


def _render_setting_value(value: str | int) -> str:
    if isinstance(value, str):
        return perl_string(value)
    if isinstance(value, bool):
        raise ValueError("Boolean DMol3 settings must use documented Yes/No strings")
    return str(value)
