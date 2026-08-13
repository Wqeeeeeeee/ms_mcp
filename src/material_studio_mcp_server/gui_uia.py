"""Narrow UI Automation support for deterministic Materials Studio view replay.

This module intentionally exposes only bounded semantic UIA invocation,
unmodified arrow-key recipes, and one Miller-plane pointer target derived from
fresh screenshot differencing. It never uses blind coordinates and never
records visual acceptance on its own.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.abc
import importlib.machinery
import os
import struct
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


COMTYPES_CACHE_ENV = "MATERIAL_STUDIO_MCP_COMTYPES_CACHE"
_COMTYPES_IMPORT_LOCK = threading.RLock()


class _ExternalComtypesGenLoader(importlib.abc.Loader):
    """Create the generated-code package without importing ``comtypes`` early."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def exec_module(self, module: Any) -> None:
        module.__package__ = "comtypes.gen"
        module.__path__ = [str(self.cache_dir)]


class _ExternalComtypesGenFinder(importlib.abc.MetaPathFinder):
    """Intercept only the first ``comtypes.gen`` package import."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.loader = _ExternalComtypesGenLoader(cache_dir)

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        if fullname != "comtypes.gen":
            return None
        spec = importlib.machinery.ModuleSpec(fullname, self.loader, is_package=True)
        spec.submodule_search_locations = [str(self.cache_dir)]
        return spec


def _validate_external_comtypes_gen_cache(cache_dir: Path) -> None:
    client_module = sys.modules.get("comtypes.client")
    if client_module is not None:
        raw_gen_dir = getattr(client_module, "gen_dir", None)
        if raw_gen_dir is None or not str(raw_gen_dir).strip():
            raise RuntimeError("comtypes.client has no filesystem generated-code cache")
        if Path(str(raw_gen_dir)).resolve() != cache_dir:
            raise RuntimeError(
                "comtypes.client was imported before the external generated-code "
                "cache was bound"
            )

    gen_module = sys.modules.get("comtypes.gen")
    if gen_module is not None:
        raw_paths = list(getattr(gen_module, "__path__", []) or [])
        observed_paths = [Path(str(item)).resolve() for item in raw_paths]
        if observed_paths != [cache_dir]:
            raise RuntimeError(
                "comtypes.gen is not bound exclusively to the external generated-code "
                "cache"
            )


@contextmanager
def _external_comtypes_gen_cache() -> Any:
    """Bind ``comtypes.gen`` while preserving pywinauto's COM initialization.

    ``comtypes`` generates Python wrappers the first time pywinauto's UIA
    backend is imported.  A version-addressed runtime must remain byte-for-byte
    immutable, so the Windows launcher supplies a fresh external directory for
    those generated modules.  The temporary import finder is installed before
    pywinauto imports ``comtypes``, allowing pywinauto to select its normal MTA
    threading model first. Source checkouts without the setting retain normal
    comtypes behavior.
    """

    configured = os.environ.get(COMTYPES_CACHE_ENV, "").strip()
    if not configured:
        yield None
        return
    configured_path = Path(configured).expanduser()
    if not configured_path.is_absolute():
        raise RuntimeError(f"{COMTYPES_CACHE_ENV} must be an absolute path")
    cache_dir = configured_path.resolve()
    runtime_prefix = Path(sys.prefix).resolve()
    if cache_dir == runtime_prefix or runtime_prefix in cache_dir.parents:
        raise RuntimeError(
            f"{COMTYPES_CACHE_ENV} must be outside the active Python runtime"
        )
    if not cache_dir.is_dir():
        raise RuntimeError(
            f"Launcher-owned comtypes cache is not an existing directory: {cache_dir}"
        )

    with _COMTYPES_IMPORT_LOCK:
        _validate_external_comtypes_gen_cache(cache_dir)
        finder: _ExternalComtypesGenFinder | None = None
        if "comtypes.gen" not in sys.modules:
            finder = _ExternalComtypesGenFinder(cache_dir)
            sys.meta_path.insert(0, finder)
        try:
            yield cache_dir
        finally:
            if finder is not None and finder in sys.meta_path:
                sys.meta_path.remove(finder)
        _validate_external_comtypes_gen_cache(cache_dir)


SAFE_STANDARD_VIEW_KEY_SEQUENCES: dict[str, list[str]] = {
    "front": [],
    "back": ["Left", "Left", "Left", "Left"],
    "right": ["Up", "Up", "Left", "Left"],
    "left": ["Up", "Up", "Right", "Right"],
    "top": ["Up", "Up"],
    "bottom": ["Left", "Left", "Left", "Left", "Down", "Down"],
}
SAFE_ISOMETRIC_KEYBOARD_STAGES: list[dict[str, Any]] = [
    {
        "rotation_increment_degrees": 45.0,
        "rotation_increment_ui_display_degrees": 45.0,
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
SAFE_LOCAL_VIEW_NAMES = frozenset(
    {*SAFE_STANDARD_VIEW_KEY_SEQUENCES, "isometric"}
)
MILLER_VIEW_ONTO_RECIPE_KINDS = frozenset(
    {
        "miller_plane_view_onto",
        "crystal_direction_via_collinear_miller_plane_view_onto",
    }
)
SAFE_ARROW_KEYS = frozenset({"Up", "Down", "Left", "Right"})
VIEWPORT_CLASS_NAME = "CViewer3DCtrl"
VIEWPORT_CONTROL_TYPE = "Pane"
FIT_TO_VIEW_COMMAND_ID = "cmdViewer3DFitToView"
FIT_TO_VIEW_CONTROL_NAME = "3D Viewer Fit to View"
FIT_TO_VIEW_TOOLBAR_NAME = "3D Viewer"
FIT_TO_VIEW_TOOLBAR_CHILD_INDEX = 7
MOVEMENT_WINDOW_TITLE = "Movement"
MOVEMENT_OPTIONS_PANE_ID = "MovementOptions"
MOVEMENT_ANGLE_CONTROL_ID = "numNudgeAngle"
MOVEMENT_SCREEN_FACTOR_CONTROL_ID = "numNudgeFactor"
MOVEMENT_NUMERIC_EDIT_ID = "TextCtrl"
MOVEMENT_DEFAULT_ANGLE_DEGREES = 45.0
MOVEMENT_EXPECTED_SCREEN_FACTOR = 2.0
MOVEMENT_NUMERIC_TOLERANCE = 0.0005
PROHIBITED_NUDGE_BUTTON_IDS = frozenset(
    {
        "cmdNudgeAroundX",
        "cmdNudgeBackwardsAroundX",
        "cmdNudgeAroundY",
        "cmdNudgeBackwardsAroundY",
        "cmdNudgeAroundZ",
        "cmdNudgeBackwardsAroundZ",
        "cmdNudgeLeft",
        "cmdNudgeRight",
        "cmdNudgeUp",
        "cmdNudgeDown",
        "cmdNudgeIn",
        "cmdNudgeOut",
    }
)
MILLER_PLANES_WINDOW_TITLE = "Miller Planes"
MILLER_PLANES_CONTROL_ID = "MillerPlanesCtl"
MILLER_INDICES_CONTROL_ID = "TxtHKL"
MILLER_CREATE_CONTROL_ID = "CmdCreate"
MILLER_SHOW_SYMMETRY_CONTROL_ID = "ChkShowSymmetry"
MILLER_SHOW_PERIODIC_CONTROL_ID = "ChkShowPeriodic"
PROPERTIES_EXPLORER_CONTROL_ID = "GenPropEdit"
PROPERTIES_OBJECT_TYPE_CONTROL_ID = "cbObjectType"
PROPERTIES_GRID_CONTROL_ID = "vGridControl"
VIEWER_SELECTION_COMMAND_ID = 33288
VIEWER_RECENTER_COMMAND_ID = 33296
VIEWER_VIEW_ONTO_COMMAND_ID = 33297
VIEWER_FIT_COMMAND_ID = 33299
PROPERTIES_EXPLORER_COMMAND_ID = 33439
UNDO_COMMAND_ID = 33052
MILLER_PLANE_SELECTION_METHOD = (
    "viewport_unique_transient_plane_properties_verified"
)
MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS = (
    "fresh_before_after_screenshot_unique_transient_plane_region"
)
MILLER_VIEW_ONTO_UNDO_LABEL = "Undo View Onto Miller Plane"
MILLER_CREATE_UNDO_LABEL = "Undo Create Miller Plane"


def local_uia_view_replay_implementation_contract() -> dict[str, Any]:
    """Describe the bounded recipe classes implemented by the local UIA backend."""

    return {
        "schema_version": 1,
        "backend": "pywinauto_uia",
        "platform": "windows",
        "execute_tool": "material_studio_gui_execute_view_replay",
        "default_execution_mode": "preview",
        "explicit_execute_required": True,
        "records_visual_acceptance": False,
        "runtime_support_fields": {
            "backend": "gui_status.local_uia_view_replay_supported",
            "transactional_miller": (
                "gui_status.local_uia_miller_plane_transaction_supported"
            ),
            "single_window": "gui_status.single_window_policy_ok",
        },
        "recipe_classes": {
            "cartesian_standard": {
                "implemented": True,
                "view_names": sorted(SAFE_STANDARD_VIEW_KEY_SEQUENCES),
                "requires_current_bound_runtime_accessibility_preflight": True,
            },
            "isometric": {
                "implemented": True,
                "view_names": ["isometric"],
                "requires_current_bound_runtime_accessibility_preflight": True,
                "staged_keyboard_recipe": True,
            },
            "transactional_miller_plane": {
                "implemented": True,
                "recipe_kind": "miller_plane_view_onto",
                "view_name_pattern": "crystal_plane_*",
                "requires_automation_ready_recipe": True,
                "requires_current_bound_runtime_ui_preflight": True,
                "runtime_gate": "safe_for_miller_plane_transaction",
                "requires_exact_viewport_restoration": True,
                "requires_structure_sha256_unchanged": True,
                "requires_post_action_visual_confirmation": True,
            },
            "exact_collinear_crystal_direction": {
                "implemented": True,
                "recipe_kind": (
                    "crystal_direction_via_collinear_miller_plane_view_onto"
                ),
                "view_name_pattern": "crystal_*",
                "eligibility_status": "exact_integer_plane_collinear",
                "requires_automation_ready_recipe": True,
                "requires_current_bound_runtime_ui_preflight": True,
                "runtime_gate": "safe_for_miller_plane_transaction",
                "requires_direct_lattice_direction_match_evidence": True,
                "requires_post_action_visual_confirmation": True,
            },
            "non_collinear_crystal_direction": {
                "implemented": False,
                "reviewed_camera_backend_required": True,
            },
        },
        "miller_recipe_kinds": sorted(MILLER_VIEW_ONTO_RECIPE_KINDS),
        "blind_coordinates_allowed": False,
        "modifier_keys_allowed": False,
        "launch_new_matstudio_process_allowed": False,
        "structure_mutation_allowed": False,
    }


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

    def execute_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        registry_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Invoke the server-verified Fit-to-View control once."""
        ...

    def execute_standard_recipe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        structure_path: str | Path | None = None,
        evidence_dir: str | Path | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Execute one allowlisted standard, isometric, or Miller recipe."""
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


def _numeric_text(value: Any, *, field_name: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise UiaReplayError(
            f"Movement {field_name} did not expose a numeric value."
        ) from exc


def _numbers_match(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= MOVEMENT_NUMERIC_TOLERANCE


def _bmp_payload(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 54 or data[:2] != b"BM":
        raise UiaReplayError(f"Screenshot is not a readable BMP: {path}")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise UiaReplayError("Miller replay requires a BITMAPINFOHEADER BMP.")
    width, height_raw, planes, bits_per_pixel, compression = struct.unpack_from(
        "<iiHHI", data, 18
    )
    if (
        width <= 0
        or height_raw == 0
        or planes != 1
        or bits_per_pixel not in {24, 32}
        or compression != 0
    ):
        raise UiaReplayError(
            "Miller replay requires an uncompressed 24-bit or 32-bit BMP."
        )
    height = abs(height_raw)
    stride = ((width * bits_per_pixel + 31) // 32) * 4
    required_size = pixel_offset + stride * height
    if required_size > len(data):
        raise UiaReplayError("Miller replay BMP pixel data is truncated.")
    return {
        "data": data,
        "pixel_offset": pixel_offset,
        "width": width,
        "height": height,
        "height_raw": height_raw,
        "bytes_per_pixel": bits_per_pixel // 8,
        "stride": stride,
    }


def _bmp_rgb(payload: dict[str, Any], x: int, y: int) -> tuple[int, int, int]:
    source_y = (
        int(payload["height"]) - 1 - y
        if int(payload["height_raw"]) > 0
        else y
    )
    offset = (
        int(payload["pixel_offset"])
        + source_y * int(payload["stride"])
        + x * int(payload["bytes_per_pixel"])
    )
    data = payload["data"]
    blue, green, red = data[offset], data[offset + 1], data[offset + 2]
    return red, green, blue


def analyze_miller_plane_bmp_diff(
    before_path: str | Path,
    after_path: str | Path,
    *,
    viewport_bounds: tuple[int, int, int, int],
    channel_threshold: int = 12,
    closing_radius: int = 2,
) -> dict[str, Any]:
    """Find one fresh transient-plane region in two same-layout BMP captures."""

    before = _bmp_payload(Path(before_path))
    after = _bmp_payload(Path(after_path))
    if (before["width"], before["height"]) != (
        after["width"],
        after["height"],
    ):
        raise UiaReplayError("Miller replay screenshots have different dimensions.")
    width = int(before["width"])
    height = int(before["height"])
    left, top, right, bottom = (int(value) for value in viewport_bounds)
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise UiaReplayError("Miller replay viewport bounds are outside the BMP.")

    changed: set[int] = set()
    for y in range(top, bottom):
        row_offset = y * width
        for x in range(left, right):
            before_rgb = _bmp_rgb(before, x, y)
            after_rgb = _bmp_rgb(after, x, y)
            if max(
                abs(before_rgb[index] - after_rgb[index]) for index in range(3)
            ) >= channel_threshold:
                changed.add(row_offset + x)
    if not changed:
        raise UiaReplayError(
            "Creating the Miller plane produced no fresh viewport pixel region."
        )

    expanded: set[int] = set()
    for encoded in changed:
        y, x = divmod(encoded, width)
        for dy in range(-closing_radius, closing_radius + 1):
            target_y = y + dy
            if target_y < top or target_y >= bottom:
                continue
            row_offset = target_y * width
            for dx in range(-closing_radius, closing_radius + 1):
                target_x = x + dx
                if left <= target_x < right:
                    expanded.add(row_offset + target_x)

    components: list[dict[str, Any]] = []
    remaining = set(expanded)
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component: set[int] = {seed}
        while stack:
            encoded = stack.pop()
            y, x = divmod(encoded, width)
            for neighbor in (
                encoded - width,
                encoded + width,
                encoded - 1,
                encoded + 1,
            ):
                if neighbor not in remaining:
                    continue
                neighbor_y, neighbor_x = divmod(neighbor, width)
                if (
                    top <= neighbor_y < bottom
                    and left <= neighbor_x < right
                    and abs(neighbor_x - x) + abs(neighbor_y - y) == 1
                ):
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        original = component & changed
        if not original:
            continue
        xs = [value % width for value in original]
        ys = [value // width for value in original]
        components.append(
            {
                "expanded": component,
                "changed": original,
                "changed_pixel_count": len(original),
                "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
            }
        )

    significant_floor = max(64, int(len(changed) * 0.02))
    significant = [
        item
        for item in components
        if int(item["changed_pixel_count"]) >= significant_floor
    ]
    if len(significant) != 1:
        raise UiaReplayError(
            "Fresh screenshot differencing did not isolate exactly one significant "
            f"Miller-plane region; observed {len(significant)}."
        )
    region = significant[0]
    bbox_left, bbox_top, bbox_right, bbox_bottom = region["bbox"]
    changed_region = region["changed"]
    sampling_radius = 6

    def candidate_score(encoded: int) -> tuple[int, int, int]:
        y, x = divmod(encoded, width)
        local_count = 0
        for dy in range(-sampling_radius, sampling_radius + 1):
            row_offset = (y + dy) * width
            for dx in range(-sampling_radius, sampling_radius + 1):
                if row_offset + x + dx in changed_region:
                    local_count += 1
        edge_distance = min(
            x - bbox_left,
            bbox_right - 1 - x,
            y - bbox_top,
            bbox_bottom - 1 - y,
        )
        return local_count, edge_distance, -encoded

    candidate_encoded = max(changed_region, key=candidate_score)
    candidate_y, candidate_x = divmod(candidate_encoded, width)
    local_count, edge_distance, _ = candidate_score(candidate_encoded)
    candidate_after_rgb = list(_bmp_rgb(after, candidate_x, candidate_y))
    return {
        "algorithm": "threshold_dilate_components_dense_interior_v1",
        "channel_threshold": channel_threshold,
        "closing_radius": closing_radius,
        "viewport_bounds": [left, top, right, bottom],
        "changed_pixel_count": len(changed),
        "component_count": len(components),
        "significant_component_count": len(significant),
        "significant_component_floor": significant_floor,
        "region_bbox": list(region["bbox"]),
        "region_changed_pixel_count": region["changed_pixel_count"],
        "candidate_window_pixel": [candidate_x, candidate_y],
        "candidate_viewport_pixel": [candidate_x - left, candidate_y - top],
        "candidate_local_changed_pixel_count": local_count,
        "candidate_bbox_edge_distance": edge_distance,
        "candidate_after_rgb": candidate_after_rgb,
    }


def compare_bmp_region(
    first_path: str | Path,
    second_path: str | Path,
    *,
    bounds: tuple[int, int, int, int],
) -> dict[str, Any]:
    """Return exact RGB restoration metrics for one same-layout BMP region."""

    first = _bmp_payload(Path(first_path))
    second = _bmp_payload(Path(second_path))
    if (first["width"], first["height"]) != (
        second["width"],
        second["height"],
    ):
        raise UiaReplayError("Restoration screenshots have different dimensions.")
    left, top, right, bottom = (int(value) for value in bounds)
    width = int(first["width"])
    height = int(first["height"])
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise UiaReplayError("Restoration bounds are outside the BMP.")
    changed = 0
    peak_delta = 0
    total = (right - left) * (bottom - top)
    for y in range(top, bottom):
        for x in range(left, right):
            first_rgb = _bmp_rgb(first, x, y)
            second_rgb = _bmp_rgb(second, x, y)
            delta = max(
                abs(first_rgb[index] - second_rgb[index]) for index in range(3)
            )
            peak_delta = max(peak_delta, delta)
            changed += int(delta != 0)
    return {
        "bounds": [left, top, right, bottom],
        "pixel_count": total,
        "changed_pixel_count": changed,
        "peak_channel_delta": peak_delta,
        "exact_match": changed == 0,
    }


def _default_window_rect(window_handle: int) -> tuple[int, int, int, int]:
    if os.name != "nt":
        raise UiaReplayError("Window rectangles are available only on Windows.")
    import ctypes.wintypes as wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(
        ctypes.c_void_p(window_handle), ctypes.byref(rect)
    ):
        raise UiaReplayError("GetWindowRect failed for the Materials Studio window.")
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _default_native_command_sender(window_handle: int, command_id: int) -> None:
    if os.name != "nt":
        raise UiaReplayError("Native commands are available only on Windows.")
    result = ctypes.windll.user32.SendMessageW(
        ctypes.c_void_p(window_handle), 0x0111, int(command_id), 0
    )
    del result


def _default_pointer_clicker(x: int, y: int) -> None:
    with _external_comtypes_gen_cache():
        from pywinauto import mouse

    mouse.click(button="left", coords=(int(x), int(y)))


def _normalize_menu_label(value: str) -> str:
    placeholder = "\0"
    return (
        str(value)
        .replace("&&", placeholder)
        .replace("&", "")
        .replace(placeholder, "&")
        .split("\t", 1)[0]
        .strip()
    )


def _default_native_menu_entries(window_handle: int) -> list[dict[str, Any]]:
    """Read the live native menu tree after initializing each popup."""

    if os.name != "nt":
        raise UiaReplayError("Native menus are available only on Windows.")
    user32 = ctypes.windll.user32
    root_menu = int(user32.GetMenu(ctypes.c_void_p(window_handle)) or 0)
    if not root_menu:
        raise UiaReplayError("Materials Studio native menu was not found.")
    entries: list[dict[str, Any]] = []

    def read_menu(menu_handle: int, path: list[str], depth: int) -> None:
        if depth > 5:
            return
        count = int(user32.GetMenuItemCount(ctypes.c_void_p(menu_handle)))
        for index in range(max(0, count)):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetMenuStringW(
                ctypes.c_void_p(menu_handle),
                index,
                buffer,
                len(buffer),
                0x00000400,
            )
            label = _normalize_menu_label(buffer.value)
            submenu = int(
                user32.GetSubMenu(ctypes.c_void_p(menu_handle), index) or 0
            )
            if submenu:
                user32.SendMessageW(
                    ctypes.c_void_p(window_handle),
                    0x0117,
                    ctypes.c_void_p(submenu),
                    index,
                )
                read_menu(submenu, [*path, label], depth + 1)
                continue
            command_id = int(
                user32.GetMenuItemID(ctypes.c_void_p(menu_handle), index)
            )
            if command_id < 0:
                continue
            state = int(
                user32.GetMenuState(
                    ctypes.c_void_p(menu_handle), index, 0x00000400
                )
            )
            entries.append(
                {
                    "path": [*path, label],
                    "label": label,
                    "command_id": command_id,
                    "enabled": not bool(state & 0x00000003),
                    "checked": bool(state & 0x00000008),
                }
            )

    read_menu(root_menu, [], 0)
    return entries


def _default_toolbar_button_reader(toolbar_handle: int) -> list[dict[str, Any]]:
    with _external_comtypes_gen_cache():
        from pywinauto.controls.common_controls import ToolbarWrapper

    toolbar = ToolbarWrapper(int(toolbar_handle))
    rows: list[dict[str, Any]] = []
    for index in range(int(toolbar.button_count())):
        button_struct = toolbar.get_button_struct(index)
        button_info = toolbar.get_button(index)
        rows.append(
            {
                "index": index,
                "command_id": int(button_struct.idCommand),
                "style": int(button_struct.fsStyle),
                "state": int(button_struct.fsState),
                "text": str(getattr(button_info, "text", "") or ""),
            }
        )
    return rows


class PywinautoViewReplayBackend:
    """A narrowly scoped pywinauto UIA backend for Materials Studio 20.1."""

    def __init__(
        self,
        *,
        desktop_factory: Callable[..., Any] | None = None,
        keyboard_sender: Callable[[str], None] | None = None,
        foreground_handle_getter: Callable[[], int | None] | None = None,
        window_capture_fn: Callable[[int, Path], None] | None = None,
        window_rect_getter: Callable[[int], tuple[int, int, int, int]] | None = None,
        pointer_clicker: Callable[[int, int], None] | None = None,
        native_command_sender: Callable[[int, int], None] | None = None,
        native_menu_reader: Callable[[int], list[dict[str, Any]]] | None = None,
        toolbar_button_reader: Callable[[int], list[dict[str, Any]]] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        platform_supported: bool | None = None,
    ) -> None:
        self._desktop_factory = desktop_factory
        self._keyboard_sender = keyboard_sender
        self._foreground_handle_getter = (
            foreground_handle_getter or _default_foreground_handle
        )
        self._window_capture = window_capture_fn
        self._window_rect_getter = window_rect_getter or _default_window_rect
        self._pointer_clicker = pointer_clicker or _default_pointer_clicker
        self._native_command_sender = (
            native_command_sender or _default_native_command_sender
        )
        self._native_menu_reader = native_menu_reader or _default_native_menu_entries
        self._toolbar_button_reader = (
            toolbar_button_reader or _default_toolbar_button_reader
        )
        self._sleep = sleep_fn
        windows_supported = os.name == "nt" if platform_supported is None else bool(
            platform_supported
        )
        self.supported = windows_supported
        self.miller_plane_transaction_supported = bool(
            windows_supported and window_capture_fn is not None
        )
        self.unavailable_reason: str | None = None

        if not windows_supported:
            self.unavailable_reason = "Local UI Automation view replay is Windows-only."
            return
        if self._desktop_factory is not None and self._keyboard_sender is not None:
            return
        try:
            with _external_comtypes_gen_cache():
                from pywinauto import Desktop
                from pywinauto.keyboard import send_keys
        except Exception as exc:
            self.supported = False
            self.miller_plane_transaction_supported = False
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
                "safe_for_miller_plane_transaction": False,
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
                "safe_for_miller_plane_transaction": False,
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
            "safe_for_miller_plane_transaction": bool(
                safe and self.miller_plane_transaction_supported
            ),
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
        structure_path: str | Path | None = None,
        evidence_dir: str | Path | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Execute one allowlisted standard, isometric, or Miller recipe."""

        if str(execution_recipe.get("recipe_kind") or "") in (
            MILLER_VIEW_ONTO_RECIPE_KINDS
        ):
            return self._execute_miller_plane_recipe(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                execution_recipe=execution_recipe,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
                structure_path=structure_path,
                evidence_dir=evidence_dir,
                expected_revision=expected_revision,
            )

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
            "gui_input_performed": False,
            "gui_transiently_modified": False,
            "reset_invocation_succeeded": False,
            "keyboard_focus_verified": False,
            "key_sequence_sent": [],
            "modifier_keys": [],
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "visual_acceptance_recorded": False,
            "keyboard_stages": None,
            "movement_dialog_closed": None,
            "rotation_increment_restored_degrees": None,
            "movement_screen_factor": None,
        }
        try:
            if not self.supported:
                raise UiaReplayError(
                    self.unavailable_reason or "Local UIA backend is unavailable."
                )
            validated = self._validate_recipe(execution_recipe)
            view_name = str(validated["view_name"])
            key_sequence = list(validated.get("key_sequence") or [])
            keyboard_stages = validated.get("keyboard_stages")
            receipt["view_name"] = view_name
            if isinstance(keyboard_stages, list):
                receipt["expected_keyboard_stages"] = keyboard_stages
            else:
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

            if isinstance(keyboard_stages, list):
                receipt["failure_phase"] = "staged_keyboard_input"
                self._execute_isometric_stages(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    execution_recipe=execution_recipe,
                    keyboard_stages=keyboard_stages,
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                    receipt=receipt,
                )
            elif key_sequence:
                receipt["failure_phase"] = "viewport_focus"
                sent = self._focus_viewport_and_send_keys(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    key_sequence=key_sequence,
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                receipt["keyboard_focus_verified"] = True
                receipt["failure_phase"] = "keyboard_input"
                receipt["key_sequence_sent"].extend(sent)

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

    def execute_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        registry_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Invoke Fit-to-View after a fresh, exact-window UIA preflight."""

        started_at = _utc_now()
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "kind": "materials_studio_local_uia_fit_to_view",
            "started_at": started_at,
            "window_handle": window_handle,
            "expected_window_title": expected_window_title,
            "command_id": FIT_TO_VIEW_COMMAND_ID,
            "registry_sha256": registry_sha256,
            "execution_succeeded": False,
            "failure_phase": "preflight",
            "gui_input_performed": False,
            "gui_modified": False,
            "structure_modified": False,
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "modifier_keys": [],
        }
        try:
            if not self.supported:
                raise UiaReplayError(
                    self.unavailable_reason or "Local UIA backend is unavailable."
                )
            if FIT_TO_VIEW_COMMAND_ID not in command_labels:
                raise UiaReplayError(
                    "Fit-to-View command label is missing from the server allowlist."
                )
            viewer_contracts = {
                FIT_TO_VIEW_TOOLBAR_NAME: toolbar_contracts[FIT_TO_VIEW_TOOLBAR_NAME]
            }
            fit_labels = {FIT_TO_VIEW_COMMAND_ID: command_labels[FIT_TO_VIEW_COMMAND_ID]}
            self._require_foreground(window_handle)
            snapshot = self._inspect_window(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                toolbar_contracts=viewer_contracts,
                command_labels=fit_labels,
            )
            if snapshot["block_reasons"]:
                raise UiaReplayError(
                    "Fresh UIA tree failed the Fit-to-View contract: "
                    + ", ".join(snapshot["block_reasons"])
                )
            fit_wrapper, fit_receipt = self._resolve_toolbar_command_target(
                snapshot=snapshot,
                toolbar_contracts=viewer_contracts,
                command_labels=fit_labels,
                toolbar_name=FIT_TO_VIEW_TOOLBAR_NAME,
                command_id=FIT_TO_VIEW_COMMAND_ID,
                expected_child_index=FIT_TO_VIEW_TOOLBAR_CHILD_INDEX,
            )
            receipt["fit_command"] = fit_receipt
            receipt["failure_phase"] = "fit_to_view_invoke"
            fit_wrapper.invoke()
            receipt["gui_input_performed"] = True
            receipt["gui_modified"] = True
            self._sleep(0.2)
            self._require_foreground(window_handle)
            final_snapshot = self._inspect_window(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                toolbar_contracts=viewer_contracts,
                command_labels=fit_labels,
            )
            if final_snapshot["block_reasons"]:
                raise UiaReplayError(
                    "The post-action UIA tree no longer matches the Fit-to-View contract: "
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
                }
            )
            return receipt
        except Exception as exc:
            receipt.update(
                {
                    "finished_at": _utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "post_action_observation_required": True,
                }
            )
            return receipt

    def _execute_miller_plane_recipe(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        structure_path: str | Path | None,
        evidence_dir: str | Path | None,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        """Create one plane, capture View Onto, and restore the exact baseline."""

        started_at = _utc_now()
        receipt: dict[str, Any] = {
            "schema_version": 2,
            "kind": "materials_studio_local_uia_miller_plane_transaction",
            "started_at": started_at,
            "window_handle": window_handle,
            "expected_window_title": expected_window_title,
            "view_name": execution_recipe.get("view_name"),
            "recipe_kind": execution_recipe.get("recipe_kind"),
            "execution_succeeded": False,
            "failure_phase": "preflight",
            "modifier_keys": [],
            "coordinate_input_used": False,
            "pointer_input_used": False,
            "pointer_input_basis": None,
            "structure_modified": False,
            "visual_acceptance_recorded": False,
            "record_call_ready": False,
            "reset_invocation_succeeded": False,
            "reset_view_used": False,
            "undo_labels_applied": [],
            "cleanup_succeeded": False,
            "manual_cleanup_required": False,
        }
        dialog: Any | None = None
        properties_initially_open: bool | None = None
        properties_toggled_open = False
        plane_created = False
        view_onto_applied = False
        structure: Path | None = None
        source_hash_before: str | None = None
        source_size_before: int | None = None
        snapshot: dict[str, Any] | None = None
        viewport_bounds: tuple[int, int, int, int] | None = None
        pre_action_viewport_bounds: tuple[int, int, int, int] | None = None
        evidence_root: Path | None = None
        pre_action_path: Path | None = None
        baseline_path: Path | None = None
        baseline_undo_label: str | None = None
        try:
            if not self.supported:
                raise UiaReplayError(
                    self.unavailable_reason or "Local UIA backend is unavailable."
                )
            if not self.miller_plane_transaction_supported:
                raise UiaReplayError(
                    "Local Miller replay requires an injected workspace BMP capture."
                )
            validated = self._validate_miller_recipe(execution_recipe)
            dialog_indices = list(validated["dialog_miller_indices"])
            dialog_text = str(validated["dialog_miller_indices_text"])
            receipt["dialog_miller_indices"] = dialog_indices
            receipt["dialog_miller_indices_text"] = dialog_text
            if expected_revision is None or expected_revision < 0:
                raise UiaReplayError(
                    "Miller replay requires the bound non-negative revision."
                )

            if structure_path is None:
                raise UiaReplayError("Miller replay requires the bound structure path.")
            structure = Path(structure_path).expanduser().resolve()
            if not structure.exists() or not structure.is_file():
                raise UiaReplayError(
                    f"Bound Miller replay structure does not exist: {structure}"
                )
            if evidence_dir is None:
                raise UiaReplayError("Miller replay requires a workspace evidence directory.")
            evidence_root = Path(evidence_dir).expanduser().resolve()
            evidence_root.mkdir(parents=True, exist_ok=True)
            source_hash_before, source_size_before = self._file_digest(structure)
            receipt["structure_artifact_path"] = str(structure)
            receipt["structure_artifact_sha256_before"] = source_hash_before

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
            self._require_selection_mode(snapshot)
            pre_action_viewport_capture = self._viewport_capture_contract(
                window_handle=window_handle,
                viewport_wrapper=snapshot["viewport_wrapper"],
            )
            pre_action_viewport_bounds = tuple(
                pre_action_viewport_capture["bounds"]
            )
            receipt["pre_action_viewport_bounds"] = list(
                pre_action_viewport_bounds
            )
            receipt["pre_action_viewport_capture_contract"] = (
                pre_action_viewport_capture
            )
            pre_action_path = evidence_root / "pre_action.bmp"
            self._capture_window(window_handle, pre_action_path)
            receipt["pre_action_screenshot_path"] = str(pre_action_path)
            receipt["pre_action_view_baseline_captured"] = True
            pre_action_document_title = self._viewer_document_title(
                snapshot["top"]
            )
            receipt["pre_action_document_title"] = pre_action_document_title

            properties_initially_open = self._properties_explorer_open(snapshot["top"])
            receipt["properties_explorer_initially_open"] = properties_initially_open
            if not properties_initially_open:
                receipt["failure_phase"] = "properties_explorer_open"
                properties_entry = self._properties_explorer_menu_entry(
                    window_handle
                )
                if properties_entry.get("enabled") is not True:
                    raise UiaReplayError("Properties Explorer command is disabled.")
                receipt["properties_explorer_command_mapping"] = properties_entry
                receipt["gui_input_performed"] = True
                receipt["gui_transiently_modified"] = True
                self._native_command_sender(
                    window_handle, PROPERTIES_EXPLORER_COMMAND_ID
                )
                properties_toggled_open = True
                self._sleep(0.4)
                top = self._top_window(window_handle)
                self._require_window_title(
                    top, expected_window_title=expected_window_title
                )
                if not self._properties_explorer_open(top):
                    raise UiaReplayError("Properties Explorer did not open.")
                snapshot = self._inspect_window(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                if snapshot["block_reasons"]:
                    raise UiaReplayError(
                        "Fresh UIA tree after opening Properties Explorer failed: "
                        + ", ".join(snapshot["block_reasons"])
                    )
                self._require_selection_mode(snapshot)

            baseline_undo_label = self._read_undo_label(window_handle)
            receipt["pre_action_undo_label"] = baseline_undo_label
            viewport_capture = self._viewport_capture_contract(
                window_handle=window_handle,
                viewport_wrapper=snapshot["viewport_wrapper"],
            )
            viewport_bounds = tuple(viewport_capture["bounds"])
            receipt["viewport_bounds"] = list(viewport_bounds)
            receipt["viewport_capture_contract"] = viewport_capture
            if properties_toggled_open:
                baseline_path = evidence_root / "transaction_baseline.bmp"
                self._capture_window(window_handle, baseline_path)
            else:
                baseline_path = pre_action_path
            receipt["baseline_screenshot_path"] = str(baseline_path)

            receipt["failure_phase"] = "miller_dialog_open"
            receipt["gui_input_performed"] = True
            receipt["gui_transiently_modified"] = True
            dialog = self._open_miller_dialog(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
            )
            controls = self._miller_dialog_controls(dialog)
            receipt["runtime_dialog_contract"] = controls["receipt"]
            if self._toggle_state(controls["show_symmetry"]) != 0:
                raise UiaReplayError("Miller symmetry images must be disabled.")
            if self._toggle_state(controls["show_periodic"]) != 0:
                raise UiaReplayError("Miller parallel planes must be disabled.")

            receipt["failure_phase"] = "miller_indices_entry"
            indices_edit = controls["indices_edit"]
            value_pattern = getattr(indices_edit, "iface_value", None)
            if value_pattern is None:
                raise UiaReplayError("TxtHKL child does not expose ValuePattern.")
            value_pattern.SetValue(dialog_text)
            self._sleep(0.5)
            fresh_controls = self._miller_dialog_controls(dialog)
            readback = self._value_text(fresh_controls["indices_edit"]).strip()
            receipt["dialog_miller_indices_text_before_create"] = readback
            receipt["dialog_miller_indices_value_source"] = (
                "fresh_modeless_child_accessibility_value"
            )
            if readback != dialog_text:
                raise UiaReplayError(
                    "TxtHKL readback differs from the prepared value: "
                    f"expected {dialog_text!r}, observed {readback!r}."
                )

            receipt["failure_phase"] = "miller_plane_create"
            fresh_controls["create"].invoke()
            plane_created = True
            self._sleep(0.5)
            dialog.close()
            dialog = None
            self._sleep(0.4)
            dirty_top = self._top_window(window_handle)
            dirty_title = self._viewer_document_title(dirty_top)
            receipt["dirty_document_title_after_create"] = dirty_title
            receipt["dirty_window_title_after_create"] = dirty_title
            self._require_expected_dirty_title(
                dirty_title, expected_window_title=pre_action_document_title
            )
            self._require_undo_label(window_handle, MILLER_CREATE_UNDO_LABEL)

            created_path = evidence_root / "created.bmp"
            self._capture_window(window_handle, created_path)
            receipt["created_screenshot_path"] = str(created_path)
            diff = analyze_miller_plane_bmp_diff(
                baseline_path,
                created_path,
                viewport_bounds=viewport_bounds,
            )
            receipt["viewport_selection_diff"] = diff

            receipt["failure_phase"] = "miller_plane_select"
            window_left, window_top, _window_right, _window_bottom = (
                self._window_rect_getter(window_handle)
            )
            candidate_x, candidate_y = diff["candidate_window_pixel"]
            screen_x = int(window_left) + int(candidate_x)
            screen_y = int(window_top) + int(candidate_y)
            self._require_foreground(window_handle)
            self._pointer_clicker(screen_x, screen_y)
            receipt["coordinate_input_used"] = True
            receipt["pointer_input_used"] = True
            receipt["pointer_input_basis"] = MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS
            receipt["pointer_screen_coordinate"] = [screen_x, screen_y]
            self._sleep(0.4)
            properties = self._verify_miller_properties(
                self._top_window(window_handle),
                expected_label=str(validated["properties_miller_label"]),
            )
            receipt["properties_verification"] = properties
            self._require_undo_label(window_handle, MILLER_CREATE_UNDO_LABEL)

            receipt["failure_phase"] = "view_onto_mapping"
            mapping = self._verify_view_onto_native_mapping(
                snapshot=snapshot,
                execution_recipe=execution_recipe,
            )
            receipt["view_onto_native_command_mapping"] = mapping
            receipt["failure_phase"] = "view_onto_invoke"
            self._native_command_sender(window_handle, VIEWER_VIEW_ONTO_COMMAND_ID)
            view_onto_applied = True
            self._sleep(0.5)
            self._require_undo_label(window_handle, MILLER_VIEW_ONTO_UNDO_LABEL)

            aligned_path = evidence_root / "aligned.bmp"
            self._capture_window(window_handle, aligned_path)
            receipt["aligned_screenshot_path"] = str(aligned_path)
            receipt["screenshot_captured_before_cleanup"] = True

            receipt["failure_phase"] = "cleanup_view_onto"
            self._invoke_undo_exact(window_handle, MILLER_VIEW_ONTO_UNDO_LABEL)
            receipt["undo_labels_applied"].append(MILLER_VIEW_ONTO_UNDO_LABEL)
            view_onto_applied = False
            self._sleep(0.4)
            self._require_undo_label(window_handle, MILLER_CREATE_UNDO_LABEL)
            receipt["failure_phase"] = "cleanup_create_plane"
            self._invoke_undo_exact(window_handle, MILLER_CREATE_UNDO_LABEL)
            receipt["undo_labels_applied"].append(MILLER_CREATE_UNDO_LABEL)
            plane_created = False
            self._sleep(0.5)

            final_top = self._top_window(window_handle)
            self._require_window_title(
                final_top, expected_window_title=expected_window_title
            )
            final_document_title = self._viewer_document_title(final_top)
            receipt["final_document_title"] = final_document_title
            if final_document_title != pre_action_document_title:
                raise UiaReplayError(
                    "Miller cleanup did not restore the pre-action document title: "
                    f"expected {pre_action_document_title!r}, observed "
                    f"{final_document_title!r}."
                )
            observed_final_undo = self._read_undo_label(window_handle)
            if observed_final_undo != baseline_undo_label:
                raise UiaReplayError(
                    "Miller cleanup did not restore the pre-action undo baseline: "
                    f"expected {baseline_undo_label!r}, observed "
                    f"{observed_final_undo!r}."
                )
            final_path = evidence_root / "final.bmp"
            self._capture_window(window_handle, final_path)
            receipt["final_screenshot_path"] = str(final_path)
            restoration = compare_bmp_region(
                baseline_path,
                final_path,
                bounds=viewport_bounds,
            )
            receipt["viewport_restoration"] = restoration
            if restoration["exact_match"] is not True:
                raise UiaReplayError(
                    "Miller cleanup did not exactly restore the pre-action viewport."
                )

            source_hash_after, source_size_after = self._file_digest(structure)
            receipt["structure_artifact_sha256_after"] = source_hash_after
            receipt["structure_artifact_size_before"] = source_size_before
            receipt["structure_artifact_size_after"] = source_size_after
            structure_unchanged = bool(
                source_hash_after == source_hash_before
                and source_size_after == source_size_before
            )
            receipt["structure_unchanged"] = structure_unchanged
            if not structure_unchanged:
                raise UiaReplayError(
                    "Bound structure artifact changed during Miller view replay."
                )

            receipt["cleanup_succeeded"] = True
            receipt["miller_plane_evidence"] = self._miller_replay_evidence(
                execution_recipe=execution_recipe,
                structure_path=structure,
                structure_sha256=source_hash_before,
                dialog_indices=dialog_indices,
                dialog_text=dialog_text,
                aligned_path=aligned_path,
                undo_labels=list(receipt["undo_labels_applied"]),
            )
            receipt["runtime_ui_evidence"] = self._miller_runtime_ui_evidence(
                execution_recipe=execution_recipe,
                expected_window_title=expected_window_title,
                window_handle=window_handle,
                expected_revision=expected_revision,
                structure_path=structure,
                structure_sha256=source_hash_before,
                aligned_path=aligned_path,
                undo_labels=list(receipt["undo_labels_applied"]),
            )
            receipt.update(
                {
                    "execution_succeeded": True,
                    "failure_phase": None,
                    "finished_at": _utc_now(),
                    "gui_input_performed": True,
                    "gui_transiently_modified": True,
                    "post_action_observation_required": True,
                    "record_call_ready": False,
                }
            )
        except Exception as exc:
            cleanup_errors: list[str] = []
            if dialog is not None:
                try:
                    dialog.close()
                    dialog = None
                    self._sleep(0.2)
                except Exception as cleanup_exc:
                    cleanup_errors.append(f"dialog_close: {cleanup_exc}")
            for active, expected_label in (
                (view_onto_applied, MILLER_VIEW_ONTO_UNDO_LABEL),
                (plane_created, MILLER_CREATE_UNDO_LABEL),
            ):
                if not active:
                    continue
                try:
                    self._invoke_undo_exact(window_handle, expected_label)
                    receipt["undo_labels_applied"].append(expected_label)
                    if expected_label == MILLER_VIEW_ONTO_UNDO_LABEL:
                        view_onto_applied = False
                    else:
                        plane_created = False
                    self._sleep(0.3)
                except Exception as cleanup_exc:
                    cleanup_errors.append(f"{expected_label}: {cleanup_exc}")
                    break
            if not view_onto_applied and not plane_created:
                if baseline_undo_label is not None:
                    try:
                        observed_undo = self._read_undo_label(window_handle)
                        if observed_undo != baseline_undo_label:
                            raise UiaReplayError(
                                "cleanup undo baseline differs: "
                                f"expected {baseline_undo_label!r}, observed "
                                f"{observed_undo!r}"
                            )
                    except Exception as cleanup_exc:
                        cleanup_errors.append(f"undo_baseline: {cleanup_exc}")
                try:
                    cleanup_top = self._top_window(window_handle)
                    self._require_window_title(
                        cleanup_top,
                        expected_window_title=expected_window_title,
                    )
                    if receipt.get("pre_action_document_title") is not None:
                        cleanup_document_title = self._viewer_document_title(
                            cleanup_top
                        )
                        if cleanup_document_title != receipt.get(
                            "pre_action_document_title"
                        ):
                            raise UiaReplayError(
                                "cleanup document title differs: expected "
                                f"{receipt.get('pre_action_document_title')!r}, "
                                f"observed {cleanup_document_title!r}"
                            )
                except Exception as cleanup_exc:
                    cleanup_errors.append(f"window_title: {cleanup_exc}")
                if (
                    baseline_path is not None
                    and viewport_bounds is not None
                    and evidence_root is not None
                ):
                    try:
                        cleanup_path = evidence_root / "cleanup_transaction.bmp"
                        self._capture_window(window_handle, cleanup_path)
                        cleanup_restoration = compare_bmp_region(
                            baseline_path,
                            cleanup_path,
                            bounds=viewport_bounds,
                        )
                        receipt["cleanup_viewport_restoration"] = (
                            cleanup_restoration
                        )
                        if cleanup_restoration["exact_match"] is not True:
                            raise UiaReplayError(
                                "cleanup did not restore the transaction viewport"
                            )
                    except Exception as cleanup_exc:
                        cleanup_errors.append(
                            f"viewport_restoration: {cleanup_exc}"
                        )
                if structure is not None and source_hash_before is not None:
                    try:
                        cleanup_hash, cleanup_size = self._file_digest(structure)
                        receipt["cleanup_structure_artifact_sha256"] = cleanup_hash
                        if (
                            cleanup_hash != source_hash_before
                            or cleanup_size != source_size_before
                        ):
                            raise UiaReplayError(
                                "cleanup structure hash or size differs"
                            )
                    except Exception as cleanup_exc:
                        cleanup_errors.append(f"structure_restoration: {cleanup_exc}")
            receipt.update(
                {
                    "execution_succeeded": False,
                    "finished_at": _utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "cleanup_errors": cleanup_errors,
                    "cleanup_succeeded": not cleanup_errors
                    and not view_onto_applied
                    and not plane_created,
                    "manual_cleanup_required": bool(cleanup_errors),
                    "post_action_observation_required": True,
                    "record_call_ready": False,
                    "retry_restarts_from_pre_action_view_baseline": True,
                }
            )
        finally:
            if properties_toggled_open:
                try:
                    top = self._top_window(window_handle)
                    if self._properties_explorer_open(top):
                        self._native_command_sender(
                            window_handle, PROPERTIES_EXPLORER_COMMAND_ID
                        )
                        self._sleep(0.3)
                    receipt["properties_explorer_state_restored"] = not (
                        self._properties_explorer_open(
                            self._top_window(window_handle)
                        )
                    )
                except Exception as exc:
                    receipt["properties_explorer_state_restored"] = False
                    receipt.setdefault("cleanup_errors", []).append(
                        f"properties_explorer_restore: {exc}"
                    )
                    receipt["cleanup_succeeded"] = False
                    receipt["manual_cleanup_required"] = True
                    receipt["execution_succeeded"] = False
            elif properties_initially_open is not None:
                receipt["properties_explorer_state_restored"] = bool(
                    self._properties_explorer_open(
                        self._top_window(window_handle)
                    )
                    == properties_initially_open
                )
            if receipt.get("properties_explorer_state_restored") is False:
                receipt.setdefault("cleanup_errors", []).append(
                    "properties_explorer_restore: initial explorer state was not restored"
                )
                receipt["cleanup_succeeded"] = False
                receipt["manual_cleanup_required"] = True
                receipt["execution_succeeded"] = False
                receipt.pop("miller_plane_evidence", None)
                receipt.pop("runtime_ui_evidence", None)
            if (
                pre_action_path is not None
                and pre_action_viewport_bounds is not None
                and evidence_root is not None
            ):
                try:
                    restored_path = evidence_root / "post_transaction_restored.bmp"
                    self._capture_window(window_handle, restored_path)
                    receipt["post_transaction_restored_screenshot_path"] = str(
                        restored_path
                    )
                    pre_action_restoration = compare_bmp_region(
                        pre_action_path,
                        restored_path,
                        bounds=pre_action_viewport_bounds,
                    )
                    receipt["pre_action_viewport_restoration"] = (
                        pre_action_restoration
                    )
                    if pre_action_restoration["exact_match"] is not True:
                        raise UiaReplayError(
                            "the original pre-action viewport was not exactly restored"
                        )
                except Exception as exc:
                    receipt.setdefault("cleanup_errors", []).append(
                        f"pre_action_viewport_restoration: {exc}"
                    )
                    receipt["cleanup_succeeded"] = False
                    receipt["manual_cleanup_required"] = True
                    receipt["execution_succeeded"] = False
                    receipt.pop("miller_plane_evidence", None)
                    receipt.pop("runtime_ui_evidence", None)
        return receipt

    @staticmethod
    def _file_digest(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_count = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                byte_count += len(chunk)
        return digest.hexdigest(), byte_count

    def _top_window(self, window_handle: int) -> Any:
        if self._desktop_factory is None:
            raise UiaReplayError("UIA Desktop factory is unavailable.")
        return self._desktop_factory(backend="uia").window(handle=window_handle)

    @staticmethod
    def _wrapper_rect(wrapper: Any) -> tuple[int, int, int, int]:
        rectangle = wrapper.rectangle()
        return (
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )

    def _viewport_capture_contract(
        self, *, window_handle: int, viewport_wrapper: Any
    ) -> dict[str, Any]:
        """Clip the viewport through its visible UIA ancestors to the target window."""

        window_left, window_top, window_right, window_bottom = (
            self._window_rect_getter(window_handle)
        )
        clip_chain: list[dict[str, Any]] = []
        current = viewport_wrapper
        reached_target_window = False
        for depth in range(16):
            if _safe_call(current, "is_visible", True) is not True:
                raise UiaReplayError(
                    "The viewport clipping ancestry contains a hidden wrapper."
                )
            rect = self._wrapper_rect(current)
            current_handle = int(_safe_call(current, "handle", 0) or 0)
            clip_chain.append(
                {
                    "depth": depth,
                    "handle": current_handle or None,
                    "name": str(_element_value(current, "name", "") or ""),
                    "control_type": str(
                        _element_value(current, "control_type", "") or ""
                    ),
                    "class_name": str(
                        _element_value(current, "class_name", "") or ""
                    ),
                    "rect": list(rect),
                }
            )
            if current_handle == window_handle:
                reached_target_window = True
                break
            parent = _safe_call(current, "parent", None)
            if parent is None or parent is current:
                break
            current = parent
        if not reached_target_window:
            raise UiaReplayError(
                "The unique viewport ancestry did not reach the exact target window."
            )
        mdi_client_count = sum(
            item.get("class_name") == "MDIClient" for item in clip_chain
        )
        if mdi_client_count != 1:
            raise UiaReplayError(
                "The unique viewport ancestry did not contain exactly one visible "
                f"MDIClient; found {mdi_client_count}."
            )

        clip_rects = [tuple(item["rect"]) for item in clip_chain]
        visible_left = max(window_left, *(rect[0] for rect in clip_rects))
        visible_top = max(window_top, *(rect[1] for rect in clip_rects))
        visible_right = min(window_right, *(rect[2] for rect in clip_rects))
        visible_bottom = min(window_bottom, *(rect[3] for rect in clip_rects))
        visible_width = visible_right - visible_left
        visible_height = visible_bottom - visible_top
        if visible_width < 100 or visible_height < 100:
            raise UiaReplayError(
                "The unique viewport has no sufficiently large visible intersection "
                "through its target-window ancestor chain: "
                f"window={(window_left, window_top, window_right, window_bottom)}, "
                f"clip_chain={clip_chain}."
            )
        bounds = (
            visible_left - window_left,
            visible_top - window_top,
            visible_right - window_left,
            visible_bottom - window_top,
        )
        return {
            "bounds": list(bounds),
            "window_rect": [window_left, window_top, window_right, window_bottom],
            "clip_chain": clip_chain,
            "target_window_reached": True,
            "mdi_client_count": mdi_client_count,
            "mdi_client_observed": True,
            "status_bar_excluded_by_ancestor_clipping": bool(
                visible_bottom < window_bottom
            ),
        }

    def _viewport_capture_bounds(
        self, *, window_handle: int, viewport_wrapper: Any
    ) -> tuple[int, int, int, int]:
        contract = self._viewport_capture_contract(
            window_handle=window_handle,
            viewport_wrapper=viewport_wrapper,
        )
        return tuple(int(value) for value in contract["bounds"])

    def _capture_window(self, window_handle: int, path: Path) -> None:
        if self._window_capture is None:
            raise UiaReplayError("Workspace BMP capture is unavailable.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._window_capture(window_handle, path)
        if not path.exists() or not path.is_file() or path.stat().st_size < 54:
            raise UiaReplayError(f"Workspace BMP capture failed: {path}")

    @staticmethod
    def _toggle_state(wrapper: Any) -> int | None:
        observed = _safe_call(wrapper, "get_toggle_state", None)
        if observed is not None:
            return int(observed)
        try:
            return int(wrapper.iface_toggle.CurrentToggleState)
        except Exception:
            return None

    @staticmethod
    def _value_text(wrapper: Any) -> str:
        try:
            return str(wrapper.iface_value.CurrentValue)
        except Exception:
            return str(_element_value(wrapper, "name", "") or "")

    @staticmethod
    def _unique_descendant(
        root: Any,
        *,
        automation_id: str | None = None,
        name: str | None = None,
        control_type: str | None = None,
    ) -> Any:
        matches = []
        for item in root.descendants():
            if automation_id is not None and str(
                _element_value(item, "automation_id", "") or ""
            ) != automation_id:
                continue
            if name is not None and str(
                _element_value(item, "name", "") or ""
            ) != name:
                continue
            if control_type is not None and str(
                _element_value(item, "control_type", "") or ""
            ) != control_type:
                continue
            if _safe_call(item, "is_visible", True) is not True:
                continue
            matches.append(item)
        if len(matches) != 1:
            identity = automation_id or name or control_type or "requested control"
            raise UiaReplayError(
                f"{identity} was not uniquely observed; found {len(matches)}."
            )
        return matches[0]

    def _properties_explorer_open(self, top: Any) -> bool:
        return any(
            str(_element_value(item, "automation_id", "") or "")
            == PROPERTIES_EXPLORER_CONTROL_ID
            and _safe_call(item, "is_visible", False) is True
            for item in top.descendants()
        )

    def _native_menu_entry(
        self,
        window_handle: int,
        *,
        command_id: int,
        expected_path: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        entries = [
            item
            for item in self._native_menu_reader(window_handle)
            if int(item.get("command_id", -1)) == command_id
        ]
        if len(entries) != 1:
            raise UiaReplayError(
                f"Native command {command_id} was not uniquely mapped in the live menu."
            )
        entry = entries[0]
        if expected_path is not None and tuple(entry.get("path") or []) != expected_path:
            raise UiaReplayError(
                f"Native command {command_id} menu path differs from the reviewed path."
            )
        return dict(entry)

    def _read_undo_label(self, window_handle: int) -> str:
        entry = self._native_menu_entry(window_handle, command_id=UNDO_COMMAND_ID)
        label = str(entry.get("label") or "").strip()
        if not label:
            raise UiaReplayError("The live Undo command has no readable label.")
        return label

    def _properties_explorer_menu_entry(
        self, window_handle: int
    ) -> dict[str, Any]:
        """Verify the exact Properties command, including MS 20.1 owner-draw menus."""

        entry = self._native_menu_entry(
            window_handle,
            command_id=PROPERTIES_EXPLORER_COMMAND_ID,
        )
        path = tuple(str(item) for item in entry.get("path") or [])
        if path == ("View", "Explorers", "Properties Explorer"):
            path_verification = "readable_native_menu_labels"
        elif path == ("View", "", "") and not str(entry.get("label") or ""):
            path_verification = (
                "ms20_owner_drawn_submenu_labels_unavailable_exact_command_id"
            )
        else:
            raise UiaReplayError(
                "Properties Explorer native command is outside the reviewed View "
                f"menu shape: observed path {path!r}."
            )
        return {
            **entry,
            "command_id_verified": True,
            "path_verification": path_verification,
        }

    def _require_undo_label(self, window_handle: int, expected_label: str) -> None:
        observed = self._read_undo_label(window_handle)
        if observed != expected_label:
            raise UiaReplayError(
                f"Expected undo label {expected_label!r}, observed {observed!r}."
            )

    def _invoke_undo_exact(self, window_handle: int, expected_label: str) -> None:
        entry = self._native_menu_entry(window_handle, command_id=UNDO_COMMAND_ID)
        observed = str(entry.get("label") or "").strip()
        if observed != expected_label:
            raise UiaReplayError(
                "Refusing to undo outside the Miller transaction: "
                f"expected {expected_label!r}, observed {observed!r}."
            )
        if entry.get("enabled") is not True:
            raise UiaReplayError(f"Undo command {expected_label!r} is disabled.")
        self._native_command_sender(window_handle, UNDO_COMMAND_ID)

    def _open_miller_dialog(
        self, *, window_handle: int, expected_window_title: str
    ) -> Any:
        top = self._top_window(window_handle)
        self._require_window_title(top, expected_window_title=expected_window_title)
        top.set_focus()
        self._sleep(0.1)
        self._require_foreground(window_handle)
        if self._keyboard_sender is None:
            raise UiaReplayError("Keyboard sender is unavailable.")
        self._keyboard_sender("%t")
        self._sleep(0.25)
        self._keyboard_sender("m")
        self._sleep(0.5)
        top = self._top_window(window_handle)
        matches = [
            item
            for item in top.descendants()
            if str(_element_value(item, "name", "") or "")
            == MILLER_PLANES_WINDOW_TITLE
            and str(_element_value(item, "control_type", "") or "") == "Window"
            and _safe_call(item, "is_visible", False) is True
        ]
        if len(matches) != 1:
            raise UiaReplayError(
                "The owned Miller Planes window was not uniquely observed."
            )
        return matches[0]

    def _miller_dialog_controls(self, dialog: Any) -> dict[str, Any]:
        pane = self._unique_descendant(
            dialog, automation_id=MILLER_PLANES_CONTROL_ID, control_type="Pane"
        )
        indices_pane = self._unique_descendant(
            pane, automation_id=MILLER_INDICES_CONTROL_ID, control_type="Pane"
        )
        indices_edit = self._unique_descendant(
            indices_pane, automation_id=MOVEMENT_NUMERIC_EDIT_ID, control_type="Edit"
        )
        create = self._unique_descendant(
            pane, automation_id=MILLER_CREATE_CONTROL_ID, control_type="Button"
        )
        show_symmetry = self._unique_descendant(
            pane, automation_id=MILLER_SHOW_SYMMETRY_CONTROL_ID
        )
        show_periodic = self._unique_descendant(
            pane, automation_id=MILLER_SHOW_PERIODIC_CONTROL_ID
        )
        if not _invoke_pattern_available(create):
            raise UiaReplayError("CmdCreate does not expose InvokePattern.")
        return {
            "indices_edit": indices_edit,
            "create": create,
            "show_symmetry": show_symmetry,
            "show_periodic": show_periodic,
            "receipt": {
                "dialog_title": MILLER_PLANES_WINDOW_TITLE,
                "dialog_control_id": MILLER_PLANES_CONTROL_ID,
                "miller_indices_control_id": MILLER_INDICES_CONTROL_ID,
                "miller_indices_edit_control_id": MOVEMENT_NUMERIC_EDIT_ID,
                "create_button_control_id": MILLER_CREATE_CONTROL_ID,
                "show_symmetry_control_id": MILLER_SHOW_SYMMETRY_CONTROL_ID,
                "show_periodic_control_id": MILLER_SHOW_PERIODIC_CONTROL_ID,
                "create_invoke_pattern_verified": True,
            },
        }

    def _require_selection_mode(self, snapshot: dict[str, Any]) -> None:
        toolbar = snapshot["toolbar_wrappers"].get("3D Viewer")
        toolbar_result = snapshot["toolbars"].get("3D Viewer")
        if toolbar is None or not isinstance(toolbar_result, dict):
            raise UiaReplayError("The exact 3D Viewer toolbar is unavailable.")
        entries = list(toolbar_result.get("children") or [])
        children = list(toolbar.children())
        if len(entries) <= 0 or len(children) <= 0:
            raise UiaReplayError("3D Viewer Selection control is unavailable.")
        if self._toggle_state(children[0]) != 1:
            raise UiaReplayError(
                "3D Viewer Selection mode must already be active before the "
                "fresh-difference plane click."
            )

    def _verify_miller_properties(
        self, top: Any, *, expected_label: str
    ) -> dict[str, Any]:
        properties = self._unique_descendant(
            top, automation_id=PROPERTIES_EXPLORER_CONTROL_ID
        )
        object_type = self._unique_descendant(
            properties, automation_id=PROPERTIES_OBJECT_TYPE_CONTROL_ID
        )
        object_type_value = self._value_text(object_type).strip()
        if object_type_value != "Miller Plane":
            raise UiaReplayError(
                "Properties Explorer did not identify the selected object as "
                f"Miller Plane; observed {object_type_value!r}."
            )
        grid = self._unique_descendant(
            properties, automation_id=PROPERTIES_GRID_CONTROL_ID
        )
        records = [
            item
            for item in grid.descendants()
            if str(_element_value(item, "name", "") or "")
            == "MillerIndex Record 0"
            and str(_element_value(item, "control_type", "") or "")
            == "DataItem"
        ]
        if len(records) != 1:
            diagnostics: list[dict[str, Any]] = []
            for item in properties.descendants():
                name = str(_element_value(item, "name", "") or "")
                automation_id = str(
                    _element_value(item, "automation_id", "") or ""
                )
                value = self._value_text(item).strip()
                combined = f"{name} {automation_id} {value}"
                if "miller" not in combined.lower() and expected_label not in combined:
                    continue
                diagnostics.append(
                    {
                        "name": name,
                        "automation_id": automation_id,
                        "control_type": str(
                            _element_value(item, "control_type", "") or ""
                        ),
                        "value": value,
                        "visible": _safe_call(item, "is_visible", None),
                    }
                )
                if len(diagnostics) >= 30:
                    break
            raise UiaReplayError(
                "Properties Explorer did not expose exactly one MillerIndex record; "
                f"observed {len(records)} legacy matches and diagnostics={diagnostics!r}."
            )
        observed_label = self._value_text(records[0]).strip()
        if observed_label != expected_label:
            raise UiaReplayError(
                "Selected Miller plane label differs from the prepared recipe: "
                f"expected {expected_label!r}, observed {observed_label!r}."
            )
        return {
            "properties_control_id": PROPERTIES_EXPLORER_CONTROL_ID,
            "object_type_control_id": PROPERTIES_OBJECT_TYPE_CONTROL_ID,
            "properties_filter": object_type_value,
            "miller_record_count": 1,
            "miller_record_name": "MillerIndex Record 0",
            "miller_record_control_type": "DataItem",
            "miller_record_visible": _safe_call(
                records[0], "is_visible", None
            ),
            "miller_record_virtualized_visibility_allowed": True,
            "properties_miller_label": observed_label,
        }

    def _verify_view_onto_native_mapping(
        self,
        *,
        snapshot: dict[str, Any],
        execution_recipe: dict[str, Any],
    ) -> dict[str, Any]:
        invocation = execution_recipe.get("view_command_invocation")
        if not isinstance(invocation, dict):
            raise UiaReplayError("Prepared View Onto native mapping is missing.")
        expected_mapping = {
            "selection_numeric_command_id": VIEWER_SELECTION_COMMAND_ID,
            "recenter_numeric_command_id": VIEWER_RECENTER_COMMAND_ID,
            "view_onto_numeric_command_id": VIEWER_VIEW_ONTO_COMMAND_ID,
            "fit_numeric_command_id": VIEWER_FIT_COMMAND_ID,
        }
        for field, expected in expected_mapping.items():
            if invocation.get(field) != expected:
                raise UiaReplayError(
                    f"Prepared View Onto mapping differs for {field}."
                )
        toolbar = snapshot["toolbar_wrappers"].get("3D Viewer")
        if toolbar is None:
            raise UiaReplayError("The exact 3D Viewer toolbar is unavailable.")
        toolbar_handle = _element_value(toolbar, "handle")
        if toolbar_handle is None:
            toolbar_handle = _safe_call(toolbar, "handle", None)
        if not toolbar_handle:
            raise UiaReplayError("The 3D Viewer toolbar native handle is unavailable.")
        rows = self._toolbar_button_reader(int(toolbar_handle))
        by_index = {int(item["index"]): item for item in rows}
        checks = {
            0: VIEWER_SELECTION_COMMAND_ID,
            6: VIEWER_RECENTER_COMMAND_ID,
            7: VIEWER_FIT_COMMAND_ID,
        }
        for index, command_id in checks.items():
            row = by_index.get(index)
            if row is None or int(row.get("command_id", -1)) != command_id:
                raise UiaReplayError(
                    "Live 3D Viewer numeric toolbar mapping differs from the "
                    f"reviewed mapping at index {index}."
                )
        recenter_style = int(by_index[6].get("style", -1))
        if recenter_style != 10:
            raise UiaReplayError(
                "Live Recenter toolbar button is not the reviewed CHECK|DROPDOWN style."
            )
        return {
            **expected_mapping,
            "toolbar_handle": int(toolbar_handle),
            "recenter_button_style": recenter_style,
            "recenter_button_style_verified": True,
            "installed_registry_order_verified": invocation.get(
                "installed_registry_order_verified"
            )
            is True,
            "view_onto_native_command_mapping_verified": True,
            "invocation_method": "wm_command_after_live_toolbar_mapping_verification",
        }

    @staticmethod
    def _require_expected_dirty_title(
        observed_title: str, *, expected_window_title: str
    ) -> None:
        allowed_titles = {
            f"{expected_window_title}*",
            f"{expected_window_title} *",
        }
        if observed_title not in allowed_titles:
            raise UiaReplayError(
                "Creating one transient Miller plane did not produce the exact "
                "clean-to-dirty viewer-document title transition."
            )

    @staticmethod
    def _viewer_document_title(top: Any) -> str:
        """Return the unique visible MDI document that owns the 3D viewport."""

        matches: list[Any] = []
        for item in top.descendants():
            if str(_element_value(item, "control_type", "") or "") != "Window":
                continue
            if _safe_call(item, "is_visible", False) is not True:
                continue
            if not any(
                str(_element_value(child, "class_name", "") or "")
                == "CViewer3DCtrl"
                for child in item.descendants()
            ):
                continue
            matches.append(item)
        if len(matches) != 1:
            raise UiaReplayError(
                "The visible viewer document window was not uniquely observed; "
                f"found {len(matches)}."
            )
        title = str(_element_value(matches[0], "name", "") or "").strip()
        if not title:
            raise UiaReplayError("The visible viewer document has no readable title.")
        return title

    def _validate_miller_recipe(
        self, execution_recipe: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(execution_recipe, dict):
            raise UiaReplayError("Execution recipe must be a JSON object.")
        recipe_kind = str(execution_recipe.get("recipe_kind") or "")
        if recipe_kind not in MILLER_VIEW_ONTO_RECIPE_KINDS:
            raise UiaReplayError("Prepared recipe is not a Miller View Onto recipe.")
        if execution_recipe.get("automation_ready") is not True:
            raise UiaReplayError("Prepared Miller recipe is not automation-ready.")
        for field in (
            "structure_mutation_allowed",
            "launch_new_matstudio_process_allowed",
            "blind_coordinate_action_allowed",
        ):
            if execution_recipe.get(field) is not False:
                raise UiaReplayError(f"Prepared Miller recipe does not prohibit {field}.")
        if execution_recipe.get("native_command_id") != "cmdViewer3DViewOnto":
            raise UiaReplayError("Prepared Miller recipe must use native View Onto.")
        if list(execution_recipe.get("modifier_keys") or []) != []:
            raise UiaReplayError("Modifier keys are forbidden for Miller replay.")
        if execution_recipe.get("selection_method") != MILLER_PLANE_SELECTION_METHOD:
            raise UiaReplayError(
                "Local Miller replay requires fresh viewport-difference selection."
            )
        if execution_recipe.get("pre_action_view_baseline_required") is not True:
            raise UiaReplayError(
                "Prepared Miller recipe does not require the pre-action view baseline."
            )
        if execution_recipe.get("reset_view_allowed") is not False:
            raise UiaReplayError("Reset View must be forbidden for Miller replay.")
        raw_indices = execution_recipe.get("dialog_miller_indices")
        if (
            not isinstance(raw_indices, list)
            or len(raw_indices) != 3
            or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_indices)
            or not any(raw_indices)
        ):
            raise UiaReplayError(
                "Prepared Miller dialog indices must be three nonzero-plane integers."
            )
        dialog_text = " ".join(str(item) for item in raw_indices)
        if execution_recipe.get("dialog_miller_indices_text") != dialog_text:
            raise UiaReplayError("Prepared Miller dialog text is not canonical.")
        expected_label = "(" + "".join(str(item) for item in raw_indices) + ")"
        if execution_recipe.get("properties_miller_label") != expected_label:
            raise UiaReplayError("Prepared Miller Properties label differs.")
        transient = execution_recipe.get("transient_change_contract")
        if not isinstance(transient, dict) or transient.get(
            "required_undo_labels"
        ) != [MILLER_VIEW_ONTO_UNDO_LABEL, MILLER_CREATE_UNDO_LABEL]:
            raise UiaReplayError(
                "Prepared Miller cleanup must contain exactly View Onto then Create undo."
            )
        return {
            "recipe_kind": recipe_kind,
            "dialog_miller_indices": list(raw_indices),
            "dialog_miller_indices_text": dialog_text,
            "properties_miller_label": expected_label,
        }

    @staticmethod
    def _miller_replay_evidence(
        *,
        execution_recipe: dict[str, Any],
        structure_path: Path,
        structure_sha256: str,
        dialog_indices: list[int],
        dialog_text: str,
        aligned_path: Path,
        undo_labels: list[str],
    ) -> dict[str, Any]:
        direction_recipe = execution_recipe.get("recipe_kind") == (
            "crystal_direction_via_collinear_miller_plane_view_onto"
        )
        camera_contract = execution_recipe.get("camera_match_contract") or {}
        return {
            "miller_plane_indices": list(
                execution_recipe.get("miller_plane_indices") or dialog_indices
            ),
            "dialog_miller_indices": list(dialog_indices),
            "dialog_miller_indices_text_before_create": dialog_text,
            "dialog_miller_indices_value_source": (
                "fresh_modeless_child_accessibility_value"
            ),
            "dialog_miller_indices_verified_before_create": True,
            "created_plane_count": 1,
            "selected_plane_count": 1,
            "miller_plane_count_before": 0,
            "miller_plane_count_after_create": 1,
            "miller_plane_count_after_cleanup": 0,
            "selection_method": MILLER_PLANE_SELECTION_METHOD,
            "object_tree_path_suffix": None,
            "viewport_hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
            "fresh_before_after_screenshots_observed": True,
            "unique_transient_plane_region_observed": True,
            "properties_selection_verified": True,
            "view_onto_popup_menu_observed": False,
            "view_onto_native_command_mapping_verified": True,
            "dialog_show_set_of_parallel_planes": False,
            "dialog_show_symmetry_images": False,
            "properties_filter": "Miller Plane",
            "properties_miller_label": execution_recipe["properties_miller_label"],
            "camera_match_scope": camera_contract.get("scope"),
            "plane_normal_matches_manifest": True,
            "direct_lattice_direction_matches_manifest": (
                True if direction_recipe else None
            ),
            "analytic_in_plane_basis_matches_manifest": None,
            "native_in_plane_roll_policy_observed": True,
            "pre_action_view_baseline_captured": True,
            "reset_view_before_alignment": False,
            "screenshot_captured_before_cleanup": aligned_path.exists(),
            "document_was_clean_before_replay": True,
            "temporary_miller_plane_cleanup_verified": True,
            "no_temporary_miller_nodes_remaining": True,
            "document_clean_after_replay": True,
            "post_replay_view_restored": True,
            "structure_artifact_path": str(structure_path),
            "structure_artifact_sha256_before": structure_sha256,
            "structure_artifact_sha256_after": structure_sha256,
            "undo_labels_applied": list(undo_labels),
        }

    @staticmethod
    def _miller_runtime_ui_evidence(
        *,
        execution_recipe: dict[str, Any],
        expected_window_title: str,
        window_handle: int,
        expected_revision: int,
        structure_path: Path,
        structure_sha256: str,
        aligned_path: Path,
        undo_labels: list[str],
    ) -> dict[str, Any]:
        dialog_indices = list(execution_recipe["dialog_miller_indices"])
        return {
            "source": "local_uia",
            "expected_revision": expected_revision,
            "expected_window_handle": window_handle,
            "expected_window_title": expected_window_title,
            "reset_view_control_observed": False,
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
            "miller_planes_dialog_title": MILLER_PLANES_WINDOW_TITLE,
            "miller_planes_dialog_control_id": MILLER_PLANES_CONTROL_ID,
            "miller_indices_control_id": MILLER_INDICES_CONTROL_ID,
            "create_button_control_id": MILLER_CREATE_CONTROL_ID,
            "selection_modifier_keys": [],
            "viewport_selection_probe": {
                "selection_method": MILLER_PLANE_SELECTION_METHOD,
                "probe_miller_indices": list(
                    execution_recipe.get("miller_plane_indices") or dialog_indices
                ),
                "dialog_miller_indices": dialog_indices,
                "unique_transient_plane_visual_target_observed": True,
                "viewport_plane_selection_observed": True,
                "properties_selection_verified": True,
                "view_onto_popup_menu_observed": False,
                "view_onto_native_command_mapping_verified": True,
                "hit_test_basis": MILLER_PLANE_VIEWPORT_HIT_TEST_BASIS,
                "properties_filter": "Miller Plane",
                "properties_miller_label": execution_recipe[
                    "properties_miller_label"
                ],
                "view_onto_command_id": "cmdViewer3DViewOnto",
                "undo_labels_observed": list(undo_labels),
                "structure_artifact_path": str(structure_path),
                "structure_artifact_sha256_before": structure_sha256,
                "structure_artifact_sha256_after": structure_sha256,
            },
            "screenshot_path": str(aligned_path),
            "note": (
                "Server-generated transactional Miller-plane UIA evidence; the "
                "temporary plane and View Onto action were both undone."
            ),
        }

    def _focus_viewport_and_send_keys(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        key_sequence: list[str],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> list[str]:
        """Focus the unique viewer and send one allowlisted unmodified sequence."""

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
        sent: list[str] = []
        for key in key_sequence:
            self._require_foreground(window_handle)
            self._require_window_title(
                focused_snapshot["top"],
                expected_window_title=expected_window_title,
            )
            if _safe_call(viewport_wrapper, "has_keyboard_focus", False) is not True:
                raise UiaReplayError(
                    "The CViewer3DCtrl lost keyboard focus before input."
                )
            if self._keyboard_sender is None:
                raise UiaReplayError("Keyboard sender is unavailable.")
            self._keyboard_sender("{" + key.upper() + "}")
            sent.append(key)
            self._sleep(0.15)
        return sent

    def _execute_isometric_stages(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        keyboard_stages: list[dict[str, Any]],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        receipt: dict[str, Any],
    ) -> None:
        """Execute the reviewed two-angle isometric recipe and restore Movement."""

        stage_receipts: list[dict[str, Any]] = []
        receipt["keyboard_stages"] = stage_receipts
        receipt["movement_options_command_id"] = "cmdViewer3DMovementOptions"
        receipt["movement_angle_control_id"] = MOVEMENT_ANGLE_CONTROL_ID
        receipt["movement_screen_factor_control_id"] = (
            MOVEMENT_SCREEN_FACTOR_CONTROL_ID
        )
        stage_error: Exception | None = None
        stage_failure_phase: str | None = None
        try:
            for stage_index, stage in enumerate(keyboard_stages, start=1):
                receipt["failure_phase"] = f"movement_stage_{stage_index}_configure"
                configured = self._configure_movement_angle(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    execution_recipe=execution_recipe,
                    angle_degrees=float(
                        stage["rotation_increment_ui_display_degrees"]
                    ),
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                receipt["movement_command"] = configured["movement_command"]
                receipt.setdefault("movement_command_invocations", []).append(
                    configured["movement_command"]
                )
                receipt["movement_dialog_closed"] = True
                receipt["movement_screen_factor"] = configured[
                    "screen_factor_after"
                ]
                receipt["failure_phase"] = f"movement_stage_{stage_index}_keyboard"
                sent = self._focus_viewport_and_send_keys(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    key_sequence=list(stage["key_sequence"]),
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                receipt["keyboard_focus_verified"] = True
                receipt["key_sequence_sent"].extend(sent)
                stage_receipts.append(
                    {
                        "rotation_increment_degrees": float(
                            stage["rotation_increment_degrees"]
                        ),
                        "rotation_increment_ui_display_degrees": float(
                            stage["rotation_increment_ui_display_degrees"]
                        ),
                        "angle_readback_degrees": configured[
                            "angle_after"
                        ],
                        "screen_factor_readback": configured[
                            "screen_factor_after"
                        ],
                        "key_sequence": sent,
                        "modifier_keys": [],
                    }
                )
        except Exception as exc:
            stage_error = exc
            stage_failure_phase = str(receipt.get("failure_phase") or "") or None
            raise
        finally:
            try:
                receipt["failure_phase"] = "movement_restore"
                restored = self._configure_movement_angle(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    execution_recipe=execution_recipe,
                    angle_degrees=MOVEMENT_DEFAULT_ANGLE_DEGREES,
                    toolbar_contracts=toolbar_contracts,
                    command_labels=command_labels,
                )
                receipt["movement_command"] = restored["movement_command"]
                receipt.setdefault("movement_command_invocations", []).append(
                    restored["movement_command"]
                )
                receipt["rotation_increment_restored_degrees"] = restored[
                    "angle_after"
                ]
                receipt["movement_screen_factor"] = restored[
                    "screen_factor_after"
                ]
                receipt["movement_dialog_closed"] = True
                receipt["movement_restore_succeeded"] = True
            except Exception as restore_exc:
                receipt["movement_restore_succeeded"] = False
                receipt["movement_restore_error"] = str(restore_exc)
                receipt["manual_movement_restore_required"] = True
                if stage_error is None:
                    raise
            finally:
                if stage_error is not None and stage_failure_phase is not None:
                    receipt["failure_phase"] = stage_failure_phase

    def _configure_movement_angle(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        angle_degrees: float,
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> dict[str, Any]:
        """Open Movement, set/read one angle, preserve factor, and close it."""

        movement: Any | None = None
        result: dict[str, Any] = {}
        try:
            movement, controls, movement_command = self._open_movement_dialog(
                window_handle=window_handle,
                expected_window_title=expected_window_title,
                execution_recipe=execution_recipe,
                toolbar_contracts=toolbar_contracts,
                command_labels=command_labels,
            )
            result["movement_command"] = movement_command
            angle_before = self._read_numeric_control(
                controls["angle_parent"],
                controls["angle_edit"],
                field_name="angle",
            )
            factor_before = self._read_numeric_control(
                controls["factor_parent"],
                controls["factor_edit"],
                field_name="screen factor",
            )
            if not _numbers_match(
                factor_before,
                MOVEMENT_EXPECTED_SCREEN_FACTOR,
            ):
                raise UiaReplayError(
                    "Movement screen factor must remain 2.0 before replay."
                )
            display_text = self._movement_display_text(angle_degrees)
            controls["angle_edit"].iface_value.SetValue(display_text)
            self._sleep(0.3)
            angle_after = self._read_numeric_control(
                controls["angle_parent"],
                controls["angle_edit"],
                field_name="angle",
            )
            factor_after = self._read_numeric_control(
                controls["factor_parent"],
                controls["factor_edit"],
                field_name="screen factor",
            )
            if not _numbers_match(angle_after, angle_degrees):
                raise UiaReplayError(
                    "Movement angle readback did not match the requested value."
                )
            if not _numbers_match(
                factor_after,
                MOVEMENT_EXPECTED_SCREEN_FACTOR,
            ):
                raise UiaReplayError(
                    "Movement screen factor changed during angle configuration."
                )
            result.update(
                {
                    "angle_before": angle_before,
                    "angle_after": angle_after,
                    "angle_display_text": display_text,
                    "screen_factor_before": factor_before,
                    "screen_factor_after": factor_after,
                }
            )
            return result
        finally:
            if movement is not None:
                self._close_movement_dialog(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    movement=movement,
                )

    def _open_movement_dialog(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        """Open and bind the exact owned Movement window through InvokePattern."""

        self._require_foreground(window_handle)
        snapshot = self._inspect_window(
            window_handle=window_handle,
            expected_window_title=expected_window_title,
            toolbar_contracts=toolbar_contracts,
            command_labels=command_labels,
        )
        if snapshot["block_reasons"]:
            raise UiaReplayError(
                "Fresh UIA tree failed before Movement invocation: "
                + ", ".join(snapshot["block_reasons"])
            )
        movement_wrapper, command_receipt = self._resolve_movement_target(
            snapshot=snapshot,
            execution_recipe=execution_recipe,
            toolbar_contracts=toolbar_contracts,
            command_labels=command_labels,
        )
        movement_wrapper.invoke()
        self._sleep(0.5)
        top = snapshot["top"]
        self._require_window_title(
            top,
            expected_window_title=expected_window_title,
        )
        dialogs = [
            item
            for item in top.descendants()
            if str(_element_value(item, "name", "") or "")
            == MOVEMENT_WINDOW_TITLE
            and str(_element_value(item, "control_type", "") or "") == "Window"
            and _safe_call(item, "is_enabled", False) is True
            and _safe_call(item, "is_visible", False) is True
        ]
        if len(dialogs) != 1:
            for dialog in dialogs:
                try:
                    dialog.close()
                except Exception:
                    pass
            self._sleep(0.4)
            try:
                top.set_focus()
            except Exception:
                pass
            raise UiaReplayError(
                "The exact owned Movement window was not uniquely observed."
            )
        try:
            controls = self._inspect_movement_dialog(dialogs[0])
        except Exception:
            try:
                self._close_movement_dialog(
                    window_handle=window_handle,
                    expected_window_title=expected_window_title,
                    movement=dialogs[0],
                )
            except Exception:
                pass
            raise
        command_receipt["invocation_succeeded"] = True
        return dialogs[0], controls, command_receipt

    def _inspect_movement_dialog(self, movement: Any) -> dict[str, Any]:
        descendants = list(movement.descendants())
        options = [
            item
            for item in descendants
            if str(_element_value(item, "automation_id", "") or "")
            == MOVEMENT_OPTIONS_PANE_ID
            and str(_element_value(item, "control_type", "") or "") == "Pane"
            and _safe_call(item, "is_enabled", False) is True
            and _safe_call(item, "is_visible", False) is True
        ]
        if len(options) != 1:
            raise UiaReplayError(
                "MovementOptions pane was not uniquely observed."
            )

        def numeric_control(control_id: str) -> tuple[Any, Any]:
            parents = [
                item
                for item in options[0].descendants()
                if str(_element_value(item, "automation_id", "") or "")
                == control_id
                and str(_element_value(item, "control_type", "") or "")
                == "Pane"
                and _safe_call(item, "is_enabled", False) is True
                and _safe_call(item, "is_visible", False) is True
            ]
            if len(parents) != 1:
                raise UiaReplayError(
                    f"Movement {control_id} was not uniquely observed."
                )
            edits = [
                item
                for item in parents[0].children()
                if str(_element_value(item, "automation_id", "") or "")
                == MOVEMENT_NUMERIC_EDIT_ID
                and str(_element_value(item, "control_type", "") or "")
                == "Edit"
                and _safe_call(item, "is_enabled", False) is True
                and _safe_call(item, "is_visible", False) is True
            ]
            if len(edits) != 1:
                raise UiaReplayError(
                    f"Movement {control_id} TextCtrl was not uniquely observed."
                )
            try:
                edits[0].iface_value.CurrentValue
            except Exception as exc:
                raise UiaReplayError(
                    f"Movement {control_id} TextCtrl lacks ValuePattern."
                ) from exc
            return parents[0], edits[0]

        nudge_buttons = {
            str(_element_value(item, "automation_id", "") or ""): item
            for item in options[0].descendants()
            if str(_element_value(item, "control_type", "") or "") == "Button"
            and str(_element_value(item, "automation_id", "") or "").startswith(
                "cmdNudge"
            )
        }
        if set(nudge_buttons) != PROHIBITED_NUDGE_BUTTON_IDS:
            raise UiaReplayError(
                "Movement cmdNudge button inventory differs from the reviewed contract."
            )
        if any(
            _safe_call(item, "is_enabled", True) is True
            for item in nudge_buttons.values()
        ):
            raise UiaReplayError(
                "Movement cmdNudge buttons must all be disabled during camera replay."
            )
        angle_parent, angle_edit = numeric_control(MOVEMENT_ANGLE_CONTROL_ID)
        factor_parent, factor_edit = numeric_control(
            MOVEMENT_SCREEN_FACTOR_CONTROL_ID
        )
        return {
            "options": options[0],
            "angle_parent": angle_parent,
            "angle_edit": angle_edit,
            "factor_parent": factor_parent,
            "factor_edit": factor_edit,
            "disabled_nudge_button_ids": sorted(nudge_buttons),
        }

    def _read_numeric_control(
        self,
        parent: Any,
        edit: Any,
        *,
        field_name: str,
    ) -> float:
        edit_value = _numeric_text(
            edit.iface_value.CurrentValue,
            field_name=field_name,
        )
        parent_value = _numeric_text(
            _element_value(parent, "name", ""),
            field_name=f"{field_name} parent",
        )
        if not _numbers_match(edit_value, parent_value):
            raise UiaReplayError(
                f"Movement {field_name} edit and parent readbacks differ."
            )
        return edit_value

    def _close_movement_dialog(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        movement: Any,
    ) -> None:
        movement.close()
        self._sleep(0.4)
        if self._desktop_factory is None:
            raise UiaReplayError("UIA Desktop factory is unavailable.")
        top = self._desktop_factory(backend="uia").window(handle=window_handle)
        self._require_window_title(
            top,
            expected_window_title=expected_window_title,
        )
        remaining = [
            item
            for item in top.descendants()
            if str(_element_value(item, "name", "") or "")
            == MOVEMENT_WINDOW_TITLE
            and str(_element_value(item, "control_type", "") or "") == "Window"
            and _safe_call(item, "is_visible", False) is True
        ]
        if remaining:
            raise UiaReplayError("Movement dialog did not close cleanly.")
        top.set_focus()
        self._sleep(0.1)
        self._require_foreground(window_handle)

    @staticmethod
    def _movement_display_text(angle_degrees: float) -> str:
        if _numbers_match(angle_degrees, MOVEMENT_DEFAULT_ANGLE_DEGREES):
            return "45.0"
        if _numbers_match(angle_degrees, 35.264):
            return "35.264"
        raise UiaReplayError("Movement angle is not in the local isometric allowlist.")

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
        open_movement_dialogs = [
            item
            for item in descendants
            if str(_element_value(item, "name", "") or "")
            == MOVEMENT_WINDOW_TITLE
            and str(_element_value(item, "control_type", "") or "") == "Window"
            and _safe_call(item, "is_visible", False) is True
        ]
        if open_movement_dialogs:
            block_reasons.append("local_uia_movement_dialog_already_open")
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
            "movement_dialog_open": bool(open_movement_dialogs),
            "block_reasons": list(dict.fromkeys(block_reasons)),
            "command_labels": dict(command_labels),
        }

    def _validate_recipe(
        self, execution_recipe: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(execution_recipe, dict):
            raise UiaReplayError("Execution recipe must be a JSON object.")
        view_name = str(execution_recipe.get("view_name") or "")
        if view_name not in SAFE_LOCAL_VIEW_NAMES:
            raise UiaReplayError(
                "Local UIA execution supports only the six standard views and "
                "the reviewed isometric recipe."
            )
        if execution_recipe.get("automation_ready") is not True:
            raise UiaReplayError("Prepared view recipe is not automation-ready.")
        if execution_recipe.get("structure_mutation_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit structure mutation.")
        if execution_recipe.get("launch_new_matstudio_process_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit process launch.")
        if execution_recipe.get("blind_coordinate_action_allowed") is not False:
            raise UiaReplayError("Prepared recipe does not prohibit blind coordinates.")
        if execution_recipe.get("native_command_id") != "cmdViewer3DResetView":
            raise UiaReplayError("Local replay must begin with Reset View.")

        if view_name == "isometric":
            observed_stages = execution_recipe.get("keyboard_stages")
            if not isinstance(observed_stages, list) or len(observed_stages) != 2:
                raise UiaReplayError(
                    "Isometric replay requires exactly two reviewed keyboard stages."
                )
            normalized_stages: list[dict[str, Any]] = []
            for index, (observed, expected) in enumerate(
                zip(observed_stages, SAFE_ISOMETRIC_KEYBOARD_STAGES),
                start=1,
            ):
                if not isinstance(observed, dict):
                    raise UiaReplayError(
                        f"Isometric keyboard stage {index} must be an object."
                    )
                angle = float(observed.get("rotation_increment_degrees", 0))
                if abs(
                    angle - float(expected["rotation_increment_degrees"])
                ) > 1e-9:
                    raise UiaReplayError(
                        f"Isometric keyboard stage {index} angle differs from the allowlist."
                    )
                display_angle = float(
                    observed.get(
                        "rotation_increment_ui_display_degrees",
                        angle,
                    )
                )
                if not _numbers_match(
                    display_angle,
                    float(expected["rotation_increment_ui_display_degrees"]),
                ):
                    raise UiaReplayError(
                        f"Isometric keyboard stage {index} UI angle differs from the allowlist."
                    )
                keys = list(observed.get("key_sequence") or [])
                if keys != expected["key_sequence"]:
                    raise UiaReplayError(
                        f"Isometric keyboard stage {index} keys differ from the allowlist."
                    )
                if any(key not in SAFE_ARROW_KEYS for key in keys):
                    raise UiaReplayError(
                        "Prepared isometric sequence contains a non-arrow key."
                    )
                modifiers = list(observed.get("modifier_keys") or [])
                if modifiers != []:
                    raise UiaReplayError(
                        "Modifier keys are forbidden for isometric replay."
                    )
                normalized_stages.append(
                    {
                        "rotation_increment_degrees": angle,
                        "rotation_increment_ui_display_degrees": display_angle,
                        "key_sequence": keys,
                        "modifier_keys": [],
                    }
                )
            if not _numbers_match(
                float(execution_recipe.get("restore_rotation_increment_degrees", 0)),
                MOVEMENT_DEFAULT_ANGLE_DEGREES,
            ):
                raise UiaReplayError(
                    "Isometric replay must restore Movement angle to 45 degrees."
                )
            if (
                execution_recipe.get("movement_options_command_id")
                != "cmdViewer3DMovementOptions"
                or execution_recipe.get("movement_angle_control_id")
                != MOVEMENT_ANGLE_CONTROL_ID
                or execution_recipe.get("movement_screen_factor_control_id")
                != MOVEMENT_SCREEN_FACTOR_CONTROL_ID
            ):
                raise UiaReplayError(
                    "Isometric Movement command/control IDs differ from the reviewed contract."
                )
            if not _numbers_match(
                float(execution_recipe.get("movement_screen_factor_expected", 0)),
                MOVEMENT_EXPECTED_SCREEN_FACTOR,
            ):
                raise UiaReplayError(
                    "Isometric replay requires Movement screen factor 2.0."
                )
            if execution_recipe.get("movement_dialog_closed_after_restore") is not True:
                raise UiaReplayError(
                    "Isometric replay must close Movement after restoring settings."
                )
            movement_target = execution_recipe.get("movement_accessibility_target")
            if (
                not isinstance(movement_target, dict)
                or movement_target.get("command_id")
                != "cmdViewer3DMovementOptions"
                or movement_target.get("toolbar_name") != "3D Movement"
                or movement_target.get("angle_control_id")
                != MOVEMENT_ANGLE_CONTROL_ID
                or movement_target.get("screen_factor_control_id")
                != MOVEMENT_SCREEN_FACTOR_CONTROL_ID
            ):
                raise UiaReplayError(
                    "Prepared isometric Movement accessibility target is incomplete."
                )
            return {
                "view_name": view_name,
                "key_sequence": [],
                "keyboard_stages": normalized_stages,
            }

        if execution_recipe.get("keyboard_stages") is not None:
            raise UiaReplayError(
                "Staged keyboard recipes are allowed only for isometric replay."
            )
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
        return {
            "view_name": view_name,
            "key_sequence": observed_keys,
            "keyboard_stages": None,
        }

    def _resolve_toolbar_command_target(
        self,
        *,
        snapshot: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
        toolbar_name: str,
        command_id: str,
        expected_child_index: int,
    ) -> tuple[Any, dict[str, Any]]:
        """Resolve one control from the freshly inspected semantic toolbar."""

        toolbar = snapshot["toolbar_wrappers"].get(toolbar_name)
        toolbar_result = snapshot["toolbars"].get(toolbar_name)
        if toolbar is None or not isinstance(toolbar_result, dict):
            raise UiaReplayError(f"The exact {toolbar_name} toolbar is unavailable.")
        if toolbar_result.get("contract_verified") is not True:
            raise UiaReplayError(f"The {toolbar_name} toolbar contract is not verified.")
        entries = list(toolbar_contracts[toolbar_name].get("entries") or [])
        matching_indexes = [
            index
            for index, (kind, item_command_id) in enumerate(entries)
            if kind == "tool" and item_command_id == command_id
        ]
        if matching_indexes != [expected_child_index]:
            raise UiaReplayError(
                f"{command_id} is not at the reviewed {toolbar_name} toolbar position."
            )
        child_index = matching_indexes[0]
        children = list(toolbar.children())
        if child_index >= len(children):
            raise UiaReplayError(f"{command_id} toolbar child is unavailable.")
        wrapper = children[child_index]
        child = toolbar_result["children"][child_index]
        expected_name = command_labels[command_id]
        observed_name = child.get("observed_control_name")
        if observed_name not in {None, expected_name}:
            raise UiaReplayError(
                f"Fresh {command_id} control name is unexpected: {observed_name!r}."
            )
        if child.get("enabled") is not True:
            raise UiaReplayError(f"Fresh {command_id} control is disabled.")
        if child.get("visible") is not True:
            raise UiaReplayError(f"Fresh {command_id} control is not visible.")
        if child.get("invoke_supported") is not True:
            raise UiaReplayError(f"Fresh {command_id} control lacks InvokePattern.")
        target_kind = "named_control" if observed_name == expected_name else "verified_anonymous_toolbar_child"
        return wrapper, {
            "command_id": command_id,
            "target_kind": target_kind,
            "invocation_method": "local_uia_invoke_pattern",
            "toolbar_name": toolbar_name,
            "toolbar_automation_id": toolbar_result["toolbar_automation_id"],
            "registry_toolbar_name": toolbar_result["registry_toolbar_name"],
            "zero_based_child_index": child_index,
            "element_index": child["computer_use_compatible_element_index"],
            "observed_control_name": observed_name,
            "invoke_pattern_verified": True,
            "accessibility_tree_refreshed": True,
        }

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

    def _resolve_movement_target(
        self,
        *,
        snapshot: dict[str, Any],
        execution_recipe: dict[str, Any],
        toolbar_contracts: dict[str, dict[str, Any]],
        command_labels: dict[str, str],
    ) -> tuple[Any, dict[str, Any]]:
        toolbar_name = "3D Movement"
        command_id = "cmdViewer3DMovementOptions"
        toolbar = snapshot["toolbar_wrappers"].get(toolbar_name)
        toolbar_result = snapshot["toolbars"].get(toolbar_name)
        if toolbar is None or not isinstance(toolbar_result, dict):
            raise UiaReplayError("The exact 3D Movement toolbar is unavailable.")
        if toolbar_result.get("contract_verified") is not True:
            raise UiaReplayError("The 3D Movement toolbar contract is not verified.")
        entries = list(toolbar_contracts[toolbar_name].get("entries") or [])
        matching_indexes = [
            index
            for index, (kind, item_command_id) in enumerate(entries)
            if kind == "tool" and item_command_id == command_id
        ]
        if matching_indexes != [4]:
            raise UiaReplayError(
                "Movement Options is not at the reviewed toolbar position."
            )
        child_index = matching_indexes[0]
        children = list(toolbar.children())
        movement_wrapper = children[child_index]
        child = toolbar_result["children"][child_index]
        target = execution_recipe.get("movement_accessibility_target")
        if not isinstance(target, dict):
            raise UiaReplayError(
                "Prepared Movement Options accessibility target is missing."
            )
        if target.get("command_id") != command_id:
            raise UiaReplayError(
                "Prepared accessibility target is not Movement Options."
            )
        if target.get("toolbar_name") != toolbar_name:
            raise UiaReplayError(
                "Prepared Movement Options toolbar identity differs."
            )
        target_kind = str(target.get("target_kind") or "")
        expected_name = command_labels[command_id]
        if target_kind == "named_control":
            if target.get("control_name") != expected_name:
                raise UiaReplayError(
                    "Prepared named Movement Options label differs."
                )
            if child.get("observed_control_name") != expected_name:
                raise UiaReplayError(
                    "Fresh named Movement Options label differs."
                )
        elif target_kind == "verified_anonymous_toolbar_child":
            exact_fields = {
                "toolbar_automation_id": toolbar_result[
                    "toolbar_automation_id"
                ],
                "registry_toolbar_name": toolbar_result[
                    "registry_toolbar_name"
                ],
                "zero_based_child_index": child_index,
                "element_index": child[
                    "computer_use_compatible_element_index"
                ],
            }
            for field, expected in exact_fields.items():
                if target.get(field) != expected:
                    raise UiaReplayError(
                        "Fresh Movement Options target differs from prepared "
                        f"{field}."
                    )
            observed_name = child.get("observed_control_name")
            if observed_name not in {None, expected_name}:
                raise UiaReplayError(
                    "Fresh Movement Options control name is unexpected."
                )
        else:
            raise UiaReplayError(
                "Prepared Movement Options target kind is not allowlisted."
            )
        if child.get("enabled") is not True:
            raise UiaReplayError("Fresh Movement Options control is disabled.")
        if child.get("invoke_supported") is not True:
            raise UiaReplayError(
                "Fresh Movement Options control lacks InvokePattern."
            )
        return movement_wrapper, {
            "command_id": command_id,
            "target_kind": target_kind,
            "invocation_method": "local_uia_invoke_pattern",
            "toolbar_name": toolbar_name,
            "toolbar_automation_id": toolbar_result["toolbar_automation_id"],
            "registry_toolbar_name": toolbar_result[
                "registry_toolbar_name"
            ],
            "zero_based_child_index": child_index,
            "element_index": child["computer_use_compatible_element_index"],
            "observed_control_name": child.get("observed_control_name"),
            "registry_sha256": target.get("registry_sha256"),
            "semantic_mapping_sha256": target.get(
                "semantic_mapping_sha256"
            ),
            "invoke_pattern_verified": True,
            "accessibility_tree_refreshed": True,
            "invocation_succeeded": False,
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
