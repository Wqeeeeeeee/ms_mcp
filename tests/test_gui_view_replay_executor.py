from __future__ import annotations

import json
from pathlib import Path

import material_studio_mcp_server.gui as gui_module
from material_studio_mcp_server.gui import (
    MaterialsStudioGuiController,
    ProcessInfo,
    WindowInfo,
)


class _GuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=100,
            title="Untitled - Materials Studio",
            pid=1234,
            rect=(0, 0, 800, 600),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )
        self.captured: list[Path] = []

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=1234)]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and pid != self.window.pid:
            return []
        return [self.window]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and pid != self.window.pid:
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
        raise AssertionError("view replay must not open a file")

    def open_file_in_existing_window(
        self, window: WindowInfo, path: Path
    ) -> dict:
        raise AssertionError("view replay must not open a file")

    def launch_app(self) -> dict:
        raise AssertionError("view replay must not launch Materials Studio")


class _ReplayBackend:
    supported = True
    unavailable_reason = None

    def __init__(self) -> None:
        self.execute_calls: list[dict] = []

    def probe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict,
        command_labels: dict,
    ) -> dict:
        assert window_handle == 100
        return {
            "supported": True,
            "safe_for_standard_view_replay": True,
            "window": {
                "handle": window_handle,
                "title": expected_window_title,
            },
            "resolved_command_ids": sorted(command_labels),
            "toolbars": [],
            "viewport": {
                "class_name": "CViewer3DCtrl",
                "keyboard_focusable": True,
                "enabled": True,
                "visible": True,
            },
            "block_reasons": [],
            "evidence": {
                "source": "local_uia",
                "expected_revision": expected_revision,
                "expected_window_handle": window_handle,
                "expected_window_title": expected_window_title,
                "accessibility_tree_refreshed": True,
                "viewer_document_observed": True,
                "empty_viewport_focus_target_observed": False,
                "semantic_viewport_focus_supported": True,
                "unnamed_toolbar_children_observed": False,
                "controls": [
                    {
                        "command_id": "cmdViewer3DResetView",
                        "observed_control_name": "3D Viewer Reset View",
                        "invoke_supported": True,
                    },
                    {
                        "command_id": "cmdViewer3DMovementOptions",
                        "observed_control_name": "3D Movement Options",
                        "invoke_supported": True,
                    },
                ],
                "anonymous_toolbars": [],
                "screenshot_path": None,
                "note": "fake local UIA probe",
            },
        }

    def execute_standard_recipe(self, **kwargs: object) -> dict:
        self.execute_calls.append(dict(kwargs))
        recipe = kwargs["execution_recipe"]
        assert isinstance(recipe, dict)
        return {
            "schema_version": 1,
            "kind": "materials_studio_local_uia_view_replay_execution",
            "view_name": recipe["view_name"],
            "execution_succeeded": True,
            "reset_invocation_succeeded": True,
            "keyboard_focus_verified": True,
            "key_sequence_sent": list(recipe.get("key_sequence") or []),
            "modifier_keys": [],
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "visual_acceptance_recorded": False,
            "reset_command": {
                "accessibility_tree_refreshed": True,
                "invocation_method": "local_uia_invoke_pattern",
            },
            "post_action_observation_required": True,
            "record_call_ready": False,
        }


def _command_evidence() -> dict:
    return {
        "registry_found": True,
        "registry_path": "C:\\Materials Studio\\#SVViewer3d.xml",
        "registry_sha256": "a" * 64,
        "registry_toolbar_parse_error": None,
        "registry_toolbar_layouts": [],
        "commands": [
            {
                "action": "reset_view",
                "command_id": "cmdViewer3DResetView",
                "label": "3D Viewer Reset View",
            },
            {
                "action": "movement_options",
                "command_id": "cmdViewer3DMovementOptions",
                "label": "3D Movement Options",
            },
        ],
        "registered_view_command_ids": [
            "cmdViewer3DResetView",
            "cmdViewer3DMovementOptions",
        ],
        "keyboard_help_found": True,
        "keyboard_help_path": "C:\\Materials Studio\\keyboard.htm",
        "unmodified_arrow_keys_rotate_view": True,
        "default_arrow_rotation_increment_degrees": 45,
        "shift_arrow_keys_rotate_selected_objects": True,
        "movement_help_found": True,
        "movement_dialog_angle_supported": True,
        "movement_options_command_registered": True,
    }


def _top_audit() -> dict:
    return {
        "project_id": "view_proj",
        "revision": 2,
        "model_type": "crystal",
        "spec_fingerprint": "abc123",
        "views": [
            {
                "name": "top",
                "supported": True,
                "coordinate_system": "cartesian",
                "camera_direction": [0.0, 1.0, 0.0],
                "camera_up": [0.0, 0.0, 1.0],
                "camera_right": [1.0, 0.0, 0.0],
                "look_at_direction": [0.0, -1.0, 0.0],
                "camera_position": [0.0, 10.0, 0.0],
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
                "projection_bbox_angstrom": {
                    "x": [-2.0, 2.0],
                    "y": [-2.0, 2.0],
                    "depth": [-2.0, 2.0],
                },
                "projection_span_angstrom": {
                    "x": 4.0,
                    "y": 4.0,
                    "depth": 4.0,
                },
                "overlap_candidates": [],
                "health": {"ok": True, "warnings": []},
            }
        ],
    }


def _controller(tmp_path: Path) -> tuple[
    MaterialsStudioGuiController, _GuiBackend, _ReplayBackend
]:
    gui_backend = _GuiBackend()
    replay_backend = _ReplayBackend()
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=gui_backend,
        view_replay_backend=replay_backend,
    )
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")
    wrapper = controller._create_project_wrapper(
        structure,
        project_id="view_proj",
        revision=2,
    )
    gui_backend.window = WindowInfo(
        handle=100,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=1234,
        rect=(0, 0, 800, 600),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    return controller, gui_backend, replay_backend


def test_preview_probes_but_does_not_write_or_execute(monkeypatch, tmp_path: Path) -> None:
    controller, _gui_backend, replay_backend = _controller(tmp_path)
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _command_evidence,
    )
    output_dir = tmp_path / "view_proj" / "outputs" / "r002"

    result = controller.run_view_replay(
        _top_audit(),
        project_id="view_proj",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["selected_view_name"] == "top"
    assert result["execution_ready"] is True
    assert result["gui_input_performed"] is False
    assert result["manifest_modified"] is False
    assert result["visual_acceptance_recorded"] is False
    assert replay_backend.execute_calls == []
    assert not (output_dir / "gui_view_replay_manifest.json").exists()
    assert not (output_dir / "gui_view_replay_accessibility_preflight.json").exists()


def test_execute_persists_mechanical_receipt_but_not_visual_acceptance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, gui_backend, replay_backend = _controller(tmp_path)
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _command_evidence,
    )

    result = controller.run_view_replay(
        _top_audit(),
        project_id="view_proj",
        revision=2,
        execution_mode="execute",
    )

    assert result["status"] == "awaiting_visual_confirmation"
    assert result["execution_succeeded"] is True
    assert result["gui_input_performed"] is True
    assert result["gui_modified"] is True
    assert result["structure_modified"] is False
    assert result["structure_unchanged"] is True
    assert result["manifest_modified"] is True
    assert result["visual_acceptance_recorded"] is False
    assert result["acceptance_event_created"] is False
    assert result["record_call_ready"] is False
    assert len(replay_backend.execute_calls) == 1
    assert len(gui_backend.captured) == 1
    template = result["post_action_record_payload_template"]
    assert template["source"] == "local_gui_fallback"
    assert template["model_visible"] is None
    assert template["camera_matches_manifest"] is None
    assert template["key_sequence"] == ["Up", "Up"]
    assert template["modifier_keys"] == []
    assert template["crystal_camera_evidence"][
        "view_direction_matches_manifest"
    ] is None
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["replay_events"] == []
    assert not Path(result["manifest_path"]).with_name(
        "gui_view_replay_events.jsonl"
    ).exists()


def _tiny_bmp() -> bytes:
    width = 2
    height = 2
    bits_per_pixel = 24
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
        + bits_per_pixel.to_bytes(2, "little")
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
