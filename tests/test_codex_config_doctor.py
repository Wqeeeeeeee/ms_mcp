from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from material_studio_mcp_server.codex_config import (
    DISABLED_TOOLS,
    SAFE_ENABLED_TOOLS,
    build_codex_config_snippet,
    diagnose_codex_config,
    main,
    write_recommended_snippet,
)


def _repo_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    (root / "run_server.py").write_text("print('server')\n", encoding="utf-8")
    return root, python


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generated_config_snippet_is_parseable_and_matches_safe_example(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)

    snippet = build_codex_config_snippet(root, python_command=python)
    payload = tomllib.loads(snippet)
    server = payload["mcp_servers"]["materials_studio"]
    example = tomllib.loads(Path(".codex/config.toml.example").read_text(encoding="utf-8"))
    example_server = example["mcp_servers"]["materials_studio"]

    assert server["command"] == str(python.resolve())
    assert server["args"] == [str((root / "run_server.py").resolve())]
    assert server["cwd"] == str(root.resolve())
    assert tuple(server["enabled_tools"]) == SAFE_ENABLED_TOOLS
    assert tuple(server["disabled_tools"]) == DISABLED_TOOLS
    assert set(example_server["enabled_tools"]) == set(SAFE_ENABLED_TOOLS)
    assert set(example_server["disabled_tools"]) == set(DISABLED_TOOLS)
    assert server["tools"]["material_studio_live_modeling_request"]["approval_mode"] == "prompt"
    assert server["tools"]["material_studio_run_script"]["approval_mode"] == "prompt"


def test_doctor_reports_missing_registration_without_modifying_active_config(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[projects.'C:\\\\work']\ntrust_level = 'trusted'\n", encoding="utf-8")
    before = _sha256(config)

    result = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    assert result["ok"] is True
    assert result["status"] == "server_not_registered"
    assert result["config_ready"] is False
    assert result["server_registered"] is False
    assert result["read_only"] is True
    assert result["active_config_modified"] is False
    assert result["config_sha256_before"] == before
    assert result["config_sha256_after"] == before
    assert result["recommended_entrypoint"]["python_exists"] is True
    assert result["recommended_entrypoint"]["run_server_exists"] is True
    assert "[mcp_servers.materials_studio]" in result["recommended_snippet"]
    assert result["next_actions"][0].startswith(
        "Preview a guarded append with ms-mcp-config-register"
    )
    assert _sha256(config) == before


def test_doctor_accepts_exact_safe_registration(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        build_codex_config_snippet(root, python_command=python)
        + "\n[projects.'C:\\\\work']\ntrust_level = 'trusted'\n",
        encoding="utf-8",
    )

    result = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=python,
        include_snippet=False,
    )

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["config_ready"] is True
    assert result["server_registered"] is True
    assert result["observed_entrypoint"] == {
        "server_name": "materials_studio",
        "command": str(python.resolve()),
        "args": [str((root / "run_server.py").resolve())],
        "additional_arg_count": 0,
        "cwd": str(root.resolve()),
    }
    assert result["command_matches"] is True
    assert result["args_match"] is True
    assert result["cwd_matches"] is True
    assert result["missing_required_tools"] == []
    assert result["missing_recommended_tools"] == []
    assert result["unexpected_dangerous_enabled_tools"] == []
    assert result["run_script_explicitly_disabled"] is True
    assert result["active_config_modified"] is False
    assert "recommended_snippet" not in result


def test_doctor_requires_the_complete_recommended_safe_allowlist(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    incomplete = build_codex_config_snippet(root, python_command=python).replace(
        '  "material_studio_model_validate",\n',
        "",
        1,
    )
    config.write_text(incomplete, encoding="utf-8")

    result = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    assert result["status"] == "tool_allowlist_drift"
    assert result["config_ready"] is False
    assert result["missing_required_tools"] == []
    assert result["missing_recommended_tools"] == ["material_studio_model_validate"]


def test_doctor_detects_legacy_ms_mcp_registration(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            (
                "[mcp_servers.legacy_materials_studio]",
                f"command = {python.as_posix()!r}",
                "args = ['-m', 'ms_mcp.server']",
                "enabled = true",
                "",
            )
        ),
        encoding="utf-8",
    )

    result = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    assert result["status"] == "legacy_entrypoint_detected"
    assert result["config_ready"] is False
    assert result["server_registered"] is False
    assert result["registration_candidates"][0]["server_name"] == "legacy_materials_studio"
    assert result["registration_candidates"][0]["legacy_ms_mcp_entrypoint"] is True


def test_snippet_writer_refuses_to_overwrite_active_config(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    active = tmp_path / "config.toml"
    active.write_text("[projects]\n", encoding="utf-8")
    before = _sha256(active)

    with pytest.raises(ValueError, match="refusing to overwrite the active Codex config"):
        write_recommended_snippet(
            active,
            active_config_path=active,
            repo_root=root,
            python_command=python,
        )

    output = write_recommended_snippet(
        tmp_path / "generated" / "materials_studio.toml",
        active_config_path=active,
        repo_root=root,
        python_command=python,
    )
    assert output.exists()
    assert "[mcp_servers.materials_studio]" in output.read_text(encoding="utf-8")
    assert _sha256(active) == before


def test_config_doctor_cli_strict_mode_reports_drift(tmp_path: Path, capsys) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config),
            "--cwd",
            str(root),
            "--python",
            str(python),
            "--omit-snippet",
            "--strict",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"status": "server_not_registered"' in output
    assert '"active_config_modified": false' in output
