from pathlib import Path


def test_codex_config_example_disables_custom_script() -> None:
    text = Path(".codex/config.toml.example").read_text(encoding="utf-8")
    assert "material_studio_live_capabilities" in text
    assert "material_studio_project_reconcile_dopant_metadata" in text
    assert "material_studio_live_session_preflight" in text
    assert "material_studio_model_create_from_spec" in text
    assert "material_studio_live_modeling_request" in text
    assert "material_studio_live_project_status" in text
    assert "material_studio_live_watchdog_status" in text
    assert "material_studio_live_update_with_patch" in text
    assert "material_studio_model_export_view_audit" in text
    assert "material_studio_model_export_view_bundle" in text
    assert "material_studio_gui_status" in text
    assert "material_studio_gui_launch" in text
    assert "material_studio_gui_apply_current_revision" in text
    assert "material_studio_gui_fit_to_view" in text
    assert "material_studio_gui_record_visual_confirmation" in text
    assert "material_studio_gui_prepare_view_replay" in text
    assert "material_studio_gui_execute_view_replay" in text
    assert "material_studio_gui_record_view_replay" in text
    assert "material_studio_castep_relax_current" in text
    assert "material_studio_castep_run_current" in text
    assert (
        "[mcp_servers.materials_studio.tools.material_studio_gui_execute_view_replay]"
        in text
    )
    assert (
        "[mcp_servers.materials_studio.tools.material_studio_gui_fit_to_view]"
        in text
    )
    assert "material_studio_run_script" in text
    assert "disabled_tools" in text
    assert (
        "[mcp_servers.materials_studio.tools.material_studio_castep_relax_current]"
        in text
    )
    assert (
        "[mcp_servers.materials_studio.tools.material_studio_castep_run_current]"
        in text
    )
