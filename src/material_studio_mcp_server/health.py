"""Consolidated modeling health verdicts for live workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _materials_studio_roundtrip_audit(response: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the persisted or in-band revision round-trip receipt."""

    direct = response.get("materials_studio_roundtrip_audit")
    if isinstance(direct, dict):
        return direct
    result = response.get("result")
    if isinstance(result, dict) and isinstance(
        result.get("materials_studio_roundtrip_audit"), dict
    ):
        return result["materials_studio_roundtrip_audit"]
    metadata = response.get("result_metadata")
    if isinstance(metadata, dict) and isinstance(
        metadata.get("materials_studio_roundtrip_audit"), dict
    ):
        return metadata["materials_studio_roundtrip_audit"]
    report = response.get("modeling_report")
    if isinstance(report, dict) and isinstance(
        report.get("materials_studio_roundtrip_audit"), dict
    ):
        return report["materials_studio_roundtrip_audit"]
    return None


def _verified_live_status_hotload_evidence(
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """Accept only fresh, revision-bound observation evidence from the server."""

    evidence = response.get("live_status_hotload_evidence")
    gui_status = response.get("gui_status")
    if not isinstance(evidence, dict) or not isinstance(gui_status, dict):
        return None
    if evidence.get("schema_version") != "material_studio_live_status_hotload_evidence_v1":
        return None
    if evidence.get("verified") is not True:
        return None
    if evidence.get("status") != "verified_current_revision_loaded":
        return None
    if evidence.get("blocking_reasons"):
        return None
    if (
        "loaded_revision_verified" in evidence
        and evidence.get("loaded_revision_verified") is not True
    ):
        return None
    if evidence.get("binding_blocking_reasons"):
        return None
    if evidence.get("result_success") is not True:
        return None
    if evidence.get("observation_only") is not True:
        return None
    if any(
        evidence.get(key) is not False
        for key in (
            "gui_input_performed",
            "structure_reopened",
            "gui_process_launched",
        )
    ):
        return None
    try:
        if int(evidence.get("process_count")) != 1:
            return None
        if int(evidence.get("window_count")) != 1:
            return None
    except (TypeError, ValueError):
        return None
    if not evidence.get("target_window_handle") or not evidence.get(
        "target_window_title"
    ):
        return None

    expected_project_id = response.get("project_id")
    if str(evidence.get("project_id") or "") != str(expected_project_id or ""):
        return None
    expected_revision = response.get("new_revision", response.get("revision"))
    try:
        if int(evidence.get("revision")) != int(expected_revision):
            return None
    except (TypeError, ValueError):
        return None
    expected_structure = (response.get("planned_outputs") or {}).get("structure")
    if not expected_structure or not _same_path(
        evidence.get("structure_path"), expected_structure
    ):
        return None
    return evidence


def build_modeling_health(response: dict[str, Any], *, execution_mode: str) -> dict[str, Any]:
    """Build a stable verdict from validation, execution, GUI, and audit fields."""

    checks: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    verdict_warnings: list[str] = []

    validation = response.get("validation") or response.get("script_validation")
    if isinstance(validation, dict):
        script_valid = bool(validation.get("valid", False))
        checks["script_valid"] = script_valid
        if not script_valid:
            errors.append("Generated or saved script did not pass validation.")
        errors.extend(str(item) for item in validation.get("errors", []) or [])
        validation_warnings = [str(item) for item in validation.get("warnings", []) or []]
        warnings.extend(validation_warnings)
        verdict_warnings.extend(validation_warnings)

    audit = response.get("view_audit")
    if isinstance(audit, dict):
        audit_health = audit.get("health") or {}
        model_health_ok = bool(audit_health.get("ok", False))
        checks["model_health_ok"] = model_health_ok
        if not model_health_ok:
            errors.extend(str(item) for item in audit_health.get("errors", []) or ["Model health checks failed."])
        model_warnings = [str(item) for item in audit_health.get("warnings", []) or []]
        warnings.extend(model_warnings)
        verdict_warnings.extend(model_warnings)
        view_warnings = []
        for view in audit.get("views", []) or []:
            for warning in (view.get("health") or {}).get("warnings", []) or []:
                view_warnings.append(f"{view.get('name', 'view')}: {warning}")
        if view_warnings:
            warnings.extend(view_warnings)
        checks["view_warning_count"] = len(view_warnings)
        checks["view_count"] = len(audit.get("views", []) or [])
        semiconductor_warnings = _semiconductor_health_warnings(audit_health.get("semiconductor_health"), checks)
        if semiconductor_warnings:
            warnings.extend(semiconductor_warnings)
            verdict_warnings.extend(semiconductor_warnings)

    planned_structure = (response.get("planned_outputs") or {}).get("structure")
    if planned_structure:
        output_exists = Path(str(planned_structure)).expanduser().exists()
        checks["planned_structure_exists"] = output_exists
    else:
        output_exists = None

    artifact_validation = response.get("structure_artifact_validation")
    if isinstance(artifact_validation, dict) and artifact_validation.get("applicable"):
        artifact_status = artifact_validation.get("status")
        artifact_ok = artifact_validation.get("ok")
        checks["structure_artifact_validation_available"] = True
        checks["structure_artifact_validation_status"] = artifact_status
        checks["structure_artifact_validation_ok"] = artifact_ok
        checks["structure_artifact_validation_required"] = artifact_validation.get("required")
        checks["structure_artifact_sha256"] = artifact_validation.get("sha256")
        checks["structure_artifact_atom_count_matches"] = artifact_validation.get("atom_count_matches")
        checks["structure_artifact_element_counts_match"] = artifact_validation.get("element_counts_match")
        checks["structure_artifact_atom_ids_match"] = artifact_validation.get("atom_ids_match")
        checks["structure_artifact_atom_elements_match"] = artifact_validation.get("atom_elements_match")
        checks["structure_artifact_fractional_coordinates_match"] = artifact_validation.get(
            "fractional_coordinates_match"
        )
        checks["structure_artifact_lattice_matches"] = artifact_validation.get("lattice_matches")
        checks["structure_artifact_max_fractional_delta"] = artifact_validation.get("max_fractional_delta")
        checks["structure_artifact_max_lattice_delta"] = artifact_validation.get("max_lattice_delta")
        if artifact_ok is False and artifact_status not in {"not_materialized", "not_planned"}:
            artifact_errors = [str(item) for item in artifact_validation.get("errors", []) or []]
            errors.extend(artifact_errors or ["Materialized structure artifact failed ModelSpec consistency validation."])
    else:
        checks["structure_artifact_validation_available"] = False

    roundtrip_requested = bool(
        response.get("materials_studio_roundtrip_audit_requested")
    )
    roundtrip = _materials_studio_roundtrip_audit(response)
    if roundtrip is not None:
        roundtrip_requested = True
    if roundtrip_requested:
        checks["materials_studio_roundtrip_audit_requested"] = True
        checks["materials_studio_roundtrip_audit_status"] = (
            roundtrip.get("status") if roundtrip else None
        )
        checks["materials_studio_roundtrip_audit_ok"] = (
            roundtrip.get("ok") if roundtrip else None
        )
        checks["materials_studio_roundtrip_real_materials_studio_status"] = (
            roundtrip.get("real_materials_studio_status") if roundtrip else None
        )
        checks["materials_studio_roundtrip_source_unchanged"] = (
            roundtrip.get("source_unchanged") if roundtrip else None
        )
        checks["materials_studio_roundtrip_source_matches_plan"] = (
            roundtrip.get("source_sha256_planned")
            == roundtrip.get("source_sha256_before")
            if roundtrip and roundtrip.get("source_sha256_planned")
            else None
        )
        checks["materials_studio_roundtrip_output_confined"] = (
            roundtrip.get("output_confined") if roundtrip else None
        )
        checks["materials_studio_roundtrip_runner_script_confined"] = (
            roundtrip.get("runner_script_confined") if roundtrip else None
        )
        checks["materials_studio_roundtrip_gui_invariant_passed"] = (
            (roundtrip.get("gui_invariant") or {}).get("passed")
            if roundtrip
            else None
        )
        comparison = roundtrip.get("comparison") if roundtrip else None
        checks["materials_studio_roundtrip_comparison_passed"] = (
            comparison.get("passed") if isinstance(comparison, dict) else None
        )
        checks["materials_studio_roundtrip_scientific_correctness_established"] = (
            roundtrip.get("scientific_correctness_established")
            if roundtrip
            else False
        )
        if execution_mode == "execute":
            if roundtrip is None:
                errors.append(
                    "A requested Materials Studio round-trip audit receipt is missing."
                )
            elif roundtrip.get("applicable") is not False and (
                roundtrip.get("ok") is not True
                or roundtrip.get("status") not in {"passed", "not_applicable"}
            ):
                errors.extend(
                    str(item)
                    for item in roundtrip.get("errors", []) or []
                )
                if not roundtrip.get("errors"):
                    errors.append("Materials Studio round-trip audit failed.")
            if (
                roundtrip is not None
                and roundtrip.get("applicable") is not False
                and roundtrip.get("real_materials_studio_status") != "PASS"
            ):
                warning = (
                    "Round-trip structural evidence does not establish real Materials "
                    "Studio 20.1 execution."
                )
                warnings.append(warning)
                verdict_warnings.append(warning)
        elif roundtrip is not None and roundtrip.get("status") == "blocked":
            errors.extend(str(item) for item in roundtrip.get("errors", []) or [])

    if execution_mode == "execute":
        result = response.get("result")
        runner_success = bool(result.get("success", False)) if isinstance(result, dict) else False
        checks["runner_success"] = runner_success
        if not runner_success:
            errors.append("Materials Studio runner did not report success.")
        if output_exists is False:
            errors.append(f"Planned output structure was not found: {planned_structure}")

        gui_open = response.get("gui_open")
        live_status_hotload = _verified_live_status_hotload_evidence(response)
        checks["gui_hot_loaded_from_live_status"] = bool(live_status_hotload)
        checks["gui_loaded_revision_verified_from_live_status"] = bool(
            live_status_hotload
        )
        if live_status_hotload is not None:
            checks["gui_interaction_ready_from_live_status"] = (
                live_status_hotload.get("interaction_ready")
            )
            checks["gui_interaction_status_from_live_status"] = (
                live_status_hotload.get("interaction_status")
            )
            checks["gui_interaction_blocking_reasons_from_live_status"] = list(
                live_status_hotload.get("interaction_blocking_reasons") or []
            )
            checks[
                "gui_activation_required_before_capture_or_input"
            ] = live_status_hotload.get(
                "activation_required_before_capture_or_input"
            )
        if isinstance(gui_open, dict):
            checks["gui_hotload_evidence_source"] = "gui_open_artifact"
            external_visual_confirmation_ok = _external_visual_confirmation_ok(response)
            checks["external_visual_confirmation_ok"] = external_visual_confirmation_ok
            checks["gui_opened"] = bool(gui_open.get("window"))
            if "activated_opened_window" in gui_open:
                activated_opened_window = bool(gui_open.get("activated_opened_window"))
                checks["gui_activated_opened_window"] = activated_opened_window
                current_foreground_verified = _gui_foreground_current_revision_verified(response)
                checks["gui_foreground_current_revision_verified"] = current_foreground_verified
                if not activated_opened_window and not current_foreground_verified:
                    warning = "GUI opened the structure, but the opened Materials Studio window was not confirmed active."
                    warnings.append(warning)
                    verdict_warnings.append(warning)
            gui_stale_reasons = _gui_stale_reasons(response, gui_open)
            checks["gui_loaded_current_revision"] = not gui_stale_reasons
            checks["gui_stale_reasons"] = gui_stale_reasons
            checks.update(_gui_identity_checks(response, gui_open, gui_stale_reasons))
            if gui_stale_reasons:
                warning = "GUI open artifact does not match the current revision: " + ", ".join(gui_stale_reasons)
                warnings.append(warning)
                verdict_warnings.append(warning)
            snapshot = gui_open.get("snapshot") or {}
            analysis = snapshot.get("analysis") or {}
            if analysis:
                checks["snapshot_readable"] = bool(analysis.get("readable"))
                checks["snapshot_likely_nonblank"] = bool(analysis.get("likely_nonblank"))
                checks["snapshot_unique_sampled_colors"] = analysis.get("unique_sampled_colors")
                checks["snapshot_viewport_likely_visible_model"] = analysis.get("viewport_likely_visible_model")
                checks["snapshot_viewport_foreground_ratio"] = analysis.get("viewport_foreground_ratio")
                checks["snapshot_viewport_background_ratio"] = analysis.get("viewport_background_ratio")
                checks["snapshot_viewport_capture_limitation_possible"] = analysis.get(
                    "viewport_capture_limitation_possible"
                )
                checks["snapshot_viewport_capture_diagnostic"] = analysis.get("viewport_capture_diagnostic")
                if not analysis.get("readable", False):
                    errors.append(f"GUI snapshot was not readable: {analysis.get('warning', 'unknown error')}")
                if analysis.get("readable") and not analysis.get("likely_nonblank", False):
                    errors.append("GUI snapshot appears blank or visually uninformative.")
                if analysis.get("viewport_likely_visible_model") is False:
                    warning = "GUI snapshot viewport does not show a visible model."
                    warnings.append(warning)
                    if not external_visual_confirmation_ok:
                        verdict_warnings.append(warning)
                if analysis.get("viewport_capture_limitation_possible") is True:
                    warning = "GUI snapshot may be limited by GDI/OpenGL viewport capture."
                    warnings.append(warning)
                    if not external_visual_confirmation_ok:
                        verdict_warnings.append(warning)
                snapshot_warnings = [str(item) for item in analysis.get("warnings", []) or []]
                warnings.extend(snapshot_warnings)
                if not external_visual_confirmation_ok:
                    verdict_warnings.extend(snapshot_warnings)
            else:
                warning = "GUI was opened but no snapshot analysis is available."
                warnings.append(warning)
                verdict_warnings.append(warning)
        elif response.get("gui_open_warning"):
            errors.append(str(response["gui_open_warning"]))
        elif live_status_hotload is not None:
            checks.update(
                {
                    "gui_opened": True,
                    "gui_loaded_current_revision": True,
                    "gui_stale_reasons": [],
                    "gui_hotload_evidence_source": "live_status_current_revision",
                    "gui_live_status_process_count": live_status_hotload.get(
                        "process_count"
                    ),
                    "gui_live_status_window_count": live_status_hotload.get(
                        "window_count"
                    ),
                    "gui_live_status_target_window_handle": (
                        live_status_hotload.get("target_window_handle")
                    ),
                    "gui_live_status_target_window_title": (
                        live_status_hotload.get("target_window_title")
                    ),
                    "gui_input_performed_by_current_request": False,
                    "gui_structure_reopened_by_current_request": False,
                    "gui_process_launched_by_current_request": False,
                }
            )
        else:
            checks["gui_opened"] = False
            warning = "GUI hot-load was not performed or no GUI open result was returned."
            warnings.append(warning)
            verdict_warnings.append(warning)
    else:
        checks["runner_success"] = None
        checks["gui_opened"] = None

    response_warnings = [str(item) for item in response.get("warnings", []) or []]
    warnings.extend(response_warnings)
    verdict_warnings.extend(response_warnings)
    if response.get("ok") is False and response.get("error"):
        errors.append(str(response["error"]))
    warnings = _dedupe(warnings)
    verdict_warnings = _dedupe(verdict_warnings)
    errors = _dedupe(errors)
    checks["verdict_warning_count"] = len(verdict_warnings)

    if errors:
        verdict = "failed"
        next_action = "Inspect errors, fix the model/spec/script, then regenerate and re-run preview or execute."
    elif execution_mode == "execute":
        verdict = "passed_with_warnings" if verdict_warnings else "passed"
        next_action = "Inspect the GUI snapshot and view_audit.json, then continue with the next modeling change."
    else:
        verdict = "ready_for_review" if not verdict_warnings else "ready_with_warnings"
        next_action = "Review the generated script and view_audit.json; execute only if live hot-loading is intended."

    return {
        "verdict": verdict,
        "ok": not errors,
        "execution_mode": execution_mode,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "verdict_warnings": verdict_warnings,
        "next_action": next_action,
    }


def _external_visual_confirmation_ok(response: dict[str, Any]) -> bool:
    direct = response.get("gui_visual_confirmation")
    if isinstance(direct, dict) and direct.get("model_visible") is True:
        return True
    for artifact in response.get("gui_artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("type") == "visual_confirmation" and artifact.get("model_visible") is True:
            return True
    return False


def _gui_stale_reasons(response: dict[str, Any], gui_open: dict[str, Any]) -> list[str]:
    reasons = []
    expected_revision = response.get("new_revision", response.get("revision"))
    opened_revision = gui_open.get("revision")
    if expected_revision is not None and opened_revision is not None and int(expected_revision) != int(opened_revision):
        reasons.append("opened_revision_does_not_match_current_revision")

    expected_project_id = response.get("project_id")
    opened_project_id = gui_open.get("project_id")
    if expected_project_id and opened_project_id and str(expected_project_id) != str(opened_project_id):
        reasons.append("opened_project_does_not_match_current_project")

    expected_structure = (response.get("planned_outputs") or {}).get("structure")
    opened_structure = gui_open.get("structure_path")
    if expected_structure and opened_structure and not _structure_path_matches_current(response, opened_structure, expected_structure):
        reasons.append("opened_structure_does_not_match_planned_structure")
    return reasons


def _gui_identity_checks(response: dict[str, Any], gui_open: dict[str, Any], gui_stale_reasons: list[str]) -> dict[str, Any]:
    wrapper = gui_open.get("project_wrapper") if isinstance(gui_open.get("project_wrapper"), dict) else {}
    expected_structure = (response.get("planned_outputs") or {}).get("structure")
    wrapper_source = wrapper.get("source_path")
    wrapper_structure_matches = None
    if wrapper_source and expected_structure:
        wrapper_structure_matches = _structure_path_matches_current(response, wrapper_source, expected_structure)

    open_stale_reasons = list(gui_stale_reasons)
    if wrapper_structure_matches is False:
        open_stale_reasons.append("project_wrapper_source_does_not_match_planned_structure")

    if open_stale_reasons:
        open_identity = "mismatched"
    elif wrapper and wrapper_structure_matches is not False:
        open_identity = "verified_project_wrapper"
    elif gui_open:
        open_identity = "matched_open_artifact"
    else:
        open_identity = "unverified"

    selected_identity = _gui_status_window_identity(response, _selected_gui_status_window(response))
    foreground_identity = _gui_status_window_identity(response, _foreground_gui_status_window(response))
    return {
        "gui_open_identity_verification": open_identity,
        "gui_open_identity_uses_project_wrapper": bool(wrapper),
        "gui_open_identity_project_wrapper_matches_structure": wrapper_structure_matches,
        "gui_selected_window_identity_verification": selected_identity,
        "gui_foreground_window_identity_verification": foreground_identity,
        "gui_window_identity_verification": _combined_gui_window_identity(selected_identity, foreground_identity),
    }


def _gui_foreground_current_revision_verified(response: dict[str, Any]) -> bool:
    """Return True when GUI status proves the foreground window is current."""

    return _gui_status_window_identity(response, _foreground_gui_status_window(response)) == "verified"


def _selected_gui_status_window(response: dict[str, Any]) -> dict[str, Any] | None:
    gui_status = response.get("gui_status") if isinstance(response.get("gui_status"), dict) else {}
    windows = gui_status.get("windows")
    selected_handle = gui_status.get("selected_window_handle")
    if isinstance(windows, list):
        for window in windows:
            if isinstance(window, dict) and window.get("is_selected"):
                return window
        if selected_handle is not None:
            for window in windows:
                if isinstance(window, dict) and window.get("handle") == selected_handle:
                    return window
    window = gui_status.get("window")
    return window if isinstance(window, dict) else None


def _foreground_gui_status_window(response: dict[str, Any]) -> dict[str, Any] | None:
    gui_status = response.get("gui_status") if isinstance(response.get("gui_status"), dict) else {}
    windows = gui_status.get("windows")
    if isinstance(windows, list):
        for window in windows:
            if isinstance(window, dict) and window.get("is_foreground"):
                return window
    return _selected_gui_status_window(response)


def _gui_status_window_identity(response: dict[str, Any], window: dict[str, Any] | None) -> str | None:
    gui_status = response.get("gui_status") if isinstance(response.get("gui_status"), dict) else {}
    status_probed = "window_found" in gui_status or "supported" in gui_status
    if not status_probed:
        return None
    if gui_status.get("supported") is False or gui_status.get("window_found") is False:
        return "no_window"
    if not isinstance(window, dict) or not window:
        return "no_window"

    metadata = window.get("project_wrapper_metadata") if isinstance(window.get("project_wrapper_metadata"), dict) else {}
    window_project_id = window.get("project_id") or metadata.get("project_id")
    window_revision = window.get("revision") if window.get("revision") is not None else metadata.get("revision")
    window_structure = window.get("source_path") or metadata.get("source_path")
    if not any(value is not None for value in (window_project_id, window_revision, window_structure)):
        return "unverified"

    expected_project_id = response.get("project_id")
    expected_revision = response.get("new_revision", response.get("revision"))
    expected_structure = (response.get("planned_outputs") or {}).get("structure")
    if window_project_id and expected_project_id and str(window_project_id) != str(expected_project_id):
        return "mismatched"
    if window_revision is not None and expected_revision is not None:
        try:
            if int(window_revision) != int(expected_revision):
                return "mismatched"
        except (TypeError, ValueError):
            if str(window_revision) != str(expected_revision):
                return "mismatched"
    if window_structure and expected_structure and not _structure_path_matches_current(response, window_structure, expected_structure):
        return "mismatched"
    return "verified"


def _combined_gui_window_identity(selected_identity: str | None, foreground_identity: str | None) -> str | None:
    values = [value for value in (selected_identity, foreground_identity) if value is not None]
    if not values:
        return None
    if "mismatched" in values:
        return "mismatched"
    if "unverified" in values:
        return "unverified"
    if all(value == "verified" for value in values):
        return "verified"
    if all(value == "no_window" for value in values):
        return "no_window"
    if "verified" in values and "no_window" in values:
        return "verified"
    return "unknown"


def _same_path(left: Any, right: Any) -> bool:
    try:
        return Path(str(left)).expanduser().resolve() == Path(str(right)).expanduser().resolve()
    except Exception:
        return str(left) == str(right)


def _structure_path_matches_current(response: dict[str, Any], candidate: Any, expected: Any) -> bool:
    """Return True for the planned structure or trusted same-revision GUI derivatives."""

    if _same_path(candidate, expected):
        return True
    return _same_revision_structure_derivative(candidate, expected, response.get("new_revision", response.get("revision")))


def _same_revision_structure_derivative(candidate: Any, expected: Any, revision: Any) -> bool:
    try:
        candidate_path = Path(str(candidate)).expanduser().resolve()
        expected_path = Path(str(expected)).expanduser().resolve()
    except Exception:
        return False

    if candidate_path.parent != expected_path.parent:
        return False

    expected_stem = expected_path.stem
    candidate_stem = candidate_path.stem
    if not expected_stem or not candidate_stem:
        return False

    if revision is not None:
        try:
            revision_tag = f"r{int(revision):03d}"
        except (TypeError, ValueError):
            revision_tag = f"r{revision}"
        if revision_tag not in expected_stem or revision_tag not in candidate_stem:
            return False

    if candidate_stem == expected_stem:
        return True
    if candidate_stem == f"{expected_stem}_visual_bonded":
        return True
    if candidate_stem.startswith(f"{expected_stem}_msimport"):
        return True
    return False


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _semiconductor_health_warnings(semiconductor: Any, checks: dict[str, Any]) -> list[str]:
    """Promote semiconductor-specific diagnostics to stable health checks."""

    if not isinstance(semiconductor, dict) or not semiconductor:
        checks["semiconductor_health_available"] = False
        return []

    checks["semiconductor_health_available"] = True
    checks["semiconductor_rule"] = semiconductor.get("rule")
    checks["semiconductor_expected_coordination"] = semiconductor.get("expected_coordination")
    checks["semiconductor_expected_coordination_by_element"] = semiconductor.get("expected_coordination_by_element")
    checks["semiconductor_unexpected_neighbor_pair_count"] = semiconductor.get("unexpected_neighbor_pair_count", 0)
    checks["semiconductor_alloy_same_sublattice_neighbor_pair_count"] = semiconductor.get(
        "alloy_same_sublattice_neighbor_pair_count",
        0,
    )
    checks["semiconductor_same_sublattice_cutoff_artifact_pair_count"] = semiconductor.get(
        "same_sublattice_cutoff_artifact_pair_count",
        0,
    )
    checks["semiconductor_coordination_excluded_neighbor_pair_count"] = semiconductor.get(
        "coordination_excluded_neighbor_pair_count",
        0,
    )
    checks["semiconductor_coordination_excluded_pair_types"] = semiconductor.get(
        "coordination_excluded_pair_types",
        [],
    )
    checks["semiconductor_coordination_outlier_count"] = semiconductor.get("coordination_outlier_count", 0)

    warnings: list[str] = []
    composition = semiconductor.get("composition_summary") or {}
    if composition:
        checks["semiconductor_formula"] = composition.get("formula")
        checks["semiconductor_reduced_formula"] = composition.get("reduced_formula")
        checks["semiconductor_element_count"] = composition.get("element_count")
        checks["semiconductor_total_atom_count"] = composition.get("total_atom_count")
        checks["semiconductor_non_passivant_atom_count"] = composition.get("non_passivant_atom_count")

    charge_balance = semiconductor.get("charge_balance_summary") or {}
    if charge_balance:
        checks["semiconductor_total_valence_electron_count"] = charge_balance.get("total_valence_electron_count")
        checks["semiconductor_valence_electrons_per_non_passivant_atom"] = charge_balance.get("valence_electrons_per_non_passivant_atom")
        checks["semiconductor_electron_count_parity"] = charge_balance.get("electron_count_parity")
        checks["semiconductor_odd_electron_warning"] = charge_balance.get("odd_electron_warning")
        checks["semiconductor_spin_charge_review_required"] = charge_balance.get("spin_charge_review_required")
        checks["semiconductor_spin_polarization_review_required"] = charge_balance.get(
            "spin_polarization_review_required"
        )
        checks["semiconductor_defect_charge_state_label"] = charge_balance.get(
            "defect_charge_state_label"
        )
        checks["semiconductor_defect_charge_state_explicit"] = charge_balance.get(
            "defect_charge_state_explicit"
        )
        checks["semiconductor_defect_charge_state_unresolved"] = charge_balance.get(
            "defect_charge_state_unresolved"
        )
        checks["semiconductor_requested_net_charge_e"] = charge_balance.get(
            "requested_net_charge_e"
        )
        checks["semiconductor_reference_spin_multiplicity"] = charge_balance.get(
            "reference_spin_multiplicity"
        )
        checks["semiconductor_charge_adjusted_valence_electron_count"] = (
            charge_balance.get("charge_adjusted_valence_electron_count")
        )
        checks["semiconductor_charge_adjusted_electron_count_parity"] = (
            charge_balance.get("charge_adjusted_electron_count_parity")
        )
        checks["semiconductor_charge_spin_backend_binding_ready"] = (
            charge_balance.get("charge_spin_backend_binding_ready")
        )
        checks["semiconductor_backend_charge_binding_status"] = (
            charge_balance.get("backend_charge_binding_status")
        )
        checks["semiconductor_backend_spin_binding_status"] = (
            charge_balance.get("backend_spin_binding_status")
        )
        checks["semiconductor_recommended_spin_treatment"] = charge_balance.get("recommended_spin_treatment")
        checks["semiconductor_charge_balance_next_action"] = charge_balance.get("next_action")
        checks["semiconductor_nominal_dopant_delta_electrons"] = charge_balance.get("nominal_dopant_delta_electrons")
        checks["semiconductor_average_host_nominal_dopant_delta_electrons"] = charge_balance.get(
            "average_host_nominal_dopant_delta_electrons"
        )
        checks["semiconductor_site_adjusted_dopant_delta_electrons"] = charge_balance.get(
            "site_adjusted_dopant_delta_electrons"
        )
        checks["semiconductor_carrier_type_hint_source"] = charge_balance.get("carrier_type_hint_source")
        checks["semiconductor_carrier_type_hint"] = charge_balance.get("carrier_type_hint")
        if charge_balance.get("odd_electron_warning"):
            electron_basis = (
                "charge-adjusted nominal"
                if charge_balance.get("defect_charge_state_explicit") is True
                else "nominal"
            )
            warnings.append(
                f"Semiconductor {electron_basis} valence-electron count is odd; "
                "inspect charge_balance_summary before spin-sensitive calculations."
            )
        if charge_balance.get("charge_spin_backend_binding_ready") is False:
            warnings.append(
                "Semiconductor defect charge/spin request is not bound to the "
                "current CASTEP schema; calculation execution must remain blocked."
            )

    calculation = semiconductor.get("calculation_preflight_summary") or {}
    if calculation:
        checks["semiconductor_calculation_module"] = calculation.get("module")
        checks["semiconductor_calculation_task"] = calculation.get("task")
        checks["semiconductor_calculation_task_family"] = calculation.get("task_family")
        checks["semiconductor_calculation_task_intent"] = calculation.get("task_intent")
        checks["semiconductor_calculation_status"] = calculation.get("status")
        checks["semiconductor_calculation_ready_for_energy_preflight"] = calculation.get("ready_for_energy_preflight")
        checks["semiconductor_calculation_ready_for_requested_task_preflight"] = calculation.get("ready_for_requested_task_preflight")
        checks["semiconductor_calculation_changes_structure"] = calculation.get("changes_structure")
        checks["semiconductor_calculation_requires_prior_relaxed_structure"] = calculation.get("requires_prior_relaxed_structure")
        checks["semiconductor_calculation_settings_review_required"] = calculation.get("settings_review_required")
        checks["semiconductor_calculation_execution_risk"] = calculation.get("execution_risk")
        checks["semiconductor_calculation_next_action"] = calculation.get("next_action")
        checks["semiconductor_calculation_cutoff_energy_ev"] = calculation.get("cutoff_energy_ev")
        checks["semiconductor_calculation_cutoff_status"] = calculation.get("cutoff_status")
        checks["semiconductor_calculation_kpoint_mode"] = calculation.get("kpoint_mode")
        checks["semiconductor_calculation_kpoint_separation"] = calculation.get("kpoint_separation")
        checks["semiconductor_calculation_kpoints"] = calculation.get("kpoints")
        checks["semiconductor_calculation_warning_count"] = calculation.get("warning_count", 0)
        if int(calculation.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor calculation preflight has warnings; inspect calculation_preflight_summary before expensive calculations.")

    reciprocal = semiconductor.get("reciprocal_lattice_summary") or {}
    if reciprocal:
        checks["semiconductor_reciprocal_status"] = reciprocal.get("status")
        checks["semiconductor_reciprocal_lengths_1_per_angstrom"] = reciprocal.get("reciprocal_lengths_1_per_angstrom")
        checks["semiconductor_reciprocal_estimated_kpoints"] = reciprocal.get("estimated_kpoints_from_separation")
        checks["semiconductor_reciprocal_actual_separations_1_per_angstrom"] = reciprocal.get("actual_separations_1_per_angstrom")
        checks["semiconductor_reciprocal_recommended_kpoints"] = reciprocal.get("recommended_kpoints")
        checks["semiconductor_reciprocal_recommendation_reason_codes"] = reciprocal.get(
            "recommendation_reason_codes"
        )
        checks["semiconductor_reciprocal_warning_count"] = reciprocal.get("warning_count", 0)
        if int(reciprocal.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor reciprocal-lattice/k-point preflight has warnings; inspect reciprocal_lattice_summary.")

    band_path = semiconductor.get("band_path_summary") or {}
    if band_path:
        checks["semiconductor_band_path_available"] = band_path.get("available")
        checks["semiconductor_band_path_bravais_lattice"] = band_path.get("bravais_lattice")
        checks["semiconductor_band_path_label"] = band_path.get("path_label")
        checks["semiconductor_band_path_point_count"] = band_path.get("point_count")
        checks["semiconductor_band_path_segment_count"] = band_path.get("segment_count")
        checks["semiconductor_band_path_task_relevant"] = band_path.get("task_relevant")
        checks["semiconductor_band_path_requires_materials_studio_review"] = band_path.get("requires_materials_studio_review")
        checks["semiconductor_band_path_warning_count"] = band_path.get("warning_count", 0)
        if band_path.get("task_relevant") and int(band_path.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor band-path preflight has warnings; inspect band_path_summary before band-structure calculations.")

    lattice = semiconductor.get("lattice_summary") or {}
    if lattice:
        checks["semiconductor_lattice_a_angstrom"] = lattice.get("a_angstrom")
        checks["semiconductor_lattice_b_angstrom"] = lattice.get("b_angstrom")
        checks["semiconductor_lattice_c_angstrom"] = lattice.get("c_angstrom")
        checks["semiconductor_lattice_volume_angstrom3"] = lattice.get("cell_volume_angstrom3")
        checks["semiconductor_volume_per_non_passivant_atom_angstrom3"] = lattice.get("volume_per_non_passivant_atom_angstrom3")
        checks["semiconductor_non_passivant_atom_density_per_angstrom3"] = lattice.get("non_passivant_atom_density_per_angstrom3")
        checks["semiconductor_surface_vacuum_fraction"] = (
            lattice.get("declared_vacuum_fraction")
            if lattice.get("declared_vacuum_fraction") is not None
            else lattice.get("atom_extent_vacuum_fraction")
        )
        checks["semiconductor_surface_bottom_vacuum_angstrom"] = lattice.get("bottom_vacuum_angstrom")
        checks["semiconductor_surface_top_vacuum_angstrom"] = lattice.get("top_vacuum_angstrom")
        checks["semiconductor_surface_vacuum_asymmetry_abs_angstrom"] = lattice.get("vacuum_asymmetry_abs_angstrom")
        checks["semiconductor_slab_centered_in_cell"] = lattice.get("centered_in_cell")
        checks["semiconductor_slab_center_offset_angstrom"] = lattice.get("slab_center_offset_angstrom")
        checks["semiconductor_slab_vacuum_ok"] = lattice.get("vacuum_ok")
        checks["semiconductor_slab_vacuum_status"] = lattice.get("slab_vacuum_status")
        checks["semiconductor_slab_vacuum_next_action"] = lattice.get("slab_vacuum_next_action")
        if lattice.get("is_slab") and lattice.get("slab_vacuum_status") not in {None, "ready"}:
            warnings.append(
                "Semiconductor slab vacuum/centering preflight needs review; inspect slab_vacuum before calculation."
            )

    neighbor_distances = semiconductor.get("neighbor_distance_summary") or {}
    if neighbor_distances:
        distance_stats = neighbor_distances.get("distance_stats_angstrom") or {}
        checks["semiconductor_neighbor_pair_count"] = neighbor_distances.get("neighbor_pair_count")
        checks["semiconductor_neighbor_pair_type_count"] = neighbor_distances.get("pair_type_count")
        checks["semiconductor_neighbor_distance_min_angstrom"] = distance_stats.get("min")
        checks["semiconductor_neighbor_distance_mean_angstrom"] = distance_stats.get("mean")
        checks["semiconductor_neighbor_distance_max_angstrom"] = distance_stats.get("max")

    local_environment = semiconductor.get("local_environment_summary") or {}
    if local_environment:
        angle_stats = local_environment.get("angle_stats_deg") or {}
        tetrahedral_stats = local_environment.get("tetrahedral_angle_deviation_stats_deg") or {}
        checks["semiconductor_local_environment_atom_count"] = local_environment.get("atom_count")
        checks["semiconductor_local_environment_coordination_outlier_count"] = local_environment.get("coordination_outlier_count")
        checks["semiconductor_local_environment_angle_min_deg"] = angle_stats.get("min")
        checks["semiconductor_local_environment_angle_mean_deg"] = angle_stats.get("mean")
        checks["semiconductor_local_environment_angle_max_deg"] = angle_stats.get("max")
        checks["semiconductor_tetrahedral_angle_deviation_mean_deg"] = tetrahedral_stats.get("mean")
        checks["semiconductor_tetrahedral_angle_deviation_max_deg"] = tetrahedral_stats.get("max")

    sublattice = semiconductor.get("sublattice_balance_summary") or {}
    if sublattice:
        checks["semiconductor_sublattice_balance_kind"] = sublattice.get("balance_kind")
        checks["semiconductor_sublattice_balanced"] = sublattice.get("balanced")
        checks["semiconductor_sublattice_balance_delta_count"] = sublattice.get("balance_delta_count")
        checks["semiconductor_iii_v_cation_count"] = sublattice.get("iii_v_cation_count")
        checks["semiconductor_iii_v_anion_count"] = sublattice.get("iii_v_anion_count")
        checks["semiconductor_ii_vi_cation_count"] = sublattice.get("ii_vi_cation_count")
        checks["semiconductor_ii_vi_anion_count"] = sublattice.get("ii_vi_anion_count")
        checks["semiconductor_tmd_metal_count"] = sublattice.get("tmd_metal_count")
        checks["semiconductor_tmd_chalcogen_count"] = sublattice.get("tmd_chalcogen_count")
        if sublattice.get("warning"):
            warnings.append("Semiconductor sublattice balance is off; inspect sublattice_balance_summary.")

    defect_summary = semiconductor.get("defect_summary") or {}
    if defect_summary:
        checks["semiconductor_defect_count"] = defect_summary.get("defect_count", 0)
        checks["semiconductor_vacancy_count"] = defect_summary.get("vacancy_count", 0)
        checks["semiconductor_total_vacancy_fraction"] = defect_summary.get("total_vacancy_fraction")
        checks["semiconductor_interstitial_count"] = defect_summary.get("interstitial_count", 0)
        checks["semiconductor_total_interstitial_fraction"] = defect_summary.get("total_interstitial_fraction")
        checks["semiconductor_antisite_count"] = defect_summary.get("antisite_count", 0)
        checks["semiconductor_total_antisite_fraction"] = defect_summary.get("total_antisite_fraction")
        checks["semiconductor_defect_complex_count"] = defect_summary.get("complex_count", 0)
        checks["semiconductor_divacancy_count"] = defect_summary.get("divacancy_count", 0)
        checks["semiconductor_nitrogen_vacancy_count"] = defect_summary.get(
            "nitrogen_vacancy_count",
            0,
        )
        checks["semiconductor_defect_charge_state_unresolved_count"] = (
            defect_summary.get("defect_charge_state_unresolved_count", 0)
        )
        checks["semiconductor_defect_charge_spin_backend_unbound_count"] = (
            defect_summary.get(
                "defect_charge_spin_backend_unbound_count",
                0,
            )
        )
        checks["semiconductor_defect_complex_integrity_ok"] = defect_summary.get(
            "defect_complex_integrity_ok",
            True,
        )
        checks["semiconductor_defect_carrier_type_hint"] = defect_summary.get("carrier_type_hint")
        checks["semiconductor_defect_donor_like_count"] = defect_summary.get("donor_like_count", 0)
        checks["semiconductor_defect_acceptor_like_count"] = defect_summary.get("acceptor_like_count", 0)
        checks["semiconductor_defect_neutral_or_intrinsic_count"] = defect_summary.get("neutral_or_intrinsic_count", 0)
        checks["semiconductor_defect_unknown_count"] = defect_summary.get("unknown_count", 0)
        missing_bonds = sum(
            int(defect.get("missing_neighbor_bond_estimate") or 0)
            for defect in defect_summary.get("defects", []) or []
            if isinstance(defect, dict)
        )
        interstitial_outliers = sum(
            1
            for defect in defect_summary.get("defects", []) or []
            if isinstance(defect, dict)
            and str(defect.get("type") or "").lower() == "interstitial"
            and bool(defect.get("coordination_outlier"))
        )
        antisite_same_sublattice_neighbors = sum(
            int(defect.get("same_sublattice_neighbor_count") or 0)
            for defect in defect_summary.get("defects", []) or []
            if isinstance(defect, dict)
            and str(defect.get("type") or "").lower() == "antisite"
        )
        checks["semiconductor_defect_missing_neighbor_bond_estimate"] = missing_bonds
        checks["semiconductor_interstitial_coordination_outlier_count"] = interstitial_outliers
        checks["semiconductor_antisite_same_sublattice_neighbor_count"] = antisite_same_sublattice_neighbors
        if missing_bonds:
            warnings.append(
                "Semiconductor vacancy/defect leaves under-coordinated neighbors; inspect defect_summary before treating the model as relaxed."
            )
        if interstitial_outliers:
            warnings.append("Semiconductor interstitial coordination differs from the expected local rule; inspect defect_summary before calculation.")
        if antisite_same_sublattice_neighbors:
            warnings.append("Semiconductor antisite creates same-sublattice nearest neighbors; inspect defect_summary before calculation.")
        if int(defect_summary.get("complex_count") or 0):
            warnings.append(
                "Semiconductor defect complex is an unrelaxed structural starting point; review pair geometry, charge state, and finite-size effects before calculation."
            )
        if not defect_summary.get("defect_complex_integrity_ok", True):
            warnings.append(
                "Semiconductor defect-complex metadata failed structural consistency checks; inspect defect_summary before continuing."
            )

    dopant_summary = semiconductor.get("dopant_summary") or {}
    if dopant_summary:
        checks["semiconductor_total_dopant_fraction"] = dopant_summary.get("total_dopant_fraction")
        dopant_outliers = sum(
            int(dopant.get("coordination_outlier_count") or 0)
            for dopant in dopant_summary.get("dopants", []) or []
            if isinstance(dopant, dict)
        )
        checks["semiconductor_dopant_coordination_outlier_count"] = dopant_outliers
        if dopant_outliers:
            warnings.append("Semiconductor dopant coordination differs from the expected local rule; inspect dopant_summary.")

    dopant_concentration = semiconductor.get("dopant_concentration_summary") or {}
    if dopant_concentration:
        checks["semiconductor_total_dopant_density_cm3"] = dopant_concentration.get("total_dopant_density_cm3")
        checks["semiconductor_net_nominal_carrier_density_cm3_abs"] = dopant_concentration.get(
            "net_nominal_carrier_density_cm3_abs"
        )
        checks["semiconductor_dopant_concentration_warning_level"] = dopant_concentration.get(
            "concentration_warning_level"
        )
        checks["semiconductor_dopant_concentration_high_warning"] = dopant_concentration.get(
            "high_concentration_warning"
        )
        checks["semiconductor_degenerate_doping_review_required"] = dopant_concentration.get(
            "degenerate_doping_review_required"
        )
        checks["semiconductor_dopant_concentration_next_action"] = dopant_concentration.get("next_action")
        if dopant_concentration.get("high_concentration_warning"):
            warnings.append(
                "Semiconductor periodic-supercell dopant concentration is high; inspect dopant_concentration_summary before quantitative claims."
            )

    dopant_sites = semiconductor.get("dopant_site_summary") or {}
    if dopant_sites:
        checks["semiconductor_dopant_site_count"] = dopant_sites.get("site_count")
        checks["semiconductor_dopant_site_carrier_type_hint"] = dopant_sites.get("carrier_type_hint")
        checks["semiconductor_dopant_site_donor_like_count"] = dopant_sites.get("donor_like_count", 0)
        checks["semiconductor_dopant_site_acceptor_like_count"] = dopant_sites.get("acceptor_like_count", 0)
        checks["semiconductor_dopant_site_isovalent_count"] = dopant_sites.get("isovalent_count", 0)
        checks["semiconductor_dopant_site_unknown_count"] = dopant_sites.get("unknown_count", 0)
        checks["semiconductor_dopant_site_raw_count"] = dopant_sites.get("raw_site_count")
        checks["semiconductor_dopant_site_stale_count"] = dopant_sites.get("stale_site_count", 0)
        checks["semiconductor_dopant_site_metadata_consistent"] = dopant_sites.get("metadata_consistent")
        checks["semiconductor_dopant_site_error_count"] = dopant_sites.get("error_count", 0)
        checks["semiconductor_dopant_site_warning_count"] = dopant_sites.get("warning_count", 0)
        if int(dopant_sites.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor dopant-site role diagnostics have warnings; inspect dopant_site_summary.")
        warnings.extend(str(item) for item in dopant_sites.get("warnings", []) or [])

    carrier_intent = semiconductor.get("carrier_intent_summary") or {}
    if carrier_intent:
        checks["semiconductor_carrier_intent_count"] = carrier_intent.get("entry_count")
        checks["semiconductor_requested_carrier_type"] = carrier_intent.get("requested_carrier_type")
        checks["semiconductor_requested_carrier_mechanism"] = carrier_intent.get("requested_carrier_mechanism")
        checks["semiconductor_requested_dopant_element"] = carrier_intent.get("requested_dopant_element")
        checks["semiconductor_requested_defect_type"] = carrier_intent.get("requested_defect_type")
        checks["semiconductor_requested_site_element"] = carrier_intent.get("requested_site_element")
        checks["semiconductor_requested_site_id"] = carrier_intent.get("requested_site_id")
        checks["semiconductor_requested_mapping_rule"] = carrier_intent.get("requested_mapping_rule")
        checks["semiconductor_actual_carrier_type"] = carrier_intent.get("actual_carrier_type")
        checks["semiconductor_actual_dopant_elements"] = carrier_intent.get("actual_dopant_elements")
        checks["semiconductor_actual_defect_count"] = carrier_intent.get("actual_defect_count")
        checks["semiconductor_carrier_intent_latest_matches"] = carrier_intent.get("latest_matches")
        checks["semiconductor_carrier_intent_all_entries_match"] = carrier_intent.get("all_entries_match")
        checks["semiconductor_carrier_intent_warning_count"] = carrier_intent.get("warning_count", 0)
        if not carrier_intent.get("latest_matches", True):
            warnings.append("Semiconductor carrier intent does not match the current dopant/charge-balance diagnostics.")
        warnings.extend(str(item) for item in carrier_intent.get("warnings", []) or [])

    junction_summary = semiconductor.get("junction_summary") or {}
    if junction_summary:
        checks["semiconductor_junction_count"] = junction_summary.get("junction_count")
        checks["semiconductor_pn_junction_count"] = junction_summary.get("pn_junction_count", 0)
        checks["semiconductor_junction_axis"] = junction_summary.get("axis")
        checks["semiconductor_junction_host_element"] = junction_summary.get("host_element")
        checks["semiconductor_p_region_dopant_element"] = junction_summary.get("p_dopant_element")
        checks["semiconductor_n_region_dopant_element"] = junction_summary.get("n_dopant_element")
        checks["semiconductor_junction_warning_count"] = junction_summary.get("warning_count", 0)
        if int(junction_summary.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor junction metadata has warnings; inspect junction_summary.")
        warnings.extend(str(item) for item in junction_summary.get("warnings", []) or [])

    finite_size = semiconductor.get("finite_size_summary") or {}
    if finite_size:
        checks["semiconductor_finite_size_warning"] = finite_size.get("finite_size_warning")
        checks["semiconductor_finite_size_small_cell_warning"] = finite_size.get("small_cell_warning")
        checks["semiconductor_finite_size_high_concentration_warning"] = finite_size.get("high_concentration_warning")
        checks["semiconductor_finite_size_non_passivant_atom_count"] = finite_size.get("non_passivant_atom_count")
        checks["semiconductor_finite_size_max_isolated_fraction"] = finite_size.get("max_isolated_fraction")
        checks["semiconductor_finite_size_max_isolated_kind"] = (finite_size.get("max_isolated_item") or {}).get("kind")
        if finite_size.get("finite_size_warning"):
            warnings.append("Semiconductor finite-size/dilution preflight has warnings; inspect finite_size_summary before quantitative defect or dopant calculations.")

    dopant_fraction_summary = semiconductor.get("dopant_fraction_summary") or {}
    if dopant_fraction_summary:
        checks["semiconductor_dopant_fraction_count"] = dopant_fraction_summary.get("entry_count")
        checks["semiconductor_dopant_fraction_max_abs_rounding_error_fraction"] = dopant_fraction_summary.get("max_abs_rounding_error_fraction")
        checks["semiconductor_dopant_fraction_rounding_warning"] = dopant_fraction_summary.get("rounding_warning")
        checks["semiconductor_dopant_fraction_periodic_maximin_count"] = dopant_fraction_summary.get(
            "periodic_maximin_count",
            0,
        )
        checks["semiconductor_dopant_fraction_site_selection_integrity_ok"] = dopant_fraction_summary.get(
            "site_selection_integrity_ok"
        )
        checks["semiconductor_dopant_fraction_site_selection_replay_verified"] = dopant_fraction_summary.get(
            "site_selection_replay_verified"
        )
        checks["semiconductor_dopant_fraction_site_pair_distribution_count"] = dopant_fraction_summary.get(
            "site_pair_distribution_count",
            0,
        )
        checks["semiconductor_dopant_fraction_site_pair_distribution_integrity_ok"] = dopant_fraction_summary.get(
            "site_pair_distribution_integrity_ok"
        )
        checks[
            "semiconductor_dopant_fraction_site_pair_distribution_current_geometry_applicable"
        ] = dopant_fraction_summary.get("site_pair_distribution_current_geometry_applicable")
        checks[
            "semiconductor_dopant_fraction_nearest_shell_pair_excess_review_required"
        ] = dopant_fraction_summary.get(
            "site_pair_distribution_nearest_shell_pair_excess_review_required"
        )
        checks[
            "semiconductor_dopant_fraction_nearest_shell_pair_avoidance_observed"
        ] = dopant_fraction_summary.get(
            "site_pair_distribution_nearest_shell_pair_avoidance_observed"
        )
        checks["semiconductor_dopant_fraction_site_short_range_order_count"] = (
            dopant_fraction_summary.get("site_short_range_order_count", 0)
        )
        checks["semiconductor_dopant_fraction_site_short_range_order_integrity_ok"] = (
            dopant_fraction_summary.get("site_short_range_order_integrity_ok")
        )
        checks[
            "semiconductor_dopant_fraction_site_short_range_order_current_geometry_applicable"
        ] = dopant_fraction_summary.get("site_short_range_order_current_geometry_applicable")
        checks[
            "semiconductor_dopant_fraction_nearest_shell_ordering_like_observed"
        ] = dopant_fraction_summary.get(
            "site_short_range_order_nearest_shell_ordering_like_observed"
        )
        checks[
            "semiconductor_dopant_fraction_nearest_shell_clustering_like_review_required"
        ] = dopant_fraction_summary.get(
            "site_short_range_order_nearest_shell_clustering_like_review_required"
        )
        if dopant_fraction_summary.get("rounding_warning"):
            warnings.append("Semiconductor dopant fraction was rounded noticeably by finite cell size; inspect dopant_fraction_summary.")
        if dopant_fraction_summary.get("periodic_maximin_count"):
            warnings.append(
                "Semiconductor dopant sites use deterministic periodic maximin separation, not an SQS."
            )
        if dopant_fraction_summary.get("site_selection_integrity_ok") is False:
            warnings.append("Semiconductor dopant-fraction site-selection metadata failed integrity checks.")
        if dopant_fraction_summary.get("site_pair_distribution_integrity_ok") is False:
            warnings.append("Semiconductor dopant-fraction pair-distribution audit failed integrity checks.")
        if dopant_fraction_summary.get("site_short_range_order_integrity_ok") is False:
            warnings.append("Semiconductor dopant-fraction short-range-order audit failed integrity checks.")
        if dopant_fraction_summary.get(
            "site_pair_distribution_nearest_shell_pair_excess_review_required"
        ):
            warnings.append(
                "Semiconductor dopant sites have a nearest-shell pair excess relative to the fixed-composition expectation."
            )
        if dopant_fraction_summary.get(
            "site_short_range_order_nearest_shell_clustering_like_review_required"
        ):
            warnings.append(
                "Semiconductor dopant sites have clustering-like unlike-pair depletion in the nearest finite-cell distance shell."
            )

    alloy_summary = semiconductor.get("alloy_summary") or {}
    if alloy_summary:
        checks["semiconductor_alloy_count"] = alloy_summary.get("entry_count")
        checks["semiconductor_alloy_max_abs_rounding_error_fraction"] = alloy_summary.get("max_abs_rounding_error_fraction")
        checks["semiconductor_alloy_rounding_warning"] = alloy_summary.get("rounding_warning")
        checks["semiconductor_alloy_periodic_maximin_count"] = alloy_summary.get("periodic_maximin_count", 0)
        checks["semiconductor_alloy_site_selection_integrity_ok"] = alloy_summary.get(
            "site_selection_integrity_ok"
        )
        checks["semiconductor_alloy_site_selection_replay_verified"] = alloy_summary.get(
            "site_selection_replay_verified"
        )
        checks["semiconductor_alloy_site_pair_distribution_count"] = alloy_summary.get(
            "site_pair_distribution_count",
            0,
        )
        checks["semiconductor_alloy_site_pair_distribution_integrity_ok"] = alloy_summary.get(
            "site_pair_distribution_integrity_ok"
        )
        checks["semiconductor_alloy_site_pair_distribution_current_geometry_applicable"] = alloy_summary.get(
            "site_pair_distribution_current_geometry_applicable"
        )
        checks["semiconductor_alloy_nearest_shell_pair_excess_review_required"] = alloy_summary.get(
            "site_pair_distribution_nearest_shell_pair_excess_review_required"
        )
        checks["semiconductor_alloy_nearest_shell_pair_avoidance_observed"] = alloy_summary.get(
            "site_pair_distribution_nearest_shell_pair_avoidance_observed"
        )
        checks["semiconductor_alloy_site_short_range_order_count"] = alloy_summary.get(
            "site_short_range_order_count",
            0,
        )
        checks["semiconductor_alloy_site_short_range_order_integrity_ok"] = alloy_summary.get(
            "site_short_range_order_integrity_ok"
        )
        checks["semiconductor_alloy_site_short_range_order_current_geometry_applicable"] = (
            alloy_summary.get("site_short_range_order_current_geometry_applicable")
        )
        checks["semiconductor_alloy_nearest_shell_ordering_like_observed"] = alloy_summary.get(
            "site_short_range_order_nearest_shell_ordering_like_observed"
        )
        checks[
            "semiconductor_alloy_nearest_shell_clustering_like_review_required"
        ] = alloy_summary.get(
            "site_short_range_order_nearest_shell_clustering_like_review_required"
        )
        if alloy_summary.get("rounding_warning"):
            warnings.append("Semiconductor alloy fraction was rounded noticeably by finite cell size; inspect alloy_summary.")
        if alloy_summary.get("periodic_maximin_count"):
            warnings.append("Semiconductor alloy sites use deterministic periodic maximin separation, not an SQS.")
        if alloy_summary.get("site_selection_integrity_ok") is False:
            warnings.append("Semiconductor alloy site-selection metadata failed integrity checks.")
        if alloy_summary.get("site_pair_distribution_integrity_ok") is False:
            warnings.append("Semiconductor alloy pair-distribution audit failed integrity checks.")
        if alloy_summary.get("site_short_range_order_integrity_ok") is False:
            warnings.append("Semiconductor alloy short-range-order audit failed integrity checks.")
        if alloy_summary.get("site_pair_distribution_nearest_shell_pair_excess_review_required"):
            warnings.append(
                "Semiconductor alloy sites have a nearest-shell pair excess relative to the fixed-composition expectation."
            )
        if alloy_summary.get("site_short_range_order_nearest_shell_clustering_like_review_required"):
            warnings.append(
                "Semiconductor alloy sites have clustering-like unlike-pair depletion in the nearest finite-cell distance shell."
            )
        if int(checks.get("semiconductor_alloy_same_sublattice_neighbor_pair_count") or 0) > 0:
            warnings.append(
                "Semiconductor alloy has same-sublattice neighbor pairs under the preflight cutoff; inspect neighbor_distance_summary."
            )

    layer_profile = semiconductor.get("layer_profile_summary") or {}
    if layer_profile:
        checks["semiconductor_layer_profile_axis"] = layer_profile.get("axis")
        checks["semiconductor_layer_profile_layer_count"] = layer_profile.get("layer_count")
        checks["semiconductor_layer_profile_min_interlayer_spacing_angstrom"] = layer_profile.get("min_interlayer_spacing_angstrom")
        checks["semiconductor_layer_profile_spacing_warning"] = layer_profile.get("spacing_warning")
        if layer_profile.get("spacing_warning"):
            warnings.append("Semiconductor layer profile has unusually small interlayer spacing; inspect layer_profile_summary.")

    layer_translation = semiconductor.get("layer_translation_summary") or {}
    if layer_translation:
        latest_translation = layer_translation.get("latest") or {}
        checks["semiconductor_layer_translation_count"] = layer_translation.get("entry_count")
        checks["semiconductor_layer_translation_quality"] = layer_translation.get("quality")
        checks["semiconductor_layer_translation_metadata_consistent"] = layer_translation.get(
            "metadata_consistent"
        )
        checks["semiconductor_layer_translation_latest_layer_index"] = latest_translation.get("layer_index")
        checks["semiconductor_layer_translation_latest_axis"] = latest_translation.get("translation_axis")
        checks["semiconductor_layer_translation_latest_distance_angstrom"] = latest_translation.get(
            "distance_angstrom"
        )
        if layer_translation.get("metadata_consistent") is False:
            warnings.append(
                "Semiconductor layer-translation receipt does not match the current layer profile; "
                "inspect layer_translation_summary."
            )

    layer_rotation = semiconductor.get("layer_rotation_summary") or {}
    if layer_rotation:
        latest_rotation = layer_rotation.get("latest") or {}
        checks["semiconductor_layer_rotation_count"] = layer_rotation.get("entry_count")
        checks["semiconductor_layer_rotation_quality"] = layer_rotation.get("quality")
        checks["semiconductor_layer_rotation_metadata_consistent"] = layer_rotation.get(
            "metadata_consistent"
        )
        checks["semiconductor_layer_rotation_coordinate_binding_matches_current"] = layer_rotation.get(
            "coordinate_binding_matches_current"
        )
        checks["semiconductor_layer_rotation_latest_layer_index"] = latest_rotation.get("layer_index")
        checks["semiconductor_layer_rotation_latest_axis"] = latest_rotation.get("rotation_axis")
        checks["semiconductor_layer_rotation_latest_angle_degrees"] = latest_rotation.get("angle_degrees")
        checks["semiconductor_layer_rotation_commensurability_verified"] = layer_rotation.get(
            "commensurability_verified"
        )
        checks["semiconductor_layer_rotation_requires_geometry_relaxation"] = layer_rotation.get(
            "requires_geometry_relaxation"
        )
        checks["semiconductor_layer_rotation_calculation_ready"] = layer_rotation.get("calculation_ready")
        if layer_rotation.get("metadata_consistent") is False:
            warnings.append(
                "Semiconductor layer-rotation receipt does not match current layer coordinates; "
                "inspect layer_rotation_summary."
            )
        if layer_rotation.get("commensurability_verified") is False:
            warnings.append(
                "Semiconductor layer rotation is not commensurability-verified; treat it as visual review only."
            )
        if layer_rotation.get("requires_geometry_relaxation"):
            warnings.append(
                "Semiconductor layer-rotation scaffold requires geometry relaxation before calculation."
            )

    castep_relaxation = semiconductor.get("castep_geometry_optimization_summary") or {}
    if castep_relaxation:
        checks["semiconductor_castep_relaxation_quality"] = castep_relaxation.get(
            "quality"
        )
        checks["semiconductor_castep_relaxation_source_revision"] = (
            castep_relaxation.get("source_revision")
        )
        checks["semiconductor_castep_relaxation_target_revision"] = (
            castep_relaxation.get("target_revision")
        )
        checks["semiconductor_castep_relaxation_converged"] = (
            castep_relaxation.get("convergence_verified")
        )
        checks["semiconductor_castep_relaxation_transition_verified"] = (
            castep_relaxation.get("transition_verified")
        )
        checks["semiconductor_castep_relaxation_fixed_cell_verified"] = (
            castep_relaxation.get("fixed_cell_transition_verified")
        )
        checks["semiconductor_castep_relaxation_output_binding_verified"] = (
            castep_relaxation.get("output_binding_verified")
        )
        checks["semiconductor_castep_relaxation_atom_identity_verified"] = (
            castep_relaxation.get("atom_identity_verified")
        )
        if castep_relaxation.get("transition_verified") is False:
            warnings.append(
                "CASTEP geometry-optimization receipt is not bound to the current immutable revision."
            )

    castep_electronic = semiconductor.get("castep_electronic_result_summary") or {}
    castep_electronic_assessment = (
        semiconductor.get("castep_electronic_result_assessment") or {}
    )
    if castep_electronic:
        checks["semiconductor_castep_electronic_task"] = castep_electronic.get(
            "task"
        )
        checks["semiconductor_castep_electronic_source_revision"] = (
            castep_electronic.get("source_revision")
        )
        checks["semiconductor_castep_electronic_target_revision"] = (
            castep_electronic.get("target_revision")
        )
        checks["semiconductor_castep_electronic_binding_verified"] = (
            castep_electronic.get("binding_verified")
        )
        checks["semiconductor_castep_electronic_backend_run_completed"] = (
            castep_electronic.get("backend_run_completed")
        )
        checks["semiconductor_castep_electronic_scientific_convergence_verified"] = (
            castep_electronic.get("scientific_convergence_verified")
        )
        checks["semiconductor_castep_electronic_scientific_band_gap_verified"] = (
            castep_electronic.get("scientific_band_gap_verified")
        )
        checks["semiconductor_castep_electronic_numeric_curve_data_exported"] = (
            castep_electronic.get("numeric_curve_data_exported")
        )
        checks["semiconductor_castep_electronic_numeric_curve_kind"] = (
            castep_electronic.get("numeric_curve_kind")
        )
        checks["semiconductor_castep_electronic_native_band_path_exported"] = (
            castep_electronic.get("native_band_kpoint_path_exported")
        )
        checks["semiconductor_castep_electronic_pdos_weights_exported"] = (
            castep_electronic.get("pdos_projection_weights_exported")
        )
        raw_native_output = castep_electronic.get("native_output_audit")
        native_output = (
            raw_native_output if isinstance(raw_native_output, dict) else {}
        )
        raw_native_scf = native_output.get("castep_output_audit")
        native_scf = raw_native_scf if isinstance(raw_native_scf, dict) else {}
        raw_native_bands = native_output.get("bands_summary")
        native_bands = (
            raw_native_bands if isinstance(raw_native_bands, dict) else {}
        )
        raw_band_edges = native_output.get("sampled_band_edges")
        band_edges = raw_band_edges if isinstance(raw_band_edges, dict) else {}
        raw_gap_crosscheck = band_edges.get("reported_band_gap_crosscheck")
        gap_crosscheck = (
            raw_gap_crosscheck if isinstance(raw_gap_crosscheck, dict) else {}
        )
        checks["semiconductor_castep_native_output_audit_status"] = (
            native_output.get("status")
        )
        checks["semiconductor_castep_native_scf_status"] = native_scf.get(
            "status"
        )
        checks["semiconductor_castep_native_scf_last_iteration"] = (
            native_scf.get("last_scf_iteration")
        )
        checks["semiconductor_castep_native_scf_maximum_cycles_reached"] = (
            native_scf.get("maximum_scf_cycles_reached")
        )
        checks["semiconductor_castep_native_band_kpoint_count"] = (
            native_bands.get("number_of_kpoints")
        )
        checks["semiconductor_castep_native_band_eigenvalue_count"] = (
            native_bands.get("eigenvalue_count")
        )
        checks["semiconductor_castep_sampled_band_edge_status"] = (
            band_edges.get("status")
        )
        checks["semiconductor_castep_sampled_band_gap_ev"] = band_edges.get(
            "sampled_gap_ev"
        )
        checks["semiconductor_castep_sampled_fermi_crossing_observed"] = (
            band_edges.get("fermi_crossing_observed")
        )
        checks["semiconductor_castep_sampled_band_gap_spin_component"] = (
            band_edges.get("gap_spin_component")
        )
        checks["semiconductor_castep_reported_band_gap_crosscheck_status"] = (
            gap_crosscheck.get("status")
        )
        checks["semiconductor_castep_reported_band_gap_difference_ev"] = (
            gap_crosscheck.get("absolute_difference_ev")
        )
        checks["semiconductor_castep_electronic_result_document_name"] = (
            castep_electronic.get("result_document_name")
        )
        checks["semiconductor_castep_electronic_total_energy_kcal_per_mol"] = (
            castep_electronic.get("total_energy_kcal_per_mol")
        )
        checks["semiconductor_castep_electronic_band_gap_ev"] = (
            castep_electronic.get("band_gap_ev")
        )
        if castep_electronic.get("binding_verified") is False:
            warnings.append(
                "CASTEP electronic-result receipt is not bound to the current immutable revision."
            )
        elif native_output.get("status") == "review_required":
            warnings.append(
                "CASTEP native-output audit requires review; inspect its persisted "
                "errors and SCF markers."
            )
        elif castep_electronic.get("scientific_convergence_verified") is not True:
            warnings.append(
                "CASTEP electronic backend completion is recorded, but independent "
                "SCF convergence remains unverified."
            )
        if (
            castep_electronic.get("binding_verified") is True
            and castep_electronic.get("numeric_curve_data_exported") is not True
        ):
            warnings.append(
                "The requested CASTEP numeric property curve was not exported."
            )
        if (
            castep_electronic.get("binding_verified") is True
            and band_edges.get("fermi_crossing_observed") is True
        ):
            warnings.append(
                "Native sampled CASTEP bands show a Fermi-level crossing; review "
                "metallic or semimetallic behavior."
            )
        if (
            castep_electronic.get("binding_verified") is True
            and gap_crosscheck.get("status") == "review_difference"
        ):
            warnings.append(
                "Native sampled CASTEP band edges differ from the reported BandGap "
                "beyond the recorded comparison tolerance."
            )
    if castep_electronic_assessment:
        checks["semiconductor_castep_electronic_assessment_status"] = (
            castep_electronic_assessment.get("status")
        )
        checks["semiconductor_castep_electronic_assessment_trust_status"] = (
            castep_electronic_assessment.get("trust_status")
        )
        checks["semiconductor_castep_electronic_artifact_evidence_verified"] = (
            castep_electronic_assessment.get("artifact_evidence_verified")
        )
        checks[
            "semiconductor_castep_electronic_calculation_result_review_required"
        ] = castep_electronic_assessment.get("calculation_result_review_required")
        checks[
            "semiconductor_castep_electronic_structure_normality_blocked"
        ] = castep_electronic_assessment.get("structure_normality_blocked")
        checks["semiconductor_castep_electronic_result_review_reasons"] = (
            castep_electronic_assessment.get("result_review_reasons") or []
        )
        if castep_electronic_assessment.get(
            "calculation_result_review_required"
        ) is True:
            warnings.append(
                "CASTEP electronic result requires calculation-only review: "
                f"{castep_electronic_assessment.get('status')}."
            )

    castep_convergence = semiconductor.get("castep_convergence_audit") or {}
    if castep_convergence:
        checks["semiconductor_castep_convergence_status"] = (
            castep_convergence.get("status")
        )
        checks["semiconductor_castep_convergence_history_entry_count"] = (
            castep_convergence.get("history_entry_count")
        )
        checks["semiconductor_castep_convergence_verified_point_count"] = (
            castep_convergence.get("verified_point_count")
        )
        checks["semiconductor_castep_convergence_rejected_point_count"] = (
            castep_convergence.get("rejected_point_count")
        )
        checks["semiconductor_castep_convergence_series_count"] = (
            castep_convergence.get("comparable_series_count")
        )
        checks["semiconductor_castep_convergence_artifact_evidence_verified"] = (
            castep_convergence.get("artifact_evidence_verified")
        )
        checks[
            "semiconductor_castep_parameter_sensitivity_evidence_verified"
        ] = castep_convergence.get("parameter_sensitivity_evidence_verified")
        checks[
            "semiconductor_castep_parameter_sensitivity_within_tolerance"
        ] = castep_convergence.get("parameter_sensitivity_within_tolerance")
        checks["semiconductor_castep_scientific_convergence_verified"] = (
            castep_convergence.get("scientific_convergence_verified")
        )
        checks["semiconductor_castep_convergence_structure_normality_blocked"] = (
            castep_convergence.get("structure_normality_blocked")
        )
        checks["semiconductor_castep_convergence_review_reasons"] = (
            castep_convergence.get("result_review_reasons") or []
        )
        convergence_status = castep_convergence.get("status")
        if convergence_status == "history_binding_review_required":
            warnings.append(
                "CASTEP convergence history contains missing, changed, or unbound "
                "result evidence."
            )
        elif convergence_status == "parameter_sensitivity_above_tolerance":
            warnings.append(
                "CASTEP cutoff or k-point parameter sensitivity remains above the "
                "recorded tolerance."
            )
        elif convergence_status in {
            "insufficient_comparable_points",
            "pairwise_evidence_only",
        }:
            warnings.append(
                "CASTEP parameter convergence needs a comparable three-point "
                "cutoff or k-point sequence."
            )
        elif convergence_status == "parameter_sensitivity_within_tolerance":
            warnings.append(
                "CASTEP parameter sensitivity is within the recorded tolerance, "
                "but this does not independently verify scientific convergence."
            )

    commensurate_twist = semiconductor.get("commensurate_twist_summary") or {}
    if commensurate_twist:
        latest_twist = commensurate_twist.get("latest") or {}
        checks["semiconductor_commensurate_twist_count"] = commensurate_twist.get("entry_count")
        checks["semiconductor_commensurate_twist_quality"] = commensurate_twist.get("quality")
        checks["semiconductor_commensurate_twist_metadata_consistent"] = commensurate_twist.get(
            "metadata_consistent"
        )
        checks["semiconductor_commensurate_twist_m"] = latest_twist.get("commensurate_m")
        checks["semiconductor_commensurate_twist_n"] = latest_twist.get("commensurate_n")
        checks["semiconductor_commensurate_twist_angle_degrees"] = latest_twist.get(
            "twist_angle_degrees"
        )
        checks["semiconductor_commensurate_twist_atom_count"] = latest_twist.get("atom_count")
        checks["semiconductor_commensurate_twist_matrix_verified"] = commensurate_twist.get(
            "matrix_determinant_verified"
        )
        checks["semiconductor_commensurate_twist_angle_verified"] = commensurate_twist.get(
            "angle_verified"
        )
        checks["semiconductor_commensurate_twist_lattice_verified"] = commensurate_twist.get(
            "lattice_verified"
        )
        checks["semiconductor_commensurate_twist_structure_binding_matches_current"] = (
            commensurate_twist.get("structure_binding_matches_current")
        )
        checks["semiconductor_commensurate_twist_structure_binding_scope"] = (
            commensurate_twist.get("structure_binding_scope")
        )
        checks["semiconductor_commensurate_twist_relaxation_transition_verified"] = (
            commensurate_twist.get("castep_relaxation_transition_verified")
        )
        checks["semiconductor_commensurate_twist_commensurability_verified"] = (
            commensurate_twist.get("commensurability_verified")
        )
        checks["semiconductor_commensurate_twist_requires_geometry_relaxation"] = (
            commensurate_twist.get("requires_geometry_relaxation")
        )
        checks["semiconductor_commensurate_twist_calculation_ready"] = commensurate_twist.get(
            "calculation_ready"
        )
        if commensurate_twist.get("metadata_consistent") is False:
            warnings.append(
                "Commensurate TMD twist receipt does not match the current structure; "
                "inspect commensurate_twist_summary."
            )
        elif commensurate_twist.get("requires_geometry_relaxation"):
            warnings.append(
                "Commensurate TMD twisted bilayer is periodic and verified but still requires geometry relaxation."
            )

    commensurate_heterobilayer = semiconductor.get("commensurate_heterobilayer_summary") or {}
    if commensurate_heterobilayer:
        latest_heterobilayer = commensurate_heterobilayer.get("latest") or {}
        checks["semiconductor_commensurate_heterobilayer_count"] = commensurate_heterobilayer.get(
            "entry_count"
        )
        checks["semiconductor_commensurate_heterobilayer_quality"] = commensurate_heterobilayer.get(
            "quality"
        )
        checks["semiconductor_commensurate_heterobilayer_metadata_consistent"] = (
            commensurate_heterobilayer.get("metadata_consistent")
        )
        checks["semiconductor_commensurate_heterobilayer_bottom_material"] = (
            commensurate_heterobilayer.get("bottom_material")
        )
        checks["semiconductor_commensurate_heterobilayer_top_material"] = (
            commensurate_heterobilayer.get("top_material")
        )
        checks["semiconductor_commensurate_heterobilayer_m"] = latest_heterobilayer.get(
            "commensurate_m"
        )
        checks["semiconductor_commensurate_heterobilayer_n"] = latest_heterobilayer.get(
            "commensurate_n"
        )
        checks["semiconductor_commensurate_heterobilayer_angle_degrees"] = (
            latest_heterobilayer.get("twist_angle_degrees")
        )
        checks["semiconductor_commensurate_heterobilayer_atom_count"] = latest_heterobilayer.get(
            "atom_count"
        )
        checks["semiconductor_commensurate_heterobilayer_layer_materials_verified"] = (
            commensurate_heterobilayer.get("layer_materials_verified")
        )
        checks["semiconductor_commensurate_heterobilayer_strain_policy"] = (
            commensurate_heterobilayer.get("strain_policy")
        )
        checks["semiconductor_commensurate_heterobilayer_max_abs_strain_percent"] = (
            commensurate_heterobilayer.get("max_abs_biaxial_strain_percent")
        )
        checks["semiconductor_commensurate_heterobilayer_strain_partition_verified"] = (
            commensurate_heterobilayer.get("strain_partition_verified")
        )
        checks["semiconductor_commensurate_heterobilayer_structure_binding_matches_current"] = (
            commensurate_heterobilayer.get("structure_binding_matches_current")
        )
        checks["semiconductor_commensurate_heterobilayer_structure_binding_scope"] = (
            commensurate_heterobilayer.get("structure_binding_scope")
        )
        checks["semiconductor_commensurate_heterobilayer_relaxation_transition_verified"] = (
            commensurate_heterobilayer.get("castep_relaxation_transition_verified")
        )
        checks["semiconductor_commensurate_heterobilayer_commensurability_verified"] = (
            commensurate_heterobilayer.get("commensurability_verified")
        )
        checks["semiconductor_commensurate_heterobilayer_requires_geometry_relaxation"] = (
            commensurate_heterobilayer.get("requires_geometry_relaxation")
        )
        checks["semiconductor_commensurate_heterobilayer_calculation_ready"] = (
            commensurate_heterobilayer.get("calculation_ready")
        )
        if commensurate_heterobilayer.get("metadata_consistent") is False:
            warnings.append(
                "Commensurate TMD heterobilayer receipt does not match the current materials, strain, "
                "or structure; inspect commensurate_heterobilayer_summary."
            )
        elif commensurate_heterobilayer.get("requires_geometry_relaxation"):
            warnings.append(
                "Commensurate TMD heterobilayer is periodic after verified strain partition but still "
                "requires geometry relaxation."
            )

    two_dimensional_electrostatics = (
        semiconductor.get("two_dimensional_electrostatic_summary") or {}
    )
    if two_dimensional_electrostatics:
        checks["semiconductor_2d_electrostatic_status"] = two_dimensional_electrostatics.get(
            "status"
        )
        checks["semiconductor_2d_electrostatic_quality"] = two_dimensional_electrostatics.get(
            "quality"
        )
        checks["semiconductor_2d_expected_asymmetry_verified"] = (
            two_dimensional_electrostatics.get("expected_compositional_asymmetry_verified")
        )
        checks["semiconductor_2d_vacuum_geometry_verified"] = two_dimensional_electrostatics.get(
            "vacuum_geometry_verified"
        )
        checks["semiconductor_2d_structure_binding_verified"] = (
            two_dimensional_electrostatics.get("structure_binding_verified")
        )
        checks["semiconductor_2d_model_geometry_verified"] = two_dimensional_electrostatics.get(
            "model_geometry_verified"
        )
        checks["semiconductor_2d_model_geometry_normality_blocker"] = (
            two_dimensional_electrostatics.get("model_geometry_normality_blocker")
        )
        checks["semiconductor_2d_charge_density_available"] = two_dimensional_electrostatics.get(
            "charge_density_available"
        )
        checks["semiconductor_2d_dipole_moment_calculated"] = two_dimensional_electrostatics.get(
            "dipole_moment_calculated"
        )
        checks["semiconductor_2d_dipole_correction_api_verified"] = (
            two_dimensional_electrostatics.get("dipole_correction_api_verified")
        )
        checks["semiconductor_2d_dipole_correction_api_contract"] = (
            two_dimensional_electrostatics.get("dipole_correction_api_contract")
        )
        checks["semiconductor_2d_dipole_correction_api_property"] = (
            two_dimensional_electrostatics.get("dipole_correction_api_property")
        )
        checks["semiconductor_2d_dipole_correction_mode"] = (
            two_dimensional_electrostatics.get("dipole_correction_mode")
        )
        checks["semiconductor_2d_dipole_correction_enabled"] = (
            two_dimensional_electrostatics.get("dipole_correction_enabled")
        )
        checks["semiconductor_2d_dipole_correction_task_compatible"] = (
            two_dimensional_electrostatics.get("dipole_correction_task_compatible")
        )
        checks["semiconductor_2d_dipole_correction_vacuum_requirement_met"] = (
            two_dimensional_electrostatics.get("dipole_correction_vacuum_requirement_met")
        )
        checks["semiconductor_2d_dipole_correction_setting_verified"] = (
            two_dimensional_electrostatics.get("dipole_correction_setting_verified")
        )
        checks["semiconductor_2d_geometry_relaxation_required"] = (
            two_dimensional_electrostatics.get("geometry_relaxation_required")
        )
        checks["semiconductor_2d_geometry_relaxation_verified"] = (
            two_dimensional_electrostatics.get("geometry_relaxation_verified")
        )
        checks["semiconductor_2d_calculation_review_required"] = (
            two_dimensional_electrostatics.get("calculation_review_required")
        )
        checks["semiconductor_2d_quantitative_electrostatic_calculation_ready"] = (
            two_dimensional_electrostatics.get(
                "quantitative_electrostatic_calculation_ready"
            )
        )
        if two_dimensional_electrostatics.get("model_geometry_normality_blocker"):
            warnings.append(
                "Two-dimensional electrostatic preflight could not verify the current heterobilayer "
                "geometry, expected surface asymmetry, or vacuum."
            )
        elif two_dimensional_electrostatics.get("calculation_review_required"):
            warnings.append(
                "Two-dimensional heterobilayer geometry is verified; configure the reviewed "
                "Materials Studio 20.1 DipoleCorrection setting before quantitative calculation."
            )

    interface_quality = semiconductor.get("interface_quality_summary") or {}
    interface_profile = semiconductor.get("interface_profile_summary") or {}
    if interface_profile:
        checks["semiconductor_interface_profile_layer_count"] = interface_profile.get("layer_count")
        checks["semiconductor_interface_profile_segment_count"] = interface_profile.get("material_segment_count")
        checks["semiconductor_interface_transition_count"] = interface_profile.get("interface_transition_count")
        checks["semiconductor_interface_mixed_layer_count"] = interface_profile.get("mixed_layer_count")
        checks["semiconductor_interface_abrupt"] = interface_profile.get("abrupt_interface")
        if int(interface_profile.get("mixed_layer_count") or 0) > 0 and not interface_quality.get("mixed_layers_expected"):
            warnings.append("Semiconductor interface profile contains mixed layers; inspect interface_profile_summary.")

    if interface_quality:
        checks["semiconductor_interface_quality"] = interface_quality.get("quality")
        checks["semiconductor_interface_material_sequence"] = interface_quality.get("material_sequence")
        checks["semiconductor_interface_expected_material_sequence"] = interface_quality.get("expected_material_sequence")
        checks["semiconductor_interface_period_count"] = interface_quality.get("period_count")
        checks["semiconductor_interface_expected_segment_count_from_periods"] = interface_quality.get("expected_segment_count_from_periods")
        checks["semiconductor_interface_segment_count_matches_periods"] = interface_quality.get("segment_count_matches_periods")
        checks["semiconductor_interface_period_sequence_complete"] = interface_quality.get("period_sequence_complete")
        checks["semiconductor_interface_transition_sequence_complete"] = interface_quality.get("transition_sequence_complete")
        checks["semiconductor_interface_periodic_transition_count"] = interface_quality.get("periodic_interface_transition_count")
        checks["semiconductor_interface_missing_declared_materials"] = interface_quality.get("missing_declared_materials")
        checks["semiconductor_interface_quality_warning_count"] = interface_quality.get("warning_count", 0)
        checks["semiconductor_interface_mixed_layers_expected"] = interface_quality.get("mixed_layers_expected")
        if interface_quality.get("quality") == "incomplete":
            warnings.append("Semiconductor interface sequence is incomplete; inspect interface_quality_summary.")
        elif int(interface_quality.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor interface quality preflight has warnings; inspect interface_quality_summary.")
        warnings.extend(str(item) for item in interface_quality.get("warnings", []) or [])

    oxide_interface_geometry = semiconductor.get("oxide_interface_geometry_summary") or {}
    if oxide_interface_geometry:
        checks["semiconductor_oxide_interface_geometry_status"] = oxide_interface_geometry.get(
            "status"
        )
        checks["semiconductor_oxide_interface_geometry_quality"] = oxide_interface_geometry.get(
            "quality"
        )
        checks["semiconductor_oxide_interface_geometry_atom_binding_complete"] = (
            oxide_interface_geometry.get("atom_binding_complete")
        )
        checks["semiconductor_oxide_interface_boundary_candidate_pair_count"] = (
            oxide_interface_geometry.get("boundary_candidate_pair_count")
        )
        checks["semiconductor_oxide_interface_boundary_neighbor_pair_count"] = (
            oxide_interface_geometry.get("boundary_neighbor_pair_count")
        )
        checks["semiconductor_oxide_interface_boundary_connected"] = (
            oxide_interface_geometry.get("boundary_connected_within_neighbor_cutoff")
        )
        checks["semiconductor_oxide_interface_spacing_count"] = (
            oxide_interface_geometry.get("interface_spacing_count")
        )
        checks["semiconductor_oxide_interface_spacing_mismatch_count"] = (
            oxide_interface_geometry.get("interface_spacing_mismatch_count")
        )
        checks["semiconductor_oxide_interface_spacing_declared_values_match"] = (
            oxide_interface_geometry.get("interface_spacing_declared_values_match")
        )
        checks["semiconductor_oxide_interface_short_contact_count"] = (
            oxide_interface_geometry.get("short_contact_count")
        )
        checks["semiconductor_oxide_interface_isolated_oxide_atom_count"] = (
            oxide_interface_geometry.get("isolated_oxide_atom_count")
        )
        checks["semiconductor_oxide_interface_geometry_preflight_ready"] = (
            oxide_interface_geometry.get("geometry_preflight_ready")
        )
        checks["semiconductor_oxide_interface_calculation_geometry_ready"] = (
            oxide_interface_geometry.get("calculation_geometry_ready")
        )
        if oxide_interface_geometry.get("quality") == "review_required":
            warnings.append(
                "Semiconductor oxide-interface geometry requires review; inspect oxide_interface_geometry_summary."
            )

    oxide_interface = semiconductor.get("oxide_interface_health_summary") or {}
    if oxide_interface:
        checks["semiconductor_oxide_interface_status"] = oxide_interface.get("status")
        checks["semiconductor_oxide_interface_quality"] = oxide_interface.get("quality")
        checks["semiconductor_oxide_interface_oxide_material"] = oxide_interface.get("oxide_material")
        checks["semiconductor_oxide_interface_layer_count"] = oxide_interface.get("oxide_layer_count")
        checks["semiconductor_oxide_interface_element_counts"] = oxide_interface.get("oxide_element_counts")
        checks["semiconductor_oxide_interface_stoichiometry_status"] = oxide_interface.get(
            "stoichiometry_status"
        )
        checks["semiconductor_oxide_interface_oxygen_to_cation_ratio"] = oxide_interface.get(
            "oxygen_to_cation_ratio"
        )
        checks["semiconductor_oxide_interface_oxygen_deficit_count"] = oxide_interface.get(
            "oxygen_deficit_count"
        )
        checks["semiconductor_oxide_interface_oxygen_deficit_binding_status"] = oxide_interface.get(
            "oxygen_deficit_binding_status"
        )
        checks["semiconductor_oxide_interface_recorded_oxygen_vacancy_count"] = oxide_interface.get(
            "recorded_oxygen_vacancy_count"
        )
        checks["semiconductor_oxide_interface_vacancy_locations_verified"] = oxide_interface.get(
            "all_recorded_oxygen_vacancy_locations_verified"
        )
        checks["semiconductor_oxide_interface_requires_geometry_relaxation"] = oxide_interface.get(
            "requires_geometry_relaxation"
        )
        checks["semiconductor_oxide_interface_visual_preflight_ready"] = oxide_interface.get(
            "visual_preflight_ready"
        )
        checks["semiconductor_oxide_interface_calculation_ready"] = oxide_interface.get(
            "calculation_ready"
        )
        if oxide_interface.get("quality") == "review_required":
            warnings.append(
                "Semiconductor oxide-interface chemistry requires review; inspect oxide_interface_health_summary."
            )

    gate_stack = semiconductor.get("gate_stack_summary") or {}
    if gate_stack:
        checks["semiconductor_gate_stack_quality"] = gate_stack.get("quality")
        checks["semiconductor_gate_stack_sequence"] = gate_stack.get("material_sequence")
        checks["semiconductor_gate_stack_expected_sequence"] = gate_stack.get("expected_stack_sequence")
        checks["semiconductor_gate_stack_sequence_matches_expected"] = gate_stack.get("sequence_matches_expected")
        checks["semiconductor_gate_stack_gate_material"] = gate_stack.get("gate_material")
        checks["semiconductor_gate_stack_oxide_material"] = gate_stack.get("gate_oxide_material")
        checks["semiconductor_gate_stack_channel_material"] = gate_stack.get("semiconductor_channel_material")
        checks["semiconductor_gate_stack_declared_oxide_thickness_angstrom"] = gate_stack.get("declared_oxide_thickness_angstrom")
        checks["semiconductor_gate_stack_declared_gate_thickness_angstrom"] = gate_stack.get("declared_gate_thickness_angstrom")
        checks["semiconductor_gate_stack_declared_channel_thickness_angstrom"] = gate_stack.get("declared_channel_thickness_angstrom")
        checks["semiconductor_gate_stack_warning_count"] = gate_stack.get("warning_count", 0)
        if gate_stack.get("quality") not in {None, "complete"}:
            warnings.append("Semiconductor MOS gate-stack preflight has warnings; inspect gate_stack_summary.")
        warnings.extend(str(item) for item in gate_stack.get("warnings", []) or [])

    contact = semiconductor.get("metal_semiconductor_contact_summary") or {}
    if contact:
        checks["semiconductor_contact_quality"] = contact.get("quality")
        checks["semiconductor_contact_type"] = contact.get("contact_type")
        checks["semiconductor_contact_sequence"] = contact.get("material_sequence")
        checks["semiconductor_contact_expected_sequence"] = contact.get("expected_contact_sequence")
        checks["semiconductor_contact_sequence_matches_expected"] = contact.get("sequence_matches_expected")
        checks["semiconductor_contact_metal_material"] = contact.get("metal_material")
        checks["semiconductor_contact_semiconductor_material"] = contact.get("semiconductor_material")
        checks["semiconductor_contact_declared_gap_angstrom"] = contact.get("declared_contact_gap_angstrom")
        checks["semiconductor_contact_actual_gap_angstrom"] = contact.get("actual_contact_gap_angstrom")
        checks["semiconductor_contact_gap_delta_angstrom"] = contact.get("contact_gap_delta_angstrom")
        checks["semiconductor_contact_geometry_status"] = contact.get("contact_geometry_status")
        checks["semiconductor_contact_geometry_next_action"] = contact.get("contact_geometry_next_action")
        checks["semiconductor_contact_declared_metal_thickness_angstrom"] = contact.get("declared_metal_thickness_angstrom")
        checks["semiconductor_contact_actual_metal_thickness_angstrom"] = contact.get("actual_metal_thickness_angstrom")
        checks["semiconductor_contact_metal_thickness_delta_angstrom"] = contact.get("metal_thickness_delta_angstrom")
        checks["semiconductor_contact_declared_semiconductor_thickness_angstrom"] = contact.get("declared_semiconductor_thickness_angstrom")
        checks["semiconductor_contact_warning_count"] = contact.get("warning_count", 0)
        barrier = contact.get("barrier_preflight") or {}
        if barrier:
            checks["semiconductor_contact_barrier_model"] = barrier.get("model")
            checks["semiconductor_contact_metal_work_function_ev"] = barrier.get("metal_work_function_ev")
            checks["semiconductor_contact_semiconductor_electron_affinity_ev"] = barrier.get(
                "semiconductor_electron_affinity_ev"
            )
            checks["semiconductor_contact_semiconductor_band_gap_ev"] = barrier.get("semiconductor_band_gap_ev")
            checks["semiconductor_contact_ideal_n_type_barrier_ev"] = barrier.get("ideal_n_type_barrier_ev")
            checks["semiconductor_contact_ideal_p_type_barrier_ev"] = barrier.get("ideal_p_type_barrier_ev")
            checks["semiconductor_contact_barrier_warning_count"] = barrier.get("warning_count", 0)
            if int(barrier.get("warning_count") or 0) > 0:
                warnings.append(
                    "Semiconductor Schottky barrier metadata preflight has warnings; inspect metal_semiconductor_contact_summary."
                )
            warnings.extend(str(item) for item in barrier.get("warnings", []) or [])
        if contact.get("quality") not in {None, "complete"}:
            warnings.append("Semiconductor metal/semiconductor contact preflight has warnings; inspect metal_semiconductor_contact_summary.")
        warnings.extend(str(item) for item in contact.get("warnings", []) or [])

    superlattice_period = semiconductor.get("superlattice_period_summary") or {}
    if superlattice_period:
        checks["semiconductor_superlattice_period_count"] = superlattice_period.get("estimated_total_period_count")
        checks["semiconductor_superlattice_period_axis"] = superlattice_period.get("axis")
        checks["semiconductor_superlattice_layers_per_period"] = superlattice_period.get("estimated_layers_per_period")

    quantum_well = semiconductor.get("quantum_well_summary") or {}
    if quantum_well:
        well_stats = quantum_well.get("well_thickness_stats_angstrom") or {}
        barrier_stats = quantum_well.get("barrier_thickness_stats_angstrom") or {}
        period_stats = quantum_well.get("period_thickness_stats_angstrom") or {}
        checks["semiconductor_quantum_well_period_count"] = quantum_well.get("period_count")
        checks["semiconductor_quantum_well_segment_count"] = quantum_well.get("material_segment_count")
        checks["semiconductor_quantum_well_well_material"] = quantum_well.get("well_material")
        checks["semiconductor_quantum_well_barrier_materials"] = quantum_well.get("barrier_materials")
        checks["semiconductor_quantum_well_well_cation_fractions_by_material"] = quantum_well.get("well_cation_fractions_by_material")
        checks["semiconductor_quantum_well_barrier_cation_fractions_by_material"] = quantum_well.get("barrier_cation_fractions_by_material")
        checks["semiconductor_quantum_well_requested_well_layer_count"] = quantum_well.get("requested_well_layer_count")
        checks["semiconductor_quantum_well_requested_barrier_layer_count"] = quantum_well.get("requested_barrier_layer_count")
        checks["semiconductor_quantum_well_requested_well_thickness_angstrom"] = quantum_well.get("requested_well_thickness_angstrom")
        checks["semiconductor_quantum_well_requested_barrier_thickness_angstrom"] = quantum_well.get("requested_barrier_thickness_angstrom")
        checks["semiconductor_quantum_well_well_thickness_error_angstrom"] = quantum_well.get("well_thickness_error_angstrom")
        checks["semiconductor_quantum_well_barrier_thickness_error_angstrom"] = quantum_well.get("barrier_thickness_error_angstrom")
        checks["semiconductor_quantum_well_mean_well_thickness_angstrom"] = well_stats.get("mean")
        checks["semiconductor_quantum_well_mean_barrier_thickness_angstrom"] = barrier_stats.get("mean")
        checks["semiconductor_quantum_well_mean_period_thickness_angstrom"] = period_stats.get("mean")
        checks["semiconductor_quantum_well_warning_count"] = quantum_well.get("warning_count", 0)
        if int(quantum_well.get("warning_count") or 0) > 0:
            warnings.append("Semiconductor quantum-well/MQW thickness diagnostics have warnings; inspect quantum_well_summary.")
        warnings.extend(str(item) for item in quantum_well.get("warnings", []) or [])

    band_alignment = semiconductor.get("band_alignment_summary") or {}
    if band_alignment:
        first_offset = (band_alignment.get("offsets") or [{}])[0] if isinstance(band_alignment.get("offsets"), list) else {}
        checks["semiconductor_band_alignment_quality"] = band_alignment.get("quality")
        checks["semiconductor_band_alignment_model"] = band_alignment.get("model")
        checks["semiconductor_band_alignment_reference_material"] = band_alignment.get("reference_material")
        checks["semiconductor_band_alignment_type_i_barrier_count"] = band_alignment.get("type_i_barrier_count")
        checks["semiconductor_band_alignment_review_offset_count"] = band_alignment.get("review_offset_count")
        checks["semiconductor_band_alignment_warning_count"] = band_alignment.get("warning_count", 0)
        checks["semiconductor_band_alignment_first_conduction_offset_ev"] = first_offset.get(
            "conduction_band_offset_vs_reference_ev"
        )
        checks["semiconductor_band_alignment_first_valence_offset_ev"] = first_offset.get(
            "valence_band_offset_vs_reference_ev"
        )
        checks["semiconductor_band_alignment_first_electron_barrier_ev"] = first_offset.get(
            "electron_barrier_height_ev"
        )
        checks["semiconductor_band_alignment_first_hole_barrier_ev"] = first_offset.get("hole_barrier_height_ev")
        if band_alignment.get("quality") not in {None, "complete"}:
            warnings.append("Semiconductor band-alignment metadata preflight has warnings; inspect band_alignment_summary.")
        warnings.extend(str(item) for item in band_alignment.get("warnings", []) or [])

    polarization_2deg = semiconductor.get("polarization_2deg_summary") or {}
    if polarization_2deg:
        barriers = polarization_2deg.get("barriers") if isinstance(polarization_2deg.get("barriers"), list) else []
        first_barrier = barriers[0] if barriers and isinstance(barriers[0], dict) else {}
        checks["semiconductor_polarization_2deg_quality"] = polarization_2deg.get("quality")
        checks["semiconductor_polarization_2deg_model"] = polarization_2deg.get("model")
        checks["semiconductor_polarization_2deg_well_material"] = polarization_2deg.get("well_material")
        checks["semiconductor_polarization_2deg_barrier_materials"] = polarization_2deg.get("barrier_materials")
        checks["semiconductor_polarization_2deg_candidate_count"] = polarization_2deg.get("candidate_count")
        checks["semiconductor_polarization_2deg_max_abs_sheet_density_cm2"] = polarization_2deg.get(
            "max_abs_sheet_carrier_density_cm2"
        )
        checks["semiconductor_polarization_2deg_warning_count"] = polarization_2deg.get("warning_count", 0)
        checks["semiconductor_polarization_2deg_first_barrier_material"] = first_barrier.get("barrier_material")
        checks["semiconductor_polarization_2deg_first_sheet_density_cm2"] = first_barrier.get(
            "sheet_carrier_density_cm2_abs"
        )
        checks["semiconductor_polarization_2deg_first_electron_barrier_ev"] = first_barrier.get(
            "electron_barrier_height_ev"
        )
        checks["semiconductor_polarization_2deg_first_two_deg_candidate"] = first_barrier.get("two_deg_candidate")
        if polarization_2deg.get("quality") not in {None, "complete"}:
            warnings.append("Semiconductor III-nitride polarization/2DEG preflight has warnings; inspect polarization_2deg_summary.")
        warnings.extend(str(item) for item in polarization_2deg.get("warnings", []) or [])

    p_gan_gate_cap = semiconductor.get("p_gan_gate_cap_summary") or {}
    if p_gan_gate_cap:
        checks["semiconductor_p_gan_gate_cap_quality"] = p_gan_gate_cap.get("quality")
        checks["semiconductor_p_gan_gate_cap_material"] = p_gan_gate_cap.get("material")
        checks["semiconductor_p_gan_gate_cap_layer_count"] = p_gan_gate_cap.get("layer_count")
        checks["semiconductor_p_gan_gate_cap_matched_layer_count"] = p_gan_gate_cap.get("matched_layer_count")
        checks["semiconductor_p_gan_gate_cap_actual_thickness_angstrom"] = p_gan_gate_cap.get(
            "actual_thickness_angstrom"
        )
        checks["semiconductor_p_gan_gate_cap_dopant_atom_id"] = p_gan_gate_cap.get("dopant_atom_id")
        checks["semiconductor_p_gan_gate_cap_dopant_site_found"] = p_gan_gate_cap.get("dopant_site_found")
        checks["semiconductor_p_gan_gate_cap_warning_count"] = p_gan_gate_cap.get("warning_count", 0)
        if p_gan_gate_cap.get("quality") not in {None, "complete"}:
            warnings.append("Semiconductor p-GaN gate/cap preflight has warnings; inspect p_gan_gate_cap_summary.")
        warnings.extend(str(item) for item in p_gan_gate_cap.get("warnings", []) or [])

    surface = semiconductor.get("surface_termination_summary") or {}
    if surface:
        checks["semiconductor_surface_dangling_bond_estimate"] = surface.get("dangling_bond_estimate", 0)
        checks["semiconductor_surface_passivation_coverage_fraction"] = surface.get("passivation_coverage_fraction")
        checks["semiconductor_surface_fully_passivated"] = surface.get("fully_passivated")
        checks["semiconductor_surface_preparation_status"] = surface.get("surface_preparation_status")
        checks["semiconductor_surface_preparation_next_action"] = surface.get("surface_preparation_next_action")
        if int(surface.get("dangling_bond_estimate") or 0) > 0:
            warnings.append("Semiconductor slab has estimated dangling bonds; inspect surface_termination_summary or passivate before calculation.")

    surface_polarity = semiconductor.get("surface_polarity_summary") or {}
    if surface_polarity:
        checks["semiconductor_surface_polar_hint"] = surface_polarity.get("polar_surface_hint")
        checks["semiconductor_surface_same_element_counts"] = surface_polarity.get("same_element_counts")
        checks["semiconductor_surface_passivation_symmetric"] = surface_polarity.get("passivation_symmetric")
        checks["semiconductor_surface_asymmetry_warning"] = surface_polarity.get("surface_asymmetry_warning")
        checks["semiconductor_surface_polarity_status"] = surface_polarity.get("surface_polarity_status")
        checks["semiconductor_surface_polarity_next_action"] = surface_polarity.get("surface_polarity_next_action")
        checks["semiconductor_surface_bottom_formula"] = (surface_polarity.get("bottom") or {}).get("formula")
        checks["semiconductor_surface_top_formula"] = (surface_polarity.get("top") or {}).get("formula")
        if surface_polarity.get("surface_asymmetry_warning"):
            warnings.append("Semiconductor slab has asymmetric or polar surface termination; inspect surface_polarity_summary before slab calculations.")

    surface_model = semiconductor.get("surface_model_summary") or {}
    if surface_model:
        checks["semiconductor_surface_model_status"] = surface_model.get("status")
        checks["semiconductor_surface_model_ready_for_calculation_preflight"] = surface_model.get(
            "ready_for_calculation_preflight"
        )
        checks["semiconductor_surface_model_next_action"] = surface_model.get("next_action")
        if surface_model.get("status") not in {None, "ready"}:
            warnings.append(
                "Semiconductor slab surface model preflight needs review; inspect surface_model_summary before calculation."
            )

    heterostructure = semiconductor.get("heterostructure_summary") or {}
    if heterostructure:
        checks["semiconductor_heterostructure_max_abs_strain_percent"] = heterostructure.get("max_abs_in_plane_strain_percent")
        checks["semiconductor_heterostructure_strain_warning"] = heterostructure.get("strain_warning")
        if heterostructure.get("strain_warning"):
            warnings.append("Semiconductor heterostructure strain exceeds the configured warning threshold.")

    strain = semiconductor.get("strain_summary") or {}
    if strain:
        checks["semiconductor_applied_strain_count"] = strain.get("entry_count")
        checks["semiconductor_applied_strain_max_abs_percent"] = strain.get("max_abs_strain_percent")
        checks["semiconductor_applied_strain_warning"] = strain.get("strain_warning")
        if strain.get("strain_warning"):
            warnings.append("Applied semiconductor lattice strain exceeds the configured warning threshold.")

    return warnings
