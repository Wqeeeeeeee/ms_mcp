from __future__ import annotations

import hashlib
import json
from pathlib import Path

from material_studio_mcp_server import server
import material_studio_mcp_server.gui as gui_module
from material_studio_mcp_server.gui import MaterialsStudioGuiController, ProcessInfo, WindowInfo
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore


def _tiny_bmp() -> bytes:
    width = height = 2
    row_stride = 8
    pixel_data = b"\x30\x30\x30\x30\x30\x30\x30\x30" * height
    file_size = 54 + len(pixel_data)
    header = bytearray(54)
    header[0:2] = b"BM"
    header[2:6] = file_size.to_bytes(4, "little")
    header[10:14] = (54).to_bytes(4, "little")
    header[14:18] = (40).to_bytes(4, "little")
    header[18:22] = width.to_bytes(4, "little", signed=True)
    header[22:26] = height.to_bytes(4, "little", signed=True)
    header[26:28] = (1).to_bytes(2, "little")
    header[28:30] = (24).to_bytes(2, "little")
    header[34:38] = len(pixel_data).to_bytes(4, "little")
    assert row_stride * height == len(pixel_data)
    return bytes(header) + pixel_data


def _command_evidence() -> dict:
    contract = gui_module.VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS["3D Viewer"]
    commands = [dict(item) for item in gui_module.MATERIALS_STUDIO_2020_VIEW_COMMANDS]
    return {
        "registry_found": True,
        "registry_path": r"C:\Materials Studio\#SVViewer3d.xml",
        "registry_sha256": "a" * 64,
        "registry_toolbar_parse_error": None,
        "registered_view_command_ids": [item["command_id"] for item in commands],
        "registry_toolbar_layouts": [
            {
                "registry_toolbar_name": contract["registry_toolbar_name"],
                "title": "3D Viewer",
                "entries": [
                    {"kind": kind, "command_id": command_id}
                    for kind, command_id in contract["entries"]
                ],
            }
        ],
    }


class _GuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=101,
            title="Untitled - Materials Studio",
            pid=202,
            rect=(0, 0, 800, 600),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )
        self.extra_window: WindowInfo | None = None
        self.captured: list[Path] = []

    def list_processes(self) -> list[ProcessInfo]:
        processes = [ProcessInfo(name="MatStudio.exe", pid=self.window.pid or 202)]
        if self.extra_window is not None:
            processes.append(ProcessInfo(name="MatStudio.exe", pid=self.extra_window.pid or 303))
        return processes

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        windows = [self.window]
        if self.extra_window is not None:
            windows.append(self.extra_window)
        if pid is not None:
            return [item for item in windows if item.pid == pid]
        return windows

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and self.window.pid != pid:
            return None
        return self.window

    def activate_window(self, window: WindowInfo) -> bool:
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        assert window.handle == self.window.handle
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        self.captured.append(output_path)
        return output_path

    def open_file(self, path: Path) -> dict:
        raise AssertionError("Fit-to-View must not open a file")

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        raise AssertionError("Fit-to-View must not open a file")

    def launch_app(self) -> dict:
        raise AssertionError("Fit-to-View must not launch Materials Studio")


class _ReplayBackend:
    supported = True
    unavailable_reason = None

    def __init__(self) -> None:
        self.probe_calls: list[dict] = []
        self.execute_calls: list[dict] = []

    def probe(self, **kwargs: object) -> dict:
        self.probe_calls.append(dict(kwargs))
        command_labels = kwargs["command_labels"]
        assert command_labels == {
            "cmdViewer3DFitToView": "3D Viewer Fit to View"
        }
        return {
            "supported": True,
            "safe_for_standard_view_replay": True,
            "resolved_command_ids": ["cmdViewer3DFitToView"],
            "block_reasons": [],
            "viewport": {
                "class_name": "CViewer3DCtrl",
                "enabled": True,
                "visible": True,
            },
        }

    def execute_fit_to_view(self, **kwargs: object) -> dict:
        self.execute_calls.append(dict(kwargs))
        assert kwargs["registry_sha256"] == "a" * 64
        return {
            "kind": "materials_studio_local_uia_fit_to_view",
            "command_id": "cmdViewer3DFitToView",
            "execution_succeeded": True,
            "gui_input_performed": True,
            "gui_modified": True,
            "structure_modified": False,
            "fit_command": {
                "target_kind": "verified_anonymous_toolbar_child",
                "invocation_method": "local_uia_invoke_pattern",
            },
        }


def _controller(tmp_path: Path) -> tuple[MaterialsStudioGuiController, _GuiBackend, _ReplayBackend, Path]:
    backend = _GuiBackend()
    replay = _ReplayBackend()
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=backend,
        view_replay_backend=replay,
    )
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = controller._create_project_wrapper(
        structure,
        project_id="fit_project",
        revision=2,
    )
    backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=202,
        rect=(0, 0, 800, 600),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    return controller, backend, replay, structure


def test_fit_to_view_preview_is_read_only_and_uses_fresh_fit_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, _structure = _controller(tmp_path)

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["execution_ready"] is True
    assert result["gui_input_performed"] is False
    assert result["structure_modified"] is False
    assert result["confirmation_action"]["payload"]["execution_mode"] == "execute"
    assert len(replay.probe_calls) == 1
    assert replay.execute_calls == []
    assert backend.captured == []
    assert not (tmp_path / "fit_project" / "gui_actions.jsonl").exists()


def test_fit_to_view_execute_captures_evidence_and_preserves_structure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, structure = _controller(tmp_path)
    before_hash = hashlib.sha256(structure.read_bytes()).hexdigest()

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=True,
    )

    assert result["status"] == "executed"
    assert result["action_receipt"]["execution_succeeded"] is True
    assert result["gui_input_performed"] is True
    assert result["gui_modified"] is True
    assert result["structure_unchanged"] is True
    assert result["structure_sha256_before"] == before_hash
    assert result["structure_sha256_after"] == before_hash
    assert result["before_snapshot"]["analysis"]["readable"] is True
    assert result["after_snapshot"]["analysis"]["readable"] is True
    assert len(backend.captured) == 2
    assert len(replay.execute_calls) == 1
    log_path = Path(result["gui_log_path"])
    assert log_path.exists()
    assert "fit_to_view" in log_path.read_text(encoding="utf-8")


def test_fit_to_view_refuses_multiple_matstudio_processes_before_probe_or_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, _structure = _controller(tmp_path)
    backend.extra_window = WindowInfo(
        handle=303,
        title="extra - Materials Studio",
        pid=303,
        rect=(0, 0, 600, 400),
        is_visible=True,
        is_minimized=False,
        is_foreground=False,
    )

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=True,
    )

    assert result["status"] == "blocked"
    assert result["execution_ready"] is False
    assert "exactly_one_matstudio_process_required" in result["preflight"]["block_reasons"]
    assert replay.probe_calls == []
    assert replay.execute_calls == []
    assert backend.captured == []


def test_mcp_fit_to_view_defaults_preview_and_persists_execute_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    spec_payload = json.loads(
        Path("src/material_studio_mcp_server/examples/benzene_spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec = ModelSpec.model_validate(
        {
            **spec_payload,
            "project_id": "fit_server_project",
            "revision": 0,
        }
    )
    ProjectStore(tmp_path).create_project(spec)
    controller, backend, replay, structure = _controller(tmp_path)
    wrapper = controller._create_project_wrapper(
        structure,
        project_id="fit_server_project",
        revision=0,
    )
    backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=202,
        rect=(0, 0, 800, 600),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: controller,
    )

    preview = server.material_studio_gui_fit_to_view(
        project_id="fit_server_project",
        working_dir=str(tmp_path),
    )
    assert preview["ok"] is True
    assert preview["execution_mode"] == "preview"
    assert preview["gui_input_performed"] is False
    assert replay.execute_calls == []

    executed = server.material_studio_gui_fit_to_view(
        project_id="fit_server_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )
    assert executed["ok"] is True
    assert executed["status"] == "executed"
    assert executed["structure_unchanged"] is True
    assert executed["report_write_transaction"]["domain"] == "gui_artifact_report"
    assert Path(executed["report_json_path"]).exists()
    assert len(replay.execute_calls) == 1
