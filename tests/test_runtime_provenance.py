from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server import codex_config
from material_studio_mcp_server import runtime_provenance as runtime_module
from material_studio_mcp_server.codex_config import build_codex_config_snippet
from material_studio_mcp_server.runtime_provenance import (
    RUNTIME_DEPLOYMENT_SCHEMA,
    RUNTIME_PROVENANCE_SCHEMA,
    RuntimeProvenanceTracker,
    runtime_deployment_status,
    source_tree_snapshot,
)


def test_source_tree_snapshot_is_deterministic_and_python_only(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    (package_root / "b.py").write_text("B = 2\n", encoding="utf-8")
    (package_root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (package_root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    cache_dir = package_root / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "ignored.py").write_text("IGNORED = True\n", encoding="utf-8")

    first = source_tree_snapshot(package_root)
    second = source_tree_snapshot(package_root)

    assert first == second
    assert first["status"] == "complete"
    assert first["file_count"] == 2
    assert first["total_bytes"] == sum(
        (package_root / name).stat().st_size for name in ("a.py", "b.py")
    )
    assert len(first["sha256"]) == 64


def test_runtime_deployment_binds_source_checkout_entrypoint_and_git(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    package_root = root / "src" / "material_studio_mcp_server"
    package_root.mkdir(parents=True)
    run_server = root / "run_server.py"
    run_server.write_text("print('server')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_module,
        "_git_metadata",
        lambda repository_root: {
            "status": "available",
            "head_commit": "a" * 40,
            "branch": "codex/runtime-binding-test",
            "worktree_dirty": False,
            "error": None,
        },
    )

    result = runtime_deployment_status(
        package_root,
        entrypoint=run_server,
        process_cwd=root,
    )

    assert result["schema"] == RUNTIME_DEPLOYMENT_SCHEMA
    assert result["status"] == "source_checkout"
    assert result["repository_root"] == str(root.resolve())
    assert result["entrypoint_binding"] == "matched_source_run_server"
    assert result["cwd_matches_repository"] is True
    assert result["git"]["head_commit"] == "a" * 40
    assert result["git"]["branch"] == "codex/runtime-binding-test"
    assert result["diagnostic_only"] is True
    assert result["materials_studio_process_started"] is False


def test_runtime_deployment_reports_different_source_entrypoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    package_root = root / "src" / "material_studio_mcp_server"
    package_root.mkdir(parents=True)
    (root / "run_server.py").write_text("print('server')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_module,
        "_git_metadata",
        lambda repository_root: {
            "status": "repository_root_unavailable",
            "head_commit": None,
            "branch": None,
            "worktree_dirty": None,
            "error": None,
        },
    )

    result = runtime_deployment_status(
        package_root,
        entrypoint=tmp_path / "other" / "run_server.py",
        process_cwd=tmp_path,
    )

    assert result["status"] == "source_checkout"
    assert result["entrypoint_binding"] == "different_entrypoint"
    assert result["cwd_matches_repository"] is False


def test_runtime_deployment_normalizes_windows_extended_path_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        return
    root = tmp_path / "repo"
    package_root = root / "src" / "material_studio_mcp_server"
    package_root.mkdir(parents=True)
    run_server = root / "run_server.py"
    run_server.write_text("print('server')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_module,
        "_git_metadata",
        lambda repository_root: {
            "status": "available",
            "head_commit": "a" * 40,
            "branch": "codex/runtime-binding-test",
            "worktree_dirty": False,
            "error": None,
        },
    )
    extended_package_root = Path("\\\\?\\" + str(package_root.resolve()))

    result = runtime_deployment_status(
        extended_package_root,
        entrypoint=run_server,
        process_cwd=root,
    )

    assert result["repository_root"] == str(root.resolve())
    assert result["package_root"] == str(package_root.resolve())
    assert result["expected_source_entrypoint"] == str(run_server.resolve())
    assert result["entrypoint_binding"] == "matched_source_run_server"
    assert result["cwd_matches_repository"] is True


def test_runtime_codex_config_status_prefers_matching_repository_registration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    (root / "run_server.py").write_text("print('server')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    user_config = tmp_path / "codex-home" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[projects]\n", encoding="utf-8")
    repository_config = root / ".codex" / "config.toml"
    repository_config.parent.mkdir(parents=True)
    repository_config.write_text(
        build_codex_config_snippet(root, python_command=python),
        encoding="utf-8",
    )
    before_user = user_config.read_bytes()
    before_repository = repository_config.read_bytes()
    monkeypatch.setattr(
        codex_config,
        "default_active_config_path",
        lambda: user_config.resolve(),
    )

    result = server._runtime_codex_config_status(
        {
            "repository_root": str(root.resolve()),
            "python_executable": str(python.resolve()),
        }
    )

    assert result["schema"] == server.RUNTIME_CODEX_CONFIG_SCHEMA
    assert result["status"] == "ready"
    assert result["config_ready"] is True
    assert result["config_scope"] == "repository_local"
    assert result["config_resolution_status"] == (
        "matching_runtime_registration_found"
    )
    assert result["runtime_source_binding_matches_config"] is True
    assert result["config_candidate_count"] == 2
    assert result["config_candidates"][0]["config_scope"] == "codex_home"
    assert result["config_candidates"][1]["config_scope"] == "repository_local"
    assert result["read_only"] is True
    assert result["active_config_modified"] is False
    assert result["advisory_only"] is True
    assert result["execution_gate_changed"] is False
    assert user_config.read_bytes() == before_user
    assert repository_config.read_bytes() == before_repository


def test_runtime_provenance_detects_source_change_and_requires_restart(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    source_path = package_root / "module.py"
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    tracker = RuntimeProvenanceTracker.capture(
        package_root,
        loaded_at=datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone.utc),
        process_id=1234,
    )

    current = tracker.status(
        observed_at=datetime(2026, 7, 19, 1, 3, 3, tzinfo=timezone.utc)
    )
    assert current["schema"] == RUNTIME_PROVENANCE_SCHEMA
    assert current["status"] == "current"
    assert current["source_current"] is True
    assert current["restart_required"] is False
    assert current["restart_action"] is None

    source_path.write_text("VALUE = 2\n", encoding="utf-8")
    changed = tracker.status(
        observed_at=datetime(2026, 7, 19, 1, 4, 3, tzinfo=timezone.utc)
    )
    assert changed["status"] == "source_changed_since_start"
    assert changed["source_current"] is False
    assert changed["source_changed_since_start"] is True
    assert changed["restart_required"] is True
    assert changed["restart_action"] == "restart_mcp_server_then_retry_preflight"
    assert changed["runtime_instance_id"] == current["runtime_instance_id"]
    assert (
        changed["source_snapshot_current"]["sha256"]
        != changed["source_snapshot_at_start"]["sha256"]
    )


def test_runtime_provenance_fails_closed_when_current_snapshot_is_unavailable(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    (package_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tracker = RuntimeProvenanceTracker.capture(package_root, process_id=1234)

    status = tracker.status(
        current_snapshot={
            "status": "unavailable",
            "sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "unreadable_files": [],
            "error": "read blocked",
        }
    )

    assert status["status"] == "source_snapshot_unavailable"
    assert status["source_current"] is False
    assert status["restart_required"] is True


def test_live_preflight_binds_runner_receipt_to_requested_workspace(
    tmp_path: Path,
) -> None:
    result = server._live_session_preflight_payload(
        working_dir=str(tmp_path),
        include_latest_project=False,
        include_gui_status=False,
    )

    runtime = result["runtime_provenance"]
    assert runtime["schema"] == RUNTIME_PROVENANCE_SCHEMA
    assert runtime["source_current"] is True
    assert result["runtime_deployment"]["schema"] == RUNTIME_DEPLOYMENT_SCHEMA
    assert result["runtime_deployment"]["diagnostic_only"] is True
    assert result["codex_config_status"]["schema"] == (
        server.RUNTIME_CODEX_CONFIG_SCHEMA
    )
    assert result["codex_config_status"]["read_only"] is True
    assert result["codex_config_status"]["active_config_modified"] is False
    assert result["readiness"]["server_source_current"] is True
    assert result["readiness"]["server_restart_required"] is False
    assert result["readiness"]["runtime_repository_root"]
    assert "runtime_git_head" in result["readiness"]
    assert "runtime_git_branch" in result["readiness"]
    assert result["readiness"]["codex_config_advisory_only"] is True
    assert result["readiness"]["gui_status_was_probed"] is False
    assert result["readiness"]["gui_preflight_verified"] is False
    assert result["readiness"]["gui_preflight_required"] is True
    assert result["readiness"]["gui_preflight_reasons"] == [
        "gui_status_not_probed",
        "single_window_policy_not_verified",
    ]
    assert result["readiness"]["live_hotload_ready"] is False
    assert result["readiness"]["crystal_cif_hotload_ready"] is False
    runner_status = result["runner_status"]
    assert runner_status["request_workspace_root"] == str(tmp_path.resolve())
    assert runner_status["default_workspace_root"] == runner_status["workspace_root"]
    assert runner_status["execution_working_dir_policy"] == (
        "explicit_tool_working_dir_overrides_runner_default"
    )
    assert result["mcp_client_readiness"]["server_source_current"] is True
    assert result["mcp_client_readiness"]["runtime_repository_root"]
    assert result["mcp_client_readiness"]["codex_config_advisory_only"] is True
    assert result["mcp_client_readiness"]["gui_preflight_required"] is True
    assert (
        result["mcp_client_readiness"][
            "can_accept_hotload_request_without_new_window"
        ]
        is False
    )
    assert result["mcp_client_readiness"]["same_window_hotload_ready"] is False
    assert result["mcp_server_source_current"] is True


def test_live_preflight_reports_config_drift_without_changing_modeling_gates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        server,
        "_runtime_codex_config_status",
        lambda deployment: {
            "schema": server.RUNTIME_CODEX_CONFIG_SCHEMA,
            "status": "entrypoint_drift",
            "config_ready": False,
            "read_only": True,
            "active_config_modified": False,
            "advisory_only": True,
            "execution_gate_changed": False,
        },
    )

    result = server._live_session_preflight_payload(
        working_dir=str(tmp_path),
        include_latest_project=False,
        include_gui_status=False,
    )

    assert result["readiness"]["codex_config_status"] == "entrypoint_drift"
    assert result["readiness"]["codex_config_ready"] is False
    assert result["readiness"]["codex_config_review_required"] is True
    assert result["readiness"]["codex_config_advisory_only"] is True
    assert result["readiness"]["preview_ready"] is True
    assert result["mcp_client_readiness"]["can_accept_preview_request"] is True
    assert result["mcp_client_readiness"]["codex_config_review_required"] is True
    assert result["mcp_client_readiness"]["codex_config_advisory_only"] is True


def test_live_preflight_blocks_stale_runtime_before_modeling_or_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stale_runtime = {
        "schema": RUNTIME_PROVENANCE_SCHEMA,
        "status": "source_changed_since_start",
        "source_current": False,
        "source_changed_since_start": True,
        "restart_required": True,
        "runtime_instance_id": "stale-runtime-1",
        "process_id": 1234,
        "loaded_at_utc": "2026-07-19T01:02:03+00:00",
        "observed_at_utc": "2026-07-19T02:02:03+00:00",
        "source_snapshot_at_start": {
            "status": "complete",
            "sha256": "a" * 64,
            "file_count": 10,
            "total_bytes": 100,
            "unreadable_files": [],
        },
        "source_snapshot_current": {
            "status": "complete",
            "sha256": "b" * 64,
            "file_count": 11,
            "total_bytes": 110,
            "unreadable_files": [],
        },
        "restart_action": "restart_mcp_server_then_retry_preflight",
    }
    monkeypatch.setattr(server, "runtime_provenance_status", lambda: stale_runtime)
    monkeypatch.setattr(
        server,
        "_latest_project_preflight_summary",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("latest project probe must be deferred")
        ),
    )
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("GUI probe must be deferred")
        ),
    )

    result = server._live_session_preflight_payload(
        working_dir=str(tmp_path),
        include_latest_project=True,
        include_gui_status=True,
    )

    assert result["state"] == "mcp_server_restart_required"
    assert result["recommended_action"] == (
        "restart_mcp_server_then_verify_runtime_source"
    )
    assert result["deferred_probes"] == {
        "latest_project": True,
        "gui_status": True,
        "reason": "mcp_server_source_changed_since_start",
    }
    assert "mcp_server_source_changed_since_start" in result["blocking_reasons"]
    readiness = result["readiness"]
    assert readiness["preview_ready"] is False
    assert readiness["execute_ready"] is False
    assert readiness["live_hotload_ready"] is False
    action = result["next_action_plan"]
    assert action["action_id"] == "restart_stale_mcp_server"
    assert action["external_action_required"] is True
    assert action["tool_call_ready"] is False
    assert action["needs_user_confirmation"] is True
    assert action["payload_hint"]["external_action"] == "restart_mcp_server_session"
    client = result["mcp_client_readiness"]
    assert client["status"] == "setup_required"
    assert client["can_accept_modeling_request"] is False
    assert client["can_accept_preview_request"] is False
    assert client["can_accept_hotload_request_without_new_window"] is False
    assert client["server_restart_required"] is True
    assert "mcp_server_source_changed_since_start" in client["hotload_blocking_reasons"]
    assert result["mcp_server_restart_required"] is True


def test_status_and_capabilities_expose_runtime_provenance_contract() -> None:
    status = server.material_studio_get_status()
    assert status["ok"] is True
    assert status["runtime_provenance"]["schema"] == RUNTIME_PROVENANCE_SCHEMA
    assert status["runtime_deployment"]["schema"] == RUNTIME_DEPLOYMENT_SCHEMA
    assert status["runtime_deployment"]["diagnostic_only"] is True
    assert status["runtime_deployment"]["materials_studio_process_started"] is False
    assert status["codex_config_status"]["schema"] == (
        server.RUNTIME_CODEX_CONFIG_SCHEMA
    )
    assert status["codex_config_status"]["read_only"] is True
    assert status["codex_config_status"]["active_config_modified"] is False
    assert status["codex_config_status"]["advisory_only"] is True
    assert status["codex_config_status"]["execution_gate_changed"] is False
    capabilities = server._live_capabilities_payload(include_status=False)
    contract = capabilities["runtime_provenance_contract"]
    assert contract["schema"] == RUNTIME_PROVENANCE_SCHEMA
    assert contract["deployment_binding_schema"] == RUNTIME_DEPLOYMENT_SCHEMA
    assert contract[
        "deployment_binding_reports_source_checkout_and_git_identity"
    ] is True
    assert contract["live_preflight_source_drift_blocks_continuation"] is True
    assert contract["direct_tool_runtime_guard"] is True
    assert (
        contract["direct_tool_runtime_guard_schema"]
        == server.DIRECT_RUNTIME_GUARD_SCHEMA
    )
    assert contract["direct_tool_runtime_guard_evaluated_before_tool_body"] is True
    assert contract["direct_tool_runtime_guard_fails_closed"] is True
    assert contract["guarded_tool_count"] == len(
        server.RUNTIME_SOURCE_GUARDED_TOOL_NAMES
    )
    assert contract["guarded_tool_names"] == list(
        server.RUNTIME_SOURCE_GUARDED_TOOL_NAMES
    )
    assert contract["restart_is_never_automatic"] is True
    config_contract = capabilities["codex_config_status_contract"]
    assert config_contract["schema"] == server.RUNTIME_CODEX_CONFIG_SCHEMA
    assert config_contract["read_only"] is True
    assert config_contract["active_config_is_never_modified"] is True
    assert config_contract["advisory_only"] is True
    assert config_contract["does_not_change_execution_or_hotload_gates"] is True


def test_direct_runtime_guard_blocks_every_side_effect_capable_tool_before_body(
    monkeypatch,
) -> None:
    stale_runtime = {
        "schema": RUNTIME_PROVENANCE_SCHEMA,
        "status": "source_changed_since_start",
        "source_current": False,
        "source_changed_since_start": True,
        "restart_required": True,
        "runtime_instance_id": "stale-direct-tool-runtime",
        "restart_action": "restart_mcp_server_then_retry_preflight",
    }

    def unexpected_body(*args, **kwargs):
        raise AssertionError("guarded tool body must not start")

    monkeypatch.setattr(server, "runtime_provenance_status", lambda: stale_runtime)
    monkeypatch.setattr(server, "_structured_store", unexpected_body)
    monkeypatch.setattr(server, "_gui_controller", unexpected_body)
    monkeypatch.setattr(server.runner, "run_script", unexpected_body)

    for tool_name in server.RUNTIME_SOURCE_GUARDED_TOOL_NAMES:
        tool = getattr(server, tool_name)
        assert getattr(tool, "__runtime_source_guarded__", False) is True
        result = tool()
        assert result["ok"] is False
        assert result["status"] == "mcp_server_restart_required"
        assert result["blocked_tool"] == tool_name
        assert result["runtime_guard"] == {
            "schema": server.DIRECT_RUNTIME_GUARD_SCHEMA,
            "blocked": True,
            "evaluated_before_tool_body": True,
            "blocking_reason": "mcp_server_source_changed_since_start",
        }
        assert result["tool_body_started"] is False
        assert result["side_effects_started"] is False
        assert result["execution_started"] is False
        assert result["runner_invoked"] is False
        assert result["gui_input_started"] is False
        assert result["gui_process_launched"] is False
        assert result["revision_created"] is False
        assert result["artifact_write_started"] is False
        assert result["retry_tool"] == tool_name
        assert result["restart_plan"]["preserve_materials_studio_process"] is True


def test_direct_runtime_guard_fails_closed_when_provenance_probe_raises(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "runtime_provenance_status",
        lambda: (_ for _ in ()).throw(OSError("source tree read failed")),
    )
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("GUI must not be probed")
        ),
    )

    result = server.material_studio_gui_launch()

    assert result["status"] == "mcp_server_restart_required"
    assert result["blocked_tool"] == "material_studio_gui_launch"
    runtime = result["runtime_provenance"]
    assert runtime["status"] == "source_snapshot_unavailable"
    assert runtime["source_current"] is False
    assert runtime["restart_required"] is True
    assert runtime["status_probe_error"] == "OSError: source tree read failed"
    assert result["blocking_reasons"] == [
        "mcp_server_source_snapshot_unavailable"
    ]


def test_direct_runtime_guard_preserves_signature_and_current_source_behavior(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "runtime_provenance_status",
        lambda: {
            "schema": RUNTIME_PROVENANCE_SCHEMA,
            "status": "current",
            "source_current": True,
            "restart_required": False,
        },
    )

    signature = inspect.signature(server.material_studio_model_create_from_spec)
    assert "spec" in signature.parameters
    assert "execution_mode" in signature.parameters
    result = server.material_studio_run_script("use strict;\n", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result.get("status") != "mcp_server_restart_required"


def test_runtime_recovery_tools_remain_callable_when_source_is_stale(
    monkeypatch,
) -> None:
    stale_runtime = {
        "schema": RUNTIME_PROVENANCE_SCHEMA,
        "status": "source_changed_since_start",
        "source_current": False,
        "source_changed_since_start": True,
        "restart_required": True,
        "runtime_instance_id": "stale-recovery-runtime",
        "restart_action": "restart_mcp_server_then_retry_preflight",
    }
    monkeypatch.setattr(server, "runtime_provenance_status", lambda: stale_runtime)

    for tool_name in (
        "material_studio_get_status",
        "material_studio_live_capabilities",
        "material_studio_live_session_preflight",
    ):
        assert getattr(
            getattr(server, tool_name), "__runtime_source_guarded__", False
        ) is False

    status = server.material_studio_get_status()
    assert status["ok"] is True
    assert status["runtime_provenance"] == stale_runtime
    capabilities = server.material_studio_live_capabilities()
    assert capabilities["ok"] is True
    assert capabilities["runtime_provenance_contract"][
        "direct_tool_runtime_guard"
    ] is True
