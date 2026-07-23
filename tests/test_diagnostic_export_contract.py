from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import server
from material_studio_mcp_server.diagnostic_contract import (
    DIAGNOSTIC_EXPORT_CONTRACT_VERSION,
    DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
    REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS,
    REQUIRED_DIAGNOSTIC_ROW_COUNTS,
    VIEW_BUNDLE_SCHEMA_VERSION,
    assess_diagnostic_export_contract,
)
from material_studio_mcp_server.diagnostics import model_view_audit, write_view_audit_bundle
from material_studio_mcp_server.specs.project import ModelSpec


def _complete_diagnostics(tmp_path: Path, *, contract_version: int | None = 2) -> dict[str, object]:
    paths: dict[str, str] = {}
    for key in REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS:
        path = tmp_path / f"{key}.artifact"
        path.write_text(key, encoding="utf-8")
        paths[key] = str(path)
    paths.update(
        {
            "view_bundle_manifest_path": str(tmp_path / "manifest.json"),
            "diagnostic_export_manifest_json": str(tmp_path / "diagnostic_manifest.json"),
        }
    )
    Path(paths["view_bundle_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(paths["diagnostic_export_manifest_json"]).write_text("{}", encoding="utf-8")
    return {
        **paths,
        "view_bundle_contract_version": contract_version,
        "view_bundle_schema_version": VIEW_BUNDLE_SCHEMA_VERSION,
        "view_bundle_row_counts": dict(REQUIRED_DIAGNOSTIC_ROW_COUNTS),
    }


def test_real_view_bundle_publishes_current_contract(tmp_path: Path) -> None:
    examples = Path(__file__).parents[1] / "src" / "material_studio_mcp_server" / "examples"
    spec = ModelSpec.model_validate_json(
        (examples / "benzene_spec.json").read_text(encoding="utf-8")
    )
    bundle = write_view_audit_bundle(tmp_path, spec, model_view_audit(spec))
    diagnostics = {
        **bundle["files"],
        "view_bundle_manifest_path": bundle["manifest_path"],
        "view_bundle_contract_version": bundle["contract_version"],
        "view_bundle_schema_version": bundle["schema_version"],
        "view_bundle_row_counts": bundle["row_counts"],
    }

    contract = assess_diagnostic_export_contract(diagnostics, diagnostic_requested=True)

    assert contract["schema_version"] == DIAGNOSTIC_EXPORT_SCHEMA_VERSION
    assert contract["current_contract_version"] == DIAGNOSTIC_EXPORT_CONTRACT_VERSION
    assert contract["status"] == "current"
    assert contract["current"] is True
    assert contract["refresh_required"] is False


def test_legacy_bundle_is_refreshable_without_prior_diagnostic_intent(tmp_path: Path) -> None:
    diagnostics = _complete_diagnostics(tmp_path, contract_version=None)
    diagnostics.pop("view_reference_atlas_svg")
    diagnostics.pop("view_reference_manifest_json")
    diagnostics.pop("view_reference_index_csv")
    diagnostics["view_bundle_row_counts"] = {
        "view_summary": 1,
        "view_projections": 1,
        "view_quality": 1,
    }
    report = {
        "project_id": "legacy_project",
        "revision": 4,
        "working_dir": str(tmp_path),
        "diagnostics": diagnostics,
    }

    contract = assess_diagnostic_export_contract(diagnostics)
    plan = server._diagnostic_focus_plan(report)
    manifest = server._diagnostic_export_manifest(report)

    assert contract["status"] == "legacy_unversioned"
    assert contract["refresh_required"] is True
    assert plan["available"] is True
    assert plan["status"] == "diagnostic_export_contract_refresh_required"
    assert plan["export_needed"] is True
    assert plan["payload_hint"]["include_gui_snapshot"] is False
    assert manifest["status"] == "stale_contract"
    assert manifest["refresh"]["changes_gui"] is False
    assert manifest["refresh"]["creates_revision"] is False


def test_current_contract_reports_missing_required_artifact(tmp_path: Path) -> None:
    diagnostics = _complete_diagnostics(tmp_path)
    diagnostics.pop("view_reference_index_csv")

    contract = assess_diagnostic_export_contract(diagnostics)

    assert contract["status"] == "incomplete"
    assert contract["current"] is False
    assert contract["refresh_allowed"] is True
    assert contract["refresh_required"] is True
    assert contract["missing_required_artifact_keys"] == ["view_reference_index_csv"]
    assert "view_reference_index_csv" not in contract["existing_required_artifact_keys"]


def test_future_contract_is_read_only_and_cannot_be_overwritten(tmp_path: Path) -> None:
    diagnostics = _complete_diagnostics(tmp_path, contract_version=DIAGNOSTIC_EXPORT_CONTRACT_VERSION + 1)
    report = {
        "project_id": "future_project",
        "revision": 2,
        "working_dir": str(tmp_path),
        "diagnostics": diagnostics,
    }

    contract = assess_diagnostic_export_contract(diagnostics)
    plan = server._diagnostic_focus_plan(report)
    manifest = server._diagnostic_export_manifest(report)
    compact_plan = server._compact_diagnostic_focus_plan(plan)

    assert contract["status"] == "newer_than_runtime"
    assert contract["refresh_allowed"] is False
    assert contract["refresh_required"] is False
    assert plan["status"] == "diagnostic_export_contract_newer_than_runtime"
    assert plan["recommended_tool"] == "material_studio_live_capabilities"
    assert plan["payload_hint"] == {}
    assert manifest["status"] == "unsupported_newer_contract"
    assert manifest["refresh"]["recommended_tool"] == "material_studio_live_capabilities"
    assert manifest["refresh"]["payload_hint"] == {}
    assert compact_plan["diagnostic_export_contract_status"] == "newer_than_runtime"


def test_status_preserves_current_contract_after_same_revision_refresh(tmp_path: Path) -> None:
    examples = Path(__file__).parents[1] / "src" / "material_studio_mcp_server" / "examples"
    spec = ModelSpec.model_validate_json(
        (examples / "benzene_spec.json").read_text(encoding="utf-8")
    )
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    exported = server.material_studio_model_export_view_bundle(
        project_id=spec.project_id,
        include_gui_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="full",
    )
    assert exported["ok"] is True
    project_dir = tmp_path / spec.project_id
    report_path = project_dir / "outputs" / "r000" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    old_artifact_keys = (
        "view_reference_atlas_svg",
        "view_reference_manifest_json",
        "view_reference_index_csv",
    )
    report["view_bundle_files"] = {
        key: value
        for key, value in report["view_bundle_files"].items()
        if key not in old_artifact_keys
    }
    report["view_bundle_row_counts"].pop("view_reference_views", None)
    diagnostics = report["modeling_report"]["diagnostics"]
    diagnostics.pop("view_bundle_schema_version", None)
    diagnostics.pop("view_bundle_contract_version", None)
    diagnostics["view_bundle_row_counts"].pop("view_reference_views", None)
    for key in old_artifact_keys:
        diagnostics.pop(key, None)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    stale = server.material_studio_live_project_status(
        project_id=spec.project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert stale["ok"] is True
    assert stale["diagnostic_focus_plan"]["diagnostic_export_contract_status"] == (
        "legacy_unversioned"
    )

    history_count_before = len(
        (project_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
    )
    refreshed = server.material_studio_model_export_view_bundle(
        project_id=spec.project_id,
        include_gui_snapshot=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    assert refreshed["ok"] is True
    assert refreshed["revision"] == 0
    assert refreshed["view_bundle_contract_version"] == DIAGNOSTIC_EXPORT_CONTRACT_VERSION

    current = server.material_studio_live_project_status(
        project_id=spec.project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    history_count_after = len(
        (project_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert current["ok"] is True
    assert current["revision"] == 0
    assert current["diagnostic_focus_plan"]["diagnostic_export_contract_status"] == "current"
    assert current["diagnostic_focus_plan"]["export_needed"] is False
    assert history_count_after == history_count_before
