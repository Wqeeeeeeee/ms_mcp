"""Materials Studio 20.1-verified CASTEP MaterialsScript rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .runner import perl_string
from .specs.castep import (
    CASTEP_INITIAL_SPIN_API_PROPERTY,
    CASTEP_OPTIMIZE_TOTAL_SPIN_API_PROPERTY,
    CASTEP_SPIN_TREATMENT_API_PROPERTY,
    CASTEP_TOTAL_CHARGE_API_PROPERTY,
    CASTEP_USE_FORMAL_SPIN_API_PROPERTY,
    CASTEP_DIPOLE_CORRECTION_API_PROPERTY,
    CastepEnergySpec,
    CastepTask,
    normalize_castep_task,
)


CASTEP_MATERIALSCRIPT_CONTRACT = "Materials Studio 20.1"

_API_OBJECT_BY_TASK: dict[CastepTask, str] = {
    CastepTask.ENERGY: "Modules->CASTEP->Energy",
    CastepTask.GEOMETRY_OPTIMIZATION: "Modules->CASTEP->GeometryOptimization",
    CastepTask.BAND_STRUCTURE: "Modules->CASTEP->Energy",
    CastepTask.DENSITY_OF_STATES: "Modules->CASTEP->Energy",
    CastepTask.PROJECTED_DENSITY_OF_STATES: "Modules->CASTEP->Energy",
    CastepTask.OPTICS: "Modules->CASTEP->Energy",
    CastepTask.PHONON: "Modules->CASTEP->Energy",
    CastepTask.ELASTIC_CONSTANTS: "Modules->CASTEP->ElasticConstants",
}

_PROPERTY_SETTING_BY_TASK: dict[CastepTask, tuple[str, str]] = {
    CastepTask.BAND_STRUCTURE: ("CalculateBandStructure", "Dispersion"),
    CastepTask.DENSITY_OF_STATES: ("CalculateDOS", "Full"),
    CastepTask.PROJECTED_DENSITY_OF_STATES: ("CalculateDOS", "Partial"),
    CastepTask.OPTICS: ("CalculateOptics", "Full"),
    CastepTask.PHONON: ("CalculatePhononDispersion", "Dispersion"),
}

_STRUCTURED_EXECUTION_TOOL_BY_TASK: dict[CastepTask, str] = {
    CastepTask.ENERGY: "material_studio_castep_run_current",
    CastepTask.GEOMETRY_OPTIMIZATION: "material_studio_castep_relax_current",
    CastepTask.BAND_STRUCTURE: "material_studio_castep_run_current",
    CastepTask.DENSITY_OF_STATES: "material_studio_castep_run_current",
    CastepTask.PROJECTED_DENSITY_OF_STATES: "material_studio_castep_run_current",
}

_PERL_SCALAR = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CastepMaterialScriptPlan:
    """Resolved task object and documented Settings entries."""

    task: CastepTask
    api_object: str
    settings: tuple[tuple[str, str | int | float], ...]
    property_setting: tuple[str, str] | None

    @property
    def run_method(self) -> str:
        return f"{self.api_object}->Run"

    def summary(self) -> dict[str, Any]:
        return {
            "task": self.task.value,
            "api_object": self.api_object,
            "run_method": self.run_method,
            "settings": [
                {"name": name, "value": value} for name, value in self.settings
            ],
            "property_setting": (
                {"name": self.property_setting[0], "value": self.property_setting[1]}
                if self.property_setting is not None
                else None
            ),
            "materials_studio_api_contract": CASTEP_MATERIALSCRIPT_CONTRACT,
        }


def castep_structured_execution_tool(task: CastepTask | str) -> str | None:
    """Return the dedicated preview-first execution tool for one CASTEP task."""

    return _STRUCTURED_EXECUTION_TOOL_BY_TASK.get(normalize_castep_task(task))


def build_castep_materialscript_plan(spec: CastepEnergySpec) -> CastepMaterialScriptPlan:
    """Resolve one validated spec to documented MaterialsScript settings."""

    task = normalize_castep_task(spec.task)
    settings: list[tuple[str, str | int | float]] = [
        ("Quality", spec.quality),
        ("XCFunctional", spec.functional),
    ]
    if spec.total_charge is not None:
        settings.append((CASTEP_TOTAL_CHARGE_API_PROPERTY, spec.total_charge))
    if spec.spin_treatment is not None:
        settings.append(
            (CASTEP_SPIN_TREATMENT_API_PROPERTY, spec.spin_treatment.value)
        )
    if spec.use_formal_spin is not None:
        settings.append(
            (
                CASTEP_USE_FORMAL_SPIN_API_PROPERTY,
                "Yes" if spec.use_formal_spin else "No",
            )
        )
    if spec.initial_spin is not None:
        settings.append((CASTEP_INITIAL_SPIN_API_PROPERTY, spec.initial_spin))
    if spec.optimize_total_spin is not None:
        settings.append(
            (
                CASTEP_OPTIMIZE_TOTAL_SPIN_API_PROPERTY,
                "Yes" if spec.optimize_total_spin else "No",
            )
        )
    if spec.cutoff_energy_ev is not None:
        settings.extend(
            [
                ("UseCustomEnergyCutoff", "Yes"),
                ("EnergyCutoff", spec.cutoff_energy_ev),
            ]
        )
    if spec.dipole_correction is not None:
        settings.append(
            (CASTEP_DIPOLE_CORRECTION_API_PROPERTY, spec.dipole_correction.value)
        )
    if spec.max_iterations is not None:
        settings.append(("MaxIterations", spec.max_iterations))
    if spec.displacement_convergence_angstrom is not None:
        settings.append(
            ("DisplacementConvergence", spec.displacement_convergence_angstrom)
        )
    if spec.energy_convergence_ev_per_atom is not None:
        settings.append(
            ("EnergyConvergence", spec.energy_convergence_ev_per_atom)
        )
    if spec.force_convergence_ev_per_angstrom is not None:
        settings.append(
            ("ForceConvergence", spec.force_convergence_ev_per_angstrom)
        )
    if spec.cell_optimization is not None:
        settings.append(("CellOptimization", spec.cell_optimization.value))
    if spec.optimization_algorithm is not None:
        settings.append(("OptimizationAlgorithm", spec.optimization_algorithm.value))
    if spec.kpoint_separation is not None:
        settings.extend(
            [
                ("KPointDerivation", "Separation"),
                ("KPointSeparation", spec.kpoint_separation),
            ]
        )
    elif spec.kpoints is not None:
        settings.extend(
            [
                ("KPointDerivation", "CustomGrid"),
                ("ParameterA", spec.kpoints[0]),
                ("ParameterB", spec.kpoints[1]),
                ("ParameterC", spec.kpoints[2]),
            ]
        )
    if spec.properties_kpoint_separation is not None:
        settings.append(
            ("PropertiesKPointSeparation", spec.properties_kpoint_separation)
        )
    if spec.band_structure_energy_max_ev is not None:
        settings.append(("BandStructureEmax", spec.band_structure_energy_max_ev))
    if spec.band_structure_extra_bands is not None:
        settings.append(
            ("BandStructureNumExtraBands", spec.band_structure_extra_bands)
        )
    if spec.band_structure_energy_tolerance_ev is not None:
        settings.append(
            (
                "BandStructureEnergyTolerance",
                spec.band_structure_energy_tolerance_ev,
            )
        )
    if spec.dos_energy_max_ev is not None:
        settings.append(("DOSEmax", spec.dos_energy_max_ev))
    if spec.dos_extra_bands is not None:
        settings.append(("DOSNumExtraBands", spec.dos_extra_bands))
    if spec.dos_energy_tolerance_ev is not None:
        settings.append(("DOSEnergyTolerance", spec.dos_energy_tolerance_ev))
    if spec.dos_smearing_width_ev is not None:
        settings.append(("DOSSmearingWidth", spec.dos_smearing_width_ev))
    if spec.dos_integration_method is not None:
        settings.append(
            ("DOSPreferredIntegrationMethod", spec.dos_integration_method.value)
        )

    property_setting = _PROPERTY_SETTING_BY_TASK.get(task)
    if property_setting is not None:
        settings.append(property_setting)

    return CastepMaterialScriptPlan(
        task=task,
        api_object=_API_OBJECT_BY_TASK[task],
        settings=tuple(settings),
        property_setting=property_setting,
    )


def render_castep_run_snippet(
    spec: CastepEnergySpec,
    *,
    document_variable: str = "$doc",
    results_variable: str = "$castep_results",
) -> str:
    """Render one deterministic CASTEP Run call for Materials Studio 20.1."""

    if not _PERL_SCALAR.fullmatch(document_variable):
        raise ValueError("document_variable must be a Perl scalar identifier")
    if not _PERL_SCALAR.fullmatch(results_variable):
        raise ValueError("results_variable must be a Perl scalar identifier")

    plan = build_castep_materialscript_plan(spec)
    settings = ",\n".join(
        f"    {name} => {_render_setting_value(value)}" for name, value in plan.settings
    )
    return (
        f"# {CASTEP_MATERIALSCRIPT_CONTRACT} CASTEP dispatch: "
        f"{plan.task.value} via {plan.run_method}\n"
        f"my {results_variable} = {plan.run_method}({document_variable}, Settings(\n"
        f"{settings}\n"
        "));"
    )


def _render_setting_value(value: str | int | float) -> str:
    if isinstance(value, str):
        return perl_string(value)
    if isinstance(value, bool):
        raise ValueError("Boolean CASTEP settings must use documented string values")
    if isinstance(value, int):
        return str(value)
    return f"{value:.10g}"
