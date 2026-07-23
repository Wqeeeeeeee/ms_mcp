from __future__ import annotations

import csv
import json
from pathlib import Path

from material_studio_mcp_server import server


def _gate() -> dict:
    return {
        "available": True,
        "status": "preview_only",
        "primary_reason": "preview_not_hot_loaded",
        "can_claim_model_normal": False,
        "can_claim_live_gui_normal": False,
        "ready_for_next_edit": True,
        "ready_for_calculation": False,
        "next_action": "execute_and_hotload_current_revision_before_claiming_normal",
        "must_not_claim_normal_reasons": ["preview_not_hot_loaded"],
        "must_not_claim_live_gui_normal_reasons": [],
        "all_must_not_claim_reasons": ["preview_not_hot_loaded"],
        "review_reasons": ["semiconductor:calculation_settings_review_required"],
        "calculation_only_review_reasons": [
            "semiconductor:calculation_settings_review_required"
        ],
    }


def test_normality_decision_binds_gate_and_preserves_explanation_difference() -> None:
    report = {
        "project_id": "decision_unit",
        "revision": 4,
        "normality_gate": _gate(),
        "normality_explanation": {
            "status": "preview_ready",
            "primary_reason": "view:projection_overlaps",
            "next_action": "review_clean_view_candidates",
        },
    }

    decision = server._normality_decision(report)

    assert decision["schema_version"] == "material_studio_normality_decision_v1"
    assert decision["authoritative_source"] == "normality_gate"
    assert decision["project_id"] == "decision_unit"
    assert decision["revision"] == 4
    assert decision["binding_verified"] is True
    assert decision["status"] == report["normality_gate"]["status"]
    assert decision["primary_reason"] == "preview_not_hot_loaded"
    assert decision["can_claim_model_normal"] is False
    assert decision["can_claim_live_gui_normal"] is False
    assert decision["explanation"] == report["normality_explanation"]
    assert decision["explanation_primary_reason_differs"] is True
    assert decision["explanation_next_action_differs"] is True
    assert decision["consistency"] == {
        "gate_available": True,
        "project_revision_bound": True,
        "required_fields_present": True,
        "mirrors_normality_gate": True,
        "ok": True,
    }


def test_normality_decision_fails_closed_without_revision_binding() -> None:
    decision = server._normality_decision(
        {
            "normality_gate": _gate(),
            "normality_explanation": {},
        }
    )

    assert decision["available"] is True
    assert decision["binding_verified"] is False
    assert decision["consistency"]["mirrors_normality_gate"] is True
    assert decision["consistency"]["ok"] is False


def test_chinese_preview_exposes_authoritative_normality_decision_everywhere(
    tmp_path: Path,
) -> None:
    request = (
        "构建硅晶体并准备预览，导出正视、俯视和等轴测视角参数并检查模型是否正常。"
    )

    result = server.material_studio_live_modeling_request(
        request,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=True,
        views=["front", "top", "isometric"],
        working_dir=str(tmp_path),
        response_mode="full",
    )

    assert result["ok"] is True
    assert result["user_request"] == request
    assert result["nl_plan"]["kind"] == "spec"
    assert result["nl_plan"]["template_id"] == "silicon_diamond"
    assert result["diagnostic_export_requested"] is True
    assert result["normality_check_requested"] is True
    assert result.get("runner_invoked") in {None, False}
    assert result.get("structure_materialization_started") in {None, False}
    assert result.get("gui_input_started") in {None, False}
    assert result.get("gui_open") is None
    assert not Path(result["planned_outputs"]["structure"]).exists()

    decision = result["normality_decision"]
    gate = result["normality_gate"]
    assert decision["schema_version"] == "material_studio_normality_decision_v1"
    assert decision["project_id"] == result["project_id"]
    assert decision["revision"] == result["revision"]
    assert decision["binding_verified"] is True
    assert decision["consistency"]["ok"] is True
    for key in (
        "status",
        "primary_reason",
        "can_claim_model_normal",
        "can_claim_live_gui_normal",
        "ready_for_next_edit",
        "ready_for_calculation",
        "next_action",
    ):
        assert decision[key] == gate[key]

    report = result["modeling_report"]
    assert report["normality_decision"] == decision
    assert report["live_summary"]["normality_decision"] == decision
    assert report["change_receipt"]["normality_decision"] == decision
    assert report["mcp_client_readiness"]["normality_decision"] == decision
    assert result["normality_decision_primary_reason"] == decision["primary_reason"]
    assert result["live_summary"]["normality_decision_primary_reason"] == (
        decision["primary_reason"]
    )

    summary_path = Path(result["view_bundle_files"]["modeling_report_summary_csv"])
    with summary_path.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["normality_decision_schema_version"] == (
        "material_studio_normality_decision_v1"
    )
    assert row["normality_decision_authoritative_source"] == "normality_gate"
    assert row["normality_decision_primary_reason"] == decision["primary_reason"]
    assert row["normality_decision_consistency_ok"] == "True"

    compact = server._compact_live_response(result, "compact")
    assert compact["normality_decision"]["schema_version"] == (
        "material_studio_normality_decision_v1"
    )
    assert compact["normality_decision"]["primary_reason"] == decision[
        "primary_reason"
    ]
    assert compact["live_summary"]["normality_decision_primary_reason"] == (
        decision["primary_reason"]
    )
    assert len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) < 48_000


def test_watchdog_prefers_authoritative_decision_with_gate_fallback() -> None:
    report = {
        "project_id": "watchdog_decision",
        "revision": 2,
        "normality_gate": {
            **_gate(),
            "normality": "preview_ready",
            "trust_level": "preview",
            "hot_loaded": False,
            "gui_loaded_current_revision": False,
            "trusted_clean_view_replay_ok": False,
            "calculation_blocking_reasons": [
                "semiconductor:calculation_settings_review_required"
            ],
            "resolved_visual_review_reasons": [],
        },
        "normality_explanation": {},
    }
    decision = server._normality_decision(report)

    receipt = server._watchdog_normality_receipt(
        decision,
        fallback_gate=report["normality_gate"],
    )

    assert receipt["schema_version"] == "material_studio_normality_decision_v1"
    assert receipt["uses_authoritative_decision"] is True
    assert receipt["binding_verified"] is True
    assert receipt["primary_reason"] == decision["primary_reason"]
    assert receipt["normality"] == "preview_ready"
    assert receipt["detail_ref"] == (
        "material_studio_live_project_status.normality_decision"
    )
