from __future__ import annotations

import copy
import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_studio_mcp_server import natural_language, server
from material_studio_mcp_server.castep_materialscript import (
    build_castep_materialscript_plan,
    render_castep_run_snippet,
)
from material_studio_mcp_server.diagnostics import (
    model_view_audit,
    write_view_audit_bundle,
)
from material_studio_mcp_server.semiconductor_contracts import (
    DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS,
    DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS,
)
from material_studio_mcp_server.specs.castep import (
    CastepEnergySpec,
    CastepSpinTreatment,
)
from material_studio_mcp_server.specs.patch import (
    SemanticPatch,
    SemanticPatchOperation,
    apply_semantic_patch,
)
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.translators.castep_to_perl import (
    render_castep_task_script,
)
from material_studio_mcp_server.validators import validate_generated_script


def _nv_spec(request: str) -> ModelSpec:
    plan = natural_language.infer_modeling_plan(request)
    assert plan.kind == "spec"
    return ModelSpec.model_validate(plan.payload)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("non polarized", CastepSpinTreatment.NON_POLARIZED),
        ("collinear", CastepSpinTreatment.COLLINEAR),
        ("non-collinear", CastepSpinTreatment.NON_COLLINEAR),
    ],
)
def test_castep_spin_treatment_normalizes_documented_values(
    alias: str,
    expected: CastepSpinTreatment,
) -> None:
    assert CastepEnergySpec(spin_treatment=alias).spin_treatment is expected


def test_castep_spin_controls_fail_closed_on_incompatible_combinations() -> None:
    with pytest.raises(ValidationError, match="explicit spin_treatment"):
        CastepEnergySpec(initial_spin=2)
    with pytest.raises(ValidationError, match="nonzero initial_spin"):
        CastepEnergySpec(
            spin_treatment="Non-polarized",
            initial_spin=1,
        )
    with pytest.raises(ValidationError, match="do not also set initial_spin"):
        CastepEnergySpec(
            spin_treatment="Collinear",
            use_formal_spin=True,
            initial_spin=2,
        )
    with pytest.raises(ValidationError, match="explicit spin_treatment"):
        SemanticPatchOperation.model_validate(
            {
                "type": "set_castep_energy",
                "initial_spin": 2,
            }
        )


def test_castep_charge_spin_renderer_uses_exact_ms_20_1_settings_order() -> None:
    spec = CastepEnergySpec(
        total_charge=-1,
        spin_treatment="Collinear",
        use_formal_spin=False,
        initial_spin=2,
        optimize_total_spin=False,
        cutoff_energy_ev=600,
        kpoints=(4, 4, 4),
    )
    plan = build_castep_materialscript_plan(spec)
    script = render_castep_run_snippet(spec)
    names = [item["name"] for item in plan.summary()["settings"]]

    assert names[:7] == [
        "Quality",
        "XCFunctional",
        "Charge",
        "SpinTreatment",
        "UseFormalSpin",
        "InitialSpin",
        "OptimizeTotalSpin",
    ]
    assert "Charge => -1" in script
    assert "SpinTreatment => 'Collinear'" in script
    assert "UseFormalSpin => 'No'" in script
    assert "InitialSpin => 2" in script
    assert "OptimizeTotalSpin => 'No'" in script
    full_script = render_castep_task_script(
        spec,
        "input.cif",
        project_id="charge_spin_renderer",
        revision=0,
    )
    assert validate_generated_script(full_script)["valid"] is True


def test_semantic_patch_binds_charge_spin_without_changing_crystal_geometry() -> None:
    base = _nv_spec("Build a diamond NV center.")
    atoms_before = [
        atom.model_dump(mode="json") for atom in base.model.basis_atoms
    ]
    lattice_before = base.model.lattice.model_dump(mode="json")
    patched, diff = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "set_castep_energy",
                    "task": "Energy",
                    "functional": "PBE",
                    "quality": "Medium",
                    "total_charge": -1,
                    "spin_treatment": "Collinear",
                    "use_formal_spin": False,
                    "initial_spin": 2,
                    "optimize_total_spin": False,
                    "cutoff_energy_ev": 600,
                    "kpoint_separation": 0.04,
                }
            ],
        ),
    )

    assert diff == ["set_castep_energy"]
    assert [
        atom.model_dump(mode="json") for atom in patched.model.basis_atoms
    ] == atoms_before
    assert patched.model.lattice.model_dump(mode="json") == lattice_before
    assert patched.simulation.total_charge == -1
    assert patched.simulation.initial_spin == 2
    assert base.simulation.total_charge is None


@pytest.mark.parametrize(
    ("prompt", "charge", "initial_spin"),
    [
        ("Build a diamond NV0 center.", 0, 1),
        ("Build a diamond NV- center.", -1, 2),
    ],
)
def test_explicit_nv_template_binds_reviewed_castep_state(
    prompt: str,
    charge: int,
    initial_spin: int,
) -> None:
    spec = _nv_spec(prompt)
    binding = server._castep_defect_charge_spin_preflight(spec)
    charge_request = spec.metadata["defect_charge_spin_request"]

    assert spec.simulation.total_charge == charge
    assert spec.simulation.spin_treatment is CastepSpinTreatment.COLLINEAR
    assert spec.simulation.use_formal_spin is False
    assert spec.simulation.initial_spin == initial_spin
    assert spec.simulation.optimize_total_spin is False
    assert charge_request["backend_charge_binding_status"] == (
        DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
    )
    assert charge_request["calculation_execution_ready"] is True
    assert binding["execution_ready"] is True
    assert binding["blocking_reasons"] == []
    assert binding["structured_castep_binding"]["settings_are_initial_state_request_not_computed_result"] is True


def test_unresolved_and_legacy_nv_revisions_remain_fail_closed() -> None:
    unresolved = _nv_spec("Build a diamond NV center.")
    unresolved_gate = server._castep_defect_charge_spin_preflight(unresolved)
    legacy_payload = copy.deepcopy(_nv_spec("Build a diamond NV- center.").model_dump(mode="json"))
    legacy_payload["simulation"].pop("total_charge")
    legacy_payload["simulation"].pop("spin_treatment")
    legacy_payload["simulation"].pop("use_formal_spin")
    legacy_payload["simulation"].pop("initial_spin")
    legacy_payload["simulation"].pop("optimize_total_spin")
    request = legacy_payload["metadata"]["defect_charge_spin_request"]
    complex_record = legacy_payload["metadata"]["defect_complexes"][0]
    for record in (request, complex_record, legacy_payload["metadata"]["last_defect_complex"]):
        record["backend_charge_binding_status"] = DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS
        record["backend_spin_binding_status"] = DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS
        record["calculation_execution_ready"] = False
    legacy = ModelSpec.model_validate(legacy_payload)
    legacy_audit = model_view_audit(legacy, views=["front"])
    legacy_gate = server._castep_defect_charge_spin_preflight(legacy)

    assert unresolved_gate["execution_ready"] is False
    assert unresolved_gate["blocking_reasons"] == ["defect_charge_state_unresolved"]
    assert legacy_audit["health"]["semiconductor_health"]["defect_summary"][
        "defect_complex_integrity_ok"
    ] is True
    assert legacy_gate["execution_ready"] is False
    assert "defect_charge_spin_settings_missing_or_mismatched" in legacy_gate[
        "blocking_reasons"
    ]


def test_nv_preflight_rejects_complex_metadata_or_runtime_setting_mismatch() -> None:
    payload = copy.deepcopy(_nv_spec("Build a diamond NV- center.").model_dump(mode="json"))
    payload["metadata"]["defect_complexes"][0]["requested_net_charge_e"] = 0
    tampered = ModelSpec.model_validate(payload)
    metadata_gate = server._castep_defect_charge_spin_preflight(tampered)
    mismatched_simulation = CastepEnergySpec.model_validate(
        {
            **_nv_spec("Build a diamond NV- center.").simulation.model_dump(
                mode="json"
            ),
            "total_charge": 0,
        }
    )
    simulation_gate = server._castep_defect_charge_spin_preflight(
        _nv_spec("Build a diamond NV- center."),
        mismatched_simulation,
    )

    assert metadata_gate["execution_ready"] is False
    assert metadata_gate["complex_metadata_consistent"] is False
    assert "defect_charge_spin_metadata_contract_invalid" in metadata_gate[
        "blocking_reasons"
    ]
    assert simulation_gate["execution_ready"] is False
    assert "defect_charge_spin_settings_missing_or_mismatched" in simulation_gate[
        "blocking_reasons"
    ]


def test_nv_charge_state_followup_updates_simulation_without_geometry_change() -> None:
    base = _nv_spec("Build a diamond NV center.")
    before = [atom.model_dump(mode="json") for atom in base.model.basis_atoms]
    plan = natural_language.infer_modeling_plan(
        "Set the current NV center charge state to NV-.",
        current_spec=base,
    )

    assert plan.kind == "patch"
    assert [operation["type"] for operation in plan.payload["operations"]] == [
        "set_castep_energy",
        "set_metadata",
    ]
    patched, _ = apply_semantic_patch(
        base,
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=plan.payload["operations"],
        ),
    )
    assert [atom.model_dump(mode="json") for atom in patched.model.basis_atoms] == before
    assert patched.simulation.total_charge == -1
    assert patched.simulation.initial_spin == 2
    assert server._castep_defect_charge_spin_preflight(patched)[
        "execution_ready"
    ] is True


def test_natural_language_castep_settings_include_charge_and_spin() -> None:
    current = _nv_spec("Build a diamond NV center.")
    operation = natural_language._match_castep_settings(
        "Set CASTEP total charge -1, collinear spin, do not use formal spin, "
        "initial spin 2, and fix total spin.",
        current,
    )

    assert operation is not None
    assert operation["total_charge"] == -1
    assert operation["spin_treatment"] == "Collinear"
    assert operation["use_formal_spin"] is False
    assert operation["initial_spin"] == 2
    assert operation["optimize_total_spin"] is False
    assert operation["cutoff_energy_ev"] == 600
    assert operation["kpoint_separation"] == 0.04


def test_nv_diagnostics_export_expected_and_observed_charge_spin_columns(
    tmp_path: Path,
) -> None:
    spec = _nv_spec("Build a diamond NV- center.")
    audit = model_view_audit(spec, views=["front"])
    calculation = audit["health"]["semiconductor_health"][
        "calculation_preflight_summary"
    ]
    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    complex_rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_defect_complexes_csv"]).open(
                encoding="utf-8"
            )
        )
    )
    charge_rows = list(
        csv.DictReader(
            Path(bundle["files"]["semiconductor_charge_balance_csv"]).open(
                encoding="utf-8"
            )
        )
    )

    assert complex_rows[0]["expected_castep_total_charge"] == "-1"
    assert complex_rows[0]["observed_castep_total_charge"] == "-1"
    assert complex_rows[0]["expected_castep_initial_spin"] == "2"
    assert complex_rows[0]["observed_castep_initial_spin"] == "2"
    assert complex_rows[0]["castep_charge_spin_all_fields_match"] == "True"
    assert {row["castep_charge_spin_all_fields_match"] for row in charge_rows} == {
        "True"
    }
    assert calculation["total_charge"] == -1
    assert calculation["spin_treatment"] == "Collinear"
    assert calculation["initial_spin"] == 2
    assert calculation["charge_spin_settings_configured"] is True


def test_public_castep_input_schema_exposes_charge_and_spin_contract() -> None:
    schema = server.CastepEnergyInput.model_json_schema()

    assert schema["properties"]["total_charge"]["anyOf"][0]["minimum"] == -9999
    assert schema["properties"]["spin_treatment"]["anyOf"][0]["$ref"] == (
        "#/$defs/CastepSpinTreatment"
    )
    assert schema["$defs"]["CastepSpinTreatment"]["enum"] == [
        "Non-polarized",
        "Collinear",
        "Non-collinear",
    ]
    assert "use_formal_spin" in schema["properties"]
    assert "initial_spin" in schema["properties"]
    assert "optimize_total_spin" in schema["properties"]
    preview = server.material_studio_castep_energy_script(
        "input.cif",
        total_charge=-1,
        spin_treatment="Collinear",
        use_formal_spin=False,
        initial_spin=2,
        optimize_total_spin=False,
    )
    assert preview["ok"] is True
    assert preview["executes_castep"] is False
    assert "Charge => -1" in preview["script"]
    assert "InitialSpin => 2" in preview["script"]


def test_bound_nv_public_castep_previews_preserve_exact_settings(
    tmp_path: Path,
) -> None:
    spec = _nv_spec("Build a diamond NV- center.").model_copy(
        update={"project_id": "bound_nv_public_preview"}
    )
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    electronic = server.material_studio_castep_run_current(
        project_id=spec.project_id,
        expected_revision=0,
        execution_mode="preview",
        task="Energy",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )
    relaxation = server.material_studio_castep_relax_current(
        project_id=spec.project_id,
        expected_revision=0,
        execution_mode="preview",
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    for result in (electronic, relaxation):
        assert result["ok"] is True
        assert result["execution_started"] is False
        assert result["preflight"]["defect_charge_spin_preflight"][
            "execution_ready"
        ] is True
        assert result["preflight"]["defect_charge_spin_preflight"][
            "blocking_reasons"
        ] == []
        assert "Charge => -1" in result["script"]
        assert "SpinTreatment => 'Collinear'" in result["script"]
        assert "InitialSpin => 2" in result["script"]
        assert "OptimizeTotalSpin => 'No'" in result["script"]


def test_public_execute_rejects_nv_charge_override_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _nv_spec("Build a diamond NV- center.").model_copy(
        update={"project_id": "bound_nv_mismatch_execute"}
    )
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="preview",
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    def unexpected_run(*args, **kwargs):
        raise AssertionError("mismatched NV charge must block before the runner")

    monkeypatch.setattr(server.runner, "run_script", unexpected_run)
    result = server.material_studio_castep_run_current(
        project_id=spec.project_id,
        expected_revision=0,
        execution_mode="execute",
        task="Energy",
        total_charge=0,
        open_in_gui=False,
        take_snapshot=False,
        export_view_audit=False,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["execution_started"] is False
    assert result["revision_created"] is False
    assert "defect_charge_spin_settings_missing_or_mismatched" in result[
        "preflight"
    ]["blocking_reasons"]
