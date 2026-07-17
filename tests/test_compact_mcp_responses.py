from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.specs import ModelSpec


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def test_compact_bundle_separates_artifact_availability_from_path_index(
    tmp_path: Path,
) -> None:
    keys = [
        "diagnostic_export_manifest_json",
        "view_quality_csv",
        "modeling_report_summary_csv",
        "modeling_issue_index_json",
        "requested_diagnostic_focus_status_json",
        "semiconductor_neighbor_pairs_csv",
    ]
    bundle_files: dict[str, str] = {}
    for key in keys:
        path = tmp_path / f"{key}.txt"
        path.write_text(key, encoding="utf-8")
        bundle_files[key] = str(path)

    compact = server._compact_live_response(
        {
            "ok": True,
            "diagnostic_export_requested": True,
            "live_summary": {},
            "view_bundle_files": bundle_files,
        },
        "compact",
    )

    assert compact["view_bundle_files_complete"] is True
    assert compact["view_bundle_files_complete_scope"] == (
        "all_listed_persisted_paths_exist"
    )
    assert compact["view_bundle_files_total_count"] == 6
    assert compact["view_bundle_files_existing_count"] == 6
    assert compact["view_bundle_files_missing_count"] == 0
    assert compact["view_bundle_files_missing_keys"] == []
    assert compact["view_bundle_file_index_entry_count_in_response"] == 5
    assert compact["view_bundle_file_index_complete_in_response"] is False
    assert compact["view_bundle_file_index_compacted"] is True
    availability = compact["view_bundle_artifact_availability"]
    assert availability["status"] == "complete"
    assert availability["complete"] is True
    assert availability["content_integrity_validated"] is False
    assert compact["view_bundle_manifest_path"] == bundle_files[
        "diagnostic_export_manifest_json"
    ]

    missing_path = Path(bundle_files["semiconductor_neighbor_pairs_csv"])
    missing_path.unlink()
    incomplete = server._compact_live_response(
        {
            "ok": True,
            "diagnostic_export_requested": True,
            "live_summary": {},
            "view_bundle_files": bundle_files,
        },
        "compact",
    )

    assert incomplete["view_bundle_files_complete"] is False
    assert incomplete["view_bundle_files_existing_count"] == 5
    assert incomplete["view_bundle_files_missing_count"] == 1
    assert incomplete["view_bundle_files_missing_keys"] == [
        "semiconductor_neighbor_pairs_csv"
    ]
    assert incomplete["view_bundle_file_index_compacted"] is True
    assert incomplete["view_bundle_artifact_availability"]["status"] == (
        "missing_or_invalid_files"
    )


def test_compact_preserves_castep_native_output_audit_receipt() -> None:
    compact = server._compact_live_response(
        {
            "ok": True,
            "status": "castep_electronic_result_recorded",
            "scientific_convergence_verified": False,
            "scientific_band_gap_verified": False,
            "numeric_curve_data_exported": True,
            "numeric_curve_kind": "native_castep_band_eigenvalues",
            "native_output_audit_status": "complete",
            "native_output_audit_path": "C:/workspace/native_output_audit.json",
            "native_scf_status": "completed_below_max_cycles",
            "native_scf_last_iteration": 12,
            "native_scf_maximum_cycles_reached": False,
            "sampled_band_edge_status": "sampled_gap",
            "sampled_band_gap_ev": 1.12,
            "sampled_fermi_crossing_observed": False,
            "reported_band_gap_crosscheck_status": "within_tolerance",
            "reported_band_gap_difference_ev": 0.01,
            "sampled_band_edge_audit_after_execution": (
                "conditional_on_hash_bound_native_bands"
            ),
            "electronic_result_assessment_status": (
                "reported_band_gap_difference_review"
            ),
            "electronic_result_trust_status": (
                "sampled_evidence_review_required"
            ),
            "electronic_result_review_reasons": [
                "scientific_convergence_unverified",
                "reported_band_gap_difference",
            ],
            "electronic_result_recommended_action_id": (
                "review_reported_band_gap_difference"
            ),
            "electronic_result_recommended_tool": (
                "material_studio_castep_run_current"
            ),
            "electronic_result_recommended_preview_payload": {
                "project_id": "electronic_result",
                "task": "BandStructure",
                "execution_mode": "preview",
            },
            "castep_convergence_status": "pairwise_evidence_only",
            "castep_convergence_history_entry_count": 2,
            "castep_convergence_verified_point_count": 2,
            "castep_convergence_rejected_point_count": 0,
            "castep_convergence_series_count": 1,
            "castep_convergence_artifact_evidence_verified": True,
            "castep_parameter_sensitivity_evidence_verified": True,
            "castep_parameter_sensitivity_within_tolerance": None,
            "castep_convergence_review_reasons": [
                "scientific_convergence_unverified",
                "three_point_sequence_required",
            ],
            "castep_convergence_recommended_action_id": (
                "preview_refined_castep_parameter_point"
            ),
            "castep_convergence_recommended_tool": (
                "material_studio_castep_run_current"
            ),
            "castep_convergence_recommended_preview_payload": {
                "project_id": "electronic_result",
                "task": "Energy",
                "cutoff_energy_ev": 550,
                "execution_mode": "preview",
                "open_in_gui": False,
            },
            "derived_artifact_count": 1,
        },
        "compact",
    )

    assert compact["numeric_curve_data_exported"] is True
    assert compact["numeric_curve_kind"] == "native_castep_band_eigenvalues"
    assert compact["native_output_audit_status"] == "complete"
    assert compact["native_scf_status"] == "completed_below_max_cycles"
    assert compact["native_scf_last_iteration"] == 12
    assert compact["native_scf_maximum_cycles_reached"] is False
    assert compact["scientific_band_gap_verified"] is False
    assert compact["sampled_band_edge_status"] == "sampled_gap"
    assert compact["sampled_band_gap_ev"] == 1.12
    assert compact["sampled_fermi_crossing_observed"] is False
    assert compact["reported_band_gap_crosscheck_status"] == "within_tolerance"
    assert compact["reported_band_gap_difference_ev"] == 0.01
    assert compact["sampled_band_edge_audit_after_execution"] == (
        "conditional_on_hash_bound_native_bands"
    )
    assert compact["electronic_result_assessment_status"] == (
        "reported_band_gap_difference_review"
    )
    assert compact["electronic_result_trust_status"] == (
        "sampled_evidence_review_required"
    )
    assert "reported_band_gap_difference" in compact[
        "electronic_result_review_reasons"
    ]
    assert compact["electronic_result_recommended_action_id"] == (
        "review_reported_band_gap_difference"
    )
    assert compact["electronic_result_recommended_tool"] == (
        "material_studio_castep_run_current"
    )
    assert compact["electronic_result_recommended_preview_payload"][
        "execution_mode"
    ] == "preview"
    assert compact["castep_convergence_status"] == "pairwise_evidence_only"
    assert compact["castep_convergence_history_entry_count"] == 2
    assert compact["castep_convergence_verified_point_count"] == 2
    assert compact["castep_convergence_rejected_point_count"] == 0
    assert compact["castep_convergence_series_count"] == 1
    assert compact["castep_convergence_artifact_evidence_verified"] is True
    assert compact["castep_parameter_sensitivity_evidence_verified"] is True
    assert "castep_parameter_sensitivity_within_tolerance" not in compact
    assert "three_point_sequence_required" in compact[
        "castep_convergence_review_reasons"
    ]
    assert compact["castep_convergence_recommended_action_id"] == (
        "preview_refined_castep_parameter_point"
    )
    assert compact["castep_convergence_recommended_tool"] == (
        "material_studio_castep_run_current"
    )
    assert compact["castep_convergence_recommended_preview_payload"][
        "execution_mode"
    ] == "preview"
    assert compact["castep_convergence_recommended_preview_payload"][
        "open_in_gui"
    ] is False
    assert compact["derived_artifact_count"] == 1


def test_compact_capabilities_preserve_semiconductor_discovery() -> None:
    full = server.material_studio_live_capabilities()
    compact = server.material_studio_live_capabilities(response_mode="compact")

    assert full["ok"] is True
    assert compact["ok"] is True
    assert compact["response_mode"] == "compact"
    assert compact["response_schema"] == "material_studio_capabilities_compact_v2"
    assert compact["target_response_bytes"] == server.COMPACT_RESPONSE_TARGET_BYTES
    assert compact["max_response_bytes"] == server.COMPACT_RESPONSE_MAX_BYTES
    assert compact["full_detail_hint"]["arguments"] == {"response_mode": "full"}
    assert compact["recommended_kpoint_remediation_action_id"] == (
        "apply_recommended_semiconductor_kpoint_grid"
    )
    assert compact["recommended_calculation_settings_confirmation_field"] == (
        "confirm_recommended_calculation_settings"
    )
    assert (
        compact["recommended_calculation_settings_requires_explicit_confirmation"]
        is True
    )
    assert compact[
        "recommended_calculation_settings_receipt_recovery_field"
    ] == "recommended_calculation_settings_receipt_recovery"
    assert compact[
        "recommended_calculation_settings_receipt_recovery_policy"
    ]["invalid_receipts_are_not_restored"] is True
    assert compact["domain_focus"]["primary"] == "semiconductor materials"
    assert compact["domain_focus"]["semiconductor_template_count"] >= 50
    assert "silicon_diamond" in compact["domain_focus"]["semiconductor_template_ids"]
    assert compact["domain_focus"]["semiconductor_virtual_template_count"] >= 1
    assert "front" in compact["diagnostics"]["supported_view_names"]
    assert "electronic_structure_preflight" in compact["diagnostics"]["diagnostic_focus_ids"]
    assert "castep_electronic_results" in compact["diagnostics"][
        "diagnostic_focus_ids"
    ]
    assert "castep_convergence_series" in compact["diagnostics"][
        "diagnostic_focus_ids"
    ]
    convergence = compact["castep_electronic_calculation"][
        "convergence_audit"
    ]
    assert convergence["schema"] == "material_studio_castep_convergence_audit_v1"
    assert convergence["axes_compared_separately"] is True
    assert convergence["scientific_convergence_verified"] is False
    assert convergence["recommended_rerun_execution_mode"] == "preview"
    assert convergence["execute_requires_explicit_confirmation"] is True
    assert "diagnostic_focus_profiles" not in compact["diagnostics"]
    assert compact["diagnostics"]["diagnostic_focus_profile_count"] >= 20
    assert compact["natural_language"]["patch_command_count"] == len(
        compact["natural_language"]["patch_commands"]
    )
    assert any(
        command["template_id"]
        == "apply_recommended_semiconductor_kpoint_grid"
        for command in compact["natural_language"]["patch_commands"]
    )
    semiconductor_view_defaults = compact["natural_language"]["view_selection"][
        "semiconductor_domain_defaults"
    ]
    assert semiconductor_view_defaults["policy_version"] == 1
    assert semiconductor_view_defaults["explicit_views_override"] is True
    assert semiconductor_view_defaults["selection_precedence"] == [
        "interface_axis",
        "surface_axis",
        "lattice_family",
    ]
    assert semiconductor_view_defaults["cartesian_context_views"] == [
        "front",
        "top",
        "isometric",
    ]
    assert semiconductor_view_defaults["profiles"]["bulk"]["cubic"] == [
        "crystal_plane_100",
        "crystal_plane_110",
        "crystal_plane_111",
    ]
    assert all(
        "pattern" not in command
        for command in compact["natural_language"]["patch_commands"]
    )
    assert compact["gui"]["open_structure_policy"][
        "auto_launch_before_open_when_window_missing"
    ] is False
    assert "material_studio_live_modeling_request" == compact["live_entry_tool"]
    assert compact["visual_confirmation_entry"]["evidence_reaudit_receipt_field"] == (
        "gui_evidence_reaudit"
    )
    assert compact["visual_confirmation_entry"][
        "automatic_reaudit_does_not_imply_normality_request"
    ] is True
    assert compact["visual_confirmation_entry"]["report_writes_serialized"] is True
    assert compact["visual_confirmation_entry"]["report_write_lock_scope"] == (
        "project_revision"
    )
    assert compact["visual_confirmation_entry"]["report_json_atomic_publish"] is True
    assert compact["view_replay_confirmation_entry"]["payload_field"] == "view_replay_confirmation"
    assert compact["view_replay_confirmation_entry"]["execute_tool"] == (
        "material_studio_gui_execute_view_replay"
    )
    assert compact["view_replay_confirmation_entry"]["creates_revision"] is False
    assert compact["view_replay_confirmation_entry"]["evidence_reaudit_receipt_field"] == (
        "gui_evidence_reaudit"
    )
    assert compact["view_replay_confirmation_entry"][
        "reviewed_copy_script_evidence_required_when_source_selected"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "reviewed_copy_script_execution_allowed"
    ] is False
    assert compact["view_replay_confirmation_entry"][
        "evidence_integrity_reverified_on_status"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "evidence_integrity_failure_invalidates_visual_confirmation"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "event_journal_reconciled_on_status"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "event_journal_divergence_invalidates_visual_confirmation"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "prepare_and_record_writes_serialized"
    ] is True
    assert compact["view_replay_confirmation_entry"][
        "write_transaction_lock_scope"
    ] == "project_revision"
    assert compact["view_replay_confirmation_entry"][
        "write_transaction_lock_kernel_released_on_process_exit"
    ] is True
    assert compact["view_replay_automation_policy"]["automatic_native_view_names"] == [
        "front",
        "back",
        "right",
        "left",
        "top",
        "bottom",
        "isometric",
    ]
    assert compact["view_replay_automation_policy"]["local_uia_standard_view_names"] == [
        "front",
        "back",
        "right",
        "left",
        "top",
        "bottom",
    ]
    assert compact["view_replay_automation_policy"]["local_uia_isometric_supported"] is True
    isometric_contract = compact["view_replay_automation_policy"][
        "local_uia_isometric_execution_contract"
    ]
    assert isometric_contract["keyboard_stages"][1][
        "rotation_increment_ui_display_degrees"
    ] == 35.264
    assert isometric_contract["restore_rotation_increment_degrees"] == 45.0
    assert isometric_contract["movement_screen_factor"] == 2.0
    assert isometric_contract["movement_dialog_closed_after_restore"] is True
    replay_policy = compact["view_replay_automation_policy"]
    assert replay_policy["local_uia_miller_plane_supported"] is True
    assert replay_policy["local_uia_exact_collinear_direction_supported"] is True
    assert replay_policy["local_uia_non_collinear_direction_supported"] is False
    assert replay_policy["crystallographic_direction_review_gate_scope"] == (
        "non_collinear_only"
    )
    implementation = replay_policy["local_uia_implementation_contract"]
    assert implementation["recipe_classes"]["transactional_miller_plane"][
        "implemented"
    ] is True
    assert implementation["recipe_classes"][
        "exact_collinear_crystal_direction"
    ]["eligibility_status"] == "exact_integer_plane_collinear"
    assert compact["view_replay_automation_policy"]["local_uia_records_visual_acceptance"] is False
    assert compact["view_replay_automation_policy"]["documented_keyboard_sequences"]["right"] == [
        "Up",
        "Up",
        "Left",
        "Left",
    ]
    assert compact["view_replay_automation_policy"]["documented_keyboard_sequences"]["bottom"] == [
        "Left",
        "Left",
        "Left",
        "Left",
        "Down",
        "Down",
    ]
    assert compact["view_replay_automation_policy"]["reviewed_standard_view_names"] == []
    assert compact["view_replay_automation_policy"]["documented_staged_keyboard_sequences"][
        "isometric"
    ][1]["key_sequence"] == ["Down"]
    assert compact["view_replay_automation_policy"]["staged_keyboard_restore"][
        "rotation_increment_degrees"
    ] == 45.0
    assert compact["view_replay_automation_policy"]["shift_arrow_keys_allowed"] is False
    assert compact["view_replay_automation_policy"]["front_native_command_id"] == "cmdViewer3DResetView"
    assert compact["view_replay_automation_policy"][
        "standard_views_require_current_bound_runtime_accessibility_preflight"
    ] is True
    assert compact["view_replay_automation_policy"][
        "runtime_accessibility_preflight_payload_field"
    ] == "runtime_accessibility_evidence"
    assert compact["view_replay_automation_policy"][
        "standard_view_static_registry_or_help_evidence_alone_is_sufficient"
    ] is False
    assert compact["view_replay_automation_policy"][
        "verified_anonymous_toolbar_mapping_supported"
    ] is True
    assert compact["view_replay_automation_policy"][
        "verified_anonymous_toolbar_requires_exact_child_count_order_roles"
    ] is True
    assert compact["view_replay_automation_policy"][
        "verified_anonymous_toolbar_record_receipt_field"
    ] == "accessibility_command_uses"
    assert compact["view_replay_automation_policy"][
        "verified_visual_postcheck_failure_suppresses_automatic_retry"
    ] is True
    assert compact["view_replay_automation_policy"][
        "automatic_postcheck_suppression_requires_integrity_verified_evidence"
    ] is True
    assert compact["view_replay_automation_policy"][
        "failed_reset_baseline_suppresses_dependent_recipes"
    ] is True
    assert compact["view_replay_automation_policy"][
        "failed_reset_baseline_only_blocks_final_camera_dependencies"
    ] is True
    assert compact["view_replay_automation_policy"][
        "postcheck_failure_clear_requires_integrity_verified_success"
    ] is True
    assert compact["view_replay_automation_policy"][
        "miller_view_onto_requires_bound_reset_accessibility_preflight"
    ] is False
    assert compact["view_replay_automation_policy"][
        "miller_view_onto_accepts_verified_anonymous_reset_target"
    ] is False
    assert compact["view_replay_automation_policy"][
        "miller_view_onto_final_camera_depends_on_reset_orientation"
    ] is False
    assert compact["view_replay_automation_policy"][
        "miller_view_onto_final_camera_command_id"
    ] == "cmdViewer3DViewOnto"
    assert compact["view_replay_automation_policy"][
        "client_asserted_command_to_element_mapping_allowed"
    ] is False
    assert compact["view_replay_automation_policy"]["structure_nudge_or_align_commands_allowed_for_camera_replay"] is False
    assert _json_size(compact) < server.COMPACT_RESPONSE_MAX_BYTES
    assert _json_size(compact) * 4 < _json_size(full)


def test_compact_capabilities_preserve_requested_runtime_status(monkeypatch) -> None:
    monkeypatch.setattr(
        server.runner,
        "status",
        lambda: {
            "connected": True,
            "runner": "C:/Materials Studio/RunMatScript.bat",
            "runner_exists": True,
            "runner_source": "environment",
            "workspace_root": "C:/workspace",
            "default_timeout_seconds": 3600,
            "extra_runner_args": [],
            "searched_candidates": [f"candidate-{index}" for index in range(100)],
            "searched_candidate_count": 100,
            "notes": [],
        },
    )

    class _Controller:
        def status(self) -> dict[str, object]:
            return {
                "ok": True,
                "supported": True,
                "status": "ready_for_same_window_live_edit",
                "recommended_tool": "material_studio_gui_snapshot",
                "recommended_action": "snapshot_target_project_window",
                "process_count": 1,
                "window_found": True,
                "window_count": 1,
                "live_window_count": 1,
                "selected_window_handle": 303,
                "single_window_policy_ok": True,
                "single_window_violation_reasons": [],
                "local_uia_view_replay_supported": True,
                "local_uia_view_replay_unavailable_reason": None,
                "local_uia_view_replay_view_names": ["front", "isometric"],
                "local_uia_miller_plane_transaction_supported": True,
                "local_uia_exact_collinear_direction_transaction_supported": True,
                "local_uia_non_collinear_direction_transaction_supported": False,
                "local_uia_view_replay_runtime": {
                    "status": "transactional_miller_available",
                    "backend_supported": True,
                    "transactional_miller_supported": True,
                    "exact_collinear_direction_supported": True,
                    "non_collinear_direction_supported": False,
                    "single_window_policy_ok": True,
                },
                "workspace_root": "C:/workspace",
                "windows": [{"handle": 303}, {"handle": 404}],
                "window_management": {
                    "status": "ready_for_same_window_live_edit",
                    "process_count": 1,
                    "window_count": 1,
                    "blocking_dialog_count": 0,
                    "selected_window_handle": 303,
                    "selected_window_title": "msmcp_r004 - Materials Studio",
                    "selected_window_project_id": "project",
                    "selected_window_revision": 4,
                    "target_window_handle": 303,
                    "target_window_title": "msmcp_r004 - Materials Studio",
                    "target_window_project_id": "project",
                    "target_window_revision": 4,
                    "target_window_is_selected": True,
                    "target_window_is_visible": True,
                    "target_window_is_minimized": False,
                    "target_window_is_foreground": True,
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "ready_for_next_live_edit": True,
                    "recommended_tool": "material_studio_gui_snapshot",
                    "recommended_action": "snapshot_target_project_window",
                },
            }

    monkeypatch.setattr(server, "_gui_controller", lambda _working_dir: _Controller())

    compact = server.material_studio_live_capabilities(
        include_status=True,
        response_mode="compact",
    )

    assert compact["runner_status"]["connected"] is True
    assert "searched_candidates" not in compact["runner_status"]
    assert compact["runner_status"]["searched_candidate_count"] == 100
    assert compact["gui_status"]["process_count"] == 1
    assert "windows" not in compact["gui_status"]
    assert compact["gui_status"][
        "local_uia_miller_plane_transaction_supported"
    ] is True
    runtime = compact["view_replay_runtime_availability"]
    assert runtime["status"] == "transactional_miller_available"
    assert runtime["transactional_miller_implemented"] is True
    assert runtime["transactional_miller_supported"] is True
    assert runtime["exact_collinear_direction_supported"] is True
    assert runtime["non_collinear_direction_supported"] is False
    assert runtime["session_gate_ready"] is True
    assert runtime["execution_requires_project_recipe_preflight"] is True
    assert runtime["post_action_visual_confirmation_required"] is True
    assert _json_size(compact) < server.COMPACT_RESPONSE_MAX_BYTES


def test_view_replay_runtime_availability_fails_closed_without_miller_backend() -> None:
    not_probed = server._view_replay_runtime_availability(None)
    assert not_probed["status"] == "not_probed"
    assert not_probed["observed"] is False
    assert not_probed["transactional_miller_implemented"] is True

    standard_only = server._view_replay_runtime_availability(
        {
            "local_uia_view_replay_supported": True,
            "local_uia_miller_plane_transaction_supported": False,
            "single_window_policy_ok": True,
            "process_count": 1,
            "window_count": 1,
        }
    )
    assert standard_only["status"] == "standard_and_isometric_only"
    assert standard_only["transactional_miller_implemented"] is True
    assert standard_only["transactional_miller_supported"] is False
    assert standard_only["exact_collinear_direction_supported"] is False
    assert standard_only["session_gate_ready"] is True


def test_compact_capabilities_expose_crystallographic_and_oriented_views() -> None:
    compact = server.material_studio_live_capabilities(response_mode="compact")

    direction_views = compact["diagnostics"]["crystallographic_direction_views"]
    plane_views = compact["diagnostics"]["crystallographic_plane_views"]
    oriented_views = compact["diagnostics"]["semiconductor_oriented_frame_views"]

    assert "crystal_001" in direction_views
    assert direction_views["crystal_001"]["indices"] == [0, 0, 1]
    assert direction_views["crystal_001"]["label"] == "[001]"
    assert "crystal_plane_111" in plane_views
    assert plane_views["crystal_plane_111"]["indices"] == [1, 1, 1]
    assert plane_views["crystal_plane_111"]["label"] == "(111)"
    assert "surface_normal" in oriented_views
    assert oriented_views["surface_normal"]["required_metadata_field"] == "surface_axis"
    assert "interface_normal" in oriented_views
    assert oriented_views["interface_normal"]["required_metadata_field"] == "interface_axis"


def test_compact_live_workflow_keeps_full_reports_and_view_parameters(tmp_path: Path) -> None:
    views = ["front", "back", "right", "left", "top", "bottom", "isometric"]
    created = server.material_studio_live_modeling_request(
        "Build silicon crystal, export all view parameters, and check whether the model is normal.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        views=views,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert created["ok"] is True
    assert created["response_mode"] == "compact"
    assert created["response_schema"] == "material_studio_live_compact_v2"
    assert created["execution_mode"] == "preview"
    assert created["nl_plan"]["template_id"] == "silicon_diamond"
    assert "supported_templates" not in created["nl_plan"]
    assert Path(created["report_json_path"]).exists()
    assert Path(created["view_audit_report_path"]).exists()
    assert Path(created["view_bundle_manifest_path"]).exists()
    assert created["view_parameter_summary"]["view_names"] == views
    assert created["view_parameter_summary"]["view_count"] == 7
    assert created["view_bundle_row_counts"]["view_summary"] == 7
    assert created["view_bundle_row_counts"]["view_projections"] == 56
    assert "evidence" not in created["normality_gate"]
    assert "artifacts" not in created["next_action_plan"]
    assert all(
        "artifacts" not in focus and "row_counts" not in focus
        for focus in created["requested_diagnostic_focus_status"]["focuses"]
    )
    assert "modeling_report" not in created
    assert "view_audit" not in created
    assert _json_size(created) < server.COMPACT_RESPONSE_MAX_BYTES

    report_path = Path(created["report_json_path"])
    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(persisted["modeling_report"], dict)
    view_audit_path = Path(created["view_audit_report_path"])
    assert view_audit_path.exists()
    assert isinstance(json.loads(view_audit_path.read_text(encoding="utf-8")), dict)
    assert report_path.stat().st_size > _json_size(created)


    project_id = created["project_id"]
    compact_status = server.material_studio_live_project_status(
        project_id=project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    full_status = server.material_studio_live_project_status(
        project_id=project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
    )
    assert compact_status["ok"] is True
    assert compact_status["response_mode"] == "compact"
    assert compact_status["project_resolution"]["source"] == "explicit"
    assert compact_status["live_summary"]["project_resolution"]["source"] == "explicit"
    assert compact_status["live_summary"]["view_names"] == views
    assert compact_status["view_parameter_summary"]["views"][0]["camera_direction"] == [0.0, 0.0, 1.0]
    assert compact_status["gui_current_revision_status"] == "not_hot_loaded"
    assert compact_status["gui_current_revision_needs_snapshot"] is False
    assert compact_status.get("gui_current_revision_single_window_policy_ok") is None
    assert compact_status.get("gui_view_replay_status") is None
    assert compact_status["gui_current_revision_recommended_tool"] == "material_studio_gui_apply_current_revision"
    assert compact_status.get("gui_current_revision_target_window_handle") is None
    assert compact_status["live_hotload_model_ready"] is True
    assert compact_status["live_hotload_gui_preflight_verified"] is False
    assert compact_status["live_hotload_gui_preflight_required"] is True
    assert compact_status["live_hotload_gui_preflight_reasons"] == [
        "gui_status_not_probed",
        "single_window_policy_not_verified",
    ]
    assert compact_status["live_hotload_safe_to_attempt"] is False
    assert compact_status["live_hotload_status"] == "gui_preflight_required"
    assert compact_status["live_hotload_recommended_tool"] == "material_studio_gui_status"
    assert compact_status["mcp_model_ready_for_hotload"] is True
    assert compact_status["mcp_gui_preflight_verified"] is False
    assert compact_status["mcp_gui_preflight_required"] is True
    assert compact_status["mcp_same_window_hotload_ready"] is False
    assert compact_status["mcp_same_window_hotload_tool"] == "material_studio_gui_status"
    gui_current_revision = compact_status["gui_current_revision"]
    assert gui_current_revision["view_audit_report_path"] == compact_status["view_audit_report_path"]
    assert gui_current_revision["view_audit_report_exists"] is True
    assert gui_current_revision["view_audit_report_path_source"] == "diagnostics"
    assert gui_current_revision["report_json_path"] == compact_status["report_json_path"]
    assert gui_current_revision["report_json_exists"] is True
    assert gui_current_revision["report_json_path_source"] == "diagnostics"
    assert (
        gui_current_revision["view_bundle_manifest_path"]
        == full_status["modeling_report"]["diagnostics"]["view_bundle_manifest_path"]
    )
    assert gui_current_revision["view_bundle_manifest_exists"] is True
    assert gui_current_revision["view_bundle_manifest_path_source"] == "diagnostics"
    assert compact_status["view_bundle_manifest_path"] == compact_status["view_bundle_files"][
        "diagnostic_export_manifest_json"
    ]
    assert compact_status["view_bundle_files_complete"] is True
    assert compact_status["view_bundle_files_existing_count"] == (
        compact_status["view_bundle_files_total_count"]
    )
    assert compact_status["view_bundle_files_missing_count"] == 0
    assert compact_status["view_bundle_file_index_entry_count_in_response"] == len(
        compact_status["view_bundle_files"]
    )
    assert compact_status["view_bundle_file_index_complete_in_response"] is False
    assert compact_status["view_bundle_file_index_compacted"] is True
    assert compact_status["live_summary"]["view_bundle_files_complete"] is True
    assert compact_status["live_summary"]["view_bundle_files_missing_count"] == 0
    assert compact_status["live_summary"]["view_bundle_file_index_compacted"] is True
    assert compact_status["live_summary"]["gui_current_revision_view_audit_report_exists"] is True
    assert compact_status["live_summary"]["gui_current_revision_report_json_exists"] is True
    assert compact_status["live_summary"]["gui_current_revision_view_bundle_manifest_exists"] is True
    assert "modeling_report" not in compact_status
    assert isinstance(full_status["modeling_report"], dict)
    assert isinstance(full_status["view_audit"], dict)
    assert _json_size(compact_status) < server.COMPACT_RESPONSE_MAX_BYTES
    assert _json_size(compact_status) * 10 < _json_size(full_status)

    bundle = server.material_studio_model_export_view_bundle(
        project_id=project_id,
        views=views,
        include_gui_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert bundle["ok"] is True
    assert bundle["response_mode"] == "compact"
    assert bundle["view_bundle_row_counts"]["view_summary"] == 7
    assert bundle["view_bundle_row_counts"]["view_projections"] == 56
    assert Path(bundle["view_bundle_manifest_path"]).exists()
    assert Path(bundle["artifacts"]["view_summary_csv"]).exists()
    assert Path(bundle["artifacts"]["view_projections_csv"]).exists()
    assert "modeling_issue_index_json" not in bundle["artifacts"]
    assert Path(bundle["view_bundle_files"]["modeling_issue_index_json"]).exists()
    assert bundle["view_bundle_files_complete"] is True
    assert bundle["view_bundle_files_existing_count"] == bundle[
        "view_bundle_files_total_count"
    ]
    assert bundle["view_bundle_files_missing_count"] == 0
    assert bundle["view_bundle_file_index_compacted"] is True
    assert "modeling_report" not in bundle
    assert _json_size(bundle) < server.COMPACT_RESPONSE_MAX_BYTES

    updated = server.material_studio_live_modeling_request(
        "Set CASTEP cutoff energy to 600 eV and export all view parameters.",
        project_id=project_id,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert updated["ok"] is True
    assert updated["response_mode"] == "compact"
    assert updated["revision"] == 1
    assert updated["revision_delta"]["simulation"]["changed_fields"] == ["cutoff_energy_ev"]
    assert "modeling_report" not in updated
    assert _json_size(updated) < server.COMPACT_RESPONSE_MAX_BYTES

    gui_preview = server.material_studio_gui_apply_current_revision(
        project_id=project_id,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert gui_preview["ok"] is True
    assert gui_preview["response_mode"] == "compact"
    assert gui_preview["execution_mode"] == "preview"
    assert gui_preview["revision"] == 1
    assert not Path(gui_preview["planned_outputs"]["structure"]).exists()
    assert "modeling_report" not in gui_preview
    assert _json_size(gui_preview) < server.COMPACT_RESPONSE_MAX_BYTES


def test_semiconductor_default_view_selection_survives_full_and_compact_live_responses(
    tmp_path: Path,
) -> None:
    expected_views = [
        "front",
        "top",
        "isometric",
        "crystal_plane_100",
        "crystal_plane_110",
        "crystal_plane_111",
    ]
    created = server.material_studio_live_modeling_request(
        "Build silicon crystal, export diagnostics, and check whether the model is normal.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
        response_mode="full",
    )

    assert created["ok"] is True
    assert created["view_selection"]["selection_profile"] == "semiconductor_bulk_cubic"
    assert created["view_selection"]["view_names"] == expected_views
    assert created["view_audit"]["view_selection"] == created["view_selection"]
    assert created["modeling_report"]["view_selection"] == created["view_selection"]
    assert created["modeling_report"]["view_review"]["view_selection"] == created[
        "view_selection"
    ]
    assert created["view_parameter_summary"]["view_selection"] == created[
        "view_selection"
    ]
    assert created["live_summary"]["view_selection"] == created["view_selection"]
    assert created["view_parameter_summary"]["view_names"] == expected_views

    compact_status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    compact_selection = compact_status["view_parameter_summary"]["view_selection"]
    assert compact_selection["source"] == "semiconductor_domain_default"
    assert compact_selection["selection_profile"] == "semiconductor_bulk_cubic"
    assert compact_selection["view_names"] == expected_views
    assert compact_status["view_parameter_summary"]["view_names"] == expected_views
    assert _json_size(compact_status) < server.COMPACT_RESPONSE_MAX_BYTES


def test_compact_live_status_uses_latest_current_project_resolution(tmp_path: Path) -> None:
    created = server.material_studio_live_modeling_request(
        "Build silicon crystal and prepare preview.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert created["ok"] is True

    status = server.material_studio_live_project_status(
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert status["ok"] is True
    assert status["project_id"] == created["project_id"]
    assert status["project_resolution"]["source"] == "latest_current"
    assert status["live_summary"]["project_resolution"]["source"] == "latest_current"
    assert status["gui_current_revision_status"] == "not_hot_loaded"
    assert status["gui_current_revision_needs_snapshot"] is False


def test_compact_slab_status_preserves_actionable_kpoint_repair(tmp_path: Path) -> None:
    created = server.material_studio_live_modeling_request(
        "Build a MoS2 monolayer for semiconductor calculation preflight.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert created["ok"] is True
    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert status["ok"] is True
    action = status["next_action_plan"]
    assert action["action_id"] == "apply_recommended_semiconductor_kpoint_grid"
    assert action["recommended_tool"] == "material_studio_live_update_with_patch"
    assert action["needs_user_confirmation"] is True
    assert action["safe_to_call_without_confirmation"] is False
    assert action["payload_hint"]["open_in_gui"] is False
    assert action["payload_hint"]["execution_mode"] == "preview"
    assert action["payload_hint"]["remediation_intent"] == (
        "apply_recommended_semiconductor_kpoint_grid"
    )
    assert action["payload_hint"]["confirm_recommended_calculation_settings"] is False
    assert action["payload_hint"]["patch"] == {
        "project_id": created["project_id"],
        "base_revision": 0,
        "operations": [
            {
                "type": "set_castep_energy",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "kpoints": [29, 29, 1],
                "cutoff_energy_ev": 600,
            }
        ],
        "execution_mode": "preview",
    }
    assert _json_size(status) < server.COMPACT_RESPONSE_MAX_BYTES


def test_compact_stale_semiconductor_status_keeps_repairable_edit_contract(tmp_path: Path) -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    doped = ModelSpec.model_validate(plan.payload)
    stale_atoms = [
        atom.model_copy(update={"element": "Si"}) if atom.id == "Si1_000" else atom
        for atom in doped.model.basis_atoms
    ]
    stale = doped.model_copy(update={"model": doped.model.model_copy(update={"basis_atoms": stale_atoms})})
    created = server.material_studio_model_create_from_spec(
        stale.model_dump(mode="json"),
        user_text="Legacy stale dopant metadata fixture.",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    status = server.material_studio_live_project_status(
        project_id=stale.project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert status["ok"] is True
    assert status["normality"] == "failed"
    assert status["ready_for_next_edit"] is True
    assert status["next_edit_status"] == "ready_with_reaudit"
    assert status["next_edit_requires_reaudit"] is True
    assert status["next_edit_blocking_reasons"] == []
    assert status["ready_for_calculation"] is False
    readiness = status["mcp_client_readiness"]
    assert readiness["can_accept_followup_request"] is True
    assert readiness["next_edit_requires_reaudit"] is True
    assert readiness["model_trust_blocked"] is True
    assert status["next_action_plan"]["action_id"] == "reconcile_dopant_metadata"
    assert status["next_action_plan"]["needs_user_confirmation"] is True
    assert status["semiconductor_normality_diagnosis"]["recommended_tool"] == (
        "material_studio_project_reconcile_dopant_metadata"
    )
    assert _json_size(status) < server.COMPACT_RESPONSE_MAX_BYTES


def test_compact_status_preserves_bound_nondefault_views_and_rejects_stale_audit(
    tmp_path: Path,
) -> None:
    views = ["crystal_001", "crystal_plane_111", "isometric"]
    created = server.material_studio_live_modeling_request(
        "Build silicon crystal and export [001], (111), and isometric view parameters.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        views=views,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert created["ok"] is True

    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert status["view_audit_source"] == (
        "computed_status_with_persisted_view_selection"
    )
    assert status["persisted_view_audit_matches_current"] is True
    assert status["persisted_view_audit_mismatch_reasons"] == []
    assert status["view_parameter_summary"]["view_names"] == views
    assert [view["name"] for view in status["view_parameter_summary"]["views"]] == views

    audit_path = Path(created["view_audit_report_path"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["spec_fingerprint"] = "stale-fingerprint"
    stale_bytes = json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8")
    audit_path.write_bytes(stale_bytes)

    fallback = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert fallback["view_audit_source"] == "computed_status_default_views"
    assert fallback["persisted_view_audit_matches_current"] is False
    assert "spec_fingerprint_mismatch" in fallback[
        "persisted_view_audit_mismatch_reasons"
    ]
    assert fallback["view_parameter_summary"]["view_names"] == [
        "front",
        "top",
        "isometric",
        "crystal_plane_100",
        "crystal_plane_110",
        "crystal_plane_111",
    ]
    assert fallback["view_parameter_summary"]["view_selection"]["source"] == (
        "semiconductor_domain_default"
    )
    assert fallback["view_parameter_summary"]["view_selection"]["selection_profile"] == (
        "semiconductor_bulk_cubic"
    )
    assert audit_path.read_bytes() == stale_bytes


def test_compact_semiconductor_stress_receipt_stays_within_budget(
    tmp_path: Path,
) -> None:
    capabilities = server.material_studio_live_capabilities(response_mode="compact")
    views = capabilities["diagnostics"]["supported_view_names"]
    created = server.material_studio_live_modeling_request(
        "Build an AlGaN/GaN HEMT and export all model parameters, 2DEG diagnostics, "
        "interface diagnostics, calculation preflight, and check whether the model is normal.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        views=views,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert created["ok"] is True
    assert _json_size(created) < server.COMPACT_RESPONSE_MAX_BYTES
    assert created["response_compaction"]["hard_budget_applied"] is False
    assert created["response_compaction"]["omitted_fields"] == []
    assert created["response_compaction"]["details_persisted"] is True
    assert created["live_summary"]["normality_check_requested"] is True
    assert created["visual_normality_summary"]["available"] is True
    assert created["view_parameter_summary"]["view_count"] == len(views)
    assert created["normality_gate"]["available"] is True
    assert created["mcp_client_readiness"]["can_accept_followup_request"] is True
    assert created["semiconductor_normality_diagnosis"]["available"] is True
    assert created["diagnostic_focus_plan"]["available"] is True
    assert len(created["view_parameter_summary"]["views"]) == len(views)
    assert Path(created["view_bundle_manifest_path"]).exists()


def test_compact_dopant_reconcile_response_mode_preserves_receipt(tmp_path: Path) -> None:
    plan = infer_modeling_plan("Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview.")
    spec = ModelSpec.model_validate(plan.payload)
    stale_atoms = [
        atom.model_copy(update={"element": "Si"}) if atom.id == "Si1_000" else atom
        for atom in spec.model.basis_atoms
    ]
    stale = spec.model_copy(update={"model": spec.model.model_copy(update={"basis_atoms": stale_atoms})})
    created = server.material_studio_model_create_from_spec(stale.model_dump(mode="json"), working_dir=str(tmp_path))
    assert created["ok"] is True

    reconciled = server.material_studio_project_reconcile_dopant_metadata(
        project_id=stale.project_id,
        confirm_metadata_reconciliation=True,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert reconciled["ok"] is True
    assert reconciled["response_mode"] == "compact"
    assert reconciled["response_schema"] == "material_studio_live_compact_v2"
    assert reconciled["workflow"] == "dopant_metadata_reconcile"
    assert reconciled["reconciliation_status"] == "reconciled"
    assert reconciled["metadata_reconciliation"]["structure_unchanged"] is True
    assert reconciled["metadata_reconciliation"]["simulation_unchanged"] is True
    assert "modeling_report" not in reconciled
    assert _json_size(reconciled) < server.COMPACT_RESPONSE_MAX_BYTES


def test_compact_live_workflow_preserves_normality_diagnostics(tmp_path: Path) -> None:
    inspected = server.material_studio_live_modeling_request(
        "Build silicon crystal and export current view parameters and check whether the model is normal.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        views=["front", "back", "right", "left", "top", "bottom", "isometric"],
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert inspected["ok"] is True
    assert inspected["response_mode"] == "compact"
    assert inspected["diagnostic_export_requested"] is True
    assert inspected["normality_check_requested"] is True
    assert inspected["view_bundle_manifest_path"] == inspected["view_bundle_files"]["diagnostic_export_manifest_json"]
    assert Path(inspected["view_bundle_manifest_path"]).exists()
    assert Path(inspected["view_bundle_files"]["view_quality_csv"]).exists()
    assert Path(inspected["view_bundle_files"]["modeling_report_summary_csv"]).exists()
    assert inspected["live_summary"]["diagnostic_export_requested"] is True
    assert inspected["live_summary"]["normality_check_requested"] is True
    assert inspected["live_summary"]["view_bundle_manifest_path"] == inspected["view_bundle_manifest_path"]
    assert inspected["live_summary"]["view_bundle_manifest_path"] == inspected["view_bundle_files"]["diagnostic_export_manifest_json"]
    assert inspected["live_summary"]["view_bundle_row_counts"]["view_summary"] == 7
    assert "modeling_report" not in inspected
    assert "view_audit" not in inspected


def test_compact_live_status_surfaces_view_replay_continuation() -> None:
    response = {
        "gui_view_replay": {
            "replay_status": "externally_confirmed",
            "replay_continuation": {
                "status": "automatic_recipe_ready",
                "next_pending_view_name": "front",
                "next_actionable_pending_view_name": "right",
                "next_automation_ready_view_name": "right",
                "execution_recipe_ref": (
                    "replay_continuation.next_view.execution_recipe"
                ),
                "gui_input_required": True,
                "post_action_observation_required": True,
                "record_call_ready": False,
                "record_tool": "material_studio_gui_record_view_replay",
                "payload_hint": {
                    "project_id": "project",
                    "revision": 4,
                    "view_name": "right",
                    "execution_recipe_ref": (
                        "replay_continuation.next_view.execution_recipe"
                    ),
                },
                "payload_hint_is_directly_callable": False,
                "post_action_record_payload_template": {
                    "project_id": "project",
                    "revision": 4,
                    "view_name": "right",
                    "model_visible": None,
                    "camera_matches_manifest": None,
                },
                "post_action_record_payload_template_is_directly_callable": False,
                "post_action_required_observation_fields": [
                    "model_visible",
                    "camera_matches_manifest",
                ],
                "evidence_values_must_be_observed_not_assumed": True,
                "next_view": {
                    "view_name": "crystal_plane_100",
                    "execution_recipe": {
                        "schema_version": 8,
                        "recipe_kind": "miller_plane_view_onto",
                        "automation_ready": True,
                        "supporting_native_command_ids": [
                            "cmdSymmetryBuilderMillerPlanes",
                            "cmdGPEToggleExplorer",
                            "cmdViewer3DSelection",
                        ],
                        "camera_result_depends_on_reset_baseline": False,
                        "camera_result_established_by": (
                            "native_miller_plane_view_onto"
                        ),
                        "reset_view_role": (
                            "forbidden_because_ms_20_1_reset_is_not_reliably_undoable"
                        ),
                        "pre_action_view_baseline_required": True,
                        "reset_view_allowed": False,
                        "final_camera_established_by_native_command_id": (
                            "cmdViewer3DViewOnto"
                        ),
                    },
                },
            },
        }
    }

    compact = server._compact_live_response(response, server.McpResponseMode.COMPACT)

    assert compact["gui_view_replay_status"] == "externally_confirmed"
    assert compact["view_replay_continuation"]["status"] == "automatic_recipe_ready"
    assert compact["view_replay_continuation"]["next_pending_view_name"] == "front"
    assert (
        compact["view_replay_continuation"][
            "next_actionable_pending_view_name"
        ]
        == "right"
    )
    assert compact["view_replay_continuation"]["next_automation_ready_view_name"] == "right"
    compact_gui = server._compact_gui_view_replay(response["gui_view_replay"])
    compact_continuation = compact_gui["replay_continuation"]
    assert compact_continuation["gui_input_required"] is True
    assert compact_continuation["post_action_observation_required"] is True
    assert compact_continuation["record_call_ready"] is False
    assert compact["view_replay_continuation"]["payload_hint"] == {
        "project_id": "project",
        "revision": 4,
        "view_name": "right",
        "execution_recipe_ref": (
            "replay_continuation.next_view.execution_recipe"
        ),
    }
    assert compact_continuation["payload_hint_is_directly_callable"] is False
    assert compact_continuation["post_action_record_payload_template"] == {
        "project_id": "project",
        "revision": 4,
        "view_name": "right",
        "model_visible": None,
        "camera_matches_manifest": None,
    }
    assert compact_continuation[
        "post_action_record_payload_template_is_directly_callable"
    ] is False
    nested_continuation = compact["gui_view_replay"]["replay_continuation"]
    assert nested_continuation["gui_input_required"] is True
    assert nested_continuation["post_action_observation_required"] is True
    assert nested_continuation["record_call_ready"] is False
    assert nested_continuation["payload_hint_is_directly_callable"] is False
    assert nested_continuation["continuation_detail_ref"] == (
        "view_replay_continuation"
    )
    assert nested_continuation["post_action_record_payload_template_ref"] == (
        "view_replay_continuation.post_action_record_payload_template"
    )
    recipe = compact["view_replay_continuation"]["next_view"][
        "execution_recipe"
    ]
    assert recipe["supporting_native_command_ids"] == [
        "cmdSymmetryBuilderMillerPlanes",
        "cmdGPEToggleExplorer",
        "cmdViewer3DSelection",
    ]
    assert recipe["camera_result_depends_on_reset_baseline"] is False
    assert recipe["camera_result_established_by"] == (
        "native_miller_plane_view_onto"
    )
    assert recipe["reset_view_role"] == (
        "forbidden_because_ms_20_1_reset_is_not_reliably_undoable"
    )
    assert recipe["pre_action_view_baseline_required"] is True
    assert recipe["reset_view_allowed"] is False
    assert recipe["final_camera_established_by_native_command_id"] == (
        "cmdViewer3DViewOnto"
    )


def test_compact_completed_view_replay_omits_only_inert_action_detail() -> None:
    terminal_continuation = {
        "status": "complete",
        "next_pending_view_name": None,
        "next_actionable_pending_view_name": None,
        "next_automation_ready_view_name": None,
        "recommended_action": (
            "review_current_revision_after_all_prepared_views_were_confirmed"
        ),
        "recommended_mcp_tool": "material_studio_live_project_status",
        "recommended_executor": None,
        "automatic_replay_ready": False,
        "gui_input_required": False,
        "post_action_observation_required": False,
        "record_call_ready": False,
        "recipe_upgrade_required": False,
        "current_camera_evidence_reverification_required": False,
        "evidence_integrity_reverification_required": False,
        "event_journal_reverification_required": False,
        "journal_consistency_status": "consistent",
        "runtime_ui_preflight_required": False,
        "runtime_accessibility_preflight_required": False,
        "runtime_accessibility_observation_blocks_automation": False,
        "payload_hint": {
            "project_id": "semiconductor_project",
            "revision": 4,
            "view_name": None,
            "model_visible": None,
            "camera_matches_manifest": None,
            "reviewed_copy_script_evidence": {
                "script_text": None,
                "capture_method": None,
                "reviewer": None,
                "copy_script_command_observed": None,
                "review_completed": None,
                "view_action_matches_manifest": None,
                "structure_unchanged_observed": None,
                "note": None,
            },
        },
        "payload_hint_is_directly_callable": False,
        "post_action_record_payload_template": {
            "project_id": "semiconductor_project",
            "revision": 4,
            "view_name": None,
            "model_visible": None,
            "camera_matches_manifest": None,
        },
        "post_action_record_payload_template_is_directly_callable": False,
        "post_action_high_level_payload_template": {},
        "post_action_required_observation_fields": [],
        "post_review_record_payload_template": None,
        "post_review_record_payload_template_is_directly_callable": False,
        "post_review_high_level_payload_template": None,
        "evidence_values_must_be_observed_not_assumed": True,
        "execution_action": None,
        "next_view": None,
    }
    response = {
        "ok": True,
        "project_id": "semiconductor_project",
        "revision": 4,
        "gui_view_replay": {
            "project_id": "semiconductor_project",
            "revision": 4,
            "replay_status": "externally_confirmed",
            "view_names": ["front", "back", "right", "left", "top"],
            "requested_view_count": 5,
            "supported_view_count": 5,
            "view_selection": {
                "source": "explicit_request",
                "policy_applied": False,
                "explicit_views_provided": True,
                "model_type": "crystal",
                "domain": "semiconductor",
                "semiconductor_domain": True,
                "selection_profile": "explicit_request",
                "lattice_family": "hexagonal",
                "orientation_kind": "surface",
                "orientation_axis": "c",
                "view_names": ["front", "back", "right", "left", "top"],
                "view_count": 5,
                "reason_codes": ["explicit_views_preserved"],
                "explicit_views_override_domain_defaults": True,
                "cartesian_context_views": ["front", "top"],
                "domain_diagnostic_views": ["surface_normal"],
                "suggested_default_view_names": ["front", "top", "isometric"],
            },
            "replay_summary": {
                "event_count": 7,
                "accepted_event_count": 6,
                "trusted_accepted_event_count": 6,
                "accepted_view_count": 5,
                "accepted_view_names": [
                    "back",
                    "front",
                    "left",
                    "right",
                    "top",
                ],
                "evidence_integrity_status": "verified",
                "journal_consistency_status": "consistent",
                "pending_view_count": 0,
                "pending_view_names": [],
                "current_camera_evidence_reverification_view_count": 0,
                "automation_ready_pending_view_count": 0,
                "review_required_pending_view_count": 0,
                "raw_accepted_event_count": 6,
                "raw_accepted_view_count": 5,
                "all_requested_views_accepted": True,
            },
            "replay_continuation": terminal_continuation,
            "next_action": {
                "continuation_status": "complete",
                "recommended_tool": "material_studio_live_project_status",
                "recommended_action": terminal_continuation[
                    "recommended_action"
                ],
                "payload_hint": terminal_continuation["payload_hint"],
                "payload_hint_is_directly_callable": False,
                "gui_input_required": False,
                "post_action_observation_required": False,
                "source": "replay_continuation",
            },
            "next_action_resolution": {
                "status": "continuation_safety_action_already_current",
                "authoritative_source": "replay_continuation",
                "continuation_status": "complete",
                "incoming_action_overridden": False,
                "resolved_recommended_tool": (
                    "material_studio_live_project_status"
                ),
                "resolved_recommended_action": terminal_continuation[
                    "recommended_action"
                ],
                "safety_gate": {
                    "automatic_replay_allowed": False,
                    "stale_recipe_execution_blocked": False,
                    "external_review_required": False,
                    "gui_input_required": False,
                    "post_action_observation_required": False,
                    "metadata_write_allowed_before_observation": False,
                    "record_tool_call_ready": False,
                    "activation_required_before_gui_input": False,
                    "structure_mutation_allowed": False,
                    "revision_creation_allowed": False,
                },
            },
            "runtime_ui_preflight": {
                "status": "verified",
                "source": "computer_use",
                "observation_available": True,
                "binding_verified": True,
                "automation_gate_satisfied": True,
                "artifact_exists": True,
                "required": False,
                "binding": {
                    "expected_window_handle": 1234,
                    "actual_window_handle": 1234,
                    "rejection_reasons": [],
                },
            },
            "runtime_accessibility_preflight": {
                "status": "verified",
                "source": "computer_use",
                "observation_available": True,
                "binding_verified": True,
                "automation_gate_satisfied": True,
                "artifact_exists": True,
                "required": False,
                "binding": {
                    "expected_window_handle": 1234,
                    "actual_window_handle": 1234,
                    "rejection_reasons": [],
                },
            },
            "recipe_contract": {
                "status": "current",
                "current": True,
                "pending_recipe_upgrade_required": False,
                "manifest_schema_current": True,
                "actual_manifest_schema_version": 5,
                "expected_manifest_schema_version": 5,
                "outdated_view_names": [],
                "pending_upgrade_view_names": [],
                "accepted_historical_view_names": [],
                "current_evidence_reverification_view_names": [],
                "reasons": [],
            },
            "event_journal": {
                "status": "loaded",
                "consistency_status": "consistent",
                "exists": True,
                "size_bytes": 167_037,
                "event_count": 7,
                "physical_line_count": 7,
                "manifest_event_count": 7,
                "journal_required_event_count": 7,
                "journal_matched_event_count": 7,
                "trusted_accepted_event_count": 6,
            },
            "last_replay_event": {
                "event_id": "event-7",
                "recorded_at": "2026-07-16T19:13:28Z",
                "view_name": "top",
                "source": "computer_use",
                "model_visible": True,
                "camera_matches_manifest": True,
                "accepted": True,
                "rejection_reasons": [],
                "screenshot_path": "C:\\workspace\\screenshots\\top.bmp",
                "native_command_id": "cmdViewer3DResetView",
                "modifier_keys": [],
                "window_binding": {
                    "ok": True,
                    "status": "verified_current_wrapper_window",
                    "current_revision_loaded": True,
                    "single_window_policy_ok": True,
                },
                "evidence_integrity": {
                    "status": "verified",
                    "trusted_for_replay": True,
                },
                "journal_consistency": {
                    "status": "matched",
                    "trusted_for_replay": True,
                    "journal_match_count": 1,
                },
                "execution_recipe_contract": {
                    "status": "current",
                    "current": True,
                    "recording_allowed": True,
                    "recipe_kind": "native_reset_view",
                },
            },
        },
    }

    compact = server._compact_live_response(response, "compact")

    continuation = compact["view_replay_continuation"]
    assert continuation["status"] == "complete"
    assert continuation["terminal_state"] is True
    assert continuation["non_callable_payload_templates_omitted"] is True
    assert continuation["gui_input_required"] is False
    assert continuation["record_call_ready"] is False
    assert "payload_hint" not in continuation
    assert "post_action_record_payload_template" not in continuation
    assert "next_pending_view_name" not in continuation
    assert "next_view" not in continuation
    assert continuation["continuation_detail_retrieval"] == {
        "tool": "material_studio_live_project_status",
        "response_mode": "full",
        "detail_ref": (
            "full_response.gui_view_replay.replay_continuation"
        ),
    }

    gui_replay = compact["gui_view_replay"]
    assert gui_replay["view_selection"]["view_names_ref"] == "view_names"
    assert "cartesian_context_views" not in gui_replay["view_selection"]
    assert gui_replay["replay_summary"]["pending_view_count"] == 0
    assert gui_replay["replay_summary"]["all_requested_views_accepted"] is True
    assert "raw_accepted_event_count" not in gui_replay["replay_summary"]
    assert gui_replay["replay_continuation"]["continuation_detail_ref"] == (
        "view_replay_continuation"
    )
    assert gui_replay["next_action"] == {
        "continuation_status": "complete",
        "source": "replay_continuation",
        "terminal_state": True,
        "decision_ref": "replay_continuation",
    }
    resolution = gui_replay["next_action_resolution"]
    assert resolution["terminal_state"] is True
    assert resolution["decision_ref"] == "replay_continuation"
    assert resolution["action_boundary"] == (
        "no_gui_input_or_structure_revision_mutation"
    )
    assert "safety_gate" not in resolution
    for key in ("runtime_ui_preflight", "runtime_accessibility_preflight"):
        assert gui_replay[key]["terminal_state"] is True
        assert gui_replay[key]["binding_verified"] is True
        assert "binding" not in gui_replay[key]
    assert gui_replay["recipe_contract"]["status"] == "current"
    assert "outdated_view_names" not in gui_replay["recipe_contract"]
    assert gui_replay["event_journal"]["consistency_status"] == "consistent"
    assert "size_bytes" not in gui_replay["event_journal"]
    assert gui_replay["last_replay_event"]["screenshot_persisted"] is True
    assert "screenshot_path" not in gui_replay["last_replay_event"]
    assert gui_replay["last_replay_event"]["window_binding"][
        "single_window_policy_ok"
    ] is True
    assert gui_replay["last_replay_event"]["evidence_integrity"] == {
        "status": "verified",
        "trusted_for_replay": True,
    }
    assert compact["response_compaction"]["hard_budget_applied"] is False
    assert compact["response_compaction"]["target_exceeded"] is False


def test_complete_replay_with_callable_action_is_not_terminal_compacted() -> None:
    continuation = {
        "status": "complete",
        "recommended_mcp_tool": "material_studio_gui_activate",
        "payload_hint": {"project_id": "project", "revision": 4},
        "payload_hint_is_directly_callable": True,
    }

    compact = server._compact_view_replay_continuation(continuation)

    assert compact["payload_hint"] == {"project_id": "project", "revision": 4}
    assert compact["payload_hint_is_directly_callable"] is True
    assert "terminal_state" not in compact


def test_compact_replay_rich_status_keeps_gates_without_hard_fallback() -> None:
    persisted_path = "C:\\workspace\\project\\outputs\\r004\\gui_replay.json"
    oversized_accessibility = {
        "registry_path": "C:\\Materials Studio\\Commands\\viewer.xml",
        "registry_sha256": "a" * 64,
        "command_id": "cmdViewer3DResetView",
        "element_index": 110,
        "verified": True,
        "invocation_ready": True,
        "semantic_mapping": {"raw_registry": "x" * 30_000},
    }
    blocked_recipe = {
        "schema_version": 5,
        "status": "native_accessibility_command_runtime_unverified",
        "recipe_kind": "native_reset_view",
        "automation_ready": False,
        "static_recipe_ready": True,
        "block_reasons": ["runtime_view_accessibility_binding_not_verified"],
        "native_command_id": "cmdViewer3DResetView",
        "accessibility_target": oversized_accessibility,
        "runtime_accessibility_preflight": {
            "status": "verified_blocked",
            "binding_verified": False,
            "automation_gate_satisfied": False,
            "artifact_path": persisted_path,
            "command_gates": [
                {
                    "command_id": "cmdViewer3DResetView",
                    "accessibility_target": oversized_accessibility,
                }
            ],
        },
    }
    continuation = {
        "status": "runtime_accessibility_preflight_required",
        "next_pending_view_name": "front",
        "next_actionable_pending_view_name": "front",
        "next_automation_ready_view_name": None,
        "recommended_action": "activate_current_wrapper_and_refresh_preflight",
        "recommended_mcp_tool": "material_studio_gui_activate",
        "automatic_replay_ready": False,
        "runtime_accessibility_preflight_required": True,
        "next_view": {
            "view_name": "front",
            "camera": {"direction": [0.0, 0.0, 1.0]},
            "execution_recipe": blocked_recipe,
        },
    }
    response = {
        "ok": True,
        "project_id": "semiconductor_project",
        "revision": 4,
        "normality": "review_warnings",
        "health_verdict": "passed_with_warnings",
        "ready_for_next_edit": True,
        "ready_for_calculation": False,
        "can_claim_model_normal": True,
        "report_json_path": "C:\\workspace\\project\\outputs\\r004\\report.json",
        "view_audit_report_path": "C:\\workspace\\project\\outputs\\r004\\view_audit.json",
        "view_bundle_manifest_path": "C:\\workspace\\project\\outputs\\r004\\manifest.json",
        "live_summary": {
            "project_id": "semiconductor_project",
            "revision": 4,
            "normality": "review_warnings",
            "health_verdict": "passed_with_warnings",
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
            "can_claim_model_normal": True,
            "view_names": ["front", "top", "isometric"],
        },
        "calculation_preview": {
            "available": True,
            "task": "Energy",
            "script_path": "C:\\workspace\\project\\scripts\\r004_castep_task.pl",
            "artifact_status": "matched",
            "persisted_artifact_trusted": True,
            "execution_policy": "explicit_separate_execution_required",
            "calculation_executed": False,
            "validation": {"valid": True},
        },
        "visual_normality_summary": {
            "available": True,
            "status": "review_warnings",
            "clean_view_available": True,
            "recommended_view_name": "isometric",
        },
        "view_parameter_summary": {
            "available": True,
            "status": "exported",
            "view_count": 3,
            "view_names": ["front", "top", "isometric"],
            "views": [
                {
                    "name": "front",
                    "supported": True,
                    "clean_for_visual_review": True,
                    "camera_direction": [0.0, 0.0, 1.0],
                }
            ],
        },
        "normality_gate": {
            "available": True,
            "status": "model_claimable_with_visual_notes",
            "can_claim_model_normal": True,
            "ready_for_calculation": False,
        },
        "mcp_client_readiness": {
            "status": "ready_for_live_edit",
            "can_accept_followup_request": True,
            "ready_for_calculation": False,
        },
        "semiconductor_normality_diagnosis": {
            "available": True,
            "status": "model_normal_calculation_review",
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
        },
        "diagnostic_focus_plan": {
            "available": True,
            "ok": True,
            "status": "requested_focuses_ready",
            "requested_focuses": ["semiconductor_structure_health"],
        },
        "gui_view_replay": {
            "manifest_path": persisted_path,
            "manifest_exists": True,
            "events_path": persisted_path.replace("gui_replay", "gui_replay_events"),
            "events_exist": True,
            "replay_status": "pending",
            "view_names": ["front", "top", "isometric"],
            "requested_view_count": 3,
            "supported_view_count": 3,
            "replay_summary": {
                "event_count": 1,
                "accepted_event_count": 1,
                "trusted_accepted_event_count": 1,
                "accepted_view_count": 1,
                "accepted_view_names": ["front"],
                "pending_view_count": 2,
                "pending_view_names": ["top", "isometric"],
                "all_requested_views_accepted": False,
            },
            "replay_continuation": continuation,
            "last_replay_event": {
                "event_id": "event-1",
                "recorded_at": "2026-07-15T00:00:00Z",
                "view_name": "front",
                "accepted": True,
                "native_command_id": "cmdViewer3DResetView",
                "execution_recipe": blocked_recipe,
                "evidence_integrity": {
                    "status": "trusted",
                    "trusted_for_replay": True,
                    "artifacts": [{"raw": "y" * 30_000}],
                },
            },
        },
    }

    assert _json_size(response) > 100_000
    compact = server._compact_live_response(response, "compact")

    assert _json_size(compact) < server.COMPACT_RESPONSE_MAX_BYTES
    assert compact["response_compaction"]["hard_budget_applied"] is False
    assert compact["response_compaction"]["omitted_fields"] == []
    assert compact["calculation_preview"]["persisted_artifact_trusted"] is True
    assert compact["visual_normality_summary"]["available"] is True
    assert compact["semiconductor_normality_diagnosis"]["available"] is True
    assert compact["diagnostic_focus_plan"]["available"] is True
    assert "execution_recipe" not in compact["gui_view_replay"]["last_replay_event"]
    assert "artifacts" not in compact["gui_view_replay"]["last_replay_event"][
        "evidence_integrity"
    ]
    top_recipe = compact["view_replay_continuation"]["next_view"][
        "execution_recipe"
    ]
    assert top_recipe["automation_ready"] is False
    assert top_recipe["status"] == "native_accessibility_command_runtime_unverified"
    assert "accessibility_target" not in top_recipe
    assert "command_gates" not in top_recipe["runtime_accessibility_preflight"]


def test_compact_hard_budget_fallback_bounds_oversized_error_payload() -> None:
    compact = server._compact_live_response(
        {
            "ok": False,
            "status": "rejected",
            "error": "x" * 100_000,
            "errors": ["y" * 20_000 for _ in range(20)],
            "warnings": ["z" * 20_000 for _ in range(20)],
        },
        server.McpResponseMode.COMPACT,
    )

    assert _json_size(compact) < server.COMPACT_RESPONSE_MAX_BYTES
    assert compact["response_compaction"]["hard_budget_applied"] is True
    assert "oversized_extended_receipt" in compact["response_compaction"][
        "omitted_fields"
    ]
    assert len(compact["error"]) == 2000
    assert len(compact["errors"]) == 10
    assert all(len(item) == 1000 for item in compact["errors"])


def test_compact_requested_focus_status_expands_only_problem_details() -> None:
    full = {
        "available": True,
        "ok": False,
        "focus_count": 2,
        "missing_csv_keys": ["semiconductor_defects_csv"],
        "missing_summary_keys": [],
        "focuses": [
            {
                "focus": "view_quality",
                "source": "user_request",
                "auto_completed": False,
                "available": True,
                "ok": True,
                "missing_summary_keys": [],
                "missing_csv_keys": [],
                "next_action": "requested_diagnostic_focus_ready",
            },
            {
                "focus": "semiconductor_defects",
                "source": "auto_completed",
                "auto_completed": True,
                "available": False,
                "ok": False,
                "missing_summary_keys": ["defect_summary"],
                "missing_csv_keys": ["semiconductor_defects_csv"],
                "next_action": "export_missing_diagnostic_focus_artifacts",
            },
        ],
    }

    compact = server._compact_requested_diagnostic_focus_status(full)

    assert compact is not None
    assert compact["focus_detail_level"] == "issues_only"
    assert compact["problem_focus_count"] == 1
    assert compact["focuses"][0] == {"focus": "view_quality", "ok": True}
    assert compact["focuses"][1]["auto_completed"] is True
    assert compact["focuses"][1]["available"] is False
    assert compact["focuses"][1]["missing_summary_keys"] == ["defect_summary"]
    assert compact["focuses"][1]["missing_csv_keys"] == [
        "semiconductor_defects_csv"
    ]
    assert _json_size(compact) < _json_size(full)


def test_compact_action_payload_is_kept_once_in_authoritative_plan() -> None:
    payload_hint = {
        "project_id": "semiconductor_project",
        "base_revision": 4,
        "patch": {
            "project_id": "semiconductor_project",
            "base_revision": 4,
            "operations": [
                {
                    "type": "set_castep_energy",
                    "task": "Energy",
                    "kpoints": [29, 29, 1],
                }
            ],
        },
        "execution_mode": "preview",
    }
    compact = server._compact_live_response(
        {
            "ok": True,
            "project_id": "semiconductor_project",
            "revision": 4,
            "next_action_plan": {
                "available": True,
                "action_id": "apply_recommended_semiconductor_kpoint_grid",
                "recommended_tool": "material_studio_live_update_with_patch",
                "recommended_action": (
                    "apply_recommended_explicit_kpoint_grid_then_reaudit"
                ),
                "needs_user_confirmation": True,
                "safe_to_call_without_confirmation": False,
                "payload_hint": payload_hint,
            },
            "mcp_client_readiness": {
                "status": "ready_for_followup_live_modeling",
                "recommended_tool": "material_studio_live_update_with_patch",
                "recommended_action": (
                    "apply_recommended_explicit_kpoint_grid_then_reaudit"
                ),
                "next_action_id": "apply_recommended_semiconductor_kpoint_grid",
                "needs_user_confirmation": True,
                "safe_to_call_without_confirmation": False,
                "payload_hint": payload_hint,
            },
            "semiconductor_normality_diagnosis": {
                "available": True,
                "status": "model_normal_calculation_review",
                "action_id": "apply_recommended_semiconductor_kpoint_grid",
                "recommended_tool": "material_studio_live_update_with_patch",
                "recommended_action": (
                    "apply_recommended_explicit_kpoint_grid_then_reaudit"
                ),
                "payload_hint": payload_hint,
            },
        },
        "compact",
    )

    assert compact["next_action_plan"]["payload_hint"] == payload_hint
    assert "payload_hint" not in compact["mcp_client_readiness"]
    assert compact["mcp_client_readiness"]["payload_hint_ref"] == (
        "next_action_plan.payload_hint"
    )
    assert "payload_hint" not in compact["semiconductor_normality_diagnosis"]
    assert compact["semiconductor_normality_diagnosis"]["payload_hint_ref"] == (
        "next_action_plan.payload_hint"
    )
    assert compact["response_compaction"]["semantic_core_preserved"] is True


def test_hard_budget_preserves_normality_visual_and_action_core() -> None:
    semantic_core = {
        "next_action_plan": {
            "available": True,
            "action_id": "apply_recommended_semiconductor_kpoint_grid",
            "recommended_tool": "material_studio_live_update_with_patch",
            "recommended_action": "apply_kpoint_patch_then_reaudit",
            "needs_user_confirmation": True,
            "payload_hint": {"project_id": "semiconductor_project"},
        },
        "normality_gate": {
            "available": True,
            "status": "calculation_blocked",
            "can_claim_model_normal": True,
            "ready_for_calculation": False,
        },
        "normality_explanation": {
            "available": True,
            "status": "review_warnings",
            "primary_reason": "semiconductor:kpoint_reciprocal_lattice_warnings",
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
        },
        "visual_normality_summary": {
            "available": True,
            "status": "review_warnings",
            "clean_view_available": True,
            "recommended_view_name": "isometric",
        },
        "view_parameter_summary": {
            "available": True,
            "status": "exported",
            "view_count": 3,
            "view_names": ["front", "top", "isometric"],
        },
    }
    oversized = {
        "ok": True,
        "project_id": "semiconductor_project",
        "revision": 4,
        **semantic_core,
        "calculation_preview": {
            "available": True,
            "task": "Energy",
            "extended": "c" * 60_000,
        },
        "artifacts": {"oversized": "a" * 60_000},
        "live_gui_acceptance": {"oversized": "g" * 60_000},
        "diagnostic_focus_plan": {"oversized": "d" * 60_000},
    }

    bounded = server._enforce_live_compact_budget(oversized)

    assert _json_size(bounded) < server.COMPACT_RESPONSE_MAX_BYTES
    for key, value in semantic_core.items():
        assert bounded[key] == value
    receipt = bounded["response_compaction"]
    assert receipt["hard_budget_applied"] is True
    assert receipt["semantic_core_preserved"] is True
    assert receipt["semantic_core_omitted_fields"] == []
    assert receipt["response_bytes"] == _json_size(bounded)
    assert receipt["headroom_bytes"] == (
        server.COMPACT_RESPONSE_MAX_BYTES - _json_size(bounded)
    )
    assert "artifacts" in receipt["omitted_fields"]
    assert "live_gui_acceptance" in receipt["omitted_fields"]
    assert "diagnostic_focus_plan" in receipt["omitted_fields"]
