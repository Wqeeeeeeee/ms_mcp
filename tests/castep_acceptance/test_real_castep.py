from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from material_studio_mcp_server.ms_roundtrip.errors import RoundtripError
from material_studio_mcp_server.ms_roundtrip.secure_io import (
    reject_link_or_reparse_components,
    resolve_existing_directory,
)
from material_studio_mcp_server.castep_acceptance.profile import (
    WINDOWS_JOB_CWD_LIMIT,
    WINDOWS_JOB_PATH_LIMIT,
    repository_root,
    validate_windows_job_cwd,
    windows_job_path_lengths,
    windows_job_cwd_length,
)


def _require_real_prerequisite(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(f"real CASTEP prerequisite failed: {message}", pytrace=False)


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_real_plan_binding(
    *,
    request_id: object,
    timeout_seconds: object,
    expected_plan_sha256: object,
) -> tuple[str, int, str]:
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("a valid reviewed request ID is required")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 7 * 24 * 3600
    ):
        raise ValueError("a reviewed timeout from 1 to 604800 seconds is required")
    if not isinstance(expected_plan_sha256, str) or not _SHA256_RE.fullmatch(
        expected_plan_sha256
    ):
        raise ValueError("a lowercase reviewed plan SHA-256 is required")
    return request_id, timeout_seconds, expected_plan_sha256


def _run_reviewed_harness(
    *,
    harness: Any,
    request_id: str,
    workspace: Path,
    timeout_seconds: int,
    expected_plan_sha256: str,
) -> tuple[Any, Any]:
    from material_studio_mcp_server.castep_acceptance import (
        CastepAcceptanceRequest,
    )

    preview_request = CastepAcceptanceRequest(
        request_id=request_id,
        workspace_root=workspace,
        execution_mode="preview",
        timeout_seconds=timeout_seconds,
    )
    preview = harness.run(preview_request)
    assert preview.preview_files_runner_or_gui_touched is False
    assert preview.backend_resolution_deferred is True
    assert preview.profile.task == "Energy"
    assert not workspace.exists()
    _require_real_prerequisite(
        preview.plan_sha256 == expected_plan_sha256,
        "preview plan SHA-256 does not match the reviewed authorization",
    )
    result = harness.run(
        CastepAcceptanceRequest(
            request_id=preview_request.request_id,
            workspace_root=workspace,
            execution_mode="execute",
            expected_plan_sha256=expected_plan_sha256,
            real_opt_in="--run-real-castep",
            timeout_seconds=preview_request.timeout_seconds,
        )
    )
    return preview, result


def _default_real_destinations() -> tuple[Path, Path]:
    """Allocate fixed external destinations for an explicitly bound real run."""

    try:
        temp_root = resolve_existing_directory(Path(tempfile.gettempdir()).expanduser())
        temp_root.relative_to(repository_root().resolve())
    except ValueError:
        pass
    except (OSError, RoundtripError) as exc:
        raise ValueError(
            "the default real CASTEP parent is not a safe regular directory"
        ) from exc
    else:
        raise ValueError("the default real CASTEP parent must be outside the repository")
    # Keep the fixed nested Materials Studio job cwd below Windows' legacy
    # CreateProcess limit even when the user profile path is long.
    root = temp_root / "msca"
    validate_windows_job_cwd(root / "workspace-001")
    try:
        reject_link_or_reparse_components(root)
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        root = resolve_existing_directory(root)
    except FileExistsError as exc:
        raise ValueError(
            "the default real CASTEP destination already exists; use a reviewed "
            "fresh external destination"
        ) from exc
    except (OSError, RoundtripError) as exc:
        raise ValueError(
            "the default real CASTEP destination is not a safe regular directory"
        ) from exc
    return root / "workspace-001", root / "real-castep-evidence-001.json"


def test_default_destinations_satisfy_literal_work_order_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        sys.modules[__name__],
        "validate_windows_job_cwd",
        lambda _workspace: None,
    )
    workspace, evidence = _default_real_destinations()
    assert workspace == tmp_path / "msca" / "workspace-001"
    assert evidence == (
        tmp_path
        / "msca"
        / "real-castep-evidence-001.json"
    )
    assert workspace.parent.is_dir()
    assert not workspace.exists()
    assert not evidence.exists()
    with pytest.raises(ValueError, match="already exists"):
        _default_real_destinations()


def test_default_destinations_reject_repository_temp_before_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(repository_root()))
    with pytest.raises(ValueError, match="outside the repository"):
        _default_real_destinations()
    assert not (repository_root() / "msca").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows path budget")
def test_default_destination_rejects_path_budget_before_root_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    long_temp = tmp_path / ("temp-" + ("x" * 120))
    long_temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(long_temp))
    with pytest.raises(ValueError, match="Windows .* path"):
        _default_real_destinations()
    assert not (long_temp / "msca").exists()


def test_short_default_shape_keeps_windows_job_cwd_below_legacy_limit() -> None:
    workspace = Path("C:/msca/workspace-001")
    assert windows_job_cwd_length(workspace) < WINDOWS_JOB_CWD_LIMIT
    assert max(windows_job_path_lengths(workspace).values()) < WINDOWS_JOB_PATH_LIMIT


def test_real_plan_binding_requires_exact_reviewed_values() -> None:
    assert _validate_real_plan_binding(
        request_id="castep-acceptance-wo002-w001",
        timeout_seconds=1800,
        expected_plan_sha256="a" * 64,
    ) == ("castep-acceptance-wo002-w001", 1800, "a" * 64)


@pytest.mark.parametrize(
    ("request_id", "timeout_seconds", "expected_plan_sha256", "message"),
    [
        (None, 1800, "a" * 64, "request ID"),
        ("invalid request", 1800, "a" * 64, "request ID"),
        ("valid-request", None, "a" * 64, "timeout"),
        ("valid-request", 0, "a" * 64, "timeout"),
        ("valid-request", 1800, None, "plan SHA-256"),
        ("valid-request", 1800, "A" * 64, "plan SHA-256"),
    ],
)
def test_real_plan_binding_rejects_missing_or_unreviewed_values(
    request_id: object,
    timeout_seconds: object,
    expected_plan_sha256: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_real_plan_binding(
            request_id=request_id,
            timeout_seconds=timeout_seconds,
            expected_plan_sha256=expected_plan_sha256,
        )


class _FakePytestConfig:
    def __init__(self, **options: object) -> None:
        self._options = options

    def getoption(self, name: str) -> object:
        return self._options.get(name)


def test_real_cli_missing_binding_stops_before_destination_or_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.castep_acceptance as acceptance

    destination_calls = 0
    harness_constructions = 0

    def unexpected_destination() -> tuple[Path, Path]:
        nonlocal destination_calls
        destination_calls += 1
        raise AssertionError("destination allocation must not run")

    class UnexpectedHarness:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal harness_constructions
            harness_constructions += 1
            raise AssertionError("harness construction must not run")

    monkeypatch.delenv("MATERIAL_STUDIO_COMMAND_TEMPLATE", raising=False)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_default_real_destinations",
        unexpected_destination,
    )
    monkeypatch.setattr(acceptance, "CastepAcceptanceHarness", UnexpectedHarness)
    config = _FakePytestConfig(**{"--run-real-castep": True})

    with pytest.raises(pytest.fail.Exception, match="reviewed request ID"):
        test_real_castep_energy_acceptance_once(config)  # type: ignore[arg-type]

    assert destination_calls == 0
    assert harness_constructions == 0


def test_real_cli_plan_mismatch_stops_after_preview_before_execute(
    tmp_path: Path,
) -> None:
    harness_requests: list[Any] = []

    class PreviewOnlyHarness:
        def __init__(self, *, real_environment: bool) -> None:
            assert real_environment is True

        def run(self, request: Any) -> SimpleNamespace:
            harness_requests.append(request)
            assert request.execution_mode == "preview"
            return SimpleNamespace(
                preview_files_runner_or_gui_touched=False,
                backend_resolution_deferred=True,
                profile=SimpleNamespace(task="Energy"),
                plan_sha256="b" * 64,
            )

    harness = PreviewOnlyHarness(real_environment=True)
    workspace = tmp_path / "fresh-workspace"
    with pytest.raises(pytest.fail.Exception, match="does not match"):
        _run_reviewed_harness(
            harness=harness,
            request_id="reviewed-request",
            workspace=workspace,
            timeout_seconds=1800,
            expected_plan_sha256="a" * 64,
        )

    assert len(harness_requests) == 1
    assert harness_requests[0].execution_mode == "preview"
    assert not workspace.exists()


def test_real_harness_execute_request_preserves_reviewed_binding(
    tmp_path: Path,
) -> None:
    harness_requests: list[Any] = []
    expected_plan_sha256 = "a" * 64
    execution_result = object()

    class BoundHarness:
        def run(self, request: Any) -> Any:
            harness_requests.append(request)
            if request.execution_mode == "preview":
                return SimpleNamespace(
                    preview_files_runner_or_gui_touched=False,
                    backend_resolution_deferred=True,
                    profile=SimpleNamespace(task="Energy"),
                    plan_sha256=expected_plan_sha256,
                )
            return execution_result

    workspace = tmp_path / "fresh-workspace"
    _preview, result = _run_reviewed_harness(
        harness=BoundHarness(),
        request_id="reviewed-request",
        workspace=workspace,
        timeout_seconds=1800,
        expected_plan_sha256=expected_plan_sha256,
    )

    assert result is execution_result
    assert len(harness_requests) == 2
    preview_request, execute_request = harness_requests
    assert preview_request.execution_mode == "preview"
    assert preview_request.request_id == "reviewed-request"
    assert preview_request.timeout_seconds == 1800
    assert preview_request.expected_plan_sha256 is None
    assert preview_request.real_opt_in is None
    assert execute_request.execution_mode == "execute"
    assert execute_request.request_id == preview_request.request_id
    assert execute_request.timeout_seconds == preview_request.timeout_seconds
    assert execute_request.expected_plan_sha256 == expected_plan_sha256
    assert execute_request.real_opt_in == "--run-real-castep"
    assert not workspace.exists()


def test_real_castep_energy_acceptance_once(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("--run-real-castep"):
        pytest.skip("requires literal --run-real-castep opt-in")

    from material_studio_mcp_server.benchmark_evaluation import (
        assert_coordinate_free_payload,
    )
    from material_studio_mcp_server.castep_acceptance import (
        CastepAcceptanceExecutionResult,
        CastepAcceptanceHarness,
        project_real_evidence,
        validate_external_evidence_path,
        write_external_evidence,
    )
    from material_studio_mcp_server.castep_acceptance.profile import (
        validate_external_fresh_workspace,
    )

    try:
        request_id, timeout_seconds, expected_plan_sha256 = (
            _validate_real_plan_binding(
                request_id=pytestconfig.getoption("--real-castep-request-id"),
                timeout_seconds=pytestconfig.getoption(
                    "--real-castep-timeout-seconds"
                ),
                expected_plan_sha256=pytestconfig.getoption(
                    "--real-castep-expected-plan-sha256"
                ),
            )
        )
    except ValueError as exc:
        pytest.fail(f"real CASTEP prerequisite failed: {exc}", pytrace=False)
    _require_real_prerequisite(os.name == "nt", "Windows is required")
    _require_real_prerequisite(
        not bool(os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE")),
        "MATERIAL_STUDIO_COMMAND_TEMPLATE must be unset",
    )
    workspace_value = pytestconfig.getoption("--real-castep-workspace")
    evidence_value = pytestconfig.getoption("--real-castep-evidence-output")
    _require_real_prerequisite(
        bool(workspace_value) == bool(evidence_value),
        "workspace and evidence destinations must be supplied together",
    )
    if workspace_value and evidence_value:
        workspace = Path(workspace_value).expanduser()
        evidence_output = Path(evidence_value).expanduser()
    else:
        try:
            workspace, evidence_output = _default_real_destinations()
        except ValueError as exc:
            pytest.fail(f"real CASTEP prerequisite failed: {exc}", pytrace=False)
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
    _preview, result = _run_reviewed_harness(
        harness=harness,
        request_id=request_id,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        expected_plan_sha256=expected_plan_sha256,
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
