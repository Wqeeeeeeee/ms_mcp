"""Explicit, fingerprint-bound Codex MCP registration and rollback."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from .codex_config import (
    SERVER_NAME,
    build_codex_config_snippet,
    default_active_config_path,
    diagnose_codex_config,
    resolve_python_command,
)


CODEX_REGISTRATION_SCHEMA = "material_studio_mcp_codex_registration_v1"
DEFAULT_BACKUP_DIRECTORY = "materials_studio_mcp_backups"


@dataclass(frozen=True)
class _PreparedRegistration:
    receipt: dict[str, Any]
    proposed_bytes: bytes | None


class _ConfigStateChanged(RuntimeError):
    pass


def plan_codex_registration(
    *,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    python_command: str | Path | None = None,
    include_snippet: bool = True,
) -> dict[str, Any]:
    """Build a read-only, fingerprint-bound registration plan."""

    return _prepare_registration(
        config_path=config_path,
        repo_root=repo_root,
        python_command=python_command,
        include_snippet=include_snippet,
    ).receipt


def apply_codex_registration(
    *,
    expected_plan_id: str | None,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    python_command: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Apply one exact registration plan after explicit confirmation."""

    prepared = _prepare_registration(
        config_path=config_path,
        repo_root=repo_root,
        python_command=python_command,
        include_snippet=False,
    )
    plan = prepared.receipt
    config = Path(plan["config_path"])
    current_hash = _file_sha256(config)
    base = {
        "schema": CODEX_REGISTRATION_SCHEMA,
        "operation": "apply_registration",
        "config_path": str(config),
        "registration_plan_id": plan.get("registration_plan_id"),
        "expected_plan_id": expected_plan_id,
        "config_sha256_before": current_hash,
        "config_sha256_after": current_hash,
        "active_config_modified": False,
        "codex_restart_performed": False,
        "materials_studio_process_touched": False,
    }
    if not expected_plan_id:
        return {
            **base,
            "ok": False,
            "status": "explicit_plan_confirmation_required",
            "apply_ready": bool(plan.get("apply_ready")),
            "next_actions": [
                "Review a fresh preview receipt.",
                "Repeat with --apply and its exact --expected-plan-id value.",
            ],
        }
    if expected_plan_id != plan.get("registration_plan_id"):
        return {
            **base,
            "ok": False,
            "status": "registration_plan_mismatch",
            "apply_ready": False,
            "observed_plan_status": plan.get("status"),
            "next_actions": [
                "Discard the stale or unrelated plan identifier.",
                "Generate and review a fresh registration preview.",
            ],
        }
    if plan.get("status") == "already_registered":
        return {
            **base,
            "ok": True,
            "status": "already_registered",
            "apply_ready": False,
            "applied": False,
            "restart_required_now": False,
        }
    if not plan.get("apply_ready") or prepared.proposed_bytes is None:
        return {
            **base,
            "ok": False,
            "status": "registration_not_applicable",
            "apply_ready": False,
            "observed_plan_status": plan.get("status"),
            "blocking_reasons": plan.get("blocking_reasons") or [],
        }

    expected_exists = bool(plan["config_exists"])
    expected_hash = plan.get("config_sha256_before")
    proposed_bytes = prepared.proposed_bytes
    proposed_hash = _sha256_bytes(proposed_bytes)
    backup_receipt: dict[str, Any] | None = None
    try:
        _assert_config_state(config, expected_exists=expected_exists, expected_hash=expected_hash)
        if expected_exists:
            original_bytes = config.read_bytes()
            backup_path = _create_backup(
                config,
                original_bytes,
                backup_dir=backup_dir,
                label="pre-registration",
            )
            backup_receipt = {
                "path": str(backup_path),
                "sha256": _file_sha256(backup_path),
                "matches_config_preimage": _file_sha256(backup_path) == expected_hash,
                "preserved": True,
            }
        _atomic_publish(
            config,
            proposed_bytes,
            expected_exists=expected_exists,
            expected_hash=expected_hash,
        )
    except _ConfigStateChanged as exc:
        return {
            **base,
            "ok": False,
            "status": "registration_plan_stale",
            "error": str(exc),
            "backup": backup_receipt,
            "config_sha256_after": _file_sha256(config),
            "active_config_modified": _file_sha256(config) != current_hash,
            "next_actions": ["Generate and review a fresh registration preview."],
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "status": "registration_apply_failed",
            "error": _bounded_error(exc),
            "backup": backup_receipt,
            "config_sha256_after": _file_sha256(config),
            "active_config_modified": _file_sha256(config) != current_hash,
        }

    after_hash = _file_sha256(config)
    diagnosis = diagnose_codex_config(
        config_path=config,
        repo_root=plan["repo_root"],
        python_command=plan["recommended_entrypoint"]["command"],
        include_snippet=False,
    )
    applied_ok = bool(
        after_hash == proposed_hash
        and diagnosis.get("config_ready") is True
        and diagnosis.get("active_config_modified") is False
    )
    result = {
        **base,
        "ok": applied_ok,
        "status": "registration_applied" if applied_ok else "registration_postcheck_failed",
        "applied": True,
        "config_sha256_after": after_hash,
        "proposed_config_sha256": proposed_hash,
        "active_config_modified": after_hash != current_hash,
        "backup": backup_receipt,
        "existing_config_prefix_preserved": (
            config.read_bytes().startswith(
                Path(backup_receipt["path"]).read_bytes()
            )
            if backup_receipt is not None
            else None
        ),
        "post_apply_diagnosis": {
            "status": diagnosis.get("status"),
            "config_ready": diagnosis.get("config_ready"),
            "server_registered": diagnosis.get("server_registered"),
            "active_config_modified": diagnosis.get("active_config_modified"),
        },
        "restart_required_now": applied_ok,
        "next_actions": (
            [
                "Restart Codex so the Materials Studio MCP registration is loaded.",
                "Call material_studio_live_session_preflight after restart.",
            ]
            if applied_ok
            else (
                ["Restore the exact backup after reviewing the failed postcheck."]
                if backup_receipt is not None
                else ["Inspect the newly created config before any further change."]
            )
        ),
    }
    if backup_receipt is not None:
        rollback_backup_dir = str(Path(backup_receipt["path"]).parent)
        result["rollback"] = {
            "explicit_confirmation_required": True,
            "backup_path": backup_receipt["path"],
            "backup_directory": rollback_backup_dir,
            "expected_current_sha256": after_hash,
            "expected_backup_sha256": backup_receipt["sha256"],
            "command": [
                "ms-mcp-config-register",
                "--config",
                str(config),
                "--rollback-backup",
                backup_receipt["path"],
                "--expected-current-sha256",
                str(after_hash),
                "--expected-backup-sha256",
                str(backup_receipt["sha256"]),
                "--backup-dir",
                rollback_backup_dir,
            ],
        }
    return result


def rollback_codex_registration(
    *,
    backup_path: str | Path,
    expected_current_sha256: str | None,
    expected_backup_sha256: str | None,
    config_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Restore one installer backup after two explicit hash checks."""

    config = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else default_active_config_path()
    )
    backup = Path(backup_path).expanduser().resolve()
    allowed_backup_dir = _resolve_backup_dir(config, backup_dir)
    before_hash = _file_sha256(config)
    base = {
        "schema": CODEX_REGISTRATION_SCHEMA,
        "operation": "rollback_registration",
        "config_path": str(config),
        "backup_path": str(backup),
        "config_sha256_before": before_hash,
        "config_sha256_after": before_hash,
        "active_config_modified": False,
        "codex_restart_performed": False,
        "materials_studio_process_touched": False,
    }
    if not expected_current_sha256 or not expected_backup_sha256:
        return {
            **base,
            "ok": False,
            "status": "explicit_rollback_hashes_required",
        }
    if backup.parent != allowed_backup_dir:
        return {
            **base,
            "ok": False,
            "status": "backup_path_outside_allowed_directory",
            "allowed_backup_directory": str(allowed_backup_dir),
        }
    if not config.is_file() or before_hash != expected_current_sha256:
        return {
            **base,
            "ok": False,
            "status": "rollback_current_config_mismatch",
            "expected_current_sha256": expected_current_sha256,
        }
    backup_hash = _file_sha256(backup)
    if not backup.is_file() or backup_hash != expected_backup_sha256:
        return {
            **base,
            "ok": False,
            "status": "rollback_backup_mismatch",
            "expected_backup_sha256": expected_backup_sha256,
            "observed_backup_sha256": backup_hash,
        }
    try:
        backup_bytes = backup.read_bytes()
        tomllib.loads(backup_bytes.decode("utf-8"))
        safety_backup = _create_backup(
            config,
            config.read_bytes(),
            backup_dir=allowed_backup_dir,
            label="pre-rollback",
        )
        _atomic_publish(
            config,
            backup_bytes,
            expected_exists=True,
            expected_hash=expected_current_sha256,
        )
    except _ConfigStateChanged as exc:
        return {
            **base,
            "ok": False,
            "status": "rollback_state_changed",
            "error": str(exc),
            "config_sha256_after": _file_sha256(config),
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "status": "rollback_failed",
            "error": _bounded_error(exc),
            "config_sha256_after": _file_sha256(config),
        }

    after_hash = _file_sha256(config)
    restored = after_hash == backup_hash
    return {
        **base,
        "ok": restored,
        "status": "registration_rolled_back" if restored else "rollback_postcheck_failed",
        "rolled_back": True,
        "config_sha256_after": after_hash,
        "active_config_modified": after_hash != before_hash,
        "restored_backup_sha256": backup_hash,
        "source_backup_preserved": backup.is_file() and _file_sha256(backup) == backup_hash,
        "rollback_preimage_backup": {
            "path": str(safety_backup),
            "sha256": _file_sha256(safety_backup),
        },
        "restart_required_now": restored,
        "next_actions": (
            ["Restart Codex so the restored MCP configuration is loaded."]
            if restored
            else ["Stop and inspect the active config and both backups."]
        ),
    }


def _prepare_registration(
    *,
    config_path: str | Path | None,
    repo_root: str | Path | None,
    python_command: str | Path | None,
    include_snippet: bool,
) -> _PreparedRegistration:
    root = Path(repo_root or Path.cwd()).expanduser().resolve()
    config = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else default_active_config_path()
    )
    command = resolve_python_command(root, python_command)
    snippet = build_codex_config_snippet(root, python_command=command)
    snippet_hash = _sha256_bytes(snippet.encode("utf-8"))
    diagnosis = diagnose_codex_config(
        config_path=config,
        repo_root=root,
        python_command=command,
        include_snippet=False,
    )
    config_exists = config.is_file()
    before_hash = _file_sha256(config)
    receipt: dict[str, Any] = {
        "schema": CODEX_REGISTRATION_SCHEMA,
        "operation": "plan_registration",
        "ok": True,
        "status": "registration_blocked",
        "read_only": True,
        "config_path": str(config),
        "config_exists": config_exists,
        "config_sha256_before": before_hash,
        "config_sha256_after": before_hash,
        "active_config_modified": False,
        "repo_root": str(root),
        "recommended_entrypoint": diagnosis.get("recommended_entrypoint"),
        "diagnosis_status": diagnosis.get("status"),
        "managed_runtime": diagnosis.get("managed_runtime"),
        "recommended_snippet_sha256": snippet_hash,
        "change_required": True,
        "change_kind": None,
        "apply_ready": False,
        "explicit_apply_required": True,
        "backup_required": config_exists,
        "restart_required_after_apply": True,
        "existing_config_bytes_preserved": False,
        "materials_studio_process_touched": False,
        "codex_process_touched": False,
        "blocking_reasons": [],
    }
    if include_snippet:
        receipt["recommended_snippet"] = snippet

    if diagnosis.get("status") == "ready":
        receipt.update(
            {
                "status": "already_registered",
                "change_required": False,
                "change_kind": "none",
                "backup_required": False,
                "restart_required_after_apply": False,
                "existing_config_bytes_preserved": True,
            }
        )
        receipt["registration_plan_id"] = _registration_plan_id(receipt, None)
        receipt["next_actions"] = [
            "Restart Codex only if the configured server is not visible in this session.",
            "Call material_studio_live_session_preflight before live modeling.",
        ]
        return _PreparedRegistration(receipt, None)

    entrypoint = diagnosis.get("recommended_entrypoint") or {}
    if not entrypoint.get("python_exists") or not entrypoint.get("run_server_exists"):
        receipt.update(
            {
                "ok": False,
                "status": "entrypoint_not_ready",
                "blocking_reasons": ["python_or_run_server_missing"],
            }
        )
        return _PreparedRegistration(receipt, None)
    if diagnosis.get("status") == "legacy_entrypoint_detected":
        receipt.update(
            {
                "ok": False,
                "status": "legacy_registration_conflict",
                "blocking_reasons": ["legacy_materials_studio_registration_requires_manual_review"],
                "registration_candidates": diagnosis.get("registration_candidates") or [],
            }
        )
        return _PreparedRegistration(receipt, None)
    if diagnosis.get("status") not in {"server_not_registered", "active_config_missing"}:
        receipt.update(
            {
                "ok": False,
                "status": "existing_registration_requires_manual_review",
                "blocking_reasons": [
                    str(diagnosis.get("status") or "unknown_configuration_conflict")
                ],
            }
        )
        return _PreparedRegistration(receipt, None)

    try:
        existing_bytes = config.read_bytes() if config_exists else b""
        observed_hash = _sha256_bytes(existing_bytes) if config_exists else None
        if observed_hash != before_hash:
            raise _ConfigStateChanged("active config changed while the preview was prepared")
        proposed_bytes = _append_registration(existing_bytes, snippet)
        validation_errors = _validate_proposed_config(existing_bytes, proposed_bytes, snippet)
    except Exception as exc:
        receipt.update(
            {
                "ok": False,
                "status": "registration_proposal_failed",
                "blocking_reasons": [_bounded_error(exc)],
            }
        )
        return _PreparedRegistration(receipt, None)
    if validation_errors:
        receipt.update(
            {
                "ok": False,
                "status": "registration_proposal_invalid",
                "blocking_reasons": validation_errors,
            }
        )
        return _PreparedRegistration(receipt, None)

    proposed_hash = _sha256_bytes(proposed_bytes)
    receipt.update(
        {
            "status": "registration_ready",
            "change_kind": (
                "append_server_registration" if config_exists else "create_active_config"
            ),
            "apply_ready": True,
            "existing_config_bytes_preserved": True,
            "existing_config_prefix_length": len(existing_bytes),
            "existing_config_prefix_sha256": (
                _sha256_bytes(existing_bytes) if config_exists else None
            ),
            "proposed_config_sha256": proposed_hash,
        }
    )
    receipt["registration_plan_id"] = _registration_plan_id(receipt, proposed_hash)
    receipt["apply_contract"] = {
        "operation": "apply_registration",
        "expected_plan_id": receipt["registration_plan_id"],
        "config_path": str(config),
        "repo_root": str(root),
        "python_command": str(command),
        "no_restart_performed_by_installer": True,
    }
    receipt["next_actions"] = [
        "Review this receipt and the recommended snippet.",
        "Apply only with the exact registration_plan_id from this preview.",
        "Restart Codex after a successful apply.",
    ]
    return _PreparedRegistration(receipt, proposed_bytes)


def _append_registration(existing: bytes, snippet: str) -> bytes:
    if existing:
        existing.decode("utf-8")
    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    snippet_bytes = snippet.replace("\n", newline.decode("ascii")).encode("utf-8")
    if not existing:
        return snippet_bytes
    if existing.endswith(newline * 2):
        separator = b""
    elif existing.endswith(newline):
        separator = newline
    else:
        separator = newline * 2
    return existing + separator + snippet_bytes


def _validate_proposed_config(
    original_bytes: bytes,
    proposed_bytes: bytes,
    snippet: str,
) -> list[str]:
    try:
        original = tomllib.loads(original_bytes.decode("utf-8")) if original_bytes else {}
        proposed = tomllib.loads(proposed_bytes.decode("utf-8"))
        expected = tomllib.loads(snippet)["mcp_servers"][SERVER_NAME]
    except Exception as exc:
        return [f"proposed_toml_parse_failed: {_bounded_error(exc)}"]
    observed_servers = proposed.get("mcp_servers")
    observed = (
        observed_servers.get(SERVER_NAME)
        if isinstance(observed_servers, dict)
        else None
    )
    errors: list[str] = []
    if original_bytes and not proposed_bytes.startswith(original_bytes):
        errors.append("unrelated_configuration_bytes_not_preserved_as_prefix")
    if observed != expected:
        errors.append("proposed_materials_studio_registration_mismatch")
    if _without_materials_studio(
        proposed,
        preserve_empty_servers="mcp_servers" in original,
    ) != original:
        errors.append("unrelated_configuration_semantics_changed")
    return errors


def _without_materials_studio(
    payload: dict[str, Any],
    *,
    preserve_empty_servers: bool,
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    servers = result.get("mcp_servers")
    if isinstance(servers, dict):
        servers.pop(SERVER_NAME, None)
        if not servers and not preserve_empty_servers:
            result.pop("mcp_servers", None)
    return result


def _registration_plan_id(receipt: dict[str, Any], proposed_hash: str | None) -> str:
    guard = {
        "schema": CODEX_REGISTRATION_SCHEMA,
        "config_path": _normalized_path(receipt["config_path"]),
        "config_exists": receipt.get("config_exists"),
        "config_sha256_before": receipt.get("config_sha256_before"),
        "repo_root": _normalized_path(receipt["repo_root"]),
        "recommended_entrypoint": receipt.get("recommended_entrypoint"),
        "recommended_snippet_sha256": receipt.get("recommended_snippet_sha256"),
        "change_kind": receipt.get("change_kind"),
        "proposed_config_sha256": proposed_hash,
    }
    encoded = json.dumps(guard, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _assert_config_state(
    config: Path,
    *,
    expected_exists: bool,
    expected_hash: str | None,
) -> None:
    observed_exists = config.is_file()
    observed_hash = _file_sha256(config)
    if observed_exists != expected_exists or observed_hash != expected_hash:
        raise _ConfigStateChanged(
            "active config no longer matches the reviewed registration plan"
        )


def _atomic_publish(
    config: Path,
    content: bytes,
    *,
    expected_exists: bool,
    expected_hash: str | None,
) -> None:
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    original_mode = (
        stat.S_IMODE(config.stat().st_mode)
        if expected_exists and config.exists()
        else None
    )
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=config.parent,
            prefix=f".{config.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        if original_mode is not None:
            os.chmod(temporary_path, original_mode)
        _assert_config_state(
            config,
            expected_exists=expected_exists,
            expected_hash=expected_hash,
        )
        os.replace(temporary_path, config)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _create_backup(
    config: Path,
    content: bytes,
    *,
    backup_dir: str | Path | None,
    label: str,
) -> Path:
    directory = _resolve_backup_dir(config, backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    digest = _sha256_bytes(content)
    path = directory / (
        f"{config.name}.{timestamp}.{label}.{digest[:12]}.{uuid.uuid4().hex[:8]}.bak"
    )
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if _file_sha256(path) != digest:
        raise OSError("backup hash verification failed")
    return path


def _resolve_backup_dir(config: Path, backup_dir: str | Path | None) -> Path:
    if backup_dir is None:
        return (config.parent / DEFAULT_BACKUP_DIRECTORY).resolve()
    return Path(backup_dir).expanduser().resolve()


def _normalized_path(value: str | Path) -> str:
    resolved = str(Path(value).expanduser().resolve())
    return resolved.casefold() if os.name == "nt" else resolved


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return _sha256_bytes(path.read_bytes())


def _bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly apply a fingerprint-bound Materials Studio MCP "
            "registration without replacing unrelated Codex configuration."
        )
    )
    parser.add_argument(
        "--config",
        help="Active Codex config.toml; defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path.cwd()),
        help="Materials Studio MCP repository root.",
    )
    parser.add_argument(
        "--python",
        dest="python_command",
        help="Python executable for the MCP entrypoint.",
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--apply",
        action="store_true",
        help="Apply a reviewed plan; requires --expected-plan-id.",
    )
    operation.add_argument(
        "--rollback-backup",
        help="Restore one installer backup after both SHA-256 checks.",
    )
    parser.add_argument("--expected-plan-id")
    parser.add_argument("--expected-current-sha256")
    parser.add_argument("--expected-backup-sha256")
    parser.add_argument("--backup-dir")
    parser.add_argument(
        "--omit-snippet",
        action="store_true",
        help="Omit the generated TOML from preview output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _build_parser().parse_args(argv)
    if options.rollback_backup:
        result = rollback_codex_registration(
            config_path=options.config,
            backup_path=options.rollback_backup,
            expected_current_sha256=options.expected_current_sha256,
            expected_backup_sha256=options.expected_backup_sha256,
            backup_dir=options.backup_dir,
        )
    elif options.apply:
        result = apply_codex_registration(
            config_path=options.config,
            repo_root=options.cwd,
            python_command=options.python_command,
            expected_plan_id=options.expected_plan_id,
            backup_dir=options.backup_dir,
        )
    else:
        result = plan_codex_registration(
            config_path=options.config,
            repo_root=options.cwd,
            python_command=options.python_command,
            include_snippet=not options.omit_snippet,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
