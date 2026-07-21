from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from material_studio_mcp_server.ms_roundtrip import (
    MaterialsStudioRoundtripAdapter,
    RoundtripError,
    RoundtripErrorCode,
    RoundtripExecutionResult,
)
from material_studio_mcp_server.ms_roundtrip import secure_io as secure_io_module


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


def test_concurrent_receipt_publication_is_atomic_and_no_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result_receipt.json"
    worker_count = 8
    payloads = [
        {"writer": index, "body": "payload-" + str(index) * 256}
        for index in range(worker_count)
    ]
    publish_barrier = threading.Barrier(worker_count)
    observed_sources: list[bytes] = []
    observed_lock = threading.Lock()
    real_link = secure_io_module.os.link

    def synchronized_link(source, target, *args, **kwargs):
        with observed_lock:
            observed_sources.append(Path(source).read_bytes())
        publish_barrier.wait(timeout=10)
        return real_link(source, target, *args, **kwargs)

    def forbidden_replace(*args, **kwargs):
        raise AssertionError("Receipt publication must not use replacing rename.")

    monkeypatch.setattr(secure_io_module.os, "link", synchronized_link)
    monkeypatch.setattr(secure_io_module.os, "replace", forbidden_replace)

    def publish(index: int):
        try:
            snapshot = secure_io_module.atomic_write_json(
                destination,
                payloads[index],
            )
        except RoundtripError as exc:
            return index, None, exc
        return index, snapshot, None

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(publish, range(worker_count)))

    successes = [result for result in results if result[1] is not None]
    failures = [result for result in results if result[2] is not None]
    assert len(observed_sources) == worker_count
    assert len(successes) == 1
    assert len(failures) == worker_count - 1
    assert all(
        result[2].code is RoundtripErrorCode.RECEIPT_PERSISTENCE_FAILED
        for result in failures
    )
    assert all(
        str(result[2]) == "The result receipt already exists."
        for result in failures
    )

    winner_index, winner_snapshot, _ = successes[0]
    expected = secure_io_module.canonical_json_bytes(
        payloads[winner_index],
        trailing_newline=True,
    )
    assert set(observed_sources) == {
        secure_io_module.canonical_json_bytes(payload, trailing_newline=True)
        for payload in payloads
    }
    assert destination.read_bytes() == expected
    assert winner_snapshot.payload == expected
    assert destination.stat().st_nlink == 1
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))
