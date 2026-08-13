from __future__ import annotations

import json
from typing import Any

import pytest

import material_studio_mcp_server.gui_fit_probe as probe_module


def _install_native_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    windows: dict[int, dict[str, Any]],
    children: dict[int, list[int]],
    foreground_handle: int = 100,
    active_document_handle: int | None = 301,
) -> None:
    monkeypatch.setattr(probe_module, "_windows_supported", lambda: True)
    monkeypatch.setattr(
        probe_module, "_native_window_exists", lambda handle: handle in windows
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_text",
        lambda handle: str(windows[handle]["title"]),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_class",
        lambda handle: str(windows[handle]["class_name"]),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_process_id",
        lambda handle: int(windows[handle]["process_id"]),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_foreground_handle",
        lambda: foreground_handle,
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_visible",
        lambda handle: bool(windows[handle].get("visible", True)),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_enabled",
        lambda handle: bool(windows[handle].get("enabled", True)),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_window_minimized",
        lambda handle: bool(windows[handle].get("minimized", False)),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_control_id",
        lambda handle: int(windows[handle].get("control_id", 0)),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_child_handles",
        lambda handle: list(children.get(handle, [])),
    )
    monkeypatch.setattr(
        probe_module,
        "_native_active_mdi_document",
        lambda handle: active_document_handle if handle == 300 else None,
    )
    monkeypatch.setattr(
        probe_module,
        "_native_is_child",
        lambda parent, child: parent == 300 and child == active_document_handle,
    )


def _windows(*, duplicate_toolbar: bool = False) -> dict[int, dict[str, Any]]:
    values: dict[int, dict[str, Any]] = {
        100: {
            "title": "fit_project - Materials Studio",
            "class_name": "MaterialsStudioMainWindow",
            "process_id": 55,
        },
        201: {
            "title": "3D Viewer",
            "class_name": "ToolbarWindow32",
            "process_id": 55,
            "control_id": 12122,
        },
        202: {
            "title": "",
            "class_name": "CViewer3DCtrl",
            "process_id": 55,
            "control_id": 700,
        },
        300: {
            "title": "",
            "class_name": "MDIClient",
            "process_id": 55,
        },
        301: {
            "title": "model.cif",
            "class_name": "AfxFrameOrView",
            "process_id": 55,
        },
    }
    if duplicate_toolbar:
        values[203] = dict(values[201])
    return values


def test_inspect_native_fit_target_returns_exact_toolbar_and_full_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = _windows()
    _install_native_fixture(
        monkeypatch,
        windows=windows,
        children={100: [201, 300], 301: [202]},
    )
    rows = [
        {"index": index, "command_id": 33290 + index, "style": 0, "state": 4}
        for index in range(9)
    ]
    observed_handles: list[int] = []

    def reader(handle: int) -> list[dict[str, Any]]:
        observed_handles.append(handle)
        return rows

    result = probe_module.inspect_native_fit_target(
        window_handle=100,
        expected_window_title="fit_project - Materials Studio",
        toolbar_button_reader=reader,
        expected_window_pid=55,
        expected_document_name="model.cif",
    )

    assert result["ok"] is True
    assert result["safe_for_fit_to_view_invoke"] is True
    assert result["gui_input_performed"] is False
    assert result["window"]["handle"] == 100
    assert result["window"]["is_foreground"] is True
    assert result["toolbar"] == result["toolbar_candidates"][0]
    assert result["toolbar"]["handle"] == 201
    assert result["toolbar"]["control_id"] == 12122
    assert result["toolbar_buttons"] == rows
    assert result["mdi_client"]["handle"] == 300
    assert result["active_document"]["handle"] == 301
    assert result["active_document"]["title"] == "model.cif"
    assert result["active_viewport"]["handle"] == 202
    assert result["viewport_candidates"][0]["handle"] == 202
    assert observed_handles == [201]


def test_inspect_native_fit_target_blocks_duplicate_toolbar_before_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = _windows(duplicate_toolbar=True)
    _install_native_fixture(
        monkeypatch,
        windows=windows,
        children={100: [201, 203, 300], 301: [202]},
    )

    def reader(_handle: int) -> list[dict[str, Any]]:
        raise AssertionError("ambiguous toolbar must not be read")

    result = probe_module.inspect_native_fit_target(
        window_handle=100,
        expected_window_title="fit_project - Materials Studio",
        toolbar_button_reader=reader,
    )

    assert result["ok"] is False
    assert result["toolbar"] is None
    assert len(result["toolbar_candidates"]) == 2
    assert result["block_reasons"] == [
        "native_3d_viewer_toolbar_identity_not_unique"
    ]


def test_inspect_native_fit_target_blocks_title_mismatch_and_background_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = _windows()
    _install_native_fixture(
        monkeypatch,
        windows=windows,
        children={100: [201, 300], 301: [202]},
        foreground_handle=999,
    )

    def reader(_handle: int) -> list[dict[str, Any]]:
        raise AssertionError("blocked target must not read the toolbar")

    result = probe_module.inspect_native_fit_target(
        window_handle=100,
        expected_window_title="another project - Materials Studio",
        toolbar_button_reader=reader,
    )

    assert result["ok"] is False
    assert result["window"]["title"] == "fit_project - Materials Studio"
    assert result["window"]["is_foreground"] is False
    assert result["block_reasons"] == [
        "target_window_title_mismatch",
        "target_window_not_foreground",
    ]
    assert result["toolbar_buttons"] == []


@pytest.mark.parametrize("observed_title", ["model.cif", "model.cif*", "model.cif *"])
def test_inspect_native_fit_target_accepts_only_reviewed_document_dirty_markers(
    monkeypatch: pytest.MonkeyPatch,
    observed_title: str,
) -> None:
    windows = _windows()
    windows[301]["title"] = observed_title
    _install_native_fixture(
        monkeypatch,
        windows=windows,
        children={100: [201, 300], 301: [202]},
    )

    result = probe_module.inspect_native_fit_target(
        window_handle=100,
        expected_window_title="fit_project - Materials Studio",
        expected_window_pid=55,
        expected_document_name="model.cif",
        toolbar_button_reader=lambda _handle: [],
    )

    assert result["ok"] is True
    assert result["active_document"]["title"] == observed_title


def test_inspect_native_fit_target_blocks_wrong_pid_document_and_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = _windows()
    windows[301]["title"] = "other.cif"
    windows[204] = dict(windows[202])
    _install_native_fixture(
        monkeypatch,
        windows=windows,
        children={100: [201, 300], 301: [202, 204]},
    )

    def reader(_handle: int) -> list[dict[str, Any]]:
        raise AssertionError("blocked document must not read the toolbar")

    result = probe_module.inspect_native_fit_target(
        window_handle=100,
        expected_window_title="fit_project - Materials Studio",
        expected_window_pid=56,
        expected_document_name="model.cif",
        toolbar_button_reader=reader,
    )

    assert result["ok"] is False
    assert result["active_viewport"] is None
    assert result["block_reasons"] == [
        "target_window_process_mismatch",
        "native_active_document_title_mismatch",
        "native_active_viewport_identity_not_unique",
    ]
    assert result["toolbar_buttons"] == []


def test_cli_emits_one_json_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, Any]] = []

    def inspect(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "window": {"handle": kwargs["window_handle"]},
            "active_document": {"title": kwargs["expected_document_name"]},
            "active_viewport": {"handle": 202},
            "toolbar": {"title": "3D Viewer"},
            "toolbar_buttons": [],
            "viewport_candidates": [],
            "block_reasons": [],
        }

    monkeypatch.setattr(probe_module, "inspect_native_fit_target", inspect)

    status = probe_module.main(
        [
            "--window-handle",
            "100",
            "--expected-window-title",
            "fit_project - Materials Studio",
            "--expected-window-pid",
            "55",
            "--expected-document-name",
            "model.cif",
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["window"] == {"handle": 100}
    assert calls == [
        {
            "window_handle": 100,
            "expected_window_title": "fit_project - Materials Studio",
            "expected_window_pid": 55,
            "expected_document_name": "model.cif",
        }
    ]
