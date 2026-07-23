from __future__ import annotations

from pathlib import Path

from ms_mcp.config import Settings


def test_settings_defaults_to_perl(monkeypatch):
    monkeypatch.delenv("MS_WORKSPACE", raising=False)
    monkeypatch.delenv("MS_SCRIPT_MODE", raising=False)
    monkeypatch.delenv("MS_SCRIPT_RUNNER", raising=False)
    monkeypatch.delenv("MS_VERSION", raising=False)

    settings = Settings.from_env()

    assert settings.script_mode == "perl"
    assert Path(settings.workspace).name == "workspace"


def test_settings_env_overrides(monkeypatch, tmp_path):
    runner = tmp_path / "RunMatScript.bat"
    monkeypatch.setenv("MS_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("MS_SCRIPT_MODE", "python")
    monkeypatch.setenv("MS_SCRIPT_RUNNER", str(runner))
    monkeypatch.setenv("MS_VERSION", "2026")

    settings = Settings.from_env()

    assert settings.workspace == str(tmp_path / "workspace")
    assert settings.script_mode == "python"
    assert settings.script_runner == str(runner)
    assert settings.version == "2026"
