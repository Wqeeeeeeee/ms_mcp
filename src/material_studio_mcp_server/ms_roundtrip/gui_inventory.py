"""Read-only one-process/one-window evidence for round-trip execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from material_studio_mcp_server.gui import (
    ProcessInfo,
    WindowInfo,
    WindowsGuiBackend,
)

from .contracts import GuiInventoryReceipt, GuiInvariantReceipt
from .secure_io import sha256_text


class ReadOnlyGuiBackend(Protocol):
    def list_processes(self) -> list[ProcessInfo]: ...

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]: ...


@dataclass(frozen=True)
class GuiObservation:
    receipt: GuiInventoryReceipt
    process_ids: tuple[int, ...]
    window_handles: tuple[int, ...]
    window_pid_pairs: tuple[tuple[int | None, int], ...]


def _deduplicate_processes(values: list[ProcessInfo]) -> tuple[ProcessInfo, ...]:
    by_pid: dict[int, ProcessInfo] = {}
    for process in values:
        if process.name.casefold() == "matstudio.exe" and process.pid > 0:
            by_pid.setdefault(process.pid, process)
    return tuple(by_pid[pid] for pid in sorted(by_pid))


def _deduplicate_windows(values: list[WindowInfo]) -> tuple[WindowInfo, ...]:
    by_handle: dict[int, WindowInfo] = {}
    for window in values:
        if window.handle > 0:
            by_handle.setdefault(window.handle, window)
    return tuple(by_handle[handle] for handle in sorted(by_handle))


def capture_gui_inventory(backend: ReadOnlyGuiBackend) -> GuiObservation:
    """Enumerate only; never activate, restore, capture, open, or hot-load."""

    processes = _deduplicate_processes(list(backend.list_processes()))
    requested_pid = processes[0].pid if len(processes) == 1 else None
    windows = _deduplicate_windows(list(backend.list_windows(pid=requested_pid)))
    process_ids = tuple(process.pid for process in processes)
    window_handles = tuple(window.handle for window in windows)
    window_pid_pairs = tuple((window.pid, window.handle) for window in windows)
    window = windows[0] if len(windows) == 1 else None
    usable = bool(
        len(processes) == 1
        and window is not None
        and window.pid == processes[0].pid
        and window.is_visible is True
        and bool(window.title)
    )
    process_hash = (
        sha256_text(f"MatStudio.exe\0{process_ids[0]}")
        if len(process_ids) == 1
        else None
    )
    window_hash = (
        sha256_text(f"{window.pid}\0{window.handle}") if window is not None else None
    )
    title_hash = sha256_text(window.title) if window is not None else None
    receipt = GuiInventoryReceipt(
        process_count=len(processes),
        window_count=len(windows),
        usable_single_window=usable,
        process_identity_sha256=process_hash,
        window_identity_sha256=window_hash,
        window_title_sha256=title_hash,
        window_visible=window.is_visible if window is not None else None,
        window_minimized=window.is_minimized if window is not None else None,
        window_foreground=window.is_foreground if window is not None else None,
    )
    return GuiObservation(
        receipt=receipt,
        process_ids=process_ids,
        window_handles=window_handles,
        window_pid_pairs=window_pid_pairs,
    )


def compare_gui_inventories(
    before: GuiObservation,
    after: GuiObservation,
) -> GuiInvariantReceipt:
    process_unchanged = before.process_ids == after.process_ids
    window_unchanged = before.window_pid_pairs == after.window_pid_pairs
    identity_unchanged = process_unchanged and window_unchanged
    launched = any(pid not in before.process_ids for pid in after.process_ids)
    passed = (
        before.receipt.usable_single_window
        and after.receipt.usable_single_window
        and process_unchanged
        and window_unchanged
        and identity_unchanged
        and not launched
    )
    return GuiInvariantReceipt(
        before=before.receipt,
        after=after.receipt,
        matstudio_process_count_before_after=(
            before.receipt.process_count,
            after.receipt.process_count,
        ),
        matstudio_window_count_before_after=(
            before.receipt.window_count,
            after.receipt.window_count,
        ),
        process_identity_unchanged=process_unchanged,
        window_identity_unchanged=window_unchanged,
        matstudio_pid_and_window_handle_unchanged=identity_unchanged,
        matstudio_process_launched=launched,
        invariant_passed=passed,
    )


def default_gui_backend() -> WindowsGuiBackend:
    return WindowsGuiBackend()


__all__ = [
    "GuiObservation",
    "ReadOnlyGuiBackend",
    "capture_gui_inventory",
    "compare_gui_inventories",
    "default_gui_backend",
]
