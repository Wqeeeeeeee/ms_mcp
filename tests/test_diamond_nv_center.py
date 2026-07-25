from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from material_studio_mcp_server import live_smoke, natural_language, server
from material_studio_mcp_server.diagnostics import (
    model_view_audit,
    write_view_audit_bundle,
)
from material_studio_mcp_server.health import build_modeling_health
from material_studio_mcp_server.natural_language import (
    infer_modeling_plan,
    supported_semiconductor_virtual_template_profiles,
)
from material_studio_mcp_server.semiconductor_contracts import (
    DIAMOND_NV_CENTER_DEFAULT_SUPERCELL,
    DIAMOND_NV_CENTER_MAX_CUBIC_REPEAT,
    DIAMOND_NV_CENTER_MIN_CUBIC_REPEAT,
    DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID,
    DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS,
)
from material_studio_mcp_server.specs.patch import (
    SemanticPatch,
    apply_semantic_patch,
)
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore


def _nv_spec(request: str, *, project_id: str | None = None) -> ModelSpec:
    plan = infer_modeling_plan(request, project_id=project_id)
    assert plan.kind == "spec"
    assert plan.template_id == DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID
    return ModelSpec.model_validate(plan.payload)


@pytest.mark.parametrize(
    ("prompt", "charge_label", "net_charge", "multiplicity"),
    [
        ("Build a diamond NV center defect supercell.", "unspecified", None, None),
        ("Build a diamond NV0 center.", "NV0", 0, 2),
        ("Build a diamond NV- center.", "NV-", -1, 3),
        (
            "\u6784\u5efa\u91d1\u521a\u77f3NV\u4e2d\u5fc3\u7f3a\u9677\u8d85\u80de\u3002",
            "unspecified",
            None,
            None,
        ),
        (
            "\u6784\u5efa\u91d1\u521a\u77f3NV-\u4e2d\u5fc3\u3002",
            "NV-",
            -1,
            3,
        ),
    ],
)
def test_diamond_nv_requests_build_dedicated_structural_scaffold(
    prompt: str,
    charge_label: str,
    net_charge: int | None,
    multiplicity: int | None,
) -> None:
    spec = _nv_spec(prompt)
    atoms = {atom.id: atom.element for atom in spec.model.basis_atoms}
    charge = spec.metadata["defect_charge_spin_request"]

    assert spec.revision == 0
    assert spec.model.name == DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID
    assert len(atoms) == 63
    assert list(atoms.values()).count("C") == 62
    assert list(atoms.values()).count("N") == 1
    assert atoms["C1_000"] == "N"
    assert "C2_000" not in atoms
    assert charge["charge_state_label"] == charge_label
    assert charge["requested_net_charge_e"] == net_charge
    assert charge["reference_spin_multiplicity"] == multiplicity
    assert charge["calculation_execution_ready"] is (
        charge_label != "unspecified"
    )
    assert charge["structure_hotload_allowed"] is True
    assert charge["state_result_computed"] is False


@pytest.mark.parametrize(
    ("prompt", "repeat"),
    [
        ("Build a diamond NV- center in a 2x2x2 supercell.", 2),
        ("Build a diamond NV- center in a 3x3x3 supercell.", 3),
        ("Build a diamond NV- center in a 4x4x4 supercell.", 4),
        (
            "\u6784\u5efa\u91d1\u521a\u77f3 NV- \u4e2d\u5fc3\uff0c"
            "\u4f7f\u7528 3\u00d73\u00d73 \u8d85\u80de\u3002",
            3,
        ),
    ],
)
def test_diamond_nv_reviewed_cubic_supercells_are_deterministic(
    prompt: str,
    repeat: int,
) -> None:
    spec = _nv_spec(prompt)
    atoms = {atom.id: atom.element for atom in spec.model.basis_atoms}
    contract = spec.metadata["diamond_nv_supercell_contract"]
    expected_host_sites = 8 * repeat**3

    assert len(atoms) == expected_host_sites - 1
    assert list(atoms.values()).count("C") == expected_host_sites - 2
    assert list(atoms.values()).count("N") == 1
    assert atoms["C1_000"] == "N"
    assert "C2_000" not in atoms
    assert contract["matrix"] == [repeat, repeat, repeat]
    assert contract["cubic_repeat"] == repeat
    assert contract["host_site_count_before_defect"] == expected_host_sites
    assert contract["atom_count_after_defect"] == expected_host_sites - 1
    assert spec.metadata["nl_composite_operations"][0] == (
        f"make_supercell {repeat}x{repeat}x{repeat}"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a diamond NV+ center.",
        "Build a diamond NV2- center.",
        "Build a charged diamond NV center.",
        "Build a diamond NV0 and NV- center.",
        "Build a diamond NV center with charge +2.",
        "Build a diamond NV center in a 1x1x1 supercell.",
        "Build a diamond NV center in a 3x3x2 supercell.",
        "Build a diamond NV center in a 5x5x5 supercell.",
    ],
)
def test_diamond_nv_unsupported_requests_fail_closed_without_pristine_fallback(
    prompt: str,
) -> None:
    plan = infer_modeling_plan(prompt)

    assert plan.kind == "unsupported"
    assert plan.template_id == DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID
    assert plan.payload is None


def test_diamond_nv_generation_does_not_mutate_loaded_base_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = natural_language._load_example("diamond_cubic_spec.json")
    supplied = copy.deepcopy(original)
    before = copy.deepcopy(supplied)
    real_load = natural_language._load_example

    def load_example(name: str) -> dict:
        if name == "diamond_cubic_spec.json":
            return supplied
        return real_load(name)

    monkeypatch.setattr(natural_language, "_load_example", load_example)
    _nv_spec("Build a diamond NV- center.")

    assert supplied == before


def test_diamond_nv_diagnostics_bind_geometry_charge_and_csvs(
    tmp_path: Path,
) -> None:
    spec = _nv_spec("Build a diamond NV- center.")
    audit = model_view_audit(spec, views=["front"])
    semiconductor = audit["health"]["semiconductor_health"]
    defect = semiconductor["defect_summary"]
    charge = semiconductor["charge_balance_summary"]
    complex_row = defect["complexes"][0]
    health = build_modeling_health(
        {
            "validation": {"valid": True, "errors": [], "warnings": []},
            "view_audit": audit,
        },
        execution_mode="preview",
    )

    assert audit["health"]["ok"] is True
    assert defect["nitrogen_vacancy_count"] == 1
    assert defect["defect_complex_integrity_ok"] is True
    assert defect["defect_charge_state_unresolved_count"] == 0
    assert defect["defect_charge_spin_backend_unbound_count"] == 0
    assert complex_row["member_site_ids"] == ["C1_000", "C2_000"]
    assert complex_row["member_dopant_record_count"] == 1
    assert complex_row["member_vacancy_record_count"] == 1
    assert complex_row["pair_distance_angstrom_recomputed"] == pytest.approx(
        1.544556
    )
    assert complex_row["nearest_neighbor_verified"] is True
    assert complex_row["metadata_consistent"] is True
    assert charge["carrier_type_hint"] == "defect_charge_state_dependent"
    assert charge["nominal_composition_electron_count_parity"] == "odd"
    assert charge["charge_adjusted_valence_electron_count"] == 254
    assert charge["charge_adjusted_electron_count_parity"] == "even"
    assert charge["odd_electron_warning"] is False
    assert charge["charge_spin_backend_binding_ready"] is True
    assert health["checks"]["semiconductor_nitrogen_vacancy_count"] == 1
    assert (
        health["checks"]["semiconductor_charge_spin_backend_binding_ready"]
        is True
    )
    assert not any(
        "charge/spin request is not bound" in warning
        for warning in health["warnings"]
    )

    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    complex_csv = Path(bundle["files"]["semiconductor_defect_complexes_csv"])
    charge_csv = Path(bundle["files"]["semiconductor_charge_balance_csv"])
    complex_rows = list(csv.DictReader(complex_csv.open(encoding="utf-8")))
    charge_rows = list(csv.DictReader(charge_csv.open(encoding="utf-8")))

    assert bundle["row_counts"]["semiconductor_defect_complexes"] == 1
    assert bundle["row_counts"]["semiconductor_charge_balance"] == 2
    assert complex_rows[0]["complex_type"] == "nitrogen_vacancy"
    assert complex_rows[0]["member_site_ids"] == "C1_000;C2_000"
    assert complex_rows[0]["charge_state_label"] == "NV-"
    assert complex_rows[0]["calculation_execution_ready"] == "True"
    assert {row["charge_adjusted_electron_count_parity"] for row in charge_rows} == {
        "even"
    }


def test_larger_diamond_nv_supercell_clears_default_finite_size_warning(
    tmp_path: Path,
) -> None:
    small = _nv_spec("Build a diamond NV- center in a 2x2x2 supercell.")
    larger = _nv_spec("Build a diamond NV- center in a 3x3x3 supercell.")
    small_finite = model_view_audit(small, views=["front"])["health"][
        "semiconductor_health"
    ]["finite_size_summary"]
    larger_audit = model_view_audit(larger, views=["front"])
    larger_finite = larger_audit["health"]["semiconductor_health"][
        "finite_size_summary"
    ]

    assert small_finite["non_passivant_atom_count"] == 63
    assert small_finite["small_cell_warning"] is True
    assert small_finite["finite_size_warning"] is True
    assert larger_finite["non_passivant_atom_count"] == 215
    assert larger_finite["max_isolated_fraction"] < 0.01
    assert larger_finite["small_cell_warning"] is False
    assert larger_finite["high_concentration_warning"] is False
    assert larger_finite["finite_size_warning"] is False
    assert larger_finite["supercell_matrix"] == [3, 3, 3]
    assert larger_finite["supercell_cubic_repeat"] == 3
    assert larger_finite["supercell_host_site_count_before_defect"] == 216
    assert larger_finite["supercell_atom_count_after_defect"] == 215
    assert larger_finite["supercell_contract_integrity_ok"] is True
    assert larger_finite["supercell_contract_errors"] == []

    bundle = write_view_audit_bundle(tmp_path, larger, larger_audit)
    finite_rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_finite_size_csv"]).open(
                encoding="utf-8"
            )
        )
    )
    assert finite_rows[0]["supercell_matrix"] == "3;3;3"
    assert finite_rows[0]["supercell_cubic_repeat"] == "3"
    assert finite_rows[0]["supercell_contract_integrity_ok"] == "True"


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("matrix", [3, 3, 2], "invalid_matrix:"),
        ("host_site_count_before_defect", 215, "host_site_count_mismatch"),
        ("atom_count_after_defect", 214, "current_atom_count_mismatch"),
    ],
)
def test_diamond_nv_supercell_contract_tampering_is_reported(
    field: str,
    value: object,
    expected_error: str,
) -> None:
    payload = copy.deepcopy(
        _nv_spec(
            "Build a diamond NV- center in a 3x3x3 supercell."
        ).model_dump(mode="json")
    )
    payload["metadata"]["diamond_nv_supercell_contract"][field] = value
    tampered = ModelSpec.model_validate(payload)
    audit = model_view_audit(tampered, views=["front"])
    finite = audit["health"]["semiconductor_health"]["finite_size_summary"]
    health = build_modeling_health(
        {
            "validation": {"valid": True, "errors": [], "warnings": []},
            "view_audit": audit,
        },
        execution_mode="preview",
    )

    assert finite["supercell_contract_integrity_ok"] is False
    assert any(
        error.startswith(expected_error)
        for error in finite["supercell_contract_errors"]
    )
    assert (
        health["checks"]["semiconductor_nv_supercell_contract_integrity_ok"]
        is False
    )
    assert any(
        "supercell metadata failed" in warning
        for warning in health["warnings"]
    )


def test_current_diamond_nv_charge_state_patch_changes_only_simulation_and_metadata() -> None:
    base = _nv_spec("Build a diamond NV center.")
    atoms_before = [
        atom.model_dump(mode="json") for atom in base.model.basis_atoms
    ]
    plan = infer_modeling_plan(
        "Set the current NV center charge state to NV-.",
        current_spec=base,
    )

    assert plan.kind == "patch"
    assert plan.template_id == "diamond_nv_charge_state"
    patched, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )
    atoms_after = [
        atom.model_dump(mode="json") for atom in patched.model.basis_atoms
    ]
    charge = patched.metadata["defect_charge_spin_request"]
    defect = model_view_audit(
        patched,
        views=["front"],
    )["health"]["semiconductor_health"]["defect_summary"]

    assert patched.revision == base.revision + 1
    assert atoms_after == atoms_before
    assert charge["charge_state_label"] == "NV-"
    assert charge["requested_net_charge_e"] == -1
    assert charge["reference_spin_multiplicity"] == 3
    assert patched.simulation.total_charge == -1
    assert patched.simulation.initial_spin == 2
    assert defect["defect_complex_integrity_ok"] is True
    assert base.metadata["defect_charge_spin_request"]["charge_state_label"] == (
        "unspecified"
    )


@pytest.mark.parametrize(
    "tamper",
    ["distance", "member_site", "backend_status", "top_level_charge"],
)
def test_diamond_nv_metadata_tampering_fails_diagnostic_integrity(
    tamper: str,
) -> None:
    payload = copy.deepcopy(
        infer_modeling_plan("Build a diamond NV- center.").payload
    )
    complex_record = payload["metadata"]["defect_complexes"][0]
    charge_request = payload["metadata"]["defect_charge_spin_request"]
    if tamper == "distance":
        complex_record["pair_distance_angstrom"] = 9.0
    elif tamper == "member_site":
        complex_record["member_site_ids"][1] = "C3_000"
    elif tamper == "backend_status":
        complex_record["backend_charge_binding_status"] = "bound"
        charge_request["backend_charge_binding_status"] = "bound"
    else:
        charge_request["requested_net_charge_e"] = 0
    tampered = ModelSpec.model_validate(payload)

    audit = model_view_audit(tampered, views=["front"])
    defect = audit["health"]["semiconductor_health"]["defect_summary"]

    assert audit["health"]["ok"] is False
    assert defect["defect_complex_integrity_ok"] is False
    assert defect["defect_complex_integrity_errors"]


def test_diamond_nv_castep_gate_blocks_unresolved_and_accepts_bound_state() -> None:
    unresolved = _nv_spec("Build a diamond NV center.")
    negative = _nv_spec("Build a diamond NV- center.")

    unresolved_gate = server._castep_defect_charge_spin_preflight(unresolved)
    negative_gate = server._castep_defect_charge_spin_preflight(negative)

    assert unresolved_gate["execution_ready"] is False
    assert unresolved_gate["structure_materialization_allowed"] is True
    assert unresolved_gate["same_window_gui_hotload_allowed"] is True
    assert "defect_charge_state_unresolved" in unresolved_gate["blocking_reasons"]
    assert unresolved_gate["blocking_reasons"] == [
        "defect_charge_state_unresolved"
    ]
    assert negative_gate["charge_state_label"] == "NV-"
    assert "defect_charge_state_unresolved" not in negative_gate["blocking_reasons"]
    assert negative_gate["execution_ready"] is True
    assert negative_gate["blocking_reasons"] == []
    assert negative_gate["backend_charge_binding_status"] == (
        DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
    )


def test_unresolved_diamond_nv_public_castep_paths_fail_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = "diamond_nv_castep_gate"
    spec = _nv_spec("Build a diamond NV center.", project_id=project_id)
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_run(*args, **kwargs):
        raise AssertionError("NV CASTEP preflight must stop before the runner")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    electronic_preview = server.material_studio_castep_run_current(
        project_id=project_id,
        execution_mode="preview",
        expected_revision=0,
        task="Energy",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    electronic_execute = server.material_studio_castep_run_current(
        project_id=project_id,
        execution_mode="execute",
        expected_revision=0,
        task="Energy",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    relaxation_preview = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="preview",
        expected_revision=0,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    relaxation_execute = server.material_studio_castep_relax_current(
        project_id=project_id,
        execution_mode="execute",
        expected_revision=0,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    blocker = "defect_charge_state_unresolved"
    assert electronic_preview["ok"] is True
    assert electronic_preview["status"] == "castep_electronic_preflight_blocked"
    assert blocker in electronic_preview["preflight"]["blocking_reasons"]
    assert electronic_execute["ok"] is False
    assert electronic_execute["execution_started"] is False
    assert electronic_execute["revision_created"] is False
    assert blocker in electronic_execute["preflight"]["blocking_reasons"]
    assert relaxation_preview["ok"] is True
    assert relaxation_preview["status"] == "relaxation_preflight_blocked"
    assert blocker in relaxation_preview["preflight"]["blocking_reasons"]
    assert relaxation_execute["ok"] is False
    assert relaxation_execute["execution_started"] is False
    assert relaxation_execute["revision_created"] is False
    assert blocker in relaxation_execute["preflight"]["blocking_reasons"]
    assert ProjectStore(tmp_path).load_current(project_id).revision == 0


def test_diamond_nv_capabilities_and_live_smoke_are_discoverable() -> None:
    profiles = {
        item["template_id"]: item
        for item in supported_semiconductor_virtual_template_profiles()
    }
    profile = profiles[DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID]
    compact = server.material_studio_live_capabilities(
        include_status=False,
        response_mode="compact",
    )

    assert profile["base_template_id"] == "diamond_cubic"
    assert profile["variant_kind"] == "defect_complex_scaffold"
    assert profile["default_supercell"] == list(
        DIAMOND_NV_CENTER_DEFAULT_SUPERCELL
    )
    assert profile["supported_supercell_contract"] == {
        "shape": "cubic",
        "min_repeat": DIAMOND_NV_CENTER_MIN_CUBIC_REPEAT,
        "max_repeat": DIAMOND_NV_CENTER_MAX_CUBIC_REPEAT,
        "base_cell": "diamond_conventional_8_atom",
    }
    assert "defects" in profile["default_diagnostic_focuses"]
    assert "spin_charge_preflight" in profile["default_diagnostic_focuses"]
    assert live_smoke.SCENARIO_VIRTUAL_TEMPLATE_IDS["diamond_nv_center"] == (
        DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID
    )
    assert "NV- center" in live_smoke.default_request_for_scenario(
        "diamond_nv_center"
    )
    assert (
        live_smoke.SCENARIO_EXPECTATIONS["diamond_nv_center"]["row_counts"][
            "semiconductor_defect_complexes"
        ]
        == 1
    )
    assert len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
