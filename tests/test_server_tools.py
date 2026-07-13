from __future__ import annotations

from pathlib import Path

from ms_mcp.schemas import CrystalSpec, RunSettings
from ms_mcp.server import (
    build_crystal,
    diagnose_failure,
    probe_environment,
    request_gui_check,
    run_geometry_optimization,
)


def test_server_tool_flow_without_real_runner(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("MS_WORKSPACE", str(workspace))
    monkeypatch.setenv("MS_SCRIPT_MODE", "perl")
    monkeypatch.setenv("MS_SCRIPT_RUNNER", str(tmp_path / "missing" / "RunMatScript.bat"))
    monkeypatch.setenv("MS_VERSION", "2020")

    probe = probe_environment()
    assert probe["workspace"] == str(workspace)
    assert probe["workspace_writable"] is True
    assert probe["runner_exists"] is False

    crystal = build_crystal(
        CrystalSpec(
            name="NaCl",
            lattice={"a": 5.64, "b": 5.64, "c": 5.64, "alpha": 90, "beta": 90, "gamma": 90},
            atoms=[
                {"element": "Na", "x": 0.0, "y": 0.0, "z": 0.0},
                {"element": "Cl", "x": 0.5, "y": 0.5, "z": 0.5},
            ],
        )
    )
    assert Path(crystal["path"]).exists()
    assert crystal["validation"]["ok"] is True

    opt = run_geometry_optimization(
        crystal["path"],
        RunSettings(engine="Forcite", task="GeometryOptimization"),
    )
    assert opt["returncode"] == 127
    assert Path(opt["report_path"]).exists()

    diagnosis = diagnose_failure(opt["job_dir"])
    assert diagnosis["diagnosis"]["category"] == "script_api_error"


def test_request_gui_check_is_prompt_only():
    result = request_gui_check("C:\\work\\input.xsd")

    assert result["target_app"] == "BIOVIA Materials Studio"
    assert "Do not change scientific parameters" in result["computer_use_prompt"]
