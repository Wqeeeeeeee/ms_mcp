"""Narrow UI Automation support for deterministic Materials Studio view replay.

This module intentionally exposes only semantic UIA invocation and unmodified
arrow-key recipes. It never uses pointer coordinates and never records visual
acceptance on its own.
"""

from __future__ import annotations

import ctypes
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


SAFE_STANDARD_VIEW_KEY_SEQUENCES: dict[str, list[str]] = {
    "front": [],
    "back": ["Left", "Left", "Left", "Left"],
    "right": ["Up", "Up", "Left", "Left"],
    "left": ["Up", "Up", "Right", "Right"],
    "top": ["Up", "Up"],
    "bottom": ["Left", "Left", "Left", "Left", "Down", "Down"],
}
SAFE_ARROW_KEYS = frozenset({"Up", "Down", "Left", "Right"})
VIEWPORT_CLASS_NAME = "CViewer3DCtrl"
VIEWPORT_CONTROL_TYPE = "Pane"


class UiaReplayError(RuntimeError):
    """Raised when an exact UIA binding or action gate cannot be satisfied."""


class ViewReplayAutomationBackend(Protocol):
    """Protocol used by the GUI controller and deterministic test doubles."""

    supported: bool
    unavailable_reason: str | None

    def probe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        """Read the exact window's UIA tree without invoking any control."""
        ...

    def execute_standard_recipe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        """Execute one allowlisted Reset plus unmodified-arrow recipe."""
        ...


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _default_foreground_handle() -> int | None:
    if os.name != "nt":
        return None
    try:
        handle = int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None
    return handle or None


def _safe_call(value: Any, name: str, default: Any = None) -> Any:
    try:
        item = getattr(value, name)
        return item() if callable(item) else item
    except Exception:
        return default


def _element_value(wrapper: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(wrapper.element_info, name)
    except Exception:
        return default


def _runtime_identity(wrapper: Any) -> tuple[Any, ...]:
    try:
        return ("uia", *tuple(wrapper.element_info.element.GetRuntimeId()))
    except Exception:
        runtime_id = _element_value(wrapper, "runtime_id")
        if isinstance(runtime_id, (list, tuple)):
            return ("declared", *tuple(runtime_id))
        return ("object", id(wrapper))


def _invoke_pattern_available(wrapper: Any) -> bool:
    try:
        return getattr(wrapper, "iface_invoke") is not None
    except Exception:
        return False


def _normalized_role(wrapper: Any) -> str:
    control_type = str(_element_value(wrapper, "control_type", "") or "")
    if control_type == "CheckBox":
        return "checkbox"
    if control_type == "Separator":
        return "separator"
    return control_type.strip().lower() or "unknown"


class PywinautoViewReplayBackend:
    """A narrowly scoped pywinauto UIA backend for Materials Studio 20.1."""

    def __init__(
        self,
        *,
        desktop_factory: Callable[..., Any] | None = None,
        keyboard_sender: Callable[[str], None] | None = None,
        foreground_handle_getter: Callable[[], int | None] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        platform_supported: bool | None = None,
    ) -> None:
        self._desktop_factory = desktop_factory
        self._keyboard_sender = keyboard_sender
        self._foreground_handle_getter = (
            foreground_handle_getter or _default_foreground_handle
        )
        self._sleep = sleep_fn
        windows_supported = os.name == "nt" if platform_supported is None else bool(
            platform_supported
        )
        self.supported = windows_supported
        self.unavailable_reason: str | None = None

        if not windows_supported:
            self.unavailable_reason = "Local UI Automation view replay is Windows-only."
            return
        if self._desktop_factory is not None and self._keyboard_sender is not None:
            return
        try:
            from pywinauto import Desktop
            from pywinauto.keyboard import send_keys
        except Exception as exc:
            self.supported = False
            self.unavailable_reason = (
                "pywinauto is unavailable for local UI Automation view replay: "
                f"{exc}"
            )
            return
        self._desktop_factory = self._desktop_factory or Desktop
        if self._keyboard_sender is None:
            self._keyboard_sender = lambda token: send_keys(token, pause=0.05)

    def probe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        """Return server-generated, read-only accessibility evidence."""

        if not self.supported:
            return {
                "supported": False,
                "safe_for_standard_view_replay": False,
                "unavailable_reason": self.unavailable_reason,
                "block_reasons": ["local_uia_backend_unavailable"],
            }
        try:
            snapshot = self._inspect_window(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
            )
        except Exception as exc:
            return {
                "supported": True,
                "safe_for_standard_view_replay": False,
                "unavailable_reason": None,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "block_reasons": ["local_uia_tree_probe_failed"],
            }

        controls: list[dict[str, Any]] = []
        anonymous_toolbars: list[dict[str, Any]] = []
        resolved_command_ids: set[str] = set()
        block_reasons = list(snapshot["block_reasons"])
        for toolbar_name, toolbar in snapshot["toolbars"].items():
            contract = toolbar_contracts[toolbar_name]
            entries = list(contract.get("entries") or [])
            all_tools_unnamed = all(
                child["observed_control_name"] is None
                for child in toolbar["children"]
                if child["role"] != "separator"
            )
            if toolbar["contract_verified"] and all_tools_unnamed:
                anonymous_toolbars.append(
                    {
                        "observed_toolbar_name": toolbar_name,
                        "toolbar_automation_id": toolbar["toolbar_automation_id"],
                        "children": [
                            {
                                "element_index": child[
                                    "computer_use_compatible_element_index"
                                ],
                                "role": child["role"],
                                "enabled": child["enabled"],
                                "observed_control_name": child[
                                    "observed_control_name"
                                ],
                            }
                            for child in toolbar["children"]
                        ],
                    }
                )

            for child_index, (kind, command_id) in enumerate(entries):
                if kind != "tool" or command_id not in command_labels:
                    continue
                if child_index >= len(toolbar["children"]):
                    controls.append(
                        {
                            "command_id": command_id,
                            "observed_control_name": None,
                            "invoke_supported": False,
                        }
                    )
                    block_reasons.append(
                        f"local_uia_{command_id}_toolbar_child_missing"
                    )
                    continue
                child = toolbar["children"][child_index]
                expected_name = command_labels[command_id]
                named_ready = bool(
                    toolbar["contract_verified"]
                    and child["observed_control_name"] == expected_name
                    and child["enabled"]
                    and child["invoke_supported"]
                )
                controls.append(
                    {
                        "command_id": command_id,
                        "observed_control_name": child[
                            "observed_control_name"
                        ],
                        "invoke_supported": named_ready,
                    }
                )
                anonymous_ready = bool(
                    toolbar["contract_verified"]
                    and all_tools_unnamed
                    and child["enabled"]
                    and child["invoke_supported"]
                )
                if named_ready or anonymous_ready:
                    resolved_command_ids.add(command_id)
                else:
                    block_reasons.append(
                        f"local_uia_{command_id}_not_semantically_invocable"
                    )

        viewport = snapshot.get("viewport")
        semantic_viewport_focus_supported = bool(
            isinstance(viewport, dict)
            and viewport.get("keyboard_focusable") is True
            and viewport.get("enabled") is True
            and viewport.get("visible") is True
        )
        if not semantic_viewport_focus_supported:
            block_reasons.append("local_uia_unique_viewport_focus_target_unavailable")
        block_reasons = list(dict.fromkeys(block_reasons))
        evidence = {
            "source": "local_uia",
            "expected_revision": expected_revision,
            "expected_window_handle": window_handle,
            "expected_window_title": expected_window_title,
            "accessibility_tree_refreshed": True,
            "viewer_document_observed": viewport is not None,
            "empty_viewport_focus_target_observed": False,
            "semantic_viewport_focus_supported": semantic_viewport_focus_supported,
            "unnamed_toolbar_children_observed": bool(anonymous_toolbars),
            "controls": controls,
            "anonymous_toolbars": anonymous_toolbars,
            "screenshot_path": None,
            "note": (
                "Server-generated local UIA probe. No control was invoked and no "
                "keyboard input was sent."
            ),
        }
        required_command_ids = set(command_labels)
        safe = bool(
            not block_reasons
            and semantic_viewport_focus_supported
            and required_command_ids <= resolved_command_ids
        )
        return {
            "supported": True,
            "safe_for_standard_view_replay": safe,
            "unavailable_reason": None,
            "observed_at": _utc_now(),
            "window": snapshot["window"],
            "descendant_count": snapshot["descendant_count"],
            "toolbars": list(snapshot["toolbars"].values()),
            "viewport": viewport,
            "resolved_command_ids": sorted(resolved_command_ids),
            "evidence": evidence,
            "block_reasons": block_reasons,
        }

    def execute_standard_recipe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        """Execute exactly one deterministic standard-view recipe."""

        started_at = _utc_now()
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "kind": "materials_studio_local_uia_view_replay_execution",
            "started_at": started_at,
            "window_handle": window_handle,
            "expected_window_title": expected_window_title,
            "view_name": execution_recipe.get("view_name"),
            "execution_succeeded": False,
            "failure_phase": "preflight",
            "reset_invocation_succeeded": False,
            "keyboard_focus_verified": False,
            "key_sequence_sent": [],
            "modifier_keys": [],
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "visual_acceptance_recorded": False,
        }
        try:
            if not self.supported:
                raise UiaReplayError(
                    self.unavailable_reason or "Local UIA backend is unavailable."
                )
            view_name, key_sequence = self._validate_recipe(execution_recipe)
            receipt["view_name"] = view_name
            receipt["expected_key_sequence"] = list(key_sequence)
            self._require_foreground(window_handle)
            snapshot = self._inspect_window(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
            )
            if snapshot["block_reasons"]:
                raise UiaReplayError(
                    "Fresh UIA tree failed its toolbar/viewport contract: "
                    + ", ".join(snapshot["block_reasons"])
                )
            reset_wrapper, reset_receipt = self._resolve_reset_target(
                snapshot=snapshot,
                execution_recipe=execution_recipe,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
            )
            receipt["reset_command"] = reset_receipt
            receipt["failure_phase"] = "reset_invoke"
            reset_wrapper.invoke()
            self._sleep(0.2)
            self._require_foreground(window_handle)
            self._require_window_title(
                snapshot["top"], expected_window_title=expected_window_title
            )
            receipt["reset_invocation_succeeded"] = True

            if key_sequence:
                receipt["failure_phase"] = "viewport_focus"
                focused_snapshot = self._inspect_window(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                viewport_wrapper = focused_snapshot["viewport_wrapper"]
                viewport_wrapper.set_focus()
                self._sleep(0.1)
                if _safe_call(viewport_wrapper, "has_keyboard_focus", False) is not True:
                    raise UiaReplayError(
                        "The unique CViewer3DCtrl did not acquire keyboard focus."
                    )
                receipt["keyboard_focus_verified"] = True
                receipt["failure_phase"] = "keyboard_input"
                for key in key_sequence:
                    self._require_foreground(window_handle)
                    self._require_window_title(
                        focused_snapshot["top"],
                        expected_window_title=expected_window_title,
                    )
                    if (
                        _safe_call(
                            viewport_wrapper,
                            "has_keyboard_focus",
                            False,
                        )
                        is not True
                    ):
                        raise UiaReplayError(
                            "The CViewer3DCtrl lost keyboard focus before input."
                        )
                    if self._keyboard_sender is None:
                        raise UiaReplayError("Keyboard sender is unavailable.")
                    self._keyboard_sender("{" + key.upper() + "}")
                    receipt["key_sequence_sent"].append(key)
                    self._sleep(0.15)

            receipt["failure_phase"] = "post_action_binding"
            self._require_foreground(window_handle)
            final_snapshot = self._inspect_window(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
            )
            if final_snapshot["block_reasons"]:
                raise UiaReplayError(
                    "The post-action UIA tree no longer matches the safe contract: "
                    + ", ".join(final_snapshot["block_reasons"])
                )
            receipt.update(
                {
                    "execution_succeeded": True,
                    "failure_phase": None,
                    "finished_at": _utc_now(),
                    "post_action_window_title": final_snapshot["window"]["title"],
                    "post_action_viewport_observed": final_snapshot.get("viewport")
                    is not None,
                    "post_action_observation_required": True,
                    "record_call_ready": False,
                }
            )
            return receipt
        except Exception as exc:
            receipt.update(
                {
                    "execution_succeeded": False,
                    "finished_at": _utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "post_action_observation_required": True,
                    "record_call_ready": False,
                    "retry_restarts_from_reset_baseline": True,
                }
            )
            return receipt

    def _inspect_window(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        if self._desktop_factory is None:
            raise UiaReplayError("UIA Desktop factory is unavailable.")
        if window_handle <= 0:
            raise UiaReplayError("Materials Studio window handle is invalid.")
        desktop = self._desktop_factory(backend="uia")
        top = desktop.window(handle=window_handle)
        self._require_window_title(top, expected_window_title=expected_window_title)
        descendants = list(top.descendants())
        index_by_runtime_id = {
            _runtime_identity(item): index for index, item in enumerate(descendants)
        }
        block_reasons: list[str] = []
        toolbar_results: dict[str, dict[str, Any]] = {}
        toolbar_wrappers: dict[str, Any] = {}

        for toolbar_name, contract in toolbar_contracts.items():
            expected_automation_id = str(
                12122 if toolbar_name == "3D Viewer" else 12134
            )
            matches = [
                item
                for item in descendants
                if str(_element_value(item, "automation_id", "") or "")
                == expected_automation_id
                and str(_element_value(item, "name", "") or "") == toolbar_name
                and str(_element_value(item, "control_type", "") or "")
                == "ToolBar"
            ]
            if len(matches) != 1:
                block_reasons.append(
                    f"local_uia_{toolbar_name.replace(' ', '_').lower()}_identity_not_unique"
                )
                continue
            toolbar = matches[0]
            toolbar_wrappers[toolbar_name] = toolbar
            children = list(toolbar.children())
            expected_entries = list(contract.get("entries") or [])
            child_rows: list[dict[str, Any]] = []
            for ordinal, child in enumerate(children):
                global_zero_based_index = index_by_runtime_id.get(
                    _runtime_identity(child)
                )
                if global_zero_based_index is None:
                    block_reasons.append(
                        f"local_uia_{toolbar_name.replace(' ', '_').lower()}_child_index_unavailable"
                    )
                child_rows.append(
                    {
                        "zero_based_child_index": ordinal,
                        "global_zero_based_element_index": global_zero_based_index,
                        "computer_use_compatible_element_index": (
                            None
                            if global_zero_based_index is None
                            else global_zero_based_index + 1
                        ),
                        "role": _normalized_role(child),
                        "enabled": _safe_call(child, "is_enabled", False) is True,
                        "visible": _safe_call(child, "is_visible", False) is True,
                        "observed_control_name": (
                            str(_element_value(child, "name", "") or "").strip()
                            or None
                        ),
                        "invoke_supported": _invoke_pattern_available(child),
                    }
                )
            expected_roles = [
                "separator" if kind == "separator" else "checkbox"
                for kind, _command_id in expected_entries
            ]
            observed_roles = [row["role"] for row in child_rows]
            contract_verified = bool(
                len(children) == len(expected_entries)
                and observed_roles == expected_roles
                and all(
                    row["computer_use_compatible_element_index"] is not None
                    for row in child_rows
                )
            )
            if len(children) != len(expected_entries):
                block_reasons.append(
                    f"local_uia_{toolbar_name.replace(' ', '_').lower()}_child_count_mismatch"
                )
            if observed_roles != expected_roles:
                block_reasons.append(
                    f"local_uia_{toolbar_name.replace(' ', '_').lower()}_role_sequence_mismatch"
                )
            toolbar_results[toolbar_name] = {
                "toolbar_name": toolbar_name,
                "toolbar_automation_id": int(expected_automation_id),
                "registry_toolbar_name": contract.get("registry_toolbar_name"),
                "contract_verified": contract_verified,
                "expected_child_count": len(expected_entries),
                "observed_child_count": len(children),
                "children": child_rows,
            }

        viewport_matches = [
            item
            for item in descendants
            if str(_element_value(item, "class_name", "") or "")
            == VIEWPORT_CLASS_NAME
            and str(_element_value(item, "control_type", "") or "")
            == VIEWPORT_CONTROL_TYPE
            and _safe_call(item, "is_enabled", False) is True
            and _safe_call(item, "is_visible", False) is True
            and _safe_call(item, "is_keyboard_focusable", False) is True
        ]
        viewport_wrapper = viewport_matches[0] if len(viewport_matches) == 1 else None
        if len(viewport_matches) != 1:
            block_reasons.append("local_uia_viewport_identity_not_unique")
        viewport = None
        if viewport_wrapper is not None:
            viewport_index = index_by_runtime_id.get(_runtime_identity(viewport_wrapper))
            viewport = {
                "class_name": VIEWPORT_CLASS_NAME,
                "control_type": VIEWPORT_CONTROL_TYPE,
                "automation_id": str(
                    _element_value(viewport_wrapper, "automation_id", "") or ""
                ),
                "global_zero_based_element_index": viewport_index,
                "computer_use_compatible_element_index": (
                    None if viewport_index is None else viewport_index + 1
                ),
                "enabled": True,
                "visible": True,
                "keyboard_focusable": True,
                "has_keyboard_focus": _safe_call(
                    viewport_wrapper, "has_keyboard_focus", False
                )
                is True,
            }

        return {
            "top": top,
            "window": {
                "handle": window_handle,
                "title": str(_element_value(top, "name", "") or ""),
                "control_type": str(
                    _element_value(top, "control_type", "") or ""
                ),
                "class_name": str(_element_value(top, "class_name", "") or ""),
            },
            "descendant_count": len(descendants),
            "descendants": descendants,
            "toolbars": toolbar_results,
            "toolbar_wrappers": toolbar_wrappers,
            "viewport": viewport,
            "viewport_wrapper": viewport_wrapper,
            "block_reasons": list(dict.fromkeys(block_reasons)),
            "command_labels": dict(command_labels),
        }

    def _validate_recipe(
        self, execution_recipe: dict[str, Any]
    ) -> tuple[str, list[str]]:
        if not isinstance(execution_recipe, dict):
            raise UiaReplayError("Execution recipe must be a JSON object.")
        view_name = str(execution_recipe.get("view_name") or "")
        if view_name not in SAFE_STANDARD_VIEW_KEY_SEQUENCES:
            raise UiaReplayError(
                "Local UIA execution supports only front/back/right/left/top/bottom."
            )
        if execution_recipe.get("automation_ready") is not True:
            raise UiaReplayError("Prepared view recipe is not automation-ready.")
        if execution_recipe.get("structure_mutation_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit structure mutation.")
        if execution_recipe.get("launch_new_matstudio_process_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit process launch.")
        if execution_recipe.get("blind_coordinate_action_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit blind coordinates.")
        if execution_recipe.get("keyboard_stages") is not None:
            raise UiaReplayError("Staged/isometric recipes are not locally executable.")
        expected_keys = SAFE_STANDARD_VIEW_KEY_SEQUENCES[view_name]
        observed_keys = list(execution_recipe.get("key_sequence") or [])
        if observed_keys != expected_keys:
            raise UiaReplayError(
                f"Prepared {view_name} key sequence does not match the allowlist."
            )
        if any(key not in SAFE_ARROW_KEYS for key in observed_keys):
            raise UiaReplayError("Prepared key sequence contains a non-arrow key.")
        if list(execution_recipe.get("modifier_keys") or []) != []:
            raise UiaReplayError("Modifier keys are forbidden for local view replay.")
        if observed_keys and execution_recipe.get("rotation_increment_degrees") != 45:
            raise UiaReplayError("Standard keyboard replay requires a 45-degree increment.")
        if execution_recipe.get("native_command_id") != "cmdViewer3DResetView":
            raise UiaReplayError("Standard local replay must begin with Reset View.")
        return view_name, observed_keys

    def _resolve_reset_target(
        self,
        *,
        snapshot: dict[str, Any],
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> tuple[Any, dict[str, Any]]:
        toolbar_name = "3D Viewer"
        command_id = "cmdViewer3DResetView"
        toolbar = snapshot["toolbar_wrappers"].get(toolbar_name)
        toolbar_result = snapshot["toolbars"].get(toolbar_name)
        if toolbar is None or not isinstance(toolbar_result, dict):
            raise UiaReplayError("The exact 3D Viewer toolbar is unavailable.")
        if toolbar_result.get("contract_verified") is not True:
            raise UiaReplayError("The 3D Viewer toolbar contract is not verified.")
        entries = list(toolbar_contracts[toolbar_name].get("entries") or [])
        matching_indexes = [
            index
            for index, (kind, item_command_id) in enumerate(entries)
            if kind == "tool" and item_command_id == command_id
        ]
        if matching_indexes != [5]:
            raise UiaReplayError("Reset View is not at the reviewed toolbar position.")
        child_index = matching_indexes[0]
        children = list(toolbar.children())
        reset_wrapper = children[child_index]
        child = toolbar_result["children"][child_index]
        target = execution_recipe.get("accessibility_target")
        if not isinstance(target, dict):
            raise UiaReplayError("Prepared Reset View accessibility target is missing.")
        if target.get("command_id") != command_id:
            raise UiaReplayError("Prepared accessibility target is not Reset View.")
        if target.get("toolbar_name") != toolbar_name:
            raise UiaReplayError("Prepared Reset View toolbar identity differs.")
        target_kind = str(target.get("target_kind") or "")
        expected_name = command_labels[command_id]
        if target_kind == "named_control":
            if target.get("control_name") != expected_name:
                raise UiaReplayError("Prepared named Reset View label differs.")
            if child.get("observed_control_name") != expected_name:
                raise UiaReplayError("Fresh named Reset View label differs.")
        elif target_kind == "verified_anonymous_toolbar_child":
            exact_fields = {
                "toolbar_automation_id": toolbar_result["toolbar_automation_id"],
                "registry_toolbar_name": toolbar_result["registry_toolbar_name"],
                "zero_based_child_index": child_index,
                "element_index": child[
                    "computer_use_compatible_element_index"
                ],
            }
            for field, expected in exact_fields.items():
                if target.get(field) != expected:
                    raise UiaReplayError(
                        f"Fresh Reset View target differs from prepared {field}."
                    )
            observed_name = child.get("observed_control_name")
            if observed_name not in {None, expected_name}:
                raise UiaReplayError("Fresh Reset View control name is unexpected.")
        else:
            raise UiaReplayError("Prepared Reset View target kind is not allowlisted.")
        if child.get("enabled") is not True:
            raise UiaReplayError("Fresh Reset View control is disabled.")
        if child.get("invoke_supported") is not True:
            raise UiaReplayError("Fresh Reset View control lacks InvokePattern.")
        return reset_wrapper, {
            "command_id": command_id,
            "target_kind": target_kind,
            "invocation_method": "local_uia_invoke_pattern",
            "toolbar_name": toolbar_name,
            "toolbar_automation_id": toolbar_result["toolbar_automation_id"],
            "registry_toolbar_name": toolbar_result["registry_toolbar_name"],
            "zero_based_child_index": child_index,
            "element_index": child["computer_use_compatible_element_index"],
            "observed_control_name": child.get("observed_control_name"),
            "invoke_pattern_verified": True,
            "accessibility_tree_refreshed": True,
        }

    def _require_window_title(
        self, top: Any, *, expected_window_title: str
    ) -> None:
        observed_title = str(_element_value(top, "name", "") or "")
        if observed_title != expected_window_title:
            raise UiaReplayError(
                "Materials Studio window title changed during UIA replay: "
                f"expected {expected_window_title!r}, observed {observed_title!r}."
            )

    def _require_foreground(self, expected_handle: int) -> None:
        observed_handle = self._foreground_handle_getter()
        if observed_handle != expected_handle:
            raise UiaReplayError(
                "The exact Materials Studio wrapper is not the foreground window."
            )
