"""Conservative CASTEP report and tagged-result parsers."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any


CASTEP_ENERGY_RE = re.compile(
    r"final\s+energy.*?(-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)",
    re.IGNORECASE,
)
CASTEP_GEOMETRY_RESULT_SCHEMA = (
    "material_studio_castep_geometry_optimization_result_v1"
)


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
