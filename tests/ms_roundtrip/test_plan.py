from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from material_studio_mcp_server.gui import WindowsGuiBackend
from material_studio_mcp_server.ms_roundtrip import (
    MaterialsStudioRoundtripAdapter,
    RoundtripError,
    RoundtripErrorCode,
    RoundtripPlan,
    plan_digest,
    plan_roundtrip,
)
from material_studio_mcp_server.runner import MaterialStudioRunner
from material_studio_mcp_server.scripts import import_export_script


def _tree(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_preview_is_deterministic_and_completely_side_effect_free(
    tmp_path: Path,
    request_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = request_factory()
    before = _tree(tmp_path)
    monkeypatch.setattr(
        MaterialStudioRunner,
        "run_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("preview called runner")
        ),
    )
    monkeypatch.setattr(
        WindowsGuiBackend,
        "list_processes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("preview probed GUI")
        ),
    )

    first = MaterialsStudioRoundtripAdapter().run(request)
    second = plan_roundtrip(request)

    assert isinstance(first, RoundtripPlan)
    assert first == second
    assert plan_digest(first) == plan_digest(second)
    assert _tree(tmp_path) == before
    assert not first.run_root.exists()
    assert first.files_written is False
    assert first.runner_called is False
    assert first.gui_probed is False
    assert first.gui_input_sent is False


def test_preview_uses_exact_reviewed_import_export_script(request_factory) -> None:
    plan = plan_roundtrip(request_factory())
    assert plan.script_text == import_export_script(
        request_factory().candidate.structure_path.resolve(),
        plan.output_path,
    )
    assert plan.script_safety.deterministic is True
    assert plan.script_safety.exact_reviewed_template is True
    assert plan.candidate_validation.atom_count == 80
    assert plan.candidate_validation.composition == ("C:32", "H:16", "Si:32")
    assert plan.candidate_validation.fixed_candidate_match is True


def test_execute_request_can_be_planned_without_execution_or_writes(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    request = request_factory(execution_mode="execute")
    plan = plan_roundtrip(request)
    assert plan.execution_mode == "execute"
    assert fake_runner.run_calls == 0
    assert fake_gui.list_process_calls == 0
    assert not plan.run_root.exists()


def test_digest_mismatch_fails_before_creating_run_root(request_factory) -> None:
    request = request_factory(expected_sha256="f" * 64)
    with pytest.raises(RoundtripError) as captured:
        plan_roundtrip(request)
    assert captured.value.code is RoundtripErrorCode.INPUT_IDENTITY_MISMATCH
    assert not (request.output_root / request.run_id).exists()


def test_preexisting_run_root_is_rejected_without_overwrite(request_factory) -> None:
    request = request_factory()
    run_root = request.output_root / request.run_id
    run_root.mkdir()
    sentinel = run_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="ascii")
    with pytest.raises(RoundtripError) as captured:
        plan_roundtrip(request)
    assert captured.value.code is RoundtripErrorCode.OUTPUT_ALREADY_EXISTS
    assert sentinel.read_text(encoding="ascii") == "keep"


def test_modified_candidate_is_not_accepted_as_fixed_profile(
    candidate_path: Path,
    request_factory,
) -> None:
    payload = candidate_path.read_text(encoding="utf-8")
    candidate_path.write_text(payload.replace("8.7192", "8.8", 1), encoding="utf-8")
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    with pytest.raises(RoundtripError) as captured:
        plan_roundtrip(request_factory(expected_sha256=digest))
    assert captured.value.code is RoundtripErrorCode.UNSUPPORTED_CANDIDATE


def test_symlink_output_root_is_rejected_when_supported(
    tmp_path: Path,
    request_factory,
) -> None:
    target = tmp_path / "real-output"
    target.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    request = request_factory(selected_output_root=linked)
    with pytest.raises(RoundtripError) as captured:
        plan_roundtrip(request)
    assert captured.value.code is RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED
    assert not (target / request.run_id).exists()


def test_run_id_contract_cannot_encode_path_traversal(
    request_factory,
) -> None:
    with pytest.raises(Exception):
        request_factory(run_id="../escape")
    assert os.path.sep not in "roundtrip-test-001"
