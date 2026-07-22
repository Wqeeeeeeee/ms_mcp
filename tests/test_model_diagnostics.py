from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from material_studio_mcp_server.castep_relaxation import build_relaxed_revision_spec
from material_studio_mcp_server.diagnostics import (
    SEMICONDUCTOR_REFERENCE_ELECTRONIC_PROPERTIES,
    _semiconductor_oxide_interface_geometry_summary,
    model_view_audit,
    write_view_audit_bundle,
    write_view_audit_report,
)
from material_studio_mcp_server.health import build_modeling_health
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.specs import SemanticPatch, apply_semantic_patch
from material_studio_mcp_server.specs.castep import CastepEnergySpec, CastepTask
from material_studio_mcp_server.specs.project import ModelSpec


def load_example(name: str) -> ModelSpec:
    path = Path("src/material_studio_mcp_server/examples") / name
    return ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _live_status_health_response(tmp_path: Path) -> dict:
    structure_path = tmp_path / "structure_r004.cif"
    structure_path.write_text("current revision structure", encoding="utf-8")
    return {
        "ok": True,
        "project_id": "live_status_health",
        "revision": 4,
        "planned_outputs": {"structure": str(structure_path)},
        "result": {"success": True},
        "gui_status": {"window_found": True},
        "live_status_hotload_evidence": {
            "schema_version": "material_studio_live_status_hotload_evidence_v1",
            "available": True,
            "verified": True,
            "status": "verified_current_revision_loaded",
            "blocking_reasons": [],
            "project_id": "live_status_health",
            "revision": 4,
            "structure_path": str(structure_path),
            "target_window_handle": 101,
            "target_window_title": "msmcp_r004_health - Materials Studio",
            "result_success": True,
            "process_count": 1,
            "window_count": 1,
            "gui_input_performed": False,
            "structure_reopened": False,
            "gui_process_launched": False,
            "observation_only": True,
        },
    }


def test_modeling_health_accepts_strict_live_status_hotload_evidence(
    tmp_path: Path,
) -> None:
    health = build_modeling_health(
        _live_status_health_response(tmp_path),
        execution_mode="execute",
    )

    assert health["verdict"] == "passed"
    assert health["checks"]["gui_opened"] is True
    assert health["checks"]["gui_loaded_current_revision"] is True
    assert health["checks"]["gui_hot_loaded_from_live_status"] is True
    assert health["checks"]["gui_hotload_evidence_source"] == (
        "live_status_current_revision"
    )
    assert health["checks"]["gui_input_performed_by_current_request"] is False
    assert "GUI hot-load was not performed" not in "\n".join(health["warnings"])


@pytest.mark.parametrize(
    "mutation",
    ["multiple_processes", "gui_input_performed", "revision_mismatch", "structure_mismatch"],
)
def test_modeling_health_rejects_incomplete_live_status_hotload_evidence(
    mutation: str,
    tmp_path: Path,
) -> None:
    response = _live_status_health_response(tmp_path)
    evidence = response["live_status_hotload_evidence"]
    if mutation == "multiple_processes":
        evidence["process_count"] = 2
    elif mutation == "gui_input_performed":
        evidence["gui_input_performed"] = True
    elif mutation == "revision_mismatch":
        evidence["revision"] = 3
    else:
        evidence["structure_path"] = str(tmp_path / "other_structure.cif")

    health = build_modeling_health(response, execution_mode="execute")

    assert health["verdict"] == "passed_with_warnings"
    assert health["checks"]["gui_opened"] is False
    assert health["checks"]["gui_hot_loaded_from_live_status"] is False
    assert "GUI hot-load was not performed" in "\n".join(health["warnings"])


def test_common_iii_v_reference_electronic_properties_cover_band_alignment_preflight() -> None:
    for material in ("GaP", "AlP", "InP", "GaSb", "AlSb", "InSb"):
        properties = SEMICONDUCTOR_REFERENCE_ELECTRONIC_PROPERTIES[material]
        assert properties["electron_affinity_ev"] > 0
        assert properties["band_gap_ev"] > 0

    spec = load_example("gallium_arsenide_aluminum_arsenide_001_heterostructure_spec.json")
    metadata = {
        **(spec.metadata or {}),
        "materials": ["GaP", "AlP", "InP", "GaSb", "AlSb", "InSb"],
        "interface": "GaP/AlP/InP/GaSb/AlSb/InSb",
        "substrate": "GaP",
    }
    metadata.pop("material_electronic_properties", None)
    patched = spec.model_copy(update={"metadata": metadata})

    band_alignment = model_view_audit(patched)["health"]["semiconductor_health"]["band_alignment_summary"]
    assert band_alignment["reference_material"] == "GaP"
    assert band_alignment["missing_property_count"] == 0
    assert not any("Missing" in warning for warning in band_alignment["warnings"])
    offsets_by_material = {item["material"]: item for item in band_alignment["offsets"]}
    assert offsets_by_material["AlP"]["material_electron_affinity_ev"] == 3.5
    assert offsets_by_material["InP"]["material_band_gap_ev"] == 1.34
    assert offsets_by_material["GaSb"]["material_electron_affinity_ev"] == 4.06
    assert offsets_by_material["InSb"]["material_band_gap_ev"] == 0.17


def test_stale_dopant_site_metadata_is_a_structural_consistency_error(tmp_path: Path) -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    doped = ModelSpec.model_validate(plan.payload)
    stale_atoms = [
        atom.model_copy(update={"element": "Si"}) if atom.id == "Si1_000" else atom
        for atom in doped.model.basis_atoms
    ]
    stale = doped.model_copy(update={"model": doped.model.model_copy(update={"basis_atoms": stale_atoms})})

    audit = model_view_audit(stale)
    dopant_sites = audit["health"]["semiconductor_health"]["dopant_site_summary"]

    assert audit["health"]["ok"] is False
    assert dopant_sites["raw_site_count"] == 1
    assert dopant_sites["site_count"] == 0
    assert dopant_sites["stale_site_count"] == 1
    assert dopant_sites["metadata_consistent"] is False
    assert dopant_sites["carrier_type_hint"] is None
    assert dopant_sites["latest"] is None
    assert dopant_sites["next_action"] == "reconcile_dopant_metadata_with_current_structure_then_reaudit"
    assert dopant_sites["recommended_tool"] == "material_studio_project_reconcile_dopant_metadata"
    assert dopant_sites["stale_entries"][0]["record_status"] == "actual_element_mismatch"
    error = "Dopant-site metadata references Si1_000 as P, but the current structure contains Si."
    assert dopant_sites["errors"] == [error]
    assert error in audit["health"]["errors"]

    modeling_health = build_modeling_health(
        {"validation": {"valid": True, "errors": [], "warnings": []}, "view_audit": audit},
        execution_mode="preview",
    )
    assert modeling_health["verdict"] == "failed"
    assert modeling_health["checks"]["semiconductor_dopant_site_stale_count"] == 1
    assert modeling_health["checks"]["semiconductor_dopant_site_metadata_consistent"] is False
    assert any(
        "dopant-site metadata" in warning.lower() and "reconcile" in warning.lower()
        for warning in modeling_health["warnings"]
    )

    bundle = write_view_audit_bundle(tmp_path, stale, audit)
    rows = list(csv.DictReader(Path(bundle["files"]["semiconductor_dopant_sites_csv"]).open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["record_status"] == "actual_element_mismatch"
    assert rows[0]["actual_element"] == "Si"
    assert rows[0]["consistency_error"] == error


def test_bulk_beta_gallium_oxide_default_axis_layer_profile_is_informational() -> None:
    spec = load_example("beta_gallium_oxide_monoclinic_spec.json")
    audit = model_view_audit(spec)
    semiconductor = audit["health"]["semiconductor_health"]

    layer_profile = semiconductor["layer_profile_summary"]
    assert layer_profile["axis"] == "c"
    assert layer_profile["axis_source"] == "default_c_axis"
    assert layer_profile["min_interlayer_spacing_angstrom"] < 0.5
    assert layer_profile["spacing_warning_applicable"] is False
    assert layer_profile["spacing_warning"] is False
    assert layer_profile["spacing_warning_reason"] == "bulk_default_axis_projection_not_layered_model"

    band_path = semiconductor["band_path_summary"]
    assert band_path["available"] is False
    assert band_path["task_intent"] == "static_energy"
    assert band_path["task_relevant"] is False
    assert band_path["warning_count"] == 1

    health = build_modeling_health(
        {
            "ok": True,
            "validation": {"valid": True},
            "view_audit": audit,
        },
        execution_mode="preview",
    )
    warning_text = "\n".join(health["warnings"])
    assert "layer profile has unusually small interlayer spacing" not in warning_text
    assert "band-path preflight has warnings" not in warning_text
    assert health["checks"]["semiconductor_layer_profile_spacing_warning"] is False
    assert health["checks"]["semiconductor_band_path_warning_count"] == 1


def test_model_view_audit_reports_oxide_semiconductor_dopant_site_roles() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    sn_plan = infer_modeling_plan("Create Sn_Ga dopant for n-type behavior.", current_spec=beta_ga2o3)
    assert sn_plan.template_id == "crystal_sublattice_dopant"
    sn_doped, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=sn_plan.payload["operations"],
        ),
    )
    sn_health = model_view_audit(sn_doped)["health"]["semiconductor_health"]
    sn_site = sn_health["dopant_site_summary"]
    sn_charge = sn_health["charge_balance_summary"]

    assert sn_health["rule"] == "oxide_semiconductor_mixed_coordination"
    assert sn_site["context"] == {
        "tmd_context": False,
        "oxide_context": True,
        "oxide_cations": ["Ga"],
    }
    assert sn_site["site_family_counts"] == {"oxide_cation": 1}
    assert sn_site["carrier_type_hint"] == "donor_like_n_type"
    assert sn_site["latest"]["site_element"] == "Ga"
    assert sn_site["latest"]["dopant_element"] == "Sn"
    assert sn_site["latest"]["site_family"] == "oxide_cation"
    assert sn_site["latest"]["role_hint"] == "donor_like_n_type_on_oxide_cation_site"
    assert sn_charge["carrier_type_hint"] == "donor_like_n_type"
    assert sn_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert sn_charge["site_adjusted_dopant_delta_electrons"] == 1
    assert sn_health["carrier_intent_summary"]["latest_matches"] is True

    n_plan = infer_modeling_plan("Create N_O dopant for p-type behavior.", current_spec=beta_ga2o3)
    assert n_plan.template_id == "crystal_sublattice_dopant"
    n_doped, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=n_plan.payload["operations"],
        ),
    )
    n_health = model_view_audit(n_doped)["health"]["semiconductor_health"]
    n_site = n_health["dopant_site_summary"]
    n_charge = n_health["charge_balance_summary"]

    assert n_site["site_family_counts"] == {"oxide_anion": 1}
    assert n_site["carrier_type_hint"] == "acceptor_like_p_type"
    assert n_site["latest"]["site_element"] == "O"
    assert n_site["latest"]["dopant_element"] == "N"
    assert n_site["latest"]["site_family"] == "oxide_anion"
    assert n_site["latest"]["role_hint"] == "acceptor_like_p_type_on_oxide_anion_site"
    assert n_charge["carrier_type_hint"] == "acceptor_like_p_type"
    assert n_charge["site_adjusted_dopant_delta_electrons"] == -1
    assert n_health["carrier_intent_summary"]["latest_matches"] is True


def test_infer_modeling_plan_maps_chinese_oxide_semiconductor_dopant_phrases() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    sublattice_plan = infer_modeling_plan(
        "Sn\u63ba\u6742Ga\u4f4d\u70b9\u505an\u578b",
        current_spec=beta_ga2o3,
    )
    assert sublattice_plan.kind == "patch"
    assert sublattice_plan.template_id == "crystal_sublattice_dopant"
    sublattice_metadata = sublattice_plan.payload["operations"][1]["metadata_updates"]
    assert sublattice_metadata["last_semiconductor_dopant_site"]["site_element"] == "Ga"
    assert sublattice_metadata["last_semiconductor_dopant_site"]["dopant_element"] == "Sn"
    assert sublattice_plan.payload["operations"][-1]["metadata_updates"]["last_semiconductor_carrier_intent"] == {
        "carrier_type": "n_type",
        "dopant_element": "Sn",
        "source": "natural_language_semiconductor_carrier_type",
    }

    ga_site_plan = infer_modeling_plan(
        "\u5728Ga\u4f4d\u63baSn\u505an\u578b",
        current_spec=beta_ga2o3,
    )
    assert ga_site_plan.kind == "patch"
    assert ga_site_plan.template_id == "crystal_sublattice_dopant"
    assert ga_site_plan.payload["operations"][0] == {
        "type": "substitute_atom",
        "atom_id": "Ga1a",
        "new_element": "Sn",
    }

    n_type_plan = infer_modeling_plan(
        "\u628a\u5b83\u53d8\u6210n\u578b\u6c27\u5316\u9553",
        current_spec=beta_ga2o3,
    )
    assert n_type_plan.kind == "patch"
    assert n_type_plan.template_id == "semiconductor_carrier_type"
    n_type_metadata = n_type_plan.payload["operations"][-1]["metadata_updates"]["last_semiconductor_carrier_intent"]
    assert n_type_metadata["carrier_type"] == "n_type"
    assert n_type_metadata["dopant_element"] == "Sn"
    assert n_type_metadata["site_element"] == "Ga"
    assert n_type_metadata["mapping_rule"] == "oxide_semiconductor_cation_site"

    p_type_plan = infer_modeling_plan(
        "\u628a\u5b83\u53d8\u6210p\u578b\u6c27\u5316\u9553",
        current_spec=beta_ga2o3,
    )
    assert p_type_plan.kind == "patch"
    assert p_type_plan.template_id == "semiconductor_carrier_type"
    p_type_metadata = p_type_plan.payload["operations"][-1]["metadata_updates"]["last_semiconductor_carrier_intent"]
    assert p_type_metadata["carrier_type"] == "p_type"
    assert p_type_metadata["dopant_element"] == "N"
    assert p_type_metadata["site_element"] == "O"
    assert p_type_metadata["mapping_rule"] == "oxide_semiconductor_anion_site"


def test_infer_modeling_plan_recognizes_chinese_show_current_view_diagnostics() -> None:
    spec = load_example("silicon_diamond_spec.json")

    plan = infer_modeling_plan("把当前模型推到 Materials Studio，并导出当前视图参数。", current_spec=spec)

    assert plan.kind == "show_current"
    assert plan.template_id == "show_current_revision_with_diagnostics"
    assert plan.payload["project_id"] == spec.project_id
    assert plan.payload["revision"] == spec.revision
    assert plan.payload["export_diagnostics"] is True


def test_infer_modeling_plan_routes_current_model_normality_checks_to_inspect_current() -> None:
    spec = load_example("silicon_diamond_spec.json")

    plan = infer_modeling_plan("Check whether the current model is normal.", current_spec=spec)

    assert plan.kind == "inspect_current"
    assert plan.template_id == "inspect_current_revision"
    assert plan.payload["project_id"] == spec.project_id
    assert plan.payload["revision"] == spec.revision
    assert plan.payload["export_diagnostics"] is True


def test_infer_modeling_plan_routes_evidence_based_normality_checks_to_inspect_current() -> None:
    spec = load_example("silicon_diamond_spec.json")

    plan = infer_modeling_plan("Run an evidence-based normality check on the current model.", current_spec=spec)

    assert plan.kind == "inspect_current"
    assert plan.template_id == "inspect_current_revision"
    assert plan.payload["project_id"] == spec.project_id
    assert plan.payload["revision"] == spec.revision
    assert plan.payload["export_diagnostics"] is True


def test_model_view_audit_reports_oxide_semiconductor_vacancy_roles() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    oxygen_plan = infer_modeling_plan("Create O vacancy.", current_spec=beta_ga2o3)
    assert oxygen_plan.template_id == "crystal_auto_vacancy"
    oxygen_vacancy, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=oxygen_plan.payload["operations"],
        ),
    )
    oxygen_audit = model_view_audit(oxygen_vacancy)
    oxygen_defects = oxygen_audit["health"]["semiconductor_health"]["defect_summary"]
    oxygen_defect = oxygen_defects["defects"][0]

    assert oxygen_defects["context"] == {
        "tmd_context": False,
        "oxide_context": True,
        "oxide_cations": ["Ga"],
    }
    assert oxygen_defects["vacancy_count"] == 1
    assert oxygen_defects["carrier_type_hint"] == "donor_like_n_type"
    assert oxygen_defects["role_counts"] == {"donor_like_n_type_oxygen_vacancy": 1}
    assert oxygen_defects["site_family_counts"] == {"oxide_anion": 1}
    assert oxygen_defects["donor_like_count"] == 1
    assert oxygen_defect["site_element"] == "O"
    assert oxygen_defect["site_family"] == "oxide_anion"
    assert oxygen_defect["role_hint"] == "donor_like_n_type_oxygen_vacancy"
    assert oxygen_defect["carrier_type_hint"] == "donor_like_n_type"

    oxygen_health = build_modeling_health(
        {"ok": True, "validation": {"valid": True}, "view_audit": oxygen_audit},
        execution_mode="preview",
    )
    assert oxygen_health["checks"]["semiconductor_defect_carrier_type_hint"] == "donor_like_n_type"
    assert oxygen_health["checks"]["semiconductor_defect_donor_like_count"] == 1

    gallium_plan = infer_modeling_plan("Create Ga vacancy.", current_spec=beta_ga2o3)
    assert gallium_plan.template_id == "crystal_auto_vacancy"
    gallium_vacancy, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=gallium_plan.payload["operations"],
        ),
    )
    gallium_audit = model_view_audit(gallium_vacancy)
    gallium_defects = gallium_audit["health"]["semiconductor_health"]["defect_summary"]
    gallium_defect = gallium_defects["defects"][0]

    assert gallium_defects["carrier_type_hint"] == "acceptor_like_p_type"
    assert gallium_defects["role_counts"] == {"acceptor_like_p_type_oxide_cation_vacancy": 1}
    assert gallium_defects["site_family_counts"] == {"oxide_cation": 1}
    assert gallium_defects["acceptor_like_count"] == 1
    assert gallium_defect["site_element"] == "Ga"
    assert gallium_defect["site_family"] == "oxide_cation"
    assert gallium_defect["role_hint"] == "acceptor_like_p_type_oxide_cation_vacancy"
    assert gallium_defect["carrier_type_hint"] == "acceptor_like_p_type"


def test_model_view_audit_reports_halide_perovskite_vacancy_roles() -> None:
    mapbi3 = load_example("methylammonium_lead_iodide_mapbi3_perovskite_spec.json")

    iodine_plan = infer_modeling_plan("Create I vacancy.", current_spec=mapbi3)
    assert iodine_plan.template_id == "crystal_auto_vacancy"
    iodine_vacancy, _ = apply_semantic_patch(
        mapbi3,
        SemanticPatch(
            project_id=mapbi3.project_id,
            base_revision=mapbi3.revision,
            operations=iodine_plan.payload["operations"],
        ),
    )
    iodine_audit = model_view_audit(iodine_vacancy)
    iodine_defects = iodine_audit["health"]["semiconductor_health"]["defect_summary"]
    iodine_defect = iodine_defects["defects"][0]

    assert iodine_defects["context"] == {
        "tmd_context": False,
        "oxide_context": False,
        "oxide_cations": [],
        "halide_perovskite_context": True,
        "halide_perovskite_b_cations": ["Pb"],
        "halide_perovskite_halides": ["I"],
    }
    assert iodine_defects["vacancy_count"] == 1
    assert iodine_defects["carrier_type_hint"] == "donor_like_n_type"
    assert iodine_defects["role_counts"] == {"donor_like_n_type_halide_perovskite_halide_vacancy": 1}
    assert iodine_defects["site_family_counts"] == {"halide_perovskite_halide": 1}
    assert iodine_defects["donor_like_count"] == 1
    assert iodine_defect["site_element"] == "I"
    assert iodine_defect["site_family"] == "halide_perovskite_halide"
    assert iodine_defect["role_hint"] == "donor_like_n_type_halide_perovskite_halide_vacancy"
    assert iodine_defect["carrier_type_hint"] == "donor_like_n_type"

    iodine_health = build_modeling_health(
        {"ok": True, "validation": {"valid": True}, "view_audit": iodine_audit},
        execution_mode="preview",
    )
    assert iodine_health["checks"]["semiconductor_defect_carrier_type_hint"] == "donor_like_n_type"
    assert iodine_health["checks"]["semiconductor_defect_donor_like_count"] == 1

    lead_plan = infer_modeling_plan("Create Pb vacancy.", current_spec=mapbi3)
    assert lead_plan.template_id == "crystal_auto_vacancy"
    lead_vacancy, _ = apply_semantic_patch(
        mapbi3,
        SemanticPatch(
            project_id=mapbi3.project_id,
            base_revision=mapbi3.revision,
            operations=lead_plan.payload["operations"],
        ),
    )
    lead_audit = model_view_audit(lead_vacancy)
    lead_defects = lead_audit["health"]["semiconductor_health"]["defect_summary"]
    lead_defect = lead_defects["defects"][0]

    assert lead_defects["carrier_type_hint"] == "acceptor_like_p_type"
    assert lead_defects["role_counts"] == {"acceptor_like_p_type_halide_perovskite_b_site_vacancy": 1}
    assert lead_defects["site_family_counts"] == {"halide_perovskite_b_cation": 1}
    assert lead_defects["acceptor_like_count"] == 1
    assert lead_defects["context"]["halide_perovskite_b_cations"] == ["Pb"]
    assert lead_defect["site_element"] == "Pb"
    assert lead_defect["site_family"] == "halide_perovskite_b_cation"
    assert lead_defect["role_hint"] == "acceptor_like_p_type_halide_perovskite_b_site_vacancy"
    assert lead_defect["carrier_type_hint"] == "acceptor_like_p_type"


def test_model_view_audit_reports_halide_perovskite_dopant_site_roles() -> None:
    mapbi3 = load_example("methylammonium_lead_iodide_mapbi3_perovskite_spec.json")

    bromide_plan = infer_modeling_plan("Create Br_I dopant.", current_spec=mapbi3)
    assert bromide_plan.template_id == "crystal_sublattice_dopant"
    bromide_doped, _ = apply_semantic_patch(
        mapbi3,
        SemanticPatch(
            project_id=mapbi3.project_id,
            base_revision=mapbi3.revision,
            operations=bromide_plan.payload["operations"],
        ),
    )
    bromide_semiconductor = model_view_audit(bromide_doped)["health"]["semiconductor_health"]
    bromide_site = bromide_semiconductor["dopant_site_summary"]
    bromide_charge = bromide_semiconductor["charge_balance_summary"]

    assert bromide_semiconductor["rule"] == "doped_halide_perovskite_framework"
    assert bromide_semiconductor["expected_coordination_by_element"] == {"Br": 2, "I": 2, "Pb": 6}
    assert bromide_semiconductor["coordination_outlier_count"] == 0
    assert bromide_semiconductor["neighbor_distance_summary"]["unexpected_pair_types"] == []
    assert bromide_site["context"] == {
        "tmd_context": False,
        "oxide_context": False,
        "oxide_cations": [],
        "halide_perovskite_context": True,
        "halide_perovskite_b_cations": ["Pb"],
        "halide_perovskite_halides": ["Br", "I"],
    }
    assert bromide_site["carrier_type_hint"] == "neutral_or_intrinsic"
    assert bromide_site["role_counts"] == {"isovalent_halide_perovskite_halide_substitution": 1}
    assert bromide_site["site_family_counts"] == {"halide_perovskite_halide": 1}
    assert bromide_site["latest"]["site_element"] == "I"
    assert bromide_site["latest"]["dopant_element"] == "Br"
    assert bromide_site["latest"]["role_hint"] == "isovalent_halide_perovskite_halide_substitution"
    assert bromide_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert bromide_charge["site_adjusted_dopant_delta_electrons"] == 0

    antimony_plan = infer_modeling_plan("Create Sb_Pb dopant for n-type behavior.", current_spec=mapbi3)
    assert antimony_plan.template_id == "crystal_sublattice_dopant"
    antimony_doped, _ = apply_semantic_patch(
        mapbi3,
        SemanticPatch(
            project_id=mapbi3.project_id,
            base_revision=mapbi3.revision,
            operations=antimony_plan.payload["operations"],
        ),
    )
    antimony_audit = model_view_audit(antimony_doped)
    antimony_semiconductor = antimony_audit["health"]["semiconductor_health"]
    antimony_site = antimony_semiconductor["dopant_site_summary"]
    antimony_charge = antimony_semiconductor["charge_balance_summary"]

    assert antimony_semiconductor["rule"] == "doped_halide_perovskite_framework"
    assert antimony_semiconductor["expected_coordination_by_element"] == {"I": 2, "Pb": 6, "Sb": 6}
    assert antimony_semiconductor["coordination_outlier_count"] == 0
    assert antimony_semiconductor["neighbor_distance_summary"]["unexpected_pair_types"] == []
    assert antimony_site["carrier_type_hint"] == "donor_like_n_type"
    assert antimony_site["role_counts"] == {"donor_like_n_type_on_halide_perovskite_b_site": 1}
    assert antimony_site["site_family_counts"] == {"halide_perovskite_b_cation": 1}
    assert antimony_site["latest"]["site_element"] == "Pb"
    assert antimony_site["latest"]["dopant_element"] == "Sb"
    assert antimony_site["latest"]["site_family"] == "halide_perovskite_b_cation"
    assert antimony_site["latest"]["role_hint"] == "donor_like_n_type_on_halide_perovskite_b_site"
    assert antimony_charge["carrier_type_hint"] == "donor_like_n_type"
    assert antimony_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert antimony_charge["site_adjusted_dopant_delta_electrons"] == 1

    antimony_health = build_modeling_health(
        {"ok": True, "validation": {"valid": True}, "view_audit": antimony_audit},
        execution_mode="preview",
    )
    assert antimony_health["checks"]["semiconductor_dopant_site_carrier_type_hint"] == "donor_like_n_type"
    assert antimony_health["checks"]["semiconductor_dopant_site_donor_like_count"] == 1


def test_infer_modeling_plan_maps_halide_perovskite_formula_and_fraction_alloys() -> None:
    formula_plan = infer_modeling_plan("Build MAPb(I0.67Br0.33)3 alloy and export diagnostics.")
    assert formula_plan.kind == "spec"
    assert formula_plan.template_id == "crystal_formula_alloy"
    formula_spec = ModelSpec.model_validate(formula_plan.payload)
    assert formula_spec.metadata["formula_alloy_request"] == {
        "formula": "MAPb(I0.67Br0.33)3",
        "host_element": "I",
        "alloy_element": "Br",
        "requested_fraction": 0.33,
        "source": "natural_language_formula_alloy",
    }
    formula_audit = model_view_audit(formula_spec)
    formula_semiconductor = formula_audit["health"]["semiconductor_health"]
    assert formula_audit["model"]["elements"] == {"Br": 1, "C": 1, "H": 6, "I": 2, "N": 1, "Pb": 1}
    assert formula_semiconductor["rule"] == "alloyed_halide_perovskite_framework"
    assert formula_semiconductor["expected_coordination_by_element"] == {"Br": 2, "I": 2, "Pb": 6}
    assert formula_semiconductor["alloy_summary"]["latest"]["actual_fraction"] == 0.333333
    assert formula_semiconductor["dopant_summary"] is None
    assert formula_semiconductor["dopant_concentration_summary"] is None
    assert formula_semiconductor["dopant_site_summary"] is None
    assert formula_semiconductor["heterostructure_summary"] is None
    assert formula_semiconductor["interface_profile_summary"] is None
    assert formula_semiconductor["quantum_well_summary"] is None

    chinese_formula_plan = infer_modeling_plan(
        "\u5c06 MAPbI3 \u4e2d 33% \u7898\u66ff\u6362\u4e3a\u6eb4\u5e76\u5bfc\u51fa\u5408\u91d1\u8bca\u65ad."
    )
    assert chinese_formula_plan.kind == "spec"
    assert chinese_formula_plan.template_id == "crystal_formula_alloy"
    chinese_formula_spec = ModelSpec.model_validate(chinese_formula_plan.payload)
    assert chinese_formula_spec.metadata["formula_alloy_request"] == {
        "formula": "MAPb(I0.67Br0.33)3",
        "host_element": "I",
        "alloy_element": "Br",
        "requested_fraction": 0.33,
        "source": "natural_language_formula_alloy",
    }

    chloride_formula_plan = infer_modeling_plan(
        "\u5c06 MAPbI3 \u4e2d 33% \u7898\u66ff\u6362\u4e3a\u6c2f\u5316\u7269\u5e76\u5bfc\u51fa\u5408\u91d1\u8bca\u65ad."
    )
    assert chloride_formula_plan.kind == "spec"
    chloride_formula_spec = ModelSpec.model_validate(chloride_formula_plan.payload)
    assert chloride_formula_spec.metadata["formula_alloy_request"]["alloy_element"] == "Cl"
    assert chloride_formula_spec.metadata["formula_alloy_request"]["formula"] == "MAPb(I0.67Cl0.33)3"

    fluoride_formula_plan = infer_modeling_plan(
        "\u628a MAPbI3 \u4e2d 33% \u7898\u6362\u6210\u6c1f\u5316\u7269\u5e76\u5bfc\u51fa\u5408\u91d1\u8bca\u65ad."
    )
    assert fluoride_formula_plan.kind == "spec"
    fluoride_formula_spec = ModelSpec.model_validate(fluoride_formula_plan.payload)
    assert fluoride_formula_spec.metadata["formula_alloy_request"]["alloy_element"] == "F"
    assert fluoride_formula_spec.metadata["formula_alloy_request"]["formula"] == "MAPb(I0.67F0.33)3"

    mapbi3 = load_example("methylammonium_lead_iodide_mapbi3_perovskite_spec.json")
    fraction_plan = infer_modeling_plan("Replace 33% I with Br in MAPbI3.", current_spec=mapbi3)
    assert fraction_plan.kind == "patch"
    assert fraction_plan.template_id == "crystal_alloy_fraction"
    assert len(fraction_plan.payload["operations"]) == 2
    metadata_updates = fraction_plan.payload["operations"][-1]["metadata_updates"]
    assert "last_applied_alloy" in metadata_updates
    assert "last_applied_dopant_fraction" not in metadata_updates
    assert metadata_updates["last_applied_alloy"]["host_element"] == "I"
    assert metadata_updates["last_applied_alloy"]["alloy_element"] == "Br"
    assert metadata_updates["last_applied_alloy"]["requested_fraction"] == 0.33

    bromide_alloy, _ = apply_semantic_patch(
        mapbi3,
        SemanticPatch(
            project_id=mapbi3.project_id,
            base_revision=mapbi3.revision,
            operations=fraction_plan.payload["operations"],
        ),
    )
    alloy_semiconductor = model_view_audit(bromide_alloy)["health"]["semiconductor_health"]
    assert alloy_semiconductor["rule"] == "alloyed_halide_perovskite_framework"
    assert alloy_semiconductor["alloy_summary"]["latest"]["selected_atom_ids"] == ["I1"]
    assert alloy_semiconductor["alloy_summary"]["latest"]["actual_fraction"] == 0.333333
    assert alloy_semiconductor["dopant_fraction_summary"] is None
    assert alloy_semiconductor["dopant_summary"] is None
    assert alloy_semiconductor["dopant_concentration_summary"] is None
    assert alloy_semiconductor["heterostructure_summary"] is None
    assert alloy_semiconductor["quantum_well_summary"] is None

    chinese_fraction_plan = infer_modeling_plan(
        "\u628a MAPbI3 \u4e2d 33% I \u6362\u6210 Br \u5e76\u5bfc\u51fa\u5408\u91d1\u8bca\u65ad.",
        current_spec=mapbi3,
    )
    assert chinese_fraction_plan.kind == "patch"
    assert chinese_fraction_plan.template_id == "crystal_alloy_fraction"
    chinese_metadata_updates = chinese_fraction_plan.payload["operations"][-1]["metadata_updates"]
    assert chinese_metadata_updates["last_applied_alloy"]["host_element"] == "I"
    assert chinese_metadata_updates["last_applied_alloy"]["alloy_element"] == "Br"
    assert "last_applied_dopant_fraction" not in chinese_metadata_updates

    silicon = load_example("silicon_diamond_spec.json")
    dopant_plan = infer_modeling_plan("Replace 25% Si with P dopants for n-type behavior.", current_spec=silicon)
    assert dopant_plan.kind == "patch"
    assert dopant_plan.template_id == "crystal_dopant_fraction"
    dopant_metadata_updates = [
        operation["metadata_updates"]
        for operation in dopant_plan.payload["operations"]
        if operation["type"] == "set_metadata"
    ]
    assert any("last_applied_dopant_fraction" in item for item in dopant_metadata_updates)
    assert not any("last_applied_alloy" in item for item in dopant_metadata_updates)


def test_infer_modeling_plan_prioritizes_restore_substitution_over_show_current() -> None:
    silicon = load_example("silicon_diamond_spec.json")

    chinese_plan = infer_modeling_plan(
        "\u628a\u5f53\u524d\u6a21\u578b\u4e2d\u7684 Si1 \u4ece P \u6362\u56de Si\uff0c"
        "\u5e76\u70ed\u52a0\u8f7d\u5230\u5f53\u524d Materials Studio \u7a97\u53e3\uff0c"
        "\u5bfc\u51fa\u5168\u89c6\u89d2\u6a21\u578b\u53c2\u6570\u5e76\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38",
        current_spec=silicon,
    )
    english_plan = infer_modeling_plan(
        "Replace Si1 from P back to Si, hot-load the current model, and export all views.",
        current_spec=silicon,
    )

    for plan in (chinese_plan, english_plan):
        assert plan.kind == "patch"
        assert plan.template_id == "substitute_atom"
        assert plan.payload["operations"] == [
            {"type": "substitute_atom", "atom_id": "Si1", "new_element": "Si"}
        ]


def test_infer_modeling_plan_maps_vacancy_notation_without_v_dopant_side_effect() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    cases = [
        ("Create V_O defect.", "O", "O1a"),
        ("Create VO defect.", "O", "O1a"),
        ("Add V_O and export defect diagnostics.", "O", "O1a"),
        ("Create V_Ga defect.", "Ga", "Ga1a"),
        ("Create VGa defect.", "Ga", "Ga1a"),
        ("\u6784\u5efa V_O \u6c27\u7a7a\u4f4d", "O", "O1a"),
        ("\u521b\u5efa V_Ga \u9553\u7a7a\u4f4d", "Ga", "Ga1a"),
    ]
    for request, expected_element, expected_atom_id in cases:
        plan = infer_modeling_plan(request, current_spec=beta_ga2o3)

        assert plan.kind == "patch"
        assert plan.template_id == "crystal_auto_vacancy"
        operations = plan.payload["operations"]
        assert operations[0] == {"type": "delete_atom", "atom_id": expected_atom_id}
        metadata = operations[1]["metadata_updates"]
        assert "last_semiconductor_dopant_site" not in metadata
        assert "semiconductor_dopant_sites" not in metadata
        assert metadata["defects"][0]["site_element"] == expected_element
        assert metadata["defects"][0]["site_id"] == expected_atom_id
        assert metadata["nl_auto_selected_sites"][0]["operation"] == "vacancy"

    explicit_dopant = infer_modeling_plan("V_O dopant", current_spec=beta_ga2o3)
    assert explicit_dopant.template_id == "crystal_sublattice_dopant"
    assert explicit_dopant.payload["operations"][0] == {
        "type": "substitute_atom",
        "atom_id": "O1a",
        "new_element": "V",
    }


def test_vacancy_carrier_intent_uses_defect_hint_without_adding_dopant() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    donor_plan = infer_modeling_plan(
        "Create V_O donor defect for n-type behavior and export defect diagnostics.",
        current_spec=beta_ga2o3,
    )
    assert donor_plan.kind == "patch"
    assert donor_plan.template_id == "crystal_composite_edit"
    donor_operations = donor_plan.payload["operations"]
    assert donor_operations[0] == {"type": "delete_atom", "atom_id": "O1a"}
    assert all(operation.get("type") != "substitute_atom" for operation in donor_operations)
    donor, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=donor_operations,
        ),
    )
    donor_semiconductor = model_view_audit(donor)["health"]["semiconductor_health"]
    assert donor_semiconductor["dopant_summary"] is None
    donor_carrier = donor_semiconductor["carrier_intent_summary"]
    assert donor_carrier["requested_carrier_type"] == "n_type"
    assert donor_carrier["requested_carrier_mechanism"] == "defect"
    assert donor_carrier["requested_defect_type"] == "vacancy"
    assert donor_carrier["requested_site_element"] == "O"
    assert donor_carrier["actual_carrier_type"] == "n_type"
    assert donor_carrier["actual_carrier_type_hint"] == "donor_like_n_type"
    assert donor_carrier["actual_dopant_elements"] == []
    assert donor_carrier["actual_defect_count"] == 1
    assert donor_carrier["latest_matches"] is True

    acceptor_plan = infer_modeling_plan(
        "Create V_Ga acceptor defect for p-type behavior and export defect diagnostics.",
        current_spec=beta_ga2o3,
    )
    assert acceptor_plan.kind == "patch"
    assert acceptor_plan.template_id == "crystal_composite_edit"
    acceptor_operations = acceptor_plan.payload["operations"]
    assert acceptor_operations[0] == {"type": "delete_atom", "atom_id": "Ga1a"}
    assert all(operation.get("type") != "substitute_atom" for operation in acceptor_operations)
    acceptor, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=acceptor_operations,
        ),
    )
    acceptor_semiconductor = model_view_audit(acceptor)["health"]["semiconductor_health"]
    assert acceptor_semiconductor["dopant_summary"] is None
    acceptor_carrier = acceptor_semiconductor["carrier_intent_summary"]
    assert acceptor_carrier["requested_carrier_type"] == "p_type"
    assert acceptor_carrier["requested_carrier_mechanism"] == "defect"
    assert acceptor_carrier["requested_defect_type"] == "vacancy"
    assert acceptor_carrier["requested_site_element"] == "Ga"
    assert acceptor_carrier["actual_carrier_type"] == "p_type"
    assert acceptor_carrier["actual_carrier_type_hint"] == "acceptor_like_p_type"
    assert acceptor_carrier["latest_matches"] is True


def test_infer_modeling_plan_maps_chinese_defect_carrier_phrases() -> None:
    beta_ga2o3 = load_example("beta_gallium_oxide_monoclinic_spec.json")

    donor_plan = infer_modeling_plan(
        "\u521b\u5efa\u6c27\u7a7a\u4f4d\u65bd\u4e3b\u7f3a\u9677\u5e76\u5bfc\u51fa\u8bca\u65ad",
        current_spec=beta_ga2o3,
    )
    assert donor_plan.kind == "patch"
    assert donor_plan.template_id == "crystal_composite_edit"
    donor_operations = donor_plan.payload["operations"]
    assert donor_operations[0] == {"type": "delete_atom", "atom_id": "O1a"}
    assert all(operation.get("type") != "substitute_atom" for operation in donor_operations)
    donor_carrier_metadata = donor_operations[-1]["metadata_updates"]["last_semiconductor_carrier_intent"]
    assert donor_carrier_metadata == {
        "carrier_type": "n_type",
        "carrier_mechanism": "defect",
        "defect_type": "vacancy",
        "source": "natural_language_semiconductor_defect_carrier_type",
        "site_element": "O",
        "site_id": "O1a",
    }

    donor, _ = apply_semantic_patch(
        beta_ga2o3,
        SemanticPatch(
            project_id=beta_ga2o3.project_id,
            base_revision=beta_ga2o3.revision,
            operations=donor_operations,
        ),
    )
    donor_carrier = model_view_audit(donor)["health"]["semiconductor_health"]["carrier_intent_summary"]
    assert donor_carrier["requested_carrier_type"] == "n_type"
    assert donor_carrier["requested_carrier_mechanism"] == "defect"
    assert donor_carrier["actual_carrier_type"] == "n_type"
    assert donor_carrier["actual_carrier_type_hint"] == "donor_like_n_type"
    assert donor_carrier["latest_matches"] is True

    acceptor_plan = infer_modeling_plan(
        "\u521b\u5efa\u9553\u7a7a\u4f4d\u53d7\u4e3b\u7f3a\u9677\u5e76\u5bfc\u51fa\u8bca\u65ad",
        current_spec=beta_ga2o3,
    )
    assert acceptor_plan.kind == "patch"
    assert acceptor_plan.template_id == "crystal_composite_edit"
    acceptor_operations = acceptor_plan.payload["operations"]
    assert acceptor_operations[0] == {"type": "delete_atom", "atom_id": "Ga1a"}
    assert all(operation.get("type") != "substitute_atom" for operation in acceptor_operations)
    acceptor_carrier_metadata = acceptor_operations[-1]["metadata_updates"]["last_semiconductor_carrier_intent"]
    assert acceptor_carrier_metadata["carrier_type"] == "p_type"
    assert acceptor_carrier_metadata["carrier_mechanism"] == "defect"
    assert acceptor_carrier_metadata["site_element"] == "Ga"
    assert acceptor_carrier_metadata["site_id"] == "Ga1a"


def test_model_view_audit_for_benzene_exports_geometry_and_views(tmp_path: Path) -> None:
    spec = load_example("benzene_spec.json")
    audit = model_view_audit(spec)

    assert audit["model"]["atom_count"] == 12
    assert audit["model"]["bond_count"] == 12
    assert audit["model"]["elements"] == {"C": 6, "H": 6}
    assert len(audit["spec_fingerprint"]) == 16
    assert len(audit["atoms"]) == 12
    assert audit["atoms"][0]["id"]
    assert audit["geometry"]["bbox"]["min"][0] < 0
    assert audit["geometry"]["bbox"]["max"][0] > 0
    assert audit["health"]["ok"] is True
    c1 = next(item for item in audit["health"]["atom_connectivity"] if item["atom_id"] == "C1")
    assert c1["degree"] == 3
    assert c1["bond_order_sum"] == 4.0
    c1_angles = [item["angle_deg"] for item in audit["health"]["bond_angles_deg"] if item["center_atom"] == "C1"]
    assert len(c1_angles) == 3
    assert all(118.0 <= angle <= 122.0 for angle in c1_angles)
    assert audit["health"]["bond_angle_stats_deg"]["min"] >= 118.0
    assert len(audit["health"]["dihedral_angles_deg"]) == 24
    assert all(
        min(abs(item["angle_deg"]), abs(abs(item["angle_deg"]) - 180.0)) <= 1.0
        for item in audit["health"]["dihedral_angles_deg"]
    )
    assert audit["health"]["nonbonded_close_contacts"] == []
    assert [view["name"] for view in audit["views"]] == ["front", "back", "right", "left", "top", "bottom", "isometric"]
    front = audit["views"][0]
    assert front["projection_bbox_angstrom"]["x"][0] < 0
    assert front["camera_distance_angstrom"] >= 10.0
    assert len(front["camera_position"]) == 3
    assert front["look_at_direction"][2] == -1.0
    assert front["framing"]["orthographic_width_angstrom"] > 1.0
    assert front["framing"]["far_clip_angstrom"] > front["framing"]["near_clip_angstrom"]
    assert front["atom_projection_count"] == 12
    assert len(front["atom_projections"]) == 12
    assert {"atom_id", "element", "x", "y", "depth"} <= set(front["atom_projections"][0])
    assert front["health"]["ok"] is True

    report_path = write_view_audit_report(tmp_path, spec, audit, gui_status={"window_found": False})
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["project_id"] == spec.project_id
    assert payload["gui_status"]["window_found"] is False


def test_crystallographic_views_follow_nonorthogonal_lattice_vectors(tmp_path: Path) -> None:
    spec = load_example("silicon_carbide_4h_hexagonal_spec.json")
    audit = model_view_audit(
        spec,
        ["crystal_100", "crystal_010", "crystal_0001", "crystal_111"],
    )
    views = {view["name"]: view for view in audit["views"]}

    assert views["crystal_100"]["camera_direction"] == [1.0, 0.0, 0.0]
    assert views["crystal_010"]["camera_direction"] == [-0.5, 0.866025, 0.0]
    assert views["crystal_0001"]["camera_direction"] == [0.0, 0.0, 1.0]
    assert views["crystal_0001"]["crystal_direction_indices"] == [0, 0, 0, 1]
    assert views["crystal_0001"]["crystal_direction_label"] == "[0001]"
    assert views["crystal_111"]["coordinate_system"] == "crystal_lattice_direction"
    assert views["crystal_100"]["crystal_direction_view_onto_plane_mapping"] == {
        "status": "exact_integer_plane_collinear",
        "automation_eligible": True,
        "miller_plane_indices": [2, -1, 0],
        "miller_plane_label": "(2-10)",
        "plane_normal_cartesian": [1.0, 0.0, 0.0],
        "direction_plane_dot_product": 1.0,
        "angular_error_degrees": 0.0,
        "closest_candidate_indices": [2, -1, 0],
        "search_max_abs_index": 12,
        "collinearity_sine_tolerance": 1e-09,
        "relation": "direct_lattice_direction_collinear_with_reciprocal_plane_normal",
    }
    assert views["crystal_010"]["crystal_direction_view_onto_plane_mapping"][
        "miller_plane_indices"
    ] == [-1, 2, 0]
    assert views["crystal_0001"]["crystal_direction_view_onto_plane_mapping"][
        "miller_plane_indices"
    ] == [0, 0, 1]
    assert views["crystal_111"]["crystal_direction_view_onto_plane_mapping"][
        "automation_eligible"
    ] is False
    assert all(view["supported"] is True for view in views.values())
    assert all(view["atom_projection_count"] == 8 for view in views.values())

    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    rows = list(csv.DictReader(Path(bundle["files"]["view_summary_csv"]).open(encoding="utf-8")))
    row_by_view = {row["view"]: row for row in rows}
    assert row_by_view["crystal_010"]["coordinate_system"] == "crystal_lattice_direction"
    assert row_by_view["crystal_010"]["crystal_direction_indices"] == "0;1;0"
    assert row_by_view["crystal_0001"]["crystal_direction_label"] == "[0001]"
    assert row_by_view["crystal_0001"]["crystal_direction_cartesian"] == "0.0;0.0;1.0"
    csv_mapping = json.loads(
        row_by_view["crystal_100"]["crystal_direction_view_onto_plane_mapping"]
    )
    assert csv_mapping["status"] == "exact_integer_plane_collinear"
    assert csv_mapping["miller_plane_indices"] == [2, -1, 0]
    assert bundle["row_counts"]["view_summary"] == 4
    assert bundle["row_counts"]["view_projections"] == 32


def test_crystallographic_views_require_crystal_lattice() -> None:
    spec = load_example("benzene_spec.json")
    view = model_view_audit(spec, ["crystal_001"])["views"][0]

    assert view["supported"] is False
    assert "require a crystal model" in view["warning"]


def test_crystal_plane_views_use_reciprocal_lattice_normals_and_export_spacing(tmp_path: Path) -> None:
    spec = load_example("silicon_carbide_4h_hexagonal_spec.json")
    audit = model_view_audit(
        spec,
        [
            "crystal_100",
            "crystal_plane_100",
            "crystal_plane_0001",
            "crystal_plane_10m10",
            "crystal_plane_11m20",
        ],
    )
    views = {view["name"]: view for view in audit["views"]}

    assert views["crystal_100"]["camera_direction"] == [1.0, 0.0, 0.0]
    assert views["crystal_plane_100"]["camera_direction"] == [0.866025, 0.5, 0.0]
    assert views["crystal_plane_100"]["coordinate_system"] == "crystal_reciprocal_plane_normal"
    assert views["crystal_plane_100"]["crystal_plane_indices"] == [1, 0, 0]
    assert views["crystal_plane_100"]["crystal_plane_label"] == "(100)"
    assert views["crystal_plane_100"]["crystal_plane_reciprocal_convention"] == "dual_basis_without_2pi"
    assert views["crystal_plane_0001"]["crystal_plane_normal_cartesian"] == [0.0, 0.0, 1.0]
    assert views["crystal_plane_0001"]["crystal_plane_spacing_angstrom"] == pytest.approx(
        spec.model.lattice.c,
        abs=1e-6,
    )
    assert views["crystal_plane_10m10"]["crystal_plane_label"] == "(10-10)"
    assert views["crystal_plane_10m10"]["camera_direction"] == [0.866025, 0.5, 0.0]
    assert views["crystal_plane_11m20"]["crystal_plane_label"] == "(11-20)"
    assert views["crystal_plane_11m20"]["crystal_plane_spacing_angstrom"] == pytest.approx(
        spec.model.lattice.a / 2.0,
        abs=1e-6,
    )

    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    rows = list(csv.DictReader(Path(bundle["files"]["view_summary_csv"]).open(encoding="utf-8")))
    row_by_view = {row["view"]: row for row in rows}
    plane_row = row_by_view["crystal_plane_100"]
    assert plane_row["crystal_plane_indices"] == "1;0;0"
    assert plane_row["crystal_plane_label"] == "(100)"
    assert plane_row["crystal_plane_normal_cartesian"] == "0.866025;0.5;0.0"
    assert plane_row["crystal_plane_reciprocal_convention"] == "dual_basis_without_2pi"
    assert float(plane_row["crystal_plane_spacing_angstrom"]) > 0.0


def test_monoclinic_plane_normal_is_not_same_as_matching_lattice_direction() -> None:
    spec = load_example("beta_gallium_oxide_monoclinic_spec.json")
    audit = model_view_audit(spec, ["crystal_001", "crystal_plane_001"])
    direction_view, plane_view = audit["views"]

    assert direction_view["camera_direction"] != plane_view["camera_direction"]
    assert direction_view["crystal_direction_view_onto_plane_mapping"][
        "automation_eligible"
    ] is False
    assert direction_view["crystal_direction_view_onto_plane_mapping"]["status"] == (
        "no_exact_integer_plane_within_search_bound"
    )
    assert abs(direction_view["camera_direction"][0]) > 0.1
    assert plane_view["camera_direction"] == [0.0, 0.0, 1.0]
    expected_spacing = spec.model.lattice.c * math.sin(math.radians(spec.model.lattice.beta))
    assert plane_view["crystal_plane_spacing_angstrom"] == pytest.approx(expected_spacing, abs=1e-6)


def test_crystal_plane_views_require_crystal_lattice() -> None:
    spec = load_example("benzene_spec.json")
    view = model_view_audit(spec, ["crystal_plane_100"])["views"][0]

    assert view["supported"] is False
    assert "require a crystal model" in view["warning"]


def test_model_view_audit_keeps_generic_defaults_for_non_semiconductor_models() -> None:
    audit = model_view_audit(load_example("benzene_spec.json"))

    assert [view["name"] for view in audit["views"]] == [
        "front",
        "back",
        "right",
        "left",
        "top",
        "bottom",
        "isometric",
    ]
    assert audit["view_selection"] == {
        "policy_version": 1,
        "source": "generic_default",
        "policy_applied": True,
        "explicit_views_provided": False,
        "model_type": "molecule",
        "domain": None,
        "semiconductor_domain": False,
        "selection_profile": "generic_default",
        "lattice_family": None,
        "orientation_kind": None,
        "orientation_axis": None,
        "cartesian_context_views": [
            "front",
            "back",
            "right",
            "left",
            "top",
            "bottom",
            "isometric",
        ],
        "domain_diagnostic_views": [],
        "view_names": [
            "front",
            "back",
            "right",
            "left",
            "top",
            "bottom",
            "isometric",
        ],
        "view_count": 7,
        "reason_codes": ["non_crystal_generic_default"],
        "explicit_views_override_domain_defaults": True,
    }


@pytest.mark.parametrize(
    (
        "example_name",
        "expected_profile",
        "expected_lattice_family",
        "expected_orientation_kind",
        "expected_orientation_axis",
        "expected_domain_views",
    ),
    [
        (
            "silicon_diamond_spec.json",
            "semiconductor_bulk_cubic",
            "cubic",
            None,
            None,
            ["crystal_plane_100", "crystal_plane_110", "crystal_plane_111"],
        ),
        (
            "gallium_nitride_wurtzite_spec.json",
            "semiconductor_bulk_hexagonal",
            "hexagonal",
            None,
            None,
            [
                "crystal_plane_0001",
                "crystal_plane_10m10",
                "crystal_plane_11m20",
            ],
        ),
        (
            "molybdenum_disulfide_2d_mos2_monolayer_spec.json",
            "semiconductor_surface_frame",
            "hexagonal",
            "surface",
            "c",
            ["surface_normal", "surface_in_plane_1", "surface_in_plane_2"],
        ),
        (
            "silicon_silicon_dioxide_100_interface_spec.json",
            "semiconductor_interface_frame",
            "tetragonal",
            "interface",
            "c",
            ["interface_normal", "interface_in_plane_1", "interface_in_plane_2"],
        ),
        (
            "beta_gallium_oxide_010_slab_spec.json",
            "semiconductor_surface_frame",
            "monoclinic",
            "surface",
            "b",
            ["surface_normal", "surface_in_plane_1", "surface_in_plane_2"],
        ),
    ],
)
def test_model_view_audit_selects_semiconductor_domain_default_views(
    example_name: str,
    expected_profile: str,
    expected_lattice_family: str,
    expected_orientation_kind: str | None,
    expected_orientation_axis: str | None,
    expected_domain_views: list[str],
) -> None:
    audit = model_view_audit(load_example(example_name))
    selection = audit["view_selection"]
    expected_views = ["front", "top", "isometric", *expected_domain_views]

    assert [view["name"] for view in audit["views"]] == expected_views
    assert selection["source"] == "semiconductor_domain_default"
    assert selection["policy_applied"] is True
    assert selection["semiconductor_domain"] is True
    assert selection["selection_profile"] == expected_profile
    assert selection["lattice_family"] == expected_lattice_family
    assert selection["orientation_kind"] == expected_orientation_kind
    assert selection["orientation_axis"] == expected_orientation_axis
    assert selection["cartesian_context_views"] == ["front", "top", "isometric"]
    assert selection["domain_diagnostic_views"] == expected_domain_views
    assert selection["view_names"] == expected_views
    assert selection["view_count"] == 6


def test_explicit_views_override_semiconductor_domain_defaults_without_expansion() -> None:
    requested = ["front", "crystal_plane_001"]
    audit = model_view_audit(load_example("silicon_diamond_spec.json"), requested)
    selection = audit["view_selection"]

    assert [view["name"] for view in audit["views"]] == requested
    assert selection["source"] == "explicit_request"
    assert selection["policy_applied"] is False
    assert selection["explicit_views_provided"] is True
    assert selection["selection_profile"] == "explicit_request"
    assert selection["suggested_default_profile"] == "semiconductor_bulk_cubic"
    assert selection["suggested_default_view_names"] == [
        "front",
        "top",
        "isometric",
        "crystal_plane_100",
        "crystal_plane_110",
        "crystal_plane_111",
    ]
    assert selection["view_names"] == requested
    assert selection["reason_codes"] == ["explicit_views_preserved"]


def test_plane_view_diagnostics_do_not_select_surface_templates() -> None:
    bulk_requests = {
        (
            "Build beta-Ga2O3 crystal and export view parameters normal to the "
            "(001) and (010) crystal planes."
        ): "beta_gallium_oxide_monoclinic",
        "Build GaAs crystal and export a view normal to the (001) crystal plane.": (
            "gallium_arsenide_zincblende"
        ),
        "Build GaN crystal and export the (0001) plane-normal view.": "gallium_nitride_wurtzite",
        "\u6784\u5efa\u03b2-\u6c27\u5316\u9553\u6676\u4f53\u5e76\u6cbf(010)\u6676\u9762\u6cd5\u5411\u5bfc\u51fa\u89c6\u56fe\u53c2\u6570": (
            "beta_gallium_oxide_monoclinic"
        ),
    }
    for request, expected_template in bulk_requests.items():
        plan = infer_modeling_plan(request)
        assert plan.kind == "spec"
        assert plan.template_id == expected_template

    explicit_surface_requests = {
        "Build beta-Ga2O3(010) surface slab.": "beta_gallium_oxide_010_slab",
        "Build GaAs(001) surface slab.": "gallium_arsenide_001_slab",
        "\u6784\u5efa\u03b2-\u6c27\u5316\u9553(010)\u8868\u9762 slab": "beta_gallium_oxide_010_slab",
    }
    for request, expected_template in explicit_surface_requests.items():
        plan = infer_modeling_plan(request)
        assert plan.kind == "spec"
        assert plan.template_id == expected_template


def test_surface_orientation_summary_validates_parent_mapping_and_current_cell_alignment(
    tmp_path: Path,
) -> None:
    parent_cases = {
        "silicon_100_slab_spec.json": ("(100)", [1, 0, 0], "c"),
        "gallium_arsenide_001_slab_spec.json": ("(001)", [0, 0, 1], "c"),
        "gallium_nitride_0001_slab_spec.json": ("(0001)", [0, 0, 0, 1], "c"),
        "beta_gallium_oxide_010_slab_spec.json": ("(010)", [0, 1, 0], "b"),
    }
    for example, (label, indices, axis) in parent_cases.items():
        audit = model_view_audit(load_example(example), ["front"])
        summary = audit["health"]["semiconductor_health"]["surface_orientation_summary"]
        assert summary["status"] == "parent_plane_mapped_to_surface_axis"
        assert summary["surface_plane_label"] == label
        assert summary["surface_plane_indices"] == indices
        assert summary["surface_axis"] == axis
        assert summary["mapping_axis_matches_surface_axis"] is True
        assert summary["alignment_applicable"] is False
        assert summary["blocking"] is False

    c_face_plan = infer_modeling_plan("Build a 6H-SiC(000-1) C-face slab.")
    assert c_face_plan.payload is not None
    c_face_audit = model_view_audit(ModelSpec.model_validate(c_face_plan.payload), ["front"])
    c_face_summary = c_face_audit["health"]["semiconductor_health"]["surface_orientation_summary"]
    assert c_face_summary["status"] == "parent_plane_mapped_to_surface_axis"
    assert c_face_summary["surface_plane_label"] == "(000-1)"
    assert c_face_summary["surface_plane_indices"] == [0, 0, 0, -1]
    assert c_face_summary["blocking"] is False

    base = load_example("gallium_arsenide_001_slab_spec.json")
    aligned = base.model_copy(
        update={
            "metadata": {
                **base.metadata,
                "surface_orientation": "(001)",
                "surface_orientation_basis": "current_cell",
            }
        }
    )
    aligned_audit = model_view_audit(aligned, ["front"])
    aligned_summary = aligned_audit["health"]["semiconductor_health"]["surface_orientation_summary"]
    assert aligned_summary["status"] == "current_cell_plane_aligned"
    assert aligned_summary["axis_plane_alignment_angle_degrees"] == 0.0
    assert aligned_summary["axis_plane_alignment_ok"] is True
    assert aligned_summary["plane_normal_cartesian"] == [0.0, 0.0, 1.0]
    assert aligned_summary["plane_spacing_angstrom"] == 25.0

    mismatched = base.model_copy(
        update={
            "metadata": {
                **base.metadata,
                "surface_orientation": "(100)",
                "surface_orientation_basis": "current_cell",
            }
        }
    )
    mismatch_audit = model_view_audit(mismatched, ["front"])
    mismatch_semiconductor = mismatch_audit["health"]["semiconductor_health"]
    mismatch_summary = mismatch_semiconductor["surface_orientation_summary"]
    assert mismatch_summary["status"] == "current_cell_plane_axis_mismatch"
    assert mismatch_summary["axis_plane_alignment_angle_degrees"] == 90.0
    assert mismatch_summary["axis_plane_alignment_ok"] is False
    assert mismatch_summary["blocking"] is True
    assert mismatch_semiconductor["surface_model_summary"]["status"] == "blocked"
    assert "surface_orientation:current_cell_plane_axis_mismatch" in mismatch_semiconductor[
        "surface_model_summary"
    ]["blocking_reasons"]
    assert mismatch_audit["health"]["ok"] is False
    assert any("Surface orientation metadata" in error for error in mismatch_audit["health"]["errors"])

    bundle = write_view_audit_bundle(tmp_path, mismatched, mismatch_audit)
    rows = list(
        csv.DictReader(Path(bundle["files"]["semiconductor_surface_model_csv"]).open(encoding="utf-8"))
    )
    assert len(rows) == 1
    assert rows[0]["surface_orientation_status"] == "current_cell_plane_axis_mismatch"
    assert rows[0]["surface_orientation_basis"] == "current_cell"
    assert rows[0]["surface_plane_label"] == "(100)"
    assert rows[0]["surface_axis_cartesian"] == "0.0;0.0;1.0"
    assert rows[0]["plane_normal_cartesian"] == "1.0;0.0;0.0"
    assert rows[0]["axis_plane_alignment_angle_degrees"] == "90.0"
    assert rows[0]["axis_plane_alignment_ok"] == "False"


def test_surface_and_interface_oriented_frame_views_are_orthonormal_and_exported(tmp_path: Path) -> None:
    surface_spec = load_example("beta_gallium_oxide_010_slab_spec.json")
    surface_views = ["surface_normal", "surface_in_plane_1", "surface_in_plane_2"]
    surface_audit = model_view_audit(surface_spec, surface_views)
    assert [view["name"] for view in surface_audit["views"]] == surface_views
    assert all(view["supported"] is True for view in surface_audit["views"])
    by_name = {view["name"]: view for view in surface_audit["views"]}
    assert by_name["surface_normal"]["camera_direction"] == [0.0, 1.0, 0.0]
    assert by_name["surface_in_plane_1"]["camera_direction"] == [1.0, 0.0, 0.0]
    assert by_name["surface_in_plane_2"]["camera_direction"] == [0.0, 0.0, 1.0]
    directions = [tuple(view["camera_direction"]) for view in surface_audit["views"]]
    assert all(sum(value * value for value in direction) == pytest.approx(1.0) for direction in directions)
    assert all(
        sum(left[index] * right[index] for index in range(3)) == pytest.approx(0.0, abs=1e-6)
        for left_index, left in enumerate(directions)
        for right in directions[left_index + 1 :]
    )
    for view, role in zip(surface_audit["views"], ("normal", "in_plane_1", "in_plane_2")):
        assert view["coordinate_system"] == "surface_cell_frame"
        assert view["oriented_frame_kind"] == "surface"
        assert view["oriented_frame_role"] == role
        assert view["oriented_frame_axis"] == "b"
        assert view["oriented_frame_source_metadata_field"] == "surface_axis"
        assert view["atom_projection_count"] == 40

    bundle = write_view_audit_bundle(tmp_path / "surface", surface_spec, surface_audit)
    rows = list(csv.DictReader(Path(bundle["files"]["view_summary_csv"]).open(encoding="utf-8")))
    assert [row["view"] for row in rows] == surface_views
    assert rows[0]["oriented_frame_kind"] == "surface"
    assert rows[0]["oriented_frame_role"] == "normal"
    assert rows[0]["oriented_frame_axis"] == "b"
    assert rows[0]["oriented_frame_axis_cartesian"] == "0.0;1.0;0.0"
    assert bundle["row_counts"]["view_summary"] == 3
    assert bundle["row_counts"]["view_projections"] == 120

    interface_spec = load_example("silicon_silicon_dioxide_100_interface_spec.json")
    interface_views = ["interface_normal", "interface_in_plane_1", "interface_in_plane_2"]
    interface_audit = model_view_audit(interface_spec, interface_views)
    assert [view["camera_direction"] for view in interface_audit["views"]] == [
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert all(view["coordinate_system"] == "interface_cell_frame" for view in interface_audit["views"])
    assert all(view["oriented_frame_axis"] == "c" for view in interface_audit["views"])
    assert all(
        view["oriented_frame_source_metadata_field"] == "interface_axis"
        for view in interface_audit["views"]
    )

    bulk = load_example("silicon_carbide_4h_hexagonal_spec.json")
    unsupported = model_view_audit(bulk, ["surface_normal", "interface_normal"])["views"]
    assert all(view["supported"] is False for view in unsupported)
    assert "metadata.surface_axis" in unsupported[0]["warning"]
    assert "metadata.interface_axis" in unsupported[1]["warning"]

    invalid_axis = surface_spec.model_copy(
        update={"metadata": {**surface_spec.metadata, "surface_axis": "q"}}
    )
    invalid_view = model_view_audit(invalid_axis, ["surface_normal"])["views"][0]
    assert invalid_view["supported"] is False
    assert "metadata.surface_axis set to a, b, or c" in invalid_view["warning"]


def test_write_view_audit_bundle_exports_csv_tables(tmp_path: Path) -> None:
    spec = load_example("benzene_spec.json")
    audit = model_view_audit(spec)

    bundle = write_view_audit_bundle(
        tmp_path,
        spec,
        audit,
        gui_status={"window_found": False},
        modeling_health={"verdict": "ready_for_review", "ok": True},
    )

    manifest_path = Path(bundle["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["project_id"] == spec.project_id
    assert Path(bundle["files"]["view_audit_json"]).exists()
    assert Path(bundle["files"]["atoms_csv"]).exists()
    assert Path(bundle["files"]["bonds_csv"]).exists()
    assert Path(bundle["files"]["bond_angles_csv"]).exists()
    assert Path(bundle["files"]["dihedrals_csv"]).exists()
    assert Path(bundle["files"]["view_summary_csv"]).exists()
    assert Path(bundle["files"]["view_projections_csv"]).exists()
    assert Path(bundle["files"]["view_quality_csv"]).exists()
    assert Path(bundle["files"]["modeling_health_summary_csv"]).exists()
    assert bundle["row_counts"]["atoms"] == 12
    assert bundle["row_counts"]["bonds"] == 12
    assert bundle["row_counts"]["bond_angles"] == 18
    assert bundle["row_counts"]["dihedrals"] == 24
    assert bundle["row_counts"]["modeling_health_summary"] == 1
    assert bundle["row_counts"]["view_summary"] == 7
    assert bundle["row_counts"]["view_projections"] == 84
    assert bundle["row_counts"]["view_quality"] == 7
    assert "atom_id,element,x_angstrom" in Path(bundle["files"]["atoms_csv"]).read_text(encoding="utf-8")
    assert "atom1,center_atom,atom3,angle_deg" in Path(bundle["files"]["bond_angles_csv"]).read_text(encoding="utf-8")
    assert "atom1,atom2,atom3,atom4,angle_deg" in Path(bundle["files"]["dihedrals_csv"]).read_text(encoding="utf-8")
    assert "camera_position,look_at_direction" in Path(bundle["files"]["view_summary_csv"]).read_text(encoding="utf-8")
    assert "clean_for_visual_review,nonblocking_visual_note,calculation_risk,recommendation" in Path(bundle["files"]["view_quality_csv"]).read_text(encoding="utf-8")
    health_csv = Path(bundle["files"]["modeling_health_summary_csv"]).read_text(encoding="utf-8")
    assert "project_id,revision,model_type,spec_fingerprint,verdict,ok,execution_mode" in health_csv
    assert spec.project_id in health_csv
    assert ",0,molecule," in health_csv
    assert "ready_for_review,True" in health_csv
    assert "view,atom_id,element,x,y,depth" in Path(bundle["files"]["view_projections_csv"]).read_text(encoding="utf-8")


def test_view_audit_bundle_marks_clean_semiconductor_projection_notes(tmp_path: Path) -> None:
    spec = load_example("gallium_arsenide_zincblende_spec.json")
    audit = model_view_audit(spec)

    bundle = write_view_audit_bundle(
        tmp_path,
        spec,
        audit,
        modeling_health=build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview"),
    )

    rows = list(csv.DictReader(Path(bundle["files"]["view_quality_csv"]).open(encoding="utf-8")))
    assert bundle["row_counts"]["view_quality"] == 6
    assert any(row["clean_for_visual_review"] == "True" and row["recommended_rank"] for row in rows)
    isometric = next(row for row in rows if row["view"] == "isometric")
    assert isometric["overlap_candidate_count"] == "4"
    assert isometric["nonblocking_visual_note"] == "True"
    assert isometric["calculation_risk"] == "False"
    assert isometric["recommendation"] == "projection_overlap_visual_note_use_clean_view_for_review"


def test_model_view_audit_reports_crystal_neighbors_and_coordination() -> None:
    spec = load_example("silicon_diamond_spec.json")
    audit = model_view_audit(spec)
    health = audit["health"]

    assert health["ok"] is True
    assert health["crystal_distance_mode"] == "periodic_minimum_image_3x3"
    assert abs(health["crystal_nearest_neighbor_stats_angstrom"]["min"] - 2.351692) < 1e-6
    assert health["crystal_nearest_neighbor_stats_angstrom"]["count"] == 8
    assert health["crystal_coordination_stats"]["min"] == 4.0
    assert health["crystal_coordination_stats"]["max"] == 4.0
    si1 = next(item for item in health["crystal_coordination"] if item["atom_id"] == "Si1")
    assert si1["neighbor_count"] == 4
    assert si1["neighbor_ids"] == ["Si2", "Si4", "Si6", "Si8"]
    assert health["semiconductor_health"]["rule"] == "group_iv_tetrahedral"
    assert health["semiconductor_health"]["ok"] is True
    assert health["semiconductor_health"]["coordination_by_element"]["Si"]["min"] == 4.0
    local_environment = health["semiconductor_health"]["local_environment_summary"]
    assert local_environment["atom_count"] == 8
    assert local_environment["coordination_outlier_count"] == 0
    assert local_environment["angle_stats_deg"]["mean"] == 109.471221
    assert local_environment["tetrahedral_angle_deviation_stats_deg"]["max"] == 0.0
    si1_environment = next(item for item in local_environment["local_environments"] if item["atom_id"] == "Si1")
    assert si1_environment["neighbor_count"] == 4
    assert si1_environment["angle_stats_deg"]["count"] == 6
    assert si1_environment["max_tetrahedral_angle_deviation_deg"] == 0.0
    charge_balance = health["semiconductor_health"]["charge_balance_summary"]
    assert charge_balance["model"] == "nominal_valence_electron_heuristic"
    assert charge_balance["total_valence_electron_count"] == 32
    assert charge_balance["electron_count_parity"] == "even"
    assert charge_balance["valence_electrons_per_non_passivant_atom"] == 4.0
    calculation = health["semiconductor_health"]["calculation_preflight_summary"]
    assert calculation["module"] == "CASTEP"
    assert calculation["cutoff_energy_ev"] == 520
    assert calculation["task_family"] == "energy"
    assert calculation["task_intent"] == "static_energy"
    assert calculation["kpoint_mode"] == "separation"
    assert calculation["kpoint_separation"] == 0.04
    assert calculation["ready_for_energy_preflight"] is True
    assert calculation["ready_for_requested_task_preflight"] is True
    assert calculation["next_action"] == "ready_for_static_energy_preview_or_explicit_execute"
    assert calculation["warning_count"] == 0
    reciprocal = health["semiconductor_health"]["reciprocal_lattice_summary"]
    assert reciprocal["status"] == "ok"
    assert reciprocal["reciprocal_lengths_1_per_angstrom"] == [1.156911, 1.156911, 1.156911]
    assert reciprocal["estimated_kpoints_from_separation"] == [29, 29, 29]
    assert reciprocal["actual_separations_1_per_angstrom"] == [0.039893, 0.039893, 0.039893]
    assert reciprocal["axes"][0]["axis"] == "a"
    assert reciprocal["axes"][0]["estimated_kpoint_from_separation"] == 29
    band_path = health["semiconductor_health"]["band_path_summary"]
    assert band_path["available"] is True
    assert band_path["bravais_lattice"] == "fcc"
    assert band_path["path_label"] == "Gamma-X-W-K-Gamma-L-U-W-L-K"
    assert band_path["point_count"] == 10
    assert band_path["segment_count"] == 9
    assert band_path["high_symmetry_points"][0] == {"label": "Gamma", "fractional": [0.0, 0.0, 0.0]}
    assert band_path["task_relevant"] is False


def test_model_view_audit_reports_3c_silicon_carbide_semiconductor_health() -> None:
    sic = model_view_audit(load_example("silicon_carbide_3c_zincblende_spec.json"))["health"]["semiconductor_health"]

    assert sic["ok"] is True
    assert sic["rule"] == "group_iv_tetrahedral"
    assert sic["structure_family"] == "zinc blende"
    assert sic["elements"] == ["C", "Si"]
    assert sic["composition_summary"]["formula"] == "C4Si4"
    assert sic["composition_summary"]["reduced_formula"] == "CSi"
    assert sic["neighbor_pair_counts"] == {"C-Si": 16}
    assert sic["unexpected_neighbor_pair_count"] == 0
    assert sic["coordination_by_element"]["C"]["min"] == 4.0
    assert sic["coordination_by_element"]["Si"]["max"] == 4.0
    neighbors = sic["neighbor_distance_summary"]
    assert neighbors["distance_stats_angstrom"]["min"] == 1.887762
    assert neighbors["pair_types"][0]["pair_role"] == "expected"
    charge_balance = sic["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 32
    assert charge_balance["valence_electrons_per_non_passivant_atom"] == 4.0
    assert charge_balance["carrier_type_hint"] == "neutral_or_intrinsic"
    calculation = sic["calculation_preflight_summary"]
    assert calculation["module"] == "CASTEP"
    assert calculation["cutoff_energy_ev"] == 600
    assert calculation["ready_for_energy_preflight"] is True
    reciprocal = sic["reciprocal_lattice_summary"]
    assert reciprocal["estimated_kpoints_from_separation"] == [37, 37, 37]
    band_path = sic["band_path_summary"]
    assert band_path["bravais_lattice"] == "fcc"
    assert band_path["path_label"] == "Gamma-X-W-K-Gamma-L-U-W-L-K"


def test_model_view_audit_reports_4h_silicon_carbide_semiconductor_health() -> None:
    sic = model_view_audit(load_example("silicon_carbide_4h_hexagonal_spec.json"))["health"]["semiconductor_health"]

    assert sic["ok"] is True
    assert sic["rule"] == "group_iv_tetrahedral"
    assert sic["structure_family"] == "hexagonal 4H-SiC"
    assert sic["elements"] == ["C", "Si"]
    assert sic["composition_summary"]["formula"] == "C4Si4"
    assert sic["composition_summary"]["reduced_formula"] == "CSi"
    assert sic["neighbor_pair_counts"] == {"C-Si": 16}
    assert sic["unexpected_neighbor_pair_count"] == 0
    assert sic["coordination_outlier_count"] == 0
    assert sic["coordination_by_element"]["C"]["min"] == 4.0
    assert sic["coordination_by_element"]["Si"]["max"] == 4.0
    charge_balance = sic["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 32
    assert charge_balance["carrier_type_hint"] == "neutral_or_intrinsic"
    calculation = sic["calculation_preflight_summary"]
    assert calculation["module"] == "CASTEP"
    assert calculation["cutoff_energy_ev"] == 600
    assert calculation["ready_for_energy_preflight"] is True
    band_path = sic["band_path_summary"]
    assert band_path["bravais_lattice"] == "hexagonal"
    assert band_path["path_label"] == "Gamma-M-K-Gamma-A-L-H-A-L-M-K-H"


def test_model_view_audit_reports_6h_silicon_carbide_semiconductor_health() -> None:
    sic = model_view_audit(load_example("silicon_carbide_6h_hexagonal_spec.json"))["health"]["semiconductor_health"]

    assert sic["ok"] is True
    assert sic["rule"] == "group_iv_tetrahedral"
    assert sic["structure_family"] == "hexagonal 6H-SiC"
    assert sic["elements"] == ["C", "Si"]
    assert sic["composition_summary"]["formula"] == "C6Si6"
    assert sic["composition_summary"]["reduced_formula"] == "CSi"
    assert sic["neighbor_pair_counts"] == {"C-Si": 24}
    assert sic["unexpected_neighbor_pair_count"] == 0
    assert sic["coordination_outlier_count"] == 0
    assert sic["coordination_by_element"]["C"]["min"] == 4.0
    assert sic["coordination_by_element"]["Si"]["max"] == 4.0
    distances = sic["neighbor_distance_summary"]["distance_stats_angstrom"]
    assert distances["min"] == 1.884804
    assert distances["max"] == 1.89665
    assert distances["mean"] == 1.888015
    charge_balance = sic["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 48
    assert charge_balance["carrier_type_hint"] == "neutral_or_intrinsic"
    calculation = sic["calculation_preflight_summary"]
    assert calculation["module"] == "CASTEP"
    assert calculation["cutoff_energy_ev"] == 600
    assert calculation["ready_for_energy_preflight"] is True
    reciprocal = sic["reciprocal_lattice_summary"]
    assert reciprocal["estimated_kpoints_from_separation"] == [59, 59, 11]
    band_path = sic["band_path_summary"]
    assert band_path["bravais_lattice"] == "hexagonal"
    assert band_path["path_label"] == "Gamma-M-K-Gamma-A-L-H-A-L-M-K-H"


def test_model_view_audit_reports_ii_vi_wurtzite_health() -> None:
    audit = model_view_audit(load_example("zinc_oxide_wurtzite_spec.json"))
    zno = audit["health"]["semiconductor_health"]

    assert zno["ok"] is True
    assert zno["rule"] == "ii_vi_tetrahedral"
    assert zno["structure_family"] == "wurtzite"
    assert zno["elements"] == ["O", "Zn"]
    assert zno["composition_summary"]["formula"] == "Zn8O8"
    assert zno["composition_summary"]["reduced_formula"] == "ZnO"
    assert zno["neighbor_pair_counts"] == {"Zn-O": 32}
    assert zno["unexpected_neighbor_pair_count"] == 0
    assert zno["coordination_outlier_count"] == 0
    assert zno["coordination_by_element"]["Zn"]["min"] == 4.0
    assert zno["coordination_by_element"]["O"]["max"] == 4.0
    sublattice = zno["sublattice_balance_summary"]
    assert sublattice["balance_kind"] == "ii_vi_cation_anion_count"
    assert sublattice["balanced"] is True
    assert sublattice["ii_vi_cation_count"] == 8
    assert sublattice["ii_vi_anion_count"] == 8
    charge_balance = zno["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 64
    assert charge_balance["valence_electrons_per_non_passivant_atom"] == 4.0
    assert charge_balance["carrier_type_hint"] == "neutral_or_intrinsic"
    band_path = zno["band_path_summary"]
    assert band_path["bravais_lattice"] == "hexagonal"
    assert band_path["path_label"] == "Gamma-M-K-Gamma-A-L-H-A-L-M-K-H"

    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert modeling_health["checks"]["semiconductor_rule"] == "ii_vi_tetrahedral"
    assert modeling_health["checks"]["semiconductor_ii_vi_cation_count"] == 8
    assert modeling_health["checks"]["semiconductor_ii_vi_anion_count"] == 8
    assert modeling_health["checks"]["semiconductor_band_path_bravais_lattice"] == "hexagonal"


def test_model_view_audit_reports_inn_wurtzite_cutoff_artifact() -> None:
    audit = model_view_audit(load_example("indium_nitride_wurtzite_spec.json"))
    inn = audit["health"]["semiconductor_health"]

    assert inn["ok"] is True
    assert inn["rule"] == "iii_v_tetrahedral"
    assert inn["structure_family"] == "wurtzite"
    assert inn["elements"] == ["In", "N"]
    assert inn["composition_summary"]["reduced_formula"] == "InN"
    assert inn["neighbor_pair_counts"] == {"In-In": 6, "In-N": 8}
    assert inn["unexpected_neighbor_pair_count"] == 0
    assert inn["same_sublattice_cutoff_artifact_pair_count"] == 6
    assert inn["coordination_excluded_neighbor_pair_count"] == 6
    assert inn["coordination_excluded_pair_types"] == ["In-In"]
    assert inn["coordination_outlier_count"] == 0
    assert inn["coordination_by_element"]["In"]["min"] == 4.0
    assert inn["coordination_by_element"]["N"]["max"] == 4.0
    unchecked_types = {
        item["pair_type"]
        for item in inn["neighbor_distance_summary"]["pair_types"]
        if item["pair_role"] == "unchecked"
    }
    assert "In-In" in unchecked_types
    assert any("cutoff artifacts" in warning for warning in inn["warnings"])
    assert inn["charge_balance_summary"]["carrier_type_hint"] == "neutral_or_intrinsic"
    assert inn["band_path_summary"]["bravais_lattice"] == "hexagonal"

    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert modeling_health["checks"]["semiconductor_rule"] == "iii_v_tetrahedral"
    assert modeling_health["checks"]["semiconductor_same_sublattice_cutoff_artifact_pair_count"] == 6
    assert modeling_health["checks"]["semiconductor_coordination_excluded_neighbor_pair_count"] == 6
    assert modeling_health["checks"]["semiconductor_coordination_excluded_pair_types"] == ["In-In"]


def test_model_view_audit_reports_inn_0001_slab_surface_health() -> None:
    audit = model_view_audit(load_example("indium_nitride_0001_slab_spec.json"))
    inn = audit["health"]["semiconductor_health"]
    surface = inn["surface_polarity_summary"]

    assert inn["ok"] is True
    assert inn["rule"] == "iii_v_tetrahedral"
    assert inn["structure_family"] == "wurtzite slab"
    assert inn["composition_summary"]["reduced_formula"] == "InN"
    assert inn["neighbor_pair_counts"] == {"In-In": 3, "In-N": 5}
    assert inn["unexpected_neighbor_pair_count"] == 0
    assert inn["same_sublattice_cutoff_artifact_pair_count"] == 3
    assert inn["coordination_excluded_neighbor_pair_count"] == 3
    assert audit["health"]["slab_vacuum"]["vacuum_ok"] is True
    assert inn["surface_termination_summary"]["dangling_bond_estimate"] > 0
    assert inn["surface_termination_summary"]["surface_preparation_status"] == "dangling_bonds"
    assert surface["polar_surface_hint"] is True
    assert surface["surface_polarity_status"] == "asymmetric_or_polar"
    assert surface["bottom"]["formula"] == "In"
    assert surface["top"]["formula"] == "N"


def test_model_view_audit_reports_ii_vi_zincblende_health() -> None:
    audit = model_view_audit(load_example("cadmium_telluride_zincblende_spec.json"))
    cdte = audit["health"]["semiconductor_health"]

    assert cdte["ok"] is True
    assert cdte["rule"] == "ii_vi_tetrahedral"
    assert cdte["structure_family"] == "zinc blende"
    assert cdte["elements"] == ["Cd", "Te"]
    assert cdte["composition_summary"]["formula"] == "Cd4Te4"
    assert cdte["composition_summary"]["reduced_formula"] == "CdTe"
    assert cdte["neighbor_pair_counts"] == {"Cd-Te": 16}
    assert cdte["unexpected_neighbor_pair_count"] == 0
    assert cdte["coordination_outlier_count"] == 0
    assert cdte["coordination_by_element"]["Cd"]["min"] == 4.0
    assert cdte["coordination_by_element"]["Te"]["max"] == 4.0
    sublattice = cdte["sublattice_balance_summary"]
    assert sublattice["balance_kind"] == "ii_vi_cation_anion_count"
    assert sublattice["balanced"] is True
    assert sublattice["ii_vi_cation_count"] == 4
    assert sublattice["ii_vi_anion_count"] == 4
    charge_balance = cdte["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 32
    assert charge_balance["carrier_type_hint"] == "neutral_or_intrinsic"
    reciprocal = cdte["reciprocal_lattice_summary"]
    assert reciprocal["estimated_kpoints_from_separation"] == [25, 25, 25]
    band_path = cdte["band_path_summary"]
    assert band_path["bravais_lattice"] == "fcc"
    assert band_path["path_label"] == "Gamma-X-W-K-Gamma-L-U-W-L-K"

    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert modeling_health["checks"]["semiconductor_rule"] == "ii_vi_tetrahedral"
    assert modeling_health["checks"]["semiconductor_ii_vi_cation_count"] == 4
    assert modeling_health["checks"]["semiconductor_ii_vi_anion_count"] == 4
    assert modeling_health["checks"]["semiconductor_band_path_bravais_lattice"] == "fcc"


def test_model_view_audit_reports_additional_ii_vi_zincblende_templates() -> None:
    cases = [
        ("zinc_sulfide_zincblende_spec.json", "ZnS", ["S", "Zn"], {"Zn-S": 16}),
        ("zinc_selenide_zincblende_spec.json", "ZnSe", ["Se", "Zn"], {"Zn-Se": 16}),
        ("zinc_telluride_zincblende_spec.json", "ZnTe", ["Te", "Zn"], {"Zn-Te": 16}),
        ("cadmium_sulfide_zincblende_spec.json", "CdS", ["Cd", "S"], {"Cd-S": 16}),
        ("cadmium_selenide_zincblende_spec.json", "CdSe", ["Cd", "Se"], {"Cd-Se": 16}),
    ]

    for example, formula, elements, neighbor_pairs in cases:
        semiconductor = model_view_audit(load_example(example))["health"]["semiconductor_health"]

        assert semiconductor["ok"] is True
        assert semiconductor["rule"] == "ii_vi_tetrahedral"
        assert semiconductor["structure_family"] == "zinc blende"
        assert semiconductor["elements"] == elements
        assert semiconductor["composition_summary"]["reduced_formula"] == formula
        assert semiconductor["neighbor_pair_counts"] == neighbor_pairs
        assert semiconductor["unexpected_neighbor_pair_count"] == 0
        assert semiconductor["coordination_outlier_count"] == 0
        assert semiconductor["sublattice_balance_summary"]["balanced"] is True
        assert semiconductor["sublattice_balance_summary"]["ii_vi_cation_count"] == 4
        assert semiconductor["sublattice_balance_summary"]["ii_vi_anion_count"] == 4
        assert semiconductor["charge_balance_summary"]["total_valence_electron_count"] == 32
        assert semiconductor["charge_balance_summary"]["carrier_type_hint"] == "neutral_or_intrinsic"
        assert semiconductor["band_path_summary"]["bravais_lattice"] == "fcc"
        assert semiconductor["band_path_summary"]["path_label"] == "Gamma-X-W-K-Gamma-L-U-W-L-K"


def test_model_view_audit_reports_tmd_mos2_monolayer_health(tmp_path: Path) -> None:
    audit = model_view_audit(load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json"))
    mos2 = audit["health"]["semiconductor_health"]

    assert mos2["ok"] is True
    assert mos2["rule"] == "tmd_layered_trigonal_prismatic"
    assert mos2["structure_family"] == "2d tmd monolayer"
    assert mos2["elements"] == ["Mo", "S"]
    assert mos2["expected_coordination"] is None
    assert mos2["expected_coordination_by_element"] == {"Mo": 6, "S": 3}
    assert mos2["host_elements"] == ["Mo", "S"]
    assert mos2["dopant_elements"] == []
    assert mos2["composition_summary"]["formula"] == "Mo4S8"
    assert mos2["composition_summary"]["reduced_formula"] == "MoS2"
    assert mos2["neighbor_pair_counts"] == {"Mo-S": 24}
    assert mos2["unexpected_neighbor_pair_count"] == 0
    assert mos2["coordination_outlier_count"] == 0
    assert mos2["coordination_by_element"]["Mo"]["min"] == 6.0
    assert mos2["coordination_by_element"]["Mo"]["max"] == 6.0
    assert mos2["coordination_by_element"]["S"]["min"] == 3.0
    assert mos2["coordination_by_element"]["S"]["max"] == 3.0
    local_environment = mos2["local_environment_summary"]
    assert local_environment["expected_coordination_by_element"] == {"Mo": 6, "S": 3}
    assert local_environment["coordination_outlier_count"] == 0
    mo_environment = next(item for item in local_environment["local_environments"] if item["atom_id"] == "Mo1_000")
    s_environment = next(item for item in local_environment["local_environments"] if item["atom_id"] == "Stop1_000")
    assert mo_environment["expected_coordination"] == 6
    assert mo_environment["neighbor_count"] == 6
    assert s_environment["expected_coordination"] == 3
    assert s_environment["neighbor_count"] == 3
    sublattice = mos2["sublattice_balance_summary"]
    assert sublattice["balance_kind"] == "tmd_metal_chalcogen_ratio"
    assert sublattice["balanced"] is True
    assert sublattice["tmd_metal_count"] == 4
    assert sublattice["tmd_chalcogen_count"] == 8
    assert sublattice["balance_delta_count"] == 0
    charge_balance = mos2["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 72
    assert charge_balance["electron_count_parity"] == "even"
    assert mos2["band_path_summary"]["bravais_lattice"] == "hexagonal"
    assert mos2["band_path_summary"]["path_label"] == "Gamma-M-K-Gamma-A-L-H-A-L-M-K-H"
    assert mos2["lattice_summary"]["is_slab"] is True
    assert mos2["lattice_summary"]["vacuum_ok"] is True
    assert mos2["surface_termination_summary"]["dangling_bond_estimate"] == 0
    assert mos2["surface_termination_summary"]["surface_preparation_status"] == "no_dangling_bonds_detected"
    assert mos2["surface_model_summary"]["status"] == "ready"
    assert mos2["surface_model_summary"]["ready_for_calculation_preflight"] is True
    assert mos2["surface_model_summary"]["next_action"] == "surface_model_ready_for_calculation_preflight"

    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    checks = modeling_health["checks"]
    assert checks["semiconductor_rule"] == "tmd_layered_trigonal_prismatic"
    assert checks["semiconductor_expected_coordination_by_element"] == {"Mo": 6, "S": 3}
    assert checks["semiconductor_tmd_metal_count"] == 4
    assert checks["semiconductor_tmd_chalcogen_count"] == 8
    assert checks["semiconductor_sublattice_balanced"] is True

    bundle = write_view_audit_bundle(
        tmp_path,
        load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json"),
        audit,
        modeling_health=modeling_health,
    )
    assert bundle["row_counts"]["semiconductor_local_environment"] == 12
    assert bundle["row_counts"]["semiconductor_sublattice_balance"] == 2
    local_environment_csv = Path(bundle["files"]["semiconductor_local_environment_csv"]).read_text(encoding="utf-8")
    assert "Mo1_000,Mo,6,6,False" in local_environment_csv
    assert "Stop1_000,S,3,3,False" in local_environment_csv


def test_model_view_audit_reports_hbn_monolayer_health(tmp_path: Path) -> None:
    spec = load_example("hexagonal_boron_nitride_2d_hbn_monolayer_spec.json")
    audit = model_view_audit(spec)
    hbn = audit["health"]["semiconductor_health"]

    assert hbn["ok"] is True
    assert hbn["rule"] == "iii_v_layered_trigonal_planar"
    assert hbn["structure_family"] == "2d hbn monolayer"
    assert hbn["elements"] == ["B", "N"]
    assert hbn["expected_coordination"] is None
    assert hbn["expected_coordination_by_element"] == {"B": 3, "N": 3}
    assert hbn["composition_summary"]["formula"] == "B4N4"
    assert hbn["composition_summary"]["reduced_formula"] == "BN"
    assert hbn["neighbor_pair_counts"] == {"B-N": 12}
    assert hbn["unexpected_neighbor_pair_count"] == 0
    assert hbn["coordination_outlier_count"] == 0
    assert hbn["coordination_by_element"]["B"]["min"] == 3.0
    assert hbn["coordination_by_element"]["N"]["max"] == 3.0
    assert hbn["lattice_summary"]["is_slab"] is True
    assert hbn["lattice_summary"]["vacuum_ok"] is True
    assert hbn["surface_model_summary"]["status"] == "ready"

    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    checks = modeling_health["checks"]
    assert checks["semiconductor_rule"] == "iii_v_layered_trigonal_planar"
    assert checks["semiconductor_expected_coordination_by_element"] == {"B": 3, "N": 3}
    assert checks["semiconductor_coordination_outlier_count"] == 0

    bundle = write_view_audit_bundle(tmp_path, spec, audit, modeling_health=modeling_health)
    assert bundle["row_counts"]["semiconductor_local_environment"] == 8
    local_environment_csv = Path(bundle["files"]["semiconductor_local_environment_csv"]).read_text(encoding="utf-8")
    assert "B1_000,B,3,3,False" in local_environment_csv
    assert "N1_000,N,3,3,False" in local_environment_csv


def test_model_view_audit_reports_common_2d_tmd_monolayer_health(tmp_path: Path) -> None:
    cases = [
        (
            "tungsten_disulfide_2d_ws2_monolayer_spec.json",
            "WS2",
            ["S", "W"],
            {"W": 6, "S": 3},
            {"W-S": 24},
            "W1_000,W,6,6,False",
        ),
        (
            "molybdenum_diselenide_2d_mose2_monolayer_spec.json",
            "MoSe2",
            ["Mo", "Se"],
            {"Mo": 6, "Se": 3},
            {"Mo-Se": 24},
            "Setop1_000,Se,3,3,False",
        ),
        (
            "tungsten_diselenide_2d_wse2_monolayer_spec.json",
            "WSe2",
            ["Se", "W"],
            {"W": 6, "Se": 3},
            {"W-Se": 24},
            "Setop1_000,Se,3,3,False",
        ),
    ]
    for filename, formula, elements, expected_coordination, neighbor_pairs, csv_marker in cases:
        spec = load_example(filename)
        audit = model_view_audit(spec)
        tmd = audit["health"]["semiconductor_health"]

        assert tmd["ok"] is True
        assert tmd["rule"] == "tmd_layered_trigonal_prismatic"
        assert tmd["structure_family"] == "2d tmd monolayer"
        assert tmd["elements"] == elements
        assert tmd["expected_coordination_by_element"] == expected_coordination
        assert tmd["composition_summary"]["reduced_formula"] == formula
        assert tmd["neighbor_pair_counts"] == neighbor_pairs
        assert tmd["coordination_outlier_count"] == 0
        assert tmd["sublattice_balance_summary"]["balanced"] is True
        assert tmd["sublattice_balance_summary"]["tmd_metal_count"] == 4
        assert tmd["sublattice_balance_summary"]["tmd_chalcogen_count"] == 8
        assert tmd["surface_termination_summary"]["dangling_bond_estimate"] == 0

        modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
        assert modeling_health["checks"]["semiconductor_expected_coordination_by_element"] == expected_coordination
        assert modeling_health["checks"]["semiconductor_sublattice_balanced"] is True
        bundle = write_view_audit_bundle(tmp_path / formula.lower(), spec, audit, modeling_health=modeling_health)
        assert bundle["row_counts"]["semiconductor_local_environment"] == 12
        local_environment_csv = Path(bundle["files"]["semiconductor_local_environment_csv"]).read_text(encoding="utf-8")
        assert csv_marker in local_environment_csv


def test_model_view_audit_reports_tmd_dopant_site_roles_and_vacancy() -> None:
    mos2 = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")

    cl_plan = infer_modeling_plan("Dope S sublattice with Cl.", current_spec=mos2)
    assert cl_plan.template_id == "crystal_sublattice_dopant"
    cl_doped, _ = apply_semantic_patch(
        mos2,
        SemanticPatch(
            project_id=mos2.project_id,
            base_revision=mos2.revision,
            operations=cl_plan.payload["operations"],
        ),
    )
    cl_health = model_view_audit(cl_doped)["health"]["semiconductor_health"]
    cl_site = cl_health["dopant_site_summary"]
    cl_charge = cl_health["charge_balance_summary"]

    assert cl_health["rule"] == "doped_tmd_layered_trigonal_prismatic"
    assert cl_health["dopant_summary"]["host_elements"] == ["Mo", "S"]
    assert cl_health["dopant_summary"]["dopant_elements"] == ["Cl"]
    assert cl_health["expected_coordination_by_element"] == {"Cl": 3, "Mo": 6, "S": 3}
    assert cl_health["coordination_outlier_count"] == 0
    assert cl_health["unexpected_neighbor_pair_count"] == 0
    assert cl_health["neighbor_pair_counts"] == {"Cl-Mo": 3, "Mo-S": 21}
    pair_roles = {
        row["pair_type"]: row["pair_role"]
        for row in cl_health["neighbor_distance_summary"]["pair_types"]
    }
    assert pair_roles == {"Cl-Mo": "expected", "Mo-S": "expected"}
    assert cl_site["carrier_type_hint"] == "donor_like_n_type"
    assert cl_site["latest"]["site_family"] == "tmd_chalcogen"
    assert cl_site["latest"]["site_element"] == "S"
    assert cl_site["latest"]["dopant_element"] == "Cl"
    assert cl_site["latest"]["role_hint"] == "donor_like_n_type_on_tmd_chalcogen_site"
    assert cl_charge["carrier_type_hint"] == "donor_like_n_type"
    assert cl_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert cl_charge["site_adjusted_dopant_delta_electrons"] == 1

    w_plan = infer_modeling_plan("Dope with W.", current_spec=mos2)
    assert w_plan.template_id == "crystal_auto_dopant"
    w_doped, _ = apply_semantic_patch(
        mos2,
        SemanticPatch(
            project_id=mos2.project_id,
            base_revision=mos2.revision,
            operations=w_plan.payload["operations"],
        ),
    )
    w_site = model_view_audit(w_doped)["health"]["semiconductor_health"]["dopant_site_summary"]
    assert w_site["carrier_type_hint"] == "neutral_or_intrinsic"
    assert w_site["latest"]["site_family"] == "tmd_metal"
    assert w_site["latest"]["site_element"] == "Mo"
    assert w_site["latest"]["dopant_element"] == "W"
    assert w_site["latest"]["role_hint"] == "isovalent_tmd_metal_substitution"

    vacancy_plan = infer_modeling_plan("Create S vacancy.", current_spec=mos2)
    assert vacancy_plan.template_id == "crystal_auto_vacancy"
    vacancy, _ = apply_semantic_patch(
        mos2,
        SemanticPatch(
            project_id=mos2.project_id,
            base_revision=mos2.revision,
            operations=vacancy_plan.payload["operations"],
        ),
    )
    defect = model_view_audit(vacancy)["health"]["semiconductor_health"]["defect_summary"]
    assert defect["vacancy_count"] == 1
    assert defect["defects"][0]["site_element"] == "S"
    assert defect["defects"][0]["expected_neighbor_count"] == 3
    assert defect["defects"][0]["auto_selected_site"] is True


def test_model_view_audit_reports_additional_iii_v_zincblende_templates() -> None:
    cases = [
        ("aluminum_phosphide_zincblende_spec.json", "AlP", ["Al", "P"], {"Al-P": 16}),
        ("aluminum_antimonide_zincblende_spec.json", "AlSb", ["Al", "Sb"], {"Al-Sb": 16}),
        ("gallium_phosphide_zincblende_spec.json", "GaP", ["Ga", "P"], {"Ga-P": 16}),
        ("gallium_antimonide_zincblende_spec.json", "GaSb", ["Ga", "Sb"], {"Ga-Sb": 16}),
        ("indium_antimonide_zincblende_spec.json", "InSb", ["In", "Sb"], {"In-Sb": 16}),
    ]

    for example, formula, elements, neighbor_pairs in cases:
        semiconductor = model_view_audit(load_example(example))["health"]["semiconductor_health"]

        assert semiconductor["ok"] is True
        assert semiconductor["rule"] == "iii_v_tetrahedral"
        assert semiconductor["structure_family"] == "zinc blende"
        assert semiconductor["elements"] == elements
        assert semiconductor["composition_summary"]["reduced_formula"] == formula
        assert semiconductor["neighbor_pair_counts"] == neighbor_pairs
        assert semiconductor["unexpected_neighbor_pair_count"] == 0
        assert semiconductor["coordination_outlier_count"] == 0
        assert semiconductor["sublattice_balance_summary"]["balanced"] is True
        assert semiconductor["sublattice_balance_summary"]["iii_v_cation_count"] == 4
        assert semiconductor["sublattice_balance_summary"]["iii_v_anion_count"] == 4
        assert semiconductor["charge_balance_summary"]["total_valence_electron_count"] == 32
        assert semiconductor["charge_balance_summary"]["carrier_type_hint"] == "neutral_or_intrinsic"
        assert semiconductor["band_path_summary"]["bravais_lattice"] == "fcc"
        assert semiconductor["band_path_summary"]["path_label"] == "Gamma-X-W-K-Gamma-L-U-W-L-K"


def test_model_view_audit_reports_semiconductor_health_for_iii_v_and_heterostructure() -> None:
    gaas = model_view_audit(load_example("gallium_arsenide_zincblende_spec.json"))["health"]["semiconductor_health"]
    assert gaas["ok"] is True
    assert gaas["rule"] == "iii_v_tetrahedral"
    assert gaas["neighbor_pair_counts"] == {"Ga-As": 16}
    assert gaas["coordination_by_element"]["Ga"]["min"] == 4.0
    assert gaas["coordination_by_element"]["As"]["max"] == 4.0
    assert gaas["unexpected_neighbor_pair_count"] == 0
    gaas_neighbors = gaas["neighbor_distance_summary"]
    assert gaas_neighbors["neighbor_pair_count"] == 16
    assert gaas_neighbors["pair_type_count"] == 1
    assert gaas_neighbors["distance_stats_angstrom"]["mean"] == 2.447951
    assert gaas_neighbors["pair_types"][0]["pair_type"] == "Ga-As"
    assert gaas_neighbors["pair_types"][0]["pair_role"] == "expected"

    hetero = model_view_audit(load_example("gallium_arsenide_aluminum_arsenide_001_heterostructure_spec.json"))["health"]["semiconductor_health"]
    assert hetero["ok"] is True
    assert hetero["interface"] == "GaAs/AlAs"
    assert hetero["neighbor_pair_counts"] == {"Al-As": 16, "Ga-As": 16}
    assert hetero["coordination_by_element"]["Al"]["min"] == 4.0
    assert hetero["coordination_by_element"]["Ga"]["max"] == 4.0
    sublattice = hetero["sublattice_balance_summary"]
    assert sublattice["balance_kind"] == "iii_v_cation_anion_count"
    assert sublattice["iii_v_cation_count"] == 8
    assert sublattice["iii_v_anion_count"] == 8
    assert sublattice["balance_delta_count"] == 0
    assert sublattice["balanced"] is True
    assert sublattice["warning"] is False
    lattice = hetero["lattice_summary"]
    assert lattice["a_angstrom"] == 5.6572
    assert lattice["c_angstrom"] == 11.3144
    assert lattice["cell_volume_angstrom3"] == 362.10506
    assert lattice["non_passivant_atom_count"] == 16
    assert lattice["volume_per_non_passivant_atom_angstrom3"] == 22.631566
    charge_balance = hetero["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 64
    assert charge_balance["electron_count_parity"] == "even"
    assert charge_balance["valence_electrons_per_non_passivant_atom"] == 4.0
    calculation = hetero["calculation_preflight_summary"]
    assert calculation["functional"] == "PBE"
    assert calculation["quality"] == "Medium"
    assert calculation["status"] == "ok"
    neighbor_distances = hetero["neighbor_distance_summary"]
    assert neighbor_distances["neighbor_pair_count"] == 32
    assert neighbor_distances["pair_type_count"] == 2
    assert neighbor_distances["distance_stats_angstrom"]["min"] == 2.449078
    assert {item["pair_type"] for item in neighbor_distances["pair_types"]} == {"Al-As", "Ga-As"}
    assert {item["pair_role"] for item in neighbor_distances["pair_types"]} == {"expected"}
    local_environment = hetero["local_environment_summary"]
    assert local_environment["atom_count"] == 16
    assert local_environment["coordination_outlier_count"] == 0
    assert local_environment["angle_stats_deg"]["min"] == 109.434064
    assert local_environment["tetrahedral_angle_deviation_stats_deg"]["max"] == 0.037173
    al1_environment = next(item for item in local_environment["local_environments"] if item["atom_id"] == "Al1")
    assert al1_environment["neighbor_ids"] == ["AsG2", "AsG3", "AsA1", "AsA4"]
    assert al1_environment["max_tetrahedral_angle_deviation_deg"] == 0.036741
    interface_profile = hetero["interface_profile_summary"]
    assert interface_profile["axis"] == "c"
    assert interface_profile["material_segment_count"] == 2
    assert interface_profile["interface_transition_count"] == 1
    assert interface_profile["mixed_layer_count"] == 0
    assert interface_profile["abrupt_interface"] is True
    assert interface_profile["segments"][0]["material_marker"] == "Ga"
    assert interface_profile["segments"][1]["material_marker"] == "Al"
    assert interface_profile["transitions"][0]["from_layer_index"] == 3
    assert interface_profile["transitions"][0]["to_layer_index"] == 5
    interface_quality = hetero["interface_quality_summary"]
    assert interface_quality["quality"] == "complete"
    assert interface_quality["material_sequence"] == ["GaAs", "AlAs"]
    assert interface_quality["expected_material_sequence"] == ["GaAs", "AlAs"]
    assert interface_quality["period_count"] == 1
    assert interface_quality["expected_segment_count_from_periods"] == 2
    assert interface_quality["segment_count_matches_periods"] is True
    assert interface_quality["period_sequence_complete"] is True
    assert interface_quality["transition_sequence_complete"] is True
    assert interface_quality["periodic_interface_transition_count"] == 2
    assert interface_quality["warning_count"] == 0
    quantum_well = hetero["quantum_well_summary"]
    assert quantum_well["interface"] == "GaAs/AlAs"
    assert quantum_well["period_count"] == 1
    assert quantum_well["material_segment_count"] == 2
    assert quantum_well["well_material"] == "GaAs"
    assert quantum_well["barrier_materials"] == ["AlAs"]
    assert quantum_well["well_thickness_stats_angstrom"]["mean"] == 5.654286
    assert quantum_well["barrier_thickness_stats_angstrom"]["mean"] == 5.660113
    assert quantum_well["period_thickness_stats_angstrom"]["mean"] == 11.314399
    assert quantum_well["segments"][0]["material"] == "GaAs"
    assert quantum_well["segments"][0]["role"] == "well"
    assert quantum_well["segments"][0]["layer_count"] == 4
    assert quantum_well["segments"][1]["material"] == "AlAs"
    assert quantum_well["segments"][1]["role"] == "barrier"
    assert quantum_well["segments"][1]["last_layer_index"] == 8
    assert quantum_well["warning_count"] == 0
    assert quantum_well["note_count"] == 1
    band_alignment = hetero["band_alignment_summary"]
    assert band_alignment["quality"] == "complete"
    assert band_alignment["reference_material"] == "GaAs"
    assert band_alignment["type_i_barrier_count"] == 1
    assert band_alignment["review_offset_count"] == 0
    assert band_alignment["offsets"][0]["material"] == "AlAs"
    assert band_alignment["offsets"][0]["conduction_band_offset_vs_reference_ev"] == 0.57
    assert band_alignment["offsets"][0]["hole_barrier_height_ev"] == 0.17
    assert band_alignment["offsets"][0]["alignment_type"] == "type_i_quantum_well_preflight"
    assert hetero["polarization_2deg_summary"] is None
    strain = hetero["heterostructure_summary"]
    assert strain["interface"] == "GaAs/AlAs"
    assert strain["substrate"] == "GaAs"
    assert strain["in_plane_lattice_angstrom"] == 5.6572
    assert strain["max_abs_in_plane_strain_percent"] < 0.08
    assert strain["max_abs_lattice_mismatch_to_substrate_percent"] < 0.15

    algan_gan = model_view_audit(load_example("aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json"))["health"]["semiconductor_health"]
    assert algan_gan["ok"] is True
    assert algan_gan["interface"] == "GaN/Al0.25Ga0.75N"
    assert algan_gan["neighbor_pair_counts"] == {"Al-N": 8, "Ga-N": 56}
    assert algan_gan["unexpected_neighbor_pair_count"] == 0
    assert algan_gan["alloy_same_sublattice_neighbor_pair_count"] == 0
    algan_sublattice = algan_gan["sublattice_balance_summary"]
    assert algan_sublattice["iii_v_cation_count"] == 16
    assert algan_sublattice["iii_v_anion_count"] == 16
    assert algan_sublattice["balanced"] is True
    algan_interface = algan_gan["interface_profile_summary"]
    assert algan_interface["material_segment_count"] == 2
    assert algan_interface["interface_transition_count"] == 1
    assert algan_interface["mixed_layer_count"] == 2
    assert algan_interface["abrupt_interface"] is False
    assert algan_interface["segments"][0]["material_marker"] == "Ga"
    assert algan_interface["segments"][1]["material_marker"] == "Al;Ga"
    algan_quality = algan_gan["interface_quality_summary"]
    assert algan_quality["quality"] == "complete_with_mixed_layers"
    assert algan_quality["material_sequence"] == ["GaN", "Al0.25Ga0.75N"]
    assert algan_quality["period_sequence_complete"] is True
    assert algan_quality["mixed_layer_count"] == 2
    algan_quantum_well = algan_gan["quantum_well_summary"]
    assert algan_quantum_well["well_material"] == "GaN"
    assert algan_quantum_well["barrier_materials"] == ["Al0.25Ga0.75N"]
    assert algan_quantum_well["segments"][0]["material"] == "GaN"
    assert algan_quantum_well["segments"][1]["material"] == "Al0.25Ga0.75N"
    assert algan_quantum_well["warning_count"] == 1
    algan_alignment = algan_gan["band_alignment_summary"]
    assert algan_alignment["quality"] == "complete"
    assert algan_alignment["reference_material"] == "GaN"
    assert algan_alignment["offsets"][0]["material"] == "Al0.25Ga0.75N"
    assert algan_alignment["offsets"][0]["electron_barrier_height_ev"] == 0.45
    assert algan_alignment["offsets"][0]["hole_barrier_height_ev"] == 0.2
    algan_polarization = algan_gan["polarization_2deg_summary"]
    assert algan_polarization["quality"] == "complete"
    assert algan_polarization["well_material"] == "GaN"
    assert algan_polarization["barrier_materials"] == ["Al0.25Ga0.75N"]
    assert algan_polarization["candidate_count"] == 1
    assert algan_polarization["max_abs_sheet_carrier_density_cm2"] > 1.0e13
    assert algan_polarization["barriers"][0]["barrier_material"] == "Al0.25Ga0.75N"
    assert algan_polarization["barriers"][0]["barrier_al_fraction"] == 0.25
    assert algan_polarization["barriers"][0]["barrier_in_fraction"] == 0.0
    assert algan_polarization["barriers"][0]["electron_barrier_height_ev"] == 0.45
    assert algan_polarization["barriers"][0]["two_deg_candidate"] is True
    algan_alloy = algan_gan["alloy_summary"]
    assert algan_alloy["latest"]["actual_fraction"] == 0.25
    assert algan_alloy["latest"]["selected_atom_ids"] == ["Ga1_001", "Ga2_011"]
    algan_strain = algan_gan["heterostructure_summary"]
    assert algan_strain["substrate"] == "GaN"
    assert algan_strain["max_abs_in_plane_strain_percent"] == 0.607303
    assert algan_strain["strain_warning"] is False

    ingan_gan = model_view_audit(load_example("indium_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json"))["health"]["semiconductor_health"]
    assert ingan_gan["ok"] is True
    assert ingan_gan["interface"] == "GaN/In0.25Ga0.75N"
    assert ingan_gan["neighbor_pair_counts"] == {"Ga-In": 22, "Ga-N": 56, "In-In": 1, "In-N": 8}
    assert ingan_gan["unexpected_neighbor_pair_count"] == 0
    assert ingan_gan["alloy_same_sublattice_neighbor_pair_count"] == 23
    assert ingan_gan["coordination_excluded_neighbor_pair_count"] == 23
    assert ingan_gan["coordination_excluded_pair_types"] == ["Ga-In", "In-In"]
    assert ingan_gan["coordination_outlier_count"] == 0
    assert any("same-sublattice neighbor pairs" in warning for warning in ingan_gan["warnings"])
    ingan_sublattice = ingan_gan["sublattice_balance_summary"]
    assert ingan_sublattice["iii_v_cation_count"] == 16
    assert ingan_sublattice["iii_v_anion_count"] == 16
    assert ingan_sublattice["balanced"] is True
    ingan_interface = ingan_gan["interface_profile_summary"]
    assert ingan_interface["material_segment_count"] == 2
    assert ingan_interface["interface_transition_count"] == 1
    assert ingan_interface["mixed_layer_count"] == 2
    assert ingan_interface["segments"][1]["material_marker"] == "Ga;In"
    ingan_quantum_well = ingan_gan["quantum_well_summary"]
    assert ingan_quantum_well["well_material"] == "GaN"
    assert ingan_quantum_well["barrier_materials"] == ["In0.25Ga0.75N"]
    assert ingan_quantum_well["segments"][1]["material"] == "In0.25Ga0.75N"
    assert ingan_quantum_well["segments"][1]["element_counts"] == {"Ga": 6, "In": 2, "N": 8}
    assert ingan_quantum_well["segments"][1]["cation_counts"] == {"Ga": 6, "In": 2}
    assert ingan_quantum_well["segments"][1]["cation_fractions"] == {"Ga": 0.75, "In": 0.25}
    assert ingan_quantum_well["barrier_cation_fractions_by_material"] == {
        "In0.25Ga0.75N": {"Ga": 0.75, "In": 0.25}
    }
    ingan_alignment = ingan_gan["band_alignment_summary"]
    assert ingan_alignment["quality"] == "review"
    assert ingan_alignment["review_offset_count"] == 1
    assert ingan_alignment["offsets"][0]["material"] == "In0.25Ga0.75N"
    assert ingan_alignment["offsets"][0]["electron_barrier_height_ev"] == -0.25
    assert ingan_alignment["offsets"][0]["confines_electrons"] is False
    assert any("may not confine both carriers" in warning for warning in ingan_alignment["warnings"])
    ingan_polarization = ingan_gan["polarization_2deg_summary"]
    assert ingan_polarization["quality"] == "review"
    assert ingan_polarization["candidate_count"] == 0
    assert ingan_polarization["max_abs_sheet_carrier_density_cm2"] > 1.0e13
    assert ingan_polarization["barriers"][0]["barrier_material"] == "In0.25Ga0.75N"
    assert ingan_polarization["barriers"][0]["barrier_al_fraction"] == 0.0
    assert ingan_polarization["barriers"][0]["barrier_in_fraction"] == 0.25
    assert ingan_polarization["barriers"][0]["electron_barrier_height_ev"] == -0.25
    assert ingan_polarization["barriers"][0]["two_deg_candidate"] is False
    assert any("does not identify" in warning for warning in ingan_polarization["warnings"])
    ingan_alloy = ingan_gan["alloy_summary"]
    assert ingan_alloy["latest"]["actual_fraction"] == 0.25
    assert ingan_alloy["latest"]["selected_atom_ids"] == ["Ga1_001", "Ga2_011"]
    ingan_strain = ingan_gan["heterostructure_summary"]
    assert ingan_strain["substrate"] == "GaN"
    assert ingan_strain["max_abs_in_plane_strain_percent"] == 2.71507
    assert ingan_strain["strain_warning"] is False

    sige = model_view_audit(load_example("silicon_germanium_001_heterostructure_spec.json"))["health"]["semiconductor_health"]
    sige_strain = sige["heterostructure_summary"]
    assert sige_strain["interface"] == "Si/Ge"
    assert sige_strain["substrate"] == "Si"
    assert sige_strain["materials"] == ["Si", "Ge"]
    assert 2.0 < sige_strain["max_abs_in_plane_strain_percent"] < 2.2
    assert 4.0 < sige_strain["max_abs_lattice_mismatch_to_substrate_percent"] < 4.3
    assert sige_strain["strain_warning"] is False
    sige_layers = sige["layer_profile_summary"]
    assert sige_layers["axis"] == "c"
    assert sige_layers["axis_source"] == "interface_axis"
    assert sige_layers["layer_count"] == 8
    assert sige_layers["layers"][0]["element_counts"] == {"Si": 2}
    assert sige_layers["layers"][-1]["element_counts"] == {"Ge": 2}
    assert sige_layers["min_interlayer_spacing_angstrom"] > 1.3
    assert sige_layers["spacing_warning"] is False
    sige_interface = sige["interface_profile_summary"]
    assert sige_interface["material_segment_count"] == 2
    assert sige_interface["interface_transition_count"] == 1
    assert sige_interface["segments"][0]["material_marker"] == "Si"
    assert sige_interface["segments"][1]["material_marker"] == "Ge"
    assert sige_interface["transitions"][0]["from_layer_index"] == 4
    assert sige_interface["transitions"][0]["to_layer_index"] == 5
    sige_quantum_well = sige["quantum_well_summary"]
    assert sige_quantum_well["interface"] == "Si/Ge"
    assert sige_quantum_well["well_material"] == "Si"
    assert sige_quantum_well["barrier_materials"] == ["Ge"]
    assert sige_quantum_well["well_thickness_stats_angstrom"]["mean"] == 5.459514
    assert sige_quantum_well["barrier_thickness_stats_angstrom"]["mean"] == 5.629486
    assert sige_quantum_well["period_thickness_stats_angstrom"]["mean"] == 11.089
    assert sige_quantum_well["warning_count"] == 0
    sige_alignment = sige["band_alignment_summary"]
    assert sige_alignment["quality"] == "review"
    assert sige_alignment["reference_material"] == "Si"
    assert sige_alignment["offsets"][0]["material"] == "Ge"
    assert sige_alignment["offsets"][0]["hole_barrier_height_ev"] == -0.51
    assert sige_alignment["offsets"][0]["alignment_type"] == "type_ii_or_inverted_barrier_review"
    assert sige["polarization_2deg_summary"] is None


def test_model_view_audit_reports_semiconductor_layer_profile_for_slab() -> None:
    slab = load_example("silicon_100_slab_spec.json")
    audit = model_view_audit(slab)
    semiconductor = audit["health"]["semiconductor_health"]
    layer_profile = semiconductor["layer_profile_summary"]
    lattice = semiconductor["lattice_summary"]
    slab_vacuum = audit["health"]["slab_vacuum"]

    assert layer_profile["axis"] == "c"
    assert layer_profile["axis_source"] == "surface_axis"
    assert layer_profile["axis_length_angstrom"] == 25.0
    assert layer_profile["layer_count"] == 4
    assert layer_profile["atom_count"] == 8
    assert layer_profile["passivant_atom_count"] == 0
    assert layer_profile["layers"][0]["atom_ids"] == ["Si1", "Si7"]
    assert layer_profile["layers"][0]["spacing_to_next_angstrom"] == 1.35775
    assert layer_profile["interlayer_spacing_stats_angstrom"]["count"] == 3
    assert lattice["is_slab"] is True
    assert lattice["surface_axis"] == "c"
    assert lattice["cell_volume_angstrom3"] == 737.394025
    assert lattice["declared_vacuum_fraction"] == 0.78276
    assert lattice["atom_extent_vacuum_fraction"] == 0.83707
    assert lattice["bottom_vacuum_angstrom"] == 0.0
    assert lattice["top_vacuum_angstrom"] == 20.92675
    assert lattice["vacuum_asymmetry_abs_angstrom"] == 20.92675
    assert lattice["centered_in_cell"] is False
    assert slab_vacuum["slab_center_offset_angstrom"] == -10.463375
    surface_model = semiconductor["surface_model_summary"]
    assert surface_model["status"] == "blocked"
    assert surface_model["ready_for_calculation_preflight"] is False
    assert surface_model["next_action"] == "center_slab_or_review_asymmetric_vacuum_before_claiming_normality"
    assert surface_model["slab_vacuum_status"] == "off_center"
    assert surface_model["surface_preparation_status"] == "dangling_bonds"
    assert surface_model["surface_polarity_status"] == "symmetric_nonpolar"
    assert surface_model["blocking_reasons"] == ["slab_vacuum:off_center", "surface_preparation:dangling_bonds"]

    centered, _ = apply_semantic_patch(
        slab,
        SemanticPatch(
            project_id=slab.project_id,
            base_revision=slab.revision,
            operations=[{"type": "center_slab", "axis": "z"}],
        ),
    )
    centered_audit = model_view_audit(centered)
    centered_vacuum = centered_audit["health"]["slab_vacuum"]
    centered_lattice = centered_audit["health"]["semiconductor_health"]["lattice_summary"]
    assert centered_vacuum["atom_fractional_min"] == 0.418535
    assert centered_vacuum["atom_fractional_max"] == 0.581465
    assert centered_vacuum["bottom_vacuum_angstrom"] == 10.463375
    assert centered_vacuum["top_vacuum_angstrom"] == 10.463375
    assert centered_vacuum["vacuum_asymmetry_abs_angstrom"] == 0.0
    assert centered_vacuum["slab_center_offset_angstrom"] == 0.0
    assert centered_vacuum["centered_in_cell"] is True
    assert centered_vacuum["slab_vacuum_status"] == "ready"
    assert centered_vacuum["slab_vacuum_next_action"] == "slab_vacuum_spacing_and_centering_ok"
    assert centered_lattice["centered_in_cell"] is True
    assert centered_lattice["slab_vacuum_status"] == "ready"
    centered_surface_model = centered_audit["health"]["semiconductor_health"]["surface_model_summary"]
    assert centered_surface_model["status"] == "blocked"
    assert centered_surface_model["next_action"] == "passivate_surface_dangling_bonds_before_calculation_or_claiming_normality"
    assert centered_surface_model["blocking_reasons"] == ["surface_preparation:dangling_bonds"]


def test_model_view_audit_does_not_treat_gate_stack_interfaces_as_surface_slabs(tmp_path: Path) -> None:
    cases = [
        (
            "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor_spec.json",
            "gate_stack_summary",
        ),
        (
            "silicon_silicon_dioxide_100_interface_spec.json",
            "interface_profile_summary",
        ),
        (
            "aluminum_silicon_100_schottky_contact_spec.json",
            "metal_semiconductor_contact_summary",
        ),
    ]
    for example_name, expected_summary in cases:
        spec = load_example(example_name)
        audit = model_view_audit(spec)
        semiconductor = audit["health"]["semiconductor_health"]

        assert semiconductor[expected_summary]
        assert audit["health"]["slab_vacuum"] is None
        assert semiconductor["surface_context"] is False
        assert semiconductor["lattice_summary"]["is_slab"] is False
        assert semiconductor["surface_model_summary"] is None
        assert semiconductor["surface_termination_summary"] is None
        assert semiconductor["surface_polarity_summary"] is None

        bundle = write_view_audit_bundle(tmp_path / spec.project_id, spec, audit)
        assert "semiconductor_surface_model_csv" not in bundle["files"]
        assert "semiconductor_surface_termination_csv" not in bundle["files"]
        assert "semiconductor_surface_polarity_csv" not in bundle["files"]


def test_semiconductor_oxide_interface_health_exports_layer_stoichiometry(
    tmp_path: Path,
) -> None:
    spec = load_example("silicon_silicon_dioxide_100_interface_spec.json")
    audit = model_view_audit(spec)
    semiconductor = audit["health"]["semiconductor_health"]
    summary = semiconductor["oxide_interface_health_summary"]
    geometry = semiconductor["oxide_interface_geometry_summary"]

    assert summary["model"] == "semiconductor_oxide_interface_health_preflight"
    assert summary["schema_version"] == 2
    assert summary["status"] == "geometry_relaxation_unverified"
    assert summary["quality"] == "complete"
    assert summary["semiconductor_material"] == "Si"
    assert summary["oxide_material"] == "SiO2"
    assert summary["metal_gate_present"] is False
    assert summary["material_sequence"] == ["Si", "SiO2"]
    assert summary["oxide_layer_count"] == 2
    assert summary["oxide_element_counts"] == {"O": 8, "Si": 4}
    assert summary["oxide_cation_elements"] == ["Si"]
    assert summary["oxide_cation_count"] == 4
    assert summary["oxygen_count"] == 8
    assert summary["oxygen_to_cation_ratio"] == 2.0
    assert summary["expected_oxygen_per_cation_ratio"] == 2.0
    assert summary["expected_oxygen_count"] == 8.0
    assert summary["oxygen_deficit_count"] == 0.0
    assert summary["stoichiometry_status"] == "matched"
    assert summary["oxygen_deficit_binding_status"] == "none_detected"
    assert summary["visual_preflight_ready"] is True
    assert summary["calculation_ready"] is False
    assert summary["semiconductor_oxide_boundary"]["axis_coordinate_angstrom"] == 8.58
    assert summary["geometry_preflight_status"] == "connected_geometry_preflight"
    assert summary["geometry_preflight_ready"] is True
    assert summary["geometry_boundary_neighbor_pair_count"] == 4
    assert summary["geometry_interface_spacing_count"] == 1
    assert summary["geometry_interface_spacing_mismatch_count"] == 0
    assert summary["geometry_interface_spacing_declared_values_match"] is True
    assert summary["geometry_short_contact_count"] == 0
    assert summary["normality_reason_codes"] == [
        "oxide_interface_geometry_relaxation_unverified"
    ]
    assert geometry["model"] == "semiconductor_oxide_interface_geometry_preflight"
    assert geometry["status"] == "connected_geometry_preflight"
    assert geometry["quality"] == "complete"
    assert geometry["atom_binding_complete"] is True
    assert geometry["boundary_candidate_pair_count"] == 12
    assert geometry["boundary_neighbor_pair_count"] == 4
    assert geometry["interface_spacing_count"] == 1
    assert geometry["interface_spacing_declared_count"] == 1
    assert geometry["interface_spacing_mismatch_count"] == 0
    assert geometry["interface_spacing_declared_values_match"] is True
    spacing = geometry["interface_spacings"][0]
    assert spacing["target_interface"] == "semiconductor_oxide"
    assert spacing["actual_gap_angstrom"] == 1.76
    assert spacing["declared_gap_angstrom"] == 1.76
    assert spacing["status"] == "matched"
    assert geometry["boundary_neighbor_pair_type_counts"] == {"Si-Si": 4}
    assert geometry["boundary_neighbor_distance_stats_angstrom"] == {
        "min": 2.604721,
        "max": 2.604721,
        "mean": 2.604721,
        "count": 4,
    }
    assert geometry["short_contact_count"] == 0
    assert geometry["isolated_oxide_atom_count"] == 0
    assert geometry["oxide_oxygen_with_cation_neighbor_count"] == 8
    assert geometry["oxide_cations_with_oxygen_neighbor_count"] == 4
    assert geometry["geometry_preflight_ready"] is True
    assert geometry["calculation_geometry_ready"] is False

    modeling_health = build_modeling_health(
        {"ok": True, "view_audit": audit},
        execution_mode="preview",
    )
    assert modeling_health["checks"]["semiconductor_oxide_interface_stoichiometry_status"] == "matched"
    assert modeling_health["checks"]["semiconductor_oxide_interface_spacing_mismatch_count"] == 0
    assert modeling_health["checks"]["semiconductor_oxide_interface_calculation_ready"] is False
    bundle = write_view_audit_bundle(
        tmp_path,
        spec,
        audit,
        modeling_health=modeling_health,
    )
    csv_path = Path(bundle["files"]["semiconductor_oxide_interface_health_csv"])
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert bundle["row_counts"]["semiconductor_oxide_interface_health"] == 3
    assert [row["row_kind"] for row in rows] == ["summary", "oxide_layer", "oxide_layer"]
    assert rows[0]["oxygen_count"] == "8"
    assert rows[0]["stoichiometry_status"] == "matched"
    assert rows[1]["element_counts"] == '{"O": 4, "Si": 2}'
    geometry_path = Path(bundle["files"]["semiconductor_oxide_interface_geometry_csv"])
    geometry_rows = list(
        csv.DictReader(geometry_path.open(encoding="utf-8", newline=""))
    )
    assert bundle["row_counts"]["semiconductor_oxide_interface_geometry"] == 26
    assert geometry_rows[0]["row_kind"] == "summary"
    assert geometry_rows[0]["boundary_neighbor_pair_count"] == "4"
    spacing_rows = [
        row for row in geometry_rows if row["row_kind"] == "interface_spacing"
    ]
    assert len(spacing_rows) == 1
    assert spacing_rows[0]["interface_spacing_status"] == "matched"
    assert spacing_rows[0]["actual_gap_angstrom"] == "1.76"
    assert spacing_rows[0]["declared_gap_angstrom"] == "1.76"
    assert sum(row["row_kind"] == "boundary_pair" for row in geometry_rows) == 12
    assert sum(row["row_kind"] == "oxide_atom" for row in geometry_rows) == 12

    metal_oxide = load_example("copper_silicon_dioxide_100_interface_spec.json")
    metal_health = model_view_audit(metal_oxide)["health"]["semiconductor_health"]
    assert metal_health["oxide_interface_health_summary"] is None
    assert metal_health["oxide_interface_geometry_summary"] is None


def test_semiconductor_oxide_geometry_binds_zero_index_boundary_layer() -> None:
    spec = load_example("silicon_silicon_dioxide_100_interface_spec.json")
    summary = _semiconductor_oxide_interface_geometry_summary(
        spec,
        {
            "structure_family": "semiconductor oxide interface",
            "semiconductor_oxide_interface": True,
            "semiconductor_channel_material": "Si",
            "oxide_material": "SiO2",
        },
        [
            {"id": "SiBoundary", "element": "Si", "fractional": [0.0, 0.0, 0.0]},
            {"id": "OBoundary", "element": "O", "fractional": [0.0, 0.0, 0.1]},
        ],
        [],
        [],
        {"axis": "c"},
        {
            "axis": "c",
            "interface": "Si/SiO2",
            "layers": [
                {
                    "layer_index": 0,
                    "material_group": "Si",
                    "atom_ids": ["SiBoundary"],
                    "element_counts": {"Si": 1},
                },
                {
                    "layer_index": 1,
                    "material_group": "SiO2",
                    "atom_ids": ["OBoundary"],
                    "element_counts": {"O": 1},
                },
            ],
            "transitions": [
                {
                    "from_layer_index": 0,
                    "to_layer_index": 1,
                    "from_material_group": "Si",
                    "to_material_group": "SiO2",
                    "boundary_coordinate_angstrom": 1.0,
                }
            ],
        },
    )

    assert summary is not None
    assert summary["atom_binding_complete"] is True
    assert summary["semiconductor_boundary_layer_index"] == 0
    assert summary["oxide_boundary_layer_index"] == 1
    assert summary["boundary_candidate_pair_count"] == 1


def test_semiconductor_oxide_interface_health_binds_recorded_oxygen_vacancy(
    tmp_path: Path,
) -> None:
    base = load_example("silicon_silicon_dioxide_100_interface_spec.json")
    oxygen = next(atom for atom in base.model.basis_atoms if atom.id == "O1")
    payload = base.model_dump(mode="json")
    payload["model"]["basis_atoms"] = [
        atom for atom in payload["model"]["basis_atoms"] if atom["id"] != "O1"
    ]
    payload["metadata"]["defects"] = [
        {
            "type": "vacancy",
            "site_id": "O1",
            "site_element": "O",
            "fractional": list(oxygen.fractional.as_tuple()),
            "source": "test_recorded_oxygen_vacancy",
        }
    ]
    vacancy_spec = ModelSpec.model_validate(payload)
    vacancy_semiconductor = model_view_audit(vacancy_spec)["health"][
        "semiconductor_health"
    ]
    summary = vacancy_semiconductor["oxide_interface_health_summary"]
    geometry = vacancy_semiconductor["oxide_interface_geometry_summary"]

    assert any(atom.id == "O1" for atom in base.model.basis_atoms)
    assert summary["status"] == "recorded_oxygen_vacancy_review"
    assert summary["quality"] == "complete_with_recorded_defect"
    assert summary["oxide_element_counts"] == {"O": 7, "Si": 4}
    assert summary["oxygen_to_cation_ratio"] == 1.75
    assert summary["oxygen_deficit_count"] == 1.0
    assert summary["stoichiometry_status"] == "oxygen_deficient"
    assert summary["oxygen_deficit_binding_status"] == "matched_recorded_oxygen_vacancies"
    assert summary["oxygen_deficit_explained_by_recorded_vacancies"] is True
    assert summary["recorded_oxygen_vacancy_site_ids"] == ["O1"]
    assert summary["all_recorded_oxygen_vacancy_locations_verified"] is True
    location = summary["oxygen_vacancy_locations"][0]
    assert location["region"] == "oxide"
    assert location["nearest_layer_index"] == 5
    assert location["nearest_layer_material"] == "SiO2"
    assert location["distance_to_semiconductor_oxide_boundary_angstrom"] == 0.88
    assert location["interface_proximal"] is True
    assert summary["normality_reason_codes"] == [
        "oxide_interface_recorded_oxygen_vacancy",
        "oxide_interface_geometry_relaxation_unverified",
    ]
    assert geometry["boundary_candidate_pair_count"] == 10
    assert geometry["boundary_neighbor_pair_count"] == 4
    assert geometry["oxide_atom_count"] == 11
    assert geometry["oxide_oxygen_atom_count"] == 7
    assert geometry["isolated_oxide_atom_count"] == 0
    assert geometry["geometry_preflight_ready"] is True

    vacancy_audit = model_view_audit(vacancy_spec)
    bundle = write_view_audit_bundle(tmp_path / "recorded", vacancy_spec, vacancy_audit)
    assert bundle["row_counts"]["semiconductor_oxide_interface_health"] == 4
    rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_oxide_interface_health_csv"]).open(
                encoding="utf-8",
                newline="",
            )
        )
    )
    assert rows[-1]["row_kind"] == "oxygen_vacancy"
    assert rows[-1]["vacancy_site_id"] == "O1"
    assert rows[-1]["vacancy_region"] == "oxide"
    assert bundle["row_counts"]["semiconductor_oxide_interface_geometry"] == 23

    unrecorded_payload = base.model_dump(mode="json")
    unrecorded_payload["model"]["basis_atoms"] = [
        atom for atom in unrecorded_payload["model"]["basis_atoms"] if atom["id"] != "O1"
    ]
    unrecorded = ModelSpec.model_validate(unrecorded_payload)
    unrecorded_summary = model_view_audit(unrecorded)["health"]["semiconductor_health"][
        "oxide_interface_health_summary"
    ]
    assert unrecorded_summary["quality"] == "review_required"
    assert unrecorded_summary["oxygen_deficit_binding_status"] == "unexplained_oxygen_deficit"
    assert unrecorded_summary["oxygen_deficit_explained_by_recorded_vacancies"] is False
    assert "oxide_interface_stoichiometry_review" in unrecorded_summary["normality_reason_codes"]

    invalid_formula = base.model_copy(
        update={
            "metadata": {
                **base.metadata,
                "oxide_material": "reviewed-oxide-marker",
            }
        }
    )
    invalid_summary = model_view_audit(invalid_formula)["health"]["semiconductor_health"][
        "oxide_interface_health_summary"
    ]
    assert invalid_summary["stoichiometry_status"] == "not_evaluated"
    assert invalid_summary["quality"] == "review_required"
    assert invalid_summary["visual_preflight_ready"] is False


def test_mos_gate_stack_reports_semiconductor_oxide_interface_health(tmp_path: Path) -> None:
    spec = load_example("titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json")
    semiconductor = model_view_audit(spec)["health"]["semiconductor_health"]
    summary = semiconductor["oxide_interface_health_summary"]
    geometry = semiconductor["oxide_interface_geometry_summary"]

    assert semiconductor["gate_stack_summary"]["quality"] == "complete"
    assert summary["metal_gate_present"] is True
    assert summary["semiconductor_material"] == "Si"
    assert summary["oxide_material"] == "HfO2"
    assert summary["stoichiometry_status"] == "matched"
    assert summary["oxygen_to_cation_ratio"] == 2.0
    assert summary["visual_preflight_ready"] is False
    assert summary["calculation_ready"] is False
    assert geometry["status"] == "declared_interface_spacing_mismatch"
    assert geometry["quality"] == "review_required"
    assert geometry["boundary_candidate_pair_count"] == 12
    assert geometry["boundary_neighbor_pair_count"] == 4
    assert geometry["interface_spacing_count"] == 2
    assert geometry["interface_spacing_mismatch_count"] == 1
    assert geometry["interface_spacing_declared_values_match"] is False
    semiconductor_oxide_spacing = geometry["interface_spacings"][0]
    assert semiconductor_oxide_spacing["target_interface"] == "semiconductor_oxide"
    assert semiconductor_oxide_spacing["actual_gap_angstrom"] == 2.24
    assert semiconductor_oxide_spacing["declared_gap_angstrom"] == 1.76
    assert semiconductor_oxide_spacing["actual_minus_declared_angstrom"] == 0.48
    assert semiconductor_oxide_spacing["patch_operation"] == {
        "type": "set_gate_stack_interface_gap",
        "target_interface": "semiconductor_oxide",
        "thickness_angstrom": 1.76,
    }
    assert geometry["short_contact_count"] == 8
    assert geometry["short_contact_scope_counts"] == {"oxide_internal": 8}
    assert geometry["geometry_preflight_ready"] is False
    assert "oxide_interface_declared_spacing_mismatch" in summary["normality_reason_codes"]
    assert "oxide_interface_short_contact_review" in summary["normality_reason_codes"]

    audit = model_view_audit(spec)
    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_oxide_interface_geometry_csv"]).open(
                encoding="utf-8",
                newline="",
            )
        )
    )
    internal_short_contacts = [
        row for row in rows if row["row_kind"] == "oxide_internal_short_contact"
    ]
    assert len(internal_short_contacts) == 8
    assert all(row["pair_scope"] == "oxide_internal" for row in internal_short_contacts)
    assert all(row["atom1_id"] and row["atom2_id"] for row in internal_short_contacts)
    assert all(not row["semiconductor_atom_id"] for row in internal_short_contacts)
    spacing_rows = [row for row in rows if row["row_kind"] == "interface_spacing"]
    assert len(spacing_rows) == 2
    assert spacing_rows[0]["interface_spacing_status"] == "mismatch"
    assert json.loads(spacing_rows[0]["patch_operation"])["type"] == (
        "set_gate_stack_interface_gap"
    )


def test_gate_stack_interface_spacing_rejects_invalid_declaration_and_infers_gate_from_sequence() -> None:
    spec = load_example("titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json")
    metadata = dict(spec.metadata or {})
    metadata.pop("gate_material")
    metadata["interface_gap_angstrom"] = "invalid"
    invalid = spec.model_copy(update={"metadata": metadata})

    geometry = model_view_audit(invalid)["health"]["semiconductor_health"][
        "oxide_interface_geometry_summary"
    ]

    assert geometry["status"] == "interface_spacing_binding_review"
    assert geometry["interface_spacing_binding_review_count"] == 1
    assert geometry["interface_spacing_mismatch_count"] == 0
    assert geometry["interface_spacing_count"] == 2
    semiconductor_oxide = geometry["interface_spacings"][0]
    assert semiconductor_oxide["declared_gap_status"] == "invalid"
    assert semiconductor_oxide["binding_status"] == "declared_value_invalid"
    assert semiconductor_oxide["actual_gap_angstrom"] == 2.24
    assert semiconductor_oxide["patch_operation"] is None
    oxide_gate = geometry["interface_spacings"][1]
    assert oxide_gate["expected_materials"] == ["HfO2", "TiN"]
    assert oxide_gate["status"] == "not_declared"


def test_model_view_audit_reports_group_iv_dopant_summary() -> None:
    base = load_example("silicon_diamond_spec.json")
    p_doped, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {"type": "make_supercell", "matrix": [2, 1, 1]},
                {"type": "substitute_atom", "atom_id": "Si1_000", "new_element": "P"},
                {
                    "type": "set_metadata",
                    "metadata_updates": {
                        "semiconductor_carrier_intents": [
                            {
                                "carrier_type": "n_type",
                                "dopant_element": "P",
                                "source": "test",
                            }
                        ],
                        "last_semiconductor_carrier_intent": {
                            "carrier_type": "n_type",
                            "dopant_element": "P",
                            "source": "test",
                        },
                    },
                },
            ],
        ),
    )
    p_health = model_view_audit(p_doped)["health"]["semiconductor_health"]
    p_summary = p_health["dopant_summary"]

    assert p_health["rule"] == "doped_group_iv_tetrahedral"
    assert p_health["host_elements"] == ["Si"]
    assert p_health["dopant_elements"] == ["P"]
    assert p_summary["total_dopant_count"] == 1
    assert p_summary["total_dopant_fraction"] == 0.0625
    assert p_summary["dopants"][0]["role_hint"] == "donor_like_n_type_for_group_iv_host"
    assert p_summary["dopants"][0]["coordination_stats"]["min"] == 4.0
    assert p_summary["dopants"][0]["neighbor_element_counts"] == {"Si": 4}
    composition = p_health["composition_summary"]
    assert composition["formula"] == "PSi15"
    assert composition["reduced_formula"] == "PSi15"
    assert composition["total_atom_count"] == 16
    assert composition["element_counts"] == {"P": 1, "Si": 15}
    assert {item["element"]: item["role"] for item in composition["elements"]} == {"P": "dopant", "Si": "host"}
    charge_balance = p_health["charge_balance_summary"]
    assert charge_balance["total_valence_electron_count"] == 65
    assert charge_balance["electron_count_parity"] == "odd"
    assert charge_balance["odd_electron_warning"] is True
    assert charge_balance["spin_charge_review_required"] is True
    assert charge_balance["spin_polarization_review_required"] is True
    assert charge_balance["recommended_spin_treatment"] == "review_spin_polarized_calculation_or_explicit_charge_state"
    assert charge_balance["next_action"] == "review_spin_polarization_or_charge_state_before_castep_execution"
    assert charge_balance["nominal_dopant_delta_electrons"] == 1
    assert charge_balance["carrier_type_hint"] == "donor_like_n_type"
    assert charge_balance["valence_electrons_per_non_passivant_atom"] == 4.0625
    finite_size = p_health["finite_size_summary"]
    assert finite_size["model"] == "isolated_dopant_defect_finite_size_heuristic"
    assert finite_size["non_passivant_atom_count"] == 16
    assert finite_size["max_isolated_fraction"] == 0.0625
    assert finite_size["max_isolated_item"]["kind"] == "dopant"
    assert finite_size["small_cell_warning"] is True
    assert finite_size["high_concentration_warning"] is True
    assert finite_size["finite_size_warning"] is True
    concentration = p_health["dopant_concentration_summary"]
    assert concentration["model"] == "periodic_supercell_equivalent_dopant_concentration"
    assert concentration["total_dopant_density_cm3"] > 3.0e21
    assert concentration["net_nominal_carrier_density_cm3_abs"] == concentration["total_dopant_density_cm3"]
    assert concentration["concentration_warning_level"] == "very_high"
    assert concentration["degenerate_doping_review_required"] is True
    assert concentration["next_action"] == (
        "increase_supercell_or_reduce_dopant_count_before_quantitative_semiconductor_claims"
    )
    assert concentration["dopants"][0]["element"] == "P"
    assert concentration["dopants"][0]["carrier_type_hint"] == "donor_like_n_type"
    carrier_intent = p_health["carrier_intent_summary"]
    assert carrier_intent["requested_carrier_type"] == "n_type"
    assert carrier_intent["requested_dopant_element"] == "P"
    assert carrier_intent["actual_carrier_type"] == "n_type"
    assert carrier_intent["actual_carrier_type_hint"] == "donor_like_n_type"
    assert carrier_intent["actual_dopant_elements"] == ["P"]
    assert carrier_intent["latest_matches"] is True
    assert carrier_intent["latest"]["actual_dopant_fraction"] == 0.0625
    p_modeling_health = build_modeling_health({"ok": True, "view_audit": model_view_audit(p_doped)}, execution_mode="preview")
    assert p_modeling_health["checks"]["semiconductor_carrier_intent_latest_matches"] is True
    assert p_modeling_health["checks"]["semiconductor_requested_carrier_type"] == "n_type"
    assert p_modeling_health["checks"]["semiconductor_actual_carrier_type"] == "n_type"
    assert p_modeling_health["checks"]["semiconductor_finite_size_warning"] is True
    assert p_modeling_health["checks"]["semiconductor_total_dopant_density_cm3"] > 3.0e21
    assert p_modeling_health["checks"]["semiconductor_dopant_concentration_warning_level"] == "very_high"
    assert p_modeling_health["checks"]["semiconductor_degenerate_doping_review_required"] is True
    assert any("finite-size/dilution" in warning for warning in p_modeling_health["warnings"])
    assert any("dopant concentration" in warning for warning in p_modeling_health["warnings"])

    b_doped, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[{"type": "substitute_atom", "atom_id": "Si1", "new_element": "B"}],
        ),
    )
    b_dopant = model_view_audit(b_doped)["health"]["semiconductor_health"]["dopant_summary"]["dopants"][0]
    assert b_dopant["element"] == "B"
    assert b_dopant["role_hint"] == "acceptor_like_p_type_for_group_iv_host"


def test_model_view_audit_warns_when_carrier_intent_mismatches_actual_dopant() -> None:
    base = load_example("silicon_diamond_spec.json")
    mismatched, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {"type": "substitute_atom", "atom_id": "Si1", "new_element": "B"},
                {
                    "type": "set_metadata",
                    "metadata_updates": {
                        "semiconductor_carrier_intents": [
                            {
                                "carrier_type": "n_type",
                                "dopant_element": "P",
                                "source": "test",
                            }
                        ],
                        "last_semiconductor_carrier_intent": {
                            "carrier_type": "n_type",
                            "dopant_element": "P",
                            "source": "test",
                        },
                    },
                },
            ],
        ),
    )

    audit = model_view_audit(mismatched)
    carrier_intent = audit["health"]["semiconductor_health"]["carrier_intent_summary"]

    assert carrier_intent["requested_carrier_type"] == "n_type"
    assert carrier_intent["actual_carrier_type"] == "p_type"
    assert carrier_intent["latest_matches"] is False
    assert carrier_intent["warning_count"] == 1
    assert "does not match" in carrier_intent["warnings"][0]
    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert modeling_health["checks"]["semiconductor_carrier_intent_latest_matches"] is False
    assert modeling_health["checks"]["semiconductor_carrier_intent_warning_count"] == 1
    assert any("carrier intent" in warning for warning in modeling_health["warnings"])


def test_infer_modeling_plan_maps_chinese_dopant_concentration_phrases() -> None:
    base = load_example("silicon_diamond_spec.json")

    concentration_plan = infer_modeling_plan(
        "\u63ba\u6742\u6d53\u5ea6\u4e3a 6.25\uff05 P",
        current_spec=base,
    )
    assert concentration_plan.kind == "patch"
    assert concentration_plan.template_id == "crystal_dopant_fraction"
    concentration_metadata = concentration_plan.payload["operations"][-1]["metadata_updates"]
    concentration_record = concentration_metadata["last_applied_dopant_fraction"]
    assert concentration_record["host_element"] == "Si"
    assert concentration_record["dopant_element"] == "P"
    assert concentration_record["requested_fraction"] == 0.0625

    dopant_first_plan = infer_modeling_plan(
        "P \u63ba\u6742\u6d53\u5ea6 6.25%",
        current_spec=base,
    )
    assert dopant_first_plan.kind == "patch"
    assert dopant_first_plan.template_id == "crystal_dopant_fraction"
    dopant_first_record = dopant_first_plan.payload["operations"][-1]["metadata_updates"]["last_applied_dopant_fraction"]
    assert dopant_first_record["dopant_element"] == "P"
    assert dopant_first_record["requested_percent"] == 6.25

    chinese_host_plan = infer_modeling_plan(
        "\u5728\u7845\u4e2d\u63ba\u6742 6.25\uff05 P",
        current_spec=base,
    )
    chinese_host_record = chinese_host_plan.payload["operations"][-1]["metadata_updates"]["last_applied_dopant_fraction"]
    assert chinese_host_record["host_element"] == "Si"
    assert chinese_host_record["dopant_element"] == "P"


def test_infer_modeling_plan_maps_chinese_alloy_fraction_phrases() -> None:
    base = load_example("silicon_diamond_spec.json")

    alloy_percent_plan = infer_modeling_plan(
        "\u9517\u5408\u91d1\u6bd4\u4f8b\u4e3a 25\uff05",
        current_spec=base,
    )
    assert alloy_percent_plan.kind == "patch"
    assert alloy_percent_plan.template_id == "crystal_alloy_fraction"
    alloy_percent_record = alloy_percent_plan.payload["operations"][-1]["metadata_updates"]["last_applied_alloy"]
    assert alloy_percent_record["host_element"] == "Si"
    assert alloy_percent_record["alloy_element"] == "Ge"
    assert alloy_percent_record["requested_fraction"] == 0.25

    alloy_first_plan = infer_modeling_plan(
        "Ge \u5408\u91d1\u6bd4\u4f8b 25%",
        current_spec=base,
    )
    assert alloy_first_plan.kind == "patch"
    assert alloy_first_plan.template_id == "crystal_alloy_fraction"
    alloy_first_record = alloy_first_plan.payload["operations"][-1]["metadata_updates"]["last_applied_alloy"]
    assert alloy_first_record["alloy_element"] == "Ge"
    assert alloy_first_record["requested_percent"] == 25.0

    chinese_host_plan = infer_modeling_plan(
        "\u7845\u4e2d\u52a0\u5165 25\uff05 \u9517\u5f62\u6210\u5408\u91d1",
        current_spec=base,
    )
    chinese_host_record = chinese_host_plan.payload["operations"][-1]["metadata_updates"]["last_applied_alloy"]
    assert chinese_host_record["host_element"] == "Si"
    assert chinese_host_record["alloy_element"] == "Ge"


def test_infer_modeling_plan_maps_chinese_strain_phrases() -> None:
    base = load_example("silicon_diamond_spec.json")

    biaxial_plan = infer_modeling_plan(
        "\u9762\u5185\u62c9\u4f38 2\uff05 \u5e94\u53d8",
        current_spec=base,
    )
    assert biaxial_plan.kind == "patch"
    assert biaxial_plan.template_id == "crystal_strain"
    biaxial_record = biaxial_plan.payload["operations"][-1]["metadata_updates"]["last_applied_strain"]
    assert biaxial_record["axes"] == ["a", "b"]
    assert biaxial_record["percent"] == 2.0
    assert biaxial_record["mode"] == "biaxial_tensile"

    uniaxial_plan = infer_modeling_plan(
        "\u5bf9c\u8f74\u52a0 -3\uff05 \u5e94\u53d8",
        current_spec=base,
    )
    assert uniaxial_plan.kind == "patch"
    assert uniaxial_plan.template_id == "crystal_strain"
    uniaxial_record = uniaxial_plan.payload["operations"][-1]["metadata_updates"]["last_applied_strain"]
    assert uniaxial_record["axes"] == ["c"]
    assert uniaxial_record["percent"] == -3.0
    assert uniaxial_record["mode"] == "uniaxial_compressive"


def test_infer_modeling_plan_maps_explicit_lattice_parameter_phrases() -> None:
    base = load_example("silicon_diamond_spec.json")

    english_plan = infer_modeling_plan(
        "Set lattice parameters a and b to 5.45 angstrom and gamma=91 degrees.",
        current_spec=base,
    )
    assert english_plan.kind == "patch"
    assert english_plan.template_id == "crystal_lattice_parameters"
    english_lattice = english_plan.payload["operations"][0]["lattice"]
    assert english_lattice == {
        "a": 5.45,
        "b": 5.45,
        "c": 5.431,
        "alpha": 90.0,
        "beta": 90.0,
        "gamma": 91.0,
    }
    record = english_plan.payload["operations"][1]["metadata_updates"]["last_lattice_parameter_edit"]
    assert record["changed_fields"] == ["a", "b", "gamma"]
    assert record["fractional_coordinates_preserved"] is True
    assert record["source"] == "natural_language_crystal_lattice_parameters"

    chinese_plan = infer_modeling_plan(
        "\u628a\u6676\u683c\u53c2\u6570 a \u548c b \u8bbe\u4e3a 0.32 nm\uff0cc \u6539\u4e3a 5.2 \u57c3",
        current_spec=base,
    )
    assert chinese_plan.kind == "patch"
    assert chinese_plan.template_id == "crystal_lattice_parameters"
    chinese_lattice = chinese_plan.payload["operations"][0]["lattice"]
    assert chinese_lattice["a"] == 3.2
    assert chinese_lattice["b"] == 3.2
    assert chinese_lattice["c"] == 5.2

    explicit_nm_plan = infer_modeling_plan(
        "Set lattice parameters a=b=0.32 nm and c=0.52 nm.",
        current_spec=base,
    )
    explicit_nm_lattice = explicit_nm_plan.payload["operations"][0]["lattice"]
    assert explicit_nm_lattice["a"] == 3.2
    assert explicit_nm_lattice["b"] == 3.2
    assert explicit_nm_lattice["c"] == 5.2

    unrelated_nm_plan = infer_modeling_plan(
        "Set lattice constant a=3.2; the existing vacuum note is 1 nm.",
        current_spec=base,
    )
    assert unrelated_nm_plan.payload["operations"][0]["lattice"]["a"] == 3.2

    no_op_plan = infer_modeling_plan(
        "Set lattice constants a=b=c=5.431 angstrom.",
        current_spec=base,
    )
    assert no_op_plan.kind == "unsupported"
    assert no_op_plan.template_id == "crystal_lattice_parameters"
    assert "already match" in " ".join(no_op_plan.notes)

    no_op_composite_plan = infer_modeling_plan(
        "Set lattice constant a to 5.431 angstrom and CASTEP cutoff to 600 eV.",
        current_spec=base,
    )
    assert no_op_composite_plan.kind == "unsupported"
    assert no_op_composite_plan.template_id == "crystal_composite_edit"
    assert "already match" in " ".join(no_op_composite_plan.notes)

    assert infer_modeling_plan("Apply 2% strain along c.", current_spec=base).template_id == "crystal_strain"
    assert infer_modeling_plan("Set vacuum to 10 angstrom.", current_spec=base).template_id == "crystal_vacuum"


def test_infer_modeling_plan_maps_chinese_gate_stack_thickness_phrases() -> None:
    base = load_example("titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json")

    cases = [
        ("\u628a HfO2 \u539a\u5ea6\u6539\u4e3a 6 \u57c3", "oxide", 6.0),
        ("\u6805\u6c27\u539a\u5ea6\u8bbe\u4e3a 5 \u212b", "oxide", 5.0),
        ("\u628a\u6c27\u5316\u5c42\u539a\u5ea6\u8c03\u5230 6\u57c3", "oxide", 6.0),
        ("\u6c9f\u9053\u539a\u5ea6\u8bbe\u4e3a 8 \u57c3", "channel", 8.0),
        ("\u91d1\u5c5e\u6805\u539a\u5ea6 2 \u57c3", "gate", 2.0),
    ]

    for request, target_layer, thickness in cases:
        plan = infer_modeling_plan(request, current_spec=base)
        assert plan.kind == "patch"
        assert plan.template_id == "gate_stack_thickness"
        operation = plan.payload["operations"][0]
        assert operation["type"] == "set_gate_stack_thickness"
        assert operation["target_layer"] == target_layer
        assert operation["thickness_angstrom"] == thickness


def test_model_view_audit_reports_iii_v_dopant_site_role_and_carrier_intent() -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan("Make n-type GaAs with Si_Ga dopant.", current_spec=base)
    assert plan.template_id == "crystal_sublattice_dopant"
    doped, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    audit = model_view_audit(doped)
    semiconductor = audit["health"]["semiconductor_health"]
    site_summary = semiconductor["dopant_site_summary"]
    carrier_intent = semiconductor["carrier_intent_summary"]

    assert semiconductor["rule"] == "doped_iii_v_tetrahedral"
    assert site_summary["site_count"] == 1
    assert site_summary["carrier_type_hint"] == "donor_like_n_type"
    assert site_summary["latest"]["site_id"] == "Ga1"
    assert site_summary["latest"]["site_element"] == "Ga"
    assert site_summary["latest"]["dopant_element"] == "Si"
    assert site_summary["latest"]["auto_selected_site"] is True
    assert site_summary["latest"]["role_hint"] == "donor_like_n_type_on_iii_v_cation_site"
    assert carrier_intent["requested_carrier_type"] == "n_type"
    assert carrier_intent["actual_carrier_type"] == "n_type"
    assert carrier_intent["latest_matches"] is True
    assert semiconductor["charge_balance_summary"]["carrier_type_hint"] == "donor_like_n_type"
    assert semiconductor["charge_balance_summary"]["carrier_type_hint_source"] == "dopant_site_summary"
    assert semiconductor["charge_balance_summary"]["nominal_dopant_delta_electrons"] == 1
    modeling_health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert modeling_health["checks"]["semiconductor_dopant_site_carrier_type_hint"] == "donor_like_n_type"
    assert modeling_health["checks"]["semiconductor_dopant_site_donor_like_count"] == 1
    assert modeling_health["checks"]["semiconductor_carrier_intent_latest_matches"] is True

    sublattice_plan = infer_modeling_plan("Dope Ga sublattice with Si.", current_spec=base)
    assert sublattice_plan.template_id == "crystal_sublattice_dopant"

    acceptor_plan = infer_modeling_plan("Make p-type GaAs with Si on As site.", current_spec=base)
    assert acceptor_plan.template_id == "crystal_sublattice_dopant"
    acceptor_doped, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=acceptor_plan.payload["operations"],
        ),
    )
    acceptor = model_view_audit(acceptor_doped)["health"]["semiconductor_health"]
    assert acceptor["dopant_site_summary"]["carrier_type_hint"] == "acceptor_like_p_type"
    assert acceptor["dopant_site_summary"]["latest"]["site_id"] == "As1"
    assert acceptor["dopant_site_summary"]["latest"]["site_element"] == "As"
    assert acceptor["dopant_site_summary"]["latest"]["role_hint"] == "acceptor_like_p_type_on_iii_v_anion_site"
    assert acceptor["carrier_intent_summary"]["latest_matches"] is True

    gan = load_example("gallium_nitride_wurtzite_spec.json")
    mg_plan = infer_modeling_plan("Dope with Mg.", current_spec=gan)
    assert mg_plan.template_id == "crystal_auto_dopant"
    mg_doped, _ = apply_semantic_patch(
        gan,
        SemanticPatch(
            project_id=gan.project_id,
            base_revision=gan.revision,
            operations=mg_plan.payload["operations"],
        ),
    )
    mg_health = model_view_audit(mg_doped)["health"]["semiconductor_health"]
    mg_site = mg_health["dopant_site_summary"]
    mg_charge = mg_health["charge_balance_summary"]

    assert mg_health["rule"] == "doped_iii_v_tetrahedral"
    assert mg_site["carrier_type_hint"] == "acceptor_like_p_type"
    assert mg_site["latest"]["site_element"] == "Ga"
    assert mg_site["latest"]["dopant_element"] == "Mg"
    assert mg_site["latest"]["role_hint"] == "acceptor_like_p_type_on_iii_v_cation_site"
    assert mg_charge["carrier_type_hint"] == "acceptor_like_p_type"
    assert mg_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert mg_charge["nominal_dopant_delta_electrons"] == -1
    assert mg_charge["site_adjusted_dopant_delta_electrons"] == -1
    assert mg_site["warning_count"] == 0


def test_model_view_audit_reports_ii_vi_dopant_site_role_and_site_adjusted_charge() -> None:
    zno = load_example("zinc_oxide_wurtzite_spec.json")
    al_plan = infer_modeling_plan("Dope with Al.", current_spec=zno)
    assert al_plan.template_id == "crystal_auto_dopant"
    al_doped, _ = apply_semantic_patch(
        zno,
        SemanticPatch(
            project_id=zno.project_id,
            base_revision=zno.revision,
            operations=al_plan.payload["operations"],
        ),
    )
    al_health = model_view_audit(al_doped)["health"]["semiconductor_health"]
    al_site = al_health["dopant_site_summary"]
    al_charge = al_health["charge_balance_summary"]

    assert al_health["rule"] == "doped_ii_vi_tetrahedral"
    assert al_health["dopant_summary"]["host_elements"] == ["O", "Zn"]
    assert al_health["dopant_summary"]["dopant_elements"] == ["Al"]
    assert al_site["carrier_type_hint"] == "donor_like_n_type"
    assert al_site["latest"]["site_element"] == "Zn"
    assert al_site["latest"]["dopant_element"] == "Al"
    assert al_site["latest"]["role_hint"] == "donor_like_n_type_on_ii_vi_cation_site"
    assert al_charge["carrier_type_hint"] == "donor_like_n_type"
    assert al_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert al_charge["nominal_dopant_delta_electrons"] == 1
    assert al_charge["average_host_nominal_dopant_delta_electrons"] == -1
    assert al_charge["site_adjusted_dopant_delta_electrons"] == 1

    n_plan = infer_modeling_plan("Dope O sublattice with N.", current_spec=zno)
    assert n_plan.template_id == "crystal_sublattice_dopant"
    n_doped, _ = apply_semantic_patch(
        zno,
        SemanticPatch(
            project_id=zno.project_id,
            base_revision=zno.revision,
            operations=n_plan.payload["operations"],
        ),
    )
    n_health = model_view_audit(n_doped)["health"]["semiconductor_health"]
    n_site = n_health["dopant_site_summary"]
    n_charge = n_health["charge_balance_summary"]

    assert n_health["dopant_summary"]["host_elements"] == ["O", "Zn"]
    assert n_health["dopant_summary"]["dopant_elements"] == ["N"]
    assert n_site["carrier_type_hint"] == "acceptor_like_p_type"
    assert n_site["latest"]["site_element"] == "O"
    assert n_site["latest"]["dopant_element"] == "N"
    assert n_site["latest"]["role_hint"] == "acceptor_like_p_type_on_ii_vi_anion_site"
    assert n_charge["carrier_type_hint"] == "acceptor_like_p_type"
    assert n_charge["nominal_dopant_delta_electrons"] == -1
    assert n_charge["average_host_nominal_dopant_delta_electrons"] == 1
    assert n_charge["site_adjusted_dopant_delta_electrons"] == -1

    cdte = load_example("cadmium_telluride_zincblende_spec.json")
    cl_plan = infer_modeling_plan("Dope Te sublattice with Cl.", current_spec=cdte)
    assert cl_plan.template_id == "crystal_sublattice_dopant"
    cl_doped, _ = apply_semantic_patch(
        cdte,
        SemanticPatch(
            project_id=cdte.project_id,
            base_revision=cdte.revision,
            operations=cl_plan.payload["operations"],
        ),
    )
    cl_health = model_view_audit(cl_doped)["health"]["semiconductor_health"]
    cl_site = cl_health["dopant_site_summary"]
    cl_charge = cl_health["charge_balance_summary"]

    assert cl_health["dopant_summary"]["host_elements"] == ["Cd", "Te"]
    assert cl_health["dopant_summary"]["dopant_elements"] == ["Cl"]
    assert cl_site["carrier_type_hint"] == "donor_like_n_type"
    assert cl_site["latest"]["site_element"] == "Te"
    assert cl_site["latest"]["dopant_element"] == "Cl"
    assert cl_site["latest"]["role_hint"] == "donor_like_n_type_on_ii_vi_anion_site"
    assert cl_charge["carrier_type_hint"] == "donor_like_n_type"
    assert cl_charge["carrier_type_hint_source"] == "dopant_site_summary"
    assert cl_charge["nominal_dopant_delta_electrons"] == 1
    assert cl_charge["average_host_nominal_dopant_delta_electrons"] == 3
    assert cl_charge["site_adjusted_dopant_delta_electrons"] == 1


def test_model_view_audit_reports_semiconductor_calculation_preflight_warnings() -> None:
    spec = load_example("silicon_diamond_spec.json")
    assert spec.simulation is not None
    simulation = spec.simulation.model_copy(update={"cutoff_energy_ev": 250, "kpoint_separation": 0.12})
    bad_spec = spec.model_copy(update={"simulation": simulation})

    audit = model_view_audit(bad_spec)
    calculation = audit["health"]["semiconductor_health"]["calculation_preflight_summary"]
    reciprocal = audit["health"]["semiconductor_health"]["reciprocal_lattice_summary"]

    assert calculation["module"] == "CASTEP"
    assert calculation["cutoff_status"] == "low"
    assert calculation["kpoint_mode"] == "separation"
    assert calculation["ready_for_energy_preflight"] is False
    assert calculation["warning_count"] == 2
    assert any("cutoff_energy_ev=250" in warning for warning in calculation["warnings"])
    assert any("kpoint_separation=0.12" in warning for warning in calculation["warnings"])
    assert reciprocal["status"] == "warnings"
    assert reciprocal["estimated_kpoints_from_separation"] == [10, 10, 10]
    assert reciprocal["recommended_kpoints"] == [15, 15, 15]
    assert reciprocal["recommendation_reason_codes"] == [
        "replace_coarse_kpoint_separation_with_explicit_grid"
    ]
    assert reciprocal["warning_count"] == 1
    assert any("kpoint_separation=0.12" in warning for warning in reciprocal["warnings"])

    health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert health["verdict"] == "ready_with_warnings"
    assert health["checks"]["semiconductor_calculation_cutoff_status"] == "low"
    assert health["checks"]["semiconductor_calculation_warning_count"] == 2
    assert health["checks"]["semiconductor_reciprocal_status"] == "warnings"
    assert health["checks"]["semiconductor_reciprocal_estimated_kpoints"] == [10, 10, 10]
    assert health["checks"]["semiconductor_reciprocal_recommended_kpoints"] == [15, 15, 15]
    assert health["checks"]["semiconductor_reciprocal_warning_count"] == 1
    assert any("calculation preflight" in warning for warning in health["warnings"])
    assert any("reciprocal-lattice" in warning for warning in health["warnings"])


def test_slab_kpoint_recommendation_clamps_surface_normal_and_clears_warning() -> None:
    spec = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    audit = model_view_audit(spec)
    reciprocal = audit["health"]["semiconductor_health"]["reciprocal_lattice_summary"]

    assert reciprocal["status"] == "warnings"
    assert reciprocal["slab_axis"] == "c"
    assert reciprocal["estimated_kpoints_from_separation"] == [29, 29, 8]
    assert reciprocal["recommended_kpoint_mode"] == "explicit_grid"
    assert reciprocal["recommended_kpoints"] == [29, 29, 1]
    assert reciprocal["recommendation_reason_codes"] == [
        "replace_slab_kpoint_separation_with_explicit_grid",
        "set_slab_surface_normal_kpoint_to_one",
    ]
    assert [row["recommended_kpoint"] for row in reciprocal["axes"]] == [29, 29, 1]

    assert spec.simulation is not None
    fixed_simulation = spec.simulation.model_copy(
        update={"kpoint_separation": None, "kpoints": (29, 29, 1)}
    )
    fixed_spec = ModelSpec.model_validate(
        spec.model_copy(update={"simulation": fixed_simulation}).model_dump(mode="json")
    )
    fixed_reciprocal = model_view_audit(fixed_spec)["health"]["semiconductor_health"][
        "reciprocal_lattice_summary"
    ]

    assert fixed_reciprocal["status"] == "ok"
    assert fixed_reciprocal["explicit_kpoints"] == [29, 29, 1]
    assert fixed_reciprocal["actual_separations_1_per_angstrom"] == [
        0.039585,
        0.039585,
        0.285599,
    ]
    assert fixed_reciprocal["recommended_kpoints"] is None
    assert fixed_reciprocal["recommendation_reason_codes"] == []
    assert fixed_reciprocal["warning_count"] == 0


def test_recommended_kpoint_natural_language_requires_explicit_apply_intent() -> None:
    spec = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    requests = (
        "Apply the recommended k-point grid.",
        "Use the suggested k point settings.",
        "\u5e94\u7528\u63a8\u8350\u7684 k \u70b9\u7f51\u683c\u3002",
        "\u91c7\u7528\u5efa\u8bae\u7684k\u70b9\u8bbe\u7f6e\u5e76\u91cd\u65b0\u68c0\u67e5\u3002",
    )

    for request in requests:
        plan = infer_modeling_plan(request, current_spec=spec)
        assert plan.kind == "apply_recommended_kpoint_grid"
        assert plan.template_id == "apply_recommended_semiconductor_kpoint_grid"
        assert plan.payload == {
            "project_id": spec.project_id,
            "revision": spec.revision,
            "action_id": "apply_recommended_semiconductor_kpoint_grid",
            "requires_explicit_confirmation": True,
        }

    question = infer_modeling_plan(
        "What is the recommended k-point grid?",
        current_spec=spec,
    )
    assert question.kind != "apply_recommended_kpoint_grid"


def test_model_view_audit_classifies_semiconductor_castep_property_tasks() -> None:
    spec = load_example("silicon_diamond_spec.json")
    assert spec.simulation is not None
    simulation = spec.simulation.model_copy(update={"task": CastepTask.BAND_STRUCTURE})
    band_spec = spec.model_copy(update={"simulation": simulation})

    audit = model_view_audit(band_spec)
    calculation = audit["health"]["semiconductor_health"]["calculation_preflight_summary"]

    assert calculation["task"] == "BandStructure"
    assert calculation["task_family"] == "property"
    assert calculation["task_intent"] == "band_structure"
    assert calculation["requires_prior_relaxed_structure"] is True
    assert calculation["settings_review_required"] is True
    assert calculation["execution_risk"] == "high"
    assert calculation["ready_for_energy_preflight"] is False
    assert calculation["ready_for_requested_task_preflight"] is False
    assert calculation["next_action"] == "review_property_task_settings_and_prior_relaxation"
    assert any("relaxed structure" in warning for warning in calculation["warnings"])

    health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert health["checks"]["semiconductor_calculation_task_family"] == "property"
    assert health["checks"]["semiconductor_calculation_task_intent"] == "band_structure"
    assert health["checks"]["semiconductor_calculation_requires_prior_relaxed_structure"] is True
    assert health["checks"]["semiconductor_calculation_execution_risk"] == "high"
    assert health["checks"]["semiconductor_band_path_available"] is True
    assert health["checks"]["semiconductor_band_path_bravais_lattice"] == "fcc"
    assert health["checks"]["semiconductor_band_path_task_relevant"] is True
    assert health["checks"]["semiconductor_band_path_point_count"] == 10


def test_infer_modeling_plan_updates_castep_settings_patch() -> None:
    base = load_example("silicon_diamond_spec.json")

    plan = infer_modeling_plan(
        "Set CASTEP cutoff to 600 eV and kpoint separation 0.03 for band structure.",
        current_spec=base,
    )

    assert plan.kind == "patch"
    assert plan.template_id == "castep_settings"
    operation = plan.payload["operations"][0]
    assert operation["type"] == "set_castep_energy"
    assert operation["task"] == "BandStructure"
    assert operation["cutoff_energy_ev"] == 600
    assert operation["kpoint_separation"] == 0.03

    patched, _ = apply_semantic_patch(
        base,
        SemanticPatch(project_id=base.project_id, base_revision=base.revision, operations=plan.payload["operations"]),
    )
    calculation = model_view_audit(patched)["health"]["semiconductor_health"]["calculation_preflight_summary"]
    assert calculation["task_family"] == "property"
    assert calculation["task_intent"] == "band_structure"


@pytest.mark.parametrize(
    ("user_text", "expected_mode"),
    [
        ("Enable self-consistent dipole correction for CASTEP.", "Self-consistent"),
        ("Apply non-self-consistent dipole correction for the energy calculation.", "Non self-consistent"),
        ("Disable dipole correction.", "None"),
        ("\u542f\u7528\u81ea\u6d3d\u5076\u6781\u4fee\u6b63", "Self-consistent"),
        ("\u5173\u95ed\u5076\u6781\u4fee\u6b63", "None"),
    ],
)
def test_infer_modeling_plan_updates_castep_dipole_correction(
    user_text: str,
    expected_mode: str,
) -> None:
    base = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")

    plan = infer_modeling_plan(user_text, current_spec=base)

    assert plan.kind == "patch"
    assert plan.template_id == "castep_settings"
    operation = plan.payload["operations"][0]
    assert operation["type"] == "set_castep_energy"
    assert operation["task"] == "Energy"
    assert operation["cutoff_energy_ev"] == base.simulation.cutoff_energy_ev
    assert operation["kpoint_separation"] == base.simulation.kpoint_separation
    assert operation["dipole_correction"] == expected_mode


def test_infer_modeling_plan_maps_semiconductor_property_aliases_to_castep_patch() -> None:
    base = load_example("silicon_diamond_spec.json")

    band_gap_plan = infer_modeling_plan(
        "Calculate the band gap with CASTEP using cutoff 520 eV.",
        current_spec=base,
    )
    assert band_gap_plan.kind == "patch"
    assert band_gap_plan.template_id == "castep_settings"
    band_gap_operation = band_gap_plan.payload["operations"][0]
    assert band_gap_operation["type"] == "set_castep_energy"
    assert band_gap_operation["task"] == "BandStructure"
    assert band_gap_operation["cutoff_energy_ev"] == 520

    chinese_band_gap_plan = infer_modeling_plan(
        "计算带隙，设置 k 点间距 0.04",
        current_spec=base,
    )
    assert chinese_band_gap_plan.kind == "patch"
    chinese_band_gap_operation = chinese_band_gap_plan.payload["operations"][0]
    assert chinese_band_gap_operation["task"] == "BandStructure"
    assert chinese_band_gap_operation["kpoint_separation"] == 0.04

    chinese_cutoff_plan = infer_modeling_plan(
        "\u8bbe\u7f6e CASTEP \u622a\u65ad\u80fd\u4e3a 600 eV",
        current_spec=base,
    )
    assert chinese_cutoff_plan.kind == "patch"
    assert chinese_cutoff_plan.template_id == "castep_settings"
    chinese_cutoff_operation = chinese_cutoff_plan.payload["operations"][0]
    assert chinese_cutoff_operation["type"] == "set_castep_energy"
    assert chinese_cutoff_operation["task"] == "Energy"
    assert chinese_cutoff_operation["cutoff_energy_ev"] == 600

    chinese_kpoint_grid_plan = infer_modeling_plan(
        "\u8ba1\u7b97\u5e26\u9699\uff0c\u5e73\u9762\u6ce2\u622a\u65ad 520 eV\uff0ck\u70b9\u7f51\u683c 6\u00d76\u00d76",
        current_spec=base,
    )
    assert chinese_kpoint_grid_plan.kind == "patch"
    assert chinese_kpoint_grid_plan.template_id == "castep_settings"
    chinese_kpoint_grid_operation = chinese_kpoint_grid_plan.payload["operations"][0]
    assert chinese_kpoint_grid_operation["task"] == "BandStructure"
    assert chinese_kpoint_grid_operation["cutoff_energy_ev"] == 520
    assert chinese_kpoint_grid_operation["kpoints"] == [6, 6, 6]
    assert "kpoint_separation" not in chinese_kpoint_grid_operation

    pdos_plan = infer_modeling_plan(
        "Set up PDOS with kpoint grid 6x6x6.",
        current_spec=base,
    )
    assert pdos_plan.kind == "patch"
    pdos_operation = pdos_plan.payload["operations"][0]
    assert pdos_operation["task"] == "ProjectedDensityOfStates"
    assert pdos_operation["kpoints"] == [6, 6, 6]

    chinese_pdos_plan = infer_modeling_plan(
        "设置投影态密度，k点网格 4x4x2。",
        current_spec=base,
    )
    assert chinese_pdos_plan.payload["operations"][0]["task"] == "ProjectedDensityOfStates"

    optical_plan = infer_modeling_plan(
        "Set up optical properties with cutoff 500 eV.",
        current_spec=base,
    )
    optical_operation = optical_plan.payload["operations"][0]
    assert optical_operation["task"] == "Optics"
    assert optical_operation["cutoff_energy_ev"] == 500

    phonon_plan = infer_modeling_plan("Set up phonon calculation.", current_spec=base)
    assert phonon_plan.payload["operations"][0]["task"] == "Phonon"

    elastic_plan = infer_modeling_plan("Set up elastic constants.", current_spec=base)
    assert elastic_plan.payload["operations"][0]["task"] == "ElasticConstants"


def test_infer_modeling_plan_applies_castep_property_settings_during_new_semiconductor_create() -> None:
    plan = infer_modeling_plan(
        "Build WS2 monolayer band structure with cutoff 620 eV and kpoint separation 0.03.",
    )
    assert plan.kind == "spec"
    assert plan.template_id == "tungsten_disulfide_2d_ws2_monolayer"
    spec = ModelSpec.model_validate(plan.payload)
    assert spec.simulation is not None
    assert spec.simulation.task == "BandStructure"
    assert spec.simulation.cutoff_energy_ev == 620
    assert spec.simulation.kpoint_separation == 0.03
    assert spec.metadata["nl_composite_operations"] == ["set_castep_energy"]

    audit = model_view_audit(spec)
    semiconductor = audit["health"]["semiconductor_health"]
    calculation = semiconductor["calculation_preflight_summary"]
    assert calculation["task_family"] == "property"
    assert calculation["task_intent"] == "band_structure"
    assert calculation["cutoff_energy_ev"] == 620
    assert calculation["kpoint_separation"] == 0.03
    assert semiconductor["band_path_summary"]["task_relevant"] is True

    alloy_plan = infer_modeling_plan(
        "Build Si0.75Ge0.25 alloy as a 2x1x1 supercell for band structure with cutoff 600 eV.",
    )
    alloy_spec = ModelSpec.model_validate(alloy_plan.payload)
    assert alloy_spec.simulation is not None
    assert alloy_spec.simulation.task == "BandStructure"
    assert alloy_spec.simulation.cutoff_energy_ev == 600
    assert "set_castep_energy" in alloy_spec.metadata["nl_composite_operations"]


def test_infer_modeling_plan_maps_hemt_barrier_thickness_aliases() -> None:
    requests = [
        "Build an AlGaN/GaN HEMT with barrier thickness 15 nm.",
        (
            "\u6784\u5efaAlGaN/GaN HEMT\uff0c"
            "\u52bf\u5792\u539a\u5ea615nm\uff0c"
            "\u5bfc\u51fa2DEG\u8bca\u65ad"
        ),
        (
            "\u6784\u5efaAlGaN/GaN HEMT\uff0c"
            "\u52bf\u5792\u5c42\u539a\u5ea615nm\uff0c"
            "\u5bfc\u51fa2DEG\u8bca\u65ad"
        ),
    ]

    for request in requests:
        plan = infer_modeling_plan(request)
        assert plan.kind == "spec"
        assert plan.template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
        spec = ModelSpec.model_validate(plan.payload)
        layer_request = spec.metadata["quantum_well_layer_request"]
        assert layer_request["source"] == "explicit_role_thicknesses"
        assert layer_request["well_material"] == "GaN"
        assert layer_request["barrier_material"] == "Al0.25Ga0.75N"
        assert layer_request["well_layer_count"] == 4
        assert layer_request["barrier_layer_count"] == 188
        assert layer_request["requested_barrier_thickness_angstrom"] == 150.0
        assert layer_request["actual_barrier_thickness_angstrom"] == 148.97825
        assert layer_request["barrier_thickness_error_angstrom"] == -1.02175
        assert spec.metadata["nl_composite_operations"][-1] == "set_quantum_well_layers GaN:4 Al0.25Ga0.75N:188"


def test_infer_modeling_plan_builds_p_gan_gate_hemt_without_silent_plain_template() -> None:
    requests = [
        "Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG diagnostics.",
        "Build AlGaN/GaN HEMT with p-type GaN cap layer and Mg doping.",
        (
            "\u6784\u5efa p-GaN \u6805 AlGaN/GaN HEMT\uff0c"
            "\u70ed\u52a0\u8f7d\u5e76\u5bfc\u51fa2DEG\u8bca\u65ad"
        ),
    ]

    for request in requests:
        plan = infer_modeling_plan(request)
        assert plan.kind == "spec"
        assert plan.template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
        spec = ModelSpec.model_validate(plan.payload)

        cap = spec.metadata["p_gan_gate_cap"]
        assert spec.model.name == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_p_gan_gate"
        assert spec.metadata["materials"] == ["GaN", "Al0.25Ga0.75N", "p-GaN"]
        assert spec.metadata["polarization_2deg_barrier_materials"] == ["Al0.25Ga0.75N"]
        assert spec.metadata["nl_composite_operations"][-1].startswith("add_p_gan_gate_cap 4 layers")
        assert cap["material"] == "p-GaN"
        assert cap["layer_count"] == 4
        assert cap["actual_thickness_angstrom"] == 3.189
        assert cap["dopant_element"] == "Mg"
        assert cap["dopant_site_element"] == "Ga"
        assert cap["dopant_atom_id"] == "MgPGaN1_1"
        assert spec.model.lattice.c == 13.559
        assert len(spec.model.basis_atoms) == 48

        audit = model_view_audit(spec)
        semiconductor = audit["health"]["semiconductor_health"]
        assert semiconductor["ok"] is True
        assert semiconductor["rule"] == "doped_iii_v_tetrahedral"
        assert semiconductor["quantum_well_summary"]["materials"] == ["GaN", "Al0.25Ga0.75N", "p-GaN"]
        assert semiconductor["polarization_2deg_summary"]["quality"] == "complete"
        assert semiconductor["polarization_2deg_summary"]["barrier_materials"] == ["Al0.25Ga0.75N"]
        assert semiconductor["polarization_2deg_summary"]["candidate_count"] == 1
        cap_summary = semiconductor["p_gan_gate_cap_summary"]
        assert cap_summary["quality"] == "complete"
        assert cap_summary["material"] == "p-GaN"
        assert cap_summary["layer_count"] == 4
        assert cap_summary["matched_layer_count"] == 4
        assert cap_summary["actual_thickness_angstrom"] == 3.189
        assert cap_summary["dopant_atom_id"] == "MgPGaN1_1"
        assert cap_summary["dopant_site_found"] is True
        assert cap_summary["polarization_2deg_quality"] == "complete"
        assert cap_summary["polarization_2deg_barrier_materials"] == ["Al0.25Ga0.75N"]
        dopant_site = semiconductor["dopant_site_summary"]
        assert dopant_site["site_count"] == 1
        assert dopant_site["carrier_type_hint"] == "acceptor_like_p_type"
        assert dopant_site["latest"]["atom_id"] == "MgPGaN1_1"


def test_infer_modeling_plan_combines_hemt_barrier_thickness_with_p_gan_gate_thickness() -> None:
    plan = infer_modeling_plan(
        "Build an AlGaN/GaN HEMT with barrier thickness 15 nm and p-GaN gate thickness 2 nm.",
    )

    assert plan.kind == "spec"
    spec = ModelSpec.model_validate(plan.payload)
    layer_request = spec.metadata["quantum_well_layer_request"]
    cap = spec.metadata["p_gan_gate_cap"]

    assert layer_request["requested_barrier_thickness_angstrom"] == 150.0
    assert layer_request["barrier_layer_count"] == 188
    assert cap["requested_thickness_angstrom"] == 20.0
    assert cap["actual_thickness_angstrom"] == 19.134
    assert cap["layer_count"] == 24
    assert len(spec.model.basis_atoms) == 864
    assert spec.metadata["nl_composite_operations"] == [
        "set_quantum_well_layers GaN:4 Al0.25Ga0.75N:188",
        "add_p_gan_gate_cap 24 layers 19.134A Mg:MgPGaN1_1",
    ]

    semiconductor = model_view_audit(spec)["health"]["semiconductor_health"]
    assert semiconductor["ok"] is True
    assert semiconductor["polarization_2deg_summary"]["barrier_materials"] == ["Al0.25Ga0.75N"]
    assert semiconductor["p_gan_gate_cap_summary"]["actual_thickness_angstrom"] == 19.134
    assert semiconductor["p_gan_gate_cap_summary"]["matched_layer_count"] == 24
    assert semiconductor["dopant_site_summary"]["carrier_type_hint"] == "acceptor_like_p_type"


def test_infer_modeling_plan_maps_p_gan_gate_cap_thickness_followups() -> None:
    created = infer_modeling_plan("Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG diagnostics.")
    current = ModelSpec.model_validate(created.payload)

    cases = [
        "set p-GaN gate thickness to 2 nm",
        "set gate thickness to 2 nm",
        "p-GaN\u6805\u539a\u5ea6\u8bbe\u4e3a2\u7eb3\u7c73",
    ]
    for request in cases:
        plan = infer_modeling_plan(request, current_spec=current)
        assert plan.kind == "patch"
        assert plan.template_id == "p_gan_gate_cap_thickness"
        operation = plan.payload["operations"][0]
        assert operation == {"type": "set_p_gan_gate_cap_thickness", "thickness_angstrom": 20.0}

    combo = infer_modeling_plan(
        "set p-GaN gate thickness to 2 nm and set CASTEP cutoff to 600 eV",
        current_spec=current,
    )
    assert combo.kind == "patch"
    assert combo.template_id == "crystal_composite_edit"
    assert [operation["type"] for operation in combo.payload["operations"]] == [
        "set_p_gan_gate_cap_thickness",
        "set_castep_energy",
    ]
    assert combo.payload["operations"][0]["thickness_angstrom"] == 20.0
    assert combo.payload["operations"][1]["cutoff_energy_ev"] == 600


def test_infer_modeling_plan_maps_current_quantum_well_thickness_followups() -> None:
    current = load_example("aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json")

    cases = [
        ("set barrier thickness to 15 nm", "barrier", 150.0),
        ("\u628a\u52bf\u5792\u539a\u5ea6\u8bbe\u4e3a15\u7eb3\u7c73", "barrier", 150.0),
        ("set well thickness to 4 nm", "well", 40.0),
    ]
    for request, target_layer, thickness in cases:
        plan = infer_modeling_plan(request, current_spec=current)
        assert plan.kind == "patch"
        assert plan.template_id == "quantum_well_thickness"
        operation = plan.payload["operations"][0]
        assert operation == {
            "type": "set_quantum_well_thickness",
            "target_layer": target_layer,
            "thickness_angstrom": thickness,
        }

    combo = infer_modeling_plan(
        "set barrier thickness to 15 nm and set CASTEP cutoff to 600 eV",
        current_spec=current,
    )
    assert combo.kind == "patch"
    assert combo.template_id == "crystal_composite_edit"
    assert [operation["type"] for operation in combo.payload["operations"]] == [
        "set_quantum_well_thickness",
        "set_castep_energy",
    ]
    assert combo.payload["operations"][0]["target_layer"] == "barrier"
    assert combo.payload["operations"][0]["thickness_angstrom"] == 150.0
    assert combo.payload["operations"][1]["cutoff_energy_ev"] == 600

    new_structure = infer_modeling_plan(
        "Build an AlGaN/GaN HEMT with barrier thickness 15 nm.",
        current_spec=current,
    )
    assert new_structure.kind == "spec"
    assert new_structure.template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"


def test_model_view_audit_reports_wurtzite_band_path_preflight() -> None:
    gan = model_view_audit(load_example("gallium_nitride_wurtzite_spec.json"))["health"]["semiconductor_health"]
    band_path = gan["band_path_summary"]

    assert band_path["available"] is True
    assert band_path["bravais_lattice"] == "hexagonal"
    assert band_path["path_label"] == "Gamma-M-K-Gamma-A-L-H-A-L-M-K-H"
    assert band_path["point_count"] == 12
    assert band_path["segment_count"] == 11
    assert {"label": "A", "fractional": [0.0, 0.0, 0.5]} in band_path["high_symmetry_points"]


def test_model_view_audit_reports_semiconductor_vacancy_defect_summary() -> None:
    base = load_example("silicon_diamond_spec.json")
    supercell, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[{"type": "make_supercell", "matrix": [2, 1, 1]}],
        ),
    )
    plan = infer_modeling_plan("Create vacancy at Si1_000.", current_spec=supercell)
    vacancy, _ = apply_semantic_patch(
        supercell,
        SemanticPatch(
            project_id=supercell.project_id,
            base_revision=supercell.revision,
            operations=plan.payload["operations"],
        ),
    )

    defect_summary = model_view_audit(vacancy)["health"]["semiconductor_health"]["defect_summary"]
    defect = defect_summary["defects"][0]

    assert plan.template_id == "crystal_vacancy"
    assert vacancy.metadata["defects"][0]["site_id"] == "Si1_000"
    assert defect_summary["vacancy_count"] == 1
    assert defect_summary["total_lattice_site_count_estimate"] == 16
    assert defect_summary["total_vacancy_fraction"] == 0.0625
    assert defect["site_element"] == "Si"
    assert defect["nearest_neighbor_count"] == 4
    assert defect["undercoordinated_neighbor_count"] == 4
    assert defect["missing_neighbor_bond_estimate"] == 4
    assert defect["nearest_neighbor_elements"] == ["Si", "Si", "Si", "Si"]
    finite_size = model_view_audit(vacancy)["health"]["semiconductor_health"]["finite_size_summary"]
    assert finite_size["non_passivant_atom_count"] == 15
    assert finite_size["max_isolated_item"]["kind"] == "vacancy"
    assert finite_size["max_isolated_fraction"] == 0.0625
    assert finite_size["finite_size_warning"] is True


def test_model_view_audit_reports_verified_nearest_neighbor_divacancy_complex(tmp_path: Path) -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan(
        "Create nearest-neighbor Ga-As divacancy.",
        current_spec=base,
    )
    divacancy, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    audit = model_view_audit(divacancy)
    defect_summary = audit["health"]["semiconductor_health"]["defect_summary"]
    complex_row = defect_summary["complexes"][0]
    health = build_modeling_health(
        {"validation": {"valid": True, "errors": [], "warnings": []}, "view_audit": audit},
        execution_mode="preview",
    )

    assert plan.kind == "patch"
    assert plan.template_id == "crystal_divacancy"
    assert [operation["atom_id"] for operation in plan.payload["operations"][:2]] == ["Ga1", "As1"]
    assert len(divacancy.model.basis_atoms) == 6
    assert divacancy.metadata["defect_count"] == 2
    assert divacancy.metadata["defect_complex_count"] == 1
    assert divacancy.metadata["last_defect_complex"]["selection_rule"] == (
        "minimum_periodic_pair_distance_then_atom_id"
    )
    assert defect_summary["vacancy_count"] == 2
    assert defect_summary["complex_count"] == 1
    assert defect_summary["divacancy_count"] == 1
    assert defect_summary["defect_complex_integrity_ok"] is True
    assert defect_summary["defect_complex_integrity_errors"] == []
    assert complex_row["member_site_ids"] == ["Ga1", "As1"]
    assert complex_row["member_site_elements"] == ["Ga", "As"]
    assert complex_row["member_vacancy_record_count"] == 2
    assert complex_row["pair_distance_angstrom_recomputed"] == pytest.approx(2.447951)
    assert complex_row["distance_delta_angstrom"] == 0.0
    assert complex_row["nearest_neighbor_recomputed"] is True
    assert complex_row["nearest_neighbor_verified"] is True
    assert complex_row["metadata_consistent"] is True
    assert health["checks"]["semiconductor_defect_complex_count"] == 1
    assert health["checks"]["semiconductor_divacancy_count"] == 1
    assert health["checks"]["semiconductor_defect_complex_integrity_ok"] is True
    assert any("unrelaxed structural starting point" in warning for warning in health["warnings"])

    bundle = write_view_audit_bundle(tmp_path, divacancy, audit)
    complex_csv = Path(bundle["files"]["semiconductor_defect_complexes_csv"])
    assert complex_csv.exists()
    assert bundle["row_counts"]["semiconductor_defect_complexes"] == 1
    rows = list(csv.DictReader(complex_csv.open(encoding="utf-8")))
    assert rows[0]["complex_id"] == "divacancy_001"
    assert rows[0]["member_site_ids"] == "Ga1;As1"
    assert rows[0]["nearest_neighbor_verified"] == "True"
    assert rows[0]["metadata_consistent"] == "True"


def test_divacancy_rejects_explicit_non_neighbor_pair() -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan(
        "Create divacancy at Ga1 and Ga2.",
        current_spec=base,
    )

    assert plan.kind == "unsupported"
    assert plan.template_id == "crystal_divacancy"
    assert plan.payload is None
    assert any("outside the verified nearest-neighbor threshold" in note for note in plan.notes)


def test_new_semiconductor_template_applies_supercell_before_divacancy_selection() -> None:
    plan = infer_modeling_plan(
        "Build GaAs zinc blende crystal as a 2x1x1 supercell with nearest-neighbor Ga-As divacancy."
    )
    spec = ModelSpec.model_validate(plan.payload)

    assert plan.kind == "spec"
    assert plan.template_id == "gallium_arsenide_zincblende"
    assert len(spec.model.basis_atoms) == 14
    assert spec.metadata["last_defect_complex"]["member_site_ids"] == ["Ga1_000", "As1_000"]
    assert spec.metadata["last_defect_complex"]["selection_rule"] == (
        "minimum_periodic_pair_distance_then_atom_id"
    )
    assert model_view_audit(spec)["health"]["semiconductor_health"]["defect_summary"][
        "defect_complex_integrity_ok"
    ] is True


def test_current_crystal_composite_patch_applies_supercell_before_divacancy_selection() -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan(
        "Make 2x1x1 supercell and create nearest-neighbor Ga-As divacancy.",
        current_spec=base,
    )
    patched, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    assert plan.kind == "patch"
    assert plan.template_id == "crystal_composite_edit"
    assert [operation["type"] for operation in plan.payload["operations"]] == [
        "make_supercell",
        "delete_atom",
        "delete_atom",
        "set_metadata",
    ]
    assert len(patched.model.basis_atoms) == 14
    assert patched.metadata["last_defect_complex"]["member_site_ids"] == ["Ga1_000", "As1_000"]
    assert model_view_audit(patched)["health"]["semiconductor_health"]["defect_summary"][
        "defect_complex_integrity_ok"
    ] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "创建最近邻 Ga-As 双空位",
        "在 Ga1 和 As1 位点创建最近邻双空位",
    ],
)
def test_divacancy_parser_supports_chinese_element_and_explicit_site_requests(prompt: str) -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan(prompt, current_spec=base)

    assert plan.kind == "patch"
    assert plan.template_id == "crystal_divacancy"
    assert [operation["atom_id"] for operation in plan.payload["operations"][:2]] == ["Ga1", "As1"]


@pytest.mark.parametrize("tampered_distance", [9.0, float("nan")], ids=["mismatch", "non_finite"])
def test_divacancy_metadata_distance_tampering_fails_closed(tampered_distance: float) -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan(
        "Create divacancy at Ga1 and As1.",
        current_spec=base,
    )
    divacancy, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )
    payload = divacancy.model_dump(mode="json")
    payload["metadata"]["defect_complexes"][0]["pair_distance_angstrom"] = tampered_distance
    tampered = ModelSpec.model_validate(payload)

    audit = model_view_audit(tampered)
    defect_summary = audit["health"]["semiconductor_health"]["defect_summary"]

    assert audit["health"]["ok"] is False
    assert defect_summary["defect_complex_integrity_ok"] is False
    assert defect_summary["complexes"][0]["metadata_consistent"] is False
    assert any(
        "recorded pair distance" in error or "pair_distance_angstrom must be finite" in error
        for error in defect_summary["defect_complex_integrity_errors"]
    )
    assert any(
        "recorded pair distance" in error or "pair_distance_angstrom must be finite" in error
        for error in audit["health"]["errors"]
    )


def test_model_view_audit_reports_semiconductor_interstitial_defect_summary() -> None:
    base = load_example("silicon_diamond_spec.json")
    plan = infer_modeling_plan("Add Si interstitial at fractional 0.5 0.5 0.5.", current_spec=base)
    interstitial, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    defect_summary = model_view_audit(interstitial)["health"]["semiconductor_health"]["defect_summary"]
    defect = defect_summary["defects"][0]

    assert plan.template_id == "crystal_interstitial_fractional"
    assert interstitial.metadata["defects"][0]["atom_id"] == "Si9"
    assert defect_summary["interstitial_count"] == 1
    assert defect_summary["vacancy_count"] == 0
    assert defect_summary["total_lattice_site_count_estimate"] == 8
    assert defect_summary["total_interstitial_fraction"] == 0.125
    assert defect["type"] == "interstitial"
    assert defect["site_id"] == "Si9"
    assert defect["site_element"] == "Si"
    assert defect["nearest_neighbor_count"] == 7
    assert defect["interstitial_neighbor_count"] == 10
    assert defect["coordination_outlier"] is True
    assert defect["missing_neighbor_bond_estimate"] == 0


def test_model_view_audit_reports_semiconductor_antisite_defect_summary() -> None:
    base = load_example("gallium_arsenide_zincblende_spec.json")
    plan = infer_modeling_plan("Create As antisite at Ga1.", current_spec=base)
    antisite, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    semiconductor = model_view_audit(antisite)["health"]["semiconductor_health"]
    defect_summary = semiconductor["defect_summary"]
    defect = defect_summary["defects"][0]

    assert plan.template_id == "crystal_antisite"
    assert semiconductor["ok"] is True
    assert semiconductor["errors"] == []
    assert semiconductor["unexpected_neighbor_pair_count"] == 4
    assert any("Intentional antisite" in warning for warning in semiconductor["warnings"])
    assert defect_summary["antisite_count"] == 1
    assert defect_summary["total_antisite_fraction"] == 0.125
    assert defect["site_id"] == "Ga1"
    assert defect["site_element"] == "Ga"
    assert defect["new_element"] == "As"
    assert defect["role_hint"] == "anion_on_cation_site"
    assert defect["antisite_neighbor_count"] == 4
    assert defect["same_sublattice_neighbor_count"] == 4
    assert defect["same_sublattice_neighbor_ids"] == ["As1", "As2", "As3", "As4"]


def test_model_view_audit_flags_unexpected_iii_v_neighbor_pairs() -> None:
    payload = load_example("gallium_arsenide_zincblende_spec.json").model_dump(mode="json")
    payload["project_id"] = "bad_iii_v_neighbor_pairs"
    payload["model"]["basis_atoms"][4]["element"] = "Ga"
    spec = ModelSpec.model_validate(payload)

    audit = model_view_audit(spec)
    semiconductor = audit["health"]["semiconductor_health"]

    assert audit["health"]["ok"] is False
    assert semiconductor["ok"] is False
    assert semiconductor["unexpected_neighbor_pair_count"] > 0
    assert semiconductor["neighbor_pair_counts"]["Ga-Ga"] > 0
    unexpected_neighbor_types = {
        item["pair_type"]
        for item in semiconductor["neighbor_distance_summary"]["pair_types"]
        if item["pair_role"] == "unexpected"
    }
    assert "Ga-Ga" in unexpected_neighbor_types
    assert any("Ga-Ga" in error for error in semiconductor["errors"])
    sublattice = semiconductor["sublattice_balance_summary"]
    assert sublattice["balanced"] is False
    assert sublattice["balance_delta_count"] == 2
    assert sublattice["iii_v_cation_count"] == 5
    assert sublattice["iii_v_anion_count"] == 3
    assert any("sublattice balance" in warning for warning in semiconductor["warnings"])


def test_model_view_audit_reports_slab_vacuum_summary() -> None:
    spec = load_example("silicon_100_slab_spec.json")
    audit = model_view_audit(spec)

    slab = audit["health"]["slab_vacuum"]
    assert slab["surface_orientation"] == "(100)"
    assert slab["surface_axis"] == "c"
    assert slab["declared_vacuum_angstrom"] > 19.0
    assert slab["atom_extent_vacuum_angstrom"] > 20.0
    assert slab["vacuum_ok"] is True
    assert slab["slab_vacuum_status"] == "off_center"
    assert slab["slab_vacuum_next_action"] == "center_slab_or_review_asymmetric_vacuum_before_claiming_normality"
    assert audit["health"]["crystal_coordination_stats"]["min"] == 2.0
    surface = audit["health"]["semiconductor_health"]["surface_termination_summary"]
    assert surface["termination"] == "unpassivated"
    assert surface["surface_orientation"] == "(100)"
    assert surface["surface_atom_count"] == 4
    assert surface["undercoordinated_surface_atom_count"] == 4
    assert surface["dangling_bond_estimate"] == 8
    assert surface["passivant_bond_count"] == 0
    assert surface["passivation_coverage_fraction"] == 0.0
    assert surface["fully_passivated"] is False
    assert surface["surface_preparation_status"] == "dangling_bonds"
    assert (
        surface["surface_preparation_next_action"]
        == "passivate_surface_dangling_bonds_before_calculation_or_claiming_normality"
    )
    assert surface["surfaces"]["top"]["surface_atom_ids"] == ["Si4", "Si6"]
    assert surface["surfaces"]["bottom"]["surface_atom_ids"] == ["Si1", "Si7"]
    polarity = audit["health"]["semiconductor_health"]["surface_polarity_summary"]
    assert polarity["model"] == "surface_element_and_passivation_symmetry_heuristic"
    assert polarity["bottom"]["formula"] == "Si2"
    assert polarity["top"]["formula"] == "Si2"
    assert polarity["same_element_counts"] is True
    assert polarity["passivation_symmetric"] is True
    assert polarity["polar_surface_hint"] is False
    assert polarity["surface_polarity_status"] == "symmetric_nonpolar"
    assert polarity["surface_polarity_next_action"] == "surface_polarity_ready"
    assert polarity["surface_asymmetry_warning"] is False


def test_model_view_audit_reports_polar_semiconductor_slab_surface_summary() -> None:
    gaas = model_view_audit(load_example("gallium_arsenide_001_slab_spec.json"))
    polarity = gaas["health"]["semiconductor_health"]["surface_polarity_summary"]

    assert polarity["surface_orientation"] == "(001)"
    assert polarity["bottom"]["formula"] == "Ga2"
    assert polarity["top"]["formula"] == "As2"
    assert polarity["same_element_counts"] is False
    assert polarity["passivation_symmetric"] is True
    assert polarity["polar_surface_hint"] is True
    assert polarity["surface_asymmetry_warning"] is True
    health = build_modeling_health({"ok": True, "view_audit": gaas}, execution_mode="preview")
    assert health["checks"]["semiconductor_surface_polar_hint"] is True
    assert health["checks"]["semiconductor_surface_bottom_formula"] == "Ga2"
    assert health["checks"]["semiconductor_surface_top_formula"] == "As2"
    assert any("asymmetric or polar surface" in warning for warning in health["warnings"])

    gan = model_view_audit(load_example("gallium_nitride_0001_slab_spec.json"))["health"]["semiconductor_health"]["surface_polarity_summary"]
    assert gan["surface_orientation"] == "(0001)"
    assert gan["bottom"]["formula"] == "Ga"
    assert gan["top"]["formula"] == "N"
    assert gan["polar_surface_hint"] is True

    zno_audit = model_view_audit(load_example("zinc_oxide_0001_slab_spec.json"))
    zno = zno_audit["health"]["semiconductor_health"]
    assert zno["rule"] == "ii_vi_tetrahedral"
    assert zno["sublattice_balance_summary"]["balanced"] is True
    assert zno["surface_termination_summary"]["dangling_bond_estimate"] == 24
    zno_polarity = zno["surface_polarity_summary"]
    assert zno_polarity["surface_orientation"] == "(0001)"
    assert zno_polarity["bottom"]["formula"] == "Zn4"
    assert zno_polarity["top"]["formula"] == "O4"
    assert zno_polarity["polar_surface_hint"] is True
    assert zno_audit["health"]["slab_vacuum"]["vacuum_ok"] is True


def test_model_view_audit_reports_centered_beta_gallium_oxide_010_surface() -> None:
    audit = model_view_audit(load_example("beta_gallium_oxide_010_slab_spec.json"))
    semiconductor = audit["health"]["semiconductor_health"]

    assert semiconductor["rule"] == "oxide_semiconductor_mixed_coordination"
    slab_vacuum = audit["health"]["slab_vacuum"]
    assert slab_vacuum["surface_orientation"] == "(010)"
    assert slab_vacuum["surface_axis"] == "b"
    assert slab_vacuum["vacuum_ok"] is True
    assert slab_vacuum["centered_in_cell"] is True
    assert slab_vacuum["bottom_vacuum_angstrom"] == 10.222175
    assert slab_vacuum["top_vacuum_angstrom"] == 10.222175
    assert slab_vacuum["slab_vacuum_status"] == "ready"
    assert slab_vacuum["slab_vacuum_next_action"] == "slab_vacuum_spacing_and_centering_ok"

    surface_model = semiconductor["surface_model_summary"]
    assert surface_model["status"] == "ready"
    assert surface_model["ready_for_calculation_preflight"] is True
    assert surface_model["next_action"] == "surface_model_ready_for_calculation_preflight"

    polarity = semiconductor["surface_polarity_summary"]
    assert polarity["bottom"]["formula"] == "Ga4O6"
    assert polarity["top"]["formula"] == "Ga4O6"
    assert polarity["same_element_counts"] is True
    assert polarity["surface_polarity_status"] == "symmetric_nonpolar"

    health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert health["checks"]["semiconductor_slab_centered_in_cell"] is True
    assert health["checks"]["semiconductor_surface_model_status"] == "ready"
    assert not any("slab vacuum/centering preflight needs review" in warning for warning in health["warnings"])


def test_model_view_audit_reports_surface_passivation_coverage() -> None:
    base = load_example("silicon_100_slab_spec.json")
    passivated, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {"type": "add_atom", "id": "Htop1", "element": "H", "fractional": [0.25, 0.75, 0.22213]},
                {"type": "add_atom", "id": "Htop2", "element": "H", "fractional": [0.75, 0.25, 0.22213]},
                {"type": "add_atom", "id": "Hbottom1", "element": "H", "fractional": [0.0, 0.0, 0.9408]},
                {"type": "add_atom", "id": "Hbottom2", "element": "H", "fractional": [0.5, 0.5, 0.9408]},
                {
                    "type": "set_metadata",
                    "metadata_updates": {
                        "termination": "hydrogen_passivated_both",
                        "passivation": {"element": "H", "surfaces": ["top", "bottom"], "added_atom_count": 4},
                    },
                },
            ],
        ),
    )

    passivated_audit = model_view_audit(passivated)
    surface = passivated_audit["health"]["semiconductor_health"]["surface_termination_summary"]

    assert surface["termination"] == "hydrogen_passivated_both"
    assert surface["passivant_bond_count"] == 4
    assert surface["dangling_bond_estimate"] == 4
    assert surface["passivation_coverage_fraction"] == 0.5
    assert surface["fully_passivated"] is False
    assert surface["surface_preparation_status"] == "partially_passivated_with_dangling_bonds"
    assert surface["surfaces"]["top"]["passivant_bond_count"] == 2
    assert surface["surfaces"]["bottom"]["passivant_bond_count"] == 2
    surface_model = passivated_audit["health"]["semiconductor_health"]["surface_model_summary"]
    assert surface_model["status"] == "blocked"
    assert surface_model["surface_preparation_status"] == "partially_passivated_with_dangling_bonds"
    assert "surface_preparation:partially_passivated_with_dangling_bonds" in surface_model["blocking_reasons"]
    polarity = passivated_audit["health"]["semiconductor_health"]["surface_polarity_summary"]
    assert polarity["same_element_counts"] is True
    assert polarity["passivation_symmetric"] is True
    assert polarity["surface_asymmetry_warning"] is False


def test_infer_modeling_plan_maps_chinese_surface_passivation_phrases() -> None:
    base = load_example("silicon_100_slab_spec.json")

    full_plan = infer_modeling_plan(
        "\u5b8c\u5168\u6c22\u949d\u5316\u4e0a\u4e0b\u8868\u9762",
        current_spec=base,
    )
    assert full_plan.kind == "patch"
    assert full_plan.template_id == "crystal_hydrogen_passivation"
    full_metadata = full_plan.payload["operations"][-1]["metadata_updates"]
    assert full_metadata["termination"] == "fully_hydrogen_passivated_both"
    assert full_metadata["passivation"]["surfaces"] == ["top", "bottom"]
    assert full_metadata["passivation"]["added_atom_count"] == 8
    assert full_metadata["passivation"]["full_passivation_requested"] is True

    hydrogenation_plan = infer_modeling_plan(
        "\u6c22\u5316\u4e0a\u4e0b\u8868\u9762",
        current_spec=base,
    )
    hydrogenation_metadata = hydrogenation_plan.payload["operations"][-1]["metadata_updates"]
    assert hydrogenation_metadata["termination"] == "hydrogen_passivated_both"
    assert hydrogenation_metadata["passivation"]["surfaces"] == ["top", "bottom"]
    assert hydrogenation_metadata["passivation"]["full_passivation_requested"] is False

    dangling_bond_plan = infer_modeling_plan(
        "\u7528\u6c22\u9971\u548c\u6240\u6709\u60ac\u6302\u952e",
        current_spec=base,
    )
    dangling_bond_metadata = dangling_bond_plan.payload["operations"][-1]["metadata_updates"]
    assert dangling_bond_metadata["termination"] == "fully_hydrogen_passivated_both"
    assert dangling_bond_metadata["passivation"]["surfaces"] == ["top", "bottom"]
    assert dangling_bond_metadata["passivation"]["full_passivation_requested"] is True


def test_write_view_audit_bundle_exports_crystal_csv_tables(tmp_path: Path) -> None:
    spec = load_example("silicon_diamond_spec.json")
    audit = model_view_audit(spec)

    bundle = write_view_audit_bundle(tmp_path, spec, audit)

    assert Path(bundle["files"]["crystal_nearest_neighbors_csv"]).exists()
    assert Path(bundle["files"]["crystal_coordination_csv"]).exists()
    assert bundle["row_counts"]["crystal_nearest_neighbors"] == 8
    assert bundle["row_counts"]["crystal_coordination"] == 8
    assert "atom_id,element,nearest_atom_id" in Path(bundle["files"]["crystal_nearest_neighbors_csv"]).read_text(encoding="utf-8")
    crystal_coordination_csv = Path(bundle["files"]["crystal_coordination_csv"]).read_text(encoding="utf-8")
    assert "atom_id,element,neighbor_count,unique_neighbor_count" in crystal_coordination_csv
    assert "unique_neighbor_ids" in crystal_coordination_csv


def test_layer_translation_diagnostics_bind_current_layer_and_export_csv(tmp_path: Path) -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")
    plan = infer_modeling_plan(
        "Shift layer 3 by 0.5 angstrom along x.",
        current_spec=base,
    )
    translated, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    audit = model_view_audit(translated)
    summary = audit["health"]["semiconductor_health"]["layer_translation_summary"]
    assert summary["quality"] == "complete"
    assert summary["metadata_consistent"] is True
    assert summary["target_binding_matches_current_layer"] is True
    assert summary["current_layer_atom_ids"] == ["Si3", "Si5"]
    assert summary["latest"]["translation_axis"] == "a"

    bundle = write_view_audit_bundle(tmp_path, translated, audit)
    csv_path = Path(bundle["files"]["semiconductor_layer_translation_csv"])
    assert csv_path.exists()
    assert bundle["row_counts"]["semiconductor_layer_translation"] == 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert rows[0]["layer_index"] == "3"
    assert rows[0]["atom_ids"] == "Si3;Si5"
    assert rows[0]["metadata_consistent"] == "True"

    expanded, _ = apply_semantic_patch(
        translated,
        SemanticPatch(
            project_id=translated.project_id,
            base_revision=translated.revision,
            operations=[{"type": "make_supercell", "matrix": [2, 1, 1]}],
        ),
    )
    stale_summary = model_view_audit(expanded)["health"]["semiconductor_health"]["layer_translation_summary"]
    assert stale_summary["quality"] == "review_required"
    assert stale_summary["metadata_consistent"] is False
    assert stale_summary["target_binding_matches_current_layer"] is False


def test_layer_rotation_diagnostics_bind_coordinates_block_calculation_and_export_csv(tmp_path: Path) -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")
    plan = infer_modeling_plan(
        "Twist layer 3 by 5 degrees.",
        current_spec=base,
    )
    rotated, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )

    audit = model_view_audit(rotated)
    summary = audit["health"]["semiconductor_health"]["layer_rotation_summary"]
    assert summary["quality"] == "visual_review_only"
    assert summary["metadata_consistent"] is True
    assert summary["target_binding_matches_current_layer"] is True
    assert summary["coordinate_binding_matches_current"] is True
    assert summary["current_layer_atom_ids"] == ["Si3", "Si5"]
    assert summary["latest"]["rotation_axis"] == "c"
    assert summary["latest"]["angle_degrees"] == 5.0
    assert summary["commensurability_verified"] is False
    assert summary["requires_commensurate_supercell"] is True
    assert summary["requires_geometry_relaxation"] is True
    assert summary["calculation_ready"] is False
    assert "layer_rotation_commensurability_unverified" in summary["calculation_blocking_reasons"]

    bundle = write_view_audit_bundle(tmp_path, rotated, audit)
    csv_path = Path(bundle["files"]["semiconductor_layer_rotation_csv"])
    assert csv_path.exists()
    assert bundle["row_counts"]["semiconductor_layer_rotation"] == 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert rows[0]["layer_index"] == "3"
    assert rows[0]["atom_ids"] == "Si3;Si5"
    assert rows[0]["angle_degrees"] == "5.0"
    assert rows[0]["coordinate_binding_matches_current"] == "True"
    assert rows[0]["commensurability_verified"] == "False"
    assert rows[0]["calculation_ready"] == "False"

    shifted, _ = apply_semantic_patch(
        rotated,
        SemanticPatch(
            project_id=rotated.project_id,
            base_revision=rotated.revision,
            operations=[
                {
                    "type": "translate_crystal_atoms",
                    "atom_ids": ["Si3", "Si5"],
                    "axis": "a",
                    "distance_angstrom": 0.1,
                }
            ],
        ),
    )
    stale_summary = model_view_audit(shifted)["health"]["semiconductor_health"]["layer_rotation_summary"]
    assert stale_summary["quality"] == "review_required"
    assert stale_summary["metadata_consistent"] is False
    assert stale_summary["target_binding_matches_current_layer"] is True
    assert stale_summary["coordinate_binding_matches_current"] is False


def test_commensurate_tmd_twist_diagnostics_verify_integer_cell_and_detect_stale_structure(
    tmp_path: Path,
) -> None:
    base = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    twisted, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "make_commensurate_twisted_bilayer",
                    "commensurate_m": 2,
                    "commensurate_n": 1,
                    "interlayer_distance_angstrom": 6.15,
                }
            ],
        ),
    )

    audit = model_view_audit(twisted)
    summary = audit["health"]["semiconductor_health"]["commensurate_twist_summary"]
    assert summary["quality"] == "commensurate_pre_relaxation"
    assert summary["metadata_consistent"] is True
    assert summary["indices_valid"] is True
    assert summary["supercell_index_verified"] is True
    assert summary["matrix_pattern_verified"] is True
    assert summary["matrix_determinant_verified"] is True
    assert summary["angle_verified"] is True
    assert summary["lattice_verified"] is True
    assert summary["layer_atom_ids_verified"] is True
    assert summary["interlayer_distance_verified"] is True
    assert summary["interlayer_gap_verified"] is True
    assert summary["structure_binding_matches_current"] is True
    assert summary["commensurability_verified"] is True
    assert summary["requires_geometry_relaxation"] is True
    assert summary["calculation_ready"] is False
    assert summary["calculation_blocking_reasons"] == [
        "commensurate_twisted_bilayer_requires_geometry_relaxation"
    ]

    modeling_health = build_modeling_health(
        {"ok": True, "view_audit": audit},
        execution_mode="preview",
    )
    assert modeling_health["checks"]["semiconductor_commensurate_twist_quality"] == (
        "commensurate_pre_relaxation"
    )
    assert modeling_health["checks"]["semiconductor_commensurate_twist_m"] == 2
    assert modeling_health["checks"]["semiconductor_commensurate_twist_n"] == 1
    assert modeling_health["checks"]["semiconductor_commensurate_twist_angle_verified"] is True
    assert (
        modeling_health["checks"]["semiconductor_commensurate_twist_commensurability_verified"]
        is True
    )
    assert modeling_health["checks"]["semiconductor_commensurate_twist_calculation_ready"] is False

    bundle = write_view_audit_bundle(tmp_path, twisted, audit)
    csv_path = Path(bundle["files"]["semiconductor_commensurate_twist_csv"])
    assert csv_path.exists()
    assert bundle["row_counts"]["semiconductor_commensurate_twist"] == 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert rows[0]["commensurate_m"] == "2"
    assert rows[0]["commensurate_n"] == "1"
    assert rows[0]["supercell_index"] == "7"
    assert rows[0]["matrix_pattern_verified"] == "True"
    assert rows[0]["matrix_determinant_verified"] == "True"
    assert rows[0]["interlayer_gap_verified"] == "True"
    assert rows[0]["structure_binding_matches_current"] == "True"
    assert len(rows[0]["structure_sha256"]) == 64
    assert rows[0]["commensurability_verified"] == "True"
    assert rows[0]["calculation_ready"] == "False"
    assert rows[0]["quality"] == "commensurate_pre_relaxation"

    target = next(atom for atom in twisted.model.basis_atoms if atom.id == "S1_L1_0000")
    stale, _ = apply_semantic_patch(
        twisted,
        SemanticPatch(
            project_id=twisted.project_id,
            base_revision=twisted.revision,
            operations=[
                {
                    "type": "set_atom_position",
                    "atom_id": target.id,
                    "fractional": [
                        target.fractional.x + 0.01,
                        target.fractional.y,
                        target.fractional.z,
                    ],
                }
            ],
        ),
    )
    stale_summary = model_view_audit(stale)["health"]["semiconductor_health"][
        "commensurate_twist_summary"
    ]
    assert stale_summary["quality"] == "review_required"
    assert stale_summary["metadata_consistent"] is False
    assert stale_summary["matrix_determinant_verified"] is True
    assert stale_summary["layer_atom_ids_verified"] is True
    assert stale_summary["structure_binding_matches_current"] is False
    assert stale_summary["commensurability_verified"] is False


def test_commensurate_tmd_heterobilayer_diagnostics_verify_materials_strain_and_structure(
    tmp_path: Path,
) -> None:
    base = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    heterobilayer, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "make_commensurate_tmd_heterobilayer",
                    "top_layer_material": "WSe2",
                    "commensurate_m": 2,
                    "commensurate_n": 1,
                    "interlayer_distance_angstrom": 6.32,
                    "strain_policy": "balanced",
                    "max_strain_percent": 3.0,
                }
            ],
        ),
    )

    audit = model_view_audit(heterobilayer)
    summary = audit["health"]["semiconductor_health"]["commensurate_heterobilayer_summary"]
    assert summary["quality"] == "commensurate_strained_pre_relaxation"
    assert summary["bottom_material"] == "MoS2"
    assert summary["top_material"] == "WSe2"
    assert summary["materials_distinct"] is True
    assert summary["bottom_layer_element_counts"] == {"Mo": 7, "S": 14}
    assert summary["top_layer_element_counts"] == {"Se": 14, "W": 7}
    assert summary["layer_materials_verified"] is True
    assert summary["strain_policy"] == "balanced"
    assert summary["common_primitive_lattice_verified"] is True
    assert summary["bottom_biaxial_strain_verified"] is True
    assert summary["top_biaxial_strain_verified"] is True
    assert summary["strain_partition_verified"] is True
    assert summary["strain_within_limit"] is True
    assert summary["matrix_determinant_verified"] is True
    assert summary["angle_verified"] is True
    assert summary["lattice_verified"] is True
    assert summary["interlayer_distance_verified"] is True
    assert summary["interlayer_gap_verified"] is True
    assert summary["structure_binding_matches_current"] is True
    assert summary["metadata_consistent"] is True
    assert summary["commensurability_verified"] is True
    assert summary["calculation_ready"] is False
    assert summary["calculation_blocking_reasons"] == [
        "commensurate_tmd_heterobilayer_requires_geometry_relaxation"
    ]
    electrostatics = audit["health"]["semiconductor_health"][
        "two_dimensional_electrostatic_summary"
    ]
    assert electrostatics["status"] == "model_geometry_verified_calculation_review"
    assert electrostatics["quality"] == "preflight_complete"
    assert electrostatics["expected_compositional_asymmetry_verified"] is True
    assert electrostatics["vacuum_geometry_verified"] is True
    assert electrostatics["structure_binding_verified"] is True
    assert electrostatics["model_geometry_verified"] is True
    assert electrostatics["model_geometry_normality_blocker"] is False
    assert electrostatics["charge_density_available"] is False
    assert electrostatics["dipole_moment_calculated"] is False
    assert electrostatics["dipole_correction_api_verified"] is True
    assert electrostatics["dipole_correction_api_contract"] == (
        "Materials Studio 20.1 CASTEP DipoleCorrection"
    )
    assert electrostatics["dipole_correction_api_property"] == "DipoleCorrection"
    assert electrostatics["dipole_correction_direction_property_exposed"] is False
    assert electrostatics["dipole_correction_setting_configured"] is False
    assert electrostatics["dipole_correction_mode"] is None
    assert electrostatics["dipole_correction_enabled"] is False
    assert electrostatics["dipole_correction_setting_verified"] is False
    assert electrostatics["geometry_relaxation_required"] is True
    assert electrostatics["calculation_review_required"] is True
    assert electrostatics["quantitative_electrostatic_calculation_ready"] is False
    assert electrostatics["calculation_blocking_reasons"] == [
        "two_dimensional_dipole_correction_review_required",
        "commensurate_tmd_heterobilayer_requires_geometry_relaxation",
    ]
    surface_polarity = audit["health"]["semiconductor_health"]["surface_polarity_summary"]
    assert surface_polarity["surface_polarity_status"] == "asymmetric_expected_2d_heterobilayer"
    assert surface_polarity["surface_asymmetry_warning"] is False
    surface_model = audit["health"]["semiconductor_health"]["surface_model_summary"]
    assert surface_model["status"] == "calculation_review"
    assert surface_model["model_geometry_ready"] is True
    assert surface_model["calculation_review_only"] is True

    modeling_health = build_modeling_health(
        {"ok": True, "view_audit": audit},
        execution_mode="preview",
    )
    checks = modeling_health["checks"]
    assert checks["semiconductor_commensurate_heterobilayer_bottom_material"] == "MoS2"
    assert checks["semiconductor_commensurate_heterobilayer_top_material"] == "WSe2"
    assert checks["semiconductor_commensurate_heterobilayer_m"] == 2
    assert checks["semiconductor_commensurate_heterobilayer_n"] == 1
    assert checks["semiconductor_commensurate_heterobilayer_layer_materials_verified"] is True
    assert checks["semiconductor_commensurate_heterobilayer_strain_partition_verified"] is True
    assert checks["semiconductor_commensurate_heterobilayer_calculation_ready"] is False
    assert checks["semiconductor_2d_electrostatic_status"] == "model_geometry_verified_calculation_review"
    assert checks["semiconductor_2d_expected_asymmetry_verified"] is True
    assert checks["semiconductor_2d_model_geometry_verified"] is True
    assert checks["semiconductor_2d_model_geometry_normality_blocker"] is False
    assert checks["semiconductor_2d_charge_density_available"] is False
    assert checks["semiconductor_2d_dipole_moment_calculated"] is False
    assert checks["semiconductor_2d_dipole_correction_api_verified"] is True
    assert checks["semiconductor_2d_dipole_correction_api_property"] == "DipoleCorrection"
    assert checks["semiconductor_2d_dipole_correction_mode"] is None
    assert checks["semiconductor_2d_dipole_correction_enabled"] is False
    assert checks["semiconductor_2d_dipole_correction_setting_verified"] is False
    assert checks["semiconductor_2d_geometry_relaxation_required"] is True
    assert checks["semiconductor_2d_calculation_review_required"] is True

    bundle = write_view_audit_bundle(
        tmp_path,
        heterobilayer,
        audit,
        modeling_health=modeling_health,
    )
    csv_path = Path(bundle["files"]["semiconductor_commensurate_heterobilayer_csv"])
    assert csv_path.exists()
    assert bundle["row_counts"]["semiconductor_commensurate_heterobilayer"] == 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert rows[0]["bottom_material"] == "MoS2"
    assert rows[0]["top_material"] == "WSe2"
    assert rows[0]["strain_policy"] == "balanced"
    assert rows[0]["layer_materials_verified"] == "True"
    assert rows[0]["strain_partition_verified"] == "True"
    assert rows[0]["structure_binding_matches_current"] == "True"
    assert rows[0]["calculation_ready"] == "False"
    electrostatic_csv_path = Path(bundle["files"]["semiconductor_2d_electrostatics_csv"])
    assert electrostatic_csv_path.exists()
    assert bundle["row_counts"]["semiconductor_2d_electrostatics"] == 1
    electrostatic_rows = list(
        csv.DictReader(electrostatic_csv_path.open(encoding="utf-8", newline=""))
    )
    assert electrostatic_rows[0]["status"] == "model_geometry_verified_calculation_review"
    assert electrostatic_rows[0]["expected_compositional_asymmetry_verified"] == "True"
    assert electrostatic_rows[0]["model_geometry_normality_blocker"] == "False"
    assert electrostatic_rows[0]["charge_density_available"] == "False"
    assert electrostatic_rows[0]["dipole_correction_api_verified"] == "True"
    assert electrostatic_rows[0]["dipole_correction_api_property"] == "DipoleCorrection"
    assert electrostatic_rows[0]["dipole_correction_setting_verified"] == "False"
    health_rows = list(
        csv.DictReader(
            Path(bundle["files"]["modeling_health_summary_csv"]).open(
                encoding="utf-8", newline=""
            )
        )
    )
    assert health_rows[0]["semiconductor_2d_electrostatic_status"] == (
        "model_geometry_verified_calculation_review"
    )
    assert health_rows[0]["semiconductor_2d_model_geometry_verified"] == "True"
    assert health_rows[0]["semiconductor_2d_dipole_correction_api_verified"] == "True"

    target = next(atom for atom in heterobilayer.model.basis_atoms if atom.id.startswith("Setop1_L2_"))
    stale, _ = apply_semantic_patch(
        heterobilayer,
        SemanticPatch(
            project_id=heterobilayer.project_id,
            base_revision=heterobilayer.revision,
            operations=[
                {
                    "type": "substitute_atom",
                    "atom_id": target.id,
                    "new_element": "S",
                }
            ],
        ),
    )
    stale_semiconductor = model_view_audit(stale)["health"]["semiconductor_health"]
    stale_summary = stale_semiconductor["commensurate_heterobilayer_summary"]
    assert stale_summary["quality"] == "review_required"
    assert stale_summary["top_layer_composition_verified"] is False
    assert stale_summary["layer_materials_verified"] is False
    assert stale_summary["structure_binding_matches_current"] is False
    assert stale_summary["metadata_consistent"] is False
    assert stale_summary["commensurability_verified"] is False
    stale_electrostatics = stale_semiconductor["two_dimensional_electrostatic_summary"]
    assert stale_electrostatics["status"] == "model_review_required"
    assert stale_electrostatics["structure_binding_verified"] is False
    assert stale_electrostatics["model_geometry_verified"] is False
    assert stale_electrostatics["model_geometry_normality_blocker"] is True
    assert "two_dimensional_electrostatic_model_geometry_unverified" in stale_electrostatics[
        "calculation_blocking_reasons"
    ]


def test_self_consistent_dipole_correction_clears_only_the_2d_setting_blocker(
    tmp_path: Path,
) -> None:
    base = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    heterobilayer, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "make_commensurate_tmd_heterobilayer",
                    "top_layer_material": "WSe2",
                    "commensurate_m": 2,
                    "commensurate_n": 1,
                    "interlayer_distance_angstrom": 6.32,
                    "strain_policy": "balanced",
                    "max_strain_percent": 3.0,
                }
            ],
        ),
    )
    simulation = heterobilayer.simulation
    assert simulation is not None
    operation = {
        "type": "set_castep_energy",
        "task": simulation.task.value,
        "functional": simulation.functional,
        "quality": simulation.quality,
        "cutoff_energy_ev": simulation.cutoff_energy_ev,
        "kpoint_separation": simulation.kpoint_separation,
        "dipole_correction": "Self-consistent",
    }
    configured, diff = apply_semantic_patch(
        heterobilayer,
        SemanticPatch(
            project_id=heterobilayer.project_id,
            base_revision=heterobilayer.revision,
            operations=[operation],
        ),
    )

    assert diff == ["set_castep_energy"]
    assert configured.simulation is not None
    assert configured.simulation.dipole_correction.value == "Self-consistent"
    semiconductor = model_view_audit(configured)["health"]["semiconductor_health"]
    electrostatics = semiconductor["two_dimensional_electrostatic_summary"]
    assert electrostatics["status"] == "dipole_correction_verified_geometry_relaxation_required"
    assert electrostatics["quality"] == "dipole_correction_verified"
    assert electrostatics["dipole_correction_api_verified"] is True
    assert electrostatics["dipole_correction_mode"] == "Self-consistent"
    assert electrostatics["dipole_correction_enabled"] is True
    assert electrostatics["dipole_correction_task"] == "Energy"
    assert electrostatics["dipole_correction_task_compatible"] is True
    assert electrostatics["dipole_correction_vacuum_requirement_met"] is True
    assert electrostatics["dipole_correction_setting_verified"] is True
    assert electrostatics["calculation_review_required"] is False
    assert electrostatics["geometry_relaxation_required"] is True
    assert electrostatics["quantitative_electrostatic_calculation_ready"] is False
    assert electrostatics["calculation_blocking_reasons"] == [
        "commensurate_tmd_heterobilayer_requires_geometry_relaxation"
    ]
    assert semiconductor["surface_model_summary"]["status"] == "ready"

    audit = model_view_audit(configured)
    health = build_modeling_health({"ok": True, "view_audit": audit}, execution_mode="preview")
    assert health["checks"]["semiconductor_2d_dipole_correction_mode"] == "Self-consistent"
    assert health["checks"]["semiconductor_2d_dipole_correction_setting_verified"] is True
    assert health["checks"]["semiconductor_2d_geometry_relaxation_required"] is True
    bundle = write_view_audit_bundle(tmp_path, configured, audit, modeling_health=health)
    rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_2d_electrostatics_csv"]).open(
                encoding="utf-8",
                newline="",
            )
        )
    )
    assert rows[0]["dipole_correction_mode"] == "Self-consistent"
    assert rows[0]["dipole_correction_setting_verified"] == "True"
    assert rows[0]["geometry_relaxation_required"] == "True"
    calculation_rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_calculation_preflight_csv"]).open(
                encoding="utf-8",
                newline="",
            )
        )
    )
    assert calculation_rows[0]["dipole_correction_mode"] == "Self-consistent"
    assert calculation_rows[0]["dipole_correction_enabled"] == "True"
    assert calculation_rows[0]["dipole_correction_api_property"] == "DipoleCorrection"


def test_verified_fixed_cell_castep_relaxation_rebinds_2d_diagnostics(
    tmp_path: Path,
) -> None:
    base = load_example("molybdenum_disulfide_2d_mos2_monolayer_spec.json")
    heterobilayer, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "make_commensurate_tmd_heterobilayer",
                    "top_layer_material": "WSe2",
                    "commensurate_m": 2,
                    "commensurate_n": 1,
                    "interlayer_distance_angstrom": 6.32,
                    "strain_policy": "balanced",
                    "max_strain_percent": 3.0,
                }
            ],
        ),
    )
    assert heterobilayer.simulation is not None
    simulation_payload = heterobilayer.simulation.model_dump(mode="json")
    simulation_payload.update(
        {
            "task": "GeometryOptimization",
            "dipole_correction": "Self-consistent",
            "cell_optimization": "None",
            "max_iterations": 120,
        }
    )
    simulation = CastepEnergySpec.model_validate(simulation_payload)

    parsed_atoms = []
    for atom in heterobilayer.model.basis_atoms:
        fractional = atom.fractional.model_dump(mode="json")
        if atom.element == "Mo" and "_L1_" in atom.id:
            fractional["z"] += 0.0005
        elif atom.element == "W" and "_L2_" in atom.id:
            fractional["z"] -= 0.0005
        parsed_atoms.append(
            {
                "id": atom.id,
                "element": atom.element,
                "fractional": fractional,
            }
        )
    parsed_cif = {
        "ok": True,
        "lattice": heterobilayer.model.lattice.model_dump(mode="json"),
        "atoms": parsed_atoms,
    }
    output_structure = tmp_path / "relaxed.cif"
    output_report = tmp_path / "geometry_optimization.txt"
    output_structure.write_text("verified relaxed CIF artifact\n", encoding="utf-8")
    output_report.write_text("CASTEP geometry optimization converged\n", encoding="utf-8")
    target_revision = heterobilayer.revision + 1
    relaxed, receipt = build_relaxed_revision_spec(
        heterobilayer,
        simulation=simulation,
        parsed_cif=parsed_cif,
        result_payload={
            "converged": True,
            "total_energy_kcal_per_mol": -123.5,
            "enthalpy_kcal_per_mol": -122.75,
        },
        output_structure=output_structure,
        output_report=output_report,
        script_sha256="a" * 64,
        target_revision=target_revision,
    )
    relaxed = relaxed.model_copy(update={"revision": target_revision}, deep=True)

    audit = model_view_audit(relaxed)
    semiconductor = audit["health"]["semiconductor_health"]
    relaxation = semiconductor["castep_geometry_optimization_summary"]
    assert relaxation["quality"] == "fixed_cell_relaxation_verified"
    assert relaxation["transition_verified"] is True
    assert relaxation["fixed_cell_transition_verified"] is True
    assert relaxation["source_binding_verified"] is True
    assert relaxation["output_binding_verified"] is True
    assert relaxation["atom_identity_verified"] is True
    assert relaxation["source_revision"] == heterobilayer.revision
    assert relaxation["target_revision"] == target_revision
    assert receipt["output_structure_sha256"] == relaxation["current_structure_sha256"]

    summary = semiconductor["commensurate_heterobilayer_summary"]
    assert summary["construction_structure_binding_matches_current"] is False
    assert summary["structure_binding_matches_current"] is True
    assert summary["structure_binding_scope"] == (
        "verified_fixed_cell_castep_relaxation_output"
    )
    assert summary["interlayer_distance_verified"] is False
    assert summary["geometry_measurement_binding_verified"] is True
    assert summary["castep_relaxation_transition_verified"] is True
    assert summary["metadata_consistent"] is True
    assert summary["commensurability_verified"] is True
    assert summary["geometry_relaxed"] is True
    assert summary["requires_geometry_relaxation"] is False
    assert summary["calculation_ready"] is True
    assert summary["calculation_blocking_reasons"] == []

    electrostatics = semiconductor["two_dimensional_electrostatic_summary"]
    assert electrostatics["geometry_relaxation_verified"] is True
    assert electrostatics["geometry_relaxation_required"] is False
    assert electrostatics["dipole_correction_setting_verified"] is True
    assert electrostatics["quantitative_electrostatic_calculation_ready"] is True
    assert electrostatics["calculation_blocking_reasons"] == []

    health = build_modeling_health(
        {"ok": True, "view_audit": audit},
        execution_mode="execute",
    )
    assert health["checks"]["semiconductor_castep_relaxation_transition_verified"] is True
    assert health["checks"]["semiconductor_2d_geometry_relaxation_verified"] is True
    bundle = write_view_audit_bundle(
        tmp_path / "bundle",
        relaxed,
        audit,
        modeling_health=health,
    )
    relaxation_rows = list(
        csv.DictReader(
            Path(
                bundle["files"]["semiconductor_castep_geometry_optimization_csv"]
            ).open(encoding="utf-8", newline="")
        )
    )
    assert relaxation_rows[0]["transition_verified"] == "True"
    assert relaxation_rows[0]["fixed_cell_transition_verified"] == "True"
    heterobilayer_rows = list(
        csv.DictReader(
            Path(
                bundle["files"]["semiconductor_commensurate_heterobilayer_csv"]
            ).open(encoding="utf-8", newline="")
        )
    )
    assert heterobilayer_rows[0]["geometry_relaxed"] == "True"
    assert heterobilayer_rows[0]["requires_geometry_relaxation"] == "False"

    forged_payload = relaxed.model_dump(mode="json")
    forged_payload["metadata"]["last_castep_geometry_optimization"][
        "source_structure_sha256"
    ] = "0" * 64
    forged_payload["metadata"]["castep_geometry_optimization_history"][-1][
        "source_structure_sha256"
    ] = "0" * 64
    forged = ModelSpec.model_validate(forged_payload)
    forged_summary = model_view_audit(forged)["health"]["semiconductor_health"][
        "commensurate_heterobilayer_summary"
    ]
    assert forged_summary["castep_relaxation_transition_verified"] is False
    assert forged_summary["metadata_consistent"] is False
    assert forged_summary["requires_geometry_relaxation"] is True

    stale_atoms = list(relaxed.model.basis_atoms)
    target_index = next(
        index
        for index, atom in enumerate(stale_atoms)
        if atom.element == "W" and "_L2_" in atom.id
    )
    target = stale_atoms[target_index]
    stale_atoms[target_index] = target.model_copy(
        update={
            "fractional": target.fractional.model_copy(
                update={"z": target.fractional.z + 0.002}
            )
        }
    )
    stale = relaxed.model_copy(
        update={
            "revision": target_revision + 1,
            "model": relaxed.model.model_copy(update={"basis_atoms": stale_atoms}),
        },
        deep=True,
    )
    stale_relaxation = model_view_audit(stale)["health"]["semiconductor_health"][
        "castep_geometry_optimization_summary"
    ]
    assert stale_relaxation["transition_verified"] is False
    assert stale_relaxation["revision_binding_verified"] is False
    assert stale_relaxation["output_binding_verified"] is False


def test_write_view_audit_bundle_exports_semiconductor_csv_tables(tmp_path: Path) -> None:
    hetero = load_example("gallium_arsenide_aluminum_arsenide_001_heterostructure_spec.json")
    hetero_bundle = write_view_audit_bundle(tmp_path / "hetero", hetero, model_view_audit(hetero))

    assert Path(hetero_bundle["files"]["semiconductor_heterostructure_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_heterostructure"] == 2
    hetero_csv = Path(hetero_bundle["files"]["semiconductor_heterostructure_csv"]).read_text(encoding="utf-8")
    assert "interface,interface_orientation,interface_axis" in hetero_csv
    assert "GaAs/AlAs" in hetero_csv
    assert "AlAs" in hetero_csv
    assert Path(hetero_bundle["files"]["semiconductor_composition_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_composition"] == 3
    composition_csv = Path(hetero_bundle["files"]["semiconductor_composition_csv"]).read_text(encoding="utf-8")
    assert "element,count,atomic_fraction" in composition_csv
    assert "Al,4,0.25,25.0,0.25,25.0,host" in composition_csv
    assert Path(hetero_bundle["files"]["semiconductor_charge_balance_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_charge_balance"] == 3
    charge_csv = Path(hetero_bundle["files"]["semiconductor_charge_balance_csv"]).read_text(encoding="utf-8")
    assert "element,count,role,nominal_valence_electrons" in charge_csv
    assert "As,8,host,5,40,0.625," in charge_csv
    assert Path(hetero_bundle["files"]["semiconductor_calculation_preflight_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_calculation_preflight"] == 1
    calculation_csv = Path(hetero_bundle["files"]["semiconductor_calculation_preflight_csv"]).read_text(encoding="utf-8")
    assert "configured,module,task,functional,quality,status" in calculation_csv
    assert "True,CASTEP,Energy,PBE,Medium,ok,True,520,ok,separation,0.04" in calculation_csv
    assert Path(hetero_bundle["files"]["semiconductor_reciprocal_lattice_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_reciprocal_lattice"] == 3
    reciprocal_csv = Path(hetero_bundle["files"]["semiconductor_reciprocal_lattice_csv"]).read_text(encoding="utf-8")
    assert "axis,real_length_angstrom,reciprocal_length_1_per_angstrom" in reciprocal_csv
    reciprocal_rows = {
        row["axis"]: row for row in csv.DictReader(reciprocal_csv.splitlines())
    }
    assert reciprocal_rows["a"] == {
        "axis": "a",
        "real_length_angstrom": "5.6572",
        "reciprocal_length_1_per_angstrom": "1.110653",
        "configured_kpoint": "",
        "estimated_kpoint_from_separation": "28",
        "recommended_kpoint": "",
        "actual_separation_1_per_angstrom": "0.039666",
        "recommended_separation_1_per_angstrom": "",
        "surface_normal_axis": "False",
        "surface_normal_warning": "False",
        "recommendation_reason_codes": "",
    }
    assert reciprocal_rows["c"]["estimated_kpoint_from_separation"] == "14"
    assert reciprocal_rows["c"]["actual_separation_1_per_angstrom"] == "0.039666"
    assert reciprocal_rows["c"]["recommended_kpoint"] == ""
    assert Path(hetero_bundle["files"]["semiconductor_band_path_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_band_path"] == 10
    band_path_csv = Path(hetero_bundle["files"]["semiconductor_band_path_csv"]).read_text(encoding="utf-8")
    assert "available,task_relevant,structure_family,bravais_lattice,path_label" in band_path_csv
    assert "True,False,zinc blende heterostructure,fcc,Gamma-X-W-K-Gamma-L-U-W-L-K,1,Gamma,0.0,0.0,0.0,X,Gamma-X,True,0," in band_path_csv
    assert Path(hetero_bundle["files"]["semiconductor_band_alignment_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_band_alignment"] == 1
    band_alignment_csv = Path(hetero_bundle["files"]["semiconductor_band_alignment_csv"]).read_text(encoding="utf-8")
    assert "interface,model,reference,reference_material,material,role" in band_alignment_csv
    assert "GaAs/AlAs,electron_affinity_metadata_reference,template_estimate_for_preflight_only,GaAs,AlAs,barrier,4.07,1.42,3.5,2.16,0.57,-0.17,0.57,0.17" in band_alignment_csv
    assert Path(hetero_bundle["files"]["semiconductor_lattice_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_lattice"] == 1
    lattice_csv = Path(hetero_bundle["files"]["semiconductor_lattice_csv"]).read_text(encoding="utf-8")
    assert "a_angstrom,b_angstrom,c_angstrom" in lattice_csv
    assert "362.10506" in lattice_csv
    assert Path(hetero_bundle["files"]["semiconductor_neighbor_pairs_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_neighbor_pairs"] == 2
    neighbor_csv = Path(hetero_bundle["files"]["semiconductor_neighbor_pairs_csv"]).read_text(encoding="utf-8")
    assert "pair_type,pair_role,count,min_distance_angstrom" in neighbor_csv
    assert "Al-As,expected,16,2.449084" in neighbor_csv
    assert "Ga-As,expected,16,2.449078" in neighbor_csv
    health_summary_csv = Path(hetero_bundle["files"]["modeling_health_summary_csv"]).read_text(encoding="utf-8")
    assert "semiconductor_coordination_excluded_neighbor_pair_count" in health_summary_csv
    assert "semiconductor_coordination_excluded_pair_types" in health_summary_csv
    assert Path(hetero_bundle["files"]["semiconductor_local_environment_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_local_environment"] == 16
    local_environment_csv = Path(hetero_bundle["files"]["semiconductor_local_environment_csv"]).read_text(encoding="utf-8")
    assert "atom_id,element,neighbor_count,expected_coordination" in local_environment_csv
    assert "Al1,Al,4,4,False,2.449084" in local_environment_csv
    assert Path(hetero_bundle["files"]["semiconductor_sublattice_balance_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_sublattice_balance"] == 2
    sublattice_csv = Path(hetero_bundle["files"]["semiconductor_sublattice_balance_csv"]).read_text(encoding="utf-8")
    assert "iii_v_cation_like_elements,Al;Ga,8,0.5,iii_v_cation_anion_count,0,True,False" in sublattice_csv
    assert Path(hetero_bundle["files"]["semiconductor_layer_profile_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_layer_profile"] == 8
    layer_csv = Path(hetero_bundle["files"]["semiconductor_layer_profile_csv"]).read_text(encoding="utf-8")
    assert "layer_index,axis,fractional_center" in layer_csv
    assert '"{""Ga"": 2}"' in layer_csv
    assert Path(hetero_bundle["files"]["semiconductor_interface_profile_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_interface_profile"] == 8
    interface_csv = Path(hetero_bundle["files"]["semiconductor_interface_profile_csv"]).read_text(encoding="utf-8")
    assert "layer_index,axis,fractional_center,axis_coordinate_angstrom,layer_role" in interface_csv
    assert "3,c,0.249828,2.826654,iii_v_cation_layer,Ga,1,False,True" in interface_csv
    assert "5,c,0.499657,5.653319,iii_v_cation_layer,Al,2,True,False" in interface_csv
    assert Path(hetero_bundle["files"]["semiconductor_interface_quality_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_interface_quality"] == 2
    interface_quality_csv = Path(hetero_bundle["files"]["semiconductor_interface_quality_csv"]).read_text(encoding="utf-8")
    assert "segment_index,period_index,segment_in_period,axis,material,role,expected_material" in interface_quality_csv
    assert "1,1,1,c,GaAs,well,GaAs,True" in interface_quality_csv
    assert "2,1,2,c,AlAs,barrier,AlAs,True" in interface_quality_csv
    assert Path(hetero_bundle["files"]["semiconductor_quantum_well_csv"]).exists()
    assert hetero_bundle["row_counts"]["semiconductor_quantum_well"] == 2
    quantum_well_csv = Path(hetero_bundle["files"]["semiconductor_quantum_well_csv"]).read_text(encoding="utf-8")
    assert "segment_index,period_index,segment_in_period,axis,material,material_marker,role" in quantum_well_csv
    assert "1,1,1,c,GaAs,Ga,well,,,,,,,1,4,4,2,8,8,0" in quantum_well_csv
    assert "2,1,2,c,AlAs,Al,barrier,,,,,,,5,8,4,2,8,8,0" in quantum_well_csv

    algan = load_example("aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json")
    algan_bundle = write_view_audit_bundle(tmp_path / "algan_gan", algan, model_view_audit(algan))
    assert Path(algan_bundle["files"]["semiconductor_polarization_2deg_csv"]).exists()
    assert algan_bundle["row_counts"]["semiconductor_polarization_2deg"] == 1
    polarization_csv = Path(algan_bundle["files"]["semiconductor_polarization_2deg_csv"]).read_text(encoding="utf-8")
    assert "interface,model,reference,well_material,barrier_material" in polarization_csv
    assert "GaN/Al0.25Ga0.75N,iii_nitride_polarization_2deg_metadata_preflight" in polarization_csv
    assert "GaN,Al0.25Ga0.75N,0.25,0.0" in polarization_csv
    assert "0.45,True,complete,0," in polarization_csv

    p_gan_plan = infer_modeling_plan("Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG diagnostics.")
    p_gan = ModelSpec.model_validate(p_gan_plan.payload)
    p_gan_bundle = write_view_audit_bundle(tmp_path / "p_gan_hemt", p_gan, model_view_audit(p_gan))
    assert Path(p_gan_bundle["files"]["semiconductor_p_gan_gate_cap_csv"]).exists()
    assert p_gan_bundle["row_counts"]["semiconductor_p_gan_gate_cap"] == 4
    p_gan_cap_csv = Path(p_gan_bundle["files"]["semiconductor_p_gan_gate_cap_csv"]).read_text(encoding="utf-8")
    assert "material,role,quality,cap_layer_index,global_layer_index" in p_gan_cap_csv
    assert "p-GaN,gate_cap,complete,1,9" in p_gan_cap_csv
    assert "MgPGaN1_1" in p_gan_cap_csv

    mos = load_example("aluminum_silicon_dioxide_silicon_mos_capacitor_spec.json")
    mos_audit = model_view_audit(mos)
    gate_stack = mos_audit["health"]["semiconductor_health"]["gate_stack_summary"]
    assert gate_stack["quality"] == "complete"
    assert mos_audit["health"]["semiconductor_health"]["band_alignment_summary"] is None
    assert gate_stack["material_sequence"] == ["Si", "SiO2", "Al"]
    assert gate_stack["sequence_matches_expected"] is True
    assert gate_stack["role_counts"] == {"channel": 1, "gate": 1, "oxide": 1}
    assert gate_stack["declared_oxide_thickness_angstrom"] == 4.84
    assert gate_stack["declared_gate_thickness_angstrom"] == 1.68
    assert gate_stack["gate_center_span_angstrom"] == 1.68
    mos_bundle = write_view_audit_bundle(tmp_path / "mos", mos, mos_audit)
    assert Path(mos_bundle["files"]["semiconductor_gate_stack_csv"]).exists()
    assert mos_bundle["row_counts"]["semiconductor_gate_stack"] == 3
    gate_stack_csv = Path(mos_bundle["files"]["semiconductor_gate_stack_csv"]).read_text(encoding="utf-8")
    assert "segment_index,axis,interface,material,role,expected_stack_sequence" in gate_stack_csv
    assert "Si,channel,Si;SiO2;Al,Si;SiO2;Al,True,complete" in gate_stack_csv
    assert "Al,gate,Si;SiO2;Al,Si;SiO2;Al,True,complete" in gate_stack_csv

    high_k = load_example("titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json")
    high_k_audit = model_view_audit(high_k)
    high_k_gate_stack = high_k_audit["health"]["semiconductor_health"]["gate_stack_summary"]
    assert high_k_gate_stack["quality"] == "complete"
    assert high_k_gate_stack["material_sequence"] == ["Si", "HfO2", "TiN"]
    assert high_k_gate_stack["expected_stack_sequence"] == ["Si", "HfO2", "TiN"]
    assert high_k_gate_stack["sequence_matches_expected"] is True
    assert high_k_gate_stack["role_counts"] == {"channel": 1, "gate": 1, "oxide": 1}
    assert high_k_gate_stack["gate_material"] == "TiN"
    assert high_k_gate_stack["gate_oxide_material"] == "HfO2"
    high_k_bundle = write_view_audit_bundle(tmp_path / "high_k_mos", high_k, high_k_audit)
    assert Path(high_k_bundle["files"]["semiconductor_gate_stack_csv"]).exists()
    assert high_k_bundle["row_counts"]["semiconductor_gate_stack"] == 3
    high_k_gate_stack_csv = Path(high_k_bundle["files"]["semiconductor_gate_stack_csv"]).read_text(encoding="utf-8")
    assert "HfO2,oxide,Si;HfO2;TiN,Si;HfO2;TiN,True,complete" in high_k_gate_stack_csv
    assert "TiN,gate,Si;HfO2;TiN,Si;HfO2;TiN,True,complete" in high_k_gate_stack_csv

    schottky = load_example("aluminum_silicon_100_schottky_contact_spec.json")
    schottky_audit = model_view_audit(schottky)
    contact = schottky_audit["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]
    assert contact["quality"] == "complete"
    assert contact["contact_type"] == "schottky"
    assert contact["material_sequence"] == ["Si", "Al"]
    assert contact["expected_contact_sequence"] == ["Si", "Al"]
    assert contact["sequence_matches_expected"] is True
    assert contact["metal_material"] == "Al"
    assert contact["semiconductor_material"] == "Si"
    assert contact["declared_contact_gap_angstrom"] == 2.52
    assert contact["actual_contact_gap_angstrom"] == 2.52
    assert contact["contact_gap_delta_angstrom"] == 0.0
    assert contact["contact_geometry_status"] == "matched"
    assert contact["contact_geometry_next_action"] == "geometry_matches_declared_contact_metadata"
    assert contact["declared_metal_thickness_angstrom"] == 1.68
    assert contact["actual_metal_thickness_angstrom"] == 1.68
    assert contact["metal_thickness_delta_angstrom"] == 0.0
    barrier = contact["barrier_preflight"]
    assert barrier["model"] == "ideal_schottky_mott_metadata_reference"
    assert barrier["metal_work_function_ev"] == 4.28
    assert barrier["semiconductor_electron_affinity_ev"] == 4.05
    assert barrier["semiconductor_band_gap_ev"] == 1.12
    assert barrier["ideal_n_type_barrier_ev"] == 0.23
    assert barrier["ideal_p_type_barrier_ev"] == 0.89
    assert barrier["warning_count"] == 0
    assert schottky_audit["health"]["semiconductor_health"]["quantum_well_summary"] is None
    assert schottky_audit["health"]["semiconductor_health"]["surface_termination_summary"] is None
    schottky_bundle = write_view_audit_bundle(tmp_path / "schottky", schottky, schottky_audit)
    assert Path(schottky_bundle["files"]["semiconductor_contact_csv"]).exists()
    assert schottky_bundle["row_counts"]["semiconductor_contact"] == 2
    assert "semiconductor_quantum_well" not in schottky_bundle["row_counts"]
    contact_csv = Path(schottky_bundle["files"]["semiconductor_contact_csv"]).read_text(encoding="utf-8")
    assert "segment_index,axis,interface,contact_type,material,role,expected_contact_sequence" in contact_csv
    assert (
        "barrier_model,metal_work_function_ev,semiconductor_electron_affinity_ev,"
        "semiconductor_band_gap_ev,ideal_n_type_barrier_ev,ideal_p_type_barrier_ev"
    ) in contact_csv
    assert "actual_contact_gap_angstrom" in contact_csv
    assert "contact_gap_delta_angstrom" in contact_csv
    assert "contact_geometry_status" in contact_csv
    assert "contact_geometry_next_action" in contact_csv
    assert "actual_metal_thickness_angstrom" in contact_csv
    assert "metal_thickness_delta_angstrom" in contact_csv
    assert "Si,semiconductor,Si;Al,Si;Al,True,complete,Al,Si" in contact_csv
    assert "Al,metal,Si;Al,Si;Al,True,complete,Al,Si" in contact_csv
    assert "ideal_schottky_mott_metadata_reference,4.28,4.05,1.12,0.23,0.89,0," in contact_csv

    plan = infer_modeling_plan(
        "Set metal work function to 4.60 eV, Si electron affinity to 4.00 eV, "
        "band gap to 1.20 eV, and interface gap to 0.24 nm.",
        current_spec=schottky,
    )
    assert plan.kind == "patch"
    assert plan.template_id == "metal_semiconductor_contact_parameters"
    updated, diff = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=plan.payload["operations"],
        ),
    )
    assert "set_metadata interface_gap_angstrom,last_contact_parameter_update,metal_work_function_ev,semiconductor_band_gap_ev,semiconductor_electron_affinity_ev" in diff
    assert schottky.metadata["metal_work_function_ev"] == 4.28
    assert updated.metadata["metal_work_function_ev"] == 4.6
    assert updated.metadata["semiconductor_electron_affinity_ev"] == 4.0
    assert updated.metadata["semiconductor_band_gap_ev"] == 1.2
    assert updated.metadata["interface_gap_angstrom"] == 2.4
    assert updated.metadata["last_contact_parameter_update"] == {
        "source": "natural_language_metal_semiconductor_contact_parameters",
        "updated_keys": [
            "interface_gap_angstrom",
            "metal_work_function_ev",
            "semiconductor_band_gap_ev",
            "semiconductor_electron_affinity_ev",
        ],
    }

    updated_contact = model_view_audit(updated)["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]
    assert updated_contact["declared_contact_gap_angstrom"] == 2.4
    assert updated_contact["actual_contact_gap_angstrom"] == 2.52
    assert updated_contact["contact_gap_delta_angstrom"] == 0.12
    assert updated_contact["contact_geometry_status"] == "mismatch"
    assert (
        updated_contact["contact_geometry_next_action"]
        == "apply_contact_gap_or_thickness_geometry_patch_before_claiming_normality"
    )
    assert updated_contact["quality"] == "complete_with_warnings"
    assert "Declared contact gap differs from inferred geometry; inspect actual_contact_gap_angstrom." in updated_contact["warnings"]
    updated_barrier = updated_contact["barrier_preflight"]
    assert updated_barrier["metal_work_function_ev"] == 4.6
    assert updated_barrier["semiconductor_electron_affinity_ev"] == 4.0
    assert updated_barrier["semiconductor_band_gap_ev"] == 1.2
    assert updated_barrier["ideal_n_type_barrier_ev"] == 0.6
    assert updated_barrier["ideal_p_type_barrier_ev"] == 0.6
    cjk_plan = infer_modeling_plan(
        "\u628a\u91d1\u5c5e\u529f\u51fd\u6570\u6539\u4e3a4.55 eV\uff0c"
        "\u7535\u5b50\u4eb2\u548c\u52bf\u6539\u4e3a4.10 eV\uff0c"
        "\u5e26\u9699\u6539\u4e3a1.15 eV\uff0c"
        "\u754c\u9762\u95f4\u8ddd\u6539\u4e3a2.5\u57c3",
        current_spec=schottky,
    )
    assert cjk_plan.kind == "patch"
    cjk_updates = cjk_plan.payload["operations"][0]["metadata_updates"]
    assert cjk_updates["metal_work_function_ev"] == 4.55
    assert cjk_updates["semiconductor_electron_affinity_ev"] == 4.1
    assert cjk_updates["semiconductor_band_gap_ev"] == 1.15
    assert cjk_updates["interface_gap_angstrom"] == 2.5

    n_barrier_plan = infer_modeling_plan(
        "Set n-type Schottky barrier to 0.45 eV.",
        current_spec=schottky,
    )
    assert n_barrier_plan.kind == "patch"
    assert n_barrier_plan.template_id == "metal_semiconductor_contact_parameters"
    n_barrier_updates = n_barrier_plan.payload["operations"][0]["metadata_updates"]
    assert n_barrier_updates["metal_work_function_ev"] == 4.5
    assert n_barrier_updates["target_schottky_barrier"] == {
        "carrier_type": "n_type",
        "target_barrier_ev": 0.45,
        "derived_metal_work_function_ev": 4.5,
        "semiconductor_electron_affinity_ev": 4.05,
        "semiconductor_band_gap_ev": 1.12,
        "source": "natural_language_schottky_barrier_target",
    }
    n_barrier_spec, _ = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=n_barrier_plan.payload["operations"],
        ),
    )
    n_barrier = model_view_audit(n_barrier_spec)["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]["barrier_preflight"]
    assert n_barrier["metal_work_function_ev"] == 4.5
    assert n_barrier["ideal_n_type_barrier_ev"] == 0.45
    assert n_barrier["ideal_p_type_barrier_ev"] == 0.67

    p_barrier_plan = infer_modeling_plan(
        "Set p-type Schottky barrier to 0.30 eV.",
        current_spec=schottky,
    )
    p_updates = p_barrier_plan.payload["operations"][0]["metadata_updates"]
    assert p_updates["metal_work_function_ev"] == 4.87
    assert p_updates["target_schottky_barrier"]["carrier_type"] == "p_type"
    p_barrier_spec, _ = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=p_barrier_plan.payload["operations"],
        ),
    )
    p_barrier = model_view_audit(p_barrier_spec)["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]["barrier_preflight"]
    assert p_barrier["ideal_n_type_barrier_ev"] == 0.82
    assert p_barrier["ideal_p_type_barrier_ev"] == 0.3

    cjk_barrier_plan = infer_modeling_plan(
        "\u628an\u578b\u8096\u7279\u57fa\u52bf\u5792\u6539\u4e3a0.5 eV",
        current_spec=schottky,
    )
    cjk_barrier_updates = cjk_barrier_plan.payload["operations"][0]["metadata_updates"]
    assert cjk_barrier_updates["metal_work_function_ev"] == 4.55
    assert cjk_barrier_updates["target_schottky_barrier"]["carrier_type"] == "n_type"
    assert cjk_barrier_updates["target_schottky_barrier"]["target_barrier_ev"] == 0.5

    metal_plan = infer_modeling_plan(
        "Change the metal contact to Au.",
        current_spec=schottky,
    )
    assert metal_plan.kind == "patch"
    assert metal_plan.template_id == "metal_semiconductor_contact_metal"
    assert len(metal_plan.payload["operations"]) == 9
    assert metal_plan.payload["operations"][0] == {
        "type": "substitute_atom",
        "atom_id": "AlContact1",
        "new_element": "Au",
    }
    metal_updates = metal_plan.payload["operations"][-1]["metadata_updates"]
    assert metal_updates["metal_contact_material"] == "Au"
    assert metal_updates["materials"] == ["Si", "Au"]
    assert metal_updates["stack_sequence"] == ["Si", "Au"]
    assert metal_updates["interface"] == "Au/Si"
    assert metal_updates["metal_work_function_ev"] == 5.1
    assert metal_updates["last_contact_metal_replacement"]["replaced_atom_count"] == 8
    au_contact, au_diff = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=metal_plan.payload["operations"],
        ),
    )
    assert "substitute_atom AlContact1->Au" in au_diff
    assert schottky.metadata["metal_contact_material"] == "Al"
    assert au_contact.metadata["metal_contact_material"] == "Au"
    au_audit = model_view_audit(au_contact)
    assert au_audit["model"]["elements"] == {"Au": 8, "Si": 8}
    au_summary = au_audit["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]
    assert au_summary["metal_material"] == "Au"
    assert au_summary["material_sequence"] == ["Si", "Au"]
    assert au_summary["expected_contact_sequence"] == ["Si", "Au"]
    assert au_summary["sequence_matches_expected"] is True
    assert au_summary["barrier_preflight"]["metal_work_function_ev"] == 5.1
    assert au_summary["barrier_preflight"]["ideal_n_type_barrier_ev"] == 1.05
    assert au_summary["barrier_preflight"]["ideal_p_type_barrier_ev"] == 0.07

    cjk_metal_plan = infer_modeling_plan(
        "\u628a\u63a5\u89e6\u91d1\u5c5e\u6539\u4e3a\u94c2",
        current_spec=schottky,
    )
    assert cjk_metal_plan.kind == "patch"
    assert cjk_metal_plan.template_id == "metal_semiconductor_contact_metal"
    assert cjk_metal_plan.payload["operations"][-1]["metadata_updates"]["metal_contact_material"] == "Pt"

    gap_plan = infer_modeling_plan(
        "Set the interface gap to 3.0 angstrom.",
        current_spec=schottky,
    )
    assert gap_plan.kind == "patch"
    assert gap_plan.template_id == "metal_semiconductor_contact_gap"
    assert len(gap_plan.payload["operations"]) == 9
    assert gap_plan.payload["operations"][0] == {
        "type": "set_atom_position",
        "atom_id": "AlContact1",
        "fractional": [0.0, 0.0, 0.457143],
    }
    gap_updates = gap_plan.payload["operations"][-1]["metadata_updates"]
    assert gap_updates["interface_gap_angstrom"] == 3.0
    assert gap_updates["last_contact_gap_adjustment"]["previous_gap_angstrom"] == 2.52
    assert gap_updates["last_contact_gap_adjustment"]["delta_angstrom"] == 0.48
    gap_contact, _ = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=gap_plan.payload["operations"],
        ),
    )
    assert next(atom for atom in schottky.model.basis_atoms if atom.id == "AlContact1").fractional.z == 0.44
    assert next(atom for atom in gap_contact.model.basis_atoms if atom.id == "AlContact1").fractional.z == 0.457143
    assert next(atom for atom in gap_contact.model.basis_atoms if atom.id == "AlContact5").fractional.z == 0.517143
    gap_summary = model_view_audit(gap_contact)["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]
    assert gap_summary["declared_contact_gap_angstrom"] == 3.0
    assert abs(gap_summary["actual_contact_gap_angstrom"] - 3.0) < 1e-4
    assert abs(gap_summary["contact_gap_delta_angstrom"]) < 1e-4
    assert gap_summary["contact_geometry_status"] == "matched"
    assert gap_summary["contact_geometry_next_action"] == "geometry_matches_declared_contact_metadata"

    cjk_gap_plan = infer_modeling_plan(
        "\u628a\u754c\u9762\u95f4\u8ddd\u6539\u4e3a3.2\u57c3",
        current_spec=schottky,
    )
    assert cjk_gap_plan.kind == "patch"
    assert cjk_gap_plan.template_id == "metal_semiconductor_contact_gap"
    assert cjk_gap_plan.payload["operations"][-1]["metadata_updates"]["interface_gap_angstrom"] == 3.2

    thickness_plan = infer_modeling_plan(
        "Set metal contact thickness to 2.8 angstrom.",
        current_spec=schottky,
    )
    assert thickness_plan.kind == "patch"
    assert thickness_plan.template_id == "metal_semiconductor_contact_thickness"
    assert len(thickness_plan.payload["operations"]) == 9
    assert thickness_plan.payload["operations"][0] == {
        "type": "set_atom_position",
        "atom_id": "AlContact1",
        "fractional": [0.0, 0.0, 0.44],
    }
    assert thickness_plan.payload["operations"][4] == {
        "type": "set_atom_position",
        "atom_id": "AlContact5",
        "fractional": [0.0, 0.5, 0.54],
    }
    thickness_updates = thickness_plan.payload["operations"][-1]["metadata_updates"]
    assert thickness_updates["metal_contact_thickness_angstrom"] == 2.8
    assert thickness_updates["last_contact_thickness_adjustment"]["previous_thickness_angstrom"] == 1.68
    assert thickness_updates["last_contact_thickness_adjustment"]["delta_angstrom"] == 1.12
    thick_contact, _ = apply_semantic_patch(
        schottky,
        SemanticPatch(
            project_id=schottky.project_id,
            base_revision=schottky.revision,
            operations=thickness_plan.payload["operations"],
        ),
    )
    assert next(atom for atom in thick_contact.model.basis_atoms if atom.id == "AlContact1").fractional.z == 0.44
    assert next(atom for atom in thick_contact.model.basis_atoms if atom.id == "AlContact5").fractional.z == 0.54
    thick_summary = model_view_audit(thick_contact)["health"]["semiconductor_health"]["metal_semiconductor_contact_summary"]
    assert thick_summary["declared_contact_gap_angstrom"] == 2.52
    assert thick_summary["actual_contact_gap_angstrom"] == 2.52
    assert thick_summary["declared_metal_thickness_angstrom"] == 2.8
    assert abs(thick_summary["actual_metal_thickness_angstrom"] - 2.8) < 1e-4
    assert abs(thick_summary["metal_thickness_delta_angstrom"]) < 1e-4
    assert thick_summary["contact_geometry_status"] == "matched"
    assert thick_summary["contact_geometry_next_action"] == "geometry_matches_declared_contact_metadata"

    cjk_thickness_plan = infer_modeling_plan(
        "\u628a\u91d1\u5c5e\u63a5\u89e6\u5c42\u539a\u5ea6\u6539\u4e3a2.4\u57c3",
        current_spec=schottky,
    )
    assert cjk_thickness_plan.kind == "patch"
    assert cjk_thickness_plan.template_id == "metal_semiconductor_contact_thickness"
    assert cjk_thickness_plan.payload["operations"][-1]["metadata_updates"]["metal_contact_thickness_angstrom"] == 2.4

    silicon = load_example("silicon_diamond_spec.json")
    doped, _ = apply_semantic_patch(
        silicon,
        SemanticPatch(
            project_id=silicon.project_id,
            base_revision=silicon.revision,
            operations=[
                {"type": "make_supercell", "matrix": [2, 1, 1]},
                {"type": "substitute_atom", "atom_id": "Si1_000", "new_element": "P"},
                {
                    "type": "set_metadata",
                    "metadata_updates": {
                        "semiconductor_carrier_intents": [
                            {
                                "carrier_type": "n_type",
                                "dopant_element": "P",
                                "source": "test",
                            }
                        ],
                        "last_semiconductor_carrier_intent": {
                            "carrier_type": "n_type",
                            "dopant_element": "P",
                            "source": "test",
                        },
                    },
                },
            ],
        ),
    )
    doped_bundle = write_view_audit_bundle(tmp_path / "doped", doped, model_view_audit(doped))
    assert Path(doped_bundle["files"]["semiconductor_dopants_csv"]).exists()
    assert doped_bundle["row_counts"]["semiconductor_dopants"] == 1
    dopant_csv = Path(doped_bundle["files"]["semiconductor_dopants_csv"]).read_text(encoding="utf-8")
    assert "host_elements,dopant_element,count,atom_ids" in dopant_csv
    assert "donor_like_n_type_for_group_iv_host" in dopant_csv
    assert Path(doped_bundle["files"]["semiconductor_dopant_concentration_csv"]).exists()
    assert doped_bundle["row_counts"]["semiconductor_dopant_concentration"] == 2
    dopant_concentration_csv = Path(
        doped_bundle["files"]["semiconductor_dopant_concentration_csv"]
    ).read_text(encoding="utf-8")
    assert "row_type,dopant_element,count,atom_ids,concentration_fraction" in dopant_concentration_csv
    assert "density_cm3" in dopant_concentration_csv
    assert "very_high" in dopant_concentration_csv
    assert "increase_supercell_or_reduce_dopant_count_before_quantitative_semiconductor_claims" in dopant_concentration_csv
    doped_composition_csv = Path(doped_bundle["files"]["semiconductor_composition_csv"]).read_text(encoding="utf-8")
    assert "P,1,0.0625,6.25,0.0625,6.25,dopant" in doped_composition_csv
    doped_charge_csv = Path(doped_bundle["files"]["semiconductor_charge_balance_csv"]).read_text(encoding="utf-8")
    assert "P,1,dopant,5,5,0.076923,1.0" in doped_charge_csv
    doped_calculation_csv = Path(doped_bundle["files"]["semiconductor_calculation_preflight_csv"]).read_text(encoding="utf-8")
    assert "True,CASTEP,Energy,PBE,Medium,ok,True,520,ok,separation,0.04" in doped_calculation_csv
    assert Path(doped_bundle["files"]["semiconductor_finite_size_csv"]).exists()
    assert doped_bundle["row_counts"]["semiconductor_finite_size"] == 1
    doped_finite_csv = Path(doped_bundle["files"]["semiconductor_finite_size_csv"]).read_text(encoding="utf-8")
    assert "non_passivant_atom_count,min_lattice_length_angstrom,max_isolated_fraction" in doped_finite_csv
    assert "16,5.431,0.0625,dopant,P,64,0.03,True,True,True" in doped_finite_csv
    assert Path(doped_bundle["files"]["semiconductor_carrier_intents_csv"]).exists()
    assert doped_bundle["row_counts"]["semiconductor_carrier_intents"] == 1
    carrier_csv = Path(doped_bundle["files"]["semiconductor_carrier_intents_csv"]).read_text(encoding="utf-8")
    assert "requested_carrier_type,requested_carrier_mechanism,requested_dopant_element" in carrier_csv
    assert "n_type,dopant,P" in carrier_csv
    assert "donor_like_n_type" in carrier_csv

    gaas = load_example("gallium_arsenide_zincblende_spec.json")
    gaas_plan = infer_modeling_plan("Make n-type GaAs by doping Ga1 with Si.", current_spec=gaas)
    gaas_doped, _ = apply_semantic_patch(
        gaas,
        SemanticPatch(
            project_id=gaas.project_id,
            base_revision=gaas.revision,
            operations=gaas_plan.payload["operations"],
        ),
    )
    gaas_bundle = write_view_audit_bundle(tmp_path / "gaas_doped", gaas_doped, model_view_audit(gaas_doped))
    assert Path(gaas_bundle["files"]["semiconductor_dopant_sites_csv"]).exists()
    assert gaas_bundle["row_counts"]["semiconductor_dopant_sites"] == 1
    dopant_site_csv = Path(gaas_bundle["files"]["semiconductor_dopant_sites_csv"]).read_text(encoding="utf-8")
    assert "site_id,site_element,dopant_element,site_family" in dopant_site_csv
    assert "Ga1,Ga,Si,iii_v_cation" in dopant_site_csv
    assert "donor_like_n_type_on_iii_v_cation_site" in dopant_site_csv

    supercell_for_vacancy, _ = apply_semantic_patch(
        silicon,
        SemanticPatch(
            project_id=silicon.project_id,
            base_revision=silicon.revision,
            operations=[{"type": "make_supercell", "matrix": [2, 1, 1]}],
        ),
    )
    plan = infer_modeling_plan("Create vacancy at Si1_000.", current_spec=supercell_for_vacancy)
    vacancy, _ = apply_semantic_patch(
        supercell_for_vacancy,
        SemanticPatch(
            project_id=supercell_for_vacancy.project_id,
            base_revision=supercell_for_vacancy.revision,
            operations=plan.payload["operations"],
        ),
    )
    vacancy_bundle = write_view_audit_bundle(tmp_path / "vacancy", vacancy, model_view_audit(vacancy))
    assert Path(vacancy_bundle["files"]["semiconductor_defects_csv"]).exists()
    assert vacancy_bundle["row_counts"]["semiconductor_defects"] == 1
    defect_csv = Path(vacancy_bundle["files"]["semiconductor_defects_csv"]).read_text(encoding="utf-8")
    assert "defect_type,site_id,site_element" in defect_csv
    assert "vacancy,Si1_000,Si" in defect_csv

    interstitial_plan = infer_modeling_plan("Add Si interstitial at fractional 0.5 0.5 0.5.", current_spec=silicon)
    interstitial, _ = apply_semantic_patch(
        silicon,
        SemanticPatch(
            project_id=silicon.project_id,
            base_revision=silicon.revision,
            operations=interstitial_plan.payload["operations"],
        ),
    )
    interstitial_bundle = write_view_audit_bundle(tmp_path / "interstitial", interstitial, model_view_audit(interstitial))
    assert Path(interstitial_bundle["files"]["semiconductor_defects_csv"]).exists()
    assert interstitial_bundle["row_counts"]["semiconductor_defects"] == 1
    interstitial_csv = Path(interstitial_bundle["files"]["semiconductor_defects_csv"]).read_text(encoding="utf-8")
    assert "interstitial_neighbor_count" in interstitial_csv
    assert "coordination_outlier" in interstitial_csv
    assert "interstitial,Si9,Si" in interstitial_csv

    gaas = load_example("gallium_arsenide_zincblende_spec.json")
    antisite_plan = infer_modeling_plan("Create As antisite at Ga1.", current_spec=gaas)
    antisite, _ = apply_semantic_patch(
        gaas,
        SemanticPatch(
            project_id=gaas.project_id,
            base_revision=gaas.revision,
            operations=antisite_plan.payload["operations"],
        ),
    )
    antisite_bundle = write_view_audit_bundle(tmp_path / "antisite", antisite, model_view_audit(antisite))
    assert Path(antisite_bundle["files"]["semiconductor_defects_csv"]).exists()
    assert antisite_bundle["row_counts"]["semiconductor_defects"] == 1
    antisite_csv = Path(antisite_bundle["files"]["semiconductor_defects_csv"]).read_text(encoding="utf-8")
    assert "original_element,new_element" in antisite_csv
    assert "antisite,Ga1,Ga,iii_v_cation,Ga,As" in antisite_csv

    slab = load_example("silicon_100_slab_spec.json")
    slab_bundle = write_view_audit_bundle(tmp_path / "slab", slab, model_view_audit(slab))
    assert Path(slab_bundle["files"]["semiconductor_surface_model_csv"]).exists()
    assert slab_bundle["row_counts"]["semiconductor_surface_model"] == 1
    surface_model_csv = Path(slab_bundle["files"]["semiconductor_surface_model_csv"]).read_text(encoding="utf-8")
    assert "status,ready_for_calculation_preflight,next_action" in surface_model_csv
    assert "blocked,False,center_slab_or_review_asymmetric_vacuum_before_claiming_normality" in surface_model_csv
    assert Path(slab_bundle["files"]["semiconductor_surface_termination_csv"]).exists()
    assert slab_bundle["row_counts"]["semiconductor_surface_termination"] == 4
    surface_csv = Path(slab_bundle["files"]["semiconductor_surface_termination_csv"]).read_text(encoding="utf-8")
    assert "surface,surface_orientation,surface_axis,termination" in surface_csv
    assert "unpassivated" in surface_csv
    assert "surface_preparation_status" in surface_csv
    assert "dangling_bonds,passivate_surface_dangling_bonds_before_calculation_or_claiming_normality" in surface_csv
    lattice_csv = Path(slab_bundle["files"]["semiconductor_lattice_csv"]).read_text(encoding="utf-8")
    assert "slab_vacuum_status,slab_vacuum_next_action,centered_in_cell" in lattice_csv
    assert "off_center,center_slab_or_review_asymmetric_vacuum_before_claiming_normality" in lattice_csv
    assert Path(slab_bundle["files"]["semiconductor_surface_polarity_csv"]).exists()
    assert slab_bundle["row_counts"]["semiconductor_surface_polarity"] == 1
    polarity_csv = Path(slab_bundle["files"]["semiconductor_surface_polarity_csv"]).read_text(encoding="utf-8")
    assert "surface_orientation,surface_axis,termination,bottom_formula,top_formula" in polarity_csv
    assert "surface_polarity_status" in polarity_csv
    assert "(100),c,unpassivated,Si2,Si2,2,2,4,4,0,0,True,True,False,False" in polarity_csv


def test_model_view_audit_flags_unrealistically_close_atoms() -> None:
    spec = ModelSpec.model_validate(
        {
            "project_id": "bad_close_atoms",
            "model_type": "molecule",
            "model": {
                "name": "bad",
                "atoms": [
                    {"id": "H1", "element": "H", "xyz_angstrom": [0, 0, 0]},
                    {"id": "H2", "element": "H", "xyz_angstrom": [0.1, 0, 0]},
                ],
                "bonds": [],
            },
        }
    )

    audit = model_view_audit(spec)
    assert audit["health"]["ok"] is False
    assert "minimum pair distance" in audit["health"]["errors"][0]


def test_model_view_audit_flags_common_over_coordination() -> None:
    spec = ModelSpec.model_validate(
        {
            "project_id": "bad_hypervalent_h",
            "model_type": "molecule",
            "model": {
                "name": "bad_h",
                "atoms": [
                    {"id": "H1", "element": "H", "xyz_angstrom": [0, 0, 0]},
                    {"id": "C1", "element": "C", "xyz_angstrom": [1.1, 0, 0]},
                    {"id": "C2", "element": "C", "xyz_angstrom": [-1.1, 0, 0]},
                ],
                "bonds": [
                    {"atom1": "H1", "atom2": "C1", "type": "Single"},
                    {"atom1": "H1", "atom2": "C2", "type": "Single"},
                ],
            },
        }
    )

    audit = model_view_audit(spec)
    assert audit["health"]["ok"] is False
    assert any("H1" in error and "over-coordinated" in error for error in audit["health"]["errors"])
    h1 = next(item for item in audit["health"]["atom_connectivity"] if item["atom_id"] == "H1")
    assert h1["degree"] == 2
    assert h1["bond_order_sum"] == 2.0


def test_model_view_audit_warns_for_nonbonded_close_contacts() -> None:
    spec = ModelSpec.model_validate(
        {
            "project_id": "close_nonbonded",
            "model_type": "molecule",
            "model": {
                "name": "close",
                "atoms": [
                    {"id": "C1", "element": "C", "xyz_angstrom": [0, 0, 0]},
                    {"id": "C2", "element": "C", "xyz_angstrom": [0.9, 0, 0]},
                    {"id": "H1", "element": "H", "xyz_angstrom": [2.0, 0, 0]},
                ],
                "bonds": [{"atom1": "C2", "atom2": "H1", "type": "Single"}],
            },
        }
    )

    audit = model_view_audit(spec)
    assert audit["health"]["ok"] is True
    assert audit["health"]["nonbonded_close_contacts"][0]["atom1"] == "C1"
    assert audit["health"]["nonbonded_close_contacts"][0]["atom2"] == "C2"
    assert any("non-bonded atoms" in warning for warning in audit["health"]["warnings"])
