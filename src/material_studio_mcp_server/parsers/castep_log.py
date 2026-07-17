"""Conservative CASTEP report and tagged-result parsers."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, FiniteFloat, ValidationError, model_validator

from ..specs.common import StrictModel


CASTEP_ENERGY_RE = re.compile(
    r"final\s+energy.*?(-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)",
    re.IGNORECASE,
)
CASTEP_GEOMETRY_RESULT_SCHEMA = (
    "material_studio_castep_geometry_optimization_result_v1"
)
CASTEP_ELECTRONIC_RESULT_SCHEMA = "material_studio_castep_electronic_result_v1"
CASTEP_ELECTRONIC_TASKS = (
    "Energy",
    "BandStructure",
    "DensityOfStates",
    "ProjectedDensityOfStates",
)
CASTEP_ELECTRONIC_RESULT_DOCUMENT_BY_TASK: dict[str, str | None] = {
    "Energy": None,
    "BandStructure": "BandStructureChart",
    "DensityOfStates": "DOSChart",
    "ProjectedDensityOfStates": "PartialDOSChart",
}
_CASTEP_ELECTRONIC_RESULT_KEYS = (
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
)


class CastepElectronicResultDocuments(StrictModel):
    band_structure_chart: str | None = Field(
        default=None,
        alias="BandStructureChart",
        min_length=1,
        max_length=500,
    )
    dos_chart: str | None = Field(
        default=None,
        alias="DOSChart",
        min_length=1,
        max_length=500,
    )
    partial_dos_chart: str | None = Field(
        default=None,
        alias="PartialDOSChart",
        min_length=1,
        max_length=500,
    )


class CastepElectronicResultPayload(StrictModel):
    schema_version: Literal[CASTEP_ELECTRONIC_RESULT_SCHEMA]
    project_id: str = Field(min_length=1, max_length=120)
    base_revision: int = Field(ge=0)
    script_kind: Literal["castep_electronic_calculation"]
    module: Literal["CASTEP"]
    task: Literal[
        "Energy",
        "BandStructure",
        "DensityOfStates",
        "ProjectedDensityOfStates",
    ]
    input_structure: str = Field(min_length=1, max_length=4096)
    output_structure: str = Field(min_length=1, max_length=4096)
    output_report: str = Field(min_length=1, max_length=4096)
    materials_studio_api_contract: Literal["Materials Studio 20.1"]
    result_keys: list[str]
    required_result_document: str | None = Field(default=None, max_length=100)
    total_energy_kcal_per_mol: FiniteFloat | None
    free_energy_kcal_per_mol: FiniteFloat | None
    band_gap_ev: FiniteFloat | None
    fermi_level_ev: FiniteFloat | None
    work_function_ev: FiniteFloat | None
    work_function_top_ev: FiniteFloat | None
    work_function_bottom_ev: FiniteFloat | None
    result_document_names: CastepElectronicResultDocuments

    @model_validator(mode="after")
    def validate_task_contract(self) -> "CastepElectronicResultPayload":
        if tuple(self.result_keys) != _CASTEP_ELECTRONIC_RESULT_KEYS:
            raise ValueError("CASTEP electronic result_keys do not match the reviewed contract")
        expected_document = CASTEP_ELECTRONIC_RESULT_DOCUMENT_BY_TASK[self.task]
        if self.required_result_document != expected_document:
            raise ValueError(
                "CASTEP required_result_document does not match the requested task"
            )
        if self.total_energy_kcal_per_mol is None:
            raise ValueError("CASTEP electronic result requires TotalEnergy")
        document_names = self.result_document_names.model_dump(by_alias=True)
        if expected_document is not None and not document_names.get(expected_document):
            raise ValueError(
                f"CASTEP {self.task} result requires {expected_document}"
            )
        return self


def parse_castep_energy(text: str) -> dict[str, Any]:
    """Extract the last reported CASTEP final energy and completion marker."""

    matches = [float(match.group(1)) for match in CASTEP_ENERGY_RE.finditer(text)]
    lowered = text.lower()
    return {
        "energy": matches[-1] if matches else None,
        "finished": "finished" in lowered or "completed" in lowered,
    }


def validate_castep_geometry_result(
    payload: Any,
    *,
    project_id: str,
    base_revision: int,
    output_structure: str | Path,
    output_report: str | Path,
) -> dict[str, Any]:
    """Validate a tagged MS 20.1 geometry-optimization result fail-closed."""

    expected_structure = Path(output_structure).expanduser().resolve()
    expected_report = Path(output_report).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    normalized: dict[str, Any] = {}
    if not isinstance(payload, dict):
        errors.append("CASTEP runner output did not contain a tagged JSON object.")
    else:
        normalized = dict(payload)
        expected_values = {
            "schema_version": CASTEP_GEOMETRY_RESULT_SCHEMA,
            "project_id": project_id,
            "base_revision": base_revision,
            "module": "CASTEP",
            "task": "GeometryOptimization",
            "materials_studio_api_contract": "Materials Studio 20.1",
        }
        for key, expected in expected_values.items():
            if normalized.get(key) != expected:
                errors.append(
                    f"CASTEP result {key} mismatch: expected {expected!r}, "
                    f"found {normalized.get(key)!r}."
                )
        for key, expected_path in (
            ("output_structure", expected_structure),
            ("output_report", expected_report),
        ):
            raw_path = normalized.get(key)
            try:
                actual_path = Path(str(raw_path)).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                actual_path = None
            if actual_path != expected_path:
                errors.append(
                    f"CASTEP result {key} is not bound to the planned workspace artifact."
                )
        if not isinstance(normalized.get("converged"), bool):
            errors.append("CASTEP result converged must be a boolean.")
        for key in ("total_energy_kcal_per_mol", "enthalpy_kcal_per_mol"):
            value = normalized.get(key)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                errors.append(f"CASTEP result {key} must be a finite number or null.")

    structure_exists = expected_structure.is_file()
    report_exists = expected_report.is_file()
    if not structure_exists:
        errors.append(f"Optimized CASTEP structure was not found: {expected_structure}")
    if not report_exists:
        errors.append(f"CASTEP report was not found: {expected_report}")
    converged = normalized.get("converged") is True
    if normalized and normalized.get("converged") is False:
        warnings.append(
            "CASTEP completed but did not meet the documented geometry convergence criteria."
        )
    return {
        "schema_version": CASTEP_GEOMETRY_RESULT_SCHEMA,
        "ok": not errors,
        "converged": converged,
        "result": normalized or None,
        "output_structure": str(expected_structure),
        "output_structure_exists": structure_exists,
        "output_report": str(expected_report),
        "output_report_exists": report_exists,
        "errors": errors,
        "warnings": warnings,
    }


def validate_castep_electronic_result(
    payload: Any,
    *,
    project_id: str,
    base_revision: int,
    task: str,
    input_structure: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
) -> dict[str, Any]:
    """Validate a tagged CASTEP Energy/property result fail-closed."""

    expected_paths = {
        "input_structure": Path(input_structure).expanduser().resolve(),
        "output_structure": Path(output_structure).expanduser().resolve(),
        "output_report": Path(output_report).expanduser().resolve(),
    }
    errors: list[str] = []
    warnings: list[str] = []
    normalized: dict[str, Any] = {}
    try:
        parsed = CastepElectronicResultPayload.model_validate(payload)
        normalized = parsed.model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        errors.extend(
            f"CASTEP electronic result payload invalid: {item['loc']}: {item['msg']}"
            for item in exc.errors()
        )

    if normalized:
        expected_values = {
            "project_id": project_id,
            "base_revision": base_revision,
            "task": task,
        }
        for key, expected in expected_values.items():
            if normalized.get(key) != expected:
                errors.append(
                    f"CASTEP electronic result {key} mismatch: expected {expected!r}, "
                    f"found {normalized.get(key)!r}."
                )
        for key, expected_path in expected_paths.items():
            try:
                actual_path = Path(str(normalized.get(key))).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                actual_path = None
            if actual_path != expected_path:
                errors.append(
                    f"CASTEP electronic result {key} is not bound to the planned workspace artifact."
                )

    file_status = {
        key: path.is_file() for key, path in expected_paths.items()
    }
    if not file_status["input_structure"]:
        errors.append(
            f"CASTEP electronic input structure was not found: {expected_paths['input_structure']}"
        )
    if not file_status["output_structure"]:
        errors.append(
            f"CASTEP electronic result structure was not found: {expected_paths['output_structure']}"
        )
    if not file_status["output_report"]:
        errors.append(
            f"CASTEP electronic report was not found: {expected_paths['output_report']}"
        )
    if normalized:
        warnings.extend(
            [
                "Materials Studio Energy Results do not expose an SCF convergence boolean; backend completion is not an independent scientific convergence proof.",
                "Chart Documents are recorded by native object name only; numeric band/DOS curve data were not exported by the documented MS 20.1 API.",
            ]
        )
    return {
        "schema_version": CASTEP_ELECTRONIC_RESULT_SCHEMA,
        "ok": not errors,
        "backend_run_completed": bool(normalized and not errors),
        "scientific_convergence_verified": False,
        "numeric_curve_data_exported": False,
        "result": normalized or None,
        "input_structure": str(expected_paths["input_structure"]),
        "input_structure_exists": file_status["input_structure"],
        "output_structure": str(expected_paths["output_structure"]),
        "output_structure_exists": file_status["output_structure"],
        "output_report": str(expected_paths["output_report"]),
        "output_report_exists": file_status["output_report"],
        "errors": errors,
        "warnings": warnings,
    }
