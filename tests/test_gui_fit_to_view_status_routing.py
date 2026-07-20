from __future__ import annotations

import pytest

from material_studio_mcp_server import server


def _low_contrast_current_revision_report() -> dict:
    return {
        "ok": True,
        "project_id": "fit_status_project",
        "revision": 4,
        "execution_mode": "execute",
        "normality": "review_warnings",
        "health_ok": True,
        "script_valid": True,
        "structure": {
            "path": "C:\\ms\\model_r004.cif",
            "exists": True,
        },
        "acceptance_review": {"available": False},
        "change_validation": {"available": False},
        "change_verification": {"available": False},
        "view_review": {
            "critical_flags": [],
            "risk_flags": [],
            "nonblocking_visual_flags": [],
        },
        "gui": {
            "status_was_probed": True,
            "window_found": True,
            "hot_loaded": True,
            "loaded_current_revision": True,
            "visual_validation": "warning",
            "snapshot_path": "C:\\ms\\low_contrast.bmp",
            "snapshot_viewport_likely_visible_model": False,
            "snapshot_viewport_capture_limitation_possible": False,
            "snapshot_viewport_capture_diagnostic": "low_contrast_or_not_fit_to_view",
            "external_visual_confirmation_ok": False,
            "single_window_policy_ok": True,
            "single_window_violation_reasons": [],
            "matching_window_count": 1,
            "matching_window_identity_verification": "verified",
            "target_window_matched_project_window": True,
            "target_window_handle": 404,
            "target_window_title": "msmcp_r004_fit_status_project - Materials Studio",
            "selected_window_matches_current": True,
            "foreground_window_matches_current": True,
            "window_management": {
                "single_window_policy_ok": True,
                "single_window_violation_reasons": [],
                "matched_project_window": True,
                "matching_window_identity_verification": "verified",
                "target_window_is_selected": True,
                "target_window_is_visible": True,
                "target_window_is_minimized": False,
                "target_window_foreground_observed": True,
                "target_window_is_foreground": True,
                "activation_required_before_capture_or_input": False,
                "needs_activation": False,
                "can_apply_current_revision_without_new_window": True,
            },
        },
    }


def _derive_live_routing(report: dict) -> dict:
    report["live_readiness"] = server._live_readiness_summary(report)
    report["semiconductor_calculation_readiness"] = {"available": False}
    report["next_action_plan"] = server._modeling_report_next_action_plan(report)
    report["gui_current_revision"] = server._gui_current_revision_status_from_report(report)
    report["live_hotload_preflight"] = server._live_hotload_preflight_summary(report)
    report["normality_gate"] = {
        "can_claim_model_normal": False,
        "can_claim_live_gui_normal": False,
        "calculation_only_review_reasons": [],
    }
    report["mcp_client_readiness"] = server._modeling_report_mcp_client_readiness(report)
    report["live_summary"] = server._live_summary_from_report(report)
    report["live_summary"].update(
        server._gui_current_revision_live_summary(report["gui_current_revision"])
    )
    return report


def test_low_contrast_current_revision_routes_all_status_layers_to_fit_preview() -> None:
    report = _derive_live_routing(_low_contrast_current_revision_report())
    expected_payload = {
        "project_id": "fit_status_project",
        "revision": 4,
        "execution_mode": "preview",
        "take_snapshot": True,
    }

    readiness = report["live_readiness"]
    assert readiness["state"] == "hot_loaded_fit_to_view_preview_recommended"
    assert readiness["recommended_tool"] == "material_studio_gui_fit_to_view"
    assert readiness["needs_user_confirmation"] is False

    current = report["gui_current_revision"]
    assert current["status"] == "current_with_visual_warning"
    assert current["fit_to_view_preview_recommended"] is True
    assert current["recommended_tool"] == "material_studio_gui_fit_to_view"
    assert current["recommended_action"] == "preview_fit_to_view_for_current_revision"
    assert current["payload_hint"] == expected_payload

    action = report["next_action_plan"]
    assert action["action_id"] == "preview_fit_to_view_for_current_revision"
    assert action["recommended_tool"] == "material_studio_gui_fit_to_view"
    assert action["payload_hint"] == expected_payload
    assert action["needs_user_confirmation"] is False
    assert action["safe_to_call_without_confirmation"] is True

    hotload = report["live_hotload_preflight"]
    assert hotload["status"] == "current_revision_loaded_fit_to_view_preview_recommended"
    assert hotload["recommended_tool"] == "material_studio_gui_fit_to_view"
    assert hotload["payload_hint"] == expected_payload

    client = report["mcp_client_readiness"]
    assert client["status"] == "live_gui_fit_to_view_preview_recommended"
    assert client["visible_followup_ready"] is False
    assert client["visible_followup_status"] == "fit_to_view_preview_recommended"
    assert client["visible_followup_blocking_reasons"] == [
        "current_revision_view_not_fit_to_view"
    ]
    assert client["visible_followup_recommended_tool"] == (
        "material_studio_gui_fit_to_view"
    )
    assert client["visible_followup_payload_hint"] == expected_payload

    response = {
        "ok": True,
        "project_id": report["project_id"],
        "revision": report["revision"],
        "execution_mode": report["execution_mode"],
        "modeling_report": report,
        "next_action_plan": action,
        "gui_current_revision": current,
        "mcp_client_readiness": client,
        "live_summary": report["live_summary"],
    }
    compact = server._compact_live_response(response, server.McpResponseMode.COMPACT)
    assert compact["next_action_plan"]["payload_hint"] == expected_payload
    compact_client = compact["mcp_client_readiness"]
    assert compact_client["visible_followup_recommended_tool"] == (
        "material_studio_gui_fit_to_view"
    )
    assert compact_client["visible_followup_payload_hint"] == expected_payload
    assert compact["gui_current_revision"]["payload_hint"] == expected_payload


def test_capture_limitation_keeps_snapshot_review_instead_of_fit_preview() -> None:
    report = _low_contrast_current_revision_report()
    report["gui"].update(
        {
            "snapshot_viewport_capture_limitation_possible": True,
            "snapshot_viewport_capture_diagnostic": "uniform_dark_viewport_surface",
        }
    )

    current = server._gui_current_revision_status_from_report(report)

    assert current["fit_to_view_preview_recommended"] is False
    assert current["recommended_tool"] == "material_studio_gui_snapshot"
    assert current["recommended_action"] == (
        "recapture_or_review_gui_snapshot_for_current_revision"
    )


def test_minimized_target_requires_activation_before_fit_preview() -> None:
    report = _low_contrast_current_revision_report()
    report["gui"]["window_management"].update(
        {
            "target_window_is_minimized": True,
            "target_window_is_foreground": False,
        }
    )

    current = server._gui_current_revision_status_from_report(report)

    assert current["status"] == "current_but_not_active"
    assert current["fit_to_view_preview_recommended"] is False
    assert "target_window_minimized" in current["fit_to_view_preview_blocking_reasons"]
    assert current["recommended_tool"] == "material_studio_gui_activate"
    assert current["recommended_action"] == (
        "restore_and_activate_current_revision_window"
    )


@pytest.mark.parametrize(
    "window_state",
    [
        {"target_window_is_minimized": True},
        {"target_window_is_visible": False},
        {
            "target_window_foreground_observed": True,
            "target_window_is_foreground": False,
        },
    ],
    ids=["minimized", "not-visible", "not-foreground"],
)
def test_project_mcp_readiness_requires_activation_before_same_window_input(
    window_state: dict,
) -> None:
    report = _low_contrast_current_revision_report()
    report["working_dir"] = "C:\\ms\\workspace"
    report["gui"]["window_management"].update(window_state)

    report = _derive_live_routing(report)

    current = report["gui_current_revision"]
    assert current["loaded_current_revision"] is True
    assert current["needs_activation"] is True
    assert (
        current["window_management_can_apply_current_revision_without_new_window"]
        is True
    )

    client = report["mcp_client_readiness"]
    assert client["status"] == "gui_activation_required_for_live_hotload"
    assert client["current_revision_loaded_in_gui"] is True
    assert client["can_accept_hotload_request_without_new_window"] is False
    assert client["can_apply_current_revision_without_new_window"] is False
    assert client["ready_for_live_hotload"] is False
    assert client["visible_followup_ready"] is False
    assert client["visible_followup_status"] == "needs_current_revision_activation"
    assert client["visible_followup_recommended_tool"] == "material_studio_gui_activate"
    assert client["visible_followup_payload_hint"] == {
        "project_id": "fit_status_project",
        "revision": 4,
        "take_snapshot": True,
        "working_dir": "C:\\ms\\workspace",
    }
    assert client["same_window_hotload_ready"] is False
    assert client["same_window_hotload_status"] == "activation_required"
    assert client["same_window_hotload_tool"] == "material_studio_gui_activate"
    assert client["same_window_hotload_payload_hint"] == {
        "project_id": "fit_status_project",
        "revision": 4,
        "take_snapshot": True,
        "working_dir": "C:\\ms\\workspace",
    }
    assert "gui_target_window_needs_activation" in client["hotload_blocking_reasons"]


def test_stale_revision_requires_reload_before_fit_preview() -> None:
    report = _low_contrast_current_revision_report()
    report["gui"].update(
        {
            "loaded_current_revision": False,
            "selected_window_matches_current": False,
            "foreground_window_matches_current": False,
            "stale_reasons": ["opened_revision_does_not_match_current_revision"],
        }
    )

    current = server._gui_current_revision_status_from_report(report)

    assert current["fit_to_view_preview_recommended"] is False
    assert current["needs_reload"] is True
    assert current["recommended_tool"] == "material_studio_gui_open_structure"


def test_external_visual_confirmation_suppresses_fit_preview() -> None:
    report = _low_contrast_current_revision_report()
    report["gui"]["external_visual_confirmation_ok"] = True
    report["gui"]["visual_validation"] = "passed"

    plan = server._gui_fit_to_view_preview_plan(report)
    current = server._gui_current_revision_status_from_report(report)

    assert plan["status"] == "not_needed_visual_evidence_already_accepted"
    assert plan["recommended"] is False
    assert "accepted_visual_evidence_already_available" in plan["blocking_reasons"]
    assert current["recommended_tool"] == "material_studio_live_modeling_request"


def test_fresh_gui_probe_overrides_stale_derived_current_revision_cache() -> None:
    report = _low_contrast_current_revision_report()
    report["gui_current_revision"] = {
        "loaded_current_revision": False,
        "target_window_loaded": False,
        "target_window_identity_verified": False,
        "selected_window_matches_current": False,
        "foreground_window_matches_current": False,
        "needs_activation": True,
        "window_management": {
            "target_window_is_visible": False,
            "target_window_is_minimized": True,
        },
    }

    plan = server._gui_fit_to_view_preview_plan(report)

    assert plan["recommended"] is True
    assert plan["blocking_reasons"] == []
    assert plan["payload_hint"]["execution_mode"] == "preview"


def test_generic_visual_warning_still_recommends_snapshot() -> None:
    report = _low_contrast_current_revision_report()
    report["gui"].pop("snapshot_viewport_capture_diagnostic")

    current = server._gui_current_revision_status_from_report(report)

    assert current["fit_to_view_preview_recommended"] is False
    assert current["recommended_tool"] == "material_studio_gui_snapshot"


def test_live_capabilities_publish_fit_preview_routing_fields() -> None:
    capabilities = server.material_studio_live_capabilities()

    client_fields = capabilities["session_preflight"]["mcp_client_readiness_fields"]
    assert "fit_to_view_preview_recommended" in client_fields
    assert "fit_to_view_preview_status" in client_fields
    assert "fit_to_view_preview_payload_hint" in client_fields

    diagnostics = capabilities["diagnostics"]
    receipt_fields = diagnostics["change_receipt_gui_current_revision_fields"]
    assert "fit_to_view_preview_recommended" in receipt_fields
    assert "fit_to_view_preview_blocking_reasons" in receipt_fields

    live_summary_fields = diagnostics["live_summary_fields"]
    assert "gui_current_revision_fit_to_view_preview_recommended" in live_summary_fields
    assert "gui_current_revision_payload_hint" in live_summary_fields
