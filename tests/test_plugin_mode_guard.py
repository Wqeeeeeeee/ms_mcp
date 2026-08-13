from __future__ import annotations

from material_studio_mcp_server import server


def _current_runtime() -> dict[str, object]:
    return {
        "schema": "material_studio_mcp_runtime_provenance_v1",
        "status": "current",
        "source_current": True,
        "restart_required": False,
    }


def test_packaged_plugin_mode_blocks_custom_script_before_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_PLUGIN_MODE", "1")
    monkeypatch.setattr(server, "runtime_provenance_status", _current_runtime)
    monkeypatch.setattr(
        server.runner,
        "run_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not be invoked in packaged plugin mode")
        ),
    )

    result = server.material_studio_run_script("not valid Perl", dry_run=False)

    assert result["ok"] is False
    assert result["status"] == "plugin_custom_script_disabled"
    assert result["blocked_tool"] == "material_studio_run_script"
    assert result["custom_script_execution_enabled"] is False
    assert result["validation_started"] is False
    assert result["side_effects_started"] is False
    assert result["runner_invoked"] is False
    assert result["recommended_tool"] == "material_studio_live_modeling_request"


def test_packaged_plugin_mode_also_blocks_custom_script_dry_run(monkeypatch) -> None:
    monkeypatch.setenv("MATERIAL_STUDIO_MCP_PLUGIN_MODE", "true")
    monkeypatch.setattr(server, "runtime_provenance_status", _current_runtime)

    result = server.material_studio_run_script("use strict;\n", dry_run=True)

    assert result["status"] == "plugin_custom_script_disabled"
    assert result["execution_started"] is False
    assert result["artifact_write_started"] is False


def test_non_plugin_source_and_wheel_behavior_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("MATERIAL_STUDIO_MCP_PLUGIN_MODE", raising=False)
    monkeypatch.setattr(server, "runtime_provenance_status", _current_runtime)

    result = server.material_studio_run_script("use strict;\n", dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result.get("status") != "plugin_custom_script_disabled"
