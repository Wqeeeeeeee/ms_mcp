from __future__ import annotations

import hashlib

import pytest

from material_studio_mcp_server.canonicalization import (
    CifParseError,
    SymmetryExpressionError,
    closest_lattice_image,
    expand_cif_symmetry,
    parse_cif_bytes,
    parse_cif_structure,
    parse_symmetry_expression,
)
from material_studio_mcp_server.canonicalization.cif import (
    MAX_CIF_BYTES,
    MAX_TOKEN_CHARACTERS,
)

from .conftest import SYNTHETIC_CIF_BYTES


def test_bounded_cif_parser_hashes_then_parses_required_subset() -> None:
    digest = hashlib.sha256(SYNTHETIC_CIF_BYTES).hexdigest()
    parsed = parse_cif_bytes(
        SYNTHETIC_CIF_BYTES,
        expected_sha256=digest,
        expected_byte_count=len(SYNTHETIC_CIF_BYTES),
    )
    expanded = expand_cif_symmetry(parsed)
    assert parsed.raw_sha256 == digest
    assert parsed.raw_byte_count == len(SYNTHETIC_CIF_BYTES)
    assert len(parsed.asymmetric_sites) == 8
    assert len(parsed.symmetry_operations) == 1
    assert len(expanded.sites) == 8
    assert parse_cif_structure(SYNTHETIC_CIF_BYTES) == expanded


def test_cif_tokenizer_safely_skips_bounded_semicolon_metadata() -> None:
    content = SYNTHETIC_CIF_BYTES.replace(
        b"_cell_length_a 4.0",
        b"_publ_section_title\n;Synthetic metadata\nwith a second line\n;\n_cell_length_a 4.0",
    )
    assert len(parse_cif_structure(content).sites) == 8


def test_cif_text_field_many_short_lines_hits_character_bound() -> None:
    content = (
        b"data_many_lines\n_note\n;\n"
        + b"\n" * (MAX_TOKEN_CHARACTERS + 1)
        + b";\n"
    )
    with pytest.raises(CifParseError, match="text field exceeds"):
        parse_cif_bytes(content)


def test_symmetry_parser_uses_closed_rational_affine_grammar() -> None:
    operation = parse_symmetry_expression("-x+1/2, y+1/3, z")
    assert operation.rotation == ((-1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert operation.translation[0].numerator == 1
    assert operation.translation[0].denominator == 2
    assert operation.translation[1].numerator == 1
    assert operation.translation[1].denominator == 3


@pytest.mark.parametrize(
    "expression",
    (
        "x,y,__import__('os')",
        "x,y,z*2",
        "x,y,0.5+z",
        "x,y",
        "x+x,y,z",
        "x,y,1/0+z",
        "x,y,",
    ),
)
def test_symmetry_parser_rejects_code_and_unsupported_algebra(expression: str) -> None:
    with pytest.raises(SymmetryExpressionError):
        parse_symmetry_expression(expression)


def test_raw_identity_mismatch_fails_before_invalid_utf8_decode() -> None:
    content = b"\xff"
    with pytest.raises(CifParseError, match="SHA-256"):
        parse_cif_bytes(content, expected_sha256="0" * 64)


def test_cif_parser_requires_exact_bytes_and_bounded_identity_inputs() -> None:
    with pytest.raises(TypeError):
        parse_cif_bytes(bytearray(SYNTHETIC_CIF_BYTES))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_cif_bytes(SYNTHETIC_CIF_BYTES, expected_byte_count=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_cif_bytes(SYNTHETIC_CIF_BYTES, expected_sha256="A" * 64)


def test_oversized_cif_is_rejected_before_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    import material_studio_mcp_server.canonicalization.cif as implementation

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("hashing must not run")

    monkeypatch.setattr(implementation.hashlib, "sha256", fail_if_called)
    with pytest.raises(CifParseError, match="byte count"):
        parse_cif_bytes(b"x" * (MAX_CIF_BYTES + 1))


def test_cif_parser_rejects_multiple_blocks_and_malformed_quoting() -> None:
    with pytest.raises(CifParseError):
        parse_cif_bytes(SYNTHETIC_CIF_BYTES + b"\ndata_second\n_tag value\n")
    malformed = SYNTHETIC_CIF_BYTES.replace(b"'x,y,z'", b"'x,y,z")
    with pytest.raises(CifParseError):
        parse_cif_bytes(malformed)


def test_cif_parser_rejects_partial_occupancy_and_ambiguous_species() -> None:
    partial = SYNTHETIC_CIF_BYTES.replace(b"C1 C 0 0 0 1", b"C1 C 0 0 0 0.5")
    with pytest.raises(CifParseError):
        parse_cif_bytes(partial)
    near_full = SYNTHETIC_CIF_BYTES.replace(
        b"C1 C 0 0 0 1",
        b"C1 C 0 0 0 0.9999999999999",
    )
    with pytest.raises(CifParseError):
        parse_cif_bytes(near_full)
    ambiguous = SYNTHETIC_CIF_BYTES.replace(b"C1 C 0 0 0 1", b"site Xx 0 0 0 1")
    with pytest.raises(CifParseError):
        parse_cif_bytes(ambiguous)


def test_cif_parser_rejects_unsupported_disorder_and_duplicate_symmetry() -> None:
    disorder = SYNTHETIC_CIF_BYTES.replace(
        b"_atom_site_occupancy\n",
        b"_atom_site_occupancy\n_atom_site_disorder_group\n",
    ).replace(b"C1 C 0 0 0 1", b"C1 C 0 0 0 1 1").replace(
        b" 1\n",
        b" 1 1\n",
    )
    with pytest.raises(CifParseError):
        parse_cif_bytes(disorder)
    duplicate = SYNTHETIC_CIF_BYTES.replace(b"'x,y,z'", b"'x,y,z'\n'x,y,z'")
    with pytest.raises(CifParseError):
        parse_cif_bytes(duplicate)


def test_cif_parser_rejects_detached_scalar_disorder_data_name() -> None:
    detached_scalar = SYNTHETIC_CIF_BYTES.replace(
        b"_cell_length_a 4.0",
        b"_atom_site_disorder_group .\n_cell_length_a 4.0",
    )
    with pytest.raises(CifParseError, match="disorder data names"):
        parse_cif_bytes(detached_scalar)


def test_cif_parser_rejects_disorder_data_name_in_separate_loop() -> None:
    detached_loop = SYNTHETIC_CIF_BYTES.replace(
        b"loop_\n_atom_site_label",
        b"loop_\n_atom_site_disorder_assembly\n.\nloop_\n_atom_site_label",
    )
    with pytest.raises(CifParseError, match="disorder data names"):
        parse_cif_bytes(detached_loop)


def test_symmetry_expansion_rejects_duplicate_asymmetric_rows() -> None:
    parsed = parse_cif_bytes(SYNTHETIC_CIF_BYTES + b"C9 C 0 0 0 1\n")
    with pytest.raises(CifParseError, match="distinct asymmetric rows"):
        expand_cif_symmetry(parsed)


def test_symmetry_expansion_counts_exact_image_candidate_work() -> None:
    parsed = parse_cif_bytes(
        SYNTHETIC_CIF_BYTES.replace(b"_cell_angle_gamma 90", b"_cell_angle_gamma 35")
    )
    first = parsed.asymmetric_sites[0].fractional_coordinates
    second = parsed.asymmetric_sites[1].fractional_coordinates
    displacement = tuple(
        candidate - reference
        for candidate, reference in zip(second, first, strict=True)
    )
    first_image = closest_lattice_image(displacement, parsed.lattice)
    with pytest.raises(CifParseError, match="candidate-work"):
        expand_cif_symmetry(
            parsed,
            max_duplicate_checks=first_image.candidates_examined,
        )


def test_cif_parser_requires_identity_and_closed_symmetry_group() -> None:
    missing_identity = SYNTHETIC_CIF_BYTES.replace(b"'x,y,z'", b"'-x,-y,-z'")
    with pytest.raises(CifParseError, match="identity"):
        parse_cif_bytes(missing_identity)
    non_closed = SYNTHETIC_CIF_BYTES.replace(
        b"'x,y,z'",
        b"'x,y,z'\n'x+1/3,y,z'",
    )
    with pytest.raises(CifParseError, match="not closed"):
        parse_cif_bytes(non_closed)


def test_cif_parser_rejects_missing_required_loop_fields() -> None:
    missing = SYNTHETIC_CIF_BYTES.replace(b"_atom_site_occupancy\n", b"")
    with pytest.raises(CifParseError):
        parse_cif_bytes(missing)


def test_cif_parser_rejects_data_names_reused_across_scalar_and_loop() -> None:
    duplicate = SYNTHETIC_CIF_BYTES.replace(
        b"_cell_length_a 4.0",
        b"_duplicate value\nloop_\n_duplicate\nvalue\n_cell_length_a 4.0",
    )
    with pytest.raises(CifParseError, match="duplicate CIF data name"):
        parse_cif_bytes(duplicate)


def test_cif_numeric_lexer_rejects_python_only_number_forms() -> None:
    invalid = SYNTHETIC_CIF_BYTES.replace(b"_cell_length_a 4.0", b"_cell_length_a 4_0")
    with pytest.raises(CifParseError, match="numeric token"):
        parse_cif_bytes(invalid)
