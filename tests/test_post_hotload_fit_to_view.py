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
        fail_prepare_view_replay: bool = False,
    ) -> None:
        self.workspace = workspace
        self.fit_status = fit_status
        self.fail_final_snapshot = fail_final_snapshot
        self.fail_prepare_view_replay = fail_prepare_view_replay
        self.calls: list[tuple[str, dict]] = []
        self.fit_transaction: dict | None = None
        self.prepare_transaction: dict | None = None
        self.inactive = False

    def _window(self, project_id: str, revision: int) -> dict:
        return {
            "handle": 701,
            "title": f"msmcp_r{revision:03d}_{project_id} - Materials Studio",
            "pid": 1701,
            "is_visible": True,
            "is_minimized": self.inactive,
            "is_selected": not self.inactive,
            "is_foreground": not self.inactive,
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
                "target_window_is_selected": not self.inactive,
                "target_window_is_visible": True,
                "target_window_is_minimized": self.inactive,
                "target_window_foreground_observed": True,
                "target_window_is_foreground": not self.inactive,
                "activation_required_before_capture_or_input": self.inactive,
                "interaction_activation_reasons": (
                    ["target_window_minimized", "target_window_not_foreground"]
                    if self.inactive
                    else []
                ),
                "can_apply_current_revision_without_new_window": not self.inactive,
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
        reuse_existing_window_only: bool = True,
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
                    "reuse_existing_window_only": reuse_existing_window_only,
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

    def prepare_view_replay(
        self,
        audit: dict,
        *,
        project_id: str,
        revision: int,
    ) -> dict:
        self.prepare_transaction = (
            server._ACTIVE_GUI_ARTIFACT_REPORT_TRANSACTION.get()
        )
        view_names = [
            str(view["name"])
            for view in audit.get("views") or []
            if isinstance(view, dict) and view.get("name")
        ]
        self.calls.append(
            (
                "prepare_view_replay",
                {
                    "project_id": project_id,
                    "revision": revision,
                    "view_names": view_names,
                },
            )
        )
        assert self.prepare_transaction is None
        if self.fail_prepare_view_replay:
            raise RuntimeError("view replay preparation unavailable")
        manifest_path = (
            self.workspace
            / project_id
            / "outputs"
            / f"r{revision:03d}"
            / "gui_view_replay_manifest.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "project_id": project_id,
                    "revision": revision,
                    "view_names": view_names,
                }
            ),
            encoding="utf-8",
        )
        next_view = view_names[0] if view_names else None
        continuation = {
            "status": "automatic_recipe_ready",
            "next_pending_view_name": next_view,
            "next_actionable_pending_view_name": next_view,
            "next_automation_ready_view_name": next_view,
            "recommended_action": "preview_next_view_replay",
            "recommended_mcp_tool": (
                "material_studio_gui_execute_view_replay"
            ),
            "automatic_replay_ready": bool(next_view),
            "gui_input_required": True,
            "needs_user_confirmation": True,
            "safe_to_call_without_confirmation": False,
            "payload_hint": {
                "project_id": project_id,
                "revision": revision,
                "view_name": next_view,
                "execution_mode": "preview",
            },
            "payload_hint_is_directly_callable": True,
        }
        return {
            "project_id": project_id,
            "revision": revision,
            "manifest_path": str(manifest_path),
            "gui_log_path": str(manifest_path.with_name("gui_operations.jsonl")),
            "replay_status": "prepared",
            "ready_for_external_replay": True,
            "preflight_block_reasons": [],
            "view_selection": audit.get("view_selection"),
            "view_names": view_names,
            "requested_view_count": len(view_names),
            "supported_view_count": len(view_names),
            "unsupported_view_count": 0,
            "replay_continuation": continuation,
            "recipe_contract": {
                "status": "current",
                "current": True,
                "pending_recipe_upgrade_required": False,
            },
            "next_action": {
                "continuation_status": continuation["status"],
                "recommended_tool": continuation["recommended_mcp_tool"],
                "recommended_action": continuation["recommended_action"],
                "payload_hint": continuation["payload_hint"],
                "payload_hint_is_directly_callable": True,
                "needs_user_confirmation": True,
                "safe_to_call_without_confirmation": False,
            },
            "next_action_resolution": {
                "status": "resolved",
                "resolved_tool": continuation["recommended_mcp_tool"],
                "resolved_action": continuation["recommended_action"],
                "safety_gate": {
                    "automatic_replay_allowed": True,
                    "structure_mutation_allowed": False,
                    "revision_creation_allowed": False,
                    "record_tool_call_ready": False,
                },
            },
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


def test_explicit_current_reload_wins_over_display_only_fit_routing() -> None:
    spec = _silicon_spec("session_fit_route")

    pure_fit = infer_modeling_plan(
        "Fit the current model to view in Materials Studio.",
        current_spec=spec,
    )
    reload_and_fit = infer_modeling_plan(
        (
            "Reload the current revision in Materials Studio and fit the "
            "current model to view."
        ),
        current_spec=spec,
    )

    assert pure_fit.kind == "fit_to_view"
    assert reload_and_fit.kind == "show_current"
    assert reload_and_fit.template_id == "show_current_revision"
    assert fit_to_view_requested(
        "Reload the current revision in Materials Studio and fit the current model to view."
    ) is True
    fit_policy = server._live_capabilities_payload()["gui"][
        "fit_to_view_policy"
    ]
    assert fit_policy["combined_session_workflows"] == [
        "create",
        "patch",
        "show_current",
        "rollback",
        "redo",
        "restore",
        "gui_apply_current_revision",
    ]


def test_show_current_preview_defers_fit_and_execute_reuses_current_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_live_modeling_request(
        "Build silicon diamond crystal.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    assert created["ok"] is True
    project_id = created["project_id"]
    gui.calls.clear()

    preview = server.material_studio_live_modeling_request(
        (
            "Reload the current revision in Materials Studio and fit the "
            "current model to view."
        ),
        project_id=project_id,
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert preview["ok"] is True
    assert preview["workflow"] == "show_current"
    assert preview["nl_plan"]["kind"] == "show_current"
    assert preview["revision"] == 0
    assert preview["post_hotload_fit_to_view_request_source"] == "natural_language"
    assert preview["post_hotload_fit_to_view"]["status"] == "deferred_until_execute"
    assert preview["post_hotload_fit_to_view"]["gui_input_performed"] is False
    assert all(
        name not in {"open_structure", "fit_to_view", "snapshot"}
        for name, _ in gui.calls
    )

    gui.calls.clear()
    executed = server.material_studio_live_modeling_request(
        (
            "Reload the current revision, hot-load it in Materials Studio, "
            "and fit the current model to view."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert executed["ok"] is True
    assert executed["workflow"] == "show_current"
    assert executed["execution_mode"] == "execute"
    assert executed["execution_mode_source"] == "explicit_live_intent"
    assert executed["revision"] == 0
    fit = executed["post_hotload_fit_to_view"]
    assert fit["request_source"] == "natural_language"
    assert fit["completed"] is True
    assert fit["final_snapshot_bound"] is True
    assert "workflow:show_current" in executed["gui_action_transaction"]["coverage"]
    assert "gui_fit_to_view" in executed["gui_action_transaction"]["coverage"]
    action_names = [name for name, _ in gui.calls]
    assert action_names.index("open_structure") < action_names.index("fit_to_view")
    assert "snapshot" in action_names[action_names.index("fit_to_view") + 1 :]
    store = ProjectStore(tmp_path)
    assert store.load_current(project_id).revision == 0
    assert len(store.list_history(project_id)) == 1


def test_rollback_redo_and_restore_fit_after_each_same_window_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_live_modeling_request(
        "Build silicon diamond crystal.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    project_id = created["project_id"]
    patched = server.material_studio_live_modeling_request(
        "Make a 2x1x1 supercell.",
        project_id=project_id,
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    assert patched["ok"] is True
    assert patched["revision"] == 1
    gui.calls.clear()

    rolled_back = server.material_studio_live_modeling_request(
        (
            "Undo the last change, hot-load it in Materials Studio, and fit "
            "the current model to view."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    assert rolled_back["ok"] is True
    assert rolled_back["workflow"] == "rollback"
    assert rolled_back["target_revision"] == 0
    assert rolled_back["new_revision"] == 2
    assert rolled_back["post_hotload_fit_to_view_request_source"] == "natural_language"
    assert rolled_back["post_hotload_fit_to_view"]["completed"] is True
    assert rolled_back["modeling_report"]["post_hotload_fit_to_view"]["completed"] is True

    redone = server.material_studio_live_modeling_request(
        (
            "Redo the last change, hot-load it in Materials Studio, and fit "
            "the current model to view."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    assert redone["ok"] is True
    assert redone["workflow"] == "redo"
    assert redone["target_revision"] == 1
    assert redone["new_revision"] == 3
    assert redone["post_hotload_fit_to_view"]["request_source"] == "natural_language"
    assert redone["post_hotload_fit_to_view"]["completed"] is True

    restored = server.material_studio_live_modeling_request(
        (
            "Restore r000, hot-load it in Materials Studio, and fit the "
            "current model to view."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=True,
    )
    assert restored["ok"] is True
    assert restored["workflow"] == "rollback"
    assert restored["nl_plan"]["template_id"] == "restore_revision"
    assert restored["target_revision"] == 0
    assert restored["new_revision"] == 4
    assert restored["post_hotload_fit_to_view"]["completed"] is True
    assert restored["post_hotload_fit_to_view"]["final_snapshot_bound"] is True
    assert "workflow:rollback" in restored["gui_action_transaction"]["coverage"]
    assert "gui_fit_to_view" in restored["gui_action_transaction"]["coverage"]

    history = ProjectStore(tmp_path).list_history(project_id)
    assert [item["revision"] for item in history] == [0, 1, 2, 3, 4]
    assert [item["action"] for item in history] == [
        "create",
        "live_patch",
        "rollback:r000",
        "rollback:r001",
        "rollback:r000",
    ]
    assert [name for name, _ in gui.calls].count("fit_to_view") == 3


def test_post_hotload_view_replay_prepare_resolution_requires_live_view_intent() -> None:
    requested, source = server._resolve_post_hotload_view_replay_prepare(
        None,
        "Hot-load the current model and export front and top view parameters.",
        views=["front", "top"],
    )
    assert requested is True
    assert source == "natural_language_views"

    requested, source = server._resolve_post_hotload_view_replay_prepare(
        None,
        "Hot-load the current model and check whether the model is normal.",
        views=None,
    )
    assert requested is True
    assert source == "natural_language_normality_check"

    requested, source = server._resolve_post_hotload_view_replay_prepare(
        None,
        "Export front and top view parameters.",
        views=["front", "top"],
    )
    assert requested is False
    assert source == "default_disabled"

    requested, source = server._resolve_post_hotload_view_replay_prepare(
        False,
        "Hot-load the current model and export front and top view parameters.",
        views=["front", "top"],
    )
    assert requested is False
    assert source == "explicit_parameter"


def test_post_hotload_view_replay_preview_is_deferred_without_gui_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_preview"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        (
            "Hot-load this silicon model in Materials Studio and export front, "
            "top, and isometric view parameters."
        ),
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "preview"
    assert result["post_hotload_view_replay_prepare_requested"] is True
    assert result["post_hotload_view_replay_prepare_request_source"] == (
        "natural_language_views"
    )
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "deferred_until_execute"
    assert prepare["prepared"] is False
    assert prepare["gui_input_performed"] is False
    assert prepare["automatic_after_hotload"] is False
    assert all(
        name not in {"open_structure", "fit_to_view", "snapshot", "prepare_view_replay"}
        for name, _ in gui.calls
    )


def test_post_hotload_view_replay_executes_open_fit_then_prepares_exact_views(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_execute"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        (
            "Hot-load this silicon model in Materials Studio, fit the current "
            "model to view, and export front, top, and isometric view parameters "
            "to check whether the model is normal."
        ),
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "execute"
    assert result["post_hotload_fit_to_view"]["completed"] is True
    assert result["post_hotload_view_replay_prepare_requested"] is True
    assert result["post_hotload_view_replay_prepare_request_source"] == (
        "natural_language_views"
    )
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "prepared"
    assert prepare["prepared"] is True
    assert prepare["view_names"] == ["front", "top", "isometric"]
    assert prepare["requested_view_count"] == 3
    assert prepare["supported_view_count"] == 3
    assert prepare["unsupported_view_count"] == 0
    assert prepare["prepared_after_gui_artifact_transaction"] is True
    assert prepare["report_rewritten_after_prepare"] is False
    assert prepare["gui_input_performed"] is False
    assert prepare["gui_modified"] is False
    assert prepare["structure_modified"] is False
    assert prepare["revision_created"] is False
    assert Path(prepare["manifest_path"]).exists()
    assert result["view_replay_prepared"] is True
    assert result["view_replay_continuation"]["status"] == (
        "automatic_recipe_ready"
    )
    assert prepare["current_revision_verified_after_prepare"] is True
    assert prepare["replay_continuation_published"] is True
    assert prepare["visual_diagnostics_action_published"] is True
    assert gui.prepare_transaction is None

    modeling_action = result["next_action_plan"]
    assert modeling_action["action_id"] == "continue_live_modeling"
    assert result["modeling_next_action_plan"]["action_id"] == (
        modeling_action["action_id"]
    )
    visual_action = result["visual_diagnostics_next_action_plan"]
    assert visual_action["project_id"] == project_id
    assert visual_action["revision"] == 0
    assert visual_action["action_scope"] == "visual_diagnostics"
    assert visual_action["recommended_tool"] == (
        "material_studio_gui_execute_view_replay"
    )
    assert visual_action["payload_hint"] == {
        "project_id": project_id,
        "revision": 0,
        "view_name": "front",
        "execution_mode": "preview",
        "working_dir": str(tmp_path),
    }
    coordinated = result["coordinated_next_action_plan"]
    assert coordinated["primary_track"] == "visual_diagnostics"
    assert coordinated["next_action_plan_preserved"] is True
    assert [
        step["track"] for step in coordinated["recommended_sequence"]
    ] == ["visual_diagnostics", "modeling"]
    assert result["next_action_tracks"][
        "visual_diagnostics_action_does_not_clear_modeling_action"
    ] is True
    assert result["next_action_tracks"][
        "next_action_plan_preserved_as_modeling_authority"
    ] is True

    action_names = [name for name, _ in gui.calls]
    assert action_names.index("open_structure") < action_names.index("fit_to_view")
    assert action_names.index("fit_to_view") < action_names.index(
        "prepare_view_replay"
    )
    assert any(
        name == "snapshot" and call.get("label") == "post_hotload_fit_to_view"
        for name, call in gui.calls[: action_names.index("prepare_view_replay")]
    )
    assert "view_replay_prepare" not in result["gui_action_transaction"]["coverage"]

    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    compact_prepare = compact["post_hotload_view_replay_prepare"]
    assert compact_prepare["status"] == "prepared"
    assert compact_prepare["manifest_path"] == prepare["manifest_path"]
    assert compact_prepare["view_names"] == ["front", "top", "isometric"]
    assert compact_prepare["replay_continuation"]["status"] == (
        "automatic_recipe_ready"
    )
    assert compact_prepare["recipe_contract"]["status"] == "current"
    assert compact["next_action_plan"]["action_id"] == "continue_live_modeling"
    assert compact["visual_diagnostics_next_action_plan"]["action_id"] == (
        "preview_gui_view_replay"
    )
    assert compact["coordinated_next_action_plan"][
        "recommended_sequence"
    ] == coordinated["recommended_sequence"]
    assert compact["next_action_tracks"]["recommended_sequence_ref"] == (
        "coordinated_next_action_plan.recommended_sequence"
    )
    compact_size = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    assert compact_size == compact["response_compaction"]["response_bytes"]
    assert compact_size < server.COMPACT_RESPONSE_MAX_BYTES
    assert compact["response_compaction"]["semantic_core_preserved"] is True


def test_postexecution_activation_retry_resumes_full_postopen_pipeline_without_rerun(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "postexecution_postopen_resume"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id).model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    original_execute = server._execute_or_materialize_structure
    execution_revisions: list[int] = []

    def execute_then_lose_focus(*args, **kwargs):
        execution = original_execute(*args, **kwargs)
        execution_revisions.append(int(kwargs["spec"].revision))
        gui.inactive = True
        return execution

    monkeypatch.setattr(
        server,
        "_execute_or_materialize_structure",
        execute_then_lose_focus,
    )

    blocked = server.material_studio_gui_apply_current_revision(
        project_id=project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=True,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        views=["front", "top", "isometric"],
        working_dir=str(tmp_path),
    )

    assert blocked["ok"] is False
    assert blocked["status"] == "execution_completed_gui_activation_required"
    assert blocked["execution_must_not_repeat"] is True
    assert execution_revisions == [0]
    assert not any(
        name in {"open_structure", "fit_to_view", "snapshot", "prepare_view_replay"}
        for name, _ in gui.calls
    )
    retry_payload = blocked["gui_open_retry_payload"]
    assert retry_payload == {
        "structure_path": blocked["planned_outputs"]["structure"],
        "project_id": project_id,
        "revision": 0,
        "take_snapshot": True,
        "export_view_audit": True,
        "reuse_existing_window_only": True,
        "fit_to_view_after_open": True,
        "prepare_view_replay_after_open": True,
        "views": ["front", "top", "isometric"],
        "working_dir": str(tmp_path),
    }
    fit = blocked["post_hotload_fit_to_view"]
    assert fit["status"] == "deferred_until_activation_and_open"
    assert fit["completed"] is False
    assert fit["followup_tool"] == "material_studio_gui_open_structure"
    assert fit["followup_payload"] == retry_payload
    prepare = blocked["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "deferred_until_activation_and_open"
    assert prepare["prepared"] is False
    assert prepare["followup_tool"] == "material_studio_gui_open_structure"
    assert prepare["followup_payload"] == retry_payload
    assert blocked["next_action_plan"]["deferred_hotload_action"][
        "payload_hint"
    ] == retry_payload

    result_metadata_path = Path(blocked["result_metadata_path"])
    result_metadata_before = result_metadata_path.read_bytes()
    original_execution_transaction = blocked["execution_transaction"]
    gui.inactive = False
    resumed = server.material_studio_gui_open_structure(**retry_payload)

    assert resumed["ok"] is True
    assert execution_revisions == [0]
    assert result_metadata_path.read_bytes() == result_metadata_before
    assert resumed["execution_transaction"] == original_execution_transaction
    assert resumed["post_hotload_fit_to_view"]["completed"] is True
    assert resumed["post_hotload_fit_to_view"]["structure_unchanged"] is True
    assert Path(
        resumed["post_hotload_fit_to_view"]["after_snapshot_path"]
    ).exists()
    resumed_prepare = resumed["post_hotload_view_replay_prepare"]
    assert resumed_prepare["status"] == "prepared"
    assert resumed_prepare["prepared"] is True
    assert resumed_prepare["view_names"] == ["front", "top", "isometric"]
    assert resumed["view_replay_prepared"] is True
    assert gui.fit_transaction is not None
    assert gui.prepare_transaction is None
    action_names = [name for name, _ in gui.calls]
    assert action_names.index("open_structure") < action_names.index("fit_to_view")
    assert action_names.index("fit_to_view") < action_names.index(
        "prepare_view_replay"
    )
    final_snapshot_index = next(
        index
        for index, (name, call) in enumerate(gui.calls)
        if name == "snapshot" and call.get("label") == "post_hotload_fit_to_view"
    )
    assert action_names.index("fit_to_view") < final_snapshot_index
    assert final_snapshot_index < action_names.index("prepare_view_replay")
    assert len(ProjectStore(tmp_path).list_history(project_id)) == 1

    report = json.loads(
        Path(resumed["report_json_path"]).read_text(encoding="utf-8")
    )
    assert report["execution_transaction"] == original_execution_transaction
    assert report["modeling_report"]["post_hotload_fit_to_view"][
        "completed"
    ] is True
    assert report["modeling_report"]["gui_postexecution_block"] is None


def test_artifact_postopen_retry_preserves_full_payload_until_window_is_active(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "postexecution_postopen_still_inactive"
    gui = _LiveGui(tmp_path)
    gui.inactive = True
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id).model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    structure_path = Path(created["planned_outputs"]["structure"])
    structure_path.parent.mkdir(parents=True, exist_ok=True)
    structure_path.write_text("data_model\n", encoding="utf-8")

    blocked = server.material_studio_gui_open_structure(
        structure_path=str(structure_path),
        project_id=project_id,
        revision=0,
        take_snapshot=True,
        export_view_audit=True,
        reuse_existing_window_only=True,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        views=["front", "top"],
        working_dir=str(tmp_path),
    )

    expected_retry = {
        "structure_path": str(structure_path),
        "project_id": project_id,
        "revision": 0,
        "take_snapshot": True,
        "export_view_audit": True,
        "reuse_existing_window_only": True,
        "fit_to_view_after_open": True,
        "prepare_view_replay_after_open": True,
        "views": ["front", "top"],
        "working_dir": str(tmp_path),
    }
    assert blocked["ok"] is False
    assert blocked["status"] == "gui_activation_required_before_open"
    assert blocked["gui_open_retry_payload"] == expected_retry
    assert blocked["post_hotload_fit_to_view"]["followup_payload"] == (
        expected_retry
    )
    assert blocked["post_hotload_view_replay_prepare"][
        "followup_payload"
    ] == expected_retry
    assert not any(
        name in {"open_structure", "fit_to_view", "snapshot", "prepare_view_replay"}
        for name, _ in gui.calls
    )


def test_artifact_postopen_retry_refuses_superseded_revision_before_gui_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "postexecution_postopen_superseded"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id).model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    structure_path = Path(created["planned_outputs"]["structure"])
    structure_path.parent.mkdir(parents=True, exist_ok=True)
    structure_path.write_text("data_model\n", encoding="utf-8")
    initially_opened = server.material_studio_gui_open_structure(
        structure_path=str(structure_path),
        project_id=project_id,
        revision=0,
        take_snapshot=False,
        working_dir=str(tmp_path),
    )
    assert initially_opened["ok"] is True
    report_path = Path(initially_opened["report_json_path"])
    committed_report = report_path.read_bytes()
    gui.calls.clear()
    store = ProjectStore(tmp_path)
    store.save_revision(
        project_id,
        store.load_current(project_id),
        user_text="concurrent revision before artifact retry",
        action="concurrent_test_revision",
        expected_revision=0,
    )

    blocked = server.material_studio_gui_open_structure(
        structure_path=str(structure_path),
        project_id=project_id,
        revision=0,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        working_dir=str(tmp_path),
    )

    assert blocked["ok"] is False
    assert blocked["status"] == "current_revision_hotload_block"
    current_block = blocked["current_revision_hotload_block"]
    assert current_block["target_revision"] == 0
    assert current_block["current_revision"] == 1
    assert "current_revision_advanced_before_artifact_retry" in current_block[
        "blocking_reasons"
    ]
    assert blocked["gui_input_started"] is False
    assert blocked["structure_reopened"] is False
    assert gui.calls == []
    assert report_path.read_bytes() == committed_report


def test_combined_postopen_pipeline_requires_structured_report_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    structure_path = tmp_path / "unbound.cif"
    structure_path.write_text("data_unbound\n", encoding="utf-8")

    def unexpected_gui_controller(working_dir=None):
        raise AssertionError("GUI must not be probed without structured context")

    monkeypatch.setattr(server, "_gui_controller", unexpected_gui_controller)
    blocked = server.material_studio_gui_open_structure(
        structure_path=str(structure_path),
        export_view_audit=False,
        fit_to_view_after_open=True,
        working_dir=str(tmp_path),
    )

    assert blocked["ok"] is False
    assert blocked["status"] == (
        "structured_context_required_for_postopen_pipeline"
    )
    assert blocked["gui_input_started"] is False
    assert blocked["structure_reopened"] is False


def test_post_hotload_view_replay_rejects_continuation_when_revision_advances_during_prepare(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_prepare_race"
    gui = _LiveGui(tmp_path)
    original_prepare = gui.prepare_view_replay

    def prepare_then_advance(
        audit: dict,
        *,
        project_id: str,
        revision: int,
    ) -> dict:
        prepared = original_prepare(
            audit,
            project_id=project_id,
            revision=revision,
        )
        store = ProjectStore(tmp_path)
        current = store.load_current(project_id)
        store.save_revision(
            project_id,
            current,
            user_text="concurrent revision during replay preparation",
            action="concurrent_test_revision",
            expected_revision=revision,
        )
        return prepared

    monkeypatch.setattr(gui, "prepare_view_replay", prepare_then_advance)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Hot-load this silicon model and export front and top view parameters.",
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="execute",
        working_dir=str(tmp_path),
        take_snapshot=False,
    )

    assert result["ok"] is False
    assert result["partial_success"] is True
    assert result["status"] == (
        "hotload_completed_view_replay_prepare_superseded"
    )
    assert result["result"]["success"] is True
    assert Path(result["planned_outputs"]["structure"]).exists()
    assert result["current_revision"] == 1
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "current_revision_advanced_during_prepare"
    assert prepare["prepared"] is False
    assert prepare["prepared_revision"] == 0
    assert prepare["superseded_revision"] == 0
    assert prepare["current_revision"] == 1
    assert prepare["current_revision_verified_after_prepare"] is False
    assert prepare["preparation_completed_for_superseded_revision"] is True
    assert prepare["historical_manifest_preserved"] is True
    assert Path(prepare["historical_manifest_path"]).exists()
    assert prepare["replay_continuation_published"] is False
    assert prepare["visual_diagnostics_action_published"] is False
    assert prepare["followup_tool"] == "material_studio_live_project_status"
    assert prepare["followup_payload"] == {
        "project_id": project_id,
        "include_gui_status": True,
        "response_mode": "compact",
        "working_dir": str(tmp_path),
    }
    assert "view_replay_continuation" not in result
    assert "visual_diagnostics_next_action_plan" not in result
    assert "coordinated_next_action_plan" not in result
    assert result["view_replay_prepared"] is False
    assert result["next_action_plan"]["action_id"] == (
        "refresh_current_project_after_replay_prepare_superseded"
    )
    assert result["next_action_plan"]["revision"] == 1
    assert result["next_action_plan"]["recommended_tool"] == (
        "material_studio_live_project_status"
    )
    assert result["next_action_plan"]["needs_user_confirmation"] is False
    assert [item["revision"] for item in ProjectStore(tmp_path).list_history(project_id)] == [
        0,
        1,
    ]

    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    assert "view_replay_continuation" not in compact
    assert "visual_diagnostics_next_action_plan" not in compact
    assert compact["next_action_plan"]["revision"] == 1
    assert compact["post_hotload_view_replay_prepare"][
        "historical_manifest_preserved"
    ] is True
    assert compact["response_compaction"]["semantic_core_preserved"] is True

    resumed = server.material_studio_live_project_status(
        project_id=project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert resumed["ok"] is True
    assert resumed["revision"] == 1
    assert resumed["gui_view_replay"]["manifest_exists"] is False


def test_post_hotload_view_replay_compact_budget_references_large_external_recipe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_large_recipe"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    result = server.material_studio_live_modeling_request(
        "Hot-load this silicon model and export front and top view parameters.",
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="execute",
        working_dir=str(tmp_path),
        take_snapshot=False,
        response_mode="full",
    )

    external_recipe = {
        "project_id": project_id,
        "revision": 0,
        "instructions": "external-observation-step:" + ("x" * 30_000),
    }
    continuation = result["view_replay_continuation"]
    continuation["payload_hint_is_directly_callable"] = False
    continuation["payload_hint"] = external_recipe
    continuation["execution_action"] = {
        "executor": "computer_use",
        "payload_hint": external_recipe,
        "payload_hint_is_directly_callable": False,
        "gui_input_required": True,
        "post_action_observation_required": True,
    }
    visual_action = result["visual_diagnostics_next_action_plan"]
    visual_action["payload_hint_is_directly_callable"] = False
    visual_action["payload_hint"] = external_recipe
    result["coordinated_next_action_plan"]["payload_hint"] = external_recipe

    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    compact_size = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    assert compact_size == compact["response_compaction"]["response_bytes"]
    assert compact_size < server.COMPACT_RESPONSE_MAX_BYTES
    assert compact["response_compaction"]["hard_budget_applied"] is True
    assert compact["response_compaction"]["semantic_core_preserved"] is True
    assert "view_replay_continuation.recipe_detail" in compact[
        "response_compaction"
    ]["omitted_fields"]
    assert compact["view_replay_continuation"]["continuation_detail_ref"] == (
        "full_response.view_replay_continuation"
    )
    assert compact["visual_diagnostics_next_action_plan"]["payload_hint_ref"] == (
        "full_response.view_replay_continuation.execution_action.payload_hint"
    )
    assert compact["coordinated_next_action_plan"]["payload_hint_ref"] == (
        "visual_diagnostics_next_action_plan.payload_hint_ref"
    )


def test_post_hotload_view_replay_explicit_false_overrides_inferred_views(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_disabled"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Hot-load this silicon model and export front and top view parameters.",
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="execute",
        prepare_view_replay_after_open=False,
        working_dir=str(tmp_path),
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["post_hotload_view_replay_prepare_requested"] is False
    assert result["post_hotload_view_replay_prepare_request_source"] == (
        "explicit_parameter"
    )
    assert result["post_hotload_view_replay_prepare"]["status"] == (
        "not_requested"
    )
    assert all(name != "prepare_view_replay" for name, _ in gui.calls)


def test_post_hotload_view_replay_execute_without_gui_returns_not_run_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_gui_disabled"
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Build this supplied silicon model.",
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="execute",
        open_in_gui=False,
        prepare_view_replay_after_open=True,
        views=["front", "top"],
        working_dir=str(tmp_path),
        take_snapshot=False,
    )

    assert result["ok"] is True
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "not_run_gui_open_disabled"
    assert prepare["prepared"] is False
    assert prepare["automatic_after_hotload"] is False
    assert prepare["view_names"] == ["front", "top"]
    assert all(
        name not in {"open_structure", "snapshot", "prepare_view_replay"}
        for name, _ in gui.calls
    )


def test_post_hotload_view_replay_prepare_failure_is_retryable_partial_success(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "post_hotload_replay_failure"
    gui = _LiveGui(tmp_path, fail_prepare_view_replay=True)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_modeling_request(
        "Hot-load this silicon model and export front and top view parameters.",
        spec=_silicon_spec(project_id).model_dump(mode="json"),
        execution_mode="execute",
        working_dir=str(tmp_path),
        take_snapshot=True,
    )

    assert result["ok"] is False
    assert result["partial_success"] is True
    assert result["status"] == "hotload_completed_view_replay_prepare_failed"
    assert result["result"]["success"] is True
    assert Path(result["planned_outputs"]["structure"]).exists()
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "prepare_failed"
    assert prepare["prepared"] is False
    assert prepare["prepared_after_gui_artifact_transaction"] is True
    assert prepare["report_rewritten_after_prepare"] is False
    assert prepare["retry_tool"] == "material_studio_gui_prepare_view_replay"
    assert prepare["retry_payload"] == {
        "project_id": project_id,
        "revision": 0,
        "views": ["front", "top"],
        "working_dir": str(tmp_path),
    }
    assert result["post_hotload_view_replay_prepare_retry_payload"] == (
        prepare["retry_payload"]
    )
    assert [name for name, _ in gui.calls].count("open_structure") == 1
    assert [name for name, _ in gui.calls].count("prepare_view_replay") == 1
    assert len(ProjectStore(tmp_path).list_history(project_id)) == 1

    compact = server._compact_live_response(result, server.McpResponseMode.COMPACT)
    assert compact["partial_success"] is True
    assert compact["post_hotload_view_replay_prepare"]["status"] == (
        "prepare_failed"
    )
    assert compact["post_hotload_view_replay_prepare_retry_payload"] == (
        prepare["retry_payload"]
    )


def test_show_current_and_direct_apply_prepare_view_replay_without_new_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_live_modeling_request(
        "Build silicon diamond crystal.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    project_id = created["project_id"]
    gui.calls.clear()

    shown = server.material_studio_live_modeling_request(
        (
            "Reload the current revision, hot-load it in Materials Studio, and "
            "export front and top view parameters to check whether the model is normal."
        ),
        project_id=project_id,
        working_dir=str(tmp_path),
        take_snapshot=False,
    )

    assert shown["ok"] is True
    assert shown["workflow"] == "show_current"
    assert shown["revision"] == 0
    assert shown["post_hotload_view_replay_prepare"]["status"] == "prepared"
    assert shown["post_hotload_view_replay_prepare"]["view_names"] == [
        "front",
        "top",
    ]
    assert len(ProjectStore(tmp_path).list_history(project_id)) == 1

    gui.calls.clear()
    applied = server.material_studio_gui_apply_current_revision(
        project_id=project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=False,
        prepare_view_replay_after_open=True,
        views=["isometric"],
        working_dir=str(tmp_path),
    )

    assert applied["ok"] is True
    assert applied["revision"] == 0
    assert applied["post_hotload_view_replay_prepare_request_source"] == (
        "explicit_parameter"
    )
    assert applied["post_hotload_view_replay_prepare"]["view_names"] == [
        "isometric"
    ]
    assert [name for name, _ in gui.calls].count("prepare_view_replay") == 1
    assert len(ProjectStore(tmp_path).list_history(project_id)) == 1


def test_rollback_redo_and_restore_prepare_replay_after_each_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)
    created = server.material_studio_live_modeling_request(
        "Build silicon diamond crystal.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    project_id = created["project_id"]
    patched = server.material_studio_live_modeling_request(
        "Make a 2x1x1 supercell.",
        project_id=project_id,
        execution_mode="preview",
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    assert patched["revision"] == 1
    gui.calls.clear()

    rolled_back = server.material_studio_live_modeling_request(
        "Undo the last change and hot-load it in Materials Studio.",
        project_id=project_id,
        prepare_view_replay_after_open=True,
        views=["front"],
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    assert rolled_back["ok"] is True
    assert rolled_back["workflow"] == "rollback"
    assert rolled_back["new_revision"] == 2
    assert rolled_back["post_hotload_view_replay_prepare"]["status"] == (
        "prepared"
    )

    redone = server.material_studio_live_modeling_request(
        "Redo the last change and hot-load it in Materials Studio.",
        project_id=project_id,
        prepare_view_replay_after_open=True,
        views=["front"],
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    assert redone["ok"] is True
    assert redone["workflow"] == "redo"
    assert redone["new_revision"] == 3
    assert redone["post_hotload_view_replay_prepare"]["status"] == "prepared"

    restored = server.material_studio_live_modeling_request(
        "Restore r000 and hot-load it in Materials Studio.",
        project_id=project_id,
        prepare_view_replay_after_open=True,
        views=["front"],
        working_dir=str(tmp_path),
        take_snapshot=False,
    )
    assert restored["ok"] is True
    assert restored["workflow"] == "rollback"
    assert restored["new_revision"] == 4
    assert restored["post_hotload_view_replay_prepare"]["status"] == "prepared"
    assert [name for name, _ in gui.calls].count("prepare_view_replay") == 3
    assert [item["revision"] for item in ProjectStore(tmp_path).list_history(project_id)] == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_direct_live_patch_prepares_replay_after_same_window_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "direct_patch_replay_prepare"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    gui = _LiveGui(tmp_path)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_live_update_with_patch(
        project_id=project_id,
        base_revision=0,
        patch={
            "project_id": project_id,
            "base_revision": 0,
            "operations": [{"type": "make_supercell", "matrix": [2, 1, 1]}],
        },
        user_text=(
            "Hot-load the updated model in Materials Studio and export front "
            "and top view parameters."
        ),
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["new_revision"] == 1
    assert result["post_hotload_view_replay_prepare_request_source"] == (
        "natural_language_views"
    )
    assert result["post_hotload_view_replay_prepare"]["status"] == "prepared"
    assert result["post_hotload_view_replay_prepare"]["view_names"] == [
        "front",
        "top",
    ]
    assert [name for name, _ in gui.calls].count("prepare_view_replay") == 1


def test_failed_rollback_returns_not_run_replay_prepare_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "failed_rollback_replay_prepare"
    ProjectStore(tmp_path).create_project(_silicon_spec(project_id), user_text="fixture")
    monkeypatch.setattr(
        server,
        "material_studio_project_rollback",
        lambda **kwargs: {"ok": False, "error": "rollback unavailable"},
    )

    result = server._handle_live_rollback_request(
        project_id=project_id,
        target_revision=999,
        user_request="Restore r999 and hot-load it in Materials Studio.",
        nl_plan={"kind": "rollback", "template_id": "restore_revision"},
        project_resolution=None,
        execution_mode=server.ExecutionMode.EXECUTE,
        execution_mode_source="explicit_live_intent",
        open_in_gui=True,
        take_snapshot=False,
        fit_to_view_after_open=False,
        fit_to_view_request_source="default_disabled",
        prepare_view_replay_after_open=True,
        view_replay_prepare_request_source="explicit_parameter",
        export_view_audit=True,
        views=["front"],
        working_dir=str(tmp_path),
        timeout_seconds=None,
    )

    assert result["ok"] is False
    assert result["workflow"] == "rollback"
    prepare = result["post_hotload_view_replay_prepare"]
    assert prepare["status"] == "not_run_rollback_failed"
    assert prepare["prepared"] is False
    assert prepare["automatic_after_hotload"] is False
    assert prepare["revision_created"] is False
    assert len(ProjectStore(tmp_path).list_history(project_id)) == 1
