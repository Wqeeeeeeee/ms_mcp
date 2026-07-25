from pathlib import Path
from types import SimpleNamespace

import pytest

from material_studio_mcp_server.config import MaterialStudioConfig
from material_studio_mcp_server import runner as runner_module
from material_studio_mcp_server.runner import (
    MaterialStudioError,
    MaterialStudioRunner,
    _materials_run_succeeded,
)


def test_build_default_command_for_runmatscript_uses_script_stem(tmp_path: Path) -> None:
    runner_path = tmp_path / "Program Files" / "BIOVIA" / "RunMatScript.bat"
    script_path = tmp_path / "jobs" / "script.pl"
    config = MaterialStudioConfig(
        runner=runner_path,
        workspace_root=tmp_path,
        default_timeout_seconds=10,
        install_home=None,
        runner_source="test",
        extra_runner_args=("--foo", "bar baz"),
    )

    command = MaterialStudioRunner(config)._build_command(runner_path, script_path, ["--x", "1 2"])

    assert command == [str(runner_path), "--foo", "bar baz", "script", "--", "--x", "1 2"]


def test_build_default_command_for_other_runner_uses_script_path(tmp_path: Path) -> None:
    runner_path = tmp_path / "RunMatserver.bat"
    script_path = tmp_path / "jobs" / "script.pl"
    config = MaterialStudioConfig(
        runner=runner_path,
        workspace_root=tmp_path,
        default_timeout_seconds=10,
        install_home=None,
        runner_source="test",
        extra_runner_args=(),
    )

    command = MaterialStudioRunner(config)._build_command(runner_path, script_path, [])

    assert command == [str(runner_path), str(script_path)]


def test_materials_log_failure_detection() -> None:
    assert not _materials_run_succeeded(0, "", "Completion status: (FAIL).")
    assert _materials_run_succeeded(0, "ok", "Completion status: (OK).")
    assert not _materials_run_succeeded(
        0,
        "Completion status: (OK).",
        "",
        require_success_markers=True,
    )
    assert _materials_run_succeeded(
        0,
        "Completion status: (OK).",
        "Exiting MatServer: status OK.",
        require_success_markers=True,
    )


def test_direct_job_dir_uses_exact_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner_path = tmp_path / "BIOVIA" / "RunMatScript.bat"
    runner_path.parent.mkdir()
    runner_path.write_text("fake", encoding="utf-8")
    job_dir = tmp_path / "short-job"
    job_dir.mkdir()
    config = MaterialStudioConfig(
        runner=runner_path,
        workspace_root=tmp_path,
        default_timeout_seconds=10,
        install_home=None,
        runner_source="test",
        extra_runner_args=(),
    )
    runner = MaterialStudioRunner(config)
    monkeypatch.delenv("MATERIAL_STUDIO_COMMAND_TEMPLATE", raising=False)

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == str(job_dir.resolve())
        (job_dir / "roundtrip.pl.out").write_text(
            "Completion status: (OK).\n",
            encoding="utf-8",
        )
        (job_dir / "roundtripMatStudioLog.htm").write_text(
            "Exiting MatServer: status OK.\n",
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    result = runner.run_script(
        "print 'ok';\n",
        working_dir=job_dir,
        keep_script_name="roundtrip.pl",
        direct_job_dir=True,
    )

    assert result.job_dir == job_dir.resolve()
    assert result.success is True
    assert result.success_markers_required is True
    assert all(result.completion_markers.values())
    assert not (job_dir / ".material-studio-mcp").exists()


def test_direct_job_dir_rejects_nonempty_directory(tmp_path: Path) -> None:
    runner_path = tmp_path / "RunMatScript.bat"
    runner_path.write_text("fake", encoding="utf-8")
    job_dir = tmp_path / "occupied"
    job_dir.mkdir()
    (job_dir / "existing.txt").write_text("occupied", encoding="utf-8")
    config = MaterialStudioConfig(
        runner=runner_path,
        workspace_root=tmp_path,
        default_timeout_seconds=10,
        install_home=None,
        runner_source="test",
        extra_runner_args=(),
    )

    with pytest.raises(MaterialStudioError, match="must be empty"):
        MaterialStudioRunner(config).run_script(
            "print 'blocked';\n",
            working_dir=job_dir,
            direct_job_dir=True,
        )
