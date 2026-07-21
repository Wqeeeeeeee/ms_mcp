from __future__ import annotations

import hashlib
import json
from pathlib import Path

from material_studio_mcp_server.ms_roundtrip import (
    MaterialsStudioRoundtripAdapter,
    RoundtripExecutionResult,
)

from ._helpers import FakeRunner


def _execute(request_factory, fake_runner, fake_gui) -> RoundtripExecutionResult:
    result = MaterialsStudioRoundtripAdapter(
        runner=fake_runner,
        gui_backend=fake_gui,
        real_environment=False,
    ).run(request_factory(execution_mode="execute"))
    assert isinstance(result, RoundtripExecutionResult)
    return result


def test_fake_execute_publishes_bound_pass_receipt(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    request = request_factory(execution_mode="execute")
    input_before = request.candidate.structure_path.read_bytes()

    result = _execute(request_factory, fake_runner, fake_gui)

    assert result.status == "PASS"
    assert result.receipt.status == "PASS"
    assert result.receipt.real_environment is False
    assert result.receipt.real_materials_studio_status == "NOT_RUN"
    assert result.receipt.calculation_evidence_status == "NOT_RUN"
    assert result.receipt.scientific_status == "NOT_RUN"
    assert result.output_path is not None
    assert result.output_path.read_bytes() == input_before
    assert request.candidate.structure_path.read_bytes() == input_before
    assert result.receipt_path.is_file()
    assert fake_runner.run_calls == 1
    assert fake_gui.list_process_calls == 2
    assert fake_gui.list_window_calls == 2
    assert fake_gui.prohibited_calls == []


def test_fake_execute_receipt_bytes_match_returned_digest(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    result = _execute(request_factory, fake_runner, fake_gui)
    payload = result.receipt_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == result.receipt_artifact.sha256
    assert len(payload) == result.receipt_artifact.byte_count
    assert json.loads(payload) == result.receipt.model_dump(mode="json")


def test_runner_failure_is_persisted_without_false_success(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    fake_runner.success = False
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.receipt.runner_execution.success is False
    assert "runner_failed" in result.receipt.failure_codes
    assert result.receipt_path.is_file()

def test_input_mutation_is_detected_and_never_accepted(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    fake_runner.mutate_input = True
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.receipt.input_candidate_immutable is False
    assert "input_mutated" in result.receipt.failure_codes
    assert result.receipt.comparison is None


def test_new_window_after_runner_is_a_hard_failure(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    fake_runner.after_run = fake_gui.add_second_window
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    invariant = result.receipt.gui_invariant
    assert invariant.matstudio_process_count_before_after == (1, 1)
    assert invariant.matstudio_window_count_before_after == (1, 2)
    assert invariant.invariant_passed is False
    assert "gui_invariant_failed" in result.receipt.failure_codes
    assert fake_gui.prohibited_calls == []


def test_runner_exception_still_publishes_failure_receipt(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    def fail_run(*args, **kwargs):
        fake_runner.run_calls += 1
        raise RuntimeError("offline runner failure")

    fake_runner.run_script = fail_run
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.output_path is None
    assert result.receipt.runner_execution.success is False
    assert "runner_failed" in result.receipt.failure_codes
    assert "output_missing" in result.receipt.failure_codes
    assert result.receipt_path.is_file()
