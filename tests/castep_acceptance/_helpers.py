from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceHarness,
    CastepAcceptanceRequest,
)
from material_studio_mcp_server.castep_acceptance.contracts import (
    CastepBenchmarkAcceptance,
    CastepVerificationReport,
    FIXED_PROJECT_ID,
    GuiInvariantProjection,
)
from material_studio_mcp_server.gui import ProcessInfo, WindowInfo
from material_studio_mcp_server.runner import ScriptRunResult
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.execution import canonical_json_sha256


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

class FakeGuiBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def list_processes(self) -> list[ProcessInfo]:
        self.calls.append(("list_processes", None))
        return [ProcessInfo(name="MatStudio.exe", pid=4101)]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        self.calls.append(("list_windows", pid))
        return [
            WindowInfo(
                handle=5101,
                title="Existing Materials Studio window",
                pid=4101,
                is_visible=True,
                is_minimized=False,
                is_foreground=True,
            )
        ]

    def _prohibited(self, name: str):
        self.calls.append((name, None))
        raise AssertionError(f"GUI method {name} must not be called")

    def activate_window(self, *args, **kwargs):
        return self._prohibited("activate_window")

    def capture_window(self, *args, **kwargs):
        return self._prohibited("capture_window")

    def open_file(self, *args, **kwargs):
        return self._prohibited("open_file")

    def open_file_in_existing_window(self, *args, **kwargs):
        return self._prohibited("open_file_in_existing_window")

    def launch_app(self, *args, **kwargs):
        return self._prohibited("launch_app")


class FakeElectronicRunner:
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
        output_structure = directory / "result_structure.cif"
        output_report = directory / "castep_report.txt"
        assert input_structure.is_file()
        assert "Modules->CASTEP->Energy->Run" in script
        assert "Quality => 'Medium'" in script
        assert "XCFunctional => 'PBE'" in script
        assert "EnergyCutoff => 300" in script
        assert "KPointDerivation => 'CustomGrid'" in script
        assert "ParameterA => 2" in script
        assert "ParameterB => 2" in script
        assert "ParameterC => 1" in script
        assert "DipoleCorrection => 'Self-consistent'" in script
        assert keep_script_name == "run_castep_electronic.pl"
        shutil.copy2(input_structure, output_structure)
        output_report.write_text("Offline fake CASTEP Energy report.\n", encoding="utf-8")

        payload = {
            "schema_version": "material_studio_castep_electronic_result_v1",
            "project_id": self.source.project_id,
            "base_revision": 0,
            "script_kind": "castep_electronic_calculation",
            "module": "CASTEP",
            "task": "Energy",
            "input_structure": str(input_structure),
            "output_structure": str(output_structure),
            "output_report": str(output_report),
            "materials_studio_api_contract": "Materials Studio 20.1",
            "result_keys": _RESULT_KEYS,
            "required_result_document": None,
            "total_energy_kcal_per_mol": -101.25,
            "free_energy_kcal_per_mol": -100.75,
            "band_gap_ev": 1.12,
            "fermi_level_ev": 0.42,
            "work_function_ev": None,
            "work_function_top_ev": None,
            "work_function_bottom_ev": None,
            "result_document_names": {
                "BandStructureChart": None,
                "DOSChart": None,
                "PartialDOSChart": None,
            },
        }
        job_dir = directory / "offline_fake_runner_job"
        job_dir.mkdir(parents=True, exist_ok=False)
        script_path = job_dir / keep_script_name
        script_path.write_text(script, encoding="utf-8")
        native_castep = job_dir / "fixed_energy.castep"
        native_castep.write_text(_NATIVE_CASTEP_OUTPUT, encoding="utf-8")
        assert not tuple(job_dir.glob("*.bands"))
        return ScriptRunResult(
            command=["offline-fake-runner", str(script_path)],
            job_id="offline-fake-castep-energy",
            job_dir=job_dir,
            script_path=script_path,
            return_code=0,
            stdout="offline fake CASTEP completed",
            stderr="",
            output_file=None,
            log_file=None,
            materials_output="",
            materials_log="",
            success=True,
            timed_out=False,
            parsed_json=payload,
            created_files=[script_path, native_castep],
            duration_seconds=0.01,
        )


def run_fake_acceptance(monkeypatch, tmp_path: Path):
    from material_studio_mcp_server import server
    from material_studio_mcp_server.castep_acceptance.profile import (
        build_fixed_candidate,
    )

    short_id = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:10]
    workspace = tmp_path.parent / f"ca-{short_id}"
    source = build_fixed_candidate(FIXED_PROJECT_ID)
    fake_runner = FakeElectronicRunner(source)
    monkeypatch.setattr(server, "runner", fake_runner)
    gui = FakeGuiBackend()
    harness = CastepAcceptanceHarness(
        tool_resolver=lambda: server.material_studio_castep_run_current,
        gui_backend_resolver=lambda: gui,
        real_environment=False,
    )
    preview = harness.run(
        CastepAcceptanceRequest(
            request_id="offline-fake-acceptance",
            workspace_root=workspace,
            execution_mode="preview",
            timeout_seconds=30,
        )
    )
    result = harness.run(
        CastepAcceptanceRequest(
            request_id="offline-fake-acceptance",
            workspace_root=workspace,
            execution_mode="execute",
            expected_plan_sha256=preview.plan_sha256,
            real_opt_in="--run-real-castep",
            timeout_seconds=30,
        )
    )
    return result, fake_runner, gui


def synthetic_verification(*, real: bool = False) -> CastepVerificationReport:
    digest = "a" * 64
    return CastepVerificationReport(
        status="PASS" if real else "NOT_RUN",
        failure_codes=(),
        real_environment=real,
        source_profile_exact=True,
        effective_settings_exact=True,
        preview_side_effect_free=True,
        public_tool_reused=True,
        runner_identity_valid=real,
        runner_success=True,
        execute_invocation_count=1,
        backend_execution_count=1,
        revision_execution_lock_verified=True,
        execution_attempt_event_types=("started", "completed"),
        execution_attempt_binding_verified=True,
        electronic_receipt_binding_verified=True,
        native_castep_file_count=1,
        native_scf_status="completed_below_max_cycles",
        native_scf_audit_valid=True,
        total_energy_kcal_per_mol=-101.25,
        total_energy_finite=True,
        structure_unchanged=True,
        metadata_only_result_revision_verified=True,
        result_revision=1,
        gui=GuiInvariantProjection(
            process_count_before_after=(1, 1),
            window_count_before_after=(1, 1),
            process_inventory_sha256_before_after=(digest, digest),
            window_inventory_sha256_before_after=("b" * 64, "b" * 64),
            identity_unchanged=True,
            process_launched=False,
        ),
        candidate_model_spec_sha256="c" * 64,
        source_structure_sha256="d" * 64,
        electronic_receipt_sha256="e" * 64,
        execution_attempt_sha256="f" * 64,
        native_castep_sha256="1" * 64,
    )


def synthetic_benchmark_acceptance() -> CastepBenchmarkAcceptance:
    from material_studio_mcp_server.benchmark_evaluation import FiveValidityStates

    shared = FiveValidityStates(
        structure_valid="PASS",
        semiconductor_domain_valid="PASS",
        ms_roundtrip_valid="NOT_RUN",
        calculation_evidence_valid="NOT_RUN",
        scientifically_verified="NOT_RUN",
    )
    states = shared.model_copy(update={"calculation_evidence_valid": "PASS"})
    verification = synthetic_verification(real=True)
    return CastepBenchmarkAcceptance(
        evaluation_run_id="synthetic-real-castep",
        shared_evaluator_report_sha256="2" * 64,
        shared_evaluator_states=shared,
        states=states,
        overall_status="PASS",
        calculation_evidence_sha256=canonical_json_sha256(
            verification.model_dump(mode="json")
        ),
        real_castep="PASS",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_canonical_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()
