from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.specs import ModelSpec


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def test_compact_capabilities_preserve_semiconductor_discovery() -> None:
    full = server.material_studio_live_capabilities()
    compact = server.material_studio_live_capabilities(response_mode="compact")

    assert full["ok"] is True
    assert compact["ok"] is True
    assert compact["response_mode"] == "compact"
    assert compact["response_schema"] == "material_studio_capabilities_compact_v2"
    assert compact["full_detail_hint"]["arguments"] == {"response_mode": "full"}
    assert compact["domain_focus"]["primary"] == "semiconductor materials"
    assert compact["domain_focus"]["semiconductor_template_count"] >= 50
    assert "silicon_diamond" in compact["domain_focus"]["semiconductor_template_ids"]
    assert compact["domain_focus"]["semiconductor_virtual_template_count"] >= 1
    assert "front" in compact["diagnostics"]["supported_view_names"]
    assert "electronic_structure_preflight" in compact["diagnostics"]["diagnostic_focus_ids"]
    assert "diagnostic_focus_profiles" not in compact["diagnostics"]
    assert compact["diagnostics"]["diagnostic_focus_profile_count"] >= 20
    assert compact["natural_language"]["patch_command_count"] == len(
        compact["natural_language"]["patch_commands"]
    )
    assert all(
        "pattern" not in command
        for command in compact["natural_language"]["patch_commands"]
    )
    assert compact["gui"]["open_structure_policy"][
        "auto_launch_before_open_when_window_missing"
    ] is False
    assert "material_studio_live_modeling_request" == compact["live_entry_tool"]
    assert compact["view_replay_confirmation_entry"]["payload_field"] == "view_replay_confirmation"
    assert compact["view_replay_confirmation_entry"]["creates_revision"] is False
    assert compact["view_replay_automation_policy"]["automatic_native_view_names"] == [
        "front",
        "back",
        "right",
        "left",
        "top",
        "bottom",
        "isometric",
    ]
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
    assert compact["view_replay_automation_policy"]["structure_nudge_or_align_commands_allowed_for_camera_replay"] is False
    assert _json_size(compact) < server.COMPACT_RESPONSE_MAX_BYTES
    assert _json_size(compact) * 4 < _json_size(full)


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
        "back",
        "right",
        "left",
        "top",
        "bottom",
        "isometric",
    ]
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
    assert created["response_compaction"]["hard_budget_applied"] is True
    assert created["response_compaction"]["omitted_fields"]
    assert created["response_compaction"]["details_persisted"] is True
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
                "next_automation_ready_view_name": "right",
            },
        }
    }

    compact = server._compact_live_response(response, server.McpResponseMode.COMPACT)

    assert compact["gui_view_replay_status"] == "externally_confirmed"
    assert compact["view_replay_continuation"]["status"] == "automatic_recipe_ready"
    assert compact["view_replay_continuation"]["next_automation_ready_view_name"] == "right"


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
