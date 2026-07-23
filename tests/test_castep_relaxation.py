from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.parsers.castep_log import (
    CASTEP_GEOMETRY_RESULT_SCHEMA,
    validate_castep_geometry_result,
)
from material_studio_mcp_server.runner import ScriptRunResult
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore
from material_studio_mcp_server.translators import write_crystal_cif


def _silicon_spec(project_id: str) -> dict:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    return payload


class _ConvergedCastepRunner:
    def __init__(self, source: ModelSpec) -> None:
        self.source = source
        self.call_count = 0

    def run_script(
        self,
        script: str,
        *,
        working_dir: str | Path,
        timeout_seconds: int | None,
        job_prefix: str,
        keep_script_name: str,
    ) -> ScriptRunResult:
        self.call_count += 1
        directory = Path(working_dir).resolve()
        input_structure = directory / "input_structure.cif"
        output_structure = directory / "relaxed_structure.cif"
        output_report = directory / "castep_report.txt"
        assert input_structure.is_file()
        assert "Modules->CASTEP->GeometryOptimization->Run" in script
        assert keep_script_name == "run_geometry_optimization.pl"
        assert job_prefix.endswith("_castep_relax")

        atoms = list(self.source.model.basis_atoms)
        first = atoms[0]
        atoms[0] = first.model_copy(
            update={
                "fractional": first.fractional.model_copy(
                    update={"x": first.fractional.x + 0.001}
                )
            }
        )
        relaxed_crystal = self.source.model.model_copy(
            update={"basis_atoms": atoms}
        )
        write_crystal_cif(relaxed_crystal, output_structure)
        output_report.write_text(
            "Fake CASTEP GeometryOptimization converged.\n",
            encoding="utf-8",
        )
        payload = {
            "schema_version": CASTEP_GEOMETRY_RESULT_SCHEMA,
            "project_id": self.source.project_id,
            "base_revision": self.source.revision,
            "module": "CASTEP",
            "task": "GeometryOptimization",
            "materials_studio_api_contract": "Materials Studio 20.1",
            "output_structure": str(output_structure),
            "output_report": str(output_report),
            "converged": True,
            "total_energy_kcal_per_mol": -100.25,
            "enthalpy_kcal_per_mol": -99.75,
        }
        fake_job_dir = directory / "fake_runner_job"
        fake_job_dir.mkdir(parents=True, exist_ok=True)
        fake_script = fake_job_dir / keep_script_name
        fake_script.write_text(script, encoding="utf-8")
        return ScriptRunResult(
            command=["fake-RunMatScript.bat", str(fake_script)],
            job_id="fake-castep-relaxation",
            job_dir=fake_job_dir,
            script_path=fake_script,
            return_code=0,
            stdout="fake CASTEP completed",
            stderr="",
            output_file=None,
            log_file=None,
            materials_output="",
            materials_log="",
            success=True,
            timed_out=False,
            parsed_json=payload,
            created_files=[output_structure, output_report],
            duration_seconds=0.01,
        )


class _GuiSession:
    def __init__(
        self,
        *,
        single_window_policy_ok: bool,
        workspace: Path | None = None,
    ) -> None:
        self.single_window_policy_ok = single_window_policy_ok
        self.workspace = workspace
        self.open_calls: list[dict] = []
        self.fit_calls: list[dict] = []
        self.prepare_calls: list[dict] = []
        self.prepare_transaction: dict | None = None

    def status(self, *, project_id: str, revision: int) -> dict:
        process_count = 1 if self.single_window_policy_ok else 2
        window_count = 1 if self.single_window_policy_ok else 2
        return {
            "ok": True,
            "supported": True,
            "window_found": True,
            "process_count": process_count,
            "window_count": window_count,
            "single_window_policy_ok": self.single_window_policy_ok,
            "single_window_violation_reasons": (
                [] if self.single_window_policy_ok else ["multiple_processes"]
            ),
            "selected_window_handle": 101,
            "window": {
                "handle": 101,
                "title": f"{project_id}_r{revision:03d} - Materials Studio",
            },
            "window_management": {
                "process_count": process_count,
                "window_count": window_count,
                "single_window_policy_ok": self.single_window_policy_ok,
                "single_window_violation_reasons": (
                    [] if self.single_window_policy_ok else ["multiple_processes"]
                ),
                "target_window_handle": 101,
                "target_window_title": (
                    f"{project_id}_r{revision:03d} - Materials Studio"
                ),
            },
        }

    def fit_to_view(
        self,
        *,
        project_id: str,
        revision: int,
        execution_mode: str,
        take_snapshot: bool,
    ) -> dict:
        call = {
            "project_id": project_id,
            "revision": revision,
            "execution_mode": execution_mode,
            "take_snapshot": take_snapshot,
        }
        self.fit_calls.append(call)
        return {
            **call,
            "status": "executed",
            "execution_ready": True,
            "gui_input_performed": True,
            "gui_modified": True,
            "structure_modified": False,
            "structure_unchanged": True,
        }

    def snapshot(
        self,
        *,
        label: str,
        project_id: str,
        revision: int,
    ) -> dict:
        assert self.workspace is not None
        screenshot = (
            self.workspace
            / project_id
            / "screenshots"
            / f"r{revision:03d}_{label}.bmp"
        )
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(b"BM")
        return {
            "ok": True,
            "project_id": project_id,
            "revision": revision,
            "label": label,
            "screenshot_path": str(screenshot),
        }

    def prepare_view_replay(
        self,
        audit: dict,
        *,
        project_id: str,
        revision: int,
    ) -> dict:
        assert self.workspace is not None
        self.prepare_transaction = (
            server._ACTIVE_GUI_ARTIFACT_REPORT_TRANSACTION.get()
        )
        view_names = [
            str(view["name"])
            for view in audit.get("views") or []
            if isinstance(view, dict) and view.get("name")
        ]
        self.prepare_calls.append(
            {
                "project_id": project_id,
                "revision": revision,
                "view_names": view_names,
            }
        )
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
            "recommended_mcp_tool": "material_studio_gui_execute_view_replay",
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

    def open_structure(
        self,
        structure_path: str | Path,
        *,
        project_id: str,
        revision: int,
        take_snapshot: bool,
    ) -> dict:
        if not self.single_window_policy_ok:
            raise AssertionError("multi-window preflight must block open_structure")
        call = {
            "structure_path": str(Path(structure_path).resolve()),
            "project_id": project_id,
            "revision": revision,
            "take_snapshot": take_snapshot,
        }
        self.open_calls.append(call)
        return {
            "ok": True,
            **call,
            "same_window_open_used": True,
            "new_process_launched": False,
            "single_window_policy_ok": True,
            "process_count_before": 1,
            "process_count_after": 1,
            "window": {
                "handle": 101,
                "title": f"{project_id}_r{revision:03d} - Materials Studio",
            },
        }


def test_castep_relax_current_preview_never_runs_or_creates_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_preview"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_run(*args, **kwargs):
        raise AssertionError("preview must not call MaterialStudioRunner.run_script")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    preview = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="preview",
        expected_revision=0,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert preview["ok"] is True
    assert preview["status"] == "ready_for_explicit_execute"
    assert preview["execution_started"] is False
    assert preview["expected_revision"] == 0
    assert preview["revision_created"] is False
    assert preview["preflight"]["execution_ready"] is True
    assert "Modules->CASTEP->GeometryOptimization->Run" in preview["script"]
    assert "CellOptimization => 'None'" in preview["script"]
    assert not Path(preview["planned_outputs"]["structure"]).exists()
    current = ProjectStore(tmp_path).load_current(project_id)
    assert current.revision == 0


def test_castep_relaxation_stale_handoff_stops_before_runner_or_run_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_stale_handoff"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_run(*args, **kwargs):
        raise AssertionError("stale handoff must stop before runner invocation")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    result = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="execute",
        expected_revision=1,
        open_in_gui=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )

    assert result["ok"] is False
    assert result["status"] == (
        "castep_geometry_optimization_revision_binding_mismatch"
    )
    assert result["expected_revision"] == 1
    assert result["current_revision"] == 0
    assert result["execution_started"] is False
    assert result["revision_created"] is False
    assert result["next_action_plan"]["payload_hint"]["working_dir"] == str(
        tmp_path.resolve()
    )
    assert not (
        tmp_path
        / project_id
        / "outputs"
        / "r000"
        / "castep_geometry_optimization"
    ).exists()


def test_castep_relax_current_promotes_only_verified_converged_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_execute"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    store = ProjectStore(tmp_path)
    source = store.load_current(project_id)
    fake_runner = _ConvergedCastepRunner(source)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="execute",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
        timeout_seconds=60,
    )

    assert result["ok"] is True
    assert result["status"] == "castep_relaxation_promoted"
    assert result["execution_started"] is True
    assert result["revision_created"] is True
    assert result["base_revision"] == 0
    assert result["revision"] == 1
    assert result["new_revision"] == 1
    assert result["result_validation"]["ok"] is True
    assert result["result_validation"]["converged"] is True
    assert result["relaxation_receipt"]["geometry_relaxation_verified"] is True
    assert result["relaxation_receipt"]["atom_identity_preserved"] is True
    assert fake_runner.call_count == 1

    promoted = store.load_current(project_id)
    assert promoted.revision == 1
    assert promoted.metadata["geometry_relaxed"] is True
    assert promoted.metadata["requires_geometry_relaxation"] is False
    relaxation = result["view_audit"]["health"]["semiconductor_health"][
        "castep_geometry_optimization_summary"
    ]
    assert relaxation["transition_verified"] is True
    assert relaxation["fixed_cell_transition_verified"] is True
    assert relaxation["target_revision"] == 1
    assert Path(result["planned_outputs"]["structure"]).is_file()
    assert Path(result["planned_outputs"]["report"]).is_file()
    assert Path(result["result_metadata_path"]).is_file()
    assert Path(result["view_audit_report_path"]).is_file()
    assert len(store.list_history(project_id)) == 2


def test_live_entry_routes_castep_relaxation_and_respects_explicit_preview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_live_preview"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_run(*args, **kwargs):
        raise AssertionError("explicit preview must override natural-language run intent")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    result = server.material_studio_live_modeling_request(
        user_request=(
            "Run CASTEP geometry optimization on the current model with fixed cell."
        ),
        project_id=project_id,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["workflow"] == "castep_geometry_optimization"
    assert result["execution_mode"] == "preview"
    assert result["execution_started"] is False
    assert result["revision_created"] is False
    assert result["nl_plan"]["kind"] == "castep_relaxation"
    assert result["nl_plan"]["template_id"] == (
        "castep_geometry_optimization_current_revision"
    )


def test_live_relaxation_preview_persists_inferred_view_action_contracts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_live_view_preview"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_gui(*args, **kwargs):
        raise AssertionError("preview must not probe or modify the GUI")

    monkeypatch.setattr(server, "_gui_controller", unexpected_gui)
    request = (
        "Run CASTEP geometry optimization on the current model, open it in "
        "Materials Studio, fit to view, and inspect front, top, and isometric "
        "views to check whether the semiconductor model is normal."
    )
    result = server.material_studio_live_modeling_request(
        user_request=request,
        project_id=project_id,
        execution_mode="preview",
        open_in_gui=True,
        take_snapshot=True,
        export_view_audit=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["workflow"] == "castep_geometry_optimization"
    assert result["execution_started"] is False
    assert result["post_hotload_fit_to_view_requested"] is True
    assert result["post_hotload_fit_to_view_request_source"] == (
        "natural_language"
    )
    assert result["post_hotload_fit_to_view"]["status"] == (
        "deferred_until_execute"
    )
    assert result["post_hotload_view_replay_prepare_requested"] is True
    assert result["post_hotload_view_replay_prepare_request_source"] == (
        "natural_language_views"
    )
    assert result["post_hotload_view_replay_prepare"]["view_names"] == [
        "front",
        "top",
        "isometric",
    ]
    report = result["modeling_report"]
    assert report["user_request"] == request
    assert report["post_hotload_fit_to_view_requested"] is True
    assert report["post_hotload_view_replay_prepare_requested"] is True
    assert report["post_hotload_view_replay_prepare"]["gui_input_performed"] is False
    assert report["live_summary"][
        "post_hotload_view_replay_prepare_requested"
    ] is True
    assert report["live_summary"][
        "post_hotload_view_replay_prepare_status"
    ] == "deferred_until_execute"


def test_live_entry_auto_executes_only_explicit_castep_relaxation_intent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_live_execute"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    store = ProjectStore(tmp_path)
    fake_runner = _ConvergedCastepRunner(store.load_current(project_id))
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_live_modeling_request(
        user_request="Run CASTEP geometry optimization on the current model now.",
        project_id=project_id,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["workflow"] == "castep_geometry_optimization"
    assert result["execution_mode"] == "execute"
    assert result["execution_started"] is True
    assert result["revision_created"] is True
    assert result["revision"] == 1
    assert result["nl_plan"]["kind"] == "castep_relaxation"
    assert fake_runner.call_count == 1


def test_castep_relaxation_blocks_before_runner_when_gui_is_not_single_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_multi_window_block"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    gui = _GuiSession(single_window_policy_ok=False)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    def unexpected_run(*args, **kwargs):
        raise AssertionError("CASTEP must not start when the one-window gate fails")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    result = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "single_window_gui_preflight_blocked"
    assert result["execution_started"] is False
    assert result["revision_created"] is False
    assert gui.open_calls == []
    assert ProjectStore(tmp_path).load_current(project_id).revision == 0


def test_castep_relaxation_hotloads_converged_revision_into_same_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "castep_relax_same_window"
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    store = ProjectStore(tmp_path)
    fake_runner = _ConvergedCastepRunner(store.load_current(project_id))
    gui = _GuiSession(single_window_policy_ok=True, workspace=tmp_path)
    monkeypatch.setattr(server, "runner", fake_runner)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=True,
        export_view_audit=True,
        views=["front", "top", "isometric"],
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["revision"] == 1
    assert result["gui_open"]["same_window_open_used"] is True
    assert result["gui_open"]["new_process_launched"] is False
    assert len(gui.open_calls) == 1
    assert gui.open_calls[0]["revision"] == 1
    assert Path(gui.open_calls[0]["structure_path"]).is_file()
    assert gui.fit_calls == [
        {
            "project_id": project_id,
            "revision": 1,
            "execution_mode": "execute",
            "take_snapshot": False,
        }
    ]
    assert result["post_hotload_fit_to_view"]["completed"] is True
    assert gui.prepare_transaction is None
    assert gui.prepare_calls == [
        {
            "project_id": project_id,
            "revision": 1,
            "view_names": ["front", "top", "isometric"],
        }
    ]
    prepared = result["post_hotload_view_replay_prepare"]
    assert prepared["status"] == "prepared"
    assert prepared["prepared_revision"] == 1
    assert Path(prepared["manifest_path"]).is_file()
    assert result["visual_diagnostics_next_action_plan"]["recommended_tool"] == (
        "material_studio_gui_execute_view_replay"
    )
    assert fake_runner.call_count == 1


def test_castep_tagged_result_rejects_wrong_revision_and_missing_artifacts(
    tmp_path: Path,
) -> None:
    structure = tmp_path / "relaxed.cif"
    report = tmp_path / "report.txt"
    payload = {
        "schema_version": CASTEP_GEOMETRY_RESULT_SCHEMA,
        "project_id": "strict_result",
        "base_revision": 7,
        "module": "CASTEP",
        "task": "GeometryOptimization",
        "materials_studio_api_contract": "Materials Studio 20.1",
        "output_structure": str(structure),
        "output_report": str(report),
        "converged": True,
        "total_energy_kcal_per_mol": -1.0,
        "enthalpy_kcal_per_mol": -0.5,
    }

    validation = validate_castep_geometry_result(
        payload,
        project_id="strict_result",
        base_revision=8,
        output_structure=structure,
        output_report=report,
    )

    assert validation["ok"] is False
    assert validation["converged"] is True
    assert any("base_revision mismatch" in error for error in validation["errors"])
    assert any("was not found" in error for error in validation["errors"])
