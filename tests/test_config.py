from pathlib import Path

from material_studio_mcp_server.config import resolve_config


def test_resolve_runner_from_env(monkeypatch, tmp_path: Path) -> None:
    runner = tmp_path / "RunMatserver.bat"
    runner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("MATERIAL_STUDIO_RUNNER", str(runner))
    monkeypatch.setenv("MATERIAL_STUDIO_WORKSPACE", str(tmp_path / "jobs"))

    config = resolve_config(cwd=tmp_path)

    assert config.runner == runner.resolve()
    assert config.runner_source == "MATERIAL_STUDIO_RUNNER"
    assert config.workspace_root == (tmp_path / "jobs").resolve()


def test_structured_workspace_is_runner_workspace_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MATERIAL_STUDIO_WORKSPACE", raising=False)
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_WORKSPACE", str(tmp_path / "structured"))

    config = resolve_config(cwd=tmp_path)

    assert config.workspace_root == (tmp_path / "structured").resolve()


def test_runner_workspace_override_keeps_precedence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_WORKSPACE", str(tmp_path / "structured"))
    monkeypatch.setenv("MATERIAL_STUDIO_WORKSPACE", str(tmp_path / "runner"))

    config = resolve_config(cwd=tmp_path)

    assert config.workspace_root == (tmp_path / "runner").resolve()


def test_gui_loop_config_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT", raising=False)
    monkeypatch.delenv("MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS", raising=False)

    config = resolve_config(cwd=tmp_path)

    assert config.gui_hotload_transport == "auto"
    assert config.gui_loop_timeout_seconds == 45
    assert config.gui_loop_heartbeat_ttl_seconds == 10


def test_gui_loop_config_parses_valid_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT", " LOOP ")
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS", "15")

    config = resolve_config(cwd=tmp_path)

    assert config.gui_hotload_transport == "loop"
    assert config.gui_loop_timeout_seconds == 90
    assert config.gui_loop_heartbeat_ttl_seconds == 15


def test_gui_loop_config_falls_back_from_invalid_values(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT", "unsafe")
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS", "not-an-int")

    config = resolve_config(cwd=tmp_path)

    assert config.gui_hotload_transport == "auto"
    assert config.gui_loop_timeout_seconds == 45
    assert config.gui_loop_heartbeat_ttl_seconds == 10
