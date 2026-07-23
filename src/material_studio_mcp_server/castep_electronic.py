"""Trusted CASTEP electronic-result revision receipts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .castep_relaxation import crystal_structure_sha256
from .parsers.castep_native import (
    CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA,
    CASTEP_NATIVE_OUTPUT_AUDIT_SUPPORTED_SCHEMAS,
    CASTEP_SAMPLED_BAND_EDGE_SCHEMA,
)
from .parsers.cif import validate_crystal_cif_against_spec
from .specs.castep import CastepEnergySpec, CastepTask
from .specs.crystal import CrystalSpec
from .specs.project import ModelSpec


CASTEP_ELECTRONIC_RECEIPT_LEGACY_SCHEMA = (
    "material_studio_castep_electronic_receipt_v1"
)
CASTEP_ELECTRONIC_RECEIPT_SCHEMA = "material_studio_castep_electronic_receipt_v2"
CASTEP_ELECTRONIC_RESULT_ASSESSMENT_SCHEMA = (
    "material_studio_castep_electronic_result_assessment_v1"
)
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
    native_output_audit: dict[str, Any],
    native_output_audit_path: str | Path,
    derived_artifacts: list[dict[str, Any]],
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
    native_audit_path = Path(native_output_audit_path).expanduser().resolve()
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
    if not native_audit_path.is_file():
        raise ValueError(
            f"CASTEP native output audit was not found: {native_audit_path}"
        )
    try:
        persisted_native_audit = json.loads(
            native_audit_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("CASTEP native output audit is not valid JSON") from exc
    if _canonical_json_sha256(persisted_native_audit) != _canonical_json_sha256(
        native_output_audit
    ):
        raise ValueError("CASTEP native output audit payload mismatch")
    if native_output_audit.get("task") != simulation.task.value:
        raise ValueError("CASTEP native output audit task mismatch")

    expected_document = RESULT_DOCUMENT_BY_TASK[simulation.task]
    document_names = dict(result_payload.get("result_document_names") or {})
    chart_name = document_names.get(expected_document) if expected_document else None
    if expected_document is not None and not chart_name:
        raise ValueError(
            f"CASTEP {simulation.task.value} did not return {expected_document}"
        )
    normalized_native_artifacts = _validate_native_artifact_manifest(native_artifacts)
    normalized_derived_artifacts = _validate_native_artifact_manifest(
        derived_artifacts
    )
    audit_contract_errors = _native_output_audit_contract_errors(
        native_output_audit,
        task=simulation.task.value,
        native_artifacts=normalized_native_artifacts,
        derived_artifacts=normalized_derived_artifacts,
        reported_band_gap_ev=result_payload.get("band_gap_ev"),
    )
    if audit_contract_errors:
        raise ValueError(
            "CASTEP native output audit contract mismatch: "
            + "; ".join(audit_contract_errors)
        )
    numeric_curve_data_exported = native_output_audit[
        "numeric_curve_data_exported"
    ]
    sampled_band_edges = native_output_audit.get("sampled_band_edges")
    if not isinstance(sampled_band_edges, dict):
        sampled_band_edges = {}
    reported_gap_crosscheck = sampled_band_edges.get(
        "reported_band_gap_crosscheck"
    )
    if not isinstance(reported_gap_crosscheck, dict):
        reported_gap_crosscheck = {}
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
        "scientific_band_gap_verified": False,
        "scientific_band_gap_claimed": False,
        "required_result_document": expected_document,
        "result_document_name": chart_name,
        "result_document_available": bool(chart_name),
        "numeric_curve_data_exported": numeric_curve_data_exported,
        "numeric_curve_kind": native_output_audit.get("numeric_curve_kind"),
        "native_band_kpoint_path_exported": native_output_audit.get(
            "native_band_kpoint_path_exported"
        ),
        "pdos_projection_weights_exported": native_output_audit.get(
            "pdos_projection_weights_exported"
        ),
        "band_path_binding_verified": False,
        "sampled_band_edge_status": sampled_band_edges.get("status"),
        "sampled_band_gap_ev": sampled_band_edges.get("sampled_gap_ev"),
        "sampled_fermi_crossing_observed": sampled_band_edges.get(
            "fermi_crossing_observed"
        ),
        "reported_band_gap_crosscheck_status": reported_gap_crosscheck.get(
            "status"
        ),
        "reported_band_gap_difference_ev": reported_gap_crosscheck.get(
            "absolute_difference_ev"
        ),
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
        "native_output_audit": native_output_audit,
        "native_output_audit_path": str(native_audit_path),
        "native_output_audit_payload_sha256": _canonical_json_sha256(
            native_output_audit
        ),
        "native_output_audit_file_sha256": _file_sha256(native_audit_path),
        "derived_artifact_count": len(normalized_derived_artifacts),
        "derived_artifacts": normalized_derived_artifacts,
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
            "castep_electronic_native_output_audit": str(native_audit_path),
        }
    )
    for artifact in native_output_audit.get("derived_artifacts", []) or []:
        if not isinstance(artifact, dict):
            continue
        kind = artifact.get("artifact_kind")
        path = artifact.get("path")
        if kind == "castep_band_eigenvalues_csv" and isinstance(path, str):
            outputs["castep_band_eigenvalues_csv"] = path
        elif kind == "castep_gaussian_total_dos_csv" and isinstance(path, str):
            outputs["castep_gaussian_total_dos_csv"] = path
    notes = list(base_spec.acceptance.notes)
    notes.append(
        "CASTEP Energy Results were recorded with immutable native-output binding; "
        "MS 20.1 does not expose an independent SCF convergence boolean. "
        + (
            "Numeric property data were exported with the provenance recorded in "
            "the native-output audit."
            if numeric_curve_data_exported
            else "The requested numeric property curve was not exported."
        )
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
    schema_version = receipt.get("schema_version")
    legacy_receipt = schema_version == CASTEP_ELECTRONIC_RECEIPT_LEGACY_SCHEMA
    current_receipt = schema_version == CASTEP_ELECTRONIC_RECEIPT_SCHEMA
    native_output_audit = receipt.get("native_output_audit")
    if not isinstance(native_output_audit, dict):
        native_output_audit = None
    checks: dict[str, bool] = {
        "schema": legacy_receipt or current_receipt,
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
        "band_gap_not_overclaimed": (
            receipt.get("scientific_band_gap_verified") in (None, False)
            and receipt.get("scientific_band_gap_claimed") in (None, False)
        ),
        "numeric_curve_contract": (
            receipt.get("numeric_curve_data_exported") is False
            if legacy_receipt
            else bool(
                native_output_audit is not None
                and isinstance(
                    receipt.get("numeric_curve_data_exported"), bool
                )
                and receipt.get("numeric_curve_data_exported")
                == native_output_audit.get("numeric_curve_data_exported")
            )
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
    if current_receipt:
        checks.update(
            {
                "native_output_audit_file": _path_hash_matches(
                    receipt.get("native_output_audit_path"),
                    receipt.get("native_output_audit_file_sha256"),
                ),
                "native_output_audit_payload": _json_file_matches_payload(
                    receipt.get("native_output_audit_path"),
                    native_output_audit,
                    receipt.get("native_output_audit_payload_sha256"),
                ),
                "native_output_audit_task": bool(
                    native_output_audit is not None
                    and native_output_audit.get("task") == receipt.get("task")
                ),
                "derived_artifacts": _native_artifacts_match(
                    receipt.get("derived_artifacts")
                ),
                "native_artifact_count": _manifest_count_matches(
                    receipt.get("native_artifacts"),
                    receipt.get("native_artifact_count"),
                ),
                "derived_artifact_count": _manifest_count_matches(
                    receipt.get("derived_artifacts"),
                    receipt.get("derived_artifact_count"),
                ),
                "native_output_audit_contract": not _native_output_audit_contract_errors(
                    native_output_audit,
                    task=str(receipt.get("task") or ""),
                    native_artifacts=receipt.get("native_artifacts"),
                    derived_artifacts=receipt.get("derived_artifacts"),
                    reported_band_gap_ev=receipt.get("band_gap_ev"),
                ),
            }
        )
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
    ]
    numeric_export_claimed = receipt.get("numeric_curve_data_exported") is True
    numeric_exported = binding_verified and numeric_export_claimed
    sampled_band_edges = (
        native_output_audit.get("sampled_band_edges")
        if isinstance(native_output_audit, dict)
        and native_output_audit.get("schema_version")
        == CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA
        and isinstance(native_output_audit.get("sampled_band_edges"), dict)
        else None
    )
    trusted_band_edges = sampled_band_edges if binding_verified else None
    if numeric_exported:
        warnings.append(
            "Numeric data provenance comes from the native CASTEP .bands file, "
            "not from undocumented Chart Document export APIs."
        )
    elif numeric_export_claimed:
        warnings.append(
            "Numeric property data are claimed by the receipt, but their immutable "
            "artifact binding is not verified."
        )
    else:
        warnings.append("The requested numeric property curve was not exported.")
    if task is CastepTask.BAND_STRUCTURE:
        warnings.append(
            "The native .bands k-points are exported when available, but they are "
            "not asserted to equal the MCP analytic band-path preview."
        )
    if trusted_band_edges is not None:
        if trusted_band_edges.get("fermi_crossing_observed") is True:
            warnings.append(
                "Native sampled bands show a Fermi-level crossing; review metallic "
                "or semimetallic behavior before using a band-gap value."
            )
        crosscheck = trusted_band_edges.get("reported_band_gap_crosscheck")
        if isinstance(crosscheck, dict) and crosscheck.get("status") == (
            "review_difference"
        ):
            warnings.append(
                "The native sampled gap and Materials Studio BandGap result differ "
                "beyond the recorded comparison tolerance."
            )
    if not binding_verified:
        warnings.insert(
            0,
            "CASTEP electronic result receipt is not bound to the current immutable revision.",
        )
    return {
        "available": True,
        "schema_version": schema_version,
        "status": "verified" if binding_verified else "binding_mismatch",
        "binding_verified": binding_verified,
        "checks": checks,
        "task": receipt.get("task"),
        "source_revision": receipt.get("source_revision"),
        "target_revision": receipt.get("target_revision"),
        "backend_run_completed": receipt.get("backend_run_completed"),
        "scientific_convergence_verified": False,
        "scientific_band_gap_verified": False,
        "numeric_curve_data_exported": numeric_exported,
        "numeric_curve_data_claimed": numeric_export_claimed,
        "numeric_curve_kind": (
            receipt.get("numeric_curve_kind") if numeric_exported else None
        ),
        "numeric_curve_kind_claimed": receipt.get("numeric_curve_kind"),
        "native_band_kpoint_path_exported": bool(
            binding_verified
            and receipt.get("native_band_kpoint_path_exported") is True
        ),
        "pdos_projection_weights_exported": bool(
            binding_verified
            and receipt.get("pdos_projection_weights_exported") is True
        ),
        "band_path_binding_verified": receipt.get("band_path_binding_verified"),
        "sampled_band_edges": trusted_band_edges,
        "sampled_band_edge_status": (
            trusted_band_edges.get("status")
            if trusted_band_edges is not None
            else None
        ),
        "sampled_band_gap_ev": (
            trusted_band_edges.get("sampled_gap_ev")
            if trusted_band_edges is not None
            else None
        ),
        "sampled_fermi_crossing_observed": (
            trusted_band_edges.get("fermi_crossing_observed")
            if trusted_band_edges is not None
            else None
        ),
        "reported_band_gap_crosscheck": (
            trusted_band_edges.get("reported_band_gap_crosscheck")
            if trusted_band_edges is not None
            else None
        ),
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
        "native_output_audit": native_output_audit,
        "native_output_audit_path": receipt.get("native_output_audit_path"),
        "derived_artifact_count": receipt.get("derived_artifact_count"),
        "derived_artifacts": receipt.get("derived_artifacts"),
        "structure_artifact_validation": structure_artifact_validation,
        "warnings": warnings,
    }


def assess_castep_electronic_result(
    spec: ModelSpec,
    *,
    receipt_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return an actionable result-review assessment without a science claim."""

    summary = (
        receipt_summary
        if receipt_summary is not None
        else verify_castep_electronic_receipt(spec)
    )
    if summary is None:
        return None

    binding_verified = summary.get("binding_verified") is True
    native_audit = (
        summary.get("native_output_audit")
        if isinstance(summary.get("native_output_audit"), dict)
        else {}
    )
    scf_audit = (
        native_audit.get("castep_output_audit")
        if isinstance(native_audit.get("castep_output_audit"), dict)
        else {}
    )
    band_edges = (
        summary.get("sampled_band_edges")
        if isinstance(summary.get("sampled_band_edges"), dict)
        else {}
    )
    gap_crosscheck = (
        band_edges.get("reported_band_gap_crosscheck")
        if isinstance(band_edges.get("reported_band_gap_crosscheck"), dict)
        else {}
    )
    task = str(summary.get("task") or "")
    native_status = native_audit.get("status")
    scf_status = scf_audit.get("status")
    sampled_status = band_edges.get("status")
    crossing_observed = band_edges.get("fermi_crossing_observed") is True
    gap_crosscheck_status = gap_crosscheck.get("status")

    review_reasons: list[str] = ["scientific_convergence_unverified"]
    if not binding_verified:
        review_reasons.insert(0, "receipt_binding_mismatch")
    if not native_audit:
        review_reasons.append("native_output_audit_unavailable")
    elif native_status != "complete":
        review_reasons.append(f"native_output_audit_{native_status or 'unknown'}")
    if scf_status != "completed_below_max_cycles":
        review_reasons.append("native_scf_completion_review_required")
    if band_edges:
        review_reasons.append("scientific_band_gap_unverified")
        if crossing_observed:
            review_reasons.append("sampled_fermi_crossing_observed")
        if gap_crosscheck_status == "review_difference":
            review_reasons.append("reported_band_gap_difference")
        if sampled_status not in {"sampled_gap", "sampled_fermi_crossing"}:
            review_reasons.append("sampled_band_edge_evidence_incomplete")
    else:
        review_reasons.append("sampled_band_edge_evidence_unavailable")
    if task == CastepTask.BAND_STRUCTURE.value and summary.get(
        "band_path_binding_verified"
    ) is not True:
        review_reasons.append("analytic_band_path_unbound")
    if task in {
        CastepTask.BAND_STRUCTURE.value,
        CastepTask.DENSITY_OF_STATES.value,
        CastepTask.PROJECTED_DENSITY_OF_STATES.value,
    } and summary.get("numeric_curve_data_exported") is not True:
        review_reasons.append("numeric_property_curve_unavailable")
    if task == CastepTask.PROJECTED_DENSITY_OF_STATES.value and summary.get(
        "pdos_projection_weights_exported"
    ) is not True:
        review_reasons.append("pdos_projection_weights_unavailable")
    review_reasons = list(dict.fromkeys(review_reasons))

    if not binding_verified:
        status = "binding_mismatch"
        trust_status = "untrusted"
        action_id = "preview_castep_electronic_rerun_after_binding_review"
        recommended_action = (
            "review_artifact_binding_then_preview_the_current_castep_task_again"
        )
        recommended_task = task
    elif native_status != "complete":
        status = "native_output_review_required"
        trust_status = "artifact_bound_review_required"
        action_id = "review_native_castep_output_before_rerun"
        recommended_action = (
            "inspect_native_output_errors_and_settings_then_preview_a_rerun"
        )
        recommended_task = task
    elif scf_status != "completed_below_max_cycles":
        status = "native_scf_review_required"
        trust_status = "artifact_bound_review_required"
        action_id = "review_native_scf_evidence_before_rerun"
        recommended_action = (
            "review_scf_markers_and_settings_then_preview_a_rerun"
        )
        recommended_task = task
    elif crossing_observed:
        status = "sampled_fermi_crossing_review"
        trust_status = "sampled_evidence_review_required"
        action_id = "review_sampled_fermi_crossing"
        recommended_action = (
            "review_metallic_or_semimetallic_behavior_with_band_structure_and_dos_sampling"
        )
        recommended_task = CastepTask.BAND_STRUCTURE.value
    elif gap_crosscheck_status == "review_difference":
        status = "reported_band_gap_difference_review"
        trust_status = "sampled_evidence_review_required"
        action_id = "review_reported_band_gap_difference"
        recommended_action = (
            "review_native_kpoint_sampling_and_reported_band_gap_before_rerun"
        )
        recommended_task = CastepTask.BAND_STRUCTURE.value
    elif sampled_status == "sampled_gap":
        status = "sampled_band_edges_available"
        trust_status = "sampled_evidence_review_required"
        action_id = "review_band_gap_convergence"
        recommended_action = (
            "review_scf_and_kpoint_convergence_before_any_scientific_band_gap_claim"
        )
        recommended_task = CastepTask.BAND_STRUCTURE.value
    else:
        status = "sampled_band_edges_unavailable"
        trust_status = "artifact_bound_review_required"
        action_id = "preview_band_structure_for_band_edge_evidence"
        recommended_action = (
            "preview_reviewed_band_structure_sampling_for_band_edge_evidence"
        )
        recommended_task = CastepTask.BAND_STRUCTURE.value

    preview_payload = {
        "project_id": spec.project_id,
        "task": recommended_task or task or CastepTask.ENERGY.value,
        "execution_mode": "preview",
        "open_in_gui": False,
        "take_snapshot": False,
        "export_view_audit": True,
    }
    return {
        "schema_version": CASTEP_ELECTRONIC_RESULT_ASSESSMENT_SCHEMA,
        "available": True,
        "status": status,
        "trust_status": trust_status,
        "receipt_binding_verified": binding_verified,
        "backend_run_completed": summary.get("backend_run_completed") is True,
        "artifact_evidence_verified": bool(
            binding_verified and native_status == "complete"
        ),
        "scientific_convergence_verified": False,
        "scientific_band_gap_verified": False,
        "scientific_result_verified": False,
        "structure_normality_blocked": False,
        "structure_normality_impact": "none",
        "calculation_result_review_required": True,
        "calculation_readiness_impact": "result_review_only",
        "task": task or None,
        "source_revision": summary.get("source_revision"),
        "target_revision": summary.get("target_revision"),
        "native_output_audit_status": native_status,
        "native_scf_status": scf_status,
        "native_scf_maximum_cycles_reached": scf_audit.get(
            "maximum_scf_cycles_reached"
        ),
        "sampled_band_edge_status": sampled_status,
        "sampled_gap_ev": band_edges.get("sampled_gap_ev"),
        "sampled_gap_spin_component": band_edges.get("gap_spin_component"),
        "sampled_fermi_crossing_observed": (
            crossing_observed if band_edges else None
        ),
        "reported_band_gap_crosscheck_status": gap_crosscheck_status,
        "reported_band_gap_difference_ev": gap_crosscheck.get(
            "absolute_difference_ev"
        ),
        "reported_band_gap_comparison_tolerance_ev": gap_crosscheck.get(
            "comparison_tolerance_ev"
        ),
        "numeric_curve_data_exported": summary.get(
            "numeric_curve_data_exported"
        ),
        "native_band_kpoint_path_exported": summary.get(
            "native_band_kpoint_path_exported"
        ),
        "band_path_binding_verified": summary.get(
            "band_path_binding_verified"
        ),
        "pdos_projection_weights_exported": summary.get(
            "pdos_projection_weights_exported"
        ),
        "result_review_reasons": review_reasons,
        "result_review_reason_count": len(review_reasons),
        "recommended_action_id": action_id,
        "recommended_tool": "material_studio_castep_run_current",
        "recommended_action": recommended_action,
        "recommended_preview_payload": preview_payload,
        "preview_safe": True,
        "execute_requires_explicit_confirmation": True,
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


def _manifest_count_matches(value: Any, count: Any) -> bool:
    return (
        isinstance(value, list)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count == len(value)
    )


def _native_output_audit_contract_errors(
    audit: Any,
    *,
    task: str,
    native_artifacts: Any,
    derived_artifacts: Any,
    reported_band_gap_ev: Any,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(audit, dict):
        return ["audit payload is not an object"]
    audit_schema = audit.get("schema_version")
    if audit_schema not in CASTEP_NATIVE_OUTPUT_AUDIT_SUPPORTED_SCHEMAS:
        errors.append("audit schema is unsupported")
    if audit.get("task") != task:
        errors.append("audit task does not match the receipt task")
    if audit.get("status") not in {
        "complete",
        "partial",
        "review_required",
        "unavailable",
    }:
        errors.append("audit status is unsupported")
    if audit.get("scientific_convergence_verified") is not False:
        errors.append("audit overclaims scientific convergence")

    numeric_exported = audit.get("numeric_curve_data_exported")
    if not isinstance(numeric_exported, bool):
        errors.append("numeric export flag is not boolean")
        numeric_exported = False
    if not isinstance(audit.get("native_band_kpoint_path_exported"), bool):
        errors.append("native band-path export flag is not boolean")
    if audit.get("pdos_projection_weights_exported") is not False:
        errors.append("PDOS projection-weight export must remain false")

    try:
        normalized_native = _normalize_manifest_for_contract(native_artifacts)
        normalized_derived = _normalize_manifest_for_contract(derived_artifacts)
        normalized_audit_derived = _normalize_manifest_for_contract(
            audit.get("derived_artifacts")
        )
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        return errors

    if normalized_audit_derived != normalized_derived:
        errors.append("audit derived-artifact manifest does not match the receipt")
    if audit.get("native_artifact_count") != len(normalized_native):
        errors.append("audit native-artifact count does not match the receipt")
    if audit.get("derived_artifact_count") != len(normalized_derived):
        errors.append("audit derived-artifact count does not match the receipt")

    native_by_path = {
        item["path"].lower(): item["sha256"] for item in normalized_native
    }
    for component_name in ("castep_output_audit", "bands_summary"):
        component = audit.get(component_name)
        if component is None:
            continue
        if not isinstance(component, dict):
            errors.append(f"{component_name} is not an object")
            continue
        source_path = component.get("source_path")
        source_sha256 = component.get("source_sha256")
        if not isinstance(source_path, str) or not isinstance(source_sha256, str):
            errors.append(f"{component_name} lacks source path/hash binding")
            continue
        try:
            source_key = str(Path(source_path).expanduser().resolve()).lower()
        except OSError:
            errors.append(f"{component_name} source path is invalid")
            continue
        if native_by_path.get(source_key) != source_sha256:
            errors.append(f"{component_name} source is not bound to native artifacts")
    output_audit = audit.get("castep_output_audit")
    if isinstance(output_audit, dict):
        if output_audit.get(
            "schema_version"
        ) not in CASTEP_NATIVE_OUTPUT_AUDIT_SUPPORTED_SCHEMAS:
            errors.append("nested CASTEP output audit schema is unsupported")
        if output_audit.get("scientific_convergence_verified") is not False:
            errors.append("nested CASTEP output audit overclaims convergence")
    sampled_band_edges = audit.get("sampled_band_edges")
    if audit_schema == CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA:
        errors.extend(
            _sampled_band_edge_contract_errors(
                sampled_band_edges,
                bands_summary=audit.get("bands_summary"),
                reported_band_gap_ev=reported_band_gap_ev,
            )
        )
        if audit.get("scientific_band_gap_verified") is not False:
            errors.append("native audit overclaims scientific band-gap verification")
    elif sampled_band_edges is not None:
        errors.append("legacy native audit cannot carry sampled band-edge evidence")

    kinds = {
        item.get("artifact_kind")
        for item in (audit.get("derived_artifacts") or [])
        if isinstance(item, dict)
    }
    numeric_kind = audit.get("numeric_curve_kind")
    if task == CastepTask.ENERGY.value:
        if numeric_exported or numeric_kind is not None or kinds:
            errors.append("Energy task must not claim a numeric property curve")
    elif task == CastepTask.BAND_STRUCTURE.value:
        expected = "castep_band_eigenvalues_csv"
        if numeric_exported:
            if numeric_kind != "native_castep_band_eigenvalues":
                errors.append("BandStructure numeric export kind is invalid")
            if kinds != {expected}:
                errors.append("BandStructure derived-artifact set is invalid")
            if audit.get("native_band_kpoint_path_exported") is not True:
                errors.append("BandStructure native k-point path flag is missing")
        elif numeric_kind is not None:
            errors.append("BandStructure numeric kind exists without an export")
    elif task == CastepTask.DENSITY_OF_STATES.value:
        if numeric_exported:
            if numeric_kind != "mcp_gaussian_total_dos_from_native_bands":
                errors.append("DOS numeric export kind is invalid")
            if kinds != {
                "castep_band_eigenvalues_csv",
                "castep_gaussian_total_dos_csv",
            }:
                errors.append("DOS derived-artifact set is invalid")
        elif numeric_kind is not None:
            errors.append("DOS numeric kind exists without an export")
    elif task == CastepTask.PROJECTED_DENSITY_OF_STATES.value:
        if numeric_exported or numeric_kind is not None:
            errors.append("PDOS numeric projection export is not supported")
        if not kinds.issubset({"castep_band_eigenvalues_csv"}):
            errors.append("PDOS derived-artifact set is invalid")
    else:
        errors.append("receipt task is not a supported electronic task")
    return errors


def _sampled_band_edge_contract_errors(
    summary: Any,
    *,
    bands_summary: Any,
    reported_band_gap_ev: Any,
) -> list[str]:
    if bands_summary is None:
        return [] if summary is None else ["band-edge audit exists without .bands data"]
    if not isinstance(summary, dict):
        return ["current native audit is missing sampled band-edge evidence"]
    errors: list[str] = []
    if summary.get("schema_version") != CASTEP_SAMPLED_BAND_EDGE_SCHEMA:
        errors.append("sampled band-edge schema is unsupported")
    if summary.get("status") not in {
        "sampled_gap",
        "sampled_fermi_crossing",
        "partial",
        "insufficient_states",
    }:
        errors.append("sampled band-edge status is unsupported")
    if summary.get("scientific_band_gap_verified") is not False:
        errors.append("sampled band-edge audit overclaims a scientific gap")
    if not isinstance(bands_summary, dict):
        errors.append("bands summary is not an object")
    else:
        if summary.get("number_of_kpoints") != bands_summary.get(
            "number_of_kpoints"
        ):
            errors.append("sampled band-edge k-point count mismatch")
        if summary.get("number_of_spin_components") != bands_summary.get(
            "number_of_spin_components"
        ):
            errors.append("sampled band-edge spin count mismatch")
    crossing = summary.get("fermi_crossing_observed")
    if not isinstance(crossing, bool):
        errors.append("sampled Fermi-crossing flag is not boolean")
    sampled_gap = summary.get("sampled_gap_ev")
    if sampled_gap is not None and (
        not isinstance(sampled_gap, (int, float))
        or isinstance(sampled_gap, bool)
        or not math.isfinite(float(sampled_gap))
        or float(sampled_gap) < 0
    ):
        errors.append("sampled band gap is not a non-negative finite value")
    if crossing is True and sampled_gap != 0.0:
        errors.append("sampled Fermi crossing must force the sampled gap to zero")
    crosscheck = summary.get("reported_band_gap_crosscheck")
    if not isinstance(crosscheck, dict):
        errors.append("reported BandGap crosscheck is missing")
    else:
        if crosscheck.get("status") not in {
            "reported_gap_unavailable",
            "sampled_gap_unavailable",
            "within_tolerance",
            "review_difference",
        }:
            errors.append("reported BandGap crosscheck status is unsupported")
        if crosscheck.get("scientific_consistency_verified") is not False:
            errors.append("reported BandGap crosscheck overclaims consistency")
        if crosscheck.get("reported_band_gap_ev") != reported_band_gap_ev:
            errors.append("reported BandGap crosscheck is not bound to result payload")
    return errors


def _normalize_manifest_for_contract(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("artifact manifest is not a list of objects")
    return _validate_native_artifact_manifest([dict(item) for item in value])


def _path_hash_matches(path_value: Any, digest_value: Any) -> bool:
    if not isinstance(path_value, str) or not isinstance(digest_value, str):
        return False
    try:
        path = Path(path_value).expanduser().resolve()
        return path.is_file() and _file_sha256(path) == digest_value
    except OSError:
        return False


def _json_file_matches_payload(
    path_value: Any,
    payload_value: Any,
    digest_value: Any,
) -> bool:
    if (
        not isinstance(path_value, str)
        or not isinstance(payload_value, dict)
        or not isinstance(digest_value, str)
    ):
        return False
    try:
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            return False
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        _canonical_json_sha256(parsed) == digest_value
        and _canonical_json_sha256(payload_value) == digest_value
    )


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
