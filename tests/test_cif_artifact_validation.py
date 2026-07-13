from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server.parsers import parse_crystal_cif, validate_crystal_cif_against_spec
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.translators import write_crystal_cif


def _silicon_spec() -> ModelSpec:
    return ModelSpec.model_validate(
        json.loads(
            Path("src/material_studio_mcp_server/examples/silicon_diamond_spec.json").read_text(
                encoding="utf-8"
            )
        )
    )


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
