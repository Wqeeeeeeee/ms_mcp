from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server
from material_studio_mcp_server.server import (
    material_studio_live_project_status,
    material_studio_model_create_from_spec,
    material_studio_model_modify_with_patch,
    material_studio_model_preview_script,
    material_studio_model_validate,
    material_studio_project_rollback,
)
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state import ProjectStore


def load_silicon() -> dict:
    path = Path(
        "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_crystal_create_persists_bound_castep_preview_and_exposes_read_receipts(
    tmp_path: Path,
) -> None:
    spec = load_silicon()
    spec["project_id"] = "crystal_castep_preview_receipt"

    created = material_studio_model_create_from_spec(
        spec,
        working_dir=str(tmp_path),
    )

    assert created["ok"] is True
    assert created["execution_mode"] == "preview"
    assert "Modules->CASTEP" not in created["script"]
    calculation = created["calculation_preview"]
    assert calculation["available"] is True
    assert calculation["task"] == "Energy"
    assert calculation["artifact_status"] == "matched"
    assert calculation["persisted_artifact_trusted"] is True
    assert calculation["preview_ready"] is True
    assert calculation["calculation_executed"] is False
    assert calculation["calculation_result_available"] is False
    assert calculation["structure_materialization_executes_calculation"] is False
    assert "Modules->CASTEP->Energy->Run" in calculation["script"]
    calculation_path = Path(calculation["script_path"])
    assert calculation_path.name == "r000_castep_task.pl"
    assert calculation_path.read_text(encoding="utf-8") == calculation["script"]
    assert not Path(created["planned_outputs"]["structure"]).exists()
    assert created["state"]["calculation_preview_script_path"] == str(
        calculation_path
    )
    current_payload = json.loads(
        Path(created["state"]["current_path"]).read_text(encoding="utf-8")
    )
    assert current_payload["calculation_preview_script_path"] == str(calculation_path)

    validated = material_studio_model_validate(
        project_id=spec["project_id"],
        working_dir=str(tmp_path),
    )
    assert validated["calculation_preview"]["artifact_status"] == "matched"
    assert "script" not in validated["calculation_preview"]

    previewed = material_studio_model_preview_script(
        project_id=spec["project_id"],
        working_dir=str(tmp_path),
    )
    assert previewed["calculation_preview"]["script"] == calculation["script"]

    status = material_studio_live_project_status(
        project_id=spec["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
    )
    assert status["calculation_preview"]["artifact_status"] == "matched"
    assert status["current"]["state"]["calculation_preview_script_path"] == str(
        calculation_path
    )
    assert status["modeling_report"]["calculation_preview"][
        "persisted_artifact_trusted"
    ] is True
    assert status["modeling_report"]["change_receipt"]["artifacts"][
        "calculation_preview_script_path"
    ] == str(calculation_path)
    assert status["live_summary"]["calculation_preview_task"] == "Energy"
    assert status["live_summary"]["calculation_preview_trusted"] is True
    assert status["live_summary"]["calculation_executed"] is False

    compact = material_studio_live_project_status(
        project_id=spec["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode=server.McpResponseMode.COMPACT,
    )
    assert compact["calculation_preview"]["artifact_status"] == "matched"
    assert "script" not in compact["calculation_preview"]
    assert compact["artifacts"]["calculation_preview_script_path"] == str(
        calculation_path
    )
    assert compact["live_summary"]["calculation_preview_task"] == "Energy"

    preflight = server._latest_project_preflight_summary(ProjectStore(tmp_path))
    assert preflight["calculation_preview"]["artifact_status"] == "matched"
    assert "script" not in preflight["calculation_preview"]


def test_crystal_castep_preview_detects_persisted_script_tampering(
    tmp_path: Path,
) -> None:
    spec = load_silicon()
    spec["project_id"] = "crystal_castep_preview_tamper"
    created = material_studio_model_create_from_spec(
        spec,
        working_dir=str(tmp_path),
    )
    calculation_path = Path(created["calculation_preview"]["script_path"])
    calculation_path.write_text(
        created["calculation_preview"]["script"] + "\n# external change\n",
        encoding="utf-8",
    )

    status = material_studio_live_project_status(
        project_id=spec["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
    )

    calculation = status["calculation_preview"]
    assert calculation["artifact_status"] == "mismatch"
    assert calculation["script_matches_generated"] is False
    assert calculation["persisted_artifact_trusted"] is False
    assert calculation["script_expected_sha256"] != calculation[
        "script_persisted_sha256"
    ]
    assert calculation["calculation_executed"] is False
    assert status["live_summary"]["calculation_preview_trusted"] is False


def test_crystal_execute_materializes_only_cif_and_never_runs_castep_companion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = load_silicon()
    spec["project_id"] = "crystal_castep_materialize_only"

    def unexpected_runner_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("crystal materialization must not run the CASTEP companion")

    monkeypatch.setattr(server.runner, "run_script", unexpected_runner_call)
    result = material_studio_model_create_from_spec(
        spec,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["result"]["execution_backend"] == "crystal_cif_materialize"
    assert Path(result["planned_outputs"]["structure"]).exists()
    calculation = result["calculation_preview"]
    assert calculation["artifact_status"] == "matched"
    assert calculation["execution_policy"] == "preview_only"
    assert calculation["execution_supported_by_structured_workflow"] is False
    assert calculation["structure_materialization_executes_calculation"] is False
    assert calculation["calculation_executed"] is False
    assert calculation["calculation_result_available"] is False


def test_gui_apply_preview_reports_companion_without_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = load_silicon()
    spec["project_id"] = "crystal_castep_gui_apply_preview"
    material_studio_model_create_from_spec(spec, working_dir=str(tmp_path))

    class FakeGuiController:
        def status(self, **kwargs: object) -> dict:
            return {
                "ok": True,
                "process_found": True,
                "window_found": True,
                "process_count": 1,
                "live_window_count": 1,
                "single_window_policy_ok": True,
                "windows": [],
            }

    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: FakeGuiController(),
    )

    result = server.material_studio_gui_apply_current_revision(
        project_id=spec["project_id"],
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "preview"
    assert result["calculation_preview"]["artifact_status"] == "matched"
    assert result["calculation_preview"]["calculation_executed"] is False
    assert "result" not in result


def test_high_level_live_create_carries_companion_into_modeling_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeGuiController:
        def status(self, **kwargs: object) -> dict:
            return {
                "ok": True,
                "process_found": False,
                "window_found": False,
                "process_count": 0,
                "live_window_count": 0,
                "single_window_policy_ok": False,
                "single_window_policy": {"ok": False},
                "windows": [],
            }

    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: FakeGuiController(),
    )

    result = server.material_studio_live_modeling_request(
        "Build silicon crystal and prepare preview.",
        execution_mode="preview",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["workflow"] == "create"
    assert result["calculation_preview"]["artifact_status"] == "matched"
    report_calculation = result["modeling_report"]["calculation_preview"]
    assert report_calculation["task"] == "Energy"
    assert report_calculation["persisted_artifact_trusted"] is True
    assert "script" not in report_calculation
    assert result["live_summary"]["calculation_preview_trusted"] is True


def test_castep_patch_and_rollback_create_revision_bound_companion_scripts(
    tmp_path: Path,
) -> None:
    spec = load_silicon()
    spec["project_id"] = "crystal_castep_revision_chain"
    created = material_studio_model_create_from_spec(
        spec,
        working_dir=str(tmp_path),
    )
    r0_path = Path(created["calculation_preview"]["script_path"])
    r0_script = r0_path.read_text(encoding="utf-8")

    modified = material_studio_model_modify_with_patch(
        spec["project_id"],
        0,
        {
            "operations": [
                {
                    "type": "set_castep_energy",
                    "task": "BandStructure",
                    "functional": "PBE",
                    "quality": "Medium",
                    "cutoff_energy_ev": 600,
                    "kpoint_separation": 0.03,
                }
            ]
        },
        working_dir=str(tmp_path),
    )

    assert modified["ok"] is True
    assert modified["new_revision"] == 1
    r1 = modified["calculation_preview"]
    r1_path = Path(r1["script_path"])
    assert r1_path.name == "r001_castep_task.pl"
    assert r1["task"] == "BandStructure"
    assert "CalculateBandStructure => 'Dispersion'" in r1["script"]
    assert "EnergyCutoff => 600" in r1["script"]
    assert "r001" in r1["input_structure"]
    assert r0_path.read_text(encoding="utf-8") == r0_script

    rolled_back = material_studio_project_rollback(
        project_id=spec["project_id"],
        target_revision=0,
        working_dir=str(tmp_path),
    )

    assert rolled_back["ok"] is True
    assert rolled_back["new_revision"] == 2
    r2 = rolled_back["calculation_preview"]
    assert Path(r2["script_path"]).name == "r002_castep_task.pl"
    assert r2["task"] == "Energy"
    assert "EnergyCutoff => 520" in r2["script"]
    assert "r002" in r2["input_structure"]
    assert r0_path.read_text(encoding="utf-8") == r0_script


def test_store_refuses_to_overwrite_orphaned_companion_preview(
    tmp_path: Path,
) -> None:
    spec = ModelSpec.model_validate(load_silicon()).model_copy(
        update={"project_id": "crystal_castep_append_only"}
    )
    store = ProjectStore(tmp_path)
    created = store.create_project(
        spec,
        generated_script="# primary r0",
        calculation_preview_script="# CASTEP r0",
    )
    assert created.calculation_preview_script_path is not None
    assert created.calculation_preview_script_path.name == "r000_castep_task.pl"

    orphan = created.project_dir / "scripts" / "r001_castep_task.pl"
    orphan.write_text("# orphaned CASTEP preview", encoding="utf-8")
    candidate = store.load_current(spec.project_id).model_copy(
        update={"metadata": {"attempted": True}}
    )

    with pytest.raises(ValueError):
        store.save_revision(
            spec.project_id,
            candidate,
            action="metadata",
            generated_script="# primary r1",
            calculation_preview_script="# CASTEP r1",
            expected_revision=0,
            expected_new_revision=1,
        )

    assert store.load_current(spec.project_id).revision == 0
    assert not (created.project_dir / "revisions" / "r001_model_spec.json").exists()
    assert not (created.project_dir / "scripts" / "r001_build.pl").exists()
    assert orphan.read_text(encoding="utf-8") == "# orphaned CASTEP preview"
