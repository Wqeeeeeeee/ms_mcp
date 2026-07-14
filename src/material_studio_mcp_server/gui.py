"""本地 Materials Studio GUI 助手。

此模块故意避免 COM 自动化。它提供了一个保守的 Windows 回退方案，
用于查找已打开的 MatStudio 窗口、激活它、通过 OS shell 关联打开结构文件，
以及捕获 BMP 快照用于审计日志。
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from xml.sax.saxutils import escape as xml_escape

from .state.store import default_workspace_root, sanitize_project_id


class GuiError(RuntimeError):
    """当本地 GUI 控制无法完成时引发。"""


VIEW_REPLAY_MANIFEST_SCHEMA_VERSION = 3

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
MILLER_PLANE_CAMERA_MATCH_SCOPE = "crystal_plane_normal_with_native_in_plane_roll"
MILLER_DIRECTION_CAMERA_MATCH_SCOPE = (
    "crystal_lattice_direction_via_collinear_plane_normal_with_native_in_plane_roll"
)
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
    "dialog_show_set_of_parallel_planes",
    "dialog_show_symmetry_images",
    "properties_filter",
    "properties_miller_label",
    "camera_match_scope",
    "plane_normal_matches_manifest",
    "analytic_in_plane_basis_matches_manifest",
    "native_in_plane_roll_policy_observed",
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
    "dialog_show_set_of_parallel_planes",
    "dialog_show_symmetry_images",
}
MILLER_PLANE_REQUIRED_TRUE_EVIDENCE_FIELDS = (
    "dialog_miller_indices_verified_before_create",
    "plane_normal_matches_manifest",
    "native_in_plane_roll_policy_observed",
    "reset_view_before_alignment",
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
    "pointer_menu_click_through_risk_observed",
    "unexpected_plane_created_during_probe",
    "unexpected_plane_cleanup_verified",
    "document_clean_before_probe",
    "document_clean_after_probe",
)
MILLER_RUNTIME_UI_REQUIRED_TRUE_FIELDS = (
    "reset_view_control_observed",
    "tools_miller_planes_menu_observed",
    "miller_planes_keyboard_menu_path_verified",
    "miller_planes_dialog_observed",
    "miller_indices_control_observed",
    "create_button_observed",
    "properties_explorer_menu_observed",
    "view_onto_control_observed",
    "document_clean_before_probe",
    "document_clean_after_probe",
)
MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS = (
    "unique_transient_plane_visual_target_observed",
    "viewport_plane_selection_observed",
    "properties_selection_verified",
    "view_onto_popup_menu_observed",
)
MILLER_RUNTIME_VIEWPORT_PROBE_FIELDS = {
    "selection_method",
    "probe_miller_indices",
    "dialog_miller_indices",
    *MILLER_RUNTIME_VIEWPORT_PROBE_TRUE_FIELDS,
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
    "reset_view_control_observed": "runtime_reset_view_control_not_observed",
    "tools_miller_planes_menu_observed": "runtime_tools_miller_planes_menu_not_observed",
    "miller_planes_keyboard_menu_path_verified": (
        "runtime_miller_planes_keyboard_menu_path_not_verified"
    ),
    "miller_planes_dialog_observed": "runtime_miller_planes_dialog_not_observed",
    "miller_indices_control_observed": "runtime_miller_indices_control_not_observed",
    "create_button_observed": "runtime_miller_plane_create_button_not_observed",
    "properties_explorer_menu_observed": "runtime_properties_explorer_menu_not_observed",
    "view_onto_control_observed": "runtime_view_onto_control_not_observed",
    "document_clean_before_probe": "runtime_document_not_clean_before_probe",
    "document_clean_after_probe": "runtime_document_not_clean_after_probe",
}


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
    if source not in {"computer_use", "manual_review"}:
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
            and viewport_fields["view_onto_popup_menu_observed"] is True
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
    if not isinstance(raw_undo_labels, list) or not 3 <= len(raw_undo_labels) <= 16:
        raise GuiError("miller_plane_evidence.undo_labels_applied must contain 3 to 16 labels")
    undo_labels = [str(value).strip() for value in raw_undo_labels]
    if any(
        not any(pattern.fullmatch(label) for pattern in MILLER_PLANE_UNDO_LABEL_PATTERNS)
        for label in undo_labels
    ):
        raise GuiError("miller_plane_evidence.undo_labels_applied contains a non-whitelisted undo")
    view_onto_undo_present = any(label.startswith("Undo View Onto ") for label in undo_labels)
    create_plane_undo_present = "Undo Create Miller Plane" in undo_labels
    reset_view_undo_present = "Undo Reset View" in undo_labels

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
        view_onto_undo_present and create_plane_undo_present and reset_view_undo_present
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
    unavailable_reason = None if supported else "本地 GUI 回退仅在 Windows 上可用。"

    def list_processes(self) -> list[ProcessInfo]:
        """列出进程。"""
        if not self.supported:
            return []
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MatStudio.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows = csv.reader(line for line in completed.stdout.splitlines() if line.strip())
        processes: list[ProcessInfo] = []
        for row in rows:
            if len(row) < 2 or row[0].upper().startswith("INFO:"):
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            if row[0].lower() == "matstudio.exe":
                processes.append(ProcessInfo(name=row[0], pid=pid))
        return processes

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
        before_pids = {process.pid for process in self.list_processes()}
        pre_dismissed_dialogs = self.dismiss_startup_dialogs(pid=window.pid, timeout_seconds=2.0)
        if not self.activate_window(window):
            raise GuiError("Could not activate the existing Materials Studio window for same-window open.")

        startup_open = _open_project_from_startup_dialogs(
            pid=window.pid,
            source_window=window,
            path=path,
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
        dialog = _find_file_open_dialog(pid=window.pid, timeout_seconds=10.0)
        if dialog is None:
            raise GuiError(
                "The existing Materials Studio window did not expose a File/Open dialog after Ctrl+O. "
                "No new MatStudio.exe was launched; use Computer Use or manual File > Open in the same "
                "window, then snapshot/audit the project."
            )

        field_result = _set_common_dialog_filename(dialog.handle, str(path))
        _click_dialog_ok(dialog.handle)
        handled_prompts = _resolve_same_window_open_prompts(
            pid=window.pid,
            source_window=window,
            path_text=str(path),
            timeout_seconds=60.0,
        )
        dialog_closed = _wait_for_window_absent(dialog.handle, timeout_seconds=12.0)
        after_pids = {process.pid for process in self.list_processes()}
        spawned_pids = sorted(after_pids - before_pids)
        return {
            "method": "existing_window_file_open_dialog",
            "path": str(path),
            "window": window.to_dict(),
            "dialog": dialog.to_dict(),
            "filename_field": field_result,
            "handled_prompts": handled_prompts,
            "dialog_closed": dialog_closed,
            "process_count_before": len(before_pids),
            "process_count_after": len(after_pids),
            "spawned_process_ids": spawned_pids,
            "same_window_open_requested": True,
            "pre_dismissed_dialogs": pre_dismissed_dialogs,
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
                ok_button = ctypes.windll.user32.GetDlgItem(ctypes.c_void_p(dialog.handle), 1)
                if is_file_association and ok_button:
                    ctypes.windll.user32.SendMessageW(ctypes.c_void_p(ok_button), 0x00F5, 0, 0)  # BM_CLICK
                else:
                    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(dialog.handle), 0x0010, 0, 0)  # WM_CLOSE
                dismissed.append(dialog.to_dict())
            time.sleep(0.75)
        return dismissed


class MaterialsStudioGuiController:
    """MCP 工具使用的高级 GUI 会话助手。"""

    def __init__(self, workspace_root: str | Path | None = None, backend: GuiBackend | None = None) -> None:
        """初始化 GUI 控制器。

        参数:
            workspace_root: 工作区根目录
            backend: GUI 后端
        """
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else default_workspace_root()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.backend = backend or (WindowsGuiBackend() if os.name == "nt" else NullGuiBackend())

    def status(self, *, project_id: str | None = None, revision: int | None = None) -> dict[str, Any]:
        """返回状态。"""
        processes = self.backend.list_processes() if self.backend.supported else []
        list_windows = getattr(self.backend, "list_windows", None)
        windows = list_windows() if self.backend.supported and callable(list_windows) else []
        window = self.backend.find_window() if self.backend.supported else None
        if window is not None and not any(item.handle == window.handle for item in windows):
            windows = [window, *windows]
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
                "scope": "structure_hotload_and_live_edit",
                "hotload_requires_existing_window": True,
                "auto_launch_during_open_allowed": False,
                "explicit_blank_session_launch_tool": "material_studio_gui_launch",
                "ok": not single_window_violation_reasons,
                "violation_reasons": single_window_violation_reasons,
            },
            "single_window_policy_ok": not single_window_violation_reasons,
            "single_window_violation_reasons": single_window_violation_reasons,
            "can_launch_matstudio": open_strategy is not None,
            "can_launch_blank_session": matstudio_exe is not None,
            "workspace_root": str(self.workspace_root),
            "screenshots_dir": str(self.workspace_root / "screenshots"),
            "capabilities": [
                "detect_matstudio_window",
                "list_matstudio_windows",
                "launch_matstudio_session",
                "activate_window",
                "open_structure_file",
                "capture_bmp_snapshot",
                "copy_script_assist",
                "prepare_view_replay_manifest",
                "record_external_view_replay",
            ],
            "limits": [
                "不使用 COM 自动化。",
                "精确的结构编辑应保持 ModelSpec/SemanticPatch/MaterialsScript 驱动。",
                "菜单和视口操作需要 Computer Use（如果可用）。",
                "任意相机向量没有经过验证的 Materials Studio 2020 MaterialsScript API；本地后端只生成回放清单。",
            ],
        }

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
            entries.append(entry)
        return entries

    def _project_wrapper_metadata_for_window(self, window: WindowInfo) -> dict[str, Any] | None:
        """Return metadata for an MCP-generated .stp wrapper window."""

        project_name = _project_name_from_window_title(window.title)
        if project_name is None:
            return None
        metadata_path = (self.workspace_root / "gui_projects" / project_name / "metadata.json").resolve()
        try:
            _ensure_inside(self.workspace_root, metadata_path)
        except GuiError:
            return None
        if not metadata_path.exists() or not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {"metadata_path": str(metadata_path), "read_error": True}
        if not isinstance(metadata, dict):
            return {"metadata_path": str(metadata_path), "read_error": True}
        return {"metadata_path": str(metadata_path), **metadata}

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
            existing_window = self.backend.find_window()
        if existing_window is not None and not any(item.handle == existing_window.handle for item in windows):
            windows = [existing_window, *windows]
        window_inventory = self._window_inventory(windows, selected_window=existing_window)
        same_window_open_supported = _backend_same_window_open_supported(self.backend)
        file_open_may_launch_new_instance = _backend_file_open_may_launch_new_instance(self.backend)
        window_management = _window_management_receipt(
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
        requested_window, target_resolution = self._require_window(project_id=project_id, revision=revision)
        activated = self.backend.activate_window(requested_window)
        refreshed_window, refreshed_resolution = self._require_window(project_id=project_id, revision=revision)
        window_identity_stable = refreshed_window.handle == requested_window.handle
        window_management = self._window_management_for_hotload(
            target_window=refreshed_window,
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
        window, target_resolution = self._require_window(project_id=project_id, revision=revision)
        window_management = self._window_management_for_hotload(
            target_window=window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        if window_management.get("activation_required_before_capture_or_input"):
            blocked_payload = {
                "captured": False,
                "window": window.to_dict(),
                "target_window_resolution": target_resolution,
                "window_management": window_management,
                "block_reason": "target_window_activation_required",
                "activation_reasons": list(window_management.get("interaction_activation_reasons") or []),
            }
            self._write_log("snapshot_blocked", project_id=project_id, revision=revision, payload=blocked_payload)
            raise GuiError(
                "Refusing to capture the Materials Studio window before the verified target is restored and "
                "foreground. Call material_studio_gui_activate with the same project_id/revision and "
                "take_snapshot=true."
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
        window, target_resolution = self._resolve_target_window(project_id=project_id, revision=revision)
        if window is None:
            raise GuiError(
                "No existing Materials Studio window was found. Refusing to launch a new MatStudio.exe "
                "from open_structure; activate an existing window first, or call material_studio_gui_launch "
                "only when starting a new GUI session is intentional."
            )
        window_management = self._window_management_for_hotload(
            target_window=window,
            target_resolution=target_resolution,
            project_id=project_id,
            revision=revision,
        )
        single_window_violation_reasons = list(window_management.get("single_window_violation_reasons") or [])
        if single_window_violation_reasons:
            raise GuiError(
                "Refusing to hot-load a structure while the Materials Studio GUI session violates the "
                "single-window policy. Close or save extra Materials Studio windows before continuing. "
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
                project_id=project_id,
                revision=revision,
            )
            if refreshed_window.handle != window.handle:
                raise GuiError(
                    "Refusing same-window GUI input because the target Materials Studio window identity changed "
                    "during activation. Run material_studio_gui_status and retry with the returned project/revision."
                )
            window = refreshed_window
            target_resolution = refreshed_resolution
            post_activation_window_management = self._window_management_for_hotload(
                target_window=window,
                target_resolution=target_resolution,
                project_id=project_id,
                revision=revision,
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
        activated_opened_window = self.backend.activate_window(window) if window is not None else False
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
        self._write_log("copy_script_assist", project_id=project_id, revision=revision, payload=payload)
        return payload

    def prepare_view_replay(
        self,
        audit: dict[str, Any],
        *,
        project_id: str,
        revision: int,
        runtime_ui_evidence: dict[str, Any] | None = None,
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
        runtime_ui_preflight = self._resolve_view_replay_runtime_ui_preflight(
            status=status,
            project_id=safe_project,
            revision=revision,
            supplied_evidence=runtime_ui_evidence,
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
        if status.get("process_count") != 1:
            block_reasons.append("exactly_one_matstudio_process_required")
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
        command_evidence = _materials_studio_view_command_evidence()
        for step in steps:
            step["execution_recipe"] = _view_replay_execution_recipe(
                step,
                command_evidence,
                runtime_ui_preflight=runtime_ui_preflight,
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
                "local_mcp_backend": "manifest_only",
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
            "safety_gate": {
                "activate_target_window_before_screenshot_or_input": True,
                "verify_project_revision_wrapper_identity": True,
                "require_exactly_one_matstudio_process": True,
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
            current_view_names = set(manifest["view_names"])
            preserved_events = [
                event
                for event in existing_manifest.get("replay_events") or []
                if isinstance(event, dict) and event.get("view_name") in current_view_names
            ]
            manifest["replay_events"] = preserved_events
            manifest["preserved_replay_event_count"] = len(preserved_events)
            manifest["prior_manifest_generated_at"] = existing_manifest.get("generated_at")
        _refresh_view_replay_summary(manifest)
        if manifest.get("replay_status") == "externally_confirmed":
            manifest["next_action"] = {
                "recommended_tool": "material_studio_live_project_status",
                "recommended_action": "review_current_revision_after_all_prepared_views_were_confirmed",
                "payload_hint": {"project_id": safe_project},
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
            "activation_required": activation_required,
            "view_names": manifest["view_names"],
            "requested_view_count": len(steps),
            "supported_view_count": len(supported_steps),
            "unsupported_view_count": len(unsupported_steps),
            "replay_continuation": manifest.get("replay_continuation"),
            "next_action": manifest["next_action"],
            "manifest": manifest,
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
        miller_plane_recipe = recipe_kind in MILLER_VIEW_ONTO_RECIPE_KINDS
        direction_via_miller_plane_recipe = (
            recipe_kind == "crystal_direction_via_collinear_miller_plane_view_onto"
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
                not staged_keyboard_evidence_required
                or staged_keyboard_evidence_complete
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
        if staged_keyboard_evidence_required and not staged_keyboard_evidence_complete:
            rejection_reasons.append("staged_keyboard_evidence_incomplete")
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
        event = {
            "event_id": uuid.uuid4().hex,
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
            "miller_plane_evidence_required": miller_plane_evidence_required,
            "direction_via_miller_plane_recipe": direction_via_miller_plane_recipe,
            "miller_plane_evidence_complete": miller_plane_evidence_complete,
            "miller_plane_artifact_binding_matches": miller_plane_artifact_binding_matches,
            "expected_structure_artifact_path": expected_structure_artifact_path,
            "miller_plane_evidence": normalized_miller_plane_evidence,
            "execution_recipe": execution_recipe or None,
            "expected_camera": matching_view.get("camera"),
            "expected_projection": matching_view.get("verification"),
        }
        replay_events = [item for item in manifest.get("replay_events") or [] if isinstance(item, dict)]
        replay_events.append(event)
        manifest["replay_events"] = replay_events
        manifest["last_replay_event"] = event
        _refresh_view_replay_summary(manifest)
        _write_json_atomic(manifest_path, manifest)

        events_path = manifest_path.with_name("gui_view_replay_events.jsonl")
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        log_path = self._write_log(
            "record_view_replay",
            project_id=safe_project,
            revision=revision,
            payload={
                "manifest_path": str(manifest_path),
                "events_path": str(events_path),
                "event": event,
                "replay_summary": manifest["replay_summary"],
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

    def _require_window(
        self,
        *,
        project_id: str | None = None,
        revision: int | None = None,
    ) -> tuple[WindowInfo, dict[str, Any]]:
        """获取必需的窗口。"""
        if not self.backend.supported:
            raise GuiError(self.backend.unavailable_reason or "GUI 后端不可用。")
        window, target_resolution = self._resolve_target_window(project_id=project_id, revision=revision)
        if window is None:
            raise GuiError("未找到打开的 Materials Studio 窗口。请先启动 MatStudio.exe。")
        return window, target_resolution

    def _resolve_target_window(
        self,
        *,
        project_id: str | None,
        revision: int | None,
    ) -> tuple[WindowInfo | None, dict[str, Any]]:
        """Prefer a live wrapper window that matches the requested project/revision."""

        selected_window = self.backend.find_window()
        candidates: list[WindowInfo] = []
        list_windows = getattr(self.backend, "list_windows", None)
        if callable(list_windows):
            try:
                candidates.extend(list_windows())
            except Exception:
                candidates = []
        if selected_window is not None and not any(window.handle == selected_window.handle for window in candidates):
            candidates.insert(0, selected_window)

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
                "fallback_used": False,
            }

        fallback_window = selected_window or (candidates[0] if candidates else None)
        return fallback_window, {
            "requested_project_id": project_id,
            "requested_revision": revision,
            "matched_project_window": False,
            "matching_window_count": 0,
            "target_handle": fallback_window.handle if fallback_window else None,
            "target_title": fallback_window.title if fallback_window else None,
            "fallback_used": fallback_window is not None and requested_target,
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
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "project_name": project_name,
            "project_path": str(project_path),
            "document_path": str(document_path),
            "metadata_path": str(metadata_path),
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
        expected_project_name = _safe_component(expected_project_name) if expected_project_name else None
        while time.monotonic() < deadline:
            time.sleep(poll_interval_seconds)
            candidates: list[WindowInfo] = []
            list_windows = getattr(self.backend, "list_windows", None)
            if expected_project_name and callable(list_windows):
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
                project_name = _project_name_from_window_title(candidate.title)
                if expected_project_name and project_name == expected_project_name:
                    return candidate
                if "materials studio" in title:
                    if not expected_project_name:
                        return candidate
        return latest

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
    if status.get("process_count") != 1:
        reasons.append("exactly_one_matstudio_process_required")
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


def _view_replay_execution_recipe(
    step: dict[str, Any],
    command_evidence: dict[str, Any],
    *,
    runtime_ui_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative machine-readable recipe for one prepared view."""

    view_name = str(step.get("view_name") or "")
    camera = step.get("camera") if isinstance(step.get("camera"), dict) else {}
    crystallography = (
        step.get("crystallography")
        if isinstance(step.get("crystallography"), dict)
        else {}
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
        "schema_version": 1,
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
        automation_ready = bool(registry_verified and command_available)
        block_reasons: list[str] = []
        if not registry_verified:
            block_reasons.append("local_view_command_registry_not_verified")
        if not command_available:
            block_reasons.append("reset_view_command_not_registered")
        return {
            **base,
            "status": (
                "native_accessibility_command_ready"
                if automation_ready
                else "native_accessibility_command_unverified"
            ),
            "automation_ready": automation_ready,
            "allowed_native_command_ids": [command_id],
            "native_command_id": command_id,
            "accessibility_target": {
                "toolbar_name": "3D Viewer",
                "control_name": "3D Viewer Reset View",
                "command_id": command_id,
            },
            "expected_axis_layout": {
                "screen_right": "A",
                "screen_up": "B",
                "view_depth": "C",
            },
            "action_sequence": [
                "verify_exact_current_wrapper_window",
                "activate_target_window",
                "refresh_accessibility_tree_and_locate_named_control",
                "invoke_named_reset_view_control",
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
        automation_ready = bool(
            registry_verified
            and reset_command_available
            and keyboard_help_verified
            and movement_command_available
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

        common_recipe: dict[str, Any] = {
            **base,
            "status": (
                "documented_staged_keyboard_sequence_ready"
                if automation_ready and staged_keyboard_recipe
                else "documented_keyboard_sequence_ready"
                if automation_ready
                else "documented_staged_keyboard_sequence_unverified"
                if staged_keyboard_recipe
                else "documented_keyboard_sequence_unverified"
            ),
            "automation_ready": automation_ready,
            "allowed_native_command_ids": [
                command_id
                for command_id in (reset_command_id, movement_command_id)
                if isinstance(command_id, str)
            ],
            "native_command_id": reset_command_id,
            "reset_before_key_sequence": True,
            "prohibited_modifier_keys": ["Shift"],
            "rotation_increment_user_configurable": True,
            "expected_axis_layout": dict(documented_key_recipe["expected_axis_layout"]),
            "accessibility_target": {
                "toolbar_name": "3D Viewer",
                "control_name": "3D Viewer Reset View",
                "command_id": reset_command_id,
            },
            "keyboard_focus_target": "visually_verified_empty_3d_viewer_region",
            "post_action_checks": [
                "verify_expected_axis_layout",
                "verify_projection_bbox_and_overlap_count",
                "verify_structure_geometry_unchanged",
            ],
            "action_sequence": [
                "verify_exact_current_wrapper_window",
                "activate_target_window",
                "refresh_accessibility_tree_and_locate_named_reset_control",
                "invoke_named_reset_view_control",
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
                "schema_version": 2,
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
                    "toolbar_name": "3D Movement",
                    "control_name": "Movement",
                    "command_id": movement_command_id,
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
                    "refresh_accessibility_tree_and_locate_named_reset_control",
                    "invoke_named_reset_view_control",
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
        reset_command_id = "cmdViewer3DResetView"
        view_onto_command_id = "cmdViewer3DViewOnto"
        reset_command_available = reset_command_id in command_ids
        view_onto_command_available = view_onto_command_id in command_ids
        runtime_preflight = runtime_ui_preflight if isinstance(runtime_ui_preflight, dict) else {}
        selection_profile = runtime_preflight.get("selection_profile")
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
        runtime_gate_satisfied = runtime_preflight.get("automation_gate_satisfied") is True
        selection_profile_verified = selection_profile in {
            "object_tree_exact_item",
            "viewport_unique_plane_properties_verified",
        }
        runtime_block_reasons = [
            str(item)
            for item in runtime_preflight.get("block_reasons") or []
            if str(item)
        ]
        if not runtime_preflight:
            runtime_block_reasons = ["runtime_miller_plane_ui_preflight_missing"]
        automation_ready = bool(
            registry_verified
            and reset_command_available
            and view_onto_command_available
            and index_error is None
            and all(evidence_requirements.values())
            and runtime_gate_satisfied
            and selection_profile_verified
        )
        block_reasons: list[str] = []
        if not registry_verified:
            block_reasons.append("local_view_command_registry_not_verified")
        if not reset_command_available:
            block_reasons.append("reset_view_command_not_registered")
        if not view_onto_command_available:
            block_reasons.append("view_onto_command_not_registered")
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
            reset_command_id,
            "cmdSymmetryBuilderMillerPlanes",
            "cmdGPEToggleExplorer",
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
            "schema_version": 6,
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
            "allowed_native_command_ids": [view_onto_command_id],
            "native_command_id": view_onto_command_id,
            "modifier_keys": [],
            "prohibited_modifier_keys": ["Shift", "Ctrl", "Alt", "Win"],
            "supporting_native_command_ids": supporting_native_command_ids,
            "runtime_ui_preflight": {
                "required": True,
                "status": runtime_preflight.get("status") or "missing",
                "automation_gate_satisfied": runtime_gate_satisfied,
                "artifact_path": runtime_preflight.get("artifact_path"),
                "binding_verified": runtime_preflight.get("binding_verified") is True,
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
                "semantic_targeting": "named_toolbar_and_native_popup_menu_item_rect",
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
                "in_plane_roll_policy": "materials_studio_native_smallest_acute_angle_from_reset",
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
                    "Undo Reset View",
                ],
                "require_exactly_one_new_miller_plane": True,
                "require_selected_plane_count": 1,
                "require_document_clean_before_and_after": True,
                "require_no_temporary_miller_nodes_after_cleanup": True,
                "require_structure_artifact_sha256_unchanged": True,
                "restore_initial_view_via_whitelisted_undo": True,
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
                "invoke_named_reset_view_control",
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
                "invoke_named_3d_viewer_recenter_popup_view_onto_item",
                "capture_fresh_screenshot_before_cleanup",
                *(
                    ["verify_direct_lattice_direction_matches_collinear_plane_normal"]
                    if direction_via_miller_plane
                    else []
                ),
                "verify_plane_normal_and_report_native_in_plane_roll_separately",
                "undo_only_whitelisted_view_onto_create_miller_plane_and_reset_view_actions",
                "verify_document_clean_tree_restored_view_restored_and_sha256_unchanged",
                "record_view_replay_event_with_miller_plane_evidence",
            ],
            "safety_notes": [
                (
                    "Do not reuse viewport coordinates; derive the unique transient-plane hit region from fresh before/after screenshots and verify the result in Properties Explorer."
                    if viewport_selection_profile
                    else "Do not use blind viewport coordinates; derive the click rectangle from the exact Object Tree item."
                ),
                "Do not click Tools > Miller Planes with a pointer or accessibility click; use Alt+T then M.",
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
                "Undo View Onto, Create Miller Plane, and Reset View in exact stack order; stop before any label outside the explicit whitelist.",
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
                registry_text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
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


def _refresh_view_replay_summary(manifest: dict[str, Any]) -> None:
    """Refresh replay progress while preserving the current preflight state."""

    replay_events = [item for item in manifest.get("replay_events") or [] if isinstance(item, dict)]
    accepted_views = {
        str(item.get("view_name"))
        for item in replay_events
        if item.get("accepted") is True and item.get("view_name") is not None
    }
    supported_steps = [
        item
        for item in manifest.get("views") or []
        if isinstance(item, dict) and item.get("supported") is True
    ]
    supported_view_names = {str(item.get("view_name")) for item in supported_steps}
    accepted_supported_views = accepted_views & supported_view_names
    pending_steps = [
        item for item in supported_steps if str(item.get("view_name")) not in accepted_supported_views
    ]
    automation_ready_steps = [
        item
        for item in pending_steps
        if isinstance(item.get("execution_recipe"), dict)
        and item["execution_recipe"].get("automation_ready") is True
    ]
    review_required_steps = [item for item in pending_steps if item not in automation_ready_steps]
    pending_view_names = [str(item.get("view_name")) for item in pending_steps]
    automation_ready_view_names = [str(item.get("view_name")) for item in automation_ready_steps]
    review_required_view_names = [str(item.get("view_name")) for item in review_required_steps]
    all_confirmed = bool(supported_view_names) and supported_view_names <= accepted_views
    manifest["replay_summary"] = {
        "event_count": len(replay_events),
        "accepted_event_count": sum(1 for item in replay_events if item.get("accepted") is True),
        "accepted_view_count": len(accepted_supported_views),
        "supported_view_count": len(supported_view_names),
        "pending_view_count": len(pending_steps),
        "pending_view_names": pending_view_names,
        "automation_ready_pending_view_count": len(automation_ready_steps),
        "automation_ready_pending_view_names": automation_ready_view_names,
        "review_required_pending_view_count": len(review_required_steps),
        "review_required_pending_view_names": review_required_view_names,
        "all_supported_views_confirmed": all_confirmed,
    }
    preflight = manifest.get("preflight") if isinstance(manifest.get("preflight"), dict) else {}
    next_pending_step = pending_steps[0] if pending_steps else None
    next_automation_step = automation_ready_steps[0] if automation_ready_steps else None
    next_pending_recipe = (
        next_pending_step.get("execution_recipe")
        if next_pending_step is not None
        and isinstance(next_pending_step.get("execution_recipe"), dict)
        else {}
    )
    runtime_ui_preflight_required = bool(
        next_pending_recipe.get("recipe_kind") in MILLER_VIEW_ONTO_RECIPE_KINDS
        and any(
            str(reason).startswith("runtime_")
            for reason in next_pending_recipe.get("block_reasons") or []
        )
    )
    if all_confirmed:
        continuation_status = "complete"
        recommended_executor = None
        recommended_action = "review_current_revision_after_all_prepared_views_were_confirmed"
        recommended_mcp_tool = "material_studio_live_project_status"
    elif preflight.get("ready_for_external_replay") is not True:
        continuation_status = "preflight_blocked"
        recommended_executor = None
        recommended_action = "resolve_view_replay_preflight_blockers"
        recommended_mcp_tool = (manifest.get("next_action") or {}).get("recommended_tool")
    elif runtime_ui_preflight_required:
        continuation_status = "runtime_ui_preflight_required"
        recommended_executor = "computer_use_or_manual_review"
        recommended_action = (
            "observe_current_window_miller_plane_controls_then_submit_bound_runtime_ui_evidence"
        )
        recommended_mcp_tool = "material_studio_gui_prepare_view_replay"
    elif next_automation_step is not None:
        continuation_status = "automatic_recipe_ready"
        recommended_executor = "computer_use"
        next_recipe = (
            next_automation_step.get("execution_recipe")
            if isinstance(next_automation_step.get("execution_recipe"), dict)
            else {}
        )
        recommended_action = (
            "execute_documented_direction_via_collinear_miller_plane_view_onto_recipe_cleanup_then_record_view"
            if next_recipe.get("recipe_kind")
            == "crystal_direction_via_collinear_miller_plane_view_onto"
            else "execute_documented_miller_plane_view_onto_recipe_cleanup_then_record_view"
            if next_recipe.get("recipe_kind") == "miller_plane_view_onto"
            else "execute_documented_staged_keyboard_recipe_restore_settings_then_record_view"
            if isinstance(next_recipe.get("keyboard_stages"), list)
            else
            "execute_documented_keyboard_recipe_then_record_view"
            if isinstance(next_recipe.get("key_sequence"), list)
            else "execute_named_accessibility_recipe_then_record_view"
        )
        recommended_mcp_tool = "material_studio_gui_record_view_replay"
    elif next_pending_step is not None:
        continuation_status = "reviewed_camera_backend_required"
        recommended_executor = "reviewed_copy_script_or_manual_gui_review"
        recommended_action = "obtain_reviewed_camera_backend_then_record_view"
        recommended_mcp_tool = "material_studio_gui_copy_script_assist"
    else:
        continuation_status = "no_supported_pending_view"
        recommended_executor = None
        recommended_action = "review_view_manifest"
        recommended_mcp_tool = "material_studio_live_project_status"
    selected_next_step = next_automation_step or next_pending_step
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
    keyboard_stage_payload_hint = (
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
    miller_plane_payload_hint: dict[str, Any] | None = None
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
        miller_plane_payload_hint = {
            "miller_plane_indices": selected_recipe.get("miller_plane_indices"),
            "dialog_miller_indices": selected_recipe.get("dialog_miller_indices"),
            "dialog_miller_indices_text_before_create": selected_recipe.get(
                "dialog_miller_indices_text"
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
            "selection_method": selected_recipe.get("selection_method"),
            "object_tree_path_suffix": selected_recipe.get("selection_path_suffix"),
            **(
                {
                    "viewport_hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
                    "fresh_before_after_screenshots_observed": True,
                    "unique_transient_plane_region_observed": True,
                    "properties_selection_verified": True,
                    "view_onto_popup_menu_observed": True,
                    "dialog_show_set_of_parallel_planes": False,
                    "dialog_show_symmetry_images": False,
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
            "plane_normal_matches_manifest": True,
            **(
                {"direct_lattice_direction_matches_manifest": True}
                if selected_recipe.get("recipe_kind")
                == "crystal_direction_via_collinear_miller_plane_view_onto"
                else {}
            ),
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_policy_observed": True,
            "reset_view_before_alignment": True,
            "screenshot_captured_before_cleanup": True,
            "document_was_clean_before_replay": True,
            "temporary_miller_plane_cleanup_verified": True,
            "no_temporary_miller_nodes_remaining": True,
            "document_clean_after_replay": True,
            "post_replay_view_restored": True,
            "structure_artifact_path": artifact_path_text,
            "structure_artifact_sha256_before": artifact_hash,
            "structure_artifact_sha256_after": artifact_hash,
            "undo_labels_applied": [
                "Undo View Onto Miller Plane",
                "Undo Create Miller Plane",
                "Undo Reset View",
            ],
        }
    record_payload_hint = {
        "project_id": manifest.get("project_id"),
        "revision": manifest.get("revision"),
        "view_name": selected_next_step.get("view_name") if selected_next_step is not None else None,
        "source": "computer_use" if next_automation_step is not None else "reviewed_copy_script",
        "model_visible": True,
        "camera_matches_manifest": True,
        "expected_revision": manifest.get("revision"),
        "expected_window_handle": preflight_target_window.get("handle"),
        "expected_window_title": preflight_target_window.get("title"),
        "native_command_id": selected_recipe.get("native_command_id"),
        "key_sequence": selected_recipe.get("key_sequence"),
        "reset_before_key_sequence": selected_recipe.get("reset_before_key_sequence"),
        "rotation_increment_degrees": selected_recipe.get("rotation_increment_degrees"),
        "modifier_keys": selected_recipe.get("modifier_keys"),
        "keyboard_stages": keyboard_stage_payload_hint,
        "rotation_increment_restored_degrees": selected_recipe.get(
            "restore_rotation_increment_degrees"
        ),
        "movement_options_command_id": selected_recipe.get("movement_options_command_id"),
        "movement_angle_control_id": selected_recipe.get("movement_angle_control_id"),
        "movement_screen_factor_control_id": selected_recipe.get(
            "movement_screen_factor_control_id"
        ),
        "movement_screen_factor": selected_recipe.get("movement_screen_factor_expected"),
        "movement_dialog_closed": selected_recipe.get("movement_dialog_closed_after_restore"),
        "miller_plane_evidence": miller_plane_payload_hint,
    }
    high_level_payload_hint = (
        {
            "user_request": f"Record the verified {selected_next_step.get('view_name')} GUI view replay.",
            "project_id": manifest.get("project_id"),
            "view_replay_confirmation": {
                key: record_payload_hint.get(key)
                for key in (
                    "view_name",
                    "source",
                    "model_visible",
                    "camera_matches_manifest",
                    "expected_revision",
                    "expected_window_handle",
                    "expected_window_title",
                    "native_command_id",
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
                    "miller_plane_evidence",
                )
                if record_payload_hint.get(key) is not None
            },
        }
        if selected_next_step is not None
        else {}
    )
    continuation_payload_hint = record_payload_hint
    continuation_high_level_payload_hint = high_level_payload_hint
    payload_hint_is_directly_callable = True
    if runtime_ui_preflight_required:
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
    manifest["replay_continuation"] = {
        "status": continuation_status,
        "automatic_replay_ready": next_automation_step is not None,
        "runtime_ui_preflight_required": runtime_ui_preflight_required,
        "runtime_ui_preflight": selected_recipe.get("runtime_ui_preflight"),
        "recommended_executor": recommended_executor,
        "recommended_action": recommended_action,
        "recommended_mcp_tool": recommended_mcp_tool,
        "record_tool": "material_studio_gui_record_view_replay",
        "high_level_record_tool": "material_studio_live_modeling_request",
        "next_pending_view_name": (
            str(next_pending_step.get("view_name")) if next_pending_step is not None else None
        ),
        "next_automation_ready_view_name": (
            str(next_automation_step.get("view_name")) if next_automation_step is not None else None
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
        "evidence_values_must_be_observed_not_assumed": bool(miller_plane_payload_hint),
    }
    if preflight.get("ready_for_external_replay") is not True:
        if accepted_views:
            manifest["replay_status"] = "blocked_with_prior_confirmation"
        return
    if all_confirmed:
        manifest["replay_status"] = "externally_confirmed"
    elif accepted_views:
        manifest["replay_status"] = "partially_confirmed"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON artifact in its existing workspace directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
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
    wrapper_entries = [entry for entry in window_inventory if entry.get("project_wrapper_metadata")]
    mcp_title_entries = [
        entry for entry in window_inventory if _project_name_from_window_title(str(entry.get("title") or "")) is not None
    ]
    primary_entries = [entry for entry in window_inventory if _is_primary_materials_studio_window_entry(entry)]
    dialog_entries = [entry for entry in window_inventory if _is_materials_studio_dialog_entry(entry)]
    file_association_dialog_entries = [
        entry for entry in window_inventory if _is_file_association_dialog_entry(entry)
    ]
    welcome_dialog_entries = [entry for entry in window_inventory if _is_welcome_dialog_entry(entry)]
    startup_dialog_entries = [entry for entry in window_inventory if _is_startup_dialog_entry(entry)]
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
    matched_project_window = bool(target_resolution and target_resolution.get("matched_project_window"))
    fallback_used = bool(target_resolution and target_resolution.get("fallback_used"))
    blocking_dialog_entries = [
        entry for entry in dialog_entries if not _is_file_association_dialog_entry(entry)
    ]
    unresolved_blocking_dialog_entries = [
        entry for entry in blocking_dialog_entries if entry not in resolvable_startup_dialog_entries
    ]

    warnings: list[str] = []
    single_window_violation_reasons: list[str] = []
    if len(processes) > 1:
        warnings.append("multiple_matstudio_processes_detected")
        single_window_violation_reasons.append("multiple_matstudio_processes_detected")
    if len(primary_entries) > 1:
        warnings.append("multiple_matstudio_windows_detected")
        single_window_violation_reasons.append("multiple_matstudio_windows_detected")
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
    if requested_target and fallback_used:
        warnings.append("target_project_window_not_verified")
    if target_window is not None and selected_window is not None and not target_window_is_selected:
        warnings.append("selected_window_is_not_target_window")
    warnings.extend(interaction_activation_reasons)
    if target_window is not None and not same_window_open_supported:
        warnings.append("same_window_open_not_supported_by_local_backend")

    if target_window is None:
        recommended_tool = "material_studio_gui_launch"
        recommended_action = "explicitly_launch_or_activate_materials_studio_only_if_intended"
        ready_for_snapshot = False
        ready_for_open = False
    elif single_window_violation_reasons:
        recommended_tool = "material_studio_gui_status"
        recommended_action = "close_save_extra_matstudio_windows_then_retry_hotload"
        ready_for_snapshot = True
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
    elif requested_target and not matched_project_window:
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
    needs_reload = bool(requested_target and not matched_project_window)
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
    )
    can_apply_current_revision_without_new_window = can_hotload_without_new_window
    if target_window is None:
        status = "target_window_missing"
    elif needs_single_window_resolution:
        status = "single_window_policy_violation"
    elif needs_dialog_resolution:
        status = "modal_dialog_blocking_hotload"
    elif startup_dialog_open_ready:
        status = "startup_dialog_ready_for_same_window_open"
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
    if recommended_tool == "material_studio_gui_open_structure":
        payload_hint["execution_mode"] = "execute"
        payload_hint["open_in_gui"] = True
    elif recommended_tool == "material_studio_gui_snapshot":
        payload_hint["take_snapshot"] = True
    elif recommended_tool == "material_studio_gui_activate":
        payload_hint["take_snapshot"] = True

    return {
        "status": status,
        "process_count": len(processes),
        "window_count": len(window_inventory),
        "primary_window_count": len(primary_entries),
        "dialog_window_count": len(dialog_entries),
        "blocking_dialog_count": len(blocking_dialog_entries),
        "unresolved_blocking_dialog_count": len(unresolved_blocking_dialog_entries),
        "resolvable_startup_dialog_count": len(resolvable_startup_dialog_entries),
        "file_association_dialog_count": len(file_association_dialog_entries),
        "welcome_dialog_count": len(welcome_dialog_entries),
        "startup_dialog_count": len(startup_dialog_entries),
        "wrapper_window_count": len(wrapper_entries),
        "mcp_title_window_count": len(mcp_title_entries),
        "unmatched_mcp_title_window_count": unmatched_mcp_title_count,
        "requested_project_id": requested_project_id,
        "requested_revision": requested_revision,
        "selected_window_handle": selected_window.handle if selected_window else None,
        "selected_window_title": selected_window.title if selected_window else None,
        "selected_window_project_id": selected_entry.get("project_id") if selected_entry else None,
        "selected_window_revision": selected_entry.get("revision") if selected_entry else None,
        "selected_window_has_project_metadata": bool(selected_entry and selected_entry.get("project_wrapper_metadata")),
        "target_window_handle": target_window.handle if target_window else None,
        "target_window_title": target_window.title if target_window else None,
        "target_window_project_id": target_entry.get("project_id") if target_entry else None,
        "target_window_revision": target_entry.get("revision") if target_entry else None,
        "target_window_has_project_metadata": bool(target_entry and target_entry.get("project_wrapper_metadata")),
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
        "current_revision_loaded": bool(requested_target and matched_project_window),
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

    normalized = title.strip()
    suffix = " - Materials Studio"
    if not normalized.endswith(suffix):
        return None
    project_name = normalized[: -len(suffix)].strip()
    return _safe_component(project_name) if project_name else None


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


def _find_file_open_dialog(*, pid: int | None, timeout_seconds: float) -> WindowInfo | None:
    """Find a common file-open dialog owned by Materials Studio."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        candidates = _find_windows(pid=pid)
        dialogs = [window for window in candidates if _looks_like_file_open_dialog(window)]
        if dialogs:
            return dialogs[0]
        time.sleep(0.25)
    return None


def _looks_like_file_open_dialog(window: WindowInfo) -> bool:
    """Return true when a top-level dialog appears to be a file-open dialog."""

    title = window.title.lower()
    if "file associations" in title or "welcome to materials studio" in title:
        return False
    if (window.class_name or "").lower() != "#32770":
        return False
    title_patterns = ("open", "select", "browse", "choose", "打开", "选择")
    if any(pattern in title for pattern in title_patterns):
        return True
    return _dialog_has_file_path_controls(window.handle)


def _dialog_has_file_path_controls(dialog_handle: int) -> bool:
    """Return true when a dialog exposes standard filename controls."""

    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    for control_id in (0x0480, 0x047C, 0x047D, 0x047E, 1):
        if user32.GetDlgItem(ctypes.c_void_p(dialog_handle), control_id):
            return True
    return any((_window_class(child) or "").lower() in {"edit", "combobox"} for child in _descendant_windows(dialog_handle))


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
) -> dict[str, Any] | None:
    """Open a project through the Materials Studio welcome page when present."""

    dialogs = [
        window
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
    ]
    if not dialogs:
        return None

    handled: list[dict[str, Any]] = []
    for dialog in dialogs:
        if dialog.title.strip().lower() != "new project":
            continue
        _cancel_dialog(dialog.handle)
        _wait_for_window_absent(dialog.handle, timeout_seconds=3.0)
        handled.append({"action": "cancel_empty_new_project_dialog", "dialog": dialog.to_dict()})

    welcome_dialogs = [
        window
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and window.title.strip().lower() == "welcome to materials studio"
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
                timeout_seconds=60.0,
            )
        )
        return {
            "method": "existing_window_welcome_dialog",
            "path": str(path),
            "window": source_window.to_dict(),
            "handled_prompts": handled,
        }

    remaining = [
        window.to_dict()
        for window in _find_windows(pid=pid)
        if (window.class_name or "").lower() == "#32770"
        and "file associations" not in window.title.strip().lower()
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
    _click_button_handle(int(browse_button["handle"]))
    browse_dialog = _find_file_open_dialog(pid=pid, timeout_seconds=10.0)
    if browse_dialog is None:
        raise GuiError("Materials Studio welcome dialog did not expose its existing-project file picker.")
    submission_attempts: list[dict[str, Any]] = []
    field_result: dict[str, Any] = {}
    picker_closed = False
    for attempt in range(1, 4):
        field_result = _set_common_dialog_filename(browse_dialog.handle, path_text)
        _click_dialog_ok(browse_dialog.handle)
        picker_closed = _wait_for_window_absent(browse_dialog.handle, timeout_seconds=4.0)
        submission_attempts.append(
            {
                "attempt": attempt,
                "filename_field": field_result,
                "picker_closed": picker_closed,
            }
        )
        if picker_closed:
            break
        time.sleep(0.25)
    if not picker_closed:
        raise GuiError("Materials Studio existing-project file picker did not close after path submission.")
    visible_path = _window_text(int(edit_control["handle"]))
    if Path(visible_path).expanduser() != Path(path_text).expanduser():
        raise GuiError("Materials Studio welcome dialog did not retain the requested MCP project path.")
    _click_button_handle(int(ok_button["handle"]))
    return {
        "ok": True,
        "target_handle": int(edit_control["handle"]),
        "target_class": str(edit_control.get("class") or ""),
        "verified_path": visible_path,
        "browse_dialog": browse_dialog.to_dict(),
        "filename_field": field_result,
        "filename_submission_attempts": submission_attempts,
    }


def _resolve_same_window_open_prompts(
    *,
    pid: int | None,
    source_window: WindowInfo,
    path_text: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Handle Materials Studio prompts that appear while reusing one GUI window."""

    handled: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    auto_save_allowed = _is_mcp_generated_project_title(source_window.title)
    while time.monotonic() < deadline:
        dialogs = [
            window
            for window in _find_windows(pid=pid)
            if (window.class_name or "").lower() == "#32770"
        ]
        if not dialogs:
            return handled
        acted = False
        for dialog in dialogs:
            title = dialog.title.lower()
            controls = _dialog_controls(dialog.handle)
            button_texts = {str(control.get("text") or "").lower() for control in controls if control.get("class") == "Button"}
            if _looks_like_file_open_dialog(dialog):
                field_result = _set_common_dialog_filename(dialog.handle, path_text)
                _click_dialog_ok(dialog.handle)
                handled.append(
                    {
                        "action": "resubmit_open_project_dialog",
                        "dialog": dialog.to_dict(),
                        "filename_field": field_result,
                    }
                )
                acted = True
                time.sleep(1.0)
                break
            if title == "save as":
                if not auto_save_allowed:
                    _cancel_dialog(dialog.handle)
                    raise GuiError(
                        "Materials Studio requested Save As for a non-MCP project while opening a structure. "
                        "The dialog was cancelled to avoid overwriting user files."
                    )
                _click_dialog_ok(dialog.handle)
                handled.append({"action": "save_as_current_mcp_project", "dialog": dialog.to_dict()})
                acted = True
                time.sleep(1.0)
                break
            if _looks_like_save_confirmation(dialog, button_texts):
                if not auto_save_allowed:
                    _cancel_dialog(dialog.handle)
                    raise GuiError(
                        "Materials Studio requested permission to save the current non-MCP project. "
                        "The dialog was cancelled to avoid modifying user files."
                    )
                _confirm_yes_dialog(dialog.handle)
                handled.append({"action": "confirm_save_current_mcp_project", "dialog": dialog.to_dict()})
                acted = True
                time.sleep(1.0)
                break
        if not acted:
            unresolved = [dialog.to_dict() for dialog in dialogs]
            raise GuiError(f"Unhandled Materials Studio dialog during same-window open: {unresolved}")
    remaining = [window.to_dict() for window in _find_windows(pid=pid) if (window.class_name or "").lower() == "#32770"]
    if remaining:
        raise GuiError(f"Timed out while handling Materials Studio same-window open dialogs: {remaining}")
    return handled


def _looks_like_save_confirmation(dialog: WindowInfo, button_texts: set[str]) -> bool:
    """Return true for Materials Studio save-current-project confirmations."""

    if dialog.title.lower() not in {"materials studio", "confirm save as"}:
        return False
    has_yes = any(text in {"&yes", "yes", "是(&y)", "是"} for text in button_texts)
    has_no_or_cancel = any(text in {"&no", "no", "cancel", "取消", "否(&n)", "否"} for text in button_texts)
    return has_yes and has_no_or_cancel


def _is_mcp_generated_project_title(title: str) -> bool:
    """Return whether a Materials Studio title appears to be an MCP wrapper project."""

    project_name = _project_name_from_window_title(title)
    if not project_name:
        return False
    normalized = project_name.lower()
    return normalized.startswith("msmcp_") or normalized.startswith("ms_mcp_")


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


def _confirm_yes_dialog(dialog_handle: int) -> None:
    """Confirm a Yes/No Materials Studio dialog."""

    for child in _descendant_windows(dialog_handle):
        if (_window_class(child) or "").lower() != "button":
            continue
        label = (_window_text(child) or "").lower()
        if label in {"&yes", "yes", "是(&y)", "是"}:
            _bring_window_foreground(dialog_handle)
            ctypes.windll.user32.SendMessageW(ctypes.c_void_p(child), 0x00F5, 0, 0)  # BM_CLICK
            time.sleep(0.2)
            if ctypes.windll.user32.IsWindow(ctypes.c_void_p(dialog_handle)):
                _press_enter_on_window(dialog_handle)
            return
    _press_enter_on_window(dialog_handle)


def _cancel_dialog(dialog_handle: int) -> None:
    """Cancel a modal dialog."""

    if os.name != "nt":
        return
    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(dialog_handle), 0x0010, 0, 0)  # WM_CLOSE


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


def _click_dialog_ok(dialog_handle: int) -> None:
    """Confirm a common file dialog without using screen coordinates."""

    if os.name != "nt":
        raise GuiError("Win32 file dialogs are only available on Windows.")
    user32 = ctypes.windll.user32
    ok_button = int(user32.GetDlgItem(ctypes.c_void_p(dialog_handle), 1) or 0)
    if ok_button:
        user32.SendMessageW(ctypes.c_void_p(ok_button), 0x00F5, 0, 0)  # BM_CLICK
    else:
        user32.PostMessageW(ctypes.c_void_p(dialog_handle), 0x0111, 1, 0)  # WM_COMMAND, IDOK
    time.sleep(0.2)
    if user32.IsWindow(ctypes.c_void_p(dialog_handle)):
        _press_enter_on_window(dialog_handle)


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
