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
