from __future__ import annotations

from ms_mcp.builders.crystal import write_cif
from ms_mcp.schemas import CrystalSpec
from ms_mcp.validators.structure import validate_structure_file


def sample_spec() -> CrystalSpec:
    return CrystalSpec(
        name="NaCl",
        lattice={"a": 5.64, "b": 5.64, "c": 5.64, "alpha": 90, "beta": 90, "gamma": 90},
        atoms=[
            {"element": "Na", "x": 0.0, "y": 0.0, "z": 0.0},
            {"element": "Cl", "x": 0.5, "y": 0.5, "z": 0.5},
        ],
    )


def test_write_cif_and_validate(tmp_path):
    output = tmp_path / "input.cif"

    write_cif(sample_spec().model_dump(), output)
    text = output.read_text(encoding="utf-8")
    validation = validate_structure_file(output)

    assert "_cell_length_a" in text
    assert "_atom_site_fract_x" in text
    assert validation["ok"] is True
    assert validation["atom_count"] == 2
    assert validation["lattice"]["a"] == 5.64


def test_validate_missing_cif_markers(tmp_path):
    output = tmp_path / "bad.cif"
    output.write_text("data_bad\n", encoding="utf-8")

    validation = validate_structure_file(output)

    assert validation["ok"] is False
    assert any("Missing CIF marker" in problem for problem in validation["problems"])
    assert any("No atom sites" in problem for problem in validation["problems"])
