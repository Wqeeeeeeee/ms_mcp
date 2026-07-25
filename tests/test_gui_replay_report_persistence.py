from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server
from material_studio_mcp_server.gui import (
    MaterialsStudioGuiController,
    ProcessInfo,
    WindowInfo,
)
from material_studio_mcp_server.specs import ExecutionMode
from material_studio_mcp_server.state.store import ProjectStore


REPLAY_VIEWS = [
    "front",
    "crystal_plane_0001",
    "top",
    "isometric",
    "crystal_100",
    "crystal_plane_001",
]


class FakeGuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False

    def __init__(self) -> None:
        self.opened_paths: list[Path] = []
        self.window = WindowInfo(
            handle=101,
            title="msmcp_r000_replay - Materials Studio",
            pid=2222,
            rect=(0, 0, 1024, 768),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=2222)]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and pid != self.window.pid:
            return None
        return self.window

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and pid != self.window.pid:
            return []
        return [self.window]

    def activate_window(self, window: WindowInfo) -> bool:
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        return output_path

    def open_file(self, path: Path) -> dict:
        self.opened_paths.append(path)
        return {"method": "fake", "path": str(path)}


class MultiProcessFakeGuiBackend(FakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.unrelated_window = WindowInfo(
            handle=202,
            title="Other - Materials Studio",
            pid=3333,
            rect=(0, 0, 900, 700),
            is_visible=True,
            is_minimized=False,
            is_foreground=False,
        )

    def list_processes(self) -> list[ProcessInfo]:
        return [
            ProcessInfo(name="MatStudio.exe", pid=2222),
            ProcessInfo(name="MatStudio.exe", pid=3333),
        ]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        windows = [self.window, self.unrelated_window]
        if pid is None:
            return windows
        return [window for window in windows if window.pid == pid]


def _tiny_bmp() -> bytes:
    width = 2
    height = 2
    row_stride = 8
    pixel_offset = 54
    image_size = row_stride * height
    file_size = pixel_offset + image_size
    header = (
        b"BM"
        + file_size.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + pixel_offset.to_bytes(4, "little")
    )
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + image_size.to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    bottom_row = bytes([0, 0, 0, 255, 255, 255, 0, 0])
    top_row = bytes([0, 0, 255, 0, 255, 0, 0, 0])
    return header + dib + bottom_row + top_row


def _prepare_replay_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_execution_mode: ExecutionMode = ExecutionMode.EXECUTE,
) -> tuple[dict, MaterialsStudioGuiController, Path, list[dict]]:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/"
            "molybdenum_disulfide_2d_mos2_monolayer_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = "snapshot_replay_persistence"
    created = server.material_studio_model_create_from_spec(
        payload,
        execution_mode=create_execution_mode,
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    exported = server.material_studio_model_export_view_bundle(
        project_id=created["project_id"],
        views=REPLAY_VIEWS,
        include_gui_snapshot=False,
        working_dir=str(tmp_path),
    )
    assert exported["ok"] is True

    store = ProjectStore(tmp_path)
    output_dir = store.outputs_dir(created["project_id"], created["revision"])
    manifest_path = output_dir / "gui_view_replay_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "project_id": created["project_id"],
                "revision": created["revision"],
                "spec_fingerprint": exported["view_audit"]["spec_fingerprint"],
                "replay_status": "complete",
                "view_names": REPLAY_VIEWS,
                "requested_view_count": len(REPLAY_VIEWS),
                "supported_view_count": len(REPLAY_VIEWS),
                "replay_summary": {
                    "trusted_accepted_event_count": len(REPLAY_VIEWS),
                    "accepted_view_count": len(REPLAY_VIEWS),
                    "accepted_view_names": REPLAY_VIEWS,
                    "evidence_integrity_status": "verified",
                    "journal_consistency_status": "consistent",
                    "all_supported_views_confirmed": True,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    refresh_calls: list[dict] = []

    def record_refresh(manifest: dict, **kwargs: object) -> None:
        refresh_calls.append({"manifest": manifest, **kwargs})

    monkeypatch.setattr(server, "_refresh_view_replay_summary", record_refresh)
    gui = MaterialsStudioGuiController(str(tmp_path), backend=FakeGuiBackend())
    return created, gui, manifest_path, refresh_calls


def _snapshot(tmp_path: Path) -> dict:
    path = tmp_path / "snapshot.bmp"
    path.write_bytes(_tiny_bmp())
    return {
        "screenshot_path": str(path),
        "analysis": {"readable": True, "likely_nonblank": True},
        "window": {
            "handle": 101,
            "title": "msmcp_r000_replay - Materials Studio",
            "is_foreground": True,
        },
    }


@pytest.mark.parametrize("operation", ["snapshot", "open", "confirmation"])
def test_gui_report_updates_preserve_current_trusted_view_replay(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, refresh_calls = _prepare_replay_project(
        tmp_path,
        monkeypatch,
    )
    project_id = created["project_id"]
    revision = created["revision"]
    structure_path = Path(created["planned_outputs"]["structure"])

    if operation == "snapshot":
        result = server._persist_gui_snapshot_report(
            project_id=project_id,
            revision=revision,
            snapshot=_snapshot(tmp_path),
            gui=gui,
            working_dir=str(tmp_path),
            views=None,
        )
    elif operation == "open":
        gui_open = {
            "project_id": project_id,
            "revision": revision,
            "structure_path": str(structure_path),
            "activated_opened_window": True,
            "window": {
                "handle": 101,
                "title": "msmcp_r000_replay - Materials Studio",
            },
            "open_result": {"method": "fake", "path": str(structure_path)},
        }
        result = server._persist_gui_open_structure_report(
            project_id=project_id,
            revision=revision,
            gui_open=gui_open,
            gui=gui,
            working_dir=str(tmp_path),
            views=None,
            gui_artifacts=[{"type": "gui_open", "result": gui_open}],
        )
    else:
        result = server._persist_gui_visual_confirmation_report(
            project_id=project_id,
            revision=revision,
            confirmation={
                "project_id": project_id,
                "revision": revision,
                "model_visible": True,
                "structure_unchanged": True,
                "note": "current replay remains trusted",
            },
            gui_status=gui.status(project_id=project_id, revision=revision),
            working_dir=str(tmp_path),
            views=None,
        )

    assert refresh_calls
    assert refresh_calls[-1]["manifest"]["project_id"] == project_id
    assert result["gui_view_replay"]["binding_verified"] is True
    assert result["trusted_clean_view_replay"]["ok"] is True
    assert result["trusted_clean_view_replay"]["view_selection_matches"] is True
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["gui_view_replay"]["binding_verified"] is True
    assert report["trusted_clean_view_replay"]["ok"] is True
    assert report["modeling_report"]["trusted_clean_view_replay"]["ok"] is True


def test_snapshot_report_does_not_trust_a_different_view_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, _ = _prepare_replay_project(tmp_path, monkeypatch)

    result = server._persist_gui_snapshot_report(
        project_id=created["project_id"],
        revision=created["revision"],
        snapshot=_snapshot(tmp_path),
        gui=gui,
        working_dir=str(tmp_path),
        views=["front"],
    )

    trusted = result["trusted_clean_view_replay"]
    assert trusted["ok"] is False
    assert trusted["view_selection_matches"] is False
    assert "view_replay_view_selection_mismatch" in trusted["blocking_reasons"]
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["trusted_clean_view_replay"]["ok"] is False


def test_apply_current_preview_preserves_current_trusted_view_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, refresh_calls = _prepare_replay_project(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.PREVIEW,
        working_dir=str(tmp_path),
    )

    assert refresh_calls
    assert result["diagnostic_export_view_resolution"]["source"] == (
        "current_revision_view_replay_manifest"
    )
    assert result["gui_view_replay"]["binding_verified"] is True
    assert result["trusted_clean_view_replay"]["ok"] is True
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["gui_view_replay"]["binding_verified"] is True
    assert report["trusted_clean_view_replay"]["ok"] is True


def test_apply_current_preview_invalidates_replay_for_explicit_view_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, _ = _prepare_replay_project(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.PREVIEW,
        views=["front"],
        working_dir=str(tmp_path),
    )

    assert result["diagnostic_export_view_resolution"]["source"] == (
        "explicit_request"
    )
    trusted = result["trusted_clean_view_replay"]
    assert trusted["ok"] is False
    assert trusted["view_selection_matches"] is False
    assert "view_replay_view_selection_mismatch" in trusted["blocking_reasons"]
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["trusted_clean_view_replay"]["ok"] is False


def test_apply_current_execute_keeps_replay_bound_through_same_window_hotload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, refresh_calls = _prepare_replay_project(
        tmp_path,
        monkeypatch,
        create_execution_mode=ExecutionMode.PREVIEW,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.EXECUTE,
        open_in_gui=True,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["result"]["success"] is True
    assert len(gui.backend.list_processes()) == 1
    assert result["gui_open"]["reuse_existing_window_only"] is True
    assert refresh_calls
    assert result["view_selection_resolution"]["source"] == (
        "persisted_current_revision"
    )
    assert result["gui_view_replay"]["binding_verified"] is True
    assert result["trusted_clean_view_replay"]["ok"] is True
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["gui_view_replay"]["binding_verified"] is True
    assert report["trusted_clean_view_replay"]["ok"] is True


def test_apply_current_preview_recovers_existing_live_revision_without_gui_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, _ = _prepare_replay_project(
        tmp_path,
        monkeypatch,
        create_execution_mode=ExecutionMode.PREVIEW,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    executed = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.EXECUTE,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    assert executed["ok"] is True
    structure_path = Path(executed["planned_outputs"]["structure"])
    wrapper = gui._create_project_wrapper(
        structure_path,
        project_id=created["project_id"],
        revision=created["revision"],
    )
    gui.backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=2222,
        rect=(0, 0, 1024, 768),
        is_visible=True,
        is_minimized=False,
        is_foreground=False,
    )
    live_status = gui.status(
        project_id=created["project_id"],
        revision=created["revision"],
    )
    assert live_status["current_revision_loaded"] is True
    snapshot_path = (
        Path(live_status["screenshots_dir"])
        / created["project_id"]
        / f"r{created['revision']:03d}"
        / "existing_live_revision.bmp"
    )
    gui.backend.capture_window(gui.backend.window, snapshot_path)
    open_count_before_preview = len(gui.backend.opened_paths)

    preview = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.PREVIEW,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
    )

    assert preview["ok"] is True
    assert preview["execution_mode"] == "preview"
    assert len(gui.backend.opened_paths) == open_count_before_preview
    request = preview["apply_current_request"]
    assert request["execution_started_by_request"] is False
    assert request["gui_input_started_by_request"] is False
    assert request["structure_reopened_by_request"] is False
    assert request["gui_process_launched_by_request"] is False
    assert request["current_revision_already_hot_loaded"] is True
    assert request["current_revision_hotload_evidence_source"] == (
        "live_status_current_revision"
    )
    evidence = request["live_status_hotload_evidence"]
    assert evidence["verified"] is True
    assert evidence["process_count"] == 1
    assert evidence["window_count"] == 1
    assert evidence["gui_input_performed"] is False
    gui_report = preview["modeling_report"]["gui"]
    assert gui_report["hot_loaded"] is True
    assert gui_report["hot_loaded_from_live_status"] is True
    assert gui_report["loaded_current_revision"] is True
    readiness = preview["modeling_report"]["live_readiness"]
    assert readiness["ready_for_hotload"] is False
    assert readiness["recommended_action"] != (
        "execute_current_revision_to_hotload_when_user_confirms"
    )
    gate = preview["modeling_report"]["normality_gate"]
    assert "preview_not_hot_loaded" not in gate["must_not_claim_normal_reasons"]
    assert "generated_structure_not_hot_loaded_in_gui" not in gate[
        "must_not_claim_normal_reasons"
    ]
    assert gate["can_claim_live_gui_normal"] is True
    assert preview["trusted_clean_view_replay"]["ok"] is True
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["modeling_report"]["gui"]["hot_loaded_from_live_status"] is True
    assert report["apply_current_request"]["current_revision_already_hot_loaded"] is True
    assert report["apply_current_request"][
        "current_revision_hotload_evidence_source"
    ] == request["current_revision_hotload_evidence_source"]

    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=True,
        working_dir=str(tmp_path),
    )
    status_health = status["modeling_health"]
    assert status_health["checks"]["gui_hot_loaded_from_live_status"] is True
    assert status_health["checks"]["gui_loaded_current_revision"] is True
    assert status_health["checks"]["gui_input_performed_by_current_request"] is False
    assert "GUI hot-load was not performed" not in "\n".join(
        status_health["warnings"]
    )
    assert status["modeling_report"]["acceptance_review"]["ok"] is True
    assert "acceptance_criteria_failed" not in status["modeling_report"][
        "normality_gate"
    ]["must_not_claim_normal_reasons"]
    assert status["modeling_report"]["normality_gate"][
        "can_claim_live_gui_normal"
    ] is True
    replay_progress = status["gui_view_replay"]["progress"]
    assert replay_progress["status"] == "complete"
    assert replay_progress["accepted_view_count"] == len(REPLAY_VIEWS)
    assert set(replay_progress["accepted_view_names"]) == set(REPLAY_VIEWS)
    assert replay_progress["remaining_supported_view_count"] == 0
    assert replay_progress["all_supported_views_confirmed"] is True
    assert replay_progress["trusted_complete"] is True
    assert status["gui_view_replay"]["accepted_view_count"] == len(REPLAY_VIEWS)
    assert status["gui_view_replay"]["all_supported_views_confirmed"] is True

    compact_status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=True,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    compact_progress = compact_status["gui_view_replay"]["progress"]
    assert compact_progress["status"] == replay_progress["status"]
    assert compact_progress["accepted_view_count"] == len(REPLAY_VIEWS)
    assert set(compact_progress["accepted_view_names"]) == set(REPLAY_VIEWS)
    assert compact_progress["all_supported_views_confirmed"] is True
    assert compact_progress["trusted_complete"] is True
    assert len(
        json.dumps(compact_status, ensure_ascii=False).encode("utf-8")
    ) < server.COMPACT_RESPONSE_MAX_BYTES


def test_live_status_preserves_loaded_revision_while_target_is_minimized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, _, _ = _prepare_replay_project(
        tmp_path,
        monkeypatch,
        create_execution_mode=ExecutionMode.PREVIEW,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    executed = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.EXECUTE,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    assert executed["ok"] is True
    structure_path = Path(executed["planned_outputs"]["structure"])
    wrapper = gui._create_project_wrapper(
        structure_path,
        project_id=created["project_id"],
        revision=created["revision"],
    )
    active_window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=2222,
        rect=(0, 0, 1024, 768),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    gui.backend.window = active_window
    live_status = gui.status(
        project_id=created["project_id"],
        revision=created["revision"],
    )
    assert live_status["current_revision_loaded"] is True
    snapshot_path = (
        Path(live_status["screenshots_dir"])
        / created["project_id"]
        / f"r{created['revision']:03d}"
        / "loaded_before_minimize.bmp"
    )
    gui.backend.capture_window(active_window, snapshot_path)
    gui.backend.window = WindowInfo(
        handle=active_window.handle,
        title=active_window.title,
        pid=active_window.pid,
        rect=(-32000, -32000, -31840, -31972),
        is_visible=True,
        is_minimized=True,
        is_foreground=False,
    )
    open_count_before_status = len(gui.backend.opened_paths)

    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=True,
        working_dir=str(tmp_path),
    )

    assert status["ok"] is True
    assert len(gui.backend.opened_paths) == open_count_before_status
    evidence = status["live_status_hotload_evidence"]
    assert evidence["verified"] is True
    assert evidence["loaded_revision_verified"] is True
    assert evidence["blocking_reasons"] == []
    assert evidence["binding_blocking_reasons"] == []
    assert evidence["interaction_ready"] is False
    assert evidence["interaction_status"] == "activation_required"
    assert "target_window_minimized_or_unknown" in evidence[
        "interaction_blocking_reasons"
    ]
    assert "target_window_not_foreground" in evidence[
        "interaction_blocking_reasons"
    ]
    assert evidence["activation_required_before_capture_or_input"] is True
    assert evidence["gui_input_performed"] is False
    assert evidence["structure_reopened"] is False
    assert evidence["gui_process_launched"] is False

    gui_report = status["modeling_report"]["gui"]
    assert gui_report["hot_loaded"] is True
    assert gui_report["hot_loaded_from_live_status"] is True
    assert gui_report["loaded_current_revision"] is True
    gui_current = status["gui_current_revision"]
    assert gui_current["status"] == "current_but_not_active"
    assert gui_current["loaded_current_revision"] is True
    assert gui_current["needs_reload"] is False
    assert gui_current["needs_activation"] is True
    assert gui_current["recommended_tool"] == "material_studio_gui_activate"
    assert status["live_hotload_preflight"]["current_revision_loaded"] is True
    assert status["live_gui_acceptance"]["window_binding_ok"] is True
    assert status["live_gui_acceptance"]["binding_failures"] == []
    assert status["modeling_health"]["checks"][
        "gui_hot_loaded_from_live_status"
    ] is True
    assert status["modeling_health"]["checks"][
        "gui_interaction_ready_from_live_status"
    ] is False
    assert status["modeling_health"]["checks"][
        "gui_activation_required_before_capture_or_input"
    ] is True
    assert "GUI hot-load was not performed" not in "\n".join(
        status["modeling_health"]["warnings"]
    )
    gate = status["normality_gate"]
    assert "generated_structure_not_hot_loaded_in_gui" not in gate[
        "must_not_claim_normal_reasons"
    ]
    assert "execute_mode_without_gui_hotload" not in gate["review_reasons"]
    assert gate["can_claim_live_gui_normal"] is True
    decision = status["normality_decision"]
    assert decision["binding_verified"] is True
    assert decision["can_claim_live_gui_normal"] is True
    assert status["gui_status"]["recommended_tool"] == (
        "material_studio_gui_activate"
    )
    assert status["gui_status"]["activation_required_before_capture_or_input"] is True


def test_live_status_accepts_exact_project_target_amid_unrelated_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, _, _ = _prepare_replay_project(
        tmp_path,
        monkeypatch,
        create_execution_mode=ExecutionMode.PREVIEW,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    executed = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.EXECUTE,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    structure_path = Path(executed["planned_outputs"]["structure"])
    wrapper = gui._create_project_wrapper(
        structure_path,
        project_id=created["project_id"],
        revision=created["revision"],
    )
    backend = MultiProcessFakeGuiBackend()
    backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=2222,
        rect=(0, 0, 1024, 768),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    gui.backend = backend

    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=True,
        working_dir=str(tmp_path),
    )
    evidence = status["live_status_hotload_evidence"]
    management = status["gui_status"]["window_management"]

    assert management["process_count"] == 2
    assert management["project_scoped_multi_instance_isolation"] is True
    assert management["window_isolation_mode"] == (
        "exact_project_target_process"
    )
    assert management["single_window_policy_ok"] is True
    assert management["target_process_id"] == 2222
    assert management["unrelated_process_ids"] == [3333]
    assert evidence["verified"] is True
    assert evidence["loaded_revision_verified"] is True
    assert evidence["binding_blocking_reasons"] == []
    assert "matstudio_process_count_not_one" not in evidence[
        "blocking_reasons"
    ]
    assert "matstudio_window_count_not_one" not in evidence[
        "blocking_reasons"
    ]
    assert status["modeling_report"]["gui"]["hot_loaded_from_live_status"] is True


def test_apply_current_preview_does_not_recover_live_state_from_failed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, _, _ = _prepare_replay_project(
        tmp_path,
        monkeypatch,
        create_execution_mode=ExecutionMode.PREVIEW,
    )
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    executed = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.EXECUTE,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    result_metadata_path = Path(executed["result_metadata_path"])
    result_metadata = json.loads(result_metadata_path.read_text(encoding="utf-8"))
    result_metadata["success"] = False
    result_metadata_path.write_text(
        json.dumps(result_metadata, indent=2),
        encoding="utf-8",
    )
    structure_path = Path(executed["planned_outputs"]["structure"])
    wrapper = gui._create_project_wrapper(
        structure_path,
        project_id=created["project_id"],
        revision=created["revision"],
    )
    gui.backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=2222,
        rect=(0, 0, 1024, 768),
        is_visible=True,
        is_minimized=False,
        is_foreground=False,
    )

    preview = server.material_studio_gui_apply_current_revision(
        project_id=created["project_id"],
        execution_mode=ExecutionMode.PREVIEW,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
    )

    evidence = preview["apply_current_request"]["live_status_hotload_evidence"]
    assert evidence["verified"] is False
    assert "successful_revision_result_missing" in evidence["blocking_reasons"]
    assert preview["apply_current_request"]["current_revision_already_hot_loaded"] is False
    assert preview["modeling_report"]["gui"]["hot_loaded_from_live_status"] is False
    assert "preview_not_hot_loaded" in preview["modeling_report"][
        "normality_gate"
    ]["must_not_claim_normal_reasons"]

    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=True,
        working_dir=str(tmp_path),
    )
    status_health = status["modeling_health"]
    assert status["live_status_hotload_evidence"]["verified"] is False
    assert "successful_revision_result_missing" in status[
        "live_status_hotload_evidence"
    ]["blocking_reasons"]
    assert status_health["checks"].get("gui_hot_loaded_from_live_status") is not True
    assert status_health["checks"]["gui_opened"] is None
    assert status["modeling_report"]["gui"]["hot_loaded"] is False
    assert status["modeling_report"]["gui"]["hot_loaded_from_live_status"] is False
    assert "preview_not_hot_loaded" in status["modeling_report"][
        "normality_gate"
    ]["must_not_claim_normal_reasons"]
