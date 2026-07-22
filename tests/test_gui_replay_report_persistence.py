from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server
from material_studio_mcp_server.gui import (
    MaterialsStudioGuiController,
    ProcessInfo,
    WindowInfo,
)
from material_studio_mcp_server.specs import ExecutionMode
from material_studio_mcp_server.state.store import ProjectStore


REPLAY_VIEWS = [
    "front",
    "crystal_plane_0001",
    "top",
    "isometric",
    "crystal_100",
    "crystal_plane_001",
]


class FakeGuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=101,
            title="msmcp_r000_replay - Materials Studio",
            pid=2222,
            rect=(0, 0, 1024, 768),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=2222)]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and pid != self.window.pid:
            return None
        return self.window

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and pid != self.window.pid:
            return []
        return [self.window]

    def activate_window(self, window: WindowInfo) -> bool:
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        return output_path

    def open_file(self, path: Path) -> dict:
        return {"method": "fake", "path": str(path)}


def _tiny_bmp() -> bytes:
    width = 2
    height = 2
    row_stride = 8
    pixel_offset = 54
    image_size = row_stride * height
    file_size = pixel_offset + image_size
    header = (
        b"BM"
        + file_size.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + pixel_offset.to_bytes(4, "little")
    )
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + image_size.to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    bottom_row = bytes([0, 0, 0, 255, 255, 255, 0, 0])
    top_row = bytes([0, 0, 255, 0, 255, 0, 0, 0])
    return header + dib + bottom_row + top_row


def _prepare_replay_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, MaterialsStudioGuiController, Path, list[dict]]:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/"
            "molybdenum_disulfide_2d_mos2_monolayer_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = "snapshot_replay_persistence"
    created = server.material_studio_model_create_from_spec(
        payload,
        execution_mode=ExecutionMode.EXECUTE,
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    exported = server.material_studio_model_export_view_bundle(
        project_id=created["project_id"],
        views=REPLAY_VIEWS,
        include_gui_snapshot=False,
        working_dir=str(tmp_path),
    )
    assert exported["ok"] is True

    store = ProjectStore(tmp_path)
    output_dir = store.outputs_dir(created["project_id"], created["revision"])
    manifest_path = output_dir / "gui_view_replay_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "project_id": created["project_id"],
                "revision": created["revision"],
                "spec_fingerprint": exported["view_audit"]["spec_fingerprint"],
                "replay_status": "complete",
                "view_names": REPLAY_VIEWS,
                "requested_view_count": len(REPLAY_VIEWS),
                "supported_view_count": len(REPLAY_VIEWS),
                "replay_summary": {
                    "trusted_accepted_event_count": len(REPLAY_VIEWS),
                    "accepted_view_count": len(REPLAY_VIEWS),
                    "accepted_view_names": REPLAY_VIEWS,
                    "evidence_integrity_status": "verified",
                    "journal_consistency_status": "consistent",
                    "all_supported_views_confirmed": True,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    refresh_calls: list[dict] = []

    def record_refresh(manifest: dict, **kwargs: object) -> None:
        refresh_calls.append({"manifest": manifest, **kwargs})

    monkeypatch.setattr(server, "_refresh_view_replay_summary", record_refresh)
    gui = MaterialsStudioGuiController(str(tmp_path), backend=FakeGuiBackend())
    return created, gui, manifest_path, refresh_calls


def _snapshot(tmp_path: Path) -> dict:
    path = tmp_path / "snapshot.bmp"
    path.write_bytes(_tiny_bmp())
    return {
        "screenshot_path": str(path),
        "analysis": {"readable": True, "likely_nonblank": True},
        "window": {
            "handle": 101,
            "title": "msmcp_r000_replay - Materials Studio",
            "is_foreground": True,
        },
    }


@pytest.mark.parametrize("operation", ["snapshot", "open", "confirmation"])
def test_gui_report_updates_preserve_current_trusted_view_replay(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, refresh_calls = _prepare_replay_project(
        tmp_path,
        monkeypatch,
    )
    project_id = created["project_id"]
    revision = created["revision"]
    structure_path = Path(created["planned_outputs"]["structure"])

    if operation == "snapshot":
        result = server._persist_gui_snapshot_report(
            project_id=project_id,
            revision=revision,
            snapshot=_snapshot(tmp_path),
            gui=gui,
            working_dir=str(tmp_path),
            views=None,
        )
    elif operation == "open":
        gui_open = {
            "project_id": project_id,
            "revision": revision,
            "structure_path": str(structure_path),
            "activated_opened_window": True,
            "window": {
                "handle": 101,
                "title": "msmcp_r000_replay - Materials Studio",
            },
            "open_result": {"method": "fake", "path": str(structure_path)},
        }
        result = server._persist_gui_open_structure_report(
            project_id=project_id,
            revision=revision,
            gui_open=gui_open,
            gui=gui,
            working_dir=str(tmp_path),
            views=None,
            gui_artifacts=[{"type": "gui_open", "result": gui_open}],
        )
    else:
        result = server._persist_gui_visual_confirmation_report(
            project_id=project_id,
            revision=revision,
            confirmation={
                "project_id": project_id,
                "revision": revision,
                "model_visible": True,
                "structure_unchanged": True,
                "note": "current replay remains trusted",
            },
            gui_status=gui.status(project_id=project_id, revision=revision),
            working_dir=str(tmp_path),
            views=None,
        )

    assert refresh_calls
    assert refresh_calls[-1]["manifest"]["project_id"] == project_id
    assert result["gui_view_replay"]["binding_verified"] is True
    assert result["trusted_clean_view_replay"]["ok"] is True
    assert result["trusted_clean_view_replay"]["view_selection_matches"] is True
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["gui_view_replay"]["binding_verified"] is True
    assert report["trusted_clean_view_replay"]["ok"] is True
    assert report["modeling_report"]["trusted_clean_view_replay"]["ok"] is True


def test_snapshot_report_does_not_trust_a_different_view_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, gui, manifest_path, _ = _prepare_replay_project(tmp_path, monkeypatch)

    result = server._persist_gui_snapshot_report(
        project_id=created["project_id"],
        revision=created["revision"],
        snapshot=_snapshot(tmp_path),
        gui=gui,
        working_dir=str(tmp_path),
        views=["front"],
    )

    trusted = result["trusted_clean_view_replay"]
    assert trusted["ok"] is False
    assert trusted["view_selection_matches"] is False
    assert "view_replay_view_selection_mismatch" in trusted["blocking_reasons"]
    report = json.loads(
        (manifest_path.parent / "report.json").read_text(encoding="utf-8")
    )
    assert report["trusted_clean_view_replay"]["ok"] is False
