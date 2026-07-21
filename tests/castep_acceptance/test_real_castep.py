from __future__ import annotations

import os
from pathlib import Path

import pytest


def _require_real_prerequisite(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(f"real CASTEP prerequisite failed: {message}", pytrace=False)


def test_real_castep_energy_acceptance_once(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("--run-real-castep"):
        pytest.skip("requires literal --run-real-castep opt-in")

    from material_studio_mcp_server.benchmark_evaluation import (
        assert_coordinate_free_payload,
    )
    from material_studio_mcp_server.castep_acceptance import (
        CastepAcceptanceExecutionResult,
        CastepAcceptanceHarness,
        CastepAcceptanceRequest,
        project_real_evidence,
        validate_external_evidence_path,
        write_external_evidence,
    )
    from material_studio_mcp_server.castep_acceptance.profile import (
        validate_external_fresh_workspace,
    )

    _require_real_prerequisite(os.name == "nt", "Windows is required")
    _require_real_prerequisite(
        not bool(os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE")),
        "MATERIAL_STUDIO_COMMAND_TEMPLATE must be unset",
    )
    workspace_value = pytestconfig.getoption("--real-castep-workspace")
    evidence_value = pytestconfig.getoption("--real-castep-evidence-output")
    _require_real_prerequisite(
        bool(workspace_value),
        "--real-castep-workspace must name one absent external directory",
    )
    _require_real_prerequisite(
        bool(evidence_value),
        "--real-castep-evidence-output must name one absent external file",
    )
    workspace = Path(workspace_value).expanduser()
    evidence_output = Path(evidence_value).expanduser()
    try:
        workspace = validate_external_fresh_workspace(workspace)
    except (OSError, ValueError) as exc:
        pytest.fail(f"real CASTEP prerequisite failed: {exc}", pytrace=False)
    _require_real_prerequisite(
        len(str(workspace)) <= 120,
        "workspace path must be at most 120 characters for MS 20.1",
    )
    _require_real_prerequisite(
        evidence_output.parent.is_dir(),
        "evidence output parent must already exist",
    )
    _require_real_prerequisite(
        not evidence_output.exists(),
        "evidence output must not already exist",
    )
    try:
        evidence_output = validate_external_evidence_path(evidence_output)
    except (OSError, ValueError) as exc:
        pytest.fail(f"real CASTEP prerequisite failed: {exc}", pytrace=False)

    harness = CastepAcceptanceHarness(real_environment=True)
    preview_request = CastepAcceptanceRequest(
        request_id="sic-3c-castep-energy-real",
        workspace_root=workspace,
        execution_mode="preview",
    )
    preview = harness.run(preview_request)
    assert preview.preview_files_runner_or_gui_touched is False
    assert preview.backend_resolution_deferred is True
    assert preview.profile.task == "Energy"
    assert not workspace.exists()

    result = harness.run(
        CastepAcceptanceRequest(
            request_id=preview_request.request_id,
            workspace_root=workspace,
            execution_mode="execute",
            expected_plan_sha256=preview.plan_sha256,
            real_opt_in="--run-real-castep",
            timeout_seconds=preview_request.timeout_seconds,
        )
    )
    assert isinstance(result, CastepAcceptanceExecutionResult)
    report = result.verification
    assert report.status == "PASS"
    assert report.real_environment is True
    assert report.failure_codes == ()
    assert report.runner_identity_valid is True
    assert report.runner_success is True
    assert report.execute_invocation_count == 1
    assert report.backend_execution_count == 1
    assert report.revision_execution_lock_verified is True
    assert report.execution_attempt_event_types == ("started", "completed")
    assert report.execution_attempt_binding_verified is True
    assert report.electronic_receipt_binding_verified is True
    assert report.native_castep_file_count == 1
    assert report.native_scf_audit_valid is True
    assert report.total_energy_finite is True
    assert report.structure_unchanged is True
    assert report.metadata_only_result_revision_verified is True
    assert report.scientific_convergence_verified is False
    assert report.scientifically_verified is False
    assert report.gui.process_count_before_after == (1, 1)
    assert report.gui.window_count_before_after == (1, 1)
    assert report.gui.identity_unchanged is True
    assert report.gui.process_launched is False
    assert report.gui.gui_input_activation_open_or_hotload_count == 0
    assert not tuple(workspace.rglob("*.bands"))

    from .test_benchmark import _evaluate_completed_castep

    acceptance = _evaluate_completed_castep(
        workspace / "benchmark_acceptance",
        result=result,
        evaluation_run_id="sic-3c-castep-energy-real",
    )
    assert acceptance.states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "PASS",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.overall_status == "PASS"
    assert acceptance.real_castep == "PASS"
    evidence = project_real_evidence(
        verification=report,
        benchmark_acceptance=acceptance,
    )
    assert_coordinate_free_payload(evidence.model_dump(mode="json"))
    write_external_evidence(evidence_output, evidence)
