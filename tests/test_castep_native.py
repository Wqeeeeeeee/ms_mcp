from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from material_studio_mcp_server.parsers import (
    HARTREE_TO_EV,
    audit_castep_native_artifacts,
    parse_castep_bands_text,
    parse_castep_output_text,
    write_castep_band_eigenvalues_csv,
    write_castep_gaussian_dos_csv,
)


_BANDS_ONE_SPIN = """\
Number of k-points   2
Number of spin components 1
Number of electrons   4
Number of eigenvalues   3
Fermi energy (in atomic units)     0.100000
Unit cell vectors
    4.000000    0.000000    0.000000
    0.000000    4.000000    0.000000
    0.000000    0.000000    4.000000
K-point    1  0.00000000  0.00000000  0.00000000  0.50000000
Spin component 1
   -0.30000000
    0.05000000
    0.20000000
K-point    2  0.50000000  0.00000000  0.00000000  0.50000000
Spin component 1
   -0.25000000
    0.08000000
    0.25000000
"""


_BANDS_TWO_SPIN = """\
Number of k-points 1
Number of spin components 2
Number of electrons 2.0 1.0
Number of eigenvalues 2 3
Fermi energies (in atomic units) 0.10 0.12
Unit cell vectors
1 0 0
0 1 0
0 0 1
K-point 1 -0.5 0.0 0.5 1.0
Spin component 1
-0.2
0.3
Spin component 2
-0.1
0.2
0.4
"""


_COMPLETED_CASTEP = """\
 total energy / atom convergence tol.           : 0.1000E-05   eV
 convergence tolerance window                   :          3   cycles
 max. number of SCF cycles                      :        100
------------------------------------------------------------------------ <-- SCF
SCF loop      Energy           Fermi           Energy gain       Timer   <-- SCF
energy          per atom          (sec)   <-- SCF
------------------------------------------------------------------------ <-- SCF
Initial  -7.21477124E+002  0.00000000E+000                        16.65  <-- SCF
      1  -8.47404610E+002  1.04511521E+001   1.25927486E+002      16.77  <-- SCF
      2  -8.58198518E+002  8.13506343E+000   1.07939082E+001      16.87  <-- SCF
     12  -8.58547076E+002  7.47890352E+000  -3.03494338E-008      18.14  <-- SCF
------------------------------------------------------------------------ <-- SCF
Final energy, E             =  -858.5426000919     eV
Final free energy (E-TS)    =  -858.5470757452     eV
WARNING: review the pseudopotential choice
Total time          =     18.64 s
"""


def test_parse_castep_bands_one_spin_and_export_eigenvalues(tmp_path: Path) -> None:
    parsed = parse_castep_bands_text(_BANDS_ONE_SPIN)

    assert parsed.number_of_kpoints == 2
    assert parsed.number_of_spin_components == 1
    assert parsed.eigenvalues_per_spin == [3]
    assert parsed.kpoints[1].fractional == (0.5, 0.0, 0.0)
    assert parsed.kpoints[0].eigenvalues_hartree[0][2] == 0.2

    target = tmp_path / "bands.csv"
    receipt = write_castep_band_eigenvalues_csv(parsed, target)
    assert receipt["row_count"] == 6
    assert receipt["artifact_kind"] == "castep_band_eigenvalues_csv"
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6
    assert float(rows[0]["eigenvalue_ev"]) == pytest.approx(
        -0.3 * HARTREE_TO_EV
    )
    assert float(rows[2]["energy_minus_fermi_ev"]) == pytest.approx(
        0.1 * HARTREE_TO_EV
    )


def test_parse_castep_bands_two_spin_counts() -> None:
    parsed = parse_castep_bands_text(_BANDS_TWO_SPIN)

    assert parsed.number_of_spin_components == 2
    assert parsed.electrons_per_spin == [2.0, 1.0]
    assert parsed.eigenvalues_per_spin == [2, 3]
    assert parsed.fermi_energy_hartree_per_spin == [0.1, 0.12]
    assert len(parsed.kpoints[0].eigenvalues_hartree[1]) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.replace("K-point    2", "K-point    3"),
        lambda value: value.replace("    0.25000000\n", ""),
        lambda value: value + "unexpected trailing data\n",
        lambda value: value.replace("    0.20000000", "    NaN"),
    ],
)
def test_parse_castep_bands_rejects_invalid_contract(mutation) -> None:
    with pytest.raises(ValueError):
        parse_castep_bands_text(mutation(_BANDS_ONE_SPIN))


def test_gaussian_dos_export_is_deterministic_and_spin_aware(
    tmp_path: Path,
) -> None:
    parsed = parse_castep_bands_text(_BANDS_ONE_SPIN)
    target = tmp_path / "dos.csv"

    receipt = write_castep_gaussian_dos_csv(
        parsed,
        target,
        smearing_width_ev=0.2,
        energy_max_ev=6.0,
        grid_points=501,
    )

    assert receipt["row_count"] == 501
    assert receipt["spin_degeneracy"] == 2.0
    assert receipt["kpoint_weight_sum_before_normalization"] == pytest.approx(1.0)
    first_hash = receipt["sha256"]
    target.unlink()
    second = write_castep_gaussian_dos_csv(
        parsed,
        target,
        smearing_width_ev=0.2,
        energy_max_ev=6.0,
        grid_points=501,
    )
    assert second["sha256"] == first_hash
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    energies = [float(row["energy_minus_fermi_ev"]) for row in rows]
    dos = [float(row["total_dos_states_per_ev"]) for row in rows]
    step = energies[1] - energies[0]
    integrated_states = sum(dos) * step
    assert integrated_states == pytest.approx(6.0, rel=0.03)


def test_castep_output_audit_preserves_convergence_limit() -> None:
    audit = parse_castep_output_text(_COMPLETED_CASTEP)

    assert audit["status"] == "completed_below_max_cycles"
    assert audit["run_completed"] is True
    assert audit["scientific_convergence_verified"] is False
    assert audit["max_scf_cycles"] == 100
    assert audit["last_scf_iteration"] == 12
    assert audit["maximum_scf_cycles_reached"] is False
    assert audit["final_energy_ev"] == pytest.approx(-858.5426000919)
    assert audit["final_free_energy_ev"] == pytest.approx(-858.5470757452)
    assert audit["total_time_seconds"] == pytest.approx(18.64)
    assert audit["warning_count"] == 1


def test_castep_output_audit_detects_maximum_cycles_and_fatal_errors() -> None:
    maximum = parse_castep_output_text(
        _COMPLETED_CASTEP.replace(
            "max. number of SCF cycles                      :        100",
            "max. number of SCF cycles                      :         12",
        )
    )
    assert maximum["status"] == "maximum_scf_cycles_reached"
    assert maximum["maximum_scf_cycles_reached"] is True
    assert maximum["scientific_convergence_verified"] is False

    fatal = parse_castep_output_text(_COMPLETED_CASTEP + "\nERROR: failed to converge\n")
    assert fatal["status"] == "fatal_error"
    assert fatal["run_completed"] is False
    assert fatal["fatal_marker_count"] == 1


def test_native_artifact_audit_exports_band_and_total_dos_data(
    tmp_path: Path,
) -> None:
    native = tmp_path / "native"
    native.mkdir()
    bands_path = native / "silicon.bands"
    castep_path = native / "silicon.castep"
    bands_path.write_text(_BANDS_ONE_SPIN, encoding="utf-8")
    castep_path.write_text(_COMPLETED_CASTEP, encoding="utf-8")
    manifest = [_manifest_item(castep_path), _manifest_item(bands_path)]

    band_audit, band_artifacts = audit_castep_native_artifacts(
        manifest,
        task="BandStructure",
        destination=tmp_path / "band_derived",
    )
    assert band_audit["status"] == "complete"
    assert band_audit["numeric_curve_data_exported"] is True
    assert band_audit["numeric_curve_kind"] == "native_castep_band_eigenvalues"
    assert band_audit["native_band_kpoint_path_exported"] is True
    assert band_audit["scientific_convergence_verified"] is False
    assert len(band_artifacts) == 1
    assert Path(band_artifacts[0]["path"]).is_file()

    dos_audit, dos_artifacts = audit_castep_native_artifacts(
        manifest,
        task="DensityOfStates",
        destination=tmp_path / "dos_derived",
        dos_integration_method="Smearing",
        dos_smearing_width_ev=0.2,
        dos_energy_max_ev=6.0,
    )
    assert dos_audit["numeric_curve_data_exported"] is True
    assert dos_audit["numeric_curve_kind"] == (
        "mcp_gaussian_total_dos_from_native_bands"
    )
    assert len(dos_artifacts) == 2
    assert {item["artifact_kind"] for item in dos_artifacts} == {
        "castep_band_eigenvalues_csv",
        "castep_gaussian_total_dos_csv",
    }

    castep_path.write_text(
        _COMPLETED_CASTEP.replace(
            "max. number of SCF cycles                      :        100",
            "max. number of SCF cycles                      :         12",
        ),
        encoding="utf-8",
    )
    maximum_cycle_audit, _ = audit_castep_native_artifacts(
        [_manifest_item(castep_path), _manifest_item(bands_path)],
        task="BandStructure",
        destination=tmp_path / "maximum_cycle_derived",
    )
    assert maximum_cycle_audit["status"] == "review_required"
    assert maximum_cycle_audit["castep_output_audit"]["status"] == (
        "maximum_scf_cycles_reached"
    )


def test_native_artifact_audit_keeps_pdos_and_ambiguous_files_fail_closed(
    tmp_path: Path,
) -> None:
    native = tmp_path / "native"
    native.mkdir()
    first = native / "first.bands"
    second = native / "second.bands"
    first.write_text(_BANDS_TWO_SPIN, encoding="utf-8")
    second.write_text(_BANDS_TWO_SPIN, encoding="utf-8")

    pdos_audit, pdos_artifacts = audit_castep_native_artifacts(
        [_manifest_item(first)],
        task="ProjectedDensityOfStates",
        destination=tmp_path / "pdos_derived",
    )
    assert pdos_audit["status"] == "partial"
    assert pdos_audit["numeric_curve_data_exported"] is False
    assert pdos_audit["pdos_projection_weights_exported"] is False
    assert len(pdos_artifacts) == 1

    ambiguous, artifacts = audit_castep_native_artifacts(
        [_manifest_item(first), _manifest_item(second)],
        task="BandStructure",
        destination=tmp_path / "ambiguous_derived",
    )
    assert ambiguous["status"] == "review_required"
    assert ambiguous["numeric_curve_data_exported"] is False
    assert artifacts == []
    assert not (tmp_path / "ambiguous_derived").exists()

    tampered = _manifest_item(first)
    tampered["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="binding mismatch"):
        audit_castep_native_artifacts(
            [tampered],
            task="BandStructure",
            destination=tmp_path / "tampered_derived",
        )


@pytest.mark.parametrize(
    ("integration_method", "smearing_width"),
    [
        (None, 0.2),
        ("Interpolation", 0.2),
        ("Smearing", None),
    ],
)
def test_native_dos_export_requires_explicit_smearing_contract(
    tmp_path: Path,
    integration_method: str | None,
    smearing_width: float | None,
) -> None:
    bands_path = tmp_path / "silicon.bands"
    bands_path.write_text(_BANDS_ONE_SPIN, encoding="utf-8")

    audit, artifacts = audit_castep_native_artifacts(
        [_manifest_item(bands_path)],
        task="DensityOfStates",
        destination=tmp_path / "derived",
        dos_integration_method=integration_method,
        dos_smearing_width_ev=smearing_width,
    )

    assert audit["numeric_curve_data_exported"] is False
    assert audit["numeric_curve_kind"] is None
    assert [item["artifact_kind"] for item in artifacts] == [
        "castep_band_eigenvalues_csv"
    ]
    assert any(
        "requires the reviewed Smearing method" in item
        for item in audit["warnings"]
    )


def _manifest_item(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }
