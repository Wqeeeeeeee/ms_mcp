"""Deterministic CIF parsing and ModelSpec round-trip validation."""

from __future__ import annotations

import hashlib
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any

from material_studio_mcp_server.specs.crystal import CrystalSpec


_LATTICE_TAGS = {
    "a": "_cell_length_a",
    "b": "_cell_length_b",
    "c": "_cell_length_c",
    "alpha": "_cell_angle_alpha",
    "beta": "_cell_angle_beta",
    "gamma": "_cell_angle_gamma",
}
_ATOM_HEADERS = {
    "label": "_atom_site_label",
    "element": "_atom_site_type_symbol",
    "x": "_atom_site_fract_x",
    "y": "_atom_site_fract_y",
    "z": "_atom_site_fract_z",
}
_CIF_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?(?:\(\d+\))?$"
)


def parse_crystal_cif(path: str | Path) -> dict[str, Any]:
    """Parse the lattice and fractional atom loop from a generated-style CIF."""

    cif_path = Path(path).expanduser().resolve()
    result: dict[str, Any] = {
        "parser": "generated_p1_cif_v1",
        "path": str(cif_path),
        "exists": cif_path.exists(),
        "lattice": {},
        "atoms": [],
        "errors": [],
        "warnings": [],
    }
    if not cif_path.exists():
        result["errors"].append(f"CIF file does not exist: {cif_path}")
        result["ok"] = False
        return result
    if not cif_path.is_file():
        result["errors"].append(f"CIF path is not a file: {cif_path}")
        result["ok"] = False
        return result
    if cif_path.suffix.lower() != ".cif":
        result["errors"].append(f"Unsupported structure format for CIF parser: {cif_path.suffix or '<none>'}")
        result["ok"] = False
        return result

    try:
        raw = cif_path.read_bytes()
        text = raw.decode("utf-8-sig")
    except Exception as exc:
        result["errors"].append(f"Failed to read CIF: {exc}")
        result["ok"] = False
        return result

    result["file_size_bytes"] = len(raw)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    logical_lines = _tokenize_cif_lines(text, result["errors"])
    scalars: dict[str, str] = {}
    atom_rows: list[dict[str, Any]] = []
    index = 0
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        first = tokens[0].lower()
        if first == "loop_":
            headers, data_tokens, index = _parse_loop_tokens(logical_lines, index + 1)
            lowered_headers = [header.lower() for header in headers]
            if set(_ATOM_HEADERS.values()) <= set(lowered_headers):
                atom_rows.extend(
                    _parse_atom_loop(lowered_headers, data_tokens, result["errors"])
                )
            continue
        if first.startswith("_"):
            if len(tokens) < 2:
                result["errors"].append(f"CIF scalar {tokens[0]} has no value.")
            else:
                scalars[first] = tokens[1]
        index += 1

    lattice: dict[str, float] = {}
    for key, tag in _LATTICE_TAGS.items():
        raw_value = scalars.get(tag)
        if raw_value is None:
            result["errors"].append(f"Required CIF lattice field is missing: {tag}")
            continue
        parsed = _parse_cif_number(raw_value)
        if parsed is None:
            result["errors"].append(f"Invalid CIF number for {tag}: {raw_value}")
            continue
        lattice[key] = parsed

    if not atom_rows:
        result["errors"].append("Required CIF fractional atom loop is missing or empty.")
    labels = [str(atom["id"]) for atom in atom_rows]
    duplicate_labels = sorted(label for label, count in Counter(labels).items() if count > 1)
    if duplicate_labels:
        result["errors"].append("Duplicate CIF atom labels: " + ", ".join(duplicate_labels))

    result["lattice"] = lattice
    result["atoms"] = atom_rows
    result["atom_count"] = len(atom_rows)
    result["element_counts"] = dict(sorted(Counter(str(atom["element"]) for atom in atom_rows).items()))
    result["ok"] = not result["errors"]
    return result


def validate_crystal_cif_against_spec(
    crystal: CrystalSpec,
    path: str | Path,
    *,
    coordinate_tolerance: float = 1e-8,
    lattice_tolerance: float = 1e-7,
) -> dict[str, Any]:
    """Compare a materialized CIF with the crystal source of truth."""

    parsed = parse_crystal_cif(path)
    receipt: dict[str, Any] = {
        "applicable": True,
        "format": "cif",
        "parser": parsed.get("parser"),
        "structure_path": parsed.get("path"),
        "exists": parsed.get("exists"),
        "file_size_bytes": parsed.get("file_size_bytes"),
        "sha256": parsed.get("sha256"),
        "coordinate_tolerance": coordinate_tolerance,
        "lattice_tolerance": lattice_tolerance,
        "expected_atom_count": len(crystal.basis_atoms),
        "actual_atom_count": parsed.get("atom_count"),
        "expected_element_counts": _element_counts(crystal),
        "actual_element_counts": parsed.get("element_counts") or {},
        "atom_count_matches": False,
        "element_counts_match": False,
        "atom_ids_match": False,
        "atom_elements_match": False,
        "fractional_coordinates_match": False,
        "lattice_matches": False,
        "max_fractional_delta": None,
        "max_lattice_delta": None,
        "missing_atom_ids": [],
        "extra_atom_ids": [],
        "element_mismatches": [],
        "fractional_coordinate_mismatches": [],
        "lattice_mismatches": [],
        "errors": list(parsed.get("errors") or []),
        "warnings": list(parsed.get("warnings") or []),
    }
    if not parsed.get("ok"):
        receipt["status"] = "missing" if not parsed.get("exists") else "parse_failed"
        receipt["ok"] = False
        return receipt

    expected_atoms = {atom.id: atom for atom in crystal.basis_atoms}
    actual_atoms = {str(atom["id"]): atom for atom in parsed.get("atoms", [])}
    expected_ids = set(expected_atoms)
    actual_ids = set(actual_atoms)
    missing_ids = sorted(expected_ids - actual_ids)
    extra_ids = sorted(actual_ids - expected_ids)
    receipt["missing_atom_ids"] = missing_ids
    receipt["extra_atom_ids"] = extra_ids
    receipt["atom_ids_match"] = not missing_ids and not extra_ids
    receipt["atom_count_matches"] = len(expected_atoms) == len(actual_atoms)
    receipt["element_counts_match"] = receipt["expected_element_counts"] == receipt["actual_element_counts"]

    element_mismatches: list[dict[str, Any]] = []
    coordinate_mismatches: list[dict[str, Any]] = []
    max_fractional_delta = 0.0
    for atom_id in sorted(expected_ids & actual_ids):
        expected = expected_atoms[atom_id]
        actual = actual_atoms[atom_id]
        if expected.element != actual.get("element"):
            element_mismatches.append(
                {
                    "atom_id": atom_id,
                    "expected": expected.element,
                    "actual": actual.get("element"),
                }
            )
        expected_fractional = (
            float(expected.fractional.x),
            float(expected.fractional.y),
            float(expected.fractional.z),
        )
        actual_fractional = tuple(float(value) for value in actual["fractional"])
        deltas = tuple(
            _periodic_fractional_delta(expected_value, actual_value)
            for expected_value, actual_value in zip(expected_fractional, actual_fractional)
        )
        atom_max_delta = max(deltas)
        max_fractional_delta = max(max_fractional_delta, atom_max_delta)
        if atom_max_delta > coordinate_tolerance:
            coordinate_mismatches.append(
                {
                    "atom_id": atom_id,
                    "expected": list(expected_fractional),
                    "actual": list(actual_fractional),
                    "delta": list(deltas),
                    "max_delta": atom_max_delta,
                }
            )
    receipt["element_mismatches"] = element_mismatches
    receipt["fractional_coordinate_mismatches"] = coordinate_mismatches
    receipt["atom_elements_match"] = receipt["atom_ids_match"] and not element_mismatches
    receipt["fractional_coordinates_match"] = receipt["atom_ids_match"] and not coordinate_mismatches
    receipt["max_fractional_delta"] = max_fractional_delta

    lattice_mismatches: list[dict[str, Any]] = []
    max_lattice_delta = 0.0
    parsed_lattice = parsed.get("lattice") or {}
    for key in _LATTICE_TAGS:
        expected_value = float(getattr(crystal.lattice, key))
        actual_value = float(parsed_lattice[key])
        delta = abs(expected_value - actual_value)
        max_lattice_delta = max(max_lattice_delta, delta)
        tolerance = max(lattice_tolerance, abs(expected_value) * 1e-9)
        if delta > tolerance:
            lattice_mismatches.append(
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": actual_value,
                    "delta": delta,
                    "tolerance": tolerance,
                }
            )
    receipt["lattice_mismatches"] = lattice_mismatches
    receipt["lattice_matches"] = not lattice_mismatches
    receipt["max_lattice_delta"] = max_lattice_delta

    required_checks = (
        "atom_count_matches",
        "element_counts_match",
        "atom_ids_match",
        "atom_elements_match",
        "fractional_coordinates_match",
        "lattice_matches",
    )
    receipt["ok"] = all(bool(receipt[key]) for key in required_checks)
    receipt["status"] = "matched" if receipt["ok"] else "mismatch"
    if not receipt["ok"]:
        receipt["errors"].append(
            "Materialized CIF content does not match the current CrystalSpec."
        )
    return receipt


def _tokenize_cif_lines(text: str, errors: list[str]) -> list[list[str]]:
    lines: list[list[str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            lines.append(shlex.split(raw_line, comments=True, posix=True))
        except ValueError as exc:
            errors.append(f"CIF tokenization failed on line {line_number}: {exc}")
            lines.append([])
    return lines


def _parse_loop_tokens(
    logical_lines: list[list[str]],
    start: int,
) -> tuple[list[str], list[str], int]:
    headers: list[str] = []
    index = start
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        if not tokens[0].startswith("_"):
            break
        headers.extend(tokens)
        index += 1

    data_tokens: list[str] = []
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        first = tokens[0].lower()
        if first in {"loop_", "stop_"} or first.startswith("_") or first.startswith("data_"):
            break
        data_tokens.extend(tokens)
        index += 1
    return headers, data_tokens, index


def _parse_atom_loop(
    headers: list[str],
    data_tokens: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    width = len(headers)
    if width == 0:
        return []
    if len(data_tokens) % width:
        errors.append(
            f"CIF atom loop token count {len(data_tokens)} is not divisible by header count {width}."
        )
        return []
    header_index = {header: index for index, header in enumerate(headers)}
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(data_tokens), width):
        values = data_tokens[offset : offset + width]
        coordinates = [
            _parse_cif_number(values[header_index[_ATOM_HEADERS[key]]])
            for key in ("x", "y", "z")
        ]
        if any(value is None for value in coordinates):
            errors.append(
                "Invalid fractional coordinate in CIF atom row: " + " ".join(values)
            )
            continue
        rows.append(
            {
                "id": values[header_index[_ATOM_HEADERS["label"]]],
                "element": values[header_index[_ATOM_HEADERS["element"]]],
                "fractional": [float(value) for value in coordinates if value is not None],
            }
        )
    return rows


def _parse_cif_number(value: str) -> float | None:
    token = str(value).strip()
    if token in {".", "?"} or not _CIF_NUMBER_RE.fullmatch(token):
        return None
    token = re.sub(r"\(\d+\)$", "", token)
    try:
        return float(token)
    except ValueError:
        return None


def _periodic_fractional_delta(expected: float, actual: float) -> float:
    wrapped = abs(expected - actual) % 1.0
    return min(wrapped, 1.0 - wrapped)


def _element_counts(crystal: CrystalSpec) -> dict[str, int]:
    return dict(sorted(Counter(atom.element for atom in crystal.basis_atoms).items()))
