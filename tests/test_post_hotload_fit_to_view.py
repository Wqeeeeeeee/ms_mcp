from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.natural_language import (
    fit_to_view_requested,
    infer_modeling_plan,
)
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore


def _silicon_spec(project_id: str) -> ModelSpec:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
        ).read_text(encoding="utf-8")
    )
    return ModelSpec.model_validate(
        {
            **payload,
            "project_id": project_id,
            "revision": 0,
        }
    )


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


class _LiveGui:
    def __init__(
        self,
        workspace: Path,
        *,
        fit_status: str = "executed",
        fail_final_snapshot: bool = False,
    ) -> None:
        self.workspace = workspace
        self.fit_status = fit_status
        self.fail_final_snapshot = fail_final_snapshot
        self.calls: list[tuple[str, dict]] = []
        self.fit_transaction: dict | None = None

    def _window(self, project_id: str, revision: int) -> dict:
        return {
            "handle": 701,
            "title": f"msmcp_r{revision:03d}_{project_id} - Materials Studio",
            "pid": 1701,
            "is_visible": True,
            "is_minimized": False,
            "is_selected": True,
            "is_foreground": True,
            "project_id": project_id,
            "revision": revision,
        }

    def status(self, *, project_id: str, revision: int) -> dict:
        self.calls.append(("status", {"project_id": project_id, "revision": revision}))
        window = self._window(project_id, revision)
        return {
            "ok": True,
            "supported": True,
            "window_found": True,
            "window": window,
            "windows": [window],
            "selected_window_handle": window["handle"],
            "single_window_policy_ok": True,
            "single_window_violation_reasons": [],
            "target_window_resolution": {
                "matched_project_window": True,
                "matching_window_count": 1,
                "target_handle": window["handle"],
                "target_title": window["title"],
                "fallback_used": False,
            },
            "window_management": {
                "single_window_policy_ok": True,
                "single_window_violation_reasons": [],
                "matched_project_window": True,
                "matching_window_identity_verification": "verified",
                "target_window_is_selected": True,
                "target_window_is_visible": True,
                "target_window_is_minimized": False,
                "target_window_foreground_observed": True,
                "target_window_is_foreground": True,
                "activation_required_before_capture_or_input": False,
                "can_apply_current_revision_without_new_window": True,
            },
        }

    def snapshot(self, *, label: str, project_id: str, revision: int) -> dict:
        self.calls.append(
            (
                "snapshot",
                {"label": label, "project_id": project_id, "revision": revision},
            )
        )
        if self.fail_final_snapshot and label == "post_hotload_fit_to_view":
            raise RuntimeError("final snapshot unavailable")
        path = self.workspace / "screenshots" / f"{label}.bmp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_tiny_bmp())
        return {
            "project_id": project_id,
            "revision": revision,
            "label": label,
            "window": self._window(project_id, revision),
            "screenshot_path": str(path),
            "analysis": {
                "readable": True,
                "likely_nonblank": True,
                "viewport_analysis_available": True,
                "viewport_likely_visible_model": True,
                "viewport_capture_limitation_possible": False,
                "viewport_capture_diagnostic": "model_pixels_detected",
            },
        }

    def open_structure(
        self,
        structure_path: str | Path,
        *,
        project_id: str,
        revision: int,
        take_snapshot: bool,
    ) -> dict:
        path = Path(structure_path)
        assert path.exists()
        self.calls.append(
            (
                "open_structure",
                {
                    "structure_path": str(path),
                    "project_id": project_id,
                    "revision": revision,
                    "take_snapshot": take_snapshot,
                },
            )
        )
        result = {
            "project_id": project_id,
            "revision": revision,
            "structure_path": str(path),
            "window": self._window(project_id, revision),
            "open_result": {"method": "same_window_fake", "path": str(path)},
            "reuse_existing_window_only": True,
            "same_window_open_supported": True,
            "same_window_open_used": True,
            "single_window_policy_ok": True,
            "single_window_violation_reasons": [],
            "post_open_single_window_policy_ok": True,
            "post_open_single_window_violation_reasons": [],
        }
        if take_snapshot:
            result["snapshot"] = self.snapshot(
                label="open_structure",
                project_id=project_id,
                revision=revision,
            )
        return result

    def fit_to_view(
        self,
        *,
        project_id: str,
        revision: int,
        execution_mode: str,
        take_snapshot: bool,
    ) -> dict:
        self.fit_transaction = server._ACTIVE_GUI_ARTIFACT_REPORT_TRANSACTION.get()
        self.calls.append(
            (
                "fit_to_view",
                {
                    "project_id": project_id,
                    "revision": revision,
                    "execution_mode": execution_mode,
                    "take_snapshot": take_snapshot,
                },
            )
        )
        assert execution_mode == "execute"
        assert take_snapshot is False
        assert self.fit_transaction is not None
        if self.fit_status != "executed":
            return {
                "project_id": project_id,
                "revision": revision,
                "execution_mode": execution_mode,
                "status": self.fit_status,
                "execution_ready": False,
                "gui_input_performed": False,
                "gui_modified": False,
                "structure_modified": False,
                "structure_unchanged": True,
                "recommended_tool": "material_studio_gui_status",
                "recommended_action": "resolve_fit_to_view_preflight_then_retry",
            }
        return {
            "project_id": project_id,
            "revision": revision,
            "execution_mode": execution_mode,
            "status": "executed",
            "execution_ready": True,
            "gui_input_performed": True,
            "gui_modified": True,
            "structure_modified": False,
            "structure_unchanged": True,
        }


def test_combined_patch_keeps_structural_plan_and_separate_fit_intent() -> None:
    spec = _silicon_spec("combined_fit_plan")
    request = (
        "Make a 2x1x1 supercell, hot-load it in Materials Studio, "
        "and fit the current model to view."
    )

    plan = infer_modeling_plan(request, current_spec=spec)

    assert plan.kind == "patch"
    assert plan.template_id == "crystal_supercell"
    assert plan.payload == {"operations": [{"type": "make_supercell", "matrix": [2, 1, 1]}]}
    assert fit_to_view_requested(request) is True
    assert fit_to_view_requested("export front, top, and isometric view parameters") is False


def test_combined_preview_defers_fit_without_gui_input(monkeypatch, tmp_path: Path) -> None:
    project_id = "combined_fit_preview"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Make a 2x1x1 supercell and fit the current model to view.",
        project_id=project_id,
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is True
    assert result["workflow"] == "patch"
    assert result["execution_mode"] == "preview"
    assert result["post_hotload_fit_to_view_requested"] is True
    assert result["post_hotload_fit_to_view_request_source"] == "natural_language"
    assert result["post_hotload_fit_to_view"]["status"] == "deferred_until_execute"
    assert result["post_hotload_fit_to_view"]["gui_input_performed"] is False
    assert result["post_hotload_fit_to_view"]["automatic_after_hotload"] is False
    assert all(name not in {"open_structure", "fit_to_view", "snapshot"} for name, _ in gui.calls)


def test_combined_execute_hotloads_fits_and_persists_final_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "combined_fit_execute"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        (
            "Make a 2x1x1 supercell, hot-load it in Materials Studio, "
            "and fit the current model to view."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "execute"
    assert result["execution_mode_source"] == "explicit_live_intent"
    assert result["new_revision"] == 1
    fit = result["post_hotload_fit_to_view"]
    assert fit["requested"] is True
    assert fit["request_source"] == "natural_language"
    assert fit["status"] == "executed"
    assert fit["completed"] is True
    assert fit["gui_input_performed"] is True
    assert fit["structure_unchanged"] is True
    assert Path(fit["after_snapshot_path"]).exists()
    assert "retry_tool" not in fit
    assert gui.fit_transaction is not None
    assert gui.fit_transaction["transaction"]["domain"] == "gui_artifact_report"
    assert "gui_fit_to_view" in result["gui_action_transaction"]["coverage"]

    action_names = [name for name, _ in gui.calls]
    assert action_names.index("open_structure") < action_names.index("fit_to_view")
    fit_index = action_names.index("fit_to_view")
    assert "snapshot" in action_names[fit_index + 1 :]

    report_path = Path(result["report_json_path"])
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    modeling_report = report_payload["modeling_report"]
    assert modeling_report["post_hotload_fit_to_view"]["completed"] is True
    assert modeling_report["gui"]["post_hotload_fit_to_view_completed"] is True
    assert modeling_report["gui"]["post_hotload_fit_to_view_final_snapshot_bound"] is True
    assert modeling_report["gui"]["snapshot_path"] == fit["after_snapshot_path"]
    assert modeling_report["live_summary"]["post_hotload_fit_to_view_completed"] is True

    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    assert compact["post_hotload_fit_to_view_requested"] is True
    assert compact["post_hotload_fit_to_view"]["completed"] is True
    assert compact["post_hotload_fit_to_view"]["final_snapshot_bound"] is True
    assert compact["post_hotload_fit_to_view"]["after_snapshot_path"] == fit["after_snapshot_path"]
    assert compact["live_summary"]["post_hotload_fit_to_view_completed"] is True


def test_explicit_false_overrides_combined_text_fit_intent() -> None:
    requested, source = server._resolve_post_hotload_fit_to_view(
        False,
        "Hot-load in Materials Studio and fit the current model to view.",
    )

    assert requested is False
    assert source == "explicit_parameter"


def test_requested_fit_block_returns_partial_success_and_exact_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "combined_fit_blocked"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    gui = _LiveGui(tmp_path, fit_status="blocked")
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Make a 2x1x1 supercell and hot-load it in Materials Studio.",
        project_id=project_id,
        fit_to_view_after_open=True,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is False
    assert result["partial_success"] is True
    assert result["status"] == "hotload_completed_fit_to_view_incomplete"
    assert result["result"]["success"] is True
    assert Path(result["planned_outputs"]["structure"]).exists()
    fit = result["post_hotload_fit_to_view"]
    assert fit["status"] == "blocked"
    assert fit["completed"] is False
    assert fit["gui_input_performed"] is False
    assert fit["retry_tool"] == "material_studio_gui_fit_to_view"
    assert fit["retry_payload"] == {
        "project_id": project_id,
        "revision": 1,
        "execution_mode": "execute",
        "take_snapshot": True,
        "working_dir": str(tmp_path),
    }
    assert result["post_hotload_fit_to_view_retry_payload"] == fit["retry_payload"]
    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    assert compact["partial_success"] is True
    assert compact["post_hotload_fit_to_view"]["completed"] is False
    assert compact["post_hotload_fit_to_view"]["retry_tool"] == (
        "material_studio_gui_fit_to_view"
    )
    assert compact["post_hotload_fit_to_view_retry_payload"] == fit["retry_payload"]
    assert not any(
        name == "snapshot" and call.get("label") == "post_hotload_fit_to_view"
        for name, call in gui.calls
    )


def test_executed_fit_without_final_snapshot_is_not_accepted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "combined_fit_snapshot_failure"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    gui = _LiveGui(tmp_path, fail_final_snapshot=True)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Make a 2x1x1 supercell and hot-load it in Materials Studio.",
        project_id=project_id,
        fit_to_view_after_open=True,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is False
    assert result["partial_success"] is True
    assert result["status"] == "hotload_completed_fit_to_view_evidence_incomplete"
    assert result["result"]["success"] is True
    fit = result["post_hotload_fit_to_view"]
    assert fit["action_completed"] is True
    assert fit["status"] == "executed_evidence_incomplete"
    assert fit["completed"] is False
    assert fit["final_snapshot_bound"] is False
    assert fit["structure_unchanged"] is True
    assert fit["snapshot_warning"] == "final snapshot unavailable"
    assert fit["retry_tool"] == "material_studio_gui_fit_to_view"
    assert fit["retry_payload"]["take_snapshot"] is True
    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    assert compact["post_hotload_fit_to_view"]["action_completed"] is True
    assert compact["post_hotload_fit_to_view"]["final_snapshot_bound"] is False
