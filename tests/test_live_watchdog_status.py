from __future__ import annotations

import copy
import json
from typing import Any

from material_studio_mcp_server import server


def _runtime_current() -> dict[str, Any]:
    return {
        "schema": "material_studio_mcp_runtime_provenance_v1",
        "status": "current",
        "source_current": True,
        "restart_required": False,
        "runtime_instance_id": "runtime-test",
        "restart_action": None,
    }


def _status_payload(*, revision: int = 4) -> dict[str, Any]:
    project_id = "watchdog_semiconductor"
    return {
        "ok": True,
        "working_dir": "C:\\watchdog-workspace",
        "project_id": project_id,
        "revision": revision,
        "execution_runtime": {
            "schema_version": "material_studio_execution_runtime_v1",
            "status": "completed",
            "active": False,
            "lock_observation_stable": True,
            "consistency": {"ok": True, "issue_codes": []},
            "continuation": {
                "automatic_retry_allowed": False,
                "explicit_execute_confirmation_required": False,
                "execution_may_still_be_running": False,
            },
        },
        "gui_status": {
            "status": "target_window_ready",
            "process_count": 1,
            "window_count": 1,
            "single_window_policy_ok": True,
            "single_window_violation_reasons": [],
            "workspace_context_mismatch": False,
            "target_window_found": True,
            "target_window_is_foreground": True,
            "target_window": {
                "handle": 101,
                "title": "watchdog r004 - Materials Studio",
                "is_visible": True,
                "is_minimized": False,
            },
            "window_management": {
                "single_window_policy_ok": True,
                "single_window_violation_reasons": [],
                "warnings": [],
            },
        },
        "gui_current_revision": {
            "status": "current_and_active",
            "loaded_current_revision": True,
            "hot_loaded": True,
            "single_window_policy_ok": True,
            "target_window_handle": 101,
            "target_window_title": "watchdog r004 - Materials Studio",
            "target_window_identity_verified": True,
            "needs_reload": False,
            "needs_activation": False,
            "needs_snapshot": False,
            "needs_single_window_resolution": False,
            "visual_validation": "passed",
        },
        "gui_view_replay": {
            "progress": {
                "schema_version": "material_studio_gui_view_replay_progress_v1",
                "available": True,
                "status": "complete",
                "project_id": project_id,
                "revision": revision,
                "binding_verified": True,
                "requested_view_count": 3,
                "supported_view_count": 3,
                "accepted_view_count": 3,
                "accepted_view_names": ["front", "top", "isometric"],
                "pending_view_count": 0,
                "pending_view_names": [],
                "accepted_view_count_consistent": True,
                "pending_view_count_consistent": True,
                "all_supported_views_confirmed": True,
                "evidence_integrity_status": "verified",
                "journal_consistency_status": "consistent",
                "trusted_complete": True,
                "blocking_reasons": [],
            }
        },
        "normality_gate": {
            "available": True,
            "status": "claimable_with_calculation_review",
            "normality": "review_warnings",
            "trust_level": "review",
            "can_claim_model_normal": True,
            "can_claim_live_gui_normal": True,
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
            "hot_loaded": True,
            "gui_loaded_current_revision": True,
            "trusted_clean_view_replay_ok": True,
            "primary_reason": "semiconductor:kpoint_review",
            "must_not_claim_live_gui_normal_reasons": [],
            "calculation_blocking_reasons": ["semiconductor:kpoint_review"],
            "review_reasons": ["semiconductor:kpoint_review"],
            "resolved_visual_review_reasons": [],
        },
        "mcp_client_readiness": {
            "status": "model_normal_calculation_review",
            "state": "hot_loaded_with_review",
            "can_accept_modeling_request": True,
            "can_accept_followup_request": True,
            "can_accept_preview_request": True,
            "ready_for_live_edit": True,
            "ready_for_live_hotload": True,
            "ready_for_calculation": False,
            "current_revision_loaded_in_gui": True,
            "next_edit_status": "ready",
            "next_edit_requires_reaudit": False,
            "blocking_reasons": [],
            "review_reasons": ["semiconductor:kpoint_review"],
        },
        "next_action_plan": {
            "state": "hot_loaded_with_review",
            "action_id": "apply_recommended_semiconductor_kpoint_grid",
            "project_id": project_id,
            "revision": revision,
            "recommended_tool": "material_studio_live_update_with_patch",
            "recommended_action": "apply_recommended_kpoints_then_reaudit",
            "needs_user_confirmation": True,
            "safe_to_call_without_confirmation": False,
            "binding_verified": True,
            "payload_hint_is_directly_callable": True,
            "payload_hint": {
                "project_id": project_id,
                "base_revision": revision,
                "execution_mode": "preview",
                "confirm_recommended_calculation_settings": False,
            },
            "blocking_reasons": [],
            "review_reasons": ["semiconductor:kpoint_review"],
        },
    }


def _install_status(
    monkeypatch,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(server, "runtime_provenance_status", _runtime_current)

    def fake_status(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return copy.deepcopy(payload)

    monkeypatch.setattr(server, "material_studio_live_project_status", fake_status)
    return calls


def test_watchdog_receipt_is_stable_bounded_and_confirmation_safe(monkeypatch) -> None:
    calls = _install_status(monkeypatch, _status_payload())

    first = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=True,
        working_dir="C:\\watchdog-workspace",
    )
    second = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        previous_state_fingerprint=first["state_fingerprint"],
        include_gui_status=True,
        working_dir="C:\\watchdog-workspace",
    )

    assert first["schema_version"] == server.LIVE_WATCHDOG_STATUS_SCHEMA
    assert first["status"] == "awaiting_user_confirmation"
    assert first["revision_matches_expected"] is True
    assert first["gui"]["process_count"] == 1
    assert first["gui"]["window_count"] == 1
    assert first["gui"]["single_window_policy_ok"] is True
    assert first["view_replay"]["trusted_complete"] is True
    assert first["normality"]["can_claim_live_gui_normal"] is True
    assert first["primary_action"]["needs_user_confirmation"] is True
    assert first["primary_action"]["automatic_call_allowed"] is False
    assert first["primary_action"]["payload"]["included"] is True
    assert first["poll_action"]["automatic_call_allowed"] is True
    assert first["safety"]["automatic_non_poll_action_allowed"] is False
    assert first["safety"]["automatic_execution_retry_allowed"] is False
    assert first["state_fingerprint"] == second["state_fingerprint"]
    assert second["changed_since_previous"] is False
    assert all(call["response_mode"] == server.McpResponseMode.FULL for call in calls)
    assert len(json.dumps(first, ensure_ascii=False).encode("utf-8")) < (
        server.LIVE_WATCHDOG_MAX_BYTES
    )


def test_watchdog_revision_change_preserves_expected_revision_gate(monkeypatch) -> None:
    _install_status(monkeypatch, _status_payload(revision=5))

    receipt = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=False,
        working_dir="C:\\watchdog-workspace",
    )

    assert receipt["status"] == "current_revision_changed"
    assert receipt["revision"] == 5
    assert receipt["revision_matches_expected"] is False
    assert "expected_revision_mismatch" in receipt["blocking_reasons"]
    assert receipt["poll_action"]["payload_hint"]["expected_revision"] == 4
    assert receipt["automatic_followup_action_allowed"] is False


def test_watchdog_fingerprint_tracks_payload_and_action_binding(monkeypatch) -> None:
    payload = _status_payload()
    calls = _install_status(monkeypatch, payload)
    first = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=False,
        working_dir="C:\\watchdog-workspace",
    )

    changed = _status_payload()
    changed["next_action_plan"]["payload_hint"]["recommended_kpoints"] = [9, 9, 9]

    def changed_status(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return copy.deepcopy(changed)

    monkeypatch.setattr(server, "material_studio_live_project_status", changed_status)
    second = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        previous_state_fingerprint=first["state_fingerprint"],
        include_gui_status=False,
        working_dir="C:\\watchdog-workspace",
    )

    assert second["changed_since_previous"] is True
    assert second["state_fingerprint"] != first["state_fingerprint"]

    changed["next_action_plan"]["project_id"] = "wrong_project"
    third = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=False,
        working_dir="C:\\watchdog-workspace",
    )
    assert third["status"] == "primary_action_binding_blocked"
    assert "primary_action_project_id_mismatch" in third["blocking_reasons"]


def test_watchdog_running_execution_only_allows_read_only_poll(monkeypatch) -> None:
    payload = _status_payload()
    payload["execution_runtime"].update(
        {
            "status": "running",
            "active": True,
            "continuation": {
                "automatic_retry_allowed": False,
                "explicit_execute_confirmation_required": False,
                "execution_may_still_be_running": True,
            },
        }
    )
    payload["next_action_plan"] = {}
    _install_status(monkeypatch, payload)

    receipt = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=False,
        working_dir="C:\\watchdog-workspace",
    )

    assert receipt["status"] == "execution_in_progress"
    assert receipt["execution"]["active"] is True
    assert receipt["execution"]["automatic_retry_allowed"] is False
    assert receipt["poll_action"]["automatic_call_allowed"] is True
    assert receipt["detail_action"]["automatic_call_allowed"] is False
    assert receipt["safety"]["automatic_execution_retry_allowed"] is False


def test_watchdog_stale_runtime_stops_before_project_or_gui_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "runtime_provenance_status",
        lambda: {
            "schema": "material_studio_mcp_runtime_provenance_v1",
            "status": "source_changed_since_start",
            "source_current": False,
            "restart_required": True,
            "runtime_instance_id": "runtime-stale",
            "restart_action": "restart_mcp_server_then_retry_preflight",
        },
    )

    def unexpected_status(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError(f"status probe must be deferred: {kwargs}")

    monkeypatch.setattr(server, "material_studio_live_project_status", unexpected_status)

    receipt = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=True,
        working_dir="C:\\watchdog-workspace",
    )

    assert receipt["status"] == "mcp_server_restart_required"
    assert receipt["runtime"]["restart_required"] is True
    assert receipt["poll_action"]["automatic_call_allowed"] is True
    assert receipt["detail_action"]["automatic_call_allowed"] is False
    assert receipt["safety"]["automatic_non_poll_action_allowed"] is False


def test_watchdog_hard_budget_fallback_never_exposes_callable_payload(monkeypatch) -> None:
    payload = _status_payload()
    payload["gui_status"]["target_window"]["title"] = "W" * 40_000
    payload["gui_current_revision"]["target_window_title"] = "W" * 40_000
    payload["next_action_plan"]["payload_hint"] = {
        "project_id": "watchdog_semiconductor",
        "spec": {"blob": "X" * 40_000},
    }
    _install_status(monkeypatch, payload)

    receipt = server.material_studio_live_watchdog_status(
        project_id="watchdog_semiconductor",
        expected_revision=4,
        include_gui_status=True,
        working_dir="C:\\watchdog-workspace",
    )

    assert len(json.dumps(receipt, ensure_ascii=False).encode("utf-8")) < (
        server.LIVE_WATCHDOG_MAX_BYTES
    )
    assert receipt["response_compaction"]["hard_budget_applied"] is True
    assert receipt["poll_action"]["automatic_call_allowed"] is False
    assert receipt["poll_action"]["payload_omitted"] is True
    assert receipt["primary_action"]["automatic_call_allowed"] is False
    assert "payload" not in receipt["primary_action"]
