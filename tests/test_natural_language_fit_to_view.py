from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore


def _benzene_spec(project_id: str) -> ModelSpec:
    example_path = (
        Path(__file__).parents[1]
        / "src"
        / "material_studio_mcp_server"
        / "examples"
        / "benzene_spec.json"
    )
    payload = json.loads(example_path.read_text(encoding="utf-8"))
    return ModelSpec.model_validate(
        {
            **payload,
            "project_id": project_id,
            "revision": 0,
        }
    )


def test_fit_to_view_plan_supports_english_and_chinese_normality_requests() -> None:
    spec = _benzene_spec("nl_fit_plan")

    english = infer_modeling_plan("fit the current model to view", current_spec=spec)
    assert english.kind == "fit_to_view"
    assert english.template_id == "fit_to_view"
    assert english.payload == {
        "project_id": "nl_fit_plan",
        "revision": 0,
        "check_normality": False,
        "export_diagnostics": False,
    }

    chinese = infer_modeling_plan(
        "\u5c06\u5f53\u524d\u6a21\u578b\u9002\u914d\u5230\u89c6\u56fe\u5e76\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38",
        current_spec=spec,
    )
    assert chinese.kind == "fit_to_view"
    assert chinese.template_id == "fit_to_view_with_normality_check"
    assert chinese.payload["check_normality"] is True


def test_live_fit_to_view_is_preview_first_and_does_not_create_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "nl_fit_server"
    spec = _benzene_spec(project_id)
    store = ProjectStore(tmp_path)
    store.create_project(spec, user_text="fixture")
    calls: list[dict] = []

    def fake_fit_to_view(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        mode = kwargs["execution_mode"]
        mode_value = getattr(mode, "value", mode)
        return {
            "ok": True,
            "status": "executed" if mode_value == "execute" else "preview_ready",
            "execution_mode": mode_value,
            "execution_ready": True,
            "gui_input_performed": mode_value == "execute",
            "structure_unchanged": True,
            "structure_modified": False,
        }

    monkeypatch.setattr(server, "material_studio_gui_fit_to_view", fake_fit_to_view)

    preview = server.material_studio_live_modeling_request(
        "fit the current model to view",
        project_id=project_id,
        working_dir=str(tmp_path),
    )

    assert preview["ok"] is True
    assert preview["workflow"] == "fit_to_view"
    assert preview["nl_plan"]["kind"] == "fit_to_view"
    assert preview["execution_mode"] == "preview"
    assert preview["execution_mode_source"] == "default_preview_fit_to_view"
    assert preview["revision_created"] is False
    assert preview["structure_unchanged"] is True
    assert calls[-1]["revision"] == 0
    assert getattr(calls[-1]["execution_mode"], "value", calls[-1]["execution_mode"]) == "preview"

    executed = server.material_studio_live_modeling_request(
        "fit the current model to view",
        project_id=project_id,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert executed["ok"] is True
    assert executed["workflow"] == "fit_to_view"
    assert executed["execution_mode"] == "execute"
    assert executed["execution_mode_source"] == "explicit_parameter"
    assert executed["revision_created"] is False
    assert getattr(calls[-1]["execution_mode"], "value", calls[-1]["execution_mode"]) == "execute"
    assert store.load_current(project_id).revision == 0
    assert len(store.list_history(project_id)) == 1


def test_live_fit_to_view_can_attach_read_only_normality_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "nl_fit_normality"
    ProjectStore(tmp_path).create_project(_benzene_spec(project_id), user_text="fixture")
    monkeypatch.setattr(
        server,
        "material_studio_gui_fit_to_view",
        lambda **kwargs: {
            "ok": True,
            "status": "preview_ready",
            "execution_ready": True,
            "structure_unchanged": True,
        },
    )
    status_calls: list[dict] = []

    def fake_status(**kwargs: object) -> dict:
        status_calls.append(dict(kwargs))
        return {
            "ok": True,
            "normality": "passed",
            "health_verdict": "passed",
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
            "status": "current",
        }

    monkeypatch.setattr(server, "material_studio_live_project_status", fake_status)

    result = server.material_studio_live_modeling_request(
        "fit the current model to view and check whether the model is normal",
        project_id=project_id,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["workflow"] == "fit_to_view"
    assert result["normality_check"] == {
        "requested": True,
        "ok": True,
        "normality": "passed",
        "health_verdict": "passed",
        "ready_for_next_edit": True,
        "ready_for_calculation": False,
        "status": "current",
        "error": None,
    }
    assert result["project_status"]["normality"] == "passed"
    assert status_calls[0]["project_id"] == project_id
    assert status_calls[0]["include_gui_status"] is True
