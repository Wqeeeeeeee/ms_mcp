"""Revision-bound CASTEP electronic parameter-sensitivity audits."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from .castep_electronic import verify_castep_electronic_receipt
from .castep_relaxation import crystal_structure_sha256
from .specs.castep import CastepEnergySpec
from .specs.crystal import CrystalSpec
from .specs.project import ModelSpec


CASTEP_CONVERGENCE_AUDIT_SCHEMA = "material_studio_castep_convergence_audit_v1"
DEFAULT_ENERGY_TOLERANCE_EV_PER_ATOM = 0.01
DEFAULT_BAND_GAP_TOLERANCE_EV = 0.05
KCAL_PER_MOL_PER_EV = 23.060548867

_ELECTRONIC_PAYLOAD_FIELDS = (
    "task",
    "quality",
    "functional",
    "cutoff_energy_ev",
    "kpoint_separation",
    "kpoints",
    "properties_kpoint_separation",
    "band_structure_energy_max_ev",
    "band_structure_extra_bands",
    "band_structure_energy_tolerance_ev",
    "dos_energy_max_ev",
    "dos_extra_bands",
    "dos_energy_tolerance_ev",
    "dos_smearing_width_ev",
    "dos_integration_method",
    "dipole_correction",
)


def audit_castep_convergence_series(
    current_spec: ModelSpec,
    revision_specs: Mapping[int, ModelSpec],
    *,
    revision_load_errors: Mapping[int, str] | None = None,
    energy_tolerance_ev_per_atom: float = DEFAULT_ENERGY_TOLERANCE_EV_PER_ATOM,
    band_gap_tolerance_ev: float = DEFAULT_BAND_GAP_TOLERANCE_EV,
) -> dict[str, Any] | None:
    """Audit comparable CASTEP results without claiming scientific convergence."""

    history = [
        dict(item)
        for item in (current_spec.metadata or {}).get(
            "castep_electronic_calculation_history", []
        )
        or []
        if isinstance(item, dict)
    ]
    if not history:
        return None
    if not isinstance(current_spec.model, CrystalSpec):
        return _unavailable_audit(
            current_spec,
            history,
            reason="current_model_is_not_crystal",
            energy_tolerance_ev_per_atom=energy_tolerance_ev_per_atom,
            band_gap_tolerance_ev=band_gap_tolerance_ev,
        )
    if not math.isfinite(energy_tolerance_ev_per_atom) or energy_tolerance_ev_per_atom <= 0:
        raise ValueError("energy_tolerance_ev_per_atom must be positive and finite")
    if not math.isfinite(band_gap_tolerance_ev) or band_gap_tolerance_ev <= 0:
        raise ValueError("band_gap_tolerance_ev must be positive and finite")

    current_structure_sha256 = crystal_structure_sha256(current_spec.model)
    atom_count = len(current_spec.model.basis_atoms)
    load_errors = {int(key): str(value) for key, value in (revision_load_errors or {}).items()}
    binding_errors: list[dict[str, Any]] = []
    verified_points: list[dict[str, Any]] = []
    seen_target_revisions: set[int] = set()

    for history_index, receipt in enumerate(history):
        target_revision = receipt.get("target_revision")
        if not isinstance(target_revision, int) or isinstance(target_revision, bool):
            binding_errors.append(
                _binding_error(history_index, None, "target_revision_invalid")
            )
            continue
        if target_revision in seen_target_revisions:
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "duplicate_target_revision",
                )
            )
            continue
        seen_target_revisions.add(target_revision)
        if target_revision in load_errors:
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "revision_load_failed",
                    detail=load_errors[target_revision],
                )
            )
            continue
        target_spec = revision_specs.get(target_revision)
        if target_spec is None:
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "target_revision_missing",
                )
            )
            continue
        if (
            target_spec.project_id != current_spec.project_id
            or target_spec.revision != target_revision
        ):
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "target_revision_identity_mismatch",
                )
            )
            continue
        target_receipt = (target_spec.metadata or {}).get(
            "last_castep_electronic_calculation"
        )
        if target_receipt != receipt:
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "history_target_receipt_mismatch",
                )
            )
            continue
        receipt_summary = verify_castep_electronic_receipt(target_spec)
        if not receipt_summary or receipt_summary.get("binding_verified") is not True:
            failed_checks = [
                key
                for key, value in (receipt_summary or {}).get("checks", {}).items()
                if value is not True
            ]
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "receipt_binding_failed",
                    detail=",".join(failed_checks) or None,
                )
            )
            continue
        if not isinstance(target_spec.model, CrystalSpec):
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "target_model_is_not_crystal",
                )
            )
            continue
        target_structure_sha256 = crystal_structure_sha256(target_spec.model)
        if target_structure_sha256 != current_structure_sha256:
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "current_structure_binding_mismatch",
                )
            )
            continue
        if not isinstance(target_spec.simulation, CastepEnergySpec):
            binding_errors.append(
                _binding_error(
                    history_index,
                    target_revision,
                    "target_simulation_is_not_castep_energy",
                )
            )
            continue
        verified_points.append(
            _convergence_point(
                target_spec,
                receipt,
                receipt_summary,
                atom_count=atom_count,
            )
        )

    verified_points.sort(key=lambda point: int(point["target_revision"]))
    series = _build_comparable_series(
        verified_points,
        energy_tolerance_ev_per_atom=energy_tolerance_ev_per_atom,
        band_gap_tolerance_ev=band_gap_tolerance_ev,
    )
    latest_verified_revision = max(
        (int(point["target_revision"]) for point in verified_points),
        default=None,
    )
    active_series = _select_active_series(series, latest_verified_revision)
    stable_count = sum(
        item.get("status") == "parameter_sensitivity_within_tolerance"
        for item in series
    )
    above_tolerance_count = sum(
        item.get("status") == "parameter_sensitivity_above_tolerance"
        for item in series
    )
    pairwise_only_count = sum(
        item.get("status") == "pairwise_evidence_only" for item in series
    )
    artifact_evidence_verified = bool(
        not binding_errors and len(verified_points) == len(history)
    )
    parameter_sensitivity_evidence_verified = bool(
        artifact_evidence_verified and series
    )
    parameter_sensitivity_within_tolerance: bool | None
    if not parameter_sensitivity_evidence_verified:
        parameter_sensitivity_within_tolerance = None
    elif above_tolerance_count:
        parameter_sensitivity_within_tolerance = False
    elif stable_count == len(series):
        parameter_sensitivity_within_tolerance = True
    else:
        parameter_sensitivity_within_tolerance = None

    if binding_errors:
        status = "history_binding_review_required"
    elif not verified_points:
        status = "no_verified_result_points"
    elif not series:
        status = "insufficient_comparable_points"
    elif above_tolerance_count:
        status = "parameter_sensitivity_above_tolerance"
    elif stable_count == len(series):
        status = "parameter_sensitivity_within_tolerance"
    else:
        status = "pairwise_evidence_only"

    review_reasons = ["scientific_convergence_unverified"]
    if binding_errors:
        review_reasons.append("convergence_history_binding_failed")
    elif not series:
        review_reasons.append("comparable_parameter_series_missing")
    elif above_tolerance_count:
        review_reasons.append("parameter_sensitivity_above_tolerance")
    elif pairwise_only_count:
        review_reasons.append("three_point_sequence_required")
    else:
        review_reasons.append("parameter_sensitivity_is_not_scientific_convergence")
    next_action = _next_action(
        current_spec,
        status=status,
        active_series=active_series,
        binding_errors=binding_errors,
    )

    return {
        "schema_version": CASTEP_CONVERGENCE_AUDIT_SCHEMA,
        "available": True,
        "status": status,
        "project_id": current_spec.project_id,
        "current_revision": current_spec.revision,
        "current_structure_sha256": current_structure_sha256,
        "atom_count": atom_count,
        "history_entry_count": len(history),
        "verified_point_count": len(verified_points),
        "rejected_point_count": len(binding_errors),
        "artifact_evidence_verified": artifact_evidence_verified,
        "parameter_sensitivity_evidence_verified": (
            parameter_sensitivity_evidence_verified
        ),
        "parameter_sensitivity_within_tolerance": (
            parameter_sensitivity_within_tolerance
        ),
        "scientific_convergence_verified": False,
        "scientific_convergence_claimed": False,
        "scientific_band_gap_verified": False,
        "structure_normality_blocked": False,
        "structure_normality_impact": "none",
        "calculation_result_review_required": True,
        "calculation_readiness_impact": "result_review_only",
        "energy_tolerance_ev_per_atom": energy_tolerance_ev_per_atom,
        "band_gap_tolerance_ev": band_gap_tolerance_ev,
        "minimum_sequence_point_count": 3,
        "comparable_series_count": len(series),
        "stable_series_count": stable_count,
        "above_tolerance_series_count": above_tolerance_count,
        "pairwise_only_series_count": pairwise_only_count,
        "latest_verified_result_revision": latest_verified_revision,
        "current_revision_is_verified_result": (
            latest_verified_revision == current_spec.revision
        ),
        "active_series_id": active_series.get("series_id") if active_series else None,
        "active_axis": active_series.get("axis") if active_series else None,
        "points": verified_points,
        "series": series,
        "binding_errors": binding_errors,
        "result_review_reasons": review_reasons,
        "result_review_reason_count": len(review_reasons),
        **next_action,
    }


def _unavailable_audit(
    spec: ModelSpec,
    history: list[dict[str, Any]],
    *,
    reason: str,
    energy_tolerance_ev_per_atom: float,
    band_gap_tolerance_ev: float,
) -> dict[str, Any]:
    return {
        "schema_version": CASTEP_CONVERGENCE_AUDIT_SCHEMA,
        "available": False,
        "status": reason,
        "project_id": spec.project_id,
        "current_revision": spec.revision,
        "history_entry_count": len(history),
        "verified_point_count": 0,
        "artifact_evidence_verified": False,
        "parameter_sensitivity_evidence_verified": False,
        "parameter_sensitivity_within_tolerance": None,
        "scientific_convergence_verified": False,
        "scientific_convergence_claimed": False,
        "structure_normality_blocked": False,
        "calculation_result_review_required": True,
        "energy_tolerance_ev_per_atom": energy_tolerance_ev_per_atom,
        "band_gap_tolerance_ev": band_gap_tolerance_ev,
        "points": [],
        "series": [],
        "binding_errors": [{"reason": reason}],
        "result_review_reasons": [
            "scientific_convergence_unverified",
            reason,
        ],
        "recommended_action_id": "review_convergence_audit_unavailable",
        "recommended_action": "review_convergence_audit_inputs",
        "recommended_tool": None,
        "recommended_preview_payload": None,
        "preview_safe": True,
        "execute_requires_explicit_confirmation": True,
    }


def _binding_error(
    history_index: int,
    target_revision: int | None,
    reason: str,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    result = {
        "history_index": history_index,
        "target_revision": target_revision,
        "reason": reason,
    }
    if detail:
        result["detail"] = detail[:500]
    return result


def _convergence_point(
    spec: ModelSpec,
    receipt: dict[str, Any],
    receipt_summary: dict[str, Any],
    *,
    atom_count: int,
) -> dict[str, Any]:
    assert isinstance(spec.simulation, CastepEnergySpec)
    simulation = spec.simulation.model_dump(mode="json")
    total_energy_kcal_per_mol = _finite_float(
        receipt_summary.get("total_energy_kcal_per_mol")
    )
    total_energy_ev_per_cell = (
        total_energy_kcal_per_mol / KCAL_PER_MOL_PER_EV
        if total_energy_kcal_per_mol is not None
        else None
    )
    total_energy_ev_per_atom = (
        total_energy_ev_per_cell / atom_count
        if total_energy_ev_per_cell is not None and atom_count > 0
        else None
    )
    native_output = receipt_summary.get("native_output_audit")
    native_output = native_output if isinstance(native_output, dict) else {}
    native_scf = native_output.get("castep_output_audit")
    native_scf = native_scf if isinstance(native_scf, dict) else {}
    kpoints = simulation.get("kpoints")
    kpoint_grid = list(kpoints) if isinstance(kpoints, (list, tuple)) else None
    return {
        "target_revision": spec.revision,
        "source_revision": receipt.get("source_revision"),
        "task": spec.simulation.task.value,
        "functional": spec.simulation.functional,
        "quality": spec.simulation.quality,
        "structure_sha256": receipt.get("target_structure_sha256"),
        "simulation_sha256": receipt.get("simulation_sha256"),
        "receipt_sha256": _canonical_json_sha256(receipt),
        "receipt_schema_version": receipt.get("schema_version"),
        "simulation": simulation,
        "cutoff_energy_ev": spec.simulation.cutoff_energy_ev,
        "kpoint_mode": (
            "custom_grid"
            if spec.simulation.kpoints is not None
            else "separation"
            if spec.simulation.kpoint_separation is not None
            else "quality_default"
        ),
        "kpoint_grid": kpoint_grid,
        "kpoint_grid_product": (
            math.prod(kpoint_grid) if kpoint_grid is not None else None
        ),
        "kpoint_separation": spec.simulation.kpoint_separation,
        "properties_kpoint_separation": (
            spec.simulation.properties_kpoint_separation
        ),
        "total_energy_kcal_per_mol": total_energy_kcal_per_mol,
        "total_energy_ev_per_cell": total_energy_ev_per_cell,
        "total_energy_ev_per_atom": total_energy_ev_per_atom,
        "band_gap_ev": _finite_float(receipt_summary.get("band_gap_ev")),
        "fermi_level_ev": _finite_float(receipt_summary.get("fermi_level_ev")),
        "native_output_audit_status": native_output.get("status"),
        "native_scf_status": native_scf.get("status"),
        "native_scf_last_iteration": native_scf.get("last_scf_iteration"),
        "native_scf_maximum_cycles_reached": native_scf.get(
            "maximum_scf_cycles_reached"
        ),
        "binding_verified": True,
        "scientific_convergence_verified": False,
        "output_report_sha256": receipt.get("output_report_sha256"),
        "native_output_audit_file_sha256": receipt.get(
            "native_output_audit_file_sha256"
        ),
    }


def _build_comparable_series(
    points: list[dict[str, Any]],
    *,
    energy_tolerance_ev_per_atom: float,
    band_gap_tolerance_ev: float,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for point in points:
        simulation = point["simulation"]
        cutoff = simulation.get("cutoff_energy_ev")
        if cutoff is not None:
            signature = _series_signature(simulation, {"cutoff_energy_ev"})
            buckets[("cutoff_energy_ev", "explicit", signature)].append(point)
        kpoint_separation = simulation.get("kpoint_separation")
        kpoints = simulation.get("kpoints")
        if kpoint_separation is not None and kpoints is None:
            signature = _series_signature(simulation, {"kpoint_separation"})
            buckets[("kpoint_separation", "separation", signature)].append(point)
        if isinstance(kpoints, (list, tuple)) and kpoint_separation is None:
            signature = _series_signature(simulation, {"kpoints"})
            buckets[("kpoint_grid", "custom_grid", signature)].append(point)
        properties_separation = simulation.get("properties_kpoint_separation")
        if properties_separation is not None:
            signature = _series_signature(
                simulation,
                {"properties_kpoint_separation"},
            )
            buckets[
                ("properties_kpoint_separation", "separation", signature)
            ].append(point)

    series: list[dict[str, Any]] = []
    for (axis, mode, signature), candidates in buckets.items():
        by_value: dict[str, dict[str, Any]] = {}
        for point in candidates:
            axis_value = _axis_value(point, axis)
            value_key = json.dumps(axis_value, sort_keys=True, separators=(",", ":"))
            existing = by_value.get(value_key)
            if existing is None or int(point["target_revision"]) > int(
                existing["target_revision"]
            ):
                by_value[value_key] = point
        if len(by_value) < 2:
            continue
        ordered = sorted(
            by_value.values(),
            key=lambda point: _refinement_sort_key(point, axis),
        )
        deltas = [
            _series_delta(
                coarse,
                fine,
                axis=axis,
                energy_tolerance_ev_per_atom=energy_tolerance_ev_per_atom,
                band_gap_tolerance_ev=band_gap_tolerance_ev,
            )
            for coarse, fine in zip(ordered, ordered[1:])
        ]
        if any(delta.get("refinement_verified") is not True for delta in deltas):
            continue
        if any(int(delta.get("available_metric_count") or 0) == 0 for delta in deltas):
            continue
        latest_pair = deltas[-1]
        point_count = len(ordered)
        if point_count < 3:
            status = "pairwise_evidence_only"
        elif latest_pair.get("all_available_metrics_within_tolerance") is True:
            status = "parameter_sensitivity_within_tolerance"
        else:
            status = "parameter_sensitivity_above_tolerance"
        series_id = hashlib.sha256(
            f"{axis}:{mode}:{signature}".encode("utf-8")
        ).hexdigest()[:20]
        series.append(
            {
                "series_id": series_id,
                "axis": axis,
                "axis_mode": mode,
                "comparison_signature_sha256": signature,
                "status": status,
                "point_count": point_count,
                "minimum_sequence_point_count": 3,
                "sequence_evidence_sufficient": point_count >= 3,
                "latest_pair_within_tolerance": latest_pair.get(
                    "all_available_metrics_within_tolerance"
                ),
                "scientific_convergence_verified": False,
                "refinement_direction": _refinement_direction(axis),
                "axis_values": [_axis_value(point, axis) for point in ordered],
                "target_revisions": [
                    int(point["target_revision"]) for point in ordered
                ],
                "points": ordered,
                "deltas": deltas,
            }
        )
    series.sort(
        key=lambda item: (
            -int(item["point_count"]),
            str(item["axis"]),
            str(item["series_id"]),
        )
    )
    return series


def _series_signature(simulation: dict[str, Any], excluded: set[str]) -> str:
    payload = {
        key: value
        for key, value in simulation.items()
        if key not in excluded and key not in {"output_file"}
    }
    return _canonical_json_sha256(payload)


def _axis_value(point: dict[str, Any], axis: str) -> Any:
    if axis == "kpoint_grid":
        return point.get("kpoint_grid")
    return point.get(axis)


def _refinement_sort_key(point: dict[str, Any], axis: str) -> tuple[Any, ...]:
    value = _axis_value(point, axis)
    if axis in {"kpoint_separation", "properties_kpoint_separation"}:
        return (-float(value), int(point["target_revision"]))
    if axis == "kpoint_grid":
        grid = tuple(int(item) for item in value)
        return (math.prod(grid), grid, int(point["target_revision"]))
    return (float(value), int(point["target_revision"]))


def _refinement_direction(axis: str) -> str:
    if axis in {"kpoint_separation", "properties_kpoint_separation"}:
        return "decreasing_is_finer"
    if axis == "kpoint_grid":
        return "increasing_grid_density_is_finer"
    return "increasing_is_finer"


def _series_delta(
    coarse: dict[str, Any],
    fine: dict[str, Any],
    *,
    axis: str,
    energy_tolerance_ev_per_atom: float,
    band_gap_tolerance_ev: float,
) -> dict[str, Any]:
    refinement_verified = _axis_refinement_verified(coarse, fine, axis)
    coarse_energy = _finite_float(coarse.get("total_energy_ev_per_atom"))
    fine_energy = _finite_float(fine.get("total_energy_ev_per_atom"))
    energy_delta = (
        abs(fine_energy - coarse_energy)
        if coarse_energy is not None and fine_energy is not None
        else None
    )
    coarse_gap = _finite_float(coarse.get("band_gap_ev"))
    fine_gap = _finite_float(fine.get("band_gap_ev"))
    band_gap_delta = (
        abs(fine_gap - coarse_gap)
        if coarse_gap is not None and fine_gap is not None
        else None
    )
    energy_within = (
        energy_delta <= energy_tolerance_ev_per_atom
        if energy_delta is not None
        else None
    )
    band_gap_within = (
        band_gap_delta <= band_gap_tolerance_ev
        if band_gap_delta is not None
        else None
    )
    available_checks = [
        value for value in (energy_within, band_gap_within) if value is not None
    ]
    return {
        "axis": axis,
        "coarse_revision": coarse.get("target_revision"),
        "fine_revision": fine.get("target_revision"),
        "coarse_axis_value": _axis_value(coarse, axis),
        "fine_axis_value": _axis_value(fine, axis),
        "refinement_verified": refinement_verified,
        "total_energy_delta_ev_per_atom": energy_delta,
        "energy_tolerance_ev_per_atom": energy_tolerance_ev_per_atom,
        "energy_within_tolerance": energy_within,
        "band_gap_delta_ev": band_gap_delta,
        "band_gap_tolerance_ev": band_gap_tolerance_ev,
        "band_gap_within_tolerance": band_gap_within,
        "available_metric_count": len(available_checks),
        "all_available_metrics_within_tolerance": (
            all(available_checks) if available_checks else None
        ),
        "scientific_convergence_verified": False,
    }


def _axis_refinement_verified(
    coarse: dict[str, Any],
    fine: dict[str, Any],
    axis: str,
) -> bool:
    coarse_value = _axis_value(coarse, axis)
    fine_value = _axis_value(fine, axis)
    if axis in {"kpoint_separation", "properties_kpoint_separation"}:
        return float(fine_value) < float(coarse_value)
    if axis == "kpoint_grid":
        coarse_grid = tuple(int(item) for item in coarse_value)
        fine_grid = tuple(int(item) for item in fine_value)
        paired = tuple(zip(coarse_grid, fine_grid))
        return all(
            fine_item >= coarse_item for coarse_item, fine_item in paired
        ) and any(
            fine_item > coarse_item for coarse_item, fine_item in paired
        )
    return float(fine_value) > float(coarse_value)


def _select_active_series(
    series: list[dict[str, Any]],
    latest_verified_revision: int | None,
) -> dict[str, Any] | None:
    if not series:
        return None
    status_priority = {
        "parameter_sensitivity_within_tolerance": 0,
        "pairwise_evidence_only": 1,
        "parameter_sensitivity_above_tolerance": 2,
    }
    return max(
        series,
        key=lambda item: (
            status_priority.get(str(item.get("status")), -1),
            latest_verified_revision in set(item.get("target_revisions") or []),
            int(item.get("point_count") or 0),
            max(item.get("target_revisions") or [-1]),
            -(
                "cutoff_energy_ev",
                "kpoint_separation",
                "kpoint_grid",
                "properties_kpoint_separation",
            ).index(str(item.get("axis"))),
        ),
    )


def _next_action(
    current_spec: ModelSpec,
    *,
    status: str,
    active_series: dict[str, Any] | None,
    binding_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    if binding_errors:
        return {
            "recommended_action_id": "review_convergence_history_binding",
            "recommended_action": (
                "restore_or_recreate_missing_castep_result_evidence_before_rerun"
            ),
            "recommended_tool": None,
            "recommended_preview_payload": None,
            "payload_hint_is_directly_callable": False,
            "preview_safe": True,
            "execute_requires_explicit_confirmation": True,
        }
    if status == "parameter_sensitivity_within_tolerance":
        return {
            "recommended_action_id": "review_parameter_sensitivity_evidence",
            "recommended_action": (
                "review_scf_and_method_convergence_before_scientific_use"
            ),
            "recommended_tool": None,
            "recommended_preview_payload": None,
            "payload_hint_is_directly_callable": False,
            "preview_safe": True,
            "execute_requires_explicit_confirmation": True,
        }
    if active_series:
        latest_point = active_series["points"][-1]
        payload = _refined_preview_payload(
            current_spec,
            latest_point,
            axis=str(active_series["axis"]),
        )
        if payload:
            return {
                "recommended_action_id": "preview_refined_castep_parameter_point",
                "recommended_action": "preview_one_finer_parameter_point",
                "recommended_tool": "material_studio_castep_run_current",
                "recommended_preview_payload": payload,
                "payload_hint_is_directly_callable": True,
                "preview_safe": True,
                "execute_requires_explicit_confirmation": True,
            }
    return {
        "recommended_action_id": "define_comparable_castep_parameter_series",
        "recommended_action": (
            "choose_one_explicit_cutoff_or_kpoint_axis_and_preview_a_refined_point"
        ),
        "recommended_tool": "material_studio_castep_run_current",
        "recommended_preview_payload": None,
        "payload_hint_is_directly_callable": False,
        "preview_safe": True,
        "execute_requires_explicit_confirmation": True,
    }


def _refined_preview_payload(
    current_spec: ModelSpec,
    point: dict[str, Any],
    *,
    axis: str,
) -> dict[str, Any] | None:
    simulation = point.get("simulation")
    if not isinstance(simulation, dict):
        return None
    payload = {
        key: simulation.get(key)
        for key in _ELECTRONIC_PAYLOAD_FIELDS
        if simulation.get(key) is not None
    }
    if axis == "cutoff_energy_ev":
        current = _finite_float(point.get("cutoff_energy_ev"))
        if current is None:
            return None
        increment = max(50, int(math.ceil((current * 0.1) / 10.0) * 10))
        payload[axis] = int(round(current)) + increment
    elif axis in {"kpoint_separation", "properties_kpoint_separation"}:
        current = _finite_float(point.get(axis))
        if current is None:
            return None
        payload[axis] = round(current * 0.8, 8)
    elif axis == "kpoint_grid":
        grid = point.get("kpoint_grid")
        if not isinstance(grid, list) or len(grid) != 3:
            return None
        payload["kpoints"] = [
            int(value) if int(value) == 1 else int(value) + 1 for value in grid
        ]
    else:
        return None
    payload.update(
        {
            "project_id": current_spec.project_id,
            "execution_mode": "preview",
            "open_in_gui": False,
            "take_snapshot": False,
            "export_view_audit": True,
        }
    )
    return payload


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
