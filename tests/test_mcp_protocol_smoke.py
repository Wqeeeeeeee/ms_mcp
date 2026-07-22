from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

import pytest

from material_studio_mcp_server.protocol_smoke import (
    COMPACT_RESPONSE_MAX_BYTES,
    REQUIRED_PROTOCOL_TOOLS,
    _protocol_roundtrip_execution_handoff_acceptance,
    _protocol_roundtrip_preview_acceptance,
    audit_codex_config,
    run_protocol_acceptance,
)
from material_studio_mcp_server.roundtrip import (
    ROUNDTRIP_AUDIT_PROFILE,
    ROUNDTRIP_AUDIT_SCHEMA_VERSION,
)


def test_stdio_protocol_acceptance_lists_and_calls_live_semiconductor_tools(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    workspace = tmp_path / "protocol_workspace"

    result = asyncio.run(
        run_protocol_acceptance(
            command=sys.executable,
            args=[str(root / "run_server.py")],
            cwd=root,
            workspace=workspace,
            config_path=root / ".codex" / "config.toml.example",
            timeout_seconds=60,
        )
    )

    assert result["ok"] is True
    assert result["transport"] == "stdio"
    assert result["protocol_version"]
    assert result["tool_count"] >= len(REQUIRED_PROTOCOL_TOOLS)
    assert result["discovery"] == {
        "ok": True,
        "required_tool_count": len(REQUIRED_PROTOCOL_TOOLS),
        "missing_tools": [],
        "annotation_errors": [],
        "schema_errors": [],
    }
    calls = result["calls"]
    assert calls["ok"] is True
    assert calls["template_id"] == "silicon_diamond"
    assert calls["execution_mode"] == "preview"
    assert calls["response_mode"] == "compact"
    assert calls["artifact_status"] == "not_materialized"
    assert calls["planned_structure_exists"] is False
    assert calls["gui_opened"] is False
    assert calls["roundtrip_preview_acceptance_ok"] is True
    assert calls["roundtrip_preview_status"] == "deferred_until_materialized"
    assert calls["roundtrip_preview_create_status_consistent"] is True
    assert calls["roundtrip_preview_runner_call_planned"] is False
    assert calls["roundtrip_preview_gui_probe_planned"] is False
    assert calls["roundtrip_preview_run_root_exists"] is False
    assert calls["roundtrip_execution_handoff_acceptance_ok"] is True
    assert calls["roundtrip_execution_handoff_confirmation_required"] is True
    assert calls["roundtrip_execution_handoff_payload_consistent"] is True
    assert calls["roundtrip_execution_handoff_acceptance"]["payload"] == {
        "project_id": calls["project_id"],
        "expected_revision": calls["revision"],
        "execution_mode": "execute",
        "open_in_gui": True,
        "take_snapshot": True,
        "verify_ms_roundtrip": True,
        "export_view_audit": True,
        "views": ["front", "top", "isometric"],
        "working_dir": str(workspace.resolve()),
    }
    assert calls["roundtrip_preview_acceptance"]["output_exists"] is False
    assert calls["roundtrip_preview_acceptance"]["side_effects"] == {
        "files_written": False,
        "runner_called": False,
        "gui_input_performed": False,
    }
    assert calls["capabilities_runner_status_present"] is True
    assert calls["capabilities_gui_status_present"] is True
    assert calls["capabilities_replay_runtime_status"] in {
        "transactional_miller_available",
        "standard_and_isometric_only",
        "unavailable",
    }
    assert calls["capabilities_replay_runtime_observed"] is True
    assert calls["runtime_deployment_schema"] == (
        "material_studio_mcp_runtime_deployment_binding_v1"
    )
    assert calls["runtime_repository_root"] == str(root)
    assert calls["runtime_entrypoint_binding"] == "matched_source_run_server"
    assert calls["codex_config_status_schema"] == (
        "material_studio_mcp_runtime_codex_config_status_v1"
    )
    assert calls["codex_config_active_modified"] is False
    assert calls["codex_config_advisory_only"] is True
    assert calls["capabilities_post_hotload_replay_prepare_parameter"] == (
        "prepare_view_replay_after_open"
    )
    assert calls[
        "capabilities_post_hotload_replay_prepare_after_report_lock"
    ] is True
    assert calls[
        "capabilities_post_hotload_replay_prepare_rewrites_report"
    ] is False
    assert calls[
        "capabilities_post_hotload_replay_prepare_preserves_hotload"
    ] is True
    assert calls["capabilities_transactional_miller_implemented"] is True
    assert calls["capabilities_exact_collinear_direction_implemented"] is True
    assert calls["capabilities_non_collinear_direction_implemented"] is False
    assert calls["capabilities_castep_handoff_workspace_bound"] is True
    assert calls["capabilities_castep_handoff_revision_bound"] is True
    assert calls["capabilities_castep_handoff_execute_confirmation"] is True
    assert calls["capabilities_castep_handoff_energy_tool"] == (
        "material_studio_castep_run_current"
    )
    assert calls["capabilities_castep_handoff_relax_tool"] == (
        "material_studio_castep_relax_current"
    )
    assert calls["capabilities_castep_electronic_tool"] == (
        "material_studio_castep_run_current"
    )
    assert calls["capabilities_castep_electronic_numeric_export_mode"] == (
        "conditional_on_native_bands"
    )
    assert calls["capabilities_castep_electronic_band_export"] == (
        "native_castep_band_eigenvalues"
    )
    assert calls["capabilities_castep_electronic_dos_export"] == (
        "mcp_gaussian_total_dos_from_native_bands_when_smearing_is_explicit"
    )
    assert calls["capabilities_castep_electronic_pdos_export"] == (
        "not_exported_until_pdos_weights_format_is_verified"
    )
    assert calls["capabilities_castep_sampled_band_edge_source"] == (
        "hash_bound_native_castep_bands"
    )
    assert (
        calls["capabilities_castep_sampled_band_edge_scientific_gap_verified"]
        is False
    )
    assert calls["capabilities_castep_result_assessment_requires_binding"] is True
    assert (
        calls[
            "capabilities_castep_result_assessment_structure_normality_blocked"
        ]
        is False
    )
    assert calls["capabilities_castep_result_assessment_preview_mode"] == (
        "preview"
    )
    assert calls["capabilities_castep_band_edge_csv"] == (
        "semiconductor_castep_band_edges.csv"
    )
    assert calls[
        "capabilities_castep_result_diagnostic_focus_present"
    ] is True
    assert calls[
        "capabilities_castep_convergence_diagnostic_focus_present"
    ] is True
    assert calls["capabilities_castep_convergence_schema"] == (
        "material_studio_castep_convergence_audit_v1"
    )
    assert calls["capabilities_castep_convergence_source"] == (
        "immutable_verified_electronic_result_revisions"
    )
    assert calls["capabilities_castep_convergence_axes_separate"] is True
    assert calls[
        "capabilities_castep_convergence_scientific_verified"
    ] is False
    assert calls["capabilities_castep_convergence_preview_mode"] == "preview"
    assert calls[
        "capabilities_castep_convergence_execute_confirmation"
    ] is True
    assert calls["castep_electronic_preview_task"] == "Energy"
    assert calls["castep_electronic_preview_execution_started"] is False
    assert calls["castep_electronic_preview_structure_exists"] is False
    assert calls["castep_electronic_preview_run_directory_exists"] is False
    assert calls["castep_electronic_preview_numeric_curve_data_exported"] is False
    assert calls["castep_electronic_preview_numeric_export_after_execution"] is None
    assert calls["castep_electronic_preview_scientific_band_gap_verified"] is False
    assert calls["castep_electronic_preview_band_edge_audit_after_execution"] == (
        "conditional_on_hash_bound_native_bands"
    )
    assert calls["view_names"] == ["front", "top", "isometric"]
    assert calls["view_bundle_row_counts"]["view_summary"] == 3
    assert calls["view_bundle_row_counts"]["view_projections"] == 24
    assert calls["view_bundle_row_counts"]["structure_artifact_validation"] == 1
    assert calls["view_bundle_files_complete"] is True
    assert calls["view_bundle_files_existing_count"] == calls[
        "view_bundle_files_total_count"
    ]
    assert calls["view_bundle_files_missing_count"] == 0
    assert calls["view_bundle_file_index_compacted"] is True
    assert calls["view_bundle_file_index_complete_in_response"] is False
    assert calls["trusted_clean_view_policy_summary_field"] == (
        "trusted_clean_view_replay"
    )
    assert calls["trusted_clean_view_policy_requires_view_selection_match"] is True
    assert calls["trusted_clean_view_policy_requires_all_views_confirmed"] is True
    assert calls["trusted_clean_view_policy_calculation_independent"] is True
    assert calls["history_count"] == 1
    assert calls["visual_diagnostics_binding_verified"] is True
    assert calls["visual_diagnostics_action_id"]
    assert calls["visual_diagnostics_action_tool"]
    assert "visual_diagnostics" in calls["coordinated_action_tracks"]
    compaction = calls["preflight_response_compaction"]
    assert compaction["schema"] == (
        "material_studio_live_session_preflight_compact_v1"
    )
    assert compaction["target_exceeded"] is False
    assert compaction["response_bytes"] < compaction["target_bytes"]
    assert compaction["headroom_bytes"] == (
        compaction["budget_bytes"] - compaction["response_bytes"]
    )
    assert max(
        calls["response_sizes_bytes"][name]
        for name in (
            "capabilities",
            "create",
            "status",
            "prepare_view_replay",
            "resumed_preflight",
            "view_bundle",
        )
    ) < COMPACT_RESPONSE_MAX_BYTES
    assert Path(calls["view_bundle_manifest_path"]).exists()
    assert result["config_audit"]["ok"] is True


def _roundtrip_preview_responses(
    tmp_path: Path,
) -> tuple[dict, dict]:
    project_id = "protocol_roundtrip_unit"
    revision = 0
    run_root = (
        tmp_path
        / project_id
        / "outputs"
        / "r000"
        / "ms_roundtrip"
        / "preview"
    )
    audit = {
        "schema_version": ROUNDTRIP_AUDIT_SCHEMA_VERSION,
        "profile": ROUNDTRIP_AUDIT_PROFILE,
        "project_id": project_id,
        "revision": revision,
        "execution_mode": "preview",
        "required": True,
        "applicable": True,
        "status": "deferred_until_materialized",
        "spec_sha256": "a" * 64,
        "output_path": str(run_root / "roundtrip_output.cif"),
        "run_root": str(run_root),
        "gui_probe_planned": False,
        "runner_call_planned": False,
        "side_effects": {
            "files_written": False,
            "runner_called": False,
            "gui_input_performed": False,
        },
        "errors": [],
        "warnings": ["Structure is not materialized."],
    }
    created = {
        "project_id": project_id,
        "revision": revision,
        "materials_studio_roundtrip_audit_requested": True,
        "materials_studio_roundtrip_audit": audit,
    }
    status = {
        "project_id": project_id,
        "revision": revision,
        "materials_studio_roundtrip_audit_requested": True,
        "materials_studio_roundtrip_audit": copy.deepcopy(audit),
    }
    return created, status


def test_protocol_roundtrip_preview_acceptance_binds_side_effect_free_plan(
    tmp_path: Path,
) -> None:
    created, status = _roundtrip_preview_responses(tmp_path)

    acceptance = _protocol_roundtrip_preview_acceptance(
        created=created,
        status=status,
        workspace=tmp_path,
    )

    assert acceptance["ok"] is True
    assert acceptance["status"] == "passed"
    assert acceptance["create_status_consistent"] is True
    assert acceptance["run_root_exists"] is False
    assert acceptance["output_exists"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing_marker", "roundtrip_create_request_marker_missing"),
        ("invalid_sha", "roundtrip_create_spec_sha256_invalid"),
        ("runner_planned", "roundtrip_create_runner_call_planned_mismatch"),
        ("side_effect", "roundtrip_create_side_effects_invalid"),
        ("status_drift", "roundtrip_create_status_plan_mismatch"),
        ("output_escape", "roundtrip_preview_output_path_mismatch"),
        ("run_root_created", "roundtrip_preview_run_root_created"),
    ],
)
def test_protocol_roundtrip_preview_acceptance_rejects_contract_drift(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    created, status = _roundtrip_preview_responses(tmp_path)
    created_audit = created["materials_studio_roundtrip_audit"]
    status_audit = status["materials_studio_roundtrip_audit"]
    if mutation == "missing_marker":
        created["materials_studio_roundtrip_audit_requested"] = False
    elif mutation == "invalid_sha":
        created_audit["spec_sha256"] = "invalid"
        status_audit["spec_sha256"] = "invalid"
    elif mutation == "runner_planned":
        created_audit["runner_call_planned"] = True
        status_audit["runner_call_planned"] = True
    elif mutation == "side_effect":
        created_audit["side_effects"]["runner_called"] = True
        status_audit["side_effects"]["runner_called"] = True
    elif mutation == "status_drift":
        status_audit["spec_sha256"] = "b" * 64
    elif mutation == "output_escape":
        escaped = str(tmp_path / "outside.cif")
        created_audit["output_path"] = escaped
        status_audit["output_path"] = escaped
    else:
        Path(created_audit["run_root"]).mkdir(parents=True)

    acceptance = _protocol_roundtrip_preview_acceptance(
        created=created,
        status=status,
        workspace=tmp_path,
    )

    assert acceptance["ok"] is False
    assert expected_error in acceptance["errors"]


def _roundtrip_handoff_responses(tmp_path: Path) -> tuple[dict, dict]:
    project_id = "protocol_roundtrip_handoff"
    revision = 3
    payload = {
        "project_id": project_id,
        "expected_revision": revision,
        "execution_mode": "execute",
        "open_in_gui": True,
        "take_snapshot": True,
        "verify_ms_roundtrip": True,
        "export_view_audit": True,
        "views": ["front", "top", "isometric"],
        "working_dir": str(tmp_path.resolve()),
    }
    action = {
        "action_id": "execute_and_hotload_current_revision",
        "recommended_tool": "material_studio_gui_apply_current_revision",
        "needs_user_confirmation": True,
        "safe_to_call_without_confirmation": False,
        "payload_hint": payload,
    }
    created = {
        "project_id": project_id,
        "revision": revision,
        "next_action_plan": copy.deepcopy(action),
    }
    status = {
        "project_id": project_id,
        "revision": revision,
        "next_action_plan": {
            "action_id": "verify_single_window_gui_preflight",
            "recommended_tool": "material_studio_gui_status",
            "needs_user_confirmation": False,
            "safe_to_call_without_confirmation": True,
            "payload_hint": {
                "project_id": project_id,
                "revision": revision,
                "working_dir": str(tmp_path.resolve()),
            },
            "deferred_hotload_action": copy.deepcopy(action),
        },
    }
    return created, status


def test_protocol_roundtrip_execution_handoff_acceptance_binds_exact_payload(
    tmp_path: Path,
) -> None:
    created, status = _roundtrip_handoff_responses(tmp_path)

    acceptance = _protocol_roundtrip_execution_handoff_acceptance(
        created=created,
        status=status,
        workspace=tmp_path,
        expected_views=("front", "top", "isometric"),
    )

    assert acceptance["ok"] is True
    assert acceptance["status"] == "passed"
    assert acceptance["needs_user_confirmation"] is True
    assert acceptance["safe_to_call_without_confirmation"] is False
    assert acceptance["create_status_payload_consistent"] is True


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing_revision", "roundtrip_create_apply_expected_revision_mismatch"),
        ("missing_roundtrip", "roundtrip_create_apply_verify_ms_roundtrip_mismatch"),
        ("missing_workspace", "roundtrip_create_apply_working_dir_mismatch"),
        ("confirmation_bypass", "roundtrip_create_apply_confirmation_gate_missing"),
        ("status_drift", "roundtrip_create_status_apply_payload_mismatch"),
    ],
)
def test_protocol_roundtrip_execution_handoff_acceptance_rejects_drift(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    created, status = _roundtrip_handoff_responses(tmp_path)
    create_action = created["next_action_plan"]
    status_action = status["next_action_plan"]["deferred_hotload_action"]
    if mutation == "missing_revision":
        create_action["payload_hint"].pop("expected_revision")
    elif mutation == "missing_roundtrip":
        create_action["payload_hint"].pop("verify_ms_roundtrip")
    elif mutation == "missing_workspace":
        create_action["payload_hint"].pop("working_dir")
    elif mutation == "confirmation_bypass":
        create_action["needs_user_confirmation"] = False
    else:
        status_action["payload_hint"]["take_snapshot"] = False

    acceptance = _protocol_roundtrip_execution_handoff_acceptance(
        created=created,
        status=status,
        workspace=tmp_path,
        expected_views=("front", "top", "isometric"),
    )

    assert acceptance["ok"] is False
    assert expected_error in acceptance["errors"]


def test_codex_config_audit_reports_missing_tools_without_modifying_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.materials_studio]
command = "python"
args = ["run_server.py"]
enabled = true
enabled_tools = ["material_studio_live_modeling_request"]
disabled_tools = ["material_studio_run_script"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = audit_codex_config(config)

    assert result["ok"] is False
    assert "material_studio_gui_prepare_view_replay" in result["missing_enabled_tools"]
    assert "material_studio_project_reconcile_dopant_metadata" in result["missing_enabled_tools"]
    assert result["unexpected_dangerous_enabled_tools"] == []
    assert result["run_script_explicitly_disabled"] is True
    assert config.read_text(encoding="utf-8").count("material_studio_live_modeling_request") == 1
