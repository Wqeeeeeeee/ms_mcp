from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server


def _silicon_payload(project_id: str) -> dict:
    payload = json.loads(
        Path("src/material_studio_mcp_server/examples/silicon_diamond_spec.json")
        .read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    return payload


def test_apply_current_revision_rejects_stale_handoff_before_gui_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "stale_apply_handoff"
    created = server.material_studio_model_create_from_spec(
        _silicon_payload(project_id),
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda *_args, **_kwargs: pytest.fail("stale handoff must not probe GUI"),
    )
    result = server.material_studio_gui_apply_current_revision(
        project_id=project_id,
        expected_revision=created["revision"] + 1,
        execution_mode="execute",
        open_in_gui=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "current_revision_execution_block"
    assert result["expected_revision"] == created["revision"] + 1
    assert result["current_revision"] == created["revision"]
    assert result["execution_started"] is False
    assert result["execution_deferred"] is True
    assert result["execution_transaction"]["gui_probe_started"] is False


def test_apply_handoff_preserves_revision_and_explicit_safety_options() -> None:
    report = {
        "project_id": "handoff_project",
        "revision": 4,
        "materials_studio_roundtrip_audit_requested": True,
        "post_hotload_fit_to_view_requested": False,
        "post_hotload_fit_to_view_request_source": "explicit_parameter",
        "post_hotload_view_replay_prepare_requested": True,
        "post_hotload_view_replay_prepare_request_source": "explicit_parameter",
        "view_selection": {
            "source": "explicit_request",
            "view_names": ["front", "crystal_111"],
        },
    }

    payload = server._bind_current_revision_apply_payload(
        {
            "project_id": "handoff_project",
            "revision": 4,
            "execution_mode": "execute",
        },
        report,
    )

    assert payload == {
        "project_id": "handoff_project",
        "execution_mode": "execute",
        "expected_revision": 4,
        "verify_ms_roundtrip": True,
        "fit_to_view_after_open": False,
        "prepare_view_replay_after_open": True,
        "views": ["front", "crystal_111"],
    }


def test_workspace_binding_reaches_deferred_apply_action() -> None:
    bound = server._workspace_bound_action_plan(
        {
            "recommended_tool": "material_studio_gui_status",
            "payload_hint": {"project_id": "p"},
            "deferred_hotload_action": {
                "recommended_tool": "material_studio_gui_apply_current_revision",
                "payload_hint": {"project_id": "p"},
            },
        },
        r"C:\workspace\handoff",
    )

    assert bound["payload_hint"]["project_id"] == "p"
    assert bound["deferred_hotload_action"]["payload_hint"]["working_dir"] == (
        r"C:\workspace\handoff"
    )


def test_preview_and_status_preserve_exact_apply_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_GUI_BACKEND", "null")
    created = server.material_studio_live_modeling_request(
        "Build silicon crystal for revision-bound handoff acceptance.",
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=False,
        export_view_audit=True,
        views=["front", "top"],
        working_dir=str(tmp_path),
        response_mode="compact",
        verify_ms_roundtrip=True,
    )
    status = server.material_studio_live_project_status(
        project_id=created["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    def apply_payload(response: dict) -> dict:
        plan = response["next_action_plan"]
        action = (
            plan
            if plan.get("recommended_tool")
            == "material_studio_gui_apply_current_revision"
            else plan["deferred_hotload_action"]
        )
        assert action["needs_user_confirmation"] is True
        assert action["safe_to_call_without_confirmation"] is False
        return action["payload_hint"]

    created_payload = apply_payload(created)
    status_payload = apply_payload(status)

    assert created_payload == status_payload
    assert created_payload["project_id"] == created["project_id"]
    assert created_payload["expected_revision"] == created["revision"]
    assert created_payload["verify_ms_roundtrip"] is True
    assert created_payload["fit_to_view_after_open"] is True
    assert created_payload["prepare_view_replay_after_open"] is False
    assert created_payload["views"] == ["front", "top"]
    assert created_payload["working_dir"] == str(tmp_path.resolve())
