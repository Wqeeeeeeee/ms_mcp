from __future__ import annotations

from dataclasses import dataclass

from material_studio_mcp_server.gui import (
    VIEW_RUNTIME_ACCESSIBILITY_COMMAND_LABELS,
    VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS,
)
from material_studio_mcp_server.gui_uia import PywinautoViewReplayBackend


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
    ) -> None:
        self.element_info = _FakeElementInfo(
            runtime_id=runtime_id,
            name=name,
            control_type=control_type,
            automation_id=automation_id,
            class_name=class_name,
        )
        self._children: list[_FakeWrapper] = []
        self._enabled = enabled
        self._visible = visible
        self._focusable = focusable
        self._acquire_focus = acquire_focus
        self._focused = False
        self._invoke_supported = invoke_supported
        self.invoke_count = 0
        self.focus_count = 0

    @property
    def iface_invoke(self) -> object:
        if not self._invoke_supported:
            raise RuntimeError("InvokePattern unavailable")
        return object()

    def children(self) -> list["_FakeWrapper"]:
        return list(self._children)

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
        return self


class _FakeTop(_FakeWrapper):
    def __init__(self, *, title: str, descendants: list[_FakeWrapper]) -> None:
        super().__init__(
            runtime_id=1,
            name=title,
            control_type="Window",
            class_name="MaterialsStudioMainWindow",
        )
        self._descendants = descendants

    def descendants(self) -> list[_FakeWrapper]:
        return list(self._descendants)


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
        movement._children.append(
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
        viewer._children.append(child)
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
    return _FakeTop(title=title, descendants=descendants), viewport, reset


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
