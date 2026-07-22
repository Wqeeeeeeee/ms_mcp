from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server.parsers import parse_crystal_cif, validate_crystal_cif_against_spec
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.translators import write_crystal_cif


_MS_EXPORT_POLICY = "materials_studio_20_1_export"
_FRACTIONAL_EXPORT_CAP = 0.000005000001
_LATTICE_EXPORT_CAP = 0.000050000001
_FINE_FRACTIONAL_EXPORT_TOLERANCE = 0.000000500001
_FINE_LATTICE_EXPORT_TOLERANCE = 0.000000500001


def _silicon_spec() -> ModelSpec:
    return ModelSpec.model_validate(
        json.loads(
            Path("src/material_studio_mcp_server/examples/silicon_diamond_spec.json").read_text(
                encoding="utf-8"
            )
        )
    )


def _synthetic_export_spec() -> ModelSpec:
    return ModelSpec.model_validate(
        {
            "project_id": "synthetic_cif_export",
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": {
                "name": "synthetic_two_element_export_fixture",
                "lattice": {
                    "a": 7.123456,
                    "b": 8.234564,
                    "c": 9.345674,
                    "alpha": 88.123456,
                    "beta": 91.234564,
                    "gamma": 93.345674,
                },
                "basis_atoms": [
                    {
                        "id": "SiNearMid",
                        "element": "Si",
                        "fractional": [0.000005, 0.2, 0.3],
                    },
                    {
                        "id": "SiOrigin",
                        "element": "Si",
                        "fractional": [0.0, 0.2, 0.3],
                    },
                    {
                        "id": "CWrapped",
                        "element": "C",
                        "fractional": [0.999996, 0.7, 0.8],
                    },
                    {
                        "id": "CInterior",
                        "element": "C",
                        "fractional": [0.375004, 0.5, 0.625004],
                    },
                ],
                "operations": [],
            },
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 400,
                "kpoint_separation": 0.08,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 1,
                "require_convergence": False,
                "notes": [],
            },
            "metadata": {"source": "synthetic_test_fixture"},
        }
    )


def _write_synthetic_export_cif(
    path: Path,
    *,
    lattice_a: str = "7.1235",
    lattice_b: str = "8.2346",
    interior_element: str = "C",
    interior_x: str = "0.37500",
    interior_y: str = "0.50000",
) -> Path:
    path.write_text(
        "\n".join(
            [
                "data_synthetic_export",
                f"_cell_length_a {lattice_a}",
                f"_cell_length_b {lattice_b}",
                "_cell_length_c 9.3457",
                "_cell_angle_alpha 88.1235",
                "_cell_angle_beta 91.2346",
                "_cell_angle_gamma 93.3457",
                "loop_",
                "_atom_site_label",
                "_atom_site_type_symbol",
                "_atom_site_fract_x",
                "_atom_site_fract_y",
                "_atom_site_fract_z",
                "MS_Si_A Si 0.00000 2.00000E-1 0.30000(2)",
                "MS_C_A C 0.00000 0.70000 0.80000",
                "MS_Si_B Si 0.00001 0.20000 0.30000",
                f"MS_C_B {interior_element} {interior_x} {interior_y} 0.62500",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_generated_crystal_cif_round_trip_matches_model_spec(tmp_path: Path) -> None:
    spec = _silicon_spec()
    path = write_crystal_cif(spec.model, tmp_path / "silicon.cif")

    parsed = parse_crystal_cif(path)
    validation = validate_crystal_cif_against_spec(spec.model, path)

    assert parsed["ok"] is True
    assert parsed["atom_count"] == len(spec.model.basis_atoms)
    assert parsed["element_counts"] == {"Si": len(spec.model.basis_atoms)}
    assert validation["ok"] is True
    assert validation["status"] == "matched"
    assert validation["atom_count_matches"] is True
    assert validation["atom_ids_match"] is True
    assert validation["atom_elements_match"] is True
    assert validation["fractional_coordinates_match"] is True
    assert validation["lattice_matches"] is True
    assert len(validation["sha256"]) == 64


def test_crystal_cif_round_trip_detects_atom_and_lattice_tampering(tmp_path: Path) -> None:
    spec = _silicon_spec()
    path = write_crystal_cif(spec.model, tmp_path / "tampered.cif")
    first_atom = spec.model.basis_atoms[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered: list[str] = []
    for line in lines:
        if line.startswith("_cell_length_a"):
            tampered.append("_cell_length_a    9.9")
        elif line.strip().startswith(f"{first_atom.id} "):
            tokens = line.split()
            tokens[1] = "P"
            tokens[2] = "0.125"
            tampered.append("  " + " ".join(tokens))
        else:
            tampered.append(line)
    path.write_text("\n".join(tampered) + "\n", encoding="utf-8")

    validation = validate_crystal_cif_against_spec(spec.model, path)

    assert validation["ok"] is False
    assert validation["status"] == "mismatch"
    assert validation["element_counts_match"] is False
    assert validation["atom_elements_match"] is False
    assert validation["fractional_coordinates_match"] is False
    assert validation["lattice_matches"] is False
    assert validation["element_mismatches"][0]["atom_id"] == first_atom.id
    assert validation["fractional_coordinate_mismatches"][0]["atom_id"] == first_atom.id
    assert validation["lattice_mismatches"][0]["field"] == "a"


def test_crystal_cif_round_trip_accepts_periodically_equivalent_fractional_coordinate(
    tmp_path: Path,
) -> None:
    spec = _silicon_spec()
    path = write_crystal_cif(spec.model, tmp_path / "periodic.cif")
    first_atom = spec.model.basis_atoms[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    shifted: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{first_atom.id} "):
            tokens = line.split()
            tokens[2] = str(float(tokens[2]) + 1.0)
            shifted.append("  " + " ".join(tokens))
        else:
            shifted.append(line)
    path.write_text("\n".join(shifted) + "\n", encoding="utf-8")

    validation = validate_crystal_cif_against_spec(spec.model, path)

    assert validation["ok"] is True
    assert validation["fractional_coordinates_match"] is True
    assert validation["max_fractional_delta"] == 0.0


def test_strict_default_thresholds_remain_unchanged_with_preserved_labels(
    tmp_path: Path,
) -> None:
    spec = _synthetic_export_spec()
    path = write_crystal_cif(spec.model, tmp_path / "strict_thresholds.cif")
    first_atom = spec.model.basis_atoms[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    shifted: list[str] = []
    for line in lines:
        if line.startswith("_cell_length_a"):
            shifted.append(
                f"_cell_length_a    {spec.model.lattice.a + 2e-7:.12f}"
            )
        elif line.strip().startswith(f"{first_atom.id} "):
            tokens = line.split()
            tokens[2] = f"{first_atom.fractional.x + 2e-8:.12f}"
            shifted.append("  " + " ".join(tokens))
        else:
            shifted.append(line)
    path.write_text("\n".join(shifted) + "\n", encoding="utf-8")

    strict = validate_crystal_cif_against_spec(spec.model, path)
    explicitly_wider_strict = validate_crystal_cif_against_spec(
        spec.model,
        path,
        coordinate_tolerance=3e-8,
        lattice_tolerance=3e-7,
    )

    assert strict["policy"] == "strict"
    assert strict["atom_ids_match"] is True
    assert strict["fractional_coordinates_match"] is False
    assert strict["lattice_matches"] is False
    assert strict["max_fractional_delta"] == pytest.approx(2e-8)
    assert strict["max_lattice_delta"] == pytest.approx(2e-7)
    assert strict["ok"] is False
    assert explicitly_wider_strict["policy"] == "strict"
    assert explicitly_wider_strict["ok"] is True


def test_crystal_cif_parser_reports_missing_and_malformed_files(tmp_path: Path) -> None:
    spec = _silicon_spec()
    missing = validate_crystal_cif_against_spec(spec.model, tmp_path / "missing.cif")
    assert missing["ok"] is False
    assert missing["status"] == "missing"

    malformed_path = tmp_path / "malformed.cif"
    malformed_path.write_text("data_bad\n_cell_length_a ?\n", encoding="utf-8")
    malformed = validate_crystal_cif_against_spec(spec.model, malformed_path)
    assert malformed["ok"] is False
    assert malformed["status"] == "parse_failed"
    assert any("Required CIF" in error or "Invalid CIF" in error for error in malformed["errors"])


def test_strict_default_rejects_regenerated_labels_while_materials_studio_policy_matches(
    tmp_path: Path,
) -> None:
    crystal = _synthetic_export_spec().model
    path = _write_synthetic_export_cif(tmp_path / "synthetic_export.cif")

    parsed = parse_crystal_cif(path)
    strict = validate_crystal_cif_against_spec(crystal, path)
    exported = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert parsed["lattice_lexemes"]["a"] == "7.1235"
    assert parsed["atoms"][0]["fractional_lexemes"] == [
        "0.00000",
        "2.00000E-1",
        "0.30000(2)",
    ]
    assert strict["policy"] == "strict"
    assert strict["ok"] is False
    assert strict["atom_ids_match"] is False
    assert "atom_label_mismatch" in strict["rejection_reasons"]

    assert exported["ok"] is True
    assert exported["policy"] == _MS_EXPORT_POLICY
    assert exported["mapping_method"] == (
        "deterministic_same_element_periodic_bipartite"
    )
    assert exported["mapping_coverage"] == 1.0
    assert exported["mapping_ambiguous"] is False
    assert exported["labels_are_diagnostic_only"] is True
    assert exported["label_set_preserved"] is False
    assert exported["labels_preserved"] is False
    assert exported["label_preservation_status"] == "regenerated"
    assert exported["tolerance_derived_from_exported_lexemes"] is True
    assert exported["maximum_fractional_export_tolerance"] == (
        _FRACTIONAL_EXPORT_CAP
    )
    assert exported["maximum_lattice_export_tolerance"] == _LATTICE_EXPORT_CAP
    assert exported["tolerance_derivation"]["fractional_tokens"][0][
        "tolerances"
    ] == [
        _FRACTIONAL_EXPORT_CAP,
        _FINE_FRACTIONAL_EXPORT_TOLERANCE,
        _FRACTIONAL_EXPORT_CAP,
    ]

    mapping = {
        item["expected_atom_id"]: item for item in exported["atom_mapping"]
    }
    assert mapping["SiNearMid"]["exported_atom_label"] == "MS_Si_B"
    assert mapping["SiOrigin"]["exported_atom_label"] == "MS_Si_A"
    assert mapping["CWrapped"]["max_delta"] < _FRACTIONAL_EXPORT_CAP


def test_materials_studio_precision_caps_reject_coarse_geometry(
    tmp_path: Path,
) -> None:
    spec = _synthetic_export_spec()
    atoms = list(spec.model.basis_atoms)
    interior_index = next(
        index for index, atom in enumerate(atoms) if atom.id == "CInterior"
    )
    interior = atoms[interior_index]
    atoms[interior_index] = interior.model_copy(
        update={
            "fractional": interior.fractional.model_copy(update={"x": 0.375006})
        }
    )
    lattice = spec.model.lattice.model_copy(update={"a": 7.12356})
    crystal = spec.model.model_copy(
        update={"basis_atoms": atoms, "lattice": lattice}
    )
    path = _write_synthetic_export_cif(
        tmp_path / "coarse_geometry.cif",
        lattice_a="7.1235",
        interior_x="0.3750",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        coordinate_tolerance=1.0,
        lattice_tolerance=1.0,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    assert validation["coordinate_tolerance"] == 1.0
    assert validation["lattice_tolerance"] == 1.0
    assert validation["maximum_fractional_export_tolerance"] == (
        _FRACTIONAL_EXPORT_CAP
    )
    assert validation["maximum_lattice_export_tolerance"] == _LATTICE_EXPORT_CAP
    assert "periodic_fractional_geometry_mismatch" in validation[
        "rejection_reasons"
    ]
    assert "lattice_geometry_mismatch" in validation["rejection_reasons"]
    assert validation["mapping_coverage"] == 0.75
    assert validation["lattice_mismatches"][0]["field"] == "a"


def test_materials_studio_policy_applies_precision_per_coordinate_token(
    tmp_path: Path,
) -> None:
    crystal = _synthetic_export_spec().model
    path = _write_synthetic_export_cif(
        tmp_path / "mixed_precision_geometry.cif",
        interior_y="0.500001",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    assert validation["mapping_coverage"] == 0.75
    mismatch = next(
        item
        for item in validation["fractional_coordinate_mismatches"]
        if item["atom_id"] == "CInterior"
    )
    assert mismatch["delta"][0] < _FRACTIONAL_EXPORT_CAP
    assert mismatch["delta"][1] == pytest.approx(0.000001)
    assert mismatch["tolerance"][1] == _FINE_FRACTIONAL_EXPORT_TOLERANCE
    assert mismatch["delta"][1] > mismatch["tolerance"][1]


def test_materials_studio_policy_preserves_large_shift_fractional_lexeme(
    tmp_path: Path,
) -> None:
    crystal = _synthetic_export_spec().model
    path = _write_synthetic_export_cif(
        tmp_path / "large_periodic_shift.cif",
        interior_x="10000000000000000.375014",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    mismatch = next(
        item
        for item in validation["fractional_coordinate_mismatches"]
        if item["atom_id"] == "CInterior"
    )
    assert mismatch["delta"][0] == pytest.approx(0.00001)
    assert mismatch["delta"][0] > mismatch["tolerance"][0]


def test_materials_studio_policy_applies_precision_per_lattice_token(
    tmp_path: Path,
) -> None:
    crystal = _synthetic_export_spec().model
    path = _write_synthetic_export_cif(
        tmp_path / "mixed_lattice_precision.cif",
        lattice_b="8.234563",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    assert validation["fractional_coordinates_match"] is True
    assert [item["field"] for item in validation["lattice_mismatches"]] == ["b"]
    mismatch = validation["lattice_mismatches"][0]
    assert mismatch["delta"] == pytest.approx(0.000001)
    assert mismatch["tolerance"] == _FINE_LATTICE_EXPORT_TOLERANCE
    assert mismatch["delta"] > mismatch["tolerance"]


def test_materials_studio_policy_preserves_large_lattice_lexeme_delta(
    tmp_path: Path,
) -> None:
    spec = _synthetic_export_spec()
    crystal = spec.model.model_copy(
        update={
            "lattice": spec.model.lattice.model_copy(
                update={"a": 10000000000000000.0}
            )
        }
    )
    path = _write_synthetic_export_cif(
        tmp_path / "large_lattice_delta.cif",
        lattice_a="10000000000000000.4",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    assert [item["field"] for item in validation["lattice_mismatches"]] == ["a"]
    mismatch = validation["lattice_mismatches"][0]
    assert mismatch["delta"] == pytest.approx(0.4)
    assert mismatch["delta_decimal"] == "0.4"
    assert mismatch["delta"] > mismatch["tolerance"]


def test_materials_studio_policy_rejects_exact_composition_change(
    tmp_path: Path,
) -> None:
    crystal = _synthetic_export_spec().model
    path = _write_synthetic_export_cif(
        tmp_path / "composition_change.cif",
        interior_element="Si",
    )

    validation = validate_crystal_cif_against_spec(
        crystal,
        path,
        policy=_MS_EXPORT_POLICY,
    )

    assert validation["ok"] is False
    assert validation["atom_count_matches"] is True
    assert validation["element_counts_match"] is False
    assert validation["mapping_coverage"] == 0.0
    assert validation["rejection_reasons"] == ["element_composition_mismatch"]


def test_materials_studio_ambiguous_mapping_has_order_independent_label_status(
    tmp_path: Path,
) -> None:
    spec = _synthetic_export_spec()
    coincident_fractional = spec.model.basis_atoms[0].fractional.model_copy(
        update={"x": 0.125, "y": 0.25, "z": 0.375}
    )
    coincident_atoms = [
        atom.model_copy(update={"fractional": coincident_fractional})
        for atom in spec.model.basis_atoms[:2]
    ]
    crystal = spec.model.model_copy(update={"basis_atoms": coincident_atoms})

    def write_ambiguous(path: Path, labels: list[str]) -> Path:
        path.write_text(
            "\n".join(
                [
                    "data_ambiguous_synthetic",
                    "_cell_length_a 7.1235",
                    "_cell_length_b 8.2346",
                    "_cell_length_c 9.3457",
                    "_cell_angle_alpha 88.1235",
                    "_cell_angle_beta 91.2346",
                    "_cell_angle_gamma 93.3457",
                    "loop_",
                    "_atom_site_label",
                    "_atom_site_type_symbol",
                    "_atom_site_fract_x",
                    "_atom_site_fract_y",
                    "_atom_site_fract_z",
                    *(f"{label} Si 0.12500 0.25000 0.37500" for label in labels),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    forward = validate_crystal_cif_against_spec(
        crystal,
        write_ambiguous(
            tmp_path / "ambiguous_forward.cif",
            ["SiNearMid", "SiOrigin"],
        ),
        policy=_MS_EXPORT_POLICY,
    )
    reversed_rows = validate_crystal_cif_against_spec(
        crystal,
        write_ambiguous(
            tmp_path / "ambiguous_reversed.cif",
            ["SiOrigin", "SiNearMid"],
        ),
        policy=_MS_EXPORT_POLICY,
    )

    for validation in (forward, reversed_rows):
        assert validation["ok"] is True
        assert validation["mapping_coverage"] == 1.0
        assert validation["mapping_ambiguous"] is True
        assert validation["label_set_preserved"] is True
        assert validation["labels_preserved"] is None
        assert validation["label_preservation_status"] == "ambiguous"
