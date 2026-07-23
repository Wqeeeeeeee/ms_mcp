from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server
from material_studio_mcp_server.server import (
    material_studio_model_create_from_spec,
    material_studio_model_get_current,
    material_studio_model_modify_with_patch,
    material_studio_model_preview_script,
    material_studio_model_validate,
    material_studio_live_project_status,
    material_studio_project_history,
    material_studio_project_rollback,
)
from material_studio_mcp_server.state.store import ProjectStore


def load_benzene() -> dict:
    path = Path("src/material_studio_mcp_server/examples/benzene_spec.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_example(name: str) -> dict:
    path = Path("src/material_studio_mcp_server/examples") / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_structured_tool_preview_workflow(tmp_path: Path) -> None:
    spec = load_benzene()
    created = material_studio_model_create_from_spec(spec, working_dir=str(tmp_path))
    assert created["ok"] is True
    assert created["execution_mode"] == "preview"
    assert "script" in created

    validation = material_studio_model_validate(project_id=spec["project_id"], working_dir=str(tmp_path))
    assert validation["ok"] is True
    assert validation["project_resolution"]["source"] == "explicit"

    patch = {
        "operations": [
            {"type": "delete_atom", "atom_id": "H1"},
            {"type": "add_atom", "id": "N1", "element": "N", "xyz_angstrom": [2.7, 0, 0]},
            {"type": "add_bond", "atom1": "C1", "atom2": "N1", "bond_type": "Single"},
        ]
    }
    modified = material_studio_model_modify_with_patch(
        spec["project_id"],
        0,
        patch,
        working_dir=str(tmp_path),
    )
    assert modified["ok"] is True
    assert modified["new_revision"] == 1
    assert modified["revision"] == 1
    assert "delete_atom H1" in modified["diff"]
    assert modified["revision_delta"]["element_count_delta"] == {"H": -1, "N": 1}
    assert modified["revision_delta"]["molecule"]["added_atoms"][0]["atom_id"] == "N1"

    current = material_studio_model_get_current(spec["project_id"], working_dir=str(tmp_path))
    assert current["revision"] == 1
    assert current["current_pointer"]["status"] == "valid"

    preview = material_studio_model_preview_script(project_id=spec["project_id"], working_dir=str(tmp_path))
    assert preview["ok"] is True
    assert preview["execution_mode"] == "preview"
    assert preview["project_resolution"]["source"] == "explicit"

    history = material_studio_project_history(spec["project_id"], working_dir=str(tmp_path))
    assert len(history["history"]) == 2

    latest_history = material_studio_project_history(working_dir=str(tmp_path))
    assert latest_history["ok"] is True
    assert latest_history["project_id"] == spec["project_id"]
    assert latest_history["project_resolution"]["source"] == "latest_current"
    assert len(latest_history["history"]) == 2

    rollback = material_studio_project_rollback(project_id=spec["project_id"], target_revision=0, working_dir=str(tmp_path))
    assert rollback["ok"] is True
    assert rollback["new_revision"] == 2
    assert rollback["revision"] == 2

    latest_rollback = material_studio_project_rollback(target_revision=1, working_dir=str(tmp_path))
    assert latest_rollback["ok"] is True
    assert latest_rollback["project_id"] == spec["project_id"]
    assert latest_rollback["project_resolution"]["source"] == "latest_current"
    assert latest_rollback["new_revision"] == 3
    assert latest_rollback["revision"] == 3


def test_structured_patch_recovers_pointer_and_uses_gap_safe_revision_paths(
    tmp_path: Path,
) -> None:
    spec = load_benzene()
    spec["project_id"] = "structured_pointer_recovery"
    created = material_studio_model_create_from_spec(spec, working_dir=str(tmp_path))
    assert created["ok"] is True
    project_dir = Path(created["state"]["project_dir"])
    current_path = Path(created["state"]["current_path"])
    orphan_path = project_dir / "revisions" / "r001_model_spec.json"
    orphan_bytes = b'{"orphan":'
    orphan_path.write_bytes(orphan_bytes)
    current_path.write_bytes(b'{"broken":')

    pointer_before_reads = current_path.read_bytes()
    validation = material_studio_model_validate(
        project_id=spec["project_id"],
        working_dir=str(tmp_path),
    )
    preview = material_studio_model_preview_script(
        project_id=spec["project_id"],
        working_dir=str(tmp_path),
    )
    current = material_studio_model_get_current(
        spec["project_id"],
        working_dir=str(tmp_path),
    )
    for result in (validation, preview, current):
        assert result["ok"] is True
        assert result["project_resolution"]["current_pointer_recovery_used"] is True
    assert current["current_pointer"]["status"] == "recovered_invalid_current_pointer"
    assert current_path.read_bytes() == pointer_before_reads

    modified = material_studio_model_modify_with_patch(
        spec["project_id"],
        0,
        {"operations": [{"type": "delete_atom", "atom_id": "H1"}]},
        working_dir=str(tmp_path),
    )

    assert modified["ok"] is True
    assert modified["new_revision"] == 2
    assert modified["revision"] == 2
    assert modified["state"]["spec_path"].endswith("r002_model_spec.json")
    assert "r002" in modified["planned_outputs"]["structure"]
    assert modified["revision_delta"]["new_revision"] == 2
    assert modified["project_resolution"]["current_pointer_recovery_used"] is True
    assert modified["current_pointer_recovery"]["status"] == (
        "recovered_invalid_current_pointer"
    )
    assert modified["current_pointer_repaired"] is True
    assert modified["current_pointer_after_write"]["status"] == "valid"
    assert modified["current_pointer_after_write"]["revision"] == 2
    assert orphan_path.read_bytes() == orphan_bytes
    current_payload = json.loads(current_path.read_text(encoding="utf-8"))
    assert current_payload["revision"] == 2
    status = material_studio_live_project_status(
        project_id=spec["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
    )
    assert status["ok"] is True
    assert status["revision_delta"]["available"] is True
    assert status["revision_delta"]["base_revision"] == 0
    assert status["revision_delta"]["new_revision"] == 2
    assert status["revision_delta"]["previous_revision_source"] == (
        "latest_valid_prior_revision"
    )
    assert status["revision_delta"]["revision_gap"] == 1
    assert [
        item["revision"]
        for item in status["revision_delta"]["skipped_invalid_revision_files"]
    ] == [1]
    history = material_studio_project_history(spec["project_id"], working_dir=str(tmp_path))
    assert [event["revision"] for event in history["history"]] == [0, 2]


def test_structured_rollback_recovers_pointer_without_overwriting_orphan_revision(
    tmp_path: Path,
) -> None:
    spec = load_benzene()
    spec["project_id"] = "structured_rollback_pointer_recovery"
    created = material_studio_model_create_from_spec(spec, working_dir=str(tmp_path))
    first_update = material_studio_model_modify_with_patch(
        spec["project_id"],
        0,
        {"operations": [{"type": "delete_atom", "atom_id": "H1"}]},
        working_dir=str(tmp_path),
    )
    assert first_update["new_revision"] == 1

    project_dir = Path(created["state"]["project_dir"])
    current_path = Path(created["state"]["current_path"])
    orphan_path = project_dir / "revisions" / "r002_model_spec.json"
    orphan_bytes = b'{"corrupt":"orphan"'
    orphan_path.write_bytes(orphan_bytes)
    current_path.write_bytes(b'{"broken":')

    rollback = material_studio_project_rollback(
        project_id=spec["project_id"],
        target_revision=0,
        working_dir=str(tmp_path),
    )

    assert rollback["ok"] is True
    assert rollback["new_revision"] == 3
    assert rollback["revision"] == 3
    assert rollback["state"]["spec_path"].endswith("r003_model_spec.json")
    assert rollback["state"]["script_path"].endswith("r003_build.pl")
    assert rollback["project_resolution"]["current_pointer_recovery_used"] is True
    assert rollback["current_pointer_recovery"]["revision"] == 1
    assert rollback["current_pointer_repaired"] is True
    assert rollback["current_pointer_after_write"]["status"] == "valid"
    assert rollback["current_pointer_after_write"]["revision"] == 3
    assert orphan_path.read_bytes() == orphan_bytes
    assert json.loads(current_path.read_text(encoding="utf-8"))["revision"] == 3
    history = material_studio_project_history(
        spec["project_id"],
        working_dir=str(tmp_path),
    )
    assert [event["revision"] for event in history["history"]] == [0, 1, 3]


def test_structured_crystal_execute_materializes_cif_without_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = load_example("silicon_diamond_spec.json")
    spec["project_id"] = "structured_silicon_execute"
    result_writes: list[tuple[str, int]] = []
    original_write_result_metadata = ProjectStore.write_result_metadata

    def tracked_write_result_metadata(
        self: ProjectStore,
        project_id: str,
        revision: int,
        result: dict,
    ) -> Path:
        result_writes.append((project_id, revision))
        return original_write_result_metadata(self, project_id, revision, result)

    monkeypatch.setattr(
        ProjectStore,
        "write_result_metadata",
        tracked_write_result_metadata,
    )

    result = material_studio_model_create_from_spec(spec, execution_mode="execute", working_dir=str(tmp_path))

    assert result["ok"] is True
    assert result["execution_mode"] == "execute"
    assert result["planned_outputs"]["structure"].endswith(".cif")
    assert result["result"]["success"] is True
    assert result["result"]["execution_backend"] == "crystal_cif_materialize"
    assert result["result"]["structure_artifact_validation"]["status"] == "matched"
    assert result["result"]["structure_artifact_validation"]["ok"] is True
    assert Path(result["planned_outputs"]["structure"]).exists()
    assert Path(result["result_metadata_path"]).exists()
    transaction = result["execution_transaction"]
    assert transaction["domain"] == "revision_execution"
    assert transaction["current_revision_still_current"] is True
    assert "crystal_cif_materialization" in transaction["coverage"]
    persisted_result = json.loads(
        Path(result["result_metadata_path"]).read_text(encoding="utf-8")
    )
    assert persisted_result["execution_transaction"] == transaction
    assert result_writes == [(spec["project_id"], 0)]
    status = material_studio_live_project_status(
        project_id=spec["project_id"],
        include_gui_status=False,
        working_dir=str(tmp_path),
    )
    assert status["execution_transaction"] == transaction
    assert status["execution_started"] is True


def test_structured_execute_propagates_runner_failure_to_top_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = load_benzene()
    spec["project_id"] = "structured_runner_failure"

    class FailedRunResult:
        def to_dict(self) -> dict:
            return {
                "success": False,
                "return_code": 1,
                "stderr": "simulated MaterialsScript failure",
                "created_files": [],
                "duration_seconds": 0.01,
            }

    monkeypatch.setattr(
        server.runner,
        "run_script",
        lambda *args, **kwargs: FailedRunResult(),
    )
    result = material_studio_model_create_from_spec(
        spec,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "revision_execution_failed"
    assert result["result"]["success"] is False
    assert result["execution_attempt"]["result_success"] is False


def test_forcite_dynamics_execute_persists_unmanaged_result_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.xsd"
    input_path.write_text("fake structure", encoding="utf-8")

    class FakeRunResult:
        def to_dict(self) -> dict:
            return {
                "success": True,
                "return_code": 0,
                "created_files": [],
                "duration_seconds": 0.01,
            }

    monkeypatch.setattr(
        server.runner,
        "run_script",
        lambda *args, **kwargs: FakeRunResult(),
    )

    result = server.material_studio_forcite_dynamics_from_spec(
        input_file=str(input_path),
        ensemble="NVT",
        timestep_fs=1.0,
        total_time_ps=1.0,
        temperature_K=300.0,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["result"]["success"] is True
    result_path = Path(result["result_metadata_path"])
    assert result_path.exists()
    assert json.loads(result_path.read_text(encoding="utf-8")) == result["result"]
