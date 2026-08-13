from __future__ import annotations

import ctypes
import importlib
import hashlib
import os
import struct
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
import material_studio_mcp_server.gui_uia as gui_uia_module

from material_studio_mcp_server.gui import (
    VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
)
from material_studio_mcp_server.gui_uia import (
    COMTYPES_CACHE_ENV,
    FIT_TO_VIEW_COMMAND_ID,
    FIT_TO_VIEW_CONTROL_NAME,
    FIT_TO_VIEW_TOOLBAR_NAME,
    FitProbeCleanupError,
    FitProbeTimeoutError,
    PywinautoViewReplayBackend,
    UiaReplayError,
    VIEWER_TOOLBAR_NATIVE_COMMAND_IDS,
    VIEWER_TOOLBAR_NATIVE_STYLES,
    _default_bounded_fit_probe_runner,
    _default_native_window_identity,
    _external_comtypes_gen_cache,
    _fit_probe_runtime_paths,
    _launch_fit_probe_process,
    _WindowsKillOnCloseJob,
    analyze_miller_plane_bmp_diff,
    compare_bmp_region,
    local_uia_view_replay_implementation_contract,
)


def test_external_comtypes_cache_binds_generated_package_outside_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "comtypes-generated"
    fake_comtypes = types.ModuleType("comtypes")
    fake_comtypes.__path__ = []
    cache.mkdir()
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.delitem(sys.modules, "comtypes.client", raising=False)
    monkeypatch.delitem(sys.modules, "comtypes.gen", raising=False)
    monkeypatch.setenv(COMTYPES_CACHE_ENV, str(cache))

    meta_path_before = list(sys.meta_path)
    with _external_comtypes_gen_cache() as observed:
        assert observed == cache.resolve()
        assert cache.is_dir()
        assert len(sys.meta_path) == len(meta_path_before) + 1
        assert getattr(sys.meta_path[0], "cache_dir", None) == cache.resolve()
        generated = importlib.import_module("comtypes.gen")
        assert generated.__path__ == [str(cache.resolve())]
        assert list(generated.__spec__.submodule_search_locations) == [
            str(cache.resolve())
        ]
        assert "comtypes.client" not in sys.modules
    assert sys.meta_path == meta_path_before


def test_external_comtypes_cache_rejects_late_client_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "expected-cache"
    fake_comtypes = types.ModuleType("comtypes")
    fake_client = types.ModuleType("comtypes.client")
    fake_client.gen_dir = str(tmp_path / "wrong-cache")
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)
    monkeypatch.delitem(sys.modules, "comtypes.gen", raising=False)
    monkeypatch.setenv(COMTYPES_CACHE_ENV, str(cache))
    cache.mkdir()

    with pytest.raises(RuntimeError, match="imported before"):
        with _external_comtypes_gen_cache():
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="pywinauto UIA is Windows-only")
def test_external_comtypes_cache_preserves_pywinauto_mta_and_runtime(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "real-comtypes-generated"
    cache.mkdir()
    code = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["MS_MCP_TEST_SRC"])

from material_studio_mcp_server.gui_uia import (
    COMTYPES_CACHE_ENV,
    PywinautoViewReplayBackend,
)

cache = Path(os.environ[COMTYPES_CACHE_ENV]).resolve()
backend = PywinautoViewReplayBackend()
assert backend.supported is True, backend.unavailable_reason
import comtypes.client
assert sys.coinit_flags == 0, sys.coinit_flags
assert Path(comtypes.client.gen_dir).resolve() == cache
assert [Path(item).resolve() for item in comtypes.gen.__path__] == [cache]
generated = sorted(path.name for path in cache.glob("*.py"))
assert "UIAutomationClient.py" in generated, generated
assert "stdole.py" in generated, generated
assert not list(cache.rglob("*.pyc"))
'''
    env = os.environ.copy()
    env[COMTYPES_CACHE_ENV] = str(cache)
    env["MS_MCP_TEST_SRC"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-I", "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _write_rgb_bmp(
    path: Path,
    *,
    width: int,
    height: int,
    pixels: dict[tuple[int, int], tuple[int, int, int]],
) -> None:
    row_stride = ((width * 24 + 31) // 32) * 4
    rows = bytearray(row_stride * height)
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels.get((x, y), (248, 248, 248))
            offset = y * row_stride + x * 3
            rows[offset : offset + 3] = bytes((blue, green, red))
    pixel_offset = 54
    image_size = len(rows)
    header = struct.pack(
        "<2sIHHI",
        b"BM",
        pixel_offset + image_size,
        0,
        0,
        pixel_offset,
    )
    dib = struct.pack(
        "<IiiHHIIiiII",
        40,
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
    path.write_bytes(header + dib + rows)


@dataclass
class _FakeElement:
    runtime_id: tuple[int, ...]

    def GetRuntimeId(self) -> tuple[int, ...]:
        return self.runtime_id


class _FakeElementInfo:
    def __init__(
        self,
        *,
        runtime_id: int,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
        class_name: str = "",
    ) -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.class_name = class_name
        self.element = _FakeElement((runtime_id,))


@dataclass
class _FakeRectangle:
    left: int
    top: int
    right: int
    bottom: int


class _FakeRectWrapper:
    def __init__(
        self,
        rect: tuple[int, int, int, int],
        *,
        parent: "_FakeRectWrapper | None" = None,
        handle: int = 0,
        name: str = "",
        control_type: str = "Pane",
        class_name: str = "",
    ) -> None:
        self._rect = _FakeRectangle(*rect)
        self._parent = parent
        self.handle = handle
        self.element_info = _FakeElementInfo(
            runtime_id=handle or id(self),
            name=name,
            control_type=control_type,
            class_name=class_name,
        )

    def rectangle(self) -> _FakeRectangle:
        return self._rect

    def parent(self) -> "_FakeRectWrapper | None":
        return self._parent

    def is_visible(self) -> bool:
        return True


class _FakeValuePattern:
    def __init__(self, wrapper: "_FakeWrapper", value: str) -> None:
        self.wrapper = wrapper
        self.CurrentValue = value

    def SetValue(self, value: str) -> None:
        self.CurrentValue = str(value)
        self.wrapper.value_history.append(str(value))
        if self.wrapper._parent is not None:
            self.wrapper._parent.element_info.name = str(value)


class _FakeWrapper:
    def __init__(
        self,
        *,
        runtime_id: int,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
        class_name: str = "",
        enabled: bool = True,
        visible: bool = True,
        focusable: bool = False,
        acquire_focus: bool = True,
        invoke_supported: bool = False,
        value: str | None = None,
        on_invoke: object | None = None,
        on_close: object | None = None,
    ) -> None:
        self.element_info = _FakeElementInfo(
            runtime_id=runtime_id,
            name=name,
            control_type=control_type,
            automation_id=automation_id,
            class_name=class_name,
        )
        self._children: list[_FakeWrapper] = []
        self._parent: _FakeWrapper | None = None
        self._enabled = enabled
        self._visible = visible
        self._focusable = focusable
        self._acquire_focus = acquire_focus
        self._focused = False
        self._invoke_supported = invoke_supported
        self._value_pattern = (
            _FakeValuePattern(self, value) if value is not None else None
        )
        self._on_invoke = on_invoke
        self._on_close = on_close
        self.invoke_count = 0
        self.focus_count = 0
        self.close_count = 0
        self.value_history: list[str] = []

    @property
    def iface_invoke(self) -> object:
        if not self._invoke_supported:
            raise RuntimeError("InvokePattern unavailable")
        return object()

    @property
    def iface_value(self) -> _FakeValuePattern:
        if self._value_pattern is None:
            raise RuntimeError("ValuePattern unavailable")
        return self._value_pattern

    def children(self) -> list["_FakeWrapper"]:
        return list(self._children)

    def descendants(self) -> list["_FakeWrapper"]:
        result: list[_FakeWrapper] = []
        for child in self._children:
            result.append(child)
            result.extend(child.descendants())
        return result

    def add_child(self, child: "_FakeWrapper") -> "_FakeWrapper":
        child._parent = self
        self._children.append(child)
        return child

    def is_enabled(self) -> bool:
        return self._enabled

    def is_visible(self) -> bool:
        return self._visible

    def is_keyboard_focusable(self) -> bool:
        return self._focusable

    def has_keyboard_focus(self) -> bool:
        return self._focused

    def set_focus(self) -> "_FakeWrapper":
        self.focus_count += 1
        if self._acquire_focus:
            self._focused = True
        return self

    def invoke(self) -> "_FakeWrapper":
        if not self._invoke_supported:
            raise RuntimeError("InvokePattern unavailable")
        self.invoke_count += 1
        if callable(self._on_invoke):
            self._on_invoke()
        return self

    def close(self) -> None:
        self.close_count += 1
        if callable(self._on_close):
            self._on_close()


class _FakeTop(_FakeWrapper):
    def __init__(self, *, title: str, descendants: list[_FakeWrapper]) -> None:
        super().__init__(
            runtime_id=1,
            name=title,
            control_type="Window",
            class_name="MaterialsStudioMainWindow",
        )
        self._descendants = descendants
        self._movement_dialog: _FakeWrapper | None = None
        self._movement_open = False

    def descendants(self) -> list[_FakeWrapper]:
        result = list(self._descendants)
        if self._movement_open and self._movement_dialog is not None:
            result.append(self._movement_dialog)
            result.extend(self._movement_dialog.descendants())
        return result


class _FakeDesktop:
    def __init__(self, top: _FakeTop) -> None:
        self.top = top

    def window(self, *, handle: int) -> _FakeTop:
        assert handle == 9001
        return self.top


def _build_tree(
    *,
    title: str = "project - Materials Studio",
    viewport_acquires_focus: bool = True,
) -> tuple[_FakeTop, _FakeWrapper, _FakeWrapper]:
    runtime_id = 10

    def next_id() -> int:
        nonlocal runtime_id
        runtime_id += 1
        return runtime_id

    viewport = _FakeWrapper(
        runtime_id=next_id(),
        control_type="Pane",
        automation_id="748125624",
        class_name="CViewer3DCtrl",
        focusable=True,
        acquire_focus=viewport_acquires_focus,
    )
    movement = _FakeWrapper(
        runtime_id=next_id(),
        name="3D Movement",
        control_type="ToolBar",
        automation_id="12134",
    )
    for kind, _command_id in VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
        "3D Movement"
    ]["entries"]:
        movement.add_child(
            _FakeWrapper(
                runtime_id=next_id(),
                control_type="Separator" if kind == "separator" else "CheckBox",
                enabled=kind != "separator",
                invoke_supported=kind != "separator",
            )
        )
    viewer = _FakeWrapper(
        runtime_id=next_id(),
        name="3D Viewer",
        control_type="ToolBar",
        automation_id="12122",
    )
    reset: _FakeWrapper | None = None
    for kind, command_id in VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
        "3D Viewer"
    ]["entries"]:
        child = _FakeWrapper(
            runtime_id=next_id(),
            name=(
                "3D Viewer Reset View"
                if command_id == "cmdViewer3DResetView"
                else ""
            ),
            control_type="Separator" if kind == "separator" else "CheckBox",
            enabled=kind != "separator",
            invoke_supported=kind != "separator",
        )
        viewer.add_child(child)
        if command_id == "cmdViewer3DResetView":
            reset = child
    assert reset is not None
    descendants = [
        viewport,
        movement,
        *movement._children,
        viewer,
        *viewer._children,
    ]
    top = _FakeTop(title=title, descendants=descendants)
    movement_dialog = _FakeWrapper(
        runtime_id=next_id(),
        name="Movement",
        control_type="Window",
        class_name="MaterialsStudioMovementWindow",
        enabled=True,
        visible=True,
        focusable=True,
    )
    movement_options = movement_dialog.add_child(
        _FakeWrapper(
            runtime_id=next_id(),
            control_type="Pane",
            automation_id="MovementOptions",
            enabled=True,
            visible=True,
            focusable=True,
        )
    )
    for command_id in (
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
    ):
        movement_options.add_child(
            _FakeWrapper(
                runtime_id=next_id(),
                control_type="Button",
                automation_id=command_id,
                enabled=False,
                visible=True,
                focusable=True,
            )
        )

    def numeric_control(control_id: str, value: str) -> _FakeWrapper:
        parent = movement_options.add_child(
            _FakeWrapper(
                runtime_id=next_id(),
                name=value,
                control_type="Pane",
                automation_id=control_id,
                enabled=True,
                visible=True,
                focusable=True,
            )
        )
        parent.add_child(
            _FakeWrapper(
                runtime_id=next_id(),
                control_type="Pane",
                automation_id=str(next_id()),
                class_name="UpDown20WndClass",
                enabled=True,
                visible=True,
            )
        )
        return parent.add_child(
            _FakeWrapper(
                runtime_id=next_id(),
                control_type="Edit",
                automation_id="TextCtrl",
                value=value,
                enabled=True,
                visible=True,
                focusable=True,
            )
        )

    angle_edit = numeric_control("numNudgeAngle", "45.0")
    factor_edit = numeric_control("numNudgeFactor", "2.0")
    top._movement_dialog = movement_dialog
    movement_dialog._on_close = lambda: setattr(top, "_movement_open", False)
    movement._children[4]._on_invoke = lambda: setattr(
        top,
        "_movement_open",
        True,
    )
    top.movement_angle_edit = angle_edit
    top.movement_factor_edit = factor_edit
    top.movement_button = movement._children[4]
    top.movement_dialog = movement_dialog
    return top, viewport, reset


def _backend(
    top: _FakeTop,
    *,
    sent_keys: list[str],
    foreground_handle: int | None = 9001,
) -> PywinautoViewReplayBackend:
    return PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=sent_keys.append,
        foreground_handle_getter=lambda: foreground_handle,
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )


def test_local_uia_implementation_contract_exposes_transactional_recipe_boundary() -> None:
    contract = local_uia_view_replay_implementation_contract()
    recipe_classes = contract["recipe_classes"]

    assert contract["default_execution_mode"] == "preview"
    assert contract["explicit_execute_required"] is True
    assert contract["records_visual_acceptance"] is False
    assert recipe_classes["fit_to_view"] == {
        "implemented": True,
        "execute_tool": "material_studio_gui_fit_to_view",
        "requires_bounded_native_probe": True,
        "probe_process_timeout_seconds": 30.0,
        "requires_exact_window_pid_document_and_viewport": True,
        "requires_full_live_toolbar_mapping": True,
        "numeric_command_id": 33299,
        "native_command_timeout_milliseconds": 5000,
        "requires_immediate_pre_dispatch_native_window_identity": True,
        "requires_single_native_materials_studio_process_and_window": True,
        "registry_sha256_verified_after_final_proof_gate": True,
        "uses_uia_descendant_tree": False,
    }
    assert recipe_classes["transactional_miller_plane"] == {
        "implemented": True,
        "recipe_kind": "miller_plane_view_onto",
        "view_name_pattern": "crystal_plane_*",
        "requires_automation_ready_recipe": True,
        "requires_current_bound_runtime_ui_preflight": True,
        "runtime_gate": "safe_for_miller_plane_transaction",
        "requires_exact_viewport_restoration": True,
        "requires_structure_sha256_unchanged": True,
        "requires_post_action_visual_confirmation": True,
    }
    assert recipe_classes["exact_collinear_crystal_direction"][
        "eligibility_status"
    ] == "exact_integer_plane_collinear"
    assert recipe_classes["exact_collinear_crystal_direction"]["implemented"] is True
    assert recipe_classes["non_collinear_crystal_direction"] == {
        "implemented": False,
        "reviewed_camera_backend_required": True,
    }
    assert contract["miller_recipe_kinds"] == [
        "crystal_direction_via_collinear_miller_plane_view_onto",
        "miller_plane_view_onto",
    ]


def _native_fit_probe_payload(
    *,
    title: str = "project - Materials Studio",
) -> dict[str, object]:
    rows = [
        {
            "index": index,
            "command_id": command_id,
            "style": VIEWER_TOOLBAR_NATIVE_STYLES[index],
            "state": 0 if index == 4 else 4,
            "text": "",
        }
        for index, command_id in enumerate(VIEWER_TOOLBAR_NATIVE_COMMAND_IDS)
    ]
    runtime = _fit_probe_runtime_paths()
    window = {
        "handle": 9001,
        "title": title,
        "class_name": "MaterialsStudioMainWindow",
        "process_id": 7001,
        "is_foreground": True,
        "visible": True,
        "enabled": True,
        "minimized": False,
    }
    toolbar = {
        "handle": 9101,
        "control_id": 12122,
        "class_name": "ToolbarWindow32",
        "title": "3D Viewer",
        "process_id": 7001,
        "visible": True,
        "enabled": True,
    }
    viewport = {
        "handle": 9301,
        "class_name": "CViewer3DCtrl",
        "process_id": 7001,
        "visible": True,
        "enabled": True,
    }
    return {
        "schema_version": 1,
        "kind": "materials_studio_native_fit_target_probe",
        "probe_runtime": {
            "module_path": str(runtime["helper_path"]),
            "module_sha256": runtime["helper_sha256"],
            "dependency_module_path": str(runtime["dependency_module_path"]),
            "dependency_module_sha256": runtime["dependency_module_sha256"],
            "package_version": "0.5.2",
            "python_executable": str(runtime["base_executable"]),
            "isolated_mode": True,
            "no_site_mode": True,
        },
        "supported": True,
        "read_only": True,
        "gui_input_performed": False,
        "ok": True,
        "safe_for_fit_to_view_invoke": True,
        "window_handle": 9001,
        "expected_window_title": "project - Materials Studio",
        "expected_window_pid": 7001,
        "expected_document_name": "structure_r004",
        "window": window,
        "mdi_client": {
            "handle": 9151,
            "class_name": "MDIClient",
            "process_id": 7001,
            "visible": True,
            "enabled": True,
        },
        "toolbar": toolbar,
        "toolbar_candidates": [dict(toolbar)],
        "toolbar_buttons": rows,
        "active_document": {
            "handle": 9201,
            "title": "structure_r004 *",
            "process_id": 7001,
            "visible": True,
            "enabled": True,
        },
        "active_viewport": viewport,
        "viewport_candidates": [dict(viewport)],
        "block_reasons": [],
        "helper_exit_code": 0,
    }


def _native_window_identity_payload(**overrides: object) -> dict[str, object]:
    return {
        "is_window": True,
        "handle": 9001,
        "pid": 7001,
        "title": "project - Materials Studio",
        "is_foreground": True,
        "is_visible": True,
        "is_enabled": True,
        "is_minimized": False,
        "session_enumeration_succeeded": True,
        "process_count": 1,
        "window_count": 1,
        "materials_studio_process_ids": [7001],
        "materials_studio_window_handles": [9001],
        "target_pid_is_materials_studio": True,
        "target_window_is_materials_studio": True,
        **overrides,
    }


@pytest.mark.skipif(os.name != "nt", reason="Win32 native identity only")
def test_native_window_identity_uses_pointer_sized_explicit_win32_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes.wintypes as wintypes

    high_handle = 0x123456789ABC
    title = "project - Materials Studio"

    class _Function:
        def __init__(self, callback: object) -> None:
            self.callback = callback
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *args: object) -> object:
            return self.callback(*args)

    class _User32:
        def __init__(self) -> None:
            self.IsWindow = _Function(lambda hwnd: int(hwnd.value) == high_handle)

            def set_pid(_hwnd: object, output: object) -> int:
                ctypes.cast(
                    output, ctypes.POINTER(wintypes.DWORD)
                ).contents.value = 7001
                return 1

            self.GetWindowThreadProcessId = _Function(set_pid)
            self.GetWindowTextLengthW = _Function(lambda _hwnd: len(title))

            def set_title(_hwnd: object, buffer: object, _length: int) -> int:
                buffer.value = title
                return len(title)

            self.GetWindowTextW = _Function(set_title)
            self.GetForegroundWindow = _Function(lambda: high_handle)
            self.IsWindowVisible = _Function(lambda _hwnd: 1)
            self.IsWindowEnabled = _Function(lambda _hwnd: 1)
            self.IsIconic = _Function(lambda _hwnd: 0)
            self.EnumWindows = _Function(
                lambda callback, lparam: int(bool(callback(high_handle, lparam)))
            )

    class _Kernel32:
        def __init__(self) -> None:
            self.CreateToolhelp32Snapshot = _Function(lambda _flags, _pid: 55)

            def first(_snapshot: object, output: object) -> int:
                entry = output._obj
                entry.th32ProcessID = 7001
                entry.szExeFile = "MatStudio.exe"
                return 1

            def next_entry(_snapshot: object, _output: object) -> int:
                ctypes.set_last_error(18)
                return 0

            self.Process32FirstW = _Function(first)
            self.Process32NextW = _Function(next_entry)
            self.CloseHandle = _Function(lambda _handle: 1)

    user32 = _User32()
    kernel32 = _Kernel32()
    monkeypatch.setattr(
        gui_uia_module.ctypes,
        "WinDLL",
        lambda name, **_kwargs: (
            user32 if str(name).casefold() == "user32" else kernel32
        ),
    )

    result = _default_native_window_identity(high_handle)

    assert result == {
        "is_window": True,
        "handle": high_handle,
        "pid": 7001,
        "title": title,
        "is_foreground": True,
        "is_visible": True,
        "is_enabled": True,
        "is_minimized": False,
        "session_enumeration_succeeded": True,
        "process_count": 1,
        "window_count": 1,
        "materials_studio_process_ids": [7001],
        "materials_studio_window_handles": [high_handle],
        "target_pid_is_materials_studio": True,
        "target_window_is_materials_studio": True,
    }
    assert user32.GetForegroundWindow.argtypes == ()
    assert user32.GetForegroundWindow.restype is wintypes.HWND
    assert user32.IsWindow.argtypes == (wintypes.HWND,)
    assert user32.GetWindowThreadProcessId.argtypes == (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    assert user32.GetWindowTextW.argtypes == (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    assert user32.GetWindowTextLengthW.argtypes == (wintypes.HWND,)
    assert user32.GetWindowTextLengthW.restype is ctypes.c_int
    assert user32.GetWindowTextW.restype is ctypes.c_int
    assert user32.GetWindowThreadProcessId.restype is wintypes.DWORD
    assert user32.IsWindow.restype is wintypes.BOOL
    assert user32.IsWindowVisible.argtypes == (wintypes.HWND,)
    assert user32.IsWindowVisible.restype is wintypes.BOOL
    assert user32.IsWindowEnabled.argtypes == (wintypes.HWND,)
    assert user32.IsWindowEnabled.restype is wintypes.BOOL
    assert user32.IsIconic.argtypes == (wintypes.HWND,)
    assert user32.IsIconic.restype is wintypes.BOOL
    assert len(user32.EnumWindows.argtypes) == 2
    assert user32.EnumWindows.argtypes[1] is wintypes.LPARAM
    assert user32.EnumWindows.restype is wintypes.BOOL
    assert kernel32.CreateToolhelp32Snapshot.argtypes == (
        wintypes.DWORD,
        wintypes.DWORD,
    )
    assert kernel32.CreateToolhelp32Snapshot.restype is wintypes.HANDLE
    process_entry_pointer = kernel32.Process32FirstW.argtypes[1]
    assert kernel32.Process32FirstW.argtypes == (
        wintypes.HANDLE,
        process_entry_pointer,
    )
    assert kernel32.Process32FirstW.restype is wintypes.BOOL
    assert kernel32.Process32NextW.argtypes == (
        wintypes.HANDLE,
        process_entry_pointer,
    )
    assert kernel32.Process32NextW.restype is wintypes.BOOL
    assert kernel32.CloseHandle.argtypes == (wintypes.HANDLE,)
    assert kernel32.CloseHandle.restype is wintypes.BOOL


def _native_fit_backend(
    *,
    runner: object,
    native_commands: list[tuple[int, int]],
) -> PywinautoViewReplayBackend:
    return PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fit must not enumerate the UIA descendant tree")
        ),
        keyboard_sender=lambda _token: (_ for _ in ()).throw(
            AssertionError("Fit must not send keyboard input")
        ),
        foreground_handle_getter=lambda: 9001,
        native_window_identity_getter=lambda _handle: (
            _native_window_identity_payload()
        ),
        fit_command_sender=lambda handle, command: native_commands.append(
            (handle, command)
        ),
        fit_probe_runner=runner,
        fit_probe_timeout_seconds=0.25,
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )


def _native_registry(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "#SVViewer3d.xml"
    path.write_text("<toolbar/>", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_fit_probe_uses_bounded_native_mapping_without_uia_tree() -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return _native_fit_probe_payload()

    backend = _native_fit_backend(runner=runner, native_commands=[])
    result = backend.probe_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["fit_command_ready"] is True
    assert result["live_toolbar_mapping_verified"] is True
    assert result["resolved_command_ids"] == [FIT_TO_VIEW_COMMAND_ID]
    assert result["accessibility_tree_enumerated"] is False
    assert result["fit_command"]["numeric_command_id"] == 33299
    assert calls == [
        {
            "window_handle": 9001,
            "expected_window_title": "project - Materials Studio",
            "expected_window_pid": 7001,
            "expected_document_name": "structure_r004",
            "timeout_seconds": 0.25,
        }
    ]


def test_fit_probe_timeout_fails_closed_without_input() -> None:
    def runner(**_kwargs: object) -> dict[str, object]:
        raise FitProbeTimeoutError("deadline")

    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=runner,
        native_commands=native_commands,
    )
    result = backend.probe_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["fit_command_ready"] is False
    assert result["probe_timed_out"] is True
    assert result["block_reasons"] == ["native_fit_probe_timed_out"]
    assert result["gui_input_performed"] is False
    assert native_commands == []


def test_fit_probe_requires_exact_pid_and_document_before_helper() -> None:
    calls: list[dict[str, object]] = []
    backend = _native_fit_backend(
        runner=lambda **kwargs: calls.append(dict(kwargs)) or _native_fit_probe_payload(),
        native_commands=[],
    )
    result = backend.probe_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
    )

    assert result["fit_command_ready"] is False
    assert result["block_reasons"] == [
        "fit_to_view_expected_window_pid_missing",
        "fit_to_view_expected_document_name_missing",
    ]
    assert calls == []


@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    [
        (
            lambda payload: payload["probe_runtime"].update(
                {"module_sha256": "0" * 64}
            ),
            "native_fit_probe_module_sha256_mismatch",
        ),
        (
            lambda payload: payload.update({"read_only": False}),
            "native_fit_probe_not_read_only",
        ),
        (
            lambda payload: payload["toolbar_buttons"][7].update({"state": 5}),
            "native_fit_probe_fit_button_state_mismatch",
        ),
        (
            lambda payload: payload["viewport_candidates"].append(
                dict(payload["viewport_candidates"][0], handle=9302)
            ),
            "native_fit_probe_viewport_candidates_mismatch",
        ),
        (
            lambda payload: (
                payload["toolbar"].update({"handle": 0}),
                payload["toolbar_candidates"][0].update({"handle": 0}),
            ),
            "native_fit_probe_toolbar_handle_invalid",
        ),
    ],
)
def test_fit_probe_parent_rejects_forged_or_ambiguous_helper_receipt(
    mutator: object,
    expected_reason: str,
) -> None:
    payload = _native_fit_probe_payload()
    mutator(payload)
    backend = _native_fit_backend(
        runner=lambda **_kwargs: payload,
        native_commands=[],
    )

    result = backend.probe_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["fit_command_ready"] is False
    assert expected_reason in result["block_reasons"]


def test_bounded_fit_probe_runner_terminates_timed_out_helper_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Stream:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _Process:
        pid = 41234
        returncode = None

        def __init__(self) -> None:
            self.stdout = _Stream()
            self.stderr = _Stream()
            self.wait_calls: list[float] = []

        def communicate(self, timeout: float) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(["probe"], timeout)

        def wait(self, timeout: float) -> int:
            self.wait_calls.append(timeout)
            self.returncode = -9
            return -9

    class _Job:
        def __init__(self) -> None:
            self.terminated: list[int] = []
            self.closed = False

        def terminate_and_verify(self, item: _Process, *, timeout_seconds: float) -> None:
            self.terminated.append(item.pid)
            item.wait(timeout_seconds)

        def verify_empty(self, *, timeout_seconds: float) -> bool:
            return True

        def close(self) -> None:
            self.closed = True

    process = _Process()
    job = _Job()
    monkeypatch.setattr(
        "material_studio_mcp_server.gui_uia._launch_fit_probe_process",
        lambda *_args, **_kwargs: (process, job),
    )

    with pytest.raises(FitProbeTimeoutError, match="terminated"):
        _default_bounded_fit_probe_runner(
            window_handle=9001,
            expected_window_title="project - Materials Studio",
            timeout_seconds=0.01,
        )

    assert job.terminated == [41234]
    assert process.wait_calls == [5]
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert job.closed is True


def test_bounded_fit_probe_runner_closes_job_when_empty_query_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        returncode = 0
        stdout = None
        stderr = None

        def communicate(self, timeout: float) -> tuple[str, str]:
            del timeout
            return "{}", ""

        def wait(self, timeout: float) -> int:
            del timeout
            return 0

    class _Job:
        def __init__(self) -> None:
            self.terminated = False
            self.closed = False

        def verify_empty(self, *, timeout_seconds: float) -> bool:
            del timeout_seconds
            raise FitProbeCleanupError("query failed")

        def terminate_and_verify(
            self,
            process: _Process,
            *,
            timeout_seconds: float,
        ) -> None:
            del process, timeout_seconds
            self.terminated = True

        def close(self) -> None:
            self.closed = True

    process = _Process()
    job = _Job()
    monkeypatch.setattr(
        "material_studio_mcp_server.gui_uia._launch_fit_probe_process",
        lambda *_args, **_kwargs: (process, job),
    )

    with pytest.raises(FitProbeCleanupError, match="could not prove"):
        _default_bounded_fit_probe_runner(
            window_handle=9001,
            expected_window_title="project - Materials Studio",
            timeout_seconds=0.1,
        )

    assert job.terminated is True
    assert job.closed is True


def test_windows_fit_probe_job_close_failure_is_not_reported_closed() -> None:
    class _Kernel:
        @staticmethod
        def CloseHandle(_handle: object) -> int:
            return 0

    job = object.__new__(_WindowsKillOnCloseJob)
    job._kernel32 = _Kernel()
    job._handle = 123
    job._closed = False

    with pytest.raises(FitProbeCleanupError, match="CloseHandle failed"):
        job.close()

    assert job._closed is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects only")
def test_windows_fit_probe_job_terminates_real_root_and_child(tmp_path: Path) -> None:
    base_executable = str(_fit_probe_runtime_paths()["base_executable"])
    script = (
        "import subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(60)']);"
        "print(child.pid,flush=True);time.sleep(60)"
    )
    process, job = _launch_fit_probe_process(
        [base_executable, "-I", "-S", "-c", script],
        environment=os.environ.copy(),
        cwd=tmp_path,
    )
    try:
        child_pid = int(process.stdout.readline().strip())
        assert child_pid > 0
        assert job.active_process_count() >= 2
        job.terminate_and_verify(process, timeout_seconds=5.0)
        assert process.poll() is not None
        assert job.active_process_count() == 0
    finally:
        if process.poll() is None:
            job.terminate_and_verify(process, timeout_seconds=5.0)
        job.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper only")
def test_bounded_fit_probe_ignores_cwd_and_pythonpath_shadow_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "material_studio_mcp_server"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadowed')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    result = _default_bounded_fit_probe_runner(
        window_handle=1,
        expected_window_title="missing - Materials Studio",
        expected_window_pid=1,
        expected_document_name="missing",
        timeout_seconds=30.0,
    )

    expected = _fit_probe_runtime_paths()
    assert result["helper_exit_code"] == 0
    assert Path(result["probe_runtime"]["module_path"]).resolve() == expected[
        "helper_path"
    ]
    assert result["probe_runtime"]["module_sha256"] == expected["helper_sha256"]
    assert Path(result["probe_runtime"]["dependency_module_path"]).resolve() == expected[
        "dependency_module_path"
    ]
    assert result["probe_runtime"]["dependency_module_sha256"] == expected[
        "dependency_module_sha256"
    ]
    assert result["probe_runtime"]["isolated_mode"] is True
    assert result["probe_runtime"]["no_site_mode"] is True


def test_execute_fit_uses_fresh_native_probe_before_and_after_one_command(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return _native_fit_probe_payload()

    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=runner,
        native_commands=native_commands,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)
    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is True
    assert result["gui_input_performed"] is True
    assert result["structure_modified"] is False
    assert result["automatic_retry_allowed"] is False
    assert native_commands == [(9001, 33299)]
    assert len(calls) == 2


def test_execute_fit_runs_native_probe_then_short_controller_gate_before_input(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    expected_binding = {
        "project_id": "project",
        "revision": 4,
        "result_metadata_path": "result_metadata.json",
        "execution_attempt_id": "a" * 32,
        "execution_attempt_sequence": 1,
        "planned_structure_path": "structure.xsd",
        "wrapper_source_path": "structure.xsd",
        "structure_sha256": "b" * 64,
        "structure_size_bytes": 42,
        "journal_latest_event_sha256": "c" * 64,
    }

    def runner(**_kwargs: object) -> dict[str, object]:
        events.append("native_probe")
        return _native_fit_probe_payload()

    def controller_gate() -> dict[str, object]:
        events.append("controller_gate")
        return {
            "execution_ready": True,
            "project_id": "project",
            "revision": 4,
            "process_count": 1,
            "window_count": 1,
            "single_window_policy_ok": True,
            "native_probe_performed": False,
            "target_window": {
                "handle": 9001,
                "title": "project - Materials Studio",
                "pid": 7001,
                "is_foreground": True,
            },
            "target_wrapper_metadata": {
                "wrapper_provenance_status": "verified_revision_wrapper",
                "wrapper_workspace_matches_controller": True,
                "source_inside_wrapper_workspace": True,
                "project_id": "project",
                "revision": 4,
                "document_name": "structure_r004",
            },
            "structure_binding": {
                "verified": True,
                "identity": expected_binding,
            },
            "block_reasons": [],
        }

    expected_proof = {
        "files": {"structure_artifact": {"sha256": "d" * 64}},
        "journal_latest_event_sha256": "c" * 64,
    }

    def final_proof_gate() -> dict[str, object]:
        events.append("final_proof_gate")
        return {
            "execution_ready": True,
            "project_id": "project",
            "revision": 4,
            "current_revision": 4,
            "process_count": 1,
            "window_count": 1,
            "single_window_policy_ok": True,
            "target_window": {
                "handle": 9001,
                "title": "project - Materials Studio",
                "pid": 7001,
                "is_foreground": True,
            },
            "execution_lock": {"active": False},
            "proof_identity": expected_proof,
            "block_reasons": [],
        }

    def native_identity(_handle: int) -> dict[str, object]:
        events.append("native_identity")
        return _native_window_identity_payload()

    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fit must not enumerate the UIA descendant tree")
        ),
        keyboard_sender=lambda _token: (_ for _ in ()).throw(
            AssertionError("Fit must not send keyboard input")
        ),
        foreground_handle_getter=lambda: 9001,
        native_window_identity_getter=native_identity,
        fit_command_sender=lambda _handle, _command: events.append("native_input"),
        fit_probe_runner=runner,
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)

    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        expected_project_id="project",
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
        expected_structure_binding=expected_binding,
        expected_structure_proof=expected_proof,
        pre_input_gate=controller_gate,
        final_pre_dispatch_gate=final_proof_gate,
    )

    assert result["execution_succeeded"] is True
    assert result["immediate_pre_input_gate"]["native_probe_performed"] is False
    assert events == [
        "native_probe",
        "controller_gate",
        "final_proof_gate",
        "native_identity",
        "native_input",
        "native_probe",
    ]


def test_execute_fit_mapping_mismatch_fails_before_native_command(
    tmp_path: Path,
) -> None:
    payload = _native_fit_probe_payload()
    payload["toolbar_buttons"][7]["command_id"] = 12345
    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=lambda **_kwargs: payload,
        native_commands=native_commands,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)
    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_attempted"] is False
    assert result["gui_input_performed"] is False
    assert result["side_effect_may_have_occurred"] is False
    assert native_commands == []


def test_execute_fit_rechecks_foreground_after_probe_before_command(
    tmp_path: Path,
) -> None:
    native_commands: list[tuple[int, int]] = []
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fit must not enumerate the UIA descendant tree")
        ),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        native_window_identity_getter=lambda _handle: (
            _native_window_identity_payload(is_foreground=False)
        ),
        fit_command_sender=lambda handle, command: native_commands.append(
            (handle, command)
        ),
        fit_probe_runner=lambda **_kwargs: _native_fit_probe_payload(),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)

    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_attempted"] is False
    assert result["automatic_retry_allowed"] is True
    assert native_commands == []


def test_execute_fit_native_identity_blocks_second_materials_studio_window(
    tmp_path: Path,
) -> None:
    native_commands: list[tuple[int, int]] = []
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fit must not enumerate the UIA descendant tree")
        ),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        native_window_identity_getter=lambda _handle: (
            _native_window_identity_payload(
                window_count=2,
                materials_studio_window_handles=[9001, 9002],
            )
        ),
        fit_command_sender=lambda handle, command: native_commands.append(
            (handle, command)
        ),
        fit_probe_runner=lambda **_kwargs: _native_fit_probe_payload(),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)

    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_attempted"] is False
    assert result["automatic_retry_allowed"] is True
    assert result["pre_dispatch_native_window_identity"]["window_count"] == 2
    assert "single-session identity" in result["error"]
    assert native_commands == []


def test_execute_fit_blocks_when_immediate_controller_gate_advances_revision(
    tmp_path: Path,
) -> None:
    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=lambda **_kwargs: _native_fit_probe_payload(),
        native_commands=native_commands,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)

    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        expected_project_id="project",
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
        pre_input_gate=lambda: {
            "execution_ready": False,
            "project_id": "project",
            "revision": 5,
            "single_window_policy_ok": True,
            "target_window": {
                "handle": 9001,
                "title": "project - Materials Studio",
                "pid": 7001,
                "is_foreground": True,
            },
            "local_uia_probe": _native_fit_probe_payload(),
            "block_reasons": ["current_project_revision_advanced"],
        },
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_attempted"] is False
    assert result["automatic_retry_allowed"] is True
    assert native_commands == []


def test_execute_fit_post_probe_failure_disables_automatic_retry(
    tmp_path: Path,
) -> None:
    payloads = [_native_fit_probe_payload(), _native_fit_probe_payload()]
    payloads[1]["toolbar"]["title"] = "Unexpected"
    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=lambda **_kwargs: payloads.pop(0),
        native_commands=native_commands,
    )
    registry_path, registry_sha256 = _native_registry(tmp_path)
    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_performed"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert native_commands == [(9001, 33299)]


def test_execute_fit_registry_mismatch_blocks_before_native_command(
    tmp_path: Path,
) -> None:
    native_commands: list[tuple[int, int]] = []
    backend = _native_fit_backend(
        runner=lambda **_kwargs: _native_fit_probe_payload(),
        native_commands=native_commands,
    )
    registry_path, _registry_sha256 = _native_registry(tmp_path)
    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256="0" * 64,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_attempted"] is False
    assert result["side_effect_may_have_occurred"] is False
    assert native_commands == []


def test_execute_fit_registry_change_after_command_disables_retry(
    tmp_path: Path,
) -> None:
    registry_path, registry_sha256 = _native_registry(tmp_path)
    native_commands: list[tuple[int, int]] = []

    def sender(handle: int, command: int) -> None:
        native_commands.append((handle, command))
        registry_path.write_text("<changed/>", encoding="utf-8")

    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Fit must not enumerate the UIA descendant tree")
        ),
        keyboard_sender=lambda _token: (_ for _ in ()).throw(
            AssertionError("Fit must not send keyboard input")
        ),
        foreground_handle_getter=lambda: 9001,
        native_window_identity_getter=lambda _handle: (
            _native_window_identity_payload()
        ),
        fit_command_sender=sender,
        fit_probe_runner=lambda **_kwargs: _native_fit_probe_payload(),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    result = backend.execute_fit_to_view(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=4,
        toolbar_contracts={
            FIT_TO_VIEW_TOOLBAR_NAME: VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS[
                FIT_TO_VIEW_TOOLBAR_NAME
            ]
        },
        command_labels={FIT_TO_VIEW_COMMAND_ID: FIT_TO_VIEW_CONTROL_NAME},
        registry_sha256=registry_sha256,
        registry_path=registry_path,
        expected_window_pid=7001,
        expected_document_name="structure_r004",
    )

    assert result["execution_succeeded"] is False
    assert result["gui_input_performed"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert native_commands == [(9001, 33299)]


def test_viewport_capture_bounds_clips_uia_child_to_negative_monitor_window() -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        window_rect_getter=lambda _handle: (-1444, 89, -4, 842),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    window = _FakeRectWrapper(
        (-1444, 89, -4, 842),
        handle=9001,
        control_type="Window",
    )
    mdi_client = _FakeRectWrapper(
        (-1276, 196, -16, 811),
        parent=window,
        class_name="MDIClient",
    )
    document = _FakeRectWrapper(
        (-1274, 198, -74, 898),
        parent=mdi_client,
        control_type="Window",
    )
    viewer_pane = _FakeRectWrapper((-1266, 229, -82, 890), parent=document)
    viewport = _FakeRectWrapper((-1266, 229, -82, 890), parent=viewer_pane)

    contract = backend._viewport_capture_contract(
        window_handle=9001,
        viewport_wrapper=viewport,
    )

    assert contract["bounds"] == [178, 140, 1362, 722]
    assert contract["target_window_reached"] is True
    assert contract["mdi_client_count"] == 1
    assert contract["mdi_client_observed"] is True
    assert contract["status_bar_excluded_by_ancestor_clipping"] is True
    assert len(contract["clip_chain"]) == 5
    assert backend._viewport_capture_bounds(
        window_handle=9001,
        viewport_wrapper=viewport,
    ) == (178, 140, 1362, 722)


def test_viewport_capture_bounds_rejects_tiny_visible_intersection() -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        window_rect_getter=lambda _handle: (0, 0, 800, 600),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    window = _FakeRectWrapper((0, 0, 800, 600), handle=9001)
    mdi_client = _FakeRectWrapper(
        (0, 0, 800, 600),
        parent=window,
        class_name="MDIClient",
    )
    viewport = _FakeRectWrapper(
        (790, 590, 1000, 900),
        parent=mdi_client,
    )

    with pytest.raises(UiaReplayError, match="visible intersection"):
        backend._viewport_capture_bounds(
            window_handle=9001,
            viewport_wrapper=viewport,
        )


def test_viewport_capture_bounds_requires_exact_target_window_ancestry() -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        window_rect_getter=lambda _handle: (0, 0, 800, 600),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    unrelated_window = _FakeRectWrapper((0, 0, 800, 600), handle=9002)
    mdi_client = _FakeRectWrapper(
        (0, 0, 800, 600),
        parent=unrelated_window,
        class_name="MDIClient",
    )
    viewport = _FakeRectWrapper(
        (100, 100, 700, 500),
        parent=mdi_client,
    )

    with pytest.raises(UiaReplayError, match="exact target window"):
        backend._viewport_capture_bounds(
            window_handle=9001,
            viewport_wrapper=viewport,
        )


def test_viewport_capture_bounds_requires_unique_mdi_client_ancestor() -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        window_rect_getter=lambda _handle: (0, 0, 800, 600),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    window = _FakeRectWrapper((0, 0, 800, 600), handle=9001)
    viewport = _FakeRectWrapper((100, 100, 700, 500), parent=window)

    with pytest.raises(UiaReplayError, match="exactly one visible MDIClient"):
        backend._viewport_capture_bounds(
            window_handle=9001,
            viewport_wrapper=viewport,
        )


def test_ancestor_clipped_viewport_excludes_status_bar_rendering_noise(
    tmp_path: Path,
) -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        window_rect_getter=lambda _handle: (0, 0, 200, 200),
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )
    window = _FakeRectWrapper((0, 0, 200, 200), handle=9001)
    mdi_client = _FakeRectWrapper(
        (0, 0, 200, 170),
        parent=window,
        class_name="MDIClient",
    )
    document = _FakeRectWrapper((0, 0, 200, 220), parent=mdi_client)
    viewport = _FakeRectWrapper((0, 0, 200, 220), parent=document)
    before = tmp_path / "before.bmp"
    after = tmp_path / "after.bmp"
    _write_rgb_bmp(before, width=200, height=200, pixels={})
    _write_rgb_bmp(
        after,
        width=200,
        height=200,
        pixels={(x, 185): (0, 0, 0) for x in range(40, 80)},
    )

    bounds = backend._viewport_capture_bounds(
        window_handle=9001,
        viewport_wrapper=viewport,
    )

    assert bounds == (0, 0, 200, 170)
    assert compare_bmp_region(before, after, bounds=bounds)["exact_match"] is True
    full_compare = compare_bmp_region(before, after, bounds=(0, 0, 200, 200))
    assert full_compare["exact_match"] is False
    assert full_compare["changed_pixel_count"] == 40


@pytest.mark.parametrize(
    ("path", "label", "expected_verification"),
    [
        (
            ["View", "Explorers", "Properties Explorer"],
            "Properties Explorer",
            "readable_native_menu_labels",
        ),
        (
            ["View", "", ""],
            "",
            "ms20_owner_drawn_submenu_labels_unavailable_exact_command_id",
        ),
    ],
)
def test_properties_explorer_accepts_only_reviewed_native_menu_shapes(
    path: list[str],
    label: str,
    expected_verification: str,
) -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        native_menu_reader=lambda _handle: [
            {
                "path": path,
                "label": label,
                "command_id": 33439,
                "enabled": True,
            }
        ],
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    entry = backend._properties_explorer_menu_entry(9001)

    assert entry["command_id_verified"] is True
    assert entry["path_verification"] == expected_verification


def test_properties_explorer_rejects_unreviewed_native_menu_shape() -> None:
    top, _viewport, _reset = _build_tree()
    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=lambda _token: None,
        foreground_handle_getter=lambda: 9001,
        native_menu_reader=lambda _handle: [
            {
                "path": ["Tools", "Unknown"],
                "label": "Unknown",
                "command_id": 33439,
                "enabled": True,
            }
        ],
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    with pytest.raises(UiaReplayError, match="outside the reviewed View menu shape"):
        backend._properties_explorer_menu_entry(9001)


def test_viewer_document_title_binds_window_that_owns_viewport() -> None:
    top = _FakeWrapper(
        runtime_id=1,
        name="project - Materials Studio",
        control_type="Window",
    )
    document = top.add_child(
        _FakeWrapper(
            runtime_id=2,
            name="model.cif",
            control_type="Window",
        )
    )
    document.add_child(
        _FakeWrapper(
            runtime_id=3,
            control_type="Pane",
            class_name="CViewer3DCtrl",
        )
    )
    top.add_child(
        _FakeWrapper(
            runtime_id=4,
            name="unrelated dialog",
            control_type="Window",
        )
    )

    assert PywinautoViewReplayBackend._viewer_document_title(top) == "model.cif"


def test_dirty_document_title_requires_exact_single_marker_transition() -> None:
    PywinautoViewReplayBackend._require_expected_dirty_title(
        "model.cif*",
        expected_window_title="model.cif",
    )
    PywinautoViewReplayBackend._require_expected_dirty_title(
        "model.cif *",
        expected_window_title="model.cif",
    )

    with pytest.raises(UiaReplayError, match="viewer-document title transition"):
        PywinautoViewReplayBackend._require_expected_dirty_title(
            "*model.cif",
            expected_window_title="model.cif",
        )


def test_miller_properties_accepts_unique_virtualized_grid_record() -> None:
    top = _FakeWrapper(runtime_id=1, control_type="Window")
    properties = top.add_child(
        _FakeWrapper(
            runtime_id=2,
            control_type="Pane",
            automation_id="GenPropEdit",
        )
    )
    properties.add_child(
        _FakeWrapper(
            runtime_id=3,
            name="Filter:",
            control_type="ComboBox",
            automation_id="cbObjectType",
            value="Miller Plane",
        )
    )
    grid = properties.add_child(
        _FakeWrapper(
            runtime_id=4,
            control_type="DataGrid",
            automation_id="vGridControl",
        )
    )
    grid.add_child(
        _FakeWrapper(
            runtime_id=5,
            name="MillerIndex Record 0",
            control_type="DataItem",
            visible=False,
            value="(001)",
        )
    )

    result = PywinautoViewReplayBackend._verify_miller_properties(
        PywinautoViewReplayBackend(platform_supported=False),
        top,
        expected_label="(001)",
    )

    assert result["properties_filter"] == "Miller Plane"
    assert result["properties_miller_label"] == "(001)"
    assert result["miller_record_visible"] is False
    assert result["miller_record_virtualized_visibility_allowed"] is True


def _top_recipe() -> dict:
    return {
        "view_name": "top",
        "automation_ready": True,
        "structure_mutation_allowed": False,
        "launch_new_matstudio_process_allowed": False,
        "blind_coordinate_action_allowed": False,
        "native_command_id": "cmdViewer3DResetView",
        "key_sequence": ["Up", "Up"],
        "modifier_keys": [],
        "rotation_increment_degrees": 45,
        "accessibility_target": {
            "target_kind": "named_control",
            "invocation_method": "accessibility_named_control",
            "toolbar_name": "3D Viewer",
            "control_name": "3D Viewer Reset View",
            "command_id": "cmdViewer3DResetView",
        },
    }


def _isometric_recipe() -> dict:
    return {
        "view_name": "isometric",
        "automation_ready": True,
        "structure_mutation_allowed": False,
        "launch_new_matstudio_process_allowed": False,
        "blind_coordinate_action_allowed": False,
        "native_command_id": "cmdViewer3DResetView",
        "accessibility_target": {
            "target_kind": "named_control",
            "invocation_method": "accessibility_named_control",
            "toolbar_name": "3D Viewer",
            "control_name": "3D Viewer Reset View",
            "command_id": "cmdViewer3DResetView",
        },
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
        "movement_accessibility_target": {
            "registry_sha256": "a" * 64,
            "registry_toolbar_name": "tbarViewer3DMovement",
            "toolbar_name": "3D Movement",
            "toolbar_automation_id": 12134,
            "command_id": "cmdViewer3DMovementOptions",
            "zero_based_child_index": 4,
            "element_index": 7,
            "semantic_mapping_sha256": "b" * 64,
            "target_kind": "verified_anonymous_toolbar_child",
            "invocation_method": "local_uia_invoke_pattern",
            "angle_control_id": "numNudgeAngle",
            "screen_factor_control_id": "numNudgeFactor",
        },
    }


def test_probe_derives_named_reset_anonymous_movement_and_semantic_viewport() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.probe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        expected_revision=7,
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["safe_for_standard_view_replay"] is True
    assert result["resolved_command_ids"] == [
        "cmdViewer3DMovementOptions",
        "cmdViewer3DResetView",
    ]
    assert result["evidence"]["source"] == "local_uia"
    assert result["evidence"]["empty_viewport_focus_target_observed"] is False
    assert result["evidence"]["semantic_viewport_focus_supported"] is True
    assert result["evidence"]["anonymous_toolbars"][0][
        "observed_toolbar_name"
    ] == "3D Movement"
    reset_control = next(
        item
        for item in result["evidence"]["controls"]
        if item["command_id"] == "cmdViewer3DResetView"
    )
    assert reset_control == {
        "command_id": "cmdViewer3DResetView",
        "observed_control_name": "3D Viewer Reset View",
        "invoke_supported": True,
    }
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert sent_keys == []


def test_execute_top_invokes_reset_focuses_exact_viewport_and_sends_only_arrows() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_top_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is True
    assert result["reset_invocation_succeeded"] is True
    assert result["keyboard_focus_verified"] is True
    assert result["key_sequence_sent"] == ["Up", "Up"]
    assert result["modifier_keys"] == []
    assert result["coordinate_input_used"] is False
    assert result["pointer_input_used"] is False
    assert result["visual_acceptance_recorded"] is False
    assert reset.invoke_count == 1
    assert viewport.focus_count == 1
    assert sent_keys == ["{UP}", "{UP}"]


def test_execute_stops_after_reset_when_exact_viewport_cannot_take_focus() -> None:
    top, viewport, reset = _build_tree(viewport_acquires_focus=False)
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_top_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert result["failure_phase"] == "viewport_focus"
    assert result["reset_invocation_succeeded"] is True
    assert result["key_sequence_sent"] == []
    assert result["retry_restarts_from_reset_baseline"] is True
    assert reset.invoke_count == 1
    assert viewport.focus_count == 1
    assert sent_keys == []


def test_execute_rejects_modifier_keys_before_any_gui_action() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)
    recipe = _top_recipe()
    recipe["modifier_keys"] = ["Shift"]

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=recipe,
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert result["failure_phase"] == "preflight"
    assert "Modifier keys are forbidden" in result["error"]
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert sent_keys == []


def test_execute_rejects_non_foreground_window_before_any_gui_action() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys, foreground_handle=123)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_top_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert "not the foreground window" in result["error"]
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert sent_keys == []


def test_execute_isometric_sets_exact_stages_restores_movement_and_closes_dialog() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_isometric_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is True
    assert result["reset_invocation_succeeded"] is True
    assert result["keyboard_focus_verified"] is True
    assert result["key_sequence_sent"] == [
        "Up",
        "Up",
        "Left",
        "Left",
        "Left",
        "Down",
    ]
    assert [
        stage["rotation_increment_degrees"]
        for stage in result["keyboard_stages"]
    ] == [45.0, 35.26438968]
    assert result["rotation_increment_restored_degrees"] == 45.0
    assert result["movement_screen_factor"] == 2.0
    assert result["movement_dialog_closed"] is True
    assert result["movement_restore_succeeded"] is True
    assert result["modifier_keys"] == []
    assert reset.invoke_count == 1
    assert viewport.focus_count == 2
    assert top.movement_button.invoke_count == 3
    assert top.movement_dialog.close_count == 3
    assert top.movement_angle_edit.value_history == [
        "45.0",
        "35.264",
        "45.0",
    ]
    assert top.movement_angle_edit.iface_value.CurrentValue == "45.0"
    assert top.movement_factor_edit.iface_value.CurrentValue == "2.0"
    assert top._movement_open is False
    assert sent_keys == [
        "{UP}",
        "{UP}",
        "{LEFT}",
        "{LEFT}",
        "{LEFT}",
        "{DOWN}",
    ]


def test_execute_isometric_rejects_modified_stage_before_any_gui_action() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)
    recipe = _isometric_recipe()
    recipe["keyboard_stages"][1]["modifier_keys"] = ["Shift"]

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=recipe,
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert result["failure_phase"] == "preflight"
    assert "Modifier keys are forbidden" in result["error"]
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert top.movement_button.invoke_count == 0
    assert sent_keys == []


def test_execute_isometric_rejects_rounded_theoretical_recipe_angle() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)
    recipe = _isometric_recipe()
    recipe["keyboard_stages"][1]["rotation_increment_degrees"] = 35.264

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=recipe,
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert "angle differs from the allowlist" in result["error"]
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert top.movement_button.invoke_count == 0
    assert sent_keys == []


def test_execute_isometric_stops_when_screen_factor_is_not_two() -> None:
    top, viewport, reset = _build_tree()
    top.movement_factor_edit.iface_value.CurrentValue = "3.0"
    top.movement_factor_edit._parent.element_info.name = "3.0"
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_isometric_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert "screen factor must remain 2.0" in result["error"]
    assert result["manual_movement_restore_required"] is True
    assert reset.invoke_count == 1
    assert viewport.focus_count == 0
    assert top._movement_open is False
    assert sent_keys == []


def test_execute_isometric_restores_angle_after_stage_keyboard_failure() -> None:
    top, viewport, reset = _build_tree()
    sent_keys: list[str] = []

    def fail_on_down(token: str) -> None:
        if token == "{DOWN}":
            raise RuntimeError("synthetic keyboard failure")
        sent_keys.append(token)

    backend = PywinautoViewReplayBackend(
        desktop_factory=lambda **_kwargs: _FakeDesktop(top),
        keyboard_sender=fail_on_down,
        foreground_handle_getter=lambda: 9001,
        sleep_fn=lambda _seconds: None,
        platform_supported=True,
    )

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_isometric_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert result["failure_phase"] == "movement_stage_2_keyboard"
    assert result["movement_restore_succeeded"] is True
    assert result["rotation_increment_restored_degrees"] == 45.0
    assert result["movement_dialog_closed"] is True
    assert result["key_sequence_sent"] == [
        "Up",
        "Up",
        "Left",
        "Left",
        "Left",
    ]
    assert top.movement_angle_edit.iface_value.CurrentValue == "45.0"
    assert top._movement_open is False
    assert reset.invoke_count == 1
    assert viewport.focus_count == 2
    assert sent_keys == [
        "{UP}",
        "{UP}",
        "{LEFT}",
        "{LEFT}",
        "{LEFT}",
    ]


def test_execute_blocks_preexisting_movement_dialog_before_reset() -> None:
    top, viewport, reset = _build_tree()
    top._movement_open = True
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_isometric_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert "local_uia_movement_dialog_already_open" in result["error"]
    assert reset.invoke_count == 0
    assert viewport.focus_count == 0
    assert top.movement_button.invoke_count == 0
    assert sent_keys == []


def test_execute_closes_movement_when_dialog_contract_probe_fails() -> None:
    top, viewport, reset = _build_tree()
    movement_options = next(
        item
        for item in top.movement_dialog.descendants()
        if item.element_info.automation_id == "MovementOptions"
    )
    movement_options._children = [
        child
        for child in movement_options._children
        if child.element_info.automation_id != "numNudgeFactor"
    ]
    sent_keys: list[str] = []
    backend = _backend(top, sent_keys=sent_keys)

    result = backend.execute_standard_recipe(
        window_handle=9001,
        expected_window_title="project - Materials Studio",
        execution_recipe=_isometric_recipe(),
        toolbar_contracts=VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
        command_labels=VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    )

    assert result["execution_succeeded"] is False
    assert "numNudgeFactor was not uniquely observed" in result["error"]
    assert result["manual_movement_restore_required"] is True
    assert reset.invoke_count == 1
    assert viewport.focus_count == 0
    assert top.movement_button.invoke_count == 2
    assert top.movement_dialog.close_count == 2
    assert top._movement_open is False
    assert sent_keys == []


def test_miller_bmp_diff_bridges_cell_line_and_selects_dense_interior(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before.bmp"
    after = tmp_path / "after.bmp"
    _write_rgb_bmp(before, width=120, height=90, pixels={})
    plane_pixels = {
        (x, y): (203, 136, 0)
        for y in range(25, 66)
        for x in range(30, 91)
        if x not in {59, 60}
    }
    _write_rgb_bmp(after, width=120, height=90, pixels=plane_pixels)

    result = analyze_miller_plane_bmp_diff(
        before,
        after,
        viewport_bounds=(10, 10, 110, 80),
    )

    assert result["significant_component_count"] == 1
    assert result["region_bbox"] == [30, 25, 91, 66]
    candidate_x, candidate_y = result["candidate_window_pixel"]
    assert 35 <= candidate_x <= 85
    assert 30 <= candidate_y <= 60
    assert result["candidate_after_rgb"] == [203, 136, 0]


def test_miller_bmp_diff_rejects_two_significant_regions(tmp_path: Path) -> None:
    before = tmp_path / "before.bmp"
    after = tmp_path / "after.bmp"
    _write_rgb_bmp(before, width=120, height=90, pixels={})
    changed = {
        (x, y): (203, 136, 0)
        for left, top in ((15, 15), (75, 50))
        for y in range(top, top + 15)
        for x in range(left, left + 20)
    }
    _write_rgb_bmp(after, width=120, height=90, pixels=changed)

    with pytest.raises(UiaReplayError, match="exactly one significant"):
        analyze_miller_plane_bmp_diff(
            before,
            after,
            viewport_bounds=(0, 0, 120, 90),
        )


def test_compare_bmp_region_requires_exact_restoration(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.bmp"
    restored = tmp_path / "restored.bmp"
    changed = tmp_path / "changed.bmp"
    _write_rgb_bmp(baseline, width=40, height=30, pixels={})
    _write_rgb_bmp(restored, width=40, height=30, pixels={})
    _write_rgb_bmp(
        changed,
        width=40,
        height=30,
        pixels={(20, 15): (247, 248, 248)},
    )

    exact = compare_bmp_region(
        baseline,
        restored,
        bounds=(5, 5, 35, 25),
    )
    mismatch = compare_bmp_region(
        baseline,
        changed,
        bounds=(5, 5, 35, 25),
    )

    assert exact["exact_match"] is True
    assert exact["changed_pixel_count"] == 0
    assert mismatch["exact_match"] is False
    assert mismatch["changed_pixel_count"] == 1
    assert mismatch["peak_channel_delta"] == 1
