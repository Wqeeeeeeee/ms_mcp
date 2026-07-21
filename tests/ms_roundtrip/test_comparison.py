from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from material_studio_mcp_server.ms_roundtrip import (
    RoundtripError,
    compare_roundtrip_cif_bytes,
    validate_fixed_candidate_cif,
)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _permuted_atom_rows(payload: bytes) -> bytes:
    lines = payload.decode("ascii").splitlines()
    first_atom = next(index for index, line in enumerate(lines) if line.startswith("  C_"))
    header = lines[:first_atom]
    atoms = lines[first_atom:]
    return ("\n".join([*header, *reversed(atoms)]) + "\n").encode("ascii")


def _move_first_hydrogen(payload: bytes, fractional_delta: float) -> bytes:
    lines = payload.decode("ascii").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("  H_"):
            fields = line.split()
            fields[-1] = f"{float(fields[-1]) + fractional_delta:.10g}"
            lines[index] = "  " + " ".join(fields)
            break
    return ("\n".join(lines) + "\n").encode("ascii")


def test_exact_roundtrip_passes_all_frozen_thresholds(candidate_path: Path) -> None:
    payload = candidate_path.read_bytes()
    receipt = compare_roundtrip_cif_bytes(
        payload,
        payload,
        expected_input_sha256=_digest(payload),
        expected_output_sha256=_digest(payload),
    )
    assert receipt.passed is True
    assert receipt.mapping_coverage == 1.0
    assert receipt.rms_displacement_angstrom == 0.0
    assert receipt.maximum_displacement_angstrom == 0.0
    assert receipt.maximum_relative_lattice_error == 0.0
    assert receipt.vacuum_absolute_error_angstrom == 0.0
    assert receipt.atom_count == 80
    assert receipt.composition == ("C:32", "H:16", "Si:32")
    assert receipt.scientific_status == "NOT_RUN"


def test_same_species_and_full_atom_permutation_is_canonicalized(
    candidate_path: Path,
) -> None:
    original = candidate_path.read_bytes()
    permuted = _permuted_atom_rows(original)
    receipt = compare_roundtrip_cif_bytes(
        original,
        permuted,
        expected_input_sha256=_digest(original),
        expected_output_sha256=_digest(permuted),
    )
    assert receipt.passed is True
    assert receipt.mapping_coverage == 1.0


def test_large_atomic_displacement_is_a_hard_threshold_failure(
    candidate_path: Path,
) -> None:
    original = candidate_path.read_bytes()
    moved = _move_first_hydrogen(original, 0.01)
    with pytest.raises(RoundtripError):
        compare_roundtrip_cif_bytes(
            original,
            moved,
            expected_input_sha256=_digest(original),
            expected_output_sha256=_digest(moved),
        )


def test_lattice_error_above_frozen_limit_fails(candidate_path: Path) -> None:
    original = candidate_path.read_bytes()
    changed = original.replace(b"_cell_length_a    8.7192", b"_cell_length_a    8.74", 1)
    receipt = compare_roundtrip_cif_bytes(
        original,
        changed,
        expected_input_sha256=_digest(original),
        expected_output_sha256=_digest(changed),
    )
    assert receipt.passed is False
    assert receipt.maximum_relative_lattice_error > 0.001
    assert receipt.lattice_pass is False


def test_composition_change_fails_closed(candidate_path: Path) -> None:
    original = candidate_path.read_bytes()
    changed = original.replace(b" H ", b" He ", 1)
    with pytest.raises(RoundtripError):
        compare_roundtrip_cif_bytes(
            original,
            changed,
            expected_input_sha256=_digest(original),
            expected_output_sha256=_digest(changed),
        )


def test_fixed_candidate_validation_binds_expected_digest(candidate_path: Path) -> None:
    payload = candidate_path.read_bytes()
    receipt = validate_fixed_candidate_cif(payload, expected_sha256=_digest(payload))
    assert receipt.fixed_candidate_match is True
    assert receipt.atom_count == 80
    assert abs(receipt.vacuum_angstrom - 15.0) <= 1.0e-6
    with pytest.raises(RoundtripError):
        validate_fixed_candidate_cif(payload, expected_sha256="0" * 64)
