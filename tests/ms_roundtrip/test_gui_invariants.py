from __future__ import annotations

from material_studio_mcp_server.gui import ProcessInfo
from material_studio_mcp_server.ms_roundtrip import (
    capture_gui_inventory,
    compare_gui_inventories,
)

from ._helpers import FakeGuiBackend


def test_single_minimized_window_is_usable_without_activation() -> None:
    gui = FakeGuiBackend(minimized=True)
    observed = capture_gui_inventory(gui)
    assert observed.receipt.process_count == 1
    assert observed.receipt.window_count == 1
    assert observed.receipt.usable_single_window is True
    assert observed.receipt.window_minimized is True
    assert observed.receipt.window_foreground is False
    assert gui.prohibited_calls == []


def test_same_process_and_window_inventory_passes() -> None:
    gui = FakeGuiBackend()
    before = capture_gui_inventory(gui)
    after = capture_gui_inventory(gui)
    receipt = compare_gui_inventories(before, after)
    assert receipt.invariant_passed is True
    assert receipt.matstudio_pid_and_window_handle_unchanged is True
    assert receipt.matstudio_process_launched is False


def test_second_process_fails_single_window_precondition() -> None:
    gui = FakeGuiBackend()
    gui.processes.append(ProcessInfo(name="MatStudio.exe", pid=5252))
    observed = capture_gui_inventory(gui)
    assert observed.receipt.process_count == 2
    assert observed.receipt.usable_single_window is False


def test_added_window_fails_post_execution_invariant() -> None:
    gui = FakeGuiBackend()
    before = capture_gui_inventory(gui)
    gui.add_second_window()
    after = capture_gui_inventory(gui)
    receipt = compare_gui_inventories(before, after)
    assert receipt.invariant_passed is False
    assert receipt.window_identity_unchanged is False


def test_replaced_process_and_window_identity_is_detected() -> None:
    gui = FakeGuiBackend()
    before = capture_gui_inventory(gui)
    gui.replace_identity()
    after = capture_gui_inventory(gui)
    receipt = compare_gui_inventories(before, after)
    assert receipt.invariant_passed is False
    assert receipt.process_identity_unchanged is False
    assert receipt.window_identity_unchanged is False
    assert receipt.matstudio_process_launched is True


def test_compact_inventory_never_contains_raw_pid_handle_or_title() -> None:
    gui = FakeGuiBackend()
    receipt = capture_gui_inventory(gui).receipt
    payload = receipt.model_dump_json()
    assert receipt.contains_pid is False
    assert receipt.contains_window_handle is False
    assert receipt.contains_window_title is False
    assert "4242" not in payload
    assert "8181" not in payload
    assert "Current Project - Materials Studio" not in payload
