from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_default_sic_3c_polar_surface_and_contact_scenarios_are_discoverable() -> None:
    si_slab = live_smoke.default_request_for_scenario("sic_3c_slab")
    c_slab = live_smoke.default_request_for_scenario("sic_3c_c_face_slab", hotload=True)
    si_contact = live_smoke.default_request_for_scenario("sic_3c_contact")
    c_contact = live_smoke.default_request_for_scenario("sic_3c_c_face_contact", hotload=True)

    assert "3C-SiC(001) Si-face slab" in si_slab
    assert "3C-SiC(00-1) C-face slab" in c_slab
    assert "Au/3C-SiC(001) Si-face Schottky contact" in si_contact
    assert "Au/3C-SiC(00-1) C-face Schottky contact" in c_contact
    assert "hot-load it in Materials Studio" in c_slab
    assert "hot-load it in Materials Studio" in c_contact
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_slab"] == (
        "silicon_carbide_3c_001_si_face_slab"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_c_face_slab"] == (
        "silicon_carbide_3c_00m1_c_face_slab"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_contact"] == (
        "metal_silicon_carbide_3c_001_si_face_schottky_contact"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_c_face_contact"] == (
        "metal_silicon_carbide_3c_00m1_c_face_schottky_contact"
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_slab"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_slab"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_c_face_slab"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_slab"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_contact"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_4h_contact"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_c_face_contact"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_4h_contact"]
    )


def test_default_sic_3c_oxide_and_mos_scenarios_are_discoverable() -> None:
    si_oxide = live_smoke.default_request_for_scenario("sic_3c_oxide_interface")
    c_oxide = live_smoke.default_request_for_scenario(
        "sic_3c_c_face_oxide_interface",
        hotload=True,
    )
    si_mos = live_smoke.default_request_for_scenario("sic_3c_mos")
    c_mos = live_smoke.default_request_for_scenario("sic_3c_c_face_mos", hotload=True)

    assert "SiO2/3C-SiC(001) Si-face interface" in si_oxide
    assert "SiO2/3C-SiC(00-1) C-face interface" in c_oxide
    assert "Al/SiO2/3C-SiC(001) Si-face MOS capacitor" in si_mos
    assert "Al/SiO2/3C-SiC(00-1) C-face MOS capacitor" in c_mos
    assert "hot-load it in Materials Studio" in c_oxide
    assert "hot-load it in Materials Studio" in c_mos
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_3c_001_si_face_interface"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_c_face_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_3c_00m1_c_face_interface"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_3c_001_si_face_mos_capacitor"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_3c_c_face_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_3c_00m1_c_face_mos_capacitor"
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_c_face_oxide_interface"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_3c_oxide_interface"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_3c_c_face_mos"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_3c_mos"]
    )
    assert (
        live_smoke.SCENARIO_EXPECTATIONS["sic_3c_oxide_interface"]["row_counts"][
            "semiconductor_oxide_interface_geometry"
        ]
        == 26
    )
    assert (
        live_smoke.SCENARIO_EXPECTATIONS["sic_3c_mos"]["row_counts"][
            "semiconductor_oxide_interface_geometry"
        ]
        == 27
    )


def test_live_smoke_previews_sic_3c_c_face_mos(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        scenario="sic_3c_c_face_mos",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["live"]["nl_plan"]["template_id"] == (
        "aluminum_silicon_dioxide_silicon_carbide_3c_00m1_c_face_mos_capacitor"
    )
    assert result["live"]["view_audit"]["metadata"]["surface_orientation"] == (
        "3C-SiC(00-1) C-face"
    )
    assert result["summary"]["scenario_expected_diagnostics_ok"] is True
    assert result["bundle"]["row_counts"]["semiconductor_gate_stack"] == 3
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_geometry"] == 27


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


def test_default_sic_6h_surface_scenario_requires_surface_and_view_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("sic_6h_slab")
    hotload = live_smoke.default_request_for_scenario("sic_6h_slab", hotload=True)
    expectation = live_smoke.SCENARIO_EXPECTATIONS["sic_6h_slab"]

    assert "6H-SiC(0001) Si-face slab" in preview
    assert "surface and all-view diagnostics" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "check whether the model is normal" in hotload
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_slab"] == (
        "silicon_carbide_6h_0001_si_face_slab"
    )
    assert expectation["row_counts"]["semiconductor_surface_model"] == 1
    assert expectation["row_counts"]["semiconductor_surface_termination"] == 1
    assert expectation["row_counts"]["semiconductor_surface_polarity"] == 1
    assert "semiconductor_surface_model_csv" in expectation["files"]
    assert "semiconductor_surface_termination_csv" in expectation["files"]


def test_default_sic_6h_contact_scenario_requires_contact_surface_and_view_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("sic_6h_contact")
    hotload = live_smoke.default_request_for_scenario("sic_6h_contact", hotload=True)
    expectation = live_smoke.SCENARIO_EXPECTATIONS["sic_6h_contact"]

    assert "Au/6H-SiC(0001) Si-face Schottky contact" in preview
    assert "contact and view diagnostics" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "check whether the model is normal" in hotload
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_contact"] == (
        "metal_silicon_carbide_6h_0001_schottky_contact"
    )
    assert expectation["row_counts"]["semiconductor_contact"] == 2
    assert expectation["row_counts"]["semiconductor_surface_polarity"] == 1
    assert expectation["row_counts"]["view_quality"] == 1
    assert "semiconductor_contact_csv" in expectation["files"]
    assert "semiconductor_surface_polarity_csv" in expectation["files"]


def test_default_sic_6h_mos_scenario_requires_gate_stack_interface_and_view_diagnostics() -> None:
    preview = live_smoke.default_request_for_scenario("sic_6h_mos")
    hotload = live_smoke.default_request_for_scenario("sic_6h_mos", hotload=True)
    interface_gaps = live_smoke.default_follow_up_request_for_scenario(
        "sic_6h_mos",
        "interface_gaps_2p0_2p5",
    )
    expectation = live_smoke.SCENARIO_EXPECTATIONS["sic_6h_mos"]
    follow_up = live_smoke.FOLLOW_UP_EXPECTATIONS["sic_6h_mos"]["interface_gaps_2p0_2p5"]

    assert "Al/SiO2/6H-SiC(0001) Si-face MOS capacitor" in preview
    assert "gate-stack, interface, and view diagnostics" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "check whether the model is normal" in hotload
    assert "semiconductor-oxide interface gap to 2.0 angstrom" in interface_gaps
    assert "oxide-gate interface gap to 2.5 angstrom" in interface_gaps
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_6h_mos_capacitor"
    )
    assert expectation["row_counts"]["semiconductor_gate_stack"] == 1
    assert expectation["row_counts"]["semiconductor_interface_quality"] == 1
    assert expectation["row_counts"]["semiconductor_oxide_interface_geometry"] == 39
    assert expectation["row_counts"]["semiconductor_oxide_interface_health"] == 3
    assert expectation["row_counts"]["view_quality"] == 1
    assert "semiconductor_gate_stack_csv" in expectation["files"]
    assert "semiconductor_interface_quality_csv" in expectation["files"]
    assert "semiconductor_oxide_interface_geometry_csv" in expectation["files"]
    assert "semiconductor_oxide_interface_health_csv" in expectation["files"]
    assert follow_up["row_counts"]["semiconductor_gate_stack"] == 3
    assert follow_up["row_counts"]["semiconductor_oxide_interface_geometry"] == 39
    assert "semiconductor_gate_stack_csv" in follow_up["files"]
    assert "semiconductor_oxide_interface_geometry_csv" in follow_up["files"]


def test_default_sic_6h_oxide_interface_scenario_and_vacancy_follow_up() -> None:
    preview = live_smoke.default_request_for_scenario("sic_6h_oxide_interface")
    hotload = live_smoke.default_request_for_scenario("sic_6h_oxide_interface", hotload=True)
    vacancy = live_smoke.default_follow_up_request_for_scenario("sic_6h_oxide_interface", "o_vacancy")
    expectation = live_smoke.SCENARIO_EXPECTATIONS["sic_6h_oxide_interface"]
    follow_up = live_smoke.FOLLOW_UP_EXPECTATIONS["sic_6h_oxide_interface"]["o_vacancy"]

    assert "SiO2/6H-SiC(0001) Si-face interface" in preview
    assert "semiconductor-oxide interface" in preview
    assert "hot-load it in Materials Studio" in hotload
    assert "O vacancy" in vacancy
    assert "semiconductor-oxide interface" in vacancy
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_6h_0001_interface"
    )
    assert expectation["row_counts"]["semiconductor_interface_quality"] == 1
    assert expectation["row_counts"]["semiconductor_oxide_interface_geometry"] == 38
    assert expectation["row_counts"]["semiconductor_oxide_interface_health"] == 3
    assert expectation["row_counts"]["semiconductor_calculation_preflight"] == 1
    assert "semiconductor_interface_quality_csv" in expectation["files"]
    assert "semiconductor_oxide_interface_geometry_csv" in expectation["files"]
    assert "semiconductor_oxide_interface_health_csv" in expectation["files"]
    assert follow_up["row_counts"]["semiconductor_defects"] == 1
    assert follow_up["row_counts"]["semiconductor_oxide_interface_geometry"] == 33
    assert follow_up["row_counts"]["semiconductor_oxide_interface_health"] == 4
    assert follow_up["row_counts"]["requested_diagnostic_focus_status"] == 3
    assert "semiconductor_defects_csv" in follow_up["files"]
    assert "semiconductor_oxide_interface_geometry_csv" in follow_up["files"]
    assert "semiconductor_oxide_interface_health_csv" in follow_up["files"]


def test_default_sic_6h_c_face_scenarios_are_discoverable() -> None:
    slab = live_smoke.default_request_for_scenario("sic_6h_c_face_slab")
    contact = live_smoke.default_request_for_scenario("sic_6h_c_face_contact", hotload=True)
    oxide = live_smoke.default_request_for_scenario("sic_6h_c_face_oxide_interface")
    mos = live_smoke.default_request_for_scenario("sic_6h_c_face_mos", hotload=True)
    gaps = live_smoke.default_follow_up_request_for_scenario(
        "sic_6h_c_face_mos",
        "interface_gaps_2p0_2p5",
    )

    assert "6H-SiC(000-1) C-face slab" in slab
    assert "Au/6H-SiC(000-1) C-face Schottky contact" in contact
    assert "hot-load" in contact
    assert "SiO2/6H-SiC(000-1) C-face interface" in oxide
    assert "Al/SiO2/6H-SiC(000-1) C-face MOS capacitor" in mos
    assert "hot-load" in mos
    assert "semiconductor-oxide interface gap to 2.0 angstrom" in gaps
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_c_face_slab"] == (
        "silicon_carbide_6h_000m1_c_face_slab"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_c_face_contact"] == (
        "metal_silicon_carbide_6h_000m1_c_face_schottky_contact"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_c_face_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_6h_000m1_c_face_interface"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_6h_c_face_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_6h_000m1_c_face_mos_capacitor"
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_6h_c_face_slab"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_slab"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_6h_c_face_contact"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_contact"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_6h_c_face_oxide_interface"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_oxide_interface"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_6h_c_face_mos"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_mos"]
    )


def test_default_sic_4h_polar_scenarios_are_discoverable() -> None:
    si_slab = live_smoke.default_request_for_scenario("sic_4h_slab")
    c_slab = live_smoke.default_request_for_scenario("sic_4h_c_face_slab")
    c_contact = live_smoke.default_request_for_scenario("sic_4h_c_face_contact", hotload=True)
    si_oxide = live_smoke.default_request_for_scenario("sic_4h_oxide_interface")
    c_oxide = live_smoke.default_request_for_scenario("sic_4h_c_face_oxide_interface")
    si_mos = live_smoke.default_request_for_scenario("sic_mos")
    c_mos = live_smoke.default_request_for_scenario("sic_4h_c_face_mos", hotload=True)
    gaps = live_smoke.default_follow_up_request_for_scenario(
        "sic_4h_c_face_mos",
        "interface_gaps_2p0_2p5",
    )

    assert "4H-SiC(0001) Si-face slab" in si_slab
    assert "4H-SiC(000-1) C-face slab" in c_slab
    assert "Au/4H-SiC(000-1) C-face Schottky contact" in c_contact
    assert "hot-load" in c_contact
    assert "SiO2/4H-SiC(0001) Si-face interface" in si_oxide
    assert "SiO2/4H-SiC(000-1) C-face interface" in c_oxide
    assert "4H-SiC MOS capacitor" in si_mos
    assert "Al/SiO2/4H-SiC(000-1) C-face MOS capacitor" in c_mos
    assert "hot-load" in c_mos
    assert "semiconductor-oxide interface gap to 2.0 angstrom" in gaps
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_slab"] == (
        "silicon_carbide_4h_0001_si_face_slab"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_c_face_slab"] == (
        "silicon_carbide_4h_000m1_c_face_slab"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_c_face_contact"] == (
        "metal_silicon_carbide_4h_000m1_c_face_schottky_contact"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_4h_0001_si_face_interface"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_c_face_oxide_interface"] == (
        "silicon_dioxide_silicon_carbide_4h_000m1_c_face_interface"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor"
    )
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["sic_4h_c_face_mos"] == (
        "aluminum_silicon_dioxide_silicon_carbide_4h_000m1_c_face_mos_capacitor"
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_4h_slab"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_slab"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_4h_c_face_contact"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_4h_contact"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_4h_c_face_oxide_interface"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_oxide_interface"]
    )
    assert live_smoke.SCENARIO_EXPECTATIONS["sic_4h_c_face_mos"] == (
        live_smoke.SCENARIO_EXPECTATIONS["sic_6h_mos"]
    )


def test_live_smoke_previews_sic_4h_c_face_mos_gap_follow_up(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        scenario="sic_4h_c_face_mos",
        follow_up_preset="interface_gaps_2p0_2p5",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["base_live"]["nl_plan"]["template_id"] == (
        "aluminum_silicon_dioxide_silicon_carbide_4h_000m1_c_face_mos_capacitor"
    )
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["base_revision"] == 0
    assert result["followup_live"]["new_revision"] == 1
    metadata = result["followup_live"]["view_audit"]["metadata"]
    assert metadata["surface_orientation"] == "4H-SiC(000-1) C-face"
    assert metadata["semiconductor_oxide_interface_gap_angstrom"] == 2.0
    assert metadata["oxide_gate_interface_gap_angstrom"] == 2.5
    assert result["summary"]["follow_up_expected_diagnostics_ok"] is True
    assert result["bundle"]["row_counts"]["semiconductor_gate_stack"] == 3
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_geometry"] == 39


def test_live_smoke_previews_sic_4h_c_face_oxide_vacancy_follow_up(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        scenario="sic_4h_c_face_oxide_interface",
        follow_up_preset="o_vacancy",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["view_audit"]["metadata"]["surface_orientation"] == (
        "4H-SiC(000-1) C-face"
    )
    assert result["summary"]["follow_up_expected_diagnostics_ok"] is True
    assert result["bundle"]["row_counts"]["semiconductor_defects"] == 1
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_geometry"] == 33
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_health"] == 4


def test_live_smoke_previews_sic_6h_c_face_mos_gap_follow_up(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        scenario="sic_6h_c_face_mos",
        follow_up_preset="interface_gaps_2p0_2p5",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["base_live"]["nl_plan"]["template_id"] == (
        "aluminum_silicon_dioxide_silicon_carbide_6h_000m1_c_face_mos_capacitor"
    )
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["base_revision"] == 0
    assert result["followup_live"]["new_revision"] == 1
    assert result["followup_live"]["project_id"] == result["base_live"]["project_id"]
    metadata = result["followup_live"]["view_audit"]["metadata"]
    assert metadata["surface_orientation"] == "6H-SiC(000-1) C-face"
    assert metadata["semiconductor_oxide_interface_gap_angstrom"] == 2.0
    assert metadata["oxide_gate_interface_gap_angstrom"] == 2.5
    assert result["summary"]["follow_up_expected_diagnostics_ok"] is True
    assert result["bundle"]["row_counts"]["semiconductor_gate_stack"] == 3
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_geometry"] == 39


def test_live_smoke_previews_sic_6h_c_face_oxide_vacancy_follow_up(tmp_path: Path) -> None:
    result = live_smoke.run_live_smoke(
        scenario="sic_6h_c_face_oxide_interface",
        follow_up_preset="o_vacancy",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
    )

    assert result["ok"] is True
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["view_audit"]["metadata"]["surface_orientation"] == (
        "6H-SiC(000-1) C-face"
    )
    assert result["summary"]["follow_up_expected_diagnostics_ok"] is True
    assert result["bundle"]["row_counts"]["semiconductor_defects"] == 1
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_geometry"] == 33
    assert result["bundle"]["row_counts"]["semiconductor_oxide_interface_health"] == 4


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
                "state": "gui_preflight_required",
                "explicit_hotload_requested": False,
                "hotload_safe_to_attempt": False,
                "recommended_tool": "material_studio_gui_status",
            },
            "live_hotload_preflight": {
                "status": "gui_preflight_required",
                "safe_to_attempt_hotload": False,
                "gui_preflight_verified": False,
                "gui_preflight_required": True,
                "gui_preflight_reasons": [
                    "gui_status_not_probed",
                    "single_window_policy_not_verified",
                ],
                "model_ready_for_hotload": True,
                "current_revision_loaded": False,
                "recommended_tool": "material_studio_gui_status",
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
                "state": "gui_preflight_required",
                "explicit_hotload_requested": False,
                "hotload_safe_to_attempt": False,
                "recommended_tool": "material_studio_gui_status",
            },
            "live_hotload_preflight": {
                "status": "gui_preflight_required",
                "safe_to_attempt_hotload": False,
                "gui_preflight_verified": False,
                "gui_preflight_required": True,
                "gui_preflight_reasons": [
                    "gui_status_not_probed",
                    "single_window_policy_not_verified",
                ],
                "model_ready_for_hotload": True,
                "current_revision_loaded": False,
                "recommended_tool": "material_studio_gui_status",
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
    assert summary["live_request_state"] == "gui_preflight_required"
    assert summary["live_request_hotload_safe_to_attempt"] is False
    assert summary["live_request_recommended_tool"] == "material_studio_gui_status"
    assert summary["live_hotload_preflight_status"] == "gui_preflight_required"
    assert summary["live_hotload_preflight_safe_to_attempt"] is False
    assert summary["live_hotload_preflight_gui_verified"] is False
    assert summary["live_hotload_preflight_gui_required"] is True
    assert summary["live_hotload_preflight_gui_reasons"] == [
        "gui_status_not_probed",
        "single_window_policy_not_verified",
    ]
    assert summary["live_hotload_preflight_model_ready"] is True
    assert summary["hotload_acceptance"]["available"] is False
    assert summary["hotload_acceptance"]["reason"] == "hotload_not_requested"
    assert summary["gui_hotload_gate_status"] == "preflight_required"
    assert summary["gui_hotload_gate_ok"] is False
    assert summary["gui_hotload_gate_recommended_tool"] == "material_studio_gui_status"
    assert summary["gui_hotload_gate_blocking_reasons"] == []
    assert summary["view_bundle_manifest_exists"] is True
    assert summary["view_bundle_row_counts"] == {"modeling_report_summary": 1}
    assert summary["scenario_expected_diagnostics"]["available"] is False
    assert summary["scenario_expected_diagnostics"]["reason"] == "no_scenario"
    assert summary["next_action_tool"] == "material_studio_gui_status"


def test_live_smoke_cli_writes_compact_json(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "smoke.json"

    def fake_run_live_smoke(**kwargs):
        assert kwargs["scenario"] == "mos2"
        assert kwargs["working_dir"] == str(tmp_path)
        assert kwargs["include_gui_status"] is False
        assert kwargs["take_snapshot"] is False
        assert kwargs["export_bundle"] is False
        assert kwargs["resume_deferred_bundle_export"] is True
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
            "--resume-deferred-bundle-export",
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
        assert kwargs["project_id"] == "si_live"
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


def test_live_smoke_summary_scopes_observed_gui_identity_to_current_revision() -> None:
    unbound = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model"},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "preview_scope",
            "revision": 0,
            "execution_mode": "preview",
            "modeling_report": {
                "normality": "preview_ready",
                "normality_gate": {"status": "preview_only"},
                "gui": {
                    "hot_loaded": False,
                    "loaded_current_revision": False,
                    "window_identity_verification": "mismatched",
                    "current_revision_gui_evidence_applicable": False,
                    "current_revision_gui_evidence_status": (
                        "not_bound_to_current_revision"
                    ),
                    "current_revision_gui_evidence_sources": [],
                },
            },
        },
        scenario="gaas",
    )

    assert unbound["gui_window_identity_verification"] == "mismatched"
    assert unbound["current_revision_gui_evidence_applicable"] is False
    assert unbound["current_revision_gui_evidence_status"] == (
        "not_bound_to_current_revision"
    )
    assert unbound["current_revision_gui_evidence_sources"] == []
    assert unbound["current_revision_gui_window_identity_verification"] == (
        "not_applicable_to_current_revision"
    )

    bound = live_smoke.build_live_smoke_summary(
        preflight={"ok": True, "state": "ready_for_new_model"},
        live={
            "ok": True,
            "workflow": "create",
            "project_id": "execute_scope",
            "revision": 0,
            "execution_mode": "execute",
            "modeling_report": {
                "normality": "review_warnings",
                "normality_gate": {"status": "review_required"},
                "gui": {
                    "hot_loaded": True,
                    "loaded_current_revision": True,
                    "window_identity_verification": "mismatched",
                    "current_revision_gui_evidence_applicable": True,
                    "current_revision_gui_evidence_status": (
                        "bound_to_current_revision"
                    ),
                    "current_revision_gui_evidence_sources": [
                        "current_request_gui_open_artifact"
                    ],
                },
            },
        },
        scenario="gaas",
    )

    assert bound["current_revision_gui_evidence_applicable"] is True
    assert bound["current_revision_gui_evidence_status"] == (
        "bound_to_current_revision"
    )
    assert bound["current_revision_gui_evidence_sources"] == [
        "current_request_gui_open_artifact"
    ]
    assert bound["current_revision_gui_window_identity_verification"] == (
        "mismatched"
    )


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


def _deferred_preexecution_live(
    tmp_path: Path,
    *,
    project_id: str,
    revision: int,
    workflow: str,
    views: list[str] | None = None,
    take_snapshot: bool = True,
    fit_to_view_after_open: bool = False,
    prepare_view_replay_after_open: bool = False,
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    workspace = str(tmp_path.resolve())
    structure = tmp_path / project_id / "outputs" / f"r{revision:03d}" / "model.cif"
    activation_payload: dict[str, object] = {
        "project_id": project_id,
        "revision": revision,
        "take_snapshot": True,
        "working_dir": workspace,
    }
    execution_payload: dict[str, object] = {
        "project_id": project_id,
        "expected_revision": revision,
        "execution_mode": "execute",
        "open_in_gui": True,
        "take_snapshot": take_snapshot,
        "fit_to_view_after_open": fit_to_view_after_open,
        "prepare_view_replay_after_open": prepare_view_replay_after_open,
        "export_view_audit": True,
        "working_dir": workspace,
    }
    if views is not None:
        activation_payload["views"] = list(views)
        execution_payload["views"] = list(views)
    if timeout_seconds is not None:
        execution_payload["timeout_seconds"] = timeout_seconds
    block = {
        "blocked": True,
        "reason": "target_window_activation_required",
        "project_id": project_id,
        "revision": revision,
        "recommended_tool": "material_studio_gui_activate",
        "recommended_action": "activate_exact_existing_window_before_revision_execution",
        "activation_payload": dict(activation_payload),
        "execution_retry_tool": "material_studio_gui_apply_current_revision",
        "execution_retry_payload": dict(execution_payload),
        "same_window_required": True,
        "reuse_existing_window_only": True,
        "gui_process_launch_allowed": False,
    }
    response: dict[str, object] = {
        "ok": False,
        "status": "gui_activation_required_before_execution",
        "error": "target window activation required",
        "workflow": workflow,
        "project_id": project_id,
        "working_dir": workspace,
        "execution_mode": "execute",
        "execution_mode_source": "explicit_argument",
        "execution_started": False,
        "execution_deferred": True,
        "runner_invoked": False,
        "structure_materialization_started": False,
        "gui_input_started": False,
        "gui_process_launched": False,
        "structure_reopened": False,
        "prepared_revision_retained": True,
        "planned_outputs": {"structure": str(structure.resolve())},
        "gui_preexecution_block": block,
        "gui_activation_retry_tool": "material_studio_gui_activate",
        "gui_activation_retry_payload": activation_payload,
        "execution_retry_tool": "material_studio_gui_apply_current_revision",
        "execution_retry_payload": execution_payload,
        "modeling_report": {
            "workflow": workflow,
            "execution_mode": "execute",
            "normality": "execution_deferred_for_gui_activation",
            "normality_gate": {"status": "visual_review_required"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }
    if workflow == "create":
        response["revision"] = revision
        response["new_revision"] = revision
    else:
        response["new_revision"] = revision
        response["base_revision"] = revision - 1
    return response


def _current_revision_receipt(project_id: str, revision: int) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": project_id,
        "revision": revision,
        "spec": {"project_id": project_id, "revision": revision},
    }


def _deferred_bundle_export(
    tmp_path: Path,
    *,
    project_id: str,
    revision: int,
    views: list[str] | None,
    include_gui_snapshot: bool,
    response_mode: str = "full",
) -> dict[str, object]:
    workspace = str(tmp_path.resolve())
    error = (
        "GUI artifact report write transaction is busy; retry after the "
        "current GUI evidence update finishes"
    )
    retry_payload = {
        "project_id": project_id,
        "views": views,
        "include_gui_snapshot": include_gui_snapshot,
        "working_dir": workspace,
        "response_mode": response_mode,
    }
    return {
        "ok": False,
        "status": "diagnostic_export_deferred",
        "error": error,
        "project_id": project_id,
        "project_resolution": {
            "source": "explicit",
            "project_id": project_id,
            "revision": revision,
        },
        "revision": revision,
        "diagnostic_export_deferred": True,
        "report_persistence_deferred": True,
        "gui_action_transaction_error": error,
        "recommended_tool": "material_studio_model_export_view_bundle",
        "diagnostic_export_retry_tool": (
            "material_studio_model_export_view_bundle"
        ),
        "diagnostic_export_retry_payload": retry_payload,
    }


def _successful_bundle_export(
    tmp_path: Path,
    *,
    project_id: str,
    revision: int,
    views: list[str] | None,
) -> dict[str, object]:
    bundle_dir = tmp_path / project_id / "outputs" / f"r{revision:03d}" / "view_audit"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest = bundle_dir / "view_bundle_manifest.json"
    projections = bundle_dir / "view_projections.csv"
    summary = bundle_dir / "view_summary.csv"
    quality = bundle_dir / "view_quality.csv"
    view_names = views or ["front"]
    projection_count = len(view_names)
    projection_rows = "".join(
        f"{view_names[index]},Si{index + 1}\n"
        for index in range(projection_count)
    )
    projections.write_text(
        "view,atom_id\n" + projection_rows,
        encoding="utf-8",
    )
    summary.write_text("view\nfront\n", encoding="utf-8")
    quality.write_text("view,status\nfront,ok\n", encoding="utf-8")
    files = {
        "view_projections_csv": str(projections.resolve()),
        "view_summary_csv": str(summary.resolve()),
        "view_quality_csv": str(quality.resolve()),
    }
    row_counts = {
        "view_projections": projection_count,
        "view_summary": 1,
        "view_quality": 1,
    }
    manifest.write_text(
        json.dumps(
            {
                "project_id": project_id,
                "revision": revision,
                "files": files,
                "row_counts": row_counts,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "project_id": project_id,
        "revision": revision,
        "manifest_path": str(manifest.resolve()),
        "view_bundle_manifest_path": str(manifest.resolve()),
        "files": files,
        "view_bundle_files": files,
        "row_counts": row_counts,
        "view_bundle_row_counts": row_counts,
        "diagnostic_export_view_resolution": {
            "resolved_view_names": views,
        },
        "report_write_transaction": {
            "domain": "gui_artifact_report",
            "project_id": project_id,
            "revision": revision,
            "workspace_root": str(tmp_path.resolve()),
            "coverage": [
                "workflow:model_export_view_bundle",
                "current_revision_revalidation",
                "diagnostic_export",
                "view_audit_bundle_write",
                "report_read_modify_write",
            ],
        },
    }


def _deferred_postexecution_live(
    tmp_path: Path,
    *,
    project_id: str,
    revision: int,
    workflow: str,
    views: list[str] | None = None,
    fit_to_view_after_open: bool = False,
    prepare_view_replay_after_open: bool = False,
) -> dict[str, object]:
    workspace = str(tmp_path.resolve())
    structure = tmp_path / project_id / "outputs" / f"r{revision:03d}" / "model.cif"
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_text("data_model\n", encoding="utf-8")
    activation_payload: dict[str, object] = {
        "project_id": project_id,
        "revision": revision,
        "take_snapshot": True,
        "working_dir": workspace,
    }
    open_payload: dict[str, object] = {
        "structure_path": str(structure.resolve()),
        "project_id": project_id,
        "revision": revision,
        "take_snapshot": True,
        "export_view_audit": True,
        "reuse_existing_window_only": True,
        "working_dir": workspace,
    }
    if views is not None:
        activation_payload["views"] = list(views)
        open_payload["views"] = list(views)
    if fit_to_view_after_open:
        open_payload["fit_to_view_after_open"] = True
    if prepare_view_replay_after_open:
        open_payload["prepare_view_replay_after_open"] = True
    block = {
        "blocked": True,
        "reason": "target_window_activation_required_after_execution",
        "project_id": project_id,
        "revision": revision,
        "execution_already_completed": True,
        "execution_retry_allowed": False,
        "result_artifacts_preserved": True,
        "recommended_tool": "material_studio_gui_activate",
        "gui_open_retry_tool": "material_studio_gui_open_structure",
        "activation_payload": dict(activation_payload),
        "gui_open_retry_payload": dict(open_payload),
        "same_window_required": True,
        "reuse_existing_window_only": True,
        "gui_process_launch_allowed": False,
    }
    return {
        "ok": False,
        "partial_success": True,
        "status": "execution_completed_gui_activation_required",
        "error": "focus lost after execution",
        "workflow": workflow,
        "project_id": project_id,
        "revision": revision,
        "new_revision": revision,
        "working_dir": workspace,
        "execution_mode": "execute",
        "execution_mode_source": "explicit_argument",
        "execution_completed_before_gui_activation": True,
        "execution_must_not_repeat": True,
        "execution_retry_allowed": False,
        "gui_input_started": False,
        "gui_process_launched": False,
        "structure_reopened": False,
        "planned_outputs": {"structure": str(structure.resolve())},
        "result": {"success": True, "execution_backend": "fake_materialsscript"},
        "execution_transaction": {"attempt_id": f"{project_id}-r{revision:03d}"},
        "gui_postexecution_block": block,
        "gui_activation_retry_tool": "material_studio_gui_activate",
        "gui_activation_retry_payload": activation_payload,
        "gui_open_retry_tool": "material_studio_gui_open_structure",
        "gui_open_retry_payload": open_payload,
        "modeling_report": {
            "workflow": workflow,
            "execution_mode": "execute",
            "normality": "execution_complete_gui_deferred",
            "normality_gate": {"status": "visual_review_required"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }


def _deferred_gui_transaction_live(
    tmp_path: Path,
    *,
    project_id: str,
    revision: int,
    workflow: str,
    views: list[str] | None = None,
    take_snapshot: bool = True,
    fit_to_view_after_open: bool = False,
    prepare_view_replay_after_open: bool = False,
) -> dict[str, object]:
    workspace = str(tmp_path.resolve())
    structure = tmp_path / project_id / "outputs" / f"r{revision:03d}" / "model.cif"
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_text("data_gui_transaction_deferred\n", encoding="utf-8")
    open_payload: dict[str, object] = {
        "structure_path": str(structure.resolve()),
        "project_id": project_id,
        "revision": revision,
        "take_snapshot": take_snapshot,
        "export_view_audit": True,
        "reuse_existing_window_only": True,
        "working_dir": workspace,
    }
    if views is not None:
        open_payload["views"] = list(views)
    if fit_to_view_after_open:
        open_payload["fit_to_view_after_open"] = True
    if prepare_view_replay_after_open:
        open_payload["prepare_view_replay_after_open"] = True
    response: dict[str, object] = {
        "ok": False,
        "workflow": workflow,
        "project_id": project_id,
        "revision": revision,
        "new_revision": revision,
        "working_dir": workspace,
        "execution_mode": "execute",
        "execution_started": True,
        "execution_deferred": False,
        "gui_input_started": False,
        "planned_outputs": {"structure": str(structure.resolve())},
        "result": {
            "success": True,
            "execution_backend": "fake_materialsscript",
        },
        "execution_transaction": {
            "execution_started": True,
            "execution_completed": True,
            "current_revision_still_current": True,
        },
        "report_persistence_deferred": True,
        "execution_completed_before_gui_transaction": True,
        "structure_ready_for_gui_retry": True,
        "gui_action_transaction_error": (
            "GUI artifact report write transaction is busy: fake lock timeout"
        ),
        "required_next_step": "Retry the exact artifact-only open payload.",
        "recommended_tool": "material_studio_gui_open_structure",
        "gui_open_retry_tool": "material_studio_gui_open_structure",
        "gui_open_retry_payload": open_payload,
        "modeling_report": {
            "workflow": workflow,
            "execution_mode": "execute",
            "normality": "execution_complete_gui_deferred",
            "normality_gate": {"status": "visual_review_required"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }
    if fit_to_view_after_open:
        response["post_hotload_fit_to_view_requested"] = True
        response["post_hotload_fit_to_view"] = {
            "requested": True,
            "status": "deferred_gui_transaction_busy",
            "completed": False,
            "followup_tool": "material_studio_gui_open_structure",
            "followup_payload": dict(open_payload),
        }
    if prepare_view_replay_after_open:
        response["post_hotload_view_replay_prepare_requested"] = True
        response["post_hotload_view_replay_prepare"] = {
            "requested": True,
            "status": "deferred_gui_transaction_busy",
            "prepared": False,
            "followup_tool": "material_studio_gui_open_structure",
            "followup_payload": dict(open_payload),
        }
    return response


def _hotloaded_report(project_id: str, revision: int) -> dict[str, object]:
    return {
        "project_id": project_id,
        "revision": revision,
        "normality": "hot_loaded_and_passed",
        "execution_mode": "execute",
        "normality_gate": {
            "status": "visual_review_required",
            "can_claim_model_normal": False,
            "can_claim_live_gui_normal": False,
        },
        "next_action_plan": {
            "recommended_tool": "material_studio_live_project_status",
            "recommended_action": "inspect_current_revision",
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
        "live_summary": {
            "hot_loaded": True,
            "current_revision_loaded_in_gui": True,
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
        },
    }


def _successful_activation(project_id: str, revision: int) -> dict[str, object]:
    return {
        "ok": True,
        "activated": True,
        "activation_verified": True,
        "window_identity_stable_after_activation": True,
        "single_window_policy_ok": True,
        "gui_action_context": {"project_id": project_id, "revision": revision},
        "window_management": {
            "activation_required_before_capture_or_input": False,
            "single_window_policy_ok": True,
        },
    }


def _successful_open_continuation(
    *,
    project_id: str,
    revision: int,
    open_payload: dict[str, object],
) -> dict[str, object]:
    gui_open = {
        "project_id": project_id,
        "revision": revision,
        "structure_path": open_payload["structure_path"],
        "reuse_existing_window_only": True,
        "same_window_open_used": True,
        "single_window_policy_ok": True,
        "post_open_single_window_policy_ok": True,
        "open_result": {"spawned_process_ids": []},
    }
    response: dict[str, object] = {
        "ok": True,
        **gui_open,
        "gui_open": dict(gui_open),
        "structured_sync_context": {
            "available": True,
            "project_id": project_id,
            "revision": revision,
        },
        "modeling_report": _hotloaded_report(project_id, revision),
        "live_summary": {
            "hot_loaded": True,
            "current_revision_loaded_in_gui": True,
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
        },
        "live_request_summary": {
            "state": "current_revision_loaded",
            "explicit_hotload_requested": True,
            "hotload_safe_to_attempt": False,
        },
        "live_hotload_preflight": {
            "status": "current_revision_loaded",
            "safe_to_attempt_hotload": False,
            "gui_preflight_verified": True,
            "current_revision_loaded": True,
            "blocking_reasons": [],
        },
    }
    if open_payload.get("fit_to_view_after_open") is True:
        response["post_hotload_fit_to_view"] = {
            "completed": True,
            "structure_unchanged": True,
            "final_snapshot_bound": True,
        }
    if open_payload.get("prepare_view_replay_after_open") is True:
        response["post_hotload_view_replay_prepare"] = {
            "status": "prepared",
            "prepared": True,
            "prepared_revision": revision,
            "view_names": list(open_payload.get("views") or []),
        }
    return response


def _successful_live_status(project_id: str, revision: int) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": project_id,
        "revision": revision,
        "modeling_report": _hotloaded_report(project_id, revision),
        "live_summary": {
            "hot_loaded": True,
            "current_revision_loaded_in_gui": True,
            "ready_for_next_edit": True,
            "ready_for_calculation": False,
        },
        "live_request_summary": {
            "state": "current_revision_loaded",
            "explicit_hotload_requested": True,
            "hotload_safe_to_attempt": False,
        },
        "live_hotload_preflight": {
            "status": "current_revision_loaded",
            "safe_to_attempt_hotload": False,
            "gui_preflight_verified": True,
            "current_revision_loaded": True,
            "blocking_reasons": [],
        },
    }


def _successful_apply_continuation(
    *,
    tmp_path: Path,
    project_id: str,
    revision: int,
) -> dict[str, object]:
    structure = tmp_path / project_id / "outputs" / f"r{revision:03d}" / "model.cif"
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_text("data_applied\n", encoding="utf-8")
    response = _successful_open_continuation(
        project_id=project_id,
        revision=revision,
        open_payload={"structure_path": str(structure.resolve())},
    )
    response.update(
        {
            "execution_mode": "execute",
            "execution_started": True,
            "execution_deferred": False,
            "planned_outputs": {"structure": str(structure.resolve())},
            "result": {
                "success": True,
                "execution_backend": "fake_materialsscript",
            },
            "execution_transaction": {
                "execution_started": True,
                "execution_completed": True,
                "current_revision_still_current": True,
            },
        }
    )
    return response


def test_bundle_export_deferred_contract_accepts_exact_server_shape(
    tmp_path: Path,
) -> None:
    project_id = "smoke_bundle_contract"
    views = ["front", "top", "isometric"]
    response = _deferred_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=2,
        views=views,
        include_gui_snapshot=False,
    )

    contract = live_smoke._validate_deferred_bundle_export(
        response,
        expected_project_id=project_id,
        expected_revision=2,
        expected_views=views,
        expected_include_gui_snapshot=False,
        working_dir=str(tmp_path),
    )

    assert contract["ok"] is True
    assert contract["failures"] == []
    assert contract["retry_payload"] == response[
        "diagnostic_export_retry_payload"
    ]
    assert contract["workspace_identity"] == live_smoke._path_identity(tmp_path)


@pytest.mark.parametrize(
    ("mismatch", "expected_failure"),
    [
        ("project", "bundle_export_retry_payload_project_mismatch"),
        ("revision", "bundle_export_response_revision_mismatch"),
        ("views", "bundle_export_retry_payload_views_mismatch"),
        ("snapshot", "bundle_export_retry_payload_snapshot_mismatch"),
        ("response_mode", "bundle_export_retry_payload_response_mode_mismatch"),
        ("workspace", "bundle_export_retry_payload_workspace_mismatch"),
    ],
)
def test_bundle_export_deferred_contract_rejects_identity_mismatch(
    tmp_path: Path,
    mismatch: str,
    expected_failure: str,
) -> None:
    project_id = "smoke_bundle_contract_mismatch"
    views = ["front", "top"]
    response = _deferred_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=4,
        views=views,
        include_gui_snapshot=True,
    )
    payload = response["diagnostic_export_retry_payload"]
    assert isinstance(payload, dict)
    if mismatch == "project":
        payload["project_id"] = "another_project"
    elif mismatch == "revision":
        response["revision"] = 5
    elif mismatch == "views":
        payload["views"] = ["back"]
    elif mismatch == "snapshot":
        payload["include_gui_snapshot"] = False
    elif mismatch == "response_mode":
        payload["response_mode"] = "compact"
    elif mismatch == "workspace":
        payload["working_dir"] = str(tmp_path / "another_workspace")

    contract = live_smoke._validate_deferred_bundle_export(
        response,
        expected_project_id=project_id,
        expected_revision=4,
        expected_views=views,
        expected_include_gui_snapshot=True,
        working_dir=str(tmp_path),
    )

    assert contract["ok"] is False
    assert expected_failure in {
        failure["type"] for failure in contract["failures"]
    }


def test_bundle_export_receipt_rejects_unbound_or_inconsistent_artifacts(
    tmp_path: Path,
) -> None:
    project_id = "smoke_bundle_artifact_mismatch"
    response = _successful_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=1,
        views=["front", "top"],
    )
    manifest_path = Path(str(response["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revision"] = 2
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    projection_path = Path(str(response["files"]["view_projections_csv"]))
    with projection_path.open("a", encoding="utf-8") as handle:
        handle.write("top,Si3\n")
    transaction = response["report_write_transaction"]
    assert isinstance(transaction, dict)
    transaction["coverage"] = []

    verification = live_smoke._validate_bundle_export_continuation_receipt(
        response,
        project_id=project_id,
        revision=1,
        expected_views=["front", "top"],
        expected_workspace=tmp_path / "different_workspace",
    )

    failure_types = {
        failure["type"] for failure in verification["failures"]
    }
    assert {
        "bundle_export_manifest_outside_workspace",
        "bundle_export_manifest_revision_mismatch",
        "bundle_export_view_projections_outside_workspace",
        "bundle_export_view_projections_row_count_mismatch",
        "bundle_export_report_transaction_coverage_missing",
    } <= failure_types


def test_live_smoke_resumes_deferred_bundle_export_once_without_model_rerun(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_bundle"
    revision = 0
    views = ["front", "top", "isometric"]
    deferred = _deferred_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=revision,
        views=views,
        include_gui_snapshot=False,
    )
    completed = _successful_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=revision,
        views=views,
    )
    modeling_calls: list[str] = []
    current_calls: list[dict[str, object]] = []
    bundle_calls: list[dict[str, object]] = []
    report = {
        "normality": "preview_ready",
        "execution_mode": "preview",
        "normality_gate": {"status": "preview_only"},
        "gui": {"hot_loaded": False, "loaded_current_revision": False},
    }

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        assert len(modeling_calls) == 1
        return {
            "ok": True,
            "workflow": "create",
            "project_id": project_id,
            "revision": revision,
            "new_revision": revision,
            "execution_mode": "preview",
            "modeling_report": report,
        }

    def fake_current(**kwargs):
        current_calls.append(kwargs)
        return _current_revision_receipt(project_id, revision)

    def fake_bundle(**kwargs):
        bundle_calls.append(kwargs)
        if len(bundle_calls) == 1:
            return deferred
        if len(bundle_calls) == 2:
            assert kwargs == deferred["diagnostic_export_retry_payload"]
            return completed
        raise AssertionError("bundle continuation must not retry more than once")

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        fake_live,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda **kwargs: {
            "ok": True,
            "project_id": project_id,
            "revision": revision,
            "modeling_report": report,
        },
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        fake_current,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_export_view_bundle",
        fake_bundle,
    )

    result = live_smoke.run_live_smoke(
        request="Preview silicon and export standard view diagnostics.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        include_gui_status=False,
        take_snapshot=False,
        views=views,
        resume_deferred_bundle_export=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 1
    assert current_calls == [
        {"project_id": project_id, "working_dir": str(tmp_path.resolve())}
    ]
    assert len(bundle_calls) == 2
    assert bundle_calls[0] == bundle_calls[1]
    continuation = result["bundle_export_continuation"]
    assert continuation["status"] == "completed"
    assert continuation["completed"] is True
    assert continuation["bundle_export_call_count"] == 1
    assert continuation["current_revision_verified_before_export"] is True
    assert continuation["modeling_request_reinvoked"] is False
    assert continuation["execution_repeated"] is False
    assert continuation["runner_reinvoked"] is False
    assert continuation["gui_open_invoked"] is False
    assert result["bundle"] is completed
    assert result["summary"]["bundle_export_continuation_status"] == "completed"
    assert result["summary"]["bundle_export_continuation_completed"] is True


def test_bundle_export_continuation_stops_on_current_revision_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_bundle_revision_drift"
    response = _deferred_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=1,
        views=["front"],
        include_gui_snapshot=False,
    )
    bundle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 2),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_export_view_bundle",
        lambda **kwargs: bundle_calls.append(kwargs),
    )

    effective, receipt = live_smoke._resume_deferred_bundle_export(
        response,
        enabled=True,
        bundle_requested=True,
        expected_project_id=project_id,
        expected_revision=1,
        expected_views=["front"],
        expected_include_gui_snapshot=False,
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "current_revision_check_failed"
    assert receipt["bundle_export_invoked"] is False
    assert bundle_calls == []
    assert "current_revision_mismatch" in {
        failure["type"] for failure in receipt["failures"]
    }


def test_bundle_export_continuation_does_not_loop_when_retry_is_still_busy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_bundle_retry_busy"
    response = _deferred_bundle_export(
        tmp_path,
        project_id=project_id,
        revision=3,
        views=["front", "top"],
        include_gui_snapshot=True,
    )
    bundle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 3),
    )

    def fake_bundle(**kwargs):
        bundle_calls.append(kwargs)
        return response

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_export_view_bundle",
        fake_bundle,
    )

    effective, receipt = live_smoke._resume_deferred_bundle_export(
        response,
        enabled=True,
        bundle_requested=True,
        expected_project_id=project_id,
        expected_revision=3,
        expected_views=["front", "top"],
        expected_include_gui_snapshot=True,
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "bundle_export_failed"
    assert receipt["bundle_export_call_count"] == 1
    assert bundle_calls == [response["diagnostic_export_retry_payload"]]


def test_bundle_export_continuation_requires_explicit_flag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    response = _deferred_bundle_export(
        tmp_path,
        project_id="smoke_bundle_disabled",
        revision=0,
        views=["front"],
        include_gui_snapshot=False,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_export_view_bundle",
        lambda **kwargs: calls.append("bundle"),
    )

    effective, receipt = live_smoke._resume_deferred_bundle_export(
        response,
        enabled=False,
        bundle_requested=True,
        expected_project_id="smoke_bundle_disabled",
        expected_revision=0,
        expected_views=["front"],
        expected_include_gui_snapshot=False,
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "disabled"
    assert receipt["attempted"] is False
    assert calls == []


def test_live_smoke_bundle_resume_reports_export_disabled_without_calls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_bundle_export_disabled"
    live = {
        "ok": True,
        "workflow": "create",
        "project_id": project_id,
        "revision": 0,
        "new_revision": 0,
        "execution_mode": "preview",
        "modeling_report": {"execution_mode": "preview"},
    }
    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        lambda *args, **kwargs: live,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda **kwargs: {"ok": True, "project_id": project_id, "revision": 0},
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_export_view_bundle",
        lambda **kwargs: calls.append("bundle"),
    )

    result = live_smoke.run_live_smoke(
        request="Preview silicon.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        export_bundle=False,
        include_gui_status=False,
        take_snapshot=False,
        resume_deferred_bundle_export=True,
    )

    assert result["ok"] is True
    assert result["bundle"] is None
    assert result["bundle_export_continuation"]["status"] == (
        "bundle_export_disabled"
    )
    assert result["bundle_export_continuation"]["attempted"] is False
    assert result["summary"]["bundle_export_continuation_status"] == (
        "bundle_export_disabled"
    )
    assert calls == []


def test_server_preexecution_block_satisfies_live_smoke_contract(tmp_path: Path) -> None:
    project_id = "smoke_server_preexecution_contract"
    revision = 3
    views = ["front", "top"]
    retry_payload = live_smoke.server._gui_apply_current_execution_retry_payload(
        project_id=project_id,
        expected_revision=revision,
        open_in_gui=True,
        take_snapshot=True,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        export_view_audit=True,
        views=views,
        working_dir=tmp_path,
        timeout_seconds=90,
        response_mode="full",
    )
    blocked = live_smoke.server._with_gui_preexecution_hotload_block(
        {
            "ok": True,
            "project_id": project_id,
            "revision": revision,
            "new_revision": revision,
            "execution_mode": "execute",
            "planned_outputs": {
                "structure": str(tmp_path / project_id / "outputs" / "r003" / "model.cif")
            },
        },
        {
            "single_window_policy_ok": True,
            "activation_required_before_capture_or_input": True,
            "needs_activation": True,
            "target_window": {
                "handle": 101,
                "title": "structured-project - Materials Studio",
                "is_visible": True,
                "is_minimized": True,
                "is_foreground": False,
            },
            "window_management": {
                "single_window_policy_ok": True,
                "activation_required_before_capture_or_input": True,
                "needs_activation": True,
                "target_window_handle": 101,
                "target_window_title": "structured-project - Materials Studio",
                "target_window_is_visible": True,
                "target_window_is_minimized": True,
                "target_window_is_foreground": False,
            },
        },
        project_id=project_id,
        revision=revision,
        working_dir=tmp_path,
        views=views,
        execution_retry_payload=retry_payload,
    )

    contract = live_smoke._validate_preexecution_execution_block(
        blocked,
        working_dir=str(tmp_path),
    )

    assert blocked["status"] == "gui_activation_required_before_execution"
    assert contract["ok"] is True
    assert contract["failures"] == []
    assert contract["activation_payload"] == blocked["gui_activation_retry_payload"]
    assert contract["execution_payload"] == blocked["execution_retry_payload"]


def test_gui_transaction_hotload_contract_accepts_exact_server_shape(
    tmp_path: Path,
) -> None:
    response = _deferred_gui_transaction_live(
        tmp_path,
        project_id="smoke_gui_transaction_contract",
        revision=2,
        workflow="patch",
        views=["front", "top", "isometric"],
        take_snapshot=False,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
    )

    contract = live_smoke._validate_gui_transaction_hotload_block(
        response,
        working_dir=str(tmp_path),
    )

    assert contract["ok"] is True
    assert contract["failures"] == []
    assert contract["project_id"] == "smoke_gui_transaction_contract"
    assert contract["revision"] == 2
    assert contract["open_payload"] == response["gui_open_retry_payload"]


def test_live_smoke_resumes_gui_transaction_hotload_without_rerun(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_gui_transaction"
    views = ["front", "top", "isometric"]
    deferred = _deferred_gui_transaction_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
        views=views,
        take_snapshot=False,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
    )
    modeling_calls: list[str] = []
    open_calls: list[dict[str, object]] = []
    unexpected_calls: list[str] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        assert len(modeling_calls) == 1
        return deferred

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return _successful_open_continuation(
            project_id=project_id,
            revision=0,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 0),
    )
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: unexpected_calls.append("activate"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: unexpected_calls.append("apply"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon, hot-load it, fit it, and prepare standard views.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=views,
        take_snapshot=False,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 1
    assert unexpected_calls == []
    assert open_calls == [deferred["gui_open_retry_payload"]]
    assert open_calls[0]["fit_to_view_after_open"] is True
    assert open_calls[0]["prepare_view_replay_after_open"] is True
    continuation = result["base_hotload_continuation"]
    assert continuation["continuation_kind"] == (
        "gui_transaction_report_persistence"
    )
    assert continuation["status"] == "completed"
    assert continuation["open_structure_call_count"] == 1
    assert continuation["current_revision_verified_before_open"] is True
    assert continuation["execution_repeated"] is False
    assert continuation["runner_reinvoked"] is False
    assert result["live"]["execution_transaction"] == deferred["execution_transaction"]
    assert result["live"]["report_persistence_deferred"] is False
    assert result["live"]["gui_transaction_report_persistence_resolved"] is True
    assert result["live"]["execution_completed_before_gui_transaction"] is True
    assert "execution_completed_before_gui_activation" not in result["live"]
    assert "gui_open_retry_payload" not in result["live"]
    assert result["summary"]["postexecution_hotload_continuation_status"] == (
        "completed"
    )


def test_live_smoke_resumes_followup_gui_transaction_without_third_modeling_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_followup_gui_transaction"
    base = _successful_apply_continuation(
        tmp_path=tmp_path,
        project_id=project_id,
        revision=0,
    )
    base.update({"workflow": "create", "revision": 0, "new_revision": 0})
    deferred = _deferred_gui_transaction_live(
        tmp_path,
        project_id=project_id,
        revision=1,
        workflow="patch",
        views=["front", "top"],
    )
    modeling_calls: list[str] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        if len(modeling_calls) == 1:
            return base
        if len(modeling_calls) == 2:
            assert kwargs["project_id"] == project_id
            return deferred
        raise AssertionError("GUI transaction continuation must not invoke modeling again")

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return _successful_open_continuation(
            project_id=project_id,
            revision=1,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 1),
    )
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 1),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        follow_up_request="Move one atom and hot-load the new revision.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=["front", "top"],
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 2
    assert len(open_calls) == 1
    assert result["base_hotload_continuation"]["status"] == "not_required"
    continuation = result["followup_hotload_continuation"]
    assert continuation["continuation_kind"] == (
        "gui_transaction_report_persistence"
    )
    assert continuation["status"] == "completed"
    assert continuation["open_structure_call_count"] == 1
    assert result["summary"]["followup_postexecution_hotload_continuation_status"] == (
        "completed"
    )


def test_preexecution_apply_can_chain_into_gui_transaction_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_preexecution_to_gui_transaction"
    blocked = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
        views=["front", "top"],
    )
    deferred = _deferred_gui_transaction_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="gui_apply_current_revision",
        views=["front", "top"],
    )
    deferred["execution_result"] = deferred.pop("result")
    current_calls: list[dict[str, object]] = []
    apply_calls: list[dict[str, object]] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        lambda *args, **kwargs: blocked,
    )

    def fake_current(**kwargs):
        current_calls.append(kwargs)
        return _current_revision_receipt(project_id, 0)

    def fake_apply(**kwargs):
        apply_calls.append(kwargs)
        assert len(apply_calls) == 1
        return deferred

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return _successful_open_continuation(
            project_id=project_id,
            revision=0,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_model_get_current", fake_current)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: _successful_activation(project_id, 0),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        fake_apply,
    )
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=["front", "top"],
        resume_deferred_execution=True,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(current_calls) == 3
    assert len(apply_calls) == 1
    assert len(open_calls) == 1
    pre = result["base_preexecution_execution_continuation"]
    post = result["base_hotload_continuation"]
    assert pre["status"] == "execution_completed_gui_transaction_required"
    assert pre["apply_current_revision_call_count"] == 1
    assert pre["gui_transaction_hotload_deferred"] is True
    assert post["continuation_kind"] == "gui_transaction_report_persistence"
    assert post["status"] == "completed"
    assert post["execution_repeated"] is False


def test_failed_preexecution_apply_verification_blocks_gui_transaction_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_preexecution_verified_project"
    wrong_project_id = "smoke_preexecution_wrong_project"
    blocked = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
    )
    deferred = _deferred_gui_transaction_live(
        tmp_path,
        project_id=wrong_project_id,
        revision=0,
        workflow="gui_apply_current_revision",
    )
    current_calls: list[dict[str, object]] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        lambda *args, **kwargs: blocked,
    )

    def fake_current(**kwargs):
        current_calls.append(kwargs)
        return _current_revision_receipt(project_id, 0)

    monkeypatch.setattr(live_smoke.server, "material_studio_model_get_current", fake_current)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: _successful_activation(project_id, 0),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: deferred,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: open_calls.append(kwargs),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        resume_deferred_execution=True,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is False
    assert len(current_calls) == 2
    assert open_calls == []
    pre = result["base_preexecution_execution_continuation"]
    post = result["base_hotload_continuation"]
    assert pre["status"] == "apply_verification_failed"
    assert "apply_response_project_mismatch" in {
        item["type"] for item in pre["failures"]
    }
    assert post["status"] == "preexecution_execution_continuation_not_verified"
    assert post["attempted"] is False
    assert post["failures"] == [
        {"type": "preexecution_execution_continuation_not_completed"}
    ]


@pytest.mark.parametrize(
    ("mismatch", "expected_failure"),
    [
        ("project", "gui_transaction_open_payload_project_mismatch"),
        ("revision", "gui_transaction_open_payload_revision_mismatch"),
        (
            "workspace",
            "gui_transaction_open_payload_requested_workspace_mismatch",
        ),
        (
            "workspace_missing",
            "gui_transaction_open_payload_workspace_missing",
        ),
        (
            "view_audit",
            "gui_transaction_open_payload_view_audit_gate_missing",
        ),
        ("execution_result", "gui_transaction_result_not_successful"),
        ("structure", "gui_transaction_open_payload_structure_mismatch"),
        ("fit", "gui_transaction_fit_to_view_payload_mismatch"),
    ],
)
def test_gui_transaction_contract_rejects_mismatch_before_open(
    monkeypatch,
    tmp_path: Path,
    mismatch: str,
    expected_failure: str,
) -> None:
    response = _deferred_gui_transaction_live(
        tmp_path,
        project_id="smoke_gui_transaction_binding",
        revision=2,
        workflow="patch",
        fit_to_view_after_open=mismatch == "fit",
    )
    open_payload = response["gui_open_retry_payload"]
    if mismatch == "project":
        open_payload["project_id"] = "wrong_project"
    elif mismatch == "revision":
        open_payload["revision"] = 3
    elif mismatch == "workspace":
        open_payload["working_dir"] = str((tmp_path / "other_workspace").resolve())
    elif mismatch == "workspace_missing":
        open_payload.pop("working_dir")
    elif mismatch == "view_audit":
        open_payload["export_view_audit"] = False
    elif mismatch == "execution_result":
        response["execution_result"] = {"success": False}
    elif mismatch == "structure":
        other = tmp_path / "other.cif"
        other.write_text("data_other\n", encoding="utf-8")
        open_payload["structure_path"] = str(other.resolve())
    elif mismatch == "fit":
        open_payload.pop("fit_to_view_after_open")

    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: calls.append("open"),
    )

    effective, receipt = live_smoke._resume_postexecution_hotload(
        response,
        enabled=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "contract_rejected"
    assert receipt["eligible"] is False
    assert receipt["attempted"] is False
    assert expected_failure in {item["type"] for item in receipt["failures"]}
    assert calls == []


def test_gui_transaction_current_revision_change_stops_before_open(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_gui_transaction_current_advanced"
    response = _deferred_gui_transaction_live(
        tmp_path,
        project_id=project_id,
        revision=1,
        workflow="patch",
    )
    open_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 2),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: open_calls.append(kwargs),
    )

    effective, receipt = live_smoke._resume_postexecution_hotload(
        response,
        enabled=True,
        phase="followup",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "current_revision_check_failed"
    assert receipt["open_structure_invoked"] is False
    assert open_calls == []


def test_gui_transaction_open_failure_does_not_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_gui_transaction_open_failure"
    response = _deferred_gui_transaction_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
    )
    open_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 0),
    )

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return {
            "ok": False,
            "status": "gui_activation_required_before_open",
            "error": "focus changed before artifact open",
            "project_id": project_id,
            "revision": 0,
        }

    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)

    effective, receipt = live_smoke._resume_postexecution_hotload(
        response,
        enabled=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective["ok"] is False
    assert effective["status"] == "gui_transaction_hotload_continuation_failed"
    assert receipt["status"] == "open_failed"
    assert receipt["open_structure_call_count"] == 1
    assert len(open_calls) == 1
    assert receipt["execution_repeated"] is False


def test_gui_transaction_continuation_is_disabled_without_explicit_flag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    response = _deferred_gui_transaction_live(
        tmp_path,
        project_id="smoke_gui_transaction_disabled",
        revision=0,
        workflow="create",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: calls.append("open"),
    )

    effective, receipt = live_smoke._resume_postexecution_hotload(
        response,
        enabled=False,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "disabled"
    assert receipt["attempted"] is False
    assert calls == []


def test_live_smoke_resumes_base_preexecution_once_without_recreating_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_preexecution_base"
    views = ["front", "top", "isometric"]
    blocked = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
        views=views,
        take_snapshot=False,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
        timeout_seconds=123,
    )
    modeling_calls: list[str] = []
    current_calls: list[dict[str, object]] = []
    activation_calls: list[dict[str, object]] = []
    apply_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        assert len(modeling_calls) == 1
        return blocked

    def fake_current(**kwargs):
        current_calls.append(kwargs)
        return _current_revision_receipt(project_id, 0)

    def fake_activate(**kwargs):
        activation_calls.append(kwargs)
        return _successful_activation(project_id, 0)

    def fake_apply(**kwargs):
        apply_calls.append(kwargs)
        return _successful_apply_continuation(
            tmp_path=tmp_path,
            project_id=project_id,
            revision=0,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_model_get_current", fake_current)
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_activate", fake_activate)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        fake_apply,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it in the existing window.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=views,
        timeout_seconds=123,
        take_snapshot=False,
        resume_deferred_execution=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 1
    assert current_calls == [
        {"project_id": project_id, "working_dir": str(tmp_path.resolve())},
        {"project_id": project_id, "working_dir": str(tmp_path.resolve())},
    ]
    assert activation_calls == [blocked["gui_activation_retry_payload"]]
    assert apply_calls == [blocked["execution_retry_payload"]]
    assert apply_calls[0]["fit_to_view_after_open"] is True
    assert apply_calls[0]["prepare_view_replay_after_open"] is True
    assert apply_calls[0]["views"] == views
    assert apply_calls[0]["timeout_seconds"] == 123
    continuation = result["base_preexecution_execution_continuation"]
    assert continuation["status"] == "completed"
    assert continuation["completed"] is True
    assert continuation["apply_current_revision_call_count"] == 1
    assert continuation["current_revision_verified_before_activation"] is True
    assert continuation["current_revision_verified_after_activation"] is True
    assert continuation["modeling_request_reinvoked"] is False
    assert continuation["revision_created"] is False
    assert continuation["runner_reinvoked"] is False
    assert result["live"]["workflow"] == "create"
    assert result["live"]["new_revision"] == 0
    assert result["live"]["gui_preexecution_block"] is None
    assert "gui_activation_retry_payload" not in result["live"]
    assert "execution_retry_payload" not in result["live"]
    summary = result["summary"]
    assert summary["preexecution_execution_continuation_status"] == "completed"
    assert summary["preexecution_execution_continuation_apply_call_count"] == 1
    assert summary["preexecution_execution_continuation_failures"] == []


def test_live_smoke_resumes_followup_preexecution_without_third_modeling_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_preexecution_followup"
    base = _successful_apply_continuation(
        tmp_path=tmp_path,
        project_id=project_id,
        revision=0,
    )
    base.update({"workflow": "create", "revision": 0, "new_revision": 0})
    blocked = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=1,
        workflow="patch",
        views=["front", "top"],
    )
    modeling_calls: list[str] = []
    apply_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        if len(modeling_calls) == 1:
            return base
        if len(modeling_calls) == 2:
            assert kwargs["project_id"] == project_id
            return blocked
        raise AssertionError("execution continuation must not invoke modeling again")

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 1),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: _successful_activation(project_id, 1),
    )

    def fake_apply(**kwargs):
        apply_calls.append(kwargs)
        return _successful_apply_continuation(
            tmp_path=tmp_path,
            project_id=project_id,
            revision=1,
        )

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        fake_apply,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 1),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        follow_up_request="Move one atom and hot-load the current revision.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=["front", "top"],
        resume_deferred_execution=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 2
    assert len(apply_calls) == 1
    assert result["base_preexecution_execution_continuation"]["status"] == (
        "not_required"
    )
    continuation = result["followup_preexecution_execution_continuation"]
    assert continuation["status"] == "completed"
    assert continuation["apply_current_revision_call_count"] == 1
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["new_revision"] == 1
    assert result["summary"][
        "followup_preexecution_execution_continuation_status"
    ] == "completed"


def test_live_smoke_preexecution_apply_then_postexecution_hotload_runs_apply_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_pre_then_postexecution"
    views = ["front", "top", "isometric"]
    blocked = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
        views=views,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
    )
    postexecution = _deferred_postexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="gui_apply_current_revision",
        views=views,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
    )
    postexecution["execution_started"] = True
    modeling_calls: list[str] = []
    activation_calls: list[dict[str, object]] = []
    apply_calls: list[dict[str, object]] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {"ok": True, "state": "ready", "blocking_reasons": []},
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        assert len(modeling_calls) == 1
        return blocked

    def fake_activate(**kwargs):
        activation_calls.append(kwargs)
        return _successful_activation(project_id, 0)

    def fake_apply(**kwargs):
        apply_calls.append(kwargs)
        assert len(apply_calls) == 1
        return postexecution

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return _successful_open_continuation(
            project_id=project_id,
            revision=0,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 0),
    )
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_activate", fake_activate)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        fake_apply,
    )
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon, hot-load it, fit it, and prepare standard views.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=views,
        resume_deferred_execution=True,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 1
    assert len(apply_calls) == 1
    assert len(activation_calls) == 2
    assert len(open_calls) == 1
    assert open_calls[0] == postexecution["gui_open_retry_payload"]
    assert open_calls[0]["fit_to_view_after_open"] is True
    assert open_calls[0]["prepare_view_replay_after_open"] is True
    pre = result["base_preexecution_execution_continuation"]
    post = result["base_hotload_continuation"]
    assert pre["status"] == "execution_completed_gui_activation_required"
    assert pre["apply_current_revision_call_count"] == 1
    assert post["status"] == "completed"
    assert post["execution_repeated"] is False
    assert post["runner_reinvoked"] is False
    assert result["summary"]["preexecution_execution_continuation_status"] == (
        "completed"
    )
    assert result["summary"]["postexecution_hotload_continuation_status"] == (
        "completed"
    )


def test_preexecution_activation_failure_does_not_apply(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_preexecution_activation_failure"
    response = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
    )
    apply_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 0),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: {
            "ok": True,
            "activation_verified": True,
            "window_identity_stable_after_activation": True,
            "single_window_policy_ok": True,
            "snapshot_status": "deferred_before_capture",
            "snapshot_deferred": True,
            "snapshot_focus_lost_after_activation": True,
            "gui_action_context": {"project_id": project_id, "revision": 0},
            "window_management": {
                "activation_required_before_capture_or_input": False,
                "single_window_policy_ok": True,
            },
        },
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: apply_calls.append(kwargs),
    )

    effective, receipt = live_smoke._resume_preexecution_execution(
        response,
        enabled=True,
        execution_authorized=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "activation_failed"
    assert receipt["attempted"] is True
    assert receipt["apply_current_revision_invoked"] is False
    assert apply_calls == []
    assert any(
        item["type"] == "activation_snapshot_deferred_before_capture"
        for item in receipt["failures"]
    )


def test_preexecution_current_revision_change_after_activation_stops_apply(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_preexecution_current_advanced"
    response = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=2,
        workflow="patch",
    )
    current_revisions = iter([2, 3])
    apply_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(
            project_id,
            next(current_revisions),
        ),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: _successful_activation(project_id, 2),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: apply_calls.append(kwargs),
    )

    effective, receipt = live_smoke._resume_preexecution_execution(
        response,
        enabled=True,
        execution_authorized=True,
        phase="followup",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "current_revision_recheck_failed"
    assert receipt["activation_completed"] is True
    assert receipt["apply_current_revision_invoked"] is False
    assert apply_calls == []
    assert any(
        item["type"] in {"current_revision_mismatch", "current_spec_revision_mismatch"}
        for item in receipt["failures"]
    )


@pytest.mark.parametrize(
    ("mismatch", "expected_failure"),
    [
        ("project", "activation_payload_project_mismatch"),
        ("revision", "activation_payload_revision_mismatch"),
        ("workspace", "continuation_payload_requested_workspace_mismatch"),
        ("apply_project", "execution_payload_project_mismatch"),
    ],
)
def test_preexecution_contract_rejects_identity_mismatch_before_gui(
    monkeypatch,
    tmp_path: Path,
    mismatch: str,
    expected_failure: str,
) -> None:
    response = _deferred_preexecution_live(
        tmp_path,
        project_id="smoke_preexecution_binding",
        revision=2,
        workflow="patch",
    )
    activation_payload = response["gui_activation_retry_payload"]
    execution_payload = response["execution_retry_payload"]
    block = response["gui_preexecution_block"]
    if mismatch == "project":
        activation_payload["project_id"] = "wrong_project"
        block["activation_payload"] = dict(activation_payload)
    elif mismatch == "revision":
        activation_payload["revision"] = 3
        block["activation_payload"] = dict(activation_payload)
    elif mismatch == "workspace":
        wrong_workspace = str((tmp_path / "other_workspace").resolve())
        activation_payload["working_dir"] = wrong_workspace
        execution_payload["working_dir"] = wrong_workspace
        block["activation_payload"] = dict(activation_payload)
        block["execution_retry_payload"] = dict(execution_payload)
    else:
        execution_payload["project_id"] = "wrong_project"
        block["execution_retry_payload"] = dict(execution_payload)

    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: calls.append("activate"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: calls.append("apply"),
    )

    effective, receipt = live_smoke._resume_preexecution_execution(
        response,
        enabled=True,
        execution_authorized=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "contract_rejected"
    assert receipt["eligible"] is False
    assert receipt["attempted"] is False
    assert expected_failure in {item["type"] for item in receipt["failures"]}
    assert calls == []


@pytest.mark.parametrize(
    "failure_status",
    [
        "revision_execution_busy",
        "current_revision_execution_block",
        "revision_execution_identity_mismatch",
    ],
)
def test_preexecution_apply_failure_stops_without_retry_or_postopen(
    monkeypatch,
    tmp_path: Path,
    failure_status: str,
) -> None:
    project_id = "smoke_preexecution_apply_failure"
    response = _deferred_preexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
    )
    apply_calls: list[dict[str, object]] = []
    open_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: _current_revision_receipt(project_id, 0),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: _successful_activation(project_id, 0),
    )

    def fake_apply(**kwargs):
        apply_calls.append(kwargs)
        return {
            "ok": False,
            "status": failure_status,
            "error": "explicit review required",
            "project_id": project_id,
            "revision": 0,
            "execution_mode": "execute",
            "execution_started": False,
        }

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        fake_apply,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: open_calls.append(kwargs),
    )

    effective, receipt = live_smoke._resume_preexecution_execution(
        response,
        enabled=True,
        execution_authorized=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective["ok"] is False
    assert effective["status"] == failure_status
    assert receipt["status"] == "apply_failed"
    assert receipt["apply_current_revision_call_count"] == 1
    assert len(apply_calls) == 1
    assert open_calls == []


def test_preexecution_resume_requires_explicit_execute_authorization(
    monkeypatch,
    tmp_path: Path,
) -> None:
    response = _deferred_preexecution_live(
        tmp_path,
        project_id="smoke_preexecution_explicit_execute",
        revision=0,
        workflow="create",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_model_get_current",
        lambda **kwargs: calls.append("current"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: calls.append("activate"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: calls.append("apply"),
    )

    effective, receipt = live_smoke._resume_preexecution_execution(
        response,
        enabled=True,
        execution_authorized=False,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "explicit_execute_required"
    assert receipt["attempted"] is False
    assert calls == []


def test_live_smoke_resumes_base_postexecution_hotload_without_rerun(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_base"
    views = ["front", "top", "isometric"]
    blocked = _deferred_postexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
        views=views,
        fit_to_view_after_open=True,
        prepare_view_replay_after_open=True,
    )
    modeling_calls: list[str] = []
    activation_calls: list[dict[str, object]] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        assert len(modeling_calls) == 1
        return blocked

    def fake_activate(**kwargs):
        activation_calls.append(kwargs)
        return _successful_activation(project_id, 0)

    def fake_open(**kwargs):
        open_calls.append(kwargs)
        return _successful_open_continuation(
            project_id=project_id,
            revision=0,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_activate", fake_activate)
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon, hot-load it, fit it, and prepare standard views.",
        hotload=True,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=views,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 1
    assert activation_calls == [blocked["gui_activation_retry_payload"]]
    assert open_calls == [blocked["gui_open_retry_payload"]]
    assert open_calls[0]["fit_to_view_after_open"] is True
    assert open_calls[0]["prepare_view_replay_after_open"] is True
    assert open_calls[0]["views"] == views
    continuation = result["base_hotload_continuation"]
    assert continuation["status"] == "completed"
    assert continuation["completed"] is True
    assert continuation["original_block"] == blocked["gui_postexecution_block"]
    assert continuation["activation_receipt"]["activation_verified"] is True
    assert continuation["open_continuation_receipt"]["ok"] is True
    assert continuation["runner_reinvoked"] is False
    assert continuation["execution_repeated"] is False
    assert result["live"]["execution_transaction"] == blocked["execution_transaction"]
    assert result["live"]["execution_must_not_repeat"] is True
    assert result["live"]["structure_reopened"] is True
    assert result["live"]["gui_postexecution_block"] is None
    assert "gui_activation_retry_payload" not in result["live"]
    assert "gui_open_retry_payload" not in result["live"]
    assert result["live"]["next_action_plan"]["recommended_tool"] == (
        "material_studio_live_project_status"
    )
    summary = result["summary"]
    assert summary["postexecution_hotload_continuation_status"] == "completed"
    assert summary["postexecution_hotload_continuation_attempted"] is True
    assert summary["postexecution_hotload_continuation_completed"] is True
    assert summary["postexecution_hotload_continuation_failures"] == []


def test_live_smoke_resumes_followup_postexecution_hotload_without_third_modeling_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_followup"
    base = _successful_open_continuation(
        project_id=project_id,
        revision=0,
        open_payload={"structure_path": str(tmp_path / "base.cif")},
    )
    base.update(
        {
            "workflow": "create",
            "new_revision": 0,
            "execution_mode": "execute",
            "result": {"success": True},
            "planned_outputs": {"structure": str(tmp_path / "base.cif")},
        }
    )
    followup_block = _deferred_postexecution_live(
        tmp_path,
        project_id=project_id,
        revision=1,
        workflow="patch",
        views=["front", "top"],
    )
    modeling_calls: list[str] = []
    gui_calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        if len(modeling_calls) == 1:
            return base
        if len(modeling_calls) == 2:
            assert kwargs["project_id"] == project_id
            return followup_block
        raise AssertionError("post-execution continuation must not invoke modeling again")

    def fake_activate(**kwargs):
        gui_calls.append(("activate", kwargs))
        return _successful_activation(project_id, 1)

    def fake_open(**kwargs):
        gui_calls.append(("open", kwargs))
        return _successful_open_continuation(
            project_id=project_id,
            revision=1,
            open_payload=kwargs,
        )

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_activate", fake_activate)
    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fake_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 1),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        follow_up_request="Move one atom and hot-load the current revision.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        views=["front", "top"],
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert len(modeling_calls) == 2
    assert [name for name, _ in gui_calls] == ["activate", "open"]
    assert result["base_hotload_continuation"]["status"] == "not_required"
    assert result["followup_hotload_continuation"]["status"] == "completed"
    assert result["followup_live"]["workflow"] == "patch"
    assert result["followup_live"]["new_revision"] == 1
    assert result["summary"]["followup_postexecution_hotload_continuation_status"] == (
        "completed"
    )


def test_live_smoke_activation_failure_does_not_open_or_run_followup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_id = "smoke_resume_activation_failure"
    blocked = _deferred_postexecution_live(
        tmp_path,
        project_id=project_id,
        revision=0,
        workflow="create",
    )
    modeling_calls: list[str] = []
    open_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )

    def fake_live(user_request, **kwargs):
        modeling_calls.append(user_request)
        if len(modeling_calls) > 1:
            raise AssertionError("follow-up must remain blocked after activation failure")
        return blocked

    monkeypatch.setattr(live_smoke.server, "material_studio_live_modeling_request", fake_live)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: {
            "ok": True,
            "activation_verified": True,
            "window_identity_stable_after_activation": True,
            "single_window_policy_ok": True,
            "snapshot_status": "deferred_before_capture",
            "snapshot_deferred": True,
            "snapshot_focus_lost_after_activation": True,
            "gui_action_context": {"project_id": project_id, "revision": 0},
            "window_management": {
                "activation_required_before_capture_or_input": False,
                "single_window_policy_ok": True,
            },
        },
    )

    def fail_open(**kwargs):
        open_calls.append(kwargs)
        raise AssertionError("open must not run after failed activation")

    monkeypatch.setattr(live_smoke.server, "material_studio_gui_open_structure", fail_open)
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: _successful_live_status(project_id, 0),
    )

    result = live_smoke.run_live_smoke(
        request="Build silicon and hot-load it.",
        follow_up_request="Apply one more live edit.",
        scenario=None,
        execution_mode="execute",
        working_dir=str(tmp_path),
        export_bundle=False,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is False
    assert len(modeling_calls) == 1
    assert open_calls == []
    continuation = result["base_hotload_continuation"]
    assert continuation["status"] == "activation_failed"
    assert continuation["attempted"] is True
    assert continuation["completed"] is False
    assert any(
        item["type"] == "activation_snapshot_deferred_before_capture"
        for item in continuation["failures"]
    )
    assert result["followup_hotload_continuation"]["status"] == "base_request_not_ready"


@pytest.mark.parametrize(
    ("mismatch", "expected_failure"),
    [
        ("project", "activation_payload_project_mismatch"),
        ("revision", "open_payload_revision_mismatch"),
        ("workspace", "continuation_payload_requested_workspace_mismatch"),
    ],
)
def test_postexecution_hotload_contract_rejects_payload_identity_mismatch_before_gui(
    monkeypatch,
    tmp_path: Path,
    mismatch: str,
    expected_failure: str,
) -> None:
    response = _deferred_postexecution_live(
        tmp_path,
        project_id="smoke_payload_binding",
        revision=2,
        workflow="patch",
    )
    activation_payload = response["gui_activation_retry_payload"]
    open_payload = response["gui_open_retry_payload"]
    block = response["gui_postexecution_block"]
    if mismatch == "project":
        activation_payload["project_id"] = "wrong_project"
        block["activation_payload"] = dict(activation_payload)
    elif mismatch == "revision":
        open_payload["revision"] = 3
        block["gui_open_retry_payload"] = dict(open_payload)
    else:
        wrong_workspace = str((tmp_path / "other_workspace").resolve())
        activation_payload["working_dir"] = wrong_workspace
        open_payload["working_dir"] = wrong_workspace
        block["activation_payload"] = dict(activation_payload)
        block["gui_open_retry_payload"] = dict(open_payload)

    gui_calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: gui_calls.append("activate"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: gui_calls.append("open"),
    )

    effective, receipt = live_smoke._resume_postexecution_hotload(
        response,
        enabled=True,
        phase="base",
        working_dir=str(tmp_path),
    )

    assert effective is response
    assert receipt["status"] == "contract_rejected"
    assert receipt["eligible"] is False
    assert receipt["attempted"] is False
    assert expected_failure in {item["type"] for item in receipt["failures"]}
    assert gui_calls == []


def test_live_smoke_resume_flag_is_explicit_and_preview_does_not_touch_gui(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parser = live_smoke._build_parser()
    assert parser.parse_args([]).resume_deferred_execution is False
    assert (
        parser.parse_args(["--resume-deferred-execution"]).resume_deferred_execution
        is True
    )
    assert parser.parse_args([]).resume_deferred_hotload is False
    assert parser.parse_args(["--resume-deferred-hotload"]).resume_deferred_hotload is True
    assert parser.parse_args([]).resume_deferred_bundle_export is False
    assert (
        parser.parse_args(
            ["--resume-deferred-bundle-export"]
        ).resume_deferred_bundle_export
        is True
    )

    live = {
        "ok": True,
        "workflow": "create",
        "project_id": "preview_no_gui_resume",
        "revision": 0,
        "new_revision": 0,
        "execution_mode": "preview",
        "planned_outputs": {"structure": str(tmp_path / "preview.cif")},
        "modeling_report": {
            "normality": "preview_ready",
            "execution_mode": "preview",
            "normality_gate": {"status": "preview_only"},
            "gui": {"hot_loaded": False, "loaded_current_revision": False},
        },
    }
    gui_calls: list[str] = []
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_session_preflight",
        lambda **kwargs: {
            "ok": True,
            "state": "ready_for_new_model",
            "blocking_reasons": [],
            "review_reasons": [],
        },
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_modeling_request",
        lambda *args, **kwargs: live,
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_activate",
        lambda **kwargs: gui_calls.append("activate"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_open_structure",
        lambda **kwargs: gui_calls.append("open"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_gui_apply_current_revision",
        lambda **kwargs: gui_calls.append("apply"),
    )
    monkeypatch.setattr(
        live_smoke.server,
        "material_studio_live_project_status",
        lambda project_id, **kwargs: {
            "ok": True,
            "project_id": project_id,
            "revision": 0,
            "modeling_report": live["modeling_report"],
        },
    )

    result = live_smoke.run_live_smoke(
        request="Preview silicon.",
        execution_mode="preview",
        working_dir=str(tmp_path),
        export_bundle=False,
        take_snapshot=False,
        resume_deferred_execution=True,
        resume_deferred_hotload=True,
    )

    assert result["ok"] is True
    assert gui_calls == []
    assert result["base_preexecution_execution_continuation"]["status"] == (
        "not_required"
    )
    assert result["base_hotload_continuation"]["status"] == "not_required"
    assert result["summary"]["preexecution_execution_continuation_status"] == (
        "not_required"
    )
    assert result["summary"]["postexecution_hotload_continuation_status"] == "not_required"


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
