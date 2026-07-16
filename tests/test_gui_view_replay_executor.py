from __future__ import annotations

import hashlib
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
        keyboard_stages = recipe.get("keyboard_stages")
        staged_receipts = (
            [
                {
                    "rotation_increment_degrees": stage[
                        "rotation_increment_degrees"
                    ],
                    "rotation_increment_ui_display_degrees": stage.get(
                        "rotation_increment_ui_display_degrees",
                        stage["rotation_increment_degrees"],
                    ),
                    "angle_readback_degrees": stage.get(
                        "rotation_increment_ui_display_degrees",
                        stage["rotation_increment_degrees"],
                    ),
                    "screen_factor_readback": 2.0,
                    "key_sequence": list(stage["key_sequence"]),
                    "modifier_keys": [],
                }
                for stage in keyboard_stages
            ]
            if isinstance(keyboard_stages, list)
            else None
        )
        flattened_keys = (
            [
                key
                for stage in staged_receipts or []
                for key in stage["key_sequence"]
            ]
            if staged_receipts is not None
            else list(recipe.get("key_sequence") or [])
        )
        return {
            "schema_version": 1,
            "kind": "materials_studio_local_uia_view_replay_execution",
            "view_name": recipe["view_name"],
            "execution_succeeded": True,
            "reset_invocation_succeeded": True,
            "keyboard_focus_verified": True,
            "key_sequence_sent": flattened_keys,
            "keyboard_stages": staged_receipts,
            "modifier_keys": [],
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "visual_acceptance_recorded": False,
            "reset_command": {
                "accessibility_tree_refreshed": True,
                "invocation_method": "local_uia_invoke_pattern",
            },
            "movement_options_command_id": (
                "cmdViewer3DMovementOptions"
                if staged_receipts is not None
                else None
            ),
            "movement_angle_control_id": (
                "numNudgeAngle" if staged_receipts is not None else None
            ),
            "movement_screen_factor_control_id": (
                "numNudgeFactor" if staged_receipts is not None else None
            ),
            "movement_screen_factor": (
                2.0 if staged_receipts is not None else None
            ),
            "rotation_increment_restored_degrees": (
                45.0 if staged_receipts is not None else None
            ),
            "movement_dialog_closed": (
                True if staged_receipts is not None else None
            ),
            "post_action_observation_required": True,
            "record_call_ready": False,
        }


class _MillerReplayBackend(_ReplayBackend):
    miller_plane_transaction_supported = True

    def probe(self, **kwargs: object) -> dict:
        result = super().probe(**kwargs)
        result["safe_for_standard_view_replay"] = False
        result["safe_for_miller_plane_transaction"] = True
        return result

    def execute_standard_recipe(self, **kwargs: object) -> dict:
        self.execute_calls.append(dict(kwargs))
        recipe = kwargs["execution_recipe"]
        assert isinstance(recipe, dict)
        assert recipe["recipe_kind"] == "miller_plane_view_onto"
        assert recipe["reset_view_allowed"] is False

        structure_path = Path(str(kwargs["structure_path"])).resolve()
        artifact_hash = hashlib.sha256(structure_path.read_bytes()).hexdigest()
        evidence_dir = Path(str(kwargs["evidence_dir"])).resolve()
        aligned_path = evidence_dir / "aligned_before_cleanup.bmp"
        aligned_path.write_bytes(_tiny_bmp())
        dialog_indices = list(recipe["dialog_miller_indices"])
        plane_indices = list(recipe["miller_plane_indices"])
        properties_label = str(recipe["properties_miller_label"])
        undo_labels = [
            "Undo View Onto Miller Plane",
            "Undo Create Miller Plane",
        ]
        viewport_probe = {
            "selection_method": gui_module.MILLER_PLANE_VIEWPORT_SELECTION_METHOD,
            "probe_miller_indices": plane_indices,
            "dialog_miller_indices": dialog_indices,
            "unique_transient_plane_visual_target_observed": True,
            "viewport_plane_selection_observed": True,
            "properties_selection_verified": True,
            "view_onto_popup_menu_observed": False,
            "view_onto_native_command_mapping_verified": True,
            "hit_test_basis": gui_module.MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
            "properties_filter": "Miller Plane",
            "properties_miller_label": properties_label,
            "view_onto_command_id": "cmdViewer3DViewOnto",
            "undo_labels_observed": undo_labels,
            "structure_artifact_path": str(structure_path),
            "structure_artifact_sha256_before": artifact_hash,
            "structure_artifact_sha256_after": artifact_hash,
        }
        runtime_ui_evidence = {
            "source": "local_uia",
            "expected_revision": int(kwargs["expected_revision"]),
            "expected_window_handle": int(kwargs["window_handle"]),
            "expected_window_title": str(kwargs["expected_window_title"]),
            "reset_view_control_observed": True,
            "tools_miller_planes_menu_observed": True,
            "miller_planes_keyboard_menu_path_verified": True,
            "miller_planes_dialog_observed": True,
            "miller_indices_control_observed": True,
            "create_button_observed": True,
            "tree_explorer_menu_observed": False,
            "properties_explorer_menu_observed": True,
            "view_onto_control_observed": True,
            "view_onto_native_command_mapping_verified": True,
            "pointer_menu_click_through_risk_observed": True,
            "unexpected_plane_created_during_probe": False,
            "unexpected_plane_cleanup_verified": True,
            "document_clean_before_probe": True,
            "document_clean_after_probe": True,
            "miller_planes_menu_key_sequence": ["Alt+T", "M"],
            "miller_planes_dialog_title": "Miller Planes",
            "miller_planes_dialog_control_id": "MillerPlanesCtl",
            "miller_indices_control_id": "TxtHKL",
            "create_button_control_id": "CmdCreate",
            "selection_modifier_keys": [],
            "viewport_selection_probe": viewport_probe,
            "screenshot_path": str(aligned_path),
            "note": "fake transactional Miller UI evidence",
        }
        miller_plane_evidence = {
            "miller_plane_indices": plane_indices,
            "dialog_miller_indices": dialog_indices,
            "dialog_miller_indices_text_before_create": " ".join(
                str(item) for item in dialog_indices
            ),
            "dialog_miller_indices_value_source": (
                "fresh_modeless_child_accessibility_value"
            ),
            "dialog_miller_indices_verified_before_create": True,
            "created_plane_count": 1,
            "selected_plane_count": 1,
            "miller_plane_count_before": 0,
            "miller_plane_count_after_create": 1,
            "miller_plane_count_after_cleanup": 0,
            "selection_method": gui_module.MILLER_PLANE_VIEWPORT_SELECTION_METHOD,
            "viewport_hit_test_basis": gui_module.MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
            "fresh_before_after_screenshots_observed": True,
            "unique_transient_plane_region_observed": True,
            "properties_selection_verified": True,
            "view_onto_popup_menu_observed": False,
            "view_onto_native_command_mapping_verified": True,
            "dialog_show_set_of_parallel_planes": False,
            "dialog_show_symmetry_images": False,
            "properties_filter": "Miller Plane",
            "properties_miller_label": properties_label,
            "camera_match_scope": "crystal_plane_normal_with_native_in_plane_roll",
            "plane_normal_matches_manifest": True,
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_policy_observed": True,
            "pre_action_view_baseline_captured": True,
            "reset_view_before_alignment": False,
            "screenshot_captured_before_cleanup": True,
            "document_was_clean_before_replay": True,
            "temporary_miller_plane_cleanup_verified": True,
            "no_temporary_miller_nodes_remaining": True,
            "document_clean_after_replay": True,
            "post_replay_view_restored": True,
            "structure_artifact_path": str(structure_path),
            "structure_artifact_sha256_before": artifact_hash,
            "structure_artifact_sha256_after": artifact_hash,
            "undo_labels_applied": undo_labels,
        }
        return {
            "schema_version": 1,
            "kind": "materials_studio_local_uia_miller_plane_transaction",
            "view_name": recipe["view_name"],
            "execution_succeeded": True,
            "gui_input_performed": True,
            "gui_transiently_modified": True,
            "coordinate_input_used": False,
            "pointer_input_used": True,
            "modifier_keys": [],
            "aligned_screenshot_path": str(aligned_path),
            "runtime_ui_evidence": runtime_ui_evidence,
            "miller_plane_evidence": miller_plane_evidence,
            "view_onto_native_command_mapping": {
                "verified": True,
                "numeric_command_id": 33297,
            },
            "visual_acceptance_recorded": False,
            "post_action_observation_required": True,
            "record_call_ready": False,
        }


class _FailedMillerReplayBackend(_MillerReplayBackend):
    def execute_standard_recipe(self, **kwargs: object) -> dict:
        self.execute_calls.append(dict(kwargs))
        return {
            "schema_version": 2,
            "kind": "materials_studio_local_uia_miller_plane_transaction",
            "view_name": "crystal_plane_001",
            "execution_succeeded": False,
            "failure_phase": "preflight",
            "error": "synthetic preflight failure",
            "gui_input_performed": False,
            "gui_transiently_modified": False,
            "pointer_input_used": False,
            "cleanup_succeeded": True,
            "manual_cleanup_required": False,
            "visual_acceptance_recorded": False,
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


def _miller_command_evidence() -> dict:
    evidence = _command_evidence()
    evidence.update(
        {
            "registered_view_command_ids": [
                "cmdViewer3DResetView",
                "cmdViewer3DMovementOptions",
                "cmdViewer3DViewOnto",
            ],
            "symmetry_builder_registry_found": True,
            "miller_plane_command_registered": True,
            "properties_explorer_registry_found": True,
            "properties_explorer_command_registered": True,
            "miller_plane_create_help_found": True,
            "miller_plane_create_workflow_verified": True,
            "miller_plane_working_help_found": True,
            "miller_plane_selection_view_onto_workflow_verified": True,
            "viewport_miller_plane_selection_properties_workflow_verified": True,
            "positioning_help_found": True,
            "native_view_roll_policy_documented": True,
        }
    )
    return evidence


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


def _isometric_audit() -> dict:
    audit = json.loads(json.dumps(_top_audit()))
    audit["views"][0]["name"] = "isometric"
    audit["views"][0]["camera_direction"] = [1.0, 1.0, 1.0]
    audit["views"][0]["camera_up"] = [-1.0, -1.0, 2.0]
    audit["views"][0]["camera_right"] = [-1.0, 1.0, 0.0]
    return audit


def _miller_audit() -> dict:
    audit = json.loads(json.dumps(_top_audit()))
    view = audit["views"][0]
    view.update(
        {
            "name": "crystal_plane_001",
            "coordinate_system": "crystal_reciprocal_plane_normal",
            "camera_direction": [0.0, 0.0, 1.0],
            "camera_up": [0.0, 1.0, 0.0],
            "camera_right": [1.0, 0.0, 0.0],
            "look_at_direction": [0.0, 0.0, -1.0],
            "crystal_plane_indices": [0, 0, 1],
            "crystal_plane_label": "(001)",
            "crystal_plane_normal_cartesian": [0.0, 0.0, 1.0],
            "crystal_plane_reciprocal_vector_per_angstrom": [0.0, 0.0, 1.0],
            "crystal_plane_reciprocal_convention": "without_2pi",
            "crystal_plane_spacing_angstrom": 1.0,
        }
    )
    return audit


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


def test_isometric_preview_is_ready_without_gui_input_or_persistence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, _gui_backend, replay_backend = _controller(tmp_path)
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _command_evidence,
    )
    output_dir = tmp_path / "view_proj" / "outputs" / "r002"

    result = controller.run_view_replay(
        _isometric_audit(),
        project_id="view_proj",
        revision=2,
        view_name="isometric",
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["selected_view_name"] == "isometric"
    assert result["execution_ready"] is True
    assert "isometric" in result["execution_supported_view_names"]
    assert result["plan"]["execution_recipe"]["keyboard_stages"] == [
        {
            "rotation_increment_degrees": 45.0,
            "key_sequence": ["Up", "Up", "Left", "Left", "Left"],
            "modifier_keys": [],
        },
        {
            "rotation_increment_degrees": 35.26438968,
            "rotation_increment_ui_display_degrees": 35.264,
            "key_sequence": ["Down"],
            "modifier_keys": [],
        },
    ]
    assert result["gui_input_performed"] is False
    assert result["manifest_modified"] is False
    assert replay_backend.execute_calls == []
    assert not (output_dir / "gui_view_replay_manifest.json").exists()


def test_isometric_execute_returns_complete_staged_mechanical_record_template(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, _gui_backend, replay_backend = _controller(tmp_path)
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _command_evidence,
    )

    result = controller.run_view_replay(
        _isometric_audit(),
        project_id="view_proj",
        revision=2,
        view_name="isometric",
        execution_mode="execute",
    )

    assert result["status"] == "awaiting_visual_confirmation"
    assert result["execution_succeeded"] is True
    assert result["structure_unchanged"] is True
    assert result["visual_acceptance_recorded"] is False
    assert len(replay_backend.execute_calls) == 1
    template = result["post_action_record_payload_template"]
    assert template["key_sequence"] is None
    assert template["keyboard_stages"] == [
        {
            "rotation_increment_degrees": 45.0,
            "key_sequence": ["Up", "Up", "Left", "Left", "Left"],
            "modifier_keys": [],
        },
        {
            "rotation_increment_degrees": 35.26438968,
            "key_sequence": ["Down"],
            "modifier_keys": [],
        },
    ]
    assert template["rotation_increment_restored_degrees"] == 45.0
    assert template["movement_options_command_id"] == (
        "cmdViewer3DMovementOptions"
    )
    assert template["movement_angle_control_id"] == "numNudgeAngle"
    assert template["movement_screen_factor_control_id"] == "numNudgeFactor"
    assert template["movement_screen_factor"] == 2.0
    assert template["movement_dialog_closed"] is True
    assert template["model_visible"] is None
    assert template["camera_matches_manifest"] is None
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["replay_events"] == []

def test_isometric_record_template_includes_every_anonymous_command_use() -> None:
    reset_target = {
        "target_kind": "verified_anonymous_toolbar_child",
        "command_id": "cmdViewer3DResetView",
        "toolbar_name": "3D Viewer",
        "toolbar_automation_id": 12122,
        "registry_toolbar_name": "tbarViewer3D1",
        "zero_based_child_index": 5,
        "element_index": 110,
        "registry_sha256": "a" * 64,
        "semantic_mapping_sha256": "b" * 64,
    }
    movement_target = {
        "target_kind": "verified_anonymous_toolbar_child",
        "command_id": "cmdViewer3DMovementOptions",
        "toolbar_name": "3D Movement",
        "toolbar_automation_id": 12134,
        "registry_toolbar_name": "tbarViewer3DMovement",
        "zero_based_child_index": 4,
        "element_index": 31,
        "registry_sha256": "a" * 64,
        "semantic_mapping_sha256": "c" * 64,
    }
    recipe = {
        "view_name": "isometric",
        "accessibility_target": reset_target,
        "movement_accessibility_target": movement_target,
        "native_command_id": "cmdViewer3DResetView",
        "required_record_evidence": {"field": "crystal_camera_evidence"},
    }
    action_receipt = {
        "reset_invocation_succeeded": True,
        "reset_command": {"accessibility_tree_refreshed": True},
        "movement_command": {
            "accessibility_tree_refreshed": True,
            "invocation_succeeded": True,
        },
        "keyboard_stages": [
            {
                "rotation_increment_degrees": 45.0,
                "key_sequence": ["Up", "Up", "Left", "Left", "Left"],
                "modifier_keys": [],
            },
            {
                "rotation_increment_degrees": 35.26438968,
                "key_sequence": ["Down"],
                "modifier_keys": [],
            },
        ],
        "rotation_increment_restored_degrees": 45.0,
        "movement_options_command_id": "cmdViewer3DMovementOptions",
        "movement_angle_control_id": "numNudgeAngle",
        "movement_screen_factor_control_id": "numNudgeFactor",
        "movement_screen_factor": 2.0,
        "movement_dialog_closed": True,
    }

    template = gui_module._local_view_replay_record_template(
        project_id="view_proj",
        revision=2,
        view_name="isometric",
        execution_recipe=recipe,
        action_receipt=action_receipt,
        target_window={"handle": 100, "title": "wrapper - Materials Studio"},
        screenshot_path="C:\\workspace\\isometric.bmp",
    )

    assert [
        item["command_id"] for item in template["accessibility_command_uses"]
    ] == ["cmdViewer3DResetView", "cmdViewer3DMovementOptions"]
    assert all(
        item["accessibility_tree_refreshed"] is True
        and item["invocation_succeeded"] is True
        for item in template["accessibility_command_uses"]
    )
    assert template["keyboard_stages"] == action_receipt["keyboard_stages"]
    assert template["model_visible"] is None
    assert template["camera_matches_manifest"] is None


def test_miller_preview_uses_transaction_gate_without_gui_input_or_persistence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, _gui_backend, _standard_backend = _controller(tmp_path)
    miller_backend = _MillerReplayBackend()
    controller.view_replay_backend = miller_backend
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _miller_command_evidence,
    )
    output_dir = tmp_path / "view_proj" / "outputs" / "r002"

    result = controller.run_view_replay(
        _miller_audit(),
        project_id="view_proj",
        revision=2,
        view_name="crystal_plane_001",
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["execution_ready"] is True
    assert result["selected_view_name"] == "crystal_plane_001"
    assert "crystal_plane_001" in result["execution_supported_view_names"]
    assert result["plan"]["execution_recipe"]["reset_view_allowed"] is False
    assert result["gui_input_performed"] is False
    assert result["manifest_modified"] is False
    assert miller_backend.execute_calls == []
    assert not (output_dir / "gui_view_replay_manifest.json").exists()
    assert not (output_dir / "gui_view_replay_runtime_preflight.json").exists()


def test_miller_execute_uses_aligned_capture_and_persists_transaction_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, gui_backend, _standard_backend = _controller(tmp_path)
    miller_backend = _MillerReplayBackend()
    controller.view_replay_backend = miller_backend
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _miller_command_evidence,
    )

    result = controller.run_view_replay(
        _miller_audit(),
        project_id="view_proj",
        revision=2,
        view_name="crystal_plane_001",
        execution_mode="execute",
    )

    assert result["status"] == "awaiting_visual_confirmation"
    assert result["execution_succeeded"] is True
    assert result["gui_input_performed"] is True
    assert result["gui_modified"] is True
    assert result["structure_modified"] is False
    assert result["structure_unchanged"] is True
    assert result["transaction_runtime_preflight_persisted"] is True
    assert result["visual_acceptance_recorded"] is False
    assert result["acceptance_event_created"] is False
    assert len(miller_backend.execute_calls) == 1
    assert gui_backend.captured == []

    snapshot = result["snapshot"]
    assert snapshot["capture_phase"] == "aligned_before_transaction_cleanup"
    assert Path(snapshot["screenshot_path"]).is_file()
    assert snapshot["analysis"]["readable"] is True
    template = result["post_action_record_payload_template"]
    assert template["native_command_id"] == "cmdViewer3DViewOnto"
    assert template["model_visible"] is None
    assert template["camera_matches_manifest"] is None
    assert template["modifier_keys"] == []
    assert template["miller_plane_evidence"]["pre_action_view_baseline_captured"] is True
    assert template["miller_plane_evidence"]["reset_view_before_alignment"] is False
    assert template["miller_plane_evidence"]["undo_labels_applied"] == [
        "Undo View Onto Miller Plane",
        "Undo Create Miller Plane",
    ]

    runtime_path = Path(result["manifest_path"]).with_name(
        "gui_view_replay_runtime_preflight.json"
    )
    assert runtime_path.is_file()
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    viewport_probe = runtime["evidence"]["viewport_selection_probe"]
    assert viewport_probe["complete"] is True
    assert viewport_probe["structure_artifact_sha256_before"] == (
        viewport_probe["structure_artifact_sha256_after"]
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["replay_events"] == []

    recorded = controller.record_view_replay(
        project_id=template["project_id"],
        revision=template["revision"],
        view_name=template["view_name"],
        source=template["source"],
        model_visible=True,
        camera_matches_manifest=True,
        screenshot_path=template["screenshot_path"],
        expected_window_handle=template["expected_window_handle"],
        expected_window_title=template["expected_window_title"],
        native_command_id=template["native_command_id"],
        modifier_keys=template["modifier_keys"],
        miller_plane_evidence=template["miller_plane_evidence"],
    )
    assert recorded["event"]["accepted"] is True
    assert recorded["event"]["rejection_reasons"] == []


def test_miller_execute_failure_does_not_resolve_missing_aligned_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, gui_backend, _standard_backend = _controller(tmp_path)
    failed_backend = _FailedMillerReplayBackend()
    controller.view_replay_backend = failed_backend
    monkeypatch.setattr(
        gui_module,
        "_materials_studio_view_command_evidence",
        _miller_command_evidence,
    )

    result = controller.run_view_replay(
        _miller_audit(),
        project_id="view_proj",
        revision=2,
        view_name="crystal_plane_001",
        execution_mode="execute",
    )

    assert result["status"] == "execution_failed"
    assert result["execution_succeeded"] is False
    assert result["snapshot"] is None
    assert result["snapshot_error"] == (
        "transactional Miller execution failed before an aligned screenshot "
        "was available"
    )
    assert "outside the GUI workspace" not in result["snapshot_error"]
    assert gui_backend.captured == []


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
