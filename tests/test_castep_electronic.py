from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from material_studio_mcp_server import server
from material_studio_mcp_server.castep_electronic import (
    RESULT_DOCUMENT_BY_TASK,
    verify_castep_electronic_receipt,
)
from material_studio_mcp_server.parsers.castep_log import (
    CASTEP_ELECTRONIC_RESULT_SCHEMA,
    CASTEP_GEOMETRY_RESULT_SCHEMA,
    validate_castep_electronic_result,
)
from material_studio_mcp_server.runner import ScriptRunResult
from material_studio_mcp_server.specs.castep import (
    CastepEnergySpec,
    CastepTask,
)
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore
from material_studio_mcp_server.translators import (
    render_castep_electronic_script,
    write_crystal_cif,
)
from material_studio_mcp_server.validators import validate_generated_script


_RESULT_KEYS = [
    "Structure",
    "Report",
    "TotalEnergy",
    "FreeEnergy",
    "BandGap",
    "FermiLevel",
    "WorkFunction",
    "WorkFunctionTop",
    "WorkFunctionBottom",
    "BandStructureChart",
    "DOSChart",
    "PartialDOSChart",
]

_NATIVE_BANDS = """\
Number of k-points 2
Number of spin components 1
Number of electrons 4
Number of eigenvalues 3
Fermi energy (in atomic units) 0.100000
Unit cell vectors
4 0 0
0 4 0
0 0 4
K-point 1 0 0 0 0.5
Spin component 1
-0.30
0.05
0.20
K-point 2 0.5 0 0 0.5
Spin component 1
-0.25
0.08
0.25
"""

_NATIVE_CASTEP_OUTPUT = """\
total energy / atom convergence tol. : 0.1000E-05 eV
convergence tolerance window : 3 cycles
max. number of SCF cycles : 100
SCF loop Energy Energy gain Timer <-- SCF
1 -8.50000000E+002 1.0E-2 1.0 <-- SCF
8 -8.58547076E+002 1.0E-8 2.0 <-- SCF
Final energy, E = -858.5426000919 eV
Total time = 2.0 s
"""


def _silicon_spec(project_id: str) -> dict[str, Any]:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    return payload


def _create_silicon(tmp_path: Path, project_id: str) -> ModelSpec:
    created = server.material_studio_model_create_from_spec(
        _silicon_spec(project_id),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True
    return ProjectStore(tmp_path).load_current(project_id)


class _ElectronicRunner:
    def __init__(
        self,
        source: ModelSpec,
        task: CastepTask,
        *,
        missing_result_document: bool = False,
        payload_revision: int | None = None,
        change_structure: bool = False,
    ) -> None:
        self.source = source
        self.task = task
        self.missing_result_document = missing_result_document
        self.payload_revision = payload_revision
        self.change_structure = change_structure
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
        output_structure = directory / "result_structure.cif"
        output_report = directory / "castep_report.txt"
        assert input_structure.is_file()
        assert "Modules->CASTEP->Energy->Run" in script
        assert keep_script_name == "run_castep_electronic.pl"
        assert f"castep_{server._castep_electronic_task_slug(self.task)}" in job_prefix

        if self.change_structure:
            atoms = list(self.source.model.basis_atoms)
            first = atoms[0]
            atoms[0] = first.model_copy(
                update={
                    "fractional": first.fractional.model_copy(
                        update={"x": first.fractional.x + 0.01}
                    )
                }
            )
            write_crystal_cif(
                self.source.model.model_copy(update={"basis_atoms": atoms}),
                output_structure,
            )
        else:
            shutil.copy2(input_structure, output_structure)
        output_report.write_text(
            f"Fake CASTEP {self.task.value} report.\n",
            encoding="utf-8",
        )

        expected_document = RESULT_DOCUMENT_BY_TASK[self.task]
        document_names = {
            "BandStructureChart": None,
            "DOSChart": None,
            "PartialDOSChart": None,
        }
        if expected_document and not self.missing_result_document:
            document_names[expected_document] = f"fake_{expected_document}"
        payload = {
            "schema_version": CASTEP_ELECTRONIC_RESULT_SCHEMA,
            "project_id": self.source.project_id,
            "base_revision": (
                self.source.revision
                if self.payload_revision is None
                else self.payload_revision
            ),
            "script_kind": "castep_electronic_calculation",
            "module": "CASTEP",
            "task": self.task.value,
            "input_structure": str(input_structure),
            "output_structure": str(output_structure),
            "output_report": str(output_report),
            "materials_studio_api_contract": "Materials Studio 20.1",
            "result_keys": _RESULT_KEYS,
            "required_result_document": expected_document,
            "total_energy_kcal_per_mol": -101.25,
            "free_energy_kcal_per_mol": -100.75,
            "band_gap_ev": 1.12,
            "fermi_level_ev": 0.42,
            "work_function_ev": None,
            "work_function_top_ev": None,
            "work_function_bottom_ev": None,
            "result_document_names": document_names,
        }
        fake_job_dir = directory / "fake_runner_job"
        fake_job_dir.mkdir(parents=True, exist_ok=False)
        fake_script = fake_job_dir / keep_script_name
        fake_script.write_text(script, encoding="utf-8")
        native_artifact = fake_job_dir / f"{self.task.value}.castep"
        native_artifact.write_text(_NATIVE_CASTEP_OUTPUT, encoding="utf-8")
        native_bands = fake_job_dir / f"{self.task.value}.bands"
        native_bands.write_text(_NATIVE_BANDS, encoding="utf-8")
        return ScriptRunResult(
            command=["fake-RunMatScript.bat", str(fake_script)],
            job_id=f"fake-{self.task.value}",
            job_dir=fake_job_dir,
            script_path=fake_script,
            return_code=0,
            stdout="fake CASTEP electronic calculation completed",
            stderr="",
            output_file=None,
            log_file=None,
            materials_output="",
            materials_log="",
            success=True,
            timed_out=False,
            parsed_json=payload,
            created_files=[fake_script, native_artifact, native_bands],
            duration_seconds=0.01,
        )


class _ConvergedGeometryRunner:
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
        output_structure = directory / "relaxed_structure.cif"
        output_report = directory / "castep_report.txt"
        atoms = list(self.source.model.basis_atoms)
        first = atoms[0]
        atoms[0] = first.model_copy(
            update={
                "fractional": first.fractional.model_copy(
                    update={"x": first.fractional.x + 0.001}
                )
            }
        )
        write_crystal_cif(
            self.source.model.model_copy(update={"basis_atoms": atoms}),
            output_structure,
        )
        output_report.write_text("Fake converged relaxation.\n", encoding="utf-8")
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
        fake_job_dir = directory / "fake_geometry_job"
        fake_job_dir.mkdir(parents=True, exist_ok=False)
        fake_script = fake_job_dir / keep_script_name
        fake_script.write_text(script, encoding="utf-8")
        return ScriptRunResult(
            command=["fake-RunMatScript.bat", str(fake_script)],
            job_id="fake-geometry",
            job_dir=fake_job_dir,
            script_path=fake_script,
            return_code=0,
            stdout="fake relaxation completed",
            stderr="",
            output_file=None,
            log_file=None,
            materials_output="",
            materials_log="",
            success=True,
            timed_out=False,
            parsed_json=payload,
            created_files=[fake_script],
            duration_seconds=0.01,
        )


class _GuiSession:
    def __init__(self, *, single_window_policy_ok: bool) -> None:
        self.single_window_policy_ok = single_window_policy_ok
        self.open_calls: list[dict[str, Any]] = []

    def status(self, *, project_id: str, revision: int) -> dict[str, Any]:
        count = 1 if self.single_window_policy_ok else 2
        return {
            "ok": True,
            "supported": True,
            "window_found": True,
            "process_count": count,
            "window_count": count,
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
                "process_count": count,
                "window_count": count,
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

    def open_structure(
        self,
        structure_path: str | Path,
        *,
        project_id: str,
        revision: int,
        take_snapshot: bool,
    ) -> dict[str, Any]:
        assert self.single_window_policy_ok
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


def _relax_current(monkeypatch, tmp_path: Path, source: ModelSpec) -> ModelSpec:
    geometry_runner = _ConvergedGeometryRunner(source)
    monkeypatch.setattr(server, "runner", geometry_runner)
    result = server.material_studio_castep_relax_current(
        project_id=source.project_id,
        execution_mode="execute",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["status"] == "castep_relaxation_promoted"
    assert geometry_runner.call_count == 1
    return ProjectStore(tmp_path).load_current(source.project_id)


def test_electronic_renderer_uses_reviewed_ms20_1_settings_and_safe_json() -> None:
    spec = CastepEnergySpec(
        task="ProjectedDensityOfStates",
        cutoff_energy_ev=520,
        kpoint_separation=0.04,
        properties_kpoint_separation=0.03,
        dos_energy_max_ev=12,
        dos_extra_bands=24,
        dos_energy_tolerance_ev=1.0e-5,
        dos_smearing_width_ev=0.15,
        dos_integration_method="Smearing",
    )
    script = render_castep_electronic_script(
        spec,
        "input.cif",
        "result.cif",
        "report.txt",
        project_id="safe_project",
        base_revision=3,
    )

    assert "Modules->CASTEP->Energy->Run" in script
    assert "CalculateDOS => 'Partial'" in script
    assert "PropertiesKPointSeparation => 0.03" in script
    assert "DOSEmax => 12" in script
    assert "DOSNumExtraBands => 24" in script
    assert "DOSEnergyTolerance => 1e-05" in script
    assert "DOSSmearingWidth => 0.15" in script
    assert "DOSPreferredIntegrationMethod => 'Smearing'" in script
    assert "$value =~ s/\\\\/\\\\\\\\/g;" in script
    assert '$value =~ s/"/\\\\"/g;' in script
    assert "__MS_MCP_JSON_START__" in script
    assert "__MS_MCP_JSON_END__" in script
    validation = validate_generated_script(script)
    assert validation["valid"] is True
    lowered = script.lower()
    assert "system(" not in lowered
    assert "unlink(" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered


def test_electronic_result_parser_is_strict_and_requires_task_chart(
    tmp_path: Path,
) -> None:
    input_structure = tmp_path / "input.cif"
    output_structure = tmp_path / "output.cif"
    output_report = tmp_path / "report.txt"
    for path in (input_structure, output_structure, output_report):
        path.write_text("evidence\n", encoding="utf-8")
    payload = {
        "schema_version": CASTEP_ELECTRONIC_RESULT_SCHEMA,
        "project_id": "strict_result",
        "base_revision": 2,
        "script_kind": "castep_electronic_calculation",
        "module": "CASTEP",
        "task": "BandStructure",
        "input_structure": str(input_structure),
        "output_structure": str(output_structure),
        "output_report": str(output_report),
        "materials_studio_api_contract": "Materials Studio 20.1",
        "result_keys": _RESULT_KEYS,
        "required_result_document": "BandStructureChart",
        "total_energy_kcal_per_mol": -10.0,
        "free_energy_kcal_per_mol": None,
        "band_gap_ev": 1.1,
        "fermi_level_ev": 0.2,
        "work_function_ev": None,
        "work_function_top_ev": None,
        "work_function_bottom_ev": None,
        "result_document_names": {
            "BandStructureChart": "Bands Chart",
            "DOSChart": None,
            "PartialDOSChart": None,
        },
    }
    valid = validate_castep_electronic_result(
        payload,
        project_id="strict_result",
        base_revision=2,
        task="BandStructure",
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert valid["ok"] is True
    assert valid["backend_run_completed"] is True
    assert valid["scientific_convergence_verified"] is False
    assert valid["numeric_curve_data_exported"] is False

    missing_chart = json.loads(json.dumps(payload))
    missing_chart["result_document_names"]["BandStructureChart"] = None
    invalid = validate_castep_electronic_result(
        missing_chart,
        project_id="strict_result",
        base_revision=2,
        task="BandStructure",
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid["ok"] is False
    assert any("BandStructureChart" in item for item in invalid["errors"])

    extra_field = {**payload, "unexpected": True}
    invalid_extra = validate_castep_electronic_result(
        extra_field,
        project_id="strict_result",
        base_revision=2,
        task="BandStructure",
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid_extra["ok"] is False
    assert any(
        "unexpected" in item and "Extra inputs are not permitted" in item
        for item in invalid_extra["errors"]
    )


def test_castep_electronic_preview_never_runs_or_materializes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_preview")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("preview must not call the Materials Studio runner")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="preview",
        task="Energy",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["status"] == "ready_for_explicit_execute"
    assert result["execution_started"] is False
    assert result["revision_created"] is False
    assert result["preflight"]["execution_ready"] is True
    assert not Path(result["run_directory"]).exists()
    assert not Path(result["planned_outputs"]["structure"]).exists()
    assert ProjectStore(tmp_path).load_current(source.project_id).revision == 0


def test_energy_result_records_metadata_only_revision_and_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_energy")
    fake_runner = _ElectronicRunner(source, CastepTask.ENERGY)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="Energy",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["status"] == "castep_electronic_result_recorded"
    assert result["revision_created"] is True
    assert result["new_revision"] == 1
    assert result["native_output_audit_status"] == "complete"
    assert result["native_scf_status"] == "completed_below_max_cycles"
    assert result["native_scf_last_iteration"] == 8
    assert result["native_scf_maximum_cycles_reached"] is False
    assert fake_runner.call_count == 1
    current = ProjectStore(tmp_path).load_current(source.project_id)
    assert current.revision == 1
    assert current.model.model_dump(mode="json") == source.model.model_dump(mode="json")
    summary = verify_castep_electronic_receipt(current)
    assert summary is not None
    assert summary["binding_verified"] is True
    assert summary["task"] == "Energy"
    assert summary["scientific_convergence_verified"] is False
    assert summary["numeric_curve_data_exported"] is False
    assert summary["native_artifact_count"] == 2
    assert summary["native_output_audit"]["status"] == "complete"
    assert summary["native_output_audit"]["castep_output_audit"]["status"] == (
        "completed_below_max_cycles"
    )
    assert summary["derived_artifact_count"] == 0
    receipt = current.metadata["last_castep_electronic_calculation"]
    script_path = Path(receipt["script_path"])
    payload_path = Path(receipt["result_payload_path"])
    assert script_path.is_file()
    assert payload_path.is_file()
    original_script = script_path.read_bytes()
    script_path.write_bytes(original_script + b"\n# tampered\n")
    tampered_script = verify_castep_electronic_receipt(current)
    assert tampered_script is not None
    assert tampered_script["binding_verified"] is False
    assert tampered_script["checks"]["script_file"] is False
    script_path.write_bytes(original_script)
    original_payload = payload_path.read_bytes()
    payload_path.write_bytes(original_payload + b"\n")
    tampered_payload = verify_castep_electronic_receipt(current)
    assert tampered_payload is not None
    assert tampered_payload["binding_verified"] is False
    assert tampered_payload["checks"]["result_payload_file"] is False
    payload_path.write_bytes(original_payload)
    restored_summary = verify_castep_electronic_receipt(current)
    assert restored_summary is not None
    assert restored_summary["binding_verified"] is True
    legacy_receipt = dict(receipt)
    legacy_receipt["schema_version"] = "material_studio_castep_electronic_receipt_v1"
    for key in (
        "numeric_curve_kind",
        "native_band_kpoint_path_exported",
        "pdos_projection_weights_exported",
        "native_output_audit",
        "native_output_audit_path",
        "native_output_audit_payload_sha256",
        "native_output_audit_file_sha256",
        "derived_artifact_count",
        "derived_artifacts",
    ):
        legacy_receipt.pop(key, None)
    legacy_receipt["numeric_curve_data_exported"] = False
    legacy_metadata = dict(current.metadata)
    legacy_metadata["last_castep_electronic_calculation"] = legacy_receipt
    legacy_metadata["castep_electronic_calculation_history"] = [legacy_receipt]
    legacy_spec = ModelSpec.model_validate(
        current.model_copy(update={"metadata": legacy_metadata}).model_dump(
            mode="json"
        )
    )
    legacy_summary = verify_castep_electronic_receipt(legacy_spec)
    assert legacy_summary is not None
    assert legacy_summary["schema_version"].endswith("_v1")
    assert legacy_summary["binding_verified"] is True
    diagnostic_summary = result["view_audit"]["health"]["semiconductor_health"][
        "castep_electronic_result_summary"
    ]
    assert diagnostic_summary["binding_verified"] is True
    csv_path = Path(
        result["view_bundle_files"][
            "semiconductor_castep_electronic_result_csv"
        ]
    )
    assert csv_path.is_file()
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "scientific_convergence_verified" in csv_text
    assert "native_output_audit_status" in csv_text
    assert "completed_below_max_cycles" in csv_text
    assert "native_band_eigenvalue_count" in csv_text
    assert "False" in csv_text


def test_property_task_requires_verified_geometry_relaxation_before_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_unrelaxed_band")
    fake_runner = _ElectronicRunner(source, CastepTask.BAND_STRUCTURE)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="BandStructure",
        open_in_gui=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "castep_electronic_preflight_blocked"
    assert (
        "verified_geometry_relaxation_required_for_property_task"
        in result["preflight"]["blocking_reasons"]
    )
    assert fake_runner.call_count == 0
    assert not Path(result["run_directory"]).exists()
    assert ProjectStore(tmp_path).load_current(source.project_id).revision == 0


def test_band_structure_after_verified_relaxation_records_required_chart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_relaxed_band")
    relaxed = _relax_current(monkeypatch, tmp_path, source)
    fake_runner = _ElectronicRunner(relaxed, CastepTask.BAND_STRUCTURE)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="BandStructure",
        open_in_gui=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["new_revision"] == 2
    receipt = result["electronic_receipt"]
    assert receipt["required_result_document"] == "BandStructureChart"
    assert receipt["result_document_name"] == "fake_BandStructureChart"
    assert receipt["band_path_binding_verified"] is False
    assert receipt["numeric_curve_data_exported"] is True
    assert receipt["numeric_curve_kind"] == "native_castep_band_eigenvalues"
    assert receipt["native_band_kpoint_path_exported"] is True
    assert Path(receipt["derived_artifacts"][0]["path"]).is_file()
    current = ProjectStore(tmp_path).load_current(source.project_id)
    verified = verify_castep_electronic_receipt(current)
    assert verified["binding_verified"] is True
    assert verified["numeric_curve_data_exported"] is True
    derived_path = Path(receipt["derived_artifacts"][0]["path"])
    original_derived = derived_path.read_bytes()
    derived_path.write_bytes(original_derived + b"\n")
    tampered_derived = verify_castep_electronic_receipt(current)
    assert tampered_derived["binding_verified"] is False
    assert tampered_derived["checks"]["derived_artifacts"] is False
    assert tampered_derived["numeric_curve_data_exported"] is False
    assert tampered_derived["numeric_curve_data_claimed"] is True
    derived_path.write_bytes(original_derived)
    audit_path = Path(receipt["native_output_audit_path"])
    original_audit = audit_path.read_bytes()
    audit_path.write_bytes(original_audit + b"\n")
    tampered_audit = verify_castep_electronic_receipt(current)
    assert tampered_audit["binding_verified"] is False
    assert tampered_audit["checks"]["native_output_audit_file"] is False
    assert tampered_audit["numeric_curve_data_exported"] is False
    audit_path.write_bytes(original_audit)
    assert verify_castep_electronic_receipt(current)["binding_verified"] is True

    invalid_receipt = dict(receipt)
    invalid_audit = dict(receipt["native_output_audit"])
    invalid_audit["schema_version"] = "unsupported_native_audit_v0"
    invalid_receipt["native_output_audit"] = invalid_audit
    invalid_metadata = dict(current.metadata)
    invalid_metadata["last_castep_electronic_calculation"] = invalid_receipt
    invalid_metadata["castep_electronic_calculation_history"] = [invalid_receipt]
    invalid_spec = ModelSpec.model_validate(
        current.model_copy(update={"metadata": invalid_metadata}).model_dump(
            mode="json"
        )
    )
    invalid_summary = verify_castep_electronic_receipt(invalid_spec)
    assert invalid_summary["binding_verified"] is False
    assert invalid_summary["checks"]["native_output_audit_contract"] is False
    assert invalid_summary["numeric_curve_data_exported"] is False


def test_dos_smearing_exports_provenance_bound_numeric_curve(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_relaxed_dos")
    relaxed = _relax_current(monkeypatch, tmp_path, source)
    fake_runner = _ElectronicRunner(relaxed, CastepTask.DENSITY_OF_STATES)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="DensityOfStates",
        properties_kpoint_separation=0.03,
        dos_integration_method="Smearing",
        dos_smearing_width_ev=0.2,
        dos_energy_max_ev=8.0,
        open_in_gui=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    receipt = result["electronic_receipt"]
    assert receipt["numeric_curve_data_exported"] is True
    assert receipt["numeric_curve_kind"] == (
        "mcp_gaussian_total_dos_from_native_bands"
    )
    assert {Path(item["path"]).name for item in receipt["derived_artifacts"]} == {
        "band_eigenvalues.csv",
        "total_dos_gaussian.csv",
    }
    assert result["result"]["numeric_curve_data_exported"] is True
    assert Path(result["planned_outputs"]["native_output_audit"]).is_file()


def test_pdos_keeps_projection_weights_fail_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_relaxed_pdos")
    relaxed = _relax_current(monkeypatch, tmp_path, source)
    fake_runner = _ElectronicRunner(relaxed, CastepTask.PROJECTED_DENSITY_OF_STATES)
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="ProjectedDensityOfStates",
        properties_kpoint_separation=0.03,
        dos_integration_method="Smearing",
        dos_smearing_width_ev=0.2,
        dos_energy_max_ev=8.0,
        open_in_gui=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    receipt = result["electronic_receipt"]
    assert receipt["numeric_curve_data_exported"] is False
    assert receipt["pdos_projection_weights_exported"] is False
    assert len(receipt["derived_artifacts"]) == 1
    assert Path(receipt["derived_artifacts"][0]["path"]).name == (
        "band_eigenvalues.csv"
    )


def test_malformed_electronic_chart_preserves_evidence_without_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_bad_chart")
    relaxed = _relax_current(monkeypatch, tmp_path, source)
    fake_runner = _ElectronicRunner(
        relaxed,
        CastepTask.DENSITY_OF_STATES,
        missing_result_document=True,
    )
    monkeypatch.setattr(server, "runner", fake_runner)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="DensityOfStates",
        open_in_gui=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "castep_electronic_execution_failed"
    assert result["revision_created"] is False
    assert fake_runner.call_count == 1
    assert Path(result["run_directory"]).is_dir()
    assert Path(result["result_metadata_path"]).is_file()
    assert ProjectStore(tmp_path).load_current(source.project_id).revision == 1


def test_multi_window_hotload_preflight_blocks_before_castep_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_multi_window")
    fake_runner = _ElectronicRunner(source, CastepTask.ENERGY)
    gui = _GuiSession(single_window_policy_ok=False)
    monkeypatch.setattr(server, "runner", fake_runner)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="Energy",
        open_in_gui=True,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "single_window_gui_preflight_blocked"
    assert fake_runner.call_count == 0
    assert gui.open_calls == []
    assert not Path(result["run_directory"]).exists()


def test_successful_energy_hotloads_only_the_existing_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _create_silicon(tmp_path, "electronic_same_window")
    fake_runner = _ElectronicRunner(source, CastepTask.ENERGY)
    gui = _GuiSession(single_window_policy_ok=True)
    monkeypatch.setattr(server, "runner", fake_runner)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: gui)

    result = server.material_studio_castep_run_current(
        project_id=source.project_id,
        execution_mode="execute",
        task="Energy",
        open_in_gui=True,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["new_revision"] == 1
    assert fake_runner.call_count == 1
    assert len(gui.open_calls) == 1
    assert gui.open_calls[0]["revision"] == 1
    assert result["gui_open"]["same_window_open_used"] is True
    assert result["gui_open"]["new_process_launched"] is False


def test_live_natural_language_electronic_execution_and_preview_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    preview_source = _create_silicon(tmp_path, "electronic_nl_preview")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("explicit preview must override natural-language run intent")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    preview = server.material_studio_live_modeling_request(
        "Run CASTEP single-point energy on the current model now.",
        project_id=preview_source.project_id,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    assert preview["ok"] is True
    assert preview["workflow"] == "castep_electronic_calculation"
    assert preview["execution_mode"] == "preview"
    assert preview["execution_started"] is False
    assert preview["revision_created"] is False
    assert ProjectStore(tmp_path).load_current(preview_source.project_id).revision == 0

    execute_source = _create_silicon(tmp_path, "electronic_nl_execute")
    fake_runner = _ElectronicRunner(execute_source, CastepTask.ENERGY)
    monkeypatch.setattr(server, "runner", fake_runner)
    executed = server.material_studio_live_modeling_request(
        "Run CASTEP single-point energy on the current model now.",
        project_id=execute_source.project_id,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    assert executed["ok"] is True
    assert executed["status"] == "castep_electronic_result_recorded"
    assert executed["execution_mode"] == "execute"
    assert executed["execution_mode_source"] == (
        "explicit_castep_electronic_execution_intent"
    )
    assert executed["nl_plan"]["kind"] == "castep_electronic_calculation"
    assert fake_runner.call_count == 1
    assert ProjectStore(tmp_path).load_current(execute_source.project_id).revision == 1
