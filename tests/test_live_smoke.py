from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server import live_smoke
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.server import _explicit_live_gui_open_requested, _explicit_live_hotload_requested
from material_studio_mcp_server.specs.common import ExecutionMode


def _fake_bundle_files(tmp_path: Path, keys: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for key in keys:
        path = tmp_path / f"{key}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
        files[key] = str(path)
    return files


def test_default_follow_up_request_for_semiconductor_scenario() -> None:
    request = live_smoke.default_follow_up_request_for_scenario("silicon", "p_dopant")

    assert "P dopant" in request
    assert "hot-load" in request
    assert "dopant diagnostics" in request


def test_default_gaas_scenario_and_follow_up_presets() -> None:
    request = live_smoke.default_request_for_scenario("gaas", hotload=True)
    dopant = live_smoke.default_follow_up_request_for_scenario("gaas", "si_ga_dopant")
    vacancy = live_smoke.default_follow_up_request_for_scenario("gaas", "as_vacancy")

    assert "GaAs zinc blende" in request
    assert "hot-load" in request
    assert "Si_Ga dopant" in dopant
    assert "dopant diagnostics" in dopant
    assert "As vacancy" in vacancy
    assert "defect diagnostics" in vacancy


def test_default_silicon_pn_junction_scenario() -> None:
    preview = live_smoke.default_request_for_scenario("silicon_pn_junction")
    hotload = live_smoke.default_request_for_scenario("silicon_pn_junction", hotload=True)

    assert "silicon p-n junction" in preview
    assert "doping diagnostics" in preview
    assert "hot-load" in hotload
    assert "doping diagnostics" in hotload


def test_default_diamond_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("diamond")
    hotload = live_smoke.default_request_for_scenario("diamond", hotload=True)
    dopant = live_smoke.default_follow_up_request_for_scenario("diamond", "b_dopant")
    vacancy = live_smoke.default_follow_up_request_for_scenario("diamond", "c_vacancy")

    assert "diamond semiconductor crystal" in preview
    assert "hot-load" in hotload
    assert "B dopant" in dopant
    assert "dopant diagnostics" in dopant
    assert "C vacancy" in vacancy
    assert "defect diagnostics" in vacancy


def test_default_p_gan_hemt_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("p_gan_hemt")
    hotload = live_smoke.default_request_for_scenario("p_gan_hemt", hotload=True)
    thickness = live_smoke.default_follow_up_request_for_scenario("p_gan_hemt", "gate_thickness_2nm")

    assert "p-GaN gate AlGaN/GaN HEMT" in preview
    assert "p-GaN gate diagnostics" in preview
    assert "hot-load" in hotload
    assert "2 nm" in thickness
    assert "p-GaN gate diagnostics" in thickness


def test_default_gan_sapphire_interface_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("gan_sapphire_interface")
    hotload = live_smoke.default_request_for_scenario("gan_sapphire_interface", hotload=True)
    gap = live_smoke.default_follow_up_request_for_scenario(
        "gan_sapphire_interface",
        "interface_gap_2p5",
    )

    assert "GaN on sapphire interface scaffold" in preview
    assert "interface scaffold diagnostics" in preview
    assert "hot-load" in hotload
    assert "2.5 angstrom" in gap
    assert "interface scaffold diagnostics" in gap


def test_default_gan_sapphire_interface_cjk_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("gan_sapphire_interface_cjk")
    hotload = live_smoke.default_request_for_scenario("gan_sapphire_interface_cjk", hotload=True)
    gap = live_smoke.default_follow_up_request_for_scenario(
        "gan_sapphire_interface_cjk",
        "interface_gap_2p5",
    )

    assert "\u6c2e\u5316\u9553" in preview
    assert "\u84dd\u5b9d\u77f3" in preview
    assert "\u754c\u9762\u6a21\u578b" in preview
    assert "\u70ed\u52a0\u8f7d" in hotload
    assert "Materials Studio" in hotload
    assert "2.5 \u57c3" in gap
    assert "\u754c\u9762\u95f4\u8ddd" in gap


def test_chinese_silicon_surface_alias_routes_to_slab_template() -> None:
    plan = infer_modeling_plan("\u6784\u5efa\u7845\u8868\u9762\u5e76\u70ed\u52a0\u8f7d\u5230 Materials Studio\u3002")

    assert plan.kind == "spec"
    assert plan.template_id == "silicon_100_slab"


def test_current_window_hotload_alias_is_recognized() -> None:
    assert _explicit_live_gui_open_requested("把模型推到当前窗口并热加载到 Materials Studio")


def test_same_window_realtime_hotload_variant_is_recognized() -> None:
    assert _explicit_live_hotload_requested("same-window real-time hot load")


def test_same_window_hotload_and_export_alias_is_recognized() -> None:
    request = "Push it to the current Materials Studio window and export view parameters."

    assert _explicit_live_hotload_requested(request)
    assert _explicit_live_gui_open_requested(request)


def test_default_aln_sapphire_interface_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("aln_sapphire_interface")
    hotload = live_smoke.default_request_for_scenario("aln_sapphire_interface", hotload=True)
    gap = live_smoke.default_follow_up_request_for_scenario(
        "aln_sapphire_interface",
        "interface_gap_2p5",
    )

    assert "AlN on sapphire interface scaffold" in preview
    assert "interface scaffold diagnostics" in preview
    assert "hot-load" in hotload
    assert "2.5 angstrom" in gap
    assert "interface scaffold diagnostics" in gap


def test_default_aln_sapphire_interface_cjk_scenario_and_follow_up_presets() -> None:
    preview = live_smoke.default_request_for_scenario("aln_sapphire_interface_cjk")
    hotload = live_smoke.default_request_for_scenario("aln_sapphire_interface_cjk", hotload=True)
    gap = live_smoke.default_follow_up_request_for_scenario(
        "aln_sapphire_interface_cjk",
        "interface_gap_2p5",
    )

    assert "\u6c2e\u5316\u94dd" in preview
    assert "\u84dd\u5b9d\u77f3" in preview
    assert "\u754c\u9762\u6a21\u578b" in preview
    assert "\u70ed\u52a0\u8f7d" in hotload
    assert "Materials Studio" in hotload
    assert "2.5 \u57c3" in gap
    assert "\u754c\u9762\u95f4\u8ddd" in gap


def test_default_mapbi3_alloy_cjk_scenario_requires_full_view_and_semiconductor_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("mapbi3_alloy_cjk")
    hotload = live_smoke.default_request_for_scenario("mapbi3_alloy_cjk", hotload=True)
    expectation = live_smoke.SCENARIO_EXPECTATIONS["mapbi3_alloy_cjk"]

    assert "MAPbI3" in preview
    assert "33%" in preview
    assert "\u5404\u4e2a\u89c6\u89d2\u6a21\u578b\u53c2\u6570" in preview
    assert "\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38" in preview
    assert "\u70ed\u52a0\u8f7d\u5230\u5f53\u524d Materials Studio \u7a97\u53e3" in hotload
    assert expectation["row_counts"]["semiconductor_alloy"] == 1
    assert expectation["row_counts"]["semiconductor_normality_diagnosis"] == 1
    assert expectation["row_counts"]["view_summary"] == 7
    assert expectation["row_counts"]["view_projections"] == 84
    assert "semiconductor_alloy_csv" in expectation["files"]
    assert "semiconductor_normality_diagnosis_csv" in expectation["files"]


def test_default_beta_ga2o3_contact_scenario_requires_contact_surface_and_view_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("beta_ga2o3_contact")
    hotload = live_smoke.default_request_for_scenario("beta_ga2o3_contact", hotload=True)
    expectation = live_smoke.SCENARIO_EXPECTATIONS["beta_ga2o3_contact"]

    assert "Au/beta-Ga2O3(010) Schottky contact" in preview
    assert "contact and view diagnostics" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "check whether the model is normal" in hotload
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["beta_ga2o3_contact"] == (
        "metal_beta_gallium_oxide_010_schottky_contact"
    )
    assert expectation["row_counts"]["semiconductor_contact"] == 2
    assert expectation["row_counts"]["semiconductor_surface_polarity"] == 1
    assert expectation["row_counts"]["view_quality"] == 1
    assert "semiconductor_contact_csv" in expectation["files"]
    assert "semiconductor_surface_polarity_csv" in expectation["files"]


def test_default_sic_4h_contact_scenario_requires_contact_surface_and_view_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("sic_4h_contact")
    hotload = live_smoke.default_request_for_scenario("sic_4h_contact", hotload=True)
    expectation = live_smoke.SCENARIO_EXPECTATIONS["sic_4h_contact"]

    assert "Au/4H-SiC(0001) Si-face Schottky contact" in preview
    assert "contact and view diagnostics" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "check whether the model is normal" in hotload
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_contact"] == (
        "metal_silicon_carbide_4h_0001_schottky_contact"
    )
    assert expectation["row_counts"]["semiconductor_contact"] == 2
    assert expectation["row_counts"]["semiconductor_surface_polarity"] == 1
    assert expectation["row_counts"]["view_quality"] == 1
    assert "semiconductor_contact_csv" in expectation["files"]
    assert "semiconductor_surface_polarity_csv" in expectation["files"]


def test_live_smoke_custom_request_preserves_inferred_crystallographic_views(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        request="Build 4H-SiC crystal and export [0001], [100], and [010] crystallographic view parameters.",
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    expected_views = ["crystal_0001", "crystal_100", "crystal_010"]
    assert result["ok"] is True
    assert result["scenario"] is None
    assert result["effective_views"] == expected_views
    assert result["live"]["live_summary"]["live_request_requested_views"] == expected_views
    assert result["bundle"]["row_counts"]["view_summary"] == 3
    assert result["bundle"]["row_counts"]["view_projections"] == 24
    assert result["summary"]["scenario"] is None
    assert result["summary"]["view_bundle_row_counts"]["view_summary"] == 3
    assert result["summary"]["view_bundle_row_counts"]["view_projections"] == 24


def test_live_smoke_custom_request_preserves_inferred_crystal_plane_views(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        request=(
            "Build beta-Ga2O3 crystal and export view parameters normal to the "
            "(001) and (010) crystal planes."
        ),
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    expected_views = ["crystal_plane_001", "crystal_plane_010"]
    assert result["ok"] is True
    assert result["scenario"] is None
    assert result["live"]["nl_plan"]["template_id"] == "beta_gallium_oxide_monoclinic"
    assert result["effective_views"] == expected_views
    assert result["live"]["live_summary"]["live_request_requested_views"] == expected_views
    assert result["bundle"]["row_counts"]["view_summary"] == 2
    atom_count = result["live"]["view_audit"]["model"]["atom_count"]
    assert atom_count == 20
    assert result["bundle"]["row_counts"]["view_projections"] == atom_count * 2


def test_live_smoke_custom_request_preserves_inferred_surface_frame_views(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        request=(
            "Build beta-Ga2O3(010) surface slab and export surface-normal and two "
            "surface in-plane view parameters."
        ),
        scenario=None,
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    expected_views = ["surface_normal", "surface_in_plane_1", "surface_in_plane_2"]
    assert result["ok"] is True
    assert result["scenario"] is None
    assert result["live"]["nl_plan"]["template_id"] == "beta_gallium_oxide_010_slab"
    assert result["effective_views"] == expected_views
    assert result["live"]["live_summary"]["live_request_requested_views"] == expected_views
    assert result["bundle"]["row_counts"]["view_summary"] == 3
    assert result["bundle"]["row_counts"]["view_projections"] == 120


def test_live_smoke_cli_custom_request_has_no_implicit_scenario() -> None:
    parser = live_smoke._build_parser()
    args = parser.parse_args(["--request", "Build silicon crystal and export [001] view parameters."])

    assert args.scenario is None
    assert args.views is None


def test_run_live_smoke_summarizes_semiconductor_workflow(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_preflight(**kwargs):
        calls.append(("preflight", kwargs))
        assert kwargs["working_dir"] == str(tmp_path)
        assert kwargs["include_latest_project"] is True
        assert kwargs["include_gui_status"] is False
        return {
            "ok": True,
            "state": "ready_for_new_model",
            "recommended_tool": "material_studio_live_modeling_request",
            "blocking_reasons": [],
            "review_reasons": [],
        }

    def fake_live(user_request, **kwargs):
        calls.append(("live", user_request))
        assert user_request == "Build 4H-SiC MOS capacitor."
        assert kwargs["execution_mode"] is ExecutionMode.PREVIEW
        assert kwargs["working_dir"] == str(tmp_path)
        assert kwargs["take_snapshot"] is False
        assert kwargs["views"] == ["front", "top"]
        return {
            "ok": True,
            "workflow": "create",
            "project_id": "sic_mos_smoke",
            "new_revision": 0,
            "execution_mode": "preview",
            "execution_mode_source": "explicit_argument",
            "recommended_diagnostic_focuses": ["mos_gate_stack", "electronic_structure_preflight"],
            "unrequested_recommended_diagnostic_focuses": ["mos_gate_stack", "electronic_structure_preflight"],
            "planned_outputs": {"structure": str(tmp_path / "sic_mos.cif")},
            "view_bundle_manifest_path": str(manifest),
            "modeling_health": {"verdict": "ready_with_warnings"},
            "live_summary": {
                "semiconductor_template_id": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor",
                "recommended_diagnostic_focuses": ["mos_gate_stack", "electronic_structure_preflight"],
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
                "hot_loaded": False,
                "next_action_tool": "material_studio_gui_apply_current_revision",
            },
            "live_request_summary": {
                "state": "ready_for_hotload",
                "explicit_hotload_requested": False,
                "hotload_safe_to_attempt": True,
                "recommended_tool": "material_studio_gui_apply_current_revision",
            },
            "live_hotload_preflight": {
                "status": "ready_to_execute_and_hotload_unverified_gui",
                "safe_to_attempt_hotload": True,
                "gui_preflight_verified": False,
                "current_revision_loaded": False,
                "recommended_tool": "material_studio_gui_apply_current_revision",
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {
                    "status": "preview_only",
                    "can_claim_model_normal": False,
                    "can_claim_live_gui_normal": False,
                },
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
                "diagnostics": {
                    "report_json_path": str(tmp_path / "report.json"),
                    "view_bundle_manifest_path": str(manifest),
                },
            },
        }

    def fake_status(project_id, **kwargs):
        calls.append(("status", project_id))
        assert project_id == "sic_mos_smoke"
        assert kwargs["include_gui_status"] is False
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "live_summary": {
                "semiconductor_template_id": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor",
                "ready_for_next_edit": True,
                "next_action_tool": "material_studio_gui_apply_current_revision",
            },
            "live_request_summary": {
                "state": "ready_for_hotload",
                "explicit_hotload_requested": False,
                "hotload_safe_to_attempt": True,
                "recommended_tool": "material_studio_gui_apply_current_revision",
            },
            "live_hotload_preflight": {
                "status": "ready_to_execute_and_hotload_unverified_gui",
                "safe_to_attempt_hotload": True,
                "gui_preflight_verified": False,
                "current_revision_loaded": False,
                "recommended_tool": "material_studio_gui_apply_current_revision",
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {
                    "status": "preview_only",
                    "can_claim_model_normal": False,
                    "can_claim_live_gui_normal": False,
                },
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_bundle(project_id, **kwargs):
        calls.append(("bundle", project_id))
        assert project_id == "sic_mos_smoke"
        assert kwargs["include_gui_snapshot"] is False
        return {
            "ok": True,
            "manifest_path": str(manifest),
            "files": {"modeling_report_summary_csv": str(tmp_path / "summary.csv")},
            "row_counts": {"modeling_report_summary": 1},
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        request="Build 4H-SiC MOS capacitor.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
        views=["front", "top"],
    )

    assert result["ok"] is True
    assert [name for name, _ in calls] == ["preflight", "live", "status", "bundle"]
    summary = result["summary"]
    assert summary["project_id"] == "sic_mos_smoke"
    assert summary["semiconductor_template_id"] == "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor"
    assert summary["recommended_diagnostic_focuses"] == ["mos_gate_stack", "electronic_structure_preflight"]
    assert summary["normality_gate_status"] == "preview_only"
    assert summary["can_claim_model_normal"] is False
    assert summary["gui_hot_loaded"] is False
    assert summary["live_request_state"] == "ready_for_hotload"
    assert summary["live_request_hotload_safe_to_attempt"] is True
    assert summary["live_request_recommended_tool"] == "material_studio_gui_apply_current_revision"
    assert summary["live_hotload_preflight_status"] == "ready_to_execute_and_hotload_unverified_gui"
    assert summary["live_hotload_preflight_safe_to_attempt"] is True
    assert summary["live_hotload_preflight_gui_verified"] is False
    assert summary["hotload_acceptance"]["available"] is False
    assert summary["hotload_acceptance"]["reason"] == "hotload_not_requested"
    assert summary["gui_hotload_gate_status"] == "ready_to_attempt"
    assert summary["gui_hotload_gate_ok"] is True
    assert summary["gui_hotload_gate_recommended_tool"] == "material_studio_gui_apply_current_revision"
    assert summary["gui_hotload_gate_blocking_reasons"] == []
    assert summary["view_bundle_manifest_exists"] is True
    assert summary["view_bundle_row_counts"] == {"modeling_report_summary": 1}
    assert summary["scenario_expected_diagnostics"]["available"] is False
    assert summary["scenario_expected_diagnostics"]["reason"] == "no_scenario"
    assert summary["next_action_tool"] == "material_studio_gui_apply_current_revision"


def test_live_smoke_cli_writes_compact_json(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "smoke.json"

    def fake_run_live_smoke(**kwargs):
        assert kwargs["scenario"] == "mos2"
        assert kwargs["working_dir"] == str(tmp_path)
        assert kwargs["include_gui_status"] is False
        assert kwargs["take_snapshot"] is False
        assert kwargs["export_bundle"] is False
        return {
            "ok": True,
            "summary": {
                "project_id": "mos2_smoke",
                "semiconductor_template_id": "molybdenum_disulfide_2d_mos2_monolayer",
                "view_bundle_manifest_exists": True,
            },
            "preflight": {},
            "live": {},
            "status": {},
            "bundle": None,
        }

    monkeypatch.setattr(live_smoke, "run_live_smoke", fake_run_live_smoke)

    exit_code = live_smoke.main(
        [
            "--scenario",
            "mos2",
            "--working-dir",
            str(tmp_path),
            "--no-include-gui-status",
            "--no-take-snapshot",
            "--no-export-bundle",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["project_id"] == "mos2_smoke"
    assert payload["semiconductor_template_id"] == "molybdenum_disulfide_2d_mos2_monolayer"
    printed = json.loads(capsys.readouterr().out)
    assert printed == payload


def test_live_smoke_cli_passes_follow_up_preset(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "smoke.json"

    def fake_run_live_smoke(**kwargs):
        assert kwargs["scenario"] == "silicon"
        assert kwargs["follow_up_preset"] == "p_dopant"
        return {
            "ok": True,
            "summary": {
                "project_id": "si_live",
                "revision": 1,
                "follow_up_requested": True,
                "follow_up_preset": "p_dopant",
            },
            "preflight": {},
            "base_live": {},
            "live": {},
            "followup_live": {},
            "status": {},
            "bundle": None,
        }

    monkeypatch.setattr(live_smoke, "run_live_smoke", fake_run_live_smoke)

    exit_code = live_smoke.main(
        [
            "--scenario",
            "silicon",
            "--follow-up-preset",
            "p_dopant",
            "--working-dir",
            str(tmp_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["follow_up_requested"] is True
    assert payload["follow_up_preset"] == "p_dopant"
    printed = json.loads(capsys.readouterr().out)
    assert printed == payload


def test_run_live_smoke_can_run_follow_up_live_edit(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_preflight(**kwargs):
        calls.append(("preflight", kwargs))
        return {
            "ok": True,
            "state": "ready_for_new_model",
            "recommended_tool": "material_studio_live_modeling_request",
            "blocking_reasons": [],
            "review_reasons": [],
        }

    def fake_live(user_request, **kwargs):
        calls.append(("live", user_request))
        common = {
            "ok": True,
            "project_id": "si_live",
            "execution_mode": "execute",
            "execution_mode_source": "explicit_live_intent",
            "view_bundle_manifest_path": str(manifest),
            "modeling_health": {"verdict": "passed_with_warnings"},
            "live_request_summary": {
                "state": "current_revision_loaded",
                "explicit_hotload_requested": True,
                "hotload_safe_to_attempt": False,
                "recommended_tool": "material_studio_live_modeling_request",
            },
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "safe_to_attempt_hotload": False,
                "gui_preflight_verified": True,
                "current_revision_loaded": True,
                "recommended_tool": "material_studio_live_modeling_request",
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "hot_loaded_and_passed",
                "normality_gate": {
                    "status": "visual_review_required",
                    "can_claim_model_normal": False,
                    "can_claim_live_gui_normal": False,
                },
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
                "diagnostics": {"view_bundle_manifest_path": str(manifest)},
            },
        }
        if user_request.startswith("Build silicon"):
            return {
                **common,
                "workflow": "create",
                "revision": 0,
                "new_revision": 0,
                "live_summary": {
                    "semiconductor_template_id": "silicon_diamond",
                    "ready_for_next_edit": True,
                    "ready_for_calculation": True,
                    "hot_loaded": True,
                    "next_action_tool": "material_studio_live_modeling_request",
                },
            }
        assert user_request.startswith("Make it n-type")
        assert kwargs["execution_mode"] is None
        return {
            **common,
            "workflow": "patch",
            "base_revision": 0,
            "revision": 1,
            "new_revision": 1,
            "nl_plan": {"kind": "patch", "template_id": "crystal_auto_dopant"},
            "live_summary": {
                "formula": "PSi15",
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
                "hot_loaded": True,
                "next_action_tool": "material_studio_live_modeling_request",
            },
        }

    def fake_status(project_id, **kwargs):
        calls.append(("status", project_id))
        assert project_id == "si_live"
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 1,
            "live_summary": {
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
                "hot_loaded": True,
                "next_action_tool": "material_studio_live_modeling_request",
            },
            "live_request_summary": {
                "state": "current_revision_loaded",
                "explicit_hotload_requested": True,
                "hotload_safe_to_attempt": False,
                "recommended_tool": "material_studio_live_modeling_request",
            },
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "safe_to_attempt_hotload": False,
                "gui_preflight_verified": True,
                "current_revision_loaded": True,
                "recommended_tool": "material_studio_live_modeling_request",
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "hot_loaded_and_passed",
                "normality_gate": {
                    "status": "visual_review_required",
                    "can_claim_model_normal": False,
                    "can_claim_live_gui_normal": False,
                },
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        }

    def fake_bundle(project_id, **kwargs):
        calls.append(("bundle", project_id))
        row_counts = {
            "semiconductor_dopants": 1,
            "semiconductor_dopant_sites": 1,
            "semiconductor_carrier_intents": 1,
            "semiconductor_finite_size": 1,
            "view_summary": 3,
            "view_quality": 3,
            "view_projections": 48,
        }
        return {
            "ok": True,
            "manifest_path": str(manifest),
            "files": _fake_bundle_files(
                tmp_path,
                [
                    "semiconductor_dopants_csv",
                    "semiconductor_dopant_sites_csv",
                    "semiconductor_carrier_intents_csv",
                    "semiconductor_finite_size_csv",
                ],
            ),
            "row_counts": row_counts,
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        request="Build silicon crystal and hot-load it in Materials Studio.",
        scenario="silicon",
        follow_up_preset="p_dopant",
        execution_mode="auto",
        working_dir=str(tmp_path),
        views=["front", "top", "isometric"],
    )

    assert result["ok"] is True
    assert [name for name, _ in calls] == ["preflight", "live", "live", "status", "bundle"]
    assert result["base_live"]["workflow"] == "create"
    assert result["followup_live"]["workflow"] == "patch"
    summary = result["summary"]
    assert summary["follow_up_requested"] is True
    assert summary["follow_up_preset"] == "p_dopant"
    assert summary["base_project_id"] == "si_live"
    assert summary["base_revision"] == 0
    assert summary["base_workflow"] == "create"
    assert summary["base_semiconductor_template_id"] == "silicon_diamond"
    assert summary["scenario_semiconductor_template_id"] == "silicon_diamond"
    assert summary["project_id"] == "si_live"
    assert summary["revision"] == 1
    assert summary["workflow"] == "patch"
    assert summary["gui_hot_loaded"] is True
    assert summary["current_revision_loaded_in_gui"] is True
    assert summary["loaded_current_revision"] is True
    assert summary["gui_loaded_current_revision"] is True
    assert summary["hotload_acceptance_ok"] is True
    assert summary["hotload_acceptance_failures"] == []
    assert summary["gui_hotload_gate_status"] == "accepted"
    assert summary["gui_hotload_gate_ok"] is True
    assert summary["gui_hotload_gate_blocking_reasons"] == []
    assert summary["view_bundle_row_counts"]["semiconductor_dopants"] == 1
    assert summary["view_bundle_row_counts"]["semiconductor_dopant_sites"] == 1
    assert summary["view_bundle_row_counts"]["view_projections"] == 48
    assert summary["follow_up_expected_diagnostics_ok"] is True
    assert summary["follow_up_expected_diagnostic_failures"] == []


def test_run_live_smoke_uses_gan_sapphire_interface_gap_follow_up(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_preflight(**kwargs):
        return {"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []}

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        common_gui = {
            "hot_loaded": True,
            "loaded_current_revision": True,
            "window_identity_verification": "verified",
            "single_window_policy_ok": True,
            "single_window_violation_reasons": [],
            "snapshot_viewport_likely_visible_model": True,
            "snapshot_viewport_capture_limitation_possible": False,
        }
        if len(calls) == 1:
            assert "GaN on sapphire interface scaffold" in user_request
            return {
                "ok": True,
                "workflow": "create",
                "project_id": "gan_sapphire_live",
                "revision": 0,
                "new_revision": 0,
                "execution_mode": "execute",
                "nl_plan": {"kind": "spec", "template_id": "alpha_alumina_sapphire_substrate"},
                "live_summary": {
                    "semiconductor_template_id": "alpha_alumina_sapphire_substrate",
                    "mcp_interface_scaffold_interface_gap_angstrom": 3.0,
                },
                "modeling_report": {"gui": common_gui, "diagnostics": {"view_bundle_manifest_path": str(manifest)}},
            }
        assert "2.5 angstrom" in user_request
        assert "interface scaffold diagnostics" in user_request
        return {
            "ok": True,
            "workflow": "patch",
            "project_id": "gan_sapphire_live",
            "base_revision": 0,
            "revision": 1,
            "new_revision": 1,
            "execution_mode": "execute",
            "nl_plan": {"kind": "patch", "template_id": "interface_scaffold_gap"},
            "live_summary": {
                "mcp_interface_scaffold_interface_gap_angstrom": 2.5,
                "ready_for_next_edit": True,
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "visual_review_required"},
                "gui": common_gui,
                "diagnostics": {"view_bundle_manifest_path": str(manifest)},
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 1,
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "current_revision_loaded": True,
                "safe_to_attempt_hotload": False,
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "visual_review_required"},
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        }

    def fake_bundle(project_id, **kwargs):
        return {
            "ok": True,
            "manifest_path": str(manifest),
            "files": _fake_bundle_files(tmp_path, ["semiconductor_interface_scaffold_csv"]),
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 3,
                "view_quality": 3,
                "view_projections": 48,
            },
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="gan_sapphire_interface",
        follow_up_preset="interface_gap_2p5",
        execution_mode="auto",
        working_dir=str(tmp_path),
        views=["front", "top", "isometric"],
    )

    assert result["ok"] is True
    assert "GaN on sapphire interface scaffold" in result["request"]
    assert "2.5 angstrom" in result["follow_up_request"]
    assert result["available_follow_up_presets"] == ["interface_gap_2p5"]
    summary = result["summary"]
    assert summary["scenario"] == "gan_sapphire_interface"
    assert summary["semiconductor_template_id"] == "interface_scaffold_gap"
    assert summary["base_workflow"] == "create"
    assert summary["base_nl_plan_template_id"] == "alpha_alumina_sapphire_substrate"
    assert summary["base_semiconductor_template_id"] == "alpha_alumina_sapphire_substrate"
    assert summary["base_semiconductor_virtual_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert summary["base_effective_semiconductor_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert summary["semiconductor_virtual_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert summary["scenario_semiconductor_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert summary["effective_semiconductor_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert summary["follow_up_expected_diagnostics_ok"] is True
    assert summary["follow_up_expected_diagnostic_failures"] == []
    assert summary["view_bundle_row_counts"]["semiconductor_interface_scaffold"] == 1


def test_run_live_smoke_preview_follow_up_does_not_require_hotload_acceptance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_preflight(**kwargs):
        return {"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []}

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        assert kwargs["execution_mode"] is ExecutionMode.PREVIEW
        common = {
            "ok": True,
            "project_id": "gan_sapphire_preview",
            "execution_mode": "preview",
            "execution_mode_source": "explicit_parameter",
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {
                    "hot_loaded": False,
                    "loaded_current_revision": False,
                    "window_identity_verification": "unverified",
                },
                "diagnostics": {"view_bundle_manifest_path": str(manifest)},
            },
        }
        if len(calls) == 1:
            return {
                **common,
                "workflow": "create",
                "revision": 0,
                "new_revision": 0,
                "nl_plan": {"kind": "spec", "template_id": "gallium_nitride_on_sapphire_interface_scaffold"},
                "live_summary": {
                    "semiconductor_template_id": "alpha_alumina_sapphire_substrate",
                },
            }
        return {
            **common,
            "workflow": "patch",
            "base_revision": 0,
            "revision": 1,
            "new_revision": 1,
            "nl_plan": {"kind": "patch", "template_id": "interface_scaffold_gap"},
            "live_summary": {"mcp_interface_scaffold_interface_gap_angstrom": 2.5},
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 1,
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {
                    "hot_loaded": False,
                    "loaded_current_revision": False,
                    "window_identity_verification": "unverified",
                },
            },
        }

    def fake_bundle(project_id, **kwargs):
        return {
            "ok": True,
            "manifest_path": str(manifest),
            "files": _fake_bundle_files(tmp_path, ["semiconductor_interface_scaffold_csv"]),
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 3,
                "view_quality": 3,
                "view_projections": 48,
            },
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="gan_sapphire_interface",
        follow_up_preset="interface_gap_2p5",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["summary"]["workflow"] == "patch"
    assert result["summary"]["revision"] == 1
    assert result["summary"]["base_semiconductor_template_id"] == "alpha_alumina_sapphire_substrate"
    assert result["summary"]["base_effective_semiconductor_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert result["summary"]["scenario_semiconductor_template_id"] == "gallium_nitride_on_sapphire_interface_scaffold"
    assert result["summary"]["hotload_acceptance"]["available"] is False
    assert result["summary"]["hotload_acceptance"]["reason"] == "hotload_not_requested"
    assert result["summary"]["follow_up_expected_diagnostics_ok"] is True


def test_run_live_smoke_uses_gaas_follow_up_preset(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_preflight(**kwargs):
        return {"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []}

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        if len(calls) == 1:
            assert "GaAs zinc blende" in user_request
            return {
                "ok": True,
                "workflow": "create",
                "project_id": "gaas_live",
                "revision": 0,
                "new_revision": 0,
                "execution_mode": "execute",
                "live_summary": {"semiconductor_template_id": "gallium_arsenide_zincblende"},
                "modeling_report": {
                    "gui": {
                        "hot_loaded": True,
                        "loaded_current_revision": True,
                        "window_identity_verification": "verified",
                        "single_window_policy_ok": True,
                        "single_window_violation_reasons": [],
                        "snapshot_viewport_likely_visible_model": True,
                        "snapshot_viewport_capture_limitation_possible": False,
                    }
                },
            }
        assert "Si_Ga dopant" in user_request
        return {
            "ok": True,
            "workflow": "patch",
            "project_id": "gaas_live",
            "base_revision": 0,
            "revision": 1,
            "new_revision": 1,
            "execution_mode": "execute",
            "nl_plan": {"kind": "patch", "template_id": "crystal_sublattice_dopant"},
            "live_summary": {"ready_for_next_edit": True},
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 1,
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "current_revision_loaded": True,
                "safe_to_attempt_hotload": False,
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        }

    def fake_bundle(project_id, **kwargs):
        return {
            "ok": True,
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 3,
                "view_quality": 3,
                "view_projections": 24,
            },
            "files": _fake_bundle_files(
                tmp_path,
                [
                    "semiconductor_dopants_csv",
                    "semiconductor_dopant_sites_csv",
                    "semiconductor_carrier_intents_csv",
                    "semiconductor_finite_size_csv",
                ],
            ),
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="gaas",
        hotload=True,
        follow_up_preset="si_ga_dopant",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert result["follow_up_request"] == live_smoke.default_follow_up_request_for_scenario("gaas", "si_ga_dopant")
    summary = result["summary"]
    assert summary["base_project_id"] == "gaas_live"
    assert summary["base_revision"] == 0
    assert summary["revision"] == 1
    assert summary["workflow"] == "patch"
    assert summary["follow_up_preset"] == "si_ga_dopant"
    assert summary["hotload_acceptance_ok"] is True
    assert summary["view_bundle_row_counts"]["semiconductor_dopant_sites"] == 1
    assert summary["follow_up_expected_diagnostics_ok"] is True


def test_run_live_smoke_fails_hotload_acceptance_when_gui_not_current(monkeypatch, tmp_path: Path) -> None:
    def fake_preflight(**kwargs):
        return {"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []}

    def fake_live(user_request, **kwargs):
        return {
            "ok": True,
            "workflow": "create",
            "project_id": "gaas_live",
            "revision": 0,
            "new_revision": 0,
            "execution_mode": "execute",
            "live_request_summary": {
                "state": "hotload_attempted",
                "explicit_hotload_requested": True,
                "hotload_safe_to_attempt": False,
            },
            "live_hotload_preflight": {
                "status": "blocked",
                "current_revision_loaded": False,
                "safe_to_attempt_hotload": False,
                "blocking_reasons": ["gui_current_revision_not_loaded"],
            },
            "nl_plan": {"kind": "spec", "template_id": "gallium_arsenide_zincblende"},
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": False,
                    "loaded_current_revision": False,
                    "window_identity_verification": "unverified",
                    "snapshot_viewport_likely_visible_model": False,
                    "snapshot_viewport_capture_limitation_possible": True,
                },
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": False,
                    "loaded_current_revision": False,
                    "window_identity_verification": "unverified",
                    "snapshot_viewport_likely_visible_model": False,
                    "snapshot_viewport_capture_limitation_possible": True,
                },
                "live_hotload_preflight": {
                    "status": "blocked",
                    "current_revision_loaded": False,
                    "safe_to_attempt_hotload": False,
                    "blocking_reasons": ["gui_current_revision_not_loaded"],
                },
            },
        }

    def fake_bundle(project_id, **kwargs):
        return {
            "ok": True,
            "row_counts": {
                "semiconductor_lattice": 1,
                "semiconductor_composition": 2,
                "semiconductor_local_environment": 8,
                "semiconductor_neighbor_pairs": 1,
                "semiconductor_reciprocal_lattice": 3,
                "semiconductor_band_path": 10,
                "semiconductor_calculation_preflight": 1,
                "semiconductor_calculation_readiness": 1,
                "view_summary": 7,
                "view_quality": 7,
                "view_projections": 56,
            },
            "files": _fake_bundle_files(tmp_path, list(live_smoke.SCENARIO_EXPECTATIONS["gaas"]["files"])),
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="gaas",
        hotload=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    acceptance = result["summary"]["hotload_acceptance"]
    assert acceptance["ok"] is False
    failure_types = {failure["type"] for failure in acceptance["failures"]}
    assert "gui_not_hot_loaded" in failure_types
    assert "gui_current_revision_not_loaded" in failure_types
    assert "hotload_preflight_current_revision_not_loaded" in failure_types
    assert "gui_window_identity_not_verified" in failure_types
    assert "snapshot_viewport_model_not_visible" in failure_types
    assert result["summary"]["current_revision_loaded_in_gui"] is False
    assert result["summary"]["loaded_current_revision"] is False


def test_live_smoke_accepts_current_revision_loaded_alias_from_live_summary() -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "gaas_live",
            "revision": 0,
            "new_revision": 0,
            "execution_mode": "execute",
            "live_summary": {
                "hot_loaded": True,
                "current_revision_loaded_in_gui": True,
                "loaded_current_revision": True,
                "mcp_single_window_policy_ok": True,
            },
            "live_request_summary": {
                "state": "current_revision_loaded",
                "explicit_hotload_requested": True,
                "hotload_safe_to_attempt": False,
            },
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "current_revision_loaded": True,
                "safe_to_attempt_hotload": False,
                "blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "hot_loaded_and_passed",
                "normality_gate": {"status": "visual_review_required"},
                "gui": {
                    "hot_loaded": True,
                    "window_identity_verification": "verified",
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        },
        status={"ok": True, "project_id": "gaas_live", "revision": 0},
        bundle={"ok": True, "row_counts": {}, "files": {}},
        scenario="gaas",
        hotload_expected=True,
        snapshot_expected=True,
    )

    assert summary["current_revision_loaded_in_gui"] is True
    assert summary["loaded_current_revision"] is True
    assert summary["gui_loaded_current_revision"] is True
    assert summary["hotload_acceptance_ok"] is True
    assert summary["hotload_acceptance_failures"] == []


def test_live_smoke_hotload_acceptance_fails_single_window_violation() -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "gaas_live",
            "revision": 0,
            "new_revision": 0,
            "execution_mode": "execute",
            "live_request_summary": {
                "state": "current_revision_loaded",
                "explicit_hotload_requested": True,
                "hotload_safe_to_attempt": False,
            },
            "live_hotload_preflight": {
                "status": "current_revision_loaded",
                "current_revision_loaded": True,
                "safe_to_attempt_hotload": False,
                "blocking_reasons": [],
            },
            "nl_plan": {"kind": "spec", "template_id": "gallium_arsenide_zincblende"},
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "verified",
                    "single_window_policy_ok": False,
                    "single_window_violation_reasons": ["multiple_matstudio_windows_detected"],
                    "snapshot_viewport_likely_visible_model": True,
                    "snapshot_viewport_capture_limitation_possible": False,
                },
            },
        },
        status={"ok": True, "project_id": "gaas_live", "revision": 0},
        bundle={"ok": True, "row_counts": {}, "files": {}},
        scenario="gaas",
        hotload_expected=True,
    )

    acceptance = summary["hotload_acceptance"]
    assert acceptance["ok"] is False
    assert summary["single_window_policy_ok"] is False
    assert summary["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]
    assert {
        "type": "single_window_policy_not_verified",
        "observed": False,
        "reasons": ["multiple_matstudio_windows_detected"],
    } in acceptance["failures"]
    assert summary["gui_hotload_gate_status"] == "blocked"
    assert summary["gui_hotload_gate_ok"] is False
    assert summary["gui_hotload_gate_recommended_tool"] == "material_studio_gui_status"
    assert "multiple_matstudio_windows_detected" in summary["gui_hotload_gate_blocking_reasons"]
    assert "single_window_policy_not_verified" in summary["gui_hotload_gate_blocking_reasons"]
    assert acceptance["observed"]["single_window_policy_ok"] is False
    assert acceptance["observed"]["single_window_violation_reasons"] == ["multiple_matstudio_windows_detected"]


def test_run_live_smoke_checks_silicon_pn_junction_diagnostics(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_preflight(**kwargs):
        return {"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []}

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        assert "silicon p-n junction" in user_request
        assert "doping diagnostics" in user_request
        return {
            "ok": True,
            "workflow": "create",
            "project_id": "si_pn_live",
            "revision": 0,
            "new_revision": 0,
            "execution_mode": "preview",
            "nl_plan": {"kind": "create", "template_id": "silicon_pn_junction"},
            "requested_diagnostic_focuses": ["pn_junction_and_doping", "view_quality"],
            "requested_diagnostic_focus_ok": True,
            "live_summary": {
                "semiconductor_template_id": "silicon_pn_junction",
                "semiconductor_pn_junction_count": 1,
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "live_summary": {
                "semiconductor_template_id": "silicon_pn_junction",
                "semiconductor_pn_junction_count": 1,
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_bundle(project_id, **kwargs):
        return {
            "ok": True,
            "row_counts": {
                "semiconductor_junctions": 1,
                "semiconductor_dopants": 2,
                "semiconductor_dopant_sites": 2,
                "semiconductor_finite_size": 1,
                "requested_diagnostic_focus_status": 2,
                "view_summary": 7,
                "view_quality": 7,
                "view_projections": 112,
            },
            "files": _fake_bundle_files(
                tmp_path,
                [
                    "semiconductor_junctions_csv",
                    "semiconductor_dopants_csv",
                    "semiconductor_dopant_sites_csv",
                    "semiconductor_finite_size_csv",
                    "requested_diagnostic_focus_status_json",
                ],
            ),
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="silicon_pn_junction",
        execution_mode="preview",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert calls == [live_smoke.default_request_for_scenario("silicon_pn_junction")]
    summary = result["summary"]
    assert summary["project_id"] == "si_pn_live"
    assert summary["scenario"] == "silicon_pn_junction"
    assert summary["nl_plan_template_id"] == "silicon_pn_junction"
    assert summary["semiconductor_template_id"] == "silicon_pn_junction"
    assert summary["scenario_expected_diagnostics_ok"] is True
    assert summary["scenario_expected_diagnostic_failures"] == []
    assert summary["view_bundle_row_counts"]["semiconductor_junctions"] == 1
    assert summary["view_bundle_row_counts"]["semiconductor_dopants"] == 2


def test_run_live_smoke_uses_diamond_scenario_expectations(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_preflight(**kwargs):
        return {
            "ok": True,
            "state": "ready_for_new_model",
            "recommended_tool": "material_studio_live_modeling_request",
            "blocking_reasons": [],
            "review_reasons": [],
        }

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        assert user_request == live_smoke.default_request_for_scenario("diamond")
        return {
            "ok": True,
            "workflow": "create",
            "project_id": "diamond_live",
            "revision": 0,
            "new_revision": 0,
            "nl_plan": {"kind": "spec", "template_id": "diamond_cubic"},
            "live_summary": {
                "semiconductor_template_id": "diamond_cubic",
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "live_summary": {
                "semiconductor_template_id": "diamond_cubic",
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_bundle(project_id, **kwargs):
        expectation = live_smoke.SCENARIO_EXPECTATIONS["diamond"]
        return {
            "ok": True,
            "row_counts": {key: max(1, int(value)) for key, value in expectation["row_counts"].items()},
            "files": _fake_bundle_files(tmp_path, list(expectation["files"])),
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="diamond",
        execution_mode="preview",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert calls == [live_smoke.default_request_for_scenario("diamond")]
    assert result["available_follow_up_presets"] == ["b_dopant", "c_vacancy"]
    summary = result["summary"]
    assert summary["project_id"] == "diamond_live"
    assert summary["scenario"] == "diamond"
    assert summary["nl_plan_template_id"] == "diamond_cubic"
    assert summary["semiconductor_template_id"] == "diamond_cubic"
    assert summary["scenario_expected_diagnostics_ok"] is True
    assert summary["scenario_expected_diagnostic_failures"] == []
    assert summary["view_bundle_row_counts"]["semiconductor_lattice"] == 1
    assert summary["view_bundle_row_counts"]["semiconductor_band_path"] == 1


def test_run_live_smoke_uses_p_gan_hemt_scenario_expectations(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_preflight(**kwargs):
        return {
            "ok": True,
            "state": "ready_for_new_model",
            "recommended_tool": "material_studio_live_modeling_request",
            "blocking_reasons": [],
            "review_reasons": [],
        }

    def fake_live(user_request, **kwargs):
        calls.append(user_request)
        assert user_request == live_smoke.default_request_for_scenario("p_gan_hemt")
        return {
            "ok": True,
            "workflow": "create",
            "project_id": "p_gan_hemt_live",
            "revision": 0,
            "new_revision": 0,
            "nl_plan": {
                "kind": "spec",
                "template_id": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure",
            },
            "live_summary": {
                "semiconductor_template_id": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure",
                "semiconductor_p_gan_gate_cap_quality": "complete",
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_status(project_id, **kwargs):
        return {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "live_summary": {
                "semiconductor_template_id": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure",
                "semiconductor_p_gan_gate_cap_quality": "complete",
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        }

    def fake_bundle(project_id, **kwargs):
        expectation = live_smoke.SCENARIO_EXPECTATIONS["p_gan_hemt"]
        return {
            "ok": True,
            "row_counts": {key: max(1, int(value)) for key, value in expectation["row_counts"].items()},
            "files": _fake_bundle_files(tmp_path, list(expectation["files"])),
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_live_session_preflight", fake_preflight)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_live_project_status", fake_status)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_export_view_bundle", fake_bundle)

    result = live_smoke.run_live_smoke(
        scenario="p_gan_hemt",
        execution_mode="preview",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert calls == [live_smoke.default_request_for_scenario("p_gan_hemt")]
    assert result["available_follow_up_presets"] == ["gate_thickness_2nm"]
    summary = result["summary"]
    assert summary["project_id"] == "p_gan_hemt_live"
    assert summary["scenario"] == "p_gan_hemt"
    assert summary["nl_plan_template_id"] == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
    assert summary["semiconductor_template_id"] == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
    assert summary["semiconductor_virtual_template_id"] == (
        "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_p_gan_gate"
    )
    assert summary["scenario_expected_diagnostics_ok"] is True
    assert summary["scenario_expected_diagnostic_failures"] == []
    assert summary["view_bundle_row_counts"]["semiconductor_p_gan_gate_cap"] == 1
    assert summary["view_bundle_row_counts"]["semiconductor_polarization_2deg"] == 1
    assert summary["view_bundle_row_counts"]["semiconductor_dopant_sites"] == 1


def test_live_smoke_scenario_expectations_cover_builtin_semiconductor_diagnostics(tmp_path: Path) -> None:
    for scenario, expectation in live_smoke.SCENARIO_EXPECTATIONS.items():
        scenario_dir = tmp_path / scenario
        row_counts = {key: int(value) for key, value in expectation["row_counts"].items()}
        summary = live_smoke.build_live_smoke_summary(
            preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
            live={
                "ok": True,
                "workflow": "create",
                "project_id": f"{scenario}_live",
                "revision": 0,
                "new_revision": 0,
                "nl_plan": {"kind": "spec", "template_id": scenario},
                "modeling_report": {
                    "normality": "preview_ready",
                    "normality_gate": {"status": "preview_only"},
                    "gui": {"hot_loaded": False, "loaded_current_revision": False},
                },
            },
            status={"ok": True, "project_id": f"{scenario}_live", "revision": 0},
            bundle={
                "ok": True,
                "row_counts": row_counts,
                "files": _fake_bundle_files(scenario_dir, list(expectation["files"])),
            },
            scenario=scenario,
        )

        assert summary["scenario"] == scenario
        assert summary["scenario_expected_diagnostics_ok"] is True
        assert summary["scenario_expected_diagnostic_failures"] == []


def test_live_smoke_summary_exposes_calculation_only_normality_review(tmp_path: Path) -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "rollback",
            "project_id": "mos2_live",
            "revision": 2,
            "new_revision": 2,
            "execution_mode": "execute",
            "nl_plan": {"kind": "rollback", "template_id": "rollback_revision"},
            "live_summary": {
                "ready_for_next_edit": True,
                "ready_for_calculation": False,
                "calculation_only_review_reasons": ["semiconductor:kpoint_reciprocal_lattice_warnings"],
                "visual_normality_status": "model_normal_with_visual_notes",
                "visual_can_report_model_normal": True,
                "visual_clean_view_available": True,
                "visual_clean_view_count": 5,
                "visual_recommended_view_name": "isometric",
                "visual_note_reasons": ["view:projection_overlaps"],
                "visual_blocking_reasons": [],
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {
                    "status": "claimable_with_calculation_review",
                    "can_claim_model_normal": True,
                    "can_claim_live_gui_normal": True,
                    "next_action": "report_model_normal_but_review_calculation_settings_before_calculation",
                    "calculation_only_review_reasons": [
                        "semiconductor:kpoint_reciprocal_lattice_warnings"
                    ],
                    "calculation_blocking_reasons": [
                        "semiconductor:kpoint_reciprocal_lattice_warnings"
                    ],
                },
                "live_delivery": {
                    "status": "delivered_model_normal_calculation_review",
                    "calculation_review_required": True,
                },
                "live_modeling_contract": {
                    "status": "live_gui_normal_calculation_review",
                    "normality": {"calculation_review_required": True},
                },
                "gui": {"hot_loaded": True, "loaded_current_revision": True},
            },
        },
        status={"ok": True, "project_id": "mos2_live", "revision": 2},
        bundle={"ok": True, "row_counts": {}, "files": {}},
        scenario="mos2",
    )

    assert summary["normality_gate_status"] == "claimable_with_calculation_review"
    assert summary["can_claim_model_normal"] is True
    assert summary["can_claim_live_gui_normal"] is True
    assert summary["visual_normality_status"] == "model_normal_with_visual_notes"
    assert summary["visual_can_report_model_normal"] is True
    assert summary["visual_clean_view_available"] is True
    assert summary["visual_clean_view_count"] == 5
    assert summary["visual_recommended_view_name"] == "isometric"
    assert summary["visual_note_reasons"] == ["view:projection_overlaps"]
    assert summary["visual_blocking_reasons"] == []
    assert summary["ready_for_calculation"] is False
    assert summary["normality_gate_next_action"] == (
        "report_model_normal_but_review_calculation_settings_before_calculation"
    )
    assert summary["normality_gate_calculation_only_reasons"] == [
        "semiconductor:kpoint_reciprocal_lattice_warnings"
    ]
    assert summary["calculation_only_review_reasons"] == [
        "semiconductor:kpoint_reciprocal_lattice_warnings"
    ]
    assert summary["live_delivery_status"] == "delivered_model_normal_calculation_review"
    assert summary["live_delivery_calculation_review_required"] is True
    assert summary["live_modeling_contract_status"] == "live_gui_normal_calculation_review"
    assert summary["live_modeling_contract_calculation_review_required"] is True


def test_live_smoke_summary_falls_back_to_report_visual_normality() -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "mos2_live",
            "revision": 0,
            "new_revision": 0,
            "execution_mode": "execute",
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {
                    "status": "model_claimable_with_visual_notes",
                    "can_claim_model_normal": True,
                    "can_claim_live_gui_normal": False,
                },
                "visual_normality_summary": {
                    "status": "model_normal_with_visual_notes",
                    "can_report_model_normal": True,
                    "clean_view_available": True,
                    "clean_view_count": 5,
                    "recommended_view_name": "isometric",
                    "visual_note_reasons": ["view:projection_overlaps", "view:view_warnings"],
                    "blocking_reasons": [],
                },
                "gui": {"hot_loaded": True, "loaded_current_revision": True},
            },
        },
        status={"ok": True, "project_id": "mos2_live", "revision": 0},
        bundle={"ok": True, "row_counts": {}, "files": {}},
        scenario="mos2",
    )

    assert summary["visual_normality_status"] == "model_normal_with_visual_notes"
    assert summary["visual_can_report_model_normal"] is True
    assert summary["visual_clean_view_available"] is True
    assert summary["visual_clean_view_count"] == 5
    assert summary["visual_recommended_view_name"] == "isometric"
    assert summary["visual_note_reasons"] == ["view:projection_overlaps", "view:view_warnings"]
    assert summary["visual_blocking_reasons"] == []


def test_live_smoke_summary_exposes_single_window_binding_for_mcp() -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_live_edit", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "patch",
            "project_id": "mos2_live",
            "revision": 4,
            "new_revision": 4,
            "execution_mode": "execute",
            "live_summary": {
                "live_gui_window_binding": {
                    "same_window_required": True,
                    "auto_launch_allowed": False,
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                    "process_count": 1,
                    "window_count": 1,
                    "target_window_found": True,
                    "target_window_handle": 551130,
                    "target_window_title": "msmcp_r004_abc123 - Materials Studio",
                    "target_window_is_selected": True,
                    "can_hotload_without_new_window": True,
                    "can_apply_current_revision_without_new_window": True,
                },
                "mcp_current_revision_loaded_in_gui": True,
            },
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "claimable_with_calculation_review"},
                "mcp_client_readiness": {
                    "must_reuse_existing_gui_window": True,
                    "auto_launch_during_hotload_allowed": False,
                    "single_window_policy_ok": True,
                    "gui_process_count": 1,
                    "gui_window_count": 1,
                    "gui_target_window_found": True,
                    "gui_target_window_handle": 551130,
                    "gui_target_window_title": "msmcp_r004_abc123 - Materials Studio",
                    "gui_target_window_is_selected": True,
                    "can_accept_hotload_request_without_new_window": True,
                    "can_apply_current_revision_without_new_window": True,
                },
                "gui": {"hot_loaded": True, "loaded_current_revision": True},
            },
        },
        status={"ok": True, "project_id": "mos2_live", "revision": 4},
        bundle={"ok": True, "row_counts": {}, "files": {}},
        scenario="mos2",
    )

    assert summary["single_window_policy_ok"] is True
    assert summary["single_window_violation_reasons"] == []
    assert summary["same_window_required"] is True
    assert summary["auto_launch_during_hotload_allowed"] is False
    assert summary["can_hotload_without_new_window"] is True
    assert summary["can_apply_current_revision_without_new_window"] is True
    assert summary["gui_process_count"] == 1
    assert summary["gui_window_count"] == 1
    assert summary["gui_target_window_found"] is True
    assert summary["gui_target_window_handle"] == 551130
    assert summary["gui_target_window_title"] == "msmcp_r004_abc123 - Materials Studio"
    assert summary["gui_target_window_is_selected"] is True
    assert summary["current_revision_loaded_in_gui"] is True


def test_live_smoke_summary_exposes_diagnostic_acceptance_for_mcp(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    view_summary = tmp_path / "view_summary.csv"
    view_summary.write_text("view,row\nfront,1\n", encoding="utf-8")
    expected_files = {
        "view_summary_csv": view_summary,
        "semiconductor_lattice_csv": tmp_path / "semiconductor_lattice.csv",
        "semiconductor_composition_csv": tmp_path / "semiconductor_composition.csv",
        "semiconductor_local_environment_csv": tmp_path / "semiconductor_local_environment.csv",
        "semiconductor_neighbor_pairs_csv": tmp_path / "semiconductor_neighbor_pairs.csv",
        "semiconductor_reciprocal_lattice_csv": tmp_path / "semiconductor_reciprocal_lattice.csv",
        "semiconductor_band_path_csv": tmp_path / "semiconductor_band_path.csv",
        "semiconductor_calculation_preflight_csv": tmp_path / "semiconductor_calculation_preflight.csv",
        "semiconductor_calculation_readiness_csv": tmp_path / "semiconductor_calculation_readiness.csv",
    }
    for path in expected_files.values():
        path.write_text("key,value\nok,1\n", encoding="utf-8")

    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_live_edit", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "mos2_diag",
            "revision": 0,
            "new_revision": 0,
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {
                    "status": "claimable_with_calculation_review",
                    "can_claim_model_normal": True,
                },
                "visual_normality_summary": {
                    "status": "model_normal_with_visual_notes",
                    "can_report_model_normal": True,
                },
                "gui": {"hot_loaded": True, "loaded_current_revision": True},
            },
        },
        status={"ok": True, "project_id": "mos2_diag", "revision": 0},
        bundle={
            "ok": True,
            "manifest_path": str(manifest),
                "row_counts": {
                    "view_summary": 7,
                    "view_quality": 7,
                    "view_projections": 84,
                    "semiconductor_lattice": 1,
                    "semiconductor_composition": 1,
                    "semiconductor_local_environment": 12,
                    "semiconductor_neighbor_pairs": 1,
                    "semiconductor_reciprocal_lattice": 3,
                    "semiconductor_band_path": 12,
                    "semiconductor_calculation_preflight": 1,
                    "semiconductor_calculation_readiness": 1,
                },
                "files": {key: str(path) for key, path in expected_files.items()},
            },
            scenario="diamond",
        )

    assert summary["diagnostic_acceptance_ok"] is True
    assert summary["diagnostic_acceptance_status"] == "diagnostics_ready"
    assert summary["diagnostic_can_check_model_normality"] is True
    assert summary["diagnostic_basic_view_tables_ok"] is True
    assert summary["diagnostic_basic_view_table_failures"] == []
    assert summary["diagnostic_row_count_total"] == 130
    assert "view_projections" in summary["diagnostic_row_count_keys"]
    assert summary["diagnostic_acceptance"]["manifest_exists"] is True
    assert summary["scenario_expected_diagnostics_ok"] is True


def test_live_smoke_summary_marks_diagnostic_acceptance_failed_without_view_tables(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_live_edit", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "mos2_diag_missing",
            "revision": 0,
            "new_revision": 0,
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        },
        status={"ok": True, "project_id": "mos2_diag_missing", "revision": 0},
        bundle={
            "ok": True,
            "manifest_path": str(manifest),
            "row_counts": {"view_summary": 1, "view_projections": 12},
            "files": {},
        },
        scenario=None,
    )

    assert summary["diagnostic_acceptance_ok"] is False
    assert summary["diagnostic_acceptance_status"] == "diagnostics_failed"
    assert summary["diagnostic_can_check_model_normality"] is False
    assert summary["diagnostic_basic_view_tables_ok"] is False
    assert {
        "type": "row_count_below_minimum",
        "key": "view_quality",
        "expected_minimum": 1,
        "observed": 0,
    } in summary["diagnostic_basic_view_table_failures"]


def test_live_smoke_marks_missing_sic_mos_diagnostics_as_failed(tmp_path: Path) -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "sic_mos_live",
            "revision": 0,
            "new_revision": 0,
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        },
        status={"ok": True, "project_id": "sic_mos_live", "revision": 0},
        bundle={
            "ok": True,
            "row_counts": {"view_summary": 7, "view_quality": 7, "view_projections": 168},
            "files": {},
        },
        scenario="sic_mos",
    )

    assert summary["scenario_expected_diagnostics_ok"] is False
    failures = summary["scenario_expected_diagnostic_failures"]
    assert {"type": "row_count_below_minimum", "key": "semiconductor_gate_stack", "expected_minimum": 1, "observed": 0} in failures
    assert any(failure["type"] == "missing_file" and failure["key"] == "semiconductor_gate_stack_csv" for failure in failures)


def test_live_smoke_marks_missing_vacancy_diagnostics_as_failed(tmp_path: Path) -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "patch",
            "project_id": "si_live",
            "revision": 1,
            "new_revision": 1,
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {"hot_loaded": True, "loaded_current_revision": True},
            },
        },
        status={"ok": True, "project_id": "si_live", "revision": 1},
        bundle={
            "ok": True,
            "row_counts": {"view_summary": 3, "view_quality": 3, "view_projections": 24},
            "files": {},
        },
        base_live={"ok": True, "project_id": "si_live", "revision": 0},
        scenario="silicon",
        follow_up_preset="vacancy",
    )

    assert summary["follow_up_expected_diagnostics_ok"] is False
    failures = summary["follow_up_expected_diagnostic_failures"]
    assert {"type": "row_count_below_minimum", "key": "semiconductor_defects", "expected_minimum": 1, "observed": 0} in failures
    assert any(failure["type"] == "missing_file" and failure["key"] == "semiconductor_defects_csv" for failure in failures)


def test_live_smoke_marks_missing_silicon_pn_junction_diagnostics_as_failed(tmp_path: Path) -> None:
    summary = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model", "blocking_reasons": [], "review_reasons": []},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "si_pn_live",
            "revision": 0,
            "new_revision": 0,
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {"hot_loaded": False, "loaded_current_revision": False},
            },
        },
        status={"ok": True, "project_id": "si_pn_live", "revision": 0},
        bundle={
            "ok": True,
            "row_counts": {"view_summary": 7, "view_quality": 7, "view_projections": 112},
            "files": {},
        },
        scenario="silicon_pn_junction",
    )

    assert summary["scenario_expected_diagnostics_ok"] is False
    failures = summary["scenario_expected_diagnostic_failures"]
    assert {"type": "row_count_below_minimum", "key": "semiconductor_junctions", "expected_minimum": 1, "observed": 0} in failures
    assert {"type": "row_count_below_minimum", "key": "semiconductor_dopants", "expected_minimum": 2, "observed": 0} in failures
    assert any(failure["type"] == "missing_file" and failure["key"] == "semiconductor_junctions_csv" for failure in failures)


def test_collect_warnings_deduplicates_across_sources() -> None:
    warnings = live_smoke._collect_warnings(
        {"warnings": ["duplicate", "unique"], "blocking_reasons": ["duplicate"]},
        {"review_reasons": ["duplicate", "second"]},
        {"warnings": ["unique", "third"]},
    )

    assert warnings == ["duplicate", "unique", "second", "third"]


def test_same_window_hotload_alias_is_recognized() -> None:
    assert _explicit_live_gui_open_requested(
        "Build silicon crystal and hot-load it in the same window of Materials Studio."
    )


def test_single_window_hotload_alias_is_recognized() -> None:
    assert _explicit_live_gui_open_requested(
        "Build silicon crystal and hot-load it in the single-window Materials Studio session."
    )


def test_current_window_hotload_alias_without_explicit_materials_studio_is_recognized() -> None:
    assert _explicit_live_gui_open_requested("Push it to the current window and hot-load it.")


def test_existing_window_hotload_alias_is_recognized() -> None:
    assert _explicit_live_gui_open_requested(
        "Build silicon crystal and hot-load it into the existing Materials Studio window."
    )


def test_same_window_real_time_hotloading_alias_is_recognized() -> None:
    assert _explicit_live_hotload_requested("same-window real-time hot-loading")


def test_chinese_same_window_hotloading_alias_is_recognized() -> None:
    assert _explicit_live_hotload_requested("同窗口热加载到 Materials Studio")
