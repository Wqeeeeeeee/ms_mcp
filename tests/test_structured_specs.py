from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.specs import CastepEnergySpec, ForciteDynamicsSpec, ModelSpec
from material_studio_mcp_server.specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from material_studio_mcp_server.specs.molecule import AtomSpec, BondSpec, MoleculeSpec
from material_studio_mcp_server.specs.patch import SemanticPatchOperation


EXAMPLES = Path("src/material_studio_mcp_server/examples")


def test_example_specs_validate() -> None:
    for path in EXAMPLES.glob("*_spec.json"):
        spec = ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert spec.project_id


def test_6h_silicon_carbide_example_preserves_reviewed_scxrd_structure() -> None:
    payload = json.loads((EXAMPLES / "silicon_carbide_6h_hexagonal_spec.json").read_text(encoding="utf-8"))
    spec = ModelSpec.model_validate(payload)

    assert spec.model_type == "crystal"
    assert spec.model.lattice.a == 3.081
    assert spec.model.lattice.c == 15.1248
    assert len(spec.model.basis_atoms) == 12
    assert [atom.element for atom in spec.model.basis_atoms].count("Si") == 6
    assert [atom.element for atom in spec.model.basis_atoms].count("C") == 6
    assert spec.model.basis_atoms[2].fractional.model_dump(mode="json") == {
        "x": 0.333333,
        "y": 0.666667,
        "z": 0.1664,
        "allow_outside_cell": False,
    }
    assert spec.model.basis_atoms[8].fractional.model_dump(mode="json") == {
        "x": 0.666667,
        "y": 0.333333,
        "z": 0.208,
        "allow_outside_cell": False,
    }
    assert spec.metadata["polytype"] == "6H"
    assert spec.metadata["space_group"] == "P63mc"
    assert spec.metadata["space_group_number"] == 186
    assert spec.metadata["stacking_sequence"] == "ABCACB"
    assert spec.metadata["source_doi"] == "10.2138/am.2007.2346"


def test_invalid_element_fails() -> None:
    with pytest.raises(ValidationError):
        AtomSpec(id="X1", element="Bad", xyz_angstrom=[0, 0, 0])


def test_duplicate_atom_ids_fail() -> None:
    with pytest.raises(ValidationError):
        MoleculeSpec(
            name="bad",
            atoms=[
                AtomSpec(id="C1", element="C", xyz_angstrom=[0, 0, 0]),
                AtomSpec(id="C1", element="C", xyz_angstrom=[1, 0, 0]),
            ],
        )


def test_bond_to_missing_atom_fails() -> None:
    with pytest.raises(ValidationError):
        MoleculeSpec(
            name="bad",
            atoms=[AtomSpec(id="C1", element="C", xyz_angstrom=[0, 0, 0])],
            bonds=[BondSpec(atom1="C1", atom2="H1")],
        )


def test_invalid_fractional_coordinate_fails() -> None:
    with pytest.raises(ValidationError):
        CrystalSpec(
            name="bad",
            lattice=LatticeSpec(a=1, b=1, c=1, alpha=90, beta=90, gamma=90),
            basis_atoms=[BasisAtomSpec(id="C1", element="C", fractional=[1.2, 0, 0])],
        )


def test_forcite_dynamics_requires_temperature_for_nvt() -> None:
    with pytest.raises(ValidationError):
        ForciteDynamicsSpec(ensemble="NVT", timestep_fs=1.0, total_time_ps=1.0)


def test_simulation_module_routes_minimal_castep_payload() -> None:
    spec = ModelSpec.model_validate(
        {
            "project_id": "minimal_castep",
            "model_type": "molecule",
            "model": {
                "name": "hydrogen",
                "atoms": [{"id": "H1", "element": "H", "xyz_angstrom": [0, 0, 0]}],
            },
            "simulation": {"module": "CASTEP", "task": "Energy"},
        }
    )

    assert isinstance(spec.simulation, CastepEnergySpec)
    assert spec.simulation.task == "Energy"


def test_simulation_specs_reject_mismatched_modules() -> None:
    with pytest.raises(ValidationError):
        CastepEnergySpec(module="Forcite")
    with pytest.raises(ValidationError):
        ForciteDynamicsSpec(
            module="CASTEP",
            ensemble="NVE",
            timestep_fs=1.0,
            total_time_ps=1.0,
        )


def test_semantic_patch_operation_type_is_enumerated() -> None:
    with pytest.raises(ValidationError):
        SemanticPatchOperation.model_validate({"type": "unsupported_operation"})

    schema = json.loads((Path("src/material_studio_mcp_server/schemas/patch_spec.schema.json")).read_text(encoding="utf-8"))
    operation_type = schema["$defs"]["SemanticPatchOperation"]["properties"]["type"]
    assert "set_bond_type" in operation_type["enum"]
    assert "set_vacuum" in operation_type["enum"]
    assert "center_slab" in operation_type["enum"]
    assert "set_metadata" in operation_type["enum"]
    assert "reconcile_dopant_metadata" in operation_type["enum"]
    assert "set_gate_stack_thickness" in operation_type["enum"]
    assert "translate_crystal_atoms" in operation_type["enum"]
    assert "rotate_crystal_atoms" in operation_type["enum"]
    assert "make_commensurate_twisted_bilayer" in operation_type["enum"]
    task_schema = schema["$defs"]["SemanticPatchOperation"]["properties"]["task"]
    assert task_schema["anyOf"][0]["$ref"] == "#/$defs/CastepTask"
    assert "metadata_updates" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "atom_ids" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "distance_angstrom" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "angle_degrees" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "pivot_fractional" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "wrap_fractional" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "commensurate_m" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "commensurate_n" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "interlayer_distance_angstrom" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "twist_orientation" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "max_atoms" in schema["$defs"]["SemanticPatchOperation"]["properties"]
    assert "unsupported_operation" not in operation_type["enum"]


def test_static_structured_schemas_are_not_placeholders() -> None:
    expected = {
        "model_spec.schema.json": {"project_id", "model_type", "model", "simulation"},
        "molecule_spec.schema.json": {"name", "atoms", "bonds"},
        "crystal_spec.schema.json": {"name", "lattice", "basis_atoms"},
        "castep_spec.schema.json": {"module", "task", "functional"},
        "patch_spec.schema.json": {"project_id", "base_revision", "operations"},
    }
    for filename, fields in expected.items():
        schema = json.loads((Path("src/material_studio_mcp_server/schemas") / filename).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert fields <= set(schema.get("properties", {}))

    forcite_schema = json.loads((Path("src/material_studio_mcp_server/schemas/forcite_spec.schema.json")).read_text(encoding="utf-8"))
    assert forcite_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "ForciteOptimizationSpec" in forcite_schema["$defs"]
    assert "ForciteDynamicsSpec" in forcite_schema["$defs"]
    assert forcite_schema["$defs"]["ForciteOptimizationSpec"]["properties"]["module"]["const"] == "Forcite"
    assert forcite_schema["$defs"]["ForciteDynamicsSpec"]["properties"]["module"]["const"] == "Forcite"

    castep_schema = json.loads((Path("src/material_studio_mcp_server/schemas/castep_spec.schema.json")).read_text(encoding="utf-8"))
    assert castep_schema["properties"]["task"]["$ref"] == "#/$defs/CastepTask"
    assert castep_schema["properties"]["module"]["const"] == "CASTEP"
    assert castep_schema["$defs"]["CastepTask"]["enum"] == [
        "Energy",
        "GeometryOptimization",
        "BandStructure",
        "DensityOfStates",
        "ProjectedDensityOfStates",
        "Optics",
        "Phonon",
        "ElasticConstants",
    ]
