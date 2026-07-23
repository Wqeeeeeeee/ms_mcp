from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.dmol3_materialscript import (
    DMOL3_REVIEWED_RESULT_KEYS,
    build_dmol3_materialscript_plan,
    render_dmol3_run_snippet,
)
from material_studio_mcp_server.dmol3_relaxation import (
    build_dmol3_relaxed_revision_spec,
    molecule_structure_sha256,
)
from material_studio_mcp_server.parsers.dmol3_log import (
    validate_dmol3_geometry_result,
)
from material_studio_mcp_server.specs.dmol3 import (
    DMol3GeometryOptimizationSpec,
    DMol3Quality,
    DMol3TheoryLevel,
)
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.translators.dmol3_to_perl import (
    DMOL3_GEOMETRY_RESULT_SCHEMA,
    render_dmol3_geometry_optimization_script,
)
from material_studio_mcp_server.translators.project_to_perl import (
    render_model_to_perl,
)
from material_studio_mcp_server.validators import validate_generated_script


def _molecule() -> MoleculeSpec:
    return MoleculeSpec.model_validate(
        {
            "name": "water",
            "atoms": [
                {
                    "id": "O1",
                    "element": "O",
                    "xyz_angstrom": [0.0, 0.0, 0.0],
                    "charge": -0.8,
                },
                {
                    "id": "H1",
                    "element": "H",
                    "xyz_angstrom": [0.95, 0.0, 0.0],
                    "charge": 0.4,
                },
                {
                    "id": "H2",
                    "element": "H",
                    "xyz_angstrom": [-0.24, 0.92, 0.0],
                    "charge": 0.4,
                },
            ],
            "bonds": [
                {"atom1": "O1", "atom2": "H1", "type": "Single"},
                {"atom1": "O1", "atom2": "H2", "type": "Single"},
            ],
            "total_charge": 0,
            "spin_multiplicity": 1,
        }
    )


def _model_spec() -> ModelSpec:
    return ModelSpec.model_validate(
        {
            "project_id": "dmol3_water",
            "revision": 4,
            "model_type": "molecule",
            "model": _molecule().model_dump(mode="json"),
            "simulation": {
                "module": "DMol3",
                "task": "GeometryOptimization",
                "quality": "Fine",
                "theory_level": "GGA",
                "charge": 0,
                "use_symmetry": "No",
                "create_energy_evolution_chart": "Yes",
            },
        }
    )


def _result_payload(
    spec: ModelSpec,
    input_structure: Path,
    output_structure: Path,
    output_report: Path,
) -> dict:
    molecule = spec.model
    assert isinstance(molecule, MoleculeSpec)
    atoms = []
    for index, atom in enumerate(molecule.atoms):
        xyz = atom.xyz_angstrom.model_dump(mode="json")
        xyz["z"] += 0.01 * (index + 1)
        atoms.append(
            {
                "id": atom.id,
                "element": atom.element,
                "xyz_angstrom": xyz,
            }
        )
    return {
        "schema_version": DMOL3_GEOMETRY_RESULT_SCHEMA,
        "project_id": spec.project_id,
        "base_revision": spec.revision,
        "script_kind": "dmol3_geometry_optimization",
        "module": "DMol3",
        "task": "GeometryOptimization",
        "input_structure": str(input_structure),
        "output_structure": str(output_structure),
        "output_report": str(output_report),
        "materials_studio_api_contract": "Materials Studio 20.1",
        "result_keys": list(DMOL3_REVIEWED_RESULT_KEYS),
        "energy_evolution_charts_requested": True,
        "converged": True,
        "total_energy_kcal_per_mol": -76.25,
        "optimized_atoms": atoms,
        "result_document_names": {
            "EnergyChart": "DMol3 Energy",
            "ConvergenceChart": "DMol3 Convergence",
        },
    }


def _write_result_evidence(
    payload: dict,
    output_structure: Path,
    output_report: Path,
) -> None:
    atom_lines = []
    for index, atom in enumerate(payload["optimized_atoms"], start=1):
        xyz = atom["xyz_angstrom"]
        atom_lines.append(
            "    "
            f'<Atom3d ID="{index + 1}" '
            f'Name="MSMCPAtom{index:06d}" '
            f'XYZ="{xyz["x"]},{xyz["y"]},{xyz["z"]}" '
            f'Components="{atom["element"]}"/>'
        )
    output_structure.write_text(
        '<?xml version="1.0" encoding="latin1"?>\n'
        '<!DOCTYPE XSD []>\n'
        '<XSD Version="20.1" WrittenBy="Materials Studio 20.1">\n'
        "  <AtomisticTreeRoot>\n"
        + "\n".join(atom_lines)
        + "\n  </AtomisticTreeRoot>\n</XSD>\n",
        encoding="latin-1",
    )
    output_report.write_text(
        "Materials Studio DMol^3 version 2020\n"
        "Geometry optimization completed successfully in 1 steps.\n"
        "Message: DMol3 job finished successfully\n",
        encoding="latin-1",
    )


def test_dmol3_spec_and_plan_are_strict_and_deterministic() -> None:
    spec = DMol3GeometryOptimizationSpec(
        quality="Fine",
        theory_level="Hybrid",
        geometry_optimization_quality="Medium",
        charge=-2,
        use_symmetry="Yes",
        create_energy_evolution_chart="No",
    )
    plan = build_dmol3_materialscript_plan(spec)

    assert spec.quality is DMol3Quality.FINE
    assert spec.theory_level is DMol3TheoryLevel.HYBRID
    assert plan.settings == (
        ("Quality", "Fine"),
        ("TheoryLevel", "Hybrid"),
        ("GeometryOptimizationQuality", "Medium"),
        ("Charge", -2),
        ("UseSymmetry", "Yes"),
        ("CreateEnergyEvolutionChart", "No"),
    )
    script = render_dmol3_run_snippet(spec)
    assert (
        "Modules->DMol3->GeometryOptimization->Run($doc, Settings(" in script
    )
    assert "Quality => 'Fine'" in script
    assert "TheoryLevel => 'Hybrid'" in script

    with pytest.raises(ValidationError, match="extra_forbidden"):
        DMol3GeometryOptimizationSpec.model_validate(
            {"extra_settings": {"UnsafeSetting": "anything"}}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DMol3GeometryOptimizationSpec.model_validate(
            {"output_file": r"C:\outside\optimized.xsd"}
        )
    with pytest.raises(ValidationError):
        DMol3GeometryOptimizationSpec(quality="Ultra")
    with pytest.raises(ValidationError):
        DMol3GeometryOptimizationSpec.model_validate({"charge": "1"})
    with pytest.raises(ValueError, match="Perl scalar"):
        render_dmol3_run_snippet(spec, document_variable="$doc; die")

    mismatched_model = _model_spec().model_dump(mode="json")
    mismatched_model["simulation"]["charge"] = 1
    with pytest.raises(ValidationError, match="total_charge"):
        ModelSpec.model_validate(mismatched_model)


def test_dmol3_is_a_companion_preview_and_never_runs_during_build(
    tmp_path: Path,
) -> None:
    generated = render_model_to_perl(_model_spec(), tmp_path)

    assert "Modules->DMol3" not in generated.script
    assert generated.calculation_preview_script is not None
    assert (
        "Modules->DMol3->GeometryOptimization->Run"
        in generated.calculation_preview_script
    )
    assert generated.calculation_preview is not None
    assert generated.calculation_preview["calculation_executed"] is False
    assert (
        generated.calculation_preview["execution_tool"]
        == "material_studio_dmol3_relax_current"
    )
    handoff = generated.calculation_preview["execution_handoff"]
    assert handoff["preview_action"]["safe_to_call_without_confirmation"] is True
    assert handoff["execute_action"]["needs_user_confirmation"] is True


def test_dmol3_result_script_binds_atom_identity_and_reviewed_results(
    tmp_path: Path,
) -> None:
    model = _model_spec()
    simulation = model.simulation
    molecule = model.model
    assert isinstance(simulation, DMol3GeometryOptimizationSpec)
    assert isinstance(molecule, MoleculeSpec)

    script = render_dmol3_geometry_optimization_script(
        simulation,
        tmp_path / "input.xsd",
        tmp_path / "optimized.xsd",
        tmp_path / "DMol3.outmol",
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
    )

    assert "my @source_atom_ids = ('O1', 'H1', 'H2');" in script
    assert 'sprintf("MSMCPAtom%06d"' in script
    assert "$dmol3_results->Structure" in script
    assert "$dmol3_results->Report" in script
    assert "$dmol3_results->Converged" in script
    assert "$dmol3_results->TotalEnergy" in script
    assert "xyz_angstrom" in script
    assert "\\\"x\\\"" in script
    assert DMOL3_GEOMETRY_RESULT_SCHEMA in script
    assert validate_generated_script(script)["valid"] is True


def test_dmol3_result_validation_and_promotion_are_fail_closed(
    tmp_path: Path,
) -> None:
    model = _model_spec()
    simulation = model.simulation
    molecule = model.model
    assert isinstance(simulation, DMol3GeometryOptimizationSpec)
    assert isinstance(molecule, MoleculeSpec)
    input_structure = tmp_path / "input.xsd"
    output_structure = tmp_path / "optimized.xsd"
    output_report = tmp_path / "DMol3.outmol"
    input_structure.write_bytes(b"input-xsd")
    payload = _result_payload(
        model,
        input_structure,
        output_structure,
        output_report,
    )
    _write_result_evidence(payload, output_structure, output_report)

    validation = validate_dmol3_geometry_result(
        payload,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert validation["ok"] is True
    assert validation["converged"] is True
    assert validation["atom_identity_preserved"] is True
    assert validation["geometry_evidence_verified"] is True

    promoted, receipt = build_dmol3_relaxed_revision_spec(
        model,
        simulation=simulation,
        result_payload=payload,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
        script_sha256="a" * 64,
        target_revision=5,
    )
    assert isinstance(promoted.model, MoleculeSpec)
    assert promoted.model.bonds == molecule.bonds
    assert promoted.model.total_charge == molecule.total_charge
    assert promoted.model.spin_multiplicity == molecule.spin_multiplicity
    assert promoted.model.atoms[0].charge == molecule.atoms[0].charge
    assert promoted.model.atoms[0].xyz_angstrom.z == pytest.approx(0.01)
    assert molecule_structure_sha256(promoted.model) != molecule_structure_sha256(
        molecule
    )
    assert receipt["target_revision"] == 5
    assert receipt["geometry_relaxation_verified"] is True
    assert receipt["output_evidence"]["verified"] is True
    assert receipt["atom_identity_preserved"] is True
    assert receipt["max_cartesian_displacement_angstrom"] > 0
    assert promoted.metadata["last_dmol3_geometry_optimization"] == receipt

    payload_with_extra = deepcopy(payload)
    payload_with_extra["unsafe"] = "ignored only by unsafe implementations"
    invalid = validate_dmol3_geometry_result(
        payload_with_extra,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid["ok"] is False
    assert any("Extra inputs" in error for error in invalid["errors"])

    string_boolean = deepcopy(payload)
    string_boolean["converged"] = "true"
    invalid_boolean = validate_dmol3_geometry_result(
        string_boolean,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid_boolean["ok"] is False

    string_coordinate = deepcopy(payload)
    string_coordinate["optimized_atoms"][0]["xyz_angstrom"]["x"] = "0.0"
    invalid_coordinate = validate_dmol3_geometry_result(
        string_coordinate,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid_coordinate["ok"] is False

    wrong_identity = deepcopy(payload)
    wrong_identity["optimized_atoms"][0]["id"] = "O_CHANGED"
    invalid_identity = validate_dmol3_geometry_result(
        wrong_identity,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid_identity["ok"] is False
    assert invalid_identity["atom_identity_preserved"] is False

    mismatched_xsd = deepcopy(payload)
    mismatched_xsd["optimized_atoms"][0]["xyz_angstrom"]["x"] += 0.1
    invalid_structure_binding = validate_dmol3_geometry_result(
        mismatched_xsd,
        project_id=model.project_id,
        base_revision=model.revision,
        source_molecule=molecule,
        input_structure=input_structure,
        output_structure=output_structure,
        output_report=output_report,
    )
    assert invalid_structure_binding["ok"] is False
    assert invalid_structure_binding["geometry_evidence_verified"] is False
    assert any(
        "coordinates differ" in error
        for error in invalid_structure_binding["errors"]
    )

    unconverged = deepcopy(payload)
    unconverged["converged"] = False
    with pytest.raises(ValueError, match="did not converge"):
        build_dmol3_relaxed_revision_spec(
            model,
            simulation=simulation,
            result_payload=unconverged,
            input_structure=input_structure,
            output_structure=output_structure,
            output_report=output_report,
            script_sha256="b" * 64,
            target_revision=5,
        )


def test_dmol3_promotion_rejects_charge_mismatch(tmp_path: Path) -> None:
    model = _model_spec()
    molecule = model.model
    assert isinstance(molecule, MoleculeSpec)
    input_structure = tmp_path / "input.xsd"
    output_structure = tmp_path / "optimized.xsd"
    output_report = tmp_path / "DMol3.outmol"
    for path in (input_structure, output_structure, output_report):
        path.write_bytes(b"artifact")
    payload = _result_payload(
        model,
        input_structure,
        output_structure,
        output_report,
    )
    mismatched = DMol3GeometryOptimizationSpec(charge=1)

    with pytest.raises(ValueError, match="total_charge"):
        build_dmol3_relaxed_revision_spec(
            model,
            simulation=mismatched,
            result_payload=payload,
            input_structure=input_structure,
            output_structure=output_structure,
            output_report=output_report,
            script_sha256="c" * 64,
            target_revision=5,
        )
