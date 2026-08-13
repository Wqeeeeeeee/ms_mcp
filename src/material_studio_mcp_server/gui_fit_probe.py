"""Bounded native Win32 preflight for the Materials Studio Fit-to-View target.

The probe is deliberately read-only.  It inspects one caller-supplied window
handle, enumerates native child HWNDs, and reads the exact 3D Viewer toolbar.
It never invokes a control, posts or sends an input command, or walks the UIA
descendant tree.  The command-line entry point exists so callers can impose a
hard process timeout around providers that stop responding.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import __version__
from .gui_uia import _default_toolbar_button_reader


VIEWER_TOOLBAR_CLASS = "ToolbarWindow32"
VIEWER_TOOLBAR_CONTROL_ID = 12122
VIEWER_TOOLBAR_TITLE = "3D Viewer"
VIEWPORT_CLASS = "CViewer3DCtrl"
MDI_CLIENT_CLASS = "MDIClient"
WM_MDIGETACTIVE = 0x0229
PROBE_SCHEMA_VERSION = 1
PROBE_KIND = "materials_studio_native_fit_target_probe"


def _probe_module_identity() -> dict[str, Any]:
    module_path = Path(__file__).resolve()
    dependency_module_path = Path(
        _default_toolbar_button_reader.__code__.co_filename
    ).resolve()
    return {
        "module_path": str(module_path),
        "module_sha256": sha256(module_path.read_bytes()).hexdigest(),
        "dependency_module_path": str(dependency_module_path),
        "dependency_module_sha256": sha256(
            dependency_module_path.read_bytes()
        ).hexdigest(),
        "package_version": __version__,
        "python_executable": str(Path(sys.executable).resolve()),
        "isolated_mode": bool(sys.flags.isolated),
        "no_site_mode": bool(sys.flags.no_site),
    }


def _windows_supported() -> bool:
    return os.name == "nt"


def _user32() -> Any:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    for name in ("IsWindowVisible", "IsWindowEnabled", "IsIconic"):
        function = getattr(user32, name)
        function.argtypes = (wintypes.HWND,)
        function.restype = wintypes.BOOL
    user32.GetDlgCtrlID.argtypes = (wintypes.HWND,)
    user32.GetDlgCtrlID.restype = ctypes.c_int
    user32.SendMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
    user32.IsChild.restype = wintypes.BOOL
    return user32


def _native_window_exists(window_handle: int) -> bool:
    return bool(_user32().IsWindow(ctypes.c_void_p(window_handle)))


def _native_window_text(window_handle: int) -> str:
    user32 = _user32()
    length = max(0, int(user32.GetWindowTextLengthW(ctypes.c_void_p(window_handle))))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(ctypes.c_void_p(window_handle), buffer, len(buffer))
    return buffer.value


def _native_window_class(window_handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    _user32().GetClassNameW(
        ctypes.c_void_p(window_handle), buffer, len(buffer)
    )
    return buffer.value


def _native_window_process_id(window_handle: int) -> int:
    process_id = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(
        ctypes.c_void_p(window_handle), ctypes.byref(process_id)
    )
    return int(process_id.value)


def _native_foreground_handle() -> int | None:
    handle = int(_user32().GetForegroundWindow() or 0)
    return handle or None


def _native_window_visible(window_handle: int) -> bool:
    return bool(_user32().IsWindowVisible(ctypes.c_void_p(window_handle)))


def _native_window_enabled(window_handle: int) -> bool:
    return bool(_user32().IsWindowEnabled(ctypes.c_void_p(window_handle)))


def _native_window_minimized(window_handle: int) -> bool:
    return bool(_user32().IsIconic(ctypes.c_void_p(window_handle)))


def _native_control_id(window_handle: int) -> int:
    return int(_user32().GetDlgCtrlID(ctypes.c_void_p(window_handle)))


def _native_active_mdi_document(mdi_client_handle: int) -> int | None:
    send_message = _user32().SendMessageW
    handle = int(
        send_message(ctypes.c_void_p(mdi_client_handle), WM_MDIGETACTIVE, 0, 0)
        or 0
    )
    return handle or None


def _native_is_child(parent_handle: int, child_handle: int) -> bool:
    return bool(
        _user32().IsChild(
            ctypes.c_void_p(parent_handle), ctypes.c_void_p(child_handle)
        )
    )


def _native_child_handles(window_handle: int) -> list[int]:
    """Return native descendant HWNDs without requesting a UIA tree."""

    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def collect(child_handle: int, _parameter: int) -> bool:
        handles.append(int(child_handle))
        return True

    callback = callback_type(collect)
    user32 = _user32()
    user32.EnumChildWindows.argtypes = (
        wintypes.HWND,
        callback_type,
        wintypes.LPARAM,
    )
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.EnumChildWindows(
        ctypes.c_void_p(window_handle), callback, 0
    )
    return handles


def _window_row(window_handle: int, *, foreground_handle: int | None) -> dict[str, Any]:
    return {
        "handle": int(window_handle),
        "title": _native_window_text(window_handle),
        "class_name": _native_window_class(window_handle),
        "process_id": _native_window_process_id(window_handle),
        "control_id": _native_control_id(window_handle),
        "visible": _native_window_visible(window_handle),
        "enabled": _native_window_enabled(window_handle),
        "minimized": _native_window_minimized(window_handle),
        "is_foreground": (
            foreground_handle is not None
            and int(foreground_handle) == int(window_handle)
        ),
    }


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def inspect_native_fit_target(
    *,
    window_handle: int,
    expected_window_title: str,
    toolbar_button_reader: Callable[[int], list[dict[str, Any]]] | None = None,
    foreground_handle_getter: Callable[[], int | None] | None = None,
    expected_window_pid: int | None = None,
    expected_document_name: str | None = None,
) -> dict[str, Any]:
    """Inspect the exact native Fit-to-View target without sending GUI input."""

    requested_handle = int(window_handle)
    expected_title = str(expected_window_title)
    result: dict[str, Any] = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "kind": PROBE_KIND,
        "probe_runtime": _probe_module_identity(),
        "supported": _windows_supported(),
        "read_only": True,
        "gui_input_performed": False,
        "window_handle": requested_handle,
        "expected_window_title": expected_title,
        "expected_window_pid": expected_window_pid,
        "expected_document_name": expected_document_name,
        "window": None,
        "mdi_client": None,
        "active_document": None,
        "active_viewport": None,
        "toolbar": None,
        "toolbar_candidates": [],
        "toolbar_buttons": [],
        "viewport_candidates": [],
        "block_reasons": [],
        "ok": False,
        "safe_for_fit_to_view_invoke": False,
    }
    reasons: list[str] = result["block_reasons"]
    if not result["supported"]:
        reasons.append("native_windows_probe_unavailable")
        return result
    if requested_handle <= 0:
        reasons.append("target_window_handle_invalid")
        return result
    try:
        if not _native_window_exists(requested_handle):
            reasons.append("target_window_not_found")
            return result

        foreground_getter = foreground_handle_getter or _native_foreground_handle
        foreground_handle = foreground_getter()
        window = _window_row(
            requested_handle, foreground_handle=foreground_handle
        )
        result["window"] = window
        if window["title"] != expected_title:
            reasons.append("target_window_title_mismatch")
        if window["is_foreground"] is not True:
            reasons.append("target_window_not_foreground")
        if window["visible"] is not True:
            reasons.append("target_window_not_visible")
        if window["enabled"] is not True:
            reasons.append("target_window_not_enabled")
        if window["minimized"] is True:
            reasons.append("target_window_minimized")
        if not str(window["class_name"] or "").strip():
            reasons.append("target_window_class_unavailable")
        if int(window["process_id"]) <= 0:
            reasons.append("target_window_process_unavailable")
        if (
            expected_window_pid is not None
            and window["process_id"] != int(expected_window_pid)
        ):
            reasons.append("target_window_process_mismatch")

        child_rows = [
            _window_row(child, foreground_handle=foreground_handle)
            for child in _native_child_handles(requested_handle)
            if _native_window_exists(child)
        ]
        mdi_candidates = [
            row
            for row in child_rows
            if row["class_name"] == MDI_CLIENT_CLASS
            and row["process_id"] == window["process_id"]
        ]
        if len(mdi_candidates) != 1:
            reasons.append("native_mdi_client_identity_not_unique")
        else:
            mdi_client = dict(mdi_candidates[0])
            result["mdi_client"] = mdi_client
            if mdi_client["visible"] is not True:
                reasons.append("native_mdi_client_not_visible")
            if mdi_client["enabled"] is not True:
                reasons.append("native_mdi_client_not_enabled")

            active_document_handle = _native_active_mdi_document(
                int(mdi_client["handle"])
            )
            if (
                active_document_handle is None
                or not _native_window_exists(active_document_handle)
                or not _native_is_child(
                    int(mdi_client["handle"]), active_document_handle
                )
            ):
                reasons.append("native_active_document_unavailable")
            else:
                active_document = _window_row(
                    active_document_handle,
                    foreground_handle=foreground_handle,
                )
                result["active_document"] = active_document
                if active_document["process_id"] != window["process_id"]:
                    reasons.append("native_active_document_process_mismatch")
                if active_document["visible"] is not True:
                    reasons.append("native_active_document_not_visible")
                if active_document["enabled"] is not True:
                    reasons.append("native_active_document_not_enabled")
                if expected_document_name is not None:
                    exact_name = str(expected_document_name)
                    allowed_titles = {
                        exact_name,
                        f"{exact_name}*",
                        f"{exact_name} *",
                    }
                    if active_document["title"] not in allowed_titles:
                        reasons.append("native_active_document_title_mismatch")

                viewport_candidates = [
                    _window_row(child, foreground_handle=foreground_handle)
                    for child in _native_child_handles(active_document_handle)
                    if _native_window_exists(child)
                    and _native_window_class(child) == VIEWPORT_CLASS
                ]
                result["viewport_candidates"] = viewport_candidates
                ready_viewports = [
                    row
                    for row in viewport_candidates
                    if row["process_id"] == window["process_id"]
                    and row["visible"] is True
                    and row["enabled"] is True
                ]
                if len(ready_viewports) != 1:
                    reasons.append("native_active_viewport_identity_not_unique")
                else:
                    result["active_viewport"] = dict(ready_viewports[0])

        toolbar_candidates = [
            row
            for row in child_rows
            if row["class_name"] == VIEWER_TOOLBAR_CLASS
            and row["control_id"] == VIEWER_TOOLBAR_CONTROL_ID
            and row["title"] == VIEWER_TOOLBAR_TITLE
            and row["process_id"] == window["process_id"]
        ]
        result["toolbar_candidates"] = toolbar_candidates
        if len(toolbar_candidates) != 1:
            reasons.append("native_3d_viewer_toolbar_identity_not_unique")
        else:
            toolbar = dict(toolbar_candidates[0])
            result["toolbar"] = toolbar
            if toolbar["visible"] is not True:
                reasons.append("native_3d_viewer_toolbar_not_visible")
            if toolbar["enabled"] is not True:
                reasons.append("native_3d_viewer_toolbar_not_enabled")

        if reasons:
            result["block_reasons"] = _unique_strings(reasons)
            return result

        reader = toolbar_button_reader or _default_toolbar_button_reader
        rows = reader(int(result["toolbar"]["handle"]))
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) for row in rows
        ):
            reasons.append("native_3d_viewer_toolbar_button_rows_invalid")
        else:
            result["toolbar_buttons"] = [dict(row) for row in rows]
    except Exception as exc:
        reasons.append("native_fit_target_probe_failed")
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)

    result["block_reasons"] = _unique_strings(reasons)
    result["ok"] = not result["block_reasons"]
    result["safe_for_fit_to_view_invoke"] = result["ok"]
    return result


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only native Materials Studio Fit-to-View target probe"
    )
    parser.add_argument("--window-handle", type=int, required=True)
    parser.add_argument("--expected-window-title", required=True)
    parser.add_argument("--expected-window-pid", type=int)
    parser.add_argument("--expected-document-name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    result = inspect_native_fit_target(
        window_handle=args.window_handle,
        expected_window_title=args.expected_window_title,
        expected_window_pid=args.expected_window_pid,
        expected_document_name=args.expected_document_name,
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
