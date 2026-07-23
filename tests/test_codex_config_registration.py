from __future__ import annotations

import hashlib
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from material_studio_mcp_server.codex_config import (
    build_codex_config_snippet,
    diagnose_codex_config,
)
from material_studio_mcp_server.codex_registration import (
    apply_codex_registration,
    main,
    plan_codex_registration,
    rollback_codex_registration,
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


def test_registration_preview_is_read_only_and_fingerprint_bound(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    original = (
        b"# keep this exact comment\r\n"
        b"[projects.'C:\\\\work']\r\n"
        b"trust_level = 'trusted'\r\n"
    )
    config.write_bytes(original)

    result = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    assert result["ok"] is True
    assert result["status"] == "registration_ready"
    assert result["read_only"] is True
    assert result["apply_ready"] is True
    assert result["change_kind"] == "append_server_registration"
    assert result["existing_config_prefix_length"] == len(original)
    assert result["existing_config_prefix_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["registration_plan_id"] == result["apply_contract"]["expected_plan_id"]
    assert len(result["registration_plan_id"]) == 64
    assert "[mcp_servers.materials_studio]" in result["recommended_snippet"]
    assert config.read_bytes() == original
    assert result["config_sha256_before"] == result["config_sha256_after"]


def test_registration_apply_preserves_prefix_and_creates_exact_backup(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    backup_dir = tmp_path / "backups"
    original = (
        b"# unrelated config remains byte-for-byte\r\n"
        b"[mcp_servers.other]\r\n"
        b"command = 'other.exe'\r\n"
        b"\r\n"
        b"[projects.'C:\\\\work']\r\n"
        b"trust_level = 'trusted'\r\n"
    )
    config.write_bytes(original)
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        include_snippet=False,
    )

    result = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
        backup_dir=backup_dir,
    )

    assert result["ok"] is True
    assert result["status"] == "registration_applied"
    assert result["applied"] is True
    assert result["active_config_modified"] is True
    assert result["existing_config_prefix_preserved"] is True
    assert result["restart_required_now"] is True
    assert result["codex_restart_performed"] is False
    assert result["materials_studio_process_touched"] is False
    assert config.read_bytes().startswith(original)
    assert b"\r\n[mcp_servers.materials_studio]\r\n" in config.read_bytes()
    backup = Path(result["backup"]["path"])
    assert backup.parent == backup_dir.resolve()
    assert backup.read_bytes() == original
    assert result["backup"]["matches_config_preimage"] is True
    assert result["post_apply_diagnosis"]["config_ready"] is True
    assert result["rollback"]["expected_current_sha256"] == _sha256(config)
    assert result["rollback"]["expected_backup_sha256"] == _sha256(backup)
    assert result["rollback"]["backup_directory"] == str(backup_dir.resolve())
    assert result["rollback"]["command"][-2:] == [
        "--backup-dir",
        str(backup_dir.resolve()),
    ]


def test_apply_requires_exact_reviewed_plan_id(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    before = _sha256(config)

    missing = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=None,
    )
    mismatched = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id="0" * 64,
    )

    assert missing["status"] == "explicit_plan_confirmation_required"
    assert mismatched["status"] == "registration_plan_mismatch"
    assert _sha256(config) == before
    assert not (config.parent / "materials_studio_mcp_backups").exists()


def test_stale_plan_is_rejected_without_backup_or_write(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )
    config.write_text("[projects]\n# changed after review\n", encoding="utf-8")
    changed = config.read_bytes()

    result = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
    )

    assert result["ok"] is False
    assert result["status"] == "registration_plan_mismatch"
    assert config.read_bytes() == changed
    assert not (config.parent / "materials_studio_mcp_backups").exists()


def test_existing_or_legacy_registration_requires_manual_review(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    existing = tmp_path / "existing.toml"
    existing.write_text(
        "[mcp_servers.materials_studio]\n"
        "command = 'stale.exe'\n"
        "args = ['old.py']\n"
        "cwd = 'C:\\\\old'\n",
        encoding="utf-8",
    )
    legacy = tmp_path / "legacy.toml"
    legacy.write_text(
        "[mcp_servers.legacy_ms]\n"
        f"command = {str(python)!r}\n"
        "args = ['-m', 'ms_mcp.server']\n",
        encoding="utf-8",
    )

    existing_result = plan_codex_registration(
        config_path=existing,
        repo_root=root,
        python_command=python,
    )
    legacy_result = plan_codex_registration(
        config_path=legacy,
        repo_root=root,
        python_command=python,
    )

    assert existing_result["status"] == "existing_registration_requires_manual_review"
    assert existing_result["apply_ready"] is False
    assert existing_result["blocking_reasons"] == ["entrypoint_drift"]
    assert legacy_result["status"] == "legacy_registration_conflict"
    assert legacy_result["apply_ready"] is False
    assert legacy_result["registration_candidates"][0]["server_name"] == "legacy_ms"


def test_already_registered_plan_and_apply_are_noop(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        build_codex_config_snippet(root, python_command=python),
        encoding="utf-8",
    )
    before = _sha256(config)
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    result = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
    )

    assert plan["status"] == "already_registered"
    assert plan["change_required"] is False
    assert result["ok"] is True
    assert result["status"] == "already_registered"
    assert result["applied"] is False
    assert _sha256(config) == before


def test_registration_can_create_missing_config_without_backup(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "new-codex-home" / "config.toml"
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )

    result = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
    )

    assert plan["change_kind"] == "create_active_config"
    assert result["ok"] is True
    assert result["backup"] is None
    assert config.is_file()
    assert tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"][
        "materials_studio"
    ]["enabled"] is True


def test_registration_rollback_restores_exact_preimage_and_keeps_evidence(
    tmp_path: Path,
) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    backup_dir = tmp_path / "backups"
    original = b"# preimage\n[projects]\n"
    config.write_bytes(original)
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )
    applied = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
        backup_dir=backup_dir,
    )

    result = rollback_codex_registration(
        config_path=config,
        backup_path=applied["backup"]["path"],
        expected_current_sha256=applied["config_sha256_after"],
        expected_backup_sha256=applied["backup"]["sha256"],
        backup_dir=backup_dir,
    )

    assert result["ok"] is True
    assert result["status"] == "registration_rolled_back"
    assert result["active_config_modified"] is True
    assert config.read_bytes() == original
    assert result["source_backup_preserved"] is True
    safety_backup = Path(result["rollback_preimage_backup"]["path"])
    assert safety_backup.is_file()
    assert safety_backup.read_bytes() != original
    assert result["restart_required_now"] is True


def test_returned_rollback_command_is_directly_callable(
    tmp_path: Path,
    capsys,
) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    backup_dir = tmp_path / "custom-backups"
    original = b"[projects]\n"
    config.write_bytes(original)
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )
    applied = apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
        backup_dir=backup_dir,
    )

    exit_code = main(applied["rollback"]["command"][1:])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"status": "registration_rolled_back"' in output
    assert config.read_bytes() == original


def test_rollback_rejects_wrong_hash_and_backup_outside_allowed_directory(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside.bak"
    outside.write_text("", encoding="utf-8")
    before = _sha256(config)

    outside_result = rollback_codex_registration(
        config_path=config,
        backup_path=outside,
        expected_current_sha256=before,
        expected_backup_sha256=_sha256(outside),
        backup_dir=allowed,
    )
    wrong_hash_result = rollback_codex_registration(
        config_path=config,
        backup_path=allowed / "missing.bak",
        expected_current_sha256="f" * 64,
        expected_backup_sha256="e" * 64,
        backup_dir=allowed,
    )

    assert outside_result["status"] == "backup_path_outside_allowed_directory"
    assert wrong_hash_result["status"] == "rollback_current_config_mismatch"
    assert _sha256(config) == before


def test_malformed_config_and_missing_entrypoint_block_registration(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[broken\n", encoding="utf-8")
    missing_root = tmp_path / "missing-repo"

    malformed_result = plan_codex_registration(
        config_path=malformed,
        repo_root=root,
        python_command=python,
    )
    missing_result = plan_codex_registration(
        config_path=tmp_path / "missing-config.toml",
        repo_root=missing_root,
        python_command=missing_root / "python.exe",
    )

    assert malformed_result["status"] == "existing_registration_requires_manual_review"
    assert malformed_result["blocking_reasons"] == ["active_config_parse_failed"]
    assert missing_result["status"] == "entrypoint_not_ready"


def test_registration_cli_defaults_to_preview_and_apply_needs_plan_id(
    tmp_path: Path,
    capsys,
) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    before = _sha256(config)

    preview_exit = main(
        [
            "--config",
            str(config),
            "--cwd",
            str(root),
            "--python",
            str(python),
            "--omit-snippet",
        ]
    )
    preview_output = capsys.readouterr().out
    apply_exit = main(
        [
            "--config",
            str(config),
            "--cwd",
            str(root),
            "--python",
            str(python),
            "--apply",
        ]
    )
    apply_output = capsys.readouterr().out

    assert preview_exit == 0
    assert '"operation": "plan_registration"' in preview_output
    assert '"status": "registration_ready"' in preview_output
    assert apply_exit == 2
    assert '"status": "explicit_plan_confirmation_required"' in apply_output
    assert _sha256(config) == before


def test_applied_config_is_accepted_by_existing_read_only_doctor(tmp_path: Path) -> None:
    root, python = _repo_fixture(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[mcp_servers.other]\ncommand = 'other.exe'\n", encoding="utf-8")
    plan = plan_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
    )
    apply_codex_registration(
        config_path=config,
        repo_root=root,
        python_command=python,
        expected_plan_id=plan["registration_plan_id"],
    )

    diagnosis = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=python,
        include_snippet=False,
    )

    assert diagnosis["status"] == "ready"
    assert diagnosis["config_ready"] is True
    assert diagnosis["active_config_modified"] is False
