"""Trusted CASTEP electronic-result revision receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .castep_relaxation import crystal_structure_sha256
from .parsers.cif import validate_crystal_cif_against_spec
from .specs.castep import CastepEnergySpec, CastepTask
from .specs.crystal import CrystalSpec
from .specs.project import ModelSpec


CASTEP_ELECTRONIC_RECEIPT_SCHEMA = "material_studio_castep_electronic_receipt_v1"
SUPPORTED_CASTEP_ELECTRONIC_TASKS = frozenset(
    {
        CastepTask.ENERGY,
        CastepTask.BAND_STRUCTURE,
        CastepTask.DENSITY_OF_STATES,
        CastepTask.PROJECTED_DENSITY_OF_STATES,
    }
)
RESULT_DOCUMENT_BY_TASK: dict[CastepTask, str | None] = {
    CastepTask.ENERGY: None,
    CastepTask.BAND_STRUCTURE: "BandStructureChart",
    CastepTask.DENSITY_OF_STATES: "DOSChart",
    CastepTask.PROJECTED_DENSITY_OF_STATES: "PartialDOSChart",
}


def build_electronic_result_revision_spec(
    base_spec: ModelSpec,
    *,
    simulation: CastepEnergySpec,
    result_payload: dict[str, Any],
    output_structure: str | Path,
    output_report: str | Path,
    result_metadata: str | Path,
    result_payload_path: str | Path,
    script_path: str | Path,
    script_sha256: str,
    native_artifacts: list[dict[str, Any]],
    target_revision: int,
) -> tuple[ModelSpec, dict[str, Any]]:
    """Create a metadata-only revision for one verified Energy task result."""

    if not isinstance(base_spec.model, CrystalSpec):
        raise ValueError("CASTEP electronic result recording requires a crystal ModelSpec")
    if simulation.task not in SUPPORTED_CASTEP_ELECTRONIC_TASKS:
        raise ValueError(
            f"Unsupported CASTEP electronic result task: {simulation.task.value}"
        )
    if result_payload.get("task") != simulation.task.value:
        raise ValueError("CASTEP electronic result task differs from the effective simulation")
    if result_payload.get("project_id") != base_spec.project_id:
        raise ValueError("CASTEP electronic result project identity mismatch")
    if result_payload.get("base_revision") != base_spec.revision:
        raise ValueError("CASTEP electronic result source revision mismatch")

    structure_path = Path(output_structure).expanduser().resolve()
    report_path = Path(output_report).expanduser().resolve()
    metadata_path = Path(result_metadata).expanduser().resolve()
    payload_path = Path(result_payload_path).expanduser().resolve()
    persisted_script_path = Path(script_path).expanduser().resolve()
    artifact_validation = validate_crystal_cif_against_spec(
        base_spec.model,
        structure_path,
    )
    if not artifact_validation.get("ok"):
        raise ValueError(
            "CASTEP electronic result changed or corrupted the source structure: "
            + "; ".join(
                str(item) for item in artifact_validation.get("errors", []) or []
            )
        )
    if not report_path.is_file():
        raise ValueError(f"CASTEP electronic report was not found: {report_path}")
    if not persisted_script_path.is_file():
        raise ValueError(
            f"CASTEP electronic execution script was not found: {persisted_script_path}"
        )
    if _file_sha256(persisted_script_path) != script_sha256:
        raise ValueError("CASTEP electronic execution script hash mismatch")
    if not payload_path.is_file():
        raise ValueError(
            f"CASTEP electronic tagged result was not found: {payload_path}"
        )
    try:
        persisted_payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("CASTEP electronic tagged result is not valid JSON") from exc
    if _canonical_json_sha256(persisted_payload) != _canonical_json_sha256(
        result_payload
    ):
        raise ValueError("CASTEP electronic tagged result payload mismatch")

    expected_document = RESULT_DOCUMENT_BY_TASK[simulation.task]
    document_names = dict(result_payload.get("result_document_names") or {})
    chart_name = document_names.get(expected_document) if expected_document else None
    if expected_document is not None and not chart_name:
        raise ValueError(
            f"CASTEP {simulation.task.value} did not return {expected_document}"
        )
    normalized_native_artifacts = _validate_native_artifact_manifest(native_artifacts)
    structure_hash = crystal_structure_sha256(base_spec.model)
    simulation_payload = simulation.model_dump(mode="json")
    receipt = {
        "schema_version": CASTEP_ELECTRONIC_RECEIPT_SCHEMA,
        "backend": "Materials Studio 20.1 CASTEP Energy",
        "materials_studio_api_contract": "Materials Studio 20.1",
        "api": "Modules->CASTEP->Energy->Run",
        "source_project_id": base_spec.project_id,
        "source_revision": base_spec.revision,
        "target_revision": target_revision,
        "task": simulation.task.value,
        "source_model_spec_sha256": _canonical_json_sha256(
            base_spec.model_dump(mode="json")
        ),
        "simulation": simulation_payload,
        "simulation_sha256": _canonical_json_sha256(simulation_payload),
        "source_structure_sha256": structure_hash,
        "target_structure_sha256": structure_hash,
        "structure_unchanged": True,
        "structure_artifact_validation_status": artifact_validation.get("status"),
        "structure_artifact_validation_ok": artifact_validation.get("ok"),
        "backend_run_completed": True,
        "calculation_result_verified": True,
        "scientific_convergence_verified": False,
        "scientific_convergence_claimed": False,
        "required_result_document": expected_document,
        "result_document_name": chart_name,
        "result_document_available": bool(chart_name),
        "numeric_curve_data_exported": False,
        "band_path_binding_verified": False,
        "total_energy_kcal_per_mol": result_payload.get(
            "total_energy_kcal_per_mol"
        ),
        "free_energy_kcal_per_mol": result_payload.get(
            "free_energy_kcal_per_mol"
        ),
        "band_gap_ev": result_payload.get("band_gap_ev"),
        "fermi_level_ev": result_payload.get("fermi_level_ev"),
        "work_function_ev": result_payload.get("work_function_ev"),
        "work_function_top_ev": result_payload.get("work_function_top_ev"),
        "work_function_bottom_ev": result_payload.get("work_function_bottom_ev"),
        "result_payload_path": str(payload_path),
        "result_payload_sha256": _canonical_json_sha256(result_payload),
        "result_payload_file_sha256": _file_sha256(payload_path),
        "output_structure": str(structure_path),
        "output_structure_file_sha256": _file_sha256(structure_path),
        "output_report": str(report_path),
        "output_report_sha256": _file_sha256(report_path),
        "result_metadata": str(metadata_path),
        "script_path": str(persisted_script_path),
        "script_sha256": script_sha256,
        "native_artifact_count": len(normalized_native_artifacts),
        "native_artifacts": normalized_native_artifacts,
    }
    metadata = dict(base_spec.metadata or {})
    history = [
        dict(item)
        for item in metadata.get("castep_electronic_calculation_history", []) or []
        if isinstance(item, dict)
    ]
    history.append(receipt)
    metadata.update(
        {
            "castep_electronic_calculation_history": history,
            "last_castep_electronic_calculation": receipt,
        }
    )
    outputs = dict(base_spec.outputs or {})
    outputs.update(
        {
            "castep_electronic_result_metadata": str(metadata_path),
            "castep_electronic_result_structure": str(structure_path),
            "castep_electronic_result_report": str(report_path),
        }
    )
    notes = list(base_spec.acceptance.notes)
    notes.append(
        "CASTEP Energy Results were recorded with immutable artifact binding; "
        "the MS 20.1 Results object does not independently prove SCF convergence "
        "or export numeric band/DOS curve data."
    )
    result_spec = base_spec.model_copy(
        update={
            "revision": target_revision,
            "simulation": simulation,
            "metadata": metadata,
            "outputs": outputs,
            "acceptance": base_spec.acceptance.model_copy(update={"notes": notes}),
        },
        deep=True,
    )
    return ModelSpec.model_validate(result_spec.model_dump(mode="json")), receipt


def verify_castep_electronic_receipt(spec: ModelSpec) -> dict[str, Any] | None:
    """Verify the latest electronic receipt against the current immutable spec."""

    raw = (spec.metadata or {}).get("last_castep_electronic_calculation")
    if not isinstance(raw, dict):
        return None
    receipt = dict(raw)
    checks: dict[str, bool] = {
        "schema": receipt.get("schema_version") == CASTEP_ELECTRONIC_RECEIPT_SCHEMA,
        "project": receipt.get("source_project_id") == spec.project_id,
        "target_revision": receipt.get("target_revision") == spec.revision,
        "source_revision_order": isinstance(receipt.get("source_revision"), int)
        and int(receipt["source_revision"]) < spec.revision,
        "task": isinstance(spec.simulation, CastepEnergySpec)
        and receipt.get("task") == spec.simulation.task.value,
        "simulation": isinstance(spec.simulation, CastepEnergySpec)
        and receipt.get("simulation_sha256")
        == _canonical_json_sha256(spec.simulation.model_dump(mode="json")),
        "structure": isinstance(spec.model, CrystalSpec)
        and receipt.get("target_structure_sha256")
        == crystal_structure_sha256(spec.model),
        "structure_unchanged": receipt.get("structure_unchanged") is True
        and receipt.get("source_structure_sha256")
        == receipt.get("target_structure_sha256"),
        "backend_completed": receipt.get("backend_run_completed") is True,
        "result_verified": receipt.get("calculation_result_verified") is True,
        "convergence_not_overclaimed": (
            receipt.get("scientific_convergence_verified") is False
            and receipt.get("scientific_convergence_claimed") is False
        ),
        "numeric_curve_not_overclaimed": (
            receipt.get("numeric_curve_data_exported") is False
        ),
        "script_file": _path_hash_matches(
            receipt.get("script_path"),
            receipt.get("script_sha256"),
        ),
        "result_payload_file": _path_hash_matches(
            receipt.get("result_payload_path"),
            receipt.get("result_payload_file_sha256"),
        ),
        "output_structure_file": _path_hash_matches(
            receipt.get("output_structure"),
            receipt.get("output_structure_file_sha256"),
        ),
        "output_report_file": _path_hash_matches(
            receipt.get("output_report"),
            receipt.get("output_report_sha256"),
        ),
        "native_artifacts": _native_artifacts_match(
            receipt.get("native_artifacts")
        ),
    }
    structure_artifact_validation: dict[str, Any] | None = None
    if isinstance(spec.model, CrystalSpec) and checks["output_structure_file"]:
        structure_artifact_validation = validate_crystal_cif_against_spec(
            spec.model,
            str(receipt.get("output_structure")),
        )
    checks["structure_artifact"] = bool(
        structure_artifact_validation
        and structure_artifact_validation.get("ok")
    )
    task = (
        spec.simulation.task
        if isinstance(spec.simulation, CastepEnergySpec)
        else None
    )
    expected_document = RESULT_DOCUMENT_BY_TASK.get(task) if task is not None else None
    checks["result_document"] = bool(
        expected_document is None
        or (
            receipt.get("required_result_document") == expected_document
            and isinstance(receipt.get("result_document_name"), str)
            and bool(str(receipt.get("result_document_name")).strip())
            and receipt.get("result_document_available") is True
        )
    )
    history = [
        item
        for item in (spec.metadata or {}).get(
            "castep_electronic_calculation_history", []
        )
        or []
        if isinstance(item, dict)
    ]
    checks["history"] = bool(history and history[-1] == raw)
    binding_verified = all(checks.values())
    warnings = [
        "MS 20.1 Energy Results do not expose an independent SCF convergence boolean.",
        "Numeric band/DOS curve data are not exported by the documented Chart Document API.",
    ]
    if task is CastepTask.BAND_STRUCTURE:
        warnings.append(
            "The returned BandStructureChart uses the Materials Studio native path; "
            "it is not bound to the MCP analytic band-path preview."
        )
    if not binding_verified:
        warnings.insert(
            0,
            "CASTEP electronic result receipt is not bound to the current immutable revision.",
        )
    return {
        "available": True,
        "schema_version": CASTEP_ELECTRONIC_RECEIPT_SCHEMA,
        "status": "verified" if binding_verified else "binding_mismatch",
        "binding_verified": binding_verified,
        "checks": checks,
        "task": receipt.get("task"),
        "source_revision": receipt.get("source_revision"),
        "target_revision": receipt.get("target_revision"),
        "backend_run_completed": receipt.get("backend_run_completed"),
        "scientific_convergence_verified": False,
        "numeric_curve_data_exported": False,
        "band_path_binding_verified": receipt.get("band_path_binding_verified"),
        "required_result_document": receipt.get("required_result_document"),
        "result_document_name": receipt.get("result_document_name"),
        "total_energy_kcal_per_mol": receipt.get("total_energy_kcal_per_mol"),
        "free_energy_kcal_per_mol": receipt.get("free_energy_kcal_per_mol"),
        "band_gap_ev": receipt.get("band_gap_ev"),
        "fermi_level_ev": receipt.get("fermi_level_ev"),
        "work_function_ev": receipt.get("work_function_ev"),
        "work_function_top_ev": receipt.get("work_function_top_ev"),
        "work_function_bottom_ev": receipt.get("work_function_bottom_ev"),
        "output_structure": receipt.get("output_structure"),
        "output_report": receipt.get("output_report"),
        "result_metadata": receipt.get("result_metadata"),
        "result_payload_path": receipt.get("result_payload_path"),
        "script_path": receipt.get("script_path"),
        "native_artifact_count": receipt.get("native_artifact_count"),
        "structure_artifact_validation": structure_artifact_validation,
        "warnings": warnings,
    }


def _validate_native_artifact_manifest(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in artifacts:
        path = Path(str(artifact.get("path"))).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"CASTEP native artifact was not found: {path}")
        path_key = str(path).lower()
        if path_key in seen:
            raise ValueError(f"CASTEP native artifact path is duplicated: {path}")
        seen.add(path_key)
        digest = _file_sha256(path)
        declared_digest = artifact.get("sha256")
        if declared_digest is not None and declared_digest != digest:
            raise ValueError(f"CASTEP native artifact hash mismatch: {path}")
        normalized.append(
            {
                "path": str(path),
                "sha256": digest,
                "size_bytes": path.stat().st_size,
            }
        )
    return sorted(normalized, key=lambda item: item["path"].lower())


def _native_artifacts_match(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    try:
        normalized = _validate_native_artifact_manifest(
            [dict(item) for item in value if isinstance(item, dict)]
        )
    except (OSError, ValueError):
        return False
    return len(normalized) == len(value)


def _path_hash_matches(path_value: Any, digest_value: Any) -> bool:
    if not isinstance(path_value, str) or not isinstance(digest_value, str):
        return False
    try:
        path = Path(path_value).expanduser().resolve()
        return path.is_file() and _file_sha256(path) == digest_value
    except OSError:
        return False


def _canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
