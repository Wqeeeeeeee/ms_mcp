"""Protocol-level stdio acceptance checks for the Materials Studio MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from .managed_runtime import managed_runtime_launch_cwd
from .roundtrip import ROUNDTRIP_AUDIT_PROFILE, ROUNDTRIP_AUDIT_SCHEMA_VERSION

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


REQUIRED_PROTOCOL_TOOLS: tuple[str, ...] = (
    "material_studio_get_status",
    "material_studio_live_capabilities",
    "material_studio_live_session_preflight",
    "material_studio_live_modeling_request",
    "material_studio_live_project_status",
    "material_studio_live_watchdog_status",
    "material_studio_live_update_with_patch",
    "material_studio_castep_relax_current",
    "material_studio_castep_run_current",
    "material_studio_model_export_view_bundle",
    "material_studio_project_history",
    "material_studio_project_rollback",
    "material_studio_project_reconcile_dopant_metadata",
    "material_studio_gui_status",
    "material_studio_gui_activate",
    "material_studio_gui_snapshot",
    "material_studio_gui_open_structure",
    "material_studio_gui_apply_current_revision",
    "material_studio_gui_fit_to_view",
    "material_studio_gui_record_visual_confirmation",
    "material_studio_gui_prepare_view_replay",
    "material_studio_gui_execute_view_replay",
    "material_studio_gui_record_view_replay",
)

COMPACT_RESPONSE_MAX_BYTES = 48_000
COMPACT_RESPONSE_TARGET_BYTES = 45_000
EXPECTED_CAPABILITIES_COMPACT_SCHEMA = "material_studio_capabilities_compact_v2"
EXPECTED_LIVE_COMPACT_SCHEMA = "material_studio_live_compact_v2"
EXPECTED_NORMALITY_DECISION_SCHEMA = "material_studio_normality_decision_v1"
EXPECTED_LIVE_WATCHDOG_SCHEMA = "material_studio_live_watchdog_status_v1"
EXPECTED_LIVE_WATCHDOG_MAX_BYTES = 12_000
EXPECTED_SESSION_PREFLIGHT_COMPACT_SCHEMA = (
    "material_studio_live_session_preflight_compact_v1"
)
EXPECTED_RUNTIME_GUARDED_TOOL_COUNT = 28
EXPECTED_RUNTIME_DEPLOYMENT_SCHEMA = (
    "material_studio_mcp_runtime_deployment_binding_v1"
)
EXPECTED_RUNTIME_CODEX_CONFIG_SCHEMA = (
    "material_studio_mcp_runtime_codex_config_status_v1"
)

_ANNOTATION_EXPECTATIONS: dict[str, dict[str, bool]] = {
    "material_studio_live_capabilities": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_session_preflight": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_project_status": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_watchdog_status": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_gui_status": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_modeling_request": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_live_update_with_patch": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_castep_relax_current": {
        "readOnlyHint": False,
        "destructiveHint": True,
    },
    "material_studio_castep_run_current": {
        "readOnlyHint": False,
        "destructiveHint": True,
    },
    "material_studio_gui_apply_current_revision": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_gui_fit_to_view": {"readOnlyHint": False, "destructiveHint": False},
    "material_studio_gui_record_visual_confirmation": {"readOnlyHint": False, "destructiveHint": False},
    "material_studio_gui_record_view_replay": {"readOnlyHint": False, "destructiveHint": False},
    "material_studio_gui_execute_view_replay": {"readOnlyHint": False, "destructiveHint": False},
    "material_studio_project_reconcile_dopant_metadata": {
        "readOnlyHint": False,
        "destructiveHint": True,
    },
    "material_studio_run_script": {"readOnlyHint": False, "destructiveHint": True},
}

_SCHEMA_EXPECTATIONS: dict[str, dict[str, set[str]]] = {
    "material_studio_live_capabilities": {
        "properties": {"include_status", "response_mode"},
        "required": set(),
    },
    "material_studio_live_modeling_request": {
        "properties": {
            "user_request",
            "execution_mode",
            "open_in_gui",
            "take_snapshot",
            "fit_to_view_after_open",
            "prepare_view_replay_after_open",
            "export_view_audit",
            "views",
            "confirm_metadata_reconciliation",
            "working_dir",
            "response_mode",
            "visual_confirmation",
            "view_replay_confirmation",
            "verify_ms_roundtrip",
        },
        "required": {"user_request"},
    },
    "material_studio_live_project_status": {
        "properties": {"project_id", "include_gui_status", "working_dir", "response_mode"},
        "required": set(),
    },
    "material_studio_live_watchdog_status": {
        "properties": {
            "project_id",
            "expected_revision",
            "previous_state_fingerprint",
            "include_gui_status",
            "working_dir",
        },
        "required": set(),
    },
    "material_studio_live_update_with_patch": {
        "properties": {
            "project_id",
            "base_revision",
            "patch",
            "confirm_metadata_reconciliation",
            "execution_mode",
            "fit_to_view_after_open",
            "prepare_view_replay_after_open",
            "working_dir",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_castep_relax_current": {
        "properties": {
            "project_id",
            "execution_mode",
            "cutoff_energy_ev",
            "kpoint_separation",
            "kpoints",
            "dipole_correction",
            "max_iterations",
            "displacement_convergence_angstrom",
            "energy_convergence_ev_per_atom",
            "force_convergence_ev_per_angstrom",
            "cell_optimization",
            "optimization_algorithm",
            "open_in_gui",
            "take_snapshot",
            "export_view_audit",
            "views",
            "fit_to_view_after_open",
            "prepare_view_replay_after_open",
            "working_dir",
            "timeout_seconds",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_castep_run_current": {
        "properties": {
            "project_id",
            "execution_mode",
            "task",
            "quality",
            "functional",
            "cutoff_energy_ev",
            "kpoint_separation",
            "kpoints",
            "properties_kpoint_separation",
            "band_structure_energy_max_ev",
            "band_structure_extra_bands",
            "band_structure_energy_tolerance_ev",
            "dos_energy_max_ev",
            "dos_extra_bands",
            "dos_energy_tolerance_ev",
            "dos_smearing_width_ev",
            "dos_integration_method",
            "dipole_correction",
            "open_in_gui",
            "take_snapshot",
            "export_view_audit",
            "views",
            "fit_to_view_after_open",
            "prepare_view_replay_after_open",
            "working_dir",
            "timeout_seconds",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_model_modify_with_patch": {
        "properties": {
            "project_id",
            "base_revision",
            "patch",
            "confirm_metadata_reconciliation",
            "execution_mode",
            "working_dir",
        },
        "required": {"project_id", "base_revision", "patch"},
    },
    "material_studio_model_export_view_bundle": {
        "properties": {"project_id", "views", "working_dir", "response_mode"},
        "required": set(),
    },
    "material_studio_project_reconcile_dopant_metadata": {
        "properties": {
            "project_id",
            "base_revision",
            "confirm_metadata_reconciliation",
            "execution_mode",
            "open_in_gui",
            "take_snapshot",
            "views",
            "working_dir",
        },
        "required": set(),
    },
    "material_studio_gui_apply_current_revision": {
        "properties": {
            "project_id",
            "expected_revision",
            "execution_mode",
            "open_in_gui",
            "take_snapshot",
            "fit_to_view_after_open",
            "prepare_view_replay_after_open",
            "export_view_audit",
            "views",
            "working_dir",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_gui_record_visual_confirmation": {
        "properties": {
            "project_id",
            "revision",
            "source",
            "model_visible",
            "expected_window_handle",
            "expected_window_title",
            "working_dir",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_gui_prepare_view_replay": {
        "properties": {
            "project_id",
            "revision",
            "views",
            "runtime_ui_evidence",
            "runtime_accessibility_evidence",
            "working_dir",
            "response_mode",
        },
        "required": set(),
    },
    "material_studio_gui_execute_view_replay": {
        "properties": {
            "project_id",
            "revision",
            "view_name",
            "execution_mode",
            "working_dir",
        },
        "required": set(),
    },
    "material_studio_gui_record_view_replay": {
        "properties": {
            "view_name",
            "project_id",
            "revision",
            "source",
            "model_visible",
            "camera_matches_manifest",
            "reviewed_copy_script_evidence",
            "expected_window_handle",
            "expected_window_title",
            "native_command_id",
            "accessibility_command_uses",
            "key_sequence",
            "reset_before_key_sequence",
            "rotation_increment_degrees",
            "modifier_keys",
            "keyboard_stages",
            "rotation_increment_restored_degrees",
            "movement_options_command_id",
            "movement_angle_control_id",
            "movement_screen_factor_control_id",
            "movement_screen_factor",
            "movement_dialog_closed",
            "miller_plane_evidence",
            "working_dir",
            "response_mode",
        },
        "required": {"view_name"},
    },
}

_NESTED_FORBID_SCHEMA_EXPECTATIONS: dict[str, set[str]] = {
    "material_studio_gui_prepare_view_replay": {
        "runtime_ui_evidence",
        "runtime_accessibility_evidence",
    },
    "material_studio_gui_record_view_replay": {
        "reviewed_copy_script_evidence",
    },
}


async def run_protocol_acceptance(
    *,
    command: str,
    args: Sequence[str],
    cwd: str | Path,
    workspace: str | Path,
    config_path: str | Path | None = None,
    timeout_seconds: float = 60.0,
    list_only: bool = False,
) -> dict[str, Any]:
    """Start the stdio server and verify discovery plus preview-safe calls."""

    root = Path(cwd).expanduser().resolve()
    launch_root = managed_runtime_launch_cwd(root)
    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["MATERIAL_STUDIO_MCP_WORKSPACE"] = str(workspace_path)
    env["MATERIAL_STUDIO_WORKSPACE"] = str(workspace_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    server = StdioServerParameters(
        command=str(command),
        args=[str(item) for item in args],
        cwd=str(launch_root),
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    timeout = timedelta(seconds=float(timeout_seconds))
    summary: dict[str, Any] = {
        "ok": False,
        "transport": "stdio",
        "command": str(command),
        "args": [str(item) for item in args],
        "cwd": str(root),
        "launch_cwd": str(launch_root),
        "workspace": str(workspace_path),
        "list_only": bool(list_only),
        "errors": [],
        "warnings": [],
    }
    with tempfile.TemporaryFile(
        mode="w+",
        encoding="utf-8",
        errors="replace",
    ) as errlog:
        try:
            async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                ) as session:
                    initialized = await session.initialize()
                    tools = await _list_all_tools(session)
                    tool_map = {tool.name: tool for tool in tools}
                    discovery = _validate_tool_discovery(tool_map)
                    summary.update(
                        {
                            "protocol_version": initialized.protocolVersion,
                            "server_info": initialized.serverInfo.model_dump(mode="json"),
                            "server_capabilities": initialized.capabilities.model_dump(mode="json"),
                            "tool_count": len(tools),
                            "tool_names": sorted(tool_map),
                            "discovery": discovery,
                        }
                    )
                    if not list_only and discovery["ok"]:
                        summary["calls"] = await _run_preview_calls(
                            session,
                            repository_root=root,
                            workspace=workspace_path,
                            timeout=timeout,
                        )
        except Exception as exc:
            summary["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            errlog.seek(0)
            stderr_text = errlog.read()
            summary["server_stderr_tail"] = stderr_text[-4000:]

    if config_path is not None:
        summary["config_audit"] = audit_codex_config(config_path)
    discovery_ok = bool((summary.get("discovery") or {}).get("ok"))
    calls_ok = list_only or bool((summary.get("calls") or {}).get("ok"))
    summary["ok"] = discovery_ok and calls_ok and not summary["errors"]
    return summary


async def _list_all_tools(session: ClientSession) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor:
            return tools


def _validate_tool_discovery(tool_map: dict[str, Tool]) -> dict[str, Any]:
    missing_tools = sorted(set(REQUIRED_PROTOCOL_TOOLS) - set(tool_map))
    annotation_errors: list[dict[str, Any]] = []
    for tool_name, expected in _ANNOTATION_EXPECTATIONS.items():
        tool = tool_map.get(tool_name)
        if tool is None:
            annotation_errors.append({"tool": tool_name, "error": "tool_missing"})
            continue
        annotations = tool.annotations.model_dump(mode="json") if tool.annotations else {}
        for field, expected_value in expected.items():
            if annotations.get(field) is not expected_value:
                annotation_errors.append(
                    {
                        "tool": tool_name,
                        "field": field,
                        "expected": expected_value,
                        "actual": annotations.get(field),
                    }
                )

    schema_errors: list[dict[str, Any]] = []
    for tool_name, expected in _SCHEMA_EXPECTATIONS.items():
        tool = tool_map.get(tool_name)
        if tool is None:
            continue
        schema = tool.inputSchema or {}
        properties = set((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or [])
        missing_properties = sorted(expected["properties"] - properties)
        missing_required = sorted(expected["required"] - required)
        unexpected_required = sorted(required - expected["required"])
        if missing_properties or missing_required or unexpected_required:
            schema_errors.append(
                {
                    "tool": tool_name,
                    "missing_properties": missing_properties,
                    "missing_required": missing_required,
                    "unexpected_required": unexpected_required,
                }
            )
        for property_name in sorted(_NESTED_FORBID_SCHEMA_EXPECTATIONS.get(tool_name, set())):
            property_schema = (schema.get("properties") or {}).get(property_name)
            resolved_schema = _resolve_object_schema(property_schema, schema)
            if resolved_schema is None or resolved_schema.get("additionalProperties") is not False:
                schema_errors.append(
                    {
                        "tool": tool_name,
                        "property": property_name,
                        "error": "nested_schema_must_forbid_additional_properties",
                        "actual_additional_properties": (
                            resolved_schema.get("additionalProperties")
                            if isinstance(resolved_schema, dict)
                            else None
                        ),
                    }
                )

    return {
        "ok": not missing_tools and not annotation_errors and not schema_errors,
        "required_tool_count": len(REQUIRED_PROTOCOL_TOOLS),
        "missing_tools": missing_tools,
        "annotation_errors": annotation_errors,
        "schema_errors": schema_errors,
    }


def _resolve_object_schema(value: Any, root_schema: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a nullable/ref property schema to its object definition."""

    if not isinstance(value, dict):
        return None
    candidates = [value]
    for union_key in ("anyOf", "oneOf"):
        union = value.get(union_key)
        if isinstance(union, list):
            candidates.extend(item for item in union if isinstance(item, dict))
    for candidate in candidates:
        resolved = candidate
        reference = candidate.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            resolved_value: Any = root_schema
            for part in reference[2:].split("/"):
                if not isinstance(resolved_value, dict):
                    resolved_value = None
                    break
                resolved_value = resolved_value.get(part.replace("~1", "/").replace("~0", "~"))
            if isinstance(resolved_value, dict):
                resolved = resolved_value
        if resolved.get("type") == "object" or "properties" in resolved:
            return resolved
    return None


def _protocol_roundtrip_preview_acceptance(
    *,
    created: dict[str, Any],
    status: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    """Validate one side-effect-free round-trip plan across MCP responses."""

    errors: list[str] = []
    created_audit = created.get("materials_studio_roundtrip_audit")
    status_audit = status.get("materials_studio_roundtrip_audit")
    project_id = created.get("project_id")
    revision = created.get("revision")

    if created.get("materials_studio_roundtrip_audit_requested") is not True:
        errors.append("roundtrip_create_request_marker_missing")
    if status.get("materials_studio_roundtrip_audit_requested") is not True:
        errors.append("roundtrip_status_request_marker_missing")
    if not isinstance(created_audit, dict):
        errors.append("roundtrip_create_plan_missing")
        created_audit = {}
    if not isinstance(status_audit, dict):
        errors.append("roundtrip_status_plan_missing")
        status_audit = {}

    expected_fields = {
        "schema_version": ROUNDTRIP_AUDIT_SCHEMA_VERSION,
        "profile": ROUNDTRIP_AUDIT_PROFILE,
        "project_id": project_id,
        "revision": revision,
        "execution_mode": "preview",
        "required": True,
        "applicable": True,
        "status": "deferred_until_materialized",
        "gui_probe_planned": False,
        "runner_call_planned": False,
    }
    for label, audit in (("create", created_audit), ("status", status_audit)):
        for field, expected in expected_fields.items():
            observed = audit.get(field)
            if observed != expected or type(observed) is not type(expected):
                errors.append(f"roundtrip_{label}_{field}_mismatch")
        spec_sha = audit.get("spec_sha256")
        if not (
            isinstance(spec_sha, str)
            and len(spec_sha) == 64
            and all(character in "0123456789abcdef" for character in spec_sha)
        ):
            errors.append(f"roundtrip_{label}_spec_sha256_invalid")
        if audit.get("side_effects") != {
            "files_written": False,
            "runner_called": False,
            "gui_input_performed": False,
        }:
            errors.append(f"roundtrip_{label}_side_effects_invalid")
        if list(audit.get("errors") or []):
            errors.append(f"roundtrip_{label}_plan_errors_present")
        for forbidden_field in (
            "receipt_path",
            "comparison",
            "runner_identity",
            "runner_created_files",
            "runner_success",
        ):
            if audit.get(forbidden_field) is not None:
                errors.append(
                    f"roundtrip_{label}_preview_field_unexpected:{forbidden_field}"
                )

    if created_audit != status_audit:
        errors.append("roundtrip_create_status_plan_mismatch")

    run_root_value = created_audit.get("run_root")
    output_value = created_audit.get("output_path")
    run_root = (
        Path(str(run_root_value)).expanduser().resolve()
        if isinstance(run_root_value, str) and run_root_value.strip()
        else None
    )
    output_path = (
        Path(str(output_value)).expanduser().resolve()
        if isinstance(output_value, str) and output_value.strip()
        else None
    )
    valid_revision = isinstance(revision, int) and not isinstance(revision, bool)
    expected_revision_root = None
    if valid_revision:
        expected_revision_root = (
            workspace
            / str(project_id or "")
            / "outputs"
            / f"r{revision:03d}"
        ).resolve()
    expected_run_root = (
        expected_revision_root / "ms_roundtrip" / "preview"
        if expected_revision_root is not None
        else None
    )
    if run_root is None:
        errors.append("roundtrip_preview_run_root_missing")
    elif run_root != expected_run_root:
        errors.append("roundtrip_preview_run_root_mismatch")
    if output_path is None:
        errors.append("roundtrip_preview_output_path_missing")
    elif run_root is None or output_path != run_root / "roundtrip_output.cif":
        errors.append("roundtrip_preview_output_path_mismatch")
    if run_root is not None and run_root.exists():
        errors.append("roundtrip_preview_run_root_created")
    if output_path is not None and output_path.exists():
        errors.append("roundtrip_preview_output_created")

    return {
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "requested": created.get("materials_studio_roundtrip_audit_requested")
        is True,
        "project_id": project_id,
        "revision": revision,
        "execution_mode": created_audit.get("execution_mode"),
        "audit_status": created_audit.get("status"),
        "spec_sha256": created_audit.get("spec_sha256"),
        "create_status_consistent": created_audit == status_audit,
        "gui_probe_planned": created_audit.get("gui_probe_planned"),
        "runner_call_planned": created_audit.get("runner_call_planned"),
        "side_effects": created_audit.get("side_effects"),
        "run_root": str(run_root) if run_root is not None else None,
        "run_root_exists": run_root.exists() if run_root is not None else None,
        "output_path": str(output_path) if output_path is not None else None,
        "output_exists": output_path.exists() if output_path is not None else None,
    }


def _protocol_roundtrip_execution_handoff_acceptance(
    *,
    created: dict[str, Any],
    status: dict[str, Any],
    workspace: Path,
    expected_views: Sequence[str],
) -> dict[str, Any]:
    """Validate the exact, confirmation-gated execute handoff after preview."""

    errors: list[str] = []
    project_id = created.get("project_id")
    revision = created.get("revision")
    workspace_path = workspace.expanduser().resolve()
    allowed_payload_fields = {
        "project_id",
        "expected_revision",
        "execution_mode",
        "open_in_gui",
        "take_snapshot",
        "fit_to_view_after_open",
        "prepare_view_replay_after_open",
        "verify_ms_roundtrip",
        "export_view_audit",
        "views",
        "working_dir",
        "timeout_seconds",
        "response_mode",
    }

    def extract(label: str, response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = response.get("next_action_plan")
        if not isinstance(plan, dict):
            errors.append(f"roundtrip_{label}_next_action_plan_missing")
            return {}, {}
        action = plan
        if plan.get("recommended_tool") != "material_studio_gui_apply_current_revision":
            deferred = plan.get("deferred_hotload_action")
            if (
                isinstance(deferred, dict)
                and deferred.get("recommended_tool")
                == "material_studio_gui_apply_current_revision"
            ):
                action = deferred
            else:
                errors.append(f"roundtrip_{label}_apply_handoff_missing")
                return {}, {}
        payload = action.get("payload_hint")
        if not isinstance(payload, dict):
            errors.append(f"roundtrip_{label}_apply_payload_missing")
            return action, {}
        return action, payload

    extracted = {
        label: extract(label, response)
        for label, response in (("create", created), ("status", status))
    }
    expected_payload_fields: dict[str, Any] = {
        "project_id": project_id,
        "expected_revision": revision,
        "execution_mode": "execute",
        "open_in_gui": True,
        "take_snapshot": True,
        "verify_ms_roundtrip": True,
        "export_view_audit": True,
        "views": list(expected_views),
        "working_dir": str(workspace_path),
    }
    for label, (action, payload) in extracted.items():
        if not action or not payload:
            continue
        if action.get("action_id") != "execute_and_hotload_current_revision":
            errors.append(f"roundtrip_{label}_apply_action_id_mismatch")
        if action.get("needs_user_confirmation") is not True:
            errors.append(f"roundtrip_{label}_apply_confirmation_gate_missing")
        if action.get("safe_to_call_without_confirmation") is not False:
            errors.append(f"roundtrip_{label}_apply_safe_flag_invalid")
        unexpected = sorted(set(payload) - allowed_payload_fields)
        if unexpected:
            errors.append(
                f"roundtrip_{label}_apply_payload_fields_unexpected:{','.join(unexpected)}"
            )
        for field, expected in expected_payload_fields.items():
            observed = payload.get(field)
            if observed != expected or type(observed) is not type(expected):
                errors.append(f"roundtrip_{label}_apply_{field}_mismatch")

    create_payload = extracted["create"][1]
    status_payload = extracted["status"][1]
    if create_payload and status_payload and create_payload != status_payload:
        errors.append("roundtrip_create_status_apply_payload_mismatch")

    return {
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "project_id": project_id,
        "revision": revision,
        "recommended_tool": "material_studio_gui_apply_current_revision",
        "needs_user_confirmation": extracted["create"][0].get(
            "needs_user_confirmation"
        ),
        "safe_to_call_without_confirmation": extracted["create"][0].get(
            "safe_to_call_without_confirmation"
        ),
        "create_status_payload_consistent": bool(
            create_payload and create_payload == status_payload
        ),
        "payload": create_payload,
    }


async def _run_preview_calls(
    session: ClientSession,
    *,
    repository_root: Path,
    workspace: Path,
    timeout: timedelta,
) -> dict[str, Any]:
    nonce = uuid.uuid4().hex[:10]
    request = (
        "构建硅晶体并准备预览，导出正视、俯视和等轴测视角参数并检查模型是否正常。"
        f"协议验收标识 {nonce}。"
    )
    calls: dict[str, Any] = {"ok": False, "errors": []}
    try:
        capabilities = await _call_tool(
            session,
            "material_studio_live_capabilities",
            {"response_mode": "compact", "include_status": True},
            timeout,
        )
        preflight = await _call_tool(
            session,
            "material_studio_live_session_preflight",
            {
                "working_dir": str(workspace),
                "include_latest_project": False,
                "include_gui_status": False,
            },
            timeout,
        )
        created = await _call_tool(
            session,
            "material_studio_live_modeling_request",
            {
                "user_request": request,
                "execution_mode": "preview",
                "open_in_gui": False,
                "take_snapshot": False,
                "export_view_audit": True,
                "views": ["front", "top", "isometric"],
                "working_dir": str(workspace),
                "response_mode": "compact",
                "verify_ms_roundtrip": True,
            },
            timeout,
        )
        project_id = str(created.get("project_id") or "")
        status = await _call_tool(
            session,
            "material_studio_live_project_status",
            {
                "project_id": project_id,
                "include_gui_status": False,
                "working_dir": str(workspace),
                "response_mode": "compact",
            },
            timeout,
        )
        watchdog = await _call_tool(
            session,
            "material_studio_live_watchdog_status",
            {
                "project_id": project_id,
                "expected_revision": created.get("revision"),
                "include_gui_status": False,
                "working_dir": str(workspace),
            },
            timeout,
        )
        watchdog_repeat = await _call_tool(
            session,
            "material_studio_live_watchdog_status",
            {
                "project_id": project_id,
                "expected_revision": created.get("revision"),
                "previous_state_fingerprint": watchdog.get(
                    "state_fingerprint"
                ),
                "include_gui_status": False,
                "working_dir": str(workspace),
            },
            timeout,
        )
        relaxation_preview = await _call_tool(
            session,
            "material_studio_castep_relax_current",
            {
                "project_id": project_id,
                "expected_revision": created.get("revision"),
                "execution_mode": "preview",
                "open_in_gui": False,
                "take_snapshot": False,
                "export_view_audit": False,
                "working_dir": str(workspace),
                "response_mode": "compact",
            },
            timeout,
        )
        electronic_preview = await _call_tool(
            session,
            "material_studio_castep_run_current",
            {
                "project_id": project_id,
                "expected_revision": created.get("revision"),
                "execution_mode": "preview",
                "task": "Energy",
                "open_in_gui": False,
                "take_snapshot": False,
                "export_view_audit": False,
                "working_dir": str(workspace),
                "response_mode": "compact",
            },
            timeout,
        )
        prepared_replay = await _call_tool(
            session,
            "material_studio_gui_prepare_view_replay",
            {
                "project_id": project_id,
                "revision": created.get("revision"),
                "views": ["front", "top", "isometric"],
                "working_dir": str(workspace),
                "response_mode": "compact",
            },
            timeout,
        )
        execution_preview = await _call_tool(
            session,
            "material_studio_gui_execute_view_replay",
            {
                "project_id": project_id,
                "revision": created.get("revision"),
                "execution_mode": "preview",
                "working_dir": str(workspace),
            },
            timeout,
        )
        resumed_preflight = await _call_tool(
            session,
            "material_studio_live_session_preflight",
            {
                "working_dir": str(workspace),
                "include_latest_project": True,
                "include_gui_status": False,
            },
            timeout,
        )
        exported = await _call_tool(
            session,
            "material_studio_model_export_view_bundle",
            {
                "project_id": project_id,
                "views": ["front", "top", "isometric"],
                "include_gui_snapshot": False,
                "working_dir": str(workspace),
                "response_mode": "compact",
            },
            timeout,
        )
        history = await _call_tool(
            session,
            "material_studio_project_history",
            {"project_id": project_id, "working_dir": str(workspace)},
            timeout,
        )

        planned_structure = Path(str((created.get("planned_outputs") or {}).get("structure") or ""))
        view_names = list((created.get("live_summary") or {}).get("view_names") or [])
        nl_plan = created.get("nl_plan") if isinstance(created.get("nl_plan"), dict) else {}
        normality_decision = (
            created.get("normality_decision")
            if isinstance(created.get("normality_decision"), dict)
            else {}
        )
        normality_gate = (
            created.get("normality_gate")
            if isinstance(created.get("normality_gate"), dict)
            else {}
        )
        status_normality_decision = (
            status.get("normality_decision")
            if isinstance(status.get("normality_decision"), dict)
            else {}
        )
        roundtrip_acceptance = _protocol_roundtrip_preview_acceptance(
            created=created,
            status=status,
            workspace=workspace,
        )
        roundtrip_handoff_acceptance = (
            _protocol_roundtrip_execution_handoff_acceptance(
                created=created,
                status=status,
                workspace=workspace,
                expected_views=("front", "top", "isometric"),
            )
        )
        response_sizes_bytes = {
            "capabilities": len(json.dumps(capabilities, ensure_ascii=False).encode("utf-8")),
            "preflight": len(json.dumps(preflight, ensure_ascii=False).encode("utf-8")),
            "create": len(json.dumps(created, ensure_ascii=False).encode("utf-8")),
            "status": len(json.dumps(status, ensure_ascii=False).encode("utf-8")),
            "watchdog": len(
                json.dumps(watchdog, ensure_ascii=False).encode("utf-8")
            ),
            "watchdog_repeat": len(
                json.dumps(watchdog_repeat, ensure_ascii=False).encode("utf-8")
            ),
            "castep_relaxation_preview": len(
                json.dumps(relaxation_preview, ensure_ascii=False).encode("utf-8")
            ),
            "castep_electronic_preview": len(
                json.dumps(electronic_preview, ensure_ascii=False).encode("utf-8")
            ),
            "prepare_view_replay": len(
                json.dumps(prepared_replay, ensure_ascii=False).encode("utf-8")
            ),
            "execute_view_replay_preview": len(
                json.dumps(execution_preview, ensure_ascii=False).encode("utf-8")
            ),
            "resumed_preflight": len(
                json.dumps(resumed_preflight, ensure_ascii=False).encode("utf-8")
            ),
            "view_bundle": len(json.dumps(exported, ensure_ascii=False).encode("utf-8")),
            "history": len(json.dumps(history, ensure_ascii=False).encode("utf-8")),
        }
        validation_errors: list[str] = []
        validation_errors.extend(roundtrip_acceptance["errors"])
        validation_errors.extend(roundtrip_handoff_acceptance["errors"])
        if capabilities.get("ok") is not True:
            validation_errors.append("capabilities_call_not_ok")
        if capabilities.get("response_mode") != "compact":
            validation_errors.append("capabilities_response_not_compact")
        if capabilities.get("response_schema") != EXPECTED_CAPABILITIES_COMPACT_SCHEMA:
            validation_errors.append("capabilities_compact_schema_mismatch")
        runtime_contract = capabilities.get("runtime_provenance_contract")
        if not isinstance(runtime_contract, dict):
            validation_errors.append("capabilities_runtime_provenance_contract_missing")
            runtime_contract = {}
        if runtime_contract.get(
            "live_preflight_source_drift_blocks_continuation"
        ) is not True:
            validation_errors.append("capabilities_runtime_source_drift_gate_missing")
        if runtime_contract.get("deployment_binding_schema") != (
            EXPECTED_RUNTIME_DEPLOYMENT_SCHEMA
        ):
            validation_errors.append("capabilities_runtime_deployment_contract_missing")
        if runtime_contract.get(
            "deployment_binding_reports_source_checkout_and_git_identity"
        ) is not True:
            validation_errors.append("capabilities_runtime_git_identity_missing")
        if runtime_contract.get("direct_tool_runtime_guard") is not True:
            validation_errors.append("capabilities_direct_runtime_guard_missing")
        if runtime_contract.get("direct_tool_runtime_guard_fails_closed") is not True:
            validation_errors.append(
                "capabilities_direct_runtime_guard_fail_closed_missing"
            )
        if (
            runtime_contract.get("guarded_tool_count")
            != EXPECTED_RUNTIME_GUARDED_TOOL_COUNT
        ):
            validation_errors.append(
                "capabilities_direct_runtime_guard_tool_count_mismatch"
            )
        if runtime_contract.get("restart_is_never_automatic") is not True:
            validation_errors.append("capabilities_runtime_restart_policy_missing")
        config_status_contract = capabilities.get("codex_config_status_contract")
        if not isinstance(config_status_contract, dict):
            validation_errors.append("capabilities_codex_config_contract_missing")
            config_status_contract = {}
        if config_status_contract.get("schema") != (
            EXPECTED_RUNTIME_CODEX_CONFIG_SCHEMA
        ):
            validation_errors.append("capabilities_codex_config_schema_mismatch")
        if config_status_contract.get("read_only") is not True:
            validation_errors.append("capabilities_codex_config_read_only_missing")
        if config_status_contract.get("advisory_only") is not True:
            validation_errors.append("capabilities_codex_config_advisory_missing")
        runtime_provenance = capabilities.get("runtime_provenance")
        if not isinstance(runtime_provenance, dict):
            validation_errors.append("capabilities_runtime_provenance_missing")
            runtime_provenance = {}
        if runtime_provenance.get("source_current") is not True:
            validation_errors.append("capabilities_runtime_source_not_current")
        if runtime_provenance.get("restart_required") is not False:
            validation_errors.append("capabilities_runtime_restart_unexpected")
        initial_source = runtime_provenance.get("source_snapshot_at_start")
        current_source = runtime_provenance.get("source_snapshot_current")
        if not isinstance(initial_source, dict) or not isinstance(current_source, dict):
            validation_errors.append("capabilities_runtime_source_snapshots_missing")
        elif initial_source.get("sha256") != current_source.get("sha256"):
            validation_errors.append("capabilities_runtime_source_hash_mismatch")
        diagnostic_capability = capabilities.get("diagnostics")
        if not isinstance(diagnostic_capability, dict):
            validation_errors.append("capabilities_diagnostics_missing")
            diagnostic_capability = {}
        diagnostic_focus_ids = diagnostic_capability.get(
            "diagnostic_focus_ids"
        )
        if not isinstance(diagnostic_focus_ids, list):
            diagnostic_focus_ids = []
        if "castep_electronic_results" not in diagnostic_focus_ids:
            validation_errors.append(
                "capabilities_castep_result_diagnostic_focus_missing"
            )
        if "castep_convergence_series" not in diagnostic_focus_ids:
            validation_errors.append(
                "capabilities_castep_convergence_diagnostic_focus_missing"
            )
        calculation_preview_handoff = capabilities.get(
            "castep_calculation_preview_handoff"
        )
        if not isinstance(calculation_preview_handoff, dict):
            validation_errors.append(
                "capabilities_castep_calculation_handoff_missing"
            )
            calculation_preview_handoff = {}
        handoff_task_tools = calculation_preview_handoff.get(
            "task_execution_tools"
        )
        if not isinstance(handoff_task_tools, dict):
            validation_errors.append(
                "capabilities_castep_calculation_handoff_tools_missing"
            )
            handoff_task_tools = {}
        if calculation_preview_handoff.get(
            "preview_actions_are_workspace_bound"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_calculation_handoff_workspace_unbound"
            )
        if calculation_preview_handoff.get(
            "preview_actions_are_revision_bound"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_calculation_handoff_revision_unbound"
            )
        if calculation_preview_handoff.get(
            "execute_requires_explicit_confirmation"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_calculation_handoff_execute_gate_missing"
            )
        if handoff_task_tools.get("Energy") != (
            "material_studio_castep_run_current"
        ):
            validation_errors.append(
                "capabilities_castep_calculation_handoff_energy_tool_mismatch"
            )
        if handoff_task_tools.get("GeometryOptimization") != (
            "material_studio_castep_relax_current"
        ):
            validation_errors.append(
                "capabilities_castep_calculation_handoff_relax_tool_mismatch"
            )
        castep_relaxation_capability = capabilities.get(
            "castep_geometry_optimization"
        )
        if not isinstance(castep_relaxation_capability, dict):
            validation_errors.append("capabilities_castep_relaxation_missing")
            castep_relaxation_capability = {}
        if castep_relaxation_capability.get("tool") != (
            "material_studio_castep_relax_current"
        ):
            validation_errors.append("capabilities_castep_relaxation_tool_mismatch")
        if castep_relaxation_capability.get("default_execution_mode") != "preview":
            validation_errors.append("capabilities_castep_relaxation_default_not_preview")
        if castep_relaxation_capability.get("promotion_requires_converged") is not True:
            validation_errors.append("capabilities_castep_relaxation_convergence_gate_missing")
        castep_electronic_capability = capabilities.get(
            "castep_electronic_calculation"
        )
        if not isinstance(castep_electronic_capability, dict):
            validation_errors.append("capabilities_castep_electronic_missing")
            castep_electronic_capability = {}
        if castep_electronic_capability.get("tool") != (
            "material_studio_castep_run_current"
        ):
            validation_errors.append(
                "capabilities_castep_electronic_tool_mismatch"
            )
        if castep_electronic_capability.get("default_execution_mode") != "preview":
            validation_errors.append(
                "capabilities_castep_electronic_default_not_preview"
            )
        if castep_electronic_capability.get(
            "successful_result_creates_metadata_only_revision"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_electronic_revision_contract_missing"
            )
        if castep_electronic_capability.get(
            "scientific_convergence_verified"
        ) is not False:
            validation_errors.append(
                "capabilities_castep_electronic_convergence_boundary_missing"
            )
        if castep_electronic_capability.get(
            "scientific_band_gap_verified"
        ) is not False:
            validation_errors.append(
                "capabilities_castep_electronic_band_gap_boundary_missing"
            )
        sampled_band_edge_capability = castep_electronic_capability.get(
            "sampled_band_edge_audit"
        )
        if not isinstance(sampled_band_edge_capability, dict):
            validation_errors.append(
                "capabilities_castep_sampled_band_edge_audit_missing"
            )
            sampled_band_edge_capability = {}
        if sampled_band_edge_capability.get("source") != (
            "hash_bound_native_castep_bands"
        ):
            validation_errors.append(
                "capabilities_castep_sampled_band_edge_source_missing"
            )
        if sampled_band_edge_capability.get(
            "scientific_band_gap_verified"
        ) is not False:
            validation_errors.append(
                "capabilities_castep_sampled_band_edge_overclaim"
            )
        electronic_result_assessment = castep_electronic_capability.get(
            "result_assessment"
        )
        if not isinstance(electronic_result_assessment, dict):
            validation_errors.append(
                "capabilities_castep_result_assessment_missing"
            )
            electronic_result_assessment = {}
        if electronic_result_assessment.get(
            "requires_receipt_binding"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_result_assessment_binding_missing"
            )
        if electronic_result_assessment.get(
            "structure_normality_blocked_by_result_review"
        ) is not False:
            validation_errors.append(
                "capabilities_castep_result_assessment_normality_boundary_missing"
            )
        if electronic_result_assessment.get(
            "recommended_payload_execution_mode"
        ) != "preview":
            validation_errors.append(
                "capabilities_castep_result_assessment_preview_boundary_missing"
            )
        if electronic_result_assessment.get(
            "execute_requires_explicit_confirmation"
        ) is not True:
            validation_errors.append(
                "capabilities_castep_result_assessment_execute_gate_missing"
            )
        convergence_audit = castep_electronic_capability.get(
            "convergence_audit"
        )
        if not isinstance(convergence_audit, dict):
            validation_errors.append(
                "capabilities_castep_convergence_audit_missing"
            )
            convergence_audit = {}
        if convergence_audit.get("schema") != (
            "material_studio_castep_convergence_audit_v1"
        ):
            validation_errors.append(
                "capabilities_castep_convergence_schema_mismatch"
            )
        if convergence_audit.get("source") != (
            "immutable_verified_electronic_result_revisions"
        ):
            validation_errors.append(
                "capabilities_castep_convergence_binding_missing"
            )
        if convergence_audit.get("axes_compared_separately") is not True:
            validation_errors.append(
                "capabilities_castep_convergence_axis_isolation_missing"
            )
        if convergence_audit.get("scientific_convergence_verified") is not False:
            validation_errors.append(
                "capabilities_castep_convergence_overclaim"
            )
        if convergence_audit.get("recommended_rerun_execution_mode") != "preview":
            validation_errors.append(
                "capabilities_castep_convergence_preview_boundary_missing"
            )
        if convergence_audit.get("execute_requires_explicit_confirmation") is not True:
            validation_errors.append(
                "capabilities_castep_convergence_execute_gate_missing"
            )
        if castep_electronic_capability.get(
            "band_edge_diagnostic_csv"
        ) != "semiconductor_castep_band_edges.csv":
            validation_errors.append(
                "capabilities_castep_band_edge_csv_missing"
            )
        if castep_electronic_capability.get(
            "numeric_curve_data_exported"
        ) != "conditional_on_native_bands":
            validation_errors.append(
                "capabilities_castep_electronic_curve_boundary_missing"
            )
        electronic_exports = castep_electronic_capability.get(
            "numeric_curve_export_by_task"
        )
        if not isinstance(electronic_exports, dict):
            validation_errors.append(
                "capabilities_castep_electronic_native_export_contract_missing"
            )
            electronic_exports = {}
        if electronic_exports.get("BandStructure") != (
            "native_castep_band_eigenvalues"
        ):
            validation_errors.append(
                "capabilities_castep_electronic_band_export_contract_missing"
            )
        if electronic_exports.get("DensityOfStates") != (
            "mcp_gaussian_total_dos_from_native_bands_when_smearing_is_explicit"
        ):
            validation_errors.append(
                "capabilities_castep_electronic_dos_export_contract_missing"
            )
        if electronic_exports.get("ProjectedDensityOfStates") != (
            "not_exported_until_pdos_weights_format_is_verified"
        ):
            validation_errors.append(
                "capabilities_castep_electronic_pdos_boundary_missing"
            )
        replay_policy = capabilities.get("view_replay_automation_policy")
        if not isinstance(replay_policy, dict):
            validation_errors.append("capabilities_replay_policy_missing")
            replay_policy = {}
        if replay_policy.get("local_uia_miller_plane_supported") is not True:
            validation_errors.append("capabilities_static_miller_support_missing")
        if (
            replay_policy.get("local_uia_exact_collinear_direction_supported")
            is not True
        ):
            validation_errors.append(
                "capabilities_static_collinear_direction_support_missing"
            )
        if replay_policy.get("local_uia_non_collinear_direction_supported") is not False:
            validation_errors.append(
                "capabilities_non_collinear_direction_boundary_missing"
            )
        if replay_policy.get("post_hotload_prepare_parameter") != (
            "prepare_view_replay_after_open"
        ):
            validation_errors.append(
                "capabilities_post_hotload_replay_prepare_parameter_missing"
            )
        if replay_policy.get(
            "post_hotload_prepare_runs_after_gui_report_lock_release"
        ) is not True:
            validation_errors.append(
                "capabilities_post_hotload_replay_prepare_lock_boundary_missing"
            )
        if replay_policy.get(
            "post_hotload_prepare_current_revision_checks"
        ) != ["before_prepare", "after_prepare_return"]:
            validation_errors.append(
                "capabilities_post_hotload_replay_prepare_revision_recheck_missing"
            )
        if replay_policy.get(
            "post_hotload_superseded_manifest_policy"
        ) != "preserve_as_historical_without_current_continuation":
            validation_errors.append(
                "capabilities_post_hotload_replay_supersession_policy_missing"
            )
        if replay_policy.get(
            "post_hotload_visual_action_never_replaces_modeling_action"
        ) is not True:
            validation_errors.append(
                "capabilities_post_hotload_replay_action_tracks_missing"
            )
        if replay_policy.get(
            "post_hotload_direct_continuations_are_workspace_bound"
        ) is not True:
            validation_errors.append(
                "capabilities_post_hotload_replay_workspace_binding_missing"
            )
        if replay_policy.get(
            "post_hotload_prepare_rewrites_modeling_report"
        ) is not False:
            validation_errors.append(
                "capabilities_post_hotload_replay_prepare_report_boundary_missing"
            )
        if replay_policy.get(
            "post_hotload_prepare_failure_preserves_hotload"
        ) is not True:
            validation_errors.append(
                "capabilities_post_hotload_replay_prepare_partial_success_missing"
            )
        trusted_clean_view_policy = replay_policy.get(
            "trusted_clean_view_normality_evidence"
        )
        if not isinstance(trusted_clean_view_policy, dict):
            validation_errors.append(
                "capabilities_trusted_clean_view_policy_missing"
            )
            trusted_clean_view_policy = {}
        if trusted_clean_view_policy.get("summary_field") != (
            "trusted_clean_view_replay"
        ):
            validation_errors.append(
                "capabilities_trusted_clean_view_summary_field_mismatch"
            )
        if trusted_clean_view_policy.get(
            "requires_diagnostic_and_replay_view_selection_match"
        ) is not True:
            validation_errors.append(
                "capabilities_trusted_clean_view_selection_gate_missing"
            )
        if trusted_clean_view_policy.get(
            "requires_all_supported_views_confirmed"
        ) is not True:
            validation_errors.append(
                "capabilities_trusted_clean_view_completion_gate_missing"
            )
        if trusted_clean_view_policy.get(
            "requires_evidence_integrity_status"
        ) != "verified":
            validation_errors.append(
                "capabilities_trusted_clean_view_integrity_gate_missing"
            )
        if trusted_clean_view_policy.get(
            "requires_event_journal_consistency_status"
        ) != "consistent":
            validation_errors.append(
                "capabilities_trusted_clean_view_journal_gate_missing"
            )
        if trusted_clean_view_policy.get(
            "may_resolve_calculation_readiness_failures"
        ) is not False:
            validation_errors.append(
                "capabilities_trusted_clean_view_calculation_boundary_missing"
            )
        replay_progress_contract = capabilities.get(
            "view_replay_progress_contract"
        )
        if not isinstance(replay_progress_contract, dict):
            validation_errors.append(
                "capabilities_view_replay_progress_contract_missing"
            )
            replay_progress_contract = {}
        if replay_progress_contract.get("schema_version") != (
            "material_studio_gui_view_replay_progress_v1"
        ):
            validation_errors.append(
                "capabilities_view_replay_progress_schema_mismatch"
            )
        if replay_progress_contract.get("status_field") != (
            "material_studio_live_project_status.gui_view_replay.progress"
        ):
            validation_errors.append(
                "capabilities_view_replay_progress_field_mismatch"
            )
        if replay_progress_contract.get("decision_field") != "trusted_complete":
            validation_errors.append(
                "capabilities_view_replay_progress_decision_field_mismatch"
            )
        if replay_progress_contract.get("fail_closed") is not True:
            validation_errors.append(
                "capabilities_view_replay_progress_fail_closed_missing"
            )
        normality_decision_contract = capabilities.get(
            "normality_decision_contract"
        )
        if not isinstance(normality_decision_contract, dict):
            validation_errors.append(
                "capabilities_normality_decision_contract_missing"
            )
            normality_decision_contract = {}
        if normality_decision_contract.get("schema_version") != (
            EXPECTED_NORMALITY_DECISION_SCHEMA
        ):
            validation_errors.append(
                "capabilities_normality_decision_schema_mismatch"
            )
        if normality_decision_contract.get("authoritative_source") != (
            "normality_gate"
        ):
            validation_errors.append(
                "capabilities_normality_decision_source_mismatch"
            )
        if normality_decision_contract.get("project_revision_bound") is not True:
            validation_errors.append(
                "capabilities_normality_decision_binding_missing"
            )
        if normality_decision_contract.get("watchdog_prefers_decision") is not True:
            validation_errors.append(
                "capabilities_normality_decision_watchdog_preference_missing"
            )
        watchdog_contract = capabilities.get("live_watchdog_contract")
        if not isinstance(watchdog_contract, dict):
            validation_errors.append("capabilities_live_watchdog_contract_missing")
            watchdog_contract = {}
        if capabilities.get("live_watchdog_tool") != (
            "material_studio_live_watchdog_status"
        ):
            validation_errors.append("capabilities_live_watchdog_tool_mismatch")
        if watchdog_contract.get("schema_version") != (
            EXPECTED_LIVE_WATCHDOG_SCHEMA
        ):
            validation_errors.append("capabilities_live_watchdog_schema_mismatch")
        if watchdog_contract.get("max_response_bytes") != (
            EXPECTED_LIVE_WATCHDOG_MAX_BYTES
        ):
            validation_errors.append("capabilities_live_watchdog_budget_mismatch")
        if watchdog_contract.get("default_poll_interval_seconds") != 1200:
            validation_errors.append("capabilities_live_watchdog_interval_mismatch")
        if watchdog_contract.get("automatic_polling_allowed") is not True:
            validation_errors.append("capabilities_live_watchdog_poll_gate_missing")
        if watchdog_contract.get("automatic_non_poll_action_allowed") is not False:
            validation_errors.append(
                "capabilities_live_watchdog_non_poll_boundary_missing"
            )
        runner_status = capabilities.get("runner_status")
        if not isinstance(runner_status, dict):
            validation_errors.append("capabilities_compact_runner_status_missing")
            runner_status = {}
        gui_status = capabilities.get("gui_status")
        if not isinstance(gui_status, dict):
            validation_errors.append("capabilities_compact_gui_status_missing")
            gui_status = {}
        replay_runtime = capabilities.get("view_replay_runtime_availability")
        if not isinstance(replay_runtime, dict):
            validation_errors.append("capabilities_replay_runtime_missing")
            replay_runtime = {}
        if replay_runtime.get("observed") is not True:
            validation_errors.append("capabilities_replay_runtime_not_observed")
        if replay_runtime.get("transactional_miller_implemented") is not True:
            validation_errors.append(
                "capabilities_runtime_miller_implementation_missing"
            )
        if replay_runtime.get("exact_collinear_direction_implemented") is not True:
            validation_errors.append(
                "capabilities_runtime_collinear_implementation_missing"
            )
        if replay_runtime.get("non_collinear_direction_implemented") is not False:
            validation_errors.append(
                "capabilities_runtime_non_collinear_boundary_missing"
            )
        if replay_runtime.get("execution_requires_project_recipe_preflight") is not True:
            validation_errors.append(
                "capabilities_runtime_project_recipe_gate_missing"
            )
        if replay_runtime.get("execute_tool") != (
            "material_studio_gui_execute_view_replay"
        ):
            validation_errors.append("capabilities_runtime_execute_tool_mismatch")
        if preflight.get("ok") is not True:
            validation_errors.append("preflight_call_not_ok")
        preflight_runtime = preflight.get("runtime_provenance")
        if not isinstance(preflight_runtime, dict):
            validation_errors.append("preflight_runtime_provenance_missing")
            preflight_runtime = {}
        if preflight_runtime.get("runtime_instance_id") != runtime_provenance.get(
            "runtime_instance_id"
        ):
            validation_errors.append("preflight_runtime_instance_mismatch")
        preflight_deployment = preflight.get("runtime_deployment")
        if not isinstance(preflight_deployment, dict):
            validation_errors.append("preflight_runtime_deployment_missing")
            preflight_deployment = {}
        if preflight_deployment.get("schema") != EXPECTED_RUNTIME_DEPLOYMENT_SCHEMA:
            validation_errors.append("preflight_runtime_deployment_schema_mismatch")
        if preflight_deployment.get("repository_root") != str(repository_root):
            validation_errors.append("preflight_runtime_repository_mismatch")
        if preflight_deployment.get("entrypoint_binding") != (
            "matched_source_run_server"
        ):
            validation_errors.append("preflight_runtime_entrypoint_binding_mismatch")
        if preflight_deployment.get("diagnostic_only") is not True:
            validation_errors.append("preflight_runtime_deployment_not_diagnostic")
        if preflight_deployment.get("materials_studio_process_started") is not False:
            validation_errors.append("preflight_runtime_deployment_started_gui")
        preflight_config_status = preflight.get("codex_config_status")
        if not isinstance(preflight_config_status, dict):
            validation_errors.append("preflight_codex_config_status_missing")
            preflight_config_status = {}
        if preflight_config_status.get("schema") != (
            EXPECTED_RUNTIME_CODEX_CONFIG_SCHEMA
        ):
            validation_errors.append("preflight_codex_config_status_schema_mismatch")
        if preflight_config_status.get("read_only") is not True:
            validation_errors.append("preflight_codex_config_status_not_read_only")
        if preflight_config_status.get("active_config_modified") is not False:
            validation_errors.append("preflight_codex_config_status_modified_config")
        if preflight_config_status.get("advisory_only") is not True:
            validation_errors.append("preflight_codex_config_status_not_advisory")
        if preflight_config_status.get("execution_gate_changed") is not False:
            validation_errors.append("preflight_codex_config_changed_execution_gate")
        preflight_readiness = preflight.get("mcp_client_readiness")
        if not isinstance(preflight_readiness, dict):
            validation_errors.append("preflight_mcp_client_readiness_missing")
            preflight_readiness = {}
        if preflight_readiness.get("server_source_current") is not True:
            validation_errors.append("preflight_runtime_source_not_current")
        if preflight_readiness.get("server_restart_required") is not False:
            validation_errors.append("preflight_runtime_restart_unexpected")
        preflight_runner = preflight.get("runner_status")
        if not isinstance(preflight_runner, dict):
            validation_errors.append("preflight_runner_status_missing")
            preflight_runner = {}
        if preflight_runner.get("request_workspace_root") != str(workspace):
            validation_errors.append("preflight_runner_request_workspace_mismatch")
        if preflight_runner.get("execution_working_dir_policy") != (
            "explicit_tool_working_dir_overrides_runner_default"
        ):
            validation_errors.append("preflight_runner_working_dir_policy_missing")
        if created.get("ok") is not True:
            validation_errors.append("preview_create_not_ok")
        if created.get("execution_mode") != "preview":
            validation_errors.append("preview_create_execution_mode_changed")
        if created.get("response_mode") != "compact":
            validation_errors.append("preview_create_response_not_compact")
        if created.get("response_schema") != EXPECTED_LIVE_COMPACT_SCHEMA:
            validation_errors.append("preview_create_compact_schema_mismatch")
        if created.get("revision") != 0:
            validation_errors.append("preview_create_revision_not_zero")
        if created.get("structure_artifact_validation_status") != "not_materialized":
            validation_errors.append("preview_artifact_status_not_not_materialized")
        if planned_structure.exists():
            validation_errors.append("preview_unexpectedly_materialized_structure")
        if created.get("gui_open") is not None:
            validation_errors.append("preview_unexpectedly_opened_gui")
        if created.get("user_request") != request:
            validation_errors.append("preview_cjk_request_echo_mismatch")
        if not any("\u4e00" <= character <= "\u9fff" for character in request):
            validation_errors.append("preview_cjk_request_fixture_missing_cjk")
        if nl_plan.get("kind") != "spec":
            validation_errors.append("preview_cjk_nl_plan_kind_mismatch")
        if nl_plan.get("template_id") != "silicon_diamond":
            validation_errors.append("preview_cjk_template_mismatch")
        if created.get("diagnostic_export_requested") is not True:
            validation_errors.append("preview_cjk_diagnostic_export_not_requested")
        if created.get("normality_check_requested") is not True:
            validation_errors.append("preview_cjk_normality_check_not_requested")
        if normality_decision.get("schema_version") != (
            EXPECTED_NORMALITY_DECISION_SCHEMA
        ):
            validation_errors.append("preview_normality_decision_schema_mismatch")
        if normality_decision.get("authoritative_source") != "normality_gate":
            validation_errors.append("preview_normality_decision_source_mismatch")
        if normality_decision.get("project_id") != project_id:
            validation_errors.append("preview_normality_decision_project_mismatch")
        if normality_decision.get("revision") != created.get("revision"):
            validation_errors.append("preview_normality_decision_revision_mismatch")
        if normality_decision.get("binding_verified") is not True:
            validation_errors.append("preview_normality_decision_binding_unverified")
        decision_consistency = normality_decision.get("consistency")
        if not isinstance(decision_consistency, dict):
            validation_errors.append("preview_normality_decision_consistency_missing")
            decision_consistency = {}
        if decision_consistency.get("ok") is not True:
            validation_errors.append("preview_normality_decision_inconsistent")
        for decision_key in (
            "status",
            "primary_reason",
            "can_claim_model_normal",
            "can_claim_live_gui_normal",
            "ready_for_next_edit",
            "ready_for_calculation",
            "next_action",
        ):
            if normality_decision.get(decision_key) != normality_gate.get(
                decision_key
            ):
                validation_errors.append(
                    f"preview_normality_decision_gate_mismatch:{decision_key}"
                )
        if created.get("normality_decision_primary_reason") != (
            normality_decision.get("primary_reason")
        ):
            validation_errors.append(
                "preview_normality_decision_top_level_reason_mismatch"
            )
        if status_normality_decision.get("schema_version") != (
            EXPECTED_NORMALITY_DECISION_SCHEMA
        ):
            validation_errors.append("status_normality_decision_schema_mismatch")
        if status_normality_decision.get("project_id") != project_id:
            validation_errors.append("status_normality_decision_project_mismatch")
        if status_normality_decision.get("revision") != created.get("revision"):
            validation_errors.append("status_normality_decision_revision_mismatch")
        if status_normality_decision.get("status") != normality_decision.get(
            "status"
        ):
            validation_errors.append("status_normality_decision_status_mismatch")
        if created.get("runner_invoked") not in {None, False}:
            validation_errors.append("preview_cjk_unexpected_runner_invocation")
        if created.get("structure_materialization_started") not in {None, False}:
            validation_errors.append("preview_cjk_unexpected_materialization")
        if created.get("gui_input_started") not in {None, False}:
            validation_errors.append("preview_cjk_unexpected_gui_input")
        if status.get("ok") is not True or status.get("project_id") != project_id:
            validation_errors.append("status_call_did_not_resolve_created_project")
        if status.get("response_mode") != "compact":
            validation_errors.append("status_response_not_compact")
        if status.get("response_schema") != EXPECTED_LIVE_COMPACT_SCHEMA:
            validation_errors.append("status_compact_schema_mismatch")
        if watchdog.get("ok") is not True:
            validation_errors.append("watchdog_status_not_ok")
        if watchdog.get("schema_version") != EXPECTED_LIVE_WATCHDOG_SCHEMA:
            validation_errors.append("watchdog_status_schema_mismatch")
        if watchdog.get("project_id") != project_id:
            validation_errors.append("watchdog_status_project_mismatch")
        if watchdog.get("revision") != created.get("revision"):
            validation_errors.append("watchdog_status_revision_mismatch")
        if watchdog.get("revision_matches_expected") is not True:
            validation_errors.append("watchdog_status_revision_binding_failed")
        if watchdog.get("read_only") is not True:
            validation_errors.append("watchdog_status_not_read_only")
        watchdog_normality = watchdog.get("normality")
        if not isinstance(watchdog_normality, dict):
            validation_errors.append("watchdog_normality_receipt_missing")
            watchdog_normality = {}
        if watchdog_normality.get("schema_version") != (
            EXPECTED_NORMALITY_DECISION_SCHEMA
        ):
            validation_errors.append("watchdog_normality_decision_schema_mismatch")
        if watchdog_normality.get("uses_authoritative_decision") is not True:
            validation_errors.append("watchdog_normality_decision_not_preferred")
        if watchdog_normality.get("binding_verified") is not True:
            validation_errors.append("watchdog_normality_decision_binding_unverified")
        if watchdog_normality.get("detail_ref") != (
            "material_studio_live_project_status.normality_decision"
        ):
            validation_errors.append("watchdog_normality_decision_ref_mismatch")
        watchdog_safety = watchdog.get("safety")
        if not isinstance(watchdog_safety, dict):
            validation_errors.append("watchdog_status_safety_missing")
            watchdog_safety = {}
        if watchdog_safety.get("automatic_poll_allowed") is not True:
            validation_errors.append("watchdog_status_poll_gate_missing")
        if watchdog_safety.get("automatic_non_poll_action_allowed") is not False:
            validation_errors.append("watchdog_status_non_poll_boundary_missing")
        if watchdog_safety.get("automatic_execution_retry_allowed") is not False:
            validation_errors.append("watchdog_status_execution_retry_boundary_missing")
        watchdog_poll = watchdog.get("poll_action")
        if not isinstance(watchdog_poll, dict):
            validation_errors.append("watchdog_status_poll_action_missing")
            watchdog_poll = {}
        if watchdog_poll.get("automatic_call_allowed") is not True:
            validation_errors.append("watchdog_status_poll_action_not_callable")
        watchdog_fingerprint = watchdog.get("state_fingerprint")
        if not isinstance(watchdog_fingerprint, str) or len(watchdog_fingerprint) != 64:
            validation_errors.append("watchdog_status_fingerprint_missing")
        if watchdog_repeat.get("state_fingerprint") != watchdog_fingerprint:
            validation_errors.append("watchdog_status_fingerprint_not_stable")
        if watchdog_repeat.get("changed_since_previous") is not False:
            validation_errors.append("watchdog_status_repeat_reported_change")
        watchdog_compaction = watchdog.get("response_compaction")
        if not isinstance(watchdog_compaction, dict):
            validation_errors.append("watchdog_status_compaction_missing")
            watchdog_compaction = {}
        watchdog_bytes = len(
            json.dumps(
                watchdog,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        if watchdog_compaction.get("response_bytes") != watchdog_bytes:
            validation_errors.append("watchdog_status_compaction_size_mismatch")
        if watchdog_bytes >= EXPECTED_LIVE_WATCHDOG_MAX_BYTES:
            validation_errors.append("watchdog_status_size_limit_exceeded")
        relaxation_structure_value = str(
            (relaxation_preview.get("planned_outputs") or {}).get("structure")
            or ""
        )
        relaxation_structure = (
            Path(relaxation_structure_value)
            if relaxation_structure_value
            else None
        )
        if relaxation_preview.get("ok") is not True:
            validation_errors.append("castep_relaxation_preview_not_ok")
        if relaxation_preview.get("workflow") != "castep_geometry_optimization":
            validation_errors.append("castep_relaxation_preview_workflow_mismatch")
        if relaxation_preview.get("execution_mode") != "preview":
            validation_errors.append("castep_relaxation_preview_mode_changed")
        if relaxation_preview.get("execution_started") is not False:
            validation_errors.append("castep_relaxation_preview_started_execution")
        if relaxation_preview.get("expected_revision") != created.get("revision"):
            validation_errors.append("castep_relaxation_preview_revision_unbound")
        if relaxation_preview.get("revision_created") is not False:
            validation_errors.append("castep_relaxation_preview_created_revision")
        if relaxation_structure is None:
            validation_errors.append("castep_relaxation_preview_structure_path_missing")
        elif relaxation_structure.exists():
            validation_errors.append("castep_relaxation_preview_materialized_structure")
        electronic_structure_value = str(
            (electronic_preview.get("planned_outputs") or {}).get("structure")
            or ""
        )
        electronic_structure = (
            Path(electronic_structure_value)
            if electronic_structure_value
            else None
        )
        electronic_run_value = str(electronic_preview.get("run_directory") or "")
        electronic_run_dir = (
            Path(electronic_run_value) if electronic_run_value else None
        )
        if electronic_preview.get("ok") is not True:
            validation_errors.append("castep_electronic_preview_not_ok")
        if electronic_preview.get("workflow") != "castep_electronic_calculation":
            validation_errors.append("castep_electronic_preview_workflow_mismatch")
        if electronic_preview.get("execution_mode") != "preview":
            validation_errors.append("castep_electronic_preview_mode_changed")
        if electronic_preview.get("execution_started") is not False:
            validation_errors.append("castep_electronic_preview_started_execution")
        if electronic_preview.get("expected_revision") != created.get("revision"):
            validation_errors.append("castep_electronic_preview_revision_unbound")
        if electronic_preview.get("revision_created") is not False:
            validation_errors.append("castep_electronic_preview_created_revision")
        if electronic_preview.get("task") != "Energy":
            validation_errors.append("castep_electronic_preview_task_mismatch")
        if electronic_preview.get("numeric_curve_data_exported") is not False:
            validation_errors.append(
                "castep_electronic_preview_claimed_numeric_export"
            )
        if electronic_preview.get("scientific_band_gap_verified") is not False:
            validation_errors.append(
                "castep_electronic_preview_claimed_scientific_band_gap"
            )
        if electronic_preview.get(
            "sampled_band_edge_audit_after_execution"
        ) != "conditional_on_hash_bound_native_bands":
            validation_errors.append(
                "castep_electronic_preview_band_edge_plan_missing"
            )
        if electronic_preview.get("numeric_export_after_execution") is not None:
            validation_errors.append(
                "castep_electronic_energy_preview_numeric_plan_mismatch"
            )
        if electronic_preview.get("pdos_projection_weights_exported") is not False:
            validation_errors.append(
                "castep_electronic_preview_claimed_pdos_weights"
            )
        if electronic_structure is None:
            validation_errors.append("castep_electronic_preview_structure_path_missing")
        elif electronic_structure.exists():
            validation_errors.append("castep_electronic_preview_materialized_structure")
        if electronic_run_dir is None:
            validation_errors.append("castep_electronic_preview_run_path_missing")
        elif electronic_run_dir.exists():
            validation_errors.append("castep_electronic_preview_created_run_directory")
        if prepared_replay.get("ok") is not True:
            validation_errors.append("view_replay_prepare_not_ok")
        if execution_preview.get("ok") is not True:
            validation_errors.append("view_replay_execution_preview_not_ok")
        if execution_preview.get("execution_mode") != "preview":
            validation_errors.append("view_replay_execution_preview_mode_changed")
        if execution_preview.get("gui_input_performed") is not False:
            validation_errors.append("view_replay_execution_preview_sent_gui_input")
        if execution_preview.get("manifest_modified") is not False:
            validation_errors.append("view_replay_execution_preview_modified_manifest")
        if "isometric" not in (
            execution_preview.get("execution_supported_view_names") or []
        ):
            validation_errors.append(
                "view_replay_execution_preview_missing_local_isometric_support"
            )
        if resumed_preflight.get("ok") is not True:
            validation_errors.append("resumed_preflight_not_ok")
        visual_summary = resumed_preflight.get("latest_project_visual_diagnostics")
        if not isinstance(visual_summary, dict):
            validation_errors.append("resumed_preflight_visual_summary_missing")
            visual_summary = {}
        if visual_summary.get("binding_verified") is not True:
            validation_errors.append("resumed_preflight_visual_binding_unverified")
        if visual_summary.get("action_available") is not True:
            validation_errors.append("resumed_preflight_visual_action_unavailable")
        visual_plan = resumed_preflight.get("visual_diagnostics_next_action_plan")
        if not isinstance(visual_plan, dict):
            validation_errors.append("resumed_preflight_visual_plan_missing")
            visual_plan = {}
        if visual_plan.get("project_id") != project_id:
            validation_errors.append("resumed_preflight_visual_project_mismatch")
        if visual_plan.get("revision") != created.get("revision"):
            validation_errors.append("resumed_preflight_visual_revision_mismatch")
        if visual_plan.get("action_scope") != "visual_diagnostics":
            validation_errors.append("resumed_preflight_visual_scope_mismatch")
        sequence = (
            (resumed_preflight.get("coordinated_next_action_plan") or {}).get(
                "recommended_sequence"
            )
            or []
        )
        if not any(step.get("track") == "visual_diagnostics" for step in sequence):
            validation_errors.append("resumed_preflight_visual_track_missing")
        if (
            (resumed_preflight.get("next_action_tracks") or {}).get(
                "recommended_sequence_ref"
            )
            != "coordinated_next_action_plan.recommended_sequence"
        ):
            validation_errors.append("resumed_preflight_sequence_ref_mismatch")
        preflight_compaction = resumed_preflight.get("response_compaction")
        if not isinstance(preflight_compaction, dict):
            validation_errors.append("resumed_preflight_compaction_receipt_missing")
            preflight_compaction = {}
        if (
            preflight_compaction.get("schema")
            != EXPECTED_SESSION_PREFLIGHT_COMPACT_SCHEMA
        ):
            validation_errors.append("resumed_preflight_compaction_schema_mismatch")
        compact_preflight_bytes = len(
            json.dumps(
                resumed_preflight,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if preflight_compaction.get("response_bytes") != compact_preflight_bytes:
            validation_errors.append("resumed_preflight_compaction_size_mismatch")
        if preflight_compaction.get("target_bytes") != COMPACT_RESPONSE_TARGET_BYTES:
            validation_errors.append("resumed_preflight_compaction_target_mismatch")
        if preflight_compaction.get("budget_bytes") != COMPACT_RESPONSE_MAX_BYTES:
            validation_errors.append("resumed_preflight_compaction_budget_mismatch")
        if preflight_compaction.get("target_exceeded") is not False:
            validation_errors.append("resumed_preflight_compaction_target_exceeded")
        if compact_preflight_bytes >= COMPACT_RESPONSE_TARGET_BYTES:
            validation_errors.append("resumed_preflight_target_size_exceeded")
        if exported.get("ok") is not True:
            validation_errors.append("view_bundle_export_not_ok")
        if exported.get("response_mode") != "compact":
            validation_errors.append("view_bundle_response_not_compact")
        if exported.get("response_schema") != EXPECTED_LIVE_COMPACT_SCHEMA:
            validation_errors.append("view_bundle_compact_schema_mismatch")
        if exported.get("view_bundle_files_complete") is not True:
            validation_errors.append("view_bundle_persisted_artifacts_incomplete")
        if exported.get("view_bundle_files_missing_count") != 0:
            validation_errors.append("view_bundle_persisted_artifacts_missing")
        if exported.get("view_bundle_file_index_compacted") is not True:
            validation_errors.append("view_bundle_compact_index_not_marked_compacted")
        if exported.get("view_bundle_file_index_complete_in_response") is not False:
            validation_errors.append(
                "view_bundle_compact_index_incorrectly_marked_complete"
            )
        oversized = sorted(
            name
            for name in (
                "capabilities",
                "create",
                "status",
                "castep_relaxation_preview",
                "castep_electronic_preview",
                "prepare_view_replay",
                "resumed_preflight",
                "view_bundle",
            )
            if response_sizes_bytes[name] >= COMPACT_RESPONSE_MAX_BYTES
        )
        if oversized:
            validation_errors.append("compact_response_size_limit_exceeded:" + ",".join(oversized))
        if int((exported.get("view_bundle_row_counts") or {}).get("view_summary") or 0) != 3:
            validation_errors.append("view_bundle_did_not_export_three_views")
        if set(view_names) != {"front", "top", "isometric"}:
            validation_errors.append("preview_live_summary_view_names_mismatch")
        if len(history.get("history") or []) != 1:
            validation_errors.append("preview_history_count_not_one")

        calls.update(
            {
                "ok": not validation_errors,
                "errors": validation_errors,
                "project_id": project_id,
                "revision": created.get("revision"),
                "execution_mode": created.get("execution_mode"),
                "user_request": request,
                "user_request_echo": created.get("user_request"),
                "cjk_request_echo_preserved": created.get("user_request") == request,
                "nl_plan_kind": nl_plan.get("kind"),
                "template_id": nl_plan.get("template_id"),
                "diagnostic_export_requested": created.get(
                    "diagnostic_export_requested"
                ),
                "normality_check_requested": created.get(
                    "normality_check_requested"
                ),
                "normality_decision": normality_decision,
                "normality_decision_schema": normality_decision.get(
                    "schema_version"
                ),
                "normality_decision_authoritative_source": normality_decision.get(
                    "authoritative_source"
                ),
                "normality_decision_binding_verified": normality_decision.get(
                    "binding_verified"
                ),
                "normality_decision_status": normality_decision.get("status"),
                "normality_decision_primary_reason": normality_decision.get(
                    "primary_reason"
                ),
                "normality_decision_consistency_ok": decision_consistency.get(
                    "ok"
                ),
                "normality_decision_explanation_primary_reason_differs": normality_decision.get(
                    "explanation_primary_reason_differs"
                ),
                "normality_decision_explanation_next_action_differs": normality_decision.get(
                    "explanation_next_action_differs"
                ),
                "artifact_status": created.get("structure_artifact_validation_status"),
                "planned_structure": str(planned_structure),
                "planned_structure_exists": planned_structure.exists(),
                "roundtrip_preview_acceptance": roundtrip_acceptance,
                "roundtrip_preview_acceptance_ok": roundtrip_acceptance["ok"],
                "roundtrip_preview_status": roundtrip_acceptance["audit_status"],
                "roundtrip_preview_create_status_consistent": (
                    roundtrip_acceptance["create_status_consistent"]
                ),
                "roundtrip_preview_runner_call_planned": (
                    roundtrip_acceptance["runner_call_planned"]
                ),
                "roundtrip_preview_gui_probe_planned": (
                    roundtrip_acceptance["gui_probe_planned"]
                ),
                "roundtrip_preview_run_root_exists": (
                    roundtrip_acceptance["run_root_exists"]
                ),
                "roundtrip_execution_handoff_acceptance": (
                    roundtrip_handoff_acceptance
                ),
                "roundtrip_execution_handoff_acceptance_ok": (
                    roundtrip_handoff_acceptance["ok"]
                ),
                "roundtrip_execution_handoff_confirmation_required": (
                    roundtrip_handoff_acceptance["needs_user_confirmation"]
                ),
                "roundtrip_execution_handoff_payload_consistent": (
                    roundtrip_handoff_acceptance[
                        "create_status_payload_consistent"
                    ]
                ),
                "castep_relaxation_preview_status": relaxation_preview.get(
                    "status"
                ),
                "castep_relaxation_preview_execution_started": (
                    relaxation_preview.get("execution_started")
                ),
                "castep_relaxation_preview_structure_exists": (
                    relaxation_structure.exists()
                    if relaxation_structure is not None
                    else None
                ),
                "castep_electronic_preview_status": electronic_preview.get(
                    "status"
                ),
                "castep_electronic_preview_task": electronic_preview.get("task"),
                "castep_electronic_preview_execution_started": (
                    electronic_preview.get("execution_started")
                ),
                "castep_electronic_preview_structure_exists": (
                    electronic_structure.exists()
                    if electronic_structure is not None
                    else None
                ),
                "castep_electronic_preview_run_directory_exists": (
                    electronic_run_dir.exists()
                    if electronic_run_dir is not None
                    else None
                ),
                "castep_electronic_preview_numeric_curve_data_exported": (
                    electronic_preview.get("numeric_curve_data_exported")
                ),
                "castep_electronic_preview_numeric_export_after_execution": (
                    electronic_preview.get("numeric_export_after_execution")
                ),
                "castep_electronic_preview_scientific_band_gap_verified": (
                    electronic_preview.get("scientific_band_gap_verified")
                ),
                "castep_electronic_preview_band_edge_audit_after_execution": (
                    electronic_preview.get(
                        "sampled_band_edge_audit_after_execution"
                    )
                ),
                "gui_opened": created.get("gui_open") is not None,
                "view_names": view_names,
                "view_bundle_manifest_path": exported.get("view_bundle_manifest_path"),
                "view_bundle_row_counts": exported.get("view_bundle_row_counts"),
                "view_bundle_files_complete": exported.get(
                    "view_bundle_files_complete"
                ),
                "view_bundle_files_total_count": exported.get(
                    "view_bundle_files_total_count"
                ),
                "view_bundle_files_existing_count": exported.get(
                    "view_bundle_files_existing_count"
                ),
                "view_bundle_files_missing_count": exported.get(
                    "view_bundle_files_missing_count"
                ),
                "view_bundle_file_index_compacted": exported.get(
                    "view_bundle_file_index_compacted"
                ),
                "view_bundle_file_index_complete_in_response": exported.get(
                    "view_bundle_file_index_complete_in_response"
                ),
                "trusted_clean_view_policy_summary_field": (
                    trusted_clean_view_policy.get("summary_field")
                ),
                "trusted_clean_view_policy_requires_view_selection_match": (
                    trusted_clean_view_policy.get(
                        "requires_diagnostic_and_replay_view_selection_match"
                    )
                ),
                "trusted_clean_view_policy_requires_all_views_confirmed": (
                    trusted_clean_view_policy.get(
                        "requires_all_supported_views_confirmed"
                    )
                ),
                "trusted_clean_view_policy_calculation_independent": (
                    trusted_clean_view_policy.get(
                        "calculation_readiness_remains_independent"
                    )
                ),
                "view_replay_progress_schema": replay_progress_contract.get(
                    "schema_version"
                ),
                "view_replay_progress_status_field": replay_progress_contract.get(
                    "status_field"
                ),
                "view_replay_progress_decision_field": replay_progress_contract.get(
                    "decision_field"
                ),
                "view_replay_progress_fail_closed": replay_progress_contract.get(
                    "fail_closed"
                ),
                "watchdog_contract_schema": watchdog_contract.get(
                    "schema_version"
                ),
                "watchdog_contract_max_response_bytes": watchdog_contract.get(
                    "max_response_bytes"
                ),
                "watchdog_contract_poll_interval_seconds": watchdog_contract.get(
                    "default_poll_interval_seconds"
                ),
                "normality_decision_contract_schema": normality_decision_contract.get(
                    "schema_version"
                ),
                "normality_decision_contract_authoritative_source": normality_decision_contract.get(
                    "authoritative_source"
                ),
                "normality_decision_contract_project_revision_bound": normality_decision_contract.get(
                    "project_revision_bound"
                ),
                "watchdog_status": watchdog.get("status"),
                "watchdog_revision_matches_expected": watchdog.get(
                    "revision_matches_expected"
                ),
                "watchdog_state_fingerprint": watchdog.get(
                    "state_fingerprint"
                ),
                "watchdog_repeat_changed_since_previous": watchdog_repeat.get(
                    "changed_since_previous"
                ),
                "watchdog_automatic_poll_allowed": watchdog_safety.get(
                    "automatic_poll_allowed"
                ),
                "watchdog_automatic_non_poll_action_allowed": (
                    watchdog_safety.get("automatic_non_poll_action_allowed")
                ),
                "watchdog_normality_uses_authoritative_decision": watchdog_normality.get(
                    "uses_authoritative_decision"
                ),
                "watchdog_normality_detail_ref": watchdog_normality.get(
                    "detail_ref"
                ),
                "watchdog_response_compaction": watchdog_compaction,
                "history_count": len(history.get("history") or []),
                "preflight_state": preflight.get("state"),
                "resumed_preflight_state": resumed_preflight.get("state"),
                "visual_diagnostics_action_id": visual_plan.get("action_id"),
                "visual_diagnostics_action_tool": visual_plan.get(
                    "recommended_tool"
                ),
                "visual_diagnostics_binding_verified": visual_summary.get(
                    "binding_verified"
                ),
                "coordinated_action_tracks": [
                    step.get("track") for step in sequence
                ],
                "preflight_response_compaction": preflight_compaction,
                "response_mode": created.get("response_mode"),
                "response_sizes_bytes": response_sizes_bytes,
                "capabilities_runner_status_present": bool(runner_status),
                "runtime_instance_id": runtime_provenance.get(
                    "runtime_instance_id"
                ),
                "runtime_source_current": runtime_provenance.get(
                    "source_current"
                ),
                "runtime_restart_required": runtime_provenance.get(
                    "restart_required"
                ),
                "runtime_source_sha256_at_start": initial_source.get(
                    "sha256"
                )
                if isinstance(initial_source, dict)
                else None,
                "runtime_source_sha256_current": current_source.get("sha256")
                if isinstance(current_source, dict)
                else None,
                "runtime_deployment_schema": preflight_deployment.get(
                    "schema"
                ),
                "runtime_repository_root": preflight_deployment.get(
                    "repository_root"
                ),
                "runtime_entrypoint_binding": preflight_deployment.get(
                    "entrypoint_binding"
                ),
                "runtime_git_head": (
                    preflight_deployment.get("git", {}).get("head_commit")
                    if isinstance(preflight_deployment.get("git"), dict)
                    else None
                ),
                "runtime_git_branch": (
                    preflight_deployment.get("git", {}).get("branch")
                    if isinstance(preflight_deployment.get("git"), dict)
                    else None
                ),
                "codex_config_status_schema": preflight_config_status.get(
                    "schema"
                ),
                "codex_config_status": preflight_config_status.get("status"),
                "codex_config_ready": preflight_config_status.get(
                    "config_ready"
                ),
                "codex_config_scope": preflight_config_status.get(
                    "config_scope"
                ),
                "codex_config_resolution_status": preflight_config_status.get(
                    "config_resolution_status"
                ),
                "codex_config_active_modified": preflight_config_status.get(
                    "active_config_modified"
                ),
                "codex_config_advisory_only": preflight_config_status.get(
                    "advisory_only"
                ),
                "preflight_runner_default_workspace": preflight_runner.get(
                    "default_workspace_root"
                ),
                "preflight_runner_request_workspace": preflight_runner.get(
                    "request_workspace_root"
                ),
                "capabilities_gui_status_present": bool(gui_status),
                "capabilities_replay_runtime_status": replay_runtime.get(
                    "status"
                ),
                "capabilities_replay_runtime_observed": replay_runtime.get(
                    "observed"
                ),
                "capabilities_post_hotload_replay_prepare_parameter": (
                    replay_policy.get("post_hotload_prepare_parameter")
                ),
                "capabilities_post_hotload_replay_prepare_after_report_lock": (
                    replay_policy.get(
                        "post_hotload_prepare_runs_after_gui_report_lock_release"
                    )
                ),
                "capabilities_post_hotload_replay_prepare_revision_checks": (
                    replay_policy.get(
                        "post_hotload_prepare_current_revision_checks"
                    )
                ),
                "capabilities_post_hotload_replay_superseded_manifest_policy": (
                    replay_policy.get(
                        "post_hotload_superseded_manifest_policy"
                    )
                ),
                "capabilities_post_hotload_replay_action_tracks": (
                    replay_policy.get(
                        "post_hotload_visual_action_never_replaces_modeling_action"
                    )
                ),
                "capabilities_post_hotload_replay_prepare_rewrites_report": (
                    replay_policy.get(
                        "post_hotload_prepare_rewrites_modeling_report"
                    )
                ),
                "capabilities_post_hotload_replay_prepare_preserves_hotload": (
                    replay_policy.get(
                        "post_hotload_prepare_failure_preserves_hotload"
                    )
                ),
                "capabilities_castep_relaxation_tool": (
                    castep_relaxation_capability.get("tool")
                ),
                "capabilities_castep_handoff_workspace_bound": (
                    calculation_preview_handoff.get(
                        "preview_actions_are_workspace_bound"
                    )
                ),
                "capabilities_castep_handoff_revision_bound": (
                    calculation_preview_handoff.get(
                        "preview_actions_are_revision_bound"
                    )
                ),
                "capabilities_castep_handoff_execute_confirmation": (
                    calculation_preview_handoff.get(
                        "execute_requires_explicit_confirmation"
                    )
                ),
                "capabilities_castep_handoff_energy_tool": (
                    handoff_task_tools.get("Energy")
                ),
                "capabilities_castep_handoff_relax_tool": (
                    handoff_task_tools.get("GeometryOptimization")
                ),
                "capabilities_castep_electronic_tool": (
                    castep_electronic_capability.get("tool")
                ),
                "capabilities_castep_electronic_numeric_export_mode": (
                    castep_electronic_capability.get(
                        "numeric_curve_data_exported"
                    )
                ),
                "capabilities_castep_sampled_band_edge_source": (
                    sampled_band_edge_capability.get("source")
                ),
                "capabilities_castep_sampled_band_edge_scientific_gap_verified": (
                    sampled_band_edge_capability.get(
                        "scientific_band_gap_verified"
                    )
                ),
                "capabilities_castep_result_assessment_requires_binding": (
                    electronic_result_assessment.get(
                        "requires_receipt_binding"
                    )
                ),
                "capabilities_castep_result_assessment_structure_normality_blocked": (
                    electronic_result_assessment.get(
                        "structure_normality_blocked_by_result_review"
                    )
                ),
                "capabilities_castep_result_assessment_preview_mode": (
                    electronic_result_assessment.get(
                        "recommended_payload_execution_mode"
                    )
                ),
                "capabilities_castep_band_edge_csv": (
                    castep_electronic_capability.get(
                        "band_edge_diagnostic_csv"
                    )
                ),
                "capabilities_castep_result_diagnostic_focus_present": (
                    "castep_electronic_results" in diagnostic_focus_ids
                ),
                "capabilities_castep_convergence_diagnostic_focus_present": (
                    "castep_convergence_series" in diagnostic_focus_ids
                ),
                "capabilities_castep_convergence_schema": (
                    convergence_audit.get("schema")
                ),
                "capabilities_castep_convergence_source": (
                    convergence_audit.get("source")
                ),
                "capabilities_castep_convergence_axes_separate": (
                    convergence_audit.get("axes_compared_separately")
                ),
                "capabilities_castep_convergence_scientific_verified": (
                    convergence_audit.get("scientific_convergence_verified")
                ),
                "capabilities_castep_convergence_preview_mode": (
                    convergence_audit.get("recommended_rerun_execution_mode")
                ),
                "capabilities_castep_convergence_execute_confirmation": (
                    convergence_audit.get("execute_requires_explicit_confirmation")
                ),
                "capabilities_castep_electronic_band_export": (
                    electronic_exports.get("BandStructure")
                ),
                "capabilities_castep_electronic_dos_export": (
                    electronic_exports.get("DensityOfStates")
                ),
                "capabilities_castep_electronic_pdos_export": (
                    electronic_exports.get("ProjectedDensityOfStates")
                ),
                "capabilities_transactional_miller_implemented": (
                    replay_runtime.get("transactional_miller_implemented")
                ),
                "capabilities_transactional_miller_supported": (
                    replay_runtime.get("transactional_miller_supported")
                ),
                "capabilities_exact_collinear_direction_implemented": (
                    replay_runtime.get("exact_collinear_direction_implemented")
                ),
                "capabilities_non_collinear_direction_implemented": (
                    replay_runtime.get("non_collinear_direction_implemented")
                ),
            }
        )
    except Exception as exc:
        calls["errors"].append(f"{type(exc).__name__}: {exc}")
    return calls


async def _call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    timeout: timedelta,
) -> dict[str, Any]:
    result = await session.call_tool(name, arguments=arguments, read_timeout_seconds=timeout)
    if result.isError:
        raise RuntimeError(f"MCP tool {name} returned an error: {_tool_result_text(result)}")
    payload = _tool_result_payload(result)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool {name} returned no JSON object payload.")
    return payload


def _tool_result_payload(result: CallToolResult) -> dict[str, Any] | None:
    structured = result.structuredContent
    if isinstance(structured, dict):
        if set(structured) == {"result"} and isinstance(structured.get("result"), dict):
            return structured["result"]
        return structured
    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _tool_result_text(result: CallToolResult) -> str:
    parts = [str(getattr(item, "text", "")) for item in result.content]
    return " ".join(part for part in parts if part).strip()


def audit_codex_config(path: str | Path) -> dict[str, Any]:
    """Report whether a Codex TOML config enables the live acceptance tools."""

    config_path = Path(path).expanduser().resolve()
    result: dict[str, Any] = {
        "path": str(config_path),
        "exists": config_path.exists(),
        "ok": False,
        "missing_enabled_tools": [],
        "unexpected_dangerous_enabled_tools": [],
        "run_script_explicitly_disabled": False,
    }
    if not config_path.exists():
        result["error"] = "config_not_found"
        return result
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        server = ((payload.get("mcp_servers") or {}).get("materials_studio") or {})
        enabled = {str(item) for item in server.get("enabled_tools", []) or []}
        disabled = {str(item) for item in server.get("disabled_tools", []) or []}
    except Exception as exc:
        result["error"] = f"config_parse_failed: {exc}"
        return result
    missing = sorted(set(REQUIRED_PROTOCOL_TOOLS) - enabled)
    dangerous_enabled = sorted({"material_studio_run_script"} & enabled)
    result.update(
        {
            "ok": not missing and not dangerous_enabled and "material_studio_run_script" in disabled,
            "server_enabled": bool(server.get("enabled", True)),
            "command": server.get("command"),
            "args": server.get("args") or [],
            "enabled_tool_count": len(enabled),
            "disabled_tools": sorted(disabled),
            "missing_enabled_tools": missing,
            "unexpected_dangerous_enabled_tools": dangerous_enabled,
            "run_script_explicitly_disabled": "material_studio_run_script" in disabled,
        }
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run protocol-level stdio acceptance for Materials Studio MCP.")
    parser.add_argument("--command", default=sys.executable, help="Python or MCP server executable.")
    parser.add_argument("--server-arg", action="append", default=[], help="Argument passed to the server process.")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Server working directory.")
    parser.add_argument("--workspace", help="Isolated structured workspace. A temporary folder is used when omitted.")
    parser.add_argument("--config", help="Optional Codex config.toml to audit without modifying it.")
    parser.add_argument("--strict-config", action="store_true", help="Fail when the optional config audit reports drift.")
    parser.add_argument("--list-only", action="store_true", help="Only initialize and validate tools/list.")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--output", help="Optional JSON summary output path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    options = parser.parse_args(argv)
    cwd = Path(options.cwd).expanduser().resolve()
    server_args = list(options.server_arg)
    if not server_args:
        server_args = [str(cwd / "run_server.py")]

    temporary: tempfile.TemporaryDirectory | None = None
    if options.workspace:
        workspace = Path(options.workspace).expanduser().resolve()
    else:
        temporary = tempfile.TemporaryDirectory(prefix="ms_mcp_protocol_smoke_")
        workspace = Path(temporary.name)
    try:
        summary = asyncio.run(
            run_protocol_acceptance(
                command=options.command,
                args=server_args,
                cwd=cwd,
                workspace=workspace,
                config_path=options.config,
                timeout_seconds=options.timeout_seconds,
                list_only=options.list_only,
            )
        )
        if options.strict_config and not (summary.get("config_audit") or {}).get("ok"):
            summary["ok"] = False
            summary.setdefault("errors", []).append("codex_config_audit_failed")
        encoded = json.dumps(summary, indent=2, ensure_ascii=False)
        print(encoded)
        if options.output:
            output_path = Path(options.output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(encoded + "\n", encoding="utf-8")
        return 0 if summary.get("ok") else 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
