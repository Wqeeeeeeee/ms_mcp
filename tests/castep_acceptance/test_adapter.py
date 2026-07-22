from __future__ import annotations

import pytest

import material_studio_mcp_server.castep_acceptance.adapter as adapter_module

from material_studio_mcp_server.config import MaterialStudioConfig
from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceError,
    CastepAcceptanceHarness,
)
from material_studio_mcp_server.runner import MaterialStudioRunner

from ._helpers import FakeGuiBackend, run_fake_acceptance


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


def test_real_execute_rejects_callable_that_only_spoofs_public_metadata(
    request_factory,
) -> None:
    def spoofed_tool(**kwargs):
        raise AssertionError("spoofed tool must not be called")

    spoofed_tool.__name__ = "material_studio_castep_run_current"
    spoofed_tool.__module__ = "material_studio_mcp_server.server"
    harness = CastepAcceptanceHarness(
        tool_resolver=lambda: spoofed_tool,
        gui_backend_resolver=FakeGuiBackend,
        real_environment=True,
    )
    preview_request = request_factory()
    preview = harness.run(preview_request)
    request = request_factory(
        execution_mode="execute",
        expected_plan_sha256=preview.plan_sha256,
        real_opt_in="--run-real-castep",
    )
    with pytest.raises(CastepAcceptanceError, match="requires material_studio_castep_run_current"):
        harness.run(request)
    assert not request.workspace_root.exists()


def test_real_runner_snapshot_binds_trusted_path_and_file_identity(
    monkeypatch,
    tmp_path,
) -> None:
    from material_studio_mcp_server import server

    install_root = tmp_path / "BIOVIA"
    runner_path = (
        install_root
        / "Materials Studio 20.1 x64 Server"
        / "etc"
        / "Scripting"
        / "bin"
        / "RunMatScript.bat"
    )
    runner_path.parent.mkdir(parents=True)
    runner_path.write_bytes(b"@echo off\r\nrem fixed runner\r\n")
    config = MaterialStudioConfig(
        runner=runner_path,
        workspace_root=tmp_path / "unused-workspace",
        default_timeout_seconds=30,
        install_home=runner_path.parents[3],
        runner_source="offline-test",
        extra_runner_args=(),
    )
    monkeypatch.setattr(adapter_module, "COMMON_INSTALL_ROOTS", (str(install_root),))
    monkeypatch.setattr(server, "runner", MaterialStudioRunner(config))
    tool = server.material_studio_castep_run_current
    before = adapter_module._real_runner_snapshot(tool)
    assert before is not None
    assert adapter_module._real_runner_unchanged(tool, before) is True
    runner_path.write_bytes(b"@echo off\r\nrem changed runner\r\n")
    assert adapter_module._real_runner_unchanged(tool, before) is False
