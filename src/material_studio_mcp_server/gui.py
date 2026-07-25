"""本地 Materials Studio GUI 助手。

此模块故意避免 COM 自动化。它提供了一个保守的 Windows 回退方案，
用于查找已打开的 MatStudio 窗口、激活它、通过 OS shell 关联打开结构文件，
以及捕获 BMP 快照用于审计日志。
"""

from __future__ import annotations

import csv
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Protocol
from xml.sax.saxutils import escape as xml_escape

from .gui_uia import (
    FIT_TO_VIEW_COMMAND_ID,
    FIT_TO_VIEW_CONTROL_NAME,
    FIT_TO_VIEW_TOOLBAR_CHILD_INDEX,
    FIT_TO_VIEW_TOOLBAR_NAME,
    PywinautoViewReplayBackend,
    SAFE_ISOMETRIC_KEYBOARD_STAGES,
    SAFE_LOCAL_VIEW_NAMES,
    SAFE_STANDARD_VIEW_KEY_SEQUENCES,
    ViewReplayAutomationBackend,
    local_uia_view_replay_implementation_contract,
)
from .parsers.copy_script import analyze_reviewed_copy_script
from .state.store import default_workspace_root, sanitize_project_id


class GuiError(RuntimeError):
    """当本地 GUI 控制无法完成时引发。"""


class GuiSnapshotBlockedError(GuiError):
    """Raised when a snapshot is blocked before any capture starts."""

    def __init__(self, message: str, *, receipt: dict[str, Any]) -> None:
        self.receipt = dict(receipt)
        super().__init__(message)


VIEW_REPLAY_MANIFEST_SCHEMA_VERSION = 5
VIEW_REPLAY_BASE_RECIPE_SCHEMA_VERSION = 4
VIEW_REPLAY_STAGED_KEYBOARD_RECIPE_SCHEMA_VERSION = 4
CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION = 5
MILLER_VIEW_ONTO_RECIPE_SCHEMA_VERSION = 8
GUI_BACKEND_ENV = "MATERIAL_STUDIO_MCP_GUI_BACKEND"

# These identifiers come from the Materials Studio 2020 #SVViewer3d command
# registry. They are evidence for reviewed GUI automation, not a public
# MaterialsScript camera API.
MATERIALS_STUDIO_2020_VIEW_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "action": "reset_view",
        "command_id": "cmdViewer3DResetView",
        "label": "3D Viewer Reset View",
    },
    {
        "action": "recenter",
        "command_id": "cmdViewer3DRecenter",
        "label": "3D Viewer Recenter",
    },
    {
        "action": "view_onto_selection",
        "command_id": "cmdViewer3DViewOnto",
        "label": "3D Viewer View Onto",
    },
    {
        "action": "view_across_selection",
        "command_id": "cmdViewer3DViewAcrossHorizontal",
        "label": "3D Viewer View Across",
    },
    {
        "action": "fit_to_view",
        "command_id": "cmdViewer3DFitToView",
        "label": "3D Viewer Fit to View",
    },
    {
        "action": "movement_options",
        "command_id": "cmdViewer3DMovementOptions",
        "label": "3D Movement Options",
    },
)

STANDARD_CARTESIAN_VIEW_NAMES = {
    "front",
    "back",
    "right",
    "left",
    "top",
    "bottom",
    "isometric",
}

STRUCTURE_MUTATING_VIEW_COMMAND_PREFIXES = (
    "cmdNudge",
    "cmdViewer3DAlign",
    "cmdSMSketcherAlign",
)

DOCUMENTED_VIEW_KEY_RECIPES: dict[str, dict[str, Any]] = {
    "back": {
        "key_sequence": ["Left", "Left", "Left", "Left"],
        "expected_axis_layout": {"screen_right": "-A", "screen_up": "B", "view_depth": "C"},
    },
    "right": {
        "key_sequence": ["Up", "Up", "Left", "Left"],
        "expected_axis_layout": {"screen_right": "B", "screen_up": "C", "view_depth": "A"},
    },
    "left": {
        "key_sequence": ["Up", "Up", "Right", "Right"],
        "expected_axis_layout": {"screen_right": "-B", "screen_up": "C", "view_depth": "A"},
    },
    "top": {
        "key_sequence": ["Up", "Up"],
        "expected_axis_layout": {"screen_right": "A", "screen_up": "C", "view_depth": "B"},
    },
    "bottom": {
        "key_sequence": ["Left", "Left", "Left", "Left", "Down", "Down"],
        "expected_axis_layout": {"screen_right": "-A", "screen_up": "C", "view_depth": "B"},
    },
    "isometric": {
        "keyboard_stages": [
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
        ],
        "restore_rotation_increment_degrees": 45.0,
        "movement_options_command_id": "cmdViewer3DMovementOptions",
        "movement_angle_control_id": "numNudgeAngle",
        "movement_screen_factor_control_id": "numNudgeFactor",
        "movement_screen_factor_expected": 2.0,
        "movement_dialog_closed_after_restore": True,
        "expected_axis_layout": {
            "axis_a_projection": "left_down",
            "axis_b_projection": "right_down",
            "axis_c_projection": "up",
            "view_depth": "+A+B+C",
        },
    },
}

VIEW_REPLAY_ARROW_KEYS = {"Up", "Down", "Left", "Right"}
VIEW_REPLAY_MODIFIER_KEYS = {"Shift", "Ctrl", "Alt", "Win"}
VIEW_REPLAY_KEYBOARD_STAGE_FIELDS = {
    "rotation_increment_degrees",
    "key_sequence",
    "modifier_keys",
}

MILLER_PLANE_SELECTION_METHOD = "object_tree_exact_item_rect_semantic_click"
MILLER_PLANE_VIEWPORT_SELECTION_METHOD = (
    "viewport_unique_transient_plane_properties_verified"
)

VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS = {
    "cmdViewer3DResetView": "3D Viewer Reset View",
    "cmdViewer3DMovementOptions": "3D Movement Options",
}
VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS = {
    "3D Viewer": {
        "registry_toolbar_name": "tbarViewer3D1",
        "entries": (
            ("tool", "cmdViewer3DSelection"),
            ("tool", "cmdViewer3DTrackball"),
            ("tool", "cmdViewer3DZoom"),
            ("tool", "cmdViewer3DTranslate"),
            ("separator", None),
            ("tool", "cmdViewer3DResetView"),
            ("tool", "cmdViewer3DRecenter"),
            ("tool", "cmdViewer3DFitToView"),
            ("tool", "cmdViewer3DDisplayStyle"),
        ),
    },
    "3D Movement": {
        "registry_toolbar_name": "tbarViewer3DMovement",
        "entries": (
            ("tool", "cmdNudgeLeft"),
            ("tool", "cmdNudgeRight"),
            ("tool", "cmdNudgeUp"),
            ("tool", "cmdNudgeDown"),
            ("tool", "cmdViewer3DMovementOptions"),
            ("separator", None),
            ("tool", "cmdSMSketcherMoveTo"),
            ("tool", "cmdViewer3DAlignOntoView"),
        ),
    },
}
VIEW_RUNTIME_ACCESSIBILITY_CONTROL_FIELDS = {
    "command_id",
    "observed_control_name",
    "invoke_supported",
}
VIEW_RUNTIME_ACCESSIBILITY_CONTROL_DERIVED_FIELDS = {
    "expected_control_name",
    "named_control_observed",
}
VIEW_RUNTIME_ACCESSIBILITY_CONTROL_ALLOWED_FIELDS = (
    VIEW_RUNTIME_ACCESSIBILITY_CONTROL_FIELDS
    | VIEW_RUNTIME_ACCESSIBILITY_CONTROL_DERIVED_FIELDS
)
VIEW_RUNTIME_ACCESSIBILITY_EVIDENCE_FIELDS = {
    "source",
    "expected_revision",
    "expected_window_handle",
    "expected_window_title",
    "accessibility_tree_refreshed",
    "viewer_document_observed",
    "empty_viewport_focus_target_observed",
    "semantic_viewport_focus_supported",
    "unnamed_toolbar_children_observed",
    "controls",
    "anonymous_toolbars",
    "screenshot_path",
    "note",
}
VIEW_RUNTIME_ACCESSIBILITY_OPTIONAL_EVIDENCE_FIELDS = {
    "anonymous_toolbars",
    "semantic_viewport_focus_supported",
    "screenshot_path",
    "note",
}
VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_TOOLBAR_FIELDS = {
    "observed_toolbar_name",
    "toolbar_automation_id",
    "children",
}
VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_CHILD_FIELDS = {
    "element_index",
    "role",
    "enabled",
    "observed_control_name",
}
VIEW_REPLAY_ACCESSIBILITY_COMMAND_USE_FIELDS = {
    "command_id",
    "toolbar_name",
    "toolbar_automation_id",
    "registry_toolbar_name",
    "zero_based_child_index",
    "element_index",
    "registry_sha256",
    "semantic_mapping_sha256",
    "accessibility_tree_refreshed",
    "invocation_succeeded",
}
REVIEWED_COPY_SCRIPT_EVIDENCE_FIELDS = {
    "script_text",
    "capture_method",
    "reviewer",
    "copy_script_command_observed",
    "review_completed",
    "view_action_matches_manifest",
    "structure_unchanged_observed",
    "note",
}
VIEW_REPLAY_EVIDENCE_INTEGRITY_SCHEMA_VERSION = 1
VIEW_REPLAY_EVIDENCE_INTEGRITY_ALGORITHM = "sha256"
VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION = 1
VIEW_REPLAY_EVENT_JOURNAL_MAX_BYTES = 16 * 1024 * 1024
VIEW_REPLAY_EVENT_JOURNAL_MAX_LINES = 10_000
VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES = 100
VIEW_REPLAY_WRITE_LOCK_TIMEOUT_SECONDS = 10.0
VIEW_REPLAY_WRITE_LOCK_POLL_SECONDS = 0.05
REVIEWED_COPY_SCRIPT_INTEGRITY_ARTIFACT_KINDS = {
    "screenshot",
    "copy_script",
    "copy_script_metadata",
    "structure",
}
MILLER_PLANE_SELECTION_METHODS = {
    MILLER_PLANE_SELECTION_METHOD,
    MILLER_PLANE_VIEWPORT_SELECTION_METHOD,
}
MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS = (
    "fresh_before_after_screenshot_unique_transient_plane_region"
)
MILLER_DIALOG_NAVIGATION_KEY_SETTLE_DELAY_MILLISECONDS = 200
MILLER_DIALOG_REPEATED_KEY_INTERPRESS_DELAY_MILLISECONDS = 200
MILLER_DIALOG_POST_MUTATION_READBACK_DELAY_MILLISECONDS = 500
MILLER_DIALOG_REQUIRED_TIMING_ACTIONS = {
    "wait_recipe_navigation_delay_after_home_or_end",
    "pace_each_backspace_or_delete_with_recipe_interpress_delay",
    "wait_recipe_post_mutation_delay_before_fresh_readback",
}
MILLER_PLANE_CAMERA_MATCH_SCOPE = "crystal_plane_normal_with_native_in_plane_roll"
MILLER_DIRECTION_CAMERA_MATCH_SCOPE = (
    "crystal_lattice_direction_via_collinear_plane_normal_with_native_in_plane_roll"
)
CRYSTAL_STANDARD_VIEW_RECIPE_KIND = "crystal_standard_view_with_native_in_plane_roll"
CRYSTAL_STANDARD_VIEW_CAMERA_MATCH_SCOPE = (
    "crystal_view_direction_with_observed_native_in_plane_roll"
)
CRYSTAL_STANDARD_VIEW_CAMERA_EVIDENCE_FIELDS = {
    "camera_match_scope",
    "view_direction_matches_manifest",
    "analytic_in_plane_basis_matches_manifest",
    "native_in_plane_roll_observed",
}
MILLER_VIEW_ONTO_RECIPE_KINDS = {
    "miller_plane_view_onto",
    "crystal_direction_via_collinear_miller_plane_view_onto",
}
MILLER_PLANE_OBJECT_TREE_PATH_SUFFIX = [
    "<Miller Family>",
    "<Miller Parallel Planes>",
    "<Miller Plane>",
]
MILLER_PLANE_REPLAY_EVIDENCE_FIELDS = {
    "miller_plane_indices",
    "dialog_miller_indices",
    "dialog_miller_indices_text_before_create",
    "dialog_miller_indices_value_source",
    "dialog_miller_indices_verified_before_create",
    "created_plane_count",
    "selected_plane_count",
    "miller_plane_count_before",
    "miller_plane_count_after_create",
    "miller_plane_count_after_cleanup",
    "selection_method",
    "object_tree_path_suffix",
    "viewport_hit_test_basis",
    "fresh_before_after_screenshots_observed",
    "unique_transient_plane_region_observed",
    "properties_selection_verified",
    "view_onto_popup_menu_observed",
    "view_onto_native_command_mapping_verified",
    "dialog_show_set_of_parallel_planes",
    "dialog_show_symmetry_images",
    "properties_filter",
    "properties_miller_label",
    "camera_match_scope",
    "plane_normal_matches_manifest",
    "analytic_in_plane_basis_matches_manifest",
    "native_in_plane_roll_policy_observed",
    "pre_action_view_baseline_captured",
    "reset_view_before_alignment",
    "screenshot_captured_before_cleanup",
    "document_was_clean_before_replay",
    "temporary_miller_plane_cleanup_verified",
    "no_temporary_miller_nodes_remaining",
    "document_clean_after_replay",
    "post_replay_view_restored",
    "structure_artifact_path",
    "structure_artifact_sha256_before",
    "structure_artifact_sha256_after",
    "undo_labels_applied",
    "direct_lattice_direction_matches_manifest",
}
MILLER_PLANE_OPTIONAL_REPLAY_EVIDENCE_FIELDS = {
    "direct_lattice_direction_matches_manifest",
    "object_tree_path_suffix",
    "viewport_hit_test_basis",
    "fresh_before_after_screenshots_observed",
    "unique_transient_plane_region_observed",
    "properties_selection_verified",
    "view_onto_popup_menu_observed",
    "reset_view_before_alignment",
    "dialog_show_set_of_parallel_planes",
    "dialog_show_symmetry_images",
}
MILLER_PLANE_REQUIRED_TRUE_EVIDENCE_FIELDS = (
    "dialog_miller_indices_verified_before_create",
    "plane_normal_matches_manifest",
    "native_in_plane_roll_policy_observed",
    "pre_action_view_baseline_captured",
    "view_onto_native_command_mapping_verified",
    "screenshot_captured_before_cleanup",
    "document_was_clean_before_replay",
    "temporary_miller_plane_cleanup_verified",
    "no_temporary_miller_nodes_remaining",
    "document_clean_after_replay",
    "post_replay_view_restored",
)
MILLER_PLANE_UNDO_LABEL_PATTERNS = (
    re.compile(r"^Undo View Onto (?:Miller Plane|Lattice 3D)$"),
    re.compile(r"^Undo Recenter$"),
    re.compile(r"^Undo Create Miller Plane$"),
    re.compile(r"^Undo Reset View$"),
)
MILLER_RUNTIME_UI_BOOLEAN_FIELDS = (
    "reset_view_control_observed",
    "tools_miller_planes_menu_observed",
    "miller_planes_keyboard_menu_path_verified",
    "miller_planes_dialog_observed",
    "miller_indices_control_observed",
    "create_button_observed",
    "tree_explorer_menu_observed",
    "properties_explorer_menu_observed",
    "view_onto_control_observed",
    "view_onto_native_command_mapping_verified",
    "pointer_menu_click_through_risk_observed",
    "unexpected_plane_created_during_probe",
    "unexpected_plane_cleanup_verified",
    "document_clean_before_probe",
    "document_clean_after_probe",
)
MILLER_RUNTIME_UI_REQUIRED_TRUE_FIELDS = (
    "tools_miller_planes_menu_observed",
    "miller_planes_keyboard_menu_path_verified",
    "miller_planes_dialog_observed",
    "miller_indices_control_observed",
    "create_button_observed",
    "properties_explorer_menu_observed",
    "view_onto_control_observed",
    "view_onto_native_command_mapping_verified",
    "document_clean_before_probe",
    "document_clean_after_probe",
)
MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS = (
    "unique_transient_plane_visual_target_observed",
    "viewport_plane_selection_observed",
    "properties_selection_verified",
)
MILLER_RUNTIME_VIEWPORT_PROBE_FIELDS = {
    "selection_method",
    "probe_miller_indices",
    "dialog_miller_indices",
    *MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS,
    "view_onto_popup_menu_observed",
    "view_onto_native_command_mapping_verified",
    "hit_test_basis",
    "properties_filter",
    "properties_miller_label",
    "view_onto_command_id",
    "undo_labels_observed",
    "structure_artifact_path",
    "structure_artifact_sha256_before",
    "structure_artifact_sha256_after",
}
MILLER_RUNTIME_VIEWPORT_PROBE_DERIVED_FIELDS = {
    "structure_artifact_sha256_current",
    "block_reasons",
    "complete",
}
MILLER_RUNTIME_VIEWPORT_PROBE_ALLOWED_FIELDS = (
    MILLER_RUNTIME_VIEWPORT_PROBE_FIELDS | MILLER_RUNTIME_VIEWPORT_PROBE_DERIVED_FIELDS
)
MILLER_RUNTIME_VIEWPORT_PROBE_UNDO_LABEL_PATTERNS = (
    re.compile(r"^Undo Reset View$"),
    re.compile(r"^Undo View Onto Miller Plane$"),
    re.compile(r"^Undo Recenter$"),
    re.compile(r"^Undo Create Miller Plane$"),
)
MILLER_RUNTIME_UI_EVIDENCE_FIELDS = {
    "source",
    "expected_revision",
    "expected_window_handle",
    "expected_window_title",
    *MILLER_RUNTIME_UI_BOOLEAN_FIELDS,
    "miller_planes_menu_key_sequence",
    "miller_planes_dialog_title",
    "miller_planes_dialog_control_id",
    "miller_indices_control_id",
    "create_button_control_id",
    "selection_modifier_keys",
    "viewport_selection_probe",
    "screenshot_path",
    "note",
}
MILLER_RUNTIME_UI_EXPECTED_IDENTIFIERS = {
    "miller_planes_dialog_title": "Miller Planes",
    "miller_planes_dialog_control_id": "MillerPlanesCtl",
    "miller_indices_control_id": "TxtHKL",
    "create_button_control_id": "CmdCreate",
}
MILLER_RUNTIME_UI_REQUIRED_KEY_SEQUENCE = ["Alt+T", "M"]
MILLER_RUNTIME_UI_BLOCK_REASON_BY_FIELD = {
    "tools_miller_planes_menu_observed": "runtime_tools_miller_planes_menu_not_observed",
    "miller_planes_keyboard_menu_path_verified": (
        "runtime_miller_planes_keyboard_menu_path_not_verified"
    ),
    "miller_planes_dialog_observed": "runtime_miller_planes_dialog_not_observed",
    "miller_indices_control_observed": "runtime_miller_indices_control_not_observed",
    "create_button_observed": "runtime_miller_plane_create_button_not_observed",
    "properties_explorer_menu_observed": "runtime_properties_explorer_menu_not_observed",
    "view_onto_control_observed": "runtime_view_onto_control_not_observed",
    "view_onto_native_command_mapping_verified": (
        "runtime_view_onto_native_command_mapping_not_verified"
    ),
    "document_clean_before_probe": "runtime_document_not_clean_before_probe",
    "document_clean_after_probe": "runtime_document_not_clean_after_probe",
}


def _normalize_view_runtime_accessibility_evidence(
    value: dict[str, Any],
) -> dict[str, Any]:
    """Validate named-control observations from the exact live wrapper window."""

    if not isinstance(value, dict):
        raise GuiError("runtime_accessibility_evidence must be a JSON object")
    extra_fields = sorted(set(value) - VIEW_RUNTIME_ACCESSIBILITY_EVIDENCE_FIELDS)
    if extra_fields:
        raise GuiError(
            "runtime_accessibility_evidence contains unsupported fields: "
            + ", ".join(extra_fields)
        )
    required_fields = (
        VIEW_RUNTIME_ACCESSIBILITY_EVIDENCE_FIELDS
        - VIEW_RUNTIME_ACCESSIBILITY_OPTIONAL_EVIDENCE_FIELDS
    )
    missing_fields = sorted(required_fields - set(value))
    if missing_fields:
        raise GuiError(
            "runtime_accessibility_evidence is missing required fields: "
            + ", ".join(missing_fields)
        )

    normalized = dict(value)
    source = str(normalized.get("source") or "").strip()
    if source not in {"computer_use", "manual_review", "local_uia"}:
        raise GuiError("unsupported runtime_accessibility_evidence source")
    normalized["source"] = source
    try:
        expected_revision = int(normalized.get("expected_revision"))
        expected_window_handle = int(normalized.get("expected_window_handle"))
    except (TypeError, ValueError) as exc:
        raise GuiError(
            "runtime_accessibility_evidence revision and window handle must be integers"
        ) from exc
    if expected_revision < 0:
        raise GuiError(
            "runtime_accessibility_evidence expected_revision must be non-negative"
        )
    if expected_window_handle <= 0:
        raise GuiError(
            "runtime_accessibility_evidence expected_window_handle must be positive"
        )
    expected_window_title = str(normalized.get("expected_window_title") or "").strip()
    if not expected_window_title:
        raise GuiError(
            "runtime_accessibility_evidence expected_window_title must not be empty"
        )
    normalized["expected_revision"] = expected_revision
    normalized["expected_window_handle"] = expected_window_handle
    normalized["expected_window_title"] = expected_window_title

    for field in (
        "accessibility_tree_refreshed",
        "viewer_document_observed",
        "empty_viewport_focus_target_observed",
        "unnamed_toolbar_children_observed",
    ):
        if not isinstance(normalized.get(field), bool):
            raise GuiError(f"runtime_accessibility_evidence.{field} must be a boolean")
    semantic_focus = normalized.get("semantic_viewport_focus_supported", False)
    if not isinstance(semantic_focus, bool):
        raise GuiError(
            "runtime_accessibility_evidence.semantic_viewport_focus_supported "
            "must be a boolean"
        )
    if source != "local_uia" and semantic_focus:
        raise GuiError(
            "semantic_viewport_focus_supported is reserved for the server-generated "
            "local_uia probe"
        )
    normalized["semantic_viewport_focus_supported"] = semantic_focus

    raw_controls = normalized.get("controls")
    if not isinstance(raw_controls, list) or not 0 <= len(raw_controls) <= len(
        VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS
    ):
        raise GuiError(
            "runtime_accessibility_evidence.controls must contain at most two control observations"
        )
    controls: list[dict[str, Any]] = []
    seen_command_ids: set[str] = set()
    for index, raw_control in enumerate(raw_controls, start=1):
        if not isinstance(raw_control, dict):
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}] must be a JSON object"
            )
        extra_control_fields = sorted(
            set(raw_control) - VIEW_RUNTIME_ACCESSIBILITY_CONTROL_ALLOWED_FIELDS
        )
        if extra_control_fields:
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}] contains unsupported fields: "
                + ", ".join(extra_control_fields)
            )
        missing_control_fields = sorted(
            VIEW_RUNTIME_ACCESSIBILITY_CONTROL_FIELDS - set(raw_control)
        )
        if missing_control_fields:
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}] is missing required fields: "
                + ", ".join(missing_control_fields)
            )
        command_id = str(raw_control.get("command_id") or "").strip()
        if command_id not in VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS:
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}] has an unsupported command_id"
            )
        if command_id in seen_command_ids:
            raise GuiError(
                "runtime_accessibility_evidence.controls contains a duplicate command_id"
            )
        seen_command_ids.add(command_id)
        observed_name_value = raw_control.get("observed_control_name")
        if observed_name_value is not None and not isinstance(observed_name_value, str):
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}].observed_control_name "
                "must be a string or null"
            )
        observed_name = (
            str(observed_name_value).strip() if observed_name_value is not None else None
        )
        if observed_name == "":
            observed_name = None
        invoke_supported = raw_control.get("invoke_supported")
        if not isinstance(invoke_supported, bool):
            raise GuiError(
                f"runtime_accessibility_evidence.controls[{index}].invoke_supported "
                "must be a boolean"
            )
        expected_name = VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS[command_id]
        named_control_observed = observed_name == expected_name
        if invoke_supported and not named_control_observed:
            raise GuiError(
                "runtime_accessibility_evidence cannot mark an unnamed or mismatched control "
                "as invocable"
            )
        controls.append(
            {
                "command_id": command_id,
                "expected_control_name": expected_name,
                "observed_control_name": observed_name,
                "named_control_observed": named_control_observed,
                "invoke_supported": invoke_supported,
            }
        )
    normalized["controls"] = controls

    raw_toolbars = normalized.get("anonymous_toolbars") or []
    if not isinstance(raw_toolbars, list) or len(raw_toolbars) > len(
        VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS
    ):
        raise GuiError(
            "runtime_accessibility_evidence.anonymous_toolbars must contain at most "
            "two toolbar observations"
        )
    anonymous_toolbars: list[dict[str, Any]] = []
    seen_toolbar_names: set[str] = set()
    seen_toolbar_automation_ids: set[int] = set()
    seen_element_indices: set[int] = set()
    for toolbar_index, raw_toolbar in enumerate(raw_toolbars, start=1):
        if not isinstance(raw_toolbar, dict):
            raise GuiError(
                "runtime_accessibility_evidence.anonymous_toolbars"
                f"[{toolbar_index}] must be a JSON object"
            )
        extra_toolbar_fields = sorted(
            set(raw_toolbar) - VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_TOOLBAR_FIELDS
        )
        if extra_toolbar_fields:
            raise GuiError(
                "runtime_accessibility_evidence.anonymous_toolbars"
                f"[{toolbar_index}] contains unsupported fields: "
                + ", ".join(extra_toolbar_fields)
            )
        missing_toolbar_fields = sorted(
            VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_TOOLBAR_FIELDS - set(raw_toolbar)
        )
        if missing_toolbar_fields:
            raise GuiError(
                "runtime_accessibility_evidence.anonymous_toolbars"
                f"[{toolbar_index}] is missing required fields: "
                + ", ".join(missing_toolbar_fields)
            )
        toolbar_name = str(raw_toolbar.get("observed_toolbar_name") or "").strip()
        if toolbar_name not in VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS:
            raise GuiError(
                "runtime_accessibility_evidence anonymous toolbar name is not allowlisted"
            )
        if toolbar_name in seen_toolbar_names:
            raise GuiError(
                "runtime_accessibility_evidence contains a duplicate anonymous toolbar name"
            )
        seen_toolbar_names.add(toolbar_name)
        toolbar_automation_id = raw_toolbar.get("toolbar_automation_id")
        if (
            not isinstance(toolbar_automation_id, int)
            or isinstance(toolbar_automation_id, bool)
            or toolbar_automation_id <= 0
        ):
            raise GuiError(
                "runtime_accessibility_evidence anonymous toolbar automation ID must "
                "be a positive integer"
            )
        if toolbar_automation_id in seen_toolbar_automation_ids:
            raise GuiError(
                "runtime_accessibility_evidence contains a duplicate toolbar automation ID"
            )
        seen_toolbar_automation_ids.add(toolbar_automation_id)
        raw_children = raw_toolbar.get("children")
        if not isinstance(raw_children, list) or not 1 <= len(raw_children) <= 32:
            raise GuiError(
                "runtime_accessibility_evidence anonymous toolbar children must contain "
                "between 1 and 32 observations"
            )
        children: list[dict[str, Any]] = []
        previous_element_index: int | None = None
        for child_index, raw_child in enumerate(raw_children, start=1):
            if not isinstance(raw_child, dict):
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar child "
                    f"{child_index} must be a JSON object"
                )
            extra_child_fields = sorted(
                set(raw_child) - VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_CHILD_FIELDS
            )
            if extra_child_fields:
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar child contains "
                    "unsupported fields: " + ", ".join(extra_child_fields)
                )
            required_child_fields = (
                VIEW_RUNTIME_ACCESSIBILITY_ANONYMOUS_CHILD_FIELDS
                - {"observed_control_name"}
            )
            missing_child_fields = sorted(required_child_fields - set(raw_child))
            if missing_child_fields:
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar child is missing "
                    "required fields: " + ", ".join(missing_child_fields)
                )
            element_index = raw_child.get("element_index")
            if (
                not isinstance(element_index, int)
                or isinstance(element_index, bool)
                or element_index < 0
            ):
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar element_index must "
                    "be a non-negative integer"
                )
            if element_index in seen_element_indices:
                raise GuiError(
                    "runtime_accessibility_evidence contains a duplicate anonymous toolbar "
                    "element_index"
                )
            if previous_element_index is not None and element_index <= previous_element_index:
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar element_index values "
                    "must be strictly increasing within each toolbar"
                )
            previous_element_index = element_index
            seen_element_indices.add(element_index)
            role = str(raw_child.get("role") or "").strip().lower()
            if role not in {"checkbox", "separator"}:
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar role must be "
                    "checkbox or separator"
                )
            enabled = raw_child.get("enabled")
            if not isinstance(enabled, bool):
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar enabled must be a boolean"
                )
            observed_name_value = raw_child.get("observed_control_name")
            if observed_name_value is not None and not isinstance(
                observed_name_value, str
            ):
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar observed_control_name "
                    "must be a string or null"
                )
            observed_name = (
                observed_name_value.strip() if isinstance(observed_name_value, str) else None
            )
            if observed_name == "":
                observed_name = None
            if role == "separator" and (enabled or observed_name is not None):
                raise GuiError(
                    "runtime_accessibility_evidence anonymous toolbar separators must be "
                    "disabled and unnamed"
                )
            children.append(
                {
                    "element_index": element_index,
                    "role": role,
                    "enabled": enabled,
                    "observed_control_name": observed_name,
                }
            )
        anonymous_toolbars.append(
            {
                "observed_toolbar_name": toolbar_name,
                "toolbar_automation_id": toolbar_automation_id,
                "children": children,
            }
        )
    if anonymous_toolbars and normalized.get("unnamed_toolbar_children_observed") is not True:
        raise GuiError(
            "runtime_accessibility_evidence anonymous toolbar observations require "
            "unnamed_toolbar_children_observed=true"
        )
    normalized["anonymous_toolbars"] = anonymous_toolbars

    for field in ("screenshot_path", "note"):
        item = normalized.get(field)
        if item is not None and not isinstance(item, str):
            raise GuiError(
                f"runtime_accessibility_evidence.{field} must be a string or null"
            )
    return normalized


def _resolve_verified_anonymous_toolbar_mappings(
    evidence: dict[str, Any],
    command_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Derive allowlisted commands from registry order and live child order."""

    observations = [
        item
        for item in evidence.get("anonymous_toolbars") or []
        if isinstance(item, dict)
    ]
    if not observations:
        return {
            "attempted": False,
            "registry_ready": False,
            "toolbar_results": [],
            "command_mappings": [],
            "mapped_command_ids": [],
            "invocation_ready_command_ids": [],
            "block_reasons": [],
        }

    registry_path = str(command_evidence.get("registry_path") or "").strip() or None
    registry_sha256 = str(command_evidence.get("registry_sha256") or "").lower()
    raw_layouts = command_evidence.get("registry_toolbar_layouts")
    registry_ready = bool(
        command_evidence.get("registry_found") is True
        and registry_path is not None
        and re.fullmatch(r"[0-9a-f]{64}", registry_sha256)
        and isinstance(raw_layouts, list)
        and command_evidence.get("registry_toolbar_parse_error") in {None, ""}
    )
    layouts = [item for item in raw_layouts or [] if isinstance(item, dict)]
    toolbar_results: list[dict[str, Any]] = []
    command_mappings: list[dict[str, Any]] = []
    all_block_reasons: list[str] = []

    for observation in observations:
        toolbar_name = str(observation.get("observed_toolbar_name") or "")
        contract = VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS.get(toolbar_name)
        reasons: list[str] = []
        if not registry_ready:
            reasons.append("installed_view_toolbar_registry_not_verified")
        if contract is None:
            reasons.append("anonymous_toolbar_contract_not_allowlisted")
            expected_entries: tuple[tuple[str, str | None], ...] = ()
            registry_toolbar_name = None
        else:
            expected_entries = contract["entries"]
            registry_toolbar_name = str(contract["registry_toolbar_name"])

        matching_layouts = [
            layout
            for layout in layouts
            if layout.get("registry_toolbar_name") == registry_toolbar_name
            and layout.get("title") == toolbar_name
        ]
        if len(matching_layouts) != 1:
            reasons.append("installed_toolbar_registry_identity_not_unique")
            installed_entries: list[tuple[str, str | None]] = []
        else:
            installed_entries = [
                (
                    str(entry.get("kind") or ""),
                    str(entry.get("command_id"))
                    if entry.get("command_id") is not None
                    else None,
                )
                for entry in matching_layouts[0].get("entries") or []
                if isinstance(entry, dict)
            ]
            if tuple(installed_entries) != tuple(expected_entries):
                reasons.append("installed_toolbar_registry_sequence_mismatch")

        children = [
            item for item in observation.get("children") or [] if isinstance(item, dict)
        ]
        if len(children) != len(expected_entries):
            reasons.append("live_toolbar_child_count_mismatch")
        if children and any(
            child.get("observed_control_name") is not None
            for child in children
            if child.get("role") != "separator"
        ):
            reasons.append("live_toolbar_children_not_all_unnamed")
        if len(children) == len(expected_entries):
            expected_roles = [
                "separator" if kind == "separator" else "checkbox"
                for kind, _command_id in expected_entries
            ]
            observed_roles = [str(child.get("role") or "") for child in children]
            if observed_roles != expected_roles:
                reasons.append("live_toolbar_child_role_sequence_mismatch")

        reasons = _unique_strings(reasons)
        toolbar_verified = not reasons
        toolbar_result: dict[str, Any] = {
            "observed_toolbar_name": toolbar_name,
            "toolbar_automation_id": observation.get("toolbar_automation_id"),
            "registry_toolbar_name": registry_toolbar_name,
            "registry_path": registry_path,
            "registry_sha256": registry_sha256 or None,
            "expected_child_count": len(expected_entries),
            "observed_child_count": len(children),
            "contract_verified": toolbar_verified,
            "block_reasons": reasons,
        }
        toolbar_results.append(toolbar_result)
        all_block_reasons.extend(
            f"{toolbar_name.replace(' ', '_').lower()}_{reason}" for reason in reasons
        )
        if not toolbar_verified:
            continue

        for child_index, ((kind, command_id), child) in enumerate(
            zip(expected_entries, children)
        ):
            if kind != "tool" or command_id not in VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS:
                continue
            mapping_basis = {
                "registry_path": registry_path,
                "registry_sha256": registry_sha256,
                "registry_toolbar_name": registry_toolbar_name,
                "toolbar_name": toolbar_name,
                "toolbar_automation_id": observation.get("toolbar_automation_id"),
                "command_id": command_id,
                "zero_based_child_index": child_index,
                "element_index": child.get("element_index"),
                "role": child.get("role"),
            }
            semantic_mapping_sha256 = hashlib.sha256(
                json.dumps(
                    mapping_basis,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            enabled = child.get("enabled") is True
            mapping = {
                **mapping_basis,
                "semantic_mapping_sha256": semantic_mapping_sha256,
                "mapping_status": (
                    "verified_installed_registry_and_live_child_order"
                    if enabled
                    else "verified_mapping_target_disabled"
                ),
                "verified": True,
                "invocation_ready": enabled,
                "target_kind": "verified_anonymous_toolbar_child",
                "invocation_method": (
                    "local_uia_invoke_pattern"
                    if evidence.get("source") == "local_uia"
                    else "computer_use_accessibility_element_index"
                ),
                "element_index_is_ephemeral": True,
                "requires_fresh_tree_match_before_invoke": True,
                "observed_control_name": child.get("observed_control_name"),
            }
            if not enabled:
                mapping["block_reasons"] = [
                    "mapped_anonymous_toolbar_control_not_enabled"
                ]
            else:
                mapping["block_reasons"] = []
            command_mappings.append(mapping)

    return {
        "attempted": True,
        "registry_ready": registry_ready,
        "registry_path": registry_path,
        "registry_sha256": registry_sha256 or None,
        "toolbar_results": toolbar_results,
        "command_mappings": command_mappings,
        "mapped_command_ids": sorted(
            str(item["command_id"]) for item in command_mappings
        ),
        "invocation_ready_command_ids": sorted(
            str(item["command_id"])
            for item in command_mappings
            if item.get("invocation_ready") is True
        ),
        "block_reasons": _unique_strings(all_block_reasons),
    }


def _verified_anonymous_recipe_targets(
    execution_recipe: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect anonymous command targets embedded in one prepared recipe."""

    targets: list[dict[str, Any]] = []
    for field in ("accessibility_target", "movement_accessibility_target"):
        target = execution_recipe.get(field)
        if (
            isinstance(target, dict)
            and target.get("target_kind")
            == "verified_anonymous_toolbar_child"
        ):
            targets.append(target)
    return targets


def _normalize_view_replay_accessibility_command_uses(
    value: list[dict[str, Any]] | None,
    *,
    execution_recipe: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Bind actual anonymous-control invocations to prepared semantic targets."""

    expected_targets = _verified_anonymous_recipe_targets(execution_recipe)
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        raise GuiError(
            "accessibility_command_uses must contain one or two command-use receipts"
        )
    if not expected_targets:
        raise GuiError(
            "accessibility_command_uses are allowed only for a verified anonymous "
            "toolbar recipe"
        )
    expected_by_command = {
        str(target.get("command_id")): target for target in expected_targets
    }
    normalized: list[dict[str, Any]] = []
    seen_command_ids: set[str] = set()
    exact_fields = (
        "toolbar_name",
        "toolbar_automation_id",
        "registry_toolbar_name",
        "zero_based_child_index",
        "element_index",
        "registry_sha256",
        "semantic_mapping_sha256",
    )
    for index, raw_item in enumerate(value, start=1):
        if not isinstance(raw_item, dict):
            raise GuiError(
                f"accessibility_command_uses[{index}] must be a JSON object"
            )
        extra_fields = sorted(
            set(raw_item) - VIEW_REPLAY_ACCESSIBILITY_COMMAND_USE_FIELDS
        )
        if extra_fields:
            raise GuiError(
                f"accessibility_command_uses[{index}] contains unsupported fields: "
                + ", ".join(extra_fields)
            )
        missing_fields = sorted(
            VIEW_REPLAY_ACCESSIBILITY_COMMAND_USE_FIELDS - set(raw_item)
        )
        if missing_fields:
            raise GuiError(
                f"accessibility_command_uses[{index}] is missing required fields: "
                + ", ".join(missing_fields)
            )
        command_id = str(raw_item.get("command_id") or "").strip()
        if command_id in seen_command_ids:
            raise GuiError("accessibility_command_uses contains a duplicate command_id")
        seen_command_ids.add(command_id)
        expected = expected_by_command.get(command_id)
        if expected is None:
            raise GuiError(
                f"accessibility_command_uses[{index}] command is not present in the "
                "prepared anonymous toolbar recipe"
            )
        item = dict(raw_item)
        item["registry_sha256"] = str(item.get("registry_sha256") or "").lower()
        item["semantic_mapping_sha256"] = str(
            item.get("semantic_mapping_sha256") or ""
        ).lower()
        for field in exact_fields:
            if item.get(field) != expected.get(field):
                raise GuiError(
                    f"accessibility_command_uses[{index}] does not match the prepared "
                    f"{field}"
                )
        for field in ("accessibility_tree_refreshed", "invocation_succeeded"):
            if not isinstance(item.get(field), bool):
                raise GuiError(
                    f"accessibility_command_uses[{index}].{field} must be a boolean"
                )
        normalized.append(item)
    return normalized


def _normalize_reviewed_copy_script_evidence(
    value: dict[str, Any],
) -> dict[str, Any]:
    """Validate inert Copy Script content and reviewer attestations."""

    if not isinstance(value, dict):
        raise GuiError("reviewed_copy_script_evidence must be a JSON object")
    extra_fields = sorted(set(value) - REVIEWED_COPY_SCRIPT_EVIDENCE_FIELDS)
    if extra_fields:
        raise GuiError(
            "reviewed_copy_script_evidence contains unsupported fields: "
            + ", ".join(extra_fields)
        )
    missing_fields = sorted(
        (REVIEWED_COPY_SCRIPT_EVIDENCE_FIELDS - {"note"}) - set(value)
    )
    if missing_fields:
        raise GuiError(
            "reviewed_copy_script_evidence is missing required fields: "
            + ", ".join(missing_fields)
        )

    script_text = value.get("script_text")
    if not isinstance(script_text, str):
        raise GuiError("reviewed_copy_script_evidence.script_text must be a string")
    try:
        analysis = analyze_reviewed_copy_script(script_text)
    except ValueError as exc:
        raise GuiError(str(exc)) from exc

    capture_method = str(value.get("capture_method") or "").strip()
    if capture_method != "materials_studio_copy_script":
        raise GuiError("unsupported reviewed Copy Script capture method")
    reviewer = str(value.get("reviewer") or "").strip()
    if reviewer not in {"computer_use", "human_review"}:
        raise GuiError("unsupported reviewed Copy Script reviewer")
    normalized: dict[str, Any] = {
        "script_text": script_text,
        "capture_method": capture_method,
        "reviewer": reviewer,
        "analysis": analysis,
    }
    for field in (
        "copy_script_command_observed",
        "review_completed",
        "view_action_matches_manifest",
        "structure_unchanged_observed",
    ):
        item = value.get(field)
        if not isinstance(item, bool):
            raise GuiError(f"reviewed_copy_script_evidence.{field} must be a boolean")
        normalized[field] = item
    note = value.get("note")
    if note is not None:
        if not isinstance(note, str):
            raise GuiError("reviewed_copy_script_evidence.note must be a string or null")
        if len(note) > 1000:
            raise GuiError("reviewed_copy_script_evidence.note must be at most 1000 characters")
    normalized["note"] = note
    return normalized


def _normalize_miller_runtime_viewport_selection_probe(
    value: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Validate a transient-plane viewport selection probe from the live MS window."""

    if not isinstance(value, dict):
        raise GuiError("runtime_ui_evidence.viewport_selection_probe must be a JSON object")
    extra_fields = sorted(set(value) - MILLER_RUNTIME_VIEWPORT_PROBE_ALLOWED_FIELDS)
    if extra_fields:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe contains unsupported fields: "
            + ", ".join(extra_fields)
        )
    missing_fields = sorted(MILLER_RUNTIME_VIEWPORT_PROBE_FIELDS - set(value))
    if missing_fields:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe is missing required fields: "
            + ", ".join(missing_fields)
        )

    normalized = dict(value)
    for field in MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS:
        if not isinstance(normalized.get(field), bool):
            raise GuiError(
                f"runtime_ui_evidence.viewport_selection_probe.{field} must be a boolean"
            )

    for field in (
        "view_onto_popup_menu_observed",
        "view_onto_native_command_mapping_verified",
    ):
        if not isinstance(normalized.get(field), bool):
            raise GuiError(
                f"runtime_ui_evidence.viewport_selection_probe.{field} must be a boolean"
            )

    probe_indices = _normalize_miller_plane_indices(
        normalized.get("probe_miller_indices"),
        field_name="runtime_ui_evidence.viewport_selection_probe.probe_miller_indices",
    )
    dialog_indices = _normalize_miller_plane_indices(
        normalized.get("dialog_miller_indices"),
        field_name="runtime_ui_evidence.viewport_selection_probe.dialog_miller_indices",
    )
    if len(dialog_indices) != 3:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe.dialog_miller_indices must contain "
            "exactly three values"
        )
    normalized["probe_miller_indices"] = probe_indices
    normalized["dialog_miller_indices"] = dialog_indices

    for field in (
        "selection_method",
        "hit_test_basis",
        "properties_filter",
        "properties_miller_label",
        "view_onto_command_id",
    ):
        if not isinstance(normalized.get(field), str):
            raise GuiError(
                f"runtime_ui_evidence.viewport_selection_probe.{field} must be a string"
            )
        normalized[field] = str(normalized[field]).strip()

    artifact_path = Path(str(normalized.get("structure_artifact_path") or "")).expanduser().resolve()
    _ensure_inside(workspace_root, artifact_path)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise GuiError(f"runtime viewport probe structure artifact does not exist: {artifact_path}")
    before_hash = str(normalized.get("structure_artifact_sha256_before") or "").strip().lower()
    after_hash = str(normalized.get("structure_artifact_sha256_after") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", before_hash) is None:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe.structure_artifact_sha256_before "
            "must be SHA-256"
        )
    if re.fullmatch(r"[0-9a-f]{64}", after_hash) is None:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe.structure_artifact_sha256_after "
            "must be SHA-256"
        )
    current_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    normalized["structure_artifact_path"] = str(artifact_path)
    normalized["structure_artifact_sha256_before"] = before_hash
    normalized["structure_artifact_sha256_after"] = after_hash
    normalized["structure_artifact_sha256_current"] = current_hash

    raw_undo_labels = normalized.get("undo_labels_observed")
    if not isinstance(raw_undo_labels, list) or not 2 <= len(raw_undo_labels) <= 8:
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe.undo_labels_observed must contain "
            "2 to 8 labels"
        )
    undo_labels = [str(item).strip() for item in raw_undo_labels]
    if any(
        not any(pattern.fullmatch(label) for pattern in MILLER_RUNTIME_VIEWPORT_PROBE_UNDO_LABEL_PATTERNS)
        for label in undo_labels
    ):
        raise GuiError(
            "runtime_ui_evidence.viewport_selection_probe.undo_labels_observed contains a "
            "non-whitelisted undo"
        )
    normalized["undo_labels_observed"] = undo_labels

    block_reasons: list[str] = []
    if normalized["selection_method"] != MILLER_PLANE_VIEWPORT_SELECTION_METHOD:
        block_reasons.append("runtime_viewport_selection_method_mismatch")
    if dialog_indices != _miller_plane_dialog_indices(probe_indices):
        block_reasons.append("runtime_viewport_probe_dialog_indices_mismatch")
    for field in MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS:
        if normalized.get(field) is not True:
            block_reasons.append(f"runtime_viewport_{field}_not_verified")
    if normalized["hit_test_basis"] != MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS:
        block_reasons.append("runtime_viewport_hit_test_basis_mismatch")
    if normalized["properties_filter"] != "Miller Plane":
        block_reasons.append("runtime_viewport_properties_filter_mismatch")
    if normalized["properties_miller_label"] != _miller_plane_label(dialog_indices):
        block_reasons.append("runtime_viewport_properties_miller_label_mismatch")
    if normalized["view_onto_command_id"] != "cmdViewer3DViewOnto":
        block_reasons.append("runtime_viewport_view_onto_command_id_mismatch")
    if not (
        normalized["view_onto_popup_menu_observed"] is True
        or normalized["view_onto_native_command_mapping_verified"] is True
    ):
        block_reasons.append("runtime_viewport_view_onto_target_not_verified")
    if before_hash != after_hash:
        block_reasons.append("runtime_viewport_structure_artifact_hash_changed")
    if after_hash != current_hash:
        block_reasons.append("runtime_viewport_structure_artifact_hash_not_current")
    if "Undo View Onto Miller Plane" not in undo_labels:
        block_reasons.append("runtime_viewport_view_onto_undo_not_observed")
    if "Undo Create Miller Plane" not in undo_labels:
        block_reasons.append("runtime_viewport_create_plane_undo_not_observed")
    normalized["block_reasons"] = _unique_strings(block_reasons)
    normalized["complete"] = not block_reasons
    return normalized


def _normalize_miller_runtime_ui_evidence(
    value: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Validate the exact runtime UI observation accepted by replay preparation."""

    if not isinstance(value, dict):
        raise GuiError("runtime_ui_evidence must be a JSON object")
    extra_fields = sorted(set(value) - MILLER_RUNTIME_UI_EVIDENCE_FIELDS)
    if extra_fields:
        raise GuiError(
            "runtime_ui_evidence contains unsupported fields: " + ", ".join(extra_fields)
        )
    required_fields = {
        "source",
        "expected_revision",
        "expected_window_handle",
        "expected_window_title",
        *MILLER_RUNTIME_UI_BOOLEAN_FIELDS,
        "miller_planes_menu_key_sequence",
        *MILLER_RUNTIME_UI_EXPECTED_IDENTIFIERS,
        "selection_modifier_keys",
    }
    missing_fields = sorted(required_fields - set(value))
    if missing_fields:
        raise GuiError(
            "runtime_ui_evidence is missing required fields: " + ", ".join(missing_fields)
        )

    normalized = dict(value)
    source = str(normalized.get("source") or "").strip()
    if source not in {"computer_use", "manual_review", "local_uia"}:
        raise GuiError("unsupported runtime_ui_evidence source")
    normalized["source"] = source
    try:
        expected_revision = int(normalized.get("expected_revision"))
        expected_window_handle = int(normalized.get("expected_window_handle"))
    except (TypeError, ValueError) as exc:
        raise GuiError("runtime_ui_evidence revision and window handle must be integers") from exc
    if expected_revision < 0:
        raise GuiError("runtime_ui_evidence expected_revision must be non-negative")
    if expected_window_handle <= 0:
        raise GuiError("runtime_ui_evidence expected_window_handle must be positive")
    expected_window_title = str(normalized.get("expected_window_title") or "").strip()
    if not expected_window_title:
        raise GuiError("runtime_ui_evidence expected_window_title must not be empty")
    normalized["expected_revision"] = expected_revision
    normalized["expected_window_handle"] = expected_window_handle
    normalized["expected_window_title"] = expected_window_title

    for field in MILLER_RUNTIME_UI_BOOLEAN_FIELDS:
        if not isinstance(normalized.get(field), bool):
            raise GuiError(f"runtime_ui_evidence.{field} must be a boolean")
    key_sequence = normalized.get("miller_planes_menu_key_sequence")
    if not isinstance(key_sequence, list) or not all(isinstance(item, str) for item in key_sequence):
        raise GuiError("runtime_ui_evidence.miller_planes_menu_key_sequence must be a string list")
    normalized["miller_planes_menu_key_sequence"] = list(key_sequence)
    modifier_keys = normalized.get("selection_modifier_keys")
    if not isinstance(modifier_keys, list) or not all(
        isinstance(item, str) and item in VIEW_REPLAY_MODIFIER_KEYS for item in modifier_keys
    ):
        raise GuiError(
            "runtime_ui_evidence.selection_modifier_keys must contain only Shift, Ctrl, Alt, or Win"
        )
    normalized["selection_modifier_keys"] = list(modifier_keys)
    viewport_probe = normalized.get("viewport_selection_probe")
    if viewport_probe is not None:
        normalized["viewport_selection_probe"] = (
            _normalize_miller_runtime_viewport_selection_probe(
                viewport_probe,
                workspace_root=workspace_root,
            )
        )
    for field in MILLER_RUNTIME_UI_EXPECTED_IDENTIFIERS:
        item = normalized.get(field)
        if item is not None and not isinstance(item, str):
            raise GuiError(f"runtime_ui_evidence.{field} must be a string or null")
    for field in ("screenshot_path", "note"):
        item = normalized.get(field)
        if item is not None and not isinstance(item, str):
            raise GuiError(f"runtime_ui_evidence.{field} must be a string or null")
    return normalized


def _normalize_view_replay_keyboard_stages(
    keyboard_stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and normalize staged, unmodified arrow-key replay evidence."""

    if not keyboard_stages:
        raise GuiError("keyboard_stages must contain at least one stage")
    if len(keyboard_stages) > 8:
        raise GuiError("keyboard_stages must contain at most 8 stages")
    normalized: list[dict[str, Any]] = []
    for index, stage in enumerate(keyboard_stages, start=1):
        if not isinstance(stage, dict):
            raise GuiError(f"keyboard stage {index} must be a JSON object")
        extra_fields = sorted(set(stage) - VIEW_REPLAY_KEYBOARD_STAGE_FIELDS)
        if extra_fields:
            raise GuiError(
                f"keyboard stage {index} contains unsupported fields: " + ", ".join(extra_fields)
            )
        missing_fields = sorted(VIEW_REPLAY_KEYBOARD_STAGE_FIELDS - set(stage))
        if missing_fields:
            raise GuiError(
                f"keyboard stage {index} is missing required fields: " + ", ".join(missing_fields)
            )
        try:
            increment = float(stage["rotation_increment_degrees"])
        except (TypeError, ValueError) as exc:
            raise GuiError(f"keyboard stage {index} has an invalid rotation increment") from exc
        if not 0.0 < increment <= 360.0:
            raise GuiError(f"keyboard stage {index} rotation increment must be in (0, 360]")
        raw_sequence = stage["key_sequence"]
        if not isinstance(raw_sequence, list) or not raw_sequence:
            raise GuiError(f"keyboard stage {index} key_sequence must contain arrow keys")
        if len(raw_sequence) > 16:
            raise GuiError(f"keyboard stage {index} key_sequence must contain at most 16 keys")
        sequence = [str(item).strip() for item in raw_sequence]
        invalid_keys = [item for item in sequence if item not in VIEW_REPLAY_ARROW_KEYS]
        if invalid_keys:
            raise GuiError(
                f"keyboard stage {index} contains unsupported keys: " + ", ".join(invalid_keys)
            )
        raw_modifiers = stage["modifier_keys"]
        if not isinstance(raw_modifiers, list):
            raise GuiError(f"keyboard stage {index} modifier_keys must be a list")
        modifiers = [str(item).strip() for item in raw_modifiers]
        invalid_modifiers = [item for item in modifiers if item not in VIEW_REPLAY_MODIFIER_KEYS]
        if invalid_modifiers:
            raise GuiError(
                f"keyboard stage {index} contains unsupported modifiers: "
                + ", ".join(invalid_modifiers)
            )
        normalized.append(
            {
                "rotation_increment_degrees": increment,
                "key_sequence": sequence,
                "modifier_keys": modifiers,
            }
        )
    return normalized


def _normalize_miller_plane_indices(
    values: Any,
    *,
    field_name: str,
) -> list[int]:
    """Normalize three-index or Miller-Bravais plane indices."""

    if not isinstance(values, (list, tuple)) or len(values) not in {3, 4}:
        raise GuiError(f"{field_name} must contain three or four integer indices")
    normalized: list[int] = []
    for value in values:
        if isinstance(value, bool):
            raise GuiError(f"{field_name} must contain integer indices")
        try:
            integer = int(value)
        except (TypeError, ValueError) as exc:
            raise GuiError(f"{field_name} must contain integer indices") from exc
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise GuiError(f"{field_name} must contain integer indices") from exc
        if float(integer) != numeric:
            raise GuiError(f"{field_name} must contain integer indices")
        if abs(integer) > 999:
            raise GuiError(f"{field_name} indices must be between -999 and 999")
        normalized.append(integer)
    if not any(normalized):
        raise GuiError(f"{field_name} cannot be the zero plane")
    if len(normalized) == 4 and normalized[2] != -(normalized[0] + normalized[1]):
        raise GuiError(
            f"{field_name} four-index Miller-Bravais values must satisfy i=-(h+k)"
        )
    return normalized


def _miller_plane_dialog_indices(indices: list[int]) -> list[int]:
    """Return the three h-k-l values accepted by the MS 20.1 dialog."""

    if len(indices) == 3:
        return list(indices)
    return [indices[0], indices[1], indices[3]]


def _miller_plane_dialog_text(indices: list[int]) -> str:
    """Return the canonical three-index text required before Create."""

    return " ".join(str(value) for value in indices)


def _miller_dialog_keyboard_correction_plan(
    observed_value: str,
    expected_value: str,
    *,
    full_replacement_attempted: bool = False,
) -> dict[str, Any]:
    """Plan an unmodified-key correction for the MS 20.1 TxtHKL control."""

    observed = str(observed_value)
    expected = str(expected_value).strip()
    if re.fullmatch(r"-?\d{1,3} -?\d{1,3} -?\d{1,3}", expected) is None:
        raise GuiError("expected Miller dialog value must be canonical 'h k l' text")

    if observed.strip() == expected:
        return {
            "strategy": "exact_trimmed_match_no_mutation",
            "focus_key": None,
            "backspace_count": 0,
            "type_text": "",
            "preserved_text": observed,
            "mutation_required": False,
            "fresh_readback_required_after_mutation": False,
        }

    if observed.endswith(expected):
        retained_prefix = observed[: -len(expected)]
        return {
            "strategy": "focus_home_delete_retained_prefix_before_expected_value",
            "focus_key": "Home",
            "delete_count": len(retained_prefix),
            "type_text": "",
            "preserved_text": expected,
            "mutation_required": True,
            "fresh_readback_required_after_mutation": True,
        }
    if observed and expected.endswith(observed):
        missing_prefix = expected[: -len(observed)]
        return {
            "strategy": "focus_home_type_missing_expected_prefix_over_retained_suffix",
            "focus_key": "Home",
            "delete_count": 0,
            "type_text": missing_prefix,
            "preserved_text": observed,
            "mutation_required": True,
            "fresh_readback_required_after_mutation": True,
        }

    shared_prefix_length = 0
    for observed_character, expected_character in zip(observed, expected):
        if observed_character != expected_character:
            break
        shared_prefix_length += 1

    if shared_prefix_length > 0:
        return {
            "strategy": "focus_end_replace_minimal_differing_suffix",
            "focus_key": "End",
            "backspace_count": len(observed) - shared_prefix_length,
            "type_text": expected[shared_prefix_length:],
            "preserved_text": observed[:shared_prefix_length],
            "mutation_required": True,
            "fresh_readback_required_after_mutation": True,
        }

    longest_common_length = 0
    longest_common_observed_start = 0
    longest_common_expected_start = 0
    for observed_start in range(len(observed)):
        for expected_start in range(len(expected)):
            common_length = 0
            while (
                observed_start + common_length < len(observed)
                and expected_start + common_length < len(expected)
                and observed[observed_start + common_length]
                == expected[expected_start + common_length]
            ):
                common_length += 1
            if common_length > longest_common_length:
                longest_common_length = common_length
                longest_common_observed_start = observed_start
                longest_common_expected_start = expected_start

    if longest_common_length > 0:
        preserved_text = observed[
            longest_common_observed_start : (
                longest_common_observed_start + longest_common_length
            )
        ]
        observed_suffix_count = len(observed) - (
            longest_common_observed_start + longest_common_length
        )
        expected_prefix = expected[:longest_common_expected_start]
        expected_suffix = expected[
            longest_common_expected_start + longest_common_length :
        ]
        common_plan = {
            "preserved_text": preserved_text,
            "observed_preserved_span": [
                longest_common_observed_start,
                longest_common_observed_start + longest_common_length,
            ],
            "expected_preserved_span": [
                longest_common_expected_start,
                longest_common_expected_start + longest_common_length,
            ],
            "mutation_required": True,
            "fresh_readback_required_after_mutation": True,
            "replan_from_fresh_readback_after_mutation": True,
        }
        if longest_common_observed_start > 0:
            return {
                **common_plan,
                "strategy": "focus_home_delete_prefix_before_longest_common_substring",
                "focus_key": "Home",
                "delete_count": longest_common_observed_start,
                "type_text": "",
            }
        if observed_suffix_count > 0:
            return {
                **common_plan,
                "strategy": "focus_end_delete_suffix_after_longest_common_substring",
                "focus_key": "End",
                "backspace_count": observed_suffix_count,
                "type_text": "",
            }
        if expected_prefix:
            return {
                **common_plan,
                "strategy": "focus_home_type_prefix_before_longest_common_substring",
                "focus_key": "Home",
                "delete_count": 0,
                "type_text": expected_prefix,
            }
        return {
            **common_plan,
            "strategy": "focus_end_type_suffix_after_longest_common_substring",
            "focus_key": "End",
            "backspace_count": 0,
            "type_text": expected_suffix,
        }

    if full_replacement_attempted:
        return {
            "strategy": "abort_unrepairable_post_full_replacement_mismatch",
            "focus_key": None,
            "delete_count": 0,
            "type_text": "",
            "preserved_text": observed,
            "mutation_required": False,
            "fresh_readback_required_after_mutation": False,
            "abort_without_create": True,
        }

    return {
        "strategy": "focus_end_backspace_observed_character_count_then_type_exact",
        "focus_key": "End",
        "backspace_count": len(observed),
        "type_text": expected,
        "preserved_text": "",
        "mutation_required": True,
        "fresh_readback_required_after_mutation": True,
    }


def _miller_plane_label(indices: list[int]) -> str:
    """Return the compact Miller label used by the Properties Explorer."""

    return "(" + "".join(str(value) for value in indices) + ")"


def _normalize_crystal_standard_view_camera_evidence(
    evidence: dict[str, Any],
    *,
    expected_camera_match_scope: str,
) -> dict[str, Any]:
    """Validate observed camera direction and native-roll evidence for a crystal view."""

    if not isinstance(evidence, dict):
        raise GuiError("crystal_camera_evidence must be a JSON object")
    extra_fields = sorted(
        set(evidence) - CRYSTAL_STANDARD_VIEW_CAMERA_EVIDENCE_FIELDS
    )
    if extra_fields:
        raise GuiError(
            "crystal_camera_evidence contains unsupported fields: "
            + ", ".join(extra_fields)
        )
    missing_fields = sorted(
        CRYSTAL_STANDARD_VIEW_CAMERA_EVIDENCE_FIELDS - set(evidence)
    )
    if missing_fields:
        raise GuiError(
            "crystal_camera_evidence is missing required fields: "
            + ", ".join(missing_fields)
        )

    camera_match_scope = str(evidence["camera_match_scope"]).strip()
    if camera_match_scope != expected_camera_match_scope:
        raise GuiError(
            "crystal_camera_evidence.camera_match_scope does not match the "
            "prepared crystal native-roll contract"
        )

    view_direction_matches_manifest = evidence["view_direction_matches_manifest"]
    native_in_plane_roll_observed = evidence["native_in_plane_roll_observed"]
    analytic_in_plane_basis_matches_manifest = evidence[
        "analytic_in_plane_basis_matches_manifest"
    ]
    for field_name, value in (
        ("view_direction_matches_manifest", view_direction_matches_manifest),
        ("native_in_plane_roll_observed", native_in_plane_roll_observed),
    ):
        if not isinstance(value, bool):
            raise GuiError(f"crystal_camera_evidence.{field_name} must be a boolean")
    if (
        analytic_in_plane_basis_matches_manifest is not None
        and not isinstance(analytic_in_plane_basis_matches_manifest, bool)
    ):
        raise GuiError(
            "crystal_camera_evidence.analytic_in_plane_basis_matches_manifest "
            "must be a boolean or null"
        )

    complete = bool(
        view_direction_matches_manifest is True
        and native_in_plane_roll_observed is True
    )
    return {
        "camera_match_scope": camera_match_scope,
        "view_direction_matches_manifest": view_direction_matches_manifest,
        "analytic_in_plane_basis_matches_manifest": (
            analytic_in_plane_basis_matches_manifest
        ),
        "native_in_plane_roll_observed": native_in_plane_roll_observed,
        "complete": complete,
    }


def _normalize_miller_plane_replay_evidence(
    evidence: dict[str, Any],
    *,
    expected_selection_method: str,
    expected_indices: list[int],
    expected_dialog_indices: list[int],
    expected_properties_label: str,
    expected_camera_match_scope: str,
    requires_direction_match: bool,
    workspace_root: Path,
) -> dict[str, Any]:
    """Validate evidence for one transient Miller-plane View Onto replay."""

    if not isinstance(evidence, dict):
        raise GuiError("miller_plane_evidence must be a JSON object")
    extra_fields = sorted(set(evidence) - MILLER_PLANE_REPLAY_EVIDENCE_FIELDS)
    if extra_fields:
        raise GuiError(
            "miller_plane_evidence contains unsupported fields: " + ", ".join(extra_fields)
        )
    required_fields = (
        MILLER_PLANE_REPLAY_EVIDENCE_FIELDS
        - MILLER_PLANE_OPTIONAL_REPLAY_EVIDENCE_FIELDS
    )
    if requires_direction_match:
        required_fields = required_fields | {"direct_lattice_direction_matches_manifest"}
    missing_fields = sorted(required_fields - set(evidence))
    if missing_fields:
        raise GuiError(
            "miller_plane_evidence is missing required fields: " + ", ".join(missing_fields)
        )

    normalized_indices = _normalize_miller_plane_indices(
        evidence["miller_plane_indices"],
        field_name="miller_plane_evidence.miller_plane_indices",
    )
    if normalized_indices != expected_indices:
        raise GuiError(
            "miller_plane_evidence.miller_plane_indices does not match the prepared view: "
            f"expected {expected_indices!r}, received {normalized_indices!r}"
        )
    normalized_dialog_indices = _normalize_miller_plane_indices(
        evidence["dialog_miller_indices"],
        field_name="miller_plane_evidence.dialog_miller_indices",
    )
    if len(normalized_dialog_indices) != 3:
        raise GuiError("miller_plane_evidence.dialog_miller_indices must contain exactly three values")
    if normalized_dialog_indices != expected_dialog_indices:
        raise GuiError(
            "miller_plane_evidence.dialog_miller_indices does not match the prepared recipe: "
            f"expected {expected_dialog_indices!r}, received {normalized_dialog_indices!r}"
        )
    expected_dialog_text = _miller_plane_dialog_text(expected_dialog_indices)
    dialog_text_before_create = str(
        evidence["dialog_miller_indices_text_before_create"]
    ).strip()
    if dialog_text_before_create != expected_dialog_text:
        raise GuiError(
            "miller_plane_evidence.dialog_miller_indices_text_before_create does not match "
            f"the prepared recipe: expected {expected_dialog_text!r}, received "
            f"{dialog_text_before_create!r}"
        )
    dialog_value_source = str(evidence["dialog_miller_indices_value_source"]).strip()
    if dialog_value_source != "fresh_modeless_child_accessibility_value":
        raise GuiError(
            "miller_plane_evidence.dialog_miller_indices_value_source must equal "
            "'fresh_modeless_child_accessibility_value'"
        )

    count_fields = (
        "created_plane_count",
        "selected_plane_count",
        "miller_plane_count_before",
        "miller_plane_count_after_create",
        "miller_plane_count_after_cleanup",
    )
    counts: dict[str, int] = {}
    for field_name in count_fields:
        value = evidence[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GuiError(f"miller_plane_evidence.{field_name} must be a non-negative integer")
        counts[field_name] = value

    selection_method = str(evidence["selection_method"]).strip()
    if selection_method not in MILLER_PLANE_SELECTION_METHODS:
        raise GuiError(
            "miller_plane_evidence.selection_method is not a prepared semantic selection method"
        )
    if selection_method != expected_selection_method:
        raise GuiError(
            "miller_plane_evidence.selection_method does not match the prepared recipe: "
            f"expected {expected_selection_method!r}, received {selection_method!r}"
        )
    raw_path_suffix = evidence.get("object_tree_path_suffix")
    path_suffix: list[str] | None = None
    viewport_selection_contract_matches = True
    viewport_fields: dict[str, Any] = {}
    if selection_method == MILLER_PLANE_SELECTION_METHOD:
        if not isinstance(raw_path_suffix, list):
            raise GuiError("miller_plane_evidence.object_tree_path_suffix must be a list")
        path_suffix = [str(value).strip() for value in raw_path_suffix]
        if path_suffix != MILLER_PLANE_OBJECT_TREE_PATH_SUFFIX:
            raise GuiError(
                "miller_plane_evidence.object_tree_path_suffix does not match the prepared recipe"
            )
    else:
        if raw_path_suffix not in (None, []):
            raise GuiError(
                "miller_plane_evidence.object_tree_path_suffix must be null or empty for the "
                "viewport selection method"
            )
        required_viewport_fields = (
            "viewport_hit_test_basis",
            "fresh_before_after_screenshots_observed",
            "unique_transient_plane_region_observed",
            "properties_selection_verified",
            "view_onto_popup_menu_observed",
            "dialog_show_set_of_parallel_planes",
            "dialog_show_symmetry_images",
        )
        missing_viewport_fields = [
            field for field in required_viewport_fields if field not in evidence
        ]
        if missing_viewport_fields:
            raise GuiError(
                "miller_plane_evidence is missing viewport selection fields: "
                + ", ".join(missing_viewport_fields)
            )
        viewport_hit_test_basis = str(evidence["viewport_hit_test_basis"]).strip()
        viewport_fields["viewport_hit_test_basis"] = viewport_hit_test_basis
        for field in required_viewport_fields[1:]:
            value = evidence[field]
            if not isinstance(value, bool):
                raise GuiError(f"miller_plane_evidence.{field} must be a boolean")
            viewport_fields[field] = value
        viewport_selection_contract_matches = bool(
            viewport_hit_test_basis == MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS
            and viewport_fields["fresh_before_after_screenshots_observed"] is True
            and viewport_fields["unique_transient_plane_region_observed"] is True
            and viewport_fields["properties_selection_verified"] is True
            and (
                viewport_fields["view_onto_popup_menu_observed"] is True
                or evidence.get("view_onto_native_command_mapping_verified") is True
            )
            and viewport_fields["dialog_show_set_of_parallel_planes"] is False
            and viewport_fields["dialog_show_symmetry_images"] is False
        )
    properties_filter = str(evidence["properties_filter"]).strip()
    if properties_filter != "Miller Plane":
        raise GuiError("miller_plane_evidence.properties_filter must equal 'Miller Plane'")
    properties_miller_label = str(evidence["properties_miller_label"]).strip()
    if properties_miller_label != expected_properties_label:
        raise GuiError(
            "miller_plane_evidence.properties_miller_label does not match the prepared dialog indices"
        )
    camera_match_scope = str(evidence["camera_match_scope"]).strip()
    if camera_match_scope != expected_camera_match_scope:
        raise GuiError(
            "miller_plane_evidence.camera_match_scope does not match the prepared native-roll contract"
        )

    booleans: dict[str, bool | None] = {}
    boolean_fields = set(MILLER_PLANE_REQUIRED_TRUE_EVIDENCE_FIELDS) | {
        "analytic_in_plane_basis_matches_manifest"
    }
    if requires_direction_match or "direct_lattice_direction_matches_manifest" in evidence:
        boolean_fields.add("direct_lattice_direction_matches_manifest")
    for field_name in boolean_fields:
        value = evidence[field_name]
        if field_name == "analytic_in_plane_basis_matches_manifest" and value is None:
            booleans[field_name] = None
            continue
        if not isinstance(value, bool):
            raise GuiError(f"miller_plane_evidence.{field_name} must be a boolean")
        booleans[field_name] = value
    booleans.setdefault("direct_lattice_direction_matches_manifest", None)
    reset_view_before_alignment = evidence.get("reset_view_before_alignment")
    if reset_view_before_alignment is not None and not isinstance(
        reset_view_before_alignment, bool
    ):
        raise GuiError(
            "miller_plane_evidence.reset_view_before_alignment must be a boolean or null"
        )

    artifact_path = Path(str(evidence["structure_artifact_path"])).expanduser().resolve()
    _ensure_inside(workspace_root, artifact_path)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise GuiError(f"Miller replay structure artifact does not exist: {artifact_path}")
    before_hash = str(evidence["structure_artifact_sha256_before"]).strip().lower()
    after_hash = str(evidence["structure_artifact_sha256_after"]).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", before_hash) is None:
        raise GuiError("miller_plane_evidence.structure_artifact_sha256_before must be SHA-256")
    if re.fullmatch(r"[0-9a-f]{64}", after_hash) is None:
        raise GuiError("miller_plane_evidence.structure_artifact_sha256_after must be SHA-256")
    current_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    raw_undo_labels = evidence["undo_labels_applied"]
    if not isinstance(raw_undo_labels, list) or not 2 <= len(raw_undo_labels) <= 16:
        raise GuiError("miller_plane_evidence.undo_labels_applied must contain 2 to 16 labels")
    undo_labels = [str(value).strip() for value in raw_undo_labels]
    if any(
        not any(pattern.fullmatch(label) for pattern in MILLER_PLANE_UNDO_LABEL_PATTERNS)
        for label in undo_labels
    ):
        raise GuiError("miller_plane_evidence.undo_labels_applied contains a non-whitelisted undo")
    view_onto_undo_present = any(label.startswith("Undo View Onto ") for label in undo_labels)
    create_plane_undo_present = "Undo Create Miller Plane" in undo_labels

    counts_match_contract = bool(
        counts["created_plane_count"] == 1
        and counts["selected_plane_count"] == 1
        and counts["miller_plane_count_after_create"]
        == counts["miller_plane_count_before"] + 1
        and counts["miller_plane_count_after_cleanup"]
        == counts["miller_plane_count_before"]
    )
    required_true_field_names = list(MILLER_PLANE_REQUIRED_TRUE_EVIDENCE_FIELDS)
    if requires_direction_match:
        required_true_field_names.append("direct_lattice_direction_matches_manifest")
    required_true_fields_match = all(
        booleans[field_name] is True
        for field_name in required_true_field_names
    )
    structure_artifact_hash_unchanged = before_hash == after_hash
    structure_artifact_hash_matches_current = after_hash == current_hash
    undo_labels_match_contract = bool(
        view_onto_undo_present and create_plane_undo_present
    )

    return {
        "miller_plane_indices": normalized_indices,
        "dialog_miller_indices": normalized_dialog_indices,
        "dialog_miller_indices_text_before_create": dialog_text_before_create,
        "dialog_miller_indices_value_source": dialog_value_source,
        **counts,
        "selection_method": selection_method,
        "object_tree_path_suffix": path_suffix,
        **viewport_fields,
        "properties_filter": properties_filter,
        "properties_miller_label": properties_miller_label,
        "camera_match_scope": camera_match_scope,
        **booleans,
        "reset_view_before_alignment": reset_view_before_alignment,
        "structure_artifact_path": str(artifact_path),
        "structure_artifact_sha256_before": before_hash,
        "structure_artifact_sha256_after": after_hash,
        "structure_artifact_sha256_current": current_hash,
        "undo_labels_applied": undo_labels,
        "counts_match_contract": counts_match_contract,
        "required_true_fields_match": required_true_fields_match,
        "structure_artifact_hash_unchanged": structure_artifact_hash_unchanged,
        "structure_artifact_hash_matches_current": structure_artifact_hash_matches_current,
        "undo_labels_match_contract": undo_labels_match_contract,
        "selection_evidence_matches_contract": viewport_selection_contract_matches,
        "complete": bool(
            counts_match_contract
            and required_true_fields_match
            and structure_artifact_hash_unchanged
            and structure_artifact_hash_matches_current
            and undo_labels_match_contract
            and viewport_selection_contract_matches
        ),
    }


def _backend_file_open_may_launch_new_instance(backend: Any) -> bool:
    """Return whether backend.open_file may spawn another Materials Studio instance."""

    return bool(getattr(backend, "file_open_may_launch_new_instance", False))


def _backend_same_window_open_callable(backend: Any) -> Any | None:
    """Return a backend same-window file opener when one is available."""

    opener = getattr(backend, "open_file_in_existing_window", None)
    return opener if callable(opener) else None


def _backend_same_window_open_supported(backend: Any) -> bool:
    """Return whether the backend can load a file into an existing GUI window."""

    if not bool(getattr(backend, "supported", False)):
        return False
    if _backend_same_window_open_callable(backend) is not None:
        return True
    return not _backend_file_open_may_launch_new_instance(backend)


def _backend_startup_dialog_open_supported(backend: Any) -> bool:
    """Return whether a backend can open a project through known startup dialogs."""

    return bool(getattr(backend, "startup_dialog_open_supported", False))


def _dismiss_backend_startup_dialogs(
    backend: Any,
    *,
    pid: int | None,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Dismiss known startup dialogs when the backend exposes a safe helper."""

    dismiss_startup = getattr(backend, "dismiss_startup_dialogs", None)
    if callable(dismiss_startup):
        return list(dismiss_startup(pid=pid, timeout_seconds=timeout_seconds))
    dismiss_file_associations = getattr(backend, "dismiss_file_association_dialogs", None)
    if callable(dismiss_file_associations):
        return list(dismiss_file_associations(pid=pid, timeout_seconds=timeout_seconds))
    return []


@dataclass(frozen=True)
class ProcessInfo:
    """进程信息。

    属性:
        name: 进程名称
        pid: 进程 ID
        title: 窗口标题
        path: 进程路径
    """

    name: str
    pid: int
    title: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回字典表示。"""
        return {
            "name": self.name,
            "pid": self.pid,
            "title": self.title,
            "path": self.path,
        }


@dataclass(frozen=True)
class WindowInfo:
    """窗口信息。

    属性:
        handle: 窗口句柄
        title: 窗口标题
        pid: 进程 ID
        process_name: 进程名称
        rect: 窗口矩形
    """

    handle: int
    title: str
    pid: int | None = None
    process_name: str = "MatStudio.exe"
    rect: tuple[int, int, int, int] | None = None
    class_name: str | None = None
    is_visible: bool | None = None
    is_minimized: bool | None = None
    is_foreground: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回字典表示。"""
        return {
            "handle": self.handle,
            "title": self.title,
            "pid": self.pid,
            "process_name": self.process_name,
            "rect": list(self.rect) if self.rect else None,
            "class_name": self.class_name,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "is_foreground": self.is_foreground,
        }


def _native_matstudio_processes() -> list[ProcessInfo]:
    """Enumerate MatStudio processes without relying on tasklist output access."""

    if os.name != "nt":
        return []

    # Some MCP hosts can start tasklist but receive "Access denied" for its
    # CSV output even though the same process is visible to Win32 APIs. The
    # Toolhelp snapshot is read-only and keeps the single-window guard fail
    # closed in that environment.
    try:
        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_uint32),
                ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_int32),
                ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        create_snapshot.restype = ctypes.c_void_p
        process32_first = kernel32.Process32FirstW
        process32_first.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
        process32_first.restype = ctypes.c_int
        process32_next = kernel32.Process32NextW
        process32_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
        process32_next.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        snapshot = create_snapshot(0x00000002, 0)
        snapshot_value = snapshot.value if isinstance(snapshot, ctypes.c_void_p) else int(snapshot or 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot_value or snapshot_value == invalid_handle:
            return []

        processes: list[ProcessInfo] = []
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(ProcessEntry32W)
            if not process32_first(ctypes.c_void_p(snapshot_value), ctypes.byref(entry)):
                return []
            while True:
                if entry.szExeFile.lower() == "matstudio.exe":
                    processes.append(
                        ProcessInfo(
                            name=entry.szExeFile,
                            pid=int(entry.th32ProcessID),
                        )
                    )
                if not process32_next(ctypes.c_void_p(snapshot_value), ctypes.byref(entry)):
                    break
        finally:
            close_handle(ctypes.c_void_p(snapshot_value))
        return processes
    except Exception:
        return []


class GuiBackend(Protocol):
    """GUI 后端协议。"""

    supported: bool
    unavailable_reason: str | None
    file_open_may_launch_new_instance: bool
    startup_dialog_open_supported: bool

    def list_processes(self) -> list[ProcessInfo]:
        """列出进程。"""
        ...

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        """List Materials Studio windows."""
        ...

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        """查找窗口。"""
        ...

    def activate_window(self, window: WindowInfo) -> bool:
        """激活窗口。"""
        ...

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        """捕获窗口。"""
        ...

    def open_file(self, path: Path) -> dict[str, Any]:
        """打开文件。"""
        ...

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict[str, Any]:
        """Open a file through an already running Materials Studio window."""
        ...

    def launch_app(self) -> dict[str, Any]:
        """Launch Materials Studio without opening a structure file."""
        ...


class NullGuiBackend:
    """空 GUI 后端，用于非 Windows 系统。"""

    supported = False
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False
    unavailable_reason = "本地 GUI 回退仅在 Windows 上可用。"

    def list_processes(self) -> list[ProcessInfo]:
        """列出进程。"""
        return []

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        """List Materials Studio windows."""
        return []

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        """查找窗口。"""
        return None

    def activate_window(self, window: WindowInfo) -> bool:
        """激活窗口。"""
        return False

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        """捕获窗口。"""
        raise GuiError(self.unavailable_reason or "GUI 后端不可用。")

    def open_file(self, path: Path) -> dict[str, Any]:
        """打开文件。"""
        raise GuiError(self.unavailable_reason or "GUI 后端不可用。")
    def launch_app(self) -> dict[str, Any]:
        """Launch Materials Studio without opening a structure file."""
        raise GuiError(self.unavailable_reason or "GUI backend is unavailable.")

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict[str, Any]:
        """Open a file through an existing Materials Studio window."""
        raise GuiError(self.unavailable_reason or "GUI backend is unavailable.")


class WindowsGuiBackend:
    """小型纯标准库 Windows GUI 后端。"""

    supported = os.name == "nt"
    file_open_may_launch_new_instance = True
    startup_dialog_open_supported = True

    def __init__(
        self,
        *,
        trusted_write_workspace_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self.trusted_write_workspace_roots = tuple(
            root.expanduser().resolve()
            for root in (trusted_write_workspace_roots or ())
        )

    def configure_trusted_write_workspace_roots(
        self,
        roots: tuple[Path, ...],
    ) -> None:
        """Bind write authorization to controller-owned workspace roots."""

        self.trusted_write_workspace_roots = tuple(
            root.expanduser().resolve()
            for root in roots
        )
    unavailable_reason = None if supported else "本地 GUI 回退仅在 Windows 上可用。"

    def list_processes(self) -> list[ProcessInfo]:
        """列出进程。"""
        if not self.supported:
            return []
        processes: list[ProcessInfo] = []
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MatStudio.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            completed = None

        if completed is not None and completed.returncode == 0:
            rows = csv.reader(line for line in completed.stdout.splitlines() if line.strip())
            for row in rows:
                if len(row) < 2 or row[0].upper().startswith("INFO:"):
                    continue
                try:
                    pid = int(row[1])
                except ValueError:
                    continue
                if row[0].lower() == "matstudio.exe":
                    processes.append(ProcessInfo(name=row[0], pid=pid))
            if processes:
                return processes

        return _native_matstudio_processes()

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        """查找窗口。"""
        if not self.supported:
            return None
        user32 = ctypes.windll.user32
        candidates: list[WindowInfo] = []
        foreground_handle = _foreground_window_handle()
        pids = {process.pid for process in self.list_processes()}
        if pid is not None:
            pids = {pid}

        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            pid_value = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
            window_pid = int(pid_value.value)
            if pid is not None and window_pid != pid:
                return True
            title_match = "Materials Studio" in title or "MatStudio" in title
            pid_match = window_pid in pids if pids else False
            if title_match or pid_match:
                rect = _window_rect(hwnd)
                candidates.append(
                    WindowInfo(
                        handle=int(hwnd),
                        title=title,
                        pid=window_pid,
                        rect=rect,
                        class_name=_window_class(hwnd),
                        is_visible=True,
                        is_minimized=bool(user32.IsIconic(hwnd)),
                        is_foreground=(
                            int(hwnd) == foreground_handle if foreground_handle is not None else None
                        ),
                    )
                )
            return True

        user32.EnumWindows(enum_proc_type(enum_proc), 0)
        if not candidates:
            return None
        return sorted(candidates, key=lambda window: _window_priority(window, foreground_handle=foreground_handle))[0]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        """List visible Materials Studio top-level windows."""

        if not self.supported:
            return []
        user32 = ctypes.windll.user32
        candidates: list[WindowInfo] = []
        foreground_handle = _foreground_window_handle()
        pids = {process.pid for process in self.list_processes()}
        if pid is not None:
            pids = {pid}

        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            pid_value = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
            window_pid = int(pid_value.value)
            if pid is not None and window_pid != pid:
                return True
            title_match = "Materials Studio" in title or "MatStudio" in title
            pid_match = window_pid in pids if pids else False
            if title_match or pid_match:
                candidates.append(
                    WindowInfo(
                        handle=int(hwnd),
                        title=title,
                        pid=window_pid,
                        rect=_window_rect(hwnd),
                        class_name=_window_class(hwnd),
                        is_visible=True,
                        is_minimized=bool(user32.IsIconic(hwnd)),
                        is_foreground=(
                            int(hwnd) == foreground_handle if foreground_handle is not None else None
                        ),
                    )
                )
            return True

        user32.EnumWindows(enum_proc_type(enum_proc), 0)
        return sorted(candidates, key=lambda window: _window_priority(window, foreground_handle=foreground_handle))

    def activate_window(self, window: WindowInfo) -> bool:
        """激活窗口。"""
        if not self.supported:
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = ctypes.c_void_p(window.handle)

        def is_foreground() -> bool:
            try:
                return int(user32.GetForegroundWindow()) == int(window.handle)
            except Exception:
                return False

        def set_foreground() -> bool:
            try:
                return bool(user32.SetForegroundWindow(hwnd)) or is_foreground()
            except Exception:
                return is_foreground()

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        if set_foreground():
            return True

        # Materials Studio windows opened by helper processes can reject a
        # direct foreground request. Pulse topmost and retry before giving up.
        swp_flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, swp_flags)  # HWND_TOPMOST
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, swp_flags)  # HWND_NOTOPMOST
        user32.BringWindowToTop(hwnd)
        if set_foreground():
            return True

        current_thread = int(kernel32.GetCurrentThreadId())
        foreground_handle = int(user32.GetForegroundWindow())
        foreground_thread = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground_handle), None)) if foreground_handle else 0
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
        attached_target = False
        attached_foreground = False
        try:
            if target_thread and target_thread != current_thread:
                attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            if foreground_thread and foreground_thread not in {current_thread, target_thread}:
                attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
            if set_foreground():
                return True
        finally:
            if attached_foreground and foreground_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target and target_thread:
                user32.AttachThreadInput(current_thread, target_thread, False)

        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass
        return is_foreground()

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        """捕获窗口。"""
        if not self.supported:
            raise GuiError(self.unavailable_reason or "GUI 后端不可用。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _capture_window_bmp(window.handle, output_path)
        return output_path

    def open_file(self, path: Path) -> dict[str, Any]:
        """打开文件。"""
        if not self.supported:
            raise GuiError(self.unavailable_reason or "GUI 后端不可用。")
        if not path.exists() or not path.is_file():
            raise GuiError(f"结构文件不存在: {path}")
        matstudio = _resolve_matstudio_exe()
        if matstudio is not None:
            process = subprocess.Popen(
                [str(matstudio), str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
            return {"method": "MatStudio.exe", "executable": str(matstudio), "path": str(path), "pid": process.pid}
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return {"method": "os.startfile", "path": str(path)}
        raise GuiError("此 Python 运行时不可用 os.startfile。")

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict[str, Any]:
        """Open a file via the existing Materials Studio window's File/Open dialog."""

        if not self.supported:
            raise GuiError(self.unavailable_reason or "GUI backend is unavailable.")
        if not path.exists() or not path.is_file():
            raise GuiError(f"Structure file does not exist: {path}")
        if window.handle <= 0:
            raise GuiError("Existing Materials Studio window handle is invalid.")
        source_wrapper_provenance = _source_wrapper_auto_save_provenance(
            source_window=window,
            target_project_path=path,
            trusted_workspace_roots=self.trusted_write_workspace_roots,
        )
        before_pids = {process.pid for process in self.list_processes()}
        pre_dismissed_dialogs = self.dismiss_startup_dialogs(pid=window.pid, timeout_seconds=2.0)
        if not self.activate_window(window):
            raise GuiError("Could not activate the existing Materials Studio window for same-window open.")

        startup_open = _open_project_from_startup_dialogs(
            pid=window.pid,
            source_window=window,
            path=path,
            source_wrapper_provenance=source_wrapper_provenance,
        )
        if startup_open is not None:
            after_pids = {process.pid for process in self.list_processes()}
            spawned_pids = sorted(after_pids - before_pids)
            return {
                **startup_open,
                "process_count_before": len(before_pids),
                "process_count_after": len(after_pids),
                "spawned_process_ids": spawned_pids,
                "same_window_open_requested": True,
                "pre_dismissed_dialogs": pre_dismissed_dialogs,
            }

        _send_ctrl_open_shortcut()
        pre_open_prompts = _resolve_same_window_pre_open_prompts(
            pid=window.pid,
            source_window=window,
            source_wrapper_provenance=source_wrapper_provenance,
            timeout_seconds=30.0,
        )
        dialog = _find_file_open_dialog(
            pid=window.pid,
            timeout_seconds=10.0,
            owner_root_handle=window.handle,
        )
        if dialog is None:
            raise GuiError(
                "The existing Materials Studio window did not expose a File/Open dialog after Ctrl+O. "
                "No new MatStudio.exe was launched; use Computer Use or manual File > Open in the same "
                "window, then snapshot/audit the project."
            )

        dialog_owner_chain = _window_owner_chain(dialog.handle)
        submission = _submit_current_file_open_dialog(
            pid=window.pid,
            owner_root_handle=window.handle,
            initial_dialog=dialog,
            expected_path=str(path),
        )
        path_binding = submission.get("path_binding")
        field_result = (
            path_binding.get("filename_field")
            if isinstance(path_binding, dict)
            else None
        )
        dialog_closed = bool(submission.get("dialogs_absent"))
        handled_prompts = _resolve_same_window_open_prompts(
            pid=window.pid,
            source_window=window,
            path_text=str(path),
            source_wrapper_provenance=source_wrapper_provenance,
            timeout_seconds=60.0,
        )
        expected_window = _wait_for_project_window(
            pid=window.pid,
            expected_project_name=path.stem,
            timeout_seconds=30.0,
        )
        if expected_window is None:
            raise GuiError(
                "The File/Open dialog closed, but the exact MCP wrapper project did not become visible "
                "in the requested Materials Studio process."
            )
        after_pids = {process.pid for process in self.list_processes()}
        spawned_pids = sorted(after_pids - before_pids)
        return {
            "dialog_protocol_schema_version": 2,
            "method": "existing_window_file_open_dialog",
            "path": str(path),
            "window": window.to_dict(),
            "dialog": dialog.to_dict(),
            "dialog_owner_chain": dialog_owner_chain,
            "filename_field": field_result,
            "path_binding": path_binding,
            "dialog_submission": submission,
            "pre_open_prompts": pre_open_prompts,
            "handled_prompts": handled_prompts,
            "dialog_closed": dialog_closed,
            "expected_project_window": expected_window.to_dict(),
            "process_count_before": len(before_pids),
            "process_count_after": len(after_pids),
            "spawned_process_ids": spawned_pids,
            "same_window_open_requested": True,
            "pre_dismissed_dialogs": pre_dismissed_dialogs,
            "source_wrapper_provenance": source_wrapper_provenance,
        }

    def launch_app(self) -> dict[str, Any]:
        """Launch Materials Studio without opening a structure file."""

        if not self.supported:
            raise GuiError(self.unavailable_reason or "GUI backend is unavailable.")
        matstudio = _resolve_matstudio_exe()
        if matstudio is None:
            raise GuiError("未找到 MatStudio.exe，无法启动空白 Materials Studio 会话。")
        process = subprocess.Popen(
            [str(matstudio)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return {"method": "MatStudio.exe", "executable": str(matstudio), "pid": process.pid}


    def dismiss_file_association_dialogs(
        self,
        *,
        pid: int | None = None,
        timeout_seconds: float = 8.0,
    ) -> list[dict[str, Any]]:
        """Dismiss Materials Studio first-run file association dialogs."""

        return self.dismiss_startup_dialogs(
            pid=pid,
            timeout_seconds=timeout_seconds,
            titles=("Materials Studio File Associations",),
        )

    def dismiss_startup_dialogs(
        self,
        *,
        pid: int | None = None,
        timeout_seconds: float = 8.0,
        titles: tuple[str, ...] = ("Materials Studio File Associations",),
    ) -> list[dict[str, Any]]:
        """Dismiss known Materials Studio startup dialogs."""

        if not self.supported:
            return []
        dismissed: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            dialogs: list[WindowInfo] = []
            for title in titles:
                dialogs.extend(_find_windows(title=title, pid=pid))
            if not dialogs:
                if dismissed:
                    break
                time.sleep(0.25)
                continue
            for dialog in dialogs:
                is_file_association = dialog.title == "Materials Studio File Associations"
                owner_chain = _window_owner_chain(dialog.handle)
                cancellation = _cancel_dialog(
                    dialog.handle,
                    pid=dialog.pid if dialog.pid is not None else pid,
                    owner_root_handle=owner_chain[-1] if owner_chain else None,
                    dialog_title=dialog.title,
                    timeout_seconds=max(2.0, min(timeout_seconds, 5.0)),
                )
                submission = cancellation["submission"]
                action = (
                    "cancel_file_association_dialog"
                    if is_file_association
                    else "cancel_known_startup_dialog"
                )
                closed = cancellation["closed"]
                dismissed.append(
                    {
                        **dialog.to_dict(),
                        "action": action,
                        "submission": submission,
                        "closed": closed,
                        "cancellation": cancellation,
                    }
                )
                if not closed:
                    raise GuiError(
                        f"Known Materials Studio startup dialog did not close: {dialog.title}"
                    )
        return dismissed


def _lock_file_descriptor_nonblocking(file_descriptor: int) -> None:
    """Acquire one kernel-managed advisory byte lock without blocking."""

    os.lseek(file_descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file_descriptor(file_descriptor: int) -> None:
    """Release the platform advisory lock held by this descriptor."""

    os.lseek(file_descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(file_descriptor, fcntl.LOCK_UN)


def _workspace_advisory_lock_status(
    path: Path,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Probe an existing advisory lock without creating or modifying it."""

    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        resolved = path.expanduser().resolve()
        _ensure_inside(workspace_root, resolved)
    except (GuiError, OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "invalid_path",
            "path": str(path),
            "exists": False,
            "active": None,
            "observed_at": observed_at,
            "error": str(exc),
        }
    if not resolved.exists():
        return {
            "status": "missing",
            "path": str(resolved),
            "exists": False,
            "active": False,
            "observed_at": observed_at,
            "error": None,
        }
    file_descriptor: int | None = None
    acquired = False
    try:
        file_descriptor = os.open(resolved, os.O_RDWR)
        if os.fstat(file_descriptor).st_size < 1:
            return {
                "status": "uninitialized",
                "path": str(resolved),
                "exists": True,
                "active": None,
                "observed_at": observed_at,
                "error": "advisory lock file has no lockable byte",
            }
        try:
            _lock_file_descriptor_nonblocking(file_descriptor)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return {
                    "status": "active",
                    "path": str(resolved),
                    "exists": True,
                    "active": True,
                    "observed_at": observed_at,
                    "error": None,
                }
            raise
        acquired = True
        return {
            "status": "inactive",
            "path": str(resolved),
            "exists": True,
            "active": False,
            "observed_at": observed_at,
            "error": None,
        }
    except OSError as exc:
        return {
            "status": "unreadable",
            "path": str(resolved),
            "exists": True,
            "active": None,
            "observed_at": observed_at,
            "error": str(exc),
        }
    finally:
        if file_descriptor is not None:
            if acquired:
                try:
                    _unlock_file_descriptor(file_descriptor)
                except OSError:
                    pass
            os.close(file_descriptor)


@contextmanager
def _workspace_advisory_write_lock(
    path: Path,
    *,
    workspace_root: Path,
    timeout_seconds: float,
    poll_seconds: float,
):
    """Serialize one workspace write domain across threads and processes."""

    resolved = path.expanduser().resolve()
    _ensure_inside(workspace_root, resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(resolved, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    started = time.monotonic()
    try:
        if os.fstat(file_descriptor).st_size == 0:
            os.write(file_descriptor, b"\0")
            os.fsync(file_descriptor)
        deadline = started + max(float(timeout_seconds), 0.0)
        while True:
            try:
                _lock_file_descriptor_nonblocking(file_descriptor)
            except OSError as exc:
                busy = exc.errno in {errno.EACCES, errno.EAGAIN}
                if not busy:
                    raise GuiError(
                        f"view replay write lock could not be acquired: {resolved}"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise GuiError(
                        "workspace write transaction is busy; retry after the current "
                        "operation completes"
                    ) from exc
                time.sleep(max(float(poll_seconds), 0.001))
                continue
            acquired = True
            break
        yield {
            "path": str(resolved),
            "scope": "project_revision",
            "waited_seconds": round(time.monotonic() - started, 6),
            "timeout_seconds": float(timeout_seconds),
            "poll_seconds": float(poll_seconds),
        }
    finally:
        if acquired:
            try:
                _unlock_file_descriptor(file_descriptor)
            except OSError:
                pass
        os.close(file_descriptor)


@contextmanager
def _view_replay_write_lock(
    path: Path,
    *,
    workspace_root: Path,
    timeout_seconds: float,
):
    """Apply the shared advisory lock to one replay manifest write domain."""

    try:
        with _workspace_advisory_write_lock(
            path,
            workspace_root=workspace_root,
            timeout_seconds=timeout_seconds,
            poll_seconds=VIEW_REPLAY_WRITE_LOCK_POLL_SECONDS,
        ) as transaction:
            yield transaction
    except GuiError as exc:
        if "workspace write transaction is busy" in str(exc):
            raise GuiError(
                "view replay write transaction is busy; retry after the current "
                "prepare or record operation completes"
            ) from exc
        raise


def _serialize_view_replay_write(method: Any) -> Any:
    """Run one manifest-mutating controller method under its revision lock."""

    @wraps(method)
    def wrapped(
        self: "MaterialsStudioGuiController",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        project_id = kwargs.get("project_id")
        revision = kwargs.get("revision")
        if project_id is None or revision is None:
            raise GuiError(
                "view replay write serialization requires project_id and revision"
            )
        safe_project = sanitize_project_id(str(project_id))
        manifest_path = self._view_replay_manifest_path(
            project_id=safe_project,
            revision=int(revision),
        )
        lock_path = manifest_path.with_name("gui_view_replay_transaction.lock")
        with _view_replay_write_lock(
            lock_path,
            workspace_root=self.workspace_root,
            timeout_seconds=VIEW_REPLAY_WRITE_LOCK_TIMEOUT_SECONDS,
        ) as transaction:
            result = method(self, *args, **kwargs)
        if isinstance(result, dict):
            result["write_transaction"] = transaction
        return result

    return wrapped


class MaterialsStudioGuiController:
    """MCP 工具使用的高级 GUI 会话助手。"""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        backend: GuiBackend | None = None,
        view_replay_backend: ViewReplayAutomationBackend | None = None,
    ) -> None:
        """初始化 GUI 控制器。

        参数:
            workspace_root: 工作区根目录
            backend: GUI 后端
        """
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else default_workspace_root()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.trusted_wrapper_workspace_roots = _trusted_wrapper_workspace_roots(self.workspace_root)
        if backend is not None:
            self.backend = backend
        elif os.environ.get(GUI_BACKEND_ENV, "").strip().lower() == "null":
            self.backend = NullGuiBackend()
        else:
            self.backend = WindowsGuiBackend() if os.name == "nt" else NullGuiBackend()
        if isinstance(self.backend, WindowsGuiBackend):
            self.backend.configure_trusted_write_workspace_roots(
                (self.workspace_root,)
            )
        self.view_replay_backend = view_replay_backend or PywinautoViewReplayBackend(
            window_capture_fn=_capture_window_bmp,
        )

    def status(self, *, project_id: str | None = None, revision: int | None = None) -> dict[str, Any]:
        """返回状态。"""
        processes = self.backend.list_processes() if self.backend.supported else []
        list_windows = getattr(self.backend, "list_windows", None)
        windows = list_windows() if self.backend.supported and callable(list_windows) else []
        discovered_window = (
            self.backend.find_window() if self.backend.supported else None
        )
        if discovered_window is not None and not any(
            item.handle == discovered_window.handle for item in windows
        ):
            windows = [discovered_window, *windows]
        window = _select_live_matstudio_window(
            processes=processes,
            windows=windows,
            preferred=discovered_window,
        )
        window_inventory = self._window_inventory(windows, selected_window=window)
        matstudio_exe = _resolve_matstudio_exe()
        open_strategy = "MatStudio.exe" if matstudio_exe is not None else ("os.startfile" if hasattr(os, "startfile") else None)
        file_open_may_launch_new_instance = _backend_file_open_may_launch_new_instance(self.backend)
        same_window_open_supported = _backend_same_window_open_supported(self.backend)
        target_resolution: dict[str, Any] | None = None
        target_window: WindowInfo | None = None
        if project_id is not None or revision is not None:
            target_window, target_resolution = self._resolve_target_window(project_id=project_id, revision=revision)
        wrapper_window_count = sum(1 for item in window_inventory if item.get("project_wrapper_metadata"))
        window_management = _window_management_receipt(
            controller_workspace_root=self.workspace_root,
            processes=processes,
            window_inventory=window_inventory,
            selected_window=window,
            target_window=target_window if project_id is not None or revision is not None else window,
            target_resolution=target_resolution,
            requested_project_id=project_id,
            requested_revision=revision,
            same_window_open_supported=same_window_open_supported,
            file_open_may_launch_new_instance=file_open_may_launch_new_instance,
            startup_dialog_open_supported=_backend_startup_dialog_open_supported(self.backend),
        )
        single_window_violation_reasons = list(window_management.get("single_window_violation_reasons") or [])
        status = str(window_management.get("status") or "")
        ready_for_same_window_open = bool(window_management.get("ready_for_same_window_open"))
        can_apply_current_revision_without_new_window = bool(
            window_management.get("can_apply_current_revision_without_new_window")
        )
        ready_for_next_live_edit = bool(window_management.get("ready_for_next_live_edit"))
        recommended_tool = window_management.get("recommended_tool")
        recommended_action = window_management.get("recommended_action")
        local_uia_implementation = local_uia_view_replay_implementation_contract()
        local_uia_supported = bool(self.view_replay_backend.supported)
        local_uia_miller_supported = bool(
            getattr(
                self.view_replay_backend,
                "miller_plane_transaction_supported",
                False,
            )
        )
        return {
            "ok": self.backend.supported,
            "supported": self.backend.supported,
            "unavailable_reason": self.backend.unavailable_reason,
            "status": status,
            "recommended_tool": recommended_tool,
            "recommended_action": recommended_action,
            "process_found": bool(processes),
            "process_count": len(processes),
            "processes": [process.to_dict() for process in processes],
            "window_found": window is not None,
            "window": window.to_dict() if window else None,
            "window_count": len(window_inventory),
            "matstudio_window_count": window_management.get(
                "matstudio_window_count"
            ),
            "ignored_non_matstudio_window_count": window_management.get(
                "ignored_non_matstudio_window_count"
            ),
            "selected_window_handle": window.handle if window else None,
            "target_window_found": target_window is not None,
            "target_window": target_window.to_dict() if target_window else None,
            "windows": window_inventory,
            "target_window_resolution": target_resolution,
            "requested_project_id": project_id,
            "requested_revision": revision,
            "live_window_count": wrapper_window_count,
            "wrapper_window_count": wrapper_window_count,
            "window_management": window_management,
            "workspace_context": window_management.get("workspace_context"),
            "workspace_context_mismatch": bool(window_management.get("workspace_context_mismatch")),
            "recommended_working_dir": window_management.get("recommended_working_dir"),
            "matstudio_exe": str(matstudio_exe) if matstudio_exe else None,
            "open_strategy": open_strategy,
            "open_strategy_may_launch_new_instance": file_open_may_launch_new_instance,
            "same_window_open_supported": same_window_open_supported,
            "can_open_structure_in_existing_window": ready_for_same_window_open,
            "can_apply_current_revision_without_new_window": can_apply_current_revision_without_new_window,
            "ready_for_next_live_edit": ready_for_next_live_edit,
            "current_revision_loaded": bool(window_management.get("current_revision_loaded")),
            "needs_reload": bool(window_management.get("needs_reload")),
            "needs_activation": bool(window_management.get("needs_activation")),
            "target_window_is_visible": window_management.get("target_window_is_visible"),
            "target_window_is_minimized": window_management.get("target_window_is_minimized"),
            "target_window_foreground_observed": bool(
                window_management.get("target_window_foreground_observed")
            ),
            "target_window_is_foreground": window_management.get("target_window_is_foreground"),
            "activation_reasons": list(window_management.get("activation_reasons") or []),
            "activation_required_before_capture_or_input": bool(
                window_management.get("activation_required_before_capture_or_input")
            ),
            "needs_single_window_resolution": bool(window_management.get("needs_single_window_resolution")),
            "needs_dialog_resolution": bool(window_management.get("needs_dialog_resolution")),
            "ready_for_snapshot": bool(window_management.get("ready_for_snapshot")),
            "ready_for_open": bool(window_management.get("ready_for_open")),
            "hotload_requires_existing_window": bool(window_management.get("hotload_requires_existing_window")),
            "auto_launch_allowed": bool(window_management.get("auto_launch_allowed")),
            "reuse_existing_window_default": True,
            "single_window_policy": {
                "enabled": True,
                "scope": (
                    "global_single_instance_or_exact_project_target_process"
                ),
                "hotload_requires_existing_window": True,
                "auto_launch_during_open_allowed": False,
                "explicit_blank_session_launch_tool": "material_studio_gui_launch",
                "window_isolation_mode": window_management.get(
                    "window_isolation_mode"
                ),
                "project_scoped_multi_instance_isolation": bool(
                    window_management.get(
                        "project_scoped_multi_instance_isolation"
                    )
                ),
                "target_process_id": window_management.get(
                    "target_process_id"
                ),
                "unrelated_process_count": window_management.get(
                    "unrelated_process_count"
                ),
                "ok": not single_window_violation_reasons,
                "violation_reasons": single_window_violation_reasons,
            },
            "single_window_policy_ok": not single_window_violation_reasons,
            "single_window_violation_reasons": single_window_violation_reasons,
            "window_isolation_mode": window_management.get(
                "window_isolation_mode"
            ),
            "project_scoped_multi_instance_isolation": bool(
                window_management.get(
                    "project_scoped_multi_instance_isolation"
                )
            ),
            "target_process_id": window_management.get("target_process_id"),
            "target_window_pid_is_matstudio_process": bool(
                window_management.get(
                    "target_window_pid_is_matstudio_process"
                )
            ),
            "unrelated_process_count": window_management.get(
                "unrelated_process_count"
            ),
            "unrelated_process_ids": list(
                window_management.get("unrelated_process_ids") or []
            ),
            "can_launch_matstudio": open_strategy is not None,
            "can_launch_blank_session": matstudio_exe is not None,
            "workspace_root": str(self.workspace_root),
            "trusted_wrapper_workspace_roots": [
                {"workspace_root": str(root), "trust_basis": trust_basis}
                for root, trust_basis in self.trusted_wrapper_workspace_roots
            ],
            "screenshots_dir": str(self.workspace_root / "screenshots"),
            "local_uia_view_replay_supported": local_uia_supported,
            "local_uia_view_replay_unavailable_reason": (
                self.view_replay_backend.unavailable_reason
            ),
            "local_uia_view_replay_view_names": sorted(
                SAFE_LOCAL_VIEW_NAMES
            ),
            "local_uia_miller_plane_transaction_supported": (
                local_uia_miller_supported
            ),
            "local_uia_exact_collinear_direction_transaction_supported": (
                local_uia_miller_supported
            ),
            "local_uia_non_collinear_direction_transaction_supported": False,
            "local_uia_view_replay_implementation": local_uia_implementation,
            "local_uia_view_replay_runtime": {
                "status": (
                    "transactional_miller_available"
                    if local_uia_supported and local_uia_miller_supported
                    else "standard_and_isometric_only"
                    if local_uia_supported
                    else "unavailable"
                ),
                "backend_supported": local_uia_supported,
                "transactional_miller_supported": local_uia_miller_supported,
                "exact_collinear_direction_supported": (
                    local_uia_miller_supported
                ),
                "non_collinear_direction_supported": False,
                "single_window_policy_ok": not single_window_violation_reasons,
                "execution_requires_prepared_automation_ready_recipe": True,
                "execution_requires_explicit_execute": True,
                "post_action_visual_confirmation_required": True,
            },
            "local_uia_fit_to_view_supported": local_uia_supported,
            "local_uia_fit_to_view_command_id": FIT_TO_VIEW_COMMAND_ID,
            "capabilities": [
                "detect_matstudio_window",
                "list_matstudio_windows",
                "launch_matstudio_session",
                "activate_window",
                "open_structure_file",
                "capture_bmp_snapshot",
                "copy_script_assist",
                "prepare_view_replay_manifest",
                *(
                    [
                        "execute_standard_view_replay_with_local_uia",
                        "execute_staged_isometric_view_replay_with_local_uia",
                        *(
                            ["execute_transactional_miller_plane_view_replay_with_local_uia"]
                            if local_uia_miller_supported
                            else []
                        ),
                    ]
                    if self.view_replay_backend.supported
                    else []
                ),
                "record_external_view_replay",
                *(["execute_fit_to_view_with_local_uia"] if local_uia_supported else []),
            ],
            "limits": [
                "COM automation is not used.",
                "Structural edits remain ModelSpec, SemanticPatch, or MaterialsScript driven.",
                "The local UIA executor records mechanical evidence but never visual acceptance.",
                "Miller planes and exact-collinear crystal directions require a prepared automation-ready transactional recipe.",
                "Non-collinear crystal directions still require a reviewed camera backend.",
                "Blind coordinates, viewport modifier keys, and implicit MatStudio launches are prohibited.",
                "Multiple MatStudio processes are usable only through one exact project/revision wrapper bound to one isolated target PID.",
                "Fit-to-View only changes the current GUI camera framing; it never changes the structure artifact.",
            ],
        }

    def _fit_to_view_registry_preflight(self) -> dict[str, Any]:
        """Verify the installed Materials Studio toolbar mapping for Fit-to-View."""

        command_evidence = _materials_studio_view_command_evidence()
        reasons: list[str] = []
        registry_sha256 = str(command_evidence.get("registry_sha256") or "").lower()
        if command_evidence.get("registry_found") is not True:
            reasons.append("installed_view_toolbar_registry_not_found")
        if not re.fullmatch(r"[0-9a-f]{64}", registry_sha256):
            reasons.append("installed_view_toolbar_registry_hash_missing")
        if command_evidence.get("registry_toolbar_parse_error") not in {None, ""}:
            reasons.append("installed_view_toolbar_registry_parse_failed")
        if FIT_TO_VIEW_COMMAND_ID not in {
            str(item) for item in command_evidence.get("registered_view_command_ids") or []
        }:
            reasons.append("fit_to_view_command_not_registered")

        contract = VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[FIT_TO_VIEW_TOOLBAR_NAME]
        layouts = [
            item
            for item in command_evidence.get("registry_toolbar_layouts") or []
            if isinstance(item, dict)
            and item.get("registry_toolbar_name") == contract.get("registry_toolbar_name")
            and item.get("title") == FIT_TO_VIEW_TOOLBAR_NAME
        ]
        if len(layouts) != 1:
            reasons.append("fit_to_view_toolbar_registry_identity_not_unique")
        else:
            installed_entries = tuple(
                (
                    str(entry.get("kind") or ""),
                    str(entry.get("command_id"))
                    if entry.get("command_id") is not None
                    else None,
                )
                for entry in layouts[0].get("entries") or []
                if isinstance(entry, dict)
            )
            expected_entries = tuple(contract.get("entries") or ())
            if installed_entries != expected_entries:
                reasons.append("fit_to_view_toolbar_registry_sequence_mismatch")

        return {
            "source": "installed_materials_studio_view_registry",
            "command_id": FIT_TO_VIEW_COMMAND_ID,
            "command_label": FIT_TO_VIEW_CONTROL_NAME,
            "toolbar_name": FIT_TO_VIEW_TOOLBAR_NAME,
            "toolbar_child_index": FIT_TO_VIEW_TOOLBAR_CHILD_INDEX,
            "registry_path": command_evidence.get("registry_path"),
            "registry_sha256": registry_sha256 or None,
            "registry_found": command_evidence.get("registry_found"),
            "registered_command": FIT_TO_VIEW_COMMAND_ID
            in {
                str(item)
                for item in command_evidence.get("registered_view_command_ids") or []
            },
            "registry_verified": not reasons,
            "block_reasons": _unique_strings(reasons),
            "registry_toolbar_layouts": layouts,
        }

    def _fit_to_view_runtime_preflight(
        self,
        *,
        project_id: str,
        revision: int,
    ) -> dict[str, Any]:
        """Read-only preflight for one exact current wrapper and Fit control."""

        safe_project = sanitize_project_id(project_id)
        status = self.status(project_id=safe_project, revision=revision)
        target_window = (
            status.get("target_window")
            if isinstance(status.get("target_window"), dict)
            else {}
        )
        target_resolution = (
            status.get("target_window_resolution")
            if isinstance(status.get("target_window_resolution"), dict)
            else {}
        )
        reasons: list[str] = []
        if status.get("supported") is not True:
            reasons.append("gui_backend_unavailable")
        if status.get("target_window_pid_is_matstudio_process") is not True:
            reasons.append("target_window_pid_not_matstudio_process")
        if status.get("single_window_policy_ok") is not True:
            reasons.extend(str(item) for item in status.get("single_window_violation_reasons") or [])
        if status.get("target_window_found") is not True:
            reasons.append("target_revision_window_not_found")
        if target_resolution.get("matched_project_window") is not True:
            reasons.append("target_revision_window_identity_unverified")
        if status.get("current_revision_loaded") is not True:
            reasons.append("target_revision_not_loaded_in_gui")
        if target_resolution.get("target_wrapper_workspace_matches_controller") is False:
            reasons.append("target_wrapper_workspace_mismatch")
        if target_window.get("is_visible") is not True:
            reasons.append("target_window_not_visible")
        if target_window.get("is_minimized") is True:
            reasons.append("target_window_minimized")
        if target_window.get("is_foreground") is not True:
            reasons.append("target_window_not_foreground")
        structure_path = _target_structure_path(target_resolution)
        if structure_path is None or not structure_path.exists() or not structure_path.is_file():
            reasons.append("target_structure_artifact_unavailable")

        registry = self._fit_to_view_registry_preflight()
        reasons.extend(str(item) for item in registry.get("block_reasons") or [])
        probe: dict[str, Any] | None = None
        if not reasons:
            probe = self.view_replay_backend.probe(
                window_handle=int(target_window["handle"]),
                expected_window_title=str(target_window["title"]),
                expected_revision=revision,
                toolbar_contracts={
                    FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                        FIT_TO_VIEW_TOOLBAR_NAME
                    ]
                },
                command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
            )
            if probe.get("supported") is not True:
                reasons.append("local_uia_backend_unavailable")
            reasons.extend(str(item) for item in probe.get("block_reasons") or [])
            if FIT_TO_VIEW_COMMAND_ID not in {
                str(item) for item in probe.get("resolved_command_ids") or []
            }:
                reasons.append("fit_to_view_control_not_invocable")
            if probe.get("viewport") is None:
                reasons.append("local_uia_unique_viewport_not_observed")

        reasons = _unique_strings(reasons)
        return {
            "status": "verified_fit_to_view_ready" if not reasons else "fit_to_view_blocked",
            "execution_ready": not reasons,
            "project_id": safe_project,
            "revision": revision,
            "target_window": target_window or None,
            "target_window_resolution": target_resolution or None,
            "target_window_is_foreground": target_window.get("is_foreground"),
            "single_window_policy_ok": status.get("single_window_policy_ok"),
            "structure_path": str(structure_path) if structure_path is not None else None,
            "registry": registry,
            "local_uia_probe": probe,
            "block_reasons": reasons,
            "gui_input_performed": False,
            "structure_modified": False,
        }

    def fit_to_view(
        self,
        *,
        project_id: str,
        revision: int,
        execution_mode: str = "preview",
        take_snapshot: bool = True,
    ) -> dict[str, Any]:
        """Preview or execute Fit-to-View in the existing verified GUI window."""

        mode = str(execution_mode).strip().lower()
        if mode not in {"preview", "execute"}:
            raise GuiError("execution_mode must be preview or execute")
        if revision < 0:
            raise GuiError("revision must be non-negative")

        preflight = self._fit_to_view_runtime_preflight(
            project_id=project_id,
            revision=revision,
        )
        response: dict[str, Any] = {
            "project_id": preflight["project_id"],
            "revision": revision,
            "execution_mode": mode,
            "status": "preview_ready" if preflight["execution_ready"] else "blocked",
            "execution_ready": preflight["execution_ready"],
            "preflight": preflight,
            "gui_input_performed": False,
            "gui_modified": False,
            "structure_modified": False,
            "structure_unchanged": True,
            "snapshot_requested": bool(take_snapshot),
            "confirmation_required": mode == "preview" and preflight["execution_ready"],
            "confirmation_action": (
                {
                    "tool": "material_studio_gui_fit_to_view",
                    "payload": {
                        "project_id": preflight["project_id"],
                        "revision": revision,
                        "execution_mode": "execute",
                        "take_snapshot": bool(take_snapshot),
                    },
                }
                if mode == "preview" and preflight["execution_ready"]
                else None
            ),
        }
        if mode == "preview" or not preflight["execution_ready"]:
            response["recommended_tool"] = (
                "material_studio_gui_activate"
                if "target_window_not_foreground" in preflight["block_reasons"]
                else "material_studio_gui_status"
                if not preflight["execution_ready"]
                else "material_studio_gui_fit_to_view"
            )
            response["recommended_action"] = (
                "activate_exact_target_window_then_retry_fit_to_view"
                if "target_window_not_foreground" in preflight["block_reasons"]
                else "resolve_fit_to_view_preflight_then_retry"
                if not preflight["execution_ready"]
                else "execute_fit_to_view_after_explicit_confirmation"
            )
            return response

        target_window = preflight["target_window"] or {}
        target_resolution = preflight["target_window_resolution"] or {}
        structure_path = _target_structure_path(target_resolution)
        structure_sha256_before: str | None = None
        structure_size_before: int | None = None
        if structure_path is not None and structure_path.exists():
            structure_sha256_before, structure_size_before = _sha256_file(structure_path)

        before_snapshot: dict[str, Any] | None = None
        if take_snapshot:
            before_snapshot = self.snapshot(
                label="fit_to_view_before",
                project_id=preflight["project_id"],
                revision=revision,
            )

        execute_method = getattr(self.view_replay_backend, "execute_fit_to_view", None)
        if not callable(execute_method):
            raise GuiError("The configured local UIA backend does not support Fit-to-View.")
        action_receipt = execute_method(
            window_handle=int(target_window["handle"]),
            expected_window_title=str(target_window["title"]),
            toolbar_contracts={
                FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                    FIT_TO_VIEW_TOOLBAR_NAME
                ]
            },
            command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
            registry_sha256=preflight["registry"].get("registry_sha256"),
        )

        structure_sha256_after: str | None = None
        structure_size_after: int | None = None
        if structure_path is not None and structure_path.exists():
            structure_sha256_after, structure_size_after = _sha256_file(structure_path)
        structure_unchanged = bool(
            structure_sha256_before is not None
            and structure_sha256_before == structure_sha256_after
            and structure_size_before == structure_size_after
        )

        after_snapshot: dict[str, Any] | None = None
        snapshot_warning: str | None = None
        if take_snapshot:
            try:
                after_snapshot = self.snapshot(
                    label="fit_to_view_after",
                    project_id=preflight["project_id"],
                    revision=revision,
                )
            except Exception as exc:
                snapshot_warning = str(exc)

        execution_succeeded = bool(
            action_receipt.get("execution_succeeded") is True and structure_unchanged
        )
        response.update(
            {
                "status": "executed" if execution_succeeded else "execution_failed",
                "execution_ready": True,
                "action_receipt": action_receipt,
                "before_snapshot": before_snapshot,
                "after_snapshot": after_snapshot,
                "snapshot_warning": snapshot_warning,
                "structure_path": str(structure_path) if structure_path is not None else None,
                "structure_sha256_before": structure_sha256_before,
                "structure_sha256_after": structure_sha256_after,
                "structure_unchanged": structure_unchanged,
                "gui_input_performed": bool(
                    action_receipt.get("gui_input_performed") is True
                ),
                "gui_modified": bool(action_receipt.get("gui_modified") is True),
                "structure_modified": not structure_unchanged,
                "visual_acceptance_recorded": False,
                "post_action_visual_confirmation_required": True,
            }
        )
        log_path = self._write_log(
            "fit_to_view",
            project_id=preflight["project_id"],
            revision=revision,
            payload=response,
        )
        response["gui_log_path"] = str(log_path)
        return response

    def _window_inventory(self, windows: list[WindowInfo], *, selected_window: WindowInfo | None) -> list[dict[str, Any]]:
        """Return window entries enriched with live wrapper metadata when available."""

        entries: list[dict[str, Any]] = []
        for index, window in enumerate(windows):
            entry = window.to_dict()
            entry["index"] = index
            entry["is_selected"] = bool(selected_window and window.handle == selected_window.handle)
            if entry.get("is_minimized") is None and _window_rect_looks_minimized(window.rect):
                entry["is_minimized"] = True
                entry["minimized_state_source"] = "window_rect_sentinel"
            elif entry.get("is_minimized") is not None:
                entry["minimized_state_source"] = "win32_is_iconic"
            else:
                entry["minimized_state_source"] = "unknown"
            entry["foreground_state_observed"] = window.is_foreground is not None
            metadata = self._project_wrapper_metadata_for_window(window)
            if metadata is not None:
                entry["project_wrapper_metadata"] = metadata
                entry["project_id"] = metadata.get("project_id")
                entry["revision"] = metadata.get("revision")
                entry["source_path"] = metadata.get("source_path")
                entry["wrapper_workspace_root"] = metadata.get("wrapper_workspace_root")
                entry["wrapper_workspace_scope"] = metadata.get("wrapper_workspace_scope")
                entry["wrapper_workspace_matches_controller"] = metadata.get(
                    "wrapper_workspace_matches_controller"
                )
                entry["external_workspace_wrapper_detected"] = metadata.get(
                    "external_workspace_wrapper_detected"
                )
                entry["wrapper_provenance_status"] = metadata.get("wrapper_provenance_status")
                entry["wrapper_integrity_verified"] = metadata.get(
                    "wrapper_integrity_verified"
                )
                entry["wrapper_integrity_status"] = metadata.get(
                    "wrapper_integrity_status"
                )
                entry["wrapper_target_identity_verified"] = metadata.get(
                    "wrapper_target_identity_verified"
                )
                entry["wrapper_target_identity_status"] = metadata.get(
                    "wrapper_target_identity_status"
                )
            entries.append(entry)
        return entries

    def _project_wrapper_metadata_for_window(self, window: WindowInfo) -> dict[str, Any] | None:
        """Return metadata for an MCP-generated .stp wrapper window."""

        project_name = _project_name_from_window_title(window.title)
        if project_name is None:
            return None
        readable_candidates: list[dict[str, Any]] = []
        unreadable_candidates: list[dict[str, Any]] = []
        for workspace_root, trust_basis in self.trusted_wrapper_workspace_roots:
            metadata_path = (workspace_root / "gui_projects" / project_name / "metadata.json").resolve()
            try:
                _ensure_inside(workspace_root, metadata_path)
            except GuiError:
                continue
            if not metadata_path.exists() or not metadata_path.is_file():
                continue

            workspace_matches_controller = _same_resolved_path(workspace_root, self.workspace_root)
            candidate_identity = {
                "metadata_path": str(metadata_path),
                "wrapper_workspace_root": str(workspace_root),
                "wrapper_workspace_scope": "controller" if workspace_matches_controller else "trusted_external",
                "wrapper_workspace_trust_basis": trust_basis,
                "wrapper_workspace_matches_controller": workspace_matches_controller,
                "external_workspace_wrapper_detected": not workspace_matches_controller,
            }
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                unreadable_candidates.append(
                    {
                        **candidate_identity,
                        "read_error": True,
                        "wrapper_provenance_status": "metadata_read_error",
                    }
                )
                continue
            if not isinstance(metadata, dict):
                unreadable_candidates.append(
                    {
                        **candidate_identity,
                        "read_error": True,
                        "wrapper_provenance_status": "metadata_not_object",
                    }
                )
                continue
            metadata_project_name = metadata.get("project_name")
            if metadata_project_name is not None and str(metadata_project_name) != project_name:
                unreadable_candidates.append(
                    {
                        **candidate_identity,
                        "read_error": True,
                        "wrapper_provenance_status": "project_name_mismatch",
                    }
                )
                continue

            project_path = (
                workspace_root
                / "gui_projects"
                / project_name
                / f"{project_name}.stp"
            ).resolve()
            wrapper_integrity = _wrapper_project_path_provenance(
                project_path,
                workspace_root=workspace_root,
                allow_locked_attestation=True,
            )
            wrapper_integrity_verified = (
                wrapper_integrity.get("verified") is True
            )
            wrapper_target_identity_verified = (
                wrapper_integrity.get("target_identity_verified") is True
            )
            source_path = metadata.get("source_path")
            source_inside_workspace = (
                _path_is_inside(workspace_root, Path(str(source_path)).expanduser())
                if source_path
                else None
            )
            has_revision_identity = False
            try:
                has_revision_identity = int(metadata.get("revision")) >= 0
            except (TypeError, ValueError):
                pass
            has_project_identity = False
            if isinstance(metadata.get("project_id"), str) and metadata["project_id"]:
                try:
                    has_project_identity = sanitize_project_id(metadata["project_id"]) == metadata["project_id"]
                except ValueError:
                    pass
            if wrapper_integrity_verified:
                provenance_status = "verified_revision_wrapper"
            elif wrapper_target_identity_verified:
                provenance_status = "verified_revision_reload_target"
            elif source_path and source_inside_workspace is False:
                provenance_status = "source_outside_wrapper_workspace"
            elif (
                has_project_identity
                and has_revision_identity
                and metadata_project_name == project_name
            ):
                provenance_status = "unverified_revision_wrapper"
            else:
                provenance_status = "legacy_or_unscoped_wrapper"
            readable_candidates.append(
                {
                    **metadata,
                    **candidate_identity,
                    "wrapper_project_name": project_name,
                    "source_inside_wrapper_workspace": source_inside_workspace,
                    "wrapper_provenance_status": provenance_status,
                    "wrapper_integrity_verified": (
                        wrapper_integrity_verified
                    ),
                    "wrapper_integrity_status": wrapper_integrity.get(
                        "status"
                    ),
                    "wrapper_target_identity_verified": (
                        wrapper_target_identity_verified
                    ),
                    "wrapper_target_identity_status": (
                        wrapper_integrity.get("target_identity_status")
                    ),
                    "wrapper_target_identity_reason_codes": list(
                        wrapper_integrity.get(
                            "target_identity_reason_codes"
                        )
                        or []
                    ),
                    "wrapper_integrity_reason_codes": list(
                        wrapper_integrity.get("reason_codes") or []
                    ),
                    "wrapper_project_xml_verification_status": (
                        wrapper_integrity.get(
                            "project_xml_verification_status"
                        )
                    ),
                    "wrapper_project_sha256_current": (
                        wrapper_integrity.get("project_sha256_current")
                    ),
                    "wrapper_document_sha256_current": (
                        wrapper_integrity.get("document_sha256_current")
                    ),
                    "wrapper_source_sha256_current": (
                        wrapper_integrity.get("source_sha256_current")
                    ),
                    "wrapper_identity_manifest_valid": (
                        wrapper_integrity.get(
                            "wrapper_identity_manifest_valid"
                        )
                    ),
                    "wrapper_revision_state_binding_valid": (
                        wrapper_integrity.get(
                            "wrapper_revision_state_binding_valid"
                        )
                    ),
                    "legacy_revision_state_binding_valid": (
                        wrapper_integrity.get(
                            "legacy_revision_state_binding_valid"
                        )
                    ),
                }
            )

        if len(readable_candidates) == 1 and not unreadable_candidates:
            return readable_candidates[0]
        if readable_candidates:
            return {
                "wrapper_project_name": project_name,
                "read_error": True,
                "wrapper_provenance_status": "ambiguous_across_trusted_workspaces",
                "wrapper_workspace_candidates": [
                    {
                        "metadata_path": candidate.get("metadata_path"),
                        "wrapper_workspace_root": candidate.get("wrapper_workspace_root"),
                        "wrapper_workspace_scope": candidate.get("wrapper_workspace_scope"),
                        "wrapper_workspace_trust_basis": candidate.get("wrapper_workspace_trust_basis"),
                    }
                    for candidate in [*readable_candidates, *unreadable_candidates]
                ],
            }
        if unreadable_candidates:
            return unreadable_candidates[0]
        return None

    def launch(
        self,
        *,
        wait_seconds: float = 20.0,
        take_snapshot: bool = False,
        project_id: str | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Launch or prepare a visible Materials Studio GUI session."""

        if not self.backend.supported:
            raise GuiError(self.backend.unavailable_reason or "GUI backend is unavailable.")
        processes = self.backend.list_processes()
        list_windows = getattr(self.backend, "list_windows", None)
        windows = list_windows() if callable(list_windows) else []
        target_resolution: dict[str, Any] | None = None
        if project_id is not None or revision is not None:
            existing_window, target_resolution = self._resolve_target_window(project_id=project_id, revision=revision)
        else:
            discovered_window = self.backend.find_window()
            if discovered_window is not None and not any(
                item.handle == discovered_window.handle for item in windows
            ):
                windows = [discovered_window, *windows]
            existing_window = _select_live_matstudio_window(
                processes=processes,
                windows=windows,
                preferred=discovered_window,
            )
        if existing_window is not None and not any(item.handle == existing_window.handle for item in windows):
            windows = [existing_window, *windows]
        window_inventory = self._window_inventory(windows, selected_window=existing_window)
        same_window_open_supported = _backend_same_window_open_supported(self.backend)
        file_open_may_launch_new_instance = _backend_file_open_may_launch_new_instance(self.backend)
        window_management = _window_management_receipt(
            controller_workspace_root=self.workspace_root,
            processes=processes,
            window_inventory=window_inventory,
            selected_window=existing_window,
            target_window=existing_window,
            target_resolution=target_resolution,
            requested_project_id=project_id,
            requested_revision=revision,
            same_window_open_supported=same_window_open_supported,
            file_open_may_launch_new_instance=file_open_may_launch_new_instance,
            startup_dialog_open_supported=_backend_startup_dialog_open_supported(self.backend),
        )
        single_window_violation_reasons = list(window_management.get("single_window_violation_reasons") or [])
        if (
            existing_window is not None
            and (project_id is not None or revision is not None)
            and isinstance(target_resolution, dict)
            and target_resolution.get("matched_project_window") is True
        ):
            metadata = (
                target_resolution.get("target_project_wrapper_metadata")
                if isinstance(
                    target_resolution.get("target_project_wrapper_metadata"),
                    dict,
                )
                else {}
            )
            if metadata.get("wrapper_integrity_verified") is not True:
                single_window_violation_reasons.append(
                    "target_wrapper_integrity_unverified"
                )
            if metadata.get("wrapper_workspace_matches_controller") is not True:
                single_window_violation_reasons.append(
                    "target_wrapper_workspace_mismatch"
                )
        single_window_violation_reasons = _unique_strings(
            single_window_violation_reasons
        )
        if single_window_violation_reasons:
            window_management["single_window_policy_ok"] = False
            window_management["single_window_violation_reasons"] = (
                single_window_violation_reasons
            )
            window_management["ready_for_snapshot"] = False
            window_management["ready_for_open"] = False
        launch_guard = {
            "process_count": len(processes),
            "window_count": len(window_inventory),
            "single_window_policy_ok": not single_window_violation_reasons,
            "single_window_violation_reasons": single_window_violation_reasons,
            "window_management": window_management,
        }
        if single_window_violation_reasons:
            payload: dict[str, Any] = {
                "launched": False,
                "activated_existing_window": False,
                "window_found": existing_window is not None,
                "window": existing_window.to_dict() if existing_window else None,
                "launch_result": None,
                "launch_blocked": True,
                "launch_block_reason": "single_window_policy_violation",
                **launch_guard,
            }
            if target_resolution is not None:
                payload["target_window_resolution"] = target_resolution
            self._write_log("launch", project_id=project_id, revision=revision, payload=payload)
            return payload
        if existing_window is not None:
            activated = self.backend.activate_window(existing_window)
            payload: dict[str, Any] = {
                "launched": False,
                "activated_existing_window": activated,
                "window_found": True,
                "window": existing_window.to_dict(),
                "launch_result": None,
                "launch_blocked": False,
                **launch_guard,
            }
            if target_resolution is not None:
                payload["target_window_resolution"] = target_resolution
        else:
            if processes:
                payload = {
                    "launched": False,
                    "activated_existing_window": False,
                    "window_found": False,
                    "window": None,
                    "launch_result": None,
                    "launch_blocked": True,
                    "launch_block_reason": "matstudio_process_without_usable_window",
                    "message": (
                        "MatStudio.exe is already running but no usable Materials Studio main window was found. "
                        "Refusing to launch another instance; activate or close/save the existing session first."
                    ),
                    **launch_guard,
                }
                if target_resolution is not None:
                    payload["target_window_resolution"] = target_resolution
                self._write_log("launch", project_id=project_id, revision=revision, payload=payload)
                return payload
            launch_result = self.backend.launch_app()
            target_pid = launch_result.get("pid") if isinstance(launch_result.get("pid"), int) else None
            if isinstance(self.backend, WindowsGuiBackend):
                dismissed = _dismiss_backend_startup_dialogs(self.backend, pid=target_pid, timeout_seconds=4.0)
                if dismissed:
                    launch_result["dismissed_dialogs"] = dismissed
            window = self._wait_for_window_after_open(
                previous_window=None,
                timeout_seconds=wait_seconds,
                target_pid=target_pid,
            )
            activated = self.backend.activate_window(window) if window is not None else False
            payload = {
                "launched": True,
                "activated_existing_window": activated,
                "window_found": window is not None,
                "window": window.to_dict() if window else None,
                "launch_result": launch_result,
                "launch_blocked": False,
                **launch_guard,
            }
            if target_resolution is not None:
                payload["target_window_resolution"] = target_resolution
        if take_snapshot and payload.get("window") is not None:
            try:
                payload["snapshot"] = self.snapshot(
                    label="launch_matstudio",
                    project_id=project_id,
                    revision=revision,
                )
            except Exception as exc:
                payload["snapshot_warning"] = str(exc)
        self._write_log("launch", project_id=project_id, revision=revision, payload=payload)
        return payload

    def activate(self, *, project_id: str | None = None, revision: int | None = None) -> dict[str, Any]:
        """激活窗口。"""
        if project_id is not None:
            project_id = sanitize_project_id(project_id)
        requested_window, target_resolution = self._require_window(project_id=project_id, revision=revision)
        self._require_direct_action_target(
            window=requested_window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        activated = self.backend.activate_window(requested_window)
        refreshed_window, refreshed_resolution = self._require_window(project_id=project_id, revision=revision)
        window_identity_stable = refreshed_window.handle == requested_window.handle
        window_management = self._require_direct_action_target(
            window=refreshed_window,
            target_resolution=refreshed_resolution,
            project_id=project_id,
            revision=revision,
        )
        activation_verified = bool(
            activated
            and window_identity_stable
            and not window_management.get("activation_required_before_capture_or_input")
        )
        payload = {
            "activated": activated,
            "activation_verified": activation_verified,
            "activation_verification_reasons": list(window_management.get("interaction_activation_reasons") or []),
            "window_identity_stable_after_activation": window_identity_stable,
            "requested_window": requested_window.to_dict(),
            "window": refreshed_window.to_dict(),
            "target_window_resolution": refreshed_resolution,
            "pre_activation_target_window_resolution": target_resolution,
            "window_management": window_management,
            "single_window_policy_ok": bool(window_management.get("single_window_policy_ok")),
            "single_window_violation_reasons": list(window_management.get("single_window_violation_reasons") or []),
        }
        self._write_log("activate", project_id=project_id, revision=revision, payload=payload)
        return payload

    def snapshot(
        self,
        *,
        label: str = "snapshot",
        project_id: str | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """捕获快照。"""
        if project_id is not None:
            project_id = sanitize_project_id(project_id)
        window, target_resolution = self._require_window(project_id=project_id, revision=revision)
        window_management = self._require_direct_action_target(
            window=window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        if window_management.get("activation_required_before_capture_or_input"):
            blocked_payload = {
                "captured": False,
                "capture_started": False,
                "project_id": project_id,
                "revision": revision,
                "working_dir": str(self.workspace_root),
                "label": label,
                "window": window.to_dict(),
                "target_window_resolution": target_resolution,
                "window_management": window_management,
                "block_reason": "target_window_activation_required",
                "activation_reasons": list(window_management.get("interaction_activation_reasons") or []),
            }
            self._write_log("snapshot_blocked", project_id=project_id, revision=revision, payload=blocked_payload)
            raise GuiSnapshotBlockedError(
                (
                    "Refusing to capture the Materials Studio window before the verified target is restored and "
                    "foreground. Call material_studio_gui_activate with the same project_id/revision and "
                    "take_snapshot=true."
                ),
                receipt=blocked_payload,
            )
        output_path = self._screenshot_path(label=label, project_id=project_id, revision=revision)
        captured_path = self.backend.capture_window(window, output_path)
        payload = {
            "window": window.to_dict(),
            "target_window_resolution": target_resolution,
            "window_management": window_management,
            "single_window_policy_ok": bool(window_management.get("single_window_policy_ok")),
            "single_window_violation_reasons": list(window_management.get("single_window_violation_reasons") or []),
            "screenshot_path": str(captured_path),
            "format": "bmp",
            "analysis": _analyze_bmp_snapshot(captured_path),
        }
        self._write_log("snapshot", project_id=project_id, revision=revision, payload=payload)
        return payload

    def open_structure(
        self,
        structure_path: str | Path,
        *,
        project_id: str | None = None,
        revision: int | None = None,
        take_snapshot: bool = True,
        reuse_existing_window_only: bool = True,
    ) -> dict[str, Any]:
        """Open a structure in Materials Studio GUI."""

        path = Path(structure_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise GuiError(f"结构文件不存在: {path}")
        window, target_resolution = self._resolve_hotload_target_window(
            project_id=project_id,
            revision=revision,
        )
        if window is None:
            raise GuiError(
                "No existing Materials Studio window was found. Refusing to launch a new MatStudio.exe "
                "from open_structure; activate an existing window first, or call material_studio_gui_launch "
                "only when starting a new GUI session is intentional."
            )
        hotload_target_project_id = target_resolution.get(
            "hotload_target_project_id",
            project_id,
        )
        hotload_target_revision = target_resolution.get(
            "hotload_target_revision",
            revision,
        )
        pre_open_hotload_target_resolution = dict(target_resolution)
        if target_resolution.get("matched_project_window") is True:
            window_management = self._require_hotload_action_target(
                window=window,
                target_resolution=target_resolution,
                project_id=hotload_target_project_id,
                revision=hotload_target_revision,
            )
        else:
            window_management = self._window_management_for_hotload(
                target_window=window,
                target_resolution=target_resolution,
                project_id=hotload_target_project_id,
                revision=hotload_target_revision,
            )
        single_window_violation_reasons = list(window_management.get("single_window_violation_reasons") or [])
        if single_window_violation_reasons:
            raise GuiError(
                "Refusing to hot-load a structure while the Materials Studio GUI session violates the "
                "single-window policy. Supply one exact verified project/revision target, or close or save "
                "extra windows in the target process before continuing. "
                f"Violations: {', '.join(single_window_violation_reasons)}"
            )

        open_target = path
        wrapper: dict[str, Any] | None = None
        if isinstance(self.backend, WindowsGuiBackend) and path.suffix.lower() in {
            ".arc",
            ".car",
            ".cif",
            ".mol",
            ".pdb",
            ".xsd",
            ".xtd",
        }:
            wrapper = self._create_project_wrapper(path, project_id=project_id, revision=revision)
            open_target = Path(wrapper["project_path"])

        if window_management.get("unresolved_blocking_dialog_count"):
            raise GuiError(
                "Refusing to hot-load a structure while a Materials Studio startup or modal dialog is open. "
                "Activate the existing Materials Studio window, dismiss the dialog without closing the app, "
                "then retry the same-window hot-load."
            )
        activated = self.backend.activate_window(window)
        post_activation_window_management = window_management
        startup_dialog_open_ready = bool(window_management.get("startup_dialog_open_ready"))
        if not startup_dialog_open_ready:
            if not activated:
                raise GuiError(
                    "Refusing same-window GUI input because the target Materials Studio window could not be "
                    "restored and activated. Call material_studio_gui_activate, verify the returned target "
                    "identity, and retry."
                )
            refreshed_window, refreshed_resolution = self._require_window(
                project_id=hotload_target_project_id,
                revision=hotload_target_revision,
            )
            if refreshed_window.handle != window.handle:
                raise GuiError(
                    "Refusing same-window GUI input because the target Materials Studio window identity changed "
                    "during activation. Run material_studio_gui_status and retry with the returned project/revision."
                )
            window = refreshed_window
            target_resolution = refreshed_resolution
            if target_resolution.get("matched_project_window") is True:
                post_activation_window_management = (
                    self._require_hotload_action_target(
                        window=window,
                        target_resolution=target_resolution,
                        project_id=hotload_target_project_id,
                        revision=hotload_target_revision,
                    )
                )
            else:
                post_activation_window_management = (
                    self._window_management_for_hotload(
                        target_window=window,
                        target_resolution=target_resolution,
                        project_id=hotload_target_project_id,
                        revision=hotload_target_revision,
                    )
                )
            if post_activation_window_management.get("activation_required_before_capture_or_input"):
                raise GuiError(
                    "Refusing same-window GUI input because the target Materials Studio window is still minimized "
                    "or is not the verified foreground window after activation."
                )

        same_window_opener = _backend_same_window_open_callable(self.backend)
        same_window_open_used = False
        if reuse_existing_window_only and _backend_file_open_may_launch_new_instance(self.backend):
            if same_window_opener is None:
                raise GuiError(
                    "Refusing to open the structure through the local Windows file-open fallback because it "
                    "would start MatStudio.exe and may create another Materials Studio window. Use the already "
                    "open GUI window/project via Computer Use or Materials Studio File > Open, then snapshot "
                    "or audit the current project."
                )
            open_result = same_window_opener(window, open_target)
            same_window_open_used = True
        else:
            open_result = self.backend.open_file(open_target)
        if isinstance(self.backend, WindowsGuiBackend):
            launch_pid = open_result.get("pid") if isinstance(open_result.get("pid"), int) else None
            target_pid = window.pid if same_window_open_used else launch_pid
            dismissed = _dismiss_backend_startup_dialogs(self.backend, pid=target_pid, timeout_seconds=8.0)
            if dismissed:
                open_result["dismissed_dialogs"] = dismissed
            window = self._wait_for_window_after_open(
                previous_window=window,
                timeout_seconds=18.0,
                target_pid=target_pid,
                expected_project_name=wrapper.get("project_name") if wrapper else None,
            )
        else:
            refreshed_window = self.backend.find_window()
            if refreshed_window is not None:
                window = refreshed_window
        post_open_target_resolution = target_resolution
        if window is not None and (project_id is not None or revision is not None):
            resolved_window, resolved_target_resolution = self._resolve_target_window(
                project_id=project_id,
                revision=revision,
            )
            if resolved_window is not None:
                window = resolved_window
                post_open_target_resolution = resolved_target_resolution
        post_open_window_management: dict[str, Any] | None = None
        post_open_single_window_violation_reasons: list[str] = []
        spawned_process_ids = [
            int(item)
            for item in open_result.get("spawned_process_ids", [])
            if isinstance(item, int)
        ]
        if window is not None:
            post_open_window_management = self._window_management_for_hotload(
                target_window=window,
                target_resolution=post_open_target_resolution,
                project_id=project_id,
                revision=revision,
            )
            post_open_single_window_violation_reasons = [
                str(item)
                for item in post_open_window_management.get("single_window_violation_reasons", [])
                if item
            ]
        if spawned_process_ids:
            post_open_single_window_violation_reasons.append(
                "matstudio_process_spawned_during_same_window_open"
            )
            if post_open_window_management is not None:
                warnings = [
                    str(item)
                    for item in post_open_window_management.get("warnings", [])
                    if item
                ]
                warnings.append("matstudio_process_spawned_during_same_window_open")
                post_open_window_management["warnings"] = _unique_strings(warnings)
                post_open_window_management["single_window_policy_ok"] = False
                post_open_window_management["ready_for_same_window_open"] = False
                post_open_window_management["ready_for_open"] = False
                post_open_window_management["recommended_tool"] = "material_studio_gui_status"
                post_open_window_management[
                    "recommended_action"
                ] = "close_save_extra_matstudio_windows_then_retry_hotload"
        post_open_single_window_violation_reasons = _unique_strings(
            post_open_single_window_violation_reasons
        )
        if post_open_window_management is not None:
            post_open_window_management["single_window_violation_reasons"] = (
                post_open_single_window_violation_reasons
            )
            post_open_window_management["single_window_policy_ok"] = (
                not post_open_single_window_violation_reasons
            )
        activated_opened_window = bool(
            window is not None
            and not post_open_single_window_violation_reasons
            and self.backend.activate_window(window)
        )
        payload: dict[str, Any] = {
            "project_id": project_id,
            "revision": revision,
            "structure_path": str(path),
            "activated_existing_window": activated,
            "activated_opened_window": activated_opened_window,
            "window": window.to_dict() if window else None,
            "open_result": open_result,
            "reuse_existing_window_only": reuse_existing_window_only,
            "same_window_open_supported": _backend_same_window_open_supported(self.backend),
            "same_window_open_used": same_window_open_used,
            "window_management": window_management,
            "post_activation_window_management": post_activation_window_management,
            "pre_open_hotload_target_resolution": (
                pre_open_hotload_target_resolution
            ),
            "post_open_target_window_resolution": post_open_target_resolution,
            "post_open_window_management": post_open_window_management,
            "post_open_single_window_policy_ok": not post_open_single_window_violation_reasons,
            "post_open_single_window_violation_reasons": post_open_single_window_violation_reasons,
            "single_window_policy_ok": not post_open_single_window_violation_reasons,
            "single_window_violation_reasons": post_open_single_window_violation_reasons,
        }
        if wrapper is not None:
            payload["project_wrapper"] = wrapper
        if take_snapshot and window is not None:
            try:
                payload["snapshot"] = self.snapshot(
                    label=f"open_{path.stem}",
                    project_id=project_id,
                    revision=revision,
                )
            except Exception as exc:  # 快照失败不应隐藏打开状态
                payload["snapshot_warning"] = str(exc)
        self._write_log("open_structure", project_id=project_id, revision=revision, payload=payload)
        return payload

    def _window_management_for_hotload(
        self,
        *,
        target_window: WindowInfo,
        target_resolution: dict[str, Any] | None,
        project_id: str | None,
        revision: int | None,
    ) -> dict[str, Any]:
        """Return window-management checks used before a structure hot-load."""

        processes = self.backend.list_processes() if self.backend.supported else []
        list_windows = getattr(self.backend, "list_windows", None)
        windows = list_windows() if self.backend.supported and callable(list_windows) else []
        selected_window = self.backend.find_window() if self.backend.supported else None
        if selected_window is not None and not any(item.handle == selected_window.handle for item in windows):
            windows = [selected_window, *windows]
        if not any(item.handle == target_window.handle for item in windows):
            windows = [target_window, *windows]
        return _window_management_receipt(
            controller_workspace_root=self.workspace_root,
            processes=processes,
            window_inventory=self._window_inventory(windows, selected_window=selected_window),
            selected_window=selected_window,
            target_window=target_window,
            target_resolution=target_resolution,
            requested_project_id=project_id,
            requested_revision=revision,
            same_window_open_supported=_backend_same_window_open_supported(self.backend),
            file_open_may_launch_new_instance=_backend_file_open_may_launch_new_instance(self.backend),
            startup_dialog_open_supported=_backend_startup_dialog_open_supported(self.backend),
        )

    def copy_script_assist(
        self,
        *,
        context: str | None = None,
        project_id: str | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Copy Script 辅助。"""
        payload = {
            "target_app": "BIOVIA Materials Studio",
            "context": context,
            "status": self.status(project_id=project_id, revision=revision),
            "checklist": [
                "激活现有的 Materials Studio 窗口。",
                "手动或使用 Computer Use（如果可用）执行预期的 GUI 操作。",
                "使用 Materials Studio Copy Script 获取确切的 API 片段。",
                "将复制的脚本粘贴到项目注释或后续提示中以进行翻译器对齐。",
                "不要删除或覆盖项目文件。",
            ],
            "computer_use_note": (
                "如果 Computer Use 不可用，请手动使用此检查清单，并保持结构化的 "
                "ModelSpec/SemanticPatch 工作流作为事实来源。"
            ),
        }
        status = payload["status"]
        target_window = (
            status.get("target_window")
            if isinstance(status.get("target_window"), dict)
            else {}
        )
        payload["checklist"] = [
            "Activate and reverify the exact current revision wrapper window.",
            "Apply the prepared view without blind coordinates or unverified unnamed controls.",
            "Use Materials Studio Copy Script and capture the exact inert script text.",
            "Capture a project-scoped screenshot after visually matching the manifest camera.",
            "Submit the script and observed window evidence to material_studio_gui_record_view_replay.",
        ]
        payload["computer_use_note"] = (
            "If Computer Use cannot capture the Copy Script output, use human review. "
            "Keep ModelSpec/SemanticPatch as the structural source of truth."
        )
        payload.update(
            {
                "reviewed_copy_script_evidence_contract": {
                    "record_tool": "material_studio_gui_record_view_replay",
                    "source": "reviewed_copy_script",
                    "script_is_evidence_only": True,
                    "script_execution_allowed": False,
                    "requires_exact_window_binding": True,
                    "requires_workspace_screenshot": True,
                    "raw_script_persisted_only_when_static_safety_passes": True,
                    "unsafe_script_persistence": "hash_and_analysis_only",
                    "artifact_directory": "gui_copy_script_evidence",
                    "required_evidence_fields": [
                        "script_text",
                        "capture_method",
                        "reviewer",
                        "copy_script_command_observed",
                        "review_completed",
                        "view_action_matches_manifest",
                        "structure_unchanged_observed",
                    ],
                    "static_block_categories": [
                        "shell_or_external_process",
                        "network_api",
                        "filesystem_delete_or_import_export",
                        "calculation_or_module_run",
                        "structure_create_delete_or_coordinate_change",
                    ],
                },
                "record_payload_template": {
                    "project_id": project_id,
                    "revision": revision,
                    "view_name": "<prepared view name>",
                    "source": "reviewed_copy_script",
                    "model_visible": True,
                    "camera_matches_manifest": True,
                    "screenshot_path": "<observed workspace screenshot path>",
                    "expected_window_handle": target_window.get("handle"),
                    "expected_window_title": target_window.get("title"),
                    "reviewed_copy_script_evidence": {
                        "script_text": "<exact Materials Studio Copy Script text>",
                        "capture_method": "materials_studio_copy_script",
                        "reviewer": "<computer_use or human_review>",
                        "copy_script_command_observed": True,
                        "review_completed": True,
                        "view_action_matches_manifest": True,
                        "structure_unchanged_observed": True,
                        "note": "<observed review note>",
                    },
                },
                "payload_template_is_directly_callable": False,
                "observed_values_must_not_be_assumed": True,
            }
        )
        self._write_log("copy_script_assist", project_id=project_id, revision=revision, payload=payload)
        return payload

    def probe_view_replay_accessibility(
        self,
        *,
        project_id: str,
        revision: int,
    ) -> dict[str, Any]:
        """Probe the exact current wrapper through local UIA without GUI input."""

        safe_project = sanitize_project_id(project_id)
        if revision < 0:
            raise GuiError("revision must be non-negative")
        status = self.status(project_id=safe_project, revision=revision)
        target_window = (
            status.get("target_window")
            if isinstance(status.get("target_window"), dict)
            else {}
        )
        target_resolution = (
            status.get("target_window_resolution")
            if isinstance(status.get("target_window_resolution"), dict)
            else {}
        )
        block_reasons: list[str] = []
        if status.get("supported") is not True:
            block_reasons.append("gui_backend_unavailable")
        if status.get("target_window_pid_is_matstudio_process") is not True:
            block_reasons.append("target_window_pid_not_matstudio_process")
        if status.get("single_window_policy_ok") is not True:
            block_reasons.extend(
                str(item)
                for item in status.get("single_window_violation_reasons") or []
            )
        if status.get("target_window_found") is not True:
            block_reasons.append("target_revision_window_not_found")
        if target_resolution.get("matched_project_window") is not True:
            block_reasons.append("target_revision_window_identity_unverified")
        if status.get("current_revision_loaded") is not True:
            block_reasons.append("target_revision_not_loaded_in_gui")
        try:
            target_handle = int(target_window.get("handle"))
        except (TypeError, ValueError):
            target_handle = 0
        target_title = str(target_window.get("title") or "")
        if target_handle <= 0 or not target_title:
            block_reasons.append("target_window_identity_missing")
        if block_reasons:
            return {
                "project_id": safe_project,
                "revision": revision,
                "supported": bool(self.view_replay_backend.supported),
                "safe_for_standard_view_replay": False,
                "gui_input_performed": False,
                "target_window": target_window or None,
                "target_window_resolution": target_resolution or None,
                "single_window_policy_ok": status.get("single_window_policy_ok"),
                "block_reasons": _unique_strings(block_reasons),
            }

        probe = self.view_replay_backend.probe(
            window_handle=target_handle,
            expected_window_title=target_title,
            expected_revision=revision,
            toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
            command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
        )
        evidence = probe.get("evidence") if isinstance(probe, dict) else None
        binding: dict[str, Any] | None = None
        if isinstance(evidence, dict):
            try:
                normalized_evidence = _normalize_view_runtime_accessibility_evidence(
                    evidence
                )
                binding = _view_replay_runtime_ui_binding(
                    status,
                    project_id=safe_project,
                    revision=revision,
                    evidence=normalized_evidence,
                )
                probe["evidence"] = normalized_evidence
                if binding.get("ok") is not True:
                    probe.setdefault("block_reasons", []).extend(
                        f"local_uia_binding_{reason}"
                        for reason in binding.get("rejection_reasons") or []
                    )
            except Exception as exc:
                probe.setdefault("block_reasons", []).append(
                    "local_uia_evidence_normalization_failed"
                )
                probe["evidence_error"] = str(exc)
        else:
            probe.setdefault("block_reasons", []).append(
                "local_uia_evidence_unavailable"
            )
        probe["block_reasons"] = _unique_strings(
            str(item) for item in probe.get("block_reasons") or []
        )
        probe["safe_for_standard_view_replay"] = bool(
            probe.get("safe_for_standard_view_replay") is True
            and isinstance(binding, dict)
            and binding.get("ok") is True
            and not probe["block_reasons"]
        )
        probe["safe_for_miller_plane_transaction"] = bool(
            probe.get("safe_for_miller_plane_transaction") is True
            and isinstance(binding, dict)
            and binding.get("ok") is True
            and not probe["block_reasons"]
        )
        return {
            "project_id": safe_project,
            "revision": revision,
            **probe,
            "gui_input_performed": False,
            "binding": binding,
            "target_window": target_window,
            "target_window_resolution": target_resolution,
            "single_window_policy_ok": status.get("single_window_policy_ok"),
            "target_window_is_foreground": target_window.get("is_foreground"),
        }

    def run_view_replay(
        self,
        audit: dict[str, Any],
        *,
        project_id: str,
        revision: int,
        view_name: str | None = None,
        execution_mode: str = "preview",
    ) -> dict[str, Any]:
        """Preview or execute one safe local-UIA view without auto-accepting it."""

        safe_project = sanitize_project_id(project_id)
        mode = str(execution_mode).strip().lower()
        if mode not in {"preview", "execute"}:
            raise GuiError("execution_mode must be preview or execute")
        requested_view_name = str(view_name or "").strip() or None
        if requested_view_name is not None and len(requested_view_name) > 80:
            raise GuiError("view_name must be at most 80 characters")
        if str(audit.get("project_id") or "") != safe_project:
            raise GuiError("view audit project_id does not match the replay target")
        try:
            audit_revision = int(audit.get("revision"))
        except (TypeError, ValueError) as exc:
            raise GuiError("view audit revision is missing or invalid") from exc
        if audit_revision != revision:
            raise GuiError("view audit revision does not match the replay target")

        initial_probe = self.probe_view_replay_accessibility(
            project_id=safe_project,
            revision=revision,
        )
        plan = self._local_view_replay_plan(
            audit,
            project_id=safe_project,
            revision=revision,
            probe=initial_probe,
            requested_view_name=requested_view_name,
        )
        response: dict[str, Any] = {
            "project_id": safe_project,
            "revision": revision,
            "execution_mode": mode,
            "status": (
                "preview_ready"
                if plan.get("execution_ready") is True
                else "blocked"
            ),
            "selected_view_name": plan.get("selected_view_name"),
            "requested_view_name": requested_view_name,
            "execution_ready": plan.get("execution_ready"),
            "execution_supported_view_names": sorted(
                {
                    *SAFE_LOCAL_VIEW_NAMES,
                    *(
                        str(item.get("view_name"))
                        for item in plan.get("candidate_views") or []
                        if isinstance(item, dict)
                        and item.get("local_execution_supported") is True
                        and item.get("view_name")
                    ),
                }
            ),
            "plan": plan,
            "local_uia_probe": initial_probe,
            "gui_input_performed": False,
            "gui_modified": False,
            "structure_modified": False,
            "manifest_modified": False,
            "revision_created": False,
            "visual_acceptance_recorded": False,
            "record_call_ready": False,
        }
        if mode == "preview" or plan.get("execution_ready") is not True:
            response["confirmation_required"] = mode == "preview"
            response["confirmation_action"] = (
                {
                    "tool": "material_studio_gui_execute_view_replay",
                    "payload": {
                        "project_id": safe_project,
                        "revision": revision,
                        "view_name": plan.get("selected_view_name"),
                        "execution_mode": "execute",
                    },
                }
                if mode == "preview" and plan.get("execution_ready") is True
                else None
            )
            return response

        manifest_path = self._view_replay_manifest_path(
            project_id=safe_project,
            revision=revision,
        )
        execution_lock_path = manifest_path.with_name(
            "gui_view_replay_execution.lock"
        )
        with _view_replay_write_lock(
            execution_lock_path,
            workspace_root=self.workspace_root,
            timeout_seconds=VIEW_REPLAY_WRITE_LOCK_TIMEOUT_SECONDS,
        ) as execution_transaction:
            activation = None
            if initial_probe.get("target_window_is_foreground") is not True:
                activation = self.activate(
                    project_id=safe_project,
                    revision=revision,
                )
                if activation.get("activation_verified") is not True:
                    response.update(
                        {
                            "status": "blocked",
                            "execution_ready": False,
                            "execution_block_reasons": [
                                "target_window_activation_not_verified"
                            ],
                            "activation": activation,
                            "execution_transaction": execution_transaction,
                        }
                    )
                    return response

            fresh_probe = self.probe_view_replay_accessibility(
                project_id=safe_project,
                revision=revision,
            )
            fresh_plan = self._local_view_replay_plan(
                audit,
                project_id=safe_project,
                revision=revision,
                probe=fresh_probe,
                requested_view_name=str(plan["selected_view_name"]),
            )
            if fresh_plan.get("execution_ready") is not True:
                response.update(
                    {
                        "status": "blocked",
                        "execution_ready": False,
                        "execution_block_reasons": fresh_plan.get(
                            "block_reasons"
                        )
                        or ["fresh_local_uia_preflight_failed"],
                        "activation": activation,
                        "local_uia_probe": fresh_probe,
                        "plan": fresh_plan,
                        "execution_transaction": execution_transaction,
                    }
                )
                return response

            prepared = self.prepare_view_replay(
                audit,
                project_id=safe_project,
                revision=revision,
                runtime_accessibility_evidence=fresh_probe.get("evidence"),
            )
            selected_view_name = str(fresh_plan["selected_view_name"])
            selected_view = next(
                (
                    item
                    for item in prepared.get("manifest", {}).get("views", [])
                    if isinstance(item, dict)
                    and item.get("view_name") == selected_view_name
                ),
                None,
            )
            if not isinstance(selected_view, dict):
                raise GuiError(
                    "prepared view replay manifest lost the selected view"
                )
            execution_recipe = selected_view.get("execution_recipe")
            if not isinstance(execution_recipe, dict):
                raise GuiError("selected view has no execution recipe")
            supported, support_reasons = _local_uia_recipe_support(
                execution_recipe
            )
            if not supported:
                raise GuiError(
                    "selected prepared recipe is not locally executable: "
                    + ", ".join(support_reasons)
                )
            current_command_evidence = _materials_studio_view_command_evidence()
            for target_field in (
                "accessibility_target",
                "movement_accessibility_target",
            ):
                target = execution_recipe.get(target_field)
                if isinstance(target, dict) and target.get("registry_sha256"):
                    if target.get(
                        "registry_sha256"
                    ) != current_command_evidence.get("registry_sha256"):
                        raise GuiError(
                            "installed Materials Studio view registry changed after prepare"
                        )

            current_status = self.status(
                project_id=safe_project,
                revision=revision,
            )
            target_window = (
                current_status.get("target_window")
                if isinstance(current_status.get("target_window"), dict)
                else {}
            )
            target_resolution = (
                current_status.get("target_window_resolution")
                if isinstance(
                    current_status.get("target_window_resolution"), dict
                )
                else {}
            )
            pre_action_reasons = _local_view_replay_status_block_reasons(
                current_status
            )
            if pre_action_reasons:
                response.update(
                    {
                        "status": "blocked",
                        "execution_ready": False,
                        "execution_block_reasons": pre_action_reasons,
                        "activation": activation,
                        "local_uia_probe": fresh_probe,
                        "plan": fresh_plan,
                        "manifest_modified": True,
                        "execution_transaction": execution_transaction,
                    }
                )
                return response

            structure_path = _target_structure_path(target_resolution)
            structure_sha256_before = None
            structure_size_before = None
            if structure_path is not None and structure_path.exists():
                structure_sha256_before, structure_size_before = _sha256_file(
                    structure_path
                )

            miller_transaction = str(
                execution_recipe.get("recipe_kind") or ""
            ) in MILLER_VIEW_ONTO_RECIPE_KINDS
            evidence_dir = (
                self.workspace_root
                / "screenshots"
                / safe_project
                / f"r{revision:03d}"
                / "view_replay_transactions"
                / (
                    re.sub(r"[^A-Za-z0-9_.-]+", "_", selected_view_name).strip("._")
                    + "_"
                    + uuid.uuid4().hex[:12]
                )
            ).resolve()
            _ensure_inside(self.workspace_root, evidence_dir)
            evidence_dir.mkdir(parents=True, exist_ok=False)
            action_receipt = self.view_replay_backend.execute_standard_recipe(
                window_handle=int(target_window["handle"]),
                expected_window_title=str(target_window["title"]),
                execution_recipe=execution_recipe,
                toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
                command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
                structure_path=structure_path,
                evidence_dir=evidence_dir,
                expected_revision=revision,
            )
            snapshot_result: dict[str, Any] | None = None
            snapshot_error: str | None = None
            if miller_transaction:
                if action_receipt.get("execution_succeeded") is True:
                    aligned_path_raw = action_receipt.get(
                        "aligned_screenshot_path"
                    )
                    try:
                        if not aligned_path_raw:
                            raise GuiError(
                                "transactional Miller aligned screenshot path is missing"
                            )
                        aligned_path = Path(
                            str(aligned_path_raw)
                        ).expanduser().resolve()
                        _ensure_inside(self.workspace_root, aligned_path)
                        if not aligned_path.exists() or not aligned_path.is_file():
                            raise GuiError(
                                "transactional Miller aligned screenshot is unavailable"
                            )
                        snapshot_result = {
                            "project_id": safe_project,
                            "revision": revision,
                            "screenshot_path": str(aligned_path),
                            "capture_phase": "aligned_before_transaction_cleanup",
                            "analysis": _analyze_bmp_snapshot(aligned_path),
                        }
                    except Exception as exc:
                        snapshot_error = str(exc)
                else:
                    snapshot_error = (
                        "transactional Miller execution failed before an aligned "
                        "screenshot was available"
                    )
            else:
                try:
                    snapshot_result = self.snapshot(
                        label=f"view_replay_{selected_view_name}_executed",
                        project_id=safe_project,
                        revision=revision,
                    )
                except Exception as exc:
                    snapshot_error = str(exc)

            transaction_runtime_preflight_persisted = not miller_transaction
            transaction_runtime_preflight_error: str | None = None
            if (
                miller_transaction
                and action_receipt.get("execution_succeeded") is True
                and isinstance(action_receipt.get("runtime_ui_evidence"), dict)
            ):
                try:
                    prepared = self.prepare_view_replay(
                        audit,
                        project_id=safe_project,
                        revision=revision,
                        runtime_ui_evidence=action_receipt["runtime_ui_evidence"],
                        runtime_accessibility_evidence=fresh_probe.get("evidence"),
                    )
                    transaction_runtime_preflight_persisted = True
                except Exception as exc:
                    transaction_runtime_preflight_error = str(exc)

            structure_sha256_after = None
            structure_size_after = None
            if structure_path is not None and structure_path.exists():
                structure_sha256_after, structure_size_after = _sha256_file(
                    structure_path
                )
            structure_unchanged = bool(
                structure_sha256_before is not None
                and structure_sha256_before == structure_sha256_after
                and structure_size_before == structure_size_after
            )
            post_status = self.status(
                project_id=safe_project,
                revision=revision,
            )
            post_action_reasons = _local_view_replay_status_block_reasons(
                post_status
            )
            execution_succeeded = bool(
                action_receipt.get("execution_succeeded") is True
                and not post_action_reasons
                and structure_unchanged
                and transaction_runtime_preflight_persisted
                and isinstance(snapshot_result, dict)
                and snapshot_result.get("analysis", {}).get("readable") is True
            )
            post_action_template = _local_view_replay_record_template(
                project_id=safe_project,
                revision=revision,
                view_name=selected_view_name,
                execution_recipe=execution_recipe,
                action_receipt=action_receipt,
                target_window=target_window,
                screenshot_path=(
                    snapshot_result.get("screenshot_path")
                    if isinstance(snapshot_result, dict)
                    else None
                ),
            )
            result_payload = {
                "project_id": safe_project,
                "revision": revision,
                "selected_view_name": selected_view_name,
                "execution_mode": mode,
                "execution_succeeded": execution_succeeded,
                "action_receipt": action_receipt,
                "snapshot": snapshot_result,
                "snapshot_error": snapshot_error,
                "transaction_runtime_preflight_persisted": (
                    transaction_runtime_preflight_persisted
                ),
                "transaction_runtime_preflight_error": (
                    transaction_runtime_preflight_error
                ),
                "transaction_evidence_dir": str(evidence_dir),
                "structure_path": str(structure_path)
                if structure_path is not None
                else None,
                "structure_sha256_before": structure_sha256_before,
                "structure_sha256_after": structure_sha256_after,
                "structure_unchanged": structure_unchanged,
                "post_action_window_status_block_reasons": post_action_reasons,
                "post_action_record_payload_template": post_action_template,
                "post_action_observation_required": True,
                "record_call_ready": False,
                "visual_acceptance_recorded": False,
                "acceptance_event_created": False,
                "revision_created": False,
                "manifest_path": prepared.get("manifest_path"),
                "runtime_accessibility_preflight_path": prepared.get(
                    "runtime_accessibility_preflight_path"
                ),
                "activation": activation,
                "execution_transaction": execution_transaction,
            }
            log_path = self._write_log(
                "execute_view_replay",
                project_id=safe_project,
                revision=revision,
                payload=result_payload,
            )
            response.update(
                {
                    **result_payload,
                    "status": (
                        "awaiting_visual_confirmation"
                        if execution_succeeded
                        else "execution_failed"
                    ),
                    "execution_ready": True,
                    "gui_input_performed": bool(
                        action_receipt.get("gui_input_performed") is True
                        or action_receipt.get("pointer_input_used") is True
                        or action_receipt.get("view_onto_native_command_mapping")
                        or
                        action_receipt.get("reset_invocation_succeeded") is True
                        or action_receipt.get("key_sequence_sent")
                    ),
                    "gui_modified": bool(
                        action_receipt.get("gui_transiently_modified") is True
                        or action_receipt.get("reset_invocation_succeeded") is True
                    ),
                    "structure_modified": not structure_unchanged,
                    "manifest_modified": True,
                    "local_uia_probe": fresh_probe,
                    "plan": fresh_plan,
                    "gui_log_path": str(log_path),
                    "confirmation_required": True,
                    "confirmation_action": {
                        "tool": "material_studio_gui_record_view_replay",
                        "payload_template": post_action_template,
                        "payload_template_is_directly_callable": False,
                        "required_observations": (
                            [
                                "model_visible",
                                "camera_matches_manifest",
                                "confirm_aligned_screenshot_shows_expected_plane_normal",
                            ]
                            if miller_transaction
                            else [
                                "model_visible",
                                "camera_matches_manifest",
                                "crystal_camera_evidence.view_direction_matches_manifest",
                                "crystal_camera_evidence.native_in_plane_roll_observed",
                            ]
                        ),
                    },
                }
            )
            return response

    def _local_view_replay_plan(
        self,
        audit: dict[str, Any],
        *,
        project_id: str,
        revision: int,
        probe: dict[str, Any],
        requested_view_name: str | None,
    ) -> dict[str, Any]:
        """Build a non-persisting local execution plan from a fresh UIA probe."""

        command_evidence = _materials_studio_view_command_evidence()
        status = self.status(project_id=project_id, revision=revision)
        evidence = probe.get("evidence") if isinstance(probe, dict) else None
        runtime_preflight: dict[str, Any]
        if isinstance(evidence, dict):
            runtime_preflight = (
                self._resolve_view_replay_runtime_accessibility_preflight(
                    status=status,
                    project_id=project_id,
                    revision=revision,
                    supplied_evidence=evidence,
                    command_evidence=command_evidence,
                    persist_supplied_evidence=False,
                )
            )
        else:
            runtime_preflight = {
                "status": "missing",
                "observation_available": False,
                "binding_verified": False,
                "automation_gate_satisfied": False,
                "block_reasons": ["local_uia_evidence_unavailable"],
            }
        steps = [
            _view_replay_step(view, index=index)
            for index, view in enumerate(audit.get("views") or [])
        ]
        for step in steps:
            step["execution_recipe"] = _view_replay_execution_recipe(
                step,
                command_evidence,
                model_type=str(audit.get("model_type") or "") or None,
                runtime_ui_preflight=None,
                runtime_accessibility_preflight=runtime_preflight,
                local_miller_transaction_supported=bool(
                    getattr(
                        self.view_replay_backend,
                        "miller_plane_transaction_supported",
                        False,
                    )
                ),
            )

        accepted_view_names = self._existing_view_replay_accepted_names(
            project_id=project_id,
            revision=revision,
            spec_fingerprint=str(audit.get("spec_fingerprint") or ""),
        )
        candidate_rows: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        for step in steps:
            item_view_name = str(step.get("view_name") or "")
            recipe = (
                step.get("execution_recipe")
                if isinstance(step.get("execution_recipe"), dict)
                else {}
            )
            locally_supported, support_reasons = _local_uia_recipe_support(recipe)
            accepted = item_view_name in accepted_view_names
            row = {
                "view_name": item_view_name,
                "accepted": accepted,
                "recipe_automation_ready": recipe.get("automation_ready") is True,
                "local_execution_supported": locally_supported,
                "block_reasons": support_reasons,
            }
            candidate_rows.append(row)
            if accepted:
                continue
            if requested_view_name is not None and item_view_name != requested_view_name:
                continue
            if locally_supported and selected is None:
                selected = step

        selected_recipe = (
            selected.get("execution_recipe")
            if isinstance(selected, dict)
            and isinstance(selected.get("execution_recipe"), dict)
            else None
        )
        selected_is_miller = bool(
            isinstance(selected_recipe, dict)
            and selected_recipe.get("recipe_kind") in MILLER_VIEW_ONTO_RECIPE_KINDS
        )
        block_reasons = list(probe.get("block_reasons") or [])
        required_probe_gate = (
            "safe_for_miller_plane_transaction"
            if selected_is_miller
            else "safe_for_standard_view_replay"
        )
        if probe.get(required_probe_gate) is not True:
            block_reasons.append(
                "local_uia_miller_transaction_probe_not_safe"
                if selected_is_miller
                else "local_uia_probe_not_safe"
            )
        if requested_view_name is not None and not any(
            row["view_name"] == requested_view_name for row in candidate_rows
        ):
            block_reasons.append("requested_view_not_in_manifest_selection")
        if requested_view_name is not None and any(
            row["view_name"] == requested_view_name and row["accepted"]
            for row in candidate_rows
        ):
            block_reasons.append("requested_view_already_accepted")
        if selected is None:
            block_reasons.append("no_pending_local_uia_view_ready")
        block_reasons = _unique_strings(str(item) for item in block_reasons)
        return {
            "project_id": project_id,
            "revision": revision,
            "requested_view_name": requested_view_name,
            "selected_view_name": (
                selected.get("view_name") if isinstance(selected, dict) else None
            ),
            "execution_ready": bool(selected is not None and not block_reasons),
            "accepted_view_names": sorted(accepted_view_names),
            "candidate_views": candidate_rows,
            "execution_recipe": selected_recipe,
            "runtime_accessibility_preflight": runtime_preflight,
            "block_reasons": block_reasons,
            "preview_has_no_gui_input": True,
            "preview_persists_no_manifest_or_evidence": True,
        }

    def _existing_view_replay_accepted_names(
        self,
        *,
        project_id: str,
        revision: int,
        spec_fingerprint: str,
    ) -> set[str]:
        """Read current accepted names without creating directories or writing files."""

        path = (
            self.workspace_root
            / sanitize_project_id(project_id)
            / "outputs"
            / f"r{revision:03d}"
            / "gui_view_replay_manifest.json"
        ).resolve()
        _ensure_inside(self.workspace_root, path)
        if not path.exists() or not path.is_file():
            return set()
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(manifest, dict):
            return set()
        if (
            manifest.get("project_id") != project_id
            or manifest.get("revision") != revision
            or str(manifest.get("spec_fingerprint") or "") != spec_fingerprint
        ):
            return set()
        _refresh_view_replay_summary(
            manifest,
            workspace_root=self.workspace_root,
            events_path=path.with_name("gui_view_replay_events.jsonl"),
        )
        summary = (
            manifest.get("replay_summary")
            if isinstance(manifest.get("replay_summary"), dict)
            else {}
        )
        return {
            str(item)
            for item in summary.get("accepted_view_names") or []
            if str(item)
        }

    @_serialize_view_replay_write
    def prepare_view_replay(
        self,
        audit: dict[str, Any],
        *,
        project_id: str,
        revision: int,
        runtime_ui_evidence: dict[str, Any] | None = None,
        runtime_accessibility_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a preview-only, externally replayable GUI view manifest."""

        safe_project = sanitize_project_id(project_id)
        if revision < 0:
            raise GuiError("revision must be non-negative")
        if str(audit.get("project_id") or "") != safe_project:
            raise GuiError("view audit project_id does not match the replay target")
        try:
            audit_revision = int(audit.get("revision"))
        except (TypeError, ValueError) as exc:
            raise GuiError("view audit revision is missing or invalid") from exc
        if audit_revision != revision:
            raise GuiError("view audit revision does not match the replay target")

        status = self.status(project_id=safe_project, revision=revision)
        command_evidence = _materials_studio_view_command_evidence()
        runtime_ui_preflight = self._resolve_view_replay_runtime_ui_preflight(
            status=status,
            project_id=safe_project,
            revision=revision,
            supplied_evidence=runtime_ui_evidence,
        )
        runtime_accessibility_preflight = (
            self._resolve_view_replay_runtime_accessibility_preflight(
                status=status,
                project_id=safe_project,
                revision=revision,
                supplied_evidence=runtime_accessibility_evidence,
                command_evidence=command_evidence,
            )
        )
        steps = [_view_replay_step(view, index=index) for index, view in enumerate(audit.get("views") or [])]
        supported_steps = [step for step in steps if step["supported"]]
        unsupported_steps = [step for step in steps if not step["supported"]]
        target_resolution = (
            status.get("target_window_resolution")
            if isinstance(status.get("target_window_resolution"), dict)
            else {}
        )
        target_window = status.get("target_window") if isinstance(status.get("target_window"), dict) else {}
        target_handle = target_window.get("handle")
        target_inventory_entry = next(
            (
                item
                for item in status.get("windows") or []
                if isinstance(item, dict) and item.get("handle") == target_handle
            ),
            {},
        )
        target_window_is_foreground = target_inventory_entry.get("is_foreground") is True
        target_identity_verified = target_resolution.get("matched_project_window") is True
        current_revision_loaded = status.get("current_revision_loaded") is True
        single_window_policy_ok = status.get("single_window_policy_ok") is True

        block_reasons: list[str] = []
        if not steps:
            block_reasons.append("no_views_requested")
        if unsupported_steps:
            block_reasons.append("unsupported_view_definition")
        if status.get("supported") is not True:
            block_reasons.append("gui_backend_unavailable")
        if status.get("target_window_pid_is_matstudio_process") is not True:
            block_reasons.append("target_window_pid_not_matstudio_process")
        if not single_window_policy_ok:
            block_reasons.extend(str(item) for item in status.get("single_window_violation_reasons") or [])
        if status.get("target_window_found") is not True:
            block_reasons.append("target_revision_window_not_found")
        if not target_identity_verified:
            block_reasons.append("target_revision_window_identity_unverified")
        if not current_revision_loaded:
            block_reasons.append("target_revision_not_loaded_in_gui")
        block_reasons = _unique_strings(block_reasons)

        activation_required = bool(target_window) and not target_window_is_foreground
        ready_for_external_replay = not block_reasons
        for step in steps:
            step["execution_recipe"] = _view_replay_execution_recipe(
                step,
                command_evidence,
                model_type=str(audit.get("model_type") or "") or None,
                runtime_ui_preflight=runtime_ui_preflight,
                runtime_accessibility_preflight=runtime_accessibility_preflight,
                local_miller_transaction_supported=bool(
                    getattr(
                        self.view_replay_backend,
                        "miller_plane_transaction_supported",
                        False,
                    )
                ),
            )
        next_tool = status.get("recommended_tool")
        next_action = status.get("recommended_action")
        if ready_for_external_replay:
            if activation_required:
                next_tool = "material_studio_gui_activate"
                next_action = "activate_verified_target_window_then_run_external_view_replay"
            else:
                next_tool = "material_studio_gui_copy_script_assist"
                next_action = "use_computer_use_or_reviewed_copy_script_for_each_manifest_view"

        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        manifest: dict[str, Any] = {
            "schema_version": VIEW_REPLAY_MANIFEST_SCHEMA_VERSION,
            "kind": "materials_studio_gui_view_replay_manifest",
            "generated_at": generated_at,
            "project_id": safe_project,
            "revision": revision,
            "spec_fingerprint": audit.get("spec_fingerprint"),
            "model_type": audit.get("model_type"),
            "source": "model_view_audit",
            "view_selection": audit.get("view_selection"),
            "replay_mode": "preview_manifest_only",
            "replay_status": "ready_for_external_replay" if ready_for_external_replay else "blocked",
            "requested_view_count": len(steps),
            "supported_view_count": len(supported_steps),
            "unsupported_view_count": len(unsupported_steps),
            "view_names": [step["view_name"] for step in steps],
            "views": steps,
            "preflight": {
                "ready_for_external_replay": ready_for_external_replay,
                "block_reasons": block_reasons,
                "single_window_policy_ok": single_window_policy_ok,
                "single_window_violation_reasons": status.get("single_window_violation_reasons") or [],
                "matstudio_process_count": status.get("process_count"),
                "matstudio_window_count": status.get("window_count"),
                "target_window_found": status.get("target_window_found"),
                "target_window_identity_verified": target_identity_verified,
                "target_window_is_foreground": target_window_is_foreground,
                "activation_required": activation_required,
                "current_revision_loaded": current_revision_loaded,
                "target_window": target_window or None,
                "target_window_resolution": target_resolution or None,
            },
            "viewport_control": {
                "local_mcp_backend": (
                    "standard_isometric_and_transactional_miller_plane_uia"
                ),
                "arbitrary_camera_materialscript_api": "not_verified_for_materials_studio_2020",
                "computer_use": "external_orchestrator_required",
                "reviewed_copy_script": "accepted_only_after_local_api_review",
                "execute_supported_by_this_tool": False,
                "execution_block_reason": (
                    "No authoritative arbitrary camera-vector MaterialsScript API was found in the local "
                    "Materials Studio 2020 help. Use Computer Use or locally reviewed Copy Script output."
                ),
                "known_native_commands": command_evidence,
            },
            "runtime_ui_preflight": runtime_ui_preflight,
            "runtime_accessibility_preflight": runtime_accessibility_preflight,
            "safety_gate": {
                "activate_target_window_before_screenshot_or_input": True,
                "verify_project_revision_wrapper_identity": True,
                "require_exactly_one_matstudio_process": False,
                "require_effective_target_window_isolation": True,
                "unrelated_matstudio_processes_allowed": True,
                "require_single_window_policy_ok": True,
                "require_post_action_visual_confirmation": True,
                "pre_activation_screenshot_may_capture_occluding_window": True,
                "blind_toolbar_or_coordinate_action_allowed": False,
                "structure_mutation_allowed": False,
                "launch_new_matstudio_process_allowed": False,
            },
            "post_action_verification": {
                "record_tool": "material_studio_gui_record_view_replay",
                "record_each_view": True,
                "compare_against_fields": [
                    "camera_direction",
                    "camera_up",
                    "target",
                    "orthographic_width_angstrom",
                    "orthographic_height_angstrom",
                    "atom_projection_count",
                    "projection_bbox_angstrom",
                ],
                "fresh_snapshot_after_each_view": True,
            },
            "next_action": {
                "recommended_tool": next_tool,
                "recommended_action": next_action,
                "payload_hint": {
                    "project_id": safe_project,
                    "revision": revision,
                    "take_snapshot": True,
                }
                if next_tool == "material_studio_gui_activate"
                else {
                    "project_id": safe_project,
                    "revision": revision,
                    "context": "Replay the persisted view manifest without changing the structure.",
                },
            },
            "replay_events": [],
        }
        manifest_path = self._view_replay_manifest_path(project_id=safe_project, revision=revision)
        prior_recipe_contract: dict[str, Any] | None = None
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise GuiError("existing view replay manifest is unreadable; refusing to overwrite replay evidence") from exc
            if not isinstance(existing_manifest, dict):
                raise GuiError("existing view replay manifest is invalid; refusing to overwrite replay evidence")
            existing_identity = (
                existing_manifest.get("project_id"),
                existing_manifest.get("revision"),
                existing_manifest.get("spec_fingerprint"),
            )
            requested_identity = (safe_project, revision, audit.get("spec_fingerprint"))
            if existing_identity != requested_identity:
                raise GuiError("existing view replay manifest identity differs from the requested immutable revision")
            prior_recipe_contract = view_replay_manifest_recipe_contract_status(
                existing_manifest
            )
            preserved_events = [
                event
                for event in existing_manifest.get("replay_events") or []
                if isinstance(event, dict)
            ]
            manifest["replay_events"] = preserved_events
            manifest["preserved_replay_event_count"] = len(preserved_events)
            manifest["prior_manifest_generated_at"] = existing_manifest.get("generated_at")
        _refresh_view_replay_summary(
            manifest,
            workspace_root=self.workspace_root,
            events_path=manifest_path.with_name("gui_view_replay_events.jsonl"),
        )
        current_recipe_contract = manifest.get("recipe_contract")
        manifest["recipe_migration"] = {
            "performed": bool(
                prior_recipe_contract is not None
                and prior_recipe_contract.get("current") is not True
                and isinstance(current_recipe_contract, dict)
                and current_recipe_contract.get("current") is True
            ),
            "prior_status": (
                prior_recipe_contract.get("status")
                if isinstance(prior_recipe_contract, dict)
                else None
            ),
            "current_status": (
                current_recipe_contract.get("status")
                if isinstance(current_recipe_contract, dict)
                else None
            ),
            "prior_outdated_view_names": (
                list(prior_recipe_contract.get("outdated_view_names") or [])
                if isinstance(prior_recipe_contract, dict)
                else []
            ),
            "preserved_replay_event_count": len(manifest.get("replay_events") or []),
            "revision_created": False,
            "structure_modified": False,
        }
        _write_json_atomic(manifest_path, manifest)
        log_path = self._write_log(
            "prepare_view_replay",
            project_id=safe_project,
            revision=revision,
            payload={
                "manifest_path": str(manifest_path),
                "view_names": manifest["view_names"],
                "replay_status": manifest["replay_status"],
                "preflight": manifest["preflight"],
                "runtime_ui_preflight": manifest["runtime_ui_preflight"],
                "runtime_accessibility_preflight": manifest[
                    "runtime_accessibility_preflight"
                ],
                "event_journal": manifest.get("event_journal"),
                "next_action": manifest["next_action"],
            },
        )
        return {
            "project_id": safe_project,
            "revision": revision,
            "manifest_path": str(manifest_path),
            "gui_log_path": str(log_path),
            "replay_status": manifest["replay_status"],
            "ready_for_external_replay": ready_for_external_replay,
            "preflight_block_reasons": block_reasons,
            "runtime_ui_preflight_path": runtime_ui_preflight.get("artifact_path"),
            "runtime_ui_preflight": runtime_ui_preflight,
            "runtime_accessibility_preflight_path": (
                runtime_accessibility_preflight.get("artifact_path")
            ),
            "runtime_accessibility_preflight": runtime_accessibility_preflight,
            "activation_required": activation_required,
            "view_selection": manifest.get("view_selection"),
            "view_names": manifest["view_names"],
            "requested_view_count": len(steps),
            "supported_view_count": len(supported_steps),
            "unsupported_view_count": len(unsupported_steps),
            "replay_continuation": manifest.get("replay_continuation"),
            "event_journal": manifest.get("event_journal"),
            "recipe_contract": manifest.get("recipe_contract"),
            "recipe_migration": manifest.get("recipe_migration"),
            "next_action": manifest["next_action"],
            "next_action_resolution": manifest.get("next_action_resolution"),
            "manifest": manifest,
        }

    def _resolve_view_replay_runtime_accessibility_preflight(
        self,
        *,
        status: dict[str, Any],
        project_id: str,
        revision: int,
        supplied_evidence: dict[str, Any] | None,
        command_evidence: dict[str, Any],
        persist_supplied_evidence: bool = True,
    ) -> dict[str, Any]:
        """Load or persist exact-window accessibility and toolbar-order evidence."""

        artifact_path = self._view_replay_runtime_accessibility_preflight_path(
            project_id=project_id,
            revision=revision,
            create_parent=bool(
                supplied_evidence is not None and persist_supplied_evidence
            ),
        )
        if supplied_evidence is None and not artifact_path.exists():
            return {
                "status": "missing",
                "observation_available": False,
                "binding_verified": False,
                "automation_gate_satisfied": False,
                "artifact_path": str(artifact_path),
                "artifact_exists": False,
                "block_reasons": ["runtime_view_accessibility_preflight_missing"],
            }

        evidence: dict[str, Any]
        observed_at: str | None = None
        source = "supplied"
        if supplied_evidence is not None:
            evidence = _normalize_view_runtime_accessibility_evidence(
                supplied_evidence
            )
            screenshot_path = evidence.get("screenshot_path")
            if screenshot_path:
                screenshot = Path(str(screenshot_path)).expanduser().resolve()
                _ensure_inside(self.workspace_root, screenshot)
                if not screenshot.exists() or not screenshot.is_file():
                    raise GuiError(
                        f"runtime accessibility preflight screenshot does not exist: {screenshot}"
                    )
                evidence["screenshot_path"] = str(screenshot)
            binding = _view_replay_runtime_ui_binding(
                status,
                project_id=project_id,
                revision=revision,
                evidence=evidence,
            )
            if binding.get("ok") is not True:
                reasons = ", ".join(
                    str(item) for item in binding.get("rejection_reasons") or []
                )
                raise GuiError(
                    "runtime_accessibility_evidence does not match the current single "
                    "Materials Studio wrapper window: "
                    f"{reasons or 'window binding rejected'}"
                )
            observed_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        else:
            source = "persisted"
            try:
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                if not isinstance(artifact, dict):
                    raise ValueError("artifact root must be an object")
                if (
                    artifact.get("project_id") != project_id
                    or artifact.get("revision") != revision
                ):
                    raise ValueError("artifact project/revision identity mismatch")
                evidence = _normalize_view_runtime_accessibility_evidence(
                    artifact.get("evidence")
                )
                observed_at = str(artifact.get("observed_at") or "") or None
            except Exception as exc:
                return {
                    "status": "invalid_persisted_evidence",
                    "observation_available": False,
                    "binding_verified": False,
                    "automation_gate_satisfied": False,
                    "artifact_path": str(artifact_path),
                    "artifact_exists": True,
                    "artifact_error": str(exc),
                    "block_reasons": [
                        "runtime_view_accessibility_preflight_artifact_invalid"
                    ],
                }
            binding = _view_replay_runtime_ui_binding(
                status,
                project_id=project_id,
                revision=revision,
                evidence=evidence,
            )

        anonymous_toolbar_mapping = _resolve_verified_anonymous_toolbar_mappings(
            evidence,
            command_evidence,
        )
        gate_reasons = [
            f"runtime_accessibility_binding_{reason}"
            for reason in binding.get("rejection_reasons") or []
        ]
        if evidence.get("accessibility_tree_refreshed") is not True:
            gate_reasons.append("runtime_accessibility_tree_not_refreshed")
        registry_sha256_at_observation = (
            str(artifact.get("registry_sha256_at_observation") or "") or None
            if source == "persisted"
            else anonymous_toolbar_mapping.get("registry_sha256")
        )
        if (
            source == "persisted"
            and anonymous_toolbar_mapping.get("attempted") is True
            and registry_sha256_at_observation
            != anonymous_toolbar_mapping.get("registry_sha256")
        ):
            gate_reasons.append(
                "runtime_anonymous_toolbar_registry_changed_since_observation"
            )
        gate_reasons = _unique_strings(gate_reasons)
        base_gate_satisfied = not gate_reasons
        controls = evidence.get("controls") or []
        observed_command_ids = {
            str(control.get("command_id"))
            for control in controls
            if isinstance(control, dict) and control.get("command_id")
        }
        named_ready_command_ids = {
            str(control.get("command_id"))
            for control in controls
            if isinstance(control, dict)
            and control.get("command_id")
            and control.get("named_control_observed") is True
            and control.get("invoke_supported") is True
        }
        semantic_mapped_command_ids = {
            str(item)
            for item in anonymous_toolbar_mapping.get("mapped_command_ids") or []
        }
        semantic_ready_command_ids = {
            str(item)
            for item in anonymous_toolbar_mapping.get(
                "invocation_ready_command_ids"
            )
            or []
        }
        resolved_observed_command_ids = (
            observed_command_ids | semantic_mapped_command_ids
        )
        resolved_ready_command_ids = (
            named_ready_command_ids | semantic_ready_command_ids
        )
        all_standard_recipe_controls_observed = (
            set(VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS)
            <= resolved_observed_command_ids
        )
        all_observed_named_controls_invocable = bool(controls) and all(
            isinstance(control, dict)
            and control.get("named_control_observed") is True
            and control.get("invoke_supported") is True
            for control in controls
        )
        all_standard_recipe_controls_ready = bool(
            all_standard_recipe_controls_observed
            and set(VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS)
            <= resolved_ready_command_ids
        )
        automation_gate_satisfied = bool(
            base_gate_satisfied and all_standard_recipe_controls_ready
        )
        observed_control_blocks_automation = bool(
            (
                controls
                and any(
                    str(control.get("command_id")) not in resolved_ready_command_ids
                    for control in controls
                    if isinstance(control, dict) and control.get("command_id")
                )
            )
            or (
                anonymous_toolbar_mapping.get("attempted") is True
                and (
                    not anonymous_toolbar_mapping.get("command_mappings")
                    or not all(
                        item.get("invocation_ready") is True
                        for item in anonymous_toolbar_mapping.get(
                            "command_mappings"
                        )
                        or []
                    )
                )
            )
        )
        if supplied_evidence is not None and persist_supplied_evidence:
            artifact = {
                "schema_version": 2,
                "kind": "materials_studio_view_runtime_accessibility_preflight",
                "observed_at": observed_at,
                "project_id": project_id,
                "revision": revision,
                "evidence": evidence,
                "binding_at_observation": binding,
                "registry_sha256_at_observation": (
                    anonymous_toolbar_mapping.get("registry_sha256")
                ),
                "anonymous_toolbar_mapping_at_observation": (
                    anonymous_toolbar_mapping
                ),
            }
            _write_json_atomic(artifact_path, artifact)
        return {
            "status": (
                "verified_automation_ready"
                if automation_gate_satisfied
                else "verified_observation_blocks_automation"
                if base_gate_satisfied and observed_control_blocks_automation
                else "verified_partial_control_coverage"
                if base_gate_satisfied
                else "verified_incomplete"
                if binding.get("ok") is True
                else "stale_window_binding"
            ),
            "source": source,
            "observation_available": True,
            "observed_at": observed_at,
            "binding_verified": binding.get("ok") is True,
            "base_gate_satisfied": base_gate_satisfied,
            "all_observed_named_controls_invocable": (
                all_observed_named_controls_invocable
            ),
            "all_standard_recipe_controls_observed": (
                all_standard_recipe_controls_observed
            ),
            "all_standard_recipe_controls_ready": (
                all_standard_recipe_controls_ready
            ),
            "resolved_observed_command_ids": sorted(
                resolved_observed_command_ids
            ),
            "resolved_ready_command_ids": sorted(resolved_ready_command_ids),
            "automation_gate_satisfied": automation_gate_satisfied,
            "artifact_path": str(artifact_path),
            "artifact_exists": artifact_path.exists(),
            "binding": binding,
            "evidence": evidence,
            "controls": controls,
            "anonymous_toolbar_mapping": anonymous_toolbar_mapping,
            "semantic_command_mappings": anonymous_toolbar_mapping.get(
                "command_mappings"
            ),
            "registry_sha256_at_observation": registry_sha256_at_observation,
            "unnamed_toolbar_children_observed": evidence.get(
                "unnamed_toolbar_children_observed"
            ),
            "block_reasons": gate_reasons,
        }

    def _resolve_view_replay_runtime_ui_preflight(
        self,
        *,
        status: dict[str, Any],
        project_id: str,
        revision: int,
        supplied_evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Load or persist runtime UI evidence and rebind it to the live wrapper window."""

        artifact_path = self._view_replay_runtime_ui_preflight_path(
            project_id=project_id,
            revision=revision,
        )
        if supplied_evidence is None and not artifact_path.exists():
            return {
                "status": "missing",
                "observation_available": False,
                "binding_verified": False,
                "automation_gate_satisfied": False,
                "artifact_path": str(artifact_path),
                "artifact_exists": False,
                "block_reasons": ["runtime_miller_plane_ui_preflight_missing"],
            }

        evidence: dict[str, Any]
        observed_at: str | None = None
        source = "supplied"
        if supplied_evidence is not None:
            evidence = _normalize_miller_runtime_ui_evidence(
                supplied_evidence,
                workspace_root=self.workspace_root,
            )
            screenshot_path = evidence.get("screenshot_path")
            if screenshot_path:
                screenshot = Path(str(screenshot_path)).expanduser().resolve()
                _ensure_inside(self.workspace_root, screenshot)
                if not screenshot.exists() or not screenshot.is_file():
                    raise GuiError(f"runtime UI preflight screenshot does not exist: {screenshot}")
                evidence["screenshot_path"] = str(screenshot)
            binding = _view_replay_runtime_ui_binding(
                status,
                project_id=project_id,
                revision=revision,
                evidence=evidence,
            )
            if binding.get("ok") is not True:
                reasons = ", ".join(str(item) for item in binding.get("rejection_reasons") or [])
                raise GuiError(
                    "runtime_ui_evidence does not match the current single Materials Studio wrapper "
                    f"window: {reasons or 'window binding rejected'}"
                )
            observed_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            artifact = {
                "schema_version": 2,
                "kind": "materials_studio_miller_plane_runtime_ui_preflight",
                "observed_at": observed_at,
                "project_id": project_id,
                "revision": revision,
                "evidence": evidence,
                "binding_at_observation": binding,
            }
            _write_json_atomic(artifact_path, artifact)
        else:
            source = "persisted"
            try:
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                if not isinstance(artifact, dict):
                    raise ValueError("artifact root must be an object")
                if artifact.get("project_id") != project_id or artifact.get("revision") != revision:
                    raise ValueError("artifact project/revision identity mismatch")
                evidence = _normalize_miller_runtime_ui_evidence(
                    artifact.get("evidence"),
                    workspace_root=self.workspace_root,
                )
                observed_at = str(artifact.get("observed_at") or "") or None
            except Exception as exc:
                return {
                    "status": "invalid_persisted_evidence",
                    "observation_available": False,
                    "binding_verified": False,
                    "automation_gate_satisfied": False,
                    "artifact_path": str(artifact_path),
                    "artifact_exists": True,
                    "artifact_error": str(exc),
                    "block_reasons": ["runtime_miller_plane_ui_preflight_artifact_invalid"],
                }
            binding = _view_replay_runtime_ui_binding(
                status,
                project_id=project_id,
                revision=revision,
                evidence=evidence,
            )

        gate_reasons = _miller_runtime_ui_gate_block_reasons(evidence, binding)
        selection_profile = _miller_runtime_ui_selection_profile(evidence)
        gate_satisfied = not gate_reasons
        return {
            "status": (
                "verified_complete"
                if gate_satisfied
                else "verified_incomplete"
                if binding.get("ok") is True
                else "stale_window_binding"
            ),
            "source": source,
            "observation_available": True,
            "observed_at": observed_at,
            "binding_verified": binding.get("ok") is True,
            "automation_gate_satisfied": gate_satisfied,
            "selection_profile": selection_profile,
            "artifact_path": str(artifact_path),
            "artifact_exists": artifact_path.exists(),
            "binding": binding,
            "evidence": evidence,
            "block_reasons": gate_reasons,
        }

    def _view_replay_runtime_ui_preflight_path(
        self,
        *,
        project_id: str,
        revision: int,
    ) -> Path:
        """Return the immutable-revision-scoped runtime UI preflight artifact path."""

        safe_project = sanitize_project_id(project_id)
        path = (
            self.workspace_root
            / safe_project
            / "outputs"
            / f"r{revision:03d}"
            / "gui_view_replay_runtime_preflight.json"
        ).resolve()
        _ensure_inside(self.workspace_root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _view_replay_runtime_accessibility_preflight_path(
        self,
        *,
        project_id: str,
        revision: int,
        create_parent: bool = True,
    ) -> Path:
        """Return the immutable-revision-scoped accessibility preflight path."""

        safe_project = sanitize_project_id(project_id)
        path = (
            self.workspace_root
            / safe_project
            / "outputs"
            / f"r{revision:03d}"
            / "gui_view_replay_accessibility_preflight.json"
        ).resolve()
        _ensure_inside(self.workspace_root, path)
        if create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @_serialize_view_replay_write
    def record_view_replay(
        self,
        *,
        project_id: str,
        revision: int,
        view_name: str,
        source: str,
        model_visible: bool,
        camera_matches_manifest: bool,
        note: str | None = None,
        screenshot_path: str | Path | None = None,
        expected_window_handle: int | None = None,
        expected_window_title: str | None = None,
        native_command_id: str | None = None,
        accessibility_command_uses: list[dict[str, Any]] | None = None,
        key_sequence: list[str] | None = None,
        reset_before_key_sequence: bool | None = None,
        rotation_increment_degrees: float | None = None,
        modifier_keys: list[str] | None = None,
        keyboard_stages: list[dict[str, Any]] | None = None,
        rotation_increment_restored_degrees: float | None = None,
        movement_options_command_id: str | None = None,
        movement_angle_control_id: str | None = None,
        movement_screen_factor_control_id: str | None = None,
        movement_screen_factor: float | None = None,
        movement_dialog_closed: bool | None = None,
        reviewed_copy_script_evidence: dict[str, Any] | None = None,
        crystal_camera_evidence: dict[str, Any] | None = None,
        miller_plane_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record externally executed view replay evidence without driving the GUI."""

        safe_project = sanitize_project_id(project_id)
        source = source.strip()
        allowed_sources = {
            "computer_use",
            "local_gui_fallback",
            "reviewed_copy_script",
            "manual_review",
        }
        if source not in allowed_sources:
            raise GuiError(f"unsupported view replay source: {source!r}")
        normalized_copy_script_evidence: dict[str, Any] | None = None
        if reviewed_copy_script_evidence is not None:
            if source != "reviewed_copy_script":
                raise GuiError(
                    "reviewed_copy_script_evidence is allowed only when source is "
                    "reviewed_copy_script"
                )
            normalized_copy_script_evidence = (
                _normalize_reviewed_copy_script_evidence(
                    reviewed_copy_script_evidence
                )
            )
        native_command: dict[str, str] | None = None
        if native_command_id is not None:
            native_command_id = native_command_id.strip()
            native_command = next(
                (
                    dict(command)
                    for command in MATERIALS_STUDIO_2020_VIEW_COMMANDS
                    if command.get("command_id") == native_command_id
                ),
                None,
            )
            if native_command is None:
                raise GuiError(f"unsupported Materials Studio 2020 view command: {native_command_id!r}")
        manifest_path = self._view_replay_manifest_path(project_id=safe_project, revision=revision)
        if not manifest_path.exists():
            raise GuiError("view replay manifest does not exist; prepare it before recording replay evidence")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GuiError("view replay manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise GuiError("view replay manifest must contain a JSON object")
        matching_view = next(
            (
                view
                for view in manifest.get("views") or []
                if isinstance(view, dict) and view.get("view_name") == view_name
            ),
            None,
        )
        if matching_view is None:
            raise GuiError(f"view {view_name!r} is not present in the replay manifest")
        if matching_view.get("supported") is not True:
            raise GuiError(f"view {view_name!r} is unsupported and cannot be confirmed")
        execution_recipe = (
            matching_view.get("execution_recipe")
            if isinstance(matching_view.get("execution_recipe"), dict)
            else {}
        )
        manifest_recipe_contract = view_replay_manifest_recipe_contract_status(manifest)
        matching_recipe_contract = _view_replay_recipe_contract_status(
            execution_recipe,
            expected_recipe_kind=_expected_view_replay_recipe_kind(
                matching_view,
                model_type=str(manifest.get("model_type") or "") or None,
            ),
        )
        if (
            manifest_recipe_contract.get("manifest_schema_current") is not True
            or matching_recipe_contract.get("recording_allowed") is not True
        ):
            reasons = _unique_strings(
                [
                    *(
                        manifest_recipe_contract.get("reasons")
                        if isinstance(manifest_recipe_contract.get("reasons"), list)
                        else []
                    ),
                    *(
                        matching_recipe_contract.get("reasons")
                        if isinstance(matching_recipe_contract.get("reasons"), list)
                        else []
                    ),
                ]
            )
            raise GuiError(
                "view replay recipe is not current and cannot accept new evidence; "
                "continue the GUI view replay to regenerate the manifest while preserving prior events"
                + (f" ({', '.join(reasons)})" if reasons else "")
            )
        normalized_accessibility_command_uses = (
            _normalize_view_replay_accessibility_command_uses(
                accessibility_command_uses,
                execution_recipe=execution_recipe,
            )
        )
        if native_command_id is not None and execution_recipe:
            allowed_native_commands = {
                str(item)
                for item in execution_recipe.get("allowed_native_command_ids") or []
                if item
            }
            if native_command_id not in allowed_native_commands:
                raise GuiError(
                    f"view {view_name!r} does not allow native command {native_command_id!r}; "
                    "use the prepared execution recipe or reviewed external evidence"
                )
        recipe_kind = execution_recipe.get("recipe_kind")
        crystal_standard_view_recipe = (
            recipe_kind == CRYSTAL_STANDARD_VIEW_RECIPE_KIND
        )
        miller_plane_recipe = recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS
        direction_via_miller_plane_recipe = (
            recipe_kind == "crystal_direction_via_collinear_miller_plane_view_onto"
        )
        normalized_crystal_camera_evidence: dict[str, Any] | None = None
        if crystal_camera_evidence is not None:
            if not crystal_standard_view_recipe:
                raise GuiError(
                    f"view {view_name!r} does not define a crystal native-roll camera contract"
                )
            normalized_crystal_camera_evidence = (
                _normalize_crystal_standard_view_camera_evidence(
                    crystal_camera_evidence,
                    expected_camera_match_scope=str(
                        (execution_recipe.get("camera_match_contract") or {}).get(
                            "scope"
                        )
                        or ""
                    ),
                )
            )
        normalized_miller_plane_evidence: dict[str, Any] | None = None
        if miller_plane_evidence is not None:
            if not miller_plane_recipe:
                raise GuiError(
                    f"view {view_name!r} does not define a Miller-plane View Onto recipe"
                )
            expected_indices = _normalize_miller_plane_indices(
                execution_recipe.get("miller_plane_indices"),
                field_name="execution_recipe.miller_plane_indices",
            )
            expected_dialog_indices = _normalize_miller_plane_indices(
                execution_recipe.get("dialog_miller_indices"),
                field_name="execution_recipe.dialog_miller_indices",
            )
            normalized_miller_plane_evidence = _normalize_miller_plane_replay_evidence(
                miller_plane_evidence,
                expected_selection_method=str(execution_recipe.get("selection_method") or ""),
                expected_indices=expected_indices,
                expected_dialog_indices=expected_dialog_indices,
                expected_properties_label=str(
                    execution_recipe.get("properties_miller_label") or ""
                ),
                expected_camera_match_scope=str(
                    (execution_recipe.get("camera_match_contract") or {}).get("scope") or ""
                ),
                requires_direction_match=direction_via_miller_plane_recipe,
                workspace_root=self.workspace_root,
            )

        if keyboard_stages is not None and key_sequence is not None:
            raise GuiError("keyboard_stages and key_sequence are mutually exclusive")
        normalized_keyboard_stages: list[dict[str, Any]] | None = None
        if keyboard_stages is not None:
            normalized_keyboard_stages = _normalize_view_replay_keyboard_stages(keyboard_stages)
            expected_keyboard_stages = execution_recipe.get("keyboard_stages")
            if not isinstance(expected_keyboard_stages, list):
                raise GuiError(f"view {view_name!r} does not define staged keyboard evidence")
            expected_normalized_stages = _normalize_view_replay_keyboard_stages(
                [
                    {
                        key: stage.get(key)
                        for key in VIEW_REPLAY_KEYBOARD_STAGE_FIELDS
                    }
                    for stage in expected_keyboard_stages
                    if isinstance(stage, dict)
                ]
            )
            if len(normalized_keyboard_stages) != len(expected_normalized_stages):
                raise GuiError(
                    f"view {view_name!r} requires {len(expected_normalized_stages)} keyboard stages"
                )
            for stage_index, (actual_stage, expected_stage) in enumerate(
                zip(normalized_keyboard_stages, expected_normalized_stages),
                start=1,
            ):
                if actual_stage["key_sequence"] != expected_stage["key_sequence"]:
                    raise GuiError(
                        f"view {view_name!r} keyboard stage {stage_index} requires key_sequence "
                        f"{expected_stage['key_sequence']!r}; received {actual_stage['key_sequence']!r}"
                    )
                if actual_stage["modifier_keys"] != expected_stage["modifier_keys"]:
                    raise GuiError(
                        f"view {view_name!r} keyboard stage {stage_index} requires modifier_keys "
                        f"{expected_stage['modifier_keys']!r}"
                    )
                if abs(
                    float(actual_stage["rotation_increment_degrees"])
                    - float(expected_stage["rotation_increment_degrees"])
                ) > 1e-9:
                    raise GuiError(
                        f"view {view_name!r} keyboard stage {stage_index} requires "
                        f"rotation_increment_degrees={float(expected_stage['rotation_increment_degrees']):g}"
                    )

        normalized_key_sequence: list[str] | None = None
        if key_sequence is not None:
            normalized_key_sequence = [str(item).strip() for item in key_sequence]
            if not normalized_key_sequence:
                raise GuiError("key_sequence must contain at least one arrow key")
            invalid_keys = [item for item in normalized_key_sequence if item not in VIEW_REPLAY_ARROW_KEYS]
            if invalid_keys:
                raise GuiError(
                    "view replay key_sequence contains unsupported keys: "
                    + ", ".join(invalid_keys)
                )
            expected_key_sequence = execution_recipe.get("key_sequence")
            if not isinstance(expected_key_sequence, list):
                raise GuiError(f"view {view_name!r} does not define a documented keyboard sequence")
            if normalized_key_sequence != expected_key_sequence:
                raise GuiError(
                    f"view {view_name!r} requires key_sequence {expected_key_sequence!r}; "
                    f"received {normalized_key_sequence!r}"
                )

        normalized_modifier_keys: list[str] | None = None
        if modifier_keys is not None:
            normalized_modifier_keys = [str(item).strip() for item in modifier_keys]
            invalid_modifiers = [
                item for item in normalized_modifier_keys if item not in VIEW_REPLAY_MODIFIER_KEYS
            ]
            if invalid_modifiers:
                raise GuiError(
                    "view replay modifier_keys contains unsupported modifiers: "
                    + ", ".join(invalid_modifiers)
                )
            expected_modifiers = execution_recipe.get("modifier_keys")
            if not isinstance(expected_modifiers, list):
                raise GuiError(f"view {view_name!r} does not define keyboard modifiers")
            if normalized_modifier_keys != expected_modifiers:
                raise GuiError(
                    f"view {view_name!r} requires modifier_keys {expected_modifiers!r}; "
                    f"received {normalized_modifier_keys!r}"
                )

        if reset_before_key_sequence is not None:
            expected_reset = execution_recipe.get("reset_before_key_sequence")
            if expected_reset is None:
                raise GuiError(f"view {view_name!r} does not define a reset-before-key-sequence step")
            if bool(reset_before_key_sequence) is not bool(expected_reset):
                raise GuiError(
                    f"view {view_name!r} requires reset_before_key_sequence={bool(expected_reset)!r}"
                )

        if rotation_increment_degrees is not None:
            expected_increment = execution_recipe.get("rotation_increment_degrees")
            if expected_increment is None:
                raise GuiError(f"view {view_name!r} does not define a keyboard rotation increment")
            if abs(float(rotation_increment_degrees) - float(expected_increment)) > 1e-9:
                raise GuiError(
                    f"view {view_name!r} requires rotation_increment_degrees="
                    f"{float(expected_increment):g}"
                )

        normalized_restored_increment: float | None = None
        if rotation_increment_restored_degrees is not None:
            expected_restored_increment = execution_recipe.get("restore_rotation_increment_degrees")
            if expected_restored_increment is None:
                raise GuiError(f"view {view_name!r} does not define a restored rotation increment")
            normalized_restored_increment = float(rotation_increment_restored_degrees)
            if abs(normalized_restored_increment - float(expected_restored_increment)) > 1e-9:
                raise GuiError(
                    f"view {view_name!r} requires rotation_increment_restored_degrees="
                    f"{float(expected_restored_increment):g}"
                )

        normalized_movement_command_id = (
            movement_options_command_id.strip() if movement_options_command_id is not None else None
        )
        normalized_angle_control_id = (
            movement_angle_control_id.strip() if movement_angle_control_id is not None else None
        )
        normalized_screen_control_id = (
            movement_screen_factor_control_id.strip()
            if movement_screen_factor_control_id is not None
            else None
        )
        string_evidence = (
            (
                "movement_options_command_id",
                normalized_movement_command_id,
                execution_recipe.get("movement_options_command_id"),
            ),
            (
                "movement_angle_control_id",
                normalized_angle_control_id,
                execution_recipe.get("movement_angle_control_id"),
            ),
            (
                "movement_screen_factor_control_id",
                normalized_screen_control_id,
                execution_recipe.get("movement_screen_factor_control_id"),
            ),
        )
        for field_name, actual_value, expected_value in string_evidence:
            if actual_value is None:
                continue
            if expected_value is None:
                raise GuiError(f"view {view_name!r} does not define {field_name}")
            if actual_value != expected_value:
                raise GuiError(f"view {view_name!r} requires {field_name}={expected_value!r}")

        normalized_screen_factor: float | None = None
        if movement_screen_factor is not None:
            expected_screen_factor = execution_recipe.get("movement_screen_factor_expected")
            if expected_screen_factor is None:
                raise GuiError(f"view {view_name!r} does not define a Movement screen factor")
            normalized_screen_factor = float(movement_screen_factor)
            if abs(normalized_screen_factor - float(expected_screen_factor)) > 1e-9:
                raise GuiError(
                    f"view {view_name!r} requires movement_screen_factor="
                    f"{float(expected_screen_factor):g}"
                )

        if movement_dialog_closed is not None:
            expected_dialog_closed = execution_recipe.get("movement_dialog_closed_after_restore")
            if expected_dialog_closed is None:
                raise GuiError(f"view {view_name!r} does not define Movement dialog close evidence")
            if bool(movement_dialog_closed) is not bool(expected_dialog_closed):
                raise GuiError(
                    f"view {view_name!r} requires movement_dialog_closed={bool(expected_dialog_closed)!r}"
                )

        keyboard_evidence_status = "not_applicable"
        staged_keyboard_recipe = isinstance(execution_recipe.get("keyboard_stages"), list)
        staged_keyboard_evidence_complete = False
        if staged_keyboard_recipe:
            staged_fields = (
                normalized_keyboard_stages is not None,
                reset_before_key_sequence is not None,
                normalized_restored_increment is not None,
                normalized_movement_command_id is not None,
                normalized_angle_control_id is not None,
                normalized_screen_control_id is not None,
                normalized_screen_factor is not None,
                movement_dialog_closed is not None,
            )
            staged_keyboard_evidence_complete = all(staged_fields)
            keyboard_evidence_status = (
                "complete_staged_recipe_and_restore_matched"
                if staged_keyboard_evidence_complete
                else "partial_staged_recipe_matched"
                if any(staged_fields)
                else "not_supplied_backward_compatible"
            )
        elif isinstance(execution_recipe.get("key_sequence"), list):
            supplied_fields = (
                normalized_key_sequence is not None,
                reset_before_key_sequence is not None,
                rotation_increment_degrees is not None,
                normalized_modifier_keys is not None,
            )
            keyboard_evidence_status = (
                "complete_and_recipe_matched"
                if all(supplied_fields)
                else "partial_recipe_matched"
                if any(supplied_fields)
                else "not_supplied_backward_compatible"
            )

        resolved_screenshot: str | None = None
        if screenshot_path is not None:
            screenshot = Path(screenshot_path).expanduser().resolve()
            _ensure_inside(self.workspace_root, screenshot)
            if not screenshot.exists() or not screenshot.is_file():
                raise GuiError(f"replay screenshot does not exist: {screenshot}")
            resolved_screenshot = str(screenshot)

        status = self.status(project_id=safe_project, revision=revision)
        target_resolution = (
            status.get("target_window_resolution")
            if isinstance(status.get("target_window_resolution"), dict)
            else {}
        )
        target_window = status.get("target_window") if isinstance(status.get("target_window"), dict) else {}
        identity_verified = target_resolution.get("matched_project_window") is True
        single_window_policy_ok = status.get("single_window_policy_ok") is True
        current_revision_loaded = status.get("current_revision_loaded") is True
        actual_window_handle = target_window.get("handle")
        actual_window_title = target_window.get("title")
        window_handle_matches = expected_window_handle is None or actual_window_handle == expected_window_handle
        window_title_matches = expected_window_title is None or actual_window_title == expected_window_title
        exact_window_binding_supplied = bool(
            expected_window_handle is not None and expected_window_title is not None
        )
        anonymous_recipe_targets = _verified_anonymous_recipe_targets(
            execution_recipe
        )
        accessibility_command_uses_required = bool(
            source == "computer_use" and anonymous_recipe_targets
        )
        expected_accessibility_command_ids = {
            str(target.get("command_id")) for target in anonymous_recipe_targets
        }
        observed_accessibility_command_ids = {
            str(item.get("command_id"))
            for item in normalized_accessibility_command_uses or []
        }
        accessibility_command_uses_complete = bool(
            anonymous_recipe_targets
            and normalized_accessibility_command_uses is not None
            and exact_window_binding_supplied
            and expected_accessibility_command_ids
            == observed_accessibility_command_ids
            and len(normalized_accessibility_command_uses)
            == len(anonymous_recipe_targets)
            and all(
                item.get("accessibility_tree_refreshed") is True
                and item.get("invocation_succeeded") is True
                for item in normalized_accessibility_command_uses
            )
        )
        staged_keyboard_evidence_required = bool(staged_keyboard_recipe and source == "computer_use")
        target_wrapper_metadata = (
            target_resolution.get("target_project_wrapper_metadata")
            if isinstance(target_resolution.get("target_project_wrapper_metadata"), dict)
            else {}
        )
        expected_structure_artifact_path: str | None = None
        raw_expected_structure_path = target_wrapper_metadata.get("source_path")
        if raw_expected_structure_path:
            try:
                expected_structure_artifact_path = str(
                    Path(str(raw_expected_structure_path)).expanduser().resolve()
                )
            except OSError:
                expected_structure_artifact_path = None
        structure_artifact_sha256_current: str | None = None
        if expected_structure_artifact_path is not None:
            structure_artifact = Path(expected_structure_artifact_path)
            if structure_artifact.exists() and structure_artifact.is_file():
                structure_artifact_sha256_current = hashlib.sha256(
                    structure_artifact.read_bytes()
                ).hexdigest()
        reviewed_copy_script_evidence_required = source == "reviewed_copy_script"
        copy_script_analysis = (
            normalized_copy_script_evidence.get("analysis")
            if isinstance(normalized_copy_script_evidence, dict)
            and isinstance(normalized_copy_script_evidence.get("analysis"), dict)
            else {}
        )
        reviewed_copy_script_evidence_complete = bool(
            normalized_copy_script_evidence is not None
            and normalized_copy_script_evidence.get("copy_script_command_observed")
            is True
            and normalized_copy_script_evidence.get("review_completed") is True
            and normalized_copy_script_evidence.get("view_action_matches_manifest")
            is True
            and normalized_copy_script_evidence.get("structure_unchanged_observed")
            is True
            and copy_script_analysis.get("safe_for_view_evidence") is True
            and exact_window_binding_supplied
            and resolved_screenshot is not None
            and structure_artifact_sha256_current is not None
        )
        crystal_camera_evidence_required = bool(crystal_standard_view_recipe)
        crystal_camera_screenshot_verified = resolved_screenshot is not None
        crystal_camera_evidence_complete = bool(
            normalized_crystal_camera_evidence is not None
            and normalized_crystal_camera_evidence.get("complete") is True
            and crystal_camera_screenshot_verified
        )
        miller_plane_evidence_required = bool(miller_plane_recipe)
        miller_plane_artifact_binding_matches = bool(
            normalized_miller_plane_evidence is not None
            and expected_structure_artifact_path is not None
            and normalized_miller_plane_evidence.get("structure_artifact_path")
            == expected_structure_artifact_path
        )
        miller_plane_native_command_matches = bool(
            native_command_id == execution_recipe.get("native_command_id")
        )
        miller_plane_unmodified_input_verified = normalized_modifier_keys == []
        miller_plane_screenshot_verified = resolved_screenshot is not None
        miller_plane_evidence_complete = bool(
            normalized_miller_plane_evidence is not None
            and normalized_miller_plane_evidence.get("complete") is True
            and miller_plane_artifact_binding_matches
            and miller_plane_native_command_matches
            and miller_plane_unmodified_input_verified
            and miller_plane_screenshot_verified
        )
        accepted = bool(
            model_visible
            and camera_matches_manifest
            and identity_verified
            and single_window_policy_ok
            and current_revision_loaded
            and window_handle_matches
            and window_title_matches
            and (
                not accessibility_command_uses_required
                or accessibility_command_uses_complete
            )
            and (
                not staged_keyboard_evidence_required
                or staged_keyboard_evidence_complete
            )
            and (
                not reviewed_copy_script_evidence_required
                or reviewed_copy_script_evidence_complete
            )
            and (
                not crystal_camera_evidence_required
                or crystal_camera_evidence_complete
            )
            and (
                not miller_plane_evidence_required
                or miller_plane_evidence_complete
            )
        )
        rejection_reasons: list[str] = []
        if not model_visible:
            rejection_reasons.append("model_not_visible")
        if not camera_matches_manifest:
            rejection_reasons.append("camera_does_not_match_manifest")
        if not identity_verified:
            rejection_reasons.append("target_revision_window_identity_unverified")
        if not single_window_policy_ok:
            rejection_reasons.extend(str(item) for item in status.get("single_window_violation_reasons") or [])
        if not current_revision_loaded:
            rejection_reasons.append("target_revision_not_loaded_in_gui")
        if not window_handle_matches:
            rejection_reasons.append("observed_window_handle_mismatch")
        if not window_title_matches:
            rejection_reasons.append("observed_window_title_mismatch")
        if (
            accessibility_command_uses_required
            and not accessibility_command_uses_complete
        ):
            rejection_reasons.append(
                "verified_anonymous_toolbar_command_use_evidence_incomplete"
            )
        if staged_keyboard_evidence_required and not staged_keyboard_evidence_complete:
            rejection_reasons.append("staged_keyboard_evidence_incomplete")
        if reviewed_copy_script_evidence_required:
            if normalized_copy_script_evidence is None:
                rejection_reasons.append("reviewed_copy_script_evidence_missing")
            else:
                if (
                    normalized_copy_script_evidence.get(
                        "copy_script_command_observed"
                    )
                    is not True
                ):
                    rejection_reasons.append(
                        "reviewed_copy_script_command_not_observed"
                    )
                if normalized_copy_script_evidence.get("review_completed") is not True:
                    rejection_reasons.append("reviewed_copy_script_review_incomplete")
                if (
                    normalized_copy_script_evidence.get(
                        "view_action_matches_manifest"
                    )
                    is not True
                ):
                    rejection_reasons.append(
                        "reviewed_copy_script_view_action_not_matched"
                    )
                if (
                    normalized_copy_script_evidence.get(
                        "structure_unchanged_observed"
                    )
                    is not True
                ):
                    rejection_reasons.append(
                        "reviewed_copy_script_structure_unchanged_not_observed"
                    )
                if copy_script_analysis.get("safe_for_view_evidence") is not True:
                    rejection_reasons.append("reviewed_copy_script_safety_blocked")
            if not exact_window_binding_supplied:
                rejection_reasons.append(
                    "reviewed_copy_script_exact_window_binding_missing"
                )
            if resolved_screenshot is None:
                rejection_reasons.append("reviewed_copy_script_screenshot_missing")
            if structure_artifact_sha256_current is None:
                rejection_reasons.append(
                    "reviewed_copy_script_structure_artifact_missing"
                )
        if crystal_camera_evidence_required:
            if normalized_crystal_camera_evidence is None:
                rejection_reasons.append("crystal_camera_evidence_missing")
            elif normalized_crystal_camera_evidence.get("complete") is not True:
                if (
                    normalized_crystal_camera_evidence.get(
                        "view_direction_matches_manifest"
                    )
                    is not True
                ):
                    rejection_reasons.append(
                        "crystal_view_direction_does_not_match_manifest"
                    )
                if (
                    normalized_crystal_camera_evidence.get(
                        "native_in_plane_roll_observed"
                    )
                    is not True
                ):
                    rejection_reasons.append(
                        "crystal_native_in_plane_roll_not_observed"
                    )
            if not crystal_camera_screenshot_verified:
                rejection_reasons.append("crystal_camera_screenshot_missing")
        if miller_plane_evidence_required:
            if normalized_miller_plane_evidence is None:
                rejection_reasons.append("miller_plane_evidence_missing")
            else:
                if normalized_miller_plane_evidence.get("counts_match_contract") is not True:
                    rejection_reasons.append("miller_plane_count_or_selection_contract_failed")
                if normalized_miller_plane_evidence.get("required_true_fields_match") is not True:
                    rejection_reasons.append("miller_plane_cleanup_or_camera_evidence_incomplete")
                if (
                    normalized_miller_plane_evidence.get("selection_evidence_matches_contract")
                    is not True
                ):
                    rejection_reasons.append("miller_plane_selection_evidence_incomplete")
                if (
                    direction_via_miller_plane_recipe
                    and normalized_miller_plane_evidence.get(
                        "direct_lattice_direction_matches_manifest"
                    )
                    is not True
                ):
                    rejection_reasons.append("crystal_direction_camera_evidence_incomplete")
                if (
                    normalized_miller_plane_evidence.get("structure_artifact_hash_unchanged")
                    is not True
                ):
                    rejection_reasons.append("miller_plane_structure_artifact_hash_changed")
                if (
                    normalized_miller_plane_evidence.get("structure_artifact_hash_matches_current")
                    is not True
                ):
                    rejection_reasons.append("miller_plane_structure_artifact_hash_not_current")
                if normalized_miller_plane_evidence.get("undo_labels_match_contract") is not True:
                    rejection_reasons.append("miller_plane_cleanup_undo_contract_failed")
            if not miller_plane_artifact_binding_matches:
                rejection_reasons.append("miller_plane_structure_artifact_not_bound_to_wrapper_source")
            if not miller_plane_native_command_matches:
                rejection_reasons.append("miller_plane_view_onto_command_evidence_missing")
            if not miller_plane_unmodified_input_verified:
                rejection_reasons.append("miller_plane_unmodified_input_evidence_missing")
            if not miller_plane_screenshot_verified:
                rejection_reasons.append("miller_plane_pre_cleanup_screenshot_missing")
        rejection_reasons = _unique_strings(rejection_reasons)

        window_binding = {
            "ok": bool(
                identity_verified
                and single_window_policy_ok
                and current_revision_loaded
                and window_handle_matches
                and window_title_matches
            ),
            "status": (
                "verified_current_wrapper_window"
                if identity_verified
                and single_window_policy_ok
                and current_revision_loaded
                and window_handle_matches
                and window_title_matches
                else "rejected_window_binding"
            ),
            "project_id": safe_project,
            "revision": revision,
            "expected_window_handle": expected_window_handle,
            "actual_window_handle": actual_window_handle,
            "window_handle_matches": window_handle_matches,
            "expected_window_title": expected_window_title,
            "actual_window_title": actual_window_title,
            "window_title_matches": window_title_matches,
            "matched_project_window": identity_verified,
            "current_revision_loaded": current_revision_loaded,
            "single_window_policy_ok": single_window_policy_ok,
        }

        recorded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        event_id = uuid.uuid4().hex
        copy_script_evidence_summary: dict[str, Any] | None = None
        if normalized_copy_script_evidence is not None:
            evidence_dir = (manifest_path.parent / "gui_copy_script_evidence").resolve()
            _ensure_inside(self.workspace_root, evidence_dir)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            script_path = (evidence_dir / f"{event_id}.copy-script.txt").resolve()
            metadata_path = (evidence_dir / f"{event_id}.json").resolve()
            _ensure_inside(self.workspace_root, script_path)
            _ensure_inside(self.workspace_root, metadata_path)
            raw_script_persisted = bool(
                copy_script_analysis.get("safe_for_view_evidence") is True
            )
            if raw_script_persisted:
                _write_text_atomic(
                    script_path,
                    str(normalized_copy_script_evidence["script_text"]),
                )
            copy_script_evidence_summary = {
                "schema_version": 1,
                "capture_method": normalized_copy_script_evidence.get(
                    "capture_method"
                ),
                "reviewer": normalized_copy_script_evidence.get("reviewer"),
                "copy_script_command_observed": normalized_copy_script_evidence.get(
                    "copy_script_command_observed"
                ),
                "review_completed": normalized_copy_script_evidence.get(
                    "review_completed"
                ),
                "view_action_matches_manifest": normalized_copy_script_evidence.get(
                    "view_action_matches_manifest"
                ),
                "structure_unchanged_observed": normalized_copy_script_evidence.get(
                    "structure_unchanged_observed"
                ),
                "note": normalized_copy_script_evidence.get("note"),
                "analysis": copy_script_analysis,
                "script_sha256": copy_script_analysis.get("script_sha256"),
                "script_path": str(script_path) if raw_script_persisted else None,
                "metadata_path": str(metadata_path),
                "raw_script_persisted": raw_script_persisted,
                "execution_allowed": False,
                "script_language": "materials_script_perl",
                "exact_window_binding_required": True,
                "screenshot_required": True,
                "complete": reviewed_copy_script_evidence_complete,
                "structure_artifact_path": expected_structure_artifact_path,
                "structure_artifact_sha256_current": (
                    structure_artifact_sha256_current
                ),
            }
            _write_json_atomic(
                metadata_path,
                {
                    "kind": "materials_studio_reviewed_copy_script_evidence",
                    "recorded_at": recorded_at,
                    "project_id": safe_project,
                    "revision": revision,
                    "view_name": view_name,
                    "event_id": event_id,
                    "accepted": accepted,
                    "window_binding": window_binding,
                    "screenshot_path": resolved_screenshot,
                    "evidence": copy_script_evidence_summary,
                },
            )
        event = {
            "event_id": event_id,
            "recorded_at": recorded_at,
            "project_id": safe_project,
            "revision": revision,
            "view_name": view_name,
            "source": source,
            "model_visible": bool(model_visible),
            "camera_matches_manifest": bool(camera_matches_manifest),
            "accepted": accepted,
            "rejection_reasons": rejection_reasons,
            "note": note,
            "screenshot_path": resolved_screenshot,
            "window": target_window or None,
            "window_binding": window_binding,
            "window_identity_verified": identity_verified,
            "single_window_policy_ok": single_window_policy_ok,
            "current_revision_loaded": current_revision_loaded,
            "native_command_id": native_command_id,
            "native_command": native_command,
            "accessibility_command_uses_required": (
                accessibility_command_uses_required
            ),
            "accessibility_command_uses_complete": (
                accessibility_command_uses_complete
            ),
            "accessibility_command_uses": (
                normalized_accessibility_command_uses
            ),
            "key_sequence": normalized_key_sequence,
            "reset_before_key_sequence": reset_before_key_sequence,
            "rotation_increment_degrees": rotation_increment_degrees,
            "modifier_keys": normalized_modifier_keys,
            "keyboard_stages": normalized_keyboard_stages,
            "rotation_increment_restored_degrees": normalized_restored_increment,
            "movement_options_command_id": normalized_movement_command_id,
            "movement_angle_control_id": normalized_angle_control_id,
            "movement_screen_factor_control_id": normalized_screen_control_id,
            "movement_screen_factor": normalized_screen_factor,
            "movement_dialog_closed": movement_dialog_closed,
            "staged_keyboard_evidence_required": staged_keyboard_evidence_required,
            "keyboard_evidence_status": keyboard_evidence_status,
            "reviewed_copy_script_evidence_required": (
                reviewed_copy_script_evidence_required
            ),
            "reviewed_copy_script_evidence_complete": (
                reviewed_copy_script_evidence_complete
            ),
            "reviewed_copy_script_evidence": copy_script_evidence_summary,
            "crystal_camera_evidence_required": crystal_camera_evidence_required,
            "crystal_camera_evidence_complete": crystal_camera_evidence_complete,
            "crystal_camera_screenshot_verified": (
                crystal_camera_screenshot_verified
            ),
            "crystal_camera_evidence": normalized_crystal_camera_evidence,
            "miller_plane_evidence_required": miller_plane_evidence_required,
            "direction_via_miller_plane_recipe": direction_via_miller_plane_recipe,
            "miller_plane_evidence_complete": miller_plane_evidence_complete,
            "miller_plane_artifact_binding_matches": miller_plane_artifact_binding_matches,
            "expected_structure_artifact_path": expected_structure_artifact_path,
            "miller_plane_evidence": normalized_miller_plane_evidence,
            "execution_recipe": execution_recipe or None,
            "execution_recipe_contract": matching_recipe_contract,
            "expected_camera": matching_view.get("camera"),
            "expected_projection": matching_view.get("verification"),
        }
        event["evidence_integrity"] = _record_view_replay_evidence_integrity(
            event,
            workspace_root=self.workspace_root,
        )
        if (
            event.get("accepted") is True
            and event["evidence_integrity"].get("strict") is True
            and event["evidence_integrity"].get("trusted_for_replay") is not True
        ):
            accepted = False
            if "evidence_integrity_verification_failed" not in rejection_reasons:
                rejection_reasons.append("evidence_integrity_verification_failed")
            event["accepted"] = False
            event["rejection_reasons"] = rejection_reasons
            event["evidence_integrity"]["trusted_for_replay"] = False
        event["event_record_schema_version"] = (
            VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION
        )
        event["event_record_sha256"] = _view_replay_event_record_sha256(event)
        events_path = manifest_path.with_name("gui_view_replay_events.jsonl")
        _append_view_replay_event_journal(
            events_path,
            event,
            workspace_root=self.workspace_root,
        )
        replay_events = [item for item in manifest.get("replay_events") or [] if isinstance(item, dict)]
        replay_events.append(event)
        manifest["replay_events"] = replay_events
        manifest["last_replay_event"] = event
        _refresh_view_replay_summary(
            manifest,
            workspace_root=self.workspace_root,
            events_path=events_path,
        )
        _write_json_atomic(manifest_path, manifest)

        log_path = self._write_log(
            "record_view_replay",
            project_id=safe_project,
            revision=revision,
            payload={
                "manifest_path": str(manifest_path),
                "events_path": str(events_path),
                "event": event,
                "replay_summary": manifest["replay_summary"],
                "event_journal": manifest.get("event_journal"),
            },
        )
        return {
            "project_id": safe_project,
            "revision": revision,
            "view_name": view_name,
            "accepted": accepted,
            "rejection_reasons": rejection_reasons,
            "manifest_path": str(manifest_path),
            "events_path": str(events_path),
            "gui_log_path": str(log_path),
            "event": event,
            "replay_status": manifest.get("replay_status"),
            "replay_summary": manifest["replay_summary"],
            "replay_continuation": manifest.get("replay_continuation"),
            "event_journal": manifest.get("event_journal"),
            "recipe_contract": manifest.get("recipe_contract"),
            "recipe_migration": manifest.get("recipe_migration"),
            "manifest_view_names": list(manifest.get("view_names") or []),
            "gui_status": status,
        }

    def _view_replay_manifest_path(self, *, project_id: str, revision: int) -> Path:
        """Return the project/revision-scoped view replay manifest path."""

        safe_project = sanitize_project_id(project_id)
        path = (
            self.workspace_root
            / safe_project
            / "outputs"
            / f"r{revision:03d}"
            / "gui_view_replay_manifest.json"
        ).resolve()
        _ensure_inside(self.workspace_root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _require_direct_action_target(
        self,
        *,
        window: WindowInfo,
        target_resolution: dict[str, Any],
        project_id: str | None,
        revision: int | None,
    ) -> dict[str, Any]:
        """Fail before direct GUI action unless the exact target is isolated."""

        management = self._window_management_for_hotload(
            target_window=window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        reasons = list(management.get("single_window_violation_reasons") or [])
        if project_id is not None or revision is not None:
            if target_resolution.get("matched_project_window") is not True:
                reasons.append("requested_project_revision_window_not_matched")
            if int(target_resolution.get("matching_window_count") or 0) != 1:
                reasons.append("requested_project_revision_window_not_unique")
            if int(management.get("matching_window_count") or 0) != 1:
                reasons.append("requested_project_revision_live_window_not_unique")
            if (
                management.get(
                    "target_window_matches_requested_project_revision"
                )
                is not True
            ):
                reasons.append(
                    "target_window_no_longer_matches_requested_project_revision"
                )
            if management.get("target_wrapper_integrity_verified") is not True:
                reasons.append("target_wrapper_integrity_unverified")
            if (
                management.get(
                    "target_window_wrapper_workspace_matches_controller"
                )
                is not True
            ):
                reasons.append("target_wrapper_workspace_mismatch")
        reasons = _unique_strings(reasons)
        if reasons:
            raise GuiError(
                "Refusing direct Materials Studio GUI action because the target "
                f"window is not uniquely verified: {', '.join(reasons)}"
            )
        return management

    def _require_hotload_action_target(
        self,
        *,
        window: WindowInfo,
        target_resolution: dict[str, Any],
        project_id: str | None,
        revision: int | None,
    ) -> dict[str, Any]:
        """Allow one authentic stale-source wrapper only as a reload target."""

        management = self._window_management_for_hotload(
            target_window=window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        reasons = list(
            management.get("single_window_violation_reasons") or []
        )
        if project_id is not None or revision is not None:
            if target_resolution.get("matched_project_window") is not True:
                reasons.append("requested_project_revision_window_not_matched")
            if int(target_resolution.get("matching_window_count") or 0) != 1:
                reasons.append("requested_project_revision_window_not_unique")
            if int(management.get("matching_window_count") or 0) != 1:
                reasons.append(
                    "requested_project_revision_live_window_not_unique"
                )
            if (
                management.get(
                    "target_window_matches_requested_project_revision"
                )
                is not True
            ):
                reasons.append(
                    "target_window_no_longer_matches_requested_project_revision"
                )
            if management.get("target_wrapper_identity_verified") is not True:
                reasons.append("target_wrapper_identity_unverified")
            if (
                management.get(
                    "target_window_wrapper_workspace_matches_controller"
                )
                is not True
            ):
                reasons.append("target_wrapper_workspace_mismatch")
        reasons = _unique_strings(reasons)
        if reasons:
            raise GuiError(
                "Refusing Materials Studio GUI hot-load because the target "
                f"window is not uniquely verified: {', '.join(reasons)}"
            )
        return management

    def _require_window(
        self,
        *,
        project_id: str | None = None,
        revision: int | None = None,
    ) -> tuple[WindowInfo, dict[str, Any]]:
        """获取必需的窗口。"""
        if not self.backend.supported:
            raise GuiError(self.backend.unavailable_reason or "GUI 后端不可用。")
        window, target_resolution = self._resolve_target_window(
            project_id=project_id,
            revision=revision,
        )
        if window is None:
            raise GuiError("未找到打开的 Materials Studio 窗口。请先启动 MatStudio.exe。")
        if int(target_resolution.get("matching_window_count") or 0) > 1:
            raise GuiError(
                "Refusing GUI input because more than one live Materials Studio "
                "window matches the requested project/revision."
            )
        return window, target_resolution

    def _resolve_target_window(
        self,
        *,
        project_id: str | None,
        revision: int | None,
    ) -> tuple[WindowInfo | None, dict[str, Any]]:
        """Prefer a live wrapper window that matches the requested project/revision."""

        processes = self.backend.list_processes()
        discovered_window = self.backend.find_window()
        candidates: list[WindowInfo] = []
        list_windows = getattr(self.backend, "list_windows", None)
        if callable(list_windows):
            try:
                candidates.extend(list_windows())
            except Exception:
                candidates = []
        if discovered_window is not None and not any(
            window.handle == discovered_window.handle for window in candidates
        ):
            candidates.insert(0, discovered_window)
        process_ids = {process.pid for process in processes}
        candidates = [
            window for window in candidates if window.pid in process_ids
        ]
        selected_window = _select_live_matstudio_window(
            processes=processes,
            windows=candidates,
            preferred=discovered_window,
        )

        requested_target = project_id is not None or revision is not None
        matching: list[tuple[WindowInfo, dict[str, Any]]] = []
        if requested_target:
            for candidate in candidates:
                metadata = self._project_wrapper_metadata_for_window(candidate)
                if metadata is None:
                    continue
                if project_id is not None and metadata.get("project_id") != project_id:
                    continue
                if revision is not None:
                    try:
                        metadata_revision = int(metadata.get("revision"))
                    except (TypeError, ValueError):
                        continue
                    if metadata_revision != int(revision):
                        continue
                matching.append((candidate, metadata))

        if matching:
            target_window, metadata = matching[0]
            return target_window, {
                "requested_project_id": project_id,
                "requested_revision": revision,
                "matched_project_window": True,
                "matching_window_count": len(matching),
                "target_handle": target_window.handle,
                "target_title": target_window.title,
                "target_project_wrapper_metadata": metadata,
                "target_wrapper_workspace_root": metadata.get("wrapper_workspace_root"),
                "target_wrapper_workspace_matches_controller": metadata.get(
                    "wrapper_workspace_matches_controller"
                ),
                "fallback_used": False,
            }

        fallback_window = selected_window or (candidates[0] if candidates else None)
        fallback_metadata = (
            self._project_wrapper_metadata_for_window(fallback_window)
            if fallback_window is not None
            else None
        )
        return fallback_window, {
            "requested_project_id": project_id,
            "requested_revision": revision,
            "matched_project_window": False,
            "matching_window_count": 0,
            "target_handle": fallback_window.handle if fallback_window else None,
            "target_title": fallback_window.title if fallback_window else None,
            "target_project_wrapper_metadata": fallback_metadata,
            "target_wrapper_workspace_root": (
                fallback_metadata.get("wrapper_workspace_root")
                if isinstance(fallback_metadata, dict)
                else None
            ),
            "target_wrapper_workspace_matches_controller": (
                fallback_metadata.get("wrapper_workspace_matches_controller")
                if isinstance(fallback_metadata, dict)
                else None
            ),
            "fallback_used": fallback_window is not None and requested_target,
        }

    def _resolve_hotload_target_window(
        self,
        *,
        project_id: str | None,
        revision: int | None,
    ) -> tuple[WindowInfo | None, dict[str, Any]]:
        """Resolve an exact revision or one unique trusted project predecessor."""

        exact_window, exact_resolution = self._resolve_target_window(
            project_id=project_id,
            revision=revision,
        )
        exact_resolution = {
            **exact_resolution,
            "hotload_requested_project_id": project_id,
            "hotload_requested_revision": revision,
            "hotload_target_mode": (
                "exact_revision"
                if exact_resolution.get("matched_project_window") is True
                else "global_single_instance_fallback"
            ),
            "hotload_target_project_id": project_id,
            "hotload_target_revision": revision,
        }
        if (
            project_id is None
            or exact_resolution.get("matched_project_window") is True
            or int(exact_resolution.get("matching_window_count") or 0) != 0
        ):
            return exact_window, exact_resolution

        project_window, project_resolution = self._resolve_target_window(
            project_id=project_id,
            revision=None,
        )
        metadata = (
            project_resolution.get("target_project_wrapper_metadata")
            if isinstance(
                project_resolution.get("target_project_wrapper_metadata"),
                dict,
            )
            else {}
        )
        if not (
            project_window is not None
            and project_resolution.get("matched_project_window") is True
            and int(project_resolution.get("matching_window_count") or 0) == 1
            and metadata.get("wrapper_target_identity_verified") is True
            and metadata.get("wrapper_workspace_matches_controller") is True
        ):
            return exact_window, exact_resolution
        try:
            target_revision = int(metadata.get("revision"))
        except (TypeError, ValueError):
            return exact_window, exact_resolution
        return project_window, {
            **project_resolution,
            "hotload_requested_project_id": project_id,
            "hotload_requested_revision": revision,
            "hotload_target_mode": "existing_project_revision",
            "hotload_target_project_id": project_id,
            "hotload_target_revision": target_revision,
            "hotload_target_revision_precedes_requested": bool(
                revision is not None and target_revision < revision
            ),
        }

    def _create_project_wrapper(
        self,
        structure_path: Path,
        *,
        project_id: str | None,
        revision: int | None,
    ) -> dict[str, Any]:
        """Create a minimal Materials Studio project around a generated structure."""

        project_label = _safe_component(project_id or structure_path.stem)
        revision_part = f"r{revision:03d}" if revision is not None else "rnew"
        unique_part = uuid.uuid4().hex[:10]
        project_name = _safe_component(f"msmcp_{revision_part}_{unique_part}")[:32]
        project_dir = self.workspace_root / "gui_projects" / project_name
        documents_dir = project_dir / f"{project_name}_Files" / "Documents"
        modules_dir = project_dir / f"{project_name}_Files" / "Modules"
        documents_dir.mkdir(parents=True, exist_ok=True)
        modules_dir.mkdir(parents=True, exist_ok=True)

        document_name = _safe_component(f"model_{revision_part}_{unique_part}") + structure_path.suffix.lower()
        document_path = documents_dir / document_name
        shutil.copy2(structure_path, document_path)

        project_path = project_dir / f"{project_name}.stp"
        metadata_path = project_dir / "metadata.json"
        identity_path = project_dir / "wrapper_identity.json"
        metadata = {
            "project_id": project_id,
            "project_label": project_label,
            "revision": revision,
            "source_path": str(structure_path),
            "source_name": structure_path.name,
            "project_name": project_name,
            "document_name": document_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "path_policy": "short_wrapper_names_to_avoid_windows_max_path_limits",
        }
        document_id = str(uuid.uuid4()).upper()
        reference_id = str(uuid.uuid4()).upper()
        project_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Project>
  <Version>20.1</Version>
  <Reference>{{{reference_id}}}</Reference>
  <DocumentManager>
    <Document>
      <ID>{{{document_id}}}</ID>
      <Paths>1</Paths>
      <URL>.\\{xml_escape(document_name)}</URL>
    </Document>
  </DocumentManager>
  <ViewRegistry>
    <Frame>
      <Active>true</Active>
      <Left>0</Left>
      <Top>0</Top>
      <Width>1200</Width>
      <Height>700</Height>
      <Maximized>false</Maximized>
      <Minimized>false</Minimized>
      <View>
        <Active>true</Active>
        <DocumentID>{{{document_id}}}</DocumentID>
        <Type>SVViewer3D.Viewer3DControl</Type>
      </View>
    </Frame>
  </ViewRegistry>
  <DocumentEventManager>
  </DocumentEventManager>
</Project>
"""
        project_path.write_text(project_xml, encoding="utf-8")
        project_sha256, project_size = _sha256_file(project_path)
        document_sha256, document_size = _sha256_file(document_path)
        source_sha256, source_size = _sha256_file(structure_path)
        identity = {
            "identity_schema_version": 1,
            "identity_profile": "materials_studio_revision_wrapper_identity_v1",
            "project_name": project_name,
            "project_id": project_id,
            "revision": revision,
            "source_path": str(structure_path),
            "source_sha256": source_sha256,
            "source_size_bytes": source_size,
            "document_name": document_name,
            "document_sha256": document_sha256,
            "document_size_bytes": document_size,
            "project_file_sha256": project_sha256,
            "project_file_size_bytes": project_size,
        }
        identity_path.write_text(
            json.dumps(identity, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        identity_sha256, identity_size = _sha256_file(identity_path)
        metadata.update(
            {
                "wrapper_schema_version": 3,
                "wrapper_profile": "materials_studio_20_1_project_wrapper_v2",
                "project_file_sha256": project_sha256,
                "project_file_size_bytes": project_size,
                "document_sha256": document_sha256,
                "document_size_bytes": document_size,
                "source_sha256": source_sha256,
                "source_size_bytes": source_size,
                "identity_manifest_name": identity_path.name,
                "identity_manifest_sha256": identity_sha256,
                "identity_manifest_size_bytes": identity_size,
            }
        )
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "project_name": project_name,
            "project_path": str(project_path),
            "document_path": str(document_path),
            "metadata_path": str(metadata_path),
            "identity_manifest_path": str(identity_path),
            "source_path": str(structure_path),
            "open_note": "Generated .stp wrapper so Materials Studio opens with an active project.",
        }

    def _wait_for_window_after_open(
        self,
        *,
        previous_window: WindowInfo | None,
        timeout_seconds: float,
        target_pid: int | None = None,
        expected_project_name: str | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> WindowInfo | None:
        """Wait briefly for Materials Studio to finish opening a structure file."""

        deadline = time.monotonic() + timeout_seconds
        latest = previous_window
        expected_project_title = (
            f"{expected_project_name} - Materials Studio"
            if expected_project_name
            else None
        )
        while time.monotonic() < deadline:
            time.sleep(poll_interval_seconds)
            candidates: list[WindowInfo] = []
            list_windows = getattr(self.backend, "list_windows", None)
            if expected_project_title and callable(list_windows):
                try:
                    if isinstance(self.backend, WindowsGuiBackend) and target_pid is not None:
                        candidates.extend(list_windows(pid=target_pid))
                    else:
                        candidates.extend(list_windows())
                except Exception:
                    candidates = []
            if isinstance(self.backend, WindowsGuiBackend) and target_pid is not None:
                candidate = self.backend.find_window(pid=target_pid)
            else:
                candidate = self.backend.find_window()
            if candidate is not None and not any(item.handle == candidate.handle for item in candidates):
                candidates.append(candidate)
            for candidate in candidates:
                latest = candidate
                title = candidate.title.lower()
                if "file associations" in title:
                    continue
                if expected_project_title and candidate.title == expected_project_title:
                    return candidate
                if "materials studio" in title:
                    if not expected_project_title:
                        return candidate
        return None if expected_project_title else latest

    def _screenshot_path(self, *, label: str, project_id: str | None, revision: int | None) -> Path:
        """获取截图路径。"""
        safe_label = _safe_name(label, fallback="snapshot")
        parts = [self.workspace_root, Path("screenshots")]
        if project_id:
            parts.append(Path(sanitize_project_id(project_id)))
        if revision is not None:
            parts.append(Path(f"r{revision:03d}"))
        base = Path(*parts).resolve()
        _ensure_inside(self.workspace_root, base)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = (base / f"{stamp}_{safe_label}.bmp").resolve()
        _ensure_inside(self.workspace_root, path)
        return path

    def _write_log(
        self,
        action: str,
        *,
        project_id: str | None,
        revision: int | None,
        payload: dict[str, Any],
    ) -> Path:
        """写入日志。"""
        if project_id:
            safe_project = sanitize_project_id(project_id)
            log_dir = (self.workspace_root / safe_project).resolve()
        else:
            log_dir = self.workspace_root.resolve()
        _ensure_inside(self.workspace_root, log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "gui_actions.jsonl"
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "project_id": project_id,
            "revision": revision,
            "payload": payload,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return log_path


def _view_replay_step(view: Any, *, index: int) -> dict[str, Any]:
    """Return the stable replay subset of one model-view audit row."""

    if not isinstance(view, dict):
        return {
            "index": index,
            "view_name": f"invalid_view_{index}",
            "supported": False,
            "warning": "View audit row is not a JSON object.",
        }
    supported = view.get("supported") is True
    framing = view.get("framing") if isinstance(view.get("framing"), dict) else {}
    camera = {
        "coordinate_system": view.get("coordinate_system"),
        "camera_direction": view.get("camera_direction"),
        "camera_up": view.get("camera_up"),
        "camera_right": view.get("camera_right"),
        "look_at_direction": view.get("look_at_direction"),
        "camera_position": view.get("camera_position"),
        "camera_distance_angstrom": view.get("camera_distance_angstrom"),
        "target": view.get("target"),
        "orthographic_width_angstrom": framing.get("orthographic_width_angstrom"),
        "orthographic_height_angstrom": framing.get("orthographic_height_angstrom"),
        "near_clip_angstrom": framing.get("near_clip_angstrom"),
        "far_clip_angstrom": framing.get("far_clip_angstrom"),
        "projection_units": framing.get("projection_units"),
    }
    crystallography = {
        key: view.get(key)
        for key in (
            "crystal_direction_indices",
            "crystal_direction_label",
            "crystal_direction_cartesian",
            "crystal_direction_view_onto_plane_mapping",
            "crystal_plane_indices",
            "crystal_plane_label",
            "crystal_plane_normal_cartesian",
            "crystal_plane_reciprocal_vector_per_angstrom",
            "crystal_plane_reciprocal_convention",
            "crystal_plane_spacing_angstrom",
            "oriented_frame_kind",
            "oriented_frame_role",
            "oriented_frame_axis",
            "oriented_frame_source_metadata_field",
            "oriented_frame_reference_cell_axis",
            "oriented_frame_axis_cartesian",
            "oriented_frame_direction_cartesian",
            "oriented_frame_in_plane_1_cartesian",
            "oriented_frame_in_plane_2_cartesian",
        )
        if view.get(key) is not None
    }
    return {
        "index": index,
        "view_name": str(view.get("name") or f"view_{index}"),
        "supported": supported,
        "warning": view.get("warning"),
        "camera": camera if supported else None,
        "crystallography": crystallography or None,
        "verification": {
            "atom_projection_count": view.get("atom_projection_count"),
            "projection_bbox_angstrom": view.get("projection_bbox_angstrom"),
            "projection_span_angstrom": view.get("projection_span_angstrom"),
            "overlap_candidate_count": len(view.get("overlap_candidates") or []),
            "view_health": view.get("health"),
        }
        if supported
        else None,
        "replay_backend": "computer_use_or_reviewed_copy_script",
        "replay_action": "orient_existing_viewer_only",
        "structure_mutation": False,
    }


def _local_uia_recipe_support(
    execution_recipe: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return whether one prepared recipe fits the narrow local UIA executor."""

    reasons: list[str] = []
    view_name = str(execution_recipe.get("view_name") or "")
    recipe_kind = str(execution_recipe.get("recipe_kind") or "")
    miller_recipe = recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS
    if not miller_recipe and view_name not in SAFE_LOCAL_VIEW_NAMES:
        reasons.append("local_uia_view_name_not_allowlisted")
    if execution_recipe.get("automation_ready") is not True:
        reasons.append("prepared_recipe_not_automation_ready")
    if execution_recipe.get("structure_mutation_allowed") is not False:
        reasons.append("prepared_recipe_structure_mutation_not_prohibited")
    if execution_recipe.get("launch_new_matstudio_process_allowed") is not False:
        reasons.append("prepared_recipe_process_launch_not_prohibited")
    if execution_recipe.get("blind_coordinate_action_allowed") is not False:
        reasons.append("prepared_recipe_blind_coordinates_not_prohibited")
    if miller_recipe:
        if execution_recipe.get("native_command_id") != "cmdViewer3DViewOnto":
            reasons.append("prepared_miller_view_onto_command_mismatch")
        if execution_recipe.get("selection_method") != (
            MILLER_PLANE_VIEWPORT_SELECTION_METHOD
        ):
            reasons.append("prepared_miller_viewport_selection_contract_missing")
        if execution_recipe.get("pre_action_view_baseline_required") is not True:
            reasons.append("prepared_miller_pre_action_baseline_missing")
        if execution_recipe.get("reset_view_allowed") is not False:
            reasons.append("prepared_miller_reset_view_not_forbidden")
        if execution_recipe.get("accessibility_target") is not None:
            reasons.append("prepared_miller_reset_target_must_be_absent")
        if list(execution_recipe.get("modifier_keys") or []) != []:
            reasons.append("prepared_miller_modifier_keys_not_empty")
        transient = execution_recipe.get("transient_change_contract")
        if not isinstance(transient, dict) or transient.get(
            "required_undo_labels"
        ) != ["Undo View Onto Miller Plane", "Undo Create Miller Plane"]:
            reasons.append("prepared_miller_two_step_cleanup_contract_missing")
        invocation = execution_recipe.get("view_command_invocation")
        expected_numeric_mapping = {
            "selection_numeric_command_id": 33288,
            "recenter_numeric_command_id": 33296,
            "view_onto_numeric_command_id": 33297,
            "fit_numeric_command_id": 33299,
            "recenter_button_style": 10,
            "installed_registry_order_verified": True,
        }
        if not isinstance(invocation, dict) or any(
            invocation.get(field) != expected
            for field, expected in expected_numeric_mapping.items()
        ):
            reasons.append("prepared_miller_native_command_mapping_missing")
        runtime_ui = execution_recipe.get("runtime_ui_preflight")
        if not isinstance(runtime_ui, dict) or runtime_ui.get(
            "automation_gate_satisfied"
        ) is not True:
            reasons.append("prepared_miller_runtime_transaction_gate_missing")
        reasons = _unique_strings(reasons)
        return not reasons, reasons
    if execution_recipe.get("native_command_id") != "cmdViewer3DResetView":
        reasons.append("prepared_recipe_reset_command_mismatch")
    target = execution_recipe.get("accessibility_target")
    if not isinstance(target, dict):
        reasons.append("prepared_recipe_reset_accessibility_target_missing")
    else:
        if target.get("target_kind") not in {
            "named_control",
            "verified_anonymous_toolbar_child",
        }:
            reasons.append("prepared_recipe_reset_target_kind_unsupported")
        if target.get("command_id") != "cmdViewer3DResetView":
            reasons.append("prepared_recipe_reset_target_command_mismatch")

    if view_name == "isometric":
        stages = execution_recipe.get("keyboard_stages")
        if not isinstance(stages, list) or len(stages) != len(
            SAFE_ISOMETRIC_KEYBOARD_STAGES
        ):
            reasons.append("prepared_isometric_stages_not_allowlisted")
        else:
            for observed, expected in zip(
                stages,
                SAFE_ISOMETRIC_KEYBOARD_STAGES,
            ):
                if not isinstance(observed, dict):
                    reasons.append("prepared_isometric_stage_not_object")
                    continue
                try:
                    angle_matches = abs(
                        float(observed.get("rotation_increment_degrees"))
                        - float(expected["rotation_increment_degrees"])
                    ) <= 1e-9
                    display_matches = abs(
                        float(
                            observed.get(
                                "rotation_increment_ui_display_degrees",
                                observed.get("rotation_increment_degrees"),
                            )
                        )
                        - float(
                            expected["rotation_increment_ui_display_degrees"]
                        )
                    ) <= 0.0005
                except (TypeError, ValueError):
                    angle_matches = False
                    display_matches = False
                if not angle_matches or not display_matches:
                    reasons.append("prepared_isometric_stage_angle_not_allowlisted")
                if list(observed.get("key_sequence") or []) != list(
                    expected["key_sequence"]
                ):
                    reasons.append("prepared_isometric_stage_keys_not_allowlisted")
                if list(observed.get("modifier_keys") or []) != []:
                    reasons.append("prepared_isometric_stage_modifiers_not_empty")
        if execution_recipe.get("restore_rotation_increment_degrees") != 45.0:
            reasons.append("prepared_isometric_restore_angle_mismatch")
        if (
            execution_recipe.get("movement_options_command_id")
            != "cmdViewer3DMovementOptions"
            or execution_recipe.get("movement_angle_control_id")
            != "numNudgeAngle"
            or execution_recipe.get("movement_screen_factor_control_id")
            != "numNudgeFactor"
            or execution_recipe.get("movement_screen_factor_expected") != 2.0
            or execution_recipe.get("movement_dialog_closed_after_restore")
            is not True
        ):
            reasons.append("prepared_isometric_movement_contract_mismatch")
        movement_target = execution_recipe.get("movement_accessibility_target")
        if not isinstance(movement_target, dict):
            reasons.append("prepared_isometric_movement_target_missing")
        else:
            if movement_target.get("target_kind") not in {
                "named_control",
                "verified_anonymous_toolbar_child",
            }:
                reasons.append("prepared_isometric_movement_target_unsupported")
            if (
                movement_target.get("command_id")
                != "cmdViewer3DMovementOptions"
            ):
                reasons.append("prepared_isometric_movement_command_mismatch")
    else:
        expected_keys = SAFE_STANDARD_VIEW_KEY_SEQUENCES.get(view_name, [])
        if execution_recipe.get("keyboard_stages") is not None:
            reasons.append("local_uia_staged_keyboard_recipe_unsupported")
        if list(execution_recipe.get("key_sequence") or []) != expected_keys:
            reasons.append("prepared_recipe_key_sequence_not_allowlisted")
        if list(execution_recipe.get("modifier_keys") or []) != []:
            reasons.append("prepared_recipe_modifier_keys_not_empty")
        if expected_keys and execution_recipe.get("rotation_increment_degrees") != 45:
            reasons.append("prepared_recipe_rotation_increment_not_45_degrees")
    reasons = _unique_strings(reasons)
    return not reasons, reasons


def _local_view_replay_status_block_reasons(
    status: dict[str, Any],
) -> list[str]:
    """Return exact single-window/foreground blockers immediately around input."""

    reasons: list[str] = []
    target_window = (
        status.get("target_window")
        if isinstance(status.get("target_window"), dict)
        else {}
    )
    target_resolution = (
        status.get("target_window_resolution")
        if isinstance(status.get("target_window_resolution"), dict)
        else {}
    )
    if status.get("supported") is not True:
        reasons.append("gui_backend_unavailable")
    if status.get("target_window_pid_is_matstudio_process") is not True:
        reasons.append("target_window_pid_not_matstudio_process")
    if status.get("single_window_policy_ok") is not True:
        reasons.extend(
            str(item) for item in status.get("single_window_violation_reasons") or []
        )
    if status.get("target_window_found") is not True:
        reasons.append("target_revision_window_not_found")
    if target_resolution.get("matched_project_window") is not True:
        reasons.append("target_revision_window_identity_unverified")
    if status.get("current_revision_loaded") is not True:
        reasons.append("target_revision_not_loaded_in_gui")
    if target_window.get("is_visible") is not True:
        reasons.append("target_window_not_visible")
    if target_window.get("is_minimized") is True:
        reasons.append("target_window_minimized")
    if target_window.get("is_foreground") is not True:
        reasons.append("target_window_not_foreground")
    structure_path = _target_structure_path(target_resolution)
    if structure_path is None or not structure_path.exists() or not structure_path.is_file():
        reasons.append("target_structure_artifact_unavailable")
    return _unique_strings(reasons)


def _target_structure_path(
    target_resolution: dict[str, Any],
) -> Path | None:
    """Return the generated source structure bound to a wrapper window."""

    metadata = (
        target_resolution.get("target_project_wrapper_metadata")
        if isinstance(
            target_resolution.get("target_project_wrapper_metadata"), dict
        )
        else {}
    )
    raw_path = metadata.get("source_path")
    if not raw_path:
        return None
    try:
        return Path(str(raw_path)).expanduser().resolve()
    except Exception:
        return None


def _local_view_replay_record_template(
    *,
    project_id: str,
    revision: int,
    view_name: str,
    execution_recipe: dict[str, Any],
    action_receipt: dict[str, Any],
    target_window: dict[str, Any],
    screenshot_path: str | None,
) -> dict[str, Any]:
    """Build a deliberately incomplete post-action visual record template."""

    key_sequence = list(execution_recipe.get("key_sequence") or [])
    accessibility_command_uses: list[dict[str, Any]] = []
    for target in _verified_anonymous_recipe_targets(execution_recipe):
        command_id = str(target.get("command_id") or "")
        command_receipt = (
            action_receipt.get("movement_command")
            if command_id == "cmdViewer3DMovementOptions"
            else action_receipt.get("reset_command")
        )
        command_receipt = (
            command_receipt if isinstance(command_receipt, dict) else {}
        )
        accessibility_command_uses.append(
            {
                "command_id": target.get("command_id"),
                "toolbar_name": target.get("toolbar_name"),
                "toolbar_automation_id": target.get("toolbar_automation_id"),
                "registry_toolbar_name": target.get("registry_toolbar_name"),
                "zero_based_child_index": target.get("zero_based_child_index"),
                "element_index": target.get("element_index"),
                "registry_sha256": target.get("registry_sha256"),
                "semantic_mapping_sha256": target.get(
                    "semantic_mapping_sha256"
                ),
                "accessibility_tree_refreshed": bool(
                    command_receipt.get("accessibility_tree_refreshed") is True
                ),
                "invocation_succeeded": bool(
                    command_receipt.get("invocation_succeeded") is True
                    or (
                        command_id == "cmdViewer3DResetView"
                        and action_receipt.get("reset_invocation_succeeded") is True
                    )
                ),
            }
        )
    if not accessibility_command_uses:
        accessibility_command_uses = None
    staged_keyboard_receipts = action_receipt.get("keyboard_stages")
    keyboard_stages = (
        [
            {
                "rotation_increment_degrees": stage.get(
                    "rotation_increment_degrees"
                ),
                "key_sequence": list(stage.get("key_sequence") or []),
                "modifier_keys": list(stage.get("modifier_keys") or []),
            }
            for stage in staged_keyboard_receipts
            if isinstance(stage, dict)
        ]
        if isinstance(staged_keyboard_receipts, list)
        and staged_keyboard_receipts
        else None
    )
    crystal_camera_evidence = None
    required_record_evidence = execution_recipe.get("required_record_evidence")
    if (
        isinstance(required_record_evidence, dict)
        and required_record_evidence.get("field") == "crystal_camera_evidence"
    ):
        crystal_camera_evidence = {
            "camera_match_scope": CRYSTAL_STANDARD_VIEW_CAMERA_MATCH_SCOPE,
            "view_direction_matches_manifest": None,
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_observed": None,
        }
    miller_plane_evidence = (
        dict(action_receipt["miller_plane_evidence"])
        if isinstance(action_receipt.get("miller_plane_evidence"), dict)
        else None
    )
    return {
        "project_id": project_id,
        "revision": revision,
        "view_name": view_name,
        "source": "local_gui_fallback",
        "model_visible": None,
        "camera_matches_manifest": None,
        "screenshot_path": screenshot_path,
        "expected_window_handle": target_window.get("handle"),
        "expected_window_title": target_window.get("title"),
        "native_command_id": execution_recipe.get("native_command_id"),
        "accessibility_command_uses": accessibility_command_uses,
        "key_sequence": key_sequence or None if keyboard_stages is None else None,
        "reset_before_key_sequence": (
            action_receipt.get("reset_invocation_succeeded")
            if key_sequence or keyboard_stages
            else None
        ),
        "rotation_increment_degrees": (
            execution_recipe.get("rotation_increment_degrees")
            if key_sequence and keyboard_stages is None
            else None
        ),
        "modifier_keys": (
            []
            if miller_plane_evidence is not None
            or (key_sequence and keyboard_stages is None)
            else None
        ),
        "keyboard_stages": keyboard_stages,
        "rotation_increment_restored_degrees": action_receipt.get(
            "rotation_increment_restored_degrees"
        ),
        "movement_options_command_id": action_receipt.get(
            "movement_options_command_id"
        ),
        "movement_angle_control_id": action_receipt.get(
            "movement_angle_control_id"
        ),
        "movement_screen_factor_control_id": action_receipt.get(
            "movement_screen_factor_control_id"
        ),
        "movement_screen_factor": action_receipt.get(
            "movement_screen_factor"
        ),
        "movement_dialog_closed": action_receipt.get(
            "movement_dialog_closed"
        ),
        "crystal_camera_evidence": crystal_camera_evidence,
        "miller_plane_evidence": miller_plane_evidence,
    }


def _view_replay_runtime_ui_binding(
    status: dict[str, Any],
    *,
    project_id: str,
    revision: int,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Bind a runtime UI observation to the exact currently loaded wrapper window."""

    target_resolution = (
        status.get("target_window_resolution")
        if isinstance(status.get("target_window_resolution"), dict)
        else {}
    )
    target_window = status.get("target_window") if isinstance(status.get("target_window"), dict) else {}
    window_management = (
        status.get("window_management")
        if isinstance(status.get("window_management"), dict)
        else {}
    )
    wrapper_metadata = (
        target_resolution.get("target_project_wrapper_metadata")
        if isinstance(target_resolution.get("target_project_wrapper_metadata"), dict)
        else {}
    )
    raw_structure_artifact_path = wrapper_metadata.get("source_path")
    try:
        target_structure_artifact_path = (
            str(Path(str(raw_structure_artifact_path)).expanduser().resolve())
            if raw_structure_artifact_path
            else None
        )
    except OSError:
        target_structure_artifact_path = None
    actual_handle = target_window.get("handle") or window_management.get("target_window_handle")
    actual_title = target_window.get("title") or window_management.get("target_window_title")
    reasons: list[str] = []
    if status.get("supported") is not True:
        reasons.append("gui_backend_unavailable")
    if status.get("target_window_pid_is_matstudio_process") is not True:
        reasons.append("target_window_pid_not_matstudio_process")
    if status.get("single_window_policy_ok") is not True:
        reasons.append("single_window_policy_not_verified")
    if status.get("target_window_found") is not True:
        reasons.append("target_revision_window_not_found")
    if target_resolution.get("matched_project_window") is not True:
        reasons.append("target_revision_window_identity_unverified")
    if status.get("current_revision_loaded") is not True:
        reasons.append("target_revision_not_loaded_in_gui")
    if status.get("target_window_is_minimized") is True:
        reasons.append("target_window_minimized")
    if status.get("target_window_is_visible") is False:
        reasons.append("target_window_not_visible")
    if (
        status.get("target_window_foreground_observed") is True
        and status.get("target_window_is_foreground") is False
    ):
        reasons.append("target_window_not_foreground")
    if status.get("needs_dialog_resolution") is True:
        reasons.append("modal_dialog_blocks_runtime_ui_preflight")
    if wrapper_metadata.get("project_id") != project_id:
        reasons.append("target_window_project_mismatch")
    try:
        wrapper_revision = int(wrapper_metadata.get("revision"))
    except (TypeError, ValueError):
        wrapper_revision = None
    if wrapper_revision != revision:
        reasons.append("target_window_revision_mismatch")
    if evidence.get("expected_revision") != revision:
        reasons.append("observed_revision_mismatch")
    if evidence.get("expected_window_handle") != actual_handle:
        reasons.append("observed_window_handle_mismatch")
    if evidence.get("expected_window_title") != actual_title:
        reasons.append("observed_window_title_mismatch")
    reasons = _unique_strings(reasons)
    return {
        "ok": not reasons,
        "status": "verified_current_wrapper_window" if not reasons else "rejected_window_binding",
        "project_id": project_id,
        "revision": revision,
        "expected_window_handle": evidence.get("expected_window_handle"),
        "actual_window_handle": actual_handle,
        "expected_window_title": evidence.get("expected_window_title"),
        "actual_window_title": actual_title,
        "matched_project_window": target_resolution.get("matched_project_window"),
        "target_window_project_id": wrapper_metadata.get("project_id"),
        "target_window_revision": wrapper_revision,
        "target_structure_artifact_path": target_structure_artifact_path,
        "current_revision_loaded": status.get("current_revision_loaded"),
        "target_window_is_visible": status.get("target_window_is_visible"),
        "target_window_is_minimized": status.get("target_window_is_minimized"),
        "target_window_foreground_observed": status.get(
            "target_window_foreground_observed"
        ),
        "target_window_is_foreground": status.get("target_window_is_foreground"),
        "needs_dialog_resolution": status.get("needs_dialog_resolution"),
        "single_window_policy_ok": status.get("single_window_policy_ok"),
        "process_count": status.get("process_count"),
        "window_count": status.get("window_count"),
        "rejection_reasons": reasons,
    }


def _miller_runtime_ui_selection_profile(evidence: dict[str, Any]) -> str | None:
    """Return the exact runtime selection path proven by the observation."""

    if evidence.get("tree_explorer_menu_observed") is True:
        return "object_tree_exact_item"
    viewport_probe = evidence.get("viewport_selection_probe")
    if isinstance(viewport_probe, dict) and viewport_probe.get("complete") is True:
        return "viewport_unique_plane_properties_verified"
    return None


def _miller_runtime_ui_gate_block_reasons(
    evidence: dict[str, Any],
    binding: dict[str, Any],
) -> list[str]:
    """Return stable blockers for the runtime Miller-plane UI automation gate."""

    reasons: list[str] = []
    for reason in binding.get("rejection_reasons") or []:
        reasons.append(f"runtime_ui_binding_{reason}")
    for field in MILLER_RUNTIME_UI_REQUIRED_TRUE_FIELDS:
        if evidence.get(field) is not True:
            reasons.append(MILLER_RUNTIME_UI_BLOCK_REASON_BY_FIELD[field])
    selection_profile = _miller_runtime_ui_selection_profile(evidence)
    if selection_profile is None:
        if evidence.get("tree_explorer_menu_observed") is not True:
            reasons.append("runtime_tree_explorer_menu_not_observed")
        viewport_probe = evidence.get("viewport_selection_probe")
        if not isinstance(viewport_probe, dict):
            reasons.append("runtime_viewport_selection_probe_missing")
        else:
            reasons.extend(str(item) for item in viewport_probe.get("block_reasons") or [])
    if selection_profile == "viewport_unique_plane_properties_verified":
        viewport_probe = evidence.get("viewport_selection_probe")
        expected_artifact_path = binding.get("target_structure_artifact_path")
        if not expected_artifact_path:
            reasons.append("runtime_target_structure_artifact_path_unavailable")
        elif not isinstance(viewport_probe, dict) or (
            viewport_probe.get("structure_artifact_path") != expected_artifact_path
        ):
            reasons.append("runtime_viewport_structure_artifact_path_mismatch")
    if evidence.get("miller_planes_menu_key_sequence") != MILLER_RUNTIME_UI_REQUIRED_KEY_SEQUENCE:
        reasons.append("runtime_miller_planes_keyboard_menu_sequence_mismatch")
    for field, expected in MILLER_RUNTIME_UI_EXPECTED_IDENTIFIERS.items():
        if evidence.get(field) != expected:
            reasons.append(f"runtime_{field}_mismatch")
    if evidence.get("selection_modifier_keys") != []:
        reasons.append("runtime_selection_modifier_keys_not_empty")
    if (
        evidence.get("unexpected_plane_created_during_probe") is True
        and evidence.get("unexpected_plane_cleanup_verified") is not True
    ):
        reasons.append("runtime_unexpected_plane_cleanup_not_verified")
    return _unique_strings(reasons)


def _view_runtime_accessibility_gate(
    preflight: dict[str, Any] | None,
    *,
    required_command_ids: list[str],
    require_viewer_document: bool,
    require_empty_viewport_focus_target: bool,
) -> dict[str, Any]:
    """Resolve recipe-specific named-control readiness from bound live evidence."""

    runtime = preflight if isinstance(preflight, dict) else {}
    reasons = [str(item) for item in runtime.get("block_reasons") or [] if str(item)]
    if not runtime or runtime.get("observation_available") is not True:
        reasons.append("runtime_view_accessibility_preflight_missing")
    if runtime.get("binding_verified") is not True:
        reasons.append("runtime_view_accessibility_binding_not_verified")
    evidence = runtime.get("evidence") if isinstance(runtime.get("evidence"), dict) else {}
    if evidence.get("accessibility_tree_refreshed") is not True:
        reasons.append("runtime_accessibility_tree_not_refreshed")
    if require_viewer_document and evidence.get("viewer_document_observed") is not True:
        reasons.append("runtime_viewer_document_not_observed")
    semantic_viewport_focus_ready = bool(
        evidence.get("source") == "local_uia"
        and evidence.get("semantic_viewport_focus_supported") is True
    )
    if (
        require_empty_viewport_focus_target
        and evidence.get("empty_viewport_focus_target_observed") is not True
        and not semantic_viewport_focus_ready
    ):
        reasons.append("runtime_empty_viewport_focus_target_not_observed")

    controls_by_id = {
        str(item.get("command_id")): item
        for item in evidence.get("controls") or []
        if isinstance(item, dict) and item.get("command_id")
    }
    semantic_mappings_by_id = {
        str(item.get("command_id")): item
        for item in runtime.get("semantic_command_mappings") or []
        if isinstance(item, dict) and item.get("command_id")
    }
    command_gates: list[dict[str, Any]] = []
    missing_required_command_ids: list[str] = []
    reason_prefixes = {
        "cmdViewer3DResetView": "reset_view",
        "cmdViewer3DMovementOptions": "movement",
    }
    for command_id in required_command_ids:
        control = controls_by_id.get(command_id)
        semantic_mapping = semantic_mappings_by_id.get(command_id)
        reason_prefix = reason_prefixes.get(command_id, command_id)
        named_control_observed = bool(
            control is not None and control.get("named_control_observed") is True
        )
        invoke_supported = bool(
            control is not None and control.get("invoke_supported") is True
        )
        named_control_ready = bool(named_control_observed and invoke_supported)
        semantic_mapping_ready = bool(
            semantic_mapping is not None
            and semantic_mapping.get("verified") is True
            and semantic_mapping.get("invocation_ready") is True
        )
        resolved_invocation_ready = bool(
            named_control_ready or semantic_mapping_ready
        )
        if control is None and semantic_mapping is None:
            missing_required_command_ids.append(command_id)
            reasons.append(f"runtime_{reason_prefix}_control_evidence_missing")
            if evidence.get("anonymous_toolbars"):
                reasons.append(
                    f"runtime_verified_{reason_prefix}_toolbar_mapping_not_available"
                )
        elif not resolved_invocation_ready:
            if semantic_mapping is not None:
                reasons.append(
                    f"runtime_verified_{reason_prefix}_control_not_enabled"
                )
            elif not named_control_observed:
                reasons.append(
                    f"runtime_named_{reason_prefix}_control_not_observed"
                )
            else:
                reasons.append(
                    f"runtime_named_{reason_prefix}_control_not_invocable"
                )
        toolbar_name = (
            "3D Movement"
            if command_id == "cmdViewer3DMovementOptions"
            else "3D Viewer"
        )
        accessibility_target = (
            dict(semantic_mapping)
            if semantic_mapping_ready
            else {
                "target_kind": "named_control",
                "invocation_method": "accessibility_named_control",
                "toolbar_name": toolbar_name,
                "control_name": VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS.get(
                    command_id
                ),
                "command_id": command_id,
            }
            if named_control_ready
            else None
        )
        command_gates.append(
            {
                "command_id": command_id,
                "expected_control_name": VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS.get(
                    command_id
                ),
                "observed_control_name": (
                    control.get("observed_control_name") if control is not None else None
                ),
                "named_control_observed": named_control_observed,
                "invoke_supported": invoke_supported,
                "named_control_ready": named_control_ready,
                "semantic_mapping_ready": semantic_mapping_ready,
                "resolved_invocation_ready": resolved_invocation_ready,
                "control_resolution": (
                    "named_control"
                    if named_control_ready
                    else "verified_anonymous_toolbar_mapping"
                    if semantic_mapping_ready
                    else "unresolved"
                ),
                "semantic_mapping": semantic_mapping,
                "accessibility_target": accessibility_target,
            }
        )
    reasons = _unique_strings(reasons)
    base_preflight_satisfied = bool(
        runtime.get("base_gate_satisfied") is True
        and runtime.get("binding_verified") is True
    )
    required_control_evidence_complete = not missing_required_command_ids
    observed_required_control_blocks_automation = bool(
        required_control_evidence_complete
        and any(
            item.get("resolved_invocation_ready") is not True
            for item in command_gates
        )
    )
    gate_satisfied = not reasons
    return {
        "required": True,
        "status": (
            "verified_ready"
            if gate_satisfied
            else "missing"
            if runtime.get("observation_available") is not True
            else "verified_blocked"
        ),
        "automation_gate_satisfied": gate_satisfied,
        "observation_available": runtime.get("observation_available") is True,
        "binding_verified": runtime.get("binding_verified") is True,
        "base_preflight_satisfied": base_preflight_satisfied,
        "artifact_path": runtime.get("artifact_path"),
        "required_command_ids": list(required_command_ids),
        "missing_required_command_ids": missing_required_command_ids,
        "required_control_evidence_complete": required_control_evidence_complete,
        "observed_required_control_blocks_automation": (
            observed_required_control_blocks_automation
        ),
        "require_viewer_document": require_viewer_document,
        "require_empty_viewport_focus_target": require_empty_viewport_focus_target,
        "semantic_viewport_focus_supported": evidence.get(
            "semantic_viewport_focus_supported"
        ),
        "semantic_viewport_focus_ready": semantic_viewport_focus_ready,
        "unnamed_toolbar_children_observed": runtime.get(
            "unnamed_toolbar_children_observed"
        ),
        "command_gates": command_gates,
        "semantic_mapping_used": any(
            item.get("semantic_mapping_ready") is True for item in command_gates
        ),
        "block_reasons": reasons,
    }


def _resolved_recipe_accessibility_target(
    runtime_gate: dict[str, Any],
    command_id: str,
    named_fallback: dict[str, Any],
) -> dict[str, Any]:
    """Return a server-derived semantic target or the existing named target."""

    command_gate = next(
        (
            item
            for item in runtime_gate.get("command_gates") or []
            if isinstance(item, dict) and item.get("command_id") == command_id
        ),
        None,
    )
    if isinstance(command_gate, dict):
        target = command_gate.get("accessibility_target")
        if (
            isinstance(target, dict)
            and target.get("target_kind")
            == "verified_anonymous_toolbar_child"
        ):
            return dict(target)
    return {
        "target_kind": "named_control",
        "invocation_method": "accessibility_named_control",
        **named_fallback,
    }


def _crystal_standard_view_recipe_fields(
    expected_axis_layout: dict[str, Any],
) -> dict[str, Any]:
    """Return the crystal-only camera contract layered onto a standard view recipe."""

    return {
        "schema_version": CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION,
        "recipe_kind": CRYSTAL_STANDARD_VIEW_RECIPE_KIND,
        "camera_match_contract": {
            "scope": CRYSTAL_STANDARD_VIEW_CAMERA_MATCH_SCOPE,
            "required_view_direction_match": True,
            "required_analytic_camera_up_match": False,
            "required_analytic_camera_right_match": False,
            "in_plane_roll_policy": (
                "materials_studio_native_reset_or_unmodified_arrow_rotation_roll"
            ),
            "expected_axis_layout": dict(expected_axis_layout),
            "camera_matches_manifest_interpretation": (
                "view_direction_and_native_roll_contract_match_not_exact_analytic_camera_basis"
            ),
        },
        "required_record_evidence": {
            "field": "crystal_camera_evidence",
            "required_true_fields": [
                "view_direction_matches_manifest",
                "native_in_plane_roll_observed",
            ],
            "analytic_in_plane_basis_match_required": False,
            "fresh_workspace_screenshot_required": True,
        },
    }


def _view_replay_execution_recipe(
    step: dict[str, Any],
    command_evidence: dict[str, Any],
    *,
    model_type: str | None = None,
    runtime_ui_preflight: dict[str, Any] | None = None,
    runtime_accessibility_preflight: dict[str, Any] | None = None,
    local_miller_transaction_supported: bool = False,
) -> dict[str, Any]:
    """Return a conservative machine-readable recipe for one prepared view."""

    view_name = str(step.get("view_name") or "")
    camera = step.get("camera") if isinstance(step.get("camera"), dict) else {}
    crystallography = (
        step.get("crystallography")
        if isinstance(step.get("crystallography"), dict)
        else {}
    )
    crystal_standard_view = bool(
        model_type == "crystal" and view_name in STANDARD_CARTESIAN_VIEW_NAMES
    )
    commands = [item for item in command_evidence.get("commands") or [] if isinstance(item, dict)]
    registered_command_ids = command_evidence.get("registered_view_command_ids")
    command_ids = {
        str(item)
        for item in registered_command_ids or []
        if item
    }
    registry_verified = command_evidence.get("registry_found") is True
    keyboard_help_verified = bool(
        command_evidence.get("keyboard_help_found") is True
        and command_evidence.get("unmodified_arrow_keys_rotate_view") is True
        and command_evidence.get("default_arrow_rotation_increment_degrees") == 45
        and command_evidence.get("shift_arrow_keys_rotate_selected_objects") is True
    )
    movement_help_verified = bool(
        command_evidence.get("movement_help_found") is True
        and command_evidence.get("movement_dialog_angle_supported") is True
        and command_evidence.get("movement_options_command_registered") is True
    )
    base = {
        "schema_version": (
            CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION
            if crystal_standard_view
            else VIEW_REPLAY_BASE_RECIPE_SCHEMA_VERSION
        ),
        "view_name": view_name,
        "executor": "computer_use_or_reviewed_external_gui",
        "structure_mutation_allowed": False,
        "launch_new_matstudio_process_allowed": False,
        "blind_coordinate_action_allowed": False,
        "copy_script_camera_api_verified": False,
        "prohibited_command_prefixes": list(STRUCTURE_MUTATING_VIEW_COMMAND_PREFIXES),
        "post_action_record_tool": "material_studio_gui_record_view_replay",
        "requires_exact_window_binding": True,
        "requires_fresh_visual_confirmation": True,
        "unnamed_control_invocation_allowed_only_when_verified_mapping": True,
        "local_command_registry_verified": registry_verified,
        "command_registry_path": command_evidence.get("registry_path"),
        "keyboard_help_path": command_evidence.get("keyboard_help_path"),
        "keyboard_help_verified": keyboard_help_verified,
    }
    if step.get("supported") is not True:
        return {
            **base,
            "status": "unsupported_view_definition",
            "automation_ready": False,
            "allowed_native_command_ids": [],
            "block_reasons": ["unsupported_view_definition"],
        }

    front_camera = (
        view_name == "front"
        and camera.get("camera_direction") == [0.0, 0.0, 1.0]
        and camera.get("camera_up") == [0.0, 1.0, 0.0]
    )
    if front_camera:
        command_id = "cmdViewer3DResetView"
        command_available = command_id in command_ids
        static_recipe_ready = bool(registry_verified and command_available)
        runtime_accessibility_gate = _view_runtime_accessibility_gate(
            runtime_accessibility_preflight,
            required_command_ids=[command_id],
            require_viewer_document=True,
            require_empty_viewport_focus_target=False,
        )
        automation_ready = bool(
            static_recipe_ready
            and runtime_accessibility_gate["automation_gate_satisfied"]
        )
        block_reasons: list[str] = []
        if not registry_verified:
            block_reasons.append("local_view_command_registry_not_verified")
        if not command_available:
            block_reasons.append("reset_view_command_not_registered")
        block_reasons.extend(runtime_accessibility_gate["block_reasons"])
        block_reasons = _unique_strings(block_reasons)
        expected_axis_layout = {
            "screen_right": "A",
            "screen_up": "B",
            "view_depth": "C",
        }
        return {
            **base,
            "status": (
                "native_accessibility_command_ready"
                if automation_ready
                else "native_accessibility_command_runtime_unverified"
                if static_recipe_ready
                else "native_accessibility_command_unverified"
            ),
            "automation_ready": automation_ready,
            "static_recipe_ready": static_recipe_ready,
            "allowed_native_command_ids": [command_id],
            "native_command_id": command_id,
            "camera_result_depends_on_reset_baseline": True,
            "camera_result_established_by": "native_reset_view",
            "final_camera_established_by_native_command_id": command_id,
            "runtime_accessibility_preflight": runtime_accessibility_gate,
            "accessibility_target": _resolved_recipe_accessibility_target(
                runtime_accessibility_gate,
                command_id,
                {
                    "toolbar_name": "3D Viewer",
                    "control_name": "3D Viewer Reset View",
                    "command_id": command_id,
                },
            ),
            "expected_axis_layout": expected_axis_layout,
            **(
                _crystal_standard_view_recipe_fields(expected_axis_layout)
                if crystal_standard_view
                else {}
            ),
            "action_sequence": [
                "verify_exact_current_wrapper_window",
                "activate_target_window",
                "refresh_accessibility_tree_and_verify_recipe_control_target",
                "invoke_recipe_reset_view_accessibility_target",
                "capture_fresh_visual_evidence",
                "compare_against_manifest_camera_and_projection",
                "record_view_replay_event",
            ],
            "block_reasons": block_reasons,
        }

    documented_key_recipe = DOCUMENTED_VIEW_KEY_RECIPES.get(view_name)
    if documented_key_recipe is not None:
        reset_command_id = "cmdViewer3DResetView"
        reset_command_available = reset_command_id in command_ids
        keyboard_stages = documented_key_recipe.get("keyboard_stages")
        staged_keyboard_recipe = isinstance(keyboard_stages, list)
        movement_command_id = documented_key_recipe.get("movement_options_command_id")
        movement_command_available = bool(
            not staged_keyboard_recipe
            or (
                isinstance(movement_command_id, str)
                and movement_command_id in command_ids
                and movement_help_verified
            )
        )
        static_recipe_ready = bool(
            registry_verified
            and reset_command_available
            and keyboard_help_verified
            and movement_command_available
        )
        required_accessibility_command_ids = [reset_command_id]
        if staged_keyboard_recipe and isinstance(movement_command_id, str):
            required_accessibility_command_ids.append(movement_command_id)
        runtime_accessibility_gate = _view_runtime_accessibility_gate(
            runtime_accessibility_preflight,
            required_command_ids=required_accessibility_command_ids,
            require_viewer_document=True,
            require_empty_viewport_focus_target=True,
        )
        automation_ready = bool(
            static_recipe_ready
            and runtime_accessibility_gate["automation_gate_satisfied"]
        )
        block_reasons: list[str] = []
        if not registry_verified:
            block_reasons.append("local_view_command_registry_not_verified")
        if not reset_command_available:
            block_reasons.append("reset_view_command_not_registered")
        if not keyboard_help_verified:
            block_reasons.append("installed_arrow_key_view_rotation_help_not_verified")
        if staged_keyboard_recipe and not movement_command_available:
            block_reasons.append("movement_angle_control_not_verified")
        block_reasons.extend(runtime_accessibility_gate["block_reasons"])
        block_reasons = _unique_strings(block_reasons)

        common_recipe: dict[str, Any] = {
            **base,
            "status": (
                "documented_staged_keyboard_sequence_ready"
                if automation_ready and staged_keyboard_recipe
                else "documented_keyboard_sequence_ready"
                if automation_ready
                else "documented_staged_keyboard_sequence_runtime_unverified"
                if static_recipe_ready and staged_keyboard_recipe
                else "documented_keyboard_sequence_runtime_unverified"
                if static_recipe_ready
                else "documented_staged_keyboard_sequence_unverified"
                if staged_keyboard_recipe
                else "documented_keyboard_sequence_unverified"
            ),
            "automation_ready": automation_ready,
            "static_recipe_ready": static_recipe_ready,
            "runtime_accessibility_preflight": runtime_accessibility_gate,
            "allowed_native_command_ids": [
                command_id
                for command_id in (reset_command_id, movement_command_id)
                if isinstance(command_id, str)
            ],
            "native_command_id": reset_command_id,
            "camera_result_depends_on_reset_baseline": True,
            "camera_result_established_by": (
                "reset_plus_staged_unmodified_keyboard_recipe"
                if staged_keyboard_recipe
                else "reset_plus_unmodified_keyboard_recipe"
            ),
            "final_camera_established_by_native_command_id": None,
            "reset_before_key_sequence": True,
            "prohibited_modifier_keys": ["Shift"],
            "rotation_increment_user_configurable": True,
            "expected_axis_layout": dict(documented_key_recipe["expected_axis_layout"]),
            "accessibility_target": _resolved_recipe_accessibility_target(
                runtime_accessibility_gate,
                reset_command_id,
                {
                    "toolbar_name": "3D Viewer",
                    "control_name": "3D Viewer Reset View",
                    "command_id": reset_command_id,
                },
            ),
            "keyboard_focus_target": "visually_verified_empty_3d_viewer_region",
            "post_action_checks": [
                "verify_expected_axis_layout",
                "verify_projection_bbox_and_overlap_count",
                "verify_structure_geometry_unchanged",
            ],
            "action_sequence": [
                "verify_exact_current_wrapper_window",
                "activate_target_window",
                "refresh_accessibility_tree_and_verify_recipe_reset_target",
                "invoke_recipe_reset_view_accessibility_target",
                "focus_visually_verified_empty_3d_viewer_region",
                "press_exact_unmodified_arrow_key_sequence",
                "capture_fresh_visual_evidence",
                "compare_axes_projection_and_overlap_against_manifest",
                "record_view_replay_event_with_keyboard_evidence",
            ],
            "safety_notes": [
                "Do not hold Shift; Shift+arrow rotates selected objects and changes geometry.",
                "The Movement dialog can change the arrow-key angle; require the 45-degree visual postcheck.",
            ],
            "block_reasons": block_reasons,
        }
        if crystal_standard_view:
            common_recipe.update(
                _crystal_standard_view_recipe_fields(
                    dict(documented_key_recipe["expected_axis_layout"])
                )
            )
        if not staged_keyboard_recipe:
            common_recipe.update(
                {
                    "key_sequence": list(documented_key_recipe["key_sequence"]),
                    "modifier_keys": [],
                    "rotation_increment_degrees": 45,
                }
            )
            return common_recipe

        normalized_stages = [
            {
                key: (
                    list(value)
                    if isinstance(value, list)
                    else value
                )
                for key, value in stage.items()
            }
            for stage in keyboard_stages
            if isinstance(stage, dict)
        ]
        common_recipe.update(
            {
                "schema_version": (
                    CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION
                    if crystal_standard_view
                    else VIEW_REPLAY_STAGED_KEYBOARD_RECIPE_SCHEMA_VERSION
                ),
                "keyboard_stages": normalized_stages,
                "restore_rotation_increment_degrees": documented_key_recipe[
                    "restore_rotation_increment_degrees"
                ],
                "movement_options_command_id": movement_command_id,
                "movement_angle_control_id": documented_key_recipe["movement_angle_control_id"],
                "movement_screen_factor_control_id": documented_key_recipe[
                    "movement_screen_factor_control_id"
                ],
                "movement_screen_factor_expected": documented_key_recipe[
                    "movement_screen_factor_expected"
                ],
                "movement_dialog_closed_after_restore": documented_key_recipe[
                    "movement_dialog_closed_after_restore"
                ],
                "movement_accessibility_target": {
                    **_resolved_recipe_accessibility_target(
                        runtime_accessibility_gate,
                        str(movement_command_id),
                        {
                            "toolbar_name": "3D Movement",
                            "control_name": "Movement",
                            "command_id": movement_command_id,
                        },
                    ),
                    "angle_control_id": documented_key_recipe["movement_angle_control_id"],
                    "screen_factor_control_id": documented_key_recipe[
                        "movement_screen_factor_control_id"
                    ],
                },
                "post_action_checks": [
                    "verify_expected_axis_layout",
                    "verify_projection_bbox_and_overlap_count",
                    "verify_rotation_increment_restored",
                    "verify_movement_screen_factor_unchanged",
                    "verify_movement_dialog_closed",
                    "verify_structure_geometry_unchanged",
                ],
                "action_sequence": [
                    "verify_exact_current_wrapper_window",
                    "activate_target_window",
                    "refresh_accessibility_tree_and_verify_recipe_reset_and_movement_targets",
                    "invoke_recipe_reset_view_accessibility_target",
                    "focus_visually_verified_empty_3d_viewer_region",
                    "execute_each_unmodified_keyboard_stage_at_its_exact_angle",
                    "restore_rotation_increment_to_45_degrees",
                    "verify_screen_factor_remains_2_percent",
                    "close_movement_dialog",
                    "capture_fresh_visual_evidence",
                    "compare_axes_projection_and_overlap_against_manifest",
                    "record_view_replay_event_with_staged_keyboard_evidence",
                ],
                "safety_notes": [
                    "Do not hold Shift; Shift+arrow rotates selected objects and changes geometry.",
                    "Use only numNudgeAngle; do not invoke any cmdNudge or object-rotation button.",
                    "Restore numNudgeAngle to 45 degrees and leave numNudgeFactor at 2.0 before recording.",
                ],
            }
        )
        return common_recipe

    raw_plane_indices = crystallography.get("crystal_plane_indices")
    direction_plane_mapping = (
        crystallography.get("crystal_direction_view_onto_plane_mapping")
        if isinstance(
            crystallography.get("crystal_direction_view_onto_plane_mapping"),
            dict,
        )
        else {}
    )
    direction_via_miller_plane = bool(
        raw_plane_indices is None
        and crystallography.get("crystal_direction_indices") is not None
        and direction_plane_mapping.get("status") == "exact_integer_plane_collinear"
        and direction_plane_mapping.get("automation_eligible") is True
        and direction_plane_mapping.get("miller_plane_indices") is not None
    )
    if direction_via_miller_plane:
        raw_plane_indices = direction_plane_mapping.get("miller_plane_indices")
    if raw_plane_indices is not None:
        index_error: str | None = None
        try:
            plane_indices = _normalize_miller_plane_indices(
                raw_plane_indices,
                field_name="crystallography.crystal_plane_indices",
            )
            dialog_indices = _miller_plane_dialog_indices(plane_indices)
        except GuiError as exc:
            plane_indices = []
            dialog_indices = []
            index_error = str(exc)
        view_onto_command_id = "cmdViewer3DViewOnto"
        view_onto_command_available = view_onto_command_id in command_ids
        runtime_accessibility_gate = _view_runtime_accessibility_gate(
            runtime_accessibility_preflight,
            required_command_ids=[],
            require_viewer_document=True,
            require_empty_viewport_focus_target=False,
        )
        runtime_preflight = runtime_ui_preflight if isinstance(runtime_ui_preflight, dict) else {}
        selection_profile = runtime_preflight.get("selection_profile")
        transactional_runtime_verification = bool(
            local_miller_transaction_supported
            and runtime_accessibility_gate["automation_gate_satisfied"]
        )
        if selection_profile is None and transactional_runtime_verification:
            selection_profile = "viewport_unique_plane_properties_verified"
        evidence_requirements = {
            "symmetry_builder_registry_found": command_evidence.get(
                "symmetry_builder_registry_found"
            )
            is True,
            "miller_plane_command_registered": command_evidence.get(
                "miller_plane_command_registered"
            )
            is True,
            "properties_explorer_registry_found": command_evidence.get(
                "properties_explorer_registry_found"
            )
            is True,
            "properties_explorer_command_registered": command_evidence.get(
                "properties_explorer_command_registered"
            )
            is True,
            "miller_plane_create_help_found": command_evidence.get(
                "miller_plane_create_help_found"
            )
            is True,
            "miller_plane_create_workflow_verified": command_evidence.get(
                "miller_plane_create_workflow_verified"
            )
            is True,
            "miller_plane_working_help_found": command_evidence.get(
                "miller_plane_working_help_found"
            )
            is True,
            "miller_plane_selection_view_onto_workflow_verified": command_evidence.get(
                "miller_plane_selection_view_onto_workflow_verified"
            )
            is True,
            "positioning_help_found": command_evidence.get("positioning_help_found") is True,
            "native_view_roll_policy_documented": command_evidence.get(
                "native_view_roll_policy_documented"
            )
            is True,
        }
        if selection_profile == "object_tree_exact_item":
            evidence_requirements.update(
                {
                    "tree_explorer_registry_found": command_evidence.get(
                        "tree_explorer_registry_found"
                    )
                    is True,
                    "tree_explorer_command_registered": command_evidence.get(
                        "tree_explorer_command_registered"
                    )
                    is True,
                    "object_tree_hierarchy_help_verified": command_evidence.get(
                        "object_tree_hierarchy_help_verified"
                    )
                    is True,
                }
            )
        elif selection_profile == "viewport_unique_plane_properties_verified":
            evidence_requirements[
                "viewport_miller_plane_selection_properties_workflow_verified"
            ] = (
                command_evidence.get(
                    "viewport_miller_plane_selection_properties_workflow_verified"
                )
                is True
            )
        runtime_gate_satisfied = bool(
            runtime_preflight.get("automation_gate_satisfied") is True
            or transactional_runtime_verification
        )
        selection_profile_verified = selection_profile in {
            "object_tree_exact_item",
            "viewport_unique_plane_properties_verified",
        }
        runtime_block_reasons = [
            str(item)
            for item in runtime_preflight.get("block_reasons") or []
            if str(item)
        ]
        if not runtime_preflight and not transactional_runtime_verification:
            runtime_block_reasons = ["runtime_miller_plane_ui_preflight_missing"]
        elif transactional_runtime_verification:
            runtime_block_reasons = []
        automation_ready = bool(
            registry_verified
            and view_onto_command_available
            and index_error is None
            and all(evidence_requirements.values())
            and runtime_gate_satisfied
            and selection_profile_verified
            and runtime_accessibility_gate["automation_gate_satisfied"]
        )
        block_reasons: list[str] = []
        if not registry_verified:
            block_reasons.append("local_view_command_registry_not_verified")
        if not view_onto_command_available:
            block_reasons.append("view_onto_command_not_registered")
        block_reasons.extend(runtime_accessibility_gate["block_reasons"])
        if index_error is not None:
            block_reasons.append("invalid_miller_plane_indices")
        if not selection_profile_verified:
            block_reasons.append("runtime_miller_plane_selection_profile_not_verified")
        for requirement, verified in evidence_requirements.items():
            if not verified:
                block_reasons.append(f"{requirement}_not_verified")
        block_reasons.extend(runtime_block_reasons)
        block_reasons = _unique_strings(block_reasons)
        properties_label = _miller_plane_label(dialog_indices) if dialog_indices else None
        viewport_selection_profile = (
            selection_profile == "viewport_unique_plane_properties_verified"
        )
        prepared_selection_method = (
            MILLER_PLANE_VIEWPORT_SELECTION_METHOD
            if viewport_selection_profile
            else MILLER_PLANE_SELECTION_METHOD
        )
        supporting_native_command_ids = [
            "cmdSymmetryBuilderMillerPlanes",
            "cmdGPEToggleExplorer",
            "cmdViewer3DSelection",
        ]
        if not viewport_selection_profile:
            supporting_native_command_ids.append("cmdTEToggleExplorer")
        recipe_kind = (
            "crystal_direction_via_collinear_miller_plane_view_onto"
            if direction_via_miller_plane
            else "miller_plane_view_onto"
        )
        camera_match_scope = (
            MILLER_DIRECTION_CAMERA_MATCH_SCOPE
            if direction_via_miller_plane
            else MILLER_PLANE_CAMERA_MATCH_SCOPE
        )
        return {
            **base,
            "schema_version": MILLER_VIEW_ONTO_RECIPE_SCHEMA_VERSION,
            "recipe_kind": recipe_kind,
            "status": (
                "documented_crystal_direction_via_miller_plane_view_onto_recipe_ready"
                if automation_ready and direction_via_miller_plane
                else "documented_miller_plane_view_onto_recipe_ready"
                if automation_ready
                else "documented_crystal_direction_via_miller_plane_view_onto_recipe_unverified"
                if direction_via_miller_plane
                else "documented_miller_plane_view_onto_recipe_unverified"
            ),
            "automation_ready": automation_ready,
            "camera_result_depends_on_reset_baseline": False,
            "camera_result_established_by": "native_miller_plane_view_onto",
            "pre_action_view_baseline_required": True,
            "reset_view_allowed": False,
            "reset_view_role": "forbidden_because_ms_20_1_reset_is_not_reliably_undoable",
            "final_camera_established_by_native_command_id": (
                view_onto_command_id
            ),
            "allowed_native_command_ids": [view_onto_command_id],
            "native_command_id": view_onto_command_id,
            "modifier_keys": [],
            "prohibited_modifier_keys": ["Shift", "Ctrl", "Alt", "Win"],
            "supporting_native_command_ids": supporting_native_command_ids,
            "runtime_accessibility_preflight": runtime_accessibility_gate,
            "accessibility_target": None,
            "runtime_ui_preflight": {
                "required": True,
                "status": (
                    "transactional_verification_required"
                    if transactional_runtime_verification
                    and runtime_preflight.get("automation_gate_satisfied") is not True
                    else runtime_preflight.get("status") or "missing"
                ),
                "automation_gate_satisfied": runtime_gate_satisfied,
                "verification_timing": (
                    "during_local_transaction_before_plane_create"
                    if transactional_runtime_verification
                    else "persisted_preflight"
                ),
                "artifact_path": runtime_preflight.get("artifact_path"),
                "binding_verified": bool(
                    runtime_preflight.get("binding_verified") is True
                    or transactional_runtime_verification
                ),
                "block_reasons": runtime_block_reasons,
                "selection_profile": selection_profile,
                "required_true_fields": list(MILLER_RUNTIME_UI_REQUIRED_TRUE_FIELDS),
                "required_menu_key_sequence": list(MILLER_RUNTIME_UI_REQUIRED_KEY_SEQUENCE),
                "required_dialog_identifiers": dict(MILLER_RUNTIME_UI_EXPECTED_IDENTIFIERS),
                "selection_modifier_keys": [],
            },
            "selection_required": True,
            "miller_plane_indices": plane_indices or None,
            "dialog_miller_indices": dialog_indices or None,
            "dialog_miller_indices_text": (
                _miller_plane_dialog_text(dialog_indices) if dialog_indices else None
            ),
            "dialog_index_entry_contract": {
                "control_id": "TxtHKL",
                "expected_value": (
                    _miller_plane_dialog_text(dialog_indices) if dialog_indices else None
                ),
                "value_source": "fresh_modeless_child_accessibility_value",
                "replacement_strategy_order": [
                    "accessibility_set_value_exact",
                    "focus_home_delete_retained_prefix_before_expected_value",
                    "focus_home_type_missing_expected_prefix_over_retained_suffix",
                    "focus_end_replace_minimal_differing_suffix_from_fresh_value",
                    "preserve_longest_common_substring_apply_one_edge_repair_then_replan",
                    "focus_end_backspace_observed_character_count_then_type_exact",
                ],
                "keyboard_correction_contract": {
                    "fresh_observed_value_required": True,
                    "minimal_suffix_rule": (
                        "when_observed_and_expected_share_a_nonempty_prefix_focus_end_"
                        "backspace_only_the_observed_suffix_then_type_only_the_expected_suffix"
                    ),
                    "full_replacement_rule": (
                        "focus_end_backspace_the_fresh_observed_character_count_then_type_exact"
                    ),
                    "post_full_replacement_retained_prefix_rule": (
                        "when_fresh_readback_ends_with_expected_focus_home_then_delete_"
                        "the_retained_prefix_character_count"
                    ),
                    "post_full_replacement_retained_suffix_rule": (
                        "when_expected_ends_with_nonempty_fresh_readback_focus_home_then_"
                        "type_only_the_missing_expected_prefix"
                    ),
                    "overlap_repair_rule": (
                        "preserve_the_longest_common_contiguous_substring_tie_breaking_"
                        "by_earliest_observed_then_expected_start_apply_exactly_one_"
                        "nonempty_edge_repair_in_observed_prefix_observed_suffix_"
                        "expected_prefix_expected_suffix_order_then_replan_fresh"
                    ),
                    "maximum_full_replacement_attempts": 1,
                    "relation_repairs_allowed_before_or_after_full_replacement": True,
                    "allowed_after_full_replacement": [
                        "focus_home_delete_retained_prefix_before_expected_value",
                        "focus_home_type_missing_expected_prefix_over_retained_suffix",
                        "focus_end_replace_minimal_differing_suffix_from_fresh_value",
                        "preserve_longest_common_substring_apply_one_edge_repair_then_replan",
                    ],
                    "navigation_key_settle_delay_milliseconds": (
                        MILLER_DIALOG_NAVIGATION_KEY_SETTLE_DELAY_MILLISECONDS
                    ),
                    "repeated_key_interpress_delay_milliseconds": (
                        MILLER_DIALOG_REPEATED_KEY_INTERPRESS_DELAY_MILLISECONDS
                    ),
                    "post_mutation_readback_delay_milliseconds": (
                        MILLER_DIALOG_POST_MUTATION_READBACK_DELAY_MILLISECONDS
                    ),
                    "first_destructive_key_must_wait_after_navigation": True,
                    "batch_repeated_backspace_or_delete_allowed": False,
                    "fresh_child_readback_required_after_each_mutation": True,
                    "unrelated_post_full_readback_action": "abort_without_create",
                    "mismatch_after_final_strategy": "abort_without_create",
                },
                "control_a_replacement_assumption_allowed": False,
                "shift_selection_allowed": False,
                "fresh_child_state_required_after_entry": True,
                "read_back_required_before_create": True,
                "comparison": "exact_trimmed_text",
                "create_allowed_only_after_exact_match": True,
                "mismatch_action": (
                    "do_not_invoke_create_correct_value_and_reverify_from_fresh_child_state"
                ),
            },
            "properties_miller_label": properties_label,
            "source_crystal_direction_indices": (
                crystallography.get("crystal_direction_indices")
                if direction_via_miller_plane
                else None
            ),
            "source_crystal_direction_label": (
                crystallography.get("crystal_direction_label")
                if direction_via_miller_plane
                else None
            ),
            "direction_plane_mapping": (
                direction_plane_mapping if direction_via_miller_plane else None
            ),
            "selection_method": prepared_selection_method,
            "selection_path_suffix": (
                None
                if viewport_selection_profile
                else list(MILLER_PLANE_OBJECT_TREE_PATH_SUFFIX)
            ),
            "viewport_selection_contract": (
                {
                    "hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
                    "capture_fresh_screenshot_before_create": True,
                    "capture_fresh_screenshot_after_create": True,
                    "require_unique_newly_rendered_plane_region": True,
                    "click_coordinates_source": "fresh_after_create_screenshot",
                    "require_no_modifier_keys": True,
                    "properties_filter": "Miller Plane",
                    "properties_miller_label": properties_label,
                    "failure_action": "abort_and_cleanup_without_view_record",
                }
                if viewport_selection_profile
                else None
            ),
            "properties_verification": {
                "filter": "Miller Plane",
                "miller_label": properties_label,
                "selected_plane_count": 1,
            },
            "view_command_invocation": {
                "toolbar_name": "3D Viewer",
                "dropdown_control_name": "3D Viewer Recenter",
                "menu_item_name": "View Onto",
                "command_id": view_onto_command_id,
                "selection_numeric_command_id": 33288,
                "recenter_numeric_command_id": 33296,
                "view_onto_numeric_command_id": 33297,
                "fit_numeric_command_id": 33299,
                "recenter_button_style": 10,
                "installed_registry_order_verified": registry_verified,
                "semantic_targeting": (
                    "wm_command_after_live_toolbar_numeric_mapping_and_installed_registry_order_verification"
                ),
            },
            "miller_planes_dialog_invocation": {
                "method": "keyboard_menu_mnemonic",
                "menu_path": ["Tools", "Miller Planes"],
                "key_sequence": list(MILLER_RUNTIME_UI_REQUIRED_KEY_SEQUENCE),
                "dialog_title": "Miller Planes",
                "dialog_control_id": "MillerPlanesCtl",
                "miller_indices_control_id": "TxtHKL",
                "create_button_control_id": "CmdCreate",
                "modeless_dialog": True,
                "targeting_surface": "fresh_modeless_child_window_state",
                "create_button_targeting": (
                    "verified_accessibility_in_child_bounds_or_fresh_child_screenshot"
                ),
                "close_button_targeting": (
                    "verified_accessibility_in_child_bounds_or_fresh_child_screenshot"
                ),
                "parent_window_coordinates_allowed": False,
                "out_of_bounds_accessibility_targets_allowed": False,
                "pointer_or_accessibility_menu_click_allowed": False,
                "reason": (
                    "A pointer release on Tools > Miller Planes can click through into the modeless "
                    "dialog and activate Create. Use the verified keyboard mnemonic path, then "
                    "target dialog controls only from a fresh child-window state."
                ),
            },
            "unexpected_plane_guard": {
                "detect_named_undo_label": "Undo Create Miller Plane",
                "cleanup_action": "invoke_exact_named_undo_create_miller_plane",
                "continue_after_cleanup": False,
                "required_post_cleanup_checks": [
                    "document_clean",
                    "no_temporary_miller_nodes_remaining",
                    "structure_artifact_sha256_unchanged",
                ],
                "failure_action": "abort_current_view_replay_and_prepare_runtime_ui_preflight_again",
            },
            "camera_match_contract": {
                "scope": camera_match_scope,
                "coordinate_system": (
                    "crystal_lattice_direction"
                    if direction_via_miller_plane
                    else "crystal_reciprocal_plane_normal"
                ),
                "required_plane_normal_match": True,
                **(
                    {
                        "required_direct_lattice_direction_match": True,
                        "required_direction_plane_collinearity_status": (
                            "exact_integer_plane_collinear"
                        ),
                    }
                    if direction_via_miller_plane
                    else {}
                ),
                "required_analytic_camera_up_match": False,
                "required_analytic_camera_right_match": False,
                "in_plane_roll_policy": (
                    "materials_studio_native_smallest_acute_angle_from_pre_action_view_baseline"
                ),
                "native_roll_must_be_reported_separately": True,
                "camera_matches_manifest_interpretation": (
                    "direct_lattice_direction_and_collinear_plane_normal_match_with_native_roll"
                    if direction_via_miller_plane
                    else "plane_normal_and_native_roll_contract_match_not_exact_analytic_camera_basis"
                ),
            },
            "transient_change_contract": {
                "persistent_structure_or_document_change_allowed": False,
                "temporary_miller_plane_creation_allowed": True,
                "capture_screenshot_before_cleanup": True,
                "allowed_undo_label_patterns": [
                    pattern.pattern for pattern in MILLER_PLANE_UNDO_LABEL_PATTERNS
                ],
                "required_undo_labels": [
                    "Undo View Onto Miller Plane",
                    "Undo Create Miller Plane",
                ],
                "require_exactly_one_new_miller_plane": True,
                "require_selected_plane_count": 1,
                "require_document_clean_before_and_after": True,
                "require_no_temporary_miller_nodes_after_cleanup": True,
                "require_structure_artifact_sha256_unchanged": True,
                "restore_initial_view_via_whitelisted_undo": True,
                "require_exact_viewport_pixel_restoration": True,
                "reset_view_forbidden": True,
                "unexpected_dialog_click_through_requires_exact_undo_and_abort": True,
            },
            "required_record_evidence": {
                "field": "miller_plane_evidence",
                "required_true_fields": [
                    *MILLER_PLANE_REQUIRED_TRUE_EVIDENCE_FIELDS,
                    *(
                        ["direct_lattice_direction_matches_manifest"]
                        if direction_via_miller_plane
                        else []
                    ),
                ],
                "require_unmodified_input": True,
                "modifier_keys": [],
            },
            "action_sequence": [
                "verify_exact_current_wrapper_window_and_single_process",
                "activate_target_window_and_verify_foreground",
                "verify_current_bound_runtime_ui_preflight_gate",
                "record_clean_document_state_and_structure_artifact_sha256",
                "capture_pre_action_view_baseline_with_properties_explorer_open",
                "verify_3d_viewer_selection_mode_is_active",
                "invoke_tools_miller_planes_with_alt_t_then_m_keyboard_mnemonics",
                "verify_miller_planes_dialog_and_exact_control_ids",
                "capture_fresh_modeless_dialog_child_window_state",
                "abort_after_exact_undo_if_unexpected_default_plane_was_created",
                "enter_exact_three_index_dialog_values",
                "try_accessibility_set_value_exact",
                "wait_recipe_navigation_delay_after_home_or_end",
                "pace_each_backspace_or_delete_with_recipe_interpress_delay",
                "replace_only_minimal_differing_suffix_when_fresh_value_shares_prefix",
                "fallback_full_backspace_and_exact_retype_from_fresh_observed_character_count",
                "repair_post_full_replacement_retained_prefix_or_suffix_from_home",
                "preserve_longest_common_substring_and_replan_from_each_fresh_readback",
                "wait_recipe_post_mutation_delay_before_fresh_readback",
                "read_back_txt_hkl_value_from_fresh_child_accessibility_state",
                "read_back_txt_hkl_value_after_each_mutation",
                "correct_dialog_value_and_reverify_if_not_exact",
                "block_create_until_exact_dialog_value_match",
                "invoke_create_only_from_verified_child_bounds_or_fresh_child_screenshot",
                *(
                    [
                        "capture_fresh_after_create_screenshot",
                        "isolate_unique_newly_rendered_transient_plane_region_from_fresh_before_after_screenshots",
                        "select_unique_plane_region_from_fresh_screenshot_with_no_modifier_keys",
                    ]
                    if viewport_selection_profile
                    else [
                        "diff_object_tree_and_isolate_exactly_one_new_miller_plane_leaf",
                        "select_leaf_by_exact_tree_item_rect_with_no_modifier_keys",
                    ]
                ),
                "verify_properties_filter_and_miller_label",
                "verify_live_recenter_view_onto_fit_numeric_mapping_against_installed_registry_order",
                "invoke_view_onto_by_verified_native_wm_command",
                "capture_fresh_screenshot_before_cleanup",
                *(
                    ["verify_direct_lattice_direction_matches_collinear_plane_normal"]
                    if direction_via_miller_plane
                    else []
                ),
                "verify_plane_normal_and_report_native_in_plane_roll_separately",
                "undo_only_whitelisted_view_onto_then_create_miller_plane_actions",
                "verify_document_clean_viewport_pixel_exactly_restored_and_sha256_unchanged",
                "record_view_replay_event_with_miller_plane_evidence",
            ],
            "safety_notes": [
                (
                    "Do not reuse viewport coordinates; derive the unique transient-plane hit region from fresh before/after screenshots and verify the result in Properties Explorer."
                    if viewport_selection_profile
                    else "Do not use blind viewport coordinates; derive the click rectangle from the exact Object Tree item."
                ),
                "Do not click Tools > Miller Planes with a pointer or accessibility click; use Alt+T then M.",
                "Do not invoke Reset View. Materials Studio 20.1 did not expose a reliable Undo Reset View for the verified live document; restore the pre-action camera only by undoing View Onto.",
                "Target CmdCreate and the dialog close control only from a fresh modeless child-window state; reject parent-window coordinates and accessibility elements outside the child bounds.",
                "Do not assume Ctrl+A replaced TxtHKL. Prefer exact set_value. From any fresh readback, first repair a verified affix relation, a differing suffix, or preserve the longest common contiguous substring while deleting only the prefix before it. Wait the recipe navigation delay after Home or End, never batch repeated Backspace/Delete events, and wait the recipe interpress delay between them. Wait the post-mutation delay before each fresh readback and replan after every mutation. Use at most one observed-count full replacement; after it, only relation-based repairs remain allowed and an unrelated value must abort. Invoke Create only when the trimmed text exactly matches dialog_miller_indices_text.",
                "If a default plane is created during dialog invocation, use only the exact named Undo Create Miller Plane action, verify cleanup, and abort this replay attempt.",
                "Do not hold Shift or Ctrl while selecting or invoking View Onto.",
                "Do not claim the analytic camera-up/right basis matched when MS used its native smallest-acute-angle roll.",
                *(
                    [
                        "Do not generalize [uvw] as (hkl); this recipe is valid only for the exact collinearity mapping persisted in the manifest."
                    ]
                    if direction_via_miller_plane
                    else []
                ),
                "Undo View Onto and Create Miller Plane in exact stack order; stop before any label outside the explicit whitelist and require exact viewport-pixel restoration.",
            ],
            "installed_evidence": {
                **evidence_requirements,
                "symmetry_builder_registry_path": command_evidence.get(
                    "symmetry_builder_registry_path"
                ),
                "tree_explorer_registry_path": command_evidence.get(
                    "tree_explorer_registry_path"
                ),
                "tree_explorer_component_path": command_evidence.get(
                    "tree_explorer_component_path"
                ),
                "tree_explorer_component_hidden": command_evidence.get(
                    "tree_explorer_component_hidden"
                ),
                "properties_explorer_registry_path": command_evidence.get(
                    "properties_explorer_registry_path"
                ),
                "explorers_help_path": command_evidence.get("explorers_help_path"),
                "public_explorer_inventory_verified": command_evidence.get(
                    "public_explorer_inventory_verified"
                ),
                "public_explorer_inventory_excludes_tree": command_evidence.get(
                    "public_explorer_inventory_excludes_tree"
                ),
                "project_explorer_help_path": command_evidence.get(
                    "project_explorer_help_path"
                ),
                "project_explorer_documents_only_verified": command_evidence.get(
                    "project_explorer_documents_only_verified"
                ),
                "miller_plane_create_help_path": command_evidence.get(
                    "miller_plane_create_help_path"
                ),
                "miller_plane_working_help_path": command_evidence.get(
                    "miller_plane_working_help_path"
                ),
                "positioning_help_path": command_evidence.get("positioning_help_path"),
            },
            "index_validation_error": index_error,
            "block_reasons": block_reasons,
        }

    if crystallography:
        candidate_ids = [
            command_id
            for command_id in (
                "cmdViewer3DViewOnto",
                "cmdViewer3DViewAcrossHorizontal",
            )
            if command_id in command_ids
        ]
        crystallographic_block_reasons = [
            "deterministic_gui_plane_or_direction_selection_not_available",
            "arbitrary_camera_vector_materialscript_api_not_verified",
        ]
        if crystallography.get("crystal_direction_indices") is not None:
            crystallographic_block_reasons.insert(
                0,
                "direct_lattice_direction_has_no_exact_collinear_integer_miller_plane_recipe",
            )
        return {
            **base,
            "status": "deterministic_selection_recipe_required",
            "automation_ready": False,
            "allowed_native_command_ids": candidate_ids,
            "candidate_native_command_ids": candidate_ids,
            "selection_required": True,
            "selection_semantics": (
                "Select an exact crystallographic plane/direction before invoking View Onto or View Across."
            ),
            "direction_plane_mapping": direction_plane_mapping or None,
            "block_reasons": crystallographic_block_reasons,
        }

    if view_name in STANDARD_CARTESIAN_VIEW_NAMES:
        return {
            **base,
            "status": "reviewed_camera_backend_required",
            "automation_ready": False,
            "allowed_native_command_ids": [],
            "selection_required": False,
            "block_reasons": [
                "no_registered_native_command_for_requested_standard_view",
                "arbitrary_camera_vector_materialscript_api_not_verified",
                "continuous_spin_roll_rock_not_deterministic",
            ],
        }

    return {
        **base,
        "status": "reviewed_camera_backend_required",
        "automation_ready": False,
        "allowed_native_command_ids": [],
        "block_reasons": [
            "no_verified_execution_recipe_for_view",
            "arbitrary_camera_vector_materialscript_api_not_verified",
        ],
    }


def _parse_view_toolbar_registry(
    registry_bytes: bytes,
) -> tuple[list[dict[str, Any]], str | None]:
    """Parse allowlisted toolbar order from one installed command registry."""

    try:
        root = ET.fromstring(registry_bytes)
    except ET.ParseError as exc:
        return [], str(exc)

    layouts: list[dict[str, Any]] = []
    allowlisted_names = {
        str(contract["registry_toolbar_name"])
        for contract in VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS.values()
    }
    for node in root.iter():
        tag = str(node.tag).rsplit("}", 1)[-1].upper()
        if tag != "TOOLBAR":
            continue
        registry_toolbar_name = str(node.attrib.get("NAME") or "").strip()
        if registry_toolbar_name not in allowlisted_names:
            continue
        entries: list[dict[str, Any]] = []
        for child in list(node):
            child_tag = str(child.tag).rsplit("}", 1)[-1].upper()
            if child_tag == "TOOL":
                entries.append(
                    {
                        "kind": "tool",
                        "command_id": str(child.attrib.get("NAME") or "").strip() or None,
                    }
                )
            elif child_tag == "SEPARATOR":
                entries.append({"kind": "separator", "command_id": None})
        layouts.append(
            {
                "registry_toolbar_name": registry_toolbar_name,
                "title": str(node.attrib.get("TITLE") or "").strip(),
                "entries": entries,
            }
        )
    return layouts, None


def _materials_studio_view_command_evidence() -> dict[str, Any]:
    """Return installed command-registry evidence when Materials Studio is present."""

    registry_path: Path | None = None
    symmetry_builder_registry_path: Path | None = None
    tree_explorer_registry_path: Path | None = None
    tree_explorer_component_path: Path | None = None
    properties_explorer_registry_path: Path | None = None
    explorers_help_path: Path | None = None
    project_explorer_help_path: Path | None = None
    keyboard_help_path: Path | None = None
    movement_help_path: Path | None = None
    miller_plane_create_help_path: Path | None = None
    miller_plane_working_help_path: Path | None = None
    positioning_help_path: Path | None = None
    registry_sha256: str | None = None
    registry_toolbar_layouts: list[dict[str, Any]] = []
    registry_toolbar_parse_error: str | None = None
    registered_view_command_ids: list[str] = []
    unmodified_arrow_keys_rotate_view = False
    shift_arrow_keys_rotate_selected_objects = False
    arrow_rotation_angle_user_configurable = False
    movement_options_command_registered = False
    movement_dialog_angle_supported = False
    miller_plane_command_registered = False
    tree_explorer_command_registered = False
    tree_explorer_component_hidden = False
    properties_explorer_command_registered = False
    miller_plane_create_workflow_verified = False
    miller_plane_selection_view_onto_workflow_verified = False
    object_tree_hierarchy_help_verified = False
    viewport_miller_plane_selection_properties_workflow_verified = False
    public_explorer_inventory_verified = False
    public_explorer_inventory_excludes_tree = False
    project_explorer_documents_only_verified = False
    native_view_roll_policy_documented = False
    matstudio_exe = _resolve_matstudio_exe()
    if matstudio_exe is not None:
        install_root = matstudio_exe.parent.parent
        share_root = install_root / "share"
        candidate = (share_root / "Commands" / "#SVViewer3d.xml").resolve()
        if candidate.exists() and candidate.is_file():
            registry_path = candidate
            try:
                registry_bytes = candidate.read_bytes()
            except OSError:
                registry_bytes = b""
            registry_sha256 = (
                hashlib.sha256(registry_bytes).hexdigest() if registry_bytes else None
            )
            if registry_bytes:
                registry_toolbar_layouts, registry_toolbar_parse_error = (
                    _parse_view_toolbar_registry(registry_bytes)
                )
                registry_text = registry_bytes.decode("utf-8", errors="replace")
            else:
                registry_text = ""
            registered_view_command_ids = [
                str(command["command_id"])
                for command in MATERIALS_STUDIO_2020_VIEW_COMMANDS
                if str(command["command_id"]) in registry_text
            ]
            movement_options_command_registered = "cmdViewer3DMovementOptions" in registry_text

        symmetry_candidate = (share_root / "Commands" / "SMPSymmetryBuilderMenu.xml").resolve()
        if symmetry_candidate.exists() and symmetry_candidate.is_file():
            symmetry_builder_registry_path = symmetry_candidate
            try:
                symmetry_text = symmetry_candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                symmetry_text = ""
            miller_plane_command_registered = "cmdSymmetryBuilderMillerPlanes" in symmetry_text

        tree_candidate = (share_root / "Commands" / "SMTreeExplorer.xml").resolve()
        if tree_candidate.exists() and tree_candidate.is_file():
            tree_explorer_registry_path = tree_candidate
            try:
                tree_text = tree_candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                tree_text = ""
            tree_explorer_command_registered = "cmdTEToggleExplorer" in tree_text

        tree_component_candidate = (
            share_root / "Components" / "SMTreeExplorer.xml"
        ).resolve()
        if tree_component_candidate.exists() and tree_component_candidate.is_file():
            tree_explorer_component_path = tree_component_candidate
            try:
                tree_component_text = tree_component_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                tree_component_text = ""
            tree_explorer_component_hidden = all(
                marker in tree_component_text
                for marker in ('NAME="Object Tree"', 'HIDDEN="Yes"')
            )

        properties_candidate = (share_root / "Commands" / "SMGenPropEditor.xml").resolve()
        if properties_candidate.exists() and properties_candidate.is_file():
            properties_explorer_registry_path = properties_candidate
            try:
                properties_text = properties_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                properties_text = ""
            properties_explorer_command_registered = "cmdGPEToggleExplorer" in properties_text

        explorers_help_candidate = (
            share_root / "doc" / "content" / "core" / "interface" / "explorers.htm"
        ).resolve()
        if explorers_help_candidate.exists() and explorers_help_candidate.is_file():
            explorers_help_path = explorers_help_candidate
            try:
                explorers_help_text = explorers_help_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                explorers_help_text = ""
            normalized_explorers_help = re.sub(r"\s+", " ", explorers_help_text).lower()
            public_explorer_inventory_verified = all(
                phrase in normalized_explorers_help
                for phrase in ("project explorer", "properties explorer", "job explorer")
            )
            public_explorer_inventory_excludes_tree = bool(
                public_explorer_inventory_verified
                and "tree explorer" not in normalized_explorers_help
                and "object tree" not in normalized_explorers_help
            )

        project_explorer_help_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "interface"
            / "projectexplorer.htm"
        ).resolve()
        if project_explorer_help_candidate.exists() and project_explorer_help_candidate.is_file():
            project_explorer_help_path = project_explorer_help_candidate
            try:
                project_explorer_help_text = project_explorer_help_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                project_explorer_help_text = ""
            normalized_project_explorer_help = re.sub(
                r"\s+",
                " ",
                project_explorer_help_text,
            ).lower()
            project_explorer_documents_only_verified = all(
                phrase in normalized_project_explorer_help
                for phrase in (
                    "access the documents associated with a project",
                    "project documents and folders",
                )
            )

        help_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "interface"
            / "mouseandkeyboardactions.htm"
        ).resolve()
        if help_candidate.exists() and help_candidate.is_file():
            keyboard_help_path = help_candidate
            try:
                help_text = help_candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                help_text = ""
            normalized_help = re.sub(r"\s+", " ", help_text).lower()
            unmodified_arrow_keys_rotate_view = all(
                phrase in normalized_help
                for phrase in (
                    "rotate view about x 45",
                    ">up arrow<",
                    ">down arrow<",
                    "rotate view about y 45",
                    ">left arrow<",
                    ">right arrow<",
                )
            )
            shift_arrow_keys_rotate_selected_objects = all(
                phrase in normalized_help
                for phrase in (
                    "rotate selected objects about x 45",
                    "shift + down arrow",
                    "shift + up arrow",
                    "rotate selected objects about y 45",
                    "shift + right arrow",
                    "shift + left arrow",
                )
            )
            arrow_rotation_angle_user_configurable = (
                "arrow key rotation angle can be set using the" in normalized_help
                and "movement dialog" in normalized_help
            )
        movement_help_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "sketching"
            / "dlgmovement.htm"
        ).resolve()
        if movement_help_candidate.exists() and movement_help_candidate.is_file():
            movement_help_path = movement_help_candidate
            try:
                movement_help_text = movement_help_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                movement_help_text = ""
            normalized_movement_help = re.sub(r"\s+", " ", movement_help_text).lower()
            movement_dialog_angle_supported = all(
                phrase in normalized_movement_help
                for phrase in (
                    "movement dialog",
                    "angular displacement rate",
                    "default",
                    "45",
                )
            )

        create_help_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "sketching"
            / "tskmillerplanes_create.htm"
        ).resolve()
        if create_help_candidate.exists() and create_help_candidate.is_file():
            miller_plane_create_help_path = create_help_candidate
            try:
                create_help_text = create_help_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                create_help_text = ""
            normalized_create_help = re.sub(r"\s+", " ", create_help_text).lower()
            miller_plane_create_workflow_verified = all(
                phrase in normalized_create_help
                for phrase in (
                    "tools | miller planes",
                    "miller indices (h k l)",
                    "click the <span class=\"uif\">create</span> button",
                )
            )

        working_help_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "sketching"
            / "tskmillerplanes_working.htm"
        ).resolve()
        if working_help_candidate.exists() and working_help_candidate.is_file():
            miller_plane_working_help_path = working_help_candidate
            try:
                working_help_text = working_help_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                working_help_text = ""
            normalized_working_help = re.sub(r"\s+", " ", working_help_text).lower()
            normalized_working_plain_help = re.sub(
                r"\s+",
                " ",
                re.sub(r"(?s)<[^>]+>", " ", working_help_text),
            ).lower()
            miller_plane_selection_view_onto_workflow_verified = all(
                phrase in normalized_working_help
                for phrase in (
                    "select a single miller plane",
                    "parallel to the screen",
                    "3d viewer recenter",
                    "view onto",
                )
            )
            viewport_miller_plane_selection_properties_workflow_verified = all(
                phrase in normalized_working_plain_help
                for phrase in (
                    "select a single miller plane",
                    "select miller plane from the filter dropdown list in the properties explorer",
                    "options arrow associated with the 3d viewer recenter button",
                    "select view onto from the dropdown list",
                )
            )
            object_tree_hierarchy_help_verified = all(
                phrase in normalized_working_help
                for phrase in (
                    "miller parallel planes",
                    "miller family",
                )
            )

        positioning_candidate = (
            share_root
            / "doc"
            / "content"
            / "core"
            / "viewers"
            / "settingpositionandorientation.htm"
        ).resolve()
        if positioning_candidate.exists() and positioning_candidate.is_file():
            positioning_help_path = positioning_candidate
            try:
                positioning_text = positioning_candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                positioning_text = ""
            normalized_positioning_help = re.sub(r"\s+", " ", positioning_text).lower()
            native_view_roll_policy_documented = all(
                phrase in normalized_positioning_help
                for phrase in (
                    "view onto",
                    "smallest, acute, angle is used",
                    "initial orientation",
                )
            )
    return {
        "version_basis": "Materials Studio 2020/20.1 local installation",
        "registry_path": str(registry_path) if registry_path is not None else None,
        "registry_found": registry_path is not None,
        "registry_sha256": registry_sha256,
        "registry_toolbar_layouts": registry_toolbar_layouts,
        "registry_toolbar_parse_error": registry_toolbar_parse_error,
        "commands": [dict(command) for command in MATERIALS_STUDIO_2020_VIEW_COMMANDS],
        "registered_view_command_ids": registered_view_command_ids,
        "symmetry_builder_registry_path": (
            str(symmetry_builder_registry_path)
            if symmetry_builder_registry_path is not None
            else None
        ),
        "symmetry_builder_registry_found": symmetry_builder_registry_path is not None,
        "miller_plane_command_id": "cmdSymmetryBuilderMillerPlanes",
        "miller_plane_command_registered": miller_plane_command_registered,
        "tree_explorer_registry_path": (
            str(tree_explorer_registry_path) if tree_explorer_registry_path is not None else None
        ),
        "tree_explorer_registry_found": tree_explorer_registry_path is not None,
        "tree_explorer_command_id": "cmdTEToggleExplorer",
        "tree_explorer_command_registered": tree_explorer_command_registered,
        "tree_explorer_component_path": (
            str(tree_explorer_component_path)
            if tree_explorer_component_path is not None
            else None
        ),
        "tree_explorer_component_hidden": tree_explorer_component_hidden,
        "properties_explorer_registry_path": (
            str(properties_explorer_registry_path)
            if properties_explorer_registry_path is not None
            else None
        ),
        "properties_explorer_registry_found": properties_explorer_registry_path is not None,
        "properties_explorer_command_id": "cmdGPEToggleExplorer",
        "properties_explorer_command_registered": properties_explorer_command_registered,
        "explorers_help_path": (
            str(explorers_help_path) if explorers_help_path is not None else None
        ),
        "public_explorer_inventory_verified": public_explorer_inventory_verified,
        "public_explorer_inventory_excludes_tree": public_explorer_inventory_excludes_tree,
        "project_explorer_help_path": (
            str(project_explorer_help_path)
            if project_explorer_help_path is not None
            else None
        ),
        "project_explorer_documents_only_verified": project_explorer_documents_only_verified,
        "keyboard_help_path": str(keyboard_help_path) if keyboard_help_path is not None else None,
        "keyboard_help_found": keyboard_help_path is not None,
        "unmodified_arrow_keys_rotate_view": unmodified_arrow_keys_rotate_view,
        "default_arrow_rotation_increment_degrees": (
            45 if unmodified_arrow_keys_rotate_view else None
        ),
        "arrow_rotation_angle_user_configurable": arrow_rotation_angle_user_configurable,
        "shift_arrow_keys_rotate_selected_objects": shift_arrow_keys_rotate_selected_objects,
        "shift_arrow_keys_prohibited_for_view_replay": True,
        "movement_help_path": str(movement_help_path) if movement_help_path is not None else None,
        "movement_help_found": movement_help_path is not None,
        "movement_options_command_id": "cmdViewer3DMovementOptions",
        "movement_options_command_registered": movement_options_command_registered,
        "movement_dialog_angle_supported": movement_dialog_angle_supported,
        "movement_angle_control_id": "numNudgeAngle",
        "movement_screen_factor_control_id": "numNudgeFactor",
        "movement_screen_factor_expected": 2.0,
        "miller_plane_create_help_path": (
            str(miller_plane_create_help_path) if miller_plane_create_help_path is not None else None
        ),
        "miller_plane_create_help_found": miller_plane_create_help_path is not None,
        "miller_plane_create_workflow_verified": miller_plane_create_workflow_verified,
        "miller_plane_working_help_path": (
            str(miller_plane_working_help_path)
            if miller_plane_working_help_path is not None
            else None
        ),
        "miller_plane_working_help_found": miller_plane_working_help_path is not None,
        "miller_plane_selection_view_onto_workflow_verified": (
            miller_plane_selection_view_onto_workflow_verified
        ),
        "viewport_miller_plane_selection_properties_workflow_verified": (
            viewport_miller_plane_selection_properties_workflow_verified
        ),
        "object_tree_hierarchy_help_verified": object_tree_hierarchy_help_verified,
        "positioning_help_path": (
            str(positioning_help_path) if positioning_help_path is not None else None
        ),
        "positioning_help_found": positioning_help_path is not None,
        "native_view_roll_policy_documented": native_view_roll_policy_documented,
        "miller_plane_selection_method": MILLER_PLANE_SELECTION_METHOD,
        "miller_plane_selection_methods": sorted(MILLER_PLANE_SELECTION_METHODS),
        "miller_plane_viewport_hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
        "miller_plane_object_tree_path_suffix": list(MILLER_PLANE_OBJECT_TREE_PATH_SUFFIX),
        "scope": "reviewed_gui_command_evidence_only",
        "arbitrary_camera_vector_api_confirmed": False,
    }


def _expected_view_replay_recipe_kind(
    view: Any,
    *,
    model_type: str | None = None,
) -> str | None:
    """Derive safety-sensitive recipe kind from the prepared view definition."""

    if not isinstance(view, dict):
        return None
    crystallography = (
        view.get("crystallography")
        if isinstance(view.get("crystallography"), dict)
        else {}
    )
    if crystallography.get("crystal_plane_indices") is not None:
        return "miller_plane_view_onto"
    direction_mapping = (
        crystallography.get("crystal_direction_view_onto_plane_mapping")
        if isinstance(
            crystallography.get("crystal_direction_view_onto_plane_mapping"),
            dict,
        )
        else {}
    )
    if (
        crystallography.get("crystal_direction_indices") is not None
        and direction_mapping.get("status") == "exact_integer_plane_collinear"
        and direction_mapping.get("automation_eligible") is True
        and direction_mapping.get("miller_plane_indices") is not None
    ):
        return "crystal_direction_via_collinear_miller_plane_view_onto"
    if (
        model_type == "crystal"
        and str(view.get("view_name") or "") in STANDARD_CARTESIAN_VIEW_NAMES
    ):
        return CRYSTAL_STANDARD_VIEW_RECIPE_KIND
    return None


def _view_replay_recipe_contract_status(
    recipe: Any,
    *,
    expected_recipe_kind: str | None = None,
) -> dict[str, Any]:
    """Return whether one persisted recipe matches this runtime's safety contract."""

    if not isinstance(recipe, dict):
        expected_schema_version = (
            MILLER_VIEW_ONTO_RECIPE_SCHEMA_VERSION
            if expected_recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS
            else CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION
            if expected_recipe_kind == CRYSTAL_STANDARD_VIEW_RECIPE_KIND
            else VIEW_REPLAY_BASE_RECIPE_SCHEMA_VERSION
        )
        return {
            "status": "missing",
            "current": False,
            "recording_allowed": False,
            "recipe_kind": None,
            "expected_recipe_kind": expected_recipe_kind,
            "actual_schema_version": None,
            "expected_schema_version": expected_schema_version,
            "reasons": ["execution_recipe_missing"],
        }

    recipe_kind = str(recipe.get("recipe_kind") or "") or None
    effective_recipe_kind = expected_recipe_kind or recipe_kind
    if effective_recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS:
        expected_schema_version = MILLER_VIEW_ONTO_RECIPE_SCHEMA_VERSION
    elif effective_recipe_kind == CRYSTAL_STANDARD_VIEW_RECIPE_KIND:
        expected_schema_version = CRYSTAL_STANDARD_VIEW_RECIPE_SCHEMA_VERSION
    elif isinstance(recipe.get("keyboard_stages"), list):
        expected_schema_version = VIEW_REPLAY_STAGED_KEYBOARD_RECIPE_SCHEMA_VERSION
    else:
        expected_schema_version = VIEW_REPLAY_BASE_RECIPE_SCHEMA_VERSION

    raw_schema_version = recipe.get("schema_version")
    actual_schema_version = (
        raw_schema_version
        if isinstance(raw_schema_version, int) and not isinstance(raw_schema_version, bool)
        else None
    )
    reasons: list[str] = []
    if expected_recipe_kind is not None and recipe_kind != expected_recipe_kind:
        reasons.append("execution_recipe_kind_does_not_match_view_definition")
    if actual_schema_version is None:
        reasons.append("execution_recipe_schema_missing_or_invalid")
    elif actual_schema_version < expected_schema_version:
        reasons.append("execution_recipe_schema_outdated")
    elif actual_schema_version > expected_schema_version:
        reasons.append("execution_recipe_schema_newer_than_runtime")

    if effective_recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS:
        dialog_contract = (
            recipe.get("dialog_index_entry_contract")
            if isinstance(recipe.get("dialog_index_entry_contract"), dict)
            else {}
        )
        correction_contract = (
            dialog_contract.get("keyboard_correction_contract")
            if isinstance(dialog_contract.get("keyboard_correction_contract"), dict)
            else {}
        )
        expected_timing = {
            "navigation_key_settle_delay_milliseconds": (
                MILLER_DIALOG_NAVIGATION_KEY_SETTLE_DELAY_MILLISECONDS
            ),
            "repeated_key_interpress_delay_milliseconds": (
                MILLER_DIALOG_REPEATED_KEY_INTERPRESS_DELAY_MILLISECONDS
            ),
            "post_mutation_readback_delay_milliseconds": (
                MILLER_DIALOG_POST_MUTATION_READBACK_DELAY_MILLISECONDS
            ),
            "first_destructive_key_must_wait_after_navigation": True,
            "batch_repeated_backspace_or_delete_allowed": False,
            "fresh_child_readback_required_after_each_mutation": True,
        }
        timing_mismatches = [
            field_name
            for field_name, expected_value in expected_timing.items()
            if correction_contract.get(field_name) != expected_value
        ]
        if timing_mismatches:
            reasons.append("miller_dialog_keyboard_timing_contract_outdated")
        action_sequence = {
            str(item) for item in recipe.get("action_sequence") or [] if item
        }
        missing_timing_actions = sorted(
            MILLER_DIALOG_REQUIRED_TIMING_ACTIONS - action_sequence
        )
        if missing_timing_actions:
            reasons.append("miller_dialog_keyboard_timing_actions_missing")
        runtime_accessibility = (
            recipe.get("runtime_accessibility_preflight")
            if isinstance(recipe.get("runtime_accessibility_preflight"), dict)
            else {}
        )
        if recipe.get("camera_result_depends_on_reset_baseline") is not False:
            reasons.append("miller_view_onto_reset_camera_dependency_contract_missing")
        if recipe.get("camera_result_established_by") != (
            "native_miller_plane_view_onto"
        ):
            reasons.append("miller_view_onto_camera_result_basis_mismatch")
        if recipe.get("final_camera_established_by_native_command_id") != (
            "cmdViewer3DViewOnto"
        ):
            reasons.append("miller_view_onto_final_camera_command_mismatch")
        if runtime_accessibility.get("required") is not True:
            reasons.append("miller_view_onto_accessibility_preflight_missing")
        if recipe.get("pre_action_view_baseline_required") is not True:
            reasons.append("miller_view_onto_pre_action_baseline_contract_missing")
        if recipe.get("reset_view_allowed") is not False:
            reasons.append("miller_view_onto_reset_forbidden_contract_missing")
        if recipe.get("accessibility_target") is not None:
            reasons.append("miller_view_onto_reset_target_must_be_absent")
        transient_contract = (
            recipe.get("transient_change_contract")
            if isinstance(recipe.get("transient_change_contract"), dict)
            else {}
        )
        if transient_contract.get("required_undo_labels") != [
            "Undo View Onto Miller Plane",
            "Undo Create Miller Plane",
        ]:
            reasons.append("miller_view_onto_two_step_cleanup_contract_missing")
        if transient_contract.get("require_exact_viewport_pixel_restoration") is not True:
            reasons.append("miller_view_onto_exact_restoration_contract_missing")
        invocation = (
            recipe.get("view_command_invocation")
            if isinstance(recipe.get("view_command_invocation"), dict)
            else {}
        )
        expected_numeric_mapping = {
            "selection_numeric_command_id": 33288,
            "recenter_numeric_command_id": 33296,
            "view_onto_numeric_command_id": 33297,
            "fit_numeric_command_id": 33299,
            "recenter_button_style": 10,
            "installed_registry_order_verified": True,
        }
        if any(
            invocation.get(field) != expected
            for field, expected in expected_numeric_mapping.items()
        ):
            reasons.append("miller_view_onto_native_command_mapping_contract_missing")
    else:
        timing_mismatches = []
        missing_timing_actions = []
        view_name = str(recipe.get("view_name") or "")
        if view_name == "front" or view_name in DOCUMENTED_VIEW_KEY_RECIPES:
            if recipe.get("camera_result_depends_on_reset_baseline") is not True:
                reasons.append("standard_view_reset_camera_dependency_contract_missing")
            expected_basis = (
                "native_reset_view"
                if view_name == "front"
                else "reset_plus_staged_unmodified_keyboard_recipe"
                if isinstance(recipe.get("keyboard_stages"), list)
                else "reset_plus_unmodified_keyboard_recipe"
            )
            if recipe.get("camera_result_established_by") != expected_basis:
                reasons.append("standard_view_camera_result_basis_mismatch")
        if effective_recipe_kind == CRYSTAL_STANDARD_VIEW_RECIPE_KIND:
            camera_contract = (
                recipe.get("camera_match_contract")
                if isinstance(recipe.get("camera_match_contract"), dict)
                else {}
            )
            record_contract = (
                recipe.get("required_record_evidence")
                if isinstance(recipe.get("required_record_evidence"), dict)
                else {}
            )
            if camera_contract.get("scope") != CRYSTAL_STANDARD_VIEW_CAMERA_MATCH_SCOPE:
                reasons.append("crystal_standard_view_camera_match_scope_missing")
            if camera_contract.get("required_view_direction_match") is not True:
                reasons.append("crystal_standard_view_direction_match_contract_missing")
            if camera_contract.get("required_analytic_camera_up_match") is not False:
                reasons.append("crystal_standard_view_native_roll_up_contract_missing")
            if camera_contract.get("required_analytic_camera_right_match") is not False:
                reasons.append("crystal_standard_view_native_roll_right_contract_missing")
            if record_contract.get("field") != "crystal_camera_evidence":
                reasons.append("crystal_standard_view_record_evidence_contract_missing")
            if record_contract.get("fresh_workspace_screenshot_required") is not True:
                reasons.append("crystal_standard_view_screenshot_contract_missing")

    reasons = _unique_strings(reasons)
    current = not reasons
    return {
        "status": "current" if current else "upgrade_required",
        "current": current,
        "recording_allowed": current,
        "recipe_kind": recipe_kind,
        "expected_recipe_kind": expected_recipe_kind,
        "actual_schema_version": actual_schema_version,
        "expected_schema_version": expected_schema_version,
        "timing_mismatch_fields": timing_mismatches,
        "missing_timing_actions": missing_timing_actions,
        "reasons": reasons,
    }


def _sha256_file(path: Path) -> tuple[str, int]:
    """Return a streaming SHA-256 digest and byte count for one artifact."""

    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _immutable_evidence_integrity_payload(value: Any) -> dict[str, Any] | None:
    """Return only the recorded fields used by the immutable event digest."""

    if not isinstance(value, dict):
        return None
    artifacts = []
    for item in value.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        artifacts.append(
            {
                key: item.get(key)
                for key in (
                    "kind",
                    "path",
                    "required",
                    "recorded_sha256",
                    "recorded_byte_count",
                )
            }
        )
    artifacts.sort(key=lambda item: str(item.get("kind") or ""))
    return {
        "schema_version": value.get("schema_version"),
        "algorithm": value.get("algorithm"),
        "policy": value.get("policy"),
        "strict": value.get("strict"),
        "required_artifact_kinds": sorted(
            str(item) for item in value.get("required_artifact_kinds") or []
        ),
        "artifacts": artifacts,
    }


def _view_replay_event_record_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Build the stable event payload shared by manifest and JSONL copies."""

    payload = {
        key: value
        for key, value in event.items()
        if key
        not in {
            "event_record_sha256",
            "journal_consistency",
        }
    }
    if "evidence_integrity" in payload:
        payload["evidence_integrity"] = _immutable_evidence_integrity_payload(
            payload.get("evidence_integrity")
        )
    return payload


def _view_replay_event_record_sha256(event: dict[str, Any]) -> str:
    canonical = json.dumps(
        _view_replay_event_record_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_view_replay_event_journal(
    events_path: Path | None,
    *,
    workspace_root: Path | None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Read the bounded append-only journal without trusting its event payloads."""

    if events_path is None or workspace_root is None:
        return (
            {
                "status": "verification_unavailable",
                "path": str(events_path) if events_path is not None else None,
                "exists": bool(events_path and events_path.exists()),
                "event_count": 0,
                "physical_line_count": 0,
                "invalid_line_numbers": [],
                "duplicate_event_ids": [],
                "read_error": "workspace_root_or_events_path_unavailable",
            },
            {},
        )
    try:
        resolved = events_path.expanduser().resolve()
        _ensure_inside(workspace_root, resolved)
    except (GuiError, OSError, RuntimeError, ValueError) as exc:
        return (
            {
                "status": "invalid_path",
                "path": str(events_path),
                "exists": False,
                "event_count": 0,
                "physical_line_count": 0,
                "invalid_line_numbers": [],
                "duplicate_event_ids": [],
                "read_error": str(exc),
            },
            {},
        )
    if not resolved.exists():
        return (
            {
                "status": "missing",
                "path": str(resolved),
                "exists": False,
                "event_count": 0,
                "physical_line_count": 0,
                "invalid_line_numbers": [],
                "duplicate_event_ids": [],
                "read_error": None,
            },
            {},
        )
    try:
        size_bytes = resolved.stat().st_size
    except OSError as exc:
        return (
            {
                "status": "unreadable",
                "path": str(resolved),
                "exists": True,
                "event_count": 0,
                "physical_line_count": 0,
                "invalid_line_numbers": [],
                "duplicate_event_ids": [],
                "read_error": str(exc),
            },
            {},
        )
    if size_bytes > VIEW_REPLAY_EVENT_JOURNAL_MAX_BYTES:
        return (
            {
                "status": "oversized",
                "path": str(resolved),
                "exists": True,
                "size_bytes": size_bytes,
                "event_count": 0,
                "physical_line_count": 0,
                "invalid_line_numbers": [],
                "duplicate_event_ids": [],
                "read_error": None,
            },
            {},
        )

    events_by_id: dict[str, list[dict[str, Any]]] = {}
    invalid_line_numbers: list[int] = []
    invalid_line_count = 0
    physical_line_count = 0
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                physical_line_count = line_number
                if line_number > VIEW_REPLAY_EVENT_JOURNAL_MAX_LINES:
                    return (
                        {
                            "status": "too_many_lines",
                            "path": str(resolved),
                            "exists": True,
                            "size_bytes": size_bytes,
                            "event_count": sum(len(items) for items in events_by_id.values()),
                            "physical_line_count": physical_line_count,
                            "invalid_line_count": invalid_line_count,
                            "invalid_line_numbers": invalid_line_numbers,
                            "invalid_line_numbers_truncated": (
                                invalid_line_count > len(invalid_line_numbers)
                            ),
                            "duplicate_event_ids": sorted(
                                event_id
                                for event_id, items in events_by_id.items()
                                if len(items) > 1
                            )[:VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES],
                            "read_error": None,
                        },
                        events_by_id,
                    )
                if not line.strip():
                    invalid_line_count += 1
                    if (
                        len(invalid_line_numbers)
                        < VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
                    ):
                        invalid_line_numbers.append(line_number)
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    invalid_line_count += 1
                    if (
                        len(invalid_line_numbers)
                        < VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
                    ):
                        invalid_line_numbers.append(line_number)
                    continue
                if not isinstance(item, dict) or not item.get("event_id"):
                    invalid_line_count += 1
                    if (
                        len(invalid_line_numbers)
                        < VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
                    ):
                        invalid_line_numbers.append(line_number)
                    continue
                events_by_id.setdefault(str(item["event_id"]), []).append(item)
    except (OSError, UnicodeError) as exc:
        return (
            {
                "status": "unreadable",
                "path": str(resolved),
                "exists": True,
                "size_bytes": size_bytes,
                "event_count": sum(len(items) for items in events_by_id.values()),
                "physical_line_count": physical_line_count,
                "invalid_line_count": invalid_line_count,
                "invalid_line_numbers": invalid_line_numbers,
                "invalid_line_numbers_truncated": (
                    invalid_line_count > len(invalid_line_numbers)
                ),
                "duplicate_event_ids": [],
                "read_error": str(exc),
            },
            events_by_id,
        )

    all_duplicate_event_ids = sorted(
        event_id for event_id, items in events_by_id.items() if len(items) > 1
    )
    duplicate_event_ids = all_duplicate_event_ids[
        :VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
    ]
    return (
        {
            "status": (
                "invalid_lines"
                if invalid_line_count
                else "duplicate_event_ids"
                if duplicate_event_ids
                else "loaded"
            ),
            "path": str(resolved),
            "exists": True,
            "size_bytes": size_bytes,
            "event_count": sum(len(items) for items in events_by_id.values()),
            "physical_line_count": physical_line_count,
            "invalid_line_count": invalid_line_count,
            "invalid_line_numbers": invalid_line_numbers,
            "invalid_line_numbers_truncated": (
                invalid_line_count > len(invalid_line_numbers)
            ),
            "duplicate_event_id_count": len(all_duplicate_event_ids),
            "duplicate_event_ids": duplicate_event_ids,
            "read_error": None,
        },
        events_by_id,
    )


def _audit_view_replay_event_journal_consistency(
    event: dict[str, Any],
    *,
    journal_events_by_id: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compare one manifest event with its independent JSONL journal copy."""

    accepted = event.get("accepted") is True
    integrity = (
        event.get("evidence_integrity")
        if isinstance(event.get("evidence_integrity"), dict)
        else {}
    )
    stored_digest = event.get("event_record_sha256")
    event_id = str(event.get("event_id") or "")
    manifest_digest = _view_replay_event_record_sha256(event)
    candidates = journal_events_by_id.get(event_id, []) if event_id else []
    required = bool(
        event.get("source") == "reviewed_copy_script"
        or integrity.get("strict") is True
        or event.get("event_record_schema_version")
        == VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION
        or stored_digest
        or any(
            candidate.get("event_record_schema_version")
            == VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION
            or candidate.get("event_record_sha256")
            for candidate in candidates
        )
    )
    issue_codes: list[str] = []
    journal_digest: str | None = None
    journal_stored_digest: str | None = None
    if not event_id:
        issue_codes.append("manifest_event_id_missing")
    if (
        event.get("event_record_schema_version")
        not in {None, VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION}
    ):
        issue_codes.append("manifest_event_record_schema_invalid")
    if isinstance(stored_digest, str):
        if not re.fullmatch(r"[0-9a-f]{64}", stored_digest):
            issue_codes.append("manifest_event_record_sha256_invalid")
        elif stored_digest != manifest_digest:
            issue_codes.append("manifest_event_record_sha256_mismatch")
    elif (
        event.get("event_record_schema_version")
        == VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION
    ):
        issue_codes.append("manifest_event_record_sha256_missing")

    if not candidates:
        if required:
            issue_codes.append("journal_event_missing")
    elif len(candidates) > 1:
        issue_codes.append("journal_event_duplicate")
    else:
        journal_event = candidates[0]
        journal_digest = _view_replay_event_record_sha256(journal_event)
        raw_journal_stored_digest = journal_event.get("event_record_sha256")
        journal_stored_digest = (
            str(raw_journal_stored_digest)
            if raw_journal_stored_digest is not None
            else None
        )
        if (
            journal_event.get("event_record_schema_version")
            not in {None, VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION}
        ):
            issue_codes.append("journal_event_record_schema_invalid")
        if journal_stored_digest is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", journal_stored_digest):
                issue_codes.append("journal_event_record_sha256_invalid")
            elif journal_stored_digest != journal_digest:
                issue_codes.append("journal_event_record_sha256_mismatch")
        if journal_digest != manifest_digest:
            issue_codes.append("manifest_journal_event_digest_mismatch")
        if (
            isinstance(stored_digest, str)
            and journal_stored_digest is not None
            and stored_digest != journal_stored_digest
        ):
            issue_codes.append("manifest_journal_stored_digest_mismatch")

    unique_issue_codes = list(dict.fromkeys(issue_codes))
    status = (
        "not_required"
        if not required
        else "matched"
        if not unique_issue_codes
        else "diverged"
    )
    return {
        "schema_version": VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION,
        "algorithm": "sha256",
        "required": required,
        "status": status,
        "event_id": event_id or None,
        "manifest_event_record_sha256": manifest_digest,
        "stored_event_record_sha256": stored_digest,
        "journal_event_record_sha256": journal_digest,
        "journal_stored_event_record_sha256": journal_stored_digest,
        "journal_match_count": len(candidates),
        "issue_codes": unique_issue_codes,
        "trusted_for_replay": bool(
            accepted and (not required or status == "matched")
        ),
    }


def _append_view_replay_event_journal(
    path: Path,
    event: dict[str, Any],
    *,
    workspace_root: Path,
) -> None:
    """Durably append one event before publishing it through the manifest."""

    resolved = path.expanduser().resolve()
    _ensure_inside(workspace_root, resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _view_replay_evidence_artifact_bindings(
    event: dict[str, Any],
) -> dict[str, str | None]:
    copy_script_evidence = (
        event.get("reviewed_copy_script_evidence")
        if isinstance(event.get("reviewed_copy_script_evidence"), dict)
        else {}
    )
    return {
        "screenshot": (
            str(event.get("screenshot_path"))
            if event.get("screenshot_path")
            else None
        ),
        "copy_script": (
            str(copy_script_evidence.get("script_path"))
            if copy_script_evidence.get("script_path")
            else None
        ),
        "copy_script_metadata": (
            str(copy_script_evidence.get("metadata_path"))
            if copy_script_evidence.get("metadata_path")
            else None
        ),
        "structure": (
            str(event.get("expected_structure_artifact_path"))
            if event.get("expected_structure_artifact_path")
            else str(copy_script_evidence.get("structure_artifact_path"))
            if copy_script_evidence.get("structure_artifact_path")
            else None
        ),
    }


def _record_view_replay_evidence_integrity(
    event: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Capture immutable digests before a replay event is persisted."""

    bindings = _view_replay_evidence_artifact_bindings(event)
    source = str(event.get("source") or "")
    required_kinds: set[str] = set()
    policy = "not_required"
    if source == "reviewed_copy_script":
        required_kinds.update(REVIEWED_COPY_SCRIPT_INTEGRITY_ARTIFACT_KINDS)
        policy = "reviewed_copy_script_strict"
    elif bindings.get("screenshot"):
        required_kinds.add("screenshot")
        if bindings.get("structure"):
            required_kinds.add("structure")
        policy = "recorded_screenshot_strict"

    artifacts: list[dict[str, Any]] = []
    for kind, path_text in bindings.items():
        if kind not in required_kinds or not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        digest: str | None = None
        byte_count: int | None = None
        try:
            _ensure_inside(workspace_root, path)
            if path.exists() and path.is_file():
                digest, byte_count = _sha256_file(path)
        except (GuiError, OSError, RuntimeError, ValueError):
            digest = None
            byte_count = None
        artifacts.append(
            {
                "kind": kind,
                "path": str(path),
                "required": kind in required_kinds,
                "recorded_sha256": digest,
                "recorded_byte_count": byte_count,
            }
        )

    captured = {
        "schema_version": VIEW_REPLAY_EVIDENCE_INTEGRITY_SCHEMA_VERSION,
        "algorithm": VIEW_REPLAY_EVIDENCE_INTEGRITY_ALGORITHM,
        "policy": policy,
        "strict": bool(required_kinds),
        "required_artifact_kinds": sorted(required_kinds),
        "artifacts": artifacts,
    }
    event_with_integrity = dict(event)
    event_with_integrity["evidence_integrity"] = captured
    return _audit_view_replay_event_integrity(
        event_with_integrity,
        workspace_root=workspace_root,
    )


def _audit_view_replay_event_integrity(
    event: dict[str, Any],
    *,
    workspace_root: Path | None,
    hash_cache: dict[Path, tuple[str, int]] | None = None,
) -> dict[str, Any]:
    """Reverify persisted replay artifacts without changing acceptance history."""

    source = str(event.get("source") or "")
    accepted = event.get("accepted") is True
    raw_integrity = (
        event.get("evidence_integrity")
        if isinstance(event.get("evidence_integrity"), dict)
        else None
    )
    strict_by_source = source == "reviewed_copy_script"
    if raw_integrity is None:
        return {
            "schema_version": VIEW_REPLAY_EVIDENCE_INTEGRITY_SCHEMA_VERSION,
            "algorithm": VIEW_REPLAY_EVIDENCE_INTEGRITY_ALGORITHM,
            "policy": (
                "reviewed_copy_script_strict"
                if strict_by_source
                else "legacy_non_copy_script"
            ),
            "strict": strict_by_source,
            "required_artifact_kinds": sorted(
                REVIEWED_COPY_SCRIPT_INTEGRITY_ARTIFACT_KINDS
                if strict_by_source
                else set()
            ),
            "artifacts": [],
            "status": (
                "missing_required_integrity"
                if strict_by_source
                else "legacy_unverified"
            ),
            "issue_codes": (
                ["evidence_integrity_record_missing"] if strict_by_source else []
            ),
            "trusted_for_replay": bool(accepted and not strict_by_source),
        }

    integrity = dict(raw_integrity)
    issue_codes: list[str] = []
    if integrity.get("schema_version") != VIEW_REPLAY_EVIDENCE_INTEGRITY_SCHEMA_VERSION:
        issue_codes.append("evidence_integrity_schema_invalid")
    if integrity.get("algorithm") != VIEW_REPLAY_EVIDENCE_INTEGRITY_ALGORITHM:
        issue_codes.append("evidence_integrity_algorithm_invalid")

    required_kinds = {
        str(item)
        for item in integrity.get("required_artifact_kinds") or []
        if str(item)
    }
    if strict_by_source:
        required_kinds.update(REVIEWED_COPY_SCRIPT_INTEGRITY_ARTIFACT_KINDS)
    strict = bool(strict_by_source or integrity.get("strict") is True)
    bindings = _view_replay_evidence_artifact_bindings(event)
    raw_artifacts = [
        item
        for item in integrity.get("artifacts") or []
        if isinstance(item, dict)
    ]
    artifacts_by_kind: dict[str, dict[str, Any]] = {}
    for item in raw_artifacts:
        kind = str(item.get("kind") or "")
        if not kind:
            issue_codes.append("evidence_integrity_artifact_kind_missing")
            continue
        if kind in artifacts_by_kind:
            issue_codes.append(f"evidence_integrity_artifact_duplicate:{kind}")
            continue
        artifacts_by_kind[kind] = item

    audited_artifacts: list[dict[str, Any]] = []
    for kind in sorted(set(artifacts_by_kind) | required_kinds):
        raw_artifact = artifacts_by_kind.get(kind)
        expected_path_text = bindings.get(kind)
        if raw_artifact is None:
            issue_codes.append(f"evidence_integrity_artifact_missing:{kind}")
            continue
        artifact = dict(raw_artifact)
        artifact["required"] = kind in required_kinds
        path_text = artifact.get("path")
        artifact_status = "verified"
        current_sha256: str | None = None
        current_byte_count: int | None = None
        if not path_text:
            issue_codes.append(f"evidence_integrity_artifact_path_missing:{kind}")
            artifact_status = "path_missing"
        elif not expected_path_text:
            issue_codes.append(f"evidence_integrity_binding_missing:{kind}")
            artifact_status = "binding_missing"
        elif workspace_root is None:
            issue_codes.append("evidence_integrity_workspace_root_unavailable")
            artifact_status = "verification_unavailable"
        else:
            try:
                path = Path(str(path_text)).expanduser().resolve()
                expected_path = Path(str(expected_path_text)).expanduser().resolve()
                _ensure_inside(workspace_root, path)
                _ensure_inside(workspace_root, expected_path)
                artifact["path"] = str(path)
                if path != expected_path:
                    issue_codes.append(
                        f"evidence_integrity_artifact_binding_mismatch:{kind}"
                    )
                    artifact_status = "binding_mismatch"
                elif not path.exists() or not path.is_file():
                    issue_codes.append(f"evidence_integrity_artifact_missing:{kind}")
                    artifact_status = "missing"
                else:
                    if hash_cache is not None and path in hash_cache:
                        current_sha256, current_byte_count = hash_cache[path]
                    else:
                        current_sha256, current_byte_count = _sha256_file(path)
                        if hash_cache is not None:
                            hash_cache[path] = (
                                current_sha256,
                                current_byte_count,
                            )
                    recorded_sha256 = artifact.get("recorded_sha256")
                    recorded_byte_count = artifact.get("recorded_byte_count")
                    if not isinstance(recorded_sha256, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", recorded_sha256
                    ):
                        issue_codes.append(
                            f"evidence_integrity_recorded_sha256_invalid:{kind}"
                        )
                        artifact_status = "recorded_digest_invalid"
                    elif current_sha256 != recorded_sha256:
                        issue_codes.append(
                            f"evidence_integrity_sha256_mismatch:{kind}"
                        )
                        artifact_status = "digest_mismatch"
                    if (
                        isinstance(recorded_byte_count, int)
                        and current_byte_count != recorded_byte_count
                    ):
                        issue_codes.append(
                            f"evidence_integrity_byte_count_mismatch:{kind}"
                        )
                        if artifact_status == "verified":
                            artifact_status = "byte_count_mismatch"
            except (GuiError, OSError, RuntimeError, ValueError):
                issue_codes.append(f"evidence_integrity_artifact_path_invalid:{kind}")
                artifact_status = "path_invalid"
        artifact["current_sha256"] = current_sha256
        artifact["current_byte_count"] = current_byte_count
        artifact["status"] = artifact_status
        audited_artifacts.append(artifact)

    unique_issue_codes = list(dict.fromkeys(issue_codes))
    status = (
        "not_required"
        if not strict and not audited_artifacts and not unique_issue_codes
        else "verified"
        if not unique_issue_codes
        else "invalid"
    )
    integrity.update(
        {
            "schema_version": VIEW_REPLAY_EVIDENCE_INTEGRITY_SCHEMA_VERSION,
            "algorithm": VIEW_REPLAY_EVIDENCE_INTEGRITY_ALGORITHM,
            "strict": strict,
            "required_artifact_kinds": sorted(required_kinds),
            "artifacts": audited_artifacts,
            "status": status,
            "issue_codes": unique_issue_codes,
            "trusted_for_replay": bool(
                accepted and (not strict or status == "verified")
            ),
        }
    )
    return integrity


def _view_replay_event_artifact_integrity_trusted(event: dict[str, Any]) -> bool:
    if event.get("accepted") is not True:
        return False
    integrity = (
        event.get("evidence_integrity")
        if isinstance(event.get("evidence_integrity"), dict)
        else {}
    )
    if event.get("source") == "reviewed_copy_script":
        return integrity.get("trusted_for_replay") is True
    if integrity.get("strict") is True:
        return integrity.get("trusted_for_replay") is True
    return True


def _view_replay_event_journal_trusted(event: dict[str, Any]) -> bool:
    if event.get("accepted") is not True:
        return False
    journal = (
        event.get("journal_consistency")
        if isinstance(event.get("journal_consistency"), dict)
        else {}
    )
    required = bool(
        event.get("source") == "reviewed_copy_script"
        or (
            isinstance(event.get("evidence_integrity"), dict)
            and event["evidence_integrity"].get("strict") is True
        )
        or event.get("event_record_schema_version")
        == VIEW_REPLAY_EVENT_RECORD_SCHEMA_VERSION
        or event.get("event_record_sha256")
    )
    if required:
        return journal.get("trusted_for_replay") is True
    return True


def _view_replay_event_is_trusted(event: dict[str, Any]) -> bool:
    return bool(
        _view_replay_event_artifact_integrity_trusted(event)
        and _view_replay_event_journal_trusted(event)
    )


def _view_replay_event_recipe_matches_step(
    event: dict[str, Any],
    step: dict[str, Any],
) -> bool:
    """Return whether an event was recorded against the step's current recipe identity."""

    event_recipe = (
        event.get("execution_recipe")
        if isinstance(event.get("execution_recipe"), dict)
        else {}
    )
    step_recipe = (
        step.get("execution_recipe")
        if isinstance(step.get("execution_recipe"), dict)
        else {}
    )
    return bool(
        event_recipe
        and step_recipe
        and event_recipe.get("schema_version") == step_recipe.get("schema_version")
        and event_recipe.get("recipe_kind") == step_recipe.get("recipe_kind")
    )


def _view_replay_event_satisfies_current_view_contract(
    event: dict[str, Any],
    view: dict[str, Any],
    *,
    model_type: str | None,
) -> bool:
    """Require fresh native-roll evidence only for current crystal standard recipes."""

    expected_recipe_kind = _expected_view_replay_recipe_kind(
        view,
        model_type=model_type,
    )
    if expected_recipe_kind != CRYSTAL_STANDARD_VIEW_RECIPE_KIND:
        return True
    if not _view_replay_event_recipe_matches_step(event, view):
        return False
    evidence = (
        event.get("crystal_camera_evidence")
        if isinstance(event.get("crystal_camera_evidence"), dict)
        else {}
    )
    return bool(
        event.get("crystal_camera_evidence_required") is True
        and event.get("crystal_camera_evidence_complete") is True
        and event.get("crystal_camera_screenshot_verified") is True
        and evidence.get("camera_match_scope")
        == CRYSTAL_STANDARD_VIEW_CAMERA_MATCH_SCOPE
        and evidence.get("view_direction_matches_manifest") is True
        and evidence.get("native_in_plane_roll_observed") is True
    )


def view_replay_manifest_recipe_contract_status(manifest: Any) -> dict[str, Any]:
    """Audit persisted recipe versions without mutating replay evidence."""

    if not isinstance(manifest, dict):
        return {
            "status": "manifest_missing_or_invalid",
            "current": False,
            "pending_recipe_upgrade_required": False,
            "manifest_schema_current": False,
            "actual_manifest_schema_version": None,
            "expected_manifest_schema_version": VIEW_REPLAY_MANIFEST_SCHEMA_VERSION,
            "view_contracts": [],
            "outdated_view_names": [],
            "pending_upgrade_view_names": [],
            "accepted_historical_view_names": [],
            "reasons": ["view_replay_manifest_missing_or_invalid"],
        }

    raw_manifest_schema = manifest.get("schema_version")
    actual_manifest_schema = (
        raw_manifest_schema
        if isinstance(raw_manifest_schema, int) and not isinstance(raw_manifest_schema, bool)
        else None
    )
    manifest_schema_current = actual_manifest_schema == VIEW_REPLAY_MANIFEST_SCHEMA_VERSION
    trusted_accepted_events = [
        event
        for event in manifest.get("replay_events") or []
        if isinstance(event, dict) and _view_replay_event_is_trusted(event)
    ]
    model_type = str(manifest.get("model_type") or "") or None
    view_contracts: list[dict[str, Any]] = []
    for view in manifest.get("views") or []:
        if not isinstance(view, dict) or view.get("supported") is not True:
            continue
        view_name = str(view.get("view_name") or "")
        recipe_contract = _view_replay_recipe_contract_status(
            view.get("execution_recipe"),
            expected_recipe_kind=_expected_view_replay_recipe_kind(
                view,
                model_type=str(manifest.get("model_type") or "") or None,
            ),
        )
        matching_events = [
            event
            for event in trusted_accepted_events
            if str(event.get("view_name") or "") == view_name
        ]
        historical_accepted = bool(matching_events)
        accepted = any(
            _view_replay_event_satisfies_current_view_contract(
                event,
                view,
                model_type=model_type,
            )
            for event in matching_events
        )
        view_contracts.append(
            {
                "view_name": view_name,
                "accepted": accepted,
                "historically_accepted": historical_accepted,
                "current_evidence_reverification_required": bool(
                    historical_accepted and not accepted
                ),
                **recipe_contract,
            }
        )

    outdated_view_names = [
        item["view_name"] for item in view_contracts if item.get("current") is not True
    ]
    pending_upgrade_view_names = [
        item["view_name"]
        for item in view_contracts
        if item.get("current") is not True and item.get("accepted") is not True
    ]
    accepted_historical_view_names = [
        item["view_name"]
        for item in view_contracts
        if item.get("current") is not True and item.get("accepted") is True
    ]
    current_evidence_reverification_view_names = [
        item["view_name"]
        for item in view_contracts
        if item.get("current_evidence_reverification_required") is True
    ]
    pending_recipe_upgrade_required = bool(
        pending_upgrade_view_names or not manifest_schema_current
    )
    current = bool(manifest_schema_current and not outdated_view_names)
    reasons: list[str] = []
    if not manifest_schema_current:
        reasons.append("view_replay_manifest_schema_outdated_or_incompatible")
    if pending_upgrade_view_names:
        reasons.append("pending_execution_recipe_upgrade_required")
    if accepted_historical_view_names:
        reasons.append("accepted_historical_recipe_evidence_retained")
    if current_evidence_reverification_view_names:
        reasons.append("current_crystal_camera_evidence_reverification_required")
    status = (
        "current"
        if current
        else "pending_recipe_upgrade_required"
        if pending_recipe_upgrade_required
        else "historical_recipe_upgrade_available"
    )
    return {
        "status": status,
        "current": current,
        "pending_recipe_upgrade_required": pending_recipe_upgrade_required,
        "manifest_schema_current": manifest_schema_current,
        "actual_manifest_schema_version": actual_manifest_schema,
        "expected_manifest_schema_version": VIEW_REPLAY_MANIFEST_SCHEMA_VERSION,
        "view_contracts": view_contracts,
        "outdated_view_names": outdated_view_names,
        "pending_upgrade_view_names": pending_upgrade_view_names,
        "accepted_historical_view_names": accepted_historical_view_names,
        "current_evidence_reverification_view_names": (
            current_evidence_reverification_view_names
        ),
        "reasons": reasons,
    }


def _verified_automatic_recipe_command_receipt(
    event: dict[str, Any],
    step: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a trusted invocation receipt for the same anonymous recipe."""

    if (
        event.get("source") != "computer_use"
        or event.get("view_name") != step.get("view_name")
        or event.get("accessibility_command_uses_required") is not True
        or event.get("accessibility_command_uses_complete") is not True
        or event.get("window_identity_verified") is not True
        or event.get("single_window_policy_ok") is not True
        or event.get("current_revision_loaded") is not True
    ):
        return None
    if not _view_replay_event_recipe_matches_step(event, step):
        return None
    window_binding = (
        event.get("window_binding")
        if isinstance(event.get("window_binding"), dict)
        else {}
    )
    if window_binding.get("ok") is not True:
        return None
    integrity = (
        event.get("evidence_integrity")
        if isinstance(event.get("evidence_integrity"), dict)
        else {}
    )
    journal = (
        event.get("journal_consistency")
        if isinstance(event.get("journal_consistency"), dict)
        else {}
    )
    if integrity.get("status") != "verified" or journal.get("status") != "matched":
        return None

    execution_recipe = (
        step.get("execution_recipe")
        if isinstance(step.get("execution_recipe"), dict)
        else {}
    )
    targets = _verified_anonymous_recipe_targets(execution_recipe)
    uses = [
        item
        for item in event.get("accessibility_command_uses") or []
        if isinstance(item, dict)
    ]
    if not targets or len(uses) != len(targets):
        return None
    identity_fields = (
        "command_id",
        "toolbar_name",
        "toolbar_automation_id",
        "registry_toolbar_name",
        "zero_based_child_index",
        "element_index",
        "registry_sha256",
        "semantic_mapping_sha256",
    )
    expected_identities = {
        tuple(target.get(field) for field in identity_fields) for target in targets
    }
    observed_identities = {
        tuple(use.get(field) for field in identity_fields) for use in uses
    }
    if expected_identities != observed_identities:
        return None
    if not all(
        use.get("accessibility_tree_refreshed") is True
        and use.get("invocation_succeeded") is True
        for use in uses
    ):
        return None
    return {
        "event_id": event.get("event_id"),
        "view_name": event.get("view_name"),
        "recorded_at": event.get("recorded_at"),
        "accepted": event.get("accepted") is True,
        "rejection_reasons": sorted(
            str(item)
            for item in event.get("rejection_reasons") or []
            if str(item)
        ),
        "command_mappings": [
            {
                "command_id": target.get("command_id"),
                "semantic_mapping_sha256": target.get(
                    "semantic_mapping_sha256"
                ),
            }
            for target in targets
        ],
        "semantic_mapping_sha256": sorted(
            str(target.get("semantic_mapping_sha256")) for target in targets
        ),
        "event_record_sha256": event.get("event_record_sha256"),
        "evidence_integrity_status": integrity.get("status"),
        "journal_consistency_status": journal.get("status"),
    }


def _verified_automatic_recipe_postcheck_failure(
    event: dict[str, Any],
    step: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a trusted visual failure for the same anonymous-control recipe."""

    receipt = _verified_automatic_recipe_command_receipt(event, step)
    if receipt is None or receipt.get("accepted") is True:
        return None
    visual_failure_reasons = set(receipt.get("rejection_reasons") or []) & {
        "camera_does_not_match_manifest",
        "model_not_visible",
    }
    if not visual_failure_reasons:
        return None
    return {
        **receipt,
        "failure_kind": "direct_visual_postcheck_failure",
        "rejection_reasons": sorted(visual_failure_reasons),
    }


def _derive_view_replay_status(
    *,
    preflight_ready: bool,
    all_confirmed: bool,
    accepted_view_count: int,
    integrity_blocked: bool,
    journal_blocked: bool,
    automatic_postcheck_failed: bool,
    pending_recipe_upgrade_required: bool,
) -> str:
    """Derive a complete top-level status without retaining stale state."""

    if not preflight_ready:
        return "blocked_with_prior_confirmation" if accepted_view_count else "blocked"
    if all_confirmed:
        return "externally_confirmed"
    if integrity_blocked:
        return "evidence_integrity_reverification_required"
    if journal_blocked:
        return "event_journal_reverification_required"
    if automatic_postcheck_failed:
        return "automatic_recipe_postcheck_failed"
    if accepted_view_count:
        return "partially_confirmed"
    if pending_recipe_upgrade_required:
        return "recipe_upgrade_required"
    return "ready_for_external_replay"


_VIEW_REPLAY_CONTINUATION_ACTION_OVERRIDE_STATUSES = frozenset(
    {
        "complete",
        "recipe_upgrade_required",
        "evidence_integrity_reverification_required",
        "event_journal_reverification_required",
        "automatic_recipe_postcheck_failed",
        "no_supported_pending_view",
    }
)


def _reconcile_view_replay_next_action(manifest: dict[str, Any]) -> None:
    """Keep stale prepared actions behind current replay safety decisions."""

    continuation = (
        manifest.get("replay_continuation")
        if isinstance(manifest.get("replay_continuation"), dict)
        else {}
    )
    incoming_action = (
        dict(manifest.get("next_action"))
        if isinstance(manifest.get("next_action"), dict)
        else {}
    )
    continuation_status = str(continuation.get("status") or "unknown")
    execution_action = (
        continuation.get("execution_action")
        if isinstance(continuation.get("execution_action"), dict)
        else {}
    )
    recipe_contract = (
        manifest.get("recipe_contract")
        if isinstance(manifest.get("recipe_contract"), dict)
        else {}
    )
    preflight = (
        manifest.get("preflight")
        if isinstance(manifest.get("preflight"), dict)
        else {}
    )
    automatic_replay_gate_open = bool(
        continuation_status == "automatic_recipe_ready"
        and continuation.get("automatic_replay_ready") is True
        and recipe_contract.get("current") is True
        and preflight.get("ready_for_external_replay") is True
        and preflight.get("activation_required") is not True
    )
    external_execution_ready = bool(
        automatic_replay_gate_open
        and execution_action.get("executor") == "computer_use"
        and isinstance(execution_action.get("payload_hint"), dict)
        and execution_action.get("gui_input_required") is True
        and execution_action.get("post_action_observation_required") is True
    )
    local_preview_ready = bool(
        automatic_replay_gate_open
        and execution_action.get("executor")
        == "material_studio_gui_execute_view_replay"
        and execution_action.get("payload_hint_is_directly_callable") is True
        and execution_action.get("gui_input_required") is False
        and execution_action.get("post_action_observation_required") is False
        and isinstance(execution_action.get("payload_hint"), dict)
        and execution_action["payload_hint"].get("execution_mode") == "preview"
    )
    continuation_overrides = (
        continuation_status in _VIEW_REPLAY_CONTINUATION_ACTION_OVERRIDE_STATUSES
        or external_execution_ready
        or local_preview_ready
    )
    if external_execution_ready or local_preview_ready:
        recommended_tool = execution_action.get("executor")
        recommended_action = execution_action.get("action")
        continuation_payload = execution_action.get("payload_hint")
        high_level_payload: Any = None
    else:
        recommended_tool = continuation.get("recommended_mcp_tool")
        recommended_action = continuation.get("recommended_action")
        continuation_payload = continuation.get("payload_hint")
        high_level_payload = continuation.get("high_level_payload_hint")

    if continuation_status == "complete":
        resolved_payload: dict[str, Any] = {
            "project_id": manifest.get("project_id")
        }
    elif (
        recommended_tool == "material_studio_live_modeling_request"
        and isinstance(high_level_payload, dict)
        and high_level_payload
    ):
        resolved_payload = dict(high_level_payload)
    elif isinstance(continuation_payload, dict):
        resolved_payload = dict(continuation_payload)
    else:
        resolved_payload = {}

    if continuation_overrides or not incoming_action:
        resolved_action = {
            "continuation_status": continuation_status,
            "recommended_tool": recommended_tool,
            "recommended_action": recommended_action,
            "payload_hint": resolved_payload,
            "payload_hint_is_directly_callable": continuation.get(
                "payload_hint_is_directly_callable"
            )
            is True,
            **(
                {
                    "gui_input_required": True,
                    "post_action_observation_required": True,
                    "post_action_record_tool": execution_action.get(
                        "post_action_record_tool"
                    ),
                    "post_action_record_payload_template_ref": (
                        "replay_continuation.post_action_record_payload_template"
                    ),
                }
                if external_execution_ready
                else {}
            ),
            **(
                {
                    "gui_input_required": False,
                    "post_action_observation_required": False,
                }
                if local_preview_ready
                else {}
            ),
            "source": "replay_continuation",
        }
    else:
        resolved_action = incoming_action

    incoming_identity = (
        incoming_action.get("recommended_tool"),
        incoming_action.get("recommended_action"),
        incoming_action.get("payload_hint"),
    )
    resolved_identity = (
        resolved_action.get("recommended_tool"),
        resolved_action.get("recommended_action"),
        resolved_action.get("payload_hint"),
    )
    incoming_action_overridden = bool(
        continuation_overrides and incoming_identity != resolved_identity
    )
    automatic_replay_allowed = automatic_replay_gate_open
    stale_recipe_execution_blocked = bool(
        continuation_status == "recipe_upgrade_required"
        or continuation.get("recipe_upgrade_required") is True
        or recipe_contract.get("pending_recipe_upgrade_required") is True
    )
    external_review_required = continuation_status in {
        "evidence_integrity_reverification_required",
        "event_journal_reverification_required",
        "automatic_recipe_postcheck_failed",
    }
    reason_codes: list[str] = []
    if continuation_overrides:
        reason_codes.append(
            "replay_continuation_safety_state_precedes_prepared_gui_action"
        )
    if external_execution_ready:
        reason_codes.append(
            "gui_recipe_execution_and_fresh_observation_precede_evidence_recording"
        )
    if local_preview_ready:
        reason_codes.append(
            "local_uia_preview_precedes_explicit_gui_execution_confirmation"
        )
    if stale_recipe_execution_blocked:
        reason_codes.append("current_safety_recipe_required_before_gui_replay")
    if external_review_required:
        reason_codes.append("fresh_observed_gui_evidence_required")
    if continuation_status == "complete":
        reason_codes.append("all_supported_views_already_confirmed")

    manifest["next_action"] = resolved_action
    manifest["next_action_resolution"] = {
        "status": (
            "continuation_safety_override_applied"
            if incoming_action_overridden
            else "continuation_safety_action_already_current"
            if continuation_overrides
            else "prepared_action_preserved"
        ),
        "authoritative_source": (
            "replay_continuation"
            if continuation_overrides or not incoming_action
            else "prepared_manifest_action"
        ),
        "continuation_status": continuation_status,
        "incoming_action_overridden": incoming_action_overridden,
        "resolved_recommended_tool": resolved_action.get("recommended_tool"),
        "resolved_recommended_action": resolved_action.get("recommended_action"),
        "reason_codes": reason_codes,
        "safety_gate": {
            "automatic_replay_allowed": automatic_replay_allowed,
            "stale_recipe_execution_blocked": stale_recipe_execution_blocked,
            "external_review_required": external_review_required,
            "gui_input_required": bool(
                execution_action.get("gui_input_required") is True
                if external_execution_ready or local_preview_ready
                else False
            ),
            "post_action_observation_required": bool(
                execution_action.get("post_action_observation_required") is True
                if external_execution_ready or local_preview_ready
                else False
            ),
            "metadata_write_allowed_before_observation": False,
            "record_tool_call_ready": False,
            "activation_required_before_gui_input": preflight.get(
                "activation_required"
            )
            is True,
            "structure_mutation_allowed": False,
            "revision_creation_allowed": False,
        },
        **(
            {
                "superseded_action": {
                    "recommended_tool": incoming_action.get("recommended_tool"),
                    "recommended_action": incoming_action.get(
                        "recommended_action"
                    ),
                }
            }
            if incoming_action_overridden
            else {}
        ),
    }


def _refresh_view_replay_summary(
    manifest: dict[str, Any],
    *,
    workspace_root: Path | None = None,
    events_path: Path | None = None,
) -> None:
    """Refresh replay progress while preserving the current preflight state."""

    replay_events = [item for item in manifest.get("replay_events") or [] if isinstance(item, dict)]
    hash_cache: dict[Path, tuple[str, int]] = {}
    for event in replay_events:
        event["evidence_integrity"] = _audit_view_replay_event_integrity(
            event,
            workspace_root=workspace_root,
            hash_cache=hash_cache,
        )
    journal_read, journal_events_by_id = _load_view_replay_event_journal(
        events_path,
        workspace_root=workspace_root,
    )
    for event in replay_events:
        event["journal_consistency"] = (
            _audit_view_replay_event_journal_consistency(
                event,
                journal_events_by_id=journal_events_by_id,
            )
        )
    manifest["replay_events"] = replay_events
    if replay_events:
        manifest["last_replay_event"] = replay_events[-1]
    raw_accepted_events = [
        item for item in replay_events if item.get("accepted") is True
    ]
    artifact_trusted_events = [
        item
        for item in raw_accepted_events
        if _view_replay_event_artifact_integrity_trusted(item)
    ]
    journal_trusted_events = [
        item
        for item in raw_accepted_events
        if _view_replay_event_journal_trusted(item)
    ]
    trusted_accepted_events = [
        item for item in replay_events if _view_replay_event_is_trusted(item)
    ]
    integrity_blocked_events = [
        item
        for item in raw_accepted_events
        if not _view_replay_event_artifact_integrity_trusted(item)
    ]
    journal_blocked_events = [
        item
        for item in raw_accepted_events
        if not _view_replay_event_journal_trusted(item)
    ]
    supported_steps = [
        item
        for item in manifest.get("views") or []
        if isinstance(item, dict) and item.get("supported") is True
    ]
    model_type = str(manifest.get("model_type") or "") or None
    accepted_views = {
        str(step.get("view_name"))
        for step in supported_steps
        if any(
            str(event.get("view_name") or "") == str(step.get("view_name") or "")
            and _view_replay_event_satisfies_current_view_contract(
                event,
                step,
                model_type=model_type,
            )
            for event in trusted_accepted_events
        )
    }
    raw_accepted_views = {
        str(item.get("view_name"))
        for item in raw_accepted_events
        if item.get("view_name") is not None
    }
    artifact_trusted_views = {
        str(item.get("view_name"))
        for item in artifact_trusted_events
        if item.get("view_name") is not None
    }
    journal_trusted_views = {
        str(item.get("view_name"))
        for item in journal_trusted_events
        if item.get("view_name") is not None
    }
    supported_view_names = {str(item.get("view_name")) for item in supported_steps}
    accepted_supported_views = accepted_views & supported_view_names
    pending_steps = [
        item for item in supported_steps if str(item.get("view_name")) not in accepted_supported_views
    ]
    recipe_contract = view_replay_manifest_recipe_contract_status(manifest)
    manifest["recipe_contract"] = recipe_contract
    current_recipe_view_names = {
        str(item.get("view_name"))
        for item in recipe_contract.get("view_contracts") or []
        if isinstance(item, dict) and item.get("current") is True
    }
    current_camera_evidence_reverification_view_names = {
        str(item)
        for item in recipe_contract.get(
            "current_evidence_reverification_view_names"
        )
        or []
        if item
    }
    automatic_postcheck_direct_failures: list[dict[str, Any]] = []
    for step in pending_steps:
        for event in reversed(replay_events):
            failure = _verified_automatic_recipe_postcheck_failure(event, step)
            if failure is not None:
                automatic_postcheck_direct_failures.append(failure)
                break

    reset_baseline_resolutions: dict[str, dict[str, Any]] = {}
    current_front_step = next(
        (
            step
            for step in supported_steps
            if str(step.get("view_name") or "") == "front"
        ),
        None,
    )
    for event in reversed(replay_events):
        if event.get("view_name") != "front":
            continue
        receipt_step = current_front_step
        if receipt_step is None:
            event_recipe = (
                event.get("execution_recipe")
                if isinstance(event.get("execution_recipe"), dict)
                else {}
            )
            event_recipe_contract = _view_replay_recipe_contract_status(
                event_recipe,
                expected_recipe_kind=_expected_view_replay_recipe_kind(
                    {"view_name": "front"},
                    model_type=model_type,
                ),
            )
            if event_recipe_contract.get("current") is not True:
                continue
            receipt_step = {
                "view_name": "front",
                "execution_recipe": event_recipe,
            }
        receipt = _verified_automatic_recipe_command_receipt(
            event,
            receipt_step,
        )
        if receipt is None:
            continue
        visual_failure_reasons = set(receipt.get("rejection_reasons") or []) & {
            "camera_does_not_match_manifest",
            "model_not_visible",
        }
        for mapping in receipt.get("command_mappings") or []:
            if not isinstance(mapping, dict):
                continue
            mapping_hash = mapping.get("semantic_mapping_sha256")
            if (
                mapping.get("command_id") != "cmdViewer3DResetView"
                or not isinstance(mapping_hash, str)
                or not mapping_hash
                or mapping_hash in reset_baseline_resolutions
            ):
                continue
            if receipt.get("accepted") is True:
                reset_baseline_resolutions[mapping_hash] = {
                    "status": "trusted_success",
                    "receipt": receipt,
                }
            elif visual_failure_reasons:
                reset_baseline_resolutions[mapping_hash] = {
                    "status": "trusted_visual_postcheck_failure",
                    "receipt": {
                        **receipt,
                        "failure_kind": "direct_visual_postcheck_failure",
                        "rejection_reasons": sorted(
                            visual_failure_reasons
                        ),
                    },
                }

    failed_reset_baselines = {
        mapping_hash: resolution["receipt"]
        for mapping_hash, resolution in reset_baseline_resolutions.items()
        if resolution.get("status")
        == "trusted_visual_postcheck_failure"
    }

    direct_failed_view_names = {
        str(item.get("view_name"))
        for item in automatic_postcheck_direct_failures
    }
    automatic_postcheck_dependency_failures: list[dict[str, Any]] = []
    for step in pending_steps:
        view_name = str(step.get("view_name"))
        if view_name in direct_failed_view_names:
            continue
        execution_recipe = (
            step.get("execution_recipe")
            if isinstance(step.get("execution_recipe"), dict)
            else {}
        )
        if (
            execution_recipe.get("camera_result_depends_on_reset_baseline")
            is not True
        ):
            continue
        reset_target = next(
            (
                target
                for target in _verified_anonymous_recipe_targets(
                    execution_recipe
                )
                if target.get("command_id") == "cmdViewer3DResetView"
                and target.get("semantic_mapping_sha256")
                in failed_reset_baselines
            ),
            None,
        )
        if reset_target is None:
            continue
        mapping_hash = str(reset_target.get("semantic_mapping_sha256"))
        dependency = failed_reset_baselines[mapping_hash]
        automatic_postcheck_dependency_failures.append(
            {
                "failure_kind": "blocked_by_failed_reset_baseline",
                "view_name": view_name,
                "dependency_view_name": dependency.get("view_name"),
                "dependency_event_id": dependency.get("event_id"),
                "dependency_event_record_sha256": dependency.get(
                    "event_record_sha256"
                ),
                "dependency_command_id": "cmdViewer3DResetView",
                "blocking_reasons": [
                    "verified_reset_baseline_failed_visual_postcheck"
                ],
                "semantic_mapping_sha256": [mapping_hash],
                "evidence_integrity_status": dependency.get(
                    "evidence_integrity_status"
                ),
                "journal_consistency_status": dependency.get(
                    "journal_consistency_status"
                ),
            }
        )

    automatic_postcheck_failures = [
        *automatic_postcheck_direct_failures,
        *automatic_postcheck_dependency_failures,
    ]
    automatic_postcheck_failed_view_names = {
        str(item.get("view_name")) for item in automatic_postcheck_failures
    }
    automation_ready_steps = [
        item
        for item in pending_steps
        if str(item.get("view_name")) in current_recipe_view_names
        if str(item.get("view_name"))
        not in automatic_postcheck_failed_view_names
        if isinstance(item.get("execution_recipe"), dict)
        and item["execution_recipe"].get("automation_ready") is True
    ]
    review_required_steps = [item for item in pending_steps if item not in automation_ready_steps]
    pending_view_names = [str(item.get("view_name")) for item in pending_steps]
    automation_ready_view_names = [str(item.get("view_name")) for item in automation_ready_steps]
    review_required_view_names = [str(item.get("view_name")) for item in review_required_steps]
    all_confirmed = bool(supported_view_names) and supported_view_names <= accepted_views
    integrity_blocked_view_names = sorted(
        (raw_accepted_views - artifact_trusted_views) & supported_view_names
    )
    journal_blocked_view_names = sorted(
        (raw_accepted_views - journal_trusted_views) & supported_view_names
    )
    trust_blocked_view_names = sorted(
        (raw_accepted_views - accepted_views) & supported_view_names
    )
    if integrity_blocked_view_names:
        evidence_integrity_status = "blocked"
    elif integrity_blocked_events:
        evidence_integrity_status = "verified_with_historical_drift"
    elif any(
        isinstance(item.get("evidence_integrity"), dict)
        and item["evidence_integrity"].get("status") == "verified"
        for item in replay_events
    ):
        evidence_integrity_status = "verified"
    else:
        evidence_integrity_status = "not_applicable"
    manifest_event_ids = {
        str(item.get("event_id"))
        for item in replay_events
        if item.get("event_id")
    }
    journal_event_ids = set(journal_events_by_id)
    all_journal_only_event_ids = sorted(journal_event_ids - manifest_event_ids)
    journal_only_event_ids = all_journal_only_event_ids[
        :VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
    ]
    journal_required_events = [
        item
        for item in replay_events
        if isinstance(item.get("journal_consistency"), dict)
        and item["journal_consistency"].get("required") is True
    ]
    journal_matched_events = [
        item
        for item in journal_required_events
        if item["journal_consistency"].get("status") == "matched"
    ]
    journal_divergent_events = [
        item
        for item in journal_required_events
        if item["journal_consistency"].get("status") != "matched"
    ]
    all_journal_divergent_event_ids = sorted(
        str(item.get("event_id"))
        for item in journal_divergent_events
        if item.get("event_id")
    )
    journal_divergent_event_ids = all_journal_divergent_event_ids[
        :VIEW_REPLAY_EVENT_JOURNAL_MAX_REPORTED_ISSUES
    ]
    journal_read_has_errors = journal_read.get("status") in {
        "invalid_path",
        "unreadable",
        "oversized",
        "too_many_lines",
        "invalid_lines",
        "duplicate_event_ids",
        "verification_unavailable",
    }
    if journal_blocked_view_names:
        journal_consistency_status = "blocked"
    elif (
        journal_divergent_events
        or all_journal_only_event_ids
        or journal_read_has_errors
    ):
        journal_consistency_status = "consistent_with_historical_divergence"
    elif journal_required_events:
        journal_consistency_status = "consistent"
    elif replay_events or journal_event_ids:
        journal_consistency_status = "legacy_not_required"
    else:
        journal_consistency_status = "not_applicable"
    manifest["event_journal"] = {
        **journal_read,
        "consistency_status": journal_consistency_status,
        "manifest_event_count": len(replay_events),
        "journal_required_event_count": len(journal_required_events),
        "journal_matched_event_count": len(journal_matched_events),
        "journal_divergent_event_count": len(all_journal_divergent_event_ids),
        "journal_divergent_event_ids": journal_divergent_event_ids,
        "journal_divergent_event_ids_truncated": (
            len(all_journal_divergent_event_ids) > len(journal_divergent_event_ids)
        ),
        "journal_only_event_count": len(all_journal_only_event_ids),
        "journal_only_event_ids": journal_only_event_ids,
        "journal_only_event_ids_truncated": (
            len(all_journal_only_event_ids) > len(journal_only_event_ids)
        ),
        "trusted_accepted_event_count": len(trusted_accepted_events),
        "journal_blocked_accepted_event_count": len(journal_blocked_events),
        "journal_blocked_view_names": journal_blocked_view_names,
    }
    manifest["replay_summary"] = {
        "event_count": len(replay_events),
        "raw_accepted_event_count": len(raw_accepted_events),
        "accepted_event_count": len(trusted_accepted_events),
        "trusted_accepted_event_count": len(trusted_accepted_events),
        "accepted_view_count": len(accepted_supported_views),
        "accepted_view_names": sorted(accepted_supported_views),
        "raw_accepted_view_count": len(raw_accepted_views & supported_view_names),
        "integrity_blocked_accepted_event_count": len(integrity_blocked_events),
        "integrity_blocked_view_count": len(integrity_blocked_view_names),
        "integrity_blocked_view_names": integrity_blocked_view_names,
        "evidence_integrity_status": evidence_integrity_status,
        "journal_consistency_status": journal_consistency_status,
        "journal_required_event_count": len(journal_required_events),
        "journal_matched_event_count": len(journal_matched_events),
        "journal_divergent_event_count": len(all_journal_divergent_event_ids),
        "journal_blocked_accepted_event_count": len(journal_blocked_events),
        "journal_blocked_view_count": len(journal_blocked_view_names),
        "journal_blocked_view_names": journal_blocked_view_names,
        "trust_blocked_view_count": len(trust_blocked_view_names),
        "trust_blocked_view_names": trust_blocked_view_names,
        "supported_view_count": len(supported_view_names),
        "pending_view_count": len(pending_steps),
        "pending_view_names": pending_view_names,
        "current_camera_evidence_reverification_view_count": len(
            current_camera_evidence_reverification_view_names
        ),
        "current_camera_evidence_reverification_view_names": sorted(
            current_camera_evidence_reverification_view_names
        ),
        "automation_ready_pending_view_count": len(automation_ready_steps),
        "automation_ready_pending_view_names": automation_ready_view_names,
        "automatic_postcheck_failure_count": len(
            automatic_postcheck_failures
        ),
        "automatic_postcheck_direct_failure_count": len(
            automatic_postcheck_direct_failures
        ),
        "automatic_postcheck_direct_failure_view_names": sorted(
            direct_failed_view_names
        ),
        "automatic_postcheck_dependency_blocked_count": len(
            automatic_postcheck_dependency_failures
        ),
        "automatic_postcheck_dependency_blocked_view_names": sorted(
            str(item.get("view_name"))
            for item in automatic_postcheck_dependency_failures
        ),
        "automatic_postcheck_failed_view_count": len(
            automatic_postcheck_failed_view_names
        ),
        "automatic_postcheck_failed_view_names": sorted(
            automatic_postcheck_failed_view_names
        ),
        "automatic_postcheck_failures": automatic_postcheck_failures,
        "review_required_pending_view_count": len(review_required_steps),
        "review_required_pending_view_names": review_required_view_names,
        "all_supported_views_confirmed": all_confirmed,
    }
    preflight = manifest.get("preflight") if isinstance(manifest.get("preflight"), dict) else {}
    next_pending_step = pending_steps[0] if pending_steps else None
    next_automation_step = automation_ready_steps[0] if automation_ready_steps else None
    next_actionable_pending_step = next(
        (
            item
            for item in pending_steps
            if str(item.get("view_name"))
            not in automatic_postcheck_failed_view_names
        ),
        None,
    )
    next_pending_view_name = (
        str(next_pending_step.get("view_name"))
        if next_pending_step is not None
        else None
    )
    next_action_step = next_automation_step or next_actionable_pending_step
    next_actionable_pending_view_name = (
        str(next_actionable_pending_step.get("view_name"))
        if next_actionable_pending_step is not None
        else None
    )
    next_action_view_name = (
        str(next_action_step.get("view_name"))
        if next_action_step is not None
        else None
    )
    next_action_integrity_blocked = bool(
        next_action_view_name in integrity_blocked_view_names
    )
    next_action_journal_blocked = bool(
        next_action_view_name in journal_blocked_view_names
    )
    next_pending_automatic_postcheck_failed = bool(
        next_pending_view_name in automatic_postcheck_failed_view_names
    )
    next_action_recipe = (
        next_action_step.get("execution_recipe")
        if next_action_step is not None
        and isinstance(next_action_step.get("execution_recipe"), dict)
        else {}
    )
    runtime_ui_preflight_required = bool(
        next_action_recipe.get("recipe_kind") in MILLER_VIEW_ONTO_RECIPE_KINDS
        and any(
            str(reason).startswith("runtime_")
            for reason in next_action_recipe.get("block_reasons") or []
        )
    )
    runtime_accessibility_recipe = (
        next_action_recipe.get("runtime_accessibility_preflight")
        if isinstance(next_action_recipe.get("runtime_accessibility_preflight"), dict)
        else {}
    )
    runtime_accessibility_preflight_required = bool(
        runtime_accessibility_recipe.get("required") is True
        and (
            runtime_accessibility_recipe.get("observation_available") is not True
            or runtime_accessibility_recipe.get("binding_verified") is not True
            or runtime_accessibility_recipe.get("base_preflight_satisfied") is not True
            or (
                runtime_accessibility_recipe.get("required_control_evidence_complete")
                is not True
            )
        )
    )
    runtime_accessibility_observation_blocks_automation = bool(
        runtime_accessibility_recipe.get("required") is True
        and runtime_accessibility_recipe.get("observation_available") is True
        and runtime_accessibility_recipe.get("binding_verified") is True
        and runtime_accessibility_recipe.get("base_preflight_satisfied") is True
        and runtime_accessibility_recipe.get("required_control_evidence_complete") is True
        and (
            runtime_accessibility_recipe.get(
                "observed_required_control_blocks_automation"
            )
            is True
        )
        and runtime_accessibility_recipe.get("automation_gate_satisfied") is not True
    )
    pending_recipe_upgrade_required = bool(
        recipe_contract.get("pending_recipe_upgrade_required") is True
    )
    local_miller_preview_supported = False
    local_miller_preview_block_reasons: list[str] = []
    if (
        next_automation_step is not None
        and next_action_recipe.get("recipe_kind") in MILLER_VIEW_ONTO_RECIPE_KINDS
    ):
        (
            local_miller_preview_supported,
            local_miller_preview_block_reasons,
        ) = _local_uia_recipe_support(next_action_recipe)
    if all_confirmed:
        continuation_status = "complete"
        recommended_executor = None
        recommended_action = "review_current_revision_after_all_prepared_views_were_confirmed"
        recommended_mcp_tool = "material_studio_live_project_status"
    elif pending_recipe_upgrade_required:
        continuation_status = "recipe_upgrade_required"
        recommended_executor = None
        recommended_action = (
            "regenerate_view_replay_manifest_with_current_safety_recipes_preserving_events"
        )
        recommended_mcp_tool = "material_studio_live_modeling_request"
    elif preflight.get("ready_for_external_replay") is not True:
        continuation_status = "preflight_blocked"
        recommended_executor = None
        recommended_action = "resolve_view_replay_preflight_blockers"
        recommended_mcp_tool = (manifest.get("next_action") or {}).get("recommended_tool")
    elif next_action_integrity_blocked and next_automation_step is None:
        continuation_status = "evidence_integrity_reverification_required"
        recommended_executor = "reviewed_copy_script_or_manual_gui_review"
        recommended_action = (
            "recapture_and_record_view_evidence_after_artifact_integrity_failure"
        )
        recommended_mcp_tool = "material_studio_gui_copy_script_assist"
    elif next_action_journal_blocked and next_automation_step is None:
        continuation_status = "event_journal_reverification_required"
        recommended_executor = "reviewed_copy_script_or_manual_gui_review"
        recommended_action = (
            "recapture_and_record_view_evidence_after_event_journal_divergence"
        )
        recommended_mcp_tool = "material_studio_gui_copy_script_assist"
    elif runtime_accessibility_preflight_required:
        continuation_status = "runtime_accessibility_preflight_required"
        recommended_executor = "computer_use_or_manual_review"
        recommended_action = (
            "observe_current_window_named_view_controls_then_submit_bound_runtime_accessibility_evidence"
        )
        recommended_mcp_tool = "material_studio_gui_prepare_view_replay"
    elif runtime_ui_preflight_required:
        continuation_status = "runtime_ui_preflight_required"
        recommended_executor = "computer_use_or_manual_review"
        recommended_action = (
            "observe_current_window_miller_plane_controls_then_submit_bound_runtime_ui_evidence"
        )
        recommended_mcp_tool = "material_studio_gui_prepare_view_replay"
    elif next_automation_step is not None:
        continuation_status = "automatic_recipe_ready"
        recommended_executor = (
            "local_uia_preview"
            if local_miller_preview_supported
            else "computer_use"
        )
        next_recipe = (
            next_automation_step.get("execution_recipe")
            if isinstance(next_automation_step.get("execution_recipe"), dict)
            else {}
        )
        recommended_action = (
            "preview_transactional_miller_plane_view_replay_before_explicit_execute"
            if local_miller_preview_supported
            else "execute_documented_direction_via_collinear_miller_plane_view_onto_recipe_cleanup_then_record_view"
            if next_recipe.get("recipe_kind")
            == "crystal_direction_via_collinear_miller_plane_view_onto"
            else "execute_documented_miller_plane_view_onto_recipe_cleanup_then_record_view"
            if next_recipe.get("recipe_kind") == "miller_plane_view_onto"
            else "execute_documented_staged_keyboard_recipe_restore_settings_then_record_view"
            if isinstance(next_recipe.get("keyboard_stages"), list)
            else
            "execute_documented_keyboard_recipe_then_record_view"
            if isinstance(next_recipe.get("key_sequence"), list)
            else "execute_verified_accessibility_recipe_then_record_view"
            if _verified_anonymous_recipe_targets(next_recipe)
            else "execute_named_accessibility_recipe_then_record_view"
        )
        recommended_mcp_tool = (
            "material_studio_gui_execute_view_replay"
            if local_miller_preview_supported
            else None
        )
    elif next_actionable_pending_step is not None:
        continuation_status = (
            "runtime_accessibility_blocks_automatic_replay"
            if runtime_accessibility_observation_blocks_automation
            else "reviewed_camera_backend_required"
        )
        recommended_executor = "reviewed_copy_script_or_manual_gui_review"
        recommended_action = (
            "use_reviewed_manual_or_copy_script_view_path_then_record_view"
            if runtime_accessibility_observation_blocks_automation
            else "obtain_reviewed_camera_backend_then_record_view"
        )
        recommended_mcp_tool = "material_studio_gui_copy_script_assist"
    elif (
        next_pending_automatic_postcheck_failed
        and next_actionable_pending_step is None
    ):
        continuation_status = "automatic_recipe_postcheck_failed"
        recommended_executor = "reviewed_copy_script_or_manual_gui_review"
        recommended_action = (
            "use_reviewed_camera_backend_after_automatic_recipe_postcheck_failure"
        )
        recommended_mcp_tool = "material_studio_gui_copy_script_assist"
    else:
        continuation_status = "no_supported_pending_view"
        recommended_executor = None
        recommended_action = "review_view_manifest"
        recommended_mcp_tool = "material_studio_live_project_status"
    selected_next_step = (
        next_automation_step
        or next_actionable_pending_step
        or next_pending_step
    )
    selected_recipe = (
        selected_next_step.get("execution_recipe")
        if selected_next_step is not None
        and isinstance(selected_next_step.get("execution_recipe"), dict)
        else {}
    )
    preflight_target_window = (
        preflight.get("target_window")
        if isinstance(preflight.get("target_window"), dict)
        else {}
    )
    selected_keyboard_stages = selected_recipe.get("keyboard_stages")
    keyboard_execution_stages = (
        [
            {
                key: stage.get(key)
                for key in VIEW_REPLAY_KEYBOARD_STAGE_FIELDS
            }
            for stage in selected_keyboard_stages
            if isinstance(stage, dict)
        ]
        if isinstance(selected_keyboard_stages, list)
        else None
    )
    crystal_camera_record_template: dict[str, Any] | None = None
    if selected_recipe.get("recipe_kind") == CRYSTAL_STANDARD_VIEW_RECIPE_KIND:
        crystal_camera_record_template = {
            "camera_match_scope": (
                selected_recipe.get("camera_match_contract") or {}
            ).get("scope"),
            "view_direction_matches_manifest": None,
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_observed": None,
        }
    miller_plane_record_template: dict[str, Any] | None = None
    miller_structure_artifact_path: str | None = None
    miller_structure_artifact_sha256: str | None = None
    if selected_recipe.get("recipe_kind") in MILLER_VIEW_ONTO_RECIPE_KINDS:
        target_resolution = (
            preflight.get("target_window_resolution")
            if isinstance(preflight.get("target_window_resolution"), dict)
            else {}
        )
        wrapper_metadata = (
            target_resolution.get("target_project_wrapper_metadata")
            if isinstance(target_resolution.get("target_project_wrapper_metadata"), dict)
            else {}
        )
        artifact_path_text = wrapper_metadata.get("source_path")
        artifact_hash: str | None = None
        if artifact_path_text:
            try:
                artifact_path = Path(str(artifact_path_text)).expanduser().resolve()
                if artifact_path.exists() and artifact_path.is_file():
                    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                    artifact_path_text = str(artifact_path)
            except OSError:
                artifact_hash = None
        miller_structure_artifact_path = artifact_path_text
        miller_structure_artifact_sha256 = artifact_hash
        miller_plane_record_template = {
            "miller_plane_indices": selected_recipe.get("miller_plane_indices"),
            "dialog_miller_indices": selected_recipe.get("dialog_miller_indices"),
            "dialog_miller_indices_text_before_create": None,
            "dialog_miller_indices_value_source": None,
            "dialog_miller_indices_verified_before_create": None,
            "created_plane_count": None,
            "selected_plane_count": None,
            "miller_plane_count_before": None,
            "miller_plane_count_after_create": None,
            "miller_plane_count_after_cleanup": None,
            "selection_method": selected_recipe.get("selection_method"),
            "object_tree_path_suffix": selected_recipe.get("selection_path_suffix"),
            **(
                {
                    "viewport_hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
                    "fresh_before_after_screenshots_observed": None,
                    "unique_transient_plane_region_observed": None,
                    "properties_selection_verified": None,
                    "view_onto_popup_menu_observed": None,
                    "view_onto_native_command_mapping_verified": None,
                    "dialog_show_set_of_parallel_planes": None,
                    "dialog_show_symmetry_images": None,
                }
                if selected_recipe.get("selection_method")
                == MILLER_PLANE_VIEWPORT_SELECTION_METHOD
                else {}
            ),
            "properties_filter": (
                selected_recipe.get("properties_verification") or {}
            ).get("filter"),
            "properties_miller_label": selected_recipe.get("properties_miller_label"),
            "camera_match_scope": (
                selected_recipe.get("camera_match_contract") or {}
            ).get("scope"),
            "plane_normal_matches_manifest": None,
            **(
                {"direct_lattice_direction_matches_manifest": None}
                if selected_recipe.get("recipe_kind")
                == "crystal_direction_via_collinear_miller_plane_view_onto"
                else {}
            ),
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_policy_observed": None,
            "pre_action_view_baseline_captured": None,
            "reset_view_before_alignment": None,
            "screenshot_captured_before_cleanup": None,
            "document_was_clean_before_replay": None,
            "temporary_miller_plane_cleanup_verified": None,
            "no_temporary_miller_nodes_remaining": None,
            "document_clean_after_replay": None,
            "post_replay_view_restored": None,
            "structure_artifact_path": artifact_path_text,
            "structure_artifact_sha256_before": None,
            "structure_artifact_sha256_after": None,
            "undo_labels_applied": None,
        }
    accessibility_command_targets = [
        {
            "command_id": target.get("command_id"),
            "toolbar_name": target.get("toolbar_name"),
            "toolbar_automation_id": target.get("toolbar_automation_id"),
            "registry_toolbar_name": target.get("registry_toolbar_name"),
            "zero_based_child_index": target.get("zero_based_child_index"),
            "element_index": target.get("element_index"),
            "registry_sha256": target.get("registry_sha256"),
            "semantic_mapping_sha256": target.get("semantic_mapping_sha256"),
        }
        for target in _verified_anonymous_recipe_targets(selected_recipe)
    ]
    accessibility_command_use_record_template = [
        {
            **target,
            "accessibility_tree_refreshed": None,
            "invocation_succeeded": None,
        }
        for target in accessibility_command_targets
    ]
    execution_payload_hint = {
        "project_id": manifest.get("project_id"),
        "revision": manifest.get("revision"),
        "view_name": selected_next_step.get("view_name") if selected_next_step is not None else None,
        "execution_recipe_ref": "replay_continuation.next_view.execution_recipe",
        "recipe_kind": selected_recipe.get("recipe_kind"),
        "expected_window_binding": {
            "expected_revision": manifest.get("revision"),
            "expected_window_handle": preflight_target_window.get("handle"),
            "expected_window_title": preflight_target_window.get("title"),
        },
        "native_command_id": selected_recipe.get("native_command_id"),
        "accessibility_command_targets": accessibility_command_targets or None,
        "key_sequence": selected_recipe.get("key_sequence"),
        "reset_before_key_sequence": selected_recipe.get("reset_before_key_sequence"),
        "rotation_increment_degrees": selected_recipe.get("rotation_increment_degrees"),
        "modifier_keys": selected_recipe.get("modifier_keys"),
        "keyboard_stages": keyboard_execution_stages,
        "rotation_increment_restored_degrees": selected_recipe.get(
            "restore_rotation_increment_degrees"
        ),
        "movement_options_command_id": selected_recipe.get("movement_options_command_id"),
        "movement_angle_control_id": selected_recipe.get("movement_angle_control_id"),
        "movement_screen_factor_control_id": selected_recipe.get(
            "movement_screen_factor_control_id"
        ),
        "movement_screen_factor_expected": selected_recipe.get(
            "movement_screen_factor_expected"
        ),
        "movement_dialog_closed_after_restore": selected_recipe.get(
            "movement_dialog_closed_after_restore"
        ),
        "expected_structure_artifact": (
            {
                "path": miller_structure_artifact_path,
                "sha256": miller_structure_artifact_sha256,
            }
            if miller_plane_record_template is not None
            else None
        ),
    }
    local_miller_preview_payload_hint = {
        "project_id": manifest.get("project_id"),
        "revision": manifest.get("revision"),
        "view_name": (
            selected_next_step.get("view_name")
            if selected_next_step is not None
            else None
        ),
        "execution_mode": "preview",
    }
    record_payload_template = {
        "project_id": manifest.get("project_id"),
        "revision": manifest.get("revision"),
        "view_name": selected_next_step.get("view_name") if selected_next_step is not None else None,
        "source": "computer_use" if next_automation_step is not None else "reviewed_copy_script",
        "model_visible": None,
        "camera_matches_manifest": None,
        "screenshot_path": None,
        "expected_window_handle": preflight_target_window.get("handle"),
        "expected_window_title": preflight_target_window.get("title"),
        "native_command_id": selected_recipe.get("native_command_id"),
        "accessibility_command_uses": (
            accessibility_command_use_record_template or None
        ),
        "key_sequence": None,
        "reset_before_key_sequence": None,
        "rotation_increment_degrees": None,
        "modifier_keys": None,
        "keyboard_stages": None,
        "rotation_increment_restored_degrees": None,
        "movement_options_command_id": selected_recipe.get("movement_options_command_id"),
        "movement_angle_control_id": selected_recipe.get("movement_angle_control_id"),
        "movement_screen_factor_control_id": selected_recipe.get(
            "movement_screen_factor_control_id"
        ),
        "movement_screen_factor": None,
        "movement_dialog_closed": None,
        "crystal_camera_evidence": crystal_camera_record_template,
        "miller_plane_evidence": miller_plane_record_template,
    }
    if record_payload_template["source"] == "reviewed_copy_script":
        record_payload_template.update(
            {
                "reviewed_copy_script_evidence": {
                    "script_text": None,
                    "capture_method": None,
                    "reviewer": None,
                    "copy_script_command_observed": None,
                    "review_completed": None,
                    "view_action_matches_manifest": None,
                    "structure_unchanged_observed": None,
                    "note": None,
                },
            }
        )
    post_action_high_level_record_payload_template = (
        {
            "user_request": f"Record the verified {selected_next_step.get('view_name')} GUI view replay.",
            "project_id": manifest.get("project_id"),
            "view_replay_confirmation": {
                "expected_revision": manifest.get("revision"),
                **{
                    key: record_payload_template.get(key)
                    for key in (
                        "view_name",
                        "source",
                        "model_visible",
                        "camera_matches_manifest",
                        "screenshot_path",
                        "reviewed_copy_script_evidence",
                        "expected_window_handle",
                        "expected_window_title",
                        "native_command_id",
                        "accessibility_command_uses",
                        "key_sequence",
                        "reset_before_key_sequence",
                        "rotation_increment_degrees",
                        "modifier_keys",
                        "keyboard_stages",
                        "rotation_increment_restored_degrees",
                        "movement_options_command_id",
                        "movement_angle_control_id",
                        "movement_screen_factor_control_id",
                        "movement_screen_factor",
                        "movement_dialog_closed",
                        "crystal_camera_evidence",
                        "miller_plane_evidence",
                    )
                    if key in record_payload_template
                },
            },
        }
        if selected_next_step is not None
        else {}
    )
    required_post_action_observation_fields = [
        "model_visible",
        "camera_matches_manifest",
    ]
    if accessibility_command_targets:
        required_post_action_observation_fields.extend(
            [
                "accessibility_command_uses[*].accessibility_tree_refreshed",
                "accessibility_command_uses[*].invocation_succeeded",
            ]
        )
    if selected_recipe.get("key_sequence") is not None:
        required_post_action_observation_fields.extend(
            [
                "key_sequence",
                "reset_before_key_sequence",
                "rotation_increment_degrees",
                "modifier_keys",
            ]
        )
    if selected_recipe.get("keyboard_stages") is not None:
        required_post_action_observation_fields.extend(
            [
                "keyboard_stages",
                "rotation_increment_restored_degrees",
                "movement_screen_factor",
                "movement_dialog_closed",
            ]
        )
    if crystal_camera_record_template is not None:
        required_post_action_observation_fields.extend(
            [
                "screenshot_path",
                "crystal_camera_evidence.view_direction_matches_manifest",
                "crystal_camera_evidence.native_in_plane_roll_observed",
            ]
        )
    if miller_plane_record_template is not None:
        required_post_action_observation_fields.extend(
            [
                "screenshot_path",
                "modifier_keys",
                "miller_plane_evidence",
            ]
        )
    if record_payload_template["source"] == "reviewed_copy_script":
        required_post_action_observation_fields.extend(
            [
                "screenshot_path",
                "reviewed_copy_script_evidence",
            ]
        )
    required_post_action_observation_fields = list(
        dict.fromkeys(required_post_action_observation_fields)
    )
    execution_action: dict[str, Any] | None = None
    if next_automation_step is not None:
        if local_miller_preview_supported:
            execution_action = {
                "phase": "local_uia_recipe_preview",
                "executor": "material_studio_gui_execute_view_replay",
                "action": recommended_action,
                "payload_hint": local_miller_preview_payload_hint,
                "payload_hint_is_directly_callable": True,
                "gui_input_required": False,
                "metadata_write_allowed": False,
                "structure_mutation_allowed": False,
                "revision_creation_allowed": False,
                "post_action_observation_required": False,
                "explicit_execute_confirmation_required_after_preview": True,
            }
        else:
            execution_action = {
                "phase": "gui_recipe_execution",
                "executor": "computer_use",
                "action": recommended_action,
                "payload_hint": execution_payload_hint,
                "payload_hint_is_directly_callable": False,
                "gui_input_required": True,
                "metadata_write_allowed": False,
                "structure_mutation_allowed": False,
                "revision_creation_allowed": False,
                "post_action_observation_required": True,
                "post_action_record_tool": "material_studio_gui_record_view_replay",
            }

    continuation_payload_hint = (
        execution_payload_hint if execution_action is not None else record_payload_template
    )
    if local_miller_preview_supported:
        continuation_payload_hint = local_miller_preview_payload_hint
    continuation_high_level_payload_hint: dict[str, Any] = {}
    post_action_record_payload_template = (
        record_payload_template
        if execution_action is not None
        and execution_action.get("post_action_observation_required") is True
        else None
    )
    post_action_high_level_payload_template = (
        post_action_high_level_record_payload_template
        if execution_action is not None
        and execution_action.get("post_action_observation_required") is True
        else None
    )
    post_review_record_payload_template: dict[str, Any] | None = None
    post_review_high_level_payload_template: dict[str, Any] | None = None
    payload_hint_is_directly_callable = bool(
        execution_action is not None
        and execution_action.get("payload_hint_is_directly_callable") is True
    )
    if pending_recipe_upgrade_required:
        continuation_payload_hint = {
            "user_request": "Continue GUI view replay using the current safety recipe.",
            "project_id": manifest.get("project_id"),
        }
        continuation_high_level_payload_hint = dict(continuation_payload_hint)
        payload_hint_is_directly_callable = True
    elif next_action_integrity_blocked and next_automation_step is None:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "context": (
                f"Recapture reviewed Copy Script and screenshot evidence for the "
                f"prepared {selected_next_step.get('view_name') if selected_next_step else 'next'} "
                "view because a persisted evidence artifact failed integrity verification."
            ),
        }
        continuation_high_level_payload_hint = {}
        post_review_record_payload_template = record_payload_template
        post_review_high_level_payload_template = (
            post_action_high_level_record_payload_template
        )
        payload_hint_is_directly_callable = True
    elif next_action_journal_blocked and next_automation_step is None:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "context": (
                f"Recapture reviewed view evidence for the prepared "
                f"{selected_next_step.get('view_name') if selected_next_step else 'next'} "
                "view because its manifest and append-only journal records diverged."
            ),
        }
        continuation_high_level_payload_hint = {}
        post_review_record_payload_template = record_payload_template
        post_review_high_level_payload_template = (
            post_action_high_level_record_payload_template
        )
        payload_hint_is_directly_callable = True
    elif runtime_accessibility_preflight_required:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "views": [selected_next_step.get("view_name")] if selected_next_step else [],
            "runtime_accessibility_evidence_required": True,
            "runtime_accessibility_evidence_schema_ref": (
                "material_studio_gui_prepare_view_replay.inputSchema.properties."
                "runtime_accessibility_evidence"
            ),
            "required_command_ids": runtime_accessibility_recipe.get(
                "required_command_ids"
            )
            or [],
            "missing_required_command_ids": runtime_accessibility_recipe.get(
                "missing_required_command_ids"
            )
            or [],
            "expected_window_binding": {
                "expected_revision": manifest.get("revision"),
                "expected_window_handle": preflight_target_window.get("handle"),
                "expected_window_title": preflight_target_window.get("title"),
            },
            "observed_values_must_not_be_assumed_from_static_command_registry": True,
        }
        continuation_high_level_payload_hint = {}
        payload_hint_is_directly_callable = False
    elif runtime_accessibility_observation_blocks_automation:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "context": (
                f"Obtain a reviewed non-coordinate GUI or Copy Script path for the "
                f"prepared {selected_next_step.get('view_name') if selected_next_step else 'next'} "
                "view before recording observed camera evidence."
            ),
        }
        continuation_high_level_payload_hint = {}
        post_review_record_payload_template = record_payload_template
        post_review_high_level_payload_template = (
            post_action_high_level_record_payload_template
        )
        payload_hint_is_directly_callable = True
    elif runtime_ui_preflight_required:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "views": [selected_next_step.get("view_name")] if selected_next_step else [],
            "runtime_ui_evidence_required": True,
            "runtime_ui_evidence_schema_ref": (
                "material_studio_gui_prepare_view_replay.inputSchema.properties.runtime_ui_evidence"
            ),
            "observed_boolean_fields": list(MILLER_RUNTIME_UI_BOOLEAN_FIELDS),
            "expected_window_binding": {
                "expected_revision": manifest.get("revision"),
                "expected_window_handle": preflight_target_window.get("handle"),
                "expected_window_title": preflight_target_window.get("title"),
            },
            "fixed_keyboard_contract": {
                "miller_planes_menu_key_sequence": list(
                    MILLER_RUNTIME_UI_REQUIRED_KEY_SEQUENCE
                ),
                "selection_modifier_keys": [],
            },
            "observed_values_must_not_be_copied_from_record_payload_examples": True,
        }
        continuation_high_level_payload_hint = {}
        payload_hint_is_directly_callable = False
    elif (
        next_pending_automatic_postcheck_failed
        and next_actionable_pending_step is None
    ):
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "context": (
                f"Obtain a reviewed camera or Copy Script path for the prepared "
                f"{selected_next_step.get('view_name') if selected_next_step else 'next'} "
                "view because the verified automatic recipe failed its visual postcheck."
            ),
        }
        continuation_high_level_payload_hint = {}
        post_review_record_payload_template = record_payload_template
        post_review_high_level_payload_template = (
            post_action_high_level_record_payload_template
        )
        payload_hint_is_directly_callable = True
    elif next_automation_step is None and next_actionable_pending_step is not None:
        continuation_payload_hint = {
            "project_id": manifest.get("project_id"),
            "revision": manifest.get("revision"),
            "context": (
                f"Obtain reviewed Copy Script and screenshot evidence for the "
                f"prepared {selected_next_step.get('view_name') if selected_next_step else 'next'} "
                "view before recording it."
            ),
        }
        continuation_high_level_payload_hint = {}
        post_review_record_payload_template = record_payload_template
        post_review_high_level_payload_template = (
            post_action_high_level_record_payload_template
        )
        payload_hint_is_directly_callable = True
    resolved_post_action_record_payload_template = (
        post_action_record_payload_template or post_review_record_payload_template
    )
    resolved_post_action_high_level_payload_template = (
        post_action_high_level_payload_template
        or post_review_high_level_payload_template
    )
    manifest["replay_continuation"] = {
        "status": continuation_status,
        "automatic_replay_ready": next_automation_step is not None,
        "recipe_upgrade_required": pending_recipe_upgrade_required,
        "recipe_contract": recipe_contract,
        "current_camera_evidence_reverification_required": bool(
            current_camera_evidence_reverification_view_names
        ),
        "current_camera_evidence_reverification_view_names": sorted(
            current_camera_evidence_reverification_view_names
        ),
        "evidence_integrity_reverification_required": bool(
            integrity_blocked_view_names
        ),
        "integrity_blocked_view_names": integrity_blocked_view_names,
        "event_journal_reverification_required": bool(
            journal_blocked_view_names
        ),
        "journal_consistency_status": journal_consistency_status,
        "journal_blocked_view_names": journal_blocked_view_names,
        "automatic_recipe_postcheck_failed": bool(
            automatic_postcheck_failed_view_names
        ),
        "automatic_postcheck_failed_view_names": sorted(
            automatic_postcheck_failed_view_names
        ),
        "automatic_postcheck_direct_failures": (
            automatic_postcheck_direct_failures
        ),
        "automatic_postcheck_dependency_failures": (
            automatic_postcheck_dependency_failures
        ),
        "runtime_accessibility_preflight_required": (
            runtime_accessibility_preflight_required
        ),
        "runtime_accessibility_observation_blocks_automation": (
            runtime_accessibility_observation_blocks_automation
        ),
        "runtime_accessibility_preflight": runtime_accessibility_recipe,
        "runtime_ui_preflight_required": runtime_ui_preflight_required,
        "runtime_ui_preflight": selected_recipe.get("runtime_ui_preflight"),
        "local_miller_preview_supported": local_miller_preview_supported,
        "local_miller_preview_block_reasons": local_miller_preview_block_reasons,
        "recommended_executor": recommended_executor,
        "recommended_action": recommended_action,
        "recommended_mcp_tool": recommended_mcp_tool,
        "execution_action": execution_action,
        "execution_recipe_ref": (
            "replay_continuation.next_view.execution_recipe"
            if execution_action is not None
            else None
        ),
        "gui_input_required": bool(
            execution_action is not None
            and execution_action.get("gui_input_required") is True
        ),
        "post_action_observation_required": bool(
            execution_action is not None
            and execution_action.get("post_action_observation_required") is True
        ),
        "record_call_ready": False,
        "record_tool": "material_studio_gui_record_view_replay",
        "high_level_record_tool": "material_studio_live_modeling_request",
        "next_pending_view_name": (
            str(next_pending_step.get("view_name")) if next_pending_step is not None else None
        ),
        "next_actionable_pending_view_name": next_actionable_pending_view_name,
        "next_automation_ready_view_name": (
            str(next_automation_step.get("view_name"))
            if next_automation_step is not None
            else None
        ),
        "next_view": {
            "view_name": selected_next_step.get("view_name"),
            "camera": selected_next_step.get("camera"),
            "crystallography": selected_next_step.get("crystallography"),
            "verification": selected_next_step.get("verification"),
            "execution_recipe": selected_next_step.get("execution_recipe"),
        }
        if selected_next_step is not None
        else None,
        "payload_hint": continuation_payload_hint,
        "payload_hint_is_directly_callable": payload_hint_is_directly_callable,
        "high_level_payload_hint": continuation_high_level_payload_hint,
        "post_action_record_payload_template": (
            resolved_post_action_record_payload_template
        ),
        "post_action_record_payload_template_is_directly_callable": False,
        "post_action_high_level_payload_template": (
            resolved_post_action_high_level_payload_template
        ),
        "post_action_required_observation_fields": (
            required_post_action_observation_fields
            if resolved_post_action_record_payload_template is not None
            else []
        ),
        "post_review_record_payload_template": post_review_record_payload_template,
        "post_review_record_payload_template_is_directly_callable": False,
        "post_review_high_level_payload_template": (
            post_review_high_level_payload_template
        ),
        "evidence_values_must_be_observed_not_assumed": bool(
            crystal_camera_record_template
            or miller_plane_record_template
            or accessibility_command_targets
            or execution_action
            or runtime_accessibility_observation_blocks_automation
            or next_action_integrity_blocked
            or next_action_journal_blocked
            or next_pending_automatic_postcheck_failed
            or record_payload_template.get("source") == "reviewed_copy_script"
        ),
    }
    _reconcile_view_replay_next_action(manifest)
    manifest["replay_status"] = _derive_view_replay_status(
        preflight_ready=preflight.get("ready_for_external_replay") is True,
        all_confirmed=all_confirmed,
        accepted_view_count=len(accepted_supported_views),
        integrity_blocked=bool(integrity_blocked_view_names),
        journal_blocked=bool(journal_blocked_view_names),
        automatic_postcheck_failed=bool(automatic_postcheck_failed_view_names),
        pending_recipe_upgrade_required=pending_recipe_upgrade_required,
    )


def _write_text_atomic(path: Path, content: str) -> None:
    """Atomically replace an inert text artifact in the workspace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON artifact in its existing workspace directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unique_strings(values: list[str]) -> list[str]:
    """Return strings in first-seen order without duplicates."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _window_management_receipt(
    *,
    controller_workspace_root: Path,
    processes: list[ProcessInfo],
    window_inventory: list[dict[str, Any]],
    selected_window: WindowInfo | None,
    target_window: WindowInfo | None,
    target_resolution: dict[str, Any] | None,
    requested_project_id: str | None,
    requested_revision: int | None,
    same_window_open_supported: bool,
    file_open_may_launch_new_instance: bool,
    startup_dialog_open_supported: bool,
) -> dict[str, Any]:
    """Return a compact decision receipt for multi-window live GUI control."""

    requested_target = requested_project_id is not None or requested_revision is not None
    selected_entry = _window_inventory_entry(window_inventory, selected_window.handle if selected_window else None)
    target_entry = _window_inventory_entry(window_inventory, target_window.handle if target_window else None)
    matched_project_window = bool(target_resolution and target_resolution.get("matched_project_window"))
    matching_window_count = (
        int(target_resolution.get("matching_window_count") or 0)
        if isinstance(target_resolution, dict)
        else 0
    )
    wrapper_entries = [entry for entry in window_inventory if entry.get("project_wrapper_metadata")]
    external_wrapper_entries = [
        entry
        for entry in wrapper_entries
        if entry.get("wrapper_workspace_matches_controller") is False
    ]
    ambiguous_wrapper_entries = [
        entry
        for entry in wrapper_entries
        if entry.get("wrapper_provenance_status") == "ambiguous_across_trusted_workspaces"
    ]
    selected_workspace_mismatch = bool(
        selected_entry and selected_entry.get("wrapper_workspace_matches_controller") is False
    )
    target_workspace_mismatch = bool(
        target_entry and target_entry.get("wrapper_workspace_matches_controller") is False
    )
    selected_workspace_ambiguous = bool(
        selected_entry
        and selected_entry.get("wrapper_provenance_status") == "ambiguous_across_trusted_workspaces"
    )
    target_workspace_ambiguous = bool(
        target_entry
        and target_entry.get("wrapper_provenance_status") == "ambiguous_across_trusted_workspaces"
    )
    exact_target_context_selected = bool(
        requested_target
        and matched_project_window
        and matching_window_count == 1
        and target_entry is not None
    )
    workspace_context_mismatch_reasons: list[str] = []
    if exact_target_context_selected:
        if target_workspace_mismatch:
            workspace_context_mismatch_reasons.append(
                "target_wrapper_belongs_to_trusted_external_workspace"
            )
        if target_workspace_ambiguous:
            workspace_context_mismatch_reasons.append(
                "target_wrapper_workspace_provenance_ambiguous"
            )
    else:
        if selected_workspace_mismatch:
            workspace_context_mismatch_reasons.append(
                "selected_wrapper_belongs_to_trusted_external_workspace"
            )
        if target_workspace_mismatch:
            workspace_context_mismatch_reasons.append(
                "target_wrapper_belongs_to_trusted_external_workspace"
            )
        if selected_workspace_ambiguous:
            workspace_context_mismatch_reasons.append(
                "selected_wrapper_workspace_provenance_ambiguous"
            )
        if target_workspace_ambiguous:
            workspace_context_mismatch_reasons.append(
                "target_wrapper_workspace_provenance_ambiguous"
            )
    workspace_context_mismatch = bool(workspace_context_mismatch_reasons)
    context_entry = (
        target_entry
        if exact_target_context_selected
        else selected_entry
        if selected_entry and selected_entry.get("project_wrapper_metadata")
        else target_entry
        if target_entry and target_entry.get("project_wrapper_metadata")
        else None
    )
    recommended_working_dir = (
        context_entry.get("wrapper_workspace_root")
        if context_entry and context_entry.get("wrapper_workspace_matches_controller") is False
        else None
    )
    visible_wrapper_identity_trusted = bool(
        context_entry
        and context_entry.get("wrapper_provenance_status") == "verified_revision_wrapper"
    )
    workspace_context = {
        "controller_workspace_root": str(controller_workspace_root.resolve()),
        "mismatch": workspace_context_mismatch,
        "mismatch_reasons": workspace_context_mismatch_reasons,
        "selected_wrapper_workspace_root": (
            selected_entry.get("wrapper_workspace_root") if selected_entry else None
        ),
        "selected_wrapper_workspace_matches_controller": (
            selected_entry.get("wrapper_workspace_matches_controller") if selected_entry else None
        ),
        "target_wrapper_workspace_root": (
            target_entry.get("wrapper_workspace_root") if target_entry else None
        ),
        "target_wrapper_workspace_matches_controller": (
            target_entry.get("wrapper_workspace_matches_controller") if target_entry else None
        ),
        "visible_wrapper_project_id": context_entry.get("project_id") if context_entry else None,
        "visible_wrapper_revision": context_entry.get("revision") if context_entry else None,
        "visible_wrapper_source_path": context_entry.get("source_path") if context_entry else None,
        "visible_wrapper_window_handle": context_entry.get("handle") if context_entry else None,
        "visible_wrapper_window_title": context_entry.get("title") if context_entry else None,
        "visible_wrapper_provenance_status": (
            context_entry.get("wrapper_provenance_status") if context_entry else None
        ),
        "visible_wrapper_identity_trusted": visible_wrapper_identity_trusted,
        "recommended_project_id": (
            context_entry.get("project_id") if visible_wrapper_identity_trusted else None
        ),
        "recommended_revision": (
            context_entry.get("revision") if visible_wrapper_identity_trusted else None
        ),
        "recommended_working_dir": recommended_working_dir,
        "automatic_workspace_adoption_allowed": False,
    }
    matstudio_process_ids = {process.pid for process in processes}
    for entry in window_inventory:
        entry["pid_is_matstudio_process"] = bool(
            entry.get("pid") in matstudio_process_ids
        )
    matstudio_window_entries = [
        entry
        for entry in window_inventory
        if entry.get("pid") in matstudio_process_ids
    ]
    ignored_non_matstudio_window_entries = [
        entry
        for entry in window_inventory
        if entry.get("pid") not in matstudio_process_ids
    ]
    matching_window_entries: list[dict[str, Any]] = []
    if requested_target:
        for entry in matstudio_window_entries:
            if not isinstance(entry.get("project_wrapper_metadata"), dict):
                continue
            if (
                requested_project_id is not None
                and entry.get("project_id") != requested_project_id
            ):
                continue
            if requested_revision is not None:
                try:
                    entry_revision = int(entry.get("revision"))
                except (TypeError, ValueError):
                    continue
                if entry_revision != int(requested_revision):
                    continue
            matching_window_entries.append(entry)
    live_matching_window_count = len(matching_window_entries)
    target_window_matches_requested_project_revision = bool(
        requested_target
        and target_entry is not None
        and any(
            entry.get("handle") == target_entry.get("handle")
            for entry in matching_window_entries
        )
    )
    mcp_title_entries = [
        entry
        for entry in matstudio_window_entries
        if _project_name_from_window_title(str(entry.get("title") or "")) is not None
    ]
    primary_entries = [
        entry
        for entry in matstudio_window_entries
        if _is_primary_materials_studio_window_entry(entry)
    ]
    all_dialog_entries = [
        entry
        for entry in matstudio_window_entries
        if _is_materials_studio_dialog_entry(entry)
    ]
    target_process_id = target_entry.get("pid") if target_entry else None
    selected_process_id = selected_entry.get("pid") if selected_entry else None
    target_window_pid_is_matstudio_process = bool(
        target_process_id is not None
        and target_process_id in matstudio_process_ids
    )
    selected_window_pid_is_matstudio_process = bool(
        selected_process_id is not None
        and selected_process_id in matstudio_process_ids
    )
    target_process_primary_entries = [
        entry
        for entry in primary_entries
        if target_process_id is not None and entry.get("pid") == target_process_id
    ]
    target_process_dialog_entries = [
        entry
        for entry in all_dialog_entries
        if target_process_id is not None and entry.get("pid") == target_process_id
    ]
    exact_target_process_candidate = bool(
        requested_target
        and matched_project_window
        and live_matching_window_count == 1
        and target_window_matches_requested_project_revision
        and target_entry is not None
        and target_entry.get("wrapper_target_identity_verified") is True
        and target_entry.get("wrapper_workspace_matches_controller") is True
        and target_window_pid_is_matstudio_process
    )
    exact_target_process_isolated = bool(
        exact_target_process_candidate
        and len(target_process_primary_entries) == 1
        and target_process_primary_entries[0].get("handle") == target_entry.get("handle")
    )
    global_multiple_processes_detected = len(processes) > 1
    global_multiple_primary_windows_detected = len(primary_entries) > 1
    project_scoped_multi_instance_isolation = bool(
        global_multiple_processes_detected and exact_target_process_isolated
    )
    dialog_entries = (
        target_process_dialog_entries
        if project_scoped_multi_instance_isolation
        else all_dialog_entries
    )
    unrelated_dialog_entries = [
        entry for entry in all_dialog_entries if entry not in dialog_entries
    ]
    file_association_dialog_entries = [
        entry for entry in dialog_entries if _is_file_association_dialog_entry(entry)
    ]
    welcome_dialog_entries = [
        entry for entry in dialog_entries if _is_welcome_dialog_entry(entry)
    ]
    startup_dialog_entries = [
        entry for entry in dialog_entries if _is_startup_dialog_entry(entry)
    ]
    resolvable_startup_dialog_entries = (
        startup_dialog_entries if startup_dialog_open_supported else []
    )
    unmatched_mcp_title_count = sum(1 for entry in mcp_title_entries if not entry.get("project_wrapper_metadata"))
    target_window_is_selected = bool(
        selected_window is not None and target_window is not None and selected_window.handle == target_window.handle
    )
    target_window_is_visible = target_entry.get("is_visible") if target_entry else None
    target_window_is_minimized = target_entry.get("is_minimized") if target_entry else None
    target_window_foreground_observed = bool(
        target_entry
        and (
            target_entry.get("foreground_state_observed") is True
            or target_entry.get("is_foreground") is not None
        )
    )
    target_window_is_foreground = (
        bool(target_entry.get("is_foreground"))
        if target_entry and target_window_foreground_observed
        else None
    )
    interaction_activation_reasons: list[str] = []
    if target_window is not None:
        if target_window_is_minimized is True:
            interaction_activation_reasons.append("target_window_minimized")
        if target_window_is_visible is False:
            interaction_activation_reasons.append("target_window_not_visible")
        if target_window_foreground_observed and target_window_is_foreground is False:
            interaction_activation_reasons.append("target_window_not_foreground")
    activation_reasons = list(interaction_activation_reasons)
    if target_window is not None and not target_window_is_selected:
        activation_reasons.insert(0, "target_window_not_selected")
    needs_activation = bool(target_window is not None and activation_reasons)
    activation_required_before_capture_or_input = bool(
        target_window is not None and interaction_activation_reasons
    )
    fallback_used = bool(target_resolution and target_resolution.get("fallback_used"))
    blocking_dialog_entries = [
        entry for entry in dialog_entries if not _is_file_association_dialog_entry(entry)
    ]
    unresolved_blocking_dialog_entries = [
        entry for entry in blocking_dialog_entries if entry not in resolvable_startup_dialog_entries
    ]

    warnings: list[str] = []
    single_window_violation_reasons: list[str] = []
    if target_window is not None and not target_window_pid_is_matstudio_process:
        warnings.append("target_window_pid_not_matstudio_process")
        single_window_violation_reasons.append(
            "target_window_pid_not_matstudio_process"
        )
    if requested_target and live_matching_window_count > 1:
        warnings.append("requested_project_revision_window_ambiguous")
        single_window_violation_reasons.append(
            "requested_project_revision_window_ambiguous"
        )
    if requested_target and live_matching_window_count != matching_window_count:
        warnings.append("requested_project_revision_window_inventory_changed")
        single_window_violation_reasons.append(
            "requested_project_revision_window_inventory_changed"
        )
    if (
        requested_target
        and matched_project_window
        and not target_window_matches_requested_project_revision
    ):
        warnings.append("target_project_revision_window_identity_changed")
        single_window_violation_reasons.append(
            "target_project_revision_window_identity_changed"
        )
    if requested_target and matched_project_window and target_entry is not None:
        if target_entry.get("wrapper_target_identity_verified") is not True:
            warnings.append("target_wrapper_identity_unverified")
            single_window_violation_reasons.append(
                "target_wrapper_identity_unverified"
            )
        elif target_entry.get("wrapper_integrity_verified") is not True:
            warnings.append("target_wrapper_source_outdated_reload_required")
        if (
            target_entry.get("wrapper_workspace_matches_controller")
            is not True
        ):
            warnings.append("target_wrapper_workspace_mismatch")
            single_window_violation_reasons.append(
                "target_wrapper_workspace_mismatch"
            )
    if global_multiple_processes_detected:
        warnings.append("multiple_matstudio_processes_detected")
        if not project_scoped_multi_instance_isolation:
            single_window_violation_reasons.append(
                "multiple_matstudio_processes_detected"
            )
    if global_multiple_primary_windows_detected:
        warnings.append("multiple_matstudio_windows_detected")
        if not project_scoped_multi_instance_isolation:
            single_window_violation_reasons.append(
                "multiple_matstudio_windows_detected"
            )
    if project_scoped_multi_instance_isolation:
        warnings.append("project_scoped_multi_instance_isolation_active")
    if ignored_non_matstudio_window_entries:
        warnings.append("non_matstudio_title_match_ignored")
    if unrelated_dialog_entries:
        warnings.append("unrelated_matstudio_dialogs_ignored")
    if file_association_dialog_entries:
        warnings.append("file_association_dialog_detected")
    elif welcome_dialog_entries:
        warnings.append("welcome_dialog_detected")
    elif any(_is_new_project_dialog_entry(entry) for entry in startup_dialog_entries):
        warnings.append("new_project_dialog_detected")
    elif startup_dialog_entries:
        warnings.append("startup_dialog_detected")
    elif dialog_entries:
        warnings.append("materials_studio_dialog_detected")
    if unmatched_mcp_title_count:
        warnings.append("mcp_wrapper_window_metadata_missing")
    if ambiguous_wrapper_entries:
        warnings.append("mcp_wrapper_workspace_provenance_ambiguous")
    if workspace_context_mismatch:
        warnings.append("gui_wrapper_workspace_mismatch")
    if requested_target and fallback_used:
        warnings.append("target_project_window_not_verified")
    if target_window is not None and selected_window is not None and not target_window_is_selected:
        warnings.append("selected_window_is_not_target_window")
    warnings.extend(interaction_activation_reasons)
    if target_window is not None and not same_window_open_supported:
        warnings.append("same_window_open_not_supported_by_local_backend")
    if processes and target_window is None:
        warnings.append("matstudio_process_without_usable_window")

    current_revision_loaded = bool(
        requested_target
        and matched_project_window
        and live_matching_window_count == 1
        and target_window_matches_requested_project_revision
        and target_window_pid_is_matstudio_process
        and target_entry is not None
        and target_entry.get("wrapper_integrity_verified") is True
        and target_entry.get("wrapper_workspace_matches_controller") is True
    )
    needs_reload = bool(requested_target and not current_revision_loaded)
    if target_window is None:
        if processes:
            recommended_tool = "material_studio_gui_status"
            recommended_action = "resolve_existing_matstudio_process_without_usable_window"
        else:
            recommended_tool = "material_studio_gui_launch"
            recommended_action = "explicitly_launch_or_activate_materials_studio_only_if_intended"
        ready_for_snapshot = False
        ready_for_open = False
    elif single_window_violation_reasons:
        recommended_tool = "material_studio_gui_status"
        recommended_action = "close_save_extra_matstudio_windows_then_retry_hotload"
        ready_for_snapshot = False
        ready_for_open = False
    elif resolvable_startup_dialog_entries and not unresolved_blocking_dialog_entries:
        recommended_tool = "material_studio_gui_open_structure"
        recommended_action = "open_current_structure_through_startup_dialog"
        ready_for_snapshot = True
        ready_for_open = True
    elif unresolved_blocking_dialog_entries:
        recommended_tool = "material_studio_gui_activate"
        recommended_action = "dismiss_startup_or_modal_dialog_then_retry_hotload"
        ready_for_snapshot = True
        ready_for_open = False
    elif workspace_context_mismatch:
        recommended_tool = "material_studio_live_session_preflight"
        recommended_action = "rerun_preflight_with_visible_wrapper_workspace_before_implicit_followup"
        ready_for_snapshot = True
        ready_for_open = True
    elif needs_reload:
        recommended_tool = "material_studio_gui_open_structure"
        recommended_action = "reload_requested_project_revision_in_gui"
        ready_for_snapshot = False
        ready_for_open = True
    elif needs_activation:
        recommended_tool = "material_studio_gui_activate"
        recommended_action = (
            "restore_and_activate_target_project_window"
            if target_window_is_minimized is True
            else "activate_target_project_window"
        )
        ready_for_snapshot = False
        ready_for_open = True
    elif not same_window_open_supported:
        recommended_tool = "material_studio_gui_copy_script_assist"
        recommended_action = "open_structure_in_existing_window_with_computer_use_or_manual_file_open_then_snapshot"
        ready_for_snapshot = True
        ready_for_open = False
    else:
        recommended_tool = "material_studio_gui_snapshot"
        recommended_action = "snapshot_target_project_window"
        ready_for_snapshot = True
        ready_for_open = True

    needs_single_window_resolution = bool(single_window_violation_reasons)
    needs_dialog_resolution = bool(unresolved_blocking_dialog_entries)
    startup_dialog_open_ready = bool(
        resolvable_startup_dialog_entries and not unresolved_blocking_dialog_entries
    )
    ready_for_snapshot = bool(ready_for_snapshot and not activation_required_before_capture_or_input)
    can_hotload_without_new_window = bool(
        target_window is not None
        and same_window_open_supported
        and not needs_single_window_resolution
        and not needs_dialog_resolution
    )
    ready_for_next_live_edit = bool(
        can_hotload_without_new_window
        and not needs_reload
        and not needs_activation
        and not workspace_context_mismatch
    )
    can_apply_current_revision_without_new_window = bool(
        can_hotload_without_new_window and not workspace_context_mismatch
    )
    if target_window is None:
        status = "matstudio_process_without_usable_window" if processes else "target_window_missing"
    elif needs_single_window_resolution:
        status = "single_window_policy_violation"
    elif needs_dialog_resolution:
        status = "modal_dialog_blocking_hotload"
    elif startup_dialog_open_ready:
        status = "startup_dialog_ready_for_same_window_open"
    elif workspace_context_mismatch:
        status = "workspace_context_mismatch"
    elif needs_reload:
        status = "requested_revision_not_loaded"
    elif needs_activation:
        status = "target_window_needs_activation"
    elif not same_window_open_supported:
        status = "same_window_open_unavailable"
    else:
        status = "ready_for_same_window_live_edit"

    payload_hint: dict[str, Any] = {
        "project_id": requested_project_id,
        "revision": requested_revision,
        "reuse_existing_window_only": True,
    }
    if workspace_context_mismatch:
        payload_hint.update(
            {
                "working_dir": recommended_working_dir,
                "project_id": workspace_context.get("recommended_project_id"),
                "revision": workspace_context.get("recommended_revision"),
            }
        )
    if recommended_tool == "material_studio_gui_open_structure":
        payload_hint["execution_mode"] = "execute"
        payload_hint["open_in_gui"] = True
    elif recommended_tool == "material_studio_gui_snapshot":
        payload_hint["take_snapshot"] = True
    elif recommended_tool == "material_studio_gui_activate":
        payload_hint["take_snapshot"] = True

    unrelated_process_ids = sorted(
        process.pid
        for process in processes
        if target_process_id is None or process.pid != target_process_id
    )
    unrelated_primary_window_entries = [
        entry
        for entry in primary_entries
        if target_process_id is None or entry.get("pid") != target_process_id
    ]
    if project_scoped_multi_instance_isolation:
        window_isolation_mode = "exact_project_target_process"
    elif not global_multiple_processes_detected and len(primary_entries) <= 1:
        window_isolation_mode = "global_single_instance"
    else:
        window_isolation_mode = "global_single_instance_violation"

    return {
        "status": status,
        "process_count": len(processes),
        "window_count": len(window_inventory),
        "matstudio_window_count": len(matstudio_window_entries),
        "ignored_non_matstudio_window_count": len(
            ignored_non_matstudio_window_entries
        ),
        "primary_window_count": len(primary_entries),
        "dialog_window_count": len(dialog_entries),
        "global_dialog_window_count": len(all_dialog_entries),
        "unrelated_dialog_window_count": len(unrelated_dialog_entries),
        "blocking_dialog_count": len(blocking_dialog_entries),
        "unresolved_blocking_dialog_count": len(unresolved_blocking_dialog_entries),
        "resolvable_startup_dialog_count": len(resolvable_startup_dialog_entries),
        "file_association_dialog_count": len(file_association_dialog_entries),
        "welcome_dialog_count": len(welcome_dialog_entries),
        "startup_dialog_count": len(startup_dialog_entries),
        "wrapper_window_count": len(wrapper_entries),
        "external_wrapper_window_count": len(external_wrapper_entries),
        "ambiguous_wrapper_window_count": len(ambiguous_wrapper_entries),
        "mcp_title_window_count": len(mcp_title_entries),
        "unmatched_mcp_title_window_count": unmatched_mcp_title_count,
        "requested_project_id": requested_project_id,
        "requested_revision": requested_revision,
        "matching_window_count": live_matching_window_count,
        "resolved_matching_window_count": matching_window_count,
        "target_window_matches_requested_project_revision": (
            target_window_matches_requested_project_revision
        ),
        "selected_window_handle": selected_window.handle if selected_window else None,
        "selected_window_title": selected_window.title if selected_window else None,
        "selected_process_id": selected_process_id,
        "selected_window_pid_is_matstudio_process": (
            selected_window_pid_is_matstudio_process
        ),
        "selected_window_project_id": selected_entry.get("project_id") if selected_entry else None,
        "selected_window_revision": selected_entry.get("revision") if selected_entry else None,
        "selected_window_wrapper_workspace_root": (
            selected_entry.get("wrapper_workspace_root") if selected_entry else None
        ),
        "selected_window_wrapper_workspace_matches_controller": (
            selected_entry.get("wrapper_workspace_matches_controller") if selected_entry else None
        ),
        "selected_window_has_project_metadata": bool(selected_entry and selected_entry.get("project_wrapper_metadata")),
        "target_window_handle": target_window.handle if target_window else None,
        "target_window_title": target_window.title if target_window else None,
        "target_window_pid_is_matstudio_process": (
            target_window_pid_is_matstudio_process
        ),
        "target_window_project_id": target_entry.get("project_id") if target_entry else None,
        "target_window_revision": target_entry.get("revision") if target_entry else None,
        "target_window_wrapper_workspace_root": (
            target_entry.get("wrapper_workspace_root") if target_entry else None
        ),
        "target_window_wrapper_workspace_matches_controller": (
            target_entry.get("wrapper_workspace_matches_controller") if target_entry else None
        ),
        "target_window_has_project_metadata": bool(target_entry and target_entry.get("project_wrapper_metadata")),
        "target_wrapper_integrity_verified": (
            target_entry.get("wrapper_integrity_verified")
            if target_entry
            else None
        ),
        "target_wrapper_integrity_status": (
            target_entry.get("wrapper_integrity_status")
            if target_entry
            else None
        ),
        "target_wrapper_identity_verified": (
            target_entry.get("wrapper_target_identity_verified")
            if target_entry
            else None
        ),
        "target_wrapper_identity_status": (
            target_entry.get("wrapper_target_identity_status")
            if target_entry
            else None
        ),
        "target_process_id": target_process_id,
        "target_process_primary_window_count": len(
            target_process_primary_entries
        ),
        "target_process_dialog_window_count": len(
            target_process_dialog_entries
        ),
        "exact_target_process_candidate": exact_target_process_candidate,
        "exact_target_process_isolated": exact_target_process_isolated,
        "project_scoped_multi_instance_isolation": (
            project_scoped_multi_instance_isolation
        ),
        "window_isolation_mode": window_isolation_mode,
        "global_multiple_processes_detected": (
            global_multiple_processes_detected
        ),
        "global_multiple_primary_windows_detected": (
            global_multiple_primary_windows_detected
        ),
        "unrelated_process_count": len(unrelated_process_ids),
        "unrelated_process_ids": unrelated_process_ids,
        "unrelated_primary_window_count": len(
            unrelated_primary_window_entries
        ),
        "target_window_is_selected": target_window_is_selected,
        "target_window_is_visible": target_window_is_visible,
        "target_window_is_minimized": target_window_is_minimized,
        "target_window_foreground_observed": target_window_foreground_observed,
        "target_window_is_foreground": target_window_is_foreground,
        "activation_reasons": activation_reasons,
        "interaction_activation_reasons": interaction_activation_reasons,
        "activation_required_before_capture_or_input": activation_required_before_capture_or_input,
        "matched_project_window": matched_project_window,
        "fallback_used": fallback_used,
        "workspace_context": workspace_context,
        "workspace_context_mismatch": workspace_context_mismatch,
        "recommended_working_dir": recommended_working_dir,
        "automatic_workspace_adoption_allowed": False,
        "same_window_open_supported": same_window_open_supported,
        "startup_dialog_open_supported": startup_dialog_open_supported,
        "startup_dialog_open_ready": startup_dialog_open_ready,
        "file_open_may_launch_new_instance": file_open_may_launch_new_instance,
        "single_window_policy": "reuse_existing_window_for_structure_hotload",
        "single_window_policy_ok": not single_window_violation_reasons,
        "single_window_violation_reasons": single_window_violation_reasons,
        "hotload_requires_existing_window": True,
        "auto_launch_during_open_allowed": False,
        "ready_for_same_window_open": bool(
            target_window is not None
            and same_window_open_supported
            and not single_window_violation_reasons
            and not unresolved_blocking_dialog_entries
        ),
        "same_window_required": True,
        "auto_launch_allowed": False,
        "can_hotload_without_new_window": can_hotload_without_new_window,
        "can_apply_current_revision_without_new_window": can_apply_current_revision_without_new_window,
        "ready_for_next_live_edit": ready_for_next_live_edit,
        "current_revision_loaded": current_revision_loaded,
        "needs_reload": needs_reload,
        "needs_activation": needs_activation,
        "needs_snapshot": status == "ready_for_same_window_live_edit",
        "needs_single_window_resolution": needs_single_window_resolution,
        "needs_dialog_resolution": needs_dialog_resolution,
        "ready_for_snapshot": ready_for_snapshot,
        "ready_for_open": ready_for_open,
        "recommended_tool": recommended_tool,
        "recommended_action": recommended_action,
        "payload_hint": {key: value for key, value in payload_hint.items() if value is not None},
        "warnings": warnings,
    }


def _is_primary_materials_studio_window_entry(entry: dict[str, Any]) -> bool:
    """Return true for top-level Materials Studio document/modeling frames."""

    if _is_materials_studio_dialog_entry(entry):
        return False
    title = str(entry.get("title") or "").strip().lower()
    if entry.get("project_wrapper_metadata"):
        return True
    return "materials studio" in title or "matstudio" in title


def _is_materials_studio_dialog_entry(entry: dict[str, Any]) -> bool:
    """Return true for transient Materials Studio dialogs, not model frames."""

    class_name = str(entry.get("class_name") or "").strip().lower()
    if class_name == "#32770":
        return True
    return _is_file_association_dialog_entry(entry)


def _is_startup_dialog_entry(entry: dict[str, Any]) -> bool:
    """Return true for known Materials Studio startup dialogs."""

    return (
        _is_file_association_dialog_entry(entry)
        or _is_welcome_dialog_entry(entry)
        or _is_new_project_dialog_entry(entry)
    )


def _is_file_association_dialog_entry(entry: dict[str, Any]) -> bool:
    """Return true for Materials Studio first-run file association dialogs."""

    title = str(entry.get("title") or "").strip().lower()
    return "materials studio file associations" in title


def _is_welcome_dialog_entry(entry: dict[str, Any]) -> bool:
    """Return true for Materials Studio welcome dialogs."""

    title = str(entry.get("title") or "").strip().lower()
    return title == "welcome to materials studio"


def _is_new_project_dialog_entry(entry: dict[str, Any]) -> bool:
    """Return true for the empty-project save dialog opened by the welcome page."""

    title = str(entry.get("title") or "").strip().lower()
    return title == "new project"


def _window_inventory_entry(window_inventory: list[dict[str, Any]], handle: int | None) -> dict[str, Any] | None:
    """Return the inventory entry for a window handle."""

    if handle is None:
        return None
    for entry in window_inventory:
        if entry.get("handle") == handle:
            return entry
    return None


def _safe_name(value: str, *, fallback: str) -> str:
    """生成安全名称。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe[:80] or fallback


def _safe_component(value: str) -> str:
    """Return an ASCII component safe for Materials Studio project paths."""

    return _safe_name(value, fallback="model")


def _project_name_from_window_title(title: str) -> str | None:
    """Return the Materials Studio project name prefix from a window title."""

    project_name = _raw_project_name_from_window_title(title)
    if (
        project_name is None
        or _safe_component(project_name) != project_name
        or title != f"{project_name} - Materials Studio"
    ):
        return None
    return project_name


def _raw_project_name_from_window_title(title: str) -> str | None:
    """Return the exact Materials Studio project name prefix without normalization."""

    suffix = " - Materials Studio"
    normalized = title.strip()
    if not normalized.endswith(suffix):
        return None
    project_name = normalized[: -len(suffix)].strip()
    return project_name or None


def _wrapper_project_path_provenance(
    project_path: Path,
    *,
    workspace_root: Path,
    allow_locked_attestation: bool = False,
) -> dict[str, Any]:
    """Verify one generated wrapper inside an explicitly trusted workspace."""

    trusted_root = workspace_root.expanduser().resolve()
    try:
        resolved_project = project_path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "verified": False,
            "status": "project_path_invalid",
            "project_path": str(project_path),
            "workspace_root": str(trusted_root),
            "error": str(exc),
        }

    project_name = resolved_project.stem
    project_dir = resolved_project.parent
    gui_projects_dir = project_dir.parent
    name_match = re.fullmatch(
        r"msmcp_r(?P<revision>\d{3,})_(?P<unique>[0-9a-f]{10})",
        project_name,
    )
    expected_project = project_dir / f"{project_name}.stp"
    path_shape_valid = bool(
        resolved_project.suffix.lower() == ".stp"
        and project_dir.name == project_name
        and _same_resolved_path(
            gui_projects_dir,
            trusted_root / "gui_projects",
        )
        and _same_resolved_path(resolved_project, expected_project)
        and _path_is_inside(trusted_root, resolved_project)
    )
    metadata_path = project_dir / "metadata.json"
    reasons: list[str] = []
    if name_match is None:
        reasons.append("project_name_not_strict_generated_revision_wrapper")
    if not path_shape_valid:
        reasons.append("project_path_not_in_trusted_wrapper_layout")
    if not resolved_project.is_file():
        reasons.append("project_file_missing")

    metadata: dict[str, Any] | None = None
    metadata_error: str | None = None
    if not metadata_path.is_file():
        reasons.append("metadata_missing")
    else:
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                metadata = payload
            else:
                reasons.append("metadata_not_object")
        except Exception as exc:
            reasons.append("metadata_read_error")
            metadata_error = str(exc)

    project_xml: ET.Element | None = None
    project_xml_error: str | None = None
    project_file_locked = False
    project_sha256_current: str | None = None
    project_size_current: int | None = None
    if resolved_project.is_file():
        try:
            project_xml = ET.parse(resolved_project).getroot()
            project_sha256_current, project_size_current = _sha256_file(
                resolved_project
            )
        except PermissionError as exc:
            project_file_locked = True
            project_xml_error = str(exc)
        except ET.ParseError as exc:
            reasons.append("project_xml_invalid")
            project_xml_error = str(exc)
        except OSError as exc:
            reasons.append("project_xml_read_error")
            project_xml_error = str(exc)
        else:
            if project_xml.tag != "Project":
                reasons.append("project_xml_root_invalid")
            if project_xml.findtext("./Version") != "20.1":
                reasons.append("project_xml_version_invalid")
            if (
                project_xml.findtext("./ViewRegistry/Frame/View/Type")
                != "SVViewer3D.Viewer3DControl"
            ):
                reasons.append("project_xml_viewer_binding_missing")

    attestation_valid = False
    identity_manifest_valid = False
    revision_identity_binding_valid = False
    revision_state_binding_valid = False
    source_current_matches_document = False
    project_xml_verification_status = (
        "metadata_attestation_required"
        if project_file_locked
        else "direct_project_xml_required"
    )
    document_sha256_current: str | None = None
    document_size_current: int | None = None
    source_sha256_current: str | None = None
    source_size_current: int | None = None
    source_path_current: Path | None = None
    identity_manifest_path = project_dir / "wrapper_identity.json"
    identity_manifest_sha256_current: str | None = None
    if metadata is not None:
        if metadata.get("project_name") != project_name:
            reasons.append("metadata_project_name_mismatch")
        try:
            metadata_revision = int(metadata.get("revision"))
            revision_valid = metadata_revision >= 0
        except (TypeError, ValueError):
            metadata_revision = None
            revision_valid = False
        if not revision_valid:
            reasons.append("metadata_revision_invalid")
        if (
            name_match is not None
            and metadata_revision != int(name_match.group("revision"))
        ):
            reasons.append("project_name_revision_metadata_mismatch")

        project_id = metadata.get("project_id")
        try:
            project_id_valid = (
                isinstance(project_id, str)
                and bool(project_id)
                and sanitize_project_id(project_id) == project_id
            )
        except ValueError:
            project_id_valid = False
        if not project_id_valid:
            reasons.append("metadata_project_id_invalid")

        document_name = metadata.get("document_name")
        document_path = (
            project_dir
            / f"{project_name}_Files"
            / "Documents"
            / str(document_name)
        )
        document_valid = bool(
            isinstance(document_name, str)
            and document_name
            and Path(document_name).name == document_name
            and _path_is_inside(project_dir, document_path)
            and document_path.is_file()
        )
        if not document_valid:
            reasons.append("wrapper_document_missing_or_invalid")
        elif name_match is not None:
            expected_document_stem = (
                f"model_r{name_match.group('revision')}_"
                f"{name_match.group('unique')}"
            )
            if Path(str(document_name)).stem != expected_document_stem:
                reasons.append("wrapper_document_revision_identity_mismatch")
            try:
                document_sha256_current, document_size_current = _sha256_file(
                    document_path
                )
            except OSError:
                reasons.append("wrapper_document_hash_unavailable")

        source_path = metadata.get("source_path")
        try:
            source_path_current = (
                Path(str(source_path)).expanduser().resolve()
                if isinstance(source_path, str) and source_path
                else None
            )
        except (OSError, RuntimeError, ValueError):
            source_path_current = None
        source_valid = bool(
            source_path_current is not None
            and _path_is_inside(trusted_root, source_path_current)
            and source_path_current.is_file()
        )
        if not source_valid:
            reasons.append("wrapper_source_missing_or_outside_workspace")
        else:
            try:
                source_sha256_current, source_size_current = _sha256_file(
                    source_path_current
                )
            except OSError:
                reasons.append("wrapper_source_hash_unavailable")
            source_sha256_matches_document = bool(
                document_sha256_current is not None
                and source_sha256_current is not None
                and source_sha256_current == document_sha256_current
            )
            source_size_matches_document = bool(
                document_size_current is not None
                and source_size_current is not None
                and source_size_current == document_size_current
            )
            source_current_matches_document = bool(
                source_sha256_matches_document
                and source_size_matches_document
            )
            if not source_sha256_matches_document:
                reasons.append("wrapper_source_document_sha256_mismatch")
            if not source_size_matches_document:
                reasons.append("wrapper_source_document_size_mismatch")

        revision_payload: dict[str, Any] | None = None
        if project_id_valid and revision_valid:
            revision_path = (
                trusted_root
                / str(project_id)
                / "revisions"
                / f"r{metadata_revision:03d}_model_spec.json"
            )
            try:
                candidate = json.loads(
                    revision_path.read_text(encoding="utf-8")
                )
                if isinstance(candidate, dict):
                    revision_payload = candidate
            except Exception:
                revision_payload = None
            expected_output_dir = (
                trusted_root
                / str(project_id)
                / "outputs"
                / f"r{metadata_revision:03d}"
            )
            revision_identity_binding_valid = bool(
                revision_payload is not None
                and revision_payload.get("project_id") == project_id
                and revision_payload.get("revision") == metadata_revision
                and source_path_current is not None
                and _path_is_inside(
                    expected_output_dir,
                    source_path_current,
                )
            )
            revision_state_binding_valid = bool(
                revision_identity_binding_valid
                and source_current_matches_document
            )
        if not revision_identity_binding_valid:
            reasons.append("wrapper_revision_identity_binding_invalid")
        elif not source_current_matches_document:
            reasons.append("wrapper_revision_source_digest_mismatch")
        if not revision_state_binding_valid:
            reasons.append("wrapper_revision_state_binding_invalid")

        project_digest = metadata.get("project_file_sha256")
        document_digest = metadata.get("document_sha256")
        project_size_attested = metadata.get("project_file_size_bytes")
        document_size_attested = metadata.get("document_size_bytes")
        base_attestation_valid = bool(
            isinstance(project_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", project_digest)
            and isinstance(document_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", document_digest)
            and isinstance(project_size_attested, int)
            and project_size_attested > 0
            and isinstance(document_size_attested, int)
            and document_size_attested > 0
        )
        wrapper_schema_version = metadata.get("wrapper_schema_version")
        wrapper_profile = metadata.get("wrapper_profile")
        if (
            wrapper_schema_version == 3
            and wrapper_profile
            == "materials_studio_20_1_project_wrapper_v2"
        ):
            source_digest = metadata.get("source_sha256")
            source_size_attested = metadata.get("source_size_bytes")
            identity_digest = metadata.get("identity_manifest_sha256")
            identity_size_attested = metadata.get(
                "identity_manifest_size_bytes"
            )
            source_attestation_valid = bool(
                isinstance(source_digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", source_digest)
                and isinstance(source_size_attested, int)
                and source_size_attested > 0
            )
            if not source_attestation_valid:
                reasons.append("wrapper_source_attestation_invalid")
            elif (
                source_digest != source_sha256_current
                or source_size_attested != source_size_current
            ):
                reasons.append("wrapper_source_attestation_current_mismatch")

            identity: dict[str, Any] | None = None
            if metadata.get("identity_manifest_name") != identity_manifest_path.name:
                reasons.append("wrapper_identity_manifest_name_invalid")
            elif not identity_manifest_path.is_file():
                reasons.append("wrapper_identity_manifest_missing")
            else:
                try:
                    identity_payload = json.loads(
                        identity_manifest_path.read_text(encoding="utf-8")
                    )
                    if isinstance(identity_payload, dict):
                        identity = identity_payload
                    else:
                        reasons.append("wrapper_identity_manifest_not_object")
                    (
                        identity_manifest_sha256_current,
                        identity_manifest_size_current,
                    ) = _sha256_file(identity_manifest_path)
                except Exception:
                    identity_manifest_size_current = None
                    reasons.append("wrapper_identity_manifest_read_error")
            if identity is not None:
                expected_identity = {
                    "identity_schema_version": 1,
                    "identity_profile": (
                        "materials_studio_revision_wrapper_identity_v1"
                    ),
                    "project_name": project_name,
                    "project_id": project_id,
                    "revision": metadata_revision,
                    "source_path": str(source_path_current)
                    if source_path_current is not None
                    else None,
                    "source_sha256": source_digest,
                    "source_size_bytes": source_size_attested,
                    "document_name": document_name,
                    "document_sha256": document_sha256_current,
                    "document_size_bytes": document_size_current,
                    "project_file_sha256": project_digest,
                    "project_file_size_bytes": project_size_attested,
                }
                identity_manifest_valid = bool(
                    identity == expected_identity
                    and isinstance(identity_digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", identity_digest)
                    and identity_digest == identity_manifest_sha256_current
                    and isinstance(identity_size_attested, int)
                    and identity_size_attested > 0
                    and identity_size_attested == identity_manifest_size_current
                )
            if not identity_manifest_valid:
                reasons.append("wrapper_identity_manifest_binding_invalid")
            attestation_valid = bool(
                base_attestation_valid
                and source_attestation_valid
                and identity_manifest_valid
                and revision_identity_binding_valid
            )
        elif (
            wrapper_schema_version == 2
            and wrapper_profile
            == "materials_studio_20_1_project_wrapper_v1"
        ):
            attestation_valid = bool(
                base_attestation_valid and revision_identity_binding_valid
            )
        else:
            reasons.append("wrapper_schema_or_profile_unsupported")
        if not attestation_valid:
            reasons.append("wrapper_attestation_missing_or_invalid")
        if (
            document_sha256_current is not None
            and document_digest != document_sha256_current
        ):
            reasons.append("wrapper_document_sha256_mismatch")
        if (
            document_size_current is not None
            and document_size_attested != document_size_current
        ):
            reasons.append("wrapper_document_size_mismatch")

        if project_file_locked:
            if attestation_valid and allow_locked_attestation:
                project_xml_verification_status = (
                    "metadata_attested_current_project_lock"
                )
            elif attestation_valid:
                reasons.append(
                    "locked_project_not_allowed_for_target_verification"
                )
            else:
                reasons.append("locked_project_without_valid_attestation")
        elif project_xml is not None:
            project_xml_verification_status = "direct_project_xml_verified"
            if project_digest != project_sha256_current:
                reasons.append("project_file_sha256_mismatch")
            if project_size_attested != project_size_current:
                reasons.append("project_file_size_mismatch")
            if isinstance(document_name, str):
                document_url = project_xml.findtext(
                    "./DocumentManager/Document/URL"
                )
                if document_url not in {
                    f".\\{document_name}",
                    f"./{document_name}",
                }:
                    reasons.append("project_xml_document_url_mismatch")

    reasons = _unique_strings(reasons)
    source_drift_reason_codes = {
        "wrapper_source_document_sha256_mismatch",
        "wrapper_source_document_size_mismatch",
        "wrapper_source_attestation_current_mismatch",
        "wrapper_revision_source_digest_mismatch",
        "wrapper_revision_state_binding_invalid",
    }
    target_identity_reason_codes = [
        reason
        for reason in reasons
        if reason not in source_drift_reason_codes
    ]
    target_identity_verified = not target_identity_reason_codes
    verified = not reasons
    return {
        "verified": verified,
        "status": "verified_revision_wrapper" if verified else "unverified_wrapper",
        "reason_codes": reasons,
        "target_identity_verified": target_identity_verified,
        "target_identity_status": (
            "verified_revision_reload_target"
            if target_identity_verified
            else "unverified_reload_target"
        ),
        "target_identity_reason_codes": target_identity_reason_codes,
        "project_name": project_name,
        "project_path": str(resolved_project),
        "metadata_path": str(metadata_path),
        "workspace_root": str(trusted_root),
        "metadata": metadata if verified else None,
        "metadata_error": metadata_error,
        "project_xml_error": project_xml_error,
        "project_file_locked": project_file_locked,
        "project_xml_verification_status": project_xml_verification_status,
        "wrapper_attestation_valid": attestation_valid,
        "wrapper_identity_manifest_valid": identity_manifest_valid,
        "wrapper_revision_state_binding_valid": (
            revision_state_binding_valid
        ),
        "wrapper_revision_identity_binding_valid": (
            revision_identity_binding_valid
        ),
        "source_current_matches_document": source_current_matches_document,
        "legacy_revision_state_binding_valid": revision_state_binding_valid,
        "project_sha256_current": project_sha256_current,
        "document_sha256_current": document_sha256_current,
        "source_sha256_current": source_sha256_current,
        "source_path_current": (
            str(source_path_current) if source_path_current is not None else None
        ),
        "identity_manifest_path": str(identity_manifest_path),
        "identity_manifest_sha256_current": (
            identity_manifest_sha256_current
        ),
    }


def _source_wrapper_auto_save_provenance(
    *,
    source_window: WindowInfo,
    target_project_path: Path,
    trusted_workspace_roots: tuple[Path, ...],
) -> dict[str, Any]:
    """Authorize save-current only within an explicitly trusted workspace."""

    trusted_roots = tuple(
        root.expanduser().resolve()
        for root in trusted_workspace_roots
    )
    matching_roots = [
        root
        for root in trusted_roots
        if _path_is_inside(root / "gui_projects", target_project_path)
    ]
    target = (
        _wrapper_project_path_provenance(
            target_project_path,
            workspace_root=matching_roots[0],
        )
        if len(matching_roots) == 1
        else {
            "verified": False,
            "status": "target_workspace_untrusted",
            "reason_codes": ["target_workspace_untrusted_or_ambiguous"],
            "project_path": str(target_project_path),
        }
    )
    source_project_name = _raw_project_name_from_window_title(source_window.title)
    source: dict[str, Any] | None = None
    reasons: list[str] = []
    if target.get("verified") is not True:
        reasons.append("target_wrapper_provenance_unverified")
    if source_project_name is None:
        reasons.append("source_window_not_exact_project_title")
    elif source_window.title != f"{source_project_name} - Materials Studio":
        reasons.append("source_window_title_not_raw_exact")
    elif target.get("workspace_root"):
        source_project_path = (
            Path(str(target["workspace_root"]))
            / "gui_projects"
            / source_project_name
            / f"{source_project_name}.stp"
        )
        source = _wrapper_project_path_provenance(
            source_project_path,
            workspace_root=Path(str(target["workspace_root"])),
            allow_locked_attestation=True,
        )
        if source.get("verified") is not True:
            reasons.append("source_wrapper_provenance_unverified")
        elif source.get("project_name") != source_project_name:
            reasons.append("source_window_project_name_mismatch")
        elif not _same_resolved_path(
            Path(str(source["workspace_root"])),
            Path(str(target["workspace_root"])),
        ):
            reasons.append("source_target_workspace_mismatch")

    auto_save_allowed = not reasons and source is not None
    return {
        "auto_save_allowed": auto_save_allowed,
        "status": (
            "verified_same_workspace_revision_wrapper"
            if auto_save_allowed
            else "auto_save_not_authorized"
        ),
        "reason_codes": reasons,
        "source_window": source_window.to_dict(),
        "source_project_name": source_project_name,
        "source_wrapper": source,
        "target_wrapper": target,
        "trusted_workspace_roots": [str(root) for root in trusted_roots],
    }


def _platform_default_workspace_root() -> Path:
    """Return the per-user workspace without honoring an override environment variable."""

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (Path(base) / "materials_studio_mcp" / "workspace").expanduser().resolve()
    return (Path.home() / ".local" / "share" / "materials_studio_mcp" / "workspace").resolve()


def _trusted_wrapper_workspace_roots(controller_workspace_root: Path) -> list[tuple[Path, str]]:
    """Return the bounded workspace roots allowed for read-only wrapper provenance lookup."""

    candidates: list[tuple[Path, str]] = [(controller_workspace_root.resolve(), "controller_workspace")]
    configured = os.environ.get("MATERIAL_STUDIO_MCP_WORKSPACE")
    if configured:
        candidates.append((Path(configured).expanduser().resolve(), "environment_workspace"))
    candidates.append((_platform_default_workspace_root(), "platform_default_workspace"))

    deduplicated: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for root, trust_basis in candidates:
        key = os.path.normcase(str(root.resolve()))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append((root.resolve(), trust_basis))
    return deduplicated


def _same_resolved_path(left: Path, right: Path) -> bool:
    """Compare resolved paths using the platform's path case rules."""

    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _path_is_inside(root: Path, path: Path) -> bool:
    """Return whether a path resolves inside a root without raising."""

    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return path_resolved == root_resolved or root_resolved in path_resolved.parents


def _ensure_inside(root: Path, path: Path) -> None:
    """确保路径在根目录内。"""
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    if path_resolved != root_resolved and root_resolved not in path_resolved.parents:
        raise GuiError("GUI 路径逃逸工作区根目录")


def _resolve_matstudio_exe() -> Path | None:
    """Resolve MatStudio.exe without COM automation."""

    for key in ("MATERIAL_STUDIO_GUI", "MATERIAL_STUDIO_EXE"):
        configured = os.environ.get(key)
        if configured:
            path = Path(configured).expanduser()
            if path.exists() and path.is_file():
                return path.resolve()

    runner = os.environ.get("MATERIAL_STUDIO_RUNNER")
    if runner:
        runner_path = Path(runner).expanduser()
        for parent in runner_path.parents:
            candidate = parent / "bin" / "MatStudio.exe"
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        try:
            root = runner_path.parents[3]
            candidate = root / "bin" / "MatStudio.exe"
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        except IndexError:
            pass

    path_candidate = shutil.which("MatStudio.exe")
    if path_candidate:
        candidate = Path(path_candidate).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    for candidate in _common_matstudio_exe_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _common_matstudio_exe_candidates() -> list[Path]:
    """Return likely BIOVIA Materials Studio GUI executable locations."""

    drives: list[str] = []
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        drives.append(system_drive.rstrip("\\/"))
    drives.extend(["C:", "D:"])

    roots: list[Path] = []
    seen_roots: set[str] = set()
    for drive in drives:
        for program_dir in ("Program Files", "Program Files (x86)"):
            root = Path(f"{drive}\\{program_dir}\\BIOVIA")
            key = str(root).lower()
            if key not in seen_roots:
                seen_roots.add(key)
                roots.append(root)

    candidates: list[Path] = []
    seen_candidates: set[str] = set()
    common_versions = (
        "Materials Studio 20.1",
        "Materials Studio 2020",
        "Materials Studio 20.0",
        "Materials Studio 2026",
        "Materials Studio 2025",
        "Materials Studio 2024",
        "Materials Studio 2023",
    )
    for root in roots:
        version_dirs = [root / version for version in common_versions]
        if root.exists():
            try:
                version_dirs.extend(sorted(root.glob("Materials Studio*"), reverse=True))
            except OSError:
                pass
        for version_dir in version_dirs:
            candidate = version_dir / "bin" / "MatStudio.exe"
            key = str(candidate).lower()
            if key not in seen_candidates:
                seen_candidates.add(key)
                candidates.append(candidate)
    return candidates


def _window_class(hwnd: int) -> str | None:
    """Return a Win32 top-level window class name."""

    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(256)
    if ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buffer, 256):
        return buffer.value
    return None


def _window_area(window: WindowInfo) -> int:
    """Return window area for priority sorting."""

    if not window.rect:
        return 0
    left, top, right, bottom = window.rect
    return max(0, right - left) * max(0, bottom - top)


def _window_rect_looks_minimized(rect: tuple[int, int, int, int] | None) -> bool:
    """Recognize the Win32 minimized-window sentinel without rejecting valid negative monitors."""

    if rect is None:
        return False
    left, top, _right, _bottom = rect
    return left <= -30000 and top <= -30000


def _window_priority(window: WindowInfo, *, foreground_handle: int | None = None) -> tuple[int, int, int, str]:
    """Prefer real Materials Studio frame windows over transient dialogs."""

    title = window.title.lower()
    class_name = (window.class_name or "").lower()
    foreground_rank = 0 if foreground_handle is not None and window.handle == foreground_handle else 1
    if "file associations" in title:
        title_rank = 90
    elif class_name == "#32770":
        title_rank = 80
    elif " - materials studio" in title or title.startswith("untitled - materials studio"):
        title_rank = 0
    elif title == "materials studio":
        title_rank = 1
    elif "materials studio" in title:
        title_rank = 2
    else:
        title_rank = 10
    return (title_rank, foreground_rank, -_window_area(window), window.title)


def _select_live_matstudio_window(
    *,
    processes: list[ProcessInfo],
    windows: list[WindowInfo],
    preferred: WindowInfo | None,
) -> WindowInfo | None:
    """Select only a window owned by the live MatStudio process inventory."""

    process_ids = {process.pid for process in processes}
    if preferred is not None and preferred.pid in process_ids:
        return preferred
    for window in windows:
        if window.pid in process_ids:
            return window
    return None


def _find_windows(*, title: str | None = None, pid: int | None = None) -> list[WindowInfo]:
    """Find visible top-level windows by exact title and/or process id."""

    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    matches: list[WindowInfo] = []
    foreground_handle = _foreground_window_handle()
    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value
        if title is not None and window_title != title:
            return True
        pid_value = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
        window_pid = int(pid_value.value)
        if pid is not None and window_pid != pid:
            return True
        matches.append(
            WindowInfo(
                handle=int(hwnd),
                title=window_title,
                pid=window_pid,
                rect=_window_rect(hwnd),
                class_name=_window_class(hwnd),
                is_visible=True,
                is_minimized=bool(user32.IsIconic(hwnd)),
                is_foreground=(
                    int(hwnd) == foreground_handle if foreground_handle is not None else None
                ),
            )
        )
        return True

    user32.EnumWindows(enum_proc_type(enum_proc), 0)
    return sorted(matches, key=lambda window: _window_priority(window, foreground_handle=foreground_handle))


def _window_owner_chain(window_handle: int, *, max_depth: int = 8) -> list[int]:
    """Return the bounded Win32 owner chain for a top-level or owned dialog."""

    if os.name != "nt" or window_handle <= 0:
        return []
    user32 = ctypes.windll.user32
    chain: list[int] = []
    seen = {window_handle}
    current = window_handle
    for _ in range(max_depth):
        owner = int(user32.GetWindow(ctypes.c_void_p(current), 4) or 0)  # GW_OWNER
        if owner <= 0 or owner in seen:
            break
        chain.append(owner)
        seen.add(owner)
        current = owner
    return chain


def _window_handle_exists(window_handle: int) -> bool:
    """Return whether a Win32 window handle is still valid."""

    if os.name != "nt" or window_handle <= 0:
        return False
    return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(window_handle)))


def _wait_for_project_window(
    *,
    pid: int | None,
    expected_project_name: str,
    timeout_seconds: float,
) -> WindowInfo | None:
    """Wait for the exact wrapper project title in the requested process."""

    expected_title = f"{expected_project_name} - Materials Studio"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for window in _find_windows(pid=pid):
            if window.title == expected_title:
                return window
        time.sleep(0.25)
    return None


def _foreground_window_handle() -> int | None:
    """Return the current foreground window handle when available."""

    if os.name != "nt":
        return None
    try:
        handle = int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None
    return handle or None


def _send_ctrl_open_shortcut() -> None:
    """Send Ctrl+O to the currently active Materials Studio window."""

    _press_virtual_key_chord([0x11, 0x4F])  # VK_CONTROL, O


def _press_virtual_key_chord(virtual_keys: list[int], *, pause_seconds: float = 0.08) -> None:
    """Press and release a Win32 virtual-key chord."""

    if os.name != "nt":
        raise GuiError("Win32 keyboard input is only available on Windows.")
    user32 = ctypes.windll.user32
    keyeventf_keyup = 0x0002
    for virtual_key in virtual_keys:
        user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(pause_seconds)
    for virtual_key in reversed(virtual_keys):
        user32.keybd_event(virtual_key, 0, keyeventf_keyup, 0)
        time.sleep(pause_seconds)


def _find_file_open_dialog(
    *,
    pid: int | None,
    timeout_seconds: float,
    owner_root_handle: int | None = None,
) -> WindowInfo | None:
    """Find a common file-open dialog owned by Materials Studio."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        dialogs = _owned_file_open_dialogs(
            pid=pid,
            owner_root_handle=owner_root_handle,
        )
        if dialogs:
            return dialogs[0]
        time.sleep(0.25)
    return None


def _owned_file_open_dialogs(
    *,
    pid: int | None,
    owner_root_handle: int | None,
) -> list[WindowInfo]:
    """Return current file-open dialogs bound to one process and owner tree."""

    dialogs = [
        window
        for window in _find_windows(pid=pid)
        if _looks_like_file_open_dialog(window)
    ]
    if owner_root_handle is None:
        return dialogs
    return [
        window
        for window in dialogs
        if owner_root_handle in _window_owner_chain(window.handle)
    ]


def _wait_for_owned_file_open_dialogs_absent(
    *,
    pid: int | None,
    owner_root_handle: int | None,
    timeout_seconds: float,
    quiet_period_seconds: float = 0.75,
    poll_interval_seconds: float = 0.1,
) -> bool:
    """Wait until the owner tree remains picker-free for a stable quiet period."""

    deadline = time.monotonic() + timeout_seconds
    quiet_started_at: float | None = None
    while time.monotonic() < deadline:
        dialogs = _owned_file_open_dialogs(
            pid=pid,
            owner_root_handle=owner_root_handle,
        )
        now = time.monotonic()
        if dialogs:
            quiet_started_at = None
        elif quiet_started_at is None:
            quiet_started_at = now
        elif now - quiet_started_at >= quiet_period_seconds:
            return True
        time.sleep(poll_interval_seconds)
    return False


def _normalized_windows_path_text(value: str) -> str:
    """Normalize a Windows path string for exact file-dialog binding checks."""

    return os.path.normcase(os.path.normpath(str(Path(value).expanduser())))


def _file_dialog_path_matches(observed: str | None, expected: str) -> bool:
    """Return whether a file-picker observation identifies the exact target path."""

    if not observed:
        return False
    return _normalized_windows_path_text(observed) == _normalized_windows_path_text(
        expected
    )


def _bind_expected_file_open_path(
    dialog_handle: int,
    *,
    expected_path: str,
) -> dict[str, Any]:
    """Verify or refill one picker so the submitted path is never inherited blindly."""

    observed_before = _visible_filename_edit_text(
        dialog_handle,
        expected=expected_path,
    )
    if _file_dialog_path_matches(observed_before, expected_path):
        return {
            "ok": True,
            "expected_path": expected_path,
            "observed_path_before": observed_before,
            "observed_path_after": observed_before,
            "path_refilled": False,
            "path_observed_exact": True,
            "path_refill_acknowledged": False,
            "verification_source": "existing_filename_exact_match",
            "filename_field": None,
        }

    field_result = _set_common_dialog_filename(dialog_handle, expected_path)
    observed_after = _visible_filename_edit_text(
        dialog_handle,
        expected=expected_path,
    )
    observed_exact = _file_dialog_path_matches(observed_after, expected_path)
    setter_acknowledged = bool(
        not field_result.get("verification_warning")
        and (
            field_result.get("ok") is True
            or any(
                isinstance(item, dict)
                and isinstance(item.get("result"), dict)
                and item["result"].get("ok") is True
                for item in field_result.get("attempted", [])
            )
        )
    )
    return {
        "ok": observed_exact or setter_acknowledged,
        "expected_path": expected_path,
        "observed_path_before": observed_before,
        "observed_path_after": observed_after,
        "path_refilled": True,
        "path_observed_exact": observed_exact,
        "path_refill_acknowledged": setter_acknowledged,
        "verification_source": (
            "filename_exact_match_after_refill"
            if observed_exact
            else "bounded_filename_refill_acknowledged"
            if setter_acknowledged
            else "unverified"
        ),
        "filename_field": field_result,
    }


def _submit_current_file_open_dialog(
    *,
    pid: int | None,
    owner_root_handle: int | None,
    initial_dialog: WindowInfo,
    expected_path: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Submit the current owned picker, tolerating native HWND recreation."""

    attempts: list[dict[str, Any]] = []
    initial_handle = initial_dialog.handle
    for attempt in range(1, max_attempts + 1):
        current = _find_file_open_dialog(
            pid=pid,
            timeout_seconds=2.0,
            owner_root_handle=owner_root_handle,
        )
        if current is None:
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "dialog_not_found",
                }
            )
            continue
        owner_chain = _window_owner_chain(current.handle)
        path_binding = _bind_expected_file_open_path(
            current.handle,
            expected_path=expected_path,
        )
        if not path_binding.get("ok"):
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "target_path_not_verified",
                    "dialog": current.to_dict(),
                    "owner_chain": owner_chain,
                    "path_binding": path_binding,
                }
            )
            continue
        try:
            submission = _click_dialog_ok(current.handle)
        except GuiError as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "stale_dialog_handle",
                    "dialog": current.to_dict(),
                    "owner_chain": owner_chain,
                    "path_binding": path_binding,
                    "error": str(exc),
                }
            )
            continue
        dialogs_absent = _wait_for_owned_file_open_dialogs_absent(
            pid=pid,
            owner_root_handle=owner_root_handle,
            timeout_seconds=12.0,
        )
        attempts.append(
            {
                "attempt": attempt,
                "status": "submitted" if dialogs_absent else "dialog_remained_open",
                "dialog": current.to_dict(),
                "owner_chain": owner_chain,
                "path_binding": path_binding,
                "submission": submission,
                "dialogs_absent": dialogs_absent,
            }
        )
        if dialogs_absent:
            return {
                "ok": True,
                "initial_dialog_handle": initial_handle,
                "submitted_dialog_handle": current.handle,
                "dialog_handle_recreated": current.handle != initial_handle,
                "dialog": current.to_dict(),
                "owner_chain": owner_chain,
                "expected_path": expected_path,
                "expected_path_verified": True,
                "expected_path_observed": (
                    path_binding.get("path_observed_exact") is True
                ),
                "path_binding": path_binding,
                "submission": submission,
                "dialogs_absent": True,
                "attempts": attempts,
            }
    raise GuiError(
        "The owned Materials Studio File/Open dialog could not be submitted after "
        f"{max_attempts} bounded attempts: {attempts}"
    )


def _looks_like_file_open_dialog(window: WindowInfo) -> bool:
    """Return true when a top-level dialog appears to be a file-open dialog."""

    title = window.title.strip().lower()
    if (
        "file associations" in title
        or "welcome to materials studio" in title
    ):
        return False
    if (window.class_name or "").lower() != "#32770":
        return False
    if any(
        pattern in title
        for pattern in (
            "save",
            "export",
            "publish",
            "backup",
            "upload",
            "download",
        )
    ):
        return False
    title_patterns = ("open", "select", "browse", "choose", "打开", "选择")
    ascii_title_tokens = set(re.findall(r"[a-z]+", title))
    if (
        ascii_title_tokens.intersection(
            {"open", "select", "browse", "choose", "import", "load"}
        )
        or any(pattern in title for pattern in title_patterns[-2:])
    ):
        return _dialog_has_file_path_controls(window.handle)
    return False


def _looks_like_non_open_file_dialog(window: WindowInfo) -> bool:
    """Return true for a path dialog that lacks positive File/Open semantics."""

    return bool(
        (window.class_name or "").lower() == "#32770"
        and not _looks_like_file_open_dialog(window)
        and _dialog_has_file_path_controls(window.handle)
    )


def _dialog_has_file_path_controls(dialog_handle: int) -> bool:
    """Return true when a dialog exposes standard filename controls."""

    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    for control_id in (0x0480, 0x047C, 0x047D, 0x047E):
        if user32.GetDlgItem(ctypes.c_void_p(dialog_handle), control_id):
            return True
    editable = any(
        (_window_class(child) or "").lower()
        in {"edit", "combobox", "comboboxex32"}
        for child in _descendant_windows(dialog_handle)
    )
    return editable and bool(
        user32.GetDlgItem(ctypes.c_void_p(dialog_handle), 1)
    )


def _set_common_dialog_filename(dialog_handle: int, path_text: str) -> dict[str, Any]:
    """Set the filename field in a classic or Explorer-style common dialog."""

    if os.name != "nt":
        raise GuiError("Win32 file dialogs are only available on Windows.")
    paste_attempts: list[dict[str, Any]] = []
    for _attempt in range(3):
        paste_result = _paste_text_into_common_dialog_filename(dialog_handle, path_text)
        paste_attempts.append(paste_result)
        if paste_result.get("ok"):
            return {**paste_result, "attempts": paste_attempts}
        time.sleep(0.25)
    user32 = ctypes.windll.user32
    attempted: list[dict[str, Any]] = []
    for control_id in (0x0480, 0x047C, 0x047D, 0x047E):
        control = int(user32.GetDlgItem(ctypes.c_void_p(dialog_handle), control_id) or 0)
        if not control:
            continue
        result = _set_control_or_child_text(control, path_text)
        attempted.append({"control_id": control_id, "handle": control, "result": result})
        if result.get("ok"):
            return {
                "method": "dialog_control_id",
                "control_id": control_id,
                "handle": control,
                "attempted": attempted,
                "paste_attempts": paste_attempts,
            }

    for child in _descendant_windows(dialog_handle):
        class_name = (_window_class(child) or "").lower()
        if class_name not in {"edit", "combobox"}:
            continue
        result = _set_control_or_child_text(child, path_text)
        attempted.append({"class_name": class_name, "handle": child, "result": result})
        if result.get("ok"):
            return {
                "method": "dialog_child_scan",
                "class_name": class_name,
                "handle": child,
                "attempted": attempted,
                "paste_attempts": paste_attempts,
            }
    if paste_attempts:
        return {
            "ok": True,
            "method": "clipboard_paste_unverified",
            "verification_warning": "filename_field_text_was_not_readable_after_paste",
            "paste_attempts": paste_attempts,
            "attempted": attempted,
        }
    raise GuiError("Could not find a writable filename control in the Materials Studio File/Open dialog.")


def _open_project_from_startup_dialogs(
    *,
    pid: int | None,
    source_window: WindowInfo,
    path: Path,
    source_wrapper_provenance: dict[str, Any],
) -> dict[str, Any] | None:
    """Open a project through the Materials Studio welcome page when present."""

    dialogs = [
        window
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and source_window.handle in _window_owner_chain(window.handle)
    ]
    if not dialogs:
        return None

    handled: list[dict[str, Any]] = []
    for dialog in dialogs:
        if dialog.title.strip().lower() != "new project":
            continue
        cancellation = _cancel_dialog(
            dialog.handle,
            pid=pid,
            owner_root_handle=source_window.handle,
            dialog_title=dialog.title,
        )
        handled.append(
            {
                "action": "cancel_empty_new_project_dialog",
                "dialog": dialog.to_dict(),
                "cancellation": cancellation,
            }
        )

    welcome_dialogs = [
        window
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and window.title.strip().lower() == "welcome to materials studio"
        and source_window.handle in _window_owner_chain(window.handle)
    ]
    if welcome_dialogs:
        welcome = welcome_dialogs[0]
        welcome_result = _open_project_from_welcome_dialog(welcome.handle, str(path), pid=pid)
        closed = _wait_for_window_absent(welcome.handle, timeout_seconds=12.0)
        if not closed:
            raise GuiError("Materials Studio welcome dialog did not close after selecting the MCP project.")
        handled.append(
            {
                "action": "open_existing_project_from_welcome_dialog",
                "dialog": welcome.to_dict(),
                "field": welcome_result,
            }
        )
        handled.extend(
            _resolve_same_window_open_prompts(
                pid=pid,
                source_window=source_window,
                path_text=str(path),
                source_wrapper_provenance=source_wrapper_provenance,
                timeout_seconds=60.0,
            )
        )
        return {
            "method": "existing_window_welcome_dialog",
            "path": str(path),
            "window": source_window.to_dict(),
            "handled_prompts": handled,
            "source_wrapper_provenance": source_wrapper_provenance,
        }

    remaining = [
        window.to_dict()
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and "file associations" not in window.title.strip().lower()
        and source_window.handle in _window_owner_chain(window.handle)
    ]
    if remaining:
        raise GuiError(f"Unhandled Materials Studio dialog before same-window open: {remaining}")
    return None


def _open_project_from_welcome_dialog(
    dialog_handle: int,
    path_text: str,
    *,
    pid: int | None,
) -> dict[str, Any]:
    """Select and submit an existing MCP project in the Materials Studio welcome dialog."""

    if os.name != "nt":
        raise GuiError("Materials Studio welcome-dialog automation is only available on Windows.")
    controls = _dialog_controls(dialog_handle)
    open_button = next(
        (
            control
            for control in controls
            if control.get("class") == "Button"
            and "open an existing project" in str(control.get("text") or "").lower()
        ),
        None,
    )
    browse_button = next(
        (
            control
            for control in controls
            if control.get("class") == "Button"
            and "browse" in str(control.get("text") or "").lower()
        ),
        None,
    )
    edit_control = next((control for control in controls if control.get("class") == "Edit"), None)
    ok_button = next(
        (
            control
            for control in controls
            if control.get("class") == "Button"
            and str(control.get("text") or "").strip().lower() == "ok"
        ),
        None,
    )
    if open_button is None or browse_button is None or edit_control is None or ok_button is None:
        raise GuiError("Materials Studio welcome dialog did not expose the expected existing-project controls.")

    _click_button_handle(int(open_button["handle"]))
    browse_submission = _post_button_click_handle(int(browse_button["handle"]))
    browse_dialog = _find_file_open_dialog(
        pid=pid,
        timeout_seconds=10.0,
        owner_root_handle=dialog_handle,
    )
    if browse_dialog is None:
        raise GuiError("Materials Studio welcome dialog did not expose its existing-project file picker.")
    browse_dialog_owner_chain = _window_owner_chain(browse_dialog.handle)
    picker_submission = _submit_current_file_open_dialog(
        pid=pid,
        owner_root_handle=dialog_handle,
        initial_dialog=browse_dialog,
        expected_path=path_text,
    )
    path_binding = picker_submission.get("path_binding")
    field_result = (
        path_binding.get("filename_field")
        if isinstance(path_binding, dict)
        else None
    )
    picker_closed = bool(picker_submission.get("dialogs_absent"))
    submission_attempts = [
        {
            "attempt": 1,
            "filename_field": field_result,
            "path_binding": path_binding,
            "picker_submission": picker_submission,
            "picker_closed": picker_closed,
        }
    ]
    expected_project_name = Path(path_text).stem
    expected_window = _wait_for_project_window(
        pid=pid,
        expected_project_name=expected_project_name,
        timeout_seconds=5.0,
    )
    welcome_auto_submitted = expected_window is not None or not _window_handle_exists(
        dialog_handle
    )
    visible_path = ""
    welcome_submission: dict[str, Any] | None = None
    if not welcome_auto_submitted:
        visible_path = _window_text(int(edit_control["handle"]))
        if visible_path and Path(visible_path).expanduser() != Path(path_text).expanduser():
            raise GuiError("Materials Studio welcome dialog did not retain the requested MCP project path.")
        welcome_submission = _post_button_click_handle(int(ok_button["handle"]))
        if not _wait_for_window_absent(dialog_handle, timeout_seconds=30.0):
            raise GuiError("Materials Studio welcome dialog did not close after asynchronous project submission.")

    if expected_window is None:
        expected_window = _wait_for_project_window(
            pid=pid,
            expected_project_name=expected_project_name,
            timeout_seconds=30.0,
        )
    if expected_window is None:
        raise GuiError(
            "Materials Studio closed its project picker, but the requested MCP wrapper project "
            "did not become visible in the same process."
        )
    picker_path_observed = picker_submission.get("expected_path_observed") is True
    verified_path = visible_path or (path_text if picker_path_observed else None)
    return {
        "ok": True,
        "dialog_protocol_schema_version": 2,
        "requested_path": path_text,
        "target_handle": int(edit_control["handle"]),
        "target_class": str(edit_control.get("class") or ""),
        "verified_path": verified_path,
        "path_verification": (
            "welcome_edit_exact_match"
            if visible_path
            else "picker_filename_exact_match_plus_exact_project_window"
            if picker_path_observed
            else "exact_project_window_only"
        ),
        "welcome_auto_submitted": welcome_auto_submitted,
        "browse_submission": browse_submission,
        "welcome_submission": welcome_submission,
        "browse_dialog": browse_dialog.to_dict(),
        "browse_dialog_owner_chain": browse_dialog_owner_chain,
        "filename_field": field_result,
        "path_binding": path_binding,
        "picker_submission": picker_submission,
        "filename_submission_attempts": submission_attempts,
        "expected_project_window": expected_window.to_dict(),
    }


def _resolve_same_window_pre_open_prompts(
    *,
    pid: int | None,
    source_window: WindowInfo,
    source_wrapper_provenance: dict[str, Any],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Resolve save-current prompts before the owned File/Open dialog appears."""

    handled: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    auto_save_allowed = (
        source_wrapper_provenance.get("auto_save_allowed") is True
    )
    while time.monotonic() < deadline:
        dialogs = [
            window
            for window in _find_windows(pid=pid)
            if (window.class_name or "").lower() == "#32770"
            and source_window.handle in _window_owner_chain(window.handle)
        ]
        if not dialogs:
            time.sleep(0.25)
            continue
        file_open_dialogs = [
            dialog for dialog in dialogs if _looks_like_file_open_dialog(dialog)
        ]
        if file_open_dialogs and len(file_open_dialogs) == len(dialogs):
            return handled

        acted = False
        for dialog in dialogs:
            if _looks_like_non_open_file_dialog(dialog):
                cancellation = _cancel_dialog(
                    dialog.handle,
                    pid=pid,
                    owner_root_handle=source_window.handle,
                    dialog_title=dialog.title,
                )
                raise GuiError(
                    "Materials Studio exposed a path dialog without positive File/Open semantics "
                    "before File/Open. Automatic save/export submission is never authorized. "
                    f"The dialog was cancelled and verified closed: {cancellation}"
                )
            controls = _dialog_controls(dialog.handle)
            button_texts = {
                str(control.get("text") or "").lower()
                for control in controls
                if control.get("class") == "Button"
            }
            if not _looks_like_save_confirmation(dialog, button_texts):
                continue
            if not auto_save_allowed:
                _cancel_dialog(
                    dialog.handle,
                    pid=pid,
                    owner_root_handle=source_window.handle,
                    dialog_title=dialog.title,
                )
                raise GuiError(
                    "Materials Studio requested permission to save a project whose exact wrapper "
                    "provenance was not verified in the target workspace. The dialog was cancelled "
                    "to avoid modifying user files."
                )
            owner_chain = _window_owner_chain(dialog.handle)
            submission = _confirm_yes_dialog(dialog.handle)
            closed = _wait_for_window_absent(dialog.handle, timeout_seconds=12.0)
            if not closed:
                raise GuiError(
                    "Materials Studio save-current confirmation did not close after the exact Yes button "
                    "was submitted asynchronously."
                )
            handled.append(
                {
                    "action": "confirm_save_current_mcp_project_before_open",
                    "dialog": dialog.to_dict(),
                    "owner_chain": owner_chain,
                    "submission": submission,
                    "closed": True,
                    "source_wrapper_provenance": source_wrapper_provenance,
                }
            )
            acted = True
            break
        if not acted:
            unresolved = [dialog.to_dict() for dialog in dialogs]
            raise GuiError(
                f"Unhandled Materials Studio dialog before File/Open: {unresolved}"
            )
        time.sleep(0.25)
    raise GuiError(
        "Timed out while waiting for the owned Materials Studio File/Open dialog."
    )


def _resolve_same_window_open_prompts(
    *,
    pid: int | None,
    source_window: WindowInfo,
    path_text: str,
    source_wrapper_provenance: dict[str, Any],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Handle Materials Studio prompts that appear while reusing one GUI window."""

    handled: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    quiet_started_at: float | None = None
    auto_save_allowed = (
        source_wrapper_provenance.get("auto_save_allowed") is True
    )
    while time.monotonic() < deadline:
        dialogs = [
            window
            for window in _find_windows(pid=pid)
            if (window.class_name or "").lower() == "#32770"
            and source_window.handle in _window_owner_chain(window.handle)
        ]
        if not dialogs:
            now = time.monotonic()
            if quiet_started_at is None:
                quiet_started_at = now
            elif now - quiet_started_at >= 0.75:
                return handled
            time.sleep(0.1)
            continue
        quiet_started_at = None
        acted = False
        for dialog in dialogs:
            controls = _dialog_controls(dialog.handle)
            button_texts = {str(control.get("text") or "").lower() for control in controls if control.get("class") == "Button"}
            if _looks_like_non_open_file_dialog(dialog):
                cancellation = _cancel_dialog(
                    dialog.handle,
                    pid=pid,
                    owner_root_handle=source_window.handle,
                    dialog_title=dialog.title,
                )
                raise GuiError(
                    "Materials Studio exposed a path dialog without positive File/Open semantics "
                    "during same-window hot-load. Automatic save/export submission is never authorized. "
                    f"The dialog was cancelled and verified closed: {cancellation}"
                )
            if _looks_like_file_open_dialog(dialog):
                submission = _submit_current_file_open_dialog(
                    pid=pid,
                    owner_root_handle=source_window.handle,
                    initial_dialog=dialog,
                    expected_path=path_text,
                )
                path_binding = submission.get("path_binding")
                field_result = (
                    path_binding.get("filename_field")
                    if isinstance(path_binding, dict)
                    else None
                )
                handled.append(
                    {
                        "dialog_protocol_schema_version": 2,
                        "action": "resubmit_open_project_dialog",
                        "dialog": dialog.to_dict(),
                        "filename_field": field_result,
                        "path_binding": path_binding,
                        "submission": submission,
                    }
                )
                acted = True
                break
            if _looks_like_save_confirmation(dialog, button_texts):
                if not auto_save_allowed:
                    _cancel_dialog(
                        dialog.handle,
                        pid=pid,
                        owner_root_handle=source_window.handle,
                        dialog_title=dialog.title,
                    )
                    raise GuiError(
                        "Materials Studio requested permission to save a project whose exact wrapper "
                        "provenance was not verified in the target workspace. The dialog was cancelled "
                        "to avoid modifying user files."
                    )
                submission = _confirm_yes_dialog(dialog.handle)
                closed = _wait_for_window_absent(
                    dialog.handle,
                    timeout_seconds=12.0,
                )
                if not closed:
                    raise GuiError(
                        "Materials Studio save-current confirmation did not close after the exact "
                        "Yes button was submitted asynchronously."
                    )
                handled.append(
                    {
                        "action": "confirm_save_current_mcp_project",
                        "dialog": dialog.to_dict(),
                        "submission": submission,
                        "closed": True,
                        "source_wrapper_provenance": source_wrapper_provenance,
                    }
                )
                acted = True
                break
        if not acted:
            unresolved = [dialog.to_dict() for dialog in dialogs]
            raise GuiError(f"Unhandled Materials Studio dialog during same-window open: {unresolved}")
    remaining = [
        window.to_dict()
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and source_window.handle in _window_owner_chain(window.handle)
    ]
    if remaining:
        raise GuiError(f"Timed out while handling Materials Studio same-window open dialogs: {remaining}")
    raise GuiError(
        "Timed out before the owned Materials Studio dialog tree remained absent "
        "for the stable quiet period."
    )


def _looks_like_save_confirmation(dialog: WindowInfo, button_texts: set[str]) -> bool:
    """Return true for Materials Studio save-current-project confirmations."""

    if dialog.title.lower() not in {"materials studio", "confirm save as"}:
        return False
    has_yes = any(text in {"&yes", "yes", "是(&y)", "是"} for text in button_texts)
    has_no_or_cancel = any(text in {"&no", "no", "cancel", "取消", "否(&n)", "否"} for text in button_texts)
    return has_yes and has_no_or_cancel


def _dialog_controls(dialog_handle: int) -> list[dict[str, Any]]:
    """Return basic child-control metadata for a dialog."""

    controls: list[dict[str, Any]] = []
    for child in _descendant_windows(dialog_handle):
        controls.append(
            {
                "handle": child,
                "class": _window_class(child),
                "text": _window_text(child),
            }
        )
    return controls


def _paste_text_into_common_dialog_filename(dialog_handle: int, text: str) -> dict[str, Any]:
    """Paste filename text into Explorer-style common dialogs and restore the clipboard."""

    if os.name != "nt":
        return {"ok": False, "method": "clipboard_paste_unavailable"}
    clipboard = _ClipboardTextPreserver()
    attempts: list[dict[str, Any]] = []
    try:
        clipboard.set_text(text)
        for method, target_handle in [("alt_n", None), *[("edit_focus", handle) for handle in _filename_edit_candidates(dialog_handle)]]:
            _bring_window_foreground(dialog_handle)
            if target_handle is None:
                _press_virtual_key_chord([0x12, ord("N")], pause_seconds=0.03)  # Alt+N, filename field
            else:
                ctypes.windll.user32.SetFocus(ctypes.c_void_p(target_handle))
            time.sleep(0.1)
            _press_virtual_key_chord([0x11, ord("A")], pause_seconds=0.03)  # Ctrl+A
            time.sleep(0.05)
            _press_virtual_key_chord([0x11, ord("V")], pause_seconds=0.03)  # Ctrl+V
            time.sleep(0.15)
            visible_text = _visible_filename_edit_text(dialog_handle, expected=text)
            ok = visible_text == text
            attempts.append(
                {
                    "method": method,
                    "target_handle": target_handle,
                    "visible_text_matches": ok,
                    "visible_text": visible_text,
                }
            )
            if ok:
                return {
                    "ok": True,
                    "method": f"clipboard_paste_{method}",
                    "clipboard_restored": True,
                    "attempts": attempts,
                }
        return {
            "ok": False,
            "method": "clipboard_paste",
            "clipboard_restored": True,
            "attempts": attempts,
        }
    finally:
        clipboard.restore()


def _filename_edit_candidates(dialog_handle: int) -> list[int]:
    """Return likely filename edit controls from a common dialog."""

    candidates: list[int] = []
    if os.name != "nt":
        return candidates
    user32 = ctypes.windll.user32
    for child in _descendant_windows(dialog_handle):
        if (_window_class(child) or "").lower() != "edit":
            continue
        if not user32.IsWindowEnabled(ctypes.c_void_p(child)):
            continue
        candidates.append(child)
    return candidates


def _visible_filename_edit_text(dialog_handle: int, *, expected: str | None = None) -> str | None:
    """Return the visible filename edit text from a common dialog when available."""

    edits: list[tuple[int, str]] = []
    for child in _descendant_windows(dialog_handle):
        if (_window_class(child) or "").lower() == "edit":
            value = _window_text(child)
            if value:
                edits.append((child, value))
    if not edits:
        return None
    if expected is not None:
        for _child, value in edits:
            if value == expected:
                return value
    # The filename field is usually the last non-empty Edit before file-type controls.
    return max((value for _child, value in edits), key=len)


def _bring_window_foreground(window_handle: int) -> None:
    """Bring a dialog to the foreground before keyboard input."""

    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    hwnd = ctypes.c_void_p(window_handle)
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)


def _confirm_yes_dialog(dialog_handle: int) -> dict[str, Any]:
    """Asynchronously confirm the exact Yes button in a Materials Studio dialog."""

    for child in _descendant_windows(dialog_handle):
        if (_window_class(child) or "").lower() != "button":
            continue
        label = (_window_text(child) or "").lower()
        if label in {"&yes", "yes", "是(&y)", "是"}:
            button_text = _window_text(child)
            return {
                **_post_button_click_handle(child),
                "button_text": button_text,
                "dialog_handle": dialog_handle,
            }
    raise GuiError(
        "Materials Studio save confirmation did not expose an exact Yes button."
    )


def _cancel_dialog(
    dialog_handle: int,
    *,
    timeout_seconds: float = 5.0,
    pid: int | None = None,
    owner_root_handle: int | None = None,
    dialog_title: str | None = None,
    quiet_period_seconds: float = 0.75,
    max_replacement_attempts: int = 3,
) -> dict[str, Any]:
    """Submit IDCANCEL and drain bounded same-owner replacement dialogs."""

    attempts: list[dict[str, Any]] = []

    def cancel_one(handle: int) -> None:
        submission = _post_dialog_command(handle, 2)
        closed = _wait_for_window_absent(
            handle,
            timeout_seconds=timeout_seconds,
        )
        attempt = {
            "command": "IDCANCEL",
            "command_id": 2,
            "dialog_handle": handle,
            "submission": submission,
            "closed": closed,
        }
        attempts.append(attempt)
        if not closed:
            raise GuiError(
                "Materials Studio dialog did not close after exact asynchronous IDCANCEL: "
                f"{attempt}"
            )

    cancel_one(dialog_handle)
    family_binding_complete = bool(
        pid is not None and dialog_title is not None
    )
    family_stable_absent: bool | None = None
    if family_binding_complete:
        normalized_title = str(dialog_title).strip().casefold()
        deadline = time.monotonic() + timeout_seconds
        quiet_started_at: float | None = None
        while time.monotonic() < deadline:
            replacements = [
                window
                for window in _find_windows(pid=pid)
                if (window.class_name or "").lower() == "#32770"
                and window.title.strip().casefold() == normalized_title
                and (
                    owner_root_handle is None
                    or owner_root_handle in _window_owner_chain(window.handle)
                )
            ]
            if replacements:
                quiet_started_at = None
                if len(replacements) != 1:
                    raise GuiError(
                        "Refusing ambiguous replacement-dialog cancellation: "
                        f"{[window.to_dict() for window in replacements]}"
                    )
                if len(attempts) > max_replacement_attempts:
                    raise GuiError(
                        "Materials Studio recreated the cancelled dialog more than the "
                        f"bounded limit: {attempts}"
                    )
                cancel_one(replacements[0].handle)
                continue
            now = time.monotonic()
            if quiet_started_at is None:
                quiet_started_at = now
            elif now - quiet_started_at >= quiet_period_seconds:
                family_stable_absent = True
                break
            time.sleep(0.1)
        if family_stable_absent is not True:
            raise GuiError(
                "Materials Studio dialog family did not remain absent for the stable "
                f"quiet period: {attempts}"
            )

    first = attempts[0]
    return {
        **first,
        "attempts": attempts,
        "replacement_cancel_count": max(0, len(attempts) - 1),
        "family_binding_complete": family_binding_complete,
        "family_stable_absent": family_stable_absent,
        "dialog_title": dialog_title,
        "pid": pid,
        "owner_root_handle": owner_root_handle,
    }


def _press_enter_on_window(window_handle: int) -> None:
    """Press Enter on a foreground dialog."""

    _bring_window_foreground(window_handle)
    time.sleep(0.05)
    user32 = ctypes.windll.user32
    user32.keybd_event(0x0D, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(0x0D, 0, 0x0002, 0)


def _window_text(window_handle: int) -> str:
    """Return Win32 window/control text."""

    if os.name != "nt":
        return ""
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(ctypes.c_void_p(window_handle))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(ctypes.c_void_p(window_handle), buffer, length + 1)
    return buffer.value


class _ClipboardTextPreserver:
    """Temporarily replace Unicode clipboard text and restore it."""

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self) -> None:
        self._available = os.name == "nt"
        self._original = self._read_text() if self._available else None

    def set_text(self, text: str) -> None:
        if not self._available:
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            raise GuiError("OpenClipboard failed while preparing same-window file open.")
        try:
            user32.EmptyClipboard()
            data = (text + "\0").encode("utf-16-le")
            handle = kernel32.GlobalAlloc(self.GMEM_MOVEABLE, len(data))
            pointer = kernel32.GlobalLock(handle)
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(self.CF_UNICODETEXT, handle)
        finally:
            user32.CloseClipboard()

    def restore(self) -> None:
        if self._available and self._original is not None:
            self.set_text(self._original)

    def _read_text(self) -> str | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        text: str | None = None
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(self.CF_UNICODETEXT)
            if handle:
                pointer = kernel32.GlobalLock(handle)
                if pointer:
                    try:
                        text = ctypes.wstring_at(pointer)
                    finally:
                        kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        return text


def _set_control_or_child_text(control_handle: int, text: str) -> dict[str, Any]:
    """Set text on a control or its first editable descendant."""

    if _set_window_text(control_handle, text):
        return {"ok": True, "target_handle": control_handle, "target_class": _window_class(control_handle)}
    for child in _descendant_windows(control_handle):
        class_name = (_window_class(child) or "").lower()
        if class_name == "edit" and _set_window_text(child, text):
            return {"ok": True, "target_handle": child, "target_class": _window_class(child)}
    return {"ok": False, "target_handle": control_handle, "target_class": _window_class(control_handle)}


def _set_window_text(window_handle: int, text: str) -> bool:
    """Set Win32 window/control text."""

    if os.name != "nt":
        return False
    return bool(ctypes.windll.user32.SetWindowTextW(ctypes.c_void_p(window_handle), text))


def _post_window_message(
    window_handle: int,
    message: int,
    wparam: int,
    lparam: int,
) -> dict[str, Any]:
    """Post one non-blocking Win32 message to an exact window or control."""

    if os.name != "nt":
        raise GuiError("Win32 dialog messaging is only available on Windows.")
    posted = bool(
        ctypes.windll.user32.PostMessageW(
            ctypes.c_void_p(window_handle),
            message,
            wparam,
            lparam,
        )
    )
    if not posted:
        raise GuiError(
            f"Could not post Win32 message 0x{message:04X} to handle {window_handle}."
        )
    return {
        "method": "PostMessageW",
        "target_handle": window_handle,
        "message": message,
        "wparam": wparam,
        "lparam": lparam,
        "posted": True,
    }


def _post_dialog_command(dialog_handle: int, command_id: int) -> dict[str, Any]:
    """Post a non-blocking WM_COMMAND to an exact dialog."""

    return _post_window_message(dialog_handle, 0x0111, command_id, 0)  # WM_COMMAND


def _post_button_click_handle(button_handle: int) -> dict[str, Any]:
    """Post a non-blocking BM_CLICK to an exact button control."""

    return _post_window_message(button_handle, 0x00F5, 0, 0)  # BM_CLICK


def _click_dialog_ok(dialog_handle: int) -> dict[str, Any]:
    """Submit a common dialog asynchronously without screen coordinates."""

    return _post_dialog_command(dialog_handle, 1)  # IDOK


def _click_button_handle(button_handle: int) -> None:
    """Click a known Win32 button handle without screen coordinates."""

    if os.name != "nt":
        raise GuiError("Win32 dialog buttons are only available on Windows.")
    ctypes.windll.user32.SendMessageW(ctypes.c_void_p(button_handle), 0x00F5, 0, 0)  # BM_CLICK


def _wait_for_window_absent(window_handle: int, *, timeout_seconds: float) -> bool:
    """Wait until a Win32 window handle disappears."""

    if os.name != "nt":
        return True
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not ctypes.windll.user32.IsWindow(ctypes.c_void_p(window_handle)):
            return True
        time.sleep(0.25)
    return not bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(window_handle)))


def _descendant_windows(root_handle: int) -> list[int]:
    """Return child window handles below a root window."""

    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    children: list[int] = []
    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_proc(hwnd: int, _lparam: int) -> bool:
        children.append(int(hwnd))
        return True

    user32.EnumChildWindows(ctypes.c_void_p(root_handle), enum_proc_type(enum_proc), 0)
    return children


def _analyze_bmp_snapshot(path: Path, *, max_samples: int = 5000) -> dict[str, Any]:
    """Return lightweight visibility metrics for a BMP snapshot."""

    try:
        data = path.read_bytes()
        if len(data) < 54:
            raise ValueError("BMP file is too small")
        signature, _file_size, _reserved1, _reserved2, pixel_offset = struct.unpack_from("<2sIHHI", data, 0)
        if signature != b"BM":
            raise ValueError("not a BMP file")
        header_size = struct.unpack_from("<I", data, 14)[0]
        if header_size < 40:
            raise ValueError(f"unsupported BMP DIB header size: {header_size}")
        _dib_size, width, height_raw, planes, bits_per_pixel, compression, _image_size = struct.unpack_from(
            "<IiiHHII",
            data,
            14,
        )
        if planes != 1:
            raise ValueError("invalid BMP plane count")
        if compression != 0:
            raise ValueError(f"compressed BMP is unsupported: {compression}")
        if bits_per_pixel not in (24, 32):
            raise ValueError(f"unsupported BMP bit depth: {bits_per_pixel}")
        height = abs(height_raw)
        if width <= 0 or height <= 0:
            raise ValueError("invalid BMP dimensions")

        bytes_per_pixel = bits_per_pixel // 8
        stride = ((width * bits_per_pixel + 31) // 32) * 4
        sample_step = max(1, int((width * height / max_samples) ** 0.5))
        colors: dict[tuple[int, int, int], int] = {}
        luminance_sum = 0.0
        dark = 0
        bright = 0
        colored = 0
        samples = 0
        for y in range(0, height, sample_step):
            source_y = height - 1 - y if height_raw > 0 else y
            row_start = pixel_offset + source_y * stride
            for x in range(0, width, sample_step):
                offset = row_start + x * bytes_per_pixel
                if offset + 2 >= len(data):
                    continue
                blue, green, red = data[offset], data[offset + 1], data[offset + 2]
                color = (red, green, blue)
                colors[color] = colors.get(color, 0) + 1
                luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
                luminance_sum += luminance
                dark += int(luminance < 32)
                bright += int(luminance > 224)
                colored += int(max(color) - min(color) > 24)
                samples += 1
        if samples == 0:
            raise ValueError("no readable BMP pixel samples")
        dominant_color, dominant_count = max(colors.items(), key=lambda item: item[1])
        dominant_ratio = dominant_count / samples
        unique_colors = len(colors)
        warnings: list[str] = []
        if unique_colors <= 3:
            warnings.append("Snapshot has very few sampled colors and may be blank.")
        if dominant_ratio >= 0.985:
            warnings.append("Snapshot is dominated by one color and may not show useful model content.")
        viewport = _analyze_bmp_viewport(
            data=data,
            pixel_offset=pixel_offset,
            width=width,
            height=height,
            height_raw=height_raw,
            bits_per_pixel=bits_per_pixel,
            stride=stride,
            bytes_per_pixel=bytes_per_pixel,
        )
        warnings.extend(viewport.get("viewport_warnings") or [])
        return {
            "readable": True,
            "width": width,
            "height": height,
            "bits_per_pixel": bits_per_pixel,
            "sample_count": samples,
            "unique_sampled_colors": unique_colors,
            "dominant_color_rgb": list(dominant_color),
            "dominant_color_ratio": round(dominant_ratio, 6),
            "non_dominant_ratio": round(1.0 - dominant_ratio, 6),
            "mean_luminance": round(luminance_sum / samples, 6),
            "dark_pixel_ratio": round(dark / samples, 6),
            "bright_pixel_ratio": round(bright / samples, 6),
            "colored_pixel_ratio": round(colored / samples, 6),
            "likely_nonblank": unique_colors > 3 and dominant_ratio < 0.985,
            "warnings": warnings,
            **viewport,
        }
    except Exception as exc:
        return {
            "readable": False,
            "likely_nonblank": False,
            "warning": str(exc),
        }


def _analyze_bmp_viewport(
    *,
    data: bytes,
    pixel_offset: int,
    width: int,
    height: int,
    height_raw: int,
    bits_per_pixel: int,
    stride: int,
    bytes_per_pixel: int,
    max_samples: int = 4000,
) -> dict[str, Any]:
    """Estimate whether the central Materials Studio model viewport has visible content."""

    if width < 160 or height < 120:
        return {
            "viewport_analysis_available": False,
            "viewport_likely_visible_model": None,
            "viewport_warnings": [],
        }

    left = int(width * 0.14)
    right = int(width * 0.94)
    top = int(height * 0.20)
    bottom = int(height * 0.93)
    if right - left < 50 or bottom - top < 50:
        return {
            "viewport_analysis_available": False,
            "viewport_likely_visible_model": None,
            "viewport_warnings": [],
        }

    sample_step = max(1, int((((right - left) * (bottom - top)) / max_samples) ** 0.5))
    colors: dict[tuple[int, int, int], int] = {}
    samples: list[tuple[int, int, int]] = []
    luminances: list[float] = []
    for y in range(top, bottom, sample_step):
        source_y = height - 1 - y if height_raw > 0 else y
        row_start = pixel_offset + source_y * stride
        for x in range(left, right, sample_step):
            offset = row_start + x * bytes_per_pixel
            if offset + 2 >= len(data):
                continue
            blue, green, red = data[offset], data[offset + 1], data[offset + 2]
            color = (red, green, blue)
            colors[color] = colors.get(color, 0) + 1
            samples.append(color)
            luminances.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)

    if not samples:
        return {
            "viewport_analysis_available": False,
            "viewport_likely_visible_model": None,
            "viewport_warnings": ["Central model viewport could not be sampled."],
        }

    background_color, background_count = max(colors.items(), key=lambda item: item[1])
    background_luminance = (
        0.2126 * background_color[0]
        + 0.7152 * background_color[1]
        + 0.0722 * background_color[2]
    )
    foreground = 0
    colored = 0
    bright = 0
    for color, luminance in zip(samples, luminances):
        distance = sum((color[index] - background_color[index]) ** 2 for index in range(3)) ** 0.5
        luminance_delta = abs(luminance - background_luminance)
        if distance > 42 or luminance_delta > 45:
            foreground += 1
        if max(color) - min(color) > 24 and luminance_delta > 20:
            colored += 1
        if luminance > 160 and luminance_delta > 50:
            bright += 1

    sample_count = len(samples)
    foreground_ratio = foreground / sample_count
    colored_ratio = colored / sample_count
    bright_ratio = bright / sample_count
    background_ratio = background_count / sample_count
    unique_colors = len(colors)
    viewport_uniform_surface = background_ratio >= 0.995 and foreground_ratio <= 0.0005 and unique_colors <= 2
    viewport_dark_uniform_surface = viewport_uniform_surface and background_luminance <= 24.0
    sparse_visible = (
        foreground_ratio >= 0.0015
        and unique_colors >= 4
        and (colored_ratio >= 0.0005 or bright_ratio >= 0.0005)
    )
    likely_visible = sparse_visible or (
        foreground_ratio >= 0.003
        and (unique_colors >= 4 or colored_ratio >= 0.001 or bright_ratio >= 0.001)
    )
    warnings: list[str] = []
    if not likely_visible:
        warnings.append("Central model viewport appears blank or not fit-to-view; the model may not be visually visible.")
    if viewport_dark_uniform_surface:
        warnings.append(
            "Central model viewport is a uniform dark surface; in Materials Studio 20.x this can also indicate "
            "GDI/BitBlt did not capture the OpenGL 3D viewport."
        )
    if likely_visible:
        diagnostic = "visible_model_pixels"
    elif viewport_dark_uniform_surface:
        diagnostic = "uniform_dark_viewport_surface"
    elif viewport_uniform_surface:
        diagnostic = "uniform_viewport_surface"
    else:
        diagnostic = "low_contrast_or_not_fit_to_view"

    return {
        "viewport_analysis_available": True,
        "viewport_bounds": [left, top, right, bottom],
        "viewport_sample_count": sample_count,
        "viewport_unique_sampled_colors": unique_colors,
        "viewport_background_rgb": list(background_color),
        "viewport_background_luminance": round(background_luminance, 6),
        "viewport_background_ratio": round(background_ratio, 6),
        "viewport_foreground_ratio": round(foreground_ratio, 6),
        "viewport_colored_pixel_ratio": round(colored_ratio, 6),
        "viewport_bright_pixel_ratio": round(bright_ratio, 6),
        "viewport_uniform_surface": viewport_uniform_surface,
        "viewport_dark_uniform_surface": viewport_dark_uniform_surface,
        "viewport_capture_limitation_possible": viewport_dark_uniform_surface,
        "viewport_capture_diagnostic": diagnostic,
        "viewport_likely_visible_model": likely_visible,
        "viewport_warnings": warnings,
    }


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """获取窗口矩形。"""
    if os.name != "nt":
        return None
    import ctypes.wintypes as wintypes

    rect = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    return None


def _capture_window_bmp(hwnd: int, output_path: Path) -> None:
    """捕获窗口 BMP。"""
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        raise GuiError("GetWindowRect 失败。")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise GuiError("Materials Studio 窗口捕获尺寸无效。")

    hwindc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    if not hwindc:
        raise GuiError("GetWindowDC 失败。")
    srcdc = gdi32.CreateCompatibleDC(hwindc)
    hbmp = gdi32.CreateCompatibleBitmap(hwindc, width, height)
    old_obj = gdi32.SelectObject(srcdc, hbmp)
    try:
        srccopy = 0x00CC0020
        if not gdi32.BitBlt(srcdc, 0, 0, width, height, hwindc, 0, 0, srccopy):
            raise GuiError("BitBlt 失败。")

        row_stride = ((width * 24 + 31) // 32) * 4
        image_size = row_stride * height
        buffer = ctypes.create_string_buffer(image_size)

        class BitmapInfoHeader(ctypes.Structure):
            """位图信息头。"""

            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        header = BitmapInfoHeader()
        header.biSize = ctypes.sizeof(BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 24
        header.biCompression = 0
        header.biSizeImage = image_size
        if not gdi32.GetDIBits(srcdc, hbmp, 0, height, buffer, ctypes.byref(header), 0):
            raise GuiError("GetDIBits 失败。")

        file_header_size = 14
        info_header_size = 40
        pixel_offset = file_header_size + info_header_size
        file_size = pixel_offset + image_size
        file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
        info_header = struct.pack(
            "<IiiHHIIiiII",
            info_header_size,
            width,
            -height,
            1,
            24,
            0,
            image_size,
            0,
            0,
            0,
            0,
        )
        output_path.write_bytes(file_header + info_header + buffer.raw)
    finally:
        if old_obj:
            gdi32.SelectObject(srcdc, old_obj)
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if srcdc:
            gdi32.DeleteDC(srcdc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hwindc)
