from __future__ import annotations

from pathlib import Path

from material_studio_mcp_server import server


def _snapshot(tmp_path: Path, management: dict) -> dict:
    path = tmp_path / "snapshot.bmp"
    path.write_bytes(b"bmp")
    return {
        "screenshot_path": str(path),
        "window": {"handle": 10, "is_foreground": True},
        "window_management": management,
    }


def test_completed_snapshot_consumes_repeat_snapshot_recommendation(
    tmp_path: Path,
) -> None:
    management = {
        "status": "ready_for_same_window_live_edit",
        "needs_snapshot": True,
        "recommended_tool": "material_studio_gui_snapshot",
        "recommended_action": "snapshot_target_project_window",
        "payload_hint": {"reuse_existing_window_only": True},
        "single_window_policy_ok": True,
    }

    receipt = server._gui_snapshot_completed_receipt(
        activation_result={"window_management": management},
        snapshot=_snapshot(tmp_path, management),
        project_id="project",
        revision=2,
        working_dir=tmp_path,
    )

    assert receipt["status"] == "gui_snapshot_captured"
    assert receipt["snapshot_status"] == "captured"
    assert receipt["snapshot_captured"] is True
    assert receipt["snapshot_deferred"] is False
    assert receipt["snapshot_evidence_persisted"] is True
    assert receipt["pre_snapshot_window_management"]["needs_snapshot"] is True
    assert receipt["window_management"]["needs_snapshot"] is False
    assert receipt["window_management"]["recommended_tool"] == (
        "material_studio_live_project_status"
    )
    assert receipt["window_management"]["payload_hint"] == {
        "project_id": "project",
        "include_gui_status": True,
        "response_mode": "compact",
        "working_dir": str(tmp_path.resolve()),
    }
    assert receipt["snapshot"]["window_management_before_capture"]["needs_snapshot"] is True
    assert receipt["snapshot"]["window_management"]["needs_snapshot"] is False


def test_completed_snapshot_does_not_hide_single_window_blocker(tmp_path: Path) -> None:
    management = {
        "status": "single_window_policy_violation",
        "needs_snapshot": False,
        "recommended_tool": "material_studio_gui_status",
        "recommended_action": "close_save_extra_matstudio_windows_then_retry_hotload",
        "payload_hint": {"reuse_existing_window_only": True},
        "single_window_policy_ok": False,
        "single_window_violation_reasons": ["multiple_matstudio_windows_detected"],
    }

    receipt = server._gui_snapshot_completed_receipt(
        activation_result={"window_management": management},
        snapshot=_snapshot(tmp_path, management),
        project_id="project",
        revision=2,
        working_dir=tmp_path,
    )

    assert receipt["snapshot_captured"] is True
    assert receipt["window_management"]["needs_snapshot"] is False
    assert receipt["window_management"]["recommended_tool"] == (
        "material_studio_gui_status"
    )
    assert receipt["window_management"]["single_window_policy_ok"] is False
    assert receipt["window_management"]["single_window_violation_reasons"] == [
        "multiple_matstudio_windows_detected"
    ]
