"""Read-only Codex MCP configuration diagnostics and safe snippet generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from .protocol_smoke import REQUIRED_PROTOCOL_TOOLS, audit_codex_config
from .managed_runtime import (
    managed_runtime_server_args,
    managed_runtime_status,
)


SERVER_NAME = "materials_studio"
RUNTIME_CODEX_CONFIG_SCHEMA = "material_studio_mcp_runtime_codex_config_status_v1"
DISABLED_TOOLS: tuple[str, ...] = ("material_studio_run_script",)
SAFE_ENABLED_TOOLS: tuple[str, ...] = (
    "material_studio_get_status",
    "material_studio_live_capabilities",
    "material_studio_live_session_preflight",
    "material_studio_model_validate",
    "material_studio_model_create_from_spec",
    "material_studio_model_modify_with_patch",
    "material_studio_model_preview_script",
    "material_studio_model_get_current",
    "material_studio_live_modeling_request",
    "material_studio_live_project_status",
    "material_studio_live_watchdog_status",
    "material_studio_model_export_view_audit",
    "material_studio_model_export_view_bundle",
    "material_studio_live_update_with_patch",
    "material_studio_project_history",
    "material_studio_project_rollback",
    "material_studio_project_reconcile_dopant_metadata",
    "material_studio_gui_status",
    "material_studio_gui_launch",
    "material_studio_gui_activate",
    "material_studio_gui_snapshot",
    "material_studio_gui_open_structure",
    "material_studio_gui_apply_current_revision",
    "material_studio_gui_fit_to_view",
    "material_studio_gui_record_visual_confirmation",
    "material_studio_gui_copy_script_assist",
    "material_studio_gui_prepare_view_replay",
    "material_studio_gui_execute_view_replay",
    "material_studio_gui_record_view_replay",
    "material_studio_structure_summary",
    "material_studio_import_export",
    "material_studio_forcite_geometry_optimization",
    "material_studio_castep_energy_script",
    "material_studio_castep_relax_current",
    "material_studio_castep_run_current",
    "material_studio_list_script_templates",
)
PROMPT_TOOLS: tuple[str, ...] = (
    "material_studio_model_create_from_spec",
    "material_studio_model_modify_with_patch",
    "material_studio_live_modeling_request",
    "material_studio_live_update_with_patch",
    "material_studio_project_reconcile_dopant_metadata",
    "material_studio_gui_launch",
    "material_studio_gui_apply_current_revision",
    "material_studio_gui_fit_to_view",
    "material_studio_gui_record_visual_confirmation",
    "material_studio_gui_open_structure",
    "material_studio_gui_execute_view_replay",
    "material_studio_castep_relax_current",
    "material_studio_castep_run_current",
    "material_studio_run_script",
)


def default_active_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return (root / "config.toml").resolve()


def resolve_python_command(repo_root: str | Path, command: str | Path | None = None) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if command is not None:
        return Path(command).expanduser().resolve()
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return Path(sys.executable).resolve()


def build_codex_config_snippet(
    repo_root: str | Path,
    *,
    python_command: str | Path | None = None,
) -> str:
    root = Path(repo_root).expanduser().resolve()
    command = resolve_python_command(root, python_command)
    server_args = managed_runtime_server_args(root)
    lines = [
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_string(command)}",
        f"args = [{', '.join(_toml_string(item) for item in server_args)}]",
        f"cwd = {_toml_string(root)}",
        "startup_timeout_sec = 30",
        "tool_timeout_sec = 1800",
        'default_tools_approval_mode = "prompt"',
        "enabled = true",
        "",
        "enabled_tools = [",
        *[f"  {_toml_string(tool)}," for tool in SAFE_ENABLED_TOOLS],
        "]",
        "",
        "disabled_tools = [",
        *[f"  {_toml_string(tool)}," for tool in DISABLED_TOOLS],
        "]",
    ]
    for tool in PROMPT_TOOLS:
        lines.extend(
            (
                "",
                f"[mcp_servers.{SERVER_NAME}.tools.{tool}]",
                'approval_mode = "prompt"',
            )
        )
    return "\n".join(lines) + "\n"


def diagnose_codex_config(
    *,
    config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    python_command: str | Path | None = None,
    include_snippet: bool = True,
) -> dict[str, Any]:
    """Inspect an active Codex config without modifying it."""

    root = Path(repo_root or Path.cwd()).expanduser().resolve()
    config = Path(config_path).expanduser().resolve() if config_path else default_active_config_path()
    command = resolve_python_command(root, python_command)
    run_server = (root / "run_server.py").resolve()
    expected_args = managed_runtime_server_args(root)
    managed_runtime = managed_runtime_status(root)
    snippet = build_codex_config_snippet(root, python_command=command)
    before_hash = _file_sha256(config)
    result: dict[str, Any] = {
        "ok": True,
        "status": "unknown",
        "config_ready": False,
        "read_only": True,
        "active_config_modified": False,
        "config_path": str(config),
        "config_exists": config.exists(),
        "config_sha256_before": before_hash,
        "config_sha256_after": before_hash,
        "repo_root": str(root),
        "recommended_entrypoint": {
            "server_name": SERVER_NAME,
            "command": str(command),
            "args": expected_args,
            "cwd": str(root),
            "python_exists": command.exists(),
            "run_server_exists": run_server.exists(),
        },
        "managed_runtime": {
            "status": managed_runtime.get("status"),
            "managed": managed_runtime.get("managed"),
            "integrity_verified": managed_runtime.get("integrity_verified"),
            "manifest_path": managed_runtime.get("manifest_path"),
            "manifest_sha256": managed_runtime.get("manifest_sha256"),
            "source_commit": managed_runtime.get("source_commit"),
            "errors": managed_runtime.get("errors") or [],
        },
        "required_protocol_tool_count": len(REQUIRED_PROTOCOL_TOOLS),
        "recommended_enabled_tool_count": len(SAFE_ENABLED_TOOLS),
        "restart_required_after_config_change": True,
        "recommended_snippet_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
    }
    if include_snippet:
        result["recommended_snippet"] = snippet

    if not run_server.exists():
        result.update(
            {
                "ok": False,
                "status": "repo_entrypoint_missing",
                "error": "run_server_not_found",
                "next_actions": ["Run the doctor from the Materials Studio MCP repository root."],
            }
        )
        return result
    if (
        managed_runtime.get("managed") is True
        and managed_runtime.get("integrity_verified") is not True
    ):
        result.update(
            {
                "ok": False,
                "status": "managed_runtime_integrity_failed",
                "error": "managed_runtime_integrity_failed",
                "next_actions": [
                    "Do not register or start the modified managed runtime.",
                    "Deploy the reviewed commit to a new immutable runtime path.",
                ],
            }
        )
        return result
    if not config.exists():
        result.update(
            {
                "status": "active_config_missing",
                "next_actions": _activation_actions(config),
            }
        )
        return result

    try:
        payload = tomllib.loads(config.read_text(encoding="utf-8"))
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "active_config_parse_failed",
                "error": f"config_parse_failed: {exc}",
                "next_actions": [
                    "Repair the TOML syntax without discarding unrelated user configuration.",
                    *_activation_actions(config),
                ],
            }
        )
        return result

    servers = payload.get("mcp_servers") if isinstance(payload.get("mcp_servers"), dict) else {}
    server = servers.get(SERVER_NAME) if isinstance(servers.get(SERVER_NAME), dict) else None
    candidates = _registration_candidates(servers)
    result["registration_candidates"] = candidates
    if server is None:
        result.update(
            {
                "status": "legacy_entrypoint_detected" if candidates else "server_not_registered",
                "server_registered": False,
                "next_actions": _activation_actions(config),
            }
        )
        return result

    base_audit = audit_codex_config(config)
    command_matches = _same_path(server.get("command"), command)
    args = server.get("args") if isinstance(server.get("args"), list) else []
    args_match = _same_server_args(args, expected_args)
    cwd_matches = _same_path(server.get("cwd"), root)
    missing_recommended = sorted(
        set(SAFE_ENABLED_TOOLS) - {str(item) for item in server.get("enabled_tools", []) or []}
    )
    config_ready = bool(
        base_audit.get("ok")
        and not missing_recommended
        and server.get("enabled", True)
        and command_matches
        and args_match
        and cwd_matches
        and command.exists()
        and run_server.exists()
    )
    if config_ready:
        status = "ready"
    elif not server.get("enabled", True):
        status = "server_disabled"
    elif not (command_matches and args_match and cwd_matches):
        status = "entrypoint_drift"
    else:
        status = "tool_allowlist_drift"
    result.update(
        {
            "status": status,
            "config_ready": config_ready,
            "server_registered": True,
            "server_enabled": bool(server.get("enabled", True)),
            "observed_entrypoint": {
                "server_name": SERVER_NAME,
                "command": str(server.get("command") or "") or None,
                "args": [str(item) for item in args],
                "additional_arg_count": max(0, len(args) - 1),
                "cwd": str(server.get("cwd") or "") or None,
            },
            "command_matches": command_matches,
            "args_match": args_match,
            "cwd_matches": cwd_matches,
            "missing_required_tools": base_audit.get("missing_enabled_tools") or [],
            "missing_recommended_tools": missing_recommended,
            "unexpected_dangerous_enabled_tools": base_audit.get(
                "unexpected_dangerous_enabled_tools"
            )
            or [],
            "run_script_explicitly_disabled": base_audit.get(
                "run_script_explicitly_disabled"
            ),
            "restart_required_now": not config_ready,
            "next_actions": (
                [
                    "Restart Codex if the configured MCP server is not visible in the current session.",
                    "Call material_studio_live_session_preflight before the first live modeling request.",
                ]
                if config_ready
                else _activation_actions(config)
            ),
        }
    )
    result["config_sha256_after"] = _file_sha256(config)
    result["active_config_modified"] = result["config_sha256_after"] != before_hash
    return result


def diagnose_runtime_codex_config(
    *,
    repository_root: str | Path,
    python_command: str | Path | None = None,
) -> dict[str, Any]:
    """Compare bounded user/repository Codex configs with one source checkout."""

    root = Path(repository_root).expanduser().resolve()
    candidate_paths = [
        ("codex_home", default_active_config_path()),
        ("repository_local", root / ".codex" / "config.toml"),
    ]
    seen_paths: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for scope, path in candidate_paths:
        resolved_path = path.expanduser().resolve()
        normalized = str(resolved_path).casefold()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        candidate = diagnose_codex_config(
            config_path=resolved_path,
            repo_root=root,
            python_command=python_command,
            include_snippet=False,
        )
        candidate["config_scope"] = scope
        candidate["runtime_source_binding_matches_config"] = bool(
            candidate.get("command_matches")
            and candidate.get("args_match")
            and candidate.get("cwd_matches")
        )
        candidates.append(candidate)

    matching = [
        item
        for item in candidates
        if item.get("server_registered") is True
        and item.get("runtime_source_binding_matches_config") is True
    ]
    registered = [
        item for item in candidates if item.get("server_registered") is True
    ]
    ready_matching = [
        item for item in matching if item.get("config_ready") is True
    ]
    ready_registered = [
        item for item in registered if item.get("config_ready") is True
    ]
    if ready_matching:
        selected = ready_matching[0]
    elif matching:
        selected = matching[0]
    elif ready_registered:
        selected = ready_registered[0]
    elif registered:
        selected = registered[0]
    else:
        selected = candidates[0]

    result = dict(selected)
    registration_candidates = result.get("registration_candidates")
    result["registration_candidate_count"] = (
        len(registration_candidates)
        if isinstance(registration_candidates, list)
        else 0
    )
    result.pop("registration_candidates", None)
    if len(matching) > 1:
        resolution_status = "multiple_matching_runtime_registrations"
    elif len(matching) == 1:
        resolution_status = "matching_runtime_registration_found"
    elif registered:
        resolution_status = "registered_entrypoint_drift"
    else:
        resolution_status = "materials_studio_registration_not_found"
    result.update(
        {
            "schema": RUNTIME_CODEX_CONFIG_SCHEMA,
            "config_resolution_status": resolution_status,
            "config_source_ambiguous": len(registered) > 1,
            "config_candidate_count": len(candidates),
            "config_candidates": [
                {
                    "config_scope": item.get("config_scope"),
                    "config_path": item.get("config_path"),
                    "config_exists": item.get("config_exists"),
                    "status": item.get("status"),
                    "config_ready": item.get("config_ready"),
                    "server_registered": item.get("server_registered"),
                    "runtime_source_binding_matches_config": item.get(
                        "runtime_source_binding_matches_config"
                    ),
                }
                for item in candidates
            ],
            "advisory_only": True,
            "execution_gate_changed": False,
        }
    )
    return result


def write_recommended_snippet(
    output_path: str | Path,
    *,
    active_config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    python_command: str | Path | None = None,
) -> Path:
    output = Path(output_path).expanduser().resolve()
    active = (
        Path(active_config_path).expanduser().resolve()
        if active_config_path
        else default_active_config_path()
    )
    if _same_path(output, active):
        raise ValueError("refusing to overwrite the active Codex config; choose a separate snippet path")
    snippet = build_codex_config_snippet(
        repo_root or Path.cwd(),
        python_command=python_command,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snippet, encoding="utf-8")
    return output


def _activation_actions(config_path: Path) -> list[str]:
    return [
        "Preview a guarded append with ms-mcp-config-register; applying it requires the exact fresh plan ID.",
        f"Review and merge the recommended snippet into {config_path}; do not replace unrelated config sections.",
        "Restart Codex so the MCP server and tool allowlist are reloaded.",
        "Call material_studio_live_session_preflight, then use material_studio_live_modeling_request.",
    ]


def _registration_candidates(servers: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for name, value in servers.items():
        if not isinstance(value, dict) or name == SERVER_NAME:
            continue
        command = str(value.get("command") or "")
        args = [str(item) for item in value.get("args", []) or []]
        cwd = str(value.get("cwd") or "")
        searchable = " ".join((name, command, *args, cwd)).lower()
        if any(token in searchable for token in ("material_studio", "materials-studio", "ms_mcp", "run_server.py")):
            candidates.append(
                {
                    "server_name": str(name),
                    "command": command or None,
                    "args": args,
                    "cwd": cwd or None,
                    "legacy_ms_mcp_entrypoint": "ms_mcp.server" in searchable,
                }
            )
    return candidates


def _same_path(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        left_path = str(Path(str(left)).expanduser().resolve())
        right_path = str(Path(str(right)).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    if os.name == "nt":
        return left_path.casefold() == right_path.casefold()
    return left_path == right_path


def _same_server_args(observed: list[Any], expected: list[str]) -> bool:
    if len(observed) != len(expected) or not observed:
        return False
    if not _same_path(observed[0], expected[0]):
        return False
    return all(
        str(left) == str(right)
        for left, right in zip(observed[1:], expected[1:])
    )


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Codex Materials Studio MCP registration without modifying active config."
    )
    parser.add_argument("--config", help="Active Codex config.toml; defaults to CODEX_HOME or ~/.codex.")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Materials Studio MCP repository root.")
    parser.add_argument("--python", dest="python_command", help="Python executable for the MCP entrypoint.")
    parser.add_argument("--output-snippet", help="Write the recommendation to a separate file, never active config.")
    parser.add_argument("--omit-snippet", action="store_true", help="Do not include TOML in JSON output.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless the active config is ready.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _build_parser().parse_args(argv)
    result = diagnose_codex_config(
        config_path=options.config,
        repo_root=options.cwd,
        python_command=options.python_command,
        include_snippet=not options.omit_snippet,
    )
    if options.output_snippet:
        try:
            output = write_recommended_snippet(
                options.output_snippet,
                active_config_path=options.config,
                repo_root=options.cwd,
                python_command=options.python_command,
            )
            result["snippet_output_path"] = str(output)
            result["snippet_output_sha256"] = _file_sha256(output)
        except ValueError as exc:
            result.update({"ok": False, "status": "unsafe_output_path_rejected", "error": str(exc)})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("ok"):
        return 2
    if options.strict and not result.get("config_ready"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
