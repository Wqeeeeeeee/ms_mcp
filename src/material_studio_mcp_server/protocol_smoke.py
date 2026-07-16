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
    "material_studio_live_update_with_patch",
    "material_studio_model_export_view_bundle",
    "material_studio_project_history",
    "material_studio_project_rollback",
    "material_studio_project_reconcile_dopant_metadata",
    "material_studio_gui_status",
    "material_studio_gui_activate",
    "material_studio_gui_snapshot",
    "material_studio_gui_open_structure",
    "material_studio_gui_apply_current_revision",
    "material_studio_gui_record_visual_confirmation",
    "material_studio_gui_prepare_view_replay",
    "material_studio_gui_record_view_replay",
)

COMPACT_RESPONSE_MAX_BYTES = 48_000
COMPACT_RESPONSE_TARGET_BYTES = 45_000
EXPECTED_CAPABILITIES_COMPACT_SCHEMA = "material_studio_capabilities_compact_v2"
EXPECTED_LIVE_COMPACT_SCHEMA = "material_studio_live_compact_v2"
EXPECTED_SESSION_PREFLIGHT_COMPACT_SCHEMA = (
    "material_studio_live_session_preflight_compact_v1"
)

_ANNOTATION_EXPECTATIONS: dict[str, dict[str, bool]] = {
    "material_studio_live_capabilities": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_session_preflight": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_project_status": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_gui_status": {"readOnlyHint": True, "destructiveHint": False},
    "material_studio_live_modeling_request": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_live_update_with_patch": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_gui_apply_current_revision": {"readOnlyHint": False, "destructiveHint": True},
    "material_studio_gui_record_visual_confirmation": {"readOnlyHint": False, "destructiveHint": False},
    "material_studio_gui_record_view_replay": {"readOnlyHint": False, "destructiveHint": False},
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
            "export_view_audit",
            "views",
            "confirm_metadata_reconciliation",
            "working_dir",
            "response_mode",
            "visual_confirmation",
            "view_replay_confirmation",
        },
        "required": {"user_request"},
    },
    "material_studio_live_project_status": {
        "properties": {"project_id", "include_gui_status", "working_dir", "response_mode"},
        "required": set(),
    },
    "material_studio_live_update_with_patch": {
        "properties": {
            "project_id",
            "base_revision",
            "patch",
            "confirm_metadata_reconciliation",
            "execution_mode",
            "working_dir",
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
            "execution_mode",
            "open_in_gui",
            "take_snapshot",
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
    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["MATERIAL_STUDIO_MCP_WORKSPACE"] = str(workspace_path)
    env["MATERIAL_STUDIO_WORKSPACE"] = str(workspace_path)
    server = StdioServerParameters(
        command=str(command),
        args=[str(item) for item in args],
        cwd=str(root),
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
        "workspace": str(workspace_path),
        "list_only": bool(list_only),
        "errors": [],
        "warnings": [],
    }
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
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


async def _run_preview_calls(
    session: ClientSession,
    *,
    workspace: Path,
    timeout: timedelta,
) -> dict[str, Any]:
    nonce = uuid.uuid4().hex[:10]
    request = f"Build silicon crystal and prepare preview for MCP protocol acceptance {nonce}."
    calls: dict[str, Any] = {"ok": False, "errors": []}
    try:
        capabilities = await _call_tool(
            session,
            "material_studio_live_capabilities",
            {"response_mode": "compact"},
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
        response_sizes_bytes = {
            "capabilities": len(json.dumps(capabilities, ensure_ascii=False).encode("utf-8")),
            "preflight": len(json.dumps(preflight, ensure_ascii=False).encode("utf-8")),
            "create": len(json.dumps(created, ensure_ascii=False).encode("utf-8")),
            "status": len(json.dumps(status, ensure_ascii=False).encode("utf-8")),
            "prepare_view_replay": len(
                json.dumps(prepared_replay, ensure_ascii=False).encode("utf-8")
            ),
            "resumed_preflight": len(
                json.dumps(resumed_preflight, ensure_ascii=False).encode("utf-8")
            ),
            "view_bundle": len(json.dumps(exported, ensure_ascii=False).encode("utf-8")),
            "history": len(json.dumps(history, ensure_ascii=False).encode("utf-8")),
        }
        validation_errors: list[str] = []
        if capabilities.get("ok") is not True:
            validation_errors.append("capabilities_call_not_ok")
        if capabilities.get("response_mode") != "compact":
            validation_errors.append("capabilities_response_not_compact")
        if capabilities.get("response_schema") != EXPECTED_CAPABILITIES_COMPACT_SCHEMA:
            validation_errors.append("capabilities_compact_schema_mismatch")
        if preflight.get("ok") is not True:
            validation_errors.append("preflight_call_not_ok")
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
        if status.get("ok") is not True or status.get("project_id") != project_id:
            validation_errors.append("status_call_did_not_resolve_created_project")
        if status.get("response_mode") != "compact":
            validation_errors.append("status_response_not_compact")
        if status.get("response_schema") != EXPECTED_LIVE_COMPACT_SCHEMA:
            validation_errors.append("status_compact_schema_mismatch")
        if prepared_replay.get("ok") is not True:
            validation_errors.append("view_replay_prepare_not_ok")
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
        oversized = sorted(
            name
            for name in (
                "capabilities",
                "create",
                "status",
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
                "template_id": (created.get("nl_plan") or {}).get("template_id"),
                "artifact_status": created.get("structure_artifact_validation_status"),
                "planned_structure": str(planned_structure),
                "planned_structure_exists": planned_structure.exists(),
                "gui_opened": created.get("gui_open") is not None,
                "view_names": view_names,
                "view_bundle_manifest_path": exported.get("view_bundle_manifest_path"),
                "view_bundle_row_counts": exported.get("view_bundle_row_counts"),
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
