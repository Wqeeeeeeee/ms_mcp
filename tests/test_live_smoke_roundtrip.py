from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from material_studio_mcp_server import live_smoke
from material_studio_mcp_server.roundtrip import (
    ROUNDTRIP_AUDIT_PROFILE,
    ROUNDTRIP_AUDIT_SCHEMA_VERSION,
)
from material_studio_mcp_server.specs.common import ExecutionMode


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preview_plan(tmp_path: Path, project_id: str, revision: int) -> dict[str, Any]:
    run_root = (
        tmp_path
        / project_id
        / "outputs"
        / f"r{revision:03d}"
        / "ms_roundtrip"
        / "preview"
    )
    return {
        "schema_version": ROUNDTRIP_AUDIT_SCHEMA_VERSION,
        "profile": ROUNDTRIP_AUDIT_PROFILE,
        "project_id": project_id,
        "revision": revision,
        "execution_mode": "preview",
        "required": True,
        "applicable": True,
        "status": "deferred_until_materialized",
        "spec_sha256": "a" * 64,
        "run_id": "preview",
        "run_root": str(run_root),
        "output_path": str(run_root / "roundtrip_output.cif"),
        "gui_probe_planned": True,
        "runner_call_planned": False,
        "side_effects": {
            "files_written": False,
            "runner_called": False,
            "gui_input_performed": False,
        },
        "errors": [],
        "warnings": ["Structure is not materialized."],
    }


def _execute_receipt(
    tmp_path: Path,
    project_id: str,
    revision: int,
    *,
    real_materials_studio: bool = False,
) -> dict[str, Any]:
    revision_root = tmp_path / project_id / "outputs" / f"r{revision:03d}"
    run_root = revision_root / "ms_roundtrip" / f"attempt_{revision:03d}"
    run_root.mkdir(parents=True)
    source = revision_root / f"structure_r{revision:03d}.cif"
    output = run_root / "roundtrip_output.cif"
    script = run_root / "roundtrip.pl"
    receipt_path = run_root / "roundtrip_audit.json"
    source.write_text("data_source\n", encoding="utf-8")
    output.write_text("data_output\n", encoding="utf-8")
    script.write_text("use MaterialsScript qw(:all);\n", encoding="utf-8")
    receipt_path.write_text("{}\n", encoding="utf-8")
    source_sha = _sha256(source)
    output_sha = _sha256(output)
    runner_path = tmp_path / "RunMatScript.bat"
    runner_path.write_text("@echo off\n", encoding="utf-8")
    runner = {
        "path": str(runner_path),
        "exists": True,
        "sha256": _sha256(runner_path),
        "real_materials_studio_20_1": real_materials_studio,
        "runner_kind": (
            "materials_studio_20_1"
            if real_materials_studio
            else "unverified_or_fake_runner"
        ),
    }
    inventory = {
        "available": True,
        "usable_single_window": True,
        "process_count": 1,
        "window_count": 1,
        "visible_window_count": 1,
        "process_identity_sha256": "c" * 64,
        "window_identity_sha256": "d" * 64,
        "window_minimized": False,
    }
    return {
        "schema_version": ROUNDTRIP_AUDIT_SCHEMA_VERSION,
        "profile": ROUNDTRIP_AUDIT_PROFILE,
        "project_id": project_id,
        "revision": revision,
        "execution_mode": "execute",
        "required": True,
        "applicable": True,
        "status": "passed",
        "ok": True,
        "scientific_status": "NOT_RUN",
        "scientific_correctness_established": False,
        "calculation_performed": False,
        "gui_input_performed": False,
        "matstudio_process_launched": False,
        "real_materials_studio_status": (
            "PASS" if real_materials_studio else "NOT_RUN"
        ),
        "spec_sha256": "a" * 64,
        "source_path": str(source),
        "source_sha256_planned": source_sha,
        "source_sha256_before": source_sha,
        "source_sha256_after": source_sha,
        "output_path": str(output),
        "output_sha256": output_sha,
        "run_root": str(run_root),
        "script_sha256": _sha256(script),
        "receipt_path": str(receipt_path),
        "source_unchanged": True,
        "output_confined": True,
        "runner_script_confined": True,
        "script_identity_verified": True,
        "tagged_summary_verified": True,
        "runner_success": True,
        "runner_timed_out": False,
        "runner_duration_seconds": 0.1,
        "runner_created_files": [str(script), str(output), str(receipt_path)],
        "runner_identity": {
            "before": dict(runner),
            "after": dict(runner),
            "unchanged": True,
        },
        "gui_invariant": {
            "required": True,
            "before": dict(inventory),
            "after": dict(inventory),
            "identity_unchanged": True,
            "single_window_ok": True,
            "process_count_before_after": [1, 1],
            "window_count_before_after": [1, 1],
            "process_launched": False,
            "window_changed": False,
            "passed": True,
        },
        "comparison": {
            "schema_version": "material_studio_cif_roundtrip_comparison_v1",
            "input_sha256": source_sha,
            "output_sha256": output_sha,
            "input_atom_count": 2,
            "output_atom_count": 2,
            "input_element_counts": {"Si": 2},
            "output_element_counts": {"Si": 2},
            "mapping_coverage": 1.0,
            "mapping_degenerate": False,
            "rms_displacement_angstrom": 0.0,
            "maximum_displacement_angstrom": 0.0,
            "maximum_relative_lattice_error": 0.0,
            "passed": True,
            "errors": [],
            "warnings": [],
        },
        "errors": [],
        "warnings": [],
    }


def _live_response(
    project_id: str,
    revision: int,
    audit: dict[str, Any],
) -> dict[str, Any]:
    mode = str(audit["execution_mode"])
    response: dict[str, Any] = {
        "ok": True,
        "workflow": "create" if revision == 0 else "patch",
        "project_id": project_id,
        "revision": revision,
        "new_revision": revision,
        "execution_mode": mode,
        "materials_studio_roundtrip_audit_requested": True,
        "materials_studio_roundtrip_audit": audit,
        "modeling_report": {
            "execution_mode": mode,
            "normality": "preview_ready" if mode == "preview" else "executed",
            "normality_gate": {"status": "preview_only" if mode == "preview" else "review_required"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }
    if mode == "execute":
        response["result"] = {"success": True, "execution_backend": "fake_runner"}
    return response


def _status_response(live: dict[str, Any]) -> dict[str, Any]:
    audit = json.loads(json.dumps(live["materials_studio_roundtrip_audit"]))
    return {
        "ok": True,
        "project_id": live["project_id"],
        "revision": live["revision"],
        "execution_mode": live["execution_mode"],
        "materials_studio_roundtrip_audit_requested": True,
        "materials_studio_roundtrip_audit": audit,
        "modeling_report": {
            "execution_mode": live["execution_mode"],
            "normality_gate": {"status": "preview_only"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }


def _install_smoke_fakes(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
    *,
    status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pending = list(responses)

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **_kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )

    def fake_live(_request: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return pending.pop(0)

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        fake_live,
    )
    final_status = status or _status_response(responses[-1])
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda **_kwargs: final_status,
    )
    return calls


def test_preview_roundtrip_is_requested_and_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "preview_roundtrip_smoke"
    plan = _preview_plan(tmp_path, project_id, 0)
    live = _live_response(project_id, 0, plan)
    calls = _install_smoke_fakes(monkeypatch, [live])

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
        export_bundle=False,
        verify_ms_roundtrip=True,
    )

    assert result["ok"] is True
    assert calls[0]["verify_ms_roundtrip"] is True
    assert calls[0]["execution_mode"] is ExecutionMode.PREVIEW
    assert not Path(plan["run_root"]).exists()
    acceptance = result["summary"]["materials_studio_roundtrip_acceptance"]
    assert acceptance["ok"] is True
    assert acceptance["phases"]["base"]["audit_status"] == "deferred_until_materialized"
    assert acceptance["final_status"]["consistent_with_final_live"] is True


def test_default_smoke_contract_omits_roundtrip_fields_and_argument(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "default_smoke"
    live = _live_response(project_id, 0, _preview_plan(tmp_path, project_id, 0))
    live.pop("materials_studio_roundtrip_audit_requested")
    live.pop("materials_studio_roundtrip_audit")
    calls = _install_smoke_fakes(
        monkeypatch,
        [live],
        status={
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "execution_mode": "preview",
            "modeling_report": {"execution_mode": "preview"},
        },
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        export_bundle=False,
    )

    assert result["ok"] is True
    assert "verify_ms_roundtrip" not in calls[0]
    assert "verify_ms_roundtrip_requested" not in result
    assert "materials_studio_roundtrip_acceptance" not in result["summary"]


def test_offline_execute_roundtrip_passes_without_claiming_real_ms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "offline_execute_smoke"
    receipt = _execute_receipt(tmp_path, project_id, 0)
    live = _live_response(project_id, 0, receipt)
    _install_smoke_fakes(monkeypatch, [live])

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        verify_ms_roundtrip=True,
    )

    acceptance = result["summary"]["materials_studio_roundtrip_acceptance"]
    assert result["ok"] is True
    assert acceptance["ok"] is True
    assert acceptance["real_materials_studio_status"] == "NOT_RUN"


@pytest.mark.parametrize("real_status", [False, True])
def test_require_real_roundtrip_accepts_only_verified_materials_studio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    real_status: bool,
) -> None:
    project_id = f"require_real_{real_status}"
    receipt = _execute_receipt(
        tmp_path,
        project_id,
        0,
        real_materials_studio=real_status,
    )
    live = _live_response(project_id, 0, receipt)
    _install_smoke_fakes(monkeypatch, [live])

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        require_real_ms_roundtrip=True,
    )

    assert result["ok"] is real_status
    acceptance = result["summary"]["materials_studio_roundtrip_acceptance"]
    assert acceptance["ok"] is real_status
    if not real_status:
        assert any(
            failure["type"] == "roundtrip_real_materials_studio_not_proven"
            for failure in acceptance["failures"]
        )


def test_follow_up_roundtrip_validates_both_revisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "followup_roundtrip_smoke"
    base = _live_response(project_id, 0, _preview_plan(tmp_path, project_id, 0))
    followup = _live_response(project_id, 1, _preview_plan(tmp_path, project_id, 1))
    calls = _install_smoke_fakes(monkeypatch, [base, followup])

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        follow_up_request="Add one vacancy.",
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        export_bundle=False,
        verify_ms_roundtrip=True,
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert all(call["verify_ms_roundtrip"] is True for call in calls)
    acceptance = result["summary"]["materials_studio_roundtrip_acceptance"]
    assert acceptance["phase_count"] == 2
    assert acceptance["phases"]["base"]["revision"] == 0
    assert acceptance["phases"]["followup"]["revision"] == 1


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("source_hash", "roundtrip_source_identity_changed"),
        ("script_identity", "roundtrip_execute_gate_not_verified"),
        ("tagged_json", "roundtrip_execute_gate_not_verified"),
        ("comparison", "roundtrip_comparison_not_passed"),
        ("gui_inventory", "roundtrip_gui_process_inventory_invalid"),
        ("status_receipt", "roundtrip_final_status_receipt_mismatch"),
    ],
)
def test_execute_roundtrip_rejects_incomplete_or_inconsistent_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
    expected_failure: str,
) -> None:
    project_id = f"invalid_{mutation}"
    receipt = _execute_receipt(tmp_path, project_id, 0)
    live = _live_response(project_id, 0, receipt)
    status = _status_response(live)
    if mutation == "source_hash":
        receipt["source_sha256_after"] = "e" * 64
        status = _status_response(live)
    elif mutation == "script_identity":
        receipt["script_identity_verified"] = False
        status = _status_response(live)
    elif mutation == "tagged_json":
        receipt["tagged_summary_verified"] = False
        status = _status_response(live)
    elif mutation == "comparison":
        receipt["comparison"]["passed"] = False
        status = _status_response(live)
    elif mutation == "gui_inventory":
        receipt["gui_invariant"]["process_count_before_after"] = [1, 2]
        status = _status_response(live)
    else:
        status["materials_studio_roundtrip_audit"]["script_sha256"] = "f" * 64
    _install_smoke_fakes(monkeypatch, [live], status=status)

    result = live_smoke.run_live_smoke(
        request="Build silicon.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        verify_ms_roundtrip=True,
    )

    assert result["ok"] is False
    failures = result["summary"]["materials_studio_roundtrip_failures"]
    assert expected_failure in {failure["type"] for failure in failures}


@pytest.mark.parametrize("mode", ["auto", "preview"])
def test_require_real_cli_rejects_non_execute_mode(mode: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        live_smoke.main(
            [
                "--request",
                "Build silicon.",
                "--execution-mode",
                mode,
                "--require-real-ms-roundtrip",
            ]
        )

    assert exc_info.value.code == 2


def test_require_real_api_rejects_non_execute_mode() -> None:
    with pytest.raises(ValueError, match="explicit execution_mode='execute'"):
        live_smoke.run_live_smoke(
            request="Build silicon.",
            scenario=None,
            execution_mode="auto",
            require_real_ms_roundtrip=True,
        )


def test_cli_require_real_implies_verify_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_live_smoke(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "summary": {"project_id": "cli_roundtrip"}}

    monkeypatch.setattr(live_smoke, "run_live_smoke", fake_run_live_smoke)

    exit_code = live_smoke.main(
        [
            "--request",
            "Build silicon.",
            "--execution-mode",
            "execute",
            "--require-real-ms-roundtrip",
        ]
    )

    assert exit_code == 0
    assert captured["verify_ms_roundtrip"] is True
    assert captured["require_real_ms_roundtrip"] is True
    assert json.loads(capsys.readouterr().out)["project_id"] == "cli_roundtrip"
