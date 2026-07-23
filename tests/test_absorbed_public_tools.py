from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from material_studio_mcp_server import server
from material_studio_mcp_server.cif_sources import (
    fetch_cif_source,
    search_cod,
)
from material_studio_mcp_server.dmol3_contract import (
    DMOL3_GEOMETRY_RESULT_SCHEMA,
    DMOL3_REVIEWED_RESULT_KEYS,
)
from material_studio_mcp_server.runner import ScriptRunResult
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore


def _runtime_current() -> dict[str, Any]:
    return {
        "schema": "material_studio_mcp_runtime_provenance_v1",
        "status": "current",
        "source_current": True,
        "source_changed_since_start": False,
        "restart_required": False,
        "runtime_instance_id": "pytest-absorbed-public-tools",
    }


@pytest.fixture(autouse=True)
def _allow_guarded_test_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "runtime_provenance_status", _runtime_current)


def _filesystem_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _forbidden_external_call(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("preview attempted DNS or network access")


def test_public_cif_search_and_ingest_previews_have_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "preview-must-not-exist"
    original_search = search_cod
    original_fetch = fetch_cif_source

    def preview_only_search(**kwargs: Any) -> dict[str, Any]:
        return original_search(
            **kwargs,
            resolver=_forbidden_external_call,
            opener=_forbidden_external_call,
        )

    def preview_only_fetch(**kwargs: Any) -> dict[str, Any]:
        return original_fetch(
            **kwargs,
            resolver=_forbidden_external_call,
            opener=_forbidden_external_call,
        )

    monkeypatch.setattr(server, "search_cod", preview_only_search)
    monkeypatch.setattr(server, "fetch_cif_source", preview_only_fetch)

    searched = server.material_studio_cif_source_search(
        formula="O2 Si",
        max_results=5,
        execution_mode="preview",
    )
    ingested = server.material_studio_cif_source_ingest(
        project_id="cif_preview_only",
        structure_name="quartz candidate",
        cod_id="1000000",
        execution_mode="preview",
        working_dir=str(workspace),
    )

    assert searched["ok"] is True
    assert searched["status"] == "ready"
    assert searched["network_performed"] is False
    assert searched["dns_resolution_performed"] is False
    assert searched["writes_performed"] is False

    assert ingested["ok"] is True
    assert ingested["status"] == "ready_for_explicit_execute"
    assert ingested["network_performed"] is False
    assert ingested["project_created"] is False
    assert ingested["materials_studio_execution_performed"] is False
    assert ingested["gui_input_performed"] is False
    assert ingested["fetch"]["writes_performed"] is False
    assert (
        ingested["model_spec_template"]["model_type"]
        == "imported_structure"
    )
    assert (
        ingested["execute_action"]["recommended_tool"]
        == "material_studio_cif_source_ingest"
    )
    assert ingested["execute_action"]["needs_user_confirmation"] is True
    assert not workspace.exists()


def test_public_cif_ingest_rejects_source_tampering_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        tmp_path
        / ".reference_sources"
        / "cif"
        / "records"
        / ("a" * 64)
        / "source.cif"
    )
    source.parent.mkdir(parents=True)
    original = (
        b"data_quartz\n"
        b"_cell_length_a 4.9\n"
        b"_cell_length_b 4.9\n"
        b"_cell_length_c 5.4\n"
    )
    source.write_bytes(original)
    original_sha256 = hashlib.sha256(original).hexdigest()
    receipt = {
        "schema_version": "material_studio_cif_source_receipt_v1",
        "status": "fetched",
        "requested_url": "https://www.crystallography.net/cod/1000000.cif",
        "final_url": "https://www.crystallography.net/cod/1000000.cif",
        "content_sha256": original_sha256,
        "network_performed": True,
        "writes_performed": True,
        "record": {
            "record_id": "a" * 64,
            "source_path": str(source),
            "provenance_path": str(source.parent / "provenance.json"),
        },
    }
    monkeypatch.setattr(
        server,
        "fetch_cif_source",
        lambda **kwargs: receipt,
    )
    original_create = server.material_studio_model_create_from_spec

    def tamper_then_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        source.write_bytes(original + b"# tampered after fetch\n")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(
        server,
        "material_studio_model_create_from_spec",
        tamper_then_create,
    )

    class ForbiddenRunner:
        def run_script(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("tampered CIF reached Materials Studio runner")

    monkeypatch.setattr(server, "runner", ForbiddenRunner())
    result = server.material_studio_cif_source_ingest(
        project_id="tampered_cif_source",
        structure_name="tampered quartz",
        cod_id="1000000",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "cif_import_failed"
    assert result["structured_import"]["status"] == "revision_execution_failed"
    assert "SHA-256 mismatch" in result["structured_import"]["error"]
    assert (
        result["model_spec"]["model"]["source_file"]["sha256"]
        == original_sha256
    )
    planned = Path(
        result["structured_import"]["planned_outputs"]["structure"]
    )
    assert not planned.exists()


def _castep_spec(project_id: str) -> dict[str, Any]:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    payload["simulation"] = {
        "module": "CASTEP",
        "task": "Energy",
        "functional": "PBE",
        "quality": "Medium",
    }
    return payload


def _materialize_remote_handoff_project(
    workspace: Path,
    *,
    project_id: str,
) -> dict[str, Any]:
    created = server.material_studio_model_create_from_spec(
        _castep_spec(project_id),
        execution_mode="preview",
        working_dir=str(workspace),
    )
    assert created["ok"] is True
    input_path = Path(created["planned_outputs"]["structure"])
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(
        "data_silicon\n"
        "_cell_length_a 5.431\n"
        "_cell_length_b 5.431\n"
        "_cell_length_c 5.431\n"
        "_cell_angle_alpha 90\n"
        "_cell_angle_beta 90\n"
        "_cell_angle_gamma 90\n",
        encoding="utf-8",
    )
    return created


def test_public_remote_handoff_lifecycle_is_revision_and_identity_bound(
    tmp_path: Path,
) -> None:
    project_id = "public_remote_handoff"
    created = _materialize_remote_handoff_project(
        tmp_path,
        project_id=project_id,
    )
    project_dir = Path(created["state"]["project_dir"])
    before_stale = _filesystem_snapshot(project_dir)

    stale = server.material_studio_remote_castep_prepare(
        project_id=project_id,
        expected_revision=1,
        calculation_name="energy_baseline",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert stale["ok"] is False
    assert stale["status"] == "remote_handoff_revision_binding_block"
    assert stale["write_performed"] is False
    assert not (project_dir / "remote_handoffs").exists()
    assert _filesystem_snapshot(project_dir) == before_stale

    before_preview = _filesystem_snapshot(project_dir)
    preview = server.material_studio_remote_castep_prepare(
        project_id=project_id,
        expected_revision=0,
        calculation_name="energy_baseline",
        requested_cores=24,
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert preview["ok"] is True
    assert preview["status"] == "preview"
    assert preview["write_performed"] is False
    assert preview["submission_performed"] is False
    assert preview["remote_query_performed"] is False
    assert preview["needs_user_confirmation"] is True
    assert preview["execute_action"] == {
        "tool": "material_studio_remote_castep_prepare",
        "payload": {
            "project_id": project_id,
            "expected_revision": 0,
            "calculation_name": "energy_baseline",
            "requested_cores": 24,
            "expected_preview_manifest_sha256": preview["manifest_sha256"],
            "execution_mode": "execute",
            "working_dir": str(tmp_path.resolve()),
        },
        "payload_hint_is_directly_callable": True,
        "needs_user_confirmation": True,
    }
    assert _filesystem_snapshot(project_dir) == before_preview

    unconfirmed = server.material_studio_remote_castep_prepare(
        project_id=project_id,
        expected_revision=0,
        calculation_name="energy_baseline",
        requested_cores=24,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )
    assert unconfirmed["ok"] is False
    assert "expected_preview_manifest_sha256" in str(unconfirmed)
    assert not (project_dir / "remote_handoffs").exists()

    drifted = server.material_studio_remote_castep_prepare(
        project_id=project_id,
        expected_revision=0,
        calculation_name="energy_baseline",
        requested_cores=25,
        expected_preview_manifest_sha256=preview["manifest_sha256"],
        execution_mode="execute",
        working_dir=str(tmp_path),
    )
    assert drifted["ok"] is False
    assert "does not match the exact preview" in drifted["error"]
    assert not (project_dir / "remote_handoffs").exists()

    prepared = server.material_studio_remote_castep_prepare(
        **preview["execute_action"]["payload"]
    )
    assert prepared["ok"] is True
    assert prepared["status"] == "prepared"
    assert prepared["artifact_integrity_status"] == "verified"
    assert prepared["submission_performed"] is False
    assert prepared["remote_query_performed"] is False

    identity = {
        "scheduler_kind": "slurm",
        "scheduler_id": "cluster-alpha",
        "job_id": "73421",
    }
    submitted = server.material_studio_remote_job_record(
        project_id=project_id,
        bundle_id=prepared["bundle_id"],
        expected_manifest_sha256=prepared["manifest_sha256"],
        event_type="submission",
        **identity,
        recorded_at="2026-07-24T10:00:00+08:00",
        channel="manual_scheduler_submission",
        working_dir=str(tmp_path),
    )
    assert submitted["ok"] is True
    assert submitted["status"] == "submitted"
    assert submitted["submission_performed_by_this_module"] is False
    assert submitted["identity"] == identity

    recorded = server.material_studio_remote_job_record(
        project_id=project_id,
        bundle_id=prepared["bundle_id"],
        expected_manifest_sha256=prepared["manifest_sha256"],
        event_type="status",
        **identity,
        recorded_at="2026-07-24T10:01:00+08:00",
        state="running",
        detail="Observed by the external scheduler adapter.",
        scheduler_message_id="poll-0001",
        working_dir=str(tmp_path),
    )
    assert recorded["ok"] is True
    assert recorded["status"] == "status_recorded"
    assert recorded["current_state"] == "running"

    bundle_dir = Path(prepared["bundle_dir"])
    before_status = _filesystem_snapshot(bundle_dir)
    status = server.material_studio_remote_job_status(
        project_id=project_id,
        bundle_id=prepared["bundle_id"],
        expected_manifest_sha256=prepared["manifest_sha256"],
        **identity,
        working_dir=str(tmp_path),
    )
    assert status["ok"] is True
    assert status["status"] == "running"
    assert status["source"] == "local_append_only_event_journal"
    assert status["write_performed"] is False
    assert status["remote_query_performed"] is False
    assert status["scheduler_execution_performed"] is False
    assert _filesystem_snapshot(bundle_dir) == before_status

    wrong_identity = server.material_studio_remote_job_status(
        project_id=project_id,
        bundle_id=prepared["bundle_id"],
        expected_manifest_sha256=prepared["manifest_sha256"],
        scheduler_kind="slurm",
        scheduler_id="cluster-alpha",
        job_id="wrong-job",
        working_dir=str(tmp_path),
    )
    assert wrong_identity["ok"] is False
    assert "identity does not match" in wrong_identity["error"]
    assert _filesystem_snapshot(bundle_dir) == before_status


def test_new_remote_and_dmol3_previews_do_not_create_missing_workspaces(
    tmp_path: Path,
) -> None:
    missing_remote = tmp_path / "missing-remote-workspace"
    remote = server.material_studio_remote_castep_prepare(
        project_id="missing_remote_project",
        expected_revision=0,
        calculation_name="missing",
        execution_mode="preview",
        working_dir=str(missing_remote),
    )
    assert remote["ok"] is False
    assert "workspace does not exist" in remote["error"]
    assert not missing_remote.exists()

    missing_dmol3 = tmp_path / "missing-dmol3-workspace"
    dmol3 = server.material_studio_dmol3_relax_current(
        project_id="missing_dmol3_project",
        expected_revision=0,
        execution_mode="preview",
        working_dir=str(missing_dmol3),
    )
    assert dmol3["ok"] is False
    assert "workspace does not exist" in dmol3["error"]
    assert not missing_dmol3.exists()


def _molecule_spec(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "revision": 0,
        "software": "Materials Studio",
        "model_type": "molecule",
        "model": {
            "name": "water",
            "atoms": [
                {
                    "id": "O1",
                    "element": "O",
                    "xyz_angstrom": [0.0, 0.0, 0.0],
                    "charge": -0.8,
                },
                {
                    "id": "H1",
                    "element": "H",
                    "xyz_angstrom": [0.95, 0.0, 0.0],
                    "charge": 0.4,
                },
                {
                    "id": "H2",
                    "element": "H",
                    "xyz_angstrom": [-0.24, 0.92, 0.0],
                    "charge": 0.4,
                },
            ],
            "bonds": [
                {"atom1": "O1", "atom2": "H1", "type": "Single"},
                {"atom1": "O1", "atom2": "H2", "type": "Single"},
            ],
            "total_charge": 0,
            "spin_multiplicity": 1,
        },
        "simulation": {
            "module": "DMol3",
            "task": "GeometryOptimization",
            "quality": "Fine",
            "theory_level": "GGA",
            "geometry_optimization_quality": "Medium",
            "charge": 0,
            "use_symmetry": "No",
            "create_energy_evolution_chart": "Yes",
        },
        "outputs": {},
        "acceptance": {},
        "metadata": {"test": "public DMol3 tool"},
    }


class _ConvergedDMol3Runner:
    def __init__(self, source: ModelSpec, input_structure: Path) -> None:
        self.source = source
        self.input_structure = input_structure.resolve()
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
        calculation_dir = (
            self.input_structure.parent / "dmol3_geometry_optimization"
        )
        execution_input = calculation_dir / "in.xsd"
        output_structure = calculation_dir / "optimized_structure.xsd"
        output_report = calculation_dir / "dmol3_geometry_optimization.outmol"
        assert self.input_structure.is_file()
        assert execution_input.is_file()
        assert execution_input.read_bytes() == self.input_structure.read_bytes()
        assert "Modules->DMol3->GeometryOptimization->Run" in script
        assert str(execution_input).replace("\\", "\\\\") in script
        assert keep_script_name == "run_geometry_optimization.pl"
        assert job_prefix.startswith("d3_")

        molecule = self.source.model
        assert isinstance(molecule, MoleculeSpec)
        optimized_atoms = []
        for index, atom in enumerate(molecule.atoms, start=1):
            optimized_atoms.append(
                {
                    "id": atom.id,
                    "element": atom.element,
                    "xyz_angstrom": {
                        "x": atom.xyz_angstrom.x,
                        "y": atom.xyz_angstrom.y,
                        "z": atom.xyz_angstrom.z + (0.01 * index),
                    },
                }
            )
        atom_lines = []
        for index, atom in enumerate(optimized_atoms, start=1):
            xyz = atom["xyz_angstrom"]
            atom_lines.append(
                "    "
                f'<Atom3d ID="{index + 1}" '
                f'Name="MSMCPAtom{index:06d}" '
                f'XYZ="{xyz["x"]},{xyz["y"]},{xyz["z"]}" '
                f'Components="{atom["element"]}"/>'
            )
        output_structure.write_text(
            '<?xml version="1.0" encoding="latin1"?>\n'
            '<!DOCTYPE XSD []>\n'
            '<XSD Version="20.1" WrittenBy="Materials Studio 20.1">\n'
            "  <AtomisticTreeRoot>\n"
            + "\n".join(atom_lines)
            + "\n  </AtomisticTreeRoot>\n</XSD>\n",
            encoding="latin-1",
        )
        output_report.write_text(
            "Materials Studio DMol^3 version 2020\n"
            "Geometry optimization completed successfully in 1 steps.\n"
            "Message: DMol3 job finished successfully\n",
            encoding="latin-1",
        )
        payload = {
            "schema_version": DMOL3_GEOMETRY_RESULT_SCHEMA,
            "project_id": self.source.project_id,
            "base_revision": self.source.revision,
            "script_kind": "dmol3_geometry_optimization",
            "module": "DMol3",
            "task": "GeometryOptimization",
            "input_structure": str(execution_input),
            "output_structure": str(output_structure),
            "output_report": str(output_report),
            "materials_studio_api_contract": "Materials Studio 20.1",
            "result_keys": list(DMOL3_REVIEWED_RESULT_KEYS),
            "energy_evolution_charts_requested": True,
            "converged": True,
            "total_energy_kcal_per_mol": -76.25,
            "optimized_atoms": optimized_atoms,
            "result_document_names": {
                "EnergyChart": "DMol3 Energy",
                "ConvergenceChart": "DMol3 Convergence",
            },
        }
        job_dir = directory / "fake_runner_job"
        job_dir.mkdir(parents=True, exist_ok=True)
        fake_script = job_dir / keep_script_name
        fake_script.write_text(script, encoding="utf-8")
        return ScriptRunResult(
            command=["fake-RunMatScript.bat", str(fake_script)],
            job_id="fake-dmol3-relaxation",
            job_dir=job_dir,
            script_path=fake_script,
            return_code=0,
            stdout="fake DMol3 completed",
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


def test_public_dmol3_preview_execute_and_revision_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "public_dmol3_water"
    created = server.material_studio_model_create_from_spec(
        _molecule_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    input_structure = Path(created["planned_outputs"]["structure"])
    input_structure.parent.mkdir(parents=True, exist_ok=True)
    input_structure.write_bytes(b"fake source molecule XSD")
    project_dir = Path(created["state"]["project_dir"])

    stale = server.material_studio_dmol3_relax_current(
        project_id=project_id,
        expected_revision=1,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )
    assert stale["ok"] is False
    assert stale["status"] == "dmol3_revision_binding_block"
    assert stale["execution_started"] is False
    assert stale["revision_created"] is False

    before_preview = _filesystem_snapshot(project_dir)
    preview = server.material_studio_dmol3_relax_current(
        project_id=project_id,
        expected_revision=0,
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert preview["ok"] is True
    assert preview["status"] == "ready_for_explicit_execute"
    assert preview["preflight"]["execution_ready"] is True
    assert (
        preview["preflight"]["execution_input_is_hash_verified_copy"]
        is False
    )
    assert (
        preview["preflight"]["execution_input_copy_status"]
        == "planned_not_performed"
    )
    assert preview["execution_started"] is False
    assert preview["revision_created"] is False
    assert preview["gui_input_performed"] is False
    assert "Modules->DMol3->GeometryOptimization->Run" in preview["script"]
    assert preview["script_validation"]["valid"] is True
    assert (
        preview["execute_action"]["recommended_tool"]
        == "material_studio_dmol3_relax_current"
    )
    assert preview["execute_action"]["needs_user_confirmation"] is True
    assert _filesystem_snapshot(project_dir) == before_preview

    source, _ = ProjectStore(tmp_path).resolve_current(project_id)
    fake_runner = _ConvergedDMol3Runner(source, input_structure)
    monkeypatch.setattr(server, "runner", fake_runner)
    metadata_publications: list[Path] = []
    original_write_json_artifact = server._write_json_artifact

    def record_metadata_publication(
        path: str | Path,
        payload: dict[str, Any],
    ) -> None:
        metadata_publications.append(Path(path).resolve())
        original_write_json_artifact(path, payload)

    monkeypatch.setattr(
        server,
        "_write_json_artifact",
        record_metadata_publication,
    )
    executed = server.material_studio_dmol3_relax_current(
        project_id=project_id,
        expected_revision=0,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert executed["ok"] is True
    assert executed["status"] == "dmol3_relaxation_promoted"
    assert executed["execution_started"] is True
    assert executed["revision_created"] is True
    assert executed["new_revision"] == 1
    assert executed["revision"] == 1
    assert executed["gui_input_performed"] is False
    assert executed["open_in_gui"] is False
    assert executed["result_validation"]["ok"] is True
    assert executed["result_validation"]["converged"] is True
    assert executed["result_validation"]["atom_identity_preserved"] is True
    assert (
        executed["preflight"]["execution_input_is_hash_verified_copy"]
        is True
    )
    assert (
        executed["preflight"]["execution_input_copy_status"]
        == "verified_during_execute"
    )
    assert fake_runner.call_count == 1
    canonical_result_metadata = (
        project_dir
        / "outputs"
        / "r000"
        / "dmol3_geometry_optimization"
        / "result_metadata.json"
    ).resolve()
    assert metadata_publications.count(canonical_result_metadata) == 1
    assert executed["result_validation"]["geometry_evidence_verified"] is True
    assert Path(executed["promotion_evidence_path"]).resolve() == (
        project_dir / "revisions" / "r001_model_spec.json"
    ).resolve()
    assert len(metadata_publications) == 1

    promoted, pointer = ProjectStore(tmp_path).resolve_current(project_id)
    assert promoted.revision == 1
    assert pointer["revision"] == 1
    assert isinstance(promoted.model, MoleculeSpec)
    assert promoted.model.atoms[0].xyz_angstrom.z == pytest.approx(0.01)
    assert promoted.model.atoms[1].xyz_angstrom.z == pytest.approx(0.02)
    assert promoted.model.atoms[2].xyz_angstrom.z == pytest.approx(0.03)
    assert promoted.model.bonds == source.model.bonds
    assert promoted.model.total_charge == source.model.total_charge
    assert promoted.model.spin_multiplicity == source.model.spin_multiplicity
    assert promoted.metadata["geometry_relaxed"] is True
    assert (
        promoted.metadata["last_dmol3_geometry_optimization"][
            "atom_identity_preserved"
        ]
        is True
    )
    assert (
        promoted.metadata["last_dmol3_geometry_optimization"][
            "output_evidence"
        ]["verified"]
        is True
    )
    for output in executed["planned_outputs"].values():
        assert Path(output).is_file()


def test_public_dmol3_promotion_loses_cleanly_to_competing_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "public_dmol3_competing_revision"
    created = server.material_studio_model_create_from_spec(
        _molecule_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    input_structure = Path(created["planned_outputs"]["structure"])
    input_structure.parent.mkdir(parents=True, exist_ok=True)
    input_structure.write_bytes(b"fake source molecule XSD")
    source, _ = ProjectStore(tmp_path).resolve_current(project_id)
    monkeypatch.setattr(
        server,
        "runner",
        _ConvergedDMol3Runner(source, input_structure),
    )

    original_next_revision = ProjectStore.next_revision_number
    race_injected = False

    def inject_competing_revision(
        store: ProjectStore,
        requested_project_id: str,
    ) -> int:
        nonlocal race_injected
        if requested_project_id == project_id and not race_injected:
            race_injected = True
            current, _ = store.resolve_current(project_id)
            competing_revision = original_next_revision(store, project_id)
            competing = current.model_copy(
                update={
                    "revision": competing_revision,
                    "metadata": {
                        **current.metadata,
                        "competing_revision": True,
                    },
                },
                deep=True,
            )
            store.save_revision(
                project_id,
                competing,
                action="test_competing_patch",
                user_text="simulated concurrent writer",
                diff=["competing_revision=true"],
                expected_revision=current.revision,
                expected_new_revision=competing_revision,
            )
        return original_next_revision(store, requested_project_id)

    monkeypatch.setattr(
        ProjectStore,
        "next_revision_number",
        inject_competing_revision,
    )
    executed = server.material_studio_dmol3_relax_current(
        project_id=project_id,
        expected_revision=0,
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert race_injected is True
    assert executed["ok"] is False
    assert executed["status"] == "project_revision_conflict"
    assert executed["expected_revision"] == 0
    assert executed["current_revision"] == 1
    current, _ = ProjectStore(tmp_path).resolve_current(project_id)
    assert current.revision == 1
    assert current.metadata["competing_revision"] is True
    project_dir = Path(created["state"]["project_dir"])
    assert not (
        project_dir / "revisions" / "r002_model_spec.json"
    ).exists()
    competing_outputs = project_dir / "outputs" / "r001"
    assert not any(
        path.is_file() for path in competing_outputs.rglob("*")
    )
    assert (
        project_dir
        / "outputs"
        / "r000"
        / "dmol3_geometry_optimization"
        / "result_metadata.json"
    ).is_file()
