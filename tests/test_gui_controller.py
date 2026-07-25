from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import material_studio_mcp_server.gui as gui_module
from material_studio_mcp_server.gui import (
    GuiError,
    GuiSnapshotBlockedError,
    MaterialsStudioGuiController,
    NullGuiBackend,
    ProcessInfo,
    WindowInfo,
    WindowsGuiBackend,
    _analyze_bmp_snapshot,
    _window_priority,
)


def _verified_source_wrapper_provenance() -> dict:
    return {
        "auto_save_allowed": True,
        "status": "verified_same_workspace_revision_wrapper",
        "reason_codes": [],
    }


def _unverified_source_wrapper_provenance() -> dict:
    return {
        "auto_save_allowed": False,
        "status": "auto_save_not_authorized",
        "reason_codes": ["source_wrapper_provenance_unverified"],
    }


def _write_test_wrapper(
    workspace: Path,
    *,
    project_name: str,
    project_id: str,
    revision: int,
) -> Path:
    project_dir = workspace / "gui_projects" / project_name
    unique_part = project_name.rsplit("_", 1)[1]
    document_name = f"model_r{revision:03d}_{unique_part}.xsd"
    document_path = (
        project_dir
        / f"{project_name}_Files"
        / "Documents"
        / document_name
    )
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text("<XSD/>\n", encoding="utf-8")
    source_path = (
        workspace
        / project_id
        / "outputs"
        / f"r{revision:03d}"
        / document_name
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(document_path.read_bytes())
    revision_path = (
        workspace
        / project_id
        / "revisions"
        / f"r{revision:03d}_model_spec.json"
    )
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(
        json.dumps({"project_id": project_id, "revision": revision}),
        encoding="utf-8",
    )
    project_path = project_dir / f"{project_name}.stp"
    project_path.write_text(
        (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<Project>\n"
            "  <Version>20.1</Version>\n"
            "  <DocumentManager><Document>"
            f"<URL>.\\{document_name}</URL>"
            "</Document></DocumentManager>\n"
            "  <ViewRegistry><Frame><View>"
            "<Type>SVViewer3D.Viewer3DControl</Type>"
            "</View></Frame></ViewRegistry>\n"
            "</Project>\n"
        ),
        encoding="utf-8",
    )
    project_sha256 = hashlib.sha256(project_path.read_bytes()).hexdigest()
    document_sha256 = hashlib.sha256(document_path.read_bytes()).hexdigest()
    identity_path = project_dir / "wrapper_identity.json"
    identity = {
        "identity_schema_version": 1,
        "identity_profile": "materials_studio_revision_wrapper_identity_v1",
        "project_name": project_name,
        "project_id": project_id,
        "revision": revision,
        "source_path": str(source_path.resolve()),
        "source_sha256": document_sha256,
        "source_size_bytes": document_path.stat().st_size,
        "document_name": document_name,
        "document_sha256": document_sha256,
        "document_size_bytes": document_path.stat().st_size,
        "project_file_sha256": project_sha256,
        "project_file_size_bytes": project_path.stat().st_size,
    }
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    (project_dir / "metadata.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "revision": revision,
                "project_name": project_name,
                "document_name": document_name,
                "source_path": str(source_path.resolve()),
                "wrapper_schema_version": 3,
                "wrapper_profile": "materials_studio_20_1_project_wrapper_v2",
                "project_file_sha256": project_sha256,
                "project_file_size_bytes": project_path.stat().st_size,
                "document_sha256": document_sha256,
                "document_size_bytes": document_path.stat().st_size,
                "source_sha256": document_sha256,
                "source_size_bytes": document_path.stat().st_size,
                "identity_manifest_name": identity_path.name,
                "identity_manifest_sha256": hashlib.sha256(
                    identity_path.read_bytes()
                ).hexdigest(),
                "identity_manifest_size_bytes": identity_path.stat().st_size,
            }
        ),
        encoding="utf-8",
    )
    return project_path


def _bind_structure_to_revision(
    controller: MaterialsStudioGuiController,
    structure: Path,
    *,
    project_id: str,
    revision: int,
) -> Path:
    source = structure.expanduser().resolve()
    bound_source = (
        controller.workspace_root
        / project_id
        / "outputs"
        / f"r{revision:03d}"
        / source.name
    ).resolve()
    bound_source.parent.mkdir(parents=True, exist_ok=True)
    if source != bound_source:
        bound_source.write_bytes(source.read_bytes())
    revision_path = (
        controller.workspace_root
        / project_id
        / "revisions"
        / f"r{revision:03d}_model_spec.json"
    )
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(
        json.dumps({"project_id": project_id, "revision": revision}),
        encoding="utf-8",
    )
    return bound_source


def _create_bound_wrapper(
    controller: MaterialsStudioGuiController,
    structure: Path,
    *,
    project_id: str,
    revision: int,
) -> dict:
    return controller._create_project_wrapper(
        _bind_structure_to_revision(
            controller,
            structure,
            project_id=project_id,
            revision=revision,
        ),
        project_id=project_id,
        revision=revision,
    )


class FakeGuiBackend:
    supported = True
    unavailable_reason = None

    def __init__(self) -> None:
        self.window = WindowInfo(handle=100, title="Untitled - Materials Studio", pid=1234, rect=(0, 0, 800, 600))
        self.opened: list[Path] = []
        self.activated_handles: list[int] = []

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=1234)]

    def find_window(self) -> WindowInfo | None:
        return self.window

    def activate_window(self, window: WindowInfo) -> bool:
        self.activated_handles.append(window.handle)
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        return output_path

    def open_file(self, path: Path) -> dict:
        self.opened.append(path)
        return {"method": "fake", "path": str(path)}

    def launch_app(self) -> dict:
        return {"method": "fake", "pid": self.window.pid}


class LaunchingFakeGuiBackend(FakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.window = None
        self.launch_count = 0

    def list_processes(self) -> list[ProcessInfo]:
        if self.window is None:
            return []
        return [ProcessInfo(name="MatStudio.exe", pid=self.window.pid or 1234)]

    def find_window(self) -> WindowInfo | None:
        return self.window

    def launch_app(self) -> dict:
        self.launch_count += 1
        self.window = WindowInfo(handle=101, title="Untitled - Materials Studio", pid=4321, rect=(0, 0, 900, 700))
        return {"method": "fake_launch", "pid": 4321}


class ProcessOnlyFakeGuiBackend(LaunchingFakeGuiBackend):
    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=9999)]

    def launch_app(self) -> dict:
        self.launch_count += 1
        raise AssertionError("launch_app must not be called while MatStudio.exe is already running")


class SpawningOpenFakeGuiBackend(FakeGuiBackend):
    file_open_may_launch_new_instance = True


class SameWindowOpenFakeGuiBackend(SpawningOpenFakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.same_window_opened: list[tuple[int, Path]] = []

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        self.same_window_opened.append((window.handle, path))
        return {
            "method": "fake_same_window_open",
            "path": str(path),
            "window": window.to_dict(),
            "same_window_open_requested": True,
        }


class SpawnAfterSameWindowOpenFakeGuiBackend(SameWindowOpenFakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.spawned_pid = 9999
        self.spawned = False

    def list_processes(self) -> list[ProcessInfo]:
        processes = super().list_processes()
        if self.spawned:
            processes.append(ProcessInfo(name="MatStudio.exe", pid=self.spawned_pid))
        return processes

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        result = super().open_file_in_existing_window(window, path)
        self.spawned = True
        result["spawned_process_ids"] = [self.spawned_pid]
        return result


class SameWindowWindowsFakeGuiBackend(WindowsGuiBackend):
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = True

    def __init__(self) -> None:
        self.window = WindowInfo(handle=111, title="current - Materials Studio", pid=2233, rect=(0, 0, 900, 700))
        self.same_window_opened: list[tuple[int, Path]] = []
        self.opened: list[Path] = []
        self.activated_handles: list[int] = []

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=self.window.pid or 2233)]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and self.window.pid != pid:
            return None
        return self.window

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and self.window.pid != pid:
            return []
        return [self.window]

    def activate_window(self, window: WindowInfo) -> bool:
        self.activated_handles.append(window.handle)
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        return output_path

    def open_file(self, path: Path) -> dict:
        self.opened.append(path)
        return {"method": "fake_windows_open", "path": str(path)}

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        self.same_window_opened.append((window.handle, path))
        self.window = WindowInfo(
            handle=window.handle,
            title=f"{path.stem} - Materials Studio",
            pid=window.pid,
            rect=window.rect,
        )
        return {
            "method": "fake_windows_same_window_open",
            "path": str(path),
            "window": window.to_dict(),
            "same_window_open_requested": True,
        }

    def dismiss_file_association_dialogs(self, *, pid: int | None = None, timeout_seconds: float = 8.0) -> list[dict]:
        return []

    def dismiss_startup_dialogs(self, *, pid: int | None = None, timeout_seconds: float = 8.0) -> list[dict]:
        return []


class StartupDialogWindowsFakeGuiBackend(SameWindowWindowsFakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.dialogs = [
            WindowInfo(
                handle=204,
                title="Welcome to Materials Studio",
                pid=self.window.pid,
                rect=(20, 20, 500, 300),
                class_name="#32770",
            ),
            WindowInfo(
                handle=205,
                title="New Project",
                pid=self.window.pid,
                rect=(30, 30, 700, 500),
                class_name="#32770",
            ),
        ]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        windows = [self.window, *self.dialogs]
        if pid is None:
            return windows
        return [window for window in windows if window.pid == pid]

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        self.dialogs = []
        return super().open_file_in_existing_window(window, path)


class NewWindowAfterOpenFakeGuiBackend(FakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.opened_window = WindowInfo(handle=200, title="opened_project - Materials Studio", pid=5678, rect=(0, 0, 900, 700))

    def open_file(self, path: Path) -> dict:
        result = super().open_file(path)
        self.window = self.opened_window
        return result


class DelayedWrapperTitleFakeGuiBackend(FakeGuiBackend):
    def __init__(self, *, project_name: str) -> None:
        super().__init__()
        self.wrapper_window = WindowInfo(
            handle=300,
            title=f"{project_name} - Materials Studio",
            pid=1234,
            rect=(0, 0, 900, 700),
        )
        self.find_count = 0

    def find_window(self) -> WindowInfo | None:
        self.find_count += 1
        if self.find_count >= 2:
            self.window = self.wrapper_window
        return self.window

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        return [self.window]


class MultiWindowFakeGuiBackend(FakeGuiBackend):
    def __init__(self) -> None:
        super().__init__()
        self.default_window = self.window
        self.windows = [self.default_window]
        self.captured_handles: list[int] = []

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is None:
            return list(self.windows)
        return [window for window in self.windows if window.pid == pid]

    def activate_window(self, window: WindowInfo) -> bool:
        self.activated_handles.append(window.handle)
        return any(candidate.handle == window.handle for candidate in self.windows)

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        self.captured_handles.append(window.handle)
        return super().capture_window(window, output_path)


class MultiProcessSameWindowOpenFakeGuiBackend(MultiWindowFakeGuiBackend):
    file_open_may_launch_new_instance = True

    def __init__(self) -> None:
        super().__init__()
        self.same_window_opened: list[tuple[int, Path]] = []
        self.process_ids = {1234}

    def list_processes(self) -> list[ProcessInfo]:
        return [
            ProcessInfo(name="MatStudio.exe", pid=pid)
            for pid in sorted(self.process_ids)
        ]

    def open_file_in_existing_window(
        self,
        window: WindowInfo,
        path: Path,
    ) -> dict:
        self.same_window_opened.append((window.handle, path))
        return {
            "method": "fake_same_window_open",
            "path": str(path),
            "window": window.to_dict(),
            "same_window_open_requested": True,
        }


class DuplicateWrapperAfterSameWindowOpenFakeGuiBackend(
    MultiProcessSameWindowOpenFakeGuiBackend,
    WindowsGuiBackend,
):
    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and self.window.pid != pid:
            return next(
                (window for window in self.windows if window.pid == pid),
                None,
            )
        return self.window

    def dismiss_file_association_dialogs(
        self,
        *,
        pid: int | None = None,
        timeout_seconds: float = 8.0,
    ) -> list[dict]:
        return []

    def dismiss_startup_dialogs(
        self,
        *,
        pid: int | None = None,
        timeout_seconds: float = 8.0,
    ) -> list[dict]:
        return []

    def open_file_in_existing_window(
        self,
        window: WindowInfo,
        path: Path,
    ) -> dict:
        self.same_window_opened.append((window.handle, path))
        opened = WindowInfo(
            handle=window.handle,
            title=f"{path.stem} - Materials Studio",
            pid=window.pid,
            rect=window.rect,
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )
        duplicate = WindowInfo(
            handle=window.handle + 1000,
            title=opened.title,
            pid=5678,
            rect=(40, 40, 940, 740),
            is_visible=True,
            is_minimized=False,
            is_foreground=False,
        )
        self.window = opened
        self.windows = [opened, duplicate]
        self.process_ids.add(duplicate.pid)
        return {
            "method": "fake_same_window_open",
            "path": str(path),
            "window": window.to_dict(),
            "same_window_open_requested": True,
        }


class MinimizedGuiBackend(FakeGuiBackend):
    def __init__(self, *, restore_on_activate: bool = True) -> None:
        super().__init__()
        self.restore_on_activate = restore_on_activate
        self.window = WindowInfo(
            handle=100,
            title="minimized - Materials Studio",
            pid=1234,
            rect=(-32000, -32000, -31840, -31972),
            is_visible=True,
            is_minimized=True,
            is_foreground=False,
        )
        self.captured_handles: list[int] = []

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and self.window.pid != pid:
            return []
        return [self.window]

    def activate_window(self, window: WindowInfo) -> bool:
        self.activated_handles.append(window.handle)
        if self.restore_on_activate:
            self.window = WindowInfo(
                handle=window.handle,
                title=window.title,
                pid=window.pid,
                rect=(0, 0, 1024, 768),
                is_visible=True,
                is_minimized=False,
                is_foreground=True,
            )
        return True

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        self.captured_handles.append(window.handle)
        return super().capture_window(window, output_path)


def test_gui_backend_can_be_explicitly_disabled_for_headless_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_GUI_BACKEND", "null")

    headless = MaterialsStudioGuiController(tmp_path / "headless")
    explicit = FakeGuiBackend()
    injected = MaterialsStudioGuiController(tmp_path / "injected", backend=explicit)

    assert isinstance(headless.backend, NullGuiBackend)
    assert injected.backend is explicit


def test_gui_status_activate_snapshot_and_logs(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()
    assert status["ok"] is True
    assert status["status"] == "ready_for_same_window_live_edit"
    assert status["recommended_tool"] == "material_studio_gui_snapshot"
    assert status["recommended_action"] == "snapshot_target_project_window"
    assert status["process_found"] is True
    assert status["window_found"] is True
    assert status["window_count"] == 1
    assert status["selected_window_handle"] == 100
    assert status["windows"][0]["is_selected"] is True
    assert status["single_window_policy_ok"] is True
    assert status["single_window_violation_reasons"] == []
    assert status["single_window_policy"]["hotload_requires_existing_window"] is True
    assert status["single_window_policy"]["auto_launch_during_open_allowed"] is False
    assert status["can_open_structure_in_existing_window"] is True
    assert status["can_apply_current_revision_without_new_window"] is True
    assert status["ready_for_next_live_edit"] is True
    assert status["needs_single_window_resolution"] is False
    assert status["needs_dialog_resolution"] is False
    assert status["auto_launch_allowed"] is False
    assert status["window_management"]["single_window_policy_ok"] is True
    assert status["window_management"]["single_window_violation_reasons"] == []
    assert status["window_management"]["status"] == "ready_for_same_window_live_edit"
    assert status["window_management"]["ready_for_next_live_edit"] is True
    assert status["window_management"]["can_hotload_without_new_window"] is True
    assert status["window_management"]["can_apply_current_revision_without_new_window"] is True
    assert status["window_management"]["same_window_required"] is True
    assert status["window_management"]["auto_launch_allowed"] is False
    assert status["window_management"]["payload_hint"]["reuse_existing_window_only"] is True
    assert "list_matstudio_windows" in status["capabilities"]
    assert status["local_uia_view_replay_view_names"] == [
        "back",
        "bottom",
        "front",
        "isometric",
        "left",
        "right",
        "top",
    ]
    assert status["local_uia_view_replay_supported"] is bool(
        controller.view_replay_backend.supported
    )
    assert status["local_uia_miller_plane_transaction_supported"] is bool(
        controller.view_replay_backend.miller_plane_transaction_supported
    )
    assert status[
        "local_uia_exact_collinear_direction_transaction_supported"
    ] is bool(controller.view_replay_backend.miller_plane_transaction_supported)
    assert status[
        "local_uia_non_collinear_direction_transaction_supported"
    ] is False
    implementation = status["local_uia_view_replay_implementation"]
    assert implementation["recipe_classes"]["transactional_miller_plane"][
        "implemented"
    ] is True
    assert implementation["recipe_classes"][
        "exact_collinear_crystal_direction"
    ]["implemented"] is True
    runtime = status["local_uia_view_replay_runtime"]
    assert runtime["backend_supported"] is bool(
        controller.view_replay_backend.supported
    )
    assert runtime["transactional_miller_supported"] is bool(
        controller.view_replay_backend.miller_plane_transaction_supported
    )
    assert runtime["non_collinear_direction_supported"] is False
    assert runtime["execution_requires_prepared_automation_ready_recipe"] is True
    assert runtime["post_action_visual_confirmation_required"] is True
    assert (
        "execute_standard_view_replay_with_local_uia" in status["capabilities"]
    ) is bool(controller.view_replay_backend.supported)
    assert (
        "execute_staged_isometric_view_replay_with_local_uia"
        in status["capabilities"]
    ) is bool(controller.view_replay_backend.supported)

    activated = controller.activate()
    assert activated["activated"] is True

    snapshot = controller.snapshot(label="main window")
    screenshot_path = Path(snapshot["screenshot_path"])
    assert screenshot_path.exists()
    assert screenshot_path.suffix == ".bmp"
    assert tmp_path in screenshot_path.parents
    assert snapshot["analysis"]["readable"] is True
    assert snapshot["analysis"]["width"] == 2

    log_path = tmp_path / "gui_actions.jsonl"
    assert log_path.exists()
    assert "snapshot" in log_path.read_text(encoding="utf-8")


def test_gui_status_requires_restore_and_activation_before_snapshot(tmp_path: Path) -> None:
    backend = MinimizedGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["status"] == "target_window_needs_activation"
    assert status["recommended_tool"] == "material_studio_gui_activate"
    assert status["recommended_action"] == "restore_and_activate_target_project_window"
    assert status["target_window_is_visible"] is True
    assert status["target_window_is_minimized"] is True
    assert status["target_window_foreground_observed"] is True
    assert status["target_window_is_foreground"] is False
    assert status["needs_activation"] is True
    assert status["ready_for_snapshot"] is False
    assert status["ready_for_next_live_edit"] is False
    assert status["activation_required_before_capture_or_input"] is True
    assert status["activation_reasons"] == ["target_window_minimized", "target_window_not_foreground"]
    assert status["window_management"]["payload_hint"] == {"reuse_existing_window_only": True, "take_snapshot": True}
    assert "target_window_minimized" in status["window_management"]["warnings"]

    with pytest.raises(
        GuiSnapshotBlockedError,
        match="before the verified target is restored and foreground",
    ) as exc_info:
        controller.snapshot(label="blocked")

    assert isinstance(exc_info.value, GuiError)
    assert exc_info.value.receipt["captured"] is False
    assert exc_info.value.receipt["capture_started"] is False
    assert exc_info.value.receipt["working_dir"] == str(tmp_path.resolve())
    assert exc_info.value.receipt["label"] == "blocked"
    assert exc_info.value.receipt["block_reason"] == "target_window_activation_required"
    assert exc_info.value.receipt["activation_reasons"] == [
        "target_window_minimized",
        "target_window_not_foreground",
    ]
    assert backend.captured_handles == []
    assert "snapshot_blocked" in (tmp_path / "gui_actions.jsonl").read_text(encoding="utf-8")

    activated = controller.activate()
    assert activated["activated"] is True
    assert activated["activation_verified"] is True
    assert activated["window_identity_stable_after_activation"] is True
    assert activated["window"]["is_minimized"] is False
    assert activated["window"]["is_foreground"] is True
    assert activated["window_management"]["status"] == "ready_for_same_window_live_edit"

    snapshot = controller.snapshot(label="restored")
    assert Path(snapshot["screenshot_path"]).exists()
    assert backend.captured_handles == [100]


def test_gui_status_infers_win32_minimized_sentinel_when_is_iconic_is_unknown(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    backend.window = WindowInfo(
        handle=100,
        title="minimized - Materials Studio",
        pid=1234,
        rect=(-32000, -32000, -31840, -31972),
    )
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["windows"][0]["is_minimized"] is True
    assert status["windows"][0]["minimized_state_source"] == "window_rect_sentinel"
    assert status["recommended_tool"] == "material_studio_gui_activate"
    assert status["activation_required_before_capture_or_input"] is True


def test_gui_open_structure_refuses_input_when_activation_is_not_verified(tmp_path: Path) -> None:
    backend = MinimizedGuiBackend(restore_on_activate=False)
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiError, match="still minimized or is not the verified foreground"):
        controller.open_structure(structure, project_id="gui_proj", revision=3, take_snapshot=False)

    assert backend.opened == []
    assert backend.activated_handles == [100]


def test_bmp_snapshot_analysis_detects_blank_model_viewport(tmp_path: Path) -> None:
    screenshot = tmp_path / "blank_viewport.bmp"
    screenshot.write_bytes(_materials_studio_like_bmp(model_visible=False))

    analysis = _analyze_bmp_snapshot(screenshot)

    assert analysis["readable"] is True
    assert analysis["likely_nonblank"] is True
    assert analysis["viewport_analysis_available"] is True
    assert analysis["viewport_likely_visible_model"] is False
    assert analysis["viewport_foreground_ratio"] == 0.0
    assert analysis["viewport_uniform_surface"] is True
    assert analysis["viewport_dark_uniform_surface"] is True
    assert analysis["viewport_capture_limitation_possible"] is True
    assert analysis["viewport_capture_diagnostic"] == "uniform_dark_viewport_surface"
    assert any("viewport appears blank" in warning for warning in analysis["warnings"])
    assert any("OpenGL 3D viewport" in warning for warning in analysis["warnings"])


def test_bmp_snapshot_analysis_detects_visible_model_pixels(tmp_path: Path) -> None:
    screenshot = tmp_path / "visible_model.bmp"
    screenshot.write_bytes(_materials_studio_like_bmp(model_visible=True))

    analysis = _analyze_bmp_snapshot(screenshot)

    assert analysis["readable"] is True
    assert analysis["likely_nonblank"] is True
    assert analysis["viewport_analysis_available"] is True
    assert analysis["viewport_likely_visible_model"] is True
    assert analysis["viewport_foreground_ratio"] > 0.003
    assert analysis["viewport_capture_limitation_possible"] is False
    assert analysis["viewport_capture_diagnostic"] == "visible_model_pixels"
    assert not any("viewport appears blank" in warning for warning in analysis["warnings"])


def test_bmp_snapshot_analysis_detects_sparse_visible_model_pixels(tmp_path: Path) -> None:
    screenshot = tmp_path / "sparse_visible_model.bmp"
    screenshot.write_bytes(_materials_studio_like_bmp(model_visible=True, sparse=True))

    analysis = _analyze_bmp_snapshot(screenshot)

    assert analysis["readable"] is True
    assert analysis["viewport_likely_visible_model"] is True
    assert 0.0015 <= analysis["viewport_foreground_ratio"] < 0.003
    assert analysis["viewport_colored_pixel_ratio"] >= 0.0005
    assert not any("viewport appears blank" in warning for warning in analysis["warnings"])


def test_gui_status_reports_resolved_matstudio_exe(monkeypatch, tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    matstudio = tmp_path / "MatStudio.exe"
    matstudio.write_text("fake exe", encoding="utf-8")
    monkeypatch.setenv("MATERIAL_STUDIO_GUI", str(matstudio))
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["matstudio_exe"] == str(matstudio.resolve())
    assert status["open_strategy"] == "MatStudio.exe"
    assert status["can_launch_matstudio"] is True
    assert status["can_launch_blank_session"] is True
    assert "launch_matstudio_session" in status["capabilities"]


def test_window_priority_prefers_foreground_materials_studio_window() -> None:
    old_window = WindowInfo(
        handle=100,
        title="old_project - Materials Studio",
        pid=1234,
        rect=(0, 0, 1200, 800),
        class_name="Afx:old",
    )
    current_window = WindowInfo(
        handle=200,
        title="current_project - Materials Studio",
        pid=5678,
        rect=(0, 0, 900, 700),
        class_name="Afx:current",
    )

    selected = sorted(
        [old_window, current_window],
        key=lambda window: _window_priority(window, foreground_handle=200),
    )[0]

    assert selected is current_window


def test_window_priority_prefers_project_frame_over_foreground_dialog() -> None:
    project_window = WindowInfo(
        handle=100,
        title="msmcp_r000_abc123 - Materials Studio",
        pid=1234,
        rect=(0, 0, 1200, 800),
        class_name="Afx:project",
    )
    dialog = WindowInfo(
        handle=200,
        title="Open Project",
        pid=1234,
        rect=(100, 100, 700, 500),
        class_name="#32770",
    )

    selected = sorted(
        [dialog, project_window],
        key=lambda window: _window_priority(window, foreground_handle=200),
    )[0]

    assert selected is project_window


def test_gui_launch_activates_existing_window_and_can_snapshot(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    result = controller.launch(take_snapshot=True)

    assert result["launched"] is False
    assert result["activated_existing_window"] is True
    assert result["window_found"] is True
    assert result["window"]["title"] == "Untitled - Materials Studio"
    assert Path(result["snapshot"]["screenshot_path"]).exists()
    assert result["snapshot"]["analysis"]["likely_nonblank"] is True
    assert "launch" in (tmp_path / "gui_actions.jsonl").read_text(encoding="utf-8")


def test_gui_launch_starts_session_when_no_window(tmp_path: Path) -> None:
    backend = LaunchingFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    result = controller.launch(wait_seconds=1, take_snapshot=False)

    assert result["launched"] is True
    assert result["activated_existing_window"] is True
    assert result["window_found"] is True
    assert result["launch_result"]["method"] == "fake_launch"
    assert backend.launch_count == 1


def test_gui_launch_refuses_existing_process_without_window(tmp_path: Path) -> None:
    backend = ProcessOnlyFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    result = controller.launch(wait_seconds=1, take_snapshot=False)

    assert result["launched"] is False
    assert result["launch_blocked"] is True
    assert result["launch_block_reason"] == "matstudio_process_without_usable_window"
    assert result["process_count"] == 1
    assert result["window_found"] is False
    assert backend.launch_count == 0


def test_gui_launch_refuses_multiple_matstudio_windows(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    backend.windows = [
        backend.default_window,
        WindowInfo(handle=900, title="second - Materials Studio", pid=1234, rect=(0, 0, 700, 500)),
    ]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    result = controller.launch(wait_seconds=1, take_snapshot=False)

    assert result["launched"] is False
    assert result["launch_blocked"] is True
    assert result["launch_block_reason"] == "single_window_policy_violation"
    assert result["single_window_policy_ok"] is False
    assert result["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]
    assert result["window_management"]["status"] == "single_window_policy_violation"
    assert result["window_management"]["needs_single_window_resolution"] is True
    assert result["window_management"]["ready_for_next_live_edit"] is False
    assert result["window_management"]["can_hotload_without_new_window"] is False
    assert backend.activated_handles == []


def test_gui_launch_refuses_multiple_windows_even_when_project_window_matches(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(controller, structure, project_id="current_proj", revision=5)
    target_window = WindowInfo(
        handle=505,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.windows = [backend.default_window, target_window]

    result = controller.launch(project_id="current_proj", revision=5, wait_seconds=1, take_snapshot=False)

    assert result["launched"] is False
    assert result["activated_existing_window"] is False
    assert result["launch_blocked"] is True
    assert result["launch_block_reason"] == "single_window_policy_violation"
    assert result["single_window_policy_ok"] is False
    assert result["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]
    assert result["target_window_resolution"]["matched_project_window"] is True
    assert result["target_window_resolution"]["target_handle"] == 505
    assert backend.activated_handles == []


def test_gui_status_marks_requested_revision_not_loaded_for_fallback_window(tmp_path: Path) -> None:
    backend = SameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status(project_id="missing_project", revision=7)

    management = status["window_management"]
    assert management["status"] == "requested_revision_not_loaded"
    assert management["matched_project_window"] is False
    assert management["fallback_used"] is True
    assert management["needs_reload"] is True
    assert management["ready_for_next_live_edit"] is False
    assert management["can_apply_current_revision_without_new_window"] is True
    assert management["recommended_tool"] == "material_studio_gui_open_structure"
    assert management["recommended_action"] == "reload_requested_project_revision_in_gui"
    assert management["payload_hint"] == {
        "project_id": "missing_project",
        "revision": 7,
        "reuse_existing_window_only": True,
        "execution_mode": "execute",
        "open_in_gui": True,
    }


def test_gui_open_structure_requires_existing_window_when_window_missing(tmp_path: Path) -> None:
    backend = LaunchingFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiError) as exc:
        controller.open_structure(structure, project_id="gui_proj", revision=3, take_snapshot=True)

    assert backend.launch_count == 0
    assert backend.opened == []
    assert "Refusing to launch a new MatStudio.exe" in str(exc.value)


def test_gui_open_structure_refuses_multiple_matstudio_windows(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    backend.windows = [
        backend.default_window,
        WindowInfo(handle=202, title="Other - Materials Studio", pid=1234, rect=(20, 20, 900, 700)),
    ]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiError) as exc:
        controller.open_structure(structure, project_id="gui_proj", revision=3, take_snapshot=True)

    assert backend.opened == []
    assert backend.activated_handles == []
    assert "single-window policy" in str(exc.value)
    assert "multiple_matstudio_windows_detected" in str(exc.value)


def test_gui_status_allows_file_association_dialog_without_counting_extra_window(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    backend.windows = [
        backend.default_window,
        WindowInfo(
            handle=203,
            title="Materials Studio File Associations",
            pid=1234,
            rect=(20, 20, 500, 300),
            class_name="#32770",
        ),
    ]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["window_count"] == 2
    assert status["status"] == "ready_for_same_window_live_edit"
    assert status["recommended_tool"] == "material_studio_gui_snapshot"
    assert status["ready_for_next_live_edit"] is True
    assert status["single_window_policy_ok"] is True
    assert status["single_window_violation_reasons"] == []
    assert status["can_open_structure_in_existing_window"] is True
    management = status["window_management"]
    assert management["window_count"] == 2
    assert management["primary_window_count"] == 1
    assert management["dialog_window_count"] == 1
    assert management["blocking_dialog_count"] == 0
    assert management["file_association_dialog_count"] == 1
    assert management["single_window_policy_ok"] is True
    assert management["single_window_violation_reasons"] == []
    assert "file_association_dialog_detected" in management["warnings"]
    assert "multiple_matstudio_windows_detected" not in management["warnings"]


def test_gui_status_allows_welcome_dialog_without_counting_extra_window(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    backend.windows = [
        backend.default_window,
        WindowInfo(
            handle=204,
            title="Welcome to Materials Studio",
            pid=1234,
            rect=(20, 20, 500, 300),
            class_name="#32770",
        ),
    ]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["window_count"] == 2
    assert status["status"] == "modal_dialog_blocking_hotload"
    assert status["recommended_tool"] == "material_studio_gui_activate"
    assert status["recommended_action"] == "dismiss_startup_or_modal_dialog_then_retry_hotload"
    assert status["ready_for_next_live_edit"] is False
    assert status["needs_dialog_resolution"] is True
    assert status["single_window_policy_ok"] is True
    assert status["single_window_violation_reasons"] == []
    assert status["can_open_structure_in_existing_window"] is False
    management = status["window_management"]
    assert management["primary_window_count"] == 1
    assert management["dialog_window_count"] == 1
    assert management["blocking_dialog_count"] == 1
    assert management["welcome_dialog_count"] == 1
    assert management["startup_dialog_count"] == 1
    assert management["single_window_policy_ok"] is True
    assert management["single_window_violation_reasons"] == []
    assert management["status"] == "modal_dialog_blocking_hotload"
    assert management["needs_dialog_resolution"] is True
    assert management["ready_for_next_live_edit"] is False
    assert management["can_hotload_without_new_window"] is False
    assert management["ready_for_same_window_open"] is False
    assert management["ready_for_open"] is False
    assert management["recommended_tool"] == "material_studio_gui_activate"
    assert management["recommended_action"] == "dismiss_startup_or_modal_dialog_then_retry_hotload"
    assert "welcome_dialog_detected" in management["warnings"]
    assert "multiple_matstudio_windows_detected" not in management["warnings"]


def test_gui_open_structure_refuses_welcome_dialog_without_counting_extra_window(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    backend.windows = [
        backend.default_window,
        WindowInfo(
            handle=204,
            title="Welcome to Materials Studio",
            pid=1234,
            rect=(20, 20, 500, 300),
            class_name="#32770",
        ),
    ]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiError) as exc:
        controller.open_structure(structure, project_id="gui_proj", revision=3, take_snapshot=True)

    assert backend.opened == []
    assert "startup or modal dialog is open" in str(exc.value)
    assert "multiple_matstudio_windows_detected" not in str(exc.value)


def test_windows_gui_status_marks_known_startup_dialogs_ready_for_same_window_open(tmp_path: Path) -> None:
    backend = StartupDialogWindowsFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["status"] == "startup_dialog_ready_for_same_window_open"
    assert status["recommended_tool"] == "material_studio_gui_open_structure"
    assert status["recommended_action"] == "open_current_structure_through_startup_dialog"
    assert status["can_open_structure_in_existing_window"] is True
    management = status["window_management"]
    assert management["blocking_dialog_count"] == 2
    assert management["unresolved_blocking_dialog_count"] == 0
    assert management["resolvable_startup_dialog_count"] == 2
    assert management["startup_dialog_open_supported"] is True
    assert management["startup_dialog_open_ready"] is True
    assert management["needs_dialog_resolution"] is False


def test_windows_gui_open_structure_resolves_known_startup_dialogs_in_same_window(tmp_path: Path) -> None:
    backend = StartupDialogWindowsFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    structure = _bind_structure_to_revision(
        controller,
        structure,
        project_id="gui_proj",
        revision=3,
    )

    opened = controller.open_structure(
        structure,
        project_id="gui_proj",
        revision=3,
        take_snapshot=False,
    )

    assert opened["same_window_open_used"] is True
    assert opened["single_window_policy_ok"] is True
    assert len(backend.list_processes()) == 1
    assert len(backend.same_window_opened) == 1
    assert backend.same_window_opened[0][0] == backend.window.handle
    assert backend.same_window_opened[0][1].suffix == ".stp"
    assert backend.dialogs == []


def test_windows_backend_uses_welcome_dialog_without_ctrl_open(monkeypatch, tmp_path: Path) -> None:
    backend = WindowsGuiBackend()
    backend.supported = True
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    window = WindowInfo(handle=111, title="Materials Studio", pid=2233, rect=(0, 0, 900, 700))
    welcome = WindowInfo(
        handle=222,
        title="Welcome to Materials Studio",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    opened_paths: list[str] = []

    monkeypatch.setattr(backend, "list_processes", lambda: [ProcessInfo(name="MatStudio.exe", pid=2233)])
    monkeypatch.setattr(backend, "dismiss_startup_dialogs", lambda **kwargs: [])
    monkeypatch.setattr(backend, "activate_window", lambda selected: selected.handle == window.handle)
    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: [welcome])
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [window.handle])
    monkeypatch.setattr(
        gui_module,
        "_open_project_from_welcome_dialog",
        lambda handle, path, **kwargs: opened_paths.append(path) or {"ok": True, "verified_path": path},
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui_module, "_resolve_same_window_open_prompts", lambda **kwargs: [])
    monkeypatch.setattr(
        gui_module,
        "_send_ctrl_open_shortcut",
        lambda: (_ for _ in ()).throw(AssertionError("Ctrl+O must not be used when welcome open is available")),
    )

    result = backend.open_file_in_existing_window(window, project)

    assert result["method"] == "existing_window_welcome_dialog"
    assert result["same_window_open_requested"] is True
    assert result["spawned_process_ids"] == []
    assert opened_paths == [str(project)]


def test_windows_backend_uses_native_process_fallback_when_tasklist_is_denied(monkeypatch) -> None:
    backend = WindowsGuiBackend()
    backend.supported = True
    monkeypatch.setattr(
        gui_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="ERROR: Access denied"
        ),
    )
    monkeypatch.setattr(
        gui_module,
        "_native_matstudio_processes",
        lambda: [ProcessInfo(name="MatStudio.exe", pid=42448)],
    )

    assert [item.to_dict() for item in backend.list_processes()] == [
        {"name": "MatStudio.exe", "pid": 42448, "title": None, "path": None}
    ]


def test_gui_status_distinguishes_existing_process_without_usable_window(tmp_path: Path) -> None:
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=ProcessOnlyFakeGuiBackend(),
    )

    status = controller.status()

    assert status["process_found"] is True
    assert status["process_count"] == 1
    assert status["window_found"] is False
    assert status["status"] == "matstudio_process_without_usable_window"
    assert status["recommended_tool"] == "material_studio_gui_status"
    assert status["recommended_action"] == "resolve_existing_matstudio_process_without_usable_window"
    assert "matstudio_process_without_usable_window" in status["window_management"]["warnings"]
    assert status["can_launch_blank_session"] is True


def test_welcome_dialog_uses_browse_picker_instead_of_direct_path_write(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    clicks: list[int] = []
    posted_clicks: list[int] = []
    submission_calls: list[dict] = []
    path_binding = {
        "ok": True,
        "expected_path": str(project),
        "verification_source": "filename_exact_match_after_refill",
        "filename_field": {"ok": True, "method": "verified_test_setter"},
    }
    picker_submission = {
        "ok": True,
        "submitted_dialog_handle": 500,
        "dialog_handle_recreated": False,
        "dialogs_absent": True,
        "expected_path_verified": True,
        "expected_path_observed": True,
        "path_binding": path_binding,
    }
    expected_window = WindowInfo(
        handle=600,
        title=f"{project.stem} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    project_window_polls = iter([None, expected_window])

    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda handle: [
            {"handle": 10, "class": "Button", "text": "&Open an existing project:"},
            {"handle": 20, "class": "Button", "text": "&Browse..."},
            {"handle": 30, "class": "Edit", "text": str(project)},
            {"handle": 40, "class": "Button", "text": "OK"},
        ],
    )
    monkeypatch.setattr(gui_module, "_click_button_handle", clicks.append)
    monkeypatch.setattr(
        gui_module,
        "_post_button_click_handle",
        lambda handle: posted_clicks.append(handle) or {"posted": True},
    )
    monkeypatch.setattr(gui_module, "_find_file_open_dialog", lambda **kwargs: picker)
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: submission_calls.append(kwargs) or picker_submission,
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui_module, "_window_handle_exists", lambda handle: True)
    monkeypatch.setattr(gui_module, "_window_text", lambda handle: str(project))
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [222])
    monkeypatch.setattr(
        gui_module,
        "_wait_for_project_window",
        lambda **kwargs: next(project_window_polls),
    )

    result = gui_module._open_project_from_welcome_dialog(222, str(project), pid=2233)

    assert clicks == [10]
    assert posted_clicks == [20, 40]
    assert submission_calls == [
        {
            "pid": 2233,
            "owner_root_handle": 222,
            "initial_dialog": picker,
            "expected_path": str(project),
        }
    ]
    assert result["verified_path"] == str(project)
    assert result["path_verification"] == "welcome_edit_exact_match"
    assert result["welcome_auto_submitted"] is False
    assert result["browse_submission"] == {"posted": True}
    assert result["welcome_submission"] == {"posted": True}
    assert result["expected_project_window"]["handle"] == 600
    assert result["browse_dialog"]["handle"] == 500
    assert result["browse_dialog_owner_chain"] == [222]
    assert result["picker_submission"] == picker_submission
    assert result["dialog_protocol_schema_version"] == 2
    assert result["filename_field"] == path_binding["filename_field"]
    assert result["path_binding"] == path_binding
    assert result["filename_submission_attempts"] == [
        {
            "attempt": 1,
            "filename_field": path_binding["filename_field"],
            "path_binding": path_binding,
            "picker_submission": picker_submission,
            "picker_closed": True,
        }
    ]


def test_welcome_dialog_records_recreated_file_picker_submission(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    clicks: list[int] = []
    posted_clicks: list[int] = []
    path_binding = {
        "ok": True,
        "expected_path": str(project),
        "verification_source": "filename_exact_match_after_refill",
        "filename_field": {"ok": True, "method": "verified_test_setter"},
    }
    picker_submission = {
        "ok": True,
        "submitted_dialog_handle": 501,
        "dialog_handle_recreated": True,
        "dialogs_absent": True,
        "expected_path_verified": True,
        "expected_path_observed": True,
        "path_binding": path_binding,
        "attempts": [
            {"attempt": 1, "status": "stale_dialog_handle"},
            {"attempt": 2, "status": "submitted", "dialogs_absent": True},
        ],
    }
    expected_window = WindowInfo(
        handle=600,
        title=f"{project.stem} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    project_window_polls = iter([None, expected_window])

    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda handle: [
            {"handle": 10, "class": "Button", "text": "Open an existing project"},
            {"handle": 20, "class": "Button", "text": "Browse..."},
            {"handle": 30, "class": "Edit", "text": str(project)},
            {"handle": 40, "class": "Button", "text": "OK"},
        ],
    )
    monkeypatch.setattr(gui_module, "_click_button_handle", clicks.append)
    monkeypatch.setattr(
        gui_module,
        "_post_button_click_handle",
        lambda handle: posted_clicks.append(handle) or {"posted": True},
    )
    monkeypatch.setattr(gui_module, "_find_file_open_dialog", lambda **kwargs: picker)
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: picker_submission,
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui_module, "_window_handle_exists", lambda handle: True)
    monkeypatch.setattr(gui_module, "_window_text", lambda handle: str(project))
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [222])
    monkeypatch.setattr(
        gui_module,
        "_wait_for_project_window",
        lambda **kwargs: next(project_window_polls),
    )

    result = gui_module._open_project_from_welcome_dialog(222, str(project), pid=2233)

    assert clicks == [10]
    assert posted_clicks == [20, 40]
    assert result["picker_submission"]["dialog_handle_recreated"] is True
    assert result["picker_submission"]["attempts"][0]["status"] == "stale_dialog_handle"
    assert result["filename_submission_attempts"][0]["picker_closed"] is True


def test_welcome_dialog_accepts_picker_auto_submit_with_exact_project_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    expected_window = WindowInfo(
        handle=600,
        title=f"{project.stem} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    synchronous_clicks: list[int] = []
    posted_clicks: list[int] = []
    picker_lookups: list[dict] = []

    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda handle: [
            {"handle": 10, "class": "Button", "text": "&Open an existing project:"},
            {"handle": 20, "class": "Button", "text": "&Browse..."},
            {"handle": 30, "class": "Edit", "text": ""},
            {"handle": 40, "class": "Button", "text": "OK"},
        ],
    )
    monkeypatch.setattr(gui_module, "_click_button_handle", synchronous_clicks.append)
    monkeypatch.setattr(
        gui_module,
        "_post_button_click_handle",
        lambda handle: posted_clicks.append(handle) or {"posted": True, "target_handle": handle},
    )
    monkeypatch.setattr(
        gui_module,
        "_find_file_open_dialog",
        lambda **kwargs: picker_lookups.append(kwargs) or picker,
    )
    monkeypatch.setattr(
        gui_module,
        "_set_common_dialog_filename",
        lambda handle, path: {"ok": True, "path": path},
    )
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: {
            "ok": True,
            "submitted_dialog_handle": 500,
            "dialog_handle_recreated": False,
            "dialogs_absent": True,
            "expected_path_verified": True,
            "expected_path_observed": True,
            "path_binding": {
                "ok": True,
                "expected_path": str(project),
            },
        },
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui_module, "_window_handle_exists", lambda handle: False)
    monkeypatch.setattr(
        gui_module,
        "_window_text",
        lambda handle: (_ for _ in ()).throw(AssertionError("destroyed welcome edit must not be read")),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [222])
    monkeypatch.setattr(gui_module, "_wait_for_project_window", lambda **kwargs: expected_window)

    result = gui_module._open_project_from_welcome_dialog(222, str(project), pid=2233)

    assert synchronous_clicks == [10]
    assert posted_clicks == [20]
    assert picker_lookups == [
        {"pid": 2233, "timeout_seconds": 10.0, "owner_root_handle": 222}
    ]
    assert result["welcome_auto_submitted"] is True
    assert result["welcome_submission"] is None
    assert result["verified_path"] == str(project)
    assert result["path_verification"] == "picker_filename_exact_match_plus_exact_project_window"
    assert result["expected_project_window"]["handle"] == 600


def test_welcome_auto_submit_does_not_synthesize_verified_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    expected_window = WindowInfo(
        handle=600,
        title=f"{project.stem} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda _handle: [
            {"handle": 10, "class": "Button", "text": "Open an existing project"},
            {"handle": 20, "class": "Button", "text": "Browse..."},
            {"handle": 30, "class": "Edit", "text": ""},
            {"handle": 40, "class": "Button", "text": "OK"},
        ],
    )
    monkeypatch.setattr(gui_module, "_click_button_handle", lambda _handle: None)
    monkeypatch.setattr(
        gui_module,
        "_post_button_click_handle",
        lambda handle: {"posted": True, "target_handle": handle},
    )
    monkeypatch.setattr(gui_module, "_find_file_open_dialog", lambda **kwargs: picker)
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: {
            "ok": True,
            "submitted_dialog_handle": picker.handle,
            "dialog_handle_recreated": False,
            "dialogs_absent": True,
            "expected_path_verified": False,
            "expected_path_observed": False,
            "path_binding": {"ok": False},
        },
    )
    monkeypatch.setattr(gui_module, "_window_handle_exists", lambda _handle: False)
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda _handle: [222])
    monkeypatch.setattr(
        gui_module,
        "_wait_for_project_window",
        lambda **kwargs: expected_window,
    )

    result = gui_module._open_project_from_welcome_dialog(
        222,
        str(project),
        pid=2233,
    )

    assert result["requested_path"] == str(project)
    assert result["verified_path"] is None
    assert result["path_verification"] == "exact_project_window_only"


def test_find_file_open_dialog_requires_expected_owner_chain(monkeypatch) -> None:
    wrong = WindowInfo(
        handle=501,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    expected = WindowInfo(
        handle=502,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: [wrong, expected])
    monkeypatch.setattr(gui_module, "_looks_like_file_open_dialog", lambda window: True)
    monkeypatch.setattr(
        gui_module,
        "_window_owner_chain",
        lambda handle: [999] if handle == wrong.handle else [222, 111],
    )

    resolved = gui_module._find_file_open_dialog(
        pid=2233,
        timeout_seconds=0.1,
        owner_root_handle=111,
    )

    assert resolved == expected


def test_wait_for_project_window_requires_raw_exact_title(monkeypatch) -> None:
    normalized_collision = WindowInfo(
        handle=600,
        title="model_name - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    exact = WindowInfo(
        handle=601,
        title="model name - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    polls = iter([[normalized_collision], [exact]])
    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: next(polls))
    monkeypatch.setattr(gui_module.time, "sleep", lambda _seconds: None)

    result = gui_module._wait_for_project_window(
        pid=2233,
        expected_project_name="model name",
        timeout_seconds=1.0,
    )

    assert result == exact


def test_picker_absence_requires_stable_quiet_period(monkeypatch) -> None:
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    states = iter([[picker], [], [picker], [], [], [], [], []])
    calls = 0

    def owned_dialogs(**_kwargs) -> list[WindowInfo]:
        nonlocal calls
        calls += 1
        return next(states, [])

    monkeypatch.setattr(gui_module, "_owned_file_open_dialogs", owned_dialogs)

    result = gui_module._wait_for_owned_file_open_dialogs_absent(
        pid=2233,
        owner_root_handle=222,
        timeout_seconds=0.3,
        quiet_period_seconds=0.03,
        poll_interval_seconds=0.01,
    )

    assert result is True
    assert calls >= 4


def test_submit_current_file_open_dialog_rebinds_after_handle_recreation(monkeypatch) -> None:
    initial = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    replacement = WindowInfo(
        handle=501,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    current_dialogs = iter([initial, replacement])

    monkeypatch.setattr(
        gui_module,
        "_find_file_open_dialog",
        lambda **kwargs: next(current_dialogs),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [222])
    monkeypatch.setattr(
        gui_module,
        "_bind_expected_file_open_path",
        lambda handle, **kwargs: {
            "ok": True,
            "expected_path": kwargs["expected_path"],
            "dialog_handle": handle,
        },
    )

    def submit(handle: int) -> dict:
        if handle == initial.handle:
            raise GuiError("stale handle")
        return {"posted": True, "target_handle": handle}

    monkeypatch.setattr(gui_module, "_click_dialog_ok", submit)
    monkeypatch.setattr(
        gui_module,
        "_wait_for_owned_file_open_dialogs_absent",
        lambda **kwargs: True,
    )

    result = gui_module._submit_current_file_open_dialog(
        pid=2233,
        owner_root_handle=222,
        initial_dialog=initial,
        expected_path=r"C:\workspace\target.stp",
    )

    assert result["ok"] is True
    assert result["dialog_handle_recreated"] is True
    assert result["initial_dialog_handle"] == 500
    assert result["submitted_dialog_handle"] == 501
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "stale_dialog_handle",
        "submitted",
    ]


def test_recreated_picker_refills_and_reverifies_exact_target_path(monkeypatch) -> None:
    expected_path = r"C:\workspace\gui_projects\msmcp_r003_target\msmcp_r003_target.stp"
    initial = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    replacement = WindowInfo(
        handle=501,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    current_dialogs = iter([initial, replacement])
    replacement_refilled = False
    refill_calls: list[tuple[int, str]] = []

    monkeypatch.setattr(
        gui_module,
        "_find_file_open_dialog",
        lambda **kwargs: next(current_dialogs),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [222])

    def visible_path(handle: int, *, expected: str | None = None) -> str:
        if handle == initial.handle:
            return expected_path
        return expected_path if replacement_refilled else r"C:\stale\old.stp"

    def refill(handle: int, path: str) -> dict:
        nonlocal replacement_refilled
        refill_calls.append((handle, path))
        replacement_refilled = True
        return {"ok": True, "method": "verified_test_setter"}

    def submit(handle: int) -> dict:
        if handle == initial.handle:
            raise GuiError("stale handle")
        return {"posted": True, "target_handle": handle}

    monkeypatch.setattr(gui_module, "_visible_filename_edit_text", visible_path)
    monkeypatch.setattr(gui_module, "_set_common_dialog_filename", refill)
    monkeypatch.setattr(gui_module, "_click_dialog_ok", submit)
    monkeypatch.setattr(
        gui_module,
        "_wait_for_owned_file_open_dialogs_absent",
        lambda **kwargs: True,
    )

    result = gui_module._submit_current_file_open_dialog(
        pid=2233,
        owner_root_handle=222,
        initial_dialog=initial,
        expected_path=expected_path,
    )

    assert refill_calls == [(replacement.handle, expected_path)]
    assert result["expected_path_verified"] is True
    assert result["submitted_dialog_handle"] == replacement.handle
    assert result["path_binding"]["path_refilled"] is True
    assert (
        result["path_binding"]["verification_source"]
        == "filename_exact_match_after_refill"
    )


def test_save_as_is_never_classified_as_file_open(monkeypatch) -> None:
    save_as = WindowInfo(
        handle=400,
        title="Save As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    monkeypatch.setattr(
        gui_module,
        "_dialog_has_file_path_controls",
        lambda _handle: True,
    )

    assert gui_module._looks_like_file_open_dialog(save_as) is False


def test_save_project_as_variant_is_not_file_open(monkeypatch) -> None:
    save_as = WindowInfo(
        handle=400,
        title="Save Project As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    monkeypatch.setattr(
        gui_module,
        "_dialog_has_file_path_controls",
        lambda _handle: True,
    )

    assert gui_module._looks_like_file_open_dialog(save_as) is False
    assert gui_module._looks_like_non_open_file_dialog(save_as) is True


@pytest.mark.parametrize("title", ["Upload File", "Download Project"])
def test_upload_download_titles_are_not_file_open(
    monkeypatch,
    title: str,
) -> None:
    dialog = WindowInfo(
        handle=400,
        title=title,
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    monkeypatch.setattr(
        gui_module,
        "_dialog_has_file_path_controls",
        lambda _handle: True,
    )

    assert gui_module._looks_like_file_open_dialog(dialog) is False
    assert gui_module._looks_like_non_open_file_dialog(dialog) is True


def test_post_open_save_as_is_cancelled_before_generic_picker_handling(
    monkeypatch,
) -> None:
    source = WindowInfo(
        handle=111,
        title="msmcp_r002_aaaaaaaaaa - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    save_as = WindowInfo(
        handle=400,
        title="Save As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    cancelled: list[int] = []
    picker_submissions: list[dict] = []
    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: [save_as])
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda _handle: [111])
    monkeypatch.setattr(gui_module, "_dialog_controls", lambda _handle: [])
    monkeypatch.setattr(
        gui_module,
        "_looks_like_non_open_file_dialog",
        lambda _window: True,
    )
    monkeypatch.setattr(
        gui_module,
        "_cancel_dialog",
        lambda handle, **kwargs: cancelled.append(handle)
        or {"command": "IDCANCEL", "closed": True},
    )
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: picker_submissions.append(kwargs),
    )

    with pytest.raises(GuiError, match="without positive File/Open semantics"):
        gui_module._resolve_same_window_open_prompts(
            pid=2233,
            source_window=source,
            path_text=r"C:\workspace\target.stp",
            source_wrapper_provenance=_verified_source_wrapper_provenance(),
            timeout_seconds=1.0,
        )

    assert cancelled == [save_as.handle]
    assert picker_submissions == []


def test_pre_open_save_as_is_cancelled_before_prompt_classification(
    monkeypatch,
) -> None:
    source = WindowInfo(
        handle=111,
        title="msmcp_r002_aaaaaaaaaa - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    save_as = WindowInfo(
        handle=400,
        title="Save As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    cancelled: list[int] = []
    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: [save_as])
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda _handle: [111])
    monkeypatch.setattr(
        gui_module,
        "_cancel_dialog",
        lambda handle, **kwargs: cancelled.append(handle)
        or {"command": "IDCANCEL", "closed": True},
    )

    monkeypatch.setattr(
        gui_module,
        "_looks_like_non_open_file_dialog",
        lambda _window: True,
    )

    with pytest.raises(GuiError, match="without positive File/Open semantics"):
        gui_module._resolve_same_window_pre_open_prompts(
            pid=2233,
            source_window=source,
            source_wrapper_provenance=_verified_source_wrapper_provenance(),
            timeout_seconds=1.0,
        )

    assert cancelled == [save_as.handle]


def test_source_wrapper_auto_save_requires_same_workspace_metadata(
    tmp_path: Path,
) -> None:
    source_name = "msmcp_r002_aaaaaaaaaa"
    target_name = "msmcp_r003_bbbbbbbbbb"
    _write_test_wrapper(
        tmp_path,
        project_name=source_name,
        project_id="semiconductor_project",
        revision=2,
    )
    target_path = _write_test_wrapper(
        tmp_path,
        project_name=target_name,
        project_id="semiconductor_project",
        revision=3,
    )
    source = WindowInfo(
        handle=111,
        title=f"{source_name} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )

    receipt = gui_module._source_wrapper_auto_save_provenance(
        source_window=source,
        target_project_path=target_path,
        trusted_workspace_roots=(tmp_path,),
    )

    assert receipt["auto_save_allowed"] is True
    assert receipt["status"] == "verified_same_workspace_revision_wrapper"
    assert receipt["source_wrapper"]["verified"] is True
    assert receipt["target_wrapper"]["verified"] is True


def test_source_wrapper_title_prefix_without_metadata_cannot_auto_save(
    tmp_path: Path,
) -> None:
    target_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    spoofed = WindowInfo(
        handle=111,
        title="msmcp_r002_aaaaaaaaaa - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )

    receipt = gui_module._source_wrapper_auto_save_provenance(
        source_window=spoofed,
        target_project_path=target_path,
        trusted_workspace_roots=(tmp_path,),
    )

    assert receipt["auto_save_allowed"] is False
    assert "source_wrapper_provenance_unverified" in receipt["reason_codes"]


def test_source_wrapper_title_must_match_raw_generated_title(
    tmp_path: Path,
) -> None:
    source_name = "msmcp_r002_aaaaaaaaaa"
    _write_test_wrapper(
        tmp_path,
        project_name=source_name,
        project_id="semiconductor_project",
        revision=2,
    )
    target_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    source = WindowInfo(
        handle=111,
        title=f" {source_name} - Materials Studio ",
        pid=2233,
        rect=(0, 0, 900, 700),
    )

    receipt = gui_module._source_wrapper_auto_save_provenance(
        source_window=source,
        target_project_path=target_path,
        trusted_workspace_roots=(tmp_path,),
    )

    assert receipt["auto_save_allowed"] is False
    assert "source_window_title_not_raw_exact" in receipt["reason_codes"]


def test_auto_save_rejects_valid_wrapper_outside_trusted_workspace(
    tmp_path: Path,
) -> None:
    source_name = "msmcp_r002_aaaaaaaaaa"
    _write_test_wrapper(
        tmp_path,
        project_name=source_name,
        project_id="semiconductor_project",
        revision=2,
    )
    target_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    source = WindowInfo(
        handle=111,
        title=f"{source_name} - Materials Studio",
        pid=2233,
    )

    receipt = gui_module._source_wrapper_auto_save_provenance(
        source_window=source,
        target_project_path=target_path,
        trusted_workspace_roots=(tmp_path / "different_workspace",),
    )

    assert receipt["auto_save_allowed"] is False
    assert receipt["target_wrapper"]["status"] == "target_workspace_untrusted"
    assert "target_wrapper_provenance_unverified" in receipt["reason_codes"]


def test_wrapper_provenance_rejects_revision_and_project_xml_tampering(
    tmp_path: Path,
) -> None:
    project_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    metadata_path = project_path.parent / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["revision"] = 4
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    project_path.write_text("<Project/>\n", encoding="utf-8")

    receipt = gui_module._wrapper_project_path_provenance(
        project_path,
        workspace_root=tmp_path,
    )

    assert receipt["verified"] is False
    assert "project_name_revision_metadata_mismatch" in receipt["reason_codes"]
    assert "project_xml_version_invalid" in receipt["reason_codes"]
    assert "project_xml_viewer_binding_missing" in receipt["reason_codes"]
    assert "project_xml_document_url_mismatch" in receipt["reason_codes"]


def test_wrapper_provenance_accepts_attested_locked_current_project(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    real_parse = gui_module.ET.parse

    def locked_parse(path):
        if Path(path) == project_path:
            raise PermissionError(13, "project is locked by Materials Studio")
        return real_parse(path)

    monkeypatch.setattr(gui_module.ET, "parse", locked_parse)

    receipt = gui_module._wrapper_project_path_provenance(
        project_path,
        workspace_root=tmp_path,
        allow_locked_attestation=True,
    )

    assert receipt["verified"] is True
    assert receipt["project_file_locked"] is True
    assert receipt["wrapper_attestation_valid"] is True
    assert (
        receipt["project_xml_verification_status"]
        == "metadata_attested_current_project_lock"
    )


def test_locked_attestation_is_rejected_for_target_verification(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_path = _write_test_wrapper(
        tmp_path,
        project_name="msmcp_r003_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=3,
    )
    monkeypatch.setattr(
        gui_module.ET,
        "parse",
        lambda _path: (_ for _ in ()).throw(
            PermissionError(13, "project is locked")
        ),
    )

    receipt = gui_module._wrapper_project_path_provenance(
        project_path,
        workspace_root=tmp_path,
    )

    assert receipt["verified"] is False
    assert (
        "locked_project_not_allowed_for_target_verification"
        in receipt["reason_codes"]
    )


@pytest.mark.parametrize("revision", [999, 1000])
def test_wrapper_provenance_accepts_three_or_more_revision_digits(
    tmp_path: Path,
    revision: int,
) -> None:
    project_path = _write_test_wrapper(
        tmp_path,
        project_name=f"msmcp_r{revision:03d}_bbbbbbbbbb",
        project_id="semiconductor_project",
        revision=revision,
    )

    receipt = gui_module._wrapper_project_path_provenance(
        project_path,
        workspace_root=tmp_path,
    )

    assert receipt["verified"] is True


def test_controller_binds_windows_backend_write_root(tmp_path: Path) -> None:
    backend = WindowsGuiBackend()

    MaterialsStudioGuiController(tmp_path, backend=backend)

    assert backend.trusted_write_workspace_roots == (tmp_path.resolve(),)


def test_pre_open_prompt_saves_only_mcp_wrapper_before_file_picker(monkeypatch) -> None:
    source = WindowInfo(
        handle=111,
        title="msmcp_r002_example - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    save_prompt = WindowInfo(
        handle=400,
        title="Materials Studio",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    window_polls = iter([[save_prompt], [picker]])
    submissions: list[int] = []

    monkeypatch.setattr(
        gui_module,
        "_find_windows",
        lambda **kwargs: next(window_polls, []),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [111])
    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda handle: [
            {"handle": 10, "class": "Button", "text": "&Yes"},
            {"handle": 20, "class": "Button", "text": "&No"},
            {"handle": 30, "class": "Button", "text": "Cancel"},
        ],
    )
    monkeypatch.setattr(
        gui_module,
        "_looks_like_file_open_dialog",
        lambda window: window.handle == picker.handle,
    )
    monkeypatch.setattr(
        gui_module,
        "_confirm_yes_dialog",
        lambda handle: submissions.append(handle) or {"posted": True},
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)

    result = gui_module._resolve_same_window_pre_open_prompts(
        pid=2233,
        source_window=source,
        source_wrapper_provenance=_verified_source_wrapper_provenance(),
        timeout_seconds=1.0,
    )

    assert submissions == [400]
    assert result[0]["action"] == "confirm_save_current_mcp_project_before_open"
    assert result[0]["owner_chain"] == [111]
    assert result[0]["submission"] == {"posted": True}
    assert result[0]["closed"] is True


def test_post_open_prompt_rebinds_only_owned_file_picker(monkeypatch) -> None:
    source = WindowInfo(
        handle=111,
        title="msmcp_r002_example - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    unrelated = WindowInfo(
        handle=499,
        title="Open Project",
        pid=2233,
        rect=(10, 10, 300, 200),
        class_name="#32770",
    )
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    window_polls = iter([[unrelated, picker], []])
    submissions: list[dict] = []

    monkeypatch.setattr(
        gui_module,
        "_find_windows",
        lambda **kwargs: next(window_polls, []),
    )
    monkeypatch.setattr(
        gui_module,
        "_window_owner_chain",
        lambda handle: [999] if handle == unrelated.handle else [111],
    )
    monkeypatch.setattr(gui_module, "_dialog_controls", lambda handle: [])
    monkeypatch.setattr(
        gui_module,
        "_looks_like_file_open_dialog",
        lambda window: window.handle == picker.handle,
    )
    path_binding = {
        "ok": True,
        "expected_path": r"C:\workspace\msmcp_r003_target.stp",
        "filename_field": {
            "ok": True,
            "path": r"C:\workspace\msmcp_r003_target.stp",
        },
    }
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: submissions.append(kwargs)
        or {
            "ok": True,
            "dialogs_absent": True,
            "path_binding": path_binding,
        },
    )

    result = gui_module._resolve_same_window_open_prompts(
        pid=2233,
        source_window=source,
        path_text=r"C:\workspace\msmcp_r003_target.stp",
        source_wrapper_provenance=_verified_source_wrapper_provenance(),
        timeout_seconds=1.0,
    )

    assert submissions == [
        {
            "pid": 2233,
            "owner_root_handle": 111,
            "initial_dialog": picker,
            "expected_path": r"C:\workspace\msmcp_r003_target.stp",
        }
    ]
    assert result == [
        {
            "dialog_protocol_schema_version": 2,
            "action": "resubmit_open_project_dialog",
            "dialog": picker.to_dict(),
            "filename_field": path_binding["filename_field"],
            "path_binding": path_binding,
            "submission": {
                "ok": True,
                "dialogs_absent": True,
                "path_binding": path_binding,
            },
        }
    ]


def test_pre_open_prompt_refuses_save_for_non_mcp_project(monkeypatch) -> None:
    source = WindowInfo(
        handle=111,
        title="user_project - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    prompt = WindowInfo(
        handle=400,
        title="Materials Studio",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    cancelled: list[int] = []

    monkeypatch.setattr(gui_module, "_find_windows", lambda **kwargs: [prompt])
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [111])
    monkeypatch.setattr(
        gui_module,
        "_dialog_controls",
        lambda handle: [
            {"handle": 10, "class": "Button", "text": "&Yes"},
            {"handle": 20, "class": "Button", "text": "&No"},
            {"handle": 30, "class": "Button", "text": "Cancel"},
        ],
    )
    monkeypatch.setattr(gui_module, "_looks_like_file_open_dialog", lambda window: False)
    monkeypatch.setattr(
        gui_module,
        "_cancel_dialog",
        lambda handle, **kwargs: cancelled.append(handle) or {"closed": True},
    )

    with pytest.raises(GuiError, match="provenance was not verified"):
        gui_module._resolve_same_window_pre_open_prompts(
            pid=2233,
            source_window=source,
            source_wrapper_provenance=_unverified_source_wrapper_provenance(),
            timeout_seconds=1.0,
        )

    assert cancelled == [400]


def test_generic_yes_no_prompt_is_not_a_file_open_dialog(monkeypatch) -> None:
    prompt = WindowInfo(
        handle=400,
        title="Materials Studio",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    monkeypatch.setattr(gui_module, "_dialog_has_file_path_controls", lambda handle: False)

    assert gui_module._looks_like_file_open_dialog(prompt) is False


def test_cancel_dialog_posts_exact_idcancel_and_verifies_close(monkeypatch) -> None:
    commands: list[tuple[int, int]] = []
    monkeypatch.setattr(
        gui_module,
        "_post_dialog_command",
        lambda handle, command_id: commands.append((handle, command_id))
        or {"posted": True, "command_id": command_id},
    )
    monkeypatch.setattr(
        gui_module,
        "_wait_for_window_absent",
        lambda handle, **kwargs: handle == 400,
    )

    result = gui_module._cancel_dialog(400)

    assert commands == [(400, 2)]
    assert result["command"] == "IDCANCEL"
    assert result["closed"] is True


def test_cancel_dialog_drains_owned_replacement_and_requires_stable_absence(
    monkeypatch,
) -> None:
    replacement = WindowInfo(
        handle=401,
        title="Save Project As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    window_states = iter([[replacement], [], [], [], []])
    commands: list[tuple[int, int]] = []
    monkeypatch.setattr(
        gui_module,
        "_post_dialog_command",
        lambda handle, command_id: commands.append((handle, command_id))
        or {"posted": True, "command_id": command_id},
    )
    monkeypatch.setattr(
        gui_module,
        "_wait_for_window_absent",
        lambda _handle, **kwargs: True,
    )
    monkeypatch.setattr(
        gui_module,
        "_find_windows",
        lambda **kwargs: next(window_states, []),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda _handle: [111])

    result = gui_module._cancel_dialog(
        400,
        pid=2233,
        owner_root_handle=111,
        dialog_title="Save Project As",
        timeout_seconds=0.3,
        quiet_period_seconds=0.02,
    )

    assert commands == [(400, 2), (401, 2)]
    assert result["replacement_cancel_count"] == 1
    assert result["family_stable_absent"] is True


def test_post_open_prompt_does_not_miss_delayed_save_dialog(monkeypatch) -> None:
    source = WindowInfo(
        handle=111,
        title="msmcp_r002_aaaaaaaaaa - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    delayed = WindowInfo(
        handle=400,
        title="Save Project As",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    window_states = iter([[], [delayed]])
    cancelled: list[int] = []
    monkeypatch.setattr(
        gui_module,
        "_find_windows",
        lambda **kwargs: next(window_states, [delayed]),
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda _handle: [111])
    monkeypatch.setattr(
        gui_module,
        "_dialog_has_file_path_controls",
        lambda _handle: True,
    )
    monkeypatch.setattr(gui_module, "_dialog_controls", lambda _handle: [])
    monkeypatch.setattr(
        gui_module,
        "_cancel_dialog",
        lambda handle, **kwargs: cancelled.append(handle)
        or {"closed": True, "family_stable_absent": True},
    )

    with pytest.raises(GuiError, match="without positive File/Open semantics"):
        gui_module._resolve_same_window_open_prompts(
            pid=2233,
            source_window=source,
            path_text=r"C:\workspace\target.stp",
            source_wrapper_provenance=_verified_source_wrapper_provenance(),
            timeout_seconds=1.0,
        )

    assert cancelled == [delayed.handle]


def test_windows_backend_submits_existing_window_open_asynchronously(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = WindowsGuiBackend()
    backend.supported = True
    project = tmp_path / "wrapped.stp"
    project.write_text("<Project/>\n", encoding="utf-8")
    window = WindowInfo(
        handle=111,
        title="Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    picker = WindowInfo(
        handle=500,
        title="Open Project",
        pid=2233,
        rect=(20, 20, 700, 500),
        class_name="#32770",
    )
    expected_window = WindowInfo(
        handle=111,
        title=f"{project.stem} - Materials Studio",
        pid=2233,
        rect=(0, 0, 900, 700),
    )
    lookup_payloads: list[dict] = []
    submission_calls: list[dict] = []

    monkeypatch.setattr(backend, "list_processes", lambda: [ProcessInfo(name="MatStudio.exe", pid=2233)])
    monkeypatch.setattr(backend, "dismiss_startup_dialogs", lambda **kwargs: [])
    monkeypatch.setattr(backend, "activate_window", lambda selected: selected == window)
    monkeypatch.setattr(gui_module, "_open_project_from_startup_dialogs", lambda **kwargs: None)
    monkeypatch.setattr(gui_module, "_send_ctrl_open_shortcut", lambda: None)
    monkeypatch.setattr(
        gui_module,
        "_resolve_same_window_pre_open_prompts",
        lambda **kwargs: [{"action": "test_pre_open"}],
    )
    monkeypatch.setattr(
        gui_module,
        "_find_file_open_dialog",
        lambda **kwargs: lookup_payloads.append(kwargs) or picker,
    )
    monkeypatch.setattr(gui_module, "_window_owner_chain", lambda handle: [111])
    monkeypatch.setattr(
        gui_module,
        "_set_common_dialog_filename",
        lambda handle, path: {"ok": True, "path": path},
    )
    monkeypatch.setattr(
        gui_module,
        "_submit_current_file_open_dialog",
        lambda **kwargs: submission_calls.append(kwargs)
        or {
            "ok": True,
            "submitted_dialog_handle": 500,
            "dialog_handle_recreated": False,
            "dialogs_absent": True,
            "path_binding": {
                "ok": True,
                "expected_path": str(project),
                "filename_field": {
                    "ok": True,
                    "method": "verified_test_setter",
                },
            },
        },
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui_module, "_resolve_same_window_open_prompts", lambda **kwargs: [])
    monkeypatch.setattr(gui_module, "_wait_for_project_window", lambda **kwargs: expected_window)

    result = backend.open_file_in_existing_window(window, project)

    assert lookup_payloads == [
        {"pid": 2233, "timeout_seconds": 10.0, "owner_root_handle": 111}
    ]
    assert submission_calls == [
        {
            "pid": 2233,
            "owner_root_handle": 111,
            "initial_dialog": picker,
            "expected_path": str(project),
        }
    ]
    assert result["dialog_submission"]["submitted_dialog_handle"] == 500
    assert result["dialog_protocol_schema_version"] == 2
    assert result["filename_field"] == {
        "ok": True,
        "method": "verified_test_setter",
    }
    assert result["path_binding"]["expected_path"] == str(project)
    assert result["pre_open_prompts"] == [{"action": "test_pre_open"}]
    assert result["dialog_closed"] is True
    assert result["dialog_owner_chain"] == [111]
    assert result["expected_project_window"]["title"] == expected_window.title
    assert result["spawned_process_ids"] == []


def test_windows_backend_cancels_file_association_dialog_without_claiming_files(
    monkeypatch,
) -> None:
    backend = WindowsGuiBackend()
    backend.supported = True
    dialog = WindowInfo(
        handle=700,
        title="Materials Studio File Associations",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    replacement = WindowInfo(
        handle=701,
        title="Materials Studio File Associations",
        pid=2233,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    find_results = iter([[dialog], [replacement], [], [], [], [], []])
    commands: list[tuple[int, int]] = []

    monkeypatch.setattr(
        gui_module,
        "_find_windows",
        lambda **kwargs: next(find_results, []),
    )
    monkeypatch.setattr(
        gui_module,
        "_post_dialog_command",
        lambda handle, command_id: commands.append((handle, command_id))
        or {"posted": True, "command_id": command_id},
    )
    monkeypatch.setattr(gui_module, "_wait_for_window_absent", lambda *args, **kwargs: True)

    result = backend.dismiss_startup_dialogs(pid=2233, timeout_seconds=1.0)

    assert commands == [(700, 2), (701, 2)]
    assert result[0]["action"] == "cancel_file_association_dialog"
    assert result[0]["submission"]["command_id"] == 2
    assert result[0]["closed"] is True
    assert result[0]["cancellation"]["replacement_cancel_count"] == 1
    assert result[0]["cancellation"]["family_stable_absent"] is True


def test_gui_open_structure_refuses_new_instance_file_open_backend(tmp_path: Path) -> None:
    backend = SpawningOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiError) as exc:
        controller.open_structure(structure, project_id="gui_proj", revision=3, take_snapshot=True)

    assert backend.opened == []
    assert backend.activated_handles == [100]
    assert "may create another Materials Studio window" in str(exc.value)


def test_gui_status_marks_spawning_file_open_as_not_hotload_ready(tmp_path: Path) -> None:
    backend = SpawningOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["window_found"] is True
    assert status["same_window_open_supported"] is False
    assert status["open_strategy_may_launch_new_instance"] is True
    assert status["can_open_structure_in_existing_window"] is False
    management = status["window_management"]
    assert management["same_window_open_supported"] is False
    assert management["file_open_may_launch_new_instance"] is True
    assert management["ready_for_same_window_open"] is False
    assert management["ready_for_open"] is False
    assert management["recommended_tool"] == "material_studio_gui_copy_script_assist"
    assert "same_window_open_not_supported_by_local_backend" in management["warnings"]


def test_gui_open_structure_uses_same_window_opener_when_file_open_may_spawn(tmp_path: Path) -> None:
    backend = SameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    result = controller.open_structure(structure, project_id="gui_proj", revision=4, take_snapshot=False)

    assert result["same_window_open_supported"] is True
    assert result["same_window_open_used"] is True
    assert result["open_result"]["method"] == "fake_same_window_open"
    assert result["open_result"]["same_window_open_requested"] is True
    assert backend.same_window_opened == [(100, structure.resolve())]
    assert backend.opened == []
    assert backend.activated_handles == [100, 100]


def test_gui_open_structure_flags_post_open_spawned_matstudio_process(tmp_path: Path) -> None:
    backend = SpawnAfterSameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    result = controller.open_structure(structure, project_id="gui_proj", revision=4, take_snapshot=False)

    assert result["same_window_open_used"] is True
    assert result["open_result"]["spawned_process_ids"] == [9999]
    assert result["single_window_policy_ok"] is False
    assert result["post_open_single_window_policy_ok"] is False
    assert "multiple_matstudio_processes_detected" in result["single_window_violation_reasons"]
    assert "matstudio_process_spawned_during_same_window_open" in result["single_window_violation_reasons"]
    assert result["post_open_window_management"]["process_count"] == 2
    assert result["post_open_window_management"]["recommended_tool"] == "material_studio_gui_status"
    assert result["post_open_window_management"]["ready_for_open"] is False


def test_gui_status_marks_same_window_opener_as_hotload_ready(tmp_path: Path) -> None:
    backend = SameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["same_window_open_supported"] is True
    assert status["open_strategy_may_launch_new_instance"] is True
    assert status["can_open_structure_in_existing_window"] is True
    management = status["window_management"]
    assert management["status"] == "ready_for_same_window_live_edit"
    assert management["same_window_open_supported"] is True
    assert management["ready_for_same_window_open"] is True
    assert management["ready_for_next_live_edit"] is True
    assert management["can_hotload_without_new_window"] is True
    assert management["ready_for_open"] is True
    assert "same_window_open_not_supported_by_local_backend" not in management["warnings"]


def test_gui_open_structure_wraps_generated_structure_for_windows_same_window_open(tmp_path: Path) -> None:
    backend = SameWindowWindowsFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    structure = _bind_structure_to_revision(
        controller,
        structure,
        project_id="gui_proj",
        revision=5,
    )

    result = controller.open_structure(structure, project_id="gui_proj", revision=5, take_snapshot=False)

    assert result["same_window_open_used"] is True
    assert result["open_result"]["method"] == "fake_windows_same_window_open"
    assert result["project_wrapper"]["source_path"] == str(structure.resolve())
    open_target = backend.same_window_opened[0][1]
    assert open_target.suffix == ".stp"
    assert open_target == Path(result["project_wrapper"]["project_path"])
    assert open_target.exists()
    assert backend.opened == []
    assert result["window"]["title"] == f"{open_target.stem} - Materials Studio"
    assert result["post_open_target_window_resolution"]["matched_project_window"] is True
    assert result["post_open_target_window_resolution"]["fallback_used"] is False
    assert result["post_open_window_management"]["status"] == "ready_for_same_window_live_edit"
    assert result["post_open_window_management"]["current_revision_loaded"] is True
    assert result["post_open_window_management"]["ready_for_next_live_edit"] is True
    assert result["post_open_window_management"]["needs_reload"] is False


def test_gui_open_structure_does_not_overwrite_input(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.xsd"
    structure.write_text("original", encoding="utf-8")

    result = controller.open_structure(structure, project_id="gui_proj", revision=1, take_snapshot=False)

    assert result["structure_path"] == str(structure.resolve())
    assert structure.read_text(encoding="utf-8") == "original"
    assert backend.opened == [structure.resolve()]


def test_gui_open_structure_does_not_activate_untracked_opened_window(
    tmp_path: Path,
) -> None:
    backend = NewWindowAfterOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    result = controller.open_structure(structure, project_id="gui_proj", revision=2, take_snapshot=False)

    assert result["activated_existing_window"] is True
    assert result["activated_opened_window"] is False
    assert result["window"]["handle"] == 200
    assert "target_window_pid_not_matstudio_process" in result[
        "post_open_single_window_violation_reasons"
    ]
    assert backend.activated_handles == [100]


def test_gui_project_wrapper_is_workspace_local(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.xsd"
    structure.write_text("<XSD />", encoding="utf-8")

    wrapper = controller._create_project_wrapper(
        structure.resolve(),
        project_id="gui proj",
        revision=2,
    )

    project_path = Path(wrapper["project_path"])
    document_path = Path(wrapper["document_path"])
    metadata_path = Path(wrapper["metadata_path"])
    assert project_path.exists()
    assert document_path.exists()
    assert metadata_path.exists()
    assert tmp_path in project_path.parents
    assert tmp_path in document_path.parents
    assert tmp_path in metadata_path.parents
    assert document_path.read_text(encoding="utf-8") == "<XSD />"
    assert structure.read_text(encoding="utf-8") == "<XSD />"
    assert f".\\{document_path.name}" in project_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["project_id"] == "gui proj"
    assert metadata["source_path"] == str(structure.resolve())


def test_gui_project_wrapper_uses_short_paths_for_long_project_ids(tmp_path: Path) -> None:
    controller = MaterialsStudioGuiController(tmp_path, backend=FakeGuiBackend())
    long_name = "ms_mcp_nl_molybdenum_disulfide_2d_mos2_monolayer_" + ("x" * 120)
    structure = tmp_path / ("generated_structure_" + ("y" * 120) + ".cif")
    structure.write_text("data_model\n", encoding="utf-8")

    wrapper = controller._create_project_wrapper(
        structure.resolve(),
        project_id=long_name,
        revision=0,
    )

    project_path = Path(wrapper["project_path"])
    document_path = Path(wrapper["document_path"])
    assert project_path.exists()
    assert document_path.exists()
    assert len(project_path.name) <= 40
    assert len(document_path.name) <= 40
    assert len(str(document_path)) < 240
    metadata = json.loads(Path(wrapper["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["project_id"] == long_name
    assert metadata["source_name"] == structure.name


def test_gui_status_maps_wrapper_window_to_project_revision(tmp_path: Path) -> None:
    backend = FakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(controller, structure, project_id="live_window_proj", revision=4)
    project_name = wrapper["project_name"]
    backend.window = WindowInfo(
        handle=404,
        title=f"{project_name} - Materials Studio",
        pid=1234,
        rect=(0, 0, 800, 600),
    )

    status = controller.status()

    assert status["window_count"] == 1
    assert status["live_window_count"] == 1
    assert status["process_count"] == 1
    assert status["wrapper_window_count"] == 1
    assert status["windows"][0]["project_id"] == "live_window_proj"
    assert status["windows"][0]["revision"] == 4
    assert status["windows"][0]["project_wrapper_metadata"]["source_path"] == wrapper[
        "source_path"
    ]
    management = status["window_management"]
    assert management["selected_window_project_id"] == "live_window_proj"
    assert management["target_window_project_id"] == "live_window_proj"
    assert management["target_window_is_selected"] is True
    assert management["recommended_tool"] == "material_studio_gui_snapshot"
    assert management["recommended_action"] == "snapshot_target_project_window"


def test_gui_status_resolves_requested_project_revision_window(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(controller, structure, project_id="current_proj", revision=5)
    target_window = WindowInfo(
        handle=505,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.windows = [backend.default_window, target_window]

    status = controller.status(project_id="current_proj", revision=5)

    assert status["requested_project_id"] == "current_proj"
    assert status["requested_revision"] == 5
    assert status["selected_window_handle"] == 100
    assert status["target_window_resolution"]["matched_project_window"] is True
    assert status["target_window_resolution"]["target_handle"] == 505
    assert status["target_window_resolution"]["fallback_used"] is False
    assert status["status"] == "single_window_policy_violation"
    assert status["recommended_tool"] == "material_studio_gui_status"
    assert status["recommended_action"] == "close_save_extra_matstudio_windows_then_retry_hotload"
    assert status["can_apply_current_revision_without_new_window"] is False
    assert status["needs_single_window_resolution"] is True
    management = status["window_management"]
    assert management["process_count"] == 1
    assert management["window_count"] == 2
    assert management["wrapper_window_count"] == 1
    assert management["target_window_handle"] == 505
    assert management["target_window_project_id"] == "current_proj"
    assert management["target_window_revision"] == 5
    assert management["target_window_is_selected"] is False
    assert management["matched_project_window"] is True
    assert management["fallback_used"] is False
    assert management["ready_for_same_window_open"] is False
    assert management["ready_for_open"] is False
    assert status["can_open_structure_in_existing_window"] is False
    assert management["recommended_tool"] == "material_studio_gui_status"
    assert management["recommended_action"] == "close_save_extra_matstudio_windows_then_retry_hotload"
    assert status["single_window_policy_ok"] is False
    assert status["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]
    assert management["single_window_policy_ok"] is False
    assert management["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]
    assert management["hotload_requires_existing_window"] is True
    assert management["auto_launch_during_open_allowed"] is False
    assert "multiple_matstudio_windows_detected" in management["warnings"]
    assert "selected_window_is_not_target_window" in management["warnings"]


def test_gui_status_isolates_exact_project_target_across_matstudio_processes(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = {1234, 5678}
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="isolated_proj",
        revision=7,
    )
    target_window = WindowInfo(
        handle=707,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=5678,
        rect=(0, 0, 900, 700),
    )
    unrelated_dialog = WindowInfo(
        handle=708,
        title="Save Project As",
        pid=1234,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    backend.windows = [
        backend.default_window,
        target_window,
        unrelated_dialog,
    ]

    status = controller.status(project_id="isolated_proj", revision=7)
    management = status["window_management"]

    assert status["process_count"] == 2
    assert status["window_count"] == 3
    assert status["matstudio_window_count"] == 3
    assert status["single_window_policy_ok"] is True
    assert status["single_window_violation_reasons"] == []
    assert status["project_scoped_multi_instance_isolation"] is True
    assert status["window_isolation_mode"] == "exact_project_target_process"
    assert status["target_process_id"] == 5678
    assert status["unrelated_process_ids"] == [1234]
    assert status["status"] == "target_window_needs_activation"
    assert status["recommended_tool"] == "material_studio_gui_activate"
    assert status["can_apply_current_revision_without_new_window"] is True
    assert status["ready_for_next_live_edit"] is False
    assert management["target_process_primary_window_count"] == 1
    assert management["target_process_dialog_window_count"] == 0
    assert management["global_dialog_window_count"] == 1
    assert management["unrelated_dialog_window_count"] == 1
    assert management["unresolved_blocking_dialog_count"] == 0
    assert management["unrelated_process_count"] == 1
    assert management["unrelated_primary_window_count"] == 1
    assert "multiple_matstudio_processes_detected" in management["warnings"]
    assert "multiple_matstudio_windows_detected" in management["warnings"]
    assert (
        "project_scoped_multi_instance_isolation_active"
        in management["warnings"]
    )
    assert "unrelated_matstudio_dialogs_ignored" in management["warnings"]

    activated = controller.activate(project_id="isolated_proj", revision=7)
    snapshot = controller.snapshot(
        label="isolated",
        project_id="isolated_proj",
        revision=7,
    )
    replay_block_reasons = gui_module._local_view_replay_status_block_reasons(
        controller.status(project_id="isolated_proj", revision=7)
    )

    assert activated["activated"] is True
    assert activated["window"]["handle"] == 707
    assert snapshot["window"]["handle"] == 707
    assert backend.activated_handles == [707]
    assert backend.captured_handles == [707]
    assert "exactly_one_matstudio_process_required" not in replay_block_reasons
    assert "target_window_pid_not_matstudio_process" not in replay_block_reasons


def test_gui_status_keeps_target_process_dialog_as_hotload_blocker(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = {1234, 5678}
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="blocked_target_proj",
        revision=2,
    )
    target_window = WindowInfo(
        handle=720,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=5678,
        rect=(0, 0, 900, 700),
    )
    target_dialog = WindowInfo(
        handle=721,
        title="Save Project As",
        pid=5678,
        rect=(20, 20, 500, 300),
        class_name="#32770",
    )
    backend.windows = [
        backend.default_window,
        target_window,
        target_dialog,
    ]

    status = controller.status(
        project_id="blocked_target_proj",
        revision=2,
    )
    management = status["window_management"]

    assert status["single_window_policy_ok"] is True
    assert status["project_scoped_multi_instance_isolation"] is True
    assert status["status"] == "modal_dialog_blocking_hotload"
    assert status["recommended_tool"] == "material_studio_gui_activate"
    assert status["can_apply_current_revision_without_new_window"] is False
    assert management["target_process_dialog_window_count"] == 1
    assert management["unresolved_blocking_dialog_count"] == 1
    assert management["unrelated_dialog_window_count"] == 0


def test_gui_open_structure_targets_exact_project_process_amid_other_sessions(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = {1234, 5678}
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    current_structure = tmp_path / "current.cif"
    current_structure.write_text("data_current\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        current_structure,
        project_id="hotload_proj",
        revision=3,
    )
    target_window = WindowInfo(
        handle=730,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=5678,
        rect=(0, 0, 900, 700),
    )
    backend.windows = [backend.default_window, target_window]
    next_structure = tmp_path / "next.cif"
    next_structure.write_text("data_next\n", encoding="utf-8")

    opened = controller.open_structure(
        next_structure,
        project_id="hotload_proj",
        revision=3,
        take_snapshot=False,
    )

    assert backend.same_window_opened == [
        (730, next_structure.resolve())
    ]
    assert opened["same_window_open_used"] is True
    assert opened["single_window_policy_ok"] is True
    assert opened["single_window_violation_reasons"] == []
    assert opened["window"]["handle"] == 730
    assert opened["window_management"][
        "project_scoped_multi_instance_isolation"
    ] is True
    assert opened["post_open_window_management"][
        "project_scoped_multi_instance_isolation"
    ] is True
    assert opened["post_open_window_management"]["target_process_id"] == 5678


def test_gui_open_structure_does_not_activate_post_open_duplicate_wrapper(
    tmp_path: Path,
) -> None:
    backend = DuplicateWrapperAfterSameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    opened = controller.open_structure(
        structure,
        project_id="post_open_duplicate",
        revision=1,
        take_snapshot=False,
    )

    assert opened["activated_existing_window"] is True
    assert opened["activated_opened_window"] is False
    assert opened["post_open_target_window_resolution"][
        "matching_window_count"
    ] == 2
    assert "requested_project_revision_window_ambiguous" in opened[
        "post_open_single_window_violation_reasons"
    ]
    assert opened["post_open_window_management"][
        "single_window_policy_ok"
    ] is False
    assert backend.activated_handles == [backend.default_window.handle]


def test_gui_status_ignores_non_matstudio_title_match_window(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    browser_help = WindowInfo(
        handle=999,
        title=(
            "Materials Studio 2020 Online Help - File Associations dialog "
            "- Google Chrome"
        ),
        pid=9999,
        rect=(0, 0, 1200, 800),
        class_name="Chrome_WidgetWin_1",
    )
    backend.windows = [backend.default_window, browser_help]

    status = controller.status()
    management = status["window_management"]

    assert status["window_count"] == 2
    assert status["matstudio_window_count"] == 1
    assert status["ignored_non_matstudio_window_count"] == 1
    assert status["single_window_policy_ok"] is True
    assert status["status"] == "ready_for_same_window_live_edit"
    assert management["primary_window_count"] == 1
    assert management["ignored_non_matstudio_window_count"] == 1
    assert "non_matstudio_title_match_ignored" in management["warnings"]


def test_gui_status_never_selects_non_matstudio_title_match_window(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    browser_help = WindowInfo(
        handle=999,
        title="Materials Studio 2020 Online Help - Google Chrome",
        pid=9999,
        rect=(0, 0, 1200, 800),
        class_name="Chrome_WidgetWin_1",
        is_foreground=True,
    )
    backend.window = browser_help
    backend.windows = [browser_help, backend.default_window]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["window_found"] is True
    assert status["selected_window_handle"] == backend.default_window.handle
    assert status["target_process_id"] == backend.default_window.pid
    assert status["target_window_pid_is_matstudio_process"] is True
    assert status["ignored_non_matstudio_window_count"] == 1
    assert status["single_window_policy_ok"] is True


def test_gui_status_refuses_title_only_window_without_matstudio_pid(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    browser_help = WindowInfo(
        handle=999,
        title="Materials Studio 2020 Online Help - Google Chrome",
        pid=9999,
        rect=(0, 0, 1200, 800),
        class_name="Chrome_WidgetWin_1",
        is_foreground=True,
    )
    backend.window = browser_help
    backend.windows = [browser_help]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["window_found"] is False
    assert status["target_window_found"] is False
    assert status["ignored_non_matstudio_window_count"] == 1
    assert status["status"] == "matstudio_process_without_usable_window"
    assert status["can_open_structure_in_existing_window"] is False
    with pytest.raises(GuiError, match="未找到打开的 Materials Studio 窗口"):
        controller.snapshot(label="must_not_capture")
    assert backend.captured_handles == []


def test_gui_status_ignores_title_only_window_when_process_inventory_is_empty(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = set()
    browser_help = WindowInfo(
        handle=999,
        title="Materials Studio 2020 Online Help - Google Chrome",
        pid=9999,
        rect=(0, 0, 1200, 800),
        class_name="Chrome_WidgetWin_1",
        is_foreground=True,
    )
    backend.window = browser_help
    backend.windows = [browser_help]
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    status = controller.status()

    assert status["process_count"] == 0
    assert status["window_found"] is False
    assert status["target_window_found"] is False
    assert status["matstudio_window_count"] == 0
    assert status["ignored_non_matstudio_window_count"] == 1
    assert status["status"] == "target_window_missing"
    assert status["can_open_structure_in_existing_window"] is False


def test_gui_direct_actions_refuse_duplicate_matching_revision_wrappers(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = {1234, 5678}
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="duplicate_target",
        revision=4,
    )
    title = f"{wrapper['project_name']} - Materials Studio"
    first = WindowInfo(
        handle=740,
        title=title,
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    second = WindowInfo(
        handle=741,
        title=title,
        pid=5678,
        rect=(0, 0, 900, 700),
    )
    backend.window = first
    backend.windows = [first, second]

    status = controller.status(project_id="duplicate_target", revision=4)

    assert status["target_window_resolution"]["matching_window_count"] == 2
    assert status["single_window_policy_ok"] is False
    assert "requested_project_revision_window_ambiguous" in status[
        "single_window_violation_reasons"
    ]
    with pytest.raises(GuiError, match="more than one live Materials Studio"):
        controller.activate(project_id="duplicate_target", revision=4)
    with pytest.raises(GuiError, match="more than one live Materials Studio"):
        controller.snapshot(
            label="duplicate",
            project_id="duplicate_target",
            revision=4,
        )
    assert backend.activated_handles == []
    assert backend.captured_handles == []


def test_gui_current_revision_requires_strong_wrapper_integrity(
    tmp_path: Path,
) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="tampered_target",
        revision=2,
    )
    project_path = Path(wrapper["project_path"])
    project_path.write_text("<Project><Version>20.1</Version></Project>\n", encoding="utf-8")
    target = WindowInfo(
        handle=750,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.window = target
    backend.windows = [target]

    status = controller.status(project_id="tampered_target", revision=2)
    metadata = status["target_window_resolution"][
        "target_project_wrapper_metadata"
    ]

    assert metadata["wrapper_integrity_verified"] is False
    assert metadata["wrapper_provenance_status"] == "unverified_revision_wrapper"
    assert status["current_revision_loaded"] is False
    assert status["needs_reload"] is True
    assert status["single_window_policy_ok"] is False
    assert "target_wrapper_identity_unverified" in status[
        "single_window_violation_reasons"
    ]
    with pytest.raises(GuiError, match="target_wrapper_integrity_unverified"):
        controller.snapshot(
            label="tampered",
            project_id="tampered_target",
            revision=2,
        )
    replacement = tmp_path / "replacement.cif"
    replacement.write_text("data_replacement\n", encoding="utf-8")
    with pytest.raises(GuiError, match="target_wrapper_identity_unverified"):
        controller.open_structure(
            replacement,
            project_id="tampered_target",
            revision=2,
            take_snapshot=False,
        )
    assert backend.captured_handles == []
    assert backend.activated_handles == []
    assert backend.opened == []


def test_gui_wrapper_identity_manifest_rejects_metadata_project_rebinding(
    tmp_path: Path,
) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="identity_project_a",
        revision=2,
    )
    metadata_path = Path(wrapper["metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["project_id"] = "identity_project_b"
    identity_path = Path(wrapper["identity_manifest_path"])
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["project_id"] = "identity_project_b"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    metadata["identity_manifest_sha256"] = hashlib.sha256(
        identity_path.read_bytes()
    ).hexdigest()
    metadata["identity_manifest_size_bytes"] = identity_path.stat().st_size
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    rebound_revision = (
        tmp_path
        / "identity_project_b"
        / "revisions"
        / "r002_model_spec.json"
    )
    rebound_revision.parent.mkdir(parents=True, exist_ok=True)
    rebound_revision.write_text(
        json.dumps({"project_id": "identity_project_b", "revision": 2}),
        encoding="utf-8",
    )
    target = WindowInfo(
        handle=751,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.window = target
    backend.windows = [target]

    status = controller.status(project_id="identity_project_b", revision=2)
    target_metadata = status["target_window_resolution"][
        "target_project_wrapper_metadata"
    ]

    assert status["target_window_resolution"]["matched_project_window"] is True
    assert target_metadata["wrapper_integrity_verified"] is False
    assert target_metadata["wrapper_identity_manifest_valid"] is True
    assert target_metadata["wrapper_revision_state_binding_valid"] is False
    assert "wrapper_revision_state_binding_invalid" in target_metadata[
        "wrapper_integrity_reason_codes"
    ]
    assert status["current_revision_loaded"] is False
    assert status["single_window_policy_ok"] is False
    replacement = tmp_path / "replacement.cif"
    replacement.write_text("data_replacement\n", encoding="utf-8")
    with pytest.raises(GuiError, match="target_wrapper_identity_unverified"):
        controller.open_structure(
            replacement,
            project_id="identity_project_b",
            revision=2,
            take_snapshot=False,
        )
    assert backend.activated_handles == []
    assert backend.opened == []


def test_gui_legacy_wrapper_requires_independent_revision_state_binding(
    tmp_path: Path,
) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    project_id = "legacy_state_bound_project"
    revision = 2
    source = (
        tmp_path
        / project_id
        / "outputs"
        / f"r{revision:03d}"
        / "structure.cif"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("data_legacy\n", encoding="utf-8")
    revision_path = (
        tmp_path
        / project_id
        / "revisions"
        / f"r{revision:03d}_model_spec.json"
    )
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(
        json.dumps({"project_id": project_id, "revision": revision}),
        encoding="utf-8",
    )
    wrapper = controller._create_project_wrapper(
        source.resolve(),
        project_id=project_id,
        revision=revision,
    )
    metadata_path = Path(wrapper["metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["wrapper_schema_version"] = 2
    metadata["wrapper_profile"] = "materials_studio_20_1_project_wrapper_v1"
    for field in (
        "source_sha256",
        "source_size_bytes",
        "identity_manifest_name",
        "identity_manifest_sha256",
        "identity_manifest_size_bytes",
    ):
        metadata.pop(field, None)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    Path(wrapper["identity_manifest_path"]).unlink()
    target = WindowInfo(
        handle=754,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.window = target
    backend.windows = [target]

    trusted = controller.status(project_id=project_id, revision=revision)
    trusted_metadata = trusted["target_window_resolution"][
        "target_project_wrapper_metadata"
    ]

    assert trusted["current_revision_loaded"] is True
    assert trusted_metadata["wrapper_integrity_verified"] is True
    assert trusted_metadata["legacy_revision_state_binding_valid"] is True

    revision_path.write_text(
        json.dumps({"project_id": "different_project", "revision": revision}),
        encoding="utf-8",
    )
    untrusted = controller.status(project_id=project_id, revision=revision)
    untrusted_metadata = untrusted["target_window_resolution"][
        "target_project_wrapper_metadata"
    ]

    assert untrusted["current_revision_loaded"] is False
    assert untrusted["single_window_policy_ok"] is False
    assert untrusted_metadata["wrapper_integrity_verified"] is False
    assert "wrapper_revision_state_binding_invalid" in untrusted_metadata[
        "wrapper_integrity_reason_codes"
    ]


def test_gui_wrapper_title_matching_never_normalizes_project_identity(
    tmp_path: Path,
) -> None:
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    backend.process_ids = {1234, 5678}
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        controller,
        structure,
        project_id="exact_title_project",
        revision=3,
    )
    altered_title = f"{wrapper['project_name'].replace('_', ' ', 1)} - Materials Studio"
    altered = WindowInfo(
        handle=752,
        title=altered_title,
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    unrelated = WindowInfo(
        handle=753,
        title="unrelated - Materials Studio",
        pid=5678,
        rect=(20, 20, 920, 720),
    )
    backend.window = altered
    backend.windows = [altered, unrelated]

    status = controller.status(project_id="exact_title_project", revision=3)

    assert status["target_window_resolution"]["matched_project_window"] is False
    assert status["current_revision_loaded"] is False
    assert status["single_window_policy_ok"] is False
    assert status["window_management"]["wrapper_window_count"] == 0
    with pytest.raises(GuiError, match="single-window policy"):
        controller.open_structure(
            structure,
            project_id="exact_title_project",
            revision=3,
            take_snapshot=False,
        )
    assert backend.same_window_opened == []


def test_gui_direct_actions_refuse_wrapper_from_trusted_external_workspace(
    tmp_path: Path,
) -> None:
    controller_root = tmp_path / "controller"
    external_root = tmp_path / "external"
    backend = MultiProcessSameWindowOpenFakeGuiBackend()
    external_controller = MaterialsStudioGuiController(
        external_root,
        backend=backend,
    )
    structure = external_root / "model.cif"
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(
        external_controller,
        structure,
        project_id="external_target",
        revision=6,
    )
    target = WindowInfo(
        handle=760,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    backend.window = target
    backend.windows = [target]
    controller = MaterialsStudioGuiController(
        controller_root,
        backend=backend,
    )
    controller.trusted_wrapper_workspace_roots = (
        (controller_root.resolve(), "controller"),
        (external_root.resolve(), "test_external"),
    )

    status = controller.status(project_id="external_target", revision=6)

    assert status["target_window_resolution"]["matched_project_window"] is True
    assert status["workspace_context_mismatch"] is True
    assert status["current_revision_loaded"] is False
    with pytest.raises(GuiError, match="target_wrapper_workspace_mismatch"):
        controller.activate(project_id="external_target", revision=6)
    with pytest.raises(GuiError, match="target_wrapper_workspace_mismatch"):
        controller.snapshot(
            label="external",
            project_id="external_target",
            revision=6,
        )
    launched = controller.launch(
        project_id="external_target",
        revision=6,
        wait_seconds=1,
        take_snapshot=False,
    )
    assert launched["launch_blocked"] is True
    assert launched["launch_block_reason"] == "single_window_policy_violation"
    assert "target_wrapper_workspace_mismatch" in launched[
        "single_window_violation_reasons"
    ]
    assert backend.activated_handles == []
    assert backend.captured_handles == []


def test_gui_snapshot_and_activate_target_matching_project_wrapper_window(tmp_path: Path) -> None:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(controller, structure, project_id="current_proj", revision=5)
    target_window = WindowInfo(
        handle=505,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 900, 700),
    )
    backend.windows = [backend.default_window, target_window]

    status = controller.status(project_id="current_proj", revision=5)
    assert status["target_window"]["handle"] == 505
    assert status["target_window_resolution"]["matched_project_window"] is True
    assert status["single_window_policy_ok"] is False
    assert status["single_window_violation_reasons"] == [
        "multiple_matstudio_windows_detected"
    ]
    assert status["ready_for_snapshot"] is False

    with pytest.raises(GuiError, match="not uniquely verified"):
        controller.snapshot(
            label="current",
            project_id="current_proj",
            revision=5,
        )
    with pytest.raises(GuiError, match="not uniquely verified"):
        controller.activate(project_id="current_proj", revision=5)

    assert backend.captured_handles == []
    assert backend.activated_handles == []


def test_wait_for_window_after_open_prefers_expected_project_title(tmp_path: Path) -> None:
    backend = DelayedWrapperTitleFakeGuiBackend(project_name="msmcp_r004_demo")
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)

    window = controller._wait_for_window_after_open(
        previous_window=backend.window,
        timeout_seconds=1.0,
        expected_project_name="msmcp_r004_demo",
        poll_interval_seconds=0.0,
    )

    assert window is not None
    assert window.handle == 300
    assert window.title == "msmcp_r004_demo - Materials Studio"
    assert backend.find_count >= 2


def test_gui_rejects_path_traversal_project_id(tmp_path: Path) -> None:
    controller = MaterialsStudioGuiController(tmp_path, backend=FakeGuiBackend())

    with pytest.raises(ValueError, match="project_id"):
        controller.snapshot(label="bad", project_id="../bad", revision=0)


def _view_replay_audit(project_id: str, revision: int) -> dict:
    return {
        "project_id": project_id,
        "revision": revision,
        "model_type": "crystal",
        "spec_fingerprint": "abc123",
        "views": [
            {
                "name": "crystal_100",
                "supported": True,
                "coordinate_system": "crystal_lattice_direction",
                "crystal_direction_indices": [1, 0, 0],
                "crystal_direction_label": "[100]",
                "crystal_direction_cartesian": [1.0, 0.0, 0.0],
                "camera_direction": [1.0, 0.0, 0.0],
                "camera_up": [0.0, 0.0, 1.0],
                "camera_right": [0.0, 1.0, 0.0],
                "look_at_direction": [-1.0, 0.0, 0.0],
                "camera_position": [10.0, 0.0, 0.0],
                "camera_distance_angstrom": 10.0,
                "target": [0.0, 0.0, 0.0],
                "framing": {
                    "orthographic_width_angstrom": 8.0,
                    "orthographic_height_angstrom": 8.0,
                    "near_clip_angstrom": 1.0,
                    "far_clip_angstrom": 20.0,
                    "projection_units": "angstrom_relative_to_target",
                },
                "atom_projection_count": 8,
                "projection_bbox_angstrom": {"x": [-2.0, 2.0], "y": [-2.0, 2.0], "depth": [-2.0, 2.0]},
                "projection_span_angstrom": {"x": 4.0, "y": 4.0, "depth": 4.0},
                "overlap_candidates": [],
                "health": {"ok": True, "warnings": []},
            }
        ],
    }


def _controller_with_verified_project_window(tmp_path: Path) -> tuple[MaterialsStudioGuiController, MultiWindowFakeGuiBackend]:
    backend = MultiWindowFakeGuiBackend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = _create_bound_wrapper(controller, structure, project_id="view_proj", revision=2)
    backend.window = WindowInfo(
        handle=100,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 800, 600),
    )
    backend.default_window = backend.window
    backend.windows = [backend.window]
    return controller, backend


def test_view_command_evidence_verifies_installed_arrow_key_help(
    monkeypatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "Materials Studio 20.1"
    executable = install_root / "bin" / "MatStudio.exe"
    registry = install_root / "share" / "Commands" / "#SVViewer3d.xml"
    symmetry_registry = install_root / "share" / "Commands" / "SMPSymmetryBuilderMenu.xml"
    tree_registry = install_root / "share" / "Commands" / "SMTreeExplorer.xml"
    tree_component = install_root / "share" / "Components" / "SMTreeExplorer.xml"
    properties_registry = install_root / "share" / "Commands" / "SMGenPropEditor.xml"
    explorers_help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "interface"
        / "explorers.htm"
    )
    project_explorer_help_path = explorers_help_path.parent / "projectexplorer.htm"
    help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "interface"
        / "mouseandkeyboardactions.htm"
    )
    movement_help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "sketching"
        / "dlgmovement.htm"
    )
    create_help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "sketching"
        / "tskmillerplanes_create.htm"
    )
    working_help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "sketching"
        / "tskmillerplanes_working.htm"
    )
    positioning_help_path = (
        install_root
        / "share"
        / "doc"
        / "content"
        / "core"
        / "viewers"
        / "settingpositionandorientation.htm"
    )
    executable.parent.mkdir(parents=True)
    registry.parent.mkdir(parents=True)
    help_path.parent.mkdir(parents=True)
    tree_component.parent.mkdir(parents=True)
    movement_help_path.parent.mkdir(parents=True)
    positioning_help_path.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    registry.write_text(
        """
        <COMMANDS>
          <ITEM NAME="cmdViewer3DResetView"/>
          <ITEM NAME="cmdViewer3DViewOnto"/>
          <ITEM NAME="cmdViewer3DMovementOptions"/>
          <TOOLBAR NAME="tbarViewer3D1" TITLE="3D Viewer">
            <TOOL NAME="cmdViewer3DSelection"/>
            <TOOL NAME="cmdViewer3DTrackball"/>
            <TOOL NAME="cmdViewer3DZoom"/>
            <TOOL NAME="cmdViewer3DTranslate"/>
            <SEPARATOR/>
            <TOOL NAME="cmdViewer3DResetView"/>
            <TOOL NAME="cmdViewer3DRecenter"/>
            <TOOL NAME="cmdViewer3DFitToView"/>
            <TOOL NAME="cmdViewer3DDisplayStyle"/>
          </TOOLBAR>
          <TOOLBAR NAME="tbarViewer3DMovement" TITLE="3D Movement">
            <TOOL NAME="cmdNudgeLeft"/>
            <TOOL NAME="cmdNudgeRight"/>
            <TOOL NAME="cmdNudgeUp"/>
            <TOOL NAME="cmdNudgeDown"/>
            <TOOL NAME="cmdViewer3DMovementOptions"/>
            <SEPARATOR/>
            <TOOL NAME="cmdSMSketcherMoveTo"/>
            <TOOL NAME="cmdViewer3DAlignOntoView"/>
          </TOOLBAR>
        </COMMANDS>
        """,
        encoding="utf-8",
    )
    symmetry_registry.write_text(
        '<commands><item name="cmdSymmetryBuilderMillerPlanes"/></commands>',
        encoding="utf-8",
    )
    tree_registry.write_text(
        '<commands><item name="cmdTEToggleExplorer"/></commands>',
        encoding="utf-8",
    )
    tree_component.write_text(
        '<explorer NAME="Object Tree" HIDDEN="Yes"/>',
        encoding="utf-8",
    )
    properties_registry.write_text(
        '<commands><item name="cmdGPEToggleExplorer"/></commands>',
        encoding="utf-8",
    )
    explorers_help_path.write_text(
        "The following explorers are used: Project Explorer, Properties Explorer, Job Explorer.",
        encoding="utf-8",
    )
    project_explorer_help_path.write_text(
        (
            "The Project Explorer enables you to access the documents associated with a project. "
            "It shows project documents and folders."
        ),
        encoding="utf-8",
    )
    help_path.write_text(
        """
        <td>Rotate view about X 45 degrees clockwise</td><td>UP arrow</td>
        <td>Rotate view about X 45 degrees counterclockwise</td><td>DOWN arrow</td>
        <td>Rotate view about Y 45 degrees clockwise</td><td>LEFT arrow</td>
        <td>Rotate view about Y 45 degrees counterclockwise</td><td>RIGHT arrow</td>
        <td>Rotate selected objects about X 45 degrees clockwise</td><td>SHIFT + DOWN arrow</td>
        <td>Rotate selected objects about X 45 degrees counterclockwise</td><td>SHIFT + UP arrow</td>
        <td>Rotate selected objects about Y 45 degrees clockwise</td><td>SHIFT + RIGHT arrow</td>
        <td>Rotate selected objects about Y 45 degrees counterclockwise</td><td>SHIFT + LEFT arrow</td>
        <p>The arrow key rotation angle can be set using the Movement dialog.</p>
        """,
        encoding="utf-8",
    )
    movement_help_path.write_text(
        "Movement dialog. Angle: Specify the angular displacement rate, in degrees. Default = 45.",
        encoding="utf-8",
    )
    create_help_path.write_text(
        (
            'Choose Tools | Miller Planes. Enter Miller indices (h k l). '
            'Click the <span class="uif">Create</span> button.'
        ),
        encoding="utf-8",
    )
    working_help_path.write_text(
        (
            "Create the plane and select a single Miller plane. Select Miller Plane from the "
            "Filter dropdown list in the Properties Explorer. Use the options arrow associated "
            "with the 3D Viewer Recenter button and select View Onto from the dropdown list so "
            "the plane is parallel to the screen. "
            "The Object Tree parents are Miller Parallel Planes and Miller Family."
        ),
        encoding="utf-8",
    )
    positioning_help_path.write_text(
        (
            "View Onto depends on the initial orientation; the smallest, acute, angle is used "
            "to orient the fragment."
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gui_module, "_resolve_matstudio_exe", lambda: executable)

    evidence = gui_module._materials_studio_view_command_evidence()

    assert evidence["registry_found"] is True
    assert evidence["registry_sha256"] == hashlib.sha256(
        registry.read_bytes()
    ).hexdigest()
    assert evidence["registry_toolbar_parse_error"] is None
    assert [
        (item["registry_toolbar_name"], item["title"], len(item["entries"]))
        for item in evidence["registry_toolbar_layouts"]
    ] == [
        ("tbarViewer3D1", "3D Viewer", 9),
        ("tbarViewer3DMovement", "3D Movement", 8),
    ]
    assert evidence["keyboard_help_found"] is True
    assert evidence["keyboard_help_path"] == str(help_path.resolve())
    assert evidence["unmodified_arrow_keys_rotate_view"] is True
    assert evidence["default_arrow_rotation_increment_degrees"] == 45
    assert evidence["arrow_rotation_angle_user_configurable"] is True
    assert evidence["shift_arrow_keys_rotate_selected_objects"] is True
    assert evidence["shift_arrow_keys_prohibited_for_view_replay"] is True
    assert evidence["movement_help_path"] == str(movement_help_path.resolve())
    assert evidence["movement_help_found"] is True
    assert evidence["movement_options_command_registered"] is True
    assert evidence["movement_dialog_angle_supported"] is True
    assert evidence["movement_angle_control_id"] == "numNudgeAngle"
    assert evidence["movement_screen_factor_control_id"] == "numNudgeFactor"
    assert evidence["registered_view_command_ids"] == [
        "cmdViewer3DResetView",
        "cmdViewer3DRecenter",
        "cmdViewer3DViewOnto",
        "cmdViewer3DFitToView",
        "cmdViewer3DMovementOptions",
    ]
    assert evidence["miller_plane_command_registered"] is True
    assert evidence["tree_explorer_command_registered"] is True
    assert evidence["tree_explorer_component_hidden"] is True
    assert evidence["public_explorer_inventory_verified"] is True
    assert evidence["public_explorer_inventory_excludes_tree"] is True
    assert evidence["project_explorer_documents_only_verified"] is True
    assert evidence["properties_explorer_command_registered"] is True
    assert evidence["miller_plane_create_workflow_verified"] is True
    assert evidence["miller_plane_selection_view_onto_workflow_verified"] is True
    assert (
        evidence["viewport_miller_plane_selection_properties_workflow_verified"]
        is True
    )
    assert evidence["object_tree_hierarchy_help_verified"] is True
    assert evidence["native_view_roll_policy_documented"] is True


def test_prepare_view_replay_persists_preview_manifest_without_gui_input(tmp_path: Path) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)

    result = controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )

    assert result["ready_for_external_replay"] is True
    assert result["replay_status"] == "ready_for_external_replay"
    assert result["view_names"] == ["crystal_100"]
    assert backend.activated_handles == []
    manifest_path = Path(result["manifest_path"])
    assert manifest_path == tmp_path / "view_proj" / "outputs" / "r002" / "gui_view_replay_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["replay_mode"] == "preview_manifest_only"
    assert manifest["viewport_control"]["execute_supported_by_this_tool"] is False
    assert manifest["safety_gate"]["blind_toolbar_or_coordinate_action_allowed"] is False
    assert manifest["safety_gate"]["pre_activation_screenshot_may_capture_occluding_window"] is True
    assert manifest["preflight"]["target_window_identity_verified"] is True
    assert manifest["views"][0]["camera"]["camera_direction"] == [1.0, 0.0, 0.0]
    assert manifest["views"][0]["verification"]["atom_projection_count"] == 8
    command_ids = {
        command["command_id"]
        for command in manifest["viewport_control"]["known_native_commands"]["commands"]
    }
    assert "cmdViewer3DResetView" in command_ids
    assert "cmdViewer3DFitToView" in command_ids


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "ready_for_external_replay"),
        ({"preflight_ready": False}, "blocked"),
        (
            {"preflight_ready": False, "accepted_view_count": 1},
            "blocked_with_prior_confirmation",
        ),
        ({"pending_recipe_upgrade_required": True}, "recipe_upgrade_required"),
        (
            {"pending_recipe_upgrade_required": True, "accepted_view_count": 1},
            "partially_confirmed",
        ),
    ],
)
def test_derive_view_replay_status_is_exhaustive(
    overrides: dict[str, object],
    expected: str,
) -> None:
    inputs: dict[str, object] = {
        "preflight_ready": True,
        "all_confirmed": False,
        "accepted_view_count": 0,
        "integrity_blocked": False,
        "journal_blocked": False,
        "automatic_postcheck_failed": False,
        "pending_recipe_upgrade_required": False,
    }
    inputs.update(overrides)

    assert gui_module._derive_view_replay_status(**inputs) == expected


def test_record_view_replay_requires_manifest_view_and_persists_append_only_event(tmp_path: Path) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    prepared = controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )

    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="computer_use",
        model_visible=True,
        camera_matches_manifest=True,
        note="Activated target window and visually checked the [100] view.",
    )

    assert recorded["accepted"] is True
    assert recorded["replay_status"] == "externally_confirmed"
    assert recorded["replay_summary"]["all_supported_views_confirmed"] is True
    events = Path(recorded["events_path"]).read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    assert json.loads(events[0])["view_name"] == "crystal_100"
    manifest = json.loads(Path(prepared["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["replay_events"][0]["accepted"] is True

    regenerated = controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    assert regenerated["replay_status"] == "externally_confirmed"
    assert regenerated["manifest"]["preserved_replay_event_count"] == 1
    assert regenerated["manifest"]["replay_summary"]["accepted_view_count"] == 1
    assert regenerated["next_action"]["recommended_tool"] == "material_studio_live_project_status"

    with pytest.raises(GuiError, match="not present"):
        controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_111",
            source="computer_use",
            model_visible=True,
            camera_matches_manifest=True,
        )

    with pytest.raises(GuiError, match="unsupported view replay source"):
        controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="untrusted_backend",
            model_visible=True,
            camera_matches_manifest=True,
        )


def _reviewed_copy_script_evidence(
    script_text: str = (
        "use MaterialsScript qw(:all);\n"
        'my $doc = $Documents{"model.xsd"};\n'
        "$doc->Views->ActiveView->Camera->ResetView();\n"
    ),
) -> dict[str, object]:
    return {
        "script_text": script_text,
        "capture_method": "materials_studio_copy_script",
        "reviewer": "human_review",
        "copy_script_command_observed": True,
        "review_completed": True,
        "view_action_matches_manifest": True,
        "structure_unchanged_observed": True,
        "note": "Reviewed as an inert camera/view action.",
    }


def test_record_view_replay_requires_bound_reviewed_copy_script_evidence(
    tmp_path: Path,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )

    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="reviewed_copy_script",
        model_visible=True,
        camera_matches_manifest=True,
    )

    assert recorded["accepted"] is False
    assert "reviewed_copy_script_evidence_missing" in recorded["rejection_reasons"]
    assert "reviewed_copy_script_exact_window_binding_missing" in recorded[
        "rejection_reasons"
    ]
    assert "reviewed_copy_script_screenshot_missing" in recorded[
        "rejection_reasons"
    ]
    assert backend.window.handle == 100


def test_record_view_replay_persists_safe_reviewed_copy_script_artifacts(
    tmp_path: Path,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    screenshot = tmp_path / "view_proj" / "outputs" / "r002" / "copy_script.bmp"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(_tiny_bmp())
    evidence = _reviewed_copy_script_evidence()

    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="reviewed_copy_script",
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=screenshot,
        expected_window_handle=backend.window.handle,
        expected_window_title=backend.window.title,
        reviewed_copy_script_evidence=evidence,
    )

    assert recorded["accepted"] is True
    event = recorded["event"]
    assert event["reviewed_copy_script_evidence_complete"] is True
    persisted = event["reviewed_copy_script_evidence"]
    assert persisted["execution_allowed"] is False
    assert persisted["raw_script_persisted"] is True
    integrity = event["evidence_integrity"]
    assert integrity["status"] == "verified"
    assert integrity["trusted_for_replay"] is True
    assert set(integrity["required_artifact_kinds"]) == {
        "screenshot",
        "copy_script",
        "copy_script_metadata",
        "structure",
    }
    assert all(item["status"] == "verified" for item in integrity["artifacts"])
    script_path = Path(persisted["script_path"])
    metadata_path = Path(persisted["metadata_path"])
    assert script_path.read_text(encoding="utf-8") == evidence["script_text"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["accepted"] is True
    assert metadata["event_id"] == event["event_id"]
    assert metadata["evidence"]["script_sha256"] == persisted["script_sha256"]


@pytest.mark.parametrize(
    "artifact_kind",
    ["screenshot", "copy_script", "copy_script_metadata", "structure"],
)
def test_refresh_view_replay_invalidates_drifted_reviewed_evidence(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    controller.prepare_view_replay(audit, project_id="view_proj", revision=2)
    screenshot = tmp_path / "view_proj" / "outputs" / "r002" / "copy_script.bmp"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(_tiny_bmp())

    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="reviewed_copy_script",
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=screenshot,
        expected_window_handle=backend.window.handle,
        expected_window_title=backend.window.title,
        reviewed_copy_script_evidence=_reviewed_copy_script_evidence(),
    )
    assert recorded["accepted"] is True
    artifacts = {
        item["kind"]: item
        for item in recorded["event"]["evidence_integrity"]["artifacts"]
    }
    Path(artifacts[artifact_kind]["path"]).write_bytes(
        f"drifted-{artifact_kind}".encode("ascii")
    )

    refreshed = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )

    manifest = refreshed["manifest"]
    event = manifest["replay_events"][0]
    integrity = event["evidence_integrity"]
    assert event["accepted"] is True
    assert integrity["status"] == "invalid"
    assert integrity["trusted_for_replay"] is False
    assert f"evidence_integrity_sha256_mismatch:{artifact_kind}" in integrity[
        "issue_codes"
    ]
    summary = manifest["replay_summary"]
    assert summary["raw_accepted_event_count"] == 1
    assert summary["accepted_event_count"] == 0
    assert summary["accepted_view_count"] == 0
    assert summary["integrity_blocked_view_names"] == ["crystal_100"]
    if artifact_kind == "structure":
        assert refreshed["replay_status"] == "blocked"
        assert "target_revision_not_loaded_in_gui" in refreshed[
            "preflight_block_reasons"
        ]
        assert refreshed["replay_continuation"]["status"] == (
            "preflight_blocked"
        )
        assert refreshed["replay_continuation"][
            "automatic_replay_ready"
        ] is False
    else:
        assert refreshed["replay_status"] == (
            "evidence_integrity_reverification_required"
        )
        assert refreshed["replay_continuation"]["status"] == (
            "evidence_integrity_reverification_required"
        )
        assert refreshed["replay_continuation"][
            "automatic_replay_ready"
        ] is False
        assert refreshed["next_action"]["recommended_tool"] == (
            "material_studio_gui_copy_script_assist"
        )
        assert refreshed["next_action_resolution"]["status"] == (
            "continuation_safety_override_applied"
        )
        assert refreshed["next_action_resolution"]["safety_gate"][
            "external_review_required"
        ] is True
    persisted_event = json.loads(
        Path(recorded["events_path"]).read_text(encoding="utf-8").splitlines()[0]
    )
    assert persisted_event["accepted"] is True
    assert persisted_event["evidence_integrity"]["status"] == "verified"
    if artifact_kind == "copy_script":
        rerecorded = controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="reviewed_copy_script",
            model_visible=True,
            camera_matches_manifest=True,
            screenshot_path=screenshot,
            expected_window_handle=backend.window.handle,
            expected_window_title=backend.window.title,
            reviewed_copy_script_evidence=_reviewed_copy_script_evidence(),
        )
        restored_summary = rerecorded["replay_summary"]
        assert rerecorded["accepted"] is True
        assert rerecorded["replay_status"] == "externally_confirmed"
        assert restored_summary["raw_accepted_event_count"] == 2
        assert restored_summary["accepted_event_count"] == 1
        assert restored_summary["accepted_view_count"] == 1
        assert restored_summary["integrity_blocked_view_names"] == []
        assert restored_summary["evidence_integrity_status"] == (
            "verified_with_historical_drift"
        )


def test_refresh_view_replay_invalidates_drifted_computer_use_screenshot(
    tmp_path: Path,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    controller.prepare_view_replay(audit, project_id="view_proj", revision=2)
    screenshot = tmp_path / "view_proj" / "outputs" / "r002" / "computer_use.bmp"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(_tiny_bmp())
    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="computer_use",
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=screenshot,
        expected_window_handle=backend.window.handle,
        expected_window_title=backend.window.title,
    )
    assert recorded["accepted"] is True
    assert recorded["event"]["evidence_integrity"]["policy"] == (
        "recorded_screenshot_strict"
    )

    screenshot.write_bytes(b"drifted-computer-use-screenshot")
    refreshed = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )

    assert refreshed["manifest"]["replay_summary"][
        "integrity_blocked_view_names"
    ] == ["crystal_100"]
    assert refreshed["manifest"]["last_replay_event"]["evidence_integrity"][
        "trusted_for_replay"
    ] is False


@pytest.mark.parametrize(
    "drift_kind, expected_issue",
    [
        (
            "manifest_mutation",
            "manifest_journal_event_digest_mismatch",
        ),
        (
            "journal_mutation",
            "manifest_journal_event_digest_mismatch",
        ),
        ("journal_deleted", "journal_event_missing"),
        ("journal_duplicate", "journal_event_duplicate"),
    ],
)
def test_refresh_view_replay_invalidates_manifest_journal_divergence(
    tmp_path: Path,
    drift_kind: str,
    expected_issue: str,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    prepared = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )
    screenshot = tmp_path / "view_proj" / "outputs" / "r002" / "journal.bmp"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(_tiny_bmp())
    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="reviewed_copy_script",
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=screenshot,
        expected_window_handle=backend.window.handle,
        expected_window_title=backend.window.title,
        reviewed_copy_script_evidence=_reviewed_copy_script_evidence(),
    )
    assert recorded["accepted"] is True
    assert recorded["event"]["journal_consistency"]["status"] == "matched"
    assert recorded["event_journal"]["consistency_status"] == "consistent"

    manifest_path = Path(prepared["manifest_path"])
    events_path = Path(recorded["events_path"])
    original_journal = events_path.read_text(encoding="utf-8")
    if drift_kind == "manifest_mutation":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["replay_events"][0]["note"] = "mutated manifest note"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif drift_kind == "journal_mutation":
        journal_event = json.loads(original_journal.splitlines()[0])
        journal_event["note"] = "mutated journal note"
        events_path.write_text(
            json.dumps(journal_event, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    elif drift_kind == "journal_deleted":
        events_path.unlink()
    else:
        events_path.write_text(
            original_journal + original_journal,
            encoding="utf-8",
        )

    refreshed = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )

    manifest = refreshed["manifest"]
    event = manifest["replay_events"][0]
    assert event["accepted"] is True
    assert event["evidence_integrity"]["trusted_for_replay"] is True
    assert event["journal_consistency"]["trusted_for_replay"] is False
    assert expected_issue in event["journal_consistency"]["issue_codes"]
    summary = manifest["replay_summary"]
    assert summary["accepted_view_count"] == 0
    assert summary["integrity_blocked_view_names"] == []
    assert summary["journal_blocked_view_names"] == ["crystal_100"]
    assert summary["journal_consistency_status"] == "blocked"
    assert refreshed["replay_status"] == "event_journal_reverification_required"

    if drift_kind == "journal_deleted":
        rerecorded = controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="reviewed_copy_script",
            model_visible=True,
            camera_matches_manifest=True,
            screenshot_path=screenshot,
            expected_window_handle=backend.window.handle,
            expected_window_title=backend.window.title,
            reviewed_copy_script_evidence=_reviewed_copy_script_evidence(),
        )
        restored_summary = rerecorded["replay_summary"]
        assert rerecorded["accepted"] is True
        assert rerecorded["replay_status"] == "externally_confirmed"
        assert restored_summary["accepted_view_count"] == 1
        assert restored_summary["journal_blocked_view_names"] == []
        assert restored_summary["journal_consistency_status"] == (
            "consistent_with_historical_divergence"
        )


def test_refresh_view_replay_reports_unrelated_invalid_journal_line(
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    controller.prepare_view_replay(audit, project_id="view_proj", revision=2)
    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="computer_use",
        model_visible=True,
        camera_matches_manifest=True,
    )
    events_path = Path(recorded["events_path"])
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    refreshed = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )

    assert refreshed["replay_status"] == "externally_confirmed"
    assert refreshed["manifest"]["replay_summary"]["accepted_view_count"] == 1
    journal = refreshed["event_journal"]
    assert journal["status"] == "invalid_lines"
    assert journal["invalid_line_count"] == 1
    assert journal["invalid_line_numbers"] == [2]
    assert journal["consistency_status"] == (
        "consistent_with_historical_divergence"
    )


def test_record_view_replay_journals_before_manifest_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    prepared = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )
    manifest_path = Path(prepared["manifest_path"])
    manifest_before = manifest_path.read_bytes()

    def fail_manifest_publish(_path: Path, _payload: dict) -> None:
        raise OSError("simulated manifest publish failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(gui_module, "_write_json_atomic", fail_manifest_publish)
        with pytest.raises(OSError, match="manifest publish failure"):
            controller.record_view_replay(
                project_id="view_proj",
                revision=2,
                view_name="crystal_100",
                source="computer_use",
                model_visible=True,
                camera_matches_manifest=True,
            )

    events_path = manifest_path.with_name("gui_view_replay_events.jsonl")
    journal_event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
    assert len(journal_event["event_record_sha256"]) == 64
    assert manifest_path.read_bytes() == manifest_before

    refreshed = controller.prepare_view_replay(
        audit,
        project_id="view_proj",
        revision=2,
    )
    assert refreshed["manifest"]["replay_events"] == []
    assert refreshed["event_journal"]["journal_only_event_count"] == 1
    assert refreshed["event_journal"]["consistency_status"] == (
        "consistent_with_historical_divergence"
    )


def test_view_replay_write_lock_serializes_concurrent_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    prepared = controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    original_append = gui_module._append_view_replay_event_journal
    first_append_started = threading.Event()
    release_first_append = threading.Event()
    contention_observed = threading.Event()
    call_count = 0
    call_count_lock = threading.Lock()
    original_lock_attempt = gui_module._lock_file_descriptor_nonblocking

    def delayed_first_append(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_append_started.set()
            assert release_first_append.wait(timeout=5.0)
        original_append(*args, **kwargs)

    def observed_lock_attempt(file_descriptor: int) -> None:
        try:
            original_lock_attempt(file_descriptor)
        except OSError:
            contention_observed.set()
            raise

    monkeypatch.setattr(
        gui_module,
        "_append_view_replay_event_journal",
        delayed_first_append,
    )
    monkeypatch.setattr(
        gui_module,
        "_lock_file_descriptor_nonblocking",
        observed_lock_attempt,
    )
    record_kwargs = {
        "project_id": "view_proj",
        "revision": 2,
        "view_name": "crystal_100",
        "source": "computer_use",
        "model_visible": True,
        "camera_matches_manifest": True,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            controller.record_view_replay,
            **record_kwargs,
            note="first concurrent event",
        )
        assert first_append_started.wait(timeout=5.0)
        second = executor.submit(
            controller.record_view_replay,
            **record_kwargs,
            note="second concurrent event",
        )
        assert contention_observed.wait(timeout=5.0)
        assert second.done() is False
        release_first_append.set()
        first_result = first.result(timeout=5.0)
        second_result = second.result(timeout=5.0)

    manifest = json.loads(Path(prepared["manifest_path"]).read_text(encoding="utf-8"))
    journal_events = [
        json.loads(line)
        for line in Path(first_result["events_path"]).read_text(encoding="utf-8").splitlines()
    ]
    manifest_ids = [event["event_id"] for event in manifest["replay_events"]]
    journal_ids = [event["event_id"] for event in journal_events]
    assert len(manifest_ids) == 2
    assert len(set(manifest_ids)) == 2
    assert journal_ids == manifest_ids
    assert second_result["replay_summary"]["event_count"] == 2
    assert second_result["replay_summary"]["accepted_event_count"] == 2
    assert second_result["event_journal"]["journal_matched_event_count"] == 2
    assert second_result["event_journal"]["consistency_status"] == "consistent"
    assert first_result["write_transaction"]["path"] == second_result[
        "write_transaction"
    ]["path"]
    assert second_result["write_transaction"]["waited_seconds"] > 0.0


def test_view_replay_prepare_waits_for_record_and_preserves_the_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    audit = _view_replay_audit("view_proj", 2)
    controller.prepare_view_replay(audit, project_id="view_proj", revision=2)
    original_append = gui_module._append_view_replay_event_journal
    append_started = threading.Event()
    release_append = threading.Event()
    contention_observed = threading.Event()
    original_lock_attempt = gui_module._lock_file_descriptor_nonblocking

    def delayed_append(*args: object, **kwargs: object) -> None:
        append_started.set()
        assert release_append.wait(timeout=5.0)
        original_append(*args, **kwargs)

    def observed_lock_attempt(file_descriptor: int) -> None:
        try:
            original_lock_attempt(file_descriptor)
        except OSError:
            contention_observed.set()
            raise

    monkeypatch.setattr(
        gui_module,
        "_append_view_replay_event_journal",
        delayed_append,
    )
    monkeypatch.setattr(
        gui_module,
        "_lock_file_descriptor_nonblocking",
        observed_lock_attempt,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        recorded_future = executor.submit(
            controller.record_view_replay,
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="computer_use",
            model_visible=True,
            camera_matches_manifest=True,
        )
        assert append_started.wait(timeout=5.0)
        prepared_future = executor.submit(
            controller.prepare_view_replay,
            audit,
            project_id="view_proj",
            revision=2,
        )
        assert contention_observed.wait(timeout=5.0)
        assert prepared_future.done() is False
        release_append.set()
        recorded_future.result(timeout=5.0)
        refreshed = prepared_future.result(timeout=5.0)

    assert refreshed["manifest"]["preserved_replay_event_count"] == 1
    assert refreshed["manifest"]["replay_summary"]["event_count"] == 1
    assert refreshed["manifest"]["replay_summary"]["accepted_event_count"] == 1
    assert refreshed["event_journal"]["journal_matched_event_count"] == 1
    assert refreshed["event_journal"]["consistency_status"] == "consistent"
    assert refreshed["write_transaction"]["waited_seconds"] > 0.0


def test_view_replay_write_lock_times_out_without_partial_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    prepared = controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    manifest_path = Path(prepared["manifest_path"])
    lock_path = manifest_path.with_name("gui_view_replay_transaction.lock")
    monkeypatch.setattr(gui_module, "VIEW_REPLAY_WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(gui_module, "VIEW_REPLAY_WRITE_LOCK_POLL_SECONDS", 0.005)

    with gui_module._view_replay_write_lock(
        lock_path,
        workspace_root=tmp_path,
        timeout_seconds=1.0,
    ):
        with pytest.raises(GuiError, match="write transaction is busy"):
            controller.record_view_replay(
                project_id="view_proj",
                revision=2,
                view_name="crystal_100",
                source="computer_use",
                model_visible=True,
                camera_matches_manifest=True,
            )

    events_path = manifest_path.with_name("gui_view_replay_events.jsonl")
    assert events_path.exists() is False
    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="computer_use",
        model_visible=True,
        camera_matches_manifest=True,
    )
    assert recorded["accepted"] is True
    assert recorded["event_journal"]["journal_matched_event_count"] == 1


def test_record_view_replay_blocks_and_does_not_persist_unsafe_copy_script_text(
    tmp_path: Path,
) -> None:
    controller, backend = _controller_with_verified_project_window(tmp_path)
    controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    screenshot = tmp_path / "view_proj" / "outputs" / "r002" / "unsafe.bmp"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(_tiny_bmp())
    evidence = _reviewed_copy_script_evidence(
        'system("cmd /c whoami");\n$doc->CreateAtom("Si", Point(X => 0));\n'
    )

    recorded = controller.record_view_replay(
        project_id="view_proj",
        revision=2,
        view_name="crystal_100",
        source="reviewed_copy_script",
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=screenshot,
        expected_window_handle=backend.window.handle,
        expected_window_title=backend.window.title,
        reviewed_copy_script_evidence=evidence,
    )

    assert recorded["accepted"] is False
    assert "reviewed_copy_script_safety_blocked" in recorded["rejection_reasons"]
    persisted = recorded["event"]["reviewed_copy_script_evidence"]
    assert persisted["raw_script_persisted"] is False
    assert persisted["script_path"] is None
    assert Path(persisted["metadata_path"]).is_file()


def test_record_view_replay_rejects_copy_script_payload_for_other_sources(
    tmp_path: Path,
) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )

    with pytest.raises(GuiError, match="allowed only"):
        controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="computer_use",
            model_visible=True,
            camera_matches_manifest=True,
            reviewed_copy_script_evidence=_reviewed_copy_script_evidence(),
        )


def test_record_view_replay_rejects_screenshot_outside_workspace(tmp_path: Path) -> None:
    controller, _ = _controller_with_verified_project_window(tmp_path)
    controller.prepare_view_replay(
        _view_replay_audit("view_proj", 2),
        project_id="view_proj",
        revision=2,
    )
    outside = tmp_path.parent / "outside_view_replay.bmp"
    outside.write_bytes(_tiny_bmp())

    with pytest.raises(GuiError, match="工作区"):
        controller.record_view_replay(
            project_id="view_proj",
            revision=2,
            view_name="crystal_100",
            source="computer_use",
            model_visible=True,
            camera_matches_manifest=True,
            screenshot_path=outside,
        )


def _tiny_bmp() -> bytes:
    width = 2
    height = 2
    bits_per_pixel = 24
    row_stride = 8
    pixel_offset = 54
    image_size = row_stride * height
    file_size = pixel_offset + image_size
    header = b"BM" + file_size.to_bytes(4, "little") + b"\x00\x00\x00\x00" + pixel_offset.to_bytes(4, "little")
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + bits_per_pixel.to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + image_size.to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    # BGR pixels, rows padded to 4 bytes.
    bottom_row = bytes([0, 0, 0, 255, 255, 255, 0, 0])
    top_row = bytes([0, 0, 255, 0, 255, 0, 0, 0])
    return header + dib + bottom_row + top_row


def _materials_studio_like_bmp(*, model_visible: bool, sparse: bool = False) -> bytes:
    width = 400
    height = 300
    bits_per_pixel = 24
    row_stride = ((width * bits_per_pixel + 31) // 32) * 4
    pixel_offset = 54
    image_size = row_stride * height
    file_size = pixel_offset + image_size
    header = b"BM" + file_size.to_bytes(4, "little") + b"\x00\x00\x00\x00" + pixel_offset.to_bytes(4, "little")
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + bits_per_pixel.to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + image_size.to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    rows: list[bytes] = []
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            red, green, blue = _materials_studio_like_pixel(x, y, model_visible=model_visible, sparse=sparse)
            row.extend([blue, green, red])
        row.extend(b"\x00" * (row_stride - width * 3))
        rows.append(bytes(row))
    return header + dib + b"".join(rows)


def _materials_studio_like_pixel(x: int, y: int, *, model_visible: bool, sparse: bool = False) -> tuple[int, int, int]:
    if y < 55:
        return (230, 230, 230) if (x // 20) % 2 else (180, 210, 245)
    if x < 50:
        return (245, 245, 245) if (y // 18) % 2 else (185, 205, 225)
    if model_visible and sparse and 196 <= x <= 207 and 151 <= y <= 162:
        palette = [(230, 30, 30), (245, 245, 245), (240, 220, 20), (20, 190, 190)]
        return palette[((x // 4) + (y // 4)) % len(palette)]
    if model_visible and not sparse and 190 <= x <= 214 and 145 <= y <= 169:
        return (230, 30, 30) if (x + y) % 2 else (245, 245, 245)
    return (0, 0, 0)
