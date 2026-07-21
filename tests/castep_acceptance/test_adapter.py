from __future__ import annotations

import pytest

from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceError,
    CastepAcceptanceHarness,
)

from ._helpers import run_fake_acceptance


def test_offline_fake_public_tool_path_executes_once_and_cannot_claim_real(
    monkeypatch,
    tmp_path,
) -> None:
    result, fake_runner, gui = run_fake_acceptance(monkeypatch, tmp_path)

    assert fake_runner.call_count == 1
    assert result.verification.status == "NOT_RUN"
    assert result.verification.failure_codes == ()
    assert result.verification.real_environment is False
    assert result.verification.execute_invocation_count == 1
    assert result.verification.backend_execution_count == 1
    assert result.verification.runner_success is True
    assert result.verification.effective_settings_exact is True
    assert result.verification.structure_unchanged is True
    assert result.verification.metadata_only_result_revision_verified is True
    assert result.public_execute["new_revision"] == 1
    assert result.public_execute["scientific_convergence_verified"] is False
    assert result.public_execute["scientific_band_gap_verified"] is False
    assert "gui_status" not in result.public_execute
    assert result.workspace_root.is_dir()
    assert gui.calls == [
        ("list_processes", None),
        ("list_windows", 4101),
        ("list_processes", None),
        ("list_windows", 4101),
    ]


def test_execute_rejects_unreviewed_plan_before_resolving_backends(
    request_factory,
) -> None:
    calls: list[str] = []

    def forbidden():
        calls.append("resolved")
        raise AssertionError("stale plan must stop before backend resolution")

    request = request_factory(
        execution_mode="execute",
        expected_plan_sha256="0" * 64,
        real_opt_in="--run-real-castep",
    )
    with pytest.raises(CastepAcceptanceError, match="reviewed preview plan"):
        CastepAcceptanceHarness(
            tool_resolver=forbidden,
            gui_backend_resolver=forbidden,
            real_environment=True,
        ).run(request)
    assert calls == []
    assert not request.workspace_root.exists()
