from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from material_studio_mcp_server.config import MaterialStudioConfig, resolve_config
from material_studio_mcp_server.gui import ProcessInfo, WindowInfo, WindowsGuiBackend
from material_studio_mcp_server.ms_roundtrip import (
    CandidateBinding,
    MaterialsStudioRoundtripAdapter,
    RoundtripExecutionResult,
    RoundtripPlan,
    RoundtripRequest,
    capture_gui_inventory,
)
from material_studio_mcp_server.ms_roundtrip.comparison import (
    _canonicalizer_compatible_cif,
)
from material_studio_mcp_server.runner import MaterialStudioRunner

from ._helpers import build_candidate
from .test_benchmark import _evaluate_completed_roundtrip


class _InventoryOnlyWindowsBackend:
    """Expose only the two read-only GUI inventory operations."""

    def __init__(self) -> None:
        self._backend = WindowsGuiBackend()
        self.calls: list[str] = []

    def list_processes(self) -> list[ProcessInfo]:
        self.calls.append("list_processes")
        return self._backend.list_processes()

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        self.calls.append("list_windows")
        return self._backend.list_windows(pid=pid)


def _require_real_prerequisite(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(f"real MS 20.1 prerequisite failed: {message}", pytrace=False)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_materials_studio_20_1_cif_roundtrip_acceptance(
    pytestconfig: pytest.Config,
    request: pytest.FixtureRequest,
) -> None:
    if not pytestconfig.getoption("--run-real-ms"):
        pytest.skip("requires explicit --run-real-ms opt-in")

    _require_real_prerequisite(os.name == "nt", "Windows is required")
    _require_real_prerequisite(
        not bool(os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE")),
        "MATERIAL_STUDIO_COMMAND_TEMPLATE must be unset",
    )

    # MatServer 20.1 still expands several internal filenames under MAX_PATH,
    # while evaluator roots reject Windows 8.3 aliases. Use one explicit,
    # long-form user temp parent with a short isolated child name.
    temp_parent = Path(
        os.environ.get(
            "LOCALAPPDATA",
            str(Path.home() / "AppData" / "Local"),
        )
    ) / "Temp"
    tmp_path = Path(tempfile.mkdtemp(prefix="msrt-", dir=temp_parent))
    request.addfinalizer(lambda: shutil.rmtree(tmp_path, ignore_errors=True))

    config = resolve_config(cwd=tmp_path)
    runner_path = config.runner
    _require_real_prerequisite(runner_path is not None, "runner was not detected")
    assert runner_path is not None
    try:
        runner_path = runner_path.expanduser().resolve(strict=True)
    except OSError as exc:
        pytest.fail(
            f"real MS 20.1 prerequisite failed: runner is unavailable ({exc})",
            pytrace=False,
        )
    _require_real_prerequisite(runner_path.is_file(), "runner is not a regular file")
    _require_real_prerequisite(
        runner_path.name.casefold() == "runmatscript.bat",
        "runner must be exactly RunMatScript.bat",
    )
    _require_real_prerequisite(
        any(
            part.casefold()
            in {"materials studio 20.1", "materials studio 20.1 x64 server"}
            for part in runner_path.parts
        ),
        "runner is not bound to a Materials Studio 20.1 installation",
    )
    _require_real_prerequisite(
        config.extra_runner_args == (),
        "MATERIAL_STUDIO_RUNNER_ARGS must be empty",
    )
    runner = MaterialStudioRunner(
        MaterialStudioConfig(
            runner=runner_path,
            workspace_root=tmp_path,
            default_timeout_seconds=config.default_timeout_seconds,
            install_home=config.install_home,
            runner_source=config.runner_source,
            extra_runner_args=config.extra_runner_args,
        )
    )

    raw_candidate_path = tmp_path / "staging" / "raw.cif"
    model = build_candidate(
        raw_candidate_path,
        project_id="sic_roundtrip_real_ms_20_1",
    )
    candidate_path = tmp_path / "candidate" / "structure.cif"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(
        _canonicalizer_compatible_cif(raw_candidate_path.read_bytes())
    )
    candidate_before = candidate_path.read_bytes()
    candidate_sha256 = hashlib.sha256(candidate_before).hexdigest()
    output_root = tmp_path / "runs"
    output_root.mkdir()

    binding = CandidateBinding(
        structure_path=candidate_path,
        expected_structure_sha256=candidate_sha256,
    )
    preview_request = RoundtripRequest(
        request_id="real-ms-20.1-roundtrip-request",
        run_id="real-ms-20.1-roundtrip-run",
        candidate=binding,
        output_root=output_root,
        execution_mode="preview",
        timeout_seconds=300,
    )
    gui = _InventoryOnlyWindowsBackend()
    adapter = MaterialsStudioRoundtripAdapter(
        runner=runner,
        gui_backend=gui,
        real_environment=True,
    )

    preview = adapter.run(preview_request)
    assert isinstance(preview, RoundtripPlan)
    assert preview.status == "preview_ready"
    assert preview.files_written is False
    assert preview.runner_called is False
    assert preview.gui_probed is False
    assert preview.gui_input_sent is False
    assert not preview.run_root.exists()
    assert gui.calls == []

    preflight = capture_gui_inventory(gui)
    _require_real_prerequisite(
        preflight.receipt.usable_single_window,
        "exactly one existing visible MatStudio.exe window is required",
    )
    _require_real_prerequisite(
        preflight.receipt.process_count == 1
        and preflight.receipt.window_count == 1,
        "MatStudio process/window inventory must be exactly 1/1",
    )

    execute_request = RoundtripRequest(
        request_id=preview_request.request_id,
        run_id=preview_request.run_id,
        candidate=binding,
        output_root=output_root,
        execution_mode="execute",
        timeout_seconds=preview_request.timeout_seconds,
    )
    result = adapter.run(execute_request)

    assert isinstance(result, RoundtripExecutionResult)
    assert result.status == "PASS"
    assert result.output_path is not None
    assert result.output_path.is_file()
    assert result.receipt_path.is_file()
    assert result.run_root.resolve().is_relative_to(output_root.resolve())
    assert candidate_path.read_bytes() == candidate_before

    receipt = result.receipt
    assert receipt.status == "PASS"
    assert receipt.real_environment is True
    assert receipt.real_materials_studio_status == "PASS"
    assert receipt.input_artifact.sha256 == candidate_sha256
    assert receipt.input_candidate_immutable is True
    assert receipt.candidate_validation.fixed_candidate_match is True
    assert receipt.candidate_validation.atom_count == 80
    assert receipt.candidate_validation.composition == ("C:32", "H:16", "Si:32")
    assert receipt.script_safety.deterministic is True
    assert receipt.script_safety.forbidden_operations_absent is True
    assert receipt.runner_identity.runner_identity == (
        "materials_studio_20.1_runmatscript.bat"
    )
    assert receipt.runner_identity.real_environment is True
    assert receipt.runner_identity.executable.sha256 == _sha256(runner_path)
    assert receipt.runner_execution.success is True
    assert receipt.runner_execution.timed_out is False
    assert receipt.runner_execution.all_artifacts_confined is True
    assert receipt.runner_executable_unchanged is True
    assert receipt.output_artifact is not None
    assert receipt.output_artifact.sha256 == _sha256(result.output_path)
    assert receipt.output_confined_and_fresh is True
    assert receipt.tagged_summary is not None
    assert receipt.tagged_summary.tagged_json_matches_input_output is True
    assert receipt.failure_codes == ()

    invariant = receipt.gui_invariant
    assert invariant.matstudio_process_count_before_after == (1, 1)
    assert invariant.matstudio_window_count_before_after == (1, 1)
    assert invariant.process_identity_unchanged is True
    assert invariant.window_identity_unchanged is True
    assert invariant.matstudio_pid_and_window_handle_unchanged is True
    assert invariant.matstudio_process_launched is False
    assert invariant.invariant_passed is True
    assert invariant.gui_input_activation_open_or_hotload_called is False
    assert receipt.gui_input_activation_open_or_hotload_called is False
    assert (
        invariant.before.process_identity_sha256
        == preflight.receipt.process_identity_sha256
    )
    assert (
        invariant.before.window_identity_sha256
        == preflight.receipt.window_identity_sha256
    )
    assert gui.calls == ["list_processes", "list_windows"] * 3

    comparison = receipt.comparison
    assert comparison is not None
    assert comparison.atom_count == 80
    assert comparison.composition == ("C:32", "H:16", "Si:32")
    assert comparison.mapping_coverage == 1.0
    assert comparison.rms_displacement_angstrom <= 0.05
    assert comparison.maximum_displacement_angstrom <= 0.15
    assert comparison.maximum_relative_lattice_error <= 0.001
    assert comparison.vacuum_absolute_error_angstrom <= 0.1
    assert comparison.passed is True
    assert receipt.calculation_evidence_status == "NOT_RUN"
    assert receipt.scientific_status == "NOT_RUN"

    assert result.receipt_artifact.sha256 == _sha256(result.receipt_path)
    assert result.receipt_artifact.byte_count == result.receipt_path.stat().st_size

    acceptance = _evaluate_completed_roundtrip(
        tmp_path / "benchmark",
        result=result,
        model=model,
        source_path=candidate_path,
        evaluation_run_id="sic-3c-roundtrip-real-ms-20.1",
    )
    assert acceptance.shared_evaluator_states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "PASS",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.overall_status == "PASS"
    assert acceptance.real_materials_studio == "PASS"
    assert acceptance.candidate_immutable is True
    assert acceptance.comparison == comparison
