from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from material_studio_mcp_server.ms_roundtrip import (
    MaterialsStudioRoundtripAdapter,
    RoundtripExecutionResult,
)


def _execute(request_factory, runner, gui) -> RoundtripExecutionResult:
    result = MaterialsStudioRoundtripAdapter(
        runner=runner,
        gui_backend=gui,
        real_environment=False,
    ).run(request_factory(execution_mode="execute"))
    assert isinstance(result, RoundtripExecutionResult)
    return result


def test_saved_script_must_match_deterministic_preview(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    fake_runner.tamper_script = True
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert "runner_artifact_invalid" in result.receipt.failure_codes


def test_tagged_json_must_bind_exact_input_and_output(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    original = fake_runner.run_script

    def wrong_summary(*args, **kwargs):
        result = original(*args, **kwargs)
        parsed = dict(result.parsed_json)
        parsed["output"] = str(Path(parsed["output"]).with_name("other.cif"))
        return replace(result, parsed_json=parsed)

    fake_runner.run_script = wrong_summary
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.receipt.tagged_summary is None
    assert "tagged_summary_invalid" in result.receipt.failure_codes


def test_runner_executable_mutation_is_detected(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    runner_path = fake_runner.config.runner
    fake_runner.after_run = lambda: runner_path.write_bytes(b"changed runner\n")
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.receipt.runner_executable_unchanged is False
    assert "runner_mutated" in result.receipt.failure_codes


def test_external_runner_artifact_is_rejected(
    request_factory,
    fake_runner,
    fake_gui,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.log"
    outside.write_text("outside", encoding="ascii")
    original = fake_runner.run_script

    def add_external(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, created_files=[*result.created_files, outside])

    fake_runner.run_script = add_external
    result = _execute(request_factory, fake_runner, fake_gui)
    assert result.status == "FAIL"
    assert result.receipt.runner_execution.all_artifacts_confined is False
    assert "runner_artifact_invalid" in result.receipt.failure_codes


def test_persisted_receipt_contains_no_absolute_paths_or_raw_gui_identity(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    result = _execute(request_factory, fake_runner, fake_gui)
    payload = result.receipt_path.read_text(encoding="ascii")
    decoded = json.loads(payload)
    assert str(result.run_root) not in payload
    assert str(request_factory().candidate.structure_path) not in payload
    assert "Current Project - Materials Studio" not in payload
    assert "4242" not in payload
    assert "8181" not in payload
    assert decoded["output_artifact"]["relative_path"] == "roundtrip_output.cif"
    assert all(
        not Path(item["relative_path"]).is_absolute()
        for item in decoded["runner_execution"]["artifacts"]
    )
