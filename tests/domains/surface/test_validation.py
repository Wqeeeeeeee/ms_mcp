from __future__ import annotations

import copy
import math

from material_studio_mcp_server.domains.surface import validate
from material_studio_mcp_server.runtime import ValidationStatus
from material_studio_mcp_server.specs import ModelSpec


def _issue_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_fixed_model_passes_with_preview_warning(built_model: ModelSpec) -> None:
    report = validate(built_model)
    facts = {fact.code: fact.value for fact in report.facts}

    assert report.status is ValidationStatus.PASS_WITH_WARNINGS
    assert report.preview_eligible is True
    assert _issue_codes(report) == {"ideal_unrelaxed_preview"}
    assert facts["atom_count"] == 80
    assert facts["substrate_plane_count"] == 8
    assert facts["bottom_carbon_count"] == 8
    assert facts["periodic_carbon_hydrogen_bond_count"] == 16
    assert abs(facts["vacuum_angstrom"] - 15.0) < 1.0e-10
    assert abs(facts["minimum_periodic_contact_angstrom"] - 1.09) < 1.0e-10


def test_metadata_only_tampering_fails_identity_without_changing_coordinates(
    built_model: ModelSpec,
) -> None:
    tampered = built_model.model_copy(deep=True)
    coordinates_before = [
        atom.fractional.as_tuple() for atom in tampered.model.basis_atoms
    ]
    tampered.metadata["surface"]["vacuum_angstrom"] = 15
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert report.preview_eligible is False
    assert "metadata_identity_mismatch" in _issue_codes(report)
    assert [
        atom.fractional.as_tuple() for atom in tampered.model.basis_atoms
    ] == coordinates_before


def test_coordinate_only_tampering_fails_geometry_with_metadata_unchanged(
    built_model: ModelSpec,
) -> None:
    tampered = built_model.model_copy(deep=True)
    metadata_before = copy.deepcopy(tampered.metadata)
    silicon = next(atom for atom in tampered.model.basis_atoms if atom.element == "Si")
    silicon.fractional.x = (silicon.fractional.x + 0.01) % 1.0
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert report.preview_eligible is False
    assert "in_plane_registry_mismatch" in _issue_codes(report)
    assert "metadata_identity_mismatch" not in _issue_codes(report)
    assert tampered.metadata == metadata_before


def test_periodic_duplicate_coordinates_are_rejected(built_model: ModelSpec) -> None:
    tampered = built_model.model_copy(deep=True)
    first, second = tampered.model.basis_atoms[:2]
    second.fractional = first.fractional.model_copy(deep=True)
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert "duplicate_atoms" in _issue_codes(report)


def test_nonduplicate_severe_short_contact_is_rejected(built_model: ModelSpec) -> None:
    tampered = built_model.model_copy(deep=True)
    hydrogens = [atom for atom in tampered.model.basis_atoms if atom.element == "H"]
    source, target = hydrogens[:2]
    target.fractional = source.fractional.model_copy(deep=True)
    target.fractional.x = (
        source.fractional.x + 0.5 / tampered.model.lattice.a
    ) % 1.0
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert "severe_short_contacts" in _issue_codes(report)
    assert "duplicate_atoms" not in _issue_codes(report)


def test_crossed_bottom_hydrogen_azimuth_is_rejected(built_model: ModelSpec) -> None:
    tampered = built_model.model_copy(deep=True)
    crystal = tampered.model
    substrate = [atom for atom in crystal.basis_atoms if atom.element in {"Si", "C"}]
    carbon = min(
        (atom for atom in substrate if atom.element == "C"),
        key=lambda atom: (atom.fractional.z, atom.id),
    )
    hydrogens = [atom for atom in crystal.basis_atoms if atom.element == "H"]

    def periodic_distance(atom) -> float:
        dx = (atom.fractional.x - carbon.fractional.x) * crystal.lattice.a
        dy = (atom.fractional.y - carbon.fractional.y) * crystal.lattice.b
        dz = (atom.fractional.z - carbon.fractional.z) * crystal.lattice.c
        dx -= round(dx / crystal.lattice.a) * crystal.lattice.a
        dy -= round(dy / crystal.lattice.b) * crystal.lattice.b
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    assigned = sorted(hydrogens, key=lambda atom: (periodic_distance(atom), atom.id))[:2]
    component = 1.09 / math.sqrt(3.0)
    carbon_x = carbon.fractional.x * crystal.lattice.a
    carbon_y = carbon.fractional.y * crystal.lattice.b
    carbon_z = carbon.fractional.z * crystal.lattice.c
    for hydrogen, (sign_x, sign_y) in zip(
        assigned,
        ((1.0, -1.0), (-1.0, 1.0)),
    ):
        hydrogen.fractional.x = (
            (carbon_x + sign_x * component) % crystal.lattice.a
        ) / crystal.lattice.a
        hydrogen.fractional.y = (
            (carbon_y + sign_y * component) % crystal.lattice.b
        ) / crystal.lattice.b
        hydrogen.fractional.z = (carbon_z - component) / crystal.lattice.c

    report = validate(tampered)
    assert report.status is ValidationStatus.FAIL
    assert "back_bond_geometry_mismatch" in _issue_codes(report)


def test_extra_metadata_key_fails_identity(built_model: ModelSpec) -> None:
    tampered = built_model.model_copy(deep=True)
    tampered.metadata["unexpected_review_field"] = "not part of the fixed profile"
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert "metadata_identity_mismatch" in _issue_codes(report)


def test_changed_source_provenance_fails_identity(built_model: ModelSpec) -> None:
    tampered = built_model.model_copy(deep=True)
    tampered.metadata["source_provenance"]["provider_revision"] = "278159"
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert "metadata_identity_mismatch" in _issue_codes(report)


def test_degenerate_lattice_returns_fail_report_instead_of_raising(
    built_model: ModelSpec,
) -> None:
    tampered = built_model.model_copy(deep=True)
    tampered.model.lattice.a = 0.0
    report = validate(tampered)

    assert report.status is ValidationStatus.FAIL
    assert report.preview_eligible is False
    assert "invalid_lattice" in _issue_codes(report)
