from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from material_studio_mcp_server.protocol_smoke import (
    COMPACT_RESPONSE_MAX_BYTES,
    REQUIRED_PROTOCOL_TOOLS,
    audit_codex_config,
    run_protocol_acceptance,
)


def test_stdio_protocol_acceptance_lists_and_calls_live_semiconductor_tools(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    workspace = tmp_path / "protocol_workspace"

    result = asyncio.run(
        run_protocol_acceptance(
            command=sys.executable,
            args=[str(root / "run_server.py")],
            cwd=root,
            workspace=workspace,
            config_path=root / ".codex" / "config.toml.example",
            timeout_seconds=60,
        )
    )

    assert result["ok"] is True
    assert result["transport"] == "stdio"
    assert result["protocol_version"]
    assert result["tool_count"] >= len(REQUIRED_PROTOCOL_TOOLS)
    assert result["discovery"] == {
        "ok": True,
        "required_tool_count": len(REQUIRED_PROTOCOL_TOOLS),
        "missing_tools": [],
        "annotation_errors": [],
        "schema_errors": [],
    }
    calls = result["calls"]
    assert calls["ok"] is True
    assert calls["template_id"] == "silicon_diamond"
    assert calls["execution_mode"] == "preview"
    assert calls["response_mode"] == "compact"
    assert calls["artifact_status"] == "not_materialized"
    assert calls["planned_structure_exists"] is False
    assert calls["gui_opened"] is False
    assert calls["capabilities_runner_status_present"] is True
    assert calls["capabilities_gui_status_present"] is True
    assert calls["capabilities_replay_runtime_status"] in {
        "transactional_miller_available",
        "standard_and_isometric_only",
        "unavailable",
    }
    assert calls["capabilities_replay_runtime_observed"] is True
    assert calls["capabilities_transactional_miller_implemented"] is True
    assert calls["capabilities_exact_collinear_direction_implemented"] is True
    assert calls["capabilities_non_collinear_direction_implemented"] is False
    assert calls["view_names"] == ["front", "top", "isometric"]
    assert calls["view_bundle_row_counts"]["view_summary"] == 3
    assert calls["view_bundle_row_counts"]["view_projections"] == 24
    assert calls["view_bundle_row_counts"]["structure_artifact_validation"] == 1
    assert calls["view_bundle_files_complete"] is True
    assert calls["view_bundle_files_existing_count"] == calls[
        "view_bundle_files_total_count"
    ]
    assert calls["view_bundle_files_missing_count"] == 0
    assert calls["view_bundle_file_index_compacted"] is True
    assert calls["view_bundle_file_index_complete_in_response"] is False
    assert calls["trusted_clean_view_policy_summary_field"] == (
        "trusted_clean_view_replay"
    )
    assert calls["trusted_clean_view_policy_requires_view_selection_match"] is True
    assert calls["trusted_clean_view_policy_requires_all_views_confirmed"] is True
    assert calls["trusted_clean_view_policy_calculation_independent"] is True
    assert calls["history_count"] == 1
    assert calls["visual_diagnostics_binding_verified"] is True
    assert calls["visual_diagnostics_action_id"]
    assert calls["visual_diagnostics_action_tool"]
    assert "visual_diagnostics" in calls["coordinated_action_tracks"]
    compaction = calls["preflight_response_compaction"]
    assert compaction["schema"] == (
        "material_studio_live_session_preflight_compact_v1"
    )
    assert compaction["target_exceeded"] is False
    assert compaction["response_bytes"] < compaction["target_bytes"]
    assert compaction["headroom_bytes"] == (
        compaction["budget_bytes"] - compaction["response_bytes"]
    )
    assert max(
        calls["response_sizes_bytes"][name]
        for name in (
            "capabilities",
            "create",
            "status",
            "prepare_view_replay",
            "resumed_preflight",
            "view_bundle",
        )
    ) < COMPACT_RESPONSE_MAX_BYTES
    assert Path(calls["view_bundle_manifest_path"]).exists()
    assert result["config_audit"]["ok"] is True


def test_codex_config_audit_reports_missing_tools_without_modifying_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.materials_studio]
command = "python"
args = ["run_server.py"]
enabled = true
enabled_tools = ["material_studio_live_modeling_request"]
disabled_tools = ["material_studio_run_script"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = audit_codex_config(config)

    assert result["ok"] is False
    assert "material_studio_gui_prepare_view_replay" in result["missing_enabled_tools"]
    assert "material_studio_project_reconcile_dopant_metadata" in result["missing_enabled_tools"]
    assert result["unexpected_dangerous_enabled_tools"] == []
    assert result["run_script_explicitly_disabled"] is True
    assert config.read_text(encoding="utf-8").count("material_studio_live_modeling_request") == 1
