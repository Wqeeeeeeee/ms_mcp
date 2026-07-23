from __future__ import annotations

from pathlib import Path

from ms_mcp.builders.crystal import write_cif
from ms_mcp.interfaces.cu_sio2 import build_cu_sio2_interface_spec
from ms_mcp.schemas import CuSiO2InterfaceSpec
from ms_mcp.server import build_cu_sio2_interface
from ms_mcp.validators.structure import validate_structure_file


def test_cu_sio2_interface_default_lattice_match():
    spec = build_cu_sio2_interface_spec(CuSiO2InterfaceSpec())

    assert spec["lattice"]["a"] == 7.16
    assert spec["lattice"]["b"] == 7.16
    assert spec["lattice_match"]["atom_counts"] == {"Cu": 48, "Si": 8, "O": 16}
    assert abs(spec["lattice_match"]["cu_strain_a_percent"] - -0.968188105117565) < 1e-6
    assert spec["lattice_match"]["sio2_strain_a_percent"] == 0.0


def test_cu_sio2_interface_writes_valid_cif(tmp_path):
    spec = build_cu_sio2_interface_spec(CuSiO2InterfaceSpec())
    output = tmp_path / "cu_sio2.cif"

    write_cif(spec, output)
    validation = validate_structure_file(output)

    assert validation["ok"] is True
    assert validation["atom_count"] == 72
    assert validation["lattice"]["a"] == 7.16


def test_build_cu_sio2_interface_server_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("MS_WORKSPACE", str(tmp_path / "workspace"))

    result = build_cu_sio2_interface(CuSiO2InterfaceSpec(name="test_interface"))

    assert Path(result["path"]).exists()
    assert Path(result["lattice_match_path"]).exists()
    assert result["validation"]["ok"] is True
    assert result["lattice_match"]["atom_counts"]["Cu"] == 48
