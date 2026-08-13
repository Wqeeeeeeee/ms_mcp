from __future__ import annotations

import importlib
import os
import struct
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from material_studio_mcp_server.gui import (
    VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
)
from material_studio_mcp_server.gui_uia import (
    COMTYPES_CACHE_ENV,
    PywinautoViewReplayBackend,
    UiaReplayError,
    _external_comtypes_gen_cache,
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
